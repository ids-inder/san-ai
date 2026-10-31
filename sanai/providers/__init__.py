"""Brain registry - swap the reasoning engine without touching the engine."""
from __future__ import annotations

from ..config import Settings, settings
from .base import LLM, ProviderError, salvage_json

__all__ = ["LLM", "ProviderError", "salvage_json", "build_llm"]


def build_llm(cfg: Settings | None = None) -> LLM:
    cfg = cfg or settings()
    kind = (cfg.provider or "anthropic").lower()
    if kind == "anthropic":
        from .anthropic_provider import AnthropicLLM

        return AnthropicLLM(cfg)
    if kind == "ollama":
        from .ollama_provider import OllamaLLM

        return OllamaLLM(cfg)
    raise ProviderError(f"unknown provider {kind!r} (expected 'anthropic' or 'ollama')")
