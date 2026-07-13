import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createPointWatch,
  distanceKm,
  incidentsToCsv,
  incidentsToGeoJSON,
  isQuietHours,
  normalizeWatchRegions,
  notificationCandidates,
  offlineHistorySummary,
  pointInWatchGeometry,
  revisionKey,
  watchMatchesIncident,
} from '../../web/watch-model.js';

const incident = (overrides = {}) => ({
  schema_version: 1,
  incident_id: 'incident:earthquake:watch-fixture',
  kind: 'earthquake',
  headline: 'Fixture earthquake',
  summary: 'Observed shaking near the coast.',
  status: 'active',
  severity: 'Severe',
  urgency: 'Immediate',
  certainty: 'Observed',
  priority_score: 88,
  revision: 2,
  change_type: 'escalated',
  first_seen_at: '2026-07-10T20:00:00Z',
  last_changed_at: '2026-07-10T21:00:00Z',
  last_observed_at: '2026-07-10T21:00:00Z',
  geometry: { type: 'Point', coordinates: [179, 0] },
  centroid: [179, 0],
  sources: [{
    provider_id: 'usgs_earthquakes', attribution: 'USGS',
    source_url: 'https://earthquake.usgs.gov/example',
  }],
  ...overrides,
});

const datelinePolygon = {
  type: 'Polygon',
  coordinates: [[[170, -10], [-170, -10], [-170, 10], [170, 10], [170, -10]]],
};

test('watch regions migrate legacy keywords without deleting them', () => {
  assert.deepEqual(normalizeWatchRegions([], [' storm ', 'storm', 'quake']), [{
    id: 'legacy:keywords', label: 'Migrated keyword watches', scope: 'global',
    geometry: null, radius_km: 100, kinds: [], minimum_severity: 'Unknown',
    keywords: ['storm', 'quake'], enabled: true,
  }]);
  const normalized = normalizeWatchRegions([{
    id: 'watch:one', label: 'One', geometry: { type: 'Point', coordinates: [1, 2] },
    radius_km: 3000, kinds: ['earthquake', 'earthquake'], minimum_severity: 'bad',
  }, { id: 'watch:one', label: 'duplicate', geometry: {} }, null]);
  assert.equal(normalized.length, 1);
  assert.equal(normalized[0].radius_km, 2000);
  assert.equal(normalized[0].minimum_severity, 'Moderate');
  const combined = normalizeWatchRegions(normalized, ['new keyword']);
  assert.equal(combined.length, 2);
  assert.equal(combined[0].id, 'watch:one');
  assert.deepEqual(combined[1].keywords, ['new keyword']);
  const atCapacity = normalizeWatchRegions(Array.from({ length: 50 }, (_value, index) => ({
    id: `watch:${index}`, label: `Watch ${index}`,
    geometry: { type: 'Point', coordinates: [0, 0] },
  })), ['retained']);
  assert.equal(atCapacity.length, 51);
  assert.equal(atCapacity.at(-1).id, 'legacy:keywords');
});

test('point watch creation validates coordinates, labels, and thresholds', () => {
  const region = createPointWatch({
    label: ' Coast ', longitude: 179, latitude: 4, radiusKm: 50,
    kinds: ['earthquake'], minimumSeverity: 'Severe', keywords: ['quake'],
  });
  assert.match(region.id, /^watch:/);
  assert.equal(region.label, 'Coast');
  assert.deepEqual(region.geometry.coordinates, [179, 4]);
  assert.equal(createPointWatch({ label: '', longitude: 0, latitude: 0 }), null);
  assert.equal(createPointWatch({ label: 'bad', longitude: 181, latitude: 0 }), null);
});

test('polygon matching includes boundaries, holes, multipolygons, and the dateline', () => {
  assert.equal(pointInWatchGeometry([179, 0], datelinePolygon), true);
  assert.equal(pointInWatchGeometry([-179, 0], datelinePolygon), true);
  assert.equal(pointInWatchGeometry([170, 0], datelinePolygon), true);
  assert.equal(pointInWatchGeometry([0, 0], datelinePolygon), false);
  const withHole = {
    type: 'Polygon',
    coordinates: [
      [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
      [[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]],
    ],
  };
  assert.equal(pointInWatchGeometry([2, 2], withHole), true);
  assert.equal(pointInWatchGeometry([5, 5], withHole), false);
  assert.equal(pointInWatchGeometry([3, 5], withHole), true);
  assert.equal(pointInWatchGeometry([22, 2], {
    type: 'MultiPolygon',
    coordinates: [[[[20, 0], [25, 0], [25, 5], [20, 5], [20, 0]]]],
  }), true);
  assert.equal(pointInWatchGeometry(null, datelinePolygon), false);
  assert.equal(pointInWatchGeometry([0, 0], { type: 'LineString', coordinates: [] }), false);
});

test('distance and watch matching are inclusive and category aware', () => {
  assert.ok(distanceKm([179.5, 0], [-179.5, 0]) < 112);
  assert.equal(distanceKm(null, [0, 0]), Number.POSITIVE_INFINITY);
  const polygonRegion = {
    id: 'watch:polygon', label: 'Dateline', scope: 'region', geometry: datelinePolygon,
    radius_km: 1, kinds: ['earthquake'], minimum_severity: 'Moderate',
    keywords: ['shaking'], enabled: true,
  };
  assert.equal(watchMatchesIncident(polygonRegion, incident()), true);
  assert.equal(watchMatchesIncident({ ...polygonRegion, kinds: ['tsunami'] }, incident()), false);
  assert.equal(watchMatchesIncident({ ...polygonRegion, minimum_severity: 'Extreme' }, incident()), false);
  assert.equal(watchMatchesIncident({ ...polygonRegion, keywords: ['missing'] }, incident()), false);
  assert.equal(watchMatchesIncident({ ...polygonRegion, enabled: false }, incident()), false);
  assert.equal(watchMatchesIncident({ ...polygonRegion, scope: 'global' }, incident({ centroid: null, geometry: null })), true);
  const pointRegion = { ...polygonRegion, geometry: { type: 'Point', coordinates: [-179.5, 0] }, radius_km: 200 };
  assert.equal(watchMatchesIncident(pointRegion, incident()), true);
});

test('quiet hours support overnight, daytime, equal, invalid, and local boundaries', () => {
  const at = hour => new Date(2026, 6, 10, hour, 0, 0);
  assert.equal(isQuietHours(at(23), '22:00', '07:00'), true);
  assert.equal(isQuietHours(at(6), '22:00', '07:00'), true);
  assert.equal(isQuietHours(at(7), '22:00', '07:00'), false);
  assert.equal(isQuietHours(at(12), '09:00', '17:00'), true);
  assert.equal(isQuietHours(at(18), '09:00', '17:00'), false);
  assert.equal(isQuietHours(at(12), '09:00', '09:00'), false);
  assert.equal(isQuietHours('bad', 'bad', '17:00'), false);
});

test('notification selection requires opt-in and deduplicates revisions, snoozes, and quiet hours', () => {
  const change = { incident_id: incident().incident_id, revision: 2, change_type: 'escalated', incident: incident() };
  const region = {
    id: 'watch:global', label: 'Global quake', scope: 'global', geometry: null,
    radius_km: 100, kinds: ['earthquake'], minimum_severity: 'Moderate', keywords: [], enabled: true,
  };
  const config = {
    enabled: true, quiet_start: '22:00', quiet_end: '07:00',
    kinds: ['earthquake'], changes: ['new', 'escalated'], minimum_severity: 'Moderate',
  };
  const now = new Date(2026, 6, 10, 12).getTime();
  assert.equal(revisionKey(change), `${incident().incident_id}@2`);
  assert.equal(revisionKey({}), '');
  assert.equal(notificationCandidates({ changes: [change, change], regions: [region], config, state: {}, now }).length, 1);
  assert.deepEqual(notificationCandidates({ changes: [change], regions: [region], config: { ...config, enabled: false }, state: {}, now }), []);
  assert.deepEqual(notificationCandidates({ changes: [change], regions: [region], config, state: { seen_revision_keys: [revisionKey(change)] }, now }), []);
  assert.deepEqual(notificationCandidates({ changes: [change], regions: [region], config, state: { acknowledged_keys: [revisionKey(change)] }, now }), []);
  assert.deepEqual(notificationCandidates({
    changes: [change], regions: [region], config,
    state: { snoozed: [{ incident_id: incident().incident_id, until: new Date(now + 3600000).toISOString() }] }, now,
  }), []);
  assert.deepEqual(notificationCandidates({ changes: [change], regions: [region], config, state: {}, now: new Date(2026, 6, 10, 23) }), []);
  assert.deepEqual(notificationCandidates({ changes: [{ ...change, change_type: 'updated' }], regions: [region], config, state: {}, now }), []);
});

test('CSV export quotes every field and neutralizes spreadsheet formulas without corrupting numbers', () => {
  const csv = incidentsToCsv([incident({
    headline: '=HYPERLINK("https://evil.test")',
    summary: '+SUM(1,2)\nsecond row',
    priority_score: '=1+1',
    centroid: [-179, -2],
    sources: [{ attribution: '@source', source_url: 'javascript:alert(1)' }],
  })]);
  assert.ok(csv.startsWith('\uFEFF'));
  assert.match(csv, /"\t=HYPERLINK\(""https:\/\/evil\.test""\)"/);
  assert.match(csv, /"\t\+SUM\(1,2\) second row"/);
  assert.match(csv, /,"\t=1\+1",/);
  assert.match(csv, /,"-179","-2",/);
  assert.doesNotMatch(csv, /javascript:/);
  assert.match(csv, /"\t@source"/);
  assert.equal(incidentsToCsv(null).split('\r\n').length, 2);
});

test('GeoJSON export is bounded, canonical, provenance-rich, and rejects unsafe geometry and URLs', () => {
  const exported = incidentsToGeoJSON([incident(), incident({
    incident_id: 'incident:news:invalid',
    geometry: { type: 'Point', coordinates: [999, 0] }, centroid: null,
    sources: [{ provider_id: 'news', source_url: 'https://user:pass@example.test/' }],
  }), incident({
    incident_id: 'incident:news:redacted',
    sources: [{ provider_id: 'news', source_url: 'https://example.test/evidence?token=do-not-export&view=full' }],
  }), null], Date.parse('2026-07-11T03:00:00Z'));
  assert.equal(exported.type, 'FeatureCollection');
  assert.equal(exported.foglight_schema_version, 1);
  assert.equal(exported.generated_at, '2026-07-11T03:00:00.000Z');
  assert.equal(exported.features.length, 3);
  assert.deepEqual(exported.features[0].geometry, { type: 'Point', coordinates: [179, 0] });
  assert.equal(exported.features[0].properties.sources[0].attribution, 'USGS');
  assert.equal(exported.features[1].geometry, null);
  assert.equal(exported.features[1].properties.sources[0].source_url, null);
  assert.equal(
    exported.features[2].properties.sources[0].source_url,
    'https://example.test/evidence?token=%3Credacted%3E&view=full',
  );
  assert.doesNotMatch(JSON.stringify(exported), /do-not-export/);
  assert.equal(incidentsToGeoJSON([], 'bad').generated_at, null);
});

test('offline history reports durable revision and oldest known source age', () => {
  assert.deepEqual(offlineHistorySummary({
    revisionCursor: 12,
    lastRevisionAt: '2026-07-11T02:00:00Z',
    sourceHealth: [
      { cached_age_seconds: 60 },
      { last_success_at: '2026-07-11T01:00:00Z' },
      { last_success_at: null },
    ],
    now: Date.parse('2026-07-11T03:00:00Z'),
  }), {
    revision_cursor: 12,
    last_revision_at: '2026-07-11T02:00:00Z',
    oldest_source_age_seconds: 7200,
  });
  assert.deepEqual(offlineHistorySummary(), {
    revision_cursor: 0, last_revision_at: null, oldest_source_age_seconds: null,
  });
});

test('defensive watch and export fallbacks stay bounded on malformed optional data', () => {
  assert.deepEqual(normalizeWatchRegions(null), []);
  assert.deepEqual(normalizeWatchRegions([
    { id: '', label: 'missing id', geometry: {} },
    { id: 'missing-label', label: '', geometry: {} },
    { id: 'missing-geometry', label: 'Missing', scope: 'region' },
    {
      id: 'global', label: 'Global', scope: 'global', geometry: null,
      radius_km: 'bad', kinds: null, minimum_severity: 'Extreme', keywords: null,
      enabled: false,
    },
  ])[0], {
    id: 'global', label: 'Global', scope: 'global', geometry: null,
    radius_km: 100, kinds: [], minimum_severity: 'Extreme', keywords: [], enabled: false,
  });
  const many = Array.from({ length: 60 }, (_value, index) => ({
    id: `watch:${index}`, label: `Watch ${index}`,
    geometry: { type: 'Point', coordinates: [0, 0] },
  }));
  assert.equal(normalizeWatchRegions(many).length, 50);

  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  try {
    Object.defineProperty(globalThis, 'crypto', { configurable: true, value: null });
    assert.match(createPointWatch({ label: 'fallback', longitude: 0, latitude: 0, radiusKm: 'bad', minimumSeverity: 'bad' }).id, /^watch:/);
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'crypto', descriptor);
  }

  assert.equal(pointInWatchGeometry([0, 0], { type: 'Polygon', coordinates: [] }), false);
  assert.equal(pointInWatchGeometry([0, 0], { type: 'Polygon', coordinates: [[[0, 0], [1, 0], ['bad', 1], [0, 0]]] }), false);
  assert.equal(pointInWatchGeometry([0, 0], { type: 'MultiPolygon', coordinates: null }), false);
  assert.equal(watchMatchesIncident(null, incident()), false);
  assert.equal(watchMatchesIncident({
    enabled: true, scope: 'region', geometry: datelinePolygon,
    minimum_severity: 'Unknown', kinds: [], keywords: [],
  }, incident({ centroid: null, geometry: null })), false);

  const noConfig = notificationCandidates({ changes: [], regions: [], config: {}, state: {} });
  assert.deepEqual(noConfig, []);
  const region = {
    id: 'global', label: 'Global', scope: 'global', geometry: null,
    radius_km: 100, kinds: [], minimum_severity: 'Unknown', keywords: [], enabled: true,
  };
  const activeConfig = { enabled: true, quiet_start: '00:00', quiet_end: '00:00', kinds: ['earthquake'], minimum_severity: 'Unknown' };
  assert.deepEqual(notificationCandidates({ changes: [], regions: [], config: activeConfig, state: {}, now: 0 }), []);
  assert.deepEqual(notificationCandidates({
    changes: [{ incident_id: incident().incident_id, revision: 3, change_type: 'new', incident: incident({ revision: 3 }) }],
    regions: [region], config: { ...activeConfig, kinds: [], changes: ['new'] }, state: {}, now: 0,
  }), []);
  assert.deepEqual(notificationCandidates({ changes: [{ incident: null }], regions: [region], config: activeConfig, state: {}, now: 0 }), []);
  assert.deepEqual(notificationCandidates({
    changes: [{ incident_id: incident().incident_id, revision: 3, change_type: 'updated', incident: incident({ revision: 3 }) }],
    regions: [region], config: activeConfig, state: {}, now: 0,
  }), []);
  const changed = {
    incident_id: incident().incident_id, revision: 7, change_type: 'new',
    incident: incident({ revision: 7, change_type: 'new' }),
  };
  assert.deepEqual(notificationCandidates({
    changes: [changed], regions: [region],
    config: { ...activeConfig, kinds: ['tsunami'], changes: ['new'] }, state: {}, now: 0,
  }), []);
  assert.deepEqual(notificationCandidates({
    changes: [changed], regions: [region],
    config: { ...activeConfig, minimum_severity: 'Extreme', changes: ['new'] }, state: {}, now: 0,
  }), []);
  assert.deepEqual(notificationCandidates({
    changes: [changed], regions: [{ ...region, keywords: ['missing'] }],
    config: { ...activeConfig, changes: ['new'] },
    state: { snoozed: [{ incident_id: changed.incident_id, until: 'bad' }] }, now: 0,
  }), []);
  assert.equal(notificationCandidates({
    changes: [changed], regions: [region], config: { ...activeConfig, changes: ['new'] },
    state: { snoozed: [{ incident_id: changed.incident_id, until: '1970-01-01T00:00:00Z' }] }, now: 1,
  }).length, 1);
  assert.equal(notificationCandidates({
    changes: [changed], regions: [region],
    config: { enabled: true, quiet_start: '00:00', quiet_end: '00:00', changes: ['new'] },
    state: {}, now: 1,
  }).length, 1);

  const csv = incidentsToCsv([null, incident({ centroid: null, geometry: null, sources: [{ provider_id: '', attribution: '', source_url: '' }] })]);
  assert.match(csv, /"","","Source"/);
  const geojson = incidentsToGeoJSON([null, incident({
    centroid: null, geometry: null, priority_score: 'bad', sources: null,
  })]);
  assert.equal(geojson.features[0].geometry, null);
  assert.equal(geojson.features[0].properties.priority_score, 0);
  assert.deepEqual(geojson.features[0].properties.sources, []);
});
