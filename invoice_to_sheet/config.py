"""Settings from environment variables / .env. Every knob lives here."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    # --- LLM provider ------------------------------------------------------
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openai_compat"))  # openai_compat | anthropic
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://api.groq.com/openai/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", ""))  # a vision-capable model
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    # --- document preparation ----------------------------------------------
    max_pages: int = field(default_factory=lambda: int(_env("MAX_PAGES", "4")))
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
    google_service_account_json_b64: str = field(default_factory=lambda: _env("GOOGLE_SERVICE_ACCOUNT_JSON", ""))
    # --- demo protection -------------------------------------------------------
    max_daily_usd: float = field(default_factory=lambda: float(_env("MAX_DAILY_USD", "2")))
    budget_file: Path = field(default_factory=lambda: Path(_env("BUDGET_FILE", ".budget.json")))
    max_files_per_batch: int = field(default_factory=lambda: int(_env("MAX_FILES_PER_BATCH", "10")))


settings = Settings()
