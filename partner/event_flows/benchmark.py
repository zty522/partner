"""Benchmark parent/subject flows.

The parent owns protocol truth and evaluation.  The subject receives only a
public arm view and emits checkpoints; it cannot see scores or hidden labels.
"""
from partner.event_fabric.flows import EventFlowDefinition as Flow, FlowNode as Node


BENCHMARK_SUBJECT = Flow("benchmark_subject", "1.0.0", (
    Node("recall", "memory.context_recall"),
    Node("inspect", "project.state_inspect", ("recall",)),
    Node("cp_state", "checkpoint.capture", ("inspect",),
         parameters={"checkpoint_id": "CP1_STATE", "source_node": "inspect"}),
    Node("propose", "candidate.propose", ("cp_state", "inspect")),
    Node("critic", "candidate.critic", ("propose",)),
    Node("plan", "candidate.select", ("critic", "propose")),
    Node("cp_candidate", "checkpoint.capture", ("plan",),
         parameters={"checkpoint_id": "CP2_CANDIDATE", "source_node": "plan"}),
    Node("core_state", "core.state_build", ("cp_candidate", "plan"),
         parameters={"domain": "project"}),
    Node("core_forecast", "core.latent_forecast", ("core_state",)),
    Node("core_jev", "core.jev_evaluate", ("core_state",)),
    Node("core_commit", "core.commitment_freeze", ("core_forecast", "core_jev")),
    Node("cp_commitment", "checkpoint.capture", ("core_commit",),
         parameters={"checkpoint_id": "CP3_COMMITMENT", "source_node": "core_commit"}),
    Node("execute", "project.action_execute", ("cp_commitment", "core_commit"),
         continue_on_failure=True),
    Node("cp_execution", "checkpoint.capture", ("execute",),
         parameters={"checkpoint_id": "CP4_EXECUTION", "source_node": "execute"}),
    Node("verify", "project.outcome_verify", ("cp_execution", "execute")),
    Node("cp_verification", "checkpoint.capture", ("verify",),
         parameters={"checkpoint_id": "CP5_VERIFICATION", "source_node": "verify"}),
    Node("reflect", "project.outcome_reflect", ("cp_verification", "verify")),
    Node("core_settlement", "core.settlement", ("reflect",),
         parameters={"evaluated_node": "verify"}),
    Node("cp_settlement", "checkpoint.capture", ("core_settlement",),
         parameters={"checkpoint_id": "CP6_SETTLEMENT", "source_node": "core_settlement"}),
), "One isolated benchmark arm. It exposes checkpoints but no evaluator feedback.")


BENCHMARK_EXPERIMENT = Flow("benchmark_experiment", "1.0.0", (
    Node("signal", "benchmark.signal_validate"),
    Node("protocol", "benchmark.protocol_resolve", ("signal",)),
    Node("preflight", "benchmark.environment_preflight", ("protocol",)),
    Node("freeze", "benchmark.run_freeze", ("preflight",)),
    Node("variant_plan", "benchmark.variant_plan", ("freeze",)),
    Node("variant_parity", "benchmark.variant_parity_check", ("variant_plan",)),

    Node("baseline_submit", "benchmark.variant_submit", ("variant_parity", "variant_plan"),
         parameters={"arm_id": "baseline"}),
    Node("baseline_collect", "benchmark.arm_collect", ("baseline_submit",),
         parameters={"arm_id": "baseline"}),
    Node("baseline_integrity", "benchmark.deterministic_evaluate", ("baseline_collect",),
         parameters={"arm_id": "baseline"}),
    Node("baseline_metric", "benchmark.domain_metric_evaluate", ("baseline_collect",),
         parameters={"arm_id": "baseline"}),

    Node("candidate_submit", "benchmark.variant_submit",
         ("baseline_integrity", "baseline_metric"), parameters={"arm_id": "candidate"}),
    Node("candidate_collect", "benchmark.arm_collect", ("candidate_submit",),
         parameters={"arm_id": "candidate"}),
    Node("candidate_integrity", "benchmark.deterministic_evaluate", ("candidate_collect",),
         parameters={"arm_id": "candidate"}),
    Node("candidate_metric", "benchmark.domain_metric_evaluate", ("candidate_collect",),
         parameters={"arm_id": "candidate"}),

    Node("execution_parity", "benchmark.execution_parity_evaluate",
         ("baseline_collect", "candidate_collect")),
    Node("paired_compare", "benchmark.paired_compare",
         ("baseline_metric", "candidate_metric", "execution_parity")),
    Node("expectation_compare", "benchmark.expectation_compare", ("paired_compare",)),
    Node("guardrail_evaluate", "benchmark.guardrail_evaluate",
         ("baseline_integrity", "candidate_integrity")),
    Node("jev_evaluate", "benchmark.jev_evaluate",
         ("paired_compare", "expectation_compare", "guardrail_evaluate"), optional=True,
         continue_on_failure=True),
    Node("llm_judge", "benchmark.llm_judge",
         ("paired_compare", "expectation_compare", "guardrail_evaluate"), optional=True,
         continue_on_failure=True),
    Node("aggregate", "benchmark.aggregate",
         ("baseline_integrity", "candidate_integrity", "paired_compare",
          "expectation_compare", "guardrail_evaluate", "execution_parity",
          "jev_evaluate", "llm_judge")),
    Node("benchmark_settlement", "benchmark.settlement", ("aggregate",)),
    Node("benchmark_route", "benchmark.route_next", ("benchmark_settlement",)),
    Node("benchmark_report", "benchmark.report_compose", ("benchmark_route", "aggregate")),
    Node("benchmark_report_verify", "benchmark.report_verify", ("benchmark_report",)),
    Node("compose", "presentation.message_compose", ("benchmark_report_verify",)),
    Node("message_critic", "presentation.message_critic", ("compose",)),
    Node("deduplicate", "presentation.message_deduplicate", ("message_critic",)),
    Node("channel", "delivery.channel_route", ("deduplicate",)),
    Node("send", "delivery.send_text", ("channel",)),
    Node("delivery_verify", "delivery.verify", ("send",)),
    Node("close", "benchmark.run_close", ("delivery_verify", "benchmark_settlement")),
), "External benchmark controller with isolated subject child flows and independent settlement.")


DEFINITIONS = (BENCHMARK_EXPERIMENT, BENCHMARK_SUBJECT)
