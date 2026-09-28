#!/usr/bin/env python3
"""Cloudflare R2 storage helpers for SpicyChat Archive.

R2 is used for the large, permanent part of the archive (full bot records,
archived images, crawler state and ranking snapshots). Git stays small and only
contains the website, code and tiny generated runtime manifests.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import mimetypes
import os
import uuid
import threading
from datetime import datetime, timezone
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


class StorageQuotaExceeded(RuntimeError):
    """Raised before an R2 write would exceed the configured free-tier safety cap."""


class BloomFilter:
    """Compact pass-membership tracker used instead of millions of UUIDs in state.json."""

    def __init__(self, size_bytes: int = 8 * 1024 * 1024, hashes: int = 7, data: bytes | None = None):
        self.size_bytes = max(1024, int(size_bytes))
        self.hashes = max(2, int(hashes))
        self.bits = bytearray(data if data is not None else b"\x00" * self.size_bytes)
        if len(self.bits) != self.size_bytes:
            resized = bytearray(b"\x00" * self.size_bytes)
            resized[: min(len(self.bits), self.size_bytes)] = self.bits[: self.size_bytes]
            self.bits = resized

    @property
    def bit_count(self) -> int:
        return self.size_bytes * 8

    def _positions(self, value: str):
        digest = hashlib.blake2b(value.encode("utf-8"), digest_size=16).digest()
        h1 = int.from_bytes(digest[:8], "big")
        h2 = int.from_bytes(digest[8:], "big") or 0x9E3779B185EBCA87
        n = self.bit_count
        for i in range(self.hashes):
            yield (h1 + i * h2 + i * i) % n

    def add(self, value: str) -> None:
        for pos in self._positions(value):
            self.bits[pos >> 3] |= 1 << (pos & 7)

    def __contains__(self, value: str) -> bool:
        for pos in self._positions(value):
            if not (self.bits[pos >> 3] & (1 << (pos & 7))):
                return False
        return True

    def clear(self) -> None:
        self.bits[:] = b"\x00" * self.size_bytes

    def to_bytes(self) -> bytes:
        return bytes(self.bits)


class R2ArchiveStore:
    """Small S3-compatible object layer around a single R2 bucket."""

    REQUIRED_ENV = (
        "CLOUDFLARE_R2_ACCOUNT_ID",
        "CLOUDFLARE_R2_ACCESS_KEY_ID",
        "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
    )

    def __init__(self, config: dict[str, Any]):
        storage = config.get("storage") or {}
        r2 = storage.get("r2") or {}
        self.config = config
        self.bucket = str(r2.get("bucket") or "spicychat-archive")
        self.public_base_url = str(r2.get("public_base_url") or "").rstrip("/")
        self.meta_prefix = str(r2.get("meta_prefix") or "_meta").strip("/")
        self.bot_prefix = str(r2.get("bot_prefix") or "bots").strip("/")
        self.media_prefix = str(r2.get("media_prefix") or "media").strip("/")
        self.rankings_prefix = str(r2.get("rankings_prefix") or "rankings").strip("/")
        self.cache_seconds = int(r2.get("cache_seconds") or 300)
        self.cache_limit = int(r2.get("memory_cache_records") or 25000)
        # Keep a deliberate buffer below Cloudflare R2's 10 GB-month free storage tier.
        self.hard_limit_bytes = int(r2.get("hard_limit_bytes") or 9_000_000_000)
        self.warning_bytes = int(r2.get("warning_bytes") or 8_000_000_000)
        self.media_limit_bytes = int(r2.get("media_limit_bytes") or 2_000_000_000)
        self.usage_checkpoint_writes = max(25, int(r2.get("usage_checkpoint_writes") or 250))
        self._usage_lock = threading.Lock()
        self._usage: dict[str, Any] | None = None
        self._usage_dirty_writes = 0

        account_id = os.environ.get("CLOUDFLARE_R2_ACCOUNT_ID", "").strip()
        access = os.environ.get("CLOUDFLARE_R2_ACCESS_KEY_ID", "").strip()
        secret = os.environ.get("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "").strip()
        if not account_id or not access or not secret:
            raise RuntimeError("Cloudflare R2 credentials are incomplete")

        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=15,
                read_timeout=45,
            ),
        )

        self._records: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.discovery_order: list[str] = []
        self.known_ids: set[str] = set()
        self.discovery_dirty = False
        self.deleted_index: dict[str, dict[str, Any]] = {}
        self.deleted_dirty = False
        self._new_ids_journal: list[str] = []
        self._journal_keys: list[str] = []

    @classmethod
    def credentials_present(cls) -> bool:
        return all(bool(os.environ.get(k, "").strip()) for k in cls.REQUIRED_ENV)

    def key(self, *parts: str) -> str:
        return "/".join(str(p).strip("/") for p in parts if str(p).strip("/"))

    def _missing(self, exc: ClientError) -> bool:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        status = (exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        return code in {"NoSuchKey", "404", "NotFound"} or status == 404


    @property
    def usage_key(self) -> str:
        return self.key(self.meta_prefix, "storage-usage.json")

    @staticmethod
    def _month_key() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _head_size(self, key: str) -> int:
        try:
            obj = self.s3.head_object(Bucket=self.bucket, Key=key)
            return int(obj.get("ContentLength") or 0)
        except ClientError as exc:
            if self._missing(exc):
                return 0
            raise

    def _normalize_usage(self, usage: dict[str, Any] | None) -> dict[str, Any]:
        u = dict(usage or {})
        u.setdefault("schemaVersion", 1)
        u["totalBytes"] = max(0, int(u.get("totalBytes") or 0))
        u["mediaBytes"] = max(0, int(u.get("mediaBytes") or 0))
        u["objects"] = max(0, int(u.get("objects") or 0))
        month = self._month_key()
        if u.get("month") != month:
            u["month"] = month
            u["writesThisMonth"] = 0
        else:
            u["writesThisMonth"] = max(0, int(u.get("writesThisMonth") or 0))
        return u

    def recalculate_usage(self) -> dict[str, Any]:
        """Rebuild byte/object totals from R2. This is only needed at setup/recovery."""
        total = media = objects = 0
        token = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.s3.list_objects_v2(**kwargs)
            for obj in page.get("Contents") or []:
                key = str(obj.get("Key") or "")
                if key == self.usage_key:
                    continue
                size = int(obj.get("Size") or 0)
                total += size
                objects += 1
                if key.startswith(self.media_prefix.rstrip("/") + "/"):
                    media += size
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        prior = self._normalize_usage(self.get_json(self.usage_key, {}))
        self._usage = {
            "schemaVersion": 1,
            "totalBytes": total,
            "mediaBytes": media,
            "objects": objects,
            "month": self._month_key(),
            "writesThisMonth": int(prior.get("writesThisMonth") or 0),
        }
        self.flush_usage(force=True)
        return deepcopy(self._usage)

    def storage_usage(self, *, reconcile_if_missing: bool = True) -> dict[str, Any]:
        if self._usage is not None:
            return deepcopy(self._usage)
        data = self.get_json(self.usage_key, None)
        if isinstance(data, dict) and "totalBytes" in data:
            self._usage = self._normalize_usage(data)
        elif reconcile_if_missing:
            return self.recalculate_usage()
        else:
            self._usage = self._normalize_usage({})
        return deepcopy(self._usage)

    def flush_usage(self, *, force: bool = False) -> None:
        if self._usage is None:
            return
        if not force and self._usage_dirty_writes < self.usage_checkpoint_writes:
            return
        payload = self._normalize_usage(self._usage)
        payload["hardLimitBytes"] = self.hard_limit_bytes
        payload["warningBytes"] = self.warning_bytes
        payload["mediaLimitBytes"] = self.media_limit_bytes
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # Usage metadata is deliberately excluded from its own byte counter.
        self.s3.put_object(
            Bucket=self.bucket,
            Key=self.usage_key,
            Body=raw,
            ContentType="application/json; charset=utf-8",
            CacheControl="no-store",
        )
        self._usage = payload
        self._usage_dirty_writes = 0

    def _reserve_usage(self, *, key: str, new_size: int, old_size: int, category: str) -> tuple[int, int, bool]:
        usage = self.storage_usage()
        delta = int(new_size) - int(old_size)
        media_delta = delta if category == "media" else 0
        new_total = max(0, int(usage["totalBytes"]) + delta)
        new_media = max(0, int(usage["mediaBytes"]) + media_delta)
        if new_total > self.hard_limit_bytes:
            raise StorageQuotaExceeded(
                f"R2 safety cap reached: write to {key} would use {new_total:,} bytes "
                f"(hard cap {self.hard_limit_bytes:,})."
            )
        if category == "media" and new_media > self.media_limit_bytes:
            raise StorageQuotaExceeded(
                f"R2 media cap reached: write to {key} would use {new_media:,} media bytes "
                f"(media cap {self.media_limit_bytes:,})."
            )
        was_new = old_size == 0
        with self._usage_lock:
            # Re-check against the newest in-process reservation to stay safe with
            # migration's parallel upload workers.
            u = self._normalize_usage(self._usage)
            total2 = max(0, int(u["totalBytes"]) + delta)
            media2 = max(0, int(u["mediaBytes"]) + media_delta)
            if total2 > self.hard_limit_bytes:
                raise StorageQuotaExceeded(f"R2 safety cap reached at {total2:,} bytes.")
            if category == "media" and media2 > self.media_limit_bytes:
                raise StorageQuotaExceeded(f"R2 media cap reached at {media2:,} bytes.")
            u["totalBytes"] = total2
            u["mediaBytes"] = media2
            if was_new:
                u["objects"] = int(u["objects"]) + 1
            u["writesThisMonth"] = int(u.get("writesThisMonth") or 0) + 1
            self._usage = u
            self._usage_dirty_writes += 1
        return delta, media_delta, was_new

    def _rollback_usage(self, delta: int, media_delta: int, was_new: bool) -> None:
        with self._usage_lock:
            if self._usage is None:
                return
            self._usage["totalBytes"] = max(0, int(self._usage.get("totalBytes") or 0) - delta)
            self._usage["mediaBytes"] = max(0, int(self._usage.get("mediaBytes") or 0) - media_delta)
            if was_new:
                self._usage["objects"] = max(0, int(self._usage.get("objects") or 0) - 1)
            self._usage["writesThisMonth"] = max(0, int(self._usage.get("writesThisMonth") or 0) - 1)
            self._usage_dirty_writes = max(0, self._usage_dirty_writes - 1)

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if self._missing(exc):
                return False
            raise

    def get_bytes(self, key: str, default: bytes | None = None) -> bytes | None:
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=key)
            body = obj["Body"].read()
            if str(obj.get("ContentEncoding") or "").lower() == "gzip":
                body = gzip.decompress(body)
            return body
        except ClientError as exc:
            if self._missing(exc):
                return default
            raise

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        gzip_content: bool = False,
        cache_control: str | None = None,
        category: str = "data",
        known_new: bool = False,
    ) -> None:
        body = gzip.compress(data, compresslevel=6) if gzip_content else data
        old_size = 0 if known_new else self._head_size(key)
        delta, media_delta, was_new = self._reserve_usage(
            key=key, new_size=len(body), old_size=old_size, category=category
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
        try:
            self.s3.put_object(**kwargs)
        except Exception:
            self._rollback_usage(delta, media_delta, was_new)
            raise
        self.flush_usage()

    def get_json(self, key: str, default: Any = None) -> Any:
        raw = self.get_bytes(key)
        if raw is None:
            return deepcopy(default)
        return json.loads(raw.decode("utf-8"))

    def put_json(
        self, key: str, data: Any, *, public: bool = False, category: str = "data", known_new: bool = False
    ) -> None:
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.put_bytes(
            key,
            raw,
            content_type="application/json; charset=utf-8",
            gzip_content=True,
            cache_control=(f"public,max-age={self.cache_seconds}" if public else "no-store"),
            category=category,
            known_new=known_new,
        )

    @property
    def marker_key(self) -> str:
        return self.key(self.meta_prefix, "migrated.json")

    @property
    def state_key(self) -> str:
        return self.key(self.meta_prefix, "state.json")

    @property
    def discovery_key(self) -> str:
        return self.key(self.meta_prefix, "discovery-order.txt")

    @property
    def deleted_key(self) -> str:
        return self.key(self.meta_prefix, "deleted-index.json")

    @property
    def discovery_journal_prefix(self) -> str:
        return self.key(self.meta_prefix, "discovery-journal") + "/"

    @property
    def bloom_key(self) -> str:
        return self.key(self.meta_prefix, "exploration.bloom")

    @property
    def ranking_latest_key(self) -> str:
        return self.key(self.meta_prefix, "rankings-latest.json")

    def is_migrated(self) -> bool:
        return self.exists(self.marker_key)

    def mark_migrated(self, payload: dict[str, Any]) -> None:
        self.put_json(self.marker_key, payload)

    def load_state(self) -> dict[str, Any]:
        state = self.get_json(self.state_key, {"schemaVersion": 2})
        if not isinstance(state, dict):
            state = {"schemaVersion": 2}
        state["schemaVersion"] = max(2, int(state.get("schemaVersion") or 0))
        # Old local mode queues are intentionally not carried into R2. Sequential
        # cursors replace unbounded new-bot queues.
        state.pop("enrichmentQueue", None)
        state.pop("imageQueue", None)
        state.setdefault("priorityEnrichment", [])
        state.setdefault("priorityImages", [])
        state.setdefault("enrichmentCursor", 0)
        state.setdefault("imageCursor", 0)
        exploration = state.setdefault("exploration", {})
        exploration.pop("seenThisPass", None)
        return state

    def save_state(self, state: dict[str, Any]) -> None:
        clean = deepcopy(state)
        clean.pop("enrichmentQueue", None)
        clean.pop("imageQueue", None)
        (clean.get("exploration") or {}).pop("seenThisPass", None)
        self.put_json(self.state_key, clean)

    def _list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.s3.list_objects_v2(**kwargs)
            keys.extend(obj["Key"] for obj in page.get("Contents") or [])
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        return keys

    def load_discovery_order(self) -> None:
        raw = self.get_bytes(self.discovery_key, b"") or b""
        self.discovery_order = [x.strip().lower() for x in raw.decode("utf-8").splitlines() if x.strip()]
        self.discovery_order = list(dict.fromkeys(self.discovery_order))
        self.known_ids = set(self.discovery_order)
        self.discovery_dirty = False

        # If a workflow died after bot objects were written but before the compact
        # discovery-order object was saved, page-level journals recover those IDs.
        self._journal_keys = self._list_keys(self.discovery_journal_prefix)
        for key in self._journal_keys:
            journal = self.get_bytes(key, b"") or b""
            for line in journal.decode("utf-8").splitlines():
                bot_id = line.strip().lower()
                if bot_id and bot_id not in self.known_ids:
                    self.known_ids.add(bot_id)
                    self.discovery_order.append(bot_id)
                    self.discovery_dirty = True

    def register_id(self, bot_id: str) -> bool:
        bot_id = str(bot_id).lower()
        if bot_id in self.known_ids:
            return False
        self.known_ids.add(bot_id)
        self.discovery_order.append(bot_id)
        self._new_ids_journal.append(bot_id)
        self.discovery_dirty = True
        return True

    def flush_discovery_journal(self) -> None:
        if not self._new_ids_journal:
            return
        key = self.key(self.discovery_journal_prefix, f"{uuid.uuid4().hex}.txt")
        raw = ("\n".join(self._new_ids_journal) + "\n").encode("utf-8")
        self.put_bytes(key, raw, content_type="text/plain; charset=utf-8", gzip_content=True, cache_control="no-store")
        self._journal_keys.append(key)
        self._new_ids_journal.clear()

    def _delete_keys(self, keys: list[str]) -> None:
        for i in range(0, len(keys), 1000):
            chunk = keys[i:i + 1000]
            if not chunk:
                continue
            sizes = [(key, self._head_size(key)) for key in chunk]
            self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True})
            with self._usage_lock:
                if self._usage is not None:
                    for key, size in sizes:
                        self._usage["totalBytes"] = max(0, int(self._usage.get("totalBytes") or 0) - size)
                        if key.startswith(self.media_prefix.rstrip("/") + "/"):
                            self._usage["mediaBytes"] = max(0, int(self._usage.get("mediaBytes") or 0) - size)
                        if size:
                            self._usage["objects"] = max(0, int(self._usage.get("objects") or 0) - 1)
                    self._usage_dirty_writes += 1
            self.flush_usage()

    def save_discovery_order(self, force: bool = False) -> None:
        self.flush_discovery_journal()
        if not (force or self.discovery_dirty or self._journal_keys):
            return
        raw = ("\n".join(self.discovery_order) + ("\n" if self.discovery_order else "")).encode("utf-8")
        self.put_bytes(self.discovery_key, raw, content_type="text/plain; charset=utf-8", gzip_content=True, cache_control="no-store")
        if self._journal_keys:
            self._delete_keys(self._journal_keys)
            self._journal_keys.clear()
        self.discovery_dirty = False

    def load_deleted_index(self) -> None:
        data = self.get_json(self.deleted_key, {})
        self.deleted_index = data if isinstance(data, dict) else {}
        self.deleted_dirty = False

    def save_deleted_index(self, force: bool = False) -> None:
        if not (force or self.deleted_dirty):
            return
        self.put_json(self.deleted_key, self.deleted_index)
        self.deleted_dirty = False

    def set_deleted_summary(self, bot_id: str, summary: dict[str, Any]) -> None:
        if self.deleted_index.get(bot_id) != summary:
            self.deleted_index[bot_id] = summary
            self.deleted_dirty = True

    def clear_deleted_summary(self, bot_id: str) -> None:
        if bot_id in self.deleted_index:
            self.deleted_index.pop(bot_id, None)
            self.deleted_dirty = True

    def load_bloom(self, *, size_bytes: int = 8 * 1024 * 1024, hashes: int = 7) -> BloomFilter:
        raw = self.get_bytes(self.bloom_key)
        return BloomFilter(size_bytes=size_bytes, hashes=hashes, data=raw)

    def save_bloom(self, bloom: BloomFilter) -> None:
        self.put_bytes(self.bloom_key, bloom.to_bytes(), content_type="application/octet-stream", gzip_content=False, cache_control="no-store")

    def bot_key(self, bot_id: str) -> str:
        compact = str(bot_id).replace("-", "").lower()
        prefix = compact[:2] if len(compact) >= 2 else "__"
        return self.key(self.bot_prefix, prefix, f"{bot_id}.json")

    def public_url(self, key: str) -> str | None:
        if not self.public_base_url:
            return None
        return f"{self.public_base_url}/{key.lstrip('/')}"

    def load_bot(self, bot_id: str) -> dict[str, Any] | None:
        bot_id = str(bot_id).lower()
        if self.known_ids and bot_id not in self.known_ids:
            return None
        if bot_id in self._records:
            value = self._records.pop(bot_id)
            self._records[bot_id] = value
            return deepcopy(value)
        data = self.get_json(self.bot_key(bot_id), None)
        if not isinstance(data, dict):
            return None
        self._records[bot_id] = data
        while len(self._records) > self.cache_limit:
            self._records.popitem(last=False)
        return deepcopy(data)

    def save_bot(self, record: dict[str, Any]) -> bool:
        bot_id = str(record["id"]).lower()
        is_new = bot_id not in self.known_ids
        self.put_json(self.bot_key(bot_id), record, public=True, category="bot", known_new=is_new)
        self.register_id(bot_id)
        self._records[bot_id] = deepcopy(record)
        while len(self._records) > self.cache_limit:
            self._records.popitem(last=False)
        return True

    def media_key_for(self, content: bytes, content_type: str, source_url: str = "") -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        ext = mimetypes.guess_extension(content_type) or Path(urlparse(source_url).path).suffix or ".img"
        if ext == ".jpe":
            ext = ".jpg"
        key = self.key(self.media_prefix, digest[:2], f"{digest}{ext}")
        return key, digest

    def save_image(self, content: bytes, content_type: str, source_url: str = "") -> dict[str, Any]:
        key, digest = self.media_key_for(content, content_type, source_url)
        if not self.exists(key):
            self.put_bytes(
                key,
                content,
                content_type=content_type,
                gzip_content=False,
                cache_control="public,max-age=31536000,immutable",
                category="media",
                known_new=True,
            )
        return {"key": key, "sha256": digest, "publicUrl": self.public_url(key)}

    def save_ranking_snapshot(self, snapshot: dict[str, Any], timestamp_slug: str) -> None:
        key = self.key(self.rankings_prefix, timestamp_slug[:8], f"{timestamp_slug}.json")
        self.put_json(key, snapshot, public=False)
        self.put_json(self.ranking_latest_key, snapshot, public=False)

    def load_latest_ranking(self) -> dict[str, Any]:
        data = self.get_json(self.ranking_latest_key, {})
        return data if isinstance(data, dict) else {}

    def publish_deleted_index(self, *, force: bool = False) -> str | None:
        key = self.key("indexes", "deleted.json")
        # This public index is a Class A write. Do not rewrite the same deleted
        # list every three hours just to refresh cache metadata. During normal
        # runs deleted_dirty is set only when the list genuinely changes.
        if not force and not self.deleted_dirty:
            return self.public_url(key)
        rows = list(self.deleted_index.values())
        rows.sort(key=lambda x: str(x.get("statusSince") or ""), reverse=True)
        self.put_json(key, {"bots": rows}, public=True)
        return self.public_url(key)
