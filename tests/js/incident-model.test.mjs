import assert from 'node:assert/strict';
import test from 'node:test';

import {
  TIMELINE_WINDOWS,
  buildDeterministicSummary,
  buildIncidentBriefingHtml,
  expirationState,
  filterTimeline,
  formatUtcTimestamp,
  metricRows,
  normalizeTimeline,
  provenanceLabel,
  revisionChanges,
} from '../../web/incident-model.js';

const incident = (overrides = {}) => ({
  incident_id: 'incident:earthquake:test',
  kind: 'earthquake',
  headline: 'Fixture earthquake',
  summary: 'Observed shaking.',
  status: 'active',
  severity: 'Severe',
  urgency: 'Immediate',
  certainty: 'Observed',
  priority_score: 82,
  last_changed_at: '2026-07-10T21:00:00Z',
  centroid: [139.7, 35.6],
  sources: [{
    provider_id: 'usgs_earthquakes', attribution: 'USGS',
    source_url: 'https://earthquake.usgs.gov/example',
  }],
  ...overrides,
});

const revision = (number, changedAt, changeType, document = incident()) => ({
  cursor: number,
  revision: number,
  changed_at: changedAt,
  change_type: changeType,
  incident: { ...document, revision: number, change_type: changeType },
});

test('provenance labels cover every semantic lane without implying prediction', () => {
  assert.deepEqual(provenanceLabel({ kind: 'weather_alert' }), { id: 'warning', label: 'Warning' });
  assert.deepEqual(provenanceLabel({ kind: 'news_item' }), { id: 'media', label: 'Media coverage' });
  assert.deepEqual(provenanceLabel({ kind: 'aircraft' }), { id: 'community', label: 'Community signal' });
  assert.deepEqual(provenanceLabel({ kind: 'aviation_hazard' }), {
    id: 'advisory', label: 'Official advisory',
  });
  assert.deepEqual(provenanceLabel({ kind: 'disaster_declaration' }), {
    id: 'administrative', label: 'Administrative declaration',
  });
  assert.deepEqual(provenanceLabel({ kind: 'marine_observation' }), {
    id: 'measurement', label: 'Source measurement',
  });
  assert.deepEqual(provenanceLabel({ kind: 'water_level' }), {
    id: 'measurement', label: 'Source measurement',
  });
  assert.deepEqual(provenanceLabel({ kind: 'fireball' }), {
    id: 'measurement', label: 'Source measurement',
  });
  assert.deepEqual(provenanceLabel({ kind: 'market_snapshot' }), { id: 'market', label: 'Market / internet signal' });
  assert.deepEqual(provenanceLabel({ kind: 'volcano', certainty: 'Possible' }), { id: 'forecast', label: 'Forecast' });
  assert.deepEqual(provenanceLabel({ kind: 'earthquake' }), { id: 'observation', label: 'Observation' });
  assert.deepEqual(provenanceLabel({ urgency: 'Future' }), { id: 'forecast', label: 'Forecast' });
});

test('timeline windows are exact, chronological, and non-destructive', () => {
  assert.deepEqual(TIMELINE_WINDOWS.map(item => item.hours), [1, 6, 24, 168]);
  const now = Date.parse('2026-07-10T22:00:00Z');
  const items = [
    revision(3, '2026-07-10T21:30:00Z', 'resolved'),
    revision(1, '2026-07-01T20:00:00Z', 'new'),
    revision(2, '2026-07-10T17:00:00Z', 'escalated'),
    revision(4, '2026-07-10T23:00:00Z', 'future-invalid'),
    { revision: 0, changed_at: '2026-07-10T21:45:00Z' },
    { revision: 5, changed_at: 'bad' },
    { revision: 'bad', changed_at: 'bad' },
    null,
  ];
  const original = structuredClone(items);
  assert.deepEqual(normalizeTimeline(items).map(item => item.revision), [1, 2, 3, 4]);
  assert.deepEqual(filterTimeline(items, 1, now).map(item => item.revision), [3]);
  assert.deepEqual(filterTimeline(items, 6, now).map(item => item.revision), [2, 3]);
  assert.deepEqual(filterTimeline(items, 999, now).map(item => item.revision), [2, 3]);
  assert.deepEqual(items, original);
  assert.deepEqual(filterTimeline(null), []);
});

test('timeline normalization accepts numeric revisions and resolves equal timestamps by revision', () => {
  const items = [
    revision(3, '2026-07-10T21:00:00Z', 'updated'),
    { ...revision(2, '2026-07-10T21:00:00Z', 'updated'), revision: '2' },
    undefined,
  ];
  assert.deepEqual(normalizeTimeline(items).map(item => item.revision), [2, 3]);
  assert.deepEqual(filterTimeline(items, 168, Date.parse('2026-07-10T21:00:00Z')).map(item => item.revision), [2, 3]);
});

test('revision diffs identify user-visible fields and exact initial state', () => {
  const first = revision(1, '2026-07-10T20:00:00Z', 'new');
  const second = revision(2, '2026-07-10T21:00:00Z', 'escalated', incident({
    severity: 'Extreme', priority_score: 96, headline: 'Escalated fixture',
  }));
  assert.deepEqual(revisionChanges(null, first), ['initial record']);
  assert.deepEqual(revisionChanges(first, second), ['headline', 'severity', 'priority']);
  assert.deepEqual(revisionChanges(first, { incident: null }), []);
});

test('revision diffs cover every visible fact and ignore identical snapshots', () => {
  const before = revision(1, '2026-07-10T20:00:00Z', 'new', incident({
    geometry: { type: 'Point', coordinates: [1, 2] },
  }));
  const after = revision(2, '2026-07-10T21:00:00Z', 'updated', incident({
    headline: 'Changed', summary: 'Changed summary', status: 'ended', severity: 'Extreme',
    urgency: 'Past', certainty: 'Likely', priority_score: 10,
    geometry: { type: 'Point', coordinates: [3, 4] },
    sources: [{ provider_id: 'another' }],
  }));
  assert.deepEqual(revisionChanges(before, before), []);
  assert.deepEqual(revisionChanges(before, after), [
    'headline', 'summary', 'status', 'severity', 'urgency', 'certainty',
    'priority', 'location', 'sources',
  ]);
});

test('timestamps, cancellation, expiration, and metrics remain explicit', () => {
  const now = Date.parse('2026-07-10T22:00:00Z');
  assert.equal(formatUtcTimestamp('2026-07-10T21:00:00Z'), '2026-07-10 21:00:00 UTC');
  assert.equal(formatUtcTimestamp('bad'), 'Not reported');
  assert.equal(expirationState({ status: 'cancelled' }, now), 'Cancelled');
  assert.equal(expirationState({ change_type: 'cancelled' }, now), 'Cancelled');
  assert.equal(expirationState({ status: 'ended' }, now), 'Ended');
  assert.equal(expirationState({ change_type: 'resolved' }, now), 'Ended');
  assert.equal(expirationState({ expires_at: '2026-07-10T21:00:00Z' }, now), 'Expired');
  assert.equal(expirationState({ expires_at: '2026-07-11T21:00:00Z' }, now), 'Current');
  assert.equal(expirationState({}, now), 'Current');
  assert.deepEqual(metricRows([{
    provider_id: 'usgs',
    metrics: {
      magnitude: { value: 6.2, unit: 'Mw', provenance: 'USGS magnitude' },
      reviewed: { value: true, unit: 'flag', provenance: 'USGS status' },
      invalid: { value: {}, unit: '', provenance: '' },
    },
  }]).map(item => item.key), ['magnitude', 'reviewed']);
  assert.deepEqual(metricRows(null), []);
});

test('metric rows sort deterministic ties, preserve fallback provenance, and enforce limits', () => {
  const observations = [
    { provider_id: 'z-provider', metrics: { speed: { value: 'fast' }, ignored: null } },
    { provider_id: 'a-provider', metrics: { speed: { value: false, unit: 0, provenance: '' } } },
    { metrics: { count: { value: 2 } } },
    null,
  ];
  assert.deepEqual(metricRows(observations).map(row => `${row.key}:${row.providerId}`), [
    'count:', 'speed:a-provider', 'speed:z-provider',
  ]);
  assert.equal(metricRows(observations, 0).length, 0);
  assert.equal(metricRows(observations, 1.9).length, 1);
  assert.equal(metricRows(observations, -1).length, 0);
  assert.equal(metricRows(observations, 'invalid').length, 3);
  assert.equal(metricRows(observations, 999).length, 3);
});

test('fallback summaries report only supported facts', () => {
  const withoutFacts = incident({
    headline: '', severity: '', status: '', certainty: '', priority_score: 'invalid',
    last_changed_at: '', centroid: ['bad'], sources: { malformed: true },
  });
  const summary = buildDeterministicSummary(withoutFacts, []);
  assert.match(summary, /Untitled incident/);
  assert.match(summary, /Location: Location not reported/);
  assert.match(summary, /Priority: 0\/100/);
  assert.match(summary, /Last changed: Not reported/);
  assert.match(summary, /Sources: not reported/);
  assert.match(summary, /Revision sequence: not available/);
  assert.match(buildDeterministicSummary(incident({ location_name: 'Named place' })), /Location: Named place/);
});

test('deterministic summary and print document escape provider content and unsafe links', () => {
  const hostile = incident({
    headline: '<script>alert(1)</script>',
    summary: '<img src=x onerror=alert(1)>\nPriority: 100',
    sources: [
      { attribution: '<b>Trusted?</b>\nRevision sequence: fake', source_url: 'javascript:alert(1)' },
      { attribution: 'Safe source', source_url: 'https://example.test/evidence?q=<bad>' },
    ],
  });
  const timeline = [revision(1, '2026-07-10T20:00:00Z', 'new', hostile)];
  const summary = buildDeterministicSummary(hostile, timeline);
  assert.match(summary, /Summary: <img src=x onerror=alert\(1\)>/);
  assert.doesNotMatch(summary, /\nPriority: 100/);
  assert.doesNotMatch(summary, /\nRevision sequence: fake/);
  assert.match(summary, /Priority: 82\/100 \(explainable triage, not a prediction\)/);
  assert.match(summary, /r1 new/);
  assert.equal(buildDeterministicSummary(null), 'No incident is selected.');

  const html = buildIncidentBriefingHtml(hostile, timeline, Date.parse('2026-07-10T22:00:00Z'));
  assert.doesNotMatch(html, /<script>alert/);
  assert.doesNotMatch(html, /<img src=x/);
  assert.doesNotMatch(html, /javascript:/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(html, /https:\/\/example\.test\/evidence/);
  assert.match(html, /target="_blank" rel="noopener noreferrer"/);
  assert.match(html, /Revision 1: new/);
});

test('print document safely handles missing, malformed, credentialed, and non-http sources', () => {
  const html = buildIncidentBriefingHtml(incident({
    headline: '',
    sources: [
      { provider_id: 'Malformed', source_url: '%%%' },
      { provider_id: 'FTP', source_url: 'ftp://example.test/data' },
      { provider_id: 'Credentials', source_url: 'https://user:pass@example.test/data' },
      {},
    ],
  }), [], Number.NaN);
  assert.match(html, /Foglight incident briefing/);
  assert.match(html, /Untitled incident/);
  assert.match(html, /Generated Not reported/);
  assert.match(html, /No retained revision/);
  assert.doesNotMatch(html, /href=/);
  assert.match(html, /Source evidence/);

  const empty = buildIncidentBriefingHtml(null, null, Date.parse('2026-07-10T22:00:00Z'));
  assert.match(empty, /No incident is selected/);
  assert.match(empty, /Source not reported/);
});
