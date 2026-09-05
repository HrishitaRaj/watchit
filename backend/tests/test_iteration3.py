"""Iteration 3 — new features: dashboard summary+watchlist_id, /stocks/preview, alerts CRUD, alert trigger."""
import os
import uuid
import time
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    email = f"watchit.i3.{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{BASE_URL}/api/auth/register",
               json={"name": "IterThree", "email": email, "password": "watchit123"})
    assert r.status_code == 200, r.text
    s.email = email
    s.user_id = r.json()["user_id"]
    return s


# ---- Bug fix: dashboard summary non-empty -------------------------------------
def test_dashboard_summary_non_empty(session):
    r = session.get(f"{BASE_URL}/api/dashboard")
    assert r.status_code == 200
    data = r.json()
    assert "summary" in data
    assert isinstance(data["summary"], str)
    assert data["summary"].strip(), "summary must not be blank"
    assert "active_watchlist" in data
    assert data["active_watchlist"].get("name")


# ---- Feature: watchlist picker (?watchlist_id switches active_watchlist) ------
def test_dashboard_switch_watchlist(session):
    # Create second watchlist
    name = f"Second_{uuid.uuid4().hex[:5]}"
    r = session.post(f"{BASE_URL}/api/watchlists", json={"name": name})
    assert r.status_code == 200
    wid = r.json()["watchlist_id"]

    # Dashboard with explicit watchlist_id must return that name
    r = session.get(f"{BASE_URL}/api/dashboard", params={"watchlist_id": wid})
    assert r.status_code == 200
    d = r.json()
    assert d["active_watchlist"]["watchlist_id"] == wid
    assert d["active_watchlist"]["name"] == name
    # Empty watchlist -> stocks empty, summary must still be non-empty
    assert d["summary"].strip()

    # cleanup
    session.delete(f"{BASE_URL}/api/watchlists/{wid}")


# ---- Feature: /api/stocks/preview --------------------------------------------
def test_stocks_preview_curated(session):
    r = session.get(f"{BASE_URL}/api/stocks/preview", params={"symbol": "TCS"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["symbol"] == "TCS"
    assert d["name"]
    assert d["sector"]
    assert "price" in d and "change_pct" in d


def test_stocks_preview_paytm(session):
    r = session.get(f"{BASE_URL}/api/stocks/preview", params={"symbol": "PAYTM"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["symbol"] == "PAYTM"
    # name should be verified (One97)
    assert d["name"] and d["name"].upper() != "PAYTM"
    assert "sector" in d


def test_stocks_preview_bogus_404(session):
    r = session.get(f"{BASE_URL}/api/stocks/preview", params={"symbol": "BOGUSNONE"})
    assert r.status_code == 404
    assert "verified" in r.json().get("detail", "").lower()


# ---- Feature: Alerts CRUD ----------------------------------------------------
def test_alerts_crud(session):
    # initial empty
    r = session.get(f"{BASE_URL}/api/alerts")
    assert r.status_code == 200
    assert r.json() == [] or isinstance(r.json(), list)

    # create
    r = session.post(f"{BASE_URL}/api/alerts", json={
        "symbol": "RELIANCE", "threshold": 55, "email": "delivered@resend.dev"
    })
    assert r.status_code == 200, r.text
    alert = r.json()
    assert alert["symbol"] == "RELIANCE"
    assert alert["threshold"] == 55
    assert alert["active"] is True
    aid = alert["alert_id"]

    # list contains it
    lst = session.get(f"{BASE_URL}/api/alerts").json()
    assert any(a["alert_id"] == aid for a in lst)

    # pause
    r = session.patch(f"{BASE_URL}/api/alerts/{aid}", json={"active": False})
    assert r.status_code == 200
    assert r.json()["active"] is False

    # resume + threshold update
    r = session.patch(f"{BASE_URL}/api/alerts/{aid}", json={"active": True, "threshold": 40})
    assert r.status_code == 200
    assert r.json()["active"] is True
    assert r.json()["threshold"] == 40

    # delete
    r = session.delete(f"{BASE_URL}/api/alerts/{aid}")
    assert r.status_code == 200
    # verify gone
    lst = session.get(f"{BASE_URL}/api/alerts").json()
    assert not any(a["alert_id"] == aid for a in lst)


# ---- Feature: Alert trigger via dashboard evaluate ---------------------------
def test_alert_trigger_via_dashboard(session):
    # threshold 0 so any live stock crosses
    r = session.post(f"{BASE_URL}/api/alerts", json={
        "symbol": "RELIANCE", "threshold": 0, "email": "delivered@resend.dev"
    })
    assert r.status_code == 200
    aid = r.json()["alert_id"]

    # trigger dashboard which schedules evaluate
    r = session.get(f"{BASE_URL}/api/dashboard")
    assert r.status_code == 200

    # background task, poll
    triggered = False
    for _ in range(15):
        time.sleep(1)
        lst = session.get(f"{BASE_URL}/api/alerts").json()
        row = next((a for a in lst if a["alert_id"] == aid), None)
        if row and row.get("trigger_count", 0) >= 1 and row.get("last_triggered_at"):
            triggered = True
            break

    session.delete(f"{BASE_URL}/api/alerts/{aid}")
    assert triggered, "alert did not fire within 15s after dashboard load"
