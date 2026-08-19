from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.config import AppConfig, SummaryPolicy
from app.llm.failover import FailoverLLM
from app.models import GroupType, StoredMessage
from app.storage.database import Database
from app.summarizer.schemas import AcademicSummary, CasualSummary, CourseSummary
from app.summarizer.triggers import TriggerReason, trigger_reason


_SCHEMAS: dict[GroupType, type[BaseModel]] = {
    GroupType.COURSE: CourseSummary,
    GroupType.ACADEMIC: AcademicSummary,
    GroupType.CASUAL: CasualSummary,
}


class SummaryService:
    def __init__(self, database: Database, config: AppConfig, llm: FailoverLLM, prompt_directory: Path):
        self.database = database
        self.config = config
        self.llm = llm
        self.prompt_directory = prompt_directory
        self._run_lock = asyncio.Lock()

    async def summarize_due_groups(self, *, now: datetime | None = None, force: bool = False) -> list[int]:
        now = now or datetime.now(timezone.utc)
        created: list[int] = []
        async with self._run_lock:
            for group in self.database.configured_groups():
                batch_id = await self.summarize_group(group["id"], now=now, force=force)
                if batch_id is not None:
                    created.append(batch_id)
        return created

    async def summarize_group(self, group_id: int, *, now: datetime, force: bool = False) -> int | None:
        group = self.database.get_group(group_id)
        messages = self.database.pending_messages(group_id)
        policy = self._policy(group)
        reason = trigger_reason(messages, policy, now=now, force=force)
        if reason is None:
            return None
        group_type = GroupType(group["type"])
        schema = _SCHEMAS[group_type]
        prompt = (self.prompt_directory / f"{group_type.value}.txt").read_text(encoding="utf-8")
        payload = _format_messages(messages, reason)
        summary, result = await self.llm.generate_validated(
            system_prompt=prompt,
            user_content=payload,
            validate=schema.model_validate,
        )
        if isinstance(summary, CourseSummary):
            available_source_ids = {message.qq_message_id for message in messages}
            unknown_sources = {
                item.source_message_id for item in summary.notifications
                if item.source_message_id not in available_source_ids
            }
            if unknown_sources:
                raise ValueError(f"course summary referenced unknown messages: {sorted(unknown_sources)}")
        tags = summary.knowledge_tags if isinstance(summary, AcademicSummary) else []
        batch_id = self.database.save_summary_batch(
            group_id=group_id,
            messages=messages,
            summary_json=summary.model_dump_json(),
            tags_json=json.dumps(tags, ensure_ascii=False),
            provider=result.provider,
            model=result.model,
            attempts=result.attempts,
            notifications=(
                [item.model_dump() for item in summary.notifications]
                if isinstance(summary, CourseSummary) else []
            ),
        )
        return batch_id

    def _policy(self, group: Any) -> SummaryPolicy:
        default = self.config.summary_policies[GroupType(group["type"])]
        return SummaryPolicy(
            max_messages=group["max_messages"] or default.max_messages,
            idle_minutes=group["idle_minutes"] or default.idle_minutes,
            max_window_hours=group["max_window_hours"] or default.max_window_hours,
        )


def _format_messages(messages: list[StoredMessage], reason: TriggerReason) -> str:
    lines = [f"触发原因: {reason.value}", "群聊消息（只依据以下内容总结）:"]
    for item in messages:
        local_time = item.sent_at.isoformat()
        lines.append(f"[{local_time}] message_id={item.qq_message_id} {item.sender_name}: {item.text}")
    return "\n".join(lines)
