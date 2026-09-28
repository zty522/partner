# Partner command scripts

Scripts are thin operator or experiment entry points. Reusable behavior belongs in the `partner` package.

| Directory | Purpose |
|---|---|
| `runtime/` | Start, supervise, inspect and control native runtimes and the web service. |
| `messaging/` | Submit tasks, inject local messages, run QQ/relay transport and inspect replies. |
| `campaigns/` | Bounded campaigns, project-driving experiments, seeding and scheduled sampling. |
| `governance/` | Index maintenance, evidence digestion, policy updates and shadow evolution. |
| `migrations/` | Explicit one-time state/schema migrations. |
| `integrations/` | External-agent shell wrappers. |
| `benchmark/` | Benchmark preparation, execution, validation, reports and dedicated canaries. |

The root contains only package metadata and this index. Service files and documentation must call the categorized path directly; compatibility aliases are intentionally not maintained.
