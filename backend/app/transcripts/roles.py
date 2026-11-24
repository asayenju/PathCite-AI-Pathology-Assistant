"""Per-speaker summaries and a doctor/patient role *suggestion*.

The suggestion only pre-fills the role select; the user always confirms roles. It counts hits
from a small bundled clinical vocabulary and only suggests when one speaker clearly leads.
Pure and deterministic; never logs transcript text.
"""

import re

from app.transcripts.schemas import Speaker, Turn

PREVIEW_LINES = 2
PREVIEW_MAX_CHARS = 120
ELLIPSIS = "…"

MIN_DOCTOR_HITS = 3
MIN_LEAD_RATIO = 2.0

ONE_SPEAKER_WARNING = "Only one speaker was found. Check that the recording has both doctor and patient."
MANY_SPEAKERS_WARNING = (
    "More than 4 speakers were found. Diarization may have split people; assign roles carefully."
)
MANY_SPEAKERS_LIMIT = 4

# Lowercase single-word terms. Oncology / pathology / colorectal heavy, plus general clinical
# words. Everyday words ("test", "blood", "well", "positive") are left out on purpose: patients
# say them too. Hyphenated words are single tokens.
_SINGLE_WORDS = """
abdomen abdominal adenocarcinoma adenocarcinomas adenoma adenomas adjuvant anastomosis anemia
anaemia anesthesia anaesthesia anesthetic antibiotic antibiotics atypia atypical benign
biopsied biopsies biopsy bowel braf cancer cancerous carcinoma carcinomas cea chemo
chemoradiation chemotherapy clinic clinical colectomy colon colonic colonoscopy colorectal
colostomy ct cytology diagnose diagnosed diagnoses diagnosis diagnostic differentiated
differentiation dosage dysplasia dysplastic endoscopic endoscopy epithelial epithelium excision
fibrosis follow-up gastroenterologist gastroenterology genetic genetics gland glands glandular
grade graded growth high-grade histologic histology histopathology ileostomy imaging
immunohistochemistry immunotherapy infection infiltrating infiltration inflammation
inflammatory invasion invasive kras laparoscopic laparoscopy lesion lesions liver low-grade
lymph lymphatic lymphocytes lymphocytic lymphovascular malignancy malignant margin margins
medication medications metastases metastasis metastatic metastasized microscope microscopic
mmr mri msi msi-high mucin mucinous mucosa mucosal muscularis necrosis neoadjuvant nodal node
nodes oncologic oncologist oncologists oncology operation organs pathologic pathological
pathologist pathologists pathology pelvic pelvis perforation peritoneal polyp polypectomy polyps
prognosis radiation radiologist radiology radiotherapy recurrence rectal rectum regimen
remission resect resected resection sample samples scan scans scope screening serosa sigmoid
specimen specimens stage staged staging stoma stroma stromal submucosa surgeon surgeons surgery
surgical symptom symptoms therapy tissue tnm treatment treatments tumor tumors tumour tumours
ultrasound x-ray
"""

# Multi-word terms, matched as token sequences (longest match first, counted once).
_PHRASES = """
blood test
ct scan
lymph node
lymph nodes
lynch syndrome
microsatellite instability
mismatch repair
moderately differentiated
muscle layer
pet scan
poorly differentiated
sigmoid colon
well differentiated
""".strip().splitlines()

_TOKEN = re.compile(r"[a-z0-9]+(?:['\-][a-z0-9]+)*")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _build_lexicon() -> tuple[frozenset[str], dict[int, frozenset[tuple[str, ...]]]]:
    by_len: dict[int, set[tuple[str, ...]]] = {}
    for phrase in _PHRASES:
        toks = tuple(_tokens(phrase))
        by_len.setdefault(len(toks), set()).add(toks)
    return frozenset(_SINGLE_WORDS.split()), {n: frozenset(s) for n, s in by_len.items()}


SINGLE_TERMS, PHRASE_TERMS = _build_lexicon()
LEXICON_SIZE = len(SINGLE_TERMS) + sum(len(s) for s in PHRASE_TERMS.values())
_MAX_PHRASE = max(PHRASE_TERMS, default=1)


def count_medical_terms(text: str) -> int:
    """Hits from the bundled vocabulary; a matched phrase counts once and consumes its words."""
    toks = _tokens(text)
    hits = 0
    i = 0
    while i < len(toks):
        for n in range(min(_MAX_PHRASE, len(toks) - i), 1, -1):
            if tuple(toks[i : i + n]) in PHRASE_TERMS.get(n, ()):
                hits += 1
                i += n
                break
        else:
            if toks[i] in SINGLE_TERMS:
                hits += 1
            i += 1
    return hits


def _word_count(turn: Turn) -> int:
    return len(turn.words) if turn.words else len(turn.text.split())


def truncate_preview(text: str, limit: int = PREVIEW_MAX_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - len(ELLIPSIS)]
    # Cut at a word boundary when the cut landed mid-word and a space exists to fall back to.
    if text[len(cut)] != " " and " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:-") + ELLIPSIS


def _suggest(speakers: list[Speaker], hits: dict[str, int]) -> dict[str, str | None]:
    roles: dict[str, str | None] = {s.id: None for s in speakers}
    eligible = [s for s in speakers if s.source_label != "unknown"]
    if len(speakers) < 2 or not eligible:
        return roles
    ranked = sorted(eligible, key=lambda s: hits[s.id], reverse=True)
    top = hits[ranked[0].id]
    runner_up = max((hits[s.id] for s in speakers if s.id != ranked[0].id), default=0)
    if top < MIN_DOCTOR_HITS or top < MIN_LEAD_RATIO * runner_up:
        return roles
    roles[ranked[0].id] = "doctor"
    if len(speakers) == 2:
        other = next(s for s in speakers if s.id != ranked[0].id)
        if other.source_label != "unknown":
            roles[other.id] = "patient"
    return roles


def summarize_speakers(speakers: list[Speaker], turns: list[Turn]) -> tuple[list[Speaker], list[str]]:
    """New Speaker objects with word_count, talk_time_s, preview and suggested_role filled, plus
    warnings. Inputs are not mutated."""
    words = {s.id: 0 for s in speakers}
    talk = {s.id: 0.0 for s in speakers}
    previews: dict[str, list[str]] = {s.id: [] for s in speakers}
    hits = {s.id: 0 for s in speakers}

    for turn in turns:
        sid = turn.speaker
        if sid not in words:
            continue
        words[sid] += _word_count(turn)
        if turn.start is not None and turn.end is not None and turn.end > turn.start:
            talk[sid] += turn.end - turn.start
        if len(previews[sid]) < PREVIEW_LINES and turn.text.strip():
            previews[sid].append(truncate_preview(turn.text))
        hits[sid] += count_medical_terms(turn.text)

    roles = _suggest(speakers, hits)
    out = [
        s.model_copy(
            update={
                "word_count": words[s.id],
                "talk_time_s": round(talk[s.id], 2),
                "preview": previews[s.id],
                "suggested_role": roles[s.id],
            }
        )
        for s in speakers
    ]

    warnings: list[str] = []
    if len(speakers) == 1:
        warnings.append(ONE_SPEAKER_WARNING)
    elif len(speakers) > MANY_SPEAKERS_LIMIT:
        warnings.append(MANY_SPEAKERS_WARNING)
    return out, warnings
