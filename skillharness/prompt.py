"""System prompt: the skill's instructions verbatim, a note mapping its shell workflow onto the tools,
then the per-run lines from the manifest filled with the request's variables."""

from __future__ import annotations

from .contract import RunRequest
from .skill import Skill

DEFAULT_NOTE = """
# How you work here

You are running inside a service, not a terminal, and there is no user to talk to during the run. The skill above was written for a shell; here you have exactly these tools:

{tools}

Budget for this run: at most {runs} command runs and {minutes} minutes of wall time. The service enforces both. When a command is refused because the budget is used up, call `finish` with the last result that passed. Do no web research; everything you need is in the skill and its files.

Work in tool calls, not prose: the plan goes in `write_file`, the hand-over goes in `finish`.
""".strip()


class _Safe(dict):
    """format_map helper: unknown placeholders stay as written."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def tool_lines(skill: Skill) -> str:
    m = skill.manifest
    readable = ", ".join(f"`{p}`" for p in skill.readable) or "none"
    lines = [
        f"- `read_file(path)`: read one file from the skill folder. Allowed paths: {readable}.",
        f"- `write_file(source, plan)`: write the whole `{m.artifact.name}`{(' ' + m.artifact.description.strip()) if m.artifact.description else ''}. "
        "Every call replaces the file, so send the complete file each time. Give your one-line plan in `plan` on the first call.",
    ]
    for name, cmd in m.commands.items():
        params = ", ".join(cmd.options) or ""
        back = ["the log"]
        if cmd.images:
            back.append("the images")
        if cmd.outputs:
            back.append("the names of the output files")
        returns = " " + ", ".join(back[:-1]) + (" and " if len(back) > 1 else "") + back[-1] + " come back in the result."
        returns = returns[0] + returns[1].upper() + returns[2:]
        lines.append(f"- `{name}({params})`: {cmd.description.strip()}{returns} Options not allowed at this profile are ignored and the log says so.")
    lines.append("- `fetch_result(key, part)`: bring back a result your history shows as a stub (see below).")
    extra = "".join(f", {k}" for k in m.finish.extra)
    lines.append(f"- `finish(summary{extra})`: hand over. This ends the run; the service delivers the result, so never try to open it yourself.")
    return "\n".join(lines)


def history_note(skill: Skill, profile: str) -> str:
    """How the service keeps the history small; stable text, so it is part of the cached prefix."""
    m = skill.manifest
    spec = m.context_for(profile) if profile in m.profiles else m.context
    command = next(iter(m.commands), "run")
    kept = "the newest result stays complete" if spec.keep_turns == 1 else f"the newest {spec.keep_turns} results stay complete"
    example = f"[rounds/02/{command}.png | {command} round 2: passed | 1200x800 | fetch_result('rounds/02/{command}.png')]"
    return (
        "# Your history\n\n"
        f"To keep this run small, the service replaces older material in your history with one-line stubs: the images of a "
        f"command result once a newer one exists ({kept}), earlier versions of `{m.artifact.name}` inside your "
        f"own `write_file` calls, and the tail of a long text result. A stub looks like `{example}`. Nothing is lost: "
        "`fetch_result(key)` brings any of them back, so compare against an older result only when you need to."
    )


def placeholders(skill: Skill, request: RunRequest) -> dict[str, str]:
    m = skill.manifest
    profile = m.profiles.get(request.profile)
    return {
        "tools": tool_lines(skill),
        "readable": ", ".join(f"`{p}`" for p in skill.readable) or "none",
        "artifact": m.artifact.name,
        "commands": ", ".join(f"`{c}`" for c in m.commands) or "none",
        "runs": str(request.budget.runs),
        "minutes": str(request.budget.seconds // 60),
        "profile": request.profile,
        "profile_minutes": profile.minutes if profile else "",
        **request.vars,
    }


def system_prompt(skill: Skill, request: RunRequest) -> str:
    values = _Safe(placeholders(skill, request))
    note = (skill.manifest.prompt.note or DEFAULT_NOTE).strip().format_map(values)
    run_lines = skill.manifest.prompt.run.strip().format_map(values)
    parts = [skill.body, note, history_note(skill, request.profile)]
    if run_lines:
        parts.append("# This run\n\n" + run_lines)
    return "\n\n".join(parts) + "\n"


def user_prompt(request: RunRequest) -> str:
    return request.prompt
