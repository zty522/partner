"""Dedicated Windows Edge worker: headless, muted, never activates desktop windows."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
from urllib.parse import urlsplit

from .events import digest, valid_url, write_json, locked


def windows_path(value):
    value = str(value)
    if value.startswith('/mnt/') and len(value) > 7 and value[6] == '/':
        return value[5].upper() + ':/' + value[7:]
    return value


class Browser:
    def __init__(self, page, root):
        self.page, self.root, self.prepared = page, Path(root), None
        self.detail = None
        def capture(response):
            if '/aweme/v1/web/aweme/detail/' in response.url:
                try:
                    self.detail = response.json()
                except Exception:
                    pass
        page.on('response', capture)

    def go(self, url):
        try:
            self.page.goto(valid_url(url), wait_until='domcontentloaded', timeout=30000)
        except Exception:
            self.page.wait_for_timeout(1000)
            if 'IP存在风险' in self.page.locator('body').inner_text():
                raise RuntimeError('SITE_RESTRICTED: 小红书拒绝当前浏览器访问（300012）；原因未确定，需要排查会话、浏览器环境及网络路由') from None
            raise
        self.page.wait_for_timeout(2000)
        if 'IP存在风险' in self.page.locator('body').inner_text():
            raise RuntimeError('SITE_RESTRICTED: 小红书拒绝当前浏览器访问（300012）；原因未确定，需要排查会话、浏览器环境及网络路由')
        valid_url(self.page.url)

    def login_wall(self):
        return self.page.locator('[class*=login-container]:visible, [class*=login-modal]:visible').count() > 0

    def account(self):
        self.go('https://www.xiaohongshu.com/explore')
        # Require the signed-in "Me" navigation, never any author's profile link.
        own = [a for a in self.page.locator('a[href*="/user/profile/"]').all()
               if a.is_visible() and a.inner_text().strip() == '我']
        if self.login_wall() or len(own) != 1:
            image = self.root / 'login' / f'xiaohongshu-{int(time.time())}.png'
            image.parent.mkdir(parents=True, exist_ok=True)
            dialog = self.page.locator('[class*=login-container]:visible, [class*=login-modal]:visible')
            if dialog.count():
                dialog.first.screenshot(path=str(image))
            else:
                self.page.screenshot(path=str(image))
            # Only send this to the requesting user. Never submit login screenshots to vision models.
            return {'authenticated': False, 'login_image': str(image), 'background_only': True, 'audio_muted': True}
        url = own[0].get_attribute('href')
        account_id = urlsplit(url).path.rstrip('/').split('/')[-1]
        if not account_id.isalnum():
            raise ValueError('Invalid account identity')
        return {'authenticated': True, 'account_id': account_id}

    def posts(self, args):
        account = args['account_id']
        self.go('https://www.xiaohongshu.com/user/profile/' + account)
        # Deliberately stay in the user's published-notes section; likes/collections are excluded.
        self.page.get_by_text('笔记', exact=True).first.click(timeout=5000)
        links = []
        for _ in range(3):
            for link in self.page.locator('section.note-item a.cover').evaluate_all('(xs)=>xs.map(x=>x.href)'):
                if link not in links:
                    links.append(link)
            if len(links) >= args['limit']:
                break
            self.page.mouse.wheel(0, 600)
            self.page.wait_for_timeout(600)
        posts = []
        for link in links[:args['limit']]:
            valid_url(link, xhs=True)
            self.go(link)
            if self.login_wall():
                break
            author = self.page.locator('.author-container a[href*="/user/profile/"]').first
            if not author.count():
                continue
            author_id = urlsplit(author.get_attribute('href')).path.rstrip('/').split('/')[-1]
            if author_id != account:
                continue
            title = self.page.locator('#detail-title')
            body = self.page.locator('#detail-desc')
            if body.count() and body.inner_text().strip():
                posts.append({'url': self.page.url, 'account_id': account,
                              'title': title.inner_text() if title.count() else '',
                              'body': body.inner_text()[:8000]})
        return {'posts': posts}

    def prepare(self, args):
        draft = args['draft']
        self.prepared = None
        if self.account().get('account_id') != draft['account_id']:
            raise ValueError('Account mismatch')
        self.go('https://creator.xiaohongshu.com/publish/publish?source=official')
        # Known image-note editor. Site changes fail closed; no generic click guessing.
        self.page.get_by_text('上传图文', exact=True).first.click(timeout=5000)
        assets = [windows_path(item['path']) for item in draft['media']]
        from hashlib import sha256
        if any(sha256(Path(path).read_bytes()).hexdigest() != item['sha256']
               for path, item in zip(assets, draft['media'])):
            raise ValueError('Media content changed')
        self.page.locator('input[type=file]').first.set_input_files(assets)
        title = self.page.locator('input[placeholder*="标题"]')
        body = self.page.locator('[contenteditable=true]')
        if title.count() != 1 or body.count() != 1:
            raise ValueError('Ambiguous editor controls')
        title.fill(draft['title'])
        body.fill(draft['body'])
        # Uploaded previews must match count. Unknown editor layouts require adaptation.
        self.page.wait_for_timeout(2500)
        previews = self.page.locator('.img-preview')
        button = self.page.get_by_role('button', name='发布', exact=True)
        verified = (title.input_value() == draft['title']
                    and body.inner_text().strip() == draft['body'].strip()
                    and previews.count() == len(assets)
                    and button.count() == 1 and button.is_enabled())
        if verified:
            self.prepared = {'hash': digest(draft), 'title': draft['title'], 'body': draft['body'],
                             'media_count': len(assets), 'url': self.page.url}
        return {'verified': verified, 'media_count': previews.count(), 'url': self.page.url}

    def publish(self, args):
        staged, self.prepared = self.prepared, None
        if not staged or args['draft_hash'] != staged['hash'] or self.page.url != staged['url']:
            raise ValueError('No matching prepared editor')
        if (self.page.locator('input[placeholder*="标题"]').input_value() != staged['title']
                or self.page.locator('[contenteditable=true]').inner_text().strip() != staged['body'].strip()
                or self.page.locator('.img-preview').count() != staged['media_count']):
            raise ValueError('Editor changed after verification')
        success = self.page.get_by_text('发布成功', exact=True)
        if success.count() and success.first.is_visible():
            raise ValueError('Preexisting success signal')
        self.page.get_by_role('button', name='发布', exact=True).click(timeout=5000)
        success.first.wait_for(state='visible', timeout=15000)
        return {'published': True, 'evidence': {'type': 'new_visible_publish_success',
                                               'url': self.page.url, 'time': time.time()}}

    def dismiss_login(self):
        # Only close a visible login dialog through its own close control.
        dialogs = self.page.locator('[role=dialog]:visible, [class*=login-modal]:visible, [class*=login-container]:visible, .dy-account-login:visible, .douyin_login_new_class:visible')
        dismissed = False
        for index in range(min(dialogs.count(), 5)):
            dialog = dialogs.nth(index)
            if not dialog.count() or not dialog.is_visible():
                continue
            if not any(t in dialog.inner_text().lower() for t in ('登录', 'sign in', 'log in')):
                continue
            close = dialog.locator('button[aria-label="关闭"], button[aria-label="Close"], [class~="close"], [class~="login-close"], .dy-account-close')
            if close.count() == 0:
                # Actual Douyin close-X glyph, verified in the login modal on 2026-09-11.
                close = dialog.locator('svg[viewBox="0 0 37 36"]:has(path[d^="M12.7929 22.2426"])')
            if close.count() == 1 and close.is_visible():
                close.click(timeout=2000)
                dialog.wait_for(state='hidden', timeout=3000)
                dismissed = True
        return dismissed

    def video(self, args):
        url = args['url']
        import re
        match = re.search(r'iesdouyin\.com/share/video/(\d+)', url)
        if match:
            url = 'https://www.douyin.com/video/' + match.group(1)
        self.go(url)
        dismissed = self.dismiss_login()
        video = None
        best_duration = 0
        for _ in range(12):
            for frame in self.page.frames:
                try:
                    for candidate in frame.locator('video').all():
                        duration = candidate.evaluate('(v)=>Number.isFinite(v.duration)?v.duration:0')
                        if candidate.is_visible() and duration > best_duration:
                            video = candidate
                            best_duration = duration
                except Exception:
                    if not frame.is_detached():
                        raise
            if video is not None:
                break
            self.page.wait_for_timeout(500)
            dismissed = self.dismiss_login() or dismissed
        if video is None:
            return {'status': 'login_required' if self.login_wall() else 'video_unavailable'}
        video.evaluate('(v)=>{v.muted=true; return v.play()}')
        start = video.evaluate('(v)=>v.currentTime')
        for _ in range(10):
            self.page.wait_for_timeout(1000)
            info = video.evaluate('(v)=>({duration:v.duration,time:v.currentTime,width:v.videoWidth,ready:v.readyState})')
            if info['time'] > start and info['ready'] >= 2:
                break
        dismissed = self.dismiss_login() or dismissed
        if info['time'] <= start or info['width'] <= 0 or info['ready'] < 2:
            return {'status': 'video_unavailable', 'playback_advanced': False}
        duration = info['duration']
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            return {'status': 'unsupported_live'}
        captions = video.evaluate('''(v)=>Array.from(v.textTracks).map(t=>({
            language:t.language,kind:t.kind,cues:Array.from(t.cues||[]).map(c=>({start:c.startTime,end:c.endTime,text:c.text}))}))''')
        destination = self.root / 'captures' / args['run_id']
        destination.mkdir(parents=True, exist_ok=True)
        frames = []
        for index in range(args['samples']):
            target = duration * (index + 0.5) / args['samples']
            actual = video.evaluate('''(v,t)=>new Promise((resolve,reject)=>{
                v.pause(); const timer=setTimeout(()=>{v.removeEventListener('seeked',done);reject(new Error('seek timeout'))},8000);
                function done(){clearTimeout(timer);resolve(v.currentTime)}
                v.addEventListener('seeked',done,{once:true});v.currentTime=t;
            })''', target)
            if abs(actual - target) > 1.5:
                raise ValueError('Video seek did not reach requested timestamp')
            self.page.wait_for_timeout(150)
            path = destination / f'frame-{index:02d}.png'
            video.screenshot(path=str(path))
            frames.append({'time': actual, 'path': str(path)})
        return {'status': 'captured', 'url': self.page.url, 'duration': duration,
                'playback_advanced': True, 'dismissed_login_prompt': dismissed,
                'frames': frames, 'captions': captions, 'audio_transcribed': False}

    def dispatch(self, action, args):
        handlers = {'xhs_account': lambda _: self.account(), 'xhs_posts': self.posts,
                    'xhs_prepare': self.prepare, 'xhs_publish': self.publish,
                    'video_capture': self.video, 'video_download': self.download,
                    'inspect': lambda _: {'url': self.page.url, 'title': self.page.title(),
                        'text': self.page.locator('body').inner_text()[:7000],
                        'login_controls': self.page.locator('[class*=login], [class*=close]').evaluate_all('''(xs)=>xs.filter(x=>x.getClientRects().length).slice(0,60).map(x=>({tag:x.tagName,cls:String(x.className),label:x.getAttribute('aria-label'),text:x.children.length?null:x.textContent.slice(0,40)}))'''),
                        'login_svg': self.page.locator('.douyin_login_new_class svg').evaluate_all('''xs=>xs.map(x=>({viewBox:x.getAttribute('viewBox'),parent:x.parentElement.className}))'''),
                        'videos': self.page.locator('video').evaluate_all('(vs)=>vs.map(v=>({duration:v.duration,visible:!!v.getClientRects().length}))')},
                    'stop': lambda _: {'stopping': True}}
        result = handlers[action](args)
        result.update(background_only=True, browser_visible=False, audio_muted=True)
        return result

    def download(self, args):
        # Download only media exposed by a genuinely playable page in this browser session.
        observed = self.video({**args, 'samples': 3})
        if observed.get('status') != 'captured':
            return observed
        import re
        target = re.search(r'/video/(\d+)', self.page.url)
        candidates = []
        if target:
            states = self.page.evaluate('''()=>{
                const result=[];
                if(window._ROUTER_DATA)result.push(window._ROUTER_DATA);
                const node=document.getElementById('RENDER_DATA');
                if(node){try{result.push(JSON.parse(decodeURIComponent(node.textContent)))}catch(e){}}
                return result;
            }''')
            states.append(self.detail)
            def matching(node):
                if isinstance(node, dict):
                    if str(node.get('aweme_id') or node.get('awemeId') or '') == target.group(1):
                        return node
                    for value in node.values():
                        found = matching(value)
                        if found:
                            return found
                if isinstance(node, list):
                    for value in node:
                        found = matching(value)
                        if found:
                            return found
                return None
            detail = matching(states)
            if detail:
                def urls(node):
                    if isinstance(node, str) and node.startswith(('https://', 'http://')):
                        candidates.append(node)
                    elif isinstance(node, dict):
                        for value in node.values():
                            urls(value)
                    elif isinstance(node, list):
                        for value in node:
                            urls(value)
                data = detail.get('video', {})
                for key in ('play_addr', 'playAddr', 'download_addr', 'downloadAddr'):
                    urls(data.get(key))
        for frame in self.page.frames:
            for video in frame.locator('video').all():
                if not video.is_visible():
                    continue
                src = video.evaluate('(v)=>v.currentSrc')
                if src.startswith(('https://', 'http://')) and abs(video.evaluate('(v)=>v.duration') - observed['duration']) < 1:
                    candidates.append(src)
        for src in dict.fromkeys(candidates):
                response = self.page.context.request.get(src, timeout=120000,
                                                        headers={'Referer': self.page.url})
                if not response.ok:
                    continue
                content = response.body()
                if len(content) < 1024:
                    continue
                path = self.root / 'captures' / args['run_id'] / 'source.mp4'
                path.write_bytes(content)
                return {'status': 'downloaded', 'path': str(path), 'duration': observed['duration'],
                        'source_url': args['url'], 'bytes': len(content)}
        return {'status': 'media_download_unavailable'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--headless', action='store_true', help='Compatibility option; always headless')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    # This worker owns only a profile below its own candidate data directory.
    with locked(root / 'worker'):
        from playwright.sync_api import sync_playwright
        for sub in ('requests', 'responses'):
            (root / sub).mkdir(parents=True, exist_ok=True)
        # Do not execute queued requests from an earlier worker session (especially publish).
        started = time.time()
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                str(root / 'edge_profile'), channel='msedge', headless=True,
                args=['--mute-audio', '--no-first-run', '--no-default-browser-check'],
                viewport={'width': 1280, 'height': 900}, locale='zh-CN')
            context.add_init_script('''(()=>{
                const play=HTMLMediaElement.prototype.play;
                HTMLMediaElement.prototype.play=function(){this.muted=true;this.volume=0;return play.call(this)};
            })()''')
            browser = Browser(context.pages[0] if context.pages else context.new_page(), root)
            print('Background muted Edge ready; no visible windows.', flush=True)
            while True:
                if browser.page.is_closed():
                    break
                write_json(root / 'heartbeat.json', {'time': time.time(), 'session': started,
                           'background_only': True, 'browser_visible': False, 'audio_muted': True})
                for request in sorted((root / 'requests').glob('*.json')):
                    try:
                        data = json.loads(request.read_text(encoding='utf-8'))
                        if data['session'] != started or data['deadline'] < time.time():
                            raise ValueError('Expired request or different worker session')
                        result = browser.dispatch(data['action'], data['params'])
                        response = {'ok': True, 'result': result}
                    except Exception as exc:
                        response = {'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)[:300]}
                    write_json(root / 'responses' / request.name, response)
                    request.unlink()
                    if data.get('action') == 'stop':
                        context.close()
                        return
                time.sleep(0.15)


if __name__ == '__main__':
    main()
