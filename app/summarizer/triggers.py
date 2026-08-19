from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from app.config import SummaryPolicy
from app.models import StoredMessage


class TriggerReason(StrEnum):
    MESSAGE_COUNT = "message_count"
    IDLE = "idle"
    MAX_WINDOW = "max_window"
    FORCED = "forced"


def trigger_reason(
    messages: list[StoredMessage], policy: SummaryPolicy, *, now: datetime, force: bool = False
) -> TriggerReason | None:
    if not messages:
        return None
    if force:
        return TriggerReason.FORCED
    if len(messages) >= policy.max_messages:
        return TriggerReason.MESSAGE_COUNT
    if now - messages[-1].sent_at >= timedelta(minutes=policy.idle_minutes):
        return TriggerReason.IDLE
    if now - messages[0].sent_at >= timedelta(hours=policy.max_window_hours):
        return TriggerReason.MAX_WINDOW
    return None

