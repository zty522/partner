"""Machine-owned isolated pytest runs shared by evolution and adoption gates.

A model can propose a patch and existing test names; it cannot supply results.
The receipt is bound to frozen inputs, test files, patch and actual subprocess.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from uuid import uuid4
from partner.runtime.action_execution import write_json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def root(workspace):
    p = Path(workspace).resolve()
    return p.parent.parent if p.parent.name == 'instances' else p


def inside(base, relative):
    p = (base / relative).resolve()
    if base not in p.parents or not p.is_file() or p.is_symlink():
        raise ValueError('input must be a real file inside the repository')
    return p


def isolate(workspace, candidate, repo=None):
    from partner.evolution.code_invention_lab import _apply_exact_unified_diff, _patch_paths
    repo = Path(repo or Path(__file__).resolve().parents[2]).resolve()
    patch = str(candidate.get('unified_diff') or '')
    targets = sorted(_patch_paths(patch))
    if not targets or len(targets) > 3 or any(not p.startswith('partner/') or p.startswith(('partner/governance/', 'partner/runtime/matched_execution')) for p in targets):
        raise ValueError('one to three Partner source targets required; evaluation/governance targets forbidden')
    tests = list(candidate.get('reproducer_tests') or [])
    regression = list(candidate.get('regression_tests') or [])
    if not tests or not regression or len(tests) + len(regression) > 20:
        raise ValueError('existing reproducer and separate regression tests required')
    if set(tests) & set(regression):
        raise ValueError('reproducer and regression selections must be distinct')
    hashes = {}
    for name in tests + regression:
        file = str(name).split('::', 1)[0]
        if not file.startswith('tests/') or not file.endswith('.py'):
            raise ValueError('only existing pytest tests can be executed')
        hashes[file] = file_hash(inside(repo, file))
    source_hashes = {p: file_hash(inside(repo, p)) for p in targets}
    directory = root(workspace) / 'state/event_runtime/isolated' / ('experiment_' + uuid4().hex)
    directory.mkdir(parents=True)
    # Snapshot working source, including uncommitted fixes; never reset/copy credentials.
    # (2026-09-16) Expand snapshot to cover docs/catalog fixtures and resources that
    # some tests assume live next to the package. Keep ignoring secrets and large
    # runtime artefacts.
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache', 'workspace',
                                    '.secrets', '*.token', '*.pem', '.env', 'credentials*')
    for kind in ('baseline', 'candidate'):
        copy = directory / kind
        copy.mkdir()
        for name in ('partner', 'tests', 'scripts', 'shells'):
            if (repo / name).is_dir():
                shutil.copytree(repo / name, copy / name, ignore=ignore)
        for name in ('pyproject.toml', 'pytest.ini', 'setup.cfg', 'conftest.py'):
            if (repo / name).is_file(): shutil.copy2(repo / name, copy / name)
        # Optional resources (docs, fixtures) — copy if they exist; absence is non-fatal.
        for name in ('docs', 'fixtures', 'data', 'catalog.yaml'):
            p_src = repo / name
            if p_src.is_dir():
                shutil.copytree(p_src, copy / name, ignore=ignore)
            elif p_src.is_file():
                shutil.copy2(p_src, copy / name)
    applied, reason = _apply_exact_unified_diff(directory / 'candidate', patch, targets)
    if not applied:
        raise ValueError('candidate patch not applied: ' + reason)
    expectations = list(candidate.get('expectations') or [])
    manifest = {'experiment_id': directory.name, 'created_at': time.time(), 'tests': tests,
                'regression_tests': regression, 'test_hashes': hashes, 'source_hashes': source_hashes,
                'expectations': expectations,
                'patch_sha256': hashlib.sha256(patch.encode()).hexdigest(), 'timeout': 120,
                'production_effective': False}
    write_json(directory / 'manifest.json', manifest)
    (directory / 'candidate.patch').write_text(patch)
    return {'directory': str(directory), 'manifest_sha256': file_hash(directory / 'manifest.json'), **manifest}


def load_manifest(workspace, experiment_id):
    if not experiment_id.startswith('experiment_') or not experiment_id.removeprefix('experiment_').isalnum():
        raise ValueError('invalid isolated experiment ID')
    directory = root(workspace) / 'state/event_runtime/isolated' / experiment_id
    manifest = json.loads((directory / 'manifest.json').read_text())
    return directory, manifest


def execute(workspace, experiment_id, kind):
    if kind not in {'baseline', 'candidate'}: raise ValueError('invalid run kind')
    directory, manifest = load_manifest(workspace, experiment_id)
    receipt = directory / (kind + '_receipt.json')
    if receipt.exists(): return verify_receipt(workspace, {'experiment_id':experiment_id, 'kind':kind})
    repo = directory / kind
    for p, expected in manifest['test_hashes'].items():
        if file_hash(inside(repo, p)) != expected: raise ValueError('frozen test changed')
    tests = manifest['tests'] + manifest['regression_tests']
    command = [sys.executable, '-m', 'pytest', '-q', *tests, '--tb=short', '--junitxml=' + str(directory / (kind + '.xml'))]
    start = time.time()
    # No application credentials are passed to isolated candidate processes.
    env = {k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','SYSTEMROOT'}}
    env.update(HOME=str(directory / 'home'), PYTHONPATH=str(repo), OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    with (directory / (kind + '.log')).open('w') as log:
        proc = subprocess.Popen(command, cwd=repo, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        timed_out = False
        try: code = proc.wait(timeout=manifest['timeout'])
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL); code = proc.wait(); timed_out = True
    import xml.etree.ElementTree as ET
    cases = []
    try:
        for item in ET.parse(directory / (kind + '.xml')).iter('testcase'):
            cases.append({'name':item.get('name'), 'class':item.get('classname'),
                          'failed':item.find('failure') is not None, 'error':item.find('error') is not None,
                          'skipped':item.find('skipped') is not None})
    except (OSError, ET.ParseError): pass
    row = {'experiment_id':experiment_id, 'kind':kind, 'executed':True, 'exit_code':code,
           'timed_out':timed_out, 'command':command, 'started_at':start, 'finished_at':time.time(),
           'manifest_sha256':file_hash(directory / 'manifest.json'), 'log_sha256':file_hash(directory / (kind + '.log')),
           'cases':cases, 'receipt_path':str(receipt), 'production_effective':False}
    write_json(receipt, row)
    return row


def verify_receipt(workspace, reference):
    directory, manifest = load_manifest(workspace, str(reference.get('experiment_id') or ''))
    kind = reference.get('kind')
    if kind not in {'baseline', 'candidate'}: raise ValueError('invalid receipt kind')
    row = json.loads((directory / (kind + '_receipt.json')).read_text())
    if row['manifest_sha256'] != file_hash(directory / 'manifest.json') or row['log_sha256'] != file_hash(directory / (kind + '.log')):
        raise ValueError('receipt artifact changed')
    return row


def compare(workspace, before, after):
    """Compare a baseline and candidate isolated run with per-dimension attribution.

    The result separates six signals (round 2026-09-17):
      a. target defect fixed   -> ``improved``
      b. frozen expectations   -> computed by ``expectation_compare`` upstream
      c. new regressions       -> ``breakdown.new_regressions`` (baseline pass -> candidate fail)
      d. existing blockers     -> ``breakdown.existing_blockers`` (both fail; not candidate-caused)
      e. execution validity    -> ``criteria_results.matched_tests``
      f. release gate          -> ``decision`` (improved AND no new regression)

    ``improved`` (target repaired) and ``regression_passed`` (no new regression)
    are DISTINCT fields.  An existing blocker can block release without being
    mislabelled as a candidate-introduced regression, and a candidate that
    repairs the target defect but adds a regression is ``improved=True`` yet
    ``regression_passed=False`` — never silently conflated.
    """
    try:
        b, a = verify_receipt(workspace, before), verify_receipt(workspace, after)
        if b['kind'] != 'baseline' or a['kind'] != 'candidate' or b['experiment_id'] != a['experiment_id']:
            raise ValueError('unmatched isolated executions')
        names = lambda r: {(c['class'], c['name']) for c in r['cases']}
        valid = bool(names(b) and names(b) == names(a) and not b['timed_out'] and not a['timed_out']
                     and not any(c['error'] or c['skipped'] for c in b['cases'] + a['cases']))
        _, manifest = load_manifest(workspace, b['experiment_id'])

        # Target vs non-regression split comes from frozen expectations' kind,
        # NOT from manifest['tests'] (which mixes reproducers and invariants).
        expectations = manifest.get('expectations') or []
        if expectations:
            target_test_names = {e.get('test_name') for e in expectations if e.get('kind') != 'non_regression'}
        else:
            # Legacy manifest without expectations: every reproducer test is a target.
            target_test_names = {str(t).split('::')[-1] for t in manifest.get('tests', [])}

        def cmap(r):
            return {(c['class'], c['name']): c for c in r['cases']}
        bm, am = cmap(b), cmap(a)
        all_names = set(bm) | set(am)

        def failed(m, n):
            c = m.get(n)
            return bool(c and c.get('failed'))

        # Reproduced: the target defect must actually fail in baseline.
        reproduced = any(failed(bm, n) for n in all_names if n[1] in target_test_names)

        target_fixed, target_regressed, new_regressions, existing_blockers = [], [], [], []
        for n in all_names:
            b_fail = failed(bm, n)
            a_fail = failed(am, n)
            is_target = n[1] in target_test_names
            if is_target:
                if b_fail and not a_fail:
                    target_fixed.append(n)
                elif not b_fail and a_fail:
                    target_regressed.append(n)
            else:
                # Non-regression invariant tests + regression_tests.
                if not b_fail and a_fail:
                    new_regressions.append(n)
                elif b_fail and a_fail:
                    existing_blockers.append(n)

        improved = bool(valid and reproduced and target_fixed and not target_regressed)
        regression_passed = not new_regressions
        decision = ('promoted' if (improved and regression_passed)
                    else 'rejected' if valid else 'inconclusive')

        def fmt(ns):
            return [f"{c}::{n}" for c, n in sorted(ns)]

        return {
            'decision': decision,
            'improved': improved,
            'regression_passed': regression_passed,
            'criteria_results': {
                'matched_tests': valid,
                'target_defect_fixed': improved,
                'no_new_regression': regression_passed,
            },
            'breakdown': {
                'target_fixed': fmt(target_fixed),
                'target_regressed': fmt(target_regressed),
                'new_regressions': fmt(new_regressions),
                'existing_blockers': fmt(existing_blockers),
            },
            'evidence_refs': [b['receipt_path'], a['receipt_path']],
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {'decision': 'inconclusive', 'improved': False, 'regression_passed': False,
                'criteria_results': {}, 'breakdown': {}, 'evidence_refs': []}

