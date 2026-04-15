#!/bin/bash
# Ave Guardian — Meme Scanner Cron
# 频率：每 30 分钟
# 功能：扫描 Meme 叙事，发现早期爆发信号并推送微信

set -e

# ============================================================
# 配置
# ============================================================

GUARDIAN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS_DIR="${GUARDIAN_DIR}/scripts"
STATE_FILE="${HOME}/.openclaw/workspace/.ave-guardian-state.json"
CREDS_FILE="${HOME}/.openclaw/workspace/.ave-credentials.json"
LOG_FILE="${HOME}/.openclaw/logs/guardian-cron.log"

# 扫描间隔（分钟）
SCAN_INTERVAL=30

# ============================================================
# 日志函数
# ============================================================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [meme_scanner] $1" | tee -a "${LOG_FILE}" 2>/dev/null || true
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [meme_scanner] ERROR: $1" | tee -a "${LOG_FILE}" 2>/dev/null || true
}

# ============================================================
# 前置检查
# ============================================================

# 检查凭证文件
if [ ! -f "${CREDS_FILE}" ]; then
    log_error "Credentials file not found: ${CREDS_FILE}"
    exit 1
fi

# 加载凭证
AVE_API_KEY=$(python3 -c "import json; print(json.load(open('${CREDS_FILE}'))['ave_api_key'])" 2>/dev/null)
API_PLAN=$(python3 -c "import json; print(json.load(open('${CREDS_FILE}').get('api_plan', 'free'))" 2>/dev/null)

if [ -z "${AVE_API_KEY}" ]; then
    log_error "AVE_API_KEY not found in credentials"
    exit 1
fi

export AVE_API_KEY
export API_PLAN

# 检查扫描状态（是否应该运行）
if [ -f "${STATE_FILE}" ]; then
    LAST_SCAN=$(python3 -c "
import json, time
try:
    data = json.load(open('${STATE_FILE}'))
    last = data.get('scan_state', {}).get('last_meme_scan')
    if last:
        elapsed = (time.time() - last) / 60
        interval = data.get('scan_state', {}).get('meme_scan_interval_minutes', ${SCAN_INTERVAL})
        print(int(elapsed), int(interval))
    else:
        print('0 ${SCAN_INTERVAL}')
except:
    print('0 ${SCAN_INTERVAL}')
" 2>/dev/null)
    
    LAST_MINUTES=$(echo "${LAST_SCAN}" | awk '{print $1}')
    INTERVAL=$(echo "${LAST_SCAN}" | awk '{print $2}')
    
    if [ "${LAST_MINUTES}" -lt "${INTERVAL}" ]; then
        log "Skipping: last scan was ${LAST_MINUTES}m ago (interval: ${INTERVAL}m)"
        exit 0
    fi
fi

# ============================================================
# 主逻辑
# ============================================================

log "Starting meme scanner..."

# 运行 meme scanner
cd "${GUARDIAN_DIR}"

OUTPUT=$(python3 scripts/meme_scanner.py --output json 2>&1)
EXIT_CODE=$?

if [ ${EXIT_CODE} -ne 0 ]; then
    log_error "meme_scanner.py failed with exit code ${EXIT_CODE}: ${OUTPUT}"
    exit ${EXIT_CODE}
fi

# 解析结果，找出高评分代币
HIGH_SCORE_TOKENS=$(echo "${OUTPUT}" | python3 -c "
import json, sys

try:
    tokens = json.load(sys.stdin)
    if not isinstance(tokens, list):
        tokens = []
    
    # 筛选高评分代币（>= 55分）
    high = []
    for t in tokens:
        score = t.get('narrative_score', 0)
        if score >= 55:
            high.append({
                'symbol': t.get('symbol', '?'),
                'token': t.get('token', ''),
                'chain': t.get('chain', '?'),
                'score': score,
                'level': t.get('narrative_level', ''),
                'price_change_24h': t.get('price_change_24h', 0),
                'tvl': t.get('tvl', 0),
            })
    
    print(json.dumps(high))
except Exception as e:
    print('[]')
" 2>/dev/null)

TOKEN_COUNT=$(echo "${HIGH_SCORE_TOKENS}" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

if [ "${TOKEN_COUNT}" -gt 0 ]; then
    log "Found ${TOKEN_COUNT} high-score tokens (score >= 55)"
    
    # 生成推送消息
    PUSH_MSG=$(echo "${HIGH_SCORE_TOKENS}" | python3 -c "
import json, sys

tokens = json.load(sys.stdin)
if not tokens:
    print('None')
    sys.exit(0)

lines = ['🔥 Meme 叙事扫描预警', '━━━━━━━━━━━━━━━━━━']

for i, t in enumerate(tokens[:5], 1):
    symbol = t.get('symbol', '?')
    score = t.get('score', 0)
    level = t.get('level', '')
    change = t.get('price_change_24h', 0)
    tvl = t.get('tvl', 0)
    
    change_str = f'+{change:.1f}%' if change >= 0 else f'{change:.1f}%'
    tvl_str = f'\${tvl/1000:.1f}K' if tvl < 1000000 else f'\${tvl/1000000:.1f}M'
    
    lines.append(f'{i}. {symbol} {level} {score:.0f}分')
    lines.append(f'   价格变化: {change_str} | TVL: {tvl_str}')

lines.append('━━━━━━━━━━━━━━━━━━')
lines.append('⚠️ Meme币极高风险，仅供参考')

print('\n'.join(lines))
" 2>/dev/null)
    
    if [ -n "${PUSH_MSG}" ] && [ "${PUSH_MSG}" != "None" ]; then
        log "Push notification prepared"
        # 这里通过 OpenClaw 的 cron handler 发送微信
        # 消息会被路由到 main session 进行发送
        echo "PUSH_MESSAGE:${PUSH_MSG}" >> "${HOME}/.openclaw/workspace/.guardian-pending-push.jsonl"
    fi
else
    log "No high-score tokens found (score < 55)"
fi

# 更新扫描状态
python3 -c "
import json, time

state_file = '${STATE_FILE}'
try:
    with open(state_file, 'r') as f:
        data = json.load(f)
except:
    data = {'scan_state': {}}

data.setdefault('scan_state', {})['last_meme_scan'] = int(time.time())
data['scan_state']['meme_scan_interval_minutes'] = ${SCAN_INTERVAL}

with open(state_file, 'w') as f:
    json.dump(data, f, indent=2)
" 2>/dev/null

log "Meme scanner completed successfully"
