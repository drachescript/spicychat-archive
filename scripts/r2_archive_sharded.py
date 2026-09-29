#!/usr/bin/env python3
"""Run the optimized archive with cheap, crash-safe discovery shards.

New discovery-only records are grouped into one immutable R2 shard per ingest
batch instead of immediately creating one R2 object per bot.  A compact public
prefix index lets the website (and later crawler runs) resolve a UUID to its
shard.  Once a bot is enriched, edited, deleted, or otherwise needs a durable
standalone record, the normal per-bot object is written and takes precedence.

The ordering is deliberately conservative:

1. write the immutable shard;
2. write a locator recovery journal;
3. let the existing discovery-order recovery journal persist the UUIDs;
4. at the successful end of the run, fold locator journals into 256 possible
   public prefix indexes and only then remove those locator journals.

A killed workflow can therefore recover both the discovered UUIDs and the
shard locations on its next run.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
import uuid

import r2_archive_optimized as optimized
from storage_r2 import R2ArchiveStore


SHARD_SCHEMA = 1
INDEX_SCHEMA = 1

# r2_accel_runner installs its bot-write accelerator before executing this file.
# Keep references to that already-accelerated behavior for standalone/materialized
# records, then layer discovery sharding on top of it.
_MATERIALIZED_SAVE_BOT = R2ArchiveStore.save_bot
_BASE_GET_JSON = R2ArchiveStore.get_json
_BASE_LOAD_DISCOVERY_ORDER = R2ArchiveStore.load_discovery_order
_BASE_FLUSH_DISCOVERY_JOURNAL = R2ArchiveStore.flush_discovery_journal
_BASE_SAVE_DISCOVERY_ORDER = R2ArchiveStore.save_discovery_order


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _prefix_for(bot_id: str) -> str:
    compact = str(bot_id).replace("-", "").lower()
    return compact[:2] if len(compact) >= 2 else "__"


def _shard_prefix(store: R2ArchiveStore) -> str:
    r2 = ((store.config.get("storage") or {}).get("r2") or {})
    return str(r2.get("discovery_shard_prefix") or "discovery/shards").strip("/")


def _index_prefix(store: R2ArchiveStore) -> str:
    r2 = ((store.config.get("storage") or {}).get("r2") or {})
    return str(r2.get("discovery_index_prefix") or "indexes/discovery").strip("/")


def _locator_journal_prefix(store: R2ArchiveStore) -> str:
    return store.key(store.meta_prefix, "discovery-locator-journal") + "/"


def _ensure_state(store: R2ArchiveStore) -> None:
    if not hasattr(store, "_shard_pending_records"):
        store._shard_pending_records = {}  # type: ignore[attr-defined]
    if not hasattr(store, "_shard_locator"):
        store._shard_locator = {}  # type: ignore[attr-defined]
    if not hasattr(store, "_shard_locator_touched"):
        store._shard_locator_touched = set()  # type: ignore[attr-defined]
    if not hasattr(store, "_shard_locator_journal_keys"):
        store._shard_locator_journal_keys = []  # type: ignore[attr-defined]
    if not hasattr(store, "_shard_index_loaded_prefixes"):
        store._shard_index_loaded_prefixes = set()  # type: ignore[attr-defined]
    if not hasattr(store, "_shard_cache"):
        store._shard_cache = {}  # type: ignore[attr-defined]


def _remember_locator(store: R2ArchiveStore, bot_id: str, shard_key: str) -> None:
    _ensure_state(store)
    bot_id = str(bot_id).lower()
    store._shard_locator[bot_id] = shard_key  # type: ignore[attr-defined]
    store._shard_locator_touched.add(_prefix_for(bot_id))  # type: ignore[attr-defined]


def _load_recovery_journals(store: R2ArchiveStore) -> None:
    _ensure_state(store)
    keys = store._list_keys(_locator_journal_prefix(store))
    store._shard_locator_journal_keys = list(keys)  # type: ignore[attr-defined]
    recovered = 0
    for key in keys:
        row = _BASE_GET_JSON(store, key, None)
        if not isinstance(row, dict):
            continue
        shard_key = str(row.get("shard") or "")
        ids = row.get("ids") or []
        if not shard_key or not isinstance(ids, list):
            continue
        for raw_id in ids:
            bot_id = str(raw_id).lower()
            if bot_id:
                _remember_locator(store, bot_id, shard_key)
                recovered += 1
    if recovered:
        print(
            f"Discovery shards: recovered {recovered:,} UUID locators from "
            f"{len(keys):,} unfinished journal(s).",
            flush=True,
        )


def _load_prefix_index(store: R2ArchiveStore, prefix: str) -> None:
    _ensure_state(store)
    loaded = store._shard_index_loaded_prefixes  # type: ignore[attr-defined]
    if prefix in loaded:
        return
    key = store.key(_index_prefix(store), f"{prefix}.json")
    payload = _BASE_GET_JSON(store, key, None)
    if isinstance(payload, dict):
        bots = payload.get("bots")
        if isinstance(bots, dict):
            for raw_id, raw_shard in bots.items():
                bot_id = str(raw_id).lower()
                shard_key = str(raw_shard or "")
                if bot_id and shard_key and bot_id not in store._shard_locator:  # type: ignore[attr-defined]
                    store._shard_locator[bot_id] = shard_key  # type: ignore[attr-defined]
    loaded.add(prefix)


def _shard_key_for_bot(store: R2ArchiveStore, bot_id: str) -> str | None:
    _ensure_state(store)
    bot_id = str(bot_id).lower()
    shard_key = store._shard_locator.get(bot_id)  # type: ignore[attr-defined]
    if shard_key:
        return str(shard_key)
    _load_prefix_index(store, _prefix_for(bot_id))
    shard_key = store._shard_locator.get(bot_id)  # type: ignore[attr-defined]
    return str(shard_key) if shard_key else None


def _load_from_shard(store: R2ArchiveStore, bot_id: str) -> dict[str, Any] | None:
    _ensure_state(store)
    bot_id = str(bot_id).lower()

    pending = store._shard_pending_records.get(bot_id)  # type: ignore[attr-defined]
    if isinstance(pending, dict):
        return deepcopy(pending)

    shard_key = _shard_key_for_bot(store, bot_id)
    if not shard_key:
        return None

    cache = store._shard_cache  # type: ignore[attr-defined]
    shard = cache.get(shard_key)
    if not isinstance(shard, dict):
        shard = _BASE_GET_JSON(store, shard_key, None)
        if not isinstance(shard, dict):
            return None
        cache[shard_key] = shard
        # Detail/enrichment usually touches only a handful of shards. Keep this
        # bounded so a very long recovery run cannot grow memory indefinitely.
        if len(cache) > 128:
            cache.pop(next(iter(cache)))

    records = shard.get("records")
    if not isinstance(records, dict):
        return None
    record = records.get(bot_id)
    return deepcopy(record) if isinstance(record, dict) else None


def sharded_get_json(self: R2ArchiveStore, key: str, default: Any = None) -> Any:
    value = _BASE_GET_JSON(self, key, None)
    if value is not None:
        return value

    bot_root = self.bot_prefix.rstrip("/") + "/"
    if str(key).startswith(bot_root) and str(key).endswith(".json"):
        bot_id = str(key).rsplit("/", 1)[-1][:-5].lower()
        record = _load_from_shard(self, bot_id)
        if record is not None:
            return record
    return deepcopy(default)


def sharded_load_discovery_order(self: R2ArchiveStore) -> None:
    _BASE_LOAD_DISCOVERY_ORDER(self)
    _load_recovery_journals(self)


def _flush_pending_shard(store: R2ArchiveStore) -> int:
    _ensure_state(store)
    pending = store._shard_pending_records  # type: ignore[attr-defined]
    if not pending:
        return 0

    records = dict(pending)
    now = datetime.now(timezone.utc)
    shard_id = uuid.uuid4().hex
    shard_key = store.key(
        _shard_prefix(store),
        now.strftime("%Y-%m-%d"),
        f"{now.strftime('%H%M%S')}-{shard_id}.json",
    )
    payload = {
        "schemaVersion": SHARD_SCHEMA,
        "createdAt": _utc_now(),
        "count": len(records),
        "records": records,
    }

    # 1) durable immutable data first.
    store.put_json(shard_key, payload, public=True, known_new=True)

    # 2) durable locator recovery journal second.
    journal_key = store.key(
        _locator_journal_prefix(store),
        f"{shard_id}.json",
    )
    journal = {
        "schemaVersion": INDEX_SCHEMA,
        "createdAt": _utc_now(),
        "shard": shard_key,
        "ids": list(records),
    }
    store.put_json(journal_key, journal, known_new=True)
    store._shard_locator_journal_keys.append(journal_key)  # type: ignore[attr-defined]

    for bot_id in records:
        _remember_locator(store, bot_id, shard_key)
    pending.clear()

    print(
        f"  discovery shard: {len(records):,} new bot records -> {shard_key}",
        flush=True,
    )
    return len(records)


def sharded_flush_discovery_journal(self: R2ArchiveStore) -> None:
    # The normal UUID journal is allowed to claim these IDs only after the shard
    # and its locator-recovery journal are durable.
    _flush_pending_shard(self)
    _BASE_FLUSH_DISCOVERY_JOURNAL(self)


def _flush_locator_indexes(store: R2ArchiveStore) -> int:
    _ensure_state(store)
    touched = sorted(store._shard_locator_touched)  # type: ignore[attr-defined]
    if not touched:
        return 0

    # Group locators once. The old code rescanned the entire locator map once per
    # touched prefix; with 256 prefixes and hundreds of thousands of bots that
    # became tens/hundreds of millions of Python-loop iterations at end-of-run.
    by_prefix: dict[str, dict[str, str]] = {prefix: {} for prefix in touched}
    touched_set = set(touched)
    for bot_id, shard_key in store._shard_locator.items():  # type: ignore[attr-defined]
        prefix = _prefix_for(bot_id)
        if prefix in touched_set:
            by_prefix[prefix][str(bot_id).lower()] = str(shard_key)

    written = 0
    for prefix in touched:
        # Merge the current durable public index before applying recovered/new
        # locator rows. Replaying a journal after a partial failure is idempotent.
        key = store.key(_index_prefix(store), f"{prefix}.json")
        prior = _BASE_GET_JSON(store, key, None)
        bots: dict[str, str] = {}
        if isinstance(prior, dict) and isinstance(prior.get("bots"), dict):
            bots.update({
                str(k).lower(): str(v)
                for k, v in prior["bots"].items()
                if k and v
            })

        bots.update(by_prefix.get(prefix, {}))

        store.put_json(
            key,
            {
                "schemaVersion": INDEX_SCHEMA,
                "updatedAt": _utc_now(),
                "prefix": prefix,
                "count": len(bots),
                "bots": bots,
            },
            public=True,
        )
        written += 1

    # Only remove recovery journals after every touched prefix index has written.
    journals = list(store._shard_locator_journal_keys)  # type: ignore[attr-defined]
    if journals:
        store._delete_keys(journals)
    store._shard_locator_journal_keys.clear()  # type: ignore[attr-defined]
    store._shard_locator_touched.clear()  # type: ignore[attr-defined]

    print(
        f"Discovery shards: published {written:,} touched prefix locator indexes; "
        f"retired {len(journals):,} recovery journal(s).",
        flush=True,
    )
    return written


def sharded_save_discovery_order(self: R2ArchiveStore, force: bool = False) -> None:
    # Flush the current page first, then make locators public, then let the normal
    # discovery-order compaction retire its own UUID recovery journals.
    self.flush_discovery_journal()
    _flush_locator_indexes(self)
    _BASE_SAVE_DISCOVERY_ORDER(self, force=force)


def sharded_save_bot(self: R2ArchiveStore, record: dict[str, Any]) -> bool:
    _ensure_state(self)
    bot_id = str(record["id"]).lower()

    if bot_id not in self.known_ids:
        # New discovery-only records are cheap immutable shard members. A later
        # save of the same UUID sees it as known and materializes the standalone
        # object through the existing accelerated save path.
        self._shard_pending_records[bot_id] = deepcopy(record)  # type: ignore[attr-defined]
        self.register_id(bot_id)
        self._records[bot_id] = deepcopy(record)
        while len(self._records) > self.cache_limit:
            self._records.popitem(last=False)
        return True

    return _MATERIALIZED_SAVE_BOT(self, record)


def install_discovery_shards() -> None:
    R2ArchiveStore.get_json = sharded_get_json
    R2ArchiveStore.load_discovery_order = sharded_load_discovery_order
    R2ArchiveStore.flush_discovery_journal = sharded_flush_discovery_journal
    R2ArchiveStore.save_discovery_order = sharded_save_discovery_order
    R2ArchiveStore.save_bot = sharded_save_bot
    print(
        "Archive discovery storage: immutable page shards + crash-safe prefix locators enabled.",
        flush=True,
    )


def main() -> int:
    install_discovery_shards()
    return optimized.main()


if __name__ == "__main__":
    raise SystemExit(main())
