# Search golden set (issue #53)

`golden.jsonl` is 100 hand-written doctor claims, each labelled with the corpus chunks that settle it. The search evals use it to measure whether `match_chunks` (and the Luna query rewrite) finds the evidence a claim needs. It holds general medical facts only: nothing patient-specific, no transcripts and no personal data.

**Status: every row is `"reviewed": false` until the user has checked it.** The rows were written by an agent from chunks it read in Supabase. Treat the set as a draft until the review below is done.

## How it was built

1. **Tags.** The corpus `documents.cancers` values are `colorectal`, `breast`, `melanoma`, `lymphoma`, `prostate` and `thyroid`. A few PubMed documents carry two tags. Lymphoma rows target DLBCL.
2. **Finding chunks.** For each cancer, topic queries were run through the hybrid search (`tools/find_chunks.py`, which calls `match_chunks` and prints chunk ids, cancer tags, trust and content). The queries followed the sample transcripts: Breslow, ulceration, margins and sentinel node for melanoma; ER/PR/HER2 and endocrine therapy for breast; MMR/MSI, staging and follow-up for colorectal; IPI, R-CHOP and biopsy for DLBCL; Gleason, active surveillance and PSA for prostate; papillary/follicular, RAI and TSH for thyroid. Most picks are high-trust guideline chunks (NICE, national guidelines). Some medium-trust PubMed abstracts and a few low-trust WikiDoc chunks were used where they were the clearest source. Trial-design details, drug-appraisal admin and numbers that only mean something inside one study were avoided.
3. **Writing claims.** Each claim is one sentence a doctor might say aloud, never copied from the chunk.
   - **supported:** the chunk confirms the claim.
   - **contradicted:** a realistic doctor mistake that the chunk clearly refutes: a wrong threshold or number, a negation, the wrong direction, a wrong stage meaning, the wrong definition (e.g. the Phoenix PSA rule applied after prostatectomy), or confusing two terms (e.g. "Breslow is how wide the spot was").
   - No two rows within a cancer test the same fact.
4. **Gold ids.** `gold_chunk_ids` holds the chunk the claim was written from, plus any other chunk that was read and clearly contains the same information. The list is not exhaustive (see the checklist).
5. **not_covered rows.** Ten plausible claims (common myths and practical questions) were each searched with `find_chunks.py`. The top results were read to confirm that nothing in the corpus settles them. These rows measure noise: a good system should retrieve nothing relevant, and the checker should say `unverified`.
6. **Validation.** `tools/validate_golden.py` checks the schema, enums, unique ids and the distribution. It also fetches every gold chunk id back from Supabase and checks that its document is tagged with the row's cancer. Last run: 97 distinct gold chunks from 64 documents, all found and all correctly tagged.

## Schema

One JSON object per line:

| field | type | meaning |
|---|---|---|
| `id` | string | `crc-`, `brc-`, `mel-`, `lym-`, `pro-`, `thy-` + 2 digits; `nc-01`..`nc-10` for not_covered |
| `cancer` | string | `colorectal` \| `breast` \| `melanoma` \| `lymphoma` \| `prostate` \| `thyroid` |
| `type` | string | `supported` \| `contradicted` \| `not_covered` |
| `claim` | string | the doctor's sentence |
| `context` | list[string] | previous doctor turns (empty in this version) |
| `needed` | string | one sentence: the fact from the gold chunk that confirms or refutes the claim (for not_covered: what evidence would be needed) |
| `gold_chunk_ids` | list[int] | `chunks.id` values that contain `needed`; `[]` for not_covered |
| `style` | string | `technical` (guideline terms) \| `lay` (patient-friendly paraphrase) |
| `reviewed` | bool | `false` until a human has checked the row |

## Distribution

| cancer | type | technical | lay | total |
|---|---|---:|---:|---:|
| colorectal | supported | 4 | 5 | 9 |
| colorectal | contradicted | 3 | 3 | 6 |
| colorectal | not_covered | 1 | 1 | 2 |
| breast | supported | 5 | 4 | 9 |
| breast | contradicted | 2 | 4 | 6 |
| breast | not_covered | 0 | 2 | 2 |
| melanoma | supported | 5 | 4 | 9 |
| melanoma | contradicted | 2 | 4 | 6 |
| melanoma | not_covered | 2 | 0 | 2 |
| lymphoma | supported | 5 | 4 | 9 |
| lymphoma | contradicted | 2 | 4 | 6 |
| lymphoma | not_covered | 1 | 1 | 2 |
| prostate | supported | 4 | 5 | 9 |
| prostate | contradicted | 3 | 3 | 6 |
| prostate | not_covered | 0 | 1 | 1 |
| thyroid | supported | 4 | 5 | 9 |
| thyroid | contradicted | 3 | 3 | 6 |
| thyroid | not_covered | 1 | 0 | 1 |
| **all** | | **47** | **53** | **100** |

Totals: 54 supported, 36 contradicted, 10 not_covered.

## Tools (`tools/`)

Run these from `backend/`. Keys come from the repo-root `.env` (`--env PATH` to override) and are never printed.

- `find_chunks.py "query" ...`: runs the hybrid search and prints the hits. `--ids 123 456` prints those chunks in full.
- `corpus_get.py /chunks select=... id=eq.123`: a raw read-only PostgREST GET.
- `validate_golden.py`: runs the checks above (`--offline` skips the Supabase lookups).

## Reviewer checklist

For each row, open the gold chunk (`python evals/search/tools/find_chunks.py --ids <id> --chars 4000`) and check:

- [ ] **Label.** The chunk really supports the claim (supported), or clearly refutes it rather than just not mentioning it (contradicted).
- [ ] **`needed`.** It states the fact as the chunk gives it, with no extra facts from outside the chunk.
- [ ] **Claim.** It reads like something a doctor would say to a patient, is a general fact rather than a plan or a patient-specific result, and doesn't copy a chunk sentence.
- [ ] **Style.** `lay` rows avoid the chunk's key terms where a plain paraphrase is natural; `technical` rows use guideline terms.
- [ ] **Clinical currency.** The fact is still current (a few sources are older guidelines, e.g. AJCC 7th-edition references, the 2001 French SOR, WikiDoc). Mark rows that rest on outdated guidance.
- [ ] **Missing gold ids.** Other chunks that clearly contain `needed` should be added. A search hit on such a chunk would otherwise count as a miss.
- [ ] **not_covered.** Run the claim through `find_chunks.py` and confirm that nothing settles it. If something does, relabel the row or replace the claim.
- [ ] When the row is right, set `"reviewed": true`, then re-run `validate_golden.py`.
