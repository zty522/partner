"""Social/video owner + dispatch helpers; no regex routing.

Service layer calls INTENT_FLOW to decide dispatch_target. The flow's
intent_synthesize event outputs ``run_id / url / topic / media / owner``
in the payload, and the Event worker threads those fields through
``params`` for every downstream stage. We do not write or read any
``intake/*.json`` file.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

from .events import Workflows, digest, locked, write_json
from .cli import Bridge, model


def intake(workspace, text, *, channel, sender_id, instance, attachments=()):
    """Deprecated stub.  Removed in partner v5; route decisions live in
    INTENT_FLOW now.  Raise a clear error so callers fail fast."""
    raise NotImplementedError(
        "social_video.integration.intake() was removed. "
        "Service.submit() now uses INTENT_FLOW (intent_observe / counter_read / "
        "synthesize) for all dispatch decisions; there is no regex short-circuit."
    )


def load_request(workspace, text):
    """Deprecated stub.  Raises the same clear error as intake()."""
    raise NotImplementedError(
        "social_video.integration.load_request() was removed; "
        "dispatch info now lives in params['run_id'/'event_type']."
    )


def root_of(workspace):
    root = Path(workspace).resolve()
    return root.parent.parent if root.parent.name == 'instances' else root


def data_root(workspace):
    return root_of(workspace) / 'state' / 'social_video'


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


def _owner_for(ctx):
    """Reconstruct the run owner from the current message context."""
    source = str(getattr(ctx, 'channel', 'local'))
    sender = str(getattr(ctx, 'sender_id', ''))
    origin = str(getattr(ctx, 'intake_instance_id', '') or getattr(ctx, 'instance_id', ''))
    return digest({'channel': source, 'sender': sender, 'instance': origin})


def execute(ctx, params, expected_event):
    """Run the social/video workflow. ``params`` already carries run_id/url/topic/media/owner
    that INTENT_FLOW wrote; we only verify the run directory matches the expected event type."""
    run_id = str(params.get('run_id') or '')
    if not run_id:
        return {'ok': False, 'error': 'INTENT_FLOW did not supply run_id in payload'}
    owner = _owner_for(ctx)
    base = data_root(ctx.workspace)
    run_dir = base / 'runs' / run_id
    if not run_dir.exists():
        return {'ok': False, 'error': f'run directory missing: {run_dir}'}
    metadata_path = run_dir / 'owner.json'
    if not metadata_path.exists():
        return {'ok': False, 'error': f'owner.json missing: {metadata_path}'}
    metadata = json.loads(metadata_path.read_text())
    if metadata.get('event') != expected_event:
        return {'ok': False, 'error': f'event mismatch: expected {expected_event}, got {metadata.get("event")}'}
    if metadata.get('owner') != owner:
        return {'ok': False, 'error': '任务与当前消息用户不匹配'}
    source = str(getattr(ctx, 'channel', 'local'))
    sender = str(getattr(ctx, 'sender_id', ''))
    origin = str(getattr(ctx, 'intake_instance_id', '') or getattr(ctx, 'instance_id', ''))
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
    result = workflow.run(expected_event, metadata['params'])
    report = Path(ctx.working_dir) / 'social_workflow.md'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('# 社交与视频任务回执\n\n状态：' + result['status']
                      + '\n\n' + str(result.get('error') or '详细证据与恢复状态见任务文件。')
                      + '\n\n' + str(result.get('state_path', '')), encoding='utf-8')
    result['files'] = [result['state_path']] if result.get('state_path') else []
    result['files'].append(str(report))
    result['files'] += [str(p) for p in (run_dir / 'notes.md', run_dir / 'draft.json',
                                       run_dir / 'full_video/transcript.srt') if p.exists()]
    result['summary'] = '社交/视频任务：' + result['status']
    if not result.get('ok'):
        notify('本次任务未完成：' + result.get('error', result['status']))
    return result


def atomic_xhs_authoring(ctx, params):
    return execute(ctx, params, 'xhs_authoring')


def atomic_browser_video_learning(ctx, params):
    return execute(ctx, params, 'browser_video_learning')
