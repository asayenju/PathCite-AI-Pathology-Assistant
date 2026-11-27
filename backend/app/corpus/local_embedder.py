"""A deterministic, offline stand-in for the OpenAI embedder: hashed bag of words.

Used by the tests and by `--dry-run`, which must make no API calls. It only sees shared vocabulary, not meaning, so
dry-run chunk boundaries inside long sections are approximate; chunk counts and tokens are close to the real run.
"""

import hashlib
import math
import re

_WORD = re.compile(r"[^\W_]+")
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with".split()
)

ID = "local:hashed-bag-of-words"


class HashingEmbedder:
    def __init__(self, dims: int = 512) -> None:
        self.dims = dims

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        v = [0.0] * self.dims
        for word in _WORD.findall(text.casefold()):
            if word not in _STOPWORDS:
                h = int.from_bytes(hashlib.blake2b(word.encode(), digest_size=8).digest(), "big")
                v[h % self.dims] += 1.0 if (h >> 32) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v] if norm else v
