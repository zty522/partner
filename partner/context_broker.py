"""Context Broker — 打通对话上下文与研究循环（强化版 v2）。

负责在 QQ 对话与 Mind 自主研究循环之间建立桥梁：
1. 实时监听用户消息，提取项目路径、文件行号、具体指标、脚本名称
2. 将提取的信息写入 knowledge.json，标记 source: "dialog"
3. 当研究循环启动时，自动加载最近 1 小时内的 dialog-sourced 知识
4. 合并到任务上下文中作为搜索和执行的优先输入
"""

import json
import os
import re
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from .knowledge import KnowledgeBase, KnowledgeEntry

logger = logging.getLogger(__name__)


class ContextBroker:
    """对话上下文代理：解析对话、提取事实、注入研究循环。"""

    def __init__(self, workspace: str, knowledge_base: KnowledgeBase = None, journal=None):
        """初始化 ContextBroker。

        Args:
            workspace: Partner 工作区路径
            knowledge_base: KnowledgeBase 实例（用于保存/检索知识）
            journal: Journal 实例（用于记录日志）
        """
        self.workspace = workspace
        self.knowledge = knowledge_base
        self.journal = journal
        self.dialog_history_path = os.path.join(
            workspace, "state", "dialog_history.jsonl"
        )
        self.knowledge_custom_path = os.path.join(
            workspace, "state", "knowledge_context.json"
        )
        # 最近一段时间的上下文缓存（避免频繁读文件）
        self._context_cache: Dict = {}
        self._cache_ts: float = 0

    # ── 实时监听入口 ───────────────────────────────────────────

    def on_user_message(self, user_id: str, text: str) -> Dict:
        """实时处理用户消息：提取上下文并保存到 knowledge.json。

        由 QQ bridge 在收到每条消息时调用。

        Args:
            user_id: QQ 用户的 openid
            text: 消息内容

        Returns:
            提取到的事实字典（供下游使用）
        """
        facts = self.extract_project_facts_from_text(text)
        if facts["project_path"] or facts["line_numbers"] or facts["metrics"] or facts["files"]:
            # 有实质性信息，保存到 knowledge
            project_name = self._detect_project(facts)
            # 写入独立的 knowledge_context.json
            self._append_to_context_json(project_name, facts)
            # 同时也写入 knowledge.json（标准知识库）
            self._save_to_knowledge_base(project_name, facts)
            logger.info(
                f"[ContextBroker] 从用户消息提取并保存了 {len(facts['metrics'])} 个指标, "
                f"{len(facts['issues'])} 个问题, "
                f"路径={facts['project_path'] or '无'}"
            )
        return facts

    # ── 信息提取 ───────────────────────────────────────────────

    def extract_project_facts_from_text(self, text: str) -> Dict:
        """从单条文本中提取项目关键信息（强化版）。

        识别模式：
        - 项目路径：/mnt/e/work/xxx, /home/user/project/
        - 文件行号：第123行、第 239 行、line 239:L239
        - 指标/数值：MAE=7.08, accuracy=0.95, loss=0.32
        - 脚本名称：xxx.py (当出现在项目上下文时)
        - 问题/缺陷：leak, bug, error, issue

        Args:
            text: 用户消息文本

        Returns:
            结构化的事实字典
        """
        facts = {
            "project_path": "",
            "line_numbers": [],
            "metrics": {},
            "issues": [],
            "files": [],
            "scripts": [],
            "keywords": [],
            "raw_snippets": [],
            "source": "dialog",
        }

        # 1. 提取项目路径：/mnt/e/... 或 /home/... 或完整 Unix 路径
        path_pattern = re.compile(r'(/[a-zA-Z0-9_\-\/]+(?:/[a-zA-Z0-9_\-\.]+)*)')
        for m in path_pattern.finditer(text):
            fpath = m.group(1).strip()
            # 过滤掉常见非路径匹配
            if len(fpath) < 8:
                continue
            if any(skip in fpath for skip in ['http://', 'https://', 'www.', '.com', '.org']):
                continue
            if fpath.endswith(('.py', '.json', '.yaml', '.yml', '.sh', '.txt', '.csv', '.md', '.toml', '.cfg')):
                # 这是文件路径
                if fpath not in facts["files"] and len(facts["files"]) < 10:
                    facts["files"].append(fpath)
            else:
                # 可能是项目目录
                if not facts["project_path"] and os.path.isabs(fpath):
                    facts["project_path"] = fpath

        # 2. 提取行号：第123行、第 239 行、line 239、L239
        line_patterns = [
            (r'第\s*(\d+)\s*行', False),
            (r'line\s+(\d+)', re.IGNORECASE),
            (r'[Ll](\d{2,4})', False),  # L239 但不匹配单字母 L
        ]
        for pat, flags in line_patterns:
            for m in re.finditer(pat, text, flags=flags if flags else 0):
                num = int(m.group(1))
                if 10 <= num <= 99999:  # 合理的行号范围
                    if num not in facts["line_numbers"]:
                        facts["line_numbers"].append(num)

        # 3. 提取指标：KEY=VALUE 模式
        metric_pattern = re.compile(
            r'(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*[=：:]\s*(?P<value>[\d]+\.[\d]+)'
        )
        for m in metric_pattern.finditer(text):
            key = m.group("key").strip()
            value = m.group("value").strip()
            if (key.isupper() and len(key) <= 8) or any(
                kw in key.lower()
                for kw in ["acc", "loss", "mae", "mse", "rmse", "f1", "precis", "recall", "bleu", "perplex", "auc", "r2"]
            ):
                facts["metrics"][key] = value

        # 4. 提取问题/缺陷
        issue_patterns = [
            r'(?:error|bug|leak|leakage|issue|问题|缺陷|错误|故障|泄漏|泄露)[\s：:]*([A-Za-z_][A-Za-z0-9_]*)',
            r'(leak|泄漏|泄露|data_leak|feature_leak|batch_leak)',
            r'(overfit|underfit|overfitting|underfitting)',
        ]
        for pat in issue_patterns:
            for m in re.finditer(pat, text, re.IGNORECASE):
                issue = m.group(1).strip().lower()
                if issue and issue not in facts["issues"] and len(issue) > 2:
                    facts["issues"].append(issue)

        # 5. 提取脚本/代码文件
        script_pattern = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_]*\.py)\b')
        for m in script_pattern.finditer(text):
            fname = m.group(1)
            if fname not in facts["scripts"] and len(facts["scripts"]) < 5:
                facts["scripts"].append(fname)

        # 6. 提取技术关键词
        kw_pattern = re.compile(r'\b([a-z]+[A-Z][a-zA-Z]+)\b')
        for m in kw_pattern.finditer(text):
            kw = m.group(1).strip()
            if 4 <= len(kw) <= 30 and kw not in facts["keywords"]:
                facts["keywords"].append(kw)

        # 7. 保留有事实含量的片段
        if re.search(r'\d+\.\d+', text) or re.search(r'/[a-z_]+/[a-z_]+', text):
            facts["raw_snippets"].append(text[:200].strip())

        return facts

    def _detect_project(self, facts: Dict) -> str:
        """从事实中检测项目名称。"""
        # 优先从路径推断
        if facts["project_path"]:
            parts = facts["project_path"].strip("/").split("/")
            for p in reversed(parts):
                if p and p not in ("mnt", "e", "work", "home", "user", "data"):
                    return p
        # 从文件路径推断
        if facts["files"]:
            for fp in facts["files"]:
                parts = fp.strip("/").split("/")
                for p in reversed(parts):
                    if '.' not in p and p not in ("mnt", "e", "work", "home", "user"):
                        return p
        return "default"

    # ── 知识保存 ───────────────────────────────────────────────

    def _append_to_context_json(self, project_name: str, facts: Dict):
        """追加对话上下文到独立的 knowledge_context.json。"""
        entry_data = {
            "source": "dialog",
            "project": project_name,
            "timestamp": datetime.now().isoformat(),
            "facts": facts,
        }

        existing = []
        if os.path.exists(self.knowledge_custom_path):
            try:
                with open(self.knowledge_custom_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, list):
                    existing = [existing]
            except (json.JSONDecodeError, Exception):
                existing = []

        existing.append(entry_data)
        # 最多保留 100 条
        if len(existing) > 100:
            existing = existing[-100:]

        os.makedirs(os.path.dirname(self.knowledge_custom_path), exist_ok=True)
        with open(self.knowledge_custom_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)

    def _save_to_knowledge_base(self, project_name: str, facts: Dict):
        """将分析结果写入 knowledge.json（标准知识库格式）。"""
        if not self.knowledge:
            return
        try:
            # 构建可读的摘要文本
            parts = []
            if facts["project_path"]:
                parts.append(f"项目路径: {facts['project_path']}")
            if facts["line_numbers"]:
                parts.append(f"相关行号: {', '.join(str(n) for n in facts['line_numbers'])}")
            if facts["metrics"]:
                parts.append(f"指标: {', '.join(f'{k}={v}' for k, v in facts['metrics'].items())}")
            if facts["issues"]:
                parts.append(f"问题: {', '.join(facts['issues'])}")
            if facts["files"]:
                parts.append(f"文件: {', '.join(facts['files'])}")
            if facts["scripts"]:
                parts.append(f"脚本: {', '.join(facts['scripts'])}")

            content = "\n".join(parts) if parts else json.dumps(facts, ensure_ascii=False)

            entry = KnowledgeEntry(
                category="dialog_context",
                title=f"对话上下文: {project_name}",
                content=content,
                source="dialog",
                related_projects=[project_name],
                tags=facts["keywords"][:5] + facts["issues"][:3],
            )
            self.knowledge.add(entry)
        except Exception as e:
            logger.warning(f"[ContextBroker] 保存到知识库失败: {e}")

    # ── 搜索/任务上下文获取 ────────────────────────────────────

    def get_context_for_search(self, query: str = "") -> Dict:
        """获取对话上下文，用于搜索和执行任务。

        优先级：
        1. 最近 1 小时内的 dialog-sourced 知识
        2. 匹配 query 的相关上下文
        3. 最近的通用上下文

        Returns:
            {
                "project_path": str,
                "line_numbers": [int],
                "metrics": {str: str},
                "issues": [str],
                "files": [str],
                "scripts": [str],
                "raw_snippets": [str],
                "has_relevant_context": bool,
            }
        """
        result = {
            "project_path": "",
            "line_numbers": [],
            "metrics": {},
            "issues": [],
            "files": [],
            "scripts": [],
            "scripts": [],
            "raw_snippets": [],
            "has_relevant_context": False,
        }

        if not os.path.exists(self.knowledge_custom_path):
            return result

        try:
            with open(self.knowledge_custom_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except Exception:
            return result

        if not isinstance(entries, list):
            return result

        now = datetime.now()
        one_hour_ago = now - timedelta(hours=1)
        query_lower = query.lower() if query else ""

        for entry in reversed(entries):  # 最新优先
            ts_str = entry.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                continue

            facts = entry.get("facts", {})
            if not isinstance(facts, dict):
                continue

            # 检查时间或内容匹配
            is_recent = ts >= one_hour_ago
            is_relevant = query_lower and (
                query_lower in str(facts.get("project_path", "")).lower()
                or any(query_lower in str(v).lower() for v in facts.get("issues", []))
                or any(query_lower in str(v).lower() for v in facts.get("keywords", []))
            )

            if not (is_recent or is_relevant):
                continue

            # 合并数据
            if facts.get("project_path") and not result["project_path"]:
                result["project_path"] = facts["project_path"]
            for n in facts.get("line_numbers", []):
                if n not in result["line_numbers"]:
                    result["line_numbers"].append(n)
            result["metrics"].update(facts.get("metrics", {}))
            for i in facts.get("issues", []):
                if i not in result["issues"]:
                    result["issues"].append(i)
            for f in facts.get("files", []):
                if f not in result["files"]:
                    result["files"].append(f)
            for s in facts.get("scripts", []):
                if s not in result["scripts"]:
                    result["scripts"].append(s)
            for sn in facts.get("raw_snippets", []):
                if sn not in result["raw_snippets"]:
                    result["raw_snippets"].append(sn)

            result["has_relevant_context"] = True

        # 去重和限制数量
        result["line_numbers"] = result["line_numbers"][-20:]
        result["issues"] = result["issues"][:10]
        result["files"] = result["files"][:10]
        result["scripts"] = result["scripts"][:5]
        result["raw_snippets"] = result["raw_snippets"][:5]

        return result

    # ── 旧接口兼容 ─────────────────────────────────────────────

    def extract_project_facts(self, dialog_entries: List[Dict]) -> Dict:
        """从对话记录列表中提取项目关键信息（兼容旧接口）。"""
        all_text = "\n".join(
            e.get("content", "") for e in dialog_entries
        )
        return self.extract_project_facts_from_text(all_text)

    def save_to_knowledge(self, project_name: str, facts: Dict):
        """兼容旧接口。"""
        self._append_to_context_json(project_name, facts)

    def get_project_context(self, project_name: str) -> str:
        """兼容旧接口：获取项目上下文字符串。"""
        ctx = self.get_context_for_search(project_name)
        if not ctx["has_relevant_context"]:
            return f"项目「{project_name}」暂无对话上下文记录。"

        parts = []
        if ctx["project_path"]:
            parts.append(f"📁 项目路径: {ctx['project_path']}")
        if ctx["line_numbers"]:
            parts.append(f"🔢 相关行号: {', '.join(str(n) for n in ctx['line_numbers'])}")
        if ctx["metrics"]:
            parts.append(f"📊 指标: {', '.join(f'{k}={v}' for k, v in ctx['metrics'].items())}")
        if ctx["issues"]:
            parts.append(f"🐛 问题: {', '.join(ctx['issues'])}")
        if ctx["files"]:
            parts.append(f"📄 文件: {', '.join(ctx['files'])}")
        if ctx["scripts"]:
            parts.append(f"▶ 脚本: {', '.join(ctx['scripts'])}")
        if ctx["raw_snippets"]:
            parts.append("💬 相关对话片段:\n" + "\n".join(f"  - {s[:100]}" for s in ctx["raw_snippets"]))

        return "\n".join(parts)
