#!/usr/bin/env python3
"""Speed up R2 cleanup without changing its bookkeeping semantics.

The normal store HEADs every key before a batch delete so byte/object counters
stay exact. Large discovery runs can leave hundreds of small recovery journals,
and doing those HEAD requests one-by-one adds avoidable tail time. This runtime
patch performs only those independent HEAD requests concurrently, then keeps the
same single S3 delete_objects call and the same usage-accounting update.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from r2_runtime_accel import flush_bot_writes
from storage_r2 import R2ArchiveStore


_INSTALLED = False
_HEAD_WORKERS = 16
_ORIGINAL_DELETE_KEYS = R2ArchiveStore._delete_keys


def _parallel_delete_keys(self: R2ArchiveStore, keys: list[str]) -> None:
    if not keys:
        return

    # Never delete journals/receipts while an earlier bot write is still queued.
    flush_bot_writes(self)

    for i in range(0, len(keys), 1000):
        chunk = keys[i:i + 1000]
        if not chunk:
            continue

        workers = max(1, min(_HEAD_WORKERS, len(chunk)))
        if workers == 1:
            sizes = [(key, self._head_size(key)) for key in chunk]
        else:
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="r2-cleanup-head",
            ) as pool:
                size_values = list(pool.map(self._head_size, chunk))
            sizes = list(zip(chunk, size_values))

        self.s3.delete_objects(
            Bucket=self.bucket,
            Delete={"Objects": [{"Key": key} for key in chunk], "Quiet": True},
        )

        with self._usage_lock:
            if self._usage is not None:
                for key, size in sizes:
                    self._usage["totalBytes"] = max(
                        0, int(self._usage.get("totalBytes") or 0) - int(size)
                    )
                    if key.startswith(self.media_prefix.rstrip("/") + "/"):
                        self._usage["mediaBytes"] = max(
                            0, int(self._usage.get("mediaBytes") or 0) - int(size)
                        )
                    if size:
                        self._usage["objects"] = max(
                            0, int(self._usage.get("objects") or 0) - 1
                        )
                self._usage_dirty_writes += 1
        self.flush_usage()

        print(
            f"  R2 cleanup: retired {len(chunk):,} objects with "
            f"{workers} concurrent size checks.",
            flush=True,
        )


def install_cleanup_acceleration(*, head_workers: int = 16) -> None:
    global _INSTALLED, _HEAD_WORKERS
    if _INSTALLED:
        return
    _HEAD_WORKERS = max(1, min(32, int(head_workers)))
    R2ArchiveStore._delete_keys = _parallel_delete_keys
    _INSTALLED = True
    print(
        f"Archive cleanup acceleration: {_HEAD_WORKERS} concurrent R2 HEAD checks.",
        flush=True,
    )
