#!/usr/bin/env python3
"""
retune_alerts.py — recompute the STOCK price levels that map to an option's
mental stop (−50% of entry) and target (2× entry), given today's mark + delta.

As time decay lowers the option's value, the underlying move needed to reach a
fixed option price changes. This keeps the price alerts honest day to day.

Input: data/retune_inputs.json
  {"symbol":"IREN","avg_price":3.25,"mark":3.75,"delta":0.565,"stock":28.06,
   "stop_mult":0.5,"target_mult":2.0,"approach_pct":2.5}

Output: prints JSON the scheduled task feeds to update_alert / create_alert:
  {"stop_threshold": 25.9, "target_threshold": 32.4, ...}

Approx: ΔS needed = (option_price_target − mark) / delta. Delta drifts, so this
is refreshed daily; good enough to fire a warning before the level, not after.
"""
import json, sys
from pathlib import Path

HERE = Path(__file__).parent


def main():
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "data" / "retune_inputs.json"
    d = json.loads(p.read_text())
    S = float(d["stock"])
    mark = float(d["mark"])
    delta = max(float(d["delta"]), 0.05)          # clamp: tiny delta → absurd distances
    avg = float(d["avg_price"])
    stop_opt = avg * float(d.get("stop_mult", 0.5))
    target_opt = avg * float(d.get("target_mult", 2.0))
    buf = S * float(d.get("approach_pct", 2.5)) / 100

    exact_stop = S - (mark - stop_opt) / delta     # stock price where option ≈ stop_opt
    exact_target = S + (target_opt - mark) / delta # stock price where option ≈ target_opt

    # fire a little BEFORE each level
    stop_threshold = round(max(0.01, exact_stop + buf), 2)
    target_threshold = round(exact_target - buf, 2)

    out = {
        "symbol": d["symbol"],
        "stop_threshold": stop_threshold,       # price_below
        "target_threshold": target_threshold,   # price_above
        "exact_stop_stock": round(exact_stop, 2),
        "exact_target_stock": round(exact_target, 2),
        "stop_option_price": round(stop_opt, 2),
        "target_option_price": round(target_opt, 2),
        "mark": mark, "delta": delta, "stock": S,
        "note": f"{d['symbol']} below {stop_threshold} warns of option ≈${stop_opt:.2f} (−50%); "
                f"above {target_threshold} warns of option ≈${target_opt:.2f} (2×).",
    }
    # sanity: target must be above stop and above current price; stop below
    if not (stop_threshold < S < target_threshold):
        out["warning"] = "current price outside [stop, target] band — level already breached, decide manually"
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
