"""Provider-neutral LLM abstraction for memory extraction and consolidation."""

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_estimate: float = 0.0


class LLMProvider(ABC):
    """Abstract interface for LLM completions."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @property
    @abstractmethod
    def default_model(self) -> str:
        pass

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """Generate text completion from LLM."""


class MockLLMProvider(LLMProvider):
    """Deterministic, offline LLM provider for zero-cost testing and offline consolidation."""

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def default_model(self) -> str:
        return "cortex-deterministic-mock"

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        start_time = time.perf_counter()
        chosen_model = model or self.default_model

        # Heuristic response for memory consolidation
        if "consolidate" in prompt.lower() or (system_prompt and "consolidate" in system_prompt.lower()):
            content = """# Consolidated Architectural Rules
- Database connections must be pooled and initialized inside application lifespan.
- All public REST endpoints must validate input models through Pydantic v2 schemas.
- High-risk operations require verification against previous failure logs.
"""
        elif "extract" in prompt.lower():
            content = """TITLE: JWT Stateless Authentication Invariant
TYPE: CONSTRAINT
SUMMARY: Tokens must be verified using HMAC-SHA256 and include revocable session ID.
CONTENT: The authentication layer enforces stateless JWT verification with redis blacklisting for revoked tokens.
"""
        else:
            content = f"CortexForge Automated Response for: {prompt[:100]}..."

        duration = (time.perf_counter() - start_time) * 1000
        in_tokens = max(1, len(prompt.split()))
        out_tokens = max(1, len(content.split()))

        return LLMResponse(
            content=content,
            model=chosen_model,
            provider=self.provider_name,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            latency_ms=round(duration, 2),
            cost_estimate=0.0,
        )


class OpenAIProvider(LLMProvider):
    """OpenAI API provider."""

    def __init__(self, api_key: str | None = None, default_model: str = "gpt-4o-mini") -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._default_model = default_model

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def default_model(self) -> str:
        return self._default_model

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        chosen_model = model or self.default_model
        if not self._api_key:
            return await MockLLMProvider().generate(prompt, system_prompt, chosen_model)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": chosen_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            latency = (time.perf_counter() - start) * 1000

            usage = data.get("usage", {})
            in_tokens = usage.get("prompt_tokens", len(prompt.split()))
            out_tokens = usage.get("completion_tokens", 0)
            content = data["choices"][0]["message"]["content"]

            cost = (in_tokens * 0.00000015) + (out_tokens * 0.0000006)
            return LLMResponse(
                content=content,
                model=chosen_model,
                provider="openai",
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                latency_ms=round(latency, 2),
                cost_estimate=cost,
            )


def get_llm_provider(provider_type: str | None = None) -> LLMProvider:
    """Factory returning configured LLM provider."""
    ptype = (provider_type or os.environ.get("CORTEX_LLM_PROVIDER", "mock")).lower()
    if ptype == "openai":
        return OpenAIProvider()
    return MockLLMProvider()
