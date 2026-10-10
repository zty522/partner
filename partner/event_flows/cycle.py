"""One user-authorized two-round cycle, with an obligatory post-delivery audit."""
from partner.event_fabric.flows import EventFlowDefinition as Flow, FlowNode as Node
from dataclasses import replace

ROUND_V1 = Flow('project_cycle_round', '1.0.0', (
    Node('recall', 'memory.context_recall'),
    Node('inspect', 'project.state_inspect', ('recall',)),
    Node('plan', 'project.plan_propose', ('inspect',)),
    Node('execute', 'project.action_execute', ('plan',), continue_on_failure=True),
    Node('verify', 'project.outcome_verify', ('execute',), continue_on_failure=True),
    Node('reflect', 'project.outcome_reflect', ('verify',), continue_on_failure=True),
), 'A real project round; its terminal returns to the cycle, never starts an independent continuation.')


ROUND_V2 = Flow('project_cycle_round', '2.0.0', (
    Node('recall', 'memory.context_recall'),
    Node('inspect', 'project.state_inspect', ('recall',)),
    Node('plan', 'project.plan_propose', ('inspect',)),
    Node('core_state', 'core.state_build', ('plan',), parameters={'domain': 'project'}),
    Node('core_forecast', 'core.latent_forecast', ('core_state',)),
    Node('core_jev', 'core.jev_evaluate', ('core_state',)),
    Node('core_commit', 'core.commitment_freeze', ('core_forecast', 'core_jev')),
    Node('execute', 'project.action_execute', ('core_commit',), continue_on_failure=True),
    Node('verify', 'project.outcome_verify', ('execute',), continue_on_failure=True),
    Node('reflect', 'project.outcome_reflect', ('verify',), continue_on_failure=True),
    Node('core_settlement', 'core.settlement', ('reflect',), parameters={'evaluated_node': 'verify'}),
), 'One designed project round with Core commitment and settlement; no autonomous continuation.')

ROUND_V3_2 = Flow('project_cycle_round', '3.2.0', (
    Node('input_resolve', 'cycle.input_resolve'),
    Node('design', 'cycle.round_design', ('input_resolve',)),
    Node('design_critic', 'cycle.round_blueprint_critic', ('design',)),
    Node('recall', 'memory.context_recall', ('design_critic',)),
    Node('inspect', 'project.state_inspect', ('recall',)),
    Node('plan', 'project.plan_propose', ('inspect',)),
    Node('core_state', 'core.state_build', ('plan',), parameters={'domain': 'project'}),
    Node('core_forecast', 'core.latent_forecast', ('core_state',)),
    Node('core_jev', 'core.jev_evaluate', ('core_state',)),
    Node('core_commit', 'core.commitment_freeze', ('core_forecast', 'core_jev')),
    Node('execute', 'project.action_execute', ('core_commit',), continue_on_failure=True),
    Node('verify', 'project.outcome_verify', ('execute',), continue_on_failure=True),
    Node('reflect', 'project.outcome_reflect', ('verify',), continue_on_failure=True),
    Node('core_settlement', 'core.settlement', ('reflect',), parameters={'evaluated_node': 'verify'}),
    Node('learning_effect', 'cycle.iteration_learning_effect', ('core_settlement',)),
    Node('next_decide', 'cycle.iteration_next_decide', ('learning_effect',)),
    Node('budget_guard', 'cycle.iteration_budget_guard', ('next_decide',)),
), 'One materially planned project iteration: an independent critic freezes the executable Event subset before runtime execution.')

ROUND = Flow('project_cycle_round', '3.3.0', (
    Node('input_resolve', 'cycle.input_resolve'),
    Node('input_eligibility', 'cycle.input_eligibility', ('input_resolve',)),
    Node('design', 'cycle.round_design', ('input_eligibility',)),
    Node('design_critic', 'cycle.round_blueprint_critic', ('design',)),
    Node('recall', 'memory.context_recall', ('design_critic',)),
    Node('inspect', 'project.state_inspect', ('recall',)),
    Node('plan', 'project.plan_propose', ('inspect',)),
    Node('core_state', 'core.state_build', ('plan',), parameters={'domain': 'project'}),
    Node('core_forecast', 'core.latent_forecast', ('core_state',)),
    Node('core_jev', 'core.jev_evaluate', ('core_state',)),
    Node('core_commit', 'core.commitment_freeze', ('core_forecast', 'core_jev')),
    Node('execute', 'project.action_execute', ('core_commit',), continue_on_failure=True),
    Node('verify', 'project.outcome_verify', ('execute',), continue_on_failure=True),
    Node('reflect', 'project.outcome_reflect', ('verify',), continue_on_failure=True),
    Node('core_settlement', 'core.settlement', ('reflect',), parameters={'evaluated_node': 'verify'}),
    Node('learning_effect', 'cycle.iteration_learning_effect', ('core_settlement',)),
    Node('next_decide', 'cycle.iteration_next_decide', ('learning_effect',)),
    Node('problem_update', 'cycle.problem_portfolio_update', ('next_decide',)),
    Node('budget_guard', 'cycle.iteration_budget_guard', ('problem_update',)),
), 'One project iteration with corpus eligibility, a frozen Event blueprint and run-level problem-portfolio continuation.')
ROUND_V3_3 = ROUND
_round_34_nodes = []
for _node in ROUND.nodes:
    if _node.node_id == 'verify':
        _round_34_nodes.append(Node('input_consumption', 'project.input_consumption_verify',
                                    ('execute',), continue_on_failure=True))
        _node = replace(_node, depends_on=('input_consumption',))
    _round_34_nodes.append(_node)
ROUND = replace(ROUND, version='3.4.0', nodes=tuple(_round_34_nodes),
                description='A corpus-gated iteration that independently proves admitted-input consumption before outcome verification.')
ROUND_V3_4 = ROUND
_round_35_nodes = []
for _node in ROUND.nodes:
    if _node.node_id == 'design':
        _round_35_nodes.append(Node('input_adequacy', 'cycle.input_adequacy',
                                    ('input_eligibility',)))
        _node = replace(_node, depends_on=('input_adequacy',))
    _round_35_nodes.append(_node)
ROUND_V3_5 = ROUND  # 历史版本别名
_round_36_nodes = ROUND.nodes  # 节点序列不变，交接信息随 next_decide 输出传递
ROUND = replace(ROUND, version='3.6.0', nodes=tuple(_round_36_nodes),
                description='v3.6.0: iteration_next_decide 输出扩展 round_handoff 字段，实现内容级轮间传承；round_design 消费 next_decide 完整输出而非 budget_guard。')

# Immutable recovery definitions for Jobs pinned before executable blueprints.
ROUND_V3 = Flow('project_cycle_round', '3.0.0', (
    Node('design', 'cycle.round_design'),
    Node('recall', 'memory.context_recall', ('design',)),
    Node('inspect', 'project.state_inspect', ('recall',)),
    Node('plan', 'project.plan_propose', ('inspect',)),
    Node('core_state', 'core.state_build', ('plan',), parameters={'domain': 'project'}),
    Node('core_forecast', 'core.latent_forecast', ('core_state',)),
    Node('core_jev', 'core.jev_evaluate', ('core_state',)),
    Node('core_commit', 'core.commitment_freeze', ('core_forecast', 'core_jev')),
    Node('execute', 'project.action_execute', ('core_commit',), continue_on_failure=True),
    Node('verify', 'project.outcome_verify', ('execute',), continue_on_failure=True),
    Node('reflect', 'project.outcome_reflect', ('verify',), continue_on_failure=True),
    Node('core_settlement', 'core.settlement', ('reflect',), parameters={'evaluated_node': 'verify'}),
    Node('learning_effect', 'cycle.iteration_learning_effect', ('core_settlement',)),
    Node('next_decide', 'cycle.iteration_next_decide', ('learning_effect',)),
    Node('budget_guard', 'cycle.iteration_budget_guard', ('next_decide',)),
), 'Historical dynamic project iteration retained for pinned Jobs.')

CYCLE_V1 = Flow('project_cycle', '1.1.0', (
    Node('round_one', 'cycle.round_request', parameters={'round_number': 1}),
    Node('round_two', 'cycle.round_request', ('round_one',), parameters={'round_number': 2}),
    Node('assess', 'cycle.assess', ('round_two',)),
    Node('notify', 'presentation.notification_decide', ('assess',)),
    Node('compose', 'presentation.message_compose', ('notify',), continue_on_failure=True),
    Node('message_critic', 'presentation.message_critic', ('compose',), continue_on_failure=True),
    Node('deduplicate', 'presentation.message_deduplicate', ('message_critic',), continue_on_failure=True),
    Node('message_sanitize', 'message.sanitizer', ('deduplicate',), continue_on_failure=True),
    Node('send', 'delivery.send_text', ('message_sanitize',), continue_on_failure=True),
    Node('text_ack', 'cycle.delivery_settle', ('send',), continue_on_failure=True),
    Node('report', 'cycle.report_request', ('text_ack',)),
    Node('report_ack', 'cycle.delivery_settle', ('report',), continue_on_failure=True),
    Node('experience', 'cycle.memory_update', ('report_ack',), parameters={'kind': 'lesson'}),
    Node('growth', 'cycle.memory_update', ('experience',), parameters={'kind': 'growth'}),
    Node('habit', 'cycle.memory_update', ('growth',), parameters={'kind': 'habit'}),
    Node('seal', 'cycle.seal', ('habit',)),
    Node('evolve', 'cycle.evolution_request', ('seal',)),
    Node('final_summary', 'cycle.final_summary', ('evolve',), continue_on_failure=True),
    Node('finish', 'cycle.finish', ('final_summary',)),
), 'Two rounds, channel acknowledgments, report, memory, autonomous evidence-driven evolution, final summary, then stop.')


CYCLE_V2 = Flow('project_cycle', '2.0.0', (
    Node('initialize', 'cycle.initialize'),
    Node('design_one', 'cycle.round_design', ('initialize',), parameters={'round_number': 1}),
    Node('round_one', 'cycle.round_request', ('design_one',), parameters={'round_number': 1}),
    Node('settle_one', 'cycle.round_settle', ('round_one',), parameters={'round_number': 1}),
    Node('learning_one', 'cycle.learning_request', ('settle_one',), parameters={'round_number': 1}),
    Node('impact_one', 'cycle.learning_impact_settle', ('learning_one',), parameters={'round_number': 1}),

    Node('design_two', 'cycle.round_design', ('impact_one',), parameters={'round_number': 2},
         when_output='settle_one.continue_iteration'),
    Node('round_two', 'cycle.round_request', ('design_two',), parameters={'round_number': 2},
         when_output='settle_one.continue_iteration'),
    Node('settle_two', 'cycle.round_settle', ('round_two',), parameters={'round_number': 2},
         when_output='settle_one.continue_iteration'),
    Node('downstream_two', 'cycle.learning_downstream_settle', ('settle_two',),
         parameters={'learning_round': 1, 'evaluation_round': 2},
         when_output='settle_one.continue_iteration'),
    Node('learning_two', 'cycle.learning_request', ('downstream_two',), parameters={'round_number': 2},
         when_output='settle_one.continue_iteration'),
    Node('impact_two', 'cycle.learning_impact_settle', ('learning_two',), parameters={'round_number': 2},
         when_output='settle_one.continue_iteration'),

    Node('design_three', 'cycle.round_design', ('impact_two',), parameters={'round_number': 3},
         when_output='settle_two.continue_iteration'),
    Node('round_three', 'cycle.round_request', ('design_three',), parameters={'round_number': 3},
         when_output='settle_two.continue_iteration'),
    Node('settle_three', 'cycle.round_settle', ('round_three',), parameters={'round_number': 3},
         when_output='settle_two.continue_iteration'),
    Node('downstream_three', 'cycle.learning_downstream_settle', ('settle_three',),
         parameters={'learning_round': 2, 'evaluation_round': 3},
         when_output='settle_two.continue_iteration'),
    Node('learning_three', 'cycle.learning_request', ('downstream_three',), parameters={'round_number': 3},
         when_output='settle_two.continue_iteration'),
    Node('impact_three', 'cycle.learning_impact_settle', ('learning_three',), parameters={'round_number': 3},
         when_output='settle_two.continue_iteration'),

    Node('assess', 'cycle.assess', ('impact_one', 'downstream_two', 'impact_two',
                                    'downstream_three', 'impact_three')),
    Node('notify', 'presentation.notification_decide', ('assess',)),
    Node('compose', 'presentation.message_compose', ('notify',), continue_on_failure=True),
    Node('message_critic', 'presentation.message_critic', ('compose',), continue_on_failure=True),
    Node('deduplicate', 'presentation.message_deduplicate', ('message_critic',), continue_on_failure=True),
    Node('message_sanitize', 'message.sanitizer', ('deduplicate',), continue_on_failure=True),
    Node('send', 'delivery.send_text', ('message_sanitize',), continue_on_failure=True),
    Node('text_ack', 'cycle.delivery_settle', ('send',), continue_on_failure=True),
    Node('report', 'cycle.report_request', ('text_ack',),
         when_output='initialize.report_required'),
    Node('report_ack', 'cycle.delivery_settle', ('report',), continue_on_failure=True,
         when_output='initialize.report_required'),
    Node('experience', 'cycle.memory_update', ('report_ack',), parameters={'kind': 'lesson'}),
    Node('growth', 'cycle.memory_update', ('experience',), parameters={'kind': 'growth'}),
    Node('habit', 'cycle.memory_update', ('growth',), parameters={'kind': 'habit'}),
    Node('seal', 'cycle.seal', ('habit',)),
    Node('partner_audit', 'cycle.partner_audit', ('seal',)),
    Node('evolution_gate', 'cycle.evolution_gate', ('partner_audit',)),
    Node('evolve', 'cycle.evolution_request', ('evolution_gate',)),
    Node('final_summary', 'cycle.final_summary', ('evolve',), continue_on_failure=True),
    Node('final_notify', 'presentation.notification_decide', ('final_summary',)),
    Node('final_compose', 'presentation.message_compose', ('final_notify',), continue_on_failure=True),
    Node('final_critic', 'presentation.message_critic', ('final_compose',), continue_on_failure=True),
    Node('final_deduplicate', 'presentation.message_deduplicate', ('final_critic',), continue_on_failure=True),
    Node('final_message_sanitize', 'message.sanitizer', ('final_deduplicate',), continue_on_failure=True),
    Node('final_send', 'delivery.send_text', ('final_message_sanitize',), continue_on_failure=True),
    Node('final_ack', 'cycle.delivery_settle', ('final_send',), continue_on_failure=True),
    Node('token_aggregate', 'metrics.token_aggregate', ('final_ack',), continue_on_failure=True),
    Node('finish', 'cycle.finish', ('token_aggregate',)),
), 'Settlement-driven project rounds, conditional learning, then Partner-only post-run evolution audit.')

CYCLE = Flow('project_cycle', '3.2.0', (
    Node('initialize', 'cycle.initialize'),
    Node('iterate', 'cycle.iteration_controller', ('initialize',)),
    Node('benchmark', 'cycle.benchmark_request', ('iterate',)),
    Node('benchmark_settle', 'cycle.benchmark_settle', ('benchmark',), continue_on_failure=True),
    Node('assess', 'cycle.assess', ('benchmark_settle',)),
    Node('seal', 'cycle.seal', ('assess',)),
    Node('partner_audit', 'cycle.partner_audit', ('seal',)),
    Node('evolution_gate', 'cycle.evolution_gate', ('partner_audit',)),
    Node('evolve', 'cycle.evolution_request', ('evolution_gate',)),
    Node('final_state', 'cycle.final_state_freeze', ('evolve',)),
    Node('narrative', 'cycle.run_narrative', ('final_state',)),
    Node('notify', 'presentation.notification_decide', ('narrative',)),
    Node('compose', 'presentation.message_compose', ('notify',), continue_on_failure=True),
    Node('message_critic', 'presentation.message_critic', ('compose',), continue_on_failure=True),
    Node('deduplicate', 'presentation.message_deduplicate', ('message_critic',), continue_on_failure=True),
    Node('message_sanitize', 'message.sanitizer', ('deduplicate',), continue_on_failure=True),
    Node('send', 'delivery.send_text', ('message_sanitize',), continue_on_failure=True),
    Node('text_ack', 'cycle.delivery_settle', ('send',), continue_on_failure=True),
    Node('report', 'cycle.report_request', ('text_ack',), when_output='initialize.report_required'),
    Node('pdf_fact_check', 'pdf.fact_adjudicate', ('report',), continue_on_failure=True,
         when_output='initialize.report_required'),
    Node('pdf_format', 'pdf.format_five_section', ('pdf_fact_check',), continue_on_failure=True,
         when_output='initialize.report_required'),
    Node('report_ack', 'cycle.delivery_settle', ('pdf_format',), continue_on_failure=True,
         when_output='initialize.report_required'),
    Node('channel_verify', 'cycle.cross_channel_verify', ('report_ack',), continue_on_failure=True),
    Node('experience', 'cycle.memory_update', ('channel_verify',), parameters={'kind': 'lesson'}),
    Node('growth', 'cycle.memory_update', ('experience',), parameters={'kind': 'growth'}),
    Node('habit', 'cycle.memory_update', ('growth',), parameters={'kind': 'habit'}),
    Node('final_summary', 'cycle.final_summary', ('habit',), continue_on_failure=True),
    Node('final_notify', 'presentation.notification_decide', ('final_summary',)),
    Node('final_compose', 'presentation.message_compose', ('final_notify',), continue_on_failure=True),
    Node('final_critic', 'presentation.message_critic', ('final_compose',), continue_on_failure=True),
    Node('final_deduplicate', 'presentation.message_deduplicate', ('final_critic',), continue_on_failure=True),
    Node('final_message_sanitize', 'message.sanitizer', ('final_deduplicate',), continue_on_failure=True),
    Node('final_send', 'delivery.send_text', ('final_message_sanitize',), continue_on_failure=True),
    Node('final_ack', 'cycle.delivery_settle', ('final_send',), continue_on_failure=True),
    Node('token_aggregate', 'metrics.token_aggregate', ('final_ack',), continue_on_failure=True),
    Node('finish', 'cycle.finish', ('token_aggregate',)),
), 'A recoverable settlement-driven loop followed by delivery, memory, Partner audit and evolution.')

# Run a requested deterministic benchmark before open-ended research rounds.
# In 3.2 it followed ``iterate``; a deadline run could therefore spend the
# whole work window on research and start its required baseline/candidate only
# after the wall clock had expired.  Preserve 3.2 for pinned Jobs and make the
# new ordering explicit in the versioned graph.
CYCLE_V3_2 = CYCLE
_cycle_33_nodes = []
for _node in CYCLE.nodes:
    if _node.node_id == 'benchmark':
        _node = replace(_node, depends_on=('initialize',))
    elif _node.node_id == 'benchmark_settle':
        _node = replace(_node, depends_on=('benchmark',))
    elif _node.node_id == 'iterate':
        _node = replace(_node, depends_on=('benchmark_settle',))
    elif _node.node_id == 'assess':
        _node = replace(_node, depends_on=('iterate',))
    _cycle_33_nodes.append(_node)
CYCLE = replace(CYCLE, version='3.3.0', nodes=tuple(_cycle_33_nodes),
                description='Required benchmark first, then a deadline-aware research and finalization loop.')
CYCLE_V3_3 = CYCLE
_cycle_34_nodes = []
for _node in CYCLE.nodes:
    if _node.node_id == 'benchmark':
        _node = replace(_node, depends_on=('research_preflight',))
    elif _node.node_id == 'channel_verify':
        _node = replace(_node, depends_on=('semantic_claim_audit',))
    _cycle_34_nodes.append(_node)
_cycle_34_nodes.insert(1, Node('research_preflight', 'cycle.research_preflight', ('initialize',)))
# 添加语料资格冻结节点
_cycle_34_nodes.insert(2, Node('corpus_eligibility', 'corpus.eligibility_freeze', ('research_preflight',)))
# 修改 benchmark 依赖
for i, node in enumerate(_cycle_34_nodes):
    if node.node_id == 'benchmark':
        _cycle_34_nodes[i] = replace(node, depends_on=('corpus_eligibility',))
        break
report_ack_index = next(i for i, node in enumerate(_cycle_34_nodes) if node.node_id == 'report_ack')
_cycle_34_nodes.insert(report_ack_index + 1,
                       Node('semantic_claim_audit', 'cycle.semantic_claim_audit', ('report_ack',),
                            continue_on_failure=True))
CYCLE = replace(CYCLE, version='3.4.0', nodes=tuple(_cycle_34_nodes),
                description='Run-level research contract, corpus-gated rounds, problem portfolio and semantic delivery audit.')
CYCLE_V3_4 = CYCLE
# Claims must be audited and, when necessary, repaired before either QQ/Web
# text or the PDF child is allowed to render and send them.  The previous 3.4
# graph audited only after both channels had already delivered the content.
_cycle_35_nodes = []
for _node in CYCLE.nodes:
    if _node.node_id == 'semantic_claim_audit':
        continue
    if _node.node_id == 'notify':
        _node = replace(_node, depends_on=('semantic_claim_reaudit',))
    elif _node.node_id == 'channel_verify':
        _node = replace(_node, depends_on=('report_ack',))
    _cycle_35_nodes.append(_node)
_narrative_index = next(i for i, node in enumerate(_cycle_35_nodes)
                        if node.node_id == 'narrative')
_cycle_35_nodes[_narrative_index + 1:_narrative_index + 1] = [
    Node('semantic_claim_audit', 'cycle.semantic_claim_audit', ('narrative',),
         continue_on_failure=True),
    Node('semantic_claim_repair', 'cycle.semantic_claim_repair', ('semantic_claim_audit',)),
    Node('semantic_claim_reaudit', 'cycle.semantic_claim_audit', ('semantic_claim_repair',)),
]
CYCLE = replace(CYCLE, version='3.5.0', nodes=tuple(_cycle_35_nodes),
                description='Audit and repair outward claims before delivery; then verify all rendered channels.')

CYCLE_V3 = Flow('project_cycle', '3.0.0', (
    Node('initialize', 'cycle.initialize'),
    Node('iterate', 'cycle.iteration_controller', ('initialize',)),
    Node('assess', 'cycle.assess', ('iterate',)),
    Node('notify', 'presentation.notification_decide', ('assess',)),
    Node('compose', 'presentation.message_compose', ('notify',), continue_on_failure=True),
    Node('message_critic', 'presentation.message_critic', ('compose',), continue_on_failure=True),
    Node('deduplicate', 'presentation.message_deduplicate', ('message_critic',), continue_on_failure=True),
    Node('message_sanitize', 'message.sanitizer', ('deduplicate',), continue_on_failure=True),
    Node('send', 'delivery.send_text', ('message_sanitize',), continue_on_failure=True),
    Node('text_ack', 'cycle.delivery_settle', ('send',), continue_on_failure=True),
    Node('report', 'cycle.report_request', ('text_ack',), when_output='initialize.report_required'),
    Node('pdf_fact_check', 'pdf.fact_adjudicate', ('report',), continue_on_failure=True,
         when_output='initialize.report_required'),
    Node('pdf_format', 'pdf.format_five_section', ('pdf_fact_check',), continue_on_failure=True,
         when_output='initialize.report_required'),
    Node('report_ack', 'cycle.delivery_settle', ('pdf_format',), continue_on_failure=True,
         when_output='initialize.report_required'),
    Node('experience', 'cycle.memory_update', ('report_ack',), parameters={'kind': 'lesson'}),
    Node('growth', 'cycle.memory_update', ('experience',), parameters={'kind': 'growth'}),
    Node('habit', 'cycle.memory_update', ('growth',), parameters={'kind': 'habit'}),
    Node('seal', 'cycle.seal', ('habit',)),
    Node('partner_audit', 'cycle.partner_audit', ('seal',)),
    Node('evolution_gate', 'cycle.evolution_gate', ('partner_audit',)),
    Node('evolve', 'cycle.evolution_request', ('evolution_gate',)),
    Node('final_summary', 'cycle.final_summary', ('evolve',), continue_on_failure=True),
    Node('final_notify', 'presentation.notification_decide', ('final_summary',)),
    Node('final_compose', 'presentation.message_compose', ('final_notify',), continue_on_failure=True),
    Node('final_critic', 'presentation.message_critic', ('final_compose',), continue_on_failure=True),
    Node('final_deduplicate', 'presentation.message_deduplicate', ('final_critic',), continue_on_failure=True),
    Node('final_message_sanitize', 'message.sanitizer', ('final_deduplicate',), continue_on_failure=True),
    Node('final_send', 'delivery.send_text', ('final_message_sanitize',), continue_on_failure=True),
    Node('final_ack', 'cycle.delivery_settle', ('final_send',), continue_on_failure=True),
    Node('token_aggregate', 'metrics.token_aggregate', ('final_ack',), continue_on_failure=True),
    Node('finish', 'cycle.finish', ('token_aggregate',)),
), 'Historical v3 cycle retained for pinned Jobs.')

# Public semantic names.  ``project_cycle`` remains versioned for recovery of
# already-pinned Jobs; new intake uses an explicit top-level workstream.
PROJECT_RESEARCH_CYCLE_V1_1 = replace(
    CYCLE_V3_2, name='project_research_cycle', version='1.1.0',
    description='Historical project research graph with benchmark after iteration.')
PROJECT_RESEARCH_CYCLE = replace(
    CYCLE, name='project_research_cycle', version='1.5.0',
    description='Project research with message sanitization, PDF fact adjudication, and token aggregation.')
META_CYCLE = Flow('meta_cycle', '2.0.0', (
    Node('meta_initialize', 'meta.initialize'),
    Node('coordinate', 'meta.coordinate', ('meta_initialize',)),
    Node('finalize', 'meta.finalize', ('coordinate',)),
    Node('notify', 'presentation.notification_decide', ('finalize',)),
    Node('compose', 'presentation.message_compose', ('notify',), continue_on_failure=True),
    Node('critic', 'presentation.message_critic', ('compose',), continue_on_failure=True),
    Node('deduplicate', 'presentation.message_deduplicate', ('critic',), continue_on_failure=True),
    Node('message_sanitize', 'message.sanitizer', ('deduplicate',), continue_on_failure=True),
    Node('send', 'delivery.send_text', ('message_sanitize',), continue_on_failure=True),
    Node('text_ack', 'cycle.delivery_settle', ('send',), continue_on_failure=True),
    Node('report', 'cycle.report_request', ('text_ack',), when_output='meta_initialize.report_required'),
    Node('report_ack', 'cycle.delivery_settle', ('report',), continue_on_failure=True,
         when_output='meta_initialize.report_required'),
    Node('finish', 'meta.finish', ('report_ack',)),
), 'Explicit coordinator that runs project, learning and evolution as separate child workstreams and merges terminal evidence.')

def evolution_flow(expanded=False, repair_tests=False, preflight_revision=False):
    nodes = []
    def add(node, event, **kw):
        nodes.append(Node(node, 'autoevolution.' + event,
                          (nodes[-1].node_id,) if nodes else (), **kw))
    add('collect', 'collect')
    add('read_plan', 'read_plan')
    add('sources', 'sources')
    add('audit', 'audit')
    add('counter', 'counter')
    add('design', 'design')
    if expanded:
        add('reconsider', 'read_plan')
        add('sources_extra', 'sources')
        add('design_confirm', 'design')
    add('tests', 'tests')
    add('test_review', 'test_review')
    if repair_tests:
        add('test_repair', 'tests')
        add('test_confirm', 'test_review')
    if preflight_revision:
        add('test_repair_2', 'tests')
        add('test_confirm_2', 'test_review')
    add('freeze', 'freeze')
    for attempt in (1, 2):
        for key in ('candidate', 'critic', 'isolate', 'baseline', 'candidate_run', 'compare', 'analyze'):
            add(f'{key}_{attempt}', key, parameters={'attempt': attempt})
    add('decision', 'decision')
    add('release', 'release')
    add('record', 'record')
    return Flow('autonomous_evolution', '1.3.0' if preflight_revision else '1.2.0' if repair_tests else '1.1.0' if expanded else '1.0.0', tuple(nodes),
                'Full-cycle audit with frozen expectations/tests, two bounded candidate revisions and guarded activation.')



def evolution_flow_v2():
    nodes = []
    def add(node, event, **kw):
        nodes.append(Node(node, 'autoevolution.' + event,
                          (nodes[-1].node_id,) if nodes else (), **kw))
    # Investigation
    add('collect', 'collect')
    add('read_plan', 'read_plan')
    add('sources', 'sources')
    add('audit', 'audit')
    # Issue selection + design
    add('counter', 'counter')
    add('design', 'design')
    add('reconsider', 'read_plan')
    add('sources_extra', 'sources')
    add('design_confirm', 'design')
    # Tests + freeze (with structured preflight + review)
    add('tests', 'tests')
    add('tests_preflight', 'tests_preflight')
    add('tests_review', 'tests_review')
    add('test_repair_v2', 'test_repair_v2')
    add('tests_review_2', 'tests_review')
    add('freeze', 'freeze')
    # Two attempts
    for attempt in (1, 2):
        for key in ('candidate', 'critic', 'isolate', 'baseline', 'candidate_run', 'compare', 'analyze'):
            add(f'{key}_{attempt}', key, parameters={'attempt': attempt})
    # Decision
    add('decision', 'decision')
    # Release gate (structured baseline+candidate+compare)
    add('release_baseline', 'release_baseline')
    add('release_candidate', 'release_candidate')
    add('release_compare', 'release_compare')
    # Failure feedback
    add('failure_analyze', 'failure_analyze')
    # Apply + reload + verify
    add('apply_source', 'apply_source')
    add('runtime_reload', 'runtime_reload')
    add('runtime_verify', 'runtime_verify')
    # Rollback (only if runtime_verify fails)
    add('rollback', 'rollback')
    add('rollback_verify', 'rollback_verify')
    # Final record
    add('record', 'record')
    return Flow('autonomous_evolution', '2.0.0', tuple(nodes),
                'v2 self-evolution: structured baseline+candidate compare, apply/reload/verify, rollback on verify failure.')


EVOLUTION_ATTEMPT = Flow('autonomous_evolution_attempt', '1.0.0', (
    Node('candidate', 'autoevolution.candidate'),
    Node('critic', 'autoevolution.critic', ('candidate',)),
    Node('isolate', 'autoevolution.isolate', ('critic',)),
    Node('baseline', 'autoevolution.baseline', ('isolate',)),
    Node('candidate_run', 'autoevolution.candidate_run', ('baseline',)),
    Node('compare', 'autoevolution.compare', ('candidate_run',)),
    Node('analyze', 'autoevolution.analyze', ('compare',)),
    Node('next_decide', 'autoevolution.next_decide', ('analyze',)),
    Node('budget_guard', 'autoevolution.attempt_budget_guard', ('next_decide',)),
), 'One isolated self-evolution candidate with matched evidence and a guarded next decision.')


AUTONOMOUS_EVOLUTION_V31 = Flow('autonomous_evolution', '3.1.0', (
    Node('collect', 'autoevolution.collect'),
    Node('read_plan', 'autoevolution.read_plan', ('collect',)),
    Node('sources', 'autoevolution.sources', ('read_plan',)),
    Node('audit', 'autoevolution.audit', ('sources',)),
    Node('counter', 'autoevolution.counter', ('audit',)),
    Node('design', 'autoevolution.design', ('counter',)),
    Node('reconsider', 'autoevolution.read_plan', ('design',)),
    Node('sources_extra', 'autoevolution.sources', ('reconsider',)),
    Node('design_confirm', 'autoevolution.design', ('sources_extra',)),
    Node('target_consistency', 'autoevolution.target_consistency', ('design_confirm',)),
    Node('tests', 'autoevolution.tests', ('target_consistency',)),
    Node('tests_preflight', 'autoevolution.tests_preflight', ('tests',)),
    Node('tests_review', 'autoevolution.tests_review', ('tests_preflight',)),
    Node('test_repair_v2', 'autoevolution.test_repair_v2', ('tests_review',)),
    Node('tests_review_2', 'autoevolution.tests_review', ('test_repair_v2',)),
    Node('freeze', 'autoevolution.freeze', ('tests_review_2',)),
    Node('attempt_loop', 'autoevolution.attempt_controller', ('freeze',)),
    Node('decision', 'autoevolution.decision', ('attempt_loop',)),
    Node('release_baseline', 'autoevolution.release_baseline', ('decision',)),
    Node('release_candidate', 'autoevolution.release_candidate', ('release_baseline',)),
    Node('release_compare', 'autoevolution.release_compare', ('release_candidate',)),
    Node('failure_analyze', 'autoevolution.failure_analyze', ('release_compare',)),
    Node('apply_source', 'autoevolution.apply_source', ('failure_analyze',)),
    Node('runtime_reload', 'autoevolution.runtime_reload', ('apply_source',)),
    Node('runtime_verify', 'autoevolution.runtime_verify', ('runtime_reload',)),
    Node('rollback', 'autoevolution.rollback', ('runtime_verify',)),
    Node('rollback_verify', 'autoevolution.rollback_verify', ('rollback',)),
    Node('record', 'autoevolution.record', ('rollback_verify',)),
), 'Dynamic self-evolution attempts; each attempt is isolated and the LLM may revise until a guard stops it.')

SUPERVISION_CYCLE = Flow('supervision_cycle', '1.0.0', (
    Node('expectations_load', 'supervise.expectations_load'),
    Node('snapshot', 'supervise.snapshot', ('expectations_load',)),
    Node('synthesize', 'supervise.synthesize', ('snapshot',)),
    Node('expectations_update', 'supervise.expectations_update', ('synthesize',)),
    Node('objective_plan', 'supervise.objective_plan', ('expectations_update',)),
), 'Shared supervision brick: load expectations, full-text LLM snapshot, gap synthesis, expectation doc update, objective plan. Invoked by the LLM-assembled route (supervise.route_dispatch).')

AUTONOMOUS_EVOLUTION_V33 = Flow('autonomous_evolution', '3.3.0', (
    Node('regression_trigger', 'autoevolution.trigger_regression'),
    Node('regression_track', 'autoevolution.track_regression', ('regression_trigger',)),
    Node('collect', 'autoevolution.collect', ('regression_track',)),
    # mechanism audit chain (investigates framework/mechanism behaviour)
    Node('read_plan', 'autoevolution.read_plan', ('collect',)),
    Node('sources', 'autoevolution.sources', ('read_plan',)),
    Node('audit', 'autoevolution.audit', ('sources',)),
    Node('counter', 'autoevolution.counter', ('audit',)),
    # supervision chain (full-text round-by-round judgment against expectations)
    Node('supervise_synthesize', 'supervise.synthesize', ('collect',)),
    Node('supervise_expectations_update', 'supervise.expectations_update', ('supervise_synthesize',)),
    Node('route_plan', 'supervise.route_plan', ('supervise_expectations_update',)),
    Node('route_dispatch', 'supervise.route_dispatch', ('route_plan',)),
    Node('issue_select', 'supervise.issue_select', ('route_dispatch',)),
    # design merges both: mechanism issue (counter) + supervision gap (issue_select)
    Node('design', 'autoevolution.design', ('counter', 'issue_select')),
    Node('reconsider', 'autoevolution.read_plan', ('design',)),
    Node('sources_extra', 'autoevolution.sources', ('reconsider',)),
    Node('design_confirm', 'autoevolution.design', ('sources_extra',)),
    Node('target_consistency', 'autoevolution.target_consistency', ('design_confirm',)),
    Node('tests', 'autoevolution.tests', ('target_consistency',)),
    Node('tests_preflight', 'autoevolution.tests_preflight', ('tests',)),
    Node('tests_review', 'autoevolution.tests_review', ('tests_preflight',)),
    Node('test_repair_v2', 'autoevolution.test_repair_v2', ('tests_review',)),
    Node('tests_review_2', 'autoevolution.tests_review', ('test_repair_v2',)),
    Node('freeze', 'autoevolution.freeze', ('tests_review_2',)),
    Node('attempt_loop', 'autoevolution.attempt_controller', ('freeze',)),
    Node('decision', 'autoevolution.decision', ('attempt_loop',)),
    Node('notes_sync', 'notes.evolution_sync', ('decision',),
         parameters={'decision_node': 'decision'}),
    Node('release_baseline', 'autoevolution.release_baseline', ('decision',)),
    Node('release_candidate', 'autoevolution.release_candidate', ('release_baseline',)),
    Node('release_compare', 'autoevolution.release_compare', ('release_candidate',)),
    Node('failure_analyze', 'autoevolution.failure_analyze', ('release_compare',)),
    Node('apply_source', 'autoevolution.apply_source', ('failure_analyze',)),
    Node('runtime_reload', 'autoevolution.runtime_reload', ('apply_source',)),
    Node('benchmark_gate', 'autoevolution.benchmark_gate', ('runtime_reload',)),
    Node('runtime_verify', 'autoevolution.runtime_verify', ('benchmark_gate',)),
    Node('rollback', 'autoevolution.rollback', ('runtime_verify',)),
    Node('rollback_verify', 'autoevolution.rollback_verify', ('rollback',)),
    Node('promote', 'notes.promote', ('rollback_verify', 'notes_sync'), optional=True,
         parameters={'evidence_sources': ['decision', 'release_compare', 'notes_sync']}),
    Node('record', 'autoevolution.record', ('promote', 'rollback_verify')),
), 'Dynamic self-evolution with live supervision: regression probe runs while its finished rounds are supervised round-by-round against the dynamic expectation doc; the LLM assembles the next route (supervision child flow / inline fix / stop) and the deterministic benchmark gate guards every source apply. Mechanism audit and supervision gap chains run in parallel and merge at design. Evolution decisions sync to the note ledger and open notes are promoted against this round evidence.')

AUTONOMOUS_EVOLUTION = replace(
    AUTONOMOUS_EVOLUTION_V33,
    version='3.4.0',
    nodes=tuple(list(AUTONOMOUS_EVOLUTION_V33.nodes) +
                [Node('engine_continue', 'autoevolution.engine_continue', ('record',))]),
    description=('Engine loop: after record, the engine gate decides whether to auto-start the next '
                 'round (budget/convergence/user-control guarded), submits it through the same '
                 'orchestrator entry with a fresh request id and the same engine id, and records the '
                 'loop state under state/evolution_regression/engine/<engine_id>/engine_state.json. '
                 'Derived from v3.3.0 (live-supervision closed loop); v3.3.0 retained for pinned Jobs.'))

AUTONOMOUS_EVOLUTION_V32 = replace(
    AUTONOMOUS_EVOLUTION_V31,
    nodes=tuple(
        [Node('regression_trigger', 'autoevolution.trigger_regression'),
         Node('regression_track', 'autoevolution.track_regression', ('regression_trigger',))]
        + list(AUTONOMOUS_EVOLUTION_V31.nodes)
        + [Node('benchmark_gate', 'autoevolution.benchmark_gate', ('runtime_reload',)),
           replace(AUTONOMOUS_EVOLUTION_V31.node('runtime_verify'), depends_on=('benchmark_gate',))]),
    version='3.2.0',
    description='Historical v3.2 system-level self-evolution retained for pinned Jobs.')

AUTONOMOUS_EVOLUTION_V3 = replace(
    AUTONOMOUS_EVOLUTION_V31, version='3.0.0',
    nodes=tuple(
        replace(node, depends_on=('design_confirm',)) if node.node_id == 'tests' else node
        for node in AUTONOMOUS_EVOLUTION_V31.nodes if node.node_id != 'target_consistency'),
    description='Historical v3 autonomous evolution retained for pinned Jobs.')




# (2026-09-16) Improvement flows - 01/02 unified via shared autonomous_evolution child.

def _improvement_flow_definitions(local_learning=True):
    from partner.event_fabric.flows import EventFlowDefinition as Flow, FlowNode

    # 01 self_improvement: recall is the root, observe steps follow it,
    # then evidence_seal depends on observe_execute (not recall).  This
    # keeps the graph acyclic: no node may depend on a node that is
    # transitively downstream of it.
    self_nodes = (
        ("recall", "improvement.recall"),
        ("observe_plan", "improvement.observe_plan", ("recall",)),
        ("observe_execute", "improvement.observe_execute", ("observe_plan",)),
        ("evidence_seal", "improvement.evidence_seal", ("observe_execute",)),
        ("opportunity_assess", "improvement.opportunity_assess", ("evidence_seal",)),
        ("opportunity_select", "improvement.opportunity_select", ("opportunity_assess",)),
        ("experiment_request", "improvement.experiment_request", ("opportunity_select",)),
        ("outcome_settle", "improvement.outcome_settle", ("experiment_request",)),
        ("memory_consolidate", "improvement.memory_consolidate", ("outcome_settle",)),
        ("iterate_more", "improvement.iteration_controller", ("memory_consolidate",)),
        ("narrative", "improvement.narrative", ("iterate_more",)),
        ("send_message", "delivery.send_text", ("narrative",)),
        ("improvement_message_ack", "cycle.delivery_settle", ("send_message",)),
        ("render", "improvement.report", ("improvement_message_ack",)),
        ("send_report", "delivery.send_pdf", ("render",)),
        ("improvement_report_ack", "cycle.delivery_settle", ("send_report",)),
        ("promote", "notes.promote", ("improvement_report_ack",),
         {"evidence_sources": ["outcome_settle", "memory_consolidate"]}),
        ("finish", "improvement.finish", ("promote",)),
    )
    # 02 learning_improvement: no observe steps; recall is the root.
    learning_nodes = (
        ("recall", "improvement.recall"),
        ("evidence_seal", "improvement.evidence_seal", ("recall",)),
        ("opportunity_assess", "improvement.opportunity_assess", ("evidence_seal",)),
        ("opportunity_select", "improvement.opportunity_select", ("opportunity_assess",)),
        ("experiment_request", "improvement.experiment_request", ("opportunity_select",)),
        ("outcome_settle", "improvement.outcome_settle", ("experiment_request",)),
        ("memory_consolidate", "improvement.memory_consolidate", ("outcome_settle",)),
        ("finish", "improvement.finish", ("memory_consolidate",)),
    )

    if local_learning:
        learning_nodes = (learning_nodes[0],
            ('notes_recall','notes.recall'),
            ('source_retrieve','active_learning.source_retrieve',('recall',)),
            ('local_read','improvement.local_read',('source_retrieve',)),
            ('local_compare','improvement.local_compare',('local_read',)),
            ('local_ideas','improvement.local_idea_record',('local_compare',)),
            ('judge','notes.judge',('local_ideas',)),
            ('learning_commitment','improvement.learning_commitment',('judge',)),
            ('learning_evaluate','improvement.learning_downstream_evaluate',('learning_commitment',)),
            ('learning_settlement','improvement.learning_settlement',('learning_evaluate',)),
            ('evidence_seal','improvement.evidence_seal',('learning_settlement',)),
            *learning_nodes[2:-1],
            ('iterate_more','improvement.iteration_controller',('memory_consolidate',)),
            ('narrative','improvement.narrative',('iterate_more',)),
            ('send_message','delivery.send_text',('narrative',)),
            ('improvement_message_ack','cycle.delivery_settle',('send_message',)),
            ('render','improvement.report',('improvement_message_ack',)),
            ('send_report','delivery.send_pdf',('render',)),
            ('improvement_report_ack','cycle.delivery_settle',('send_report',)),
            ('promote','notes.promote',('improvement_report_ack',),
             {'evidence_sources': ['learning_settlement','learning_evaluate','judge']}),
            ('finish','improvement.finish',('promote',)))

    def build(nodes, name, version, description, soft=False):
        flow_nodes = []
        for entry in nodes:
            nid, ev = entry[0], entry[1]
            deps = entry[2] if len(entry) >= 3 else ()
            node = FlowNode(nid, ev, deps)
            if len(entry) >= 4 and entry[3]:
                from dataclasses import replace as _dc_replace
                node = _dc_replace(node, parameters=dict(entry[3]))
            flow_nodes.append(node)
        return Flow(name, version, tuple(flow_nodes), description, soft)

    self_flow = build(self_nodes, 'self_improvement_cycle', '1.4.0' if local_learning else '1.1.0',
        'Internal-mechanism improvement driven by runtime observation; feeds shared autonomous_evolution child.')
    learning_flow = build(learning_nodes, 'learning_improvement_cycle', '1.8.0' if local_learning else '1.2.0',
        'External-learning driven improvement; reads user-supplied sources when given; '
        'feeds shared autonomous_evolution child. Soft-orchestrated baseline.',
        soft=True)
    def through_memory(nodes):
        rows = []
        for entry in nodes:
            if entry[0] in {'iterate_more', 'narrative', 'send_message',
                             'improvement_message_ack', 'render', 'send_report',
                             'improvement_report_ack', 'promote', 'finish'}:
                continue
            rows.append(entry)
        rows.append(('finish', 'improvement.finish', ('memory_consolidate',)))
        return tuple(rows)
    # v1.0.0 is the historical single-pass graph.  Giving the current child
    # the same version let the historical registry entry silently replace its
    # active-learning nodes for pinned child execution.
    self_round = build(through_memory(self_nodes), 'self_improvement_round',
        '1.1.0' if local_learning else '1.0.0',
        'One evidence and matched-experiment self-improvement round without user delivery.')
    learning_round = build(through_memory(learning_nodes), 'learning_improvement_round',
        '1.1.0' if local_learning else '1.0.0',
        'One novel-source active-learning round without user delivery.')
    return [self_flow, learning_flow, self_round, learning_round]


_IMPROVEMENT_DEFS = None
def get_improvement_flow_definitions():
    global _IMPROVEMENT_DEFS
    if _IMPROVEMENT_DEFS is None:
        _IMPROVEMENT_DEFS = _improvement_flow_definitions()
    return list(_IMPROVEMENT_DEFS)
def _resolved_definitions():
    return [ROUND, CYCLE, PROJECT_RESEARCH_CYCLE, META_CYCLE,
            EVOLUTION_ATTEMPT, AUTONOMOUS_EVOLUTION, SUPERVISION_CYCLE,
            *get_improvement_flow_definitions()]

DEFINITIONS = _resolved_definitions()
DEFINITIONS_BY_VERSION = {'2.0.0': evolution_flow_v2()}
HISTORICAL = [ROUND_V1, ROUND_V2, ROUND_V3, ROUND_V3_2, ROUND_V3_3, ROUND_V3_4,
              CYCLE_V1, CYCLE_V2, CYCLE_V3, CYCLE_V3_2, CYCLE_V3_3, CYCLE_V3_4,
              replace(CYCLE_V3, name='project_research_cycle', version='1.0.0'),
              PROJECT_RESEARCH_CYCLE_V1_1,
              replace(CYCLE_V3_4, name='project_research_cycle', version='1.3.0'),
              replace(CYCLE_V3, name='meta_cycle', version='1.0.0'),
              replace(CYCLE, name='meta_cycle', version='1.1.0'),
              AUTONOMOUS_EVOLUTION_V3,
              AUTONOMOUS_EVOLUTION_V31,
              AUTONOMOUS_EVOLUTION_V32,
              AUTONOMOUS_EVOLUTION_V33,
              evolution_flow(), evolution_flow(expanded=True),
              evolution_flow(expanded=True, repair_tests=True), evolution_flow_v2()]
