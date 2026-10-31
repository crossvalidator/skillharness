"""Context management: what the model's history holds as a run goes on.

Elision runs before every model request and loses nothing: the images of older command results and
the earlier versions of the artifact in the model's own `write_file` calls become one-line stubs that
name the store key, and `fetch_result` brings any of them back. Only the turn that has just aged out
is rewritten, never an older message, and one explicit cache point sits at the end of the last message
elision will not touch again, so the provider reads the whole stable prefix from its cache on every
request. (Without that point the cache read stalls: the provider's automatic breakpoint sits at the
end of the previous request, inside the turn that is rewritten next.) Compaction, the lossy step
behind a threshold, is described in docs/context-management.md and comes after this. The rules and
their defaults are the manifest's `context:` section.
"""

from __future__ import annotations

import json
import struct
from typing import TYPE_CHECKING, Any

from pydantic_ai import BinaryContent
from pydantic_ai.messages import CachePoint, ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart

if TYPE_CHECKING:
    from .tools import RunState

WRITES_PREFIX = "writes"  # every accepted artifact version: writes/NN/<artifact>
FETCH_TOOL = "fetch_result"
CHARS_PER_TOKEN = 4  # the estimate used for text limits; no tokenizer in the library
PAGE_CHARS = 2000 * CHARS_PER_TOKEN  # one page of a fetched text result
HEAD_LINES = 40  # what stays in view of a long text result


def version_key(number: int, artifact: str) -> str:
    return f"{WRITES_PREFIX}/{number:02d}/{artifact}"


def image_size(data: bytes) -> str:
    """`WxH` from a PNG header, or an empty string."""
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        return f"{width}x{height}"
    return ""


def image_stub(key: str, command: str, round_number: int, ok: bool, size: str) -> str:
    status = "fetched" if command == FETCH_TOOL else ("passed" if ok else "failed")
    size_part = f" | {size}" if size else ""
    return f"[{key} | {command} round {round_number}: {status}{size_part} | {FETCH_TOOL}('{key}')]"


def version_stub(artifact: str, number: int, chars: int) -> str:
    key = version_key(number, artifact)
    return f"[write_file {artifact} version {number}, {chars:,} chars | replaced by version {number + 1} | {FETCH_TOOL}('{key}')]"


def rejected_stub(artifact: str, chars: int) -> str:
    return f"[write_file {artifact}: {chars:,} chars, not accepted]"


def text_head(text: str, key: str, chars: int) -> str:
    """The first lines of a long text result plus the stub for the rest."""
    lines = text.splitlines()
    head = "\n".join(lines[:HEAD_LINES])
    return f"{head}\n[{key} | {len(lines)} lines, {chars:,} chars; the first {HEAD_LINES} shown | {FETCH_TOOL}('{key}')]"


def paginate(text: str) -> list[str]:
    pages = [text[i : i + PAGE_CHARS] for i in range(0, len(text), PAGE_CHARS)]
    return pages or [""]


def _round_of(part: ToolReturnPart) -> tuple[int, str, bool] | None:
    meta = part.metadata
    if isinstance(meta, dict) and isinstance(meta.get("round"), int):
        return meta["round"], str(meta.get("command", part.tool_name)), bool(meta.get("ok", True))
    return None


def elide(messages: list[ModelMessage], state: RunState) -> list[ModelMessage]:
    """Apply the elision rules in place and return the same list. Idempotent: a stub is a string."""
    spec = state.context_spec
    artifact = state.skill.manifest.artifact.name

    # Images: in command results older than the last `keep_turns` rounds.
    returns: list[tuple[ToolReturnPart, int, str, bool]] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and (info := _round_of(part)) is not None:
                    returns.append((part, *info))
    if returns:
        latest = max(number for _, number, _, _ in returns)
        for part, number, command, ok in returns:
            if number > latest - spec.keep_turns or not isinstance(part.content, list):
                continue
            content: list[Any] = []
            for item in part.content:
                if isinstance(item, BinaryContent):
                    key = item.identifier or f"rounds/{number:02d}/image"
                    content.append(image_stub(key, command, number, ok, image_size(item.data)))
                    state.fetchable.add(key)
                    state.context.elided_images += 1
                else:
                    content.append(item)
            part.content = content

    # Artifact versions: in write_file arguments, all but the last `keep_versions` accepted ones.
    calls: list[ToolCallPart] = [
        part
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_name == "write_file"
    ]
    if calls and state.write_ids:
        latest_version = max(state.write_ids.values())
        for part in calls:
            args = part.args_as_dict()
            source = args.get("source")
            if not isinstance(source, str) or source.startswith("[write_file "):
                continue
            number = state.write_ids.get(part.tool_call_id)
            if number is None:
                stub = rejected_stub(artifact, len(source))
            elif number <= latest_version - spec.keep_versions:
                stub = version_stub(artifact, number, len(source))
                state.fetchable.add(version_key(number, artifact))
            else:
                continue
            new_args = {**args, "source": stub}
            part.args = new_args if isinstance(part.args, dict) else json.dumps(new_args)
            state.context.elided_versions += 1

    place_cache_point(messages)
    return messages


def is_cache_marker(part: Any) -> bool:
    return isinstance(part, UserPromptPart) and isinstance(part.content, list) and bool(part.content) and all(isinstance(c, CachePoint) for c in part.content)


def _may_still_change(message: ModelMessage) -> bool:
    """Whether elision may rewrite this message on a later request: it still holds an image in a
    command result, or a full artifact source in a write_file call."""
    if isinstance(message, ModelRequest):
        return any(
            isinstance(p, ToolReturnPart)
            and _round_of(p) is not None
            and isinstance(p.content, list)
            and any(isinstance(c, BinaryContent) for c in p.content)
            for p in message.parts
        )
    return any(
        isinstance(p, ToolCallPart)
        and p.tool_name == "write_file"
        and isinstance(p.args_as_dict().get("source"), str)
        and not p.args_as_dict()["source"].startswith("[write_file ")
        for p in message.parts
    )


def place_cache_point(messages: list[ModelMessage]) -> None:
    """One explicit cache point at the end of the last request before anything elision may still
    rewrite. Moving a marker changes no content, so the prefix it covers is read from cache."""
    requests = [m for m in messages if isinstance(m, ModelRequest)]
    for m in requests:
        m.parts = [p for p in m.parts if not is_cache_marker(p)]
    first_unstable = next((i for i, m in enumerate(messages) if _may_still_change(m)), None)
    if first_unstable is None:
        return  # nothing will be rewritten; the provider's automatic breakpoint at the end is enough
    anchor = next((m for m in reversed(messages[:first_unstable]) if isinstance(m, ModelRequest)), None)
    if anchor is not None:
        anchor.parts = [*anchor.parts, UserPromptPart(content=[CachePoint()])]
