"""Provider abstraction: anything that can reason over text for San AI."""
from __future__ import annotations

import abc
import json
import re
from typing import Any


class ProviderError(RuntimeError):
    pass


class LLM(abc.ABC):
    """Minimal surface the engine needs. Two calls, nothing else."""

    name: str = "base"

    @abc.abstractmethod
    def text(self, system: str, user: str, *, deep: bool = False, max_tokens: int | None = None) -> str:
        """Free-form completion."""

    @abc.abstractmethod
    def json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        deep: bool = False,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Completion constrained to a JSON schema."""


def salvage_json(raw: str) -> dict[str, Any]:
    """Best-effort recovery when a model wraps JSON in prose or fences."""
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ProviderError(f"could not parse JSON from model output: {raw[:400]}")
