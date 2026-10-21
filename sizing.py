"""
sizing.py — how big should this trade be, given THIS account?

Three ceilings, take the lowest:
  1. Risk budget:   risk_per_trade_pct × account = max loss at the stop
                    → premium_allowed = risk_budget / stop_fraction
  2. Kelly ceiling: f* = p − (1−p)/b, scaled by kelly_fraction, × account
                    where p = chance of profit, b = (payoff at +1σ) / premium
  3. Hard cap:      max_premium_pct × account

Then contracts = floor(min_ceiling / premium). Zero is a legitimate answer —
the report says what the cheapest viable contract would be.
"""
import math


def kelly_fraction(p, b):
    """Kelly f* for win-prob p and net odds b (win $b per $1 risked). Clamped ≥0."""
    if b <= 0 or p <= 0:
        return 0.0
    return max(0.0, p - (1 - p) / b)


def size_trade(premium, cop, payoff_1sigma, account_value, options_outstanding, cfg):
    """
    premium            cost of ONE contract in dollars (mark × 100)
    cop                broker chance-of-profit (0–1)
    payoff_1sigma      dollar P&L of one contract if the underlying moves +1σ favorably by expiry
    account_value      total account value
    options_outstanding premium already deployed in open options
    """
    s = cfg["sizing"]
    stop_frac = s["stop_loss_pct"] / 100

    risk_budget = account_value * s["risk_per_trade_pct"] / 100
    ceiling_risk = risk_budget / stop_frac if stop_frac else risk_budget

    b = payoff_1sigma / premium if premium else 0
    f = kelly_fraction(cop, b) * s["kelly_fraction"]
    ceiling_kelly = f * account_value

    ceiling_cap = account_value * s["max_premium_pct"] / 100

    bucket_room = account_value * s["options_bucket_pct"] / 100 - options_outstanding

    ceilings = {
        "risk_budget": round(ceiling_risk),
        "hard_cap": round(ceiling_cap),
        "bucket_room": round(max(0, bucket_room)),
    }
    if s.get("kelly_mode", "flag") == "ceiling":
        ceilings["quarter_kelly"] = round(ceiling_kelly)
    allowed = min(ceilings.values())
    binding = min(ceilings, key=ceilings.get)
    contracts = int(math.floor(allowed / premium)) if premium else 0

    kelly_full = kelly_fraction(cop, b)
    edge_note = (f"Kelly says no edge (CoP {cop:.0%} needs >{(1 - cop) / cop:.1f}:1 payoff, "
                 f"+1σ pays {b:.1f}:1) — this is a controlled bet, not an edge"
                 if kelly_full == 0 else f"Kelly edge {kelly_full:.0%} (quarter-Kelly size ${ceiling_kelly:.0f})")

    return {
        "contracts": contracts,
        "allowed_premium": round(allowed),
        "binding_constraint": binding,
        "ceilings": ceilings,
        "kelly_size": round(ceiling_kelly),
        "edge_note": edge_note,
        "risk_budget": round(risk_budget),
        "max_loss_at_stop": round(contracts * premium * stop_frac),
        "kelly_full": round(kelly_full, 3),
        "net_odds_b": round(b, 2),
        "pct_of_account": round(contracts * premium / account_value * 100, 1) if account_value else 0,
    }
