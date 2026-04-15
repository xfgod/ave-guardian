#!/bin/bash
# Ave Guardian — Alert Checker Cron
# 频率：每 5 分钟
# 功能：检查所有活跃警报规则是否触发

set -e

GUARDIAN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS_DIR="${GUARDIAN_DIR}/scripts"
STATE_FILE="${HOME}/.openclaw/workspace/.ave-guardian-state.json"
CREDS_FILE="${HOME}/.openclaw/workspace/.ave-credentials.json"
LOG_FILE="${HOME}/.openclaw/logs/guardian-cron.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [alert_checker] $1" | tee -a "${LOG_FILE}" 2>/dev/null || true
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [alert_checker] ERROR: $1" | tee -a "${LOG_FILE}" 2>/dev/null || true
}

# 检查凭证
if [ ! -f "${CREDS_FILE}" ]; then
    log_error "Credentials not found"
    exit 1
fi

AVE_API_KEY=$(python3 -c "import json; print(json.load(open('${CREDS_FILE}'))['ave_api_key'])" 2>/dev/null)
if [ -z "${AVE_API_KEY}" ]; then
    log_error "AVE_API_KEY not found"
    exit 1
fi
export AVE_API_KEY
export API_PLAN

log "Starting alert checker..."

cd "${GUARDIAN_DIR}"

OUTPUT=$(python3 scripts/anomaly_alert.py check 2>&1)
EXIT_CODE=$?

if [ ${EXIT_CODE} -ne 0 ]; then
    log_error "anomaly_alert.py failed: ${OUTPUT}"
    exit ${EXIT_CODE}
fi

# 检查是否有警报触发
HAS_ALERTS=$(echo "${OUTPUT}" | grep -c "🚨" || true)

if [ "${HAS_ALERTS}" -gt 0 ]; then
    log "${HAS_ALERTS} alert(s) triggered"
    
    # 清理输出格式，生成推送
    PUSH_MSG=$(echo "${OUTPUT}" | grep -v "^\[Alert Engine\]" | grep -v "^$" | head -40 | tr '\n' ' ' | sed 's/  */ /g')
    
    if [ -n "${PUSH_MSG}" ] && [ "${PUSH_MSG}" != "✅ 暂无异常" ]; then
        FULL_MSG="🚨 异常警报通知

━━━━━━━━━━━━━━━━━━
${PUSH_MSG}
━━━━━━━━━━━━━━━━━━
⚠️ 及时关注盘面变化。"
        echo "PUSH_MESSAGE:${FULL_MSG}" >> "${HOME}/.openclaw/workspace/.guardian-pending-push.jsonl"
    fi
else
    log "No alerts triggered"
fi

log "Alert checker completed"
