# Options Bot v3 — Playbook

A proposal engine with a research memory and a track record. It never trades.
It finds where the evidence is, prices options in three risk lanes, sizes
them to your account, explains why, and grades itself later.

## What's new in v3

- **Three risk lanes.** Low (45–180 DTE, deep-ish delta), Balanced (21–45),
  High (7–21, needs a catalyst). The brief shows the best idea per lane; you
  pick your risk. High lane gets a smaller size by design.
- **Conviction score (0–100)** from evidence: insider cluster buying, net
  insider buys, institutional ownership rising two quarters, analyst upgrades,
  and the stock's *actual historical behavior* into and through its events.
- **Event history, measured not guessed.** For every past earnings /
  shareholder meeting / investor day on file, `universe.py` computes the
  run-up (T-5 → T-1) and the reaction (T-1 → T+1) from price data: median,
  hit rate, n. A call into an event only gets the "history favors" flag when
  the stock went up through ≥65% of its last events.
- **Research memory.** `data/universe.json` caches evidence with timestamps.
  Insiders/13F/analysts refresh monthly (they're quarterly data), events
  weekly, prices daily. Nothing is fetched twice.
- **Peer discovery.** Weekly, the Researcher finds comparable names to what
  you hold; they enter the universe and get priced daily if they score.

## Honesty rules the bot encodes

- The *average* earnings move is priced into IV. Edge, if any, is in the
  run-up drift and in lopsided history — so those are what get flagged.
- Insider *cluster* buying (≥3 people) is the strongest of these signals;
  single insider buys and 13F changes (45+ days stale) are confirmation only.
- "Will blow up soon" is not a thing the bot says. It says "conviction 70 —
  here are the six lines of evidence" and lets you decide.
- Kelly is reported on every idea. Most long options show "no edge —
  controlled bet." That's not a bug.

## Files

| File | Role |
|---|---|
| `config.yaml` | lanes, sizing, conviction weights, discovery budget — all numbers |
| `triggers.py` | Tier-0 pulse → TIER 0/1/2 + symbols to scan |
| `universe.py` | merges `data/raw/*.json` evidence, computes event stats + conviction, prints what's stale |
| `analyze.py` + `sizing.py` | lane assignment, filters, insight metrics, account-relative sizing, scoring |
| `report.py` | brief by lane + conviction watchlist; logs ideas to `proposals.jsonl` |
| `grade.py` | weekly scorecard of past ideas |
| `sample_data/` + `sample_data/raw/` | exact JSON shapes for every input |

## Evidence sources → `data/raw/` shapes

Claude writes **compact** JSON — only these fields — never raw tool output.

| Raw file | Shape | Source tool | Refresh |
|---|---|---|---|
| `events.json` | `{SYM: [{date, type, timing?}]}` | `get_earnings_results` (dates + am/pm); Bigdata.com `bigdata_events_calendar` or `bigdata_search` for shareholder meetings, investor days, product events, lockups | weekly |
| `insiders.json` | `{SYM: {distinct_buyers_6m, net_shares_2q, last_buy}}` | Alpha Vantage `INSIDER_TRANSACTIONS` (count distinct buyers, net shares) | monthly |
| `institutions.json` | `{SYM: {ownership_pct_by_quarter: [q-2, q-1, q]}}` | Alpha Vantage `INSTITUTIONAL_HOLDINGS` | monthly |
| `analysts.json` | `{SYM: {net_upgrades_90d, buy, hold, sell}}` | `get_equity_analyst_ratings` | monthly |
| `peers.json` | `{SYM: {similar_to, why}}` | Researcher subagent (Bigdata.com / web) | weekly |
| `historicals.json` | `{SYM: [[date, close], …]}` ≥ 18 months daily | `get_equity_historicals` (10 symbols/call) | daily for scan symbols |

`universe.py` prints `STALE: {...}`; Claude fetches **only** those symbols for
only those sources. `data/stale.json` holds the same list.

## Sizing

Three ceilings, lowest wins, then `contracts = floor(allowed / premium)`:
risk budget (`risk_per_trade_pct` × lane `risk_multiplier` × account ÷ stop
fraction), hard premium cap, options-bucket room. Kelly computed and reported;
`kelly_mode: ceiling` makes it a fourth limit.

At $1,612 and 5% risk: low lane allows $161 premium, balanced $129, high $97.
A $3.00 contract doesn't fit any lane until risk ≈ 12%. The brief says so.

## The run

### Tier 0 — pulse (every weekday, ~4 tool calls, no message on quiet days)
```
get_portfolio · get_equity_positions · get_option_positions(nonzero)
get_equity_quotes(holdings + universe symbols with conviction ≥ 30, ≤ 20/call)
python3 triggers.py
```
TIER 0 → stop. No chains, no subagents, no message.

### Tier 1 — scan (triggered, ≤ 3 symbols, ~15 tool calls, 2 subagents)
```
get_equity_historicals(scan symbols, 18 months, interval=day)  → historicals.json   1 call
get_equity_technical_indicators(rsi, day, latest, ≤10 syms)    → rsi.json           1 call
python3 universe.py  →  fetch only what STALE lists for scan symbols  →  python3 universe.py again
per symbol: get_option_chains → pick 3 expirations (one per lane: ~14d, ~30d, ~60d)
            get_option_instruments ×3 (ATM ±2 strikes, calls + puts) → get_option_quotes (1 call)
python3 analyze.py && python3 report.py
```
Subagents, parallel, each given ONLY `data/analysis.json`:
- **Scanner** — per idea: bull case in one line, the one thing that kills it,
  whether the catalyst/evidence is real (verify with ≤2 Bigdata.com searches).
- **Critic** — argue against the top idea in each lane; base rates; what the
  Kelly line implies; whether the "history favors" flag is signal or noise (n matters).

Append both under `## Reasoning`, `SendUserFile(status: proactive)`, one-paragraph chat summary.

### Tier 2 — weekly (Mondays): Tier 1 for all + discovery + grading + allocation
```
Researcher subagent (≤ research_searches_per_run lookups, Bigdata.com first, web second):
  for each holding → peers_per_holding comparable names + why (sector, business model, size)
  for each universe symbol with events stale → upcoming shareholder meetings, investor days,
    product events, lockup expiries in next 60 days, with dates
  writes data/raw/peers.json and data/raw/events.json ONLY
python3 universe.py --add <peers>   →  fetch STALE for new names (insiders, 13F, analysts, events)
get_option_quotes(every instrument_id in proposals.jsonl, ≤20) → grade_quotes.json → python3 grade.py
Allocator subagent (analysis.json + grades.json): 0–2 relocations with trigger, cost, bucket effect.
```

### When Sam approves an idea
He places it (account is read-only to the bot). With his confirmation, the bot
sets Robinhood alerts on the underlying: `price_below` at
`S − 0.5·premium/delta` (stop) and `price_above` at `S + premium/delta`
(target). For run-up plays it also sets a reminder for T-1 to exit before the
event. Logs `{"placed": true, "fill": …}` to the proposal line.

## Scheduled-task prompt

> You are the Options Bot. Work in `/home/claude/options_bot` (if missing: `gh repo
> clone <owner>/options-bot /home/claude/options_bot`; if that fails, stop and tell
> Sam). Read `PLAYBOOK.md` and `config.yaml`. Account 932707813 is READ-ONLY:
> never call place_*, cancel_*, exercise_*, or any order tool; `create_alert` only
> for a trade Sam explicitly approved in a prior message. Run Tier 0 exactly as the
> playbook specifies, writing compact JSON to `data/` in the shapes `sample_data/`
> shows — never dump raw tool output. Run `python3 triggers.py` and obey its TIER
> line literally. On TIER 0 end with no message. On TIER 1/2 follow the playbook:
> refresh only what `universe.py` lists as STALE, spawn the named subagents in
> parallel with only the files named, append their findings under `## Reasoning`,
> commit `data/` and `reports/` to the repo, and send the brief with SendUserFile
> (status proactive). One-paragraph chat summary. Never place a trade.

Cadence: weekdays 10:30 AM ET. Push on, email off.

## Usage budget (per run, estimated)

| Day | Tool calls | Subagents |
|---|---|---|
| Quiet (Tier 0) | 4–5 | 0 |
| Triggered (Tier 1) | ~15–20 | 2 |
| Monday (Tier 2) | ~30–40 | 3 |

Typical week: 3–4 quiet days, 1 Monday, 0–1 triggered. To cut further: raise
`move_pct`, lower `max_symbols_per_scan`, or set `weekly_review_day: Never`.

## Guardrails

- Read-only account; scripts have no network access.
- `min_dte` for the high lane is 7. Weeklies never appear unless you lower it.
- Every idea ships with lane, size, stop, target, breakeven σ, scenario table, Kelly line, evidence.
- Conviction is evidence-weighted, not predictive. n < 4 events → no event stats at all.
- `grade.py` is the only judge of whether any of this works. Give it a month.
