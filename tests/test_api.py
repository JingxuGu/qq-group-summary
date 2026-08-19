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
    app = create_app(
        database=database, ingestion=IngestionService(database), ingest_token="secret",
        allow_user_writes=True, update_delivery_schedule=scheduled.append,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/v1/subscription", json={"email": "reader@example.com", "daily_time": "21:15"}
        )
        saved = await client.get("/api/v1/subscription")
    assert response.status_code == 200
    assert saved.json() == {"email": "reader@example.com", "daily_time": "21:15"}
    assert scheduled == ["21:15"]


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
    assert status_response.json()["login_url"] == "http://127.0.0.1:6099/webui"


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
        developer_config = await client.get("/api/v1/config")
    assert page.status_code == 200
    assert "Connect your QQ account" in page.text
    assert "API key" not in page.text
    assert developer_config.status_code == 404
