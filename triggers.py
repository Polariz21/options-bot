#!/usr/bin/env python3
"""
triggers.py — the Tier-0 pulse. Decides whether today earns a full scan.

Reads only portfolio.json, positions.json, quotes.json, option_positions.json,
earnings.json (≈3 cheap tool calls). Prints a decision Claude follows literally:

    TIER 0  — nothing moved. Log one line, stop. (~3 tool calls total)
    TIER 1  — scan these symbols: IREN, SPCX. (~4 calls per symbol + 2 batch calls)
    TIER 2  — weekly review: scan all + allocation + grade past ideas.

Also appends a one-line entry to data/pulse.log so there's a record of quiet days.
"""
import argparse, json, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import yaml


def load(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(Path(__file__).parent / "data"))
    ap.add_argument("--config", default=str(Path(__file__).parent / "config.yaml"))
    a = ap.parse_args()
    cfg = yaml.safe_load(Path(a.config).read_text())
    t = cfg["triggers"]
    d = Path(a.data)

    quotes = (load(d / "quotes.json") or {}).get("data", {}).get("results", [])
    positions = (load(d / "positions.json") or {}).get("data", {}).get("positions", [])
    open_opts = load(d / "option_positions.json") or []
    earnings = load(d / "earnings.json") or {}
    held = {p["symbol"] for p in positions}

    reasons, symbols = [], {}

    def hit(sym, why, weight=1):
        reasons.append(f"{sym}: {why}")
        symbols[sym] = symbols.get(sym, 0) + weight

    for r in quotes:
        q = r["quote"]
        sym, px, prev = q["symbol"], f(q.get("last_trade_price")), f(q.get("adjusted_previous_close"))
        if prev and abs(px / prev - 1) * 100 >= t["move_pct"]:
            hit(sym, f"moved {(px / prev - 1) * 100:+.1f}% today", 2)

    for o in open_opts:
        avg, mark = f(o["avg_price"]), f(o["mark"])
        stop, target = avg * (1 - cfg["sizing"]["stop_loss_pct"] / 100), avg * 2
        if mark <= stop:
            hit(o["symbol"], f"OPEN OPTION STOP HIT ({mark} ≤ {stop:.2f})", 5)
        elif mark >= target:
            hit(o["symbol"], f"open option at target ({mark} ≥ {target:.2f})", 5)
        elif (mark - stop) / mark * 100 <= t["stop_proximity_pct"]:
            hit(o["symbol"], f"open option within {t['stop_proximity_pct']}% of stop", 3)
        if (date.fromisoformat(o["expiration"]) - date.today()).days <= 3:
            hit(o["symbol"], "open option expires within 3 days", 4)

    for sym, ed in earnings.items():
        if ed and sym in held:
            de = (date.fromisoformat(ed) - date.today()).days
            if 0 <= de <= t["earnings_within_days"]:
                hit(sym, f"earnings in {de}d", 2)

    weekly = date.today().strftime("%A") == t["weekly_review_day"]

    if weekly:
        tier, scan = 2, sorted(held | set(cfg["watchlist"]["extra"]))
    elif symbols:
        tier = 1
        scan = [s for s, _ in sorted(symbols.items(), key=lambda kv: -kv[1])][: t["max_symbols_per_scan"]]
    else:
        tier, scan = 0, []

    line = f"{date.today()} TIER {tier} scan={','.join(scan) or '-'} | {'; '.join(reasons) or 'quiet'}"
    with (d / "pulse.log").open("a") as fh:
        fh.write(line + "\n")

    print(f"TIER {tier}")
    print(f"SCAN: {', '.join(scan) if scan else 'none'}")
    for r in reasons:
        print(f"  - {r}")
    if tier == 0:
        print("Nothing earned a scan today. Stop here — do not pull option chains or spawn subagents.")


if __name__ == "__main__":
    main()
