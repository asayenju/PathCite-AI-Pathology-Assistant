"""Load the fetched corpus, NFC-normalise it, assign trust levels and drop duplicate documents (PRD steps 1-2).

Input is what `scripts/fetch_cancer_corpus.py` writes to `.cache/corpus/`: `guidelines.jsonl` (source nice, pubmed or
wikidoc, optional linked `pmid`) and `pubmed.jsonl` (abstracts; the PMID is in the id, `pubmed:<pmid>`).
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Trust = Literal["high", "medium", "low"]
Kind = Literal["guideline", "abstract"]

GUIDELINE_TRUST: dict[str, Trust] = {"nice": "high", "pubmed": "high", "wikidoc": "low"}
ABSTRACT_TRUST: Trust = "medium"
TRUST_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}

_HYPHENS = str.maketrans({"‐": "-", "‑": "-", "­": None})
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Document:
    id: str
    kind: Kind
    source: str
    trust: Trust
    title: str
    url: str
    text: str
    cancers: tuple[str, ...] = ()
    pmid: str | None = None
    journal: str | None = None
    year: int | None = None
    pub_types: tuple[str, ...] = ()


@dataclass
class DedupeReport:
    pmid_dropped: list[str] = field(default_factory=list)
    duplicate_dropped: list[str] = field(default_factory=list)


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def normalise_for_hash(text: str) -> str:
    """For equality only (document dedupe, `content_hash`): NFC, ASCII hyphens, collapsed whitespace, casefold.
    Stored text is never rewritten this way."""
    return _WS.sub(" ", nfc(text).translate(_HYPHENS)).strip().casefold()


def parse_record(record: dict, *, kind: Kind) -> Document:
    source = record["source"]
    if kind == "guideline":
        if source not in GUIDELINE_TRUST:
            raise ValueError(f"unknown guideline source {source!r} in {record.get('id')}")
        trust = GUIDELINE_TRUST[source]
        pmid = record.get("pmid")
    else:
        trust = ABSTRACT_TRUST
        pmid = record.get("pmid") or record["id"].removeprefix("pubmed:")
    year = record.get("year")
    return Document(
        id=record["id"],
        kind=kind,
        source=source,
        trust=trust,
        title=nfc(record.get("title") or record["id"]).strip(),
        url=record["url"],
        text=nfc(record["text"]),
        cancers=tuple(record.get("cancers") or ()),
        pmid=str(pmid) if pmid else None,
        journal=nfc(record["journal"]) if record.get("journal") else None,
        year=int(year) if year not in (None, "") else None,
        pub_types=tuple(record.get("pub_types") or ()),
    )


def load_corpus(corpus_dir: Path) -> list[Document]:
    docs: list[Document] = []
    for name, kind in (("guidelines.jsonl", "guideline"), ("pubmed.jsonl", "abstract")):
        path = Path(corpus_dir) / name
        if not path.exists():
            raise FileNotFoundError(f"{path} not found; run scripts/fetch_cancer_corpus.py first")
        with path.open(encoding="utf-8") as f:
            docs.extend(parse_record(json.loads(line), kind=kind) for line in f if line.strip())  # type: ignore[arg-type]
    return docs


def dedupe_documents(docs: list[Document]) -> tuple[list[Document], DedupeReport]:
    """Drop an abstract whose guideline full text is in the corpus, then exact duplicates of normalised text,
    keeping the more trusted copy (then the lower id, so the result doesn't depend on file order)."""
    report = DedupeReport()
    guideline_pmids = {d.pmid for d in docs if d.kind == "guideline" and d.pmid}
    survivors = []
    for d in docs:
        if d.kind == "abstract" and d.pmid in guideline_pmids:
            report.pmid_dropped.append(d.id)
        else:
            survivors.append(d)

    best: dict[str, Document] = {}
    for d in survivors:
        key = normalise_for_hash(d.text)
        held = best.get(key)
        if held is None:
            best[key] = d
        elif (TRUST_RANK[d.trust], d.id) < (TRUST_RANK[held.trust], held.id):
            report.duplicate_dropped.append(held.id)
            best[key] = d
        else:
            report.duplicate_dropped.append(d.id)
    keep = {id(d) for d in best.values()}
    return [d for d in survivors if id(d) in keep], report
