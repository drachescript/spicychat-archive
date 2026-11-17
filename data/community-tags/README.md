# Archive Community Tags & Theme Signals (v1)

The public catalog covers **all currently known** bot, Lorebook and Rulebook tag names, including retired Archive bot tags. It does **not** claim that every archived bot has complete public data or that every theme can be inferred from its title.

## Stable JSON interfaces

- [Catalog manifest](https://spicychatarchive.drache.uk/data/community-tags/manifest.json)
- [Taxonomy](https://spicychatarchive.drache.uk/data/community-tags/taxonomy.json) — every known official tag across the three scopes, plus explicitly marked custom themes
- [Aliases](https://spicychatarchive.drache.uk/data/community-tags/aliases.json) — synonyms, not proof of a premise
- [Detection rules](https://spicychatarchive.drache.uk/data/community-tags/rules.json) — conservative, explicit **title-only** candidate hints
- [Reviewed decisions](https://spicychatarchive.drache.uk/data/community-tags/reviews.json) — human-review decisions, initially empty
- [Response schema](https://spicychatarchive.drache.uk/data/community-tags/schema.json)
- Dynamic index: `https://data.spicychatarchive.drache.uk/indexes/community-tags/manifest.json`
- Lookup API: `POST https://spicychat-archive-import.dragongraf.workers.dev/api/community-tags/lookup` with `{"ids":["<bot-uuid>"]}` (max 80 bot IDs).

The dynamic index is built from the Archive's **256 existing creator-index shards**, including public and no-longer-public archived bot metadata. It does not issue a million+ full-bot GETs. Every shard is content-addressed; the manifest is written **last**, and unchanged outputs are reused. The worker looks up a whole batch of UUIDs by shard. QoL should call it only with an explicit opt-in, cache results, and default to **UNKNOWN** when it cannot fetch data.

## Meaning of signals

- `historical-tag`: a tag was previously observed, but it is **not** in the last-known tag list; does **not** prove the tag remains applicable
- `rule-candidate`: a deterministic phrase was found in the archived **public title**; the bot may be misclassified, so never state this is verified
- `reviewed`: an approved human decision based on publicly available evidence, with its provenance and review timestamp

A rejected review suppresses corresponding automatic candidates only; it cannot erase the truth that a historical tag was once observed. Old reviews are invalidated when the referenced public metadata fingerprint changes. No private chats, unlisted definitions, personal data, or "learning" from someone's RP.

## Learn without AI

The system improves by collecting **reviewed** false positives, missing themes, exceptions and rule proposals. Only repository-reviewed edits to `reviews.json` or `rules.json` can change public classifications. Test the rules against known positive and negative cases before publishing. Official tags are observations, not proof; no automatic dislikes, blocks, or punitive action.

## Scope and limits

The taxonomy includes known tags for bots, Lorebooks and Rulebooks, but bot-ID evidence currently derives from creator-indexed **bot** metadata. Lorebook and Rulebook ID evidence is a separate future index. Unknown bots, missing descriptions, private content and incomplete Archive coverage are not safe or negative classifications. Bot availability is last observed, not a live 404 check.
