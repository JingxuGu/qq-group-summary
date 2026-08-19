from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone

from app.runtime import build_runtime


def run() -> None:
    parser = argparse.ArgumentParser(description="QQ Group Summary maintenance commands")
    parser.add_argument("--config", default=os.environ.get("QQ_DAILY_CONFIG", "config.yaml"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="create/update the database and synchronize groups")
    summarize = subparsers.add_parser("summarize", help="run stage summaries once")
    summarize.add_argument("--force", action="store_true", help="include groups below normal thresholds")
    subparsers.add_parser("send-digest", help="force pending summaries and send one digest")
    subparsers.add_parser("cleanup", help="remove expired messages that already have summaries")
    args = parser.parse_args()

    runtime = build_runtime(args.config)
    if args.command == "init-db":
        print(f"database ready: {runtime.database.path}")
    elif args.command == "summarize":
        ids = asyncio.run(runtime.summarizer.summarize_due_groups(force=args.force))
        print(f"created summary batches: {ids}")
    elif args.command == "send-digest":
        result = asyncio.run(runtime.digest.deliver())
        print(f"delivery result: {result.reason}, id={result.delivery_id}")
    elif args.command == "cleanup":
        from datetime import timedelta

        before = datetime.now(timezone.utc) - timedelta(days=runtime.config.raw_message_retention_days)
        print(f"deleted messages: {runtime.database.cleanup_summarized_messages(before=before)}")


if __name__ == "__main__":
    run()
