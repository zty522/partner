# 六小时五项目 Campaign 审计（2026-08-24）

## 审计对象

Campaign `campaign_744a39317fad`，运行目标为五项目轮转、最多双槽、长期 evidence
scout 和 05 离线 RL。Controller 在 deadline 停止，不是异常退出。

## 终态数字

| 指标 | 结果 |
|---|---:|
| WorkItem created | 75 |
| completed | 74 |
| blocked | 1 |
| failure/retry 计数 | 2 / 2 |
| evidence scout | 24 |
| report | 6 |
| 含 `delivery_confirmed=true` | 74 / 75 |

按实例创建数为 01=14、02=11、03=11、04=9、05=30。数字很高不等于产生了同等数量
的项目创新：其中 24 项是监测 scout，05 又在业务波次和 scout 后多次做 RL 审计。

## 唯一未完成项

02 的 `work_eff99a5768e0`（主动探索 Round 1，事件
`targetdiff_official_split_benchmark`）最终为 blocked，原因为任务侧报告失败且没有
找到最终 LLM acceptance。Campaign 记录了失败和重试，没有将退出或部分产物冒充完成。

## 暴露的问题

1. 同一个低收益 action 的证据没有变化时，05 每轮仍调用 `record_issue`，导致同一
   fingerprint 在 JSONL 中重复出现数十次。轨迹追加是合理的，Issue 重复不是进展。
2. 74 completed 主要证明调度、持久状态、产物和 QQ 交付合同能长跑；不能证明五个
   业务项目每 15 分钟都有新科研/工程成果。
3. 05 在 75 项中占 30 项，说明自进化观察频率偏高。后续证据图/reducer 应区分
   no-change monitor、真实业务增量和策略变化，再决定是否需要生成用户可见 RL 报告。

## 已采取措施

- `record_issue` 对相同 fingerprint 且 incoming evidence 已被包含的观察返回
  `unchanged`；新 Campaign 或新指标仍作为新证据累计。
- DeepSeek Harness/OpenAI Codex 学习输入进入实例 04 的有界指纹；只读取关键文件，
  不自动执行外部仓库。
- 新 Campaign `campaign_76550fd7382a` 已启动。首周期 03、04 和其后的 05 均完成，
  证明代码变化与新外部资料实际触发了新波次。
