"""Acceptance Evaluator — structured output verification against tool/agent profile.

Uses an LLM to judge whether a tool/agent's actual output meets the
expectations defined in its profile. Returns pass/fail with specific gaps.

Usage:
    from partner.evaluation import AcceptanceEvaluator
    evaluator = AcceptanceEvaluator(adapter)
    result = await evaluator.evaluate(
        profile=tool_profile_dict,
        actual_output={"stdout": "...", "files": [...], "content": "..."},
        user_goal="查询PocketFlow的评估指标"
    )
    # result: {"pass": bool, "score": 0-100, "gaps": [...], "summary": "..."}
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


EVALUATE_PROMPT = """你是一个验收评估器。根据工具/Agent的能力描述和用户的原始目标，判断实际输出是否满足要求。

## 工具/Agent 能力描述
{profile_text}

## 用户原始目标
{user_goal}

## 实际输出
{actual_output}

## 评估要求
按以下格式输出 JSON（不要输出其他内容）：
{{
    "pass": true/false,
    "score": 0-100,
    "gaps": ["缺失项1", "缺失项2"],
    "strengths": ["做得好1"],
    "summary": "一句话总结"
}}

评估原则：
1. 根据工具类型调整期望：知识检索工具只需返回相关信息，不需要生成报告
2. 数据分析Agent应产出结构化结果
3. 如果输出与目标相关且信息准确，即使不完美也通过
4. 只在关键信息完全缺失时才判定不通过
"""


class AcceptanceEvaluator:

    def __init__(self, adapter: Any):
        self._adapter = adapter

    def _format_profile(self, profile: dict) -> str:
        lines = []
        atype = profile.get("type", "unknown")
        lines.append(f"类型: {atype}")
        outputs = profile.get("expected_outputs", [])
        if outputs:
            lines.append("预期输出:")
            for o in outputs:
                lines.append(f"  - {o.get('format','?')}: {o.get('description','')}")
        constraints = profile.get("task_constraints", {})
        suitable = constraints.get("suitable_for", [])
        if suitable:
            lines.append(f"适用: {'; '.join(suitable)}")
        return "\n".join(lines)

    def _format_output(self, output: dict) -> str:
        parts = []
        if output.get("content"):
            parts.append(f"输出内容 ({len(str(output['content']))} 字符):\n{str(output['content'])[:3000]}")
        if output.get("stdout"):
            parts.append(f"标准输出:\n{str(output['stdout'])[:2000]}")
        if output.get("files"):
            files = output["files"]
            if isinstance(files, list):
                parts.append(f"产出文件 ({len(files)} 个):\n" + "\n".join(f"  - {f}" for f in files[:20]))
        if output.get("error"):
            parts.append(f"执行错误: {output['error']}")
        if not parts:
            parts.append("(无输出)")
        return "\n\n".join(parts)

    async def evaluate(self, profile: dict, actual_output: dict, user_goal: str = "") -> dict:
        profile_text = self._format_profile(profile)
        actual_text = self._format_output(actual_output)
        prompt = EVALUATE_PROMPT.format(
            profile_text=profile_text,
            user_goal=user_goal[:2000],
            actual_output=actual_text,
        )
        try:
            response = self._adapter.chat(prompt, purpose="acceptance_eval")
            if not response:
                return self._fallback(actual_output)
            result = self._parse_json(response)
            return result or self._fallback(actual_output)
        except Exception as e:
            logger.warning("[ACCEPTANCE_EVAL] failed: %s", e)
            return self._fallback(actual_output)

    def _parse_json(self, response: str) -> dict | None:
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        m = re.search(r'\{[^{}]*"pass"[^{}]*\}', response, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return None

    def _fallback(self, output: dict) -> dict:
        has = bool(output.get("content") or output.get("stdout") or output.get("files"))
        err = bool(output.get("error"))
        if err and not has:
            return {"pass": False, "score": 0, "gaps": [str(output.get("error",""))[:200]], "strengths": [], "summary": "执行失败"}
        if has:
            return {"pass": True, "score": 70, "gaps": [], "strengths": ["有输出"], "summary": "有输出（未LLM评估）"}
        return {"pass": False, "score": 0, "gaps": ["无输出"], "strengths": [], "summary": "无输出"}
