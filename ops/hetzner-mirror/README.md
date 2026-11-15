# Hetzner Windows mirror — phase 1

This is a one-way local mirror of the existing SpicyChat Archive Cloudflare R2
bucket, designed for a Windows-based Hetzner server. The crawler, website and
all read/write traffic continue using R2 until a separate migration is tested.

**Mirroring does not reduce storage used in R2.** Do not delete objects from
the source until we have a verified full backup and reversible fallback path.

## Scope

The mirror preserves the object bytes and paths under bots/, discovery/shards/,
indexes/, media/, and _meta/ in the bucket named spicychat-archiv. Some JSON
objects are gzip-encoded within R2, so this is a RAW object mirror: do not
assume every .json file can be opened directly in Notepad or PostgreSQL without
decompression. Metadata indexing in PostgreSQL can be added after the mirror
is complete.

Last measured R2 had about 753,000 objects and 5.5 GB. Windows NTFS overhead
for this many files may be considerable. Choose a dedicated drive/folder with
at least 12 GB free, preferably more for future growth.

## Install and configure locally

1. Install rclone on the Hetzner Windows server:
   https://rclone.org/downloads/
2. Create a dedicated Object Read-only R2 API token scoped ONLY to the
   spicychat-archiv bucket. Do not reuse the crawler's write credential.
3. In the Windows account that will run the mirror, use rclone config:
   remote name: r2
   backend: S3; provider: Cloudflare; region: auto
   endpoint: https://YOUR_ACCOUNT_ID.r2.cloudflarestorage.com
   access key ID and secret key: enter locally (never in GitHub).
   If using a token limited to object-level access, set no_check_bucket=true.
4. Test listing without downloading:

    rclone lsf "r2:spicychat-archiv" --max-depth 1

## Preview and start

Run this from the repository checkout, replacing D: with a suitable data
drive. No -Apply means the script defaults to a DRY RUN:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\ops\hetzner-mirror\mirror-r2.ps1" -Destination "D:\SpicyChatArchive\R2"

Then perform the real one-way copy:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\ops\hetzner-mirror\mirror-r2.ps1" -Destination "D:\SpicyChatArchive\R2" -Apply

It uses rclone copy, NOT sync. Source objects are never modified, and older
local objects are never deleted. On a later run, rclone only copies new/changed
objects. Interrupted runs can be repeated. The script uses server modtime to
avoid extra per-object HEAD requests, 8 simultaneous transfers, a request-rate
limit, retries, a free-space check and an exclusive lock.

Logs and machine-readable completion status are kept in
DESTINATION\.mirror-control\last-run.json and dated mirror-*.log files.

## Task Scheduler

Under Create Task:
- General: use the same Windows account configured with rclone; choose
  "Run whether user is logged on or not".
- Triggers: once every 12 hours and optionally at startup; enable
  "Run task as soon as possible after a scheduled start is missed".
- Action: Program = powershell.exe
  Arguments = -NoProfile -ExecutionPolicy Bypass -File "C:\path\to\repo\ops\hetzner-mirror\mirror-r2.ps1" -Destination "D:\SpicyChatArchive\R2" -Apply
- Settings: If already running, do not start a new instance.

If using a different task account or SYSTEM, provide the -RcloneConfig
parameter pointing to a protected local rclone.conf; the Administrator
user's default rclone config is not available automatically to SYSTEM.

Do not expose the mirrored _meta directory or raw objects through an
unauthenticated HTTP server. R2 is still the authoritative copy.

## Next phase (not enabled by this patch)

Verify the copy using a sampling and eventual full manifest/checksums, then
build an optional Hetzner API and PostgreSQL JSONB metadata index. Use R2 as
fallback on Windows Updates outages. Only after a reversible read switch and
successful recovery test may selected R2 objects be migrated and deleted,
which is when R2 storage would actually decrease.

## R2 storage guard

The config threshold values use decimal bytes:
- 9,400,000,000 bytes: normal R2 pause
- 9,200,000,000 bytes: resume after a storage-caused pause
- 9,300,000,000 bytes: storage warning
- 9,500,000,000 bytes: hard stop for R2 object writes
- Class A pauses at 950,000 operations; the 5 GB image-pause is unchanged.

Cloudflare R2 Standard's free storage is 10 GB-month (averaged over the
billing period), not an instantaneous 10 GB safety guarantee. Keep monitoring
billing and do not assume these thresholds prevent every possible charge.
