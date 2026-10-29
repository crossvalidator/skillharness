"""Local-disk FileStore: `<root>/<run id>/<key>`."""

from __future__ import annotations

from pathlib import Path

from . import check_key


class LocalDiskFileStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path(self, run_id: str, key: str = "") -> Path:
        check_key(run_id, key)
        return (self.root / run_id / key).resolve()

    async def put(self, run_id: str, key: str, data: bytes) -> None:
        p = self.path(run_id, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    async def get(self, run_id: str, key: str) -> bytes:
        return self.path(run_id, key).read_bytes()

    async def exists(self, run_id: str, key: str) -> bool:
        return self.path(run_id, key).is_file()

    async def keys(self, run_id: str) -> list[str]:
        folder = self.path(run_id)
        if not folder.is_dir():
            return []
        return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
