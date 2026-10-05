"""Longitudinal v4 benchmark Suite and Episode Flow definitions."""
from partner.event_fabric.flows import EventFlowDefinition as Flow, FlowNode as Node


V4_BENCHMARK_EPISODE = Flow("v4_benchmark_episode", "0.2.0", (
    Node("intake", "v4.episode_intake"),
    Node("plan", "v4.episode_plan", ("intake",)),
    Node("plan_validate", "v4.plan_validate", ("plan",)),
    Node("commit", "v4.episode_commit", ("plan_validate",)),
    Node("arm_a", "v4.arm_execute", ("commit",), branch_id="arm_a",
         parameters={"arm_slot": 0}),
    Node("arm_b", "v4.arm_execute", ("commit",), branch_id="arm_b",
         parameters={"arm_slot": 1}),
    Node("arm_c", "v4.arm_execute", ("commit",), branch_id="arm_c",
         parameters={"arm_slot": 2}),
    Node("checkpoint", "v4.checkpoint_evaluate", ("arm_a", "arm_b", "arm_c")),
    Node("settle", "v4.episode_settle", ("checkpoint",)),
    Node("memory_audit", "v4.memory_consumption_audit", ("settle",)),
    Node("finish", "v4.episode_finish", ("memory_audit",)),
    Node("progress_compose", "v4.episode_progress_compose", ("finish",)),
    Node("progress_send", "v4.episode_progress_send", ("progress_compose",)),
), "One isolated longitudinal episode with up to three separately logged arms.")


V4_BENCHMARK_SUITE = Flow("v4_benchmark_suite", "0.2.0", (
    Node("protocol_freeze", "v4.suite_protocol_freeze"),
    Node("start_compose", "v4.suite_start_compose", ("protocol_freeze",)),
    Node("start_send", "v4.suite_start_send", ("start_compose",)),
    Node("schedule", "v4.suite_schedule", ("start_send",)),
    Node("episode_loop", "v4.episode_controller", ("schedule",)),
    Node("transfer", "v4.transfer_evaluate", ("episode_loop",)),
    Node("settle", "v4.suite_settle", ("transfer",)),
    Node("report", "v4.suite_report", ("settle",)),
    Node("final_compose", "v4.suite_final_message_compose", ("report",)),
    Node("delivery", "v4.suite_delivery", ("final_compose",)),
    Node("delivery_ack", "v4.suite_delivery_ack", ("delivery",)),
    Node("close", "v4.suite_close", ("delivery_ack",)),
), "Longitudinal multi-episode controller; each episode is a child Event Flow.")


DEFINITIONS = (V4_BENCHMARK_SUITE, V4_BENCHMARK_EPISODE)
