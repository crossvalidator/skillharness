"""Modal sandbox backend: one sandbox per run, network blocked, no secrets, the skill baked in read-only.

`sandbox_image` turns the manifest's `sandbox` section into a Modal image: Debian with the apt
packages, Node if asked, the pip packages, the setup commands, an unprivileged user, and the skill
folder (a pinned snapshot) at /opt/skill owned by root and not writable. Each command run writes the
artifact into /work, runs the command there as that user, and reads outputs and images back. The
container (gVisor, no network, thrown away after the run) is the isolation boundary.

Needs the `modal` extra. Image globs are matched against the top level of /work only.
"""

from __future__ import annotations

import fnmatch
import shlex
import time
from pathlib import Path
from typing import Any

from ..skill import Skill
from . import CommandCall, CommandOutcome, strip_paths

SKILL_DIR = "/opt/skill"  # root-owned, mode a-w: the run user can read, not write
WORK_DIR = "/work"  # owned by the run user
SANDBOX_USER = "runner"
HASH_ENV = "SKILLHARNESS_SKILL_HASH"


def image_name(skill: Skill) -> str:
    """Name to publish the image under: `<skill>-sandbox:<hash>`. Old names stay for old runs."""
    return f"{skill.name}-sandbox:{skill.git_hash.removesuffix('-dirty')[:12]}"


def sandbox_image(skill: Skill, snapshot: Path, *, python_version: str = "3.13") -> Any:
    """A Modal image for the skill's commands. `snapshot` is the skill folder at its committed hash
    (see `skill.export_at_hash`), so uncommitted edits never reach the image."""
    import modal

    spec = skill.manifest.sandbox
    image = modal.Image.debian_slim(python_version=python_version).apt_install("curl", "ca-certificates", *spec.apt)
    if spec.node:
        image = image.run_commands(
            f"curl -fsSL https://deb.nodesource.com/setup_{spec.node}.x | bash - && apt-get install -y nodejs && node --version"
        )
    if spec.pip:
        image = image.uv_pip_install(*spec.pip)
    for command in spec.setup:
        image = image.run_commands(command)
    image = (
        image.run_commands(
            f"useradd --create-home --shell /bin/sh {SANDBOX_USER} && mkdir -p {WORK_DIR} && chown {SANDBOX_USER}:{SANDBOX_USER} {WORK_DIR}"
        )
        .add_local_dir(snapshot, SKILL_DIR, copy=True)
        .run_commands(f"chown -R root:root {SKILL_DIR} && chmod -R a-w,a+rX {SKILL_DIR}")
    )
    return image.env({HASH_ENV: skill.git_hash.removesuffix("-dirty"), **spec.env})


class ModalSandboxBackend:
    """Create with `await ModalSandboxBackend.open(...)`; `await close()` when the run ends."""

    def __init__(self, sandbox: Any, *, python: str = "python3", node: str = "node") -> None:
        self.sandbox = sandbox
        self.python = python
        self.node = node
        self.sandbox_seconds = 0.0
        self.runs = 0
        self._opened = time.monotonic()

    @classmethod
    async def open(cls, *, image: Any, timeout: int, app: Any = None, cpu: float = 1.0, memory_mib: int = 1024) -> ModalSandboxBackend:
        import modal

        sandbox = await modal.Sandbox.create.aio(
            app=app,
            image=image,
            timeout=timeout,
            workdir=WORK_DIR,
            cpu=cpu,
            memory=memory_mib,
            block_network=True,  # no outbound traffic: the model's code can reach nothing
            secrets=[],  # provider and store keys stay with the caller
        )
        return cls(sandbox)

    async def close(self) -> None:
        self.sandbox_seconds = round(time.monotonic() - self._opened, 1)
        try:
            await self.sandbox.terminate.aio()
        except Exception:  # noqa: BLE001 - the sandbox times out on its own
            pass

    def command_for(self, call: CommandCall) -> str:
        values = {"skill": SKILL_DIR, "artifact": shlex.quote(call.artifact_name), "python": self.python, "node": self.node}
        return " ".join([call.run.format_map(values), *map(shlex.quote, call.argv)]).strip()

    async def run(self, workdir: Path, call: CommandCall) -> CommandOutcome:
        """`workdir` is ignored: the sandbox always works in /work."""
        fs = self.sandbox.filesystem
        self.runs += 1
        started = time.monotonic()
        clean = await self.sandbox.exec.aio("sh", "-c", f"rm -rf {WORK_DIR}/* {WORK_DIR}/.[!.]* 2>/dev/null; true", timeout=30)
        await clean.wait.aio()
        await fs.write_text.aio(call.source, f"{WORK_DIR}/{call.artifact_name}")
        # runuser drops root; the run user owns /work and can only read the skill.
        proc = await self.sandbox.exec.aio(
            "runuser", "-u", SANDBOX_USER, "--", "sh", "-c", f"cd {WORK_DIR} && exec {self.command_for(call)}",
            timeout=call.timeout, workdir=WORK_DIR, env={"HOME": f"/home/{SANDBOX_USER}"},
        )
        out, err = await proc.stdout.read.aio(), await proc.stderr.read.aio()
        code = await proc.wait.aio()
        seconds = round(time.monotonic() - started, 1)
        log = strip_paths(out + err, WORK_DIR, SKILL_DIR).strip()
        if code == -1 or (code != 0 and not log):
            log = f"{call.name} timed out after {call.timeout} s or the sandbox was lost"
        ok = code == 0
        present = sorted(entry.name for entry in await fs.list_files.aio(WORK_DIR))
        outputs: dict[str, tuple[str, bytes]] = {}
        if ok:
            for oname, fname in call.outputs.items():
                if fname in present:
                    outputs[oname] = (fname, await fs.read_bytes.aio(f"{WORK_DIR}/{fname}"))
        images: dict[str, bytes] = {}
        for pattern in call.images:
            for name in present:
                if name not in images and fnmatch.fnmatch(name, pattern):
                    images[name] = await fs.read_bytes.aio(f"{WORK_DIR}/{name}")
        return CommandOutcome(ok, log, seconds, outputs, images)
