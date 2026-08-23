"""读图事件 —— 用 workspace config/api.json 预设的 qwen 视觉模型读取图片内容。

read_image: 输入图片路径 + 可选提示词，返回 qwen VL 对图片内容的描述。
用于检查截图/图表实际内容（"截图内容为空"问题的核查手段）。
"""
import base64
import io
import json
import logging
import os
import urllib.error
import urllib.request
from PIL import Image

logger = logging.getLogger(__name__)

_VISION_PROMPT_DEFAULT = "请详细描述这张图片的内容，包括所有可见的文字、图表、界面元素。如果是截图，说明它显示了什么。"


def _find_workspace_root(start: str = "") -> str:
    """从任意路径（实例目录等）上溯查找包含 config/api.json 的 workspace 根。"""
    cur = os.path.abspath(start or "") if start else ""
    if not cur:
        return ""
    while True:
        if os.path.exists(os.path.join(cur, "config", "api.json")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return ""


def _load_qwen_vision_cfg(workspace: str = "") -> dict:
    """从 workspace config/api.json 读取 qwen 视觉配置（vision_model 优先）。"""
    try:
        from ..api_log import workspace_root_from_pointer
        root = _find_workspace_root(workspace) or workspace_root_from_pointer() or ""
        api_path = os.path.join(root, "config", "api.json")
        if not os.path.exists(api_path):
            return {}
        with open(api_path, encoding="utf-8") as f:
            data = json.load(f)
        qw = data.get("apis", {}).get("qwen", {}) or {}
        key = str(qw.get("api_key") or "").strip()
        if not key:
            return {}
        base = str(qw.get("base_url") or "").strip().rstrip("/")
        model = str(qw.get("vision_model") or qw.get("model") or "").strip()
        return {"api_key": key, "base_url": base, "model": model}
    except Exception as exc:
        logger.warning("[read_image] load qwen cfg failed: %s", exc)
        return {}


def read_image_with_qwen(image_path: str, prompt: str = "", workspace: str = "") -> dict:
    """用 qwen VL 读取单张图片，返回 {ok, description, model, image}。"""
    if not image_path or not os.path.exists(image_path):
        return {"ok": False, "error": f"图片不存在: {image_path}"}
    cfg = _load_qwen_vision_cfg(workspace)
    if not cfg.get("api_key") or not cfg.get("model"):
        return {"ok": False, "error": "api.json 未配置 qwen api_key/vision_model"}
    try:
        # 统一缩放到 1200 内（dashscope 限制，实测 1200 宽稳定）
        im = Image.open(image_path)
        w, h = im.size
        scale = min(1.0, 1200 / max(w, h))
        if scale < 1.0:
            im = im.resize((int(w * scale), int(h * scale)))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()

        text = prompt.strip() or _VISION_PROMPT_DEFAULT
        payload = {
            "model": cfg["model"],
            "messages": [
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": text},
                ]},
            ],
            "max_tokens": 1024,
        }
        url = cfg["base_url"] + "/chat/completions" if cfg["base_url"] else ""
        if not url:
            return {"ok": False, "error": "qwen base_url 未配置"}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
        )
        with opener.open(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        desc = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"ok": True, "description": str(desc).strip(), "model": cfg["model"],
                "image": os.path.basename(image_path)}
    except Exception as exc:
        return {"ok": False, "error": f"qwen 读图失败: {exc}"}


def _find_recent_image(workspace: str, workdir: str = "") -> str:
    """Find a recent image in the current task only.

    Global and ``/tmp`` fallbacks are intentionally excluded because they can
    silently attach an older task's screenshot to the current result.
    """
    import time as _time
    candidates = []
    dirs = []
    for d in ((workdir,) if workdir else (workspace,)):
        if d and os.path.isdir(d):
            dirs.append(d)
    now = _time.time()
    for d in dirs:
        try:
            for f in os.listdir(d):
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                    fp = os.path.join(d, f)
                    mt = os.path.getmtime(fp)
                    if now - mt > 1800:
                        continue
                    candidates.append((mt, fp))
        except OSError:
            continue
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][1]


def atomic_read_image(ctx, params: dict) -> dict:
    """读取图片内容。参数: path/image(图片路径), prompt(可选描述要求)。

    path 不存在时自动回退到工作目录/实例 screenshots 目录最近的一张图片
    （LLM 常按任务工作目录猜截图路径，而 web_capture 默认输出到 screenshots 目录）。
    """
    path = str(params.get("path") or params.get("image") or "").strip()
    workspace = getattr(ctx, "workspace", "")
    task = getattr(ctx, "task_instance", None)
    workdir = (getattr(task, "working_dir", "") if task is not None else "") or getattr(ctx, "working_dir", "")
    if not path or not os.path.exists(path):
        # 容错：找最近截图
        fallback = _find_recent_image(workspace, workdir)
        if fallback:
            logger.info("[read_image] path %r 不存在，回退到最近截图 %s", path, fallback)
            path = fallback
    if not path:
        return {"ok": False, "error": "缺少参数 path，且未找到可用的最近截图"}
    prompt = str(params.get("prompt") or "").strip()
    result = read_image_with_qwen(path, prompt, workspace)
    if result.get("ok"):
        # ── C hook: 识别登录墙 → 调 handle_login_wall 阻止后续重试同动作 ──
        desc = result["description"]
        login_keywords = ("登录弹窗", "手机号登录", "扫码登录", "未登录", "未注册", "需要登录", "登录入口", "微信扫码")
        logged_in_cues = ("已登录", "用户头像", "账号昵称", "用户昵称", "退出登录", "创作服务平台")
        is_login_wall = any(kw in desc for kw in login_keywords) and not any(cue in desc for cue in logged_in_cues)
        if is_login_wall:
            try:
                from partner.v2.repair_events import atomic_handle_login_wall
                fb = atomic_handle_login_wall(ctx, {})
                logger.info("[read_image] 登录墙检测: %s, note=%s", fb.get("login_wall"), fb.get("note"))
                return {"ok": True, "description": desc, "model": result["model"],
                        "image": result["image"], "content": desc, "files": [path],
                        "login_wall": True, "login_note": fb.get("note")}
            except Exception as _exc_c:
                logger.warning("[read_image] handle_login_wall failed: %s", _exc_c)
        return {"ok": True, "description": desc, "model": result["model"],
                "image": result["image"], "content": desc, "files": [path]}
    return {"ok": False, "error": result.get("error", "读图失败")}


__all__ = ["atomic_read_image", "read_image_with_qwen", "load_qwen_vision_cfg"] if False else ["atomic_read_image"]
