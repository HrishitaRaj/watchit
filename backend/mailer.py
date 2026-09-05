"""Resend-powered transactional email.

The SDK is synchronous; we always call it via `asyncio.to_thread` so the
FastAPI event loop stays non-blocking. Sending gracefully degrades to a
logged warning when the key is missing so alerts can still be recorded
in the UI even before the environment is fully configured.
"""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger("watchit.mailer")

_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
SENDER = os.environ.get("SENDER_EMAIL", "onboarding@resend.dev")

if _API_KEY:
    try:
        import resend  # noqa: E402
        resend.api_key = _API_KEY
    except ImportError:
        log.warning("resend package not found; email sending is disabled")
        resend = None  # type: ignore
else:
    resend = None  # type: ignore


async def send(recipient: str, subject: str, html: str) -> dict:
    """Send one transactional email. Returns {sent: bool, id?: str, error?: str}."""
    if not _API_KEY or resend is None:
        log.warning("resend_disabled recipient=%s reason=missing_api_key", recipient)
        return {"sent": False, "error": "Email sending is not configured"}
    params = {"from": SENDER, "to": [recipient], "subject": subject, "html": html}
    try:
        result = await asyncio.to_thread(resend.Emails.send, params)
        return {"sent": True, "id": (result or {}).get("id")}
    except Exception as err:  # noqa: BLE001
        log.warning("resend_send_failed recipient=%s err=%s", recipient, err)
        return {"sent": False, "error": str(err)}


def render_attention_alert(user_name: str, stock: dict, threshold: int) -> tuple[str, str]:
    """Return (subject, html_body) for the attention alert email."""
    change = stock.get("change", 0.0)
    change_str = f"{change:+.2f}%"
    price_str = f"₹{stock.get('price', 0):,.2f}"
    reasons = stock.get("reasons") or []
    reason_html = "".join(
        f"<li style='margin-bottom:6px;color:#334155'>{r}</li>" for r in reasons[:3]
    )
    subject = f"WATCHIT! · {stock['symbol']} is scoring {stock['attention']} attention"
    html = f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f6f8fb;padding:32px 0;font-family:'Helvetica Neue',Arial,sans-serif">
      <tr><td align="center">
        <table width="520" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:14px;padding:32px;text-align:left">
          <tr><td>
            <p style="color:#00a677;font-size:12px;letter-spacing:.14em;font-weight:700;margin:0 0 12px">
              WATCHIT! · ATTENTION ALERT
            </p>
            <h1 style="font-size:26px;color:#0f172a;margin:0 0 8px">
              {stock['symbol']} crossed your threshold
            </h1>
            <p style="color:#475569;font-size:15px;margin:0 0 24px">
              Hi {user_name}, {stock.get('name', stock['symbol'])} is scoring
              <strong>{stock['attention']}</strong>, above the {threshold} you set.
            </p>
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="background:#f8fafc;border-radius:10px;padding:18px;margin-bottom:22px">
              <tr>
                <td style="font-size:22px;color:#0f172a;font-weight:600">{price_str}</td>
                <td align="right" style="font-size:15px;color:{'#00a677' if change >= 0 else '#dc2626'};font-weight:600">
                  {change_str} today
                </td>
              </tr>
            </table>
            <p style="color:#475569;font-size:13px;margin:0 0 8px;font-weight:600">Why it matters</p>
            <ul style="padding-left:18px;margin:0 0 24px;color:#334155;font-size:14px">{reason_html}</ul>
            <p style="color:#94a3b8;font-size:12px;margin:0">
              You will only receive one alert per stock per cooldown window. Manage alerts in Settings.
            </p>
          </td></tr>
        </table>
      </td></tr>
    </table>
    """
    return subject, html
