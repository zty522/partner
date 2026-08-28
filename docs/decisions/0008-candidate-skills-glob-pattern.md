# ADR 0008：candidate_skills.load_candidate_skills glob pattern 修复

**状态**: accepted
**日期**: 2026-08-27
**触发**: Bug #39 — 任务 2/3（03 单文件修复）发现

## 背景

2026-08-27 推进03 实例任务 2/3 时，在 `partner/governance/candidate_skills.py` 中
发现一个隐蔽的"成功注册但加载不到"bug：

```python
# partner/governance/candidate_skills.py:75 (Bug #39)
def load_candidate_skills(workspace: str) -> list[dict[str, Any]]:
    root = workspace_root(workspace) / "share/mind/governance/rl/candidate_skills"
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("candidate_*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows
```

`register_candidate_skill` 函数（同一文件）按 `safe_id(candidate_id)` 生成文件名——**`candidate_id` 不强制以 `candidate_` 开头**。
但 `load_candidate_skills` 用 `glob("candidate_*.json")`——**只匹配 `candidate_` 前缀的文件**。

## 真实影响

- Codex 8/27 canary 留下的两个 candidate skill（`candidate_preflight_aware_planning_v1`,
  `candidate_preflight_contract_v2`）都恰好以 `candidate_` 开头——**碰巧命中 glob**
- 但任何用户调用 `register_candidate_skill(ws, {"candidate_id": "manual_stable_truth_audit_v2", ...})` 时
  （这是 Codex 8/26 真用过的 ID，参见 `docs/decisions/0004-manual-stable-production-baseline.md`
  与 `docs/architecture/rl_evolution.md` 的"promoted"字段）——文件会写入 disk
  （`manual_stable_truth_audit_v2.json`），但 `load_candidate_skills` **不会找到它**
- 结果：`control_policy.json` 的 `promoted["literature_github_learning:manual_final_artifact_truth"]` 值即使
  与磁盘文件名匹配，**`load_candidate_skills` 仍然返回不完整列表**，造成所有依赖它做 consistency check 的
  shadow / canary 评估器（`evaluate_isolated_preflight_canary`、`evaluate_preflight_shadow` 等）**对真实 promoted 策略视而不见**

## 实证

测试环境实测（2026-08-27 17:24 UTC+8）：

```python
register_candidate_skill(ws, {"candidate_id": "my-custom-candidate-id", ...})
# → 写入 /mnt/e/work/partner_workspace/share/mind/governance/rl/candidate_skills/my-custom-candidate-id.json
# → 文件确实存在，size = 真实 candidate JSON

load_candidate_skills(ws)
# → 返回 2 条，全是 Codex 8/27 的 candidate_preflight_* 记录
# → my-custom-candidate-id 完全不见
# → 静默丢失——没有任何 error / warning / log
```

## 修复

```python
def load_candidate_skills(workspace: str) -> list[dict[str, Any]]:
    root = workspace_root(workspace) / "share/mind/governance/rl/candidate_skills"
    rows: list[dict[str, Any]] = []
    # Hermes 2026-08-27 fix: glob all candidate-skill files, not just the
    # `candidate_*.json` pattern. The directory holds one file per
    # `register_candidate_skill` call keyed by `safe_id(candidate_id)`,
    # and that prefix is not guaranteed to start with `candidate_`.
    for path in sorted(root.glob("*.json")):
        if path.name == "revisions.jsonl":
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows
```

`revisions.jsonl` 是 append-only 审计 log——虽然 `.jsonl` 不匹配 `*.json` glob，但加
防御性 `if path.name == "revisions.jsonl": continue` 兜底任何未来扩展。

## 回归测试

`tests/test_governance.py` 新增两个测试：
- `test_load_candidate_skills_returns_non_default_candidate_ids` — 同时注册
  `candidate_default_pattern` 和 `my-custom-candidate-id`，断言两者都能被加载
- `test_load_candidate_skills_ignores_corrupt_and_revisions_jsonl` — 验证损坏 JSON
  被跳过且 `revisions.jsonl` 不被错误解析

## 后果

1. **修复后**：`load_candidate_skills` 与 `register_candidate_skill` 行为一致——任何
   `candidate_id`（包括 Codex 8/26 用的 `manual_stable_truth_audit_v2`、8/27 的
   `candidate_preflight_*`、用户自定义的 `my-*`）都能被加载
2. **修复不影响现有行为**：Codex 8/27 留下的两个 candidate skill 仍能被正确加载
3. **未做扩大化改动**：
   - 没有修改 `register_candidate_skill`（它的行为一直正确——`safe_id(candidate_id)` 不强制前缀）
   - 没有修改 candidate_id 的命名约定
   - 没有改 `evaluate_isolated_preflight_canary` / `evaluate_preflight_shadow` 等
     依赖 `load_candidate_skills` 的 evaluator——它们自身行为正确，只是被卡死的 input 误导
4. **污染清理**：bug 验证过程中我注册了一个 `my-custom-candidate-id` 测试用 candidate，
   `.json` 文件已删除（disk cleanup），但 `revisions.jsonl` append-only log 保留那条记录作为实证
5. **全量回归**：337 → 339 passed（+ 2 个新测试，0 回归）

## 不重做

- 没有改 `control_policy.json` 的 `promoted` 字段（Codex 8/26 已经正确写入）
- 没有为 `register_candidate_skill` 加额外校验（保持 backwards-compatible）
- 没有改 `shadow_replay.py` 的 evaluator 路径（它们没 bug，是 input 被卡）

## 与 Bug #38 的关系

Bug #38（preflight 占位判定）修复了 **执行**路径——让 partner 框架能正确
跑带 `${step_id.result.field}` 引用的 plan。Bug #39 是 **governance 评估**路径
上的——让 `load_candidate_skills` 能正确读取所有 candidate_skill。两个 fix
都是 partner 框架"非预期"行为与实际 Codex 8/27 canary 配套改动之间的落差。
