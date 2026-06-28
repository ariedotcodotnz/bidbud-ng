import json

from app import config, db
from .factories import frend_html


def test_healthz(api):
    client, _ = api
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


class TestDashboard:
    def test_logged_out_shows_banner(self, api):
        client, fakes = api
        fakes.member_id = None
        r = client.get("/")
        assert r.status_code == 200
        assert "Watched auctions" in r.text
        assert "No auctions yet" in r.text
        assert "not logged in to TradeMe" in r.text

    def test_logged_in_hides_banner(self, api):
        client, fakes = api
        fakes.member_id = 42
        r = client.get("/")
        assert r.status_code == 200
        assert "not logged in to TradeMe" not in r.text


def test_static_pages_render(api):
    client, _ = api
    for path in ("/add", "/settings", "/login", "/login/status", "/partials/jobs"):
        assert client.get(path).status_code == 200, path


class TestSettings:
    def test_post_persists(self, api):
        client, _ = api
        r = client.post("/settings", data={
            "default_strategy": "blocking", "shipping_preference": "dearest",
            "snipe_seconds": "10", "fast_lead_seconds": "90",
            "poll_far_seconds": "5", "poll_near_seconds": "2",
            "poll_final_seconds": "1", "trademe_email": "me@example.com",
            "dont_add_cents": "on",
        })
        assert r.status_code == 200  # followed the redirect
        assert db.get_setting("default_strategy") == "blocking"
        assert db.get_setting("shipping_preference") == "dearest"
        assert db.get_bool("dont_add_cents") is True
        # unchecked boxes become "0"
        assert db.get_bool("bid_early_single_bid") is False

    def test_password_is_encrypted(self, api):
        client, _ = api
        client.post("/settings", data={"trademe_password": "s3cret"})
        enc = db.get_setting("trademe_password_enc")
        assert enc and enc != "s3cret"
        from app import security
        assert security.decrypt(enc) == "s3cret"


class TestPreview:
    def test_valid_listing(self, api):
        client, fakes = api
        fakes.html = frend_html(member_id=1, current=1, min_next=1.5, title="Cool Item")
        r = client.post("/listings/preview", data={"url": "6006426545"})
        assert r.status_code == 200
        assert "Cool Item" in r.text
        assert "Auckland, Standard" in r.text       # real shipping option offered
        assert "North Island, Standard" in r.text

    def test_unreadable_listing(self, api):
        client, fakes = api
        fakes.html = None
        r = client.post("/listings/preview", data={"url": "999"})
        assert r.status_code == 200
        assert "Couldn" in r.text and "read that listing" in r.text

    def test_offers_pickup_when_allowed(self, api):
        client, fakes = api
        fakes.html = frend_html(extra_item={"allowsPickups": 1})  # Allow
        r = client.post("/listings/preview", data={"url": "6006426545"})
        assert 'value="pickup"' in r.text and "Pick-up" in r.text

    def test_no_pickup_when_forbidden(self, api):
        client, fakes = api
        fakes.html = frend_html(extra_item={"allowsPickups": 3})  # Forbid
        r = client.post("/listings/preview", data={"url": "6006426545"})
        assert 'value="pickup"' not in r.text


class TestJobLifecycle:
    def test_create_view_cancel_delete(self, api):
        client, _ = api
        r = client.post("/listings", data={
            "url": "https://www.trademe.co.nz/a/marketplace/listing/6006426545",
            "listing_id": "6006426545", "title": "Test MacBook",
            "strategy": "fast", "max_bid": "42.50",
            "end_date": "2030-01-01T00:00:00+00:00", "current_price": "1.00",
            "shipping_choice": "5",
        })
        assert r.status_code == 200  # redirected to dashboard

        jobs = db.list_jobs()
        assert len(jobs) == 1
        jid = jobs[0]["id"]
        import json
        assert json.loads(jobs[0]["options"])["shipping_choice"] == "5"

        assert "Test MacBook" in client.get("/partials/jobs").text
        assert client.get(f"/jobs/{jid}").status_code == 200

        client.post(f"/jobs/{jid}/cancel")
        assert db.get_job(jid)["status"] == "cancelled"

        client.post(f"/jobs/{jid}/delete")
        assert db.get_job(jid) is None

    def test_create_rejects_bad_max_bid(self, api):
        client, _ = api
        r = client.post("/listings", data={
            "url": "https://x/listing/1", "listing_id": "1", "title": "X",
            "strategy": "fast", "max_bid": "0",
        })
        assert r.status_code == 400


class TestSessionImport:
    def test_import_valid_session(self, api, monkeypatch):
        client, _ = api
        from app.trademe.browser import browser as bm
        from app.trademe.auth import login_manager

        async def fake_apply(state):
            assert state["cookies"] and "trademe" in state["cookies"][0]["domain"]
            return 4242

        monkeypatch.setattr(bm, "apply_storage_state", fake_apply)
        blob = json.dumps({"cookies": [
            {"name": "idsrv", "value": "x", "domain": ".trademe.co.nz", "path": "/"}
        ], "origins": []}).encode()

        r = client.post("/session/import",
                        files={"session_file": ("s.json", blob, "application/json")})
        assert r.status_code == 200  # followed redirect to /login
        assert login_manager.member_id == 4242
        assert login_manager.state == "success"

    def test_import_invalid_session(self, api):
        client, _ = api
        from app.trademe.auth import login_manager
        r = client.post("/session/import",
                        files={"session_file": ("s.json", b"garbage", "application/json")})
        assert r.status_code == 200
        assert login_manager.state == "error"
        assert "failed" in login_manager.message.lower()

    def test_login_page_shows_import_card(self, api):
        client, _ = api
        assert "Import session" in client.get("/login").text


class TestBasicAuth:
    def test_blocks_without_creds_and_allows_with(self, api, monkeypatch):
        client, _ = api
        monkeypatch.setattr(config, "UI_USER", "user")
        monkeypatch.setattr(config, "UI_PASS", "pass")
        assert client.get("/").status_code == 401
        ok = client.get("/", auth=("user", "pass"))
        assert ok.status_code == 200
