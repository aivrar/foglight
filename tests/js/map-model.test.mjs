import assert from 'node:assert/strict';
import test from 'node:test';

import {
  clusterIncidents,
  clusterRadius,
  incidentMapStyle,
  incidentPoint,
  normalizeLongitude,
  pointInBounds,
  sanitizeGeometry,
  unwrapGeometry,
  validPoint,
} from '../../web/map-model.js';

const incident = (id, longitude, latitude, score = 50, extra = {}) => ({
  incident_id: id,
  centroid: [longitude, latitude],
  priority_score: score,
  ...extra,
});

test('coordinates normalize safely at the antimeridian and poles', () => {
  assert.equal(normalizeLongitude('bad'), null);
  assert.equal(normalizeLongitude(Infinity), null);
  assert.equal(normalizeLongitude(null), null);
  assert.equal(normalizeLongitude(''), null);
  assert.equal(normalizeLongitude(false), null);
  assert.equal(normalizeLongitude([180]), null);
  assert.equal(normalizeLongitude({ value: 180 }), null);
  assert.equal(normalizeLongitude(540), 180);
  assert.equal(normalizeLongitude(-540), -180);
  assert.equal(normalizeLongitude(181), -179);
  assert.deepEqual(validPoint([180, 90]), [180, 90]);
  assert.deepEqual(validPoint([-180, -90]), [-180, -90]);
  assert.equal(validPoint([541, 20]), null);
  assert.equal(validPoint(null), null);
  assert.equal(validPoint([0]), null);
  assert.equal(validPoint([0, 91]), null);
  assert.equal(validPoint([0, Number.NaN]), null);
  assert.equal(validPoint([null, null]), null);
  assert.equal(validPoint([0, '']), null);
  assert.equal(validPoint([[0], [1]]), null);
  assert.deepEqual(incidentPoint({ geometry: { type: 'Point', coordinates: [2, 3] } }), [2, 3]);
  assert.equal(incidentPoint({ geometry: { type: 'Polygon', coordinates: [] } }), null);
});

test('bounds handle ordinary, crossing-antimeridian, and invalid viewports', () => {
  assert.equal(pointInBounds([10, 10], { west: 0, east: 20, south: 0, north: 20 }), true);
  assert.equal(pointInBounds([-10, 10], { west: 0, east: 20, south: 0, north: 20 }), false);
  const crossing = { west: 170, east: -170, south: -20, north: 20 };
  assert.equal(pointInBounds([179, 0], crossing), true);
  assert.equal(pointInBounds([-179, 0], crossing), true);
  assert.equal(pointInBounds([0, 0], crossing), false);
  assert.equal(pointInBounds([0, 0], { west: 0, east: 1, south: 20, north: 10 }), false);
  assert.equal(pointInBounds([0, 0], { west: 'bad', east: 1, south: -1, north: 1 }), false);
  assert.equal(pointInBounds([0, 0], null), false);
  assert.equal(pointInBounds(null, { west: -1, east: 1, south: -1, north: 1 }), false);
  assert.equal(pointInBounds([0, 90], { west: -1, east: 1, south: -100, north: 100 }), true);
});

test('grid clustering is deterministic, priority ordered, and viewport scoped', () => {
  const input = [
    incident('lower', 10, 10, 20),
    incident('higher', 10.01, 10.01, 80),
    incident('invalid-score', 10.02, 10.02, 'bad'),
    incident('outside', 100, 50, 100),
    incident('invalid', 0, 99, 100),
  ];
  const options = {
    zoom: 2,
    cellPixels: 100,
    bounds: { west: 0, east: 20, south: 0, north: 20 },
  };
  const clusters = clusterIncidents(input, options);
  assert.equal(clusters.length, 1);
  assert.deepEqual(clusters[0].incidents.map(item => item.incident_id), [
    'higher', 'lower', 'invalid-score',
  ]);
  assert.equal(clusters[0].count, 3);
  assert.equal(clusters[0].maxPriority, 80);
  assert.ok(clusters[0].longitude > 10 && clusters[0].longitude < 10.02);
  assert.ok(clusters[0].latitude > 10 && clusters[0].latitude < 10.02);
  assert.deepEqual(clusterIncidents([...input].reverse(), options), clusters);
  assert.deepEqual(clusterIncidents(null), []);
  const seam = clusterIncidents([
    incident('east', 179, 1, 1), incident('west', -179, 1, 2),
  ], { bounds: { west: 170, east: -170, south: -10, north: 10 }, zoom: 'bad', cellPixels: 1 });
  assert.equal(seam.length, 1);
  assert.equal(seam[0].count, 2);
  assert.equal(seam.reduce((sum, cluster) => sum + cluster.count, 0), 2);
  assert.deepEqual(clusterIncidents([
    { incident_id: 'point-fallback', geometry: { type: 'Point', coordinates: [4, 5] } },
  ])[0].incidents[0].incident_id, 'point-fallback');
});

test('5,000 overlapping and mixed points stay bounded and selectable', () => {
  const input = Array.from({ length: 5000 }, (_value, index) => (
    index % 20 === 0
      ? { incident_id: `invalid-${index}`, centroid: [0, 100] }
      : incident(`item-${String(index).padStart(4, '0')}`, (index % 100) / 100, (index % 50) / 100, index % 101)
  ));
  const started = performance.now();
  const clusters = clusterIncidents(input, { zoom: 5, cellPixels: 52 });
  assert.ok(performance.now() - started < 1000);
  assert.ok(clusters.length > 0 && clusters.length < input.length);
  assert.equal(clusters.reduce((sum, cluster) => sum + cluster.count, 0), 4750);
  assert.ok(clusters.every(cluster => cluster.incidents.length === cluster.count));
});

test('geometry sanitizer preserves lines and closes polygon rings', () => {
  assert.equal(sanitizeGeometry({ type: 'Point', coordinates: [181, 90] }), null);
  assert.deepEqual(sanitizeGeometry({
    type: 'LineString', coordinates: [[0, 0], [1, 1], [2, 2]],
  }), {
    type: 'LineString', coordinates: [[0, 0], [1, 1], [2, 2]],
  });
  const polygon = sanitizeGeometry({
    type: 'Polygon',
    coordinates: [[[0, 0], [2, 0], [2, 2], [0, 2]]],
  });
  assert.deepEqual(polygon.coordinates[0][0], polygon.coordinates[0].at(-1));
  assert.equal(polygon.coordinates[0].length, 5);
  const multi = sanitizeGeometry({
    type: 'MultiPolygon',
    coordinates: [
      [[[-1, -1], [0, -1], [0, 0], [-1, -1]]],
      [[[1, 1], [2, 1], [2, 2], [1, 1]]],
    ],
  });
  assert.equal(multi.coordinates.length, 2);
  assert.deepEqual(sanitizeGeometry({
    type: 'MultiPoint', coordinates: [[0, 0], [1, 1], [2, 99]],
  }), { type: 'MultiPoint', coordinates: [[0, 0], [1, 1]] });
  assert.deepEqual(sanitizeGeometry({
    type: 'MultiLineString', coordinates: [[[0, 0], [1, 1]], [[2, 2], [3, 3]]],
  }), {
    type: 'MultiLineString', coordinates: [[[0, 0], [1, 1]], [[2, 2], [3, 3]]],
  });
  const collection = sanitizeGeometry({
    type: 'GeometryCollection',
    geometries: [
      { type: 'Point', coordinates: [0, 0] },
      { type: 'LineString', coordinates: [[0, 0], [1, 1]] },
    ],
  });
  assert.equal(collection.geometries.length, 2);
  const nestedCollection = sanitizeGeometry({
    type: 'GeometryCollection',
    geometries: [{
      type: 'GeometryCollection',
      geometries: [{ type: 'Point', coordinates: [2, 3] }],
    }],
  });
  assert.equal(nestedCollection.geometries[0].geometries[0].type, 'Point');
  const withHole = sanitizeGeometry({
    type: 'Polygon',
    coordinates: [
      [[0, 0], [3, 0], [3, 3], [0, 0]],
      [[1, 1], [2, 1], [2, 2], [1, 1]],
    ],
  });
  assert.equal(withHole.coordinates.length, 2);
});

test('invalid and excessive geometries fail closed without unbounded output', () => {
  assert.equal(sanitizeGeometry(null), null);
  assert.equal(sanitizeGeometry({ type: 'GeometryCollection', coordinates: [] }), null);
  assert.equal(sanitizeGeometry({ type: 'Point', coordinates: [0, 200] }), null);
  assert.equal(sanitizeGeometry({ type: 'LineString', coordinates: [[0, 0]] }), null);
  assert.equal(sanitizeGeometry({ type: 'LineString', coordinates: [[0, 0], ['bad', 1]] }), null);
  assert.equal(sanitizeGeometry({ type: 'Polygon', coordinates: [[[0, 0], [1, 1]]] }), null);
  assert.equal(sanitizeGeometry({ type: 'MultiPolygon', coordinates: 'bad' }), null);
  assert.equal(sanitizeGeometry({ type: 'MultiPoint', coordinates: 'bad' }), null);
  assert.equal(sanitizeGeometry({ type: 'MultiPoint', coordinates: [] }), null);
  assert.equal(sanitizeGeometry({ type: 'GeometryCollection', geometries: [] }), null);
  assert.equal(sanitizeGeometry({
    type: 'GeometryCollection',
    geometries: [{
      type: 'GeometryCollection',
      geometries: [{ type: 'Point', coordinates: [0, 0] }],
    }],
  }, { maxDepth: 1 }), null);
  assert.equal(sanitizeGeometry({ type: 'GeometryCollection', geometries: 'bad' }), null);
  assert.equal(sanitizeGeometry({
    type: 'GeometryCollection', geometries: Array.from({ length: 101 }, () => ({ type: 'Point', coordinates: [0, 0] })),
  }), null);
  assert.equal(sanitizeGeometry({
    type: 'GeometryCollection', geometries: [{ type: 'Unsupported' }],
  }), null);
  assert.equal(sanitizeGeometry({ type: 'MultiLineString', coordinates: 'bad' }), null);
  assert.deepEqual(sanitizeGeometry({
    type: 'MultiLineString', coordinates: [[], [[0, 0], [1, 1]]],
  }), { type: 'MultiLineString', coordinates: [[[0, 0], [1, 1]]] });
  assert.equal(sanitizeGeometry({
    type: 'Polygon', coordinates: ['bad', [[0, 0], [1, 0], [0, 1], [0, 0]]],
  }), null);
  assert.equal(sanitizeGeometry({
    type: 'GeometryCollection',
    geometries: [
      { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [0, 1], [0, 0]]] },
      { type: 'Polygon', coordinates: [[[2, 2], [3, 2], [2, 3], [2, 2]]] },
    ],
  }, { maxPoints: 4 }), null);
  const throwing = { type: 'LineString' };
  let reads = 0;
  Object.defineProperty(throwing, 'coordinates', {
    get() {
      reads += 1;
      if (reads > 1) throw new Error('changed during sanitization');
      return [[0, 0], [1, 1]];
    },
  });
  assert.equal(sanitizeGeometry(throwing), null);

  const points = Array.from({ length: 10_001 }, (_value, index) => [
    -170 + (index % 3400) / 10,
    -80 + (index % 1600) / 10,
  ]);
  points.push(points[0]);
  const huge = sanitizeGeometry({ type: 'Polygon', coordinates: [points] }, { maxPoints: 100 });
  const retained = huge.coordinates[0].length;
  assert.ok(retained >= 4 && retained <= 100);
});

test('antimeridian geometry unwraps to the short path after validation', () => {
  const polygon = sanitizeGeometry({
    type: 'Polygon',
    coordinates: [[[179, -10], [-179, -10], [-179, 10], [179, 10], [179, -10]]],
  });
  const unwrapped = unwrapGeometry(polygon);
  assert.deepEqual(unwrapped.coordinates[0], [
    [179, -10], [181, -10], [181, 10], [179, 10], [179, -10],
  ]);
  const line = unwrapGeometry(sanitizeGeometry({
    type: 'LineString', coordinates: [[-179, 0], [179, 0]],
  }));
  assert.deepEqual(line.coordinates, [[-179, 0], [-181, 0]]);
  const multiPoint = { type: 'MultiPoint', coordinates: [[0, 0], [1, 1]] };
  assert.equal(unwrapGeometry(multiPoint), multiPoint);
  assert.deepEqual(unwrapGeometry({
    type: 'MultiLineString', coordinates: [[[179, 0], [-179, 0]]],
  }).coordinates, [[[179, 0], [181, 0]]]);
  assert.deepEqual(unwrapGeometry({
    type: 'MultiPolygon',
    coordinates: [[[[179, 0], [-179, 0], [-179, 1], [179, 0]]]],
  }).coordinates[0][0][1], [181, 0]);
  assert.equal(unwrapGeometry({
    type: 'GeometryCollection', geometries: [{ type: 'Point', coordinates: [0, 0] }, null],
  }).geometries.length, 1);
  assert.equal(unwrapGeometry(null), null);
  assert.equal(unwrapGeometry({ type: 'Unsupported' }), null);
});

test('styles distinguish freshness, uncertainty, change, and category', () => {
  const now = Date.parse('2026-07-10T12:00:00Z');
  const fresh = incidentMapStyle({
    kind: 'earthquake', certainty: 'Observed', change_type: 'new',
    last_changed_at: '2026-07-10T11:00:00Z',
  }, now);
  assert.equal(fresh.color, '#ff715f');
  assert.equal(fresh.opacity, 0.95);
  assert.equal(fresh.dashArray, null);
  assert.equal(fresh.uncertain, false);
  assert.equal(fresh.pulse, true);
  const uncertain = incidentMapStyle({
    kind: 'not-known', certainty: 'Possible', change_type: 'updated',
    last_changed_at: '2026-07-08T12:00:00Z',
  }, now);
  assert.equal(uncertain.color, '#8290a3');
  assert.equal(uncertain.opacity, 0.5);
  assert.equal(uncertain.dashArray, '6 5');
  assert.equal(uncertain.pulse, false);
  assert.equal(incidentMapStyle({ last_changed_at: '2026-07-10T00:00:00Z' }, now).opacity, 0.75);
  assert.equal(incidentMapStyle({ last_changed_at: '2026-07-11T00:00:00Z' }, now).opacity, 0.95);
  assert.equal(incidentMapStyle({}, now).opacity, 0.5);
  assert.equal(incidentMapStyle({ last_changed_at: '2025-01-01T00:00:00Z' }, now).opacity, 0.3);
});

test('cluster radii have stable minimum, growth, and cap', () => {
  assert.equal(clusterRadius(null), 9);
  assert.equal(clusterRadius({ count: 4 }), 12);
  assert.equal(clusterRadius({ count: 10_000 }), 24);
  assert.equal(clusterRadius({ count: 'bad' }), 9);
});
