import base64
import json

import httpx
import pytest

from app.services.ai.providers import ExtractionRequest, GeminiAdapter, ProviderError
from app.services.ai.schema import PROVIDER_RESPONSE_SCHEMA


def request(**kw) -> ExtractionRequest:
    fields = dict(
        model_name="gemini-test",
        api_key="AIza-test-key-123",
        system_prompt="sys",
        user_prompt="user",
        document=b"%PDF-1.4 doc",
        mime_type="application/pdf",
        response_schema=PROVIDER_RESPONSE_SCHEMA,
    )
    return ExtractionRequest(**{**fields, **kw})


def adapter(handler) -> GeminiAdapter:
    return GeminiAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))


def ok_body(text: str, finish: str = "STOP") -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 1500, "candidatesTokenCount": 420},
    }


def test_success_and_request_shape():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["key"] = req.headers["x-goog-api-key"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=ok_body('{"invoice_number": "A-1"}'))

    resp = adapter(handler).extract(request())
    assert resp.data == {"invoice_number": "A-1"}
    assert (resp.input_tokens, resp.output_tokens) == (1500, 420)
    assert seen["url"].endswith("/models/gemini-test:generateContent")
    assert seen["key"] == "AIza-test-key-123"
    assert "key=" not in seen["url"]  # key never in the URL (keeps it out of logs)
    parts = seen["body"]["contents"][0]["parts"]
    assert base64.b64decode(parts[0]["inline_data"]["data"]) == b"%PDF-1.4 doc"
    config = seen["body"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["type"] == "OBJECT"


@pytest.mark.parametrize(
    "status,message,expected",
    [
        (429, "Resource exhausted", "rate_limited"),
        (401, "unauthenticated", "auth_failed"),
        (403, "permission denied", "auth_failed"),
        (400, "API key not valid", "auth_failed"),
        (400, "bad schema", "invalid_request"),
        (404, "model not found", "invalid_request"),
        (500, "internal", "provider_unavailable"),
        (503, "overloaded", "provider_unavailable"),
        (504, "deadline", "timeout"),
    ],
)
def test_http_errors_are_classified(status, message, expected):
    a = adapter(lambda req: httpx.Response(status, json={"error": {"message": message}}))
    with pytest.raises(ProviderError) as exc:
        a.extract(request())
    assert exc.value.error_class == expected


def test_timeout_and_network_errors():
    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(ProviderError) as exc:
        adapter(slow).extract(request())
    assert exc.value.error_class == "timeout"

    def down(req):
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(ProviderError) as exc:
        adapter(down).extract(request())
    assert exc.value.error_class == "provider_unavailable"


@pytest.mark.parametrize(
    "body,expected",
    [
        ({"promptFeedback": {"blockReason": "SAFETY"}}, "content_blocked"),
        (ok_body("{}", finish="SAFETY"), "content_blocked"),
        (ok_body('{"a": 1', finish="MAX_TOKENS"), "malformed_response"),
        (ok_body("not json at all"), "malformed_response"),
        ({"candidates": []}, "malformed_response"),
    ],
)
def test_bad_responses(body, expected):
    with pytest.raises(ProviderError) as exc:
        adapter(lambda req: httpx.Response(200, json=body)).extract(request())
    assert exc.value.error_class == expected


def test_code_fenced_json_is_accepted():
    a = adapter(lambda req: httpx.Response(200, json=ok_body('```json\n{"x": 1}\n```')))
    assert a.extract(request()).data == {"x": 1}


def test_missing_key_and_oversized_document():
    with pytest.raises(ProviderError) as exc:
        GeminiAdapter().extract(request(api_key=None))
    assert exc.value.error_class == "credential_missing"
    big = ExtractionRequest(
        model_name="m",
        api_key="k" * 10,
        system_prompt="s",
        user_prompt="u",
        document=b"x" * (19 * 1024 * 1024),
        mime_type="application/pdf",
        response_schema={},
    )
    with pytest.raises(ProviderError) as exc:
        GeminiAdapter().extract(big)
    assert exc.value.error_class == "invalid_request"
