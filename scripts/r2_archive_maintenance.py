#!/usr/bin/env python3
"""Post-backfill archive maintenance mode.

Every run:
- scans newest Typesense pages first and stops after a small known-overlap window;
- advances a small deep Typesense sweep for recovery/change/deletion coverage;
- rotates through stored bot IDs with the public character API so richer fields
  (greeting/personality/scenario/etc.) are refreshed when they change;
- requires repeated 404 evidence before marking a bot deleted.

The rolling character-API sweep is checkpointed in R2 state, so scheduled runs
resume where the previous run stopped instead of starting over.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
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


def _seed_missing_404(
    run_state: dict[str, Any],
    bot_id: str,
    at: str,
) -> None:
    """Record the first explicit 404 without allowing same-run confirmation."""
    checks = run_state.setdefault("missingChecks", {})
    info = checks.setdefault(
        bot_id,
        {
            "count": 0,
            "lastAt": None,
            "lastStatus": None,
            "reason": "character-api-maintenance-404",
            "queuedAt": at,
        },
    )
    if int(info.get("count") or 0) <= 0:
        info["count"] = 1
        info["lastAt"] = at
        info["lastStatus"] = 404
        info.setdefault("reason", "character-api-maintenance-404")
        info.setdefault("queuedAt", at)


def _maintenance_character_refresh(
    client,
    run_config: dict[str, Any],
    at: str,
    run_state: dict[str, Any],
    *,
    store,
) -> dict[str, Any]:
    """Refresh richer character data across the stored corpus with a resumable cursor."""
    crawler = run_config.get("crawler") or {}
    if crawler.get("maintenance_verification_enabled") is False:
        sweep = run_state.setdefault("verificationSweep", {})
        order = store.discovery_order
        total = len(order)
        cursor = max(0, int(sweep.get("cursor", run_state.get("enrichmentCursor") or 0)))
        if total:
            cursor %= total
        else:
            cursor = 0
        print(
            "maintenance verification: skipped for discovery-focused manual run; "
            "scheduled runs will continue the rolling Character API sweep.",
            flush=True,
        )
        sweep.update(
            {
                "lastRunAt": at,
                "lastRunProcessed": 0,
                "lastRunEnriched": 0,
                "lastRunContentChanged": 0,
                "lastRunMetricUpdates": 0,
                "lastRunMissing": 0,
                "lastRunRestricted": 0,
                "lastRunRestored": 0,
                "lastRunTransient": 0,
                "lastRunPriority": 0,
                "lastRunSequential": 0,
                "lastRunCovered": 0,
                "lastRunWorkers": 0,
                "lastRunDurationSeconds": 0,
                "lastRunTimeLimited": False,
                "lastRunSkipped": True,
            }
        )
        return {
            "processed": 0,
            "enriched": 0,
            "changed": 0,
            "contentChanged": 0,
            "metricUpdates": 0,
            "missing": 0,
            "restricted": 0,
            "restored": 0,
            "transient": 0,
            "priorityProcessed": 0,
            "sequentialProcessed": 0,
            "sequentialCovered": 0,
            "verificationWorkers": 0,
            "verificationCursor": cursor,
            "verificationTotal": total,
            "verificationPass": int(sweep.get("completedPasses") or 0) + 1,
            "verificationCompletedPasses": int(sweep.get("completedPasses") or 0),
            "timeLimited": False,
            "durationSeconds": 0,
            "skipped": True,
            "skipReason": "manual-discovery-only",
        }

    budget = _positive_int(
        crawler.get("maintenance_verification_budget")
        or crawler.get("enrichment_budget"),
        5000,
        maximum=50000,
    )
    priority_budget = _positive_int(
        crawler.get("maintenance_priority_budget"),
        min(1000, budget),
        maximum=budget,
    )
    time_limit = _positive_int(
        crawler.get("maintenance_verification_time_limit_seconds"),
        1800,
        minimum=60,
        maximum=5400,
    )
    workers = _positive_int(
        crawler.get("maintenance_verification_workers"),
        8,
        maximum=16,
    )

    order = store.discovery_order
    total = len(order)
    priority = list(dict.fromkeys(run_state.setdefault("priorityEnrichment", [])))
    priority_selected = priority[: min(priority_budget, budget)]
    remaining_priority = priority[len(priority_selected) :]
    selected_ids = set(priority_selected)

    sweep = run_state.setdefault("verificationSweep", {})
    cursor = max(0, int(sweep.get("cursor", run_state.get("enrichmentCursor") or 0)))
    if total:
        cursor %= total
    else:
        cursor = 0

    completed_passes = max(0, int(sweep.get("completedPasses") or 0))
    pass_number = completed_passes + 1
    started_at = sweep.get("startedAt") or at
    checked_this_pass = max(0, int(sweep.get("checkedThisPass") or 0))

    processed = 0
    enriched = 0
    changed = 0
    metric_updates = 0
    missing = 0
    restricted = 0
    restored = 0
    transient = 0
    priority_processed = 0
    sequential_processed = 0
    sequential_covered = 0
    time_limited = False
    retry: list[str] = []
    started = time.monotonic()
    next_progress = 250

    print(
        "maintenance verification: "
        f"up to {budget:,} character API checks; "
        f"{workers} concurrent workers; "
        f"priority cap {min(priority_budget, budget):,}; "
        f"time limit {time_limit // 60}m; "
        f"cursor {cursor:,}/{total:,}; pass {pass_number:,}.",
        flush=True,
    )

    def out_of_time() -> bool:
        return (time.monotonic() - started) >= time_limit

    # requests.Session is not guaranteed to be thread-safe. The real crawler
    # therefore gives each concurrent slot its own Client/session while keeping
    # the same guest user id. Test/fake clients are shared deliberately.
    worker_clients = [client]
    if workers > 1 and isinstance(client, legacy.Client):
        for _ in range(workers - 1):
            clone = legacy.Client(run_config)
            clone.guest_user_id = getattr(client, "guest_user_id", clone.guest_user_id)
            worker_clients.append(clone)

    def fetch_batch(executor: ThreadPoolExecutor, bot_ids: list[str]) -> list[tuple[str, legacy.HTTPResult]]:
        futures = []
        for index, bot_id in enumerate(bot_ids):
            worker = worker_clients[index % len(worker_clients)]
            futures.append((bot_id, executor.submit(worker.character, bot_id)))

        results: list[tuple[str, legacy.HTTPResult]] = []
        for bot_id, future in futures:
            try:
                response = future.result()
            except Exception as exc:
                response = legacy.HTTPResult(False, 0, error=str(exc))
            results.append((bot_id, response))
        return results

    def process_response(bot_id: str, response: legacy.HTTPResult) -> None:
        nonlocal processed, enriched, changed, metric_updates
        nonlocal missing, restricted, restored, transient

        processed += 1

        if response.ok:
            payload = legacy.unwrap_character_payload(response.data)
            if not payload:
                transient += 1
                retry.append(bot_id)
                return

            payload.setdefault("character_id", bot_id)
            # The rolling verification pass exists to detect availability and
            # authored-content changes. Message/rating counters are high-churn
            # traffic metrics, not creator edits, so do not rewrite a bot record
            # just because those counters moved.
            for field in tuple(payload):
                if field in legacy.VOLATILE_FIELDS:
                    payload.pop(field, None)

            record = legacy.load_bot(bot_id)
            old_status = (record or {}).get("status", {}).get("current")
            old_history_len = len((record or {}).get("fieldHistory") or [])
            old_metrics = deepcopy(((record or {}).get("metrics") or {}).get("latest") or {})

            record, did_change = legacy.observe_bot(
                record,
                payload,
                source="character-api",
                at=at,
            )

            new_history_len = len((record or {}).get("fieldHistory") or [])
            new_metrics = deepcopy(((record or {}).get("metrics") or {}).get("latest") or {})
            substantive_change = (
                new_history_len > old_history_len
                or old_status != (record or {}).get("status", {}).get("current")
            )
            metrics_changed = new_metrics != old_metrics

            if did_change:
                legacy.save_bot(record)
                # Message/rating counters change constantly and are still worth
                # archiving, but they should not make the maintenance log claim
                # the creator edited the bot.
                if metrics_changed and not substantive_change:
                    metric_updates += 1
                else:
                    changed += 1

            if bot_id in run_state.setdefault("missingChecks", {}):
                run_state["missingChecks"].pop(bot_id, None)
            # Clearing a suspect/missing check is not a restoration. Count only
            # records that were actually confirmed deleted before this 200.
            if old_status == "deleted":
                restored += 1

            enriched += 1
            return

        if response.status == 404:
            missing += 1
            # Already-confirmed deleted bots stay in the rolling sweep so a
            # future 200 can restore them, but repeated 404s do not create a new
            # suspect queue for a bot that is already archived as deleted.
            if bot_id not in store.deleted_index:
                _seed_missing_404(run_state, bot_id, at)
            return

        if response.status in {401, 403}:
            restricted += 1
            return

        transient += 1
        retry.append(bot_id)

    def print_progress() -> None:
        nonlocal next_progress
        if processed < next_progress:
            return
        print(
            f"  maintenance verification {processed:,}/{budget:,}: "
            f"{enriched:,} ok, {changed:,} content changed, "
            f"{metric_updates:,} metric-only, {missing:,} 404; "
            f"cursor {cursor:,}/{total:,}",
            flush=True,
        )
        while next_progress <= processed:
            next_progress += 250

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            # Priority changes/new bots are refreshed first, but they cannot
            # consume the whole run forever; the rolling corpus sweep always
            # keeps most of the budget.
            priority_index = 0
            while (
                priority_index < len(priority_selected)
                and processed < budget
                and not out_of_time()
            ):
                room = min(
                    workers,
                    budget - processed,
                    len(priority_selected) - priority_index,
                )
                batch = priority_selected[priority_index : priority_index + room]
                for bot_id, response in fetch_batch(executor, batch):
                    process_response(bot_id, response)
                    priority_processed += 1
                    print_progress()
                priority_index += len(batch)

            if priority_index < len(priority_selected) and processed < budget:
                time_limited = True

            # Walk the stored ID order. Advancing the cursor over a bot already
            # checked through the priority queue still counts as covering that
            # corpus position.
            traversed = 0
            while (
                total
                and processed < budget
                and traversed < total
                and not out_of_time()
            ):
                batch: list[str] = []
                room = min(workers, budget - processed)

                while len(batch) < room and traversed < total:
                    bot_id = order[cursor]
                    cursor += 1
                    traversed += 1
                    sequential_covered += 1
                    checked_this_pass += 1

                    if cursor >= total:
                        cursor = 0
                        completed_passes += 1
                        pass_number = completed_passes + 1
                        sweep["lastCompletedAt"] = at
                        sweep["lastCompletedBots"] = total
                        started_at = at
                        checked_this_pass = 0

                    if bot_id in selected_ids:
                        continue
                    selected_ids.add(bot_id)
                    batch.append(bot_id)

                if not batch:
                    continue

                for bot_id, response in fetch_batch(executor, batch):
                    process_response(bot_id, response)
                    sequential_processed += 1
                    print_progress()

            if out_of_time() and processed < budget:
                time_limited = True
    finally:
        for worker in worker_clients[1:]:
            try:
                worker.session.close()
            except Exception:
                pass

    elapsed = int(time.monotonic() - started)

    run_state["enrichmentCursor"] = cursor
    run_state["priorityEnrichment"] = []
    unprocessed_priority = priority_selected[priority_processed:]
    for bot_id in [*unprocessed_priority, *remaining_priority, *retry]:
        optimized.cloud._priority_add(run_state, "priorityEnrichment", bot_id)

    sweep.update(
        {
            "schemaVersion": 2,
            "cursor": cursor,
            "totalBots": total,
            "completedPasses": completed_passes,
            "pass": completed_passes + 1,
            "startedAt": started_at,
            "checkedThisPass": checked_this_pass,
            "lastRunAt": at,
            "lastRunProcessed": processed,
            "lastRunEnriched": enriched,
            "lastRunChanged": changed,
            "lastRunMetricUpdates": metric_updates,
            "lastRunMissing": missing,
            "lastRunRestricted": restricted,
            "lastRunRestored": restored,
            "lastRunTransient": transient,
            "lastRunPriority": priority_processed,
            "lastRunSequential": sequential_processed,
            "lastRunCovered": sequential_covered,
            "lastRunWorkers": workers,
            "lastRunDurationSeconds": elapsed,
            "lastRunTimeLimited": time_limited,
        }
    )

    print(
        "maintenance verification: finished "
        f"{processed:,}/{budget:,} checks in {elapsed/60:.1f}m with {workers} workers; "
        f"{enriched:,} available, {changed:,} content changed, "
        f"{metric_updates:,} metric-only updates, {missing:,} 404, "
        f"{restricted:,} restricted, {restored:,} restored, "
        f"{transient:,} transient; cursor {cursor:,}/{total:,}; "
        f"completed passes {completed_passes:,}.",
        flush=True,
    )

    return {
        "processed": processed,
        "enriched": enriched,
        "changed": changed,
        "contentChanged": changed,
        "metricUpdates": metric_updates,
        "missing": missing,
        "restricted": restricted,
        "restored": restored,
        "transient": transient,
        "priorityProcessed": priority_processed,
        "sequentialProcessed": sequential_processed,
        "sequentialCovered": sequential_covered,
        "verificationWorkers": workers,
        "verificationCursor": cursor,
        "verificationTotal": total,
        "verificationPass": completed_passes + 1,
        "verificationCompletedPasses": completed_passes,
        "timeLimited": time_limited,
        "durationSeconds": elapsed,
    }


def maintenance_configure_cloud(config: dict[str, Any], store):
    state, bloom = _original_configure_cloud(config, store)

    optimized_scan_listing = legacy.scan_listing
    optimized_explore_more = legacy.explore_more
    optimized_observe_bot = legacy.observe_bot
    optimized_verify_missing = legacy.verify_missing

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

    def maintenance_enrich_queue(
        client,
        run_config: dict[str, Any],
        at: str,
        run_state: dict[str, Any],
    ):
        return _maintenance_character_refresh(
            client,
            run_config,
            at,
            run_state,
            store=store,
        )

    def maintenance_verify_missing(
        client,
        run_config: dict[str, Any],
        at: str,
        run_state: dict[str, Any],
        seen_public: set[str],
    ):
        # A first 404 discovered by the rolling character sweep is already one
        # explicit observation. Do not immediately hit the same bot again a few
        # minutes later and count that as an independent confirmation.
        checks = run_state.setdefault("missingChecks", {})
        deferred: dict[str, dict[str, Any]] = {}
        for bot_id, info in list(checks.items()):
            if (
                int(info.get("lastStatus") or 0) == 404
                and str(info.get("lastAt") or "") == at
            ):
                if bot_id in seen_public:
                    checks.pop(bot_id, None)
                else:
                    deferred[bot_id] = checks.pop(bot_id)

        try:
            result = optimized_verify_missing(
                client,
                run_config,
                at,
                run_state,
                seen_public,
            )
        finally:
            for bot_id, info in deferred.items():
                checks.setdefault(bot_id, info)
        result["deferredSameRun404"] = len(deferred)
        return result

    legacy.observe_bot = observe_with_change_refresh
    legacy.scan_listing = maintenance_scan_listing
    legacy.explore_more = maintenance_explore_more
    legacy.enrich_queue = maintenance_enrich_queue
    legacy.verify_missing = maintenance_verify_missing

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
