"""Structural split at markdown headings and the boilerplate section filter (PRD steps 3-4)."""

from app.corpus.config import ChunkerConfig
from app.corpus.documents import parse_record
from app.corpus.sections import filter_sections, split_sections

SEP = " › "


def guideline_doc(text: str, title: str = "Melanoma: assessment and management"):
    return parse_record({"id": "g1", "source": "nice", "title": title, "url": "https://example.org/g1", "text": text,
                         "cancers": ["melanoma"]}, kind="guideline")  # fmt: skip


def test_sections_get_title_and_heading_path():
    doc = guideline_doc("# Staging\nIntro to staging.\n## Sentinel lymph node biopsy\nOffer SLNB to people with IIB.")
    sections = split_sections(doc)
    assert [s.path for s in sections] == [
        "Melanoma: assessment and management › Staging",
        "Melanoma: assessment and management › Staging › Sentinel lymph node biopsy",
    ]
    assert sections[1].text == "Offer SLNB to people with IIB."
    assert sections[1].headings == ("Staging", "Sentinel lymph node biopsy")


def test_document_with_no_headings_is_one_section_named_after_its_title():
    doc = guideline_doc("Line one of text.\nLine two of text.", title="Thyroid nodules")
    [section] = split_sections(doc)
    assert section.path == "Thyroid nodules"
    assert section.headings == ()
    assert section.text == "Line one of text.\nLine two of text."


def test_preamble_before_the_first_heading_drops_a_repeated_title_line():
    doc = guideline_doc("Thyroid nodules\n\nEvidence-based recommendations.\n# Recommendations\nDo X.", title="Thyroid nodules")
    sections = split_sections(doc)
    assert sections[0].path == "Thyroid nodules"
    assert sections[0].text == "Evidence-based recommendations."
    assert sections[1].path == "Thyroid nodules › Recommendations"


def test_heading_with_empty_body_is_dropped_but_stays_in_descendant_paths():
    doc = guideline_doc("# Treatment\n## Surgery\nWide local excision.\n# Empty\n# Follow-up\nSee every 3 months.")
    sections = split_sections(doc)
    paths = [s.path.split(SEP, 1)[1] for s in sections]
    assert paths == ["Treatment › Surgery", "Follow-up"]


def test_headings_nested_more_than_three_levels():
    doc = guideline_doc("# A\n## B\n### C\n#### D\n##### E\n###### F\nDeep text here.")
    [section] = split_sections(doc)
    assert section.path == SEP.join(["Melanoma: assessment and management", "A", "B", "C", "D", "E", "F"])


def test_skipped_level_and_return_to_a_shallower_heading():
    doc = guideline_doc("# A\n### C\nUnder C.\n# B\nUnder B.")
    sections = split_sections(doc)
    assert [s.path.split(SEP, 1)[1] for s in sections] == ["A › C", "B"]


def test_headings_without_letters_or_digits_are_left_out_of_the_path():
    doc = guideline_doc("# Diagnosis\n## -\nBody text.")
    [section] = split_sections(doc)
    assert section.path.endswith("› Diagnosis")


def test_list_only_section_keeps_its_lines():
    doc = guideline_doc("# Differential diagnosis\n- Crohn's disease\n- Infectious colitis\n- Ischaemic colitis")
    [section] = split_sections(doc)
    assert section.text == "- Crohn's disease\n- Infectious colitis\n- Ischaemic colitis"


def test_abstract_is_one_section_even_with_labels_and_hashes():
    doc = parse_record({"id": "pubmed:1", "source": "pubmed", "title": "A review.", "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
                        "text": "IMPORTANCE: X matters.\n# not a heading\nOBSERVATIONS: Y. CONCLUSIONS: Z.",
                        "cancers": [], "year": "2020", "pub_types": ["Review"]}, kind="abstract")  # fmt: skip
    [section] = split_sections(doc)
    assert section.path == "A review."
    assert section.text.startswith("IMPORTANCE: X matters.") and "# not a heading" in section.text


def test_section_text_has_no_trailing_spaces_from_the_splitter():
    doc = guideline_doc("# A\nline one\n\nline two")
    [section] = split_sections(doc)
    assert section.text == "line one\nline two"


def test_boilerplate_sections_and_their_subsections_are_dropped():
    doc = guideline_doc(
        "# Recommendations\nDo X.\n# Cost effectiveness\nICER text.\n## Utility values\nQALY text.\n"
        "# Price\n£100.\n# Why the committee made the recommendations\nBecause.\n# References\n1. Ref.\n"
        "# Information for the public\nLeaflet.\n# See also\nLinks.\n# Historical Perspective\nOld.\n"
        "# Accost\nKept: not the word cost."
    )
    kept, dropped = filter_sections(split_sections(doc), ChunkerConfig())
    assert [s.headings[-1] for s in kept] == ["Recommendations", "Accost"]
    assert len(dropped) == 8
    assert sum(len(s.text) for s in dropped) == len("ICER text.QALY text.£100.Because.1. Ref.Leaflet.Links.Old.")


def test_title_is_not_matched_against_the_boilerplate_list():
    doc = guideline_doc("No headings here.", title="Implementation of bowel screening")
    kept, dropped = filter_sections(split_sections(doc), ChunkerConfig())
    assert len(kept) == 1 and dropped == []


def test_every_boilerplate_term_from_the_prd_is_configured():
    terms = ["cost", "price", "pprs", "economic", "marketing authorisation", "committee", "references",
             "acknowledgements", "information for the public", "implementation", "about this", "external links",
             "see also", "historical perspective"]  # fmt: skip
    for term in terms:
        doc = guideline_doc(f"# {term.title()} section\nBody.\n# Keep\nBody.")
        kept, dropped = filter_sections(split_sections(doc), ChunkerConfig())
        assert len(dropped) == 1, term
