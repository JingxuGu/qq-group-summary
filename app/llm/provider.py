from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import LLMEndpoint


class LLMProvider(Protocol):
    name: str
    model: str

    async def generate_json(self, *, system_prompt: str, user_content: str) -> dict[str, Any]: ...


class LLMError(RuntimeError):
    pass


@dataclass(slots=True)
class OpenAICompatibleProvider:
    name: str
    model: str
    base_url: str
    api_key: str
    timeout_seconds: float = 90.0

    async def generate_json(self, *, system_prompt: str, user_content: str) -> dict[str, Any]:
        if not self.api_key:
            raise LLMError(f"missing API key for {self.name}")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(_strip_code_fence(content))
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"{self.name} request failed: {type(exc).__name__}") from exc
        if not isinstance(parsed, dict):
            raise LLMError(f"{self.name} returned a non-object JSON value")
        return parsed


def provider_from_config(endpoint: LLMEndpoint, api_key: str) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(endpoint.provider, endpoint.model, endpoint.base_url, api_key)


def _strip_code_fence(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
    return stripped

