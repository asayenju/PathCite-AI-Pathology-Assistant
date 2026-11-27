"""The chunk plan: records, the plan file with its config hash, --rechunk, the dry-run report and the CLI."""

import hashlib
import json
import random
import time

import pytest

from app.corpus import cli
from app.corpus.chunking import count_tokens
from app.corpus.config import ChunkerConfig
from app.corpus.documents import normalise_for_hash, parse_record
from app.corpus.local_embedder import HashingEmbedder
from app.corpus.plan import PlanMismatch, build_plan, load_or_build_plan, read_plan, write_plan
from app.corpus.report import estimate_cost_usd, estimate_mb, format_report

from .test_corpus_chunking import MELANOMA, THYROID, topic_sentences
from .test_corpus_documents import abstract, guideline, write_corpus

CFG = ChunkerConfig()
EMB = HashingEmbedder()


def plan_of(records: list[tuple[dict, str]], config: ChunkerConfig = CFG):
    docs = [parse_record(r, kind=k) for r, k in records]
    return build_plan(docs, EMB, config, embedder_id="test-hash")


def test_chunk_record_fields_for_a_guideline():
    text = "# Staging\n" + " ".join(topic_sentences(MELANOMA, 4, seed=1))
    plan = plan_of([(guideline("g1", source="pubmed", text=text, pmid="99"), "guideline")])
    [c] = plan.chunks
    assert c.doc_id == "g1" and c.ord == 0
    assert c.section == "Title g1 › Staging"
    assert c.embed_text == c.section + "\n\n" + c.content
    assert c.content_hash == hashlib.sha256(normalise_for_hash(c.content).encode()).hexdigest()
    assert c.token_count == count_tokens(c.content)
    assert (c.url, c.title, c.pmid, c.year, c.pub_types) == ("https://example.org/g1", "Title g1", "99", None, [])
    assert (c.cancers, c.source, c.trust) == (["melanoma"], "pubmed", "high")


def test_labelled_abstract_is_one_chunk_named_after_its_title():
    text = ("BACKGROUND: Melanoma incidence is rising worldwide. METHODS: We reviewed 40 trials of adjuvant therapy. "
            "RESULTS: Relapse-free survival improved. CONCLUSIONS: Adjuvant therapy is recommended for stage III.")  # fmt: skip
    plan = plan_of([(abstract("5", text=text), "abstract")])
    [c] = plan.chunks
    assert c.section == "Abstract 5"
    assert c.content == text
    assert (c.pmid, c.year, c.pub_types, c.trust, c.source) == ("5", 2023, ["Review", "Journal Article"], "medium", "pubmed")


def test_merging_never_crosses_a_heading():
    text = "# A\nOffer surgery to people with stage I disease after discussion at the multidisciplinary team meeting.\n# B\nOffer radiotherapy to people who cannot have surgery because of frailty or other conditions."
    plan = plan_of([(guideline("g1", text=text), "guideline")])
    assert [c.section.split(" › ")[-1] for c in plan.chunks] == ["A", "B"]
    assert [c.ord for c in plan.chunks] == [0, 1]


def test_dedupe_and_boilerplate_are_counted_in_stats():
    docs = [(guideline("g1", source="pubmed", pmid="5", text="# Recommendations\nOffer surgery to everyone who is eligible after review by the team.\n"
                       "# Cost effectiveness\nThe ICER was £20,000 per QALY gained."), "guideline"),
            (guideline("g2", source="wikidoc", text="# Recommendations\nOffer surgery to everyone who is eligible after review by the team.\n"
                       "# Cost effectiveness\nThe ICER was £20,000 per QALY gained."), "guideline"),
            (abstract("5"), "abstract")]  # fmt: skip
    s = plan_of(docs).stats
    assert (s.documents_loaded, s.pmid_dropped, s.duplicate_dropped, s.documents) == (3, 1, 1, 1)
    assert s.sections_dropped == 1
    assert s.sections_dropped_chars == len("The ICER was £20,000 per QALY gained.")
    assert s.chunks == 1


def test_180k_character_document_stays_within_bounds_and_is_fast():
    rng = random.Random(0)
    parts = []
    while sum(map(len, parts)) < 180_000:
        level = "#" * rng.randint(1, 4)
        parts.append(f"{level} Heading {len(parts)}\n")
        n = rng.choice([1, 3, 10, 60, 150])
        parts.append("\n".join(topic_sentences(rng.choice([MELANOMA, THYROID]), n, seed=len(parts))) + "\n")
    text = "".join(parts)
    assert len(text) >= 180_000
    t0 = time.perf_counter()
    plan = plan_of([(guideline("big", text=text), "guideline")])
    assert time.perf_counter() - t0 < 60
    assert plan.chunks
    for c in plan.chunks:
        assert CFG.min_tokens <= c.token_count <= CFG.max_tokens
        assert count_tokens(c.embed_text) < 8191
        assert c.url and c.section
    assert [c.ord for c in plan.chunks] == list(range(len(plan.chunks)))


def test_config_hash_changes_with_the_config():
    assert ChunkerConfig().config_hash() == ChunkerConfig().config_hash()
    assert ChunkerConfig(max_tokens=400).config_hash() != ChunkerConfig().config_hash()


def test_plan_file_round_trip_with_meta_line(tmp_path):
    plan = plan_of([(guideline("g1", text="# A\nOffer surgery to people with stage I disease after discussion at the multidisciplinary team meeting."), "guideline")])
    path = tmp_path / "chunks.jsonl"
    write_plan(path, plan)
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert first["meta"]["chunker_config_hash"] == CFG.config_hash()
    assert first["meta"]["breakpoint_embedder"] == "test-hash"
    again = read_plan(path)
    assert again.chunks == plan.chunks
    assert again.stats == plan.stats


def test_existing_plan_is_reused_unless_rechunk(tmp_path):
    write_corpus(tmp_path, [guideline("g1", text="# A\nOffer surgery to people with stage I disease after discussion at the multidisciplinary team meeting.")], [])
    plan, reused = load_or_build_plan(tmp_path, EMB, embedder_id="test-hash", config=CFG, rechunk=False)
    assert not reused and (tmp_path / "chunks.jsonl").exists()
    write_corpus(tmp_path, [guideline("g1", text="# A\nCompletely different text that is long enough to be kept as a chunk here.")], [])
    again, reused = load_or_build_plan(tmp_path, EMB, embedder_id="test-hash", config=CFG, rechunk=False)
    assert reused and again.chunks == plan.chunks  # the cached boundaries win
    rebuilt, reused = load_or_build_plan(tmp_path, EMB, embedder_id="test-hash", config=CFG, rechunk=True)
    assert not reused and "Completely different" in rebuilt.chunks[0].content


def test_plan_with_another_config_or_embedder_is_refused_without_rechunk(tmp_path):
    write_corpus(tmp_path, [guideline("g1", text="# A\nOffer surgery to people with stage I disease after discussion at the multidisciplinary team meeting.")], [])
    load_or_build_plan(tmp_path, EMB, embedder_id="test-hash", config=CFG, rechunk=False)
    with pytest.raises(PlanMismatch, match="--rechunk"):
        load_or_build_plan(tmp_path, EMB, embedder_id="test-hash", config=ChunkerConfig(max_tokens=400), rechunk=False)
    with pytest.raises(PlanMismatch, match="--rechunk"):
        load_or_build_plan(tmp_path, EMB, embedder_id="openai", config=CFG, rechunk=False)


def test_estimates():
    assert estimate_cost_usd(1_000_000) == pytest.approx(0.02)
    assert estimate_mb(1000, 500) == pytest.approx(1000 * (1536 * 4 + 500 + 1024) * 2 / 1e6)


def test_report_mentions_every_number_and_approximate_boundaries():
    plan = plan_of([(guideline("g1", text="# A\nOffer surgery to people with stage I disease after discussion at the multidisciplinary team meeting."), "guideline")])
    out = format_report(plan.stats, approximate=True)
    for label in ["Documents", "Sections dropped", "Chunks", "Tokens", "Estimated cost", "Estimated size",
                  "approximate", "chunk dedupe     0 skipped"]:  # fmt: skip
        assert label in out, label


def test_cli_dry_run_prints_the_report_and_writes_no_real_plan(tmp_path, capsys):
    write_corpus(tmp_path, [guideline("g1", text="# A\nOffer surgery to people with stage I disease after discussion at the multidisciplinary team meeting.")],
                 [abstract("1", text="BACKGROUND: Something about melanoma. CONCLUSIONS: Something else entirely.")])  # fmt: skip
    assert cli.main(["--dry-run", "--corpus-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Chunks" in out and "approximate" in out
    assert not (tmp_path / "chunks.jsonl").exists()  # the stand-in embedder never writes the real plan
    assert (tmp_path / "chunks.dry-run.jsonl").exists()


def test_cli_dry_run_reads_an_existing_real_plan(tmp_path, capsys):
    write_corpus(tmp_path, [guideline("g1", text="# A\nOffer surgery to people with stage I disease after discussion at the multidisciplinary team meeting.")], [])
    load_or_build_plan(tmp_path, EMB, embedder_id="openai:text-embedding-3-small", config=CFG, rechunk=False)
    assert cli.main(["--dry-run", "--corpus-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "existing plan" in out and "approximate" not in out


def test_cli_without_dry_run_is_issue_45(tmp_path):
    write_corpus(tmp_path, [guideline("g1")], [])
    with pytest.raises(NotImplementedError, match="#45"):
        cli.main(["--corpus-dir", str(tmp_path)])
