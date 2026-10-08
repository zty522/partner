# e2e_fix_rules —— 自动修复规则注册表

`e2e_fix_loop.py` 在端到端回归失败时，用本目录下的规则匹配失败签名，
命中则自动：备份 → 打补丁 → 跑聚焦测试 → 重跑 e2e → 失败则回滚。

## 规则 JSON 格式

```json
{
  "id": "verify_business_delta",
  "match_errors": ["verify not green", "business_delta"],
  "patch": {
    "file": "partner/events/project.py",
    "old": "……要替换的原文（必须精确匹配）……",
    "new": "……替换后的新文……"
  },
  "tests": ["benchmark/runtime/test_closed_loop_effect_contract.py"],
  "summary": "一句话说明修什么"
}
```

字段说明：
- `id`：规则唯一标识（也是备份文件名后缀）。
- `match_errors`：OR 匹配。任一子串出现在 e2e 报告 `errors` 里即命中；可空（仅当 `patch` 为 null 的诊断规则）。
- `patch`：单文件单处替换。`old` 必须精确匹配（锚点），替换只发生一次。
- `tests`：应用补丁后必须先跑过的聚焦测试（pytest 路径列表）；全过才允许重跑 e2e。
- `patch` 为 null 时该规则只做诊断（不自动改代码）。

## 纪律

- 每条规则必须能通过聚焦测试 + 一次完整 e2e 验证，否则回滚（备份在 `scripts/runtime/fix_backups/`）。
- 新失败模式先手动修好并回归通过后，再把"失败签名 → 补丁 → 测试"沉淀成规则，避免把未验证的补丁写进注册表。
- 规则补丁禁止改文档、禁止绕过测试。
