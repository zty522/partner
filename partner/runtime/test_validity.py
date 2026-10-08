"""Fail closed on broken generated tests before judging a candidate."""
import hashlib
import json
import xml.etree.ElementTree as ET


def classify_preflight(receipt, plan):
    value = dict(receipt)
    value.setdefault('kind_classifications', [])
    value.setdefault('summary', {k: 0 for k in
        ('target_failure', 'no_import', 'timeout', 'ok', 'skipped', 'test_invalid', 'inconclusive')})
    value['plan_sha256'] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    value.update(valid=False, classification='execution_incomplete')
    if not receipt.get('executed') or receipt.get('exit_code') not in (0, 1):
        return value
    try:
        cases = list(ET.parse(receipt['junit_path']).iter('testcase'))
    except (KeyError, OSError, ET.ParseError):
        return value
    expected = set(plan.get('reproducer_names') or [])
    names = {c.get('name') for c in cases}
    if not expected or not expected.issubset(names):
        return value
    expectations = {
        str(row.get('test_name')): str(row.get('kind'))
        for row in plan.get('expectations', [])
    }
    for case in cases:
        failure = case.find('failure')
        error = case.find('error')
        if error is not None:
            category = 'no_import'
        elif case.find('skipped') is not None:
            category = 'skipped'
        elif failure is None:
            category = 'ok'
        else:
            detail = (failure.get('message', '') + '\n' + (failure.text or ''))
            infrastructure = any(x in detail for x in (
                'AttributeError', 'ImportError', 'ModuleNotFoundError', 'NameError',
                'TypeError', 'fixture ', 'unittest/mock.py'))
            behavioral = ('AssertionError' in detail or 'assert ' in detail
                          or 'Failed:' in detail)
            category = ('target_failure'
                        if expectations.get(str(case.get('name'))) == 'repair'
                        and behavioral and not infrastructure else 'inconclusive')
        value['kind_classifications'].append({'test': case.get('name'), 'kind': category})
        value['summary'][category] += 1
        if case.find('error') is not None or case.find('skipped') is not None:
            value['classification'] = 'test_invalid'
            return value
        if failure is not None:
            detail = (failure.get('message', '') + '\n' + (failure.text or ''))
            if any(x in detail for x in ('AttributeError', 'ImportError', 'ModuleNotFoundError',
                                          'NameError', 'TypeError', 'fixture ', 'unittest/mock.py')):
                value['classification'] = 'test_invalid'
                return value
            if 'AssertionError' not in detail and 'assert ' not in detail and 'Failed:' not in detail:
                value['classification'] = 'inconclusive'
                return value
            # A preservation test must already pass in the baseline.
            if any(e.get('test_name') == case.get('name') and e.get('kind') == 'non_regression'
                   for e in plan.get('expectations', [])):
                value['classification'] = 'baseline_contract_unmet'
                return value
    # Hard gate: a declared repair test must fail on the unmodified baseline
    # (red baseline).  If it passes there too, it never detects the defect and
    # the whole experiment is invalid -- demand a rewrite instead of letting
    # the candidate be judged against a non-discriminating test.
    repair_green = [
        str(case.get('name')) for case in cases
        if expectations.get(str(case.get('name'))) == 'repair'
        and case.find('failure') is None and case.find('error') is None
    ]
    if repair_green:
        value.update(valid=False, classification='repair_red_baseline_missing',
                     repair_green_tests=repair_green,
                     repair_feedback=(
                         'repair 测试在未修改基线上通过，未复现待修缺陷，测试无判别力；'
                         '必须重写测试：先构造能稳定触发原缺陷的最小用例，使其在基线上失败。'))
        return value
    value.update(valid=True, classification='behavioral_review_required')
    return value
