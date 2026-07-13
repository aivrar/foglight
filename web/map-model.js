const KIND_COLORS = Object.freeze({
  earthquake: '#ff715f', weather_alert: '#efb64f', tropical_cyclone: '#64b5f6',
  tsunami: '#55c4d4', volcano: '#ec8050', wildfire: '#ef9a55',
  natural_event: '#8dbb72', disaster: '#e06d75', conflict_report: '#dc6b91',
  disaster_declaration: '#b99066',
  humanitarian_report: '#d4a85c', news_item: '#7ca6ce', aircraft: '#9d8ee8',
  aviation_hazard: '#7db4e8',
  marine_observation: '#63c3ba', water_level: '#4ca9cf',
  fireball: '#d5a2ff',
  space_weather: '#75c9c0', orbital_position: '#83d3ee',
  market_snapshot: '#c69a65', technology_activity: '#8496b1', unknown: '#8290a3',
});

export function normalizeLongitude(value) {
  if (!['number', 'string'].includes(typeof value) || String(value).trim() === '') return null;
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  const normalized = ((number + 180) % 360 + 360) % 360 - 180;
  return normalized === -180 && number > 0 ? 180 : normalized;
}

export function validPoint(value) {
  if (!Array.isArray(value) || value.length < 2) return null;
  if (!['number', 'string'].includes(typeof value[0]) || String(value[0]).trim() === '') return null;
  const longitude = Number(value[0]);
  if (!['number', 'string'].includes(typeof value[1]) || String(value[1]).trim() === '') return null;
  const latitude = Number(value[1]);
  if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180
      || !Number.isFinite(latitude) || latitude < -90 || latitude > 90) {
    return null;
  }
  return [longitude, latitude];
}

export function pointInBounds(point, bounds) {
  const normalized = validPoint(point);
  if (!normalized || !bounds) return false;
  const [longitude, latitude] = normalized;
  const west = Number(bounds.west);
  const east = Number(bounds.east);
  const south = Math.max(-90, Number(bounds.south));
  const north = Math.min(90, Number(bounds.north));
  if (![west, east, south, north].every(Number.isFinite) || south > north) return false;
  const inLongitude = west <= east
    ? longitude >= west && longitude <= east
    : longitude >= west || longitude <= east;
  return inLongitude && latitude >= south && latitude <= north;
}

export function incidentPoint(incident) {
  const centroid = validPoint(incident?.centroid);
  if (centroid) return centroid;
  if (incident?.geometry?.type === 'Point') return validPoint(incident.geometry.coordinates);
  return null;
}

function circularLongitude(points) {
  let sine = 0;
  let cosine = 0;
  for (const [longitude] of points) {
    const radians = longitude * Math.PI / 180;
    sine += Math.sin(radians);
    cosine += Math.cos(radians);
  }
  return normalizeLongitude(Math.atan2(sine, cosine) * 180 / Math.PI) ?? points[0][0];
}

function priorityScore(incident) {
  const score = Number(incident?.priority_score);
  return Number.isFinite(score) ? score : 0;
}

export function clusterIncidents(
  incidents,
  { bounds = { west: -180, south: -90, east: 180, north: 90 }, zoom = 2, cellPixels = 52 } = {},
) {
  const safeZoom = Math.max(0, Math.min(20, Number(zoom) || 0));
  const safeCell = Math.max(16, Math.min(256, Number(cellPixels) || 52));
  const longitudeCell = 360 / (256 * (2 ** safeZoom)) * safeCell;
  const latitudeCell = 170 / (256 * (2 ** safeZoom)) * safeCell;
  const groups = new Map();
  for (const incident of Array.isArray(incidents) ? incidents : []) {
    const point = incidentPoint(incident);
    if (!point || !pointInBounds(point, bounds)) continue;
    const shiftedLongitude = ((point[0] + 180 + longitudeCell / 2) % 360 + 360) % 360;
    const x = Math.floor(shiftedLongitude / longitudeCell);
    const y = Math.floor((point[1] + 90) / latitudeCell);
    const key = `${x}:${y}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push({ incident, point });
  }
  return [...groups.entries()].map(([key, members]) => {
    members.sort((left, right) => (
      priorityScore(right.incident) - priorityScore(left.incident)
      || String(left.incident?.incident_id || '').localeCompare(String(right.incident?.incident_id || ''))
    ));
    const points = members.map(item => item.point);
    return {
      key,
      longitude: circularLongitude(points),
      latitude: points.reduce((sum, item) => sum + item[1], 0) / points.length,
      count: members.length,
      maxPriority: Math.max(...members.map(item => priorityScore(item.incident))),
      incidents: members.map(item => item.incident),
    };
  }).sort((left, right) => left.key.localeCompare(right.key));
}

function flattenCoordinates(value, output) {
  const point = validPoint(value);
  if (point) {
    output.push(point);
    return;
  }
  if (!Array.isArray(value)) throw new TypeError('geometry coordinates must be arrays');
  for (const item of value) flattenCoordinates(item, output);
}

function countGeometryPoints(geometry) {
  if (geometry?.type === 'GeometryCollection') {
    return geometry.geometries.reduce((sum, item) => sum + countGeometryPoints(item), 0);
  }
  const points = [];
  flattenCoordinates(geometry?.coordinates, points);
  return points.length;
}

function decimateLine(coordinates, stride) {
  const line = coordinates.map(validPoint).filter(Boolean);
  if (line.length < 2) return [];
  return line.filter((_item, index) => (
    index === 0 || index === line.length - 1 || index % stride === 0
  ));
}

function decimateRing(coordinates, stride) {
  const ring = coordinates.map(validPoint).filter(Boolean);
  if (ring.length < 3) return [];
  const first = ring[0];
  const last = ring[ring.length - 1];
  const isClosed = first[0] === last[0] && first[1] === last[1];
  const open = isClosed ? ring.slice(0, -1) : ring;
  if (open.length < 3) return [];
  const output = open.filter((_item, index) => (
    index === 0 || index === open.length - 1 || index % stride === 0
  ));
  if (output.length < 3) return [open[0], open[Math.floor(open.length / 2)], open.at(-1), open[0]];
  output.push(output[0]);
  return output;
}

function sanitizeWithStride(geometry, stride) {
  if (geometry.type === 'LineString') {
    const coordinates = decimateLine(geometry.coordinates, stride);
    return coordinates.length ? { type: geometry.type, coordinates } : null;
  }
  if (geometry.type === 'MultiLineString') {
    const coordinates = Array.isArray(geometry.coordinates)
      ? geometry.coordinates.map(line => (
        Array.isArray(line) ? decimateLine(line, stride) : []
      )).filter(line => line.length)
      : [];
    return coordinates.length ? { type: geometry.type, coordinates } : null;
  }
  const sanitizePolygon = polygon => {
    if (!Array.isArray(polygon) || !Array.isArray(polygon[0])) return [];
    const outer = decimateRing(polygon[0], stride);
    if (!outer.length) return [];
    const holes = polygon.slice(1).map(ring => (
      Array.isArray(ring) ? decimateRing(ring, stride) : []
    )).filter(ring => ring.length);
    return [outer, ...holes];
  };
  if (geometry.type === 'Polygon') {
    const coordinates = sanitizePolygon(geometry.coordinates);
    return coordinates.length ? { type: geometry.type, coordinates } : null;
  }
  const coordinates = Array.isArray(geometry.coordinates)
    ? geometry.coordinates.map(sanitizePolygon).filter(polygon => polygon.length)
    : [];
  return coordinates.length ? { type: geometry.type, coordinates } : null;
}

export function sanitizeGeometry(geometry, { maxPoints = 2000, maxDepth = 8 } = {}) {
  const supported = [
    'Point', 'MultiPoint', 'LineString', 'MultiLineString',
    'Polygon', 'MultiPolygon', 'GeometryCollection',
  ];
  if (!geometry || !supported.includes(geometry.type)) {
    return null;
  }
  const limit = Math.max(4, Math.min(20_000, Number(maxPoints) || 2000));
  const depth = Math.max(0, Math.min(32, Number(maxDepth) || 0));
  if (geometry.type === 'Point') {
    const point = validPoint(geometry.coordinates);
    return point ? { type: 'Point', coordinates: point } : null;
  }
  if (geometry.type === 'MultiPoint') {
    if (!Array.isArray(geometry.coordinates)) return null;
    const coordinates = geometry.coordinates.map(validPoint).filter(Boolean).slice(0, limit);
    return coordinates.length ? { type: geometry.type, coordinates } : null;
  }
  if (geometry.type === 'GeometryCollection') {
    if (depth === 0) return null;
    if (!Array.isArray(geometry.geometries) || !geometry.geometries.length
        || geometry.geometries.length > 100) return null;
    const share = Math.max(4, Math.floor(limit / geometry.geometries.length));
    const geometries = geometry.geometries.map(item => (
      sanitizeGeometry(item, { maxPoints: share, maxDepth: depth - 1 })
    )).filter(Boolean);
    if (!geometries.length) return null;
    const retained = geometries.reduce((sum, item) => sum + countGeometryPoints(item), 0);
    return retained <= limit ? { type: geometry.type, geometries } : null;
  }
  const points = [];
  try {
    flattenCoordinates(geometry.coordinates, points);
  } catch {
    return null;
  }
  if (!points.length) return null;
  let stride = Math.max(1, Math.ceil(points.length / limit));
  try {
    let sanitized = sanitizeWithStride(geometry, stride);
    while (sanitized) {
      const retained = [];
      flattenCoordinates(sanitized.coordinates, retained);
      if (retained.length <= limit) return sanitized;
      if (stride >= points.length) return null;
      stride = Math.min(points.length, stride * 2);
      sanitized = sanitizeWithStride(geometry, stride);
    }
    return null;
  } catch {
    return null;
  }
}

function unwrapLine(coordinates) {
  if (!coordinates.length) return [];
  const output = [[...coordinates[0]]];
  for (const point of coordinates.slice(1)) {
    let longitude = point[0];
    const previous = output.at(-1)[0];
    while (longitude - previous > 180) longitude -= 360;
    while (longitude - previous < -180) longitude += 360;
    output.push([longitude, point[1]]);
  }
  return output;
}

export function unwrapGeometry(geometry) {
  if (!geometry) return null;
  if (geometry.type === 'Point' || geometry.type === 'MultiPoint') return geometry;
  if (geometry.type === 'LineString') {
    return { ...geometry, coordinates: unwrapLine(geometry.coordinates) };
  }
  if (geometry.type === 'MultiLineString' || geometry.type === 'Polygon') {
    return { ...geometry, coordinates: geometry.coordinates.map(unwrapLine) };
  }
  if (geometry.type === 'MultiPolygon') {
    return {
      ...geometry,
      coordinates: geometry.coordinates.map(polygon => polygon.map(unwrapLine)),
    };
  }
  if (geometry.type === 'GeometryCollection') {
    return { ...geometry, geometries: geometry.geometries.map(unwrapGeometry).filter(Boolean) };
  }
  return null;
}

export function incidentMapStyle(incident, now = Date.now()) {
  const changed = Date.parse(incident?.last_changed_at || '');
  const ageHours = Number.isFinite(changed) ? Math.max(0, (now - changed) / 3_600_000) : 168;
  const certainty = String(incident?.certainty || 'Unknown');
  const uncertain = !['Observed', 'Likely'].includes(certainty);
  const opacity = ageHours <= 6 ? 0.95 : ageHours <= 24 ? 0.75 : ageHours <= 168 ? 0.5 : 0.3;
  return {
    color: KIND_COLORS[incident?.kind] || KIND_COLORS.unknown,
    opacity,
    fillOpacity: uncertain ? opacity * 0.12 : opacity * 0.28,
    dashArray: uncertain ? '6 5' : null,
    uncertain,
    pulse: ['new', 'escalated'].includes(incident?.change_type),
  };
}

export function clusterRadius(cluster) {
  const count = Number(cluster?.count);
  const safeCount = Number.isFinite(count) && count > 0 ? count : 1;
  return Math.max(7, Math.min(24, 6 + Math.sqrt(safeCount) * 3));
}
