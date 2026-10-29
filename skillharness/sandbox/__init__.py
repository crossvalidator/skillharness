"""CommandBackend: run one of the skill's declared commands on the artifact and return what it produced.

The backend owns where the skill folder and the workdir live (this machine, or a sandbox far away),
so it resolves the command template: `{skill}` the skill folder, `{artifact}` the artifact file name,
`{python}` and `{node}` the interpreters it has.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class CommandCall:
    name: str
    run: str  # the manifest's template
    argv: list[str]  # option arguments, already resolved
    artifact_name: str
    source: str
    outputs: dict[str, str]  # output name -> file name in the workdir
    images: list[str]  # globs, in the order the model should see them
    timeout: int


@dataclass
class CommandOutcome:
    ok: bool
    log: str
    seconds: float
    outputs: dict[str, tuple[str, bytes]] = field(default_factory=dict)  # output name -> (file name, bytes); only when ok
    images: dict[str, bytes] = field(default_factory=dict)  # file name -> PNG bytes, in the manifest's order


class CommandBackend(Protocol):
    async def run(self, workdir: Path, call: CommandCall) -> CommandOutcome: ...

    async def close(self) -> None: ...


def strip_paths(log: str, *roots: Path | str) -> str:
    """Absolute paths in a log mean nothing to the model; show file names only."""
    for root in roots:
        log = log.replace(str(root).rstrip("/") + "/", "")
    return log


def collect_images(workdir: Path, patterns: list[str]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for pattern in patterns:
        for p in sorted(workdir.glob(pattern)):
            name = str(p.relative_to(workdir))
            if p.is_file() and name not in out:
                out[name] = p.read_bytes()
    return out
