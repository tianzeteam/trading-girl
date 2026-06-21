# Hermes Agent Setup for 交易媛

交易媛 runs on **Hermes Agent** — an open-source AI agent framework by Nous Research.
This document explains how to configure Hermes to run 交易媛's complete pipeline.

---

## Overview

Hermes provides four capabilities that 交易媛 depends on:

| Capability | Why 交易媛 needs it |
|-----------|-------------------|
| **SOUL.md** | Injected as system prompt → defines persona in every LLM session |
| **MCP Gateway** | Manages Bitget MCP server lifecycle |
| **Cron Scheduler** | Runs 交易媛-信号 (2min) and 交易媛-日报 (9AM) |
| **Telegram Bridge** | Sends notifications to user |
| **Tool Routing** | File I/O, terminal commands for executing scripts |

Without Hermes, 交易媛 is just Python scripts and prompt text. With Hermes, it's a living trading agent.

---

## Step 1: Install Hermes Agent

```bash
# Prerequisites: Python 3.10+, Node.js 18+

# Install Hermes
pip install hermes-agent

# Or via the official installer:
# curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# Verify
hermes --version
```

---

## Step 2: Create Profile

交易媛 runs under a dedicated profile called `jiaoyiyuan`.

```bash
hermes profile create jiaoyiyuan
```

---

## Step 3: Place SOUL.md

The SOUL.md file must be at the **root of the profile's data directory**:

```bash
cp SOUL.md ~/.hermes/profiles/jiaoyiyuan/SOUL.md
```

This file is auto-loaded by Hermes' `load_soul_md()` function and injected as the system prompt for every session — cron jobs, live chat, everything.

---

## Step 4: Configure MCP Servers

Bitget MCP servers provide the bridge to exchange data:

```bash
# Install Bitget MCP server
npm install -g bitget-mcp-server

# Test connection (you need API keys first)
# Create at: https://www.bitget.com → API Key Management
```

### MCP Server Configuration

Add to `~/.hermes/profiles/jiaoyiyuan/config.yaml`:

```yaml
mcp_servers:
  bitget:
    command: npx
    args:
      - bitget-mcp-server
      - --modules
      - spot,futures,account
    env:
      BITGET_API_KEY: "your_live_api_key"
      BITGET_SECRET_KEY: "your_live_secret"
      BITGET_PASSPHRASE: "your_live_passphrase"

  bitget-paper:
    command: npx
    args:
      - bitget-mcp-server
      - --paper-trading
      - --modules
      - spot,futures,account
    env:
      BITGET_PAPER_API_KEY: "your_demo_api_key"
      BITGET_PAPER_SECRET_KEY: "your_demo_secret"
      BITGET_PAPER_PASSPHRASE: "your_demo_passphrase"
```

> **Note**: The `--paper-trading` flag enables demo mode. API credentials must be generated from Bitget's **Demo Mode** (switch to Demo in the web UI first).

---

## Step 5: Create Cron Jobs

### 交易媛-信号 (Signal Pipeline — every 2 minutes)

```bash
hermes cron create \
  --name "交易媛-信号" \
  --schedule "*/2 * * * *" \
  --prompt "$(cat docs/CRON_SIGNAL_PROMPT.md)" \
  --deliver "origin"
```

This cron job:
1. Reads `pending-signal.json` (written by ws-monitor daemon)
2. Analyzes signals via LLM (with SOUL.md persona)
3. Runs evaluator quality check
4. Decides whether to trade
5. If trade: calls `python3 src/demo_execute.py`
6. Pushes to Telegram

### 交易媛-日报 (Daily Report — 9:00 AM)

```bash
hermes cron create \
  --name "交易媛-日报" \
  --schedule "0 9 * * *" \
  --prompt "$(cat docs/CRON_DAILY_PROMPT.md)" \
  --deliver "origin"
```

---

## Step 6: Copy Scripts

```bash
# ws-monitor.py needs to find the scripts directory
cp src/ws-monitor.py ~/.hermes/profiles/jiaoyiyuan/scripts/
cp src/demo_execute.py ~/.hermes/profiles/jiaoyiyuan/scripts/
```

The scripts reference profile-relative paths for data files (`pending-signal.json`, `trade-log.json`, etc.).

---

## Step 7: Configure Environment

Create `~/.hermes/profiles/jiaoyiyuan/.env`:

```bash
# Bitget Live API Keys
BITGET_API_KEY=bg_***
BITGET_SECRET_KEY=***
BITGET_PASSPHRASE=block0ai

# Bitget Demo/Paper API Keys
BITGET_PAPER_API_KEY=bg_***
BITGET_PAPER_SECRET_KEY=***
BITGET_PAPER_PASSPHRASE=jiaoyiyuan

# Optional: Telegram
TELEGRAM_BOT_TOKEN=***
TELEGRAM_CHAT_ID=***
```

---

## Step 8: Start the Gateway

The MCP gateway manages the lifecycle of Bitget MCP servers:

```bash
hermes gateway run --profile jiaoyiyuan
```

---

## Step 9: Start ws-monitor Daemon

```bash
cd ~/.hermes/profiles/jiaoyiyuan/scripts
python3 ws-monitor.py
```

---

## Step 10: Verify Everything

```bash
# Check MCP servers
hermes tools | grep bitget

# Check cron jobs
hermes cron list

# Check ws-monitor health
cat ~/.hermes/profiles/jiaoyiyuan/ws-health.json

# Test trade execution
python3 src/demo_execute.py '{"action":"skip","reason":"test"}'
```

---

## Running Architecture (Production)

In production on block0's machine, the system runs as:

```
Process                    Type              Purpose
─────────────────────────────────────────────────────────────
hermes gateway            Hermes daemon     MCP server lifecycle
bitget-mcp-server         Node.js process   Live Bitget API bridge
bitget-mcp-server paper   Node.js process   Demo Bitget API bridge
ws-monitor.py             Python daemon     Real-time signal detection
hermes cron 信号           Hermes cron       Signal pipeline (2min)
hermes cron 日报           Hermes cron       Daily report (9AM)
```

---

## Troubleshooting

### "sign signature error" when calling Demo API
→ Make sure you're using **BASE64** encoding, not hex.
→ Verify the signing format: `ts + method + path + "?" + qs + body`

### ws-monitor keeps disconnecting
→ Bitget public WebSocket has occasional interruptions. This is normal — the monitor auto-reconnects with exponential backoff. Check `ws-monitor.log`.

### Hermes can't find SOUL.md
→ Make sure it's at `~/.hermes/profiles/jiaoyiyuan/SOUL.md` (profile root, not profile subdirectory).

### MCP server unreachable
→ Run `hermes gateway restart` or check if the gateway process is running.
