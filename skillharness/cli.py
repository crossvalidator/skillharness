"""Run a skill once on this machine and print the events as they happen.

    skillharness run examples/chart "Monthly rainfall in Lisbon, 2025" --profile quick --var audience=students
    skillharness show examples/chart --profile quick      # the system prompt and tools, no model call
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

from .contract import Event, FailedEvent, FinishedEvent, RunRequest
from .env import key_for, load_dotenv
from .models import DEFAULT_MODEL, MODELS
from .prompt import system_prompt
from .runner import PydanticAIRunner
from .sandbox.local import LocalSubprocessBackend
from .skill import Skill, load_skill
from .store.local import LocalDiskFileStore


def describe(event: Event) -> str:
    match event:
        case FinishedEvent():
            u = event.usage
            extra = "".join(f"\n    {k}: {v}" for k, v in event.extra.items() if v)
            return (
                f"finished ({event.stopped_by}) in {event.seconds:.0f} s: {event.rounds} rounds, "
                f"{u.requests} requests, {u.input_tokens} in ({u.cache_read_tokens} cached) / {u.output_tokens} out, "
                f"${u.cost_usd:.3f}\n    {event.summary}{extra}"
            )
        case FailedEvent():
            return f"FAILED after {event.seconds:.0f} s, {event.rounds} rounds, ${event.usage.cost_usd:.3f}: {event.reason}"
        case _:
            data = event.model_dump(mode="json")
            data.pop("type")
            if "log" in data:
                data["log"] = data["log"].replace("\n", " | ")
            return f"{event.type} {json.dumps(data)}"


def make_request(skill: Skill, prompt: str, profile: str, model: str, vars: dict[str, str], workdir: Path, run_id: str | None = None) -> RunRequest:
    return RunRequest(
        run_id=run_id or secrets.token_urlsafe(9),
        prompt=prompt,
        skill=skill.ref,
        profile=profile,
        model=model,
        vars=vars,
        budget=skill.manifest.budget(profile),
        workdir=workdir,
    )


async def run_one(runner: PydanticAIRunner, request: RunRequest, *, quiet: bool = False) -> Event:
    started = time.monotonic()
    last: Event | None = None
    async for event in runner.run(request):
        last = event
        if not quiet:
            print(f"[{time.monotonic() - started:6.1f}s] {describe(event)}", flush=True)
    assert last is not None
    return last


def parse_vars(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--var needs key=value, got {item!r}")
        k, _, v = item.partition("=")
        out[k.strip()] = v.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skillharness", description="Run an Agent Skill folder as a bounded, recorded run.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("skill", type=Path, help="the skill folder (with SKILL.md)")
        p.add_argument("--manifest", type=Path, default=None, help="harness.yaml to use instead of the one in the folder")
        p.add_argument("--profile", default=None, help="budget profile from the manifest (default: its first)")
        p.add_argument("--var", action="append", default=[], help="key=value filled into the manifest's run lines")

    run = sub.add_parser("run", help="run the skill once")
    common(run)
    run.add_argument("prompt")
    run.add_argument("--model", choices=sorted(MODELS), default=DEFAULT_MODEL)
    run.add_argument("--runs-dir", type=Path, default=Path("runs"), help="where run folders go (default: ./runs)")
    run.add_argument("--open", action="store_true", help="open the first output file when done (macOS)")

    show = sub.add_parser("show", help="print the system prompt and the tools; no model call")
    common(show)

    args = parser.parse_args(argv)
    load_dotenv()
    skill = load_skill(args.skill, args.manifest)
    profile = args.profile or next(iter(skill.manifest.profiles))
    vars = parse_vars(args.var)

    if args.cmd == "show":
        request = make_request(skill, "<prompt>", profile, DEFAULT_MODEL, vars, Path("/tmp"), run_id="show")
        print(system_prompt(skill, request))
        return 0

    provider = MODELS[args.model].provider
    if not key_for(provider):
        print(f"missing API key for {provider}: set {provider.upper()}_API_KEY (or put it in .env)", file=sys.stderr)
        return 2
    store = LocalDiskFileStore(args.runs_dir)
    backend = LocalSubprocessBackend(skill.folder)
    runner = PydanticAIRunner(skill, backend, store)
    request = make_request(skill, args.prompt, profile, args.model, vars, args.runs_dir / "_work")
    print(f"run {request.run_id}  skill {skill.name}@{skill.git_hash[:12]}  model {args.model}  profile {profile}")
    last = asyncio.run(run_one(runner, request))
    folder = store.path(request.run_id)
    print(f"files: {folder}")
    if isinstance(last, FinishedEvent):
        if args.open and last.outputs:
            subprocess.run(["open", str(folder / next(iter(last.outputs.values())))], check=False)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
