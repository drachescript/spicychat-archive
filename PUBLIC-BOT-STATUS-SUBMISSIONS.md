# Public QoL Bot Status submissions

This adds a public, anonymous review-first path alongside the existing private
trusted import route.

## Routes

Trusted/private route — unchanged:

`POST https://spicychat-archive-import.dragongraf.workers.dev/api/imports/bot-status`

Public anonymous route:

`POST https://spicychat-archive-import.dragongraf.workers.dev/api/submissions/bot-status`

Admin review API:

`GET/POST https://spicychat-archive-import.dragongraf.workers.dev/api/admin/submissions...`

The admin API uses the SAME `SPICYCHAT_ARCHIVE_IMPORT_TOKEN` Worker secret as
the trusted direct-import route. There is no second admin token.

## Public QoL request

Headers:

- `Content-Type: application/gzip`
- `Content-Encoding: gzip`
- `X-Install-Id: <random local installation UUID>`
- optional `X-Import-Filename`
- optional `X-Exported-At`

Do NOT send the admin/import token on the public route.

Preferred body before gzip:

```json
{
  "schemaVersion": 1,
  "kind": "spicychat-qol-bot-status-public-submission",
  "exportedAt": "2026-09-28T22:00:00Z",
  "savedCopies": [
    {
      "botId": "character-uuid",
      "savedAt": "2026-09-20T12:34:56Z",
      "observedStatus": "public",
      "snapshot": {
        "character_id": "character-uuid",
        "name": "Example",
        "title": "Example title",
        "creator_username": "creator",
        "tags": ["Fantasy"],
        "avatar_url": "https://cdn.nd-api.com/...",
        "personality": "...",
        "greeting": "...",
        "scenario": "..."
      }
    }
  ]
}
```

The Worker sanitizes the submission again server-side. Only whitelisted bot
profile/snapshot fields are retained. SpicyChat account details, usernames for
the submitting user, chats, favorites, personas, cookies, extension settings
and arbitrary unknown fields are never retained.

The raw local install ID is not stored. The Worker hashes it with SHA-256 and
uses only the hash for a basic six-submissions-per-24-hours anti-spam limit.

Limits:

- 16 MiB compressed public request
- 64 MiB decompressed JSON
- 5,000 bot snapshots per submission
- 2 MiB maximum per sanitized bot snapshot

If a public QoL export has more than 5,000 saved copies, split it into chunks.

## Response

Accepted submissions receive HTTP 202:

```json
{
  "ok": true,
  "pendingReview": true,
  "submissionId": "...",
  "botCount": 1234,
  "malformedDropped": 0,
  "problemRecords": 0,
  "bytes": 123456
}
```

This only means "queued for admin review". It does NOT mean the snapshots were
added to the live archive.

## Review flow

1. Public submission is privacy-sanitized and placed in pending R2 storage.
2. A normal scheduled archive run analyzes pending submissions.
3. `/admin/submissions/` shows:
   - new
   - known/same
   - changed/fills archive gaps
   - exact duplicate
   - unavailable-history warning
   - malformed/problem
4. Admin may:
   - Approve all safe
   - Approve selected
   - Reject
5. Accepted snapshots are copied into the EXISTING Bot Status import queue.
6. The existing importer merges them on the next archive run.

Submitted status is review metadata only. Public/deleted/private/etc. from a
submitter can never override the archive's current status or deletion rules.

Rejected sanitized payloads are retained for about seven days, then a Worker
cron deletes them. A small rejection decision record remains.

## Admin page

`https://spicychatarchive.drache.uk/admin/submissions/`

The page contains no token. Enter the existing archive import token in the
browser. It is kept in session storage by default. "Remember token" stores it
locally on that browser only.
