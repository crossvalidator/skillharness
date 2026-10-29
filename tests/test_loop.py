"""The loop end to end with a scripted model: no API key, the real local render command."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from skillharness.contract import CommandFinishedEvent, FailedEvent, FinishedEvent, PlanEvent
from skillharness.manifest import Manifest
from skillharness.prompt import system_prompt
from skillharness.skill import Skill, load_skill

from .conftest import CHART, EXAMPLE, collect, request_for, runner_for, scripted


def test_skill_loads_with_readable_files_and_tools(chart: Skill, tmp_path: Path):
    assert chart.name == "chart" and chart.readable == ("examples/monthly-rain.py", "references/style.md")
    assert chart.body.startswith("# Chart") and "name: chart" not in chart.body
    assert chart.read("references/style.md").startswith("# Style")
    prompt = system_prompt(chart, request_for(chart, tmp_path, audience="students"))
    assert "`render(wide, dark)`" in prompt and "`finish(summary, caveats)`" in prompt
    assert "at most 2 command runs and 2 minutes" in prompt
    assert "Audience: students." in prompt and "Profile: quick (about 1 minute)." in prompt


async def test_happy_path(chart: Skill, tmp_path: Path):
    source = EXAMPLE.read_text()
    model = scripted(
        [
            [ToolCallPart("read_file", {"path": "references/style.md"})],
            [ToolCallPart("write_file", {"source": source, "plan": "Line chart, November highlighted."})],
            [ToolCallPart("render", {"dark": True})],
        ]
    )
    runner, store = runner_for(chart, tmp_path, model)
    events = await collect(runner, request_for(chart, tmp_path))
    assert [e.type for e in events] == ["plan", "artifact_written", "command_started", "command_finished", "finished"]
    assert isinstance(events[0], PlanEvent) and "November" in events[0].text
    done_cmd = events[3]
    assert isinstance(done_cmd, CommandFinishedEvent) and done_cmd.ok and done_cmd.command == "render"
    assert done_cmd.images == ["rounds/01/chart.png"] and done_cmd.outputs == {"png": "rounds/01/chart.png", "svg": "rounds/01/chart.svg"}
    assert "ignored at this profile: dark" in done_cmd.log and "Run 1 of 2." in done_cmd.log
    done = events[-1]
    assert isinstance(done, FinishedEvent) and done.stopped_by == "model" and done.rounds == 1
    assert done.artifact_key == "chart.py" and done.outputs == {"png": "chart.png", "svg": "chart.svg"}
    assert done.extra == {"caveats": ["illustrative values"]}
    keys = await store.keys("t1")
    assert {"chart.py", "chart.png", "chart.svg", "transcript.json", "result.json", "rounds/01/chart.py", "rounds/01/chart.png"} <= set(keys)
    result = json.loads(await store.get("t1", "result.json"))
    assert result["skill"]["git_hash"] == chart.git_hash and result["type"] == "finished" and result["vars"] == {"audience": "a report"}
    transcript = json.dumps(json.loads(await store.get("t1", "transcript.json")))
    assert "<see identifier>" in transcript and "rounds/01/chart.png" in transcript


async def test_command_failure_then_cap(chart: Skill, tmp_path: Path):
    broken = "def draw(ax):\n    ax.plot([1, 2], [3, 4])\n"  # no title, no labels: the checks fail
    model = scripted(
        [
            [ToolCallPart("write_file", {"source": broken, "plan": "p"})],
            [ToolCallPart("render", {})],
            [ToolCallPart("render", {})],
            [ToolCallPart("render", {})],  # refused once
            [ToolCallPart("render", {})],  # refused twice -> run_cap
        ]
    )
    runner, _ = runner_for(chart, tmp_path, model)
    events = await collect(runner, request_for(chart, tmp_path))
    runs = [e for e in events if isinstance(e, CommandFinishedEvent)]
    assert len(runs) == 2 and not runs[0].ok and "FAILED: no title" in runs[0].log
    assert runs[0].images == ["rounds/01/chart.png"] and runs[0].outputs == {}  # the image is saved even when checks fail
    assert isinstance(events[-1], FailedEvent) and "run_cap" in events[-1].reason


async def test_write_rejected_without_draw(chart: Skill, tmp_path: Path):
    model = scripted(
        [
            [ToolCallPart("write_file", {"source": "x = 1\n"})],  # retried: must contain def draw(
            [ToolCallPart("write_file", {"source": EXAMPLE.read_text(), "plan": "p"})],
            [ToolCallPart("render", {})],
        ]
    )
    runner, _ = runner_for(chart, tmp_path, model)
    events = await collect(runner, request_for(chart, tmp_path))
    assert isinstance(events[-1], FinishedEvent)


async def test_finish_before_render_is_retried(chart: Skill, tmp_path: Path):
    source = EXAMPLE.read_text()
    turn = {"n": 0}

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        turn["n"] += 1
        if turn["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("finish", {"summary": "too early"})])
        last = messages[-1].parts[-1]
        if turn["n"] == 2:
            assert isinstance(last, ToolReturnPart) or "write_file" in str(last)
            return ModelResponse(parts=[ToolCallPart("write_file", {"source": source, "plan": "p"})])
        if turn["n"] == 3:
            return ModelResponse(parts=[ToolCallPart("render", {})])
        return ModelResponse(parts=[ToolCallPart("finish", {"summary": "ok"})])

    runner, _ = runner_for(chart, tmp_path, FunctionModel(model))
    events = await collect(runner, request_for(chart, tmp_path))
    assert isinstance(events[-1], FinishedEvent) and events[-1].summary == "ok"


async def test_skill_without_commands(tmp_path: Path):
    folder = tmp_path / "haiku"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: haiku\ndescription: Write a haiku.\n---\n# Haiku\n\nWrite one haiku into haiku.md.\n")
    manifest = Manifest.from_yaml("artifact: {name: haiku.md}\nprofiles: {quick: {runs: 0, seconds: 30, dollars: 0.1}}\n")
    skill = load_skill(folder, manifest)
    assert skill.git_hash in ("unknown", "uncommitted") or len(skill.git_hash) >= 40
    model = scripted([[ToolCallPart("write_file", {"source": "old pond\nfrog jumps in\nsound of water\n", "plan": "basho"})]], {"summary": "a haiku"})
    runner, store = runner_for(skill, tmp_path, model)
    events = await collect(runner, request_for(skill, tmp_path, prompt="a frog"))
    assert [e.type for e in events] == ["plan", "artifact_written", "finished"]
    done = events[-1]
    assert isinstance(done, FinishedEvent) and done.outputs == {} and done.artifact_key == "haiku.md"
    assert (await store.get("t1", "haiku.md")).startswith(b"old pond")
    assert "`finish(summary)`" in system_prompt(skill, request_for(skill, tmp_path))


def test_example_skill_git_hash_is_a_commit_or_marked(chart: Skill):
    assert chart.folder == CHART.resolve()
    assert chart.git_hash in ("unknown", "uncommitted") or chart.git_hash.endswith("-dirty") or len(chart.git_hash) == 40
