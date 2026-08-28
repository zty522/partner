# ADR 0012：required_output_exts 否定句修饰过滤

**状态**: accepted
**日期**: 2026-08-27
**触发**: Bug #43 — 03 + 05 真启动跑通任务，但 framework 报 `expected_artifacts missing`

## 背景

2026-08-27 03 + 05 真启动完成真任务（03 自主找 bug 写出 `bug_autonomous_findings.md` 5190 B + push 到 QQ；
05 真启动读 4 个 holdout 文件写出 `review_05_holdout_findings.md` 2209 B + push 到 QQ）——用户确认
两个文件都收到。但两个任务都报 `expected_artifacts missing`。

## 根因

`partner/mind/executor.py:2417-2438` 的 `_required_output_exts` 用正则
`rf"{output_verb}.{{0,48}}[^\s\`'\"，、。；：:()（）]+\\.py"` 匹配"output_verb + .py"。

03 任务 user_message 里含：
```
如果找到了，**直接用 create_file 写 patch 文件**到 working_dir 下的
`proposed_fix.patch`——不要直接修改 batch_planner.py。
```

正则把 **"不要直接修改 batch_planner.py"** 当作 `(verb="修改", filename=batch_planner.py)`
的正向要求 → `_required_output_exts` 自动加 `.py` 到 required_exts →
`_align_expected_artifacts_with_required_exts` 给 expected_artifacts 加
`{"pattern": "*.py", "required": True}`。

但用户的语义是**否定句**——"不要修改 .py" 不是"要求生成 .py"。_align 把 `*.py`
required=True 加进去后，ArtifactValidator 看到 task working_dir 没 .py 文件 →整个 task 判
missing → framework 报 `expected_artifacts missing` ——尽管 `bug_autonomous_findings.md`
实际真存在。

同样的问题在05 任务、之前 04 holdout 1/5 等都触发——只要 user_message 出现"不要修改 X.py"
这种否定句，framework 就会自动加 `.py` required。

## 修复

`partner/mind/executor.py:2417-2438` 改写 output_verb + filename 匹配逻辑——
增加 `_positive_match(pattern)` 函数：先检查 verb 前 12 字符是否有 negation
(`不要|不需(?:要)?|无需|禁止|别|不生成|不输出|不制作|不直接`)；如果有，再检查 negation 后到
verb 之间是否有 clause_break (`但|而|并|又|且|然后|之后|随后|再|才|就`)——如果有，negation 属
于前一个 clause，不传染到当前 verb。

```python
negation_prefix = r"(?:不要|不需(?:要)?|无需|禁止|别|不生成|不输出|不制作|不直接)"
clause_break = r"(?:但|而|并|又|且|然后|之后|随后|再|才|就)"
def _positive_match(pattern: str) -> bool:
    for m in re.finditer(pattern, text, re.I):
        verb_start = m.start()
        preceding = text[max(0, verb_start - 12):verb_start]
        if not re.search(negation_prefix, preceding, re.I):
            return True
        neg_match = re.search(negation_prefix, preceding, re.I)
        between = preceding[neg_match.end():]
        if re.search(clause_break, between, re.I):
            return True
    return False
```

应用到 `py_output`、`json_output`、`md_output` 三种扩展名匹配。

## 验证

`tests/test_governance.py` 新增 3 个针对性测试：

1. `test_required_output_exts_skips_negated_py_match`：输入"不要直接修改 batch_planner.py"，
   断言 `.py` 不在 required_exts。
2. `test_required_output_exts_still_positive_for_real_output`：输入"请帮我生成 report.py 并运行测试"，
   断言 `.py` 在 required_exts（避免过度修复导致真正需要 .py 时漏掉）。
3. `test_required_output_exts_mix_clauses_with_break`：输入"不要改 partner 代码；只产出诊断报告 X.md"，
   断言 `.md` 在 required_exts（验证分句情况）。

## 实测结果

| user_message 摘要 | 修前 | 修后 |
|---|---|---|
| 生成 report.pdf | `.pdf` ✓ | `.pdf` ✓ |
| 不要直接修改 batch_planner.py | `.py` ❌ | 空集 ✓ |
| 生成 bug_autonomous_findings.md + 修改 batch_planner.py | `.md`, `.py` ✓ | `.md`, `.py` ✓ |
| 产出 bug_autonomous_findings.md | `.md` ✓ | `.md` ✓ |
| 不要改 partner 代码；只产出 X.md | `.md` ✓ | `.md` ✓ |
| 不要修改 harness.py 但请生成 Y.md（进阶 mix）| `.md` ❌ | `.md` ❌（未修，未影响核心场景）|

## 全量回归

`342 passed in 10.26s`（333 + 4 Bug #38 + 1 Bug #39 + 1 Bug #40 + 3 Bug #43 = 342，0 回归）

## 后果

- 03 + 05 后续任务（带否定句指令）不再被 framework 误判 `expected_artifacts missing`
- "不要修改 X.py" 类 instruction 不会被 framework 误读为"要求生成 X.py"
- 用户在 user_message 里加否定句不再隐式产生 required_exts 副作用

## 不重做

- 未改 `_required_output_exts` 其他分支（pdf / csv / xlsx 等），Bug #43 仅触发于 verb + filename 模式
- 未在 planner prompt 加"避免用否定句描述 .py 文件"约束——LLM 用否定句是合理表达方式
- 未改 ArtifactValidator 的"missing 即 fail"行为——bug 是上游把不该 required 的 ext 加进了 required_exts

## 与 Bug #41 + #42 的关系

- Bug #41 + #42 在生成 + 推送链路上的修复——让 04 跑出真 holdout 报告
- Bug #43 在**期望契约**链路上的修复——让 framework 不再误判用户表达
- 三个 fix 共同保证 03 + 05 + 04 在 manual_stable 模式下能稳定交付真产物
