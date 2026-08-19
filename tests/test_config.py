from pathlib import Path

import pytest

from app.config import load_config
from app.models import GroupType


def write_config(path: Path, group_type="course"):
    path.write_text(f"""
app:
  timezone: Asia/Shanghai
storage:
  database: ./data/test.db
llm:
  primary:
    provider: qwen
    model: qwen
    base_url: https://example.com/v1
    api_key_env: QWEN_API_KEY
summary_policy:
  course: {{max_messages: 1, idle_minutes: 1, max_window_hours: 1}}
  academic: {{max_messages: 2, idle_minutes: 2, max_window_hours: 2}}
  casual: {{max_messages: 3, idle_minutes: 3, max_window_hours: 3}}
smtp:
  host: smtp.example.com
  from_address: from@example.com
  to_address: to@example.com
groups:
  - id: "100"
    name: 测试群
    type: {group_type}
""", encoding="utf-8")


def test_config_resolves_paths_relative_to_config_file(tmp_path):
    path = tmp_path / "config.yaml"
    write_config(path)
    config = load_config(path, env={"MESSAGE_INGEST_TOKEN": "ingest-secret-value", "QWEN_API_KEY": "llm-secret-value"})
    assert config.database == (tmp_path / "data" / "test.db").resolve()
    assert config.groups[0].type is GroupType.COURSE
    assert "ingest-secret-value" not in repr(config)
    assert "llm-secret-value" not in repr(config)


def test_ingest_token_is_required(tmp_path):
    path = tmp_path / "config.yaml"
    write_config(path)
    with pytest.raises(ValueError, match="MESSAGE_INGEST_TOKEN"):
        load_config(path, env={})
