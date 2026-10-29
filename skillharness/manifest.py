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

RESERVED_TOOLS = {"read_file", "write_file", "finish"}
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


class ProfileSpec(BaseModel):
    runs: int
    seconds: int
    dollars: float
    options: list[str] = []
    effort: Effort = "high"
    request_limit: int | None = None  # default: runs * 3 + 4
    minutes: str = ""  # what the model is told the profile takes, e.g. "about 1 minute"

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
    folders: list[str] = []  # folders of the skill to copy beyond SKILL.md and the readable files
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

    @classmethod
    def from_yaml(cls, text: str) -> Manifest:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("the manifest must be a mapping")
        return cls.model_validate(data)


def load_manifest(path: Path) -> Manifest:
    return Manifest.from_yaml(path.read_text(encoding="utf-8"))
