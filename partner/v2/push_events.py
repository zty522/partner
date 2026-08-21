"""Atomic QQ file push event for Partner Harness — tested and working."""
import os, logging

logger = logging.getLogger(__name__)


def atomic_push_files(ctx, params):
    """Push a specific file or all files in a directory to QQ user.
    
    Params:
        source: str — file path or directory path (required)
        caption: str — message text for QQ (default: filename)
    
    Returns {ok, pushed, total, results}
    
    Works with both HarnessContext objects and dict contexts.
    """
    import asyncio as _asyncio
    try:
        # Support both HarnessContext (object) and dict-based ctx
        if hasattr(ctx, 'workspace'):
            workspace = ctx.workspace
        elif isinstance(ctx, dict):
            workspace = ctx.get("workspace", "")
        else:
            workspace = ""
        
        source = params.get("source", "")
        caption = params.get("caption", "")
        
        if not source:
            return {"ok": False, "error": "source parameter required (file path or directory)"}
        
        # Resolve relative paths against workspace
        if not os.path.isabs(source) and workspace:
            source = os.path.join(workspace, source)
        
        # Try alternative extensions if exact path not found (e.g., planner says .png but file is .jpg)
        if not os.path.exists(source):
            alt_exts = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.pdf', '.csv', '.xlsx', '.txt', '.md']
            base = os.path.splitext(source)[0]
            found = False
            for ext in alt_exts:
                alt_path = base + ext
                if os.path.exists(alt_path):
                    logger.info("[PUSH-EVENT] source %s not found, using %s instead", os.path.basename(source), os.path.basename(alt_path))
                    source = alt_path
                    found = True
                    break
            if not found:
                return {"ok": False, "error": f"source not found: {source}"}
        
        # Use direct HTTP API to avoid asyncio.run() conflict with existing event loop
        if os.path.isfile(source):
            ok = _push_single_file_direct(workspace, source, caption)
            r = {"ok": True, "pushed": int(ok), "total": 1, "results": {os.path.basename(source): ok}}
        elif os.path.isdir(source):
            results = {}
            for f in sorted(os.listdir(source)):
                fp = os.path.join(source, f)
                if os.path.isfile(fp) and os.path.getsize(fp) > 100:
                    ok = _push_single_file_direct(workspace, fp, caption or f)
                    results[f] = ok
            pushed = sum(1 for v in results.values() if v)
            r = {"ok": True, "pushed": pushed, "total": len(results), "results": results}
        else:
            return {"ok": False, "error": f"not a file or directory: {source}"}
        
        logger.info("[PUSH-EVENT] %d/%d files pushed", r.get("pushed", 0), r.get("total", 0))
        return {"ok": True, "pushed": r.get("pushed", 0), "total": r.get("total", 0)}
    
    except Exception as e:
        logger.exception("push_files failed")
        return {"ok": False, "error": str(e)}


def _push_single_file_direct(workspace: str, filepath: str, caption: str = "") -> bool:
    """Push a single file to QQ using direct HTTP API (no asyncio.run)."""
    import json, requests, base64
    cfg_path = os.path.join(workspace, "config", "qq_config.json")
    ctx_path = os.path.join(workspace, "state", "qq_user_context.json")
    
    if not os.path.exists(cfg_path) or not os.path.exists(ctx_path):
        return False
    
    with open(cfg_path) as f:
        cfg = json.load(f)
    with open(ctx_path) as f:
        ctx = json.load(f)
    
    app_id = cfg.get("app_id", "")
    secret = cfg.get("app_secret", "")
    openid = ctx.get("openid", "")
    sandbox = cfg.get("is_sandbox", False)
    base = "https://api.sgroup.qq.com" if not sandbox else "https://sandbox.api.sgroup.qq.com"
    
    if not app_id or not secret or not openid:
        return False
    
    # Get access token
    try:
        r = requests.post("https://bots.qq.com/app/getAppAccessToken",
                         json={"appId": app_id, "clientSecret": secret},
                         timeout=10,
                         proxies={"http": None, "https": None})
        token = r.json().get("access_token", "")
        if not token:
            return False
    except Exception:
        return False
    
    # Read file
    with open(filepath, "rb") as f:
        data = f.read()
    fn = os.path.basename(filepath)
    ext = os.path.splitext(fn)[1].lower()
    ft = 1 if ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp') else 4
    b64 = base64.b64encode(data).decode("ascii")
    
    headers = {
        "Authorization": f"QQBot {token}",
        "Content-Type": "application/json",
        "X-Union-Appid": app_id,
    }
    
    # Upload file
    try:
        r = requests.post(f"{base}/v2/users/{openid}/files", headers=headers,
                         json={"file_type": ft, "file_data": b64,
                               "srv_send_msg": False, "file_name": fn},
                         timeout=30,
                         proxies={"http": None, "https": None})
        fi = r.json().get("file_info", "")
        if not fi:
            return False
        
        # Send message with file
        r2 = requests.post(f"{base}/v2/users/{openid}/messages", headers=headers,
                          json={"content": caption or fn, "msg_type": 7,
                                "media": {"file_info": fi}},
                          timeout=10,
                          proxies={"http": None, "https": None})
        return r2.status_code == 200
    except Exception:
        return False
