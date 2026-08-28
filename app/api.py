from __future__ import annotations

import base64
import secrets
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.service import IngestionService
from app.models import GroupType, IncomingMessage, MessageSegment
from app.qq_login import NapCatWebUIGateway, QQLoginUnavailable
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


class BridgeGroupPayload(BaseModel):
    qq_group_id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class BridgeGroupsPayload(BaseModel):
    groups: list[BridgeGroupPayload]


class GroupActivityPayload(BridgeGroupPayload):
    occurred_at: datetime


class GroupSelectionPayload(BaseModel):
    qq_group_id: str = Field(min_length=1)
    type: GroupType


class GroupSelectionsPayload(BaseModel):
    groups: list[GroupSelectionPayload]


def create_app(
    *,
    database: Database,
    ingestion: IngestionService,
    ingest_token: str,
    scheduler_running: Callable[[], bool] = lambda: True,
    lifespan: Any = None,
    web_directory: Path | None = None,
    napcat_webui_url: str = "http://127.0.0.1:6099/webui",
    qq_login_gateway: NapCatWebUIGateway | None = None,
    allow_user_writes: bool = False,
    update_delivery_schedule: Callable[[str], None] | None = None,
    summarize_now: Callable[[], Awaitable[list[int]]] | None = None,
    send_subscription_confirmation: Callable[[str, str], Awaitable[None]] | None = None,
    mailjet_webhook_username: str = "",
    mailjet_webhook_password: str = "",
) -> FastAPI:
    app = FastAPI(title="QQ Group Summary", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def prevent_stale_web_ui(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/ui" or request.url.path.startswith("/ui/assets/"):
            response.headers["Cache-Control"] = "no-store"
        return response

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

    @app.post("/api/v1/summaries/run")
    async def run_summaries() -> dict[str, object]:
        if not allow_user_writes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Immediate summaries require localhost access")
        if summarize_now is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Summary service is unavailable")
        try:
            batch_ids = await summarize_now()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Summary generation failed. Messages remain pending and can be retried.",
            ) from exc
        return {"status": "completed", "created": len(batch_ids)}

    @app.get("/api/v1/messages")
    def messages(group_id: str | None = None, q: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, object]:
        items, total = database.message_feed(
            qq_group_id=group_id, query=q, limit=min(max(limit, 1), 200), offset=max(offset, 0)
        )
        return {"items": items, "total": total, "offset": max(offset, 0)}

    @app.get("/api/v1/messages/groups")
    def message_groups(q: str | None = None) -> dict[str, object]:
        return {"items": database.message_groups(query=q)}

    @app.get("/api/v1/subscription")
    def subscription() -> dict[str, str]:
        return {
            "email": database.get_setting("subscription_email") or "",
            "daily_time": database.get_setting("daily_email_time") or "22:30",
        }

    @app.put("/api/v1/subscription")
    async def update_subscription(payload: SubscriptionPayload) -> dict[str, object]:
        if not allow_user_writes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Subscription changes require localhost access")
        if send_subscription_confirmation is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Email confirmation service is unavailable")
        try:
            await send_subscription_confirmation(payload.email, payload.daily_time)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not send the confirmation email. Subscription was not changed.",
            ) from exc
        database.set_setting("subscription_email", payload.email)
        database.set_setting("daily_email_time", payload.daily_time)
        if update_delivery_schedule is not None:
            update_delivery_schedule(payload.daily_time)
        return {"status": "saved", "confirmation_sent": True}

    @app.post("/api/v1/mailjet/events")
    async def receive_mailjet_events(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict[str, int | str]:
        if not mailjet_webhook_username or not mailjet_webhook_password:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Mailjet webhook is not configured",
            )
        expected = "Basic " + base64.b64encode(
            f"{mailjet_webhook_username}:{mailjet_webhook_password}".encode("utf-8")
        ).decode("ascii")
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
        try:
            raw = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON") from exc
        events = raw if isinstance(raw, list) else [raw]
        if not events or not all(isinstance(item, dict) for item in events):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid Mailjet event")
        inserted = database.record_mailjet_events(events)
        return {"status": "accepted", "received": len(events), "stored": inserted}

    @app.get("/api/v1/qq/status")
    def qq_status() -> dict[str, object]:
        value = database.get_setting("qq_connection_status")
        status_data = json.loads(value) if value else {"connected": False, "qq_id": "", "nickname": "", "platform": "onebot"}
        return status_data

    @app.get("/api/v1/qq/login")
    def qq_login() -> dict[str, object]:
        if qq_login_gateway is None or not qq_login_gateway.configured:
            return {
                "available": False, "logged_in": False, "offline": True,
                "qr_code": "", "error": "QQ login is not configured on this server",
            }
        try:
            return qq_login_gateway.snapshot()
        except QQLoginUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    @app.post("/api/v1/qq/login/refresh")
    def refresh_qq_login() -> dict[str, object]:
        if not allow_user_writes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="QQ login requires localhost access")
        if qq_login_gateway is None or not qq_login_gateway.configured:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="QQ login is not configured")
        try:
            return qq_login_gateway.refresh_qr_code()
        except QQLoginUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    @app.get("/api/v1/qq/groups")
    def qq_groups() -> dict[str, object]:
        return {"items": database.available_groups()}

    @app.put("/api/v1/qq/groups")
    def update_qq_groups(payload: GroupSelectionsPayload) -> dict[str, object]:
        if not allow_user_writes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Group changes require localhost access")
        try:
            database.replace_group_subscriptions(
                [(item.qq_group_id, item.type) for item in payload.groups]
            )
        except (UnknownGroupError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"status": "saved", "selected": len(payload.groups)}

    @app.post("/api/v1/bridge/status")
    def update_qq_status(payload: QQStatusPayload, authorization: str | None = Header(default=None)) -> dict[str, str]:
        expected = f"Bearer {ingest_token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
        data = payload.model_dump() | {"updated_at": datetime.now().astimezone().isoformat()}
        database.set_setting("qq_connection_status", json.dumps(data, ensure_ascii=False))
        return {"status": "saved"}

    @app.post("/api/v1/bridge/groups")
    def update_group_catalog(
        payload: BridgeGroupsPayload, authorization: str | None = Header(default=None)
    ) -> dict[str, object]:
        expected = f"Bearer {ingest_token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
        database.sync_available_groups([item.model_dump() for item in payload.groups])
        return {"status": "saved", "groups": len(payload.groups)}

    @app.post("/api/v1/bridge/activity")
    def update_group_activity(
        payload: GroupActivityPayload, authorization: str | None = Header(default=None)
    ) -> dict[str, object]:
        expected = f"Bearer {ingest_token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
        selected = database.record_group_activity(
            payload.qq_group_id, payload.name, payload.occurred_at
        )
        return {"status": "saved", "selected": selected}

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
