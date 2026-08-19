from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import uvicorn

from app.runtime import build_runtime


def configure_logging(directory: Path, retention_days: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = TimedRotatingFileHandler(
        directory / "qq-group-summary.log", when="midnight", backupCount=retention_days, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[console, file_handler])


def run() -> None:
    config_path = os.environ.get("QQ_DAILY_CONFIG", "config.yaml")
    runtime = build_runtime(config_path)
    configure_logging(runtime.config.log_directory, runtime.config.log_retention_days)
    uvicorn.run(runtime.app, host=runtime.config.host, port=runtime.config.port)


if __name__ == "__main__":
    run()
