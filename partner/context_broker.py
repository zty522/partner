"""DEPRECATED — 上下文由 Hermes 读取状态文件自行理解。

Context Broker — 打通对话上下文与研究循环。

负责在 QQ 对话与 Mind 自主研究循环之间建立桥梁：
1. 从 dialog_history.jsonl 提取项目关键信息
2. 将对话中提炼的事实写入 knowledge.json
3. 为研究循环提供完整的项目上下文
"""

import json
import os
import re
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .workspace_layout import dialog_history_path, ensure_instance_layout

logger = logging.getLogger(__name__)


class ContextBroker:
    """对话上下文代理：解析对话、提取事实、注入研究循环。"""

    def __init__(self, workspace: str, knowledge_base=None, journal=None):
        """初始化 ContextBroker。

        Args:
            workspace: Partner 工作区路径
            knowledge_base: KnowledgeBase 实例（用于保存/检索知识）
            journal: Journal 实例（用于记录日志）
        """
        self.workspace = workspace
        self.knowledge = knowledge_base
        self.journal = journal
        ensure_instance_layout(workspace)
        self.dialog_history_path = dialog_history_path(workspace)
        # 如果 knowledge.json 路径有自定义格式，在此记录
        self.knowledge_custom_path = os.path.join(
            workspace, "state", "knowledge_context.json"
        )
        # 搜索查询短时记忆（2小时内同一query不重复）
        self.last_search_queries_path = os.path.join(
            workspace, "state", "last_search_queries.json"
        )
        self._search_query_cache: Dict[str, float] = {}
        self._load_search_query_cache()

    def _load_search_query_cache(self):
        """从 last_search_queries.json 加载查询缓存。"""
        if os.path.exists(self.last_search_queries_path):
            try:
                with open(self.last_search_queries_path, "r") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._search_query_cache = data
            except Exception:
                self._search_query_cache = {}

    def _save_search_query_cache(self):
        """保存查询缓存到磁盘。"""
        try:
            os.makedirs(os.path.dirname(self.last_search_queries_path), exist_ok=True)
            with open(self.last_search_queries_path, "w") as f:
                json.dump(self._search_query_cache, f, indent=2)
        except Exception:
            pass

    def is_query_recent(self, query: str, hours: int = 2) -> bool:
        """检查查询在指定小时内是否已使用过。

        Args:
            query: 查询字符串
            hours: 时间窗口（默认2小时）

        Returns:
            True 如果该查询在最近 hours 小时内已使用过
        """
        now = time.time()
        query_key = query.strip().lower()
        last_ts = self._search_query_cache.get(query_key, 0)
        return (now - last_ts) < hours * 3600

    def record_search_query(self, query: str):
        """记录一次搜索查询（用于去重）。

        Args:
            query: 已执行的查询字符串
        """
        query_key = query.strip().lower()
        self._search_query_cache[query_key] = time.time()
        # 清理超过 24 小时的旧条目
        now = time.time()
        stale = [k for k, v in self._search_query_cache.items() if now - v > 86400]
        for k in stale:
            del self._search_query_cache[k]
        self._save_search_query_cache()

    # ── 对话数据加载 ─────────────────────────────────────────────

    def _load_all_dialogs(self) -> List[Dict]:
        """从 dialog_history.jsonl 加载所有对话记录。"""
        if not os.path.exists(self.dialog_history_path):
            return []
        entries = []
        try:
            with open(self.dialog_history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning(f"[ContextBroker] 加载对话历史失败: {e}")
        return entries

    # ── 方法 5: 收集最近对话 ─────────────────────────────────────

    def collect_recent_dialogs(self, hours: int = 24) -> List[Dict]:
        """从 dialog_history.jsonl 获取最近 N 小时的对话记录。

        Args:
            hours: 回溯的小时数（默认 24）

        Returns:
            最近 hours 小时内的对话条目列表（每项含 role/content/timestamp 等）
        """
        all_entries = self._load_all_dialogs()
        if not all_entries:
            return []

        cutoff = datetime.now() - timedelta(hours=hours)
        recent = []

        for entry in all_entries:
            ts_str = entry.get("timestamp", "")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts >= cutoff:
                    recent.append(entry)
            except (ValueError, TypeError):
                continue

        logger.info(
            f"[ContextBroker] 收集到 {len(recent)} 条最近对话 (回溯 {hours}h)"
        )
        return recent

    # ── 方法 2: 提取项目关键信息 ─────────────────────────────────

    def extract_project_facts(self, dialog_entries: List[Dict]) -> Dict:
        """从对话记录中提取项目关键信息。

        识别模式：
        - 指标/数值：如 MAE=7.08, accuracy=0.95, loss=0.32 等
        - 问题/缺陷：如 batch_correction_leak, data_leakage 等
        - 文件路径：如 /path/to/file.py, experiments/results/ 等
        - 技术关键词：如 transformer, diffusion, normalization 等

        Args:
            dialog_entries: 对话条目列表（role/content/timestamp）

        Returns:
            结构化的事实字典，包含：
            {
                "metrics": {"MAE": "7.08", ...},
                "issues": ["batch_correction_leak", ...],
                "files": ["path/to/file.py", ...],
                "keywords": ["transformer", ...],
                "recent_topics": ["topic1", ...],
            }
        """
        facts = {
            "metrics": {},
            "issues": [],
            "files": [],
            "keywords": [],
            "recent_topics": [],
            "raw_snippets": [],
        }

        # 收集最近的话题
        topics_seen = set()

        for entry in dialog_entries:
            content = entry.get("content", "")
            topic = entry.get("topic", "")
            role = entry.get("role", "")

            # 收集话题
            if topic and topic not in topics_seen:
                topics_seen.add(topic)
                facts["recent_topics"].append(topic)

            if not content:
                continue

            # 1. 提取指标：匹配 KEY=value 模式（如 MAE=7.08, acc=0.95）
            metric_pattern = re.compile(
                r'(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*[=：:]\s*(?P<value>[\d]+\.[\d]+)'
            )
            for m in metric_pattern.finditer(content):
                key = m.group("key").strip()
                value = m.group("value").strip()
                # 只收录看起来像指标的键（大写缩写或含精度/损失等）
                if (key.isupper() and len(key) <= 8) or any(
                    kw in key.lower()
                    for kw in ["acc", "loss", "mae", "mse", "rmse", "f1", "precis", "recall", "bleu", "perplex"]
                ):
                    facts["metrics"][key] = value

            # 2. 提取问题/缺陷：检测 error/bug/leak/issue 等关键词
            issue_pattern = re.compile(
                r'(?:error|bug|leak|leakage|issue|问题|缺陷|错误|故障)\s*[：:]\s*([A-Za-z_][A-Za-z0-9_]*)',
                re.IGNORECASE,
            )
            for m in issue_pattern.finditer(content):
                issue = m.group(1).strip()
                if issue and issue not in facts["issues"]:
                    facts["issues"].append(issue)

            # 3. 提取文件路径
            file_pattern = re.compile(
                r'(?:/[\w._-]+)+[/\w._-]*\.(?:py|json|yaml|yml|toml|cfg|txt|csv|md|sh|js|ts|css|html)'
            )
            for m in file_pattern.finditer(content):
                fpath = m.group(0).strip()
                if fpath not in facts["files"]:
                    facts["files"].append(fpath)

            # 4. 提取技术关键词（含双下划线的 camelCase 标识符）
            kw_pattern = re.compile(r'\b([a-z]+[A-Z][a-zA-Z]+)\b')
            for m in kw_pattern.finditer(content):
                kw = m.group(1).strip()
                # 排除常见英文单词
                if 4 <= len(kw) <= 30 and kw not in facts["keywords"]:
                    facts["keywords"].append(kw)

            # 5. 保留有事实含量的对话片段（用户或 partner 提及数值/代码的）
            if role in ("user", "partner") and (
                re.search(r'\d+\.\d+', content) or re.search(r'/[a-z_]+/[a-z_]+', content)
            ):
                snippet = content[:200].strip()
                if snippet and len(facts["raw_snippets"]) < 20:
                    facts["raw_snippets"].append(snippet)

        # 去重和修剪
        facts["recent_topics"] = facts["recent_topics"][-5:]
        facts["keywords"] = facts["keywords"][:15]
        facts["files"] = facts["files"][:10]

        logger.info(
            f"[ContextBroker] 提取到 {len(facts['metrics'])} 个指标, "
            f"{len(facts['issues'])} 个问题, "
            f"{len(facts['files'])} 个文件"
        )
        return facts

    # ── 方法 3: 保存到知识库 ─────────────────────────────────────

    def save_to_knowledge(self, project_name: str, facts: Dict):
        """将对话中提取的信息写入 knowledge.json 和 Recorder 记录系统。

        格式：
        {
            "source": "dialog",
            "project": project_name,
            "timestamp": "...",
            "facts": { ... }
        }

        Args:
            project_name: 项目名称
            facts: extract_project_facts() 返回的事实字典
        """
        saved_any = False

        # --- 原有的 knowledge_context.json 保存逻辑 ---
        if self.knowledge:
            try:
                # 构造可存储的知识条目
                entry_data = {
                    "source": "dialog",
                    "project": project_name,
                    "timestamp": datetime.now().isoformat(),
                    "facts": facts,
                }

                # 写入独立的 knowledge_context.json（保持 knowledge.json 原有格式干净）
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
                # 最多保留 50 条
                if len(existing) > 50:
                    existing = existing[-50:]

                os.makedirs(os.path.dirname(self.knowledge_custom_path), exist_ok=True)
                with open(self.knowledge_custom_path, "w", encoding="utf-8") as f:
                    json.dump(existing, f, indent=2, ensure_ascii=False)

                logger.info(f"[ContextBroker] 项目 '{project_name}' 的事实已保存 ({len(facts)} 项)")
                saved_any = True

            except Exception as e:
                logger.error(f"[ContextBroker] 保存知识失败: {e}")

        # --- 新增：使用 Recorder 记录系统保存结构化知识条目 ---
        try:
            from .recorder import Recorder
            recorder = Recorder(self.workspace)

            # 记录指标
            metrics = facts.get("metrics", {})
            if metrics:
                metric_content = ", ".join(f"{k}={v}" for k, v in metrics.items())
                recorder.add_knowledge(
                    project=project_name,
                    entry_type="metric",
                    content=f"Experiment metrics: {metric_content}",
                    source="user_dialog",
                    confidence=0.8,
                )

            # 记录发现的问题
            issues = facts.get("issues", [])
            for issue in issues:
                recorder.add_knowledge(
                    project=project_name,
                    entry_type="issue",
                    content=f"Issue identified: {issue}",
                    source="user_dialog",
                    confidence=0.7,
                )

            # 记录相关文件
            files = facts.get("files", [])
            if files:
                recorder.add_knowledge(
                    project=project_name,
                    entry_type="file_reference",
                    content=f"Related files: {', '.join(files[:8])}",
                    source="user_dialog",
                    confidence=0.9,
                )

            # 记录技术关键词
            keywords = facts.get("keywords", [])
            if keywords:
                recorder.add_knowledge(
                    project=project_name,
                    entry_type="keyword",
                    content=f"Technical keywords: {', '.join(keywords[:12])}",
                    source="user_dialog",
                    confidence=0.6,
                )

            # 记录原始对话片段（如果有）
            snippets = facts.get("raw_snippets", [])
            for snippet in snippets[-3:]:  # Last 3 snippets
                recorder.add_knowledge(
                    project=project_name,
                    entry_type="dialog_snippet",
                    content=snippet[:300],
                    source="user_dialog",
                    confidence=0.8,
                )

            logger.info(f"[ContextBroker] 已通过 Recorder 记录 {project_name} 的对话知识")
            saved_any = True

        except ImportError:
            # Recorder not available — non-fatal
            pass
        except Exception as e:
            logger.warning(f"[ContextBroker] Recorder save failed (non-fatal): {e}")

        if not saved_any:
            logger.warning("[ContextBroker] knowledge 未初始化，无法保存")

    # ── 方法 4: 获取项目上下文 ───────────────────────────────────

    def get_project_context(self, project_name: str) -> str:
        """从 knowledge.json + dialog_history 获取项目的完整上下文文本。

        整合所有来源的信息，生成一个可读的上下文描述。

        Args:
            project_name: 项目名称或话题关键词

        Returns:
            格式化的上下文文本（供 LLM 注入）
        """
        parts = []

        # 1. 从 knowledge_context.json 中查找相关条目
        if os.path.exists(self.knowledge_custom_path):
            try:
                with open(self.knowledge_custom_path, "r", encoding="utf-8") as f:
                    ctx_entries = json.load(f)
                for entry in ctx_entries:
                    if not isinstance(entry, dict):
                        continue
                    proj = entry.get("project", "")
                    if project_name.lower() in proj.lower() or proj.lower() in project_name.lower():
                        facts = entry.get("facts", {})
                        lines = ["【从对话中提取的项目事实】"]
                        metrics = facts.get("metrics", {})
                        if metrics:
                            lines.append("指标: " + ", ".join(f"{k}={v}" for k, v in metrics.items()))
                        issues = facts.get("issues", [])
                        if issues:
                            lines.append("发现的问题: " + ", ".join(issues))
                        files = facts.get("files", [])
                        if files:
                            lines.append("相关文件: " + ", ".join(files))
                        keywords = facts.get("keywords", [])
                        if keywords:
                            lines.append("技术关键词: " + ", ".join(keywords[:8]))
                        parts.append("\n".join(lines))
            except Exception as e:
                logger.warning(f"[ContextBroker] 读取知识上下文失败: {e}")

        # 2. 从 knowledge.py 的 KnowledgeBase 中搜索（如果可用）
        if self.knowledge:
            try:
                kb_results = self.knowledge.search(project_name, top_k=3)
                if kb_results:
                    kb_lines = ["【知识库相关条目】"]
                    for e in kb_results:
                        kb_lines.append(f"- [{e.category}] {e.title}")
                        if e.content:
                            kb_lines.append(f"  {e.content[:200]}")
                    parts.append("\n".join(kb_lines))
            except Exception as e:
                logger.warning(f"[ContextBroker] 知识库搜索失败: {e}")

        # 3. 从 dialog_history 中提取最近的对话摘要
        recent = self.collect_recent_dialogs(hours=24)
        if recent:
            dialog_lines = ["【最近对话摘要】"]
            for entry in recent[-5:]:
                role = entry.get("role", "?")
                content = entry.get("content", "")[:150]
                label = "用户" if role == "user" else "Partner"
                dialog_lines.append(f"{label}: {content}")
            parts.append("\n".join(dialog_lines))

        if not parts:
            return f"项目「{project_name}」暂无上下文记录。"

        return "\n\n".join(parts)
