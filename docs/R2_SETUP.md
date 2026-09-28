# Cloudflare R2 setup

SpicyChat Archive can move its large permanent data out of Git and into Cloudflare R2. The website and code remain on GitHub Pages; R2 holds full bot records, archived avatars, crawler state, deleted-bot index data and ranking snapshots.

## 1. Create the bucket

In Cloudflare Dashboard open **Storage & databases → R2 → Overview** and create a Standard bucket named:

`spicychat-archive`

## 2. Add the public data domain

Open the bucket → **Settings → Custom Domains** and connect:

`data.spicychatarchive.drache.uk`

The bucket is public archive data, so this custom domain is intentional.

## 3. Add CORS

The GitHub Pages site loads bot JSON from the R2 custom domain. Configure the bucket CORS policy to allow GET/HEAD from the site:

```json
[
  {
    "AllowedOrigins": ["https://spicychatarchive.drache.uk"],
    "AllowedMethods": ["GET", "HEAD"],
    "ExposeHeaders": ["Content-Type", "Content-Encoding", "ETag"],
    "MaxAgeSeconds": 86400
  }
]
```

## 4. Create R2 S3 credentials

From R2, open **Manage R2 API Tokens** and create credentials with Object Read & Write access to the `spicychat-archive` bucket.

Add these GitHub repository Actions secrets:

- `CLOUDFLARE_R2_ACCOUNT_ID`
- `CLOUDFLARE_R2_ACCESS_KEY_ID`
- `CLOUDFLARE_R2_SECRET_ACCESS_KEY`

The bucket name and public custom domain are already in `config.json`; only the credentials belong in GitHub Secrets.

## 5. Run the migration once

In GitHub Actions manually run:

**Migrate archive to Cloudflare R2**

The migration uploads the current full bot records, archived images and ranking history first. It writes the R2 migration marker only after those uploads succeed. It then removes the duplicated large archive directories from the Git working tree and performs one R2-backed archive scan.

After the migration commit lands, `config.json` is changed from `storage.mode = "auto"` to `storage.mode = "r2"`. Future archive runs fail safely if the R2 credentials are missing instead of silently starting a new local archive.

## Storage layout

The main R2 object prefixes are:

- `bots/<prefix>/<uuid>.json` — complete per-bot archival record, gzip encoded
- `media/<prefix>/<sha256>.<ext>` — deduplicated archived avatars
- `rankings/...` — compressed ranking snapshots
- `indexes/deleted.json` — public deleted-bot index used by the website
- `_meta/...` — crawler state, discovery order, compact exploration Bloom filter and migration marker

Git retains only code, website files and small public runtime/manifest data.
