"""Run ASR in its isolated dependency environment; credentials are unnecessary."""
import json
from pathlib import Path
import sys


def main():
    from faster_whisper import WhisperModel
    audio, output = map(Path, sys.argv[1:3])
    model = WhisperModel('small', device='cpu', compute_type='int8', cpu_threads=4,
                         download_root='/mnt/e/work/partner_workspace/tools/whisper_models')
    segments, info = model.transcribe(str(audio), beam_size=5, vad_filter=True,
                                      condition_on_previous_text=False)
    rows = []
    for s in segments:
        rows.append({'start': s.start, 'end': s.end, 'text': s.text.strip(),
                     'avg_logprob': s.avg_logprob, 'no_speech_prob': s.no_speech_prob})
        if len(rows) % 20 == 0:
            output.with_suffix('.partial.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
            print(json.dumps({'segments': len(rows), 'processed_until': s.end, 'duration': info.duration}), flush=True)
    value = {'model': 'faster-whisper-small', 'language': info.language,
             'language_probability': info.language_probability,
             'duration': info.duration, 'decoded_audio_duration': info.duration,
             'full_audio_processed': True, 'segments': rows,
             'text': '\n'.join(s['text'] for s in rows)}
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(output)
    def stamp(value):
        millis = int(value * 1000)
        return f'{millis // 3600000:02}:{millis // 60000 % 60:02}:{millis // 1000 % 60:02},{millis % 1000:03}'
    output.with_suffix('.srt').write_text('\n\n'.join(
        f"{i}\n{stamp(s['start'])} --> {stamp(s['end'])}\n{s['text']}" for i, s in enumerate(rows, 1)), encoding='utf-8')
    print(json.dumps({'segments': len(rows), 'duration': info.duration}), flush=True)


if __name__ == '__main__':
    main()
