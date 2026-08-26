# Sprint 11：执行型持续迭代与证据学习

**状态**：进行中  
**开始日期**：2026-08-23  
**目标**：把已通过的 Campaign/RL 审计闭环升级为真实项目推进闭环。

## 一、为什么需要 Sprint 11

上一阶段已经证明双槽调度、任务恢复、截图/视觉回执、QQ 交付、离线轨迹和预算收口能够工作。
但审计、索引和测试本身不等于项目持续推进。新阶段的基本单位必须是：

```text
真实输入 → 写入可复跑代码 → 子进程实际执行 → 机器结果 → 分析 → 下一波 → RL/Experiment 决策
```

只生成 Markdown、只列文件、只写“下一步”、只重复上一轮 audit，均不计为 Sprint 11 进展。

## 二、Execution Profile

启动参数：`partner_campaign.py start --profile execution --waves 2`。

| 实例 | Wave 1 | Wave 2 | 安全边界 |
|---|---|---|---|
| 01 | 编写/运行 content inbox 索引器 | 重复来源、open 项和候选 brief 分析 | 不上传、不发布，brief 标记未授权 |
| 02 | 读取 TargetDiff `affinity_info.pkl` 建立 184087 条记录基线 | 对有 pK 记录做 Vina 分层比较 | 相关性不写成药效因果 |
| 03 | 编写/运行 Campaign 指标分析器 | 历史回放、clean rate 与异常对比 | 原型不自动合并生产 |
| 04 | 真实 shallow clone/fetch SESA | AST/Skill Bank 适配契约 | 不安装或运行 GPU 训练栈 |
| 05 | 等待 01–04 全终态，补齐轨迹并运行候选评估器 | 本 Sprint 只执行一次汇总决策 | 样本不足必须 inconclusive |

每个 WorkItem 固定产出 Python 源码、命令/退出码、JSON、Markdown、详细 PDF 和真实 QQ 回执。
01–04 默认两波，共 8 个执行任务；05 最后汇总，共 9 个业务 WorkItem。

## 三、持续运行合同

1. 同实例 Wave 2 只能在 Wave 1 终态后获得槽位；不同实例最多并发两个。
2. 05 对本 Campaign 的所有 01–04 非报告 WorkItem 有硬依赖，不能学习半轮结果。
3. `evidence_execution_slice` 是有界事件，结束后不回退泛化 planner；下一波来自预声明 profile。
4. 报告间隔与最终日报计入总 WorkItem 预算；截止前执行 RL final sync。
5. 外部拉取失败时保留 exit code/stderr，并可使用已有只读 archive 分析，但不能把 fallback 写成 clone 成功。

## 四、验收标准

- 两波的源码和 JSON 内容不同，并且退出码均为 0。
- 02 必须真正读取 TargetDiff pickle，而不是重新做 QED/SA 排序。
- 04 必须留下 git 命令、退出码和所分析 commit/目录证据。
- 05 必须生成 PromotionDecision；若样本门不够，正确结果是 `inconclusive`。
- 9 个主 WorkItem 的产物率和 QQ 送达率均为 100%，failure/retry 在预算内。
- 最终报告明确区分业务完成、证据型 blocked、报告链和自进化决策。
- 新增代码完整 pytest 通过后才启动 2 小时 Campaign。

## 五、本轮实现记录

- 新增 `partner/v2/execution_iteration_events.py`：五实例执行型事件与生成脚本。
- 新增 `seed_execution_work()` 和 CLI `--profile execution --waves N`。
- `evidence_execution_slice` 纳入有界事件集合；05 纳入整轮依赖门。
- 新增隔离测试，验证 content/TargetDiff 脚本真实运行、两波排队和 05 不抢跑。
- 01/02/03 已用真实工作区输入做事件 smoke：均生成详细 PDF，代码退出码为 0。
- 完整 pytest：162 passed。

## 六、当前运行

2 小时 execution Campaign 已于 18:21 启动：`campaign_cf78d794f832`，截止 20:21。

- 初始两波与 05 汇总共 9/9 completed；追加证据驱动第三波和第二次 05 决策后为 14/14 completed。
- failure=0、retry=0；所有业务 WorkItem 均有 `.py + JSON + Markdown + PDF` 和真实 QQ callback。
- 02 实际读取 184087 条 TargetDiff affinity 记录，其中 76803 条 pK>0；Wave 3 固定拆分
  61442/15361，Vina→pK 线性基线测试 RMSE=1.57176，只作为非因果 baseline。
- 04 shallow clone SESA 成功，commit=`74de5d77a19774cfba53d6950d47633a2d632430`；后续两次 fetch 成功，
  Wave 3 用 Partner Issue 生成 10 张适配样例卡，未修改外部仓库、未启动训练。
- 03 从历史 Campaign 得到 6 次预算越界样本、21 个泛化非报告动作，并形成三个可执行治理门候选。
- 05 两次都在前序波次终态后运行，分别新增 8 和 5 条轨迹；对
  `experiment_71d70766ba21` 两次写出 `inconclusive`，原因是 post-intervention 样本门仍未满足，未 promotion。
- 在 14 个确定性执行项之后，追加 3 个交给便宜模型自由规划的动态 WorkItem，专门验证“会不会自己继续写代码”。这一层没有通过：03 规划超时，04 产出不完整的适配脚本，02 生成了带 `NotImplementedError` 和错误输入路径的脚本。三者都被终验收拒绝，没有冒充 completed。
- 动态链暴露两个框架问题：planner 可以把用户明确要求的 `.py + .json + .md + .pdf` 降级为只验收 PDF；“读取 `execution_wave_3_result.json`”被错当成本轮必须生成的文件。已修复为用户输出合同覆盖 planner 降级，并按句子语境区分输入与输出；回归为 152 passed。
- 继续跟踪发现，便宜模型会在 `generate_code` 或“生成代码”步骤返回 `<think>/<tool_call>` 转录，旧框架会把它当 `.py` 写入后才在运行阶段失败。现在只接受 AST 可解析的 Python，伪代码在落盘前失败；且 planner 用通用 LLM 生成 `.py` 源码时会自动改路由到 `generate_code`。中文顿号分隔的多文件合同也已覆盖；回归为 156 passed。
- 修复后持续 canary `campaign_6201619b614b` 于 18:39 启动，截止 20:39。首次动态尝试的四个失败已保留且由 05 摄取；已在同一 Campaign 中注入 01–04 修复后重试和最后 05 汇总，新 TaskInstance 的必需合同均为 `.json + .md + .pdf + .py`。
- 动态 02 首次真正生成并运行 7.3 KB 脚本，读取 184087 条记录并生成 JSON/Markdown，但分析误把 Vina 同时当特征和目标，导致 slope=1、intercept=0、RMSE=0 的 target leakage；且 PDF 内容门不通过，因此正确 blocked。已新增身份泄漏语义门，这组数字不得进入研究结论。
- 新增 planner 超时的有界本地 MicroPlan fallback：只对明确列出 `.py/.json/.md/.pdf` 的 Campaign 代码任务组装“代码生成→落盘→运行→目录核验→QQ”，不生成伪业务结果。已覆盖自动代码 Agent 未安装回退 Hermes、直接模板依赖失败禁止空文件，回归 161 passed。
- 19:20 追加的五实例治理回放已终态：03 合同回放 completed；01 在发布授权边界 blocked；02 在缺“分子身份+明确靶点+活性测量”边界 blocked；05 在全部终态后 completed。04 首次因 `issues.jsonl` 被截断为 `issues.json` 而误判，已修复扩展名/“核验输入”语境并用典型产物合同复跑，04 和随后 05 均 completed。回归 162 passed。
- Campaign 当前为证据边界 `blocked`，Controller 仍保留到 20:39；这证明“长运行服务”已存活，但没有新证据时仍缺少能生成有意义 NextAction 的持续工作源，不能用重复计算伪造“一直改进”。

这一结果证明“预声明的执行链”已经能快速推进三波，但“便宜模型自主规划下一波”仍不稳定。两者必须分开记账：前者是已通过能力，后者是当前 P0 调试对象。下一轮应使用修复后的强产物合同重跑动态 canary，只有脚本真实运行、机器结果可解析、分析和 QQ 送达都成功才生成 NextAction。
