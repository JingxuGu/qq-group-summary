import base64
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.api import create_app
from app.config import GroupConfig
from app.ingestion.service import IngestionService
from app.models import GroupType
from app.storage.database import Database


def build_client(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    database.sync_groups((GroupConfig("100", "课程群", GroupType.COURSE),))
    app = create_app(database=database, ingestion=IngestionService(database), ingest_token="secret")
    return app, database


def payload(group_id="100"):
    return {
        "qq_message_id": "m1", "qq_group_id": group_id, "sender_id": "u1", "sender_name": "甲",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "segments": [{"type": "text", "data": {"text": "周三交"}}],
    }


@pytest.mark.asyncio
async def test_api_requires_bearer_token(tmp_path):
    app, _ = build_client(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/v1/messages", json=payload())).status_code == 401


@pytest.mark.asyncio
async def test_api_stores_and_reports_duplicate(tmp_path):
    app, database = build_client(tmp_path)
    headers = {"Authorization": "Bearer secret"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/messages", json=payload(), headers=headers)
        second = await client.post("/api/v1/messages", json=payload(), headers=headers)
    assert first.json()["status"] == "stored"
    assert second.json()["status"] == "duplicate"
    assert len(database.pending_messages(1)) == 1


@pytest.mark.asyncio
async def test_message_groups_report_independent_history_counts(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    database.sync_groups((
        GroupConfig("100", "Course chat", GroupType.COURSE),
        GroupConfig("200", "Research chat", GroupType.ACADEMIC),
    ))
    app = create_app(database=database, ingestion=IngestionService(database), ingest_token="secret")
    headers = {"Authorization": "Bearer secret"}
    first = payload("100")
    second = payload("200") | {"qq_message_id": "m2", "sender_name": "乙"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/messages", json=first, headers=headers)
        await client.post("/api/v1/messages", json=second, headers=headers)
        groups = await client.get("/api/v1/messages/groups")
        filtered = await client.get("/api/v1/messages/groups", params={"q": "乙"})
    assert {item["qq_group_id"]: item["message_count"] for item in groups.json()["items"]} == {"100": 1, "200": 1}
    assert [item["qq_group_id"] for item in filtered.json()["items"]] == ["200"]


@pytest.mark.asyncio
async def test_api_rejects_unconfigured_group(tmp_path):
    app, _ = build_client(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/messages", json=payload("999"), headers={"Authorization": "Bearer secret"}
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_user_can_update_email_subscription_on_localhost(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    scheduled = []
    confirmations = []

    async def send_confirmation(email, daily_time):
        confirmations.append((email, daily_time))

    app = create_app(
        database=database, ingestion=IngestionService(database), ingest_token="secret",
        allow_user_writes=True, update_delivery_schedule=scheduled.append,
        send_subscription_confirmation=send_confirmation,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/v1/subscription", json={"email": "reader@example.com", "daily_time": "21:15"}
        )
        saved = await client.get("/api/v1/subscription")
    assert response.status_code == 200
    assert response.json() == {"status": "saved", "confirmation_sent": True}
    assert saved.json() == {"email": "reader@example.com", "daily_time": "21:15"}
    assert scheduled == ["21:15"]
    assert confirmations == [("reader@example.com", "21:15")]


@pytest.mark.asyncio
async def test_failed_confirmation_does_not_change_subscription(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    database.set_setting("subscription_email", "old@example.com")
    database.set_setting("daily_email_time", "20:00")
    scheduled = []

    async def fail_confirmation(email, daily_time):
        raise RuntimeError("smtp unavailable")

    app = create_app(
        database=database, ingestion=IngestionService(database), ingest_token="secret",
        allow_user_writes=True, update_delivery_schedule=scheduled.append,
        send_subscription_confirmation=fail_confirmation,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/v1/subscription", json={"email": "new@example.com", "daily_time": "21:15"}
        )
        saved = await client.get("/api/v1/subscription")
    assert response.status_code == 502
    assert saved.json() == {"email": "old@example.com", "daily_time": "20:00"}
    assert scheduled == []


@pytest.mark.asyncio
async def test_mailjet_events_require_auth_and_are_idempotent(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    app = create_app(
        database=database,
        ingestion=IngestionService(database),
        ingest_token="secret",
        mailjet_webhook_username="mailjet-hook",
        mailjet_webhook_password="hook-secret",
    )
    event = {
        "event": "bounce",
        "time": 1787880000,
        "email": "reader@example.com",
        "MessageID": 123,
        "CustomID": "daily-123",
    }
    token = base64.b64encode(b"mailjet-hook:hook-secret").decode("ascii")
    headers = {"Authorization": f"Basic {token}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.post("/api/v1/mailjet/events", json=event)
        first = await client.post("/api/v1/mailjet/events", json=event, headers=headers)
        duplicate = await client.post("/api/v1/mailjet/events", json=event, headers=headers)
    assert denied.status_code == 401
    assert first.json() == {"status": "accepted", "received": 1, "stored": 1}
    assert duplicate.json() == {"status": "accepted", "received": 1, "stored": 0}
    with database.connect() as connection:
        saved = connection.execute("SELECT * FROM mail_events").fetchall()
    assert len(saved) == 1
    assert saved[0]["event_type"] == "bounce"
    assert saved[0]["delivery_key"] == "daily-123"


@pytest.mark.asyncio
async def test_user_can_force_pending_summaries_on_localhost(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    calls = []

    async def summarize_now():
        calls.append("run")
        return [10, 11]

    app = create_app(
        database=database, ingestion=IngestionService(database), ingest_token="secret",
        allow_user_writes=True, summarize_now=summarize_now,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/summaries/run")
    assert response.json() == {"status": "completed", "created": 2}
    assert calls == ["run"]


@pytest.mark.asyncio
async def test_immediate_summary_is_not_available_on_nonlocal_service(tmp_path):
    app, _ = build_client(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/summaries/run")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_bridge_reports_connected_qq_without_exposing_write_endpoint(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    app = create_app(
        database=database, ingestion=IngestionService(database), ingest_token="secret",
        napcat_webui_url="http://127.0.0.1:6099/webui",
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post("/api/v1/bridge/status", json={"connected": True, "qq_id": "123"})
        accepted = await client.post(
            "/api/v1/bridge/status", headers={"Authorization": "Bearer secret"},
            json={"connected": True, "qq_id": "123", "nickname": "Reader", "platform": "onebot"},
        )
        status_response = await client.get("/api/v1/qq/status")
    assert denied.status_code == 401
    assert accepted.status_code == 200
    assert status_response.json()["qq_id"] == "123"
    assert "login_url" not in status_response.json()


@pytest.mark.asyncio
async def test_qq_login_is_proxied_without_exposing_server_credential(tmp_path):
    class FakeGateway:
        configured = True

        def snapshot(self):
            return {"available": True, "logged_in": False, "offline": False, "qr_code": "https://example.com/qr", "error": ""}

        def refresh_qr_code(self):
            return {"available": True, "logged_in": False, "offline": False, "qr_code": "https://example.com/new-qr", "error": ""}

    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    app = create_app(
        database=database, ingestion=IngestionService(database), ingest_token="secret",
        qq_login_gateway=FakeGateway(), allow_user_writes=True,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        initial = await client.get("/api/v1/qq/login")
        refreshed = await client.post("/api/v1/qq/login/refresh")
    assert initial.json()["qr_code"] == "https://example.com/qr"
    assert refreshed.json()["qr_code"] == "https://example.com/new-qr"
    assert "token" not in initial.text.lower()


@pytest.mark.asyncio
async def test_user_selects_synced_groups_sorted_by_latest_activity(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    app = create_app(
        database=database, ingestion=IngestionService(database), ingest_token="secret",
        allow_user_writes=True,
    )
    headers = {"Authorization": "Bearer secret"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        synced = await client.post(
            "/api/v1/bridge/groups", headers=headers,
            json={"groups": [
                {"qq_group_id": "100", "name": "Course chat"},
                {"qq_group_id": "200", "name": "Research chat"},
            ]},
        )
        older = await client.post(
            "/api/v1/bridge/activity", headers=headers,
            json={"qq_group_id": "100", "name": "Course chat", "occurred_at": "2026-08-19T10:00:00Z"},
        )
        newer = await client.post(
            "/api/v1/bridge/activity", headers=headers,
            json={"qq_group_id": "200", "name": "Research chat", "occurred_at": "2026-08-19T11:00:00Z"},
        )
        choices = await client.get("/api/v1/qq/groups")
        saved = await client.put(
            "/api/v1/qq/groups",
            json={"groups": [{"qq_group_id": "200", "type": "academic"}]},
        )
        selected_activity = await client.post(
            "/api/v1/bridge/activity", headers=headers,
            json={"qq_group_id": "200", "name": "Research chat", "occurred_at": "2026-08-19T12:00:00Z"},
        )
    assert synced.status_code == 200
    assert older.json()["selected"] is False
    assert newer.json()["selected"] is False
    assert [item["qq_group_id"] for item in choices.json()["items"]] == ["200", "100"]
    assert saved.json() == {"status": "saved", "selected": 1}
    assert selected_activity.json()["selected"] is True
    assert database.configured_groups()[0]["type"] == "academic"


@pytest.mark.asyncio
async def test_activity_placeholder_does_not_replace_a_synced_group_name(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    app = create_app(
        database=database, ingestion=IngestionService(database), ingest_token="secret",
    )
    headers = {"Authorization": "Bearer secret"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/api/v1/bridge/groups", headers=headers,
            json={"groups": [{"qq_group_id": "200", "name": "Research chat"}]},
        )
        await client.post(
            "/api/v1/bridge/activity", headers=headers,
            json={"qq_group_id": "200", "name": "200", "occurred_at": "2026-08-19T12:00:00Z"},
        )
        groups = await client.get("/api/v1/qq/groups")
    assert groups.json()["items"][0]["name"] == "Research chat"


@pytest.mark.asyncio
async def test_web_ui_is_user_facing_and_has_no_developer_config_endpoint(tmp_path):
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    web_directory = Path(__file__).resolve().parents[1] / "web"
    app = create_app(
        database=database, ingestion=IngestionService(database), ingest_token="secret",
        web_directory=web_directory,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/ui")
        script = await client.get("/ui/assets/app.js?v=test")
        developer_config = await client.get("/api/v1/config")
    assert page.status_code == 200
    assert "Connect your QQ account" in page.text
    assert "Show login QR" in page.text
    assert "Search by group name or QQ number" in page.text
    assert 'aria-label="Available QQ groups"' in page.text
    assert '<details class="message-group-card">' in page.text
    assert "Open this group to load its saved history" in page.text
    assert "NapCat" not in page.text
    assert "API key" not in page.text
    assert page.headers["cache-control"] == "no-store"
    assert script.headers["cache-control"] == "no-store"
    assert developer_config.status_code == 404
