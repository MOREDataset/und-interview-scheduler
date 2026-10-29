"""Logo handling. Logos are uploaded by a coordinator (Search settings → Branding) and
kept in the database, so they survive redeploys on Streamlit Community Cloud.

No logo ships with the app: official university marks must come from the
university's own brand site, and their use should follow its guidelines.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from .services import LOGO_DARK, LOGO_LIGHT, ValidationError, get_setting_blob

MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_SIZE = (1200, 400)  # plenty for a header logo; keeps pages and PDFs light


@dataclass
class Brand:
    light: bytes | None = None  # for white paper: PDFs, itineraries, sign-in page
    dark: bytes | None = None  # for the dark green sidebar

    @property
    def any(self) -> bool:
        return bool(self.light or self.dark)


def prepare_logo(raw: bytes) -> bytes:
    """Check an uploaded image and store it as a reasonably sized PNG (transparency kept)."""
    from PIL import Image, UnidentifiedImageError

    if not raw:
        raise ValidationError("That file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationError("That image is larger than 4 MB. Use the “Digital PNG” version of the logo.")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError("That file isn't an image this app can read. Upload a PNG or JPG.") from exc
    img = img.convert("RGBA")
    img.thumbnail(MAX_SIZE)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


@lru_cache(maxsize=8)
def on_white_tile(img: bytes) -> bytes:
    """A full-color logo placed on a white rounded tile, so it stays readable on the dark sidebar."""
    from PIL import Image, ImageDraw

    logo = Image.open(io.BytesIO(img)).convert("RGBA")
    pad = max(8, logo.height // 6)
    w, h = logo.width + 2 * pad, logo.height + 2 * pad
    tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle([0, 0, w - 1, h - 1], radius=pad, fill=(255, 255, 255, 255))
    tile.alpha_composite(logo, (pad, pad))
    out = io.BytesIO()
    tile.save(out, format="PNG")
    return out.getvalue()


def sidebar_logo(brand: "Brand") -> bytes | None:
    if brand.dark:
        return brand.dark
    if brand.light:
        try:
            return on_white_tile(brand.light)
        except Exception:
            return None
    return None


def load_brand(s: Session, fallback_path: str | None = None) -> Brand:
    light = get_setting_blob(s, LOGO_LIGHT)
    dark = get_setting_blob(s, LOGO_DARK)
    if light is None and fallback_path:
        p = Path(fallback_path)
        if p.is_file():
            light = p.read_bytes()
    return Brand(light=light, dark=dark)


def data_uri(img: bytes) -> str:
    mime = "image/png"
    if img[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif img[:4] == b"<svg" or img[:5] == b"<?xml":
        mime = "image/svg+xml"
    return f"data:{mime};base64,{base64.b64encode(img).decode('ascii')}"
