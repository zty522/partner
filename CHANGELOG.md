# Changelog

All notable changes to Partner will be documented in this file.

## [v0.1.0] - 2026-05-24

### ✨ Features

#### Event System (Autonomous Research)
- Autonomous research cycles: runs tasks every 15-30 minutes
- Event templates: 8 predefined research workflows
- Knowledge gap detection: automatically identifies research gaps
- Event statistics: tracks completed, spawned, and executed phases

#### Conversation Engine V2
- Multi-turn context: remembers conversation history
- Response generator: supports "show me the 3rd one", "next page"
- Proactive notifications: alerts about important findings
- User preference learning: adapts to research style

#### Self-Evolution Engine
- Strategy learner: learns which task types succeed
- Memory pruner: cleans outdated knowledge automatically
- CPE guard: monitors core capabilities, alerts on degradation

#### Strategy Map
- DAG structure: visual research roadmap
- Fork discovery: finds new research directions automatically
- Policy selection: 5-factor scoring for next action

#### Quality Assurance
- 100+ unit tests
- Knowledge base automatic audit
- End-to-end integration tests

### 🐛 Bug Fixes
- Fix task_queue string handling (bare strings instead of dicts)
- Fix task detail printing (don't show internal info)
- Add tool restriction hook for partner skill

### 📦 Infrastructure
- pyproject.toml: support `pip install partner`
- Optional dependencies: `partner[wechat]`, `partner[qq]`, `partner[voice]`
- CHANGELOG.md: version history tracking
- release.sh: automated release script

### 🤖 Supported Agents
- 🔮 Hermes Agent (Full support)
- 🦞 OpenClaw (Supported)
- ⚡ OpenAI Codex (Supported)
- 👥 CrewAI (Supported)
- 💻 gptme (Supported)

---

## Planned

### v0.2.0 (In Progress)
- [ ] QQ integration (NapCat)
- [ ] WeChat integration (cross-platform)
- [ ] OpenClaw integration check
- [ ] Research agent adaptation (CytoBridge, etc.)
- [ ] One-click messaging setup
- [ ] Partner auto-upgrade mechanism
