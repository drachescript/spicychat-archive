# Archive Blocking Presets (v1)

A separate, **opt-in** name/title blocking-preset catalog for use by DragonScript QoL and the Archive. This is a **preference tool**, not a community safety/accuracy finding. It complements but does not overwrite [Community Themes](../community-tags/README.md).

## Public files

- Manifest: https://spicychatarchive.drache.uk/data/block-presets/manifest.json
- Catalog: https://spicychatarchive.drache.uk/data/block-presets/catalog.json
- Schema: https://spicychatarchive.drache.uk/data/block-presets/schema.json
- Preview UI: https://spicychatarchive.drache.uk/presets/
- Reference matcher: https://spicychatarchive.drache.uk/assets/block-preset-matcher.js
- Weekly discovery (once populated): https://data.spicychatarchive.drache.uk/indexes/block-presets/discovery.json

The catalog contains `8` seeded media/franchise presets, `42` curated character identities, `2` groups, and `5` optional title-keyword theme presets. All are **OFF by default**. The Archive can add new presets/aliases after reviewing deterministic candidate discovery; newly observed strings are never silently trusted or applied.

## Independent options

- `all`: Hide strong franchise, group or recognized character matches, even if the bot's primary character is unknown.
- `keep-female`: Hide only recognized canonical male characters; keep recognized female and **unknown/mixed/gender-variant** titles.
- `keep-male`: Hide only recognized canonical female characters; keep recognized male and **unknown/mixed/gender-variant** titles.
- `custom`: Select `keepGenders` and `keepCharacterIds`; any kept character wins over other matched characters.
- A specific character or group can be offered as its own sub-selection in QoL.

**Do not infer bot gender from canonical character identity**, official `Male`/`Female` tags, franchise mentions, descriptions, or group membership. For example, `Call of Duty — female OC` is an unknown-character title and remains visible with `keep-female`. A title naming both a kept woman and an excluded man is kept by default. QoL v0.2.87 conservatively requires **exactly one** identified canonical character to hide in gender-selective modes; even two identified characters of the same excluded gender are kept, because their title may describe a mixed cast. The Archive reference matcher and tests follow the same behavior. Users can choose the stricter `all` mode if desired.

Use only bot **name/public title**, not a bot's biography, description or story supporting cast. Names like `Ghost`, `Soap`, `Price`, `Gaz`, `Marvel`, `Avatar`, and `BTS` require franchise/group context. Quotes, accent differences (König/Konig), case, punctuation and camelCase are normalized; entire tokens are matched (no substring of unrelated words). The seed list is manually curated, not a verified assertion that every fan-made bot reflects character canon.

The words `theory` and other unrelated ambiguous generic terms are **not preset triggers**. Very broad personal blocked terms (e.g., `BBC`, `the system`) should stay user-managed rather than automatically shipped to everyone.

## Versioning and optional discovery

Catalog version `1`; stable IDs and explicit `schemaVersion`. QoL should check the small manifest, fetch the catalog only on opt-in and on a changed catalogVersion, and cache locally; no per-card requests. Local matching uses the existing dirty-card pipeline, batching and on-demand modules. Failure to load presets must not affect manual blocked words, Community Themes, or other QoL functions.

Weekly discovery aggregates the existing 256 public Archive creator-index shards. It counts observed franchise/character alias patterns and prepares **review-only candidate phrases** from public titles; no million individual bot reads or automatic trusted alias additions. Candidate frequencies **do not prove** franchise membership. Title signals are public archival observations, including deleted or no-longer-public records; not a live count or full site coverage.

A reviewed alias/character addition should be committed to the catalog with regression cases and a version bump. Do not take reports as proof; require public evidence and allow corrections. No AI models, private chats, imported user blocked words, account details, or automatic permanent blocks / dislikes.
