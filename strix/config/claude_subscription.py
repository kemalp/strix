"""Claude subscription auth: run Strix on a Claude plan via a Claude Code OAuth token.

The Anthropic API accepts an OAuth token minted by ``claude setup-token`` (an
``sk-ant-oat01-...`` bearer). Using a Claude subscription outside Anthropic's own
products is not officially supported; the user chooses this path knowingly.

The API gates subscription tokens on the request *identifying itself as Claude
Code*: the system prompt must start with :data:`IDENTITY_PROMPT` as its own text
block, byte for byte. Strix's own prompt is appended as a second block, and the
gate passes. The system prompt must be split into blocks for this, because the
identical text folded into one string is rejected.

Selecting the route:

- ``STRIX_LLM=claude/<model>`` — use the stored token from ``strix auth login
  claude``.
- ``STRIX_LLM=claude-code/<model>`` — same, for users who read it as the CLI.

The token is sent as the API key. LiteLLM recognises the ``sk-ant-oat`` prefix
and swaps it for an ``Authorization: Bearer`` header with the mandatory
``anthropic-beta`` values, so nothing else in the request changes.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests

from strix.utils.secret_files import write_secret_text


if TYPE_CHECKING:
    from collections.abc import Iterator

try:  # POSIX only; the Windows fallback is a no-op guard.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

PROVIDER = "claude"

# The console OAuth client Claude Code uses. ``setup-token`` and the login flow
# share it, so a token from either refreshes the same way.
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"  # noqa: S105 - URL, not a secret

# Must be the first system block, verbatim, or the subscription route rejects
# the request as a plain API call with 429.
IDENTITY_PROMPT = "You are Claude Code, Anthropic's official CLI for Claude."

PREFIXES = ("claude/", "claude-code/")

_TOKEN_TIMEOUT = 30
_EXPIRY_SKEW_S = 120

AUTH_PATH = Path.home() / ".strix" / "claude-subscription-auth.json"


def _read_store() -> dict[str, Any]:
    try:
        data = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_store(data: dict[str, Any]) -> None:
    write_secret_text(AUTH_PATH, json.dumps(data, indent=2))


def read_record() -> dict[str, Any] | None:
    record = _read_store().get(PROVIDER)
    if not isinstance(record, dict) or record.get("type") != "oauth":
        return None
    if not record.get("access"):
        return None
    return record


def is_authenticated() -> bool:
    return read_record() is not None


def save_record(record: dict[str, Any]) -> None:
    data = _read_store()
    data[PROVIDER] = record
    _write_store(data)


def logout() -> None:
    data = _read_store()
    if PROVIDER not in data:
        return
    del data[PROVIDER]
    if data:
        _write_store(data)
        return
    with contextlib.suppress(OSError):
        AUTH_PATH.unlink()


@contextlib.contextmanager
def _refresh_guard() -> Iterator[None]:
    """Serialize refresh across Strix processes; the refresh token is single-use."""
    try:
        if fcntl is None:
            yield
            return
        lock_path = AUTH_PATH.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("w")
    except (ImportError, OSError):
        yield
        return
    try:
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class ClaudeAuthError(Exception):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


def subscription_model(model_name: str | None) -> str | None:
    """The model slug behind a ``claude/<model>`` STRIX_LLM, or None."""
    name = (model_name or "").strip()
    lowered = name.lower()
    for prefix in PREFIXES:
        if lowered.startswith(prefix):
            return name[len(prefix) :] or None
    return None


def auth_mode(model_name: str | None) -> str:
    return "subscription" if subscription_model(model_name) else "api_key"


def _near_expiry(record: dict[str, Any]) -> bool:
    expires_at = record.get("expires_at")
    if not isinstance(expires_at, int | float):
        return True
    return expires_at - _EXPIRY_SKEW_S <= time.time()


def _post_refresh(refresh_token: str) -> dict[str, Any]:
    try:
        with requests.post(
            TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
            headers={"Accept": "application/json"},
            timeout=_TOKEN_TIMEOUT,
        ) as response:
            status = response.status_code
            body = response.content
            detail = response.text[:300]
    except requests.RequestException as exc:
        raise ClaudeAuthError("unavailable", str(exc)) from exc
    if status >= 400:
        raise ClaudeAuthError("token_http_error", f"HTTP {status}: {detail}")
    data = json.loads(body or b"{}")
    if not isinstance(data, dict):
        raise ClaudeAuthError("bad_response", "token endpoint returned non-object")
    return data


def refresh_tokens(refresh_token: str) -> dict[str, Any]:
    data = _post_refresh(refresh_token)
    access = data.get("access_token")
    if not isinstance(access, str) or not access:
        raise ClaudeAuthError("bad_response", "refresh response missing access_token")
    ttl = data.get("expires_in")
    record: dict[str, Any] = {
        "type": "oauth",
        "provider": PROVIDER,
        "access": access,
        "expires_at": time.time() + (ttl if isinstance(ttl, int | float) else 3600),
    }
    rotated = data.get("refresh_token")
    record["refresh"] = rotated if isinstance(rotated, str) and rotated else refresh_token
    return record


def get_valid_token() -> str:
    """The usable access token, refreshed (once, guarded) if near expiry."""
    record = read_record()
    if record is None:
        raise ClaudeAuthError(
            "not_authenticated",
            "not signed in; run: strix auth login claude",
        )
    if not _near_expiry(record):
        return str(record["access"])
    refresh = record.get("refresh")
    if not isinstance(refresh, str) or not refresh:
        raise ClaudeAuthError(
            "no_refresh_token",
            "stored sign-in has no refresh token; run: strix auth login claude",
        )
    with _refresh_guard():
        record = read_record()
        if record is None:
            raise ClaudeAuthError("not_authenticated", "signed out during refresh")
        if not _near_expiry(record):
            return str(record["access"])
        latest_refresh = record.get("refresh")
        try:
            refreshed = refresh_tokens(
                latest_refresh if isinstance(latest_refresh, str) else refresh
            )
        except ClaudeAuthError:
            # A peer process may have spent the single-use refresh token already.
            latest = read_record()
            if latest and latest.get("refresh") != latest_refresh and not _near_expiry(latest):
                return str(latest["access"])
            raise
        save_record(refreshed)
        return str(refreshed["access"])


def apply_identity_blocks(system_instructions: str | None) -> list[dict[str, Any]]:
    """System blocks with the required identity block first, then Strix's prompt."""
    parts = [part for part in (system_instructions or "").split("<cache_point>") if part.strip()]
    return [
        {"type": "text", "text": IDENTITY_PROMPT},
        *({"type": "text", "text": part} for part in parts),
    ]
