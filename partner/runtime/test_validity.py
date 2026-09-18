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
    for case in cases:
        category = ('no_import' if case.find('error') is not None else
                    'skipped' if case.find('skipped') is not None else
                    'inconclusive' if case.find('failure') is not None else 'ok')
        value['kind_classifications'].append({'test': case.get('name'), 'kind': category})
        value['summary'][category] += 1
        if case.find('error') is not None or case.find('skipped') is not None:
            value['classification'] = 'test_invalid'
            return value
        failure = case.find('failure')
        if failure is not None:
            detail = (failure.get('message', '') + '\n' + (failure.text or ''))
            if any(x in detail for x in ('AttributeError', 'ImportError', 'ModuleNotFoundError',
                                          'NameError', 'TypeError', 'fixture ', 'unittest/mock.py')):
                value['classification'] = 'test_invalid'
                return value
            if 'AssertionError' not in detail and 'assert ' not in detail:
                value['classification'] = 'inconclusive'
                return value
            # A preservation test must already pass in the baseline.
            if any(e.get('test_name') == case.get('name') and e.get('kind') == 'non_regression'
                   for e in plan.get('expectations', [])):
                value['classification'] = 'baseline_contract_unmet'
                return value
    value.update(valid=True, classification='behavioral_review_required')
    return value
