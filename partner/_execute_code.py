import sys; sys.path.insert(0, '/mnt/e/work/partner')
path = '/mnt/e/work/partner/partner/planner/batch_planner.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()
old = '''    truth_quote_required = bool(
       truth_policy_active
       and re.search(r"evidence_quote|逐字(?:连续)?(?:摘录|引文|引用)", str(user_message or ""), re.I)
   )'''
new = '''    truth_quote_required = bool(
       truth_policy_active
       and re.search(
           r"evidence_quote|逐字(?:连续)?(?:摘录|引文|引用)|source_path|source证据|真值引用|真值审计|truth_quote|truth_audit|真值|逐字|原文|原话",
           str(user_message or ""),
           re.I,
       )
   )'''
assert old in content, 'old block not found'
with open(path, 'w', encoding='utf-8') as f:
    f.write(content.replace(old, new, 1))
print('PATCHED')