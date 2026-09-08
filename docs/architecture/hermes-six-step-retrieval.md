# ADR 0062 附录：从 hermes_external_learning 借鉴的 6 步检索流程

> **partner_test/hermes_external_learning/process.md §2** 描述了 hermes 抓取的完整流程。本附录将该
> 流程落地到 partner 工具链。

## 6 步 → partner 实现

| hermes step | 工具 | partner 引用 |
|---|---|---|
| 1. 列表侦察（list） | `partner.knowledge.searcher`（已存在，自带 multi-backend 降级：S2 / Crossref / PubMed / ArXiv） | 学术面覆盖完整 |
| 2. 主题筛选（filter） | `_category_for_topic()` in `partner.learn.learn_from_hermes` | 已有 |
| 3. 深抓详情（deep fetch） | `partner.governance.external_retrieval.fetch(workspace, url)` (ADR 0062 新增) | 12h 缓存 + arxiv ID 直取 + 任意 URL |
| 4. 消化笔记（summarize） | `learn_from_hermes._parse_note()` regex + 后续 LLM 摘要 | 已有 regex 解析 |
| 5. 跨主题连接 | `notes/connections.md` 5 大金矿映射 | 已被 ADR 0023-0037 接入；partner 没有运行时的连接映射器——记入未来选项 |
| 6. 候选产物（skill draft） | `register_candidate_skill(..., status="candidate", production_effective=False)` | 已有 |

## 关键差异

- hermes: 手工 trigger，每次重抓约 5-30 KB
- partner: 自动 driven by `handle_terminal` 失败路径。Receipt 的 `unresolved_questions` 字段
  作为 主题种子，调 `external_retrieval.harvest_for_topic`。
- hermes 缓存命中：`source/_seen.json` 去重；partner 缓存：`share/external_cache/<source>/<sha>.json` 12h TTL。

## 没做的事

- **不**让 partner 试图重新实现 `process.md` 的 source/_seen.json——partner 主
  用于主动学习，不是离线索引。Partner 与 hermes 互补，不替代。
- **不**让 partner 试图重新跑 arxiv/github 列表侦察——`searcher.search()` 已
  支持 4 个后端。重复实现只会引出不一致。
- 5 大金矿跨主题连接 = 长期研究备份，partner 暂不重写。
