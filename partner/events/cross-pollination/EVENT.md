---
name: cross-pollination
description: "跨项目知识迁移：发现不同项目间的方法复用机会"
version: 1.0.0
author: partner
tags: [cross-project, transfer, analogy]
priority_base: 6
ttl_hours: 48
estimated_minutes: 10

triggers:
  auto: false
  keywords: ["迁移", "跨项目", "类比", "transfer"]

inputs:
  source_project:
    type: string
    default: ""
    description: "源项目"
  target_project:
    type: string
    default: ""
    description: "目标项目"
  methods:
    type: list
    default: []
    description: "要迁移的方法"

phases:
  - name: compare
    type: project_scan
    description: "扫描所有活跃项目的方法论"
    prompt: "扫描所有活跃项目的方法论，对比异同"
    targets: "all_active_projects"
  - name: find-analogies
    type: knowledge_synthesis
    description: "识别方法相似性"
    prompt: "识别项目间的方法相似性和可迁移的技术"
  - name: adapt
    type: idea_generation
    description: "设计迁移方案"
    prompt: "设计跨项目方法迁移的具体方案"
  - name: document
    type: knowledge_extraction
    description: "记录迁移方案"
    prompt: "记录迁移方案到知识库"
    max_events: 1
---

# 跨项目知识迁移

## 目标

扫描多个项目的方法论，识别跨项目的方法相似性和可复用技术，设计知识迁移方案。

## 适用场景

- 拥有多个活跃项目，希望复用已有方法
- 发现某个项目的方法可能适用于另一个项目
- 寻找跨领域的类比和技术迁移机会

## 执行指南

1. **对比阶段**：扫描所有活跃项目的方法论，对比异同
2. **识别阶段**：找到项目间的方法相似性和可迁移的技术
3. **设计阶段**：设计具体的迁移方案，包括适配要点
4. **记录阶段**：将迁移方案记录到知识库

## 注意事项

- 关注方法的本质而非表面形式，寻找深层相似性
- 迁移方案要考虑目标项目的特殊约束
- 记录迁移的成功案例，为未来提供参考
