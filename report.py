#!/usr/bin/env python3
"""
report.py — analysis.json → Markdown brief + appends proposals to the track-record log.

Usage: python3 report.py [--data data/] [--out reports/]
"""
import argparse, json
from datetime import date
from pathlib import Path


def money(x):
    return f"${x:,.0f}"


def idea_block(e, i):
    z, sc = e["sizing"], e["scenarios_at_expiry"]
    L = [f"#### {i}. {e['symbol']} {e['expiration']} ${e['strike']:g} {e['type']} — {money(e['premium'])}/contract · {e['dte']} DTE", ""]
    if e.get("conviction") or e.get("catalysts_in_window"):
        cat = ", ".join(f"{c['type']} {c['date']}" for c in e.get("catalysts_in_window", []))
        L.append(f"**Conviction {e.get('conviction', 0)}/100**" + (f" · catalyst in window: {cat}" if cat else ""))
        for w in e.get("evidence", []):
            L.append(f"  - {w}")
        L.append("")
    L.append(f"**Size: {z['contracts']} contract(s) = {money(z['contracts'] * e['premium'])} "
             f"({z['pct_of_account']}% of account). Max loss at stop: {money(z['max_loss_at_stop'])}.** "
             f"Binding limit: {z['binding_constraint'].replace('_', ' ')}.")
    L.append("")
    L.append(f"- Needs a **{e['sigma_to_breakeven']}σ** move to breakeven ({e['breakeven']}); "
             f"1σ expected move by expiry is ±${e['expected_move_1sigma']}")
    L.append(f"- Chance of profit {e['chance_of_profit']:.0%} · delta {e['delta']} · IV {e['iv']:.0%}"
             + (f" ({e['iv_over_rv']}× realized)" if e["iv_over_rv"] else ""))
    L.append(f"- Decays {e['theta_pct_day']}%/day → halves in ~{e['days_to_half_from_theta']} days if price sits still")
    L.append(f"- Stop ${e['stop_price']} · target ${e['target_price']} · spread {e['spread_pct']}% · OI {e['oi']:,}")
    L.append(f"- At expiry: −1σ {money(sc['-1σ'])} · flat {money(sc['flat'])} · +1σ **{money(sc['+1σ'])}** · +2σ {money(sc['+2σ'])} (per contract)")
    L.append(f"- {z['edge_note']}")
    if e["flags"]:
        L.append(f"- ⚠ {'; '.join(e['flags'])}")
    L.append("")
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(Path(__file__).parent / "data"))
    ap.add_argument("--out", default=str(Path(__file__).parent / "reports"))
    a = ap.parse_args()
    d = json.loads((Path(a.data) / "analysis.json").read_text())
    acct = d["account"]

    L = [f"# Options Bot brief — {d['as_of'][:16].replace('T', ' ')}", ""]
    L.append(f"**Account {money(acct['total_value'])}** · cash {money(acct['cash'])} · deployable {money(acct['deployable'])} · "
             f"options {money(acct['options_outstanding'])} ({acct['options_pct']}%) · "
             f"risk budget/trade {money(acct['risk_budget_per_trade'])} · max premium {money(acct['max_premium'])}")
    L.append("")

    if d["open_options"]:
        L += ["## Open options", "", "| Contract | Qty | Avg | Mark | P&L | Stop | Target | DTE | Status |", "|---|---|---|---|---|---|---|---|---|"]
        for o in d["open_options"]:
            L.append(f"| {o['symbol']} {o['expiration']} ${o['strike']:g} {o['type']} | {o['qty']} | {o['avg_price']} | {o['mark']} | "
                     f"{o['pnl_pct']:+.0f}% | {o['stop']} | {o['target']} | {o['dte']} | **{o['status']}** |")
        L.append("")

    LANE_TITLES = {"low": "Low risk — further out, time is the cushion",
                   "balanced": "Balanced — 3–6 weeks",
                   "high": "High risk — near-dated, catalyst-driven, smaller size"}
    L += ["## Ideas by risk lane", ""]
    any_idea = False
    for lane, title in LANE_TITLES.items():
        ideas = d.get("ideas_by_lane", {}).get(lane, [])
        L += [f"### {title}", ""]
        if not ideas:
            L += ["_None passes this lane's rules today._", ""]
            continue
        any_idea = True
        for i, e in enumerate(ideas, 1):
            L += idea_block(e, i)
    if not any_idea:
        L.append("_No lane has a qualifying idea. That is a valid answer — no trade is a position._")
        if d["near_miss_sizing"]:
            L.append("")
            L.append("Closest fits (pass every filter except size):")
            for e in d["near_miss_sizing"]:
                L.append(f"- {e['symbol']} {e['expiration']} ${e['strike']:g} {e['type']} — {money(e['premium'])}, "
                         f"allowed {money(e['sizing']['allowed_premium'])} ({e['sizing']['binding_constraint'].replace('_', ' ')})")
        L.append("")

    if d.get("conviction_watchlist"):
        L += ["## Conviction watchlist — where the evidence is", "",
              "| Symbol | Conv | Src | Next catalyst | Evidence |", "|---|---|---|---|---|"]
        for w in d["conviction_watchlist"]:
            nc = f"{w['next_catalyst']['type']} {w['next_catalyst']['date']}" if w["next_catalyst"] else "—"
            L.append(f"| {w['symbol']} | {w['conviction']} | {w['source'][:4]} | {nc} | {'; '.join(w['evidence']) or '—'} |")
        L.append("")

    L += ["## Portfolio", "", "| Symbol | Value | P&L | % acct | RSI | vs 20d | Flags |", "|---|---|---|---|---|---|---|"]
    mc = d["market_context"]
    for p in d["positions"]:
        m = mc.get(p["symbol"], {})
        rsi = f"{m['rsi']:.0f}" if m.get("rsi") else "—"
        sma = f"{m['vs_sma20']:+.1f}%" if m.get("vs_sma20") is not None else "—"
        L.append(f"| {p['symbol']} | {money(p['value'])} | {p['pnl_pct']:+.1f}% | {p['pct_of_account']}% | {rsi} | {sma} | {'; '.join(p['flags']) or '—'} |")
    L.append("")

    if d["rejected"]:
        L += ["## Rejected", ""]
        for e in d["rejected"]:
            L.append(f"- {e['symbol']} {e['expiration']} ${e['strike']:g} {e['type']} ({money(e['premium'])}, CoP {e['chance_of_profit']:.0%}) — {'; '.join(e['rejected_because'])}")
        L.append("")

    s, pr = d["rules"]["sizing"], d["rules"].get("profiles", {})
    lanes = " · ".join(f"{k}: {v['dte'][0]}–{v['dte'][1]}d, CoP ≥{v['min_chance_of_profit']:.0%}, δ ≥{v['min_delta']}" for k, v in pr.items())
    L += ["---", f"_Sizing: risk {s['risk_per_trade_pct']}%/trade · premium ≤{s['max_premium_pct']}% · options ≤{s['options_bucket_pct']}% · "
          f"{s['kelly_fraction']:g}×Kelly ({s.get('kelly_mode','flag')}) · stop {s['stop_loss_pct']}%._", f"_Lanes — {lanes}_",
          "", "_Proposal only. Nothing is placed automatically. Conviction is evidence-weighted, not a prediction. Not financial advice._"]

    out_dir = Path(a.out); out_dir.mkdir(exist_ok=True)
    out = out_dir / f"brief-{date.today()}.md"
    out.write_text("\n".join(L))

    # track record: one line per proposed idea
    log = Path(a.data) / "proposals.jsonl"
    with log.open("a") as fh:
        for e in d["ideas"]:
            fh.write(json.dumps({"date": str(date.today()), "symbol": e["symbol"], "type": e["type"],
                                 "strike": e["strike"], "expiration": e["expiration"],
                                 "instrument_id": e["instrument_id"], "entry_mark": e["mark"],
                                 "stop": e["stop_price"], "target": e["target_price"],
                                 "underlying_at_entry": e["underlying"], "cop": e["chance_of_profit"],
                                 "score": e["score"], "contracts": e["sizing"]["contracts"]}) + "\n")
    print(f"✓ {out}  (+{len(d['ideas'])} logged to proposals.jsonl)")


if __name__ == "__main__":
    main()
