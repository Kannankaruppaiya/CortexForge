"""Provider-neutral, versioned embedding generation interface."""

import hashlib
import math
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx


@dataclass
class EmbeddingResult:
    vector: list[float]
    model: str
    version: str
    dimension: int


class EmbeddingProvider(ABC):
    """Abstract base class for generating text embeddings."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        pass

    @abstractmethod
    async def embed_text(self, text: str) -> EmbeddingResult:
        """Embed a single string into a normalized dense vector."""

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Embed a batch of strings."""


class FastDeterministicEmbeddingProvider(EmbeddingProvider):
    """Local, high-speed, zero-dependency embedding generator.

    Uses deterministic n-gram hashing and feature-hashing projection
    to generate normalized 384-dimensional dense vectors with genuine
    cosine similarity properties for local development and testing.
    """

    def __init__(self, dim: int = 384) -> None:
        self._dim = dim

    @property
    def model_name(self) -> str:
        return "cortex-fast-hash-384"

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def version(self) -> str:
        return "1.0.0"

    def _hash_vector(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        words = text.lower().split()
        if not words:
            return vec

        # Unigrams & Bigrams
        tokens = list(words)
        for i in range(len(words) - 1):
            tokens.append(f"{words[i]}_{words[i+1]}")

        for tok in tokens:
            # 64-bit murmur-like hash simulation via md5
            digest = hashlib.md5(tok.encode("utf-8")).digest()
            val = int.from_bytes(digest[:4], "big")
            sign = 1.0 if (val & 1) else -1.0
            idx = val % self._dim
            vec[idx] += sign

        # L2 normalization
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            vec = [x / norm for x in vec]
        return vec

    async def embed_text(self, text: str) -> EmbeddingResult:
        vec = self._hash_vector(text)
        return EmbeddingResult(
            vector=vec,
            model=self.model_name,
            version=self.version,
            dimension=self.dimension,
        )

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        return [await self.embed_text(t) for t in texts]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI API embedding provider using async HTTP client."""

    def __init__(self, api_key: str | None = None, model: str = "text-embedding-3-small") -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model = model
        self._dim = 1536 if "large" not in model else 3072

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def version(self) -> str:
        return "openai-v1"

    async def embed_text(self, text: str) -> EmbeddingResult:
        batch = await self.embed_batch([text])
        return batch[0]

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        if not self._api_key:
            env = os.environ.get("CORTEX_ENV", os.environ.get("ENVIRONMENT", "development")).lower()
            if env == "production":
                raise RuntimeError(
                    "Production configuration error: OpenAIEmbeddingProvider configured in production but OPENAI_API_KEY is not set."
                )
            # Fallback to local deterministic in development/test if no API key provided
            fallback = FastDeterministicEmbeddingProvider(dim=self._dim)
            return await fallback.embed_batch(texts)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={"input": texts, "model": self._model},
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            for item in data["data"]:
                results.append(
                    EmbeddingResult(
                        vector=item["embedding"],
                        model=self._model,
                        version=self.version,
                        dimension=len(item["embedding"]),
                    )
                )
            return results


def get_embedding_provider(provider_type: str | None = None) -> EmbeddingProvider:
    """Factory creating configured embedding provider."""
    ptype = (provider_type or os.environ.get("CORTEX_EMBEDDING_PROVIDER", "local")).lower()
    if ptype == "openai":
        return OpenAIEmbeddingProvider()
    return FastDeterministicEmbeddingProvider()
