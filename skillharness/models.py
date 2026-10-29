"""Models, prices and provider settings.

Prices are per million tokens in US dollars from the providers' public price pages (2026-10-15).
They live here, not in a library, so a cost figure never changes under a run. A consumer may pass its
own table to the runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

Provider = Literal["anthropic", "openai"]


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: Provider
    label: str
    input_per_m: float
    cache_read_per_m: float
    cache_write_per_m: float
    output_per_m: float

    def cost_usd(self, *, input_tokens: int, cache_read: int, cache_write: int, output_tokens: int) -> float:
        """Cost of a usage total. `input_tokens` is the full prompt count, cache tokens included."""
        plain = max(0, input_tokens - cache_read - cache_write)
        return (
            plain * self.input_per_m
            + cache_read * self.cache_read_per_m
            + cache_write * self.cache_write_per_m
            + output_tokens * self.output_per_m
        ) / 1_000_000


MODELS: dict[str, ModelSpec] = {
    # Anthropic: cache reads at $0.20, cache writes (5 minute) at 1.25x input.
    "claude-opus-5-5": ModelSpec("claude-opus-5-5", "anthropic", "Claude Opus 5.5", 4.0, 0.20, 5.0, 20.0),
    "claude-sonnet-5-5": ModelSpec("claude-sonnet-5-5", "anthropic", "Claude Sonnet 5.5", 2.0, 0.20, 2.5, 10.0),
    # OpenAI: cached input is discounted; tokens reported as cache writes are billed as plain input.
    "gpt-6.1-sol": ModelSpec("gpt-6.1-sol", "openai", "GPT-6.1 Sol", 2.0, 0.10, 2.0, 10.0),
    "gpt-6-astra": ModelSpec("gpt-6-astra", "openai", "GPT-6 Astra", 10.0, 1.0, 10.0, 50.0),
}
DEFAULT_MODEL = "claude-opus-5-5"


def make_model(spec: ModelSpec) -> Model:
    if spec.provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel

        return AnthropicModel(spec.id)
    from pydantic_ai.models.openai import OpenAIResponsesModel

    return OpenAIResponsesModel(spec.id)


def make_settings(spec: ModelSpec, effort: str) -> ModelSettings:
    if spec.provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModelSettings

        return AnthropicModelSettings(
            max_tokens=32_000,
            timeout=600,
            anthropic_thinking={"type": "adaptive"},
            anthropic_effort=effort,  # type: ignore[typeddict-item]
            anthropic_cache=True,
            anthropic_cache_instructions=True,
            anthropic_cache_tool_definitions=True,
        )
    from pydantic_ai.models.openai import OpenAIResponsesModelSettings

    return OpenAIResponsesModelSettings(
        max_tokens=32_000,
        timeout=600,
        openai_reasoning_effort=effort,  # type: ignore[typeddict-item]
    )
