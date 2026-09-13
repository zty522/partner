"""Trusted application intake and canonical Event adapters for social/video tasks."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import time
import uuid

from .events import Workflows, digest, locked, write_json
from .cli import Bridge, model


def root_of(workspace):
    root = Path(workspace).resolve()
    return root.parent.parent if root.parent.name == 'instances' else root


def data_root(workspace):
    return root_of(workspace) / 'state' / 'social_video'


def intake(workspace, text, *, channel, sender_id, instance, attachments=()):
    """Only actual frontend intake can create an approval; model parameters cannot."""
    if '[social_request=' in text:
        raise ValueError('内部请求标记不能作为用户输入')
    value = text.strip()
    start = re.match(r'^/?(?:小红书创作|小红书试发)\s+(.+)', value, re.S)
    video = re.match(r'^/?(?:视频学习|学习视频|完整学习视频)\s+(https://\S+)', value)
    if not video:
        link = re.search(r'(https://[^\s<>]+)', value)
        from urllib.parse import urlsplit
        host = urlsplit(link.group(1)).hostname if link else ''
        video_host = any(host == site or host.endswith('.' + site)
                         for site in ('iesdouyin.com', 'douyin.com', 'bilibili.com', 'b23.tv', 'youtube.com', 'youtu.be', 'vimeo.com')) if host else False
        if link and ((value == link.group(1) and video_host)
                     or ('视频' in value and any(x in value for x in ('学习', '看', '完整')))):
            video = link
    resume = re.fullmatch(r'/?(?:已登录|继续小红书|恢复视频)\s+([a-zA-Z0-9_-]+)', value)
    direct = re.fullmatch(r'/?直接发布\s+([a-zA-Z0-9_-]+)', value)
    approve = re.fullmatch(r'/?批准发布\s+([a-zA-Z0-9_-]+)\s+([a-f0-9]{64})', value)
    if not any((start, video, resume, approve, direct)):
        return None
    if instance != '01':
        raise ValueError('本次社交/视频验收请发送给 01 实例')
    base = data_root(workspace)
    owner = digest({'channel': channel, 'sender': sender_id, 'instance': instance})
    request_id = uuid.uuid4().hex
    if start or video:
        run_id = ('xhs-' if start else 'video-') + request_id[:12]
        run = base / 'runs' / run_id
        run.mkdir(parents=True)
        params = {'run_id': run_id}
        if start:
            params.update(topic=start.group(1).strip(), media=[str(a.get('path')) if isinstance(a, dict) else str(a)
                                                              for a in attachments])
        else:
            params['url'] = video.group(1).rstrip(')）]，。')
        event = 'xhs_authoring' if start else 'browser_video_learning'
        write_json(run / 'owner.json', {'owner': owner, 'event': event, 'params': params})
    else:
        run_id = (resume or approve or direct).group(1)
        run = base / 'runs' / run_id
        with locked(run):
            metadata = json.loads((run / 'owner.json').read_text())
            if metadata['owner'] != owner:
                raise ValueError('只能恢复或批准自己发起的任务')
            event, params = metadata['event'], metadata['params']
            if direct:
                if event != 'xhs_authoring':
                    raise ValueError('直接发布只适用于小红书创作任务')
                write_json(run / 'publish_authorization.json', {
                    'source': 'trusted_frontend', 'decision': 'direct_publish', 'owner': owner,
                    'run_id': run_id, 'params_hash': digest(params),
                    'scope': 'one_run', 'created_at': time.time()})
            if approve:
                state = json.loads((run / 'state.json').read_text())
                if (event != 'xhs_authoring' or state['status'] != 'awaiting_approval'
                        or state['draft_hash'] != approve.group(2) or digest(state['draft']) != approve.group(2)):
                    raise ValueError('草稿版本或审批状态不匹配')
                write_json(run / 'approval.json', {'decision': 'approve', 'source': 'trusted_frontend',
                    'owner': owner, 'draft_hash': state['draft_hash'], 'account_id': state['account_id'],
                    'expires_at': time.time() + 3600})
    requests = base / 'intake'
    requests.mkdir(parents=True, exist_ok=True)
    write_json(requests / (request_id + '.json'), {'event': event, 'params': params, 'owner': owner,
                                                'created_at': time.time()})
    return f'[social_request={request_id}]\n{value}'


def load_request(workspace, text):
    match = re.search(r'\[social_request=([a-f0-9]{32})\]', text)
    if not match:
        return None
    return json.loads((data_root(workspace) / 'intake' / (match.group(1) + '.json')).read_text())


def ensure_edge(workspace, purpose='xhs'):
    base = data_root(workspace)
    if purpose == 'video':
        base = base / 'video_browser'
    bridge = Bridge(base)
    if (base / 'worker/.workflow.lock').exists() and (base / 'heartbeat.json').exists():
        return bridge  # Long media actions keep the worker lease while its event loop is busy.
    try:
        heartbeat = json.loads((base / 'heartbeat.json').read_text())
        if time.time() - heartbeat['time'] < 5:
            return bridge
    except (OSError, ValueError):
        pass
    repo = Path(__file__).resolve().parents[2]
    def win(path):
        return subprocess.check_output(['wslpath', '-w', str(path)], text=True).strip()
    base.mkdir(parents=True, exist_ok=True)
    # A dedicated headless, muted worker; never acquire the production shared Edge profile.
    launcher = base / 'launch_edge.py'
    launcher.write_text('import sys\nsys.path.insert(0, ' + repr(win(repo)) + ')\n'
                        'from partner.social_video.edge_worker import main\n'
                        "sys.argv = ['edge_worker', '--root', " + repr(win(base)) + ']\nmain()\n', encoding='utf-8')
    with (base / 'edge_worker.log').open('ab') as output:
        existing = root_of(workspace) / 'runtime/windows_browser_env/Scripts/python.exe'
        python_exe = str(existing) if existing.exists() else '/mnt/c/Python314/python.exe'
        subprocess.Popen([python_exe, win(launcher)], stdout=output, stderr=output,
                         start_new_session=True)
    for _ in range(60):
        time.sleep(0.5)
        try:
            heartbeat = json.loads((base / 'heartbeat.json').read_text())
            if time.time() - heartbeat['time'] < 5:
                return bridge
        except (OSError, ValueError):
            pass
    raise RuntimeError('独立 Edge 启动失败，查看 state/social_video/edge_worker.log')


def publication_authority(run, owner, fingerprint, account):
    """A specific approval or the user's explicit one-run direct-publish authorization."""
    run = Path(run)
    direct = run / 'publish_authorization.json'
    if direct.exists():
        value = json.loads(direct.read_text())
        metadata = json.loads((run / 'owner.json').read_text())
        if (value.get('source') == 'trusted_frontend' and value.get('decision') == 'direct_publish'
                and value.get('owner') == owner == metadata.get('owner')
                and value.get('run_id') == run.name and value.get('scope') == 'one_run'
                and metadata.get('event') == 'xhs_authoring'
                and value.get('params_hash') == digest(metadata['params'])):
            bound = {'decision': 'approve', 'source': 'user_direct_publish', 'owner': owner,
                     'draft_hash': fingerprint, 'account_id': account, 'scope': 'one_run'}
            write_json(run / 'publication_binding.json', bound)
            return bound
    path = run / 'approval.json'
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    return value if (value.get('source') == 'trusted_frontend' and value.get('owner') == owner
                     and value.get('draft_hash') == fingerprint and value.get('account_id') == account
                     and value.get('expires_at', 0) > time.time()) else None


def execute(ctx, params, expected_event):
    rid = str(params.get('request_id', ''))
    if not re.fullmatch('[a-f0-9]{32}', rid):
        return {'ok': False, 'error': '必须由可信 QQ/应用请求发起，不能直接传入审批参数'}
    base = data_root(ctx.workspace)
    request = json.loads((base / 'intake' / (rid + '.json')).read_text())
    if request['event'] != expected_event:
        return {'ok': False, 'error': 'event mismatch'}
    source = str(getattr(ctx, 'channel', 'local'))
    sender = str(getattr(ctx, 'sender_id', ''))
    origin = str(getattr(ctx, 'intake_instance_id', '') or getattr(ctx, 'instance_id', ''))
    owner = digest({'channel': source, 'sender': sender, 'instance': origin})
    if request['owner'] != owner:
        return {'ok': False, 'error': '任务与当前消息用户不匹配'}
    def notify(text):
        from partner.events.delivery import send_text
        return send_text(ctx, {'text': text, 'channel': source, 'sender_id': sender,
                               'origin_instance': origin})
    def approval(run_id, fingerprint, account):
        return publication_authority(base / 'runs' / run_id, owner, fingerprint, account)
    def bridge(action, arguments):
        purpose = 'video' if action.startswith('video_') else 'xhs'
        return ensure_edge(ctx.workspace, purpose=purpose)(action, arguments)
    workflow = Workflows(base / 'runs', bridge, model(ctx.workspace), notify, approval)
    result = workflow.run(expected_event, request['params'])
    report = Path(ctx.working_dir) / 'social_workflow.md'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('# 社交与视频任务回执\n\n状态：' + result['status']
                      + '\n\n' + str(result.get('error') or '详细证据与恢复状态见任务文件。')
                      + '\n\n' + str(result.get('state_path', '')), encoding='utf-8')
    result['files'] = [result['state_path']] if result.get('state_path') else []
    result['files'].append(str(report))
    run_dir = base / 'runs' / request['params']['run_id']
    result['files'] += [str(p) for p in (run_dir / 'notes.md', run_dir / 'draft.json',
                                       run_dir / 'full_video/transcript.srt') if p.exists()]
    result['summary'] = '社交/视频任务：' + result['status']
    outcomes = base / 'outcomes'
    outcomes.mkdir(parents=True, exist_ok=True)
    write_json(outcomes / (rid + '.json'), result)
    if not result.get('ok'):
        notify('本次任务未完成：' + result.get('error', result['status']))
    return result


def job_outcome(workspace, text):
    match = re.search(r'\[social_request=([a-f0-9]{32})\]', text)
    if not match:
        return None
    path = data_root(workspace) / 'outcomes' / (match.group(1) + '.json')
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    status = value['status']
    if status == 'learned':
        state = json.loads(Path(value['state_path']).read_text())
        if not state.get('report_complete'):
            return {'status': 'failed', 'error': '综合报告尚未通过完整性验收'}
    return {'status': ('completed' if status in {'learned', 'published'} else
                        'waiting_user' if status in {'awaiting_user_login', 'awaiting_approval', 'awaiting_media',
                                                   'login_required', 'publication_unknown', 'site_restricted'} else 'failed'),
            'error': '' if value.get('ok') else value.get('error', status)}


def atomic_xhs_authoring(ctx, params):
    return execute(ctx, params, 'xhs_authoring')


def atomic_browser_video_learning(ctx, params):
    return execute(ctx, params, 'browser_video_learning')
