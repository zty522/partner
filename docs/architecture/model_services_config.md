# 辅助模型统一配置

- 状态：implemented
- 更新：2026-09-24

Partner 把模型配置分成两个明确入口：`config/api.json` 只管理主对话 LLM；`config/model_services.json` 管理 Jev、世界模型以及以后加入的 reranker、critic、视觉模型和专用预测模型。`partner_config.json` 只保留运行策略和 Core 预算，不再保存 Jev endpoint、model 或 credential。

`model_services.json` 的稳定顶层为 `schema_version` 和 `services`。每个服务至少声明 `kind`、`enabled`、`provider`、`endpoint`、`model`、运行 `mode`、超时和 `authentication`。调用方必须按服务名做点读取，不能扫描其他配置或把 credential 写入 Event、日志、报告和测试产物。

当前条目：

- `jev`：OpenRouter typed evaluator，`shadow`，实际凭据保存在本机工作区配置中，也可用 `api_key_env` 回退；运行证据只记录 provider、model、状态、延迟和 usage。
- `world_model`：统一占位条目，当前 `enabled=false`。旧 `world_model.yaml/json` 仅保留读取兼容；CLI 的新写入统一进入模型服务注册表。

`partner.model_services` 提供有界读取、credential 解析和原子单服务更新。配置文件是工作区状态，不进入 Git。工作区位于 Windows 挂载盘时，WSL 可能把文件 mode 显示为 `0777`，不能把 POSIX mode 当作访问控制证据；生产部署应改用仅当前用户可读的 Linux 文件系统、WSL metadata 挂载或外部 secret manager。

迁移后真实连通探针通过：统一加载器读取工作区 JSON 后，OpenRouter 返回 `completed`，实际模型版本为 `typesafe/jev-1.13-20260917`，四类答案齐全，Jev 仍为 `authoritative=false`。
