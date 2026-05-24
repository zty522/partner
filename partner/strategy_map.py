"""Strategy Map — 基于 APEX 的 DAG 任务编排层

用有向无环图(DAG)组织研究里程碑，替代扁平任务队列的简单优先级排序。
核心概念：
- MilestoneNode: 研究里程碑（可达成的目标）
- StrategyEdge: 里程碑间的依赖关系
- Fork Discovery: 从已达成里程碑发现新探索方向
- Policy Selection: 智能选择下一个执行目标
"""

import json
import os
import uuid
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


# ============================================================
# Data Classes
# ============================================================

@dataclass
class MilestoneNode:
    """里程碑节点 — DAG 中的核心实体"""
    id: str = field(default_factory=lambda: f"ms_{uuid.uuid4().hex[:8]}")
    title: str = ""
    description: str = ""
    status: str = "pending"  # pending | active | achieved | failed | skipped
    priority: int = 5
    tags: List[str] = field(default_factory=list)
    task_ids: List[str] = field(default_factory=list)  # 关联的 task_queue 任务
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    achieved_at: Optional[str] = None
    evidence: str = ""  # 达成证据/结果摘要
    fork_generated: bool = False  # 是否已生成 fork
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MilestoneNode":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class StrategyEdge:
    """依赖边 — 里程碑间的关系"""
    from_id: str = ""
    to_id: str = ""
    edge_type: str = "prerequisite"  # prerequisite | enables | informs | blocks
    weight: float = 1.0  # 0.0-1.0, 依赖强度
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyEdge":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid})


# ============================================================
# StrategyMap — DAG 主体
# ============================================================

class StrategyMap:
    """研究策略图 — 用 DAG 编排研究里程碑"""

    MAX_DEPTH = 10

    def __init__(self, path: str):
        self.path = path
        self.nodes: Dict[str, MilestoneNode] = {}
        self.edges: List[StrategyEdge] = []
        self._load()

    # ----------------------------------------------------------
    # CRUD
    # ----------------------------------------------------------

    def add_node(self, node: MilestoneNode) -> str:
        """添加里程碑节点"""
        self.nodes[node.id] = node
        self._save()
        return node.id

    def add_edge(self, from_id: str, to_id: str,
                 edge_type: str = "prerequisite",
                 weight: float = 1.0) -> None:
        """添加依赖边"""
        if from_id not in self.nodes or to_id not in self.nodes:
            raise ValueError(f"Node not found: {from_id} or {to_id}")
        # 检测环
        if self._would_create_cycle(from_id, to_id):
            raise ValueError(f"Edge {from_id} -> {to_id} would create cycle")
        edge = StrategyEdge(from_id=from_id, to_id=to_id,
                           edge_type=edge_type, weight=weight)
        self.edges.append(edge)
        self._save()

    def remove_node(self, node_id: str) -> None:
        """移除节点及其相关边"""
        self.nodes.pop(node_id, None)
        self.edges = [e for e in self.edges
                      if e.from_id != node_id and e.to_id != node_id]
        self._save()

    def achieve(self, node_id: str, evidence: str = "") -> None:
        """标记里程碑为已达成"""
        node = self.nodes.get(node_id)
        if not node:
            return
        node.status = "achieved"
        node.achieved_at = datetime.now().isoformat()
        node.evidence = evidence
        self._save()
        logger.info(f"Milestone achieved: {node.title}")

    def fail(self, node_id: str, reason: str = "") -> None:
        """标记里程碑为失败"""
        node = self.nodes.get(node_id)
        if not node:
            return
        node.status = "failed"
        node.evidence = reason
        self._save()

    def activate(self, node_id: str) -> None:
        """标记里程碑为进行中"""
        node = self.nodes.get(node_id)
        if not node:
            return
        node.status = "active"
        self._save()

    # ----------------------------------------------------------
    # 查询
    # ----------------------------------------------------------

    def get_ready_nodes(self) -> List[MilestoneNode]:
        """获取所有前置已达成的 pending 节点（可执行）"""
        ready = []
        for node in self.nodes.values():
            if node.status != "pending":
                continue
            # 检查所有 prerequisite 前置是否已达成
            prereqs = [e for e in self.edges
                       if e.to_id == node.id and e.edge_type == "prerequisite"]
            if all(self.nodes[e.from_id].status == "achieved"
                   for e in prereqs
                   if e.from_id in self.nodes):
                # 排除被 block 的
                blockers = [e for e in self.edges
                           if e.to_id == node.id and e.edge_type == "blocks"
                           and e.from_id in self.nodes
                           and self.nodes[e.from_id].status != "achieved"]
                if not blockers:
                    ready.append(node)
        return ready

    def get_frontier(self) -> List[MilestoneNode]:
        """获取叶子节点（无后继的节点）"""
        has_successor = set()
        for e in self.edges:
            has_successor.add(e.from_id)
        return [n for n in self.nodes.values()
                if n.id not in has_successor and n.status in ("pending", "active")]

    def get_roots(self) -> List[MilestoneNode]:
        """获取根节点（无前驱的节点）"""
        has_predecessor = set()
        for e in self.edges:
            has_predecessor.add(e.to_id)
        return [n for n in self.nodes.values() if n.id not in has_predecessor]

    def get_achieved(self) -> List[MilestoneNode]:
        """获取已达成的节点"""
        return [n for n in self.nodes.values() if n.status == "achieved"]

    def get_successors(self, node_id: str) -> List[MilestoneNode]:
        """获取直接后继节点"""
        succ_ids = [e.to_id for e in self.edges if e.from_id == node_id]
        return [self.nodes[sid] for sid in succ_ids if sid in self.nodes]

    def get_predecessors(self, node_id: str) -> List[MilestoneNode]:
        """获取直接前驱节点"""
        pred_ids = [e.from_id for e in self.edges if e.to_id == node_id]
        return [self.nodes[pid] for pid in pred_ids if pid in self.nodes]

    def compute_depth(self, node_id: str) -> int:
        """计算节点深度（最长路径从根到此节点）"""
        memo = {}

        def _depth(nid: str) -> int:
            if nid in memo:
                return memo[nid]
            preds = [e.from_id for e in self.edges if e.to_id == nid]
            if not preds:
                memo[nid] = 0
                return 0
            d = 1 + max(_depth(pid) for pid in preds if pid in self.nodes)
            memo[nid] = d
            return d

        return _depth(node_id)

    # ----------------------------------------------------------
    # Fork Discovery
    # ----------------------------------------------------------

    def discover_forks(self, node_id: str,
                       knowledge_tags: Optional[List[str]] = None
                       ) -> List[MilestoneNode]:
        """从已达成节点发现新的探索方向

        基于达成证据和知识库标签，生成新的候选里程碑。
        """
        node = self.nodes.get(node_id)
        if not node or node.status != "achieved":
            return []
        if node.fork_generated:
            return []

        forks = []

        # 策略1: 基于 evidence 中的关键词扩展
        keywords = self._extract_keywords(node.evidence)
        existing_tags = set()
        for n in self.nodes.values():
            existing_tags.update(n.tags)

        # 策略2: 基于 knowledge_tags 找相关但未覆盖的方向
        if knowledge_tags:
            for tag in knowledge_tags:
                if tag not in existing_tags and tag not in set(node.tags):
                    fork = MilestoneNode(
                        title=f"探索 {tag}（从 {node.title} 分支）",
                        description=f"基于 {node.title} 的达成，扩展探索 {tag} 方向",
                        priority=max(1, node.priority - 1),
                        tags=node.tags + [tag, "fork"],
                        metadata={"source_node": node_id, "fork_type": "tag_expansion"},
                    )
                    forks.append(fork)

        # 策略3: 基于已有后继生成互补方向
        existing_succ_tags = set()
        for succ in self.get_successors(node_id):
            existing_succ_tags.update(succ.tags)

        for kw in keywords[:3]:  # 最多 3 个关键词 fork
            if kw not in existing_succ_tags:
                fork = MilestoneNode(
                    title=f"深入 {kw}（从 {node.title} 分支）",
                    description=f"基于 {node.title} 的达成证据，深入研究 {kw}",
                    priority=max(1, node.priority - 2),
                    tags=node.tags + [kw, "fork"],
                    metadata={"source_node": node_id, "fork_type": "keyword_deep"},
                )
                forks.append(fork)

        # 限制 fork 数量
        forks = forks[:5]

        # 注册 fork
        for fork in forks:
            self.add_node(fork)
            try:
                self.add_edge(node_id, fork.id, "enables", 0.8)
            except ValueError:
                pass  # cycle detected, skip

        node.fork_generated = True
        self._save()

        if forks:
            logger.info(f"Discovered {len(forks)} forks from {node.title}")

        return forks

    # ----------------------------------------------------------
    # Policy Selection
    # ----------------------------------------------------------

    def select_next(self) -> Optional[MilestoneNode]:
        """智能选择下一个执行目标

        综合考虑：优先级、深度、扇出、时间衰减、探索奖励
        """
        ready = self.get_ready_nodes()
        if not ready:
            return None

        now = datetime.now()
        scored = []

        for node in ready:
            score = 0.0

            # 1. 基础优先级 (0-15 → 0-30)
            score += node.priority * 2.0

            # 2. 深度奖励：靠近根节点优先（深度优先遍历）
            depth = self.compute_depth(node.id)
            score += max(0, 5 - depth) * 1.0

            # 3. 扇出奖励：后继越多越优先（解锁更多任务）
            successors = self.get_successors(node.id)
            score += len(successors) * 1.5

            # 4. 时间衰减：等待越久优先级越高
            try:
                created = datetime.fromisoformat(node.created_at)
                age_hours = (now - created).total_seconds() / 3600
                score += min(5.0, age_hours / 24.0)
            except (ValueError, TypeError):
                pass

            # 5. 关联任务数奖励：有更多待执行任务的里程碑更值得优先
            score += len(node.task_ids) * 0.5

            # 6. 探索奖励：新 fork 的高优先级节点
            if "fork" in node.tags and node.priority >= 8:
                score += 3.0

            scored.append((node, score))

        scored.sort(key=lambda x: -x[1])
        return scored[0][0]

    # ----------------------------------------------------------
    # 迁移辅助
    # ----------------------------------------------------------

    def import_from_tasks(self, tasks: List[dict]) -> int:
        """从 task_queue 导入任务，按 tag 聚类生成里程碑

        返回创建的里程碑数。
        """
        # 按 tag 聚类
        tag_groups: Dict[str, List[dict]] = defaultdict(list)
        untagged = []

        for t in tasks:
            if t.get("status") != "pending":
                continue
            tags = t.get("tags", [])
            if tags:
                for tag in tags[:2]:  # 取前两个 tag
                    tag_groups[tag].append(t)
            else:
                untagged.append(t)

        created = 0

        # 为每个 tag 组创建里程碑
        for tag, group_tasks in sorted(tag_groups.items(),
                                        key=lambda x: -len(x[1])):
            # 只为 ≥2 个任务的组创建里程碑
            if len(group_tasks) < 2:
                for t in group_tasks:
                    untagged.append(t)
                continue

            max_priority = max(t.get("priority", 5) for t in group_tasks)
            task_ids = [t["id"] for t in group_tasks]

            node = MilestoneNode(
                title=f"完成 {tag} 方向研究",
                description=f"聚合 {len(group_tasks)} 个相关任务: " +
                           ", ".join(t.get("title", "")[:30] for t in group_tasks[:3]),
                priority=max_priority,
                tags=[tag, "imported"],
                task_ids=task_ids,
                metadata={"import_count": len(group_tasks)},
            )
            self.add_node(node)
            created += 1

        # 为零散任务创建通用里程碑
        if untagged:
            # 按优先级分组
            high = [t for t in untagged if t.get("priority", 5) >= 10]
            medium = [t for t in untagged if 5 <= t.get("priority", 5) < 10]
            low = [t for t in untagged if t.get("priority", 5) < 5]

            for label, group, pri in [
                ("高优先级零散任务", high, 12),
                ("中优先级零散任务", medium, 7),
                ("低优先级零散任务", low, 3),
            ]:
                if group:
                    node = MilestoneNode(
                        title=label,
                        description=f"包含 {len(group)} 个独立任务",
                        priority=pri,
                        tags=["misc", "imported"],
                        task_ids=[t["id"] for t in group],
                    )
                    self.add_node(node)
                    created += 1

        # 建立简单依赖：高优先级里程碑 -> 低优先级
        all_nodes = list(self.nodes.values())
        all_nodes.sort(key=lambda n: -n.priority)
        for i, node in enumerate(all_nodes[:10]):  # 只为前 10 个建依赖
            for j in range(i + 1, min(i + 3, len(all_nodes))):
                target = all_nodes[j]
                if node.priority > target.priority + 3:
                    try:
                        self.add_edge(node.id, target.id, "enables", 0.6)
                    except ValueError:
                        pass

        logger.info(f"Imported {created} milestones from {len(tasks)} tasks")
        return created

    # ----------------------------------------------------------
    # 可视化
    # ----------------------------------------------------------

    def to_mermaid(self) -> str:
        """生成 Mermaid 流程图代码"""
        lines = ["graph TD"]

        status_style = {
            "achieved": ":::achieved",
            "active": ":::active",
            "failed": ":::failed",
            "pending": "",
            "skipped": ":::skipped",
        }

        # 节点
        for nid, node in self.nodes.items():
            safe_title = node.title.replace('"', "'")[:40]
            style = status_style.get(node.status, "")
            lines.append(f'    {nid}["{safe_title}"]{style}')

        # 边
        edge_styles = {
            "prerequisite": "-->",
            "enables": "-..->",
            "informs": "-.->",
            "blocks": "--x",
        }
        for e in self.edges:
            arrow = edge_styles.get(e.edge_type, "-->")
            lines.append(f"    {e.from_id} {arrow} {e.to_id}")

        # 样式
        lines.extend([
            "    classDef achieved fill:#90EE90,stroke:#333",
            "    classDef active fill:#87CEEB,stroke:#333",
            "    classDef failed fill:#FFB6C1,stroke:#333",
            "    classDef skipped fill:#D3D3D3,stroke:#333",
        ])

        return "\n".join(lines)

    def to_ascii(self) -> str:
        """生成简单的 ASCII 树形图"""
        roots = self.get_roots()
        if not roots:
            return "(empty strategy map)"

        lines = []
        visited = set()

        def _render(node: MilestoneNode, prefix: str, is_last: bool):
            if node.id in visited:
                lines.append(f"{prefix}{'└── ' if is_last else '├── '}[{node.status}] {node.title} (circular)")
                return
            visited.add(node.id)

            connector = "└── " if is_last else "├── "
            status_icon = {"achieved": "✅", "active": "🔵", "pending": "⏳",
                          "failed": "❌", "skipped": "⏭"}.get(node.status, "?")
            lines.append(f"{prefix}{connector}{status_icon} {node.title} [P{node.priority}]")

            children = self.get_successors(node.id)
            child_prefix = prefix + ("    " if is_last else "│   ")
            for i, child in enumerate(children):
                _render(child, child_prefix, i == len(children) - 1)

        for i, root in enumerate(sorted(roots, key=lambda n: -n.priority)):
            status_icon = {"achieved": "✅", "active": "🔵", "pending": "⏳",
                          "failed": "❌", "skipped": "⏭"}.get(root.status, "?")
            lines.append(f"{status_icon} {root.title} [P{root.priority}]")
            children = self.get_successors(root.id)
            for j, child in enumerate(children):
                _render(child, "", j == len(children) - 1)
            if i < len(roots) - 1:
                lines.append("")

        return "\n".join(lines)

    def summary(self) -> dict:
        """返回统计摘要"""
        by_status = defaultdict(int)
        for n in self.nodes.values():
            by_status[n.status] += 1

        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "by_status": dict(by_status),
            "ready_count": len(self.get_ready_nodes()),
            "frontier_count": len(self.get_frontier()),
            "achieved_count": len(self.get_achieved()),
        }

    # ----------------------------------------------------------
    # 序列化
    # ----------------------------------------------------------

    def _load(self):
        """从文件加载"""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.loads(f.read(), strict=False)
            self.nodes = {
                nid: MilestoneNode.from_dict(nd)
                for nid, nd in data.get("nodes", {}).items()
            }
            self.edges = [
                StrategyEdge.from_dict(e) for e in data.get("edges", [])
            ]
        except (FileNotFoundError, json.JSONDecodeError):
            self.nodes = {}
            self.edges = []

    def _save(self):
        """保存到文件"""
        data = {
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
            "meta": {
                "created_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "version": 1,
                "total_nodes": len(self.nodes),
                "total_edges": len(self.edges),
            },
        }
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ----------------------------------------------------------
    # 内部工具
    # ----------------------------------------------------------

    def _would_create_cycle(self, from_id: str, to_id: str) -> bool:
        """检测添加 from_id -> to_id 边是否会产生环"""
        if from_id == to_id:
            return True
        # BFS: 从 to_id 出发，看能否到达 from_id
        adj = defaultdict(list)
        for e in self.edges:
            adj[e.from_id].append(e.to_id)
        adj[from_id].append(to_id)  # 假设添加

        visited = set()
        queue = deque([to_id])
        while queue:
            current = queue.popleft()
            if current == from_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            for neighbor in adj[current]:
                if neighbor not in visited:
                    queue.append(neighbor)
        return False

    def _extract_keywords(self, text: str) -> List[str]:
        """从文本中提取关键词（简单实现）"""
        if not text:
            return []
        # 去除常见停用词，提取有意义的词
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "can", "shall",
            "of", "in", "to", "for", "with", "on", "at", "from", "by",
            "and", "or", "but", "not", "no", "if", "then", "else",
            "的", "了", "是", "在", "和", "与", "对", "中", "为", "不",
            "有", "这", "那", "也", "就", "都", "而", "及", "到",
        }
        import re
        words = re.findall(r'[\w\u4e00-\u9fff]+', text.lower())
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        # 去重保序
        seen = set()
        result = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
        return result[:10]
