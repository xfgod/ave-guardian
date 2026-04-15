#!/usr/bin/env python3
"""
Ave Guardian — Anomaly Alert Engine
异常警报引擎。

功能：
1. 从 state_manager 读取活跃的警报规则
2. 对 watchlist 中的每个代币进行异常检测
3. 支持的警报类型：
   - whale_tx: 大额鲸鱼交易
   - price_spike: 价格异动
   - liquidity_drop: 流动性骤降
   - buy_sell_ratio: 买卖比异常
   - whale_accumulation: 鲸鱼吸筹
   - new_holder_surge: 新地址激增
4. 检查冷却时间，避免重复警报
5. 触发警报时记录到 state 并输出警报信息

用法：
    python3 anomaly_alert.py
    python3 anomaly_alert.py list
    python3 anomaly_alert.py check <token> <chain>
    python3 anomaly_alert.py simulate <rule_id>
"""

import sys
import os
import json
import argparse
import time
from datetime import datetime, timezone
from typing import Optional

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import utils
from utils import (
    format_price,
    format_amount,
    format_pct,
    format_address,
    ts_to_str,
    run_ave_rest,
    get_state_manager,
)

# ============================================================
# 常量定义
# ============================================================

# 警报类型
ALERT_TYPE_WHALE_TX = "whale_tx"
ALERT_TYPE_PRICE_SPIKE = "price_spike"
ALERT_TYPE_LIQUIDITY_DROP = "liquidity_drop"
ALERT_TYPE_BUY_SELL_RATIO = "buy_sell_ratio"
ALERT_TYPE_WHALE_ACCUMULATION = "whale_accumulation"
ALERT_TYPE_NEW_HOLDER_SURGE = "new_holder_surge"
ALERT_TYPE_PRICE_ABOVE = "price_above"
ALERT_TYPE_PRICE_BELOW = "price_below"

# 所有警报类型
ALL_ALERT_TYPES = {
    ALERT_TYPE_WHALE_TX,
    ALERT_TYPE_PRICE_SPIKE,
    ALERT_TYPE_LIQUIDITY_DROP,
    ALERT_TYPE_BUY_SELL_RATIO,
    ALERT_TYPE_WHALE_ACCUMULATION,
    ALERT_TYPE_NEW_HOLDER_SURGE,
    ALERT_TYPE_PRICE_ABOVE,
    ALERT_TYPE_PRICE_BELOW,
}

# 默认冷却时间（分钟）
DEFAULT_COOLDOWN = 30

# 大户最小交易量（USD）
MIN_WHALE_TX_USD = 5000

# ============================================================
# 数据获取
# ============================================================

def get_token_detail_for_alert(token: str, chain: str) -> dict:
    """获取代币详情（用于警报）"""
    result = run_ave_rest("token", "--address", token, "--chain", chain)
    
    if "error" in result:
        return {}
    
    data = result.get("data", {})
    if isinstance(data, dict):
        data = data.get("token", data)
    
    return {
        "token": data.get("token", token),
        "chain": data.get("chain", chain),
        "symbol": data.get("symbol", "?"),
        "current_price_usd": _safe_float(data.get("current_price_usd")),
        "price_change_5m": _safe_float(data.get("token_price_change_5m", data.get("price_change_5m"))),
        "price_change_1h": _safe_float(data.get("token_price_change_1h", data.get("price_change_1h"))),
        "price_change_24h": _safe_float(data.get("token_price_change_24h")),
        "tvl": _safe_float(data.get("tvl")),
        "tx_volume_u_24h": _safe_float(data.get("tx_volume_u_24h")),
        "tx_count_24h": _safe_float(data.get("tx_count_24h", 0)),
        "holders": data.get("holders", 0),
        "main_pair": data.get("main_pair", ""),
    }


def get_recent_txs_for_alert(token: str, chain: str, pair: str, limit: int = 50) -> list:
    """获取最近交易（用于警报检测）"""
    if not pair:
        return []
    
    result = run_ave_rest(
        "txs",
        "--pair", pair,
        "--chain", chain,
        "--limit", str(limit)
    )
    
    if "error" in result:
        return []
    
    raw_data = result.get("data", {})
    if isinstance(raw_data, dict):
        raw_data = raw_data.get("txs", [])
    if not isinstance(raw_data, list):
        return []
    
    txs = []
    for tx in raw_data:
        txs.append({
            "time": _safe_int(tx.get("tx_time")),
            "amount_usd": _safe_float(tx.get("amount_usd", 0)),
            "sender": tx.get("sender_address", ""),
            "from_token_symbol": tx.get("from_token_symbol", ""),
            "to_token_symbol": tx.get("to_token_symbol", ""),
        })
    
    return txs


def get_holders_for_alert(token: str, chain: str, limit: int = 10) -> list:
    """获取持仓（用于警报检测）"""
    result = run_ave_rest(
        "holders",
        "--address", token,
        "--chain", chain,
        "--limit", str(limit)
    )
    
    if "error" in result:
        return []
    
    raw_data = result.get("data", [])
    if isinstance(raw_data, dict):
        raw_data = raw_data.get("holders", [])
    if not isinstance(raw_data, list):
        return []
    
    holders = []
    for h in raw_data[:limit]:
        holders.append({
            "holder": h.get("holder", h.get("address", "")),
            "balance_ratio": _safe_float(h.get("balance_ratio", 0)),
            "balance_usd": _safe_float(h.get("balance_usd", 0)),
        })
    
    return holders


# ============================================================
# 警报检测器
# ============================================================

def check_whale_tx(
    token: str,
    chain: str,
    threshold_usd: float,
    direction: str = "any",
    window_minutes: int = 60
) -> dict:
    """
    检测大额鲸鱼交易。
    
    Args:
        threshold_usd: 触发阈值（USD）
        direction: any / buy / sell
        window_minutes: 检测窗口（分钟）
    
    Returns:
        {"triggered": bool, "details": {...}}
    """
    token_detail = get_token_detail_for_alert(token, chain)
    if not token_detail:
        return {"triggered": False, "error": "Failed to get token detail"}
    
    pair = token_detail.get("main_pair", "")
    txs = get_recent_txs_for_alert(token, chain, pair, limit=100)
    
    if not txs:
        return {"triggered": False, "reason": "No recent txs"}
    
    now_ts = int(time.time())
    window_start = now_ts - (window_minutes * 60)
    
    # 筛选窗口内的交易
    recent_txs = [tx for tx in txs if tx.get("time", 0) >= window_start]
    
    if not recent_txs:
        return {"triggered": False, "reason": "No txs in window"}
    
    # 按金额排序，找到最大的交易
    recent_txs.sort(key=lambda x: x.get("amount_usd", 0), reverse=True)
    
    max_tx = recent_txs[0]
    max_amount = max_tx.get("amount_usd", 0)
    
    if max_amount < threshold_usd:
        return {
            "triggered": False,
            "max_tx_amount": max_amount,
            "threshold": threshold_usd,
            "reason": f"Max tx ${format_amount(max_amount)} below threshold ${format_amount(threshold_usd)}"
        }
    
    # 判断方向
    from_sym = max_tx.get("from_token_symbol", "").upper()
    side = "unknown"
    if from_sym in ["USDT", "USDC", "BUSD", "DAI", "BNB", "ETH", "WETH"]:
        side = "buy"
    else:
        side = "sell"
    
    if direction != "any" and direction != side:
        return {"triggered": False, "reason": f"Direction {side} doesn't match filter {direction}"}
    
    return {
        "triggered": True,
        "max_tx_amount": max_amount,
        "threshold": threshold_usd,
        "direction": side,
        "sender": max_tx.get("sender", ""),
        "time": max_tx.get("time"),
        "token_detail": token_detail,
        "summary": (
            f"{'🐋 鲸鱼买入' if side == 'buy' else '🐋 鲸鱼卖出'} "
            f"${format_amount(max_amount)} "
            f"(阈值 ${format_amount(threshold_usd)})"
        )
    }


def check_price_spike(
    token: str,
    chain: str,
    threshold_pct: float = 5.0,
    direction: str = "any"
) -> dict:
    """
    检测价格异动。
    
    Args:
        threshold_pct: 触发阈值（百分比）
        direction: any / up / down
    
    Returns:
        {"triggered": bool, "details": {...}}
    """
    token_detail = get_token_detail_for_alert(token, chain)
    if not token_detail:
        return {"triggered": False, "error": "Failed to get token detail"}
    
    price_change_5m = abs(token_detail.get("price_change_5m", 0))
    price_change_1h = abs(token_detail.get("price_change_1h", 0))
    actual_direction_5m = "up" if token_detail.get("price_change_5m", 0) > 0 else "down"
    actual_direction_1h = "up" if token_detail.get("price_change_1h", 0) > 0 else "down"
    
    # 用 5m 变化检测
    triggered = price_change_5m >= threshold_pct
    actual_change = token_detail.get("price_change_5m", 0)
    direction_match = direction == "any" or direction == actual_direction_5m
    
    if not triggered:
        return {
            "triggered": False,
            "price_change_5m": price_change_5m,
            "threshold": threshold_pct,
            "reason": f"5m change {price_change_5m:.2f}% below threshold {threshold_pct}%"
        }
    
    if not direction_match:
        return {"triggered": False, "reason": f"Direction {actual_direction_5m} doesn't match filter {direction}"}
    
    return {
        "triggered": True,
        "price_change_5m": actual_change,
        "price_change_1h": token_detail.get("price_change_1h", 0),
        "threshold": threshold_pct,
        "direction": actual_direction_5m,
        "token_detail": token_detail,
        "summary": (
            f"📈 价格{'上涨' if actual_direction_5m == 'up' else '下跌'} "
            f"{format_pct(actual_change)} (5m) "
            f"(阈值 ±{threshold_pct}%)"
        )
    }


def check_liquidity_drop(
    token: str,
    chain: str,
    threshold_pct: float = 30.0,
    window_hours: int = 24
) -> dict:
    """
    检测流动性骤降。
    
    比较当前 TVL 与 window_hours 前的 TVL。
    注意：此检测需要历史数据，简化版用 24h 内的 TVL 变化估算。
    
    Returns:
        {"triggered": bool, "details": {...}}
    """
    token_detail = get_token_detail_for_alert(token, chain)
    if not token_detail:
        return {"triggered": False, "error": "Failed to get token detail"}
    
    current_tvl = token_detail.get("tvl", 0)
    # 由于没有实时历史 TVL，这里用 24h 成交量对比来估算
    # 如果 24h 成交量远大于 TVL，说明流动性可能被抽走
    vol_24h = token_detail.get("tx_volume_u_24h", 0)
    
    if current_tvl == 0:
        return {"triggered": False, "reason": "TVL is 0"}
    
    # 用 vol/tvl 比例估算流动性压力
    vol_tvl_ratio = vol_24h / current_tvl
    
    if vol_tvl_ratio > threshold_pct / 100:
        return {
            "triggered": True,
            "current_tvl": current_tvl,
            "vol_24h": vol_24h,
            "vol_tvl_ratio": vol_tvl_ratio,
            "threshold": threshold_pct,
            "token_detail": token_detail,
            "summary": (
                f"💧 流动性骤降风险 "
                f"24h成交/TVL = {vol_tvl_ratio:.0%} "
                f"(阈值 {threshold_pct}%)"
            )
        }
    
    return {
        "triggered": False,
        "current_tvl": current_tvl,
        "vol_24h": vol_24h,
        "vol_tvl_ratio": vol_tvl_ratio,
        "threshold": threshold_pct,
        "reason": f"vol/TVL ratio {vol_tvl_ratio:.0%} below threshold {threshold_pct}%"
    }


def check_buy_sell_ratio(
    token: str,
    chain: str,
    threshold: float = 3.0,
    window_minutes: int = 30
) -> dict:
    """
    检测买卖比异常。
    
    Args:
        threshold: 买卖比阈值（超过此值触发）
        window_minutes: 检测窗口
    
    Returns:
        {"triggered": bool, "details": {...}}
    """
    token_detail = get_token_detail_for_alert(token, chain)
    if not token_detail:
        return {"triggered": False, "error": "Failed to get token detail"}
    
    pair = token_detail.get("main_pair", "")
    txs = get_recent_txs_for_alert(token, chain, pair, limit=50)
    
    if not txs:
        return {"triggered": False, "reason": "No recent txs"}
    
    now_ts = int(time.time())
    window_start = now_ts - (window_minutes * 60)
    
    recent_txs = [tx for tx in txs if tx.get("time", 0) >= window_start]
    
    if len(recent_txs) < 5:
        return {"triggered": False, "reason": "Not enough txs in window"}
    
    # 统计买卖
    buy_volume = 0.0
    sell_volume = 0.0
    buy_count = 0
    sell_count = 0
    
    for tx in recent_txs:
        amount = tx.get("amount_usd", 0)
        from_sym = tx.get("from_token_symbol", "").upper()
        
        if from_sym in ["USDT", "USDC", "BUSD", "DAI", "BNB", "ETH", "WETH"]:
            buy_volume += amount
            buy_count += 1
        else:
            sell_volume += amount
            sell_count += 1
    
    total_volume = buy_volume + sell_volume
    if total_volume == 0:
        return {"triggered": False, "reason": "No volume"}
    
    buy_ratio = buy_volume / total_volume
    sell_ratio = sell_volume / total_volume
    
    # 检查是否超过阈值
    # 例如 threshold=3.0，意味着 buy/sell > 3 或 < 1/3
    ratio = buy_volume / sell_volume if sell_volume > 0 else float('inf')
    
    if ratio > threshold or ratio < 1 / threshold:
        direction = "偏向买入" if ratio > 1 else "偏向卖出"
        return {
            "triggered": True,
            "buy_ratio": buy_ratio,
            "sell_ratio": sell_ratio,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "ratio": ratio,
            "threshold": threshold,
            "token_detail": token_detail,
            "summary": (
                f"⚖️ 买卖比异常 "
                f"买/卖 = {ratio:.1f} ({direction}) "
                f"买单${format_amount(buy_volume)} vs 卖单${format_amount(sell_volume)}"
            )
        }
    
    return {
        "triggered": False,
        "buy_ratio": buy_ratio,
        "sell_ratio": sell_ratio,
        "ratio": ratio,
        "threshold": threshold,
        "reason": f"Ratio {ratio:.1f} within threshold ({threshold})"
    }


def check_whale_accumulation(
    token: str,
    chain: str,
    threshold_usd: float = 10000,
    window_hours: int = 24
) -> dict:
    """
    检测鲸鱼吸筹。
    
    检测 Top Holder 地址的净买入行为。
    
    Returns:
        {"triggered": bool, "details": {...}}
    """
    token_detail = get_token_detail_for_alert(token, chain)
    if not token_detail:
        return {"triggered": False, "error": "Failed to get token detail"}
    
    pair = token_detail.get("main_pair", "")
    holders = get_holders_for_alert(token, chain, limit=20)
    
    if not holders:
        return {"triggered": False, "reason": "No holders data"}
    
    txs = get_recent_txs_for_alert(token, chain, pair, limit=100)
    
    if not txs:
        return {"triggered": False, "reason": "No recent txs"}
    
    # 获取 Top Holder 地址集合
    top_holders = {h["holder"].lower() for h in holders[:5]}
    
    # 统计 Top Holder 的交易
    now_ts = int(time.time())
    window_start = now_ts - (window_hours * 3600)
    
    holder_buys = 0.0
    holder_sells = 0.0
    holder_buy_count = 0
    holder_sell_count = 0
    
    for tx in txs:
        tx_time = tx.get("time", 0)
        if tx_time < window_start:
            continue
        
        sender = tx.get("sender", "").lower()
        if sender not in top_holders:
            continue
        
        amount = tx.get("amount_usd", 0)
        from_sym = tx.get("from_token_symbol", "").upper()
        
        if from_sym in ["USDT", "USDC", "BUSD", "DAI", "BNB", "ETH", "WETH"]:
            holder_buys += amount
            holder_buy_count += 1
        else:
            holder_sells += amount
            holder_sell_count += 1
    
    net_flow = holder_buys - holder_sells
    
    if net_flow > threshold_usd:
        return {
            "triggered": True,
            "holder_buys": holder_buys,
            "holder_sells": holder_sells,
            "net_flow": net_flow,
            "threshold": threshold_usd,
            "buy_count": holder_buy_count,
            "sell_count": holder_sell_count,
            "token_detail": token_detail,
            "summary": (
                f"🐋 鲸鱼吸筹信号 "
                f"净买入 ${format_amount(net_flow)} "
                f"(阈值 ${format_amount(threshold_usd)}) "
                f"24h内 Top Holder 买入{holder_buy_count}笔/卖出{holder_sell_count}笔"
            )
        }
    
    return {
        "triggered": False,
        "holder_buys": holder_buys,
        "holder_sells": holder_sells,
        "net_flow": net_flow,
        "threshold": threshold_usd,
        "reason": f"Net flow ${format_amount(net_flow)} below threshold ${format_amount(threshold_usd)}"
    }


# ============================================================
# 警报检查主流程


def check_price_above(
    token: str,
    chain: str,
    threshold_usd: float
) -> dict:
    """
    检测价格是否向上突破指定阈值。
    """
    token_detail = get_token_detail_for_alert(token, chain)
    if not token_detail:
        return {"triggered": False, "error": "Failed to get token detail"}

    current_price = token_detail.get("current_price_usd", 0)
    triggered = current_price >= threshold_usd

    return {
        "triggered": triggered,
        "current_price": current_price,
        "threshold": threshold_usd,
        "summary": f"价格 ${current_price:.4f} {'>=' if triggered else '<'} 阈值 ${threshold_usd}"
    }


def check_price_below(
    token: str,
    chain: str,
    threshold_usd: float
) -> dict:
    """
    检测价格是否向下突破指定阈值。
    """
    token_detail = get_token_detail_for_alert(token, chain)
    if not token_detail:
        return {"triggered": False, "error": "Failed to get token detail"}

    current_price = token_detail.get("current_price_usd", 0)
    triggered = current_price <= threshold_usd

    return {
        "triggered": triggered,
        "current_price": current_price,
        "threshold": threshold_usd,
        "summary": f"价格 ${current_price:.4f} {'<=' if triggered else '>'} 阈值 ${threshold_usd}"
    }


# ============================================================
# 警报检测函数映射
# ============================================================

CHECKER_MAP = {
    ALERT_TYPE_WHALE_TX: check_whale_tx,
    ALERT_TYPE_PRICE_SPIKE: check_price_spike,
    ALERT_TYPE_LIQUIDITY_DROP: check_liquidity_drop,
    ALERT_TYPE_BUY_SELL_RATIO: check_buy_sell_ratio,
    ALERT_TYPE_WHALE_ACCUMULATION: check_whale_accumulation,
    ALERT_TYPE_PRICE_ABOVE: check_price_above,
    ALERT_TYPE_PRICE_BELOW: check_price_below,
}


def check_alert_rule(rule: dict) -> dict:
    """
    检查单个警报规则是否触发。
    
    Returns:
        {"triggered": bool, "rule": {...}, "result": {...}}
    """
    token = rule.get("token", "")
    chain = rule.get("chain", "")
    alert_type = rule.get("type", "")
    
    if alert_type not in CHECKER_MAP:
        return {
            "triggered": False,
            "rule": rule,
            "result": {},
            "error": f"Unknown alert type: {alert_type}"
        }
    
    checker = CHECKER_MAP[alert_type]
    
    # 根据警报类型调用对应的检测函数
    try:
        if alert_type == ALERT_TYPE_WHALE_TX:
            result = checker(
                token, chain,
                threshold_usd=rule.get("threshold_usd", MIN_WHALE_TX_USD),
                direction=rule.get("direction", "any"),
                window_minutes=60
            )
        elif alert_type == ALERT_TYPE_PRICE_SPIKE:
            result = checker(
                token, chain,
                threshold_pct=rule.get("threshold_pct", 5.0),
                direction=rule.get("direction", "any")
            )
        elif alert_type == ALERT_TYPE_LIQUIDITY_DROP:
            result = checker(
                token, chain,
                threshold_pct=rule.get("threshold_pct", 30.0)
            )
        elif alert_type == ALERT_TYPE_BUY_SELL_RATIO:
            result = checker(
                token, chain,
                threshold=rule.get("threshold_count", 3.0),
                window_minutes=30
            )
        elif alert_type == ALERT_TYPE_WHALE_ACCUMULATION:
            result = checker(
                token, chain,
                threshold_usd=rule.get("threshold_usd", 10000),
                window_hours=24
            )
        elif alert_type == ALERT_TYPE_PRICE_ABOVE:
            result = checker(
                token, chain,
                threshold_usd=rule.get("threshold_usd", 0)
            )
        elif alert_type == ALERT_TYPE_PRICE_BELOW:
            result = checker(
                token, chain,
                threshold_usd=rule.get("threshold_usd", 0)
            )
        else:
            result = {"triggered": False, "error": f"No checker for {alert_type}"}
    except Exception as e:
        result = {"triggered": False, "error": str(e)}
    
    return {
        "triggered": result.get("triggered", False),
        "rule": rule,
        "result": result
    }


def run_alert_check(alert_rule_id: str = None, token: str = None, chain: str = None) -> list:
    """
    运行警报检查。
    
    如果指定了 rule_id，只检查该规则。
    如果指定了 token/chain，检查该代币的所有规则。
    否则检查所有活跃规则。
    
    Returns:
        list of triggered alerts
    """
    sm = get_state_manager()
    
    # 获取要检查的规则
    if alert_rule_id:
        rules = [r for r in sm.get_alert_rules() if r["id"] == alert_rule_id]
    elif token and chain:
        rules = sm.get_alert_rules(token=token, chain=chain, active_only=True)
    else:
        rules = sm.get_alert_rules(active_only=True)
    
    if not rules:
        return []
    
    triggered_alerts = []
    
    for rule in rules:
        # 检查冷却时间
        is_in_cooldown, remaining = sm.check_alert_cooldown(rule["id"])
        if is_in_cooldown:
            continue
        
        # 检查规则
        check_result = check_alert_rule(rule)
        
        if check_result.get("triggered", False):
            # 触发！记录并添加到结果
            sm.trigger_alert(rule["id"])
            
            result = check_result["result"]
            rule_info = check_result["rule"]
            token_detail = result.get("token_detail", {})
            
            alert_info = {
                "rule_id": rule["id"],
                "token": rule["token"],
                "chain": rule["chain"],
                "symbol": token_detail.get("symbol", "?"),
                "alert_type": rule["type"],
                "triggered_at": int(time.time()),
                "summary": result.get("summary", "警报触发"),
                "details": {
                    "threshold": rule.get("threshold_usd", rule.get("threshold_pct", 0)),
                    "cooldown_minutes": rule.get("cooldown_minutes", DEFAULT_COOLDOWN),
                    "trigger_count": rule.get("trigger_count", 0) + 1,
                    "remaining_cooldown_seconds": remaining,
                }
            }
            
            triggered_alerts.append(alert_info)
    
    return triggered_alerts


# ============================================================
# 警报格式化
# ============================================================

def format_alert(alert: dict) -> str:
    """格式化单个警报"""
    symbol = alert.get("symbol", "?")
    token = alert.get("token", "")
    chain = alert.get("chain", "")
    alert_type = alert.get("alert_type", "")
    summary = alert.get("summary", "")
    details = alert.get("details", {})
    rule_id = alert.get("rule_id", "")
    trigger_count = details.get("trigger_count", 1)
    
    lines = [
        f"🚨 警报：{symbol}（{format_address(token)}）",
        f"━━━━━━━━━━━━━━━━━━",
        f"类型：{ALERT_TYPE_LABELS.get(alert_type, alert_type)}",
        f"触发次数：{trigger_count} 次",
        f"",
        f"📋 {summary}",
        f"",
        f"⏱️ 冷却时间：{details.get('cooldown_minutes', DEFAULT_COOLDOWN)} 分钟",
        f"规则ID：{rule_id}",
        f"━━━━━━━━━━━━━━━━━━",
    ]
    
    return "\n".join(lines)


def format_alerts_list(alerts: list) -> str:
    """格式化警报列表"""
    if not alerts:
        return "✅ 暂无触发警报"
    
    lines = [
        f"🚨 触发警报（{len(alerts)} 个）",
        f"━━━━━━━━━━━━━━━━━━",
        ""
    ]
    
    for i, alert in enumerate(alerts, 1):
        lines.append(f"{i}. {format_alert(alert)}")
        lines.append("")
    
    return "\n".join(lines)


ALERT_TYPE_LABELS = {
    ALERT_TYPE_WHALE_TX: "🐋 大额交易",
    ALERT_TYPE_PRICE_SPIKE: "📈 价格异动",
    ALERT_TYPE_LIQUIDITY_DROP: "💧 流动性骤降",
    ALERT_TYPE_BUY_SELL_RATIO: "⚖️ 买卖比异常",
    ALERT_TYPE_WHALE_ACCUMULATION: "🐋 鲸鱼吸筹",
    ALERT_TYPE_NEW_HOLDER_SURGE: "👥 新地址激增",
}


# ============================================================
# 工具函数
# ============================================================

def _safe_float(value, default=0.0):
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value, default=0):
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Ave Guardian — Anomaly Alert Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 anomaly_alert.py                  # 检查所有活跃规则
  python3 anomaly_alert.py list             # 列出所有规则
  python3 anomaly_alert.py check 0xf43c... bsc  # 检查特定代币
  python3 anomaly_alert.py simulate alert_1  # 模拟触发特定规则
        """
    )
    
    parser.add_argument(
        "action",
        nargs="?",
        default="check",
        choices=["check", "list", "simulate", "stats"],
        help="操作：check=检查警报，list=列出规则，simulate=模拟触发，stats=统计"
    )
    parser.add_argument("target", nargs="?", help="规则ID / 代币地址 / token chain")
    parser.add_argument("chain", nargs="?", help="链 ID（当 target 是代币地址时）")
    parser.add_argument("--output", "-o", choices=["full", "compact", "json"], default="full")
    
    args = parser.parse_args()
    
    sm = get_state_manager()
    
    if args.action == "list":
        # 列出所有规则
        rules = sm.get_alert_rules()
        
        if not rules:
            print("❌ 暂无警报规则")
            return
        
        print(f"📋 警报规则（共 {len(rules)} 条）")
        print("━━━━━━━━━━━━━━━━━━")
        
        active = [r for r in rules if r.get("active", True)]
        inactive = [r for r in rules if not r.get("active", True)]
        
        print(f"🟢 活跃：{len(active)} 条")
        print(f"🔴 暂停：{len(inactive)} 条")
        print()
        
        for i, rule in enumerate(rules, 1):
            status = "🟢" if rule.get("active") else "🔴"
            print(f"{i}. {status} [{rule['id']}]")
            print(f"   代币：{format_address(rule['token'])} @ {rule['chain']}")
            print(f"   类型：{ALERT_TYPE_LABELS.get(rule['type'], rule['type'])}")
            print(f"   冷却：{rule.get('cooldown_minutes', DEFAULT_COOLDOWN)} 分钟")
            print(f"   触发：{rule.get('trigger_count', 0)} 次")
            last = rule.get("last_triggered_str")
            print(f"   上次：{last if last else '从未'}")
            print()
        
        # 输出 watchlist
        watchlist = sm.get_watchlist()
        if watchlist:
            print(f"📌 关注列表（共 {len(watchlist)} 个）")
            print("━━━━━━━━━━━━━━━━━━")
            for item in watchlist:
                alert_on = "🔔" if item.get("alert_enabled") else "🔕"
                threshold = item.get("alert_threshold_usd", 5000)
                print(f"  {alert_on} {item.get('symbol', '?')} @ {item.get('chain')} (阈值 ${threshold}+)")
        
        return
    
    elif args.action == "check":
        if args.target:
            # 检查特定代币
            token = args.target
            chain = args.chain or "bsc"
            
            print(f"[Alert Engine] 检查代币: {format_address(token)} @ {chain}", file=sys.stderr)
            
            triggered = run_alert_check(token=token, chain=chain)
            
            if not triggered:
                print("✅ 暂无异常")
            else:
                print(format_alerts_list(triggered))
        else:
            # 检查所有规则
            print(f"[Alert Engine] 检查所有活跃规则...", file=sys.stderr)
            
            triggered = run_alert_check()
            
            if not triggered:
                print("✅ 暂无异常")
            else:
                print(format_alerts_list(triggered))
        
        return
    
    elif args.action == "simulate":
        if not args.target:
            print("❌ 需要指定规则ID", file=sys.stderr)
            sys.exit(1)
        
        rule_id = args.target
        print(f"[Alert Engine] 模拟触发规则: {rule_id}", file=sys.stderr)
        
        # 直接触发规则（不检查条件）
        sm = get_state_manager()
        rules = sm.get_alert_rules()
        
        rule = None
        for r in rules:
            if r["id"] == rule_id:
                rule = r
                break
        
        if not rule:
            print(f"❌ 规则不存在: {rule_id}", file=sys.stderr)
            sys.exit(1)
        
        sm.trigger_alert(rule_id)
        
        print(f"✅ 规则 {rule_id} 已触发")
        print()
        print(format_alert({
            "rule_id": rule_id,
            "token": rule["token"],
            "chain": rule["chain"],
            "symbol": "SIM",
            "alert_type": rule["type"],
            "triggered_at": int(time.time()),
            "summary": "🧪 模拟触发（无实际异常）",
            "details": {
                "threshold": rule.get("threshold_usd", rule.get("threshold_pct", 0)),
                "cooldown_minutes": rule.get("cooldown_minutes", DEFAULT_COOLDOWN),
                "trigger_count": rule.get("trigger_count", 0) + 1,
                "remaining_cooldown_seconds": 0,
            }
        }))
        
        return
    
    elif args.action == "stats":
        # 统计信息
        rules = sm.get_alert_rules()
        stats = sm.get_stats()
        
        print("📊 警报统计")
        print("━━━━━━━━━━━━━━━━━━")
        print(f"总规则数：{len(rules)}")
        print(f"活跃规则：{len([r for r in rules if r.get('active')])}")
        print(f"暂停规则：{len([r for r in rules if not r.get('active')])}")
        print(f"总触发次数：{stats.get('total_alerts_triggered', 0)}")
        print()
        
        # 按类型统计
        type_counts = {}
        for r in rules:
            t = r.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        
        print("按类型分布：")
        for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"  {ALERT_TYPE_LABELS.get(t, t)}: {count}")
        
        return


if __name__ == "__main__":
    main()
