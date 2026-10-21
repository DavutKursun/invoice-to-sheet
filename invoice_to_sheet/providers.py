"""LLM providers for structured extraction from document images.

* ``OpenAICompatProvider`` – any OpenAI-compatible chat API with vision (Groq, OpenRouter, …).
  Sends pages as image_url data URIs. Default: JSON mode (``response_format={"type": "json_object"}``)
  with the JSON schema spelled out in the prompt; the caller validates with Pydantic and repairs
  once. With ``use_json_schema=True`` (``USE_JSON_SCHEMA=1``) it asks the server to enforce the
  schema (``{"type": "json_schema"}``) first and uses JSON mode only when the server does not accept
  that request format. JSON mode is the default because Groq's schema-constrained decoder was seen
  turning Ü/ı/Ş into control characters (2026-10-11, 7 of 20 vendor names) while JSON mode kept them.
* ``AnthropicProvider`` – Claude with native PDF input (document block) or images, and
  ``client.messages.parse`` structured outputs. A PDF longer than ``max_pages`` is sent as the
  rendered page images, so the page limit holds for Claude too.

Both return ``ExtractResult(raw_json, input_tokens, output_tokens, model, elapsed,
usage_estimated, schema_enforced)``. Network errors become ``ProviderError``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Protocol

from .prepare import PreparedDoc
from .schema import InvoiceExtraction, json_schema


class ProviderError(RuntimeError):
    pass


@dataclass
class ExtractResult:
    raw_json: str
    input_tokens: int
    output_tokens: int
    model: str
    elapsed_seconds: float
    usage_estimated: bool = False
    schema_enforced: bool = False  # True when the server enforced the JSON schema


class Provider(Protocol):
    name: str

    def extract(self, doc: PreparedDoc, system: str, instruction: str, model: str, max_tokens: int = 2000) -> ExtractResult: ...


log = logging.getLogger(__name__)

JSON_OBJECT_FORMAT = {"type": "json_object"}
JSON_SCHEMA_FORMAT = {"type": "json_schema", "json_schema": {"name": "invoice", "schema": json_schema(), "strict": True}}
SCHEMA_IN_PROMPT = "\n\nThe JSON object must match this JSON Schema exactly (every field required, no other fields):\n{schema}"


def _error_fields(exc: Exception) -> dict:
    """The server's error object ({"message", "type", "code", ...}) from an OpenAI-style exception."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            return err
    return {}


def _error_code(exc: Exception) -> str:
    err = _error_fields(exc)
    return str(err.get("code") or err.get("type") or "")


def _wrap(exc: Exception) -> ProviderError:
    status = getattr(exc, "status_code", None)
    code = _error_code(exc)
    prefix = f"HTTP {status}" if status else type(exc).__name__
    return ProviderError(f"{prefix}{f' ({code})' if code else ''}: {exc}")


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, api_key: str, base_url: str, client=None, use_json_schema: bool = False) -> None:
        if client is None:
            from openai import OpenAI

            if not api_key:
                raise ProviderError("LLM_API_KEY is not set (see .env.example)")
            client = OpenAI(api_key=api_key, base_url=base_url)
        self._client = client
        self.use_json_schema = use_json_schema
        self._schema_rejected = False  # set once the server has refused response_format=json_schema
        self._schema_text = json.dumps(json_schema(), ensure_ascii=False, separators=(",", ":"))

    def _messages(self, doc: PreparedDoc, system: str, instruction: str) -> list[dict]:
        content: list[dict] = []
        for b64 in doc.images_b64():
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        content.append({"type": "text", "text": instruction})
        return [{"role": "system", "content": system}, {"role": "user", "content": content}]

    def _create(self, messages: list[dict], model: str, max_tokens: int, response_format: dict):
        return self._client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=0, response_format=response_format,
        )

    def _json_mode(self, doc: PreparedDoc, system: str, instruction: str, model: str, max_tokens: int) -> tuple[str, object, bool]:
        """JSON mode: the server guarantees JSON, the prompt carries the schema, Pydantic checks it."""
        messages = self._messages(doc, system, instruction + SCHEMA_IN_PROMPT.format(schema=self._schema_text))
        resp = self._create(messages, model, max_tokens, JSON_OBJECT_FORMAT)
        return resp.choices[0].message.content or "", getattr(resp, "usage", None), False

    def _json_schema(self, doc: PreparedDoc, system: str, instruction: str, model: str, max_tokens: int) -> tuple[str, object, bool] | None:
        """Server-enforced schema. Returns None when this call has to go through JSON mode instead."""
        try:
            resp = self._create(self._messages(doc, system, instruction), model, max_tokens, JSON_SCHEMA_FORMAT)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status not in (400, 422):
                raise
            code = _error_code(exc)
            if code == "json_validate_failed":
                # Groq: the schema was accepted but the model's output did not satisfy it. A bad
                # generation, not a missing feature: redo this call in JSON mode and keep the
                # schema path for the next document.
                log.warning("model output failed the server-side schema check (HTTP %s, %s); redoing this call in JSON mode", status, code)
            else:
                log.warning("server does not accept response_format=json_schema (HTTP %s, %s); using JSON mode from now on",
                            status, code or "no error code")
                self._schema_rejected = True
            return None
        return resp.choices[0].message.content or "", getattr(resp, "usage", None), True

    def extract(self, doc: PreparedDoc, system: str, instruction: str, model: str, max_tokens: int = 2000) -> ExtractResult:
        t0 = time.perf_counter()
        try:
            result = None
            if self.use_json_schema and not self._schema_rejected:
                result = self._json_schema(doc, system, instruction, model, max_tokens)
            if result is None:
                result = self._json_mode(doc, system, instruction, model, max_tokens)
        except ProviderError:
            raise
        except Exception as exc:
            raise _wrap(exc) from exc
        text, usage, schema_enforced = result
        elapsed = time.perf_counter() - t0
        if usage is not None and getattr(usage, "prompt_tokens", None) is not None:
            return ExtractResult(text, usage.prompt_tokens, usage.completion_tokens or 0, model, elapsed, False, schema_enforced)
        return ExtractResult(text, _estimate_tokens(doc, instruction + self._schema_text), max(len(text) // 4, 1), model, elapsed, True, schema_enforced)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, client=None) -> None:
        if client is None:
            import anthropic

            if not api_key:
                raise ProviderError("ANTHROPIC_API_KEY is not set (see .env.example)")
            client = anthropic.Anthropic(api_key=api_key)
        self._client = client

    def _content(self, doc: PreparedDoc, instruction: str) -> list[dict]:
        """The document before the instruction: the whole PDF as one document block, or page images.

        A PDF with more pages than ``max_pages`` (``doc.truncated``) goes as the rendered page images instead,
        so the page limit holds for Claude too and the "only the first N of M pages were read" reason is true.
        """
        content: list[dict] = []
        if doc.kind == "pdf" and doc.pdf_bytes is not None and not doc.truncated:
            import base64

            content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(doc.pdf_bytes).decode()}})
        else:
            for b64 in doc.images_b64():
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
        content.append({"type": "text", "text": instruction})
        return content

    def extract(self, doc: PreparedDoc, system: str, instruction: str, model: str, max_tokens: int = 2000) -> ExtractResult:
        t0 = time.perf_counter()
        try:
            resp = self._client.messages.parse(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": self._content(doc, instruction)}],
                output_format=InvoiceExtraction,
            )
        except Exception as exc:
            raise _wrap(exc) from exc
        elapsed = time.perf_counter() - t0
        parsed = getattr(resp, "parsed_output", None)
        raw = parsed.model_dump_json() if parsed is not None else next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        u = resp.usage
        return ExtractResult(raw, u.input_tokens, u.output_tokens, model, elapsed, False, True)


def _estimate_tokens(doc: PreparedDoc, instruction: str) -> int:
    # ~1,200 tokens per image at the sizes we send, plus text
    return 1200 * len(doc.images) + len(instruction) // 4 + 300


def get_provider(cfg) -> tuple[Provider, str]:
    """Return (provider, model) from settings."""
    if cfg.llm_provider == "anthropic":
        return AnthropicProvider(cfg.anthropic_api_key), cfg.anthropic_model
    if cfg.llm_provider == "openai_compat":
        if not cfg.llm_model:
            raise ProviderError("LLM_MODEL is not set (see .env.example)")
        return OpenAICompatProvider(cfg.llm_api_key, cfg.llm_base_url, use_json_schema=cfg.use_json_schema), cfg.llm_model
    raise ProviderError(f"Unknown LLM_PROVIDER {cfg.llm_provider!r}")


def parse_json_loose(text: str) -> dict:
    """Parse JSON, tolerating a ```json fence or prose around the object."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise
