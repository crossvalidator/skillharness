"""The model's tools, generated from the manifest: read_file, write_file, one tool per command, finish.

No shell and no network from the model's side, ever. Budgets are enforced here, whatever the model does.
"""

from __future__ import annotations

import asyncio
import mimetypes
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model
from pydantic_ai import Agent, BinaryContent, ModelRetry, RunContext, Tool, ToolOutput, ToolReturn
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from .context import CHARS_PER_TOKEN, FETCH_TOOL, elide, paginate, text_head, version_key
from .contract import (
    ArtifactWrittenEvent,
    CommandFinishedEvent,
    CommandStartedEvent,
    ContextTotals,
    Event,
    PlanEvent,
    RunRequest,
)
from .manifest import ContextSpec
from .models import ModelSpec
from .sandbox import CommandBackend, CommandCall
from .skill import Skill
from .store import FileStore

StopKind = Literal["run_cap", "cost_limit"]


class BudgetStop(Exception):
    """Raised inside the loop when a hard limit is hit. Ends the run; not shown to the model."""

    def __init__(self, kind: StopKind) -> None:
        super().__init__(kind)
        self.kind: StopKind = kind


@dataclass
class RunState:
    """Per-run state the tools share. Passed to the agent as `deps`."""

    request: RunRequest
    skill: Skill
    backend: CommandBackend
    store: FileStore
    spec: ModelSpec
    events: asyncio.Queue[Event | None]
    context_spec: ContextSpec = field(default_factory=ContextSpec)
    started: float = field(default_factory=time.monotonic)
    source: str | None = None
    runs: int = 0  # command runs that counted toward the budget
    rounds: int = 0  # every command run, for folder numbering
    refused: int = 0
    plan_seen: bool = False
    last_ok: dict[str, tuple[int, dict[str, tuple[str, bytes]]]] = field(default_factory=dict)  # command -> (round, outputs)
    writes: int = 0  # accepted write_file calls, for writes/NN numbering
    write_ids: dict[str, int] = field(default_factory=dict)  # tool call id -> version number
    fetchable: set[str] = field(default_factory=set)  # store keys shown as stubs, the only ones fetch_result accepts
    context: ContextTotals = field(default_factory=ContextTotals)

    def emit(self, event: Event) -> None:
        self.events.put_nowait(event)

    @property
    def run_id(self) -> str:
        return self.request.run_id

    @property
    def deliverable_ready(self) -> bool:
        """Something to hand over: the artifact exists and every required command has succeeded once."""
        return self.source is not None and all(c in self.last_ok for c in self.skill.manifest.required_commands)

    def cost_of(self, usage: RunUsage) -> float:
        return self.spec.cost_usd(
            input_tokens=usage.input_tokens,
            cache_read=usage.cache_read_tokens,
            cache_write=usage.cache_write_tokens,
            output_tokens=usage.output_tokens,
        )

    def check_cost(self, usage: RunUsage) -> None:
        if self.cost_of(usage) > self.request.budget.dollars:
            raise BudgetStop("cost_limit")


# --- the fixed tools ---


async def read_file(ctx: RunContext[RunState], path: str) -> str:
    """Read one file from the skill folder: a reference the skill tells you to read, or an example to
    open. `path` is relative to the skill folder, e.g. `references/levels.md`."""
    skill = ctx.deps.skill
    path = path.strip().lstrip("./")
    try:
        return skill.read(path)
    except FileNotFoundError:
        raise ModelRetry(f"No such file {path!r}. You can read: {', '.join(skill.readable) or 'nothing'}") from None


async def write_file(ctx: RunContext[RunState], source: str, plan: str | None = None) -> str:
    """Write the whole artifact file. Every call replaces the file, so send the complete file each
    time. On the first call, put your one-line plan in `plan`."""
    state = ctx.deps
    artifact = state.skill.manifest.artifact
    if artifact.must_contain and artifact.must_contain not in source:
        raise ModelRetry(f"The file must contain {artifact.must_contain!r}.")
    if plan and not state.plan_seen:
        state.plan_seen = True
        state.emit(PlanEvent(text=plan.strip()))
    state.source = source
    data = source.encode("utf-8")
    state.writes += 1
    state.write_ids[ctx.tool_call_id or f"write-{state.writes}"] = state.writes
    await state.store.put(state.run_id, artifact.name, data)
    await state.store.put(state.run_id, version_key(state.writes, artifact.name), data)
    state.emit(ArtifactWrittenEvent(key=artifact.name, bytes=len(data)))
    state.check_cost(ctx.usage)
    commands = ", ".join(f"`{c}`" for c in state.skill.manifest.commands)
    next_step = f" Call {commands} to check it." if commands else ""
    return f"Wrote {artifact.name} ({len(data)} bytes).{next_step}"


# --- one tool per command ---


async def run_command(ctx: RunContext[RunState], name: str, asked: dict[str, Any]) -> ToolReturn | str:
    state = ctx.deps
    manifest = state.skill.manifest
    cmd = manifest.commands[name]
    budget = state.request.budget
    state.check_cost(ctx.usage)
    if state.source is None:
        raise ModelRetry(f"Nothing to run yet: call write_file first.")
    if cmd.counts_toward_budget and state.runs >= budget.runs:
        state.refused += 1
        if state.refused >= 2:
            raise BudgetStop("run_cap")
        return (
            f"{name} refused: the budget of {budget.runs} runs for this run is used up. "
            "Call finish now with the last result that passed."
        )

    wanted = {opt: value for opt, value in asked.items() if value}
    ignored = sorted(opt for opt in wanted if opt not in budget.options)
    argv: list[str] = []
    for opt, value in wanted.items():
        if opt in budget.options:
            argv += cmd.options[opt].argv_for(value)
    if cmd.counts_toward_budget:
        state.runs += 1
    state.rounds += 1
    number = state.rounds
    state.emit(CommandStartedEvent(command=name, round=number, max_rounds=budget.runs, argv=argv))

    prefix = f"rounds/{number:02d}"
    await state.store.put(state.run_id, f"{prefix}/{manifest.artifact.name}", state.source.encode("utf-8"))
    outcome = await state.backend.run(
        state.request.workdir,
        CommandCall(
            name=name,
            run=cmd.run,
            argv=argv,
            artifact_name=manifest.artifact.name,
            source=state.source,
            outputs=cmd.outputs,
            images=cmd.images,
            timeout=cmd.timeout,
        ),
    )

    image_keys: list[str] = []
    for fname, png in outcome.images.items():
        key = f"{prefix}/{fname}"
        await state.store.put(state.run_id, key, png)
        image_keys.append(key)
    output_keys: dict[str, str] = {}
    if outcome.ok:
        for oname, (fname, data) in outcome.outputs.items():
            key = f"{prefix}/{fname}"
            await state.store.put(state.run_id, key, data)
            output_keys[oname] = key
        state.last_ok[name] = (number, outcome.outputs)

    log = outcome.log
    if ignored:
        log += f"\n(ignored at this profile: {', '.join(ignored)})"
    if cmd.counts_toward_budget:
        log += f"\nRun {state.runs} of {budget.runs}."
    log = log.strip()
    state.emit(
        CommandFinishedEvent(
            command=name, round=number, ok=outcome.ok, log=log, outputs=output_keys, images=image_keys, seconds=round(outcome.seconds, 1)
        )
    )

    # A long log is stored in full and the model sees its head plus a stub (docs/context-management.md).
    if len(log) > state.context_spec.text_limit_tokens * CHARS_PER_TOKEN:
        text_key = f"{prefix}/{name}.txt"
        await state.store.put(state.run_id, text_key, log.encode("utf-8"))
        state.fetchable.add(text_key)
        state.context.stored_text_results += 1
        log = text_head(log, text_key, len(log))

    metadata = {"round": number, "ok": outcome.ok, "command": name}
    if not outcome.images:
        return ToolReturn(return_value=log, metadata=metadata)
    content: list[str | BinaryContent] = [log]
    for (fname, png), key in zip(outcome.images.items(), image_keys, strict=True):
        content.append(f"{fname}:")
        content.append(BinaryContent(data=png, media_type="image/png", identifier=key))
    return ToolReturn(return_value=content, metadata=metadata)


async def elide_history(ctx: RunContext[RunState], messages: list[ModelMessage]) -> list[ModelMessage]:
    """The Pydantic AI history hook: runs before every model request (see context.py)."""
    return elide(messages, ctx.deps)


# --- fetch_result ---


async def fetch_result(ctx: RunContext[RunState], key: str, part: int = 0) -> ToolReturn | str:
    """Bring back a stored result that your history shows as a stub `[... | fetch_result('key')]`.
    An image comes back as the image; a text file comes back one page at a time (`part`, from 0)."""
    state = ctx.deps
    key = key.strip().strip("'\"")
    if key not in state.fetchable:
        raise ModelRetry(f"{key!r} is not a stub in your history. Only keys shown as {FETCH_TOOL}('...') can be fetched.")
    data = await state.store.get(state.run_id, key)
    state.context.fetches += 1
    media_type, _ = mimetypes.guess_type(key)
    if media_type and media_type.startswith("image/"):
        content: list[str | BinaryContent] = [f"{key}:", BinaryContent(data=data, media_type=media_type, identifier=key)]
        return ToolReturn(return_value=content, metadata={"round": state.rounds, "ok": True, "command": FETCH_TOOL})
    pages = paginate(data.decode("utf-8", "replace"))
    if not 0 <= part < len(pages):
        raise ModelRetry(f"{key} has {len(pages)} part(s), numbered 0 to {len(pages) - 1}.")
    header = f"{key}, part {part + 1} of {len(pages)}:" if len(pages) > 1 else f"{key}:"
    return f"{header}\n{pages[part]}"


def command_tool(skill: Skill, name: str) -> Tool[RunState]:
    cmd = skill.manifest.commands[name]
    properties: dict[str, Any] = {}
    for opt, spec in cmd.options.items():
        properties[opt] = {"type": "boolean" if spec.type == "flag" else "string"}
        if spec.description:
            properties[opt]["description"] = spec.description
    schema = {"type": "object", "properties": properties, "additionalProperties": False}

    async def call(ctx: RunContext[RunState], **kwargs: Any) -> ToolReturn | str:
        return await run_command(ctx, name, kwargs)

    description = cmd.description.strip()
    if cmd.images:
        description += " The log and the images come back in the result; look at every image."
    else:
        description += " The log comes back in the result."
    return Tool.from_schema(call, name=name, description=description, json_schema=schema, takes_ctx=True)


# --- finish ---


def finish_model(skill: Skill) -> type[BaseModel]:
    spec = skill.manifest.finish
    fields: dict[str, Any] = {"summary": (str, Field(description=spec.summary))}
    for name, extra in spec.extra.items():
        if extra.type == "list":
            fields[name] = (list[str], Field(default_factory=list, description=extra.description))
        else:
            fields[name] = (str | None, Field(default=None, description=extra.description))
    model = create_model("Finish", **fields)
    model.__doc__ = "Hand over and end the run."
    return model


def make_agent(skill: Skill, model: Model, instructions: str) -> Agent[RunState, BaseModel]:
    finish = finish_model(skill)
    agent: Agent[RunState, BaseModel] = Agent(
        model,
        deps_type=RunState,
        instructions=instructions,
        output_type=[ToolOutput(finish, name="finish", description="Hand over and end the run.")],
        tools=[
            Tool(read_file, takes_ctx=True),
            Tool(write_file, takes_ctx=True),
            *(command_tool(skill, c) for c in skill.manifest.commands),
            Tool(fetch_result, takes_ctx=True),
        ],
        capabilities=[ProcessHistory(elide_history)],
        retries=2,
    )

    @agent.output_validator
    async def ready(ctx: RunContext[RunState], output: BaseModel) -> BaseModel:
        if ctx.partial_output or ctx.deps.deliverable_ready:
            return output
        required = ctx.deps.skill.manifest.required_commands
        if ctx.deps.source is None:
            raise ModelRetry("Nothing has been written yet. Call write_file before finishing.")
        raise ModelRetry(f"No {' or '.join(required)} has succeeded yet. Fix the file and run it before finishing.")

    return agent
