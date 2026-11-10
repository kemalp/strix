"""Claude subscription route: prefix parsing, auth store, and prompt identity block."""

# Fixtures are requested for their side effects (patching the auth path / token).
# ruff: noqa: ARG001

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from strix.config import claude_subscription as cs


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def auth_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "claude-subscription-auth.json"
    monkeypatch.setattr(cs, "AUTH_PATH", path)
    return path


def test_prefixes_map_to_the_subscription_route() -> None:
    assert cs.subscription_model("claude/claude-mythos-5-2") == "claude-mythos-5-2"
    assert cs.subscription_model("claude-code/claude-opus-5-5") == "claude-opus-5-5"
    assert cs.subscription_model("CLAUDE/claude-sonnet-5-5") == "claude-sonnet-5-5"


def test_api_key_routes_are_untouched() -> None:
    assert cs.subscription_model("anthropic/claude-sonnet-5-5") is None
    assert cs.subscription_model("openrouter/z-ai/glm-5.3") is None
    assert cs.subscription_model("") is None
    assert cs.subscription_model(None) is None


def test_bare_prefix_without_a_model_is_not_a_route() -> None:
    assert cs.subscription_model("claude/") is None


def test_auth_mode() -> None:
    assert cs.auth_mode("claude/claude-sonnet-5-5") == "subscription"
    assert cs.auth_mode("anthropic/claude-sonnet-5-5") == "api_key"


def test_not_authenticated_until_a_record_is_saved(auth_path: Path) -> None:
    assert not cs.is_authenticated()
    cs.save_record({"type": "oauth", "provider": "claude", "access": "sk-ant-oat01-x"})
    assert auth_path.exists()
    assert cs.is_authenticated()
    cs.logout()
    assert not cs.is_authenticated()


def test_a_record_without_an_access_token_is_not_a_sign_in(auth_path: Path) -> None:
    cs.save_record({"type": "oauth", "provider": "claude"})
    assert not cs.is_authenticated()


def test_logout_leaves_other_providers_alone(auth_path: Path) -> None:
    auth_path.write_text(
        '{"codex": {"type": "oauth"}, "claude": {"type": "oauth", "access": "a"}}',
        encoding="utf-8",
    )
    cs.logout()
    assert '"codex"' in auth_path.read_text(encoding="utf-8")


def test_identity_block_comes_first_and_strix_prompt_follows() -> None:
    blocks = cs.apply_identity_blocks("Strix prompt one<cache_point>two")
    assert blocks[0] == {"type": "text", "text": cs.IDENTITY_PROMPT}
    assert [b["text"] for b in blocks[1:]] == ["Strix prompt one", "two"]


def test_identity_block_is_sent_even_without_a_strix_prompt() -> None:
    assert cs.apply_identity_blocks(None) == [{"type": "text", "text": cs.IDENTITY_PROMPT}]
    assert cs.apply_identity_blocks("") == [{"type": "text", "text": cs.IDENTITY_PROMPT}]


def test_the_identity_prompt_is_verbatim() -> None:
    # The API rejects subscription tokens unless this block matches exactly.
    assert cs.IDENTITY_PROMPT == "You are Claude Code, Anthropic's official CLI for Claude."


def test_get_valid_token_refreshes_when_near_expiry(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs.save_record(
        {"type": "oauth", "provider": "claude", "access": "old", "refresh": "r", "expires_at": 0}
    )

    def fake_refresh(refresh_token: str) -> dict[str, Any]:
        assert refresh_token == "r"  # noqa: S105 - a test value
        return {
            "type": "oauth",
            "provider": "claude",
            "access": "new",
            "refresh": "r2",
            "expires_at": 9e18,
        }

    monkeypatch.setattr(cs, "refresh_tokens", fake_refresh)
    assert cs.get_valid_token() == "new"
    assert cs.read_record()["refresh"] == "r2"


def test_get_valid_token_reuses_a_fresh_token(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs.save_record({"type": "oauth", "provider": "claude", "access": "fresh", "expires_at": 9e18})

    def explode(token: str) -> dict[str, Any]:
        raise AssertionError("should not refresh")

    monkeypatch.setattr(cs, "refresh_tokens", explode)
    assert cs.get_valid_token() == "fresh"


def test_get_valid_token_without_a_sign_in_says_how_to_fix_it(auth_path: Path) -> None:
    assert not auth_path.exists()
    with pytest.raises(cs.ClaudeAuthError) as excinfo:
        cs.get_valid_token()
    assert "strix auth login claude" in str(excinfo.value)
