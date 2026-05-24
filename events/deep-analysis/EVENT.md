---
name: deep-analysis
description: "深入分析特定问题：定义问题、研究背景、分析方案"
version: 1.0.0
author: partner
tags: [analysis, deep-dive, research]
priority_base: 7
ttl_hours: 48
estimated_minutes: 15

triggers:
  auto: false
  keywords: ["分析", "深入", "研究", "analyze", "deep"]

inputs:
  problem:
    type: string
    required: true
    description: "要分析的问题"
  context:
    type: string
    default: ""
    description: "问题背景"

phases:
  - name: define
    type: planning
    description: "定义问题"
    prompt: "明确 {problem} 的范围、关键子问题和分析目标"
  - name: research
    type: literature_search
    description: "研究背景"
    prompt: "搜索 {problem} 相关的研究文献和解决方案"
    max_queries: 3
  - name: analyze
    type: knowledge_synthesis
    description: "深入分析"
    prompt: "综合文献和现有知识，深入分析 {problem} 的根因和可行方案"
  - name: conclude
    type: idea_generation
    description: "形成结论"
    prompt: "基于分析结果，形成结论和下一步行动建议"
  - name: spawn
    type: event_generation
    description: "生成后续探索"
    max_events: 2
---

# 深入分析

## 目标

对特定问题进行系统性分析：明确问题范围、研究背景文献、深入分析根因、形成结论和行动建议。

## 适用场景

- 遇到需要深入理解的技术问题
- 评估某个方案的可行性
- 分析项目中的瓶颈或挑战

## 执行指南

1. **定义阶段**：明确问题的范围、关键子问题和分析目标
2. **研究阶段**：搜索相关文献和现有解决方案
3. **分析阶段**：综合文献和现有知识，深入分析根因
4. **结论阶段**：形成结论和下一步行动建议
5. **生成阶段**：基于分析结果生成后续探索方向

## 注意事项

- 问题定义要具体，避免过于宽泛
- 分析要基于证据（文献、数据），避免主观臆断
- 结论要可操作，包含具体的下一步建议
