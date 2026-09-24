# ADR 0110：Jev 与专用模型使用统一工作区配置

- 状态：accepted
- 日期：2026-09-24

## 决策

新增工作区 `config/model_services.json` 作为辅助模型唯一新写入入口。Jev 从 `partner_config.json` 迁出；世界模型客户端和 CLI 改读写同一注册表。主对话 LLM 继续由 `config/api.json` 管理。后续模型以 `services.<name>` 增加，不再各自发明配置文件。

服务配置允许直接 credential 或环境变量引用；运行证据禁止复制 credential。Jev 当前保持 shadow，世界模型默认关闭。旧世界模型 YAML 只作读取兼容，不是新配置来源。

## 原因

模型 endpoint、模型版本、运行模式和认证属于同一变化单元。把 Jev 混在运行策略、世界模型放在独立 YAML，会造成配置来源分裂，也不利于以后同时接入多种模型。

## 验收

加载器能从独立 JSON 解析 Jev endpoint、model 和 credential；Core Event 使用该配置；世界模型客户端优先读取该配置；缺失或损坏配置时 fail closed；真实 credential 不进入 Git、文档、测试或 Event 产物。
