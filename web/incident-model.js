const WARNING_KINDS = new Set(['weather_alert', 'tropical_cyclone', 'tsunami']);
const ADVISORY_KINDS = new Set(['aviation_hazard']);
const ADMINISTRATIVE_KINDS = new Set(['disaster_declaration']);
const MEASUREMENT_KINDS = new Set(['marine_observation', 'water_level', 'fireball']);
const MEDIA_KINDS = new Set(['conflict_report', 'humanitarian_report', 'news_item']);
const COMMUNITY_KINDS = new Set(['aircraft']);
const SIGNAL_KINDS = new Set(['market_snapshot', 'technology_activity']);

export const TIMELINE_WINDOWS = Object.freeze([
  { hours: 1, label: '1 hour' },
  { hours: 6, label: '6 hours' },
  { hours: 24, label: '24 hours' },
  { hours: 168, label: '7 days' },
]);

export function provenanceLabel(item) {
  const kind = String(item?.kind || 'unknown');
  if (WARNING_KINDS.has(kind)) return { id: 'warning', label: 'Warning' };
  if (ADVISORY_KINDS.has(kind)) return { id: 'advisory', label: 'Official advisory' };
  if (ADMINISTRATIVE_KINDS.has(kind)) {
    return { id: 'administrative', label: 'Administrative declaration' };
  }
  if (MEASUREMENT_KINDS.has(kind)) return { id: 'measurement', label: 'Source measurement' };
  if (MEDIA_KINDS.has(kind)) return { id: 'media', label: 'Media coverage' };
  if (COMMUNITY_KINDS.has(kind)) return { id: 'community', label: 'Community signal' };
  if (SIGNAL_KINDS.has(kind)) return { id: 'market', label: 'Market / internet signal' };
  if (item?.urgency === 'Future' || ['Possible', 'Unlikely'].includes(item?.certainty)) {
    return { id: 'forecast', label: 'Forecast' };
  }
  return { id: 'observation', label: 'Observation' };
}

export function formatUtcTimestamp(value) {
  const parsed = Date.parse(value || '');
  if (!Number.isFinite(parsed)) return 'Not reported';
  return new Date(parsed).toISOString().replace('T', ' ').replace('.000Z', ' UTC').replace('Z', ' UTC');
}

export function expirationState(item, now = Date.now()) {
  if (item?.status === 'cancelled' || item?.change_type === 'cancelled') return 'Cancelled';
  if (item?.status === 'ended' || item?.change_type === 'resolved') return 'Ended';
  const expires = Date.parse(item?.expires_at || '');
  return Number.isFinite(expires) && expires <= now ? 'Expired' : 'Current';
}

export function normalizeTimeline(items) {
  return (Array.isArray(items) ? items : []).filter(item => (
    item && Number.isInteger(Number(item.revision)) && Number(item.revision) >= 1
    && Number.isFinite(Date.parse(item.changed_at || ''))
  )).map(item => ({ ...item, revision: Number(item.revision) })).sort((left, right) => (
    Date.parse(left.changed_at) - Date.parse(right.changed_at)
    || left.revision - right.revision
  ));
}

export function filterTimeline(items, hours = 24, now = Date.now()) {
  const safeHours = TIMELINE_WINDOWS.some(item => item.hours === Number(hours)) ? Number(hours) : 24;
  const cutoff = now - safeHours * 3_600_000;
  return normalizeTimeline(items).filter(item => {
    const changedAt = Date.parse(item.changed_at);
    return changedAt >= cutoff && changedAt <= now;
  });
}

export function revisionChanges(previous, current) {
  if (!current?.incident) return [];
  if (!previous?.incident) return ['initial record'];
  const fields = [
    ['headline', 'headline'], ['summary', 'summary'], ['status', 'status'],
    ['severity', 'severity'], ['urgency', 'urgency'], ['certainty', 'certainty'],
    ['priority_score', 'priority'], ['geometry', 'location'], ['sources', 'sources'],
  ];
  return fields.filter(([key]) => (
    JSON.stringify(previous.incident?.[key] ?? null) !== JSON.stringify(current.incident?.[key] ?? null)
  )).map(([, label]) => label);
}

export function metricRows(observations, limit = 50) {
  const rows = [];
  for (const observation of Array.isArray(observations) ? observations : []) {
    for (const [key, metric] of Object.entries(observation?.metrics || {})) {
      if (!metric || !['string', 'number', 'boolean'].includes(typeof metric.value)) continue;
      rows.push({
        key,
        value: String(metric.value),
        unit: String(metric.unit || ''),
        provenance: String(metric.provenance || observation.provider_id || 'Source not reported'),
        providerId: String(observation.provider_id || ''),
      });
    }
  }
  const parsedLimit = Number(limit);
  const safeLimit = Number.isFinite(parsedLimit)
    ? Math.max(0, Math.min(200, Math.trunc(parsedLimit))) : 50;
  return rows.sort((left, right) => (
    left.key.localeCompare(right.key) || left.providerId.localeCompare(right.providerId)
  )).slice(0, safeLimit);
}

function safeUrl(value) {
  try {
    const url = new URL(value || '');
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
      ? url.href : null;
  } catch {
    return null;
  }
}

function escapeMarkup(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));
}

function singleLine(value, fallback = '') {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim();
  return text || fallback;
}

export function buildDeterministicSummary(incident, timeline = []) {
  if (!incident) return 'No incident is selected.';
  const provenance = provenanceLabel(incident).label;
  const location = singleLine(incident.location_name) || (Array.isArray(incident.centroid)
    && incident.centroid.length >= 2
    && incident.centroid.every(value => Number.isFinite(Number(value)))
    ? `${incident.centroid[1]}, ${incident.centroid[0]}` : 'Location not reported');
  const changes = normalizeTimeline(timeline).map(item => (
    `r${item.revision} ${String(item.change_type || 'updated').replaceAll('_', ' ')} ${formatUtcTimestamp(item.changed_at)}`
  ));
  const sources = [...new Set((Array.isArray(incident.sources) ? incident.sources : []).map(
    item => singleLine(item?.attribution || item?.provider_id),
  ).filter(Boolean))];
  return [
    singleLine(incident.headline, 'Untitled incident'),
    `Summary: ${singleLine(incident.summary, 'not reported')}`,
    `${provenance}. ${incident.severity || 'Unknown'} severity; ${incident.status || 'unknown'} status; ${incident.certainty || 'Unknown'} certainty.`,
    `Location: ${location}.`,
    `Priority: ${Number.isFinite(Number(incident.priority_score)) ? Number(incident.priority_score) : 0}/100 (explainable triage, not a prediction).`,
    `Last changed: ${formatUtcTimestamp(incident.last_changed_at)}.`,
    `Sources: ${sources.join(', ') || 'not reported'}.`,
    changes.length ? `Revision sequence: ${changes.join('; ')}.` : 'Revision sequence: not available.',
  ].join('\n');
}

export function buildIncidentBriefingHtml(incident, timeline = [], generatedAt = Date.now()) {
  const entries = normalizeTimeline(timeline);
  const summary = buildDeterministicSummary(incident, entries);
  const sourceItems = (Array.isArray(incident?.sources) ? incident.sources : []).map(source => {
    const label = source?.attribution || source?.provider_id || 'Source evidence';
    const url = safeUrl(source?.source_url);
    return `<li>${url ? `<a href="${escapeMarkup(url)}" target="_blank" rel="noopener noreferrer">${escapeMarkup(label)}</a>` : escapeMarkup(label)}</li>`;
  }).join('') || '<li>Source not reported</li>';
  const timelineItems = entries.map(item => (
    `<li><b>Revision ${item.revision}: ${escapeMarkup(String(item.change_type || 'updated').replaceAll('_', ' '))}</b>`
    + `<br>${escapeMarkup(formatUtcTimestamp(item.changed_at))}</li>`
  )).join('') || '<li>No retained revision in this time window.</li>';
  const generatedDate = new Date(generatedAt);
  const generatedTimestamp = Number.isFinite(generatedDate.getTime())
    ? generatedDate.toISOString() : '';
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${escapeMarkup(incident?.headline || 'Foglight incident briefing')}</title><style>body{font:14px/1.5 Segoe UI,sans-serif;max-width:800px;margin:auto;padding:32px;color:#20242a}h1{line-height:1.15}.meta{color:#59616d}pre{white-space:pre-wrap;background:#f2f4f7;padding:16px;border-left:4px solid #365f86}li{margin:6px 0}.print-bar{display:flex;gap:8px}@media print{.print-bar{display:none}}</style></head><body><div class="print-bar"><button id="incident-briefing-print">Print / save as PDF</button><button id="incident-briefing-close">Close</button></div><h1>${escapeMarkup(incident?.headline || 'Untitled incident')}</h1><p class="meta">Generated ${escapeMarkup(formatUtcTimestamp(generatedTimestamp))} · ${escapeMarkup(provenanceLabel(incident).label)}</p><pre>${escapeMarkup(summary)}</pre><h2>Revision history</h2><ol>${timelineItems}</ol><h2>Sources and provenance</h2><ul>${sourceItems}</ul><p class="meta">Foglight preserves public-source provenance. Priority is explainable triage, not a prediction.</p></body></html>`;
}
