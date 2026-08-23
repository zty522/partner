---

## 2026-08-23 — 30 分钟 Campaign 实跑修复

- 新增 01/02 确定性 Campaign 事件：小红书上传契约安全审计、分子目标/活性数据就绪度审计。
- 修复 failed task 即时协调、deadline 限制 Lease、业务 blocked Receipt/resume event、所有终态幂等和取消队列收口。
- 修复 blocked 不发阶段报告、报告误入通用 LLM planner、Campaign ack/STOP/Research Loop 噪声。
- 修复 02 外部目录无界扫描；产出详细 PDF、Markdown、校验 JSON 和目标数据契约。
- 修复视觉步骤丢失和模型调用漏账；planner 完成即持久化成本 checkpoint。
- 实机 `campaign_a06e75ccfa0f`：01/02 主 WorkItem 均真实交付后 blocked；12:53 阶段报告真实 QQ 送达。
- 回归：`137 passed`。


## 2026-08-21（续17）— 真实效果复测：浏览器操作链路修复（8 缺陷）

用户指出首轮测试深度不足（example.com 静态页掩盖问题）。真实浏览器操作测试（Bing/DDG）
暴露并修复 8 个缺陷：

1. browser_worker 结构 bug：helper 插入破坏 _dispatch 分支（screenshot 返回 null）→ ast 验证重组
2. 实例环境 chromium SIGTRAP（主进程 Popen spawn）→ systemd-run 干净进程 + unix socket 长驻
3. worker 健康检查失效（systemd-run 句柄立即退出）→ socket 连通性检查
4. persistent_context 未设 UA（headless 特征被反爬）→ UA 统一设置
5. 页面加载慢导致操作超时 → open 渲染等待 + 超时 30s
6. selector 盲猜无反馈 → 失败 dump 可见元素 + 标题/正文
7. report.md design 串写（LLM 生成报告=design 模板，反复出现）→ 防护提前 + 步骤结果提取 + 校正放行
8. （复测确认）失败步骤显示 ✅ → ✅/❌ 图标

**验证**：browser_open→type→screenshot→read_image 链路真实打通（截图 748KB-2.9MB、
qwen 真实识别内容）；design 防护单测通过（design 内容→替换为 read_image 真实描述）。

**遗留**：反爬网站（Bing/DDG）headless 访问受限；LLM 生成报告内容=design 模板（核心质量遗留）。

---

## 2026-08-21（续16）— Sprint 10 严格测试完成（147 项全绿 + 修复 7 缺陷）

### 测试体系（docs/sprint10_严格测试.md + testing_report_sprint10.md）

- L1 单元 pytest：70 用例（8 文件）
- L2 事件集成：31 项真实调用（qwen API/Edge/真实工具）
- L3 生信 Agent：29 项真实数据（enrichment/plink/iqtree/bcftools/diffexp）
- L4 端到端：3 实例真实任务（代码/截图读图/分析落地）
- L5 回归 + L6 稳定性：17 项（batch_plan ×3、write_design、planner 过滤、实例隔离）

### 测试发现并修复的缺陷

1. evaluator.py record/load 忽略 workspace 参数 → workspace 优先/指针 fallback
2. C4 技能卡片固定根级 share/mind（跨实例共享语义）
3. **run_command `python` not found**（systemd PATH 无 miniconda）→ python→python3 兼容替换
4. **progress_done 模板固定 ✅** → 加 {icon}（✅/❌ + 失败错误摘要）
5. correct_extension 重构模块级（可测试）
6. direct_api 提取 select_model_and_tokens（可测试，单一事实来源）

### 遗留观察

- write 步骤 content 偶发引用 design（LLM 行为）→ executor 层检测兜底列为后续优化
- execute_code 生成代码质量问题（LLM）→ 重试机制已兜底，可加强验收

---

## 2026-08-21（续15）— Sprint 10 启动：测试体系 + P1 单元测试

### docs 更新

- partner_code.md：代码结构树更新（evolution 新增 evaluator/gap_filler/self_review、
  v2 新增 vision_events/gap_events/capability_events、tools 新增 run_log、5 个生信 wrapper、
  manifest 18 个）
- skill.md：Agent 表 +5、事件模块表 +3（capability_inventory/ensure_tool/read_image）、
  自进化体系表 +C1-C4
- 新增 docs/sprint10_严格测试.md：L1-L6 六层测试方案（单元/事件集成/Agent 实测/
  端到端/回归/稳定性）+ P1-P6 执行计划 + DoD

### P1 完成：pytest 基建 + 第一批单元测试（34/34 全绿）

tests/ 目录 5 个文件：
- test_artifact_validator.py（5）| test_correct_extension.py（8）
- test_evaluator.py（9）| test_gap_filler.py（8）| test_run_log.py（4）

### 测试发现并修复的真实 bug

1. **evaluator.py record/load 函数忽略 workspace 参数**（参数契约缺陷）：
   record_failure/record_quality_score/record_success/load_recent_failures 全部硬编码
   workspace_root_from_pointer()，传入参数无效。统一为 workspace 优先、指针 fallback。
2. **C4 技能卡片共享语义**：record_success/load_recent_successes 设计为跨实例共享
   （share/mind 根级），修复后固定写/读根级（不随实例 workspace 隔离）。
3. **C2 失败反思自洽性确认**：实例级读写（写实例 state/logs、注入读实例级）——
   此前写根级读实例级，反思注入从未真正生效；现在自洽。
4. __main__.py correct_extension 从嵌套函数重构为模块级（可测试）。

---

## 2026-08-21（续14）— 运行追踪：代码日志 + 读图事件实测与修复

### 实测（注入真实任务到 05/04）

**05（写代码+运行代码）**：任务自动规划 create_file(fib.py) → run_command 运行 → 报告。
- 运行日志生效：instances/05/state/logs/code_runs.jsonl 记录 2 次 run_command（21:59、22:01），
  exit=0，stdout 为真实斐波那契数列（F1..F20）——实例还自行修正脚本（第一版 F1=0 → 第二版 F1=1）。
- 注：日志写入**实例级** state/logs（ctx.workspace 是实例目录），查看用实例路径。

**04（截图+读图核查）**：web_capture 截图 → read_image 读图 → 生成检查报告。
- read_image 用 qwen3-vl-flash 准确识别 example.com 截图："不是空白页，页面已正常渲染"，
  描述出标题 Example Domain、说明文字、Learn more 链接——核查链路打通。
- 但 web_capture 实际失败（"powershell.exe not found"）——WSL systemd 服务 PATH 不含
  powershell.exe，而 _local_web_capture 用相对名调用。修复：改用完整路径
  （/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe，含 fallback）。
- 修复后重测：web_capture ✅ → read_image ✅ 完整成功。

### 修复（4 处）

1. **web_capture powershell 完整路径**（harness.py）：相对名 → 完整路径 + which fallback。
2. **web_capture 事件描述引导**：output 参数必填且指向任务工作目录（防产物不可追踪）。
3. **read_image 容错**（vision_events.py）：path 不存在时自动回退到工作目录/
   实例 screenshots/根 screenshots 最近的图片（LLM 常猜错截图路径）。
4. **artifact_validator 图片扩展名兼容**：*.png pattern 同时匹配 .jpg/.jpeg/.webp/.gif
   （web_capture 输出 jpg 是已知行为，验收不再误报 missing）。

### 遗留观察（LLM 行为层面）

- 04 的 screenshot_check.md 内容仍是 design 文档（write 步骤 content 引用了 design 而非
  read_image 报告）——prompt 已加"content 必须引用 $step_X.result.content"，但 LLM 偶发
  引用错误；后续可考虑 executor 层兜底（write 步骤 content 含 "# 总设计" 时警告/重试）。

---

## 2026-08-21（续13）— 代码运行日志 + 读图事件（检查落地与截图）

### 1. 代码运行日志（partner/tools/run_log.py）

- `log_code_run()`：记录 execute_code/run_command 事件到 `{workspace}/state/logs/code_runs.jsonl`
  （ts/event/workdir/script/exit_code/ok/stdout_preview/stderr_preview/error）。
- `recent_code_runs()`：读取最近记录（供检查）。
- 接入 harness.py `_local_execute_code` + `_local_run_command`（执行后自动记录）。
- 用途：检查"方案是否真实落地"——每实例跑了什么代码、成功与否一目了然。

### 2. 读图事件 read_image（partner/v2/vision_events.py，注册进 v2 事件表，61 事件）

- `atomic_read_image`：输入图片路径 + 可选 prompt，用 api.json 预设的 qwen 视觉模型
  （qwen.vision_model=qwen3-vl-flash）返回图片内容描述。
- workspace 自动上溯定位 config/api.json（实例目录 → 根）；图片自动缩放到 1200 内。
- 实测：生成测试图（白底黑字）4.3s 准确识别"PARTNER READ_IMAGE TEST 2026"。

### 3. 截图空内容深层证据（read_image 核查发现）

- 04 今天产出 creation_center.png（4254B，有效 PNG）实为**纯白色空白图**——
  Edge headless 截小红书创作中心（登录墙/反爬）页面渲染失败。
- 结论：空截图两类成因——① md 文本冒充 png（已修：扩展名纠正+截图路径）；
  ② 真 PNG 但内容空白（页面未渲染）→ 现在可用 read_image 事件自动核查检出。

### 实例代码落地核查（实机）

- 03 最近任务：_execute_code.py 生成 + execute_code 步骤 ok=True（真实运行代码）✓
- 01/02/04/05 最近任务为调研/截图类（web_search/read_file/web_capture），无代码需求。

---

## 2026-08-21（续12）— batch_plan 稳定性修复链（运行追踪发现）

### 问题（03 实例实测：注入"分析 executor.py + 必须运行代码验证"任务）

1. `Batch planner LLM call failed: timeout after 120s`——batch_plan 走 direct_api，
   model=deepseek-v4-flash（api.json 配置），25KB+ prompt 超时（skill 早有记录）。
2. 切 deepseek-chat 后：`incomplete JSON object in planner output`——max_tokens=4096
   截断（实测输出 10909 chars 处断裂 = 4096 tokens 上限）。
3. max_tokens 加大后：输出仍可达 25KB+（LLM 生成超长 plan，description 里甚至嵌代码）。
4. write_design 卡住 11 分钟无响应——purpose="action" 走 v4-flash 长文档生成卡死。

### 修复（direct_api.py + prompt_builder.py）

- **长生成类 purpose 统一 deepseek-chat**：`batch_plan/action/report/focus_extract` →
  deepseek-chat（v4-flash 保留给 chat/classify/direct_reply 普通对话）；api.json 可加
  `long_gen_model` 覆盖；batch_plan 可用 `batch_plan_model` 单独覆盖。
- **max_tokens 16000**（长生成类）：避免输出截断导致 JSON 不完整。
- **prompt_builder 输出约束**：计划 JSON ≤8000 字符、description 一句话、禁止代码/长文本
  嵌入 parameters、输出必须纯 JSON 无 markdown 围栏。

### 验证（03 实机，注入测试任务）

- 修复前：超时 → JSON 截断 → JSON 不完整，连续 3 次失败。
- 修复后：规划 20s 成功（9 步）；**任务含 4 个 execute_code 步骤实际运行 Python 验证**；
  write_design/报告生成快速完成；全任务 ~2 分钟；产出 report.md 11.8KB
  （明确"静态分析 + 动态运行验证"，附验证脚本与运行结果）。
- 「方案不落地」修复确认：实例现在自动规划实际执行步骤，不再只写方案。

---

## 2026-08-21（续11）— 实例实测问题修复：截图空内容 + 方案不落地

### 问题证据（实机追踪）

- 04 浏览器登录任务：browser_screenshot 成功但输出到 `/tmp/partner_screenshot_xxx.png`（LLM 未传 save_path，
  worker 默认 tempfile），后续步骤无法引用 → 推送时把 design.md 文本当 .png 发（文件名 .png、内容 markdown，
  files/outgoing 实测 3 个"截图"文件头为 `# 总设计`）。
- 03 改进任务循环失败：`Agent CLI not found: 'cline'`——planner 的 agent 健康过滤只查 exec_module
  （Python 模块），CLI agent（cline/skyvern/julius-ai）全部放行，planner 选到未安装 agent。
- 任务 prompt 多为"提出方案并输出报告"，规划天然只写文档，不执行代码。

### 修复（4 处）

1. **截图输出路径**（v2/browser.py atomic_browser_screenshot）：save_path 缺省时默认
   `{working_dir}/screenshot_{ts}.png`（任务工作目录），不再落 /tmp。
2. **推送内容校验**（__main__.py `_correct_extension`）：按文件魔数纠正扩展名——
   md 文本带 .png 名 → 自动改 .md；真 PNG/JPEG 保持。实测 5 个场景全对。
3. **planner agent 过滤**（planner/prompt_builder.py）：健康检查增强——python_api 查模块、
   CLI 查 which(命令)/exists(绝对路径)、`python -m` 查模块。实测 cline/skyvern/julius-ai/
   cognitive-kernel 被过滤，剩 14 个健康 agent。
4. **落地执行引导**（prompt_builder 规划指南 + research_loop 下一步生成）：
   - 规划指南新增"落地执行原则"：方案必须建立在实际执行之上、禁止只输出方案不落地、
     分析类任务首步读真实代码、截图保存到工作目录。
   - research_loop 下一步生成要求"必须包含实际执行动作"，上轮方案/假设用真实运行验证。

### 附带修复

- prompt_builder 新增规则文本中误用 ASCII 双引号导致语法错误（报错在 575 行迷惑性位置），
  已换为中文引号「」。

### 验证

- 4 个改动文件全部编译通过；5 实例重启正常。
- 截图/推送/过滤的单元测试通过；落地引导需下一轮任务实测观察。

---

## 2026-08-21（续10）— C3 增强：缺口自动检测 + 自动补缺执行

### 新文件 partner/evolution/gap_filler.py

- `detect_tool()` / `detect_all()`：检测工具是否就绪（external/tools/ 与 PATH），覆盖
  plink/iqtree/bcftools/samtools/mafft/muscle/seqkit/prokka 等 8 个。
- `fill_gap()`：自动补缺三态——`already_present`（已就绪）/ `filled`（自动下载官方二进制，
  支持 plink、iqtree 的已知源）/ `manual_required`（需 sudo/conda，如 prokka，给明确命令）。
- 所有补缺动作记录 `state/logs/gap_fill_log.jsonl`（时间/工具/状态/信息）。

### 新 Harness 事件 ensure_tool（partner/v2/gap_events.py，注册进 v2 事件表）

- 参数 tool；返回 {ok, status, path, message}。任务执行前可调用确保依赖就绪。
- v2 事件总数 52 → 60（含 ensure_tool）。

### capability_inventory 渲染增强

- 学习计划的补缺动作后自动标注工具就绪状态：
  "工具状态[prokka:未检测到, bakta:未检测到]"——哪天装了 prokka，清单自动变"已就绪"。

### 验证（2026-08-21 实机）

- detect_all：7/8 工具已就绪（prokka 未检测到，符合实际）。
- fill_gap：iqtree→already_present、prokka→manual_required（含 apt/conda 命令）、未知→unsupported。
- ensure_tool 事件：bcftools→已就绪、prokka→manual_required、缺参数→报错。gap_fill_log 正确记录。
- capabilities.md 学习计划含工具状态；5 实例重启正常。

---

## 2026-08-21（续9）— C1/C2/C4 自进化机制（评估器 + 失败反思库 + 技能卡片）

### C1 质量评估器（新文件 partner/evolution/evaluator.py）

- `evaluate_outputs()`：产出量化打分（满分 100）：文件产出 40 + 非空 20 + 非模板 20 + 实质内容 20。
  实测：好文件 100、空文件 60、模板文件 80、无文件 0。
- `record_quality_score()`：每轮分数写 `{workspace}/state/logs/quality_scores.jsonl`。
- 接入 `research_loop.py` Gate 3（`_record_eval`）：每轮有/无产出都打分记录。

### C2 失败反思库（Reflexion 式）

- `record_failure()`：低分（<50）、连续无产出、重复循环等失败自动沉淀到 `state/logs/failure_reflections.jsonl`。
- `load_recent_failures()`：`_generate_next_task` 生成下一步时注入最近 3 条失败教训
  （"【最近失败教训（本实例，务必避免重蹈）】"），避免重蹈覆辙。

### C4 技能卡片（Voyager 式成功沉淀）

- `record_success()`：高分（>=60）且有产出时，把「任务→摘要→产出文件」沉淀到 `share/mind/skill_cards.jsonl`（跨实例共享）。
- `load_recent_successes()`：生成下一步时注入最近 2 条可复用成功经验。
- 测试：记录/读取/按实例过滤均正常。

### 验证

- 单元测试全部通过；5 实例重启正常。
- 效果：进化的北极星（分数）+ 记忆闭环（失败教训 + 成功经验都注入决策），形成 C1→C2/C4 的自进化循环。

---

## 2026-08-21（续8）— C3 缺口→补缺动作闭环

- `capability_events.py` 新增 `_GAP_REMEDIATION` 补缺动作库（缺口关键词 → 具体动作：
  工具名、安装方式、来源、或"已由 XX agent 覆盖"的说明）与 `_find_remediation()`。
- `_derive_learn_plan` 生成的学习计划从空话（"安排后续学习"）变为可执行清单
  （如"基因组注释 → 建议集成 prokka（apt install prokka 或 conda -c bioconda prokka）或 bakta"）。
- 验证：capabilities.md 学习计划已带具体补缺动作；gaps=1（仅基因组注释）。
- 效果：缺口清单成为"缺口 → 动作"闭环的第一环——接任务遇缺时可直接按动作补，无需人工决策。

---

## 2026-08-21（续7）— B 缺口消化：新增 5 个生信 Agent（gaps 19 → 1）

### 新增 Agent（wrapper + manifest，全部实测通过）

| Agent | 工具 | 能力 | 安装方式 | 实测 |
|-------|------|------|----------|------|
| enrichment | gseapy 1.1.13（cytobridge env 已有） | pathway_enrichment / gsea / ora | 零安装 | 15 基因 → 163 通路 → 119 显著 |
| plink | PLINK 1.9 官方二进制 | gwas / association_analysis | 官方 zip 解压 external/tools/plink/ | 模拟 20 SNP 检出 8 显著 |
| iqtree | IQ-TREE 2.4.0 官方二进制 | phylogeny / maximum_likelihood | 官方 tar.gz 解压 external/tools/iqtree/ | 8 序列建树 1s，Newick 输出 |
| bcftools | bcftools 1.19 | variant_calling / vcf / snpcalling | apt download + dpkg -x（无 sudo） | 10 位点统计 + 过滤 VCF |
| diffexp | scanpy 1.11.5（cytobridge env 已有） | differential_expression / deg | 零安装 | 模拟 400 细胞正确检出差异基因 |

### 原则（延续上轮教训）

- 每个 wrapper 都先**真实调用验证**再注册能力，不凭描述。
- 二进制工具（PLINK/IQ-TREE）放 workspace external/tools/，不污染系统；bcftools 用 apt 下载+dpkg -x 免 sudo。
- GATK 缺口由 bcftools 的 variant_calling 覆盖（token 匹配），不再单独集成重型 GATK。

### 剩余缺口

- 仅剩「基因组注释」：prokka 依赖 perl/bioperl 生态，当前无 sudo 环境 apt 装不了、conda solver 冲突；
  留待有 sudo 权限或 conda 修复后处理（备选：bakta / eggNOG-mapper，均需 conda）。

### 验证

- capabilities.md：agents 13 → 18，gaps 19 → 1。
- 5 实例重启正常。

---

## 2026-08-21（续6）— 纠正：cytobridge 差异表达能力声明撤回（依据不足）

### 错误与纠正

- 上一轮曾给 `partner/agents/manifests/cytobridge.json` 补 `differential_expression` 能力词，
  依据仅是 manifest 的 description_for_planner 文本（"驱动基因→差异表达"）。
- 用户质疑后实机查证 `/mnt/e/work/CytoBridge/CytoBridge-agent-release-runtime-v2-20260309/`：
  - `cytobridge_agent/tools/downstream_analysis_toolkit.py`、`scanpy_tools.py`、`tool_catalog.py`、
    `CytoBridge/tl/downstream/` 中均无 wilcoxon / rank_genes / deseq / gsea / enrichment 实现
    （仅一处 SDE 关键词巧合命中）。
  - **结论：cytobridge 实际不支持差异表达与富集分析，能力声明属无依据夸大，已撤回**
    （内置 manifest 与 workspace config/agents/ 副本均移除该词）。
- capabilities.md 重新盘点：gaps 回到 9（差异表达分析、DESeq2 缺口恢复）。

### 教训（写进自进化纪律）

- manifest 的能力词必须以**代码实证**为准（grep 工具实现），不能凭描述文本/推断补充。
- 任何"补能力"动作前先跑 `grep -rn "wilcoxon|rank_genes|deseq|gsea" <agent源码>` 验证。

---

## 2026-08-21（续5）— 能力缺口误报根因修复（gap 19 → 7）

### 根因（两个，均导致"有能力的 Agent 被误报为缺口"）

1. `self_review.py:identify_gaps` 用**精确集合匹配**（`ec in all_covered_caps`）判断工具覆盖，
   但 agent 能力是复合词（`blast_search`、`protein_structure`、`single_cell`），关键词永远匹配不上
   → AlphaFold/DiffDock/Scanpy/BLAST/Rosetta/CellChat/Seurat/GROMACS 全部误报。
2. `self_review.py:_derive_weaknesses` 用**子串匹配但分隔符不一致**（`"single cell"` 空格 vs `"single_cell"` 下划线）
   → "缺少'单细胞分析'覆盖"等误报。

### 修复

- 新增 `SelfReview._cap_tokens()`（能力名/关键词 → 小写词 token 集合），两处判定统一改为
  **词 token 交集**：blast→blast_search、protein→protein_structure、single cell→single_cell 均视为覆盖。
- 内置 `partner/agents/manifests/cytobridge.json` 补 `differential_expression` 能力声明
  （其 wrapper 描述明确含"驱动基因→差异表达"），消除"差异表达分析"误报。
- 注：`AgentRegistry` 发现顺序为内置 → workspace → 用户，workspace config/agents/ 的同名 manifest
  不覆盖内置，能力词需改内置副本。

### 验证（2026-08-21 实机）

- `identify_gaps` 缺口从 19 → 7，剩余均为真实缺口：GATK、通路富集分析、系统发育分析、基因组注释、
  变体调用、PLINK、IQ-TREE（确实无 Agent 声明对应能力）。
- capabilities.md 已重新生成（agents=13 / skills=9 / gaps=7）。
- 5 实例重启正常。

---

## 2026-08-21（续4）— Workspace 结构整理：共享目录收拢 + 实例目录瘦身

### 共享目录收拢到 share/（代码 + 数据同步）

| 旧路径 | 新路径 | 代码改动 |
|--------|--------|----------|
| `shared_knowledge/` | `share/knowledge/` | `research_loop.py:_shared_knowledge_root()` |
| `shared_mind/` | `share/mind/` | `research_guardrails.py:_shared_mind_dir/_shared_user_dir()`、`research_memory.py:_shared_path()` |
| `shared_projects/` | `share/projects/` | `project_registry.py:shared_projects_base()`（核心）、`rule_loader.py`、`project_state.py:_workspace_from_project_dir()`（新路径 + 旧路径兼容）、`workspace_layout.py` 4 个 legacy 函数 |

### 实例目录瘦身

- 删除实例级 `conf/`（符号链接，指向实例 config/）与 `config/`（无活跃读取，config 统一根级）；config 内 hypotheses/reports/rounds 等历史数据**归档**到 `share/_legacy_config/instances/<id>/`（不丢失）。
- 实例级 `shared_projects/`（OODA 路径 bug 残留的 molgen_exploration 产物）→ 归档 `share/projects/_legacy_instances/<id>/`。
- 删除 `instances/03/system/hermes_home/`（1 文件）与 `instances/05/system/hermes_home/`（2855 文件，207MB+，NEVER used 垃圾）。
- 删除 workspace 根残留：`test_files/`（7 个测试文件，无引用）、`_execute_code.py`（execute_code 运行产物）、`daemon.log`（0B）。

### 保留（有活跃引用，不可删）

- `digest_state.json`：`scripts/partner_digest.py`（周报 cron）状态文件，在用。
- 实例级 `partner_data/`：**活跃数据**——systemd 未设 `PARTNER_DATA_DIR`，`get_partner_data_dir()` fallback 到 `{workspace}/partner_data`，5 个实例的 learning.db 每日写入（根级 partner_data/learning.db 已 8 天未动）。删除会丢学习记录；若要统一到根级需设 PARTNER_DATA_DIR + 数据合并迁移（独立任务）。
- 实例级 `dialogue/ state/ system/`：实例隔离运行时状态（desktop_inbox、active_plan、agent_sessions、hermes_work 等 150+ 处代码引用含 GUI），收拢到根级 `state/<id>/` 需改 150+ 处，风险高，保持现状。
- 实例级 `ooda_data/`：OODA 引擎 RL 学习库（`ooda_engine.py` 直接读写），活跃。

### 验证（2026-08-21 实机）

- 路径函数实测：`shared_projects_base → share/projects`、`_shared_knowledge_root → share/knowledge`、`_shared_mind_dir → share/mind/system`、`_workspace_from_project_dir` 新旧路径均正确解析。
- 5 实例重启后全部 Bot ready，无 ImportError/ModuleNotFound。
- 代码中旧目录名仅剩 docstring/注释（已清理 # 注释 2 处）。

---

## 2026-08-21（续3）— API 统一管理与调用日志

### 新增：workspace config/api.json 统一管理 API 凭证

- 位置：`{workspace_root}/config/api.json`（workspace 为用户数据目录，不入 git；每个用户/部署各自维护）。
- 结构：`apis.<服务名> = {base_url, api_key, model, 备注}`；deepseek=对话模型，qwen=图片相关（`vision_model` 看图 / `model` 文生图）。
- 读取方：`partner/adapters/direct_api.py`（deepseek：`_resolve_api_json()` 惰性读取，fallback .env/环境变量；base_url 自动剥尾部 /v1 避免双 /v1 404）、`partner/adapters/adapter.py`（qwen：`_load_qwen_vision_cfg()`）。
- 改 api.json 后需重启实例生效。

### 新增：API 调用日志（`partner/api_log.py`）

- 写入：`{workspace_root}/state/logs/api_calls.jsonl`，每行一个 JSON（ts/api/model/base_url/purpose/status/elapsed_ms/prompt_chars/response_chars/error/instance）。
- 覆盖：deepseek 全部调用路径（成功/HTTP 错误/硬超时/fallback 成功/异常）与 qwen 视觉调用（逐张图成功/失败）。
- 日志失败只降级 debug，不影响主流程。

### 图片分析改走 Qwen 视觉模型

- `HermesAdapter.chat_with_images` 优先直连阿里云百炼 qwen（OpenAI 兼容端点，图片缩放 ≤1200 宽，实测 qwen3-vl-flash 1.5s 看图成功）；失败回退原 Hermes CLI 多模态路径。
- 实测：qwen-image-3.0 为文生图模型，看图请求超时（90s+），不适用于视觉理解；看图用 `qwen3-vl-flash`。

### 验证（2026-08-21 实机）

- direct_api 真实调用 deepseek-v4-flash → "1+1等于2。"（2.1s，日志 status=ok）。
- chat_with_images 真实调用 qwen3-vl-flash 分析测试图 → 正确描述内容（1.9s，日志 status=ok）。
- api_calls.jsonl 正确区分两个 API 与成功/失败状态；5 实例重启后全部正常。

### README 更新

- 按最新功能重写：核心功能表新增 API 统一管理、API 调用日志、能力盘点、强制总设计、深度研究循环、自进化与自愈、沙箱验证、浏览器自动化等；新增架构概览与外部知识借鉴章节。

# Partner 变更日志 (Change Log)

## 2026-08-21（续2）— 纠正"虚假迭代"根本问题

### 现象（严格评价实测发现）

- 03 实例 research loop 跑到 5 轮（表面达标），但 r1/r2/r3 报告 **md5 完全相同**（16237B 逐字一致），"深入迭代"是虚假的——每轮重复生成相同内容。

### 根因（两条，均非补丁能解决）

1. `load_latest_knowledge` 取报告**前 2000 字符**，而报告开头是 force_design 写的固定"总设计→目标→现状"框架，每轮不变 → 每轮注入相同摘要。
2. `_generate_next_task` 用**固定角色模板**（"继续深度研究（第N轮）…"），没有基于上一轮成果生成具体的增量指令。

两者叠加形成死循环：每轮生成同样报告 → 归档同样开头 → 下一轮注入同样摘要 → 再生成同样报告。

### 根本修复

1. `load_latest_knowledge`：改为取报告**后半部分**（增量：方案/结论/发现），开头保留 400 字作上下文。
2. `_generate_next_task`：改为 **LLM 驱动**——把上一轮成果（增量部分）喂给 LLM，生成"具体的、有增量的下一步"（禁止泛泛的"继续分析"，必须具体到对象和问题）；LLM 不可用时 fallback 到旧模板。
3. `on_task_done` / executor 调用处：传入 `adapter=_adapter`，让 research loop 能调 LLM。
4. `batch_planner.py` 的 `force_design`：**只在第一轮写总设计**——research loop 后续轮次（title 带 `_rN`）跳过 write_design，避免每轮重复写"总设计→目标→现状"固定框架。

### 验证（2026-08-21）

- `load_latest_knowledge(03)` 现在返回结尾含"方案设计/核心思路"的增量内容（修复前是纯开头"目标/现状"）。
- `_generate_next_task` 变 async，`on_task_done` 签名含 adapter。

---

## 2026-08-21（续）— 实例深入迭代修复

### 修复 web_search 的 LLM 调用挂起（`partner/adapters/direct_api.py`）

- 根因：`requests.post(timeout=...)` 在网络挂起条件下（连接建立后服务器不返回、DNS 偶发挂起）不生效，01 实测挂起 45 分钟远超 600s timeout。
- 修复：新增 `_post_hard_timeout()`，用 `ThreadPoolExecutor + future.result(timeout+20)` 做第二道硬超时，超时后强制返回空串（后台线程泄漏可接受，远好过整个事件循环被卡死）。同时 timeout 改为 `(30, timeout)` 元组（connect 30s / read timeout）。

### 修复浏览器 selector 盲猜（`mind/executor.py` + `prompts/reflect_patch.txt`）

- 根因：reflect 生成补丁时 LLM 看不到实际页面 DOM，盲猜 selector（如直接找 `input[placeholder*='手机号']`，而小红书首页需先点"登录"才出现输入框）。
- 修复：`_run_root_cause_diagnosis` 检测到 browser 步骤失败（wait_for_selector/Element not found/Timeout）时，先 `atomic_browser_extract` 提取当前页面 body 内容，注入 reflect prompt 的 `{page_content}`；模板加规则"必须基于页面实际内容生成 selector，禁止盲猜"。

### 修复 reflect 阻断研究循环（`mind/executor.py`）

- 根因：reflect 判定"【需询问用户】"break 后，batch_plan 完成时不 enqueue stop_project（completed_with_delivery=False），研究循环 on_task_done 不跑，实例"跑一会就停"。
- 修复：reflect "需询问用户" break 前，显式 `_enqueue_stop_project_event()`，确保 stop_project → research loop 继续自主迭代。

### 修复内容收集类任务不循环（`mind/research_loop.py`）

- 根因：`_RESEARCH_KEYWORDS` 缺"收集/整理"类词，05 的"收集...整理成清单"被判定为一次性任务跳过循环。
- 修复：补上"收集、整理、汇总、归纳、梳理"。

### 验证（2026-08-21）

- `should_loop("...内容收集...整理成清单...")` → True（修复前 False）。
- `_post_hard_timeout` 就位，`direct_api.chat` 主请求 + fallback 均走硬超时。

---

## 2026-08-21 — 浏览器自动化修复 + 研究循环迭代修复

### 修复 harness 三个 bug（`partner/mind/harness.py`）

| Bug | 根因 | 修复 |
|-----|------|------|
| handler 异常被误判成功 | retry 循环 except 只 break 不设 result，result=None 被 `if not isinstance(result, dict)` 转成 `{"ok":True,"content":None}` | except 里设 `result={"ok":False,"error":...}` |
| v2 事件失败检测不到 | browser.py 返回 `status` 字段，harness 只认 `ok` 字段 | 加 status→ok 转换 |
| Playwright Sync API 报错 | Sync API 在 asyncio 事件循环里无法运行 | sync handler 用 `asyncio.to_thread` 跑 |

### 修复浏览器 SIGTRAP 崩溃（`partner/v2/browser.py` + 新增 `browser_worker.py`）

- **根因**：chromium 在 Partner 主进程（systemd 服务 + 51 线程 + 长驻事件循环）及其 fork 出的任何子进程里稳定 SIGTRAP。8 轮对照实验逐一排除：环境变量、cgroup、栈限制、OpenBLAS/numpy/scipy/cv2/rdkit、Sync vs Async API、线程数、NTFS cwd、LD_LIBRARY_PATH、DISPLAY。结论是 fork 链继承的某种深层状态所致。
- **解决**：浏览器操作改用 `systemd-run --user` 启动独立 `browser_worker.py`（Playwright **Async API**），让 chromium 运行在 systemd 直接 fork 的干净进程里，彻底脱离 Partner fork 链。
- browser.py 的 9 个原子 handler 改成 subprocess 调度 worker；会话用 `launch_persistent_context` 持久化 profile 到 `/tmp`（避开 NTFS）。

### 修复研究循环知识承接（`partner/mind/research_loop.py`）

- `OUTPUT_REQUIRED_TYPES` 缺 `"01"`，导致 01 实例从不归档知识（shared_knowledge 恒空），每轮"从零开始"无法承接迭代。修复：补上 `"01"`，5 实例全部归档。

### 验证（2026-08-21 实机）

- browser_open 成功打开小红书（url=https://www.xiaohongshu.com/explore，title 正确）。
- 调度链路 3/3 PASS：worker open / worker 真 PNG 截图 / systemd-run 调度。
- 其余实例：小红书账号已配置（04），登录流程 selector 由 reflect 迭代修正中。

---

## 2026-08-21 — 强制写总设计 + 能力盘点（自我认知地基）

### 新增两个 Harness 事件（`partner/v2/capability_events.py`）

| 事件 | 用途 | 输出 |
|------|------|------|
| `capability_inventory` | 盘点能力（会什么/不会什么/需学什么） | `partner_data/capabilities.md`（共享，5 实例同读） |
| `write_design` | LLM 生成软件项目式总设计文档 | `shared_projects/<项目>/design.md` |

两者关系：接任务 → write_design 读 capabilities.md 作为"现状与能力"参考 → 照设计执行 → 能力清单可随时用 capability_inventory 刷新。

### 强制写设计机制（`planner/batch_planner.py`）

- `plan()` 生成计划后，若 `force_design`（默认 True，可经 `config/batch_planner.yaml` 关闭）则注入 `step_design`（event_type=write_design）到计划最前，并追加到所有步骤 `depends_on`，保证"先设计后执行"。
- 设计文档路径由 handler 用 `ctx.project_dir` 解析（= shared_projects/<title>/）。

### 修复 self_review.py 两个统计 bug

- `_collect_event_types`：原 import 不存在的 `partner.harness.event_registry`（静默返回空），改为 `partner.mind.harness.default_registry` → 事件数 0 → 102。
- `_count_skills`：SkillRegistry 是纯内存注册表从不读 db（恒 0），改为先查 `skills_registry.db` 的 skills 表 → 技能数 0 → 9。

### 验证（2026-08-21 实机）

- 注入测试消息，active_plan.json 显示 step_design(write_design) completed 31.7s，后续步骤 summary 依赖含 step_design。
- design.md 14KB（含技术路线图、能力缺口引用）；capabilities.md 统计 agents=13 / skills=9 / events=102 / gaps=19。

---

## 2026-08-13 — 任务类型判断 + 深度研究闭环

### Research Loop 改为按任务类型判断是否循环

**问题**: 之前 Research Loop 对所有 stop_project 无条件进入循环，导致截图/列目录等一次性任务也被拖进循环（如 01 连续截图 14 次触发限制）。

**修复** (`research_loop.py` `should_loop`):
- 新增 `_RESEARCH_KEYWORDS`（研究/分析/对比/深入/实现/探索/benchmark 等）和 `_ONESHOT_KEYWORDS`（截图/列表/发送/查询/状态等）
- `on_task_done` 首次调用时用 `should_loop(user_request)` 判断：研究类 → 循环；一次性动作 → 直接 return False
- 研究意图优先：即使消息同时含"列出"，只要主意图是研究就循环
- 默认不循环（保守）

**验证**: 01"截图当前桌面"正确跳过循环；03"研究 targetdiff"正确进入循环（round=1/5 → 归档 → enqueue）。14 个分类用例全部通过。

### 03 实际运行外部代码（深度研究闭环）

- 修复 targetdiff numpy 2.x 兼容性（`np.int`/`np.long`/`np.bool` → `int`/`np.int64`/`bool`，12 处）
- 修复 `harness.py` `_local_execute_code` 返回 `content` 字段（让 `$step_X.result.content` 能引用真实 stdout）
- 03 execute_code 真实运行 `parse_sdf_file`，产出含真实数据（56 原子/118 键/8 维特征/真实 SMILES）的 benchmark_report.md
- 限制：完整 benchmark 复现需 GPU/权重/torch_scatter，当前环境不可行，已如实记录

### 周报 cron job

- 每周一 9:00 汇总 shared_knowledge/ 生成 weekly_report.md（job_id 34d33d1c98d8）

### shared_knowledge 改为保留历史版本

**问题**: 之前归档是覆盖式的（文件名固定，新报告覆盖旧报告），`latest/` 只留最后一版，v1→v2→v3 历史丢失，循环在浅层重复。

**修复** (`research_loop.py`):
- `archive_outputs` 加 `round_num` 参数，归档文件名带轮次后缀（`analysis_r2.md`）
- `on_task_done` 调用时传 `state.round`
- `load_latest_knowledge` 改为按轮次号（`_rN` 正则提取）取最大，读最新轮次
- history.jsonl 记录加 `round` 字段

**验证**: round=2/3 归档为 `analysis_r2.md`/`analysis_r3.md`，`load_latest_knowledge` 正确取到 r3。

---

## 2026-08-12 (evening) — Research Loop 上线

### 新增: research_loop.py — 替代 OODA 的自主研究循环

**背景**: OODA 引擎因 desktop_inbox 注入 + polling loop 竞态 + CircuitBreaker 复杂度过高被删除。
需要一个新的自主循环机制，避免相同 bug。

**设计原则**:
- 不经过 desktop_inbox，直接 enqueue 到事件队列
- 质量门控：最大 5 轮、多样性检查（同类型连 3 次停）、产出验证
- 实例差异化：每个实例有自己的研究方向

**文件**: `partner/mind/research_loop.py`（新建，190 行）

**集成点**:
- `executor.py` `_handle_stop_project`：task 完成时调用 `on_task_done()`
- `executor.py` `_handle_user_message`：新消息时调用 `reset()` 重置循环

**验证**: 03 实例 5 轮自主循环正常，enqueue 确认

### 深挖修复: 产出验收 4 个根因 bug

**Bug 1: expected_artifacts 不同步**
- 现象: `[CHECK] expected_artifacts missing: *.md` 即使文件已产出
- 根因: `_ensure_write_artifact` 改 `micro_plan.expected_artifacts`，但 `task.expected_artifacts` 在 plan 前就已从 event payload 赋值，两者从不回写同步
- 修复: `executor.py` plan 生成后 `task.update_expected_artifacts(micro_plan.expected_artifacts or [])`

**Bug 2: 写路径错误**
- 现象: `atomic_write_artifact` 写到 `/mnt/e/work/.../external/<项目>/xxx.md`，验收查 `state/tasks/<uuid>/`
- 根因: LLM 从用户消息的绝对路径推导输出路径
- 修复: `executor.py` plan 执行前规范化所有绝对写路径到 `task.working_dir`

**Bug 3: 文件检测用 UUID 排序**
- 现象: Research Loop 的 `files=[]` 即使磁盘有文件
- 根因: `sorted(os.listdir(tasks_dir), reverse=True)` 按 UUID 字符串排序 ≠ 时间排序
- 修复: `sorted(..., key=os.path.getmtime, reverse=True)`

**Bug 4: last_outputs 摊平**
- 现象: `files=['analysis.md']` 已检测到但 `_has_output_this_round` 仍返回 False
- 根因: `state.last_outputs = old + files` 把字符串摊平成扁平列表，`[-1]` 是字符串不是 list
- 修复: `state.last_outputs.append(list(files or []))` 保留嵌套结构

**验证结果 (02/03/04)**: 三个研究型实例连续多轮自主循环，每轮 files 正确检测、自动生成下一步。01(截图)/05(工具)任务性质不适配简单循环，已接受现状。

### 接入 shared_knowledge 累积知识库

**目标**: Sprint 8 P1 — 每轮产出归档 shared_knowledge/，下一轮基于上一轮继续，实现 v1→v2→v3。

**实现** (`partner/mind/research_loop.py`):
- `archive_outputs(instance_id, workspace, files)` — 每轮产出归档到 `shared_knowledge/{id}/latest/`，过滤 task_instance.json/task_log.jsonl/_step_* 等元数据，追加 `history.jsonl`
- `load_latest_knowledge(instance_id, workspace)` — 读取 latest/ 下最新 .md 的摘要（截断 2000 字）
- `_generate_next_task` 注入 `【上一轮成果摘要】` 到新任务 prompt，实现累积演进
- `on_task_done` 新增 `workspace` 参数；executor 传 `_workspace`

**验证**: 03 实例完整闭环 — round=1 archived 1 files → injecting prior knowledge (2006 chars) → 第二轮任务携带上一轮摘要。

### 深度研究闭环: 03 实际运行外部代码

**目标**: Sprint 8 P0 — 03 从"读代码分析"升级到"真正运行代码"。

**发现并修复的兼容性 bug (targetdiff numpy 2.x)**:
- `np.int` → `int`、`np.long` → `np.int64`、`np.bool` → `bool`（共 12 处，`utils/data.py` + `datasets/protein_ligand.py`）
- 这些是 numpy 1.20+ 弃用、2.x 彻底移除的别名

**execute_code 内容引用修复** (`harness.py` `_local_execute_code`):
- 根因: execute_code 返回 `stdout`，但 `atomic_write_artifact` 引用 `$step_X.result.content`，字段不匹配导致报告内容为空、write 步骤被判定依赖失败跳过
- 修复: 返回 dict 加 `"content": stdout`，让模板解析能取到真实运行结果

**验证**: 03 execute_code 真实运行 targetdiff `parse_sdf_file` 解析 `examples/3ug2_ligand.sdf`，产出含真实数据（1 分子、56 原子、118 键、8 维特征、真实 SMILES）的 benchmark_report.md。QQ 推送成功。

**限制**: TargetDiff 完整 benchmark 复现（训练/采样）需 CUDA GPU + 预训练权重 + torch_scatter + CrossDocked 数据集，当前 WSL 环境无 GPU/权重，不可行。已如实记录，不做虚假声称。

---

## 2026-08-12 (afternoon) — harness 架构加固 + QQ Bot 修复

### QQ Bot 修复

**问题: 所有 Bot Token refresh 失败 (code 100002)**
- 根因: `qq_config.json` 中 `app_id` 为整数，QQ API 要求字符串
- 修复: 所有实例 app_id 改为字符串 `"1904095253"` 格式

**问题: WebSocket INVALID_SESSION 后不重连**
- 修复: `_on_ws_message()` 抛异常触发重连
- 文件: `shells/frontend/qq_bot/qq_official_bot.py`

**问题: 02/04/05 缺少 qq_user_context.json**
- 修复: 用户发 QQ 消息自动重建 openid 映射

### harness 架构增强

| 改动 | 文件 |
|------|------|
| ProjectProber — 自动探测项目结构 | harness.py |
| _ensure_write_artifact — LLM 忘 write 时兜底 | batch_planner.py |
| MicroPlanner 支持 probe_dir + step_failures | harness.py |
| PlanExecutor 返回 step_failures | harness.py |
| 产出验证 — 检查 expected_artifacts | harness.py |
| prompt_builder 接受 probe_results | prompt_builder.py |
| BatchPlanner JSON 兜底计划 | batch_planner.py |
| executor 自动探测项目路径 | executor.py |
| _event_completion_receipt_local 加 sanitize | executor.py |

### 清理
- OODA 引擎删除（__main__.py + executor.py）
- polling loop 删除（qq_official_bridge.py）
- 消息重复、HTML `<img>`、stop_project 泄漏修复

### 验证结果
5 实例全部产出文件 + QQ 推送正常

---

## 2026-08-08 — 截图路径统一

截图路径统一到 `{PARTNER_DATA_DIR}/screenshots/`，涉及 7 个文件。

---

*最后更新: 2026-08-21*

## 2026-08-22 — 自进化追踪真实性恢复（Codex 审计）

### 文件推送语义

- 根因：`atomic_push_files` 仅向 `delivery_queue.jsonl` 追加记录，却把日志写入解释为 QQ 已发送。
- 修复：统一调用运行时文件发送回调；只有活动渠道确认才返回 `status=sent`、`pushed=1`。
- 自动发现范围缩小到当前任务目录；旧任务产物必须显式指定，不能参与当前任务验收。

### 浏览器生命周期和前台模式

- 根因：worker unit 使用主进程 PID 命名，重启后不断产生孤儿服务；浏览器固定 `headless=True`，不可能显示给用户。
- 修复：每个实例使用确定性 `partner-browser-{instance}` unit；加入协议级健康检查、真实 close 和 WSLg 环境传递。
- `browser_open` 支持 `visible=true` / `foreground=true`，并调用 `page.bring_to_front()`。

### PDF 与验收

- 根因：CJK 字体对象未注册，ReportLab 失败后 minimal PDF 仍返回成功，导致中文变成 `?`、内容单行截断。
- 修复：注册 CJK 字体，支持分页、表格和等比例图片；生成失败直接返回失败，不再伪造 minimal PDF。
- `ArtifactValidator` 只接受当前任务目录内的产物并记录 provenance。

### 运维与安全

- 删除 QQ token 请求/响应的敏感调试日志；systemd unit 不再内嵌模型密钥。
- 新增持久化 pause/resume 控制；systemd 改为仅异常退出时重启。
- 测试发现并修复 `gap_filler` 未定义变量/错误提前返回，以及长任务模型路由回归。
- 文件发送增加 5 分钟内容签名去重：显式 `push_files` 已获确认后，最终 one-shot report 不再重复发送同一版本文件。
- 回归：78 项测试通过；可见浏览器、中文 PDF 已完成独立实机验证。
### 2026-08-22：前台登录通知与详细 PDF 质量门槛

- 新增 `open_browser_foreground_and_notify`：必须同时确认可见浏览器已置前、worker 保持运行、用户消息真实送达；旧 `open_login_on_confirm` 改为复用该事件。
- `send_user_text` 不再写 `delivery_queue.jsonl` 冒充成功，改走运行时用户通道，并以 `delivered=true` 为成功依据。
- 对 5 分钟内已确认送达的相同文字提示做进程内去重，避免规划迭代重复打扰用户。
- 新增 `generate_detailed_pdf`：默认要求有效正文不少于 1200 字、至少 4 个章节、至少 2 类证据信号；质量失败标记为不可机械重试。
- 修复 Harness 对同步 auto-repair handler 直接 `await` 的错误；事件可用 `retryable=false` 阻止相同坏参数重复执行。
- 真实验收：01 前台打开小红书并获得消息发送确认；02 生成 12894B、3 页、7 章节详细报告，随后获得文件发送确认。

### 2026-08-22：登录续跑与有意义自进化闭环

- 把“已登录”从普通对话改为协议消息：必须读取小红书真实页面词、Cookie 和登录墙信号；核验成功后自动排队下一步，失败则如实通知且不续跑。
- 新增 `xiaohongshu_open_publish_editor` 原子事务：前台打开创作平台、点击“上传图文”、核验图片上传控件、保存截图和 JSON；不再把只有侧栏的空壳页或猜测出的标题/正文框当成成功。
- 新增 `xiaohongshu_inspect_upload_requirements`：真实读取文件控件 `accept/multiple` 与页面上传文字，输出 JSON/MD；实机读取到 1 个控件和 16 条要求，截图显示 32MB、PNG/JPG/JPEG/WebP 与分辨率建议。
- 禁止 Markdown 产物再次递归注入“自动反思”任务；`strict_reflect` 不再暗中重复调度下一轮。自主循环只使用当前任务的绝对证据文件，并在消息中说明证据、评分、行为变化和已排队动作。
- 浏览器单页操作增加进程锁与计划依赖串行化，截图遵守显式文件名，消除 click/extract/screenshot 并发导致的空响应与错误重启。
- 新增两阶段 RDKit 实验：第一轮生成并评估 85 个有效候选；第二轮读取真实 CSV，计算 Bemis–Murcko 骨架多样性和 Morgan 指纹两两相似度。两轮均生成详细 PDF；第二轮实机 PDF 61260B 且文件发送回执为 `delivered=true`。
- 分子事件固定产物契约，规划器不能再虚构 `molecules.pdf/csv` 或在补救计划中重复运行同一基准。
- 阶段成功不再显示“已停止”，改为“阶段完成并判断自动下一步”；协议型研究进展优先于通用的一次性关键词判定。
- 回归测试新增登录验证、发布入口事务、上传要求、详细 PDF、真实发送语义、分子生成与结构多样性实验覆盖。

### 2026-08-22：逐步视觉回执与连续实验执行

- 01 在小红书发布流程的每个关键操作后截图，调用 `qwen3-vl-flash` 读图，再把图片附件和中文视觉说明通过真实消息通道发送；任一截图、读图或发送失败都不得报成功。
- 02 增加第三轮 SA/随机基线对照和第四轮 QED/SA 多目标选择；上一轮报告写出的下一步会直接入队并执行，不再只留在 Markdown 中。
- 第四轮只在 PDF 和候选 CSV 都获得发送确认后才结束；由于当前没有目标活性数据，流程会明确说明证据边界，而不伪造无限迭代。

### 2026-08-23：文档进度基线对齐

- 新增 `docs/current_status.md`，统一记录实例活性、已闭环能力、01/02 实机证据、已知边界和下一阶段优先级。
- 同步更新 README、self_awareness、skill、architecture_review、partner_code、Sprint 10 测试报告、自进化追踪指南和 evolution_journal。
- 明确区分历史 Sprint 结论与当前运行基线；移除 README 中已删除 OODA 仍作为当前触发器的错误说明。

### 2026-08-23：治理基础落地——文档、项目迭代和证据型自进化

- 让 `docs/` 与 `tests/` 退出 `.gitignore`，新增根级 `AGENTS.md`、机器目录
  `docs/catalog.yaml`、阅读顺序、改动协议、验真规则以及七类 JSON Schema。
- 规划和每个 Harness 步骤接入预算化上下文选择；保留来源、selection ID 和确定性回退，
  历史 L4 默认不加载。
- 新增 ProjectState / IterationReceipt / NextAction：写出“下一步”只算 proposed，
  收到真实 task ID 后才算 queued；后一轮强制承接前轮产物。
- 新增 Issue / EvolutionExperiment / PromotionDecision：问题按证据去重，改动先候选验证，
  只有成功标准和全量回归都通过才能 promotion，失败必须记录 rollback。
- 把 01/02 的连续流程改为声明式协议；项目累计轮次与协议局部步骤分离，允许完成后新周期
  继续追加历史。历史 01 两轮、02 四轮已迁移为治理收据。
- 新增五实例角色和最多双活动槽位硬门，启动入口和运维切换共同执行该约束。
- 新增 10 项治理测试；当前全量回归为 98 passed。


### 2026-08-23：实例健康与项目状态单页面板（partner_dashboard）

- 新增 `partner/monitoring/partner_dashboard.py`：纯确定性收集器，从
  `systemctl --user is-active`、`instances/<id>/state/heartbeat.json`、
  `instances/<id>/state/instance_runtime.lock`、
  `share/projects/<id>/governance/project_state.json` 和最近一份
  `IterationReceipt` 中读取事实，输出 JSON 快照；不调用任何 LLM，不写任何文件。
- 新增 `scripts/partner_status.py`：纯命令行入口，默认输出固定列宽文本面板，
  支持 `--json` 与 `--active-only`，可作为运维与 L1/L2 文档的快速证据入口。
- 新增 `tests/test_partner_dashboard.py`（7 项）：覆盖 5 个实例读取、active-only
  过滤、blocked 项目的 blocked_reason/resume_event 暴露、pytest 摘要解析、
  缺文件回退与时间格式化。
- 新增 `docs/testing/last_pytest.txt`：dashboard 据此显示最近一次 pytest
  通过/失败数。需在每次回归后更新或通过 CI 写入。
- 实机：`partner_control.py status` 与 `partner_status.py` 同时调用结果一致；
  全量回归从 98 passed 提升到 105 passed；不需要重启任何实例。
- 影响：未触动 18 个 M/17 个 ??的既有改动；只是新增 4 个文件，不修改任何已有
  Partner 业务文件。

### 2026-08-23：实例两两组合硬门测试 10/10 通过 + dashboard 角色映射修复

- 把五实例 (01–05) 两两组合全部跑过一次硬门切换（`switch` → `systemctl` →
  `heartbeat` → `dashboard`），共 10 个组合。`partner_control.py switch` 全部
  exit=0；`systemctl --user is-active` 立刻反映；新进程 PID 改变，`heartbeat`
  cycle 计数从0 重启；`instance_runtime.lock` 被新进程原子覆盖（不是死锁）。
- 实测发现并修复 3 个真实问题：
  - **role→project_id 硬编码**：`partner_dashboard._project_id_for` 之前
    把 "01→xiaohongshu_operations / 02→molecular_generation" 写死。现在改为
    从 `partner.governance.scheduler.ROLES` 读，新增角色自动生效（测试
    `test_project_id_for_uses_scheduler_roles` 覆盖）。
  - **03/04/05 governance 状态缺失**：`share/projects/` 下只有
    xiaohongshu_operations 与 molecular_generation 两个治理项目；dashboard
    因此显示 03–05 项目列空。用 `partner.governance.storage.save_project_state`
    原子写入三个新 project_state.json（status=paused, resume_event=
    user_slot_assignment），不假装已启动，仅作为“等待活动槽位”的真实占位。
  - **健康阈值缺少边界覆盖**：新增 `test_healthy_flag_flips_for_stale_or_crashing_instances`，
    验证 active+stale(>600s)→False、active+crash>0→False、inactive→False，
    并通过真实运行时把 02 heartbeat 改成 700s 前验证 dashboard
    `healthy=1/5 age=11m42s`，还原后 healthy 立刻回到 2/5。
- 测试基线：98 → 105（dashboard 初版）→ 107（roles 修复+paused 项目）→ **108 passed**。
- 实机确认 01/02 仍在活动槽（pid 269840/269841），`partner_status.py` 在 5 个
  实例上稳定输出真实数据；03–05 paused 项目清晰显示“等待活动槽位”，避免
  dashboard 让运维误以为这些实例已经启动。
- 没有伪造任何 receipt、没有改 18 个 M 既有改动；新增 1 个文件改动
  (`partner/monitoring/partner_dashboard.py` 12.6K → 13.0K) 和测试扩展。

### 2026-08-23：持久 Campaign Controller——从“进程在线”到连续项目/自进化运行

- 新增 CampaignState、WorkItem、InstanceLease、CampaignReport 四类契约及 Schema；状态持久化到
  `state/campaigns/{campaign_id}`，外部 Agent 退出或 Controller 重启后可以恢复。
- 新增 `partner/governance/campaign.py`、`campaign_storage.py`、`campaign_runtime.py` 和
  `scripts/partner_campaign.py`：支持创建、后台运行、状态、暂停、恢复、取消和按 tick 推进。
- Campaign 自动选择最多两个实例槽；dispatch 前写租约，只有 inbox 返回唯一 message/task ID 才 queued；
  task log 出现后 running，完成后核验产物和真实 delivery step 回执。
- Campaign marker 禁用旧 Research Loop 的内存续跑，项目下一轮通过 Receipt/NextAction 返回 Controller，
  避免两个循环重复入队。
- 修复旧 `enqueue_next_action` 在 callback 返回 None 时生成 `enqueue_ack_*` 的假回执；执行器现在返回真实 event ID。
- Watchdog 支持租约超时重试、失败预算、重启 reconcile、三轮相同事件/产物内容熔断和 Issue 记录。
- 真实发布/支付/购买/密码等敏感 WorkItem 自动 human_required；到达时间/任务/失败/模型/成本预算后
  只允许最终日报发送。
- dashboard 新增活动 Campaign 摘要；新增 5 个 Campaign governance events。
- 回归：123 passed。120 tick 确定性模拟 dispatch 237 项、最大槽位 2、0 失败、25 份报告、正常完成。
  该模拟不冒充真实 QQ/模型整夜 soak。

### 2026-08-23：Campaign 短程实机审计与严格验收收口

- 多轮真实 canary 驱动 01 打开已登录的小红书发布页、生成关键截图、调用视觉模型并走 QQ 文本/文件回调；未执行真实发布。
- 修复 Controller 把 `completion_status=done` 当最终完成的问题：恢复时必须等到后续 `iteration_llm_check.satisfied=true`。
- 修复 work-item 创建预算一到就让最终日报抢跑；现在先执行并排空已准入的主工作项。
- Campaign 总目标进入默认 WorkItem；重试 message ID 带 attempt；Campaign marker 不再被同标题内容去重吞掉。
- 删除“文件送达即覆盖全部验收为成功”的旧逻辑；真实交付仅作为验收证据之一。
- 明确文件名采用双层硬门，宽泛 `*.md` 不能替代；禁止把 PDF fallback 复制成 `.md`；补充 `$workdir`、列表索引、写文件事件别名和消息/截图安全降级。
- 取消 Campaign 会关闭未终态 WorkItem、释放 Lease、恢复原双槽；Dashboard 使用 receipt correction 后的有效最新 Receipt。
- 错误 canary 产生的 Receipt 通过追加 correction 失效，历史文件未删除，项目状态恢复到最后有效迭代。
- 全量回归 **132 passed**；120 cycle 模拟完成 241 个 WorkItem（122 ticks）、最大并发 2、0 失败、25 份报告。
- 实机整轮最终因 QQ 文件 API 间歇性连接失败取消，明确不宣称 30 分钟或整夜 soak 通过。
