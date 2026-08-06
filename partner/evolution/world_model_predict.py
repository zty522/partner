"""世界模型预测集成 - 在自进化中调用 AETHER 预测 GUI 修改效果。

流程：
  截图(before) → AETHER 预测(修改后界面视频) → 实际修改 → 截图(after)
                                                    ↓
                                              "预测 vs 实际" 对比
"""
import asyncio
import base64
import json
import logging
import os
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional

from PIL import Image

logger = logging.getLogger(__name__)

# World Model 服务器地址
WM_ENDPOINT = os.environ.get("WORLD_MODEL_ENDPOINT", "http://localhost:8100")


async def predict_gui_change(
    before_screenshot_path: str,
    change_descriptions: List[str],
    session_tag: str = "",
    timeout: int = 180,
) -> Dict[str, Any]:
    """用 AETHER 预测 GUI 修改后的视觉效果。

    Args:
        before_screenshot_path: "修改前"截图的本地路径
        change_descriptions: 计划执行的修改描述列表
        session_tag: 可选的会话标签（用于输出目录命名）
        timeout: 请求超时（秒）

    Returns:
        {
            "status": "success" | "error",
            "video_path": str | None,        # 预测视频的本地路径
            "frames_dir": str | None,         # 预测帧目录
            "session_dir": str | None,        # 完整输出目录
            "predicted_images": [str],        # 预测的关键帧路径
            "error": str | None,
        }
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tag = session_tag or f"prediction_{ts}"

    try:
        # 1. 读取截图并 base64 编码
        if not os.path.isfile(before_screenshot_path):
            return {"status": "error", "error": f"Screenshot not found: {before_screenshot_path}"}

        with open(before_screenshot_path, "rb") as f:
            img_data = f.read()
        img_b64 = base64.b64encode(img_data).decode("utf-8")

        # 2. 构造请求
        prompt_text = "; ".join(change_descriptions[:10])

        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{WM_ENDPOINT}/simulate",
                json={
                    "plan": [
                        {"action": desc, "parameters": {}}
                        for desc in change_descriptions
                    ],
                    "state": {
                        "task_id": f"evolution_prediction_{tag}",
                        "user_message": f"自进化界面修改预测: {prompt_text}",
                        "image": img_b64,           # ← 传入真实截图！
                    },
                },
            )
            resp.raise_for_status()
            result = resp.json()

        if result.get("status") != "success":
            return {
                "status": "error",
                "error": result.get("error", result.get("status", "unknown")),
            }

        # 3. 在预测输出目录中标记预测帧
        local_dir = result.get("local_session_dir", "")
        video_path = result.get("video_path", "")
        frames_dir = result.get("frames_dir", "")

        # 找出预测视频中的关键帧（中间帧和最后一帧）
        predicted_images = []
        if frames_dir and os.path.isdir(frames_dir):
            frames = sorted(os.listdir(frames_dir))
            # 取中间帧和最后帧作为关键预测帧
            for idx in [len(frames) // 2, len(frames) - 1]:
                if idx < len(frames):
                    predicted_images.append(os.path.join(frames_dir, frames[idx]))

        logger.info(
            "GUI change prediction complete: video=%s, frames=%d, dir=%s",
            video_path, result.get("frames_generated", 0), local_dir
        )

        return {
            "status": "success",
            "video_path": video_path,
            "frames_dir": frames_dir,
            "session_dir": local_dir,
            "predicted_images": predicted_images,
            "frames_generated": result.get("frames_generated", 0),
            "elapsed_seconds": result.get("elapsed_seconds", 0),
            "prompt": "自进化界面修改预测: " + prompt_text,
        }

    except Exception as e:
        logger.warning("World model GUI prediction failed: %s", e)
        return {"status": "error", "error": str(e)}


async def world_model_predict_in_evolution(
    workspace: str,
    before_screenshot: str,
    gaps: List[Dict],
    plans: List[Dict],
    output_dir: str = "",
    progress_callback=None,
) -> Dict[str, Any]:
    """在自进化流程中调用世界模型预测。

    这个函数在 '截图(before)' 之后、'实际修改' 之前调用。
    输出保存到 workspace 的世界模型输出目录。

    Args:
        workspace: Partner 工作区路径
        before_screenshot: "修改前"截图路径
        gaps: 当前自进化周期的 gap 列表
        plans: 生成的改进方案列表
        output_dir: 输出目录（默认 workspace/world_model_outputs/）
        progress_callback: 进度回调

    Returns:
        {
            "status": "success" | "skipped" | "error",
            "prediction": { ... },   # 预测结果
            "reason": str,
        }
    """
    if not before_screenshot or not os.path.isfile(before_screenshot):
        if progress_callback:
            await progress_callback("⏭️ 世界模型：无截图，跳过预测")
        return {"status": "skipped", "reason": "no_before_screenshot"}

    if not gaps and not plans:
        if progress_callback:
            await progress_callback("⏭️ 世界模型：无修改方案，跳过预测")
        return {"status": "skipped", "reason": "no_plans"}

    # 提取修改描述
    change_descriptions = []
    for g in (gaps or []):
        desc = g.get("description", str(g))[:200] if isinstance(g, dict) else str(g)[:200]
        change_descriptions.append(desc)
    for p in (plans or []):
        desc = p.get("description", p.get("target_module", str(p)))[:200] if isinstance(p, dict) else str(p)[:200]
        change_descriptions.append(desc)

    if not change_descriptions:
        change_descriptions = ["GUI modification (no detailed description)"]

    session_tag = f"evolution_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    if progress_callback:
        msg = f"🎬 世界模型正在预测界面修改效果 ({len(change_descriptions)} 项变更)..."
        await progress_callback(msg)

    # 调用 AETHER 预测
    result = await predict_gui_change(
        before_screenshot_path=before_screenshot,
        change_descriptions=change_descriptions,
        session_tag=session_tag,
    )

    if result.get("status") == "success":
        video = result.get("video_path", "")
        frames = result.get("frames_generated", 0)
        elapsed = result.get("elapsed_seconds", 0)

        if progress_callback:
            msg = (
                f"🎬 世界模型预测完成: 生成 {frames} 帧 ({elapsed:.1f}s)"
            )
            if video:
                msg += f"\n📹 预测视频: {video}"
            await progress_callback(msg)

        logger.info(
            "World model evolution prediction saved: video=%s, dir=%s",
            video, result.get("session_dir")
        )
    else:
        if progress_callback:
            await progress_callback(
                f"⚠️ 世界模型预测失败: {result.get('error', 'unknown')}（不影响自进化继续执行）"
            )

    return {
        "status": result.get("status", "error"),
        "prediction": result,
        "reason": "",
    }
