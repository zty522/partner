\# Sprint 5: Loop+Harness 增强

**时间**: 2026-08-01

**目标**: 基于 8 篇外部 AI 技术文章，系统性完善 Partner 架构设计，新增 6
大模块体系

**外部知识灵感来源**:

1.  Loop+Harness
    清华自进化Agent综述（Harness形式化、快慢路径、5条进化路径）

2.  生产级Agent全景（Agent定位、Workflow vs Agent决策）

3.  AI立规矩：Rules体系（Rules/Tools/Skills三层分离、多层规则架构）

4.  PaperFlow 科研阅读闭环（论文策展、兴趣漂移追踪、反馈语义分离）

5.  CellOS 单细胞世界模型（JEPA架构、双视角观测）

6.  世界模型+Agent Nature正刊（DreamerV3、世界模型调用率\<1%分析）

7.  世界模型+强化学习 Nature（GigaBrain、WoVR）

8.  AI基因编辑+蛋白质工程 Nature（AF3接触概率、ProteinMPNN加固起点）

## 1. 设计内容

### 1.1 Harness 架构正式化

**灵感**: Loop+Harness 综述提出 Harness 是进化的核心载体，三代 Agent
演进（Task Loops → Cross-Task Reuse → Runtime Systems）。

**新增模块**:

  ------------------------------------------------------------------------------------------------------------------------
  **模块**                **文件**                                  **功能**
  ----------------------- ----------------------------------------- ------------------------------------------------------
  环境能力度量            \`harness_core/capability_model.py\`      三维评分：行动多样性×反馈密度×任务时长，计算进化上限

  进化载体                \`harness_core/evolution_carrier.py\`     快慢路径分离决策，自动跟踪 Harness 变更统计

  自修改接口              \`harness_core/harness_self_modify.py\`   Harness 自描述/自修改/快照回滚
  ------------------------------------------------------------------------------------------------------------------------

**设计细节**:

-   \`CapabilityScore\`: 使用调和平均数（harmonic
    mean）计算综合分，零值轴直接置零

-   \`EvolutionPath\`: FAST（技能/记忆/提示词/配置）vs
    SLOW（模型权重微调）

-   \`HarnessSnapshot\`: 时间点快照，支持最多 10 个历史版本回滚

### 1.2 Rules 规则系统

**灵感**: AI立规矩文章提出 Rules ≠
提示词技巧，而是一条新的工程规范交付链路。

**新增模块**:

  ---------------------------------------------------------------------------------------------------
  **模块**                **文件**                     **功能**
  ----------------------- ---------------------------- ----------------------------------------------
  规则类型                \`rules/\_\_init\_\_.py\`    Constraint/Preference/Convention/Prohibition
                                                       四种类型

  规则加载                \`rules/rule_loader.py\`     三层架构：Personal → Project →
                                                       Global，YAML/JSON/MD 三种格式

  规则注入                \`rules/rule_injector.py\`   注入 Planner prompt + 执行前 prohibition 检查

  默认规则                \`rules/defaults/\`          17条预设规则（coding 8 + research 5 + safety
                                                       4）
  ---------------------------------------------------------------------------------------------------

**设计细节**:

-   优先级覆盖：Personal \> Project \> Global，同层按 priority 数字升序

-   Markdown 格式支持：\`- \[MUST\]\` / \`- \[SHOULD\]\` / \`-
    \[NEVER\]\` / \`- \[BY DEFAULT\]\` 语法

-   \`RuleInjector.inject_rules_block()\`: 按
    prohibition→constraint→preference→convention 排序，与 goal
    关键词相关的规则提前

-   \`RuleInjector.check_prohibitions()\`: 执行前禁止规则检查，返回
    (allowed, reason)

### 1.3 Skills 自动进化

**灵感**: Loop+Harness 综述提出的技能三阶段闭环（创建→使用→进化）。

**新增模块**: \`evolution/skill_evolver.py\`

**设计细节**:

-   三阶段闭环：

-   **Creation**: 从 ≥3 个相似成功轨迹中提取新技能候选

-   **Detection**: SkillHealth 复合评分（40% 新鲜度 + 35% 成功率 + 15%
    使用频率 + 10% 依赖数）

-   **Improvement**: 从 failure patterns
    中匹配常见错误模式并生成修复建议

-   \`SkillHealth.health\`: 0-1 评分，\<0.2 为 stale，\>30天未使用也标记
    stale

-   已接入 \`self_evolve_engine.py\`：每个进化周期末尾自动执行
    \`run_cycle()\`

-   已接入 \`learning.db\`：通过 growth 和 experiences 表查询真实数据

### 1.4 三层记忆系统

**灵感**: Loop+Harness 综述提出的三层记忆（表示层/操作层/进化层）。

**新增模块**: \`memory/\_\_init\_\_.py\`（独立包）

**设计细节**:

-   **表示层** \`MemoryRepresentation\`: Raw Logs / Episodic Traces /
    Semantic Summaries 三级存储，SQLite 持久化

-   **操作层** \`MemoryOperations\`:

-   \`compress()\`: Raw \> 50条时，压缩最旧的为 episodic 摘要

-   \`merge()\`: 关键词相似度 ≥70% 的条目合并为 semantic

-   \`update_importance()\`: 动态调权

-   **进化层** \`MemoryEvolution\`:

-   \`should_forget()\`: 判断是否有低价值记忆该清除

-   \`prune()\`: 按 importance\<0.3 + age\>90天 清理

-   统一入口 \`LayeredMemoryStore\`: \`remember()\` / \`recall()\` /
    \`optimize()\`

### 1.5 科研论文策展闭环

**灵感**: PaperFlow（上海 AI Lab）的每日论文推荐闭环 + 兴趣漂移追踪。

**新增模块**: \`curation/\_\_init\_\_.py\`（独立包）

**设计细节**:

-   **Profiling**: \`PaperProfiler\` ---
    结构化画像（研究方向/主题权重/偏好方法/必读关键词）

-   **Recommending**: \`DailyRecommender\` --- 六信号排序：

-   主题匹配 40% + 作者先验 20% + 方法偏好 15% + 必读命中 10% + 漂移对齐
    10% + 新鲜度 5%

-   四层展示：must_read / high_relevant / maybe_interested /
    edge_relevant

-   **Adapting**: \`FeedbackLearner\` --- 四类反馈语义分离：

-   选中：强正信号→boost topic_weight +0.15

-   精读/保存：中正信号→boost +0.08 + 添加作者

-   跳过：弱负信号→decay -0.02

-   纠错：独立通道→仅影响指定topic

-   **Drift Tracking**: 漂移状态机 Stable → Observing → Shifting →
    Recovered

-   需 ≥5 次连续证据 + 7天窗口才触发 SHIFTING

### 1.6 生物信息学领域模块

**灵感**:
CellOS（单细胞JEPA）、Nature两篇（AF3接触概率+ProteinMPNN）、GEO数据检索。

**新增模块**: \`domain/\_\_init\_\_.py\`（独立包）

**设计细节**:

-   **SingleCellAnalyzer**: 标准 scanpy 流程封装

-   \`run_pipeline()\`:
    filter→normalize→HVG→PCA→neighbors→UMAP→clustering

-   \`quality_control()\`: 快速 QC 指标报告

-   **ProteinDesignTools**:

-   \`extract_contact_matrix()\`: AF3 接触概率矩阵提取（需 ColabFold）

-   \`stabilize_sequence()\`: ProteinMPNN 稳定性优化（需
    ProteinMPNN），fallback ESM 预测

-   **GEOCohortFinder**: NCBI E-utilities API 搜索，返回结构化数据集摘要

-   **CellWorldModelClient**: CellOS API 预留接口（待开放）

## 2. 集成点

本 Sprint 将新模块集成到现有管道：

  ---------------------------------------------------------------------------------------------
  **集成点**              **文件**                              **变更**
  ----------------------- ------------------------------------- -------------------------------
  Rules → batch_planner   \`planner/prompt_builder.py\`         新增
                                                                \`load_rules_for_prompt()\` +
                                                                \`build_prompt(rules=\...)\`
                                                                参数

  SkillEvolver →          \`evolution/self_evolve_engine.py\`   在 A 方案之后新增 P2
  self_evolve                                                   skill_evolver 钩子

  PaperCuration → cron    cronjob                               每日 9:00 自动运行 (job_id:
                                                                1901beb66320)
  ---------------------------------------------------------------------------------------------

## 3. 新增文件清单

  -----------------------------------------------------------------------------------------
  **文件**                                  **行数**                **状态**
  ----------------------------------------- ----------------------- -----------------------
  \`harness_core/capability_model.py\`      221                     ✅

  \`harness_core/evolution_carrier.py\`     176                     ✅

  \`harness_core/harness_self_modify.py\`   283                     ✅

  \`harness_core/\_\_init\_\_.py\`          49 (修改)               ✅

  \`rules/\_\_init\_\_.py\`                 67                      ✅

  \`rules/rule_loader.py\`                  267                     ✅

  \`rules/rule_injector.py\`                132                     ✅

  \`rules/defaults/coding.yaml\`            68                      ✅

  \`rules/defaults/research.yaml\`          37                      ✅

  \`rules/defaults/safety.yaml\`            30                      ✅

  \`evolution/skill_evolver.py\`            474                     ✅

  \`evolution/\_\_init\_\_.py\`             14 (修改)               ✅

  \`memory/\_\_init\_\_.py\`                518                     ✅

  \`curation/\_\_init\_\_.py\`              591                     ✅

  \`domain/\_\_init\_\_.py\`                517                     ✅

  \`planner/prompt_builder.py\`             +40 (修改)              ✅

  \`evolution/self_evolve_engine.py\`       +25 (修改)              ✅
  -----------------------------------------------------------------------------------------

**总计**: 18 个文件，约 3,500 行新增/修改代码

## 4. 测试结果

### 4.1 测试概览

15 项独立测试，全部通过：

  ----------------------------------------------------------------------------------------
  **编号**          **测试名称**         **状态**          **关键指标**
  ----------------- -------------------- ----------------- -------------------------------
  T1                CapabilityModel      ✅                feedback_density=0.45,
                    环境能力度量                           horizon=1.00, 3个反馈源

  T2                EvolutionCarrier     ✅                5个快路径+1个慢路径正确,
                    快慢路径决策                           饱和检测正常

  T3                HarnessSelfModify    ✅                prompts=15, 快照历史=3
                    自描述与快照                           

  T4                Rules 系统加载与注入 ✅                17条规则, 注入1675字符,
                                                           禁止检查正确拦截

  T5                SkillEvolver         ✅                cytobridge健康度查询正常
                    真实DB集成                             

  T6                三层记忆系统全流程   ✅                4条记忆3级存储,
                                                           semantic检索正常

  T7                PaperCuration        ✅                scVelo排名最高(must_read),
                    论文策展管道                           气候论文排最后

  T8                GEO 真实API搜索      ✅                10条真实结果(GSE/GDS编号正确)

  T9                PromptBuilder        ✅                规则块1675字符, 含\<rules\>标签
                    Rules注入集成                          

  T10               SelfEvolve           ✅                learning.db存在, 健康度计算正常
                    SkillEvolver钩子                       

  T11               PromptBuilder        ✅                prompt=51,017字符, 含rules注入
                    完整构建                               

  T12               记忆操作压缩与合并   ✅                压缩10条, 合并1对

  T13               SingleCellAnalyzer   ✅                9,535 cells正确检测
                    QC                                     

  T14               ProteinDesign ESM    ✅                stability=0.85
                    fallback                               

  T15               Partner03 端到端验证 ✅                重启成功, QQ Bot连接,
                                                           消息路由正常
  ----------------------------------------------------------------------------------------

### 4.2 测试文件位置

所有测试代码、日志、记录文件位于 \`tests/\` 目录：

-   \`tests/test_results_20260801_133619.json\` --- 第一轮 10 项

-   \`tests/test_results_retest_20260801_133700.json\` --- 重测 4 项

-   \`tests/domain_tests_20260801_135212.json\` --- 领域模块 5 项

-   \`tests/scanpy_pipeline_test_20260801_135322.json\` --- scanpy 管道

-   \`tests/MASTER_REPORT.json\` --- 总报告

## 5. 完成情况

  -----------------------------------------------------------------------------------------
  **模块**       **代码**       **集成**             **测试**       **备注**
  -------------- -------------- -------------------- -------------- -----------------------
  P0 能力度量    ✅             ✅ harness_core      ✅ T1          需传入真实
                                                                    EventRegistry
                                                                    以获得非零
                                                                    action_diversity

  P0 进化载体    ✅             ✅ harness_core      ✅ T2          ---

  P0 自修改接口  ✅             ✅ harness_core      ✅ T3          ---

  P1 规则类型    ✅             ✅ rules包           ✅ T4          ---

  P1 规则加载    ✅             ✅ prompt_builder    ✅ T9/T11      ---

  P1 规则注入    ✅             ✅ prompt_builder    ✅ T9/T11      每次 batch_plan
                                                                    自动注入

  P1 默认规则    ✅             ✅ 自动加载          ✅ T4          17条规则开箱即用

  P2 技能进化    ✅             ✅                   ✅ T5/T10      每次进化周期自动运行
                                self_evolve_engine                  

  P3 记忆系统    ✅             ✅ 独立模块          ✅ T6/T12      ---

  P4 论文策展    ✅             ✅ cron job          ✅ T7          每日9:00自动运行

  P5 单细胞分析  ✅             ✅ 独立模块          ✅ T13         需真实 scRNA-seq 数据

  P5 蛋白质设计  ✅             ✅ 独立模块          ✅ T14         ColabFold/ProteinMPNN
                                                                    需GPU

  P5 GEO检索     ✅             ✅ 独立模块          ✅ T8          真实 NCBI API

  P5             ✅             ✅ 独立模块          ⚠️             待 CellOS API 开放
  细胞世界模型                                                      

  Partner03 E2E  ---            ✅                   ✅ T15         不影响现有管道
  -----------------------------------------------------------------------------------------

## 6. 后续待完成

9.  CapabilityModel 接入真实 EventRegistry 以获得准确 action_diversity
    评分

10. ColabFold / ProteinMPNN GPU 环境部署后验证完整蛋白质设计管道

11. CellOS API 开放后对接 \`CellWorldModelClient\`

12. 为具体项目创建 project-level rules 测试三层优先级覆盖

13. PaperCuration 首次运行后根据结果调整画像和推荐参数

14. 扩展现有 Benchmark 套件（利用新模块创建 literature_curation_v2 等）

15. 记忆系统从 learning.db 迁移到新的 layered_memory 表结构
