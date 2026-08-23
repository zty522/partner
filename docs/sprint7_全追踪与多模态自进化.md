# Sprint 7: 全过程追踪与多模态自进化

**时间**: 2026-08-07 ~
**目标**: 自进化从"被动修复"升级为"主动追踪+外部感知+多模态操作"，多实例并行

---

## 一、Sprint 6 成果继承

Sprint 6 建立了自进化和自愈的基础框架：

| 已具备 | 文件 | 状态 |
|--------|------|------|
| OODA v4 自主循环 | `ooda_engine.py` | ✅ 断路器 + LLM 计划 |
| 自愈 Skill Bank | `self_heal.py` | ✅ SQLite + SESA 风格 |
| 树搜索修复 | `tree_search.py` | ✅ ERA 风格多分支 |
| execute_code 体系 | `harness.py` | ✅ 替代 agent call |
| 5 实例 QQ 配置 | `instances/01-05/config/` | ✅ |
| 文档体系 | `docs/` (12 文件) | ✅ |

**Sprint 6 的不足**（Sprint 7 要解决）：
- 自愈只在 failure 时触发，缺少主动巡检
- 无法操作桌面/浏览器，感知范围受限
- 外部知识获取仅靠 web_search，不读 PDF/网页
- OODA cycle() 返回 None 导致任务断档
- 只有 03 在工作，01/02/04/05 闲置

---

## 二、Sprint 7 目标

### 方向 1：全过程主动追踪自进化

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 主动巡检 | 定时检查运行状态、skill_bank 健康度、日志异常 | P0 |
| 执行过程记录 | 每步执行时写 `state/execution_trace.jsonl` | P0 |
| 过程截图 | 关键步骤自动截图保存 | P1 |
| 趋势分析 | 统计成功率趋势、常见失败模式变化 | P1 |
| 主动优化建议 | 基于趋势提出 prompt/config 优化建议 | P2 |

**代码位置**: `partner/evolution/active_monitor.py` (新建)

### 方向 2：外部内容深度获取

| 功能 | 说明 | 优先级 |
|------|------|--------|
| PDF 论文阅读 | 用 fitz/PyMuPDF 提取论文全文 | P0 |
| 网页内容抓取 | bs4 + requests 抓取 arXiv/GitHub Pages | P0 |
| external/ 索引 | 自动扫描 external/ 目录并索引可用工具 | P1 |
| 内容消化→假设 | 读取内容 → 提取方法 → 生成研究假设 | P1 |

**代码位置**: 
- `partner/v2/multimodal.py` 增强（现有基础）
- `partner/evolution/content_digest.py` (新建)

### 方向 3：本地电脑操作（win-gui-test-skill 嵌入）

**来源**: `/mnt/e/work/partner_workspace/external/code/win-gui-test-skill`

| 功能 | 现有能力 | Sprint 7 增强 |
|------|---------|-------------|
| 窗口列表 | list-all ✅ | Partner event: `win_list_windows` |
| 控件探测 | list-elements ✅ | Partner event: `win_list_elements` |
| 截图 | screenshot ✅ | Partner event: `win_screenshot` + 自动保存 |
| 点击 | click ✅ | Partner event: `win_click` |
| 键盘输入 | sendkeys ✅ | Partner event: `win_sendkeys` |
| 滚动 | scroll ✅ | Partner event: `win_scroll` |
| 启动程序 | launch ✅ | Partner event: `win_launch` |
| 视觉分析 | analyze ✅ | Partner event: `win_analyze` + OCR |
| **新增** | — | 定时后台截图巡检 |
| **新增** | — | 截图变化检测（感知桌面活动） |
| **新增** | — | 浏览器窗口操作（Edge/Chrome） |

**嵌入方式**:
1. 将 win-gui-test-skill 核心代码复制到 `partner/v2/win_gui/`
2. 通过 PowerShell 桥接调用 Windows Python（pywinauto）
3. 注册为 Harness event，batch_plan 可直接调用
4. 增强：自动重试、超时处理、结果结构化

### 方向 4：后台截图与桌面感知

| 功能 | 说明 |
|------|------|
| 定时截图 | 每 N 分钟自动截取桌面 |
| 变化检测 | 对比前后截图，检测窗口弹出/内容变化 |
| 事件触发 | 检测到变化 → 触发分析或通知 |
| 截图归档 | 按时间组织，供后续分析 |

**代码位置**: `partner/v2/perception.py` 增强 + `partner/v2/win_gui/`

### 方向 5：网页读取与操作

| 功能 | 说明 |
|------|------|
| 浏览器自动化 | Playwright 打开/点击/提取/截图（已有基础） |
| arXiv 论文抓取 | 自动搜索 arXiv → 下载 PDF → 阅读 |
| GitHub 代码浏览 | 浏览 repo 结构 → 读取关键文件 → 借鉴 |
| 网页表单操作 | 填写/提交网页表单 |

**代码位置**: `partner/v2/browser.py` 增强

---

## 三、多实例分工

| 实例 | QQ App ID | Sprint 7 任务 | 模式 |
|------|-----------|-------------|------|
| **01** | 1904072984 | 桌面监控与操作 — win_gui 模块测试，后台截图巡检 | 感知 + 操作 |
| **02** | 1904082527 | 外部内容获取 — 论文爬取、网页阅读、external/ 索引 | 知识获取 |
| **03** | 1904095253 | 分子生成技术创新探索（继续 Sprint 6） | 深度研究 |
| **04** | 1904095257 | 浏览器自动化 — Playwright 网页操作、arXiv 浏览 | 浏览器 |
| **05** | 1904110644 | 工具测试 — wrapper 逐个测试、错误记录、ETA 报告 | 工具测试 |

---

## 四、代码变更计划

### 新建文件

```
partner/evolution/
├── active_monitor.py          ← 主动巡检引擎（定时检查 + 趋势分析）
└── content_digest.py          ← 外部内容消化（论文→假设）

partner/v2/
└── win_gui/                   ← win-gui-test-skill 嵌入
    ├── __init__.py
    ├── bridge.py              ← PowerShell 桥接层
    ├── core.py                ← 窗口操作核心（移植自 win-gui-test-skill）
    ├── screenshot.py          ← 截图 + 变化检测
    └── cli.py                 ← CLI 入口

partner/adapters/
└── pdf_reader.py              ← PDF 论文阅读器（fitz）

instances/
├── 01/state/                  ← 新建 state 目录
├── 02/state/                  ← 新建 state 目录
└── 04/state/                  ← 新建 state 目录
```

### 修改文件

| 文件 | 改动 |
|------|------|
| `harness.py` | 注册 win_gui 事件 |
| `v2/__init__.py` | 导入 win_gui 模块 |
| `v2/perception.py` | 增加定时截图 + 变化检测 |
| `v2/browser.py` | 增加 arXiv/GitHub 专用方法 |
| `v2/multimodal.py` | 增加 PDF 内容提取 |
| `ooda_engine.py` | 修复 cycle() 返回 None 问题 |
| `evolution/self_heal.py` | 读取 evolution_journal.md 增强自我认知 |

---

## 五、验收标准

| 标准 | 验证方式 |
|------|---------|
| 主动巡检每 5 分钟运行一次 | 日志中出现 `[ACTIVE_MONITOR]` |
| win-gui-test-skill 可通过 batch_plan 调用 | `win_screenshot "Partner"` 成功截图 |
| 论文 PDF 可被读取并提取方法 | 读取 Self-Play.pdf 提取出 SESA 方法描述 |
| 01-05 五个实例全部在 QQ 上活跃 | 每个实例 QQ Bot ready |
| 03 继续产出分子生成研究报告 | method_comparison 类报告 |
| 自愈/自进化触发时自动更新 docs/ | evolution_journal.md 有新条目 |

---

*创建: 2026-08-06*
*状态: 规划阶段*

    
## Sprint 7 进展日志 (2026-08-07 16:32)

### 已完成模块
1. pdf_reader.py — 读论文/提取方法/搜索PDF (已测试: SP140 13页, PocketFlow 方法提取)
2. screen_monitor.py — 截图对比/变化检测 (2秒间隔 hash 对比验证)
3. content_digest.py — 论文→研究假设 (已消化 literature/ 2篇论文)
4. web_scraper.py — GitHub README获取 (已验证: TargetDiff 10,955字符)
5. direct_ops.py — screenshots + window list (261KB PNG验证)
6. file_ops.py — 文件搜索 + git clone + conda管理
7. auto_continue_daemon.py — 120s自动续任务

### 基础修复
- QQ消息显示产出文件名 (deliverable: overview.png(662KB) README.md(10KB))
- "需补齐"死循环修复 (expected_artifacts宽松)
- executor.py/harness.py 零修改 (通过独立模块规避)
- 5实例并行 (daemon自动续任务)

### 待完成
- win_gui 剩余6种操作 (click, sendkeys, scroll, launch, analyze, OCR)
- 浏览器自动化 (Playwright)
- OODA auto-continue
- 趋势分析 + 优化建议


### 最终状态 (2026-08-07 16:54)

11 modules, 265 steps, 0 failures, 100% rate.
方向5 网页操作: 50% (browser ✅)
Sprint 7: 55% complete
