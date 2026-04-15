#!/usr/bin/env python3
"""
Ave Guardian — Meme & Narrative Scanner
Meme 币叙事捕捉引擎。

功能：
1. 扫描平台标签（meme / pump_in_hot / fourmeme_in_hot 等）
2. 获取链上动量数据（成交量增速、持有人变化）
3. 识别早期爆发信号
4. 计算叙事评分（0~100）
5. 按评分排序输出榜单

用法：
    python3 meme_scanner.py
    python3 meme_scanner.py trending
    python3 meme_scanner.py 0xf43c8f27754829202d2f66650eb3f6d168c288dc bsc
    python3 meme_scanner.py PEPE bsc --output compact
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
    run_ave_rest,
)

# ============================================================
# 常量定义
# ============================================================

# 平台标签列表（按热度排序）
PLATFORM_TAGS = [
    "meme",
    "pump_in_hot",
    "pump_in_new",
    "fourmeme_in_hot",
    "fourmeme_in_new",
    "bonk_in_hot",
    "nadfun_in_hot",
]

# 叙事评分权重
WEIGHT_VOLUME = 0.30       # 成交量增速
WEIGHT_HOLDERS = 0.25     # 持有人增长
WEIGHT_ADDRESS = 0.20     # 新地址加速
WEIGHT_KLINE = 0.15        # K线突破
WEIGHT_PLATFORM = 0.10    # 平台标签

# 触发阈值
VOLUME_SPIKE_THRESHOLD = 3.0       # 成交量增长倍数（3x）
HOLDER_GROWTH_THRESHOLD = 0.20     # 持有人增长 20%
PRICE_SPIKE_THRESHOLD = 0.10       # 价格变化 10%
MIN_TVL_THRESHOLD = 10000          # 最低 TVL（$10K）

# 叙事评分阈值
SCORE_EXPLOSIVE = 0.75   # 🔥 强烈关注
SCORE_WATCH = 0.55       # 🟠 值得关注
SCORE_OBSERVE = 0.40     # 🟡 观察中

# 每次扫描的代币上限
MAX_TOKENS_PER_TAG = 30
MAX_TOTAL_RESULTS = 20

# ============================================================
# 数据获取
# ============================================================

def get_platform_tokens(platform: str, limit: int = 30) -> list:
    """
    获取平台标签下的代币列表。
    
    Returns:
        list of {token, chain, symbol, name, price_usd, ...}
    """
    result = run_ave_rest(
        "platform-tokens",
        "--platform", platform,
        "--limit", str(limit)
    )
    
    if "error" in result:
        return []
    
    raw_data = result.get("data", [])
    if isinstance(raw_data, dict):
        raw_data = raw_data.get("data", raw_data.get("tokens", []))
    if not isinstance(raw_data, list):
        return []
    
    tokens = []
    for t in raw_data:
        tokens.append({
            "token": t.get("token", ""),
            "chain": t.get("chain", ""),
            "symbol": t.get("symbol", "?"),
            "name": t.get("name", ""),
            "current_price_usd": _safe_float(t.get("current_price_usd", t.get("price"))),
            "price_change_24h": _safe_float(t.get("price_change_24h", 0)),
            "price_change_5m": _safe_float(t.get("price_change_5m", 0)),
            "price_change_1h": _safe_float(t.get("price_change_1h", 0)),
            "market_cap": _safe_float(t.get("market_cap", 0)),
            "fdv": _safe_float(t.get("fdv", 0)),
            "tvl": _safe_float(t.get("tvl", 0)),
            "tx_volume_u_24h": _safe_float(t.get("tx_volume_u_24h", 0)),
            "tx_count_24h": _safe_float(t.get("tx_count_24h", 0)),
            "holders": _safe_float(t.get("holders", 0)),
            "platform_tag": platform,
            "rank": t.get("rank", 0),
        })
    
    return tokens


def get_trending(chain: str = "bsc", page: int = 1, page_size: int = 50) -> list:
    """
    获取链上热门代币。
    
    Returns:
        list of token dicts
    """
    result = run_ave_rest(
        "trending",
        "--chain", chain,
        "--page", str(page),
        "--page-size", str(page_size)
    )
    
    if "error" in result:
        return []
    
    raw_data = result.get("data", {})
    if isinstance(raw_data, dict):
        raw_data = raw_data.get("data", [])
    if not isinstance(raw_data, list):
        return []
    
    tokens = []
    for t in raw_data:
        tokens.append({
            "token": t.get("token", ""),
            "chain": t.get("chain", chain),
            "symbol": t.get("symbol", "?"),
            "name": t.get("name", ""),
            "current_price_usd": _safe_float(t.get("current_price_usd", t.get("price"))),
            "price_change_24h": _safe_float(t.get("price_change_24h", 0)),
            "price_change_1h": _safe_float(t.get("price_change_1h", 0)),
            "price_change_5m": _safe_float(t.get("price_change_5m", 0)),
            "market_cap": _safe_float(t.get("market_cap", 0)),
            "tvl": _safe_float(t.get("tvl", 0)),
            "tx_volume_u_24h": _safe_float(t.get("tx_volume_u_24h", 0)),
            "holders": _safe_float(t.get("holders", 0)),
            "platform_tag": "trending",
        })
    
    return tokens


def get_token_detail(token: str, chain: str) -> dict:
    """
    获取单个代币的详细信息。
    用于补充平台标签数据中没有的字段。
    """
    result = run_ave_rest("token", "--address", token, "--chain", chain)
    
    if "error" in result:
        return {}
    
    data = result.get("data", {})
    if isinstance(data, dict):
        data = data.get("token", data)
    
    return {
        "current_price_usd": _safe_float(data.get("current_price_usd")),
        "price_change_5m": _safe_float(data.get("token_price_change_5m")),
        "price_change_1h": _safe_float(data.get("token_price_change_1h")),
        "price_change_24h": _safe_float(data.get("token_price_change_24h")),
        "market_cap": _safe_float(data.get("market_cap")),
        "tvl": _safe_float(data.get("tvl")),
        "tx_volume_u_24h": _safe_float(data.get("tx_volume_u_24h")),
        "holders": data.get("holders", 0),
        "main_pair": data.get("main_pair", ""),
    }


def get_klines_simple(token: str, chain: str, interval: int = 60, limit: int = 6) -> dict:
    """
    获取简单 K 线数据（用于价格突破检测）。
    """
    result = run_ave_rest(
        "kline-token",
        "--address", token,
        "--chain", chain,
        "--interval", str(interval),
        "--size", str(limit)
    )
    
    if "error" in result:
        return {}
    
    raw_data = result.get("data", {})
    if isinstance(raw_data, dict):
        raw_data = raw_data.get("points", [])
    if not isinstance(raw_data, list) or len(raw_data) == 0:
        return {}
    
    points = raw_data[-6:]  # 最近 6 根
    
    closes = [float(p.get("close", 0)) for p in points]
    volumes = [float(p.get("volume", 0)) for p in points]
    
    if not closes:
        return {}
    
    # 计算近期 vs 前期
    mid = len(closes) // 2
    recent_closes = closes[-mid:] if mid > 0 else closes[-2:]
    prev_closes = closes[:-mid] if mid > 0 else closes[:-2]
    
    recent_avg = sum(recent_closes) / len(recent_closes) if recent_closes else 0
    prev_avg = sum(prev_closes) / len(prev_closes) if prev_closes else 0
    
    recent_vol_avg = sum(volumes[-mid:]) / len(volumes[-mid:]) if mid > 0 and volumes else 0
    prev_vol_avg = sum(volumes[:-mid]) / len(volumes[:-mid]) if mid > 0 and volumes else 0
    
    return {
        "recent_avg_price": recent_avg,
        "prev_avg_price": prev_avg,
        "price_change_pct": ((recent_avg - prev_avg) / prev_avg * 100) if prev_avg > 0 else 0,
        "recent_avg_volume": recent_vol_avg,
        "prev_avg_volume": prev_vol_avg,
        "volume_ratio": recent_vol_avg / prev_vol_avg if prev_vol_avg > 0 else 1.0,
        "latest_close": closes[-1],
        "highest_6h": max(closes) if closes else 0,
    }


def get_recent_txs_simple(token: str, chain: str, pair: str, limit: int = 20) -> dict:
    """
    获取最近的 swap（简化版，用于计算新地址数）。
    """
    if not pair:
        return {}
    
    result = run_ave_rest(
        "txs",
        "--pair", pair,
        "--chain", chain,
        "--limit", str(limit)
    )
    
    if "error" in result:
        return {}
    
    raw_data = result.get("data", {})
    if isinstance(raw_data, dict):
        raw_data = raw_data.get("txs", [])
    if not isinstance(raw_data, list):
        return {}
    
    return {
        "tx_count": len(raw_data),
        "total_volume_usd": sum(_safe_float(tx.get("amount_usd", 0)) for tx in raw_data),
        "senders": len(set(tx.get("sender_address", "") for tx in raw_data if tx.get("sender_address"))),
    }


# ============================================================
# 叙事评分算法
# ============================================================

def calculate_narrative_score(token_data: dict, kline_data: dict = None, tx_data: dict = None) -> dict:
    """
    计算单个代币的叙事评分。
    
    Returns:
        {
            "score": float,          # 0~100
            "level": str,            # 🔥🟠🟡⚪
            "signals": dict,         # 各维度得分
            "reasons": list,         # 触发信号描述
            "recommendation": str    # 建议
        }
    """
    signals = {}
    reasons = []
    
    # ---------- 1. 成交量信号（权重 30%）----------
    vol_24h = token_data.get("tx_volume_u_24h", 0)
    tvl = token_data.get("tvl", 0)
    
    # 用 TVL 估算成交量是否异常
    # 正常交易对的 vol/tvl 比例应该在一定范围内
    vol_tvl_ratio = vol_24h / tvl if tvl > 0 else 0
    
    # 如果成交量相对于 TVL 异常高（3x 以上），说明有资金在炒作
    if vol_tvl_ratio > 0.5:  # 成交量超过 TVL 的 50%
        vol_score = 95
        reasons.append(f"24h成交量暴增（TVL的{vol_tvl_ratio:.0%}）")
    elif vol_tvl_ratio > 0.3:
        vol_score = 85
        reasons.append(f"成交量激增（TVL的{vol_tvl_ratio:.0%}）")
    elif vol_tvl_ratio > 0.1:
        vol_score = 70
        reasons.append(f"成交量放大（TVL的{vol_tvl_ratio:.0%}）")
    elif vol_24h > 100000:
        vol_score = 60
        reasons.append(f"24h成交量 ${format_amount(vol_24h)}")
    elif vol_24h > 10000:
        vol_score = 45
    elif vol_24h > 1000:
        vol_score = 30
    else:
        vol_score = 15
        reasons.append("⚠️ 成交量极低")
    
    signals["volume"] = {
        "score": vol_score,
        "vol_24h": vol_24h,
        "tvl": tvl,
        "vol_tvl_ratio": round(vol_tvl_ratio, 3)
    }
    
    # ---------- 2. 持有人增长信号（权重 25%）----------
    holders = token_data.get("holders", 0)
    
    if holders > 50000:
        holder_score = 85
        reasons.append(f"持有人数高（{holders:,}）")
    elif holders > 10000:
        holder_score = 70
        reasons.append(f"持有人 {holders:,}")
    elif holders > 1000:
        holder_score = 50
    elif holders > 100:
        holder_score = 35
    else:
        holder_score = 20
        reasons.append("⚠️ 持有人数少")
    
    signals["holders"] = {
        "score": holder_score,
        "holders": holders
    }
    
    # ---------- 3. 新地址加速信号（权重 20%）----------
    if tx_data:
        senders = tx_data.get("senders", 0)
        tx_count = tx_data.get("tx_count", 0)
        
        # 高发送者 / 交易数比 = 更多独立地址参与
        if tx_count > 0:
            unique_ratio = senders / tx_count
            if unique_ratio > 0.8:
                address_score = 90
                reasons.append(f"新地址加速入场（{senders}个独立地址）")
            elif unique_ratio > 0.5:
                address_score = 70
            elif unique_ratio > 0.3:
                address_score = 50
            else:
                address_score = 30
        else:
            address_score = 40
    else:
        address_score = 40
    
    signals["address"] = {
        "score": address_score,
        "senders": tx_data.get("senders", 0) if tx_data else 0,
        "tx_count": tx_data.get("tx_count", 0) if tx_data else 0
    }
    
    # ---------- 4. K线突破信号（权重 15%）----------
    if kline_data:
        price_change_pct = kline_data.get("price_change_pct", 0)
        volume_ratio = kline_data.get("volume_ratio", 1.0)
        latest_close = kline_data.get("latest_close", 0)
        highest_6h = kline_data.get("highest_6h", 0)
        
        # 价格突破检测（1h 内创 6h 新高）
        if highest_6h > 0 and latest_close >= highest_6h:
            price_breakout = True
        else:
            price_breakout = False
        
        if price_breakout and price_change_pct > 5:
            kline_score = 95
            reasons.append(f"🚀 价格突破（1h +{price_change_pct:.1f}%）")
        elif price_breakout:
            kline_score = 80
            reasons.append("📈 价格创新高")
        elif price_change_pct > 10:
            kline_score = 85
            reasons.append(f"🚀 强势上涨 +{price_change_pct:.1f}%")
        elif price_change_pct > 5:
            kline_score = 70
            reasons.append(f"📈 上涨 +{price_change_pct:.1f}%")
        elif price_change_pct > 2:
            kline_score = 55
        elif price_change_pct < -5:
            kline_score = 25
            reasons.append(f"📉 下跌 {price_change_pct:.1f}%")
        else:
            kline_score = 45
        
        # 成交量放大配合
        if volume_ratio > 2.0 and price_change_pct > 0:
            kline_score = min(100, kline_score + 10)
            reasons.append(f"✅ 放量上涨（{volume_ratio:.1f}x）")
    else:
        price_change_pct = token_data.get("price_change_1h", 0)
        kline_score = 50
        if price_change_pct > 10:
            kline_score = 80
        elif price_change_pct > 5:
            kline_score = 65
    
    signals["kline"] = {
        "score": kline_score,
        "price_change_pct": kline_data.get("price_change_pct", token_data.get("price_change_1h", 0)) if kline_data else token_data.get("price_change_1h", 0),
        "volume_ratio": kline_data.get("volume_ratio", 1.0) if kline_data else 1.0,
        "breakout": kline_data.get("latest_close", 0) >= kline_data.get("highest_6h", 0) if kline_data else False
    }
    
    # ---------- 5. 平台标签信号（权重 10%）----------
    platform_tag = token_data.get("platform_tag", "")
    tag_score = 50
    
    if platform_tag in ["pump_in_hot", "fourmeme_in_hot"]:
        tag_score = 90
        reasons.append(f"🏷️ {platform_tag} 热门标签")
    elif platform_tag in ["pump_in_new", "fourmeme_in_new"]:
        tag_score = 75
        reasons.append(f"🆕 {platform_tag} 新上榜")
    elif platform_tag in ["bonk_in_hot", "nadfun_in_hot"]:
        tag_score = 70
    elif platform_tag == "meme":
        tag_score = 65
    elif platform_tag == "trending":
        tag_score = 60
    else:
        tag_score = 40
    
    signals["platform"] = {
        "score": tag_score,
        "tag": platform_tag
    }
    
    # ---------- 综合评分 ----------
    total = (
        signals["volume"]["score"] * WEIGHT_VOLUME +
        signals["holders"]["score"] * WEIGHT_HOLDERS +
        signals["address"]["score"] * WEIGHT_ADDRESS +
        signals["kline"]["score"] * WEIGHT_KLINE +
        signals["platform"]["score"] * WEIGHT_PLATFORM
    )
    
    # 判断等级
    if total >= SCORE_EXPLOSIVE * 100:
        level = "🔥 强烈关注"
        recommendation = "综合信号强，建议关注是否持续突破"
    elif total >= SCORE_WATCH * 100:
        level = "🟠 值得关注"
        recommendation = "有信号出现，需进一步验证趋势持续性"
    elif total >= SCORE_OBSERVE * 100:
        level = "🟡 观察中"
        recommendation = "存在部分信号，需持续关注"
    else:
        level = "⚪ 普通"
        recommendation = "信号不足，建议观望"
    
    return {
        "score": round(total, 1),
        "level": level,
        "signals": signals,
        "reasons": reasons,
        "recommendation": recommendation
    }


# ============================================================
# 叙事扫描主流程
# ============================================================

def scan_meme_tokens(chain: str = "bsc") -> list:
    """
    扫描所有平台标签，返回按叙事评分排序的代币列表。
    """
    all_tokens = {}
    
    print(f"[Meme Scanner] 正在扫描链: {chain.upper()}", flush=True)
    
    # 1. 扫描各平台标签
    for tag in PLATFORM_TAGS:
        print(f"[Meme Scanner] 扫描标签: {tag}", flush=True)
        tokens = get_platform_tokens(tag, limit=MAX_TOKENS_PER_TAG)
        
        for t in tokens:
            # 按 token+chain 作为唯一键
            key = f"{t['token']}-{t['chain']}"
            if key not in all_tokens:
                all_tokens[key] = t
            else:
                # 合并平台标签
                existing_tag = all_tokens[key].get("platform_tags", [])
                if tag not in existing_tag:
                    existing_tag.append(tag)
                    all_tokens[key]["platform_tags"] = existing_tag
        
        time.sleep(0.1)  # 避免过快请求
    
    # 2. 扫描 trending
    print(f"[Meme Scanner] 扫描 trending", flush=True)
    trending = get_trending(chain=chain, page=1, page_size=50)
    for t in trending:
        key = f"{t['token']}-{t['chain']}"
        if key not in all_tokens:
            all_tokens[key] = t
            all_tokens[key]["platform_tags"] = ["trending"]
        else:
            existing_tags = all_tokens[key].get("platform_tags", [])
            if "trending" not in existing_tags:
                existing_tags.append("trending")
                all_tokens[key]["platform_tags"] = existing_tags
    
    # 3. 去重和过滤低质量
    tokens_to_score = []
    for key, t in all_tokens.items():
        # 过滤：必须有有效 token 地址
        if not t.get("token"):
            continue
        # 过滤：TVL 太低
        if t.get("tvl", 0) < MIN_TVL_THRESHOLD:
            continue
        tokens_to_score.append(t)
    
    print(f"[Meme Scanner] 待评分代币数量: {len(tokens_to_score)}", flush=True)
    
    # 4. 逐个计算叙事评分
    scored_tokens = []
    for t in tokens_to_score:
        token_addr = t["token"]
        chain_id = t["chain"]
        
        # 补充 K 线数据（只对 Top 10 候选补充）
        kline_data = {}
        tx_data = {}
        
        # 获取 K 线数据
        kline_data = get_klines_simple(token_addr, chain_id, interval=60, limit=6)
        
        # 获取 swap 数据
        pair = t.get("main_pair", "")
        if pair:
            tx_data = get_recent_txs_simple(token_addr, chain_id, pair, limit=20)
        
        # 合并 K 线的价格变化到 token_data
        if kline_data:
            t["price_change_1h"] = kline_data.get("price_change_pct", t.get("price_change_1h", 0))
        
        # 计算叙事评分
        score_result = calculate_narrative_score(t, kline_data, tx_data)
        
        # 合并结果
        scored_tokens.append({
            **t,
            "narrative_score": score_result["score"],
            "narrative_level": score_result["level"],
            "narrative_signals": score_result["signals"],
            "narrative_reasons": score_result["reasons"],
            "narrative_recommendation": score_result["recommendation"],
        })
        
        time.sleep(0.05)  # 避免限流
    
    # 5. 按评分排序
    scored_tokens.sort(key=lambda x: x["narrative_score"], reverse=True)
    
    return scored_tokens[:MAX_TOTAL_RESULTS]


def analyze_single_token(token: str, chain: str) -> dict:
    """
    分析单个代币的叙事评分。
    """
    # 获取完整信息
    token_detail = get_token_detail(token, chain)
    kline_data = get_klines_simple(token, chain, interval=60, limit=6)
    
    # 获取 main_pair
    pair = token_detail.get("main_pair", "")
    tx_data = get_recent_txs_simple(token, chain, pair, limit=20) if pair else {}
    
    # 合并数据
    full_data = {
        "token": token,
        "chain": chain,
        "platform_tag": "user_query",
        **token_detail,
        "main_pair": pair,
    }
    
    # 计算叙事评分
    score_result = calculate_narrative_score(full_data, kline_data, tx_data)
    
    return {
        **full_data,
        "narrative_score": score_result["score"],
        "narrative_level": score_result["level"],
        "narrative_signals": score_result["signals"],
        "narrative_reasons": score_result["reasons"],
        "narrative_recommendation": score_result["recommendation"],
        "kline_data": kline_data,
        "tx_data": tx_data,
    }


# ============================================================
# 输出格式化
# ============================================================

def format_meme_report(tokens: list, chain: str) -> str:
    """
    格式化 Meme 扫描报告。
    """
    if not tokens:
        return f"❌ 未找到符合条件的 Meme 代币（链: {chain.upper()}）"
    
    lines = [
        f"🔥 Meme 叙事扫描报告",
        f"━━━━━━━━━━━━━━━━━━",
        f"🕐 扫描时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
        f"🌍 链：{chain.upper()}",
        f"📊 扫描范围：{' / '.join(PLATFORM_TAGS + ['trending'])}",
        f"━━━━━━━━━━━━━━━━━━",
        f"",
    ]
    
    # 🔥 强烈关注
    explosive = [t for t in tokens if t["narrative_score"] >= SCORE_EXPLOSIVE * 100]
    if explosive:
        lines.append(f"🔥 强烈关注（{len(explosive)} 个）")
        lines.append("━━━━━━━━━━━━━━━━━━")
        for i, t in enumerate(explosive, 1):
            lines.extend(format_token_entry(t, i))
            lines.append("")
    
    # 🟠 值得关注
    watch = [t for t in tokens if SCORE_WATCH * 100 <= t["narrative_score"] < SCORE_EXPLOSIVE * 100]
    if watch:
        lines.append(f"🟠 值得关注（{len(watch)} 个）")
        lines.append("━━━━━━━━━━━━━━━━━━")
        for i, t in enumerate(watch, 1):
            lines.extend(format_token_entry(t, i))
            lines.append("")
    
    # 🟡 观察中
    observe = [t for t in tokens if SCORE_OBSERVE * 100 <= t["narrative_score"] < SCORE_WATCH * 100]
    if observe:
        lines.append(f"🟡 观察中（{len(observe)} 个）")
        lines.append("━━━━━━━━━━━━━━━━━━")
        for i, t in enumerate(observe[:5], 1):  # 最多显示 5 个
            lines.extend(format_token_entry(t, i))
            lines.append("")
    
    if not explosive and not watch and not observe:
        lines.append("⚪ 暂无明显叙事信号")
    
    lines.extend([
        f"",
        f"━━━━━━━━━━━━━━━━━━",
        f"⚠️ 提示：Meme 币极高风险，以上为客观数据，不构成投资建议。",
    ])
    
    return "\n".join(lines)


def format_token_entry(t: dict, rank: int) -> list:
    """格式化单个代币的条目"""
    symbol = t.get("symbol", "?")
    ca = t.get("token", "")
    score = t.get("narrative_score", 0)
    level = t.get("narrative_level", "⚪")
    price = t.get("current_price_usd", 0)
    price_change = t.get("price_change_24h", 0)
    tvl = t.get("tvl", 0)
    vol_24h = t.get("tx_volume_u_24h", 0)
    reasons = t.get("narrative_reasons", [])
    
    lines = [
        f"{rank}. {symbol}（{format_address(ca)}）— {level}",
        f"   评分：{score:.1f}/100 | 价格：{format_price(price)}（{format_pct(price_change)}）",
        f"   TVL：{format_amount(tvl)} | 24h成交量：{format_amount(vol_24h)}",
    ]
    
    # 添加触发信号
    if reasons:
        for reason in reasons[:3]:
            lines.append(f"   {reason}")
    
    return lines


def format_single_analysis(result: dict) -> str:
    """格式化单个代币的分析结果"""
    symbol = result.get("symbol", "?")
    ca = result.get("token", "")
    chain = result.get("chain", "?")
    score = result.get("narrative_score", 0)
    level = result.get("narrative_level", "⚪")
    recommendation = result.get("narrative_recommendation", "")
    reasons = result.get("narrative_reasons", [])
    signals = result.get("narrative_signals", {})
    price = result.get("current_price_usd", 0)
    price_change_24h = result.get("price_change_24h", 0)
    price_change_1h = result.get("price_change_1h", 0)
    tvl = result.get("tvl", 0)
    holders = result.get("holders", 0)
    
    kline_data = result.get("kline_data", {})
    tx_data = result.get("tx_data", {})
    
    lines = [
        f"🔥 Meme 叙事分析",
        f"━━━━━━━━━━━━━━━━━━",
        f"🐸 {symbol}（{format_address(ca)}）",
        f"🌍 链：{chain.upper()}",
        f"🕐 时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
        f"",
        f"📊 综合评分：{score:.1f}/100 {level}",
        f"",
        f"💰 当前价格：{format_price(price)}",
        f"📈 5m 变化：{format_pct(result.get('price_change_5m', 0))}",
        f"📈 1h 变化：{format_pct(price_change_1h)}",
        f"📈 24h 变化：{format_pct(price_change_24h)}",
        f"💧 TVL：{format_amount(tvl)}",
        f"👥 持有人：{holders:,}" if holders else "",
        f"",
        f"━━━━━━━━━━━━━━━━━━",
        f"📋 触发信号：",
    ]
    
    if reasons:
        for reason in reasons:
            lines.append(f"  ✅ {reason}")
    else:
        lines.append("  ⚪ 暂无明显信号")
    
    lines.extend([
        f"",
        f"📐 详细指标：",
        f"  成交量信号：{signals.get('volume', {}).get('score', 0):.0f}/100",
        f"    24h成交量：{format_amount(signals.get('volume', {}).get('vol_24h', 0))}",
        f"    TVL：{format_amount(signals.get('volume', {}).get('tvl', 0))}",
        f"    vol/TVL：{signals.get('volume', {}).get('vol_tvl_ratio', 0):.1%}",
        f"  持有人信号：{signals.get('holders', {}).get('score', 0):.0f}/100",
        f"  K线信号：{signals.get('kline', {}).get('score', 0):.0f}/100",
        f"    1h 变化：{signals.get('kline', {}).get('price_change_pct', 0):.1f}%",
        f"    成交量比：{signals.get('kline', {}).get('volume_ratio', 0):.1f}x",
        f"  平台标签：{signals.get('platform', {}).get('score', 0):.0f}/100",
        f"    标签：{signals.get('platform', {}).get('tag', 'N/A')}",
    ])
    
    if tx_data:
        lines.extend([
            f"  地址活跃：{signals.get('address', {}).get('score', 0):.0f}/100",
            f"    独立地址数：{tx_data.get('senders', 0)}",
            f"    交易数：{tx_data.get('tx_count', 0)}",
        ])
    
    lines.extend([
        f"",
        f"━━━━━━━━━━━━━━━━━━",
        f"💡 AI 判断：{recommendation}",
        f"",
        f"⚠️ 免责声明：以上分析仅供参考，不构成投资建议。",
    ])
    
    return "\n".join(filter(None, lines))


def format_compact_report(tokens: list) -> str:
    """格式化精简版榜单"""
    if not tokens:
        return "❌ 未找到符合条件的代币"
    
    lines = [
        f"🔥 Meme 叙事榜单（Top {len(tokens)}）"
    ]
    
    for i, t in enumerate(tokens[:10], 1):
        symbol = t.get("symbol", "?")
        score = t.get("narrative_score", 0)
        level = t.get("narrative_level", "⚪")
        price_change = t.get("price_change_24h", 0)
        tvl = t.get("tvl", 0)
        
        lines.append(
            f"{i}. {symbol} | {level} {score:.0f}分 "
            f"| {format_pct(price_change)} | TVL {format_amount(tvl)}"
        )
    
    return "\n".join(lines)


# ============================================================
# 工具函数
# ============================================================

def _safe_float(value, default=0.0):
    """安全转换为浮点数"""
    if value is None or value == "":
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
        description="Ave Guardian — Meme & Narrative Scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 meme_scanner.py
  python3 meme_scanner.py trending
  python3 meme_scanner.py 0xf43c8f27754829202d2f66650eb3f6d168c288dc bsc
  python3 meme_scanner.py PEPE bsc --chain bsc --output compact
        """
    )
    
    parser.add_argument(
        "token",
        nargs="?",
        default=None,
        help="代币地址或符号（可选）"
    )
    parser.add_argument(
        "chain",
        nargs="?",
        default="bsc",
        help="链 ID（默认 bsc）"
    )
    parser.add_argument(
        "--chain", "-c",
        dest="chain_flag",
        default=None,
        help="链 ID（可选，默认 bsc）"
    )
    parser.add_argument(
        "--output", "-o",
        choices=["full", "compact", "json"],
        default="full",
        help="输出格式"
    )
    
    args = parser.parse_args()
    
    # 解析 chain 参数（positional 或 flag）
    chain_arg = args.chain_flag if args.chain_flag else args.chain
    
    if args.token:
        # 分析单个代币
        # 判断是 CA 还是 symbol
        token_input = args.token.strip()
        
        if token_input.startswith("0x"):
            # 是 CA，直接使用
            token_addr = token_input
            chain_id = chain_arg
        else:
            # 是 symbol，需要先搜索
            search_result = run_ave_rest(
                "search",
                "--keyword", token_input,
                "--chain", chain_arg,
                "--limit", "1"
            )
            if "error" in search_result:
                print(f"❌ 搜索失败: {search_result['error']}", file=sys.stderr)
                sys.exit(1)
            
            data = search_result.get("data", [])
            if isinstance(data, dict):
                data = data.get("data", [])
            if not isinstance(data, list) or len(data) == 0:
                print(f"❌ 未找到代币: {token_input}", file=sys.stderr)
                sys.exit(1)
            
            first_result = data[0]
            token_addr = first_result.get("token", "")
            chain_id = first_result.get("chain", chain_arg)
        
        if not token_addr:
            print(f"❌ 无法确定代币地址", file=sys.stderr)
            sys.exit(1)
        
        print(f"[Meme Scanner] 分析代币: {token_addr} @ {chain_id}", file=sys.stderr)
        
        result = analyze_single_token(token_addr, chain_id)
        
        if args.output == "json":
            output = {k: v for k, v in result.items() if k not in ["kline_data", "tx_data"]}
            print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
        else:
            print(format_single_analysis(result))
    
    else:
        # 全量扫描
        print(f"[Meme Scanner] 开始全量扫描...", file=sys.stderr)
        
        tokens = scan_meme_tokens(chain=chain_arg)
        
        if args.output == "json":
            output = [{"symbol": t["symbol"], "token": t["token"], "chain": t["chain"],
                      "narrative_score": t["narrative_score"], "narrative_level": t["narrative_level"],
                      "current_price_usd": t.get("current_price_usd"),
                      "price_change_24h": t.get("price_change_24h"),
                      "tvl": t.get("tvl")}
                     for t in tokens]
            print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
        elif args.output == "compact":
            print(format_compact_report(tokens))
        else:
            print(format_meme_report(tokens, chain_arg))


if __name__ == "__main__":
    main()
