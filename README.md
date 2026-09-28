# SpicyChat Archive

A public historical archive and browser for public SpicyChat characters.

The project is intentionally **archive-first**: once a real public value has been observed, a later scan cannot erase it merely because SpicyChat stops returning the field. Current observations, last-known-good values, field history, availability history, metrics, rankings and archived avatars are kept separately.

## Core behavior

- Refreshes public **Trending**, **Top Rated**, **Popular** and **Latest** discovery views every 3 hours.
- Continues wildcard Typesense exploration after the obvious listings so the catalog can grow beyond the current front-page set.
- Uses the public SpicyChat Typesense search key shipped to clients; no user account token, cookies or private authentication are required.
- Queues newly discovered characters for enrichment through the public `v2/characters/<id>` endpoint.
- Preserves a previously seen Personality / Scenario / Greeting / other field if a later response hides, omits or empties it.
- Does not call a bot deleted merely because it vanished from a listing. Deletion requires repeated explicit `404` results from the public character endpoint after the bot has become suspect.
- Archives avatar bytes gradually and keeps the original CDN URL plus SHA-256 provenance.
- Generates a static GitHub Pages website with a dedicated **Deleted bots** page, bot detail pages, include/exclude tag filters, creator/status filters, search and sorting.

## Public Typesense configuration

The default configuration mirrors the SpicyChat client/QoL setup:

- load-balanced endpoint: `https://ts-lb.nd-api.com/multi_search`
- fallback: `https://etmzpxgvnid370fyp.a1.typesense.net/multi_search`
- collection: `public_characters_alias`
- query fields: `name,title,tags,creator_username,character_id,type`

The public search key is stored once in `config.json`. `SPICYCHAT_TYPESENSE_API_KEY` can override it without changing code.

## Run locally

```bash
python -m pip install -r requirements.txt
python scripts/probe.py
python scripts/archive.py
python -m unittest discover -s tests -v
```

`probe.py` is non-destructive. It writes `probe-report.json` and checks the raw field set, total wildcard result count, pagination around the 2,500 boundary, configured listing sorts, several public character endpoint responses, the known deleted Raptor Pack Handler fixture, and its old CDN avatar.

The normal crawler writes archival data under `archive/` and generated website data under `site/data/`.

## GitHub Actions

`Update SpicyChat archive` runs at minute 37 every 3 hours. It scans, explores, enriches, archives images, rebuilds the static site data, runs tests, then commits only actual archive changes.

`Probe SpicyChat public APIs` is manual and uploads `probe-report.json` as an Actions artifact.

`Deploy archive website` publishes the `site/` directory through GitHub Pages after site changes are pushed. In the repository Pages settings, use **GitHub Actions** as the deployment source.

## Data layout

```text
archive/
  state.json                crawler frontier and queues
  bots/ab/<uuid>.json       canonical per-bot archival record
  rankings/                 changed ranking snapshots only
  media/ab/<sha256>.*       deduplicated archived avatar bytes

site/
  index.html                public catalog
  deleted/index.html        confirmed deleted bots
  bot/index.html            full per-bot archive viewer
  data/catalog/*.json       generated compact catalog shards
  data/bots/<uuid>.json     generated full bot records
  media/                    deploy copy of archived media
```

Bot records deliberately distinguish:

- `current`: the latest raw observation from each source;
- `lastKnown`: monotonic last-known-good public data;
- `fieldHistory`: value changes and hidden/missing transitions;
- `availabilityHistory`: public/deleted/restored transitions;
- `metrics`: latest values plus bounded historical observations;
- `avatarArchive`: original URL, SHA-256, stored file and archive time.

## Listing definitions

The QoL extension confirms SpicyChat's default discovery sort uses `num_messages_24h:desc` and Latest uses `createdAt:desc`. The initial archive configuration uses `num_messages:desc` for Popular and `rating_score:desc,num_messages:desc` for Top Rated. The probe workflow exists specifically so these can be compared against live public behavior and adjusted without changing the archival model.

## Important archive rule

A later empty, omitted or hidden field is **state information**, not permission to destroy the older archived value.

For example, if Personality is public on Monday and hidden on Tuesday, Tuesday's record can say that Personality is no longer exposed, while `lastKnown.personality` still contains Monday's public value and `fieldHistory` records when it disappeared.
