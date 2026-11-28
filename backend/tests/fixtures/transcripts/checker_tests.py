"""Hand-testing conversations for the RAG claim checker (issue #54).

Synthetic and AI-written: no real patients, not medical advice. One short conversation per
corpus cancer, written so that, across the set, the doctor turns hit every checker outcome
(accurate, inaccurate, incomplete, unverified, disputed, needs review), plus small talk and
plans that triage must skip and a planted speech-to-text error that must give a transcription
card. Most claims paraphrase rows of backend/evals/search/golden.jsonl (noted per line), so
the evidence is known to be in the corpus. The observed live results are in
frontend/public/transcripts/TESTING.md.

Unlike scenarios.py these have no fact-check answer keys or triage gold labels and are not part
of any eval: they are for clicking through the app. `generate.py` renders them to
frontend/public/transcripts/checker-tests/ and lists them in index.json.

`expect` maps a doctor sentence (exactly as it appears in its line) to the card it should give:
"skip", "transcription", or "claim:<verdict>" / "claim:<verdict>+review". Doctor sentences not
listed are skipped. Same text rules as scenarios.py (letters, digits, spaces, ' - + . , ? !).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

try:
    from .scenarios import AsrError
except ImportError:  # run as a script from this folder
    from scenarios import AsrError  # type: ignore[no-redef]

Vendor = Literal["deepgram", "aws_medical", "whisperx", "openai_diarized"]

DOCTOR, PATIENT = 0, 1

FOLDER: tuple[str, str] = ("Checker test cases", "checker-tests")


@dataclass(frozen=True)
class CheckerScenario:
    id: str
    title: str
    vendor: Vendor
    lines: list[tuple[int, str]]
    expect: dict[str, str]
    asr_error: AsrError | None = None
    low_confidence: dict[str, float] = field(default_factory=dict)
    folder: str = FOLDER[0]

    @property
    def dialogue(self) -> list[tuple[int, str]]:
        return self.lines


SCENARIOS: list[CheckerScenario] = [
    # ------------------------------------------------------------------------- colorectal
    CheckerScenario(
        id="check_colorectal",
        title="Colorectal cancer: checker test",
        vendor="deepgram",
        asr_error=AsrError("mismatch repeat", "mismatch repair", 0.38),
        lines=[
            (DOCTOR, "Hi, come on in and have a seat. How have you been since the colonoscopy?"),
            (PATIENT, "Okay, I guess. A bit nervous about today, to be honest."),
            (DOCTOR, "That's completely normal. Your biopsy shows a colorectal cancer, an adenocarcinoma in the sigmoid colon."),
            (PATIENT, "Oh. Okay. So what happens now?"),
            (DOCTOR, "We'll book a CT scan of your chest, abdomen and pelvis for next week to stage it."),
            (PATIENT, "What does the stage tell you?"),
            # crc-14 (contradicted)
            (DOCTOR, "Stage three bowel cancer means it has spread to the liver."),
            (PATIENT, "Oh no. Is that what I have?"),
            (DOCTOR, "We don't know yet, the scan will tell us. Colon cancers are tested for mismatch repair proteins."),
            (PATIENT, "Mismatch what?"),
            (DOCTOR, "The mismatch repeat proteins fix mistakes when a cell copies its DNA."),
            (PATIENT, "Did I get this from eating spicy food? I love hot sauce."),
            # nc-04 (not_covered)
            (DOCTOR, "Eating a lot of spicy food is one of the causes of bowel cancer."),
            (PATIENT, "And if it comes back after the surgery?"),
            # crc-10 (supported)
            (DOCTOR, "If bowel cancer does come back, it's usually within the first three years after the operation, so that's when we watch most closely."),
            (PATIENT, "Okay. Thank you, doctor."),
            (DOCTOR, "You're welcome. I know it's a lot to take in."),
        ],
        expect={
            "Your biopsy shows a colorectal cancer, an adenocarcinoma in the sigmoid colon.": "skip",
            "We'll book a CT scan of your chest, abdomen and pelvis for next week to stage it.": "skip",
            "Stage three bowel cancer means it has spread to the liver.": "claim:inaccurate",
            "Colon cancers are tested for mismatch repair proteins.": "claim:incomplete",
            "The mismatch repeat proteins fix mistakes when a cell copies its DNA.": "transcription",
            "Eating a lot of spicy food is one of the causes of bowel cancer.": "claim:unverified",
            "If bowel cancer does come back, it's usually within the first three years after the operation, so that's when we watch most closely.": "claim:accurate",
        },
    ),
    # ------------------------------------------------------------------------- breast
    CheckerScenario(
        id="check_breast",
        title="Breast cancer: checker test",
        vendor="openai_diarized",
        lines=[
            (DOCTOR, "Hello, thanks for coming in. Is it okay if your husband stays for this?"),
            (PATIENT, "Yes, please. I'd like him here."),
            (DOCTOR, "Of course. Your biopsy shows an invasive breast cancer that is estrogen receptor positive and HER2 negative."),
            (PATIENT, "Is the receptor part good or bad?"),
            # brc-03 (supported)
            (DOCTOR, "Breast cancers that have hormone receptors generally have a better outlook than ones that don't."),
            (PATIENT, "And the HER2 part?"),
            # brc-02 (contradicted)
            (DOCTOR, "HER2-positive breast cancers tend to be the slower-growing kind, so they usually do better than HER2-negative ones."),
            (PATIENT, "So what kind of treatment would I get?"),
            # the HER2 / hormone pills claim from the v2 smoke run (low_confidence)
            (DOCTOR, "Because it's HER2 negative, hormone-blocking pills won't work for you."),
            (PATIENT, "Oh. My friend took tamoxifen for years after her breast cancer."),
            (DOCTOR, "Tamoxifen is usually taken for five years, and staying on it for ten years doesn't lower the chance of the cancer coming back."),
            (PATIENT, "I also read online that underwire bras cause this. Is that true?"),
            # nc-01 (not_covered)
            (DOCTOR, "Wearing an underwire bra can raise your risk of breast cancer."),
            (PATIENT, "Okay. What's next?"),
            (DOCTOR, "We'll set up a meeting with the breast surgeon next week to plan the operation."),
            (PATIENT, "Thank you."),
        ],
        expect={
            "Your biopsy shows an invasive breast cancer that is estrogen receptor positive and HER2 negative.": "skip",
            "Breast cancers that have hormone receptors generally have a better outlook than ones that don't.": "claim:accurate",
            "HER2-positive breast cancers tend to be the slower-growing kind, so they usually do better than HER2-negative ones.": "claim:inaccurate",
            "Because it's HER2 negative, hormone-blocking pills won't work for you.": "claim:inaccurate+review",
            "Tamoxifen is usually taken for five years, and staying on it for ten years doesn't lower the chance of the cancer coming back.": "claim:disputed+review",
            "Wearing an underwire bra can raise your risk of breast cancer.": "claim:unverified",
            "We'll set up a meeting with the breast surgeon next week to plan the operation.": "skip",
        },
    ),
    # ------------------------------------------------------------------------- melanoma
    CheckerScenario(
        id="check_melanoma",
        title="Melanoma: checker test",
        vendor="aws_medical",
        asr_error=AsrError("Breast low", "Breslow", 0.39),
        low_confidence={"breslow": 0.53},
        lines=[
            (DOCTOR, "Hi, good to see you again. How has the wound on your back been healing?"),
            (PATIENT, "Fine, it's just a bit itchy."),
            (DOCTOR, "Glad to hear it. The mole we removed was a melanoma, and the report gives a few measurements."),
            (PATIENT, "What kind of measurements?"),
            # mel-02 (contradicted)
            (DOCTOR, "The Breslow number is basically how wide the spot was across the skin, so a small one is reassuring."),
            (PATIENT, "Breslow? Sorry, what's that?"),
            (DOCTOR, "Breast low thickness is the main measurement pathologists use to stage melanoma."),
            (PATIENT, "Do I need more surgery?"),
            # mel-05 (supported)
            (DOCTOR, "Sentinel lymph node biopsy is recommended for melanomas thicker than one millimetre."),
            (DOCTOR, "We'll book you in for a wider excision and the sentinel node biopsy next month."),
            (PATIENT, "Does the coffee I drink make any difference?"),
            # nc-06 (not_covered)
            (DOCTOR, "Regular coffee drinking is linked to a lower risk of melanoma."),
            (PATIENT, "The report also mentioned how many cells were dividing."),
            (DOCTOR, "The mitotic rate is used to decide the T stage of thin melanomas."),
            (PATIENT, "Okay. Thanks for explaining."),
            (DOCTOR, "You're welcome. Call us if anything changes with the scar."),
        ],
        expect={
            "The mole we removed was a melanoma, and the report gives a few measurements.": "skip",
            "The Breslow number is basically how wide the spot was across the skin, so a small one is reassuring.": "claim:inaccurate",
            "Breast low thickness is the main measurement pathologists use to stage melanoma.": "transcription",
            "Sentinel lymph node biopsy is recommended for melanomas thicker than one millimetre.": "claim:accurate",
            "We'll book you in for a wider excision and the sentinel node biopsy next month.": "skip",
            "Regular coffee drinking is linked to a lower risk of melanoma.": "claim:unverified",
            "The mitotic rate is used to decide the T stage of thin melanomas.": "claim:disputed+review",
        },
    ),
    # ------------------------------------------------------------------------- DLBCL
    CheckerScenario(
        id="check_dlbcl",
        title="Diffuse large B-cell lymphoma: checker test",
        vendor="whisperx",
        asr_error=AsrError("B sell", "B-cell", 0.37),
        lines=[
            (DOCTOR, "Hello, please come in. Is your daughter joining us today?"),
            (PATIENT, "Yes, she's just here with me."),
            (DOCTOR, "Good. The biopsy of the lump in your neck shows a diffuse large B-cell lymphoma."),
            (PATIENT, "Is that a bad kind?"),
            # lym-03 (supported)
            (DOCTOR, "Diffuse large B-cell lymphoma is the most common type of non-Hodgkin lymphoma in adults, and it mostly turns up in older people."),
            (PATIENT, "But the lump never hurt at all."),
            # lym-04 (contradicted)
            (DOCTOR, "This kind of lymphoma usually shows up as a painful, tender lump."),
            (PATIENT, "Can it be cured?"),
            # lym-09 (supported)
            (DOCTOR, "This lymphoma can be cured in about two out of every three people."),
            (DOCTOR, "Large B sell lymphoma is usually treated with a combination called R-CHOP."),
            (PATIENT, "We have a holiday booked. Can I fly during the chemo?"),
            # nc-08 (not_covered)
            (DOCTOR, "Flying on a plane isn't safe while you're having R-CHOP chemotherapy."),
            (PATIENT, "Oh. Okay."),
            (DOCTOR, "We'll start the first cycle in two weeks, after a PET-CT scan."),
            (PATIENT, "Thank you."),
            (DOCTOR, "Take care, and call the ward straight away if you get a fever."),
        ],
        expect={
            "The biopsy of the lump in your neck shows a diffuse large B-cell lymphoma.": "skip",
            "Diffuse large B-cell lymphoma is the most common type of non-Hodgkin lymphoma in adults, and it mostly turns up in older people.": "claim:accurate",
            "This kind of lymphoma usually shows up as a painful, tender lump.": "claim:inaccurate",
            "This lymphoma can be cured in about two out of every three people.": "claim:accurate",
            "Large B sell lymphoma is usually treated with a combination called R-CHOP.": "transcription",
            "Flying on a plane isn't safe while you're having R-CHOP chemotherapy.": "claim:unverified",
            "We'll start the first cycle in two weeks, after a PET-CT scan.": "skip",
        },
    ),
    # ------------------------------------------------------------------------- prostate
    CheckerScenario(
        id="check_prostate",
        title="Prostate cancer: checker test",
        vendor="deepgram",
        asr_error=AsrError("glisten", "Gleason", 0.36),
        lines=[
            (DOCTOR, "Hi, thanks for coming in. Did your wife come with you today?"),
            (PATIENT, "She's parking the car. Let's just start."),
            (DOCTOR, "Okay. Your biopsy shows prostate cancer, Gleason three plus four, so a score of seven."),
            (PATIENT, "Is seven bad?"),
            # pro-02 (contradicted)
            (DOCTOR, "Three plus four and four plus three both add up to seven, so they behave exactly the same."),
            # pro-01 (supported), with the planted error
            (DOCTOR, "A glisten score is made by adding together the two most common patterns seen in the tumour."),
            (PATIENT, "Do I need treatment right away?"),
            # pro-04 (supported)
            (DOCTOR, "Active surveillance should be offered for low-risk localised prostate cancer."),
            (PATIENT, "My brother had it too. Does that matter?"),
            # pro-09 (supported)
            (DOCTOR, "Men whose brothers or fathers had prostate cancer are more likely to get it themselves."),
            (PATIENT, "Could my wife catch it from me?"),
            # nc-09 (not_covered)
            (DOCTOR, "Prostate cancer can be passed on to a partner through sex."),
            (PATIENT, "And does PSA screening actually help?"),
            (DOCTOR, "PSA screening reduces the number of men who die from prostate cancer."),
            (DOCTOR, "We'll repeat your PSA and an MRI in six months."),
            (PATIENT, "Okay, thanks."),
        ],
        expect={
            "Your biopsy shows prostate cancer, Gleason three plus four, so a score of seven.": "skip",
            "Three plus four and four plus three both add up to seven, so they behave exactly the same.": "claim:inaccurate",
            "A glisten score is made by adding together the two most common patterns seen in the tumour.": "transcription",
            "Active surveillance should be offered for low-risk localised prostate cancer.": "claim:accurate",
            "Men whose brothers or fathers had prostate cancer are more likely to get it themselves.": "claim:accurate",
            "Prostate cancer can be passed on to a partner through sex.": "claim:unverified",
            "PSA screening reduces the number of men who die from prostate cancer.": "claim:disputed+review",
            "We'll repeat your PSA and an MRI in six months.": "skip",
        },
    ),
    # ------------------------------------------------------------------------- thyroid
    CheckerScenario(
        id="check_thyroid",
        title="Papillary thyroid cancer: checker test",
        vendor="whisperx",
        asr_error=AsrError("Capillary", "Papillary", 0.36),
        lines=[
            (DOCTOR, "Morning, come on in. How is your neck feeling after the operation?"),
            (PATIENT, "A little sore, but okay."),
            (DOCTOR, "Good. The operation confirmed a papillary thyroid cancer, about one and a half centimetres across."),
            (PATIENT, "Is that a bad one?"),
            # thy-01 (supported)
            (DOCTOR, "Papillary thyroid cancer has an excellent outlook, and about ninety-nine out of a hundred people are still alive ten years on."),
            (DOCTOR, "Capillary thyroid cancer is the most common type of thyroid cancer."),
            (PATIENT, "Do I need radioactive iodine?"),
            # thy-04 neighbourhood: NICE vs older UK guidance
            (DOCTOR, "Most papillary thyroid cancers bigger than one centimetre are treated with radioactive iodine after surgery."),
            (PATIENT, "My aunt had a different kind, anaplastic."),
            # thy-12 (contradicted)
            (DOCTOR, "Anaplastic thyroid cancer usually has a good outlook, much like the papillary type."),
            (PATIENT, "Could my mobile phone have caused it?"),
            # nc-10 (not_covered)
            (DOCTOR, "Radiation from mobile phones is a known cause of thyroid cancer."),
            (PATIENT, "Okay."),
            (DOCTOR, "You'll start on thyroid hormone tablets today, and we'll check your blood levels in six weeks."),
            (PATIENT, "Thanks, doctor."),
        ],
        expect={
            "The operation confirmed a papillary thyroid cancer, about one and a half centimetres across.": "skip",
            "Papillary thyroid cancer has an excellent outlook, and about ninety-nine out of a hundred people are still alive ten years on.": "claim:accurate",
            "Capillary thyroid cancer is the most common type of thyroid cancer.": "transcription",
            "Most papillary thyroid cancers bigger than one centimetre are treated with radioactive iodine after surgery.": "claim:disputed+review",
            "Anaplastic thyroid cancer usually has a good outlook, much like the papillary type.": "claim:inaccurate",
            "Radiation from mobile phones is a known cause of thyroid cancer.": "claim:unverified",
            "You'll start on thyroid hormone tablets today, and we'll check your blood levels in six weeks.": "skip",
        },
    ),
]
