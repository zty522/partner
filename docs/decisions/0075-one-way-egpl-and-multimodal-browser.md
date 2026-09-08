# ADR 0075：EGPL 单向迁移与多模态浏览器初验

日期：2026-09-08  
状态：已实施；只读多模态进入真实验收，交互与发布未授权

## 决策

Partner 不再保留旧 `share/mind/governance/rl/` 路径、旧决策字段或运行时兼容回退。当前唯一状态根为
`share/mind/governance/experience_guided_policy/`，当前算法字段为 `selection_algorithm`，动作决策键为
`policy_decision_key`。模块、脚本和当前测试也改为 experience/policy 命名。WebRL、JIT-RL、Agentic RL
是外部项目或论文专名，保留原名不代表 Partner 自称传统 RL。

生产配置将 LLM 审议设为 `every_project_action`。每个实质项目动作调用 MiniMax 输出现实证据、联想边界、
约束、主假设、反假设、反例与安全下一动作；连续停滞的 Candidate 另调用 proposal 和独立 critic。LLM
提供语义判断，不拥有 Event 注册、真实指标、代码实施或 production promotion 权限。
该调用使用独立审计 purpose `project_action_deliberation`，不再混入通用 `classify` 统计。
生产检查发现首版 1,800-token 上限会让 MiniMax 只返回 reasoning：最近 10 条只有 1 条
`llm_participation.completed=true`。现改用 5,200-token 主调用、完整嵌套 JSON 解码与必要时的
`project_action_deliberation_reduction`；重启后首两条新选择均一次解析成功。旧失败记录不改写。

## 多模态 Event

新增 `multimodal_browser_observe`、`multimodal_community_read` 和 `multimodal_login_resume`。三者使用可见
Playwright persistent profile；导航前后都验证 HTTPS hostname allowlist；保存 URL、标题、可见 DOM、
截图及 SHA-256、Qwen3-VL 描述和 MiniMax 对照结论。当前只允许观察和一次受限同域导航，禁止输入凭据、
点赞、评论、关注、上传与发布。

browser worker 不再盲目继承调用者解释器。可以用 `PARTNER_BROWSER_PYTHON` 明确配置；本机默认选用安装
Playwright 的生产 Conda Python，避免开发探针由 `/usr/bin/python3` 启动时出现 worker socket 未就绪。

## 真实证据与修正

腾讯开发者社区首页 observe 成功，MiniMax 从 DOM 选出真实文章链接，第二次 observe 得到完整公开正文，
链路状态为 `article_observed`。阿里社区第二页返回 CAPTCHA，初版错误记为成功；新 `challenge_detected`
硬门使此类页面失败，旧探针不得计入 Reward。小红书显示登录浮层但背景仍有普通导航；登录浮层现优先于
弱导航信号，无安全帖子链接时停止。微信公众号首页可观察，但证据不足时登录态保持 `unknown`。

本次探针窗口实际调用 Qwen3-VL 8 次、MiniMax-M3 8 次，共 66,074 tokens。目标是把 LLM 放在需要视觉
理解、冲突解释和可证伪决策的位置，而非通过周期心跳制造 token 数量。

## 验证与边界

- 聚焦回归：多模态与策略相关测试通过。
- 完整回归：976 passed，2 warnings，0 failed。
- 已证明：四平台观察、腾讯公开文章只读、挑战页硬门、登录冲突 fail-closed。
- 未证明：小红书登录后恢复、微信公众号文章检索、通用桌面操作、任何发布行为和跨日期业务改善。
