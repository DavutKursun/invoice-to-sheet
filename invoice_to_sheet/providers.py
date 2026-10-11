"""LLM providers for structured extraction from document images.

* ``OpenAICompatProvider`` – any OpenAI-compatible chat API with vision (Groq's Llama 4
  models, OpenRouter, …). Sends pages as image_url data URIs. Tries
  ``response_format={"type": "json_schema"}`` first; if the server rejects it, falls
  back to JSON mode (``{"type": "json_object"}``). The caller validates with Pydantic.
* ``AnthropicProvider`` – Claude with native PDF input (document block) or images, and
  ``client.messages.parse`` structured outputs.

Both return ``ExtractResult(raw_json, input_tokens, output_tokens, model, elapsed,
usage_estimated)``. Network errors become ``ProviderError``.
"""

from __future__ import annotations

import json
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


def _wrap(exc: Exception) -> ProviderError:
    status = getattr(exc, "status_code", None)
    code = ""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            code = err.get("code") or err.get("type") or ""
    msg = str(exc)
    prefix = f"HTTP {status}" if status else type(exc).__name__
    return ProviderError(f"{prefix}{f' ({code})' if code else ''}: {msg}")


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, api_key: str, base_url: str, client=None) -> None:
        if client is None:
            from openai import OpenAI

            if not api_key:
                raise ProviderError("LLM_API_KEY is not set (see .env.example)")
            client = OpenAI(api_key=api_key, base_url=base_url)
        self._client = client

    def _messages(self, doc: PreparedDoc, system: str, instruction: str) -> list[dict]:
        content: list[dict] = []
        for b64 in doc.images_b64():
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        content.append({"type": "text", "text": instruction})
        return [{"role": "system", "content": system}, {"role": "user", "content": content}]

    def extract(self, doc: PreparedDoc, system: str, instruction: str, model: str, max_tokens: int = 2000) -> ExtractResult:
        messages = self._messages(doc, system, instruction)
        t0 = time.perf_counter()
        schema_enforced = True
        try:
            try:
                resp = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=0,
                    response_format={"type": "json_schema", "json_schema": {"name": "invoice", "schema": json_schema(), "strict": True}},
                )
            except Exception as exc:  # server does not support json_schema → JSON mode
                if getattr(exc, "status_code", None) not in (400, 422):
                    raise
                schema_enforced = False
                resp = self._client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens, temperature=0,
                    response_format={"type": "json_object"},
                )
        except ProviderError:
            raise
        except Exception as exc:
            raise _wrap(exc) from exc
        elapsed = time.perf_counter() - t0
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        if usage is not None and getattr(usage, "prompt_tokens", None) is not None:
            return ExtractResult(text, usage.prompt_tokens, usage.completion_tokens or 0, model, elapsed, False, schema_enforced)
        return ExtractResult(text, _estimate_tokens(doc, instruction), max(len(text) // 4, 1), model, elapsed, True, schema_enforced)


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
        content: list[dict] = []
        if doc.kind == "pdf" and doc.pdf_bytes is not None:
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
        return OpenAICompatProvider(cfg.llm_api_key, cfg.llm_base_url), cfg.llm_model
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
