#!/usr/bin/env python3
"""Run an R2-backed archive script with a serialized usage-metadata writer.

Cloudflare R2 can reject concurrent PUTs to the exact same object. The archive
normally writes different bot/media keys in parallel, which is fine, but all
workers periodically checkpoint to `_meta/storage-usage.json`. Without
serialization, several workers can try to PUT that same metadata key at once.

This wrapper patches only the usage checkpoint path:
- actual bot/media uploads remain parallel;
- only one worker may write storage-usage.json at a time;
- non-forced checkpoints simply let the current writer finish;
- forced/final checkpoints wait for the writer;
- newer in-memory usage changes are not overwritten by an older checkpoint.
"""
from __future__ import annotations

import json
import runpy
import sys
import threading
from pathlib import Path

import storage_r2


def install_usage_flush_fix() -> None:
    cls = storage_r2.R2ArchiveStore
    if getattr(cls, "_spicychat_usage_flush_fix_installed", False):
        return

    original_init = cls.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        # Separate from _usage_lock: network I/O must never happen while holding
        # the in-memory counter lock, otherwise upload workers would serialize too.
        self._usage_flush_lock = threading.Lock()

    def patched_flush_usage(self, *, force: bool = False) -> None:
        # Normal worker checkpoints should not queue behind another checkpoint.
        # A final/forced checkpoint must wait so the run finishes with durable
        # metadata.
        lock = getattr(self, "_usage_flush_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._usage_flush_lock = lock

        acquired = lock.acquire(blocking=force)
        if not acquired:
            return

        try:
            # Snapshot under the small in-memory lock, then release it before the
            # R2 network request. Remember how many dirty reservations this
            # snapshot contains so later reservations are not accidentally erased.
            with self._usage_lock:
                if self._usage is None:
                    return

                dirty_in_snapshot = int(self._usage_dirty_writes)
                if not force and dirty_in_snapshot < self.usage_checkpoint_writes:
                    return

                payload = self._normalize_usage(self._usage)
                payload["hardLimitBytes"] = self.hard_limit_bytes
                payload["warningBytes"] = self.warning_bytes
                payload["mediaLimitBytes"] = self.media_limit_bytes

            raw = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")

            # Only this one small metadata object is serialized. Unique bot/media
            # object PUTs continue in parallel.
            self.s3.put_object(
                Bucket=self.bucket,
                Key=self.usage_key,
                Body=raw,
                ContentType="application/json; charset=utf-8",
                CacheControl="no-store",
            )

            # Do not replace self._usage with the older snapshot. Other workers may
            # have reserved additional bytes while the PUT was in flight.
            with self._usage_lock:
                if self._usage is not None:
                    self._usage["hardLimitBytes"] = self.hard_limit_bytes
                    self._usage["warningBytes"] = self.warning_bytes
                    self._usage["mediaLimitBytes"] = self.media_limit_bytes
                self._usage_dirty_writes = max(
                    0,
                    int(self._usage_dirty_writes) - dirty_in_snapshot,
                )
        finally:
            lock.release()

    cls.__init__ = patched_init
    cls.flush_usage = patched_flush_usage
    cls._spicychat_usage_flush_fix_installed = True


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: python scripts/r2_safe_runner.py <script.py> [script args...]",
            file=sys.stderr,
        )
        return 2

    target_name = Path(sys.argv[1]).name
    allowed = {
        "migrate_r2.py",
        "archive_cloud.py",
        "replay_fallback.py",
    }
    if target_name not in allowed:
        print(f"refusing unsupported target: {target_name}", file=sys.stderr)
        return 2

    install_usage_flush_fix()

    target = Path(__file__).resolve().parent / target_name
    if not target.exists():
        print(f"target does not exist: {target}", file=sys.stderr)
        return 2

    script_args = sys.argv[2:]
    sys.argv = [str(target), *script_args]

    try:
        runpy.run_path(str(target), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
