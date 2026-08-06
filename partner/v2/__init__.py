"""Partner v2.0 — 感知、操控、浏览器、多媒体、视频、外循环、滚动规划、Loop Engineering 引擎。

Partner v2.0 扩展模块体系，提供完整的"感知→学习→规划→执行→验证→迭代"闭环。

模块结构：
- perception.py: 屏幕/硬件/应用/多模态感知
- control.py: 鼠标/键盘/剪贴板/应用操控
- browser.py: 浏览器自动化（打开/点击/填写/提取/视频）
- media.py: 图表/架构图/截图标注/视觉报告生成
- video.py: 视频截帧/分析/摘要/字幕提取
- multimodal.py: 多模态获取/分析/比较/生成
- outer_loop.py: GitHub搜索/网页检索/知识学习
- loop_engine.py: 滚动规划器/信息缺口检测/Loop Engineering
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]

# ── Convenience re-exports ──
from .perception import (
    atomic_screen_capture,
    atomic_screen_ocr,
    atomic_screen_detect_ui,
    atomic_screen_find,
    atomic_screen_watch,
    atomic_screen_analyze,
    atomic_hardware_info,
    atomic_app_list,
    atomic_app_focus,
)
from .control import (
    atomic_mouse_move,
    atomic_mouse_click,
    atomic_mouse_drag,
    atomic_mouse_scroll,
    atomic_keyboard_type,
    atomic_keyboard_press,
    atomic_clipboard_get,
    atomic_clipboard_set,
    atomic_app_launch,
    atomic_app_close,
    atomic_app_send_keys,
    atomic_app_list_windows,
    atomic_app_click_element,
    atomic_app_screenshot_window,
    atomic_app_list_elements,
)
from .browser import (
    atomic_browser_open,
    atomic_browser_click,
    atomic_browser_type,
    atomic_browser_scroll,
    atomic_browser_extract,
    atomic_browser_wait,
    atomic_browser_video,
    atomic_browser_screenshot,
    atomic_browser_execute,
)
from .media import (
    atomic_gen_chart,
    atomic_gen_diagram,
    atomic_gen_screenshot_annotated,
    atomic_gen_visual_report,
    atomic_gen_meme,
)
from .video import (
    atomic_video_capture_frame,
    atomic_video_extract_frames,
    atomic_video_analyze,
    atomic_video_summarize,
    atomic_video_text_from_audio,
)
from .multimodal import (
    atomic_multimodal_fetch,
    atomic_multimodal_analyze,
    atomic_multimodal_compare,
    atomic_multimodal_generate,
)
from .outer_loop import (
    atomic_github_search,
    atomic_github_clone,
    atomic_v2_web_search,
    atomic_knowledge_learn,
)
from .loop_engine import (
    atomic_goal_parse,
    atomic_rolling_plan,
    atomic_gap_detect,
    atomic_pause_resume,
    atomic_outer_learn,
)


def get_all_events() -> list[tuple[str, str, str, Any, dict]]:
    """Return list of (name, description, exec_method, handler, kwargs) for harness registration.

    Each tuple:
      name: event name (used in plan steps)
      description: human-readable description
      exec_method: "local" | "llm" | "agent"
      handler: callable(ctx, params) -> dict
      kwargs: extra HarnessEventSpec kwargs
    """
    events = [
        # ── Perception (9 events) ──
        ("screen_capture", "截取当前桌面屏幕截图。参数: region(可选), save_path(可选)", "local",
         atomic_screen_capture, {"external_call": False, "produces_artifact": True}),
        ("screen_ocr", "对截图或图片进行OCR文字识别（中英文）。参数: image_path(可选，默认最新截图)", "local",
         atomic_screen_ocr, {"external_call": False}),
        ("screen_detect_ui", "检测屏幕上的UI元素（按钮、输入框、标签等）。参数: image_path(可选), target(可选)", "local",
         atomic_screen_detect_ui, {"external_call": False}),
        ("screen_find", "在屏幕上查找指定图像或文本位置。参数: target(图片路径或文本), confidence(可选)", "local",
         atomic_screen_find, {"external_call": False}),
        ("screen_watch", "持续监控屏幕指定区域的变化。参数: region, timeout, poll_interval, change_threshold", "local",
         atomic_screen_watch, {"external_call": False}),
        ("screen_analyze", "用多模态模型分析屏幕截图内容。参数: image_path(可选), question", "llm",
         atomic_screen_analyze, {"external_call": True}),
        ("hardware_info", "获取CPU/GPU/内存/磁盘等硬件信息。参数: category(可选，默认all)", "local",
         atomic_hardware_info, {"external_call": False}),
        ("app_list", "列出当前系统运行的窗口列表。参数: filter(可选)", "local",
         atomic_app_list, {"external_call": False}),
        ("app_focus", "获取或切换到指定应用程序窗口。参数: target(窗口标题或句柄)", "local",
         atomic_app_focus, {"external_call": False}),

        # ── Control (11 events) ──
        ("mouse_move", "移动鼠标到指定坐标。参数: x, y, duration(可选)", "local",
         atomic_mouse_move, {"external_call": False}),
        ("mouse_click", "在指定位置点击鼠标。参数: x(可选), y(可选), button(可选, left/right/middle), clicks(可选)", "local",
         atomic_mouse_click, {"external_call": False}),
        ("mouse_drag", "从起点拖拽到终点。参数: start_x, start_y, end_x, end_y, duration(可选)", "local",
         atomic_mouse_drag, {"external_call": False}),
        ("mouse_scroll", "滚动鼠标滚轮。参数: clicks(向上为正数), x(可选), y(可选)", "local",
         atomic_mouse_scroll, {"external_call": False}),
        ("keyboard_type", "在指定位置输入文本。参数: text, interval(可选，按键间隔秒)", "local",
         atomic_keyboard_type, {"external_call": False}),
        ("keyboard_press", "按下和释放组合键。参数: keys(列表，如['ctrl','c'])", "local",
         atomic_keyboard_press, {"external_call": False}),
        ("clipboard_get", "读取剪贴板内容（文本或图像）。参数: format(可选, text/image)", "local",
         atomic_clipboard_get, {"external_call": False}),
        ("clipboard_set", "写入内容到剪贴板。参数: content, format(可选, text/image)", "local",
         atomic_clipboard_set, {"external_call": False}),
        ("app_launch", "启动应用程序。参数: command, args(可选), wait(可选)", "local",
         atomic_app_launch, {"external_call": True}),
        ("app_close", "关闭指定应用程序。参数: target(窗口标题或进程名)", "local",
         atomic_app_close, {"external_call": False}),
        ("app_send_keys", "向指定窗口发送按键。参数: target, keys, interval(可选)", "local",
         atomic_app_send_keys, {"external_call": False}),
        ("app_list_windows", "列出 Windows 桌面所有可见窗口。参数: filter(可选)", "local",
         atomic_app_list_windows, {"external_call": True}),
        ("app_click_element", "点击 Windows 窗口中的 UI 元素。参数: target(窗口标题), element(元素名)", "local",
         atomic_app_click_element, {"external_call": True}),
        ("app_screenshot_window", "截取指定 Windows 窗口的截图。参数: target(窗口标题), save_path(可选)", "local",
         atomic_app_screenshot_window, {"external_call": True, "produces_artifact": True}),
        ("app_list_elements", "列出 Windows 窗口中的 UI 控件。参数: target(窗口标题)", "local",
         atomic_app_list_elements, {"external_call": True}),

        # ── Browser (9 events) ──
        ("browser_open", "打开浏览器并访问指定URL。参数: url, headless(可选), browser_type(可选)", "local",
         atomic_browser_open, {"external_call": True}),
        ("browser_click", "点击页面上的元素。参数: selector(CSS/XPath/文本), wait_ms(可选)", "local",
         atomic_browser_click, {"external_call": True}),
        ("browser_type", "在输入框中填写文本。参数: selector, text, clear_first(可选)", "local",
         atomic_browser_type, {"external_call": True}),
        ("browser_scroll", "滚动页面。参数: direction(down/up), amount(像素)", "local",
         atomic_browser_scroll, {"external_call": True}),
        ("browser_extract", "提取页面内容（文本/属性/HTML）。参数: selector(可选,默认body), attribute(可选), format(可选)", "local",
         atomic_browser_extract, {"external_call": True}),
        ("browser_wait", "等待页面元素出现。参数: selector, timeout(可选), state(attached/detached/visible/hidden)", "local",
         atomic_browser_wait, {"external_call": True}),
        ("browser_video", "打开并监控视频播放状态。参数: url, wait_for_play(可选), max_wait(可选)", "local",
         atomic_browser_video, {"external_call": True}),
        ("browser_screenshot", "截取网页截图（全屏或部分）。参数: selector(可选), full_page(可选)", "local",
         atomic_browser_screenshot, {"external_call": True, "produces_artifact": True}),
        ("browser_execute", "在页面执行JavaScript代码。参数: script, args(可选)", "local",
         atomic_browser_execute, {"external_call": True}),

        # ── Media (5 events) ──
        ("gen_chart", "生成数据图表（柱状图/折线图/散点图/饼图）。参数: data, chart_type, title, save_path, color_theme(可选)", "local",
         atomic_gen_chart, {"produces_artifact": True}),
        ("gen_diagram", "生成流程图/架构图/UML图。参数: description, diagram_type, save_path", "local",
         atomic_gen_diagram, {"produces_artifact": True}),
        ("gen_screenshot_annotated", "对截图添加标注（箭头/矩形/文字），生成带标注的图片。参数: image_path, annotations, save_path", "local",
         atomic_gen_screenshot_annotated, {"produces_artifact": True}),
        ("gen_visual_report", "生成可视化报告（包含图表和文字说明）。参数: sections, title, save_path, format(可选, pdf/md)", "local",
         atomic_gen_visual_report, {"produces_artifact": True}),
        ("gen_meme", "生成表情包或示意图。参数: template, top_text, bottom_text, save_path", "local",
         atomic_gen_meme, {"produces_artifact": True}),

        # ── Video (5 events) ──
        ("video_capture_frame", "从视频中截取一帧。参数: video_path, time_sec, save_path", "local",
         atomic_video_capture_frame, {"produces_artifact": True}),
        ("video_extract_frames", "批量提取视频关键帧（按间隔或场景变化）。参数: video_path, interval(可选), scene_detect(可选), max_frames", "local",
         atomic_video_extract_frames, {"produces_artifact": True}),
        ("video_analyze", "分析视频内容（物体检测/运动检测等）。参数: video_path, analysis_type, frame_interval(可选)", "local",
         atomic_video_analyze, {"external_call": False}),
        ("video_summarize", "生成视频摘要（以帧序列形式）。参数: video_path, max_frames(可选), output_format(可选)", "local",
         atomic_video_summarize, {"produces_artifact": True}),
        ("video_text_from_audio", "从视频音频中提取文字（字幕/ASR）。参数: video_path, language(可选), model_size(可选)", "local",
         atomic_video_text_from_audio, {"external_call": False}),

        # ── Multimodal (4 events) ──
        ("multimodal_fetch", "从URL获取网页并提取其中的文本+图像+视频链接。参数: url, extract_images(可选), extract_videos(可选)", "local",
         atomic_multimodal_fetch, {"external_call": True}),
        ("multimodal_analyze", "对多模态内容进行综合分析（文本+图像）。参数: image_paths, text, question, model(可选)", "llm",
         atomic_multimodal_analyze, {"external_call": True}),
        ("multimodal_compare", "比较两个多模态内容。参数: source_a, source_b, aspects", "llm",
         atomic_multimodal_compare, {"external_call": True}),
        ("multimodal_generate", "根据多模态输入生成输出内容。参数: inputs, output_type, parameters", "llm",
         atomic_multimodal_generate, {"external_call": True}),

        # ── Outer Loop / Learning (4 events) ──
        ("github_search", "搜索GitHub仓库。参数: query, language(可选), sort(可选), max_results(可选)", "local",
         atomic_github_search, {"external_call": True}),
        ("github_clone", "克隆GitHub仓库到本地。参数: repo_url, destination, branch(可选)", "local",
         atomic_github_clone, {"external_call": True}),
        ("v2_web_search", "执行网页搜索并解析结果。参数: query, num_results(可选), source(可选, web/github)", "local",
         atomic_v2_web_search, {"external_call": True}),
        ("knowledge_learn", "从搜索结果中学习并记录知识。参数: topic, sources, key_insights(可选), save(可选)", "local",
         atomic_knowledge_learn, {"external_call": False}),

        # ── Loop / Planning (5 events) ──
        ("goal_parse", "解析用户目标为结构化的子目标列表。参数: goal, context(可选)", "local",
         atomic_goal_parse, {"external_call": False}),
        ("rolling_plan", "滚动规划：根据已完成步骤生成后续3-5步。参数: goal, context, completed_steps, steps_per_plan(可选)", "llm",
         atomic_rolling_plan, {"external_call": True}),
        ("gap_detect", "检测当前状态与目标之间的信息/工具/数据缺口。参数: goal, current_state, available_tools(可选)", "llm",
         atomic_gap_detect, {"external_call": True}),
        ("pause_resume", "暂停或恢复当前计划执行。参数: action(pause/resume/status), reason(可选)", "local",
         atomic_pause_resume, {"external_call": False}),
        ("outer_learn", "外循环学习：在检测到缺口时，主动检索并吸收知识。参数: gap_type, gap_description, target", "llm",
         atomic_outer_learn, {"external_call": True}),
    ]
    return events
