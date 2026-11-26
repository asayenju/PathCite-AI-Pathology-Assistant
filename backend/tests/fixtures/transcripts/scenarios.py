"""Scripted sample conversations about pathology results, with a hidden answer key.

Synthetic and AI-written: no real patients, not medical advice. The answer keys need review
by a pathologist before being used to score anything (docs/PRD-transcript-samples.md, issue 3).
Corrections go here; then run `python tests/fixtures/transcripts/generate.py`.

Each line is (speaker, text, keys). Speaker 0 is always the doctor, 1 the patient. Only doctor
lines carry keys. A key's `span` is an exact substring of its line and occurs once in the whole
conversation, so it can be found again in the imported (possibly merged) doctor turn. Doctor
lines without a key (greetings, scheduling) count as not_checkable.

Facts only (docs/PRD-jev-triage.md): keys sit on general medical facts, never on plans,
recommendations or patient-specific results with no general assertion, because those are
triaged as small talk and never reach the checker.

Triage gold labels (backend/evals/triage/README.md) come from the same data: a doctor sentence
is a claim if it contains a key span or is listed in `claims` (unkeyed general facts), a
transcription error if it contains `asr_error.wrong`, and small talk otherwise.

Text uses only letters, digits, spaces, apostrophes, hyphens, plus signs and . , ? ! so every
vendor renderer tokenises it the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["accurate", "incomplete", "inaccurate"]
Vendor = Literal["deepgram", "aws_medical", "whisperx", "openai_diarized"]

DOCTOR, PATIENT = 0, 1

# Display order of the sample folders, with their URL-safe slugs.
FOLDERS: list[tuple[str, str]] = [
    ("GI", "gi"),
    ("Breast", "breast"),
    ("Skin & blood", "skin-blood"),
    ("Prostate & thyroid", "prostate-thyroid"),
]


@dataclass(frozen=True)
class Key:
    span: str
    verdict: Verdict
    issue: str | None
    correct: str
    source_hint: str


@dataclass(frozen=True)
class AsrError:
    """A planted speech-to-text mistake in one doctor line, e.g. "colin" for "colon".

    `wrong` occurs exactly once in the conversation. Its words get `confidence` in vendor
    formats that carry word confidences (only at that spot, not elsewhere in the conversation).
    """

    wrong: str
    intended: str
    confidence: float


@dataclass(frozen=True)
class Scenario:
    id: str
    folder: str
    title: str
    vendor: Vendor
    low_confidence: dict[str, float]
    """A correctly transcribed medical term given a low score everywhere it appears."""
    asr_error: AsrError
    lines: list[tuple[int, str, list[Key]]] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    """Unkeyed doctor sentences that are general medical facts (triage label "claim")."""

    @property
    def dialogue(self) -> list[tuple[int, str]]:
        return [(spk, text) for spk, text, _ in self.lines]

    @property
    def keys(self) -> list[Key]:
        return [k for _, _, keys in self.lines for k in keys]


def _acc(span: str, correct: str, source_hint: str) -> Key:
    return Key(span, "accurate", None, correct, source_hint)


SCENARIOS: list[Scenario] = [
    # ------------------------------------------------------------------------------- GI
    Scenario(
        id="crc_adenocarcinoma",
        folder="GI",
        title="Colorectal adenocarcinoma results",
        vendor="deepgram",
        low_confidence={"sigmoid": 0.52},
        asr_error=AsrError("colin", "colon", 0.41),
        lines=[
            (0, "Good morning, thanks for coming back in. Please, have a seat.", []),
            (1, "Morning. Um, I've been kind of anxious all week.", []),
            (0, "That's completely understandable. We have the biopsy results from your colonoscopy, so let's go through them together.", []),
            (1, "Okay.", []),
            (0, "The growth in your sigmoid colin is an adenocarcinoma. That's a cancer that starts in the gland cells lining the colon.", [
                _acc(
                    "a cancer that starts in the gland cells lining the colon",
                    "Adenocarcinoma is a cancer that starts in the gland-forming cells that line the colon.",
                    "CAP colon and rectum protocol",
                ),
            ]),
            (1, "So it is cancer. Is it bad? Has it gone anywhere else?", []),
            (0, "The pathologist described the tumor as moderately differentiated. Because it's moderately differentiated, that tells us it hasn't spread.", [
                Key(
                    "Because it's moderately differentiated, that tells us it hasn't spread.",
                    "inaccurate",
                    "Confuses grade with stage: differentiation says nothing about whether the cancer has spread.",
                    "Differentiation is the grade, how abnormal the cells look. Whether it has spread is the stage, judged from the depth of invasion, the lymph nodes and imaging.",
                    "AJCC 8th colorectal; CAP colon and rectum protocol",
                ),
            ]),
            (1, "Oh, okay. That's a relief, I guess.", []),
            (0, "We'll still do a CT scan of your chest, abdomen and pelvis to stage it.", []),
            (1, "Sorry, what does that part mean?", []),
            (0, "Staging tells us how deep the tumor goes and whether the cancer is in the lymph nodes or other organs.", []),
            (1, "Okay. Um, is there anything else they're checking on it?", []),
            (0, "Yes. We'll also test the tumor for mismatch repair proteins. Mismatch repair testing is a check for Lynch syndrome, an inherited condition.", [
                Key(
                    "Mismatch repair testing is a check for Lynch syndrome, an inherited condition.",
                    "incomplete",
                    "Leaves out that it is recommended for every colorectal cancer and that the result can guide treatment such as immunotherapy.",
                    "Mismatch repair (MMR) or MSI testing screens for Lynch syndrome, an inherited condition, and can guide treatment such as immunotherapy.",
                    "CAP colorectal biomarker template",
                ),
            ]),
            (1, "Alright. And what happens after that?", []),
            (0, "Once the scan is done, the surgeon and the oncologist will meet with you together to go over treatment options.", []),
            (1, "Okay. Thank you for explaining all this.", []),
            (0, "Of course. The front desk will call you this week to book the scan.", []),
        ],
        claims=[
            "Staging tells us how deep the tumor goes and whether the cancer is in the lymph nodes or other organs.",
        ],
    ),
    Scenario(
        id="tubular_adenoma",
        folder="GI",
        title="Benign tubular adenoma polyp",
        vendor="aws_medical",
        low_confidence={"adenoma": 0.52},
        asr_error=AsrError("pollen", "polyp", 0.38),
        lines=[
            (0, "Hi there, nice to see you again. How have you been feeling since the procedure?", []),
            (1, "Pretty good, thanks. A little bloated the first day, but that went away.", []),
            (0, "Good, that's normal. I have the pathology report on the pollen we removed during your colonoscopy.", []),
            (1, "Um, okay. Was it, was it anything bad?", []),
            (0, "The pathologist didn't see anything worrying.", []),
            (1, "Oh, good. So what was it?", []),
            (0, "It was a tubular adenoma. A tubular adenoma is a benign polyp, but it could slowly turn into cancer over years if it were left alone.", [
                _acc(
                    "a benign polyp, but it could slowly turn into cancer over years if it were left alone",
                    "A tubular adenoma is a benign precancerous polyp that can slowly progress to cancer over years if not removed.",
                    "US Multi-Society Task Force on Colorectal Cancer",
                ),
            ]),
            (1, "A tubular what? Sorry, adenoma?", []),
            (0, "Adenoma, yes. The good news is it was removed completely and showed no high-grade dysplasia. Dysplasia means the cells look abnormal under the microscope.", [
                Key(
                    "Dysplasia means the cells look abnormal under the microscope.",
                    "incomplete",
                    "Doesn't say that dysplasia is a precancerous change, or that high-grade dysplasia is the step closest to cancer.",
                    "Dysplasia is a precancerous change in the lining cells. Low-grade dysplasia is the usual finding in an adenoma; high-grade dysplasia is a more advanced change, closer to cancer.",
                    "CAP colon and rectum protocol; WHO classification of digestive system tumours",
                ),
            ]),
            (1, "Okay. So that's it then?", []),
            (0, "That's it. Once an adenoma has been removed completely, there's no need for another colonoscopy.", [
                Key(
                    "Once an adenoma has been removed completely, there's no need for another colonoscopy.",
                    "inaccurate",
                    "Surveillance colonoscopy is still recommended after an adenoma is removed.",
                    "Surveillance colonoscopy is still recommended. For one or two tubular adenomas under 10 mm, the next one is usually in 7 to 10 years.",
                    "US Multi-Society Task Force 2020 post-polypectomy surveillance",
                ),
            ]),
            (1, "Oh, great. I really didn't enjoy the prep.", []),
            (0, "Nobody does. Keep up the fiber and let us know if you notice any bleeding or changes in your bowel habits.", []),
            (1, "Will do. Thanks, doctor.", []),
            (0, "You're welcome. Take care.", []),
        ],
    ),
    Scenario(
        id="ulcerative_colitis",
        folder="GI",
        title="Ulcerative colitis biopsy",
        vendor="whisperx",
        low_confidence={"colitis": 0.49},
        asr_error=AsrError("mesa lamine", "mesalamine", 0.36),
        lines=[
            (0, "Hello, come on in. How are the symptoms this week?", []),
            (1, "Still running to the bathroom a lot, and there's some blood sometimes.", []),
            (0, "I'm sorry, that sounds exhausting. Your biopsy results from the colonoscopy came back, and they help explain it.", []),
            (1, "Okay. What did they find?", []),
            (0, "The biopsies show chronic active colitis, and the pattern fits ulcerative colitis.", []),
            (1, "Ulcerative colitis. My aunt has something like that, I think. Um, where is it exactly?", []),
            (0, "Ulcerative colitis mostly affects your small intestine.", [
                Key(
                    "Ulcerative colitis mostly affects your small intestine.",
                    "inaccurate",
                    "Wrong location: ulcerative colitis affects the colon and rectum.",
                    "Ulcerative colitis affects the colon and rectum, continuously from the rectum upward. The small intestine is typically spared; Crohn's disease is the one that can affect any part of the GI tract.",
                    "ACG ulcerative colitis guideline",
                ),
            ]),
            (1, "Okay. Is it, like, dangerous?", []),
            (0, "It's a long-term inflammatory condition, but most people manage it well with medication.", [
                _acc(
                    "It's a long-term inflammatory condition, but most people manage it well with medication.",
                    "Ulcerative colitis is a chronic inflammatory bowel disease, and most people control it with medication such as mesalamine.",
                    "ACG ulcerative colitis guideline",
                ),
            ]),
            (1, "Did they see anything that looked like cancer?", []),
            (0, "There was no dysplasia in the samples. Over many years, ulcerative colitis can raise the risk of colon cancer.", [
                Key(
                    "Over many years, ulcerative colitis can raise the risk of colon cancer.",
                    "incomplete",
                    "Doesn't say that the risk is managed with regular surveillance colonoscopy.",
                    "Long-standing ulcerative colitis increases colorectal cancer risk, so regular surveillance colonoscopy is recommended, usually starting about 8 years after symptoms begin.",
                    "ACG ulcerative colitis guideline; SCENIC consensus",
                ),
            ]),
            (1, "What's dysplasia?", []),
            (0, "Dysplasia means precancerous changes in the lining cells, and there weren't any.", []),
            (1, "Okay, good. So what do I do now?", []),
            (0, "We'll start you on a medication called mesa lamine and see you back in the clinic in about 8 weeks.", []),
            (1, "Okay. Thanks.", []),
            (0, "If the bleeding gets worse or you get a fever, call us right away.", []),
        ],
        claims=[
            "Dysplasia means precancerous changes in the lining cells, and there weren't any.",
        ],
    ),
    # ------------------------------------------------------------------------------- Breast
    Scenario(
        id="breast_idc",
        folder="Breast",
        title="Invasive ductal carcinoma, ER and PR positive, HER2 negative",
        vendor="openai_diarized",
        low_confidence={"ductal": 0.5},
        asr_error=AsrError("humor", "tumor", 0.4),
        lines=[
            (0, "Hi, thanks for coming in. Is it okay if your sister stays for this?", []),
            (1, "Yes, please. I want her here.", []),
            (0, "Of course. The biopsy of the lump in your left breast shows an invasive ductal carcinoma.", []),
            (1, "Um, okay. What does invasive mean? That sounds scary.", []),
            (0, "Invasive means the cancer cells have grown out of the milk duct into the surrounding breast tissue.", [
                _acc(
                    "Invasive means the cancer cells have grown out of the milk duct into the surrounding breast tissue",
                    "Invasive carcinoma has grown beyond the duct into the surrounding breast tissue.",
                    "CAP invasive breast carcinoma protocol",
                ),
            ]),
            (1, "Okay.", []),
            (0, "The pathologist also tested the humor for hormone receptors. It's estrogen receptor and progesterone receptor positive, and HER2 negative.", []),
            (1, "I don't know what any of that means, honestly.", []),
            (0, "Because it's HER2 negative, hormone-blocking pills won't work for you.", [
                Key(
                    "Because it's HER2 negative, hormone-blocking pills won't work for you.",
                    "inaccurate",
                    "Mixes up HER2 with hormone receptors: hormone therapy depends on ER and PR, not HER2.",
                    "Whether hormone therapy helps depends on ER and PR. ER and PR positive means endocrine therapy is likely to help. HER2 negative means HER2-targeted drugs such as trastuzumab aren't used.",
                    "ASCO/CAP ER/PgR and HER2 testing guidelines",
                ),
            ]),
            (1, "Oh. Okay.", []),
            (0, "It's a grade 2 tumor. Breast cancers are graded from 1 to 3.", [
                Key(
                    "Breast cancers are graded from 1 to 3.",
                    "incomplete",
                    "Gives the scale but not what grade describes, or that it is not the stage.",
                    "The Nottingham grade, 1 to 3, describes how abnormal the cells look and how fast they are likely to grow. It is not the stage.",
                    "CAP invasive breast carcinoma protocol (Nottingham grade)",
                ),
            ]),
            (1, "Is that good or bad?", []),
            (0, "It sits in the middle of the scale. The next step is to meet with the breast surgeon and the oncologist to plan treatment.", []),
            (1, "Will I need chemo?", []),
            (0, "That depends on a few more results, and we'll go through all of it at that visit.", []),
            (1, "Okay. Thank you.", []),
        ],
    ),
    Scenario(
        id="fibroadenoma",
        folder="Breast",
        title="Fibroadenoma",
        vendor="deepgram",
        low_confidence={"fibroadenoma": 0.46},
        asr_error=AsrError("court", "core", 0.42),
        lines=[
            (0, "Hi, good to see you. Come on in and have a seat.", []),
            (1, "Hi. I've been checking my phone all day for the results.", []),
            (0, "Well, I have good news. The court biopsy of the lump in your breast shows a fibroadenoma.", []),
            (1, "A fibro, a fibroadenoma? What's that?", []),
            (0, "A fibroadenoma is a benign, non-cancerous lump made of glandular and connective tissue, and it's common in younger women.", [
                _acc(
                    "a benign, non-cancerous lump made of glandular and connective tissue, and it's common in younger women",
                    "A fibroadenoma is a benign lump made of glandular and connective tissue, most common in younger women.",
                    "WHO classification of breast tumours; CAP breast biopsy reporting",
                ),
            ]),
            (1, "Oh, thank goodness. Will it go away on its own?", []),
            (0, "Sometimes they shrink. Most fibroadenomas can simply be watched over time.", [
                Key(
                    "Most fibroadenomas can simply be watched over time.",
                    "incomplete",
                    "Doesn't say what watching involves or when to come back.",
                    "Follow-up usually means a clinical exam and/or ultrasound in a few months to check the size, and coming back sooner if it grows or changes.",
                    "NCCN breast cancer screening and diagnosis",
                ),
            ]),
            (1, "Um, okay. Could it ever turn into cancer, though?", []),
            (0, "Fibroadenomas usually turn into cancer, so we have to remove it at some point.", [
                Key(
                    "Fibroadenomas usually turn into cancer, so we have to remove it",
                    "inaccurate",
                    "Overstates the risk: simple fibroadenomas very rarely become cancer.",
                    "Simple fibroadenomas very rarely become cancer. Most don't need removal and can be monitored; removal is considered if it grows, is large or causes symptoms.",
                    "NCCN breast cancer screening and diagnosis; WHO classification of breast tumours",
                ),
            ]),
            (1, "Oh. That's, that's not what I expected to hear.", []),
            (0, "I know it's a lot. The surgeon can talk you through the operation and the timing.", []),
            (1, "Okay. Thanks.", []),
            (0, "Call the clinic if the lump gets bigger or you notice anything new.", []),
        ],
        claims=["Sometimes they shrink."],
    ),
    # ------------------------------------------------------------------------------- Skin & blood
    Scenario(
        id="melanoma",
        folder="Skin & blood",
        title="Melanoma, Breslow 1.2 mm",
        vendor="aws_medical",
        low_confidence={"breslow": 0.53},
        asr_error=AsrError("Breast low", "Breslow", 0.39),
        lines=[
            (0, "Hello, thanks for coming in. How's the spot on your back healing?", []),
            (1, "It's fine, a little itchy where the stitches were.", []),
            (0, "That's normal. I have the pathology report from the skin biopsy, and I want to go over it carefully.", []),
            (1, "Okay. Um, is it skin cancer?", []),
            (0, "Yes, it's a melanoma. Melanoma is a cancer of the pigment-making cells in the skin.", [
                _acc(
                    "a cancer of the pigment-making cells in the skin",
                    "Melanoma is a cancer of melanocytes, the pigment-producing cells of the skin.",
                    "CAP cutaneous melanoma protocol",
                ),
            ]),
            (1, "Oh no. How serious is it?", []),
            (0, "The report gives a few measurements. The Breslow thickness was 1.2 millimeters, which is how wide the spot was.", [
                Key(
                    "The Breslow thickness was 1.2 millimeters, which is how wide the spot was.",
                    "inaccurate",
                    "Breslow thickness is depth, not width.",
                    "Breslow thickness is how deep the melanoma grew into the skin, measured in millimeters. It is the main factor in the T stage.",
                    "CAP cutaneous melanoma protocol; AJCC 8th melanoma",
                ),
            ]),
            (1, "A Breslow? Sorry, what's that?", []),
            (0, "Breast low thickness is a standard measurement pathologists use for melanoma.", []),
            (1, "Okay. So what happens next?", []),
            (0, "For a melanoma like this, the standard treatment is to remove a wider area of skin around the biopsy scar.", [
                Key(
                    "For a melanoma like this, the standard treatment is to remove a wider area of skin around the biopsy scar.",
                    "incomplete",
                    "Misses lymph node evaluation and the margin size.",
                    "For a melanoma thicker than 0.8 mm a sentinel lymph node biopsy is usually discussed. Wide excision margins are typically 1 to 2 cm for this thickness.",
                    "NCCN cutaneous melanoma",
                ),
            ]),
            (1, "Another surgery? Will it leave a big scar?", []),
            (0, "It will be a longer scar, but the surgeon will close it neatly.", []),
            (1, "Okay. When would that be?", []),
            (0, "The surgery team will call you in the next week or two to schedule it.", []),
        ],
        claims=["Breast low thickness is a standard measurement pathologists use for melanoma."],
    ),
    Scenario(
        id="dlbcl",
        folder="Skin & blood",
        title="Diffuse large B-cell lymphoma",
        vendor="whisperx",
        low_confidence={"lymphoma": 0.45},
        asr_error=AsrError("sell", "cell", 0.37),
        lines=[
            (0, "Hi, come on in. How's the neck feeling since the biopsy?", []),
            (1, "Sore, but okay. I just want to know what it is.", []),
            (0, "I understand. The lymph node biopsy shows a diffuse large B-cell lymphoma.", []),
            (1, "Lymphoma. That's a cancer, right?", []),
            (0, "Yes, it's a cancer of the lymphocytes, which are a type of white blood sell.", []),
            (0, "Diffuse large B-cell lymphoma is a fast-growing lymphoma, but it's often curable with treatment.", [
                _acc(
                    "Diffuse large B-cell lymphoma is a fast-growing lymphoma, but it's often curable with treatment",
                    "Diffuse large B-cell lymphoma is an aggressive, fast-growing lymphoma that is often curable with standard treatment.",
                    "NCCN B-cell lymphomas",
                ),
            ]),
            (1, "Okay. Um, it's just the one lump in my neck, though.", []),
            (0, "Lymphoma only lives in the lymph nodes, so the rest of your body is fine.", [
                Key(
                    "Lymphoma only lives in the lymph nodes, so the rest of your body is fine.",
                    "inaccurate",
                    "Lymphoma can involve organs outside the lymph nodes; staging hasn't been done yet.",
                    "Lymphoma can involve organs outside the lymph nodes, such as the spleen, bone marrow or gut. Staging, usually with a PET-CT scan, shows where it is.",
                    "NCCN B-cell lymphomas; Lugano classification",
                ),
            ]),
            (1, "That's good. What's the treatment?", []),
            (0, "This type of lymphoma is usually treated with chemotherapy.", [
                Key(
                    "This type of lymphoma is usually treated with chemotherapy.",
                    "incomplete",
                    "Incomplete regimen and no mention of staging first.",
                    "Standard first treatment combines chemotherapy with the antibody rituximab, for example R-CHOP, after staging with PET-CT.",
                    "NCCN B-cell lymphomas",
                ),
            ]),
            (1, "Will I lose my hair?", []),
            (0, "With most of these regimens, yes, hair loss is common, but it grows back after treatment ends.", []),
            (1, "Okay. How soon do we start?", []),
            (0, "Soon. I'm referring you to the lymphoma team, and they'll see you this week.", []),
            (1, "Okay. Thank you.", []),
        ],
        claims=[
            "Yes, it's a cancer of the lymphocytes, which are a type of white blood sell.",
            "With most of these regimens, yes, hair loss is common, but it grows back after treatment ends.",
        ],
    ),
    # ------------------------------------------------------------------------------- Prostate & thyroid
    Scenario(
        id="prostate_gg2",
        folder="Prostate & thyroid",
        title="Prostate biopsy, Gleason 3+4=7",
        vendor="openai_diarized",
        low_confidence={"gleason": 0.5},
        asr_error=AsrError("prostrate", "prostate", 0.4),
        lines=[
            (0, "Good afternoon, thanks for coming in. Your wife is welcome to stay.", []),
            (1, "Thanks. We've both been waiting on this.", []),
            (0, "The prostrate biopsy did find cancer in a few of the samples.", []),
            (1, "Okay. How bad is it?", []),
            (0, "The pathologist gave it a Gleason score of 3 plus 4 equals 7, which is Grade Group 2.", [
                _acc(
                    "a Gleason score of 3 plus 4 equals 7, which is Grade Group 2",
                    "Gleason score 3+4=7 corresponds to ISUP Grade Group 2.",
                    "ISUP grading; CAP prostate protocol",
                ),
            ]),
            (1, "Gleason, okay. My brother had a Gleason 6, I think.", []),
            (0, "A Gleason 7 is the most aggressive kind there is.", [
                Key(
                    "A Gleason 7 is the most aggressive kind there is.",
                    "inaccurate",
                    "Gleason 3+4=7 is intermediate, not the most aggressive.",
                    "Grade Groups run from 1 to 5. Gleason 3+4=7 is Grade Group 2, an intermediate risk. The most aggressive is Grade Group 5, Gleason 9 to 10.",
                    "ISUP grading; CAP prostate protocol",
                ),
            ]),
            (1, "Oh. Oh, wow. So what do we do?", []),
            (0, "For this grade of prostate cancer, the standard treatment is surgery to remove the prostate.", [
                Key(
                    "For this grade of prostate cancer, the standard treatment is surgery to remove the prostate.",
                    "incomplete",
                    "Omits the other options.",
                    "Options for Grade Group 2 also include radiation therapy and, for some carefully selected men, active surveillance. The choice depends on PSA, extent of disease and the patient's preferences.",
                    "NCCN prostate cancer",
                ),
            ]),
            (1, "Um, would that be soon?", []),
            (0, "I'll refer you to a urologist and an oncologist to talk through the details.", []),
            (1, "Okay. Can my wife come to those appointments too?", []),
            (0, "Absolutely, and bring any questions you have written down.", []),
            (1, "Okay. Thank you, doctor.", []),
        ],
    ),
    Scenario(
        id="thyroid_bethesda4",
        folder="Prostate & thyroid",
        title="Thyroid FNA, Bethesda IV",
        vendor="deepgram",
        low_confidence={"follicular": 0.48},
        asr_error=AsrError("noodle", "nodule", 0.35),
        lines=[
            (0, "Hi, thanks for waiting. Come on in.", []),
            (1, "Hi. Is this about the needle test on my neck?", []),
            (0, "Yes. The fine needle aspiration of your thyroid noodle came back, and the pathologist reviewed the cytology.", []),
            (1, "Okay. And?", []),
            (0, "The result is Bethesda category 4, which means a follicular neoplasm.", [
                _acc(
                    "Bethesda category 4, which means a follicular neoplasm",
                    "Bethesda category IV is follicular neoplasm.",
                    "Bethesda System for Reporting Thyroid Cytopathology 2023",
                ),
            ]),
            (1, "Follicular, um, neoplasm? I don't know that word.", []),
            (0, "Neoplasm means a growth. A follicular neoplasm on a needle test means it's cancer.", [
                Key(
                    "A follicular neoplasm on a needle test means it's cancer.",
                    "inaccurate",
                    "Bethesda IV is not a cancer diagnosis.",
                    "Bethesda IV means follicular neoplasm. A needle sample can't tell a benign follicular adenoma from a carcinoma, because that needs the whole capsule examined after surgery. The cancer risk is roughly a quarter to a third.",
                    "Bethesda System for Reporting Thyroid Cytopathology 2023",
                ),
            ]),
            (1, "Oh my god. Okay. Okay.", []),
            (0, "For a Bethesda 4 nodule, the usual next step is surgery to take out half of the thyroid.", [
                Key(
                    "For a Bethesda 4 nodule, the usual next step is surgery to take out half of the thyroid.",
                    "incomplete",
                    "Omits molecular testing as an option.",
                    "Molecular testing of the FNA sample is an option that can refine the risk and sometimes avoid surgery. Lobectomy also gives the diagnosis.",
                    "ATA thyroid nodule guidelines",
                ),
            ]),
            (1, "Half? Will I need to take pills after that?", []),
            (0, "Many people don't after removing one side, but we'll check your thyroid levels with a blood test after surgery.", []),
            (1, "Okay. How long is the recovery?", []),
            (0, "Most people go home the same day or the next morning, and the surgeon will go over that with you.", []),
            (1, "Okay. Thank you.", []),
        ],
        claims=[
            "Neoplasm means a growth.",
            "Many people don't after removing one side, but we'll check your thyroid levels with a blood test after surgery.",
            "Most people go home the same day or the next morning, and the surgeon will go over that with you.",
        ],
    ),
]
