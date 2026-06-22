#!/usr/bin/env python3
"""
交易媛 WS Monitor
常驻后台，通过 Bitget 公开 WebSocket 实时监听市场数据。
检测到信号 → 原子写入 pending-signal.json → 交 LLM 决策层处理。

鲁棒性设计：
- 自动重连（指数退避 1s-60s）
- 启动时 REST 获取历史蜡烛初始化 RSI
- 信号去重 + 速率限制 + 冷却
- 原子写文件（防竞态）
- **持久化 RSI 历史 + 信号日志（跨重启不丢）**
- 心跳健康检查
- 优雅关闭（SIGTERM/SIGINT）
"""

import json
import os
import sys
import time
import signal
import logging
import threading
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── 配置 ──────────────────────────────────────────
CONFIG = {
    # WS
    "ws_url": "wss://ws.bitget.com/v2/ws/public",
    "ws_ping_interval": 30,
    "ws_ping_timeout": 10,

    # REST（启动时取历史蜡烛）
    "rest_base": "https://api.bitget.com",
    "candle_limit": 30,

    # 标的
    "symbols": ["BTCUSDT", "ETHUSDT"],

    # 信号阈值
    "btc_rsi_oversold": 35,
    "btc_rsi_overbought": 65,
    "btc_rsi_approach_oversold": 40,
    "btc_rsi_approach_overbought": 60,
    "btc_change_threshold": 0.025,
    "btc_funding_high": 0.0001,
    "btc_funding_low": -0.0001,

    "eth_rsi_oversold": 30,
    "eth_rsi_overbought": 70,
    "eth_rsi_approach_oversold": 35,
    "eth_rsi_approach_overbought": 65,
    "eth_change_threshold": 0.035,
    "eth_funding_low": -0.0001,

    # 趋势预警：连续 N 次同向
    "trend_min_consecutive": 3,

    # OI 追踪
    "oi_poll_interval": 300,
    "oi_surge_threshold": 0.05,
    "oi_drop_threshold": -0.05,

    # 冷却（秒）
    "cooldown_same_signal": 3600,
    "cooldown_any_signal": 1800,

    # 信号输出
    "signal_file": "/home/block0/.hermes/profiles/jiaoyiyuan/pending-signal.json",

    # 持久化（跨重启）
    "history_file": "/home/block0/.hermes/profiles/jiaoyiyuan/ws-history.json",

    # 健康检查
    "health_file": "/home/block0/.hermes/profiles/jiaoyiyuan/ws-health.json",
    "heartbeat_interval": 300,

    # 日志
    "log_file": "/home/block0/.hermes/profiles/jiaoyiyuan/logs/ws-monitor.log",
    "log_level": "INFO",
}

# ── 日志 ──────────────────────────────────────────
_log = None
def get_logger():
    global _log
    if _log:
        return _log
    _log = logging.getLogger("ws-monitor")
    _log.setLevel(getattr(logging, CONFIG["log_level"]))
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    Path(CONFIG["log_file"]).parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(CONFIG["log_file"])
    fh.setFormatter(fmt)
    _log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    _log.addHandler(sh)
    return _log

log = get_logger()

# ── 原子写 ────────────────────────────────────────
def atomic_write(path: str, data: dict):
    tmp = path + ".tmp." + str(os.getpid())
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False)
        os.rename(tmp, path)
    except Exception as e:
        log.error(f"atomic_write failed ({path}): {e}")
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass

def atomic_read(path: str):
    """安全读 JSON，不存在或损坏就返回默认值"""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

# ── REST 历史蜡烛 ────────────────────────────────
def fetch_historical_candles(symbol: str, limit: int = 30):
    url = (f"{CONFIG['rest_base']}/api/v2/mix/market/candles"
           f"?productType=USDT-FUTURES&symbol={symbol}"
           f"&granularity=4H&limit={limit}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode())
        if raw.get("code") != "00000":
            log.error(f"REST candles error for {symbol}: {raw}")
            return []
        data = raw.get("data", [])
        candles = []
        for c in data:
            candles.append({
                "ts": int(c[0]),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            })
        candles.sort(key=lambda x: x["ts"])
        log.info(f"Fetched {len(candles)} historical candles for {symbol}")
        return candles
    except Exception as e:
        log.error(f"Failed to fetch candles for {symbol}: {e}")
        return []

def fetch_oi(symbol: str) -> float:
    """通过 OKX REST API 获取 OI（Binance geo-blocked，换 OKX）"""
    symbol_map = {"BTCUSDT": "BTC-USDT-SWAP", "ETHUSDT": "ETH-USDT-SWAP"}
    inst_id = symbol_map.get(symbol)
    if not inst_id:
        return None
    url = f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode())
        if raw.get("code") != "0":
            return None
        items = raw.get("data", [])
        if items:
            # oiCcy = OI in coin (e.g. BTC), oiUsd = OI in USD
            return float(items[0]["oiCcy"])
        return None
    except Exception:
        return None


# ── RSI 计算 ──────────────────────────────────────
def calc_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    if al == 0:
        return 100.0
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag / al)), 2)


# ── 市场状态 ──────────────────────────────────────
class MarketState:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.last_price = None
        self.change24h = None
        self.funding_rate = None
        self.high24h = None
        self.low24h = None
        self.last_ts = None

        self.completed_candles = []
        self.current_candle = None
        self.rsi = None
        self.estimated_rsi = None
        self.rsi_history = []
        self.rsi_direction = "横盘"

        self.last_signal_time = {}
        self.subscribed = False

        # OI 追踪
        self.open_interest = None
        self.oi_history = []
        self.oi_direction = "横盘"

    def update_ticker(self, data: dict):
        self.last_price = float(data.get("lastPr", self.last_price or 0))
        self.change24h = float(data.get("change24h", self.change24h or 0))
        self.funding_rate = float(data.get("fundingRate", self.funding_rate or 0))
        self.high24h = float(data.get("high24h", self.high24h or 0))
        self.low24h = float(data.get("low24h", self.low24h or 0))
        self.last_ts = int(data.get("ts", time.time() * 1000))

    def update_candle(self, candle_data: list):
        ts = int(candle_data[0])
        close = float(candle_data[4])
        high = float(candle_data[2])
        low = float(candle_data[3])
        vol = float(candle_data[5])

        if self.current_candle and self.current_candle["ts"] == ts:
            self.current_candle["high"] = max(self.current_candle["high"], high)
            self.current_candle["low"] = min(self.current_candle["low"], low)
            self.current_candle["close"] = close
            self.current_candle["volume"] = vol
        else:
            if self.current_candle:
                self.completed_candles.append(self.current_candle)
                if len(self.completed_candles) > 30:
                    self.completed_candles = self.completed_candles[-30:]
            self.current_candle = {
                "ts": ts, "open": float(candle_data[1]),
                "high": high, "low": low, "close": close, "volume": vol,
            }

        self._recalc_rsi()

    def _recalc_rsi(self):
        closes = [c["close"] for c in self.completed_candles]
        if len(closes) >= 14:
            self.rsi = calc_rsi(closes)
        if self.current_candle:
            all_closes = closes + [self.current_candle["close"]]
            if len(all_closes) >= 14:
                self.estimated_rsi = calc_rsi(all_closes)

        rsi_val = self.rsi or self.estimated_rsi
        if rsi_val is not None:
            self.rsi_history.append(rsi_val)
            if len(self.rsi_history) > 20:
                self.rsi_history = self.rsi_history[-20:]
            self._update_direction()

    def _update_direction(self):
        n = CONFIG["trend_min_consecutive"]
        hist = self.rsi_history
        if len(hist) >= n:
            last_n = hist[-n:]
            if all(last_n[i] < last_n[i + 1] for i in range(n - 1)):
                self.rsi_direction = "上行"
            elif all(last_n[i] > last_n[i + 1] for i in range(n - 1)):
                self.rsi_direction = "下行"
            else:
                self.rsi_direction = "横盘"

    def is_on_cooldown(self, signal_type: str) -> bool:
        now = time.time()
        last = self.last_signal_time.get(signal_type, 0)
        if now - last < CONFIG["cooldown_same_signal"]:
            return True
        last_any = max(self.last_signal_time.values()) if self.last_signal_time else 0
        if now - last_any < CONFIG["cooldown_any_signal"]:
            return True
        return False

    def mark_signal(self, signal_type: str):
        self.last_signal_time[signal_type] = time.time()

    def update_oi(self, oi_value: float):
        """更新 OI，追踪变化"""
        if self.open_interest is not None and oi_value:
            self.oi_history.append(self.open_interest)
            if len(self.oi_history) > 20:
                self.oi_history = self.oi_history[-20:]
        self.open_interest = oi_value
        # 更新 OI 方向
        n = CONFIG["trend_min_consecutive"]
        hist = self.oi_history
        if len(hist) >= n:
            # 比较当前 OI 和倒数第 n 个 OI 判断趋势
            if oi_value > hist[-n+1] if len(hist) >= n else oi_value > hist[0]:
                self.oi_direction = "上行"
            elif oi_value < hist[-n+1] if len(hist) >= n else oi_value < hist[0]:
                self.oi_direction = "下行"
            else:
                self.oi_direction = "横盘"

    def summary(self) -> dict:
        return {
            "price": self.last_price,
            "change24h": self.change24h,
            "funding_rate": self.funding_rate,
            "rsi": self.rsi,
            "estimated_rsi": self.estimated_rsi,
            "direction": self.rsi_direction,
            "open_interest": self.open_interest,
            "oi_direction": self.oi_direction,
            "oi_change_pct": round(
                (self.open_interest - (self.oi_history[-1] if self.oi_history else self.open_interest))
                / (self.oi_history[-1] if self.oi_history else self.open_interest) * 100, 2
            ) if self.open_interest and self.oi_history else None,
        }


# ── 持久化历史 ────────────────────────────────────
class HistoryStore:
    """跨重启持久化 RSI 趋势和信号日志"""

    def __init__(self, path: str):
        self.path = path
        self.data = {
            "btc": {"rsi_history": [], "signals_log": []},
            "eth": {"rsi_history": [], "signals_log": []},
        }
        self._load()

    def _load(self):
        saved = atomic_read(self.path)
        if saved and "btc" in saved:
            self.data = saved
            log.info(f"Loaded history: BTC {len(self.data['btc']['rsi_history'])} RSI points, "
                     f"{len(self.data['btc']['signals_log'])} signals | "
                     f"ETH {len(self.data['eth']['rsi_history'])} RSI points")
        else:
            log.info("No saved history found, starting fresh")

    def save(self):
        atomic_write(self.path, self.data)

    def append_rsi(self, symbol: str, rsi: float, price: float):
        key = "btc" if symbol == "BTCUSDT" else "eth"
        self.data[key]["rsi_history"].append({
            "ts": int(time.time()),
            "rsi": rsi,
            "price": price,
        })
        # 最多保留 200 条（约 8h 的 4h 数据）
        if len(self.data[key]["rsi_history"]) > 200:
            self.data[key]["rsi_history"] = self.data[key]["rsi_history"][-200:]

    def append_signal(self, symbol: str, signal: dict):
        key = "btc" if symbol == "BTCUSDT" else "eth"
        self.data[key]["signals_log"].append({
            "ts": int(time.time()),
            "type": signal["type"],
            "severity": signal["severity"],
            "detail": signal.get("detail", ""),
            "value": signal.get("value"),
        })
        # 最多保留 50 条
        if len(self.data[key]["signals_log"]) > 50:
            self.data[key]["signals_log"] = self.data[key]["signals_log"][-50:]

    def get_24h_rsi(self, symbol: str) -> list:
        """返回过去 24h 的 RSI 值列表"""
        key = "btc" if symbol == "BTCUSDT" else "eth"
        cutoff = time.time() - 86400
        return [p["rsi"] for p in self.data[key]["rsi_history"] if p["ts"] > cutoff]

    def get_today_signals(self, symbol: str) -> int:
        key = "btc" if symbol == "BTCUSDT" else "eth"
        cutoff = time.time() - 86400
        return sum(1 for s in self.data[key]["signals_log"] if s["ts"] > cutoff)

    def get_similar_signal(self, symbol: str, signal_type: str):
        """找上次同类型信号，返回简况"""
        key = "btc" if symbol == "BTCUSDT" else "eth"
        for s in reversed(self.data[key]["signals_log"]):
            if s["type"] == signal_type and s["ts"] != int(time.time()):
                return s
        return None


# ── 信号检测器 ────────────────────────────────────
class SignalDetector:
    def __init__(self, btc: MarketState, eth: MarketState, history: HistoryStore):
        self.btc = btc
        self.eth = eth
        self.history = history
        self.signal_id = 0

    def detect(self) -> list:
        signals = []
        self.signal_id += 1
        signals.extend(self._check_symbol("BTC", self.btc))
        signals.extend(self._check_symbol("ETH", self.eth))
        signals.extend(self._check_oi("BTC", self.btc))
        signals.extend(self._check_oi("ETH", self.eth))
        return signals

    def _check_oi(self, label: str, state: MarketState) -> list:
        """检测 OI 变化信号"""
        signals = []
        oi = state.open_interest
        oi_pct = state.summary().get("oi_change_pct")
        if oi is None or oi_pct is None or len(state.oi_history) < 2:
            return signals
        p = CONFIG
        if oi_pct > p["oi_surge_threshold"] * 100:
            signals.append({
                "type": "oi_surge", "symbol": label, "severity": "medium",
                "value": oi_pct, "detail": f"OI 暴增 {oi_pct:.1f}%，博弈加剧",
            })
        elif oi_pct < p["oi_drop_threshold"] * 100:
            signals.append({
                "type": "oi_drop", "symbol": label, "severity": "medium",
                "value": oi_pct, "detail": f"OI 骤降 {oi_pct:.1f}%，资金离场",
            })
        return signals

    def _check_symbol(self, label: str, state: MarketState) -> list:
        signals = []
        rsi = state.rsi or state.estimated_rsi
        if rsi is None:
            return signals

        p = CONFIG
        if label == "BTC":
            oversold, overbought = p["btc_rsi_oversold"], p["btc_rsi_overbought"]
            approach_low, approach_high = p["btc_rsi_approach_oversold"], p["btc_rsi_approach_overbought"]
        else:
            oversold, overbought = p["eth_rsi_oversold"], p["eth_rsi_overbought"]
            approach_low, approach_high = p["eth_rsi_approach_oversold"], p["eth_rsi_approach_overbought"]

        if rsi < oversold:
            sig = {"type": "rsi_oversold", "symbol": label, "severity": "high",
                   "value": rsi, "detail": f"RSI {rsi} < {oversold}"}
            if state.rsi_direction == "下行":
                sig["severity"] = "critical"
            signals.append(sig)
        elif rsi > overbought:
            sig = {"type": "rsi_overbought", "symbol": label, "severity": "high",
                   "value": rsi, "detail": f"RSI {rsi} > {overbought}"}
            if state.rsi_direction == "上行":
                sig["severity"] = "critical"
            signals.append(sig)
        elif approach_low <= rsi < oversold:
            signals.append({
                "type": "rsi_approaching_oversold", "symbol": label,
                "severity": "low" if state.rsi_direction == "横盘" else "medium",
                "value": rsi, "detail": f"RSI {rsi} 接近超卖",
            })
        elif overbought < rsi <= approach_high:
            signals.append({
                "type": "rsi_approaching_overbought", "symbol": label,
                "severity": "low" if state.rsi_direction == "横盘" else "medium",
                "value": rsi, "detail": f"RSI {rsi} 接近超买",
            })

        if state.rsi_direction != "横盘" and len(state.rsi_history) >= p["trend_min_consecutive"]:
            has_rsi_sig = any(s["type"].startswith("rsi_") for s in signals)
            if not has_rsi_sig:
                signals.append({
                    "type": "trend_warning", "symbol": label, "severity": "medium",
                    "value": rsi,
                    "detail": f"RSI 连续{p['trend_min_consecutive']}次{state.rsi_direction}",
                })

        chg = abs(state.change24h) if state.change24h else 0
        threshold = p["btc_change_threshold"] if label == "BTC" else p["eth_change_threshold"]
        if chg > threshold:
            direction = "涨" if state.change24h > 0 else "跌"
            signals.append({
                "type": "big_move", "symbol": label, "severity": "medium",
                "value": state.change24h,
                "detail": f"24h {direction}{abs(state.change24h)*100:.2f}%",
            })

        fr = state.funding_rate
        if fr is not None:
            if label == "BTC":
                if fr > p["btc_funding_high"]:
                    signals.append({
                        "type": "funding_high", "symbol": label, "severity": "high",
                        "value": fr, "detail": f"资金费率 {fr*100:.4f}%，偏高",
                    })
                elif fr < p["btc_funding_low"]:
                    signals.append({
                        "type": "funding_negative", "symbol": label, "severity": "medium",
                        "value": fr, "detail": f"资金费率 {fr*100:.4f}%，转负",
                    })
            else:
                if fr < p["eth_funding_low"]:
                    signals.append({
                        "type": "funding_negative", "symbol": label, "severity": "medium",
                        "value": fr, "detail": f"资金费率 {fr*100:.4f}%，转负",
                    })

        return signals

    def build_context(self) -> dict:
        return {
            "btc": self.btc.summary(),
            "eth": self.eth.summary(),
            "ts": int(time.time() * 1000),
        }

    def build_history_context(self) -> dict:
        """构建 LLM 可读的历史上下文"""
        btc_symbol = "BTCUSDT"
        eth_symbol = "ETHUSDT"
        return {
            "btc": {
                "rsi_24h_trend": self.history.get_24h_rsi(btc_symbol)[-10:],
                "signals_today": self.history.get_today_signals(btc_symbol),
            },
            "eth": {
                "rsi_24h_trend": self.history.get_24h_rsi(eth_symbol)[-10:],
                "signals_today": self.history.get_today_signals(eth_symbol),
            },
        }


# ── WS 管理器 ─────────────────────────────────────
class WSManager:
    def __init__(self):
        self.btc = MarketState("BTCUSDT")
        self.eth = MarketState("ETHUSDT")
        self.history = HistoryStore(CONFIG["history_file"])
        self.detector = SignalDetector(self.btc, self.eth, self.history)
        self._ws = None
        self._running = True
        self._reconnect_delay = 1
        self._last_heartbeat_ts = time.time()

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        # 启动 OI 轮询线程（每5分钟）
        self._oi_timer = threading.Thread(target=self._oi_poll_loop, daemon=True)
        self._oi_timer.start()

        # 启动持久化定时器（每 30s 写一次 RSI 快照）
        self._persist_timer = threading.Thread(target=self._persist_loop, daemon=True)
        self._persist_timer.start()

    def _handle_signal(self, signum, frame):
        log.info(f"Received signal {signum}, persisting history before exit...")
        self._running = False
        self._persist_now()
        self._write_health(status="shutdown")
        if self._ws:
            self._ws.close()
        sys.exit(0)

    def _persist_loop(self):
        """每 30s 持久化 RSI 历史"""
        while self._running:
            time.sleep(30)
            self._persist_now()

    def _persist_now(self):
        """持久化当前 RSI 到历史文件"""
        for state in [self.btc, self.eth]:
            rsi = state.rsi or state.estimated_rsi
            if rsi is not None and state.last_price:
                self.history.append_rsi(state.symbol, rsi, state.last_price)
        self.history.save()

    def start(self):
        self._init_historical()
        self._ws_loop()

    def _init_historical(self):
        log.info("Fetching historical candles via REST...")
        for symbol in CONFIG["symbols"]:
            candles = fetch_historical_candles(symbol)
            state = self.btc if symbol == "BTCUSDT" else self.eth
            state.completed_candles = candles
            state._recalc_rsi()
            # 初始化历史文件（REST 已有数据）
            for c in candles:
                self.history.append_rsi(symbol, c["close"], c["close"])
            log.info(f"{symbol} initial RSI: {state.rsi} (est: {state.estimated_rsi})")
        self.history.save()

    def _oi_poll_loop(self):
        """每 5 分钟轮询一次 OI"""
        while self._running:
            try:
                for symbol in CONFIG["symbols"]:
                    oi = fetch_oi(symbol)
                    if oi is not None:
                        state = self.btc if symbol == "BTCUSDT" else self.eth
                        old_oi = state.open_interest
                        state.update_oi(oi)
                        if old_oi is not None and old_oi != oi:
                            chg = (oi - old_oi) / old_oi * 100
                            log.info(f"OI {symbol}: {old_oi:.0f} → {oi:.0f} ({chg:+.2f}%)")
            except Exception as e:
                log.warning(f"OI poll error: {e}")
            # 等待 sleep 前检查退出
            for _ in range(CONFIG["oi_poll_interval"]):
                if not self._running:
                    return
                time.sleep(1)

    def _ws_loop(self):
        while self._running:
            try:
                self._connect()
            except Exception as e:
                log.error(f"WS connection error: {e}")
            if not self._running:
                break
            delay = min(self._reconnect_delay, 60)
            log.info(f"Reconnecting in {delay}s...")
            time.sleep(delay)
            self._reconnect_delay = min(delay * 2, 60)

    def _connect(self):
        import websocket
        ws = websocket.WebSocketApp(
            CONFIG["ws_url"],
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws = ws
        log.info(f"Connecting to {CONFIG['ws_url']}...")
        ws.run_forever(
            sslopt={"cert_reqs": 0},
            ping_interval=CONFIG["ws_ping_interval"],
            ping_timeout=CONFIG["ws_ping_timeout"],
            reconnect=0,
        )

    def _subscribe(self, ws):
        args = []
        for symbol in CONFIG["symbols"]:
            args.append({"instType": "USDT-FUTURES", "channel": "ticker", "instId": symbol})
            args.append({"instType": "USDT-FUTURES", "channel": "candle4H", "instId": symbol})
        ws.send(json.dumps({"op": "subscribe", "args": args}))
        log.info(f"Subscribed to {len(args)} channels")

    def _on_open(self, ws):
        log.info("WS connected")
        self._reconnect_delay = 1
        self._subscribe(ws)

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        if data.get("event") == "subscribe":
            return
        if data.get("event") == "error":
            log.error(f"WS error: {data}")
            return

        arg = data.get("arg", {})
        channel = arg.get("channel", "")
        inst_id = arg.get("instId", "")
        raw_data = data.get("data", [])
        if not raw_data:
            return

        state = self.btc if inst_id == "BTCUSDT" else (self.eth if inst_id == "ETHUSDT" else None)
        if not state:
            return

        if channel == "ticker":
            state.update_ticker(raw_data[0])
        elif channel == "candle4H":
            for candle in raw_data:
                state.update_candle(candle)

        self._check_signals()
        self._heartbeat()

    def _on_error(self, ws, error):
        if self._running:
            log.warning(f"WS error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        if self._running:
            log.info(f"WS closed: {close_status_code} - {close_msg}")

    def _check_signals(self):
        if self.btc.last_price is None:
            return

        raw_signals = self.detector.detect()
        new_signals = []
        for sig in raw_signals:
            label = sig["symbol"]
            state = self.btc if label == "BTC" else self.eth
            stype = sig["type"]
            if not state.is_on_cooldown(stype):
                state.mark_signal(stype)
                new_signals.append(sig)
                # 记入历史
                self.history.append_signal(
                    "BTCUSDT" if label == "BTC" else "ETHUSDT", sig
                )

        if not new_signals:
            return

        # 构建带历史上下文的信号输出
        context = self.detector.build_context()
        hist_context = self.detector.build_history_context()

        # 为每个信号附加上次同类型信号记录
        for sig in new_signals:
            sym = "BTCUSDT" if sig["symbol"] == "BTC" else "ETHUSDT"
            last = self.history.get_similar_signal(sym, sig["type"])
            if last:
                sig["last_similar"] = last

        output = {
            "id": f"sig_{int(time.time())}_{os.getpid()}",
            "ts": int(time.time() * 1000),
            "signals": new_signals,
            "context": context,
            "history_context": hist_context,
        }

        atomic_write(CONFIG["signal_file"], output)
        log.info(f"Signal generated: {[s['type']+'('+s['symbol']+')' for s in new_signals]} "
                 f"(with history context)")

        # 走 Hermes CLI 实时推送给用户
        self._dispatch_via_hermes(new_signals, context, hist_context)

        self._persist_now()
        self._write_health(status="active", signals=len(new_signals))

    def _auto_trade(self, signals: list, context: dict) -> dict | None:
        """规则驱动自动交易——信号匹配就执行，不依赖 LLM"""
        import subprocess, json, os
        # 查交易所真实持仓（不依赖 trade-log.json）
        positions = self._fetch_positions()
        size_map = {"BTC": "0.001", "ETH": "0.01"}

        # ── Phase 0: 持仓管理 — 止盈 / RSI回归 / 超时平仓 ──
        entry_prices = {}
        try:
            log_path = os.path.join(os.path.dirname(CONFIG["signal_file"]), "trade-log.json")
            if os.path.exists(log_path):
                with open(log_path) as f:
                    for e in json.load(f):
                        sym_e = e.get("symbol", "")
                        act = e.get("action", "")
                        if e.get("success") and ("open_long" in act or "open_short" in act):
                            entry_prices[sym_e] = {"action": act, "price": float(e.get("price", 0) or 0), "ts": e.get("ts", "")}
        except Exception:
            pass

        close_side = {"open_long": "close_long", "open_short": "close_short"}
        cur_ts = time.time()
        for sym, side in list(positions.items()):
            ep = entry_prices.get(sym)
            if not ep or ep["price"] == 0:
                continue
            cur_price = context.get(sym.lower(), {}).get("price")
            if not cur_price:
                continue
            entry_p = ep["price"]
            pnl_pct = (cur_price - entry_p) / entry_p if "open_short" in ep["action"] else (entry_p - cur_price) / entry_p
            rsi = context.get(sym.lower(), {}).get("rsi", 50)
            # 计算持仓时长
            hold_hours = 0
            try:
                hold_hours = (cur_ts - datetime.strptime(ep["ts"][:19], "%Y-%m-%dT%H:%M:%S").timestamp()) / 3600
            except Exception:
                pass
            close_reason = None
            # a) 止盈
            if pnl_pct >= 0.01:
                close_reason = f"止盈 {pnl_pct*100:.1f}%"
            # b) 止损
            elif pnl_pct <= -0.03:
                close_reason = f"止损 {pnl_pct*100:.1f}%"
            # c) RSI 回归中性
            elif side == "long" and rsi and rsi >= 55:
                close_reason = f"RSI {rsi} 回归中性"
            elif side == "short" and rsi and rsi <= 45:
                close_reason = f"RSI {rsi} 回归中性"
            # d) 持仓超时
            elif hold_hours >= 12:
                close_reason = f"持仓超时 {hold_hours:.0f}h"
            if close_reason:
                act_close = close_side.get(ep["action"], "")
                if act_close:
                    cmd = {"action": act_close, "symbol": sym, "size": size_map.get(sym, "0.001"), "reason": close_reason}
                    exec_script = os.path.join(os.path.dirname(__file__), "demo_execute.py")
                    log.info(f"Auto close: {json.dumps(cmd, ensure_ascii=False)}")
                    try:
                        exec_result = subprocess.run(
                            [sys.executable or "python3", exec_script, json.dumps(cmd)],
                            capture_output=True, text=True, timeout=20,
                        )
                        if exec_result.returncode == 0:
                            result = json.loads(exec_result.stdout)
                            log.info(f"Auto close OK: {result.get('price','')} | PnL {pnl_pct*100:.1f}%")
                            return result
                        else:
                            log.warning(f"Auto close failed: {exec_result.stdout[:200]}")
                    except Exception as e:
                        log.warning(f"Auto close exception: {e}")

        # ── Phase 1: 信号驱动开/平 ──
        for s in signals:
            sym = s["symbol"]
            sz = size_map.get(sym, "0.001")
            signal_type = s.get("type", "")
            detail = s.get("detail", "")
            c = context.get(sym.lower(), {})
            rsi = c.get("rsi", 50)
            change24h = c.get("change24h", 0)
            oversold, overbought = (35, 65) if sym == "BTC" else (30, 70)
            action, reason = None, ""
            if rsi is not None and rsi <= oversold and positions.get(sym) != "long":
                action, reason = "open_long", f"RSI {rsi} 超卖区"
            elif rsi is not None and rsi >= overbought and positions.get(sym) != "short":
                action, reason = "open_short", f"RSI {rsi} 超买区"
            elif "trend" in signal_type:
                if "上行" in detail and rsi < 50 and positions.get(sym) != "long":
                    action, reason = "open_long", f"RSI趋势上行 {detail}"
                elif "下行" in detail and rsi > 50 and positions.get(sym) != "short":
                    action, reason = "open_short", f"RSI趋势下行 {detail}"
            elif "big_move" in signal_type or "anomaly" in signal_type:
                if change24h and change24h < -0.05 and positions.get(sym) != "long":
                    action, reason = "open_long", f"24h跌幅 {change24h*100:.1f}% 均值回归"
                elif change24h and change24h > 0.05 and positions.get(sym) != "short":
                    action, reason = "open_short", f"24h涨幅 {change24h*100:.1f}% 均值回归"
            # 5. 平仓：持仓方向与信号相反 → 平仓
            if action is None and positions.get(sym):
                if positions[sym] == "long" and "下行" in detail:
                    action, reason = "close_long", f"趋势转空平多 {detail}"
                elif positions[sym] == "short" and "上行" in detail:
                    action, reason = "close_short", f"趋势转多平空 {detail}"
            # 6. 兜底：有信号且没持仓 → 按方向开单
            if action is None and not positions.get(sym):
                if "下行" in detail:
                    action, reason = "open_short", f"信号触发开空 {detail}"
                elif "上行" in detail:
                    action, reason = "open_long", f"信号触发开多 {detail}"
                elif "超买" in detail or "overbought" in signal_type:
                    action, reason = "open_short", f"超买信号 {detail}"
                elif "超卖" in detail or "oversold" in signal_type:
                    action, reason = "open_long", f"超卖信号 {detail}"
            if action is None:
                continue
            cmd = {"action": action, "symbol": sym, "size": sz, "reason": reason}
            exec_script = os.path.join(os.path.dirname(__file__), "demo_execute.py")
            log.info(f"Auto trade: {json.dumps(cmd, ensure_ascii=False)}")
            try:
                exec_result = subprocess.run(
                    [sys.executable or "python3", exec_script, json.dumps(cmd)],
                    capture_output=True, text=True, timeout=20,
                )
                if exec_result.returncode == 0:
                    try:
                        result = json.loads(exec_result.stdout)
                        log.info(f"Auto trade OK: {result.get('order_id','')} @ {result.get('price','')}")
                        return result
                    except (json.JSONDecodeError, TypeError):
                        log.warning(f"Auto trade stdout parse failed: {exec_result.stdout[:200]}")
                        return {"action": action, "symbol": sym, "size": sz, "reason": reason,
                                "success": False, "error": "stdout parse failed"}
                else:
                    err_body = exec_result.stdout.strip() or exec_result.stderr.strip() or "unknown error (empty output)"
                    log.warning(f"Auto trade failed: {err_body[:200]}")
                    return {"action": action, "symbol": sym, "size": sz, "reason": reason,
                            "success": False, "error": err_body[:200]}
            except Exception as e:
                log.warning(f"Auto trade exception: {e}")
                return None
        return None

    def _fetch_positions(self) -> dict:
        """查 Demo API 真实持仓，返回 {symbol: 'long'|'short'|None}"""
        import hmac, hashlib, base64, json, os, time, urllib.request
        api_key = os.environ.get('BITGET_PAPER_API_KEY', '')
        secret = os.environ.get('BITGET_PAPER_SECRET_KEY', '')
        phrase = os.environ.get('BITGET_PAPER_PASSPHRASE', '')
        if not all([api_key, secret, phrase]):
            try:
                env_path = os.path.abspath(os.path.join(os.path.dirname(CONFIG["signal_file"]), ".env"))
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#') or '=' not in line: continue
                        k, v = line.split('=', 1)
                        if k.strip() == 'BITGET_PAPER_API_KEY' and not api_key:
                            api_key = v.strip()
                        elif k.strip() == 'BITGET_PAPER_SECRET_KEY' and not secret:
                            secret = v.strip()
                        elif k.strip() == 'BITGET_PAPER_PASSPHRASE' and not phrase:
                            phrase = v.strip()
            except Exception:
                pass
        if not all([api_key, secret, phrase]):
            return {}
        def _sign(method, path, qs, body, ts):
            msg = ts + method + path + ('?' + qs if qs else '') + (json.dumps(body) if body else '')
            mac = hmac.new(secret.encode(), msg.encode(), hashlib.sha256)
            return base64.b64encode(mac.digest()).decode()
        ts = str(int(time.time() * 1000))
        path = '/api/v2/mix/position/allPosition'
        qs = 'productType=USDT-FUTURES'
        sig = _sign('GET', path, qs, None, ts)
        try:
            req = urllib.request.Request(
                f'https://api.bitget.com{path}?{qs}',
                headers={'ACCESS-KEY': api_key, 'ACCESS-SIGN': sig,
                         'ACCESS-TIMESTAMP': ts, 'ACCESS-PASSPHRASE': phrase,
                         'Content-Type': 'application/json', 'paptrading': '1'},
                method='GET',
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            if data.get('code') == '00000':
                positions = {}
                for pos in data.get('data', []):
                    sym = pos.get('symbol', '')
                    hold = pos.get('holdSide', '')
                    total = float(pos.get('total', '0'))
                    if total > 0:
                        key = 'BTC' if 'BTC' in sym else 'ETH' if 'ETH' in sym else sym
                        positions[key] = hold
                return positions
            return {}
        except Exception:
            return {}

    def _dispatch_via_hermes(self, signals: list, context: dict, hist_context: dict):
        """检测到信号后，直接调 hermes CLI 让 Agent 分析并推送给用户"""
        import subprocess
        import threading

        # 构建信号摘要
        sig_lines = []
        for s in signals[:3]:  # 最多3个
            label = s["symbol"]
            price = context.get(label.lower(), {}).get("price", "?")
            sig_lines.append(f"{label} {s['type']}: {s['detail']} (价格 ${price})")

        prompt_text = (
            f"⚠️ 检测到市场信号。请分析并用 send_message 工具推送到 Telegram：\n"
            + "\n".join(sig_lines)
            + f"\n\n当前 BTC ${context.get('btc', {}).get('price', '?')} "
            f"RSI {context.get('btc', {}).get('rsi', '?')} "
            f"方向 {context.get('btc', {}).get('direction', '?')}"
            f" OI {context.get('btc', {}).get('open_interest', '?')}"
            f"\nETH ${context.get('eth', {}).get('price', '?')} "
            f"RSI {context.get('eth', {}).get('rsi', '?')} "
            f"方向 {context.get('eth', {}).get('direction', '?')}"
            f" OI {context.get('eth', {}).get('open_interest', '?')}"
        )

        def _run():
            try:
                hermes_bin = os.path.join(
                    os.path.dirname(__file__), "..", "..", "..",
                    ".hermes", "hermes-agent", "venv", "bin", "hermes"
                )
                hermes_bin = os.path.abspath(hermes_bin)
                if not os.path.exists(hermes_bin):
                    hermes_bin = "hermes"  # fallback to PATH

                # 1. Agent 分析，获取话术
                result = subprocess.run(
                    [hermes_bin, "-z", prompt_text],
                    capture_output=True, text=True, timeout=90,
                    env={**os.environ, "HERMES_PROFILE": "jiaoyiyuan"},
                )
                if result.returncode != 0:
                    log.warning(f"Hermes analysis failed: {result.stderr[:300]}")
                    return

                analysis = result.stdout.strip()
                if not analysis:
                    log.warning("Hermes returned empty analysis")
                    return

                log.info(f"Hermes analysis: {analysis[:200]}")

                # 2. Evaluator — 检查分析质量再决定是否推送
                eval_prompt = (
                    f"你是交易媛的评估员（Evaluator），请严格检查以下分析内容是否达标。\n\n"
                    f"## 检查标准 (5项)\n"
                    f"1. 先给判断，再列数据 — 第一句应该是对市场的明确看法\n"
                    f"2. 数据最多3条 — 不堆砌数字\n"
                    f"3. 有风险提示 — 是否说了该说的风险\n"
                    f"4. 语气自信不卑微 — 不讨好、不承诺收益\n"
                    f"5. 不越界 — 没有\"稳赚\"\"保证\"类用语\n\n"
                    f"## 请分析以下内容\n{analysis}\n\n"
                    f"## 输出格式（仅一行 JSON，不要其他文字）\n"
                    f'{{"verdict":"PASS|REVISE|REJECT","reason":"简短理由"}}\n'
                    f"- PASS ✅ 可以直接推送\n"
                    f"- REVISE ⚠️ 有小问题但可以发\n"
                    f"- REJECT ❌ 质量差，不要发送\n"
                )

                eval_result = subprocess.run(
                    [hermes_bin, "-z", eval_prompt],
                    capture_output=True, text=True, timeout=60,
                    env={**os.environ, "HERMES_PROFILE": "jiaoyiyuan"},
                )

                verdict = "PASS"
                eval_reason = ""
                if eval_result.returncode == 0:
                    raw = eval_result.stdout.strip()
                    try:
                        eval_data = json.loads(raw)
                        verdict = eval_data.get("verdict", "PASS")
                        eval_reason = eval_data.get("reason", "")
                    except (json.JSONDecodeError, TypeError):
                        # 如果不是 JSON，检查关键字
                        up = raw.upper()
                        if "REJECT" in up:
                            verdict = "REJECT"
                        elif "REVISE" in up:
                            verdict = "REVISE"
                        eval_reason = raw[:100]
                else:
                    log.warning(f"Evaluator failed, defaulting to PASS: {eval_result.stderr[:200]}")

                log.info(f"Evaluator verdict: {verdict} — {eval_reason[:100]}")

                if verdict == "REJECT":
                    log.info(f"Signal REJECTED by evaluator — not sending to Telegram")
                    return

                # 3. Trade decision — 规则驱动自动交易（替代 LLM 决策）
                trade_result = self._auto_trade(signals, context)

                # 4. 推送到 Telegram（含交易结果）
                label = f"[{verdict}] " if verdict == "REVISE" else ""
                msg = f"{label}{analysis}"
                if trade_result:
                    if trade_result.get("success"):
                        act_labels = {"open_long": "🔥开多", "open_short": "🔻开空",
                                      "close_long": "✅平多", "close_short": "✅平空"}
                        act_label = act_labels.get(trade_result["action"], trade_result["action"])
                        trade_line = (
                            f"\n\n📊 模拟交易: {act_label} {trade_result['symbol']} "
                            f"{trade_result['size']} @ {trade_result['price']}\n"
                            f"理由: {trade_result.get('reason','')}\n"
                            f"手续费: {trade_result.get('fee','')} USDT"
                        )
                        msg += trade_line
                    else:
                        msg += f"\n\n⚠️ 模拟交易失败: {trade_result.get('error','未知错误')}"

                push_result = subprocess.run(
                    [hermes_bin, "send", "-t", "telegram", msg],
                    capture_output=True, text=True, timeout=30,
                    env={**os.environ, "HERMES_PROFILE": "jiaoyiyuan"},
                )
                if push_result.returncode == 0:
                    log.info(f"Hermes dispatch OK (verdict={verdict}, trade={bool(trade_result)})")
                else:
                    log.warning(f"Hermes send failed: {push_result.stderr[:300]}")
            except subprocess.TimeoutExpired:
                log.warning("Hermes dispatch timed out after 90s")
            except FileNotFoundError:
                log.warning("Hermes CLI not found, skipping dispatch")
            except Exception as e:
                log.warning(f"Hermes dispatch failed: {e}")

        # 非阻塞，不拖慢 WS 消息处理
        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def _heartbeat(self):
        now = time.time()
        if now - self._last_heartbeat_ts > CONFIG["heartbeat_interval"]:
            self._last_heartbeat_ts = now
            self._persist_now()
            self._write_health(status="running")
            btc_s = self.btc.summary()
            eth_s = self.eth.summary()
            log.info(f"HEARTBEAT | "
                     f"BTC ${btc_s['price']} RSI {btc_s['rsi'] or '-'} dir={btc_s['direction']} | "
                     f"ETH ${eth_s['price']} RSI {eth_s['rsi'] or '-'} dir={eth_s['direction']}")

    def _write_health(self, status: str = "running", signals: int = 0):
        btc_s = self.btc.summary()
        eth_s = self.eth.summary()
        data = {
            "pid": os.getpid(),
            "status": status,
            "ts": int(time.time()),
            "datetime": datetime.now(timezone.utc).isoformat(),
            "btc": btc_s,
            "eth": eth_s,
            "signals_generated": signals,
            "history_size": {
                "btc_rsi": len(self.history.data["btc"]["rsi_history"]),
                "btc_signals": len(self.history.data["btc"]["signals_log"]),
                "eth_rsi": len(self.history.data["eth"]["rsi_history"]),
                "eth_signals": len(self.history.data["eth"]["signals_log"]),
            },
        }
        atomic_write(CONFIG["health_file"], data)


# ── 入口 ──────────────────────────────────────────
if __name__ == "__main__":
    log.info("=" * 50)
    log.info("交易媛 WS Monitor v2 — with persistent history")
    log.info(f"PID: {os.getpid()}")
    log.info("=" * 50)

    manager = WSManager()
    manager.start()
