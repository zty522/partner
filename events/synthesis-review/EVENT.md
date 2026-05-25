---
name: synthesis-review
description: "知识库综合审查：发现空白、纠正错误、优化组织"
version: 1.0.0
author: partner
tags: [synthesis, review, maintenance]
priority_base: 5
ttl_hours: 72
estimated_minutes: 10

triggers:
  auto: true
  schedule: "every_5_events"
  keywords: ["审查", "综合", "清理", "review"]

phases:
  - name: scan
    type: knowledge_scan
    description: "扫描知识库"
    prompt: "扫描知识库所有条目，标记过时、低置信度、重复的条目"
  - name: find-gaps
    type: gap_analysis
    description: "识别知识空白"
    prompt: "识别知识库中的空白领域"
  - name: fill
    type: literature_search
    description: "填补空白"
    prompt: "搜索填补空白的文献"
    max_queries: 2
  - name: reorganize
    type: knowledge_synthesis
    description: "重组知识库"
    prompt: "重组知识库结构，更新关联关系"
---

# 知识库综合审查

## 目标

定期审查知识库的健康状态：发现空白领域、纠正过时信息、消除重复条目、优化组织结构。

## 适用场景

- 定期执行（每 5 个 Event 一次），维护知识库质量
- 知识库条目增长到一定规模后进行整理
- 发现知识库中有过时或低质量条目时

## 执行指南

1. **扫描阶段**：遍历 knowledge.json 所有条目，标记过时、低置信度、重复的条目
2. **空白识别**：分析知识库的覆盖范围，识别缺失的领域
3. **填补阶段**：针对空白领域搜索文献，补充知识条目
4. **重组阶段**：优化知识库结构，更新条目间的关联关系

## 注意事项

- 重点关注低置信度（<0.5）和过时（>30天未更新）的条目
- 重复条目合并时保留信息更完整的版本
- 空白识别要结合当前研究方向，优先填补相关空白
