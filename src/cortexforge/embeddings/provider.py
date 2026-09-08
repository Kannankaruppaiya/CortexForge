"""Provider-neutral, versioned embedding generation (specification section 22).

Two embedding spaces are never interchangeable. A deterministic hash vector and an
OpenAI embedding of the same sentence are unrelated points in unrelated spaces, so
mixing them inside one index makes every cosine score meaningless. The providers
here therefore carry an explicit ``quality_class``, and substituting one for
another is either refused or loudly announced -- never silent.
"""

import hashlib
import logging
import math
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingResult:
    vector: list[float]
    model: str
    version: str
    dimension: int
    quality_class: str = "REAL_SEMANTIC_EMBEDDING"


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

    @property
    def quality_class(self) -> str:
        """What kind of vector space this provider produces.

        ``LOCAL_DETERMINISTIC_HASH`` vectors are reproducible and useful for tests,
        offline development and baseline benchmarking, but they encode token
        overlap rather than meaning. Callers that care about semantic similarity
        must be able to tell the difference.
        """
        return "REAL_SEMANTIC_EMBEDDING"

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

    @property
    def quality_class(self) -> str:
        return "LOCAL_DETERMINISTIC_HASH"

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
            quality_class=self.quality_class,
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
            # Falling back to the local hash provider here would be worse than
            # failing: the vectors are not comparable to real embeddings, so a
            # single missing key would silently poison the index with a mixture of
            # two incompatible spaces (sections 22 and 24). Outside production the
            # fallback is allowed, but it is loud and the returned vectors are
            # labelled with the substitute model so nothing downstream can mistake
            # them for OpenAI embeddings.
            environment = os.environ.get(
                "CORTEX_ENV", os.environ.get("ENVIRONMENT", "development")
            ).lower()
            if environment in ("production", "prod", "staging"):
                raise RuntimeError(
                    "OpenAIEmbeddingProvider is configured but OPENAI_API_KEY is not "
                    f"set, and the environment is '{environment}'. Refusing to "
                    "substitute deterministic hash vectors for semantic embeddings."
                )
            logger.warning(
                "OPENAI_API_KEY is not set; using local deterministic hash embeddings "
                "instead. These are NOT semantic embeddings and must not be mixed "
                "with a real embedding index."
            )
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


# Both spellings are accepted because `.env.example` documents the plural form
# while the code originally read the singular one -- setting the documented
# variable silently had no effect. Accepting both, with the documented name taking
# precedence, means the configuration a person is told to write is the one that works.
_PROVIDER_ENV_VARS = ("CORTEX_EMBEDDINGS_PROVIDER", "CORTEX_EMBEDDING_PROVIDER")


def configured_embedding_provider_name() -> str:
    """The embedding provider this environment asks for."""
    for variable in _PROVIDER_ENV_VARS:
        value = os.environ.get(variable)
        if value:
            return value.strip().lower()
    return "local"


def get_embedding_provider(provider_type: str | None = None) -> EmbeddingProvider:
    """Return the configured embedding provider.

    An unknown provider name is an error rather than a silent fall back to the
    local hash provider: quietly substituting a deterministic hash for the
    semantic model an operator asked for would put incompatible vectors into the
    same index and make every similarity score meaningless (section 22).
    """
    ptype = (provider_type or configured_embedding_provider_name()).lower()
    if ptype == "openai":
        return OpenAIEmbeddingProvider()
    if ptype in ("local", "fast", "deterministic", "hash"):
        return FastDeterministicEmbeddingProvider()
    raise ValueError(
        f"Unknown embedding provider '{ptype}'. Set one of "
        f"{' or '.join(_PROVIDER_ENV_VARS)} to 'local' or 'openai'."
    )
