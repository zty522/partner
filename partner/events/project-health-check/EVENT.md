---
name: project-health-check
description: "项目状态扫描：检查代码质量、依赖更新、测试覆盖"
version: 1.0.0
author: partner
tags: [project, scan, health]
priority_base: 5
ttl_hours: 72
estimated_minutes: 8

triggers:
  auto: true
  keywords: ["项目", "扫描", "检查", "health"]
  schedule: "weekly"

inputs:
  project:
    type: string
    required: true
    description: "项目路径或名称"
  focus:
    type: enum
    values: [code, deps, tests, all]
    default: all

phases:
  - name: scan
    type: project_scan
    description: "扫描项目结构和状态"
    prompt: "扫描项目 {project} 的文件结构、依赖版本、测试状态"
  - name: analyze
    type: knowledge_extraction
    description: "分析扫描结果"
    prompt: "分析项目扫描结果，识别潜在问题和改进点"
  - name: report
    type: planning
    description: "生成报告和建议"
    prompt: "生成项目健康报告，列出优先改进项"
  - name: spawn
    type: event_generation
    description: "生成后续任务"
    max_events: 1
---

# 项目健康检查

## 目标

定期扫描项目状态，检查代码质量、依赖版本、测试覆盖等指标，生成健康报告和改进建议。

## 适用场景

- 定期执行（每周一次），监控项目健康状态
- 项目发布前的全面检查
- 接手新项目时了解项目现状

## 执行指南

1. **扫描阶段**：检查项目的文件结构、依赖版本、测试状态
2. **分析阶段**：识别潜在问题（过时依赖、缺失测试、代码异味）
3. **报告阶段**：生成项目健康报告，列出优先改进项
4. **生成阶段**：为关键改进项生成后续任务

## 注意事项

- 只读扫描，不修改任何项目文件
- 关注依赖安全性（已知漏洞）和版本新鲜度
- 测试覆盖率是重要的健康指标
