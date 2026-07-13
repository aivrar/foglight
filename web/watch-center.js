import { element } from './core.js';
import {
  createPointWatch,
  incidentsToCsv,
  incidentsToGeoJSON,
  notificationCandidates,
  normalizeWatchRegions,
  offlineHistorySummary,
} from './watch-model.js';

const KIND_OPTIONS = Object.freeze([
  ['earthquake', 'Earthquake'], ['weather_alert', 'Weather warning'],
  ['tropical_cyclone', 'Tropical cyclone'], ['tsunami', 'Tsunami'],
  ['volcano', 'Volcano'], ['wildfire', 'Wildfire'], ['natural_event', 'Natural event'],
  ['disaster', 'Disaster'], ['conflict_report', 'Conflict report'],
  ['disaster_declaration', 'FEMA declaration'],
  ['humanitarian_report', 'Humanitarian report'], ['aircraft', 'Aircraft'],
  ['aviation_hazard', 'Aviation hazard'],
  ['marine_observation', 'Marine observation'], ['water_level', 'Water level'],
  ['fireball', 'Fireball observation'],
  ['space_weather', 'Space weather'], ['orbital_position', 'Orbital position'],
  ['market_snapshot', 'Market signal'], ['technology_activity', 'Internet signal'],
  ['news_item', 'News item'],
]);
const NOTIFICATION_KEY_LIMIT = 400;

function append(parent, tag, className, value) {
  const node = element(tag, className, value);
  parent.appendChild(node);
  return node;
}

function ageLabel(seconds) {
  if (!Number.isFinite(seconds)) return 'age unavailable';
  if (seconds < 60) return `${Math.floor(seconds)}s old`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m old`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h old`;
  return `${Math.floor(seconds / 86400)}d old`;
}

function utcLabel(value) {
  const parsed = Date.parse(value || '');
  return Number.isFinite(parsed)
    ? new Date(parsed).toISOString().replace('T', ' ').replace('.000Z', ' UTC')
    : 'not available';
}

export function createWatchCenterController({
  getJSON,
  request,
  store,
  openIncident = () => false,
  printSelected = () => false,
  getIncidents = () => [],
  cycleIncident = () => false,
  beginMapPick = () => false,
  cancelMapPick = () => false,
  now = () => Date.now(),
}) {
  let snapshot = { incidents: [], health: { counts: {}, sources: [] }, revisionCursor: 0 };
  let alerts = [];
  let searchVersion = 0;
  let wallEnabled = false;
  let wallPaused = false;
  let wallIndex = -1;
  let wallTimer = null;
  let started = false;
  let regionSaving = false;
  let notificationSaving = false;
  let notificationQueue = Promise.resolve();
  const reducedMotion = () => globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;

  const byId = id => document.getElementById(id);

  async function persist(patch) {
    const response = await request('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!response.ok) throw new Error(`settings write failed (${response.status})`);
    return response.json();
  }

  function renderRegions() {
    const list = byId('watch-region-list');
    list.replaceChildren();
    for (const region of store.state.user.watchRegions) {
      const row = append(list, 'li', '', '');
      append(row, 'strong', '', region.label);
      const location = region.scope === 'global'
        ? 'Global migrated keyword watch'
        : region.geometry?.type === 'Point'
          ? `${region.geometry.coordinates[1].toFixed(3)}, ${region.geometry.coordinates[0].toFixed(3)} · ${region.radius_km} km`
          : `${region.geometry?.type || 'Region'} geometry`;
      append(row, 'span', '', `${location} · ${region.minimum_severity}+${region.kinds.length ? ` · ${region.kinds.join(', ')}` : ''}`);
      if (region.keywords.length) append(row, 'span', '', `Keywords: ${region.keywords.join(', ')}`);
      const actions = append(row, 'div', 'watch-region-actions', '');
      const toggle = append(actions, 'button', '', region.enabled ? 'Disable' : 'Enable');
      toggle.type = 'button';
      toggle.dataset.watchToggle = region.id;
      const remove = append(actions, 'button', '', 'Remove');
      remove.type = 'button';
      remove.dataset.watchRemove = region.id;
      if (region.id === 'legacy:keywords') {
        remove.textContent = 'Legacy keywords retained';
        remove.disabled = true;
      }
    }
    if (!list.childElementCount) append(list, 'li', '', 'No watch regions. Alerts remain off until a region is added.');
  }

  async function saveRegions(next) {
    if (new TextEncoder().encode(JSON.stringify(next)).length > 180 * 1024) {
      throw new Error('watch regions exceed the local settings size budget');
    }
    store.update('user', { watchRegions: next });
    renderRegions();
    const saved = await persist({ watch_regions: next });
    const canonical = normalizeWatchRegions(saved?.watch_regions, saved?.watchlist);
    store.update('user', { watchRegions: canonical });
    renderRegions();
    return canonical;
  }

  async function addRegion(event) {
    event.preventDefault();
    const status = byId('watch-region-status');
    if (regionSaving) {
      status.textContent = 'A watch-region change is already being saved.';
      return;
    }
    if (store.state.user.watchRegions.filter(
      region => region.id !== 'legacy:keywords',
    ).length >= 50) {
      status.textContent = 'Foglight stores up to 50 watch regions.';
      return;
    }
    const kind = byId('watch-region-kind').value;
    const region = createPointWatch({
      label: byId('watch-region-label').value,
      latitude: byId('watch-region-lat').value,
      longitude: byId('watch-region-lon').value,
      radiusKm: byId('watch-region-radius').value,
      kinds: kind ? [kind] : [],
      minimumSeverity: byId('watch-region-severity').value,
      keywords: byId('watch-region-keywords').value.split(',').map(item => item.trim()).filter(Boolean),
    });
    if (!region) {
      status.textContent = 'Enter a label and valid latitude/longitude.';
      return;
    }
    const previous = store.state.user.watchRegions;
    regionSaving = true;
    try {
      await saveRegions([...previous, region]);
      byId('watch-region-form').reset();
      byId('watch-region-radius').value = '100';
      byId('watch-region-severity').value = 'Moderate';
      status.textContent = `Watch region “${region.label}” saved locally.`;
    } catch {
      store.update('user', { watchRegions: previous });
      renderRegions();
      status.textContent = 'The watch region could not be saved.';
    } finally {
      regionSaving = false;
    }
  }

  async function regionAction(event) {
    const toggle = event.target.closest?.('[data-watch-toggle]');
    const remove = event.target.closest?.('[data-watch-remove]');
    if (!toggle && !remove) return;
    if (regionSaving) {
      byId('watch-region-status').textContent = 'A watch-region change is already being saved.';
      return;
    }
    const id = toggle?.dataset.watchToggle || remove?.dataset.watchRemove;
    const previous = store.state.user.watchRegions;
    const next = remove
      ? previous.filter(item => item.id !== id)
      : previous.map(item => item.id === id ? { ...item, enabled: !item.enabled } : item);
    regionSaving = true;
    try {
      await saveRegions(next);
    } catch {
      store.update('user', { watchRegions: previous });
      renderRegions();
      byId('watch-region-status').textContent = 'The watch change could not be saved.';
    } finally {
      regionSaving = false;
    }
  }

  function renderKindSettings() {
    const container = byId('notification-kind-list');
    container.replaceChildren();
    const selected = new Set(store.state.user.notifications.kinds || []);
    for (const [id, label] of KIND_OPTIONS) {
      const wrapper = append(container, 'label', '', '');
      const input = append(wrapper, 'input', '', '');
      input.type = 'checkbox';
      input.value = id;
      input.checked = selected.has(id);
      wrapper.append(label);
    }
  }

  function renderNotificationSettings() {
    const config = store.state.user.notifications;
    byId('notification-in-app').checked = config.in_app !== false;
    byId('notification-system').checked = config.system !== false;
    byId('notification-quiet-start').value = config.quiet_start || '22:00';
    byId('notification-quiet-end').value = config.quiet_end || '07:00';
    byId('notification-minimum-severity').value = config.minimum_severity || 'Moderate';
    renderKindSettings();
    let permission = 'unavailable';
    try {
      if (typeof Notification !== 'undefined') permission = Notification.permission;
    } catch { permission = 'unavailable'; }
    byId('notification-permission-status').textContent = config.enabled
      ? `Alerts enabled. Windows notification permission: ${permission}; in-app fallback remains available.`
      : 'Notifications are off until you enable them.';
  }

  function readNotificationSettings(enabled = store.state.user.notifications.enabled === true) {
    return {
      ...store.state.user.notifications,
      enabled,
      in_app: byId('notification-in-app').checked,
      system: byId('notification-system').checked,
      quiet_start: byId('notification-quiet-start').value || '22:00',
      quiet_end: byId('notification-quiet-end').value || '07:00',
      minimum_severity: byId('notification-minimum-severity').value,
      kinds: [...byId('notification-kind-list').querySelectorAll('input:checked')].map(item => item.value),
      changes: ['new', 'escalated'],
    };
  }

  async function saveNotificationConfig(config) {
    store.update('user', { notifications: config });
    await persist({ notifications: config });
    renderNotificationSettings();
  }

  async function enableNotifications() {
    const status = byId('notification-permission-status');
    if (notificationSaving) {
      status.textContent = 'Notification settings are already being saved.';
      return;
    }
    const previous = store.state.user.notifications;
    let permission = 'unavailable';
    notificationSaving = true;
    try {
      if (typeof Notification !== 'undefined') {
        permission = Notification.permission === 'default'
          ? await Notification.requestPermission() : Notification.permission;
      }
      await saveNotificationConfig(readNotificationSettings(true));
      status.textContent = permission === 'granted'
        ? 'Notifications enabled. Windows and in-app delivery are available.'
        : `Notifications enabled. Windows permission is ${permission}; using the in-app fallback.`;
    } catch {
      store.update('user', { notifications: previous });
      renderNotificationSettings();
      status.textContent = 'Notification permission could not be enabled; alerts remain off.';
    } finally {
      notificationSaving = false;
    }
  }

  async function disableNotifications() {
    if (notificationSaving) return;
    const previous = store.state.user.notifications;
    notificationSaving = true;
    try {
      await saveNotificationConfig(readNotificationSettings(false));
    } catch {
      store.update('user', { notifications: previous });
      renderNotificationSettings();
      byId('notification-permission-status').textContent = 'Notifications could not be disabled.';
    } finally {
      notificationSaving = false;
    }
  }

  async function saveNotificationSettings() {
    if (notificationSaving) return;
    const previous = store.state.user.notifications;
    notificationSaving = true;
    try {
      await saveNotificationConfig(readNotificationSettings());
    } catch {
      store.update('user', { notifications: previous });
      renderNotificationSettings();
      byId('notification-permission-status').textContent = 'Alert settings could not be saved.';
    } finally {
      notificationSaving = false;
    }
  }

  function renderAlerts() {
    const list = byId('notification-center-list');
    list.replaceChildren();
    for (const alert of alerts) {
      const row = append(list, 'li', '', '');
      append(row, 'strong', '', alert.incident.headline || 'Untitled incident');
      append(row, 'span', '', `${alert.change_type.replaceAll('_', ' ')} · ${alert.incident.severity || 'Unknown'} · ${alert.watch_labels.join(', ')}`);
      const actions = append(row, 'div', 'notification-actions', '');
      const open = append(actions, 'button', '', 'Open');
      open.type = 'button';
      open.dataset.alertOpen = alert.incident.incident_id;
      const acknowledge = append(actions, 'button', '', 'Acknowledge');
      acknowledge.type = 'button';
      acknowledge.dataset.alertAcknowledge = alert.key;
      const snooze = append(actions, 'button', '', 'Snooze 1 hour');
      snooze.type = 'button';
      snooze.dataset.alertSnooze = alert.incident.incident_id;
    }
    byId('notification-center-empty').hidden = alerts.length > 0;
  }

  async function saveNotificationState(next) {
    store.update('user', { notificationState: next });
    await persist({ notification_state: next });
  }

  async function processChangesNow(changes) {
    const candidates = notificationCandidates({
      changes,
      regions: store.state.user.watchRegions,
      config: store.state.user.notifications,
      state: store.state.user.notificationState,
      now: now(),
    });
    if (!candidates.length) return [];
    const previous = store.state.user.notificationState;
    const next = {
      ...previous,
      seen_revision_keys: [...new Set([
        ...(previous.seen_revision_keys || []), ...candidates.map(item => item.key),
      ])].slice(-NOTIFICATION_KEY_LIMIT),
    };
    try {
      await saveNotificationState(next);
    } catch {
      store.update('user', { notificationState: previous });
      return [];
    }
    for (const candidate of candidates) {
      let systemDelivered = false;
      if (store.state.user.notifications.system !== false
          && typeof Notification !== 'undefined') {
        try {
          if (Notification.permission === 'granted') {
            const notification = new Notification(candidate.incident.headline || 'Foglight alert', {
              body: `${candidate.change_type.replaceAll('_', ' ')} · ${candidate.incident.severity || 'Unknown'} · ${candidate.watch_labels.join(', ')}`,
              tag: candidate.key,
              renotify: false,
            });
            notification.onclick = () => openIncident(candidate.incident.incident_id);
            systemDelivered = true;
          }
        } catch { systemDelivered = false; }
      }
      if (store.state.user.notifications.in_app !== false || !systemDelivered) alerts.unshift(candidate);
    }
    alerts = alerts.slice(0, 50);
    renderAlerts();
    return candidates;
  }

  function enqueueNotification(operation) {
    const pending = notificationQueue.then(operation, operation);
    notificationQueue = pending.catch(() => {});
    return pending;
  }

  function processChanges(changes) {
    return enqueueNotification(() => processChangesNow(changes));
  }

  function alertAction(event) {
    const open = event.target.closest?.('[data-alert-open]');
    const acknowledge = event.target.closest?.('[data-alert-acknowledge]');
    const snooze = event.target.closest?.('[data-alert-snooze]');
    if (open) {
      openIncident(open.dataset.alertOpen, open);
      return;
    }
    if (!acknowledge && !snooze) return;
    return enqueueNotification(async () => {
      const previous = store.state.user.notificationState;
      const previousAlerts = alerts.slice();
      let next;
      if (acknowledge) {
        const key = acknowledge.dataset.alertAcknowledge;
        next = {
          ...previous,
          acknowledged_keys: [...new Set([
            ...(previous.acknowledged_keys || []), key,
          ])].slice(-NOTIFICATION_KEY_LIMIT),
        };
        alerts = alerts.filter(item => item.key !== key);
      } else {
        const incidentId = snooze.dataset.alertSnooze;
        const until = new Date(now() + 3_600_000).toISOString();
        next = {
          ...previous,
          snoozed: [
            ...(previous.snoozed || []).filter(item => item.incident_id !== incidentId),
            { incident_id: incidentId, until },
          ].slice(-200),
        };
        alerts = alerts.filter(item => item.incident.incident_id !== incidentId);
      }
      renderAlerts();
      try { await saveNotificationState(next); } catch {
        store.update('user', { notificationState: previous });
        alerts = previousAlerts;
        renderAlerts();
      }
    });
  }

  async function search(event) {
    event.preventDefault();
    const query = byId('incident-search-query').value.replace(/\s+/g, ' ').trim();
    const status = byId('incident-search-status');
    const list = byId('incident-search-results');
    if (query.length < 2) {
      status.textContent = 'Enter at least two characters.';
      list.replaceChildren();
      return;
    }
    const version = ++searchVersion;
    status.textContent = 'Searching local retained history…';
    try {
      const response = await getJSON(`/api/v2/search?q=${encodeURIComponent(query)}&limit=50`);
      if (version !== searchVersion) return;
      if (response.status !== 200 || !Array.isArray(response.body?.items)) throw new Error('search unavailable');
      list.replaceChildren();
      for (const incident of response.body.items) {
        const row = append(list, 'li', '', '');
        const button = append(row, 'button', '', '');
        button.type = 'button';
        button.dataset.searchIncident = incident.incident_id;
        append(button, 'strong', '', incident.headline || 'Untitled incident');
        append(button, 'span', '', `${incident.kind || 'unknown'} · ${incident.severity || 'Unknown'} · priority ${incident.priority_score ?? 0}`);
      }
      status.textContent = `${response.body.items.length} local incidents found.`;
      if (!list.childElementCount) append(list, 'li', '', 'No retained incident matched.');
    } catch {
      if (version !== searchVersion) return;
      list.replaceChildren();
      status.textContent = 'Local search is temporarily unavailable.';
    }
  }

  function openSearchResult(event) {
    const button = event.target.closest?.('[data-search-incident]');
    if (button) openIncident(button.dataset.searchIncident, button);
  }

  function download(name, type, content) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function exportCsv() {
    const incidents = getIncidents().slice(0, 200);
    download('foglight-incidents.csv', 'text/csv;charset=utf-8', incidentsToCsv(incidents));
    byId('incident-search-status').textContent = `${incidents.length} incidents prepared as CSV.`;
  }

  function exportGeoJSON() {
    const incidents = getIncidents().slice(0, 200);
    const payload = incidentsToGeoJSON(incidents, now());
    download('foglight-incidents.geojson', 'application/geo+json;charset=utf-8', `${JSON.stringify(payload, null, 2)}\n`);
    byId('incident-search-status').textContent = `${incidents.length} incidents prepared as GeoJSON.`;
  }

  function renderHistory() {
    const summary = offlineHistorySummary({
      revisionCursor: snapshot.revisionCursor,
      lastRevisionAt: snapshot.lastRevisionAt,
      sourceHealth: snapshot.health?.sources,
      now: now(),
    });
    const counts = snapshot.health?.counts || {};
    const hasLive = Number(counts.live || 0) > 0;
    const hasCached = Number(counts.cached || 0) + Number(counts.stale || 0)
      + Number(counts.error || 0) > 0;
    const incidentCount = Array.isArray(snapshot.incidents) ? snapshot.incidents.length : 0;
    const prefix = incidentCount && (snapshot.failed || !hasLive)
      ? 'Cached local history — not live.'
      : snapshot.failed ? 'Offline; no retained incident history is available.'
      : hasLive && hasCached ? 'Mixed source freshness; cached records are labeled.'
        : hasLive ? 'Local snapshot includes live source checks.' : 'Local history waiting.';
    const revision = summary.revision_cursor
      ? ` Revision ${summary.revision_cursor}, newest ${utcLabel(summary.last_revision_at)}.` : ' No retained revision yet.';
    const age = summary.oldest_source_age_seconds == null
      ? '' : ` Oldest source cache ${ageLabel(summary.oldest_source_age_seconds)}.`;
    byId('overview-history-status').textContent = `${prefix}${revision}${age}`;
  }

  function updateSnapshot(value) {
    snapshot = { ...snapshot, ...(value || {}) };
    renderHistory();
  }

  function useMapCoordinates({ latitude, longitude }) {
    const lat = Number(latitude);
    const lon = Number(longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)
        || lat < -85 || lat > 85 || lon < -180 || lon > 180) {
      byId('watch-region-status').textContent = 'The selected map coordinates were invalid.';
      return false;
    }
    const center = byId('watch-center-details');
    if (center) center.open = true;
    const details = document.querySelector('.watch-settings-panel');
    if (details) details.open = true;
    byId('watch-region-lat').value = lat.toFixed(5);
    byId('watch-region-lon').value = lon.toFixed(5);
    byId('watch-region-label').focus();
    byId('watch-region-status').textContent = 'Map coordinates loaded. Add a local label and radius.';
    return true;
  }

  function requestMapPick() {
    const startedPick = beginMapPick();
    byId('watch-region-status').textContent = startedPick
      ? 'Map pick active. Click a location on the map, or press Escape to cancel.'
      : 'The map is not ready yet. Enter coordinates directly or try again.';
  }

  function refreshSettings() {
    if (!started) return;
    renderRegions();
    renderNotificationSettings();
    const interval = String(store.state.user.wallDisplay.interval_seconds || 30);
    if ([...byId('wall-display-interval').options].some(item => item.value === interval)) {
      byId('wall-display-interval').value = interval;
    }
    renderWall();
  }

  function wallItems() {
    return getIncidents().filter(item => item?.incident_id).slice(0, 50);
  }

  function cycle(direction = 1) {
    const items = wallItems();
    if (!items.length) {
      byId('wall-display-status').textContent = 'No incidents available to cycle.';
      return false;
    }
    wallIndex = (wallIndex + direction + items.length) % items.length;
    cycleIncident(items[wallIndex].incident_id);
    byId('wall-display-status').textContent = `${wallIndex + 1} of ${items.length}: ${items[wallIndex].headline || 'Untitled incident'}.`;
    return true;
  }

  function clearWallTimer() {
    if (wallTimer !== null) window.clearInterval(wallTimer);
    wallTimer = null;
  }

  function scheduleWall() {
    clearWallTimer();
    if (!wallEnabled || wallPaused || document.hidden || reducedMotion()) return;
    const seconds = Number(store.state.user.wallDisplay.interval_seconds) || 30;
    wallTimer = window.setInterval(() => cycle(1), seconds * 1000);
  }

  function renderWall() {
    const button = byId('wall-display-toggle');
    button.setAttribute('aria-pressed', String(wallEnabled && !wallPaused));
    button.textContent = !wallEnabled ? 'Start auto-cycle'
      : reducedMotion() ? 'Stop wall display' : wallPaused ? 'Resume auto-cycle' : 'Pause auto-cycle';
    if (!wallEnabled) byId('wall-display-status').textContent = 'Stopped.';
    else if (reducedMotion()) byId('wall-display-status').textContent = 'Reduced motion is active; use Previous and Next.';
    else if (document.hidden) byId('wall-display-status').textContent = 'Paused while Foglight is hidden.';
    else if (wallPaused) byId('wall-display-status').textContent = 'Paused.';
  }

  function toggleWall() {
    if (!wallEnabled) {
      wallEnabled = true;
      wallPaused = false;
      wallIndex = -1;
      cycle(1);
    } else if (reducedMotion()) {
      wallEnabled = false;
      wallPaused = false;
    } else {
      wallPaused = !wallPaused;
    }
    scheduleWall();
    renderWall();
  }

  async function changeWallInterval() {
    const previous = store.state.user.wallDisplay;
    const next = { interval_seconds: Number(byId('wall-display-interval').value) || 30 };
    store.update('user', { wallDisplay: next });
    scheduleWall();
    try { await persist({ wall_display: next }); } catch {
      store.update('user', { wallDisplay: previous });
      byId('wall-display-interval').value = String(previous.interval_seconds || 30);
      scheduleWall();
    }
  }

  function wallKeyboard(event) {
    if (!wallEnabled || event.target.closest?.(
      'input, select, textarea, button, a[href], summary, [contenteditable="true"]',
    )) return;
    if (![' ', 'ArrowLeft', 'ArrowRight', 'Escape'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'ArrowLeft') cycle(-1);
    else if (event.key === 'ArrowRight') cycle(1);
    else if (event.key === 'Escape') {
      wallEnabled = false;
      wallPaused = false;
      clearWallTimer();
      renderWall();
    } else if (!reducedMotion()) toggleWall();
  }

  function visibilityChanged() {
    scheduleWall();
    renderWall();
  }

  function start() {
    if (started) return;
    started = true;
    byId('watch-region-form').addEventListener('submit', addRegion);
    byId('watch-region-map-pick').addEventListener('click', requestMapPick);
    byId('watch-region-list').addEventListener('click', regionAction);
    byId('enable-notifications').addEventListener('click', enableNotifications);
    byId('disable-notifications').addEventListener('click', disableNotifications);
    byId('save-notification-settings').addEventListener('click', saveNotificationSettings);
    byId('notification-center-list').addEventListener('click', alertAction);
    byId('incident-search-form').addEventListener('submit', search);
    byId('incident-search-results').addEventListener('click', openSearchResult);
    byId('export-incidents-csv').addEventListener('click', exportCsv);
    byId('export-incidents-geojson').addEventListener('click', exportGeoJSON);
    byId('print-selected-incident').addEventListener('click', () => {
      if (!printSelected()) byId('incident-search-status').textContent = 'Select an incident before printing.';
    });
    byId('wall-display-toggle').addEventListener('click', toggleWall);
    byId('wall-display-previous').addEventListener('click', () => cycle(-1));
    byId('wall-display-next').addEventListener('click', () => cycle(1));
    byId('wall-display-interval').addEventListener('change', changeWallInterval);
    document.addEventListener('keydown', wallKeyboard);
    document.addEventListener('visibilitychange', visibilityChanged);
    const interval = String(store.state.user.wallDisplay.interval_seconds || 30);
    if ([...byId('wall-display-interval').options].some(item => item.value === interval)) {
      byId('wall-display-interval').value = interval;
    }
    renderRegions();
    renderNotificationSettings();
    renderAlerts();
    renderHistory();
    renderWall();
  }

  function stop() {
    wallEnabled = false;
    wallPaused = false;
    clearWallTimer();
    cancelMapPick();
    renderWall();
  }

  return Object.freeze({
    start, stop, processChanges, updateSnapshot, useMapCoordinates, refreshSettings,
  });
}
