"""Deadline-bound command execution with durable, replay-safe receipts."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with tmp.open('w', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.flush()
        os.fsync(out.fileno())
    tmp.replace(path)


def run_command(command: str, cwd: str, seconds: float, output_path: Path | None = None) -> dict:
    if seconds <= 0:
        return {'exit_code': None, 'timed_out': True, 'output': '', 'executed': False}
    import tempfile
    start = time.monotonic()
    capture = output_path.open('w+b') if output_path else tempfile.TemporaryFile()
    with capture:
        proc = subprocess.Popen(command, shell=True, executable='/bin/bash', cwd=cwd,
                                stdout=capture, stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        try:
            proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        # A shell must not leave an untracked '&' child after its receipt.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        capture.flush()
        total = capture.seek(0, os.SEEK_END)
        capture.seek(max(0, total-24000))
        output = capture.read().decode('utf-8', errors='replace')
    return {'pid': proc.pid, 'exit_code': proc.returncode, 'timed_out': timed_out,
            'executed': True, 'elapsed_seconds': time.monotonic() - start,
            'output':output, 'stdout_bytes':total,
            'output_path':str(output_path) if output_path else '', 'output_truncated':total>24000}


def execute(adapter, prompt: str, *, seconds: float = 240, max_turns: int = 32, command_seconds: float = 180) -> str:
    from partner.adapters import direct_api
    match = re.search(r'工作目录\s*[=:：]\s*([^\n]+)', prompt)
    work = Path(match.group(1) if match else adapter.workspace).resolve()
    work.mkdir(parents=True, exist_ok=True)
    audit = work / '.execution'
    audit.mkdir(exist_ok=True)
    checkpoint = audit / 'checkpoint.json'
    saved = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    if saved.get('state') == 'completed':
        return saved['answer']
    if saved.get('inflight'):
        raise RuntimeError('interrupted command has unknown effects; automatic replay refused')
    conversation = saved.get('conversation') or (prompt + '\n仅执行已选的一个有界动作，不完成整个项目。'
        '\n命令用 <bash>...</bash> 包裹。生成脚本后必须真正运行，并检查退出码和数据。'
        '不得用写结论、复制旧文件或文件存在冒充实验。所有新业务产物写入给定工作目录。'
        '不得把候选输出写成常量名单来代替用户要求的生成算法。种子或对照可明确标记为输入，但候选必须来自可复现的真实生成、搜索或变换，并记录来源。'
        '复用中间产物前核对来源输入、参数、坐标/索引及单位一致性，不能因文件名相同就当作当前输入的处理结果。'
        '依赖失败先依据真实 API 修复。不得用伪工具、硬造输入或替代评分冒充指定计算；简化替代必须明确方法与局限，不能借此宣称原实验通过。'
        '不要用nohup、后台&或setsid脱离任务；系统已提供后台执行和取消管理。'
        'timed_out=true 回执表示该命令进程组已被系统停止，不必再 pkill；pkill -f 匹配整条命令文本可能误杀当前 shell，阻止后续修正代码写入。'
        f'本动作总预算{seconds}秒、最多{max_turns}次模型交互，每条命令最多{command_seconds}秒；大计算分小批并保存结果。'
        '新管线先用一个输入跑通到最终产物并读取核验，再扩批量。相同接口/格式异常连续出现时立即停止该批并修复，不要重复几十次同一错误；已完成的结果逐项持久保存。'
        '将必要源码和环境信息批量读取，通常3次探查后应尝试主计算，不能反复列目录耗尽交互次数。'
        '文件检索限于已知项目、安装目录，禁止全盘 find /；环境检查用短 timeout。'
        '结束时写真实结果、证据路径和下一步。不要把思考过程当答案。')
    deadline = time.monotonic() + seconds
    history = saved.get('commands', [])
    for turn in range(max_turns):
        remaining = deadline - time.monotonic() - 3
        if remaining <= 0:
            break
        raw = ''
        for attempt in range(3):
            remaining = deadline - time.monotonic() - 3
            if remaining <= 0:
                break
            raw = direct_api.chat(conversation, max_tokens=16384, purpose='action',
                timeout=min(120, remaining), workspace=adapter.workspace,
                task_id=getattr(adapter,"task_id",""), project_id=getattr(adapter,"project_id",""),
                event_type="project.action_execute")
            usage = direct_api.get_last_usage()
            text = re.sub(r'<(think|analysis)>.*?</\1>', '', raw or '', flags=re.S | re.I).strip()
            if text and not re.search(r'</?(think|analysis)>', text, re.I) and usage.get('finish_reason') != 'length':
                raw = text
                break
            raw = ''
            if attempt < 2:
                time.sleep(max(0, min(2 ** attempt, deadline - time.monotonic() - 3)))
        if not raw:
            break
        blocks = adapter._extract_commands(raw)
        if any(re.match(r"\s*<(?:parameter|invoke|bash)\b", block, re.I) for block in blocks):
            blocks = []
        if not blocks and re.search(r'<(?:/?(?:bash|invoke|parameter|tool_call|function_calls))\b', raw, re.I):
            conversation += ('\n上一条输出不是可执行的完整命令，也不是最终答案。'
                '请将尚未执行的命令重新输出为 <bash>纯 shell 命令</bash>，'
                '不要嵌套 parameter/invoke 标签；不要重做已取得执行回执的命令。\n错误输出：' + raw)
            write_json(checkpoint, {'state':'running', 'commands':history,
                                   'conversation':conversation, 'inflight':None})
            continue
        if not blocks:
            if not history:
                conversation += ('\n尚无任何实际命令执行回执。刚才是计划性文字，不能作为动作完成。'
                    '请输出一个完成当前已选动作所需的真实 <bash>命令</bash>；不能执行则说明具体错误，不能假报完成。\n' + raw)
                write_json(checkpoint, {'state':'running', 'commands':history,
                                       'conversation':conversation, 'inflight':None})
                if turn >= 2:
                    raise RuntimeError('action produced no executable commands after bounded feedback')
                continue
            write_json(checkpoint, {'state':'completed', 'answer':raw, 'commands':history,
                                   'conversation':conversation, 'inflight':None})
            return raw
        results = []
        for command in blocks:
            remaining = deadline - time.monotonic() - 3
            if remaining <= 0:
                break
            index = len(history)
            write_json(checkpoint, {'state':'running', 'commands':history, 'conversation':conversation,
                       'inflight':{'index':index, 'command':command}})
            result = run_command(command, str(work), min(command_seconds, remaining), audit / f"command_{index:03d}.log")
            result.update(command=command, index=index)
            history.append(result)
            write_json(audit / f'command_{index:03d}.json', result)
            results.append(json.dumps(result, ensure_ascii=False))
            # Persist immediately: completed side effects must never be replayed.
            conversation += '\n真实执行回执：\n' + results[-1]
            write_json(checkpoint, {'state':'running', 'commands':history, 'conversation':conversation, 'inflight':None})
            if result['timed_out']:
                break
    write_json(checkpoint, {'state':'budget_exhausted', 'commands':history,
                           'conversation':conversation, 'inflight':None})
    if deadline - time.monotonic() <= 3:
        raise TimeoutError('action wall-clock deadline exhausted; command receipts and checkpoint preserved')
    raise RuntimeError('action model-turn budget exhausted; command receipts and checkpoint preserved')
