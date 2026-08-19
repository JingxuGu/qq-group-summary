from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

from app.models import GroupType


@dataclass(frozen=True, slots=True)
class SummaryPolicy:
    max_messages: int
    idle_minutes: int
    max_window_hours: int


@dataclass(frozen=True, slots=True)
class GroupConfig:
    id: str
    name: str
    type: GroupType
    enabled: bool = True
    max_messages: int | None = None
    idle_minutes: int | None = None
    max_window_hours: int | None = None


@dataclass(frozen=True, slots=True)
class LLMEndpoint:
    provider: str
    model: str
    base_url: str
    api_key_env: str
    retries: int = 1


@dataclass(frozen=True, slots=True)
class SMTPConfig:
    host: str
    port: int
    use_ssl: bool
    starttls: bool
    from_address: str
    to_address: str


@dataclass(frozen=True, slots=True)
class AppConfig:
    timezone: str
    daily_email_time: str
    host: str
    port: int
    napcat_webui_url: str
    database: Path
    raw_message_retention_days: int
    log_directory: Path
    log_retention_days: int
    primary_llm: LLMEndpoint
    fallback_llm: LLMEndpoint | None
    summary_policies: dict[GroupType, SummaryPolicy]
    groups: tuple[GroupConfig, ...]
    smtp: SMTPConfig
    ingest_token: str = field(repr=False)
    env: dict[str, str] = field(repr=False)

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def api_key_for(self, endpoint: LLMEndpoint) -> str:
        return self.env.get(endpoint.api_key_env, "")

    @property
    def smtp_username(self) -> str:
        return self.env.get("SMTP_USERNAME", "")

    @property
    def smtp_password(self) -> str:
        return self.env.get("SMTP_PASSWORD", "")


def _required(mapping: dict[str, Any], key: str) -> Any:
    if key not in mapping:
        raise ValueError(f"missing configuration key: {key}")
    return mapping[key]


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _endpoint(raw: dict[str, Any]) -> LLMEndpoint:
    return LLMEndpoint(
        provider=str(_required(raw, "provider")),
        model=str(_required(raw, "model")),
        base_url=str(_required(raw, "base_url")).rstrip("/"),
        api_key_env=str(_required(raw, "api_key_env")),
        retries=max(0, int(raw.get("retries", 1))),
    )


def load_config(path: str | Path = "config.yaml", *, env: dict[str, str] | None = None) -> AppConfig:
    config_path = Path(path).resolve()
    load_dotenv(config_path.with_name(".env"), override=False)
    values = dict(os.environ if env is None else env)
    with config_path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream) or {}

    app = raw.get("app", {})
    storage = raw.get("storage", {})
    logging_raw = raw.get("logging", {})
    llm = raw.get("llm", {})
    smtp_raw = raw.get("smtp", {})
    timezone_name = str(app.get("timezone", "Asia/Shanghai"))
    ZoneInfo(timezone_name)

    policies: dict[GroupType, SummaryPolicy] = {}
    for name, policy in _required(raw, "summary_policy").items():
        group_type = GroupType(name)
        policies[group_type] = SummaryPolicy(
            max_messages=int(_required(policy, "max_messages")),
            idle_minutes=int(_required(policy, "idle_minutes")),
            max_window_hours=int(_required(policy, "max_window_hours")),
        )
    if set(policies) != set(GroupType):
        raise ValueError("summary_policy must define course, academic and casual")

    groups = tuple(
        GroupConfig(
            id=str(_required(item, "id")),
            name=str(_required(item, "name")),
            type=GroupType(_required(item, "type")),
            enabled=bool(item.get("enabled", True)),
            max_messages=item.get("max_messages"),
            idle_minutes=item.get("idle_minutes"),
            max_window_hours=item.get("max_window_hours"),
        )
        for item in raw.get("groups", [])
    )
    if len({group.id for group in groups}) != len(groups):
        raise ValueError("group ids must be unique")

    ingest_token = values.get("MESSAGE_INGEST_TOKEN", "")
    if not ingest_token:
        raise ValueError("MESSAGE_INGEST_TOKEN is required")

    fallback_raw = llm.get("fallback")
    return AppConfig(
        timezone=timezone_name,
        daily_email_time=str(app.get("daily_email_time", "22:30")),
        host=str(app.get("host", "127.0.0.1")),
        port=int(app.get("port", 8765)),
        napcat_webui_url=str(app.get("napcat_webui_url", "http://127.0.0.1:6099/webui")),
        database=_resolve(config_path.parent, str(storage.get("database", "./data/qq_summary.db"))),
        raw_message_retention_days=int(storage.get("raw_message_retention_days", 14)),
        log_directory=_resolve(config_path.parent, str(logging_raw.get("directory", "./logs"))),
        log_retention_days=int(logging_raw.get("retention_days", 14)),
        primary_llm=_endpoint(_required(llm, "primary")),
        fallback_llm=_endpoint(fallback_raw) if fallback_raw else None,
        summary_policies=policies,
        groups=groups,
        smtp=SMTPConfig(
            host=str(_required(smtp_raw, "host")),
            port=int(smtp_raw.get("port", 465)),
            use_ssl=bool(smtp_raw.get("use_ssl", True)),
            starttls=bool(smtp_raw.get("starttls", False)),
            from_address=str(_required(smtp_raw, "from_address")),
            to_address=str(_required(smtp_raw, "to_address")),
        ),
        ingest_token=ingest_token,
        env=values,
    )
