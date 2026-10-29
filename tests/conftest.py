from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from skillharness.contract import RunRequest
from skillharness.runner import PydanticAIRunner
from skillharness.sandbox.local import LocalSubprocessBackend
from skillharness.skill import Skill, load_skill
from skillharness.store.local import LocalDiskFileStore

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "examples" / "chart"
EXAMPLE = CHART / "examples" / "monthly-rain.py"


def scripted(steps: list[list[ToolCallPart | TextPart]], finish: dict | None = None) -> FunctionModel:
    """A FunctionModel that replays tool calls turn by turn and then calls finish."""
    turn = {"n": 0}
    final = finish or {"summary": "done", "caveats": ["illustrative values"]}

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        i = turn["n"]
        turn["n"] += 1
        if i < len(steps):
            return ModelResponse(parts=steps[i])
        return ModelResponse(parts=[ToolCallPart("finish", final)])

    return FunctionModel(model)


@pytest.fixture
def chart() -> Skill:
    return load_skill(CHART)


def request_for(skill: Skill, tmp_path: Path, profile: str = "quick", prompt: str = "Monthly rain in Lisbon", **vars: str) -> RunRequest:
    return RunRequest(
        run_id="t1",
        prompt=prompt,
        skill=skill.ref,
        profile=profile,
        model="claude-opus-5-5",
        vars=vars or {"audience": "a report"},
        budget=skill.manifest.budget(profile),
        workdir=tmp_path / "work",
    )


def runner_for(skill: Skill, tmp_path: Path, model: FunctionModel) -> tuple[PydanticAIRunner, LocalDiskFileStore]:
    store = LocalDiskFileStore(tmp_path / "runs")
    backend = LocalSubprocessBackend(skill.folder)
    return PydanticAIRunner(skill, backend, store, model=model, stream=False), store


async def collect(runner: PydanticAIRunner, request: RunRequest):
    return [event async for event in runner.run(request)]
