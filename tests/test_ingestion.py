from datetime import datetime, timezone

import pytest

from app.config import GroupConfig
from app.ingestion.normalize import normalize_segments
from app.ingestion.service import IngestionService
from app.models import GroupType, IncomingMessage, MessageSegment
from app.storage.database import Database, UnknownGroupError


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "test.db")
    database.migrate()
    database.sync_groups((GroupConfig("100", "课程群", GroupType.COURSE),))
    return database


def message(message_id="m1", text="周三交", group_id="100"):
    return IncomingMessage(
        qq_message_id=message_id,
        qq_group_id=group_id,
        sender_id="u1",
        sender_name="张三",
        sent_at=datetime(2026, 8, 19, 2, 0, tzinfo=timezone.utc),
        segments=(MessageSegment("text", {"text": text}),),
    )


def test_message_is_stored_and_duplicate_is_idempotent(database):
    service = IngestionService(database)
    first = service.ingest(message())
    second = service.ingest(message())
    assert first.duplicate is False
    assert second.duplicate is True
    assert second.message_id == first.message_id
    assert database.pending_messages(1)[0].text == "周三交"


def test_short_informative_message_is_not_filtered(database):
    result = IngestionService(database).ingest(message(text="302教室"))
    assert result.is_noise is False


def test_fixed_chatter_is_marked_but_still_stored(database):
    result = IngestionService(database).ingest(message(text="哈哈"))
    assert result.is_noise is True
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM messages").fetchone()[0] == 1


def test_unknown_group_is_rejected(database):
    with pytest.raises(UnknownGroupError):
        IngestionService(database).ingest(message(group_id="999"))


def test_file_and_link_metadata_are_preserved():
    normalized = normalize_segments((
        MessageSegment("text", {"text": "资料在这里"}),
        MessageSegment("file", {"name": "lecture.pdf"}),
        MessageSegment("link", {"title": "课程主页", "url": "https://example.com"}),
    ))
    assert normalized.attachment_title == "lecture.pdf"
    assert normalized.url == "https://example.com"
    assert "资料在这里" in normalized.text
    assert "lecture.pdf" in normalized.text
