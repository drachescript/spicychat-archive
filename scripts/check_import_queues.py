#!/usr/bin/env python3
"""Fast, read-only check for work in the two QoL/Bot Status import queues.

Import-only runs skip expensive archive state loads and index publication when
both queues are empty. Unknown R2 errors must fail rather than report empty.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import archive as legacy
from import_bot_status import list_inbox
from review_bot_status_submissions import _list_pending_meta
from storage_r2 import R2ArchiveStore


def check_queues(store: R2ArchiveStore) -> tuple[bool, bool]:
    """Return whether private imports or unanalyzed public submissions exist."""
    private_pending = bool(list_inbox(store, 1))
    public_pending = bool(_list_pending_meta(store, 1))
    return private_pending, public_pending


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--github-output", help="GitHub Actions output file")
    args = ap.parse_args()

    if not R2ArchiveStore.credentials_present():
        raise RuntimeError("R2 credentials missing; cannot safely check import queues")
    config = legacy.load_config()
    if str((config.get("storage") or {}).get("mode") or "").lower() != "r2":
        raise RuntimeError("Archive is not using R2; cannot check import queues")

    store = R2ArchiveStore(config)
    if not store.is_migrated():
        raise RuntimeError("R2 archive migration marker missing")

    private_pending, public_pending = check_queues(store)
    has_work = private_pending or public_pending
    print(
        "Import-only preflight: "
        f"private exports {'queued' if private_pending else 'empty'}; "
        f"public submissions {'awaiting analysis' if public_pending else 'empty'}; "
        f"{'continue' if has_work else 'nothing queued, finish early'}.",
        flush=True,
    )
    if args.github_output:
        path = Path(args.github_output)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"has_work={'true' if has_work else 'false'}\n")
            f.write(f"private_pending={'true' if private_pending else 'false'}\n")
            f.write(f"public_pending={'true' if public_pending else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
