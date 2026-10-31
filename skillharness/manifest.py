"""The manifest: what a skill folder leaves unsaid when there is no shell.

The Agent Skills standard says what a skill contains, not how its scripts run. The manifest declares,
for one skill, the artifact the model writes, the files it may read, the commands it may run (with
their options, outputs and images), the budget profiles, the prompt additions and what the sandbox
needs. The runtime generates the model's tools from it.

It is `harness.yaml` in the skill folder, or a file the consumer supplies for a read-only skill.
"""

from __future__ import annotations

import keyword
import re
import shlex
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from .contract import Budget, Effort

RESERVED_TOOLS = {"read_file", "write_file", "fetch_result", "finish"}
NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class ArtifactSpec(BaseModel):
    name: str  # file name the model writes, e.g. figure.js
    must_contain: str | None = None  # cheap check before a write is accepted
    description: str = ""  # one line for the write_file tool, after the file name


class OptionSpec(BaseModel):
    type: Literal["flag", "string"]
    argv: str  # "--zoom" for a flag, "--state {value}" for a string
    description: str = ""

    def argv_for(self, value: Any) -> list[str]:
        if self.type == "flag":
            return shlex.split(self.argv) if value else []
        return shlex.split(self.argv.replace("{value}", shlex.quote(str(value)))) if value else []


class CommandSpec(BaseModel):
    description: str
    run: str  # template: {skill} {artifact} {python} {node}
    options: dict[str, OptionSpec] = {}
    outputs: dict[str, str] = {}  # output name -> file in the workdir, read back on success
    images: list[str] = []  # globs in the workdir, returned to the model as images in this order
    timeout: int = 180
    counts_toward_budget: bool = True
    required_before_finish: bool = False

    @model_validator(mode="after")
    def _names(self) -> CommandSpec:
        for name in self.options:
            if not NAME.match(name) or keyword.iskeyword(name):
                raise ValueError(f"bad option name {name!r}")
        return self


class ExtraField(BaseModel):
    type: Literal["string", "list"] = "string"
    description: str = ""


class FinishSpec(BaseModel):
    summary: str = "One or two lines on what you made."
    extra: dict[str, ExtraField] = {}

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        # `extra: {skipped: "why"}` means a string field with that description
        if isinstance(data, dict) and isinstance(data.get("extra"), dict):
            data = dict(data)
            data["extra"] = {k: ({"description": v} if isinstance(v, str) else v) for k, v in data["extra"].items()}
        return data


class ContextSpec(BaseModel):
    """What the model's history holds as a run goes on. Every key has a default; see
    docs/context-management.md. Elision is lossless (every stub is one `fetch_result` away);
    compaction is the lossy last resort behind a threshold."""

    budget_tokens: int | None = None  # None = the model's context window; a number overrides it (tests)
    compact_at: float = 0.75  # compact when the previous request's input reached this share of the budget
    tail_share: float = 0.20  # the most recent turns kept verbatim through a compaction, as a share of the budget
    keep_turns: int = 1  # images of command results older than this many turns become stubs
    keep_versions: int = 1  # artifact versions older than this many, in write_file arguments, become stubs
    text_limit_tokens: int = 2000  # a longer text result is stored and shown as a head plus a stub
    compactor_model: str | None = None  # None = the Sonnet-class model of the run's provider
    sequences: list[list[str]] = []  # pairs of tool names a compaction must not split, e.g. [[write_file, build]]
    pin: list[str] = []  # tool.field names kept verbatim through a compaction, e.g. write_file.plan
    summary_extra: dict[str, ExtraField] = {}  # extra fields of the compaction summary

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("summary_extra"), dict):
            data = dict(data)
            data["summary_extra"] = {k: ({"description": v} if isinstance(v, str) else v) for k, v in data["summary_extra"].items()}
        return data

    @model_validator(mode="after")
    def _ranges(self) -> ContextSpec:
        if not 0 < self.compact_at <= 1 or not 0 <= self.tail_share < 1:
            raise ValueError("compact_at must be in (0, 1] and tail_share in [0, 1)")
        if self.keep_turns < 1 or self.keep_versions < 1:
            raise ValueError("keep_turns and keep_versions must be at least 1: the current one is never elided")
        for pair in self.sequences:
            if len(pair) != 2:
                raise ValueError(f"a sequence is a pair of tool names, got {pair!r}")
        for name in self.pin:
            if name.count(".") != 1:
                raise ValueError(f"pin entries are tool.field, got {name!r}")
        return self


class ProfileSpec(BaseModel):
    runs: int
    seconds: int
    dollars: float
    options: list[str] = []
    effort: Effort = "high"
    request_limit: int | None = None  # default: runs * 3 + 4
    minutes: str = ""  # what the model is told the profile takes, e.g. "about 1 minute"
    context: dict[str, Any] = {}  # overrides of the manifest's context keys at this profile

    def budget(self) -> Budget:
        return Budget(
            runs=self.runs,
            seconds=self.seconds,
            dollars=self.dollars,
            options=set(self.options),
            effort=self.effort,
            request_limit=self.request_limit or self.runs * 3 + 4,
        )


class PromptSpec(BaseModel):
    note: str | None = None  # replaces the default harness note; same placeholders
    run: str = ""  # per-run lines, filled from the request's vars and profile


class SandboxSpec(BaseModel):
    """What a sandbox image needs beyond Python: used by the Modal backend, ignored locally."""

    node: str | None = None  # major version, e.g. "20"
    apt: list[str] = []
    pip: list[str] = []
    setup: list[str] = []  # shell commands run while the image is built (fonts, wrappers)
    env: dict[str, str] = {}
    cpu: float = 1.0
    memory_mib: int = 1024


class Manifest(BaseModel):
    artifact: ArtifactSpec
    readable: list[str] = []
    commands: dict[str, CommandSpec] = {}
    finish: FinishSpec = Field(default_factory=FinishSpec)
    profiles: dict[str, ProfileSpec]
    prompt: PromptSpec = Field(default_factory=PromptSpec)
    sandbox: SandboxSpec = Field(default_factory=SandboxSpec)
    context: ContextSpec = Field(default_factory=ContextSpec)

    @model_validator(mode="after")
    def _check(self) -> Manifest:
        if not self.profiles:
            raise ValueError("at least one profile is needed")
        for name in self.commands:
            if not NAME.match(name) or keyword.iskeyword(name) or name in RESERVED_TOOLS:
                raise ValueError(f"bad command name {name!r}")
        known = {opt for cmd in self.commands.values() for opt in cmd.options}
        for pname, profile in self.profiles.items():
            unknown = sorted(set(profile.options) - known)
            if unknown:
                raise ValueError(f"profile {pname!r} allows options no command has: {', '.join(unknown)}")
            self.context_for(pname)  # validates the overrides
        tools = RESERVED_TOOLS | set(self.commands)
        for pair in self.context.sequences:
            if not set(pair) <= tools:
                raise ValueError(f"context.sequences names a tool that does not exist: {pair!r}")
        for name in self.context.pin:
            if name.split(".")[0] not in tools:
                raise ValueError(f"context.pin names a tool that does not exist: {name!r}")
        for pattern in self.readable:
            if pattern.startswith("/") or ".." in pattern:
                raise ValueError(f"readable pattern must stay inside the skill folder: {pattern!r}")
        return self

    @property
    def required_commands(self) -> list[str]:
        return [name for name, cmd in self.commands.items() if cmd.required_before_finish]

    def budget(self, profile: str) -> Budget:
        if profile not in self.profiles:
            raise KeyError(f"unknown profile {profile!r}; profiles: {', '.join(self.profiles)}")
        return self.profiles[profile].budget()

    def context_for(self, profile: str) -> ContextSpec:
        """The context rules at a profile: the manifest's, with the profile's overrides applied."""
        overrides = self.profiles[profile].context
        if not overrides:
            return self.context
        return ContextSpec.model_validate({**self.context.model_dump(), **overrides})

    @classmethod
    def from_yaml(cls, text: str) -> Manifest:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("the manifest must be a mapping")
        return cls.model_validate(data)


def load_manifest(path: Path) -> Manifest:
    return Manifest.from_yaml(path.read_text(encoding="utf-8"))
