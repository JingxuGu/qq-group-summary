import shutil
from pathlib import Path

from app.runtime import build_runtime


def test_runtime_builds_database_and_persists_retention_setting(tmp_path, monkeypatch):
    project_root = Path(__file__).resolve().parents[1]
    shutil.copy(project_root / "config.example.yaml", tmp_path / "config.yaml")
    shutil.copytree(project_root / "prompts", tmp_path / "prompts")
    monkeypatch.setenv("MESSAGE_INGEST_TOKEN", "test-token")
    runtime = build_runtime(tmp_path / "config.yaml")
    assert runtime.database.path == (tmp_path / "data" / "qq_summary.db").resolve()
    assert runtime.database.get_setting("raw_message_retention_days") == "14"
    assert len(runtime.database.configured_groups()) == 0
    assert runtime.scheduler.running is False
    assert runtime.app.title == "QQ Group Summary"
