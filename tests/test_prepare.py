from __future__ import annotations

import io

import pytest
from PIL import Image

from invoice_to_sheet.prepare import prepare


def test_pdf_renders_first_pages_and_flags_truncation(sample_pdf):
    d = prepare(sample_pdf, max_pages=2)
    assert d.kind == "pdf" and d.page_count == 3 and len(d.images) == 2 and d.truncated
    assert d.pdf_bytes and d.warnings
    img = Image.open(io.BytesIO(d.images[0]))
    assert img.format == "JPEG" and max(img.size) <= 1568


def test_image_is_resized(sample_png):
    d = prepare(sample_png, max_image_side=1000)
    assert d.kind == "image" and d.page_count == 1 and not d.truncated
    assert max(Image.open(io.BytesIO(d.images[0])).size) == 1000
    assert len(d.images_b64()[0]) > 100


def test_unsupported_and_too_large(tmp_path):
    f = tmp_path / "x.docx"
    f.write_bytes(b"x")
    with pytest.raises(ValueError):
        prepare(f)
    big = tmp_path / "big.png"
    big.write_bytes(b"0" * 2_000_000)
    with pytest.raises(ValueError):
        prepare(big, max_file_mb=1)
