"""Canonical flows for user work and the three distinct learning lines."""
from __future__ import annotations

from partner.event_fabric.flows import EventFlowDefinition as Flow, FlowNode as Node


INTENT_FLOW = Flow("intent", "1.0.0", (
    Node("observe", "interaction.intent_observe"),
    Node("counter_read", "interaction.intent_counter_read", ("observe",)),
    Node("synthesize", "interaction.intent_synthesize", ("counter_read",)),
), "Three-pass intent understanding; route, dispatch_target and warm_reply live in synthesize output.")

#: The previous topology, kept resolvable for requests already pinned to 1.0.0.
DIRECT_ANSWER_V1 = Flow("direct_answer", "1.0.0", (
    Node("understand_1", "interaction.intent_observe"),
    Node("understand_2", "interaction.intent_counter_read", ("understand_1",)),
    Node("understand_3", "interaction.intent_synthesize", ("understand_2",)),
    Node("answer", "interaction.direct_answer", ("understand_3",)),
    Node("compose", "presentation.message_compose", ("answer",)),
    Node("critic", "presentation.message_critic", ("compose",)),
    Node("deduplicate", "presentation.message_deduplicate", ("critic",)),
    Node("send", "delivery.send_text", ("deduplicate",)),
    Node("verify", "delivery.verify", ("send",)),
), "Simple answer with three-pass understanding and verified delivery.")

# 1.1.0 adds one node: once the message is understood, the commitment kernel
# records a BetRecord for it.  It is record-only -- no action, no LLM, no project
# work -- so the flow still answers the message exactly as before.
DIRECT_ANSWER_V1_1 = Flow("direct_answer", "1.1.0", (
    Node("understand_1", "interaction.intent_observe"),
    Node("understand_2", "interaction.intent_counter_read", ("understand_1",)),
    Node("understand_3", "interaction.intent_synthesize", ("understand_2",)),
    Node("commitment", "commitment.bet_record", ("understand_3",)),
    Node("answer", "interaction.direct_answer", ("understand_3",)),
    Node("compose", "presentation.message_compose", ("answer",)),
    Node("critic", "presentation.message_critic", ("compose",)),
    Node("deduplicate", "presentation.message_deduplicate", ("critic",)),
    Node("send", "delivery.send_text", ("deduplicate",)),
    Node("verify", "delivery.verify", ("send",)),
), "Answer plus a recorded commitment BetRecord for the triggering message.")

# 1.2.0 adds the execution half: the recorded bet is run to a terminal state with a
# bounded, deterministic action, an independently measured baseline and a machine
# settlement.  Still no project work and no LLM.
DIRECT_ANSWER = Flow("direct_answer", "1.2.0", (
    Node("understand_1", "interaction.intent_observe"),
    Node("understand_2", "interaction.intent_counter_read", ("understand_1",)),
    Node("understand_3", "interaction.intent_synthesize", ("understand_2",)),
    Node("commitment", "commitment.bet_record", ("understand_3",)),
    Node("commitment_execute", "commitment.bet_execute", ("commitment",)),
    Node("answer", "interaction.direct_answer", ("understand_3",)),
    Node("compose", "presentation.message_compose", ("answer",)),
    Node("critic", "presentation.message_critic", ("compose",)),
    Node("deduplicate", "presentation.message_deduplicate", ("critic",)),
    Node("send", "delivery.send_text", ("deduplicate",)),
    Node("verify", "delivery.verify", ("send",)),
), "Answer plus a recorded and machine-settled commitment bet for the message.")


PROJECT_ITERATION = Flow("project_iteration", "2.5.0", (
    Node("understand_1", "interaction.intent_observe"),
    Node("understand_2", "interaction.intent_counter_read", ("understand_1",)),
    Node("understand_3", "interaction.intent_synthesize", ("understand_2",)),
    # The router picks between direct_answer and project_iteration by intent, so the
    # commitment nodes live in both: the kernel must be reachable whichever flow the
    # instance's own routing chooses.  Both nodes are bounded, deterministic and
    # isolated -- they perform no project work and call no LLM.
    Node("commitment", "commitment.bet_record", ("understand_3",)),
    Node("commitment_execute", "commitment.bet_execute", ("commitment",)),
    Node("recall", "memory.context_recall", ("understand_3",)),
    Node("pre_iteration_reflect", "evolution.pre_iteration_reflect", ("recall",), optional=True),
    Node("inspect", "project.state_inspect", ("pre_iteration_reflect", "recall")),
    Node("plan", "project.plan_propose", ("inspect",)),
    Node("execute", "project.action_execute", ("plan",), continue_on_failure=True),
    Node("verify", "project.outcome_verify", ("execute",)),
    Node("reflect", "project.outcome_reflect", ("verify",)),
    Node("reflect_to_evolve", "evolution.reflect_to_evolve", ("reflect",), optional=True),
    Node("remember", "memory.lesson_extract", ("reflect",), optional=True),
    Node("route", "selector.assess_next", ("remember",)),
    Node("continuation", "project.continuation_propose", ("route",), when_route="continue_project", optional=True),
    Node("notify", "presentation.notification_decide", ("continuation",)),
    Node("compose", "presentation.message_compose", ("notify",)),
    Node("message_critic", "presentation.message_critic", ("compose",)),
    Node("deduplicate", "presentation.message_deduplicate", ("message_critic",)),
    Node("channel", "delivery.channel_route", ("deduplicate",)),
    Node("send", "delivery.send_text", ("channel",)),
    Node("delivery_verify", "delivery.verify", ("send",)),
), "One falsifiable project step; learning/evolution are inserted only when evidence calls for them.")


ACTIVE_LEARNING = Flow("active_learning", "1.0.0", (
    Node("recall", "memory.context_recall"),
    Node("question", "active_learning.question_formulate", ("recall",)),
    Node("source_plan", "active_learning.source_plan", ("question",)),
    Node("retrieve", "active_learning.source_retrieve", ("source_plan",)),
    Node("read", "active_learning.source_read", ("retrieve",)),
    Node("crosscheck", "active_learning.claim_crosscheck", ("read",)),
    Node("synthesize", "active_learning.synthesize", ("crosscheck",)),
    Node("adoption", "active_learning.adoption_candidate", ("synthesize",)),
    Node("matched", "active_learning.matched_verify", ("adoption",)),
    Node("remember", "memory.belief_update", ("matched",)),
    Node("resume", "selector.assess_next", ("remember",)),
), "External knowledge acquisition with source and claim-level verification.")


SELF_EVOLUTION = Flow("self_evolution", "1.0.0", (
    Node("observe", "self_evolution.issue_observe"),
    Node("diagnose", "self_evolution.issue_diagnose", ("observe",)),
    Node("candidate", "self_evolution.candidate_propose", ("diagnose",)),
    Node("critic", "self_evolution.candidate_critic", ("candidate",)),
    Node("isolate", "self_evolution.candidate_isolate", ("critic",)),
    Node("baseline", "self_evolution.baseline_execute", ("isolate",)),
    Node("candidate_run", "self_evolution.candidate_execute", ("baseline",)),
    Node("compare", "self_evolution.matched_compare", ("candidate_run",)),
    Node("decision", "self_evolution.promotion_decide", ("compare",)),
    Node("habit", "memory.habit_propose", ("decision",), optional=True),
    Node("resume", "selector.assess_next", ("habit",)),
), "Partner-internal mechanism improvement with isolated matched evidence and rollback.")


MESSAGE_DELIVERY = Flow("message_delivery", "1.0.0", (
    Node("decide", "presentation.notification_decide"),
    Node("recall", "memory.context_recall", ("decide",)),
    Node("compose", "presentation.message_compose", ("recall",)),
    Node("critic", "presentation.message_critic", ("compose",)),
    Node("deduplicate", "presentation.message_deduplicate", ("critic",)),
    Node("route", "delivery.channel_route", ("deduplicate",)),
    Node("send", "delivery.send_text", ("route",)),
    Node("verify", "delivery.verify", ("send",)),
), "Natural, non-repetitive milestone messaging with channel acknowledgment.")


PDF_REPORT = Flow("pdf_report", "1.0.0", (
    Node("decide", "presentation.report_decide"),
    Node("sources", "presentation.report_sources_collect", ("decide",)),
    Node("outline", "presentation.report_outline", ("sources",)),
    Node("visual_plan", "presentation.visual_plan", ("outline",)),
    Node("visuals", "presentation.visual_generate", ("visual_plan",)),
    Node("draft", "presentation.report_draft", ("visuals",)),
    Node("claims", "presentation.claim_verify", ("draft",), optional=True),
    Node("render", "presentation.pdf_render", ("claims", "draft")),
    Node("quality", "presentation.pdf_quality_review", ("render",)),
    Node("send", "delivery.send_pdf", ("quality", "render")),
    Node("verify", "delivery.verify", ("send",)),
), "Domain-specific Chinese PDF with real project visuals and claim verification.")

PDF_REPORT_REVISION = Flow("pdf_report_revision", "1.0.0", (
    Node("sources", "presentation.report_sources_collect"),
    Node("draft", "presentation.report_draft", ("sources",)),
    Node("claims", "presentation.claim_verify", ("draft",)),
    Node("render", "presentation.pdf_render", ("claims", "draft")),
    Node("quality", "presentation.pdf_quality_review", ("render",)),
    Node("send", "delivery.send_pdf", ("quality", "render")),
    Node("verify", "delivery.verify", ("send",)),
), "Revise an existing report against explicit review feedback, then reverify and deliver.")

PDF_REPORT_REISSUE = Flow("pdf_report_reissue", "1.0.0", (
    Node("sources", "presentation.report_sources_collect"),
    Node("claims", "presentation.claim_verify", ("sources",)),
    Node("render", "presentation.pdf_render", ("claims",)),
    Node("quality", "presentation.pdf_quality_review", ("render",)),
    Node("send", "delivery.send_pdf", ("quality", "render")),
    Node("verify", "delivery.verify", ("send",)),
), "Recheck and render an existing report after a formatting repair, preserving prior files.")


ASPECT_ITERATION_REFLECTION = Flow("aspect_iteration_reflection", "1.0.0", (
    Node("observe", "evolution.aspect_observe", parameters={"aspect": "iteration"}),
    Node("counter_read", "evolution.aspect_counter_read", ("observe",), parameters={"aspect": "iteration"}),
    Node("synthesize", "evolution.aspect_synthesize", ("counter_read",), parameters={"aspect": "iteration"}),
    Node("emit", "evolution.aspect_emit", ("synthesize",), parameters={"aspect": "iteration"}),
), "Three-pass reflection over the iteration aspect (every round).")


ASPECT_INTENT_REFLECTION = Flow("aspect_intent_reflection", "1.0.0", (
    Node("observe", "evolution.aspect_observe", parameters={"aspect": "intent"}),
    Node("counter_read", "evolution.aspect_counter_read", ("observe",), parameters={"aspect": "intent"}),
    Node("synthesize", "evolution.aspect_synthesize", ("counter_read",), parameters={"aspect": "intent"}),
    Node("emit", "evolution.aspect_emit", ("synthesize",), parameters={"aspect": "intent"}),
), "Reflection over the intent-routing aspect.")


ASPECT_MESSAGE_REFLECTION = Flow("aspect_message_reflection", "1.0.0", (
    Node("observe", "evolution.aspect_observe", parameters={"aspect": "message"}),
    Node("counter_read", "evolution.aspect_counter_read", ("observe",), parameters={"aspect": "message"}),
    Node("synthesize", "evolution.aspect_synthesize", ("counter_read",), parameters={"aspect": "message"}),
    Node("emit", "evolution.aspect_emit", ("synthesize",), parameters={"aspect": "message"}),
), "Reflection over the user-message aspect.")


ASPECT_PDF_REPORT_REFLECTION = Flow("aspect_pdf_report_reflection", "1.0.0", (
    Node("observe", "evolution.aspect_observe", parameters={"aspect": "pdf_report"}),
    Node("counter_read", "evolution.aspect_counter_read", ("observe",), parameters={"aspect": "pdf_report"}),
    Node("synthesize", "evolution.aspect_synthesize", ("counter_read",), parameters={"aspect": "pdf_report"}),
    Node("emit", "evolution.aspect_emit", ("synthesize",), parameters={"aspect": "pdf_report"}),
), "Reflection over the PDF report aspect.")


ASPECT_EVENT_FLOW_REFLECTION = Flow("aspect_event_flow_reflection", "1.0.0", (
    Node("observe", "evolution.aspect_observe", parameters={"aspect": "event_flow"}),
    Node("counter_read", "evolution.aspect_counter_read", ("observe",), parameters={"aspect": "event_flow"}),
    Node("synthesize", "evolution.aspect_synthesize", ("counter_read",), parameters={"aspect": "event_flow"}),
    Node("emit", "evolution.aspect_emit", ("synthesize",), parameters={"aspect": "event_flow"}),
), "Reflection over the event-flow / shared-worker aspect.")


NEW_PROJECT = Flow("new_project", "1.1.0", (
    Node("understand_1", "interaction.intent_observe"),
    Node("understand_2", "interaction.intent_counter_read", ("understand_1",)),
    Node("understand_3", "interaction.intent_synthesize", ("understand_2",)),
    Node("init", "interaction.project_init", ("understand_3",)),
    Node("route", "selector.assess_next", ("init",)),
    Node("continuation", "project.continuation_propose", ("route",), when_route="continue_project", optional=True),
    Node("notify", "presentation.notification_decide", ("continuation",)),
    Node("compose", "presentation.message_compose", ("notify",)),
    Node("critic", "presentation.message_critic", ("compose",)),
    Node("deduplicate", "presentation.message_deduplicate", ("critic",)),
    Node("channel", "delivery.channel_route", ("deduplicate",)),
    Node("send", "delivery.send_text", ("channel",)),
    Node("verify", "delivery.verify", ("send",)),
), "Create project from understood intent, then assess remaining user work before notifying and continuing.")

XHS_AUTHORING = Flow("xhs_authoring", "1.1.0", (
    Node("account", "social.xhs_account_observe", continue_on_failure=True),
    Node("history", "social.xhs_history_read", ("account",), continue_on_failure=True),
    Node("style", "social.xhs_style_infer", ("history",), continue_on_failure=True),
    Node("draft", "social.xhs_draft_compose", ("style",), continue_on_failure=True),
    Node("media", "social.xhs_media_prepare", ("draft",), continue_on_failure=True),
    Node("verify", "social.xhs_draft_verify", ("media",), continue_on_failure=True),
    Node("publish", "social.xhs_publish", ("verify",), continue_on_failure=True),
    Node("receipt", "social.xhs_publish_verify", ("publish",), continue_on_failure=True),
    Node("notify", "presentation.notification_decide", ("receipt",)),
    Node("compose", "presentation.message_compose", ("notify",)),
    Node("message_critic", "presentation.message_critic", ("compose",)),
    Node("deduplicate", "presentation.message_deduplicate", ("message_critic",)),
    Node("channel", "delivery.channel_route", ("deduplicate",)),
    Node("send", "delivery.send_text", ("channel",)),
    Node("delivery_verify", "delivery.verify", ("send",)),
), "Decomposed replacement for the old xhs_authoring workflow.")


VIDEO_LEARNING = Flow("browser_video_learning", "1.1.0", (
    Node("open", "social.video_open", continue_on_failure=True),
    Node("capture", "social.video_capture" , ("open",), continue_on_failure=True),
    Node("transcribe", "social.video_transcribe", ("capture",), continue_on_failure=True),
    Node("vision", "social.video_frames_describe", ("capture",), continue_on_failure=True),
    Node("align", "social.video_timeline_align", ("transcribe", "vision"), continue_on_failure=True),
    Node("synthesize", "social.video_knowledge_synthesize", ("align",), continue_on_failure=True),
    Node("verify", "social.video_learning_verify", ("synthesize",), continue_on_failure=True),
    Node("notify", "presentation.notification_decide", ("verify",)),
    Node("compose", "presentation.message_compose", ("notify",)),
    Node("message_critic", "presentation.message_critic", ("compose",)),
    Node("deduplicate", "presentation.message_deduplicate", ("message_critic",)),
    Node("channel", "delivery.channel_route", ("deduplicate",)),
    Node("send", "delivery.send_text", ("channel",)),
    Node("delivery_verify", "delivery.verify", ("send",)),
), "Backend Edge video learning with aligned audio, frames and source evidence.")

ACCEPTANCE_MINIMAL = Flow("acceptance_minimal_chain", "1.0.0", (
    Node("accept_echo", "acceptance.echo"),
), "Acceptance-only minimal task chain. Used to verify JobRepository claim/release path.")


DEFINITIONS = [DIRECT_ANSWER, PROJECT_ITERATION, ACTIVE_LEARNING, SELF_EVOLUTION,
               MESSAGE_DELIVERY, PDF_REPORT, PDF_REPORT_REVISION, PDF_REPORT_REISSUE,
               NEW_PROJECT, XHS_AUTHORING, VIDEO_LEARNING]

# Snapshot existing graph definitions before installing the illustrated delivery
# graph. Pinned historical requests continue to resolve their original topology.
from dataclasses import replace
LEGACY_PRESENTATION_FLOWS = tuple(DEFINITIONS) + (
    DIRECT_ANSWER_V1, DIRECT_ANSWER_V1_1,
    # The project_iteration entry above is captured *after* the commitment nodes were
    # added, so register the true pre-integration topology as well -- a request pinned
    # to 2.5.0 must resolve to the graph that was actually shipped under that version.
    # It is listed last so it wins for the same (name, version) key.
    replace(next(_f for _f in DEFINITIONS if _f.name == "project_iteration"),
            nodes=tuple(_n for _n in next(_f for _f in DEFINITIONS
                                          if _f.name == "project_iteration").nodes
                        if _n.node_id not in {"commitment", "commitment_execute"})),
)
_updated=[]
for _flow in DEFINITIONS:
    if _flow.name.startswith('pdf_report'):
        _nodes=[Node('decide','presentation.report_decide'),
            Node('sources','presentation.report_sources_collect',('decide',)),
            Node('outline','presentation.report_outline',('sources',)),
            Node('visual_plan','presentation.visual_plan',('outline',)),
            Node('visuals','visualization.render',('visual_plan',)),
            Node('visual_verify','visualization.verify',('visuals',)),
            Node('draft','presentation.report_draft',('visual_verify',)),
            Node('claims','presentation.claim_verify',('draft',)),
            Node('render','presentation.pdf_render',('claims','draft')),
            Node('quality','presentation.pdf_quality_review',('render',)),
            Node('compose','presentation.message_compose',('quality',)),
            Node('message_critic','presentation.message_critic',('compose',)),
            Node('send','delivery.send_pdf',('quality','render','message_critic')),
            Node('verify','delivery.verify',('send',))]
        if _flow.name=='pdf_report_reissue':
            _nodes=[replace(n,depends_on=('sources',)) if n.node_id=='visual_plan' else replace(n,depends_on=('visual_verify',)) if n.node_id=='claims' else replace(n,depends_on=('claims',)) if n.node_id=='render' else n for n in _nodes if n.node_id not in {'draft','outline'}]
        _flow=replace(_flow,version='2.0.0',nodes=tuple(_nodes))
    else:
        _gate=next((n.node_id for n in _flow.nodes if n.event_type=='presentation.notification_decide'),'')
        if _gate:
            _after=False; _nodes=[]
            for _node in _flow.nodes:
                _nodes.append(replace(_node,when_output=_gate+'.notify') if _after else _node)
                if _node.node_id==_gate: _after=True
            _major,_minor,_patch=_flow.version.split('.')
            _flow=replace(_flow,version=f'{_major}.{int(_minor)+1}.0',nodes=tuple(_nodes))
    _updated.append(_flow)
DEFINITIONS=_updated
DEFINITIONS.append(ACCEPTANCE_MINIMAL)
