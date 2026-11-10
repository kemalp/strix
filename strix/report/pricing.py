"""LiteLLM model-name resolution for local cost estimates."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, cast


# Providers that sell a model first-party, preferred over reseller namespaces
# when one model name matches several of them.
_FIRST_PARTY_PROVIDERS = (
    "xai",
    "anthropic",
    "openai",
    "deepseek",
    "gemini",
    "mistral",
    "cohere",
    "groq",
)

_STRIPPED_PREFIXES = ("litellm/", "any-llm/", "openai/")


@lru_cache(maxsize=512)
def resolve_litellm_model(model: str) -> str | None:
    """Return a provider-qualified model name that LiteLLM can price."""
    try:
        import litellm

        normalized = _strip_prefixes(model)
        if not normalized:
            return None
        model_cost = cast("dict[str, dict[str, Any]]", getattr(litellm, "model_cost"))  # noqa: B009
        return _resolve(normalized, model_cost)
    except Exception:  # noqa: BLE001
        return None


def _strip_prefixes(model: str) -> str:
    normalized = model.strip()
    for prefix in _STRIPPED_PREFIXES:
        if normalized.startswith(prefix):
            return normalized.removeprefix(prefix)
    return normalized


def _resolve(normalized: str, model_cost: dict[str, dict[str, Any]]) -> str | None:
    exact = _resolve_exact(normalized, model_cost)
    if exact is not None:
        return exact
    candidates = [normalized]
    if "/" in normalized:
        candidates.append(normalized.rsplit("/", 1)[-1])
    for name in candidates:
        found = _resolve_suffix(name, model_cost)
        if found is not None:
            return found
    return None


def _resolve_exact(normalized: str, model_cost: dict[str, dict[str, Any]]) -> str | None:
    """Resolve names LiteLLM already keys verbatim.

    A fully qualified name is trusted as-is. A bare name adopts the provider
    LiteLLM itself records for it.
    """
    entry = model_cost.get(normalized)
    if not isinstance(entry, dict):
        return None
    if "/" in normalized:
        return normalized
    provider = entry.get("litellm_provider")
    if isinstance(provider, str) and provider:
        return f"{provider}/{normalized}"
    return None


def _resolve_suffix(name: str, model_cost: dict[str, dict[str, Any]]) -> str | None:
    """Resolve a name that only appears as a provider-qualified key suffix."""
    matches = sorted(key for key in model_cost if key.endswith(f"/{name}"))
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    # Several providers sell the model, sometimes at the same price and
    # sometimes not (grok-4.5 via kyma). Prefer the providers LiteLLM treats as
    # first-party before accepting a name only when every price agrees.
    for provider in _FIRST_PARTY_PROVIDERS:
        if f"{provider}/{name}" in matches:
            return f"{provider}/{name}"
    if len({_price_of(key, model_cost) for key in matches}) == 1:
        return matches[0]
    return None


def _price_of(key: str, model_cost: dict[str, dict[str, Any]]) -> tuple[Any, Any]:
    entry = model_cost.get(key)
    if not isinstance(entry, dict):
        return (None, None)
    return (entry.get("input_cost_per_token"), entry.get("output_cost_per_token"))
