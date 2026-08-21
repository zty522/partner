"""DirectAPIAdapter - calls DeepSeek API directly, bypassing hermes subprocess.

Avoids the subprocess.PIPE deadlock on WSL (Python 3.13 parent, Python 3.11 child)
by using simple HTTP requests.
"""
import json, os, time, logging
import requests
import concurrent.futures
from typing import Optional, List

logger = logging.getLogger(__name__)

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


def _resolve_api_json() -> dict:
    """从 workspace config/api.json 读取 deepseek 配置（统一管理入口）。

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
        ds = data.get("apis", {}).get("deepseek", {}) or {}
        out = {}
        for k in ("api_key", "model", "base_url"):
            v = str(ds.get(k) or "").strip()
            if v:
                out[k] = v
        # base_url 剥掉尾部 /v1：chat() 内部固定拼 /v1/chat/completions，
        # 用户 api.json 里习惯填 https://api.deepseek.com/v1 会导致双 /v1 404。
        b = out.get("base_url", "")
        if b.endswith("/v1"):
            out["base_url"] = b[:-3]
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
    def _do():
        return requests.post(url, headers=headers, json=payload,
                             timeout=(30, timeout), proxies=proxies)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
        fut = _ex.submit(_do)
        try:
            return fut.result(timeout=timeout + 20)
        except concurrent.futures.TimeoutError:
            logger.warning("[DirectAPI] hard timeout after %ss (network hung)", timeout + 20)
            return None


def _log_api_call(**kw):
    """记录 deepseek API 调用日志；失败不影响主流程。"""
    try:
        from ..api_log import append_api_call
        append_api_call("deepseek", **kw)
    except Exception:
        pass


def chat(prompt: str, max_tokens: int = 4096, temperature: float = 0.0,
         purpose: str = "chat", timeout: int = 60) -> str:
    """Send a chat request directly to DeepSeek API.
    
    Returns the model's response text, or empty string on failure.
    """
    cfg = _resolve_api_json()
    api_key = cfg.get("api_key") or API_KEY
    model = cfg.get("model") or os.environ.get("DEEPSEEK_MODEL") or MODEL
    api_base = (cfg.get("base_url") or API_BASE).rstrip("/")
    if not api_key:
        logger.error("[DirectAPI] No DeepSeek API key found")
        _log_api_call(purpose=purpose, status="failed", error="no api key", model=model)
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
            _log_api_call(model=model, purpose=purpose, status="failed",
                          error="hard timeout", elapsed_ms=int((time.time() - start) * 1000),
                          prompt_chars=len(prompt))
            return ""
        elapsed = time.time() - start
        if r.status_code == 200:
            data = r.json()
            resp_content = data["choices"][0]["message"]["content"]
            logger.info(f"[DirectAPI] {purpose} OK in {elapsed:.1f}s, prompt={len(prompt)}chars response={len(resp_content)}chars")
            _log_api_call(model=model, base_url=api_base, purpose=purpose, status="ok",
                          elapsed_ms=int(elapsed * 1000), prompt_chars=len(prompt),
                          response_chars=len(resp_content))
            # Fallback: if v4-flash returns empty on batch_plan, retry with v4-pro
            if purpose == "batch_plan" and len(resp_content) < 10 and payload.get("model") == "deepseek-v4-flash":
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
                        _log_api_call(model="deepseek-v4-pro", base_url=api_base, purpose=purpose,
                                      status="failed", error="fallback hard timeout",
                                      elapsed_ms=int((time.time() - start) * 1000),
                                      prompt_chars=len(prompt))
                        return ""
                    if fr.status_code == 200:
                        fb_data = fr.json()
                        fb_content = fb_data["choices"][0]["message"]["content"]
                        logger.info(f"[DirectAPI] v4-pro fallback OK in {time.time()-start:.1f}s, {len(fb_content)} chars")
                        _log_api_call(model="deepseek-v4-pro", base_url=api_base, purpose=purpose,
                                      status="ok", elapsed_ms=int((time.time() - start) * 1000),
                                      prompt_chars=len(prompt), response_chars=len(fb_content),
                                      error="fallback_from_v4_flash_empty")
                        return fb_content
                    else:
                        logger.warning(f"[DirectAPI] v4-pro fallback HTTP {fr.status_code}")
                        _log_api_call(model="deepseek-v4-pro", base_url=api_base, purpose=purpose,
                                      status="failed", error=f"fallback HTTP {fr.status_code}",
                                      elapsed_ms=int((time.time() - start) * 1000),
                                      prompt_chars=len(prompt))
                except Exception as fe:
                    logger.warning(f"[DirectAPI] v4-pro fallback failed: {fe}")
                    _log_api_call(model="deepseek-v4-pro", base_url=api_base, purpose=purpose,
                                  status="failed", error=f"fallback exception: {fe}",
                                  elapsed_ms=int((time.time() - start) * 1000),
                                  prompt_chars=len(prompt))
            return resp_content
        else:
            logger.warning(f"[DirectAPI] {purpose} HTTP {r.status_code} in {elapsed:.1f}s: {r.text[:200]}")
            _log_api_call(model=model, base_url=api_base, purpose=purpose, status="failed",
                          error=f"HTTP {r.status_code}: {r.text[:150]}",
                          elapsed_ms=int(elapsed * 1000), prompt_chars=len(prompt))
            return ""
    except Exception as e:
        elapsed = time.time() - start
        logger.warning(f"[DirectAPI] {purpose} failed in {elapsed:.1f}s: {e}")
        _log_api_call(model=model, base_url=api_base, purpose=purpose, status="failed",
                      error=str(e), elapsed_ms=int(elapsed * 1000), prompt_chars=len(prompt))
        return ""
