"""The embedding interface the chunker and the ingest depend on, so tests can inject a deterministic fake."""

from typing import Protocol


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
