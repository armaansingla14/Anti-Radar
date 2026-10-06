"""Versioned prompt texts. Bump the caller's ``prompt_version`` when a prompt changes materially."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache
def load_prompt(name: str) -> str:
    """Return the text of ``prompts/<name>.md`` (stripped)."""
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
