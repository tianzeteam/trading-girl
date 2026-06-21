#!/usr/bin/env python3
"""
交易媛 Demo 执行器
接收参数 → 调 Bitget Demo API 开/平仓 → 记日志

用法:
  python3 demo_execute.py '{"action":"open_long","symbol":"BTC","size":"0.001","reason":"RSI超卖反弹"}'
  python3 demo_execute.py '{"action":"close_long","symbol":"BTC","size":"0.001","reason":"趋势反转"}'

action: open_long | open_short | close_long | close_short | skip
symbol: BTC | ETH
size: 数量（BTC最小0.001, ETH最小0.01）
"""
import hmac, hashlib, base64, json, sys, os, time, urllib.request, urllib.error
from datetime import datetime, timezone

# ── 配置 ──
DEMO_API_KEY = os.environ.get('BITGET_PAPER_API_KEY', '')
DEMO_SECRET_KEY = os.environ.get('BITGET_PAPER_SECRET_KEY', '')
DEMO_PASSPHRASE = os.environ.get('BITGET_PAPER_PASSPHRASE', '')

TRADE_LOG = "/home/block0/.hermes/profiles/jiaoyiyuan/trade-log.json"

# 限仓
MAX_POSITION = {"BTC": 0.01, "ETH": 0.1}
DAILY_TRADE_LIMIT = 20  # 只计成功交易

# ── 如果环境变量没读到，从 .env 文件补充 ──
SIZE_MIN = {"BTC": 0.001, "ETH": 0.01}
SYMBOL_MAP = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

if not all([DEMO_API_KEY, DEMO_SECRET_KEY, DEMO_PASSPHRASE]):
    try:
        env_path = '/home/block0/.hermes/profiles/jiaoyiyuan/.env'
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    if k.strip() == 'BITGET_PAPER_API_KEY' and not DEMO_API_KEY:
                        DEMO_API_KEY = v.strip()
                    elif k.strip() == 'BITGET_PAPER_SECRET_KEY' and not DEMO_SECRET_KEY:
                        DEMO_SECRET_KEY = v.strip()
                    elif k.strip() == 'BITGET_PAPER_PASSPHRASE' and not DEMO_PASSPHRASE:
                        DEMO_PASSPHRASE = v.strip()
    except Exception:
        pass


# ── 签名 ──
def sign(method, req_path, qs, body_str, ts):
    if qs:
        msg = ts + method + req_path + '?' + qs + body_str
    else:
        msg = ts + method + req_path + body_str
    mac = hmac.new(DEMO_SECRET_KEY.encode(), msg.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def api_call(method, path, qs='', body=None):
    """调 Demo API"""
    ts = str(int(time.time() * 1000))
    body_str = json.dumps(body) if body else ''
    sig = sign(method, path, qs, body_str, ts)
    url = f'https://api.bitget.com{path}' + (f'?{qs}' if qs else '')
    headers = {
        'ACCESS-KEY': DEMO_API_KEY,
        'ACCESS-SIGN': sig,
        'ACCESS-TIMESTAMP': ts,
        'ACCESS-PASSPHRASE': DEMO_PASSPHRASE,
        'Content-Type': 'application/json',
        'paptrading': '1',
    }
    data = body_str.encode() if body_str else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {'_error': True, 'code': e.code, 'body': e.read().decode()}
    except Exception as e:
        return {'_error': True, 'message': str(e)}


# ── 风控检查 ──
def check_daily_limit() -> tuple:
    """检查今日交易次数。返回 (ok, reason)"""
    try:
        if os.path.exists(TRADE_LOG):
            with open(TRADE_LOG) as f:
                log = json.load(f)
        else:
            log = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_trades = [t for t in log if t.get("ts", "").startswith(today) and t.get("success")]
        if len(today_trades) >= DAILY_TRADE_LIMIT:
            return False, f"今日已达交易上限 {DAILY_TRADE_LIMIT} 笔"
        return True, ""
    except Exception:
        return True, ""


def check_position_limit(symbol: str, size: float, action: str) -> tuple:
    """检查是否超过最大仓位限制"""
    max_sz = MAX_POSITION.get(symbol, 0.01)
    if "open" in action and size > max_sz:
        return False, f"{symbol} 单笔最大 {max_sz}，请求 {size} 超限"
    return True, ""


# ── 余额查询 ──
def fetch_balance() -> dict:
    """查 Demo 账户余额，返回 {coin: available}"""
    try:
        resp = api_call('GET', '/api/v2/mix/account/accounts', 'productType=USDT-FUTURES')
        if resp.get('code') == '00000':
            bal = {}
            for item in resp.get('data', []):
                bal[item.get('marginCoin', item.get('coin', ''))] = item.get('available', '0')
            return bal
        return {}
    except Exception:
        return {}


# ── 交易执行 ──
def execute(cmd: dict) -> dict:
    """执行交易指令，返回结果"""
    action = cmd.get("action", "skip")
    symbol = cmd.get("symbol", "").upper()
    size = str(cmd.get("size", "0.001"))
    reason = cmd.get("reason", "")

    # 交易前余额
    bal_before = fetch_balance()

    result = {
        "action": action,
        "symbol": symbol,
        "size": size,
        "reason": reason,
        "success": False,
        "order_id": "",
        "price": "",
        "error": "",
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "balance_before": bal_before,
    }

    if action == "skip":
        result["success"] = True
        result["msg"] = "跳过，不操作"
        return result

    # 风控
    ok, msg = check_daily_limit()
    if not ok:
        result["error"] = msg
        return result

    symbol_pair = SYMBOL_MAP.get(symbol)
    if not symbol_pair:
        result["error"] = f"不支持的标的: {symbol}"
        return result

    size_float = float(size)
    min_sz = SIZE_MIN.get(symbol, 0.001)
    if size_float < min_sz:
        result["error"] = f"{symbol} 最小开仓 {min_sz}"
        return result

    ok, msg = check_position_limit(symbol, size_float, action)
    if not ok:
        result["error"] = msg
        return result

    # 参数映射
    side_map = {
        "open_long": ("buy", "open"),
        "close_long": ("buy", "close"),
        "open_short": ("sell", "open"),
        "close_short": ("sell", "close"),
    }
    if action not in side_map:
        result["error"] = f"未知操作: {action}"
        return result

    side, trade_side = side_map[action]

    order = {
        "symbol": symbol_pair,
        "productType": "USDT-FUTURES",
        "marginMode": "crossed",
        "marginCoin": "USDT",
        "side": side,
        "tradeSide": trade_side,
        "orderType": "market",
        "size": size,
    }

    resp = api_call('POST', '/api/v2/mix/order/place-order', '', order)

    if resp.get('code') == '00000':
        result["success"] = True
        result["order_id"] = resp["data"]["orderId"]
        result["client_oid"] = resp["data"]["clientOid"]
        # 查成交价
        time.sleep(1)
        fills = api_call('GET', '/api/v2/mix/order/fills',
                         f'productType=USDT-FUTURES&orderId={result["order_id"]}')
        if fills.get('code') == '00000' and fills.get('data', {}).get('fillList'):
            p = fills['data']['fillList'][0]
            result["price"] = p.get("price", "")
            result["fee"] = p.get("feeDetail", [{}])[0].get("totalFee", "")

        # 交易后余额
        time.sleep(0.5)
        result["balance_after"] = fetch_balance()
    else:
        result["error"] = resp.get('body', str(resp))

    # 写日志
    _append_log(result)
    return result


def _append_log(entry: dict):
    try:
        if os.path.exists(TRADE_LOG):
            with open(TRADE_LOG) as f:
                log = json.load(f)
        else:
            log = []
        log.append(entry)
        # 保留最近 200 条
        if len(log) > 200:
            log = log[-200:]
        with open(TRADE_LOG, 'w') as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        cmd = {"action": "skip"}
    else:
        try:
            cmd = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            cmd = {"action": "skip", "error": f"JSON解析失败: {sys.argv[1][:200]}"}

    result = execute(cmd)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("success") else 1)
