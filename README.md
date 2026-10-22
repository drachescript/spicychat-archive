## Archive bot counts and audit evidence

The Archive reports three **different** populations: its cumulative observed/discovery-order bot IDs, the individual materialized bot records represented by the Archive's R2 text-search index, and SpicyChat's **current** public Typesense count. Subtracting the live public count and confirmed-gone Archive count from cumulative observed IDs gives an arithmetic difference **not an identifiable missing-bot list**. The explicit `missingChecks` queue covers only candidates found by sufficiently complete discovery passes, not every bot that could have vanished. Old last-seen dates also do **not** prove deletion.

- `scripts/publish_bot_status_audit.py` reads compact saved creator indexes daily to report materialized-index count, distinct indexed bot IDs, last-observed statuses, and stale observations. It does not label last-known public as verified-current.
- `scripts/audit_discovery_ids.py` runs weekly (and can run manually) to compare observed IDs to materialized text-index IDs, then does at most 10,000 read-only R2 object-existence checks for discovery-only IDs. Its report at `indexes/discovery-id-audit.json` contains exact IDs with no stored Archive bot file. A missing Archive file is **not** confirmation of a deleted SpicyChat bot.
- Neither audit deletes records or changes live/deleted status. Cleanup must first classify specific IDs and have recoverable evidence. The homepage and Stats label these distinctions instead of pretending the remaining difference consists of verified missing IDs.

# SpicyChat Archive

SpicyChat Archive is a public historical browser for publicly discoverable SpicyChat characters.

The website tracks public character listings over time, preserves previously observed public metadata when fields later become hidden or unavailable, records availability and ranking history, and keeps archived copies of public avatar images when available.

It provides a searchable catalog with sorting, creator and status filters, include/exclude tag filters, individual bot archive pages, and a dedicated view for bots confirmed as deleted or unavailable.

Live site: https://spicychatarchive.drache.uk/

Tag statistics: https://spicychatarchive.drache.uk/tag-stats/

The Tag Stats page counts last-known bot tag fields in the Archive's creator indexes, including previously archived/deleted bots. It provides exact label lookup, rare/popular tags, co-occurring tag pairs, creator breakdowns, and two-tag comparison. The daily stats workflow publishes a new snapshot of current Archive tag counts; trends start on the first successful publication (no fabricated past tag histories). These counts do not represent official tag availability, words in bot scenarios, current live sitewide usage, or audience preferences.

The Archive intentionally retains **historical tag labels** even if they no longer appear in SpicyChat's current character creator. As of the October 21, 2026 SpicyChat v4.7.2 picker capture, `Step-Family` is no longer visible in the 90-tag creator picker but remains in the Archive's 91-entry historical tag catalog and can still be used to filter archived records. Older bot tag values also remain recoverable from their recorded field-change history if later observations change the current tags. The Archive's tag-statistics counts refer to last-known indexed bot tags, not every past tag assignment.

SpicyChat Archive is an independent archival project and is not affiliated with SpicyChat.
