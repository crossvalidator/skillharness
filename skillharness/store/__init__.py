"""FileStore: one folder of files per run, keyed by run id then file key."""

from __future__ import annotations

from typing import Protocol


class FileStore(Protocol):
    async def put(self, run_id: str, key: str, data: bytes) -> None: ...

    async def get(self, run_id: str, key: str) -> bytes: ...

    async def exists(self, run_id: str, key: str) -> bool: ...

    async def keys(self, run_id: str) -> list[str]: ...


def check_key(run_id: str, key: str) -> None:
    if not run_id or "/" in run_id or ".." in run_id or ".." in key or key.startswith("/"):
        raise ValueError("bad run id or key")
