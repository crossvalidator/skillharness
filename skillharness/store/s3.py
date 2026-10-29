"""S3-compatible FileStore through obstore (a small Rust client): Cloudflare R2, AWS S3, MinIO.

Keys are `<prefix>/<run id>/<key>` in one bucket. Operation counts are kept so a run can report what
it cost in requests; the prices default to Cloudflare R2's (class A writes and lists, class B reads,
per million, developers.cloudflare.com/r2/pricing on 2026-10-15).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import obstore
from obstore.store import ObjectStore, S3Store

from . import check_key

R2_CLASS_A_PER_M = 4.50
R2_CLASS_B_PER_M = 0.36


def r2_endpoint(account_id: str) -> str:
    return f"https://{account_id}.r2.cloudflarestorage.com"


def s3_store(bucket: str, *, endpoint: str, access_key_id: str, secret_access_key: str, region: str = "auto") -> S3Store:
    return S3Store(
        bucket,
        endpoint=endpoint,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        region=region,
        virtual_hosted_style_request=False,
    )


@dataclass
class StoreOps:
    """Request counts, so a run can report what it cost in store operations."""

    writes: int = 0  # PutObject, ListObjects
    reads: int = 0  # GetObject, HeadObject
    write_price_per_m: float = R2_CLASS_A_PER_M
    read_price_per_m: float = R2_CLASS_B_PER_M

    @property
    def cost_usd(self) -> float:
        return (self.writes * self.write_price_per_m + self.reads * self.read_price_per_m) / 1_000_000


@dataclass
class S3FileStore:
    """`store` is any obstore store; tests pass a MemoryStore."""

    store: ObjectStore
    prefix: str = "runs"
    ops: StoreOps = field(default_factory=StoreOps)

    def path(self, run_id: str, key: str = "") -> str:
        check_key(run_id, key)
        return f"{self.prefix}/{run_id}/{key}" if key else f"{self.prefix}/{run_id}/"

    async def put(self, run_id: str, key: str, data: bytes) -> None:
        self.ops.writes += 1
        await obstore.put_async(self.store, self.path(run_id, key), data)

    async def get(self, run_id: str, key: str) -> bytes:
        self.ops.reads += 1
        try:
            result = await obstore.get_async(self.store, self.path(run_id, key))
            return bytes(await result.bytes_async())
        except FileNotFoundError:
            raise FileNotFoundError(f"{run_id}/{key}") from None

    async def exists(self, run_id: str, key: str) -> bool:
        self.ops.reads += 1
        try:
            await obstore.head_async(self.store, self.path(run_id, key))
            return True
        except FileNotFoundError:
            return False

    async def keys(self, run_id: str) -> list[str]:
        self.ops.writes += 1
        folder = self.path(run_id)
        out: list[str] = []
        async for chunk in obstore.list(self.store, prefix=folder):
            out += [meta["path"][len(folder):] for meta in chunk]
        return sorted(out)
