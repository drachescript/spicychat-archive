#!/usr/bin/env python3
"""Run selected archive entry points with safe runtime acceleration enabled."""
from __future__ import annotations

import os
import runpy
import sys
from copy import deepcopy
from pathlib import Path

import archive as legacy
from r2_runtime_accel import flush_all_stores, install_runtime_acceleration


HERE = Path(__file__).resolve().parent
TARGETS = {
    "archive": HERE / "r2_archive_sharded.py",
    "import": HERE / "import_bot_status.py",
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _install_manual_archive_target() -> None:
    """Raise only this process's base discovery target for a manual test run."""
    raw = os.environ.get("SPICYCHAT_ARCHIVE_MANUAL_PAGES", "").strip()
    if not raw:
        return
    try:
        requested = int(raw)
    except ValueError:
        return
    if requested <= 0:
        return

    original_load_config = legacy.load_config

    def load_config_with_manual_target():
        config = deepcopy(original_load_config())
        crawler = config.setdefault("crawler", {})
        configured_max = max(
            int(crawler.get("explore_pages_per_run") or 1),
            int(crawler.get("explore_pages_max") or 1),
        )
        target = min(configured_max, requested)
        crawler["explore_pages_per_run"] = target
        # Manual tests are intentionally one-off. The checked-in config remains
        # at its normal scheduled target, and no config file is rewritten here.
        print(
            f"Manual discovery target: {target} pages for this run "
            f"(configured max {configured_max}).",
            flush=True,
        )
        return config

    legacy.load_config = load_config_with_manual_target


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in TARGETS:
        print(
            "Usage: r2_accel_runner.py <archive|import> [target arguments...]",
            file=sys.stderr,
        )
        return 2

    target_name = sys.argv[1]
    target = TARGETS[target_name]
    target_args = sys.argv[2:]

    install_runtime_acceleration(
        write_workers=_env_int("SPICYCHAT_ARCHIVE_R2_WRITE_WORKERS", 8),
        typesense_batch=_env_int("SPICYCHAT_ARCHIVE_TYPESENSE_BATCH_SIZE", 4),
    )
    if target_name == "archive":
        _install_manual_archive_target()

    old_argv = sys.argv[:]
    code = 0
    uncaught: BaseException | None = None
    try:
        sys.argv = [str(target), *target_args]
        try:
            runpy.run_path(str(target), run_name="__main__")
        except SystemExit as exc:
            if exc.code is None:
                code = 0
            elif isinstance(exc.code, int):
                code = exc.code
            else:
                print(str(exc.code), file=sys.stderr)
                code = 1
        except BaseException as exc:
            uncaught = exc
    finally:
        sys.argv = old_argv
        try:
            flush_all_stores()
        except BaseException as exc:
            if uncaught is None:
                print(f"ERROR flushing parallel R2 writes: {exc}", file=sys.stderr)
                code = 2
            else:
                print(
                    f"ERROR flushing parallel R2 writes after target failure: {exc}",
                    file=sys.stderr,
                )

    if uncaught is not None:
        raise uncaught
    return code


if __name__ == "__main__":
    raise SystemExit(main())
