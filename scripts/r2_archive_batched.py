#!/usr/bin/env python3
"""Run the sharded archive with batched discovery durability checkpoints.

The shard architecture is unchanged: immutable shard data is still written before
its locator journal, and UUID discovery journals are written after that. The only
change is that normal discovery-page checkpoints may collect a few consecutive
pages before performing those three durable writes. A crash can therefore replay
at most a small batch of already-fetched pages instead of paying R2 write latency
on every single page.
"""
from __future__ import annotations

import os
import threading
import weakref
from typing import Any

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


def _stamp_run_finished() -> None:
    """Replace the run-start marker with the real completion timestamp.

    archive.py intentionally uses one observation timestamp while a crawl is in
    progress. Historically that same value leaked into stats history as the run's
    finishedAt, which made long manual crawls look as if they finished when they
    actually started. Correct the current successful run after optimized.main()
    has finished, before the public feed/growth steps read the compact stats.
    """
    state = optimized._active_state
    if not isinstance(state, dict):
        return

    started_at = str(state.get("lastRunAt") or "")
    if not started_at:
        return

    finished_at = optimized.legacy.utc_now()
    state["lastRunAt"] = finished_at

    stats_path = optimized.legacy.SITE_DATA_DIR / "stats.json"
    stats = optimized.legacy.read_json(stats_path, {})
    if isinstance(stats, dict):
        changed = False
        for row in reversed(stats.get("runs") or []):
            if row.get("kind") != "archive-run":
                continue
            if str(row.get("at") or "") != started_at:
                continue
            row["at"] = finished_at
            changed = True
            break
        if changed:
            stats["generatedAt"] = finished_at
            stats.get("runs", []).sort(key=lambda row: str(row.get("at") or ""))
            optimized.legacy.write_json_if_changed(stats_path, stats)

    store = optimized._active_store
    if store is not None:
        try:
            key = store.key(store.meta_prefix, "stats-history.json")
            history = store.get_json(key, None)
            history_changed = False
            if isinstance(history, dict):
                for row in reversed(history.get("runs") or []):
                    if row.get("kind") != "archive-run":
                        continue
                    if str(row.get("at") or "") != started_at:
                        continue
                    row["at"] = finished_at
                    history_changed = True
                    break
                if history_changed:
                    history.get("runs", []).sort(
                        key=lambda row: str(row.get("at") or "")
                    )
                    store.put_json(key, history)

            # cloud.run() saved state before optimized.main() recorded the compact
            # stats row. Persist the corrected lastRunAt too so the next run does
            # not reload the old start-time marker from R2.
            store.save_state(state)
            if history_changed:
                store.flush_usage(force=True)
        except Exception as exc:
            print(
                f"WARNING: could not persist corrected run-finish timestamp to R2: {exc}",
                flush=True,
            )

    print(
        f"Run timestamp: completion recorded at {finished_at} "
        f"(crawl started at {started_at}).",
        flush=True,
    )


def _exploration_outcome() -> dict[str, Any] | None:
    """Build the exact run outcome before compact stats can lose the stop reason."""
    state = optimized._active_state
    if not isinstance(state, dict):
        return None

    summary = state.get("lastRunSummary") or {}
    exploration = summary.get("exploration") or {}
    if not isinstance(exploration, dict) or not exploration:
        return None

    raw_errors = exploration.get("errors") or []
    if isinstance(raw_errors, str):
        raw_errors = [raw_errors]
    errors = [str(item).strip() for item in raw_errors if str(item).strip()]

    page_budget = int(exploration.get("pageBudget") or 0)
    pages_completed = int(exploration.get("pagesCompleted") or 0)
    time_limited = bool(exploration.get("timeLimited"))
    partial = bool(time_limited or errors)

    # New explorer summaries explicitly distinguish a genuine end-of-pass from
    # a numbered-page result cap that successfully switched to createdAt cursor
    # mode. Keep the old inference only for legacy summaries that predate those
    # markers.
    if "naturalEnd" in exploration:
        natural_end = bool(exploration.get("naturalEnd"))
    else:
        natural_end = bool(
            page_budget > 0
            and pages_completed < page_budget
            and not partial
            and not bool(exploration.get("switchedToCursor"))
        )

    result_cap_reached = bool(exploration.get("resultCapReached"))
    switched_to_cursor = bool(exploration.get("switchedToCursor"))

    return {
        "at": state.get("lastRunAt"),
        "pageBudget": page_budget,
        "pagesCompleted": pages_completed,
        "timeLimited": time_limited,
        "errors": errors,
        "partial": partial,
        "naturalEnd": natural_end,
        "resultCapReached": result_cap_reached,
        "switchedToCursor": switched_to_cursor,
    }


def _annotate_run_history(outcome: dict[str, Any]) -> None:
    """Keep partial/natural-end markers in both R2 history and public stats."""
    run_at = str(outcome.get("at") or "")
    if not run_at:
        return

    markers = {
        "errors": list(outcome.get("errors") or []),
        "partial": bool(outcome.get("partial")),
        "naturalEnd": bool(outcome.get("naturalEnd")),
        "resultCapReached": bool(outcome.get("resultCapReached")),
        "switchedToCursor": bool(outcome.get("switchedToCursor")),
    }

    store = optimized._active_store
    if store is not None:
        key = store.key(store.meta_prefix, "stats-history.json")
        history = store.get_json(key, None)
        if isinstance(history, dict):
            changed = False
            for row in reversed(history.get("runs") or []):
                if str(row.get("at") or "") != run_at:
                    continue
                exploration = row.setdefault("exploration", {})
                for name, value in markers.items():
                    if exploration.get(name) != value:
                        exploration[name] = value
                        changed = True
                break
            if changed:
                store.put_json(key, history)
                store.flush_usage(force=True)

    stats_path = optimized.legacy.SITE_DATA_DIR / "stats.json"
    stats = optimized.legacy.read_json(stats_path, {})
    if isinstance(stats, dict):
        changed = False
        for row in reversed(stats.get("runs") or []):
            if str(row.get("at") or "") != run_at:
                continue
            exploration = row.setdefault("exploration", {})
            for name, value in markers.items():
                if exploration.get(name) != value:
                    exploration[name] = value
                    changed = True
            break
        if changed:
            optimized.legacy.write_json_if_changed(stats_path, stats)


def _write_exploration_outcome() -> None:
    outcome = _exploration_outcome()
    if not outcome:
        return

    optimized.legacy.write_json_if_changed(
        optimized.legacy.SITE_DATA_DIR / "last-exploration-status.json",
        outcome,
    )
    _annotate_run_history(outcome)


def main() -> int:
    sharded.install_discovery_shards()
    install_discovery_flush_batching(
        pages=_env_int("SPICYCHAT_ARCHIVE_DISCOVERY_FLUSH_PAGES", _DEFAULT_FLUSH_PAGES)
    )

    rc = 1
    try:
        rc = optimized.main()
        if rc == 0:
            _stamp_run_finished()
        return rc
    finally:
        # These are tiny status markers. R2 still owns the archive data; they
        # only let the website/Discord feed distinguish full, natural-end and
        # safely interrupted discovery runs.
        try:
            _write_exploration_outcome()
        except Exception as exc:
            print(
                f"WARNING: could not persist exploration outcome marker: {exc}",
                flush=True,
            )


if __name__ == "__main__":
    raise SystemExit(main())
