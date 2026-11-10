"""Tests for the `strix auth` CLI: subcommand routing and provider naming."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from strix.config import claude_subscription, codex
from strix.interface import auth_cli


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "home" / ".strix"
    monkeypatch.setattr(codex, "AUTH_PATH", store / "subscription-auth.json")
    monkeypatch.setattr(claude_subscription, "AUTH_PATH", store / "claude-subscription-auth.json")


def test_login_provider_is_chatgpt() -> None:
    assert auth_cli.LOGIN_PROVIDER == "chatgpt"
    assert codex.PROVIDER in auth_cli._ACCEPTED_PROVIDERS
    assert "chatgpt" in auth_cli._ACCEPTED_PROVIDERS


def test_unknown_subcommand_returns_usage_error() -> None:
    assert auth_cli.run_auth(["bogus"]) == 2


def test_help_returns_zero() -> None:
    assert auth_cli.run_auth(["--help"]) == 0


def test_status_not_signed_in() -> None:
    assert auth_cli.run_auth(["status"]) == 1


def test_login_rejects_unsupported_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def _should_not_run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        msg = "OAuth flow must not start for an unsupported provider"
        raise AssertionError(msg)

    monkeypatch.setattr(auth_cli, "_run_oauth_flow", _should_not_run)
    assert auth_cli.run_auth(["login", "gemini"]) == 2


def test_finish_requires_state_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex, "exchange_code", lambda *_: {"ok": True})

    # Loopback (require_state=True): missing or mismatched state is rejected.
    with pytest.raises(codex.CodexAuthError) as missing:
        auth_cli._finish("code", None, "verifier", "expected", require_state=True)
    assert missing.value.code == "state_mismatch"
    with pytest.raises(codex.CodexAuthError) as mismatch:
        auth_cli._finish("code", "wrong", "verifier", "expected", require_state=True)
    assert mismatch.value.code == "state_mismatch"

    # Matching state proceeds to the exchange.
    assert auth_cli._finish("code", "expected", "verifier", "expected", require_state=True) == {
        "ok": True
    }


def test_finish_manual_paste_allows_absent_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex, "exchange_code", lambda *_: {"ok": True})
    # Manual paste (require_state=False): a bare code with no state is accepted,
    # but a present-and-wrong state is still rejected.
    assert auth_cli._finish("code", None, "verifier", "expected", require_state=False) == {
        "ok": True
    }
    with pytest.raises(codex.CodexAuthError):
        auth_cli._finish("code", "wrong", "verifier", "expected", require_state=False)


def test_finish_rejects_missing_code() -> None:
    with pytest.raises(codex.CodexAuthError) as exc:
        auth_cli._finish(None, "expected", "verifier", "expected", require_state=True)
    assert exc.value.code == "no_code"


def test_model_subcommand_removed() -> None:
    assert auth_cli.run_auth(["model", "gpt-5.5"]) == 2


@pytest.mark.parametrize("provider", ["chatgpt", "codex", "ChatGPT"])
def test_login_accepts_provider_aliases(provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
    reached = {"flow": False}

    def _fake_flow(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        reached["flow"] = True
        return {
            "type": "oauth",
            "provider": "codex",
            "access": "a",
            "refresh": "r",
            "account_id": "acct",
            "expires_at": 0,
        }

    monkeypatch.setattr(auth_cli, "_run_oauth_flow", _fake_flow)
    monkeypatch.setattr(codex, "save_record", lambda _record: None)

    assert auth_cli.run_auth(["login", provider]) == 0
    assert reached["flow"] is True


@pytest.mark.parametrize("provider", ["claude", "Claude", "claude-code"])
def test_login_accepts_the_claude_provider(provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_cli, "_keychain_oauth", lambda: None)
    monkeypatch.setattr("builtins.input", lambda *_: "sk-ant-oat01-pasted")

    assert auth_cli.run_auth(["login", provider, "--token"]) == 0
    assert claude_subscription.read_record()["access"] == "sk-ant-oat01-pasted"


def test_claude_login_falls_back_to_the_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth_cli,
        "_keychain_oauth",
        lambda: {"accessToken": "sk-ant-oat01-keychain", "refreshToken": "refresh-me"},
    )
    monkeypatch.setattr("builtins.input", lambda *_: "")
    monkeypatch.setattr(
        claude_subscription,
        "refresh_tokens",
        lambda _token: {"type": "oauth", "provider": "claude", "access": "sk-ant-oat01-refreshed"},
    )

    assert auth_cli.run_auth(["login", "claude"]) == 0
    assert claude_subscription.read_record()["access"] == "sk-ant-oat01-refreshed"


def test_claude_login_rejects_a_non_subscription_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda *_: "sk-ant-api03-an-api-key")
    assert auth_cli.run_auth(["login", "claude", "--token"]) == 1
    assert claude_subscription.read_record() is None


def test_status_reports_a_claude_sign_in() -> None:
    claude_subscription.save_record(
        {"type": "oauth", "provider": "claude", "access": "sk-ant-oat01-x"}
    )
    assert auth_cli.run_auth(["status"]) == 0


def test_logout_clears_both_providers() -> None:
    claude_subscription.save_record(
        {"type": "oauth", "provider": "claude", "access": "sk-ant-oat01-x"}
    )
    assert auth_cli.run_auth(["logout"]) == 0
    assert claude_subscription.read_record() is None
