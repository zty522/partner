---
name: idea-exploration
description: "创意探索：基于现有知识库生成和评估新想法"
version: 1.0.0
author: partner
tags: [idea, creativity, brainstorm]
priority_base: 6
ttl_hours: 48
estimated_minutes: 8

triggers:
  auto: true
  keywords: ["想法", "创意", "改进", "idea"]
  min_knowledge_entries: 10

inputs:
  context:
    type: string
    default: ""
    description: "探索上下文（来自哪个 Event 的发现）"
  focus_area:
    type: string
    default: ""
    description: "聚焦领域"

phases:
  - name: review
    type: knowledge_scan
    description: "审查现有知识"
    prompt: "扫描知识库中的高价值条目，寻找可扩展的方向"
  - name: brainstorm
    type: idea_generation
    description: "头脑风暴新想法"
    prompt: "基于知识库 {context}，生成 3-5 个改进建议或新方向"
  - name: evaluate
    type: knowledge_synthesis
    description: "评估想法可行性"
    prompt: "评估生成的想法，按可行性和影响力排序"
  - name: spawn
    type: event_generation
    description: "为最佳想法生成深入探索 Event"
    max_events: 1
---

# 创意探索

## 目标

基于现有知识库生成和评估新想法，筛选出最有价值的方向进行后续探索。

## 适用场景

- 知识库积累到一定规模后，寻找改进方向
- 某个 Event 的发现激发了新的想法
- 需要为项目寻找新的研究方向或改进点

## 执行指南

1. **审查阶段**：扫描知识库中的高价值条目，寻找可扩展的方向
2. **头脑风暴阶段**：基于现有知识生成 3-5 个改进建议或新方向
3. **评估阶段**：按可行性和影响力对想法排序
4. **生成阶段**：为最佳想法生成深入探索的 Event

## 注意事项

- 想法要基于现有知识，不要凭空想象
- 评估时考虑可行性（资源、时间）和影响力（价值、范围）
- 每次只 spawn 最多 1 个后续 Event，避免队列膨胀
