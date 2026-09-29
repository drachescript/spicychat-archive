#!/usr/bin/env python3
"""Runtime accelerators for R2-backed archive jobs.

This module deliberately leaves the archive's persistent format alone. It only
changes how independent bot-object PUTs and Typesense page searches are issued:

- bot JSON PUTs can run concurrently while quota accounting remains synchronous;
- non-bot writes are barriers, so discovery journals/receipts/state are never
  persisted before their pending bot objects finish successfully;
- repeated writes to the same bot are serialized to preserve last-write order;
- single-page Typesense calls can prefetch a small consecutive page batch through
  the API's native multi_search endpoint.

The accelerators are opt-in. Importing this module alone changes nothing.
"""
from __future__ import annotations

import gzip
import json
import threading
import time
import weakref
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from copy import deepcopy
from typing import Any

import archive as legacy
from storage_r2 import R2ArchiveStore


_INSTALLED = False
_WRITE_WORKERS = 8
_TYPESENSE_BATCH = 4

_ORIGINAL_PUT_BYTES = R2ArchiveStore.put_bytes
_ORIGINAL_SAVE_BOT = R2ArchiveStore.save_bot
_ORIGINAL_MULTI_SEARCH = legacy.Client.multi_search

_STORES: "weakref.WeakSet[R2ArchiveStore]" = weakref.WeakSet()


class _WriteState:
    def __init__(self, workers: int):
        self.executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="r2-bot-put",
        )
        self.lock = threading.RLock()
        self.pending: dict[Future, str] = {}
        self.pending_by_key: dict[str, Future] = {}
        self.touched_keys: set[str] = set()
        self.closed = False


def _state(store: R2ArchiveStore) -> _WriteState:
    value = getattr(store, "_runtime_accel_write_state", None)
    if isinstance(value, _WriteState) and not value.closed:
        return value
    value = _WriteState(_WRITE_WORKERS)
    setattr(store, "_runtime_accel_write_state", value)
    _STORES.add(store)
    return value


def _forget_future(state: _WriteState, future: Future) -> None:
    with state.lock:
        key = state.pending.pop(future, None)
        if key and state.pending_by_key.get(key) is future:
            state.pending_by_key.pop(key, None)


def _wait_one(store: R2ArchiveStore, future: Future) -> None:
    state = _state(store)
    try:
        future.result()
    finally:
        _forget_future(state, future)


def _wait_key(store: R2ArchiveStore, key: str) -> None:
    state = _state(store)
    with state.lock:
        future = state.pending_by_key.get(key)
    if future is not None:
        _wait_one(store, future)


def pending_bot_writes(store: R2ArchiveStore) -> int:
    state = getattr(store, "_runtime_accel_write_state", None)
    if not isinstance(state, _WriteState) or state.closed:
        return 0
    with state.lock:
        return len(state.pending)


def flush_bot_writes(store: R2ArchiveStore, *, announce: bool = False) -> int:
    """Wait for all queued bot PUTs and surface any write failure."""
    state = getattr(store, "_runtime_accel_write_state", None)
    if not isinstance(state, _WriteState) or state.closed:
        return 0

    with state.lock:
        futures = list(state.pending)
    if not futures:
        return 0

    started = time.monotonic()
    first_error: BaseException | None = None
    completed = 0
    for future in as_completed(futures):
        try:
            future.result()
        except BaseException as exc:  # propagate after draining the whole batch
            if first_error is None:
                first_error = exc
        finally:
            completed += 1
            _forget_future(state, future)

    if announce and completed:
        elapsed = time.monotonic() - started
        print(
            f"  parallel R2 bot writes: {completed:,} completed with "
            f"{_WRITE_WORKERS} workers in {elapsed:.1f}s",
            flush=True,
        )

    if first_error is not None:
        raise first_error
    return completed


def close_store(store: R2ArchiveStore) -> None:
    state = getattr(store, "_runtime_accel_write_state", None)
    if not isinstance(state, _WriteState) or state.closed:
        return
    try:
        flush_bot_writes(store)
    finally:
        state.closed = True
        state.executor.shutdown(wait=True, cancel_futures=False)


def flush_all_stores() -> None:
    first_error: BaseException | None = None
    for store in list(_STORES):
        try:
            close_store(store)
        except BaseException as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


def _async_put_bytes(
    self: R2ArchiveStore,
    key: str,
    data: bytes,
    *,
    content_type: str = "application/octet-stream",
    gzip_content: bool = False,
    cache_control: str | None = None,
    category: str = "data",
    known_new: bool = False,
) -> None:
    # Receipts, journals, state, rankings and media are ordering barriers. This
    # guarantees they never claim a bot write succeeded while it is still queued.
    if category != "bot" or _WRITE_WORKERS <= 1:
        flush_bot_writes(self, announce=(category != "bot"))
        return _ORIGINAL_PUT_BYTES(
            self,
            key,
            data,
            content_type=content_type,
            gzip_content=gzip_content,
            cache_control=cache_control,
            category=category,
            known_new=known_new,
        )

    state = _state(self)

    # Preserve deterministic last-write-wins behavior when one bot is changed
    # more than once before a barrier (e.g. duplicate historical snapshots).
    _wait_key(self, key)

    body = gzip.compress(data, compresslevel=6) if gzip_content else data
    old_size = 0 if known_new else self._head_size(key)
    delta, media_delta, was_new = self._reserve_usage(
        key=key,
        new_size=len(body),
        old_size=old_size,
        category=category,
    )

    kwargs: dict[str, Any] = {
        "Bucket": self.bucket,
        "Key": key,
        "Body": body,
        "ContentType": content_type,
    }
    if gzip_content:
        kwargs["ContentEncoding"] = "gzip"
    if cache_control:
        kwargs["CacheControl"] = cache_control

    def put_one() -> None:
        try:
            self.s3.put_object(**kwargs)
        except BaseException:
            self._rollback_usage(delta, media_delta, was_new)
            raise

    future = state.executor.submit(put_one)
    with state.lock:
        state.pending[future] = key
        state.pending_by_key[key] = future
        state.touched_keys.add(key)

    # Keep the existing conservative usage checkpoints. The checkpoint may lead
    # an in-flight PUT by a few seconds, which can only over-count after a crash.
    self.flush_usage()


def _parallel_save_bot(self: R2ArchiveStore, record: dict[str, Any]) -> bool:
    bot_id = str(record["id"]).lower()
    key = self.bot_key(bot_id)
    state = _state(self)

    # If this key already has an in-flight write, finish it before calculating
    # the next object's old size and scheduling the replacement.
    _wait_key(self, key)

    # import_bot_status historically registers a brand-new ID immediately before
    # save_bot(). Treat that first write as known-new too, avoiding thousands of
    # unnecessary HEAD requests while keeping later writes to the same key safe.
    with state.lock:
        touched = key in state.touched_keys
    is_new = bot_id not in self.known_ids or (
        bot_id in self._new_ids_journal and not touched
    )

    self.put_json(
        key,
        record,
        public=True,
        category="bot",
        known_new=is_new,
    )
    self.register_id(bot_id)
    self._records[bot_id] = deepcopy(record)
    while len(self._records) > self.cache_limit:
        self._records.popitem(last=False)
    return True


def _request_signature(request: dict[str, Any]) -> str:
    return json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _batched_multi_search(self: legacy.Client, searches: list[dict[str, Any]]):
    if _TYPESENSE_BATCH <= 1 or len(searches) != 1:
        return _ORIGINAL_MULTI_SEARCH(self, searches)

    request = searches[0]
    try:
        page = int(request.get("page") or 1)
        per_page = int(request.get("per_page") or 0)
    except (TypeError, ValueError):
        return _ORIGINAL_MULTI_SEARCH(self, searches)

    # Cursor mode depends on the previous page's last createdAt and cannot be
    # predicted safely. Leave those searches strictly sequential.
    filter_by = str(request.get("filter_by") or "")
    if page < 1 or per_page <= 0 or "createdAt:<" in filter_by:
        return _ORIGINAL_MULTI_SEARCH(self, searches)

    cache = getattr(self, "_runtime_accel_typesense_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(self, "_runtime_accel_typesense_cache", cache)

    signature = _request_signature(request)
    cached = cache.pop(signature, None)
    if isinstance(cached, dict):
        return legacy.HTTPResult(
            True,
            int(cached.get("status") or 200),
            data={"results": [cached.get("result") or {}]},
            url=cached.get("url"),
        )

    batched: list[dict[str, Any]] = []
    for offset in range(_TYPESENSE_BATCH):
        row = dict(request)
        row["page"] = page + offset
        batched.append(row)

    response = _ORIGINAL_MULTI_SEARCH(self, batched)
    if not response.ok or not isinstance(response.data, dict):
        return response

    results = response.data.get("results") or []
    if not isinstance(results, list) or not results:
        return response

    for generated, result in zip(batched[1:], results[1:]):
        cache[_request_signature(generated)] = {
            "status": response.status,
            "url": response.url,
            "result": result if isinstance(result, dict) else {},
        }

    current = results[0] if isinstance(results[0], dict) else {}
    return legacy.HTTPResult(
        True,
        response.status,
        data={"results": [current]},
        url=response.url,
    )


def install_runtime_acceleration(*, write_workers: int = 8, typesense_batch: int = 4) -> None:
    """Enable conservative runtime-only acceleration for this Python process."""
    global _INSTALLED, _WRITE_WORKERS, _TYPESENSE_BATCH
    if _INSTALLED:
        return

    _WRITE_WORKERS = max(1, min(32, int(write_workers)))
    _TYPESENSE_BATCH = max(1, min(8, int(typesense_batch)))

    R2ArchiveStore.put_bytes = _async_put_bytes
    R2ArchiveStore.save_bot = _parallel_save_bot
    legacy.Client.multi_search = _batched_multi_search
    _INSTALLED = True

    print(
        "Archive acceleration: "
        f"{_WRITE_WORKERS} concurrent R2 bot writers; "
        f"Typesense prefetch batch {_TYPESENSE_BATCH}.",
        flush=True,
    )
