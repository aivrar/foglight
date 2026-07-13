import { safeHttpUrl } from './core.js';
import { incidentPoint, sanitizeGeometry, validPoint } from './map-model.js';

export const SEVERITY_ORDER = Object.freeze({
  Unknown: 0, Minor: 1, Moderate: 2, Severe: 3, Extreme: 4,
});

const DEFAULT_CHANGES = Object.freeze(['new', 'escalated']);

function text(value, fallback = '') {
  const output = String(value ?? '').replace(/\s+/g, ' ').trim();
  return output || fallback;
}

function uniqueText(items, limit = 20) {
  return [...new Set((Array.isArray(items) ? items : []).map(item => text(item)).filter(Boolean))]
    .slice(0, limit);
}

function watchId() {
  if (globalThis.crypto?.randomUUID) return `watch:${globalThis.crypto.randomUUID()}`;
  return `watch:${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function normalizeWatchRegions(regions, legacyKeywords = []) {
  const output = [];
  const seen = new Set();
  let structuredCount = 0;
  const keywords = uniqueText(legacyKeywords, 100);
  const migrateLegacy = keywords.length > 0;
  for (const item of (Array.isArray(regions) ? regions : []).slice(0, 51)) {
    const id = text(item?.id);
    if (migrateLegacy && id === 'legacy:keywords') continue;
    const label = text(item?.label);
    const scope = item?.scope === 'global' ? 'global' : 'region';
    const geometry = item?.geometry && typeof item.geometry === 'object'
      ? structuredClone(item.geometry) : null;
    if (!id || !label || seen.has(id) || (scope === 'region' && !geometry)) continue;
    const isLegacy = id === 'legacy:keywords' && scope === 'global';
    if (!isLegacy && structuredCount >= 50) continue;
    const radius = Number(item?.radius_km);
    output.push({
      id,
      label,
      scope,
      geometry,
      radius_km: Number.isFinite(radius) ? Math.max(1, Math.min(2000, radius)) : 100,
      kinds: uniqueText(item?.kinds),
      minimum_severity: Object.hasOwn(SEVERITY_ORDER, item?.minimum_severity)
        ? item.minimum_severity : 'Moderate',
      keywords: uniqueText(item?.keywords),
      enabled: item?.enabled !== false,
    });
    seen.add(id);
    if (!isLegacy) structuredCount += 1;
  }
  if (migrateLegacy) {
    output.push({
      id: 'legacy:keywords',
      label: 'Migrated keyword watches',
      scope: 'global',
      geometry: null,
      radius_km: 100,
      kinds: [],
      minimum_severity: 'Unknown',
      keywords,
      enabled: true,
    });
  }
  return output;
}

export function createPointWatch({ label, longitude, latitude, radiusKm = 100, kinds = [], minimumSeverity = 'Moderate', keywords = [] }) {
  const point = validPoint([longitude, latitude]);
  const cleanLabel = text(label);
  if (!point || !cleanLabel) return null;
  const radius = Number(radiusKm);
  return {
    id: watchId(),
    label: cleanLabel.slice(0, 80),
    scope: 'region',
    geometry: { type: 'Point', coordinates: point },
    radius_km: Number.isFinite(radius) ? Math.max(1, Math.min(2000, radius)) : 100,
    kinds: uniqueText(kinds),
    minimum_severity: Object.hasOwn(SEVERITY_ORDER, minimumSeverity) ? minimumSeverity : 'Moderate',
    keywords: uniqueText(keywords),
    enabled: true,
  };
}

function longitudeNear(value, anchor) {
  return anchor + ((((value - anchor) + 180) % 360 + 360) % 360) - 180;
}

function ringRelation(point, ring) {
  if (!Array.isArray(ring) || ring.length < 4) return 0;
  const [anchor, y] = point;
  const unwrapped = [];
  for (const value of ring) {
    const valid = validPoint(value);
    if (!valid) return 0;
    const longitude = unwrapped.length
      ? longitudeNear(valid[0], unwrapped.at(-1)[0])
      : longitudeNear(valid[0], anchor);
    unwrapped.push([longitude, valid[1]]);
  }
  let inside = false;
  for (let index = 0, previous = unwrapped.length - 1; index < unwrapped.length; previous = index, index += 1) {
    const [x1, y1] = unwrapped[previous];
    const [x2, y2] = unwrapped[index];
    const cross = (anchor - x1) * (y2 - y1) - (y - y1) * (x2 - x1);
    const onSegment = Math.abs(cross) <= 1e-9
      && anchor >= Math.min(x1, x2) - 1e-9 && anchor <= Math.max(x1, x2) + 1e-9
      && y >= Math.min(y1, y2) - 1e-9 && y <= Math.max(y1, y2) + 1e-9;
    if (onSegment) return 2;
    if ((y1 > y) !== (y2 > y)) {
      const intersection = x1 + ((y - y1) * (x2 - x1)) / (y2 - y1);
      if (intersection > anchor) inside = !inside;
    }
  }
  return inside ? 1 : 0;
}

function pointInPolygon(point, polygon) {
  if (!Array.isArray(polygon) || !polygon.length) return false;
  const outer = ringRelation(point, polygon[0]);
  if (!outer) return false;
  for (const hole of polygon.slice(1)) {
    const relation = ringRelation(point, hole);
    if (relation === 2) return true;
    if (relation === 1) return false;
  }
  return true;
}

export function pointInWatchGeometry(pointValue, geometry) {
  const point = validPoint(pointValue);
  if (!point || !geometry || typeof geometry !== 'object') return false;
  if (geometry.type === 'Polygon') return pointInPolygon(point, geometry.coordinates);
  if (geometry.type === 'MultiPolygon') {
    return Array.isArray(geometry.coordinates)
      && geometry.coordinates.some(polygon => pointInPolygon(point, polygon));
  }
  return false;
}

export function distanceKm(leftValue, rightValue) {
  const left = validPoint(leftValue);
  const right = validPoint(rightValue);
  if (!left || !right) return Number.POSITIVE_INFINITY;
  const radians = value => value * Math.PI / 180;
  const deltaLat = radians(right[1] - left[1]);
  const deltaLon = radians(longitudeNear(right[0], left[0]) - left[0]);
  const a = Math.sin(deltaLat / 2) ** 2
    + Math.cos(radians(left[1])) * Math.cos(radians(right[1])) * Math.sin(deltaLon / 2) ** 2;
  return 6371.0088 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0, 1 - a)));
}

export function watchMatchesIncident(region, incident) {
  if (!region?.enabled || !incident) return false;
  if (region.kinds?.length && !region.kinds.includes(String(incident.kind || 'unknown'))) return false;
  const minimum = SEVERITY_ORDER[region.minimum_severity] ?? SEVERITY_ORDER.Moderate;
  if ((SEVERITY_ORDER[incident.severity] ?? 0) < minimum) return false;
  if (region.keywords?.length) {
    const searchable = text(`${incident.headline || ''} ${incident.summary || ''} ${incident.location_name || ''}`).toLowerCase();
    if (!region.keywords.some(keyword => searchable.includes(text(keyword).toLowerCase()))) return false;
  }
  if (region.scope === 'global') return true;
  const point = incidentPoint(incident);
  if (!point) return false;
  if (region.geometry?.type === 'Point') {
    return distanceKm(region.geometry.coordinates, point) <= Number(region.radius_km || 100);
  }
  return pointInWatchGeometry(point, region.geometry);
}

function minutes(value) {
  const match = /^(\d{2}):(\d{2})$/.exec(String(value || ''));
  if (!match) return null;
  const output = Number(match[1]) * 60 + Number(match[2]);
  return Number(match[1]) < 24 && Number(match[2]) < 60 ? output : null;
}

export function isQuietHours(nowValue, start = '22:00', end = '07:00') {
  const startMinutes = minutes(start);
  const endMinutes = minutes(end);
  const now = nowValue instanceof Date ? nowValue : new Date(nowValue);
  if (startMinutes == null || endMinutes == null || startMinutes === endMinutes
      || !Number.isFinite(now.getTime())) return false;
  const current = now.getHours() * 60 + now.getMinutes();
  return startMinutes < endMinutes
    ? current >= startMinutes && current < endMinutes
    : current >= startMinutes || current < endMinutes;
}

export function revisionKey(change) {
  const incident = change?.incident || change;
  const id = text(change?.incident_id || incident?.incident_id);
  const revision = Number(change?.revision ?? incident?.revision);
  return id && Number.isInteger(revision) && revision >= 1 ? `${id}@${revision}` : '';
}

export function notificationCandidates({ changes, regions, config, state, now = Date.now() }) {
  if (!config?.enabled || isQuietHours(now, config.quiet_start, config.quiet_end)) return [];
  const selectedRegions = normalizeWatchRegions(regions).filter(item => item.enabled);
  if (!selectedRegions.length) return [];
  const seen = new Set(state?.seen_revision_keys || []);
  const acknowledged = new Set(state?.acknowledged_keys || []);
  const snoozed = new Map((Array.isArray(state?.snoozed) ? state.snoozed : []).map(
    item => [item?.incident_id, Date.parse(item?.until || '')],
  ));
  const hasKindFilter = Array.isArray(config?.kinds);
  const kinds = new Set(hasKindFilter ? config.kinds : []);
  const changesAllowed = new Set(Array.isArray(config?.changes) ? config.changes : DEFAULT_CHANGES);
  const minimum = SEVERITY_ORDER[config?.minimum_severity] ?? SEVERITY_ORDER.Moderate;
  const output = [];
  for (const change of Array.isArray(changes) ? changes : []) {
    const incident = change?.incident;
    const key = revisionKey(change);
    const changeType = String(change?.change_type || incident?.change_type || 'updated');
    if (!incident || !key || seen.has(key) || acknowledged.has(key) || !changesAllowed.has(changeType)) continue;
    if (hasKindFilter && !kinds.has(String(incident.kind || 'unknown'))) continue;
    if ((SEVERITY_ORDER[incident.severity] ?? 0) < minimum) continue;
    if ((snoozed.get(incident.incident_id) || 0) > Number(now)) continue;
    const matching = selectedRegions.filter(region => watchMatchesIncident(region, incident));
    if (!matching.length) continue;
    output.push({ key, incident, change_type: changeType, watch_labels: matching.map(item => item.label) });
    seen.add(key);
  }
  return output;
}

function csvCell(value, { numeric = false } = {}) {
  const hasNumber = numeric && value != null && String(value).trim() !== ''
    && Number.isFinite(Number(value));
  let output = hasNumber ? String(Number(value)) : text(value);
  if (!hasNumber && /^[=+\-@＝＋－＠]/u.test(output)) output = `\t${output}`;
  return `"${output.replaceAll('"', '""')}"`;
}

function sourceText(incident) {
  return (Array.isArray(incident?.sources) ? incident.sources : []).slice(0, 20).map(source => {
    const label = text(source?.attribution || source?.provider_id, 'Source');
    const url = safeHttpUrl(source?.source_url);
    return url ? `${label} (${url})` : label;
  }).join('; ');
}

export function incidentsToCsv(incidents) {
  const columns = [
    'schema_version', 'incident_id', 'kind', 'headline', 'summary', 'status', 'severity',
    'urgency', 'certainty', 'priority_score', 'first_seen_at', 'last_changed_at',
    'last_observed_at', 'longitude', 'latitude', 'sources',
  ];
  const rows = [columns.map(csvCell).join(',')];
  for (const incident of (Array.isArray(incidents) ? incidents : []).slice(0, 200)) {
    if (!incident?.incident_id) continue;
    const point = incidentPoint(incident) || [null, null];
    const values = [
      [incident.schema_version ?? 1, true], [incident.incident_id], [incident.kind],
      [incident.headline], [incident.summary], [incident.status], [incident.severity],
      [incident.urgency], [incident.certainty], [incident.priority_score, true],
      [incident.first_seen_at], [incident.last_changed_at], [incident.last_observed_at],
      [point[0], true], [point[1], true], [sourceText(incident)],
    ];
    rows.push(values.map(([value, numeric]) => csvCell(value, { numeric })).join(','));
  }
  return `\uFEFF${rows.join('\r\n')}\r\n`;
}

export function incidentsToGeoJSON(incidents, generatedAt = Date.now()) {
  const features = [];
  for (const incident of (Array.isArray(incidents) ? incidents : []).slice(0, 200)) {
    if (!incident?.incident_id) continue;
    let geometry = sanitizeGeometry(incident.geometry, { maxPoints: 2000 });
    const point = incidentPoint(incident);
    if (!geometry && point) geometry = { type: 'Point', coordinates: point };
    const sources = (Array.isArray(incident.sources) ? incident.sources : []).slice(0, 20).map(source => ({
      provider_id: text(source?.provider_id),
      attribution: text(source?.attribution || source?.provider_id),
      source_url: safeHttpUrl(source?.source_url) || null,
    }));
    features.push({
      type: 'Feature',
      id: String(incident.incident_id),
      geometry,
      properties: {
        schema_version: incident.schema_version ?? 1,
        incident_id: String(incident.incident_id),
        kind: text(incident.kind, 'unknown'),
        headline: text(incident.headline, 'Untitled incident'),
        summary: text(incident.summary),
        status: text(incident.status, 'unknown'),
        severity: text(incident.severity, 'Unknown'),
        urgency: text(incident.urgency, 'Unknown'),
        certainty: text(incident.certainty, 'Unknown'),
        priority_score: Number.isFinite(Number(incident.priority_score)) ? Number(incident.priority_score) : 0,
        first_seen_at: incident.first_seen_at || null,
        last_changed_at: incident.last_changed_at || null,
        last_observed_at: incident.last_observed_at || null,
        sources,
      },
    });
  }
  const date = new Date(generatedAt);
  return {
    type: 'FeatureCollection',
    foglight_schema_version: 1,
    generated_at: Number.isFinite(date.getTime()) ? date.toISOString() : null,
    features,
  };
}

export function offlineHistorySummary({ revisionCursor = 0, lastRevisionAt = null, sourceHealth = [], now = Date.now() } = {}) {
  const ages = (Array.isArray(sourceHealth) ? sourceHealth : []).map(item => {
    const seconds = Number(item?.cached_age_seconds);
    if (Number.isFinite(seconds) && seconds >= 0) return seconds;
    const parsed = Date.parse(item?.last_success_at || '');
    return Number.isFinite(parsed) ? Math.max(0, Math.floor((Number(now) - parsed) / 1000)) : null;
  }).filter(Number.isFinite);
  const oldest = ages.length ? Math.max(...ages) : null;
  return {
    revision_cursor: Math.max(0, Number(revisionCursor) || 0),
    last_revision_at: Number.isFinite(Date.parse(lastRevisionAt || '')) ? lastRevisionAt : null,
    oldest_source_age_seconds: oldest,
  };
}
