"""Domain-specific Markdown reports; machine JSON remains a separate artifact."""
from __future__ import annotations

import json
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _metric_rows(metrics: dict[str, Any]) -> str:
    rows = []
    for key, value in metrics.items():
        display = _json(value) if isinstance(value, (dict, list)) else str(value)
        rows.append(f"| `{key}` | {display[:500]} |")
    return "\n".join(rows) or "| 状态 | 本轮没有可比较的数值指标 |"


def render_continuous_report(instance_id: str, strategy: str, result: dict[str, Any]) -> str:
    metrics = dict(result.get("business_metrics") or {})
    if instance_id == "01":
        matrix = list(result.get("claim_evidence_matrix") or result.get("claim_risk_queue")
                      or result.get("editorial_backlog") or result.get("draft_candidates") or [])
        evidence_rows = "\n".join(
            f"| {row.get('record_id') or index + 1} | {', '.join(row.get('source_urls') or [str(row.get('source_url') or '')])} | "
            f"{row.get('claim_status') or row.get('lane') or '待核验'} | {'是' if row.get('publish_authorized') else '否'} |"
            for index, row in enumerate(matrix[:12])
        ) or "| — | 当前没有可发布候选 | 等待新证据 | 否 |"
        return f"""# 01 内容证据与候选决策报告

> 本轮策略：`{strategy}`。目标是把来源材料变成可审核候选，而不是为了持续运行自动发布。

## 本轮结论

共映射 {metrics.get('records_mapped', result.get('records', 0))} 条记录，其中带来源证据 {metrics.get('records_with_source_evidence', result.get('unique_urls', 0))} 条；发布授权始终为 {metrics.get('publish_authorized', 0)}。当前结果支持继续做主张级核验，不支持自动上传或发布。

## 本轮实际操作

1. 从持久 content inbox 读取真实记录，而不是从报告文字反推输入。
2. 对每条记录保留来源 URL、原文摘要哈希和文本长度，便于识别来源变化与重复采集。
3. 将“来源存在”“主张已核验”“允许发布”拆成三个独立状态，防止网页可达被误写成内容真实或已获授权。
4. 输出机器 JSON 供下一轮消费，同时将面向用户的判断整理为本报告。

## 来源与主张矩阵

| 记录 | 来源 | 主张状态 | 已获发布授权 |
|---|---|---|---|
{evidence_rows}

## 关键指标

| 指标 | 结果 |
|---|---|
{_metric_rows(metrics)}

## 风险与判断

- 来源可达不等于主张真实；原文、时间和上下文仍需逐条核对。
- 相同 URL 的多条记录可能是重复采集，不能被包装成多个独立证据。
- 未获得明确内容与发布授权前，Partner 只能准备候选和证据，不能执行公开发布。

证据哈希只能证明本轮读取内容是否变化，不能证明页面陈述本身正确。下一轮若要形成候选稿，应逐条标注可引用原句、事实核验来源、时效性和潜在误导风险；无法核验的数字或因果主张必须删除或明确标记为待确认。

## 下一步

优先处理证据完整且主题明确的候选：提取可引用原文、列出需二次核实的主张、形成适合用户审核的内容 brief。若没有新证据，则进入低频 Scout，而不是重复生成相同报告。

## 机器证据

完整结构化结果位于同目录 JSON；本 PDF 只展示用户需要阅读的判断和指标，避免用大段 JSON 充当报告正文。
"""
    if instance_id == "02":
        risks = list(result.get("model_risk_register") or [])
        experiments = list(result.get("next_experiments") or [])
        risk_rows = "\n".join(
            f"| `{row.get('risk')}` | {'当前有信号' if row.get('present') else '尚无直接信号'} | {row.get('required_evidence')} |"
            for row in risks
        ) or "| — | 未生成新风险项 | 读取机器 JSON 和上一轮 Receipt |"
        experiment_rows = "\n".join(
            f"| {row.get('hypothesis')} | `{row.get('event')}` | {row.get('acceptance')} |"
            for row in experiments
        ) or "| — | — | 本轮是风险登记，不声称实验已经执行 |"
        return f"""# 02 TargetDiff 模型风险与下一实验门

> 本轮策略：`{strategy}`。目标是承接官方 split、bootstrap、校准和误差切片证据，决定下一项可证伪实验；不是重复汇报整体 RMSE。

## 证据承接

本轮读取了 {result.get('evidence_records') if isinstance(result.get('evidence_records'), int) else len(result.get('evidence_records') or [])} 份持久机器结果。输入来自已归档 EvidenceManifest 对应 JSON，不从 PDF 文案反推数据。已有结果只支持数据集内预测和误差分析，不支持药效因果、临床有效性或生产晋升。

## 模型风险登记

| 风险 | 当前状态 | 关闭风险所需证据 |
|---|---|---|
{risk_rows}

## 候选实验与验收门

| 可证伪假设 | 执行事件 | 最低验收条件 |
|---|---|---|
{experiment_rows}

## 关键指标

| 指标 | 结果 |
|---|---|
{_metric_rows(metrics)}

## 科学边界

- 官方测试身份数量有限时，整体均值不足以描述跨靶点稳定性，必须保留组级不确定性。
- Vina、RMSD、身份和目标字段必须遵守预注册特征/目标合同；任何训练测试身份重叠都使比较失效。
- `experiments_executed=0` 表示本轮只形成下一实验合同，不能写成方法已经改善。
- candidate 只有在独立分组、预定义指标和不确定性门通过后才可进入进一步 canary；不会自动 production promotion。

## 下一步

若本轮生成候选实验，Campaign 应选择其中一项实际入队并保存源码、退出码、JSON、误差分析和交付回执。若风险所需输入不存在，应明确记录恢复条件并把槽位让给其他项目，而不是重新生成相同风险表。

## 机器证据

完整风险项、来源路径和实验合同保存在同目录 JSON。本报告只呈现影响决策的结构，避免用大段原始 JSON 充当分析。
"""
    if instance_id == "03":
        command = " ".join(str(value) for value in result.get("command") or [])
        output = str(result.get("test_output") or "")[-1800:]
        return f"""# 03 Partner 框架验证与变更决策

> 本轮策略：`{strategy}`。本报告区分“测试通过”“实机可用”和“允许晋升”，三者不能互相替代。

## 要验证的问题

本轮验证持久证据、双槽调度、Campaign 恢复或 RL 控制合同是否仍成立。影响范围限定在 Partner 框架，不以生成 PDF 或发送消息作为框架进步。

## 合同覆盖范围

| 合同 | 本轮关注点 | 不能由此证明 |
|---|---|---|
| 持久证据 | Task 产物可归档并被下一轮读取 | 业务结论一定正确 |
| 双槽调度 | 同时活动实例不超过两个 | 长时间资源没有累积 |
| 恢复语义 | runner 重启不重复注入任务 | 外部 QQ/浏览器永不失败 |
| RL 控制 | candidate 不越过晋升门 | 当前策略已经优于所有替代 |

## 实际执行

- 命令：`{command or '由确定性事件执行本地合同检查'}`
- 退出码：`{result.get('exit_code', 'n/a')}`
- 策略标识：`{strategy}`

## 测试结果

```text
{output or '机器结果中没有测试输出；需查看同目录 JSON。'}
```

## 指标与证据

| 指标 | 结果 |
|---|---|
{_metric_rows(metrics)}

## 决策

当前处理器结果为 `ok={result.get('ok')}`。通过只说明声明的合同在隔离测试中没有回归；若要晋升候选，仍需真实 Campaign canary、QQ 回执、资源边界和 PromotionDecision。失败时应定位具体测试并建立 Issue，不能用重写报告掩盖。

本轮验收同时检查了真实子进程退出码和测试输出。`ok=True` 时保留通过证据，`ok=False` 时必须保留失败测试名称与 stderr；两种结果都进入后续 Receipt，不能只保存成功样本。报告版式和消息送达属于用户体验硬门，但它们不会提高框架动作的业务奖励。

## 风险与下一步

隔离测试不能证明 systemd 长跑、QQ 网络、浏览器前台或模型延迟稳定。下一步应根据 Receipt 选择不同的 canary 或等待新代码指纹，避免对同一测试换标题重跑。
"""
    if instance_id == "04":
        concepts = result.get("source_concepts") or {}
        implementations = result.get("partner_implementation") or {}
        concept_rows = "\n".join(
            f"| `{name}` | {'、'.join(source for source, found in values.items() if found) or '未命中'} |"
            for name, values in concepts.items()
        ) or "| — | 未形成概念映射 |"
        implementation_rows = "\n".join(
            f"| `{name}` | `{row.get('path')}` | {'存在' if row.get('exists') else '缺失'} |"
            for name, row in implementations.items()
        ) or "| — | — | 未验证 |"
        return f"""# 04 外部 Harness 学习与独立适配报告

> 本轮策略：`{strategy}`。学习目标是提取可验证设计，不复制 DeepSeek/Codex 的实现根基。

## 本轮结论

外部概念映射 {metrics.get('concepts_mapped', len(concepts))} 项，Partner 本地合同存在 {metrics.get('partner_contracts_present', 0)} 项；`copied_source={result.get('copied_source', False)}`。这属于适配候选证据，不等于外部代码已经 integrated 或取得生产控制权。

## 外部概念证据

| 概念 | 命中的来源 |
|---|---|
{concept_rows}

## Partner 独立落点

| 合同 | 本地路径 | 状态 |
|---|---|---|
{implementation_rows}

## 采用与不采用

- 采用：append-only evidence、生命周期边界、先观察再归约、模型可见事实边界。
- 不采用：直接复制外部执行器、训练栈或会话根基；未经隔离实验不运行外部训练代码。
- 当前缺口：概念存在性检查仍弱于真实 adapter 输入输出测试，后续应使用一个真实 Issue 做只读原型验证。

## 下一步与晋升边界

下一轮必须产出可运行的独立 adapter contract、失败样本和针对性测试；只有 Experiment 与回归门通过才能写入 Partner 稳定能力。重复索引相同文件只能算 Scout no-change。

## 机器证据

完整映射和路径位于同目录 JSON，PDF 只保留来源、采用判断和边界。
"""
    return f"""# 持续项目结果：{strategy}

## 结论

处理器返回 `ok={result.get('ok')}`，本轮机器指标如下。

## 关键指标

| 指标 | 结果 |
|---|---|
{_metric_rows(metrics)}

## 实际执行证据

```json
{_json(result)}
```

## 限制

文件生成与发送只属于交付合同，不自动构成业务进步。完整机器结果保存在同目录 JSON。

## 下一步

由持久 Receipt 和 Campaign 调度选择下一动作；没有任务 ID 时不得声称已开始。
"""
