# Ave Guardian 完整文档

> 五合一主动式链上智能体。集庄家识别、Meme 叙事捕捉、异常警报、策略执行、体检报告于一身。

---

## 目录

1. [快速开始](#快速开始)
2. [架构总览](#架构总览)
3. [模块详解](#模块详解)
4. [状态管理](#状态管理)
5. [Cron 定时任务](#cron-定时任务)
6. [安装与配置](#安装与配置)
7. [文件结构](#文件结构)

---

## 快速开始

### 前置要求

- Python 3.10+
- `jq` 命令（系统包）
- AVE Cloud API Key（免费注册：https://cloud.ave.ai/）
- OpenClaw Agent

### 安装

```bash
git clone https://github.com/YOUR_USERNAME/ave-guardian.git
cd ave-guardian
make install
```

### 配置 API Key

```bash
# 创建凭证文件
cat > ~/.openclaw/workspace/.ave-credentials.json << 'EOF'
{
  "ave_api_key": "你的API密钥",
  "api_plan": "pro"
}
EOF
```

### 基本对话

```
用户：帮我分析 0xf43c8f27754829202d2f66650eb3f6d168c288dc 有没有庄
→ 触发 whale_detector

用户：最近有没有值得关注的 Meme 币？
→ 触发 meme_scanner

用户：PEPE 如果超过 5000 美元的大单提醒我
→ 触发 anomaly_alert

用户：ETH 跌 5% 买 0.5 个，涨 10% 卖
→ 触发 strategy_executor

用户：给我做个 PEPE 的体检报告
→ 触发 health_reporter
```

---

## 架构总览

```
用户对话 / Cron 触发
        ↓
   Central Dialog Router（意图识别）
        ↓
   ┌──────────────────────────────────┐
   │     Central State Manager          │
   │  watchlist │ alerts │ strategies│
   └──────────────────────────────────┘
        ↓
   ┌──────────────────────────────────┐
   │   whale_detector                  │
   │   health_reporter                │
   │   meme_scanner                   │
   │   anomaly_alert                  │
   │   strategy_executor              │
   └──────────────────────────────────┘
        ↓
   Ave Data REST / WSS / Trade Proxy
        ↓
   Response Formatter
```

---

## 模块详解

### 模块一：Whale & Manipulator Detector（庄家行为识别）

**功能：** 判断一个代币是否存在庄家控盘，识别吸筹 / 拉升 / 出货阶段。

**触发词：** "有没有庄"、"分析庄"、"分析这个币"

**算法：** 三层加权评分
- 持仓集中度（Top10 占比）× 0.40
- 交易行为（大户净买卖方向）× 0.40
- K线形态（放量/缩量/背离）× 0.20

**输出示例：**
```
🐸 PEPE（0xf43c...288dc）
📊 持仓：Top10 71.2%（🔴 极高）
💱 行为：近24h净卖出 $12,847（疑似出货）
📈 K线：拉升后缩量横盘
🤖 综合：【🔴 高度控盘 - 出货阶段】
```

详见 [ALGORITHMS.md](./ALGORITHMS.md#一庄家控盘评分算法whale--manipulator-detector)

---

### 模块二：Meme & Narrative Scanner（Meme 叙事捕捉）

**功能：** 扫描 Meme 平台标签 + 链上动量，提前发现爆发信号。

**触发词：** "Meme"、"下一个会爆"、"最近热门"

**算法：** 五维叙事评分
- 成交量增速 × 0.30
- 持有人增长 × 0.25
- 新地址加速 × 0.20
- K线突破 × 0.15
- 平台标签 × 0.10

**输出：** 叙事榜单 + 每币的触发信号详情

详见 [ALGORITHMS.md](./ALGORITHMS.md#二meme-叙事评分算法meme--narrative-scanner)

---

### 模块三：Anomaly Alert Engine（异常警报）

**功能：** 实时监控用户 watchlist，有异常立即推送。

**触发词：** "监控"、"提醒我"、"警报"

**支持警报类型：**
- `whale_tx` — 单笔 swap 超过 $X
- `price_spike` — 5min 价格变化超过 ±X%
- `liquidity_drop` — TVL 较 24h 前下降 > X%
- `buy_sell_ratio` — 买卖比超过阈值
- `whale_accumulation` — 庄家净买入超过 $X

详见 [ALERTS.md](./ALERTS.md)

---

### 模块四：Strategy Executor（策略执行）

**功能：** 自然语言 → AVE 限价单 + 止盈止损。

**触发词：** "跌X%买"、"涨X%卖"、"止盈"、"止损"

**示例：**
```
用户：ETH 跌 5% 买 0.5 个，涨 10% 卖，止损 3%
↓
AI 确认 → 用户确认 → 挂限价买单 + 预埋止盈止损单
```

---

### 模块五：Token Health Reporter（体检报告）

**功能：** 一键生成代币综合健康报告（0~100 分）。

**触发词：** "体检"、"安不安全"、"风险报告"

**五大维度：**
- 合约安全（30%）— 蜜罐/税率/权限
- 流动性（25%）— TVL/FDV比/活跃度
- 持仓分布（20%）— Top10集中度
- 市场行为（15%）— 买卖比/波动率
- 基本面（10%）— 市值/FDV/发行时间

详见 [ALGORITHMS.md](./ALGORITHMS.md#三体检报告综合评分token-health-score)

---

## 状态管理

### 状态文件

```
~/.openclaw/workspace/.ave-guardian-state.json
```

### 状态结构

```json
{
  "version": "1.0",
  "watchlist": [...],
  "alerts": {"max_open_alerts": 10, "rules": [...]},
  "strategies": [...],
  "context": {...},
  "scan_state": {...}
}
```

### 常用操作

```bash
# 查看所有关注
jq '.watchlist' ~/.openclaw/workspace/.ave-guardian-state.json

# 添加关注
jq '.watchlist += [{"token":"<CA>","chain":"bsc","symbol":"PEPE","added_at":<ts>}]' \
  ~/.openclaw/workspace/.ave-guardian-state.json > /tmp/ags.json
mv /tmp/ags.json ~/.openclaw/workspace/.ave-guardian-state.json

# 删除关注
jq 'del(.watchlist[] | select(.token=="<CA>"))' \
  ~/.openclaw/workspace/.ave-guardian-state.json > /tmp/ags.json
mv /tmp/ags.json ~/.openclaw/workspace/.ave-guardian-state.json

# 列出活跃警报
jq '.alerts.rules[] | select(.active==true)' \
  ~/.openclaw/workspace/.ave-guardian-state.json

# 触发策略
python3 scripts/strategy_executor.py trigger <strategy_id>
```

---

## Cron 定时任务

| 任务 | 频率 | 功能 |
|------|------|------|
| `meme_scanner_cron.sh` | 每 30 分钟 | 扫描 Meme 爆发信号 |
| `whale_watcher_cron.sh` | 每 15 分钟 | 对 watchlist 币运行庄家检测 |
| `liquidity_check_cron.sh` | 每 2 小时 | 检测 TVL 骤降 |

### 注册 Cron

```bash
# 编辑 crontab
crontab -e

# 添加（按实际路径修改）
*/30 * * * * /path/to/ave-guardian/cron/meme_scanner_cron.sh >> ~/.openclaw/logs/guardian.log 2>&1
*/15 * * * * /path/to/ave-guardian/cron/whale_watcher_cron.sh >> ~/.openclaw/logs/guardian.log 2>&1
0 */2 * * * /path/to/ave-guardian/cron/liquidity_check_cron.sh >> ~/.openclaw/logs/guardian.log 2>&1
```

---

## 安装与配置

### 方式一：pip install

```bash
pip install ave-guardian
```

### 方式二：git clone + make

```bash
git clone https://github.com/YOUR_USERNAME/ave-guardian.git
cd ave-guardian
make install
```

### 配置 OpenClaw

确保 `~/.openclaw/workspace/.ave-credentials.json` 存在：

```json
{
  "ave_api_key": "your_key_here",
  "api_plan": "pro"
}
```

---

## 文件结构

```
ave-guardian/
├── SKILL.md                    # OpenClaw 加载用（< 200 行）
├── README.md                   # 本文档
├── LICENSE                     # MIT 开源协议
├── Makefile                    # 一键安装
├── pyproject.toml              # Python 包配置
│
├── docs/                       # 详细文档（人类阅读）
│   ├── ALGORITHMS.md           # 评分算法详解
│   ├── CONVERSATIONS.md        # 对话示例
│   └── ALERTS.md               # 推送模板
│
├── scripts/                    # 可执行脚本
│   ├── utils.py               # 共享工具函数
│   ├── whale_detector.py      # 模块一
│   ├── meme_scanner.py        # 模块二
│   ├── health_reporter.py      # 模块五
│   ├── anomaly_alert.py       # 模块三
│   ├── strategy_executor.py   # 模块四
│   └── narrative_scorer.py    # 评分算法
│
├── cron/                       # Cron 任务模板
│   ├── meme_scanner_cron.sh
│   ├── whale_watcher_cron.sh
│   └── liquidity_check_cron.sh
│
└── tests/                      # 测试
    └── ...
```

---

## 参考资料

- AVE Cloud API: https://cloud.ave.ai/
- AVE Skill 官方: https://github.com/AveCloud/ave-cloud-skill
- OpenClaw Docs: https://docs.openclaw.ai/
