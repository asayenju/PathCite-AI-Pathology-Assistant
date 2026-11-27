"""Corpus loading, NFC, trust levels and document-level dedupe (docs/PRD-corpus-ingest.md steps 1-2)."""

import json
import unicodedata
from pathlib import Path

import pytest

from app.corpus.documents import dedupe_documents, load_corpus, normalise_for_hash, parse_record


def guideline(id_: str, source: str = "nice", text: str = "Some guideline text.", **extra) -> dict:
    return {"id": id_, "source": source, "title": f"Title {id_}", "url": f"https://example.org/{id_}", "text": text,
            "cancers": ["melanoma"], **extra}  # fmt: skip


def abstract(pmid: str, text: str = "BACKGROUND: Something. CONCLUSIONS: Something else.", **extra) -> dict:
    return {"id": f"pubmed:{pmid}", "source": "pubmed", "title": f"Abstract {pmid}",
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "journal": "JAMA", "year": "2023",
            "pub_types": ["Review", "Journal Article"], "text": text, "cancers": ["breast"], **extra}  # fmt: skip


def write_corpus(dir_: Path, guidelines: list[dict], abstracts: list[dict]) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "guidelines.jsonl").write_text("".join(json.dumps(r) + "\n" for r in guidelines), encoding="utf-8")
    (dir_ / "pubmed.jsonl").write_text("".join(json.dumps(r) + "\n" for r in abstracts), encoding="utf-8")
    return dir_


@pytest.mark.parametrize(
    ("source", "kind", "trust"),
    [("nice", "guideline", "high"), ("pubmed", "guideline", "high"), ("wikidoc", "guideline", "low"),
     ("pubmed", "abstract", "medium")],
)  # fmt: skip
def test_trust_levels(source, kind, trust):
    record = guideline("g1", source=source) if kind == "guideline" else abstract("1")
    assert parse_record(record, kind=kind).trust == trust


def test_unknown_guideline_source_is_rejected():
    with pytest.raises(ValueError, match="source"):
        parse_record(guideline("g1", source="blog"), kind="guideline")


def test_abstract_fields_pmid_from_id_year_as_int_and_pub_types():
    doc = parse_record(abstract("37698559"), kind="abstract")
    assert doc.pmid == "37698559"
    assert doc.year == 2023
    assert doc.pub_types == ("Review", "Journal Article")
    assert doc.journal == "JAMA"
    assert doc.cancers == ("breast",)


def test_guideline_keeps_its_linked_pmid_and_has_no_year():
    doc = parse_record(guideline("g1", source="pubmed", pmid="123"), kind="guideline")
    assert doc.pmid == "123"
    assert doc.year is None
    assert doc.pub_types == ()


def test_text_and_title_are_nfc_normalised():
    decomposed = "Café au lait spots"  # e + combining acute
    record = guideline("g1", text=decomposed)
    record["title"] = decomposed
    doc = parse_record(record, kind="guideline")
    assert doc.text == unicodedata.normalize("NFC", decomposed) == "Café au lait spots"
    assert doc.title == "Café au lait spots"


def test_non_ascii_hyphen_survives_in_text_but_not_in_the_hash():
    doc = parse_record(guideline("g1", text="BRAF‐mutant melanoma"), kind="guideline")
    assert "‐" in doc.text  # the source text is not rewritten
    assert normalise_for_hash("BRAF‐mutant  Melanoma") == normalise_for_hash("BRAF-mutant melanoma")


def test_load_corpus_reads_both_files(tmp_path):
    corpus = write_corpus(tmp_path, [guideline("g1"), guideline("g2", source="wikidoc")], [abstract("1")])
    docs = load_corpus(corpus)
    assert [d.id for d in docs] == ["g1", "g2", "pubmed:1"]
    assert [d.kind for d in docs] == ["guideline", "guideline", "abstract"]


def test_load_corpus_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="fetch_cancer_corpus"):
        load_corpus(tmp_path)


def test_abstract_with_the_pmid_of_a_guideline_is_dropped():
    docs = [parse_record(guideline("g1", source="pubmed", pmid="42"), kind="guideline"),
            parse_record(abstract("42", text="Different abstract text."), kind="abstract"),
            parse_record(abstract("43"), kind="abstract")]  # fmt: skip
    kept, report = dedupe_documents(docs)
    assert [d.id for d in kept] == ["g1", "pubmed:43"]
    assert report.pmid_dropped == ["pubmed:42"]
    assert report.duplicate_dropped == []


def test_exact_duplicate_text_keeps_the_more_trusted_copy():
    text = "Colorectal cancer is a common cancer."
    docs = [parse_record(guideline("wiki", source="wikidoc", text=text), kind="guideline"),
            parse_record(guideline("nice", source="nice", text=text.upper() + "  "), kind="guideline"),
            parse_record(abstract("7", text=text), kind="abstract")]  # fmt: skip
    kept, report = dedupe_documents(docs)
    assert [d.id for d in kept] == ["nice"]
    assert sorted(report.duplicate_dropped) == ["pubmed:7", "wiki"]


def test_exact_duplicates_of_equal_trust_keep_the_lower_id():
    docs = [parse_record(guideline("b", text="Same text."), kind="guideline"),
            parse_record(guideline("a", text="Same text."), kind="guideline")]  # fmt: skip
    kept, report = dedupe_documents(docs)
    assert [d.id for d in kept] == ["a"]
    assert report.duplicate_dropped == ["b"]


def test_same_title_different_text_is_not_a_duplicate():
    docs = [parse_record(guideline("n", source="nice", text="NICE text."), kind="guideline"),
            parse_record(guideline("w", source="wikidoc", text="WikiDoc text."), kind="guideline")]  # fmt: skip
    docs = [d.__class__(**{**d.__dict__, "title": "Colorectal cancer"}) for d in docs]
    kept, _ = dedupe_documents(docs)
    assert [d.id for d in kept] == ["n", "w"]
