# 01 小红书浏览器 Playbook

## 已验证实现

- Playwright 运行在确定性 `partner-browser-01` systemd worker，避免主进程 fork 导致 Chromium SIGTRAP。
- 可见操作必须 `visible=true` / `foreground=true`，并执行 `page.bring_to_front()`。
- 登录成功不信任用户文字或 LLM 猜测；联合页面文字、Cookie 长度和登录墙信号。
- 关键步骤调用 `_visual_step`：截图 → qwen 读图 → 图片回调发送 → 文字回调发送。
- 任一环节失败都不能返回事务成功。
- 视觉说明可能小范围误判 UI 选中态，所以必须同时检查 DOM、文件控件和页面要求文字。

## 历史反模式

- headless 截图不是用户可见页面。
- `delivery_queue.jsonl` 无消费者，写入它不是 QQ 发送。
- 从全局截图目录找“最近图”会把旧任务产物当成本轮证据。
- 单页 click/extract/screenshot 并发会产生空响应和错误重启，必须串行。
- 打开登录页后不能停在 Markdown “下一步”；登录验证成功后需真实 enqueue 后续事件。

## 当前边界

已验证到登录后的图文上传入口与上传要求。最终内容发布是外部不可轻易回退动作，需内容、安全检查和明确授权。
