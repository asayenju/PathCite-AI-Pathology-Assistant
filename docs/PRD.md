# PRD: Tissue Classification API

## What
`POST /predict` accepts one H&E histology patch image and returns the predicted tissue type and the model's confidence. The service wraps the fine-tuned ResNet18 (`model/resnet18_finetuned.pt`, 9 NCT-CRC tissue classes). Backend only.

Response: `{label, tissue, confidence, uncertain, probabilities, warnings, model_version}`.
`GET /health` also reports `model_loaded`.

## Why
The trained model is only usable from a notebook. A stable API is the base for the rest of PathCite (UI, citations, explanations).

Known limits that shape the design:
- Model scores: 95.4% accuracy and 0.936 macro-F1 on the external test. STR recall is 0.64.
- Softmax confidence is uncalibrated, so it is a model score, not a probability of being correct.
- There is no "not tissue" class, so any image gets one of 9 labels. The `uncertain` flag (confidence below 0.7, configurable) tells callers to be careful.
- The model was trained on 224x224 Macenko-normalized colorectal H&E patches. Other stains, organs or scanners are out of distribution.

## How
- Stack: FastAPI, PyTorch, timm. Weights load once at startup, device picked cuda > mps > cpu, and startup fails fast if the weights are missing or mismatched.
- Preprocessing matches training: RGB, resize to 224x224, ImageNet mean/std.
- Input rules:
  - Formats: PNG, JPEG or TIFF, max 10 MB, one file.
  - Decode with Pillow rather than trusting the extension, cap pixel count, convert any colour mode to RGB.
  - Errors: 413 too large, 415 unsupported type, 422 corrupt or missing file.
  - Warn (don't reject) when the input is not square or not close to 224px.
- No image storage and no image content in logs. Responses carry a "research use only, not a diagnosis" note.
- Class map (index to tissue): 0 ADI, 1 BACK, 2 DEB, 3 LYM, 4 MUC, 5 MUS, 6 NORM, 7 STR, 8 TUM. Only index 0 is confirmed; the rest must be verified against the dataset.

## Non-goals
Batch endpoint, Grad-CAM, auth, Docker, calibration, out-of-distribution detection, LLM or citation features.
Anti-goal: presenting confidence as clinical certainty.

## Testing
- Unit and integration tests with synthetic images, no network.
- Opt-in smoke eval (`pytest -m hf`) on `dpdl-benchmark/colorectal_histology`:
  - 50 images per class, seed 42, fetched through the Hub filter or streaming API. The 187 MB parquet is never downloaded.
  - The dataset has 8 classes (150x150 images). Seven map onto our model: tumor to TUM, stroma to STR, lympho to LYM, debris to DEB, mucosa to NORM, adipose to ADI, empty to BACK.
  - `complex` has no equivalent and is reported separately. MUC and MUS never appear.
  - Report-only (per-class table and confusion matrix). This is a domain-shift check with wide error bars, not a benchmark.

## Issues
1. Verify and lock the class-index to tissue-name map.
2. Config and model loader.
3. Image validation and normalization.
4. `POST /predict` endpoint and schema.
5. Preprocessing parity with the notebook.
6. Error handling, concurrency and privacy.
7. Synthetic-image tests.
8. Opt-in Hugging Face smoke evaluation.
