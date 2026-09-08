# Sprint 21：多模态计算机操作与社区阅读

**状态**：Initial Events Implemented / Real Read-only Validation Running  
**日期**：2026-09-08

## 目标

让 Partner 能在用户可见的浏览器中打开小红书、微信公众号页面、腾讯开发者社区和阿里云开发者社区，使用同一实例的持久浏览器登录态阅读真实页面；每个关键动作同时产生 DOM/URL 确定性证据、页面截图、视觉模型描述和 LLM 跨模态判断。遇到登录墙时把窗口置前并明确等待用户登录，不读取、记录或代填密码；登录完成后可从同一持久 profile 继续。

## 基础能力盘点

- 浏览器：现有 Playwright 子进程与每实例 persistent profile，可见窗口可 `bring_to_front`。
- 视觉模型：`config/api.json` 已配置阿里百炼 `qwen3-vl-flash`；`qwen-image-3.0` 是生成模型，不用于看图。
- 推理模型：MiniMax-M3 负责将 DOM、视觉描述、项目历史和阅读目标综合为受限下一动作。
- 已有小红书 Event 能打开创作页、截图、视觉描述和检测登录墙，但偏发布入口，尚无通用社区阅读状态机。

## 证据原则

视觉模型描述是概率证据，不能单独证明点击、登录或阅读成功。每一步必须至少保存：最终 URL、页面标题、DOM 正文摘要、可见链接/控件、截图路径、截图 SHA-256、视觉模型及其描述、DOM 与视觉冲突、LLM 选择理由。登录成功还需要 DOM/URL/profile 状态信号；“看起来像已登录”不够。

## Event 设计

1. `multimodal_browser_observe`：打开白名单社区 URL 到前台，采集 DOM、截图，调用 Qwen VL，再由 MiniMax 做跨模态对照；不点击、不发布。
2. `multimodal_community_read`：先 observe；若有登录墙则返回 `awaiting_user_login`；否则 LLM 只能从真实 DOM 链接编号中选择一条同白名单链接，打开后再次采证并形成阅读结果。
3. `multimodal_login_resume`：用户完成登录后复验 DOM 与视觉证据；只有确定性登录信号存在才恢复，
   `unknown` 不得视为已登录。
4. 后续 `multimodal_browse_loop`：每轮只允许一次受限导航，满足新证据/相关性/预算门才继续；禁止点赞、评论、关注、发帖、上传和发布。

## 平台与边界

- 小红书：`xiaohongshu.com`、`creator.xiaohongshu.com`。
- 微信公众号公开页：`mp.weixin.qq.com`。
- 腾讯开发者社区：`cloud.tencent.com`。
- 阿里云开发者社区：`developer.aliyun.com`。
- 任意跳转必须重新做 hostname allowlist；短链、第三方广告、支付、下载和外部发布全部拒绝。

## 验收

- [x] 四个平台至少各完成一次 observe，证据包可复查。
- [x] 腾讯首页与公开文章完成 LLM 选链和二次真实阅读；阿里第二页 CAPTCHA 不计成功样本。
- [~] 小红书登录墙能打开到前台并正确停住；登录后的 resume 尚待用户下一次真实登录验收。
- [ ] DOM 与视觉故意冲突的夹具必须 fail closed。
- [ ] 所有外发用户文件默认只有最终 PDF；JSON、MD、截图保留本地证据目录，除非用户明确要求截图。
- [x] 当前三个 Event 均不能自动发布、评论、关注、上传或输入凭据。

## 2026-09-08 真实调试记录

- 腾讯：`multimodal_observe` 成功；LLM 从真实 DOM 选择 index 9，随后打开《和AI一起搞事情#8》并再次
  采集 DOM、截图、视觉和推理证据，状态 `article_observed`。
- 阿里：入口可观察，但选出的 `/blog/` 返回 `CAPTCHA Verification`。初版错误记为文章成功；修复后
  `challenge_detected` 是业务失败硬门。这条旧探针不得用于成功率或 Reward。
- 小红书：公开 feed 后存在扫码/手机号登录浮层。背景的“创作中心/消息”曾造成已登录假阳性；现改为
  登录浮层优先，普通导航不再算 auth proof。页面没有安全的帖子链接时停止，不用坐标猜点。
- 微信公众平台：首页 observe 成功，登录状态证据不足时保持 `unknown`，不擅自宣称已登录。
- 模型审计：Qwen3-VL 8 次、MiniMax-M3 8 次，共 66,074 tokens。真实调用是证据理解和决策，不是固定
  心跳或无意义 token 消耗。
