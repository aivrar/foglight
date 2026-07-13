export const FILTERS = Object.freeze([
  { id: 'global', label: 'Global' },
  { id: 'natural', label: 'Natural hazards' },
  { id: 'weather', label: 'Severe weather' },
  { id: 'conflict', label: 'Conflict / humanitarian' },
  { id: 'mobility', label: 'Aviation / marine' },
  { id: 'signals', label: 'Signals' },
]);

const KIND_PRESENTATION = Object.freeze({
  earthquake: ['EQ', 'Earthquake', 'diamond'],
  weather_alert: ['WX', 'Weather alert', 'square'],
  tropical_cyclone: ['TC', 'Tropical cyclone', 'circle'],
  tsunami: ['TS', 'Tsunami', 'triangle'],
  volcano: ['VO', 'Volcano', 'triangle'],
  wildfire: ['WF', 'Wildfire', 'diamond'],
  natural_event: ['HZ', 'Natural event', 'circle'],
  disaster: ['DS', 'Disaster', 'square'],
  disaster_declaration: ['FD', 'FEMA declaration', 'square'],
  conflict_report: ['CF', 'Conflict report', 'diamond'],
  humanitarian_report: ['HR', 'Humanitarian report', 'square'],
  news_item: ['NW', 'News coverage', 'circle'],
  aircraft: ['AV', 'Aircraft', 'triangle'],
  aviation_hazard: ['AW', 'Aviation hazard', 'triangle'],
  marine_observation: ['MO', 'Marine observation', 'circle'],
  water_level: ['WL', 'Water level', 'square'],
  fireball: ['FB', 'Fireball observation', 'diamond'],
  space_weather: ['SW', 'Space weather', 'circle'],
  orbital_position: ['OR', 'Orbital position', 'circle'],
  market_snapshot: ['MK', 'Market signal', 'square'],
  technology_activity: ['TE', 'Technology signal', 'diamond'],
});

const FILTER_KINDS = Object.freeze({
  natural: new Set([
    'earthquake', 'tropical_cyclone', 'tsunami', 'volcano', 'wildfire',
    'natural_event', 'disaster', 'disaster_declaration',
  ]),
  weather: new Set(['weather_alert']),
  conflict: new Set(['conflict_report', 'humanitarian_report', 'news_item']),
  mobility: new Set([
    'aircraft', 'aviation_hazard', 'marine_observation', 'water_level',
    'tropical_cyclone', 'tsunami',
  ]),
  signals: new Set([
    'fireball', 'space_weather', 'orbital_position', 'market_snapshot',
    'technology_activity', 'unknown',
  ]),
});

export const STATE_COPY = Object.freeze({
  loading: ['Loading current picture', 'Reading the local incident history.'],
  first_run: ['Ready out of the box', 'No account or API key is required. Live sources will appear as they report.'],
  empty: ['No incidents to show', 'No current incident matches this view. Source status remains available below.'],
  partial: ['Some sources need attention', 'Showing the current information that is available locally.'],
  stale: ['Showing stored information', 'No source is currently live; ages and source status are preserved.'],
  offline: ['Local incident service unavailable', 'Standard mode remains available while Foglight reconnects.'],
  ready: ['Current picture is ready', 'Priority is explainable and does not imply certainty.'],
});

export function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export function kindPresentation(kind) {
  const value = KIND_PRESENTATION[kind] || ['OT', 'Other signal', 'circle'];
  return { abbreviation: value[0], label: value[1], shape: value[2] };
}

export function filterIncidents(incidents, filter = 'global') {
  const source = Array.isArray(incidents) ? incidents : [];
  const kinds = FILTER_KINDS[filter];
  const selected = kinds ? source.filter(item => kinds.has(item?.kind)) : source;
  return selected.slice().sort((left, right) => (
    finiteNumber(right?.priority_score) - finiteNumber(left?.priority_score)
    || String(right?.last_changed_at || '').localeCompare(String(left?.last_changed_at || ''))
    || String(left?.incident_id || '').localeCompare(String(right?.incident_id || ''))
  ));
}

export function formatIncidentAge(timestamp, now = Date.now()) {
  const parsed = Date.parse(timestamp || '');
  if (!Number.isFinite(parsed)) return 'age unknown';
  const seconds = Math.max(0, Math.floor((now - parsed) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function formatLocation(incident) {
  const centroid = incident?.centroid;
  if (!Array.isArray(centroid) || centroid.length < 2) return 'Location not reported';
  const lon = Number(centroid[0]);
  const lat = Number(centroid[1]);
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return 'Location not reported';
  const latitude = `${Math.abs(lat).toFixed(1)}°${lat < 0 ? 'S' : 'N'}`;
  const longitude = `${Math.abs(lon).toFixed(1)}°${lon < 0 ? 'W' : 'E'}`;
  return `${latitude}, ${longitude}`;
}

export function priorityExplanation(incident) {
  const components = incident?.priority_components || {};
  const parts = [
    ['impact', 'impact'], ['urgency', 'urgency'], ['freshness', 'freshness'],
    ['corroboration', 'corroboration'], ['watch_region', 'watch relevance'],
  ].filter(([key]) => finiteNumber(components[key]) !== 0)
    .map(([key, label]) => `${label} ${finiteNumber(components[key]) > 0 ? '+' : ''}${finiteNumber(components[key])}`);
  const penalty = finiteNumber(components.penalty);
  if (penalty) parts.push(`status/age ${penalty}`);
  return parts.length ? parts.join(' · ') : 'No scoring components reported';
}

export function deriveOverviewState({ loaded, failed, incidents, health, firstRun }) {
  if (!loaded && !failed) return 'loading';
  const items = Array.isArray(incidents) ? incidents : [];
  const counts = health?.counts || {};
  const live = finiteNumber(counts.live);
  const cached = finiteNumber(counts.cached);
  const stale = finiteNumber(counts.stale);
  const errors = finiteNumber(counts.error);
  if (failed || (errors > 0 && live + cached + stale === 0)) return 'offline';
  if (firstRun) return 'first_run';
  if (!items.length) return 'empty';
  if (errors > 0 || finiteNumber(counts.pending) > 0) return 'partial';
  if (live === 0 && cached + stale > 0) return 'stale';
  return 'ready';
}

export function summarizeChanges(changes) {
  const items = Array.isArray(changes) ? changes : [];
  if (!items.length) return 'No changes since this view opened.';
  const counts = {};
  for (const item of items) {
    const type = String(item?.change_type || 'updated').replaceAll('_', ' ');
    counts[type] = (counts[type] || 0) + 1;
  }
  return Object.entries(counts)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([type, count]) => `${count} ${type}`)
    .join(' · ');
}
