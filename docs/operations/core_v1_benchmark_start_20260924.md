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
