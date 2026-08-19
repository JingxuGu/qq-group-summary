from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from app.llm.provider import LLMError, LLMProvider


@dataclass(frozen=True, slots=True)
class LLMResult:
    data: dict[str, Any]
    provider: str
    model: str
    attempts: int


class FailoverLLM:
    def __init__(
        self,
        primary: LLMProvider,
        *,
        primary_retries: int,
        fallback: LLMProvider | None = None,
        fallback_retries: int = 0,
        retry_delay: float = 0.0,
    ):
        self.primary = primary
        self.primary_retries = primary_retries
        self.fallback = fallback
        self.fallback_retries = fallback_retries
        self.retry_delay = retry_delay

    async def generate_validated(
        self,
        *,
        system_prompt: str,
        user_content: str,
        validate: Callable[[dict[str, Any]], Any],
    ) -> tuple[Any, LLMResult]:
        attempts = 0
        errors: list[str] = []
        providers = [(self.primary, self.primary_retries)]
        if self.fallback is not None:
            providers.append((self.fallback, self.fallback_retries))
        for provider, retries in providers:
            for retry in range(retries + 1):
                attempts += 1
                try:
                    data = await provider.generate_json(system_prompt=system_prompt, user_content=user_content)
                    validated = validate(data)
                    return validated, LLMResult(data, provider.name, provider.model, attempts)
                except Exception as exc:
                    errors.append(f"{provider.name}: {type(exc).__name__}")
                    if retry < retries and self.retry_delay:
                        await asyncio.sleep(self.retry_delay)
        raise LLMError(f"all providers failed after {attempts} attempts ({'; '.join(errors)})")

