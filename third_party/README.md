# Third-party notices and design references

This directory records third-party code that is distributed with Partner, and
external projects studied as design references. A reference entry does not mean
that its source code is vendored, executed, or incorporated into Partner.

## Harness design references (2026-08-26)

| Project | Upstream | Pinned revision | License | Use in Partner |
|---|---|---|---|---|
| DeepSeek Harness | <https://github.com/deepseek-ai/deepseek-harness> | `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e` | MIT | architecture study only; no source copied |
| OpenAI Codex | <https://github.com/openai/codex> | `76d98a771e6cd44a79a3ab895a9f7c49d27d6deb` | Apache-2.0 | architecture study only; no source copied |
| Hermes Agent | <https://github.com/NousResearch/hermes-agent> | `9d059cfa3b05d693f9f7e1f8a486e5b29b872860` | MIT | memory/skill/observer study only; no source copied |
| OpenClaw | <https://github.com/openclaw/openclaw> | `97196164358dd9b58bd6d2207ccfcd219a2492ad` | MIT | session/gateway/memory study only; no source copied |

Read-only shallow checkouts used for the study are outside the Partner source
tree at `/mnt/e/work/partner_workspace/external/code/`. They are not packaged
with Partner. See [harness_design_references.md](harness_design_references.md)
for the exact borrowed concepts and exclusions.

The existing `*_LICENSE` files in this directory cover separately distributed
third-party components. Consult each upstream license before copying any source
in a future experiment.
