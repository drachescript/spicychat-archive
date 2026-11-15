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

Run this from the repository checkout on the Hetzner Windows server. The
default destination is E:\Bots\Spicychat_Archive\R2; -Destination overrides
it. No -Apply means the script defaults to a DRY RUN:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\ops\hetzner-mirror\mirror-r2.ps1" -Destination "E:\Bots\Spicychat_Archive\R2"

Then perform the real one-way copy:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\ops\hetzner-mirror\mirror-r2.ps1" -Destination "E:\Bots\Spicychat_Archive\R2" -Apply

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
  Arguments = -NoProfile -ExecutionPolicy Bypass -File "C:\path\to\repo\ops\hetzner-mirror\mirror-r2.ps1" -Destination "E:\Bots\Spicychat_Archive\R2" -Apply
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


## Phase 2 groundwork: R2 hot tier (~8 GB) + Hetzner cold tier

The first phase remains non-destructive. The new hybrid policy file is
ops/hetzner-mirror/hybrid-policy.json. It currently has mode=observe-only,
mirrorVerified=false, offloadEnabled=false and deleteR2Objects=false.

Intended future topology:
- R2 remains the fast first-choice data source; target occupancy ~8 decimal GB.
- Hetzner E:\Bots\Spicychat_Archive\R2 is the independent bulk copy, with an
  authenticated read API and optional PostgreSQL JSONB metadata/search index.
- The static GitHub Pages website keeps one bot URL. Future routing will first
  consult a verified object-location manifest; a cold object can be fetched from
  Hetzner, while a hot object stays in R2.
- Windows Update outages must not cause permanent loss. Before offloading, test
  an R2 fallback copy or an alternate recovery replica for each cold object.
  Deleting the sole R2 copy of a cold record does NOT make it available during
  a Hetzner outage. Design the reader with this limitation explicitly.
- NEVER remove shared discovery shards or mutable _meta/, indexes/, rankings/
  on a per-bot basis. Only individual, immutable bots/<prefix>/<uuid>.json
  are candidates in the initial cold tier; shared shards need an atomic plan.
- Existing avatar URLs stay as URLs. Continue priority-only preservation for
  deleted/lost avatars; no bulk downloading of ordinary bot pictures.

The existing 9.4 GB Cloudflare pause and 9.5 GB hard cap are unchanged. The
8 GB figure is a TARGET for later offloading, not a new pause that would stop
the live crawler while Hetzner is still unconfigured.

### Anonymous bot popularity tracking

On a successful archive bot-detail render, assets/app.js now sends ONE
anonymized bot-ID view event per browser tab roughly every 30 minutes via the
existing Cloudflare import Worker. It does not track search queries or lists.
The browser respects Global Privacy Control and Do Not Track, and the Worker
stores only a bot UUID and a count in Workers Analytics Engine (dataset
sca_bot_views_v1). No IP, account, browser fingerprint, cookies or browsing
history fields are written to that dataset. It is an advisory popularity
signal, not an exact count of unique humans; client-side events can be blocked,
sampled or spoofed.

Worker configuration is in worker/wrangler.toml:
  [[analytics_engine_datasets]]
  binding = "BOT_VIEWS_ANALYTICS"
  dataset = "sca_bot_views_v1"

Before deploying the Worker binding, **enable Workers Analytics Engine in
the Cloudflare dashboard** under Workers & Pages > Analytics Engine for the
account that owns spicychat-archive-import. The first deployment with this
binding failed with Cloudflare error 10089 ("You need to enable Analytics
Engine"). This requires an account-owner dashboard action, not a code patch
or a token pasted into the repository. Do not post secrets in a chat.

After enabling it, rerun the failed GitHub Action:
https://github.com/drachescript/spicychat-archive/actions/workflows/deploy-import-worker.yml
The normal deploy-import-worker.yml workflow deploys this Worker. Verify
the job succeeds and the deployed Worker responds with botViewEndpoint in
its /health JSON. The Analytics Engine dataset is created on its first
successful write. Until that deployment succeeds, page views are not
collected (the website remains usable).

### Export a PRIVATE 30-day popularity table

Cloudflare Analytics Engine retains about three months of events; export or
roll up counts periodically if we want historical comparisons beyond that.
The script only reads aggregated per-bot counts. It requires a separate
Cloudflare API token granting Account Analytics Read, provided locally (never
in GitHub, a browser, or a chat). On Hetzner PowerShell:

~~~powershell
$env:CLOUDFLARE_R2_ACCOUNT_ID = "YOUR_CLOUDFLARE_ACCOUNT_ID"
$env:CLOUDFLARE_ANALYTICS_READ_TOKEN = "TOKEN_ENTERED_LOCALLY"
python .\ops\hetzner-mirror\export-bot-views.py --days 30 --output "E:\Bots\Spicychat_Archive\analytics\bot-views-30d.csv"
Remove-Item Env:\CLOUDFLARE_ANALYTICS_READ_TOKEN
~~~

The output is deliberately outside the repository. Do not commit analytics
data or API tokens. This script uses the Analytics Engine SQL API, groups by
bot UUID and correctly applies Cloudflare's sampling weight. It fails closed
on incomplete or malformed query results instead of silently producing a
misleading cold list.

### Review cold candidates (no mutations)

Do not run a cold candidate report until we have at least 30 ACTUAL days of
working telemetry and a successfully completed mirror. Record the date the
first production bot-view event was confirmed, not the PR merge date:

~~~powershell
python .\ops\hetzner-mirror\plan-cold-records.py --mirror "E:\Bots\Spicychat_Archive\R2" --views-csv "E:\Bots\Spicychat_Archive\analytics\bot-views-30d.csv" --tracking-started-at YYYY-MM-DD --output "E:\Bots\Spicychat_Archive\analytics\cold-candidates.json"
~~~

This is a read-only REPORT, not a migration. The default candidate rule is:
a local individual bot JSON file whose last modified time is at least 90
days ago and which received at most 2 recorded detail views in 30 days.
These are provisional heuristic thresholds. A local file's age is NOT proof
of when the bot was first seen, and absent analytics counts are NOT proof of
no visitors. Exclude protected recently deleted bots, mutable objects and
shared discovery shards from all actual offloads.

The report does not verify that the candidate is still in R2 and does not
prove the mirror copy is intact. Before implementing automated moves, compare
current R2 inventory, hashed mirror files, shared-shard dependencies and all
existing public fetch paths. Do not use this report as a deletion manifest.

### Final migration gate (still BLOCKED)

Only when the mirror and backups are verified should we implement:
1. A private Hetzner read service with health checks and a fixed HTTPS domain;
   no direct exposure of raw _meta/ state or the PostgreSQL database.
2. A versioned object-location index, explicit R2/Hetzner/fallback storage
   states, and updated bot detail/search/Recovery Lab readers.
3. A restore/failover test during a simulated Hetzner outage, including shard
   reads, deleted bot records and non-deleted images.
4. An explicit dry-run plan matching current R2 object sizes, and an atomic,
   bounded offloader with verified restore, rate limiting and rollback.
5. Only then opt in to deletions. Do not automatically delete based solely
   on a low-view score, and retain a separate recovery copy for cold objects.

No Hetzner API, PostgreSQL server, rclone task, DNS change, or actual object
migration is installed by this PR. Those require server access and user
configuration later.

Documentation:
- https://developers.cloudflare.com/workers/examples/analytics-engine/
- https://developers.cloudflare.com/analytics/analytics-engine/sql-api/
- https://developers.cloudflare.com/analytics/analytics-engine/limits/
