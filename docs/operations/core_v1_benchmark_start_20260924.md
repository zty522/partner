# Core v1 Davis benchmark 启动记录（2026-09-24）

## 目标

在实例 01 上用 `pk_target_feature_v1` 运行完整 `benchmark_experiment`，比较冻结 HGB baseline 与唯一增加 `target_aac20` 的 candidate。数据为 Davis 的 30,056 条实验 Kd，使用五个 target-disjoint folds 和固定 bootstrap seed 20260924。

## 配置

最初曾同时维护 `api.json` 和重复的 `agent_api_config.json`。用户随后明确要求删除重复文件，以 `api.json` 作为唯一 LLM provider、模型、端点和凭据来源；Direct API、Hermes 子进程和 Commitment proposer 均读取其 `default_provider` 对应的完整条目。2026-09-24 已切换为用户新配置的 Qwen。凭据未写入本文档。

结构化 benchmark 入口同时修正为不经过普通三遍 LLM 意图审议；明确协议直接创建父 Flow，subject 内 LLM 节点仍正常调用 provider 并 fail-closed。

## 真实运行状态

- Job：`job_19fa9d176ebb4e3e`
- Benchmark run：`bench_e6f69a20a3204268`
- 父 Flow：`flow_666a6f9df74f4a13`
- 当前 baseline 子 Flow：`flow_8d816b6adcd64aec`
- 已完成：协议解析、环境预检、运行冻结、arm 规划、差异检查、baseline recall、inspect、`CP1_STATE`
- 当前节点：`candidate.propose`

MiniMax 在该节点连续三次返回 HTTP 429 / code 2056，信息为 Token Plan 用量上限。配额查询还表明该旧域名 key 当前没有 active Token Plan。没有替换 provider，没有生成伪候选，没有运行 baseline HGB，也没有 Settlement。Run manifest 保持 `running`，可在 MiniMax 配额恢复后从 `candidate.propose` 继续，不应重复创建新 run。

本记录证明结构化入口和父子 Flow 已真实启动，不证明 benchmark 完成或 Core v1 获得 uplift。

## Qwen 切换后的恢复结果

删除重复配置并切换 `api.json.default_provider=qwen` 后，`qwen3.8-flash` 短探针成功。长提示最初因模型默认开启思考而连续三次达到90秒硬超时；随后在 Qwen provider 条目显式设置 `enable_thinking=false`，约18k input token 的长提示探针成功，baseline subject 从 `candidate.propose` 继续通过 critic、select、世界模型/Jev和 Commitment，到达真实 execute。

该 execute 暴露了新的实验隔离缺陷：baseline subject 的通用 LLM action 同时调用了 baseline 和 candidate runner，并生成一个合并 `benchmark_evidence.json`；两个分臂原始文件虽然数值真实，但这违反“一次 subject 只运行当前 arm”的协议，也使父 collector 不能把合并文件当成合格分臂证据。因此当前 run 不继续 candidate，不宣称 Settlement 成功。后续需把 arm 执行收敛为协议驱动的专用 Event，由 Runtime 注入当前 arm，不能依靠 LLM 遵守命令文字。

## 隔离修复

旧 Job `job_19fa9d176ebb4e3e`、父 Flow `flow_666a6f9df74f4a13` 和 baseline 子 Flow `flow_8d816b6adcd64aec` 已于 2026-09-24 标记为 `cancelled`。原始产物和账本保留，不能作为成功实验引用。

`benchmark_subject@1.1.0` 使用专用 `benchmark_subject.arm_execute` 替代通用 LLM action。该 Event 只接受 subject 公开视图中的当前 arm，以 argv 数组调用冻结 runner 一次，并校验 RMSE、预测记录、guardrails、run config、当前臂特征和数据 SHA-256。baseline 必须记录空 declared feature，candidate 必须精确记录预声明 feature；任一不符均 fail-closed。协议 `pk_target_feature_v1@1.1.0` 将 `arm_runner_path` 纳入必需输入和冻结哈希，并明确单臂最长 1800 秒。旧 `benchmark_subject@1.0.0` 仍只为历史 Flow 的版本解析保留。

仓库级父子 Flow 测试已验证两臂分别执行、子 Flow 恢复、六个检查点、父 Flow 结算和 run close。真实 Davis 新运行须使用新 run id，最终结果将在完成后追加；本节只证明隔离机制修复，不提前宣称科学结果。

## 首次干净完整运行

- Job：`job_2d274c5e3ec44009`
- Benchmark run：`bench_5e9522710d3343bb`
- 父 Flow：`flow_15e72893cbff428b`
- baseline 子 Flow：`flow_3232338117784453`
- candidate 子 Flow：`flow_7d5e04788c3d40e9`
- 终态：Job、父 Flow、两个子 Flow 和 manifest 均为 `completed`；Settlement 为 `confirmed`，`valid=true`

baseline 和 candidate 各有且只有一份 `invocation_count=1`、`returncode=0` 的命令回执，argv 中的 arm 分别为 `baseline` 和 `candidate`。两臂各产生 30,056 条同 sample id、同 truth 的 out-of-fold 预测。baseline 的 declared feature 为 null，candidate 精确为 `target_aac20`。

baseline RMSE 为 0.7885014888，candidate RMSE 为 0.6495254264，冻结方向下的改善为 0.1389760624。1000 次固定 seed bootstrap 的 95% CI 为 [0.1290150478, 0.1486379029]，超过预声明最小改善 0.03 且不跨 0。MAE 从 0.5050648390 降至 0.4098221440，R² 从 0.2233091449 升至 0.4729698391。全部四个硬 guardrail 以及派生的 secondary metric guardrail 通过，执行配置只含协议允许的 feature 差异。Qwen 双顺序盲评一致；Jev 因缺少 `TYPESAFE_API_KEY` 标记为 shadow/unavailable，不参与权威裁决。报告核验和 local event-ledger delivery ACK 均通过。

该次运行还显示 `benchmark_subject@1.1.0` 的通用 memory recall 和 project state inspect 会让反思节点看到历史污染运行内容。它没有进入父级确定性指标、配对比较或 Settlement，因而不改变上述结论，但违反严格认知隔离。`benchmark_subject@1.2.0` 已将二者替换成 public-only context 和 declared-input inspection Event：禁止读取项目历史、记忆及先前实验臂，只暴露当前公开视图与声明输入哈希。后续新运行默认使用 1.2.0；1.0.0 和 1.1.0 仅用于解析历史状态。

## 1.2.0 最终验收运行

- Job：`job_767874112b114070`
- Benchmark run：`bench_cc47db5d81c245d6`
- 父 Flow：`flow_1ae840f7f0ee45a9`
- baseline 子 Flow：`flow_8823243f951a4914`
- candidate 子 Flow：`flow_e579d2b10cc04a00`
- 代码 revision：`f83cb226dfb89c93048e2de6ff3bdb9fd27bca87`

最终验收完整通过。两个子 Flow 均固定为 `benchmark_subject@1.2.0`、无失败节点，各有六个检查点；`recall` 与 `inspect` 均记录 `visibility=public_arm_only`、`history_access=false`、`memory_access=false`。两臂分别只有一次 runner 命令回执。Job、父 Flow、两个子 Flow、manifest 均为 `completed`，报告核验和 local event-ledger delivery ACK 通过。

确定性结果与首次干净运行完全一致：baseline RMSE 0.7885014888，candidate RMSE 0.6495254264，改善 0.1389760624，95% bootstrap CI [0.1290150478, 0.1486379029]，Settlement 为 `confirmed` 且 `valid=true`。Qwen 双顺序盲评一致。Jev 仍因没有 `TYPESAFE_API_KEY` 仅标记为 shadow/unavailable，不影响权威确定性结算。
