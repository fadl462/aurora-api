"""
Tests app/orchestration.py directly, without needing a real Anthropic
API key or network access. The "real call" tests mock the Anthropic
client so we're verifying our own integration code (correct model
passed, correct system prompt per mode, graceful error handling) —
not testing Anthropic's API itself.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic

from app.orchestration import DEFAULT_MODEL, generate_reply


def test_no_api_key_returns_labeled_stub(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = generate_reply("hello")
    assert result["model_used"] == "aurora-stub"
    assert "placeholder response" in result["content"]
    assert result["citations"] is None
    assert result["confidence"] is None
    assert result["tokens_are_estimated"] is True
    assert result["tokens_used"] > 0


def test_stub_token_estimate_scales_with_message_length(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    short = generate_reply("hi")
    long = generate_reply("a" * 500)
    assert long["tokens_used"] > short["tokens_used"]


def test_real_call_never_fabricates_citations_or_confidence(monkeypatch):
    """Even on a successful real model call, citations/confidence must
    stay None — there's no search tool wired up to justify either."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    fake_text_block = SimpleNamespace(type="text", text="A real-looking response.")
    fake_usage = SimpleNamespace(input_tokens=12, output_tokens=8)
    fake_response = SimpleNamespace(content=[fake_text_block], usage=fake_usage)

    with patch("app.orchestration.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = fake_response
        result = generate_reply("What's the capital of France?")

    assert result["content"] == "A real-looking response."
    assert result["citations"] is None
    assert result["confidence"] is None
    assert result["tokens_used"] == 20  # real number from the mocked usage object, not an estimate
    assert result["tokens_are_estimated"] is False


def test_real_call_uses_configured_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-custom-test-model")

    fake_response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
    )

    with patch("app.orchestration.anthropic.Anthropic") as MockClient:
        mock_create = MockClient.return_value.messages.create
        mock_create.return_value = fake_response
        result = generate_reply("hello")

    assert result["model_used"] == "claude-custom-test-model"
    _, kwargs = mock_create.call_args
    assert kwargs["model"] == "claude-custom-test-model"


def test_research_mode_uses_research_system_prompt(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    fake_response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
    )

    with patch("app.orchestration.anthropic.Anthropic") as MockClient:
        mock_create = MockClient.return_value.messages.create
        mock_create.return_value = fake_response
        generate_reply("compare these things", mode="research")

    _, kwargs = mock_create.call_args
    assert "live web search" in kwargs["system"]


def test_general_mode_uses_general_system_prompt(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    fake_response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
    )

    with patch("app.orchestration.anthropic.Anthropic") as MockClient:
        mock_create = MockClient.return_value.messages.create
        mock_create.return_value = fake_response
        generate_reply("hello", mode=None)

    _, kwargs = mock_create.call_args
    assert "Aurora" in kwargs["system"]


def test_explicit_ultra_choice_maps_to_opus(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    fake_response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
    )

    with patch("app.orchestration.anthropic.Anthropic") as MockClient:
        mock_create = MockClient.return_value.messages.create
        mock_create.return_value = fake_response
        result = generate_reply("hello", model_choice="ultra")

    assert result["model_used"] == "claude-opus-4-8"
    _, kwargs = mock_create.call_args
    assert kwargs["model"] == "claude-opus-4-8"


def test_explicit_fast_choice_maps_to_haiku(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    fake_response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
    )

    with patch("app.orchestration.anthropic.Anthropic") as MockClient:
        mock_create = MockClient.return_value.messages.create
        mock_create.return_value = fake_response
        result = generate_reply("hello", model_choice="fast")

    assert result["model_used"] == "claude-haiku-4-5-20251001"


def test_auto_mode_routes_short_message_to_fast_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    fake_response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
    )

    with patch("app.orchestration.anthropic.Anthropic") as MockClient:
        mock_create = MockClient.return_value.messages.create
        mock_create.return_value = fake_response
        result = generate_reply("hi there", model_choice="auto")

    assert result["model_used"] == "claude-haiku-4-5-20251001"


def test_auto_mode_routes_long_message_to_ultra_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    fake_response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
    )

    with patch("app.orchestration.anthropic.Anthropic") as MockClient:
        mock_create = MockClient.return_value.messages.create
        mock_create.return_value = fake_response
        result = generate_reply("a" * 2500, model_choice="auto")

    assert result["model_used"] == "claude-opus-4-8"


def test_auto_mode_routes_medium_message_to_balanced_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    fake_response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
    )

    with patch("app.orchestration.anthropic.Anthropic") as MockClient:
        mock_create = MockClient.return_value.messages.create
        mock_create.return_value = fake_response
        result = generate_reply("a" * 500, model_choice="auto")

    assert result["model_used"] == "claude-sonnet-5"


def test_unrecognized_model_choice_falls_back_to_configured_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    fake_response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
    )

    with patch("app.orchestration.anthropic.Anthropic") as MockClient:
        mock_create = MockClient.return_value.messages.create
        mock_create.return_value = fake_response
        result = generate_reply("hello", model_choice="some-old-frontend-value")

    assert result["model_used"] == DEFAULT_MODEL


def test_api_status_error_falls_back_to_stub_instead_of_crashing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    with patch("app.orchestration.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.side_effect = anthropic.APIStatusError(
            message="not found",
            response=MagicMock(status_code=404),
            body={"error": {"message": "model not found"}},
        )
        result = generate_reply("hello")

    assert result["model_used"] == "aurora-stub"
    assert "model call failed" in result["content"]


def test_connection_error_falls_back_to_stub(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    with patch("app.orchestration.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.side_effect = anthropic.APIConnectionError(request=MagicMock())
        result = generate_reply("hello")

    assert result["model_used"] == "aurora-stub"
    assert "could not reach" in result["content"]


def test_unexpected_error_falls_back_to_stub_rather_than_500ing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    with patch("app.orchestration.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.side_effect = ValueError("something unrelated broke")
        result = generate_reply("hello")

    assert result["model_used"] == "aurora-stub"
    assert "unexpected error" in result["content"]
