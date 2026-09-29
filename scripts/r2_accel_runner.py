#!/usr/bin/env python3
"""Run selected archive entry points with safe runtime acceleration enabled."""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

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
