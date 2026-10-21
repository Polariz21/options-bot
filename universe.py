#!/usr/bin/env python3
"""
universe.py — the research memory and conviction scorer. Zero tokens.

data/universe.json is a cache: one entry per symbol (holdings + discovered peers)
holding whatever evidence has been fetched, with timestamps so nothing is
re-fetched before it could have changed. This script:

  1. Merges new raw evidence Claude wrote into data/raw/ (compact JSON, one file
     per source — shapes in sample_data/raw/) into the cache.
  2. Computes HISTORICAL EVENT STATS from price data + past event dates:
       run-up   = close(T-1) / close(T-5) − 1        (does it drift up into events?)
       reaction = close(T+1) / close(T-1) − 1         (what happens through the event?)
     → median, hit rate, n. Evidence, not opinion.
  3. Scores CONVICTION 0–100 from config weights, lists the evidence lines that
     earned each point, and marks upcoming catalysts.
  4. Prints what needs refreshing so Claude fetches only that.

Usage:
  python3 universe.py                 # merge raw/, recompute, print stale list
  python3 universe.py --add SYM,SYM   # add discovered candidates
"""
import argparse, json, statistics
from datetime import date, datetime, timedelta
from pathlib import Path
import yaml

HERE = Path(__file__).parent


def load(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def days_since(iso):
    return (date.today() - date.fromisoformat(iso[:10])).days if iso else 10**6


# ------------------------------------------------------------ event stats --

def event_stats(closes, event_dates, cfg):
    """closes: [[YYYY-MM-DD, close], ...] oldest→newest. event_dates: [{date, timing}]."""
    if not closes or not event_dates:
        return None
    idx = {d: i for i, (d, _) in enumerate(closes)}
    px = [f(c) for _, c in closes]
    dates = [d for d, _ in closes]
    w = cfg["conviction"]["runup_window_days"]
    runups, reactions = [], []
    for ev in event_dates:
        ed = ev["date"][:10]
        if ed > str(date.today()):
            continue
        # find the trading day on/after the event date
        i = next((k for k, d in enumerate(dates) if d >= ed), None)
        if i is None or i - w - 1 < 0 or i + 1 >= len(px):
            continue
        # pm report: reaction is next day's close vs that day's close; am: that day vs prior
        if ev.get("timing") == "am":
            pre, post = i - 1, i
        else:
            pre, post = i, i + 1
        if pre - w < 0:
            continue
        runups.append(px[pre] / px[pre - w] - 1)
        reactions.append(px[post] / px[pre] - 1)
    n = len(reactions)
    if n < cfg["conviction"]["min_events_for_stats"]:
        return {"n": n, "insufficient": True}
    up = sum(1 for r in reactions if r > 0)
    return {
        "n": n,
        "runup_median_pct": round(statistics.median(runups) * 100, 1),
        "runup_hit_rate": round(sum(1 for r in runups if r > 0) / n, 2),
        "reaction_median_pct": round(statistics.median(reactions) * 100, 1),
        "reaction_abs_median_pct": round(statistics.median(abs(r) for r in reactions) * 100, 1),
        "reaction_up_rate": round(up / n, 2),
        "reaction_direction": "up" if up / n >= 0.65 else "down" if up / n <= 0.35 else "mixed",
    }


# ------------------------------------------------------------- conviction --

def conviction(entry, cfg):
    W = cfg["conviction"]["weights"]
    pts, why = 0, []
    ins = entry.get("insiders") or {}
    if ins.get("distinct_buyers_6m", 0) >= 3:
        pts += W["insider_cluster"]; why.append(f"{ins['distinct_buyers_6m']} distinct insiders bought in 6m (cluster)")
    if ins.get("net_shares_2q", 0) > 0:
        pts += W["insider_net_buy"]; why.append(f"insiders net buyers last 2 quarters (+{ins['net_shares_2q']:,.0f} sh)")
    inst = entry.get("institutions") or {}
    q = inst.get("ownership_pct_by_quarter") or []
    if len(q) >= 3 and q[-1] > q[-2] > q[-3]:
        pts += W["institutional_qoq"]; why.append(f"institutional ownership up 2 straight quarters ({q[-3]:.0f}→{q[-2]:.0f}→{q[-1]:.0f}%)")
    an = entry.get("analysts") or {}
    if an.get("net_upgrades_90d", 0) > 0:
        pts += W["analyst_upgrades"]; why.append(f"net {an['net_upgrades_90d']} analyst upgrades in 90d")
    es = entry.get("event_stats") or {}
    if es and not es.get("insufficient"):
        if es["runup_median_pct"] > 0 and es["runup_hit_rate"] >= 0.6:
            pts += W["event_runup"]; why.append(f"drifts +{es['runup_median_pct']}% into events ({es['runup_hit_rate']:.0%} of {es['n']})")
        if es["reaction_direction"] != "mixed":
            pts += W["event_reaction"]; why.append(f"post-event move {es['reaction_direction']} {es['reaction_up_rate']:.0%} of {es['n']} (median {es['reaction_median_pct']:+}%)")
    # upcoming catalysts
    upcoming = [e for e in (entry.get("events") or [])
                if e.get("type") in cfg["conviction"]["catalysts_count"] and 0 <= days_until(e["date"]) <= 60]
    upcoming.sort(key=lambda e: e["date"])
    return min(100, pts), why, upcoming


def days_until(iso):
    return (date.fromisoformat(iso[:10]) - date.today()).days


# ------------------------------------------------------------------ merge --

def merge_raw(uni, raw_dir, holdings):
    """Each raw file is {symbol: payload}. Shapes documented in sample_data/raw/."""
    stamp = str(date.today())
    for key, fname in (("insiders", "insiders.json"), ("institutions", "institutions.json"),
                       ("analysts", "analysts.json"), ("events", "events.json"), ("peers", "peers.json")):
        raw = load(raw_dir / fname) or {}
        for sym, payload in raw.items():
            e = uni.setdefault(sym, {"added": stamp, "source": "holding" if sym in holdings else "discovered"})
            if key == "events":   # union by (date,type)
                have = {(x["date"], x["type"]) for x in e.get("events", [])}
                e["events"] = e.get("events", []) + [x for x in payload if (x["date"], x["type"]) not in have]
                e["events_refreshed"] = stamp
            elif key == "peers":  # payload: {"similar_to": "IREN", "why": "..."}
                e.update(payload)
            else:
                e[key] = payload
                e[f"{key}_refreshed"] = stamp
    return uni


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE / "data"))
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--add", default="", help="comma-separated symbols to add as candidates")
    a = ap.parse_args()
    cfg = yaml.safe_load(Path(a.config).read_text())
    d = Path(a.data); raw = d / "raw"
    uni = load(d / "universe.json") or {}
    positions = (load(d / "positions.json") or {}).get("data", {}).get("positions", [])
    holdings = {p["symbol"] for p in positions}
    for s in holdings:
        uni.setdefault(s, {"added": str(date.today()), "source": "holding"})
    for s in [x.strip().upper() for x in a.add.split(",") if x.strip()]:
        uni.setdefault(s, {"added": str(date.today()), "source": "discovered"})
    uni = merge_raw(uni, raw, holdings)

    hist = load(d / "historicals.json") or {}
    dc = cfg["discovery"]
    for sym, e in uni.items():
        e["source"] = "holding" if sym in holdings else e.get("source", "discovered")
        if sym in hist and isinstance(hist[sym], list) and hist[sym] and isinstance(hist[sym][0], list):
            e["event_stats"] = event_stats(hist[sym], e.get("events", []), cfg)
        score, why, upcoming = conviction(e, cfg)
        e["conviction"], e["evidence"], e["upcoming_catalysts"] = score, why, upcoming
        e["scored"] = str(date.today())

    # prune stale discovered names with no signal
    for sym in list(uni):
        e = uni[sym]
        if e["source"] == "discovered" and e["conviction"] == 0 and not e.get("upcoming_catalysts") \
                and days_since(e["added"]) > dc["drop_after_days_without_signal"]:
            del uni[sym]
    # cap
    if len(uni) > dc["max_universe"]:
        keep = sorted(uni, key=lambda s: (uni[s]["source"] != "holding", -uni[s]["conviction"]))[: dc["max_universe"]]
        uni = {s: uni[s] for s in keep}

    (d / "universe.json").write_text(json.dumps(uni, indent=1))

    # what's stale → Claude fetches only these
    stale = {"insiders": [], "institutions": [], "analysts": [], "events": []}
    for sym, e in uni.items():
        for k in ("insiders", "institutions", "analysts"):
            if days_since(e.get(f"{k}_refreshed", "")) > dc["refresh_fundamentals_days"]:
                stale[k].append(sym)
        if days_since(e.get("events_refreshed", "")) > dc["refresh_events_days"]:
            stale["events"].append(sym)
    (d / "stale.json").write_text(json.dumps(stale, indent=1))

    ranked = sorted(uni.items(), key=lambda kv: -kv[1]["conviction"])
    print(f"✓ universe: {len(uni)} symbols ({sum(1 for e in uni.values() if e['source']=='holding')} held)")
    for sym, e in ranked[:10]:
        cat = f" · next: {e['upcoming_catalysts'][0]['type']} {e['upcoming_catalysts'][0]['date']}" if e["upcoming_catalysts"] else ""
        print(f"  {e['conviction']:3d} {sym:6} [{e['source'][:4]}]{cat}")
        for w in e["evidence"]:
            print(f"        - {w}")
    print("STALE:", {k: v for k, v in stale.items() if v} or "nothing")


if __name__ == "__main__":
    main()
