"""The Pydantic AI runner: one provider-neutral tool loop over the manifest's tools.

The model gets the skill's instructions and the generated tools; `finish` is the run's output, so
calling it ends the run. Hard limits (runs, seconds, dollars, turns) are enforced here, whatever the
model does. Every ending becomes a `finished` or `failed` event and a `result.json`.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent, ModelMessagesTypeAdapter, UsageLimitExceeded, UsageLimits
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from .contract import Event, FailedEvent, FinishedEvent, NoteEvent, RunRequest, UsageTotals
from .models import DEFAULT_MODEL, MODELS, ModelSpec, make_model, make_settings
from .prompt import system_prompt, user_prompt
from .sandbox import CommandBackend
from .skill import Skill
from .store import FileStore
from .tools import BudgetStop, RunState, make_agent

TRANSCRIPT_KEY = "transcript.json"
RESULT_KEY = "result.json"

STOP_REASONS = {
    "time_limit": "Stopped at the time limit",
    "cost_limit": "Stopped at the cost limit",
    "run_cap": "Stopped at the run limit",
    "turn_limit": "Stopped at the turn limit",
    "model": "The model ended the run without calling finish",
}


def describe_failure(error: Exception) -> str:
    """One readable line for the user; the full traceback goes to the log."""
    name = type(error).__name__
    if name == "ContentFilterError":
        return "The model declined this request (its content filter stopped the reply). Try rewording the prompt."
    first = str(error).strip().splitlines()[0] if str(error).strip() else ""
    return f"{name}: {first[:240]}" if first else name


def strip_images(messages: list[ModelMessage]) -> Any:
    """Transcript as JSON-able data with image bytes replaced by their store key."""
    data = ModelMessagesTypeAdapter.dump_python(messages, mode="json")

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if node.get("kind") == "binary" and "data" in node:
                return {k: ("<see identifier>" if k == "data" else v) for k, v in node.items()}
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(data)


def per_request_usage(messages: list[ModelMessage]) -> list[dict[str, int]]:
    rows = []
    for m in messages:
        if isinstance(m, ModelResponse):
            u = m.usage
            rows.append(
                {
                    "input_tokens": u.input_tokens,
                    "cache_read_tokens": u.cache_read_tokens,
                    "cache_write_tokens": u.cache_write_tokens,
                    "output_tokens": u.output_tokens,
                }
            )
    return rows


def totals(spec: ModelSpec, usage: RunUsage) -> UsageTotals:
    return UsageTotals(
        requests=usage.requests,
        input_tokens=usage.input_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=round(
            spec.cost_usd(
                input_tokens=usage.input_tokens,
                cache_read=usage.cache_read_tokens,
                cache_write=usage.cache_write_tokens,
                output_tokens=usage.output_tokens,
            ),
            4,
        ),
    )


class PydanticAIRunner:
    """Runs one request. `model` overrides the provider model (tests pass a FunctionModel)."""

    def __init__(
        self,
        skill: Skill,
        backend: CommandBackend,
        store: FileStore,
        *,
        model: Model | None = None,
        models: dict[str, ModelSpec] = MODELS,
        stream: bool = True,
    ) -> None:
        self.skill = skill
        self.backend = backend
        self.store = store
        self.model_override = model
        self.models = models
        self.stream = stream

    async def run(self, request: RunRequest) -> AsyncIterator[Event]:
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        task = asyncio.create_task(self._drive(request, queue))
        try:
            while (event := await queue.get()) is not None:
                yield event
            await task
        finally:
            if not task.done():
                task.cancel()

    async def _drive(self, request: RunRequest, queue: asyncio.Queue[Event | None]) -> None:
        spec = self.models.get(request.model) or self.models[DEFAULT_MODEL]
        state = RunState(request, self.skill, self.backend, self.store, spec, queue)
        model = self.model_override or make_model(spec)
        agent = make_agent(self.skill, model, system_prompt(self.skill, request))
        settings = None if self.model_override else make_settings(spec, request.budget.effort)
        limits = UsageLimits(request_limit=request.budget.request_limit)

        stopped_by: str = "model"
        failure: str | None = None
        output: BaseModel | None = None
        messages: list[ModelMessage] = []
        usage = RunUsage()
        agent_run = None
        try:
            try:
                async with asyncio.timeout(request.budget.seconds):
                    async with agent.iter(
                        user_prompt(request), deps=state, model_settings=settings, usage_limits=limits
                    ) as agent_run:
                        async for node in agent_run:
                            if self.stream and Agent.is_model_request_node(node):
                                async with node.stream(agent_run.ctx) as request_stream:
                                    async for _ in request_stream:
                                        pass
                            elif Agent.is_call_tools_node(node):
                                for part in node.model_response.parts:
                                    if isinstance(part, TextPart) and part.content.strip():
                                        state.emit(NoteEvent(text=part.content.strip()))
                                state.check_cost(agent_run.usage)
                        if agent_run.result is not None:
                            output = agent_run.result.output
            except TimeoutError:
                stopped_by = "time_limit"
            except BudgetStop as stop:
                stopped_by = stop.kind
            except UsageLimitExceeded:
                stopped_by = "turn_limit"
            except Exception as error:  # noqa: BLE001 - every ending must become an event
                failure = describe_failure(error)
                traceback.print_exc(file=sys.stderr)
            if agent_run is not None:
                messages = agent_run.all_messages()
                usage = agent_run.usage
            terminal = await self._conclude(state, output, stopped_by, failure, messages, usage)
            queue.put_nowait(terminal)
        except Exception as error:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            queue.put_nowait(
                FailedEvent(
                    reason=f"runner error: {type(error).__name__}: {error}",
                    transcript_key=None,
                    rounds=state.rounds,
                    seconds=round(time.monotonic() - state.started, 1),
                    usage=totals(spec, usage),
                )
            )
        finally:
            queue.put_nowait(None)

    async def _conclude(
        self,
        state: RunState,
        output: BaseModel | None,
        stopped_by: str,
        failure: str | None,
        messages: list[ModelMessage],
        usage: RunUsage,
    ) -> FinishedEvent | FailedEvent:
        seconds = round(time.monotonic() - state.started, 1)
        use = totals(state.spec, usage)
        request = state.request
        manifest = state.skill.manifest
        await state.store.put(
            state.run_id,
            TRANSCRIPT_KEY,
            json.dumps({"messages": strip_images(messages), "per_request_usage": per_request_usage(messages)}, indent=1).encode(),
        )
        base: dict[str, Any] = {
            "run_id": request.run_id,
            "prompt": request.prompt,
            "profile": request.profile,
            "vars": request.vars,
            "model": request.model,
            "skill": request.skill.model_dump(),
            "budget": request.budget.model_dump(mode="json"),
        }
        if failure is None and state.deliverable_ready:
            # The last successful outputs of every command go to the top of the folder.
            outputs: dict[str, str] = {}
            for _, (_, files) in state.last_ok.items():
                for oname, (fname, data) in files.items():
                    await state.store.put(state.run_id, fname, data)
                    outputs[oname] = fname
            if output is None:
                last = max((r for r, _ in state.last_ok.values()), default=None)
                tail = f"; this is run {last}, the last that passed." if last else "."
                data = {"summary": f"{STOP_REASONS[stopped_by]}{tail}"}
            else:
                data = output.model_dump(mode="json")
            summary = str(data.pop("summary", "")).strip()
            event: FinishedEvent | FailedEvent = FinishedEvent(
                summary=summary,
                extra=data,
                artifact_key=manifest.artifact.name,
                outputs=outputs,
                transcript_key=TRANSCRIPT_KEY,
                rounds=state.rounds,
                seconds=seconds,
                usage=use,
                stopped_by=stopped_by,  # type: ignore[arg-type]
            )
        else:
            reason = failure or f"nothing passed before the run stopped ({stopped_by})"
            event = FailedEvent(reason=reason, transcript_key=TRANSCRIPT_KEY, rounds=state.rounds, seconds=seconds, usage=use)
        await state.store.put(state.run_id, RESULT_KEY, json.dumps({**base, **event.model_dump(mode="json")}, indent=1).encode())
        return event
