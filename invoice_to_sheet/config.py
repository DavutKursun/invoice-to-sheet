"""Settings from environment variables / .env. Every knob lives here.

Space mode: Hugging Face sets ``SPACE_ID``. On a Space ``.env`` is never read (secrets come from the Space
settings) and the public-demo defaults apply (Claude Haiku, 20 pages, $0.50 and 100 documents per UTC day,
budget file under the temp directory). An explicitly set variable always wins over a default, on a Space too.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    """KEY=VALUE lines; surrounding quotes removed; an inline " #" comment is stripped from unquoted values.

    Never overrides a real environment variable (``os.environ.setdefault``).
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif value.startswith("#"):  # KEY=   # only a comment
            value = ""
        else:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


def on_space() -> bool:
    """True on Hugging Face Spaces (they set SPACE_ID): secrets come from the environment, never from .env."""
    return bool(os.environ.get("SPACE_ID"))


def load_dotenv_unless_on_space(path: Path = Path(".env")) -> bool:
    """Read ``path`` into the environment for local development; a Space never reads it. Returns whether it was read."""
    if on_space():
        return False
    _load_dotenv(path)
    return True


load_dotenv_unless_on_space()  # module import time: .env is merged (setdefault) before Settings() is built


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _demo(space_default: str, local_default: str) -> str:
    """The public-demo default on a Space, the local one otherwise. Fields read _env(NAME, _demo(...)): an explicit variable wins."""
    return space_default if on_space() else local_default


def _space_budget_file() -> str:
    return str(Path(tempfile.gettempdir()) / "invoice-to-sheet" / "budget.json")


@dataclass(frozen=True)
class Settings:
    # --- LLM provider ------------------------------------------------------
    # openai_compat | anthropic: locally the free Groq path, on a Space the measured Claude Haiku path (CLAUDE.md §0)
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", _demo("anthropic", "openai_compat")))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://api.groq.com/openai/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", ""), repr=False)  # never in repr/logs
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", ""))  # a vision-capable model
    # Ask the server to enforce the JSON schema (response_format=json_schema). Off by default: Groq's
    # constrained decoder turned Ü/ı/Ş into control characters; JSON mode + Pydantic keeps them.
    use_json_schema: bool = field(default_factory=lambda: _env("USE_JSON_SCHEMA", "0").strip().lower() in ("1", "true", "yes"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY", ""), repr=False)
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", _demo("claude-haiku-5-5", "claude-sonnet-5-5")))
    # --- document preparation ----------------------------------------------
    max_pages: int = field(default_factory=lambda: int(_env("MAX_PAGES", _demo("20", "4"))))
    render_dpi: int = field(default_factory=lambda: int(_env("RENDER_DPI", "150")))
    max_image_side: int = field(default_factory=lambda: int(_env("MAX_IMAGE_SIDE", "1568")))
    max_file_mb: int = field(default_factory=lambda: int(_env("MAX_FILE_MB", "10")))
    # --- validation ----------------------------------------------------------
    amount_tolerance_abs: float = field(default_factory=lambda: float(_env("AMOUNT_TOLERANCE_ABS", "0.02")))
    amount_tolerance_rel: float = field(default_factory=lambda: float(_env("AMOUNT_TOLERANCE_REL", "0.01")))
    min_confidence: float = field(default_factory=lambda: float(_env("MIN_CONFIDENCE", "0.85")))
    # --- output ----------------------------------------------------------------
    output_dir: Path = field(default_factory=lambda: Path(_env("OUTPUT_DIR", "output")))
    sheet_id: str = field(default_factory=lambda: _env("SHEET_ID", ""))
    google_service_account_json_b64: str = field(default_factory=lambda: _env("GOOGLE_SERVICE_ACCOUNT_JSON", ""), repr=False)
    # --- demo protection (per UTC day, for the whole deployment) ---------------
    max_daily_usd: float = field(default_factory=lambda: float(_env("MAX_DAILY_USD", _demo("0.50", "2"))))
    max_daily_docs: int = field(default_factory=lambda: int(_env("MAX_DAILY_DOCS", _demo("100", "0"))))  # 0 = no cap
    budget_file: Path = field(default_factory=lambda: Path(_env("BUDGET_FILE", _demo(_space_budget_file(), ".budget.json"))))
    max_files_per_batch: int = field(default_factory=lambda: int(_env("MAX_FILES_PER_BATCH", "10")))
    # DOCS_FAKE_EXTRACTOR=1: answers copied from data/synthetic/labels.jsonl instead of an LLM (headless smoke tests only)
    fake_extractor: bool = field(default_factory=lambda: _env("DOCS_FAKE_EXTRACTOR", "0").strip().lower() in ("1", "true", "yes"))
    on_space: bool = field(default_factory=on_space)


settings = Settings()
