import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  deriveOverviewState,
  filterIncidents,
  finiteNumber,
  formatIncidentAge,
  formatLocation,
  kindPresentation,
  priorityExplanation,
  summarizeChanges,
} from '../../web/overview-model.js';

const fixture = (id, kind, score, changed = '2026-07-10T20:00:00Z') => ({
  incident_id: id,
  kind,
  priority_score: score,
  last_changed_at: changed,
  priority_components: {
    impact: 30, urgency: 20, freshness: 15, corroboration: 5,
    watch_region: 0, penalty: -20,
  },
  centroid: [139.7, 35.6],
});

test('overview filters are deterministic and priority ordered', () => {
  const incidents = [
    fixture('weather', 'weather_alert', 70),
    fixture('quake-low', 'earthquake', 30),
    fixture('quake-high', 'earthquake', 80),
    fixture('aircraft', 'aircraft', 60),
    fixture('aviation', 'aviation_hazard', 65),
    fixture('declaration', 'disaster_declaration', 15),
    fixture('marine', 'marine_observation', 55),
    fixture('water', 'water_level', 45),
    fixture('fireball', 'fireball', 55),
    fixture('signal', 'space_weather', 50),
    fixture('report', 'humanitarian_report', 40),
    fixture('unknown', 'unknown', 35),
  ];
  assert.deepEqual(filterIncidents(incidents, 'natural').map(item => item.incident_id), [
    'quake-high', 'quake-low', 'declaration',
  ]);
  assert.deepEqual(filterIncidents(incidents, 'weather').map(item => item.incident_id), ['weather']);
  assert.deepEqual(filterIncidents(incidents, 'mobility').map(item => item.incident_id), [
    'aviation', 'aircraft', 'marine', 'water',
  ]);
  assert.deepEqual(filterIncidents(incidents, 'signals').map(item => item.incident_id), [
    'fireball', 'signal', 'unknown',
  ]);
  assert.deepEqual(filterIncidents(incidents, 'conflict').map(item => item.incident_id), ['report']);
  assert.equal(filterIncidents(null).length, 0);
  assert.equal(filterIncidents([null], 'natural').length, 0);
  assert.deepEqual(filterIncidents([
    { incident_id: 'b', priority_score: 'invalid', last_changed_at: '2026-01-01T00:00:00Z' },
    { incident_id: 'a', priority_score: 0, last_changed_at: '2026-01-01T00:00:00Z' },
    { incident_id: 'newer', priority_score: 0, last_changed_at: '2026-01-02T00:00:00Z' },
  ]).map(item => item.incident_id), ['newer', 'a', 'b']);
  assert.equal(kindPresentation('tsunami').shape, 'triangle');
  assert.equal(kindPresentation('not-known').label, 'Other signal');
  assert.equal(finiteNumber('bad', 7), 7);
  assert.equal(finiteNumber('5'), 5);
});

test('every versioned taxonomy kind has text, shape, and a non-global filter path', () => {
  const taxonomy = JSON.parse(readFileSync(
    new URL('../../config/data_taxonomy.v1.json', import.meta.url), 'utf8',
  ));
  for (const { id } of taxonomy.categories) {
    assert.notEqual(kindPresentation(id).label, 'Other signal', id);
    const item = fixture(id, id, 50);
    assert.ok(
      ['natural', 'weather', 'conflict', 'mobility', 'signals']
        .some(filter => filterIncidents([item], filter).length === 1),
      `${id} has no category filter`,
    );
  }
});

test('overview formatting exposes age, location, and score evidence', () => {
  const now = Date.parse('2026-07-10T22:00:00Z');
  assert.equal(formatIncidentAge('2026-07-10T21:59:45Z', now), '15s ago');
  assert.equal(formatIncidentAge('2026-07-10T21:30:00Z', now), '30m ago');
  assert.equal(formatIncidentAge('2026-07-10T19:00:00Z', now), '3h ago');
  assert.equal(formatIncidentAge('2026-07-08T19:00:00Z', now), '2d ago');
  assert.equal(formatIncidentAge('bad', now), 'age unknown');
  assert.equal(formatIncidentAge('2026-07-10T23:00:00Z', now), '0s ago');
  assert.equal(formatIncidentAge('2099-01-01T00:00:00Z'), '0s ago');
  assert.equal(formatLocation(fixture('quake', 'earthquake', 80)), '35.6°N, 139.7°E');
  assert.equal(formatLocation({ centroid: [-70.1, -20.2] }), '20.2°S, 70.1°W');
  assert.equal(formatLocation({}), 'Location not reported');
  assert.equal(formatLocation({ centroid: [1] }), 'Location not reported');
  assert.equal(formatLocation({ centroid: ['bad', 1] }), 'Location not reported');
  assert.equal(formatLocation({ centroid: [1, 'bad'] }), 'Location not reported');
  assert.equal(
    priorityExplanation(fixture('quake', 'earthquake', 80)),
    'impact +30 · urgency +20 · freshness +15 · corroboration +5 · status/age -20',
  );
  assert.equal(priorityExplanation({}), 'No scoring components reported');
  assert.equal(priorityExplanation({
    priority_components: { watch_region: 10, impact: 0, penalty: 0 },
  }), 'watch relevance +10');
});

test('overview states cover loading, first run, empty, partial, stale, offline, and ready', () => {
  const incidents = [fixture('quake', 'earthquake', 80)];
  const value = (counts, overrides = {}) => ({
    loaded: true, failed: false, incidents, health: { counts }, firstRun: false,
    ...overrides,
  });
  assert.equal(deriveOverviewState({ loaded: false }), 'loading');
  assert.equal(deriveOverviewState({ loaded: true, incidents: [] }), 'empty');
  assert.equal(deriveOverviewState({ loaded: false, failed: true }), 'offline');
  assert.equal(deriveOverviewState(value({ live: 1 }, { firstRun: true })), 'first_run');
  assert.equal(deriveOverviewState(value({ live: 1 }, { incidents: [] })), 'empty');
  assert.equal(deriveOverviewState(value({ live: 1, error: 1 })), 'partial');
  assert.equal(deriveOverviewState(value({ cached: 1, stale: 1 })), 'stale');
  assert.equal(deriveOverviewState(value({ error: 3 })), 'offline');
  assert.equal(deriveOverviewState(value({ live: 3 })), 'ready');
  assert.equal(deriveOverviewState(value({ live: 3, pending: 1 })), 'partial');
  assert.equal(deriveOverviewState(value({ cached: 1, error: 1 })), 'partial');
  assert.equal(deriveOverviewState(value({ live: 3 }, { failed: true })), 'offline');
});

test('change summary is concise, textual, and stable', () => {
  assert.equal(summarizeChanges([]), 'No changes since this view opened.');
  assert.equal(summarizeChanges(null), 'No changes since this view opened.');
  assert.equal(summarizeChanges([
    { change_type: 'new' },
    { change_type: 'source_lost' },
    { change_type: 'new' },
  ]), '2 new · 1 source lost');
  assert.equal(summarizeChanges([{}]), '1 updated');
});
