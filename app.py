"""invoice-to-sheet — the Gradio app. Entry point for Hugging Face Spaces: ``python app.py`` serves on port 7860.

Tab "Single document": upload a PDF or image (or press a sample button) and see the rendered first page next to
the extracted fields (editable), the line items, the decision badge with its reasons and the cost. "Append to
CSV" re-validates the edited fields (with the duplicate check within the session) and appends the row to the
session's ``invoices.csv`` or ``review.csv``, which can then be downloaded. Tab "Batch": up to
``MAX_FILES_PER_BATCH`` files with a progress bar, a summary table and a downloadable ``batch.csv``.

Served the standard Gradio way (``demo.launch()`` on ``GRADIO_SERVER_NAME``/``GRADIO_SERVER_PORT``), which is what
the Spaces runtime expects; ``/health`` is added to Gradio's own FastAPI app after launch and reports only
counters, never document text or the key.

Public-demo behaviour: on a Space (``SPACE_ID`` set) the provider is Claude Haiku 5.5 and the page, file, batch
and per-day limits come from ``Settings``. Without an API key the app starts anyway and shows a "demo key not
configured" banner instead of crashing: no provider is created at import time, the first extraction creates it
under a lock. ``DOCS_FAKE_EXTRACTOR=1`` swaps in the fake extractor (fields copied from the dataset labels) for
headless smoke tests. Handlers never raise: every error becomes a ``gr.Warning`` and the outputs stay as they were.

Google Sheets is never used here: the app writes per-session CSVs in a temp folder. The local Sheets mode stays
available through ``scripts/run_folder.py``.
"""

from __future__ import annotations

import os

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")  # before gradio is imported

import csv
import html
import inspect
import io
import logging
import math
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import gradio as gr
from PIL import Image
from pydantic import ValidationError

from invoice_to_sheet import __version__
from invoice_to_sheet.config import Settings, settings
from invoice_to_sheet.costs import BudgetExceeded, BudgetGuard, format_cost
from invoice_to_sheet.fake import FakeExtractor
from invoice_to_sheet.pipeline import ProcessedDoc, process_document
from invoice_to_sheet.providers import Provider, ProviderError, get_provider
from invoice_to_sheet.schema import InvoiceExtraction
from invoice_to_sheet.sheets import COLUMNS, CsvWriter
from invoice_to_sheet.validation import validate

log = logging.getLogger("invoice_to_sheet.app")

TITLE = "invoice-to-sheet"
TAGLINE = (
    "Invoices and receipts (PDF, JPG, PNG) → the fields a bookkeeper needs, checked with arithmetic and date rules "
    "and appended to a spreadsheet; doubtful documents go to a review queue with the reason."
)
ROOT = Path(__file__).resolve().parent
SAMPLES = {
    "clean": ROOT / "data/synthetic/pdf/inv010.pdf",  # Kestrel Software OÜ, EUR, total 2028.40
    "photo": ROOT / "data/synthetic/photo/inv002.jpg",  # Kestrel Software OÜ, EUR, total 1233.99
    "tricky": ROOT / "data/synthetic/pdf/inv030.pdf",  # Aria Air GmbH, injected footer instruction, total 1725.82
}
FIELD_ROWS = [
    "document_type", "vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date", "currency",
    "subtotal", "discount", "tax_amount", "total", "payment_terms", "confidence", "low_confidence_fields", "notes",
]
NUMERIC_FIELDS = {"subtotal", "discount", "tax_amount", "total", "confidence"}
LINE_HEADERS = ["description", "quantity", "unit_price", "amount"]
SUMMARY_HEADERS = ["file", "decision", "total", "cost"]
FILE_TYPES = [".pdf", ".jpg", ".jpeg", ".png", ".webp"]
NO_FILE_MESSAGE = "Upload a PDF or image first"
SESSION_TTL_S = 3600  # session CSVs and Gradio's upload cache are pruned after an hour
NO_KEY_MESSAGE = "Demo key not configured: set ANTHROPIC_API_KEY as a Space secret (or LLM_API_KEY and LLM_MODEL)."

_providers: dict[tuple, tuple[Provider, str]] = {}
_lock = threading.Lock()


# --- configuration helpers (pure; every one takes cfg so tests can pass their own Settings) -----------------------


def key_configured(cfg: Settings = settings) -> bool:
    """Can this deployment extract at all? (the fake extractor counts; it is for smoke tests)"""
    if cfg.fake_extractor:
        return True
    if cfg.llm_provider == "anthropic":
        return bool(cfg.anthropic_api_key)
    return bool(cfg.llm_api_key and cfg.llm_model)


def model_id(cfg: Settings = settings) -> str:
    return cfg.anthropic_model if cfg.llm_provider == "anthropic" else cfg.llm_model


def provider_name(cfg: Settings = settings) -> str:
    return "fake" if cfg.fake_extractor else cfg.llm_provider


def banner_text(cfg: Settings = settings) -> str | None:
    if cfg.fake_extractor:
        return (
            "⚠️ **Fake extractor active** (smoke-test mode): the fields are copied from the sample dataset's labels, "
            "not extracted by a model."
        )
    if not key_configured(cfg):
        return (
            "⚠️ **Demo key not configured.** Set `ANTHROPIC_API_KEY` as a Space secret (or `LLM_API_KEY` and `LLM_MODEL` "
            "for an OpenAI-compatible provider) to enable extraction. The page loads, but documents cannot be processed."
        )
    return None


def budget_guard(cfg: Settings = settings) -> BudgetGuard:
    """The shared daily guard: a dollar cap and a documents-per-day cap (0 = no document cap)."""
    return BudgetGuard(cfg.budget_file, cfg.max_daily_usd, cfg.max_daily_docs or None)


def status_text(cfg: Settings = settings) -> str:
    guard = budget_guard(cfg)
    left = guard.remaining_docs()
    docs = f"{left} of {cfg.max_daily_docs}" if left is not None else "unlimited"
    return (
        f"Model `{model_id(cfg) or 'not configured'}` via {provider_name(cfg)} · documents left today: {docs} · "
        f"budget left today: ${guard.remaining_usd():.2f} of ${cfg.max_daily_usd:.2f} · "
        f"limits: {cfg.max_file_mb} MB per file, {cfg.max_pages} pages, {cfg.max_files_per_batch} files per batch"
    )


def health_payload(cfg: Settings = settings) -> dict:
    """``GET /health``: counters only, never document content or the key."""
    guard = budget_guard(cfg)
    spend = guard.read()
    return {
        "status": "ok",
        "version": __version__,
        "model": model_id(cfg) or None,
        "provider": provider_name(cfg),
        "key_configured": key_configured(cfg),
        "fake_extractor": cfg.fake_extractor,
        "on_space": cfg.on_space,
        "limits": {
            "file_mb": cfg.max_file_mb,
            "pages": cfg.max_pages,
            "files_per_batch": cfg.max_files_per_batch,
            "docs_per_day": cfg.max_daily_docs or None,
            "usd_per_day": cfg.max_daily_usd,
        },
        "budget": {
            "docs_today": spend.calls,
            "usd_today": round(spend.usd, 6),
            "docs_left_today": guard.remaining_docs(),
            "usd_left_today": round(guard.remaining_usd(), 6),
        },
    }


def provider_and_model(cfg: Settings = settings) -> tuple[Provider, str]:
    """The provider for this configuration, created on first use and cached per process (never at import)."""
    key = (cfg.fake_extractor, cfg.llm_provider, cfg.llm_base_url, model_id(cfg))
    with _lock:
        if key not in _providers:
            _providers[key] = (FakeExtractor(), model_id(cfg)) if cfg.fake_extractor else get_provider(cfg)
        return _providers[key]


def about_text(cfg: Settings = settings) -> str:
    docs = f"{cfg.max_daily_docs} documents" if cfg.max_daily_docs else "no document cap"
    return (
        "**Synthetic data.** The three samples are generated invoices with exact labels, from the dataset the accuracy "
        "numbers were measured on (every scored field correct on the held-out test split with Claude Haiku 5.5). "
        "Results on real documents will differ: they are messier and this demo has not been tuned on them.\n\n"
        "**What gets stored.** Nothing beyond your session: uploads are processed in memory and a temporary folder, "
        "the session CSV lives in a temp folder, is deleted after an hour of inactivity and disappears when the Space restarts, and no document text is logged.\n\n"
        f"**Limits.** {cfg.max_file_mb} MB per file, {cfg.max_pages} pages per document, {cfg.max_files_per_batch} files per "
        f"batch, and for the whole demo {docs} and ${cfg.max_daily_usd:.2f} per UTC day (resets at 00:00 UTC).\n\n"
        f"**Model and cost.** `{model_id(cfg) or 'not configured'}` via {provider_name(cfg)}: one call with a strict schema "
        "(structured outputs), then arithmetic and date validation in plain Python; about $0.001 per document with Claude "
        "Haiku 5.5. Documents that fail a check go to the review queue with the reason instead of into the sheet.\n\n"
        "Source code, design notes and the evaluation: https://github.com/DavutKursun/invoice-to-sheet"
    )


# --- table and text helpers -------------------------------------------------------------------------------------


def fields_table(data: InvoiceExtraction) -> list[list[str]]:
    """``[[field, value], ...]`` in FIELD_ROWS order for the editable table; lists are joined with ", "."""
    d = data.model_dump()
    return [[name, ", ".join(str(x) for x in d[name]) if isinstance(d[name], list) else str(d[name])] for name in FIELD_ROWS]


def line_rows(data: InvoiceExtraction) -> list[list]:
    return [[li.description, li.quantity, li.unit_price, li.amount] for li in data.line_items]


def _cells(table) -> list[list]:
    """Rows of a gr.Dataframe value: a pandas DataFrame, a ``{"data": ...}`` dict, a list of lists, or None."""
    if table is None:
        return []
    if hasattr(table, "to_numpy"):  # pandas, what gr.Dataframe delivers to a handler
        return table.to_numpy(dtype=object).tolist()
    if isinstance(table, dict):
        return [list(r) for r in table.get("data") or []]
    return [list(r) for r in table]


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and not v.strip())


def _text(v) -> str:
    return "" if _blank(v) else str(v).strip()


def _number(field: str, v) -> float:
    if _blank(v):
        return 0.0
    if isinstance(v, bool):
        raise ValueError(f"{field}: {v!r} is not a number")
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError:
        raise ValueError(f"{field}: {v!r} is not a number (digits with a dot as decimal separator, e.g. 1234.56)") from None


def table_to_extraction(fields_rows, line_rows, base: InvoiceExtraction) -> InvoiceExtraction:
    """Rebuild the extraction from the edited tables; ``ValueError`` with a readable message on bad input."""
    d = base.model_dump()
    for row in _cells(fields_rows):
        if len(row) < 2 or _text(row[0]) not in FIELD_ROWS:
            continue
        name, value = _text(row[0]), row[1]
        if name in NUMERIC_FIELDS:
            d[name] = _number(name, value)
        elif name == "low_confidence_fields":
            d[name] = [p.strip() for p in _text(value).split(",") if p.strip()]
        elif name == "document_type":
            d[name] = _text(value).lower()
        else:
            d[name] = _text(value)
    items = []
    for i, row in enumerate(_cells(line_rows), 1):
        row = list(row) + [None] * (4 - len(row))
        if all(_blank(c) for c in row[:4]):
            continue
        items.append({
            "description": _text(row[0]), "quantity": _number(f"line {i} quantity", row[1]),
            "unit_price": _number(f"line {i} unit_price", row[2]), "amount": _number(f"line {i} amount", row[3]),
        })
    if items:  # an empty table (nothing arrived) keeps the extracted line items
        d["line_items"] = items
    try:
        return InvoiceExtraction.model_validate(d)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ValueError(f"Edited fields do not fit the schema: {problems}") from None


def decision_html(decision: str, reasons: list[str]) -> str:
    """A green (auto_approved) or amber (needs_review) badge plus the reasons as a list; everything escaped."""
    colours = {"auto_approved": ("#dcfce7", "#166534"), "needs_review": ("#fef3c7", "#92400e")}
    bg, fg = colours.get(decision, ("#e5e7eb", "#374151"))
    badge = (
        f'<span style="display:inline-block;padding:4px 14px;border-radius:999px;background:{bg};color:{fg};'
        f'font-weight:600;font-family:monospace">{html.escape(decision)}</span>'
    )
    items = "".join(f"<li>{html.escape(r)}</li>" for r in reasons)
    return f"<div><strong>Decision</strong> {badge}{f'<ul>{items}</ul>' if items else ''}</div>"


def _pages_read(out: ProcessedDoc) -> int | None:
    """How many pages were read when the document was cut at the page limit (None when it was not)."""
    for text in (*out.reasons, *out.warnings):
        m = re.search(r"only the first (\d+)", text)
        if m:
            return int(m.group(1))
    return None


def cost_line(out: ProcessedDoc) -> str:
    read = _pages_read(out)
    n = out.pages if read is None else read
    pages = f"{n} page{'s' if n != 1 else ''}" + (f" of {out.pages} read" if read is not None else "")
    line = f"cost {out.cost_label} · {out.seconds:.1f} s · {out.model or 'unknown model'} · {pages}"
    return line + (" · repaired once" if out.repaired else "")


# --- sessions and CSV files -------------------------------------------------------------------------------------


def _safe_session(session_id) -> str:
    sid = str(session_id or "")
    return sid if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid) else uuid.uuid4().hex


def sessions_root() -> Path:
    return Path(tempfile.gettempdir()) / "invoice-to-sheet" / "sessions"


def session_dir(session_id: str) -> Path:
    return sessions_root() / _safe_session(session_id)


def cleanup_sessions(max_age_s: float = SESSION_TTL_S, now: float | None = None, root: Path | None = None) -> int:
    """Delete session folders whose newest file is older than ``max_age_s``; called on every request. Returns the count.

    Uploads live in Gradio's own cache, which ``gr.Blocks(delete_cache=...)`` prunes on the same schedule.
    """
    root = root or sessions_root()
    now = time.time() if now is None else now
    removed = 0
    if not root.is_dir():
        return 0
    for folder in root.iterdir():
        try:
            if not folder.is_dir():
                continue
            newest = max((f.stat().st_mtime for f in folder.rglob("*") if f.is_file()), default=folder.stat().st_mtime)
            if now - newest > max_age_s:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        log.info("pruned %d session folder(s) older than %d s", removed, max_age_s)
    return removed


def session_writer(session_id: str) -> CsvWriter:
    """The session's CSV pair (invoices.csv / review.csv) in a temp folder; gone when the Space restarts."""
    return CsvWriter(session_dir(session_id))


def _csv_rows(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as fh:
        return sum(1 for _ in csv.DictReader(fh))


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _placeholder_row(file_name: str, decision: str, message: str) -> dict:
    row = {c: "" for c in COLUMNS}
    row.update(processed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), file_name=file_name, decision=decision, reasons=message)
    return row


def _preview(out: ProcessedDoc):
    raw = getattr(out, "preview_jpeg", None)
    if not raw:
        return None
    img = Image.open(io.BytesIO(raw))
    img.load()
    return img


def _skip(n: int) -> tuple:
    return tuple(gr.skip() for _ in range(n))


# --- handlers (never raise: errors become gr.Warning toasts and the outputs stay as they were) --------------------


def on_load(session_id, cfg: Settings = settings):
    cleanup_sessions()
    return session_id or uuid.uuid4().hex, status_text(cfg)


def extract_single(file, session_id, progress=gr.Progress(), cfg: Settings = settings):
    """Prepare → extract → validate one upload; outputs preview, tables, decision, cost, download, state, status."""
    cleanup_sessions()
    if not file:
        gr.Warning(NO_FILE_MESSAGE)
        return _skip(8)
    if not key_configured(cfg):
        gr.Warning(NO_KEY_MESSAGE)
        return _skip(8)
    path = str(file)  # gr.File gives a NamedString (a str with .name); samples give a plain path
    try:
        provider, model = provider_and_model(cfg)
        guard = budget_guard(cfg)
        progress(0.1, desc=f"{Path(path).name}: rendering and extracting …")
        out = process_document(path, provider, model, writer=None, cfg=cfg, guard=guard, keep_preview=True)
    except (BudgetExceeded, ProviderError, ValueError, RuntimeError) as exc:
        gr.Warning(str(exc))
        return _skip(8)
    except Exception as exc:  # a corrupt image or PDF must not take the handler down
        log.exception("extract_single failed")
        gr.Warning(f"Unexpected error ({type(exc).__name__}): {exc}")
        return _skip(8)
    progress(1.0, desc="done")
    state = {
        "file_name": out.file_name, "decision": out.decision, "reasons": list(out.reasons), "data": out.data.model_dump(),
        "model": out.model, "cost_usd": out.cost_usd, "seconds": out.seconds, "pages": out.pages,
    }
    return (
        _preview(out), fields_table(out.data), line_rows(out.data), decision_html(out.decision, out.reasons), cost_line(out),
        gr.DownloadButton(visible=False), state, status_text(cfg),
    )


def run_sample(kind: str, session_id, progress=gr.Progress(), cfg: Settings = settings):
    return extract_single(str(SAMPLES[kind]), session_id, progress, cfg)


def append_to_csv(fields_rows, line_rows, doc_state, session_id, cfg: Settings = settings):
    """Re-validate the edited tables (duplicate check within the session) and append the row to the session CSV."""
    cleanup_sessions()
    if not doc_state:
        gr.Warning("Nothing extracted yet: extract a document first.")
        return _skip(3)
    try:
        base = InvoiceExtraction.model_validate(doc_state["data"])
        data = table_to_extraction(fields_rows, line_rows, base)
        writer = session_writer(session_id)
        reasons = validate(
            data, tol_abs=cfg.amount_tolerance_abs, tol_rel=cfg.amount_tolerance_rel, min_confidence=cfg.min_confidence,
            existing_keys=writer.existing_keys(),
        )
        reasons += [r for r in doc_state.get("reasons", []) if "pages were read" in r]  # a page cut is still true after edits
        decision = "auto_approved" if not reasons else "needs_review"
        model, usd = str(doc_state.get("model", "")), float(doc_state.get("cost_usd", 0.0))
        out = ProcessedDoc(
            str(doc_state.get("file_name", "")), decision, reasons, data, model, usd, format_cost(model, usd),
            float(doc_state.get("seconds", 0.0)), False, int(doc_state.get("pages", 1)),
        )
        review = decision == "needs_review"
        writer.append(out.row(), review=review)
    except (BudgetExceeded, ProviderError, ValueError, RuntimeError) as exc:
        gr.Warning(str(exc))
        return _skip(3)
    except Exception as exc:
        log.exception("append_to_csv failed")
        gr.Warning(f"Unexpected error ({type(exc).__name__}): {exc}")
        return _skip(3)
    target = writer.review if review else writer.invoices
    n = _csv_rows(target)
    gr.Info(f"Appended to {target.name} ({n} row{'s' if n != 1 else ''})")
    return (
        decision_html(decision, reasons),
        gr.DownloadButton(value=str(target), visible=True, label=f"Download {target.name}"),
        status_text(cfg),
    )


def run_batch(files, session_id, progress=gr.Progress(), cfg: Settings = settings):
    """Process up to ``max_files_per_batch`` uploads; the daily cap stops the loop, a bad file does not."""
    cleanup_sessions()
    paths = [str(f) for f in (files or [])]
    if not paths:
        gr.Warning(f"Upload up to {cfg.max_files_per_batch} PDF or image files first")
        return _skip(3)
    if len(paths) > cfg.max_files_per_batch:
        gr.Warning(f"{len(paths)} files uploaded, but the limit is {cfg.max_files_per_batch} per batch; nothing was processed.")
        return _skip(3)
    if not key_configured(cfg):
        gr.Warning(NO_KEY_MESSAGE)
        return _skip(3)
    try:
        provider, model = provider_and_model(cfg)
    except (ProviderError, ValueError, RuntimeError) as exc:
        gr.Warning(str(exc))
        return _skip(3)
    guard = budget_guard(cfg)
    rows: list[dict] = []
    summary: list[list] = []
    stopped: str | None = None
    n = len(paths)
    for i, path in enumerate(paths):
        name = Path(path).name
        progress((i, n), desc=name)
        if stopped is not None:
            rows.append(_placeholder_row(name, "skipped", stopped))
            summary.append([name, "skipped", None, ""])
            continue
        try:
            out = process_document(path, provider, model, writer=None, cfg=cfg, guard=guard)
        except BudgetExceeded as exc:
            stopped = str(exc)
            gr.Warning(stopped)
            rows.append(_placeholder_row(name, "skipped", stopped))
            summary.append([name, "skipped", None, ""])
            continue
        except Exception as exc:  # ProviderError, ValueError, a corrupt file: this row shows the error, the loop goes on
            if not isinstance(exc, (ProviderError, ValueError, RuntimeError)):
                log.exception("run_batch failed on one file")
            rows.append(_placeholder_row(name, "error", str(exc)))
            summary.append([name, "error", None, ""])
            continue
        rows.append(out.row())
        summary.append([name, out.decision, out.data.total, out.cost_label])
    progress((n, n), desc="done")
    target = session_dir(session_id) / "batch.csv"
    _write_csv(target, rows)
    gr.Info(f"{n} file{'s' if n != 1 else ''} processed; batch.csv is ready")
    return summary, gr.DownloadButton(value=str(target), visible=True), status_text(cfg)


# --- UI -----------------------------------------------------------------------------------------------------------


def _bind(fn, cfg: Settings):
    """``fn`` with ``cfg`` fixed, exposing a signature without ``cfg``.

    Gradio reads the handler's signature to inject ``gr.Progress`` and to fill trailing parameters that have a
    default with that default, so a visible ``cfg`` would arrive twice (positionally from Gradio and here).
    """
    sig = inspect.signature(fn)

    def bound(*args, **kwargs):
        return fn(*args, cfg=cfg, **kwargs)

    bound.__name__, bound.__doc__ = fn.__name__, fn.__doc__
    bound.__signature__ = sig.replace(parameters=[p for p in sig.parameters.values() if p.name != "cfg"])
    return bound


def _sample(kind: str, cfg: Settings):
    def handler(session_id, progress=gr.Progress()):
        return run_sample(kind, session_id, progress, cfg)

    handler.__name__ = f"sample_{kind}"
    return handler


def build_ui(cfg: Settings = settings) -> gr.Blocks:
    with gr.Blocks(title=TITLE, delete_cache=(SESSION_TTL_S, SESSION_TTL_S)) as demo:  # uploads: pruned hourly after an hour
        session_state = gr.State(None)
        doc_state = gr.State(None)

        gr.Markdown(f"# {TITLE}\n\n{TAGLINE}")
        banner = banner_text(cfg)
        if banner:
            gr.Markdown(banner)
        status_md = gr.Markdown(status_text(cfg))

        with gr.Tabs():
            with gr.Tab("Single document"):
                with gr.Row():
                    with gr.Column(scale=1, min_width=320):
                        upload = gr.File(
                            label=f"PDF or image (max {cfg.max_file_mb} MB, {cfg.max_pages} pages)",
                            file_count="single", file_types=FILE_TYPES, type="filepath",
                        )
                        extract_btn = gr.Button("Extract", variant="primary")
                        with gr.Row():
                            clean_btn = gr.Button("Try a sample: clean invoice", size="sm")
                            photo_btn = gr.Button("phone photo", size="sm")
                            tricky_btn = gr.Button("tricky", size="sm")
                        preview = gr.Image(label="First page", type="pil", format="png", height=560, interactive=False)
                    with gr.Column(scale=1, min_width=320):
                        decision = gr.HTML("")
                        cost_md = gr.Markdown("")
                        fields_df = gr.Dataframe(
                            headers=["field", "value"], datatype=["str", "str"], column_count=2, interactive=True,
                            static_columns=[0], wrap=True, label="Extracted fields (edit before appending)",
                        )
                        lines_df = gr.Dataframe(
                            headers=LINE_HEADERS, datatype=["str", "number", "number", "number"], interactive=False, label="Line items",
                        )
                        with gr.Row():
                            append_btn = gr.Button("Append to CSV")
                            download_btn = gr.DownloadButton("Download CSV", visible=False)
            with gr.Tab("Batch"):
                batch_files = gr.File(
                    file_count="multiple", type="filepath", label=f"Up to {cfg.max_files_per_batch} files", file_types=FILE_TYPES,
                )
                batch_btn = gr.Button("Process batch", variant="primary")
                summary_df = gr.Dataframe(
                    headers=SUMMARY_HEADERS, datatype=["str", "str", "number", "str"], interactive=False, label="Summary",
                )
                batch_download = gr.DownloadButton("Download batch CSV", visible=False)

        with gr.Accordion("About", open=False):
            gr.Markdown(about_text(cfg))

        extract_outputs = [preview, fields_df, lines_df, decision, cost_md, download_btn, doc_state, status_md]
        demo.load(_bind(on_load, cfg), [session_state], [session_state, status_md], api_name="load")
        extract_btn.click(_bind(extract_single, cfg), [upload, session_state], extract_outputs, api_name="extract")
        for btn, kind in ((clean_btn, "clean"), (photo_btn, "photo"), (tricky_btn, "tricky")):
            btn.click(_sample(kind, cfg), [session_state], extract_outputs, api_name=f"sample_{kind}")
        append_btn.click(
            _bind(append_to_csv, cfg), [fields_df, lines_df, doc_state, session_state], [decision, download_btn, status_md], api_name="append",
        )
        batch_btn.click(_bind(run_batch, cfg), [batch_files, session_state], [summary_df, batch_download, status_md], api_name="batch")
    return demo


def add_health_route(fastapi_app) -> None:
    """``GET /health`` on Gradio's FastAPI app (``demo.app`` once launched): JSON counters for monitoring."""
    fastapi_app.add_api_route("/health", lambda: health_payload(), methods=["GET"], include_in_schema=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = settings
    log.info(
        "space=%s provider=%s model=%s key_configured=%s fake_extractor=%s limits=%s MB/file, %s pages, %s files/batch, %s docs/day, $%.2f/day",
        cfg.on_space, provider_name(cfg), model_id(cfg) or "-", key_configured(cfg), cfg.fake_extractor,
        cfg.max_file_mb, cfg.max_pages, cfg.max_files_per_batch, cfg.max_daily_docs or "unlimited", cfg.max_daily_usd,
    )
    demo = build_ui()
    demo.queue(default_concurrency_limit=2)
    port_env = os.environ.get("GRADIO_SERVER_PORT")  # 7860 on a Space; locally Gradio picks the next free port
    demo.launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(port_env) if port_env else None,
        prevent_thread_lock=True,
        show_error=False,
        quiet=True,
        ssr_mode=False,  # keep uvicorn on the public port so /health is reachable
        max_file_size=f"{cfg.max_file_mb}mb",
    )
    add_health_route(demo.app)
    log.info("serving on %s (health: /health)", demo.local_url)
    demo.block_thread()


if __name__ == "__main__":
    main()
