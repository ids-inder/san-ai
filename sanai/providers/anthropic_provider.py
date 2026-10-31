"""Claude-backed brain.

Uses the Messages API with adaptive thinking and structured outputs. Streams
every request so large max_tokens values don't hit HTTP timeouts.
"""
from __future__ import annotations

from typing import Any

from ..config import Settings
from .base import LLM, ProviderError, salvage_json


class AnthropicLLM(LLM):
    name = "anthropic"

    def __init__(self, cfg: Settings) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("pip install anthropic") from exc
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.cfg = cfg

    def _model(self, deep: bool) -> str:
        return self.cfg.deep_model if deep else self.cfg.model

    def _call(
        self,
        system: str,
        user: str,
        *,
        deep: bool,
        max_tokens: int | None,
        output_config: dict[str, Any] | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model(deep),
            "max_tokens": max_tokens or self.cfg.max_tokens,
            # Stable prefix first so the cache can hold the system prompt.
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": user}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "max" if deep else self.cfg.effort},
        }
        if output_config:
            kwargs["output_config"].update(output_config)

        try:
            with self.client.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except self._anthropic.APIStatusError as exc:
            raise ProviderError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise ProviderError(f"cannot reach the Claude API: {exc}") from exc

        if msg.stop_reason == "refusal":
            detail = getattr(msg, "stop_details", None)
            cat = getattr(detail, "category", None)
            raise ProviderError(f"request declined by safety classifier (category={cat})")

        parts = [b.text for b in msg.content if b.type == "text"]
        out = "\n".join(p for p in parts if p).strip()
        if not out:
            raise ProviderError(f"empty response (stop_reason={msg.stop_reason})")
        return out

    def text(self, system: str, user: str, *, deep: bool = False, max_tokens: int | None = None) -> str:
        return self._call(system, user, deep=deep, max_tokens=max_tokens)

    def json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        deep: bool = False,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        raw = self._call(
            system,
            user,
            deep=deep,
            max_tokens=max_tokens,
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        return salvage_json(raw)
