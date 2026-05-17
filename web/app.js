/* Foglight client v2 --- sitrep-focused dashboard.
 *
 * Layout:
 *   topbar | tickers | { leftRail | map | rightRail } | bottomStrip
 *
 * Left rail (always on):   Quakes, Severe Weather, Conflict Watch
 * Right rail (toggleable): Live TV, Tropical Cyclones, Humanitarian Sitreps
 * Bottom strip:            Bitcoin Pulse + optional (Wiki, GitHub, SEC, HN/Reddit)
 *
 * The internet-pulse panels (Wikipedia, GitHub, SEC, HN/Reddit) are off by
 * default. They are toggleable from Settings. All stream panels autoscroll
 * vertically via CSS animation that pauses on hover.
 */
'use strict';

const API = '';  // same origin

// ---------- helpers ----------
const $  = (id) => document.getElementById(id);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};
const ago = (sec) => {
  if (sec < 60)     return sec + 's';
  if (sec < 3600)   return Math.floor(sec / 60) + 'm';
  if (sec < 86400)  return Math.floor(sec / 3600) + 'h';
  return Math.floor(sec / 86400) + 'd';
};
const fmtTime = (d) =>
  String(d.getUTCHours()).padStart(2, '0') + ':' +
  String(d.getUTCMinutes()).padStart(2, '0');

async function fget(url, opts = {}) {
  const r = await fetch(API + url, { cache: 'no-store', ...opts });
  return r;
}
async function fgetJSON(url) {
  const r = await fget(url);
  const fresh = r.headers.get('X-Foglight-Freshness') || 'unknown';
  let body = null;
  try { body = await r.json(); } catch { body = null; }
  return { body, fresh, status: r.status };
}
function setBadge(id, fresh) {
  const b = $(id);
  if (!b) return;
  b.classList.remove('live', 'cached', 'stale', 'error');
  if (fresh === 'live')        { b.classList.add('live');   b.textContent = 'live'; }
  else if (fresh === 'cached') { b.classList.add('cached'); b.textContent = 'cached'; }
  else if (fresh === 'stale')  { b.classList.add('stale');  b.textContent = 'stale'; }
  else if (fresh === 'error')  { b.classList.add('error');  b.textContent = 'err'; }
  else b.textContent = '...';
}

/* Render rows into a stream container. The container is the `.stream-track`
 * element inside a `.stream-wrap`. We install a JS auto-scroller on the
 * parent wrap (once) that nudges scrollTop forward every tick, pauses on
 * hover, and pauses while the user is actively scrolling --- with PAUSED
 * indicator. */
function fillStream(container, rowsHtml) {
  if (!rowsHtml || !rowsHtml.trim()) {
    container.innerHTML = '<div class="empty">No items.</div>';
    return;
  }
  // Render once; the JS scroller wraps content back to top automatically.
  container.innerHTML = rowsHtml;
  const wrap = container.parentElement;  // .stream-wrap
  if (wrap && !wrap._autoscrollInit) installAutoScroll(wrap);
}

/* JS-driven auto-scroll that respects user interaction.
 *   - Each tick: scrollTop += 1 (pixel-per-tick at ~30fps = 30px/s).
 *   - Hover OR active scrollwheel pauses for N seconds.
 *   - Resumes after the cooldown elapses.
 *   - Loops back to top when reaching the bottom.
 * Different panels run at different speeds via data-speed attribute (px/s).
 */
function installAutoScroll(wrap) {
  if (wrap._autoscrollInit) return;
  wrap._autoscrollInit = true;
  // Default 25 px/s, slower for the dense Wiki panel, etc.
  const px_per_sec = parseFloat(wrap.dataset.speed || '25');
  let lastTs = performance.now();
  let paused = false;
  let resumeAt = 0;

  function tick(now) {
    if (!wrap.isConnected) return;
    const dt = (now - lastTs) / 1000;
    lastTs = now;
    // Resume if cooldown elapsed.
    if (paused && now >= resumeAt) {
      paused = false;
      wrap.classList.remove('user-active');
    }
    if (!paused) {
      const max = wrap.scrollHeight - wrap.clientHeight;
      if (max > 0) {
        wrap.scrollTop += px_per_sec * dt;
        if (wrap.scrollTop >= max) wrap.scrollTop = 0;
      }
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  // Pause on hover.
  wrap.addEventListener('mouseenter', () => {
    paused = true;
    wrap.classList.add('user-active');
  });
  wrap.addEventListener('mouseleave', () => {
    // Small cooldown so it doesn't snap to scrolling the instant the cursor leaves.
    resumeAt = performance.now() + 800;
    // (paused will flip off in tick() once resumeAt is reached)
  });
  // Pause on user-driven scroll. Distinguish from our own programmatic
  // scrolls by checking wheel/touch events.
  let userScrollUntil = 0;
  wrap.addEventListener('wheel', () => {
    paused = true;
    wrap.classList.add('user-active');
    userScrollUntil = performance.now() + 3500;
    resumeAt = userScrollUntil;
  }, { passive: true });
  wrap.addEventListener('touchstart', () => {
    paused = true;
    wrap.classList.add('user-active');
    resumeAt = performance.now() + 4000;
  }, { passive: true });
}

// ---------- top stats ----------
let feedsHealth = { live: 0, errored: 0, cached: 0 };
const FEED_HEALTH_WINDOW = [];

function updateClock() {
  const d = new Date();
  $('clock').textContent =
    String(d.getUTCHours()).padStart(2, '0') + ':' +
    String(d.getUTCMinutes()).padStart(2, '0') + ':' +
    String(d.getUTCSeconds()).padStart(2, '0') + ' UTC';
}
setInterval(updateClock, 1000); updateClock();

function updateFeedsStat() {
  const total = feedsHealth.live + feedsHealth.cached + feedsHealth.errored;
  $('stat-feeds-txt').textContent = total ? `${feedsHealth.live}/${total} live` : 'starting';
  const dot = $('stat-feeds').querySelector('.dot');
  dot.className = 'dot' + (feedsHealth.errored > total / 2 ? ' err'
                         : feedsHealth.errored ? ' stale' : '');
}
function recordFreshness(fresh) {
  FEED_HEALTH_WINDOW.push(fresh);
  while (FEED_HEALTH_WINDOW.length > 80) FEED_HEALTH_WINDOW.shift();
  feedsHealth = { live: 0, errored: 0, cached: 0 };
  for (const f of FEED_HEALTH_WINDOW) {
    if (f === 'live')                                  feedsHealth.live++;
    else if (f === 'cached' || f === 'stale')          feedsHealth.cached++;
    else                                               feedsHealth.errored++;
  }
  updateFeedsStat();
}

// ============================================================
// MAP
// ============================================================

let MAP = null;
let LAYERS = { quakes: null, conflict: null, weather: null, iss: null, cyclones: null, eonet: null, flights: null, firms: null };
let ISS_MARKER = null;

// ============================================================
// THEATER spotlight — zoom presets + filter terms
// ============================================================
const THEATERS = {
  global: { label: 'Global',           view: [25, 20, 3],    kw: null },
  ukr:    { label: 'Ukraine/Russia',   view: [50, 32, 5],    kw: /ukrain|russia|kyiv|kharkiv|donetsk|kherson|moscow|belgorod|kursk/i },
  isr:    { label: 'Israel/Lebanon',   view: [32.5, 35.5, 6],kw: /israel|gaza|lebanon|hezbollah|west bank|jerusalem|rafah|tel aviv|hamas|idf/i },
  sdn:    { label: 'Sudan/Horn',       view: [12, 32, 5],    kw: /sudan|khartoum|darfur|ethiopia|tigray|somalia|south sudan|rsf|eritrea/i },
  tw:     { label: 'Taiwan/SCS',       view: [20, 118, 5],   kw: /taiwan|taipei|china|beijing|south china sea|spratly|paracel|pla|prc/i },
  sahel:  { label: 'Sahel',            view: [15, 0, 5],     kw: /mali|burkina|niger|sahel|nigeria|chad|boko haram|jnim/i },
  mex:    { label: 'US-Mex Border',    view: [27, -101, 5],  kw: /mexic|border|cartel|sinaloa|jalisco|cbp|migrant|tijuana|el paso|rio grande/i },
  kor:    { label: 'Korean Peninsula', view: [38.5, 127, 6],  kw: /korea|pyongyang|seoul|dprk|kim jong/i },
};
let CURRENT_THEATER = 'global';

// EONET category → color + short label. Used both on the map and for the legend.
const EONET_CAT_STYLE = {
  wildfires:           { color: '#ff7a3d', label: 'Wildfire' },
  volcanoes:           { color: '#ff5a4d', label: 'Volcano' },
  severeStorms:        { color: '#5fb8ff', label: 'Severe storm' },
  floods:              { color: '#4a9eff', label: 'Flood' },
  drought:             { color: '#c2a86b', label: 'Drought' },
  earthquakes:         { color: '#ff8a3d', label: 'Earthquake' },
  seaLakeIce:          { color: '#9fd3ff', label: 'Sea/lake ice' },
  snow:                { color: '#dde6f5', label: 'Snow' },
  dustHaze:            { color: '#b9a37e', label: 'Dust/haze' },
  manmade:             { color: '#7d8aa3', label: 'Manmade' },
  tempExtremes:        { color: '#e6b14a', label: 'Temp extremes' },
  waterColor:          { color: '#3dd6c2', label: 'Water color' },
};
function eonetStyle(cats) {
  for (const c of cats || []) {
    if (EONET_CAT_STYLE[c]) return EONET_CAT_STYLE[c];
  }
  return { color: '#7d8aa3', label: (cats && cats[0]) || 'Event' };
}

function initMap() {
  MAP = L.map('map', {
    worldCopyJump: true, zoomControl: true, attributionControl: true,
    minZoom: 2, maxZoom: 8, preferCanvas: true,
  }).setView([25, 20], 3);

  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png', {
    subdomains: 'abcd', maxZoom: 8,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
  }).addTo(MAP);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png', {
    subdomains: 'abcd', maxZoom: 8, opacity: 0.45, attribution: '',
  }).addTo(MAP);

  // Tactical graticule: lat/lon lines every 30 degrees in a desaturated cyan.
  drawGraticule();

  // Layer paint order matters: layers added LATER render on top. The big red
  // conflict markers were drawing over everything else --- moving them to the
  // bottom lets earthquakes / weather / cyclones / ISS show through.
  LAYERS.conflict = L.layerGroup().addTo(MAP);
  LAYERS.weather  = L.layerGroup().addTo(MAP);
  LAYERS.cyclones = L.layerGroup().addTo(MAP);
  LAYERS.eonet    = L.layerGroup().addTo(MAP);
  LAYERS.firms    = L.layerGroup().addTo(MAP);
  LAYERS.flights  = L.layerGroup().addTo(MAP);
  LAYERS.quakes   = L.layerGroup().addTo(MAP);
  LAYERS.iss      = L.layerGroup().addTo(MAP);
  attachMapCoordReadout();
  attachMapClickWeather();

  // Right-click on map → add a labeled pin (intel annotation).
  MAP.on('contextmenu', (e) => {
    const lat = e.latlng.lat;
    const lon = ((e.latlng.lng + 540) % 360) - 180;
    addAnnotation(lat, lon);
  });
  ensureAnnotationLayer();
  redrawAnnotations();

  // Expose for in-app debugging / programmatic interaction (e.g. focus a
  // hotspot from another panel via window.__foglight.focus('Ukraine'))
  window.__foglight = {
    map: MAP,
    layers: LAYERS,
    focus(name) {
      LAYERS.conflict.eachLayer(l => {
        const p = l.getPopup && l.getPopup();
        if (!p) return;
        const html = p.getContent();
        if (typeof html === 'string' && html.toLowerCase().includes(name.toLowerCase())) {
          MAP.setView(l.getLatLng(), Math.max(MAP.getZoom(), 4));
          l.openPopup();
        }
      });
    },
  };
}

// Tactical status counts displayed in the top status strip. Each setStatus
// call also computes a delta vs the previous sample, surfacing change-of-
// state ("▲ 3" when conflict wire jumped).
const STATUS      = { hotspots: 0, wxalerts: 0, quakes: 0, cyclones: 0, conflict: 0, relief: 0, gdacs: 0 };
const STATUS_PREV = {};  // populated by setStatus on second+ call
function setStatus(key, val, severity) {
  const prev = STATUS[key];
  STATUS[key] = val;
  const el = document.getElementById('stat-' + key);
  if (!el) return;
  el.textContent = String(val);
  el.classList.remove('hot', 'warn', 'good');
  if (severity) el.classList.add(severity);
  // Delta arrow (only if we've seen this stat before AND it changed).
  const dlt = document.getElementById('dlt-' + key);
  if (dlt && STATUS_PREV[key] != null && STATUS_PREV[key] !== val) {
    const diff = val - STATUS_PREV[key];
    dlt.className = 'delta ' + (diff > 0 ? 'up' : 'down');
    dlt.textContent = (diff > 0 ? '▲' : '▼') + Math.abs(diff);
    // Fade out after a few minutes.
    clearTimeout(dlt._fade);
    dlt._fade = setTimeout(() => { dlt.textContent = ''; dlt.className = 'delta'; }, 300000);
  }
  STATUS_PREV[key] = val;
}

// ============================================================
// WORLD CAPITALS clock strip
// ============================================================
const CAPITALS = [
  { tag: 'WAS', tz: 'America/New_York' },
  { tag: 'MOW', tz: 'Europe/Moscow' },
  { tag: 'PEK', tz: 'Asia/Shanghai' },
  { tag: 'JER', tz: 'Asia/Jerusalem' },
  { tag: 'KYV', tz: 'Europe/Kyiv' },
  { tag: 'TPE', tz: 'Asia/Taipei' },
];
function updateCapitalClocks() {
  const strip = document.getElementById('zulu-strip');
  if (!strip) return;
  if (!strip.children.length) {
    strip.innerHTML = CAPITALS.map(c =>
      `<div class="city"><b id="cap-${c.tag}">--:--</b><span>${c.tag}</span></div>`
    ).join('');
  }
  for (const c of CAPITALS) {
    try {
      const t = new Date().toLocaleTimeString('en-GB', {
        timeZone: c.tz, hour: '2-digit', minute: '2-digit', hour12: false,
      });
      const el = document.getElementById('cap-' + c.tag);
      if (el) el.textContent = t;
    } catch {}
  }
}
setInterval(updateCapitalClocks, 30000);

// ============================================================
// THEATER switching
// ============================================================
function switchTheater(id) {
  const t = THEATERS[id];
  if (!t) return;
  CURRENT_THEATER = id;
  document.querySelectorAll('#theaterbar .t-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.theater === id));
  if (MAP) MAP.setView([t.view[0], t.view[1]], t.view[2]);
  // Re-render conflict + sitreps + GDACS with the theater filter applied.
  refreshConflict();
  refreshRelief();
  refreshCyclones();  // includes GDACS section
}

function inTheater(text) {
  const t = THEATERS[CURRENT_THEATER];
  if (!t || !t.kw) return true;  // global → no filter
  return t.kw.test(text || '');
}

// ============================================================
// BREAKING-EVENT banner (M≥6 quake, red GDACS, etc.)
// ============================================================
let LAST_BREAKING = 0;
function fireBreaking(text) {
  const now = Date.now();
  if (now - LAST_BREAKING < 60000) return;  // dedupe within 1 min
  LAST_BREAKING = now;
  const b = document.getElementById('breaking-banner');
  b.textContent = '⚠ BREAKING — ' + text;
  b.classList.add('show');
  b.onclick = () => b.classList.remove('show');
  // Auto-dismiss after 18 seconds.
  setTimeout(() => b.classList.remove('show'), 18000);
}

// ============================================================
// BRIEFING export — print-ready HTML
// ============================================================
function generateBriefing() {
  const now = new Date();
  const ts = now.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
  // Pull current displayed data from the cached state.
  const hotspotsHtml = (FG_LAST_HOTSPOTS || []).slice(0, 10).map(p =>
    `<li><b>${escapeHtml(p.name)}</b> &mdash; ${p.count} mentions (score ${p.score})<br>` +
    `<small>${escapeHtml(p.latest || '')}</small></li>`).join('');
  const conflictHtml = (FG_LAST_CONFLICT || []).slice(0, 12).map(a =>
    `<li>${a.ts ? '<small>' + new Date(a.ts*1000).toISOString().slice(11,16) + 'Z</small> ' : ''}<b>${escapeHtml(a.src)}</b> &mdash; ${escapeHtml(a.title)}</li>`).join('');
  const quakesBig = (LAST_USGS && LAST_USGS.features)
    ? LAST_USGS.features.filter(f => (f.properties.mag || 0) >= 5)
        .sort((a,b)=>b.properties.time-a.properties.time).slice(0, 10)
    : [];
  const quakesHtml = quakesBig.map(f =>
    `<li><b>M ${f.properties.mag.toFixed(1)}</b> &mdash; ${escapeHtml(f.properties.place||'?')}<br><small>${new Date(f.properties.time).toISOString().slice(0,16).replace('T',' ')} UTC</small></li>`).join('');
  const wxHtml = (FG_LAST_WX || []).slice(0, 8).map(p =>
    `<li><b>${escapeHtml(p.event||'Alert')}</b> [${escapeHtml(p.severity||'?')}] &mdash; ${escapeHtml((p.areaDesc||'').slice(0,140))}</li>`).join('');
  const reliefHtml = (FG_LAST_RELIEF || []).slice(0, 8).map(it =>
    `<li>${it.ts ? '<small>' + new Date(it.ts*1000).toISOString().slice(0,10) + '</small> ' : ''}${escapeHtml(it.title||'')}</li>`).join('');
  const gdacsHtml = (GDACS_CACHE || []).filter(d => {
    const a = (d.alert||'').toLowerCase(); return a === 'red' || a === 'orange';
  }).slice(0, 10).map(d =>
    `<li>[<b>${escapeHtml(d.alert)}</b>] ${escapeHtml(d.country||'')} &mdash; ${escapeHtml(d.title||'')}</li>`).join('');

  const html = `<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Foglight Sitrep ${ts}</title>
<style>
  body { font: 13px/1.5 -apple-system, Segoe UI, Arial, sans-serif; max-width: 760px; margin: 0 auto; padding: 24px 32px; color: #222; }
  h1 { font-size: 22px; margin: 0; color: #000; letter-spacing: 0.04em; }
  .meta { color: #666; font: 11px monospace; margin: 4px 0 22px; letter-spacing: 0.1em; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.14em; margin: 22px 0 6px; color: #000; border-bottom: 1px solid #999; padding-bottom: 3px; }
  ul { margin: 4px 0 0 0; padding-left: 22px; }
  li { margin-bottom: 6px; }
  li small { color: #666; }
  .summary { background: #f4f4f4; padding: 10px 14px; border-left: 4px solid #444; margin: 10px 0 16px; font-size: 12px; }
  .empty { color: #aaa; font-style: italic; }
  .print-bar { position: sticky; top: 0; background: #fff; padding: 8px 0; border-bottom: 1px solid #ddd; margin: -24px -32px 16px; padding: 12px 32px; }
  .print-bar button { padding: 6px 14px; font: 11px monospace; letter-spacing: 0.08em; text-transform: uppercase; cursor: pointer; }
  @media print { .print-bar { display: none; } }
</style></head><body>
<div class="print-bar">
  <button onclick="window.print()">Print / save as PDF</button>
  <button onclick="window.close()">Close</button>
</div>
<h1>Foglight Situation Report</h1>
<div class="meta">As of ${ts} &middot; theater: ${escapeHtml((THEATERS[CURRENT_THEATER]||{}).label || '?')}</div>

<div class="summary">
  <b>Top-line:</b> ${STATUS.hotspots} active conflict hotspots &middot;
  ${STATUS.gdacs} red/orange GDACS alerts &middot;
  ${STATUS.quakes} significant seismic events (≥M4) &middot;
  ${STATUS.cyclones} tropical cyclones &middot;
  ${STATUS.conflict} conflict-wire items in last 24h.
</div>

<h2>Conflict Hotspots</h2>
<ul>${hotspotsHtml || '<li class="empty">No hotspots scored.</li>'}</ul>

<h2>Conflict Wire (latest)</h2>
<ul>${conflictHtml || '<li class="empty">No items.</li>'}</ul>

<h2>Significant Earthquakes (M≥5, 24h)</h2>
<ul>${quakesHtml || '<li class="empty">None.</li>'}</ul>

<h2>US Severe Weather (active)</h2>
<ul>${wxHtml || '<li class="empty">No active alerts.</li>'}</ul>

<h2>Humanitarian Sitreps</h2>
<ul>${reliefHtml || '<li class="empty">No reports.</li>'}</ul>

<h2>GDACS High-Severity</h2>
<ul>${gdacsHtml || '<li class="empty">No red/orange alerts.</li>'}</ul>

<div class="meta" style="margin-top: 36px; border-top: 1px solid #ccc; padding-top: 8px;">
  Generated by Foglight &middot; open-source civilian sitrep dashboard &middot; data from GDELT, USGS, NWS, NOAA NHC, ReliefWeb, GDACS, EONET.
</div>
</body></html>`;
  const w = window.open('', '_blank', 'width=820,height=900');
  if (!w) {
    // Popup blocked; fall back to in-app overlay.
    const blob = new Blob([html], { type: 'text/html' });
    location.href = URL.createObjectURL(blob);
    return;
  }
  w.document.write(html);
  w.document.close();
}

// Caches the renderers feed for the briefing export.
let FG_LAST_HOTSPOTS = [];
let FG_LAST_CONFLICT = [];
let FG_LAST_RELIEF   = [];
let FG_LAST_WX       = [];

// ============================================================
// WATCHLIST — keyword alerts across streams
// ============================================================
let WATCHLIST = [];
let WATCHLIST_SEEN = new Set();   // titles we've already alerted on
function matchesWatchlist(text) {
  if (!WATCHLIST.length || !text) return false;
  const lower = text.toLowerCase();
  return WATCHLIST.some(kw => kw && lower.includes(kw.toLowerCase()));
}
function checkAndAlert(items, srcLabel) {
  // For new items matching watchlist, fire breaking banner (deduped).
  for (const it of items) {
    const text = (it.title || '') + ' ' + (it.summary || '');
    if (!matchesWatchlist(text)) continue;
    const key = srcLabel + '|' + (it.title || '').slice(0, 80);
    if (WATCHLIST_SEEN.has(key)) continue;
    WATCHLIST_SEEN.add(key);
    if (WATCHLIST_SEEN.size > 1) {  // skip first-launch flood
      fireBreaking('WATCHLIST HIT — ' + srcLabel + ' — ' + (it.title || '').slice(0, 80));
    }
  }
}

async function saveWatchlist() {
  const text = $('watchlist-text').value;
  const kws = text.split('\n').map(s => s.trim()).filter(Boolean);
  WATCHLIST = kws;
  WATCHLIST_SEEN.clear();  // reset so re-saves can re-alert
  await fget('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ watchlist: kws }),
  });
  // Re-render streams to apply highlight.
  refreshConflict(); refreshRelief();
}

// ============================================================
// MAP ANNOTATIONS — user-pinned points
// ============================================================
let ANNOTATIONS = [];          // {lat, lon, label}
let ANNOTATION_LAYER = null;

function ensureAnnotationLayer() {
  if (!ANNOTATION_LAYER && MAP) {
    ANNOTATION_LAYER = L.layerGroup().addTo(MAP);
  }
}

function redrawAnnotations() {
  ensureAnnotationLayer();
  if (!ANNOTATION_LAYER) return;
  ANNOTATION_LAYER.clearLayers();
  for (let i = 0; i < ANNOTATIONS.length; i++) {
    const a = ANNOTATIONS[i];
    const icon = L.divIcon({
      className: '',
      iconSize: [16, 16],
      html: `<div class="anno-pin"></div>`,
    });
    const m = L.marker([a.lat, a.lon], { icon });
    m.bindTooltip(a.label || 'Pinned', { permanent: false, direction: 'top' });
    m.bindPopup(
      `<div style="font:11px 'JetBrains Mono',monospace">` +
      `<b style="color:#f7931a;text-transform:uppercase;letter-spacing:0.06em">${escapeHtml(a.label || 'Pinned')}</b><br>` +
      `<span style="color:#7e8aa3">${a.lat.toFixed(2)}°, ${a.lon.toFixed(2)}°</span><br><br>` +
      `<button onclick="removeAnnotation(${i})" style="background:var(--hot);color:#fff;border:0;padding:4px 8px;cursor:pointer;font:10px monospace;text-transform:uppercase">Remove pin</button>` +
      `</div>`
    );
    m.on('click', e => L.DomEvent.stopPropagation(e));
    m.addTo(ANNOTATION_LAYER);
  }
  renderAnnotationList();
}

function renderAnnotationList() {
  const wrap = $('annotation-list');
  if (!wrap) return;
  if (!ANNOTATIONS.length) {
    wrap.innerHTML = '<div style="color:var(--text-dimmer);font-size:11px;font-style:italic;padding:6px 0">No pins yet. Right-click anywhere on the world map to add one.</div>';
    return;
  }
  wrap.innerHTML = ANNOTATIONS.map((a, i) =>
    `<div class="anno"><span class="dot"></span>` +
    `<span class="lbl">${escapeHtml(a.label || 'Pinned')}</span>` +
    `<span class="coord">${a.lat.toFixed(2)}°, ${a.lon.toFixed(2)}°</span>` +
    `<button onclick="removeAnnotation(${i})">remove</button></div>`
  ).join('');
}

async function addAnnotation(lat, lon) {
  const label = prompt('Pin label (intel note):', '');
  if (label == null) return;  // cancelled
  ANNOTATIONS.push({ lat, lon, label: label.trim() || 'Pinned' });
  await persistAnnotations();
  redrawAnnotations();
}

async function removeAnnotation(idx) {
  ANNOTATIONS.splice(idx, 1);
  await persistAnnotations();
  redrawAnnotations();
  MAP && MAP.closePopup();
}

async function clearAllAnnotations() {
  if (!confirm('Remove all map pins?')) return;
  ANNOTATIONS = [];
  await persistAnnotations();
  redrawAnnotations();
}

async function persistAnnotations() {
  await fget('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ annotations: ANNOTATIONS }),
  });
}

window.removeAnnotation    = removeAnnotation;
window.saveWatchlist       = saveWatchlist;
window.clearAllAnnotations = clearAllAnnotations;

// ============================================================
// TTS — voice briefing
// ============================================================
let TTS_UTTERANCE = null;
function speakBriefing() {
  // Cancel previous if user clicks again.
  if (TTS_UTTERANCE && window.speechSynthesis.speaking) {
    window.speechSynthesis.cancel();
    TTS_UTTERANCE = null;
    return;
  }
  const lines = [
    `Foglight situation briefing at ${new Date().toUTCString()}.`,
    `${STATUS.hotspots} active conflict hotspots.`,
    `${STATUS.gdacs} red or orange GDACS disaster alerts.`,
    `${STATUS.quakes} significant earthquakes magnitude four or greater.`,
    `${STATUS.cyclones} active tropical cyclones.`,
    `${STATUS.wxalerts} active US severe weather alerts.`,
  ];
  // Top 3 conflict articles.
  for (const a of (FG_LAST_CONFLICT || []).slice(0, 3)) {
    lines.push(`From ${a.src}: ${a.title}.`);
  }
  // Top 3 hotspots by score.
  for (const h of (FG_LAST_HOTSPOTS || []).slice(0, 3)) {
    lines.push(`${h.name}: ${h.count} mentions, score ${h.score}.`);
  }
  lines.push('End of briefing.');
  TTS_UTTERANCE = new SpeechSynthesisUtterance(lines.join(' '));
  TTS_UTTERANCE.rate = 1.05;
  TTS_UTTERANCE.pitch = 0.95;
  TTS_UTTERANCE.volume = 0.95;
  window.speechSynthesis.speak(TTS_UTTERANCE);
}
window.speakBriefing = speakBriefing;

// ============================================================
// FULLSCREEN
// ============================================================
function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(() => {});
  } else {
    document.exitFullscreen().catch(() => {});
  }
}
window.toggleFullscreen = toggleFullscreen;
document.addEventListener('keydown', (e) => {
  // F toggles fullscreen unless user is typing in an input.
  if (e.key !== 'f' && e.key !== 'F') return;
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
  toggleFullscreen();
});

// ============================================================
// DEFENSE WIRE (military / strategic-analysis RSS)
//   Items are merged INTO the Conflict Wire stream on its next refresh,
//   so users see DOD / NATO / CISA / Stripes / War on the Rocks alongside
//   UN / DW / France 24 — one unified intel firehose.
// ============================================================
let LAST_DEFENSE = [];
async function refreshDefense() {
  const { body, fresh } = await fgetJSON('/api/defense-wire');
  recordFreshness(fresh);
  if (!body || !body.articles) return;
  LAST_DEFENSE = body.articles;
  checkAndAlert(LAST_DEFENSE, 'DEFENSE');
  // Trigger a conflict-wire re-render so the merged feed shows.
  refreshConflict();
}

// ============================================================
// COMMODITIES
// ============================================================
let LAST_COMMODITIES = null;
async function refreshCommodities() {
  const { body, fresh } = await fgetJSON('/api/commodities');
  recordFreshness(fresh);
  if (body && body.items) LAST_COMMODITIES = body.items;
}

function drawGraticule() {
  const style = { color: '#1c2336', weight: 0.6, opacity: 0.85, interactive: false };
  for (let lat = -60; lat <= 60; lat += 30) {
    L.polyline([[lat, -180], [lat, 180]], style).addTo(MAP);
  }
  for (let lon = -180; lon <= 180; lon += 30) {
    L.polyline([[-85, lon], [85, lon]], style).addTo(MAP);
  }
  L.polyline([[0, -180], [0, 180]],
    { color: '#26324a', weight: 0.9, opacity: 1, interactive: false }).addTo(MAP);
}

// Click on an empty patch of the world map → fetch Open-Meteo current
// conditions for that lat/lon and pop a Leaflet popup with the data.
// Clicks on existing markers are handled by Leaflet's normal binding flow.
function attachMapClickWeather() {
  MAP.on('click', async (e) => {
    const lat = e.latlng.lat;
    const lon = ((e.latlng.lng + 540) % 360) - 180;
    if (lat < -85 || lat > 85) return;
    // Show a lightweight loading popup immediately --- replaced with real
    // data when the fetch resolves.
    const popup = L.popup({ maxWidth: 320, minWidth: 240, className: 'wx-popup-wrap' })
      .setLatLng(e.latlng)
      .setContent(`<div class="wx-popup"><div class="wx-loading">fetching weather…</div></div>`)
      .openOn(MAP);
    try {
      const r = await fget(`/api/openmeteo?lat=${lat.toFixed(2)}&lon=${lon.toFixed(2)}`);
      const data = await r.json();
      popup.setContent(renderWeatherPopup(lat, lon, data));
    } catch (err) {
      popup.setContent(`<div class="wx-popup">Weather fetch failed: ${escapeHtml(err.message)}</div>`);
    }
  });
}

const WX_CODE = {
  0: 'Clear', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast',
  45: 'Fog', 48: 'Rime fog',
  51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle',
  56: 'Freezing drizzle', 57: 'Freezing drizzle',
  61: 'Light rain', 63: 'Rain', 65: 'Heavy rain',
  66: 'Freezing rain', 67: 'Freezing rain',
  71: 'Light snow', 73: 'Snow', 75: 'Heavy snow',
  77: 'Snow grains',
  80: 'Rain showers', 81: 'Rain showers', 82: 'Violent rain',
  85: 'Snow showers', 86: 'Snow showers',
  95: 'Thunderstorm', 96: 'Thunderstorm + hail', 99: 'Thunderstorm + hail',
};
function compassPoint(deg) {
  const dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
  return dirs[Math.round(((deg % 360) / 22.5)) % 16];
}
function renderWeatherPopup(lat, lon, data) {
  const c = data && data.current;
  if (!c) return `<div class="wx-popup">No data.</div>`;
  const ns = lat >= 0 ? 'N' : 'S', ew = lon >= 0 ? 'E' : 'W';
  const coord = `${Math.abs(lat).toFixed(2)}°${ns} · ${Math.abs(lon).toFixed(2)}°${ew}`;
  const cond = WX_CODE[c.weather_code] || ('Code ' + c.weather_code);
  const windKt = Math.round((c.wind_speed_10m || 0) * 0.539957);  // km/h → kt
  const hourly = (data.hourly && data.hourly.temperature_2m) || [];
  // Build a tiny 6-hour temp strip.
  let strip = '';
  for (let i = 0; i < Math.min(6, hourly.length); i++) {
    strip += `<span class="hr"><b>${Math.round(hourly[i])}°</b><i>+${i+1}h</i></span>`;
  }
  return `
    <div class="wx-popup">
      <div class="wx-coord">${escapeHtml(coord)}</div>
      <div class="wx-temp">${Math.round(c.temperature_2m)}°C <span class="wx-cond">${escapeHtml(cond)}</span></div>
      <div class="wx-meta">
        <span><span class="lab">Feels</span> ${Math.round(c.apparent_temperature)}°</span>
        <span><span class="lab">Humidity</span> ${Math.round(c.relative_humidity_2m)}%</span>
        <span><span class="lab">Wind</span> ${windKt} kt ${compassPoint(c.wind_direction_10m || 0)}</span>
        <span><span class="lab">Cloud</span> ${Math.round(c.cloud_cover || 0)}%</span>
        <span><span class="lab">Pressure</span> ${Math.round(c.pressure_msl || 0)} hPa</span>
      </div>
      ${strip ? `<div class="wx-strip">${strip}</div>` : ''}
    </div>
  `;
}

// Tactical lat/lon readout that follows the cursor.
function attachMapCoordReadout() {
  const out = $('map-coord');
  MAP.on('mousemove', (e) => {
    const lat = e.latlng.lat;
    const lon = ((e.latlng.lng + 540) % 360) - 180;  // normalize wrap
    const ns = lat >= 0 ? 'N' : 'S';
    const ew = lon >= 0 ? 'E' : 'W';
    out.textContent =
      Math.abs(lat).toFixed(2).padStart(5, '0') + '° ' + ns + ' · ' +
      Math.abs(lon).toFixed(2).padStart(6, '0') + '° ' + ew;
  });
  MAP.on('mouseout', () => { out.textContent = '-- · --'; });
}

function pulseIcon(kind, sizePx) {
  const s = sizePx || 12;
  return L.divIcon({
    className: '',
    iconSize: [s, s],
    html: `<div style="width:${s}px;height:${s}px;border-radius:50%;background:${
      kind === 'iss' ? 'var(--iss)' : kind === 'storm' ? 'var(--storm)' : 'var(--quake)'
    };box-shadow:0 0 0 0 currentColor;animation:lamp 1.6s ease-in-out infinite"></div>`,
  });
}

// ============================================================
// EARTHQUAKES (USGS)
// ============================================================

let LAST_QUAKE_IDS = new Set();
let LAST_USGS = null;  // shared by Earthquakes panel + Major Hazards "significant"

async function refreshQuakes() {
  const { body, fresh } = await fgetJSON('/api/usgs?window=day');
  setBadge('bd-quakes', fresh);
  recordFreshness(fresh);
  if (!body || !body.features) {
    // Don't blank the panel on transient errors --- keep whatever was there.
    if (fresh === 'error' && !$('body-quakes').firstChild) {
      $('body-quakes').innerHTML = '<div class="empty"><b>USGS feed unreachable.</b> Retrying shortly.</div>';
    }
    return;
  }
  LAST_USGS = body;

  const feats = body.features.slice().sort((a, b) => b.properties.time - a.properties.time);
  const m4plus = feats.filter(f => (f.properties.mag || 0) >= 4).length;
  setStatus('quakes', m4plus, m4plus > 20 ? 'hot' : m4plus > 8 ? 'warn' : null);

  const out = $('body-quakes');
  out.innerHTML = '';
  const top = feats.slice(0, 30);
  if (!top.length) { out.innerHTML = '<div class="empty">No recent earthquakes.</div>'; return; }
  for (const f of top) {
    const p = f.properties;
    const mag = p.mag;
    const magCls = mag >= 6 ? 'm4' : mag >= 5 ? 'm3' : mag >= 4 ? 'm2' : 'm1';
    const dt = new Date(p.time);
    const row = el('div', 'row clickable');
    row.appendChild(el('div', 'when', fmtTime(dt)));
    const lbl = el('div', 'label');
    lbl.appendChild(el('span', 'title', p.place || 'Unknown'));
    const depth = (f.geometry && f.geometry.coordinates && f.geometry.coordinates[2]) || 0;
    lbl.appendChild(el('span', 'sub',
      `depth ${Math.round(depth)} km${p.tsunami ? ' • tsunami flag' : ''}`));
    row.appendChild(lbl);
    const m = el('div'); m.appendChild(el('span', 'mag ' + magCls, mag ? mag.toFixed(1) : '?'));
    row.appendChild(m);
    if (p.url) {
      row.addEventListener('click', () => window.open(p.url, '_blank', 'noopener'));
    }
    out.appendChild(row);
  }

  // Map markers
  LAYERS.quakes.clearLayers();
  const newIds = new Set();
  for (const f of feats) {
    if (!f.geometry || !f.geometry.coordinates) continue;
    const [lon, lat] = f.geometry.coordinates;
    const mag = f.properties.mag || 0;
    if (mag < 2) continue;
    L.circleMarker([lat, lon], {
      radius: Math.max(3, mag * 2), color: '#ff8a3d',
      fillColor: '#ff8a3d', fillOpacity: 0.55, weight: 1,
      bubblingMouseEvents: false,
    }).bindPopup(`<b>M ${mag.toFixed(1)}</b><br>${f.properties.place}<br>` +
                 `<small>${new Date(f.properties.time).toISOString().replace('T',' ').slice(0,16)} UTC</small>` +
                 (f.properties.url ? `<br><a href="${f.properties.url}" target="_blank">USGS &rarr;</a>` : ''))
      .addTo(LAYERS.quakes);
    newIds.add(f.id);
  }
  for (const f of feats) {
    if (f.properties.mag >= 4 && !LAST_QUAKE_IDS.has(f.id)) {
      playEarthquakeCue(f.properties.mag);
      if (f.properties.mag >= 6) {
        fireBreaking(`M ${f.properties.mag.toFixed(1)} earthquake — ${f.properties.place || 'location unknown'}`);
      }
      break;
    }
  }
  LAST_QUAKE_IDS = newIds;
}

// ============================================================
// GDELT geo + conflict watch
// ============================================================

// ============================================================
// FLIGHTS (adsb.lol, free, no key)
// ============================================================
async function refreshFlights() {
  // Query around the current map center, scaled by zoom.
  if (!MAP) return;
  const c = MAP.getCenter();
  const z = MAP.getZoom();
  const dist = z >= 6 ? 100 : z >= 5 ? 180 : z >= 4 ? 250 : 250;
  const { body, fresh } = await fgetJSON(`/api/flights?lat=${c.lat.toFixed(2)}&lon=${c.lng.toFixed(2)}&dist=${dist}`);
  recordFreshness(fresh);
  if (!body) return;
  const ac = body.ac || body.aircraft || [];
  LAYERS.flights.clearLayers();
  for (const a of ac.slice(0, 250)) {
    if (a.lat == null || a.lon == null) continue;
    const heading = a.track != null ? a.track : (a.true_heading != null ? a.true_heading : 0);
    const callsign = (a.flight || a.r || '').trim();
    const alt = a.alt_baro != null ? a.alt_baro : (a.alt_geom != null ? a.alt_geom : '?');
    const spd = a.gs != null ? Math.round(a.gs) : '?';
    // Tiny rotated triangle. Use a divIcon so we can rotate it.
    const icon = L.divIcon({
      className: '',
      iconSize: [14, 14],
      html: `<div style="transform:rotate(${heading}deg);color:#82e0ff;font-size:13px;line-height:1;pointer-events:none">▲</div>`,
    });
    const m = L.marker([a.lat, a.lon], { icon, interactive: true });
    m.on('click', e => L.DomEvent.stopPropagation(e));
    m.bindPopup(`<b>${escapeHtml(callsign || '?')}</b><br>` +
                `alt ${alt} ft &middot; gs ${spd} kt &middot; hdg ${Math.round(heading)}°<br>` +
                `<small>${escapeHtml((a.t || '') + ' ' + (a.r || ''))}</small>`);
    m.addTo(LAYERS.flights);
  }
}

// ============================================================
// FIRMS WILDFIRES (BYOK; renders nothing if no key)
// ============================================================
let LAST_FIRMS_FETCH = 0;
async function refreshFirms() {
  // Throttle --- FIRMS is expensive and updates only every 3 hours.
  if (Date.now() - LAST_FIRMS_FETCH < 900000) return;
  LAST_FIRMS_FETCH = Date.now();
  const { body, fresh } = await fgetJSON('/api/firms');
  if (!body || !body.items) { LAYERS.firms.clearLayers(); return; }
  recordFreshness(fresh);
  LAYERS.firms.clearLayers();
  for (const f of body.items) {
    L.circleMarker([f.lat, f.lon], {
      radius: f.frp > 50 ? 4 : 2.5,
      color: '#ff7a3d', fillColor: '#ffae6f',
      fillOpacity: 0.7, weight: 0,
      bubblingMouseEvents: false,
    }).bindPopup(`<b>Active fire</b><br>FRP ${f.frp.toFixed(1)} MW<br><small>${escapeHtml(f.ts || '')}</small>`)
      .addTo(LAYERS.firms);
  }
}

// ============================================================
// EONET (NASA Natural Events Tracker) --- map overlay
// ============================================================

// ============================================================
// GDACS disaster wire (rendered inside the Major Hazards panel)
// ============================================================

const GDACS_TYPE = { EQ:'Quake', TC:'Cyclone', FL:'Flood', VO:'Volcano',
                     WF:'Wildfire', DR:'Drought', TS:'Tsunami' };
let GDACS_CACHE = [];
let GDACS_KNOWN_REDS = new Set();

async function refreshGdacs() {
  const { body, fresh } = await fgetJSON('/api/gdacs');
  recordFreshness(fresh);
  if (!body || !body.items) return;
  GDACS_CACHE = body.items;
  // Status bar count: orange + red alerts only (skip green = informational).
  const hot = GDACS_CACHE.filter(d =>
    (d.alert || '').toLowerCase() === 'red' ||
    (d.alert || '').toLowerCase() === 'orange').length;
  setStatus('gdacs', hot, hot > 5 ? 'hot' : hot > 0 ? 'warn' : 'good');
  // Fire breaking banner for new RED alerts (track which we've seen).
  for (const d of GDACS_CACHE) {
    if ((d.alert || '').toLowerCase() !== 'red') continue;
    const key = (d.title || '') + '|' + (d.ts || '');
    if (GDACS_KNOWN_REDS.has(key)) continue;
    GDACS_KNOWN_REDS.add(key);
    // Skip on initial load to avoid stale-data flood.
    if (GDACS_KNOWN_REDS.size > 1) {
      fireBreaking('RED ALERT — ' + (d.country || '') + ' — ' + (d.title || '').slice(0, 80));
    }
  }
  // Re-render Major Hazards (which integrates GDACS).
  refreshCyclones();
}

function gdacsRowHtml(d) {
  const cls = (d.alert || 'green').toLowerCase();
  const t = GDACS_TYPE[d.etype] || d.etype || d.title.slice(0, 6);
  const country = (d.country || '').slice(0, 18);
  const title = (d.title || '').replace(/^[A-Za-z ]+alert:\s*/i, '').slice(0, 80);
  const href = d.link ? ` href="${escapeHtml(d.link)}" target="_blank" rel="noopener noreferrer"` : '';
  const tag = d.link ? 'a' : 'div';
  return `<${tag} class="gdacs-row ${cls}"${href}>
    <div class="sev"></div>
    <div class="meta">${escapeHtml(t)}</div>
    <div class="name">${escapeHtml(country ? `[${country}] ` : '')}${escapeHtml(title)}</div>
  </${tag}>`;
}

// ============================================================
// NOAA Space Weather (K-index status indicator)
// ============================================================

async function refreshSpaceWeather() {
  const { body, fresh } = await fgetJSON('/api/space-weather');
  recordFreshness(fresh);
  // NOAA SWPC returns either array-of-arrays (older format with header row)
  // or array-of-objects {time_tag, Kp, ...}. Support both.
  let kp = null;
  if (Array.isArray(body)) {
    for (let i = body.length - 1; i >= 0; i--) {
      const row = body[i];
      let v = NaN;
      if (Array.isArray(row))       v = parseFloat(row[1]);
      else if (row && typeof row === 'object') v = parseFloat(row.Kp != null ? row.Kp : row.kp);
      if (!isNaN(v)) { kp = v; break; }
    }
  }
  const el = $('stat-kp');
  if (kp == null) {
    el.textContent = '--';
    return;
  }
  // Severity: 0-3 quiet (good), 4 unsettled, 5-6 G1-G2 (warn), ≥7 G3+ (hot).
  let sev = null;
  if (kp >= 7) sev = 'hot';
  else if (kp >= 5) sev = 'warn';
  else if (kp >= 4) sev = 'warn';
  else sev = 'good';
  el.className = 'val ' + sev;
  el.textContent = kp.toFixed(1);
}

async function refreshEonet() {
  const { body, fresh } = await fgetJSON('/api/eonet');
  recordFreshness(fresh);
  if (!body || !body.events) return;
  LAYERS.eonet.clearLayers();
  for (const ev of body.events) {
    const s = eonetStyle(ev.cats);
    const radius = 4 + (ev.cats && ev.cats.includes('wildfires') ? 1 : 0);
    L.circleMarker([ev.lat, ev.lon], {
      radius, color: s.color, fillColor: s.color,
      fillOpacity: 0.55, weight: 1,
      bubblingMouseEvents: false,
    }).bindPopup(`
      <div style="min-width:200px;max-width:320px">
        <b style="color:${s.color};text-transform:uppercase;letter-spacing:0.06em;font-size:11px">${escapeHtml(s.label)}</b><br>
        <span style="font-size:11.5px">${escapeHtml(ev.title || '')}</span><br>
        <small style="color:#7e8aa3">${ev.date ? escapeHtml(ev.date.slice(0,16).replace('T',' ') + ' UTC') : ''}</small>
        ${ev.link ? `<br><a href="${escapeHtml(ev.link)}" target="_blank" rel="noopener noreferrer" style="font-size:11px">Source →</a>` : ''}
      </div>
    `).addTo(LAYERS.eonet);
  }
}

// ============================================================
// CONFLICT HOTSPOTS (map overlay)
// ============================================================

async function refreshConflictHotspots() {
  const { body, fresh } = await fgetJSON('/api/conflict-hotspots');
  recordFreshness(fresh);
  if (!body || !body.features) return;
  setStatus('hotspots', body.features.length, body.features.length > 10 ? 'hot' : body.features.length > 3 ? 'warn' : null);
  FG_LAST_HOTSPOTS = body.features.map(f => f.properties);
  LAYERS.conflict.clearLayers();
  // Sort by score so the biggest markers are drawn last (on top).
  const feats = body.features.slice().sort((a, b) =>
    a.properties.score - b.properties.score);
  for (const f of feats) {
    const [lon, lat] = f.geometry.coordinates;
    const p = f.properties;
    // Map score to radius (8-22 px). Score is typically 0.5-8 for hot zones.
    const radius = Math.min(22, 7 + Math.sqrt(p.score) * 4);
    // Outer faint pulse ring.
    const outer = L.circleMarker([lat, lon], {
      radius: radius + 4,
      color: '#ff3b6e', fillColor: '#ff3b6e',
      fillOpacity: 0.05, weight: 0, interactive: false,
    });
    // Hollow ring + soft fill, so smaller earthquake/cyclone markers behind
    // it remain visible. Click target is the full ring.
    const core = L.circleMarker([lat, lon], {
      radius: radius,
      color: '#ff5577', fillColor: '#ff3b6e',
      fillOpacity: 0.22, weight: 1.8,
      className: 'conflict-marker',
      bubblingMouseEvents: false,
    });
    const recent = (p.recent || []).map(r => {
      const t = r.ts ? new Date(r.ts * 1000).toISOString().slice(11, 16) + 'Z' : '--:--';
      const tag = r.link ? 'a' : 'div';
      const href = r.link ? ` href="${escapeHtml(r.link)}" target="_blank" rel="noopener noreferrer"` : '';
      return `<${tag} class="cm-row"${href}>
                <div class="cm-meta"><span class="cm-time">${t}</span><span class="cm-src">${escapeHtml(r.src)}</span></div>
                <div class="cm-title">${escapeHtml(r.title)}</div>
              </${tag}>`;
    }).join('');
    core.bindPopup(`
      <div class="conflict-popup">
        <div class="cm-header">
          <div class="cm-name">${escapeHtml(p.name)}</div>
          <div class="cm-stats"><b>${p.count}</b> mentions / 24h &middot; score <b>${p.score}</b></div>
        </div>
        <div class="cm-list">${recent}</div>
        <div class="cm-footer">Click any headline to read full article</div>
      </div>
    `, { maxWidth: 420, minWidth: 320, className: 'conflict-popup-wrap' });
    outer.addTo(LAYERS.conflict);
    core.addTo(LAYERS.conflict);
  }
}

async function refreshConflict() {
  const { body, fresh } = await fgetJSON('/api/gdelt-conflict');
  setBadge('bd-conflict', fresh);
  recordFreshness(fresh);
  let arts = (body && body.articles) || [];
  // Merge the Defense Wire articles so the panel is a single unified intel
  // stream covering both open-source conflict news and military-defense
  // publications.
  if (LAST_DEFENSE && LAST_DEFENSE.length) {
    arts = arts.concat(LAST_DEFENSE);
    arts.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  }
  // Theater filter: when a theater is active, hide items whose title doesn't match.
  if (CURRENT_THEATER !== 'global') {
    arts = arts.filter(a => inTheater((a.title || '') + ' ' + (a.summary || '')));
  }
  FG_LAST_CONFLICT = arts;
  setStatus('conflict', arts.length, arts.length > 30 ? 'hot' : arts.length > 0 ? 'warn' : null);
  if (!arts.length) {
    $('body-conflict').innerHTML = (fresh === 'error')
      ? '<div class="empty"><b>Upstream feed error.</b> Retrying in a moment.</div>'
      : '<div class="empty">No items right now in this theater.</div>';
    return;
  }
  let html = '';
  for (const a of arts.slice(0, 40)) {
    const dt = a.ts ? new Date(a.ts * 1000) : null;
    const t = dt ? fmtTime(dt) : '--';
    const src = (a.src || '').slice(0, 10);
    const title = (a.title || '').slice(0, 130);
    const isWatch = matchesWatchlist(title);
    const tag = a.link ? 'a' : 'div';
    const href = a.link ? ` href="${escapeHtml(a.link)}" target="_blank" rel="noopener noreferrer"` : '';
    html += `<${tag} class="ln cnf${isWatch ? ' watch' : ''}"${href}><span class="t">${t}</span><span class="who">${escapeHtml(src)}</span><span class="ttl">${escapeHtml(title)}</span></${tag}>`;
  }
  fillStream($('body-conflict'), html);
  checkAndAlert(arts, 'CONFLICT');
}

// ============================================================
// SEVERE WEATHER (NWS US)
// ============================================================

const SEVERITY_COLOR = {
  Extreme:  '#ff3b6e',
  Severe:   '#ff5a4d',
  Moderate: '#e6b14a',
  Minor:    '#4ec5ff',
};

async function refreshWeather() {
  const { body, fresh } = await fgetJSON('/api/nws');
  setBadge('bd-weather', fresh);
  recordFreshness(fresh);
  LAYERS.weather.clearLayers();
  const out = $('body-weather');
  out.innerHTML = '';
  if (!body || !body.features || !body.features.length) {
    setStatus('wxalerts', 0, fresh === 'error' ? 'err' : 'good');
    FG_LAST_WX = [];
    out.innerHTML = (fresh === 'error')
      ? '<div class="empty"><b>NWS api.weather.gov unreachable.</b> Retrying shortly.</div>'
      : '<div class="empty">No active US alerts.</div>';
    return;
  }
  setStatus('wxalerts', body.features.length, body.features.length > 100 ? 'hot' : body.features.length > 30 ? 'warn' : null);
  FG_LAST_WX = body.features.map(f => f.properties);
  const order = { Extreme: 0, Severe: 1, Moderate: 2, Minor: 3, Unknown: 4 };
  const feats = body.features.slice().sort((a, b) =>
    (order[a.properties.severity] ?? 5) - (order[b.properties.severity] ?? 5));

  let tornadoSeen = false, hurricaneSeen = false;
  for (const f of feats.slice(0, 40)) {
    const p = f.properties || {};
    const sev = p.severity || 'Unknown';
    const ev  = p.event || 'Alert';
    if (/Tornado/i.test(ev))   tornadoSeen = true;
    if (/Hurricane/i.test(ev)) hurricaneSeen = true;
    const dt = p.onset || p.sent;
    const row = el('div', 'row clickable');
    row.appendChild(el('div', 'when', dt ? fmtTime(new Date(dt)) : '--'));
    const lbl = el('div', 'label');
    lbl.appendChild(el('span', 'title', ev));
    lbl.appendChild(el('span', 'sub',  (p.areaDesc || '').slice(0, 90)));
    row.appendChild(lbl);
    row.appendChild(el('div', 'right', sev[0]));
    // NWS alerts don't have a stable public HTML URL --- open an in-app
    // detail drawer with the full description / instruction / area / times.
    row.addEventListener('click', () => openNwsAlert(p));
    out.appendChild(row);
    if (f.geometry && f.geometry.type === 'Polygon') {
      const coords = f.geometry.coordinates[0].map(([lon, lat]) => [lat, lon]);
      const poly = L.polygon(coords, {
        color: SEVERITY_COLOR[sev] || '#e6b14a',
        fillColor: SEVERITY_COLOR[sev] || '#e6b14a',
        fillOpacity: 0.18, weight: 1.2,
        bubblingMouseEvents: false,
      });
      poly.on('click', () => openNwsAlert(p));
      poly.addTo(LAYERS.weather);
    }
  }
  if (tornadoSeen)   playCue('tornado');
  if (hurricaneSeen) playCue('hurricane');
}

// ============================================================
// TROPICAL CYCLONES (NOAA NHC)
// ============================================================

async function refreshCyclones() {
  // The panel is now "Major Hazards" --- it aggregates active tropical
  // cyclones AND significant earthquakes (M≥5) AND active volcano notices.
  // This way it always has content year-round.
  const { body, fresh } = await fgetJSON('/api/cyclones');
  setBadge('bd-cyclones', fresh);
  recordFreshness(fresh);
  LAYERS.cyclones.clearLayers();
  const out = $('body-cyclones');
  const storms = (body && body.activeStorms) || [];
  setStatus('cyclones', storms.length, storms.length > 3 ? 'hot' : storms.length > 0 ? 'warn' : 'good');

  // Reuse the Earthquakes panel's already-fetched USGS data (LAST_USGS) so we
  // don't double-hit the cache. If refreshQuakes hasn't run yet, fetch once.
  let bigQuakes = [];
  let usgs = LAST_USGS;
  if (!usgs) {
    try {
      const q = await fgetJSON('/api/usgs?window=day');
      if (q.body && q.body.features) { usgs = q.body; LAST_USGS = usgs; }
    } catch {}
  }
  if (usgs && usgs.features) {
    bigQuakes = usgs.features
      .filter(f => (f.properties.mag || 0) >= 5)
      .sort((a, b) => b.properties.time - a.properties.time)
      .slice(0, 8);
  }

  out.innerHTML = '';
  let any = false;

  // Section: active tropical cyclones
  if (storms.length) {
    any = true;
    const hdr = el('div', 'hazard-section', 'TROPICAL CYCLONES');
    out.appendChild(hdr);
    for (const s of storms) {
      const div = el('div', 'cyclone');
      div.appendChild(el('div', 'name', `${s.name || s.id}`));
      const intensity = parseInt(s.intensity || '0', 10);
      let catLabel = 'TD', major = false;
      if (intensity >= 137) { catLabel = 'C5'; major = true; }
      else if (intensity >= 113) { catLabel = 'C4'; major = true; }
      else if (intensity >= 96)  { catLabel = 'C3'; major = true; }
      else if (intensity >= 83)  { catLabel = 'C2'; }
      else if (intensity >= 64)  { catLabel = 'C1'; }
      else if (intensity >= 34)  { catLabel = 'TS'; }
      div.appendChild(el('div', 'cat' + (major ? ' major' : ''), catLabel));
      div.appendChild(el('div', 'basin', (s.classification || '')));
      out.appendChild(div);

      const lat = parseFloat(s.latitude || '0');
      const lon = parseFloat(s.longitude || '0');
      if (lat && lon) {
        L.circleMarker([lat, lon], {
          radius: major ? 10 : intensity >= 64 ? 8 : 6,
          color: major ? '#ff5a4d' : '#5fb8ff',
          fillColor: major ? '#ff5a4d' : '#5fb8ff',
          fillOpacity: 0.6, weight: 1.5,
          bubblingMouseEvents: false,
        }).bindPopup(`<b>${s.name || s.id}</b><br>${s.classification || ''}<br><small>winds ${intensity} kt</small>`)
          .addTo(LAYERS.cyclones);
      }
    }
  }

  // Section: significant earthquakes (M≥5)
  if (bigQuakes.length) {
    any = true;
    const hdr = el('div', 'hazard-section', 'SIGNIFICANT QUAKES (M≥5, 24h)');
    out.appendChild(hdr);
    for (const f of bigQuakes) {
      const p = f.properties;
      const dt = new Date(p.time);
      const row = el('div', 'row clickable');
      row.appendChild(el('div', 'when', fmtTime(dt)));
      const lbl = el('div', 'label');
      lbl.appendChild(el('span', 'title', p.place || 'Unknown'));
      const depth = (f.geometry && f.geometry.coordinates && f.geometry.coordinates[2]) || 0;
      lbl.appendChild(el('span', 'sub', `depth ${Math.round(depth)} km${p.tsunami ? ' • tsunami flag' : ''}`));
      row.appendChild(lbl);
      const mag = p.mag || 0;
      const magCls = mag >= 6 ? 'm4' : mag >= 5 ? 'm3' : 'm2';
      row.appendChild((() => { const d = el('div'); d.appendChild(el('span', 'mag ' + magCls, mag.toFixed(1))); return d; })());
      if (p.url) row.addEventListener('click', () => window.open(p.url, '_blank', 'noopener'));
      out.appendChild(row);
    }
  }

  // Section: GDACS red+orange alerts (filter out greens).
  const gdacsHot = GDACS_CACHE.filter(d => {
    const a = (d.alert || '').toLowerCase();
    return a === 'red' || a === 'orange';
  }).slice(0, 8);
  if (gdacsHot.length) {
    any = true;
    out.appendChild(el('div', 'hazard-section', 'GDACS ALERTS'));
    const wrap = el('div');
    wrap.innerHTML = gdacsHot.map(gdacsRowHtml).join('');
    out.appendChild(wrap);
  }

  if (!any) {
    // No major hazards at the moment --- a clear, visible quiet state.
    const month = new Date().getUTCMonth() + 1;
    let ctx = '';
    if (month >= 6 && month <= 11) {
      ctx = 'Atlantic hurricane season open. No named storms yet.';
    } else if (month === 5 || month === 12) {
      ctx = 'Inter-seasonal period for Atlantic tropical systems.';
    } else {
      ctx = 'Northern hemisphere season closed; Southern hemisphere active Nov–Apr.';
    }
    out.innerHTML = `<div class="empty">
        <b>No major hazards reported.</b><br>
        No active tropical cyclones (NOAA NHC), no M ≥ 5 earthquakes in the last 24h, and no orange/red GDACS alerts.
        <div class="seasonal">${ctx}</div>
      </div>`;
    return;
  }
}

// ============================================================
// HUMANITARIAN SITREPS (ReliefWeb / UN OCHA)
// ============================================================

async function refreshRelief() {
  const { body, fresh } = await fgetJSON('/api/relief');
  setBadge('bd-relief', fresh);
  recordFreshness(fresh);
  let items = (body && body.articles) || [];
  if (CURRENT_THEATER !== 'global') {
    items = items.filter(a => inTheater(a.title || ''));
  }
  FG_LAST_RELIEF = items;
  setStatus('relief', items.length);
  if (!items.length) {
    $('body-relief').innerHTML = (fresh === 'error')
      ? '<div class="empty"><b>ReliefWeb feed unreachable.</b> Retrying shortly.</div>'
      : '<div class="empty">No reports in this theater.</div>';
    return;
  }
  let html = '';
  for (const it of items.slice(0, 30)) {
    const dt = it.ts ? new Date(it.ts * 1000) : null;
    const t = dt ? fmtTime(dt) : '--';
    const title = (it.title || '').slice(0, 110);
    const m = title.match(/^([^:]{2,40}):\s*(.+)$/);
    const country = m ? m[1] : '';
    const rest = m ? m[2] : title;
    const isWatch = matchesWatchlist(title);
    const tag = it.link ? 'a' : 'div';
    const href = it.link ? ` href="${escapeHtml(it.link)}" target="_blank" rel="noopener noreferrer"` : '';
    html += `<${tag} class="ln rlf${isWatch ? ' watch' : ''}"${href}><span class="t">${t}</span><span class="who">${escapeHtml((country || 'RW').slice(0,10))}</span><span class="ttl">${escapeHtml(rest)}</span></${tag}>`;
  }
  fillStream($('body-relief'), html);
  checkAndAlert(items, 'SITREPS');
}

// ============================================================
// BITCOIN PULSE (mempool.space)
// ============================================================

let LAST_BLOCK_HEIGHT = null;

async function refreshBitcoin() {
  if (!PANELS_VISIBLE.btc) return;  // skip work if hidden
  const { body, fresh } = await fgetJSON('/api/mempool');
  setBadge('bd-btc', fresh);
  recordFreshness(fresh);
  if (!body) return;

  const fees   = body.fees      || {};
  const mp     = body.mempool   || {};
  const blocks = Array.isArray(body.blocks) ? body.blocks : [];
  const adj    = body.difficulty || {};

  // BTC price comes from the crypto endpoint cache; refreshCrypto stores
  // the latest BTC tick in LAST_BTC_PRICE on its way through.
  if (typeof LAST_BTC_PRICE === 'number' && LAST_BTC_PRICE > 0) {
    $('btc-price').textContent = '$' + Math.round(LAST_BTC_PRICE).toLocaleString();
  }

  $('btc-mempool-count').textContent = (mp.count != null) ? mp.count.toLocaleString() : '--';
  $('btc-mempool-vsize').textContent = mp.vsize ? Math.round(mp.vsize / 1e6) + ' MB' : '--';

  $('btc-fast-fee').textContent = fees.fastestFee != null ? fees.fastestFee + ' sat/vB' : '--';
  $('btc-econ-fee').textContent = fees.economyFee != null ? fees.economyFee + ' sat/vB' : '--';

  if (blocks.length) {
    const top = blocks[0];
    $('btc-height').textContent = top.height ? top.height.toLocaleString() : '--';
    if (top.timestamp) {
      const a = Math.max(0, Math.floor(Date.now() / 1000) - top.timestamp);
      $('btc-last-block').textContent = ago(a) + ' ago';
    }
    if (LAST_BLOCK_HEIGHT != null && top.height > LAST_BLOCK_HEIGHT) playCue('bitcoin_block');
    LAST_BLOCK_HEIGHT = top.height || LAST_BLOCK_HEIGHT;
  }
  if (adj && adj.remainingBlocks != null) {
    $('btc-adj').textContent = adj.remainingBlocks + ' blocks';
    const dc = adj.difficultyChange;
    const cd = $('btc-diff-change');
    cd.textContent = (dc != null) ? (dc > 0 ? '+' : '') + dc.toFixed(2) + '%' : '--';
    cd.style.color = dc > 0 ? 'var(--good)' : dc < 0 ? 'var(--hot)' : 'var(--text)';
  }
}

let LAST_BTC_PRICE = null;

// ============================================================
// WIKIPEDIA edit stream
// ============================================================

let LAST_WIKI_TS = 0;
const WIKI_BUF = [];

async function refreshWiki() {
  if (!PANELS_VISIBLE.wiki) return;
  const { body, fresh } = await fgetJSON('/api/wiki/recent?limit=80');
  setBadge('bd-wiki', fresh);
  if (!body || !body.events) return;
  const events = body.events;
  const dot = $('stat-wiki').querySelector('.dot');
  const txt = $('stat-wiki').querySelector('span:last-child');
  if (events.length) {
    dot.className = 'dot';
    txt.textContent = events.length + ' edits';
  }
  // Maintain a sliding buffer of latest 30 events for the stream.
  for (const e of events) {
    if ((e.ts || 0) <= LAST_WIKI_TS) continue;
    WIKI_BUF.unshift(e);
  }
  if (events.length) LAST_WIKI_TS = events[events.length - 1].ts || LAST_WIKI_TS;
  while (WIKI_BUF.length > 30) WIKI_BUF.pop();

  let html = '';
  for (const e of WIKI_BUF) {
    const t = fmtTime(new Date((e.ts || 0) * 1000));
    const who = (e.user || '?').slice(0, 14);
    const sz = (e.length && e.length.new != null && e.length.old != null)
      ? (e.length.new - e.length.old) : 0;
    const base = (e.serverurl || 'https://en.wikipedia.org').replace(/\/$/, '');
    const url = `${base}/wiki/${encodeURIComponent((e.title || '').replace(/ /g, '_'))}`;
    html += `<a class="ln wk" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"><span class="t">${t}</span><span class="who">${escapeHtml(who)}</span><span class="ttl">${escapeHtml(e.title || '?')}</span><span class="sz">${sz >= 0 ? '+' : ''}${sz}</span></a>`;
  }
  fillStream($('body-wiki'), html);
}

// ============================================================
// GITHUB
// ============================================================

const GH_VERBS = {
  PushEvent:        'pushed',
  PullRequestEvent: 'PR',
  IssuesEvent:      'issue',
  CreateEvent:      'created',
  ReleaseEvent:     'released',
  ForkEvent:        'forked',
  WatchEvent:       'starred',
};

async function refreshGitHub() {
  if (!PANELS_VISIBLE.github) return;
  const { body, fresh } = await fgetJSON('/api/github');
  setBadge('bd-gh', fresh);
  recordFreshness(fresh);
  if (!Array.isArray(body)) return;
  let html = '';
  for (const ev of body.slice(0, 30)) {
    const verb = GH_VERBS[ev.type] || ev.type;
    const t = fmtTime(new Date(ev.created_at));
    const who = (ev.actor && ev.actor.login) || '?';
    const repo = (ev.repo && ev.repo.name) || '?';
    const url = `https://github.com/${repo}`;
    html += `<a class="ln gh" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"><span class="t">${t}</span><span class="who">${escapeHtml(who)}</span><span class="evt">${verb}</span><span class="ttl">${escapeHtml(repo)}</span></a>`;
  }
  fillStream($('body-gh'), html);
}

// ============================================================
// ISS TRACKER
// ============================================================

async function refreshISS() {
  if (!PANELS_VISIBLE.iss) {
    if (ISS_MARKER) { LAYERS.iss.removeLayer(ISS_MARKER); ISS_MARKER = null; }
    return;
  }
  const { body } = await fgetJSON('/api/iss');
  if (!body || !body.iss_position) return;
  const lat = parseFloat(body.iss_position.latitude);
  const lon = parseFloat(body.iss_position.longitude);
  if (isNaN(lat) || isNaN(lon)) return;
  $('iss-readout').textContent = `ISS · ${lat.toFixed(2)}, ${lon.toFixed(2)}`;
  if (!ISS_MARKER) {
    ISS_MARKER = L.marker([lat, lon], { icon: pulseIcon('iss', 14) });
    ISS_MARKER.on('click', e => L.DomEvent.stopPropagation(e));
    ISS_MARKER.addTo(LAYERS.iss);
  } else {
    ISS_MARKER.setLatLng([lat, lon]);
  }
}

// ============================================================
// SEC EDGAR
// ============================================================

async function refreshSEC() {
  if (!PANELS_VISIBLE.sec) return;
  const r = await fget('/api/sec');
  const fresh = r.headers.get('X-Foglight-Freshness') || 'unknown';
  setBadge('bd-sec', fresh);
  recordFreshness(fresh);
  const text = await r.text();
  const entries = text.split(/<entry>/i).slice(1, 41);
  if (!entries.length) {
    $('body-sec').innerHTML = '<div class="empty">No filings.</div>';
    return;
  }
  let html = '';
  for (const e of entries) {
    const m = (re) => { const r = e.match(re); return r ? r[1].replace(/<[^>]*>/g, '').trim() : ''; };
    const title = m(/<title[^>]*>([\s\S]*?)<\/title>/i);
    const updated = m(/<updated[^>]*>([\s\S]*?)<\/updated>/i);
    const cat = m(/term="([^"]+)"/i);
    const linkM = e.match(/<link[^>]*href="([^"]+)"/i);
    const link = linkM ? linkM[1] : '';
    if (!title) continue;
    const dt = updated ? new Date(updated) : null;
    const t = dt ? fmtTime(dt) : '--';
    const tag = link ? 'a' : 'div';
    const href = link ? ` href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer"` : '';
    html += `<${tag} class="ln sec"${href}><span class="t">${t}</span><span class="who">${escapeHtml(cat || 'FILE')}</span><span class="ttl">${escapeHtml(title.slice(0,110))}</span></${tag}>`;
  }
  fillStream($('body-sec'), html);
}

// ============================================================
// HACKER NEWS + REDDIT
// ============================================================

async function refreshTalk() {
  if (!PANELS_VISIBLE.talk) return;
  const hn = await fgetJSON('/api/hn/top');
  recordFreshness(hn.fresh);
  let hnList = [];
  if (Array.isArray(hn.body)) {
    const ids = hn.body.slice(0, 8);
    const items = await Promise.all(
      ids.map(id => fgetJSON('/api/hn/item/' + id).then(x => x.body))
    );
    hnList = items.filter(x => x && x.title);
  }
  const reddit = await fgetJSON('/api/reddit');
  let rd = [];
  if (reddit.body && reddit.body.data && Array.isArray(reddit.body.data.children)) {
    rd = reddit.body.data.children.slice(0, 8).map(c => c.data);
  }
  setBadge('bd-talk', hn.fresh === 'live' || reddit.fresh === 'live' ? 'live'
                   : hn.fresh === 'cached' || reddit.fresh === 'cached' ? 'cached'
                   : (hn.fresh || reddit.fresh));

  let html = '';
  for (const it of hnList) {
    const url = it.url || `https://news.ycombinator.com/item?id=${it.id}`;
    html += `<a class="ln tlk" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"><span class="t">HN</span><span class="who">+${it.score || 0}</span><span class="ttl">${escapeHtml((it.title || '').slice(0,110))}</span></a>`;
  }
  for (const it of rd) {
    const url = `https://www.reddit.com${it.permalink || ''}`;
    html += `<a class="ln tlk" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"><span class="t">r/</span><span class="who">${escapeHtml((it.subreddit||'').slice(0,10))}</span><span class="ttl">${escapeHtml((it.title || '').slice(0,110))}</span></a>`;
  }
  fillStream($('body-talk'), html);
}

// ============================================================
// CRYPTO + FOREX + NEWS TICKERS (slow)
// ============================================================

async function refreshCrypto() {
  const { body, fresh } = await fgetJSON('/api/crypto');
  recordFreshness(fresh);
  if (!Array.isArray(body)) return;
  const top = body.slice(0, 30);
  const btc = body.find(t => t && (t.symbol === 'BTC' || t.id === 'btc-bitcoin'));
  if (btc && btc.quotes && btc.quotes.USD && btc.quotes.USD.price) {
    LAST_BTC_PRICE = btc.quotes.USD.price;
  }
  // Commodities (oil, gas, gold, etc.) prepended to the ticker so they
  // appear before the crypto block --- generals care about WTI more than DOGE.
  let commodityHtml = '';
  if (LAST_COMMODITIES) {
    for (const [label, c] of Object.entries(LAST_COMMODITIES)) {
      const cls = c.chg >= 0 ? 'up' : 'down';
      const sign = c.chg >= 0 ? '+' : '';
      commodityHtml +=
        `<span class="ti-crypto"><span class="sym">${escapeHtml(label)}</span>` +
        `<span>$${c.close.toFixed(2)}</span>` +
        `<span class="chg ${cls}">${sign}${c.chg.toFixed(2)}%</span></span>`;
    }
  }
  let cryptoHtml = top.map(t => {
    const px = (t.quotes && t.quotes.USD && t.quotes.USD.price) || 0;
    const ch = (t.quotes && t.quotes.USD && t.quotes.USD.percent_change_24h) || 0;
    const cls = ch >= 0 ? 'up' : 'down';
    const sign = ch >= 0 ? '+' : '';
    const price = px >= 1000 ? '$' + Math.round(px).toLocaleString()
                : px >= 1 ? '$' + px.toFixed(2)
                : '$' + px.toFixed(4);
    return `<span class="ti-crypto"><span class="sym">${t.symbol || t.id}</span><span>${price}</span><span class="chg ${cls}">${sign}${ch.toFixed(2)}%</span></span>`;
  }).join('');

  // Append forex pairs from the cached forex response if present.
  if (LAST_FOREX && LAST_FOREX.rates) {
    const want = ['EUR', 'GBP', 'JPY', 'CHF', 'CNY', 'AUD', 'CAD'];
    cryptoHtml += want.map(c => {
      const r = LAST_FOREX.rates[c];
      if (r == null) return '';
      const inv = (c === 'JPY' ? r : (1 / r));
      return `<span class="ti-crypto"><span class="sym">USD/${c}</span><span>${inv.toFixed(c === 'JPY' ? 2 : 4)}</span></span>`;
    }).join('');
  }
  // Commodities first, then crypto, then forex (already appended). Double
  // the whole thing for seamless scroll loop.
  const combined = commodityHtml + cryptoHtml;
  $('ticker-crypto-track').innerHTML = combined + combined;
}

let LAST_FOREX = null;
async function refreshForex() {
  const { body, fresh } = await fgetJSON('/api/forex');
  recordFreshness(fresh);
  if (body && body.rates) LAST_FOREX = body;
}

async function refreshNews() {
  let feeds;
  try {
    const s = await fgetJSON('/api/settings');
    feeds = (s.body && s.body.rss_feeds) || [];
  } catch { feeds = []; }

  const headlines = [];
  const results = await Promise.all(feeds.map(async url => {
    try {
      const r = await fget('/api/rss?url=' + encodeURIComponent(url));
      const text = await r.text();
      const src = (url.match(/\/\/([^/]+)\//) || [, ''])[1].replace(/^www\./, '').split('.')[0] || 'rss';
      const items = [];
      const re = /<item[^>]*>([\s\S]*?)<\/item>/gi;
      let m, n = 0;
      while ((m = re.exec(text)) && n < 8) {
        const blk = m[1];
        const t = (blk.match(/<title[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/title>/i) || [, ''])[1].trim();
        const d = (blk.match(/<pubDate[^>]*>([\s\S]*?)<\/pubDate>/i) || [, ''])[1].trim();
        if (t) items.push({ src, t, d });
        n++;
      }
      return items;
    } catch { return []; }
  }));
  results.forEach(arr => headlines.push(...arr));
  if (!headlines.length) return;
  for (let i = headlines.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [headlines[i], headlines[j]] = [headlines[j], headlines[i]];
  }
  const html = headlines.slice(0, 40).map(it => {
    const tsStr = it.d ? (() => { try { return fmtTime(new Date(it.d)); } catch { return ''; } })() : '';
    return `<span class="ti-news"><span class="src">${escapeHtml(it.src)}</span><span class="sep"></span><span>${escapeHtml(it.t)}</span>${tsStr ? `<span class="ts">${tsStr}</span>` : ''}</span>`;
  }).join('');
  $('ticker-news-track').innerHTML = html + html;
}

// ============================================================
// LIVE TV
// ============================================================

// Verified 24/7 live streams (confirmed playable via /embed/live_stream).
// If a channel goes dark, swap the ID here.
const TV_CHANNELS = [
  { id: 'aljazeera',  label: 'Al Jazeera',     ytChannel: 'UCNye-wNBqNL5ZzHSJj3l8Bg' },
  { id: 'france24',   label: 'France 24',      ytChannel: 'UCQfwfsi5VrQ8yKZ-UWmAEFg' },
  { id: 'dw',         label: 'DW News',        ytChannel: 'UCknLrEdhRCp1aegoMqRaCZg' },
  { id: 'bloomberg',  label: 'Bloomberg TV',   ytChannel: 'UCIALMKvObZNtJ6AmdCLP7Lg' },
];

let CURRENT_TV_CHANNEL = 'aljazeera';

function ytEmbedUrl(channelId, muted = true) {
  const m = muted ? '&mute=1' : '&mute=0';
  const origin = encodeURIComponent(window.location.origin || 'http://127.0.0.1');
  // Use plain youtube.com (not nocookie) --- nocookie variant sometimes
  // rejects live_stream embeds for newer channels.
  return `https://www.youtube.com/embed/live_stream?channel=${channelId}&autoplay=1${m}&playsinline=1&origin=${origin}`;
}

function ytWatchUrl(channelId) {
  return `https://www.youtube.com/channel/${channelId}/live`;
}

function setTvOpenLink(channelId) {
  const a = $('tv-open');
  if (a) a.href = ytWatchUrl(channelId);
}

function renderTvTabs() {
  const wrap = $('tv-tabs');
  wrap.innerHTML = '';
  for (const c of TV_CHANNELS) {
    const b = el('button', 'tv-tab' + (c.id === CURRENT_TV_CHANNEL ? ' active' : ''), c.label);
    b.dataset.id = c.id;
    b.addEventListener('click', () => switchTv(c.id));
    wrap.appendChild(b);
  }
}

let TV_STARTED = false;
function switchTv(channelId) {
  const ch = TV_CHANNELS.find(c => c.id === channelId);
  if (!ch) return;
  CURRENT_TV_CHANNEL = channelId;
  // Default state: iframe loads muted-autoplay. After the user clicks the
  // overlay, we reload the iframe WITHOUT mute (since now we have a user
  // gesture and browsers will allow audio).
  $('tv-frame').src = ytEmbedUrl(ch.ytChannel, TV_STARTED ? false : true);
  setTvOpenLink(ch.ytChannel);
  $('tv-overlay-label').textContent = `Start ${ch.label}`;
  $('tv-overlay').classList.toggle('hide', TV_STARTED);
  document.querySelectorAll('.tv-tab').forEach(b =>
    b.classList.toggle('active', b.dataset.id === channelId));
  fget('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tv_channel: channelId }),
  }).catch(() => {});
}

function startTvWithSound() {
  TV_STARTED = true;
  const ch = TV_CHANNELS.find(c => c.id === CURRENT_TV_CHANNEL) || TV_CHANNELS[0];
  $('tv-frame').src = ytEmbedUrl(ch.ytChannel, false);
  setTvOpenLink(ch.ytChannel);
  $('tv-overlay').classList.add('hide');
}

// ============================================================
// PANEL VISIBILITY
// ============================================================

const PANEL_DEFS = [
  { id: 'tv',       label: 'Live TV',                 node: 'panel-tv' },
  { id: 'conflict', label: 'Conflict Watch',          node: 'panel-conflict' },
  { id: 'cyclones', label: 'Major Hazards',           node: 'panel-cyclones' },
  { id: 'relief',   label: 'Humanitarian Sitreps',    node: 'panel-relief' },
  { id: 'btc',      label: 'Bitcoin Pulse',           node: 'panel-btc' },
  { id: 'iss',      label: 'ISS Tracker overlay',     node: null },  // map overlay
  { id: 'wiki',     label: 'Wikipedia Edits',         node: 'panel-wiki' },
  { id: 'github',   label: 'GitHub Pulse',            node: 'panel-gh' },
  { id: 'sec',      label: 'SEC EDGAR',               node: 'panel-sec' },
  { id: 'talk',     label: 'Hacker News + Reddit',    node: 'panel-talk' },
];

let PANELS_VISIBLE = {};

function applyPanelVisibility() {
  for (const p of PANEL_DEFS) {
    if (!p.node) continue;
    const el = $(p.node);
    if (el) el.classList.toggle('hidden', !PANELS_VISIBLE[p.id]);
  }
  $('iss-readout').style.display = PANELS_VISIBLE.iss ? '' : 'none';
  // ISS toggle also drops the map marker.
  if (LAYERS && LAYERS.iss && !PANELS_VISIBLE.iss && ISS_MARKER) {
    LAYERS.iss.removeLayer(ISS_MARKER);
    ISS_MARKER = null;
  }
  renderOptionalCta();
}

// Show a discoverable CTA at the bottom strip when one or more of the
// optional panels (Bitcoin / Wikipedia / GitHub / SEC / HN+Reddit) are off.
function renderOptionalCta() {
  const optionalIds = ['btc', 'wiki', 'github', 'sec', 'talk'];
  const hidden = optionalIds.filter(id => !PANELS_VISIBLE[id]);
  const bottom = $('pane-bottom');
  const main = $('main');
  let cta = $('optional-cta');
  // If everything optional is visible, no CTA needed.
  if (!hidden.length) {
    main.classList.remove('optional-empty');
    main.style.gridTemplateRows = '';
    main.style.gridTemplateAreas = '';
    if (cta) cta.remove();
    bottom.style.display = '';
    return;
  }
  // If everything optional is HIDDEN, the bottom row would be empty ---
  // replace it entirely with the CTA strip so the layout breathes upward.
  const allHidden = hidden.length === optionalIds.length;
  main.classList.toggle('optional-empty', allHidden);
  main.style.gridTemplateRows = '';
  main.style.gridTemplateAreas = '';
  if (allHidden) {
    bottom.style.display = 'none';
    // Place the CTA in #main as a sibling of pane-bottom.
    if (!cta) {
      cta = document.createElement('div');
      cta.id = 'optional-cta';
      cta.addEventListener('click', () => { openSettings(); scrollSettingsTo('panels'); });
      document.body.appendChild(cta);
      cta.style.position = 'fixed';
      cta.style.left = '0'; cta.style.right = '0'; cta.style.bottom = '0';
      cta.style.zIndex = '30';
    }
    cta.innerHTML = `<span class="pill">+ ${hidden.length} optional panels</span>
                     <span>Bitcoin · Wikipedia · GitHub · SEC · Hacker News</span>
                     <span class="pill">Open Settings</span>`;
  } else {
    // Some optionals are visible --- show CTA as a small chip in the bottom row.
    bottom.style.display = '';
    if (cta) cta.remove();
  }
}

function scrollSettingsTo(_section) {
  // Settings page is a single scrolling pane; "Panels" is the first section
  // so opening Settings naturally lands on it. Hook kept for future deeper links.
  const wrap = $('pane-settings');
  if (wrap) wrap.scrollTop = 0;
}

function renderPanelToggles() {
  const wrap = $('panel-toggles');
  wrap.innerHTML = '';
  for (const p of PANEL_DEFS) {
    const row = el('div', 'setting-row');
    const lbl = el('div', 'lbl');
    lbl.appendChild(el('h3', '', p.label));
    row.appendChild(lbl);
    const t = el('div', 'toggle' + (PANELS_VISIBLE[p.id] ? ' on' : ''));
    t.addEventListener('click', () => togglePanel(p.id, t));
    row.appendChild(t);
    wrap.appendChild(row);
  }
}

async function togglePanel(id, toggleEl) {
  PANELS_VISIBLE[id] = !PANELS_VISIBLE[id];
  toggleEl.classList.toggle('on', PANELS_VISIBLE[id]);
  applyPanelVisibility();
  // Persist
  fget('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ panels: { [id]: PANELS_VISIBLE[id] } }),
  }).catch(() => {});
  // Refresh the panel's data if we just enabled it.
  if (PANELS_VISIBLE[id]) {
    if (id === 'wiki')     refreshWiki();
    if (id === 'github')   refreshGitHub();
    if (id === 'sec')      refreshSEC();
    if (id === 'talk')     refreshTalk();
    if (id === 'cyclones') refreshCyclones();
    if (id === 'relief')   refreshRelief();
    if (id === 'iss')      refreshISS();
    if (id === 'btc')      refreshBitcoin();
  }
}

function renderTvChannelPicker() {
  const wrap = $('tv-channels');
  wrap.innerHTML = '';
  for (const c of TV_CHANNELS) {
    const row = el('div', 'setting-row');
    const lbl = el('div', 'lbl');
    lbl.appendChild(el('h3', '', c.label));
    lbl.appendChild(el('p', '', `YouTube channel ${c.ytChannel}`));
    row.appendChild(lbl);
    const b = el('button', '', c.id === CURRENT_TV_CHANNEL ? 'Default' : 'Make default');
    b.addEventListener('click', () => { switchTv(c.id); renderTvChannelPicker(); });
    if (c.id === CURRENT_TV_CHANNEL) b.style.borderColor = 'var(--good)';
    row.appendChild(b);
    wrap.appendChild(row);
  }
}

// ============================================================
// AUDIO CUES
// ============================================================

let AUDIO_CTX = null;
let AUDIO_SETTINGS = { master: false };
function audioOn(kind) { return AUDIO_SETTINGS.master && AUDIO_SETTINGS[kind]; }
function ensureAudio() {
  if (!AUDIO_CTX) {
    try { AUDIO_CTX = new (window.AudioContext || window.webkitAudioContext)(); } catch { return null; }
  }
  if (AUDIO_CTX.state === 'suspended') AUDIO_CTX.resume();
  return AUDIO_CTX;
}
function playTone({ freq, dur = 0.5, type = 'sine', vol = 0.15, attack = 0.01, release = 0.3 }) {
  const ctx = ensureAudio(); if (!ctx) return;
  const t0 = ctx.currentTime;
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = type; osc.frequency.value = freq;
  g.gain.setValueAtTime(0, t0);
  g.gain.linearRampToValueAtTime(vol, t0 + attack);
  g.gain.linearRampToValueAtTime(0, t0 + dur + release);
  osc.connect(g); g.connect(ctx.destination);
  osc.start(t0); osc.stop(t0 + dur + release + 0.05);
}
function playEarthquakeCue(mag) {
  if (!audioOn('earthquake')) return;
  const f = mag >= 6 ? 60 : mag >= 5 ? 110 : 200;
  playTone({ freq: f, dur: mag >= 6 ? 1.4 : 0.6, type: 'sine', vol: 0.18 });
}
function playCue(kind) {
  if (!audioOn(kind)) return;
  if (kind === 'tornado')         playTone({ freq: 660, dur: 0.4, type: 'triangle', vol: 0.10 });
  else if (kind === 'hurricane')  playTone({ freq: 120, dur: 1.0, type: 'sawtooth', vol: 0.10 });
  else if (kind === 'bitcoin_block') playTone({ freq: 90, dur: 0.18, type: 'sine', vol: 0.14, attack: 0.005, release: 0.18 });
  else if (kind === 'breaking_news') playTone({ freq: 880, dur: 0.18, type: 'sine', vol: 0.08 });
  else if (kind === 'iss_pass')   playTone({ freq: 520, dur: 0.5, type: 'sine', vol: 0.08 });
  else if (kind === 'gdelt_conflict') playTone({ freq: 220, dur: 0.4, type: 'sine', vol: 0.10 });
}

// ============================================================
// SETTINGS
// ============================================================

let SETTINGS_CACHED = null;
function openSettings() {
  $('pane-settings').classList.add('show');
  $('settings-close').classList.add('show');
  refreshSettings();
}
function closeSettings() {
  $('pane-settings').classList.remove('show');
  $('settings-close').classList.remove('show');
}
// Global Esc key handler --- closes whichever drawer / overlay is open.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if ($('alert-drawer').classList.contains('show'))  { closeAlertDrawer(); return; }
  if ($('pane-settings').classList.contains('show')) { closeSettings();    return; }
});

function openNwsAlert(p) {
  const drawer = $('alert-drawer');
  const event = p.event || p.headline || 'NWS alert';
  $('ad-event').textContent = event;
  const sev = (p.severity || 'unknown').toLowerCase();
  const onset = p.onset   ? new Date(p.onset).toISOString().replace('T', ' ').slice(0, 16) + ' UTC' : '—';
  const expir = p.expires ? new Date(p.expires).toISOString().replace('T', ' ').slice(0, 16) + ' UTC' : '—';
  const sender = p.senderName || p.sender || '—';
  const desc = (p.description || '').trim();
  const instr = (p.instruction || '').trim();
  const area = (p.areaDesc || '').trim();
  const headline = (p.headline || '').trim();

  $('ad-body').innerHTML = `
    <div class="ad-meta">
      <span class="lab">Severity</span><span class="val sev-${escapeHtml(sev)}">${escapeHtml(p.severity || 'Unknown')}</span>
      <span class="lab">Urgency</span><span class="val">${escapeHtml(p.urgency || '—')}</span>
      <span class="lab">Certainty</span><span class="val">${escapeHtml(p.certainty || '—')}</span>
      <span class="lab">Onset</span><span class="val">${escapeHtml(onset)}</span>
      <span class="lab">Expires</span><span class="val">${escapeHtml(expir)}</span>
      <span class="lab">Issued by</span><span class="val">${escapeHtml(sender)}</span>
    </div>
    ${headline ? `<h3>Headline</h3><p>${escapeHtml(headline)}</p>` : ''}
    ${area     ? `<h3>Affected Area</h3><p>${escapeHtml(area)}</p>` : ''}
    ${desc     ? `<h3>Description</h3><p>${escapeHtml(desc)}</p>` : ''}
    ${instr    ? `<h3>Instructions</h3><p>${escapeHtml(instr)}</p>` : ''}
  `;
  drawer.classList.add('show');
}

function closeAlertDrawer() { $('alert-drawer').classList.remove('show'); }
window.closeAlertDrawer = closeAlertDrawer;

async function refreshSettings() {
  const { body } = await fgetJSON('/api/settings');
  SETTINGS_CACHED = body;
  if (!body) return;
  for (const k of Object.keys(body.keys || {})) {
    const el2 = $('ks-' + k);
    if (el2) {
      el2.textContent = body.keys[k] ? 'connected' : 'not set';
      el2.classList.toggle('on', !!body.keys[k]);
    }
    const input = $('key-' + k);
    if (input) input.value = '';
  }
  AUDIO_SETTINGS = Object.assign({}, body.audio || {});
  for (const k of Object.keys(body.audio || {})) {
    const t = $('aud-' + k);
    if (t) t.classList.toggle('on', !!body.audio[k]);
  }
  // Render the panel toggle list (the visibility is already applied at boot).
  renderPanelToggles();
  renderTvChannelPicker();
}

async function saveKeys() {
  const patch = { keys: {} };
  for (const k of ['aisstream', 'nasa_firms', 'opensky_id', 'opensky_secret',
                   'openweathermap', 'fred', 'finnhub']) {
    const v = ($('key-' + k) || {}).value;
    if (v != null && v !== '') patch.keys[k] = v.trim();
  }
  await fget('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  await refreshSettings();
}
async function clearAllKeys() {
  const ok = await showDangerConfirm('Erase all stored API keys?', 'Clear keys', 'Clear all');
  if (!ok) return;
  const patch = { keys: {} };
  for (const k of ['aisstream', 'nasa_firms', 'opensky_id', 'opensky_secret',
                   'openweathermap', 'fred', 'finnhub']) patch.keys[k] = '';
  await fget('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  await refreshSettings();
}

async function toggleAudio(kind) {
  const next = !AUDIO_SETTINGS[kind];
  AUDIO_SETTINGS[kind] = next;
  const t = $('aud-' + kind); if (t) t.classList.toggle('on', next);
  if (next && kind === 'master') ensureAudio();
  await fget('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ audio: { [kind]: next } }),
  });
}

// ============================================================
// MODAL
// ============================================================

function showDangerConfirm(message, title, accept) {
  return new Promise(resolve => {
    $('modal-title').textContent = title || 'Confirm';
    $('modal-msg').textContent = message;
    const ok = $('modal-ok'), cancel = $('modal-cancel'), ov = $('modal-overlay');
    ok.textContent = accept || 'OK';
    ov.classList.add('show');
    const cleanup = () => { ov.classList.remove('show'); ok.removeEventListener('click', onOk); cancel.removeEventListener('click', onC); };
    const onOk = () => { cleanup(); resolve(true); };
    const onC  = () => { cleanup(); resolve(false); };
    ok.addEventListener('click', onOk);
    cancel.addEventListener('click', onC);
  });
}

// ============================================================
// SHUTDOWN
// ============================================================

let SHUTDOWN_IN_PROGRESS = false;
async function shutdownApp() {
  if (SHUTDOWN_IN_PROGRESS) return;
  const ok = await showDangerConfirm(
    'This stops the local Foglight server and closes the desktop app.',
    'Shut down Foglight', 'Shut down');
  if (!ok) return;
  SHUTDOWN_IN_PROGRESS = true;
  $('shutdown-overlay').classList.add('show');
  try {
    await fetch('/api/shutdown', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  } catch {}
  setTimeout(() => { try { if (window.closeApp) window.closeApp(); } catch {} }, 350);
  setTimeout(() => { try { window.close(); } catch {} }, 1200);
}

// ============================================================
// UTIL
// ============================================================

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

// ============================================================
// BOOT
// ============================================================

async function loadInitialSettings() {
  // Defaults: focus panels on, internet-pulse + bitcoin off.
  const SITREP_ON = new Set(['tv', 'conflict', 'cyclones', 'relief', 'iss']);
  try {
    const { body } = await fgetJSON('/api/settings');
    if (body) {
      PANELS_VISIBLE = Object.assign({}, body.panels || {});
      for (const p of PANEL_DEFS) {
        if (PANELS_VISIBLE[p.id] == null) {
          PANELS_VISIBLE[p.id] = SITREP_ON.has(p.id);
        }
      }
      AUDIO_SETTINGS = Object.assign({}, body.audio || {});
      if (body.tv_channel && TV_CHANNELS.some(c => c.id === body.tv_channel)) {
        CURRENT_TV_CHANNEL = body.tv_channel;
      }
      if (Array.isArray(body.watchlist)) {
        WATCHLIST = body.watchlist.slice();
        // Populate the settings textarea on first paint.
        setTimeout(() => {
          const ta = $('watchlist-text');
          if (ta) ta.value = WATCHLIST.join('\n');
        }, 0);
      }
      if (Array.isArray(body.annotations)) {
        ANNOTATIONS = body.annotations.slice();
      }
    }
  } catch {}
}

function wireTheaterBar() {
  document.querySelectorAll('#theaterbar .t-btn').forEach(b => {
    b.addEventListener('click', () => switchTheater(b.dataset.theater));
  });
  document.querySelector('#theaterbar .t-btn[data-theater="global"]')?.classList.add('active');
}

async function start() {
  // Pre-fill the ticker tracks immediately with placeholder pills so the
  // user doesn't see two empty bars while the RSS feeds load (the negative
  // animation-delay means the ticker is mid-cycle on first paint --- and
  // a mid-cycle empty track looks like a bug).
  $('ticker-news-track').innerHTML = (
    '<span class="ti-news"><span class="src">Foglight</span><span class="sep"></span>' +
    '<span>Loading global news wire&hellip;</span></span>'
  ).repeat(24);
  $('ticker-crypto-track').innerHTML = (
    '<span class="ti-crypto"><span class="sym">markets</span><span>loading</span></span>'
  ).repeat(50);

  await loadInitialSettings();
  initMap();
  applyPanelVisibility();

  // Live TV: render tabs and load the default channel muted (autoplay
  // works while muted in every modern browser). The overlay invites a
  // click to unmute, which gives us the user gesture needed for audio.
  renderTvTabs();
  const ch = TV_CHANNELS.find(c => c.id === CURRENT_TV_CHANNEL) || TV_CHANNELS[0];
  $('tv-frame').src = ytEmbedUrl(ch.ytChannel, true);
  setTvOpenLink(ch.ytChannel);
  $('tv-overlay-label').textContent = `Start ${ch.label}`;
  $('tv-overlay').addEventListener('click', startTvWithSound);

  // Wire the new chrome bars.
  wireTheaterBar();
  updateCapitalClocks();

  // Initial paint: focus panels (sitrep core).
  refreshQuakes();
  refreshConflictHotspots();
  refreshEonet();
  refreshFlights();
  refreshFirms();
  refreshDefense();
  refreshCommodities();
  refreshWeather();
  refreshConflict();
  refreshGdacs();
  refreshCyclones();
  refreshRelief();
  refreshSpaceWeather();
  refreshISS();
  refreshNews();
  refreshCrypto();
  refreshForex();
  // Optional panels: only fetch if visible.
  refreshBitcoin();
  refreshWiki();
  refreshGitHub();
  refreshSEC();
  refreshTalk();
  refreshSettings();

  // Per-panel cadence (slower than v1 --- this is a "leave open" app).
  setInterval(refreshQuakes,            120 * 1000);
  setInterval(refreshConflictHotspots,  240 * 1000);
  setInterval(refreshEonet,             600 * 1000);
  setInterval(refreshWeather,           180 * 1000);
  setInterval(refreshConflict,          240 * 1000);
  setInterval(refreshGdacs,             300 * 1000);
  setInterval(refreshCyclones,          600 * 1000);
  setInterval(refreshRelief,            300 * 1000);
  setInterval(refreshSpaceWeather,      900 * 1000);
  setInterval(refreshBitcoin,            45 * 1000);
  setInterval(refreshWiki,               10 * 1000);
  setInterval(refreshGitHub,             45 * 1000);
  setInterval(refreshISS,                10 * 1000);
  setInterval(refreshSEC,               180 * 1000);
  setInterval(refreshTalk,              180 * 1000);
  setInterval(refreshCrypto,            120 * 1000);
  setInterval(refreshForex,        60 * 60 * 1000);
  setInterval(refreshNews,              240 * 1000);
  setInterval(refreshFlights,            30 * 1000);
  setInterval(refreshFirms,             900 * 1000);
  setInterval(refreshDefense,           300 * 1000);
  setInterval(refreshCommodities,       300 * 1000);
}

window.generateBriefing = generateBriefing;

window.openSettings = openSettings;
window.closeSettings = closeSettings;
window.saveKeys = saveKeys;
window.clearAllKeys = clearAllKeys;
window.toggleAudio = toggleAudio;
window.shutdownApp = shutdownApp;

document.addEventListener('DOMContentLoaded', start);
