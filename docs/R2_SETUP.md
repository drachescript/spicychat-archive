# Cloudflare R2 setup — SpicyChat Archive

The archive uses Cloudflare R2 only while the account is comfortably inside the Standard free tier. A quota guard queries Cloudflare's account-wide R2 analytics before every 3-hour run. If storage, Class A operations, or Class B operations enter the safety reserve, new observations go to compact gzip journals in Git instead. When R2 has enough headroom again, those journals are replayed into the permanent R2 archive automatically.

## Bucket used by this repository

The existing Cloudflare bucket is named:

`spicychat-archiv`

The repository config intentionally uses that exact spelling.

Use **Standard** storage and the normal/automatic location. Do not switch the bucket to Infrequent Access: Cloudflare's R2 monthly free tier applies only to Standard storage.

## 1. Connect the public data hostname

In Cloudflare:

1. Storage & databases → R2 Object Storage.
2. Open `spicychat-archiv`.
3. Open **Settings**.
4. Under **Public access → Custom Domains**, choose **Connect Domain**.
5. Enter `data.spicychatarchive.drache.uk` and confirm. Cloudflare creates the DNS record for the R2 bucket.
6. You do not need the `r2.dev` development URL for the site.

The public site remains `https://spicychatarchive.drache.uk/`; the R2 custom domain is only the data/media origin.

## 2. Add the bucket CORS policy

Still in the bucket's **Settings**, under **CORS Policy**, choose **Add CORS policy**, open the JSON editor, and use:

```json
[
  {
    "AllowedOrigins": ["https://spicychatarchive.drache.uk"],
    "AllowedMethods": ["GET", "HEAD"],
    "ExposeHeaders": ["Content-Type", "Content-Encoding", "ETag", "CF-Cache-Status"],
    "MaxAgeSeconds": 86400
  }
]
```

Save it. If the custom domain was already serving cached objects before CORS was added, purge that hostname once after saving CORS.

## 3. Add a Cache Rule for the R2 hostname

JSON is not cached by Cloudflare automatically just because it is on an R2 custom domain. Caching reduces Class B R2 reads.

In the `drache.uk` zone:

1. Go to **Caching → Cache Rules → Create rule**.
2. Name it `SpicyChat Archive R2`.
3. Match **Hostname equals `data.spicychatarchive.drache.uk`**.
4. Set **Cache eligibility → Eligible for cache**.
5. Set an **Edge TTL** of about **1 hour**.
6. Save/deploy the rule.

A one-hour edge cache is fine because the crawler itself only runs every three hours. Archived image objects are content-addressed and can safely remain cached longer, but one rule is enough to start.

## 4. Create the R2 S3 credentials

These credentials let GitHub Actions read/write archive objects.

1. Go to **R2 Object Storage → Overview**.
2. Under **Account Details / API Tokens**, choose **Manage**.
3. Create an Account or User R2 API token.
4. Permission: **Object Read & Write**.
5. Scope it to the specific bucket **`spicychat-archiv`** only.
6. Create the token.
7. Copy both values immediately: **Access Key ID** and **Secret Access Key**. Cloudflare does not show the secret again.

Also copy your Cloudflare **Account ID**. The Python S3 client uses the endpoint `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`.

## 5. Create the Analytics token for the free-tier guard

This is a separate normal Cloudflare API token. It does not write R2 objects. It only reads the same R2 analytics datasets used by the Cloudflare dashboard so the workflow can stop before the free allowance is approached.

1. Open **Account → API Tokens → Create Token**.
2. Choose **Create Custom Token**.
3. Name it something like `SpicyChat Archive R2 Usage Guard`.
4. Permission: **Account → Account Analytics → Read**.
5. Scope the account resource to the Cloudflare account that owns `spicychat-archiv`.
6. Create and copy the token value.

## 6. Add four GitHub Actions secrets

In GitHub open `drachescript/spicychat-archive` → **Settings → Secrets and variables → Actions → New repository secret** and add:

- `CLOUDFLARE_R2_ACCOUNT_ID` = your Cloudflare Account ID
- `CLOUDFLARE_R2_ACCESS_KEY_ID` = the R2 Access Key ID
- `CLOUDFLARE_R2_SECRET_ACCESS_KEY` = the R2 Secret Access Key
- `CLOUDFLARE_API_TOKEN` = the Account Analytics Read token

Do not put the secret key or Analytics token into `config.json`.

## 7. Run the one-time migration

After all four secrets exist:

1. GitHub → **Actions**.
2. Open **Migrate archive to Cloudflare R2**.
3. Choose **Run workflow**.
4. The workflow first checks current account-wide R2 usage. If the analytics token is missing, invalid, or the free-tier safety reserve is already too small, migration fails closed before uploading anything.
5. If safe, it uploads the current permanent archive to R2, switches `config.json` to R2 mode, removes the large current Git copies, and performs a deliberately small R2-backed scan.

After that, the normal `Update SpicyChat archive` workflow handles everything automatically every three hours.

## Automatic quota behavior

Cloudflare Standard R2 currently includes 10 GB-month storage, 1,000,000 Class A operations/month, and 10,000,000 Class B operations/month. The archive intentionally switches earlier than those limits:

- R2 storage writes pause around **8.0 GB current footprint** and only resume if actual footprint falls below **7.5 GB**.
- R2 writes pause around **700,000 Class A operations** and resume after a monthly reset / when usage is below **500,000**.
- R2-backed crawler activity and site archive reads pause around **6,000,000 Class B operations**, with normal R2 mode resuming below **4,000,000**.
- New permanent image copies stop even earlier, at about **5 GB** of total R2 storage. The original SpicyChat CDN URL stays archived.
- Missing/failed Cloudflare usage analytics are treated as unsafe after migration (`fail_closed`), so the crawler uses Git fallback rather than guessing.

While R2 is paused, the workflow stores compact `fallback/observations/*.jsonl.gz` journals in Git. It does **not** download avatar binaries during fallback. Duplicate pending observations are skipped, the fallback scan is deliberately smaller than normal R2 discovery, and the current fallback working set has a 1 GB safety cap so Git cannot grow without bound. Once R2 is healthy, `replay_fallback.py` merges those observations into R2 using the same monotonic archive rules and deletes drained current fallback files from Git.

A storage-triggered pause differs from an operation-triggered pause: Class A/B counters reset monthly, so those normally resume automatically. Stored bytes do not reset each month; if R2 itself remains above the storage-resume threshold, fallback remains active until actual R2 space is freed.

The website normally browses active bots directly from SpicyChat's public Typesense index, so active browsing does not consume R2 reads. If the R2 Class B safety limit is reached, the deleted-bot index and archived bot detail fetches are temporarily disabled by the public quota status file and automatically return when the guard is healthy again.

## Optional billing alert

Cloudflare budget alerts are still worth enabling as a second warning layer. They are alerts, not a hard spending cutoff, so the repository's quota guard remains the primary protection.
