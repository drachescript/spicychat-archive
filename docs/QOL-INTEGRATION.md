# SpicyChat Archive ↔ DragonScript QoL interoperability

Archive is the **public, versioned evidence source**; QoL remains the
**opt-in, local filtering client**. Neither repo needs simultaneous releases.

Current consumers: QoL v0.2.86+ (**Community Themes**) and v0.2.87+
(**Archive Blocking Presets**).

## Read-only interfaces

- Community taxonomy: https://spicychatarchive.drache.uk/data/community-tags/taxonomy.json
- Community aliases: https://spicychatarchive.drache.uk/data/community-tags/aliases.json
- Community evidence manifest: https://data.spicychatarchive.drache.uk/indexes/community-tags/manifest.json
- Community batch API: POST https://spicychat-archive-import.dragongraf.workers.dev/api/community-tags/lookup with JSON `{"ids":["<bot-uuid>"]}`, at most 80 IDs.
- Preset manifest: https://spicychatarchive.drache.uk/data/block-presets/manifest.json
- Preset catalog: https://spicychatarchive.drache.uk/data/block-presets/catalog.json
- Preset reference matcher: https://spicychatarchive.drache.uk/assets/block-preset-matcher.js

## Versioning

- Bump both preset manifest and catalog `catalogVersion` for changes to
  preset JSON, names, labels, aliases, gender identities, or selections.
  QoL refreshes the manifest every ~6 hours when enabled and downloads
  the catalog only if its version changed.
- For updated community theme evidence, publish immutable shards first,
  then update the snapshot manifest. QoL caches by the manifest version.
- Do not silently change an existing API or JSON contract shape. Add
  regression tests and coordinate breaking changes with the dedicated
  QoL repository before publishing them.
- The Archive reference matcher and preview should match QoL's conservative
  gender-selective logic: require exactly one recognized canonical character;
  unknown/group/mixed/gender-swapped titles are kept.
- A bot with no matching Archive evidence is **UNKNOWN**, never proven safe.
  Historical tags are not current proof; title rules are unverified candidates.

## Safety and performance

Both QoL integrations start disabled. No per-card profile requests, no
automated native Block/Dislike/Less Like This, and no private conversations,
personas, authenticated Rulebook definitions, or user settings uploaded.
Submissions require review of publicly visible bot premise evidence.

Archive CI: `node --test --experimental-default-type=module tests/archive-qol-contract.test.mjs`

Scheduled GitHub workflow: **QoL public feed health check** tests the deployed
JSON and bot-ID lookup contract once per day and on relevant repository changes.
When reporting incompatibility, include the versions/snapshot, public bot UUID,
and expected hide/keep/warning action, but never private chat data.
