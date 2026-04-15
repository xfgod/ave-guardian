---
name: ave-guardian
version: 1.0.0
description: |
  Active on-chain guardian for Ave Cloud API. Five integrated capabilities:

  1. Whale & Manipulator Detector — identifies if a token has a dominant player,
     detects accumulation, pumping, or distribution phase.
  2. Meme & Narrative Scanner — scans platform tags and on-chain momentum to
     discover early-stage meme coin signals before they explode.
  3. Anomaly Alert Engine — monitors watchlist tokens in real-time via WSS and
     pushes channel notifications when thresholds are breached.
  4. Strategy Executor — converts natural-language trading strategies into AVE
     limit orders + TP/SL; fires automatically when conditions are met.
  5. Token Health Reporter — generates a structured risk/contract/holder/liquidity
     report with a 0–100 health score.

  All state persists in ~/.openclaw/workspace/.ave-guardian-state.json.
  State management via: python3 scripts/state_manager.py <command>
  Requires AVE_API_KEY and API_PLAN (free|normal|pro) from ~/.openclaw/workspace/.ave-credentials.json.

  Detailed docs: ./docs/ — ALGORITHMS.md, CONVERSATIONS.md, ALERTS.md, README.md
license: MIT
metadata:
  openclaw:
    primaryEnv: AVE_API_KEY
    requires:
      env:
        - AVE_API_KEY
        - API_PLAN
      bins:
        - python3
        - jq
---

# Ave Guardian（链上守护者）

五合一主动式链上智能体。持续监控，主动推送，不等你来问。

## 状态管理

**状态文件**：`~/.openclaw/workspace/.ave-guardian-state.json`

**管理命令**（使用 state_manager CLI）：

```bash
# 读取完整状态
python3 scripts/state_manager.py read

# 健康检查
python3 scripts/state_manager.py health

# 查看关注列表
python3 scripts/state_manager.py watchlist

# 查看警报规则
python3 scripts/state_manager.py alerts

# 查看策略
python3 scripts/state_manager.py strategies

# 查看审计日志
python3 scripts/state_manager.py audit 50

# 查看统计
python3 scripts/state_manager.py stats

# 查看扫描状态
python3 scripts/state_manager.py scan-state
```

## 意图路由

| 用户说（示例） | 模块 | 说明 |
|---|---|---|
| "分析 0xf43c...288dc 有没有庄" | whale_detector | 庄家行为识别 |
| "帮我做个 PEPE 的体检报告" | health_reporter | Token 体检 |
| "最近有没有值得关注的 Meme 币" | meme_scanner | Meme 叙事扫描 |
| "PEPE 超过 5000 美元大单提醒我" | anomaly_alert | 异常警报配置 |
| "ETH 跌 5% 买 0.5 个，涨 10% 卖" | strategy_executor | 自然语言策略 |
| "帮我看看 PEPE" | whale_detector（默认） | 自动进入庄家分析 |
| "监控这个币" | anomaly_alert | 加入 watchlist + 开启警报 |
| "把 PEPE 从关注列表移除" | state_manager | 管理 watchlist |

## 对话路由规则

```
当用户输入包含：
  "体检" / "安不安全" / "风险报告" / "有没有问题" → health_reporter
  "Meme" / "下一个会爆" / "最近热门"             → meme_scanner
  "监控" / "提醒我" / "警报" / "超过...提醒"     → anomaly_alert
  "跌" / "涨" / "止盈" / "止损" / "策略"         → strategy_executor
  "分析" / "有没有庄" / "这个币"                → whale_detector（默认）
  "关注列表" / "移除" / "我的警报"               → state_manager
```

多意图时优先级：`strategy > alert > whale > health > meme`

## 脚本速查

```bash
# 模块脚本
python3 scripts/whale_detector.py <CA> <chain>      # 庄家识别
python3 scripts/health_reporter.py <CA> <chain>    # Token 体检
python3 scripts/meme_scanner.py                    # Meme 扫描（全部）
python3 scripts/meme_scanner.py <CA> <chain>     # Meme 扫描（单个）
python3 scripts/anomaly_alert.py                   # 警报检查
python3 scripts/strategy_executor.py list         # 列出激活策略
python3 scripts/strategy_executor.py trigger <id>  # 触发策略执行

# Ave Data REST（通过工具函数）
python3 ../ave-scripts/scripts/ave_data_rest.py token --address <CA> --chain <chain>
python3 ../ave-scripts/scripts/ave_data_rest.py holders --address <CA> --chain <chain> --limit 100
python3 ../ave-scripts/scripts/ave_data_rest.py txs --pair <pair> --chain <chain> --limit 100
python3 ../ave-scripts/scripts/ave_data_rest.py risk --address <CA> --chain <chain>
python3 ../ave-scripts/scripts/ave_data_rest.py platform-tokens --platform meme --limit 30

# Ave Trade Proxy
python3 ../ave-scripts/scripts/ave_trade_rest.py market-order --chain bsc --assets-id <id> ...
python3 ../ave-scripts/scripts/ave_trade_rest.py limit-order --chain bsc --assets-id <id> ...
```

## 安全规则

1. 所有交易执行必须经过用户显式确认
2. 新用户第一次交易限额 ≤ $10（测试模式）
3. API Key 从 `~/.openclaw/workspace/.ave-credentials.json` 读取，不硬编码
4. 每条分析/警报输出必须附带免责声明

## 详细文档

- `./docs/README.md` — 完整架构和使用文档
- `./docs/ALGORITHMS.md` — 各模块评分算法详解
- `./docs/CONVERSATIONS.md` — 完整对话示例（7个场景）
- `./docs/ALERTS.md` — 所有推送模板格式规范
