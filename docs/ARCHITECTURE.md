# 交易媛 Architecture

交易媛 is built on **4 layers**, each with a distinct role:

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 4:  LLM (DeepSeek v4 Flash)                            │
│  • Persona: SOUL.md defines "who" 交易媛 is                   │
│  • Reasoning: Signal analysis, trade decisions, Evaluator     │
└──────────────────────────────────────────────────────────────┘
                            ↕
┌──────────────────────────────────────────────────────────────┐
│ Layer 3:  Hermes Agent                                       │
│  • Task orchestrator: routes work to LLM, tools, cron        │
│  • Prompt management: SOUL.md injected into every session     │
│  • Tool bridging: MCP clients, Telegram, file I/O            │
└──────────────────────────────────────────────────────────────┘
                            ↕
┌──────────────────────────────────────────────────────────────┐
│ Layer 2:  Cron Scheduler                                     │
│  • 交易媛-信号 (every 2 min): 读取 pending-signal.json → LLM 分析 → 执行 → 通知 │
│  • 交易媛-日报 (daily 9AM): MCP拉数据 → 生成日报 → Telegram推送      │
│  • ws-monitor (daemon): WebSocket 实时监听 → 信号写入           │
└──────────────────────────────────────────────────────────────┘
                            ↕
┌──────────────────────────────────────────────────────────────┐
│ Layer 1:  Python Scripts + Bitget API                        │
│  • ws-monitor.py: 实时市场数据监听 + RSI/OI/funding 信号检测      │
│  • demo_execute.py: Demo API 签名 + 交易执行 + 风控 + 日志      │
│  • Bitget WebSocket API (public candlesticks)                 │
│  • Bitget Demo REST API (paptrading: 1)                      │
│  • Bitget MCP Server (paper-trading, fallback)               │
└──────────────────────────────────────────────────────────────┘
```

## Layer 1: Data & Execution (src/)

**ws-monitor.py** — Python daemon (~1000 lines)
- Connects to Bitget public WebSocket (`wss://ws.bitget.com/v2/ws/public`)
- Subscribes to 4 channels: BTC/USDT 4h candles, ETH/USDT 4h candles, tickers
- Computes RSI-14 in real-time from streaming candlestick data
- Tracks Open Interest via OKX REST API (fallback, since Bitget OI WS is private)
- Detects signals: RSI overbought/oversold, trend continuity (3+ consecutive), OI surges/drops, funding rate extremes
- Dedup + cooldown: same signal type 1h, any signal 30min
- Exponential backoff reconnection (1s → 2s → 4s → ... → 60s)
- Writes to `pending-signal.json` (atomic write, crash-safe)
- Persists RSI history (200 points, survives restarts)
- Health heartbeat every 5 minutes

**demo_execute.py** — Trade execution script (~200 lines)
- Receives JSON command: `{"action":"open_long","symbol":"BTC","size":"0.001","reason":"..."}`
- Signs requests with HMAC-SHA256 BASE64 + `paptrading: 1` header
- Calls Bitget Demo Trading REST API directly
- Risk controls: max position per symbol, daily trade limit (10)
- Trade logging to `trade-log.json` (audit trail)

## Layer 2: Scheduling (Cron)

Managed by Hermes Agent's built-in cron scheduler (not system crontab).

### 交易媛-信号 (every 2 minutes)
**Prompt type**: LLM signal analysis + trade decision + execution

1. Read `pending-signal.json`
2. If empty/consumed → `[SILENT]` (suppress output, save tokens)
3. Parse signal data: type, severity, symbol, context (price, RSI, direction, OI)
4. LLM analyzes signal using 交易媛 persona (SOUL.md)
5. Evaluator checks analysis against 5 quality criteria
6. LLM decides whether to trade (open/close/skip)
7. If trade → calls `demo_execute.py` → executes on Demo API
8. Pushes to Telegram: analysis + trade result

### 交易媛-日报 (daily at 9AM)
**Prompt type**: Market briefing generation

1. Read `trading-state.md` for context
2. Pull fresh data via Bitget MCP (ticker, candles, funding rates)
3. Calculate RSI from candles
4. Generate structured daily report: price, RSI, funding, signal recap, market judgment
5. Append to trading-state.md archive
6. Push to Telegram

## Layer 3: Orchestration (Hermes Agent)

Hermes Agent provides:
- **SOUL.md loading**: Persona file injected into system prompt for every session
- **Tool routing**: MCP servers (bitget, bitget-paper), Telegram send, file operations
- **Cron management**: Create/update/pause/resume cron jobs via `cronjob` tool
- **Gateway**: Manages MCP server lifecycle (auto-restart on failure)
- **Profile isolation**: `jiaoyiyuan` profile keeps config, env, skills separate

## Layer 4: Intelligence (LLM)

**Model**: DeepSeek v4 Flash (via DeepSeek provider)

**Persona (SOUL.md)**:
- Private trading companion — not a tool, not customer service
- Confident, playful, direct — "性感不是露，是自信"
- Principles: judgment first, max 3 data points, always flag risk, never auto-trade live
- Boundaries: no investment advice, no guaranteed returns

**Prompts used**:
1. **Signal analysis** — Reads market signals, generates natural language analysis in 交易媛's voice
2. **Evaluator** — 5-point quality gate on analysis quality before delivery
3. **Trade decision** — Decides whether to execute a trade based on signal + analysis
4. **Daily report** — Generates structured market briefing

## Data Flow

```
                          Bitget WS
                             │ (candles, ticker)
                             ▼
  ┌─────────────────────────────────────┐
  │ ws-monitor.py (daemon)              │
  │ RSI-14, OI, funding → signal detect │
  └─────────────┬───────────────────────┘
                │ pending-signal.json
                ▼
  ┌─────────────────────────────────────┐
  │ Cron: 交易媛-信号 (every 2min)       │
  │ 1. Read signal file                 │
  │ 2. Hermes Agent spawns LLM session  │
  │    → SOUL.md injected as persona    │
  │    → Signal analysis                │
  │    → Evaluator check                │
  │    → Trade decision                 │
  │ 3. If trade → demo_execute.py       │
  │ 4. Push to Telegram                 │
  └─────────────────────────────────────┘
               │           │
               ▼           ▼
        Telegram     Bitget Demo API
        (you see)    (trades execute)
```

## Key Design Decisions

1. **Signal detection in Python, not LLM**: RSI/OI/funding computations are deterministic. LLM should think, not calculate. Python handles the math; LLM handles the interpretation.

2. **Atomic file as IPC**: ws-monitor (daemon) writes to file; cron (scheduled) reads from file. No shared memory, no database, no message queue. Simple, crash-safe, debuggable with `cat`.

3. **LLM for decision, script for execution**: The LLM decides *what* to do (open/close/skip). The Python script handles *how* (signing, API calls, risk checks). Separation of concerns.

4. **Evaluator gate**: Every signal analysis passes a quality check before reaching the user. Prevents the LLM from sending rambling, incoherent, or overconfident messages.

5. **Demo-first execution**: All automated trading runs on simulation. The system can prove itself before touching real funds.
