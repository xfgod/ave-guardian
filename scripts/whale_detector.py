#!/usr/bin/env python3
"""
Ave Guardian — Whale & Manipulator Detector
庄家行为识别引擎。

三层分析算法：
1. 持仓集中度分析 — Top10 持仓占比，判断筹码分布
2. 交易行为模式识别 — 大户 swap 记录聚类，判断吸筹/拉升/出货
3. K线形态辅助判断 — 量价关系，识别典型庄家形态

输出：结构化分析报告 + 自然语言结论

用法：
    python3 whale_detector.py <CA> <chain> [--window-hours 24]
    python3 whale_detector.py 0xf43c8f27754829202d2f66650eb3f6d168c288dc bsc
    python3 whale_detector.py 0xf43c8f27754829202d2f66650eb3f6d168c288dc bsc --window-hours 48
"""

import sys
import os
import json
import argparse
import statistics
from datetime import datetime, timezone
from typing import Optional

# 添加父目录到路径以便导入 utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import utils
from utils import (
    format_price,
    format_amount,
    format_pct,
    format_address,
    ts_to_str,
    concentration_score,
    run_ave_rest,
)

# ============================================================
# 常量定义
# ============================================================

# 分析窗口期默认值（小时）
DEFAULT_WINDOW_HOURS = 24

# 主导地址最小交易量（USD），低于此值不计入大户分析
MIN_DOMINANT_TX_USD = 5000

# 大户地址数量阈值
TOP_ANALYSIS_ADDR_COUNT = 5

# ============================================================
# 数据获取
# ============================================================

def get_token_detail(token: str, chain: str) -> dict:
    """
    获取代币基本信息。
    
    Returns:
        dict 包含 price/market_cap/TVL/volume/holders 等
    """
    result = run_ave_rest("token", "--address", token, "--chain", chain)
    
    if "error" in result:
        return {"error": result["error"]}
    
    # API 返回结构：result["data"]["token"]
    data = result.get("data", {})
    if isinstance(data, dict):
        data = data.get("token", data)
    
    # 提取需要的字段
    return {
        "name": data.get("name", "Unknown"),
        "symbol": data.get("symbol", "UNKNOWN"),
        "token": data.get("token", token),
        "chain": data.get("chain", chain),
        "current_price_usd": _safe_float(data.get("current_price_usd")),
        "current_price_eth": _safe_float(data.get("current_price_eth")),
        "price_change_5m": _safe_float(data.get("token_price_change_5m", data.get("price_change_5m"))),
        "price_change_1h": _safe_float(data.get("token_price_change_1h", data.get("price_change_1h"))),
        "price_change_24h": _safe_float(data.get("token_price_change_24h", data.get("price_change_24h"))),
        "market_cap": _safe_float(data.get("market_cap")),
        "fdv": _safe_float(data.get("fdv")),
        "tvl": _safe_float(data.get("tvl")),
        "tx_volume_u_24h": _safe_float(data.get("tx_volume_u_24h")),
        "tx_count_24h": data.get("tx_count_24h", 0),
        "holders": data.get("holders", 0),
        "total_supply": data.get("total", "0"),
        "main_pair": data.get("main_pair", ""),
        "risk_level": data.get("risk_level", "UNKNOWN"),
        "updated_at": data.get("updated_at"),
    }


def get_holders(token: str, chain: str, limit: int = 100) -> list:
    """
    获取代币持仓分布。
    
    Returns:
        list of {address, balance, percent, buy_tx, sell_tx}
    """
    result = run_ave_rest(
        "holders",
        "--address", token,
        "--chain", chain,
        "--limit", str(limit),
        "--sort-by", "balance"
    )
    
    if "error" in result:
        return []
    
    # API 返回结构：result["data"] 是列表
    # 字段：holder, balance_ratio, balance_usd, amount_cur, buy/sell 相关字段
    raw_data = result.get("data", [])
    if isinstance(raw_data, dict):
        raw_data = raw_data.get("holders", [])
    if not isinstance(raw_data, list):
        raw_data = []
    
    holders = []
    for h in raw_data:
        # balance_ratio 是 0~1 的比例，需要转成百分比（0~100）
        balance_ratio = _safe_float(h.get("balance_ratio", 0))
        
        holders.append({
            "address": h.get("holder", h.get("address", "")),
            "balance": _safe_float(h.get("balance_usd", h.get("amount_cur", 0))),
            "percent": balance_ratio * 100,  # 转为百分比 0~100
            "balance_ratio": balance_ratio,   # 保留原始比例
            "buy_tx": _safe_float(h.get("buy_tx_count_cur", 0)),
            "sell_tx": _safe_float(h.get("sell_tx_count_cur", 0)),
            "buy_volume_usd": _safe_float(h.get("total_transfer_in_usd", 0)),
            "sell_volume_usd": _safe_float(h.get("total_transfer_out_usd", 0)),
            "is_contract": h.get("new_tags") is not None,
        })
    
    return holders


def get_recent_txs(token: str, chain: str, pair: str, limit: int = 100, window_hours: int = 24) -> list:
    """
    获取最近的 swap 交易记录。
    
    Returns:
        list of {time, tx_hash, side, amount_usd, price, sender}
    """
    if not pair:
        # 如果没有 pair，先搜索
        token_result = run_ave_rest("token", "--address", token, "--chain", chain)
        data = token_result.get("data", {})
        if isinstance(data, dict):
            data = data.get("token", data)
        pair = data.get("main_pair", "")
    
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
    
    # API 返回结构：result["data"]["txs"]
    raw_data = result.get("data", {})
    if isinstance(raw_data, dict):
        raw_data = raw_data.get("txs", [])
    if not isinstance(raw_data, list):
        raw_data = []
    
    # 计算时间窗口边界
    now_ts = int(datetime.now(timezone.utc).timestamp())
    window_start = now_ts - (window_hours * 3600)
    
    txs = []
    for tx in raw_data:
        tx_time = _safe_int(tx.get("tx_time"))
        if tx_time and tx_time < window_start:
            continue  # 跳过窗口外的交易
        
        # 判断买卖方向
        from_token = tx.get("from_token_symbol", "")
        to_token = tx.get("to_token_symbol", "")
        # 如果 to_token 不是稳定币或主流币，认为是卖入（买入了这个代币）
        # 简化处理：如果有 from_token_amount 和 to_token_amount，看哪个更小
        side = "unknown"
        amount_usd = _safe_float(tx.get("amount_usd", 0))
        
        # 根据 sender_address 是否在 to_address 中判断买卖
        # 更准确的方式是看代币流向，但这里简化处理
        txs.append({
            "time": tx_time,
            "tx_hash": tx.get("tx_hash", ""),
            "side": side,  # 暂时留空，后面再判断
            "amount_usd": amount_usd,
            "price": _safe_float(tx.get("from_token_price_usd", 0)),
            "sender": tx.get("sender_address", tx.get("wallet_address", "")),
            "token_amount": tx.get("from_token_amount", ""),
        })
    
    # 尝试根据代币方向判断买卖
    # 如果 to_token 是目标 token，认为是 buy
    for tx in txs:
        if tx["price"] > 0 and tx["amount_usd"] > 0:
            # 通过金额估算方向（简化）
            # 如果 from_token 是稳定币，认为是 buy
            from_sym = tx.get("token_amount", "")
            if from_sym.lower() in ["usdt", "usdc", "bnb", "eth", "weth"]:
                tx["side"] = "buy"
            else:
                tx["side"] = "sell"
    
    return txs


def get_klines(token: str, chain: str, interval: int = 60, limit: int = 24) -> list:
    """
    获取 K 线数据。
    
    Args:
        interval: K 线周期（分钟），默认 60 = 1h
        limit: K 线根数
    
    Returns:
        list of {time, open, high, low, close, volume}
    """
    result = run_ave_rest(
        "kline-token",
        "--address", token,
        "--chain", chain,
        "--interval", str(interval),
        "--size", str(limit)
    )
    
    if "error" in result:
        return []
    
    # API 返回结构：result["data"]["points"]
    raw_data = result.get("data", {})
    if isinstance(raw_data, dict):
        raw_data = raw_data.get("points", [])
    if not isinstance(raw_data, list):
        raw_data = []
    
    klines = []
    for k in raw_data:
        klines.append({
            "time": _safe_int(k.get("time")),
            "open": _safe_float(k.get("open")),
            "high": _safe_float(k.get("high")),
            "low": _safe_float(k.get("low")),
            "close": _safe_float(k.get("close")),
            "volume": _safe_float(k.get("volume")),
        })
    
    return klines


# ============================================================
# 第一层：持仓集中度分析
# ============================================================

def analyze_concentration(holders: list, token_detail: dict = None) -> dict:
    """
    分析持仓集中度。
    
    算法：
    1. 计算 Top5 / Top10 / Top20 持仓占比
    2. 检测 CEX 充币地址（通常是已知交易所地址）
    3. 计算集中度评分（0~100）
    4. 判断风险等级
    
    Returns:
        {
            "top5_pct": float,
            "top10_pct": float,
            "top20_pct": float,
            "score": float,
            "level": str,
            "top_addresses": [...],
            "cex_risk": float,
            "description": str
        }
    """
    if not holders:
        return {
            "top5_pct": 0,
            "top10_pct": 0,
            "top20_pct": 0,
            "score": 0,
            "level": "⚪ 数据不足",
            "top_addresses": [],
            "cex_risk": 0,
            "description": "无法获取持仓数据"
        }
    
    # 计算各层占比
    top5_pct = sum(h.get("percent", 0) for h in holders[:5]) / 100
    top10_pct = sum(h.get("percent", 0) for h in holders[:10]) / 100
    top20_pct = sum(h.get("percent", 0) for h in holders[:20]) / 100
    
    # 集中度评分（0~100）
    # 映射：10% → 0分，90% → 100分
    raw_score = (top10_pct - 0.10) / 0.80 * 100
    score = max(0, min(100, raw_score))
    
    # 风险等级
    if top10_pct > 0.70:
        level = "🔴 极高控盘"
        description = f"Top10 持仓 {top10_pct*100:.1f}%，筹码高度集中，单一大户即可影响价格"
    elif top10_pct > 0.50:
        level = "🟠 高控盘"
        description = f"Top10 持仓 {top10_pct*100:.1f}%，存在明显控盘迹象，2~3 个大户可联合操纵价格"
    elif top10_pct > 0.30:
        level = "🟡 中等集中"
        description = f"Top10 持仓 {top10_pct*100:.1f}%，有一定集中度，需关注大户动向"
    else:
        level = "🟢 持仓分散"
        description = f"Top10 持仓 {top10_pct*100:.1f}%，无明显单一控盘方"
    
    # Top 地址列表（用于行为分析）
    top_addresses = holders[:TOP_ANALYSIS_ADDR_COUNT]
    
    # CEX 风险检测（简化版：检测是否是已知的 CEX 相关模式）
    # 注意：这里只是粗略判断，更精确的需要地址标签库
    cex_risk = 0
    cex_addresses = []
    for h in holders[:10]:
        addr = h.get("address", "").lower()
        # 常见 CEX 地址特征（简化判断）
        # 实际上需要真实的 CEX 地址标签库
        if any(cex in addr for cex in ["0x3f5ce5fbfe3e9af3971dd833d26ba9b5",  # Binance Hot Wallet
                                          "0x28c6c06298d514db089934071355e5743bf21d60",  # Binance 8
                                          "0x21a31ee1afc51d94c2efccaa2092ad1028285549"]):  # Binance 18
            cex_risk += 0.2
            cex_addresses.append(format_address(h.get("address", "")))
    
    cex_risk = min(1.0, cex_risk)
    
    # 如果有 CEX 地址，description 需要补充
    if cex_addresses:
        description += f"\n⚠️ 检测到 CEX 充币地址：{', '.join(cex_addresses)}，存在潜在抛压"
    
    return {
        "top5_pct": round(top5_pct * 100, 2),
        "top10_pct": round(top10_pct * 100, 2),
        "top20_pct": round(top20_pct * 100, 2),
        "score": round(score, 1),
        "level": level,
        "top_addresses": top_addresses,
        "cex_risk": cex_risk,
        "cex_addresses": cex_addresses,
        "description": description,
        "holders_count": len(holders)
    }


# ============================================================
# 第二层：交易行为模式识别
# ============================================================

def analyze_behavior(txs: list, holders: list, window_hours: int = 24) -> dict:
    """
    分析交易行为模式。
    
    算法：
    1. 按 sender 地址聚类，计算每个地址的净流入/流出
    2. 识别主导地址（交易量 > MIN_DOMINANT_TX_USD）
    3. 判断行为模式：吸筹 / 拉升 / 出货 / 洗盘 / 混合博弈
    4. 计算行为评分（0~100）
    
    Returns:
        {
            "pattern": str,
            "score": float,
            "level": str,
            "top_addresses": [...],
            "net_flow_total": float,
            "buy_ratio_avg": float,
            "description": str
        }
    """
    if not txs:
        return {
            "pattern": "无数据",
            "score": 0,
            "level": "⚪ 数据不足",
            "top_addresses": [],
            "net_flow_total": 0,
            "buy_ratio_avg": 0,
            "description": "无法获取交易数据"
        }
    
    # 主导地址集合（用于快速查找）
    dominant_addrs = set()
    if holders:
        for h in holders[:10]:
            # holders 里是 holder，txs 里是 sender_address
            dominant_addrs.add(h.get("holder", h.get("address", "")).lower())
    
    # 按 sender 聚类
    addr_flows = {}
    for tx in txs:
        sender = tx.get("sender", "").lower()
        amount = tx.get("amount_usd", 0)
        side = tx.get("side", "").lower()
        
        if sender not in addr_flows:
            addr_flows[sender] = {"buy": 0, "sell": 0, "count": 0, "txs": []}
        
        if side == "buy":
            addr_flows[sender]["buy"] += amount
        elif side == "sell":
            addr_flows[sender]["sell"] += amount
        
        addr_flows[sender]["count"] += 1
        addr_flows[sender]["txs"].append(tx)
    
    # 计算每个地址的净流量和买卖比
    addr_analysis = []
    for addr, flow in addr_flows.items():
        total_volume = flow["buy"] + flow["sell"]
        
        # 只分析交易量超过阈值的主导地址
        if total_volume < MIN_DOMINANT_TX_USD:
            continue
        
        net_flow = flow["buy"] - flow["sell"]
        buy_ratio = flow["buy"] / total_volume if total_volume > 0 else 0.5
        
        is_dominant = addr in dominant_addrs
        
        addr_analysis.append({
            "address": addr,
            "buy": flow["buy"],
            "sell": flow["sell"],
            "net_flow": net_flow,
            "total_volume": total_volume,
            "buy_ratio": buy_ratio,
            "tx_count": flow["count"],
            "is_dominant_holder": is_dominant,
        })
    
    # 按总交易量排序
    addr_analysis.sort(key=lambda x: x["total_volume"], reverse=True)
    
    if not addr_analysis:
        return {
            "pattern": "无主导交易",
            "score": 10,
            "level": "🟢 正常",
            "top_addresses": [],
            "net_flow_total": 0,
            "buy_ratio_avg": 0,
            "description": "过去 {} 小时内无明显主导交易地址".format(window_hours)
        }
    
    # 分析主导地址的行为模式
    top_addrs = addr_analysis[:TOP_ANALYSIS_ADDR_COUNT]
    total_net_flow = sum(a["net_flow"] for a in top_addrs)
    total_volume = sum(a["total_volume"] for a in top_addrs)
    avg_buy_ratio = sum(a["buy_ratio"] for a in top_addrs) / len(top_addrs) if top_addrs else 0.5
    
    # 识别主导模式
    # 计算有多少地址在净买入
    buying_addrs = [a for a in top_addrs if a["net_flow"] > 0]
    selling_addrs = [a for a in top_addrs if a["net_flow"] < 0]
    
    # 计算总净流量方向
    if total_net_flow > 0:
        flow_direction = "inflow"  # 净买入
    else:
        flow_direction = "outflow"  # 净卖出
    
    # 综合判断模式
    if len(buying_addrs) >= len(top_addrs) * 0.6 and total_net_flow > 0:
        if avg_buy_ratio > 0.7:
            pattern = "吸筹"
            score = 75
            level = "🟡 吸筹中"
            description = (
                f"检测到 {len(buying_addrs)} 个主导地址在净买入，"
                f"近 {window_hours}h 累计净买入 ${format_amount(total_net_flow)}。"
                f"平均买卖比 {avg_buy_ratio*100:.0f}%，买入力量强。"
            )
        else:
            pattern = "拉升"
            score = 65
            level = "🟠 拉升中"
            description = (
                f"主导地址整体偏买入，但买卖比较为均衡（{avg_buy_ratio*100:.0f}%），"
                f"可能处于拉升初期。"
            )
    elif len(selling_addrs) >= len(top_addrs) * 0.6 and total_net_flow < 0:
        pattern = "出货"
        score = 85
        level = "🔴 出货中"
        description = (
            f"⚠️ 警告：{len(selling_addrs)} 个主导地址在净卖出，"
            f"近 {window_hours}h 累计净流出 ${format_amount(abs(total_net_flow))}。"
            f"平均买卖比 {avg_buy_ratio*100:.0f}%，卖出压力明显。"
        )
    elif len(buying_addrs) > 0 and len(selling_addrs) > 0:
        # 混合博弈
        if abs(total_net_flow) < total_volume * 0.2:
            pattern = "洗盘"
            score = 55
            level = "🟡 横盘整理"
            description = (
                f"多空双方博弈，近 {window_hours}h 净流量较小（${format_amount(abs(total_net_flow))}），"
                f"买卖比 {avg_buy_ratio*100:.0f}%，可能是横盘整理或洗盘阶段。"
            )
        else:
            pattern = "混合博弈"
            score = 40
            level = "🟡 多空博弈"
            description = (
                f"多空双方均有动作，近 {window_hours}h 净流量 ${format_amount(abs(total_net_flow))}，"
                f"方向不明确，建议等待趋势明朗。"
            )
    else:
        pattern = "无明显模式"
        score = 30
        level = "⚪ 正常"
        description = f"未检测到明显的单一方向交易行为。"
    
    # 标注是否涉及已知的持仓大户
    dominant_holder_taking = [a for a in top_addrs if a.get("is_dominant_holder")]
    if dominant_holder_taking:
        description += (
            f"\n⚠️ 其中 {len(dominant_holder_taking)} 个为已知持仓大户，"
            f"需特别关注其动向。"
        )
    
    return {
        "pattern": pattern,
        "score": score,
        "level": level,
        "top_addresses": top_addrs,
        "net_flow_total": round(total_net_flow, 2),
        "net_flow_direction": flow_direction,
        "buy_ratio_avg": round(avg_buy_ratio * 100, 1),
        "description": description,
        "buying_addresses_count": len(buying_addrs),
        "selling_addresses_count": len(selling_addrs),
        "total_analyzed_addresses": len(addr_analysis),
        "window_hours": window_hours
    }


# ============================================================
# 第三层：K线形态分析
# ============================================================

def analyze_klines(klines: list, window_hours: int = 24) -> dict:
    """
    分析 K 线形态。
    
    算法：
    1. 计算成交量变化（近期 vs 前期）
    2. 计算价格变化和波动率
    3. 识别典型形态：放量拉升、缩量横盘、量价背离、宽幅震荡
    
    Returns:
        {
            "patterns": list[str],
            "score": float,
            "level": str,
            "volume_ratio": float,
            "price_change_pct": float,
            "volatility": float,
            "description": str
        }
    """
    if not klines or len(klines) < 4:
        return {
            "patterns": ["数据不足"],
            "score": 50,
            "level": "⚪ 数据不足",
            "volume_ratio": 0,
            "price_change_pct": 0,
            "volatility": 0,
            "description": "K 线数据不足，无法进行形态分析"
        }
    
    closes = [k["close"] for k in klines if k.get("close") is not None]
    volumes = [k["volume"] for k in klines if k.get("volume") is not None]
    
    if not closes or not volumes:
        return {
            "patterns": ["数据不足"],
            "score": 50,
            "level": "⚪ 数据不足",
            "volume_ratio": 0,
            "price_change_pct": 0,
            "volatility": 0,
            "description": "K 线数据不完整"
        }
    
    # 计算近期和前期的均值（假设 len >= 6）
    mid = len(klines) // 2
    recent_klines = klines[-mid:] if mid > 0 else klines[-3:]
    prev_klines = klines[:-mid] if mid > 0 else klines[:3]
    
    recent_vol_avg = statistics.mean([k.get("volume", 0) for k in recent_klines if k.get("volume")])
    prev_vol_avg = statistics.mean([k.get("volume", 0) for k in prev_klines if k.get("volume")])
    
    recent_close_avg = statistics.mean([k.get("close", 0) for k in recent_klines if k.get("close")])
    prev_close_avg = statistics.mean([k.get("close", 0) for k in prev_klines if k.get("close")])
    
    # 成交量比率（近期 / 前期）
    volume_ratio = recent_vol_avg / prev_vol_avg if prev_vol_avg > 0 else 1.0
    
    # 价格变化百分比（最新收盘 vs 最早收盘）
    first_close = closes[0]
    last_close = closes[-1]
    if first_close > 0:
        price_change_pct = (last_close - first_close) / first_close * 100
    else:
        price_change_pct = 0
    
    # 波动率（标准差 / 均值）
    if len(closes) >= 4:
        price_std = statistics.stdev(closes)
        price_mean = statistics.mean(closes)
        volatility = price_std / price_mean if price_mean > 0 else 0
    else:
        volatility = 0
    
    # 形态识别
    patterns = []
    descriptions = []
    
    # 放量上涨
    if volume_ratio > 1.5 and price_change_pct > 5:
        patterns.append("放量拉升")
        descriptions.append(f"成交量放大 {volume_ratio:.1f}x，价格上涨 {price_change_pct:.1f}%，资金明显介入")
    
    # 缩量横盘
    if volume_ratio < 0.6 and abs(price_change_pct) < 3:
        patterns.append("缩量横盘")
        descriptions.append(f"成交量萎缩至 {volume_ratio:.1f}x，价格波动 < 3%，可能蓄势或无人关注")
    
    # 放量下跌
    if volume_ratio > 1.5 and price_change_pct < -5:
        patterns.append("放量下跌")
        descriptions.append(f"成交量放大 {volume_ratio:.1f}x，价格下跌 {price_change_pct:.1f}%，抛压明显")
    
    # 价涨量跌（背离）
    if price_change_pct > 3 and volume_ratio < 0.8:
        patterns.append("量价背离")
        descriptions.append(f"价格上涨但成交量萎缩，上涨动力可能不足")
    
    # 宽幅震荡
    if len(klines) >= 4:
        high = max(k.get("high", 0) for k in klines if k.get("high"))
        low = min(k.get("low", 0) for k in klines if k.get("low"))
        if high > 0:
            range_pct = (high - low) / low * 100
            if range_pct > 15:
                patterns.append("宽幅震荡")
                descriptions.append(f"日内振幅 {range_pct:.1f}%，波动剧烈，风险较高")
    
    # 综合评分
    if not patterns:
        patterns.append("正常")
        descriptions.append("未检测到异常形态，价格走势正常")
    
    # 评分
    if "放量拉升" in patterns:
        score = 70
        level = "🟢 强势"
    elif "放量下跌" in patterns:
        score = 30
        level = "🔴 弱势"
    elif "量价背离" in patterns:
        score = 35
        level = "🟠 背离"
    elif "缩量横盘" in patterns:
        score = 45
        level = "🟡 整理"
    elif "宽幅震荡" in patterns:
        score = 40
        level = "🟠 高波动"
    else:
        score = 50
        level = "🟢 正常"
    
    description = "；".join(descriptions)
    
    return {
        "patterns": patterns,
        "score": score,
        "level": level,
        "volume_ratio": round(volume_ratio, 2),
        "price_change_pct": round(price_change_pct, 2),
        "volatility": round(volatility * 100, 2) if volatility else 0,
        "description": description,
        "klines_count": len(klines)
    }


# ============================================================
# 综合评分
# ============================================================

def calculate_manipulation_score(
    concentration_result: dict,
    behavior_result: dict,
    kline_result: dict
) -> dict:
    """
    计算综合控盘评分。
    
    权重：
    - 持仓集中度：40%
    - 交易行为：40%
    - K线形态：20%
    
    Returns:
        {
            "score": float,
            "level": str,
            "action": str,
            "signals": {...},
            "summary": str
        }
    """
    conc_score = concentration_result.get("score", 50)
    beh_score = behavior_result.get("score", 50)
    kline_score = kline_result.get("score", 50)
    
    # 加权综合
    total = conc_score * 0.40 + beh_score * 0.40 + kline_score * 0.20
    
    if total > 75:
        level = "🔴 极高控盘风险"
        action = "不建议介入，或极轻仓（< 1%）严格设止损"
        color = "red"
    elif total > 60:
        level = "🟠 高控盘迹象"
        action = "谨慎持仓，严格止损，避免大仓位"
        color = "orange"
    elif total > 40:
        level = "🟡 中等控盘"
        action = "可参与，跟随主流方向，注意设置止盈止损"
        color = "yellow"
    else:
        level = "🟢 无明显控盘"
        action = "正常参与，注意仓位管理即可"
        color = "green"
    
    return {
        "score": round(total, 1),
        "level": level,
        "action": action,
        "color": color,
        "signals": {
            "concentration": {
                "score": conc_score,
                "level": concentration_result.get("level", ""),
                "weight": "40%"
            },
            "behavior": {
                "score": beh_score,
                "level": behavior_result.get("level", ""),
                "weight": "40%"
            },
            "kline": {
                "score": kline_score,
                "level": kline_result.get("level", ""),
                "weight": "20%"
            }
        }
    }


# ============================================================
# 输出格式化
# ============================================================

def format_analysis_report(
    token: str,
    chain: str,
    token_detail: dict,
    concentration: dict,
    behavior: dict,
    klines: dict,
    manipulation: dict,
    window_hours: int
) -> str:
    """
    格式化完整的分析报告（用于微信输出）。
    """
    symbol = token_detail.get("symbol", "UNKNOWN")
    name = token_detail.get("name", "")
    
    lines = [
        f"🐸 {symbol}（{format_address(token)}）",
        f"🌍 链：{chain.upper()} {('— ' + name) if name and name != symbol else ''}",
        f"━━━━━━━━━━━━━━━━━━",
        f"",
        f"📊 持仓分布分析",
        f"{concentration.get('level', '⚪')}",
        f"  Top5：{concentration.get('top5_pct', 0):.1f}%",
        f"  Top10：{concentration.get('top10_pct', 0):.1f}%",
        f"  Top20：{concentration.get('top20_pct', 0):.1f}%",
        f"  持币人数：{concentration.get('holders_count', 'N/A')}",
        f"  {concentration.get('description', '')}",
        f"",
        f"💱 交易行为分析（近 {behavior.get('window_hours', window_hours)}h）",
        f"{behavior.get('level', '⚪')}",
        f"  净流量：{'+' if behavior.get('net_flow_total', 0) > 0 else ''}"
        f"${format_amount(abs(behavior.get('net_flow_total', 0)))}（{behavior.get('net_flow_direction', '')}）",
        f"  买卖比：{behavior.get('buy_ratio_avg', 0):.0f}%",
        f"  分析地址数：{behavior.get('total_analyzed_addresses', 0)}",
    ]
    
    # 添加 Top 地址详情
    top_addrs = behavior.get("top_addresses", [])
    if top_addrs:
        lines.append(f"  Top 主导地址：")
        for i, addr_info in enumerate(top_addrs[:3], 1):
            direction = "↑买" if addr_info.get("net_flow", 0) > 0 else "↓卖"
            net = abs(addr_info.get("net_flow", 0))
            lines.append(
                f"    {i}. {format_address(addr_info.get('address', ''))} "
                f"{direction} ${format_amount(net)}"
            )
    
    lines.extend([
        f"  {behavior.get('description', '')}",
        f"",
        f"📈 K线形态（{klines.get('klines_count', 0)} 根）",
        f"{klines.get('level', '⚪')}",
        f"  形态：{', '.join(klines.get('patterns', []))}",
        f"  成交量比：{klines.get('volume_ratio', 0):.1f}x",
        f"  价格变化：{format_pct(klines.get('price_change_pct', 0))}",
        f"  波动率：{klines.get('volatility', 0):.1f}%",
        f"  {klines.get('description', '')}",
        f"",
        f"━━━━━━━━━━━━━━━━━━",
        f"🤖 综合判断",
        f"{manipulation.get('level', '')}",
        f"  控盘评分：{manipulation.get('score', 0)}/100",
        f"  持仓信号：{manipulation['signals']['concentration']['level']} "
        f"（{manipulation['signals']['concentration']['score']:.0f}分）",
        f"  行为信号：{manipulation['signals']['behavior']['level']} "
        f"（{manipulation['signals']['behavior']['score']:.0f}分）",
        f"  形态信号：{manipulation['signals']['kline']['level']} "
        f"（{manipulation['signals']['kline']['score']:.0f}分）",
        f"",
        f"💡 建议：{manipulation.get('action', '正常操作')}",
        f"",
        f"⚠️ 免责声明：以上分析仅供参考，不构成投资建议。",
        f"请DYOR（Do Your Own Research）。"
    ])
    
    return "\n".join(lines)


def format_compact_report(
    token: str,
    chain: str,
    token_detail: dict,
    manipulation: dict,
    behavior: dict
) -> str:
    """
    格式化精简版报告（用于快速回复）。
    """
    symbol = token_detail.get("symbol", "UNKNOWN")
    price = token_detail.get("current_price_usd")
    
    lines = [
        f"🐸 {symbol} — {manipulation.get('level', '')}",
        f"💰 {format_price(price)}（{format_pct(token_detail.get('price_change_24h', 0))}）",
        f"📊 控盘评分：{manipulation.get('score', 0)}/100",
        f"📍 行为：{behavior.get('pattern', '未知')} "
        f"（{'净买入' if behavior.get('net_flow_direction') == 'inflow' else '净卖出'} "
        f"${format_amount(abs(behavior.get('net_flow_total', 0)))}）",
        f"💡 {manipulation.get('action', '')}"
    ]
    
    return "\n".join(lines)


# ============================================================
# 主分析流程
# ============================================================

def analyze(
    token: str,
    chain: str,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    output_format: str = "full"
) -> dict:
    """
    主分析流程。
    
    Args:
        token: 代币合约地址
        chain: 链 ID
        window_hours: 分析窗口期（小时）
        output_format: 输出格式，full=完整报告，compact=精简版
    
    Returns:
        包含所有分析结果的 dict
    """
    # 1. 获取基础信息
    token_detail = get_token_detail(token, chain)
    if "error" in token_detail:
        return {"error": f"Failed to get token detail: {token_detail['error']}"}
    
    # 2. 获取持仓数据
    holders = get_holders(token, chain, limit=100)
    
    # 3. 获取交易数据
    pair = token_detail.get("main_pair", "")
    txs = get_recent_txs(token, chain, pair, limit=100, window_hours=window_hours)
    
    # 4. 获取 K 线数据（使用 1h K 线，根数根据窗口期）
    kline_limit = min(window_hours, 48)  # 最多 48 根
    klines = get_klines(token, chain, interval=60, limit=kline_limit)
    
    # 5. 执行三层分析
    concentration = analyze_concentration(holders, token_detail)
    behavior = analyze_behavior(txs, holders, window_hours=window_hours)
    kline_analysis = analyze_klines(klines, window_hours=window_hours)
    
    # 6. 计算综合评分
    manipulation = calculate_manipulation_score(
        concentration,
        behavior,
        kline_analysis
    )
    
    # 7. 格式化输出
    if output_format == "compact":
        report = format_compact_report(
            token, chain, token_detail, manipulation, behavior
        )
    else:
        report = format_analysis_report(
            token, chain, token_detail,
            concentration, behavior, kline_analysis,
            manipulation, window_hours
        )
    
    return {
        "token": token,
        "chain": chain,
        "symbol": token_detail.get("symbol"),
        "token_detail": token_detail,
        "concentration": concentration,
        "behavior": behavior,
        "klines": kline_analysis,
        "manipulation": manipulation,
        "window_hours": window_hours,
        "report": report
    }


# ============================================================
# 工具函数
# ============================================================

def _safe_float(value, default=0.0):
    """安全转换为浮点数"""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value, default=0):
    """安全转换为整数"""
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
        description="Ave Guardian — Whale & Manipulator Detector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 whale_detector.py 0xf43c8f27754829202d2f66650eb3f6d168c288dc bsc
  python3 whale_detector.py 0xf43c8f27754829202d2f66650eb3f6d168c288dc bsc --window-hours 48
  python3 whale_detector.py 0xf43c8f27754829202d2f66650eb3f6d168c288dc bsc --output compact
        """
    )
    
    parser.add_argument("token", help="代币合约地址")
    parser.add_argument("chain", help="链 ID（如 bsc, eth, base）")
    parser.add_argument(
        "--window-hours", type=int, default=DEFAULT_WINDOW_HOURS,
        help=f"分析窗口期（小时），默认 {DEFAULT_WINDOW_HOURS}"
    )
    parser.add_argument(
        "--output", "-o", choices=["full", "compact", "json"],
        default="full",
        help="输出格式：full=完整报告，compact=精简版，json=原始数据"
    )
    
    args = parser.parse_args()
    
    # 执行分析
    result = analyze(
        token=args.token,
        chain=args.chain,
        window_hours=args.window_hours,
        output_format=args.output
    )
    
    if "error" in result:
        print(f"❌ Error: {result['error']}", file=sys.stderr)
        sys.exit(1)
    
    # 根据输出格式打印结果
    if args.output == "json":
        # 输出原始 JSON（不含 report 字符串）
        output = {k: v for k, v in result.items() if k != "report"}
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    else:
        print(result["report"])
    
    # 同时输出 JSON 到 stderr（方便 Agent 解析）
    if args.output != "json":
        output = {k: v for k, v in result.items() if k != "report"}
        print(f"\n[DEBUG JSON]\n{json.dumps(output, indent=2, ensure_ascii=False, default=str)}", file=sys.stderr)


if __name__ == "__main__":
    main()
