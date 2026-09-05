"""Snapshot Engine.

Persist a small snapshot every time the user visits the dashboard so we can
answer *what happened while I was away?* on the next visit.

- `save` writes one snapshot per visit, but throttles rapid page reloads via
  `DEBOUNCE_SECONDS` so refreshes don't wipe the previous session.
- `previous` fetches the most recent snapshot that is *older* than the one we
  just saved, guaranteeing a stable "last visit" reference.
- `last_for_symbol` finds the last remembered price for one ticker across all
  a user's watchlists — used on the stock-detail page comparison card.
- `diff` computes meaningful movements between two snapshots.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone

DEBOUNCE_SECONDS = int(os.environ.get("SNAPSHOT_DEBOUNCE_SECONDS", "300"))


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def save(db, user_id: str, watchlist_id: str, items: list[dict]) -> dict:
    """Persist a snapshot. Returns the stored document (without _id)."""
    doc = {
        "snapshot_id": "snap_" + secrets.token_hex(6),
        "user_id": user_id,
        "watchlist_id": watchlist_id,
        "captured_at": _iso_now(),
        "items": [
            {
                "symbol": item["symbol"],
                "close": item.get("close", 0.0),
                "volume": item.get("volume", 0),
                "attention": item.get("attention", 0),
                "change_pct": item.get("change_pct", 0.0),
            }
            for item in items
        ],
    }
    await db.snapshots.insert_one(dict(doc))
    return doc


async def latest(db, user_id: str, watchlist_id: str) -> dict | None:
    return await db.snapshots.find_one(
        {"user_id": user_id, "watchlist_id": watchlist_id},
        {"_id": 0},
        sort=[("captured_at", -1)],
    )


async def previous(db, user_id: str, watchlist_id: str, exclude_id: str | None = None) -> dict | None:
    query: dict = {"user_id": user_id, "watchlist_id": watchlist_id}
    if exclude_id:
        query["snapshot_id"] = {"$ne": exclude_id}
    return await db.snapshots.find_one(query, {"_id": 0}, sort=[("captured_at", -1)])


async def last_for_symbol(db, user_id: str, symbol: str, before: datetime | None = None) -> dict | None:
    """Find the most recent snapshot item across any of the user's watchlists
    that contains `symbol`. Optionally restrict to snapshots older than `before`."""
    query: dict = {"user_id": user_id, "items.symbol": symbol.upper()}
    if before is not None:
        query["captured_at"] = {"$lt": before.isoformat() if isinstance(before, datetime) else before}
    doc = await db.snapshots.find_one(query, {"_id": 0}, sort=[("captured_at", -1)])
    if not doc:
        return None
    match = next((it for it in doc.get("items", []) if it["symbol"] == symbol.upper()), None)
    if not match:
        return None
    return {"captured_at": doc["captured_at"], "watchlist_id": doc.get("watchlist_id"), "item": match}


async def record_visit(db, user_id: str, watchlist_id: str, items: list[dict]) -> tuple[dict, dict | None]:
    """Debounced save: returns (current_snapshot, previous_snapshot_for_diff).

    If the last snapshot for this watchlist is younger than `DEBOUNCE_SECONDS`,
    we skip persisting and reuse the one before it as "previous", so a page
    reload does not overwrite the user's last-visit reference point.
    """
    last = await latest(db, user_id, watchlist_id)
    now = datetime.fromisoformat(_iso_now())
    if last:
        last_at = datetime.fromisoformat(last["captured_at"])
        if (now - last_at).total_seconds() < DEBOUNCE_SECONDS:
            prev = await previous(db, user_id, watchlist_id, exclude_id=last["snapshot_id"])
            return last, prev
    current = await save(db, user_id, watchlist_id, items)
    return current, last


def diff(previous_snapshot: dict | None, current_items: list[dict]) -> dict:
    """Compute a compact diff for the dashboard header.

    Returns:
        {last_visit_at, changes: [{symbol, price_delta_pct, volume_delta_pct,
                                    attention_delta, direction, current_close}]}
    """
    if not previous_snapshot:
        return {"last_visit_at": None, "changes": []}
    prev_map = {it["symbol"]: it for it in previous_snapshot.get("items", [])}
    changes: list[dict] = []
    for cur in current_items:
        prev = prev_map.get(cur["symbol"])
        if not prev or not prev.get("close"):
            continue
        price_delta = (cur["close"] - prev["close"]) / prev["close"] * 100 if prev["close"] else 0.0
        vol_delta = (cur["volume"] - prev["volume"]) / prev["volume"] * 100 if prev["volume"] else 0.0
        attn_delta = cur.get("attention", 0) - prev.get("attention", 0)
        if abs(price_delta) < 0.5 and abs(attn_delta) < 5:
            continue
        changes.append({
            "symbol": cur["symbol"],
            "price_delta_pct": round(price_delta, 2),
            "volume_delta_pct": round(vol_delta, 2),
            "attention_delta": attn_delta,
            "direction": "up" if price_delta >= 0 else "down",
            "current_close": cur["close"],
        })
    changes.sort(key=lambda c: abs(c["price_delta_pct"]) + abs(c["attention_delta"]) * 0.5, reverse=True)
    return {"last_visit_at": previous_snapshot.get("captured_at"), "changes": changes[:8]}
