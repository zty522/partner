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
from .push_events import atomic_push_files
from .candidate_events import atomic_execute_candidate
from .gap_events import atomic_ensure_tool
from .vision_events import atomic_read_image
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
    atomic_xhs_open_publish_editor,
    atomic_xhs_inspect_upload_requirements,
)
from .multimodal_browser_events import (
    atomic_multimodal_browser_observe,
    atomic_multimodal_community_read,
    atomic_multimodal_login_resume,
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
from .capability_events import (
    atomic_capability_inventory,
    atomic_write_design,
)
from .pdf_events import atomic_generate_pdf, atomic_generate_detailed_pdf
from .molecular_events import atomic_molecular_generation_benchmark
from .molecular_diversity_events import atomic_molecular_diversity_benchmark
from .molecular_iteration_events import (
    atomic_molecular_synth_baseline_benchmark,
    atomic_molecular_goal_optimization_benchmark,
)
from .repair_events import (
    atomic_auto_repair_plan, atomic_batch_plan_fallback,
    atomic_handle_login_wall, atomic_write_artifact_fallback,
    atomic_open_login_on_confirm, atomic_open_browser_foreground_and_notify,
    atomic_resume_after_login, atomic_verify_login_and_continue,
    atomic_skip_login, atomic_send_user_text,
)
from .chart_events import atomic_create_chart
from .iteration_events import (
    atomic_understand_intent,
    atomic_write_plan,
    atomic_strict_reflect,
    atomic_next_iteration,
)
from .governance_events import (
    atomic_select_context,
    atomic_record_iteration,
    atomic_request_next_action,
    atomic_record_issue,
    atomic_start_evolution_experiment,
    atomic_decide_evolution_experiment,
    atomic_activate_promoted_candidate,
    atomic_observe_evolution_signals,
    atomic_review_manual_evolution_evidence,
    atomic_decide_manual_canary,
)
from .campaign_events import (
    atomic_campaign_status,
    atomic_create_campaign,
    atomic_enqueue_campaign_work,
    atomic_pause_campaign,
    atomic_cancel_campaign,
)
from .continuous_project_events import atomic_continuous_project_step
from .external_learning_events import atomic_external_knowledge_scout
from .sprint19_events import (
    atomic_active_learning_effect_audit,
    atomic_project_iteration_audit,
    atomic_self_evolution_effect_audit,
    atomic_sprint19_acceptance,
)
from .project_hypothesis_events import atomic_project_hypothesis_propose
from .active_learning_events import (
    atomic_agent_active_learning_observe_episode,
    atomic_agent_active_learning_diagnostic_shadow,
    atomic_agent_active_learning_refine_taxonomy,
    atomic_agent_active_learning_skipped_terminal_repair_shadow,
    atomic_agent_active_learning_skipped_terminal_fresh_canary,
    atomic_agent_active_learning_handoff_intent_fresh_canary,
    atomic_agent_active_learning_preflight_manifest_fresh_canary,
    atomic_agent_active_learning_manual_failure_matched,
    atomic_agent_active_learning_feedback,
    atomic_agent_active_learning_select,
    atomic_agent_active_learning_propose_episode_repair,
    atomic_learning_observation_ingest,
    atomic_learning_topic_select,
    atomic_learning_candidate_propose,
    atomic_learning_policy_update,
    atomic_sprint18_learning_cycle,
    atomic_targetdiff_active_learning,
    atomic_targetdiff_active_robustness,
    atomic_targetdiff_uncertainty_diagnostic,
    atomic_targetdiff_uncertainty_candidate,
)
from .research_learning_events import (
    atomic_research_active_learning_observe,
    atomic_research_active_learning_select,
    atomic_research_active_learning_investigate,
    atomic_research_active_learning_matched,
    atomic_research_adoption_context_shadow,
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

        # ── File push to QQ ──
        ("push_files", "发送文件/图片到QQ。用于截图后推送图片文件给用户。参数: source(文件路径), caption(描述文本)", "local",
         atomic_push_files, {"external_call": True}),
        ("ensure_tool", "确保外部工具可用（检测/自动下载补缺）。参数: tool(plink/iqtree/bcftools/prokka等)", "local",
         atomic_ensure_tool, {"external_call": False, "produces_artifact": False}),
        ("read_image", "读取图片内容：用 workspace api.json 预设的 qwen 视觉模型（vision_model）描述图片。参数: path(图片路径), prompt(可选)。用于核查截图/图表内容。", "local",
         atomic_read_image, {}),

        # ── 迭代引擎 (5 events) ──
        ("understand_intent", "理解用户初始意图：目标/约束/成功标准/期望效果。参数: task(可选)", "local",
         atomic_understand_intent, {"external_call": True, "produces_artifact": True}),
        ("write_plan", "基于意图+可用能力写迭代计划文档。参数: round(可选)", "local",
         atomic_write_plan, {"external_call": True, "produces_artifact": True}),
        ("strict_reflect", "对本轮执行结果做严格反思批评（证据驱动，找根因与缺口）。参数: round(可选)", "local",
         atomic_strict_reflect, {"external_call": True, "produces_artifact": True}),
        ("next_iteration", "根据意图+上轮反思提出下一轮计划并启动执行（写 inbox 不等待）。参数: round(可选), max_iterations(可选)", "local",
         atomic_next_iteration, {"external_call": True, "produces_artifact": True}),

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
        ("xiaohongshu_open_publish_editor", "确定性打开小红书上传图文入口，核验真实上传控件并保存截图和JSON证据；不会上传或发布。参数: 无", "local",
         atomic_xhs_open_publish_editor, {"external_call": True, "produces_artifact": True}),
        ("xiaohongshu_inspect_upload_requirements", "读取小红书真实上传控件的accept/multiple及页面格式要求，输出JSON和MD；不会上传或发布。参数: 无", "local",
         atomic_xhs_inspect_upload_requirements, {"external_call": True, "produces_artifact": True}),
        ("multimodal_browser_observe", "在白名单社区打开可见浏览器，采集 DOM、截图、Qwen VL 描述并由 MiniMax 跨模态核对；不点击或发布。参数: platform, url(可选)", "llm",
         atomic_multimodal_browser_observe, {"external_call": True, "produces_artifact": True}),
        ("multimodal_community_read", "观察白名单社区，由 LLM 仅从真实 DOM 链接编号中选择一条并阅读；登录墙会置前等待用户，不输入凭据。参数: platform, url(可选)", "llm",
         atomic_multimodal_community_read, {"external_call": True, "produces_artifact": True}),
        ("multimodal_login_resume", "打开白名单平台登录页到前台，并以 DOM、截图、视觉模型和推理模型复核登录状态；只在确定性登录信号存在时恢复，不读取或填写凭据。参数: platform, url(可选)", "llm",
         atomic_multimodal_login_resume, {"external_call": True, "produces_artifact": True}),

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
        ("external_knowledge_scout", "外部知识主动学习：用 MiniMax 提出检索问题、选择新 GitHub/论文、拉取真实来源并形成原创可证伪想法。参数: topic(可选)", "local",
         atomic_external_knowledge_scout, {"external_call": True, "produces_artifact": True}),
        ("project_hypothesis_propose", "经验驱动策略学习：读取项目终态历史，由 LLM 在 Event 测量契约内提出可证伪的有界 Candidate；只形成候选，不执行任意代码。参数: project_id, project_steps", "llm",
         atomic_project_hypothesis_propose, {"external_call": True, "produces_artifact": False}),

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

        # ── Capability & Design (2 events) ──
        ("capability_inventory", "盘点自身能力（会什么/不会什么/需学什么），持续更新到共享 capabilities.md。参数: save_path(可选)", "local",
         atomic_capability_inventory, {"external_call": False, "produces_artifact": True}),
        ("write_design", "为当前任务生成软件项目式总设计文档，写入项目目录 design.md。参数: goal(可选), save_path(可选)", "llm",
         atomic_write_design, {"external_call": True, "produces_artifact": True}),
        # ── User Reporting (self-evolution additions) ──
        ("generate_pdf", "把内容转 PDF 方便用户阅读。参数: content 或 source_path, output_path(可选), title(可选)", "local",
         atomic_generate_pdf, {"external_call": False, "produces_artifact": True}),
        ("generate_detailed_pdf", "生成面向用户的详细 PDF 报告；正文至少1200字、至少4个章节，并包含可核查的证据/数据/结果。建议章节：摘要、目标与方法、执行证据、结果、限制风险、结论与下一步。参数: content 或 source_path, output_path(可选), title(可选)", "local",
         atomic_generate_detailed_pdf, {"external_call": False, "produces_artifact": True}),
        ("molecular_generation_benchmark", "本地运行可复现 RDKit 分子生成基准：生成50+候选，计算 validity/uniqueness/novelty/QED/MW/logP，输出 CSV/JSON/PNG/详细 PDF 并要求真实发送回执。参数: 无", "local",
         atomic_molecular_generation_benchmark, {"external_call": True, "produces_artifact": True}),
        ("molecular_diversity_benchmark", "读取上一轮分子CSV，真实计算 Bemis-Murcko 骨架多样性与 Morgan 指纹两两相似度，输出 JSON/PNG/MD/详细PDF。参数: source(可选)", "local",
         atomic_molecular_diversity_benchmark, {"external_call": True, "produces_artifact": True}),
        ("molecular_synth_baseline_benchmark", "读取上一轮分子CSV，计算SA合成可及性并与同样本量可复现随机生成基线比较。参数: source(可选)", "local",
         atomic_molecular_synth_baseline_benchmark, {"external_call": True, "produces_artifact": True}),
        ("molecular_goal_optimization_benchmark", "读取QED/SA对照CSV，执行多目标排序、骨架集中度检查并输出前20候选和详细PDF。参数: source(可选)", "local",
         atomic_molecular_goal_optimization_benchmark, {"external_call": True, "produces_artifact": True}),
        ("create_chart", "根据数据生成图表 PNG。参数: type(line/bar/scatter), data, output_path(可选), title(可选)", "local",
         atomic_create_chart, {"external_call": False, "produces_artifact": True}),

        ("auto_repair_plan", "plan 执行失败时基于已有产物重生成。参数: 无（自动扫 task dir）", "local",
         atomic_auto_repair_plan, {"external_call": False}),
        ("batch_plan_fallback", "batch_planner 超时时用本地 micro-plan 模板替代。参数: 无", "local",
         atomic_batch_plan_fallback, {"external_call": False}),
        ("handle_login_wall", "检测到登录墙时停止重试并记录状态。参数: 无", "local",
         atomic_handle_login_wall, {"external_call": False}),
        ("write_artifact_fallback", "atomic_write_artifact 失败时用 atomic_generate_pdf 重生成。参数: 无", "local",
         atomic_write_artifact_fallback, {"external_call": False}),
        ("open_login_on_confirm", "用户确认后在电脑前台打开并保持登录网页，同时通过真实用户通道发送登录提示；两者均有回执才成功。参数: url (可选, 默认小红书)", "browser",
         atomic_open_login_on_confirm, {"external_call": False}),
        ("open_browser_foreground_and_notify", "在电脑前台打开并保持网页，同时通过真实用户通道通知用户操作；浏览器打开和消息送达均有回执才成功。参数: url, message(可选)", "browser",
         atomic_open_browser_foreground_and_notify, {"external_call": True}),
        ("resume_after_login", "用户回「已登录」后恢复任务执行。参数: 无", "local",
         atomic_resume_after_login, {"external_call": False}),
        ("verify_login_and_continue", "用真实网页导航与 Cookie 证据验证登录；验证成功后真实通知用户并自动排队执行下一步，而不是只写计划。参数: url(可选), next_task(可选)", "local",
         atomic_verify_login_and_continue, {"external_call": True}),
        ("skip_login", "用户选择跳过登录。参数: 无", "local",
         atomic_skip_login, {"external_call": False}),
        ("send_user_text", "通过 QQ Bot API 真实发文本消息给用户。参数: text (必填)", "external",
         atomic_send_user_text, {"external_call": False}),

        # ── Governed context / project / evolution contracts ──
        ("select_context", "读取 L0 目录，用确定性规则+低成本 LLM 分类选择本步 L1/L2/L3 上下文，记录来源与预算。参数: query, project_id(可选), budget_chars(可选), document_ids(可选)", "local",
         atomic_select_context, {"external_call": True, "produces_artifact": True}),
        ("record_iteration", "为项目本轮写入可验证 IterationReceipt，包含 inputs/actions/artifacts/findings/next_actions 或 stop_reason。", "local",
         atomic_record_iteration, {"external_call": False, "produces_artifact": True}),
        ("request_next_action", "读取项目最新 Receipt 并返回 proposed NextAction；没有运行时 enqueue 回执时不得冒充 queued。参数: project_id, task_id(仅在已入队后提供)", "local",
         atomic_request_next_action, {"external_call": False}),
        ("record_issue", "把可复核问题写入 Issue Ledger 并去重累计。参数: summary, category, severity, evidence", "local",
         atomic_record_issue, {"external_call": False}),
        ("start_evolution_experiment", "为 Issue 建立 candidate 自进化实验。参数: issue_id, hypothesis, intervention, baseline, success_criteria, tests", "local",
         atomic_start_evolution_experiment, {"external_call": False, "produces_artifact": True}),
        ("decide_evolution_experiment", "根据显式成功标准、回归和前后证据决定 promoted/rejected/inconclusive；未过晋升门不得 promoted。", "local",
         atomic_decide_evolution_experiment, {"external_call": False}),
        ("activate_promoted_candidate", "仅在已有 policy/promoted Event 后，将可执行 Candidate 激活到 control_policy 并同步 production_effective 投影；学习型 Candidate 还必须提供通过三类生产硬门的 readiness_attestation_path。参数: candidate_id, decision_key, policy_event_id, readiness_attestation_path", "local",
         atomic_activate_promoted_candidate, {"external_call": False, "produces_artifact": True}),
        ("execute_candidate", "通过代码白名单执行已注册的 Event Candidate；参数: candidate_id, execution_id, instance_id, mode, event_params。", "local",
         atomic_execute_candidate, {"external_call": False, "produces_artifact": True}),
        ("observe_evolution_signals", "从事件失败、当前任务无产物、交付缺回执和连续重复事件中提取高信号 Issue；不根据模糊文字猜测问题。", "local",
         atomic_observe_evolution_signals, {"external_call": False}),
        ("review_manual_evolution_evidence", "审查01-04真实手动任务轨迹的样本数、结果新颖性、Receipt和来源异构性；过门只建立candidate实验，绝不自动晋升。参数: project_id(可选)", "local",
         atomic_review_manual_evolution_evidence, {"external_call": False, "produces_artifact": True}),
        ("decide_manual_canary", "仅在baseline/candidate各至少3样本且已有真实回归证明时汇总指定实验并写显式PromotionDecision。参数: experiment_id", "local",
         atomic_decide_manual_canary, {"external_call": False, "produces_artifact": True}),
        ("agent_active_learning_select", "读取真实 Episode 失败证据，用信息增益×任务价值×新颖性−成本−风险选择下一项诊断/repair/resample 实验；可用 focus_failure_class 续接既有诊断链；只提议，不执行或晋升。参数: instance_ids(默认03,05), focus_failure_class(可选)", "local",
         atomic_agent_active_learning_select, {"external_call": False, "produces_artifact": True}),
        ("agent_active_learning_observe_episode", "只读打开用户明确指定的 Episode 状态并返回失败类别与证据路径；不修改生产。参数: episode_id", "local",
         atomic_agent_active_learning_observe_episode, {"external_call": False}),
        ("agent_active_learning_feedback", "仅凭显式证据记录主动实验成败并更新选择记忆；不改生产策略。参数: context_key, option_id, success, evidence_refs", "local",
         atomic_agent_active_learning_feedback, {"external_call": False, "produces_artifact": True}),
        ("agent_active_learning_diagnostic_shadow", "读取选定 Episode 及原始 task log，诊断失败更像 systematic、transient 或 uncertain；只生成证据，不执行修复。", "local",
         atomic_agent_active_learning_diagnostic_shadow, {"external_call": False, "produces_artifact": True}),
        ("agent_active_learning_refine_taxonomy", "当一个失败标签含多个机制时，根据诊断证据生成版本化子类型；不改写历史 Episode 或生产策略。", "local",
         atomic_agent_active_learning_refine_taxonomy, {"external_call": False, "produces_artifact": True}),
        ("agent_active_learning_skipped_terminal_repair_shadow", "只读回放 systematic step 子类型诊断；仅在同一 trace 有 dependency-skipped 证据时评估补写终态的反事实效果，不改历史或生产。参数: diagnosis_path", "local",
         atomic_agent_active_learning_skipped_terminal_repair_shadow, {"external_call": False, "produces_artifact": True}),
        ("agent_active_learning_skipped_terminal_fresh_canary", "在隔离目录用真实 PlanExecutor 跑失败输入/跳过生成与交付/并行成功报告拓扑，验证每步唯一终态；不调用外部模型或生产实例。参数: canary_id", "local",
         atomic_agent_active_learning_skipped_terminal_fresh_canary, {"external_call": False, "produces_artifact": True}),
        ("agent_active_learning_handoff_intent_fresh_canary", "在隔离 workspace 验证独立任务可读取非前序输入、明确 continuation 漏交接时仍硬拒绝、正确交接可推进；不触碰生产实例。参数: canary_id", "local",
         atomic_agent_active_learning_handoff_intent_fresh_canary, {"external_call": False, "produces_artifact": True}),
        ("agent_active_learning_preflight_manifest_fresh_canary", "在隔离 workspace 验证 Candidate prompt 只注入已存在且白名单内的显式输入路径，baseline 不变、虚构路径仍拒绝。参数: canary_id", "local",
         atomic_agent_active_learning_preflight_manifest_fresh_canary, {"external_call": False, "produces_artifact": True}),
        ("agent_active_learning_manual_failure_matched", "在冻结失败 fixture 上匹配比较 literal baseline 与 claim-ledger/typed-output Candidate，记录决策和主动学习反馈；不修改生产。参数: experiment_id(可选)", "local",
         atomic_agent_active_learning_manual_failure_matched, {"external_call": False, "produces_artifact": True}),
        ("agent_active_learning_propose_episode_repair", "对用户明确指定的失败 Episode 生成受限 repair proposal 和只读审查 JSON；不改源码、control_policy 或 promotion。参数: episode_id", "local",
         atomic_agent_active_learning_propose_episode_repair, {"external_call": False, "produces_artifact": True}),
        ("learning_observation_ingest", "将真实轨迹投影为可审计 LearningObservation，分离 learning_eligible 与 promotion_eligible，保留负样本。", "local",
         atomic_learning_observation_ingest, {"external_call": False, "produces_artifact": True}),
        ("learning_topic_select", "用信息增益、不确定性、业务/迁移价值与成本/风险/重复惩罚主动选题，critic 拒绝无意义题目。", "local",
         atomic_learning_topic_select, {"external_call": False, "produces_artifact": True}),
        ("learning_candidate_propose", "从选题决策生成 R0-R4 受限 RepairRecipe Candidate；只建候选，不修改生产。参数: decision_path", "local",
         atomic_learning_candidate_propose, {"external_call": False, "produces_artifact": True}),
        ("learning_policy_update", "从正负 LearningObservation 更新 Beta 后验与最小探索概率；学习策略不自动投影生产。", "local",
         atomic_learning_policy_update, {"external_call": False, "produces_artifact": True}),
        ("sprint18_learning_cycle", "一次 Event-first 闭环：观测入库→主动选题→受限 Candidate→学习策略更新；不自动晋升生产。", "local",
         atomic_sprint18_learning_cycle, {"external_call": False, "produces_artifact": True}),
        ("targetdiff_active_learning", "在真实 TargetDiff pK 数据和官方 identity split 上，在相同标注预算下连续三轮比较 active/random/MaxVar 的下一样本选择；不是函数选择，不做药效因果主张。", "local",
         atomic_targetdiff_active_learning, {"external_call": False, "produces_artifact": True}),
        ("targetdiff_active_robustness", "汇总至少三个真实池随机种子的 equal-budget 主动学习结果；要求 Candidate 至少赢 2/3 且均值优于 random/MaxVar，否则正式 rejected。参数: run_ids, evaluation_id(可选)", "local",
         atomic_targetdiff_active_robustness, {"external_call": False, "produces_artifact": True}),
        ("targetdiff_uncertainty_diagnostic", "在真实 TargetDiff 官方 test 上，仅以后验评估方式检验 RF 方差与绝对误差的跨 seed 相关性、误差富集和 RMSD 分片可靠性；不选择样本、不测试 Candidate、不晋升。参数: seeds, labelled_budget, evaluation_id(可选)", "local",
         atomic_targetdiff_uncertainty_diagnostic, {"external_call": False, "produces_artifact": True}),
        ("targetdiff_uncertainty_candidate", "在冻结的真实 TargetDiff official test、三 seed 和相同标注预算下，matched 比较 raw RF variance 与仅由已标注样本训练的 cross-fitted residual uncertainty；test 标签只用于后验验收，不晋升。参数: seeds, labelled_budget, evaluation_id(可选)", "local",
         atomic_targetdiff_uncertainty_candidate, {"external_call": False, "produces_artifact": True}),
        ("research_active_learning_observe", "核验真实 GitHub 源码/论文来源并登记项目知识问题；只观察，不把阅读冒充改进。参数: project_id, goal, questions(列表), source_paths(至少2个真实路径)", "local",
         atomic_research_active_learning_observe, {"external_call": False, "produces_artifact": True}),
        ("research_active_learning_select", "根据预期信息增益、任务价值、新颖性与读取成本，选择下一条项目研究问题及真实来源；只选择不执行。参数: project_id", "local",
         atomic_research_active_learning_select, {"external_call": False, "produces_artifact": True}),
        ("research_active_learning_investigate", "读取主动选择的源码或论文，保存来源指纹、逐字证据与命中术语；不修改生产。参数: project_id", "local",
         atomic_research_active_learning_investigate, {"external_call": False, "produces_artifact": True}),
        ("research_active_learning_matched", "用相同真实问题和来源匹配比较 first-unread baseline 与信息价值 Candidate；最多接受进入 shadow，绝不自动 production。参数: project_id", "local",
         atomic_research_active_learning_matched, {"external_call": False, "produces_artifact": True}),
        ("research_adoption_context_shadow", "执行由多源源码/论文证据生成的最小上下文 Candidate：保留项目 Receipt、检索同项目轨迹并附来源证据；仅 shadow，不修改生产。参数: query, project_id, research_project_id, budget_chars(可选)", "local",
         atomic_research_adoption_context_shadow, {"external_call": False, "produces_artifact": True}),
        ("campaign_status", "读取持久 Campaign、WorkItem 和 Lease 状态；service/heartbeat 不冒充任务进度。", "local",
         atomic_campaign_status, {"external_call": False}),
        ("create_campaign", "创建有期限、双槽、重试/失败/模型/成本预算的长期 Campaign；只持久化，需 runner 才会 dispatch。", "local",
         atomic_create_campaign, {"external_call": False, "produces_artifact": True}),
        ("enqueue_campaign_work", "向现有 Campaign 添加有边界 WorkItem；真实发布/支付/凭证动作自动进入 human_required。", "local",
         atomic_enqueue_campaign_work, {"external_call": False, "produces_artifact": True}),
        ("pause_campaign", "持久暂停 Campaign，不删除 WorkItem/Receipt/Lease 证据。", "local",
         atomic_pause_campaign, {"external_call": False}),
        ("cancel_campaign", "取消 Campaign 并保留全部审计记录。参数: reason", "local",
         atomic_cancel_campaign, {"external_call": False}),
        ("continuous_project_step", "执行由 Receipt 与经验驱动策略 Candidate 选择的有界业务增量步骤。参数: strategy_id", "local",
         atomic_continuous_project_step, {"external_call": True, "produces_artifact": True}),
        ("project_iteration_audit", "核验项目是否真正承接前序 Receipt、改变动作并产生新产物。参数: project_id", "local",
         atomic_project_iteration_audit, {"external_call": False}),
        ("active_learning_effect_audit", "核验知识缺口是否触发学习、完成学习并实际改变下一动作。参数: instance_id(可选)", "local",
         atomic_active_learning_effect_audit, {"external_call": False}),
        ("self_evolution_effect_audit", "核验 Candidate、匹配决策、行为代码面和 production_effective，不把反思当自进化。", "local",
         atomic_self_evolution_effect_audit, {"external_call": False}),
        ("sprint19_acceptance", "同时给出项目迭代、主动学习、自进化三条独立证据链及未过硬门。参数: project_id/instance_id(可选)", "local",
         atomic_sprint19_acceptance, {"external_call": False, "produces_artifact": True}),

    ]
    return events
