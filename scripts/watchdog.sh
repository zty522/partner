#!/bin/bash
# ============================================================
# Partner Research Loop Watchdog
# ============================================================
# 外部监控脚本，由 cron 每分钟执行一次。
# 检查 /tmp/partner_research_heartbeat.txt 的心跳时间戳，
# 如果超过 2 分钟未更新，则认为研究循环已挂起并重启。
#
# 安装方式（添加到 cron）：
#   crontab -e
#   添加一行：* * * * * /path/to/partner/scripts/watchdog.sh
#
# 也可作为 systemd timer 运行。
# ============================================================

HEARTBEAT_FILE="/tmp/partner_research_heartbeat.txt"
RESTART_CMD="systemctl --user restart partner.service"
RESTART_MAX_AGE=120  # 2 分钟（秒）

# 日志文件
LOG_FILE="/tmp/partner_watchdog.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# 检查心跳文件是否存在
if [ ! -f "$HEARTBEAT_FILE" ]; then
    log "WARN: 心跳文件不存在，研究循环可能未启动"
    exit 0
fi

# 读取时间戳（第一行）
HEARTBEAT_TS=$(head -1 "$HEARTBEAT_FILE" 2>/dev/null)

# 验证时间戳格式
if ! [[ "$HEARTBEAT_TS" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
    log "WARN: 心跳文件格式异常: $HEARTBEAT_TS"
    exit 0
fi

CURRENT_TS=$(date +%s)
AGE=$(echo "$CURRENT_TS - $HEARTBEAT_TS" | bc 2>/dev/null || echo "999")

# 检查是否超过最大允许间隔
if [ "$(echo "$AGE > $RESTART_MAX_AGE" | bc 2>/dev/null)" = "1" ]; then
    log "ALERT: 研究循环心跳已停滞 ${AGE} 秒（>${RESTART_MAX_AGE}s），准备重启"
    
    # 记录重启前的状态
    if [ -f "$HEARTBEAT_FILE" ]; then
        cat "$HEARTBEAT_FILE" >> "$LOG_FILE"
    fi
    
    # 执行重启
    log "执行重启命令: $RESTART_CMD"
    eval "$RESTART_CMD" >> "$LOG_FILE" 2>&1
    RESTART_EXIT=$?
    
    if [ $RESTART_EXIT -eq 0 ]; then
        log "SUCCESS: 研究循环已重启"
    else
        log "ERROR: 重启失败 (exit=$RESTART_EXIT)"
        # 尝试直接启动 python 进程
        log "尝试直接启动 python..."
        nohup python3 -m partner.mind >> /tmp/partner_research_restart.log 2>&1 &
        log "直接启动已触发"
    fi
    
    # 重置心跳文件，防止短时间内重复重启
    echo "$CURRENT_TS" > "$HEARTBEAT_FILE"
    echo "restarted at $CURRENT_TS" >> "$HEARTBEAT_FILE"
else
    log "OK: 研究循环正常 (心跳 ${AGE}s 前)"
fi
