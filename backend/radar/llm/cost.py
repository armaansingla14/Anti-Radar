"""Token prices and cost computation for LLM calls.

Prices are USD per 1M tokens. Anthropic prices are the published first-party API
rates; OpenAI entries are marked ``approximate`` and should be re-checked before
relying on them for budgeting.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input: float
    output: float
    cache_read: float
    cache_write: float
    approximate: bool = False


def _anthropic(inp: float, out: float) -> ModelPrice:
    # Cache reads bill at 0.1x input, 5-minute cache writes at 1.25x input.
    return ModelPrice(input=inp, output=out, cache_read=inp * 0.1, cache_write=inp * 1.25)


PRICES: dict[str, ModelPrice] = {
    "claude-haiku-4-5": _anthropic(1.0, 5.0),
    "claude-sonnet-5-5": _anthropic(2.0, 10.0),
    "claude-opus-5-5": _anthropic(4.0, 20.0),
    # OpenAI (optional provider) — approximate list prices.
    "gpt-5": ModelPrice(input=1.25, output=10.0, cache_read=0.125, cache_write=1.25, approximate=True),
    "gpt-5-mini": ModelPrice(input=0.25, output=2.0, cache_read=0.025, cache_write=0.25, approximate=True),
}

# Family fallbacks for model ids not listed exactly (e.g. a configured snapshot alias).
_FAMILY_FALLBACK: tuple[tuple[str, str], ...] = (
    ("haiku", "claude-haiku-4-5"),
    ("sonnet", "claude-sonnet-5-5"),
    ("opus", "claude-opus-5-5"),
    ("gpt-5-mini", "gpt-5-mini"),
    ("gpt-5", "gpt-5"),
)


def price_for(model: str) -> ModelPrice | None:
    if model in PRICES:
        return PRICES[model]
    m = model.lower()
    for needle, key in _FAMILY_FALLBACK:
        if needle in m:
            return PRICES[key]
    return None


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Cost of one call. ``input_tokens`` excludes cache reads/writes (Anthropic usage semantics).

    Unknown models cost 0.0 rather than raising so logging never breaks a caller.
    """
    p = price_for(model)
    if p is None:
        return 0.0
    total = (
        input_tokens * p.input
        + output_tokens * p.output
        + cache_read_tokens * p.cache_read
        + cache_write_tokens * p.cache_write
    )
    return round(total / 1_000_000, 6)
