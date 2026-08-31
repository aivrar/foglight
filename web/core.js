export const byId = id => document.getElementById(id);

export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

export function elapsed(seconds) {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

export function formatUtcTime(date) {
  return `${String(date.getUTCHours()).padStart(2, '0')}:${String(date.getUTCMinutes()).padStart(2, '0')}`;
}

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
  }[character]));
}

export function safeHttpUrl(value, base = globalThis.location?.origin || 'http://localhost') {
  if (!value) return '';
  try {
    const parsed = new URL(String(value), base);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) return '';
    for (const key of [...parsed.searchParams.keys()]) {
      if (/(?:api[_-]?key|token|secret|password|appid)/i.test(key)) {
        parsed.searchParams.set(key, '<redacted>');
      }
    }
    if (/(?:api[_-]?key|token|secret|password|appid)/i.test(parsed.hash)) {
      parsed.hash = '#<redacted>';
    }
    return parsed.href;
  } catch {
    return '';
  }
}

function finiteNumber(value) {
  const candidate = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
  return Number.isFinite(candidate) ? candidate : null;
}

export function latestKpValue(rows) {
  if (!Array.isArray(rows)) return null;
  let fallback = null;
  let newest = null;
  let newestTimestamp = -Infinity;

  for (const row of rows) {
    let rawKp;
    let rawTimestamp;
    if (Array.isArray(row)) {
      [rawTimestamp, rawKp] = row;
    } else if (row && typeof row === 'object') {
      rawKp = row.Kp ?? row.kp;
      rawTimestamp = row.time_tag;
    } else {
      continue;
    }

    const kp = finiteNumber(rawKp);
    if (kp == null) continue;
    fallback = kp;
    const timestamp = Date.parse(String(rawTimestamp ?? ''));
    if (Number.isFinite(timestamp) && timestamp >= newestTimestamp) {
      newest = kp;
      newestTimestamp = timestamp;
    }
  }

  return newest ?? fallback;
}

export function validMapCoordinates(latitudeValue, longitudeValue) {
  const directionalNumber = (value, allowedDirections) => {
    const numeric = finiteNumber(value);
    if (numeric != null) return numeric;
    if (typeof value !== 'string') return null;
    const match = value.trim().match(/^(\d+(?:\.\d+)?)\s*([NSEW])$/i);
    if (!match || !allowedDirections.includes(match[2].toUpperCase())) return null;
    const magnitude = Number(match[1]);
    return ['S', 'W'].includes(match[2].toUpperCase()) ? -magnitude : magnitude;
  };
  const latitude = directionalNumber(latitudeValue, 'NS');
  const longitude = directionalNumber(longitudeValue, 'EW');
  if (
    latitude == null || longitude == null
    || latitude < -90 || latitude > 90
    || longitude < -180 || longitude > 180
  ) return null;
  return [latitude, longitude];
}

export function updateSourceFreshness(states, source, freshness) {
  if (!(states instanceof Map)) throw new TypeError('source freshness state must be a Map');
  if (typeof source === 'string' && source) {
    if (freshness == null) states.delete(source);
    else if (typeof freshness === 'string') states.set(source, freshness);
  }
  const counts = { live: 0, errored: 0, cached: 0 };
  for (const value of states.values()) {
    if (value === 'live') counts.live += 1;
    else if (value === 'cached' || value === 'stale') counts.cached += 1;
    else counts.errored += 1;
  }
  return counts;
}

export async function runWithConcurrency(items, worker, limit = 3) {
  if (!Array.isArray(items)) throw new TypeError('concurrency items must be an array');
  if (typeof worker !== 'function') throw new TypeError('concurrency worker must be a function');
  if (!Number.isInteger(limit) || limit < 1) {
    throw new TypeError('concurrency limit must be a positive integer');
  }
  const results = new Array(items.length);
  let cursor = 0;
  async function consume() {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await worker(items[index], index);
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, () => consume())
  );
  return results;
}
