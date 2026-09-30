#!/usr/bin/env python3
"""Conservative guards for SpicyChat's public character endpoint.

Observed live behavior includes a deleted/unavailable bot returning HTTP 200 with
an empty JSON object, while the frontend immediately renders its 404 page. This
module normalizes that signature into the archive's existing repeated-missing
verification path and suppresses traffic-only timestamp churn.

It also treats the first rich character-API snapshot for an already archived bot
as baseline enrichment rather than a creator edit. The richer fields are saved,
but they are not backfilled into fieldHistory as though they changed at that
moment.
"""
from __future__ import annotations

from typing import Any

import archive as legacy


_INSTALLED = False
_ORIGINAL_CHARACTER = None
_ORIGINAL_OBSERVE = None

# These timestamps can move with activity/counter updates and are not authored
# bot content. Actual greeting/personality/scenario/etc. changes are still
# compared independently, so ignoring these timestamps does not hide edits.
_EXTRA_VOLATILE_FIELDS = {
    "updatedAt",
    "updated_at",
    "lastActivityAt",
    "last_activity_at",
    "lastMessageAt",
    "last_message_at",
}


def is_empty_character_payload(data: Any) -> bool:
    """Return True only for the observed explicit HTTP-200 empty-object signature."""
    return isinstance(data, dict) and len(data) == 0


def _has_character_api_baseline(record: dict[str, Any] | None) -> bool:
    if not isinstance(record, dict):
        return False
    current = record.get("current")
    if not isinstance(current, dict):
        return False
    return isinstance(current.get("character-api"), dict) and bool(current.get("character-api"))


def install_character_api_guard() -> None:
    """Install the endpoint normalization and first-baseline behavior once."""
    global _INSTALLED, _ORIGINAL_CHARACTER, _ORIGINAL_OBSERVE
    if _INSTALLED:
        return

    _INSTALLED = True
    legacy.VOLATILE_FIELDS.update(_EXTRA_VOLATILE_FIELDS)

    _ORIGINAL_CHARACTER = legacy.Client.character
    original_character = _ORIGINAL_CHARACTER

    def guarded_character(self, bot_id: str):
        response = original_character(self, bot_id)
        if response.ok and response.status == 200 and is_empty_character_payload(response.data):
            # Keep the archive's existing conservative repeated-missing machinery:
            # one observation only creates a suspect; a later run must confirm it.
            return legacy.HTTPResult(
                False,
                404,
                data=response.data,
                error="HTTP 200 empty character payload (frontend-unavailable signature)",
                url=response.url,
            )
        return response

    legacy.Client.character = guarded_character

    _ORIGINAL_OBSERVE = legacy.observe_bot
    original_observe = _ORIGINAL_OBSERVE

    def guarded_observe_bot(
        record: dict[str, Any] | None,
        incoming: dict[str, Any],
        *,
        source: str,
        at: str,
    ):
        first_character_baseline = (
            source == "character-api"
            and record is not None
            and not _has_character_api_baseline(record)
        )
        history_len = len((record or {}).get("fieldHistory") or [])

        updated, changed = original_observe(record, incoming, source=source, at=at)

        if first_character_baseline and changed:
            # Typesense and the character endpoint expose different field sets.
            # The first rich snapshot is new archival coverage, not evidence that
            # the creator edited every newly visible field at this timestamp.
            history = updated.get("fieldHistory")
            if isinstance(history, list) and len(history) > history_len:
                del history[history_len:]

            # The normal maintenance caller only saves when observe_bot reports a
            # change. Save the baseline here, then return False so its log does not
            # claim a creator/content edit occurred.
            legacy.save_bot(updated)
            return updated, False

        return updated, changed

    legacy.observe_bot = guarded_observe_bot

    print(
        "Character API guard: HTTP 200 {} is treated as a repeated-missing candidate; "
        "activity timestamps are ignored; first rich snapshots are baseline enrichment.",
        flush=True,
    )
