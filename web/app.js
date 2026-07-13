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

import { createApiClient } from './api.js';
import { createCommunityControllers } from './community.js';
import {
  byId as $,
  elapsed as ago,
  element as el,
  escapeHtml,
  formatUtcTime as fmtTime,
  runWithConcurrency,
  safeHttpUrl,
  updateSourceFreshness,
} from './core.js';
import { createAppStore } from './store.js';
import { normalizeInitialSettings } from './settings.js';
import { createTickerControllers } from './tickers.js';
import { createOverviewController } from './overview.js';
import { addBundledWorldBase } from './map-v2.js';
import { createWatchCenterController } from './watch-center.js';
import { isQuietHours, normalizeWatchRegions } from './watch-model.js';

const APP_STORE = createAppStore();
const API_CLIENT = createApiClient();
const fget = API_CLIENT.request;
const fgetJSON = API_CLIENT.getJSON;
const loadSession = API_CLIENT.loadSession;

// ---------- helpers ----------
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
const FEED_HEALTH_BY_SOURCE = new Map();

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
  window.__foglightFeedHealth = { ...feedsHealth, total };
}
function recordFreshness(source, fresh) {
  feedsHealth = updateSourceFreshness(FEED_HEALTH_BY_SOURCE, source, fresh);
  updateFeedsStat();
}

function combineFreshness(values) {
  const rank = { live: 0, cached: 1, stale: 2, error: 3 };
  return values.reduce((worst, value) =>
    (rank[value] ?? 3) > (rank[worst] ?? 3) ? value : worst, 'live');
}

// ============================================================
// MAP
// ============================================================

let MAP = null;
let OPEN_METEO_ENABLED = false;
let YAHOO_FINANCE_ENABLED = false;
let PROVIDER_CATALOG_LOADED = false;
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

  addBundledWorldBase(MAP, { statusNode: $('map-status') });

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
  if (OPEN_METEO_ENABLED) attachMapClickWeather();

  // Right-click on map → add a labeled pin (intel annotation).
  MAP.on('contextmenu', (e) => {
    const lat = e.latlng.lat;
    const lon = ((e.latlng.lng + 540) % 360) - 180;
    addAnnotation(lat, lon).catch(() => alert('The pin could not be saved.'));
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
  APP_STORE.state.ui.theater = id;
  document.querySelectorAll('#theaterbar .t-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.theater === id));
  if (MAP) MAP.setView([t.view[0], t.view[1]], t.view[2]);
  // Re-render conflict + sitreps + GDACS with the theater filter applied.
  runRefresh(refreshConflict);
  runRefresh(refreshRelief);
  runRefresh(refreshCyclones);  // includes GDACS section
}

function inTheater(text) {
  const t = THEATERS[APP_STORE.state.ui.theater];
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
  if (APP_STORE.state.ui.displayMode !== 'standard') {
    if (!OVERVIEW_CONTROLLER?.printSelectedBriefing()) {
      const live = $('overview-live');
      if (live) live.textContent = 'Select an incident before opening a printable briefing.';
    }
    return;
  }
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
  <button id="briefing-print">Print / save as PDF</button>
  <button id="briefing-close">Close</button>
</div>
<h1>Foglight Situation Report</h1>
<div class="meta">As of ${ts} &middot; theater: ${escapeHtml((THEATERS[APP_STORE.state.ui.theater]||{}).label || '?')}</div>

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
  Generated by Foglight &middot; open-source civilian sitrep dashboard &middot; data from public RSS, USGS, NWS, NOAA NHC, ReliefWeb, GDACS, EONET.
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
  w.document.getElementById('briefing-print')?.addEventListener('click', () => w.print());
  w.document.getElementById('briefing-close')?.addEventListener('click', () => w.close());
}

// Caches the renderers feed for the briefing export.
let FG_LAST_HOTSPOTS = [];
let FG_LAST_CONFLICT = [];
let FG_LAST_RELIEF   = [];
let FG_LAST_WX       = [];

// ============================================================
// APP_STORE.state.user.watchlist — keyword alerts across streams
// ============================================================
let WATCHLIST_SEEN = new Set();   // titles we've already alerted on
function matchesWatchlist(text) {
  if (!APP_STORE.state.user.watchlist.length || !text) return false;
  const lower = text.toLowerCase();
  return APP_STORE.state.user.watchlist.some(kw => kw && lower.includes(kw.toLowerCase()));
}
function checkAndAlert(items, srcLabel) {
  // For new items matching watchlist, fire breaking banner (deduped).
  const notificationConfig = APP_STORE.state.user.notifications || {};
  if (!notificationConfig.enabled || isQuietHours(
    Date.now(), notificationConfig.quiet_start, notificationConfig.quiet_end,
  )) return;
  for (const it of items) {
    const text = (it.title || '') + ' ' + (it.summary || '');
    if (!matchesWatchlist(text)) continue;
    const key = srcLabel + '|' + (it.title || '').slice(0, 80);
    if (WATCHLIST_SEEN.has(key)) continue;
    WATCHLIST_SEEN.add(key);
    if (WATCHLIST_SEEN.size > 1) {  // skip first-launch flood
      fireBreaking('APP_STORE.state.user.watchlist HIT — ' + srcLabel + ' — ' + (it.title || '').slice(0, 80));
    }
  }
}

async function saveWatchlist() {
  const button = $('save-watchlist');
  if (button.disabled) return;
  button.disabled = true;
  const text = $('watchlist-text').value;
  const kws = text.split('\n').map(s => s.trim()).filter(Boolean);
  const previousWatchlist = APP_STORE.state.user.watchlist.slice();
  const previousRegions = APP_STORE.state.user.watchRegions.slice();
  const retainedRegions = (APP_STORE.state.user.watchRegions || [])
    .filter(region => region?.id !== 'legacy:keywords');
  const watchRegions = normalizeWatchRegions(retainedRegions, kws);
  APP_STORE.update('user', { watchlist: kws, watchRegions });
  WATCHLIST_SEEN.clear();  // reset so re-saves can re-alert
  try {
    const response = await fget('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ watchlist: kws, watch_regions: watchRegions }),
    });
    if (!response.ok) throw new Error(`watchlist write failed (${response.status})`);
    WATCH_CENTER_CONTROLLER?.refreshSettings();
    button.textContent = 'Saved';
    window.setTimeout(() => { button.textContent = 'Save watchlist'; }, 1200);
    // Re-render streams to apply highlight.
    refreshConflict(); refreshRelief();
  } catch {
    APP_STORE.update('user', {
      watchlist: previousWatchlist, watchRegions: previousRegions,
    });
    $('watchlist-text').value = previousWatchlist.join('\n');
    WATCH_CENTER_CONTROLLER?.refreshSettings();
    button.textContent = 'Save failed';
  } finally {
    button.disabled = false;
  }
}

// ============================================================
// MAP APP_STORE.state.user.annotations — user-pinned points
// ============================================================
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
  for (let i = 0; i < APP_STORE.state.user.annotations.length; i++) {
    const a = APP_STORE.state.user.annotations[i];
    const icon = L.divIcon({
      className: '',
      iconSize: [16, 16],
      html: `<div class="anno-pin"></div>`,
    });
    const m = L.marker([a.lat, a.lon], {
      icon,
      title: `Map annotation: ${a.label || 'Saved pin'}`,
    });
    m.bindTooltip(a.label || 'Pinned', { permanent: false, direction: 'top' });
    m.bindPopup(
      `<div style="font:11px 'JetBrains Mono',monospace">` +
      `<b style="color:#f7931a;text-transform:uppercase;letter-spacing:0.06em">${escapeHtml(a.label || 'Pinned')}</b><br>` +
      `<span style="color:#7e8aa3">${a.lat.toFixed(2)}°, ${a.lon.toFixed(2)}°</span><br><br>` +
      `<button class="annotation-remove" data-annotation-index="${i}" style="background:var(--hot);color:#fff;border:0;padding:4px 8px;cursor:pointer;font:10px monospace;text-transform:uppercase">Remove pin</button>` +
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
  if (!APP_STORE.state.user.annotations.length) {
    wrap.innerHTML = '<div style="color:var(--text-dimmer);font-size:11px;font-style:italic;padding:6px 0">No pins yet. Right-click anywhere on the world map to add one.</div>';
    return;
  }
  wrap.innerHTML = APP_STORE.state.user.annotations.map((a, i) =>
    `<div class="anno"><span class="dot"></span>` +
    `<span class="lbl">${escapeHtml(a.label || 'Pinned')}</span>` +
    `<span class="coord">${a.lat.toFixed(2)}°, ${a.lon.toFixed(2)}°</span>` +
    `<button class="annotation-remove" data-annotation-index="${i}">remove</button></div>`
  ).join('');
}

async function addAnnotation(lat, lon) {
  if (APP_STORE.state.user.annotations.length >= 100) {
    alert('Foglight stores up to 100 pins. Remove one in Settings first.');
    return;
  }
  const label = prompt('Pin label (intel note):', '');
  if (label == null) return;  // cancelled
  APP_STORE.state.user.annotations.push({
    lat, lon, label: (label.trim() || 'Pinned').slice(0, 80),
  });
  try {
    await persistAnnotations();
  } catch (error) {
    APP_STORE.state.user.annotations.pop();
    throw error;
  }
  redrawAnnotations();
}

async function addAnnotationFromForm(annotation) {
  APP_STORE.state.user.annotations.push(annotation);
  try {
    await persistAnnotations();
  } catch (error) {
    APP_STORE.state.user.annotations.pop();
    throw error;
  }
  redrawAnnotations();
}

async function removeAnnotation(idx) {
  APP_STORE.state.user.annotations.splice(idx, 1);
  await persistAnnotations();
  redrawAnnotations();
  MAP && MAP.closePopup();
}

async function clearAllAnnotations() {
  if (!confirm('Remove all map pins?')) return;
  APP_STORE.state.user.annotations = [];
  await persistAnnotations();
  redrawAnnotations();
}

async function persistAnnotations() {
  await fget('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ annotations: APP_STORE.state.user.annotations }),
  });
}

window.removeAnnotation    = removeAnnotation;
window.saveWatchlist       = saveWatchlist;
window.clearAllAnnotations = clearAllAnnotations;
document.addEventListener('click', (event) => {
  const button = event.target.closest && event.target.closest('.annotation-remove');
  if (!button) return;
  const index = Number(button.dataset.annotationIndex);
  if (Number.isInteger(index)) removeAnnotation(index);
});

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
//   so users see official DOD, Defense News, and War on the Rocks alongside
//   UN / DW / France 24 — one unified intel firehose.
// ============================================================
let LAST_DEFENSE = [];
async function refreshDefense() {
  const { body, fresh } = await fgetJSON('/api/defense-wire');
  recordFreshness('defense', fresh);
  if (!body || !body.articles) return;
  LAST_DEFENSE = body.articles;
  checkAndAlert(LAST_DEFENSE, 'DEFENSE');
  // Trigger a conflict-wire re-render so the merged feed shows.
  runRefresh(refreshConflict);
}

// ============================================================
// COMMODITIES
// ============================================================
let LAST_COMMODITIES = null;
async function refreshCommodities() {
  const { body, fresh } = await fgetJSON('/api/commodities');
  recordFreshness('commodities', fresh);
  if (body && body.items) LAST_COMMODITIES = body.items;
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
  recordFreshness('earthquakes', fresh);
  if (!body || !body.features) {
    // Don't blank the panel on transient errors --- keep whatever was there.
    // The initial loading placeholder is itself a child, so checking firstChild
    // left first-run failures stuck on "Loading USGS…" forever. Preserve a
    // rendered data set, but replace the placeholder when no valid response has
    // ever been painted.
    if (fresh === 'error' && !LAST_USGS) {
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
    const quakeUrl = safeHttpUrl(p.url);
    if (quakeUrl) {
      row.addEventListener('click', () => window.open(quakeUrl, '_blank', 'noopener'));
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
    const popupUrl = safeHttpUrl(f.properties.url);
    L.circleMarker([lat, lon], {
      radius: Math.max(3, mag * 2), color: '#ff8a3d',
      fillColor: '#ff8a3d', fillOpacity: 0.55, weight: 1,
      bubblingMouseEvents: false,
    }).bindPopup(`<b>M ${mag.toFixed(1)}</b><br>${escapeHtml(f.properties.place || 'Unknown')}<br>` +
                 `<small>${new Date(f.properties.time).toISOString().replace('T',' ').slice(0,16)} UTC</small>` +
                 (popupUrl ? `<br><a href="${escapeHtml(popupUrl)}" target="_blank" rel="noopener noreferrer">USGS &rarr;</a>` : ''))
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
// Conflict watch
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
  recordFreshness('aircraft', fresh);
  if (!body) return;
  const ac = body.ac || body.aircraft || [];
  LAYERS.flights.clearLayers();
  for (const a of ac.slice(0, 250)) {
    if (a.lat == null || a.lon == null) continue;
    const rawHeading = a.track != null ? a.track : (a.true_heading != null ? a.true_heading : 0);
    const heading = Number.isFinite(Number(rawHeading)) ? Number(rawHeading) : 0;
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
  recordFreshness('firms', fresh);
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
  recordFreshness('gdacs', fresh);
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
  runRefresh(refreshCyclones);
}

function gdacsRowHtml(d) {
  const rawClass = (d.alert || 'green').toLowerCase();
  const cls = ['green', 'orange', 'red'].includes(rawClass) ? rawClass : 'green';
  const t = GDACS_TYPE[d.etype] || d.etype || String(d.title || 'Event').slice(0, 6);
  const country = (d.country || '').slice(0, 18);
  const title = (d.title || '').replace(/^[A-Za-z ]+alert:\s*/i, '').slice(0, 80);
  const url = safeHttpUrl(d.link);
  const href = url ? ` href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"` : '';
  const tag = url ? 'a' : 'div';
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
  recordFreshness('space-weather', fresh);
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
  recordFreshness('eonet', fresh);
  if (!body || !body.events) return;
  LAYERS.eonet.clearLayers();
  for (const ev of body.events) {
    const s = eonetStyle(ev.cats);
    const radius = 4 + (ev.cats && ev.cats.includes('wildfires') ? 1 : 0);
    const eventUrl = safeHttpUrl(ev.link);
    L.circleMarker([ev.lat, ev.lon], {
      radius, color: s.color, fillColor: s.color,
      fillOpacity: 0.55, weight: 1,
      bubblingMouseEvents: false,
    }).bindPopup(`
      <div style="min-width:200px;max-width:320px">
        <b style="color:${s.color};text-transform:uppercase;letter-spacing:0.06em;font-size:11px">${escapeHtml(s.label)}</b><br>
        <span style="font-size:11.5px">${escapeHtml(ev.title || '')}</span><br>
        <small style="color:#7e8aa3">${ev.date ? escapeHtml(ev.date.slice(0,16).replace('T',' ') + ' UTC') : ''}</small>
        ${eventUrl ? `<br><a href="${escapeHtml(eventUrl)}" target="_blank" rel="noopener noreferrer" style="font-size:11px">Source →</a>` : ''}
      </div>
    `).addTo(LAYERS.eonet);
  }
}

// ============================================================
// CONFLICT HOTSPOTS (map overlay)
// ============================================================

async function refreshConflictHotspots() {
  const { body, fresh } = await fgetJSON('/api/conflict-hotspots');
  recordFreshness('conflict-hotspots', fresh);
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
      const url = safeHttpUrl(r.link);
      const tag = url ? 'a' : 'div';
      const href = url ? ` href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"` : '';
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
  const { body, fresh } = await fgetJSON('/api/conflict');
  setBadge('bd-conflict', fresh);
  recordFreshness('conflict', fresh);
  let arts = (body && body.articles) || [];
  // Merge the Defense Wire articles so the panel is a single unified intel
  // stream covering both open-source conflict news and military-defense
  // publications.
  if (LAST_DEFENSE && LAST_DEFENSE.length) {
    arts = arts.concat(LAST_DEFENSE);
    arts.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  }
  // Theater filter: when a theater is active, hide items whose title doesn't match.
  if (APP_STORE.state.ui.theater !== 'global') {
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
    const url = safeHttpUrl(a.link);
    const tag = url ? 'a' : 'div';
    const href = url ? ` href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"` : '';
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
  recordFreshness('weather', fresh);
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
  const [cycloneResult, volcanoResult, tsunamiResult] = await Promise.all([
    fgetJSON('/api/cyclones'),
    fgetJSON('/api/volcanoes-real'),
    fgetJSON('/api/tsunami'),
  ]);
  const { body, fresh } = cycloneResult;
  const hazardFresh = combineFreshness([
    fresh, volcanoResult.fresh, tsunamiResult.fresh,
  ]);
  setBadge('bd-cyclones', hazardFresh);
  recordFreshness('major-hazards', hazardFresh);
  LAYERS.cyclones.clearLayers();
  const out = $('body-cyclones');
  const storms = (body && body.activeStorms) || [];
  const volcanoes = (volcanoResult.body && volcanoResult.body.items) || [];
  const tsunamis = (tsunamiResult.body && tsunamiResult.body.items) || [];
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
        }).bindPopup(`<b>${escapeHtml(s.name || s.id || 'Storm')}</b><br>${escapeHtml(s.classification || '')}<br><small>winds ${intensity} kt</small>`)
          .addTo(LAYERS.cyclones);
      }
    }
  }

  if (volcanoes.length) {
    any = true;
    out.appendChild(el('div', 'hazard-section', 'VOLCANO ACTIVITY'));
    for (const volcano of volcanoes.slice(0, 6)) {
      const row = el('div', 'row clickable');
      row.appendChild(el('div', 'when', 'VOL'));
      const lbl = el('div', 'label');
      lbl.appendChild(el('span', 'title', volcano.name || 'Volcano notice'));
      lbl.appendChild(el('span', 'sub', (volcano.summary || '').slice(0, 100)));
      row.appendChild(lbl);
      const url = safeHttpUrl(volcano.link);
      if (url) row.addEventListener('click', () => window.open(url, '_blank', 'noopener'));
      out.appendChild(row);
      if (Number.isFinite(Number(volcano.lat)) && Number.isFinite(Number(volcano.lon))) {
        L.circleMarker([Number(volcano.lat), Number(volcano.lon)], {
          radius: 6, color: '#ff5a4d', fillColor: '#ff5a4d',
          fillOpacity: 0.55, weight: 1.2, bubblingMouseEvents: false,
        }).bindPopup(`<b>${escapeHtml(volcano.name || 'Volcano notice')}</b><br>` +
                     `<small>${escapeHtml((volcano.summary || '').slice(0, 180))}</small>`)
          .addTo(LAYERS.cyclones);
      }
    }
  }

  if (tsunamis.length) {
    any = true;
    out.appendChild(el('div', 'hazard-section', 'TSUNAMI NOTICES'));
    for (const notice of tsunamis.slice(0, 6)) {
      const row = el('div', 'row');
      const when = notice.ts ? fmtTime(new Date(notice.ts * 1000)) : '--';
      row.appendChild(el('div', 'when', when));
      const lbl = el('div', 'label');
      lbl.appendChild(el('span', 'title', notice.title || 'Tsunami notice'));
      lbl.appendChild(el('span', 'sub', (notice.summary || '').slice(0, 100)));
      row.appendChild(lbl);
      row.appendChild(el('div', 'right', notice.source || 'TS'));
      out.appendChild(row);
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
      const quakeUrl = safeHttpUrl(p.url);
      if (quakeUrl) row.addEventListener('click', () => window.open(quakeUrl, '_blank', 'noopener'));
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
  recordFreshness('relief', fresh);
  let items = (body && body.articles) || [];
  if (APP_STORE.state.ui.theater !== 'global') {
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
    const url = safeHttpUrl(it.link);
    const tag = url ? 'a' : 'div';
    const href = url ? ` href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"` : '';
    html += `<${tag} class="ln rlf${isWatch ? ' watch' : ''}"${href}><span class="t">${t}</span><span class="who">${escapeHtml((country || 'RW').slice(0,10))}</span><span class="ttl">${escapeHtml(rest)}</span></${tag}>`;
  }
  fillStream($('body-relief'), html);
  checkAndAlert(items, 'SITREPS');
}
// ============================================================
// OPTIONAL COMMUNITY CONTROLLERS
// ============================================================

let LAST_BTC_PRICE = null;
const { refreshBitcoin, refreshWiki, refreshGitHub } = createCommunityControllers({
  store: APP_STORE,
  byId: $,
  getJSON: fgetJSON,
  setBadge,
  recordFreshness,
  fillStream,
  elapsed: ago,
  formatTime: fmtTime,
  escapeHtml,
  safeHttpUrl,
  playCue,
  getBitcoinPrice: () => LAST_BTC_PRICE,
});



// ============================================================
// ISS TRACKER
// ============================================================

async function refreshISS() {
  if (!APP_STORE.state.ui.panels.iss) {
    if (ISS_MARKER) { LAYERS.iss.removeLayer(ISS_MARKER); ISS_MARKER = null; }
    return;
  }
  const { body, fresh } = await fgetJSON('/api/iss');
  recordFreshness('iss', fresh);
  if (!body || !body.iss_position) return;
  const lat = parseFloat(body.iss_position.latitude);
  const lon = parseFloat(body.iss_position.longitude);
  if (isNaN(lat) || isNaN(lon)) return;
  $('iss-readout').textContent = `ISS · ${lat.toFixed(2)}, ${lon.toFixed(2)}`;
  if (!ISS_MARKER) {
    ISS_MARKER = L.marker([lat, lon], {
      icon: pulseIcon('iss', 14),
      title: 'International Space Station current position',
    });
    ISS_MARKER.on('click', e => L.DomEvent.stopPropagation(e));
    ISS_MARKER.addTo(LAYERS.iss);
  } else {
    ISS_MARKER.setLatLng([lat, lon]);
  }
}
// ============================================================
// OPTIONAL TICKER CONTROLLERS
// ============================================================

const { refreshSEC, refreshTalk, refreshCrypto, refreshForex, refreshNews } =
  createTickerControllers({
    store: APP_STORE,
    byId: $,
    request: fget,
    getJSON: fgetJSON,
    setBadge,
    recordFreshness,
    fillStream,
    combineFreshness,
    formatTime: fmtTime,
    escapeHtml,
    safeHttpUrl,
    getCommodities: () => LAST_COMMODITIES,
    setBitcoinPrice: value => { LAST_BTC_PRICE = value; },
  });



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
    const b = el('button', 'tv-tab' + (c.id === APP_STORE.state.ui.tvChannel ? ' active' : ''), c.label);
    b.dataset.id = c.id;
    b.addEventListener('click', () => switchTv(c.id));
    wrap.appendChild(b);
  }
}

let TV_STARTED = false;
function switchTv(channelId) {
  const ch = TV_CHANNELS.find(c => c.id === channelId);
  if (!ch) return;
  APP_STORE.state.ui.tvChannel = channelId;
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
  const ch = TV_CHANNELS.find(c => c.id === APP_STORE.state.ui.tvChannel) || TV_CHANNELS[0];
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


function applyPanelVisibility() {
  for (const p of PANEL_DEFS) {
    if (!p.node) continue;
    const el = $(p.node);
    if (el) el.classList.toggle('hidden', !APP_STORE.state.ui.panels[p.id]);
  }
  $('iss-readout').style.display = APP_STORE.state.ui.panels.iss ? '' : 'none';
  // ISS toggle also drops the map marker.
  if (LAYERS && LAYERS.iss && !APP_STORE.state.ui.panels.iss && ISS_MARKER) {
    LAYERS.iss.removeLayer(ISS_MARKER);
    ISS_MARKER = null;
  }
  renderOptionalCta();
}

// Show a discoverable CTA at the bottom strip when one or more of the
// optional panels (Bitcoin / Wikipedia / GitHub / SEC / HN+Reddit) are off.
function renderOptionalCta() {
  const optionalIds = ['btc', 'wiki', 'github', 'sec', 'talk'];
  const hidden = optionalIds.filter(id => !APP_STORE.state.ui.panels[id]);
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
    // Keep the CTA in the bottom grid row so it cannot cover map attribution.
    if (!cta) {
      cta = document.createElement('div');
      cta.id = 'optional-cta';
      cta.addEventListener('click', () => { openSettings(); scrollSettingsTo('panels'); });
      main.appendChild(cta);
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
    const t = el('button', 'toggle' + (APP_STORE.state.ui.panels[p.id] ? ' on' : ''));
    t.type = 'button';
    t.setAttribute('aria-label', `Toggle ${p.label}`);
    t.setAttribute('aria-pressed', String(!!APP_STORE.state.ui.panels[p.id]));
    t.addEventListener('click', () => togglePanel(p.id, t));
    row.appendChild(t);
    wrap.appendChild(row);
  }
}

async function togglePanel(id, toggleEl) {
  APP_STORE.state.ui.panels[id] = !APP_STORE.state.ui.panels[id];
  toggleEl.classList.toggle('on', APP_STORE.state.ui.panels[id]);
  toggleEl.setAttribute('aria-pressed', String(!!APP_STORE.state.ui.panels[id]));
  applyPanelVisibility();
  // Persist
  fget('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ panels: { [id]: APP_STORE.state.ui.panels[id] } }),
  }).catch(() => {});
  // Refresh the panel's data if we just enabled it.
  if (APP_STORE.state.ui.panels[id]) {
    if (id === 'wiki')     runRefresh(refreshWiki);
    if (id === 'github')   runRefresh(refreshGitHub);
    if (id === 'sec')      runRefresh(refreshSEC);
    if (id === 'talk')     runRefresh(refreshTalk);
    if (id === 'cyclones') runRefresh(refreshCyclones);
    if (id === 'relief')   runRefresh(refreshRelief);
    if (id === 'iss')      runRefresh(refreshISS);
    if (id === 'btc')      runRefresh(refreshBitcoin);
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
    const b = el('button', '', c.id === APP_STORE.state.ui.tvChannel ? 'Default' : 'Make default');
    b.addEventListener('click', () => { switchTv(c.id); renderTvChannelPicker(); });
    if (c.id === APP_STORE.state.ui.tvChannel) b.style.borderColor = 'var(--good)';
    row.appendChild(b);
    wrap.appendChild(row);
  }
}

// ============================================================
// AUDIO CUES
// ============================================================

let AUDIO_CTX = null;
function audioOn(kind) { return APP_STORE.state.user.audio.master && APP_STORE.state.user.audio[kind]; }
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
}

// ============================================================
// SETTINGS
// ============================================================

let SETTINGS_RETURN_FOCUS = null;

function setPrimarySurfacesInert(inert) {
  for (const id of ['topbar', 'statusbar', 'theaterbar', 'overview-surface', 'main']) {
    const node = $(id);
    if (node) node.inert = inert;
  }
}

function settingsFocusables() {
  return [
    $('settings-close'),
    ...$('pane-settings').querySelectorAll(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), a[href]',
    ),
  ].filter(node => node && node.getClientRects().length);
}

function openSettings() {
  SETTINGS_RETURN_FOCUS = document.activeElement;
  $('pane-settings').classList.add('show');
  $('pane-settings').setAttribute('aria-hidden', 'false');
  $('settings-close').classList.add('show');
  setPrimarySurfacesInert(true);
  refreshSettings();
  requestAnimationFrame(() => $('settings-close').focus());
}
function closeSettings() {
  $('pane-settings').classList.remove('show');
  $('pane-settings').setAttribute('aria-hidden', 'true');
  $('settings-close').classList.remove('show');
  setPrimarySurfacesInert(false);
  if (SETTINGS_RETURN_FOCUS?.isConnected) SETTINGS_RETURN_FOCUS.focus();
  SETTINGS_RETURN_FOCUS = null;
}
// Global Esc key handler --- closes whichever drawer / overlay is open.
document.addEventListener('keydown', (e) => {
  if (e.key === 'Tab' && $('pane-settings').classList.contains('show')) {
    const nodes = settingsFocusables();
    if (!nodes.length) return;
    const current = nodes.indexOf(document.activeElement);
    if (e.shiftKey && current <= 0) {
      e.preventDefault();
      nodes[nodes.length - 1].focus();
    } else if (!e.shiftKey && current === nodes.length - 1) {
      e.preventDefault();
      nodes[0].focus();
    }
    return;
  }
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
  APP_STORE.state.lifecycle.settings = body;
  if (!body) return;
  const normalized = normalizeInitialSettings(body, {
    panelIds: PANEL_DEFS.map(panel => panel.id),
    defaultVisible: new Set(['tv', 'conflict', 'cyclones', 'relief', 'iss']),
    tvChannelIds: new Set(TV_CHANNELS.map(channel => channel.id)),
  });
  APP_STORE.update('user', {
    watchlist: normalized.watchlist,
    watchRegions: normalized.watchRegions,
    notifications: normalized.notifications,
    notificationState: normalized.notificationState,
    wallDisplay: normalized.wallDisplay,
  });
  WATCH_CENTER_CONTROLLER?.refreshSettings();
  for (const k of Object.keys(body.keys || {})) {
    const el2 = $('ks-' + k);
    if (el2) {
      el2.textContent = body.keys[k] ? 'configured' : 'not set';
      el2.classList.toggle('on', !!body.keys[k]);
    }
    const input = $('key-' + k);
    if (input) input.value = '';
  }
  APP_STORE.state.user.audio = Object.assign({}, body.audio || {});
  for (const k of Object.keys(body.audio || {})) {
    const t = $('aud-' + k);
    if (t) {
      t.classList.toggle('on', !!body.audio[k]);
      t.setAttribute('aria-pressed', String(!!body.audio[k]));
    }
  }
  // Render the panel toggle list (the visibility is already applied at boot).
  renderPanelToggles();
  renderTvChannelPicker();
  await refreshProviderAttributions();
}

async function refreshProviderAttributions() {
  const container = $('provider-attributions');
  if (!container || PROVIDER_CATALOG_LOADED) return;
  let body;
  let status = 0;
  try {
    ({ body, status } = await fgetJSON('/api/providers'));
  } catch {}
  const items = Array.isArray(body?.items)
    ? body.items.slice(0, 100).filter(item => item && typeof item === 'object')
    : [];
  if (status !== 200 || !items.length) {
    container.replaceChildren(el('div', 'empty', 'Source terms are temporarily unavailable.'));
    return;
  }
  container.replaceChildren();
  const groups = [
    ['Overview sources', items.filter(item => item?.overview === true)],
    ['Optional and compatibility sources', items.filter(item => item?.overview !== true)],
  ];
  for (const [label, sources] of groups) {
    if (!sources.length) continue;
    const section = el('section', 'provider-group');
    section.append(el('h3', '', `${label} (${sources.length})`));
    for (const source of sources) {
      const row = el('div', 'provider-source');
      const identity = el('div');
      identity.append(
        el('strong', '', String(source.attribution || source.id || 'Unknown source')),
        document.createElement('br'),
        el('code', '', String(source.id || 'unknown')),
      );
      row.append(identity);
      const termsUrl = safeHttpUrl(source.terms);
      if (termsUrl) {
        const link = el('a', '', 'Terms / policy');
        link.href = termsUrl;
        link.target = '_blank';
        link.rel = 'noopener';
        row.append(link);
      } else {
        row.append(el('span', '', 'Publisher terms'));
      }
      const auth = source.auth === 'none' ? 'No API key' : `Authentication: ${source.auth}`;
      row.append(el('div', 'provider-policy', `${auth} · ${source.decision || 'reviewed'}`));
      section.append(row);
    }
    container.append(section);
  }
  PROVIDER_CATALOG_LOADED = true;
}

async function saveKeys() {
  const patch = { keys: {} };
  for (const k of ['nasa_firms']) {
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
  const ok = await showDangerConfirm('Erase the stored NASA FIRMS key?', 'Clear key', 'Clear');
  if (!ok) return;
  const patch = { keys: {} };
  patch.keys.nasa_firms = '';
  await fget('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  await refreshSettings();
}

async function toggleAudio(kind) {
  const next = !APP_STORE.state.user.audio[kind];
  APP_STORE.state.user.audio[kind] = next;
  const t = $('aud-' + kind);
  if (t) {
    t.classList.toggle('on', next);
    t.setAttribute('aria-pressed', String(next));
  }
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

async function shutdownApp() {
  if (APP_STORE.state.lifecycle.shuttingDown) return;
  const ok = await showDangerConfirm(
    'This stops the local Foglight server and closes the desktop app.',
    'Shut down Foglight', 'Shut down');
  if (!ok) return;
  APP_STORE.state.lifecycle.shuttingDown = true;
  $('shutdown-overlay').classList.add('show');
  try {
    await fget('/api/shutdown', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  } catch {}
  setTimeout(() => { try { if (window.closeApp) window.closeApp(); } catch {} }, 350);
  setTimeout(() => { try { window.close(); } catch {} }, 1200);
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
      APP_STORE.update('lifecycle', { settings: body });
      const normalized = normalizeInitialSettings(body, {
        panelIds: PANEL_DEFS.map(panel => panel.id),
        defaultVisible: SITREP_ON,
        tvChannelIds: new Set(TV_CHANNELS.map(channel => channel.id)),
      });
      APP_STORE.update('ui', {
        panels: normalized.panels,
        tvChannel: normalized.tvChannel,
        displayMode: normalized.displayMode,
      });
      APP_STORE.update('user', {
        audio: normalized.audio,
        watchlist: normalized.watchlist,
        annotations: normalized.annotations,
        watchRegions: normalized.watchRegions,
        notifications: normalized.notifications,
        notificationState: normalized.notificationState,
        wallDisplay: normalized.wallDisplay,
      });
      setTimeout(() => {
        const textarea = $('watchlist-text');
        if (textarea) textarea.value = APP_STORE.state.user.watchlist.join('\n');
      }, 0);
    }
  } catch {}
}

function wireTheaterBar() {
  document.querySelectorAll('#theaterbar .t-btn').forEach(b => {
    b.addEventListener('click', () => switchTheater(b.dataset.theater));
  });
  document.querySelector('#theaterbar .t-btn[data-theater="global"]')?.classList.add('active');
}

function wireStaticControls() {
  $('action-settings').addEventListener('click', openSettings);
  $('action-speak').addEventListener('click', speakBriefing);
  $('action-brief').addEventListener('click', generateBriefing);
  $('action-fullscreen').addEventListener('click', toggleFullscreen);
  $('settings-close').addEventListener('click', closeSettings);
  $('save-watchlist').addEventListener('click', saveWatchlist);
  $('clear-annotations').addEventListener('click', clearAllAnnotations);
  $('save-keys').addEventListener('click', saveKeys);
  $('clear-keys').addEventListener('click', clearAllKeys);
  $('alert-close').addEventListener('click', closeAlertDrawer);
  document.querySelectorAll('.settings-back').forEach(button =>
    button.addEventListener('click', closeSettings));
  document.querySelectorAll('.js-shutdown').forEach(button =>
    button.addEventListener('click', shutdownApp));
  document.querySelectorAll('[data-audio]').forEach(button =>
    button.addEventListener('click', () => toggleAudio(button.dataset.audio)));
}

function runRefresh(fn) {
  return Promise.resolve().then(fn).catch(error => {
    console.warn(`[foglight] ${fn.name || 'refresh'} failed`, error);
    recordFreshness(fn.name || 'refresh', 'error');
  });
}

async function start() {
  wireStaticControls();
  await loadSession();
  await loadInitialSettings();

  let appConfig = {};
  try {
    const response = await fgetJSON('/api/app-config');
    if (response.status === 200 && response.body) appConfig = response.body;
  } catch {}
  OPEN_METEO_ENABLED = appConfig.open_meteo_enabled === true;
  YAHOO_FINANCE_ENABLED = appConfig.yahoo_finance_enabled === true;

  if (appConfig.overview_enabled) {
    initializeDisplayModes();
    WATCH_CENTER_CONTROLLER = createWatchCenterController({
      getJSON: fgetJSON,
      request: fget,
      store: APP_STORE,
      openIncident: (incidentId, opener) => OVERVIEW_CONTROLLER?.openIncident(incidentId, opener),
      printSelected: () => OVERVIEW_CONTROLLER?.printSelectedBriefing() || false,
      getIncidents: () => OVERVIEW_CONTROLLER?.getIncidents() || [],
      cycleIncident: incidentId => OVERVIEW_CONTROLLER?.cycleIncident(incidentId) || false,
      beginMapPick: () => OVERVIEW_CONTROLLER?.beginMapPick() || false,
      cancelMapPick: () => OVERVIEW_CONTROLLER?.cancelMapPick() || false,
    });
    OVERVIEW_CONTROLLER = createOverviewController({
      getJSON: fgetJSON,
      store: APP_STORE,
      onAddPin: addAnnotationFromForm,
      onIncidentChanges: changes => WATCH_CENTER_CONTROLLER.processChanges(changes),
      onSnapshot: value => WATCH_CENTER_CONTROLLER.updateSnapshot(value),
      onMapPick: value => WATCH_CENTER_CONTROLLER.useMapCoordinates(value),
    });
    WATCH_CENTER_CONTROLLER.start();
    window.__foglightWatchCenter = WATCH_CENTER_CONTROLLER;
    const isFirstRun = !APP_STORE.state.lifecycle.settings?.first_run_done;
    const initialOverviewLoad = OVERVIEW_CONTROLLER.start({ isFirstRun });
    const initialMode = ['overview', 'standard', 'command'].includes(APP_STORE.state.ui.displayMode)
      ? APP_STORE.state.ui.displayMode : appConfig.default_mode || 'overview';
    await setDisplayMode(initialMode, { persist: false, focus: false });
    if (isFirstRun) {
      initialOverviewLoad.then(success => {
        if (!success) return;
        fget('/api/settings', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ first_run_done: true }),
        }).catch(() => {});
      });
    }
    return;
  }

  APP_STORE.update('ui', { displayMode: 'standard' });
  document.body.className = 'mode-standard';
  await startStandardDashboard();
}

let STANDARD_STARTED = false;
let OVERVIEW_CONTROLLER = null;
let WATCH_CENTER_CONTROLLER = null;

async function startStandardDashboard() {
  if (STANDARD_STARTED) return;
  STANDARD_STARTED = true;
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

  initMap();
  applyPanelVisibility();

  // Live TV: render tabs and load the default channel muted (autoplay
  // works while muted in every modern browser). The overlay invites a
  // click to unmute, which gives us the user gesture needed for audio.
  renderTvTabs();
  const ch = TV_CHANNELS.find(c => c.id === APP_STORE.state.ui.tvChannel) || TV_CHANNELS[0];
  $('tv-frame').src = ytEmbedUrl(ch.ytChannel, true);
  setTvOpenLink(ch.ytChannel);
  $('tv-overlay-label').textContent = `Start ${ch.label}`;
  $('tv-overlay').addEventListener('click', startTvWithSound);

  // Wire the new chrome bars.
  wireTheaterBar();
  updateCapitalClocks();

  // Initial paint: focus panels (sitrep core).
  const standardRefreshes = [refreshQuakes, refreshConflictHotspots, refreshEonet,
   refreshFirms, refreshDefense, refreshWeather,
   refreshConflict, refreshGdacs, refreshCyclones, refreshRelief,
   refreshSpaceWeather, refreshISS, refreshNews, refreshCrypto,
   refreshForex];
  if (YAHOO_FINANCE_ENABLED) standardRefreshes.push(refreshCommodities);
  // Optional panels: only fetch if visible.
  const optionalRefreshes = [refreshBitcoin, refreshWiki, refreshGitHub, refreshSEC,
   refreshTalk, refreshSettings];
  void runWithConcurrency(standardRefreshes, runRefresh, 3)
    .then(() => runWithConcurrency(optionalRefreshes, runRefresh, 2))
    .then(() => {
      // A short, cache-friendly recovery pass prevents a transient network
      // outage at launch from leaving slow-cadence panels dead for minutes.
      setTimeout(() => {
        void runWithConcurrency(standardRefreshes, runRefresh, 3);
      }, 15 * 1000);
    });

  // Per-panel cadence (slower than v1 --- this is a "leave open" app).
  const every = (fn, ms) => setInterval(() => runRefresh(fn), ms);
  every(refreshQuakes,            120 * 1000);
  every(refreshConflictHotspots,  240 * 1000);
  every(refreshEonet,             600 * 1000);
  every(refreshWeather,           180 * 1000);
  every(refreshConflict,          240 * 1000);
  every(refreshGdacs,             300 * 1000);
  every(refreshCyclones,          600 * 1000);
  every(refreshRelief,            300 * 1000);
  every(refreshSpaceWeather,      900 * 1000);
  every(refreshBitcoin,            45 * 1000);
  every(refreshWiki,               10 * 1000);
  every(refreshGitHub,             45 * 1000);
  every(refreshISS,                10 * 1000);
  every(refreshSEC,               180 * 1000);
  every(refreshTalk,              180 * 1000);
  every(refreshCrypto,            120 * 1000);
  every(refreshForex,        60 * 60 * 1000);
  every(refreshNews,              240 * 1000);
  // Community aircraft positions remain an explicit experimental seam. They
  // are not fetched or scheduled by the zero-configuration default surface.
  every(refreshFirms,             900 * 1000);
  every(refreshDefense,           300 * 1000);
  if (YAHOO_FINANCE_ENABLED) every(refreshCommodities, 300 * 1000);
}

function initializeDisplayModes() {
  const navigation = $('display-modes');
  navigation.hidden = false;
  for (const button of navigation.querySelectorAll('[data-display-mode]')) {
    button.setAttribute('aria-pressed', 'false');
    button.addEventListener('click', () => {
      setDisplayMode(button.dataset.displayMode, { persist: true, focus: 'heading' });
    });
  }
  navigation.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const modes = ['overview', 'standard', 'command'];
    const current = modes.indexOf(APP_STORE.state.ui.displayMode);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? modes.length - 1
      : (current + (event.key === 'ArrowRight' ? 1 : -1) + modes.length) % modes.length;
    setDisplayMode(modes[next], { persist: true, focus: 'button' });
  });
}

async function setDisplayMode(mode, { persist = true, focus = 'heading' } = {}) {
  if (!['overview', 'standard', 'command'].includes(mode)) mode = 'overview';
  APP_STORE.update('ui', { displayMode: mode });
  document.body.classList.remove('mode-overview', 'mode-standard', 'mode-command');
  document.body.classList.add(`mode-${mode}`);
  for (const button of document.querySelectorAll('[data-display-mode]')) {
    const active = button.dataset.displayMode === mode;
    button.setAttribute('aria-pressed', String(active));
    button.tabIndex = active ? 0 : -1;
    if (active && focus === 'button') button.focus();
  }
  if (mode === 'standard') {
    OVERVIEW_CONTROLLER?.closeDrawer();
    WATCH_CENTER_CONTROLLER?.stop();
    await startStandardDashboard();
  }
  else {
    OVERVIEW_CONTROLLER?.render();
    OVERVIEW_CONTROLLER?.activateMap();
  }
  if (persist) {
    fget('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_mode: mode }),
    }).catch(() => {});
  }
  if (focus === 'heading' && mode !== 'standard') $('overview-title').focus();
}

window.generateBriefing = generateBriefing;

window.openSettings = openSettings;
window.closeSettings = closeSettings;
window.saveKeys = saveKeys;
window.clearAllKeys = clearAllKeys;
window.toggleAudio = toggleAudio;
window.shutdownApp = shutdownApp;

document.addEventListener('DOMContentLoaded', () => {
  start().catch(error => {
    console.error('[foglight] startup failed', error);
    const banner = $('breaking-banner');
    if (banner) {
      banner.textContent = 'Foglight failed to start. Check the local runtime log.';
      banner.classList.add('show');
    }
  });
});
