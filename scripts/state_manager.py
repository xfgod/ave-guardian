#!/usr/bin/env python3
"""
Ave Guardian — State Manager
完整的持久化状态管理模块。

所有模块都依赖此模块进行状态读写。

状态文件：~/.openclaw/workspace/.ave-guardian-state.json

功能：
- 原子读写（防止并发写入损坏）
- Watchlist 管理（关注列表）
- Alert Rules 管理（警报规则）
- Strategy 管理（交易策略）
- Context 追踪（最近分析记录）
- Scan State 追踪（定时扫描状态）
- Audit Log（操作审计日志）
"""

import json
import os
import sys
import time
import fcntl
import shutil
from datetime import datetime, timezone
from typing import Any, Optional
from pathlib import Path

# ============================================================
# 路径配置
# ============================================================

# 运行时状态文件（OpenClaw workspace）
STATE_FILE = Path(os.environ.get(
    "AVE_GUARDIAN_STATE",
    os.path.expanduser("~/.openclaw/workspace/.ave-guardian-state.json")
))

# 模板文件（git 仓库里的初始状态）
TEMPLATE_FILE = Path(__file__).parent.parent / "state" / ".ave-guardian-state.json"

# 凭证文件
CREDS_FILE = Path(os.environ.get(
    "AVE_CREDS",
    os.path.expanduser("~/.openclaw/workspace/.ave-credentials.json")
))

# 备份文件（每次写入前备份，用于灾难恢复）
BACKUP_FILE = STATE_FILE.with_suffix(".json.bak")

# 日志文件
LOG_DIR = Path(os.environ.get(
    "AVE_GUARDIAN_LOG",
    os.path.expanduser("~/.openclaw/logs")
))

# ============================================================
# 错误类型
# ============================================================

class StateError(Exception):
    """状态管理基础异常"""
    pass

class StateFileNotFoundError(StateError):
    """状态文件不存在"""
    pass

class StateLockError(StateError):
    """文件锁获取失败"""
    pass

class StateValidationError(StateError):
    """状态数据验证失败"""
    pass

class DuplicateEntryError(StateError):
    """重复条目"""
    pass

class EntryNotFoundError(StateError):
    """条目未找到"""
    pass

# ============================================================
# 日志工具
# ============================================================

def get_logger():
    """获取日志记录器"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "guardian-state.log"
    
    import logging
    logger = logging.getLogger("ave_guardian.state")
    if not logger.handlers:
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

_logger = None

def log(level: str, message: str, **kwargs):
    """写日志"""
    global _logger
    if _logger is None:
        _logger = get_logger()
    
    extra = " ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
    full_msg = f"{message} {extra}".strip() if extra else message
    
    getattr(_logger, level.lower(), _logger.info)(full_msg)

# ============================================================
# 状态模板（初始空状态）
# ============================================================

def get_default_state() -> dict:
    """
    返回默认状态模板。
    所有新字段必须有默认值，保证前向兼容。
    """
    return {
        "version": "1.0",
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
        
        # 关注列表
        "watchlist": [],
        
        # 警报规则
        "alerts": {
            "max_open_alerts": 10,
            "rules": []
        },
        
        # 交易策略
        "strategies": [],
        
        # 上下文追踪
        "context": {
            "last_analysis_token": None,
            "last_analysis_chain": None,
            "last_analysis_type": None,
            "recent_queries": [],       # 最近 N 次查询（用于去重/记忆）
            "active_wss_subscriptions": [],  # 当前 WSS 订阅（仅作记录，不做运行时保证）
            "recent_watchlist_changes": []   # watchlist 最近的变动历史
        },
        
        # 定时扫描状态
        "scan_state": {
            "last_meme_scan": None,
            "last_whale_scan": None,
            "last_liquidity_scan": None,
            "last_strategy_check": None,
            "meme_scan_interval_minutes": 30,
            "whale_scan_interval_minutes": 15,
            "liquidity_scan_interval_hours": 2
        },
        
        # 操作审计日志
        "audit_log": [],
        
        # 统计信息
        "stats": {
            "total_analyses": 0,
            "total_alerts_triggered": 0,
            "total_strategies_executed": 0,
            "total_scans_run": 0
        }
    }

# ============================================================
# 凭证读取
# ============================================================

def get_credentials() -> dict:
    """读取 AVE API 凭证"""
    if not CREDS_FILE.exists():
        raise StateError(
            f"Credentials file not found: {CREDS_FILE}\n"
            "Please create it with ave_api_key and api_plan."
        )
    
    with open(CREDS_FILE, "r", encoding="utf-8") as f:
        creds = json.load(f)
    
    required = ["ave_api_key"]
    missing = [k for k in required if k not in creds or not creds[k]]
    if missing:
        raise StateError(f"Missing credentials: {missing}")
    
    return creds

# ============================================================
# 原子读写
# ============================================================

def _acquire_lock(lock_file: Path, timeout: float = 5.0) -> tuple:
    """
    获取文件锁。
    返回 (lock_fd, lock_path)。
    使用完必须调用 _release_lock。
    """
    LOCK_FILE = lock_file.with_suffix(".lock")
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    start_time = time.time()
    while True:
        try:
            lock_fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_fd, LOCK_FILE
        except (IOError, OSError):
            if time.time() - start_time > timeout:
                raise StateLockError(
                    f"Failed to acquire lock after {timeout}s: {LOCK_FILE}"
                )
            time.sleep(0.05)


def _release_lock(lock_fd: int, lock_path: Path):
    """释放文件锁"""
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def _atomic_write(file_path: Path, data: dict):
    """
    原子写入状态文件。
    流程：
    1. 获取锁
    2. 备份原文件
    3. 写入临时文件
    4. os.replace（原子替换）
    5. 释放锁
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    lock_fd, lock_path = _acquire_lock(file_path)
    
    try:
        # 1. 备份原文件（如果存在）
        if file_path.exists():
            shutil.copy2(file_path, file_path.with_suffix(".json.bak"))
        
        # 2. 写入临时文件
        temp_file = file_path.with_suffix(".json.tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())  # 确保写入磁盘
        
        # 3. 原子替换
        os.replace(temp_file, file_path)
        
    finally:
        _release_lock(lock_fd, lock_path)


def read_state() -> dict:
    """
    读取当前状态文件。
    如果文件不存在或损坏，尝试从备份恢复或创建默认状态。
    """
    if not STATE_FILE.exists():
        log("info", "State file not found, creating default")
        default = get_default_state()
        _atomic_write(STATE_FILE, default)
        return default
    
    lock_fd, lock_path = _acquire_lock(STATE_FILE, timeout=3.0)
    
    try:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            log("warning", f"State file corrupted: {e}, attempting recovery")
            # 尝试从备份恢复
            if BACKUP_FILE.exists():
                try:
                    with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    log("info", "Recovered from backup")
                    # 重新保存正确的状态
                    _atomic_write(STATE_FILE, data)
                except Exception:
                    log("error", "Backup recovery failed, creating fresh state")
                    data = get_default_state()
                    _atomic_write(STATE_FILE, data)
            else:
                data = get_default_state()
                _atomic_write(STATE_FILE, data)
        
        # 验证并补充默认值（前向兼容）
        data = _validate_and_patch(data)
        return data
        
    finally:
        _release_lock(lock_fd, lock_path)


def write_state(state: dict) -> None:
    """
    写入状态文件。
    所有修改必须通过此函数持久化。
    """
    state["updated_at"] = int(time.time())
    _atomic_write(STATE_FILE, state)
    log("info", f"State written", updated_at=state["updated_at"])


def _validate_and_patch(state: dict) -> dict:
    """
    验证状态完整性，并补充新增字段（前向兼容）。
    如果发现致命错误，抛出 StateValidationError。
    """
    if not isinstance(state, dict):
        raise StateValidationError("State must be a dict")
    
    default = get_default_state()
    
    # 检查必要字段
    if "version" not in state:
        raise StateValidationError("Missing 'version' field")
    
    # 补充缺失的顶层字段
    for key, default_value in default.items():
        if key not in state:
            log("warning", f"Missing state field '{key}', adding default")
            state[key] = default_value
        elif isinstance(default_value, dict):
            # 深拷贝，防止引用问题
            import copy
            for subkey, subvalue in default_value.items():
                if subkey not in state[key]:
                    log("warning", f"Missing state field '{key}.{subkey}', adding default")
                    state[key][subkey] = subvalue
    
    return state


# ============================================================
# 审计日志
# ============================================================

def _add_audit_log(state: dict, action: str, module: str, detail: dict, result: str = "success"):
    """
    添加审计日志条目。
    保留最近 500 条记录。
    """
    entry = {
        "timestamp": int(time.time()),
        "datetime": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "module": module,
        "detail": detail,
        "result": result
    }
    
    state.setdefault("audit_log", [])
    state["audit_log"].append(entry)
    
    # 只保留最近 500 条
    if len(state["audit_log"]) > 500:
        state["audit_log"] = state["audit_log"][-500:]


# ============================================================
# 工具函数
# ============================================================

def generate_id(prefix: str, state: dict) -> str:
    """生成唯一 ID"""
    counter_key = f"_id_counter_{prefix}"
    counter = state.get(counter_key, 0) + 1
    state[counter_key] = counter
    return f"{prefix}_{counter}"


def now_ts() -> int:
    """返回当前时间戳（秒）"""
    return int(time.time())


def ts_to_str(ts: int) -> str:
    """时间戳转可读字符串"""
    if ts is None:
        return "never"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# Watchlist 管理
# ============================================================

def add_to_watchlist(
    token: str,
    chain: str,
    symbol: Optional[str] = None,
    note: Optional[str] = None,
    alert_enabled: bool = True,
    alert_threshold_usd: float = 5000.0
) -> dict:
    """
    将代币添加到关注列表。
    
    Args:
        token: 代币合约地址
        chain: 链 ID（如 bsc, eth, base）
        symbol: 代币符号（可选，自动从 API 获取）
        note: 用户备注
        alert_enabled: 是否默认启用警报
        alert_threshold_usd: 默认警报阈值（USD）
    
    Returns:
        新增的 watchlist 条目
    
    Raises:
        DuplicateEntryError: 代币已在关注列表中
    """
    state = read_state()
    
    # 检查是否重复
    for item in state["watchlist"]:
        if item["token"].lower() == token.lower() and item["chain"].lower() == chain.lower():
            raise DuplicateEntryError(
                f"Token {token} on {chain} is already in watchlist"
            )
    
    entry = {
        "id": generate_id("watch", state),
        "token": token,
        "chain": chain,
        "symbol": symbol or "UNKNOWN",
        "added_at": now_ts(),
        "added_at_str": datetime.now(timezone.utc).isoformat(),
        "note": note or "",
        "alert_enabled": alert_enabled,
        "alert_threshold_usd": alert_threshold_usd,
        "stats": {
            "analysis_count": 0,
            "alert_triggered_count": 0,
            "last_seen": None
        }
    }
    
    state["watchlist"].append(entry)
    
    # 记录到 recent_watchlist_changes
    state["context"]["recent_watchlist_changes"].append({
        "action": "add",
        "token": token,
        "chain": chain,
        "at": now_ts()
    })
    # 只保留最近 20 条变动记录
    if len(state["context"]["recent_watchlist_changes"]) > 20:
        state["context"]["recent_watchlist_changes"] = state["context"]["recent_watchlist_changes"][-20:]
    
    _add_audit_log(state, "add_to_watchlist", "state_manager", {
        "token": token, "chain": chain, "symbol": symbol
    })
    
    write_state(state)
    log("info", f"Added to watchlist", token=token, chain=chain)
    
    return entry


def remove_from_watchlist(token: str, chain: str) -> bool:
    """
    从关注列表移除代币。
    
    Returns:
        True if removed, False if not found
    """
    state = read_state()
    original_len = len(state["watchlist"])
    
    state["watchlist"] = [
        item for item in state["watchlist"]
        if not (item["token"].lower() == token.lower() and item["chain"].lower() == chain.lower())
    ]
    
    removed = len(state["watchlist"]) < original_len
    
    if removed:
        # 同步删除相关的 alerts 和 strategies
        state["alerts"]["rules"] = [
            r for r in state["alerts"]["rules"]
            if not (r.get("token", "").lower() == token.lower() and r.get("chain", "").lower() == chain.lower())
        ]
        state["strategies"] = [
            s for s in state["strategies"]
            if not (s.get("token", "").lower() == token.lower() and s.get("chain", "").lower() == chain.lower())
        ]
        
        state["context"]["recent_watchlist_changes"].append({
            "action": "remove",
            "token": token,
            "chain": chain,
            "at": now_ts()
        })
        if len(state["context"]["recent_watchlist_changes"]) > 20:
            state["context"]["recent_watchlist_changes"] = state["context"]["recent_watchlist_changes"][-20:]
        
        _add_audit_log(state, "remove_from_watchlist", "state_manager", {
            "token": token, "chain": chain
        })
        write_state(state)
        log("info", f"Removed from watchlist", token=token, chain=chain)
    
    return removed


def get_watchlist(token: Optional[str] = None, chain: Optional[str] = None) -> list:
    """
    获取关注列表。
    
    Args:
        token: 可选，精确匹配 token 地址
        chain: 可选，精确匹配 chain
    
    Returns:
        匹配的 watchlist 条目列表
    """
    state = read_state()
    
    results = state["watchlist"]
    
    if token:
        results = [
            r for r in results
            if r["token"].lower() == token.lower()
        ]
    
    if chain:
        results = [
            r for r in results
            if r["chain"].lower() == chain.lower()
        ]
    
    return results


def update_watchlist_entry(token: str, chain: str, updates: dict) -> Optional[dict]:
    """
    更新 watchlist 条目中的特定字段。
    
    Args:
        token: 代币地址
        chain: 链 ID
        updates: 要更新的字段字典
    
    Returns:
        更新后的条目，如果未找到返回 None
    """
    state = read_state()
    
    for item in state["watchlist"]:
        if item["token"].lower() == token.lower() and item["chain"].lower() == chain.lower():
            # 只允许更新特定字段
            allowed_fields = {"note", "alert_enabled", "alert_threshold_usd", "symbol"}
            for key, value in updates.items():
                if key in allowed_fields:
                    item[key] = value
            
            _add_audit_log(state, "update_watchlist", "state_manager", {
                "token": token, "chain": chain, "updates": updates
            })
            write_state(state)
            return item
    
    return None


def increment_watchlist_stat(token: str, chain: str, stat_name: str):
    """增加 watchlist 条目的统计计数"""
    state = read_state()
    
    for item in state["watchlist"]:
        if item["token"].lower() == token.lower() and item["chain"].lower() == chain.lower():
            if "stats" not in item:
                item["stats"] = {
                    "analysis_count": 0,
                    "alert_triggered_count": 0,
                    "last_seen": None
                }
            item["stats"][stat_name] = item["stats"].get(stat_name, 0) + 1
            item["stats"]["last_seen"] = now_ts()
            write_state(state)
            return


# ============================================================
# Alert Rules 管理
# ============================================================

ALERT_TYPES = {
    "whale_tx",           # 大额鲸鱼交易
    "price_spike",        # 价格异动
    "liquidity_drop",     # 流动性骤降
    "buy_sell_ratio",     # 买卖比异常
    "new_holder_surge",   # 新地址激增
    "whale_accumulation", # 鲸鱼吸筹
    "price_above",       # 价格向上突破
    "price_below",       # 价格向下突破
}


def add_alert_rule(
    token: str,
    chain: str,
    alert_type: str,
    threshold_usd: Optional[float] = None,
    threshold_pct: Optional[float] = None,
    threshold_count: Optional[int] = None,
    direction: str = "any",  # any, up, down
    cooldown_minutes: int = 30,
    note: Optional[str] = None
) -> dict:
    """
    添加警报规则。
    
    Args:
        token: 代币地址
        chain: 链 ID
        alert_type: 警报类型（必须是 ALERT_TYPES 中的值）
        threshold_usd: 金额阈值（USD，用于 whale_tx, whale_accumulation）
        threshold_pct: 百分比阈值（用于 price_spike, liquidity_drop）
        threshold_count: 数量阈值（用于 new_holder_surge）
        direction: 触发方向（any, up, down）
        cooldown_minutes: 冷却时间（分钟）
        note: 用户备注
    
    Returns:
        新增的警报规则
    
    Raises:
        ValueError: 无效的 alert_type
        DuplicateEntryError: 相同规则已存在
    """
    if alert_type not in ALERT_TYPES:
        raise ValueError(
            f"Invalid alert_type: {alert_type}. "
            f"Valid types: {ALERT_TYPES}"
        )
    
    state = read_state()
    
    # 检查规则数量限制
    active_rules = [r for r in state["alerts"]["rules"] if r.get("active", True)]
    if len(active_rules) >= state["alerts"]["max_open_alerts"]:
        raise StateError(
            f"Maximum alert rules reached ({state['alerts']['max_open_alerts']}). "
            f"Please delete some rules first."
        )
    
    # 检查重复规则
    for rule in state["alerts"]["rules"]:
        if (rule["token"].lower() == token.lower() and
            rule["chain"].lower() == chain.lower() and
            rule["type"] == alert_type and
            rule.get("active", True)):
            raise DuplicateEntryError(
                f"Active alert rule for {token} ({alert_type}) already exists"
            )
    
    rule = {
        "id": generate_id("alert", state),
        "token": token,
        "chain": chain,
        "type": alert_type,
        "threshold_usd": threshold_usd,
        "threshold_pct": threshold_pct,
        "threshold_count": threshold_count,
        "direction": direction,
        "cooldown_minutes": cooldown_minutes,
        "last_triggered": None,
        "last_triggered_str": None,
        "trigger_count": 0,
        "active": True,
        "note": note or "",
        "created_at": now_ts(),
        "created_at_str": datetime.now(timezone.utc).isoformat()
    }
    
    state["alerts"]["rules"].append(rule)
    
    _add_audit_log(state, "add_alert_rule", "anomaly_alert", {
        "rule_id": rule["id"],
        "token": token,
        "chain": chain,
        "type": alert_type
    })
    
    write_state(state)
    log("info", f"Alert rule added", rule_id=rule["id"], type=alert_type, token=token)
    
    return rule


def remove_alert_rule(rule_id: str) -> bool:
    """
    删除警报规则。
    
    Returns:
        True if deleted, False if not found
    """
    state = read_state()
    original_len = len(state["alerts"]["rules"])
    
    state["alerts"]["rules"] = [
        r for r in state["alerts"]["rules"]
        if r["id"] != rule_id
    ]
    
    removed = len(state["alerts"]["rules"]) < original_len
    
    if removed:
        _add_audit_log(state, "remove_alert_rule", "anomaly_alert", {
            "rule_id": rule_id
        })
        write_state(state)
        log("info", f"Alert rule removed", rule_id=rule_id)
    
    return removed


def get_alert_rules(
    token: Optional[str] = None,
    chain: Optional[str] = None,
    alert_type: Optional[str] = None,
    active_only: bool = False
) -> list:
    """
    获取警报规则列表。
    
    Args:
        token: 可选，精确匹配
        chain: 可选，精确匹配
        alert_type: 可选，匹配类型
        active_only: 只返回激活的规则
    
    Returns:
        匹配的规则列表
    """
    state = read_state()
    results = state["alerts"]["rules"]
    
    if token:
        results = [r for r in results if r["token"].lower() == token.lower()]
    if chain:
        results = [r for r in results if r["chain"].lower() == chain.lower()]
    if alert_type:
        results = [r for r in results if r["type"] == alert_type]
    if active_only:
        results = [r for r in results if r.get("active", True)]
    
    return results


def check_alert_cooldown(rule_id: str) -> tuple[bool, Optional[int]]:
    """
    检查警报规则是否在冷却中。
    
    Returns:
        (is_in_cooldown, seconds_remaining)
        如果规则不存在，返回 (False, 0)
    """
    state = read_state()
    
    for rule in state["alerts"]["rules"]:
        if rule["id"] == rule_id:
            if rule["last_triggered"] is None:
                return False, 0
            
            elapsed = now_ts() - rule["last_triggered"]
            cooldown_seconds = rule["cooldown_minutes"] * 60
            remaining = cooldown_seconds - elapsed
            
            return remaining > 0, max(0, int(remaining))
    
    return False, 0


def trigger_alert(rule_id: str) -> Optional[dict]:
    """
    触发警报规则。
    更新 last_triggered 和 trigger_count。
    
    Returns:
        被触发的规则，如果未找到返回 None
    """
    state = read_state()
    
    for rule in state["alerts"]["rules"]:
        if rule["id"] == rule_id:
            rule["last_triggered"] = now_ts()
            rule["last_triggered_str"] = datetime.now(
                timezone.utc
            ).isoformat()
            rule["trigger_count"] = rule.get("trigger_count", 0) + 1
            
            state["stats"]["total_alerts_triggered"] += 1
            
            _add_audit_log(state, "alert_triggered", "anomaly_alert", {
                "rule_id": rule_id,
                "token": rule["token"],
                "chain": rule["chain"],
                "type": rule["type"],
                "trigger_count": rule["trigger_count"]
            })
            
            write_state(state)
            log("info", f"Alert triggered", rule_id=rule_id, type=rule["type"])
            
            return rule
    
    return None


def update_alert_rule(rule_id: str, updates: dict) -> Optional[dict]:
    """
    更新警报规则字段。
    
    Args:
        rule_id: 规则 ID
        updates: 要更新的字段
    
    Returns:
        更新后的规则，未找到返回 None
    """
    state = read_state()
    
    allowed_fields = {
        "threshold_usd", "threshold_pct", "threshold_count",
        "direction", "cooldown_minutes", "active", "note"
    }
    
    for rule in state["alerts"]["rules"]:
        if rule["id"] == rule_id:
            for key, value in updates.items():
                if key in allowed_fields:
                    rule[key] = value
            
            _add_audit_log(state, "update_alert_rule", "anomaly_alert", {
                "rule_id": rule_id,
                "updates": {k: v for k, v in updates.items() if k in allowed_fields}
            })
            write_state(state)
            return rule
    
    return None


def pause_alert_rule(rule_id: str) -> bool:
    """暂停警报规则"""
    result = update_alert_rule(rule_id, {"active": False})
    return result is not None


def resume_alert_rule(rule_id: str) -> bool:
    """恢复警报规则"""
    result = update_alert_rule(rule_id, {"active": True})
    return result is not None


# ============================================================
# Strategy 管理
# ============================================================

STRATEGY_CONDITIONS = {
    "price_drop_pct",      # 价格下跌百分比
    "price_rise_pct",      # 价格上涨百分比
    "price_below",         # 价格低于
    "price_above",        # 价格高于
    "volume_spike",        # 成交量激增
    "whale_detected",      # 检测到鲸鱼活动
}

STRATEGY_ACTIONS = {
    "buy",                 # 市价买入
    "sell",                # 市价卖出
    "limit_buy",           # 限价买入
    "limit_sell",          # 限价卖出
}


def add_strategy(
    token: str,
    chain: str,
    symbol: str,
    condition: str,
    condition_value: float,
    condition_unit: str = "pct",  # pct, usd, count
    action: str = "buy",
    action_amount_usd: float = 0.0,
    action_amount_token: float = 0.0,
    tp_pct: float = 0.0,
    sl_pct: float = 0.0,
    tp_price: float = 0.0,
    sl_price: float = 0.0,
    note: Optional[str] = None
) -> dict:
    """
    添加交易策略。
    
    Args:
        token: 代币地址
        chain: 链 ID
        symbol: 代币符号
        condition: 触发条件类型（必须是 STRATEGY_CONDITIONS 中的值）
        condition_value: 触发条件值
        condition_unit: 条件单位（pct, usd）
        action: 执行动作（必须是 STRATEGY_ACTIONS 中的值）
        action_amount_usd: 操作金额（USD）
        action_amount_token: 操作数量（token）
        tp_pct: 止盈百分比（相对于买入价）
        sl_pct: 止损百分比（相对于买入价）
        tp_price: 止盈目标价（绝对价格）
        sl_price: 止损目标价（绝对价格）
        note: 用户备注
    
    Returns:
        新增的策略
    """
    if condition not in STRATEGY_CONDITIONS:
        raise ValueError(
            f"Invalid condition: {condition}. "
            f"Valid types: {STRATEGY_CONDITIONS}"
        )
    if action not in STRATEGY_ACTIONS:
        raise ValueError(
            f"Invalid action: {action}. "
            f"Valid types: {STRATEGY_ACTIONS}"
        )
    
    state = read_state()
    
    strategy = {
        "id": generate_id("strat", state),
        "token": token,
        "chain": chain,
        "symbol": symbol,
        "condition": condition,
        "condition_value": condition_value,
        "condition_unit": condition_unit,
        "action": action,
        "action_amount_usd": action_amount_usd,
        "action_amount_token": action_amount_token,
        "tp_pct": tp_pct,
        "sl_pct": sl_pct,
        "tp_price": tp_price,
        "sl_price": sl_price,
        "status": "armed",  # armed, triggered, completed, cancelled, failed
        "armed_at": now_ts(),
        "armed_at_str": datetime.now(timezone.utc).isoformat(),
        "triggered_at": None,
        "triggered_at_str": None,
        "completed_at": None,
        "completed_at_str": None,
        "trigger_count": 0,
        "last_check_at": None,
        "linked_order_id": None,  # 关联的 AVE 订单 ID
        "execution_price": None,  # 实际执行价格
        "execution_result": None,  # 执行结果备注
        "note": note or "",
        "created_at": now_ts(),
        "created_at_str": datetime.now(timezone.utc).isoformat()
    }
    
    state["strategies"].append(strategy)
    
    _add_audit_log(state, "add_strategy", "strategy_executor", {
        "strategy_id": strategy["id"],
        "token": token,
        "chain": chain,
        "condition": condition,
        "action": action
    })
    
    write_state(state)
    log("info", f"Strategy added", strategy_id=strategy["id"], condition=condition)
    
    return strategy


def remove_strategy(strategy_id: str) -> bool:
    """
    删除策略。
    
    Returns:
        True if deleted, False if not found
    """
    state = read_state()
    original_len = len(state["strategies"])
    
    state["strategies"] = [
        s for s in state["strategies"]
        if s["id"] != strategy_id
    ]
    
    removed = len(state["strategies"]) < original_len
    
    if removed:
        _add_audit_log(state, "remove_strategy", "strategy_executor", {
            "strategy_id": strategy_id
        })
        write_state(state)
        log("info", f"Strategy removed", strategy_id=strategy_id)
    
    return removed


def get_strategies(
    token: Optional[str] = None,
    chain: Optional[str] = None,
    status: Optional[str] = None,
    active_only: bool = False
) -> list:
    """
    获取策略列表。
    
    Args:
        token: 可选，精确匹配
        chain: 可选，精确匹配
        status: 可选，匹配状态
        active_only: 只返回激活的（armed 且未触发的）策略
    """
    state = read_state()
    results = state["strategies"]
    
    if token:
        results = [s for s in results if s["token"].lower() == token.lower()]
    if chain:
        results = [s for s in results if s["chain"].lower() == chain.lower()]
    if status:
        results = [s for s in results if s["status"] == status]
    if active_only:
        results = [s for s in results if s["status"] == "armed"]
    
    return results


def update_strategy(strategy_id: str, updates: dict) -> Optional[dict]:
    """
    更新策略字段。
    
    Returns:
        更新后的策略，未找到返回 None
    """
    state = read_state()
    
    allowed_fields = {
        "status", "triggered_at", "triggered_at_str",
        "completed_at", "completed_at_str", "trigger_count",
        "last_check_at", "linked_order_id", "execution_price",
        "execution_result", "note"
    }
    
    for strategy in state["strategies"]:
        if strategy["id"] == strategy_id:
            for key, value in updates.items():
                if key in allowed_fields:
                    strategy[key] = value
            
            _add_audit_log(state, "update_strategy", "strategy_executor", {
                "strategy_id": strategy_id,
                "updates": {k: v for k, v in updates.items() if k in allowed_fields}
            })
            write_state(state)
            return strategy
    
    return None


def trigger_strategy(strategy_id: str, execution_price: float = 0.0, order_id: Optional[str] = None) -> Optional[dict]:
    """
    标记策略已触发。
    
    Args:
        strategy_id: 策略 ID
        execution_price: 执行价格
        order_id: 关联的订单 ID
    
    Returns:
        更新后的策略
    """
    now = now_ts()
    result = update_strategy(strategy_id, {
        "status": "triggered",
        "triggered_at": now,
        "triggered_at_str": datetime.now(timezone.utc).isoformat(),
        "trigger_count": 0,  # 重置，为 TP/SL 计数
        "execution_price": execution_price,
        "linked_order_id": order_id
    })
    
    if result:
        state = read_state()
        state["stats"]["total_strategies_executed"] += 1
        write_state(state)
        log("info", f"Strategy triggered", strategy_id=strategy_id)
    
    return result


def complete_strategy(strategy_id: str, result: str = "completed") -> Optional[dict]:
    """
    标记策略已完成。
    
    Args:
        strategy_id: 策略 ID
        result: 完成结果（completed, cancelled, failed）
    """
    now = now_ts()
    return update_strategy(strategy_id, {
        "status": result,
        "completed_at": now,
        "completed_at_str": datetime.now(timezone.utc).isoformat()
    })


def arm_pending_strategies():
    """
    将所有 pending 状态的策略转为 armed。
    （用于系统启动时重置）
    """
    state = read_state()
    updated = False
    
    for strategy in state["strategies"]:
        if strategy["status"] == "pending":
            strategy["status"] = "armed"
            strategy["armed_at"] = now_ts()
            strategy["armed_at_str"] = datetime.now(timezone.utc).isoformat()
            updated = True
    
    if updated:
        write_state(state)
        log("info", "Pending strategies armed")


# ============================================================
# Context 管理
# ============================================================

def update_context(
    last_analysis_token: Optional[str] = None,
    last_analysis_chain: Optional[str] = None,
    last_analysis_type: Optional[str] = None
):
    """更新上下文追踪信息"""
    state = read_state()
    
    if last_analysis_token:
        state["context"]["last_analysis_token"] = last_analysis_token
    if last_analysis_chain:
        state["context"]["last_analysis_chain"] = last_analysis_chain
    if last_analysis_type:
        state["context"]["last_analysis_type"] = last_analysis_type
    
    write_state(state)


def add_recent_query(query: str, module: str, result_summary: str = ""):
    """
    添加最近查询记录。
    保留最近 50 条。
    """
    state = read_state()
    
    entry = {
        "timestamp": now_ts(),
        "datetime": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "module": module,
        "result_summary": result_summary
    }
    
    state["context"]["recent_queries"].append(entry)
    
    if len(state["context"]["recent_queries"]) > 50:
        state["context"]["recent_queries"] = state["context"]["recent_queries"][-50:]
    
    write_state(state)


def get_recent_queries(limit: int = 10) -> list:
    """获取最近的查询记录"""
    state = read_state()
    queries = state["context"]["recent_queries"]
    return queries[-limit:] if len(queries) > limit else queries


def clear_recent_queries():
    """清空最近查询记录"""
    state = read_state()
    state["context"]["recent_queries"] = []
    write_state(state)


# ============================================================
# Scan State 管理
# ============================================================

def update_scan_state(
    scan_type: str,  # meme, whale, liquidity, strategy
    timestamp: Optional[int] = None
):
    """
    更新定时扫描状态。
    
    Args:
        scan_type: 扫描类型（meme, whale, liquidity）
        timestamp: 扫描时间戳，默认当前时间
    """
    state = read_state()
    key = f"last_{scan_type}_scan"
    
    if timestamp is None:
        timestamp = now_ts()
    
    state["scan_state"][key] = timestamp
    
    _add_audit_log(state, f"scan_{scan_type}", "cron", {
        "timestamp": timestamp
    })
    
    write_state(state)


def get_scan_state(scan_type: str) -> dict:
    """
    获取指定扫描类型的状态。
    
    Returns:
        {"last_scan": ts or None, "interval_minutes": int}
    """
    state = read_state()
    scan_state = state["scan_state"]
    
    key = f"last_{scan_type}_scan"
    interval_key = f"{scan_type}_scan_interval_minutes"
    
    last_scan = scan_state.get(key)
    
    # interval 可能以 hours 或 minutes 结尾
    interval_minutes = scan_state.get(interval_key)
    if interval_minutes is None:
        # 尝试 hours 版本
        interval_minutes = scan_state.get(f"{scan_type}_scan_interval_hours")
        if interval_minutes:
            interval_minutes *= 60
    
    return {
        "last_scan": last_scan,
        "last_scan_str": ts_to_str(last_scan),
        "interval_minutes": interval_minutes or 30,
        "should_run": _should_run_scan(last_scan, interval_minutes or 30)
    }


def _should_run_scan(last_scan_ts: Optional[int], interval_minutes: int) -> bool:
    """
    判断是否应该运行扫描。
    基于上次运行时间和间隔。
    """
    if last_scan_ts is None:
        return True
    
    elapsed_minutes = (now_ts() - last_scan_ts) / 60
    return elapsed_minutes >= interval_minutes


def should_run_meme_scan() -> bool:
    """判断是否应该运行 Meme 扫描"""
    return get_scan_state("meme")["should_run"]


def should_run_whale_scan() -> bool:
    """判断是否应该运行 Whale 扫描"""
    return get_scan_state("whale")["should_run"]


def should_run_liquidity_scan() -> bool:
    """判断是否应该运行流动性扫描"""
    return get_scan_state("liquidity")["should_run"]


# ============================================================
# 统计信息
# ============================================================

def increment_stat(stat_name: str, value: int = 1):
    """增加统计计数"""
    state = read_state()
    state["stats"][stat_name] = state["stats"].get(stat_name, 0) + value
    write_state(state)


def get_stats() -> dict:
    """获取统计信息"""
    state = read_state()
    return state.get("stats", {})


# ============================================================
# 审计日志查询
# ============================================================

def get_audit_log(
    module: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 50
) -> list:
    """
    获取审计日志。
    
    Args:
        module: 可选，按模块过滤
        action: 可选，按操作过滤
        limit: 返回条数限制
    """
    state = read_state()
    logs = state.get("audit_log", [])
    
    if module:
        logs = [l for l in logs if l.get("module") == module]
    if action:
        logs = [l for l in logs if l.get("action") == action]
    
    return logs[-limit:] if len(logs) > limit else logs


# ============================================================
# 健康检查
# ============================================================

def health_check() -> dict:
    """
    运行状态文件健康检查。
    
    Returns:
        健康状态报告
    """
    report = {
        "state_file_exists": STATE_FILE.exists(),
        "state_file_size": STATE_FILE.stat().st_size if STATE_FILE.exists() else 0,
        "backup_exists": BACKUP_FILE.exists(),
        "backup_size": BACKUP_FILE.stat().st_size if BACKUP_FILE.exists() else 0,
        "creds_exists": CREDS_FILE.exists(),
        "watchlist_count": 0,
        "alert_rules_count": 0,
        "active_alert_rules_count": 0,
        "strategies_count": 0,
        "armed_strategies_count": 0,
        "stats": {},
        "issues": []
    }
    
    if not report["state_file_exists"]:
        report["issues"].append("State file does not exist")
        return report
    
    try:
        state = read_state()
        report["watchlist_count"] = len(state["watchlist"])
        report["alert_rules_count"] = len(state["alerts"]["rules"])
        report["active_alert_rules_count"] = len([
            r for r in state["alerts"]["rules"] if r.get("active", True)
        ])
        report["strategies_count"] = len(state["strategies"])
        report["armed_strategies_count"] = len([
            s for s in state["strategies"] if s["status"] == "armed"
        ])
        report["stats"] = state.get("stats", {})
        report["last_updated"] = state.get("updated_at")
        report["last_updated_str"] = ts_to_str(state.get("updated_at"))
        
    except Exception as e:
        report["issues"].append(f"Failed to read state: {e}")
    
    if not report["creds_exists"]:
        report["issues"].append("Credentials file does not exist")
    
    return report


# ============================================================
# 主函数（CLI 入口）
# ============================================================

def main():
    """CLI 入口"""
    if len(sys.argv) < 2:
        print("Ave Guardian State Manager")
        print()
        print("Usage:")
        print("  python3 state_manager.py read                    # 读取状态")
        print("  python3 state_manager.py health                  # 健康检查")
        print("  python3 state_manager.py watchlist              # 查看关注列表")
        print("  python3 state_manager.py alerts                 # 查看警报规则")
        print("  python3 state_manager.py strategies              # 查看策略")
        print("  python3 state_manager.py audit [limit]           # 查看审计日志")
        print("  python3 state_manager.py stats                   # 查看统计")
        print("  python3 state_manager.py scan-state [type]      # 查看扫描状态")
        print()
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == "read":
        state = read_state()
        print(json.dumps(state, indent=2, ensure_ascii=False))
    
    elif command == "health":
        report = health_check()
        print(json.dumps(report, indent=2, ensure_ascii=False))
    
    elif command == "watchlist":
        items = get_watchlist()
        if not items:
            print("Watchlist is empty.")
        else:
            for i, item in enumerate(items, 1):
                print(f"{i}. {item['symbol']} ({item['chain']})")
                print(f"   Token: {item['token']}")
                print(f"   Added: {item['added_at_str']}")
                print(f"   Alert: {'ON' if item.get('alert_enabled') else 'OFF'} @ ${item.get('alert_threshold_usd', 0):.0f}+")
                print()
    
    elif command == "alerts":
        rules = get_alert_rules()
        if not rules:
            print("No alert rules.")
        else:
            for i, rule in enumerate(rules, 1):
                status = "🟢" if rule.get("active") else "🔴"
                print(f"{i}. {status} [{rule['id']}] {rule['type']} on {rule['token'][:12]}... ({rule['chain']})")
                print(f"   Cooldown: {rule['cooldown_minutes']}min | Triggered: {rule.get('trigger_count', 0)} times")
                print(f"   Last: {rule.get('last_triggered_str', 'never')}")
                print()
    
    elif command == "strategies":
        strategies = get_strategies()
        if not strategies:
            print("No strategies.")
        else:
            for i, s in enumerate(strategies, 1):
                print(f"{i}. [{s['status']}] {s['symbol']} ({s['chain']})")
                print(f"   Condition: {s['condition']} {s['condition_value']} {s.get('condition_unit', 'pct')}")
                print(f"   Action: {s['action']} @ ${s.get('action_amount_usd', 0):.2f}")
                print(f"   TP: {s.get('tp_pct', 0)}% | SL: {s.get('sl_pct', 0)}%")
                print(f"   Armed: {s['armed_at_str']}")
                print()
    
    elif command == "audit":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        logs = get_audit_log(limit=limit)
        if not logs:
            print("No audit log entries.")
        else:
            for entry in logs:
                print(f"[{entry['datetime']}] {entry['module']}.{entry['action']} — {entry['result']}")
                if entry.get('detail'):
                    print(f"   {json.dumps(entry['detail'], ensure_ascii=False)}")
                print()
    
    elif command == "stats":
        stats = get_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    
    elif command == "scan-state":
        scan_type = sys.argv[2] if len(sys.argv) > 2 else None
        if scan_type:
            result = get_scan_state(scan_type)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print("Available scan types: meme, whale, liquidity")
            for t in ["meme", "whale", "liquidity"]:
                r = get_scan_state(t)
                print(f"  {t}: last={r['last_scan_str']}, interval={r['interval_minutes']}min, should_run={r['should_run']}")
    
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
