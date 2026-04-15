# Ave Guardian（链上守护者）

> 集庄家识别、Meme 叙事捕捉、异常警报、自动策略执行、Token 体检五大能力于一体的主动式链上智能体。

## 五大能力

| 模块 | 功能 | 触发方式 |
|------|------|---------|
| 🐋 **庄家识别** | 判断币是否有庄家、处于吸筹/拉升/出货哪阶段 | 用户查询 / 定时扫描 |
| 🔥 **Meme 叙事捕捉** | 提前发现即将爆发的 Meme 币信号 | 定时扫描（每30分钟） |
| 🚨 **异常警报** | 监控大额交易/价格异动，主动推送微信 | WebSocket 实时订阅 |
| ⚡ **策略执行** | 自然语言设置止损/止盈，到价自动执行 | 用户配置 |
| 🩺 **Token 体检** | 一键生成风险评分报告 | 用户查询 |

## 快速开始

### 环境配置

```bash
# 设置 API Key（如果还没有）
# API Key 从 ~/.openclaw/workspace/.ave-credentials.json 读取
# 内容格式：
# {
#   "ave_api_key": "你的API密钥",
#   "api_plan": "pro"
# }
```

### 基本对话示例

```
用户：帮我分析 0xf43c...288dc 这个币有没有庄
→ 触发庄家识别引擎

用户：有没有值得关注的 Meme 币？
→ 触发 Meme 叙事扫描

用户：PEPE 如果超过 5000 美元的大单提醒我
→ 触发异常警报配置

用户：PEPE 跌 5% 买 0.5 个，涨 10% 卖
→ 触发策略执行

用户：给我做个 PEPE 的体检报告
→ 触发 Token 体检报告
```

## 状态文件

所有持久化状态保存在：
```
~/.openclaw/workspace/.ave-guardian-state.json
```

包含：watchlist（关注列表）、alerts（警报规则）、strategies（策略配置）、scan_state（扫描状态）

## 定时任务

| 任务 | 频率 | 功能 |
|------|------|------|
| meme_scanner | 每 30 分钟 | 扫描 Meme 平台标签，发现早期爆发信号 |
| whale_watcher | 每 15 分钟 | 对 watchlist 币运行庄家行为检测 |
| liquidity_check | 每 2 小时 | 检测 TVL 骤降等流动性异常 |

## 文档

- [SKILL.md](./SKILL.md) — 完整设计文档（含架构、算法、对话流程）
- [references/algorithms.md](./references/algorithms.md) — 评分算法详细说明
- [examples/conversation_examples.md](./examples/conversation_examples.md) — 完整对话示例

## 架构

```
用户对话 / Cron 触发
       ↓
  Central Dialog Router（意图识别）
       ↓
  ┌──────────────────────────────────┐
  │     Central State Manager        │
  │  watchlist │ alerts │ strategies│
  └──────────────────────────────────┘
       ↓
  ┌──────────────────────────────────┐
  │   模块一  模块二  模块三         │
  │   模块四  模块五                 │
  │   (并行/串行，由意图决定)        │
  └──────────────────────────────────┘
       ↓
  Ave Data REST / WSS / Trade Proxy
       ↓
  Response Formatter（微信卡片）
```

## 技术栈

- **运行环境**：OpenClaw Agent
- **API**：AVE Cloud API（Data REST + Data WSS + Trade Proxy）
- **脚本语言**：Python 3 + Bash
- **状态存储**：JSON 文件（`jq` 操作）
- **定时任务**：OpenClaw Cron
- **推送通道**：微信（OpenClaw WeChat Channel）
