#!/bin/bash
# Ave Guardian — 通用推送脚本
# 用法: ./notify.sh "消息内容" [title]
# 支持多渠道: 微信/TG/Discord/Slack 等

GUARDIAN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PENDING_FILE="${HOME}/.openclaw/workspace/.guardian-pending-push.jsonl"
LOG_FILE="${HOME}/.openclaw/logs/guardian-cron.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [notify] $1" >> "${LOG_FILE}" 2>/dev/null || true
}

# 消息内容
MESSAGE="${1:-}"
TITLE="${2:-Ave Guardian}"

if [ -z "$MESSAGE" ]; then
    echo "用法: $0 <消息内容> [标题]"
    exit 1
fi

# 写入待推送队列（供 OpenClaw heartbeat 读取并分发）
# 格式: JSON Lines
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
cat >> "${PENDING_FILE}" << EOF
{"ts":"${TIMESTAMP}","title":"${TITLE}","message":"${MESSAGE}","channels":["auto"]}
EOF

log "已写入待推送队列: ${TITLE} | 渠道: auto"
echo "OK"
