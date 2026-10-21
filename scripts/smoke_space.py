#!/usr/bin/env python
"""Headless smoke test of the Space build: start app.py the way a Space does, check /health, run a sample and a batch.

    python scripts/smoke_space.py                                   # fake extractor, no API call, fresh counters
    python scripts/smoke_space.py --port 7892 --timeout 90 --python /path/to/fresh-venv/bin/python

The app is started with SPACE_ID set (so .env is ignored and the public-demo limits apply), DOCS_FAKE_EXTRACTOR=1
(the fields come from the dataset labels; no LLM is called, no key is passed along) and a fresh BUDGET_FILE, so
runs never share counters. ``--python`` runs app.py with another interpreter, e.g. a venv built from
requirements-space.txt. Exit code 0 = healthy.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEAN_PDF = ROOT / "data" / "synthetic" / "pdf" / "inv010.pdf"
PHOTO_JPG = ROOT / "data" / "synthetic" / "photo" / "inv002.jpg"
EXPECTED_LIMITS = {"file_mb": 10, "pages": 20, "files_per_batch": 10, "docs_per_day": 100, "usd_per_day": 0.5}


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_for_health(url: str, deadline_s: float, proc: subprocess.Popen) -> dict:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < deadline_s:
        if proc.poll() is not None:
            raise RuntimeError(f"app exited early with code {proc.returncode}")
        try:
            payload = fetch_json(url)
            payload["_startup_seconds"] = round(time.perf_counter() - t0, 1)
            return payload
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, ValueError):
            pass
        time.sleep(0.5)
    raise TimeoutError(f"no /health answer within {deadline_s} s")


def rows_of(value) -> list:
    """A Dataframe output: ``{"headers", "data", ...}`` from gradio_client, or a bare list of rows."""
    if isinstance(value, dict):
        return list(value.get("data") or [])
    return list(value or [])


def path_of(value) -> str | None:
    """A DownloadButton/File output: a bare local path, or ``{"__type__": "update", "value": path}``."""
    if isinstance(value, dict):
        inner = value.get("value")
        return inner.get("path") if isinstance(inner, dict) else inner
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=7892)
    parser.add_argument("--timeout", type=float, default=90, help="seconds to wait for /health")
    parser.add_argument("--log", type=Path, default=ROOT / "smoke_app.log", help="where the app's output goes")
    parser.add_argument("--python", default=sys.executable, help="interpreter that runs app.py (e.g. a fresh Space venv)")
    args = parser.parse_args(argv)

    budget_file = Path(tempfile.mkdtemp(prefix="invoice-to-sheet-smoke-")) / "budget.json"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "LLM_API_KEY")}  # the fake needs no key
    env.update({
        "SPACE_ID": os.environ.get("SPACE_ID", "local/invoice-to-sheet-smoke"),
        "GRADIO_SERVER_NAME": "127.0.0.1",
        "GRADIO_SERVER_PORT": str(args.port),
        "DOCS_FAKE_EXTRACTOR": "1",
        "BUDGET_FILE": str(budget_file),
        "GRADIO_ANALYTICS_ENABLED": "False",
    })
    base = f"http://127.0.0.1:{args.port}"
    problems: list[str] = []

    def check(name: str, ok: bool) -> None:
        print(f"  {'ok ' if ok else 'FAIL'} {name}")
        if not ok:
            problems.append(name)

    with args.log.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen([args.python, "app.py"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            health = wait_for_health(f"{base}/health", args.timeout, proc)
            print(f"health after {health['_startup_seconds']} s: {json.dumps({k: v for k, v in health.items() if not k.startswith('_')})}")
            check("fake extractor active", health.get("fake_extractor") is True)
            check("space mode (SPACE_ID honoured)", health.get("on_space") is True)
            check("demo limits applied", health.get("limits") == EXPECTED_LIMITS)

            from gradio_client import Client, handle_file  # installed with gradio

            client = Client(base, verbose=False)
            t0 = time.perf_counter()
            res = client.predict(api_name="/sample_clean")
            print(f"sample_clean via the Gradio API: {time.perf_counter() - t0:.1f} s, {len(res)} outputs")
            cells = " | ".join(str(c) for row in rows_of(res[1]) for c in row)
            check("fields table has the vendor", "Kestrel Software OÜ" in cells)
            check("fields table has the total", "2028.4" in cells)
            check("decision is auto_approved", "auto_approved" in str(res[3]))
            check("cost line shows dollars", "$" in str(res[4]))
            fields_in, lines_in = res[1], res[2]

            t0 = time.perf_counter()
            res = client.predict(fields_in, lines_in, api_name="/append")  # the session state still holds the sample
            print(f"append via the Gradio API: {time.perf_counter() - t0:.1f} s")
            check("append: decision stays auto_approved", "auto_approved" in str(res[0]))
            csv_path = path_of(res[1])
            appended = []
            if csv_path and Path(csv_path).exists():
                with Path(csv_path).open(newline="", encoding="utf-8") as fh:
                    appended = list(csv.reader(fh))
            check("append: invoices.csv downloadable with header + 1 row", len(appended) == 2 and "Kestrel Software OÜ" in appended[1])

            t0 = time.perf_counter()
            res = client.predict(handle_file(str(PHOTO_JPG)), api_name="/extract")
            print(f"extract (upload) via the Gradio API: {time.perf_counter() - t0:.1f} s, {len(res)} outputs")
            cells = " | ".join(str(c) for row in rows_of(res[1]) for c in row)
            check("upload: preview image returned", bool(res[0]))
            check("upload: fields table has the photo's total", "1233.99" in cells)

            t0 = time.perf_counter()
            res = client.predict([handle_file(str(CLEAN_PDF)), handle_file(str(PHOTO_JPG))], api_name="/batch")
            print(f"batch of 2 via the Gradio API: {time.perf_counter() - t0:.1f} s")
            summary = rows_of(res[0])
            check("batch summary: 2 rows, both auto_approved", len(summary) == 2 and all(str(r[1]) == "auto_approved" for r in summary))
            csv_path = path_of(res[1])
            records = []
            if csv_path and Path(csv_path).exists():
                with Path(csv_path).open(newline="", encoding="utf-8") as fh:
                    records = list(csv.reader(fh))
            check("batch CSV downloadable with header + 2 rows", len(records) == 3)

            health = fetch_json(f"{base}/health")
            check("health counts 4 documents today (sample + upload + batch of 2)", health.get("budget", {}).get("docs_today") == 4)
        except Exception as exc:  # report, do not traceback
            problems.append(f"{type(exc).__name__}: {exc}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    if problems:
        print("SMOKE FAILED:", "; ".join(problems), f"(app log: {args.log})")
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
