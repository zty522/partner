## v0.5.0 – 多实例隔离与消息去重 Hotfix

### 🐛 消息推送彻底修复
- **移除所有硬编码回复**：「思考中，请等待...」「刚重启完」「有进展了跟你说」「系统已重启」全部删除，改用 LLM 生成或简洁事实性回复
- **消息去重表**：`_send_reply` 中 5 分钟 MD5 去重，`_handle_report` 中 10 分钟去重
- **推送内容过滤**：`_should_send()` 检测模板化内容，纯模板不推送
- **Project 进展限流**：每 5 轮推送一次，仅在有实质指标变化时推送
- **空闲不推送**：移除空闲报告推送到 QQ，仅记录日志

### 🔄 无限重启防护
- **新增 `restart_tracker.py`**：每个实例独立记录重启次数，1小时内超过3次停止自动重启
- **`__main__.py` 启动检查**：超过限制打印错误并退出(exit code 2)
- **`core.py` 崩溃检测**：连续异常记录到 `10_logs/crash.log`
- **`state.py` 增强**：Heartbeat 增加 `crash_count` 字段

### 🛡️ 后台干扰消除
- **新增 `resource_limiter.py`**：nice + RLIMIT_AS 限制 CPU/内存
- **心跳文件隔离**：`/tmp/partner_idle_heartbeat.txt` → 实例专属路径
- **空闲心跳不写 /tmp/**：避免全局临时目录冲突

### 📂 工作空间数据复制
- **`copy_external_data_to_workspace()`**：执行任务前将外部数据复制到 `99_temp/inputs/`

### 🚀 已有功能（从之前版本继承）
- Multi-Instance Manager with `partner-manager` CLI
- Isolated instance directories under `~/.partner/instances/{id}/`
- APScheduler-based in-process cron (per instance)
- Systemd template `partner@.service`
- Mind Pool async event architecture
- Token usage tracking (`/usage` command)
- Windows installer improvements

### ⚠️ Hermes Agent Availability Detection
- **GUI Chat Tab**: When Hermes Agent is not installed, shows a yellow warning card with "⚠ 当前没有可用的 AI 引擎" and an **📥 安装 Hermes Agent** button that opens the install docs.
- **QQ Bot**: Returns a user-friendly message with installation link instead of silently falling back to a non-LLM response.
- **`adapter.py`**: New `HermesAdapter.is_available()` static method to quickly detect if the Hermes CLI is on PATH.

**Full Changelog**: https://github.com/zty522/partner/compare/v0.4.0...v0.5.0
