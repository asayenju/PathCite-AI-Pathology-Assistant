# PathCite backend

FastAPI service that classifies the tissue type of a colorectal H&E patch with the fine-tuned ResNet18 in `../model/resnet18_finetuned.pt`.

> **Research use only. Not a medical device and not a diagnosis.** `confidence` is the model's raw softmax score, not the probability of being correct. The model has no "not tissue" class: any image gets one of 9 labels, so check `uncertain` and `warnings`.

## Run

```
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Interactive docs: http://localhost:8000/docs

## Endpoints

### `GET /health`
`200 {"status": "ok", "model_loaded": true}`, or `503 {"status": "unavailable", "model_loaded": false}`.

### `POST /predict`
Multipart upload, field name `file`: one PNG, JPEG or TIFF image (max 10 MB).

```
curl -F file=@patch.png http://localhost:8000/predict
```

```json
{
  "label": 8,
  "tissue": "TUM",
  "confidence": 0.97,
  "uncertain": false,
  "probabilities": {"ADI": 0.0, "BACK": 0.0, "DEB": 0.0, "LYM": 0.0, "MUC": 0.0, "MUS": 0.0, "NORM": 0.0, "STR": 0.03, "TUM": 0.97},
  "warnings": [],
  "model_version": "resnet18_finetuned.pt@657f5d2a",
  "notice": "Research use only. Not a medical device and not a diagnosis."
}
```

- `uncertain` is true when `confidence` is below `UNCERTAIN_THRESHOLD`. A result is always returned.
- `warnings` is non-empty when the input is not square or not about 224x224 (resized to 224x224 like in training; a resized whole slide is not a valid patch).
- Tissue codes: ADI adipose, BACK background, DEB debris, LYM lymphocytes, MUC mucus, MUS smooth muscle, NORM normal mucosa, STR stroma, TUM tumor epithelium.

### Errors
Always JSON: `{"detail": "...", "code": "..."}`.

| Status | code | When |
|---|---|---|
| 413 | `file_too_large` | upload over `MAX_UPLOAD_MB` |
| 415 | `unsupported_type` | not PNG/JPEG/TIFF (detected from content, not file name) |
| 422 | `empty_file`, `corrupt_image`, `image_too_large`, `invalid_request` | empty or unreadable file, too many pixels, missing `file` field |
| 500 | `internal_error` | unexpected error (no details are returned) |
| 503 | `model_unavailable` | model not loaded |

## Configuration (environment variables or `.env`)

| Variable | Default | Meaning |
|---|---|---|
| `MODEL_PATH` | `../model/resnet18_finetuned.pt` | weights file; startup fails if missing or mismatched |
| `UNCERTAIN_THRESHOLD` | `0.7` | confidence below this sets `uncertain` |
| `MAX_UPLOAD_MB` | `10` | max upload size |
| `MAX_IMAGE_PIXELS` | `25000000` | max width x height, guards against decompression bombs |

## Privacy
Uploaded images are decoded in memory only: never written to disk (uploads are not spooled to temp files) and never logged. Unexpected errors log only the exception type.

## Tests

```
pytest                # offline, synthetic images, no GPU needed
pytest -m hf -s       # opt-in smoke eval on a Hugging Face dataset (needs network)
```
