from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


class KnowledgeTransfer:
    def transfer(self, source: str, target: str) -> dict:
        """Transfer knowledge from source direction to target."""
        result = {"source": source, "target": target, "transfers": []}

        if source == "bioinformatics" and target in (
            "agent_dispatch",
            "world_model",
        ):
            # Transfer tool discovery pattern
            result["transfers"].append(
                {
                    "pattern": "PATH扫描发现可用工具",
                    "from": "CapabilityDiscovery.discover_bioinformatics_tools()",
                    "to": f"扫描 {target} 的可用组件",
                }
            )
        return result

    def suggest_transfers(self, completed_directions: list[str]) -> list[dict]:
        """Suggest possible knowledge transfers between completed directions."""
        suggestions = []
        pairs = [
            ("bioinformatics", "agent_dispatch"),
            ("bioinformatics", "world_model"),
            ("bioinformatics", "self_evolution"),
            ("NatureBench", "all"),
        ]
        for src, tgt in pairs:
            if src in completed_directions and (
                tgt == "all" or tgt in completed_directions
            ):
                suggestions.append(self.transfer(src, tgt))
        return suggestions
