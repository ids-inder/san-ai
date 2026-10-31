"""Local-model brain via Ollama, for a fully offline San AI."""
from __future__ import annotations

import json
from typing import Any

from ..config import Settings
from .base import LLM, ProviderError, salvage_json


class OllamaLLM(LLM):
    name = "ollama"

    def __init__(self, cfg: Settings) -> None:
        super().__init__()
        try:
            import httpx2 as httpx
        except ImportError:  # pragma: no cover - fall back to httpx if present
            try:
                import httpx  # type: ignore
            except ImportError as exc:
                raise ProviderError("pip install httpx2") from exc
        self._httpx = httpx
        self.cfg = cfg

    def _call(
        self,
        system: str,
        user: str,
        *,
        fmt: dict[str, Any] | None,
        max_tokens: int | None,
    ) -> str:
        self.budget.check(self.usage)
        payload: dict[str, Any] = {
            "model": self.cfg.ollama_model,
            "system": system,
            "prompt": user,
            "stream": False,
            "options": {"num_predict": max_tokens or 4096, "temperature": 0.2},
        }
        if fmt:
            payload["format"] = fmt
        try:
            resp = self._httpx.post(
                f"{self.cfg.ollama_host.rstrip('/')}/api/generate",
                json=payload,
                timeout=600.0,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - httpx variants differ
            raise ProviderError(f"Ollama call failed: {exc}") from exc
        body = resp.json()
        self.usage.add(
            self.cfg.ollama_model,
            input_tokens=body.get("prompt_eval_count", 0) or 0,
            output_tokens=body.get("eval_count", 0) or 0,
        )
        out = (body.get("response") or "").strip()
        if not out:
            raise ProviderError("empty response from Ollama")
        return out

    def text(self, system: str, user: str, *, deep: bool = False, max_tokens: int | None = None) -> str:
        return self._call(system, user, fmt=None, max_tokens=max_tokens)

    def json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        deep: bool = False,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        hint = (
            f"{user}\n\nReply with JSON only, matching this schema:\n"
            f"{json.dumps(schema, indent=2)}"
        )
        return salvage_json(self._call(system, hint, fmt=schema, max_tokens=max_tokens))
