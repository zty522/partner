"""Resumable workflows; browser, model, notification and approval are trusted ports."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    # Windows may briefly deny replacement while WSL reads an IPC heartbeat.
    for attempt in range(50):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.05)


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True)
    lock = root / '.workflow.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError('workflow busy; stale locks require manual inspection')
    try:
        yield
    finally:
        lock.rmdir()


def valid_url(url, xhs=False):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password:
        raise ValueError('An HTTPS web URL without credentials is required')
    if xhs and p.hostname not in {'www.xiaohongshu.com', 'xiaohongshu.com'}:
        raise ValueError('Expected a xiaohongshu.com URL')
    return url


class Workflows:
    def __init__(self, root, browser, model, notify, approval):
        self.root = Path(root)
        self.browser, self.model = browser, model
        self.notify, self.approval = notify, approval

    def _notify(self, text):
        receipt = self.notify(text)
        if not isinstance(receipt, dict) or not (receipt.get('delivered') or receipt.get('queued')):
            raise RuntimeError('notification was not accepted by a channel transport')
        return receipt

    def run(self, event, params):
        # All handlers share one browser page. Hold the lease over observe -> act -> verify.
        with locked(self.root / '.browser_lease'):
            return self._run(event, params)

    def _run(self, event, params):
        if event not in {'xhs_authoring', 'browser_video_learning'}:
            raise ValueError('Unknown event')
        run_id = params.get('run_id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
            raise ValueError('run_id must contain 1–80 letters, digits, underscores or hyphens')
        directory = self.root / run_id
        with locked(directory):
            state_file = directory / 'state.json'
            state = json.loads(state_file.read_text()) if state_file.exists() else {'event': event}
            if state['event'] != event:
                raise ValueError('run_id belongs to another event')
            def save(status, **fields):
                state.update(status=status, **fields)
                write_json(state_file, state)
                with (directory / 'transitions.jsonl').open('a', encoding='utf-8') as f:
                    f.write(json.dumps({'ts': time.time(), 'status': status,
                                        'state_hash': digest(state)}) + '\n')
                return {'ok': status not in {'failed', 'publication_unknown'},
                        'status': status, 'state_path': str(state_file),
                        'summary': {'title': event, 'conclusion': status,
                                    'production_effective': False}}
            try:
                if (state.get('status') in {'published', 'publishing', 'publication_unknown'}
                        or (state.get('status') == 'learned' and
                            (not params.get('full_audio', True) or state.get('report_complete')))):
                    return save('publication_unknown' if state['status'] == 'publishing' else state['status'])
                self._notify('开始：' + ('检查账号、阅读历史内容并准备草稿。' if event == 'xhs_authoring'
                                      else '获取完整视频、转录音轨并分析各时段画面。'))
                if event == 'xhs_authoring':
                    return self._xhs(params, state, directory, save)
                return self._video(params, state, directory, save)
            except Exception as exc:
                # Persist uncertainty BEFORE any notification/retry. Never retry a publish click.
                status = ('publication_unknown' if state.get('status') == 'publishing' else
                          'site_restricted' if 'SITE_RESTRICTED:' in str(exc) else 'failed')
                result = save(status, error_type=type(exc).__name__)
                result['error'] = str(exc)
                if status == 'site_restricted':
                    ack = self._notify(str(exc).split('SITE_RESTRICTED:', 1)[-1].strip())
                    result = save(status, notification=ack, error=str(exc))
                return result

    def _xhs(self, p, s, directory, save):
        topic = s.get('topic') or str(p.get('topic', '')).strip()
        if not topic:
            raise ValueError('topic is required')
        s['topic'] = topic
        observation = self.browser('xhs_account', {})
        if not observation.get('authenticated'):
            save('awaiting_user_login', login_image=observation.get('login_image'))
            ack = self._notify('需要小红书登录。浏览器保持后台静音，不弹出窗口；'
                               '请使用发给你的登录二维码完成登录，再回复恢复命令。')
            return save('awaiting_user_login', notification=ack)
        account = observation['account_id']
        if s.get('account_id') and s['account_id'] != account:
            raise RuntimeError('Account changed; start a new run and approve its new draft')
        s['account_id'] = account
        if 'draft' not in s:
            self._notify('登录已通过页面账号证据核验，开始阅读该账号已发布的笔记。')
            posts = self.browser('xhs_posts', {'account_id': account, 'limit': 6})['posts']
            if len(posts) < 2:
                raise RuntimeError('Need at least two readable posts from this account to infer style')
            write_json(directory / 'sources.json', posts)
            draft = self.model('xhs_draft', {'topic': topic, 'posts': posts})
            for key in ('title', 'body', 'style'):
                if not isinstance(draft.get(key), str) or not draft[key].strip():
                    raise ValueError('Draft model omitted ' + key)
            if len(draft['title']) > 20 or len(draft['body']) > 1000:
                raise ValueError('Draft exceeds configured editor limits (title 20/body 1000)')
            media = []
            for item in p.get('media', []):
                path = Path(item).resolve(strict=True)
                media.append({'path': str(path), 'sha256': sha256(path.read_bytes()).hexdigest()})
            if not media:
                from .cover import create_cover
                path = create_cover(draft['title'], directory)
                media.append({'path': str(path), 'sha256': sha256(path.read_bytes()).hexdigest()})
            s['draft'] = {k: draft[k] for k in ('title', 'body', 'style')}
            s['draft'].update(account_id=account, media=media,
                              sources=[post['url'] for post in posts])
            s['draft_hash'] = digest(s['draft'])
            write_json(directory / 'draft.json', s['draft'])
        draft = s['draft']
        if digest(draft) != s['draft_hash']:
            raise RuntimeError('Draft integrity check failed')
        for asset in draft['media']:
            if sha256(Path(asset['path']).read_bytes()).hexdigest() != asset['sha256']:
                raise RuntimeError('Media changed after draft creation; new review required')
        approval = self.approval(directory.name, s['draft_hash'], account)
        if not approval:
            save('awaiting_approval')
            ack = self._notify('请审批以下完整草稿（批准后才能发布）：\n'
                               + json.dumps(draft, ensure_ascii=False, indent=2)
                               + '\n草稿指纹：' + s['draft_hash'])
            return save('awaiting_approval', notification=ack)
        if not draft['media']:
            self._notify('草稿已审批，但当前发布适配器需要配图；请新建 run_id 并提供 media 后重新审稿。')
            return save('awaiting_media')
        self._notify('按你对本次试发的直接发布授权，开始填写并核对正文、配图和账号。'
                     if approval.get('source') == 'user_direct_publish' else
                     '已收到绑定当前账号和草稿指纹的审批，开始填写并核对发布编辑器。')
        staged = self.browser('xhs_prepare', {'draft': draft})
        if not staged.get('verified'):
            raise RuntimeError('Editor/account/media verification failed')
        save('publishing', approval=approval, staged=staged)
        receipt = self.browser('xhs_publish', {'draft_hash': s['draft_hash']})
        if receipt.get('published') is not True or not receipt.get('evidence'):
            return save('publication_unknown', publication_receipt=receipt)
        result = save('published', publication_receipt=receipt)
        # A delivery failure after verified publication must not erase the publication state.
        try:
            ack = self._notify('页面已确认发布成功。')
            result = save('published', publication_notification=ack)
        except Exception:
            result['notification_status'] = 'failed'
        return result

    def _video(self, p, s, directory, save):
        url = valid_url(str(p.get('url') or s.get('url') or ''))
        if s.get('url') and s['url'] != url:
            raise ValueError('URL changed; use a new run_id')
        s['url'] = url
        if p.get('full_audio', True):
            from .full_video import learn
            evidence = learn(url, directory, self.browser, self.model, self._notify)
            return save('learned', evidence=str(directory / 'video_evidence.json'),
                        notes=str(directory / 'notes.md'), learning_scope=evidence['scope'], report_complete=True)
        sample_count = min(12, max(3, int(p.get('samples', 6))))
        capture = self.browser('video_capture', {'url': url, 'samples': sample_count,
                                                'run_id': directory.name})
        write_json(directory / 'capture.json', capture)
        if capture.get('status') != 'captured':
            status = capture.get('status', 'failed')
            if status not in {'login_required', 'video_unavailable', 'unsupported_live', 'failed'}:
                status = 'failed'
            self._notify('视频采集未完成：' + status + '。可关闭的登录提示已尝试关闭；需要登录才能播放的内容须人工登录。')
            return save(status)
        frames = capture.get('frames', [])
        if len(frames) < 3 or not capture.get('playback_advanced'):
            raise RuntimeError('No verified playback / insufficient video frames')
        self._notify(f'已验证播放，并采集 {len(frames)} 个时间点；开始识别实际视频画面。')
        observations = []
        for frame in frames:
            description = self.model('video_frame', frame)
            if not isinstance(description, str) or not description.strip():
                raise RuntimeError('Vision model returned no frame content')
            observations.append({'time': frame['time'], 'description': description,
                                 'path': frame['path']})
        evidence = {**capture, 'observations': observations}
        write_json(directory / 'video_evidence.json', evidence)
        notes = self.model('video_notes', evidence)
        if not isinstance(notes, str) or not notes.strip():
            raise RuntimeError('No learning notes produced')
        report = ('# 视频学习笔记\n\n来源：' + url
                  + '\n\n范围：离散画面抽样与播放器提供的字幕；未转录音轨，不能声称完整观看或理解所有口播。'
                  + '\n\n' + notes)
        (directory / 'notes.md').write_text(report, encoding='utf-8')
        self._notify(report)
        return save('learned', evidence=str(directory / 'video_evidence.json'),
                    notes=str(directory / 'notes.md'), learning_scope='sampled_frames_and_available_captions')
