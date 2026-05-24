---
name: literature-deep-dive
description: "深入研究特定主题的文献，整合发现并生成后续探索方向"
version: 1.0.0
author: partner
tags: [literature, research, synthesis]
priority_base: 7
ttl_hours: 48
estimated_minutes: 10

triggers:
  auto: true
  keywords: ["研究", "搜索", "文献", "paper", "survey"]
  min_knowledge_gap: 0.3

inputs:
  topic:
    type: string
    required: true
    description: "研究主题"
  depth:
    type: enum
    values: [shallow, medium, deep]
    default: medium

phases:
  - name: search
    type: literature_search
    description: "搜索相关文献"
    prompt: "搜索关于 {topic} 的最新论文（2024-2026），关注方法论创新"
    max_queries: 3
  - name: extract
    type: knowledge_extraction
    description: "提取关键发现"
    prompt: "从搜索结果中提取关键发现、方法和工具"
    min_entries: 1
  - name: synthesize
    type: knowledge_synthesis
    description: "整合到知识库"
    prompt: "将新发现与现有知识库整合，找到关联和空白"
  - name: spawn
    type: event_generation
    description: "生成后续 Event"
    prompt: "基于本次文献阅读，生成 1-2 个后续探索方向"
    max_events: 2
---

# 文献深度研究

## 目标

对特定研究主题进行系统性的文献调研，提取关键发现，整合到知识库，并生成后续探索方向。

## 适用场景

- 了解某个研究领域的最新进展
- 发现新的方法论或工具
- 填补知识库中的空白领域
- 为项目决策提供文献支撑

## 执行指南

1. **搜索阶段**：使用 arXiv API 搜索最新论文，优先使用 `ti:` 和 `abs:` 字段限定符
2. **提取阶段**：从搜索结果中提取关键发现、方法创新、工具名称
3. **综合阶段**：将新发现与 knowledge.json 中的现有条目关联，识别知识空白
4. **生成阶段**：基于发现生成 1-2 个后续探索方向作为新 Event

## 注意事项

- 优先使用 arXiv API 而非 Bing 搜索（见常见坑 #9）
- 搜索词必须用字段限定符，否则返回大量无关结果
- 每次搜索控制在 2-3 个查询，避免噪声过多
