from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NotificationCandidate(StrictModel):
    title: str = Field(min_length=1)
    original_text: str = Field(min_length=1)
    source_message_id: str = Field(min_length=1)
    dedup_key: str = Field(min_length=1)
    update_text: str | None = None


class CourseSummary(StrictModel):
    notifications: list[NotificationCandidate] = Field(default_factory=list)
    qa_summary: str = ""


class MemberView(StrictModel):
    member: str = Field(min_length=1)
    view: str = Field(min_length=1)


class AcademicSummary(StrictModel):
    overview: str = ""
    member_views: list[MemberView] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    consensus: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    knowledge_tags: list[str] = Field(default_factory=list)


class CasualSummary(StrictModel):
    overview: str = ""
    noteworthy: list[str] = Field(default_factory=list)
    plans: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)

