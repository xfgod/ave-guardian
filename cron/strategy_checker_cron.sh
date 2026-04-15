#!/bin/bash
# Ave Guardian — Strategy Checker Cron
# 频率：每 5 分钟
# 功能：检查 armed 策略是否触发，触发时发送通知

set -e

GUARDIAN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS_DIR="${GUARDIAN_DIR}/scripts"
STATE_FILE="${HOME}/.openclaw/workspace/.ave-guardian-state.json"
CREDS_FILE="${HOME}/.openclaw/workspace/.ave-credentials.json"
LOG_FILE="${HOME}/.openclaw/logs/guardian-cron.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [strategy_checker] $1" | tee -a "${LOG_FILE}" 2>/dev/null || true
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [strategy_checker] ERROR: $1" | tee -a "${LOG_FILE}" 2>/dev/null || true
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

log "Starting strategy checker..."

# 运行策略检查
cd "${GUARDIAN_DIR}"

OUTPUT=$(python3 scripts/strategy_executor.py check 2>&1)
EXIT_CODE=$?

if [ ${EXIT_CODE} -ne 0 ]; then
    log_error "strategy_executor.py failed: ${OUTPUT}"
    exit ${EXIT_CODE}
fi

# 检查是否有触发
TRIGGERED=$(echo "${OUTPUT}" | grep -c "触发" || true)

if [ "${TRIGGERED}" -gt 0 ]; then
    log "${TRIGGERED} strategy(ies) triggered"
    
    # 生成推送消息
    PUSH_MSG=$(echo "${OUTPUT}" | grep -v "^\[Strategy Executor\]" | grep -v "^$" | head -30 | tr '\n' ' ' | sed 's/  */ /g')
    
    if [ -n "${PUSH_MSG}" ]; then
        FULL_MSG="⚡ 策略触发通知

━━━━━━━━━━━━━━━━━━
${PUSH_MSG}
━━━━━━━━━━━━━━━━━━
⚠️ 投资有风险，请关注仓位变化。"
        
        echo "PUSH_MESSAGE:${FULL_MSG}" >> "${HOME}/.openclaw/workspace/.guardian-pending-push.jsonl"
    fi
else
    log "No strategies triggered"
fi

log "Strategy checker completed"
