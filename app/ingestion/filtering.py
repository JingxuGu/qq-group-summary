from __future__ import annotations

import re


_FIXED_NOISE = {"哈哈", "哈哈哈", "收到", "好的", "ok", "OK", "+1", "1", "666"}
_PLACEHOLDER_ONLY = re.compile(r"^(?:\[(?:图片|视频|语音)\]\s*)+$")
_EMOJI_ONLY = re.compile(r"^[\W_\u2600-\u27ff\U0001f300-\U0001faff]+$", re.UNICODE)


def is_obvious_noise(text: str, *, message_type: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return True
    if normalized in _FIXED_NOISE:
        return True
    if _PLACEHOLDER_ONLY.fullmatch(normalized):
        return False
    return bool(_EMOJI_ONLY.fullmatch(normalized))

