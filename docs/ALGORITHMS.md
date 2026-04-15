# Ave Guardian 评分算法详解

> 本文档详细说明 Ave Guardian 各模块使用的评分算法、阈值设定依据和标定方法。

---

## 一、庄家控盘评分算法（Whale & Manipulator Detector）

### 1.1 持仓集中度评分（Concentration Score）

**数据来源**：`holders` 接口，Top N 持仓地址

**算法**：

```python
def concentration_score(holders_data, total_supply=None):
    """
    holders_data: list of {address, balance, percent}
    total_supply: 可选，如果 holders_data 包含 percent 则不需要
    """
    top5_balance = sum(h.balance for h in holders_data[:5])
    top10_balance = sum(h.balance for h in holders_data[:10])

    if total_supply:
        top5_pct = top5_balance / total_supply
        top10_pct = top10_balance / total_supply
    else:
        top5_pct = sum(h.percent for h in holders_data[:5]) / 100
        top10_pct = sum(h.percent for h in holders_data[:10]) / 100

    # 持仓集中度评分（0~100）
    # 线性映射：10% → 0分，90% → 100分
    concentration = (top10_pct - 0.10) / 0.80 * 100
    concentration = max(0, min(100, concentration))

    return {
        'top5_pct': round(top5_pct * 100, 2),
        'top10_pct': round(top10_pct * 100, 2),
        'score': round(concentration, 1),
        'level': concentration_to_level(top10_pct)
    }

def concentration_to_level(pct):
    if pct > 0.70: return '极高控盘'
    elif pct > 0.50: return '高控盘'
    elif pct > 0.30: return '中等集中'
    else: return '持仓分散'
```

**阈值标定依据**：

| 集中度 | Top10 占比 | 庄家行为特征 | 参考项目 |
|--------|-----------|------------|---------|
| > 70% | 极高控盘 | 单地址可能超 30%，出货时跌幅剧烈 | 典型貔貅盘 |
| 50~70% | 高控盘 | 2~3 个大地址联合控盘 | 多数小市值币 |
| 30~50% | 中等集中 | 庄家有一定影响力但不绝对 | 中型市值正常项目 |
| < 30% | 持仓分散 | 无明显单一控盘方 | BTC/ETH 等主流币 |

### 1.2 交易行为评分（Behavioral Score）

**数据来源**：`txs` 接口（swap 交易记录）

```python
def behavioral_score(txs_data, window_hours=24):
    """
    分析窗口期内的交易行为模式
    txs_data: list of {side, amount_usd, sender, time}
    """
    now = time.time()
    window_start = now - window_hours * 3600

    # 按 sender 聚类
    address_flows = defaultdict(lambda: {'buy': 0, 'sell': 0, 'count': 0})
    for tx in txs_data:
        if tx.time < window_start:
            continue
        addr = tx.sender
        if tx.side == 'buy':
            address_flows[addr]['buy'] += tx.amount_usd
        else:
            address_flows[addr]['sell'] += tx.amount_usd
        address_flows[addr]['count'] += 1

    # 找出主导地址（交易量 > $5,000 的地址）
    dominant_addrs = [
        (addr, flow) for addr, flow in address_flows.items()
        if (flow['buy'] + flow['sell']) > 5000
    ]

    if not dominant_addrs:
        return {'pattern': '无明显主导', 'score': 10, 'level': '🟢'}

    # 按主导程度排序
    dominant_addrs.sort(key=lambda x: x[1]['buy'] + x[1]['sell'], reverse=True)
    top_addr = dominant_addrs[0][0]
    top_flow = dominant_addrs[0][1]

    buy_ratio = top_flow['buy'] / (top_flow['buy'] + top_flow['sell'])
    net_flow = top_flow['buy'] - top_flow['sell']

    # 判断模式
    if buy_ratio > 0.7 and net_flow > 10000:
        pattern = '吸筹'
        score = 75  # 庄家买入，散户此时跟进有机会
    elif buy_ratio < 0.3 and net_flow < -10000:
        pattern = '出货'
        score = 85  # 庄家卖出，风险极高
    elif buy_ratio > 0.6:
        pattern = '拉升'
        score = 65
    elif std_dev([f['buy']+f['sell'] for _, f in dominant_addrs[:3]]) / \
          avg([f['buy']+f['sell'] for _, f in dominant_addrs[:3]]) > 0.8:
        pattern = '洗盘'
        score = 55
    else:
        pattern = '混合博弈'
        score = 40

    return {
        'pattern': pattern,
        'score': score,
        'top_address': top_addr[:8] + '...',
        'net_flow_24h_usd': round(net_flow, 2),
        'buy_ratio_24h': round(buy_ratio * 100, 1),
        'level': pattern_to_level(pattern)
    }

def pattern_to_level(pattern):
    return {
        '吸筹': '🟡',   # 谨慎但可能有机会
        '拉升': '🟠',   # 风险上升
        '出货': '🔴',   # 高风险
        '洗盘': '🟡',   # 不确定方向
        '混合博弈': '🟢', # 相对正常
        '无明显主导': '🟢',
    }[pattern]
```

### 1.3 K线形态评分（Kline Pattern Score）

**数据来源**：`kline-token` 接口（OHLCV）

```python
def kline_pattern_score(klines):
    """
    分析最近 N 根 K 线的形态
    klines: list of {open, high, low, close, volume, time}
    """
    closes = [k['close'] for k in klines]
    volumes = [k['volume'] for k in klines]

    # 计算各项指标
    recent_vol_avg = mean(closes[-3:])
    prev_vol_avg = mean(closes[-8:-3]) if len(klines) >= 8 else mean(closes[:-3])

    price_change_6h = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(klines) >= 6 else 0
    volume_ratio = recent_vol_avg / prev_vol_avg if prev_vol_avg > 0 else 1
    price_std = std_dev(closes[-6:]) / mean(closes[-6:]) if len(closes) >= 6 else 0

    # 形态识别
    patterns = []

    # 放量上涨
    if volume_ratio > 1.5 and price_change_6h > 5:
        patterns.append('放量拉升')

    # 缩量横盘
    if volume_ratio < 0.6 and price_std < 0.02:
        patterns.append('缩量横盘')

    # 价涨量跌（背离）
    if price_change_6h > 3 and volume_ratio < 0.8:
        patterns.append('量价背离')

    # 急涨急跌
    if max(closes[-6:]) / min(closes[-6:]) > 1.15:
        patterns.append('宽幅震荡')

    # 综合评分
    if '放量拉升' in patterns:
        score = 70
    elif '量价背离' in patterns:
        score = 30  # 不可持续
    elif '缩量横盘' in patterns:
        score = 45  # 方向不明
    elif '宽幅震荡' in patterns:
        score = 40
    else:
        score = 50  # 正常

    return {
        'patterns': patterns if patterns else ['正常'],
        'price_change_6h_pct': round(price_change_6h, 2),
        'volume_ratio': round(volume_ratio, 2),
        'volatility': round(price_std * 100, 2),
        'score': score
    }
```

### 1.4 综合控盘评分（Manipulation Score）

```python
def manipulation_score(concentration_score, behavioral_score, kline_score):
    """
    三层加权综合评分
    concentration_score: 0~100
    behavioral_score: 0~100
    kline_score: 0~100
    """
    score = (
        concentration_score * 0.40 +
        behavioral_score * 0.40 +
        kline_score * 0.20
    )

    if score > 70:
        level = '🔴 极高控盘风险'
        action = '不建议介入，或极轻仓'
    elif score > 55:
        level = '🟠 高控盘迹象'
        action = '谨慎持仓，严格止损'
    elif score > 35:
        level = '🟡 中等控盘'
        action = '可参与，跟随庄家方向'
    else:
        level = '🟢 无明显控盘'
        action = '正常参与'

    return {
        'score': round(score, 1),
        'level': level,
        'action': action,
        'signals': {
            'concentration': concentration_score,
            'behavioral': behavioral_score,
            'kline': kline_score
        }
    }
```

---

## 二、Meme 叙事评分算法（Meme & Narrative Scanner）

### 2.1 爆发潜力评分（Narrative Score）

```python
def narrative_score(token_detail, holders_data=None, txs_data=None):
    """
    计算 Meme 币的爆发潜力评分（0~100）
    """
    signals = {}

    # 1. 成交量增速信号（权重 30%）
    vol_score = volume_signal(token_detail)  # 0~100
    signals['volume'] = vol_score

    # 2. 持有人增长信号（权重 25%）
    holder_score = holder_signal(token_detail, holders_data)  # 0~100
    signals['holder'] = holder_score

    # 3. 新地址加速信号（权重 20%）
    address_score = address_signal(tx_data)  # 0~100
    signals['address'] = address_score

    # 4. K 线突破信号（权重 15%）
    kline_score = kline_breakout_signal(token_detail)  # 0~100
    signals['kline'] = kline_score

    # 5. 平台标签信号（权重 10%）
    platform_score = platform_tag_signal(token_detail)  # 0~100
    signals['platform'] = platform_score

    # 加权综合
    total = (
        vol_score * 0.30 +
        holder_score * 0.25 +
        address_score * 0.20 +
        kline_score * 0.15 +
        platform_score * 0.10
    )

    return {
        'score': round(total, 1),
        'level': score_to_narrative_level(total),
        'signals': signals,
        'recommendation': level_to_recommendation(total)
    }


def volume_signal(token_detail):
    """
    成交量增速评分
    参考：24h 成交量对比前一天
    """
    vol_24h = token_detail.get('tx_volume_u_24h', 0)
    # 如果有历史对比数据，计算增速；否则用绝对值估算
    vol_change_pct = token_detail.get('vol_change_24h_pct', None)

    if vol_change_pct is not None:
        if vol_change_pct > 500: return 95
        elif vol_change_pct > 300: return 85
        elif vol_change_pct > 200: return 75
        elif vol_change_pct > 100: return 65
        elif vol_change_pct > 50: return 55
        elif vol_change_pct > 20: return 45
        elif vol_change_pct > 0: return 35
        else: return 20
    else:
        # 绝对值估算（无历史对比时）
        if vol_24h > 1_000_000: return 80
        elif vol_24h > 500_000: return 70
        elif vol_24h > 100_000: return 55
        elif vol_24h > 10_000: return 40
        else: return 25


def holder_signal(token_detail, holders_data):
    """
    持有人增长评分
    """
    holders_count = token_detail.get('holders', 0)
    holders_change_pct = token_detail.get('holders_change_pct', None)

    if holders_change_pct is not None:
        if holders_change_pct > 100: return 90
        elif holders_change_pct > 50: return 75
        elif holders_change_pct > 20: return 60
        elif holders_change_pct > 10: return 50
        else: return 35
    else:
        # 绝对值估算
        if holders_count > 10000: return 80
        elif holders_count > 5000: return 65
        elif holders_count > 1000: return 50
        elif holders_count > 100: return 35
        else: return 20


def address_signal(tx_data):
    """
    新地址加速入场评分
    需要对比不同时段的平均新地址数
    """
    # 实现：统计近期 swap 中的新地址占比
    # 简化版：直接用 swap 数量估算
    swap_count = len(tx_data) if tx_data else 0
    if swap_count > 500: return 85
    elif swap_count > 200: return 70
    elif swap_count > 50: return 50
    else: return 30


def kline_breakout_signal(token_detail):
    """
    K 线突破形态评分
    """
    price_change_1h = token_detail.get('price_change_1h', 0)
    price_change_5m = token_detail.get('price_change_5m', 0)

    # 同时检测 5m 和 1h 的短时突破
    max_change = max(abs(price_change_1h), abs(price_change_5m))
    direction = 'up' if price_change_1h > 0 else 'down'

    if max_change > 15:
        return 90 if direction == 'up' else 30  # 向上突破加分，向下减分
    elif max_change > 10:
        return 80 if direction == 'up' else 35
    elif max_change > 5:
        return 65 if direction == 'up' else 45
    elif max_change > 2:
        return 55 if direction == 'up' else 50
    else:
        return 45


def platform_tag_signal(token_detail):
    """
    平台标签信号评分
    出现在哪些平台标签？
    """
    tags = token_detail.get('platform_tags', [])
    score = 40  # 默认无标签

    for tag in tags:
        if tag in ['pump_in_hot', 'fourmeme_in_hot']:
            score += 40
        elif tag in ['pump_in_new', 'bonk_in_hot']:
            score += 25
        elif tag in ['meme', 'new', 'hot']:
            score += 15

    return min(100, score)


def score_to_narrative_level(score):
    if score >= 70: return '🔥 强烈关注'
    elif score >= 55: return '🟠 值得关注'
    elif score >= 40: return '🟡 观察中'
    else: return '⚪ 普通'


def level_to_recommendation(score):
    if score >= 70:
        return '综合信号强，建议关注是否持续突破'
    elif score >= 55:
        return '有信号出现，需进一步验证趋势持续性'
    elif score >= 40:
        return '存在部分信号，但需谨慎评估'
    else:
        return '信号不足，建议观望'
```

---

## 三、体检报告综合评分（Token Health Score）

```python
def token_health_score(token_detail, risk_data, holders_data, txs_data):
    """
    代币体检综合评分（0~100）
    权重：合约安全 30% | 流动性 25% | 持仓分布 20% | 市场行为 15% | 基本面 10%
    """
    contract = contract_score(risk_data)
    liquidity = liquidity_score(token_detail)
    holders = holder_distribution_score(holders_data, token_detail)
    market = market_behavior_score(txs_data, token_detail)
    basic = basic_score(token_detail)

    total = (
        contract['score'] * 0.30 +
        liquidity['score'] * 0.25 +
        holders['score'] * 0.20 +
        market['score'] * 0.15 +
        basic['score'] * 0.10
    )

    stars = score_to_stars(total)
    level = score_to_health_level(total)

    return {
        'overall': round(total, 1),
        'stars': stars,
        'level': level,
        'dimensions': {
            'contract': contract,
            'liquidity': liquidity,
            'holders': holders,
            'market': market,
            'basic': basic
        }
    }


def contract_score(risk_data):
    """
    合约安全评分（0~100）
    致命问题直接拉低到 0
    """
    score = 100

    # 致命问题（直接判 0）
    if risk_data.get('is_honeypot'):
        return {'score': 0, 'level': '🔴', 'issues': ['🚨 蜜罐合约']}

    # 扣分项
    deductions = []

    buy_tax = risk_data.get('buy_tax', 0)
    sell_tax = risk_data.get('sell_tax', 0)

    if buy_tax > 20 or sell_tax > 20:
        score -= 40
        deductions.append(f'极高税率（买{buy_tax}%/卖{sell_tax}%）')
    elif buy_tax > 10 or sell_tax > 10:
        score -= 20
        deductions.append(f'中高税率（买{buy_tax}%/卖{sell_tax}%）')
    elif buy_tax > 5 or sell_tax > 5:
        score -= 10
        deductions.append(f'中等税率（买{buy_tax}%/卖{sell_tax}%）')

    if not risk_data.get('ownership_renounced'):
        score -= 15
        deductions.append('⚠️ 所有权未放弃')

    if risk_data.get('has_mint_method'):
        score -= 15
        deductions.append('⚠️ 存在 Mint 方法（通胀风险）')

    if risk_data.get('has_black_method'):
        score -= 20
        deductions.append('🚨 存在黑名单方法')

    # Top Holder 集中度
    top_holder_pct = risk_data.get('top_holder_percent', 0)
    if top_holder_pct > 50:
        score -= 15
        deductions.append(f'Top Holder 占比 {top_holder_pct}%（高集中）')

    return {
        'score': max(0, score),
        'level': score_to_contract_level(score),
        'deductions': deductions
    }


def liquidity_score(token_detail):
    """
    流动性评分（0~100）
    """
    tvl = token_detail.get('tvl', 0)
    fdv = token_detail.get('fdv', 0)
    vol_24h = token_detail.get('tx_volume_u_24h', 0)

    score = 50

    # TVL 绝对值
    if tvl > 10_000_000: score += 25
    elif tvl > 1_000_000: score += 20
    elif tvl > 500_000: score += 15
    elif tvl > 100_000: score += 10
    elif tvl > 10_000: score += 5
    else: score -= 20  # TVL 过低

    # TVL / FDV 比值
    if fdv > 0:
        tvl_fdv_ratio = tvl / fdv
        if tvl_fdv_ratio > 0.8: score += 15
        elif tvl_fdv_ratio > 0.5: score += 10
        elif tvl_fdv_ratio > 0.2: score += 5
        else: score -= 10

    # 24h 成交量活跃度
    if tvl > 0:
        vol_ratio = vol_24h / tvl
        if vol_ratio > 0.5: score += 10
        elif vol_ratio > 0.2: score += 5
        elif vol_ratio < 0.05: score -= 10

    return {
        'score': max(0, min(100, score)),
        'level': score_to_liquidity_level(score),
        'tvl': tvl,
        'tvl_fdv_ratio': round(tvl/fdv, 2) if fdv > 0 else 0
    }


def holder_distribution_score(holders_data, token_detail):
    """
    持仓分布评分（0~100）
    """
    if not holders_data:
        return {'score': 50, 'level': '⚪', 'top5_pct': None}

    top5_pct = sum(h.get('percent', 0) for h in holders_data[:5])
    top10_pct = sum(h.get('percent', 0) for h in holders_data[:10])

    score = 100

    if top5_pct > 70: score -= 50
    elif top5_pct > 50: score -= 30
    elif top5_pct > 30: score -= 15
    else: score += 10  # 分散加分

    # 检测 Cex 地址（通过地址特征）
    cex_risk = check_cex_addresses(holders_data[:10])
    score -= cex_risk * 20

    return {
        'score': max(0, min(100, score)),
        'level': score_to_holder_level(score),
        'top5_pct': round(top5_pct, 1),
        'top10_pct': round(top10_pct, 1),
        'cex_risk': cex_risk
    }


def score_to_stars(score):
    """100分 → 5星"""
    if score >= 85: return '★★★★★'
    elif score >= 65: return '★★★★☆'
    elif score >= 45: return '★★★☆☆'
    elif score >= 25: return '★★☆☆☆'
    else: return '★☆☆☆☆'


def score_to_health_level(score):
    if score >= 75: return '🟢 低风险'
    elif score >= 50: return '🟡 中等风险'
    elif score >= 25: return '🟠 高风险'
    else: return '🔴 极高风险'
```

---

## 四、异常警报阈值标定

### 4.1 警报阈值默认值

```python
DEFAULT_ALERT_THRESHOLDS = {
    'whale_tx': {
        'min_amount_usd': 5000,      # 单笔 swap 超过 $5,000 触发
        'cooldown_minutes': 30,        # 同一规则 30 分钟内不重复触发
    },
    'price_change_pct': {
        'threshold_pct': 5.0,         # 5min 内价格变化超过 ±5%
        'cooldown_minutes': 15,
    },
    'liquidity_drop_pct': {
        'threshold_pct': 30,            # TVL 较 24h 前下降超 30%
        'cooldown_minutes': 60,
    },
    'buy_sell_ratio': {
        'threshold': 3.0,              # 5min 内买/卖比超过 3.0 或低于 0.33
        'cooldown_minutes': 30,
    },
    'new_holder_surge': {
        'threshold_count': 20,         # 10min 内新地址数超过 20
        'cooldown_minutes': 60,
    },
    'whale_accumulation': {
        'min_net_buy_usd': 10000,      # 庄家地址净买入超过 $10,000
        'cooldown_minutes': 30,
    }
}
```

### 4.2 阈值自调优（可选扩展）

```python
def adaptive_threshold(token_symbol, alert_type, historical_data):
    """
    根据代币历史波动率自动调整阈值
    波动率高的代币自动放宽阈值，避免噪音
    """
    if not historical_data:
        return DEFAULT_ALERT_THRESHOLDS[alert_type]

    # 计算历史波动率
    volatility = calculate_volatility(historical_data)

    # 根据波动率调整
    if volatility > 0.3:  # 高波动币
        factor = 2.0      # 阈值放宽 2 倍
    elif volatility > 0.15:  # 中波动
        factor = 1.5
    else:
        factor = 1.0

    base = DEFAULT_ALERT_THRESHOLDS[alert_type]
    return {
        k: v * factor if isinstance(v, (int, float)) and k.startswith(('min_', 'threshold_'))
        else v
        for k, v in base.items()
    }
```

---

## 五、阈值标定方法论

### 5.1 标定原则

1. **保守优先**：宁可漏报，不要误报。警报泛滥会让用户麻木。
2. **分层阈值**：低敏感度（日常）+ 高敏感度（关键信号）两档可选
3. **数据驱动**：用历史数据回测（backtest）验证阈值有效性
4. **动态调整**：根据代币波动率自适应调整

### 5.2 回测框架（用于阈值验证）

```python
def backtest_thresholds(token_ca, chain, alert_type, threshold, days=30):
    """
    用过去 N 天的数据回测某个阈值是否能产生有效警报
    返回：precision / recall / false_positive_rate
    """
    historical_txs = get_historical_txs(token_ca, chain, days)
    historical_prices = get_historical_prices(token_ca, chain, days)

    triggered = 0
    real_events = 0  # 需要人工标注或用外部信号验证
    false_positives = 0

    for tx in historical_txs:
        if should_trigger(tx, threshold):
            triggered += 1
            if not is_real_event(tx):  # 简化判断
                false_positives += 1

    precision = (triggered - false_positives) / triggered if triggered > 0 else 0
    false_positive_rate = false_positives / triggered if triggered > 0 else 0

    return {
        'triggered_count': triggered,
        'false_positive_rate': round(false_positive_rate, 3),
        'precision': round(precision, 3),
        'recommendation': '可用' if false_positive_rate < 0.3 else '阈值过低，需调高'
    }
```

---

*本文档为 Ave Guardian v1.0 算法参考，详细实现请参考 `scripts/` 目录下的各模块脚本。*
