"""Offline lexical feature hashing. No claim of learned semantic understanding."""

import hashlib
import math
import re
from typing import Protocol


class EmbeddingProvider(Protocol):
    identity: str

    async def embed(self, text: str) -> list[float]: ...


class HashEmbeddingProvider:
    identity = "hash-lexical-v1-256"

    async def embed(self, text: str) -> list[float]:
        vector = [0.0] * 256
        for word in re.findall(r"\w+", text.lower()):
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest[:4], "big") % 256] += 1 if digest[4] % 2 else -1
        length = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / length for v in vector]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("Embedding dimensions differ")
    denominator = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / denominator if denominator else 0.0
