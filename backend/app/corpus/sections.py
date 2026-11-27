"""Split a document into heading sections and drop boilerplate ones (PRD steps 3-4).

Almost every guideline has markdown headings, so they are the first, cheapest and most faithful cut. The path
(`Title › H1 › H2`) is embedded with every chunk, so a chunk keeps the context its heading gave it.
"""

import re
from dataclasses import dataclass

from langchain_text_splitters import MarkdownHeaderTextSplitter

from app.corpus.config import ChunkerConfig
from app.corpus.documents import Document

PATH_SEP = " › "
_LEVELS = [("#" * n, f"h{n}") for n in range(1, 7)]
_HAS_WORD = re.compile(r"\w")


@dataclass(frozen=True)
class Section:
    path: str
    headings: tuple[str, ...]
    """Headings under the title, outermost first."""
    text: str


def split_sections(doc: Document) -> list[Section]:
    """Abstracts and heading-less documents are one section named after the title. A heading with no body of its
    own produces no section (the splitter only emits text), but its descendants carry it in their metadata."""
    if doc.kind == "abstract":
        return _one_section(doc.title, _clean(doc.text))
    splitter = MarkdownHeaderTextSplitter(_LEVELS, strip_headers=True)
    sections: list[Section] = []
    for i, part in enumerate(splitter.split_text(doc.text)):
        headings = tuple(
            h for key in (k for _, k in _LEVELS) if (h := part.metadata.get(key, "").strip()) and _HAS_WORD.search(h)
        )
        text = _clean(part.page_content)
        if i == 0 and not part.metadata:
            text = _drop_title_line(text, doc.title)
        if text:
            sections.append(Section(PATH_SEP.join((doc.title, *headings)), headings, text))
    return sections


def filter_sections(sections: list[Section], config: ChunkerConfig) -> tuple[list[Section], list[Section]]:
    """Returns (kept, dropped). A section is dropped when any heading in its path matches the boilerplate list, so
    the subsections of "Cost effectiveness" go with it."""
    pattern = re.compile(r"\b(?:" + "|".join(config.boilerplate_headings) + ")", re.IGNORECASE)
    kept, dropped = [], []
    for s in sections:
        (dropped if any(pattern.search(h) for h in s.headings) else kept).append(s)
    return kept, dropped


def _one_section(title: str, text: str) -> list[Section]:
    return [Section(title, (), text)] if text else []


def _clean(text: str) -> str:
    """The splitter joins lines with trailing double spaces (a markdown line break) and drops blank lines."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines() if line.strip())


def _drop_title_line(text: str, title: str) -> str:
    """NICE text starts by repeating the title, which is already in the section path."""
    first, _, rest = text.partition("\n")
    return rest.strip() if first.strip().casefold() == title.casefold() else text
