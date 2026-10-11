from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from invoice_to_sheet.extract import extract
from invoice_to_sheet.prepare import prepare
from invoice_to_sheet.providers import OpenAICompatProvider, ProviderError


class FakeHTTPError(Exception):
    """Shaped like openai.APIStatusError: .status_code and .body (the server's error object)."""

    def __init__(self, status_code: int, body: dict):
        super().__init__(f"Error code: {status_code} - {body}")
        self.status_code = status_code
        self.body = body


class FakeClient:
    """Records every chat.completions.create call; handler(kwargs) returns a response or raises."""

    def __init__(self, handler):
        self.calls: list[dict] = []

        def create(**kwargs):
            self.calls.append(kwargs)
            return handler(kwargs)

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _response(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=300))


def _user_text(call: dict) -> str:
    return next(part["text"] for part in call["messages"][1]["content"] if part["type"] == "text")


def _formats(client: FakeClient) -> list[str]:
    return [c["response_format"]["type"] for c in client.calls]


GOOD = json.dumps({
    "document_type": "invoice", "vendor_name": "Kestrel Software OÜ", "vendor_tax_id": "EE102345678", "invoice_number": "F-34175",
    "invoice_date": "2026-06-05", "due_date": "2026-07-05", "currency": "EUR", "subtotal": 100.0, "discount": 0.0, "tax_amount": 19.0,
    "total": 119.0, "line_items": [{"description": "Dock", "quantity": 4, "unit_price": 25.0, "amount": 100.0}],
    "payment_terms": "Net 30", "confidence": 0.95, "low_confidence_fields": [], "notes": "",
}, ensure_ascii=False)


def test_default_is_json_mode_with_schema_in_prompt(sample_png):
    client = FakeClient(lambda kw: _response(GOOD))
    res = OpenAICompatProvider("k", "http://x", client=client).extract(prepare(sample_png), "sys", "Extract.", "m")
    assert res.raw_json == GOOD and res.schema_enforced is False and res.usage_estimated is False
    assert (res.input_tokens, res.output_tokens) == (1200, 300)
    call = client.calls[0]
    assert call["response_format"] == {"type": "json_object"} and call["temperature"] == 0 and call["model"] == "m"
    assert call["messages"][0] == {"role": "system", "content": "sys"}
    assert call["messages"][1]["content"][0]["type"] == "image_url"  # the document comes before the instruction
    text = _user_text(call)
    assert text.startswith("Extract.") and "JSON Schema" in text and '"vendor_name"' in text and '"low_confidence_fields"' in text


def test_json_schema_when_enabled(sample_png):
    client = FakeClient(lambda kw: _response(GOOD))
    provider = OpenAICompatProvider("k", "http://x", client=client, use_json_schema=True)
    res = provider.extract(prepare(sample_png), "sys", "Extract.", "m")
    assert res.schema_enforced is True
    fmt = client.calls[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True and "vendor_name" in fmt["json_schema"]["schema"]["properties"]
    assert '"vendor_name"' not in _user_text(client.calls[0])  # the server has the schema; the prompt does not repeat it


def test_json_validate_failed_redoes_the_call_in_json_mode_and_keeps_the_schema_path(sample_png, caplog):
    def handler(kw):
        if kw["response_format"]["type"] == "json_schema":
            raise FakeHTTPError(400, {"message": "Failed to generate JSON", "type": "invalid_request_error",
                                      "code": "json_validate_failed", "failed_generation": '{"vendor_name": "x"'})
        return _response(GOOD)

    client = FakeClient(handler)
    provider = OpenAICompatProvider("k", "http://x", client=client, use_json_schema=True)
    with caplog.at_level(logging.WARNING, logger="invoice_to_sheet.providers"):
        res = provider.extract(prepare(sample_png), "sys", "Extract.", "m")
    assert res.raw_json == GOOD and res.schema_enforced is False
    assert _formats(client) == ["json_schema", "json_object"]
    assert "json_validate_failed" in caplog.text and "HTTP 400" in caplog.text
    provider.extract(prepare(sample_png), "sys", "Extract.", "m")  # a bad generation, not a missing feature: try the schema again
    assert _formats(client) == ["json_schema", "json_object", "json_schema", "json_object"]


def test_unsupported_json_schema_falls_back_to_json_mode_and_remembers(sample_png, caplog):
    def handler(kw):
        if kw["response_format"]["type"] == "json_schema":
            raise FakeHTTPError(400, {"error": {"message": "response_format json_schema is not supported", "type": "invalid_request_error"}})
        return _response(GOOD)

    client = FakeClient(handler)
    provider = OpenAICompatProvider("k", "http://x", client=client, use_json_schema=True)
    with caplog.at_level(logging.WARNING, logger="invoice_to_sheet.providers"):
        res = provider.extract(prepare(sample_png), "sys", "Extract.", "m")
    assert res.raw_json == GOOD and res.schema_enforced is False
    assert _formats(client) == ["json_schema", "json_object"]
    assert '"vendor_name"' in _user_text(client.calls[1])  # JSON mode carries the schema in the prompt
    assert "does not accept" in caplog.text and "invalid_request_error" in caplog.text
    provider.extract(prepare(sample_png), "sys", "Extract.", "m")
    assert _formats(client) == ["json_schema", "json_object", "json_object"]  # remembered: straight to JSON mode


def test_other_http_errors_become_provider_error(sample_png):
    def handler(kw):
        raise FakeHTTPError(429, {"message": "Rate limit reached", "type": "tokens", "code": "rate_limit_exceeded"})

    with pytest.raises(ProviderError, match=r"HTTP 429 \(rate_limit_exceeded\)"):
        OpenAICompatProvider("k", "http://x", client=FakeClient(handler)).extract(prepare(sample_png), "sys", "Extract.", "m")


def test_extract_end_to_end_in_json_mode(sample_png):
    client = FakeClient(lambda kw: _response(GOOD))
    ex = extract(prepare(sample_png), OpenAICompatProvider("k", "http://x", client=client), "m")
    assert not ex.repaired and ex.data.vendor_name == "Kestrel Software OÜ" and ex.result.schema_enforced is False
