## v0.5.0 – Multi-Instance Parallel Architecture

### 🚀 Multi-Instance Manager
- **`partner-manager` CLI**: create, start, stop, restart, list, and monitor multiple independent Partner instances.
- Each instance has its own **QQ bot account**, **research direction**, **knowledge base**, **logs**, and **cron schedule**.

### 📂 Isolated Instance Directories
```
~/.partner/instances/{id}/
├── 00_config/    # Instance-specific config
├── 10_logs/      # Instance logs
├── 20_records/   # 🔥 Core records (exploration, knowledge, experiments)
└── 99_temp/
```

### ⏰ APScheduler-Based Cron
- Replaced external crontab with in-process APScheduler.
- Each instance runs independently: self-check (04:00), diary (23:00), clock tick (15min).
- Supports custom job scheduling via `/set_cron`.

### 🖥️ Systemd Template
- `partner@.service` template for per-instance auto-start.
- Enable: `partner-manager enable --id age_pred` → `systemctl --user start partner@age_pred.service`.

### 🔄 Migration
- Existing single-instance workspace auto-migrated to `instances/default/`.
- Full backward compatibility.

**Full Changelog**: https://github.com/zty522/partner/compare/v0.4.0...v0.5.0
