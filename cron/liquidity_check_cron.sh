#!/bin/bash
# Ave Guardian — Liquidity Check Cron
# 频率：每 2 小时
# 功能：检测 watchlist 代币的流动性骤降

set -e

GUARDIAN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS_DIR="${GUARDIAN_DIR}/scripts"
STATE_FILE="${HOME}/.openclaw/workspace/.ave-guardian-state.json"
CREDS_FILE="${HOME}/.openclaw/workspace/.ave-credentials.json"
LOG_FILE="${HOME}/.openclaw/logs/guardian-cron.log"

SCAN_INTERVAL=120  # 2小时

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [liquidity_check] $1" | tee -a "${LOG_FILE}" 2>/dev/null || true
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [liquidity_check] ERROR: $1" | tee -a "${LOG_FILE}" 2>/dev/null || true
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

# 检查扫描状态
if [ -f "${STATE_FILE}" ]; then
    LAST_SCAN=$(python3 -c "
import json, time
try:
    data = json.load(open('${STATE_FILE}'))
    last = data.get('scan_state', {}).get('last_liquidity_scan')
    if last:
        elapsed = (time.time() - last) / 60
        interval = data.get('scan_state', {}).get('liquidity_scan_interval_hours', ${SCAN_INTERVAL}) * 60
        print(int(elapsed), int(interval))
    else:
        print('0 ${SCAN_INTERVAL}')
except:
    print('0 ${SCAN_INTERVAL}')
" 2>/dev/null)
    
    LAST_MINUTES=$(echo "${LAST_SCAN}" | awk '{print $1}')
    INTERVAL=$(echo "${LAST_SCAN}" | awk '{print $2}')
    
    if [ "${LAST_MINUTES}" -lt "${INTERVAL}" ]; then
        log "Skipping: last check was ${LAST_MINUTES}m ago (interval: ${INTERVAL}m)"
        exit 0
    fi
fi

log "Starting liquidity check..."

# 获取 watchlist
WATCHLIST=$(python3 -c "
import json
try:
    with open('${STATE_FILE}') as f:
        data = json.load(f)
    watchlist = data.get('watchlist', [])
    print(json.dumps([{'token': w.get('token'), 'chain': w.get('chain', 'bsc'), 'symbol': w.get('symbol', '?')} for w in watchlist]))
except:
    print('[]')
" 2>/dev/null)

WATCHLIST_COUNT=$(echo "${WATCHLIST}" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

if [ "${WATCHLIST_COUNT}" -eq 0 ]; then
    log "Watchlist empty, skipping"
    python3 -c "import json, time; data=json.load(open('${STATE_FILE}')); data.setdefault('scan_state',{})['last_liquidity_scan']=int(time.time()); json.dump(data,open('${STATE_FILE}','w'),indent=2)" 2>/dev/null
    exit 0
fi

log "Checking ${WATCHLIST_COUNT} tokens..."

# 检测流动性问题
ALERTS=$(echo "${WATCHLIST}" | python3 -c "
import json, sys, subprocess, os

watchlist = json.load(sys.stdin)
alerts = []

for item in watchlist:
    token = item.get('token', '')
    chain = item.get('chain', 'bsc')
    symbol = item.get('symbol', '?')
    
    if not token:
        continue
    
    try:
        result = subprocess.run(
            ['python3', '${SCRIPTS_DIR}/health_reporter.py', token, chain, '--output', 'json'],
            capture_output=True,
            text=True,
            timeout=60,
            env=dict(os.environ)
        )
        
        if result.returncode == 0:
            # 提取 JSON
            json_str = result.stdout.strip()
            json_start = json_str.find('{')
            if json_start >= 0:
                data = json.loads(json_str[json_start:])
                liq = data.get('liquidity', {})
                score = liq.get('score', 50)
                
                if score < 50:
                    alerts.append({
                        'symbol': symbol,
                        'token': token,
                        'chain': chain,
                        'score': score,
                        'level': liq.get('level', ''),
                        'tvl': data.get('token_detail', {}).get('tvl', 0),
                        'vol_24h': data.get('token_detail', {}).get('tx_volume_u_24h', 0),
                    })
    except:
        pass

print(json.dumps(alerts))
" 2>/dev/null)

ALERT_COUNT=$(echo "${ALERTS}" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

if [ "${ALERT_COUNT}" -gt 0 ]; then
    log "Found ${ALERT_COUNT} tokens with low liquidity (score < 50)"
    
    PUSH_MSG=$(echo "${ALERTS}" | python3 -c "
import json, sys

alerts = json.load(sys.stdin)
if not alerts:
    print('None')
    sys.exit(0)

lines = ['💧 流动性预警', '━━━━━━━━━━━━━━━━━━']

for a in alerts[:5]:
    symbol = a.get('symbol', '?')
    score = a.get('score', 0)
    level = a.get('level', '')
    tvl = a.get('tvl', 0)
    
    tvl_str = f'\${tvl/1000:.0f}K' if tvl < 1000000 else f'\${tvl/1000000:.1f}M'
    
    lines.append(f'{symbol} {level} {score:.0f}分')
    lines.append(f'   TVL: {tvl_str}')

lines.append('━━━━━━━━━━━━━━━━━━')
lines.append('⚠️ 流动性不足，谨慎参与')

print('\n'.join(lines))
" 2>/dev/null)
    
    if [ -n "${PUSH_MSG}" ] && [ "${PUSH_MSG}" != "None" ]; then
        echo "PUSH_MESSAGE:${PUSH_MSG}" >> "${HOME}/.openclaw/workspace/.guardian-pending-push.jsonl"
    fi
else
    log "No liquidity issues found"
fi

# 更新扫描状态
python3 -c "
import json, time
try:
    with open('${STATE_FILE}') as f:
        data = json.load(f)
    data.setdefault('scan_state', {})['last_liquidity_scan'] = int(time.time())
    with open('${STATE_FILE}', 'w') as f:
        json.dump(data, f, indent=2)
except:
    pass
" 2>/dev/null

log "Liquidity check completed"
