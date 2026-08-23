# 自进化闭环设计

> ⚠️ **本文档已过时** — OODA 引擎于 2026-08-12 被删除。
> 替代方案: `partner/mind/research_loop.py`（详见 `sprint8_设计.md`）

---

## 旧 OODA 设计（已废弃）

```
每 10 分钟 → 读取 self_awareness.md → 生成修复 → 应用 → 验证 → 记录
```

### 废弃原因
1. desktop_inbox 注入与正常消息流冲突，造成重复
2. CircuitBreaker 复杂但仍有漏洞（反复复活 waiting 项目）
3. 超时文本洪水（"这一轮后台执行超过单步时间限制"）

### 新方案: Research Loop

| | 旧 OODA | 新 Research Loop |
|------|------|------|
| 注入方式 | desktop_inbox | `_event_queue.put()` 直接 |
| 触发时机 | 定时器 60s | task 完成回调 |
| 消息流冲突 | ✅ 冲突 | ❌ 不经过 inbox |
| 质量门控 | 无 | 5 轮上限 + 多样性 + 产出验证 |
| 代码量 | ~1100 行 | ~190 行 |

---

## 如需重新引入 OODA 风格的自进化

参考 `evolution/` 目录下保留的模块:
- `self_heal.py` — 步骤失败诊断
- `tree_search.py` — ERA 风格树搜索修复
- `auto_fixer.py` — LLM 增量 patch

但这些需要在 Research Loop 框架内重新集成。

---

*最后更新: 2026-08-12 — 标记过时*
