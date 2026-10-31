"""Provider abstraction: anything that can reason over text for San AI."""
from __future__ import annotations

import abc
import json
import re
from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    """Raised when a task has spent its allowance. Stops the loop, not the server."""


# USD per million tokens. Used for the spend ceiling and the dashboard.
PRICES: dict[str, dict[str, float]] = {
    "claude-opus-5-5":  {"in": 4.00, "out": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-opus-5":    {"in": 5.00, "out": 25.00, "cache_read": 0.25, "cache_write": 6.25},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125},
}
_FALLBACK_PRICE = {"in": 5.00, "out": 25.00, "cache_read": 0.25, "cache_write": 6.25}


@dataclass
class Usage:
    """Running tally of what the brain has cost. Local models cost 0."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    per_model: dict[str, int] = field(default_factory=dict)

    def add(
        self,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_read_tokens += cache_read_tokens
        self.cache_write_tokens += cache_write_tokens
        self.per_model[model] = self.per_model.get(model, 0) + 1
        p = PRICES.get(model, _FALLBACK_PRICE)
        self.cost_usd += (
            input_tokens * p["in"]
            + output_tokens * p["out"]
            + cache_read_tokens * p["cache_read"]
            + cache_write_tokens * p["cache_write"]
        ) / 1_000_000

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_usd": round(self.cost_usd, 4),
            "per_model": dict(self.per_model),
        }


@dataclass
class Budget:
    """A ceiling on one operation. max_usd <= 0 means no ceiling."""

    max_usd: float = 0.0
    max_calls: int = 0

    def check(self, usage: Usage) -> None:
        if self.max_usd > 0 and usage.cost_usd >= self.max_usd:
            raise BudgetExceeded(
                f"spend ceiling reached: ${usage.cost_usd:.2f} of ${self.max_usd:.2f}"
            )
        if self.max_calls > 0 and usage.calls >= self.max_calls:
            raise BudgetExceeded(
                f"call ceiling reached: {usage.calls} of {self.max_calls} model calls"
            )


class LLM(abc.ABC):
    """Minimal surface the engine needs. Two calls, nothing else."""

    name: str = "base"

    def __init__(self) -> None:
        self.usage = Usage()
        self.budget = Budget()

    @abc.abstractmethod
    def text(
        self, system: str, user: str, *, deep: bool = False, max_tokens: int | None = None
    ) -> str:
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
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ProviderError(f"could not parse JSON from model output: {raw[:400]}")
