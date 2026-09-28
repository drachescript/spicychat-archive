const MAX_UPLOAD_BYTES = 64 * 1024 * 1024;
const QUEUE_PREFIX = "_imports/bot-status/queued";

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS, GET",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Content-Encoding, X-Import-Filename, X-Exported-At",
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

function safeFilename(value) {
  return String(value || "bot-status-export")
    .replace(/[^a-zA-Z0-9._-]+/g, "_")
    .slice(0, 100);
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
      });
    }

    if (request.method !== "POST" || url.pathname !== "/api/imports/bot-status") {
      return json({ ok: false, error: "not_found" }, 404);
    }

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
  },
};
