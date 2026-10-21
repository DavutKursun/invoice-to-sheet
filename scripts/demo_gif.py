#!/usr/bin/env python
"""Record the demo GIF against the live Space with Playwright (a throwaway venv; not in requirements).

    python -m venv /tmp/pw && /tmp/pw/bin/pip install playwright pillow && /tmp/pw/bin/playwright install chromium
    /tmp/pw/bin/python scripts/demo_gif.py --url https://davutkursun-invoice-to-sheet.hf.space --out docs/img/demo.gif

Flow (three real documents, about $0.003): the "phone photo" sample → fields and the decision badge →
"Append to CSV" → the Batch tab with two files → the summary table. Frames are screenshots taken while
waiting, so the GIF shows real latency; it is kept under 30 s and 3 MB by width, colours and frame count.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PDF = ROOT / "data" / "synthetic" / "pdf" / "inv010.pdf"
SAMPLE_JPG = ROOT / "data" / "synthetic" / "photo" / "inv002.jpg"


class Recorder:
    """Screenshots with a duration each; ``wait_for`` polls a condition and keeps snapping meanwhile."""

    def __init__(self, page, interval_s: float) -> None:
        self.page, self.interval_s = page, interval_s
        self.frames: list[tuple[bytes, int]] = []  # (png bytes, duration ms)

    def snap(self, hold_s: float | None = None) -> None:
        self.frames.append((self.page.screenshot(type="png"), int(1000 * (hold_s or self.interval_s))))

    def wait_for(self, condition, timeout_s: float, label: str) -> float:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout_s:
            if condition():
                seconds = time.perf_counter() - t0
                print(f"  {label}: {seconds:.1f} s")
                return seconds
            self.snap()
            time.sleep(self.interval_s)
        raise TimeoutError(f"{label}: not within {timeout_s} s")


def visible(page, text: str):
    return lambda: page.get_by_text(text, exact=False).first.is_visible()


def build_gif(frames: list[tuple[bytes, int]], out: Path, width: int, colours: int, max_mb: float, max_seconds: float) -> tuple[int, float]:
    from PIL import Image

    total_ms = sum(d for _, d in frames)
    if total_ms > max_seconds * 1000:  # scale every hold down proportionally; the flow stays the same
        scale = max_seconds * 1000 / total_ms
        frames = [(png, max(40, int(d * scale))) for png, d in frames]
    for attempt_width, attempt_colours in ((width, colours), (int(width * 0.85), max(64, colours // 2)), (int(width * 0.7), 64)):
        images, durations = [], []
        for png, d in frames:
            im = Image.open(io.BytesIO(png)).convert("RGB")
            im = im.resize((attempt_width, int(im.height * attempt_width / im.width)), Image.LANCZOS)
            images.append(im.quantize(colors=attempt_colours, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
            durations.append(d)
        out.parent.mkdir(parents=True, exist_ok=True)
        images[0].save(out, save_all=True, append_images=images[1:], duration=durations, loop=0, optimize=True)
        size_mb = out.stat().st_size / 1_000_000
        print(f"  gif {attempt_width}px, {attempt_colours} colours, {len(images)} frames, {sum(durations)/1000:.1f} s, {size_mb:.2f} MB")
        if size_mb <= max_mb:
            return len(images), sum(durations) / 1000
    raise RuntimeError(f"GIF is still over {max_mb} MB")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="https://davutkursun-invoice-to-sheet.hf.space")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "img" / "demo.gif")
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--colours", type=int, default=128)
    ap.add_argument("--interval", type=float, default=0.6, help="seconds between frames while waiting")
    ap.add_argument("--max-seconds", type=float, default=30)
    ap.add_argument("--max-mb", type=float, default=3)
    ap.add_argument("--timeout", type=float, default=60, help="seconds to wait for each extraction")
    a = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 860}, device_scale_factor=1)
        rec = Recorder(page, a.interval)
        print(f"open {a.url}")
        page.goto(a.url, wait_until="domcontentloaded", timeout=90_000)
        rec.wait_for(visible(page, "Try a sample"), 60, "page ready")
        rec.snap(1.2)

        page.get_by_role("button", name="phone photo").click()
        rec.wait_for(visible(page, "Kestrel Software OÜ"), a.timeout, "phone photo extracted")
        rec.snap(2.0)  # fields, line items and the badge
        page.get_by_text("Decision", exact=False).first.scroll_into_view_if_needed()
        rec.snap(1.2)

        page.get_by_role("button", name="Append to CSV").click()
        rec.wait_for(visible(page, "Download invoices.csv"), 30, "appended")
        rec.snap(1.5)

        page.get_by_role("tab", name="Batch").click()
        rec.wait_for(lambda: page.get_by_role("button", name="Process batch").is_visible(), 15, "batch tab")
        rec.snap(0.8)
        files_input = page.locator("input[type=file][multiple]")  # the batch upload, not a Dataframe's CSV input
        files_input = files_input.first if files_input.count() else page.locator('input[type=file][accept*=".pdf"]').last
        files_input.set_input_files([str(SAMPLE_PDF), str(SAMPLE_JPG)])
        rec.wait_for(visible(page, "inv002.jpg"), 20, "files listed")
        rec.snap(0.8)
        page.get_by_role("button", name="Process batch").click()
        rec.wait_for(lambda: page.get_by_text("Download batch CSV", exact=False).first.is_visible(), a.timeout, "batch processed")
        rec.snap(2.5)
        browser.close()

    n, seconds = build_gif(rec.frames, a.out, a.width, a.colours, a.max_mb, a.max_seconds)
    print(f"wrote {a.out} ({n} frames, {seconds:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
