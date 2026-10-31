"""Claude-backed brain.

Uses the Messages API with adaptive thinking and structured outputs. Every
request streams, so large max_tokens values don't hit HTTP timeouts, and
every request opts into server-side refusal fallback - an unattended agent
should not die mid-task because one call tripped a safety classifier.
"""
from __future__ import annotations

from typing import Any

from ..config import Settings
from .base import LLM, ProviderError, salvage_json

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLLM(LLM):
    name = "anthropic"

    def __init__(self, cfg: Settings) -> None:
        super().__init__()
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("pip install anthropic") from exc
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.cfg = cfg
        # Flipped off permanently if the deployment rejects the beta.
        self._fallback_supported = True

    def _model(self, deep: bool) -> str:
        return self.cfg.deep_model if deep else self.cfg.model

    def _build(
        self,
        system: str,
        user: str,
        *,
        deep: bool,
        max_tokens: int | None,
        output_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
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
        return kwargs

    def _call(
        self,
        system: str,
        user: str,
        *,
        deep: bool,
        max_tokens: int | None,
        output_config: dict[str, Any] | None = None,
    ) -> str:
        self.budget.check(self.usage)
        kwargs = self._build(
            system, user, deep=deep, max_tokens=max_tokens, output_config=output_config
        )

        msg = self._send(kwargs)
        self._record(msg, kwargs["model"])

        if msg.stop_reason == "refusal":
            detail = getattr(msg, "stop_details", None)
            cat = getattr(detail, "category", None) or "unspecified"
            raise ProviderError(
                f"request declined by a safety classifier (category={cat}); "
                "no fallback model produced a response"
            )

        parts = [b.text for b in msg.content if b.type == "text"]
        out = "\n".join(p for p in parts if p).strip()
        if not out:
            raise ProviderError(f"empty response (stop_reason={msg.stop_reason})")
        return out

    def _send(self, kwargs: dict[str, Any]) -> Any:
        """Stream a request, with refusal fallback when the API supports it."""
        if self._fallback_supported:
            try:
                with self.client.beta.messages.stream(
                    betas=[FALLBACK_BETA], fallbacks="default", **kwargs
                ) as stream:
                    return stream.get_final_message()
            except self._anthropic.BadRequestError as exc:
                # Unknown beta / unsupported parameter on this deployment.
                self._fallback_supported = False
            except self._anthropic.APIStatusError as exc:
                raise ProviderError(
                    f"Claude API error {exc.status_code}: {exc.message}"
                ) from exc
            except self._anthropic.APIConnectionError as exc:
                raise ProviderError(f"cannot reach the Claude API: {exc}") from exc

        try:
            with self.client.messages.stream(**kwargs) as stream:
                return stream.get_final_message()
        except self._anthropic.APIStatusError as exc:
            raise ProviderError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise ProviderError(f"cannot reach the Claude API: {exc}") from exc

    def _record(self, msg: Any, requested_model: str) -> None:
        u = getattr(msg, "usage", None)
        if u is None:
            return
        # A fallback may have served this on a different model than requested.
        served = getattr(msg, "model", None) or requested_model
        self.usage.add(
            served,
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )

    def text(
        self, system: str, user: str, *, deep: bool = False, max_tokens: int | None = None
    ) -> str:
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
