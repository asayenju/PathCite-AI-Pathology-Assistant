"""Local BM25 keyword index for the claim checker (docs/PRD-rag-checker.md, "v4 local BM25 (issue #56)").

Why: on Supabase's free tier, Postgres full-text search had to scan and rank many rows per query, and sustained
searches exhausted the disk IO budget (v3 eval: ~95 statement timeouts in 330 calls, p95 26 s). The keyword half of
the hybrid search now runs here, in memory, and Supabase only does the HNSW vector lookup, a primary-key lookup of
the fused ids and the fusion (`match_chunks_fused`, supabase/migrations/20261128010000_match_chunks_bm25.sql).

- **Index:** bm25s (Lucene BM25, k1 1.5, b 0.75) over `section + "\\n" + content` of every stored chunk. Tokens:
  lowercased words of 2+ characters (`\\b\\w\\w+\\b`), bm25s's English stopwords dropped, Snowball English stems
  (PyStemmer), plus one fold for Greek plurals the Snowball stemmer leaves apart ("metastases"/"metastasis",
  "diagnoses"/"diagnosis": a stem ending in "sis" or "si" loses the "is"/"i"). The same tokenizer runs on queries.
- **Cancer filter:** applied to the full score vector before the top k is taken (a chunk must carry the label in
  `chunks.cancers`), so a filtered search returns the best k of that cancer, or fewer if fewer chunks match at all.
  Only chunks with a positive score (at least one query token) are returned; ties go to the lower chunk id. Scoring
  all ~9.5k chunks with numpy is well under a millisecond-scale cost, so no approximate retrieval is needed.
- **Files** (default `backend/.cache/corpus/bm25/`, gitignored): the bm25s matrices and vocabulary, `ids.npy` (row
  -> chunk id), `cancers.json` (label -> row numbers) and `manifest.json`, written last, with the corpus fingerprint.
- **Fingerprint:** chunk count, max chunk id (ids are an identity column, so any re-ingested chunk raises it) and
  corpus_meta's `chunker_config_hash` and `quality_filter_hash`; the manifest also has a sha256 of the sorted ids.
  `KeywordIndexProvider` loads the index lazily, compares it with the live corpus once (corpus_meta + one count
  query) and caches the answer for the life of the process: no extra round trip per claim. A missing, unreadable or
  stale index (or a database without the fused RPC) means the v3 `match_chunks` path, logged once (condition or
  exception type only). After rebuilding the index, restart the backend to pick it up.
- **Threads:** a built index is read-only; the stemmer (not thread-safe) is per thread. Search is CPU work, so
  callers run it in a worker thread (EvidenceSearch does it inside its own worker thread).

Build it with `python scripts/build_bm25_index.py`; the ingest CLI rebuilds it after every ingest that changed the
corpus. Privacy: chunk text is public guideline/abstract text; queries are never logged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import bm25s
import numpy as np
import Stemmer
from bm25s.stopwords import STOPWORDS_EN

from app.config import REPO_ROOT

logger = logging.getLogger("pathcite")

DEFAULT_DIR = REPO_ROOT / "backend" / ".cache" / "corpus" / "bm25"
INDEX_VERSION = 1
CANDIDATES = 40
"""Keyword ids sent to match_chunks_fused per search (its `candidates` default)."""
MANIFEST = "manifest.json"
IDS_FILE = "ids.npy"
CANCERS_FILE = "cancers.json"
BM25_DIR = "bm25s"
FINGERPRINT_KEYS = ("chunks", "max_id", "chunker_config_hash", "quality_filter_hash")

_TOKEN = re.compile(r"(?u)\b\w\w+\b")
_STOPWORDS = frozenset(STOPWORDS_EN)
_local = threading.local()


class IndexError_(Exception):  # noqa: N801 - "IndexError" is a builtin
    """The index files are unreadable or inconsistent."""


class IndexMissing(IndexError_):
    """No index at that directory."""


@dataclass(frozen=True)
class IndexRow:
    chunk_id: int
    section: str
    content: str
    cancers: tuple[str, ...] = ()


def _stemmer() -> Stemmer.Stemmer:
    s = getattr(_local, "stemmer", None)
    if s is None:
        s = _local.stemmer = Stemmer.Stemmer("english")
    return s


def _fold(stem: str) -> str:
    if stem.endswith("sis") and len(stem) > 5:
        return stem[:-2]
    if stem.endswith("si") and len(stem) > 4:
        return stem[:-1]
    return stem


def tokenize(text: str) -> list[str]:
    words = [w for w in _TOKEN.findall(text.lower()) if w not in _STOPWORDS]
    return [_fold(s) for s in _stemmer().stemWords(words)]


def fingerprint(meta: dict, *, chunks: int, max_id: int | None) -> dict:
    return {
        "chunks": chunks,
        "max_id": max_id,
        "chunker_config_hash": meta.get("chunker_config_hash"),
        "quality_filter_hash": meta.get("quality_filter_hash"),
    }


def store_fingerprint(store) -> dict:
    """The live corpus's fingerprint: corpus_meta plus one count/max-id query."""
    meta = store.read_meta()
    n, max_id = store.chunk_count_and_max_id()
    return fingerprint(meta, chunks=n, max_id=max_id)


class Bm25Index:
    def __init__(self, retriever: bm25s.BM25, ids: np.ndarray, cancers: dict[str, np.ndarray], manifest: dict):
        self._retriever = retriever
        self._ids = ids
        self._cancers = cancers  # label -> bool mask over rows
        self.manifest = manifest

    def __len__(self) -> int:
        return len(self._ids)

    @property
    def fingerprint(self) -> dict:
        return dict(self.manifest["fingerprint"])

    def matches(self, fp: dict) -> bool:
        return all(self.manifest["fingerprint"].get(k) == fp.get(k) for k in FINGERPRINT_KEYS)

    # --- build -------------------------------------------------------------

    @classmethod
    def build(cls, rows: Sequence[IndexRow], meta: dict) -> Bm25Index:
        ids = np.array([r.chunk_id for r in rows], dtype=np.int64)
        if len(set(ids.tolist())) != len(ids):
            raise ValueError("duplicate chunk ids")
        corpus = [tokenize(f"{r.section}\n{r.content}") for r in rows]
        retriever = bm25s.BM25()
        retriever.index(corpus, show_progress=False)
        labels: dict[str, list[int]] = {}
        for i, r in enumerate(rows):
            for label in set(r.cancers):
                labels.setdefault(label, []).append(i)
        cancers = {label: _mask(len(rows), rows_) for label, rows_ in labels.items()}
        manifest = {
            "version": INDEX_VERSION,
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "chunks": len(rows),
            "ids_sha256": _ids_hash(ids),
            "fingerprint": fingerprint(meta, chunks=len(rows), max_id=int(ids.max()) if len(ids) else None),
            "tokenizer": "bm25s english stopwords + snowball english + sis fold",
            "cancers": {label: len(r) for label, r in sorted(labels.items())},
        }
        return cls(retriever, ids, cancers, manifest)

    # --- files -------------------------------------------------------------

    def save(self, directory: Path) -> None:
        """Writes a sibling temp directory, then swaps it in (the manifest is written last)."""
        directory = Path(directory)
        directory.parent.mkdir(parents=True, exist_ok=True)
        tmp = directory.with_name(directory.name + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        self._retriever.save(str(tmp / BM25_DIR), show_progress=False)
        np.save(tmp / IDS_FILE, self._ids, allow_pickle=False)
        rows = {label: np.flatnonzero(m).tolist() for label, m in self._cancers.items()}
        (tmp / CANCERS_FILE).write_text(json.dumps(rows), encoding="utf-8")
        (tmp / MANIFEST).write_text(json.dumps(self.manifest, indent=1), encoding="utf-8")
        old = directory.with_name(directory.name + ".old")
        if old.exists():
            shutil.rmtree(old)
        if directory.exists():
            directory.rename(old)
        tmp.rename(directory)
        if old.exists():
            shutil.rmtree(old)

    @staticmethod
    def read_manifest(directory: Path) -> dict | None:
        try:
            return json.loads((Path(directory) / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @classmethod
    def load(cls, directory: Path) -> Bm25Index:
        directory = Path(directory)
        if not (directory / MANIFEST).is_file():
            raise IndexMissing(f"no BM25 index at {directory}")
        try:
            manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
            if manifest.get("version") != INDEX_VERSION:
                raise IndexError_("index version mismatch")
            ids = np.load(directory / IDS_FILE, allow_pickle=False)
            labels = json.loads((directory / CANCERS_FILE).read_text(encoding="utf-8"))
            retriever = bm25s.BM25.load(str(directory / BM25_DIR), show_progress=False, verbosity=0)
        except IndexError_:
            raise
        except Exception as e:  # noqa: BLE001 - any unreadable file
            raise IndexError_(f"unreadable BM25 index ({type(e).__name__})") from None
        if len(ids) != manifest.get("chunks") or _ids_hash(ids) != manifest.get("ids_sha256"):
            raise IndexError_("BM25 index ids don't match its manifest")
        cancers = {label: _mask(len(ids), rows) for label, rows in labels.items()}
        return cls(retriever, ids, cancers, manifest)

    # --- search ------------------------------------------------------------

    def search(self, terms: str, k: int = CANDIDATES, cancer: str | None = None) -> list[tuple[int, float]]:
        """The best k (chunk_id, score) for `terms`, best first, restricted to chunks tagged `cancer` if given."""
        if not isinstance(k, int) or k < 1:
            raise ValueError("k must be a positive integer")
        vocab = self._retriever.vocab_dict
        token_ids = [vocab[t] for t in dict.fromkeys(tokenize(terms or "")) if t in vocab]
        if not token_ids or not len(self._ids):
            return []
        scores = np.asarray(self._retriever.get_scores_from_ids(token_ids), dtype=np.float64)
        if cancer is not None:
            mask = self._cancers.get(cancer)
            if mask is None:
                return []
            scores = np.where(mask, scores, 0.0)
        rows = np.flatnonzero(scores > 0)
        if len(rows) > k:
            # Keep every row tied with the k-th score, then sort exactly (score desc, chunk id asc).
            kth = np.partition(scores[rows], len(rows) - k)[len(rows) - k]
            rows = rows[scores[rows] >= kth]
        order = np.lexsort((self._ids[rows], -scores[rows]))[:k]
        return [(int(self._ids[r]), float(scores[r])) for r in rows[order]]


def _mask(n: int, rows: Iterable[int]) -> np.ndarray:
    m = np.zeros(n, dtype=bool)
    m[list(rows)] = True
    return m


def _ids_hash(ids: np.ndarray) -> str:
    return hashlib.sha256(",".join(map(str, sorted(ids.tolist()))).encode()).hexdigest()


# --- building from Supabase ----------------------------------------------------------------------------------------


def build_from_store(store, directory: Path = DEFAULT_DIR, *, progress: Callable[[str], None] = lambda _: None):
    """Reads every chunk (id, section, content, cancers) through `store.chunk_texts()` and saves the index."""
    meta = store.read_meta()
    rows: list[IndexRow] = []
    for r in store.chunk_texts():
        rows.append(IndexRow(int(r["id"]), r.get("section") or "", r.get("content") or "", tuple(r.get("cancers") or ())))
        if len(rows) % 2000 == 0:
            progress(f"  read {len(rows):,} chunks")
    progress(f"  read {len(rows):,} chunks; indexing")
    index = Bm25Index.build(rows, meta)
    index.save(directory)
    return index


def refresh_from_store(store, directory: Path = DEFAULT_DIR, *, progress: Callable[[str], None] = lambda _: None):
    """(index or None, rebuilt). Rebuilds only when the saved index doesn't match the live corpus."""
    manifest = Bm25Index.read_manifest(directory)
    if manifest is not None:
        live = store_fingerprint(store)
        if all(manifest.get("fingerprint", {}).get(k) == live.get(k) for k in FINGERPRINT_KEYS):
            return None, False
    return build_from_store(store, directory, progress=progress), True


def dir_size(directory: Path) -> int:
    return sum(p.stat().st_size for p in Path(directory).rglob("*") if p.is_file())


# --- the lazy, cached provider used by /check --------------------------------------------------------------------


class KeywordIndexProvider:
    """Loads the index on first use and checks it against the live corpus once; caches the result.

    `status`: "unchecked", "ready", "missing", "unreadable", "stale" or a reason given to `disable`. An upstream
    error while reading the fingerprint is not cached (the next search tries again)."""

    def __init__(self, directory: Path = DEFAULT_DIR):
        self.directory = Path(directory)
        self.status = "unchecked"
        self._index: Bm25Index | None = None
        self._lock = threading.Lock()

    def get(self, store) -> Bm25Index | None:
        if self.status == "ready":
            return self._index
        if self.status != "unchecked":
            return None
        with self._lock:
            if self.status == "unchecked":
                self._check(store)
            return self._index if self.status == "ready" else None

    def _check(self, store) -> None:
        try:
            index = Bm25Index.load(self.directory)
        except IndexMissing:
            self._set("missing")
            return
        except IndexError_ as e:
            self._set("unreadable", type(e).__name__)
            return
        try:
            live = store_fingerprint(store)
        except Exception as e:  # noqa: BLE001 - not cached; only the type is logged
            logger.warning("BM25 keyword index not checked, using match_chunks for now: %s", type(e).__name__)
            return
        if not index.matches(live):
            self._set("stale")
            return
        self._index = index
        self.status = "ready"

    def disable(self, reason: str) -> None:
        with self._lock:
            self._index = None
            self._set(reason)

    def _set(self, status: str, detail: str | None = None) -> None:
        self.status = status
        self._index = None
        suffix = f" ({detail})" if detail else ""
        logger.warning("BM25 keyword index unavailable: %s%s; keyword search falls back to match_chunks", status, suffix)


_providers: dict[Path, KeywordIndexProvider] = {}
_providers_lock = threading.Lock()


def default_provider(directory: Path | None = None) -> KeywordIndexProvider:
    """One provider per index directory for the process (so /check loads and checks the index once)."""
    key = Path(directory or DEFAULT_DIR).resolve()
    with _providers_lock:
        if key not in _providers:
            _providers[key] = KeywordIndexProvider(key)
        return _providers[key]


def manifest_summary(manifest: dict[str, Any]) -> str:
    fp = manifest.get("fingerprint", {})
    return (f"{manifest.get('chunks', 0):,} chunks, max id {fp.get('max_id')}, chunker "
            f"{str(fp.get('chunker_config_hash'))[:12]}, quality filter {str(fp.get('quality_filter_hash'))[:12]}")  # fmt: skip
