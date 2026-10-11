"""Turn an uploaded file into a list of JPEG images ready for a vision model.

PDF → pages rendered with pypdfium2 (first ``max_pages``); images → resized so the
longest side ≤ ``max_image_side`` and re-encoded as JPEG. The raw PDF bytes are kept
too, so the Anthropic provider (native PDF input) can send the document as is.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class PreparedDoc:
    file_name: str
    kind: str  # "pdf" | "image"
    images: list[bytes]  # JPEG bytes, one per page
    page_count: int  # pages in the original PDF (1 for images)
    truncated: bool  # True if more pages existed than were rendered
    pdf_bytes: bytes | None = None
    warnings: list[str] = field(default_factory=list)

    def images_b64(self) -> list[str]:
        return [base64.standard_b64encode(b).decode() for b in self.images]


def _to_jpeg(img: Image.Image, max_side: int, quality: int = 88) -> bytes:
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    scale = max_side / max(w, h)
    if scale < 1:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def prepare(path: str | Path, max_pages: int = 4, render_dpi: int = 150, max_image_side: int = 1568, max_file_mb: int = 10) -> PreparedDoc:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f"Unsupported file type {path.name!r}; supported: {sorted(SUPPORTED)}")
    size_mb = path.stat().st_size / 1_000_000
    if size_mb > max_file_mb:
        raise ValueError(f"{path.name} is {size_mb:.1f} MB; the limit is {max_file_mb} MB")

    if suffix == ".pdf":
        import pypdfium2 as pdfium

        pdf_bytes = path.read_bytes()
        doc = pdfium.PdfDocument(pdf_bytes)
        n = len(doc)
        if n == 0:
            raise ValueError(f"{path.name} has no pages")
        images = []
        for i in range(min(n, max_pages)):
            page = doc[i]
            bitmap = page.render(scale=render_dpi / 72)
            images.append(_to_jpeg(bitmap.to_pil(), max_image_side))
        warnings = []
        if n > max_pages:
            warnings.append(f"{path.name}: {n} pages, only the first {max_pages} were read")
        return PreparedDoc(path.name, "pdf", images, n, n > max_pages, pdf_bytes, warnings)

    with Image.open(path) as img:
        img.load()
        return PreparedDoc(path.name, "image", [_to_jpeg(img, max_image_side)], 1, False)
