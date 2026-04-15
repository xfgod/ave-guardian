# Ave Guardian 警报推送模板

> 所有推送的警报使用以下模板格式，确保信息密度合适、关键数据突出、操作选项清晰。

---

## 模板规范

```
每条推送遵循「三层结构」：
  1. 标题行（类型 + 币种 + 核心数据）
  2. 详情行（背景数据 / 原因）
  3. 操作行（预设按钮，供用户一键响应）
```

---

## 🚨 警报类型 1：whale_tx（大额鲸鱼交易）

```
🚨 大额 {{SIDE}}（{{SYMBOL}}）
━━━━━━━━━━━━━━━━━━
🐋 金额：${{AMOUNT_USD}}
📍 地址：{{ADDRESS}}（{{ADDR_LABEL}}）
📐 方向：{{SIDE}}（{{SIDE_CHINESE}}）
🕐 时间：{{TIME}}

📊 背景：
  过去 1h 净 {{SIDE}}：${{NET_FLOW_1H_USD}}
  当前价格：${{CURRENT_PRICE}}（24h {{PRICE_CHANGE_24H}}）
  24h 成交量：${{VOLUME_24H}}

🤖 AI 判断：
  {{NARRATIVE}}（{{CONFIDENCE}}）

操作建议：
  [设置止损] [加入监控] [忽略 30min]
━━━━━━━━━━━━━━━━━━
```

**字段说明**：
| 字段 | 说明 |
|------|------|
| `SYMBOL` | 代币符号，如 PEPE |
| `SIDE` | buy / sell |
| `SIDE_CHINESE` | 买入 / 卖出 |
| `AMOUNT_USD` | 交易金额（USD） |
| `ADDRESS` | sender 地址（前6后4） |
| `ADDR_LABEL` | 地址标签（庄家A / Cex 充币 / 普通用户） |
| `TIME` | HH:MM:SS |
| `NET_FLOW_1H_USD` | 过去1小时净流入/流出 USD |
| `CURRENT_PRICE` | 当前价格 |
| `PRICE_CHANGE_24H` | 24h 价格变化（带正负号和百分比） |
| `VOLUME_24H` | 24h 成交量 |
| `NARRATIVE` | 文字判断（疑似出货 / 正常调仓 / 疑似吸筹） |
| `CONFIDENCE` | 置信度（高/中/低） |

---

## 🚨 警报类型 2：price_spike（价格异动）

```
🚨 价格异动（{{SYMBOL}}）
━━━━━━━━━━━━━━━━━━
📈 当前价格：${{CURRENT_PRICE}}
📉 5min 变化：{{CHANGE_PCT}}%（{{DIRECTION}}）
📊 1h 变化：{{CHANGE_1H_PCT}}%

📍 触发阈值：>{{THRESHOLD_PCT}}%（{{THRESHOLD_TYPE}}）
🕐 时间：{{TIME}}

🤖 变化原因推测：
  {{CAUSE_ESTIMATE}}
  （基于近期 swap 方向和成交量）

⚠️ 当前走势：{{TREND_DIRECTION}}

操作建议：
  [查看详情] [设置止盈止损] [忽略]
━━━━━━━━━━━━━━━━━━
```

**字段说明**：
| 字段 | 说明 |
|------|------|
| `CHANGE_PCT` | 5min 内变化百分比（带 ± 和 %） |
| `DIRECTION` | 急速上涨 / 急速下跌 / 横盘震荡 |
| `THRESHOLD_PCT` | 触发阈值 |
| `THRESHOLD_TYPE` | 用户配置的阈值类型 |
| `CAUSE_ESTIMATE` | 基于 swap 方向的推测原因 |
| `TREND_DIRECTION` | 短时强势 / 短时弱势 / 多空博弈 |

---

## 🚨 警报类型 3：liquidity_drop（流动性骤降）

```
🚨 流动性骤降（{{SYMBOL}}）
━━━━━━━━━━━━━━━━━━
💧 当前 TVL：${{CURRENT_TVL}}
📉 变化：-{{TVL_DROP_PCT}}%（- ${{TVL_DROP_USD}}）
📊 24h 前 TVL：${{TVL_24H_AGO}}

⚠️ 触发条件：TVL 下降 > {{THRESHOLD_PCT}}%（阈值）

🤖 影响评估：
  流动性骤降通常意味着：
  ① 项目方或庄家撤出流动性（高风险）
  ② 大规模清算触发（连环爆仓）
  ③ 正常市场行为（低风险）

  当前情况判断：{{ASSESSMENT}}（{{CONFIDENCE}}）

💡 建议：
  {{SUGGESTION}}

操作建议：
  [查看庄家行为] [设置止损] [忽略]
━━━━━━━━━━━━━━━━━━
```

---

## 🚨 警报类型 4：buy_sell_ratio（买卖比异常）

```
🚨 买卖比异常（{{SYMBOL}}）
━━━━━━━━━━━━━━━━━━
📊 买卖比（5min）：{{RATIO}}
  买：${{BUY_VOLUME_5M}} | 卖：${{SELL_VOLUME_5M}}
📈 买卖比（1h）：{{RATIO_1H}}

⏱ 时间窗口：5 分钟
🕐 时间：{{TIME}}

🤖 信号解读：
  {{SIGNAL_INTERPRETATION}}

⚠️ 方向偏向：{{DIRECTION_BIAS}}

操作建议：
  [跟进分析] [设置监控] [忽略]
━━━━━━━━━━━━━━━━━━
```

---

## 🚨 警报类型 5：whale_accumulation（鲸鱼吸筹检测）

```
🚨 鲸鱼吸筹信号（{{SYMBOL}}）
━━━━━━━━━━━━━━━━━━
🐋 庄家地址：{{ADDR}}（{{ADDR_NOTE}}）
📈 过去 {{WINDOW}}：
  净买入：${{NET_BUY_USD}}
  买入次数：{{BUY_COUNT}} 笔
  卖出次数：{{SELL_COUNT}} 笔
  买入/卖出：{{BUY_SELL_RATIO}}

📊 背景数据：
  当前价格：${{CURRENT_PRICE}}（{{PRICE_CHANGE_PCT}}%）
  TVL：${{TVL}}

🤖 AI 判断：
  庄家地址近 {{WINDOW }}持续净买入，累计 ${{NET_BUY_USD }}。
  吸筹阶段通常意味着 {{INTERPRETATION}}。
  注意：吸筹不代表一定拉盘，请结合其他信号判断。

💡 建议关注：
  {{SUGGESTION}}

操作建议：
  [查看完整分析] [加入关注列表] [设置警报]
━━━━━━━━━━━━━━━━━━
```

---

## 🚨 警报类型 6：strategy_triggered（策略触发）

```
⚡ 策略触发提醒
━━━━━━━━━━━━━━━━━━
策略：{{STRATEGY_NAME}}
代币：{{SYMBOL}}（{{CA}}）
触发条件：{{TRIGGER_CONDITION}}

📋 执行详情：
  动作：{{ACTION}} {{AMOUNT}} {{SYMBOL}}
  触发价格：${{TRIGGER_PRICE}}
  触发时间：{{TIME}}

{{IF_TP:
  ✅ 止盈目标达成
  成交均价：${{EXEC_PRICE}}
  盈亏：{{PNL_USD}}（{{PNL_PCT}}%）
}}

{{IF_SL:
  ⚠️ 止损触发
  亏损：{{LOSS_USD}}（{{LOSS_PCT}}%）
}}

策略状态：{{STRATEGY_STATUS}}

━━━━━━━━━━━━━━━━━━
```

---

## 🔥 主动推送类型：meme_scan_result（Meme 叙事扫描）

```
🔥 Meme 预警
━━━━━━━━━━━━━━━━━━
币：{{SYMBOL}}（{{CA}}）
链：{{CHAIN}} | 叙事评分：{{SCORE}} {{LEVEL}}

🔥 触发信号：
{{SIGNALS}}

🤖 AI 判断：
  {{NARRATIVE}}

━━━━━━━━━━━━━━━━━━
📌 操作建议：
  [查看完整分析] [加入监控] [忽略]
━━━━━━━━━━━━━━━━━━
```

**SIGNALS 部分格式**：
```
  ✅ 24h 成交量 +{{VOL_CHANGE_PCT}}%
  ✅ 持有人数 +{{HOLDER_CHANGE_PCT}}%
  ⚠️ {{SIGNAL_ITEM}}
  ❌ {{NEGATIVE_SIGNAL}}
```

---

## 🩺 主动推送类型：health_report（定期体检摘要）

```
🩺 代币体检周报（{{SYMBOL}}）
━━━━━━━━━━━━━━━━━━
报告周期：{{START_DATE}} ~ {{END_DATE}}
综合评分：{{SCORE}} {{STARS}}（{{LEVEL}}）

📊 本周变化：
  价格：{{PRICE_WEEK_CHANGE}}%
  TVL：{{TVL_WEEK_CHANGE}}%
  持仓集中度：{{HOLDER_CHANGE}}
  庄家行为：{{WHALE_BEHAVIOR}}

⚠️ 本周新风险：
  {{NEW_RISKS}}

💡 建议：
  {{SUGGESTION}}

[查看完整报告] [调整警报阈值] [取消订阅周报]
━━━━━━━━━━━━━━━━━━
```

---

## 📊 格式规范总结

### 关键原则

1. **标题行必须在第一行**，含 emoji + 核心数据（金额/方向/评分）
2. **总长度不超过一屏**（约 400 字），超出的用折叠
3. **操作按钮不超过 3 个**，避免选择困难
4. **emoji 数量克制**，每种类型只用一种 emoji 标记级别
5. **置信度必须标注**，避免给用户绝对确定感

### 颜色语义（emoji）

| Emoji | 含义 | 适用场景 |
|-------|------|---------|
| 🔴 | 高风险 / 立即行动 | 止损触发、庄家出货警报 |
| 🟠 | 中高风险 / 关注 | 吸筹检测、策略触发 |
| 🟡 | 中等风险 / 观察 | 买卖比异常、价格横盘 |
| 🟢 | 低风险 / 正常 | 正常波动、无明显异常 |
| ⚠️ | 注意 | 所有警报的标准前缀 |
| ✅ | 条件达成 | 止盈触发、警报条件满足 |
| ⚡ | 策略执行 | 策略触发通知 |

### 置信度标注

| 置信度 | 说明 | 适用场景 |
|--------|------|---------|
| 高 | 多项数据交叉验证 | whale_tx + holders 行为一致时 |
| 中 | 单项数据强信号 | 只有 whale_tx 或只有持仓集中 |
| 低 | 单一数据触发 | 仅价格波动警报 |
