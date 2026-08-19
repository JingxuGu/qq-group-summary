from __future__ import annotations

from app.ingestion.filtering import is_obvious_noise
from app.ingestion.normalize import normalize_segments
from app.models import IncomingMessage, IngestResult
from app.storage.database import Database


class IngestionService:
    def __init__(self, database: Database):
        self.database = database

    def ingest(self, message: IncomingMessage) -> IngestResult:
        normalized = normalize_segments(message.segments)
        noise = is_obvious_noise(normalized.text, message_type=normalized.message_type)
        return self.database.insert_message(message, normalized, is_noise=noise)

