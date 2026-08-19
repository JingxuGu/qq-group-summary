from __future__ import annotations

import secrets
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.service import IngestionService
from app.models import IncomingMessage, MessageSegment
from app.storage.database import Database, UnknownGroupError


class SegmentPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)


class MessagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qq_message_id: str = Field(min_length=1)
    qq_group_id: str = Field(min_length=1)
    sender_id: str = Field(min_length=1)
    sender_name: str = Field(min_length=1)
    sent_at: datetime
    received_at: datetime | None = None
    segments: list[SegmentPayload] = Field(min_length=1)


class SubscriptionPayload(BaseModel):
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    daily_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class QQStatusPayload(BaseModel):
    connected: bool
    qq_id: str = ""
    nickname: str = ""
    platform: str = "onebot"


def create_app(
    *,
    database: Database,
    ingestion: IngestionService,
    ingest_token: str,
    scheduler_running: Callable[[], bool] = lambda: True,
    lifespan: Any = None,
    web_directory: Path | None = None,
    napcat_webui_url: str = "http://127.0.0.1:6099/webui",
    allow_user_writes: bool = False,
    update_delivery_schedule: Callable[[str], None] | None = None,
) -> FastAPI:
    app = FastAPI(title="QQ Group Summary", version="0.1.0", lifespan=lifespan)

    if web_directory is not None:
        app.mount("/ui/assets", StaticFiles(directory=web_directory / "assets"), name="ui-assets")

        @app.get("/", include_in_schema=False)
        def root() -> RedirectResponse:
            return RedirectResponse("/ui")

        @app.get("/ui", include_in_schema=False)
        def web_ui() -> FileResponse:
            return FileResponse(web_directory / "index.html")

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        if not database.is_writable() or not scheduler_running():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="service not ready")
        return {"status": "ready"}

    @app.get("/api/v1/dashboard")
    def dashboard() -> dict[str, object]:
        return database.dashboard_snapshot()

    @app.get("/api/v1/summaries")
    def summaries(group_type: str | None = None, group_id: str | None = None, limit: int = 50) -> dict[str, object]:
        return {"items": database.summary_feed(group_type=group_type, qq_group_id=group_id, limit=min(max(limit, 1), 200))}

    @app.get("/api/v1/messages")
    def messages(group_id: str | None = None, q: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, object]:
        items, total = database.message_feed(
            qq_group_id=group_id, query=q, limit=min(max(limit, 1), 200), offset=max(offset, 0)
        )
        return {"items": items, "total": total, "offset": max(offset, 0)}

    @app.get("/api/v1/subscription")
    def subscription() -> dict[str, str]:
        return {
            "email": database.get_setting("subscription_email") or "",
            "daily_time": database.get_setting("daily_email_time") or "22:30",
        }

    @app.put("/api/v1/subscription")
    def update_subscription(payload: SubscriptionPayload) -> dict[str, object]:
        if not allow_user_writes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Subscription changes require localhost access")
        database.set_setting("subscription_email", payload.email)
        database.set_setting("daily_email_time", payload.daily_time)
        if update_delivery_schedule is not None:
            update_delivery_schedule(payload.daily_time)
        return {"status": "saved"}

    @app.get("/api/v1/qq/status")
    def qq_status() -> dict[str, object]:
        value = database.get_setting("qq_connection_status")
        status_data = json.loads(value) if value else {"connected": False, "qq_id": "", "nickname": "", "platform": "onebot"}
        status_data["login_url"] = napcat_webui_url
        return status_data

    @app.post("/api/v1/bridge/status")
    def update_qq_status(payload: QQStatusPayload, authorization: str | None = Header(default=None)) -> dict[str, str]:
        expected = f"Bearer {ingest_token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
        data = payload.model_dump() | {"updated_at": datetime.now().astimezone().isoformat()}
        database.set_setting("qq_connection_status", json.dumps(data, ensure_ascii=False))
        return {"status": "saved"}

    @app.post("/api/v1/messages")
    def receive_message(payload: MessagePayload, authorization: str | None = Header(default=None)) -> dict[str, object]:
        expected = f"Bearer {ingest_token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
        message = IncomingMessage(
            qq_message_id=payload.qq_message_id,
            qq_group_id=payload.qq_group_id,
            sender_id=payload.sender_id,
            sender_name=payload.sender_name,
            sent_at=payload.sent_at,
            received_at=payload.received_at or datetime.now().astimezone(),
            segments=tuple(MessageSegment(item.type, item.data) for item in payload.segments),
        )
        try:
            result = ingestion.ingest(message)
        except UnknownGroupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not configured") from exc
        except sqlite3.OperationalError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database temporarily unavailable") from exc
        return {
            "status": "duplicate" if result.duplicate else "stored",
            "message_id": result.message_id,
            "is_noise": result.is_noise,
        }

    return app
