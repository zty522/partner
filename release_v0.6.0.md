## v0.6.0 - Long-Term Research Partner

This release turns Partner from a basic autonomous runtime into a long-running AI research companion.
Partner now focuses on persistent project lifelines, research habits, long-term memory, reflection,
growth, and user-facing stage reports.

### Core Changes

- Added a continuous project lifeline: active projects can keep re-queuing the next minimal step instead of relying only on fixed cron ticks.
- Added a lightweight interaction orchestrator: user messages are interpreted first, then code applies safe lifeline mutations.
- Added long-term research memory for project cards, lessons, user ideas, risk events, growth events, and cross-project transfer candidates.
- Added explicit research habits: minimal closed-loop execution, evidence-first reporting, leakage checks, report deduplication, memory cleanup, and reflection.
- Added a brain-inspired architecture model: hippocampus-like episodes, synapse-like method memory, prefrontal project contracts, amygdala-like risk checks, cerebellum-like habits, and default-mode reflection.
- Added stage report generation: Partner can produce Markdown, PPTX, and PDF reports under the user-facing project folder after enough project work has accumulated.
- Added content feed groundwork for user-shared articles, posts, videos, and social-platform signals, treated as hypotheses rather than facts.
- Added Codex adapter support alongside Hermes and Direct mode.
- Improved QQ official bot behavior: fewer duplicated replies, better internal-error filtering, more concise user-facing progress reports.
- Improved project brief and memory cleanup to reduce prompt bloat and prevent file-list noise from leaking into user reports.
- Updated Windows desktop launchers, GUI assets, and installer metadata for v0.6.0.

### User-Facing Report Improvements

Partner now tries harder to avoid messages such as:

- "I will continue in the background."
- raw backend errors
- max-iteration warnings
- file list summaries
- duplicate progress reports

Reports should focus on research content:

- what changed
- what was verified
- what is still uncertain
- what risk was detected
- what the next executable step is

### Stage Reports

Projects can now produce:

- `latest_stage_report.md`
- `<project>_stage_report_YYYYMMDD_HHMM.pptx`
- `<project>_stage_report_YYYYMMDD_HHMM.pdf`

Default trigger:

```text
PARTNER_STAGE_REPORT_EVERY_ROUNDS=24
PARTNER_STAGE_REPORT_MIN_INTERVAL_HOURS=12
```

Reports are written under:

```text
user/reports/<project>/
user/current_project/reports/
```

### Installation

Linux / WSL:

```bash
curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
partner setup
```

Windows PowerShell:

```powershell
powershell -Command "& { iwr -useb https://raw.githubusercontent.com/zty522/partner/main/scripts/install.ps1 } | iex"
partner setup
```

The Windows installer is built automatically by GitHub Actions and attached to this release.

### Notes

This release changes the runtime behavior substantially. Existing users should run:

```bash
partner update
partner setup
```

Then restart their Partner instances.
