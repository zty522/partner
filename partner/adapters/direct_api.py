"""Direct API adapter with an explicit provider boundary.

Avoids the subprocess.PIPE deadlock on WSL (Python 3.13 parent, Python 3.11 child)
by using simple HTTP requests.
"""
import json, os, time, logging, threading
import requests
import concurrent.futures
from typing import Optional, List

logger = logging.getLogger(__name__)
_usage_local = threading.local()


def get_last_usage() -> dict:
    """Return provider-reported usage for the current calling thread."""
    return dict(getattr(_usage_local, "value", {}) or {})

# Load API key from Hermes env
def _load_deepseek_key():
    env_file = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(env_file):
        for line in open(env_file):
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("DEEPSEEK_API_KEY", "")

API_KEY = _load_deepseek_key()
API_BASE = "https://api.deepseek.com"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
# Default provider when the caller does not explicitly pick one.
# Switched back to "minimax" (MiniMax-M3) on 2026-09-12 per user request.
# DeepSeek remains the explicit alternative via api.json apis.deepseek or
# PARTNER_DEFAULT_PROVIDER=deepseek env override.
DEFAULT_PROVIDER = os.environ.get("PARTNER_DEFAULT_PROVIDER", "minimax")


def _resolve_api_json(provider: str = "") -> dict:
    """从 workspace config/api.json 读取 provider 配置（统一管理入口）。

    默认 MiniMax。DeepSeek 只能由调用方显式指定，绝不作为 fallback。

    解析顺序：~/.partner_workspace 指针 → workspace_root/config/api.json。
    任何失败都返回空 dict，调用方回退到环境变量 / 模块默认值。
    """
    try:
        pointer = os.path.expanduser("~/.partner_workspace")
        ws_root = None
        if os.path.exists(pointer):
            raw = open(pointer, encoding="utf-8", errors="replace").read().strip()
            norm = raw.replace("\\", "/")
            if norm.startswith("/mnt/"):
                ws_root = norm
            elif len(norm) >= 2 and norm[1] == ":":
                ws_root = "/mnt/" + norm[0].lower() + norm[2:]
        if not ws_root:
            return {}
        api_path = os.path.join(ws_root, "config", "api.json")
        if not os.path.exists(api_path):
            return {}
        with open(api_path, encoding="utf-8") as f:
            data = json.load(f)
        apis = data.get("apis", {}) or {}
        requested = str(provider or "").strip().lower()
        selected_name = requested if requested else DEFAULT_PROVIDER
        primary = apis.get(selected_name, {}) or {}
        if not (str(primary.get("api_key") or "").strip() and str(primary.get("base_url") or "").strip()):
            return {}
        out = {}
        for k in ("api_key", "model", "base_url"):
            v = str(primary.get(k) or "").strip()
            if v:
                out[k] = v
        # base_url 剥掉尾部 /v1：chat() 内部固定拼 /v1/chat/completions，
        # 避免双 /v1 404。
        b = out.get("base_url", "")
        if b.endswith("/v1"):
            out["base_url"] = b[:-3]
        out["_provider"] = selected_name
        return out
    except Exception:
        return {}

def _post_hard_timeout(url: str, headers: dict, payload: dict, proxies: dict, timeout: int):
    """requests.post 带外层硬超时。

    requests 的 timeout 在某些网络挂起条件下（连接建立后服务器不返回、DNS 偶发
    挂起等）可能不生效，导致请求无限期挂起。这里用 ThreadPoolExecutor + future
    timeout 做第二道保险，超时后放弃（后台线程会泄漏，但对长驻进程可接受，
    远好过整个事件循环被单个请求卡死）。
    """
    # read_timeout 设宽一点：minimax 长 prompt 经常 50-80s 才回，30s 太短；
    # 连接超时 30s 已够。``timeout`` 由调用方决定，留作外层硬上限。
    def _do():
        with requests.Session() as session:
            session.trust_env = False
            return session.post(url, headers=headers, json=payload,
                                timeout=(min(15, timeout), timeout), proxies=proxies)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_do)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.warning("[DirectAPI] hard timeout after %ss (network hung)", timeout)
        return None
    finally:
        # Context-manager shutdown waits for the hung request, defeating the
        # caller deadline. Network requests retain their own socket timeout.
        executor.shutdown(wait=False, cancel_futures=True)


def _log_api_call(**kw):
    """记录 API 调用日志；失败不影响主流程。provider 缺省为 MiniMax。"""
    try:
        from ..api_log import append_api_call
        provider = DEFAULT_PROVIDER
        try:
            provider = _resolve_api_json().get("_provider", DEFAULT_PROVIDER)
        except Exception:
            pass
        append_api_call(provider, **kw)
    except Exception:
        pass


_LONG_GEN_PURPOSES = ("batch_plan", "action", "report", "focus_extract")


def select_model_and_tokens(cfg: dict, purpose: str = "", max_tokens=None) -> tuple[str, int]:
    """模型与 max_tokens 选择（purpose 分流）。

    默认 provider：minimax（MiniMax-M3）。DeepSeek 不做隐式 fallback。
    长内容生成类 purpose（batch_plan/action/report/focus_extract）用 minimax 长生成模型；
    max_tokens ≥16000 防输出截断。
    普通对话（chat/classify/direct_reply 等）保持 api.json 配置模型。
    api.json 可加 long_gen_model / batch_plan_model 覆盖。
    """
    model = cfg.get("model") or os.environ.get("MINIMAX_MODEL") or "MiniMax-M3"
    mt = max_tokens
    if purpose in _LONG_GEN_PURPOSES:
        configured_model = str(cfg.get("model") or "").lower()
        provider = str(cfg.get("_provider") or (
            "deepseek" if configured_model.startswith("deepseek") else DEFAULT_PROVIDER
        )).lower()
        # minimax 没有"长生成"专属模型；batch_plan/action/report 都用同一型号。
        provider_default = (
            cfg.get("model") or os.environ.get("MINIMAX_LONG_GEN_MODEL") or "MiniMax-M3"
            if provider == "minimax"
            else os.environ.get("DEEPSEEK_LONG_GEN_MODEL") or "deepseek-v4-pro"
        )
        model = (cfg.get("long_gen_model")
                 or (cfg.get("batch_plan_model") if purpose == "batch_plan" else "")
                 or provider_default)
        if mt is None or mt < 16000:
            mt = 16000
    return model, mt


def _instance_from_workspace(workspace: str) -> str:
    path = os.path.normpath(str(workspace or ""))
    match = __import__("re").search(r"[/\\]instances[/\\](0[1-5])(?:$|[/\\])", path)
    return match.group(1) if match else ""


def chat(prompt: str, max_tokens: int = 4096, temperature: float = 0.0,
         purpose: str = "chat", timeout: int = 90, provider: str = "",
         *, workspace: str = "", instance_id: str = "", project_id: str = "",
         task_id: str = "", episode_id: str = "", event_type: str = "") -> str:
    """Send a chat request to the explicitly selected provider (MiniMax by default).
    
    Returns the model's response text, or empty string on failure.
    """
    _usage_local.value = {}
    cfg = _resolve_api_json(provider)
    call_meta = {
        "workspace_root": workspace,
        "instance": instance_id or _instance_from_workspace(workspace),
        "project_id": project_id,
        "task_id": task_id,
        "episode_id": episode_id,
        "event_type": event_type,
    }
    selected_provider = str(cfg.get("_provider") or provider or DEFAULT_PROVIDER).lower()
    api_key = cfg.get("api_key") or (API_KEY if selected_provider == "deepseek" else "")
    model, max_tokens = select_model_and_tokens(cfg, purpose, max_tokens)
    api_base = (cfg.get("base_url") or (API_BASE if selected_provider == "deepseek" else "")).rstrip("/")
    if not api_key:
        logger.error("[DirectAPI] No API key found for provider=%s", selected_provider)
        _log_api_call(**call_meta, purpose=purpose, status="failed", error="no api key", model=model)
        return ""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    # Bypass any system proxy that might interfere
    proxies = {"http": None, "https": None}
    
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    # 仅 deepseek-v4-pro 是 reasoning 模型（默认会先产一大段思考拖慢
    # execute 又烧 token）。reasoning_effort=none 关掉思考，又快又省。
    # minimax 是普通 chat 模型，不传这个字段。
    if selected_provider == "deepseek":
        payload["reasoning_effort"] = "none"
    
    start = time.time()
    try:
        if purpose == "batch_plan":

            try:

                with open("/tmp/batch_plan_prompt.txt", "w") as _pf:

                    _pf.write(prompt)

            except: pass
        r = _post_hard_timeout(
            f"{api_base}/v1/chat/completions",
            headers=headers,
            payload=payload,
            proxies=proxies,
            timeout=timeout,
        )
        if r is None:
            _usage_local.value = {"error": "hard timeout"}
            _log_api_call(**call_meta, model=model, purpose=purpose, status="failed",
                          error="hard timeout", elapsed_ms=int((time.time() - start) * 1000),
                          prompt_chars=len(prompt))
            return ""
        elapsed = time.time() - start
        if r.status_code == 200:
            data = r.json()
            resp_content = data["choices"][0]["message"].get("content") or ""
            finish_reason = data["choices"][0].get("finish_reason", "")
            usage = data.get("usage") or {}
            _usage_local.value = {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
                "model": model, "provider": selected_provider,
                "finish_reason": finish_reason,
            }
            logger.info(f"[DirectAPI] {purpose} OK in {elapsed:.1f}s, prompt={len(prompt)}chars response={len(resp_content)}chars")
            _log_api_call(**call_meta, model=model, base_url=api_base, purpose=purpose, status="ok",
                          elapsed_ms=int(elapsed * 1000), prompt_chars=len(prompt),
                          response_chars=len(resp_content), finish_reason=finish_reason,
                          prompt_tokens=int(usage.get("prompt_tokens") or 0),
                          completion_tokens=int(usage.get("completion_tokens") or 0),
                          total_tokens=int(usage.get("total_tokens") or 0))
            # Fallback: if v4-flash returns empty on batch_plan, retry with v4-pro
            if (selected_provider == "deepseek" and purpose == "batch_plan"
                    and len(resp_content) < 10 and payload.get("model") == "deepseek-v4-flash"):
                logger.warning(f"[DirectAPI] v4-flash returned empty, falling back to v4-pro...")
                fallback_payload = dict(payload)
                fallback_payload["model"] = "deepseek-v4-pro"
                try:
                    fr = _post_hard_timeout(
                        f"{api_base}/v1/chat/completions",
                        headers=headers,
                        payload=fallback_payload,
                        proxies=proxies,
                        timeout=max(timeout, 120),
                    )
                    if fr is None:
                        logger.warning("[DirectAPI] v4-pro fallback hard timeout")
                        _log_api_call(**call_meta, model="deepseek-v4-pro", base_url=api_base, purpose=purpose,
                                      status="failed", error="fallback hard timeout",
                                      elapsed_ms=int((time.time() - start) * 1000),
                                      prompt_chars=len(prompt))
                        return ""
                    if fr.status_code == 200:
                        fb_data = fr.json()
                        fb_content = fb_data["choices"][0]["message"]["content"]
                        fb_usage = fb_data.get("usage") or {}
                        logger.info(f"[DirectAPI] v4-pro fallback OK in {time.time()-start:.1f}s, {len(fb_content)} chars")
                        _log_api_call(**call_meta, model="deepseek-v4-pro", base_url=api_base, purpose=purpose,
                                      status="ok", elapsed_ms=int((time.time() - start) * 1000),
                                      prompt_chars=len(prompt), response_chars=len(fb_content),
                                      prompt_tokens=int(fb_usage.get("prompt_tokens") or 0),
                                      completion_tokens=int(fb_usage.get("completion_tokens") or 0),
                                      total_tokens=int(fb_usage.get("total_tokens") or 0),
                                      error="fallback_from_v4_flash_empty")
                        return fb_content
                    else:
                        logger.warning(f"[DirectAPI] v4-pro fallback HTTP {fr.status_code}")
                        _log_api_call(**call_meta, model="deepseek-v4-pro", base_url=api_base, purpose=purpose,
                                      status="failed", error=f"fallback HTTP {fr.status_code}",
                                      elapsed_ms=int((time.time() - start) * 1000),
                                      prompt_chars=len(prompt))
                except Exception as fe:
                    logger.warning(f"[DirectAPI] v4-pro fallback failed: {fe}")
                    _log_api_call(**call_meta, model="deepseek-v4-pro", base_url=api_base, purpose=purpose,
                                  status="failed", error=f"fallback exception: {fe}",
                                  elapsed_ms=int((time.time() - start) * 1000),
                                  prompt_chars=len(prompt))
            return resp_content
        else:
            _usage_local.value = {"error": f"HTTP {r.status_code}"}
            logger.warning(f"[DirectAPI] {purpose} HTTP {r.status_code} in {elapsed:.1f}s: {r.text[:200]}")
            _log_api_call(**call_meta, model=model, base_url=api_base, purpose=purpose, status="failed",
                          error=f"HTTP {r.status_code}: {r.text[:150]}",
                          elapsed_ms=int(elapsed * 1000), prompt_chars=len(prompt))
            return ""
    except Exception as e:
        _usage_local.value = {"error": type(e).__name__}
        elapsed = time.time() - start
        logger.warning(f"[DirectAPI] {purpose} failed in {elapsed:.1f}s: {e}")
        _log_api_call(**call_meta, model=model, base_url=api_base, purpose=purpose, status="failed",
                      error=str(e), elapsed_ms=int(elapsed * 1000), prompt_chars=len(prompt))
        return ""
