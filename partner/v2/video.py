"""
video.py — Handler functions for video processing (frame capture, analysis,
summarization, ASR).

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
    import cv2
except ImportError:
    cv2 = None

try:
    import numpy as np
except ImportError:
    np = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _frames_dir(ctx):
    """Return the video frames storage directory, creating it if needed."""
    working_dir = getattr(ctx, "working_dir", os.getcwd())
    frames_dir = os.path.join(working_dir, "workspace", "state", "video_frames")
    os.makedirs(frames_dir, exist_ok=True)
    return frames_dir


def _read_video(video_path: str):
    """Open a video file and return (cap, fps, total_frames, duration_s).

    Returns (cap, fps, total, duration) on success.
    On failure returns (None, 0, 0, 0).
    """
    if cv2 is None:
        return None, 0, 0, 0
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            cap.release()
            return None, 0, 0, 0
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0
        return cap, fps, total_frames, duration
    except Exception:
        logger.exception("_read_video failed")
        return None, 0, 0, 0


def _scene_change_score(prev_hist, curr_gray, bins=64):
    """Compute histogram difference between two frames as a scene-change score."""
    if prev_hist is None:
        return 0.0
    hist = cv2.calcHist([curr_gray], [0], None, [bins], [0, 256])
    hist = cv2.normalize(hist, hist).flatten()
    score = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CHISQR)
    return score


# ---------------------------------------------------------------------------
# 1. atomic_video_capture_frame
# ---------------------------------------------------------------------------

def atomic_video_capture_frame(ctx, params):
    """Capture a single frame from a video at a specified timestamp.

    Params:
        video_path (str): Path to the video file.
        time_sec  (float): Timestamp in seconds of the frame to capture.
        save_path (str):   Explicit file path to save the frame.

    Returns:
        dict: {ok: bool, path: str}
    """
    video_path = params.get("video_path", "")
    time_sec = params.get("time_sec", 0.0)
    save_path = params.get("save_path", "")

    if cv2 is None:
        return {"ok": False, "path": "", "error": "OpenCV (cv2) is not installed."}

    if not video_path or not os.path.isfile(video_path):
        return {"ok": False, "path": "", "error": f"Video file not found: {video_path}"}

    cap = None
    try:
        cap, fps, total_frames, _ = _read_video(video_path)
        if cap is None:
            return {"ok": False, "path": "", "error": "Could not open video file."}

        target_frame = int(time_sec * fps)
        target_frame = max(0, min(target_frame, total_frames - 1))

        # Seek to frame
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame = cap.read()
        if not ret or frame is None:
            return {"ok": False, "path": "", "error": f"Could not read frame at time_sec={time_sec}"}

        # Determine output path
        if save_path:
            out = save_path
        else:
            frames_dir = _frames_dir(ctx)
            ts = time.strftime("%Y%m%d_%H%M%S")
            base = os.path.splitext(os.path.basename(video_path))[0]
            out = os.path.join(frames_dir, f"{base}_frame_{ts}.jpg")

        os.makedirs(os.path.dirname(out), exist_ok=True)
        cv2.imwrite(out, frame)

        return {"ok": True, "path": out}
    except Exception as exc:
        logger.exception("video_capture_frame failed")
        return {"ok": False, "path": "", "error": str(exc)}
    finally:
        if cap is not None:
            cap.release()


# ---------------------------------------------------------------------------
# 2. atomic_video_extract_frames
# ---------------------------------------------------------------------------

def atomic_video_extract_frames(ctx, params):
    """Extract frames from a video, either at a fixed interval or by scene
    detection.

    Params:
        video_path   (str):  Path to the video file.
        interval     (float): Seconds between frames when using interval mode
                              (default 5.0).
        scene_detect (bool):  Use scene-detection (histogram diff) instead of
                              interval (default False).
        max_frames   (int):   Maximum number of frames to extract (default 50).

    Returns:
        dict: {ok: bool, frames: [{time, path}], count: int}
    """
    video_path = params.get("video_path", "")
    interval = params.get("interval", 5.0)
    scene_detect = params.get("scene_detect", False)
    max_frames = params.get("max_frames", 50)

    if cv2 is None or np is None:
        return {"ok": False, "frames": [], "count": 0, "error": "OpenCV (cv2) or numpy is not installed."}

    if not video_path or not os.path.isfile(video_path):
        return {"ok": False, "frames": [], "count": 0, "error": f"Video file not found: {video_path}"}

    cap = None
    try:
        cap, fps, total_frames, duration = _read_video(video_path)
        if cap is None:
            return {"ok": False, "frames": [], "count": 0, "error": "Could not open video file."}

        frames_dir = _frames_dir(ctx)
        base = os.path.splitext(os.path.basename(video_path))[0]
        extracted = []
        prev_hist = None

        if scene_detect:
            # Scene-detection mode: walk frames sequentially, save when
            # histogram diff exceeds threshold 30.0
            scene_threshold = 30.0
            frame_idx = 0

            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                hist = cv2.calcHist([gray], [0], None, [64], [0, 256])
                hist = cv2.normalize(hist, hist).flatten()

                score = 0.0
                if prev_hist is not None:
                    score = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CHISQR)

                prev_hist = hist

                if score > scene_threshold or len(extracted) == 0:
                    time_sec = frame_idx / fps if fps > 0 else 0
                    out_path = os.path.join(
                        frames_dir, f"{base}_scene_{len(extracted):04d}_{frame_idx}.jpg"
                    )
                    cv2.imwrite(out_path, frame)
                    extracted.append({"time": round(time_sec, 3), "path": out_path})

                    if len(extracted) >= max_frames:
                        break

                frame_idx += 1
        else:
            # Interval mode: sample frames at regular time intervals
            frame_interval = int(interval * fps)
            if frame_interval < 1:
                frame_interval = 1

            frame_idx = 0
            while len(extracted) < max_frames:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break

                if frame_idx % frame_interval == 0:
                    time_sec = frame_idx / fps if fps > 0 else 0
                    out_path = os.path.join(
                        frames_dir, f"{base}_interval_{len(extracted):04d}_{frame_idx}.jpg"
                    )
                    cv2.imwrite(out_path, frame)
                    extracted.append({"time": round(time_sec, 3), "path": out_path})

                frame_idx += 1

        return {"ok": True, "frames": extracted, "count": len(extracted)}
    except Exception as exc:
        logger.exception("video_extract_frames failed")
        return {"ok": False, "frames": [], "count": 0, "error": str(exc)}
    finally:
        if cap is not None:
            cap.release()


# ---------------------------------------------------------------------------
# 3. atomic_video_analyze
# ---------------------------------------------------------------------------

def atomic_video_analyze(ctx, params):
    """Analyze video content — motion, object count, or summary.

    Params:
        video_path     (str):   Path to the video file.
        analysis_type  (str):   One of 'motion', 'object_count', 'summary'
                                (default 'motion').
        frame_interval (int):   Process every N-th frame (default 10).

    Returns:
        dict: {ok: bool, analysis: dict}
    """
    video_path = params.get("video_path", "")
    analysis_type = params.get("analysis_type", "motion")
    frame_interval = params.get("frame_interval", 10)

    if cv2 is None or np is None:
        return {"ok": False, "analysis": {}, "error": "OpenCV (cv2) or numpy is not installed."}

    if not video_path or not os.path.isfile(video_path):
        return {"ok": False, "analysis": {}, "error": f"Video file not found: {video_path}"}

    cap = None
    try:
        cap, fps, total_frames, duration = _read_video(video_path)
        if cap is None:
            return {"ok": False, "analysis": {}, "error": "Could not open video file."}

        analysis = {
            "video_path": video_path,
            "fps": fps,
            "total_frames": total_frames,
            "duration_s": round(duration, 3),
            "analysis_type": analysis_type,
        }

        if analysis_type == "motion":
            # Use background subtractor to detect motion
            bg_subtractor = cv2.createBackgroundSubtractorMOG2()
            motion_frame_count = 0
            motion_ratio_sum = 0.0
            frame_count = 0

            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break

                if frame_count % frame_interval == 0:
                    fg_mask = bg_subtractor.apply(frame)
                    if np is not None:
                        motion_pixels = np.count_nonzero(fg_mask)
                        total_pixels = fg_mask.size
                        ratio = motion_pixels / total_pixels if total_pixels > 0 else 0
                        motion_ratio_sum += ratio
                        if motion_pixels > 0:
                            motion_frame_count += 1

                frame_count += 1

            processed_frames = frame_count // frame_interval if frame_interval > 0 else 0
            avg_motion = motion_ratio_sum / processed_frames if processed_frames > 0 else 0

            analysis["motion"] = {
                "frames_with_motion": motion_frame_count,
                "total_processed_frames": processed_frames,
                "average_motion_ratio": round(avg_motion, 6),
                "motion_percent": round(motion_frame_count / processed_frames * 100, 2)
                if processed_frames > 0 else 0,
            }

        elif analysis_type == "object_count":
            # Estimate foreground objects via connected components on
            # background-subtracted frames (simple heuristic)
            bg_subtractor = cv2.createBackgroundSubtractorMOG2()
            object_counts = []
            frame_count = 0

            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break

                if frame_count % frame_interval == 0:
                    fg_mask = bg_subtractor.apply(frame)
                    # Clean up mask
                    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
                    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
                    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)

                    if np is not None:
                        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
                            fg_mask, connectivity=8
                        )
                        # Subtract background label (0)
                        obj_count = num_labels - 1
                        object_counts.append(obj_count)

                frame_count += 1

            avg_objects = sum(object_counts) / len(object_counts) if object_counts else 0
            analysis["object_count"] = {
                "average_object_count": round(avg_objects, 2),
                "max_object_count": max(object_counts) if object_counts else 0,
                "min_object_count": min(object_counts) if object_counts else 0,
                "samples": len(object_counts),
            }

        elif analysis_type == "summary":
            # Generate a lightweight text summary of video characteristics
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            codec = int(cap.get(cv2.CAP_PROP_FOURCC))
            codec_str = "".join(chr((codec >> 8 * i) & 0xFF) for i in range(4)) if codec else "unknown"

            analysis["summary"] = {
                "resolution": f"{width}x{height}",
                "codec": codec_str,
                "fps": fps,
                "duration_s": round(duration, 3),
                "total_frames": total_frames,
                "aspect_ratio": round(width / height, 4) if height > 0 else 0,
            }

        else:
            return {
                "ok": False,
                "analysis": {},
                "error": f"Unknown analysis_type: {analysis_type}. "
                f"Use 'motion', 'object_count', or 'summary'.",
            }

        return {"ok": True, "analysis": analysis}
    except Exception as exc:
        logger.exception("video_analyze failed")
        return {"ok": False, "analysis": {}, "error": str(exc)}
    finally:
        if cap is not None:
            cap.release()


# ---------------------------------------------------------------------------
# 4. atomic_video_summarize
# ---------------------------------------------------------------------------

def atomic_video_summarize(ctx, params):
    """Generate a video summary by extracting key frames via scene detection.

    Params:
        video_path    (str): Path to the video file.
        max_frames    (int): Maximum number of summary frames (default 10).
        output_format (str): Unused reserved parameter for future extensibility.

    Returns:
        dict: {ok: bool, summary_frames: [str], count: int, duration: float}
    """
    video_path = params.get("video_path", "")
    max_frames = params.get("max_frames", 10)
    _ = params.get("output_format", "")  # reserved for future use

    if cv2 is None or np is None:
        return {
            "ok": False,
            "summary_frames": [],
            "count": 0,
            "duration": 0.0,
            "error": "OpenCV (cv2) or numpy is not installed.",
        }

    if not video_path or not os.path.isfile(video_path):
        return {
            "ok": False,
            "summary_frames": [],
            "count": 0,
            "duration": 0.0,
            "error": f"Video file not found: {video_path}",
        }

    cap = None
    try:
        cap, fps, total_frames, duration = _read_video(video_path)
        if cap is None:
            return {
                "ok": False,
                "summary_frames": [],
                "count": 0,
                "duration": 0.0,
                "error": "Could not open video file.",
            }

        frames_dir = _frames_dir(ctx)
        base = os.path.splitext(os.path.basename(video_path))[0]

        # Scene detection to pick key frames
        scene_threshold = 30.0
        summary_frames = []
        prev_hist = None
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            hist = cv2.calcHist([gray], [0], None, [64], [0, 256])
            hist = cv2.normalize(hist, hist).flatten()

            score = 0.0
            if prev_hist is not None:
                score = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CHISQR)

            prev_hist = hist

            # Accept the first frame and frames where scene change detected
            if score > scene_threshold or len(summary_frames) == 0:
                time_sec = frame_idx / fps if fps > 0 else 0
                out_path = os.path.join(
                    frames_dir, f"{base}_summary_{len(summary_frames):04d}_{frame_idx}.jpg"
                )
                cv2.imwrite(out_path, frame)
                summary_frames.append(out_path)

                if len(summary_frames) >= max_frames:
                    break

            frame_idx += 1

        # Even sampling: if we didn't get enough frames via scene detection,
        # fall back to uniform time-based sampling
        if len(summary_frames) < max_frames and total_frames > 0:
            # We already captured the first frame; add more uniformly
            # Only add if we have fewer than max_frames
            needed = max_frames - len(summary_frames)
            if needed > 0 and len(summary_frames) > 0:
                step = total_frames // (max_frames)
                for i in range(1, max_frames):
                    target = i * step
                    if target >= total_frames:
                        break
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        time_sec = target / fps if fps > 0 else 0
                        out_path = os.path.join(
                            frames_dir, f"{base}_summary_uniform_{i:04d}_{target}.jpg"
                        )
                        cv2.imwrite(out_path, frame)
                        summary_frames.append(out_path)
                        if len(summary_frames) >= max_frames:
                            break

        return {
            "ok": True,
            "summary_frames": summary_frames[:max_frames],
            "count": min(len(summary_frames), max_frames),
            "duration": round(duration, 3),
        }
    except Exception as exc:
        logger.exception("video_summarize failed")
        return {
            "ok": False,
            "summary_frames": [],
            "count": 0,
            "duration": 0.0,
            "error": str(exc),
        }
    finally:
        if cap is not None:
            cap.release()


# ---------------------------------------------------------------------------
# 5. atomic_video_text_from_audio
# ---------------------------------------------------------------------------

def atomic_video_text_from_audio(ctx, params):
    """Extract speech text from video audio via ASR / speech-to-text.

    Tries whisper (openai-whisper) first, then faster-whisper as fallback.
    Returns a 'not installed' message if neither is available.

    Params:
        video_path (str):  Path to the video file.
        language   (str):  Language code (default 'zh').
        model_size (str):  Model size: 'tiny', 'base', 'small', 'medium',
                           'large' (default 'tiny').

    Returns:
        dict: {ok: bool, text: str, segments: [{start, end, text}]}
    """
    video_path = params.get("video_path", "")
    language = params.get("language", "zh")
    model_size = params.get("model_size", "tiny")

    if not video_path or not os.path.isfile(video_path):
        return {"ok": False, "text": "", "segments": [], "error": f"Video file not found: {video_path}"}

    # ------------------------------------------------------------------
    # Try whisper (openai-whisper)
    # ------------------------------------------------------------------
    try:
        import whisper

        model = whisper.load_model(model_size)
        result = model.transcribe(video_path, language=language)

        text = (result.get("text") or "").strip()
        segments_raw = result.get("segments", [])

        segments = []
        for seg in segments_raw:
            segments.append(
                {
                    "start": round(seg.get("start", 0), 3),
                    "end": round(seg.get("end", 0), 3),
                    "text": (seg.get("text") or "").strip(),
                }
            )

        return {"ok": True, "text": text, "segments": segments}
    except ImportError:
        logger.info("openai-whisper not available, trying faster-whisper...")
    except Exception as exc:
        logger.warning(f"whisper transcription failed: {exc}")

    # ------------------------------------------------------------------
    # Fallback: faster-whisper
    # ------------------------------------------------------------------
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments_raw, info = model.transcribe(video_path, language=language)

        text_parts = []
        segments = []
        for seg in segments_raw:
            s = {
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": seg.text.strip(),
            }
            segments.append(s)
            text_parts.append(seg.text.strip())

        return {
            "ok": True,
            "text": " ".join(text_parts),
            "segments": segments,
        }
    except ImportError:
        logger.info("faster-whisper also not available.")
    except Exception as exc:
        logger.warning(f"faster-whisper transcription failed: {exc}")

    # ------------------------------------------------------------------
    # Neither is installed
    # ------------------------------------------------------------------
    return {
        "ok": False,
        "text": "",
        "segments": [],
        "error": (
            "Neither openai-whisper nor faster-whisper is installed. "
            "Please install one of them, e.g.: "
            "pip install openai-whisper  or  pip install faster-whisper"
        ),
        "note": "not installed",
    }
