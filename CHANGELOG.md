# Changelog

All notable changes to Partner will be documented in this file.

## [v0.2.0] - 2026-05-24

### ✨ Features
- Partner core: task queue, knowledge base, journal system
- Event system: autonomous research cycles
- Multi-agent support: Hermes, OpenClaw, Codex, CrewAI, gptme
- Cron job integration for background research
- Partner skill v0.3.0 with tool restrictions
- Blog posts: Dev.to, 知乎, 小红书

### 🐛 Bug Fixes
- Fix task_queue string handling (bare strings instead of dicts)
- Fix task detail printing (don't show internal info)
- Add tool restriction hook for partner skill

### 📦 Infrastructure
- Add pyproject.toml for pip installation
- Add MANIFEST.in
- Remove docs/ from git tracking

---

## [v0.1.0] - 2026-05-23

### ✨ Features
- Initial release
- Basic Partner functionality
- Hermes integration
- CLI commands: setup, status

---

## Planned

### v0.3.0 (In Progress)
- [ ] QQ integration (NapCat)
- [ ] WeChat integration (cross-platform)
- [ ] OpenClaw integration check
- [ ] Research agent adaptation (CytoBridge, etc.)
- [ ] One-click messaging setup
- [ ] Partner self-upgrade mechanism
