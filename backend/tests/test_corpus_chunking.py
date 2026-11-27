"""Semantic split and size limits inside one section (PRD step 5), with a deterministic offline embedder."""

import random

import pytest

from app.corpus.chunking import chunk_section, count_tokens
from app.corpus.config import ChunkerConfig
from app.corpus.local_embedder import HashingEmbedder

CFG = ChunkerConfig()

MELANOMA = ("melanoma breslow sentinel node biopsy excision margin dermoscopy nivolumab ipilimumab braf "
            "ulceration mitotic satellite nodal staging pembrolizumab dabrafenib").split()  # fmt: skip
THYROID = ("thyroid nodule ultrasound fine needle aspiration bethesda papillary follicular thyroidectomy "
           "radioiodine thyroglobulin levothyroxine calcitonin medullary lobectomy").split()  # fmt: skip


class RecordingEmbedder:
    """Wraps the hashing embedder and records every input, so tests can check what would be sent to OpenAI."""

    def __init__(self) -> None:
        self.inner = HashingEmbedder()
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return self.inner.embed(texts)


def topic_sentences(vocab: list[str], n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return [" ".join(rng.choice(vocab) for _ in range(9)).capitalize() + "." for _ in range(n)]


def test_short_section_is_one_chunk_without_embedding():
    emb = RecordingEmbedder()
    text = "Offer wide local excision. Use a 1 cm margin for stage I disease and review at 3 months."
    assert chunk_section(text, emb, CFG) == [text]
    assert emb.calls == []


def test_section_under_the_minimum_is_dropped():
    assert chunk_section("See below.", RecordingEmbedder(), CFG) == []


def test_long_section_is_cut_where_the_topic_changes():
    a = topic_sentences(MELANOMA, 20, seed=1)
    b = topic_sentences(THYROID, 20, seed=2)
    text = " ".join(a + b)
    assert count_tokens(text) > CFG.max_tokens
    chunks = chunk_section(text, RecordingEmbedder(), CFG)
    assert chunks == [" ".join(a), " ".join(b)]


def test_chunks_are_verbatim_slices_within_bounds_and_cover_the_text_in_order():
    rng = random.Random(0)
    sentences = topic_sentences(MELANOMA + THYROID, 400, seed=3)
    text = "\n".join(" ".join(sentences[i : i + rng.randint(1, 6)]) for i in range(0, 400, 6))
    chunks = chunk_section(text, RecordingEmbedder(), CFG)
    pos = 0
    for c in chunks:
        assert CFG.min_tokens <= count_tokens(c) <= CFG.max_tokens
        found = text.index(c, pos)
        assert text[pos:found].strip() == ""  # nothing skipped between chunks
        pos = found + len(c)
    assert text[pos:].strip() == ""


def test_small_pieces_are_merged_into_a_neighbour():
    a = topic_sentences(MELANOMA, 45, seed=4)  # over 512 tokens on its own
    b = topic_sentences(THYROID, 3, seed=5)  # a short off-topic tail
    chunks = chunk_section(" ".join(a + b), RecordingEmbedder(), CFG)
    assert all(count_tokens(c) >= CFG.min_merge_tokens for c in chunks)


def test_a_single_sentence_over_the_limit_is_cut_at_word_boundaries():
    words = [random.Random(6).choice(MELANOMA) for _ in range(1500)]
    text = " ".join(words)  # no punctuation at all
    chunks = chunk_section(text, RecordingEmbedder(), CFG)
    assert len(chunks) >= 3
    assert all(CFG.min_tokens <= count_tokens(c) <= CFG.max_tokens for c in chunks)
    assert " ".join(chunks).split() == words


PROTECTED = [
    "Offer a targeted agent, e.g. Dabrafenib with trametinib, to people with stage IV disease.",
    "Assess margins histologically, i.e. Not by eye alone, before closing the wound.",
    "Compare adjuvant nivolumab vs. Observation in resected stage III melanoma patients.",
    "Dr. Patel recommended sentinel node biopsy for all stage IIB and IIC tumours.",
    "The excision margins are summarised in Fig. 3 of the full guideline document.",
    "A Breslow thickness of 1.2 mm upstages the tumour to pT2a without ulceration.",
    "The protocol was drafted by A. B. Jones and colleagues at the national centre.",
]


@pytest.mark.parametrize("seed", range(5))
def test_never_cut_at_abbreviations_decimals_or_initials(seed):
    rng = random.Random(seed)
    filler = topic_sentences(MELANOMA, 120, seed=seed)
    sentences = filler + PROTECTED * 3
    rng.shuffle(sentences)
    text = " ".join(sentences)
    chunks = chunk_section(text, RecordingEmbedder(), CFG)
    assert len(chunks) > 1
    for sentence in PROTECTED:
        assert sum(c.count(sentence) for c in chunks) == text.count(sentence), sentence  # never cut
    for c in chunks:
        assert not c.endswith(("e.g.", "i.e.", "vs.", "Dr.", "Fig.", " 1.", " A.", " B."))


def test_a_list_that_fits_stays_in_one_chunk_with_its_lead_in():
    before = " ".join(topic_sentences(MELANOMA, 20, seed=7))
    items = "\n".join(f"- {s}" for s in topic_sentences(THYROID, 8, seed=8))
    after = " ".join(topic_sentences(MELANOMA, 20, seed=9))
    lead_in = "Consider the following options:"
    text = f"{before}\n{lead_in}\n{items}\n{after}"
    assert count_tokens(text) > CFG.max_tokens
    chunks = chunk_section(text, RecordingEmbedder(), CFG)
    assert sum(f"{lead_in}\n{items}" in c for c in chunks) == 1


def test_list_only_section_over_the_limit_is_cut_between_items():
    items = [f"- {s}" for s in topic_sentences(MELANOMA + THYROID, 80, seed=10)]
    text = "\n".join(items)
    chunks = chunk_section(text, RecordingEmbedder(), CFG)
    assert len(chunks) > 1
    for c in chunks:
        assert count_tokens(c) <= CFG.max_tokens
        assert all(line in items for line in c.split("\n"))


def test_embedder_only_sees_non_empty_text_from_the_section():
    emb = RecordingEmbedder()
    text = " ".join(topic_sentences(MELANOMA, 60, seed=11))
    chunk_section(text, emb, CFG)
    sent = [t for call in emb.calls for t in call]
    assert sent and all(t.strip() and t in text for t in sent)


def test_wrong_number_of_vectors_from_the_embedder_is_rejected():
    class Broken:
        def embed(self, texts):
            return [[1.0, 0.0]]

    with pytest.raises(ValueError, match="embedder"):
        chunk_section(" ".join(topic_sentences(MELANOMA, 60, seed=12)), Broken(), CFG)


def test_hashing_embedder_is_deterministic_and_normalised():
    e = HashingEmbedder()
    v1, v2 = e.embed(["Melanoma staging.", "Melanoma staging."])
    assert v1 == v2
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-9
    assert e.embed(["..."])[0] == [0.0] * e.dims  # no words: a zero vector, not a crash
