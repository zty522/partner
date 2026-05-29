## v0.5.0 – Multi-Instance Parallel Architecture

### 🚀 Multi-Instance Manager
- New `partner-manager` CLI: create, start, stop, restart, list, and monitor multiple independent Partner instances.
- Each instance has its own QQ bot account, research direction, knowledge base, logs, and cron schedule.

### 📂 Isolated Instance Directories
```
~/.partner/instances/{id}/
├── 00_config/
├── 10_logs/
├── 20_records/   (exploration, knowledge, experiments)
└── 99_temp/
```

### ⏰ APScheduler-Based Cron
- Replaced external crontab with in-process APScheduler.
- Each instance runs independently: self-check, diary, clock tick.

### 🖥️ Systemd Template
- `partner@.service` template for per-instance auto-start.

### 🔄 Migration
- Existing single-instance workspace auto-migrated to `instances/default/`.

### 🪟 Windows Installer Fixes
- Embedded Python 3.12 now properly installs setuptools/wheel before `pip install -e .`.
- Full pip error output shown on failure (no more hidden errors).
- `_pth` file written without UTF-8 BOM to prevent standard library loading failure.

**Full Changelog**: https://github.com/zty522/partner/compare/v0.4.0...v0.5.0
