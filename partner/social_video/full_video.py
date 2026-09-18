"""Acquire complete media, transcribe the complete audio, then study every time block."""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import wave

from .events import write_json, digest


def python_runtime():
    path = Path('/mnt/e/work/partner_workspace/tools/social_video_env/bin/python')
    if not path.exists():
        raise RuntimeError('social_video_env is not installed')
    return str(path)


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                             '-of', 'json', str(path)], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def acquire(url, directory, browser, notify):
    directory.mkdir(parents=True, exist_ok=True)
    media = directory / 'source.mp4'
    if not media.exists():
        notify('开始获取完整视频媒体；若站点需要浏览器会话，将使用独立 Edge 中实际可播放的媒体。')
        result = None
        timeout_error = False
        try:
            result = subprocess.run([python_runtime(), '-m', 'yt_dlp', '--no-playlist', '--no-progress',
                                 '--socket-timeout', '25', '--retries', '1', '--merge-output-format', 'mp4',
                                 '-f', 'bv*+ba/b', '-o', str(media), url],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            timeout_error = True
        write_json(directory / 'download_attempt.json', {'url':url, 'method':'yt_dlp',
            'timed_out':timeout_error, 'exit_code':result.returncode if result else None,
            'media_exists':media.exists()})
        if timeout_error or result.returncode or not media.exists():
            notify('命令行下载未完成，开始从独立浏览器中实际可播放的来源获取媒体。')
            downloaded = browser('video_download', {'url': url, 'run_id': directory.parent.name})
            if downloaded.get('status') != 'downloaded':
                raise RuntimeError('站点未提供可下载的完整视频：' + downloaded.get('status', 'unknown'))
            import shutil
            shutil.copyfile(downloaded['path'], media)
            write_json(directory / 'browser_download.json', downloaded)
    info = probe(media)
    duration = float(info['format']['duration'])
    browser_evidence = directory / 'browser_download.json'
    if browser_evidence.exists():
        expected = float(json.loads(browser_evidence.read_text())['duration'])
        if abs(expected - duration) > max(2, expected * .02):
            raise ValueError('下载媒体时长与目标播放器不一致，拒绝学习错误视频')
    if not math.isfinite(duration) or not 0 < duration <= 3600:
        raise ValueError('本次有界学习支持不超过1小时的非直播视频，超限不会静默截断')
    if not any(s.get('codec_type') == 'audio' for s in info['streams']):
        raise ValueError('媒体中没有音轨，无法完成用户要求的音轨转录')
    if not any(s.get('codec_type') == 'video' for s in info['streams']):
        raise ValueError('媒体中没有视频轨')
    write_json(directory / 'media_probe.json', info)
    return media, duration


def transcribe(media, directory, duration, notify):
    output = directory / 'transcript.json'
    if output.exists():
        return json.loads(output.read_text())
    audio = directory / 'audio.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-y', '-i', str(media), '-vn',
                    '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(audio)],
                   check=True, capture_output=True, timeout=600)
    with wave.open(str(audio)) as source:
        audio_duration = source.getnframes() / source.getframerate()
    if abs(audio_duration - duration) > max(2, duration * .02):
        raise RuntimeError('音轨与视频时长不一致，拒绝声称完整转录')
    notify(f'完整媒体 {duration:.1f} 秒，音轨 {audio_duration:.1f} 秒；开始本地语音转录。')
    subprocess.run([python_runtime(), '-m', 'partner.social_video.transcribe_worker',
                    str(audio), str(output)], cwd=str(Path(__file__).resolve().parents[2]),
                   check=True, timeout=7200)
    transcript = json.loads(output.read_text())
    transcript['decoded_audio_duration'] = audio_duration
    transcript['full_audio_processed'] = True
    write_json(output, transcript)
    return transcript


def learn(url, directory, browser, model, notify):
    directory = Path(directory)
    assets = directory / 'full_video'
    media, duration = acquire(url, assets, browser, notify)
    transcript = transcribe(media, assets, duration, notify)
    from PIL import Image, ImageDraw
    import cv2
    cap = cv2.VideoCapture(str(media))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    chapters = []
    try:
        # Every 30-second block is studied, with all transcript segments overlapping it.
        for index in range(math.ceil(duration / 30)):
            start, end = index * 30, min(duration, (index + 1) * 30)
            chapter_file = assets / f'chapter-{index:04d}.json'
            if chapter_file.exists():
                chapters.append(json.loads(chapter_file.read_text()))
                continue
            canvas = Image.new('RGB', (960, 660), 'white')
            draw = ImageDraw.Draw(canvas)
            timestamps = []
            for slot in range(6):
                timestamp = start + (end - start) * (slot + .5) / 6
                timestamp = min(timestamp, max(0, duration - 1 / fps))
                cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError(f'无法解码 {timestamp:.2f} 秒的画面')
                timestamp = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                frame = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                frame.thumbnail((320, 300))
                x, y = slot % 3 * 320, slot // 3 * 330
                canvas.paste(frame, (x, y + 25))
                draw.text((x + 8, y + 5), f'{timestamp:.2f}s', fill='black')
                timestamps.append(timestamp)
            sheet = assets / f'frames-{index:04d}.jpg'
            canvas.save(sheet, quality=85)
            speech = [s for s in transcript['segments'] if s['end'] > start and s['start'] < end]
            visual = model('video_frame', {'path': str(sheet), 'time': start, 'grid': '2x3'})
            chapter = {'start': start, 'end': end, 'timestamps': timestamps,
                       'visual_observation': visual, 'speech_segments': speech, 'sheet': str(sheet)}
            # Preserve every segment verbatim for the final cross-modal reasoning call.
            # Avoid summarising the transcript once per block and losing qualifications.
            chapter['learning'] = {'visual_observation': visual, 'speech_segments': speech}
            if not visual:
                raise RuntimeError('分段视觉模型返回空内容')
            write_json(chapter_file, chapter)
            chapters.append(chapter)
            if (index + 1) % 6 == 0 or end == duration:
                notify(f'已完成 {end:.0f}/{duration:.0f} 秒：口播与对应画面已整理，正在形成学习结论。')
    finally:
        cap.release()
    notes = model('video_full_notes', {'url': url, 'duration': duration,
                                      'chapters': [{k: c[k] for k in ('start', 'end', 'learning')} for c in chapters]})
    from .cli import final_text
    notes = final_text(notes)
    marker = '<!-- REPORT_COMPLETE -->'
    if not notes.endswith(marker):
        raise RuntimeError('综合学习笔记未完整结束，保留转录和画面缓存供恢复')
    notes = notes[:-len(marker)].strip()
    if len(notes) < 100:
        raise RuntimeError('综合学习笔记内容不足')
    evidence = {'source_url': url, 'duration': duration, 'report_complete': True, 'full_audio_processed': True,
                'audio_duration': transcript['decoded_audio_duration'], 'transcript': str(assets / 'transcript.json'),
                'chapters': chapters, 'visual_sampling_interval_max_seconds': 5,
                'scope': 'complete_media_full_audio_all_30s_blocks_with_sampled_visuals'}
    write_json(directory / 'video_evidence.json', evidence)
    report = ('# 视频学习笔记\n\n来源：' + url
              + f'\n\n完整媒体与音轨：{duration:.1f} 秒；逐30秒联合分析，画面间隔最多5秒。'
              + '\n语音转录和视觉识别可能存在误差；口播主张不自动等于已验证事实。\n\n' + notes)
    (directory / 'notes.md').write_text(report, encoding='utf-8')
    evidence['report_hash'] = digest(report)
    evidence['notification'] = notify(report)
    write_json(directory / 'video_evidence.json', evidence)
    return evidence
