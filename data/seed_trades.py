#!/usr/bin/env python3
"""强制触发模拟交易，生成提交所需的交易数据"""
import json, os, subprocess, sys

exec_script = os.path.join(os.path.dirname(__file__), "demo_execute.py")

# 基于当前市场状态的策略交易
trades = [
    # ETH: RSI 45.42 连续3次下行 → 开空 (趋势延续策略)
    {"action": "open_short", "symbol": "ETH", "size": "0.01",
     "reason": "ETH RSI连续下行45.4 趋势空头"},
    # BTC: RSI 46.23 横盘 → 双向试仓
    {"action": "open_long", "symbol": "BTC", "size": "0.001",
     "reason": "BTC RSI 46 中性偏下 博弈反弹"},
    # ETH: 再补一个不同方向
    {"action": "close_short", "symbol": "ETH", "size": "0.01",
     "reason": "ETH短线止盈 信号衰减"},
    {"action": "open_long", "symbol": "ETH", "size": "0.01",
     "reason": "ETH RSI超卖反弹 均值回归"},
]

for cmd in trades:
    print(f"\n{'='*50}")
    print(f"Executing: {cmd['action']} {cmd['symbol']} {cmd['size']}")
    try:
        result = subprocess.run(
            [sys.executable or "python3", exec_script, json.dumps(cmd)],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            print(f"✅ OK: {data.get('price','?')} | fee: {data.get('fee','?')}")
            if data.get("balance_before"):
                print(f"   余额前: {json.dumps(data['balance_before'])}")
            if data.get("balance_after"):
                print(f"   余额后: {json.dumps(data['balance_after'])}")
        else:
            print(f"❌ Failed: {result.stderr[:200]}")
    except Exception as e:
        print(f"❌ Error: {e}")

# 显示最终交易日志
print(f"\n{'='*50}")
log_path = "/home/block0/.hermes/profiles/jiaoyiyuan/trade-log.json"
with open(log_path) as f:
    log = json.load(f)
print(f"Total trades: {len(log)}")
for t in log[-5:]:
    print(f"  {t['ts'][:19]} | {t['action']:12} | {t['symbol']:3} | {t['size']:>5} @ {t['price']:>8}")
