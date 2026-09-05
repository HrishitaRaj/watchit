"""Attention Alerts.

Users opt-in to receive a quiet email when a stock in their watchlist
crosses a configured attention threshold. Alerts respect a per-alert
cooldown so we never spam the same person about the same stock.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import mailer

log = logging.getLogger("watchit.alerts")

COOLDOWN_HOURS = int(os.environ.get("ALERT_COOLDOWN_HOURS", "6"))


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def list_for_user(db, user_id: str) -> list[dict]:
    return await db.alerts.find({"user_id": user_id}, {"_id": 0}).to_list(200)


async def create(db, user_id: str, symbol: str, threshold: int, email: str) -> dict:
    doc = {
        "alert_id": "alert_" + secrets.token_hex(6),
        "user_id": user_id,
        "symbol": symbol.upper(),
        "threshold": max(0, min(100, threshold)),
        "email": email.lower().strip(),
        "active": True,
        "created_at": _iso_now(),
        "last_triggered_at": None,
        "trigger_count": 0,
    }
    await db.alerts.insert_one(dict(doc))
    return doc


async def update(db, user_id: str, alert_id: str, changes: dict) -> dict | None:
    allowed = {k: v for k, v in changes.items() if k in {"threshold", "active", "email"}}
    if "threshold" in allowed:
        allowed["threshold"] = max(0, min(100, int(allowed["threshold"])))
    if not allowed:
        return await db.alerts.find_one({"alert_id": alert_id, "user_id": user_id}, {"_id": 0})
    from pymongo import ReturnDocument
    return await db.alerts.find_one_and_update(
        {"alert_id": alert_id, "user_id": user_id},
        {"$set": allowed},
        return_document=ReturnDocument.AFTER, projection={"_id": 0},
    )


async def delete(db, user_id: str, alert_id: str) -> bool:
    result = await db.alerts.delete_one({"alert_id": alert_id, "user_id": user_id})
    return bool(result.deleted_count)


async def evaluate(db, user_id: str, user_name: str, stocks: list[dict]) -> list[str]:
    """Send emails for alerts that just crossed their threshold. Returns fired alert_ids."""
    alerts = await db.alerts.find(
        {"user_id": user_id, "active": True,
         "symbol": {"$in": [s["symbol"] for s in stocks]}},
        {"_id": 0},
    ).to_list(200)
    if not alerts:
        return []
    stock_map = {s["symbol"]: s for s in stocks}
    now = datetime.now(timezone.utc)
    cooldown = timedelta(hours=COOLDOWN_HOURS)
    fired: list[str] = []

    async def _fire(alert: dict, stock: dict) -> None:
        subject, html = mailer.render_attention_alert(user_name, stock, alert["threshold"])
        result = await mailer.send(alert["email"], subject, html)
        await db.alerts.update_one(
            {"alert_id": alert["alert_id"]},
            {
                "$set": {"last_triggered_at": _iso_now(),
                         "last_email_id": result.get("id"),
                         "last_email_error": result.get("error")},
                "$inc": {"trigger_count": 1},
            },
        )
        fired.append(alert["alert_id"])

    tasks: list = []
    for alert in alerts:
        stock = stock_map.get(alert["symbol"])
        if not stock:
            continue
        if stock.get("attention", 0) < alert["threshold"]:
            continue
        last = alert.get("last_triggered_at")
        if last:
            last_dt = datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if now - last_dt < cooldown:
                continue
        tasks.append(_fire(alert, stock))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return fired
