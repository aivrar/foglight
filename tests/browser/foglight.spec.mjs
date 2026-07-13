import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const settings = {
  keys: { nasa_firms: false },
  audio: {
    master: false,
    earthquake: true,
    tornado: true,
    hurricane: true,
    bitcoin_block: true,
  },
  panels: {
    tv: true,
    conflict: true,
    cyclones: true,
    relief: true,
    iss: true,
    btc: false,
    wiki: false,
    github: false,
    sec: false,
    talk: false,
  },
  tv_channel: 'aljazeera',
  watchlist: [],
  annotations: [],
  rss_feeds: ['https://example.test/world.xml'],
  first_run_done: true,
};

const deterministicMapTile = '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256">'
  + '<rect width="256" height="256" fill="#15293a"/></svg>';
const pageErrors = new WeakMap();

async function downloadText(download) {
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
}

function incident(index, overrides = {}) {
  const kinds = [
    'earthquake', 'weather_alert', 'tropical_cyclone', 'tsunami',
    'conflict_report', 'humanitarian_report', 'aircraft', 'space_weather',
    'market_snapshot', 'wildfire', 'volcano', 'news_item',
  ];
  const kind = overrides.kind || kinds[index % kinds.length];
  return {
    schema_version: 1,
    incident_id: `incident:${kind}:browser${String(index).padStart(6, '0')}`,
    kind,
    headline: overrides.headline || `Fixture ${kind.replaceAll('_', ' ')} ${index + 1}`,
    summary: overrides.summary || 'Verified local fixture with enough context to understand the current situation.',
    status: overrides.status || 'active',
    severity: overrides.severity || ['Extreme', 'Severe', 'Moderate', 'Minor'][index % 4],
    urgency: overrides.urgency || (index % 2 ? 'Expected' : 'Immediate'),
    certainty: overrides.certainty || (index % 3 ? 'Likely' : 'Observed'),
    priority_score: overrides.priority_score ?? 96 - index,
    priority_components: {
      rule_version: 'priority-v1', lane: 'hazards', impact: 30,
      urgency: 20, freshness: 15, corroboration: index % 4 ? 5 : 0,
      watch_region: 0, penalty: 0, age_hours: 1, source_count: 2,
      correlation_rule: 'fixture', correlation_version: 'correlation-v1',
      correlation_evidence: 'browser fixture',
    },
    first_seen_at: '2026-07-10T19:00:00Z',
    last_changed_at: `2026-07-10T${String(21 - (index % 3)).padStart(2, '0')}:30:00Z`,
    last_observed_at: '2026-07-10T21:30:00Z',
    observation_ids: [`fixture:${String(index).padStart(24, '0')}`],
    change_type: overrides.change_type || (index % 5 === 0 ? 'escalated' : 'updated'),
    revision: overrides.revision || 2,
    geometry: Object.hasOwn(overrides, 'geometry')
      ? overrides.geometry : { type: 'Point', coordinates: [139.7 - index, 35.6 - index / 2] },
    centroid: Object.hasOwn(overrides, 'centroid')
      ? overrides.centroid : [139.7 - index, 35.6 - index / 2],
    bbox: Object.hasOwn(overrides, 'bbox')
      ? overrides.bbox : [139.7 - index, 35.6 - index / 2, 139.7 - index, 35.6 - index / 2],
    relations: overrides.relations || [],
    sources: [{
      provider_id: index % 2 ? 'nws_alerts' : 'usgs_earthquakes',
      attribution: index % 2 ? 'National Weather Service' : 'USGS',
      provider_record_id: `record-${index}`,
      source_url: 'https://example.test/evidence',
    }],
    lane: 'hazards',
    observation_count: 1,
    observations_truncated: false,
  };
}

const overviewItems = Array.from({ length: 12 }, (_, index) => incident(index));

function observationFixture(item, overrides = {}) {
  const declaration = item.kind === 'disaster_declaration';
  const marine = item.kind === 'marine_observation';
  const waterLevel = item.kind === 'water_level';
  const fireball = item.kind === 'fireball';
  let metrics = { magnitude: { value: 6.2, unit: 'Mw', provenance: 'Fixture source magnitude' } };
  if (declaration) {
    metrics = {
      administrative_context: {
        value: 'federal_disaster_declaration', unit: 'semantics', provenance: 'OpenFEMA dataset',
      },
      incident_begin: {
        value: '2026-07-05T00:00:00Z', unit: 'RFC 3339', provenance: 'incidentBeginDate',
      },
    };
  } else if (marine) {
    metrics = {
      wind_speed: { value: 19, unit: 'kn', provenance: 'NDBC station report' },
      significant_wave_height: { value: 6.9, unit: 'ft', provenance: 'NDBC station report' },
    };
  } else if (waterLevel) {
    metrics = {
      water_level: { value: 1.099, unit: 'm', provenance: 'CO-OPS data.v; MLLW' },
      quality_flag: { value: 'preliminary', unit: 'CO-OPS QA', provenance: 'CO-OPS data.q' },
    };
  } else if (fireball) {
    metrics = {
      radiated_energy: { value: 2.3, unit: '10^10 J', provenance: 'NASA/JPL data.energy' },
      impact_energy: { value: 0.082, unit: 'kt', provenance: 'NASA/JPL data.impact-e' },
      peak_brightness_altitude: { value: 32.1, unit: 'km', provenance: 'NASA/JPL data.alt' },
    };
  }
  return {
    schema_version: 1,
    observation_id: `fixture:${String(item.incident_id).replace(/[^a-z0-9]/gi, '').slice(-24).padStart(24, '0')}`,
    provider_id: item.sources[0].provider_id,
    provider_record_id: `observation-${item.incident_id}`,
    kind: item.kind,
    headline: item.headline,
    summary: item.summary,
    status: item.status,
    severity: item.severity,
    urgency: item.urgency,
    certainty: item.certainty,
    event_at: declaration ? null : '2026-07-10T20:00:00Z',
    effective_at: declaration ? '2026-07-10T18:00:00Z'
      : marine || waterLevel || fireball ? null : '2026-07-10T20:05:00Z',
    expires_at: declaration || marine || waterLevel || fireball ? null : '2026-07-11T04:00:00Z',
    source_updated_at: '2026-07-10T21:00:00Z',
    ingested_at: '2026-07-10T21:01:00Z',
    geometry: item.geometry,
    centroid: item.centroid,
    bbox: item.bbox,
    location_name: 'Fixture Coast',
    country_codes: ['JP'],
    metrics,
    source_url: item.sources[0].source_url,
    content_hash: 'a'.repeat(64),
    raw_fingerprint: 'b'.repeat(64),
    ...overrides,
  };
}

function detailFixture(item, overrides = {}) {
  const observations = overrides.observations || [observationFixture(item)];
  return {
    ...item,
    observation_count: observations.length,
    observations_truncated: false,
    observations,
    ...overrides,
  };
}

function timelineFixture(item) {
  const first = {
    ...item, revision: 1, change_type: 'new', priority_score: Math.max(0, item.priority_score - 10),
    last_changed_at: '2026-07-10T19:00:00Z',
  };
  return {
    items: [
      { cursor: 1, revision: 1, changed_at: first.last_changed_at, change_type: 'new', incident: first },
      { cursor: 2, revision: item.revision, changed_at: item.last_changed_at, change_type: item.change_type, incident: item },
    ],
  };
}

function bootstrapFixture({ items = overviewItems, counts = { live: 8, cached: 1 }, sources } = {}) {
  const healthSources = sources || [
    { provider_id: 'usgs_earthquakes', status: 'live', detail: 'observations=1' },
    { provider_id: 'nws_alerts', status: 'live', detail: 'observations=1' },
  ];
  return {
    schema_version: 1,
    incidents: { items, next_cursor: null, total: items.length },
    taxonomy: { schema_version: 1 },
    source_health: { counts, sources: healthSources },
    revision_cursor: 12,
    last_revision_at: '2026-07-11T02:30:00Z',
  };
}

const overviewConfig = {
  overview_enabled: true,
  overview_requested: true,
  v2_available: true,
  default_mode: 'overview',
  open_meteo_enabled: false,
  yahoo_finance_enabled: false,
};

const watchRegionFixture = {
  id: 'watch:browser-global', label: 'Browser watch', scope: 'global', geometry: null,
  radius_km: 100, kinds: ['earthquake'], minimum_severity: 'Moderate',
  keywords: [], enabled: true,
};

const jsonBodies = {
  '/api/session': { token: 'browser-fixture-session-token' },
  '/api/settings': settings,
  '/api/app-config': {
    overview_enabled: false, v2_available: false, default_mode: 'overview',
    open_meteo_enabled: false, yahoo_finance_enabled: false,
  },
  '/api/providers': {
    schema_version: 1,
    items: [
      { id: 'usgs_earthquakes', attribution: 'USGS', terms: 'https://www.usgs.gov/', auth: 'none', decision: 'approved', overview: true },
      { id: 'nasa_firms', attribution: 'NASA FIRMS', terms: 'https://firms.modaps.eosdis.nasa.gov/', auth: 'user MAP_KEY', decision: 'optional', overview: false },
    ],
  },
  '/api/usgs': {
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [139.7, 35.6, 20] },
      properties: {
        id: 'fixture-quake-1', mag: 6.2, place: 'Fixture Coast', time: 1783700000000,
        updated: 1783700300000, url: 'https://earthquake.usgs.gov/', tsunami: 0,
      },
    }],
  },
  '/api/nws': {
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      geometry: null,
      properties: {
        id: 'fixture-alert-1', event: 'Severe Thunderstorm Warning', severity: 'Severe',
        urgency: 'Immediate', certainty: 'Observed', areaDesc: 'Fixture County',
        headline: 'Severe weather fixture', description: 'Fixture description.',
        instruction: 'Move indoors.', onset: '2026-07-10T20:00:00Z',
        expires: '2026-07-10T22:00:00Z', senderName: 'Fixture Weather Office',
      },
    }],
  },
  '/api/conflict': { articles: [{ ts: 1783700000, src: 'UN/PEACE', title: 'Fixture ceasefire talks', link: 'https://news.un.org/' }] },
  '/api/conflict-hotspots': { type: 'FeatureCollection', features: [] },
  '/api/eonet': { events: [] },
  '/api/flights': { ac: [] },
  '/api/firms': { items: [] },
  '/api/defense-wire': { articles: [] },
  '/api/commodities': { items: { GOLD: { sym: 'GC=F', close: 2400, chg: 0.4 } } },
  '/api/gdacs': { items: [] },
  '/api/cyclones': { activeStorms: [] },
  '/api/relief': { articles: [{ ts: 1783700000, title: 'Fixture humanitarian update', link: 'https://reliefweb.int/' }] },
  '/api/space-weather': [],
  '/api/iss': { message: 'success', timestamp: 1783700000, iss_position: { latitude: '12.3', longitude: '45.6' } },
  '/api/crypto': [{ id: 'bitcoin', symbol: 'BTC', rank: 1, quotes: { USD: { price: 60000, percent_change_24h: 1.2 } } }],
  '/api/forex': { base: 'USD', date: '2026-07-10', rates: { EUR: 0.85, GBP: 0.74, JPY: 145 } },
  '/api/tsunami': { items: [] },
  '/api/volcanoes-real': { items: [] },
  '/api/mempool': { fees: {}, mempool: {}, blocks: [], difficulty: {}, _freshness: {} },
  '/api/github': [],
  '/api/wiki/recent': { events: [] },
  '/api/sec': {},
  '/api/hn/top': [],
  '/api/reddit': { items: [] },
};

async function installDeterministicNetwork(page, overrides = {}) {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/assets/natural-earth-110m-countries.v5.1.1.geojson'
        && overrides[url.pathname]?.status) {
      return route.fulfill({
        status: overrides[url.pathname].status,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'fixture map asset failure' }),
      });
    }
    if (url.origin === 'http://127.0.0.1:19876') return route.continue();
    if (url.hostname === 'tile.openstreetmap.org' && overrides.__tileSuccess) {
      return route.fulfill({ body: deterministicMapTile, contentType: 'image/svg+xml' });
    }
    return route.abort();
  });

  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (route.request().method() === 'POST') {
      let patch = {};
      try { patch = route.request().postDataJSON() || {}; } catch { patch = {}; }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...settings, ...patch }),
      });
    }
    if (url.pathname === '/api/rss') {
      return route.fulfill({
        status: 200,
        headers: { 'X-Foglight-Freshness': 'live' },
        contentType: 'application/rss+xml',
        body: '<rss><channel><item><title>Fixture world headline</title><pubDate>Fri, 10 Jul 2026 20:00:00 GMT</pubDate></item></channel></rss>',
      });
    }
    let selected = Object.hasOwn(overrides, url.pathname) ? overrides[url.pathname] : jsonBodies[url.pathname];
    if (selected == null && url.pathname.startsWith('/api/v2/incidents/')) {
      const suffix = url.pathname.slice('/api/v2/incidents/'.length);
      const timelineRequest = suffix.endsWith('/timeline');
      const id = decodeURIComponent(timelineRequest ? suffix.slice(0, -'/timeline'.length) : suffix);
      const loaded = overrides['/api/v2/bootstrap']?.incidents?.items || overviewItems;
      const item = loaded.find(candidate => candidate.incident_id === id) || overviewItems.find(candidate => candidate.incident_id === id);
      if (overrides.__drawerFailure && !timelineRequest) {
        selected = { status: 503, body: { error: 'fixture detail failure' } };
      } else if (overrides.__drawerTimelineFailure && timelineRequest) {
        selected = { status: 503, body: { error: 'fixture timeline failure' } };
      } else if (overrides.__drawerMalformed && !timelineRequest) {
        selected = { ...detailFixture(item || overviewItems[0]), sources: {}, observations: {}, relations: {} };
      } else if (overrides.__drawerTimeline?.id === id) {
        selected = timelineRequest ? overrides.__drawerTimeline.timeline : overrides.__drawerTimeline.detail;
      } else {
        selected = timelineRequest ? timelineFixture(item || overviewItems[0]) : detailFixture(item || overviewItems[0]);
      }
    }
    if (selected == null && url.pathname === '/api/v2/search') {
      const query = String(url.searchParams.get('q') || '').toLowerCase();
      const loaded = overrides['/api/v2/bootstrap']?.incidents?.items || overviewItems;
      const items = loaded.filter(item => (
        `${item.headline} ${item.summary} ${item.kind} ${item.status}`.toLowerCase().includes(query)
      )).slice(0, 50);
      selected = { query, items, count: items.length };
    }
    if (selected == null && url.pathname.startsWith('/api/v2/source-health/')) {
      const providerId = decodeURIComponent(url.pathname.slice('/api/v2/source-health/'.length));
      selected = {
        provider_id: providerId,
        attribution: providerId === 'usgs_earthquakes' ? 'USGS'
          : providerId === 'openfema_declarations' ? 'FEMA OpenFEMA'
            : providerId === 'nasa_jpl_fireballs' ? 'NASA/JPL CNEOS'
            : 'National Weather Service',
        status: 'live', detail: 'fixture source current', last_success_at: '2026-07-11T02:30:00Z',
      };
    }
    if (selected?.delay) await new Promise(resolve => setTimeout(resolve, selected.delay));
    if (Number.isInteger(selected?.status)) {
      return route.fulfill({ status: selected.status, headers: { 'X-Foglight-Freshness': selected.freshness || 'error' }, contentType: 'application/json', body: JSON.stringify(selected.body || { error: 'fixture failure' }) });
    }
    return route.fulfill({ status: 200, headers: { 'X-Foglight-Freshness': 'live' }, contentType: 'application/json', body: JSON.stringify(selected ?? {}) });
  });
}

test.beforeEach(async ({ page }, testInfo) => {
  const errors = [];
  pageErrors.set(page, errors);
  page.on('pageerror', error => errors.push(error.message));
  let overrides = testInfo.title === 'renders a provider error without losing the application shell'
    ? { '/api/usgs': { status: 502, freshness: 'error' } } : {};
  if (testInfo.title.includes('[overview')) {
    overrides = {
      ...overrides,
      '/api/app-config': overviewConfig,
      '/api/settings': { ...settings, display_mode: 'overview', first_run_done: true },
      '/api/v2/bootstrap': bootstrapFixture(),
      '/api/v2/changes': { items: [], next_cursor: 12 },
      '/api/v2/source-health': bootstrapFixture().source_health,
    };
  }
  if (testInfo.title.includes('[overview loading]')) {
    overrides['/api/v2/bootstrap'] = { delay: 350, ...bootstrapFixture() };
  }
  if (testInfo.title.includes('[overview empty]')) {
    overrides['/api/v2/bootstrap'] = bootstrapFixture({ items: [] });
  }
  if (testInfo.title.includes('[overview partial]')) {
    overrides['/api/v2/bootstrap'] = bootstrapFixture({
      counts: { live: 7, error: 2 },
      sources: [{ provider_id: 'gdacs', status: 'error', detail: 'timeout' }],
    });
  }
  if (testInfo.title.includes('[overview stale]')) {
    overrides['/api/v2/bootstrap'] = bootstrapFixture({
      counts: { cached: 6, stale: 3 },
      sources: [{ provider_id: 'gdacs', status: 'stale', detail: 'cached data' }],
    });
  }
  if (testInfo.title.includes('[overview offline]')) {
    overrides['/api/v2/bootstrap'] = {
      status: 503, freshness: 'error', body: { error: 'fixture offline' },
    };
  }
  if (testInfo.title.includes('[overview first run]')) {
    overrides['/api/settings'] = { ...settings, display_mode: 'overview', first_run_done: false };
  }
  if (testInfo.title.includes('[overview watch]')) {
    overrides['/api/settings'] = { ...settings, display_mode: 'overview', watchlist: ['storm'] };
  }
  if (testInfo.title.includes('[overview notifications]')) {
    overrides['/api/settings'] = {
      ...settings,
      display_mode: 'overview',
      watch_regions: [watchRegionFixture],
      notifications: {
        enabled: false, in_app: true, system: true,
        quiet_start: '22:00', quiet_end: '07:00', minimum_severity: 'Moderate',
        kinds: ['earthquake'], changes: ['new', 'escalated'],
      },
      notification_state: { seen_revision_keys: [], acknowledged_keys: [], snoozed: [] },
    };
  }
  if (testInfo.title.includes('[overview offline history]')) {
    overrides['/api/v2/bootstrap'] = bootstrapFixture({
      counts: { cached: 2, stale: 1 },
      sources: [
        { provider_id: 'usgs_earthquakes', status: 'cached', cached_age_seconds: 3600, last_success_at: '2026-07-11T02:00:00Z' },
        { provider_id: 'nws_alerts', status: 'stale', cached_age_seconds: 7200, last_success_at: '2026-07-11T01:00:00Z' },
      ],
    });
  }
  if (testInfo.title.includes('[overview performance]')) {
    const many = Array.from({ length: 1000 }, (_, index) => incident(index));
    overrides['/api/v2/bootstrap'] = bootstrapFixture({ items: many });
  }
  if (testInfo.title.includes('[overview aviation]')) {
    const aviation = incident(0, {
      kind: 'aviation_hazard',
      headline: 'CONVECTIVE SIGMET 3E',
      severity: 'Unknown',
      urgency: 'Unknown',
      certainty: 'Unknown',
      priority_score: 15,
    });
    aviation.sources = [{
      provider_id: 'noaa_aviation_weather',
      attribution: 'NOAA Aviation Weather Center',
      provider_record_id: 'KZDV:SIGMET:3E:2026-07-10T21:00:00Z',
      source_url: 'https://aviationweather.gov/sigmet',
    }];
    aviation.priority_components.lane = 'mobility';
    aviation.priority_components.impact = 0;
    aviation.priority_components.urgency = 0;
    aviation.priority_components.corroboration = 0;
    overrides['/api/v2/bootstrap'] = bootstrapFixture({ items: [aviation] });
  }
  if (testInfo.title.includes('[overview declaration]')) {
    const declaration = incident(0, {
      kind: 'disaster_declaration',
      headline: 'SEVERE STORMS AND FLOODING',
      summary: 'Official FEMA administrative declaration context; not event onset.',
      status: 'unknown',
      severity: 'Unknown',
      urgency: 'Unknown',
      certainty: 'Unknown',
      priority_score: 15,
      geometry: null,
      centroid: null,
      bbox: null,
    });
    declaration.sources = [{
      provider_id: 'openfema_declarations',
      attribution: 'FEMA OpenFEMA',
      provider_record_id: 'fixture-openfema-1',
      source_url: 'https://www.fema.gov/disaster/4999',
    }];
    declaration.priority_components.lane = 'hazards';
    declaration.priority_components.impact = 0;
    declaration.priority_components.urgency = 0;
    declaration.priority_components.corroboration = 0;
    overrides['/api/v2/bootstrap'] = bootstrapFixture({ items: [declaration] });
  }
  if (testInfo.title.includes('[overview marine]')) {
    const marine = incident(0, {
      kind: 'marine_observation', headline: 'Station 46042 latest observation',
      severity: 'Unknown', urgency: 'Unknown', certainty: 'Unknown', priority_score: 20,
      centroid: [-122.408, 36.787],
    });
    marine.sources = [{
      provider_id: 'ndbc_observations', attribution: 'NOAA NDBC',
      provider_record_id: '46042', source_url: 'https://www.ndbc.noaa.gov/station_page.php?station=46042',
    }];
    marine.priority_components.lane = 'mobility';
    marine.priority_components.impact = 0;
    marine.priority_components.urgency = 0;
    const water = incident(1, {
      kind: 'water_level', headline: 'Water level at San Francisco',
      severity: 'Unknown', urgency: 'Unknown', certainty: 'Unknown', priority_score: 15,
      centroid: [-122.4659, 37.8063],
    });
    water.sources = [{
      provider_id: 'noaa_coops_water_levels', attribution: 'NOAA CO-OPS',
      provider_record_id: '9414290',
      source_url: 'https://tidesandcurrents.noaa.gov/stationhome.html?id=9414290',
    }];
    water.priority_components.lane = 'mobility';
    water.priority_components.impact = 0;
    water.priority_components.urgency = 0;
    overrides['/api/v2/bootstrap'] = bootstrapFixture({ items: [marine, water] });
  }
  if (testInfo.title.includes('[overview fireball]')) {
    const fireball = incident(0, {
      kind: 'fireball', headline: 'Reported fireball 2026-07-01 12:34:56 UTC',
      summary: 'NASA/JPL reports radiated and estimated impact energy at peak brightness.',
      status: 'ended', severity: 'Unknown', urgency: 'Unknown', certainty: 'Unknown',
      priority_score: 0, centroid: [-20.25, 10.5],
    });
    fireball.sources = [{
      provider_id: 'nasa_jpl_fireballs', attribution: 'NASA/JPL CNEOS',
      provider_record_id: '2026-07-01T12:34:56Z',
      source_url: 'https://cneos.jpl.nasa.gov/fireballs/',
    }];
    fireball.priority_components.lane = 'science';
    fireball.priority_components.impact = 0;
    fireball.priority_components.urgency = 0;
    fireball.priority_components.freshness = 0;
    fireball.priority_components.penalty = -40;
    overrides['/api/v2/bootstrap'] = bootstrapFixture({ items: [fireball] });
  }
  if (testInfo.title.includes('[overview map performance]')) {
    const many = Array.from({ length: 5000 }, (_, index) => incident(index, {
      centroid: [-170 + (index % 340) + (index % 10) / 10, -75 + (index % 150)],
      geometry: index % 100 === 0 ? {
        type: 'Polygon',
        coordinates: [[[-179, -10], [179, -10], [179, 10], [-179, 10], [-179, -10]]],
      } : undefined,
    }));
    overrides['/api/v2/bootstrap'] = bootstrapFixture({ items: many });
  }
  if (testInfo.title.includes('[overview map pin limit]')) {
    overrides['/api/settings'] = {
      ...settings,
      display_mode: 'overview',
      annotations: Array.from({ length: 100 }, (_value, index) => ({
        lat: 0, lon: index - 50, label: `Pin ${index + 1}`,
      })),
    };
  }
  if (testInfo.title.includes('[overview map base failure]')) {
    overrides['/assets/natural-earth-110m-countries.v5.1.1.geojson'] = { status: 503 };
  }
  if (testInfo.title.includes('[overview map tile success]')) overrides.__tileSuccess = true;
  if (testInfo.title.includes('[overview drawer timeline')) {
    const current = {
      ...overviewItems[0], revision: 4, status: 'ended', change_type: 'resolved',
      severity: 'Severe', priority_score: 60,
      first_seen_at: '2026-07-05T12:00:00Z',
      last_changed_at: '2026-07-11T02:30:00Z', last_observed_at: '2026-07-11T02:25:00Z',
      relations: [{ relation_type: 'related_to', target_incident_id: overviewItems[1].incident_id }],
      sources: [overviewItems[0].sources[0], {
        ...overviewItems[1].sources[0], provider_record_id: 'related-warning',
      }],
    };
    const documents = [
      { ...current, revision: 1, status: 'active', change_type: 'new', severity: 'Moderate', priority_score: 40, last_changed_at: '2026-07-05T12:00:00Z' },
      { ...current, revision: 2, status: 'updated', change_type: 'escalated', severity: 'Extreme', priority_score: 90, last_changed_at: '2026-07-10T12:00:00Z' },
      { ...current, revision: 3, status: 'updated', change_type: 'downgraded', severity: 'Severe', priority_score: 70, last_changed_at: '2026-07-10T23:00:00Z' },
      current,
    ];
    const observations = [observationFixture(current), observationFixture(current, {
      observation_id: 'fixture:drawerwarning00000001',
      provider_id: 'nws_alerts', provider_record_id: 'drawer-warning', kind: 'weather_alert',
      headline: 'Fixture warning cancellation', status: 'cancelled', change_type: 'cancelled',
      expires_at: '2026-07-10T23:30:00Z', source_url: 'https://weather.gov/fixture-warning',
      metrics: { wind_speed: { value: 65, unit: 'mph', provenance: 'NWS warning' } },
    })];
    overrides['/api/v2/bootstrap'] = bootstrapFixture({ items: [current, ...overviewItems.slice(1)] });
    overrides.__drawerTimeline = {
      id: current.incident_id,
      detail: detailFixture(current, { observations, observation_count: 2 }),
      timeline: {
        items: documents.map((document, index) => ({
          cursor: index + 1, revision: document.revision,
          changed_at: document.last_changed_at, change_type: document.change_type,
          incident: document,
        })),
      },
    };
  }
  if (testInfo.title.includes('[overview drawer failure]')) overrides.__drawerFailure = true;
  if (testInfo.title.includes('[overview drawer partial]')) overrides.__drawerTimelineFailure = true;
  if (testInfo.title.includes('[overview drawer malformed]')) overrides.__drawerMalformed = true;
  if (testInfo.title.includes('[overview catalog]')) {
    overrides['/api/v2/bootstrap'] = {
      ...bootstrapFixture({ items: overviewItems.slice(0, 3) }),
      incidents: { items: overviewItems.slice(0, 3), next_cursor: 3, total: 12 },
    };
    overrides['/api/v2/incidents'] = {
      items: overviewItems.slice(3), next_cursor: null, total: 12,
    };
  }
  if (testInfo.title.includes('[overview onboarding failure]')) {
    overrides['/api/settings'] = { ...settings, display_mode: 'overview', first_run_done: false };
    overrides['/api/v2/bootstrap'] = {
      status: 503, freshness: 'error', body: { error: 'fixture offline' },
    };
  }
  await installDeterministicNetwork(page, overrides);
});

test.afterEach(async ({ page }) => {
  expect(pageErrors.get(page) || []).toEqual([]);
});

test('starts with deterministic data and supports primary settings interaction', async ({ page }) => {
  const requestedPaths = [];
  page.on('request', request => requestedPaths.push(new URL(request.url()).pathname));
  await page.setViewportSize({ width: 1500, height: 950 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#map')).toHaveClass(/leaflet-container/);
  await expect(page.locator('#body-quakes')).toContainText('Fixture Coast');
  const attributionCovered = await page.evaluate(() => {
    const attribution = document.querySelector('.leaflet-control-attribution')?.getBoundingClientRect();
    const cta = document.getElementById('optional-cta')?.getBoundingClientRect();
    if (!attribution || !cta) return false;
    return attribution.left < cta.right && attribution.right > cta.left
      && attribution.top < cta.bottom && attribution.bottom > cta.top;
  });
  expect(attributionCovered).toBe(false);
  await expect(page.locator('#body-weather')).toContainText('Severe Thunderstorm Warning');
  await expect(page.locator('#body-conflict')).toContainText('Fixture ceasefire talks');
  await expect(page.locator('#stat-feeds-txt')).toHaveText('15/15 live');
  expect(await page.evaluate(() => window.__foglightFeedHealth)).toEqual({
    live: 15, cached: 0, errored: 0, total: 15,
  });
  expect(requestedPaths).not.toContain('/api/flights');
  expect(requestedPaths).not.toContain('/api/commodities');

  await page.locator('#action-settings').click();
  await expect(page.locator('#pane-settings')).toHaveClass(/show/);
  await expect(page.getByRole('heading', { name: /Foglight.*Settings/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Data sources & attribution' })).toBeVisible();
  await expect(page.locator('#provider-attributions')).toContainText('Overview sources (1)');
  await expect(page.locator('#provider-attributions')).toContainText('USGS');
  await expect(page.locator('#provider-attributions')).toContainText('NASA FIRMS');
  await expect(page.locator('#provider-attributions a').first()).toHaveAttribute('href', 'https://www.usgs.gov/');
  await page.locator('#settings-close').click();
  await expect(page.locator('#pane-settings')).not.toHaveClass(/show/);
});

test('[conditional standard disabled] map click does not contact Open-Meteo', async ({ page }) => {
  const paths = [];
  page.on('request', request => paths.push(new URL(request.url()).pathname));
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#map')).toHaveClass(/leaflet-container/);
  await page.locator('#map').click({ position: { x: 300, y: 220 } });
  await page.waitForTimeout(200);
  expect(paths).not.toContain('/api/openmeteo');
});

test('[conditional standard yahoo enabled] fetches commodities only after opt-in', async ({ page }) => {
  const paths = [];
  page.on('request', request => paths.push(new URL(request.url()).pathname));
  await page.route('**/api/app-config', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      overview_enabled: false, v2_available: false, default_mode: 'overview',
      open_meteo_enabled: false, yahoo_finance_enabled: true,
    }),
  }));
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => paths.includes('/api/commodities')).toBe(true);
});

test('[settings attribution failure] keeps Settings usable', async ({ page }) => {
  await page.route('**/api/providers', route => route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({ error: 'fixture catalog failure' }),
  }));
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.locator('#provider-attributions')).toHaveText(
    'Source terms are temporarily unavailable.',
  );
  await expect(page.getByRole('heading', { name: 'Panels' })).toBeVisible();
});

test('[settings attribution malformed] fails closed without a page error', async ({ page }) => {
  await page.route('**/api/providers', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ items: [null, false, 'bad'] }),
  }));
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.locator('#provider-attributions')).toHaveText(
    'Source terms are temporarily unavailable.',
  );
});

test('[overview aviation] presents official advisories in the default mobility view', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-now-list')).toContainText('CONVECTIVE SIGMET 3E');
  await expect(page.locator('#overview-now-list')).toContainText('Aviation hazard');
  await expect(page.locator('#overview-now-list')).toContainText('NOAA Aviation Weather Center');
  const mobility = page.getByRole('button', { name: 'Aviation / marine' });
  await mobility.click();
  await expect(mobility).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#overview-now-list .overview-incident')).toHaveCount(1);
});

test('[overview declaration] labels FEMA context without emergency semantics', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-now-list')).toContainText('SEVERE STORMS AND FLOODING');
  await expect(page.locator('#overview-now-list')).toContainText('FEMA declaration');
  await expect(page.locator('#overview-now-list')).toContainText('FEMA OpenFEMA');
  await expect(page.locator('#overview-now-list')).toContainText('Unknown severity');
  await page.locator('.overview-incident-summary').first().press('Enter');
  await expect(page.locator('#incident-drawer-body')).toContainText('Administrative declaration');
  await expect(page.locator('#incident-drawer-body')).toContainText('Unknown / Unknown');
});

test('[overview marine] presents bounded source measurements with units and quality', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-now-list')).toContainText('Station 46042 latest observation');
  await expect(page.locator('#overview-now-list')).toContainText('Water level at San Francisco');
  await expect(page.locator('#overview-now-list')).toContainText('NOAA NDBC');
  await expect(page.locator('#overview-now-list')).toContainText('NOAA CO-OPS');
  const mobility = page.getByRole('button', { name: 'Aviation / marine' });
  await mobility.click();
  await expect(page.locator('#overview-now-list .overview-incident')).toHaveCount(2);
  await page.getByText('Water level at San Francisco', { exact: true }).first().click();
  await expect(page.locator('#incident-drawer-body')).toContainText('Source measurement');
  await expect(page.locator('#incident-drawer-body')).toContainText('water level');
  await expect(page.locator('#incident-drawer-body')).toContainText('1.099 m');
  await expect(page.locator('#incident-drawer-body')).toContainText('preliminary CO-OPS QA');
});

test('[overview fireball] presents a low-frequency observation without emergency semantics', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-now-list')).toContainText('Reported fireball 2026-07-01');
  await expect(page.locator('#overview-now-list')).toContainText('Fireball observation');
  await expect(page.locator('#overview-now-list')).toContainText('NASA/JPL CNEOS');
  const signals = page.getByRole('button', { name: 'Signals' });
  await signals.click();
  await expect(page.locator('#overview-now-list .overview-incident')).toHaveCount(1);
  await page.locator('.overview-incident-summary').first().click();
  await expect(page.locator('#incident-drawer-body')).toContainText('Source measurement');
  await expect(page.locator('#incident-drawer-body')).toContainText('2.3 10^10 J');
  await expect(page.locator('#incident-drawer-body')).toContainText('0.082 kt');
  await expect(page.locator('#incident-drawer-body')).toContainText('Unknown / Unknown');
  await expect(page.locator('.drawer-status-line')).toContainText('ended');
});

test('[overview conditional disabled] needs no conditional provider', async ({ page }) => {
  const paths = [];
  page.on('request', request => paths.push(new URL(request.url()).pathname));
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-state-title')).toHaveText('Current picture is ready');
  await expect(page.locator('#overview-now-list .overview-incident')).toHaveCount(8);
  await expect(page.locator('#overview-count')).toContainText('12 matching');
  expect(paths).not.toContain('/api/openmeteo');
  expect(paths.every(path => !path.toLowerCase().includes('gdelt'))).toBe(true);
});

test('has no critical automated accessibility violations in the baseline shell', async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 950 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#body-quakes')).toContainText('Fixture Coast');
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter(item => ['critical', 'serious'].includes(item.impact))).toEqual([]);
  const snapshot = await page.locator('#top-actions').ariaSnapshot();
  expect(snapshot).toContain('button "Settings"');
  expect(snapshot).toContain('button "Shut down"');
  await expect(page.getByRole('button', { name: /BRIEF/ })).toBeVisible();

  await page.locator('#action-settings').click();
  const settingsSnapshot = await page.locator('#pane-settings').ariaSnapshot();
  expect(settingsSnapshot).toContain('heading "Foglight · Settings"');
  expect(settingsSnapshot).toContain('Back to dashboard');
  expect(settingsSnapshot).toContain('Data sources & attribution');
  const settingsAxe = await new AxeBuilder({ page }).include('#pane-settings').analyze();
  expect(settingsAxe.violations.filter(item => ['critical', 'serious'].includes(item.impact))).toEqual([]);
  await page.evaluate(() => { document.documentElement.style.zoom = '200%'; });
  await expect(page.getByRole('heading', { name: 'Data sources & attribution' })).toBeVisible();
  const settingsOverflow = await page.locator('#pane-settings').evaluate(
    node => node.scrollWidth - node.clientWidth,
  );
  expect(settingsOverflow).toBeLessThanOrEqual(1);
  await page.evaluate(() => { document.documentElement.style.zoom = '100%'; });
  await page.locator('#settings-close').click();

  await page.locator('#body-weather .row').first().click();
  const alertSnapshot = await page.locator('#alert-drawer').ariaSnapshot();
  expect(alertSnapshot).toContain('Severe Thunderstorm Warning');
  expect(alertSnapshot).toContain('button "Close alert details"');
});

test('switches theater mode and supports map and list selection', async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 950 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#body-quakes')).toContainText('Fixture Coast');

  const ukraine = page.locator('#theaterbar [data-theater="ukr"]');
  await ukraine.click();
  await expect(ukraine).toHaveClass(/active/);
  await expect(page.locator('#theaterbar [data-theater="global"]')).not.toHaveClass(/active/);

  await page.locator('#body-weather .row').first().click();
  await expect(page.locator('#alert-drawer')).toHaveClass(/show/);
  await expect(page.locator('#alert-drawer')).toContainText('Severe Thunderstorm Warning');
  await page.locator('#alert-close').click();
  await expect(page.locator('#alert-drawer')).not.toHaveClass(/show/);

  await page.evaluate(() => {
    let firstMarker;
    window.__foglight.layers.quakes.eachLayer(layer => { firstMarker ||= layer; });
    if (!firstMarker) throw new Error('fixture earthquake marker was not rendered');
    firstMarker.openPopup();
  });
  await expect(page.locator('.leaflet-popup-content')).toContainText('Fixture Coast');
});

test('[overview] presents prioritized incidents, filters, changes, and all display modes', async ({ page }) => {
  const localApiRequests = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/')) localApiRequests.push(url.pathname);
  });
  await page.setViewportSize({ width: 1500, height: 950 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('body')).toHaveClass(/mode-overview/);
  await expect(page.locator('#overview-surface')).toHaveAttribute('data-view-state', 'ready');
  await expect(page.locator('#overview-now-list .overview-incident')).toHaveCount(8);
  await expect(page.locator('#overview-now-list')).toContainText('Sources: USGS');
  await expect(page.locator('#overview-now-list')).toContainText('Change: escalated');
  await expect(page.locator('#overview-now-list')).toContainText('priority');
  await expect(page.locator('#main')).toBeHidden();
  expect(localApiRequests.filter(pathname => ![
    '/api/session', '/api/settings', '/api/app-config', '/api/v2/bootstrap',
  ].includes(pathname))).toEqual([]);
  await page.evaluate(() => window.generateBriefing());
  await expect(page.locator('#overview-live')).toHaveText('Select an incident before opening a printable briefing.');
  await page.getByText('Browse all incidents without the map').click();
  await expect(page.locator('#overview-catalog-list .overview-incident')).toHaveCount(12);

  const natural = page.getByRole('button', { name: 'Natural hazards' });
  await natural.click();
  await expect(natural).toHaveAttribute('aria-pressed', 'true');
  await natural.press('ArrowRight');
  await expect(page.getByRole('button', { name: 'Severe weather' })).toHaveAttribute('aria-pressed', 'true');

  await page.getByRole('button', { name: 'Global' }).click();
  const firstIncident = page.locator('.overview-incident-summary').first();
  await firstIncident.press('Enter');
  await expect(firstIncident).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('.incident-evidence').first()).toContainText('impact +30');
  await expect(page.locator('.incident-source-links a').first()).toHaveAttribute('href', 'https://example.test/evidence');
  await expect(page.locator('#incident-drawer-body')).toContainText('Fixture earthquake 1');
  await page.locator('#incident-drawer-close').click();

  await page.route('**/api/v2/changes?**', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      items: [{
        cursor: 13, incident_id: overviewItems[0].incident_id, revision: 3,
        changed_at: '2026-07-10T22:00:00Z', change_type: 'escalated',
        incident: { ...overviewItems[0], revision: 3, priority_score: 99, change_type: 'escalated' },
      }],
      next_cursor: 13,
    }),
  }));
  await page.route('**/api/v2/source-health', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      counts: { live: 7, error: 2 },
      sources: [{ provider_id: 'gdacs', status: 'error', detail: 'timeout' }],
    }),
  }));
  await page.evaluate(() => window.__foglightOverview.pollChanges());
  await expect(page.locator('#overview-change-summary')).toHaveText('1 escalated');
  await expect(page.locator('#overview-health')).toContainText('2 need attention');

  await page.getByRole('button', { name: 'Command' }).click();
  await expect(page.locator('body')).toHaveClass(/mode-command/);
  await expect(page.locator('#overview-now-list .overview-incident')).toHaveCount(12);
  await expect(page.locator('#overview-surface')).toHaveAttribute('data-density', 'command');

  await page.evaluate(incidentId => {
    window.__foglightOverview.selectIncident(incidentId, { source: 'map' });
    document.querySelector('[data-display-mode="standard"]').click();
  }, overviewItems[0].incident_id);
  await expect(page.locator('body')).toHaveClass(/mode-standard/);
  await page.waitForTimeout(50);
  await expect(page.locator('#incident-drawer')).toBeHidden();
  await expect(page.locator('#map')).toHaveClass(/leaflet-container/);
  await expect(page.locator('#body-quakes')).toContainText('Fixture Coast');
  await page.getByRole('button', { name: 'Overview' }).click();
  await expect(page.locator('#overview-title')).toBeFocused();
});

test('[overview map] remains useful offline and synchronizes map, list, filters, and pins', async ({ page }) => {
  const externalRequests = [];
  page.on('request', request => {
    if (!request.url().startsWith('http://127.0.0.1:19876')) externalRequests.push(request.url());
  });
  await page.setViewportSize({ width: 1500, height: 950 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-surface')).toHaveAttribute('data-view-state', 'ready');
  await expect(page.locator('#overview-map')).toHaveClass(/leaflet-container/);
  await expect(page.locator('#overview-map-status')).toHaveText('Offline world base ready.');
  await expect(page.locator('#overview-map canvas').first()).toBeVisible();
  await expect(page.locator('#overview-map-count')).toContainText('12 incidents');
  await expect(page.locator('.incident-cluster-icon')).toContainText('12');
  expect(externalRequests).toEqual([]);

  await page.locator('.incident-cluster-icon').focus();
  await page.keyboard.press('Enter');
  await expect.poll(() => page.evaluate(() => window.__foglightMapMetrics.zoom)).toBe(2);

  const first = page.locator('#overview-now-list .overview-incident').first();
  await first.locator('.overview-incident-summary').click();
  await expect(first).toHaveClass(/is-selected/);
  await expect(first.locator('.overview-incident-summary')).toHaveAttribute('aria-current', 'true');
  await expect(page.locator('#incident-drawer-body')).toContainText('Fixture earthquake 1');
  await page.locator('#incident-drawer-close').click();

  const ninthId = overviewItems[8].incident_id;
  await page.evaluate(id => window.__foglightOverview.selectIncident(id, { source: 'map' }), ninthId);
  await expect(page.locator('#overview-catalog')).toHaveAttribute('open', '');
  const catalogSelection = page.locator(`#overview-catalog-list [data-incident-id="${ninthId}"]`);
  await expect(catalogSelection).toHaveClass(/is-selected/);
  await expect(catalogSelection.locator('.incident-evidence')).toBeVisible();
  await expect(page.locator('#incident-drawer-title')).toBeFocused();
  await expect(page.locator('#incident-drawer-body')).toContainText('Fixture market snapshot 9');
  await page.locator('#incident-drawer-close').click();
  await expect(catalogSelection.locator('.overview-incident-summary')).toBeFocused();

  await page.locator('[data-incident-filter="natural"]').click();
  await expect(page.locator('#overview-count')).toContainText('matching');
  const filterCount = await page.locator('#overview-now-list .overview-incident').count();
  expect(filterCount).toBeGreaterThan(0);
  await expect(page.locator('#overview-map-count')).not.toHaveText('0 visible markers');

  await page.locator('#overview-pin-lat').fill('85');
  await page.locator('#overview-pin-lon').fill('-179');
  await page.locator('#overview-pin-label').fill('Keyboard pin');
  await page.locator('#overview-pin-form button').press('Enter');
  await expect(page.locator('#overview-map-status')).toHaveText('Added pin “Keyboard pin”.');
  await expect(page.locator('.overview-pin-marker')).toHaveAttribute('title', 'Keyboard pin');
  await expect(page.locator('#overview-pin-lat')).toHaveValue('');
  await expect(page.locator('#overview-pin-lon')).toHaveValue('');
});

test('[overview map] degrades optional tile failure back to the bundled base', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 800 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-map-status')).toHaveText('Offline world base ready.');
  await page.locator('#overview-detail-tiles').click();
  await expect(page.locator('#overview-map-status')).toContainText(/Loading optional|Detailed tiles unavailable/);
  await expect(page.locator('#overview-map-status')).toHaveText('Detailed tiles unavailable; using the offline base.');
  await expect(page.locator('#overview-detail-tiles')).not.toBeChecked();
  await expect(page.locator('#overview-map canvas').first()).toBeVisible();
});

test('[overview map tile success] keeps attribution and the offline base visible', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-map-status')).toHaveText('Offline world base ready.');
  await page.locator('#overview-detail-tiles').click();
  await expect(page.locator('#overview-map-status')).toHaveText(
    'Detailed tiles enabled over the offline base.',
  );
  await expect(page.locator('#overview-map .leaflet-control-attribution')).toContainText(
    'OpenStreetMap contributors',
  );
  await expect(page.locator('#overview-map canvas').first()).toBeVisible();
});

test('[overview map base failure] keeps the coordinate grid and incidents usable', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-map-status')).toHaveText(
    'Boundaries unavailable; coordinate grid remains usable.',
  );
  await expect(page.locator('#overview-map')).toHaveClass(/leaflet-container/);
  await expect(page.locator('.incident-cluster-icon')).toContainText('12');
  await expect(page.locator('#overview-map svg').first()).toBeVisible();
});

test('[overview map pin limit] enforces the persisted 100-pin bound', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-map-status')).toHaveText('Offline world base ready.');
  await page.locator('#overview-pin-lat').fill('10');
  await page.locator('#overview-pin-lon').fill('20');
  await page.locator('#overview-pin-form button').click();
  await expect(page.locator('#overview-map-status')).toHaveText(
    'Foglight stores up to 100 pins. Remove one in Settings first.',
  );
  await expect(page.locator('.overview-pin-marker')).toHaveCount(100);
});

test('[overview map performance] renders 5,000 mixed incidents within the interaction budget', async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 950 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-count')).toContainText('5000 matching');
  await expect.poll(() => page.evaluate(() => window.__foglightMapMetrics?.incidentCount)).toBe(5000);
  const metrics = await page.evaluate(() => window.__foglightMapMetrics);
  expect(metrics.visibleClusters).toBeGreaterThan(0);
  expect(metrics.visibleClusters).toBeLessThan(5000);
  expect(metrics.renderMs).toBeLessThan(1000);
  const footprint = await page.evaluate(() => ({
    canvases: document.querySelectorAll('#overview-map canvas').length,
    domNodes: document.querySelectorAll('#overview-surface *').length,
    heapBytes: performance.memory?.usedJSHeapSize ?? null,
  }));
  expect(footprint.canvases).toBeLessThanOrEqual(4);
  expect(footprint.domNodes).toBeLessThan(2000);
  if (footprint.heapBytes != null) expect(footprint.heapBytes).toBeLessThan(100 * 1024 * 1024);
  await page.locator('#overview-now-list .overview-incident-summary').first().click();
  await expect(page.locator('#overview-now-list .overview-incident').first()).toHaveClass(/is-selected/);
  await expect(page.locator('#incident-drawer-body')).toContainText('Fixture earthquake 1');
  await page.locator('#incident-drawer-close').click();
  await page.locator('#overview-map').focus();
  const centerBeforePan = await page.evaluate(() => window.__foglightMapMetrics.center[0]);
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => page.evaluate(() => window.__foglightMapMetrics.center[0])).not.toBe(centerBeforePan);
  await page.keyboard.press('+');
  await expect(page.locator('#overview-map')).toBeVisible();
});

test('[overview drawer timeline] renders exact history, provenance, copy, and print', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-07-11T03:00:00Z'));
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async value => { window.__copiedIncidentSummary = value; } },
    });
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.getByText('Browse all incidents without the map').click();
  const opener = page.locator(`#overview-catalog-list [data-incident-id="${overviewItems[0].incident_id}"] .overview-incident-summary`);
  await opener.click();
  await expect(page.locator('#incident-drawer')).toBeVisible();
  await expect(page.locator('#incident-drawer-title')).toBeFocused();
  await expect(page.locator('#overview-surface')).toHaveAttribute('inert', '');
  await expect(page.locator('#incident-drawer-body')).toContainText('Fixture earthquake 1');
  await expect(page.locator('.drawer-status-line')).toContainText('ended');
  await expect(page.locator('#incident-drawer-body')).toContainText('USGS');
  await expect(page.locator('#incident-drawer-body')).toContainText('National Weather Service');
  await expect(page.locator('#incident-drawer-body')).toContainText('magnitude');
  await expect(page.locator('#incident-drawer-body')).toContainText('wind speed');
  await expect(page.locator('#incident-drawer-body')).toContainText('Fixture weather alert 2');
  await expect(page.locator('.drawer-observation-list')).toContainText('Observation');
  await expect(page.locator('.drawer-observation-list')).toContainText('Warning');
  await expect(page.locator('.drawer-observation-list')).toContainText('Cancelled');
  await expect(page.locator('.drawer-relation-list')).toContainText('Warning');
  await expect(page.locator('.drawer-source-list a').first()).toHaveAttribute('rel', 'noopener noreferrer');
  await expect(page.locator('.timeline-list li')).toHaveCount(3);
  await expect(page.locator('.timeline-list li').first()).toContainText('status, severity, priority');
  await expect(page.locator('.timeline-list li').first()).not.toContainText('initial record');
  await expect(page.locator('.timeline-list')).toContainText('escalated');
  await expect(page.locator('.timeline-list')).toContainText('downgraded');
  await expect(page.locator('.timeline-list')).toContainText('resolved');

  await page.getByRole('button', { name: '1 hour', exact: true }).click();
  await expect(page.locator('.timeline-list li')).toHaveCount(1);
  await page.getByRole('button', { name: '6 hours', exact: true }).click();
  await expect(page.locator('.timeline-list li')).toHaveCount(2);
  await page.getByRole('button', { name: '7 days', exact: true }).click();
  await expect(page.locator('.timeline-list li')).toHaveCount(4);
  await page.locator('#incident-timeline-scrubber').fill('0');
  await expect(page.locator('#incident-timeline-preview')).toContainText('Revision 1 · new');
  await expect(page.locator('#incident-timeline-preview')).toContainText('initial record');

  await page.getByRole('button', { name: 'Copy deterministic summary' }).click();
  await expect(page.locator('#incident-drawer-live')).toHaveText('Deterministic incident summary copied.');
  const copied = await page.evaluate(() => window.__copiedIncidentSummary);
  expect(copied).toContain('Revision sequence: r1 new');
  expect(copied).toContain('explainable triage, not a prediction');

  const popupPromise = page.waitForEvent('popup');
  await page.getByRole('button', { name: 'Print incident briefing' }).click();
  const popup = await popupPromise;
  await expect(popup.getByRole('heading', { name: 'Fixture earthquake 1' })).toBeVisible();
  await expect(popup.locator('body')).toContainText('Revision 4: resolved');
  await expect(popup.locator('body')).toContainText('Sources and provenance');
  await popup.close();

  await page.keyboard.press('Escape');
  await expect(page.locator('#incident-drawer')).toBeHidden();
  await expect(page.locator('#overview-surface')).not.toHaveAttribute('inert', '');
  await expect(opener).toBeFocused();
});

for (const viewport of [
  { name: 'incident-drawer-1280x900.png', width: 1280, height: 900 },
  { name: 'incident-drawer-520x900.png', width: 520, height: 900 },
]) {
  test(`[overview drawer timeline visual] matches ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.clock.setFixedTime(new Date('2026-07-11T03:00:00Z'));
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.getByText('Browse all incidents without the map').click();
    await page.locator(
      `#overview-catalog-list [data-incident-id="${overviewItems[0].incident_id}"] .overview-incident-summary`,
    ).click();
    const drawer = page.locator('#incident-drawer');
    await expect(drawer).toBeVisible();
    await expect(page.locator('.drawer-health-list')).toContainText('USGS');
    await expect(drawer).toHaveScreenshot(viewport.name, { animations: 'disabled' });
  });
}

test('[overview drawer failure] keeps the compact incident fallback usable', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  const opener = page.locator('#overview-now-list .overview-incident-summary').first();
  await opener.click();
  await expect(page.locator('#incident-drawer-body')).toContainText('Incident details unavailable');
  await expect(page.locator('#incident-drawer-body')).toContainText('Now card remains available');
  await page.locator('#incident-drawer-close').click();
  await expect(opener).toBeFocused();
  await expect(page.locator('.incident-evidence').first()).toBeVisible();
});

test('[overview drawer partial] keeps current facts usable when revision history fails', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.locator('#overview-now-list .overview-incident-summary').first().click();
  await expect(page.locator('#incident-drawer-body')).toContainText('Fixture earthquake 1');
  await expect(page.locator('#incident-drawer-body')).toContainText('Revision history is temporarily unavailable');
  await expect(page.locator('#incident-drawer-body')).toContainText('Sources and provenance');
  await expect(page.locator('#incident-drawer-live')).toContainText('revision history is unavailable');
});

test('[overview drawer malformed] isolates malformed optional collections', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.locator('#overview-now-list .overview-incident-summary').first().click();
  await expect(page.locator('#incident-drawer-body')).toContainText('Fixture earthquake 1');
  await expect(page.locator('#incident-drawer-body')).toContainText('No source reference was reported');
  await expect(page.locator('#incident-drawer-body')).toContainText('No related incident is recorded');
  await expect(page.locator('#incident-drawer-body')).toContainText('No normalized observation detail is available');
});

test('[overview watch] migrates keywords, accepts map coordinates, searches, and exports', async ({ page }) => {
  const writes = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/settings') {
      writes.push(request.postDataJSON());
    }
  });
  await page.clock.setFixedTime(new Date('2026-07-11T03:00:00Z'));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#watch-region-list')).toContainText('Migrated keyword watches');
  await expect(page.locator('#watch-region-list')).toContainText('storm');

  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#watchlist-text').fill('storm\ncoast');
  await page.getByRole('button', { name: 'Save watchlist' }).click();
  await expect(page.getByRole('button', { name: 'Saved' })).toBeVisible();
  await page.getByRole('button', { name: 'Close settings' }).click();
  await expect(page.locator('#watch-region-list')).toContainText('coast');
  await expect.poll(() => writes.some(item => item?.watchlist?.includes('coast')
    && item?.watch_regions?.some(region => region.id === 'legacy:keywords'))).toBe(true);

  await page.locator('#watch-center-details').evaluate(node => { node.open = true; });
  await page.locator('.watch-settings-panel').evaluate(node => { node.open = true; });
  await page.getByRole('button', { name: 'Pick on map' }).click();
  await expect(page.locator('#overview-map')).toHaveClass(/is-coordinate-picking/);
  await page.locator('#overview-map').click({ position: { x: 180, y: 140 } });
  await expect(page.locator('.watch-settings-panel')).toHaveAttribute('open', '');
  await expect(page.locator('#watch-region-label')).toBeFocused();
  await expect(page.locator('#watch-region-lat')).not.toHaveValue('');
  await expect(page.locator('#watch-region-lon')).not.toHaveValue('');
  await expect(page.locator('#watch-region-status')).toContainText('Map coordinates loaded');
  await page.locator('#watch-region-label').fill('Local coast');
  await page.locator('#watch-region-radius').fill('250');
  await page.locator('#watch-region-kind').selectOption('earthquake');
  await page.locator('#watch-region-severity').selectOption('Severe');
  await page.locator('#watch-region-keywords').fill('quake, coast');
  await page.getByRole('button', { name: 'Add watch region' }).click();
  await expect(page.locator('#watch-region-list')).toContainText('Local coast');
  await expect.poll(() => writes.some(item => item?.watch_regions?.length === 2)).toBe(true);
  await expect(page.locator('#watch-region-status')).toContainText('saved locally');
  expect(await page.evaluate(() => window.__foglightWatchCenter.useMapCoordinates({
    latitude: 99, longitude: 0,
  }))).toBe(false);
  await expect(page.locator('#watch-region-status')).toContainText('coordinates were invalid');
  await page.getByRole('button', { name: 'Pick on map' }).click();
  await expect(page.locator('#overview-map')).toHaveClass(/is-coordinate-picking/);
  await page.evaluate(() => window.__foglightWatchCenter.stop());
  await expect(page.locator('#overview-map')).not.toHaveClass(/is-coordinate-picking/);

  await page.locator('#incident-search-query').fill('earthquake');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('#incident-search-status')).toHaveText('1 local incidents found.');
  await page.locator('#incident-search-results button').click();
  await expect(page.locator('#incident-drawer-body')).toContainText('Fixture earthquake 1');
  await page.locator('#incident-drawer-close').click();

  const csvPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  const csvDownload = await csvPromise;
  expect(csvDownload.suggestedFilename()).toBe('foglight-incidents.csv');
  const csv = await downloadText(csvDownload);
  expect(csv).toContain('"incident_id"');
  expect(csv).toContain('incident:earthquake:browser000000');

  const geoPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export GeoJSON' }).click();
  const geoDownload = await geoPromise;
  expect(geoDownload.suggestedFilename()).toBe('foglight-incidents.geojson');
  const geojson = JSON.parse(await downloadText(geoDownload));
  expect(geojson.type).toBe('FeatureCollection');
  expect(geojson.features).toHaveLength(12);
  expect(geojson.features[0].properties.sources[0].attribution).toBe('USGS');
});

test('[overview watch] visual matches the expanded watch-center baseline', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-07-11T03:00:00Z'));
  await page.setViewportSize({ width: 1280, height: 1800 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.locator('#watch-center-details').evaluate(node => { node.open = true; });
  await page.locator('.watch-settings-panel').evaluate(node => { node.open = true; });
  await expect(page.locator('#watch-center')).toHaveScreenshot('watch-center-1280x1000.png', {
    animations: 'disabled',
  });
});

test('[overview watch] visual matches the mobile watch-center baseline', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-07-11T03:00:00Z'));
  await page.setViewportSize({ width: 520, height: 2500 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.locator('#watch-center-details').evaluate(node => { node.open = true; });
  await page.locator('.watch-settings-panel').evaluate(node => { node.open = true; });
  await expect(page.locator('#watch-center')).toHaveScreenshot('watch-center-520x900.png', {
    animations: 'disabled',
  });
});

test('[overview watch] failures roll back settings and keep local tools usable', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.locator('#watch-center-details').evaluate(node => { node.open = true; });
  await page.locator('.watch-settings-panel').evaluate(node => { node.open = true; });
  await page.route('**/api/settings', async route => {
    if (route.request().method() === 'POST') {
      await route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"fixture"}' });
    } else {
      await route.fallback();
    }
  });

  await page.locator('#watch-region-label').fill('Unsaved region');
  await page.locator('#watch-region-lat').fill('10');
  await page.locator('#watch-region-lon').fill('20');
  await page.getByRole('button', { name: 'Add watch region' }).click();
  await expect(page.locator('#watch-region-list')).not.toContainText('Unsaved region');
  await expect(page.locator('#watch-region-status')).toContainText('could not be saved');

  await page.locator('#notification-in-app').uncheck();
  await page.getByRole('button', { name: 'Save alert settings' }).click();
  await expect(page.locator('#notification-in-app')).toBeChecked();
  await expect(page.locator('#notification-permission-status')).toContainText('could not be saved');

  await page.locator('#wall-display-interval').selectOption('10');
  await expect(page.locator('#wall-display-interval')).toHaveValue('30');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.locator('#watchlist-text').fill('unsaved');
  await page.getByRole('button', { name: 'Save watchlist' }).click();
  await expect(page.getByRole('button', { name: 'Save failed' })).toBeVisible();
  await expect(page.locator('#watchlist-text')).toHaveValue('storm');
  await page.getByRole('button', { name: 'Close settings' }).click();

  await page.route('**/api/v2/search**', route => route.fulfill({
    status: 500, contentType: 'application/json', body: '{"error":"fixture"}',
  }));
  await page.locator('#incident-search-query').fill('earthquake');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('#incident-search-status')).toHaveText(
    'Local search is temporarily unavailable.',
  );
});

test('[overview notifications] requires opt-in, deduplicates, acknowledges, snoozes, and honors quiet hours', async ({ page }) => {
  await page.clock.setFixedTime(new Date(2026, 6, 10, 12, 0, 0));
  await page.addInitScript(() => {
    class FixtureNotification {
      static permission = 'default';
      static async requestPermission() {
        window.__notificationPermissionRequests = (window.__notificationPermissionRequests || 0) + 1;
        FixtureNotification.permission = 'granted';
        return 'granted';
      }
      constructor(title, options) {
        if (window.__notificationConstructorFails) throw new Error('fixture notification failure');
        window.__systemNotifications = [...(window.__systemNotifications || []), { title, options }];
      }
    }
    Object.defineProperty(window, 'Notification', { configurable: true, value: FixtureNotification });
  });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-surface')).toHaveAttribute('data-view-state', 'ready');
  await expect.poll(() => page.evaluate(() => Boolean(window.__foglightWatchCenter))).toBe(true);
  const makeChange = revision => ({
    cursor: 20 + revision,
    incident_id: overviewItems[0].incident_id,
    revision,
    changed_at: `2026-07-11T0${Math.min(revision, 9)}:00:00Z`,
    change_type: 'escalated',
    incident: { ...overviewItems[0], revision, change_type: 'escalated' },
  });
  expect(await page.evaluate(change => window.__foglightWatchCenter.processChanges([change]), makeChange(3))).toEqual([]);
  await expect(page.locator('#notification-center-list li')).toHaveCount(0);

  await page.locator('#watch-center-details').evaluate(node => { node.open = true; });
  await page.locator('.watch-settings-panel').evaluate(node => { node.open = true; });
  await page.getByRole('button', { name: 'Enable notifications' }).click();
  await expect(page.locator('#notification-permission-status')).toContainText('Windows and in-app delivery');
  expect(await page.evaluate(() => window.__notificationPermissionRequests)).toBe(1);

  await page.evaluate(change => Promise.all([
    window.__foglightWatchCenter.processChanges([change]),
    window.__foglightWatchCenter.processChanges([change]),
  ]), makeChange(3));
  await expect(page.locator('#notification-center-list li')).toHaveCount(1);
  expect(await page.evaluate(() => window.__systemNotifications.length)).toBe(1);
  await page.evaluate(change => window.__foglightWatchCenter.processChanges([change]), makeChange(3));
  await expect(page.locator('#notification-center-list li')).toHaveCount(1);
  expect(await page.evaluate(() => window.__systemNotifications.length)).toBe(1);

  await page.getByRole('button', { name: 'Acknowledge' }).click();
  await expect(page.locator('#notification-center-list li')).toHaveCount(0);
  await page.evaluate(() => { window.__notificationConstructorFails = true; });
  await page.evaluate(change => window.__foglightWatchCenter.processChanges([change]), makeChange(4));
  await expect(page.locator('#notification-center-list li')).toHaveCount(1);
  expect(await page.evaluate(() => window.__systemNotifications.length)).toBe(1);
  await page.getByRole('button', { name: 'Snooze 1 hour' }).click();
  await expect(page.locator('#notification-center-list li')).toHaveCount(0);
  await page.evaluate(change => window.__foglightWatchCenter.processChanges([change]), makeChange(5));
  await expect(page.locator('#notification-center-list li')).toHaveCount(0);

  await page.clock.setFixedTime(new Date(2026, 6, 10, 23, 0, 0));
  await page.evaluate(change => window.__foglightWatchCenter.processChanges([change]), makeChange(6));
  await expect(page.locator('#notification-center-list li')).toHaveCount(0);
  expect(await page.evaluate(() => window.__systemNotifications.length)).toBe(1);
});

test('[overview wall] cycles without opening the drawer and pauses for keyboard and visibility', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.locator('#watch-center-details').evaluate(node => { node.open = true; });
  await page.getByRole('button', { name: 'Start auto-cycle' }).click();
  await expect(page.locator('#overview-now-list .overview-incident').first()).toHaveClass(/is-selected/);
  await expect(page.locator('#incident-drawer')).toBeHidden();
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.locator('#overview-now-list .overview-incident').nth(1)).toHaveClass(/is-selected/);
  await page.keyboard.press(' ');
  await expect(page.locator('#wall-display-status')).not.toHaveText('Paused.');
  await page.locator('#overview-title').focus();
  await page.keyboard.press(' ');
  await expect(page.locator('#wall-display-status')).toHaveText('Paused.');
  await page.keyboard.press(' ');
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(page.locator('#wall-display-status')).toHaveText('Paused while Foglight is hidden.');
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.keyboard.press('Escape');
  await expect(page.locator('#wall-display-status')).toHaveText('Stopped.');
});

test('[overview wall reduced motion] keeps wall display manual-only', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.locator('#watch-center-details').evaluate(node => { node.open = true; });
  await page.getByRole('button', { name: 'Start auto-cycle' }).click();
  await expect(page.locator('#wall-display-status')).toContainText('Reduced motion is active');
  const selected = page.locator('#overview-now-list .overview-incident.is-selected');
  await expect(selected).toHaveCount(1);
  const firstId = await selected.getAttribute('data-incident-id');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.locator('#overview-now-list .overview-incident.is-selected')).not.toHaveAttribute('data-incident-id', firstId);
});

test('[overview offline history] labels cached revision and source age as not live', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-07-11T03:00:00Z'));
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-history-status')).toContainText('Cached local history — not live.');
  await expect(page.locator('#overview-history-status')).toContainText('Revision 12');
  await expect(page.locator('#overview-history-status')).toContainText('Oldest source cache 2h old.');
  await page.getByText('Source status and freshness').click();
  await expect(page.locator('#overview-source-list')).toContainText('1h old');
  await expect(page.locator('#overview-source-list')).toContainText('2h old');
});

for (const scenario of [
  ['loading', 'loading'],
  ['empty', 'empty'],
  ['partial', 'partial'],
  ['stale', 'stale'],
  ['offline', 'offline'],
  ['first run', 'first_run'],
]) {
  test(`[overview ${scenario[0]}] renders the ${scenario[0]} state`, async ({ page }) => {
    const settingsWrites = [];
    page.on('request', request => {
      if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/settings') {
        settingsWrites.push(request.postDataJSON());
      }
    });
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    if (scenario[0] === 'loading') {
      await expect(page.locator('#overview-surface')).toHaveAttribute('data-view-state', 'loading');
      await expect(page.locator('.overview-skeleton')).toHaveCount(5);
      await expect(page.locator('#overview-surface')).toHaveAttribute('data-view-state', 'ready');
    } else {
      await expect(page.locator('#overview-surface')).toHaveAttribute('data-view-state', scenario[1]);
    }
    if (scenario[0] === 'offline') {
      await expect(page.locator('#overview-history-status')).toContainText(
        'Offline; no retained incident history is available.',
      );
    }
    await expect(page.locator('#overview-state-title')).not.toBeEmpty();
    await expect(page.locator('#overview-health')).not.toBeEmpty();
    if (scenario[0] === 'first run') {
      await expect.poll(() => settingsWrites.some(item => item?.first_run_done === true)).toBe(true);
    }
  });
}

test('[overview] passes accessibility, keyboard, target-size, and reflow checks', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-surface')).toHaveAttribute('data-view-state', 'ready');
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter(item => ['critical', 'serious'].includes(item.impact))).toEqual([]);
  const snapshot = await page.locator('#overview-surface').ariaSnapshot();
  expect(snapshot).toContain('heading "What matters now"');
  expect(snapshot).toContain('heading "Now"');
  expect(snapshot).toContain('application "Incident map');
  expect(snapshot).toContain('button "Global"');
  expect(snapshot).toContain('Source status and freshness');

  await page.getByRole('button', { name: 'Global' }).focus();
  await page.keyboard.press('End');
  await expect(page.getByRole('button', { name: 'Signals' })).toBeFocused();
  await page.keyboard.press('Tab');
  const incidentButton = page.locator('.overview-incident-summary').first();
  await expect(incidentButton).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(incidentButton).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#incident-drawer-body')).toContainText('Fixture');
  const drawerAxe = await new AxeBuilder({ page }).include('#incident-drawer').analyze();
  expect(drawerAxe.violations.filter(item => ['critical', 'serious'].includes(item.impact))).toEqual([]);
  const drawerUndersized = await page.locator(
    '#incident-drawer button, #incident-drawer a[href], #incident-drawer input',
  ).evaluateAll(nodes => nodes.filter(node => {
    if (!node.getClientRects().length) return false;
    const box = node.getBoundingClientRect();
    return box.width < 24 || box.height < 24;
  }).map(node => ({ text: node.textContent.trim(), box: node.getBoundingClientRect().toJSON() })));
  expect(drawerUndersized).toEqual([]);
  await page.evaluate(() => { document.documentElement.style.zoom = '200%'; });
  await expect(page.locator('#incident-drawer-title')).toBeVisible();
  const drawerOverflow = await page.locator('#incident-drawer').evaluate(
    node => node.scrollWidth - node.clientWidth,
  );
  expect(drawerOverflow).toBeLessThanOrEqual(1);
  await page.evaluate(() => { document.documentElement.style.zoom = '100%'; });
  await expect(page.locator('#incident-drawer-title')).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  expect(await page.evaluate(() => document.getElementById('incident-drawer').contains(document.activeElement))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(incidentButton).toBeFocused();

  await page.locator('#watch-center-details').evaluate(node => { node.open = true; });
  await page.locator('.watch-settings-panel').evaluate(node => { node.open = true; });
  const watchAxe = await new AxeBuilder({ page }).include('#watch-center').analyze();
  expect(watchAxe.violations.filter(item => ['critical', 'serious'].includes(item.impact))).toEqual([]);

  const undersized = await page.locator(
    '#top-actions button, #display-modes button, #overview-filters button, .overview-incident-summary, .overview-sources summary, .overview-catalog summary, #overview-catalog-more, #watch-center button, #watch-center input, #watch-center select, #watch-center summary',
  ).evaluateAll(nodes => nodes.filter(node => {
    if (!node.getClientRects().length) return false;
    const box = node.getBoundingClientRect();
    return box.width < 24 || box.height < 24;
  }).map(node => ({ text: node.textContent.trim(), box: node.getBoundingClientRect().toJSON() })));
  expect(undersized).toEqual([]);

  const settingsButton = page.getByRole('button', { name: 'Settings', exact: true });
  await settingsButton.click();
  await expect(page.getByRole('button', { name: 'Close settings' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(settingsButton).toBeFocused();

  await page.evaluate(() => { document.documentElement.style.zoom = '200%'; });
  await expect(page.locator('#overview-title')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test('[overview loading] honors reduced motion', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.overview-skeleton').first()).toBeVisible();
  const duration = await page.locator('.overview-skeleton').first().evaluate(
    node => getComputedStyle(node).animationDuration,
  );
  expect(Number.parseFloat(duration)).toBeLessThanOrEqual(0.001);
});

test('[overview map reduced motion] suppresses change pulses', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.locator('.incident-cluster-icon').focus();
  await page.keyboard.press('Enter');
  await expect.poll(() => page.evaluate(() => window.__foglightMapMetrics.zoom)).toBe(2);
  await page.locator('#overview-map').focus();
  for (let targetZoom = 3; targetZoom <= 6; targetZoom += 1) {
    await page.keyboard.press('+');
    await expect.poll(() => page.evaluate(() => window.__foglightMapMetrics.zoom)).toBe(targetZoom);
  }
  await expect(page.locator('.incident-map-pulse').first()).toBeVisible();
  const duration = await page.locator('.incident-map-pulse').first().evaluate(
    node => getComputedStyle(node).animationDuration,
  );
  expect(Number.parseFloat(duration)).toBeLessThanOrEqual(0.001);
});

test('[overview performance] paints 1,000 fixture incidents within budget', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-surface')).toHaveAttribute('data-view-state', 'ready');
  const metrics = await page.evaluate(() => window.__foglightOverview.metrics);
  expect(metrics.firstIncidentPaintMs).toBeLessThan(2000);
  await expect(page.locator('#overview-count')).toContainText('1000 matching');
  await expect(page.locator('#overview-now-list .overview-incident')).toHaveCount(8);
});

test('[overview catalog] paginates every incident without map interaction', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-surface')).toHaveAttribute('data-view-state', 'ready');
  await page.getByText('Browse all incidents without the map').click();
  await expect(page.locator('#overview-catalog-list .overview-incident')).toHaveCount(3);
  await page.getByRole('button', { name: 'Load more incidents' }).click();
  await expect(page.locator('#overview-catalog-list .overview-incident')).toHaveCount(12);
  await expect(page.locator('#overview-catalog-more')).toBeHidden();
  const last = page.locator('#overview-catalog-list .overview-incident-summary').last();
  await last.focus();
  await last.press('Enter');
  await expect(last).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#overview-catalog-list .incident-evidence').last()).toContainText('Priority');
});

test('[overview onboarding failure] preserves first-run state until a successful load', async ({ page }) => {
  const writes = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/settings') {
      writes.push(request.postDataJSON());
    }
  });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#overview-surface')).toHaveAttribute('data-view-state', 'offline');
  await page.waitForTimeout(400);
  expect(writes.some(item => item?.first_run_done === true)).toBe(false);
});

for (const viewport of [
  { name: 'overview-1500x950.png', width: 1500, height: 950 },
  { name: 'overview-520x900.png', width: 520, height: 900 },
]) {
  test(`[overview] matches ${viewport.width}x${viewport.height} visual baseline`, async ({ page }) => {
    await page.clock.setFixedTime(new Date('2026-07-11T03:00:00Z'));
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#overview-surface')).toHaveAttribute('data-view-state', 'ready');
    await expect(page.locator('#overview-map-status')).toHaveText('Offline world base ready.');
    await expect(page).toHaveScreenshot(viewport.name, {
      fullPage: true,
      animations: 'disabled',
      mask: [page.locator('#clock')],
      maskColor: '#08101f',
    });
  });
}

for (const viewport of [
  { name: 'dashboard-1500x950.png', width: 1500, height: 950 },
  { name: 'dashboard-1280x800.png', width: 1280, height: 800 },
  { name: 'dashboard-900x900.png', width: 900, height: 900 },
  { name: 'dashboard-520x900.png', width: 520, height: 900 },
]) {
  test(`matches ${viewport.width}x${viewport.height} visual baseline`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#body-quakes')).toContainText('Fixture Coast');
    await expect(page.locator('#map-status')).toHaveText('Offline world base ready.');
    await expect(page.locator('#stat-feeds-txt')).toHaveText('15/15 live');
    await expect(page).toHaveScreenshot(viewport.name, {
      fullPage: true,
      animations: 'disabled',
      mask: [page.locator('#clock'), page.locator('#zulu-strip')],
      maskColor: '#08101f',
    });
  });
}

test('renders a provider error without losing the application shell', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#body-quakes')).toContainText('USGS feed unreachable');
  await expect(page.locator('#map')).toHaveClass(/leaflet-container/);
  await expect(page.locator('#action-settings')).toBeVisible();
});
