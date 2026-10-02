const MAX_UPLOAD_BYTES = 64 * 1024 * 1024;
const ARCHIVE_FIELD_BATCH_MAX = 250;

// Public submissions are parsed/sanitized inside the Worker, so keep their
// compressed/raw limits lower than the trusted private direct-import route.
const MAX_PUBLIC_UPLOAD_BYTES = 16 * 1024 * 1024;
const MAX_PUBLIC_DECOMPRESSED_BYTES = 64 * 1024 * 1024;
const MAX_PUBLIC_BOTS = 5000;
const MAX_PUBLIC_BOT_BYTES = 2 * 1024 * 1024;
const PUBLIC_SUBMISSIONS_PER_24H = 6;
const PUBLIC_SUBMISSION_GROUP_MAX_CHUNKS = 12;
const REJECTED_PAYLOAD_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;

const QUEUE_PREFIX = "_imports/bot-status/queued";
const SUB_PENDING_PREFIX = "_submissions/bot-status/pending";
const SUB_PENDING_META_PREFIX = "_submissions/bot-status/meta/pending";
const SUB_ANALYSIS_PREFIX = "_submissions/bot-status/analysis";
const SUB_APPROVED_PREFIX = "_submissions/bot-status/reviewed/approved";
const SUB_REJECTED_PREFIX = "_submissions/bot-status/reviewed/rejected";
const SUB_REJECTED_PAYLOAD_PREFIX = "_submissions/bot-status/rejected-payload";
const SUB_RATE_PREFIX = "_submissions/bot-status/rate";

const PUBLIC_IMPORT_FIELDS = new Set([
  "character_id", "characterId", "id", "uuid",
  "name", "title", "description", "greeting", "greetings",
  "alternate_greetings", "personality", "scenario",
  "example_dialogue", "example_dialogues", "definition", "persona",
  "character_definition", "characterDefinition", "system_prompt",
  "post_history_instructions",
  "creator_username", "creator", "language", "type", "visibility", "tags",
  "avatar_url", "avatar", "image", "avatar_is_nsfw", "is_nsfw",
  "definition_visible", "definition_size_category", "token_count",
  "has_lorebooks", "lorebooks", "group_size_category", "group_addable",
  "createdAt", "updatedAt",
  "num_messages", "num_messages_24h", "rating_score", "rating_count",
]);

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS, GET",
    "Access-Control-Allow-Headers":
      "Authorization, Content-Type, Content-Encoding, X-Import-Filename, X-Exported-At, X-Install-Id, X-Submission-Group",
    "Access-Control-Max-Age": "86400",
  };
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...corsHeaders(),
    },
  });
}

function archiveMeaningful(value) {
  if (value == null) return false;
  if (typeof value === "string") return value.trim() !== "";
  if (Array.isArray(value)) return value.some(archiveMeaningful);
  if (typeof value === "object") return Object.values(value).some(archiveMeaningful);
  return true;
}

function archiveBestField(record, keys) {
  const lastKnown = record?.lastKnown && typeof record.lastKnown === "object" ? record.lastKnown : {};
  for (const key of keys) if (archiveMeaningful(lastKnown[key])) return lastKnown[key];
  const current = record?.current && typeof record.current === "object" ? record.current : {};
  for (const source of Object.values(current)) {
    if (!source || typeof source !== "object" || Array.isArray(source)) continue;
    for (const key of keys) if (archiveMeaningful(source[key])) return source[key];
  }
  return null;
}

function archiveFieldFlags(record) {
  return {
    personality: archiveMeaningful(archiveBestField(record, ["persona", "personality", "definition", "character_definition", "characterDefinition"])),
    scenario: archiveMeaningful(archiveBestField(record, ["scenario"])),
    dialogue: archiveMeaningful(archiveBestField(record, ["dialogue", "example_dialogue", "example_dialogues"])),
  };
}

function validArchiveBotId(value) {
  const id = String(value || "").trim().toLowerCase();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id) ? id : "";
}

async function readArchiveBotRecord(bucket, botId) {
  const compact = botId.replaceAll("-", "");
  const key = `bots/${compact.slice(0, 2)}/${botId}.json`;
  const object = await bucket.get(key);
  if (!object) return null;
  try {
    let bytes = new Uint8Array(await object.arrayBuffer());
    if (bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b) {
      const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
      bytes = new Uint8Array(await new Response(stream).arrayBuffer());
    }
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return null;
  }
}

async function cachedArchiveFieldFlags(env, botId) {
  const cache = caches.default;
  const cacheRequest = new Request(`https://archive-field-cache.spicychatarchive.invalid/v1/${botId}`);
  const cached = await cache.match(cacheRequest);
  if (cached) {
    try { return await cached.json(); } catch {}
  }
  const record = await readArchiveBotRecord(env.ARCHIVE_BUCKET, botId);
  const flags = record ? archiveFieldFlags(record) : { personality: false, scenario: false, dialogue: false };
  await cache.put(cacheRequest, new Response(JSON.stringify(flags), {
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=3600" },
  }));
  return flags;
}

async function handleArchiveFieldPresence(request, env) {
  let body;
  try { body = await request.json(); }
  catch { return json({ ok: false, error: "invalid_json" }, 400); }

  const rawIds = Array.isArray(body?.ids) ? body.ids : [];
  const ids = [...new Set(rawIds.map(validArchiveBotId).filter(Boolean))].slice(0, ARCHIVE_FIELD_BATCH_MAX);
  if (!ids.length) return json({ ok: true, fields: {} });

  const rows = await mapWithConcurrency(ids.map(id => ({ id })), 12, async item => ({
    id: item.id,
    fields: await cachedArchiveFieldFlags(env, item.id),
  }));
  const fields = {};
  for (const row of rows) {
    if (row?.id && row?.fields) fields[row.id] = row.fields;
  }
  return json({ ok: true, fields });
}

async function sameToken(left, right) {
  if (!left || !right) return false;
  const enc = new TextEncoder();
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(left)),
    crypto.subtle.digest("SHA-256", enc.encode(right)),
  ]);
  const aa = new Uint8Array(a);
  const bb = new Uint8Array(b);
  if (aa.length !== bb.length) return false;
  let diff = 0;
  for (let i = 0; i < aa.length; i++) diff |= aa[i] ^ bb[i];
  return diff === 0;
}

async function sha256Hex(value) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function safeFilename(value) {
  return String(value || "bot-status-export")
    .replace(/[^a-zA-Z0-9._-]+/g, "_")
    .slice(0, 100);
}

function normalizeStatus(value) {
  if (value && typeof value === "object") {
    value = value.current ?? value.status ?? value.state ?? "";
  }
  const text = String(value || "").trim().toLowerCase();
  return text.slice(0, 48);
}

function unwrapSnapshot(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  let current = value;
  for (let i = 0; i < 4; i++) {
    let moved = false;
    for (const key of ["snapshot", "savedCopy", "character", "bot", "record"]) {
      const nested = current[key];
      if (nested && typeof nested === "object" && !Array.isArray(nested)) {
        current = nested;
        moved = true;
        break;
      }
    }
    if (!moved) {
      const data = current.data;
      if (
        data &&
        typeof data === "object" &&
        !Array.isArray(data) &&
        ["character_id", "characterId", "id", "name", "title"].some((key) => key in data)
      ) {
        current = data;
        moved = true;
      }
    }
    if (!moved) break;
  }
  return current && typeof current === "object" && !Array.isArray(current) ? current : {};
}

function sourceItems(payload) {
  if (Array.isArray(payload)) return payload;
  if (!payload || typeof payload !== "object") return [];
  for (const key of ["savedCopies", "bots", "records", "items", "snapshots"]) {
    if (Array.isArray(payload[key])) return payload[key];
    if (payload[key] && typeof payload[key] === "object") return Object.values(payload[key]);
  }
  if (["character_id", "characterId", "botId", "id"].some((key) => key in payload)) return [payload];
  return [];
}

function findIds(item, raw) {
  return [
    item?.botId,
    item?.characterId,
    item?.character_id,
    item?.id,
    raw?.character_id,
    raw?.characterId,
    raw?.id,
    raw?.uuid,
  ]
    .map((value) => String(value || "").trim().toLowerCase())
    .filter(Boolean);
}

const QOL_NESTED_FIELD_ALIASES = Object.freeze({
  name: "name",
  title: "title",
  description: "description",
  greeting: "greeting",
  personality: "personality",
  scenario: "scenario",
  exampleDialogues: "example_dialogues",
  tags: "tags",
  visibility: "visibility",
  creator: "creator",
  image: "image",
  messageCount: "num_messages",
  rating: "rating_score",
  tokenCount: "token_count",
});

function cleanPublicSnapshot(item, botId) {
  const raw = unwrapSnapshot(item);
  const clean = {};

  // Native/archive-shaped fields.
  for (const key of PUBLIC_IMPORT_FIELDS) {
    if (key in raw) clean[key] = raw[key];
  }

  // QoL Bot Status Center keeps its public character copy under snapshot.fields.
  // Translate that richer local shape into the archive's canonical public keys
  // before the privacy whitelist is applied.
  const nestedFields =
    raw?.fields && typeof raw.fields === "object" && !Array.isArray(raw.fields)
      ? raw.fields
      : {};
  for (const [sourceKey, archiveKey] of Object.entries(QOL_NESTED_FIELD_ALIASES)) {
    if (!(archiveKey in clean) && sourceKey in nestedFields) clean[archiveKey] = nestedFields[sourceKey];
  }

  clean.character_id = botId;

  for (const other of ["characterId", "id", "uuid"]) {
    if (other in clean && String(clean[other] || "").toLowerCase() !== botId) delete clean[other];
  }
  return clean;
}

function extractObservedStatus(item, raw) {
  const candidates = [
    item?.observedStatus,
    item?.currentStatus,
    item?.availability,
    item?.status,
    item?.statusObservation?.status,
    raw?.observedStatus,
    raw?.currentStatus,
    raw?.status,
    raw?.statusObservation?.status,
  ];
  for (const value of candidates) {
    const normalized = normalizeStatus(value);
    if (normalized) return normalized;
  }
  return "";
}

function safeTimestamp(value, fallback) {
  const text = String(value || "").trim();
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? new Date(parsed).toISOString() : fallback;
}

function sanitizePublicPayload(payload, nowIso) {
  const items = sourceItems(payload);
  const exportedAt = safeTimestamp(payload?.exportedAt || payload?.createdAt, nowIso);
  const savedCopies = [];
  let malformedCount = 0;
  let problemCount = 0;

  for (const item of items.slice(0, MAX_PUBLIC_BOTS + 1)) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      malformedCount++;
      continue;
    }

    const raw = unwrapSnapshot(item);
    const ids = [...new Set(findIds(item, raw))];
    if (!ids.length) {
      malformedCount++;
      continue;
    }

    const botId = ids[0];
    const problems = [];
    if (ids.some((id) => id !== botId)) problems.push("id_mismatch");

    const snapshot = cleanPublicSnapshot(item, botId);
    if (Object.keys(snapshot).length <= 1) {
      malformedCount++;
      continue;
    }

    let serialized;
    try {
      serialized = JSON.stringify(snapshot);
    } catch {
      malformedCount++;
      continue;
    }
    if (new TextEncoder().encode(serialized).byteLength > MAX_PUBLIC_BOT_BYTES) {
      problems.push("record_too_large");
    }

    if (problems.length) problemCount++;

    savedCopies.push({
      botId,
      savedAt: safeTimestamp(
        item.savedAt || item.snapshotAt || item.capturedAt || item.updatedAt,
        exportedAt,
      ),
      observedStatus: extractObservedStatus(item, raw),
      problems,
      snapshot,
    });
  }

  if (items.length > MAX_PUBLIC_BOTS) {
    throw new Error(`too_many_bots:${items.length}`);
  }

  return {
    payload: {
      schemaVersion: 1,
      kind: "spicychat-qol-bot-status-public-submission",
      exportedAt,
      savedCopies,
    },
    malformedCount,
    problemCount,
  };
}

async function decompressGzip(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  const raw = await new Response(stream).arrayBuffer();
  if (raw.byteLength > MAX_PUBLIC_DECOMPRESSED_BYTES) {
    throw new Error("decompressed_upload_too_large");
  }
  return new Uint8Array(raw);
}

async function gzipBytes(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function readJsonObject(bucket, key, fallback = null) {
  const object = await bucket.get(key);
  if (!object) return fallback;
  try {
    return await object.json();
  } catch {
    return fallback;
  }
}

async function putJson(bucket, key, value) {
  await bucket.put(key, JSON.stringify(value), {
    httpMetadata: {
      contentType: "application/json; charset=utf-8",
      cacheControl: "no-store",
    },
  });
}

function authToken(request) {
  const auth = request.headers.get("Authorization") || "";
  return auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
}

async function requireAdmin(request, env) {
  if (!env.IMPORT_TOKEN) return { ok: false, response: json({ ok: false, error: "import_token_not_configured" }, 503) };
  if (!(await sameToken(authToken(request), env.IMPORT_TOKEN))) {
    return { ok: false, response: json({ ok: false, error: "unauthorized" }, 401) };
  }
  return { ok: true };
}

async function enforcePublicRateLimit(env, installId, nowIso, submissionGroupId = "") {
  const installHash = await sha256Hex(installId);
  const key = `${SUB_RATE_PREFIX}/${installHash}.json`;
  const row = (await readJsonObject(env.ARCHIVE_BUCKET, key, {})) || {};
  const cutoff = Date.now() - 24 * 60 * 60 * 1000;
  const recent = (Array.isArray(row.timestamps) ? row.timestamps : []).filter((value) => {
    const t = Date.parse(value);
    return Number.isFinite(t) && t >= cutoff;
  });
  const groups = (Array.isArray(row.groups) ? row.groups : [])
    .filter((value) => {
      const t = Date.parse(value?.at || "");
      return value && typeof value === "object" && Number.isFinite(t) && t >= cutoff;
    })
    .map((value) => ({
      id: String(value.id || "").slice(0, 128),
      at: String(value.at || ""),
      count: Math.max(1, Number(value.count) || 1),
    }))
    .filter((value) => value.id);

  const groupId = String(submissionGroupId || "").trim().slice(0, 128);
  const existingGroup = groupId ? groups.find((value) => value.id === groupId) : null;

  // Multiple chunks created by one user-initiated QoL submission count as one
  // anti-spam event, while still having a hard per-group chunk ceiling.
  if (existingGroup) {
    if (existingGroup.count >= PUBLIC_SUBMISSION_GROUP_MAX_CHUNKS) {
      return {
        ok: false,
        installHash,
        retryAfterSeconds: 24 * 60 * 60,
      };
    }
    existingGroup.count += 1;
    await putJson(env.ARCHIVE_BUCKET, key, {
      schemaVersion: 2,
      installHash,
      timestamps: recent,
      groups,
      lastAt: nowIso,
    });
    return { ok: true, installHash, grouped: true };
  }

  if (recent.length >= PUBLIC_SUBMISSIONS_PER_24H) {
    const oldest = Math.min(...recent.map((v) => Date.parse(v)));
    return {
      ok: false,
      installHash,
      retryAfterSeconds: Math.max(60, Math.ceil((oldest + 24 * 60 * 60 * 1000 - Date.now()) / 1000)),
    };
  }

  recent.push(nowIso);
  if (groupId) groups.push({ id: groupId, at: nowIso, count: 1 });
  await putJson(env.ARCHIVE_BUCKET, key, {
    schemaVersion: 2,
    installHash,
    timestamps: recent,
    groups,
    lastAt: nowIso,
  });
  return { ok: true, installHash, grouped: !!groupId };
}

async function listReviewRows(bucket, prefix, limit = 100) {
  const listed = await bucket.list({ prefix, limit: Math.min(500, Math.max(1, limit)) });
  const rows = [];
  for (const object of listed.objects) {
    const row = await readJsonObject(bucket, object.key, null);
    if (row) rows.push(row);
  }
  rows.sort((a, b) =>
    String(b.submittedAt || b.reviewedAt || "").localeCompare(String(a.submittedAt || a.reviewedAt || "")),
  );
  return rows;
}

async function loadPendingPayload(bucket, submissionId) {
  const key = `${SUB_PENDING_PREFIX}/${submissionId}.json.gz`;
  const object = await bucket.get(key);
  if (!object) return null;
  const bytes = new Uint8Array(await object.arrayBuffer());
  const raw = bytes[0] === 0x1f && bytes[1] === 0x8b ? await decompressGzip(bytes) : bytes;
  return JSON.parse(new TextDecoder().decode(raw));
}

async function handlePublicSubmission(request, env) {
  const announced = Number(request.headers.get("Content-Length") || 0);
  if (announced > MAX_PUBLIC_UPLOAD_BYTES) {
    return json({ ok: false, error: "upload_too_large", maxBytes: MAX_PUBLIC_UPLOAD_BYTES }, 413);
  }

  const installId = String(request.headers.get("X-Install-Id") || "").trim();
  if (installId.length < 16 || installId.length > 256) {
    return json({ ok: false, error: "missing_or_invalid_install_id" }, 400);
  }

  const now = new Date();
  const nowIso = now.toISOString();
  const submissionGroupId = String(request.headers.get("X-Submission-Group") || "").trim();
  const rate = await enforcePublicRateLimit(env, installId, nowIso, submissionGroupId);
  if (!rate.ok) {
    return new Response(
      JSON.stringify({
        ok: false,
        error: "rate_limited",
        retryAfterSeconds: rate.retryAfterSeconds,
      }),
      {
        status: 429,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Retry-After": String(rate.retryAfterSeconds),
          "Cache-Control": "no-store",
          ...corsHeaders(),
        },
      },
    );
  }

  const body = new Uint8Array(await request.arrayBuffer());
  if (!body.byteLength) return json({ ok: false, error: "empty_upload" }, 400);
  if (body.byteLength > MAX_PUBLIC_UPLOAD_BYTES) {
    return json({ ok: false, error: "upload_too_large", maxBytes: MAX_PUBLIC_UPLOAD_BYTES }, 413);
  }

  try {
    const isGzip =
      request.headers.get("Content-Encoding")?.toLowerCase() === "gzip" ||
      (body.length >= 2 && body[0] === 0x1f && body[1] === 0x8b);
    const raw = isGzip ? await decompressGzip(body) : body;
    if (raw.byteLength > MAX_PUBLIC_DECOMPRESSED_BYTES) {
      return json({ ok: false, error: "decompressed_upload_too_large" }, 413);
    }

    const parsed = JSON.parse(new TextDecoder().decode(raw));
    const sanitized = sanitizePublicPayload(parsed, nowIso);

    if (!sanitized.payload.savedCopies.length) {
      return json({ ok: false, error: "no_usable_bot_snapshots" }, 400);
    }

    const submissionId = crypto.randomUUID();
    const sanitizedBytes = new TextEncoder().encode(JSON.stringify(sanitized.payload));
    const compressed = await gzipBytes(sanitizedBytes);

    await env.ARCHIVE_BUCKET.put(`${SUB_PENDING_PREFIX}/${submissionId}.json.gz`, compressed, {
      httpMetadata: {
        contentType: "application/gzip",
        cacheControl: "no-store",
      },
      customMetadata: {
        source: "public-qol-bot-status-submission",
        submittedAt: nowIso,
      },
    });

    const meta = {
      schemaVersion: 1,
      submissionId,
      status: "pending-analysis",
      submittedAt: nowIso,
      exportedAt: sanitized.payload.exportedAt,
      botCount: sanitized.payload.savedCopies.length,
      malformedCount: sanitized.malformedCount,
      problemCount: sanitized.problemCount,
      sizeBytes: compressed.byteLength,
      installHash: rate.installHash,
      filename: safeFilename(request.headers.get("X-Import-Filename")),
      analysisReady: false,
    };
    await putJson(env.ARCHIVE_BUCKET, `${SUB_PENDING_META_PREFIX}/${submissionId}.json`, meta);

    return json(
      {
        ok: true,
        pendingReview: true,
        submissionId,
        botCount: meta.botCount,
        malformedDropped: meta.malformedCount,
        problemRecords: meta.problemCount,
        bytes: meta.sizeBytes,
      },
      202,
    );
  } catch (error) {
    const message = String(error?.message || error);
    if (message.startsWith("too_many_bots:")) {
      return json(
        {
          ok: false,
          error: "too_many_bots",
          maxBots: MAX_PUBLIC_BOTS,
          received: Number(message.split(":")[1] || 0),
        },
        413,
      );
    }
    if (message === "decompressed_upload_too_large") {
      return json({ ok: false, error: message }, 413);
    }
    return json({ ok: false, error: "invalid_export", detail: message.slice(0, 200) }, 400);
  }
}

async function handleAdminList(request, env, url) {
  const auth = await requireAdmin(request, env);
  if (!auth.ok) return auth.response;

  const status = String(url.searchParams.get("status") || "pending").toLowerCase();
  const limit = Number(url.searchParams.get("limit") || 100);
  let prefix;
  if (status === "pending") prefix = `${SUB_PENDING_META_PREFIX}/`;
  else if (status === "approved") prefix = `${SUB_APPROVED_PREFIX}/`;
  else if (status === "rejected") prefix = `${SUB_REJECTED_PREFIX}/`;
  else return json({ ok: false, error: "invalid_status" }, 400);

  const rows = await listReviewRows(env.ARCHIVE_BUCKET, prefix, limit);
  return json({ ok: true, status, submissions: rows });
}

async function findReviewRecord(bucket, submissionId) {
  const locations = [
    ["pending", `${SUB_PENDING_META_PREFIX}/${submissionId}.json`],
    ["approved", `${SUB_APPROVED_PREFIX}/${submissionId}.json`],
    ["rejected", `${SUB_REJECTED_PREFIX}/${submissionId}.json`],
  ];
  for (const [status, key] of locations) {
    const row = await readJsonObject(bucket, key, null);
    if (row) return { status, row };
  }
  return null;
}

async function handleAdminDetail(request, env, submissionId, url) {
  const auth = await requireAdmin(request, env);
  if (!auth.ok) return auth.response;

  const found = await findReviewRecord(env.ARCHIVE_BUCKET, submissionId);
  if (!found) return json({ ok: false, error: "submission_not_found" }, 404);

  if (found.status !== "pending") {
    return json({ ok: true, status: found.status, submission: found.row });
  }

  const meta = found.row;
  const analysis = await readJsonObject(
    env.ARCHIVE_BUCKET,
    `${SUB_ANALYSIS_PREFIX}/${submissionId}.json`,
    null,
  );

  const offset = Math.max(0, Number(url.searchParams.get("offset") || 0));
  const limit = Math.min(200, Math.max(1, Number(url.searchParams.get("limit") || 100)));
  const payload = await loadPendingPayload(env.ARCHIVE_BUCKET, submissionId);
  if (!payload) return json({ ok: false, error: "pending_payload_missing" }, 409);

  const items = Array.isArray(payload.savedCopies) ? payload.savedCopies : [];
  const assessments = new Map(
    (analysis?.records || []).map((row) => [String(row.botId || "").toLowerCase(), row]),
  );
  const page = items.slice(offset, offset + limit).map((item) => ({
    ...item,
    assessment: assessments.get(String(item.botId || "").toLowerCase()) || null,
  }));

  return json({
    ok: true,
    status: "pending",
    submission: meta,
    analysis,
    records: page,
    offset,
    limit,
    totalRecords: items.length,
    hasMore: offset + page.length < items.length,
  });
}

async function moveRejectedPayload(bucket, submissionId) {
  const sourceKey = `${SUB_PENDING_PREFIX}/${submissionId}.json.gz`;
  const object = await bucket.get(sourceKey);
  if (!object) return null;

  const bytes = await object.arrayBuffer();
  const key = `${SUB_REJECTED_PAYLOAD_PREFIX}/${Date.now()}-${submissionId}.json.gz`;
  await bucket.put(key, bytes, {
    httpMetadata: {
      contentType: "application/gzip",
      cacheControl: "no-store",
    },
    customMetadata: {
      rejectedAt: new Date().toISOString(),
      deleteAfterDays: "7",
    },
  });
  await bucket.delete(sourceKey);
  return key;
}

async function clearPending(bucket, submissionId, { keepPayload = false } = {}) {
  const keys = [
    `${SUB_PENDING_META_PREFIX}/${submissionId}.json`,
    `${SUB_ANALYSIS_PREFIX}/${submissionId}.json`,
  ];
  if (!keepPayload) keys.push(`${SUB_PENDING_PREFIX}/${submissionId}.json.gz`);
  await bucket.delete(keys);
}

async function handleAdminAction(request, env, submissionId) {
  const auth = await requireAdmin(request, env);
  if (!auth.ok) return auth.response;

  const meta = await readJsonObject(
    env.ARCHIVE_BUCKET,
    `${SUB_PENDING_META_PREFIX}/${submissionId}.json`,
    null,
  );
  if (!meta) return json({ ok: false, error: "pending_submission_not_found" }, 404);

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "invalid_action_body" }, 400);
  }

  const action = String(body?.action || "").toLowerCase();
  const nowIso = new Date().toISOString();

  if (action === "reject") {
    const rejectedPayloadKey = await moveRejectedPayload(env.ARCHIVE_BUCKET, submissionId);
    const review = {
      schemaVersion: 1,
      submissionId,
      status: "rejected",
      submittedAt: meta.submittedAt,
      reviewedAt: nowIso,
      botCount: meta.botCount,
      sizeBytes: meta.sizeBytes,
      breakdown: meta.breakdown || null,
      reason: String(body?.reason || "").slice(0, 500),
      rejectedPayloadKey,
      rejectedPayloadDeleteAfter: new Date(Date.now() + REJECTED_PAYLOAD_RETENTION_MS).toISOString(),
    };
    await putJson(env.ARCHIVE_BUCKET, `${SUB_REJECTED_PREFIX}/${submissionId}.json`, review);
    await clearPending(env.ARCHIVE_BUCKET, submissionId, { keepPayload: true });
    return json({ ok: true, submission: review });
  }

  if (!["approve-safe", "approve-selected"].includes(action)) {
    return json({ ok: false, error: "invalid_action" }, 400);
  }

  const analysis = await readJsonObject(
    env.ARCHIVE_BUCKET,
    `${SUB_ANALYSIS_PREFIX}/${submissionId}.json`,
    null,
  );
  if (!analysis || !meta.analysisReady) {
    return json({ ok: false, error: "analysis_pending" }, 409);
  }

  const payload = await loadPendingPayload(env.ARCHIVE_BUCKET, submissionId);
  if (!payload) return json({ ok: false, error: "pending_payload_missing" }, 409);

  const assessmentById = new Map(
    (analysis.records || []).map((row) => [String(row.botId || "").toLowerCase(), row]),
  );
  const selected = new Set(
    Array.isArray(body?.selectedBotIds)
      ? body.selectedBotIds.map((id) => String(id || "").toLowerCase()).filter(Boolean)
      : [],
  );

  const accepted = [];
  const skipped = [];

  for (const item of payload.savedCopies || []) {
    const botId = String(item.botId || "").toLowerCase();
    const assessment = assessmentById.get(botId);
    const wanted = action === "approve-safe" ? true : selected.has(botId);

    if (!wanted) continue;
    if (!assessment || !assessment.safe || assessment.classification === "exact_duplicate") {
      skipped.push({
        botId,
        reason: !assessment
          ? "missing_analysis"
          : assessment.classification === "exact_duplicate"
            ? "exact_duplicate"
            : "problem_record",
      });
      continue;
    }

    // Keep only the established import contract. observedStatus/problems are
    // review-only metadata and never enter the live import queue.
    accepted.push({
      botId,
      savedAt: item.savedAt,
      snapshot: item.snapshot,
    });
  }

  if (accepted.length) {
    const queuePayload = {
      schemaVersion: 1,
      kind: "spicychat-qol-bot-status-public-approved",
      exportedAt: payload.exportedAt,
      approvedAt: nowIso,
      submissionId,
      savedCopies: accepted,
    };
    const bytes = new TextEncoder().encode(JSON.stringify(queuePayload));
    const gz = await gzipBytes(bytes);
    const stamp = nowIso.replace(/[:.]/g, "-");
    await env.ARCHIVE_BUCKET.put(
      `${QUEUE_PREFIX}/${stamp}-public-${submissionId}.json.gz`,
      gz,
      {
        httpMetadata: {
          contentType: "application/gzip",
          cacheControl: "no-store",
        },
        customMetadata: {
          source: "approved-public-qol-submission",
          submissionId,
          approvedAt: nowIso,
        },
      },
    );
  }

  const review = {
    schemaVersion: 1,
    submissionId,
    status: "approved",
    mode: action,
    submittedAt: meta.submittedAt,
    reviewedAt: nowIso,
    originalBotCount: meta.botCount,
    acceptedCount: accepted.length,
    skippedCount: skipped.length,
    skipped: skipped.slice(0, 250),
    breakdown: meta.breakdown || analysis.breakdown || null,
  };
  await putJson(env.ARCHIVE_BUCKET, `${SUB_APPROVED_PREFIX}/${submissionId}.json`, review);
  await clearPending(env.ARCHIVE_BUCKET, submissionId);

  return json({
    ok: true,
    submission: review,
    queuedForImport: accepted.length,
  });
}

async function cleanupRejectedPayloads(env) {
  const cutoff = Date.now() - REJECTED_PAYLOAD_RETENTION_MS;
  let cursor;
  let deleted = 0;

  do {
    const listed = await env.ARCHIVE_BUCKET.list({
      prefix: `${SUB_REJECTED_PAYLOAD_PREFIX}/`,
      limit: 500,
      cursor,
    });
    const expired = listed.objects
      .filter((object) => {
        const uploaded = object.uploaded ? new Date(object.uploaded).getTime() : 0;
        return uploaded && uploaded < cutoff;
      })
      .map((object) => object.key);

    if (expired.length) {
      await env.ARCHIVE_BUCKET.delete(expired);
      deleted += expired.length;
    }
    cursor = listed.truncated ? listed.cursor : undefined;
  } while (cursor);

  return deleted;
}

const TRANSLATION_MAX_ITEMS = 200;
const TRANSLATION_MAX_TEXT_CHARS = 1200;
const TRANSLATION_MAX_TOTAL_CHARS = 30000;
const TRANSLATION_ALLOWED_ORIGINS = new Set([
  "https://spicychatarchive.drache.uk",
  "https://drachescript.github.io",
]);

async function mapWithConcurrency(items, limit, mapper) {
  const output = new Array(items.length);
  let cursor = 0;
  async function worker() {
    while (true) {
      const index = cursor++;
      if (index >= items.length) return;
      try { output[index] = await mapper(items[index], index); }
      catch (error) { output[index] = { id: items[index]?.id || "", error: String(error?.message || error || "translation_failed") }; }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
  return output;
}

async function translateTextCached(text, target = "en") {
  const cacheKey = await sha256Hex(`${target}\u0000${text}`);
  const cache = caches.default;
  const request = new Request(`https://translation-cache.spicychatarchive.invalid/v1/${cacheKey}`);
  const cached = await cache.match(request);
  if (cached) return cached.json();

  const url = new URL("https://translate.googleapis.com/translate_a/single");
  url.searchParams.set("client", "gtx");
  url.searchParams.set("sl", "auto");
  url.searchParams.set("tl", target);
  url.searchParams.set("dt", "t");
  url.searchParams.set("q", text);
  const response = await fetch(url.toString(), { headers: { "User-Agent": "SpicyChatArchive/1.0" } });
  if (!response.ok) throw new Error(`translate_upstream_${response.status}`);
  const payload = await response.json();
  const translated = Array.isArray(payload?.[0]) ? payload[0].map(part => String(part?.[0] || "")).join("") : "";
  const result = { translated: translated || text, sourceLanguage: String(payload?.[2] || "") };
  await cache.put(request, new Response(JSON.stringify(result), {
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=2592000" },
  }));
  return result;
}

async function handleTranslation(request) {
  const origin = String(request.headers.get("Origin") || "");
  if (!TRANSLATION_ALLOWED_ORIGINS.has(origin)) return json({ ok: false, error: "origin_not_allowed" }, 403);
  let body;
  try { body = await request.json(); }
  catch { return json({ ok: false, error: "invalid_json" }, 400); }
  const target = String(body?.target || "en").toLowerCase();
  if (target !== "en") return json({ ok: false, error: "unsupported_target" }, 400);
  const incoming = Array.isArray(body?.items) ? body.items.slice(0, TRANSLATION_MAX_ITEMS) : [];
  if (!incoming.length) return json({ ok: true, items: [] });
  const items = [];
  let totalChars = 0;
  for (const item of incoming) {
    const id = String(item?.id || "").slice(0, 160);
    const text = String(item?.text || "").trim().slice(0, TRANSLATION_MAX_TEXT_CHARS);
    if (!id || !text) continue;
    totalChars += text.length;
    if (totalChars > TRANSLATION_MAX_TOTAL_CHARS) return json({ ok: false, error: "translation_batch_too_large" }, 413);
    items.push({ id, text });
  }
  const translated = await mapWithConcurrency(items, 6, async item => ({ id: item.id, ...(await translateTextCached(item.text, target)) }));
  return json({ ok: true, items: translated });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    if (request.method === "GET" && url.pathname === "/health") {
      return json({
        ok: true,
        service: "spicychat-archive-import",
        accepts: "SpicyChat QoL Bot Status Center export v1",
        publicSubmissionEndpoint: "/api/submissions/bot-status",
        adminSubmissionEndpoint: "/api/admin/submissions",
        translationEndpoint: "/api/translate",
        archiveFieldPresenceEndpoint: "/api/archive-field-presence",
      });
    }


    if (request.method === "POST" && url.pathname === "/api/translate") {
      return handleTranslation(request);
    }

    if (request.method === "POST" && url.pathname === "/api/archive-field-presence") {
      return handleArchiveFieldPresence(request, env);
    }

    // -----------------------------------------------------------------------
    // EXISTING TRUSTED DIRECT-IMPORT ROUTE.
    // Keep this path and its behavior unchanged.
    // -----------------------------------------------------------------------
    if (request.method === "POST" && url.pathname === "/api/imports/bot-status") {
      if (!env.IMPORT_TOKEN) {
        return json({ ok: false, error: "import_token_not_configured" }, 503);
      }

      const auth = request.headers.get("Authorization") || "";
      const supplied = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
      if (!(await sameToken(supplied, env.IMPORT_TOKEN))) {
        return json({ ok: false, error: "unauthorized" }, 401);
      }

      const announced = Number(request.headers.get("Content-Length") || 0);
      if (announced > MAX_UPLOAD_BYTES) {
        return json({ ok: false, error: "upload_too_large", maxBytes: MAX_UPLOAD_BYTES }, 413);
      }

      const body = await request.arrayBuffer();
      if (!body.byteLength) {
        return json({ ok: false, error: "empty_upload" }, 400);
      }
      if (body.byteLength > MAX_UPLOAD_BYTES) {
        return json({ ok: false, error: "upload_too_large", maxBytes: MAX_UPLOAD_BYTES }, 413);
      }

      const bytes = new Uint8Array(body);
      const isGzip =
        request.headers.get("Content-Encoding")?.toLowerCase() === "gzip" ||
        (bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b);

      const now = new Date();
      const stamp = now.toISOString().replace(/[:.]/g, "-");
      const id = crypto.randomUUID();
      const ext = isGzip ? "json.gz" : "json";
      const key = `${QUEUE_PREFIX}/${stamp}-${id}.${ext}`;
      const filename = safeFilename(request.headers.get("X-Import-Filename"));
      const exportedAt = String(request.headers.get("X-Exported-At") || "").slice(0, 64);

      await env.ARCHIVE_BUCKET.put(key, body, {
        httpMetadata: {
          contentType: isGzip ? "application/gzip" : "application/json; charset=utf-8",
          cacheControl: "no-store",
        },
        customMetadata: {
          filename,
          exportedAt,
          uploadedAt: now.toISOString(),
          source: "spicychat-qol-bot-status-center",
        },
      });

      return json({
        ok: true,
        queued: true,
        queueId: id,
        bytes: body.byteLength,
        compressed: isGzip,
      }, 202);
    }

    // -----------------------------------------------------------------------
    // PUBLIC, ANONYMOUS, UNTRUSTED SUBMISSION ROUTE.
    // No admin token is accepted or required here.
    // -----------------------------------------------------------------------
    if (request.method === "POST" && url.pathname === "/api/submissions/bot-status") {
      return handlePublicSubmission(request, env);
    }

    // -----------------------------------------------------------------------
    // ADMIN REVIEW API. Uses the SAME IMPORT_TOKEN as trusted direct import.
    // -----------------------------------------------------------------------
    if (request.method === "GET" && url.pathname === "/api/admin/submissions") {
      return handleAdminList(request, env, url);
    }

    const detailMatch = url.pathname.match(/^\/api\/admin\/submissions\/([a-f0-9-]+)$/i);
    if (request.method === "GET" && detailMatch) {
      return handleAdminDetail(request, env, detailMatch[1], url);
    }

    const actionMatch = url.pathname.match(/^\/api\/admin\/submissions\/([a-f0-9-]+)\/action$/i);
    if (request.method === "POST" && actionMatch) {
      return handleAdminAction(request, env, actionMatch[1]);
    }

    return json({ ok: false, error: "not_found" }, 404);
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(cleanupRejectedPayloads(env));
  },
};
