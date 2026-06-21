# 交易媛 — Your Private Trading Companion

> *"Not a trading bot. A trading partner."*

交易媛 is an **AI-powered trading agent** that reads real-time market signals, makes contextual decisions via LLM reasoning, and executes trades on a demo account — all through natural conversation.

Built for the Bitget Hackathon. Uses Bitget WebSocket API, Bitget Demo Trading REST API, and MCP Server.

---

## 🧠 Core Idea

**Most trading bots fail because they have rigid strategies.** An RSI strategy works in trends but gets slaughtered in ranges. A mean-reversion strategy catches every knife in a breakout. Quant traders spend months backtesting, only to find their edge vanishes when market regime shifts.

**交易媛's approach: Replace the fixed strategy engine with an LLM trader.**

The LLM reads the same signals a human trader would — RSI, Open Interest, Funding Rate, price action — and makes contextual decisions, but with machine speed and discipline.

### Signal Layer (what we sense)

| Signal | Source | Purpose |
|--------|--------|---------|
| RSI-14 (4h) | Bitget WebSocket candlesticks | Overbought/oversold + trend continuity |
| Open Interest | OKX REST API (fallback) | Capital inflow/outflow detection |
| Funding Rate | Bitget WebSocket ticker | Long/short crowdedness |
| Price Action | Real-time trades | Consecutive direction detection, anomaly alerts |
| Trend Context | 200-point RSI history | "RSI climbed from 54 to 60 over 24h" — trend narrative |

### Decision Layer (how we think)

```
Raw market data → Signal Detection Engine (Python daemon)
                → Signal written to pending-signal.json
                → LLM analysis (DeepSeek v4 Flash via Hermes Agent)
                → Evaluator quality gate (5 checks)
                → [NEW] Trade Decision (LLM decides: open/close/skip)
                → [NEW] Demo API Execution (if trade decided)
                → Telegram notification (analysis + trade result)
```

### Risk Control Layer (how we stay safe)

- **Demo-only execution**: All trades go through Bitget Demo Trading API (`paptrading: 1`). No real money.
- **Position limits**: BTC 0.01 max / ETH 0.1 max per trade
- **Daily trade cap**: 10 trades/day
- **Audit log**: Every trade recorded in `trade-log.json`
- **Quality gate**: Every analysis passes a 5-point Evaluator check before any action
- **Persona guard**: Built-in SOUL.md defines boundaries — "my judgment, your decision"

---

## ✅ Current Completion

### Working Features

- [x] Real-time WebSocket market monitoring (BTC / ETH, 4h candles)
- [x] RSI-14 computation with 200-point persistent history (survives restarts)
- [x] Multi-signal detection: RSI thresholds, trend warnings, OI surges, funding extremes
- [x] Signal dedup + rate limiting + cooldown (same-type: 1h, any-type: 30min)
- [x] LLM signal analysis pipeline via Hermes Agent
- [x] Evaluator quality gate (5 criteria)
- [x] Demo Trading API integration (HMAC-SHA256 BASE64 signing)
- [x] Automated demo trade execution with risk controls
- [x] Telegram push notifications (analysis + trade result)
- [x] Automatic WebSocket reconnection (exponential backoff 1s-60s)

### Next Steps

- [ ] Multi-timeframe analysis (1h + 4h + 1d cascade)
- [ ] Dynamic position sizing based on account equity
- [ ] Limit orders with TP/SL (currently market orders only)
- [ ] Strategy backtest dashboard
- [ ] Trading journal with P&L tracking

### Tech Stack

| Component | Technology |
|-----------|-----------|
| Data Pipeline | Python, Bitget WebSocket Public API |
| Execution | Bitget Demo Trading REST API (`paptrading: 1`) |
| LLM Agent | Hermes Agent + DeepSeek v4 Flash |
| Bitget Tools | WebSocket API, Demo REST API, MCP Server (`--paper-trading`) |
| Notification | Telegram Bot |
| Persistence | JSON files (atomic writes, crash-safe) |

---

## 🎯 Positioning

交易媛 is **not a trading bot**. It's a **trading companion** — it has a personality, it explains its reasoning, it tells you when you're being dumb, and it celebrates when you win.

> *"性感不是露，是自信。"*
> *"I can talk markets, flirt a little, and yell at you when you're FOMO-ing. But I never trade without your OK. My judgment, your decision."*

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- Bitget account (register for Demo API Key: [Bitget Demo Trading](https://www.bitget.com/api-doc/common/demotrading/restapi))
- Telegram Bot Token (optional, for push notifications)

### Setup

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/trading-yuan.git
cd trading-yuan

# Install dependencies
pip install -r requirements.txt

# Configure credentials
cp .env.example .env
# Edit .env with your Bitget Demo API Key, Secret, Passphrase

# Start the monitor
python src/ws-monitor.py
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `BITGET_PAPER_API_KEY` | Bitget Demo API Key |
| `BITGET_PAPER_SECRET_KEY` | Bitget Demo Secret Key |
| `BITGET_PAPER_PASSPHRASE` | Bitget Demo API Passphrase |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token (optional) |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID (optional) |

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Bitget WebSocket                         │
│              (public candlesticks + ticker)                  │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│  ws-monitor.py (Python daemon)                               │
│  • RSI-14 computation                                       │
│  • OI tracking (via OKX REST fallback)                      │
│  • Signal detection + dedup + cooldown                      │
│  • Auto-reconnect (exponential backoff)                     │
└─────────────────────────┬───────────────────────────────────┘
                          │ writes to pending-signal.json
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Hermes Agent (LLM pipeline)                                 │
│  1. Signal analysis → generates natural language analysis   │
│  2. Evaluator → 5-point quality check                       │
│  3. Trade decision → open/close/skip (structured JSON)      │
└─────────────────────────┬───────────────────────────────────┘
                          │
              ┌───────────┴───────────┐
              │                       │
              ▼                       ▼
┌─────────────────────┐   ┌─────────────────────────────────┐
│  Telegram Notification│   │  Demo Execution                  │
│  (analysis + trade   │   │  demo_execute.py → Bitget Demo   │
│   result)            │   │  REST API + trade log            │
└─────────────────────┘   └─────────────────────────────────┘
```

---

## ⚠️ Disclaimer

This project is for **educational and hackathon purposes only**. All trading is executed on a demo/simulated account using Bitget's Demo Trading API. No real cryptocurrency or fiat money is involved. The LLM can make mistakes — never blindly follow AI trading signals with real funds.

---

## 📄 License

MIT
