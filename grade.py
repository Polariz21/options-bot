#!/usr/bin/env python3
"""
grade.py — is the bot any good? Grades past proposals against current prices.

Weekly (Tier 2). Claude pulls get_option_quotes for every instrument_id in
proposals.jsonl (ONE call, up to 20 ids) and writes data/grade_quotes.json as
{instrument_id: mark}. This script then marks each proposal:

  WIN   mark ≥ target at any check, or currently ≥ entry
  STOP  mark ≤ stop
  OPEN  still live, between stop and target
  EXP   expired worthless / below entry

Prints hit rate, average R, and which score band actually paid — so you can
tune config.yaml on evidence instead of vibes.
"""
import argparse, json
from datetime import date
from pathlib import Path


def load(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(Path(__file__).parent / "data"))
    a = ap.parse_args()
    d = Path(a.data)
    marks = load(d / "grade_quotes.json") or {}
    rows = [json.loads(l) for l in (d / "proposals.jsonl").read_text().splitlines() if l.strip()] \
        if (d / "proposals.jsonl").exists() else []
    if not rows:
        print("No proposals logged yet."); return

    graded = []
    for r in rows:
        m = marks.get(r["instrument_id"])
        if m is None:
            continue
        m = float(m)
        expired = date.fromisoformat(r["expiration"]) < date.today()
        R = (m - r["entry_mark"]) / (r["entry_mark"] - r["stop"]) if r["entry_mark"] > r["stop"] else 0
        if m >= r["target"]: status = "WIN"
        elif m <= r["stop"]: status = "STOP"
        elif expired: status = "EXP"
        else: status = "OPEN"
        graded.append({**r, "mark_now": m, "R": round(R, 2), "status": status})

    closed = [g for g in graded if g["status"] != "OPEN"]
    wins = [g for g in closed if g["R"] > 0]
    print(f"{len(graded)} graded · {len(closed)} closed · hit rate {len(wins)}/{len(closed) or 1} "
          f"· avg R {sum(g['R'] for g in closed) / (len(closed) or 1):+.2f}")
    for band in ((0, 40), (40, 55), (55, 70), (70, 101)):
        b = [g for g in closed if band[0] <= g["score"] < band[1]]
        if b:
            print(f"  score {band[0]}–{band[1]}: n={len(b)} avg R {sum(g['R'] for g in b) / len(b):+.2f}")
    for g in graded:
        print(f"  {g['status']:4} {g['date']} {g['symbol']} ${g['strike']:g} {g['type']} {g['expiration']} "
              f"entry {g['entry_mark']} now {g['mark_now']} R {g['R']:+.2f}")
    (d / "grades.json").write_text(json.dumps(graded, indent=1))


if __name__ == "__main__":
    main()
