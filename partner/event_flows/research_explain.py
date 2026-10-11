"""research_explain: bounded single-pass summary/research/explain flow.

Selected when workstream_type == 'research_explain' (user asks to summarize
learned content, research a direction, explain something, or give improvement
recommendations).  It consumes real notes/learned content and real external
sources, then one LLM synthesize produces a plain-language explanation.
No continuation nodes: an explain task is done when the message is delivered.
"""
from partner.event_fabric.flows import EventFlowDefinition as Flow, FlowNode as Node

RESEARCH_EXPLAIN = Flow('research_explain', '1.0.0', (
    Node('input_resolve', 'cycle.input_resolve'),
    Node('input_eligibility', 'cycle.input_eligibility', ('input_resolve',)),
    Node('recall', 'memory.context_recall', ('input_eligibility',)),
    Node('research', 'active_learning.source_retrieve', ('recall',)),
    Node('synthesize', 'research_explain.synthesize', ('research',)),
    Node('send', 'delivery.send_text', ('synthesize',)),
), '总结已学内容 + 调研方向 + 生成面向用户的讲解与建议；单趟完成，不进入项目循环或续跑。'
   'Soft-orchestrated: the LLM may adjust the remaining graph at runtime against the shared Event pool.',
   soft_orchestrated=True)

DEFINITIONS = [RESEARCH_EXPLAIN]
