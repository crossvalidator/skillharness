"""The Modal sandbox backend driven against a fake sandbox: no Modal account needed."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from skillharness.sandbox import CommandCall
from skillharness.sandbox.modal import SANDBOX_USER, SKILL_DIR, WORK_DIR, ModalSandboxBackend, image_name, sandbox_image
from skillharness.skill import Skill


class Aio:
    """Wraps a coroutine function so `.aio(...)` works like Modal's synchronicity wrappers."""

    def __init__(self, fn):
        self.aio = fn


class FakeStream:
    def __init__(self, text: str) -> None:
        self.read = Aio(self._read)
        self._text = text

    async def _read(self) -> str:
        return self._text


class FakeProcess:
    def __init__(self, out: str, err: str = "", code: int = 0) -> None:
        self.stdout, self.stderr, self._code = FakeStream(out), FakeStream(err), code
        self.wait = Aio(self._wait)

    async def _wait(self) -> int:
        return self._code


class FakeSandbox:
    """Remembers what was exec'd and written; pretends the render produced a PNG, an SVG and a stray file."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.files: dict[str, bytes] = {}
        self.commands: list[tuple[str, ...]] = []
        self.terminated = False
        self.exec = Aio(self._exec)
        self.terminate = Aio(self._terminate)
        self.filesystem = SimpleNamespace(
            write_text=Aio(self._write_text), read_bytes=Aio(self._read_bytes), list_files=Aio(self._list_files)
        )

    async def _exec(self, *args: str, **kwargs):
        self.commands.append(args)
        if args[0] == "sh":
            self.files = {}
            return FakeProcess("")
        assert args[:4] == ("runuser", "-u", SANDBOX_USER, "--") and kwargs["workdir"] == WORK_DIR
        self.files[f"{WORK_DIR}/chart-extra.png"] = b"png-extra"
        self.files[f"{WORK_DIR}/chart.png"] = b"png"
        if self.ok:
            self.files[f"{WORK_DIR}/chart.svg"] = b"<svg>"
            return FakeProcess(f"title: ok\nsaved {WORK_DIR}/chart.png and chart.svg\n")
        return FakeProcess("title: (none)", "FAILED: no title\n", 1)

    async def _terminate(self) -> None:
        self.terminated = True

    async def _write_text(self, data: str, path: str) -> None:
        self.files[path] = data.encode()

    async def _read_bytes(self, path: str) -> bytes:
        return self.files[path]

    async def _list_files(self, path: str):
        return [SimpleNamespace(name=p.rsplit("/", 1)[1]) for p in self.files if p.startswith(path + "/")]


def call_for(chart: Skill, argv: list[str]) -> CommandCall:
    cmd = chart.manifest.commands["render"]
    return CommandCall(
        name="render", run=cmd.run, argv=argv, artifact_name="chart.py", source="def draw(ax): ...",
        outputs=cmd.outputs, images=["chart.png", "chart-*.png"], timeout=cmd.timeout,
    )


async def test_sandbox_runs_unprivileged_and_reads_back(chart: Skill):
    sb = FakeSandbox()
    backend = ModalSandboxBackend(sb)
    outcome = await backend.run(Path("/ignored"), call_for(chart, ["--wide"]))
    assert outcome.ok
    assert outcome.outputs == {"png": ("chart.png", b"png"), "svg": ("chart.svg", b"<svg>")}
    assert list(outcome.images) == ["chart.png", "chart-extra.png"]  # the manifest's order, not alphabetical
    assert sb.files[f"{WORK_DIR}/chart.py"] == b"def draw(ax): ..."
    command = sb.commands[-1][-1]
    assert command == f"cd {WORK_DIR} && exec python3 {SKILL_DIR}/scripts/render.py chart.py --wide"
    assert WORK_DIR + "/" not in outcome.log and "saved chart.png" in outcome.log
    await backend.close()
    assert sb.terminated and backend.sandbox_seconds >= 0 and backend.runs == 1


async def test_sandbox_failure_keeps_log_and_images(chart: Skill):
    outcome = await ModalSandboxBackend(FakeSandbox(ok=False)).run(Path("/x"), call_for(chart, []))
    assert not outcome.ok and outcome.outputs == {} and list(outcome.images) == ["chart.png", "chart-extra.png"]
    assert "FAILED: no title" in outcome.log and "title: (none)" in outcome.log


def test_image_name_and_definition(chart: Skill, tmp_path: Path):
    pytest.importorskip("modal")
    assert image_name(chart).startswith("chart-sandbox:") and len(image_name(chart)) == len("chart-sandbox:") + 12
    (tmp_path / "SKILL.md").write_text("x")
    image = sandbox_image(chart, tmp_path)  # lazy: nothing is built or uploaded here
    assert image is not None
