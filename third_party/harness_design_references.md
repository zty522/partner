# Harness design-reference attribution

## DeepSeek Harness

- Upstream: <https://github.com/deepseek-ai/deepseek-harness>
- Studied revision: `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`
- Revision date: 2026-08-21
- License at that revision: MIT, copyright 2026 DeepSeek
- Local study checkout: `/mnt/e/work/partner_workspace/external/code/deepseek-harness`
- Primary study files: `docs/architecture.md`, `docs/agent-lifecycle.md`,
  `docs/subsystems/session.md`, `docs/subsystems/tools.md`,
  `docs/subsystems/compaction.md`, `docs/tool-execution-pipeline.md`, and
  `docs/defensive-patterns.md`.

Concepts adapted independently: durable append-only event evidence; separation
of durable session facts from live control signals; explicit lifecycle brackets;
capability provider/consumer boundaries; tool policy around a canonical tool
outcome; and crash-detectable compaction markers.

## OpenAI Codex

- Upstream: <https://github.com/openai/codex>
- Studied revision: `76d98a771e6cd44a79a3ab895a9f7c49d27d6deb`
- Revision date: 2026-08-24
- License at that revision: Apache-2.0; upstream `NOTICE` retained in checkout
- Local study checkout: `/mnt/e/work/partner_workspace/external/code/openai-codex`
- Primary study files: `codex-rs/rollout-trace/README.md`,
  `codex-rs/thread-store/README.md`, `codex-rs/execpolicy/README.md`,
  `codex-rs/protocol/src/protocol.rs`, `codex-rs/core/src/compact_remote.rs`,
  and `docs/sandbox.md`.

Concepts adapted independently: record raw evidence before semantic reduction;
keep model-visible conversation separate from runtime observations; maintain a
storage boundary for canonical history and metadata; evaluate command policy
independently from execution; and preserve trace links across threads and tools.

## Hermes Agent

- Upstream: <https://github.com/NousResearch/hermes-agent>
- Local checkout remote: <https://github.com/zty522/hermes-agent> (fork; recorded separately from upstream)
- Studied revision: `9d059cfa3b05d693f9f7e1f8a486e5b29b872860`
- Revision date: 2026-08-03
- License: MIT, copyright Nous Research
- Local checkout: `/mnt/e/work/partner_workspace/external/code/hermes-agent`
- Primary files: `agent/trajectory.py`, `agent/memory_provider.py`, `agent/turn_finalizer.py`,
  `agent/context_compressor.py`, `agent/tool_executor.py`, `docs/observability/README.md`.

Concepts adapted independently: post-turn memory synchronization, searchable experiences, candidate skill review,
protected recent context, and correlated session/turn/tool observer identifiers. Hermes trajectory completion is not
treated as verified RL.

## OpenClaw

- Upstream: <https://github.com/openclaw/openclaw>
- Studied revision: `97196164358dd9b58bd6d2207ccfcd219a2492ad`
- Revision date: 2026-08-25
- License: MIT, copyright OpenClaw Foundation
- Local checkout: `/mnt/e/work/partner_workspace/external/code/openclaw`
- Primary files: `docs/agent-runtime-architecture.md`, `docs/reference/session-management-compaction.md`,
  `docs/concepts/agent-workspace.md`, `docs/concepts/memory.md`.

Concepts adapted independently: session/transcript authority, user-visible harness transcript mirroring, tiered
workspace memory, pre-compaction memory flush, and isolated cron retention/authority reset. Partner does not import
the OpenClaw Gateway.

## Non-adoption boundary

No source file from any of the four harness repositories was copied into Partner. Partner
keeps its Python event runtime, five-instance portfolio, Campaign/WorkItem,
Receipt, QQ/browser delivery, and offline-RL promotion contracts. TypeScript
Cordis/OpenClaw, the Rust Codex runtime, and Hermes Python runtime are references, not new foundations.
Any future source-level reuse requires a separate experiment, license review,
file-level attribution, regression tests, and a recorded promotion decision.
