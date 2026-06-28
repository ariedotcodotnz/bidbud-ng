import json

from sqlalchemy import inspect, text
from sqlmodel import Session

from app import db


class TestSettings:
    def test_defaults_seeded(self, temp_db):
        assert db.get_setting("default_strategy") == "fast"
        assert db.get_bool("enter_default_bid") is True
        assert db.get_int("snipe_seconds") == 8

    def test_set_and_get(self, temp_db):
        db.set_setting("default_strategy", "slow")
        assert db.get_setting("default_strategy") == "slow"

    def test_set_many(self, temp_db):
        db.set_settings({"dont_add_cents": "1", "shipping_preference": "dearest"})
        assert db.get_bool("dont_add_cents") is True
        assert db.get_setting("shipping_preference") == "dearest"

    def test_all_settings_merges_defaults(self, temp_db):
        s = db.all_settings()
        assert "default_strategy" in s and "poll_final_seconds" in s

    def test_get_int_handles_bad_value(self, temp_db):
        db.set_setting("snipe_seconds", "not-a-number")
        assert db.get_int("snipe_seconds", 99) == 99


class TestMigrations:
    def test_init_db_records_alembic_revision(self, temp_db):
        inspector = inspect(db.engine())
        assert inspector.has_table("alembic_version")
        with Session(db.engine()) as session:
            rev = session.exec(text("SELECT version_num FROM alembic_version")).one()
        assert rev[0] == "0001_initial_schema"


class TestJobs:
    def _make(self):
        return db.create_job(
            listing_id="123", url="https://x/listing/123", title="T",
            strategy="fast", max_bid="50.00", end_date="2030-01-01T00:00:00+00:00",
            current_price="1.00", options={"shipping_choice": "4"},
        )

    def test_create_and_get(self, temp_db):
        jid = self._make()
        job = db.get_job(jid)
        assert job["listing_id"] == "123"
        assert job["status"] == "scheduled"
        assert json.loads(job["options"])["shipping_choice"] == "4"

    def test_update(self, temp_db):
        jid = self._make()
        db.update_job(jid, status="active", current_price="2.00", is_leader=1)
        job = db.get_job(jid)
        assert job["status"] == "active"
        assert job["current_price"] == "2.00"
        assert job["is_leader"] == 1

    def test_active_jobs_filter(self, temp_db):
        a = self._make()
        b = self._make()
        db.update_job(b, status="won")
        ids = {j["id"] for j in db.active_jobs()}
        assert a in ids and b not in ids

    def test_list_jobs_all(self, temp_db):
        self._make()
        self._make()
        assert len(db.list_jobs()) == 2

    def test_delete_removes_job_and_logs(self, temp_db):
        jid = self._make()
        db.log(jid, "info", "hello")
        db.delete_job(jid)
        assert db.get_job(jid) is None
        assert db.job_logs(jid) == []


class TestLogs:
    def test_job_logs_ordering(self, temp_db):
        jid = db.create_job(
            listing_id="1", url="u", title="t", strategy="fast",
            max_bid="1", end_date=None, current_price=None, options={},
        )
        db.log(jid, "info", "first")
        db.log(jid, "warn", "second")
        logs = db.job_logs(jid)
        assert [l["message"] for l in logs] == ["second", "first"]  # newest first
        assert logs[0]["level"] == "warn"

    def test_recent_logs(self, temp_db):
        db.log(None, "info", "system event")
        recent = db.recent_logs(10)
        assert any(l["message"] == "system event" for l in recent)
