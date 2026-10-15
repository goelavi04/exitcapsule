"""Deterministic fake Gemma provider.

Why: tests and CI have no network secrets. The fake returns canned responses
registered per task so the full pipeline runs offline. It reports a model ID
that is clearly marked fake so it can never be mistaken for real Gemma output.
"""

from __future__ import annotations

from collections.abc import Callable

from engine.ai.provider import Completion, GemmaProvider
from engine.config import Settings

Responder = Callable[[str, str], str]

FAKE_MODEL_ID = "gemma-4-fake-for-tests"


class FakeProvider(GemmaProvider):
    name = "fake"

    def __init__(self, settings: Settings, responder: Responder | None = None) -> None:
        super().__init__(settings)
        self.responder: Responder = responder or (lambda system, prompt: "{}")
        self.calls: list[tuple[str, str]] = []

    def list_models(self) -> list[str]:
        return [FAKE_MODEL_ID]

    async def _generate(
        self, model_id: str, system: str, prompt: str, temperature: float, json_mode: bool
    ) -> Completion:
        self.calls.append((system, prompt))
        text = self.responder(system, prompt)
        return Completion(
            text=text,
            model_id=model_id,
            input_tokens=len(prompt) // 4,
            output_tokens=len(text) // 4,
        )
