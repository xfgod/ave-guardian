#!/usr/bin/env python3
"""
Ave Guardian — Strategy Executor
自动策略执行引擎。

功能：
1. 自然语言策略解析 → 结构化策略
2. 策略持久化（state_manager）
3. 策略条件检查（定时轮询）
4. 条件触发时执行交易（AVE Trade API）
5. 支持的交易类型：
   - 市价单（market-order）
   - 限价单（limit-order）
   - 止盈止损（TP/SL）

用法：
    python3 strategy_executor.py list
    python3 strategy_executor.py check
    python3 strategy_executor.py check <strategy_id>
    python3 strategy_executor.py arm <token> <chain> <condition> <value>
    python3 strategy_executor.py cancel <strategy_id>
"""

import sys
import os
import json
import argparse
import time
import re
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

# 策略条件类型
CONDITION_PRICE_DROP_PCT = "price_drop_pct"      # 价格下跌百分比
CONDITION_PRICE_RISE_PCT = "price_rise_pct"      # 价格上涨百分比
CONDITION_PRICE_BELOW = "price_below"           # 价格低于
CONDITION_PRICE_ABOVE = "price_above"           # 价格高于

# 策略动作类型
ACTION_BUY = "buy"
ACTION_SELL = "sell"
ACTION_LIMIT_BUY = "limit_buy"
ACTION_LIMIT_SELL = "limit_sell"

# 策略状态
STATUS_ARMED = "armed"       # 待触发
STATUS_TRIGGERED = "triggered"  # 已触发（等待TP/SL执行）
STATUS_COMPLETED = "completed"  # 已完成
STATUS_CANCELLED = "cancelled"  # 已取消
STATUS_FAILED = "failed"        # 执行失败

# 策略操作类型（用于 CLI）
OP_LIST = "list"
OP_CHECK = "check"
OP_ARM = "arm"
OP_CANCEL = "cancel"
OP_TRIGGER = "trigger"

# ============================================================
# 自然语言解析
# ============================================================

def parse_natural_strategy(user_input: str) -> dict:
    """
    解析自然语言策略描述。
    
    支持的语法：
    - "ETH 跌 5% 买 0.5 个"
    - "BTC 跌 5% 买 0.01，涨 10% 卖"
    - "ETH 跌破 3000 就卖"
    - "PEPE 涨到 0.001 卖出一半"
    - "设置止损 跌 10% 卖"
    
    Returns:
        {
            "token": str,
            "chain": str,
            "symbol": str,
            "condition": str,
            "condition_value": float,
            "action": str,
            "action_amount_usd": float,
            "action_amount_token": float,
            "tp_pct": float,  # 止盈百分比
            "sl_pct": float,   # 止损百分比
            "raw": str
        }
        或 {"error": str}
    """
    result = {
        "token": "",
        "chain": "bsc",
        "symbol": "",
        "condition": "",
        "condition_value": 0.0,
        "condition_unit": "pct",
        "action": ACTION_BUY,
        "action_amount_usd": 0.0,
        "action_amount_token": 0.0,
        "tp_pct": 0.0,
        "sl_pct": 0.0,
        "raw": user_input,
    }
    
    text = user_input.strip()
    
    # ---------- 1. 解析代币 ----------
    # 常见代币符号
    KNOWN_SYMBOLS = {
        "BTC": {"token": "", "chain": "bsc"},  # 需要通过搜索获取 CA
        "ETH": {"token": "", "chain": "bsc"},
        "BNB": {"token": "0xbb4CdB79CB9bfCd050e4ab1AD21F02f573b8b1A", "chain": "bsc"},
        "PEPE": {"token": "", "chain": "bsc"},  # 需要搜索
        "SOL": {"token": "", "chain": "solana"},
        "USDT": {"token": "0x55d398326f99059fF775485246999027B3197955", "chain": "bsc"},
        "USDC": {"token": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", "chain": "bsc"},
    }
    
    symbol_found = None
    for sym in sorted(KNOWN_SYMBOLS.keys(), key=lambda x: -len(x)):
        if re.search(rf'\b{sym}\b', text, re.IGNORECASE):
            symbol_found = sym
            break
    
    if not symbol_found:
        return {"error": f"无法识别代币符号: {text[:50]}"}
    
    result["symbol"] = symbol_found
    token_info = KNOWN_SYMBOLS[symbol_found]
    
    if token_info["token"]:
        result["token"] = token_info["token"]
    result["chain"] = token_info["chain"]
    
    # 如果没有 CA，搜索获取
    if not result["token"]:
        search_result = run_ave_rest(
            "search",
            "--keyword", symbol_found,
            "--chain", result["chain"],
            "--limit", "1"
        )
        if "error" not in search_result:
            data = search_result.get("data", [])
            if isinstance(data, dict):
                data = data.get("data", [])
            if isinstance(data, list) and len(data) > 0:
                result["token"] = data[0].get("token", "")
    
    if not result["token"]:
        return {"error": f"无法找到 {symbol_found} 的合约地址"}
    
    # ---------- 2. 解析条件 ----------
    # 价格下跌: "跌 X%" / "drop X%" / "下跌 X%"
    drop_match = re.search(r'(\w+)\s*[跌掉下降]\s*(\d+(?:\.\d+)?)\s*%', text)
    if drop_match:
        result["condition"] = CONDITION_PRICE_DROP_PCT
        result["condition_value"] = float(drop_match.group(2))
        result["condition_unit"] = "pct"
    
    # 价格上涨: "涨 X%" / "rise X%" / "上涨 X%"
    if not result["condition"]:
        rise_match = re.search(r'(\w+)\s*[涨升上]\s*(\d+(?:\.\d+)?)\s*%', text)
        if rise_match:
            result["condition"] = CONDITION_PRICE_RISE_PCT
            result["condition_value"] = float(rise_match.group(2))
            result["condition_unit"] = "pct"
    
    # 价格低于: "跌破 X" / "price below X"
    if not result["condition"]:
        below_match = re.search(r'[跌破下低至到]\s*\$?\s*([\d.]+)', text)
        if below_match:
            result["condition"] = CONDITION_PRICE_BELOW
            result["condition_value"] = float(below_match.group(1))
            result["condition_unit"] = "usd"
    
    # 价格高于: "涨破 X" / "price above X"
    if not result["condition"]:
        above_match = re.search(r'[涨升上破过]\s*\$?\s*([\d.]+)', text)
        if above_match:
            result["condition"] = CONDITION_PRICE_ABOVE
            result["condition_value"] = float(above_match.group(1))
            result["condition_unit"] = "usd"
    
    if not result["condition"]:
        return {"error": f"无法识别触发条件: {text[:50]}"}
    
    # ---------- 3. 解析动作 ----------
    # 买: "买 X 个" — 匹配"买"后面的数字
    buy_match = re.search(r'买\s*(\d+(?:\.\d+)?)\s*(?:个|枚|U|USDT|美元)?', text)
    if buy_match:
        amount = float(buy_match.group(1))
        result["action"] = ACTION_BUY
        # 如果代币是 USDT/USDC 等稳定币，数量就是 USD 金额
        if symbol_found in ["USDT", "USDC", "BUSD"]:
            result["action_amount_usd"] = amount
        else:
            result["action_amount_token"] = amount
    else:
        # 卖: "卖 X 个"
        sell_match = re.search(r'卖\s*(\d+(?:\.\d+)?)\s*(?:个|枚)?', text)
        if sell_match:
            amount = float(sell_match.group(1))
            result["action"] = ACTION_SELL
            result["action_amount_token"] = amount
        else:
            # "卖一半" / "sell half" / "半仓"
            if re.search(r'卖?一半|半仓|50%', text):
                result["action"] = ACTION_SELL
                result["action_amount_token"] = 0.5  # 50%
    
    # ---------- 4. 解析止盈止损 ----------
    # 止盈: "涨 X% 卖" / "止盈 X%"
    tp_match = re.search(r'[止盈TP]\s*[涨升]?\s*(\d+(?:\.\d+)?)\s*%', text)
    if tp_match:
        result["tp_pct"] = float(tp_match.group(1))
    
    # 止损: "跌 X% 止损" / "SL X%"
    sl_match = re.search(r'[止损SL]\s*[跌]?\s*(\d+(?:\.\d+)?)\s*%', text)
    if sl_match:
        result["sl_pct"] = float(sl_match.group(1))
    
    # 如果条件本身就是"涨"，动作就是卖（止盈方向）
    if result["condition"] == CONDITION_PRICE_RISE_PCT:
        if result["action"] == ACTION_BUY:
            # "涨 X% 买" 不合理，改为"涨 X% 卖"
            result["action"] = ACTION_SELL
    
    # ---------- 5. 验证 ----------
    if not result["token"]:
        return {"error": "无法确定代币地址"}
    
    if not result["condition"]:
        return {"error": "无法确定触发条件"}
    
    return result


def resolve_strategy_amounts(strategy: dict, current_price: float) -> dict:
    """
    根据当前价格解析策略中的数量。
    
    将 action_amount_token 转换为 action_amount_usd，
    或者将 action_amount_usd 转换为 action_amount_token。
    """
    result = {**strategy}
    
    if strategy.get("action_amount_token") and strategy.get("action_amount_token", 0) > 0:
        # 有 token 数量，计算 USD
        amount = strategy["action_amount_token"]
        if amount <= 1.0:
            # 小于等于1，可能是比例（0.5 = 一半）
            if amount <= 1.0:
                result["action_amount_token_resolved"] = amount
        else:
            result["action_amount_token_resolved"] = amount
        
        result["action_amount_usd_resolved"] = amount * current_price
    
    elif strategy.get("action_amount_usd") and strategy.get("action_amount_usd", 0) > 0:
        # 有 USD 数量
        amount_usd = strategy["action_amount_usd"]
        result["action_amount_usd_resolved"] = amount_usd
        result["action_amount_token_resolved"] = amount_usd / current_price if current_price > 0 else 0
    
    return result


# ============================================================
# 条件检查
# ============================================================

def get_current_price(token: str, chain: str) -> tuple:
    """
    获取代币当前价格。
    
    Returns:
        (price_usd, error)
    """
    result = run_ave_rest("token", "--address", token, "--chain", chain)
    
    if "error" in result:
        return 0.0, result["error"]
    
    data = result.get("data", {})
    if isinstance(data, dict):
        data = data.get("token", data)
    
    price = _safe_float(data.get("current_price_usd", 0))
    return price, None


def check_strategy_condition(strategy: dict, current_price: float) -> tuple:
    """
    检查策略条件是否满足。
    
    Returns:
        (triggered: bool, reason: str)
    """
    condition = strategy.get("condition", "")
    condition_value = strategy.get("condition_value", 0.0)
    condition_unit = strategy.get("condition_unit", "pct")
    
    if current_price <= 0:
        return False, "无法获取当前价格"
    
    if condition == CONDITION_PRICE_DROP_PCT:
        # 需要知道基准价格（入场价）
        # 如果策略没有记录入场价，用当前价的 (1 - value%) 计算参考
        reference_price = strategy.get("reference_price", current_price * (1 + condition_value / 100))
        drop_pct = (reference_price - current_price) / reference_price * 100
        
        if drop_pct >= condition_value:
            return True, f"价格下跌 {drop_pct:.2f}%（触发条件 {condition_value}%）"
        else:
            return False, f"价格下跌 {drop_pct:.2f}%（条件 {condition_value}%）"
    
    elif condition == CONDITION_PRICE_RISE_PCT:
        reference_price = strategy.get("reference_price", current_price * (1 - condition_value / 100))
        rise_pct = (current_price - reference_price) / reference_price * 100
        
        if rise_pct >= condition_value:
            return True, f"价格上涨 {rise_pct:.2f}%（触发条件 {condition_value}%）"
        else:
            return False, f"价格上涨 {rise_pct:.2f}%（条件 {condition_value}%）"
    
    elif condition == CONDITION_PRICE_BELOW:
        if current_price <= condition_value:
            return True, f"价格 ${format_price(current_price)} <= ${format_price(condition_value)}"
        else:
            return False, f"价格 ${format_price(current_price)} > ${format_price(condition_value)}"
    
    elif condition == CONDITION_PRICE_ABOVE:
        if current_price >= condition_value:
            return True, f"价格 ${format_price(current_price)} >= ${format_price(condition_value)}"
        else:
            return False, f"价格 ${format_price(current_price)} < ${format_price(condition_value)}"
    
    return False, "未知条件类型"


# ============================================================
# 策略管理
# ============================================================

def list_strategies(status: str = None) -> list:
    """列出策略"""
    sm = get_state_manager()
    return sm.get_strategies(status=status)


def arm_strategy(
    token: str,
    chain: str,
    symbol: str,
    condition: str,
    condition_value: float,
    action: str = ACTION_BUY,
    action_amount_usd: float = 0.0,
    action_amount_token: float = 0.0,
    tp_pct: float = 0.0,
    sl_pct: float = 0.0,
    tp_price: float = 0.0,
    sl_price: float = 0.0,
    note: str = ""
) -> dict:
    """
    创建并激活策略。
    """
    sm = get_state_manager()
    
    # 获取当前价格作为参考价
    current_price, _ = get_current_price(token, chain)
    
    strategy = sm.add_strategy(
        token=token,
        chain=chain,
        symbol=symbol,
        condition=condition,
        condition_value=condition_value,
        condition_unit="pct" if "pct" in condition else "usd",
        action=action,
        action_amount_usd=action_amount_usd,
        action_amount_token=action_amount_token,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        tp_price=tp_price,
        sl_price=sl_price,
        note=note
    )
    
    # 保存参考价格
    if current_price > 0:
        sm.update_strategy(strategy["id"], {
            "reference_price": current_price,
            "reference_price_str": f"${format_price(current_price)}"
        })
    
    return strategy


def cancel_strategy(strategy_id: str) -> bool:
    """取消策略"""
    sm = get_state_manager()
    return sm.update_strategy(strategy_id, {"status": STATUS_CANCELLED})


def delete_strategy(strategy_id: str) -> bool:
    """删除策略"""
    sm = get_state_manager()
    return sm.remove_strategy(strategy_id)


# ============================================================
# 策略检查（定时任务调用）
# ============================================================

def check_armed_strategies() -> list:
    """
    检查所有 armed 状态策略。
    
    Returns:
        list of triggered strategies with details
    """
    sm = get_state_manager()
    strategies = sm.get_strategies(status=STATUS_ARMED)
    
    triggered = []
    
    for strategy in strategies:
        token = strategy.get("token", "")
        chain = strategy.get("chain", "")
        
        if not token or not chain:
            continue
        
        # 获取当前价格
        current_price, err = get_current_price(token, chain)
        if err or current_price <= 0:
            # 更新检查时间
            sm.update_strategy(strategy["id"], {
                "last_check_at": int(time.time()),
                "last_check_error": err
            })
            continue
        
        # 检查条件
        is_triggered, reason = check_strategy_condition(strategy, current_price)
        
        # 更新检查时间
        sm.update_strategy(strategy["id"], {
            "last_check_at": int(time.time()),
            "last_check_price": current_price,
            "last_check_reason": reason
        })
        
        if is_triggered:
            # 触发！
            sm.update_strategy(strategy["id"], {
                "status": STATUS_TRIGGERED,
                "triggered_at": int(time.time()),
                "triggered_at_str": datetime.now(timezone.utc).isoformat(),
                "triggered_price": current_price,
            })
            
            triggered.append({
                "strategy": strategy,
                "triggered_price": current_price,
                "reason": reason
            })
    
    return triggered


def format_strategy_card(strategy: dict, include_history: bool = False) -> str:
    """格式化策略信息"""
    symbol = strategy.get("symbol", "?")
    token = strategy.get("token", "")
    chain = strategy.get("chain", "")
    status = strategy.get("status", "?")
    condition = strategy.get("condition", "")
    condition_value = strategy.get("condition_value", 0)
    condition_unit = strategy.get("condition_unit", "pct")
    action = strategy.get("action", "")
    tp_pct = strategy.get("tp_pct", 0)
    sl_pct = strategy.get("sl_pct", 0)
    created = strategy.get("created_at_str", "?")
    
    status_icon = {
        STATUS_ARMED: "🟢",
        STATUS_TRIGGERED: "🟠",
        STATUS_COMPLETED: "✅",
        STATUS_CANCELLED: "❌",
        STATUS_FAILED: "🚨",
    }.get(status, "⚪")
    
    condition_str = {
        CONDITION_PRICE_DROP_PCT: f"跌 {condition_value}%",
        CONDITION_PRICE_RISE_PCT: f"涨 {condition_value}%",
        CONDITION_PRICE_BELOW: f"低于 ${format_price(condition_value)}",
        CONDITION_PRICE_ABOVE: f"高于 ${format_price(condition_value)}",
    }.get(condition, f"{condition} {condition_value}")
    
    action_str = {
        ACTION_BUY: "买入",
        ACTION_SELL: "卖出",
        ACTION_LIMIT_BUY: "限价买入",
        ACTION_LIMIT_SELL: "限价卖出",
    }.get(action, action)
    
    lines = [
        f"{status_icon} {symbol}（{format_address(token)}）@ {chain.upper()}",
        f"   条件：{condition_str}",
        f"   动作：{action_str}",
    ]
    
    if tp_pct > 0:
        lines.append(f"   止盈：+{tp_pct}%")
    if sl_pct > 0:
        lines.append(f"   止损：-{sl_pct}%")
    
    lines.append(f"   状态：{status} | 创建：{created}")
    
    if include_history:
        triggered_at = strategy.get("triggered_at_str")
        completed_at = strategy.get("completed_at_str")
        if triggered_at:
            lines.append(f"   触发时间：{triggered_at}")
        if completed_at:
            lines.append(f"   完成时间：{completed_at}")
        
        ref_price = strategy.get("reference_price_str")
        if ref_price:
            lines.append(f"   参考价：{ref_price}")
        
        trigger_price = strategy.get("triggered_price")
        if trigger_price:
            lines.append(f"   触发价格：${format_price(trigger_price)}")
    
    lines.append(f"   ID：{strategy.get('id', '')}")
    
    return "\n".join(lines)


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


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Ave Guardian — Strategy Executor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 列出所有策略
  python3 strategy_executor.py list
  
  # 列出活跃策略
  python3 strategy_executor.py list --status armed
  
  # 解析自然语言策略
  python3 strategy_executor.py parse "ETH 跌 5% 买 0.5，涨 10% 卖"
  
  # 创建策略
  python3 strategy_executor.py arm 0xf43c... bsc price_drop_pct 5 --action buy --amount-usd 50 --tp 10 --sl 3
  
  # 检查所有策略（定时任务调用）
  python3 strategy_executor.py check
  
  # 触发策略
  python3 strategy_executor.py trigger <strategy_id>
  
  # 取消策略
  python3 strategy_executor.py cancel <strategy_id>
  
  # 删除策略
  python3 strategy_executor.py delete <strategy_id>
        """
    )
    
    parser.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["list", "check", "arm", "cancel", "delete", "trigger", "parse"],
        help="操作"
    )
    parser.add_argument("target", nargs="*", help="参数")
    parser.add_argument("--status", help="按状态过滤")
    parser.add_argument("--output", "-o", choices=["full", "compact", "json"], default="full")
    
    args = parser.parse_args()
    
    sm = get_state_manager()
    
    # ========== list ==========
    if args.action == "list":
        status = args.status
        strategies = sm.get_strategies(status=status) if status else sm.get_strategies()
        
        if not strategies:
            print("❌ 暂无策略")
            return
        
        print(f"📋 策略列表（共 {len(strategies)} 条）")
        print("━━━━━━━━━━━━━━━━━━")
        
        # 按状态分组
        by_status = {}
        for s in strategies:
            st = s.get("status", "?")
            if st not in by_status:
                by_status[st] = []
            by_status[st].append(s)
        
        for st, items in by_status.items():
            status_icon = {
                STATUS_ARMED: "🟢 活跃",
                STATUS_TRIGGERED: "🟠 待执行",
                STATUS_COMPLETED: "✅ 已完成",
                STATUS_CANCELLED: "❌ 已取消",
                STATUS_FAILED: "🚨 失败",
            }.get(st, f"⚪ {st}")
            
            print(f"\n{status_icon}（{len(items)} 条）")
            
            for s in items:
                print(format_strategy_card(s))
                print()
        
        return
    
    # ========== parse ==========
    elif args.action == "parse":
        if not args.target:
            print("❌ 需要输入策略描述", file=sys.stderr)
            sys.exit(1)
        
        user_input = " ".join(args.target)
        result = parse_natural_strategy(user_input)
        
        if "error" in result:
            print(f"❌ 解析失败: {result['error']}")
            return
        
        print(f"✅ 解析结果：")
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return
    
    # ========== arm ==========
    elif args.action == "arm":
        # arm 命令格式: arm <token> <chain> <condition> <value> [key=value ...]
        # 示例: arm 0x... bsc price_drop_pct 5 action=buy amount_usd=50 tp=15 sl=5
        raw_args = args.target if args.target else []
        
        if len(raw_args) < 4:
            print("❌ 参数不足")
            print("用法: arm <token> <chain> <condition> <value> [key=value ...]")
            print("示例: arm 0xf43c... bsc price_drop_pct 5 action=buy amount_usd=50 tp=15 sl=5")
            print()
            print("condition 选项: price_drop_pct | price_rise_pct | price_below | price_above")
            print("action 选项: buy | sell")
            sys.exit(1)
        
        token = raw_args[0]
        chain = raw_args[1]
        condition = raw_args[2]
        try:
            condition_value = float(raw_args[3])
        except ValueError:
            print(f"❌ 无效的 condition_value: {raw_args[3]}")
            sys.exit(1)
        
        # 解析 key=value 格式的额外参数
        action = ACTION_BUY
        action_amount_usd = 0.0
        tp_pct = 0.0
        sl_pct = 0.0
        
        for arg in raw_args[4:]:
            if "=" in arg:
                key, value = arg.split("=", 1)
                key = key.strip()
                value = value.strip()
                try:
                    if key == "action":
                        action = value
                    elif key == "amount_usd":
                        action_amount_usd = float(value)
                    elif key == "tp":
                        tp_pct = float(value)
                    elif key == "sl":
                        sl_pct = float(value)
                except ValueError:
                    pass
        
        # 尝试从 token 获取 symbol
        symbol = "?"
        if token.startswith("0x"):
            detail_result = run_ave_rest("token", "--address", token, "--chain", chain)
            if "error" not in detail_result:
                data = detail_result.get("data", {})
                if isinstance(data, dict):
                    data = data.get("token", data)
                symbol = data.get("symbol", "?")
        
        strategy = arm_strategy(
            token=token,
            chain=chain,
            symbol=symbol,
            condition=condition,
            condition_value=condition_value,
            action=action,
            action_amount_usd=action_amount_usd,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            note=""
        )
        
        print(f"✅ 策略已创建：")
        print(format_strategy_card(strategy))
        return
    
    # ========== check ==========
    elif args.action == "check":
        if args.target:
            # 检查单个策略
            strategy_id = args.target[0]
            strategies = sm.get_strategies()
            strategy = None
            for s in strategies:
                if s.get("id") == strategy_id:
                    strategy = s
                    break
            
            if not strategy:
                print(f"❌ 策略不存在: {strategy_id}")
                return
            
            token = strategy.get("token", "")
            chain = strategy.get("chain", "")
            current_price, err = get_current_price(token, chain)
            
            if err:
                print(f"❌ 获取价格失败: {err}")
                return
            
            triggered, reason = check_strategy_condition(strategy, current_price)
            
            print(f"📋 策略检查：{strategy.get('symbol', '?')}")
            print(f"   当前价格：${format_price(current_price)}")
            print(f"   条件：{strategy.get('condition')} {strategy.get('condition_value')}")
            print(f"   状态：{'✅ 触发' if triggered else '❌ 未触发'}")
            print(f"   原因：{reason}")
        else:
            # 检查所有 armed 策略
            print(f"[Strategy Executor] 检查所有活跃策略...", file=sys.stderr)
            triggered = check_armed_strategies()
            
            if not triggered:
                print("✅ 暂无策略触发")
            else:
                print(f"🚨 触发 {len(triggered)} 个策略：")
                print("━━━━━━━━━━━━━━━━━━")
                for item in triggered:
                    s = item["strategy"]
                    print(format_strategy_card(s, include_history=True))
                    print(f"   触发原因：{item['reason']}")
                    print()
        return
    
    # ========== cancel ==========
    elif args.action == "cancel":
        if not args.target:
            print("❌ 需要指定策略ID", file=sys.stderr)
            sys.exit(1)
        
        strategy_id = args.target[0]
        result = cancel_strategy(strategy_id)
        
        if result:
            print(f"✅ 策略已取消: {strategy_id}")
        else:
            print(f"❌ 策略不存在或取消失败: {strategy_id}")
        return
    
    # ========== delete ==========
    elif args.action == "delete":
        if not args.target:
            print("❌ 需要指定策略ID", file=sys.stderr)
            sys.exit(1)
        
        strategy_id = args.target[0]
        result = delete_strategy(strategy_id)
        
        if result:
            print(f"✅ 策略已删除: {strategy_id}")
        else:
            print(f"❌ 策略不存在或删除失败: {strategy_id}")
        return
    
    # ========== trigger ==========
    elif args.action == "trigger":
        if not args.target:
            print("❌ 需要指定策略ID", file=sys.stderr)
            sys.exit(1)
        
        strategy_id = args.target[0]
        strategies = sm.get_strategies()
        strategy = None
        for s in strategies:
            if s.get("id") == strategy_id:
                strategy = s
                break
        
        if not strategy:
            print(f"❌ 策略不存在: {strategy_id}")
            return
        
        # 手动触发（用于测试）
        sm.update_strategy(strategy_id, {
            "status": STATUS_TRIGGERED,
            "triggered_at": int(time.time()),
            "triggered_at_str": datetime.now(timezone.utc).isoformat(),
            "triggered_price": 0.0,
        })
        
        print(f"✅ 策略已触发: {strategy_id}")
        print(format_strategy_card(sm.get_strategies(token=strategy.get("token"))[0], include_history=True))
        return
    
    else:
        print(f"❌ 未知操作: {args.action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
