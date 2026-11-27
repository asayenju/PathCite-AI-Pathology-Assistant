"""Chunk-level exact and near-duplicate dedupe (docs/PRD-corpus-ingest.md step 7, issue #44).

Lexical only: a fact checker must never lose "X is not recommended" because "X is recommended" looks alike."""

import hashlib
import random

import pytest

from app.corpus.config import ChunkerConfig
from app.corpus.documents import normalise_for_hash, parse_record
from app.corpus.local_embedder import HashingEmbedder
from app.corpus.plan import ChunkRecord, PlanMismatch, build_plan, dedupe_chunks, load_or_build_plan
from app.corpus.report import format_report

from .test_corpus_documents import guideline, write_corpus

CFG = ChunkerConfig()

# A NICE-style "Your responsibility" boilerplate paragraph (~210 words, like the real one).
BOILERPLATE = (
    "The recommendations in this guideline represent the view of the institute, arrived at after careful "
    "consideration of the evidence available. When exercising their judgement, professionals and practitioners "
    "are expected to take this guideline fully into account, alongside the individual needs, preferences and "
    "values of their patients or the people using their service. It is not mandatory to apply the "
    "recommendations, and the guideline does not override the responsibility to make decisions appropriate to "
    "the circumstances of the individual, in consultation with them and their families and carers or guardian. "
    "Local commissioners and providers of healthcare have a responsibility to enable the guideline to be "
    "applied when individual professionals and people using services wish to use it. They should do so in the "
    "context of local and national priorities for funding and developing services, and in light of their "
    "duties to have due regard to the need to eliminate unlawful discrimination, to advance equality of "
    "opportunity and to reduce health inequalities. Nothing in this guideline should be interpreted in a way "
    "that would be inconsistent with complying with those duties. Commissioners and providers have a "
    "responsibility to promote an environmentally sustainable health and care system and should assess and "
    "reduce the environmental impact of implementing the recommendations wherever possible, using the "
    "resources and tools that are made available to support them in doing so."
)
BOILERPLATE_ONE_WORD_CHANGED = BOILERPLATE.replace("careful consideration", "thorough consideration")

# ~60 words: one changed word mid-paragraph gives a 5-shingle Jaccard of about 0.84.
SHORT_PARAGRAPH = (
    "This guidance covers the diagnosis and management of colorectal cancer in adults. It aims to improve "
    "quality of life and survival by ensuring that people get the right tests and treatment, with prompt "
    "access to specialist care. Healthcare professionals should discuss the options with each person and "
    "record the decision in the notes so that everyone involved understands it."
)
SHORT_PARAGRAPH_ONE_WORD_CHANGED = SHORT_PARAGRAPH.replace("prompt access", "timely access")

POS = "Adjuvant chemotherapy is recommended for stage III colon cancer."
NEG = "Adjuvant chemotherapy is not recommended for stage III colon cancer."
LEAD = (
    "After surgical resection the multidisciplinary team should review the pathology report, including nodal "
    "status, margins and lymphovascular invasion, and discuss the findings with the person. "
)
TAIL = " Treatment should start within eight weeks of surgery where the person is fit enough to receive it."

LONG_FILLER = f"{BOILERPLATE} {SHORT_PARAGRAPH} {LEAD}{TAIL}"
"""~340 words of distinct text: one inserted word leaves a 5-shingle Jaccard of ~0.97, above the threshold."""


def chunk(content: str, doc_id: str = "g1", ord_: int = 0, *, trust: str = "high", source: str = "nice") -> ChunkRecord:
    return ChunkRecord(
        doc_id=doc_id, ord=ord_, section=f"Title {doc_id}", content=content, embed_text=content,
        content_hash=hashlib.sha256(normalise_for_hash(content).encode()).hexdigest(), token_count=len(content.split()),
        cancers=["colorectal"], source=source, trust=trust, url=f"https://example.org/{doc_id}", title=f"Title {doc_id}",
        pmid=None, journal=None, year=None, pub_types=[],
    )  # fmt: skip


def kept_contents(chunks: list[ChunkRecord], config: ChunkerConfig = CFG) -> list[str]:
    kept, _ = dedupe_chunks(chunks, config)
    return [c.content for c in kept]


def test_exact_duplicate_is_skipped_case_and_whitespace_insensitively():
    a = chunk(BOILERPLATE, "nice-a")
    b = chunk("  " + BOILERPLATE.upper().replace(" ", "\n  ", 3), "nice-b")
    kept, result = dedupe_chunks([a, b], CFG)
    assert [c.doc_id for c in kept] == ["nice-a"]
    assert (result.exact, result.near) == (1, 0)
    assert result.by_source == {"nice": 1}


def test_near_duplicate_boilerplate_with_one_word_changed_is_skipped():
    kept, result = dedupe_chunks([chunk(BOILERPLATE, "nice-a"), chunk(BOILERPLATE_ONE_WORD_CHANGED, "nice-b")], CFG)
    assert [c.doc_id for c in kept] == ["nice-a"]
    assert (result.exact, result.near) == (0, 1)
    [skip] = result.skips
    assert (skip.dropped_doc_id, skip.kept_doc_id, skip.kind) == ("nice-b", "nice-a", "near")
    assert skip.jaccard >= CFG.near_dup_threshold


def test_threshold_is_configurable():
    pair = [chunk(SHORT_PARAGRAPH, "a"), chunk(SHORT_PARAGRAPH_ONE_WORD_CHANGED, "b")]
    assert len(dedupe_chunks(pair, ChunkerConfig(near_dup_threshold=0.9))[0]) == 2  # Jaccard ~0.83
    assert len(dedupe_chunks(pair, ChunkerConfig(near_dup_threshold=0.8))[0]) == 1


def test_a_different_imaging_modality_in_a_short_chunk_is_kept():
    """Real corpus, WikiDoc: Jaccard exactly 0.900, so the PRD's 0.9 dropped it. Why the default is 0.95."""
    ct = ("CT scan can b helpful in the diagnosis of severe ulcerative colitis and in differentiating ulcerative "
          "colitis from Crohn's disease. Initial changes in the mucosa may not be detected by a CT scan.")  # fmt: skip
    mri = ct.replace("CT scan", "MRI", 1)
    pair = [chunk(ct, "w", 13, trust="low", source="wikidoc"), chunk(mri, "w", 14, trust="low", source="wikidoc")]
    assert len(dedupe_chunks(pair, ChunkerConfig(near_dup_threshold=0.9))[0]) == 1
    assert kept_contents(pair) == [ct, mri]


def test_nice_copy_beats_wikidoc_copy_whatever_the_input_order():
    wiki = chunk(BOILERPLATE_ONE_WORD_CHANGED, "a-wiki", trust="low", source="wikidoc")
    nice = chunk(BOILERPLATE, "z-nice", trust="high", source="nice")
    for order in ([wiki, nice], [nice, wiki]):
        kept, result = dedupe_chunks(order, CFG)
        assert [c.doc_id for c in kept] == ["z-nice"]
        assert result.by_source == {"wikidoc": 1}


def test_same_trust_keeps_the_lower_doc_id_then_the_lower_ord():
    kept, _ = dedupe_chunks([chunk(BOILERPLATE, "nice-b"), chunk(BOILERPLATE, "nice-a", 3)], CFG)
    assert [(c.doc_id, c.ord) for c in kept] == [("nice-a", 3)]
    kept, _ = dedupe_chunks([chunk(BOILERPLATE, "nice-a", 5), chunk(BOILERPLATE, "nice-a", 2)], CFG)
    assert [(c.doc_id, c.ord) for c in kept] == [("nice-a", 2)]


def test_surviving_chunks_keep_their_input_order():
    chunks = [chunk(f"Distinct chunk number {i} about a different topic entirely here.", "g", i) for i in range(5)]
    chunks.insert(2, chunk(chunks[4].content, "a", 0, trust="low", source="wikidoc"))
    kept, _ = dedupe_chunks(chunks, CFG)
    assert [(c.doc_id, c.ord) for c in kept] == [("g", 0), ("g", 1), ("g", 2), ("g", 3), ("g", 4)]


# The negation regression: both statements must survive, alone and inside otherwise identical paragraphs.


def test_negation_standalone_sentences_are_both_kept():
    assert kept_contents([chunk(POS, "nice-a"), chunk(NEG, "nice-b")]) == [POS, NEG]


def test_negation_inside_otherwise_identical_paragraphs_is_kept():
    pos, neg = LEAD + POS + TAIL, LEAD + NEG + TAIL
    assert 40 <= len(pos.split()) <= 60
    assert kept_contents([chunk(pos, "nice-a"), chunk(neg, "nice-b")]) == [pos, neg]


def test_negation_inside_a_long_chunk_is_kept_although_jaccard_is_above_threshold():
    pos, neg = LONG_FILLER + " " + POS, LONG_FILLER + " " + NEG
    kept, result = dedupe_chunks([chunk(pos, "nice-a"), chunk(neg, "nice-b")], CFG)
    assert [c.content for c in kept] == [pos, neg]
    assert result.guarded == 1  # lexically ~0.97 alike; only the guard keeps it


@pytest.mark.parametrize(
    ("a", "b"),
    [("stage III", "stage II"), ("5 years", "15 years"), ("tumours >= 2 cm", "tumours < 2 cm"),
     ("small cell lung cancer", "non-small cell lung cancer"), ("can be offered", "can't be offered"),
     ("with radiotherapy", "without radiotherapy"), ("should be offered", "should never be offered")],
)  # fmt: skip
def test_numbers_stages_comparisons_and_negations_are_guarded_in_long_chunks(a, b):
    x, y = f"{LONG_FILLER} Patients with {a} disease.", f"{LONG_FILLER} Patients with {b} disease."
    kept, result = dedupe_chunks([chunk(x, "nice-a"), chunk(y, "nice-b")], CFG)
    assert [c.content for c in kept] == [x, y]
    assert result.guarded == 1


def test_short_chunks_are_exact_only():
    assert kept_contents([chunk("Offer surgery.", "a"), chunk("offer  surgery.", "b")]) == ["Offer surgery."]
    assert kept_contents([chunk("Offer surgery now.", "a"), chunk("Offer surgery today.", "b")]) == [
        "Offer surgery now.", "Offer surgery today."]  # fmt: skip


def test_two_runs_give_identical_output_and_input_order_does_not_change_the_kept_set():
    rng = random.Random(7)
    chunks = []
    for i in range(40):
        base = BOILERPLATE if i % 3 else LEAD + POS + TAIL
        words = base.split()
        words[rng.randrange(len(words))] = "w" + "abcde"[rng.randrange(5)]
        trust, source = rng.choice([("high", "nice"), ("medium", "pubmed"), ("low", "wikidoc")])
        chunks.append(chunk(" ".join(words), f"d{rng.randrange(10)}", i, trust=trust, source=source))
    first, r1 = dedupe_chunks(chunks, CFG)
    second, r2 = dedupe_chunks(chunks, CFG)
    assert first == second and r1 == r2
    shuffled = chunks[:]
    rng.shuffle(shuffled)
    third, r3 = dedupe_chunks(shuffled, CFG)
    assert sorted((c.doc_id, c.ord) for c in third) == sorted((c.doc_id, c.ord) for c in first)
    assert (r3.exact, r3.near, r3.by_source) == (r1.exact, r1.near, r1.by_source)


# Through the plan: ords, empty documents, stats, report and the cache.


def plan_of(records: list[dict], config: ChunkerConfig = CFG):
    return build_plan([parse_record(r, kind="guideline") for r in records], HashingEmbedder(), config,
                      embedder_id="test-hash")  # fmt: skip


def test_plan_drops_duplicates_leaving_ord_gaps_and_counts_emptied_documents():
    other = "Offer radiotherapy to people who cannot have surgery because of frailty or other serious conditions."
    plan = plan_of([
        guideline("nice-a", text=f"# About\n{BOILERPLATE}"),
        guideline("wiki-b", source="wikidoc", text=f"# Intro\n{BOILERPLATE_ONE_WORD_CHANGED}\n# Treatment\n{other}"),
        guideline("wiki-c", source="wikidoc", text=f"# Intro\n{BOILERPLATE.upper()}"),
    ])  # fmt: skip
    assert [(c.doc_id, c.ord) for c in plan.chunks] == [("nice-a", 0), ("wiki-b", 1)]
    s = plan.stats
    assert (s.chunk_dedupe_skipped, s.chunk_dedupe_exact, s.chunk_dedupe_near) == (2, 1, 1)
    assert s.chunk_dedupe_by_source == {"wikidoc": 2}
    assert s.documents_without_chunks == 1  # wiki-c lost its only chunk; ingest never sees an empty document
    assert s.chunks == 2
    report = format_report(s, approximate=True)
    assert "not run" not in report
    assert "2 skipped (1 exact, 1 near-duplicate; wikidoc 2)" in report


def test_near_dup_threshold_is_part_of_the_config_hash_and_invalidates_a_cached_plan(tmp_path):
    assert ChunkerConfig(near_dup_threshold=0.9).config_hash() != CFG.config_hash()
    write_corpus(tmp_path, [guideline("g1", text=f"# About\n{BOILERPLATE}")], [])
    load_or_build_plan(tmp_path, HashingEmbedder(), embedder_id="test-hash", config=CFG, rechunk=False)
    with pytest.raises(PlanMismatch):
        load_or_build_plan(tmp_path, HashingEmbedder(), embedder_id="test-hash",
                           config=ChunkerConfig(near_dup_threshold=0.9), rechunk=False)  # fmt: skip
