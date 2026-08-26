# Sprint 12：先跑好一个项目的证据闭环

## 目标

暂停五项目平均用力，先把实例 02 的 TargetDiff 项目跑成可复现、可承接、可交付的完整主线。实例 05 只在项目里程碑完成后学习真实轨迹，不能用“自进化”替代科学项目。

## 纠正的事实

旧收据 `receipt_656e81a71043` 认为没有同时包含目标与活性的数据。真实读取
`external/targetdiff/data/affinity_info.pkl` 后确认：184,087 条记录均可审计，其中 76,803 条有有限且大于零的 `pk`，同时含 `vina/rmsd`；有效记录覆盖 1,041 个 key 首段近似组。旧收据通过 `receipt_corrections.jsonl` 追加失效记录，历史未删除。

## 单项目协议

| 阶段 | 固定事件 | 新证据 |
|---|---|---|
| 1 | `targetdiff_data_contract` | 字段、有效样本、组键和拆分合同 |
| 2 | `targetdiff_group_baseline` | 训练均值、Vina、Vina+RMSD 分组基线 |
| 3 | `targetdiff_nonlinear_compare` | 固定参数非线性候选同集比较 |
| 4 | `targetdiff_residual_analysis` | RMSD 分层和最差的样本充分组 |
| 5 | `targetdiff_group_cv` | 五个确定性留出组的均值与波动 |

硬语义门：唯一标签为 `pk`；特征只能使用声明的 `vina/rmsd`；按 key 第一个斜杠前的组做 SHA256 五折；训练/测试组重叠必须为零；不作药效因果或临床主张。

## 工程实现

- `partner/v2/targetdiff_project_events.py`：真实写出并运行分析源码，保存 JSON、Markdown 和严格详细 PDF。
- `partner/governance/campaign.py`：新增五个 bounded event 和 `seed_targetdiff_project_work()`；Stage 1–5 同实例串行，05 等全部 02 项终态。
- `partner/mind/executor.py`：直接路由固定事件并将 PDF 与清晰摘要分别通过真实 QQ callback 交付。
- `scripts/partner_campaign.py`：新增 `--profile molecular --stages 5`。
- 项目自己的 `project_contract.json`、`project_brief.md` 已补齐目标、禁区、证据和完成门。

## 实证基线

正式 Campaign 前的真实 smoke：五折 Vina+RMSD 线性模型平均 RMSE 1.5971、标准差 0.0533、平均 MAE 1.2616；各折 R² 为 0.085–0.195。这个结果说明现有特征有有限预测力，不能夸大。非线性候选是否保留，必须以后续同测试集和跨折证据决定。

## 当前运行

`campaign_0587a59dfe22` 已完成 Stage 1–7、两次 05 审计和最终日报：10/10 completed。最终日报首试因项目合同旧格式不兼容失败，读取器已兼容、合同已规范化，重试真实送达；账本保留 1 failure/1 retry。

Stage 6 的训练折 Vina 分位数裁剪 5/5 折改善，平均 RMSE 降 0.0312。Stage 7 的 HGB 相对线性模型 5/5 折改善，平均 RMSE 降 0.0648，达到预声明门，结论为保留 candidate 而非 production promotion。

后续 `campaign_f6cfb4e0ed9d` 完成 Stage 8 来源审计、05 增量审计和最终日报。首次任务因实例仍加载旧 handler 而失败，重启后确定性事件真实运行和交付；另一个从旧收据物化的泛化 batch_plan 被明确 cancelled，未允许其抢占主线。审计确认 README 把 pK 描述为实验 binding affinity，构建器从 CrossDocked train0 types 读取 pK/RMSD/Vina，但官方 split 文件本地缺失。

## 验收

- 每阶段必须同时有源码、退出成功、机器 JSON、详细 PDF、QQ 文件和摘要回执。
- Campaign WorkItem 必须按阶段顺序承接同一合同，不由自由 planner 临时换题。
- 05 只使用真实完成/失败/送达轨迹；样本门或回归门不足保持 inconclusive。
- 全回归基线：`166 passed`。

## 后续边界

完成五阶段只是“基线项目第一闭环”，不是分子生成创新已经完成。下一轮应根据非线性增益、最差组和跨折波动选择一个可证伪的特征或方法实验；还需要核对官方数据字典与官方 split，并处理 Vina 异常值。证据不足时宁可拒绝复杂模型，也不能为了持续运行机械生成轮次。

## molecular-continuous canary（campaign_a5f3c0c41760）

新增 Receipt 驱动的持续补给器与 `--profile molecular-continuous`。Controller 只在上一阶段 completed、产物/QQ 回执验收并写入 Receipt 后补充一个下一阶段；主线为 Stage 9→10→05→11→12→13→05。历史 Issue 和泛化 Project NextAction 在该 profile 下禁用。

- Stage 9：76,803 行聚合为 63,123 个配体 identity，重复倍数 1.2167；HGB 相对线性五折平均 RMSE 改善 0.0592，5/5 折改善。
- Stage 10：1,041 个靶点组等权后，线性 macro RMSE 1.5449、HGB 1.5046，改善 0.0403。
- Stage 12/13：靶点组固定种子 1,000 次 bootstrap，模型差异均值 -0.0405，95% CI [-0.0683,-0.0148]，非线性更优概率 0.999。三个预注册门通过，因此保留 HGB candidate，但 `production_promotion=false`。
- Stage 10/13 后分别运行一次 05。业务轨迹 reward=0.98，其中 `novel_evidence=0.08`、`handoff_consumed=0.08`。
- 正式账本：7/7 completed、0 failure、0 retry。Stage 13 后进入 `waiting for resume event or new evidence`，没有机械制造轮次。

这证明“执行→收据→补给→里程碑 RL→恢复项目”已经跑通；下一阶段应让 portfolio scheduler 在 02 等待时切换到 03 或 04 的声明项目主线。

该后续已在 2026-08-24 实现为 `portfolio-continuous`；Sprint 12 到此封版，组合调度的当前合同转入 `docs/architecture/project_portfolio.md`。
