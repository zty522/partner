"""Blinded autonomous Partner-loop benchmark subject.

The subject owns reasoning and execution.  The parent benchmark Flow owns the
sealed oracle and all scoring, so evaluator feedback cannot enter an arm.
"""
from partner.event_fabric.flows import EventFlowDefinition as Flow, FlowNode as Node


PARTNER_LOOP_BENCH_SUBJECT = Flow("partner_loop_bench_subject", "1.1.0", (
    Node("inspect", "loop_bench.task_inspect"),
    Node("cp_state", "checkpoint.capture", ("inspect",),
         parameters={"checkpoint_id": "CP1_STATE", "source_node": "inspect"}),
    Node("propose", "loop_bench.candidate_propose", ("cp_state", "inspect")),
    Node("critic", "loop_bench.candidate_critic", ("propose",)),
    Node("plan", "loop_bench.candidate_select", ("critic", "propose")),
    Node("cp_candidate", "checkpoint.capture", ("plan",),
         parameters={"checkpoint_id": "CP2_CANDIDATE", "source_node": "plan"}),
    Node("core_state", "core.state_build", ("cp_candidate", "plan"),
         parameters={"domain": "project"}),
    Node("core_forecast", "core.latent_forecast", ("core_state",)),
    Node("core_jev", "core.jev_evaluate", ("core_state",)),
    Node("core_commit", "core.commitment_freeze", ("core_forecast", "core_jev")),
    Node("cp_commitment", "checkpoint.capture", ("core_commit",),
         parameters={"checkpoint_id": "CP3_COMMITMENT", "source_node": "core_commit"}),
    Node("execute", "loop_bench.action_execute", ("cp_commitment", "core_commit")),
    Node("cp_execution", "checkpoint.capture", ("execute",),
         parameters={"checkpoint_id": "CP4_EXECUTION", "source_node": "execute"}),
    Node("verify", "loop_bench.outcome_verify", ("cp_execution", "execute")),
    Node("cp_verification", "checkpoint.capture", ("verify",),
         parameters={"checkpoint_id": "CP5_VERIFICATION", "source_node": "verify"}),
    Node("core_settlement", "loop_bench.settlement", ("cp_verification", "verify")),
    Node("cp_settlement", "checkpoint.capture", ("core_settlement",),
         parameters={"checkpoint_id": "CP6_SETTLEMENT", "source_node": "core_settlement"}),
), "Blind autonomous choice with explicit critique, commitment, execution and settlement.")


DEFINITIONS = (PARTNER_LOOP_BENCH_SUBJECT,)
