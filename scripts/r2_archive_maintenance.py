#!/usr/bin/env python3
"""Post-backfill archive maintenance mode.

The original deep discovery cursor stays in place as a rotating verification sweep,
but every run also scans the newest Typesense pages from page 1 and stops after a
small, consecutive overlap window of already-known bots. That keeps new bots fresh
without waiting for the deep sweep to wrap around.

Stable Typesense metadata changes also promote the bot into the richer character
API enrichment queue. Deletion decisions remain conservative: only a completed deep
Typesense sweep can queue missing bots, and repeated character-API 404s are still
required before a bot is marked deleted.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import archive as legacy
import r2_archive_batched as batched
import r2_archive_optimized as optimized


_original_configure_cloud = optimized.optimized_configure_cloud


def _positive_int(value: Any, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _unique_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """De-duplicate shifting Typesense pages while preserving first-seen order."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for doc in docs:
        bot_id = legacy.normalize_id(doc)
        if bot_id:
            if bot_id in seen:
                continue
            seen.add(bot_id)
        out.append(doc)
    return out


class _ReplayTypesenseClient:
    """Feed already-fetched latest docs through the optimized listing ingester."""

    def __init__(self, docs: list[dict[str, Any]], found: int | None):
        self.docs = docs
        self.found = found

    def multi_search(self, searches: list[dict[str, Any]]) -> legacy.HTTPResult:
        request = searches[0] if searches else {}
        page = _positive_int(request.get("page"), 1)
        per_page = _positive_int(request.get("per_page"), 250, maximum=250)
        start = (page - 1) * per_page
        rows = self.docs[start : start + per_page]
        found = self.found if self.found is not None else len(self.docs)
        result = {
            "found": found,
            "hits": [{"document": row} for row in rows],
        }
        return legacy.HTTPResult(
            True,
            200,
            data={"results": [result]},
            url="memory://latest-overlap",
        )


def _scan_latest_until_overlap(
    client,
    config: dict[str, Any],
    *,
    name: str,
    sort_by: str,
    at: str,
    state: dict[str, Any],
    store,
    ingest_listing,
) -> dict[str, Any]:
    crawler = config.get("crawler") or {}
    page_size = _positive_int(
        crawler.get("latest_page_size") or crawler.get("listing_page_size"),
        250,
        maximum=250,
    )
    overlap_target = _positive_int(crawler.get("latest_overlap_pages"), 3, maximum=20)
    max_pages = _positive_int(crawler.get("latest_max_pages"), 20, maximum=200)

    known_at_start = set(store.known_ids)
    deleted_at_start = set(store.deleted_index)
    docs: list[dict[str, Any]] = []
    found: int | None = None
    overlap_streak = 0
    pages_completed = 0
    stop_reason = "max-pages"

    print(
        f"{name}: newest-first scan; stop after {overlap_target} consecutive "
        f"all-known pages (max {max_pages} pages × {page_size}).",
        flush=True,
    )

    for page in range(1, max_pages + 1):
        request = legacy.typesense_search(
            config,
            page=page,
            per_page=page_size,
            sort_by=sort_by,
        )
        response = client.multi_search([request])
        if not response.ok:
            return {
                "ok": False,
                "error": response.error,
                "status": response.status,
                "count": len(docs),
                "ids": [],
                "pagesCompleted": pages_completed,
                "overlapPages": overlap_streak,
                "stopReason": "request-error",
            }

        result = (response.data.get("results") or [{}])[0]
        hits, found_now = legacy.extract_hits(result)
        if found is None:
            found = found_now
        if not hits:
            stop_reason = "end-of-results"
            break

        pages_completed += 1
        docs.extend(hits)

        page_ids = [
            bot_id
            for bot_id in (legacy.normalize_id(doc) for doc in hits)
            if bot_id
        ]
        interesting = sum(
            1
            for bot_id in page_ids
            if bot_id not in known_at_start or bot_id in deleted_at_start
        )
        if interesting == 0:
            overlap_streak += 1
        else:
            overlap_streak = 0

        print(
            f"  {name} page {page}: {len(hits):,} hits; "
            f"{interesting:,} new/restored candidates; "
            f"known-overlap streak {overlap_streak}/{overlap_target}",
            flush=True,
        )

        if overlap_streak >= overlap_target:
            stop_reason = "known-overlap"
            break
        if len(hits) < page_size:
            stop_reason = "end-of-results"
            break

    docs = _unique_docs(docs)
    if not docs:
        return {
            "ok": True,
            "found": found,
            "count": 0,
            "new": 0,
            "changed": 0,
            "ids": [],
            "metrics": {},
            "pagesCompleted": pages_completed,
            "overlapPages": overlap_streak,
            "stopReason": stop_reason,
        }

    # Reuse the existing optimized fingerprint/R2 ingestion path instead of
    # duplicating it. The replay client serves the docs collected above from
    # memory, so this stage does not hit Typesense a second time.
    replay_config = deepcopy(config)
    replay_crawler = replay_config.setdefault("crawler", {})
    replay_crawler["listing_page_size"] = page_size
    replay_crawler["listing_max_hits"] = len(docs)
    info = ingest_listing(
        _ReplayTypesenseClient(docs, found),
        replay_config,
        name,
        sort_by,
        at,
        state,
    )
    info["pagesCompleted"] = pages_completed
    info["overlapPages"] = overlap_streak
    info["overlapTarget"] = overlap_target
    info["stopReason"] = stop_reason
    return info


def maintenance_configure_cloud(config: dict[str, Any], store):
    state, bloom = _original_configure_cloud(config, store)

    optimized_scan_listing = legacy.scan_listing
    optimized_explore_more = legacy.explore_more
    optimized_observe_bot = legacy.observe_bot

    def observe_with_change_refresh(
        record: dict[str, Any] | None,
        incoming: dict[str, Any],
        *,
        source: str,
        at: str,
    ):
        existed = record is not None
        observed, changed = optimized_observe_bot(
            record,
            incoming,
            source=source,
            at=at,
        )
        # The optimized Typesense path has already removed volatile counters and
        # updatedAt noise. A remaining change is useful enough to refresh richer
        # character-API fields such as greeting/personality when available.
        if existed and changed and source == "typesense":
            bot_id = str(observed.get("id") or "").lower()
            if bot_id:
                optimized.cloud._priority_add(state, "priorityEnrichment", bot_id)
        return observed, changed

    def maintenance_scan_listing(
        client,
        run_config: dict[str, Any],
        name: str,
        sort_by: str,
        at: str,
        run_state: dict[str, Any],
    ):
        if name == "latest":
            return _scan_latest_until_overlap(
                client,
                run_config,
                name=name,
                sort_by=sort_by,
                at=at,
                state=run_state,
                store=store,
                ingest_listing=optimized_scan_listing,
            )
        return optimized_scan_listing(
            client,
            run_config,
            name,
            sort_by,
            at,
            run_state,
        )

    def maintenance_explore_more(
        client,
        run_config: dict[str, Any],
        at: str,
        run_state: dict[str, Any],
    ):
        result = optimized_explore_more(client, run_config, at, run_state)
        result["purpose"] = "deep-refresh-and-deletion-sweep"
        result["sweepPass"] = int((run_state.get("exploration") or {}).get("pass") or 0)
        return result

    legacy.observe_bot = observe_with_change_refresh
    legacy.scan_listing = maintenance_scan_listing
    legacy.explore_more = maintenance_explore_more

    return state, bloom


def main() -> int:
    # optimized.main() installs whatever is currently bound to
    # optimized.optimized_configure_cloud, so patch it before entering the
    # existing batched/sharded runner.
    optimized.optimized_configure_cloud = maintenance_configure_cloud
    try:
        return batched.main()
    finally:
        optimized.optimized_configure_cloud = _original_configure_cloud


if __name__ == "__main__":
    raise SystemExit(main())
