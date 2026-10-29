from __future__ import annotations

from pathlib import Path

import pytest
from obstore.store import MemoryStore

from skillharness.store.local import LocalDiskFileStore
from skillharness.store.s3 import S3FileStore


async def test_local_store_round_trip(tmp_path: Path):
    store = LocalDiskFileStore(tmp_path)
    await store.put("abc", "rounds/01/chart.png", b"png")
    await store.put("abc", "chart.py", b"def draw")
    assert await store.exists("abc", "chart.py") and not await store.exists("abc", "nope")
    assert await store.get("abc", "rounds/01/chart.png") == b"png"
    assert await store.keys("abc") == ["chart.py", "rounds/01/chart.png"]
    assert await store.keys("other") == []
    with pytest.raises(ValueError):
        store.path("a/b", "x")
    with pytest.raises(ValueError):
        store.path("abc", "../x")


async def test_s3_store_round_trip_and_ops():
    store = S3FileStore(store=MemoryStore())
    await store.put("abc", "rounds/01/chart.png", b"png")
    await store.put("abc", "chart.py", b"src")
    assert await store.exists("abc", "chart.py")
    assert not await store.exists("abc", "nope.txt")
    assert await store.get("abc", "rounds/01/chart.png") == b"png"
    assert await store.keys("abc") == ["chart.py", "rounds/01/chart.png"]
    assert await store.keys("other") == []
    with pytest.raises(FileNotFoundError):
        await store.get("abc", "missing")
    assert store.ops.writes == 4 and store.ops.reads == 4  # 2 puts + 2 lists; 2 heads + 2 gets
    assert 0 < store.ops.cost_usd < 1e-4
