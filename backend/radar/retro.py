"""Retrospective set: public projects used to sanity-check the signal framework.

These are well-known open-source projects that became (or are run by) companies.
They are NOT presented as predictions. The Retrospective page shows which parts of
the framework fire on their *observable* public signals (real star history from
GitHub's star-history endpoint, commit activity, releases, HN discussion), and
states plainly where historical data is unavailable.

`why` strings must stay neutral and verifiable from the linked repository.
"""

RETRO: list[dict[str, str]] = [
    {"repo": "vllm-project/vllm", "why": "LLM inference engine; organization-owned open-source project with a large contributor base."},
    {"repo": "sgl-project/sglang", "why": "LLM serving framework; a younger project in the same category as vLLM."},
    {"repo": "ollama/ollama", "why": "Local model runtime; company-run open-source project."},
    {"repo": "unslothai/unsloth", "why": "Fine-tuning performance library; company-run open-source project."},
    {"repo": "tinygrad/tinygrad", "why": "Minimal deep-learning framework targeting alternative accelerators."},
    {"repo": "Physical-Intelligence/openpi", "why": "Open robot foundation-model code released by a robotics AI company."},
    {"repo": "huggingface/lerobot", "why": "Open-source robot-learning library; useful contrast (part of a large established company)."},
    {"repo": "commaai/openpilot", "why": "Open-source driver-assistance system; long-lived company-run project."},
]
RETRO_REPOS = [r["repo"] for r in RETRO]
