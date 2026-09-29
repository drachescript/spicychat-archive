# SpicyChat Archive acceleration patch

Changed/new files only:

- `.github/workflows/archive.yml`
- `scripts/r2_accel_runner.py`
- `scripts/r2_runtime_accel.py`

## What it does

- Runs independent R2 bot JSON PUTs with 8 concurrent workers.
- Keeps quota/storage accounting synchronous and keeps non-bot writes as ordering barriers.
- Serializes repeated writes to the same bot so last-write ordering is preserved.
- Treats newly registered Bot Status imports as known-new objects, avoiding unnecessary R2 HEAD requests.
- Uses Typesense `multi_search` to prefetch 4 consecutive page searches per HTTP request when page-mode discovery/ranking allows it.
- Leaves cursor-mode discovery sequential because the next cursor depends on the previous result.
- Existing R2 operation/storage guards, discovery page budgets, 60-minute ceiling, enrichment budgets and image budgets are unchanged.

## Expected log lines

At the beginning of accelerated import/archive steps:

`Archive acceleration: 8 concurrent R2 bot writers; Typesense prefetch batch 4.`

At write barriers you should also see lines similar to:

`parallel R2 bot writes: 240 completed with 8 workers in X.Xs`

The main thing to compare against the previous run is the ~35-45 seconds that a mostly-new 250-bot exploration page used to spend ingesting. It should drop substantially if R2 accepts the concurrency cleanly.

No archive schema/storage format changes are included in this patch.
