---
name: exploration
description: "探索性研究：自由探索新方向，寻找灵感和机会"
version: 1.0.0
author: partner
tags: [exporation, discovery, serendipity]
priority_base: 4
ttl_hours: 72
estimated_minutes: 10

triggers:
  auto: true
  keywords: ["探索", "发现", "新方向", "explore"]
  schedule: "every_10_events"

inputs:
  direction:
    type: string
    default: ""
    description: "探索方向（可选，留空则随机探索）"

phases:
  - name: survey
    type: literature_search
    description: "浏览前沿"
    prompt: "搜索最近一周的 AI/ML/生物信息学前沿论文和项目"
    max_queries: 2
  - name: discover
    type: knowledge_extraction
    description: "发现亮点"
    prompt: "从搜索结果中发现有趣的方法、工具或想法"
  - name: connect
    type: knowledge_synthesis
    description: "关联现有知识"
    prompt: "将新发现与知识库中的现有条目关联，找到潜在联系"
  - name: spark
    type: idea_generation
    description: "激发新想法"
    prompt: "基于新发现和现有知识的关联，生成 2-3 个探索性想法"
---

# 探索性研究

## 目标

自由探索前沿领域，寻找灵感和新机会。不预设方向，通过浏览最新论文和项目发现有趣的方法、工具或想法。

## 适用场景

- 定期执行（每 10 个 Event 一次），保持对前沿的敏感度
- 寻找跨领域的灵感
- 发现新的研究方向或工具

## 执行指南

1. **浏览阶段**：搜索最近一周的 AI/ML/生物信息学前沿论文
2. **发现阶段**：从搜索结果中挑选有趣的方法、工具或想法
3. **关联阶段**：将新发现与知识库中的现有条目关联，找到潜在联系
4. **激发阶段**：基于关联生成 2-3 个探索性想法

## 注意事项

- 探索性研究不要求深度，重在广度和发现
- 关注跨领域的类比和迁移机会
- 结果以激发新想法为主要目标
