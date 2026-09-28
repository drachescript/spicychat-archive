# v0.5 runtime / stats cleanup

This patch fixes the two site issues exposed by the first adaptive scheduled run.

- Fixes the archive workflow's masked `git add` failure. A missing optional `fallback` path caused the whole multi-path `git add` to fail, then `|| true` hid the failure. This is why generated `runtime.json`, `stats.json`, manifest updates and R2-mode cleanup were never committed.
- Adds a real `data/runtime.json` immediately, plus a frontend R2 fallback so a transient missing runtime file cannot blank the whole site.
- Adds the `stats/` directory to the GitHub Pages trigger and Pages artifact. The file existed in the repository, but the deploy workflow never copied it, which caused `/stats/` to 404.
- Seeds `data/stats.json` with the completed scheduled-run numbers. The next successful archive run will extend it from the full R2 `_meta/stats-history.json`.
- In R2 mode the Pages artifact now strips old `data/bots`, `data/catalog`, and root `media` copies. The next successful archive run will also commit their Git cleanup because the staging bug is fixed.
