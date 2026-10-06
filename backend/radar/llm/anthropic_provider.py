"""Anthropic (Claude) provider.

Tier mapping (models come from settings):
* fast     — Haiku 4.5: classification/extraction. No effort parameter, no thinking.
* standard — Sonnet 5.5: narratives and the morning brief, ``effort: low``.
* deep     — Opus 5.5: investigation and memo, thinking always on, ``effort: medium``.

Sonnet 5.5 / Opus 5.5 reject forced ``tool_choice``; tools are offered with
``tool_choice`` auto and ``strict: true`` schemas and the prompt steers usage.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import anthropic
from pydantic import ValidationError

from radar.llm.provider import (
    BaseProvider,
    LLMError,
    LLMOutputInvalid,
    LLMRefused,
    LLMUnavailable,
    T,
    Tier,
    ToolDef,
    ToolExecutor,
    ToolLoopResult,
    Usage,
    anthropic_model_for,
    log_call,
)

_EFFORT: dict[Tier, str | None] = {"fast": None, "standard": "low", "deep": "medium"}
_LOOP_MAX_TOKENS = 16000


def _usage(resp: Any) -> Usage:
    u = getattr(resp, "usage", None)
    if u is None:
        return Usage()
    return Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )


def _map_api_error(e: Exception) -> LLMError:
    if isinstance(e, anthropic.BadRequestError):
        return LLMError(f"bad request: {e}")
    if isinstance(e, (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APIStatusError)):
        return LLMUnavailable(f"{type(e).__name__}: {e}")
    return LLMError(str(e))


def _check_stop(resp: Any) -> None:
    if resp.stop_reason == "refusal":
        details = getattr(resp, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise LLMRefused(f"model refused (category={category})")


class AnthropicProvider(BaseProvider):
    name = "anthropic"
    available = True

    def __init__(self, api_key: str | None = None, *, client: Any | None = None) -> None:
        """``client`` may be injected (tests use a scripted fake with the same surface)."""
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=300.0)

    def model_for(self, tier: Tier) -> str:
        return anthropic_model_for(tier)

    @staticmethod
    def _output_config(tier: Tier) -> dict[str, Any] | None:
        effort = _EFFORT[tier]
        return {"effort": effort} if effort else None

    def _call_structured(
        self, model: str, tier: Tier, system: str, user: str, schema: type[T], max_tokens: int
    ) -> tuple[T, Usage]:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_format": schema,
        }
        oc = self._output_config(tier)
        if oc:
            kwargs["output_config"] = oc
        try:
            resp = self.client.messages.parse(**kwargs)
        except anthropic.APIError as e:
            raise _map_api_error(e) from e
        except (ValidationError, ValueError) as e:
            raise LLMOutputInvalid(f"structured output failed validation: {e}") from e
        _check_stop(resp)
        usage = _usage(resp)
        if resp.stop_reason == "max_tokens":
            raise LLMOutputInvalid("output truncated at max_tokens")
        parsed = resp.parsed_output
        if parsed is None:
            raise LLMOutputInvalid("no parsed output in response")
        if not isinstance(parsed, schema):
            try:
                parsed = schema.model_validate(parsed)
            except ValidationError as e:
                raise LLMOutputInvalid(str(e)) from e
        return parsed, usage

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
        """Manual agentic loop: run ALL tool_use blocks per turn, return ALL results in one user message."""
        model = self.model_for(tier)
        tool_params = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema, "strict": True}
            for t in tools
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        cost = 0.0
        turns = 0
        n_calls = 0
        final_text = ""
        stop: str = "max_turns"
        oc = self._output_config(tier)
        while turns < max_turns:
            turns += 1
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": _LOOP_MAX_TOKENS,
                "system": system,
                "messages": messages,
                "tools": tool_params,
                "tool_choice": {"type": "auto"},
                "cache_control": {"type": "ephemeral"},
            }
            if oc:
                kwargs["output_config"] = oc
            t0 = time.perf_counter()
            try:
                resp = self.client.messages.create(**kwargs)
            except anthropic.APIError as e:
                err = _map_api_error(e)
                log_call(provider=self.name, model=model, purpose=purpose, ok=False, error=str(err),
                         latency_ms=(time.perf_counter() - t0) * 1000)
                raise err from e
            usage = _usage(resp)
            call_cost = usage.cost(model)
            cost += call_cost
            log_call(provider=self.name, model=model, purpose=purpose, usage=usage, cost=call_cost,
                     latency_ms=(time.perf_counter() - t0) * 1000, ok=resp.stop_reason != "refusal",
                     error="refusal" if resp.stop_reason == "refusal" else None)
            _check_stop(resp)
            text = "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
            if text:
                final_text = text
            if resp.stop_reason != "tool_use":
                stop = "max_tokens" if resp.stop_reason == "max_tokens" else "end_turn"
                break
            # Thinking blocks must be passed back unchanged: append the full content list.
            messages.append({"role": "assistant", "content": resp.content})
            results: list[dict[str, Any]] = []
            for block in resp.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                n_calls += 1
                try:
                    content, is_error = execute(block.name, dict(block.input or {}))
                except Exception as e:  # noqa: BLE001 — tool failures are reported to the model
                    content, is_error = f"Tool error: {type(e).__name__}: {e}", True
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}
                )
            messages.append({"role": "user", "content": results})
            if budget_usd is not None and cost > budget_usd:
                stop = "budget"
                break
        return ToolLoopResult(
            final_text=final_text, model=model, turns=turns, tool_calls=n_calls, cost_usd=round(cost, 6),
            stop_reason=stop,  # type: ignore[arg-type]
        )
