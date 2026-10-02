"""Provider adapters behind one interface (spec 9). Adapters translate a provider's API and
errors into ``ProviderResponse`` / ``ProviderError``; they never see the database."""

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass
class ExtractionRequest:
    model_name: str
    api_key: str | None
    system_prompt: str
    user_prompt: str
    document: bytes
    mime_type: str
    response_schema: dict
    timeout_seconds: float = 60.0
    temperature: float = 0.0
    max_output_tokens: int = 8192
    base_url: str | None = None


@dataclass
class ProviderResponse:
    data: Any  # parsed JSON
    input_tokens: int | None = None
    output_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """``error_class`` is one of ``AI_ERROR_CLASSES``; ``detail`` is internal only."""

    def __init__(
        self,
        error_class: str,
        detail: str = "",
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        super().__init__(f"{error_class}: {detail}")
        self.error_class = error_class
        self.detail = detail
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class ProviderAdapter(Protocol):
    def extract(self, request: ExtractionRequest) -> ProviderResponse: ...


def _parse_json(text: str, **tokens) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned[4:] if cleaned.lower().startswith("json") else cleaned
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ProviderError("malformed_response", f"invalid JSON: {exc.msg}", **tokens) from exc


class GeminiAdapter:
    """Google Gemini ``generateContent`` with structured JSON output."""

    DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"
    MAX_INLINE_BYTES = 18 * 1024 * 1024

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def extract(self, request: ExtractionRequest) -> ProviderResponse:
        if not request.api_key:
            raise ProviderError("credential_missing", "no API key configured")
        if len(request.document) > self.MAX_INLINE_BYTES:
            raise ProviderError("invalid_request", "document exceeds inline size limit")
        base = (request.base_url or self.DEFAULT_BASE).rstrip("/")
        body = {
            "systemInstruction": {"parts": [{"text": request.system_prompt}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": request.mime_type,
                                "data": base64.b64encode(request.document).decode(),
                            }
                        },
                        {"text": request.user_prompt},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
                "responseMimeType": "application/json",
                "responseSchema": request.response_schema,
            },
        }
        client = self._client or httpx.Client()
        try:
            resp = client.post(
                f"{base}/models/{request.model_name}:generateContent",
                json=body,
                headers={"x-goog-api-key": request.api_key},
                timeout=request.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("provider_unavailable", type(exc).__name__) from exc
        finally:
            if self._client is None:
                client.close()
        return self._handle(resp)

    @staticmethod
    def _handle(resp: httpx.Response) -> ProviderResponse:
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        if resp.status_code != 200:
            message = str((payload.get("error") or {}).get("message", ""))[:300]
            status = resp.status_code
            if status in (401, 403) or (status == 400 and "API key" in message):
                raise ProviderError("auth_failed", f"HTTP {status}: {message}")
            if status == 429:
                raise ProviderError("rate_limited", f"HTTP 429: {message}")
            if status in (408, 504):
                raise ProviderError("timeout", f"HTTP {status}: {message}")
            if status >= 500:
                raise ProviderError("provider_unavailable", f"HTTP {status}: {message}")
            raise ProviderError("invalid_request", f"HTTP {status}: {message}")

        usage = payload.get("usageMetadata") or {}
        tokens = {
            "input_tokens": usage.get("promptTokenCount"),
            "output_tokens": usage.get("candidatesTokenCount"),
        }
        if block := (payload.get("promptFeedback") or {}).get("blockReason"):
            raise ProviderError("content_blocked", f"prompt blocked: {block}", **tokens)
        candidates = payload.get("candidates") or []
        if not candidates:
            raise ProviderError("malformed_response", "no candidates", **tokens)
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        if finish in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
            raise ProviderError("content_blocked", f"finish reason {finish}", **tokens)
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text:
            raise ProviderError("malformed_response", f"empty response ({finish})", **tokens)
        if finish == "MAX_TOKENS":
            raise ProviderError("malformed_response", "output truncated (MAX_TOKENS)", **tokens)
        return ProviderResponse(
            data=_parse_json(text, **tokens), metadata={"finish": finish}, **tokens
        )


FAKE_INVOICE = {
    "invoice_number": "INV-1001",
    "invoice_date": "2026-09-15",
    "due_date": "2026-10-15",
    "currency": "INR",
    "supplier": {
        "name": "Acme Supplies Pvt Ltd",
        "address": "12 MG Road, Pune",
        "tax_id": "27AAPFU0939F1ZV",
    },
    "customer": {
        "name": "Globex Retail",
        "address": "5 Park Street, Mumbai",
        "tax_id": "27AAACG1234A1ZE",
    },
    "line_items": [
        {
            "description": "A4 paper ream",
            "hsn_sac": "4802",
            "quantity": "10",
            "unit": "pcs",
            "unit_price": "250.00",
            "tax_rate": "18",
            "tax_amount": "450.00",
            "line_total": "2500.00",
        },
    ],
    "subtotal": "2500.00",
    "taxes": [
        {"type": "CGST", "rate": "9", "amount": "225.00"},
        {"type": "SGST", "rate": "9", "amount": "225.00"},
    ],
    "cgst": "225.00",
    "sgst": "225.00",
    "total_tax": "450.00",
    "grand_total": "2950.00",
    "field_confidence": [
        {"field": "grand_total", "confidence": 0.97},
        {"field": "invoice_number", "confidence": 0.99},
    ],
}


class FakeAdapter:
    """Deterministic adapter for tests, local development and admin dry runs.

    Behaviour is selected by model name: ``fake-ok`` (default), ``fake-rate-limited``,
    ``fake-timeout``, ``fake-unavailable``, ``fake-malformed``, ``fake-auth``, ``fake-invalid``.
    """

    def extract(self, request: ExtractionRequest) -> ProviderResponse:
        tokens = {"input_tokens": 1200, "output_tokens": 350}
        behaviour = request.model_name.removeprefix("fake-")
        errors = {
            "rate-limited": "rate_limited",
            "timeout": "timeout",
            "unavailable": "provider_unavailable",
            "auth": "auth_failed",
            "invalid": "invalid_request",
        }
        if behaviour in errors:
            raise ProviderError(errors[behaviour], f"fake {behaviour}")
        if behaviour == "malformed":
            return ProviderResponse(data=_parse_json("{not json", **tokens), **tokens)
        return ProviderResponse(data=json.loads(json.dumps(FAKE_INVOICE)), **tokens)


_ADAPTERS: dict[str, ProviderAdapter] = {"gemini": GeminiAdapter(), "fake": FakeAdapter()}
ADAPTER_NAMES = tuple(_ADAPTERS)


def get_adapter(name: str) -> ProviderAdapter:
    if name not in _ADAPTERS:
        raise ProviderError("invalid_request", f"unknown adapter {name!r}")
    return _ADAPTERS[name]


def register_adapter(name: str, adapter: ProviderAdapter) -> None:
    _ADAPTERS[name] = adapter
