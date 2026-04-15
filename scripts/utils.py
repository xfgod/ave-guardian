#!/usr/bin/env python3
"""
Ave Guardian — Utilities
共享工具函数，所有模块都用它来调用 Ave API 和操作状态。

状态管理统一通过 state_manager 模块，不直接操作 JSON 文件。

注意：本模块绕过 ave_data_rest.py Docker 包装，
直接调用 Ave Cloud REST API（无 Docker 依赖）。
"""

import json
import subprocess
import os
import sys
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ============================================================
# 路径配置
# ============================================================

# 获取脚本所在目录的父目录（即 ave-guardian/）
GUARDIAN_DIR = Path(__file__).parent.parent.resolve()

# Ave Cloud API Base URLs
AVE_DATA_REST_BASE = "https://data.ave-api.xyz/v2"
AVE_TRADE_REST_BASE = "https://bot-api.ave.ai/v1"
AVE_WSS_BASE = "wss://wss.ave-api.xyz"

# 凭证文件
CREDS_FILE = Path(os.environ.get(
    "AVE_CREDS",
    os.path.expanduser("~/.openclaw/workspace/.ave-credentials.json")
))

# ============================================================
# 凭证读取
# ============================================================

def get_credentials() -> dict:
    """
    读取 AVE API 凭证。
    
    Returns:
        {"ave_api_key": str, "api_plan": str}
    
    Raises:
        FileNotFoundError: 凭证文件不存在
        KeyError: 缺少必要字段
    """
    if not CREDS_FILE.exists():
        raise FileNotFoundError(
            f"Credentials file not found: {CREDS_FILE}\n"
            "Please create it with {\"ave_api_key\": \"...\", \"api_plan\": \"pro\"}"
        )
    
    with open(CREDS_FILE, "r", encoding="utf-8") as f:
        creds = json.load(f)
    
    if "ave_api_key" not in creds:
        raise KeyError(f"Missing 'ave_api_key' in {CREDS_FILE}")
    
    return {
        "ave_api_key": creds.get("ave_api_key", ""),
        "api_plan": creds.get("api_plan", "pro")
    }


# ============================================================
# 核心：直接调用 Ave REST API（无 Docker 依赖）
# ============================================================

def _make_request(
    url: str,
    method: str = "GET",
    data: dict = None,
    headers: dict = None,
    timeout: int = 30
) -> dict:
    """
    直接向 Ave API 发起 HTTP 请求。
    绕过 ave_data_rest.py Docker 包装。
    
    Args:
        url: 请求 URL
        method: HTTP 方法
        data: 请求体数据（dict，会序列化为 JSON）
        headers: 额外请求头
        timeout: 超时秒数
    
    Returns:
        parsed JSON dict，或 {"error": str}
    """
    creds = get_credentials()
    
    # 默认请求头
    default_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-API-Key": creds["ave_api_key"],
    }
    
    if headers:
        default_headers.update(headers)
    
    try:
        # 序列化请求数据
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
        
        # 构建请求
        req = urllib.request.Request(
            url,
            data=body,
            headers=default_headers,
            method=method
        )
        
        # 发起请求
        with urllib.request.urlopen(req, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
            
            if not response_body.strip():
                return {"status": "ok", "data": None}
            
            try:
                return json.loads(response_body)
            except json.JSONDecodeError:
                return {"raw": response_body}
    
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        try:
            error_json = json.loads(error_body)
            return {
                "error": f"HTTP {e.code}: {error_json.get('msg', error_body[:200])}"
            }
        except:
            return {
                "error": f"HTTP {e.code}: {error_body[:200]}"
            }
    
    except urllib.error.URLError as e:
        return {"error": f"URL Error: {e.reason}"}
    
    except TimeoutError:
        return {"error": f"Request timed out after {timeout}s"}
    
    except json.JSONDecodeError as e:
        return {"error": f"JSON decode error: {e}"}
    
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Ave Data REST API 调用（直接，无 Docker）
# ============================================================

def ave_search(keyword: str, chain: str = None, limit: int = 20, orderby: str = None) -> dict:
    """搜索代币"""
    params = {"keyword": keyword, "limit": limit}
    if chain:
        params["chain"] = chain
    if orderby:
        params["orderby"] = orderby
    
    url = f"{AVE_DATA_REST_BASE}/tokens?{urllib.parse.urlencode(params)}"
    return _make_request(url)


def ave_token_detail(address: str, chain: str) -> dict:
    """获取代币详情"""
    url = f"{AVE_DATA_REST_BASE}/tokens/{address}-{chain}"
    return _make_request(url)


def ave_holders(address: str, chain: str, limit: int = 100, sort_by: str = "balance") -> dict:
    """获取持仓分布"""
    params = {"limit": limit, "sortby": sort_by}
    url = f"{AVE_DATA_REST_BASE}/tokens/top100/{address}-{chain}?{urllib.parse.urlencode(params)}"
    return _make_request(url)


def ave_txs(pair: str, chain: str, limit: int = 100) -> dict:
    """获取 Swap 交易记录"""
    params = {"limit": limit}
    url = f"{AVE_DATA_REST_BASE}/txs/{pair}-{chain}?{urllib.parse.urlencode(params)}"
    return _make_request(url)


def ave_kline_token(address: str, chain: str, interval: int = 60, limit: int = 24) -> dict:
    """获取 K 线数据（按 token）"""
    params = {"interval": interval, "limit": limit}
    url = f"{AVE_DATA_REST_BASE}/klines/token/{address}-{chain}?{urllib.parse.urlencode(params)}"
    return _make_request(url)


def ave_kline_pair(pair: str, chain: str, interval: int = 60, limit: int = 24) -> dict:
    """获取 K 线数据（按 pair）"""
    params = {"interval": interval, "limit": limit}
    url = f"{AVE_DATA_REST_BASE}/klines/pair/{pair}-{chain}?{urllib.parse.urlencode(params)}"
    return _make_request(url)


def ave_risk(address: str, chain: str) -> dict:
    """获取合约风险报告"""
    url = f"{AVE_DATA_REST_BASE}/contracts/{address}-{chain}"
    return _make_request(url)


def ave_platform_tokens(platform: str, limit: int = 30, orderby: str = None) -> dict:
    """获取平台标签代币列表"""
    params = {"tag": platform, "limit": limit}
    if orderby:
        params["orderby"] = orderby
    url = f"{AVE_DATA_REST_BASE}/tokens/platform?{urllib.parse.urlencode(params)}"
    return _make_request(url)


def ave_trending(chain: str, page: int = 1, page_size: int = 50) -> dict:
    """获取链上热门代币"""
    params = {"chain": chain, "current_page": page, "page_size": page_size}
    url = f"{AVE_DATA_REST_BASE}/tokens/trending?{urllib.parse.urlencode(params)}"
    return _make_request(url)


def ave_ranks(topic: str, limit: int = 100) -> dict:
    """获取主题排行榜"""
    params = {"topic": topic, "limit": limit}
    url = f"{AVE_DATA_REST_BASE}/ranks?{urllib.parse.urlencode(params)}"
    return _make_request(url)


def ave_batch_price(token_ids: list, tvl_min: float = 0, tx_24h_volume_min: float = 0) -> dict:
    """批量获取代币价格"""
    data = {
        "token_ids": token_ids,
        "tvl_min": tvl_min,
        "tx_24h_volume_min": tx_24h_volume_min
    }
    url = f"{AVE_DATA_REST_BASE}/tokens/price"
    return _make_request(url, method="POST", data=data)


def ave_chains() -> dict:
    """获取支持的链列表"""
    url = f"{AVE_DATA_REST_BASE}/supported_chains"
    return _make_request(url)


# ============================================================
# Ave Trade REST API 调用
# ============================================================

def ave_quote(
    chain: str,
    in_amount: str,
    in_token: str,
    out_token: str,
    swap_type: str  # buy or sell
) -> dict:
    """获取报价（计算预估输出）"""
    data = {
        "chain": chain,
        "inAmount": in_amount,
        "inTokenAddress": in_token,
        "outTokenAddress": out_token,
        "swapType": swap_type
    }
    url = f"{AVE_TRADE_REST_BASE}/thirdParty/chainWallet/getAmountOut"
    return _make_request(url, method="POST", data=data)


# ============================================================
# Ave WSS API 调用（返回可用的命令行）
# ============================================================

def ave_wss_command(command: str, *args) -> str:
    """
    生成 ave_data_wss.py 的命令字符串。
    注意：WSS 需要 Docker，不在这里执行。
    这里只返回命令。
    """
    # 返回完整命令供调用方使用
    return f"python3 scripts/ave_data_wss.py {command} {' '.join(args)}"


# ============================================================
# 兼容层：通过 subprocess 调用 ave_data_rest.py（仅当 Docker 可用时）
# ============================================================

def _try_docker_fallback(command: str, *args) -> dict:
    """
    尝试通过 Docker 调用 ave_data_rest.py。
    如果 Docker 不可用，返回错误。
    """
    # 检查 Docker 是否可用
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5
        )
        if result.returncode != 0:
            return {"error": "Docker not available"}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"error": "Docker not available"}
    
    # Docker 可用，执行命令
    ave_scripts_dir = GUARDIAN_DIR.parent / "ave-scripts" / "scripts"
    script_path = ave_scripts_dir / "ave_data_rest.py"
    
    if not script_path.exists():
        return {"error": f"Script not found: {script_path}"}
    
    creds = get_credentials()
    env = os.environ.copy()
    env["AVE_API_KEY"] = creds["ave_api_key"]
    env["API_PLAN"] = creds["api_plan"]
    
    cmd = [sys.executable, str(script_path), command, *args]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=30
        )
        
        if result.returncode != 0:
            return {"error": result.stderr.strip()}
        
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return {"raw": result.stdout.strip()}
    
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out"}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 统一 API 调用入口（推荐使用这个）
# ============================================================

def run_ave_rest(command: str, *args, capture_output: bool = True) -> dict:
    """
    执行 Ave Data REST API 调用。
    
    优先直接调用 Ave API（无 Docker 依赖）。
    command 参数保留用于兼容，实际不执行 ave_data_rest.py。
    
    Args:
        command: 子命令（search/token/holders/txs/kline/risk 等）
        *args: 额外参数
    
    Returns:
        parsed JSON dict
    """
    # 解析参数（简单的键值对解析）
    kwargs = {}
    remaining_args = []
    
    i = 0
    while i < len(args):
        arg = str(args[i])
        if arg.startswith("--"):
            key = arg[2:]
            if i + 1 < len(args) and not str(args[i + 1]).startswith("--"):
                kwargs[key] = str(args[i + 1])
                i += 2
            else:
                kwargs[key] = True
                i += 1
        else:
            remaining_args.append(arg)
            i += 1
    
    # 路由到对应的直接 API 函数
    try:
        if command == "search":
            return ave_search(
                keyword=kwargs.get("keyword", remaining_args[0] if remaining_args else ""),
                chain=kwargs.get("chain"),
                limit=int(kwargs.get("limit", 20)),
                orderby=kwargs.get("orderby")
            )
        
        elif command == "token":
            return ave_token_detail(
                address=kwargs.get("address", remaining_args[0] if remaining_args else ""),
                chain=kwargs.get("chain", remaining_args[1] if len(remaining_args) > 1 else "bsc")
            )
        
        elif command in ("holders", "top100"):
            return ave_holders(
                address=kwargs.get("address", remaining_args[0] if remaining_args else ""),
                chain=kwargs.get("chain", remaining_args[1] if len(remaining_args) > 1 else "bsc"),
                limit=int(kwargs.get("limit", 100)),
                sort_by=kwargs.get("sort-by", kwargs.get("sort_by", "balance"))
            )
        
        elif command == "txs":
            return ave_txs(
                pair=kwargs.get("pair", remaining_args[0] if remaining_args else ""),
                chain=kwargs.get("chain", remaining_args[1] if len(remaining_args) > 1 else "bsc"),
                limit=int(kwargs.get("limit", 100))
            )
        
        elif command in ("kline-token", "kline"):
            return ave_kline_token(
                address=kwargs.get("address", remaining_args[0] if remaining_args else ""),
                chain=kwargs.get("chain", remaining_args[1] if len(remaining_args) > 1 else "bsc"),
                interval=int(kwargs.get("interval", 60)),
                limit=int(kwargs.get("size", kwargs.get("limit", 24)))
            )
        
        elif command == "kline-pair":
            return ave_kline_pair(
                pair=kwargs.get("address", remaining_args[0] if remaining_args else ""),
                chain=kwargs.get("chain", remaining_args[1] if len(remaining_args) > 1 else "bsc"),
                interval=int(kwargs.get("interval", 60)),
                limit=int(kwargs.get("size", kwargs.get("limit", 24)))
            )
        
        elif command == "risk":
            return ave_risk(
                address=kwargs.get("address", remaining_args[0] if remaining_args else ""),
                chain=kwargs.get("chain", remaining_args[1] if len(remaining_args) > 1 else "bsc")
            )
        
        elif command in ("platform-tokens", "platform"):
            return ave_platform_tokens(
                platform=kwargs.get("platform", kwargs.get("tag", remaining_args[0] if remaining_args else "meme")),
                limit=int(kwargs.get("limit", 30)),
                orderby=kwargs.get("orderby")
            )
        
        elif command == "trending":
            return ave_trending(
                chain=kwargs.get("chain", remaining_args[0] if remaining_args else "bsc"),
                page=int(kwargs.get("page", 1)),
                page_size=int(kwargs.get("page-size", kwargs.get("page_size", 50)))
            )
        
        elif command == "ranks":
            return ave_ranks(
                topic=kwargs.get("topic", remaining_args[0] if remaining_args else "hot"),
                limit=int(kwargs.get("limit", 100))
            )
        
        elif command == "chains":
            return ave_chains()
        
        else:
            return {"error": f"Unknown command: {command}"}
    
    except Exception as e:
        return {"error": str(e)}


def run_ave_trade(command: str, *args, capture_output: bool = True) -> dict:
    """
    执行 Ave Trade REST API 调用。
    目前暂不支持（需要代理钱包认证）。
    """
    return {"error": "Trade API not yet implemented without Docker"}


def run_ave_wss(command: str, *args, capture_output: bool = True) -> dict:
    """
    WSS 需要 Docker。
    返回错误提示。
    """
    return {"error": "WSS requires Docker. Use cron-based polling instead."}


# ============================================================
# 状态管理（通过 state_manager）
# ============================================================

def get_state_manager():
    """延迟导入 state_manager，避免循环依赖"""
    state_manager_path = GUARDIAN_DIR / "scripts" / "state_manager.py"
    if str(state_manager_path) not in sys.path:
        sys.path.insert(0, str(GUARDIAN_DIR / "scripts"))
    import state_manager
    return state_manager


def add_to_watchlist(token: str, chain: str, symbol: str = None, **kwargs):
    """添加到关注列表（包装 state_manager）"""
    sm = get_state_manager()
    return sm.add_to_watchlist(token, chain, symbol=symbol, **kwargs)


def remove_from_watchlist(token: str, chain: str):
    """从关注列表移除（包装 state_manager）"""
    sm = get_state_manager()
    return sm.remove_from_watchlist(token, chain)


def get_watchlist(token: str = None, chain: str = None):
    """获取关注列表（包装 state_manager）"""
    sm = get_state_manager()
    return sm.get_watchlist(token=token, chain=chain)


def add_alert_rule(token: str, chain: str, alert_type: str, **kwargs):
    """添加警报规则（包装 state_manager）"""
    sm = get_state_manager()
    return sm.add_alert_rule(token, chain, alert_type, **kwargs)


def get_alert_rules(**kwargs):
    """获取警报规则（包装 state_manager）"""
    sm = get_state_manager()
    return sm.get_alert_rules(**kwargs)


def remove_alert_rule(rule_id: str):
    """删除警报规则（包装 state_manager）"""
    sm = get_state_manager()
    return sm.remove_alert_rule(rule_id)


def check_alert_cooldown(rule_id: str):
    """检查警报冷却（包装 state_manager）"""
    sm = get_state_manager()
    return sm.check_alert_cooldown(rule_id)


def trigger_alert(rule_id: str):
    """触发警报（包装 state_manager）"""
    sm = get_state_manager()
    return sm.trigger_alert(rule_id)


def add_strategy(token: str, chain: str, symbol: str, condition: str,
                  condition_value: float, **kwargs):
    """添加策略（包装 state_manager）"""
    sm = get_state_manager()
    return sm.add_strategy(token, chain, symbol, condition, condition_value, **kwargs)


def get_strategies(**kwargs):
    """获取策略（包装 state_manager）"""
    sm = get_state_manager()
    return sm.get_strategies(**kwargs)


def update_strategy(strategy_id: str, updates: dict):
    """更新策略（包装 state_manager）"""
    sm = get_state_manager()
    return sm.update_strategy(strategy_id, updates)


def trigger_strategy(strategy_id: str, **kwargs):
    """触发策略（包装 state_manager）"""
    sm = get_state_manager()
    return sm.trigger_strategy(strategy_id, **kwargs)


def update_scan_state(scan_type: str):
    """更新扫描状态（包装 state_manager）"""
    sm = get_state_manager()
    return sm.update_scan_state(scan_type)


# ============================================================
# 格式化工具
# ============================================================

def format_price(price, decimals: int = None) -> str:
    """格式化价格（根据精度自动选择小数位）"""
    if price is None or price == "":
        return "N/A"
    
    try:
        price = float(price)
    except (ValueError, TypeError):
        return str(price)
    
    if decimals is not None:
        return f"${price:.{decimals}f}"
    
    if price < 0.00001:
        return f"${price:.8f}"
    elif price < 0.001:
        return f"${price:.6f}"
    elif price < 0.1:
        return f"${price:.4f}"
    elif price < 1:
        return f"${price:.4f}"
    elif price < 1000:
        return f"${price:.2f}"
    else:
        return f"${price:,.2f}"


def format_amount(amount: float, decimals: int = 2) -> str:
    """格式化大数字（添加 K/M/B 后缀）"""
    if amount is None:
        return "N/A"
    
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return str(amount)
    
    if abs(amount) >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.{decimals}f}B"
    elif abs(amount) >= 1_000_000:
        return f"${amount / 1_000_000:.{decimals}f}M"
    elif abs(amount) >= 1_000:
        return f"${amount / 1_000:.{decimals}f}K"
    else:
        return f"${amount:.{decimals}f}"


def format_pct(pct: float, show_sign: bool = True) -> str:
    """格式化百分比"""
    if pct is None:
        return "N/A"
    
    try:
        pct = float(pct)
    except (ValueError, TypeError):
        return str(pct)
    
    sign = "+" if show_sign and pct > 0 else ""
    return f"{sign}{pct:.2f}%"


def format_address(address: str, start: int = 6, end: int = 4) -> str:
    """格式化地址（保留首尾，隐藏中间）"""
    if not address:
        return "N/A"
    
    if len(address) <= start + end:
        return address
    
    return f"{address[:start]}...{address[-end:]}"


def format_timestamp(ts: int) -> str:
    """时间戳转可读字符串"""
    if ts is None:
        return "never"
    
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def ts_to_str(ts: int) -> str:
    """format_timestamp 的别名"""
    return format_timestamp(ts)


def now_ts() -> int:
    """返回当前时间戳（秒）"""
    return int(datetime.now(timezone.utc).timestamp())


# ============================================================
# 评分工具
# ============================================================

def concentration_score(top10_pct: float) -> tuple[float, str]:
    """计算持仓集中度评分"""
    score = max(0, min(100, (top10_pct - 0.10) / 0.80 * 100))
    
    if top10_pct > 0.70:
        level = "🔴 极高控盘"
    elif top10_pct > 0.50:
        level = "🟠 高控盘"
    elif top10_pct > 0.30:
        level = "🟡 中等集中"
    else:
        level = "🟢 持仓分散"
    
    return round(score, 1), level


def score_to_stars(score: float) -> str:
    """分数转星级"""
    if score >= 85:
        return "★★★★★"
    elif score >= 65:
        return "★★★★☆"
    elif score >= 45:
        return "★★★☆☆"
    elif score >= 25:
        return "★★☆☆☆"
    else:
        return "★☆☆☆☆"


def score_to_level(score: float, level_names: list = None) -> str:
    """分数转风险等级文字"""
    if level_names is None:
        level_names = ["极低风险", "低风险", "中等风险", "高风险", "极高风险"]
    
    if score >= 75:
        return "🟢 " + level_names[1]
    elif score >= 50:
        return "🟡 " + level_names[2]
    elif score >= 25:
        return "🟠 " + level_names[3]
    else:
        return "🔴 " + level_names[4]


# ============================================================
# 数据验证工具
# ============================================================

def validate_chain(chain: str) -> bool:
    """验证链 ID 是否支持"""
    VALID_CHAINS = {
        "bsc", "eth", "base", "solana",
        "arbitrum", "optimism", "avalanche", "polygon",
        "tron", "ton", "aptos", "sui"
    }
    return chain.lower() in VALID_CHAINS


def validate_token_address(address: str) -> bool:
    """简单验证代币地址格式"""
    if not address:
        return False
    
    address = address.strip()
    
    if address.startswith("0x") and len(address) == 42:
        try:
            int(address[2:], 16)
            return True
        except ValueError:
            return False
    
    if 32 <= len(address) <= 44:
        return True
    
    return False


# ============================================================
# 卡片格式化
# ============================================================

def format_token_basic_card(symbol: str, ca: str, chain: str, data: dict) -> str:
    """格式化代币基本信息卡片"""
    price = data.get("current_price_usd") or data.get("price")
    change_24h = data.get("price_change_24h", "N/A")
    tvl = data.get("tvl", 0)
    volume = data.get("tx_volume_u_24h", 0)
    market_cap = data.get("market_cap", 0)
    holders = data.get("holders", 0)
    
    price_str = format_price(price) if price else "N/A"
    change_str = format_pct(float(change_24h)) if change_24h not in (None, "N/A") else str(change_24h)
    tvl_str = format_amount(float(tvl)) if tvl else "$N/A"
    volume_str = format_amount(float(volume)) if volume else "$N/A"
    mc_str = format_amount(float(market_cap)) if market_cap else "$N/A"
    holders_str = f"{holders:,}" if holders else "N/A"
    
    return f"""🐸 {symbol}（{format_address(ca)}）
🌍 链：{chain.upper()}
━━━━━━━━━━━━━━━━━━
💰 价格：{price_str}（{change_str} / 24h）
📊 市值：{mc_str}
💧 TVL：{tvl_str}
📈 24h 成交量：{volume_str}
👥 持仓人数：{holders_str}"""


def format_risk_card(symbol: str, ca: str, chain: str, risk_data: dict) -> str:
    """格式化风险评估卡片"""
    is_honeypot = risk_data.get("is_honeypot", False)
    buy_tax = risk_data.get("buy_tax", 0)
    sell_tax = risk_data.get("sell_tax", 0)
    renounced = risk_data.get("ownership_renounced", False)
    has_mint = risk_data.get("has_mint_method", False)
    has_black = risk_data.get("has_black_method", False)
    risk_level = risk_data.get("risk_level", "UNKNOWN")
    risk_score = risk_data.get("risk_score", "N/A")
    
    honeypot_icon = "🚨" if is_honeypot else "✅"
    renounced_icon = "✅" if renounced else "⚠️"
    mint_icon = "❌" if has_mint else "✅"
    black_icon = "❌" if has_black else "✅"
    
    return f"""🔒 合约安全评估
━━━━━━━━━━━━━━━━━━
🚨 蜜罐检测：{honeypot_icon} {'是' if is_honeypot else '否'}
💰 买税：{buy_tax}% | 卖税：{sell_tax}%
🔐 所有权放弃：{renounced_icon} {'是' if renounced else '否（风险）'}
⚙️ Mint方法：{mint_icon} {'有（通胀风险）' if has_mint else '无'}
🚫 黑名单方法：{black_icon} {'有' if has_black else '无'}
📊 风险等级：{risk_level}（{risk_score}分）"""


def format_holders_card(symbol: str, holders_data: list) -> str:
    """格式化持仓分布卡片"""
    if not holders_data:
        return "👥 持仓分布\n━━━━━━━━━━━━━━━━━━\n数据暂不可用"
    
    top10_pct = sum(h.get("percent", 0) for h in holders_data[:10]) / 100
    top5_pct = sum(h.get("percent", 0) for h in holders_data[:5]) / 100
    
    score, level = concentration_score(top10_pct)
    
    lines = [
        "👥 持仓分布",
        "━━━━━━━━━━━━━━━━━━",
        level,
        f"Top5 占比：{top5_pct*100:.1f}%",
        f"Top10 占比：{top10_pct*100:.1f}%",
        "",
        "Top 5 持仓地址："
    ]
    
    for i, h in enumerate(holders_data[:5], 1):
        addr = format_address(h.get("address", ""), 6, 4)
        pct = h.get("percent", 0) / 100
        balance = h.get("balance", 0)
        balance_str = format_amount(float(balance)) if balance else "N/A"
        lines.append(f"  {i}. {addr} — {balance_str}（{pct*100:.1f}%）")
    
    return "\n".join(lines)


# ============================================================
# 主函数（测试入口）
# ============================================================

def main():
    """测试工具函数"""
    print("Ave Guardian Utils — Test")
    print()
    
    # 测试凭证
    try:
        creds = get_credentials()
        print(f"✅ Credentials: plan={creds['api_plan']}, key={creds['ave_api_key'][:8]}...")
    except Exception as e:
        print(f"❌ Credentials error: {e}")
        return
    
    print()
    
    # 测试直接 API 调用
    print("Testing direct Ave API (no Docker)...")
    
    # 1. 搜索代币
    print("\n1. Search PEPE:")
    result = run_ave_rest("search", "--keyword", "PEPE", "--chain", "bsc", "--limit", "3")
    if "error" in result:
        print(f"   ❌ Error: {result['error']}")
    else:
        data = result.get("data", [])
        if isinstance(data, dict):
            data = data.get("data", data.get("tokens", []))
        print(f"   ✅ Found {len(data) if isinstance(data, list) else 'N/A'} results")
        for item in (data[:3] if isinstance(data, list) else []):
            print(f"   - {item.get('symbol', '?')} @ {item.get('chain', '?')}: {item.get('token', '?')[:20]}...")
    
    # 2. 代币详情
    print("\n2. Token detail (PEPE):")
    result = run_ave_rest("token", "--address", "0xf43c8f27754829202d2f66650eb3f6d168c288dc", "--chain", "bsc")
    if "error" in result:
        print(f"   ❌ Error: {result['error']}")
    else:
        data = result.get("data", {})
        if isinstance(data, dict):
            print(f"   ✅ symbol={data.get('symbol')}, price={data.get('current_price_usd')}, TVL={data.get('tvl')}")
        else:
            print(f"   ✅ Response type: {type(data)}")
    
    # 3. 持仓
    print("\n3. Holders:")
    result = run_ave_rest("holders", "--address", "0xf43c8f27754829202d2f66650eb3f6d168c288dc", "--chain", "bsc", "--limit", "5")
    if "error" in result:
        print(f"   ❌ Error: {result['error']}")
    else:
        print(f"   ✅ Response keys: {list(result.keys())}")
        data = result.get("data", [])
        print(f"   Found {len(data) if isinstance(data, list) else 'N/A'} holders")
    
    # 4. K线
    print("\n4. K-lines:")
    result = run_ave_rest("kline-token", "--address", "0xf43c8f27754829202d2f66650eb3f6d168c288dc", "--chain", "bsc", "--interval", "60", "--size", "6")
    if "error" in result:
        print(f"   ❌ Error: {result['error']}")
    else:
        print(f"   ✅ status={result.get('status')}, msg={result.get('msg')}")
    
    # 测试格式化
    print("\n5. Formatters:")
    print(f"   format_price(0.00000523) = {format_price(0.00000523)}")
    print(f"   format_price(1234.56) = {format_price(1234.56)}")
    print(f"   format_amount(1234567) = {format_amount(1234567)}")
    print(f"   format_pct(-5.23) = {format_pct(-5.23)}")
    print(f"   format_address('0xf43c8f27754829202d2f66650eb3f6d168c288dc') = {format_address('0xf43c8f27754829202d2f66650eb3f6d168c288dc')}")
    print(f"   concentration_score(0.712) = {concentration_score(0.712)}")
    print(f"   score_to_stars(72.3) = {score_to_stars(72.3)}")


if __name__ == "__main__":
    main()
