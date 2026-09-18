"""Durable action processes. Events submit/collect; workers never wait on models here."""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from partner.runtime.action_execution import write_json

TERMINAL = {'completed', 'failed', 'cancelled', 'timed_out'}


def identity(pid):
    try:
        # starttime protects against PID reuse; zombies are no longer active.
        fields = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip() + ':' + fields[19] if fields[0] != 'Z' else None
    except (OSError, IndexError):
        return None


class BackgroundActions:
    def __init__(self, workspace):
        self.root = Path(workspace).resolve()
        self.directory = self.root / 'state/event_runtime/background'
        self.directory.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _state_lock(self, folder):
        with (folder/'.state.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def inspect(self, task_id):
        path = self.directory / task_id / 'task.json'
        with self._state_lock(path.parent):
            row = json.loads(path.read_text())
            expired = row['status'] not in TERMINAL and time.time() > row['deadline']
            if (row['status'] not in TERMINAL and not expired and row.get('pid')
                    and identity(row['pid']) != row.get('process_start')):
                row.update(status='failed', error='background process exited without terminal receipt')
                write_json(path, row)
        if expired:
            self.cancel(task_id, status='timed_out')
            return json.loads(path.read_text())
        return row

    def cancel(self, task_id, status='cancelled', reason=''):
        path = self.directory / task_id / 'task.json'
        if not path.exists(): return
        # Serialize cancellation and completion: a late success cannot replace
        # cancellation, and cancellation cannot overwrite a terminal success.
        with self._state_lock(path.parent):
            row = json.loads(path.read_text())
            if row['status'] in TERMINAL:
                return
            pid = row.get('pid')
            if pid and identity(pid) == row.get('process_start'):
                pending = {pid}
                for _ in range(8):
                    for proc in Path('/proc').iterdir():
                        if not proc.name.isdigit(): continue
                        try:
                            fields = (proc/'stat').read_text().split(') ',1)[1].split()
                            if int(fields[1]) in pending: pending.add(int(proc.name))
                        except (OSError, ValueError, IndexError): pass
                for child in sorted(pending, reverse=True):
                    try: os.kill(child, signal.SIGKILL)
                    except ProcessLookupError: pass
            row.update(status=status, error=reason or 'background action '+status, finished_at=time.time())
            write_json(path, row)
        # (2026-09-15) Issue I1: cancel()/timed_out path must persist partial_artifacts.json
        # and result.json even when the subprocess never reached its own except branch.
        # Before this change, framework.verify.business_delta=False and evidence_refs=[]
        # because run() was killed by SIGKILL before it could write anything. The receipt
        # is the durable completion signal — write it here so downstream verify can find
        # what was actually produced before the kill.
        work = Path(row.get('work') or '')
        if work.is_dir():
            try:
                partial = sorted(
                    (p for p in work.rglob('*') if p.is_file() and '.execution' not in p.parts),
                    key=lambda p: (len(p.relative_to(work).parts), str(p)),
                )
                commands = sorted((work/'.execution').glob('command_*.json'))
                partial_index = work/'.execution/partial_artifacts.json'
                write_json(partial_index, {
                    'observed_at': time.time(),
                    'count': len(partial),
                    'files': [str(p) for p in partial],
                    'not_a_success_receipt': True,
                    'source': f'cancel:{status}',
                })
                result_path = path.parent/'result.json'
                if not result_path.exists():
                    result = {
                        'ok': False,
                        'status': status,
                        'business_delta': False,
                        'error': reason or f'background action {status}',
                        'files': [str(p) for p in partial[:30]],
                        'evidence_refs': [str(partial_index)] + [str(p) for p in partial[:20]],
                        'semantic_output': {
                            'execution_status': status,
                            'error': reason or f'background action {status}',
                            'partial_artifacts': [str(p) for p in partial[:30]],
                            'partial_artifact_count': len(partial),
                            'partial_artifact_index': str(partial_index),
                            'partial_artifact_preview_truncated': len(partial) > 30,
                            'partial_results_are_not_success': True,
                            'command_receipts': [str(p) for p in commands],
                            'cancel_persisted': True,
                        },
                    }
                    write_json(result_path, result)
            except (OSError, ValueError, TypeError):
                # Cancellation path must not break the kill/log flow; the partial
                # index is a best-effort recovery. The task.json status is the
                # authoritative signal for downstream verify.
                pass

    def submit(self, key, ctx, params, *, seconds=900):
        task_id = 'action_' + hashlib.sha256(key.encode()).hexdigest()[:24]
        folder = self.directory / task_id
        with (self.directory/'admission.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if (folder/'task.json').exists():
                return self.inspect(task_id)
            from partner.governance.scheduler import effective_max_active
            active = [self.inspect(p.parent.name) for p in self.directory.glob('*/task.json')]
            if sum(r['status'] not in TERMINAL for r in active) >= effective_max_active(str(self.root)):
                return {'status':'capacity_wait', 'task_id':task_id}
            folder.mkdir(exist_ok=True)
            payload = {'ctx':ctx, 'params':params, 'seconds':seconds}
            write_json(folder/'input.json', payload)
            row = {'task_id':task_id, 'status':'starting', 'created_at':time.time(),
                   'deadline':time.time()+seconds+10, 'work':params['action_work'],
                   'key':key, 'job_id':ctx['job_id']}
            write_json(folder/'task.json', row)
            with (folder/'runner.log').open('a') as log:
                proc = subprocess.Popen([sys.executable, '-m', 'partner.runtime.background_actions', str(folder)],
                    cwd=str(Path(__file__).resolve().parents[2]), stdout=log, stderr=log,
                    start_new_session=True)
            row.update(pid=proc.pid, process_start=identity(proc.pid), status='running')
            write_json(folder/'task.json', row)
            # Child waits for admission.lock, so the start receipt cannot
            # overwrite a fast terminal result.
            return row


def run(folder):
    from types import SimpleNamespace
    from partner.adapters.adapter import DirectAdapter
    from partner.events.project import action_execute_inline
    folder = Path(folder)
    data = json.loads((folder/'input.json').read_text())
    with (folder.parent/'admission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        row = json.loads((folder/'task.json').read_text())
    ctx = SimpleNamespace(**data['ctx'])
    ctx.adapter = DirectAdapter(ctx.workspace)
    ctx.adapter.task_id = ctx.job_id
    ctx.adapter.project_id = ctx.project_id
    ctx.action_seconds = data['seconds']
    try:
        operation = data['params'].get('_background_operation', 'project_action')
        if operation == 'social_stage':
            from partner.social_video.stages import execute_stage
            result = execute_stage(ctx, data['params'])
        elif operation == 'matched_execution':
            from partner.runtime.matched_execution import execute
            result = {'ok': True, 'status': 'completed', 'semantic_output': execute(
                ctx.workspace, data['params']['experiment_id'], data['params']['kind'])}
        elif operation == 'project_action':
            result = action_execute_inline(ctx, data['params'])
        else:
            raise ValueError('unknown background operation')
    except BaseException as exc:
        work = Path(data['params']['action_work'])
        partial = sorted((p for p in work.rglob('*') if p.is_file() and '.execution' not in p.parts),
                         key=lambda p: (len(p.relative_to(work).parts), str(p)))
        commands = sorted((work/'.execution').glob('command_*.json'))
        partial_index=work/'.execution/partial_artifacts.json'
        write_json(partial_index, {'observed_at':time.time(), 'count':len(partial),
            'files':[str(p) for p in partial], 'not_a_success_receipt':True})
        from partner.runtime.artifact_checks import check_file
        partial_checks = [check_file(p) for p in partial]
        result = {'ok':False, 'status':'failed', 'business_delta':False,
                  'error':f'{type(exc).__name__}: {exc}',
                  'files':[str(p) for p in partial[:30]],
                  'evidence_refs':[str(work/'.execution/checkpoint.json')] + [str(p) for p in partial[:20]],
                  'semantic_output':{'execution_status':'failed', 'error':f'{type(exc).__name__}: {exc}',
                      'partial_artifacts':[str(p) for p in partial[:30]],
                      'partial_artifact_count':len(partial), 'partial_artifact_index':str(partial_index),
                      'partial_artifact_preview_truncated':len(partial)>30,
                      'artifact_checks':partial_checks,
                      'command_receipts':[str(p) for p in commands],
                      'partial_results_are_not_success':True}}
    # Receipt is the durable completion signal. It is observed without LLM polling.
    manager = BackgroundActions(ctx.workspace)
    with manager._state_lock(folder):
        row = json.loads((folder/'task.json').read_text())
        if row['status'] in TERMINAL:
            return
        write_json(folder/'result.json', result)
        row.update(status='completed' if result.get('ok') else 'failed', finished_at=time.time())
        write_json(folder/'task.json', row)


if __name__ == '__main__':
    run(sys.argv[1])
