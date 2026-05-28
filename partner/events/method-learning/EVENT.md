---
name: method-learning
description: "方法学习：搜索、理解并记录新工具或方法"
version: 1.0.0
author: partner
tags: [learning, method, tool]
priority_base: 6
ttl_hours: 48
estimated_minutes: 12

triggers:
  auto: false
  keywords: ["学习", "方法", "工具", "learn", "method"]

inputs:
  method:
    type: string
    required: true
    description: "要学习的方法或工具名称"
  context:
    type: string
    default: ""
    description: "学习上下文（为什么需要这个方法）"

phases:
  - name: search
    type: literature_search
    description: "搜索方法文档"
    prompt: "搜索 {method} 的官方文档、教程和最佳实践"
    max_queries: 3
  - name: read
    type: knowledge_extraction
    description: "提取核心概念"
    prompt: "从搜索结果中提取 {method} 的核心概念、API 和使用模式"
  - name: practice
    type: exploration
    description: "实践验证"
    prompt: "尝试使用 {method} 解决一个简单问题，记录过程"
  - name: document
    type: knowledge_synthesis
    description: "记录学习成果"
    prompt: "将 {method} 的学习成果整理为知识条目，包含示例代码和注意事项"
---

# 方法学习

## 目标

系统性地学习一个新的工具或方法：搜索文档、提取核心概念、实践验证、记录学习成果。

## 适用场景

- 项目中需要用到新的库、框架或工具
- 学习新的算法或方法论
- 掌握新的开发工具或工作流

## 执行指南

1. **搜索阶段**：搜索官方文档、教程和最佳实践
2. **提取阶段**：从搜索结果中提取核心概念、API 和使用模式
3. **实践阶段**：尝试使用该方法解决一个简单问题，记录过程
4. **记录阶段**：将学习成果整理为知识条目，包含示例代码和注意事项

## 注意事项

- 优先查阅官方文档，再看社区教程
- 实践验证是关键，不要只停留在理论层面
- 记录时包含可运行的示例代码
