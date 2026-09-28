# Archive-side Bot Status Center import

Files in this patch:

- `.github/workflows/archive.yml` — processes queued exports before each R2 crawl.
- `.github/workflows/deploy-import-worker.yml` — manual Cloudflare Worker deploy.
- `worker/wrangler.toml`
- `worker/src/index.js` — authenticated upload/staging endpoint.
- `scripts/import_bot_status.py` — safe historical merge into R2.
- `tests/test_bot_status_import.py`
- `BOT-STATUS-IMPORT.md` — extension/upload contract and setup.

Important safety behavior:
- The upload token is never committed.
- Only public character/profile fields are accepted.
- Imported copies cannot mark bots public/deleted on their own.
- Old copies can fill missing archive fields but cannot overwrite newer known values.
- Imported bots are gradually verified/enriched by the normal archive crawler.
