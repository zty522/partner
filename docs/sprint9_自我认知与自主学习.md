# Sprint 9: 自我认知与自主学习

**时间**: 2026-08-21 ~
**目标**: Partner 从"能跑"（Sprint 8）升级为"先想清楚再做、知道自己几斤几两、能自己学着长本事"

---

## 一、背景与定位

Sprint 8 完成了"从能跑到有用"：Research Loop 自主循环、深度研究闭环（真实运行外部代码）、QQ 消息可见性。

Sprint 9 的核心是给 Partner 装上"自我认知"的地基，为自主学习铺路：

- 接任务前，先盘点自己会什么、不会什么、需要先学什么（能力清单）
- 动手前，先写软件项目式总设计文档，再照设计执行
- 有了这两块，才能谈"自主学习做东西"

---

## 二、已实现（2026-08-21）

### 能力 A：强制写总设计（write_design 事件）

- 每个任务执行前，BatchPlanner 自动在计划最前注入 write_design 步骤
- 后续步骤都依赖它（真正的"先设计后执行"）
- 产出：`shared_projects/<项目>/design.md`（目标 / 模块 / 方案 / 步骤 / 依赖 / 验收）
- 开关：`config/batch_planner.yaml` 的 `force_design`（默认 true）

### 能力 B：能力盘点（capability_inventory 事件）

- 复用 SelfReview 盘点能力（Agent / 技能 / 事件 / 历史经验）
- 产出并持续更新：`partner_data/capabilities.md`（共享，5 实例同读）
- 三栏结构：会什么 / 不会什么（缺口）/ 需学什么（学习计划）

### 顺带修复

- self_review.py 事件数统计 bug（引用了不存在的模块 → 0，改为 default_registry → 102）
- self_review.py 技能数统计 bug（SkillRegistry 不读 db → 0，改为查 skills_registry.db → 9）

### 验证（实机）

- 端到端：step_design 31.7s 生成 design.md，后续步骤 depends_on 含 step_design
- capabilities.md 统计：agents=13 / skills=9 / events=102 / gaps=19

---

## 三、待做

| 任务 | 优先级 | 状态 |
|------|--------|:--:|
| 自主学习闭环：Partner 基于能力清单自主决定学什么、做什么 | P0 | ⏳ 待做 |
| 能力清单自动更新：任务完成后自动回填（当前需手动/事件触发 capability_inventory） | P1 | ⏳ 待做 |
| write_design 引用能力清单 | P1 | ✅ 已实现（write_design 自动读 capabilities.md） |
| 设计文档执行对照：执行完后检查是否照设计走、记录偏差 | P2 | ⏳ 待做 |

---

## 四、关键文件

| 文件 | 作用 |
|------|------|
| `partner/v2/capability_events.py` | 两个新事件的实现 |
| `partner/planner/batch_planner.py` | force_design 开关 + 注入 design 步骤 |
| `partner/evolution/self_review.py` | 能力盘点（修复 2 个统计 bug） |

---

*创建: 2026-08-21*
