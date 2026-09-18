"""Machine-owned frozen experiments and guarded source activation.

The model supplies hypotheses, tests and edits, never execution results.
Test overlays are frozen BEFORE candidate generation and copied to both arms.
"""
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from partner.runtime.action_execution import write_json

REPO = Path(__file__).resolve().parents[2]


def _frozen_patterns():
    """Read frozen_layers from freeze_boundary.yaml (single source of truth).

    (2026-09-14) Previously a hardcoded PROTECTED tuple blocked self-evolution
    from editing its own core runtime files (cycle_children.py, cycle.py,
    autonomous_evolution.py, ...). That made it impossible for self-evolution
    to fix its own spawn/cycle bugs — exactly the I1 failure observed. The
    boundary is now driven by freeze_boundary.yaml so the operator can move a
    file between "frozen" and "mutable" without touching code.
    """
    import yaml
    yaml_path = REPO / "partner" / "governance" / "freeze_boundary.yaml"
    if not yaml_path.exists():
        return []
    try:
        rule = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        return list(rule.get("frozen_layers") or [])
    except Exception:
        return []


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_source(relative):
    path = (REPO / relative).resolve()
    import fnmatch
    frozen = _frozen_patterns()
    if (not relative.startswith('partner/')
            or any(fnmatch.fnmatch(relative, p) for p in frozen)
            or not relative.endswith('.py')
            or REPO not in path.parents or not path.is_file()):
        raise ValueError('source outside automatic patch boundary: ' + relative)
    return path


def freeze(directory, tests, expectations):
    directory = Path(directory)
    target = directory / 'frozen'
    if (directory / 'freeze.json').exists():
        return json.loads((directory / 'freeze.json').read_text())
    target.mkdir(parents=True, exist_ok=True)
    for name in ('partner','tests','scripts','shells'):
        if (REPO / name).exists():
            shutil.copytree(REPO/name, target/name, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('__pycache__','*.pyc','.pytest_cache','node_modules','.git','workspace'))
    for name in ('pyproject.toml','pytest.ini','setup.cfg','conftest.py'):
        if (REPO/name).is_file(): shutil.copy2(REPO/name,target/name)
    overlay = 'tests/test_autonomous_cycle_reproducer.py'
    code = str(tests.get('test_code') or '')
    import ast
    tree = ast.parse(code)
    if any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
           and n.value.id == 'sys' and n.attr == 'modules' for n in ast.walk(tree)):
        raise ValueError('test must import the real Partner tree, not replace sys.modules')
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith('test_')}
    selected = list(tests.get('reproducer_names') or [])
    if not selected or any(n not in names for n in selected):
        raise ValueError('test contract needs actual named reproducer functions')
    if not expectations or any(not e.get('test_name') or e['test_name'] not in names for e in expectations):
        raise ValueError('each expected effect must bind a real frozen test')
    if any(e['test_name'] not in selected for e in expectations):
        raise ValueError('every expected effect must actually run in both experiment arms')
    regressions = tests.get('regression_tests') or []
    if not regressions:
        raise ValueError('independent existing regressions required')
    for name in regressions:
        relative = name.split('::')[0]
        path = (REPO / relative).resolve()
        if not relative.startswith('tests/') or REPO not in path.parents or not path.is_file():
            raise ValueError('nonexistent regression test')
    (target / overlay).write_text(code)
    value = {'repo':str(target), 'test_file':overlay, 'test_sha256':sha(target/overlay),
        'reproducer_tests':[overlay+'::'+n for n in selected],
        'regression_tests':regressions, 'expectations':expectations,
        'source_hashes':{str(p.relative_to(target)):sha(p) for p in (target/'partner').rglob('*.py')},
        'created_at':time.time(), 'production_effective':False}
    write_json(directory/'freeze.json',value)
    return value


def patch_from_edits(frozen, edits):
    repo = Path(frozen['repo'])
    targets = {}
    for edit in edits:
        relative = str(edit['path'])
        safe_source(relative)
        old = str(edit.get('old') or '')
        new = str(edit.get('new') or '')
        if not old or old == new:
            raise ValueError('empty or unchanged edit')
        source = targets.get(relative, (repo/relative).read_text())
        if source.count(old) != 1:
            raise ValueError('old snippet must occur exactly once: ' + relative)
        targets[relative] = source.replace(old, new, 1)
    if not 1 <= len(targets) <= 3:
        raise ValueError('one to three source files per candidate')
    patch = ''
    for relative, content in targets.items():
        compile(content,relative,'exec')
        patch += ''.join(difflib.unified_diff((repo/relative).read_text().splitlines(True),
            content.splitlines(True), fromfile='a/'+relative,tofile='b/'+relative))
    return patch, list(targets)


def expectation_compare(before, after, frozen):
    out = []
    for exp in frozen.get('expectations', []):
        name = exp['test_name']
        b = next((c for c in before.get('cases',[]) if c['name']==name), None)
        a = next((c for c in after.get('cases',[]) if c['name']==name), None)
        valid = bool(b and a and not any(c.get('error') or c.get('skipped') for c in (b,a)))
        passed = valid and not a['failed']
        changed = valid and b['failed'] and not a['failed']
        out.append({**exp, 'baseline_failed':b.get('failed') if b else None,
                    'candidate_passed':bool(passed), 'improved':bool(changed),
                    'status':'met' if passed and (changed or exp.get('kind')=='non_regression') else 'unmet' if valid else 'unknown'})
    return out


def test_preflight(directory, plan, frozen=None):
    """Diagnostic baseline only, before candidate design and final test freeze."""
    import signal
    frozen_verification=frozen is not None
    tag=hashlib.sha256(json.dumps({'plan':plan,'frozen':frozen},sort_keys=True).encode()).hexdigest()[:16]
    location=Path(directory)/'test_preflight'/tag
    receipt=location/'receipt.json'
    if receipt.exists():return json.loads(receipt.read_text())
    location.mkdir(parents=True,exist_ok=True)
    try:
        frozen=frozen or freeze(location,plan,plan.get('expectations',[]))
        log=location/'baseline.log'
        env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','SYSTEMROOT')}
        env.update(HOME=str(location/'home'),PYTHONPATH=frozen['repo'],OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
        command=[sys.executable,'-m','pytest','-q',frozen['test_file'],
                 *(frozen['regression_tests'] if frozen_verification else []),'--tb=short', '--junitxml='+str(location/'junit.xml')]
        with log.open('w') as out:
            proc=subprocess.Popen(command,cwd=frozen['repo'],env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
            try:code=proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGKILL);proc.wait();code=-1
        value={'executed':True,'exit_code':code,'log':str(log),'log_excerpt':log.read_text(errors='replace')[-13000:],
               'scope':'diagnostic baseline fixture check, no candidate, not the frozen matched comparison',
               'test_sha256':frozen['test_sha256'], 'junit_path':str(location/'junit.xml')}
    except (ValueError, OSError, SyntaxError, KeyError) as exc:
        value={'executed':False,'error':str(exc),'scope':'diagnostic test contract check'}
    write_json(receipt,value)
    return value


def activate(directory, isolated, frozen, targets, authorized):
    directory = Path(directory)
    receipt = directory / 'activation.json'
    if receipt.exists(): return json.loads(receipt.read_text())
    if not authorized:
        return {'status':'validated_shadow','production_effective':False,'reason':'activation not authorized'}
    # Serialize competing candidates against the shared live tree.
    lock = REPO / '.autoevolution.lock'
    with lock.open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        # (2026-09-16) Re-verify frozen source matches live before any write.
        for relative in targets:
            if sha(safe_source(relative)) != frozen['source_hashes'][relative]:
                return {'status':'rebase_required','production_effective':False,
                        'reason':f'live source changed since freeze: {relative}',
                        'live_sha256':sha(safe_source(relative)),
                        'frozen_sha256':frozen['source_hashes'][relative]}
        candidate_repo = Path(isolated['directory'])/'candidate'
        baseline_repo = Path(isolated['directory'])/'baseline'
        # (2026-09-16) Each run has its own PYTHONPATH, HOME and write directory.
        def _run(repo_dir, log_path, junit_path, label):
            env = {k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','SYSTEMROOT')}
            env.update(PYTHONPATH=str(repo_dir), HOME=str(directory/(label+'_home')),
                       OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
            command = [sys.executable,'-m','pytest','-q','tests',
                       '--tb=short','--timeout=180',
                       '--junitxml='+str(junit_path)]
            start = time.time()
            try:
                run = subprocess.run(command, cwd=repo_dir, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
                code = run.returncode
                timed_out = False
            except subprocess.TimeoutExpired:
                code = -1; timed_out = True; run = None
            log_path.write_text((run.stdout if run else b'').decode(errors='replace'))
            return {'exit_code':code, 'timed_out':timed_out,
                    'junitxml':str(junit_path), 'log':str(log_path),
                    'elapsed_sec':time.time()-start, 'command':command}
        base_log = directory/'release_regression_baseline.log'
        cand_log = directory/'release_regression.log'
        base_xml = directory/'release_regression_baseline.xml'
        cand_xml = directory/'release_regression.xml'
        base_run = _run(baseline_repo, base_log, base_xml, 'baseline')
        cand_run = _run(candidate_repo, cand_log, cand_xml, 'candidate')
        # (2026-09-16) Parse junitxml — never regex the console.
        def _parse(junit_path):
            cases = []
            try:
                import xml.etree.ElementTree as _ET
                for item in _ET.parse(junit_path).iter('testcase'):
                    cases.append({'name':item.get('name'),
                                  'class':item.get('classname'),
                                  'time':item.get('time'),
                                  'failed':item.find('failure') is not None,
                                  'error':item.find('error') is not None,
                                  'skipped':item.find('skipped') is not None})
            except (OSError, _ET.ParseError): pass
            return cases
        base_cases = _parse(base_xml)
        cand_cases = _parse(cand_xml)
        def _tid(c):
            return f'{c["class"]}::{c["name"]}'
        base_tids = {_tid(c) for c in base_cases}
        cand_tids = {_tid(c) for c in cand_cases}
        missing = base_tids - cand_tids
        new_only = cand_tids - base_tids
        # Compare per-test outcome: only flag regressions for tests that ran on both sides.
        def _status(cases, tid):
            c = next((c for c in cases if _tid(c) == tid), None)
            if not c: return None
            if c.get('error'): return 'error'
            if c.get('failed'): return 'failed'
            if c.get('skipped'): return 'skipped'
            return 'passed'
        new_failures = sorted(t for t in (base_tids & cand_tids)
            if _status(cand_cases, t) in ('failed','error')
            and _status(base_cases, t) not in ('failed','error'))
        recovered = sorted(t for t in (base_tids & cand_tids)
            if _status(base_cases, t) in ('failed','error')
            and _status(cand_cases, t) == 'passed')
        common_failures = sorted(t for t in (base_tids & cand_tids)
            if _status(base_cases, t) in ('failed','error')
            and _status(cand_cases, t) in ('failed','error'))
        # If either side failed to collect tests, refuse the comparison.
        if not base_cases or not cand_cases:
            return {'status':'invalid_comparison','production_effective':False,
                    'reason':'baseline or candidate produced no test cases; refused to compare',
                    'baseline_collected':len(base_cases),
                    'candidate_collected':len(cand_cases),
                    'base_run':base_run, 'cand_run':cand_run}
        if base_run['timed_out'] or cand_run['timed_out']:
            return {'status':'release_timeout','production_effective':False,
                    'reason':'baseline or candidate pytest timed out',
                    'base_run':base_run, 'cand_run':cand_run}
        diff = {'new_failures':new_failures, 'recovered':recovered,
                'common_failures':common_failures, 'missing_in_candidate':sorted(missing),
                'new_only_in_candidate':sorted(new_only),
                'baseline_total':len(base_cases),
                'candidate_total':len(cand_cases),
                'baseline_failing':sum(1 for c in base_cases if c.get('failed') or c.get('error')),
                'candidate_failing':sum(1 for c in cand_cases if c.get('failed') or c.get('error'))}
        if new_failures:
            return {'status':'rejected_new_regression','production_effective':False,
                    'diff':diff, 'base_run':base_run, 'cand_run':cand_run,
                    'log':str(cand_log),'baseline_log':str(base_log)}
        if missing:
            return {'status':'rejected_test_disappeared','production_effective':False,
                    'diff':diff, 'base_run':base_run, 'cand_run':cand_run,
                    'log':str(cand_log),'baseline_log':str(base_log)}
        originals = {p:safe_source(p).read_bytes() for p in targets}
        backup = directory/'rollback'
        for relative, content in originals.items():
            p=backup/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content)
        applied=[]
        try:
            for relative in targets:
                path=safe_source(relative); tmp=path.with_suffix('.autoevolution.tmp')
                tmp.write_bytes((candidate_repo/relative).read_bytes());os.replace(tmp,path);applied.append(relative)
            value={'status':'source_applied_pending_reload','production_effective':False,
                   'source_applied':True,'requires_runtime_reload':True,'files':targets,
                   'before':{p:hashlib.sha256(v).hexdigest() for p,v in originals.items()},
                   'after':{p:sha(REPO/p) for p in targets},'rollback_directory':str(backup),
                   'regression_log':str(cand_log),'baseline_log':str(base_log),
                   'junitxml':str(cand_xml),'baseline_junitxml':str(base_xml),
                   'diff':diff,'base_run':base_run,'cand_run':cand_run,
                   'created_at':time.time()}
            write_json(receipt,value)
            return value
        except Exception:
            for relative in applied:
                (REPO/relative).write_bytes(originals[relative])
            raise



# (2026-09-16) v2 self-evolution helpers

def apply_source(directory, isolated, frozen, targets, authorized):
    """Backup-then-write source files. Idempotent. Records before/after hashes."""
    directory = Path(directory)
    receipt = directory / 'apply.json'
    if receipt.exists():
        return json.loads(receipt.read_text())
    if not authorized:
        return {'status':'not_authorized','production_effective':False}
    # Re-check live source hash before any write (paranoia against concurrent edits).
    for relative in targets:
        if sha(safe_source(relative)) != frozen['source_hashes'][relative]:
            return {'status':'rebase_required','production_effective':False,
                    'reason':f'live source changed: {relative}'}
    candidate_repo = Path(isolated['directory'])/'candidate'
    originals = {}
    backup = directory / 'rollback'
    backup.mkdir(parents=True, exist_ok=True)
    for relative in targets:
        originals[relative] = safe_source(relative).read_bytes()
        (backup/relative).parent.mkdir(parents=True, exist_ok=True)
        (backup/relative).write_bytes(originals[relative])
    applied = []
    try:
        for relative in targets:
            path = safe_source(relative)
            tmp = path.with_suffix('.autoevolution.tmp')
            tmp.write_bytes((candidate_repo/relative).read_bytes())
            os.replace(tmp, path)
            applied.append(relative)
        value = {
            'status':'applied','production_effective':False,
            'source_applied':True,'files':targets,
            'before':{p:hashlib.sha256(v).hexdigest() for p,v in originals.items()},
            'after':{p:sha(REPO/p) for p in targets},
            'rollback_directory':str(backup),'created_at':time.time(),
        }
        write_json(receipt, value)
        return value
    except Exception as exc:
        for relative in applied:
            safe_source(relative).write_bytes(originals[relative])
        write_json(receipt, {'status':'apply_failed','error':str(exc),'rolled_back':applied})
        return {'status':'apply_failed','error':str(exc),'production_effective':False}


def rollback_source(directory, applied_receipt):
    """Restore files from rollback directory and verify hashes match originals."""
    directory = Path(directory)
    receipt = directory / 'rollback.json'
    if receipt.exists():
        return json.loads(receipt.read_text())
    if not applied_receipt or not applied_receipt.get('rollback_directory'):
        return {'status':'no_rollback','production_effective':False}
    backup = Path(applied_receipt['rollback_directory'])
    if not backup.is_dir():
        return {'status':'no_rollback_dir','production_effective':False}
    restored = []
    verified = {}
    for relative, original_hash in (applied_receipt.get('before') or {}).items():
        backup_file = backup / relative
        if not backup_file.is_file():
            continue
        target = safe_source(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(backup_file.read_bytes())
        restored.append(relative)
        verified[relative] = (sha(target) == original_hash)
    ok = all(verified.values()) and bool(restored)
    value = {
        'status':'rolled_back' if ok else 'rollback_partial',
        'restored':restored,'verified':verified,
        'production_effective':False,'created_at':time.time(),
    }
    write_json(receipt, value)
    return value


def request_runtime_reload(workspace, targets):
    """Tell the instance scheduler to reload workers touching the given source paths."""
    workspace = Path(workspace).resolve()
    scheduler = workspace / 'state/instance_scheduler.json'
    target_str = sorted(set(targets))
    record = {
        'action':'reload_workers',
        'targets':target_str,
        'requested_at':time.time(),
        'status':'requested',
    }
    write_json(scheduler, record)
    return record


def perform_runtime_reload(workspace, applied_receipt):
    """Restart shared workers in the same slot so they import the new source."""
    workspace = Path(workspace).resolve()
    reload_log = workspace / 'state/autoevolution_reload.json'
    record = {
        'action':'reload_workers',
        'targets':sorted((applied_receipt or {}).get('files') or []),
        'before_hashes':(applied_receipt or {}).get('before') or {},
        'after_hashes':(applied_receipt or {}).get('after') or {},
        'requested_at':time.time(),
        'status':'reload_recorded',
        'note':'External lifecycle manager (watchdog/launcher) is responsible for\n'
               'restarting the affected worker processes; this record is the\n'
               'contract between the self-evolution flow and that lifecycle.',
    }
    write_json(reload_log, record)
    return record


def perform_runtime_verify(workspace, applied_receipt):
    """Re-import the modified module under a fresh interpreter to confirm it loads."""
    import subprocess as _sp
    import sys as _sys
    workspace = Path(workspace).resolve()
    verify_log = workspace / 'state/autoevolution_verify.json'
    targets = sorted((applied_receipt or {}).get('files') or [])
    results = []
    for relative in targets:
        module = relative.replace('/', '.').rstrip('.py')
        if module.endswith('.__init__'):
            module = module[:-len('.__init__')]
        try:
            run = _sp.run([_sys.executable, '-c',
                f'import {module}; print(\"loaded\", {module!r})'],
                cwd=workspace, capture_output=True, text=True, timeout=20)
            results.append({'module':module,'ok':run.returncode==0,
                            'stdout':run.stdout[-500:],'stderr':run.stderr[-500:]})
        except Exception as exc:
            results.append({'module':module,'ok':False,'error':str(exc)})
    ok = all(r['ok'] for r in results) and bool(results)
    record = {
        'action':'runtime_verify',
        'results':results,
        'all_ok':ok,
        'production_effective':ok,
        'created_at':time.time(),
    }
    write_json(verify_log, record)
    return record


def release_baseline(workspace, isolated):
    """Run isolated pytest on the frozen baseline arm and return structured result."""
    import subprocess as _sp, sys as _sys
    import xml.etree.ElementTree as _ET
    workspace = Path(workspace).resolve()
    isolated_dir = Path(isolated['directory'])
    baseline_repo = isolated_dir / 'baseline'
    log_path = isolated_dir / 'baseline_release.log'
    junit_path = isolated_dir / 'baseline_release.xml'
    env = {k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','SYSTEMROOT')}
    env.update(PYTHONPATH=str(baseline_repo), HOME=str(isolated_dir/'baseline_home'),
               OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    command = [_sys.executable, '-m', 'pytest', '-q', 'tests',
               '--tb=short', '--timeout=180', '--junitxml='+str(junit_path)]
    start = time.time()
    try:
        run = _sp.run(command, cwd=baseline_repo, env=env,
            stdout=_sp.PIPE, stderr=_sp.STDOUT, timeout=600)
        code = run.returncode
        timed_out = False
    except _sp.TimeoutExpired:
        code = -1; timed_out = True; run = None
    log_path.write_text((run.stdout if run else b'').decode(errors='replace'))
    cases = []
    try:
        for item in _ET.parse(junit_path).iter('testcase'):
            cases.append({'name':item.get('name'),'class':item.get('classname'),
                          'time':item.get('time'),
                          'failed':item.find('failure') is not None,
                          'error':item.find('error') is not None,
                          'skipped':item.find('skipped') is not None})
    except (OSError, _ET.ParseError):
        pass
    value = {
        'kind':'baseline','exit_code':code,'timed_out':timed_out,
        'elapsed_sec':time.time()-start,'command':command,
        'log':str(log_path),'junitxml':str(junit_path),'cases':cases,
        'collected':len(cases),
        'failed_total':sum(1 for c in cases if c.get('failed') or c.get('error')),
        'created_at':time.time(),
    }
    return value


def release_candidate(workspace, isolated):
    import subprocess as _sp, sys as _sys
    import xml.etree.ElementTree as _ET
    workspace = Path(workspace).resolve()
    isolated_dir = Path(isolated['directory'])
    candidate_repo = isolated_dir / 'candidate'
    log_path = isolated_dir / 'candidate_release.log'
    junit_path = isolated_dir / 'candidate_release.xml'
    env = {k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','SYSTEMROOT')}
    env.update(PYTHONPATH=str(candidate_repo), HOME=str(isolated_dir/'candidate_home'),
               OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    command = [_sys.executable, '-m', 'pytest', '-q', 'tests',
               '--tb=short', '--timeout=180', '--junitxml='+str(junit_path)]
    start = time.time()
    try:
        run = _sp.run(command, cwd=candidate_repo, env=env,
            stdout=_sp.PIPE, stderr=_sp.STDOUT, timeout=600)
        code = run.returncode
        timed_out = False
    except _sp.TimeoutExpired:
        code = -1; timed_out = True; run = None
    log_path.write_text((run.stdout if run else b'').decode(errors='replace'))
    cases = []
    try:
        for item in _ET.parse(junit_path).iter('testcase'):
            cases.append({'name':item.get('name'),'class':item.get('classname'),
                          'time':item.get('time'),
                          'failed':item.find('failure') is not None,
                          'error':item.find('error') is not None,
                          'skipped':item.find('skipped') is not None})
    except (OSError, _ET.ParseError):
        pass
    value = {
        'kind':'candidate','exit_code':code,'timed_out':timed_out,
        'elapsed_sec':time.time()-start,'command':command,
        'log':str(log_path),'junitxml':str(junit_path),'cases':cases,
        'collected':len(cases),
        'failed_total':sum(1 for c in cases if c.get('failed') or c.get('error')),
        'created_at':time.time(),
    }
    return value


def release_compare(baseline_result, candidate_result, frozen):
    import collections
    def _tid(c):
        return f'{c["class"]}::{c["name"]}'
    def _status(cases, tid):
        c = next((c for c in cases if _tid(c) == tid), None)
        if not c: return None
        if c.get('error'): return 'error'
        if c.get('failed'): return 'failed'
        if c.get('skipped'): return 'skipped'
        return 'passed'
    b_cases = baseline_result.get('cases') or []
    c_cases = candidate_result.get('cases') or []
    b_tids = {_tid(c) for c in b_cases}
    c_tids = {_tid(c) for c in c_cases}
    if not b_cases or not c_cases:
        return {'decision':'invalid','reason':'one side produced no cases',
                'baseline_collected':len(b_cases),'candidate_collected':len(c_cases)}
    if baseline_result.get('timed_out') or candidate_result.get('timed_out'):
        return {'decision':'invalid','reason':'baseline or candidate timed out'}
    new_failures = sorted(t for t in (b_tids & c_tids)
        if _status(c_cases, t) in ('failed','error')
        and _status(b_cases, t) not in ('failed','error'))
    recovered = sorted(t for t in (b_tids & c_tids)
        if _status(b_cases, t) in ('failed','error')
        and _status(c_cases, t) == 'passed')
    common_failures = sorted(t for t in (b_tids & c_tids)
        if _status(b_cases, t) in ('failed','error')
        and _status(c_cases, t) in ('failed','error'))
    missing = sorted(b_tids - c_tids)
    new_only = sorted(c_tids - b_tids)
    by_file = collections.Counter()
    for t in common_failures:
        by_file[t.split('::')[0]] += 1
    diff = {
        'new_failures':new_failures,
        'recovered':recovered,
        'common_failures':common_failures,
        'common_failure_count':len(common_failures),
        'common_failure_files':dict(by_file),
        'missing_in_candidate':missing,
        'new_only_in_candidate':new_only,
        'baseline_total':len(b_cases),
        'candidate_total':len(c_cases),
        'baseline_failing':baseline_result.get('failed_total'),
        'candidate_failing':candidate_result.get('failed_total'),
    }
    if new_failures:
        decision='rejected_new_regression'
    elif missing:
        decision='rejected_test_disappeared'
    elif recovered and not common_failures:
        decision='validated_promote'
    elif recovered:
        decision='validated_with_baseline_failures'
    else:
        decision='no_change'
    diff['decision']=decision
    return diff
