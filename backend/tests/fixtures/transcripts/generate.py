"""Generate synthetic speech-to-text fixtures for the transcript importer.

One scripted doctor–patient conversation (colorectal biopsy results, entirely fictional,
no PHI) is rendered into the JSON shapes produced by Deepgram, AWS Transcribe / Transcribe
Medical, WhisperX and OpenAI diarized transcription, plus the edge cases in
docs/PRD-transcript-import.md.

Shapes follow the vendors' public docs as of 2026-11 and are NOT real vendor output.
Treat them as our reading of the docs; swap in real outputs when available (issue #19).

Deterministic: `python tests/fixtures/transcripts/generate.py` rewrites the same bytes.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

OUT = Path(__file__).resolve().parent

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


def timed_dialogue(dialogue=DIALOGUE, seed: int = 42):
    """[(speaker, text, start, end, [(word, punct, start, end, confidence)])]"""
    rng = random.Random(seed)
    t = 0.3
    turns = []
    for speaker, text in dialogue:
        words = []
        start = t
        for word, punct in _tokens(text):
            dur = round(0.12 + 0.045 * len(word), 2)
            conf = LOW_CONFIDENCE.get(word.lower(), round(rng.uniform(0.86, 0.995), 3))
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


def aws(turns, *, layout="audio_segments", medical=False):
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
        "jobName": "SYNTHETIC-PATIENT-NAME-colonoscopy-results",
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


def main() -> None:
    for name, doc in build().items():
        (OUT / name).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    # Truncated JSON: valid prefix, cut mid-document.
    full = json.dumps(build()["deepgram_utterances.json"], indent=1)
    (OUT / "truncated.json").write_text(full[: len(full) // 2], encoding="utf-8")
    print(f"wrote {len(build()) + 1} fixtures to {OUT}")


if __name__ == "__main__":
    main()
