"""One user-authorized two-round cycle, with an obligatory post-delivery audit."""
from partner.event_fabric.flows import EventFlowDefinition as Flow, FlowNode as Node

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

ROUND = Flow('project_cycle_round', '3.0.0', (
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
), 'One dynamically numbered project iteration with an LLM decision and deterministic budget guard.')

CYCLE_V1 = Flow('project_cycle', '1.1.0', (
    Node('round_one', 'cycle.round_request', parameters={'round_number': 1}),
    Node('round_two', 'cycle.round_request', ('round_one',), parameters={'round_number': 2}),
    Node('assess', 'cycle.assess', ('round_two',)),
    Node('notify', 'presentation.notification_decide', ('assess',)),
    Node('compose', 'presentation.message_compose', ('notify',), continue_on_failure=True),
    Node('message_critic', 'presentation.message_critic', ('compose',), continue_on_failure=True),
    Node('deduplicate', 'presentation.message_deduplicate', ('message_critic',), continue_on_failure=True),
    Node('send', 'delivery.send_text', ('deduplicate',), continue_on_failure=True),
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
    Node('send', 'delivery.send_text', ('deduplicate',), continue_on_failure=True),
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
    Node('final_send', 'delivery.send_text', ('final_deduplicate',), continue_on_failure=True),
    Node('final_ack', 'cycle.delivery_settle', ('final_send',), continue_on_failure=True),
    Node('finish', 'cycle.finish', ('final_ack',)),
), 'Settlement-driven project rounds, conditional learning, then Partner-only post-run evolution audit.')

CYCLE = Flow('project_cycle', '3.0.0', (
    Node('initialize', 'cycle.initialize'),
    Node('iterate', 'cycle.iteration_controller', ('initialize',)),
    Node('assess', 'cycle.assess', ('iterate',)),
    Node('notify', 'presentation.notification_decide', ('assess',)),
    Node('compose', 'presentation.message_compose', ('notify',), continue_on_failure=True),
    Node('message_critic', 'presentation.message_critic', ('compose',), continue_on_failure=True),
    Node('deduplicate', 'presentation.message_deduplicate', ('message_critic',), continue_on_failure=True),
    Node('send', 'delivery.send_text', ('deduplicate',), continue_on_failure=True),
    Node('text_ack', 'cycle.delivery_settle', ('send',), continue_on_failure=True),
    Node('report', 'cycle.report_request', ('text_ack',), when_output='initialize.report_required'),
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
    Node('final_send', 'delivery.send_text', ('final_deduplicate',), continue_on_failure=True),
    Node('final_ack', 'cycle.delivery_settle', ('final_send',), continue_on_failure=True),
    Node('finish', 'cycle.finish', ('final_ack',)),
), 'A recoverable settlement-driven loop followed by delivery, memory, Partner audit and evolution.')

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


AUTONOMOUS_EVOLUTION = Flow('autonomous_evolution', '3.0.0', (
    Node('collect', 'autoevolution.collect'),
    Node('read_plan', 'autoevolution.read_plan', ('collect',)),
    Node('sources', 'autoevolution.sources', ('read_plan',)),
    Node('audit', 'autoevolution.audit', ('sources',)),
    Node('counter', 'autoevolution.counter', ('audit',)),
    Node('design', 'autoevolution.design', ('counter',)),
    Node('reconsider', 'autoevolution.read_plan', ('design',)),
    Node('sources_extra', 'autoevolution.sources', ('reconsider',)),
    Node('design_confirm', 'autoevolution.design', ('sources_extra',)),
    Node('tests', 'autoevolution.tests', ('design_confirm',)),
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
        ("finish", "improvement.finish", ("memory_consolidate",)),
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
            ('local_read','improvement.local_read',('recall',)),
            ('local_compare','improvement.local_compare',('local_read',)),
            ('local_ideas','improvement.local_idea_record',('local_compare',)),
            ('evidence_seal','improvement.evidence_seal',('local_ideas',)),
            *learning_nodes[2:])

    def build(nodes, name, version, description):
        flow_nodes = []
        for entry in nodes:
            nid, ev = entry[0], entry[1]
            deps = entry[2] if len(entry) == 3 else ()
            flow_nodes.append(FlowNode(nid, 'improvement.' + ev.split('.')[1], deps))
        return Flow(name, version, tuple(flow_nodes), description)

    self_flow = build(self_nodes, 'self_improvement_cycle', '1.1.0',
        'Internal-mechanism improvement driven by runtime observation; feeds shared autonomous_evolution child.')
    learning_flow = build(learning_nodes, 'learning_improvement_cycle', '1.3.0' if local_learning else '1.1.0',
        'External-learning driven improvement; feeds shared autonomous_evolution child.')
    return [self_flow, learning_flow]


_SELF_IMPROVEMENT_DEFS, _LEARNING_IMPROVEMENT_DEFS = None, None
def get_improvement_flow_definitions():
    global _SELF_IMPROVEMENT_DEFS, _LEARNING_IMPROVEMENT_DEFS
    if _SELF_IMPROVEMENT_DEFS is None:
        _SELF_IMPROVEMENT_DEFS, _LEARNING_IMPROVEMENT_DEFS = _improvement_flow_definitions()
    return _SELF_IMPROVEMENT_DEFS, _LEARNING_IMPROVEMENT_DEFS
def _resolved_definitions():
    return [ROUND, CYCLE, EVOLUTION_ATTEMPT, AUTONOMOUS_EVOLUTION, *get_improvement_flow_definitions()]

DEFINITIONS = _resolved_definitions()
DEFINITIONS_BY_VERSION = {'2.0.0': evolution_flow_v2()}
HISTORICAL = [ROUND_V1, ROUND_V2, CYCLE_V1, CYCLE_V2, evolution_flow(), evolution_flow(expanded=True), evolution_flow(expanded=True, repair_tests=True), evolution_flow_v2()]
