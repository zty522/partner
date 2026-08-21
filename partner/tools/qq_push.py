"""Standalone QQ file push — verified working (Sprint 7)."""
import os, json, base64, asyncio, aiohttp, logging

logger = logging.getLogger(__name__)

async def _push(ws, path, caption=""):
    cfg = json.load(open(os.path.join(ws, "config", "qq_config.json")))
    ctx = json.load(open(os.path.join(ws, "state", "qq_user_context.json")))
    app_id, secret, openid = cfg["app_id"], cfg.get("app_secret",""), ctx.get("openid","")
    sandbox = cfg.get("is_sandbox", False)
    base = "https://api.sgroup.qq.com" if not sandbox else "https://sandbox.api.sgroup.qq.com"
    
    async with aiohttp.ClientSession() as s:
        r = await s.post("https://bots.qq.com/app/getAppAccessToken", json={"appId": app_id, "clientSecret": secret})
        token = (await r.json()).get("access_token","")
    if not token: return False
    
    with open(path, "rb") as f: data = f.read()
    fn = os.path.basename(path)
    ext = os.path.splitext(fn)[1].lower()
    ft = 1 if ext in ('.png','.jpg','.jpeg','.gif','.webp','.bmp') else 4
    b64 = base64.b64encode(data).decode("ascii")
    h = {"Authorization": f"QQBot {token}", "Content-Type": "application/json", "X-Union-Appid": app_id}
    
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{base}/v2/users/{openid}/files", headers=h, json={"file_type":ft,"file_data":b64,"srv_send_msg":False,"file_name":fn})
        fi = (await r.json()).get("file_info","")
        if not fi: return False
        r2 = await s.post(f"{base}/v2/users/{openid}/messages", headers=h, json={"content":caption or fn,"msg_type":7,"media":{"file_info":fi}})
        return r2.status == 200

def push_file(ws, path, caption=""):
    try: return asyncio.run(_push(ws, path, caption))
    except: return False

def push_deliverables(ws, caption="", _workspace=None):
    d = os.path.join(ws, "deliverables")
    if not os.path.exists(d): return {"ok": False}
    results = {}
    for f in sorted(os.listdir(d)):
        fp = os.path.join(d, f)
        if os.path.isfile(fp) and os.path.getsize(fp) > 100:
            ok = push_file(ws, fp, caption or f)
            results[f] = ok
            logger.info("[QQ-PUSH] %s → %s", f, "OK" if ok else "FAIL")
    pushed = sum(1 for v in results.values() if v)
    total = len(results)
    logger.info("[QQ-PUSH] Summary: %d/%d files pushed", pushed, total)
    return {"ok": True, "pushed": pushed, "total": total, "results": results}
