#!/usr/bin/env python3
"""
analyze.py — the zero-token core. Filters, scores, sizes, explains.

Reads compact JSON that Claude's tools write into data/, applies config.yaml,
writes data/analysis.json (a few KB). Claude reasons over THAT, never raw chains.

Inputs (all optional except portfolio/positions; shapes in sample_data/):
  portfolio.json        get_portfolio → {"data": {...}}
  positions.json        get_equity_positions → {"data": {"positions": [...]}}
  quotes.json           get_equity_quotes → {"data": {"results": [...]}}
  options.json          [ {symbol, type, strike, expiration, instrument_id, quote:{...}} ]
  option_positions.json [ {symbol, type, strike, expiration, instrument_id, qty, avg_price, mark} ]
  earnings.json         {symbol: "YYYY-MM-DD" | null}
  historicals.json      {symbol: [close, close, ...]}   (≥21 daily closes, oldest first)
  rsi.json              {symbol: 61.2}

Never places orders. Has no network access. Just math.
"""
import argparse, json, math, statistics, sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sizing import size_trade

try:
    import yaml
except ImportError:
    sys.exit("pip install --break-system-packages pyyaml")


def load(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def dte(exp):
    return (date.fromisoformat(exp) - date.today()).days


def realized_vol(closes, window=20):
    """Annualized realized vol from daily closes."""
    c = [f(x) for x in closes if f(x) > 0][-(window + 1):]
    if len(c) < 10:
        return None
    rets = [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    return statistics.stdev(rets) * math.sqrt(252)


def sma(closes, n=20):
    c = [f(x) for x in closes if f(x) > 0][-n:]
    return sum(c) / len(c) if len(c) >= n else None


def intrinsic(opt_type, S, K):
    return max(0.0, S - K) if opt_type == "call" else max(0.0, K - S)


# ----------------------------------------------------------------- scoring --

def score_option(c, ctx, cfg):
    """Returns dict with metrics, reasons it was rejected (if any), and flags."""
    fl, sz = cfg["filters"], cfg["sizing"]
    q = c["quote"]
    sym, typ, K = c["symbol"], c["type"], f(c["strike"])
    S = ctx["prices"].get(sym, 0)
    mark = f(q.get("mark_price"))
    bid, ask = f(q.get("bid_price")), f(q.get("ask_price"))
    cop = f(q.get("chance_of_profit_long"))
    theta = abs(f(q.get("theta")))
    delta = abs(f(q.get("delta")))
    iv = f(q.get("implied_volatility"))
    oi = int(f(q.get("open_interest")))
    days = dte(c["expiration"])
    premium = mark * 100
    be = f(q.get("break_even_price")) or (K + mark if typ == "call" else K - mark)

    # --- insight metrics -------------------------------------------------
    em = S * iv * math.sqrt(max(days, 1) / 365) if S and iv else 0   # 1σ expected move ($)
    sign = 1 if typ == "call" else -1
    sigma_to_be = ((be - S) * sign / em) if em else 99
    theta_pct = (theta / mark * 100) if mark else 999
    spread_pct = ((ask - bid) / mark * 100) if mark else 999
    days_to_half = (mark * 0.5 / theta) if theta else 99               # theta-only decay to -50%

    # payoff table at expiry for underlying at −1σ, 0, +1σ, +2σ (favorable direction)
    scen = {}
    for label, k in (("-1σ", -1), ("flat", 0), ("+1σ", 1), ("+2σ", 2)):
        S_T = S + sign * k * em
        scen[label] = round((intrinsic(typ, S_T, K) - mark) * 100)
    payoff_1s = max(scen["+1σ"], 0)

    rv = ctx["rv"].get(sym)
    iv_rv = (iv / rv) if (rv and rv > 0) else None
    rsi = ctx["rsi"].get(sym)
    sma20 = ctx["sma20"].get(sym)

    # --- lane + conviction ---------------------------------------------------
    lane = None
    for name, p in cfg["profiles"].items():
        if p["dte"][0] <= days <= p["dte"][1]:
            lane = name; break
    u = ctx["universe"].get(sym, {})
    conv = u.get("conviction", 0)
    cats = [e for e in u.get("upcoming_catalysts", []) if 0 <= (date.fromisoformat(e["date"][:10]) - date.today()).days <= days]
    es = u.get("event_stats") or {}

    # --- filters -----------------------------------------------------------
    rej, flags = [], []
    if lane is None:
        rej.append(f"{days} DTE outside every lane")
        p = cfg["profiles"]["balanced"]
    else:
        p = cfg["profiles"][lane]
        if cop < p["min_chance_of_profit"]:
            rej.append(f"CoP {cop:.0%} < {p['min_chance_of_profit']:.0%} ({lane} lane)")
        if delta < p["min_delta"]:
            rej.append(f"delta {delta:.2f} < {p['min_delta']} ({lane} lane)")
        if theta_pct > p["max_theta_pct_per_day"]:
            rej.append(f"decays {theta_pct:.1f}%/day > {p['max_theta_pct_per_day']}% ({lane} lane)")
        if sigma_to_be > p["max_sigma_to_breakeven"]:
            rej.append(f"breakeven needs {sigma_to_be:.1f}σ > {p['max_sigma_to_breakeven']} ({lane} lane)")
        if p.get("require_catalyst") and not cats and conv < cfg["conviction"]["min_for_high_lane"]:
            rej.append(f"high lane needs a catalyst in-window or conviction ≥{cfg['conviction']['min_for_high_lane']} (has {conv})")
    if spread_pct > fl["max_spread_pct"]:
        rej.append(f"spread {spread_pct:.0f}% of mark")
    if bid == 0:
        rej.append("no bid")
    if oi < fl["min_open_interest"]:
        rej.append(f"OI {oi}")

    # catalyst alignment: does history favor this direction through the event?
    if cats and es and not es.get("insufficient"):
        want = "up" if typ == "call" else "down"
        if es["reaction_direction"] == want:
            flags.append(f"history favors: post-event {want} {es['reaction_up_rate' if want=='up' else 'reaction_up_rate']:.0%} of {es['n']}")
        elif es["reaction_direction"] != "mixed":
            flags.append(f"history AGAINST: post-event moves {es['reaction_direction']} {es['n']}x")
        if typ == "call" and es["runup_median_pct"] > 0 and es["runup_hit_rate"] >= 0.6:
            flags.append(f"run-up play: +{es['runup_median_pct']}% median drift into events — consider exiting T-1")

    if typ == "call" and rsi and rsi > fl["rsi_chase_call"]:
        flags.append(f"RSI {rsi:.0f} — chasing")
    if typ == "put" and rsi and rsi < fl["rsi_chase_put"]:
        flags.append(f"RSI {rsi:.0f} — chasing the dip")
    if iv_rv and iv_rv > fl["iv_rv_expensive"]:
        flags.append(f"IV {iv_rv:.1f}× realized — premium rich")
    if sma20 and S:
        trend = "above" if S > sma20 else "below"
        if (typ == "call" and trend == "below") or (typ == "put" and trend == "above"):
            flags.append(f"fighting the 20d trend (price {trend} SMA)")
    ed = ctx["earnings"].get(sym)
    if ed:
        de = (date.fromisoformat(ed) - date.today()).days
        if 0 <= de <= days:
            flags.append(f"earnings {ed} inside window")

    # --- sizing (lane-scaled) ----------------------------------------------
    lane_cfg = {**cfg, "sizing": {**cfg["sizing"],
                "risk_per_trade_pct": cfg["sizing"]["risk_per_trade_pct"] * p.get("risk_multiplier", 1)}}
    sizing = size_trade(premium, cop, payoff_1s, ctx["account_value"],
                        ctx["options_outstanding"], lane_cfg)
    if not rej and sizing["contracts"] == 0:
        rej.append(f"1 contract (${premium:.0f}) exceeds allowed ${sizing['allowed_premium']} "
                   f"[{sizing['binding_constraint']}]")

    # --- composite score 0–100 (ranking heuristic, not a probability) -----
    s = cop * 35
    s += max(0, 12 - theta_pct * 2.5)
    s += max(0, 8 - spread_pct)
    s += min(8, math.log10(max(oi, 1)) * 2)
    s += 12 * max(0, 1 - abs(delta - 0.45) / 0.45)
    s += max(0, 8 - sigma_to_be * 5)             # closer breakeven scores higher
    s += conv * 0.17                             # conviction 0–100 → up to 17 pts
    s += 5 if cats else 0
    s -= 4 * sum(1 for x in flags if "AGAINST" in x or "chasing" in x or "fighting" in x or "rich" in x)

    return {
        "symbol": sym, "type": typ, "strike": K, "expiration": c["expiration"],
        "instrument_id": c.get("instrument_id"), "score": round(s, 1),
        "lane": lane, "conviction": conv, "evidence": u.get("evidence", []),
        "catalysts_in_window": cats, "event_stats": es if es and not es.get("insufficient") else None,
        "premium": round(premium), "mark": mark, "bid": bid, "ask": ask,
        "underlying": S, "breakeven": be, "dte": days,
        "chance_of_profit": round(cop, 3), "delta": round(delta, 2), "iv": round(iv, 2),
        "theta_pct_day": round(theta_pct, 1), "days_to_half_from_theta": round(days_to_half, 1),
        "spread_pct": round(spread_pct, 1), "oi": oi,
        "expected_move_1sigma": round(em, 2), "sigma_to_breakeven": round(sigma_to_be, 2),
        "scenarios_at_expiry": scen,
        "iv_over_rv": round(iv_rv, 2) if iv_rv else None, "rsi": rsi,
        "stop_price": round(mark * (1 - sz["stop_loss_pct"] / 100), 2),
        "target_price": round(mark * 2, 2),
        "sizing": sizing, "flags": flags, "rejected_because": rej,
    }


# --------------------------------------------------------------- portfolio --

def analyze_positions(positions, prices, total, cfg):
    rl = cfg["relocation"]
    rows = []
    for p in positions:
        sym, qty, avg = p["symbol"], f(p["quantity"]), f(p["average_buy_price"])
        px = prices.get(sym, avg)
        val, pnl = qty * px, ((px - avg) / avg * 100) if avg else 0
        pct = (val / total * 100) if total else 0
        fl = []
        if pct > rl["max_single_position_pct"]: fl.append(f"{pct:.0f}% of account — concentration")
        if val < rl["min_position_usd"]: fl.append(f"dust (${val:.0f})")
        if pnl > rl["trim_after_gain_pct"]: fl.append(f"+{pnl:.0f}% — trim candidate")
        if pnl < rl["review_after_loss_pct"]: fl.append(f"{pnl:.0f}% — thesis check")
        rows.append({"symbol": sym, "qty": qty, "avg": avg, "price": px, "value": round(val),
                     "pnl_pct": round(pnl, 1), "pct_of_account": round(pct, 1), "flags": fl})
    return sorted(rows, key=lambda r: -r["value"])


def analyze_open_options(opts, cfg):
    out = []
    for o in opts or []:
        avg, mark = f(o["avg_price"]), f(o["mark"])
        pnl = (mark - avg) / avg * 100 if avg else 0
        stop, target = avg * (1 - cfg["sizing"]["stop_loss_pct"] / 100), avg * 2
        status = "STOP HIT" if mark <= stop else "TARGET HIT" if mark >= target else "open"
        out.append({**o, "pnl_pct": round(pnl, 1), "stop": round(stop, 2), "target": round(target, 2),
                    "dte": dte(o["expiration"]), "status": status,
                    "pct_to_stop": round((mark - stop) / mark * 100, 1) if mark else 0})
    return out


# -------------------------------------------------------------------- main --

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(Path(__file__).parent / "data"))
    ap.add_argument("--config", default=str(Path(__file__).parent / "config.yaml"))
    a = ap.parse_args()
    cfg = yaml.safe_load(Path(a.config).read_text())
    d = Path(a.data)

    portfolio = (load(d / "portfolio.json") or {}).get("data", {})
    positions = (load(d / "positions.json") or {}).get("data", {}).get("positions", [])
    quotes = (load(d / "quotes.json") or {}).get("data", {}).get("results", [])
    options = load(d / "options.json") or []
    open_opts = load(d / "option_positions.json") or []
    hist = load(d / "historicals.json") or {}

    prices = {r["quote"]["symbol"]: f(r["quote"].get("last_trade_price")) for r in quotes}
    total = f(portfolio.get("total_value"))
    cash = f(portfolio.get("cash"))
    bp = f(portfolio.get("buying_power", {}).get("buying_power"))
    options_out = sum(f(o["avg_price"]) * 100 * f(o["qty"]) for o in open_opts)
    floor = total * cfg["account"]["cash_floor_pct"] / 100
    deployable = max(0, min(bp, cash) - floor)

    # historicals may be [close,...] or [[date, close],...]
    closes = {s: ([x[1] for x in c] if c and isinstance(c[0], list) else c) for s, c in hist.items()}
    universe = load(d / "universe.json") or {}
    ctx = {
        "prices": prices, "account_value": total, "options_outstanding": options_out,
        "earnings": load(d / "earnings.json") or {}, "rsi": load(d / "rsi.json") or {},
        "rv": {s: realized_vol(c) for s, c in closes.items()},
        "sma20": {s: sma(c) for s, c in closes.items()},
        "universe": universe,
    }

    scored = [score_option(c, ctx, cfg) for c in options]
    ideas = sorted([s for s in scored if not s["rejected_because"]], key=lambda e: -e["score"])
    rejected = sorted([s for s in scored if s["rejected_because"]], key=lambda e: -e["score"])
    by_lane = {ln: [e for e in ideas if e["lane"] == ln][: cfg["report"]["top_n_ideas"]] for ln in cfg["profiles"]}
    watch = sorted([{"symbol": s, "conviction": u["conviction"], "source": u["source"], "evidence": u["evidence"],
                     "next_catalyst": (u["upcoming_catalysts"] or [None])[0], "price": prices.get(s)}
                    for s, u in universe.items() if u.get("conviction", 0) >= 30 or u.get("upcoming_catalysts")],
                   key=lambda x: -x["conviction"])[:10]

    # what WOULD fit: cheapest contract that passes everything but sizing
    near_miss = [r for r in rejected if len(r["rejected_because"]) == 1
                 and r["rejected_because"][0].startswith("1 contract")]

    out = {
        "as_of": datetime.now().isoformat(timespec="minutes"),
        "account": {"total_value": total, "cash": cash, "buying_power": bp,
                    "deployable": round(deployable), "options_outstanding": round(options_out),
                    "options_pct": round(options_out / total * 100, 1) if total else 0,
                    "risk_budget_per_trade": round(total * cfg["sizing"]["risk_per_trade_pct"] / 100),
                    "max_premium": round(total * cfg["sizing"]["max_premium_pct"] / 100)},
        "open_options": analyze_open_options(open_opts, cfg),
        "positions": analyze_positions(positions, prices, total, cfg),
        "ideas": ideas[: cfg["report"]["top_n_ideas"]],
        "ideas_by_lane": by_lane,
        "conviction_watchlist": watch,
        "near_miss_sizing": near_miss[:3],
        "rejected": rejected[:12] if cfg["report"]["include_rejected"] else [],
        "market_context": {s: {"rsi": ctx["rsi"].get(s), "rv20": round(ctx["rv"][s], 2) if ctx["rv"].get(s) else None,
                               "vs_sma20": round((prices.get(s, 0) / ctx["sma20"][s] - 1) * 100, 1) if ctx["sma20"].get(s) and prices.get(s) else None}
                           for s in prices},
        "rules": {"sizing": cfg["sizing"], "filters": cfg["filters"], "profiles": cfg["profiles"]},
    }
    (d / "analysis.json").write_text(json.dumps(out, indent=1))
    print(f"✓ {len(ideas)} pass · {len(near_miss)} fit-except-size · {len(rejected)} rejected")
    for e in ideas:
        z = e["sizing"]
        print(f"  {e['score']:5.1f} [{e['lane']:8}] conv {e['conviction']:3d} {e['symbol']} {e['expiration']} ${e['strike']:g} {e['type']} "
              f"${e['premium']} ×{z['contracts']} ({z['pct_of_account']}% acct, max loss ${z['max_loss_at_stop']}) "
              f"CoP {e['chance_of_profit']:.0%} needs {e['sigma_to_breakeven']}σ"
              + (f" ⚠ {'; '.join(e['flags'])}" if e["flags"] else ""))


if __name__ == "__main__":
    main()
