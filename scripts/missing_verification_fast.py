"""Bounded, fair, parallel retrieval for already-queued Character API suspects.

Only the main archive thread changes state or writes R2. Workers perform
Character API GETs with separate requests.Session instances. There is one
request per bot per run; a transient error never counts toward deletion.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterator

import archive as legacy


def ordered_suspects(
    checks: dict[str, dict[str, Any]], at: str, limit: int,
    *, due: Callable[[dict[str, Any], str], bool] = legacy.missing_check_due,
) -> list[str]:
    """Favor awaiting second 404s without starving never-tested IDs or errors.

    Existing dict order is the persisted round-robin checkpoint. Processed but
    unresolved entries rotate to the back in the parent verifier.
    """
    first_404: list[str] = []
    unqueried: list[str] = []
    errors: list[str] = []
    for bot_id, info in checks.items():
        if not due(info, at):
            continue
        status = int(info.get("lastStatus") or 0)
        if status == 404 and int(info.get("count") or 0) > 0:
            first_404.append(bot_id)
        elif not info.get("lastAt"):
            unqueried.append(bot_id)
        else:
            errors.append(bot_id)

    budget = max(0, int(limit))
    if not budget:
        return []
    quotas = (
        min(len(first_404), max(1, budget * 45 // 100)),
        min(len(unqueried), max(1, budget * 45 // 100)),
        min(len(errors), max(1, budget // 10)),
    )
    chosen = (first_404[:quotas[0]] + unqueried[:quotas[1]]
              + errors[:quotas[2]])
    # Fill unused space in priority order, still guaranteeing that all three
    # cohorts can make progress when they exist.
    if len(chosen) < budget:
        chosen.extend((first_404[quotas[0]:] + unqueried[quotas[1]:]
                       + errors[quotas[2]:])[:budget - len(chosen)])
    return chosen[:budget]


class GlobalPacer:
    """Shared request start-time limiter, not one independent limiter per worker."""

    def __init__(self, rate_per_sec: int, clock=time.monotonic,
                 sleep=time.sleep):
        self.delay = 1.0 / max(1, min(int(rate_per_sec), 16))
        self._lock = threading.Lock()
        self._next = 0.0
        self._clock = clock
        self._sleep = sleep

    def wait(self) -> None:
        with self._lock:
            current = self._clock()
            delay = max(0.0, self._next - current)
            self._next = max(current, self._next) + self.delay
        if delay:
            self._sleep(delay)


def verified_responses(
    ids: list[str], client: Any, config: dict[str, Any],
    *, workers: int = 8, rate_per_sec: int = 8,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[tuple[str, legacy.HTTPResult]]:
    """Yield complete bounded batches, pause/stop on persistent API errors.

    Reuses the same guest identity across separately allocated Client sessions,
    matching the normal maintenance verifier. The circuit breaker limits
    429/5xx/transport bursts; unattempted IDs remain in the persisted queue.
    """
    count = max(1, min(int(workers), 16))
    pacing = GlobalPacer(rate_per_sec, sleep=sleep)
    clients = [client]
    if count > 1 and isinstance(client, legacy.Client):
        for _ in range(count - 1):
            clone = legacy.Client(config)
            clone.guest_user_id = client.guest_user_id
            clients.append(clone)

    def fetch(index: int, bot_id: str) -> legacy.HTTPResult:
        pacing.wait()
        try:
            return clients[index % len(clients)].character(bot_id)
        except Exception as exc:
            return legacy.HTTPResult(False, 0, error=str(exc))

    failed_batches = 0
    pause_seconds = 1
    with ThreadPoolExecutor(max_workers=count) as pool:
        for offset in range(0, len(ids), count):
            subset = ids[offset:offset + count]
            futures = [pool.submit(fetch, idx, bot_id)
                       for idx, bot_id in enumerate(subset)]
            failures = 0
            for bot_id, future in zip(subset, futures):
                try:
                    response = future.result()
                except Exception as exc:
                    response = legacy.HTTPResult(False, 0, error=str(exc))
                if response.status == 429 or response.status == 0 or response.status >= 500:
                    failures += 1
                yield bot_id, response
            if failures:
                # Cloudflare or API throttling: back off *between* batches;
                # never retry the same ID inside this verification run.
                sleep(pause_seconds)
                pause_seconds = min(pause_seconds * 2, 16)
                if failures >= max(2, (len(subset) + 1) // 2):
                    failed_batches += 1
                else:
                    failed_batches = 0
            else:
                pause_seconds = 1
                failed_batches = 0
            if failed_batches >= 3:
                print(
                    "Missing verifier: circuit breaker paused remaining queries "
                    "after three heavily throttled/error batches. "
                    "Unattempted IDs stay queued for the next run.",
                    flush=True,
                )
                break
