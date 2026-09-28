# Public Bot Status submission + admin approval patch

This patch keeps the existing trusted `/api/imports/bot-status` route unchanged
and adds a separate public review-first submission path.

Changed/new files:

- `worker/src/index.js`
- `worker/wrangler.toml`
- `scripts/review_bot_status_submissions.py`
- `.github/workflows/archive.yml`
- `.github/workflows/pages.yml`
- `admin/submissions/index.html`
- `assets/submissions-admin.css`
- `assets/submissions-admin.js`
- `tests/test_public_bot_status_submissions.py`
- `PUBLIC-BOT-STATUS-SUBMISSIONS.md`

After committing:

1. Re-run `Deploy Bot Status import endpoint` once so the Worker gets the new
   public/admin routes and daily rejected-payload cleanup cron.
2. Let `Deploy archive website` publish `/admin/submissions/`.
3. Public QoL builds can POST anonymous saved-copy exports to:
   `https://spicychat-archive-import.dragongraf.workers.dev/api/submissions/bot-status`
4. Do not put the archive import token in public QoL builds. The public route
   does not need it.
