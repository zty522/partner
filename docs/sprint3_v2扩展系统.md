\# Sprint 3: v2 扩展系统

**时间**: 2026-07-01 \~ 2026-07-05

**目标**: 建立 Partner 的能力扩展体系 ---
感知、操控、浏览器、多媒体、视频、自进化

## 1. 设计内容

### 1.1 v2 模块体系（56 个 Harness Event）

10 个模块覆盖完整的\"感知→学习→规划→执行→验证→迭代\"循环：

  ------------------------------------------------------------------------------------------------------------
  **模块**          **文件**          **Event数**       **核心能力**
  ----------------- ----------------- ----------------- ------------------------------------------------------
  感知层            perception.py     9                 截图(4层fallback)、OCR(2层)、UI检测、硬件信息

  操控层            control.py        15                鼠标/键盘/剪贴板、Windows GUI (pywinauto)

  浏览器            browser.py        9                 Playwright: 打开/点击/提取/截图/视频监控

  多媒体            media.py          5                 图表(matplotlib)、截图标注(PIL)、视觉报告(reportlab)

  视频              video.py          5                 截帧(opencv)、场景检测、ASR(whisper)

  多模态            multimodal.py     4                 网页获取(bs4)、视觉分析(Ollama llava/moondream)

  外循环            outer_loop.py     4                 GitHub搜索、网页搜索(DDG)、知识记录

  Loop引擎          loop_engine.py    5                 目标解析、滚动规划、缺口检测、暂停恢复
  ------------------------------------------------------------------------------------------------------------

### 1.2 自进化引擎

五步闭环周期：

核心模块：

-   \`self_description.py\` --- 输出 Partner
    当前架构描述（事件数、v2模块、Agent数）

-   \`architecture_mapper.py\` --- 分析外部架构→识别差距→生成改进方案

-   \`architecture_improver.py\` --- 执行改进：config(YAML) /
    prompt(规则) / code(diff)

A+B 方案：

-   **A 方案**（自动）：低风险配置改进，进化周期末尾自动应用

-   **B 方案**（用户审批）：中高风险改进，通过自然语言审批

### 1.3 WSL ↔ Windows 集成

-   **截图**: pyautogui → mss → PowerShell
    CopyFromScreen（真实桌面帧缓冲）

-   **OCR**: pytesseract → Windows OCR API（Win10+内置，支持中文）

-   **GUI操控**: pywinauto via PowerShell，支持后台 UIA 接口

-   **视觉模型**: llava:7b (57s CPU)，moondream:1.8b (17s CPU)

### 1.4 Benchmark 评估框架

-   \`BenchmarkProtocol\` / \`BenchmarkTask\` 抽象接口

-   \`NatureBenchRunner\` → sync_harness_executor

-   内置套件：literature_review_v1, data_analysis_v1

-   Harness 集成：\`TaskInstance\` + Hermes 子进程执行

## 2. 关键文件

  ------------------------------------------------------------------------------------------------
  **文件**                                         **行数**                **功能**
  ------------------------------------------------ ----------------------- -----------------------
  \`partner/v2/\_\_init\_\_.py\`                   \~400                   56个Event注册

  \`partner/v2/perception.py\`                     \~550                   截图/OCR/UI检测

  \`partner/v2/control.py\`                        \~530                   鼠标/键盘/Windows GUI

  \`partner/v2/browser.py\`                        \~530                   Playwright自动化

  \`partner/evolution/self_evolve_engine.py\`      \~1500                  自进化核心引擎

  \`partner/evolution/architecture_improver.py\`   \~400                   架构改进执行

  \`partner/evolution/architecture_mapper.py\`     \~250                   外部架构映射

  \`partner/benchmark/naturebench_runner.py\`      \~300                   Benchmark执行器
  ------------------------------------------------------------------------------------------------

## 3. 完成情况

  ----------------------------------------------------------------------------
  **功能**                **状态**                **验证方式**
  ----------------------- ----------------------- ----------------------------
  v2 Event注册            ✅ 完成                 56个event成功注册到Harness

  感知层截图              ✅ 完成                 PowerShell截图 1536x960,
                                                  \~100KB

  操控层GUI               ✅ 完成                 pywinauto窗口列表正常

  浏览器自动化            ✅ 完成                 Playwright headless正常

  自进化引擎              ✅ 完成                 已完成 64 个进化周期，115
                                                  条规则

  Benchmark               ✅ 完成                 literature_review 套件可执行

  WSL截图                 ✅ 完成                 三层fallback全部验证
  ----------------------------------------------------------------------------

## 4. 遗留问题

-   部分 v2 模块返回 schema 不一致（perception 用 \`ok\`，control 用
    \`success\`，browser 用 \`status\`）

-   pyautogui 在 headless 环境崩溃 → 已通过 PowerShell fallback 规避

-   自进化截图需手动复制到 outgoing/ 才能发送到 QQ

-   Benchmark 仅 2 个套件，覆盖范围有限
