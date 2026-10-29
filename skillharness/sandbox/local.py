"""Local subprocess backend: the command runs on this machine, in a fresh workdir each time.

Nothing here is a security boundary; the model's file is executed by whatever the command runs. Use
it on a development machine with a skill you trust. Production runs belong in a sandbox.
"""

from __future__ import annotations

import asyncio
import shlex
import shutil
import sys
import time
from pathlib import Path

from . import CommandCall, CommandOutcome, collect_images, strip_paths


class LocalSubprocessBackend:
    def __init__(self, skill_folder: Path, *, python: str = sys.executable, node: str = "node") -> None:
        self.skill_folder = skill_folder
        self.python = python
        self.node = node

    def argv_for(self, call: CommandCall) -> list[str]:
        command = call.run.format_map(
            {"skill": shlex.quote(str(self.skill_folder)), "artifact": shlex.quote(call.artifact_name), "python": shlex.quote(self.python), "node": shlex.quote(self.node)}
        )
        return [*shlex.split(command), *call.argv]

    async def run(self, workdir: Path, call: CommandCall) -> CommandOutcome:
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True)
        (workdir / call.artifact_name).write_text(call.source, encoding="utf-8")
        argv = self.argv_for(call)
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=workdir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=call.timeout)
        except TimeoutError:
            proc.kill()
            return CommandOutcome(False, f"{call.name} timed out after {call.timeout} s", time.monotonic() - started)
        seconds = time.monotonic() - started
        log = strip_paths(raw.decode("utf-8", "replace"), workdir, self.skill_folder).strip()
        ok = proc.returncode == 0
        outputs: dict[str, tuple[str, bytes]] = {}
        if ok:
            for oname, fname in call.outputs.items():
                p = workdir / fname
                if p.is_file():
                    outputs[oname] = (fname, p.read_bytes())
        return CommandOutcome(ok, log, seconds, outputs, collect_images(workdir, call.images))

    async def close(self) -> None:
        return None
