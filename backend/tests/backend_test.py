"""WATCHIT! backend regression + new feature tests.

Covers:
 - auth (register/me/logout)
 - dashboard (live stocks + envelope status)
 - stock detail + news endpoint (real Yahoo News)
 - custom NSE ticker universe (PAYTM ok, BOGUS404)
 - sector deep dive (Technology + FMCG empty)
 - watchlists CRUD
 - legacy watchlist self-heal (missing watchlist_id -> dashboard still 200)
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    email = f"watchit.{uuid.uuid4().hex[:10]}@example.com"
    r = s.post(f"{BASE_URL}/api/auth/register",
               json={"name": "Aarav", "email": email, "password": "watchit123"})
    assert r.status_code == 200, r.text
    s.email = email
    return s


# ---- auth ---------------------------------------------------------------------
def test_auth_me(session):
    r = session.get(f"{BASE_URL}/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == session.email


# ---- dashboard ----------------------------------------------------------------
def test_dashboard_live_stocks(session):
    r = session.get(f"{BASE_URL}/api/dashboard")
    assert r.status_code == 200
    data = r.json()
    assert "stocks" in data and len(data["stocks"]) >= 6
    live = [s for s in data["stocks"] if s.get("status") == "live"]
    assert len(live) >= 1, f"No live stocks: statuses={[s['status'] for s in data['stocks']]}"
    for s in data["stocks"]:
        assert "attention" in s and "reasons" in s
        assert isinstance(s["reasons"], list)


# ---- stock detail + news ------------------------------------------------------
def test_stock_detail_reliance(session):
    r = session.get(f"{BASE_URL}/api/stocks/RELIANCE")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["symbol"] == "RELIANCE"
    assert isinstance(d["news"], list) and len(d["news"]) >= 1
    n0 = d["news"][0]
    for k in ("title", "source", "url", "time"):
        assert k in n0


def test_stock_news_endpoint(session):
    r = session.get(f"{BASE_URL}/api/stocks/RELIANCE/news")
    assert r.status_code == 200
    d = r.json()
    assert d["symbol"] == "RELIANCE"
    assert isinstance(d["news"], list)
    if d["count"] > 0:
        item = d["news"][0]
        for k in ("url", "title", "source", "time", "summary", "published_at"):
            assert k in item, f"missing {k} in news item {item.keys()}"


# ---- custom universe ----------------------------------------------------------
def test_add_custom_ticker_paytm_and_reject_bogus(session):
    lists = session.get(f"{BASE_URL}/api/watchlists").json()
    wid = lists[0]["watchlist_id"]

    # PAYTM (not curated) must be verified & persisted
    r = session.post(f"{BASE_URL}/api/watchlists/{wid}/stocks",
                     json={"symbol": "PAYTM", "exchange": "XNSE"})
    assert r.status_code == 200, r.text
    assert "PAYTM" in r.json()["symbols"]

    # BOGUSTICKER123 must 404 with helpful message
    r = session.post(f"{BASE_URL}/api/watchlists/{wid}/stocks",
                     json={"symbol": "BOGUSTICKER123", "exchange": "XNSE"})
    assert r.status_code == 404
    assert "verified" in r.json().get("detail", "").lower()

    # PAYTM present in dashboard/watchlists after add
    lists2 = session.get(f"{BASE_URL}/api/watchlists").json()
    assert "PAYTM" in lists2[0]["symbols"]
    dash = session.get(f"{BASE_URL}/api/dashboard").json()
    assert any(s["symbol"] == "PAYTM" for s in dash["stocks"])


# ---- sector deep dive ---------------------------------------------------------
def test_sector_technology(session):
    r = session.get(f"{BASE_URL}/api/sectors/Technology")
    assert r.status_code == 200, r.text
    d = r.json()
    for k in ("name", "constituents", "leaders", "laggards", "avg_change",
              "total", "total_volume", "top_attention"):
        assert k in d, f"missing {k}"
    assert d["total"] >= 1
    assert isinstance(d["constituents"], list)


def test_sector_empty_returns_404(session):
    # FMCG has no default constituents (ITC/HINDUNILVR aren't in default list)
    r = session.get(f"{BASE_URL}/api/sectors/FMCG")
    # Default watchlist has no FMCG entries so should 404
    assert r.status_code == 404
    body = r.json()
    assert "watchlist" in body.get("detail", "").lower() or "no stocks" in body.get("detail", "").lower()


# ---- watchlists CRUD ----------------------------------------------------------
def test_watchlists_crud(session):
    name = f"TEST_{uuid.uuid4().hex[:6]}"
    r = session.post(f"{BASE_URL}/api/watchlists", json={"name": name})
    assert r.status_code == 200
    wid = r.json()["watchlist_id"]

    # rename
    new_name = name + "_r"
    r = session.patch(f"{BASE_URL}/api/watchlists/{wid}", json={"name": new_name})
    assert r.status_code == 200
    assert r.json()["name"] == new_name

    # add/remove stock (curated symbol)
    r = session.post(f"{BASE_URL}/api/watchlists/{wid}/stocks",
                     json={"symbol": "WIPRO", "exchange": "XNSE"})
    assert r.status_code == 200
    assert "WIPRO" in r.json()["symbols"]
    r = session.delete(f"{BASE_URL}/api/watchlists/{wid}/stocks/WIPRO")
    assert r.status_code == 200
    assert "WIPRO" not in r.json()["symbols"]

    # delete
    r = session.delete(f"{BASE_URL}/api/watchlists/{wid}")
    assert r.status_code == 200


# ---- legacy self-heal ---------------------------------------------------------
def test_legacy_watchlist_self_heal_via_dashboard():
    """Inject a legacy watchlist doc (no watchlist_id) via a fresh user
    and confirm dashboard returns 200 (self-heal path)."""
    import pymongo
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not (mongo_url and db_name):
        pytest.skip("Mongo env not available in test runner")
    from motor.motor_asyncio import AsyncIOMotorClient  # noqa: F401
    client = pymongo.MongoClient(mongo_url)
    db = client[db_name]

    s = requests.Session()
    email = f"legacy.{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{BASE_URL}/api/auth/register",
               json={"name": "Legacy", "email": email, "password": "watchit123"})
    assert r.status_code == 200
    user_id = r.json()["user_id"]

    # Wipe default watchlist and insert legacy (no watchlist_id)
    db.watchlists.delete_many({"user_id": user_id})
    db.watchlists.insert_one({
        "user_id": user_id,
        "name": "Legacy WL",
        "symbols": ["RELIANCE", "TCS"],
    })

    r = s.get(f"{BASE_URL}/api/dashboard")
    assert r.status_code == 200, f"legacy dashboard failed: {r.status_code} {r.text[:200]}"

    # After request, doc should have watchlist_id assigned by self-heal
    healed = db.watchlists.find_one({"user_id": user_id})
    assert healed and healed.get("watchlist_id"), "self-heal did not add watchlist_id"

    # And startup migration should also ensure no doc is missing watchlist_id
    missing = list(db.watchlists.find({"watchlist_id": {"$exists": False}}))
    assert not missing, f"{len(missing)} watchlists still missing watchlist_id"

    client.close()


# ---- logout at the end --------------------------------------------------------
def test_zzz_logout(session):
    assert session.post(f"{BASE_URL}/api/auth/logout").status_code == 200
    assert session.get(f"{BASE_URL}/api/auth/me").status_code == 401
