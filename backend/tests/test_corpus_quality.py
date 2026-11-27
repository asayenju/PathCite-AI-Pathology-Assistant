"""Quality filter (issue #46): each rule drops what it should and keeps its near misses; its config hash is separate
from the chunker's; the plan-level counts. The ingest side (--prune, corpus_meta) is in test_corpus_ingest.py."""

import hashlib
from dataclasses import replace

import pytest

from app.corpus.chunking import count_tokens
from app.corpus.config import ChunkerConfig
from app.corpus.plan import ChunkRecord, Plan, PlanStats
from app.corpus.quality import QualityFilterConfig, apply_quality_filter, format_quality_report, removal_rule

CLINICAL = ("Offer sentinel lymph node biopsy to people with stage IB to IIC melanoma who have a Breslow thickness of "
            "more than 1 mm, after discussing the benefits and risks.")  # fmt: skip


def chunk(content: str = CLINICAL, *, source: str = "nice", title: str = "Melanoma: assessment and management",
          headings: tuple[str, ...] = ("Staging",), doc_id: str | None = None, ord: int = 0) -> ChunkRecord:  # fmt: skip
    section = " › ".join((title, *headings))
    return ChunkRecord(
        doc_id=doc_id or f"{source}:{title}", ord=ord, section=section, content=content,
        embed_text=f"{section}\n\n{content}", content_hash=hashlib.sha256(content.encode()).hexdigest(),
        token_count=count_tokens(content), cancers=["melanoma"], source=source,
        trust={"nice": "high", "pubmed": "high", "wikidoc": "low"}[source], url="https://example.org/x", title=title,
        pmid=None, journal=None, year=None, pub_types=[],
    )  # fmt: skip


# --- sandbox -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("title", ["Sandbox: Breast Abscess", "Sandbox breast cancer staging", "sandbox"])
def test_sandbox_wikidoc_pages_are_dropped(title):
    assert removal_rule(chunk(source="wikidoc", title=title, headings=("Overview",))) == "sandbox"


@pytest.mark.parametrize("title", ["Breast cancer (patient information)", "Lymphoma (patient information)",
                                   "Sand fly fever", "Breast abscess"])  # fmt: skip
def test_patient_information_and_other_wikidoc_pages_are_kept(title):
    assert removal_rule(chunk(source="wikidoc", title=title, headings=("What are the symptoms?",))) is None


def test_sandbox_rule_is_wikidoc_only():
    assert removal_rule(chunk(source="nice", title="Sandbox trial results", headings=("Results",))) is None


# --- NICE economic modelling ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("headings", [
    ("Cost effectiveness evidence", "The company's economic model"),
    ("Utility values",),
    ("The company's utility values are broadly appropriate",),
    ("Comparators",),
    ("Treatment pathway and comparators",),
    ("Decision problem",),
    ("Survival extrapolations",),
    ("In TITAN, extrapolating overall survival using a Weibull model is acceptable for decision making",),
    ("End of life",),
    ("Other factors", "End‑of‑life considerations"),
    ("Cancer Drugs Fund reconsideration",),
    ("Cancer Drugs Fund", "Selpercatinib is recommended for the Cancer Drugs Fund"),
    ("Cancer Drugs Fund reconsideration", "Comparators"),
    ("Cancer Drugs Fund reconsideration of TA295", "Probabilistic ICERs"),
    ("Managed access",),
    ("Severity", "QALY weighting"),
    ("Severity modifier",),
    ("Resource use",),
    ("Innovation",),
    ("Equality issues",),
    ("Survival modelling",),
    ("Model details",),
    ("The assessment group's model is preferred for decision-making",),
])  # fmt: skip
def test_nice_economic_sections_are_dropped(headings):
    assert removal_rule(chunk(headings=headings)) == "nice_economic"


@pytest.mark.parametrize("headings", [
    ("Clinical effectiveness", "Overall survival"),
    ("Clinical-effectiveness evidence", "CheckMate 067 trial results"),
    ("Clinical effectiveness", "Comparators"),  # a comparator under clinical evidence
    ("Appropriate comparators", "Nab-paclitaxel has similar efficacy to weekly paclitaxel and docetaxel"),
    ("Indirect treatment comparison", "Progression-free survival estimates for comparators in the network"),
    ("Clinical utility",),
    ("Trial results", "Adverse events"),
    ("Severity of disease",),
    ("Prediction modelling studies",),
    ("Managing stage IV and unresectable stage III melanoma",),
    # A Cancer Drugs Fund reconsideration is a whole re-appraisal: its clinical parts stay.
    ("Cancer Drugs Fund reconsideration", "Clinical management of HER2-positive advanced breast cancer"),
    ("Cancer Drugs Fund reconsideration", "Patient experience"),
    ("New data for the Cancer Drugs Fund review of population3", "Rates of overall survival after transplant"),
])  # fmt: skip
def test_nice_clinical_sections_are_kept(headings):
    assert removal_rule(chunk(headings=headings)) is None


def test_nice_economic_rule_reads_headings_not_the_title_or_content():
    title = "Care of dying adults in the last days of life: end of life care"
    assert removal_rule(chunk(title=title, headings=("Recognising when a person may be in the last days",))) is None
    text = "The company's economic model used utility values from the trial; end of life criteria were met."
    assert removal_rule(chunk(text, headings=("Clinical effectiveness",))) is None


def test_nice_economic_rule_is_nice_only():
    assert removal_rule(chunk(source="pubmed", title="ESMO guideline", headings=("Comparators",))) is None


# --- research recommendations and questions ------------------------------------------------------------------------


@pytest.mark.parametrize("headings", [
    ("Research recommendations", "Biomarkers"),
    ("Key recommendations for research", "Surveillance strategies"),
    ("Recommendations for further research",),
    ("Introduction", "Research questions"),
])  # fmt: skip
def test_research_sections_are_dropped(headings):
    assert removal_rule(chunk(headings=headings)) == "research"


@pytest.mark.parametrize("content", [
    "What is the optimal histological excision margin in stage 0 melanoma?",
    "- How often should surveillance imaging be done?\n- Which imaging modality should be used?",
])  # fmt: skip
def test_short_question_only_chunks_are_dropped(content):
    assert removal_rule(chunk(content, source="pubmed", title="Lynch syndrome", headings=("Question no 2",))) == (
        "research"
    )


@pytest.mark.parametrize("content", [
    "What is the role of aspirin in Lynch syndrome? Aspirin 600 mg daily reduces colorectal cancer incidence.",
    "Should all patients have MRI? " * 12,  # all questions but 40+ tokens: a long list is left alone
])  # fmt: skip
def test_questions_with_answers_or_long_lists_are_kept(content):
    assert removal_rule(chunk(content, headings=("Surveillance",))) is None


def test_a_heading_mentioning_research_in_another_sense_is_kept():
    assert removal_rule(chunk(headings=("Research evidence on sentinel node biopsy",))) is None


# --- cross-reference stubs -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("content", [
    "Recommendations 1.2.1 to 1.2.8",
    "Recommendation 1.3.4",
    "See 1.2",
    "Recommendations 1.3.26 and 1.3.27 and recommendation 1.5.6",
    "Recommendations 1.8.3 and 1.8.6 to 1.8.16.",
    "See recommendations 1.4.1–1.4.3",
])  # fmt: skip
def test_cross_reference_stubs_are_dropped(content):
    assert removal_rule(chunk(content, headings=("Staging",))) == "cross_reference"


@pytest.mark.parametrize("content", [
    "Offer imaging as described in recommendation 1.2.3 to people with stage IIC melanoma.",
    "Recommendation 1.2 applies to people with stage III disease who have had surgery.",
    "12.5",
    "See the NICE guideline on suspected cancer for referral criteria.",
])  # fmt: skip
def test_text_mentioning_a_recommendation_is_kept(content):
    assert removal_rule(chunk(content, headings=("Staging",))) is None


# --- WikiDoc navigation --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("content", [
    "For the WikiDoc page for this topic, click here",
    "To review the wikidoc page on thyroid cancer, click here.",
    "Colorectal cancer overview\nTo view the overview of familial adenomatous polyposis (FAP), click here\n"
    "To view the overview of hereditary nonpolyposis colorectal cancer (HNPCC), click here",
])  # fmt: skip
def test_wiki_navigation_chunks_are_dropped(content):
    assert removal_rule(chunk(content, source="wikidoc", title="Colorectal cancer", headings=())) == (
        "wiki_navigation"
    )


def test_wiki_chunk_with_real_text_besides_click_here_is_kept():
    content = ("Organ specific endoscopic tissue biopsy is diagnostic of MALT lymphoma and shows a dense lymphoid "
               "infiltrate with lymphoepithelial lesions. To view findings on biopsy, click here.")  # fmt: skip
    assert removal_rule(chunk(content, source="wikidoc", title="MALT lymphoma", headings=("Biopsy",))) is None


def test_wiki_navigation_rule_is_wikidoc_only():
    assert removal_rule(chunk("For the full guideline, click here", source="nice")) is None


# --- garbled tables ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("content", [
    "to 100.0\nto 65.1\nto 100.0\nto 33.4\nBarnetson etal. 2006\nto 82.7\nto 95.2\nto 99.2\nto 88.2",
    "Hazard ratio (95%CI)\n(0.65 to 0.87); p<0.001\n(0.65 to 0.87); p<0.001\n(0.65 to 0.89); p=0.001\n"
    "(0.61 to 0.84); p<0.001\n(0.57 to 0.82); p<0.001",
])  # fmt: skip
def test_garbled_tables_are_dropped(content):
    assert removal_rule(chunk(content, headings=("Clinical effectiveness",))) == "garbled_table"


@pytest.mark.parametrize("content", [
    # Readable results sentences, letters under half of all characters but in one long line.
    "Volume reduction rate increased after 1 month (SMD = 0.453, 95% CI: 0.323-0.583, p < .001), 3 months "
    "(SMD = 0.655, 95% CI: 0.563-0.747, p < .001), 6 months (SMD = 0.691, 95% CI: 0.607-0.774, p < .001).",
    "Median OS was 12.3 months (95% CI 10.1-14.5) vs 9.8 months (HR 0.72, 95% CI 0.61-0.85; p<0.001).",
    "12.5 mg/m2 on days 1, 8, 15",  # short: under 50 characters
])  # fmt: skip
def test_numeric_heavy_readable_text_is_kept(content):
    assert removal_rule(chunk(content, headings=("Clinical effectiveness",))) is None


def test_ordinary_clinical_text_is_kept():
    assert removal_rule(chunk()) is None


# --- config hash, plan filtering and report ------------------------------------------------------------------------


def test_filter_hash_is_independent_of_the_chunker_config_hash():
    base = QualityFilterConfig()
    changed = replace(base, question_max_tokens=30)
    assert base.filter_hash() != changed.filter_hash()
    assert len(base.filter_hash()) == 64
    # The chunker config (and so the cached plan) knows nothing about the filter.
    assert "question_max_tokens" not in str(ChunkerConfig())
    assert ChunkerConfig().config_hash() == ChunkerConfig().config_hash()


def _plan(chunks: list[ChunkRecord]) -> Plan:
    meta = {"kind": "chunk_plan", "chunker_config_hash": "abc", "config": {"encoding": "cl100k_base"}, "stats": {}}
    stats = PlanStats(documents=len({c.doc_id for c in chunks}), chunks=len(chunks))
    return Plan(meta, chunks, stats)


def test_apply_filter_counts_by_rule_and_source_keeps_ords_and_empties_documents():
    chunks = [
        chunk(doc_id="nice:ta1", ord=0, headings=("Clinical effectiveness",)),
        chunk(doc_id="nice:ta1", ord=1, headings=("End of life",)),
        chunk(doc_id="nice:ta1", ord=2, headings=("Innovation",)),
        chunk(doc_id="nice:ta1", ord=3, headings=("Conclusion",)),
        chunk("Recommendations 1.2.1 to 1.2.8", doc_id="nice:ng1", ord=0),
        chunk(doc_id="nice:ng1", ord=1),
        chunk(source="wikidoc", title="Sandbox: Breast Abscess", doc_id="wikidoc:sb", ord=0),
        chunk(source="wikidoc", title="Sandbox: Breast Abscess", doc_id="wikidoc:sb", ord=1),
        chunk("What is the role of aspirin in Lynch syndrome?", source="pubmed", title="LS", doc_id="pm:1", ord=4),
    ]
    filtered, result = apply_quality_filter(_plan(chunks))
    assert result.by_rule == {"nice_economic": 2, "cross_reference": 1, "sandbox": 2, "research": 1}
    assert result.by_rule_source == {"nice_economic": {"nice": 2}, "cross_reference": {"nice": 1},
                                     "sandbox": {"wikidoc": 2}, "research": {"pubmed": 1}}  # fmt: skip
    assert [(c.doc_id, c.ord) for c in filtered.chunks] == [("nice:ta1", 0), ("nice:ta1", 3), ("nice:ng1", 1)]
    assert result.documents_emptied == ["pm:1", "wikidoc:sb"]
    assert (result.chunks_before, result.chunks_after) == (9, 3)
    assert filtered.stats.chunks == 3 and filtered.stats.documents_without_chunks == 2
    assert filtered.meta["quality_filter_hash"] == QualityFilterConfig().filter_hash()
    assert filtered.meta["chunker_config_hash"] == "abc"  # untouched: the cached plan stays valid
    # Kept chunks are the same records: same content hash, same ord.
    assert filtered.chunks[1] == chunks[3]

    lines = "\n".join(format_quality_report(result, show_patterns=True))
    assert "6 chunks removed (9 -> 3)" in lines
    assert "nice_economic         2  (nice 2)" in lines and "sandbox               2  (wikidoc 2)" in lines
    assert "garbled_table         0  (-)" in lines
    assert "by source         nice 3, pubmed 1, wikidoc 2" in lines
    assert "documents emptied 2" in lines and "wikidoc:sb" in lines
    assert r"\bcancer drugs fund\b" in lines  # the patterns are shown on a dry run
