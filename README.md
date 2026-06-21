# 交易媛 — Your Private Trading Companion

> *"Not a trading bot. A trading partner."*

交易媛 is an **AI-powered trading agent** built on a 4-layer architecture:

| Layer | Component | Role |
|-------|-----------|------|
| **🧠 LLM** | DeepSeek v4 Flash + SOUL.md persona | Reasoning, analysis, trade decisions |
| **⚙️ Agent** | Hermes Agent | Task orchestration, tool routing, cron management |
| **⏰ Cron** | 2-min signal pipeline + 9AM daily report | Scheduled execution, signal polling |
| **🐍 Scripts** | ws-monitor.py + demo_execute.py | Real-time data, signal detection, API execution |

Built for the Bitget Hackathon. Uses Bitget WebSocket API, Bitget Demo Trading REST API, and MCP Server.

---

## 🧠 Core Idea

**Most trading bots fail because they have rigid strategies.** An RSI strategy works in trends but gets slaughtered in ranges. Quant traders spend months backtesting, only to find their edge vanishes when market regime shifts.

**交易媛's approach: Replace the fixed strategy engine with an LLM trader.**

The LLM reads the same signals a human trader would — RSI, Open Interest, Funding Rate, price action — and makes contextual decisions, with machine speed and discipline.

### Signal Layer

| Signal | Source | Purpose |
|--------|--------|---------|
| RSI-14 (4h) | Bitget WebSocket candlesticks | Overbought/oversold + trend continuity |
| Open Interest | OKX REST API (fallback) | Capital inflow/outflow detection |
| Funding Rate | Bitget WebSocket ticker | Long/short crowdedness |
| Price Action | Real-time trades | Consecutive direction, anomaly alerts |
| Trend Context | 200-point RSI history | "RSI climbed from 54 to 60 over 24h" |

### Decision Flow

```
Raw data → ws-monitor (Python daemon)
         → pending-signal.json (atomic file IPC)
         → 交易媛-信号 cron (every 2 min)
             → Hermes Agent loads SOUL.md + signal data
             → LLM analysis (DeepSeek v4 Flash)
             → Evaluator quality gate (5 criteria)
             → Trade decision (open/close/skip) ← NEW
             → demo_execute.py → Bitget Demo API
         → Telegram notification
```

### Risk Control

- **Demo-only**: All automated trades via Bitget Demo API (`paptrading: 1`). Real money handled manually if confirmed.
- **Position limits**: BTC 0.01 max / ETH 0.1 max per trade
- **Daily cap**: 10 trades/day
- **Audit trail**: Every trade logged in `trade-log.json`
- **Quality gate**: Evaluator checks every analysis before action
- **Persona guard**: SOUL.md defines boundaries — *"my judgment, your decision"*

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 4:  LLM (DeepSeek v4 Flash)                            │
│  • Persona: SOUL.md — 私人交易搭子                             │
│  • Analysis: 信号 → 话术 (先判断再数据)                         │
│  • Decision: 开/平/观望 (JSON output)                         │
└──────────────────────────────────────────────────────────────┘
                            ↕
┌──────────────────────────────────────────────────────────────┐
│ Layer 3:  Hermes Agent                                       │
│  • SOUL.md inject → system prompt for every session          │
│  • MCP bridge → Bitget API tools                             │
│  • Cron management → 2 pipelines running                     │
│  • Telegram send → user-facing output                        │
└──────────────────────────────────────────────────────────────┘
                            ↕
┌──────────────────────────────────────────────────────────────┐
│ Layer 2:  Cron Scheduler                                     │
│  • 交易媛-信号 (every 2 min): 信号检测 → LLM → 执行 → 通知      │
│  • 交易媛-日报 (daily 9AM): 复盘 → 数据拉取 → 报告 → 推送      │
└──────────────────────────────────────────────────────────────┘
                            ↕
┌──────────────────────────────────────────────────────────────┐
│ Layer 1:  Python Scripts + Bitget API                        │
│  • ws-monitor.py: 实时WS监听 + RSI/OI/funding 信号检测         │
│  • demo_execute.py: Demo API签名 + 交易执行 + 风控 + 日志      │
│  • Bitget: WebSocket Public + Demo REST + MCP Server         │
└──────────────────────────────────────────────────────────────┘
```

Full architecture details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## ✅ Current Completion

**Working features:**
- ✅ Real-time WebSocket market monitoring (BTC/ETH)
- ✅ RSI-14 computation with 200-point persistent history
- ✅ Multi-signal detection: RSI, OI, funding, trend, anomaly
- ✅ Signal dedup + rate limiting + cooldown
- ✅ LLM signal analysis pipeline (Hermes + DeepSeek v4 Flash)
- ✅ Evaluator quality gate (5 criteria)
- ✅ Automated demo trade execution with risk controls
- ✅ Trade audit log
- ✅ Telegram push notifications (analysis + trade result)
- ✅ Automatic WebSocket reconnection (exponential backoff)
- ✅ Daily market report (9AM cron)

**Next steps:**
- [ ] Multi-timeframe analysis (1h + 4h + 1d cascade)
- [ ] Dynamic position sizing based on account equity
- [ ] Limit orders with TP/SL
- [ ] Strategy backtest dashboard
- [ ] Web UI for monitoring

**Tech stack:**

| Component | Technology | Bitget Tool Used |
|-----------|-----------|------------------|
| Data pipeline | Python, WebSocket | Bitget WebSocket Public API |
| Execution | REST API | Bitget Demo Trading API (`paptrading: 1`) |
| LLM Agent | Hermes Agent | Bitget MCP Server (`--paper-trading`) |
| LLM | DeepSeek v4 Flash | — |
| Scheduling | Hermes Cron | — |
| Notification | Telegram | — |

---

## 🚀 Quick Start

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/trading-girl.git
cd trading-girl

# 1. Get Bitget Demo API Key
#    Log in → Switch to Demo Mode → API Key Management → Create Demo API Key

# 2. Configure
cp .env.example .env
# Edit: BITGET_PAPER_API_KEY, BITGET_PAPER_SECRET_KEY, BITGET_PAPER_PASSPHRASE

# 3. Start signal monitor (Python daemon)
python src/ws-monitor.py

# 4. The cron pipeline will pick up signals automatically
#    (Requires Hermes Agent — see docs/SETUP.md)
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BITGET_PAPER_API_KEY` | ✅ | Bitget Demo API Key |
| `BITGET_PAPER_SECRET_KEY` | ✅ | Bitget Demo Secret Key |
| `BITGET_PAPER_PASSPHRASE` | ✅ | Bitget Demo API Passphrase |
| `TELEGRAM_BOT_TOKEN` | ❌ | Telegram Bot Token (notifications) |
| `TELEGRAM_CHAT_ID` | ❌ | Telegram Chat ID |

---

## 📁 Project Structure

```
trading-girl/
├── README.md                 ← 项目说明（hackathon 提交用）
├── SOUL.md                   ← 交易媛人设（核心）
├── .env.example              ← 配置模板
├── requirements.txt          ← 依赖（纯标准库）
├── LICENSE                   ← MIT
├── .gitignore
├── src/
│   ├── ws-monitor.py         ← 信号检测引擎（1017行）
│   └── demo_execute.py       ← 交易执行器（234行）
└── docs/
    ├── ARCHITECTURE.md       ← 四层架构详解
    └── PIPELINE.md           ← 管道流程 + 完整 Prompts
```

---

## Bitget Hackathon Submission

### 第一段 · 思路

传统交易机器人有一个根本问题：策略越确定，越容易被市场淘汰。RSI 超买超卖策略在趋势行情里有效，震荡行情里就来回打脸。量化交易者花大量时间调参、回测，本质上是在和市场的非平稳性对抗。

交易媛的解法是：**用 LLM 取代固定策略引擎，让 AI 像人类交易员一样阅读市场信号，但拥有机器的纪律和速度。**

感知的信号：RSI-14（4h，200点历史）、OI 变化率、资金费率、价格行为连续识别。

决策逻辑：Python 信号检测引擎（ws-monitor）通过 Bitget WebSocket 实时计算 → 信号写入 `pending-signal.json` → Hermes Agent 注入 SOUL.md 人设，让 LLM 综合分析 → LLM 输出信号话术 → Evaluator 5项质检 → LLM 决定是否开/平/观望 → 调用 Demo API 执行。

风控：模拟盘优先、单笔仓位上限（BTC 0.01/ETH 0.1）、每日10笔上限、交易日志审计、Evaluator 质检门禁。

### 第二段 · 完成度

开发中遇到的挑战：
- WebSocket 稳定性：Bitget 公开 WS 偶发断开，实现指数退避自动重连 + RSI 历史持久化
- Demo API 签名：需 BASE64 编码 + `paptrading: 1` 头，签名格式严格区分 requestPath 和 queryString
- 仓位模式发现：模拟账户为 hedge_mode（双向持仓），下单需同时指定 `side` + `tradeSide`

当前完成：
- ✅ WebSocket 实时监听 + RSI-14 实时计算 + 200点持久化
- ✅ OI / 资金费率 / 涨跌幅 / 趋势信号检测 + 去重冷却
- ✅ Hermes Agent LLM 分析管道 + Evaluator 质检
- ✅ Demo API 直连 + 模拟盘自动开平仓 + 风控日志
- ✅ Telegram 推送（含交易结果）
- ✅ 日报自动化（9AM）

使用的 Bitget 工具：**WebSocket API**（行情/RSI/OI）、**Demo Trading REST API**（`paptrading: 1`）、**MCP Server**（`--paper-trading`）

AI 框架：**Hermes Agent**（任务编排）、**DeepSeek v4 Flash**（LLM推理）

### 第三段 · 对 AI Trading 的看法

WebSocket 公共频道约 2-3 小时出现一次瞬断，建议加强稳定性。Demo API 的 `paptrading` header 设计合理，但签名算法的 BASE64 要求容易踩坑，建议在示例代码中直接给出完整实现。

对 Agentic Trading 的判断：LLM 在交易中不应替代量化策略，而应成为"策略调度器"。固定规则策略负责执行（快、准、不犹豫），LLM 负责决策（看全局、理解上下文、切换策略）。这种混合架构——信号检测引擎 + LLM 推理层 + 确定性执行层——是当前最务实的形态。交易媛的核心价值在于它不是一个策略，而是一个能独立思考、判断风险、解释决策的交易员。

---

## ⚠️ Disclaimer

This project is for **educational and hackathon purposes only**. All automated trading is executed on a demo/simulated account using Bitget's Demo Trading API. No real cryptocurrency or fiat money is involved. The LLM can make mistakes — never blindly follow AI trading signals with real funds.

---

## 📄 License

MIT
