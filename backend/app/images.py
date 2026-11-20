import io

import numpy as np
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from app.errors import ApiError

ALLOWED_FORMATS = ["PNG", "JPEG", "TIFF"]
_SIXTEEN_BIT = {"I;16", "I;16L", "I;16B", "I;16N", "I"}


async def read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Read an upload, refusing to buffer more than max_bytes."""
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ApiError(413, "file_too_large", f"File exceeds the {max_bytes // (1024 * 1024)} MB limit")
    return data


def _flatten_alpha(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode == "RGB":
        return img
    if img.mode in _SIXTEEN_BIT:
        arr = np.asarray(img, dtype=np.float64)
        scale = 256.0 if img.mode.startswith("I;16") else 1.0
        return Image.fromarray(np.clip(arr / scale, 0, 255).astype(np.uint8), "L").convert("RGB")
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        return _flatten_alpha(img)
    return img.convert("RGB")  # CMYK, L, P, 1, ...


def load_image(data: bytes, *, max_bytes: int, max_pixels: int) -> Image.Image:
    """Validate and decode image bytes into an upright RGB PIL image.

    The format is detected from the content, never from the file name or content-type.
    """
    if not data:
        raise ApiError(422, "empty_file", "Uploaded file is empty")
    if len(data) > max_bytes:
        raise ApiError(413, "file_too_large", f"File exceeds the {max_bytes // (1024 * 1024)} MB limit")

    try:
        img = Image.open(io.BytesIO(data), formats=ALLOWED_FORMATS)
    except UnidentifiedImageError:
        raise ApiError(415, "unsupported_type", "Unsupported file type; use PNG, JPEG or TIFF") from None
    except Image.DecompressionBombError:
        raise ApiError(422, "image_too_large", "Image has too many pixels") from None
    except Exception:
        raise ApiError(422, "corrupt_image", "Image could not be read") from None

    try:
        # Check dimensions from the header before decoding any pixels.
        if img.width * img.height > max_pixels:
            raise ApiError(422, "image_too_large", f"Image exceeds {max_pixels:,} pixels")
        img.seek(0)  # multi-frame TIFF: first frame only
        img.load()
        img = ImageOps.exif_transpose(img)
        return _to_rgb(img)
    except ApiError:
        raise
    except Exception:
        raise ApiError(422, "corrupt_image", "Image could not be read") from None
