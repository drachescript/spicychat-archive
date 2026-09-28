# SpicyChat Archive — discovery + website + stats update

Replace/merge these files at the repository root.

Main changes:
- Adaptive discovery: starts at 20 pages, +1 after each healthy successful run, max 50.
- Discovery has a 60-minute wall-clock ceiling and resumes from its saved cursor.
- Quota guard now supplies a ceiling rather than forcing discovery back to 20.
- Fingerprint v4 ignores updatedAt, ranking counters and backend noise.
- New bots are prioritized for rich character-API enrichment.
- updatedAt no longer creates field-history noise during R2 runs.
- Full scan/growth history stored at R2 `_meta/stats-history.json`.
- Small public `data/stats.json` updated after successful runs.
- New /stats page with growth, recent runs, discovery depth and R2 stats.
- Browse + Deleted redesigned around a SpicyChat-like sidebar/filter/card layout.
- All 90 current supported tags included from the supplied Create Bot HTML.
- NTR + Cheating excluded by default, but visitors can remove either exclusion.
- Latest sort removed from the site.
- Bot profiles prefer the live SpicyChat CDN image for public bots and archived R2 copy for deleted bots.
- Old migrated avatar paths are reconstructed against the R2 public domain instead of GitHub.
- Bot profiles include an “Open in SpicyChat” link.

The first run after installing this patch will rebuild fingerprint schema v4 once.
