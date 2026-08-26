# 用户可观察性与领域报告架构

> **当前适用边界**：普通生产任务遵循 `manual_stable_core.md` 的“收到—每步开始/完成—最终结果”。
> 本文中的 Campaign 五阶段协议仅供暂停的自治实验使用，不能取代或旁路手动消息序列。

## 目标

让 deterministic fast path 保留可靠性和低成本，同时恢复 Partner 作为协作伙伴应有的过程透明度。该协议适用于所有 Campaign 直接事件，不依赖 LLM 自由发挥。

## 五阶段消息协议

每个非报告 WorkItem 强制产生三类真实 QQ 消息：

| 阶段 | 内容 | 发送时机 | 硬门 |
|---|---|---|---|
| `instruction_received` | 原样复述本轮业务指令、事件和安全边界 | 处理器执行前 | callback delivered |
| `started` | 沿用项目化“开始本轮执行”格式：项目、策略、承接目标和拟执行里程碑 | 处理器执行前 | callback delivered |
| `executed` | 沿用项目化“关键操作完成”格式：精简的实际事件或命令、机器指标和产物名称 | 处理器返回后 | callback delivered |
| `verified` | 项目化“结果核验完成”：机器验收、结果文件和 PDF 真实送达状态 | 文件推送后 | callback delivered |
| `finished` | 用户结论、下一动作或停止边界 | 验收回执后 | callback delivered |

三条回执写入 Task log 的 `campaign_progress_update`，并随 deterministic result 保存为 `progress_receipts`。`validate_progress_receipts` 缺任一阶段时，最终 `iteration_llm_check` 不得 satisfied。

Scout 使用 compact 文案，但仍保留五阶段，以便用户知道收到什么指令、何时开始检查、实际执行了什么、是否变化和为什么没有继续。禁止把每个内部函数调用都发 QQ；关键步骤消息应对应可核验的业务里程碑或实际命令，避免用内部噪声冒充过程。

呈现层不得直接暴露 `[portfolio_*]`、policy marker、Campaign 控制语句或冗长绝对路径。五阶段是回执合同，不是强制更换既有消息视觉风格；应保留各项目已验证的标题、语气和信息层级。

浏览器事件继续执行更强协议：每个声明 UI 步骤截图、视觉模型描述、图片送达和文字送达；五阶段 Campaign 消息不能替代这些逐步视觉回执。

## 领域化报告

报告生成分为两层：

1. 事件输出机器 JSON，完整保留参数、命令、指标和原始结果。
2. 领域 renderer 选择对用户有用的信息架构，生成 Markdown/PDF。

公共段落不得超过报告主体。原始 JSON 默认不整段嵌入 PDF，除非它很小且对判断不可替代。表格、图表和截图只在能解释关系时使用。

当前 renderer：

- `partner/v2/domain_reports.py`：01/03/04 continuous project 报告。
- TargetDiff 事件：各实验自己的方法/指标/不确定性报告。
- 05：轨迹、策略、Issue/Experiment/Promotion 报告。

PDF 统一提供视觉基础设施，而不是统一正文模板：封面标题、颜色层级、表格、代码块、页眉和页码由 `pdf_events.py` 负责；章节和结论由领域 renderer 负责。

## 消息与报告的关系

- 消息用于及时知道“正在做什么、做到了哪里、接下来怎样”。
- PDF 用于系统阅读与归档。
- JSON 用于机器复核与下一轮承接。
- EvidenceManifest 用于哈希、来源和持久路径。

四者不能互相替代。文件送达不能弥补缺少步骤消息；步骤消息也不能弥补报告空洞或机器证据缺失。

## 回归要求

- 普通任务消息必须包含五阶段；`user_progress_v2` 缺少任何阶段都不得完成。
- Scout 必须明确 no-change 不算创新。
- 不同实例的报告必须有不同领域章节。
- 报告不得重新退回共同的“目标与承接/真实结果/业务增量”套壳。
- PDF 必须通过 Unicode、正文、章节、证据和文件大小门。
- 实机 canary 必须核对 QQ history 与 Task log，而不只看测试 mock。
