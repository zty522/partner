"""Typed checkpoints for social/video flows.

The current trusted intake and browser implementation remains in
``partner.social_video``.  These adapters make each observable phase a
catalogued Event without weakening owner, account or publication checks.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from partner.event_fabric.catalog import EventDefinition


def _stage(name: str):
    def handler(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        failures = [r for r in (params.get('flow_outputs') or {}).values()
                    if isinstance(r, dict) and r.get('ok') is False]
        if failures:
            return {'ok':False, 'status':'failed', 'error':'前置阶段未完成，本阶段未执行。',
                    'requires_human':any(r.get('requires_human') for r in failures)}
        from partner.social_video.stages import trusted_request
        from partner.runtime.background_actions import BackgroundActions, TERMINAL
        try:
            request, directory = trusted_request(ctx, params)
        except (OSError, ValueError, KeyError, AttributeError) as exc:
            return {"ok": False, "status": "failed", "error": str(exc)}
        manager = BackgroundActions(ctx.workspace)
        identity = {key:str(getattr(ctx,key,'') or '') for key in
                    ('workspace','job_id','project_id','instance_id','intake_instance_id','channel','sender_id')}
        work = str(directory)
        key = 'social:' + str(params.get('event_id') or params.get('run_id') or ctx.job_id) + ':' + name
        task = manager.submit(key, identity, {**params, '_stage':name,
            '_background_operation':'social_stage', 'action_work':work},
            seconds=7500 if name=='video_transcribe' else 1800 if name.startswith('video_') else 300)
        if task['status'] not in TERMINAL:
            return {'ok':True,'status':'waiting','background_task_id':task['task_id'],
                    'summary':name+' 正在执行，等待后台回执'}
        result_path = manager.directory / task['task_id'] / 'result.json'
        if result_path.exists():
            return json.loads(result_path.read_text())
        return {'ok':False,'status':'failed','error':task.get('error') or task['status']}
    return handler


DEFINITIONS = [
    EventDefinition("social.xhs_account_observe", "project", "后台检查小红书账号和登录态", _stage("xhs_account_observe"), external_call=True),
    EventDefinition("social.xhs_history_read", "project", "读取本人历史笔记证据", _stage("xhs_history_read"), external_call=True),
    EventDefinition("social.xhs_style_infer", "project", "从历史来源推断写作风格", _stage("xhs_style_infer"), execution_method="llm"),
    EventDefinition("social.xhs_draft_compose", "project", "形成绑定来源的草稿", _stage("xhs_draft_compose"), execution_method="llm"),
    EventDefinition("social.xhs_media_prepare", "project", "准备并哈希绑定配图", _stage("xhs_media_prepare"), produces_artifact=True),
    EventDefinition("social.xhs_draft_verify", "project", "核对账号、正文、图片和授权范围", _stage("xhs_draft_verify")),
    EventDefinition("social.xhs_publish", "project", "依据当前草稿授权执行发布并保留页面回执", _stage("xhs_publish"), external_call=True, idempotent=False),
    EventDefinition("social.xhs_publish_verify", "project", "核验页面发布回执", _stage("xhs_publish_verify"), reads_existing_artifact=True),
    EventDefinition("social.video_open", "active_learning", "在后台 Edge 打开视频来源", _stage("video_open"), external_call=True),
    EventDefinition("social.video_capture", "active_learning", "采集真实播放时间点和截图", _stage("video_capture"), external_call=True, produces_artifact=True),
    EventDefinition("social.video_transcribe", "active_learning", "转录完整音轨", _stage("video_transcribe"), produces_artifact=True),
    EventDefinition("social.video_frames_describe", "active_learning", "使用视觉模型描述时间点画面", _stage("video_frames_describe"), execution_method="llm"),
    EventDefinition("social.video_timeline_align", "active_learning", "对齐音频、字幕和画面时间线", _stage("video_timeline_align")),
    EventDefinition("social.video_knowledge_synthesize", "active_learning", "综合视频知识并提出可追溯认识", _stage("video_knowledge_synthesize"), external_call=True, idempotent=False),
    EventDefinition("social.video_learning_verify", "active_learning", "核验完整视频学习证据", _stage("video_learning_verify"), reads_existing_artifact=True),
]
