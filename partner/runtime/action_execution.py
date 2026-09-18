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


def command_budget(command: str, default: float, remaining: float) -> float:
    """An explicit shell timeout may use the remaining bounded action budget."""
    requested = default
    for amount, unit in re.findall(r'(?:^|&&|\n)\s*timeout\s+([0-9]+(?:\.[0-9]+)?)([smh]?)\b', command):
        requested = max(requested, float(amount) * {'':1, 's':1, 'm':60, 'h':3600}[unit])
    return min(requested, remaining)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with tmp.open('w', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.flush()
        os.fsync(out.fileno())
    tmp.replace(path)
    from partner.index.resource_catalog import register_runtime
    register_runtime(path, value)


def run_command(command: str, cwd: str, seconds: float, output_path: Path | None = None) -> dict:
    if seconds <= 0:
        return {'exit_code': None, 'timed_out': True, 'output': '', 'executed': False}
    import tempfile
    start = time.monotonic()
    capture = output_path.open('w+b') if output_path else tempfile.TemporaryFile()
    with capture:
        proc = subprocess.Popen('set -o pipefail\n'+command, shell=True, executable='/bin/bash', cwd=cwd,
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
        f'本动作总预算{seconds}秒、最多{max_turns}次模型交互，普通命令最多{command_seconds}秒；明确写timeout N的长计算可申请更长，但不得超过动作剩余总预算。大计算分小批并逐项保存结果。'
        '新管线先用一个输入跑通到最终产物并读取核验，再扩批量。相同接口/格式异常连续出现时立即停止该批并修复，不要重复几十次同一错误；已完成的结果逐项持久保存。'
        '将必要源码和环境信息批量读取，通常3次探查后应尝试主计算，不能反复列目录耗尽交互次数。'
        '文件检索限于已知项目、安装目录，禁止全盘 find /；环境检查用短 timeout。'
        '结束时写真实结果、证据路径和下一步。不要把思考过程当答案。')
    deadline = time.monotonic() + seconds
    history = saved.get('commands', [])
    for turn in range(max_turns):
        feedback_path = getattr(adapter, 'execution_feedback_path', '')
        if feedback_path and Path(feedback_path).is_file():
            data = Path(feedback_path).read_bytes()
            fingerprint = hashlib.sha256(data).hexdigest()
            receipt = audit / f'operator_feedback_{fingerprint}.json'
            if not receipt.exists():
                feedback = json.loads(data)
                if feedback.get('source_kind') == 'authorized_operator_feedback':
                    conversation += '\n运行期间新增监督反馈（保留原预算与已执行回执，先核实所述事实，不重复成功动作）：\n' + json.dumps(feedback, ensure_ascii=False)[:12000]
                    write_json(checkpoint, {'state':'running', 'commands':history, 'conversation':conversation, 'inflight':None})
                    write_json(receipt, {'source':feedback_path, 'sha256':fingerprint, 'observed_at':time.time(), 'feedback':feedback})
        remaining = deadline - time.monotonic() - 3
        if remaining <= 0:
            break
        raw = ''
        for attempt in range(3):
            remaining = deadline - time.monotonic() - 3
            if remaining <= 0:
                break
            live_budget = (f'\n运行器实际剩余预算：约{int(remaining)}秒，本动作尚余{max_turns-turn}次模型交互。'
                '已完成的步骤不要重做，优先完成已选动作的最小真实结果；时间不足则保留进度并如实说明未完成。')
            raw = direct_api.chat(conversation + live_budget, max_tokens=16384, purpose='action',
                timeout=min(90, remaining), workspace=adapter.workspace,
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
        if any(re.search(r"(?im)^\s*</?(?:parameter|invoke|bash|command)\b", block) for block in blocks):
            blocks = []
        if not blocks and re.search(r'<(?:/?(?:bash|invoke|parameter|tool_call|function_calls))\b|```(?:bash|sh|shell)\s*\n', raw, re.I):
            conversation += ('\n上一条输出不是可执行的完整命令，也不是最终答案。'
                '请将尚未执行的命令重新输出为 <bash>纯 shell 命令</bash>，'
                '不要嵌套 parameter/invoke 标签；不要重做已取得执行回执的命令。\n错误输出：' + raw)
            write_json(checkpoint, {'state':'running', 'commands':history,
                                   'conversation':conversation, 'inflight':None})
            continue
        if not blocks:
            artifacts = [p for p in work.rglob('*') if p.is_file() and '.execution' not in p.parts]
            planning_only = bool(re.search(r"(?i)^(?:I'll|I will|Let me|Looking at|接下来|我将|让我|计划)",raw.strip()))
            explicit_failure = bool(re.search(r'未完成|无法|失败|阻塞|cannot|unable|blocked|failed',raw,re.I))
            if not history or planning_only or (not artifacts and not explicit_failure):
                conversation += ('\n尚无任何实际命令执行回执。刚才是计划性文字，不能作为动作完成。'
                    if not history else '\n刚才是计划性文字或没有本轮产物，不能作为完成回执。已执行命令不必重跑。')
                conversation += ('请直接执行剩余业务动作，输出真实 <bash>命令</bash>；不能执行则明确写未完成、实际错误和已保留证据。禁止把“I will/Let me”作为最终结果。\n'+raw)
                write_json(checkpoint, {'state':'running', 'commands':history,
                                       'conversation':conversation, 'inflight':None})
                if not history and turn >= 2:
                    raise RuntimeError('action produced no executable commands after bounded feedback')
                continue
            write_json(checkpoint, {'state':'completed', 'answer':raw, 'commands':history,
                                   'conversation':conversation, 'inflight':None})
            return raw
        results = []
        for command in blocks:
            if re.search(r'\bpython(?:\d(?:\.\d+)?)?\b[^\n|]+\.py[^\n|]*\|\s*head\b',command):
                conversation += ('\n此命令未执行：Python计算脚本不能接head管道，head提前退出会关闭输出并可能中断计算。'
                    '执行器已经保存完整日志并限制回传长度。请直接运行脚本，或将输出重定向到日志，结束后另用tail查看；不要因输出警告就判断计算失败。')
                write_json(checkpoint, {'state':'running','commands':history,'conversation':conversation,'inflight':None})
                continue
            if re.search(r'\[>[\]]?|\[<\]|\bminimax\[|<\|(?:im_end|im_start|endoftext)\|>', command):
                conversation += '\n命令含模型协议残片，未执行。请重新输出干净的完整bash命令，不附模型边界标记。'
                write_json(checkpoint, {'state':'running','commands':history,'conversation':conversation,'inflight':None})
                continue
            remaining = deadline - time.monotonic() - 3
            if remaining <= 0:
                break
            index = len(history)
            write_json(checkpoint, {'state':'running', 'commands':history, 'conversation':conversation,
                       'inflight':{'index':index, 'command':command}})
            result = run_command(command, str(work), command_budget(command, command_seconds, remaining), audit / f"command_{index:03d}.log")
            result.update(command=command, index=index)
            history.append(result)
            write_json(audit / f'command_{index:03d}.json', result)
            results.append(json.dumps(result, ensure_ascii=False))
            # Persist immediately: completed side effects must never be replayed.
            conversation += '\n真实执行回执：\n' + results[-1]
            if result.get('exit_code') and 'No such file or directory' in result.get('output',''):
                missing={Path(p).name for p in re.findall(r"/[^\s'\"]+\.(?:json|jsonl|csv|pdb|pdbqt|sdf|md|py)",result['output'])}
                index=work.parent/'verified_project_artifacts.json'
                try:
                    known=json.loads(index.read_text()).get('artifacts',[])
                    matches=[r['path'] for r in known if Path(r['path']).name in missing and Path(r['path']).is_file()]
                except (OSError,ValueError,KeyError,TypeError):
                    matches=[]
                if matches:
                    conversation += '\n当前证据索引中的同名文件候选（路径实际存在；仍须核查来源输入，不自动视为等价）：'+json.dumps(matches[:8],ensure_ascii=False)
            if len(history)>=3 and not any(p.is_file() and '.execution' not in p.parts for p in work.rglob('*')):
                conversation += ('\n运行时检查：至今没有本动作产物。现在必须执行一次实际读取/获取/计算并保存原始结果，'
                    '或明确返回具体阻塞。停止列目录和阅读已有工具实现；接口已在初始指令给出，不要猜list_registered_events等函数。'
                    '管道退出码不再掩盖前段错误；沿实际失败修正最小命令，不重新做一整轮环境探查。')
            write_json(checkpoint, {'state':'running', 'commands':history, 'conversation':conversation, 'inflight':None})
            if result['timed_out']:
                break
    write_json(checkpoint, {'state':'budget_exhausted', 'commands':history,
                           'conversation':conversation, 'inflight':None})
    if deadline - time.monotonic() <= 3:
        raise TimeoutError('action wall-clock deadline exhausted; command receipts and checkpoint preserved')
    raise RuntimeError('action model-turn budget exhausted; command receipts and checkpoint preserved')
