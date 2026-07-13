import {
  clusterIncidents,
  clusterRadius,
  incidentMapStyle,
  incidentPoint,
  normalizeLongitude,
  sanitizeGeometry,
  unwrapGeometry,
} from './map-model.js';

const WORLD_ASSET = '/assets/natural-earth-110m-countries.v5.1.1.geojson';
const NATURAL_EARTH_ATTRIBUTION = '<a href="https://www.naturalearthdata.com/">Natural Earth</a> (public domain)';

function leaflet() {
  if (!window.L) throw new Error('The bundled Leaflet runtime did not load.');
  return window.L;
}

function status(statusNode, message, state = 'ready') {
  if (!statusNode) return;
  statusNode.textContent = message;
  statusNode.dataset.state = state;
}

function tooltipNode(text) {
  const node = document.createElement('span');
  node.textContent = text;
  return node;
}

function drawGraticule(map) {
  const L = leaflet();
  const style = { color: '#294158', weight: 0.55, opacity: 0.42, interactive: false };
  const group = L.layerGroup().addTo(map);
  for (let latitude = -60; latitude <= 60; latitude += 30) {
    const points = [];
    for (let longitude = -180; longitude <= 180; longitude += 5) points.push([latitude, longitude]);
    L.polyline(points, style).addTo(group);
  }
  for (let longitude = -150; longitude <= 180; longitude += 30) {
    L.polyline([[-85, longitude], [85, longitude]], style).addTo(group);
  }
  return group;
}

export async function addBundledWorldBase(
  map,
  { fetchImpl = window.fetch.bind(window), statusNode = null } = {},
) {
  const L = leaflet();
  map.getContainer().classList.add('foglight-offline-map');
  drawGraticule(map);
  try {
    const response = await fetchImpl(WORLD_ASSET, { cache: 'force-cache' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const geojson = await response.json();
    if (geojson?.type !== 'FeatureCollection' || !Array.isArray(geojson.features)) {
      throw new TypeError('invalid bundled boundary data');
    }
    const layer = L.geoJSON(geojson, {
      renderer: L.canvas({ padding: 0.5 }),
      interactive: false,
      style: {
        color: '#49657c', fillColor: '#111f2c', fillOpacity: 0.72,
        opacity: 0.82, weight: 0.65,
      },
    }).addTo(map);
    layer.bringToBack();
    map.attributionControl?.addAttribution(NATURAL_EARTH_ATTRIBUTION);
    status(statusNode, 'Offline world base ready.');
    return layer;
  } catch (error) {
    status(statusNode, 'Boundaries unavailable; coordinate grid remains usable.', 'degraded');
    console.warn('[foglight] bundled world base unavailable', error);
    return null;
  }
}

function viewportBounds(map) {
  const bounds = map.getBounds();
  const westRaw = bounds.getWest();
  const eastRaw = bounds.getEast();
  const span = eastRaw - westRaw;
  return {
    west: span >= 359 ? -180 : normalizeLongitude(westRaw),
    east: span >= 359 ? 180 : normalizeLongitude(eastRaw),
    south: Math.max(-90, bounds.getSouth()),
    north: Math.min(90, bounds.getNorth()),
  };
}

function pointLabel(incident) {
  const headline = String(incident?.headline || 'Untitled incident');
  const score = Number(incident?.priority_score);
  return Number.isFinite(score) ? `${headline} — priority ${Math.round(score)}` : headline;
}

export function createIncidentMapController({
  containerId = 'overview-map',
  statusId = 'overview-map-status',
  countId = 'overview-map-count',
  tileToggleId = 'overview-detail-tiles',
  pinFormId = 'overview-pin-form',
  annotations = () => [],
  onAddPin = async () => {},
  onSelect = () => {},
  onPickCoordinates = () => {},
} = {}) {
  let map = null;
  let incidentLayer = null;
  let geometryLayer = null;
  let pulseLayer = null;
  let pinLayer = null;
  let optionalTiles = null;
  let incidents = [];
  let selectedId = null;
  let tileErrors = 0;
  let savingPin = false;
  let coordinatePickActive = false;
  let baseState = 'loading';
  let canvasRenderer = null;
  const canvas = () => {
    if (!canvasRenderer) canvasRenderer = leaflet().canvas({ padding: 0.45, tolerance: 7 });
    return canvasRenderer;
  };
  const statusNode = () => document.getElementById(statusId);

  function renderPins() {
    if (!pinLayer) return;
    const L = leaflet();
    pinLayer.clearLayers();
    for (const pin of annotations()) {
      const latitude = Number(pin?.lat);
      const longitude = normalizeLongitude(pin?.lon);
      if (!Number.isFinite(latitude) || latitude < -85 || latitude > 85 || longitude === null) continue;
      const label = String(pin?.label || 'Pinned');
      L.marker([latitude, longitude], {
        keyboard: true,
        title: label,
        zIndexOffset: 1000,
        icon: L.divIcon({
          className: 'overview-pin-marker', html: '', iconSize: [16, 16], iconAnchor: [8, 8],
        }),
      }).bindTooltip(tooltipNode(label)).addTo(pinLayer);
    }
  }

  function select(id, { announce = false } = {}) {
    const previousId = selectedId;
    selectedId = id == null ? null : String(id);
    const selected = incidents.find(item => String(item?.incident_id) === selectedId);
    const point = incidentPoint(selected);
    if (announce && point && !map.getBounds().contains([point[1], point[0]])) {
      map.setView([point[1], point[0]], Math.max(2, map.getZoom()), { animate: false });
    }
    if (selectedId !== previousId || announce) renderIncidents();
    if (!selectedId && previousId && !announce) {
      status(statusNode(), 'Selection cleared; offline map remains available.');
    }
    if (announce && selectedId) {
      status(statusNode(), selected ? `Selected ${pointLabel(selected)}.` : 'Selection is no longer visible.');
    }
  }

  function drawGeometry(incident, style) {
    const geometry = unwrapGeometry(sanitizeGeometry(incident?.geometry, { maxPoints: 2000 }));
    if (!geometry || ['Point', 'MultiPoint'].includes(geometry.type)) return;
    const L = leaflet();
    const layer = L.geoJSON({ type: 'Feature', properties: {}, geometry }, {
      renderer: canvas(),
      pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
        renderer: canvas(), radius: 3, color: style.color,
        fillColor: style.color, fillOpacity: style.fillOpacity, opacity: style.opacity,
        weight: 1,
      }),
      style: {
        color: style.color,
        dashArray: style.dashArray,
        fillColor: style.color,
        fillOpacity: style.fillOpacity,
        opacity: style.opacity,
        weight: String(incident?.incident_id) === selectedId ? 3 : 1.4,
      },
    });
    layer.on('click', () => onSelect(String(incident.incident_id), { source: 'map' }));
    layer.addTo(geometryLayer);
  }

  function renderIncidents() {
    if (!map || !incidentLayer) return;
    const started = performance.now();
    const L = leaflet();
    incidentLayer.clearLayers();
    geometryLayer.clearLayers();
    pulseLayer.clearLayers();
    const zoom = map.getZoom();
    if (zoom >= 4) {
      for (const incident of incidents.slice(0, 150)) drawGeometry(incident, incidentMapStyle(incident));
    }
    const clusters = clusterIncidents(incidents, {
      bounds: viewportBounds(map), zoom, cellPixels: zoom >= 6 ? 42 : 52,
    });
    let pulses = 0;
    for (const cluster of clusters) {
      const top = cluster.incidents[0];
      const style = incidentMapStyle(top);
      const selected = cluster.incidents.some(item => String(item?.incident_id) === selectedId);
      const label = cluster.count === 1 ? pointLabel(top) : `${cluster.count} incidents; highest: ${pointLabel(top)}`;
      const radius = selected ? clusterRadius(cluster) + 3 : clusterRadius(cluster);
      const canZoom = cluster.count > 1 && map.getZoom() < map.getMaxZoom();
      const marker = cluster.count > 1
        ? L.marker([cluster.latitude, cluster.longitude], {
          keyboard: true,
          title: `${label}. Activate to ${canZoom ? 'zoom' : 'select the highest priority incident'}.`,
          icon: L.divIcon({
            className: `incident-cluster-icon${selected ? ' is-selected' : ''}`,
            html: `<span>${cluster.count}</span>`,
            iconSize: [radius * 2, radius * 2],
            iconAnchor: [radius, radius],
          }),
        })
        : L.circleMarker([cluster.latitude, cluster.longitude], {
          renderer: canvas(),
          radius,
          color: selected ? '#ffffff' : style.color,
          fillColor: style.color,
          fillOpacity: Math.max(0.14, Math.min(0.72, style.opacity * 0.55)),
          opacity: style.opacity,
          weight: selected ? 3 : 1.5,
        });
      marker.bindTooltip(tooltipNode(label));
      if (cluster.count > 1) {
        marker.on('add', () => {
          const element = marker.getElement();
          element?.style.setProperty('--cluster-color', style.color);
          if (element) element.style.opacity = String(selected ? 1 : style.opacity);
        });
      }
      const activateMarker = () => {
        if (cluster.count > 1 && map.getZoom() < map.getMaxZoom()) {
          map.setView([cluster.latitude, cluster.longitude], map.getZoom() + 2);
        } else {
          onSelect(String(top.incident_id), { source: 'map' });
        }
      };
      marker.on('click', activateMarker);
      marker.addTo(incidentLayer);
      if (cluster.count > 1) {
        const element = marker.getElement();
        element?.addEventListener('keydown', event => {
          if (!['Enter', ' '].includes(event.key)) return;
          event.preventDefault();
          activateMarker();
        });
      }
      if (cluster.count > 1) {
        marker.bindTooltip(tooltipNode(`${label}. Activate to ${canZoom ? 'zoom' : 'select'}.`));
      }

      if (cluster.count === 1 && style.pulse && zoom >= 3 && pulses < 40) {
        L.marker([cluster.latitude, cluster.longitude], {
          interactive: false,
          icon: L.divIcon({ className: 'incident-map-pulse', html: '', iconSize: [26, 26], iconAnchor: [13, 13] }),
        }).addTo(pulseLayer);
        pulses += 1;
      }
    }
    const countNode = document.getElementById(countId);
    if (countNode) {
      const visibleIncidents = clusters.reduce((sum, cluster) => sum + cluster.count, 0);
      countNode.textContent = `${clusters.length} markers · ${visibleIncidents} incidents`;
    }
    window.__foglightMapMetrics = {
      incidentCount: incidents.length,
      visibleClusters: clusters.length,
      zoom,
      center: [map.getCenter().lng, map.getCenter().lat],
      renderMs: Math.round((performance.now() - started) * 10) / 10,
    };
  }

  function toggleDetailedTiles(enabled) {
    if (!map) return;
    const L = leaflet();
    if (!enabled) {
      if (optionalTiles) map.removeLayer(optionalTiles);
      optionalTiles = null;
      tileErrors = 0;
      status(
        statusNode(),
        baseState === 'ready' ? 'Offline world base ready.'
          : baseState === 'degraded' ? 'Boundaries unavailable; coordinate grid remains usable.'
            : 'Loading bundled world boundaries.',
        baseState,
      );
      return;
    }
    if (optionalTiles) return;
    optionalTiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 8,
      opacity: 0.62,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    });
    optionalTiles.on('tileerror', () => {
      tileErrors += 1;
      if (tileErrors < 3 || !optionalTiles) return;
      map.removeLayer(optionalTiles);
      optionalTiles = null;
      const toggle = document.getElementById(tileToggleId);
      if (toggle) toggle.checked = false;
      status(
        statusNode(),
        baseState === 'degraded'
          ? 'Detailed tiles and boundaries unavailable; coordinate grid remains usable.'
          : 'Detailed tiles unavailable; using the offline base.',
        'degraded',
      );
    });
    optionalTiles.on('load', () => {
      if (optionalTiles) status(statusNode(), 'Detailed tiles enabled over the offline base.');
    });
    optionalTiles.addTo(map);
    status(statusNode(), 'Loading optional detailed tiles.');
  }

  function wirePinForm() {
    const form = document.getElementById(pinFormId);
    if (!form) return;
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (savingPin) return;
      const latitude = Number(document.getElementById('overview-pin-lat')?.value);
      const longitude = Number(document.getElementById('overview-pin-lon')?.value);
      const label = String(document.getElementById('overview-pin-label')?.value || 'Pinned').trim() || 'Pinned';
      if (!Number.isFinite(latitude) || latitude < -85 || latitude > 85
          || !Number.isFinite(longitude) || longitude < -180 || longitude > 180) {
        status(statusNode(), 'Enter a latitude from −85 to 85 and longitude from −180 to 180.', 'error');
        return;
      }
      const currentAnnotations = annotations();
      if (Array.isArray(currentAnnotations) && currentAnnotations.length >= 100) {
        status(statusNode(), 'Foglight stores up to 100 pins. Remove one in Settings first.', 'error');
        return;
      }
      const submit = form.querySelector('button[type="submit"]');
      savingPin = true;
      if (submit) submit.disabled = true;
      try {
        await onAddPin({ lat: latitude, lon: longitude, label });
        renderPins();
        map.setView([latitude, longitude], Math.max(4, map.getZoom()));
        status(statusNode(), `Added pin “${label}”.`);
        form.reset();
        const labelInput = document.getElementById('overview-pin-label');
        if (labelInput) labelInput.value = 'Pinned';
      } catch {
        status(statusNode(), 'The pin could not be saved.', 'error');
      } finally {
        savingPin = false;
        if (submit) submit.disabled = false;
      }
    });
  }

  function start() {
    if (map) return map;
    const L = leaflet();
    map = L.map(containerId, {
      attributionControl: true, maxZoom: 8, minZoom: 0, preferCanvas: true,
      scrollWheelZoom: false, worldCopyJump: true, zoomControl: true,
    }).setView([15, 0], 0);
    incidentLayer = L.layerGroup().addTo(map);
    geometryLayer = L.layerGroup().addTo(map);
    pulseLayer = L.layerGroup().addTo(map);
    pinLayer = L.layerGroup().addTo(map);
    addBundledWorldBase(map, { statusNode: statusNode() }).then(layer => {
      baseState = layer ? 'ready' : 'degraded';
      incidentLayer.bringToFront?.();
      geometryLayer.bringToFront?.();
      renderIncidents();
    });
    map.on('moveend zoomend', renderIncidents);
    map.on('click', event => {
      if (!coordinatePickActive) return;
      cancelCoordinatePick({ announce: false });
      const latitude = Math.max(-85, Math.min(85, Number(event.latlng.lat)));
      const longitude = normalizeLongitude(event.latlng.lng);
      if (!Number.isFinite(latitude) || longitude === null) return;
      onPickCoordinates({ latitude, longitude });
      status(statusNode(), 'Map coordinates loaded into the local watch-region form.');
    });
    map.on('contextmenu', event => {
      cancelCoordinatePick({ announce: false });
      const latitude = Math.max(-85, Math.min(85, Number(event.latlng.lat)));
      const longitude = normalizeLongitude(event.latlng.lng);
      if (!Number.isFinite(latitude) || longitude === null) return;
      onPickCoordinates({ latitude, longitude });
      status(statusNode(), 'Map coordinates loaded into the local watch-region form.');
    });
    document.getElementById(tileToggleId)?.addEventListener('change', event => {
      toggleDetailedTiles(Boolean(event.currentTarget.checked));
    });
    wirePinForm();
    renderPins();
    renderIncidents();
    return map;
  }

  function beginCoordinatePick() {
    if (!map) return false;
    coordinatePickActive = true;
    document.getElementById(containerId)?.classList.add('is-coordinate-picking');
    status(statusNode(), 'Map pick active. Click a location or press Escape to cancel.');
    return true;
  }

  function cancelCoordinatePick({ announce = true } = {}) {
    if (!coordinatePickActive) return false;
    coordinatePickActive = false;
    document.getElementById(containerId)?.classList.remove('is-coordinate-picking');
    if (announce) status(statusNode(), 'Map coordinate pick cancelled.');
    return true;
  }

  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !coordinatePickActive) return;
    cancelCoordinatePick();
  });

  function update(nextIncidents) {
    incidents = Array.isArray(nextIncidents) ? nextIncidents.filter(Boolean) : [];
    renderIncidents();
  }

  function activate() {
    if (!map) return;
    window.setTimeout(() => {
      map.invalidateSize({ animate: false });
      renderIncidents();
    }, 0);
  }

  return Object.freeze({
    start, update, select, activate, renderPins, toggleDetailedTiles,
    beginCoordinatePick, cancelCoordinatePick,
  });
}
