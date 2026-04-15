#!/bin/bash
# Ave Guardian — Cron Installer
# 用于安装/卸载定时任务到 crontab

set -e

GUARDIAN_DIR="${HOME}/.openclaw/workspace/skills/ave-guardian"
CRON_DIR="${GUARDIAN_DIR}/cron"
CRON_MARKER="# Ave Guardian Cron Jobs"

show_help() {
    cat << EOF
Ave Guardian — Cron Installer

Usage: $0 [install|uninstall|list|status]

Commands:
  install    Install cron jobs
  uninstall Remove cron jobs
  list      Show current cron jobs
  status    Show cron service status

Installed Jobs:
  meme_scanner_cron.sh     — 每 30 分钟扫描 Meme 叙事
  whale_watcher_cron.sh     — 每 15 分钟监控庄家行为
  liquidity_check_cron.sh    — 每 2 小时检测流动性
  strategy_checker_cron.sh  — 每 5 分钟检查策略触发
  alert_checker_cron.sh      — 每 5 分钟检查警报触发

EOF
}

install_crons() {
    echo "Installing Ave Guardian cron jobs..."
    
    # 创建日志目录
    mkdir -p "${HOME}/.openclaw/logs"
    
    # 确保脚本可执行
    chmod +x "${CRON_DIR}"/*.sh
    
    # 生成新的 crontab 条目
    NEW_CRON_ENTRIES="
${CRON_MARKER}
# Meme Scanner — 每 30 分钟
*/30 * * * * ${CRON_DIR}/meme_scanner_cron.sh >> ${HOME}/.openclaw/logs/guardian-cron.log 2>&1

# Whale Watcher — 每 15 分钟
*/15 * * * * ${CRON_DIR}/whale_watcher_cron.sh >> ${HOME}/.openclaw/logs/guardian-cron.log 2>&1

# Liquidity Check — 每 2 小时
0 */2 * * * ${CRON_DIR}/liquidity_check_cron.sh >> ${HOME}/.openclaw/logs/guardian-cron.log 2>&1

# Strategy Checker — 每 5 分钟
*/5 * * * * ${CRON_DIR}/strategy_checker_cron.sh >> ${HOME}/.openclaw/logs/guardian-cron.log 2>&1

# Alert Checker — 每 5 分钟
*/5 * * * * ${CRON_DIR}/alert_checker_cron.sh >> ${HOME}/.openclaw/logs/guardian-cron.log 2>&1

# End Ave Guardian Cron Jobs"
    
    # 获取当前 crontab
    CURRENT_CRONTAB=$(crontab -l 2>/dev/null || true)
    
    # 移除旧的 Ave Guardian 条目
    CLEAN_CRONTAB=$(echo "${CURRENT_CRONTAB}" | sed "/${CRON_MARKER}/,/^# End Ave Guardian/d")
    
    # 添加新的条目
    NEW_CRONTAB="${CLEAN_CRONTAB}
${NEW_CRON_ENTRIES}"
    
    # 安装新的 crontab
    echo "${NEW_CRONTAB}" | crontab -
    
    echo "✅ Cron jobs installed successfully"
    echo ""
    echo "Installed jobs:"
    echo "  */30 * * * * — meme_scanner_cron.sh (Meme 叙事扫描)"
    echo "  */15 * * * * — whale_watcher_cron.sh (庄家行为监控)"
    echo "  0 */2 * * * — liquidity_check_cron.sh (流动性检测)"
    echo "  */5 * * * * — strategy_checker_cron.sh (策略触发检查)"
    echo "  */5 * * * * — alert_checker_cron.sh (异常警报检查)"
    echo ""
    echo "Log file: ${HOME}/.openclaw/logs/guardian-cron.log"
    echo ""
    echo "To view logs: tail -f ${HOME}/.openclaw/logs/guardian-cron.log"
}

uninstall_crons() {
    echo "Removing Ave Guardian cron jobs..."
    
    CURRENT_CRONTAB=$(crontab -l 2>/dev/null || true)
    
    CLEAN_CRONTAB=$(echo "${CURRENT_CRONTAB}" | sed "/${CRON_MARKER}/,/^# End Ave Guardian/d")
    
    if [ -z "${CLEAN_CRONTAB}" ]; then
        echo "No crontab to install"
    else
        echo "${CLEAN_CRONTAB}" | crontab -
    fi
    
    echo "✅ Cron jobs removed"
}

list_crons() {
    echo "Current Ave Guardian cron jobs:"
    echo ""
    crontab -l 2>/dev/null | grep -A 20 "${CRON_MARKER}" || echo "No Ave Guardian jobs found"
}

status_crons() {
    echo "Cron service status:"
    if command -v crontab >/dev/null 2>&1; then
        echo "  Cron installed: ✅"
        CRON_COUNT=$(crontab -l 2>/dev/null | grep -c "${CRON_MARKER}" || true)
        echo "  Ave Guardian jobs: ${CRON_COUNT}"
        echo ""
        echo "Recent log entries:"
        if [ -f "${HOME}/.openclaw/logs/guardian-cron.log" ]; then
            tail -10 "${HOME}/.openclaw/logs/guardian-cron.log"
        else
            echo "  (no log file yet)"
        fi
    else
        echo "  Cron installed: ❌"
        echo "  Install cron to use scheduled tasks"
    fi
}

case "${1:-install}" in
    install)
        install_crons
        ;;
    uninstall)
        uninstall_crons
        ;;
    list)
        list_crons
        ;;
    status)
        status_crons
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo "Unknown command: $1"
        show_help
        exit 1
        ;;
esac
