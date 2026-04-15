#!/usr/bin/env python3
"""
Ave Guardian — Token Health Reporter
代币体检报告生成器。

五大维度分析：
1. 合约安全 — 蜜罐/税率/权限/Mint/黑名单
2. 流动性评估 — TVL/FDV比/活跃度
3. 持仓分布 — Top10集中度/CEX地址
4. 市场行为 — 买卖比/波动率/僵尸币检测
5. 基本面 — 市值/FDV/发行时间

综合评分：0~100 分，输出星级和安全建议。

用法：
    python3 health_reporter.py <CA> <chain>
    python3 health_reporter.py 0xf43c8f27754829202d2f66650eb3f6d168c288dc bsc
    python3 health_reporter.py 0xf43c8f27754829202d2f66650eb3f6d168c288dc bsc --output compact
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
    score_to_stars,
    score_to_level,
    run_ave_rest,
)

# ============================================================
# 常量定义
# ============================================================

# 权重配置
WEIGHT_CONTRACT = 0.30    # 合约安全权重 30%
WEIGHT_LIQUIDITY = 0.25   # 流动性权重 25%
WEIGHT_HOLDERS = 0.20     # 持仓分布权重 20%
WEIGHT_MARKET = 0.15       # 市场行为权重 15%
WEIGHT_BASIC = 0.10        # 基本面权重 10%

# 风险阈值
HONEYPOT_SCORE_CUTOFF = 10      # 蜜罐直接判 10 分以下
HIGH_TAX_CUTOFF = 15             # 高税阈值 %
CEX_ADDRESS_WARNING_PCT = 20    # CEX 地址超过 20% 发出警告

# ============================================================
# 数据获取
# ============================================================

def get_token_detail(token: str, chain: str) -> dict:
    """获取代币基本信息"""
    result = run_ave_rest("token", "--address", token, "--chain", chain)
    
    if "error" in result:
        return {"error": result["error"]}
    
    # API 返回结构：result["data"]["token"]
    data = result.get("data", {})
    if isinstance(data, dict):
        data = data.get("token", data)
    
    return {
        "name": data.get("name", "Unknown"),
        "symbol": data.get("symbol", "UNKNOWN"),
        "token": data.get("token", token),
        "chain": data.get("chain", chain),
        "current_price_usd": _safe_float(data.get("current_price_usd")),
        "price_change_5m": _safe_float(data.get("token_price_change_5m")),
        "price_change_1h": _safe_float(data.get("token_price_change_1h")),
        "price_change_24h": _safe_float(data.get("token_price_change_24h")),
        "market_cap": _safe_float(data.get("market_cap")),
        "fdv": _safe_float(data.get("fdv")),
        "tvl": _safe_float(data.get("tvl")),
        "tx_volume_u_24h": _safe_float(data.get("tx_volume_u_24h")),
        "tx_count_24h": _safe_float(data.get("tx_count_24h", 0)),
        "holders": data.get("holders", 0),
        "total_supply": data.get("total", "0"),
        "main_pair": data.get("main_pair", ""),
        "decimal": data.get("decimal", 18),
        "risk_level": data.get("risk_level", 0),
        "updated_at": data.get("updated_at"),
    }


def get_risk_report(token: str, chain: str) -> dict:
    """获取合约风险报告"""
    result = run_ave_rest("risk", "--address", token, "--chain", chain)
    
    if "error" in result:
        return {"error": result["error"]}
    
    # API 返回结构：result["data"]
    data = result.get("data", {})
    
    if isinstance(data, dict):
        return {
            "is_honeypot": data.get("is_honeypot", False),
            "honeypot_score": _safe_float(data.get("honeypot_score", 100)),
            "buy_tax": _safe_float(data.get("buy_tax", 0)),
            "sell_tax": _safe_float(data.get("sell_tax", 0)),
            "transfer_tax": _safe_float(data.get("transfer_tax", 0)),
            "risk_level": data.get("risk_level", "UNKNOWN"),
            "risk_score": _safe_float(data.get("risk_score", 50)),
            "owner": data.get("owner", ""),
            "ownership_renounced": data.get("ownership_renounced", False),
            "owner_address": data.get("owner_address", ""),
            "has_mint_method": data.get("has_mint_method", False),
            "has_black_method": data.get("has_black_method", False),
            "has_restrict_method": data.get("has_restrict_method", False),
            "total_fake_token": data.get("total_fake_token", False),
            "hidden_owner": data.get("hidden_owner", False),
            "transfer_pause": data.get("transfer_pause", False),
            "can_take_back_ownership": data.get("can_take_back_ownership", False),
            "top_holder_percent": _safe_float(data.get("top_holder_percent", 0)),
            "is_in_dex": data.get("is_in_dex", False),
            "is_in_ipo": data.get("is_in_ipo", False),
            "is_locked": data.get("is_locked", False),
            "locked_amount": data.get("locked_amount", "0"),
            "lock_type": data.get("lock_type", ""),
            "created_block_number": data.get("created_block_number", 0),
            "deployer": data.get("deployer", ""),
            "deployer_address": data.get("deployer_address", ""),
            # 扩展字段（可能存在）
            "buy_tax_24h": _safe_float(data.get("buy_tax_24h", data.get("buy_tax", 0))),
            "sell_tax_24h": _safe_float(data.get("sell_tax_24h", data.get("sell_tax", 0))),
        }
    
    return {}


def get_holders(token: str, chain: str, limit: int = 20) -> list:
    """获取持仓分布（Top 20）"""
    result = run_ave_rest(
        "holders",
        "--address", token,
        "--chain", chain,
        "--limit", str(limit),
        "--sort-by", "balance"
    )
    
    if "error" in result:
        return []
    
    raw_data = result.get("data", [])
    if isinstance(raw_data, dict):
        raw_data = raw_data.get("holders", [])
    if not isinstance(raw_data, list):
        raw_data = []
    
    holders = []
    for h in raw_data:
        holders.append({
            "address": h.get("holder", h.get("address", "")),
            "balance": _safe_float(h.get("balance_usd", h.get("balance", 0))),
            "balance_ratio": _safe_float(h.get("balance_ratio", 0)),
            "percent": _safe_float(h.get("balance_ratio", 0)) * 100,
            "tags": h.get("new_tags", []) or [],
        })
    
    return holders


def get_recent_txs(token: str, chain: str, pair: str, limit: int = 100) -> list:
    """获取最近交易记录（用于买卖比分析）"""
    if not pair:
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
    
    raw_data = result.get("data", {})
    if isinstance(raw_data, dict):
        raw_data = raw_data.get("txs", [])
    if not isinstance(raw_data, list):
        raw_data = []
    
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


# ============================================================
# 第一维度：合约安全评分
# ============================================================

def analyze_contract_safety(risk_data: dict) -> dict:
    """
    合约安全评分（0~100）
    
    致命问题直接拉低分数：
    - 蜜罐 → 立即返回 0 分
    - 黑名单方法 → 大幅扣分
    
    扣分项：
    - 买卖税率
    - 未放弃所有权
    - Mint 方法
    - Top Holder 集中
    """
    score = 100
    deductions = []
    warnings = []
    
    # 致命问题
    # is_honeypot: 1 = 是蜜罐，-1/0 = 不是蜜罐（-1表示明确非蜜罐）
    if risk_data.get("is_honeypot") == 1:
        return {
            "score": 0,
            "level": "🚨 致命",
            "deductions": ["🚨 蜜罐合约 — 代币无法卖出"],
            "warnings": [],
            "details": {
                "is_honeypot": risk_data.get("is_honeypot") == 1,
                "honeypot_score": risk_data.get("honeypot_score", 0),
            },
            "summary": "该代币被检测为蜜罐合约（honeypot），代币可能无法正常卖出，强烈建议不要购买。"
        }
    
    if risk_data.get("has_black_method", False):
        score -= 40
        deductions.append("🚨 存在黑名单方法（可冻结任意地址）")
    
    if risk_data.get("hidden_owner", False):
        score -= 20
        deductions.append("⚠️ 存在隐藏 Owner（可能存在后门）")
    
    if risk_data.get("can_take_back_ownership", False):
        score -= 15
        deductions.append("⚠️ 可以收回所有权（可能有权限风险）")
    
    # 税费检查
    buy_tax = risk_data.get("buy_tax", 0)
    sell_tax = risk_data.get("sell_tax", 0)
    
    if buy_tax > 50 or sell_tax > 50:
        score -= 50
        deductions.append(f"🚨 极高税费（买{buy_tax}%/卖{sell_tax}%）— 几乎无法交易")
    elif buy_tax > 20 or sell_tax > 20:
        score -= 30
        deductions.append(f"🔴 高税费（买{buy_tax}%/卖{sell_tax}%）— 交易成本极高")
    elif buy_tax > 10 or sell_tax > 10:
        score -= 15
        deductions.append(f"🟠 中高税费（买{buy_tax}%/卖{sell_tax}%）— 需注意")
    elif buy_tax > 5 or sell_tax > 5:
        score -= 8
        deductions.append(f"🟡 中等税费（买{buy_tax}%/卖{sell_tax}%）")
    
    # 所有权检查
    if not risk_data.get("ownership_renounced", False):
        owner = risk_data.get("owner_address", "")
        if owner and owner != "0x0000000000000000000000000000000000000000":
            score -= 15
            deductions.append(f"⚠️ 所有权未放弃（Owner: {format_address(owner)}）")
    
    # Mint 方法
    if risk_data.get("has_mint_method", False):
        score -= 15
        deductions.append("⚠️ 存在 Mint 方法（可无限增发）")
    
    # 转让暂停
    if risk_data.get("transfer_pause", False):
        score -= 10
        deductions.append("⚠️ 转让已被暂停")
    
    # Top Holder 集中
    top_holder_pct = risk_data.get("top_holder_percent", 0)
    if top_holder_pct > 50:
        score -= 15
        deductions.append(f"⚠️ Top Holder 占比 {top_holder_pct:.1f}%（高集中度）")
    elif top_holder_pct > 30:
        score -= 8
        deductions.append(f"🟡 Top Holder 占比 {top_holder_pct:.1f}%（中等集中）")
    
    # 虚假代币
    if risk_data.get("total_fake_token", False):
        score -= 30
        deductions.append("🚨 虚假代币检测")
    
    # 锁定检查
    if risk_data.get("is_locked", False):
        lock_info = f"流动性锁定（{risk_data.get('lock_type', '未知')}）"
        warnings.append(f"🔒 {lock_info}")
    
    # 最终分数
    score = max(0, min(100, score))
    
    if score >= 85:
        level = "🟢 安全"
        summary = "合约安全，无明显风险。"
    elif score >= 65:
        level = "🟡 基本安全"
        summary = "存在一些风险点，请注意上述问题。"
    elif score >= 40:
        level = "🟠 风险较高"
        summary = "存在多项风险，建议谨慎或避免。"
    else:
        level = "🔴 高风险"
        summary = "多项严重风险，不建议购买。"
    
    return {
        "score": round(score, 1),
        "level": level,
        "deductions": deductions,
        "warnings": warnings,
        "details": {
            "is_honeypot": risk_data.get("is_honeypot", False),
            "buy_tax": buy_tax,
            "sell_tax": sell_tax,
            "ownership_renounced": risk_data.get("ownership_renounced", False),
            "has_mint_method": risk_data.get("has_mint_method", False),
            "has_black_method": risk_data.get("has_black_method", False),
            "top_holder_percent": top_holder_pct,
            "transfer_pause": risk_data.get("transfer_pause", False),
            "is_locked": risk_data.get("is_locked", False),
        },
        "summary": summary
    }


# ============================================================
# 第二维度：流动性评估
# ============================================================

def analyze_liquidity(token_detail: dict) -> dict:
    """
    流动性评分（0~100）
    
    评估维度：
    - TVL 绝对值
    - TVL / FDV 比值（流动性健康度）
    - 24h 成交量活跃度（成交量 / TVL）
    """
    score = 50
    deductions = []
    warnings = []
    details = {}
    
    tvl = token_detail.get("tvl", 0)
    fdv = token_detail.get("fdv", 1)  # 避免除零
    volume_24h = token_detail.get("tx_volume_u_24h", 0)
    
    # TVL 绝对值评分
    if tvl > 10_000_000:
        score += 20
    elif tvl > 1_000_000:
        score += 15
    elif tvl > 500_000:
        score += 10
    elif tvl > 100_000:
        score += 5
    elif tvl > 10_000:
        score -= 10
    else:
        score -= 20
        deductions.append(f"TVL 过低（${format_amount(tvl)}），可能存在流动性风险")
    
    details["tvl"] = tvl
    
    # TVL / FDV 比值（衡量估值合理性）
    tvl_fdv_ratio = tvl / fdv if fdv > 0 else 0
    details["tvl_fdv_ratio"] = round(tvl_fdv_ratio, 3)
    
    if tvl_fdv_ratio > 1.0:
        score += 15  # TVL 高于 FDV，非常健康
        warnings.append(f"✅ TVL/FDV = {tvl_fdv_ratio:.2f}（非常健康）")
    elif tvl_fdv_ratio > 0.5:
        score += 10
    elif tvl_fdv_ratio > 0.2:
        score += 5
    elif tvl_fdv_ratio > 0.05:
        score -= 10
        deductions.append(f"TVL/FDV 比值过低（{tvl_fdv_ratio:.2%}），可能存在估值泡沫")
    else:
        score -= 20
        deductions.append(f"TVL/FDV 比值极低（{tvl_fdv_ratio:.2%}），死亡螺旋风险")
    
    # 24h 成交量活跃度
    if tvl > 0:
        vol_ratio = volume_24h / tvl
        details["vol_tvl_ratio"] = round(vol_ratio, 4)
        
        if vol_ratio > 0.5:
            score += 10  # 非常活跃
        elif vol_ratio > 0.2:
            score += 5
        elif vol_ratio > 0.05:
            score -= 5
            warnings.append(f"⚠️ 成交量/TVL 偏低（{vol_ratio:.1%}），交易活跃度不足")
        else:
            score -= 15
            deductions.append(f"成交量极低（{vol_ratio:.1%} of TVL），可能是僵尸币")
    else:
        details["vol_tvl_ratio"] = 0
    
    # 最终分数
    score = max(0, min(100, score))
    
    if score >= 80:
        level = "🟢 优秀"
        summary = "流动性充足，交易活跃。"
    elif score >= 60:
        level = "🟢 良好"
        summary = "流动性良好，可正常交易。"
    elif score >= 40:
        level = "🟡 一般"
        summary = "流动性一般，注意仓位大小。"
    else:
        level = "🔴 不足"
        summary = "流动性不足，可能存在滑点风险或无法正常退出。"
    
    return {
        "score": round(score, 1),
        "level": level,
        "deductions": deductions,
        "warnings": warnings,
        "details": details,
        "summary": summary
    }


# ============================================================
# 第三维度：持仓分布
# ============================================================

def analyze_holders_distribution(holders: list, token_detail: dict = None) -> dict:
    """
    持仓分布评分（0~100）
    
    评估维度：
    - Top5 / Top10 持仓占比
    - 是否存在 CEX 充币地址
    - 是否有明显项目方持仓
    """
    score = 100
    deductions = []
    warnings = []
    details = {}
    
    if not holders:
        return {
            "score": 50,
            "level": "⚪ 数据不足",
            "deductions": ["无法获取持仓数据"],
            "warnings": [],
            "details": {},
            "summary": "持仓数据暂不可用。"
        }
    
    # 计算 Top 占比
    top5_pct = sum(h.get("percent", 0) for h in holders[:5]) / 100
    top10_pct = sum(h.get("percent", 0) for h in holders[:10]) / 100
    
    details["top5_pct"] = round(top5_pct * 100, 2)
    details["top10_pct"] = round(top10_pct * 100, 2)
    details["holders_count"] = len(holders)
    
    # Top10 集中度评分
    if top10_pct > 0.80:
        score -= 40
        deductions.append(f"🔴 Top10 持仓 {top10_pct*100:.1f}% — 极高控盘")
    elif top10_pct > 0.60:
        score -= 25
        deductions.append(f"🟠 Top10 持仓 {top10_pct*100:.1f}% — 高控盘")
    elif top10_pct > 0.40:
        score -= 15
        deductions.append(f"🟡 Top10 持仓 {top10_pct*100:.1f}% — 中等集中")
    elif top10_pct > 0.25:
        score -= 5
    else:
        score += 10  # 分散持仓加分
    
    # 检测 CEX 充币地址
    cex_tags = {"Cex", "Exchange", "Binance", "Okex", "Huobi", "Coinbase", "Kraken", "Kucoin"}
    cex_holders = []
    project_holders = []
    
    for h in holders[:10]:
        tags = h.get("tags", [])
        addr = h.get("address", "")
        
        # 检测 CEX
        for tag in tags:
            if any(ct.lower() in str(tag).lower() for ct in cex_tags):
                cex_holders.append({
                    "address": addr,
                    "percent": h.get("percent", 0),
                    "tag": tag
                })
                break
        
        # 检测项目方（通过地址特征）
        # 已知项目方可能留下的地址模式
        if any(x in addr.lower() for x in ["dead", "burn", "team", "founder", "dev"]):
            project_holders.append({
                "address": addr,
                "percent": h.get("percent", 0)
            })
    
    details["cex_holders"] = cex_holders
    details["project_holders"] = project_holders
    
    if cex_holders:
        cex_pct = sum(h["percent"] for h in cex_holders)
        if cex_pct > 30:
            score -= 15
            deductions.append(f"⚠️ CEX 充币地址占比 {cex_pct:.1f}% — 存在潜在抛压")
        elif cex_pct > 15:
            score -= 8
            warnings.append(f"🟡 检测到 CEX 充币地址（{cex_pct:.1f}%）")
    
    if project_holders:
        project_pct = sum(h["percent"] for h in project_holders)
        if project_pct > 20:
            score -= 10
            warnings.append(f"⚠️ 项目方/团队地址持仓 {project_pct:.1f}% — 解锁风险")
    
    # 检测是否是貔貅盘（Top1 占比过高）
    if holders:
        top1_pct = holders[0].get("percent", 0) / 100
        if top1_pct > 0.50:
            score -= 20
            deductions.append(f"🔴 Top1 持仓 {top1_pct*100:.1f}% — 单地址极高控盘")
        elif top1_pct > 0.30:
            score -= 10
            warnings.append(f"🟠 Top1 持仓 {top1_pct*100:.1f}% — 单地址集中")
    
    # 最终分数
    score = max(0, min(100, score))
    
    if score >= 75:
        level = "🟢 分散"
        summary = "持仓分布健康，无明显控盘迹象。"
    elif score >= 50:
        level = "🟡 较集中"
        summary = "持仓有一定集中度，需关注大户动向。"
    elif score >= 25:
        level = "🟠 高集中"
        summary = "持仓高度集中，存在明显控盘风险。"
    else:
        level = "🔴 极高集中"
        summary = "持仓极度集中，不建议大仓位介入。"
    
    return {
        "score": round(score, 1),
        "level": level,
        "deductions": deductions,
        "warnings": warnings,
        "details": details,
        "summary": summary
    }


# ============================================================
# 第四维度：市场行为
# ============================================================

def analyze_market_behavior(txs: list, token_detail: dict = None) -> dict:
    """
    市场行为评分（0~100）
    
    评估维度：
    - 买卖比（buy tx / sell tx）
    - 大额交易占比
    - 波动率
    - 僵尸币检测（24h 无交易）
    """
    score = 50
    deductions = []
    warnings = []
    details = {}
    
    # 僵尸币检测
    tx_count_24h = 0
    if token_detail:
        tx_count_24h = token_detail.get("tx_count_24h", 0)
        price_change_24h = token_detail.get("price_change_24h", 0)
        price_change_1h = token_detail.get("price_change_1h", 0)
    else:
        price_change_24h = 0
        price_change_1h = 0
    
    details["tx_count_24h"] = tx_count_24h
    
    if tx_count_24h < 10:
        score -= 30
        deductions.append(f"🔴 24h Tx 数仅 {tx_count_24h} — 疑似僵尸币")
    elif tx_count_24h < 50:
        score -= 15
        warnings.append(f"🟡 24h Tx 数偏低（{tx_count_24h}），活跃度不足")
    else:
        score += 10
    
    # 买卖方向分析（从 txs 数据）
    if txs:
        buy_count = 0
        sell_count = 0
        buy_volume = 0.0
        sell_volume = 0.0
        large_tx_count = 0
        large_tx_threshold = 1000  # $1000 以上算大额
        
        for tx in txs:
            amount = tx.get("amount_usd", 0)
            from_sym = tx.get("from_token_symbol", "").upper()
            to_sym = tx.get("to_token_symbol", "").upper()
            
            # 简单判断：如果成交的不是稳定币或主流币，认为是卖出
            if from_sym in ["USDT", "USDC", "BUSD", "DAI", "BNB", "ETH", "WETH"]:
                buy_count += 1
                buy_volume += amount
            else:
                sell_count += 1
                sell_volume += amount
            
            if amount > large_tx_threshold:
                large_tx_count += 1
        
        total_buysell = buy_count + sell_count
        if total_buysell > 0:
            buy_ratio = buy_count / total_buysell
        else:
            buy_ratio = 0.5
        
        details["buy_count"] = buy_count
        details["sell_count"] = sell_count
        details["buy_volume"] = round(buy_volume, 2)
        details["sell_volume"] = round(sell_volume, 2)
        details["buy_ratio"] = round(buy_ratio * 100, 1)
        details["large_tx_count"] = large_tx_count
        
        # 买卖比评分
        if buy_ratio > 0.7:
            score += 15
            warnings.append(f"✅ 买入主导（{buy_ratio*100:.0f}% 买单）")
        elif buy_ratio > 0.55:
            score += 8
        elif buy_ratio < 0.3:
            score -= 15
            deductions.append(f"🔴 卖出主导（{buy_ratio*100:.0f}% 卖单）— 抛压明显")
        elif buy_ratio < 0.45:
            score -= 8
            warnings.append(f"🟡 偏向卖出（{buy_ratio*100:.0f}% 买单）")
        
        # 大额交易占比
        if total_buysell > 0:
            large_tx_ratio = large_tx_count / total_buysell
            if large_tx_ratio > 0.3:
                score -= 10
                warnings.append(f"⚠️ 大额交易占比高（{large_tx_ratio*100:.0f}%）— 大户主导")
    else:
        # 无交易数据时的默认值
        details["buy_count"] = 0
        details["sell_count"] = 0
        details["buy_ratio"] = 50
    
    # 价格波动率分析
    if abs(price_change_24h) > 30:
        score -= 15
        deductions.append(f"🔴 24h 价格波动 {price_change_24h:.1f}% — 极高波动")
    elif abs(price_change_24h) > 15:
        score -= 8
        warnings.append(f"🟡 24h 价格波动较大（{price_change_24h:.1f}%）")
    elif abs(price_change_24h) < 2:
        score -= 5
        warnings.append(f"⚠️ 价格几乎无波动（{price_change_24h:.1f}%）— 可能横盘")
    
    # 最终分数
    score = max(0, min(100, score))
    
    if score >= 75:
        level = "🟢 活跃健康"
        summary = "市场活跃度良好，买卖均衡。"
    elif score >= 50:
        level = "🟡 一般"
        summary = "市场活跃度一般，需关注方向选择。"
    elif score >= 25:
        level = "🟠 偏弱"
        summary = "市场偏弱，卖出压力较大。"
    else:
        level = "🔴 极弱"
        summary = "市场极弱，存在严重抛压或僵尸币风险。"
    
    return {
        "score": round(score, 1),
        "level": level,
        "deductions": deductions,
        "warnings": warnings,
        "details": details,
        "summary": summary
    }


# ============================================================
# 第五维度：基本面
# ============================================================

def analyze_basic_factors(token_detail: dict) -> dict:
    """
    基本面评分（0~100）
    
    评估维度：
    - 市值规模
    - FDV 合理性
    - 发行时间（老币 vs 新币）
    """
    score = 50
    deductions = []
    warnings = []
    details = {}
    
    market_cap = token_detail.get("market_cap", 0)
    fdv = token_detail.get("fdv", 0)
    price = token_detail.get("current_price_usd", 0)
    holders = token_detail.get("holders", 0)
    updated_at = token_detail.get("updated_at", 0)
    
    # 市值评分
    if market_cap > 10_000_000:
        score += 15  # 大市值
    elif market_cap > 1_000_000:
        score += 10  # 中市值
    elif market_cap > 100_000:
        score += 5   # 小市值
    elif market_cap > 10_000:
        score -= 5  # 微市值
    else:
        score -= 15
        deductions.append(f"🔴 市值极低（${format_amount(market_cap)}）— 退出风险大")
    
    details["market_cap"] = market_cap
    
    # FDV 合理性
    if fdv > 0 and market_cap > 0:
        fdv_ratio = fdv / market_cap
        details["fdv_ratio"] = round(fdv_ratio, 2)
        
        if fdv_ratio > 10:
            score -= 15
            deductions.append(f"🔴 FDV/市值 = {fdv_ratio:.0f}x — 解锁抛压巨大")
        elif fdv_ratio > 5:
            score -= 10
            warnings.append(f"🟠 FDV/市值 = {fdv_ratio:.0f}x — 未来抛压较大")
        elif fdv_ratio < 0.5:
            score += 5  # FDV < 市值，说明有部分token被burn或锁定
    else:
        details["fdv_ratio"] = None
    
    # 持有人数
    if holders:
        if holders > 10000:
            score += 5
        elif holders > 1000:
            score += 3
        elif holders < 50:
            score -= 10
            deductions.append(f"🟡 持有人数仅 {holders} — 社区基础薄弱")
        
        details["holders"] = holders
    
    # 价格合理性
    if price < 0.0000001:
        score -= 10
        warnings.append(f"🟡 价格极低（${price}），注意精度风险")
    elif price > 10000:
        score -= 5
        warnings.append(f"🟡 价格较高（${format_price(price)})，注意单位风险")
    
    # 最终分数
    score = max(0, min(100, score))
    
    if score >= 75:
        level = "🟢 优秀"
        summary = "基本面良好，规模和结构健康。"
    elif score >= 50:
        level = "🟡 一般"
        summary = "基本面一般，无明显优势或劣势。"
    else:
        level = "🔴 较弱"
        summary = "基本面较弱，存在估值或规模风险。"
    
    return {
        "score": round(score, 1),
        "level": level,
        "deductions": deductions,
        "warnings": warnings,
        "details": details,
        "summary": summary
    }


# ============================================================
# 综合评分
# ============================================================

def calculate_health_score(
    contract: dict,
    liquidity: dict,
    holders: dict,
    market: dict,
    basic: dict
) -> dict:
    """
    计算综合健康评分。
    
    权重：
    - 合约安全 30%
    - 流动性 25%
    - 持仓分布 20%
    - 市场行为 15%
    - 基本面 10%
    """
    total = (
        contract.get("score", 50) * WEIGHT_CONTRACT +
        liquidity.get("score", 50) * WEIGHT_LIQUIDITY +
        holders.get("score", 50) * WEIGHT_HOLDERS +
        market.get("score", 50) * WEIGHT_MARKET +
        basic.get("score", 50) * WEIGHT_BASIC
    )
    
    stars = score_to_stars(total)
    
    if total >= 75:
        level = "🟢 低风险"
        recommendation = "可以参与，注意仓位管理。"
    elif total >= 55:
        level = "🟡 中等风险"
        recommendation = "可以小仓位参与，建议设置止损。"
    elif total >= 35:
        level = "🟠 高风险"
        recommendation = "谨慎参与，不建议大仓位，持严格止损。"
    else:
        level = "🔴 极高风险"
        recommendation = "不建议介入，存在严重风险。"
    
    # 致命问题检测
    if contract.get("score", 100) == 0:
        level = "🚨 危险"
        recommendation = "蜜罐合约，禁止购买。"
    
    return {
        "overall": round(total, 1),
        "stars": stars,
        "level": level,
        "recommendation": recommendation,
        "weights": {
            "contract": f"{int(WEIGHT_CONTRACT*100)}%",
            "liquidity": f"{int(WEIGHT_LIQUIDITY*100)}%",
            "holders": f"{int(WEIGHT_HOLDERS*100)}%",
            "market": f"{int(WEIGHT_MARKET*100)}%",
            "basic": f"{int(WEIGHT_BASIC*100)}%"
        },
        "dimension_scores": {
            "contract": contract.get("score", 50),
            "liquidity": liquidity.get("score", 50),
            "holders": holders.get("score", 50),
            "market": market.get("score", 50),
            "basic": basic.get("score", 50),
        }
    }


# ============================================================
# 输出格式化
# ============================================================

def format_health_report(
    token: str,
    chain: str,
    symbol: str,
    token_detail: dict,
    contract: dict,
    liquidity: dict,
    holders: dict,
    market: dict,
    basic: dict,
    overall: dict
) -> str:
    """
    格式化完整的体检报告。
    """
    price = token_detail.get("current_price_usd")
    price_change = token_detail.get("price_change_24h", 0)
    market_cap = token_detail.get("market_cap", 0)
    tvl = token_detail.get("tvl", 0)
    
    lines = [
        f"╔══════════════════════════════════════╗",
        f"║       🩺 代币体检报告              ║",
        f"╚══════════════════════════════════════╝",
        f"",
        f"🐸 {symbol}（{format_address(token)}）",
        f"🌍 链：{chain.upper()}",
        f"🕐 时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 综合评分：{overall['stars']}（{overall['overall']}/100）",
        f"⚠️ 风险等级：{overall['level']}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"",
    ]
    
    # 1. 合约安全
    lines.extend([
        f"🔒 合约安全 — {contract['score']}/100 {contract['level']}",
    ])
    if contract.get("deductions"):
        for d in contract["deductions"]:
            lines.append(f"  {d}")
    if contract.get("warnings"):
        for w in contract["warnings"]:
            lines.append(f"  {w}")
    if not contract.get("deductions") and not contract.get("warnings"):
        lines.append(f"  ✅ {contract.get('summary', '无异常')}")
    lines.append(f"  {contract.get('summary', '')}")
    lines.append("")
    
    # 2. 流动性
    liq_details = liquidity.get("details", {})
    lines.extend([
        f"💧 流动性 — {liquidity['score']}/100 {liquidity['level']}",
        f"  TVL：{format_amount(liq_details.get('tvl', 0))}",
    ])
    if liq_details.get("tvl_fdv_ratio") is not None:
        lines.append(f"  TVL/FDV：{liq_details['tvl_fdv_ratio']:.2f}")
    if liq_details.get("vol_tvl_ratio"):
        lines.append(f"  成交量/TVL：{liq_details['vol_tvl_ratio']:.1%}")
    if liquidity.get("deductions"):
        for d in liquidity["deductions"]:
            lines.append(f"  {d}")
    if liquidity.get("warnings"):
        for w in liquidity["warnings"]:
            lines.append(f"  {w}")
    if not liquidity.get("deductions") and not liquidity.get("warnings"):
        lines.append(f"  ✅ {liquidity.get('summary', '正常')}")
    lines.append("")
    
    # 3. 持仓分布
    hold_details = holders.get("details", {})
    lines.extend([
        f"👥 持仓分布 — {holders['score']}/100 {holders['level']}",
        f"  Top5：{hold_details.get('top5_pct', 0):.1f}%",
        f"  Top10：{hold_details.get('top10_pct', 0):.1f}%",
    ])
    if hold_details.get("cex_holders"):
        cex_info = ", ".join([
            f"{format_address(h['address'])}({h['percent']:.1f}%)"
            for h in hold_details["cex_holders"][:2]
        ])
        lines.append(f"  🔵 CEX：{cex_info}")
    if holders.get("deductions"):
        for d in holders["deductions"]:
            lines.append(f"  {d}")
    if holders.get("warnings"):
        for w in holders["warnings"]:
            lines.append(f"  {w}")
    if not holders.get("deductions") and not holders.get("warnings"):
        lines.append(f"  ✅ {holders.get('summary', '正常')}")
    lines.append("")
    
    # 4. 市场行为
    mkt_details = market.get("details", {})
    lines.extend([
        f"📈 市场行为 — {market['score']}/100 {market['level']}",
        f"  24h Tx数：{mkt_details.get('tx_count_24h', 0):.0f}",
    ])
    if mkt_details.get("buy_ratio") is not None:
        lines.append(f"  买卖比（量）：{mkt_details['buy_ratio']:.0f}% 买单")
    if mkt_details.get("large_tx_count", 0) > 0:
        lines.append(f"  大额交易：{mkt_details['large_tx_count']} 笔")
    if market.get("deductions"):
        for d in market["deductions"]:
            lines.append(f"  {d}")
    if market.get("warnings"):
        for w in market["warnings"]:
            lines.append(f"  {w}")
    if not market.get("deductions") and not market.get("warnings"):
        lines.append(f"  ✅ {market.get('summary', '正常')}")
    lines.append("")
    
    # 5. 基本面
    basic_details = basic.get("details", {})
    lines.extend([
        f"📦 基本面 — {basic['score']}/100 {basic['level']}",
        f"  市值：{format_amount(basic_details.get('market_cap', 0))}",
    ])
    if basic_details.get("holders"):
        lines.append(f"  持有人：{basic_details['holders']:,}")
    if basic_details.get("fdv_ratio") is not None:
        lines.append(f"  FDV/市值：{basic_details['fdv_ratio']:.0f}x")
    if basic.get("deductions"):
        for d in basic["deductions"]:
            lines.append(f"  {d}")
    if basic.get("warnings"):
        for w in basic["warnings"]:
            lines.append(f"  {w}")
    if not basic.get("deductions") and not basic.get("warnings"):
        lines.append(f"  ✅ {basic.get('summary', '正常')}")
    lines.append("")
    
    # 综合建议
    lines.extend([
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🤖 AI 综合建议",
        f"",
        f"【{overall['level']}】{overall['recommendation']}",
        f"",
        f"💰 当前价格：{format_price(price)}（{format_pct(price_change)} / 24h）",
        f"📊 市值：{format_amount(market_cap)}",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⚠️ 免责声明：以上分析仅供参考，不构成投资建议。",
        f"请DYOR（Do Your Own Research）。",
    ])
    
    return "\n".join(lines)


def format_compact_report(
    token: str,
    chain: str,
    symbol: str,
    overall: dict,
    contract: dict,
    liquidity: dict
) -> str:
    """
    格式化精简版报告。
    """
    lines = [
        f"🩺 {symbol} — {overall['stars']} {overall['level']}",
        f"综合：{overall['overall']}/100 | 合约：{contract['score']} | 流动性：{liquidity['score']}",
        f"{overall['recommendation']}",
    ]
    return "\n".join(lines)


# ============================================================
# 主分析流程
# ============================================================

def analyze(
    token: str,
    chain: str,
    output_format: str = "full"
) -> dict:
    """
    主分析流程。
    """
    # 1. 获取数据
    token_detail = get_token_detail(token, chain)
    if "error" in token_detail:
        return {"error": f"Failed to get token detail: {token_detail['error']}"}
    
    risk_data = get_risk_report(token, chain)
    holders = get_holders(token, chain, limit=20)
    pair = token_detail.get("main_pair", "")
    txs = get_recent_txs(token, chain, pair, limit=100)
    
    # 2. 五维度分析
    contract = analyze_contract_safety(risk_data)
    liquidity = analyze_liquidity(token_detail)
    holders_analysis = analyze_holders_distribution(holders, token_detail)
    market = analyze_market_behavior(txs, token_detail)
    basic = analyze_basic_factors(token_detail)
    
    # 3. 综合评分
    overall = calculate_health_score(
        contract, liquidity, holders_analysis, market, basic
    )
    
    # 4. 格式化输出
    symbol = token_detail.get("symbol", "UNKNOWN")
    
    if output_format == "compact":
        report = format_compact_report(
            token, chain, symbol, overall, contract, liquidity
        )
    else:
        report = format_health_report(
            token, chain, symbol, token_detail,
            contract, liquidity, holders_analysis, market, basic,
            overall
        )
    
    return {
        "token": token,
        "chain": chain,
        "symbol": symbol,
        "token_detail": token_detail,
        "risk_data": risk_data,
        "contract": contract,
        "liquidity": liquidity,
        "holders": holders_analysis,
        "market": market,
        "basic": basic,
        "overall": overall,
        "report": report
    }


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
        description="Ave Guardian — Token Health Reporter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 health_reporter.py 0xf43c8f27754829202d2f66650eb3f6d168c288dc bsc
  python3 health_reporter.py 0xf43c8f27754829202d2f66650eb3f6d168c288dc bsc --output compact
  python3 health_reporter.py 0xf43c8f27754829202d2f66650eb3f6d168c288dc bsc --output json
        """
    )
    
    parser.add_argument("token", help="代币合约地址")
    parser.add_argument("chain", help="链 ID（如 bsc, eth, base）")
    parser.add_argument(
        "--output", "-o",
        choices=["full", "compact", "json"],
        default="full",
        help="输出格式：full=完整报告，compact=精简版，json=原始数据"
    )
    
    args = parser.parse_args()
    
    # 执行分析
    result = analyze(
        token=args.token,
        chain=args.chain,
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
    
    # 同时输出 JSON 到 stderr
    if args.output != "json":
        output = {k: v for k, v in result.items() if k != "report"}
        print(f"\n[DEBUG JSON]\n{json.dumps(output, indent=2, ensure_ascii=False, default=str)}", file=sys.stderr)


if __name__ == "__main__":
    main()
