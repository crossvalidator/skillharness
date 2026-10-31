"""Elision and fetch_result, end to end with a scripted model that looks at the history it is given."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from skillharness.context import image_size, is_cache_marker, paginate, text_head, version_key
from skillharness.contract import FinishedEvent
from skillharness.manifest import Manifest
from skillharness.prompt import system_prompt
from skillharness.skill import Skill, load_skill

from .conftest import CHART, EXAMPLE, collect, request_for, runner_for


def returns(messages: list[ModelMessage], tool: str) -> list[ToolReturnPart]:
    return [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == tool]


def calls(messages: list[ModelMessage], tool: str) -> list[ToolCallPart]:
    return [p for m in messages if isinstance(m, ModelResponse) for p in m.parts if isinstance(p, ToolCallPart) and p.tool_name == tool]


def images_in(part: ToolReturnPart) -> list[BinaryContent]:
    return [c for c in part.content if isinstance(c, BinaryContent)] if isinstance(part.content, list) else []


def stubs_in(part: ToolReturnPart) -> list[str]:
    return [c for c in part.content if isinstance(c, str) and c.startswith("[rounds/")] if isinstance(part.content, list) else []


async def test_older_images_and_versions_become_stubs_and_come_back(chart: Skill, tmp_path: Path):
    source = EXAMPLE.read_text()
    v2 = source.replace("Monthly rain", "Monthly rainfall")
    seen: dict[int, list[ModelMessage]] = {}
    turn = {"n": 0}

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        turn["n"] += 1
        seen[turn["n"]] = copy.deepcopy(messages)  # later turns rewrite parts in place
        script = {
            1: ToolCallPart("write_file", {"source": source, "plan": "line chart"}),
            2: ToolCallPart("render", {}),
            3: ToolCallPart("write_file", {"source": v2}),
            4: ToolCallPart("render", {}),
            5: ToolCallPart("fetch_result", {"key": "rounds/01/chart.png"}),
            6: ToolCallPart("fetch_result", {"key": "rounds/09/nothing.png"}),
        }
        return ModelResponse(parts=[script.get(turn["n"], ToolCallPart("finish", {"summary": "ok"}))])

    runner, store = runner_for(chart, tmp_path, FunctionModel(model))
    events = await collect(runner, request_for(chart, tmp_path, profile="standard"))
    done = events[-1]
    assert isinstance(done, FinishedEvent), getattr(done, "reason", None)

    # Turn 3 (after one render): the only render result is the newest, so its image is still there.
    first = returns(seen[3], "render")
    assert len(first) == 1 and len(images_in(first[0])) == 1 and first[0].metadata == {"round": 1, "ok": True, "command": "render"}

    # Turn 5 (after two renders and two writes): round 1's image is a stub, round 2's is an image;
    # version 1 of the artifact is a stub in the first write_file call, version 2 is verbatim.
    r1, r2 = returns(seen[5], "render")
    assert images_in(r1) == [] and len(images_in(r2)) == 1
    (stub,) = stubs_in(r1)
    assert stub.startswith("[rounds/01/chart.png | render round 1: passed | ") and stub.endswith("| fetch_result('rounds/01/chart.png')]")
    assert "x" in stub.split(" | ")[2]  # WxH from the PNG header
    w1, w2 = calls(seen[5], "write_file")
    assert w1.args_as_dict()["source"].startswith("[write_file chart.py version 1, ") and "replaced by version 2" in w1.args_as_dict()["source"]
    assert w1.args_as_dict()["plan"] == "line chart" and w2.args_as_dict()["source"] == v2

    # Turn 6: the fetched image came back as an image, tagged with the round it was fetched in.
    (fetched,) = returns(seen[6], "fetch_result")
    assert len(images_in(fetched)) == 1 and fetched.metadata == {"round": 2, "ok": True, "command": "fetch_result"}
    assert images_in(fetched)[0].identifier == "rounds/01/chart.png"

    # Turn 7: an unknown key is refused with a message the model can act on.
    refused = returns(seen[7], "fetch_result")[-1]
    assert refused.outcome == "retried" and "is not a stub in your history" in str(refused.content)

    # Nothing older than the turn that aged out is touched: the stub of round 1 is the same string on every later turn.
    assert stubs_in(returns(seen[7], "render")[0]) == [stub]

    # One cache point, at the end of the last request that elision will not rewrite again:
    # turn 3 (write v1 and render 1 still in full) -> on the user prompt;
    # turn 5 (write v2 and render 2 still in full) -> on the request holding render 1's stubbed result.
    def marker_index(messages: list[ModelMessage]) -> int:
        marked = [i for i, m in enumerate(messages) if isinstance(m, ModelRequest) and any(is_cache_marker(p) for p in m.parts)]
        assert len(marked) == 1
        return marked[0]

    assert marker_index(seen[3]) == 0
    holder = seen[5][marker_index(seen[5])]
    assert isinstance(holder, ModelRequest) and returns([holder], "render") == [r1] and is_cache_marker(holder.parts[-1])
    assert marker_index(seen[5]) < seen[5].index(next(m for m in seen[5] if isinstance(m, ModelResponse) and calls([m], "write_file") == [w2]))

    # The run folder has every version, the result counts what happened, the transcript shows the stubs.
    keys = set(await store.keys("t1"))
    assert {version_key(1, "chart.py"), version_key(2, "chart.py"), "rounds/01/chart.png", "rounds/02/chart.png"} <= keys
    assert (await store.get("t1", version_key(1, "chart.py"))).decode() == source
    assert done.context.model_dump() == {"elided_images": 1, "elided_versions": 1, "stored_text_results": 0, "fetches": 1, "compactions": []}
    result = json.loads(await store.get("t1", "result.json"))
    assert result["context"]["elided_images"] == 1
    transcript = json.dumps(json.loads(await store.get("t1", "transcript.json")))
    assert "fetch_result('rounds/01/chart.png')" in transcript and "[write_file chart.py version 1" in transcript


async def test_keep_turns_two_keeps_the_previous_images(chart: Skill, tmp_path: Path):
    manifest = chart.manifest.model_copy(update={"context": chart.manifest.context.model_copy(update={"keep_turns": 2})})
    skill = load_skill(CHART, manifest)
    seen: dict[int, list[ModelMessage]] = {}
    turn = {"n": 0}

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        turn["n"] += 1
        seen[turn["n"]] = copy.deepcopy(messages)  # later turns rewrite parts in place
        script = {1: ToolCallPart("write_file", {"source": EXAMPLE.read_text(), "plan": "p"}), 2: ToolCallPart("render", {}), 3: ToolCallPart("render", {}), 4: ToolCallPart("render", {})}
        return ModelResponse(parts=[script.get(turn["n"], ToolCallPart("finish", {"summary": "ok"}))])

    runner, _ = runner_for(skill, tmp_path, FunctionModel(model))
    events = await collect(runner, request_for(skill, tmp_path, profile="standard"))
    assert isinstance(events[-1], FinishedEvent)
    r1, r2, r3 = returns(seen[5], "render")
    assert images_in(r1) == [] and len(images_in(r2)) == 1 and len(images_in(r3)) == 1
    assert events[-1].context.elided_images == 1


async def test_long_text_result_is_stored_and_paged(chart: Skill, tmp_path: Path):
    manifest = chart.manifest.model_copy(update={"context": chart.manifest.context.model_copy(update={"text_limit_tokens": 10})})
    skill = load_skill(CHART, manifest)
    seen: dict[int, list[ModelMessage]] = {}
    turn = {"n": 0}

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        turn["n"] += 1
        seen[turn["n"]] = copy.deepcopy(messages)  # later turns rewrite parts in place
        script = {
            1: ToolCallPart("write_file", {"source": EXAMPLE.read_text(), "plan": "p"}),
            2: ToolCallPart("render", {}),
            3: ToolCallPart("fetch_result", {"key": "rounds/01/render.txt"}),
        }
        return ModelResponse(parts=[script.get(turn["n"], ToolCallPart("finish", {"summary": "ok"}))])

    runner, store = runner_for(skill, tmp_path, FunctionModel(model))
    events = await collect(runner, request_for(skill, tmp_path))
    done = events[-1]
    assert isinstance(done, FinishedEvent) and done.context.stored_text_results == 1 and done.context.fetches == 1
    (render,) = returns(seen[3], "render")
    head = render.content[0]
    assert "the first 40 shown | fetch_result('rounds/01/render.txt')]" in head
    (fetched,) = returns(seen[4], "fetch_result")
    full = (await store.get("t1", "rounds/01/render.txt")).decode()
    assert fetched.content == f"rounds/01/render.txt:\n{full}" and "Run 1 of 2." in full


def test_helpers():
    assert image_size(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (1158).to_bytes(4, "big") + (2190).to_bytes(4, "big")) == "1158x2190"
    assert image_size(b"not a png") == ""
    assert paginate("") == [""] and len(paginate("x" * 8001)) == 2
    head = text_head("\n".join(str(i) for i in range(50)), "rounds/01/build.txt", 140)
    assert head.startswith("0\n1\n") and "\n39\n[rounds/01/build.txt | 50 lines, 140 chars; the first 40 shown | fetch_result('rounds/01/build.txt')]" in head


def test_manifest_context_section_and_profile_overrides():
    text = (CHART / "harness.yaml").read_text()
    m = Manifest.from_yaml(text + "\ncontext:\n  keep_turns: 1\n  sequences: [[write_file, render]]\n  pin: [write_file.plan]\n")
    m.profiles["standard"].context = {"keep_turns": 2}
    assert m.context_for("quick").keep_turns == 1 and m.context_for("standard").keep_turns == 2
    assert m.context_for("standard").sequences == [["write_file", "render"]]
    assert m.context.budget_tokens is None and m.context.compact_at == 0.75 and m.context.text_limit_tokens == 2000
    with pytest.raises(ValueError, match="does not exist"):
        Manifest.from_yaml(text + "\ncontext:\n  sequences: [[write_file, deploy]]\n")
    with pytest.raises(ValueError, match="tool.field"):
        Manifest.from_yaml(text + "\ncontext:\n  pin: [plan]\n")
    with pytest.raises(ValueError, match="at least 1"):
        Manifest.from_yaml(text + "\ncontext:\n  keep_turns: 0\n")
    with pytest.raises(ValueError, match="bad command name"):
        Manifest.from_yaml(text.replace("  render:", "  fetch_result:"))


def test_prompt_explains_stubs(chart: Skill, tmp_path: Path):
    prompt = system_prompt(chart, request_for(chart, tmp_path))
    assert "`fetch_result(key, part)`" in prompt and "# Your history" in prompt
    assert "fetch_result('rounds/02/render.png')" in prompt and "the newest result stays complete" in prompt
