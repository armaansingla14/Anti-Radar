"""AI-system review, only for AI-native companies (model dependencies, agents, evaluation, observability).

Each aspect is ``known`` (directly visible in public artifacts), ``inferred`` (deduced from them) or
``unknown``. The default is unknown: most of what matters (prompts, guardrails, fallbacks) is not public.
"""

from __future__ import annotations

import re
from typing import Any

from radar.diligence.types import ArtifactDraft

EVAL_PATH = re.compile(r"(^|/)(evals?|evaluation|benchmarks?)(/|$)|lm_eval|lm-eval|ragas|deepeval|promptfoo", re.I)
AI_TAGS = re.compile(r"\b(ai|llm|ml|agent|inference|model|genai)\b", re.I)


def _item(aspect: str, state: str, detail: str, evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"aspect": aspect, "state": state, "detail": detail, "evidence": evidence or []}


def _ev(services: list[dict], category: str) -> list[dict[str, Any]]:
    return [{"vendor": s["vendor"], "path": e["path"], "item": e["item"], "url": e["url"]}
            for s in services if s["category"] == category for e in s["evidence"][:2]]


def is_ai_native(deps: dict[str, Any], thesis_tags: list[str], artifacts: list[ArtifactDraft]) -> bool:
    cats = {s["category"] for s in deps.get("external_services") or []}
    if cats & {"llm_provider", "model_router", "agent_framework", "model_runtime", "model_hub", "evaluation",
               "llm_observability"}:
        return True
    return any(AI_TAGS.search(t.replace(".", " ").replace("_", " ")) for t in thesis_tags)


def ai_review(artifacts: list[ArtifactDraft], deps: dict[str, Any], thesis_tags: list[str]) -> dict[str, Any] | None:
    if not is_ai_native(deps, thesis_tags, artifacts):
        return None
    services = [s for s in deps.get("external_services") or [] if s["runtime"] or s["category"] == "evaluation"]
    vendors = sorted({s["vendor"] for s in services if s["category"] == "llm_provider"})
    routers = sorted({s["vendor"] for s in services if s["category"] == "model_router"})
    runtimes = sorted({s["vendor"] for s in services if s["category"] == "model_runtime"})
    agents = sorted({s["vendor"] for s in services if s["category"] == "agent_framework"})
    obs = sorted({s["vendor"] for s in services if s["category"] == "llm_observability"})
    paths = [p for t in artifacts if t.kind == "tree" for p in t.data.get("paths") or []]
    eval_paths = sorted({p for p in paths if EVAL_PATH.search(p)})[:5]
    items: list[dict[str, Any]] = []

    if vendors:
        items.append(_item("model_providers", "known", f"Hosted model APIs used: {', '.join(vendors)}.",
                           _ev(services, "llm_provider")))
    elif runtimes:
        items.append(_item("model_providers", "inferred", f"No hosted model API SDKs; local runtimes: "
                           f"{', '.join(runtimes)} (models likely self-hosted).", _ev(services, "model_runtime")))
    else:
        items.append(_item("model_providers", "unknown", "No model SDKs or runtimes in the fetched manifests."))

    if len(vendors) == 1 and not routers:
        items.append(_item("provider_concentration", "known",
                           f"All hosted model access goes through one provider ({vendors[0]}).",
                           _ev(services, "llm_provider")))
    elif len(vendors) > 1:
        items.append(_item("provider_concentration", "known", f"{len(vendors)} hosted providers referenced.",
                           _ev(services, "llm_provider")))
    else:
        items.append(_item("provider_concentration", "unknown", "Provider concentration cannot be determined."))

    if routers:
        items.append(_item("model_switching", "inferred", f"A model router is a dependency ({', '.join(routers)}); "
                           "switching providers is likely configuration.", _ev(services, "model_router")))
    elif len(vendors) > 1:
        items.append(_item("model_switching", "inferred", "Several provider SDKs suggest more than one provider "
                           "can be used.", _ev(services, "llm_provider")))
    else:
        items.append(_item("model_switching", "unknown", "No public evidence of provider switching."))

    if agents:
        items.append(_item("agent_frameworks", "known", f"Agent / tool frameworks: {', '.join(agents)}. Tool "
                           "permissions and prompt-injection handling are not visible in public artifacts.",
                           _ev(services, "agent_framework")))
    else:
        items.append(_item("agent_frameworks", "unknown", "No agent or tool-calling framework in the fetched manifests."))

    evals = sorted({s["vendor"] for s in services if s["category"] == "evaluation"})
    if eval_paths or evals:
        detail = "Evaluation evidence: " + ", ".join(evals + eval_paths)
        items.append(_item("evaluation", "known", detail + ".",
                           [{"path": p, "item": "path", "url": ""} for p in eval_paths] + _ev(services, "evaluation")))
    else:
        items.append(_item("evaluation", "unknown", "No evaluation harness or eval dependencies in public artifacts."))

    if obs:
        items.append(_item("observability", "known", f"LLM observability: {', '.join(obs)}.",
                           _ev(services, "llm_observability")))
    else:
        items.append(_item("observability", "unknown", "No LLM tracing/observability dependency found."))

    items.append(_item("fallbacks", "inferred" if routers else "unknown",
                       "Routers commonly provide fallbacks; configuration is not public." if routers
                       else "Fallback behaviour when a model or provider fails is not visible."))
    return {
        "items": items,
        "llm_providers": vendors,
        "routers": routers,
        "runtimes": runtimes,
        "agent_frameworks": agents,
        "eval_evidence": eval_paths + evals,
        "observability": obs,
        "unknown": [i["detail"] for i in items if i["state"] == "unknown"],
    }
