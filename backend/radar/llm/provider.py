"""LLM provider abstraction.

All model access goes through :func:`get_llm`, which returns an
:class:`LLMProvider`. Every provider shares :meth:`BaseProvider.structured`, which

1. looks the request up in the persistent cache *before* requiring a live
   provider (so a NullProvider still serves pre-generated/demo outputs),
2. calls the provider-specific implementation,
3. logs an ``LLMCall`` row (tokens, cost, latency, cache hit, error) in its own
   short session so logging can never break a caller,
4. stores the validated output in the cache.

Callers catch :class:`LLMError` (or a subclass) and fall back to deterministic
templates.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from radar.db import session_scope
from radar.llm import cache as llm_cache
from radar.llm.cost import cost_usd
from radar.models import LLMCall
from radar.settings import get_settings

log = logging.getLogger(__name__)

Tier = Literal["fast", "standard", "deep"]
T = TypeVar("T", bound=BaseModel)
ToolExecutor = Callable[[str, dict[str, Any]], tuple[str, bool]]  # (content, is_error)


class LLMError(RuntimeError):
    """Base class: any failure that should make the caller fall back to a template."""


class LLMUnavailable(LLMError):
    """No provider configured, missing key, network/rate-limit/auth failure."""


class LLMRefused(LLMError):
    """The model declined the request (stop_reason == 'refusal')."""


class LLMOutputInvalid(LLMError):
    """Output was truncated, unparsable, or failed schema validation."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def cost(self, model: str) -> float:
        return cost_usd(model, self.input_tokens, self.output_tokens, self.cache_read_tokens, self.cache_write_tokens)


@dataclass
class LLMResult[T: BaseModel]:
    parsed: T
    model: str
    cached: bool
    cost_usd: float
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class ToolDef:
    """A client tool. ``input_schema`` must set additionalProperties false and list every property in required."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolLoopResult:
    final_text: str
    model: str
    turns: int
    tool_calls: int
    cost_usd: float
    stop_reason: Literal["end_turn", "max_turns", "budget", "max_tokens"]


class LLMProvider(Protocol):
    name: str
    available: bool

    def model_for(self, tier: Tier) -> str: ...

    def structured(
        self,
        purpose: str,
        tier: Tier,
        system: str,
        user: str,
        schema: type[T],
        *,
        cache: bool = True,
        prompt_version: str = "v1",
        max_tokens: int = 4096,
    ) -> LLMResult[T]: ...

    def tool_loop(
        self,
        *,
        purpose: str,
        tier: Tier,
        system: str,
        user: str,
        tools: Sequence[ToolDef],
        execute: ToolExecutor,
        max_turns: int = 10,
        budget_usd: float | None = None,
    ) -> ToolLoopResult: ...


def log_call(
    *,
    provider: str,
    model: str,
    purpose: str,
    usage: Usage | None = None,
    cost: float = 0.0,
    cache_hit: bool = False,
    latency_ms: float = 0.0,
    ok: bool = True,
    error: str | None = None,
) -> None:
    """Persist one LLMCall row in an independent session; never raises."""
    u = usage or Usage()
    try:
        with session_scope() as s:
            s.add(
                LLMCall(
                    provider=provider,
                    model=model[:80],
                    purpose=purpose[:60],
                    input_tokens=u.input_tokens + u.cache_write_tokens,
                    output_tokens=u.output_tokens,
                    cache_read_tokens=u.cache_read_tokens,
                    cost_usd=cost,
                    cache_hit=cache_hit,
                    latency_ms=latency_ms,
                    ok=ok,
                    error=(error or None) and error[:2000],
                )
            )
    except Exception as e:  # noqa: BLE001
        log.warning("failed to log LLM call: %s", e)


class BaseProvider:
    """Shared caching/logging; subclasses implement ``_call_structured`` and ``tool_loop``."""

    name: str = "base"
    available: bool = False

    def model_for(self, tier: Tier) -> str:
        raise NotImplementedError

    def cache_models(self, tier: Tier) -> list[str]:
        """Model ids whose cached outputs this provider may serve."""
        return [self.model_for(tier)]

    def _call_structured(
        self, model: str, tier: Tier, system: str, user: str, schema: type[T], max_tokens: int
    ) -> tuple[T, Usage]:
        raise LLMUnavailable(f"provider {self.name} cannot make live calls")

    def structured(
        self,
        purpose: str,
        tier: Tier,
        system: str,
        user: str,
        schema: type[T],
        *,
        cache: bool = True,
        prompt_version: str = "v1",
        max_tokens: int = 4096,
    ) -> LLMResult[T]:
        inputs = {"system": system, "user": user, "schema": schema.model_json_schema()}
        if cache:
            for m in self.cache_models(tier):
                hit = llm_cache.cache_get(llm_cache.cache_key(m, purpose, prompt_version, inputs))
                if hit is None:
                    continue
                try:
                    parsed = schema.model_validate(hit)
                except ValidationError:
                    continue
                log_call(provider=self.name, model=m, purpose=purpose, cache_hit=True)
                return LLMResult(parsed=parsed, model=m, cached=True, cost_usd=0.0)

        if not self.available:
            raise LLMUnavailable("no live LLM provider configured and no cached output")

        model = self.model_for(tier)
        t0 = time.perf_counter()
        try:
            parsed, usage = self._call_structured(model, tier, system, user, schema, max_tokens)
        except LLMError as e:
            log_call(
                provider=self.name, model=model, purpose=purpose, ok=False, error=f"{type(e).__name__}: {e}",
                latency_ms=(time.perf_counter() - t0) * 1000,
            )
            raise
        cost = usage.cost(model)
        log_call(
            provider=self.name, model=model, purpose=purpose, usage=usage, cost=cost,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
        if cache:
            key = llm_cache.cache_key(model, purpose, prompt_version, inputs)
            llm_cache.cache_put(key, purpose, model, parsed.model_dump(mode="json"))
        return LLMResult(parsed=parsed, model=model, cached=False, cost_usd=cost, usage=usage)

    def tool_loop(
        self,
        *,
        purpose: str,
        tier: Tier,
        system: str,
        user: str,
        tools: Sequence[ToolDef],
        execute: ToolExecutor,
        max_turns: int = 10,
        budget_usd: float | None = None,
    ) -> ToolLoopResult:
        raise LLMUnavailable(f"provider {self.name} does not support tool use")


class NullProvider(BaseProvider):
    """No live model. Serves cache hits (from any configured provider's models); otherwise LLMUnavailable."""

    name = "none"
    available = False

    def model_for(self, tier: Tier) -> str:
        return anthropic_model_for(tier)

    def cache_models(self, tier: Tier) -> list[str]:
        return list(dict.fromkeys([anthropic_model_for(tier), openai_model_for(tier)]))


def anthropic_model_for(tier: Tier) -> str:
    s = get_settings()
    return {"fast": s.llm_fast_model, "standard": s.llm_standard_model, "deep": s.llm_deep_model}[tier]


def openai_model_for(tier: Tier) -> str:
    s = get_settings()
    return s.openai_fast_model if tier == "fast" else s.openai_deep_model


_PROVIDERS: dict[tuple[str, str], BaseProvider] = {}


def get_llm() -> BaseProvider:
    """The configured provider (cached per provider/key); NullProvider when none is usable."""
    s = get_settings()
    choice = (s.llm_provider or "none").lower()
    try:
        if choice == "anthropic" and s.anthropic_api_key:
            key = ("anthropic", s.anthropic_api_key)
            if key not in _PROVIDERS:
                from radar.llm.anthropic_provider import AnthropicProvider

                _PROVIDERS[key] = AnthropicProvider(api_key=s.anthropic_api_key)
            return _PROVIDERS[key]
        if choice == "openai" and s.openai_api_key:
            key = ("openai", s.openai_api_key)
            if key not in _PROVIDERS:
                from radar.llm.openai_provider import OpenAIProvider

                _PROVIDERS[key] = OpenAIProvider(api_key=s.openai_api_key)
            return _PROVIDERS[key]
    except Exception as e:  # noqa: BLE001 — a broken SDK install must degrade to templates
        log.warning("LLM provider %s unavailable: %s", choice, e)
    return NullProvider()
