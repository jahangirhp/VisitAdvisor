"""Dedicated embeddings adapter; no generated prose is used as a vector."""

import asyncio
import math
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class RetrievalFailure(Exception):
    """Only fixed safe codes cross the provider/Atlas boundary."""


@dataclass
class EmbeddingBatch:
    vectors: list[list[float]]
    input_tokens: int


class Embedder(Protocol):
    model: str
    dimensions: int

    async def embed(self, texts: list[str]) -> EmbeddingBatch: ...


def validate_vector(value: Any, dimensions: int) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != dimensions
        or any(type(v) not in (float, int) or not math.isfinite(v) for v in value)
        or not any(value)
    ):
        raise RetrievalFailure("INVALID_EMBEDDING")
    return [float(v) for v in value]


class OpenAIEmbedder:
    def __init__(
        self, http: httpx.AsyncClient, model: str, dimensions: int, attempts: int = 1
    ) -> None:
        self.http, self.model, self.dimensions = http, model, dimensions
        self.attempts = attempts

    async def embed(self, texts: list[str]) -> EmbeddingBatch:
        if not 1 <= len(texts) <= 32 or any(
            not t.strip() or len(t.encode("utf-8")) > 6000 for t in texts
        ):
            raise RetrievalFailure("EMBEDDING_INPUT_TOO_LARGE_OR_EMPTY")
        for attempt in range(self.attempts):
            try:
                response = await self.http.post(
                    "https://api.openai.com/v1/embeddings",
                    json={
                        "model": self.model,
                        "input": texts,
                        "dimensions": self.dimensions,
                        "encoding_format": "float",
                    },
                )
            except httpx.TransportError:
                if attempt + 1 < self.attempts:
                    await asyncio.sleep(2**attempt)
                    continue
                raise RetrievalFailure("EMBEDDING_NETWORK_ERROR") from None
            if response.status_code in (401, 403):
                raise RetrievalFailure("EMBEDDING_ACCESS_DENIED")
            if response.status_code == 429:
                try:
                    error = response.json().get("error", {})
                    quota = error.get("type") == "insufficient_quota" or error.get(
                        "code"
                    ) in ("insufficient_quota", "credit_balance_exhausted")
                except (ValueError, AttributeError):
                    quota = False
                if quota:
                    raise RetrievalFailure("EMBEDDING_QUOTA_EXHAUSTED")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < self.attempts:
                    await asyncio.sleep(2**attempt)
                    continue
            if response.is_error:
                raise RetrievalFailure("EMBEDDING_REQUEST_FAILED")
            try:
                data = response.json()
                rows = sorted(data["data"], key=lambda row: row["index"])
                if data["model"] != self.model or [
                    row["index"] for row in rows
                ] != list(range(len(texts))):
                    raise RetrievalFailure("INVALID_EMBEDDING")
                return EmbeddingBatch(
                    [
                        validate_vector(row["embedding"], self.dimensions)
                        for row in rows
                    ],
                    int(data["usage"]["total_tokens"]),
                )
            except (ValueError, KeyError, TypeError, AttributeError):
                raise RetrievalFailure("INVALID_EMBEDDING") from None
        raise RetrievalFailure("EMBEDDING_REQUEST_FAILED")
