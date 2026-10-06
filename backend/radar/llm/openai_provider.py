"""Optional OpenAI provider (Responses API). Prices in cost.py are approximate."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

import openai
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
    log_call,
    openai_model_for,
)

_EFFORT: dict[Tier, str] = {"fast": "low", "standard": "low", "deep": "medium"}


def _usage(resp: Any) -> Usage:
    u = getattr(resp, "usage", None)
    if u is None:
        return Usage()
    cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
    return Usage(
        input_tokens=max((u.input_tokens or 0) - cached, 0),
        output_tokens=u.output_tokens or 0,
        cache_read_tokens=cached,
    )


def _check_refusal(resp: Any) -> None:
    for item in getattr(resp, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                raise LLMRefused(getattr(part, "refusal", "refused"))


def _map_api_error(e: Exception) -> LLMError:
    if isinstance(e, openai.BadRequestError):
        return LLMError(f"bad request: {e}")
    return LLMUnavailable(f"{type(e).__name__}: {e}")


class OpenAIProvider(BaseProvider):
    name = "openai"
    available = True

    def __init__(self, api_key: str | None = None, *, client: Any | None = None) -> None:
        self.client = client or openai.OpenAI(api_key=api_key, max_retries=2, timeout=300.0)

    def model_for(self, tier: Tier) -> str:
        return openai_model_for(tier)

    def _call_structured(
        self, model: str, tier: Tier, system: str, user: str, schema: type[T], max_tokens: int
    ) -> tuple[T, Usage]:
        try:
            resp = self.client.responses.parse(
                model=model,
                instructions=system,
                input=user,
                text_format=schema,
                max_output_tokens=max_tokens,
                reasoning={"effort": _EFFORT[tier]},
            )
        except openai.APIError as e:
            raise _map_api_error(e) from e
        except (ValidationError, ValueError) as e:
            raise LLMOutputInvalid(str(e)) from e
        _check_refusal(resp)
        parsed = resp.output_parsed
        if parsed is None:
            raise LLMOutputInvalid("no parsed output (possibly truncated)")
        return parsed, _usage(resp)

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
        model = self.model_for(tier)
        tool_params = [
            {"type": "function", "name": t.name, "description": t.description, "parameters": t.input_schema,
             "strict": True}
            for t in tools
        ]
        cost, turns, n_calls = 0.0, 0, 0
        final_text, stop = "", "max_turns"
        prev_id: str | None = None
        next_input: Any = user
        while turns < max_turns:
            turns += 1
            t0 = time.perf_counter()
            try:
                kwargs: dict[str, Any] = {
                    "model": model, "instructions": system, "input": next_input, "tools": tool_params,
                    "reasoning": {"effort": _EFFORT[tier]}, "max_output_tokens": 16000,
                }
                if prev_id:
                    kwargs["previous_response_id"] = prev_id
                resp = self.client.responses.create(**kwargs)
            except openai.APIError as e:
                err = _map_api_error(e)
                log_call(provider=self.name, model=model, purpose=purpose, ok=False, error=str(err),
                         latency_ms=(time.perf_counter() - t0) * 1000)
                raise err from e
            usage = _usage(resp)
            c = usage.cost(model)
            cost += c
            log_call(provider=self.name, model=model, purpose=purpose, usage=usage, cost=c,
                     latency_ms=(time.perf_counter() - t0) * 1000)
            _check_refusal(resp)
            if resp.output_text:
                final_text = resp.output_text
            calls = [o for o in resp.output if getattr(o, "type", None) == "function_call"]
            if not calls:
                stop = "end_turn"
                break
            outputs = []
            for c_ in calls:
                n_calls += 1
                try:
                    content, is_error = execute(c_.name, json.loads(c_.arguments or "{}"))
                except Exception as e:  # noqa: BLE001
                    content, is_error = f"{type(e).__name__}: {e}", True
                outputs.append({"type": "function_call_output", "call_id": c_.call_id,
                                "output": ("ERROR: " + content) if is_error else content})
            prev_id, next_input = resp.id, outputs
            if budget_usd is not None and cost > budget_usd:
                stop = "budget"
                break
        return ToolLoopResult(final_text=final_text, model=model, turns=turns, tool_calls=n_calls,
                              cost_usd=round(cost, 6), stop_reason=stop)  # type: ignore[arg-type]
