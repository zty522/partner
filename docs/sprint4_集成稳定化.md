\# Sprint 4: 集成与稳定化

**时间**: 2026-07-05 \~ 2026-07-18

**目标**: 世界模型部署、GUI 打磨、生产 Bug 修复、端到端稳定性

## 1. 设计内容

### 1.1 AETHER 世界模型 GPU 部署

-   CogVideoX-5b-I2V 微调权重 \`AetherWorldModel/AetherV1\`

-   远程 GPU 服务器 (matpool RTX 4090 24GB) + SSH 隧道 → 本地 WSL

-   三层回退：AETHER → LLM (DeepSeek) → Heuristic

-   VRAM 优化后 \~11.6GB

-   推理时间 12-14s（简单计划）

**结论**: AETHER 对 Partner 的任务类型（代码执行、生信分析）无效 ---
像素预测≠代码结果。Sprint 5 正式废弃此方向。

### 1.2 GUI 打磨

-   Inno Setup 安装器 (\`partner_installer.iss\`)：Windows EXE 打包 78MB

-   Settings 页面重构：工作区配置、Agent 安装检测面板

-   实例管理：多实例启动/停止/状态监控

-   跨平台实例管理：WSL 实例通过 \`wsl.exe python3\` 启动

-   单实例检测：重复打开时唤醒已有窗口

### 1.3 Bug 修复汇总

  --------------------------------------------------------------------------------------------
  **问题**                  **根因**                  **修复**
  ------------------------- ------------------------- ----------------------------------------
  QQ 群@消息回复不可见      \`\_push_to_last_user\`   根据 message_type 判断群/私聊
                            硬编码 PRIVATE            

  群被动回复配额耗尽        进度通知消耗了            群@时跳过 \`\[进度\]\` 开头消息
  (40034128)                5次/5分钟配额             

  batch_planner 超时        LLM context 累计 \~400K   \`\_clear_session_id()\` 清除无状态分类
                            tokens                    

  Hermes 子进程超时         asyncio PIPE 缓冲区问题   改用同步 \`subprocess.run()\`

  plan_ready 消息不发       progress_updates 默认     增加 \`plan_ready\` 到 gated 列表
                            false                     

  \`desktop_inbox.jsonl\`   路径写到了 workspace_root 统一为
  不被消费                  而非 instance_dir         instance_dir/state/desktop_inbox.jsonl

  config 找不到             load_harness_config       增加 workspace_root/config/ fallback
                            只看实例目录              

  队列模块冲突              \`partner/queue/\` shadow 重命名为 \`partner/msg_queue/\`
                            stdlib queue              
  --------------------------------------------------------------------------------------------

### 1.4 多实例管理

统一三个实例到同一 workspace：

-   **partner03**: QQ Bot + cytobridge（主实例）

-   **partner05**: 世界模型集成（后废弃）

-   **partner06**: 桌面 GUI 测试实例

## 2. 关键文件

  -----------------------------------------------------------------------------------------------
  **文件**                                                    **变更**
  ----------------------------------------------------------- -----------------------------------
  \`shells/frontend/qq_bot/qq_official_bot.py\`               群@ member_openid 支持

  \`shells/frontend/desktop_gui/modern/pages/settings.py\`    Settings 重构 (+900行)

  \`shells/frontend/desktop_gui/modern/pages/instances.py\`   多实例管理

  \`partner/adapters/adapter.py\`                             session_id 清除, 超时修复

  \`partner/mind/executor.py\`                                progress_updates gating

  \`partner/planner/batch_planner.py\`                        WorldModelClient 集成
  -----------------------------------------------------------------------------------------------

## 3. 完成情况

  -----------------------------------------------------------------------
  **功能**                **状态**                **验证方式**
  ----------------------- ----------------------- -----------------------
  GUI 安装器              ✅ 完成                 Inno Setup 打包 78MB
                                                  EXE

  QQ 群@回复              ✅ 完成                 群消息正确回复到群

  上下文超时修复          ✅ 完成                 per-purpose session
                                                  清除

  多实例管理              ✅ 完成                 3个实例正常运行

  Bug 修复                ✅ 完成                 8 项主要 Bug 全部修复
  -----------------------------------------------------------------------

## 4. 遗留问题

-   AETHER 世界模型对 Partner 任务无实际价值（像素≠代码） → Sprint 5
    结论确认

-   CWM 32B 需要 160GB VRAM，当前硬件不可行

-   batch_planner 偶尔超时（DeepSeek API 波动）
