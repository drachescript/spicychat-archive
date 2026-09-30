#!/usr/bin/env python3
"""Retry transient Typesense failures without changing archive semantics."""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import archive as legacy


_INSTALLED = False
_RETRYABLE_STATUSES = {0, 429, 500, 502, 503, 504}
_DEFAULT_RETRY_DELAYS = (0.0, 2.0, 5.0, 10.0)


def _retry_delays() -> tuple[float, ...]:
    raw = os.environ.get("SPICYCHAT_ARCHIVE_TYPESENSE_RETRY_DELAYS", "").strip()
    if not raw:
        return _DEFAULT_RETRY_DELAYS

    delays: list[float] = []
    for part in raw.split(","):
        try:
            value = float(part.strip())
        except (TypeError, ValueError):
            continue
        if value >= 0:
            delays.append(value)
    return tuple(delays) or _DEFAULT_RETRY_DELAYS


def _describe(response: legacy.HTTPResult) -> str:
    status = int(response.status or 0)
    error = str(response.error or "unknown Typesense error")
    return f"HTTP {status}: {error}" if status else error


def multi_search_with_retry(
    original: Callable[[Any, list[dict[str, Any]]], legacy.HTTPResult],
    client: Any,
    searches: list[dict[str, Any]],
    *,
    delays: tuple[float, ...] | None = None,
) -> legacy.HTTPResult:
    """Retry only transient/network Typesense failures, preserving final error."""
    response = original(client, searches)
    if response.ok or int(response.status or 0) not in _RETRYABLE_STATUSES:
        return response

    retry_delays = _retry_delays() if delays is None else delays
    total = len(retry_delays)

    for retry_no, delay in enumerate(retry_delays, start=1):
        if delay > 0:
            time.sleep(delay)

        print(
            "Typesense transient failure: "
            f"{_describe(response)}; retry {retry_no}/{total}"
            + (f" after {delay:g}s" if delay > 0 else " immediately")
            + ".",
            flush=True,
        )

        response = original(client, searches)
        if response.ok:
            print(
                f"Typesense recovered on retry {retry_no}/{total}.",
                flush=True,
            )
            return response

        if int(response.status or 0) not in _RETRYABLE_STATUSES:
            return response

    print(
        f"Typesense retries exhausted after {total} retries: {_describe(response)}",
        flush=True,
    )
    return response


def install_typesense_retry() -> None:
    """Wrap the currently installed Client.multi_search implementation."""
    global _INSTALLED
    if _INSTALLED:
        return

    original = legacy.Client.multi_search

    def retrying_multi_search(
        self: legacy.Client,
        searches: list[dict[str, Any]],
    ) -> legacy.HTTPResult:
        return multi_search_with_retry(original, self, searches)

    legacy.Client.multi_search = retrying_multi_search
    _INSTALLED = True
    print(
        "Typesense retry guard: transient failures get 4 retries "
        "(immediate, 2s, 5s, 10s).",
        flush=True,
    )
