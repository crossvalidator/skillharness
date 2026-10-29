"""The run contract: what goes in, the events that come out while it runs, and the result.

A consumer talks to a `Runner`, never to a model. The Pydantic AI runner is the implementation here;
a runner on another agent framework would translate its stream into these same events.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field

Effort = Literal["low", "medium", "high", "xhigh", "max"]
StopReason = Literal["model", "run_cap", "time_limit", "cost_limit", "turn_limit"]


class Budget(BaseModel):
    """Hard limits the harness enforces, whatever the model does."""

    runs: int  # command runs that count toward the budget
    seconds: int  # wall time for the whole run
    dollars: float  # model cost cap
    options: set[str] = set()  # command options the model may use; others are ignored and logged
    effort: Effort = "high"  # reasoning effort, the same word on both providers
    request_limit: int  # backstop on model turns


class SkillRef(BaseModel):
    name: str
    git_hash: str


class RunRequest(BaseModel):
    run_id: str
    prompt: str
    skill: SkillRef
    profile: str
    model: str
    vars: dict[str, str] = {}  # filled into the manifest's per-run prompt lines
    budget: Budget
    workdir: Path


class UsageTotals(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class InfraTotals(BaseModel):
    """What the run cost beyond the model. Filled in by a consumer that measures it; absent locally."""

    sandbox_seconds: float = 0.0
    function_seconds: float = 0.0
    compute_cost_usd: float = 0.0
    store_writes: int = 0
    store_reads: int = 0
    store_cost_usd: float = 0.0


# --- events ---


class PlanEvent(BaseModel):
    """The plan the model gave with its first write."""

    type: Literal["plan"] = "plan"
    text: str


class ArtifactWrittenEvent(BaseModel):
    type: Literal["artifact_written"] = "artifact_written"
    key: str
    bytes: int


class CommandStartedEvent(BaseModel):
    type: Literal["command_started"] = "command_started"
    command: str
    round: int
    max_rounds: int
    argv: list[str]


class CommandFinishedEvent(BaseModel):
    type: Literal["command_finished"] = "command_finished"
    command: str
    round: int
    ok: bool
    log: str
    outputs: dict[str, str] = {}  # output name -> store key, when the command succeeded
    images: list[str] = []  # store keys, in the order the model saw them
    seconds: float


class NoteEvent(BaseModel):
    """Text the model wrote between tool calls. Progress for a run page, not part of the result."""

    type: Literal["note"] = "note"
    text: str


class FinishedEvent(BaseModel):
    type: Literal["finished"] = "finished"
    summary: str
    extra: dict[str, Any] = {}  # the manifest's extra finish fields
    artifact_key: str
    outputs: dict[str, str] = {}  # output name -> store key of the last successful result
    transcript_key: str
    rounds: int
    seconds: float
    usage: UsageTotals
    stopped_by: StopReason
    infra: InfraTotals | None = None


class FailedEvent(BaseModel):
    type: Literal["failed"] = "failed"
    reason: str
    transcript_key: str | None
    rounds: int
    seconds: float
    usage: UsageTotals
    infra: InfraTotals | None = None


Event = Annotated[
    PlanEvent
    | ArtifactWrittenEvent
    | CommandStartedEvent
    | CommandFinishedEvent
    | NoteEvent
    | FinishedEvent
    | FailedEvent,
    Field(discriminator="type"),
]
TerminalEvent = FinishedEvent | FailedEvent


class Runner(Protocol):
    def run(self, request: RunRequest) -> AsyncIterator[Event]:
        """Yield events as they happen. The last event is always `finished` or `failed`."""
        ...
