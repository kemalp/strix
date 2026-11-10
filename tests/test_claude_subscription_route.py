"""The claude/ route: provider wiring and the identity-block system prompt split."""

# Fixtures are requested for their side effects (config override, signed-in token).
# ruff: noqa: ARG001

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from agents.extensions.models.litellm_model import LitellmModel
from agents.model_settings import ModelSettings

from strix.config import claude_subscription, loader
from strix.config.models import StrixProvider, _split_cached_prefix


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def signed_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A per-test config source also drops the memoized settings, so the env the
    # test sets below is what the route reads.
    loader.apply_config_override(tmp_path / "cli-config.json")
    monkeypatch.setattr(claude_subscription, "get_valid_token", lambda: "sk-ant-oat01-token")


def _inner(model: object) -> object:
    while hasattr(model, "_inner"):
        model = model._inner  # type: ignore[attr-defined]
    return model


def test_provider_resolves_the_prefix_to_an_anthropic_model(signed_in: None) -> None:
    model = StrixProvider().get_model("claude/claude-mythos-5-2")
    inner = _inner(model)
    assert isinstance(inner, LitellmModel)
    assert inner.model == "anthropic/claude-mythos-5-2"
    assert inner.api_key == "sk-ant-oat01-token"


def test_claude_code_prefix_works_too(signed_in: None) -> None:
    inner = _inner(StrixProvider().get_model("claude-code/claude-opus-5-5"))
    assert isinstance(inner, LitellmModel)
    assert inner.model == "anthropic/claude-opus-5-5"


def test_the_api_key_route_is_not_treated_as_a_subscription(signed_in: None) -> None:
    assert claude_subscription.subscription_model("anthropic/claude-sonnet-5-5") is None


def test_split_puts_the_identity_block_first(
    signed_in: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STRIX_LLM", "claude/claude-sonnet-5-5")
    system, model_input = _split_cached_prefix("Strix prompt", "hello", ModelSettings())
    assert system is None
    assert model_input[0]["role"] == "system"
    assert model_input[0]["content"][0]["text"] == claude_subscription.IDENTITY_PROMPT
    assert model_input[1] == {"role": "user", "content": "hello"}


def test_split_keeps_the_strix_prompt_as_trailing_blocks(
    signed_in: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STRIX_LLM", "claude/claude-sonnet-5-5")
    _system, model_input = _split_cached_prefix("first<cache_point>second", "hi", ModelSettings())
    texts = [block["text"] for block in model_input[0]["content"]]
    assert texts == [claude_subscription.IDENTITY_PROMPT, "first", "second"]


def test_split_leaves_other_routes_on_the_plain_system_string(
    signed_in: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STRIX_LLM", "anthropic/claude-sonnet-5-5")
    system, model_input = _split_cached_prefix("Strix prompt", "hello", ModelSettings())
    assert system == "Strix prompt"
    assert model_input == "hello"
