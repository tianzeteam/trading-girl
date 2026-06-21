#!/usr/bin/env python3
"""
交易媛 WS Monitor — Real-time market signal detection daemon.

Connects to Bitget public WebSocket, computes RSI-14, tracks OI and funding
rates, and writes signals to pending-signal.json for the LLM pipeline.

Run:
  BITGET_PAPER_API_KEY=xxx BITGET_PAPER_SECRET_KEY=xxx BITGET_PAPER_PASSPHRASE=xxx \\
  python src/ws-monitor.py

Optional env vars:
  SIGNAL_FILE       path to pending-signal.json (default: ./pending-signal.json)
  HISTORY_FILE      path to RSI history persistence (default: ./ws-history.json)
  HEALTH_FILE       path to health check output (default: ./ws-health.json)
  LOG_FILE          path to log file (default: ./ws-monitor.log)
"""
import json, os, sys, time, signal, logging, threading, tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Config ──────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
WS_URL = "wss://ws.bitget.com/v2/ws/public"
REST_BASE = "https://api.bitget.com"

CONFIG = {
    "ws_url": WS_URL, "ws_ping_interval": 30, "ws_ping_timeout": 10,
    "rest_base": REST_BASE, "candle_limit": 30,
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "btc_rsi_oversold": 35, "btc_rsi_overbought": 65,
    "btc_rsi_approach_oversold": 40, "btc_rsi_approach_overbought": 60,
    "btc_change_threshold": 0.025, "btc_funding_high": 0.0001, "btc_funding_low": -0.0001,
    "eth_rsi_oversold": 30, "eth_rsi_overbought": 70,
    "eth_rsi_approach_oversold": 35, "eth_rsi_approach_overbought": 65,
    "eth_change_threshold": 0.035, "eth_funding_low": -0.0001,
    "trend_min_consecutive": 3,
    "oi_poll_interval": 300, "oi_surge_threshold": 0.05, "oi_drop_threshold": -0.05,
    "cooldown_same_signal": 3600, "cooldown_any_signal": 1800,
    "signal_file": os.environ.get("SIGNAL_FILE", str(BASE_DIR / "pending-signal.json")),
    "history_file": os.environ.get("HISTORY_FILE", str(BASE_DIR / "ws-history.json")),
    "health_file": os.environ.get("HEALTH_FILE", str(BASE_DIR / "ws-health.json")),
    "heartbeat_interval": 300,
    "log_file": os.environ.get("LOG_FILE", str(BASE_DIR / "ws-monitor.log")),
    "log_level": "INFO",
}

# ── Demo API credentials (used only by demo_execute.py) ──
# These are loaded from environment variables
# BITGET_PAPER_API_KEY, BITGET_PAPER_SECRET_KEY, BITGET_PAPER_PASSPHRASE

# ── Logging ──
_log = None
def get_logger():
    global _log
    if _log: return _log
    _log = logging.getLogger("ws-monitor")
    _log.setLevel(getattr(logging, CONFIG["log_level"]))
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    Path(CONFIG["log_file"]).parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(CONFIG["log_file"]); fh.setFormatter(fmt)
    _log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout); sh.setFormatter(fmt)
    _log.addHandler(sh)
    return _log

log = get_logger()

# ── Helper: atomic file I/O ──
def atomic_write(path, data):
    tmp = path + ".tmp." + str(os.getpid())
    try:
        with open(tmp, "w") as f: json.dump(data, f, ensure_ascii=False)
        os.rename(tmp, path)
    except Exception as e:
        log.error(f"atomic_write failed ({path}): {e}")
        try: os.unlink(tmp)
        except FileNotFoundError: pass

def atomic_read(path):
    try:
        with open(path) as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return None

# ── Full implementation available at: github.com/YOUR_USER/trading-yuan ──
# The complete ws-monitor.py (1017 lines) includes:
# • RSI-14 computation from streaming candlestick data
# • OI tracking via OKX REST API (fallback)
# • Trend detection (consecutive direction changes)
# • Funding rate anomaly detection
# • Signal dedup + cooldown logic
# • Exponential backoff auto-reconnect
# • Health heartbeat
# • LLM dispatch pipeline: analysis → evaluator → trade decision
# • Threaded non-blocking execution

if __name__ == "__main__":
    print(f"交易媛 WS Monitor v2")
    print(f"Full source code available at the project repository.")
    print(f"Run the complete version for production use.")
