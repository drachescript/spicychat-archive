// Lightweight, privacy-minimal popularity signal for the hybrid archive.
// Only send an event AFTER an archived bot record was successfully loaded.
// No cookies, accounts, IPs, searches, or identifying browser details.
// A per-tab 30-minute cooldown keeps reloads from inflating one bot's count.
export const BOT_VIEW_ENDPOINT =
  'https://spicychat-archive-import.dragongraf.workers.dev/api/analytics/bot-view';
const BOT_ID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/;
const COOLDOWN_MS = 30 * 60 * 1000;

function safeSessionStorage() {
  try { return globalThis.sessionStorage; } catch { return null; }
}

export function validViewedBotId(value) {
  const id = String(value ?? '').trim().toLowerCase();
  return BOT_ID.test(id) ? id : '';
}

export function shouldTrackBotView(id, {
  storage = safeSessionStorage(),
  now = Date.now(),
  navigator = globalThis.navigator,
} = {}) {
  const botId = validViewedBotId(id);
  if (!botId || navigator?.globalPrivacyControl === true ||
      navigator?.doNotTrack === '1') return false;
  try {
    const last = Number(storage?.getItem('sca-bot-view:' + botId));
    return !Number.isFinite(last) || !last || now - last >= COOLDOWN_MS || now < last;
  } catch {
    // If sessionStorage is restricted, don't invent a persistent identifier.
    return true;
  }
}

export async function recordBotView(id, {
  storage = safeSessionStorage(),
  navigator = globalThis.navigator,
  fetchImpl = globalThis.fetch,
  now = Date.now(),
} = {}) {
  const botId = validViewedBotId(id);
  if (!botId || !shouldTrackBotView(botId, {storage, now, navigator}) ||
      typeof fetchImpl !== 'function') return false;
  try {
    // text/plain avoids a CORS preflight. The Worker checks the exact site origin.
    const result = await fetchImpl(BOT_VIEW_ENDPOINT, {
      method: 'POST',
      mode: 'cors',
      keepalive: true,
      cache: 'no-store',
      headers: {'Content-Type': 'text/plain;charset=UTF-8'},
      body: botId,
    });
    if (!result.ok) return false;
    try { storage?.setItem('sca-bot-view:' + botId, String(now)); } catch {}
    return true;
  } catch {
    // Popularity metrics must never interrupt archive browsing.
    return false;
  }
}
