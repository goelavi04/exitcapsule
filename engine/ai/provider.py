"""Gemma provider abstraction.

Why: Gemma 4 is reachable via Google AI Studio (Gemini API) or a local Ollama.
Both sit behind one interface so tasks, cache and replay never care which is
used. Model discovery is strict: only a Gemma 4 model is ever accepted —
there is deliberately no fallback to any other model family.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from engine.config import Settings

# Preference order when auto-discovering (larger dense model first: best at code).
_GEMMA4 = re.compile(r"gemma[-_]?4", re.IGNORECASE)
_PREFERENCE = ("31b", "26b", "12b", "e4b", "e2b")


class ProviderError(RuntimeError):
    def __init__(self, msg: str, hint: str = "") -> None:
        super().__init__(msg)
        self.hint = hint


class Completion(BaseModel):
    text: str
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0


def is_gemma4(model_id: str) -> bool:
    return bool(_GEMMA4.search(model_id))


def pick_gemma4(model_ids: list[str]) -> str | None:
    cands = [m for m in model_ids if is_gemma4(m) and "cloud" not in m.lower()]
    if not cands:
        return None
    for pref in _PREFERENCE:
        for m in cands:
            if pref in m.lower():
                return m
    return sorted(cands)[0]


class GemmaProvider(ABC):
    name: str = "abstract"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model: str | None = None

    @abstractmethod
    def list_models(self) -> list[str]: ...

    @abstractmethod
    async def _generate(
        self, model_id: str, system: str, prompt: str, temperature: float, json_mode: bool
    ) -> Completion: ...

    def resolve_model(self) -> str:
        """Return the exact Gemma 4 model ID, verified against the provider's model list."""
        if self._model:
            return self._model
        available = self.list_models()
        wanted = self.settings.gemma_model.strip()
        if wanted:
            if not is_gemma4(wanted):
                raise ProviderError(
                    f"GEMMA_MODEL={wanted!r} is not a Gemma 4 model; refusing to use it",
                    "set GEMMA_MODEL to a gemma-4 model ID or leave it empty to auto-discover",
                )
            if wanted not in available:
                raise ProviderError(
                    f"{wanted!r} not offered by {self.name}. Gemma 4 models seen: "
                    f"{[m for m in available if is_gemma4(m)]}",
                    "fix GEMMA_MODEL or leave it empty",
                )
            self._model = wanted
        else:
            picked = pick_gemma4(available)
            if not picked:
                raise ProviderError(
                    f"no Gemma 4 model available via {self.name}",
                    "check API access, or switch GEMMA_PROVIDER (google/ollama)",
                )
            self._model = picked
        return self._model

    async def generate(
        self, system: str, prompt: str, temperature: float | None = None, json_mode: bool = True
    ) -> Completion:
        model_id = self.resolve_model()
        temp = self.settings.gemma_temperature if temperature is None else temperature
        return await self._generate(model_id, system, prompt, temp, json_mode)


class GoogleProvider(GemmaProvider):
    """Google AI Studio / Gemini API via the `google-genai` SDK."""

    name = "google-ai-studio"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        if not settings.gemini_api_key or not settings.gemini_api_key.get_secret_value():
            raise ProviderError(
                "GEMINI_API_KEY is not set", "put GEMINI_API_KEY=<your key> in .env"
            )
        from google import genai

        self._client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())

    def list_models(self) -> list[str]:
        names = []
        for m in self._client.models.list():
            n = (m.name or "").removeprefix("models/")
            if n:
                names.append(n)
        return names

    async def _generate(
        self, model_id: str, system: str, prompt: str, temperature: float, json_mode: bool
    ) -> Completion:
        from google.genai import types

        # Gemma on the Gemini API may not accept system_instruction / JSON mime type,
        # so the system text is prepended to the user turn and JSON is enforced by
        # the prompt + Pydantic validation (with retries) instead.
        contents = f"{system}\n\n{prompt}" if system else prompt
        cfg = types.GenerateContentConfig(temperature=temperature)
        resp = await self._client.aio.models.generate_content(
            model=model_id, contents=contents, config=cfg
        )
        usage: Any = resp.usage_metadata
        return Completion(
            text=resp.text or "",
            model_id=model_id,
            input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
        )


class OllamaProvider(GemmaProvider):
    """Local Ollama (offline fallback). Still Gemma 4 only."""

    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        import ollama

        self._client = ollama.Client(host=settings.ollama_host)
        self._aclient = ollama.AsyncClient(host=settings.ollama_host)

    def list_models(self) -> list[str]:
        try:
            resp = self._client.list()
        except Exception as exc:
            raise ProviderError(f"ollama not reachable: {exc}", "start Ollama") from exc
        return [m.model for m in resp.models if m.model]

    async def _generate(
        self, model_id: str, system: str, prompt: str, temperature: float, json_mode: bool
    ) -> Completion:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        resp = await self._aclient.chat(
            model=model_id,
            messages=msgs,
            format="json" if json_mode else None,
            options={"temperature": temperature},
        )
        return Completion(
            text=resp.message.content or "",
            model_id=model_id,
            input_tokens=int(resp.prompt_eval_count or 0),
            output_tokens=int(resp.eval_count or 0),
        )


def make_provider(settings: Settings) -> GemmaProvider:
    if settings.gemma_provider == "google":
        return GoogleProvider(settings)
    if settings.gemma_provider == "ollama":
        return OllamaProvider(settings)
    if settings.gemma_provider == "fake":
        from engine.ai.fake import FakeProvider

        return FakeProvider(settings)
    raise ProviderError(f"unknown GEMMA_PROVIDER {settings.gemma_provider!r}")
