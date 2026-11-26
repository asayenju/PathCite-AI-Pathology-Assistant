"""Generate synthetic speech-to-text fixtures for the transcript importer.

One scripted doctor–patient conversation (colorectal biopsy results, entirely fictional,
no PHI) is rendered into the JSON shapes produced by Deepgram, AWS Transcribe / Transcribe
Medical, WhisperX and OpenAI diarized transcription, plus the edge cases in
docs/PRD-transcript-import.md.

Shapes follow the vendors' public docs as of 2026-11 and are NOT real vendor output.
Treat them as our reading of the docs; swap in real outputs when available (issue #19).

It also renders the sample conversations in scenarios.py to frontend/public/transcripts/
(plus index.json), their answer keys to backend/evals/factcheck/keys/, their triage gold labels
to backend/evals/triage/keys/ (sentences cut by app.triage.segment.split_spans on the imported
doctor turns) and the saved imports used by the frontend mock reviews.

Deterministic: `python tests/fixtures/transcripts/generate.py` rewrites the same bytes.
"""

from __future__ import annotations

import json
import random
import re
import zlib
from pathlib import Path

try:  # imported as tests.fixtures.transcripts.generate
    from . import scenarios
except ImportError:  # run as a script
    import sys

    import scenarios  # type: ignore[no-redef]

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # backend/, for `app`

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[3]
SAMPLES_DIR = Path("frontend/public/transcripts")  # relative to REPO; served by Vite
KEYS_DIR = Path("backend/evals/factcheck/keys")  # relative to REPO; never shipped to the UI
SAMPLES_NOTICE = "Synthetic, AI-written sample conversations. Not real patients. Not medical advice."
KEYS_NOTICE = "Synthetic and AI-written. Needs review by a pathologist before being used to score anything (issue #22)."
TRIAGE_KEYS_DIR = Path("backend/evals/triage/keys")  # relative to REPO; never shipped to the UI
TRIAGE_NOTICE = (
    "Synthetic and AI-written triage gold labels. Needs review by a pathologist before being used to score anything."
)
# Saved imports of the samples that have a hand-written mock review (frontend/src/mocks/review).
MOCK_IMPORTS_DIR = Path("frontend/src/mocks/review/__fixtures__")
MOCK_IMPORTS = ("crc_adenocarcinoma", "breast_idc", "melanoma")
# Vendor label of speaker 0 (the doctor) after parsing.
DOCTOR_LABEL = {"deepgram": "speaker_0", "aws_medical": "spk_0", "whisperx": "SPEAKER_00", "openai_diarized": "A"}

# (speaker index, text). 0 = doctor, 1 = patient. Includes back-to-back doctor lines
# (turn merging), a digit (WhisperX can't align it) and low-confidence medical terms.
DIALOGUE: list[tuple[int, str]] = [
    (0, "Hi, thanks for coming in. Do you want to sit down?"),
    (1, "Thanks. I've been pretty nervous about the results."),
    (0, "That's completely understandable. So, we got the biopsy results back from your colonoscopy."),
    (1, "Okay."),
    (0, "The sample from the growth in your sigmoid colon showed what we call an adenocarcinoma."),
    (0, "That's a type of cancer that starts in the gland cells lining the colon."),
    (1, "Is it bad? Has it spread?"),
    (0, "The biopsy alone can't tell us that. We'll do a CT scan of your chest, abdomen and pelvis to stage it."),
    (1, "What does stage mean?"),
    (0, "Staging tells us how deep the tumor goes and whether it's in the lymph nodes or other organs."),
    (0, "The pathologist also described it as moderately differentiated, which means the cells look somewhat abnormal."),
    (1, "Um, and the growth was about 2 centimeters?"),
    (0, "Yes, about 2 centimeters on the scope. We'll also test the tumor for mismatch repair proteins."),
    (1, "Okay. What happens after the scan?"),
    (0, "We'll meet with surgery and oncology together and go over the options with you."),
]

# Words that a real ASR system tends to get wrong: low confidence, used to test reporting.
LOW_CONFIDENCE = {"adenocarcinoma": 0.41, "sigmoid": 0.55, "mismatch": 0.58}
# WhisperX can't align digits, so these get no start/end/score.
UNALIGNABLE = re.compile(r"^\d")

PUNCT = re.compile(r"^(.*?)([.,?!]*)$")
GAP_S = 0.6  # silence between turns


def _tokens(text: str) -> list[tuple[str, str]]:
    """[(bare word, trailing punctuation)]"""
    return [PUNCT.match(t).groups() for t in text.split()]  # type: ignore[union-attr]


def timed_dialogue(
    dialogue=DIALOGUE,
    seed: int = 42,
    low_confidence: dict[str, float] | None = None,
    word_confidence: dict[tuple[int, int], float] | None = None,
):
    """[(speaker, text, start, end, [(word, punct, start, end, confidence)])]

    low_confidence maps lowercased bare words to a fixed score everywhere they occur (default:
    LOW_CONFIDENCE). word_confidence maps (line index, word index) to a score for that one word
    only, e.g. a planted transcription error."""
    low = LOW_CONFIDENCE if low_confidence is None else low_confidence
    exact = word_confidence or {}
    rng = random.Random(seed)
    t = 0.3
    turns = []
    for li, (speaker, text) in enumerate(dialogue):
        words = []
        start = t
        for wi, (word, punct) in enumerate(_tokens(text)):
            dur = round(0.12 + 0.045 * len(word), 2)
            conf = low.get(word.lower(), round(rng.uniform(0.86, 0.995), 3))
            conf = exact.get((li, wi), conf)
            words.append((word, punct, round(t, 2), round(t + dur, 2), conf))
            t += dur + (0.25 if punct else 0.04)
        end = words[-1][3]
        turns.append((speaker, text, round(start, 2), end, words))
        t = end + GAP_S
    return turns


def _r(x: float) -> float:
    return round(x, 3)


# ---------------------------------------------------------------- Deepgram (pre-recorded)


def _dg_word(w, speaker=None):
    word, punct, s, e, c = w
    d = {"word": word.lower(), "start": s, "end": e, "confidence": c, "punctuated_word": word + punct}
    if speaker is not None:
        d["speaker"] = speaker
        d["speaker_confidence"] = 0.82
    return d


def deepgram(turns, *, utterances=True, language="en", multichannel=False):
    duration = turns[-1][3] + 0.5 if turns else 0.0
    metadata = {
        "transaction_key": "deprecated",
        "request_id": "00000000-0000-4000-8000-SYNTHETIC0001",
        "sha256": "0" * 64,
        "created": "2026-11-24T00:00:00.000Z",
        "duration": _r(duration),
        "channels": 2 if multichannel else 1,
        "models": ["synthetic-model-uuid"],
        "model_info": {"synthetic-model-uuid": {"name": "nova-3-medical", "version": "synthetic", "arch": "nova-3"}},
    }

    def alt(ws):
        return {
            "transcript": " ".join(w["punctuated_word"] for w in ws),
            "confidence": _r(sum(w["confidence"] for w in ws) / len(ws)) if ws else 0.0,
            "words": ws,
        }

    if multichannel:
        # Separate channel per participant, no diarization: words carry no `speaker`.
        channels = []
        for ch in (0, 1):
            ws = [_dg_word(w) for spk, _, _, _, words in turns if spk == ch for w in words]
            channels.append({"alternatives": [alt(ws)]})
    else:
        ws = [_dg_word(w, spk) for spk, _, _, _, words in turns for w in words]
        channels = [{"alternatives": [alt(ws)], "detected_language": language, "language_confidence": 0.97}]

    results: dict = {"channels": channels}
    if utterances:
        results["utterances"] = [
            {
                "start": s,
                "end": e,
                "confidence": _r(sum(w[4] for w in words) / len(words)),
                "channel": spk if multichannel else 0,
                "transcript": text,
                "words": [_dg_word(w, None if multichannel else spk) for w in words],
                **({} if multichannel else {"speaker": spk}),
                "id": f"00000000-0000-4000-8000-{i:012d}",
            }
            for i, (spk, text, s, e, words) in enumerate(turns)
        ]
    return {"metadata": metadata, "results": results}


# ---------------------------------------------------------------- AWS Transcribe (batch)


def _aws_items(turns, *, speaker_labels: bool, with_ids: bool):
    items = []
    for spk, _, _, _, words in turns:
        for word, punct, s, e, c in words:
            it = {
                "type": "pronunciation",
                "alternatives": [{"confidence": f"{c:.3f}", "content": word}],
                "start_time": f"{s:.3f}",
                "end_time": f"{e:.3f}",
            }
            if speaker_labels:
                it["speaker_label"] = f"spk_{spk}"
            items.append(it)
            for p in punct:
                pt = {"type": "punctuation", "alternatives": [{"confidence": "0.0", "content": p}]}
                if speaker_labels:
                    pt["speaker_label"] = f"spk_{spk}"
                items.append(pt)
    if with_ids:
        items = [{"id": i, **it} for i, it in enumerate(items)]
    return items


def aws(turns, *, layout="audio_segments", medical=False, job_name="SYNTHETIC-PATIENT-NAME-colonoscopy-results"):
    """layout: audio_segments (current) | items (speaker_label on items, no audio_segments)
    | legacy (speakers only in speaker_labels.segments) | channels (channel identification)."""
    full = " ".join(t[1] for t in turns)
    is_channels = layout == "channels"
    items = _aws_items(
        turns,
        speaker_labels=layout in ("audio_segments", "items"),
        with_ids=layout in ("audio_segments", "items"),
    )
    results: dict = {"transcripts": [{"transcript": full}], "items": items}

    if layout in ("audio_segments", "items", "legacy"):
        results["speaker_labels"] = {
            "speakers": 2,
            "segments": [
                {
                    "start_time": f"{s:.3f}",
                    "end_time": f"{e:.3f}",
                    "speaker_label": f"spk_{spk}",
                    "items": [
                        {"start_time": f"{ws:.3f}", "end_time": f"{we:.3f}", "speaker_label": f"spk_{spk}"}
                        for _, _, ws, we, _ in words
                    ],
                }
                for spk, _, s, e, words in turns
            ],
        }
    if is_channels:
        results["channel_labels"] = {
            "number_of_channels": 2,
            "channels": [
                {
                    "channel_label": f"ch_{ch}",
                    "items": _aws_items(
                        [t for t in turns if t[0] == ch], speaker_labels=False, with_ids=False
                    ),
                }
                for ch in (0, 1)
            ],
        }
    if layout == "audio_segments":
        idx = 0
        segs = []
        for i, (spk, text, s, e, words) in enumerate(turns):
            n = len(words) + sum(len(w[1]) for w in words)
            segs.append(
                {
                    "id": i,
                    "transcript": text,
                    "start_time": f"{s:.3f}",
                    "end_time": f"{e:.3f}",
                    "speaker_label": f"spk_{spk}",
                    "items": list(range(idx, idx + n)),
                }
            )
            idx += n
        results["audio_segments"] = segs

    doc = {
        # Real job names are user-chosen and often contain patient names: the importer must drop them.
        "jobName": job_name,
        "accountId": "123456789012",
        "status": "COMPLETED",
        "results": results,
    }
    if medical:
        doc["specialty"] = "PRIMARYCARE"
        doc["type"] = "CONVERSATION"
    return doc


def aws_call_analytics(turns):
    return {
        "JobName": "SYNTHETIC-call-analytics",
        "AccountId": "123456789012",
        "JobStatus": "COMPLETED",
        "LanguageCode": "en-US",
        "Transcript": [
            {
                "Id": f"seg-{i}",
                "ParticipantRole": "AGENT" if spk == 0 else "CUSTOMER",
                "Content": text,
                "BeginOffsetMillis": int(s * 1000),
                "EndOffsetMillis": int(e * 1000),
            }
            for i, (spk, text, s, e, _) in enumerate(turns)
        ],
    }


def aws_healthscribe(turns):
    return {
        "Conversation": {
            "ConversationId": "synthetic-conversation",
            "JobName": "SYNTHETIC-healthscribe",
            "LanguageCode": "en-US",
            "TranscriptSegments": [
                {
                    "SegmentId": f"seg-{i}",
                    "Content": text,
                    "BeginAudioTime": s,
                    "EndAudioTime": e,
                    "ParticipantDetails": {"ParticipantRole": "CLINICIAN" if spk == 0 else "PATIENT"},
                }
                for i, (spk, text, s, e, _) in enumerate(turns)
            ],
        }
    }


# ---------------------------------------------------------------- Whisper family


def whisperx(turns, *, drop_speaker_segment: int | None = None):
    segments, word_segments = [], []
    for i, (spk, text, s, e, words) in enumerate(turns):
        label = f"SPEAKER_{spk:02d}"
        ws = []
        for word, punct, ws_, we, c in words:
            if UNALIGNABLE.match(word):
                w = {"word": word + punct}  # WhisperX can't align digits: no timing, score or speaker
            else:
                w = {"word": word + punct, "start": ws_, "end": we, "score": c}
                if i != drop_speaker_segment:
                    w["speaker"] = label
            ws.append(w)
        seg = {"start": s, "end": e, "text": " " + text, "words": ws}
        if i != drop_speaker_segment:
            seg["speaker"] = label
        segments.append(seg)
        word_segments.extend(ws)
    return {"segments": segments, "word_segments": word_segments, "language": "en"}


def openai_diarized(turns):
    # Shape of response_format="diarized_json" (gpt-4o-transcribe-diarize). Re-check field
    # names against the current OpenAI docs before relying on them.
    return {
        "task": "transcribe",
        "duration": _r(turns[-1][3] + 0.5),
        "text": " ".join(t[1] for t in turns),
        "segments": [
            {
                "type": "transcript.text.segment",
                "id": f"seg_{i:03d}",
                "start": s,
                "end": e,
                "text": text,
                "speaker": "AB"[spk],
            }
            for i, (spk, text, s, e, _) in enumerate(turns)
        ],
        "usage": {"type": "duration", "seconds": int(turns[-1][3]) + 1},
    }


def whisper_plain(turns):
    """openai-whisper CLI JSON: segments with decoding stats and no speakers."""
    return {
        "text": " " + " ".join(t[1] for t in turns),
        "segments": [
            {
                "id": i,
                "seek": int(s * 100) // 3000 * 3000,
                "start": s,
                "end": e,
                "text": " " + text,
                "tokens": [50364 + i],
                "temperature": 0.0,
                "avg_logprob": -0.21,
                "compression_ratio": 1.42,
                "no_speech_prob": 0.01,
            }
            for i, (_, text, s, e, _) in enumerate(turns)
        ],
        "language": "en",
    }


# ---------------------------------------------------------------- edge-case dialogues

SPANISH: list[tuple[int, str]] = [
    (0, "Buenos días, ya tenemos los resultados de la biopsia."),
    (1, "¿Y qué mostraron?"),
    (0, "Mostraron un adenocarcinoma en el colon sigmoide."),
]

MONOLOGUE: list[tuple[int, str]] = [
    (0, "Dictation for the record. Biopsy of the sigmoid colon shows adenocarcinoma, moderately differentiated."),
    (0, "Mismatch repair immunohistochemistry has been requested."),
]


def build() -> dict[str, object]:
    t = timed_dialogue()
    return {
        # supported formats
        "deepgram_utterances.json": deepgram(t),
        "deepgram_words_only.json": deepgram(t, utterances=False),
        "deepgram_multichannel.json": deepgram(t, multichannel=True),
        "aws_audio_segments.json": aws(t, layout="audio_segments"),
        "aws_items_speaker_label.json": aws(t, layout="items"),
        "aws_legacy_speaker_segments.json": aws(t, layout="legacy"),
        "aws_channel_identification.json": aws(t, layout="channels"),
        "aws_medical.json": aws(t, layout="audio_segments", medical=True),
        "whisperx.json": whisperx(t),
        "whisperx_unknown_speaker.json": whisperx(t, drop_speaker_segment=3),
        "openai_diarized.json": openai_diarized(t),
        # edge cases that still import (with warnings)
        "deepgram_spanish.json": deepgram(timed_dialogue(SPANISH), language="es"),
        "deepgram_single_speaker.json": deepgram(timed_dialogue(MONOLOGUE)),
        # rejected
        "whisper_plain.json": whisper_plain(t),
        "aws_call_analytics.json": aws_call_analytics(t),
        "aws_healthscribe.json": aws_healthscribe(t),
        "deepgram_empty.json": deepgram([]),
    }


# ---------------------------------------------------------------- sample conversations


def asr_error_words(sc: "scenarios.Scenario") -> dict[tuple[int, int], float]:
    """{(line index, word index): confidence} for the words of the planted transcription error."""
    err = sc.asr_error
    found = [(i, text) for i, (_, text) in enumerate(sc.dialogue) if err.wrong in text]
    if len(found) != 1 or found[0][1].count(err.wrong) != 1 or sc.dialogue[found[0][0]][0] != scenarios.DOCTOR:
        raise ValueError(f"{sc.id}: asr_error {err.wrong!r} must occur once, in a doctor line")
    li, text = found[0]
    first = len(text[: text.index(err.wrong)].split())
    return {(li, first + k): err.confidence for k in range(len(err.wrong.split()))}


def render_scenario(sc: "scenarios.Scenario") -> dict:
    """The scenario as its vendor's JSON. Seed is derived from the id so each sample differs."""
    turns = timed_dialogue(
        sc.dialogue,
        seed=zlib.crc32(sc.id.encode()),
        low_confidence=sc.low_confidence,
        word_confidence=asr_error_words(sc),
    )
    if sc.vendor == "deepgram":
        return deepgram(turns)
    if sc.vendor == "aws_medical":
        return aws(turns, layout="audio_segments", medical=True, job_name=f"SYNTHETIC-PATIENT-NAME-{sc.id}")
    if sc.vendor == "whisperx":
        return whisperx(turns)
    if sc.vendor == "openai_diarized":
        return openai_diarized(turns)
    raise ValueError(f"unknown vendor {sc.vendor!r}")


def _folder_slug(name: str) -> str:
    return dict(scenarios.FOLDERS)[name]


def sample_path(sc: "scenarios.Scenario") -> str:
    """Path of the sample relative to frontend/public (what the frontend fetches)."""
    return f"transcripts/{_folder_slug(sc.folder)}/{sc.id}.json"


def answer_key(sc: "scenarios.Scenario") -> dict:
    return {
        "id": sc.id,
        "title": sc.title,
        "folder": sc.folder,
        "vendor": sc.vendor,
        "notice": KEYS_NOTICE,
        "keys": [
            {
                "span": k.span,
                "verdict": k.verdict,
                "issue": k.issue,
                "correct": k.correct,
                "source_hint": k.source_hint,
                "speaker": "doctor",
            }
            for k in sc.keys
        ],
    }


def imported(sc: "scenarios.Scenario"):
    """The sample as the app imports it (POST /transcripts/import)."""
    from app.config import Settings
    from app.transcripts.service import import_transcript

    return import_transcript(dump(render_scenario(sc)).encode("utf-8"), Settings())


def triage_key(sc: "scenarios.Scenario") -> dict:
    """Gold triage label for every non-filler span (split_spans) of every imported doctor turn.

    claim: contains a fact-check key span, or is listed in sc.claims. transcription: contains the
    planted asr_error (base_kind is what it would be otherwise). skip: everything else."""
    from app.triage.segment import split_spans

    imp = imported(sc)
    doctor = next(s.id for s in imp.speakers if s.source_label == DOCTOR_LABEL[sc.vendor])
    turns = [t for t in imp.turns if t.speaker == doctor]
    err = sc.asr_error
    spans: list[dict] = []
    for i, turn in enumerate(turns):
        for sp in split_spans(turn.text):
            if sp.filler:
                continue
            quote = turn.text[sp.start : sp.end]
            is_claim = quote in sc.claims or any(k.span in quote for k in sc.keys)
            label = {"doctor_turn_index": i, "start": sp.start, "end": sp.end, "quote": quote}
            if err.wrong in quote:
                label |= {
                    "label": "transcription",
                    "base_kind": "claim" if is_claim else "skip",
                    "wrong": err.wrong,
                    "intended": err.intended,
                    "intended_quote": quote.replace(err.wrong, err.intended),
                }
            else:
                label["label"] = "claim" if is_claim else "skip"
            spans.append(label)
    quotes = [s["quote"] for s in spans]
    for k in sc.keys:
        if sum(k.span in q for q in quotes) != 1:
            raise ValueError(f"{sc.id}: key span {k.span!r} must lie inside exactly one doctor sentence")
    for c in sc.claims:
        if quotes.count(c) != 1:
            raise ValueError(f"{sc.id}: claim {c!r} must be exactly one doctor sentence")
    if sum(err.wrong in q for q in quotes) != 1:
        raise ValueError(f"{sc.id}: asr_error {err.wrong!r} must lie inside exactly one doctor sentence")
    return {"notice": TRIAGE_NOTICE, "sample_id": sc.id, "file": sample_path(sc), "spans": spans}


def build_samples() -> dict[str, object]:
    """{path relative to the repo root: JSON document} for the sample transcripts, the
    manifest, the fact-check answer keys, the triage gold labels and the saved imports the
    frontend mock reviews are tested against."""
    out: dict[str, object] = {}
    folders = []
    for name, slug in scenarios.FOLDERS:
        items = []
        for sc in (s for s in scenarios.SCENARIOS if s.folder == name):
            out[f"frontend/public/{sample_path(sc)}"] = render_scenario(sc)
            out[(KEYS_DIR / f"{sc.id}.json").as_posix()] = answer_key(sc)
            out[(TRIAGE_KEYS_DIR / f"{sc.id}.json").as_posix()] = triage_key(sc)
            if sc.id in MOCK_IMPORTS:
                out[(MOCK_IMPORTS_DIR / f"{sc.id}.import.json").as_posix()] = imported(sc).model_dump(mode="json")
            items.append({"id": sc.id, "title": sc.title, "vendor": sc.vendor, "file": sample_path(sc)})
        folders.append({"name": name, "slug": slug, "items": items})
    out[(SAMPLES_DIR / "index.json").as_posix()] = {"notice": SAMPLES_NOTICE, "folders": folders}
    return out


def dump(doc: object) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def main() -> None:
    for name, doc in build().items():
        (OUT / name).write_text(dump(doc), encoding="utf-8")
    # Truncated JSON: valid prefix, cut mid-document.
    full = json.dumps(build()["deepgram_utterances.json"], indent=1)
    (OUT / "truncated.json").write_text(full[: len(full) // 2], encoding="utf-8")
    print(f"wrote {len(build()) + 1} fixtures to {OUT}")
    samples = build_samples()
    for rel, doc in samples.items():
        path = REPO / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump(doc), encoding="utf-8")
    print(f"wrote {len(samples)} sample transcripts, manifest and answer keys under {REPO}")


if __name__ == "__main__":
    main()
