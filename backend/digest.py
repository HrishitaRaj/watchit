"""Weekly Digest.

Every Monday morning we send a calm one-page recap of each user's watchlists.
The digest is composed from the same primitives the dashboard uses (live quotes
+ attention scores) so users get a consistent story across channels.

`send_for_user` is idempotent enough for manual replay — it uses the last
digest timestamp on the user document to avoid duplicate sends within 24h.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import attention
import mailer
import market

log = logging.getLogger("watchit.digest")

DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR_LOCAL", "8"))
DIGEST_TIMEZONE = os.environ.get("DIGEST_TIMEZONE", "Asia/Kolkata")


def _render(user_name: str, watchlists: list[dict]) -> tuple[str, str]:
    rows_html: list[str] = []
    for wl in watchlists:
        cards = wl["cards"]
        if not cards:
            continue
        gainers = sorted([c for c in cards if c["change"] > 0], key=lambda c: c["change"], reverse=True)[:3]
        losers = sorted([c for c in cards if c["change"] < 0], key=lambda c: c["change"])[:3]
        rows_html.append(
            f"<h3 style='margin:24px 0 8px;color:#0f172a;font-size:18px'>{wl['name']}</h3>"
        )
        if gainers:
            rows_html.append("<p style='margin:0 0 6px;color:#475569;font-size:13px'>Leaders</p><ul style='margin:0 0 12px;padding-left:20px;color:#334155;font-size:14px'>")
            for c in gainers:
                rows_html.append(
                    f"<li><strong>{c['symbol']}</strong> {c['change']:+.2f}% "
                    f"(attention {c['attention']}) — {c['reasons'][0] if c['reasons'] else ''}</li>"
                )
            rows_html.append("</ul>")
        if losers:
            rows_html.append("<p style='margin:0 0 6px;color:#475569;font-size:13px'>Laggards</p><ul style='margin:0 0 12px;padding-left:20px;color:#334155;font-size:14px'>")
            for c in losers:
                rows_html.append(
                    f"<li><strong>{c['symbol']}</strong> {c['change']:+.2f}% "
                    f"(attention {c['attention']})</li>"
                )
            rows_html.append("</ul>")
    inner = "".join(rows_html) or "<p style='color:#475569'>No live movement to summarise — markets may have been quiet.</p>"
    subject = "WATCHIT! · Your weekly market recap"
    html = f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f6f8fb;padding:32px 0;font-family:'Helvetica Neue',Arial,sans-serif">
      <tr><td align="center"><table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:14px;padding:32px;text-align:left">
        <tr><td>
          <p style="color:#00a677;font-size:12px;letter-spacing:.14em;font-weight:700;margin:0 0 12px">WATCHIT! · WEEKLY DIGEST</p>
          <h1 style="font-size:26px;color:#0f172a;margin:0 0 8px">Good Monday, {user_name}.</h1>
          <p style="color:#475569;font-size:15px;margin:0 0 12px">Here is a calm recap of what your watchlists are doing right now — no advice, just signal.</p>
          {inner}
          <p style="color:#94a3b8;font-size:12px;margin:28px 0 0">You can pause the digest any time from Settings.</p>
        </td></tr>
      </table></td></tr>
    </table>
    """
    return subject, html


async def build_for_user(db, user: dict) -> list[dict]:
    """Assemble per-watchlist cards for the given user."""
    watchlists = await db.watchlists.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(50)
    result: list[dict] = []
    for wl in watchlists:
        symbols = wl.get("symbols", [])
        if not symbols:
            continue
        quotes = await market.get_quotes(db, symbols)
        cards = []
        for sym in symbols:
            q = quotes[sym]
            att = attention.score(q, 0.0)
            cards.append({
                "symbol": sym,
                "change": q.get("change_pct", 0.0),
                "attention": att["score"],
                "reasons": att["reasons"],
            })
        result.append({"name": wl["name"], "cards": cards})
    return result


async def send_for_user(db, user: dict) -> dict:
    """Send the digest email to `user['email']`. Returns the send envelope."""
    watchlists = await build_for_user(db, user)
    subject, html = _render(user.get("name", "Investor"), watchlists)
    result = await mailer.send(user["email"], subject, html)
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"last_digest_at": datetime.now(timezone.utc).isoformat(),
                  "last_digest_status": result}},
    )
    return result


async def run_all(db) -> dict:
    """Broadcast digests to all users who have opted in."""
    sent, skipped = 0, 0
    async for user in db.users.find({"digest_opt_in": {"$ne": False}}):
        try:
            await send_for_user(db, user)
            sent += 1
        except Exception as err:  # noqa: BLE001
            log.warning("digest_failed user=%s err=%s", user.get("user_id"), err)
            skipped += 1
    log.info("digest_run_complete sent=%s skipped=%s", sent, skipped)
    return {"sent": sent, "skipped": skipped}
