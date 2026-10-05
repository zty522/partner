"""Partner v5 open-generalization research Flow."""
from partner.event_fabric.flows import EventFlowDefinition as Flow, FlowNode as Node

V5_RESEARCH_STUDY = Flow("v5_research_study", "1.0.0", (
    Node("protocol_freeze", "v5.protocol_freeze"),
    Node("environment_snapshot", "v5.environment_snapshot", ("protocol_freeze",)),
    Node("corpus_audit", "v5.corpus_audit", ("environment_snapshot",)),
    Node("start_compose", "v5.start_compose", ("corpus_audit",)),
    Node("start_send", "v5.start_send", ("start_compose",)),
    Node("knowledge_extract", "v5.knowledge_extract", ("start_send",)),
    Node("ablation_execute", "v5.ablation_execute", ("knowledge_extract",)),
    Node("longitudinal_audit", "v5.longitudinal_audit", ("ablation_execute",)),
    Node("self_evolution_audit", "v5.self_evolution_audit", ("longitudinal_audit",)),
    Node("human_annotation", "v5.human_annotation", ("self_evolution_audit",)),
    Node("reproducibility", "v5.reproducibility", ("human_annotation",)),
    Node("statistics", "v5.statistics", ("reproducibility",)),
    Node("settlement", "v5.settlement", ("statistics",)),
    Node("report", "v5.report", ("settlement",)),
    Node("reproduction_bundle", "v5.reproduction_bundle", ("report",)),
    Node("final_compose", "v5.final_compose", ("reproduction_bundle",)),
    Node("delivery", "v5.delivery", ("final_compose",)),
    Node("delivery_ack", "v5.delivery_ack", ("delivery",)),
    Node("close", "v5.close", ("delivery_ack",)),
), "Frozen cross-project ablations, external validation gates, and reproducible evidence bundle.")

DEFINITIONS = (V5_RESEARCH_STUDY,)
