#!/usr/bin/env python3
"""Run the sharded archive with batched discovery durability checkpoints.

The shard architecture is unchanged: immutable shard data is still written before
its locator journal, and UUID discovery journals are written after that.  The only
change is that normal discovery-page checkpoints may collect a few consecutive
pages before performing those three durable writes.  A crash can therefore replay
at most a small batch of already-fetched pages instead of paying R2 write latency
on every single page.
"""
from __future__ import annotations

import os
import threading
import weakref

import r2_archive_optimized as optimized
import r2_archive_sharded as sharded
from storage_r2 import R2ArchiveStore


_DEFAULT_FLUSH_PAGES = 2


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def install_discovery_flush_batching(*, pages: int = _DEFAULT_FLUSH_PAGES) -> None:
    """Batch repeated page checkpoints while keeping final persistence forced."""
    pages = max(1, min(32, int(pages)))
    if pages <= 1:
        print("Discovery checkpoint batching disabled (flush every page).", flush=True)
        return

    actual_flush = R2ArchiveStore.flush_discovery_journal
    actual_save_order = R2ArchiveStore.save_discovery_order
    counters: "weakref.WeakKeyDictionary[R2ArchiveStore, int]" = weakref.WeakKeyDictionary()
    lock = threading.RLock()

    def batched_flush(self: R2ArchiveStore) -> None:
        with lock:
            count = counters.get(self, 0) + 1
            counters[self] = count
            should_flush = count >= pages
        if not should_flush:
            return
        actual_flush(self)
        with lock:
            counters[self] = 0

    def save_order_with_forced_flush(self: R2ArchiveStore, force: bool = False) -> None:
        # End-of-run compaction must never depend on whether the page counter
        # happens to land exactly on the configured batch size.
        actual_flush(self)
        with lock:
            counters[self] = 0
        actual_save_order(self, force=force)

    R2ArchiveStore.flush_discovery_journal = batched_flush
    R2ArchiveStore.save_discovery_order = save_order_with_forced_flush
    print(
        f"Discovery checkpoints: batching up to {pages} page flushes before R2 persistence; "
        "final save remains forced.",
        flush=True,
    )


def _write_exploration_outcome() -> None:
    """Persist the exact stop reason before the public stats layer trims details."""
    state = optimized._active_state
    if not isinstance(state, dict):
        return

    summary = state.get("lastRunSummary") or {}
    exploration = summary.get("exploration") or {}
    if not isinstance(exploration, dict) or not exploration:
        return

    raw_errors = exploration.get("errors") or []
    if isinstance(raw_errors, str):
        raw_errors = [raw_errors]
    errors = [str(item).strip() for item in raw_errors if str(item).strip()]

    page_budget = int(exploration.get("pageBudget") or 0)
    pages_completed = int(exploration.get("pagesCompleted") or 0)
    time_limited = bool(exploration.get("timeLimited"))

    payload = {
        "at": state.get("lastRunAt"),
        "pageBudget": page_budget,
        "pagesCompleted": pages_completed,
        "timeLimited": time_limited,
        "errors": errors,
        # Fewer pages can also mean a legitimate end-of-pass. Only an explicit
        # error or the discovery time limit makes the saved run partial.
        "partial": bool(time_limited or errors),
    }
    optimized.legacy.write_json_if_changed(
        optimized.legacy.SITE_DATA_DIR / "last-exploration-status.json",
        payload,
    )


def main() -> int:
    sharded.install_discovery_shards()
    install_discovery_flush_batching(
        pages=_env_int("SPICYCHAT_ARCHIVE_DISCOVERY_FLUSH_PAGES", _DEFAULT_FLUSH_PAGES)
    )

    rc = 1
    try:
        rc = optimized.main()
        return rc
    finally:
        # This is a tiny Git-side run marker. R2 still owns the archive data;
        # the marker only lets the website/Discord feed distinguish a complete
        # crawl from one that safely stopped after retries or a time limit.
        try:
            _write_exploration_outcome()
        except Exception as exc:
            print(
                f"WARNING: could not write exploration outcome marker: {exc}",
                flush=True,
            )


if __name__ == "__main__":
    raise SystemExit(main())
