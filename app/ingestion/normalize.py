from __future__ import annotations

import json
import re

from app.models import MessageSegment, NormalizedMessage


_MEDIA_LABELS = {"image": "[图片]", "video": "[视频]", "record": "[语音]", "audio": "[语音]"}


def normalize_segments(segments: tuple[MessageSegment, ...]) -> NormalizedMessage:
    text_parts: list[str] = []
    attachment_title: str | None = None
    url: str | None = None
    kinds: list[str] = []
    raw: list[dict[str, object]] = []

    for segment in segments:
        kind = segment.type.lower()
        data = dict(segment.data)
        raw.append({"type": kind, "data": data})
        kinds.append(kind)
        if kind == "text":
            text_parts.append(str(data.get("text", "")))
        elif kind in {"at", "mention"}:
            text_parts.append(f"@{data.get('name') or data.get('qq') or data.get('id') or '成员'}")
        elif kind == "file":
            attachment_title = str(data.get("name") or data.get("file") or "未命名文件")
            text_parts.append(f"[文件: {attachment_title}]")
        elif kind in {"link", "share"}:
            url = str(data.get("url") or "") or None
            title = str(data.get("title") or "链接")
            text_parts.append(f"[{title}] {url or ''}".strip())
        elif kind in _MEDIA_LABELS:
            text_parts.append(_MEDIA_LABELS[kind])

    text = re.sub(r"\s+", " ", " ".join(part.strip() for part in text_parts if part.strip())).strip()
    meaningful = [kind for kind in kinds if kind not in {"reply", "at", "mention"}]
    message_type = meaningful[0] if len(set(meaningful)) == 1 and meaningful else "mixed"
    return NormalizedMessage(
        message_type=message_type,
        text=text,
        attachment_title=attachment_title,
        url=url,
        segments_json=json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
    )

