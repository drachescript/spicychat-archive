#!/usr/bin/env python3
"""Run selected archive entry points with safe runtime acceleration enabled."""
from __future__ import annotations

import os
import runpy
import sys
from copy import deepcopy
from pathlib import Path

import archive as legacy
from character_api_guard import install_character_api_guard
from r2_cleanup_accel import install_cleanup_acceleration
from r2_runtime_accel import flush_all_stores, install_runtime_acceleration
from typesense_retry import install_typesense_retry


HERE = Path(__file__).resolve().parent
TARGETS = {
    # The same maintenance runner handles automatic newest-first runs and
    # explicitly requested manual deep sweeps without rewriting config.json.
    "archive": HERE / "r2_archive_maintenance.py",
    "import": HERE / "import_bot_status.py",
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _manual_full_maintenance_requested() -> bool:
    return os.environ.get(
        "SPICYCHAT_ARCHIVE_MANUAL_FULL_MAINTENANCE", ""
    ).strip().lower() in {"1", "true", "yes", "on"}


def _apply_manual_archive_profile(
    config: dict,
    requested: int,
    *,
    full_maintenance: bool = False,
    catalog_audit: bool = False,
) -> tuple[dict, int, int]:
    """Build the in-memory config used by workflow_dispatch archive runs."""
    config = deepcopy(config)
    crawler = config.setdefault("crawler", {})
    configured_max = max(
        int(crawler.get("explore_pages_per_run") or 1),
        int(crawler.get("explore_pages_max") or 1),
    )
    target = min(configured_max, max(1, int(requested)))
    crawler["explore_pages_per_run"] = target
    crawler["manual_discovery_run"] = True
    # Every manual sweep gets a generous discovery window, while larger sweeps
    # receive proportionally more time. These are only in-memory manual-run
    # overrides; scheduled latest-only maintenance and R2 quotas are unchanged.
    # The GitHub job timeout is higher than these discovery timers so the
    # crawler can checkpoint and publish results before its runner is stopped.
    if target >= 2500:
        # Keep extra room for retries and variable R2 checkpoint latency.
        crawler["explore_time_limit_seconds"] = max(
            int(crawler.get("explore_time_limit_seconds") or 4200), 16200
        )
        crawler["manual_request_delay_seconds"] = 0.30
    elif target >= 1500:
        crawler["explore_time_limit_seconds"] = max(
            int(crawler.get("explore_time_limit_seconds") or 4200), 12600
        )
        crawler["manual_request_delay_seconds"] = 0.15
    elif target >= 1000:
        crawler["explore_time_limit_seconds"] = max(
            int(crawler.get("explore_time_limit_seconds") or 4200), 9000
        )
    else:
        # Even smaller/manual runs should not hit the regular 70m cutoff
        # during unusually slow API or R2 checkpoint conditions.
        crawler["explore_time_limit_seconds"] = max(
            int(crawler.get("explore_time_limit_seconds") or 4200), 7200
        )

    # Manual workflow_dispatch runs are primarily used to advance the deep
    # Typesense sweep. Scheduled runs already perform the 5,000-bot rolling
    # Character API verification and image maintenance every six hours, so
    # repeating those jobs after a 500-1,000 page manual sweep wastes ~30m.
    # Keep a manual escape hatch for the rare run that intentionally wants both.
    if not full_maintenance or catalog_audit:
        crawler["maintenance_verification_enabled"] = False
        crawler["image_archive_enabled"] = False

    if catalog_audit:
        # Finish the saved wildcard cursor, record changed listing metadata,
        # and only after reaching a natural end verify suspects from the pass.
        # Limit API probes per run; never trust listing absence as a deletion.
        crawler["catalog_audit_run"] = True
        crawler["missing_verification_budget"] = 2000

    return config, target, configured_max


def _apply_missing_verification_profile(config: dict, *, budget: int = 10000) -> dict:
    """Inspect a bounded suspect-ID batch without touching catalog cursors."""
    config = deepcopy(config)
    crawler = config.setdefault("crawler", {})
    crawler["verify_missing_only"] = True
    crawler["scheduled_latest_only"] = True
    crawler["maintenance_verification_enabled"] = False
    crawler["image_archive_enabled"] = False
    crawler["missing_verification_budget"] = max(1, min(10000, int(budget)))
    # Keep listing *configuration* for the published runtime/sort selectors.
    # maintenance_scan_listing() skips the actual requests for this profile.
    return config


def _install_missing_verification_profile() -> None:
    original_load_config = legacy.load_config

    def load_missing_verification_profile():
        config = _apply_missing_verification_profile(
            original_load_config(),
            budget=_env_int("SPICYCHAT_ARCHIVE_VERIFY_BUDGET", 10000),
        )
        print(
            "Missing-ID verification only: no Typesense scan, no deep-discovery "
            "cursor movement, no image downloads, no rolling enrichment. "
            f"Max {config['crawler']['missing_verification_budget']:,} "
            "Character API checks; R2 guard enforced.",
            flush=True,
        )
        return config

    legacy.load_config = load_missing_verification_profile


def _apply_scheduled_archive_profile(config: dict) -> dict:
    """Newest-first + existing maintenance; preserve all manual deep-sweep settings."""
    config = deepcopy(config)
    crawler = config.setdefault("crawler", {})
    crawler["scheduled_latest_only"] = True
    # The newest listing has an independent known-overlap stop. Leave the
    # ranking lists untouched in R2; manual deep-discovery runs still refresh
    # them. We merge the prior ranking snapshot before publishing.
    listings = config.get("listings") or {}
    if "latest" not in listings:
        raise ValueError("Automatic archive profile needs a 'latest' listing")
    config["listings"] = {"latest": listings["latest"]}
    return config


def _install_scheduled_archive_profile() -> None:
    original_load_config = legacy.load_config

    def load_scheduled_profile():
        config = _apply_scheduled_archive_profile(original_load_config())
        print(
            "Scheduled archive: newest bots until 3 known-overlap pages, "
            "then rolling Character API verification and missing checks; "
            "deep Typesense sweep skipped (manual discovery remains available).",
            flush=True,
        )
        return config

    legacy.load_config = load_scheduled_profile


def _install_manual_archive_target() -> None:
    """Apply a discovery-focused profile to this manual workflow process."""
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
    catalog_audit = os.environ.get(
        "SPICYCHAT_ARCHIVE_CATALOG_AUDIT", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    full_maintenance = _manual_full_maintenance_requested() and not catalog_audit

    def load_config_with_manual_target():
        config, target, configured_max = _apply_manual_archive_profile(
            original_load_config(),
            requested,
            full_maintenance=full_maintenance,
            catalog_audit=catalog_audit,
        )
        # Manual runs are intentionally one-off. The checked-in config remains
        # at its normal scheduled target, and no config file is rewritten here.
        suffix = (
            "catalog completion: current public pass, then bounded missing-ID checks"
            if catalog_audit else
            "full maintenance enabled" if full_maintenance else
            "discovery-only: rolling verification + images skipped"
        )
        print(
            f"Manual discovery target: {target} pages for this run "
            f"(configured max {configured_max}; {suffix}).",
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
    install_cleanup_acceleration(
        head_workers=_env_int("SPICYCHAT_ARCHIVE_R2_CLEANUP_WORKERS", 16),
    )
    if target_name == "archive":
        # Normalize the character endpoint before maintenance starts. SpicyChat
        # can return HTTP 200 with {} for a bot whose frontend is already 404;
        # this feeds that signature into the same repeated-missing safeguards as
        # an ordinary 404 and suppresses activity-only update noise.
        install_character_api_guard()
        # Install this after the batching/maintenance wrapper so retries repeat
        # the exact same logical Typesense request, including failover.
        install_typesense_retry()
        if os.environ.get("SPICYCHAT_ARCHIVE_VERIFY_MISSING", "").strip().lower() in {
            "1", "true", "yes", "on",
        }:
            _install_missing_verification_profile()
        elif os.environ.get("SPICYCHAT_ARCHIVE_SCHEDULED_LATEST_ONLY", "").strip().lower() in {
            "1", "true", "yes", "on",
        }:
            _install_scheduled_archive_profile()
        else:
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
