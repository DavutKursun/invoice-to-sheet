"""Price table, cost per call, free-tier labelling and a daily budget guard.

Prices in USD per million tokens (list prices checked 2026-10-09 at
https://platform.claude.com/docs/en/about-claude/pricing). Models not in the table
are treated as free tier (cost 0, labelled "free tier", never "$0.0000").
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}


def is_priced(model: str) -> bool:
    return model in PRICES_PER_MTOK


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    if model not in PRICES_PER_MTOK:
        return 0.0
    i, o = PRICES_PER_MTOK[model]
    return (input_tokens * i + output_tokens * o) / 1_000_000


def format_cost(model: str, usd: float) -> str:
    return f"${usd:.4f}" if is_priced(model) else "free tier"


@dataclass
class Spend:
    date: str
    usd: float
    calls: int  # documents recorded today (one record() per document)


class BudgetExceeded(RuntimeError):
    """Daily cap reached; the message is safe to show to the user."""


_LOCK = threading.Lock()  # one per process: record() is a read-modify-write on the file


class BudgetGuard:
    """Today's spend and document count in a small JSON file; refuses work past either daily cap.

    Days are UTC (``time.gmtime``), so the counters reset implicitly at 00:00 UTC. ``max_daily_docs`` None
    or 0 means no document cap; the dollar cap alone would never trip for free-tier models that record $0.
    """

    def __init__(self, path: str | Path, max_daily_usd: float, max_daily_docs: int | None = None, clock=time.time) -> None:
        self.path = Path(path)
        self.max_daily_usd = max_daily_usd
        self.max_daily_docs = max_daily_docs or None
        self._clock = clock

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self._clock()))

    def read(self) -> Spend:
        if not self.path.exists():
            return Spend(self._today(), 0.0, 0)
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Spend(self._today(), 0.0, 0)
        if not isinstance(d, dict) or d.get("date") != self._today():
            return Spend(self._today(), 0.0, 0)
        return Spend(d["date"], float(d.get("usd", 0.0)), int(d.get("calls", 0)))

    def _write(self, s: Spend) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".budget-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"date": s.date, "usd": s.usd, "calls": s.calls}, fh)
        os.replace(tmp, self.path)

    def can_spend(self, estimated_usd: float = 0.0) -> bool:
        s = self.read()
        if self.max_daily_docs is not None and s.calls >= self.max_daily_docs:
            return False
        return s.usd + estimated_usd <= self.max_daily_usd

    def docs_exhausted(self) -> bool:
        return self.max_daily_docs is not None and self.read().calls >= self.max_daily_docs

    def remaining_docs(self) -> int | None:
        return None if self.max_daily_docs is None else max(self.max_daily_docs - self.read().calls, 0)

    def remaining_usd(self) -> float:
        return max(self.max_daily_usd - self.read().usd, 0.0)

    def record(self, usd: float) -> Spend:
        """Add one document and its cost; read-modify-write under the lock, written atomically."""
        with _LOCK:
            s = self.read()
            s = Spend(s.date, s.usd + usd, s.calls + 1)
            self._write(s)
        return s

    def exhausted_message(self) -> str:
        s = self.read()
        if self.docs_exhausted():
            return f"Daily demo budget reached ({s.calls} of {self.max_daily_docs} documents today); resets at 00:00 UTC."
        return f"Daily demo budget reached (${s.usd:.2f} of ${self.max_daily_usd:.2f} spent today); resets at 00:00 UTC."

    def check(self, estimated_usd: float = 0.01) -> None:
        """Raise BudgetExceeded (a RuntimeError) with the user-facing message when the next document would pass a cap."""
        if not self.can_spend(estimated_usd):
            raise BudgetExceeded(self.exhausted_message())
