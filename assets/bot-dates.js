// A SpicyChat timestamp is not the same as the Archive's first-seen time.
// Accept ISO dates and Unix seconds / milliseconds without guessing missing dates.
export function botTimestamp(value, now = Date.now()) {
  if (value == null || value === '' || typeof value === 'boolean') return null;
  let parsed = null;
  if (typeof value === 'number' || (typeof value === 'string' && /^\\d+(?:\\.\\d+)?$/.test(value.trim()))) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return null;
    const ms = n < 100000000000 ? n * 1000 : n;
    parsed = new Date(ms);
  } else if (typeof value === 'string' && value.trim()) {
    parsed = new Date(value.trim());
  }
  if (!parsed || !Number.isFinite(parsed.getTime())) return null;
  const ms = parsed.getTime();
  if (ms < Date.UTC(2000, 0, 1) || ms > now + 86400000) return null;
  return parsed.toISOString();
}

export function reportedBotTimestamp(record, fields, now = Date.now()) {
  const lk = record?.lastKnown;
  const sources = record?.current;
  const values = [lk, ...(sources && typeof sources === 'object' ? Object.values(sources) : []), record];
  for (const field of fields) {
    for (const source of values) {
      if (!source || typeof source !== 'object') continue;
      const iso = botTimestamp(source[field], now);
      if (iso) return iso;
    }
  }
  return null;
}
