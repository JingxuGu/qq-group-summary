from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from app.config import GroupConfig
from app.models import GroupType, IncomingMessage, IngestResult, NormalizedMessage, StoredMessage


SCHEMA_VERSION = 3


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.transaction() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    qq_group_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL CHECK(type IN ('course','academic','casual')),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    max_messages INTEGER,
                    idle_minutes INTEGER,
                    max_window_hours INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    delivery_key TEXT NOT NULL UNIQUE,
                    window_start TEXT,
                    window_end TEXT NOT NULL,
                    email_subject TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('preparing','sent','failed')),
                    sent_at TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mail_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    provider TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_time TEXT,
                    recipient TEXT,
                    message_id TEXT,
                    delivery_key TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mail_events_delivery
                    ON mail_events(delivery_key, event_time);
                CREATE TABLE IF NOT EXISTS summary_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL REFERENCES groups(id),
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    message_count INTEGER NOT NULL,
                    summary_json TEXT NOT NULL,
                    knowledge_tags_json TEXT NOT NULL DEFAULT '[]',
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    last_error TEXT,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent')),
                    delivery_id INTEGER REFERENCES deliveries(id),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    qq_message_id TEXT NOT NULL,
                    group_id INTEGER NOT NULL REFERENCES groups(id),
                    sender_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    segments_json TEXT NOT NULL,
                    attachment_title TEXT,
                    url TEXT,
                    sent_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    is_noise INTEGER NOT NULL DEFAULT 0,
                    summary_batch_id INTEGER REFERENCES summary_batches(id),
                    created_at TEXT NOT NULL,
                    UNIQUE(group_id, qq_message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_pending
                    ON messages(group_id, summary_batch_id, is_noise, sent_at);
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_group_id INTEGER NOT NULL REFERENCES groups(id),
                    source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
                    first_seen_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    original_text TEXT NOT NULL,
                    latest_update_text TEXT,
                    dedup_key TEXT NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent')),
                    delivery_id INTEGER REFERENCES deliveries(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_notifications_dedup ON notifications(dedup_key);
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            group_columns = {row[1] for row in db.execute("PRAGMA table_info(groups)").fetchall()}
            if "last_message_at" not in group_columns:
                db.execute("ALTER TABLE groups ADD COLUMN last_message_at TEXT")
            if "available" not in group_columns:
                db.execute("ALTER TABLE groups ADD COLUMN available INTEGER NOT NULL DEFAULT 0")
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _utc_now()),
            )

    def sync_available_groups(self, groups: Sequence[dict[str, str | None]]) -> None:
        """Upsert the QQ account's group catalog without changing user subscriptions."""
        now = _utc_now()
        with self.transaction() as db:
            db.execute("UPDATE groups SET available=0,updated_at=? WHERE available=1", (now,))
            for group in groups:
                qq_group_id = str(group["qq_group_id"])
                name = str(group.get("name") or qq_group_id)
                last_message_at = group.get("last_message_at")
                db.execute(
                    """
                    INSERT INTO groups(
                      qq_group_id,name,type,enabled,last_message_at,available,created_at,updated_at
                    ) VALUES(?,?,'casual',0,?,1,?,?)
                    ON CONFLICT(qq_group_id) DO UPDATE SET
                      name=excluded.name,
                      last_message_at=COALESCE(excluded.last_message_at,groups.last_message_at),
                      available=1,
                      updated_at=excluded.updated_at
                    """,
                    (qq_group_id, name, last_message_at, now, now),
                )

    def record_group_activity(self, qq_group_id: str, name: str, occurred_at: datetime) -> bool:
        """Record metadata for sorting without storing content from unsubscribed groups."""
        now = _utc_now()
        occurred = _iso(occurred_at)
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO groups(
                  qq_group_id,name,type,enabled,last_message_at,available,created_at,updated_at
                ) VALUES(?,?,'casual',0,?,1,?,?)
                ON CONFLICT(qq_group_id) DO UPDATE SET
                  name=CASE
                    WHEN excluded.name=excluded.qq_group_id THEN groups.name
                    ELSE excluded.name
                  END,
                  last_message_at=excluded.last_message_at,
                  available=1,
                  updated_at=excluded.updated_at
                """,
                (qq_group_id, name or qq_group_id, occurred, now, now),
            )
            row = db.execute(
                "SELECT enabled FROM groups WHERE qq_group_id=?", (qq_group_id,)
            ).fetchone()
        return bool(row["enabled"])

    def available_groups(self) -> list[dict[str, object]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT qq_group_id,name,type,enabled,last_message_at
                FROM groups WHERE available=1
                ORDER BY last_message_at IS NULL,last_message_at DESC,name COLLATE NOCASE,qq_group_id
                """
            ).fetchall()
        return [dict(row) | {"enabled": bool(row["enabled"])} for row in rows]

    def replace_group_subscriptions(self, groups: Sequence[tuple[str, GroupType]]) -> None:
        ids = [group_id for group_id, _ in groups]
        if len(ids) != len(set(ids)):
            raise ValueError("group ids must be unique")
        with self.transaction() as db:
            if ids:
                placeholders = ",".join("?" for _ in ids)
                found = int(db.execute(
                    f"SELECT count(*) FROM groups WHERE available=1 AND qq_group_id IN ({placeholders})", ids
                ).fetchone()[0])
                if found != len(ids):
                    raise UnknownGroupError("one or more groups are unavailable")
            db.execute("UPDATE groups SET enabled=0,updated_at=? WHERE enabled=1", (_utc_now(),))
            for qq_group_id, group_type in groups:
                db.execute(
                    "UPDATE groups SET enabled=1,type=?,updated_at=? WHERE qq_group_id=?",
                    (group_type.value, _utc_now(), qq_group_id),
                )

    def sync_groups(self, groups: Sequence[GroupConfig]) -> None:
        now = _utc_now()
        with self.transaction() as db:
            for group in groups:
                db.execute(
                    """
                    INSERT INTO groups(qq_group_id,name,type,enabled,max_messages,idle_minutes,max_window_hours,available,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,1,?,?)
                    ON CONFLICT(qq_group_id) DO UPDATE SET
                      name=excluded.name,type=excluded.type,enabled=excluded.enabled,
                      max_messages=excluded.max_messages,idle_minutes=excluded.idle_minutes,
                      max_window_hours=excluded.max_window_hours,available=1,updated_at=excluded.updated_at
                    """,
                    (group.id, group.name, group.type.value, int(group.enabled), group.max_messages,
                     group.idle_minutes, group.max_window_hours, now, now),
                )

    def insert_message(self, message: IncomingMessage, normalized: NormalizedMessage, *, is_noise: bool) -> IngestResult:
        with self.transaction() as db:
            group = db.execute(
                "SELECT id, enabled FROM groups WHERE qq_group_id = ?", (message.qq_group_id,)
            ).fetchone()
            if group is None or not group["enabled"]:
                raise UnknownGroupError(message.qq_group_id)
            existing = db.execute(
                "SELECT id, is_noise FROM messages WHERE group_id = ? AND qq_message_id = ?",
                (group["id"], message.qq_message_id),
            ).fetchone()
            if existing:
                return IngestResult(existing["id"], True, bool(existing["is_noise"]))
            cursor = db.execute(
                """
                INSERT INTO messages(
                  qq_message_id,group_id,sender_id,sender_name,message_type,text,segments_json,
                  attachment_title,url,sent_at,received_at,is_noise,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (message.qq_message_id, group["id"], message.sender_id, message.sender_name,
                 normalized.message_type, normalized.text, normalized.segments_json,
                 normalized.attachment_title, normalized.url, _iso(message.sent_at),
                 _iso(message.received_at), int(is_noise), _utc_now()),
            )
            return IngestResult(int(cursor.lastrowid), False, is_noise)

    def pending_messages(self, group_id: int) -> list[StoredMessage]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT m.*, g.qq_group_id, g.name AS group_name, g.type AS group_type
                FROM messages m JOIN groups g ON g.id=m.group_id
                WHERE m.group_id=? AND m.summary_batch_id IS NULL AND m.is_noise=0
                ORDER BY m.sent_at, m.id
                """,
                (group_id,),
            ).fetchall()
        return [_stored_message(row) for row in rows]

    def configured_groups(self) -> list[sqlite3.Row]:
        with self.connect() as db:
            return list(db.execute("SELECT * FROM groups WHERE enabled=1 AND available=1 ORDER BY id").fetchall())

    def get_group(self, group_id: int) -> sqlite3.Row:
        with self.connect() as db:
            row = db.execute("SELECT * FROM groups WHERE id=? AND enabled=1", (group_id,)).fetchone()
        if row is None:
            raise UnknownGroupError(str(group_id))
        return row

    def save_summary_batch(
        self,
        *,
        group_id: int,
        messages: Sequence[StoredMessage],
        summary_json: str,
        tags_json: str,
        provider: str,
        model: str,
        attempts: int,
        notifications: Sequence[dict[str, str | None]] = (),
    ) -> int:
        if not messages:
            raise ValueError("cannot save an empty summary batch")
        message_ids = [message.id for message in messages]
        placeholders = ",".join("?" for _ in message_ids)
        with self.transaction() as db:
            available = db.execute(
                f"SELECT count(*) FROM messages WHERE id IN ({placeholders}) AND summary_batch_id IS NULL",
                message_ids,
            ).fetchone()[0]
            if available != len(message_ids):
                raise ConcurrentSummaryError("one or more messages were already summarized")
            cursor = db.execute(
                """
                INSERT INTO summary_batches(
                  group_id,started_at,ended_at,message_count,summary_json,knowledge_tags_json,
                  provider,model,attempts,status,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,'pending',?)
                """,
                (group_id, _iso(messages[0].sent_at), _iso(messages[-1].sent_at), len(messages),
                 summary_json, tags_json, provider, model, attempts, _utc_now()),
            )
            batch_id = int(cursor.lastrowid)
            db.execute(
                f"UPDATE messages SET summary_batch_id=? WHERE id IN ({placeholders})",
                [batch_id, *message_ids],
            )
            for notification in notifications:
                self._upsert_notification_in_transaction(
                    db,
                    group_id=group_id,
                    source_qq_message_id=str(notification["source_message_id"]),
                    title=str(notification["title"]),
                    original_text=str(notification["original_text"]),
                    dedup_key=str(notification["dedup_key"]),
                    update_text=notification.get("update_text"),
                )
            return batch_id

    def upsert_notification(
        self,
        *,
        group_id: int,
        source_qq_message_id: str,
        title: str,
        original_text: str,
        dedup_key: str,
        update_text: str | None,
    ) -> int:
        with self.transaction() as db:
            return self._upsert_notification_in_transaction(
                db,
                group_id=group_id,
                source_qq_message_id=source_qq_message_id,
                title=title,
                original_text=original_text,
                dedup_key=dedup_key,
                update_text=update_text,
            )

    def _upsert_notification_in_transaction(
        self,
        db: sqlite3.Connection,
        *,
        group_id: int,
        source_qq_message_id: str,
        title: str,
        original_text: str,
        dedup_key: str,
        update_text: str | None,
    ) -> int:
        normalized_key = " ".join(dedup_key.casefold().split())
        now = _utc_now()
        source = db.execute(
            "SELECT id, sent_at FROM messages WHERE group_id=? AND qq_message_id=?",
            (group_id, source_qq_message_id),
        ).fetchone()
        if source is None:
            raise ValueError(f"notification source message not found: {source_qq_message_id}")
        source_item = {"group_id": group_id, "message_id": source["id"]}
        existing = db.execute(
            "SELECT * FROM notifications WHERE dedup_key=? ORDER BY first_seen_at LIMIT 1",
            (normalized_key,),
        ).fetchone()
        if existing:
            sources = json.loads(existing["sources_json"])
            if source_item not in sources:
                sources.append(source_item)
            is_new_update = bool(update_text and update_text != existing["latest_update_text"])
            next_status = "pending" if is_new_update else existing["status"]
            next_delivery_id = None if is_new_update else existing["delivery_id"]
            db.execute(
                """
                UPDATE notifications SET
                  latest_update_text=COALESCE(?,latest_update_text), sources_json=?,
                  status=?,delivery_id=?,updated_at=?
                WHERE id=?
                """,
                (update_text, json.dumps(sources, ensure_ascii=False), next_status,
                 next_delivery_id, now, existing["id"]),
            )
            return int(existing["id"])
        cursor = db.execute(
            """
            INSERT INTO notifications(
              source_group_id,source_message_id,first_seen_at,title,original_text,
              latest_update_text,dedup_key,sources_json,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?, 'pending',?,?)
            """,
            (group_id, source["id"], source["sent_at"], title, original_text, update_text,
             normalized_key, json.dumps([source_item], ensure_ascii=False), now, now),
        )
        return int(cursor.lastrowid)

    def pending_digest(self) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
        with self.connect() as db:
            batches = list(db.execute(
                """
                SELECT b.*,g.name AS group_name,g.type AS group_type
                FROM summary_batches b JOIN groups g ON g.id=b.group_id
                WHERE b.status='pending' ORDER BY b.started_at,b.id
                """
            ).fetchall())
            notifications = list(db.execute(
                """
                SELECT n.*,g.name AS group_name
                FROM notifications n JOIN groups g ON g.id=n.source_group_id
                WHERE n.status='pending' ORDER BY n.first_seen_at,n.id
                """
            ).fetchall())
        return batches, notifications

    def get_setting(self, key: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        now = _utc_now()
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
                """,
                (key, value, now),
            )

    def set_setting_if_absent(self, key: str, value: str) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO settings(key,value,updated_at) VALUES(?,?,?)",
                (key, value, _utc_now()),
            )

    def summary_feed(
        self, *, group_type: str | None, qq_group_id: str | None, limit: int
    ) -> list[dict[str, object]]:
        clauses = ["1=1"]
        parameters: list[object] = []
        if group_type:
            clauses.append("g.type=?")
            parameters.append(group_type)
        if qq_group_id:
            clauses.append("g.qq_group_id=?")
            parameters.append(qq_group_id)
        parameters.append(limit)
        with self.connect() as db:
            rows = db.execute(
                f"""
                SELECT b.id,b.started_at,b.ended_at,b.message_count,b.summary_json,
                       b.knowledge_tags_json,b.status,b.created_at,
                       g.qq_group_id,g.name AS group_name,g.type AS group_type
                FROM summary_batches b JOIN groups g ON g.id=b.group_id
                WHERE {' AND '.join(clauses)}
                ORDER BY b.ended_at DESC,b.id DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [
            dict(row) | {
                "summary": json.loads(row["summary_json"]),
                "knowledge_tags": json.loads(row["knowledge_tags_json"]),
            }
            for row in rows
        ]

    def message_feed(
        self,
        *,
        qq_group_id: str | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, object]], int]:
        clauses = ["1=1"]
        parameters: list[object] = []
        if qq_group_id:
            clauses.append("g.qq_group_id=?")
            parameters.append(qq_group_id)
        if query:
            clauses.append("(m.text LIKE ? OR m.sender_name LIKE ?)")
            pattern = f"%{query}%"
            parameters.extend([pattern, pattern])
        where = " AND ".join(clauses)
        with self.connect() as db:
            total = int(db.execute(
                f"SELECT count(*) FROM messages m JOIN groups g ON g.id=m.group_id WHERE {where}",
                parameters,
            ).fetchone()[0])
            rows = db.execute(
                f"""
                SELECT m.id,m.qq_message_id,m.sender_id,m.sender_name,m.message_type,m.text,
                       m.attachment_title,m.url,m.sent_at,m.received_at,m.is_noise,
                       m.summary_batch_id,g.qq_group_id,g.name AS group_name,g.type AS group_type
                FROM messages m JOIN groups g ON g.id=m.group_id
                WHERE {where} ORDER BY m.sent_at DESC,m.id DESC LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
        return [dict(row) for row in rows], total

    def message_groups(self, *, query: str | None = None) -> list[dict[str, object]]:
        clauses = ["1=1"]
        parameters: list[object] = []
        if query:
            clauses.append("(m.text LIKE ? OR m.sender_name LIKE ?)")
            pattern = f"%{query}%"
            parameters.extend([pattern, pattern])
        where = " AND ".join(clauses)
        with self.connect() as db:
            rows = db.execute(
                f"""
                SELECT g.qq_group_id,g.name,g.type,COUNT(*) AS message_count,
                       MAX(m.sent_at) AS latest_message_at
                FROM messages m JOIN groups g ON g.id=m.group_id
                WHERE {where}
                GROUP BY g.id,g.qq_group_id,g.name,g.type
                ORDER BY latest_message_at DESC,g.name COLLATE NOCASE,g.qq_group_id
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def create_delivery(self, *, delivery_key: str, window_start: str | None, window_end: str, subject: str) -> int:
        with self.transaction() as db:
            cursor = db.execute(
                """
                INSERT INTO deliveries(delivery_key,window_start,window_end,email_subject,status,created_at)
                VALUES(?,?,?,?,'preparing',?)
                """,
                (delivery_key, window_start, window_end, subject, _utc_now()),
            )
            return int(cursor.lastrowid)

    def record_mailjet_events(self, events: Sequence[dict[str, object]]) -> int:
        inserted = 0
        with self.transaction() as db:
            for event in events:
                payload = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                event_key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                cursor = db.execute(
                    """
                    INSERT OR IGNORE INTO mail_events(
                      event_key,provider,event_type,event_time,recipient,message_id,
                      delivery_key,payload_json,created_at
                    ) VALUES(?,'mailjet',?,?,?,?,?,?,?)
                    """,
                    (
                        event_key,
                        str(event.get("event") or "unknown"),
                        str(event.get("time") or ""),
                        str(event.get("email") or ""),
                        str(event.get("MessageID") or event.get("Message_GUID") or ""),
                        str(event.get("CustomID") or event.get("customcampaign") or ""),
                        payload,
                        _utc_now(),
                    ),
                )
                inserted += max(cursor.rowcount, 0)
        return inserted

    def fail_delivery(self, delivery_id: int, error_message: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE deliveries SET status='failed',error_message=? WHERE id=?",
                (error_message[:1000], delivery_id),
            )

    def complete_delivery(
        self, delivery_id: int, *, window_end: str, batch_ids: Sequence[int], notification_ids: Sequence[int]
    ) -> None:
        with self.transaction() as db:
            if batch_ids:
                placeholders = ",".join("?" for _ in batch_ids)
                db.execute(
                    f"UPDATE summary_batches SET status='sent',delivery_id=? WHERE id IN ({placeholders}) AND status='pending'",
                    [delivery_id, *batch_ids],
                )
            if notification_ids:
                placeholders = ",".join("?" for _ in notification_ids)
                db.execute(
                    f"UPDATE notifications SET status='sent',delivery_id=? WHERE id IN ({placeholders}) AND status='pending'",
                    [delivery_id, *notification_ids],
                )
            now = _utc_now()
            db.execute("UPDATE deliveries SET status='sent',sent_at=?,error_message=NULL WHERE id=?", (now, delivery_id))
            db.execute(
                """
                INSERT INTO settings(key,value,updated_at) VALUES('last_successful_delivery_at',?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
                """,
                (window_end, now),
            )

    def cleanup_summarized_messages(self, *, before: datetime) -> int:
        with self.transaction() as db:
            cursor = db.execute(
                "DELETE FROM messages WHERE sent_at < ? AND summary_batch_id IS NOT NULL",
                (_iso(before),),
            )
            return int(cursor.rowcount)

    def dashboard_snapshot(self) -> dict[str, object]:
        with self.connect() as db:
            counts = db.execute(
                """
                SELECT
                  count(*) AS received,
                  sum(CASE WHEN summary_batch_id IS NULL AND is_noise=0 THEN 1 ELSE 0 END) AS pending_messages,
                  sum(CASE WHEN is_noise=1 THEN 1 ELSE 0 END) AS noise_messages
                FROM messages
                WHERE received_at >= datetime('now','-24 hours')
                """
            ).fetchone()
            summary_counts = db.execute(
                """
                SELECT
                  count(*) AS total,
                  sum(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending
                FROM summary_batches
                WHERE created_at >= datetime('now','-24 hours')
                """
            ).fetchone()
            pending_notifications = db.execute(
                "SELECT count(*) FROM notifications WHERE status='pending'"
            ).fetchone()[0]
            groups = list(db.execute(
                """
                SELECT g.qq_group_id,g.name,g.type,g.enabled,
                  count(m.id) AS messages_24h,
                  sum(CASE WHEN m.summary_batch_id IS NULL AND m.is_noise=0 THEN 1 ELSE 0 END) AS pending,
                  max(m.sent_at) AS last_message_at
                FROM groups g
                LEFT JOIN messages m ON m.group_id=g.id AND m.received_at >= datetime('now','-24 hours')
                WHERE g.enabled=1 AND g.available=1
                GROUP BY g.id ORDER BY g.type,g.name
                """
            ).fetchall())
            deliveries = list(db.execute(
                """
                SELECT delivery_key,email_subject,status,sent_at,error_message,created_at
                FROM deliveries ORDER BY id DESC LIMIT 5
                """
            ).fetchall())
            last_message = db.execute("SELECT max(received_at) FROM messages").fetchone()[0]
        return {
            "generated_at": _utc_now(),
            "last_message_at": last_message,
            "metrics": {
                "received_24h": int(counts["received"] or 0),
                "pending_messages": int(counts["pending_messages"] or 0),
                "noise_messages_24h": int(counts["noise_messages"] or 0),
                "summaries_24h": int(summary_counts["total"] or 0),
                "pending_summaries": int(summary_counts["pending"] or 0),
                "pending_notifications": int(pending_notifications or 0),
            },
            "groups": [dict(row) | {
                "messages_24h": int(row["messages_24h"] or 0),
                "pending": int(row["pending"] or 0),
            } for row in groups],
            "deliveries": [dict(row) for row in deliveries],
        }

    def is_writable(self) -> bool:
        try:
            with self.transaction() as db:
                db.execute("CREATE TEMP TABLE IF NOT EXISTS healthcheck(value INTEGER)")
            return True
        except sqlite3.Error:
            return False


class UnknownGroupError(ValueError):
    pass


class ConcurrentSummaryError(RuntimeError):
    pass


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stored_message(row: sqlite3.Row) -> StoredMessage:
    return StoredMessage(
        id=row["id"], qq_message_id=row["qq_message_id"], group_id=row["group_id"],
        qq_group_id=row["qq_group_id"], group_name=row["group_name"],
        group_type=GroupType(row["group_type"]), sender_id=row["sender_id"],
        sender_name=row["sender_name"], message_type=row["message_type"], text=row["text"],
        attachment_title=row["attachment_title"], url=row["url"],
        sent_at=datetime.fromisoformat(row["sent_at"]), received_at=datetime.fromisoformat(row["received_at"]),
    )
