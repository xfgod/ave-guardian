#!/bin/bash
# Ave Guardian — Whale Watcher Cron
# 频率：每 15 分钟
# 功能：对 watchlist 中的代币运行庄家检测，检测异常行为变化

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
SCAN_INTERVAL=15

# 控盘评分变化阈值（超过此值才推送）
SCORE_CHANGE_THRESHOLD=15

# ============================================================
# 日志函数
# ============================================================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [whale_watcher] $1" | tee -a "${LOG_FILE}" 2>/dev/null || true
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [whale_watcher] ERROR: $1" | tee -a "${LOG_FILE}" 2>/dev/null || true
}

# ============================================================
# 前置检查
# ============================================================

if [ ! -f "${CREDS_FILE}" ]; then
    log_error "Credentials file not found"
    exit 1
fi

AVE_API_KEY=$(python3 -c "import json; print(json.load(open('${CREDS_FILE}'))['ave_api_key'])" 2>/dev/null)
API_PLAN=$(python3 -c "import json; print(json.load(open('${CREDS_FILE}'))['api_plan'])" 2>/dev/null)

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
    last = data.get('scan_state', {}).get('last_whale_scan')
    if last:
        elapsed = (time.time() - last) / 60
        interval = data.get('scan_state', {}).get('whale_scan_interval_minutes', ${SCAN_INTERVAL})
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

log "Starting whale watcher..."

# 获取 watchlist
WATCHLIST=$(python3 -c "
import json

try:
    with open('${STATE_FILE}') as f:
        data = json.load(f)
    
    watchlist = data.get('watchlist', [])
    
    # 只返回启用了警报的代币
    enabled = [w for w in watchlist if w.get('alert_enabled', True)]
    
    print(json.dumps(enabled))
except:
    print('[]')
" 2>/dev/null)

WATCHLIST_COUNT=$(echo "${WATCHLIST}" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

if [ "${WATCHLIST_COUNT}" -eq 0 ]; then
    log "Watchlist is empty, skipping"
    
    # 更新扫描时间
    python3 -c "
import json, time
try:
    with open('${STATE_FILE}') as f:
        data = json.load(f)
    data.setdefault('scan_state', {})['last_whale_scan'] = int(time.time())
    with open('${STATE_FILE}', 'w') as f:
        json.dump(data, f, indent=2)
except:
    pass
" 2>/dev/null
    
    exit 0
fi

log "Scanning ${WATCHLIST_COUNT} tokens from watchlist..."

# 创建临时文件存储结果
TEMP_RESULTS=$(mktemp)
ALERTS_FOUND=0

# 对每个 watchlist 代币运行庄家检测
echo "${WATCHLIST}" | python3 -c "
import json, sys, subprocess, os

watchlist = json.load(sys.stdin)
results = []

for item in watchlist:
    token = item.get('token', '')
    chain = item.get('chain', 'bsc')
    symbol = item.get('symbol', '?')
    
    if not token:
        continue
    
    try:
        # 运行 whale_detector.py
        result = subprocess.run(
            ['python3', '${SCRIPTS_DIR}/whale_detector.py', token, chain, '--output', 'json'],
            capture_output=True,
            text=True,
            timeout=60,
            env=dict(os.environ)
        )
        
        if result.returncode == 0:
            output = result.stdout.strip()
            # 找 JSON 开始的位置
            json_start = output.find('{')
            if json_start >= 0:
                json_str = output[json_start:]
                data = json.loads(json_str)
                
                manipulation = data.get('manipulation', {})
                score = manipulation.get('score', 50)
                level = manipulation.get('level', '')
                
                results.append({
                    'token': token,
                    'chain': chain,
                    'symbol': symbol,
                    'score': score,
                    'level': level,
                    'action': manipulation.get('action', ''),
                    'behavior_pattern': data.get('behavior', {}).get('pattern', ''),
                    'net_flow': data.get('behavior', {}).get('net_flow_total', 0),
                    'concentration': data.get('concentration', {}).get('top10_pct', 0),
                })
    except Exception as e:
        pass

print(json.dumps(results))
" > "${TEMP_RESULTS}" 2>/dev/null

# 检查是否有高风险代币
ALERTS=$(python3 -c "
import json

with open('${TEMP_RESULTS}') as f:
    results = json.load(f)

alerts = []
for r in results:
    score = r.get('score', 0)
    if score >= 60:
        alerts.append(r)

print(json.dumps(alerts))
" 2>/dev/null)

ALERT_COUNT=$(echo "${ALERTS}" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

if [ "${ALERT_COUNT}" -gt 0 ]; then
    log "Found ${ALERT_COUNT} high-risk tokens (score >= 60)"
    
    # 生成推送消息
    PUSH_MSG=$(echo "${ALERTS}" | python3 -c "
import json, sys

alerts = json.load(sys.stdin)
if not alerts:
    print('None')
    sys.exit(0)

lines = ['🚨 庄家监控预警', '━━━━━━━━━━━━━━━━━━']

for i, a in enumerate(alerts[:5], 1):
    symbol = a.get('symbol', '?')
    score = a.get('score', 0)
    level = a.get('level', '')
    pattern = a.get('behavior_pattern', '')
    net_flow = a.get('net_flow', 0)
    conc = a.get('concentration', 0)
    
    net_str = f'+\${abs(net_flow)/1000:.1f}K' if net_flow > 0 else f'-\${abs(net_flow)/1000:.1f}K'
    
    lines.append(f'{i}. {symbol} {level}')
    lines.append(f'   控盘评分: {score:.0f}/100 | {pattern}')
    lines.append(f'   净流量: {net_str} | Top10集中度: {conc:.1f}%')

lines.append('━━━━━━━━━━━━━━━━━━')
lines.append('⚠️ 仅供参考，不构成投资建议')

print('\n'.join(lines))
" 2>/dev/null)
    
    if [ -n "${PUSH_MSG}" ] && [ "${PUSH_MSG}" != "None" ]; then
        log "Push notification prepared"
        echo "PUSH_MESSAGE:${PUSH_MSG}" >> "${HOME}/.openclaw/workspace/.guardian-pending-push.jsonl"
    fi
else
    log "No high-risk tokens found (score < 60)"
fi

# 更新扫描状态
python3 -c "
import json, time

try:
    with open('${STATE_FILE}') as f:
        data = json.load(f)
    data.setdefault('scan_state', {})['last_whale_scan'] = int(time.time())
    data['scan_state']['whale_scan_interval_minutes'] = ${SCAN_INTERVAL}
    with open('${STATE_FILE}', 'w') as f:
        json.dump(data, f, indent=2)
except:
    pass
" 2>/dev/null

# 清理
rm -f "${TEMP_RESULTS}"

log "Whale watcher completed successfully"
