# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Research-use web app that serves a fine-tuned timm `resnet18` (`model/resnet18_finetuned_v2.pt`) classifying 224×224 colorectal H&E patches into 9 NCT-CRC tissue classes. Not a medical device; the UI and API both carry that disclaimer, keep it.

- `backend/`: FastAPI (`GET /health`, `POST /predict`). See `backend/README.md` for the API and error table.
- `frontend/`: Vue 3 + TS + Tailwind v4 + shadcn-vue. See `frontend/README.md`.
- `docs/PRD.md`: backend PRD.

## Commands

Backend (from `backend/`, venv in `.venv`):
```
uvicorn app.main:app --reload              # http://localhost:8000/docs
pytest                                      # offline suite (hf tests excluded by addopts)
pytest tests/test_images.py::test_name      # single test
pytest -m hf -s                             # opt-in network smoke eval on Hugging Face data
```

Frontend (from `frontend/`):
```
npm run dev          # :5173, proxies /api/* to localhost:8000 (prefix stripped; backend has no CORS)
npm test             # vitest; single file: npx vitest run src/lib/api.test.ts
npm run type-check
npm run build        # type-check + production build
```

## Architecture notes

- **Class order is a contract.** `backend/app/classes.py::CLASS_NAMES` (ADI, BACK, DEB, LYM, MUC, MUS, NORM, STR, TUM = indices 0–8) must match `frontend/src/lib/classes.ts`; a frontend test parses the Python file to enforce it. The order was verified against the dataset, so don't reorder.
- **Backend request path:** `main.py` (lifespan loads the model fail-fast and computes `model_version` as `name@sha256[:8]`) → `images.read_upload`/`load_image` (format sniffed from content, pixel cap, EXIF, first TIFF frame, alpha flattened to white, RGB) → `predict.predict_probs` in a threadpool → `build_response`. Preprocessing must stay `Resize((224,224))` + ImageNet normalize to match training (`test_preprocessing_parity.py`).
- **Errors** are always `{detail, code}` (`errors.py`); the frontend `lib/api.ts::messageFor` maps on `code`, and maps network failures to a "start uvicorn" message.
- **Privacy:** uploads are never written to disk or logged. The lifespan raises `MultiPartParser.spool_max_size` above the upload limit so Starlette doesn't spool to temp files (there's a test); unexpected errors log only the exception type.
- **`uncertain`** is decided server-side (`confidence < UNCERTAIN_THRESHOLD`, default 0.7). The frontend only draws the 0.70 tick; colour/state follows the response flag.
- **Frontend flow:** `composables/usePredict.ts` is the state machine (`idle|loading|result|error`). Sample slides are dragged (custom MIME `application/x-pathcite-slide`, payload = unique file path) or clicked from `SlideFolders` onto `Stage`, fetched as a Blob and sent through the same `/predict` path as a real upload. While the stage is occupied, file drops are refused (clear first); slide drops replace.
- **Bundled samples:** `frontend/public/samples/<CODE>/<row>.jpg` (72 = 8 per class) listed in `samples.json`; the folder is the true label. They're JPEG renderings from NCT-CRC-HE-100K (CC-BY-4.0, 0.5 µm/px, hence the 25 µm scale bar on bundled slides only). Tests assert 8 per class and JPEG magic bytes.

## Gotchas

- The model has no "not tissue" class and its softmax is uncalibrated. The current weights are v2 (colour jitter + label smoothing; the earlier baseline is in git history). Notebook results: 95.9% on half of the external NCT-CRC test set, 94% on the 72 bundled JPEG samples, but only 65% on the Kather `dpdl-benchmark` set (domain shift in stain colour and tile scale; the baseline got 15% there, and ~8% in the old API smoke test). A temperature fitted on in-domain data makes v2 overconfident on Kather, so none is applied. Don't present confidence as probability of correctness; the UI calls it "model score".
- Plain `shadcn-vue@latest` crashes on Node 20; components in `src/components/ui/` were added with CLI 2.4.3. TS 6: no `baseUrl`, use `paths` only.
- `backend/.cache/` (HF smoke images), `.venv/`, `reports/` are gitignored.
- Not built yet: Azure storage, auth, batch upload, dark mode, confidence calibration / OOD detection.
