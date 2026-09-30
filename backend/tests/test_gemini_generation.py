"""Tests for the Gemini-backed LLM generation in response_builder.

The Gemini API is mocked with ``httpx.MockTransport``; no network access
happens. These tests pin the request shape (endpoint, auth header, grounding
prompt) and the response parsing, so a provider change cannot silently break
the pipeline — it must fall back to the extractive answer instead.
"""

import json

import httpx
import pytest

from app.core.config import settings
from app.rag.response_builder import generate_grounded


# ------------------------------------------------------------- fakes

GEMINI_OK_PAYLOAD = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [
                    {"text": "Marking is required per "},
                    {"text": "IS 9999:2024 clause 7."},
                ],
            },
            "finishReason": "STOP",
        }
    ]
}


def make_handler(payload: dict | None = None, *, status: int = 200, capture: dict | None = None):
    """Build a MockTransport handler returning a canned Gemini response."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["url"] = str(request.url)
            capture["api_key_header"] = request.headers.get("x-goog-api-key")
            capture["body"] = json.loads(request.content)
        return httpx.Response(status, json=payload or {})

    return handler


def install_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Route every httpx.AsyncClient created by generate_grounded to the mock."""
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


@pytest.fixture
def gemini_settings(monkeypatch: pytest.MonkeyPatch):
    """Enable the LLM path with fake credentials against a fake base URL."""
    monkeypatch.setattr(settings, "llm_api_key", "test-gemini-key")
    monkeypatch.setattr(settings, "llm_api_url", "https://gemini.example.com/v1beta")
    monkeypatch.setattr(settings, "llm_model", "gemini-test-model")


SECTIONS = [
    {
        "standard_number": "IS 9999:2024",
        "clauses": [{"clause": "7", "excerpt": "Every rope shall be marked.", "score": 0.9}],
        "total_excerpts": 1,
    }
]


# ------------------------------------------------------------- happy path


@pytest.mark.asyncio
async def test_parses_gemini_response_and_joins_parts(monkeypatch, gemini_settings):
    install_transport(monkeypatch, make_handler(GEMINI_OK_PAYLOAD))

    answer = await generate_grounded("marking requirements", SECTIONS)

    assert answer == "Marking is required per IS 9999:2024 clause 7."


@pytest.mark.asyncio
async def test_request_shape_endpoint_auth_and_prompt(monkeypatch, gemini_settings):
    capture: dict = {}
    install_transport(monkeypatch, make_handler(GEMINI_OK_PAYLOAD, capture=capture))

    await generate_grounded("what is the marking rule?", SECTIONS)

    # Endpoint: {base}/models/{model}:generateContent
    assert capture["url"] == (
        "https://gemini.example.com/v1beta/models/gemini-test-model:generateContent"
    )
    # Auth via x-goog-api-key, not a Bearer token.
    assert capture["api_key_header"] == "test-gemini-key"

    body = capture["body"]
    # System instruction carries the strict grounding prompt.
    assert "Answer ONLY from the evidence provided" in (
        body["systemInstruction"]["parts"][0]["text"]
    )
    # User content contains the evidence and the query, no chat history.
    assert len(body["contents"]) == 1
    assert body["contents"][0]["role"] == "user"
    assert "IS 9999:2024" in body["contents"][0]["parts"][0]["text"]
    assert "what is the marking rule?" in body["contents"][0]["parts"][0]["text"]
    # Deterministic, bounded generation.
    assert body["generationConfig"]["temperature"] == 0.0
    assert body["generationConfig"]["maxOutputTokens"] == 2048


# ------------------------------------------------------------- fallbacks


@pytest.mark.asyncio
async def test_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=GEMINI_OK_PAYLOAD)

    install_transport(monkeypatch, handler)

    assert await generate_grounded("q", SECTIONS) is None
    assert not called  # extractive mode: no HTTP request at all


@pytest.mark.asyncio
async def test_http_error_falls_back_to_none(monkeypatch, gemini_settings):
    install_transport(monkeypatch, make_handler({"error": {"code": 500}}, status=500))

    assert await generate_grounded("q", SECTIONS) is None


@pytest.mark.asyncio
async def test_empty_candidates_falls_back_to_none(monkeypatch, gemini_settings):
    install_transport(monkeypatch, make_handler({"candidates": []}))

    assert await generate_grounded("q", SECTIONS) is None


@pytest.mark.asyncio
async def test_missing_parts_fall_back_to_none(monkeypatch, gemini_settings):
    payload = {"candidates": [{"content": {"role": "model", "parts": []}}]}
    install_transport(monkeypatch, make_handler(payload))

    assert await generate_grounded("q", SECTIONS) is None


@pytest.mark.asyncio
async def test_whitespace_only_reply_becomes_none(monkeypatch, gemini_settings):
    payload = {"candidates": [{"content": {"parts": [{"text": "   \n  "}]}}]}
    install_transport(monkeypatch, make_handler(payload))

    assert await generate_grounded("q", SECTIONS) is None
