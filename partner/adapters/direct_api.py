"""DirectAPIAdapter - calls DeepSeek API directly, bypassing hermes subprocess.

Avoids the subprocess.PIPE deadlock on WSL (Python 3.13 parent, Python 3.11 child)
by using simple HTTP requests.
"""
import json, os, time, logging
import requests
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

def chat(prompt: str, max_tokens: int = 4096, temperature: float = 0.0,
         purpose: str = "chat", timeout: int = 60) -> str:
    """Send a chat request directly to DeepSeek API.
    
    Returns the model's response text, or empty string on failure.
    """
    if not API_KEY:
        logger.error("[DirectAPI] No DeepSeek API key found")
        return ""

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    
    # Bypass any system proxy that might interfere
    proxies = {"http": None, "https": None}
    
    payload = {
        "model": MODEL,
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
        r = requests.post(
            f"{API_BASE}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=timeout,
            proxies=proxies,
        )
        elapsed = time.time() - start
        if r.status_code == 200:
            data = r.json()
            resp_content = data["choices"][0]["message"]["content"]
            logger.info(f"[DirectAPI] {purpose} OK in {elapsed:.1f}s, prompt={len(prompt)}chars response={len(resp_content)}chars")
            # Fallback: if v4-flash returns empty on batch_plan, retry with v4-pro
            if purpose == "batch_plan" and len(resp_content) < 10 and payload.get("model") == "deepseek-v4-flash":
                logger.warning(f"[DirectAPI] v4-flash returned empty, falling back to v4-pro...")
                fallback_payload = dict(payload)
                fallback_payload["model"] = "deepseek-v4-pro"
                try:
                    fr = requests.post(
                        f"{API_BASE}/v1/chat/completions",
                        headers=headers,
                        json=fallback_payload,
                        timeout=max(timeout, 120),
                        proxies=proxies,
                    )
                    if fr.status_code == 200:
                        fb_data = fr.json()
                        fb_content = fb_data["choices"][0]["message"]["content"]
                        logger.info(f"[DirectAPI] v4-pro fallback OK in {time.time()-start:.1f}s, {len(fb_content)} chars")
                        return fb_content
                    else:
                        logger.warning(f"[DirectAPI] v4-pro fallback HTTP {fr.status_code}")
                except Exception as fe:
                    logger.warning(f"[DirectAPI] v4-pro fallback failed: {fe}")
            return resp_content
        else:
            logger.warning(f"[DirectAPI] {purpose} HTTP {r.status_code} in {elapsed:.1f}s: {r.text[:200]}")
            return ""
    except Exception as e:
        elapsed = time.time() - start
        logger.warning(f"[DirectAPI] {purpose} failed in {elapsed:.1f}s: {e}")
        return ""
