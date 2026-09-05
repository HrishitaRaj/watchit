"""Attention Engine.

Every stock gets a deterministic 0-100 score that blends real signals from the
live snapshot with an optional sector-level trend. Every score ships with a
handful of plain-English reasons so the UI can always answer *why*.

Weights (sum to 100):
  price movement       30
  volume spike         25
  52-week breakout     20
  short-term momentum  15
  sector context       10
"""
from __future__ import annotations


def _band(value: float, low: float, high: float, weight: float) -> float:
    """Linearly interpolate `value` in [low, high] onto [0, weight]."""
    if value <= low:
        return 0.0
    if value >= high:
        return weight
    return (value - low) / (high - low) * weight


def score(snapshot: dict, sector_trend_pct: float = 0.0) -> dict:
    """Return {"score": int 0-100, "reasons": [str, ...]} for one stock snapshot.

    Snapshot expected keys: close, change_pct, volume_ratio, momentum_5d_pct,
    high_52w, low_52w. Missing/zero values collapse safely to a 0 contribution.
    """
    close = snapshot.get("close", 0.0)
    change_pct = snapshot.get("change_pct", 0.0)
    volume_ratio = snapshot.get("volume_ratio", 0.0)
    momentum = snapshot.get("momentum_5d_pct", 0.0)
    hi_52w = snapshot.get("high_52w", 0.0)
    lo_52w = snapshot.get("low_52w", 0.0)

    price_pts = _band(abs(change_pct), 0.5, 6.0, 30.0)
    volume_pts = _band(volume_ratio, 1.2, 2.5, 25.0)
    momentum_pts = _band(abs(momentum), 1.0, 8.0, 15.0)
    sector_pts = _band(abs(sector_trend_pct), 0.5, 3.5, 10.0)

    breakout_pts = 0.0
    reasons: list[str] = []
    if close and hi_52w and close >= hi_52w * 0.98:
        breakout_pts = 20.0
        reasons.append("Trading within 2% of the 52-week high")
    elif close and lo_52w and close <= lo_52w * 1.02:
        breakout_pts = 20.0
        reasons.append("Trading within 2% of the 52-week low")

    if change_pct > 0.5:
        reasons.append(f"Price up {change_pct:+.2f}% today")
    elif change_pct < -0.5:
        reasons.append(f"Price down {change_pct:+.2f}% today")
    else:
        reasons.append("Price is relatively unchanged today")

    if volume_ratio >= 2.0:
        reasons.append(f"Volume {volume_ratio:.1f}× the 20-day average")
    elif volume_ratio >= 1.3:
        reasons.append(f"Above-average volume ({volume_ratio:.1f}×)")

    if abs(momentum) >= 3:
        reasons.append(f"5-day momentum {momentum:+.2f}%")
    if abs(sector_trend_pct) >= 1:
        reasons.append(f"Sector moved {sector_trend_pct:+.2f}% today")

    total = round(price_pts + volume_pts + breakout_pts + momentum_pts + sector_pts)
    return {"score": max(0, min(100, total)), "reasons": reasons[:4]}
