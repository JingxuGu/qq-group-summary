from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class GroupType(StrEnum):
    COURSE = "course"
    ACADEMIC = "academic"
    CASUAL = "casual"


@dataclass(frozen=True, slots=True)
class MessageSegment:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    qq_message_id: str
    qq_group_id: str
    sender_id: str
    sender_name: str
    sent_at: datetime
    segments: tuple[MessageSegment, ...]
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    message_type: str
    text: str
    attachment_title: str | None
    url: str | None
    segments_json: str


@dataclass(frozen=True, slots=True)
class IngestResult:
    message_id: int
    duplicate: bool
    is_noise: bool


@dataclass(frozen=True, slots=True)
class StoredMessage:
    id: int
    qq_message_id: str
    group_id: int
    qq_group_id: str
    group_name: str
    group_type: GroupType
    sender_id: str
    sender_name: str
    message_type: str
    text: str
    attachment_title: str | None
    url: str | None
    sent_at: datetime
    received_at: datetime

