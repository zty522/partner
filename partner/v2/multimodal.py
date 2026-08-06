"""
multimodal.py — Multimodal analysis: fetch, analyze, compare, generate.

Each handler has signature: atomic_XXX(ctx, params) -> dict.
All imports are try/except guarded for resilience.
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guarded imports
# ---------------------------------------------------------------------------

try:
    import requests
except ImportError:
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    from PIL import Image
except ImportError:
    Image = None

# ---------------------------------------------------------------------------
# 1. atomic_multimodal_fetch
# ---------------------------------------------------------------------------

def atomic_multimodal_fetch(ctx, params):
    """Fetch a web page and extract text content + image/video URLs.

    Params:
        url (str):                  Target URL to fetch.
        extract_images (bool):      Whether to extract image URLs (default True).
        extract_videos (bool):      Whether to extract video URLs (default False).

    Returns:
        dict: {ok: bool, title: str, text: str, images: [str], videos: [str]}
    """
    _ = ctx
    if requests is None or BeautifulSoup is None:
        return {
            "ok": False,
            "title": "",
            "text": "",
            "images": [],
            "videos": [],
            "error": "Missing dependencies: requests and/or beautifulsoup4",
        }

    url = params.get("url", "")
    extract_images = params.get("extract_images", True)
    extract_videos = params.get("extract_videos", False)

    if not url:
        return {"ok": False, "title": "", "text": "", "images": [], "videos": [],
                "error": "url parameter is required"}

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()

        # Detect encoding from headers or content
        resp.encoding = resp.apparent_encoding or resp.encoding

        soup = BeautifulSoup(resp.text, "html.parser")

        # Title
        title = ""
        if soup.title:
            title = soup.title.get_text(strip=True)

        # Text content (remove script/style)
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)

        # Images
        images = []
        if extract_images:
            seen = set()
            for img in soup.find_all("img"):
                src = img.get("src")
                if src and src not in seen:
                    seen.add(src)
                    # Make absolute URLs
                    if src.startswith("//"):
                        images.append("https:" + src)
                    elif src.startswith("/"):
                        from urllib.parse import urljoin
                        images.append(urljoin(url, src))
                    elif src.startswith(("http://", "https://")):
                        images.append(src)
                    else:
                        from urllib.parse import urljoin
                        images.append(urljoin(url, src))

        # Videos
        videos = []
        if extract_videos:
            seen = set()
            # <video> tags
            for vid in soup.find_all("video"):
                src = vid.get("src")
                if src and src not in seen:
                    seen.add(src)
                    from urllib.parse import urljoin
                    videos.append(urljoin(url, src))
                # <source> children
                for source in vid.find_all("source"):
                    src = source.get("src")
                    if src and src not in seen:
                        seen.add(src)
                        from urllib.parse import urljoin
                        videos.append(urljoin(url, src))

            # iframe video embeds (YouTube, Vimeo, etc.)
            for iframe in soup.find_all("iframe"):
                src = iframe.get("src", "")
                if any(domain in src for domain in
                       ("youtube.com", "youtu.be", "vimeo.com", "player.", "embed.")):
                    if src and src not in seen:
                        seen.add(src)
                        from urllib.parse import urljoin
                        videos.append(urljoin(url, src))

        return {
            "ok": True,
            "title": title,
            "text": text,
            "images": images,
            "videos": videos,
        }

    except requests.exceptions.Timeout:
        return {"ok": False, "title": "", "text": "", "images": [], "videos": [],
                "error": f"Request timed out after 10s: {url}"}
    except requests.exceptions.RequestException as exc:
        return {"ok": False, "title": "", "text": "", "images": [], "videos": [],
                "error": f"Request failed: {exc}"}
    except Exception as exc:
        logger.exception("multimodal_fetch failed")
        return {"ok": False, "title": "", "text": "", "images": [], "videos": [],
                "error": str(exc)}


# ---------------------------------------------------------------------------
# 2. atomic_multimodal_analyze
# ---------------------------------------------------------------------------

def atomic_multimodal_analyze(ctx, params):
    """Analyze multimodal content — extract structured metadata from images + text.

    Uses Ollama vision model (llava/moondream) for actual image understanding.
    Falls back to PIL structural metadata if vision model unavailable.

    Params:
        image_paths (list[str]):  Paths to image files.
        text (str):               Associated text content.
        question (str):           Optional question guiding the analysis.
        model (str):              Vision model to use (default: "llava", "moondream:1.8b")

    Returns:
        dict: {ok: bool, analysis: dict}
    """
    _ = ctx
    image_paths = params.get("image_paths", [])
    text = params.get("text", "")
    question = params.get("question", "")
    model = params.get("model", "llava")

    if not isinstance(image_paths, list):
        image_paths = [image_paths] if image_paths else []

    analysis = {
        "images": [],
        "text": text,
        "question": question,
        "summary": "",
        "vision_analysis": "",
    }

    # ── Step 1: Collect PIL metadata for all images ──
    if Image is not None:
        for path in image_paths:
            info = {"path": path}
            try:
                if not os.path.isfile(path):
                    info["error"] = f"File not found: {path}"
                    analysis["images"].append(info)
                    continue
                img = Image.open(path)
                info["size"] = {"w": img.width, "h": img.height}
                info["mode"] = img.mode
                info["format"] = img.format or "unknown"
                # EXIF data
                exif_data = {}
                try:
                    exif = img.getexif()
                    if exif:
                        for tag_id, value in exif.items():
                            tag_name = Image.ExifTags.TAGS.get(tag_id, str(tag_id))
                            if isinstance(value, bytes):
                                try:
                                    value = value.decode("utf-8", errors="replace")
                                except Exception:
                                    value = str(value)
                            exif_data[tag_name] = value
                except Exception:
                    pass
                info["exif"] = exif_data
            except Exception as exc:
                info["error"] = str(exc)
            analysis["images"].append(info)

    # ── Step 2: Vision model analysis (Ollama llava/moondream) ──
    vision_result = ""
    if image_paths and os.path.isfile(str(image_paths[0])):
        try:
            import base64, json, urllib.request, urllib.error

            # Check if Ollama is running
            try:
                _hc = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
                _models = json.loads(_hc.read().decode()).get("models", [])
                _available = [m["name"] for m in _models]
                _has_vision = any(model in m for m in _available) or any(v in str(_available) for v in ["llava", "moondream"])
            except Exception:
                _available = []
                _has_vision = False

            if _has_vision:
                # Find the best available vision model
                _vision_model = model
                if _vision_model not in str(_available):
                    for _candidate in ["llava", "llava:latest", "moondream:1.8b"]:
                        if any(_candidate in m for m in _available):
                            _vision_model = _candidate
                            break

                with open(image_paths[0], "rb") as _f:
                    _b64 = base64.b64encode(_f.read()).decode()

                _prompt = question if question else "详细描述这张图片中的内容，包括文字、物体、布局等"
                if text:
                    _prompt += f"\n\n附加上下文文字：{text[:500]}"

                _payload = {
                    "model": _vision_model,
                    "prompt": _prompt,
                    "images": [_b64],
                    "stream": False,
                    "options": {"num_ctx": 2048, "temperature": 0.2}
                }

                _req = urllib.request.Request(
                    "http://localhost:11434/api/generate",
                    data=json.dumps(_payload).encode(),
                    headers={"Content-Type": "application/json"}
                )
                _resp = urllib.request.urlopen(_req, timeout=120)
                _result = json.loads(_resp.read().decode())
                vision_result = _result.get("response", "")

                logger.info("[multimodal_analyze] vision model %s: %d chars, eval=%d tokens",
                            _vision_model, len(vision_result),
                            _result.get("eval_count", 0))
            else:
                logger.warning("[multimodal_analyze] no vision model available (have: %s)", _available[:5])
                vision_result = ""
        except Exception as e:
            logger.warning("[multimodal_analyze] vision model failed: %s", e)
            vision_result = f"[vision model unavailable: {e}]"

    analysis["vision_analysis"] = vision_result

    # ── Step 3: Build summary ──
    parts = []
    if analysis["images"]:
        valid = [i for i in analysis["images"] if "error" not in i]
        if valid:
            sizes = [f'{i["size"]["w"]}x{i["size"]["h"]}' for i in valid]
            parts.append(f"{len(valid)} images ({', '.join(sizes)})")
    if text:
        parts.append(f"text: {len(text)} chars")
    if vision_result:
        parts.append(f"vision analysis: {len(vision_result)} chars")
    if question:
        parts.append(f"question: {question[:60]}")
    analysis["summary"] = "; ".join(parts) if parts else "no content to analyze"

    return {"ok": True, "analysis": analysis,
            "note": "PIL metadata + Ollama vision model" if vision_result else "PIL metadata only"}


# ---------------------------------------------------------------------------
# 3. atomic_multimodal_compare
# ---------------------------------------------------------------------------

def atomic_multimodal_compare(ctx, params):
    """Compare two multimodal sources across specified aspects.

    For each aspect, the function computes a structured difference
    between source_a and source_b based on available text and image metadata.

    Params:
        source_a (dict): {text?: str, image_paths?: [str]}
        source_b (dict): {text?: str, image_paths?: [str]}
        aspects (list[str]): Aspects to compare, e.g. ["content", "images", "length"].

    Returns:
        dict: {ok: bool, comparison: {aspect: {a: any, b: any, difference: str}}}
    """
    _ = ctx
    source_a = params.get("source_a", {})
    source_b = params.get("source_b", {})
    aspects = params.get("aspects", [])

    if not isinstance(aspects, list):
        aspects = [aspects] if aspects else []

    if not aspects:
        aspects = ["content", "images", "length"]

    comparison = {}

    # Extract text and image paths, defaulting to empty
    text_a = (source_a.get("text") or "").strip()
    text_b = (source_b.get("text") or "").strip()
    images_a = source_a.get("image_paths") or []
    images_b = source_b.get("image_paths") or []

    try:
        for aspect in aspects:
            a_val = None
            b_val = None
            diff = ""

            if aspect == "content":
                a_val = text_a
                b_val = text_b
                if text_a and text_b:
                    # Simple word-count based difference
                    words_a = set(text_a.lower().split())
                    words_b = set(text_b.lower().split())
                    common = len(words_a & words_b)
                    total = len(words_a | words_b)
                    overlap_pct = round(common / total * 100, 1) if total else 0
                    diff = f"Word overlap: {overlap_pct}% ({common}/{total})"
                elif text_a and not text_b:
                    diff = "Only source A has text content"
                elif not text_a and text_b:
                    diff = "Only source B has text content"
                else:
                    diff = "Neither source has text content"

            elif aspect == "images":
                a_val = images_a
                b_val = images_b
                if images_a and images_b:
                    set_a = set(images_a)
                    set_b = set(images_b)
                    shared = len(set_a & set_b)
                    diff = (
                        f"A has {len(images_a)} image(s), B has {len(images_b)} image(s); "
                        f"{shared} shared"
                    )
                elif images_a and not images_b:
                    diff = f"A has {len(images_a)} image(s), B has none"
                elif not images_a and images_b:
                    diff = f"A has no images, B has {len(images_b)} image(s)"
                else:
                    diff = "Neither source has images"

            elif aspect == "length":
                a_val = len(text_a)
                b_val = len(text_b)
                delta = len(text_a) - len(text_b)
                diff = f"A has {len(text_a)} chars, B has {len(text_b)} chars (diff: {delta:+d})"

            else:
                # Generic aspect — try to extract same key from both sources
                a_val = source_a.get(aspect)
                b_val = source_b.get(aspect)
                if a_val is None and b_val is None:
                    diff = f"Aspect '{aspect}' not found in either source"
                elif a_val == b_val:
                    diff = "Values are identical"
                else:
                    diff = f"Values differ: A={a_val!r} vs B={b_val!r}"

            comparison[aspect] = {
                "a": a_val,
                "b": b_val,
                "difference": diff,
            }

        return {"ok": True, "comparison": comparison}

    except Exception as exc:
        logger.exception("multimodal_compare failed")
        return {"ok": False, "comparison": {}, "error": str(exc)}


# ---------------------------------------------------------------------------
# 4. atomic_multimodal_generate
# ---------------------------------------------------------------------------

def atomic_multimodal_generate(ctx, params):
    """Generate output from multimodal inputs.

    Processes a list of inputs (text snippets, image files) and produces
    a structured output of the requested type.

    Params:
        inputs (list[dict]):  List of input items, each {type: str, path?: str, content?: str}.
                              type can be "text" or "image".
        output_type (str):    One of "description", "summary", "report" (default "summary").
        parameters (dict):    Optional extra parameters (format, max_length, etc.).

    Returns:
        dict: {ok: bool, output: dict}
              output = {
                  output_type: str,
                  processed_inputs: [{"type": str, "path"?, "content"?, "info"?}],
                  result: str,
                  note: str
              }
    """
    _ = ctx
    if Image is None:
        return {"ok": False, "output": {},
                "error": "Missing dependency: Pillow (PIL)"}

    inputs = params.get("inputs", [])
    output_type = params.get("output_type", "summary")
    parameters = params.get("parameters", {})

    if not isinstance(inputs, list):
        inputs = [inputs] if inputs else []

    if output_type not in ("description", "summary", "report"):
        output_type = "summary"

    output = {
        "output_type": output_type,
        "processed_inputs": [],
        "result": "",
        "note": "",
    }

    try:
        for item in inputs:
            item_type = item.get("type", "").lower()
            processed = {"type": item_type}

            if item_type == "text":
                content = item.get("content", "")
                processed["content"] = content
                output["processed_inputs"].append(processed)

            elif item_type == "image":
                path = item.get("path", "")
                processed["path"] = path
                info = {}
                try:
                    if os.path.isfile(path):
                        img = Image.open(path)
                        info["size"] = {"w": img.width, "h": img.height}
                        info["mode"] = img.mode
                        info["format"] = img.format or "unknown"
                    else:
                        info["error"] = "File not found"
                except Exception as exc:
                    info["error"] = str(exc)
                processed["info"] = info
                output["processed_inputs"].append(processed)

            else:
                output["processed_inputs"].append({
                    "type": item_type,
                    "error": f"Unknown input type: {item_type}",
                })

        # Build result based on output_type
        text_parts = []
        image_descs = []
        for p in output["processed_inputs"]:
            if p["type"] == "text" and "content" in p:
                text_parts.append(p["content"])
            elif p["type"] == "image" and "info" in p:
                info = p["info"]
                if "error" not in info:
                    image_descs.append(
                        f"{p.get('path', '?')}: {info['size']['w']}x{info['size']['h']} "
                        f"{info['mode']} {info['format']}"
                    )
                else:
                    image_descs.append(f"{p.get('path', '?')}: {info['error']}")

        combined = []
        if image_descs:
            combined.append(f"Images ({len(image_descs)}):\n" + "\n".join(image_descs))
        if text_parts:
            combined.append(f"Text ({len(text_parts)} segment(s)):\n" + "\n".join(text_parts))

        raw = "\n\n".join(combined) if combined else "No inputs provided"

        if output_type == "description":
            output["result"] = raw
            output["note"] = (
                "Structural description only (PIL metadata). "
                "For semantic analysis, pipe through an LLM."
            )
        elif output_type == "summary":
            lines = []
            n_text = len(text_parts)
            n_img = len([p for p in output["processed_inputs"] if p["type"] == "image"
                         and "info" in p and "error" not in p.get("info", {})])
            n_img_err = len([p for p in output["processed_inputs"] if p["type"] == "image"
                             and "info" in p and "error" in p.get("info", {})])
            lines.append(f"{n_text} text segment(s), {n_img} image(s)")
            if n_img_err:
                lines.append(f"{n_img_err} image(s) had errors")
            total_chars = sum(len(t) for t in text_parts)
            if total_chars:
                lines.append(f"Total text length: {total_chars} chars")
            output["result"] = "; ".join(lines)
            output["note"] = "Summary based on structural metadata."
        elif output_type == "report":
            output["result"] = raw
            output["note"] = (
                "Raw structural report. For a formatted narrative report, "
                "pipe through an LLM with a system prompt."
            )

        # Apply any format parameter hint
        fmt = parameters.get("format")
        if fmt:
            output["format"] = fmt

        return {"ok": True, "output": output}

    except Exception as exc:
        logger.exception("multimodal_generate failed")
        return {"ok": False, "output": {}, "error": str(exc)}
