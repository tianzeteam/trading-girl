#!/usr/bin/env python3
"""
交易媛 Demo Trade Executor — Execute trades on Bitget Demo Trading API.

Usage:
  python src/demo_execute.py '{"action":"open_long","symbol":"BTC","size":"0.001","reason":"RSI oversold bounce"}'

Actions: open_long | open_short | close_long | close_short | skip
Symbols: BTC | ETH
"""
import hmac
import hashlib
import base64
import json
import sys
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ── Credentials ──
def _load_creds():
    """Load Bitget Demo API credentials from environment or .env file."""
    api_key = os.environ.get("BITGET_PAPER_API_KEY", "")
    secret_key = os.environ.get("BITGET_PAPER_SECRET_KEY", "")
    passphrase = os.environ.get("BITGET_PAPER_PASSPHRASE", "")

    if not all([api_key, secret_key, passphrase]):
        env_path = os.environ.get("DOTENV_PATH", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if k == "BITGET_PAPER_API_KEY" and not api_key:
                        api_key = v
                    elif k == "BITGET_PAPER_SECRET_KEY" and not secret_key:
                        secret_key = v
                    elif k == "BITGET_PAPER_PASSPHRASE" and not passphrase:
                        passphrase = v
    return api_key, secret_key, passphrase

API_KEY, SECRET_KEY, PASSPHRASE = _load_creds()

# ── Config ──
BASE_DIR = Path(__file__).parent.parent
TRADE_LOG = os.environ.get("TRADE_LOG", str(BASE_DIR / "trade-log.json"))
MAX_POSITION = {"BTC": 0.01, "ETH": 0.1}
DAILY_TRADE_LIMIT = 10
SYMBOL_MAP = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
SIZE_MIN = {"BTC": 0.001, "ETH": 0.01}

# ── Signing ──
def sign(method, req_path, qs, body_str, ts):
    msg = ts + method + req_path + ("?" + qs if qs else "") + body_str
    mac = hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

def api_call(method, path, qs="", body=None):
    ts = str(int(time.time() * 1000))
    body_str = json.dumps(body) if body else ""
    sig = sign(method, path, qs, body_str, ts)
    url = "https://api.bitget.com" + path + ("?" + qs if qs else "")
    headers = {
        "ACCESS-KEY": API_KEY,
        "ACCESS-SIGN": sig,
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json",
        "paptrading": "1",
    }
    data = body_str.encode() if body_str else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": True, "code": e.code, "body": e.read().decode()}
    except Exception as e:
        return {"_error": True, "message": str(e)}

# ── Risk controls ──
def _check_daily_limit():
    try:
        log_data = json.load(open(TRADE_LOG)) if os.path.exists(TRADE_LOG) else []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count = sum(1 for t in log_data if str(t.get("ts", "")).startswith(today))
        if count >= DAILY_TRADE_LIMIT:
            return False, "Daily trade limit {} reached".format(DAILY_TRADE_LIMIT)
        return True, ""
    except Exception:
        return True, ""

def _check_position(symbol, size, action):
    if "open" in action and size > MAX_POSITION.get(symbol, 0.01):
        return False, "Max {} {} per trade".format(symbol, MAX_POSITION.get(symbol, 0.01))
    return True, ""

# ── Execute ──
def execute(cmd):
    action = cmd.get("action", "skip")
    symbol = cmd.get("symbol", "").upper()
    size = str(cmd.get("size", "0.001"))
    reason = cmd.get("reason", "")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    result = {
        "action": action, "symbol": symbol, "size": size,
        "reason": reason, "success": False, "order_id": "",
        "price": "", "fee": "", "error": "", "ts": ts,
    }

    if action == "skip":
        result["success"] = True
        result["msg"] = "Skip - no trade"
        return result

    # Daily limit
    ok, msg = _check_daily_limit()
    if not ok:
        result["error"] = msg
        return result

    # Symbol validation
    pair = SYMBOL_MAP.get(symbol)
    if not pair:
        result["error"] = "Unsupported symbol: {}".format(symbol)
        return result

    # Min size
    sz = float(size)
    min_sz = SIZE_MIN.get(symbol, 0.001)
    if sz < min_sz:
        result["error"] = "Min {} size: {}".format(symbol, min_sz)
        return result

    # Position limit
    ok, msg = _check_position(symbol, sz, action)
    if not ok:
        result["error"] = msg
        return result

    # Action mapping (hedge mode)
    sides = {
        "open_long": ("buy", "open"),
        "close_long": ("buy", "close"),
        "open_short": ("sell", "open"),
        "close_short": ("sell", "close"),
    }
    if action not in sides:
        result["error"] = "Unknown action: {}".format(action)
        return result
    side, trade_side = sides[action]

    order = {
        "symbol": pair,
        "productType": "USDT-FUTURES",
        "marginMode": "crossed",
        "marginCoin": "USDT",
        "side": side,
        "tradeSide": trade_side,
        "orderType": "market",
        "size": size,
    }

    resp = api_call("POST", "/api/v2/mix/order/place-order", "", order)
    if resp.get("code") == "00000":
        result["success"] = True
        result["order_id"] = resp["data"]["orderId"]
        # Fetch fill price
        time.sleep(1)
        fills = api_call(
            "GET", "/api/v2/mix/order/fills",
            "productType=USDT-FUTURES&orderId={}".format(result["order_id"]),
        )
        if fills.get("code") == "00000" and fills.get("data", {}).get("fillList"):
            p = fills["data"]["fillList"][0]
            result["price"] = p.get("price", "")
            result["fee"] = p.get("feeDetail", [{}])[0].get("totalFee", "")
    else:
        result["error"] = resp.get("body", str(resp))

    _append_log(result)
    return result

def _append_log(entry):
    try:
        log = json.load(open(TRADE_LOG)) if os.path.exists(TRADE_LOG) else []
        log.append(entry)
        if len(log) > 200:
            log = log[-200:]
        json.dump(log, open(TRADE_LOG, "w"), ensure_ascii=False, indent=2)
    except Exception:
        pass

if __name__ == "__main__":
    cmd = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {"action": "skip"}
    result = execute(cmd)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("success") else 1)
