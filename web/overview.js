import {
  FILTERS,
  STATE_COPY,
  deriveOverviewState,
  filterIncidents,
  finiteNumber,
  formatIncidentAge,
  formatLocation,
  kindPresentation,
  priorityExplanation,
  summarizeChanges,
} from './overview-model.js';
import { createIncidentMapController } from './map-v2.js';
import { createIncidentDrawerController } from './incident-drawer.js';
import { safeHttpUrl } from './core.js';

function appendText(parent, tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  parent.appendChild(node);
  return node;
}

function sourceNames(incident) {
  const sources = Array.isArray(incident?.sources) ? incident.sources : [];
  const names = [...new Set(sources.map(
    item => item?.attribution || item?.provider_id,
  ).filter(Boolean))];
  if (!names.length) return 'Source not reported';
  return names.length <= 3 ? names.join(', ') : `${names.slice(0, 3).join(', ')} +${names.length - 3}`;
}

function sourceAge(source) {
  const seconds = Number(source?.cached_age_seconds);
  if (!Number.isFinite(seconds) || seconds < 0) return '';
  if (seconds < 60) return `${Math.floor(seconds)}s old`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m old`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h old`;
  return `${Math.floor(seconds / 86400)}d old`;
}

function renderIncident(container, incident, now, idPrefix = 'now') {
  const item = document.createElement('li');
  item.className = `overview-incident severity-${String(incident?.severity || 'unknown').toLowerCase()}`;
  item.dataset.incidentId = String(incident?.incident_id || 'unknown');
  const presentation = kindPresentation(incident?.kind);

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'overview-incident-summary';
  button.setAttribute('aria-expanded', 'false');
  const detailId = `overview-${idPrefix}-detail-${String(incident?.incident_id || 'unknown').replace(/[^a-z0-9_-]/gi, '-')}`;
  button.setAttribute('aria-controls', detailId);

  const marker = appendText(button, 'span', `kind-marker shape-${presentation.shape}`, presentation.abbreviation);
  marker.setAttribute('aria-hidden', 'true');
  const heading = document.createElement('span');
  heading.className = 'incident-heading';
  appendText(heading, 'span', 'incident-kicker', `${presentation.label} · ${incident?.severity || 'Unknown'} severity`);
  appendText(heading, 'span', 'incident-title', incident?.headline || 'Untitled incident');
  appendText(heading, 'span', 'incident-summary', incident?.summary || 'No summary reported.');
  button.appendChild(heading);

  const score = document.createElement('span');
  score.className = 'incident-score';
  score.setAttribute('aria-label', `Priority ${Math.round(finiteNumber(incident?.priority_score))} out of 100`);
  appendText(score, 'strong', '', String(Math.round(finiteNumber(incident?.priority_score))));
  appendText(score, 'span', '', 'priority');
  button.appendChild(score);
  item.appendChild(button);

  const meta = document.createElement('div');
  meta.className = 'incident-meta';
  for (const text of [
    formatIncidentAge(incident?.last_changed_at, now),
    formatLocation(incident),
    `Sources: ${sourceNames(incident)}`,
    `Change: ${String(incident?.change_type || 'updated').replaceAll('_', ' ')}`,
  ]) appendText(meta, 'span', '', text);
  item.appendChild(meta);

  const detail = document.createElement('div');
  detail.id = detailId;
  detail.className = 'incident-evidence';
  detail.hidden = true;
  appendText(detail, 'p', '', `Priority ${incident?.priority_score ?? 0}: ${priorityExplanation(incident)}`);
  appendText(detail, 'p', '', `Status ${incident?.status || 'unknown'} · Urgency ${incident?.urgency || 'Unknown'} · Certainty ${incident?.certainty || 'Unknown'}`);
  const sources = Array.isArray(incident?.sources) ? incident.sources : [];
  const links = document.createElement('div');
  links.className = 'incident-source-links';
  for (const source of sources.slice(0, 5)) {
    const url = safeHttpUrl(source?.source_url);
    if (!url) continue;
    const link = document.createElement('a');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = source.attribution || source.provider_id || 'Source evidence';
    links.appendChild(link);
  }
  if (links.childElementCount) detail.appendChild(links);
  item.appendChild(detail);

  button.addEventListener('click', () => {
    const expanded = button.getAttribute('aria-expanded') === 'true';
    button.setAttribute('aria-expanded', String(!expanded));
    detail.hidden = expanded;
  });
  container.appendChild(item);
}

export function createOverviewController({
  getJSON,
  store,
  now = () => Date.now(),
  onAddPin = async () => {},
  onIncidentChanges = async () => {},
  onSnapshot = () => {},
  onMapPick = () => {},
}) {
  let incidents = [];
  let health = { counts: {}, sources: [] };
  let cursor = 0;
  let loaded = false;
  let failed = false;
  let firstRun = false;
  let timer = null;
  let pendingDrawerTimer = null;
  let changeText = 'No changes since this view opened.';
  let nextIncidentCursor = null;
  let catalogVisible = 50;
  let selectedIncidentId = null;
  let lastRevisionAt = null;
  const mapController = createIncidentMapController({
    annotations: () => store.state.user.annotations,
    onAddPin,
    onSelect: incidentId => selectIncident(incidentId, { source: 'map' }),
    onPickCoordinates: onMapPick,
  });
  const drawerController = createIncidentDrawerController({ getJSON, now });

  function applySelectionState({ focus = false, expand = false } = {}) {
    let target = null;
    for (const item of document.querySelectorAll('.overview-incident')) {
      const selected = item.dataset.incidentId === selectedIncidentId;
      item.classList.toggle('is-selected', selected);
      const button = item.querySelector('.overview-incident-summary');
      if (button) button.setAttribute('aria-current', selected ? 'true' : 'false');
      if (selected && !target) target = item;
    }
    if (!target || !focus) return;
    const button = target.querySelector('.overview-incident-summary');
    if (expand && button) {
      button.setAttribute('aria-expanded', 'true');
      const detail = target.querySelector('.incident-evidence');
      if (detail) detail.hidden = false;
    }
    target.scrollIntoView({ block: 'nearest' });
    button?.focus({ preventScroll: true });
  }

  function selectIncident(incidentId, { source = 'list' } = {}) {
    selectedIncidentId = String(incidentId || '');
    const selected = filterIncidents(incidents, store.state.ui.incidentFilter);
    const index = selected.findIndex(item => String(item?.incident_id) === selectedIncidentId);
    if (index < 0) {
      selectedIncidentId = null;
      mapController.select(null);
      return;
    }
    mapController.select(selectedIncidentId, { announce: source === 'list' });
    if (source === 'map' && index >= (store.state.ui.displayMode === 'command' ? 12 : 8)) {
      const catalog = document.getElementById('overview-catalog');
      catalog.open = true;
      catalogVisible = Math.max(catalogVisible, index + 1);
      renderCatalog();
    }
    applySelectionState({ focus: source === 'map', expand: source === 'map' });
    if (source === 'wall') {
      document.getElementById('overview-live').textContent = `Wall display selected ${selected[index].headline || 'incident'}.`;
    } else if (source === 'map') {
      document.getElementById('overview-live').textContent = `Map selected ${selected[index].headline || 'incident'}.`;
      // Opening <details> queues a toggle event which may rebuild the catalog.
      // Restore focus and expansion after that event so map-to-list selection is stable.
      if (pendingDrawerTimer !== null) window.clearTimeout(pendingDrawerTimer);
      const selectionId = selectedIncidentId;
      pendingDrawerTimer = window.setTimeout(() => {
        pendingDrawerTimer = null;
        if (selectionId !== selectedIncidentId) return;
        applySelectionState({ focus: true, expand: true });
        drawerController.open(selectedIncidentId, { opener: document.activeElement });
      }, 0);
    } else {
      drawerController.open(selectedIncidentId, { opener: document.activeElement });
    }
  }

  function renderHealth() {
    const node = document.getElementById('overview-health');
    const counts = health?.counts || {};
    const attention = finiteNumber(counts.error) + finiteNumber(counts.stale);
    const live = finiteNumber(counts.live);
    const cached = finiteNumber(counts.cached);
    const pending = finiteNumber(counts.pending);
    const parts = [];
    if (live) parts.push(`${live} live`);
    if (cached) parts.push(`${cached} cached`);
    if (attention) parts.push(`${attention} need attention`);
    if (pending) parts.push(`${pending} pending`);
    node.textContent = parts.join(' · ') || 'Sources pending';
    node.dataset.status = attention ? 'attention' : live ? 'current' : cached ? 'cached' : 'pending';

    const list = document.getElementById('overview-source-list');
    list.replaceChildren();
    for (const source of (health?.sources || []).filter(item => item.status !== 'live').slice(0, 12)) {
      const row = document.createElement('li');
      const age = sourceAge(source);
      row.textContent = `${source.attribution || source.provider_id}: ${source.status}`
        + `${age ? ` · ${age}` : ''}${source.detail ? ` · ${source.detail}` : ''}`;
      list.appendChild(row);
    }
    if (!list.childElementCount) appendText(list, 'li', '', 'All checked sources are current.');
  }

  function renderCatalog() {
    const details = document.getElementById('overview-catalog');
    if (!details.open) return;
    const selected = filterIncidents(incidents, store.state.ui.incidentFilter);
    const list = document.getElementById('overview-catalog-list');
    list.replaceChildren();
    for (const incident of selected.slice(0, catalogVisible)) {
      renderIncident(list, incident, now(), 'catalog');
    }
    if (!list.childElementCount) appendText(list, 'li', 'overview-empty', 'No loaded incident matches this category.');
    const more = document.getElementById('overview-catalog-more');
    more.hidden = catalogVisible >= selected.length && nextIncidentCursor == null;
    more.textContent = catalogVisible < selected.length
      ? 'Show more loaded incidents' : 'Load more incidents';
    applySelectionState();
  }

  async function loadMoreCatalog() {
    const selected = filterIncidents(incidents, store.state.ui.incidentFilter);
    if (catalogVisible < selected.length) {
      catalogVisible += 50;
      renderCatalog();
      return;
    }
    if (nextIncidentCursor == null) return;
    let response;
    try {
      response = await getJSON(`/api/v2/incidents?limit=200&cursor=${nextIncidentCursor}`);
    } catch {
      document.getElementById('overview-live').textContent = 'More incidents could not be loaded.';
      return;
    }
    if (response.status !== 200 || !Array.isArray(response.body?.items)) {
      document.getElementById('overview-live').textContent = 'More incidents could not be loaded.';
      return;
    }
    const byId = new Map(incidents.map(item => [item.incident_id, item]));
    for (const incident of response.body.items) {
      if (incident?.incident_id) byId.set(incident.incident_id, incident);
    }
    incidents = [...byId.values()];
    nextIncidentCursor = response.body.next_cursor ?? null;
    catalogVisible += 50;
    render();
    renderCatalog();
    document.getElementById('overview-live').textContent = `${response.body.items.length} more incidents loaded.`;
  }

  function render() {
    const mode = store.state.ui.displayMode;
    const filter = store.state.ui.incidentFilter;
    const selected = filterIncidents(incidents, filter);
    const state = deriveOverviewState({ loaded, failed, incidents: selected, health, firstRun });
    const surface = document.getElementById('overview-surface');
    surface.dataset.viewState = state;
    surface.dataset.density = mode === 'command' ? 'command' : 'overview';
    const [title, message] = STATE_COPY[state];
    document.getElementById('overview-state-title').textContent = title;
    document.getElementById('overview-state-message').textContent = message;
    document.getElementById('overview-change-summary').textContent = changeText;
    document.getElementById('overview-count').textContent = `${selected.length} matching · showing ${Math.min(selected.length, mode === 'command' ? 12 : 8)}`;

    const list = document.getElementById('overview-now-list');
    list.replaceChildren();
    list.setAttribute('aria-busy', String(state === 'loading'));
    if (state === 'loading') {
      for (let index = 0; index < 5; index += 1) {
        const placeholder = document.createElement('li');
        placeholder.className = 'overview-skeleton';
        placeholder.setAttribute('aria-hidden', 'true');
        list.appendChild(placeholder);
      }
    } else {
      const limit = mode === 'command' ? 12 : 8;
      for (const incident of selected.slice(0, limit)) renderIncident(list, incident, now());
    }
    document.getElementById('overview-empty').hidden = selected.length > 0 || state === 'loading';
    if (selectedIncidentId && !selected.some(item => String(item?.incident_id) === selectedIncidentId)) {
      selectedIncidentId = null;
      closeDrawer();
    }
    mapController.update(selected);
    mapController.select(selectedIncidentId);
    applySelectionState();
    renderHealth();
    renderCatalog();
    onSnapshot({
      incidents: incidents.slice(), health, revisionCursor: cursor,
      lastRevisionAt, failed, loaded,
    });
  }

  function setFilter(filter, { focus = false } = {}) {
    if (!FILTERS.some(item => item.id === filter)) filter = 'global';
    store.update('ui', { incidentFilter: filter });
    catalogVisible = 50;
    for (const button of document.querySelectorAll('[data-incident-filter]')) {
      const active = button.dataset.incidentFilter === filter;
      button.setAttribute('aria-pressed', String(active));
      button.tabIndex = active ? 0 : -1;
      if (active && focus) button.focus();
    }
    render();
    document.getElementById('overview-live').textContent = `${filterIncidents(incidents, filter).length} incidents in ${FILTERS.find(item => item.id === filter).label}.`;
  }

  function wireFilters() {
    const group = document.getElementById('overview-filters');
    for (const button of group.querySelectorAll('[data-incident-filter]')) {
      button.addEventListener('click', () => setFilter(button.dataset.incidentFilter));
    }
    group.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const ids = FILTERS.map(item => item.id);
      const current = ids.indexOf(store.state.ui.incidentFilter);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? ids.length - 1
        : (current + (event.key === 'ArrowRight' ? 1 : -1) + ids.length) % ids.length;
      setFilter(ids[next], { focus: true });
    });
  }

  function wireIncidentSelection() {
    for (const listId of ['overview-now-list', 'overview-catalog-list']) {
      document.getElementById(listId).addEventListener('click', event => {
        const button = event.target.closest?.('.overview-incident-summary');
        const item = button?.closest('.overview-incident');
        if (item?.dataset.incidentId) selectIncident(item.dataset.incidentId);
      });
    }
  }

  async function refresh() {
    failed = false;
    render();
    try {
      const response = await getJSON('/api/v2/bootstrap');
      if (response.status !== 200 || !response.body?.incidents) throw new Error('bootstrap unavailable');
      incidents = Array.isArray(response.body.incidents.items) ? response.body.incidents.items : [];
      nextIncidentCursor = response.body.incidents.next_cursor ?? null;
      health = response.body.source_health || { counts: {}, sources: [] };
      cursor = Math.max(0, Number(response.body.revision_cursor) || 0);
      lastRevisionAt = response.body.last_revision_at || null;
      loaded = true;
    } catch {
      failed = true;
      loaded = true;
    }
    render();
    window.__foglightOverview.metrics.firstIncidentPaintMs = Math.round(
      performance.now() - window.__foglightOverview.metrics.startedAt,
    );
    return !failed;
  }

  async function pollChanges() {
    if (!loaded || failed) return refresh();
    try {
      const [response, healthResponse] = await Promise.all([
        getJSON(`/api/v2/changes?cursor=${cursor}&limit=200`),
        getJSON('/api/v2/source-health'),
      ]);
      if (response.status !== 200 || !Array.isArray(response.body?.items)) throw new Error('changes unavailable');
      if (healthResponse.status !== 200 || !healthResponse.body?.counts) throw new Error('health unavailable');
      health = healthResponse.body;
      const changes = response.body.items;
      if (changes.length) {
        const byId = new Map(incidents.map(item => [item.incident_id, item]));
        for (const change of changes) {
          if (change.incident?.incident_id) byId.set(change.incident.incident_id, change.incident);
        }
        incidents = [...byId.values()];
        cursor = Math.max(cursor, Number(response.body.next_cursor) || cursor);
        lastRevisionAt = changes.at(-1)?.changed_at || lastRevisionAt;
        changeText = summarizeChanges(changes);
        document.getElementById('overview-live').textContent = `What changed: ${changeText}`;
        try { await onIncidentChanges(changes); } catch { /* Alerts degrade independently. */ }
      }
      render();
    } catch {
      failed = true;
      render();
    }
  }

  function start({ isFirstRun = false } = {}) {
    firstRun = isFirstRun;
    window.__foglightOverview = {
      metrics: { startedAt: performance.now(), firstIncidentPaintMs: null },
      refresh, selectIncident,
      pollChanges,
    };
    mapController.start();
    drawerController.start();
    wireFilters();
    wireIncidentSelection();
    document.getElementById('overview-catalog').addEventListener('toggle', renderCatalog);
    document.getElementById('overview-catalog-more').addEventListener('click', loadMoreCatalog);
    setFilter(store.state.ui.incidentFilter);
    const initialLoad = refresh();
    timer = window.setInterval(pollChanges, 30_000);
    return initialLoad;
  }

  function stop() {
    if (timer !== null) window.clearInterval(timer);
    timer = null;
    closeDrawer();
  }

  function closeDrawer() {
    if (pendingDrawerTimer !== null) window.clearTimeout(pendingDrawerTimer);
    pendingDrawerTimer = null;
    drawerController.close();
  }

  function openIncident(incidentId, opener = document.activeElement) {
    const id = String(incidentId || '');
    if (!id) return false;
    const loadedIncident = incidents.find(item => String(item?.incident_id) === id);
    if (loadedIncident) {
      selectedIncidentId = id;
      mapController.select(id);
      applySelectionState();
    }
    drawerController.open(id, { opener });
    return true;
  }

  function cycleIncident(incidentId) {
    selectIncident(incidentId, { source: 'wall' });
    return selectedIncidentId === String(incidentId);
  }

  return Object.freeze({
    start, stop, render, setFilter, refresh, pollChanges, loadMoreCatalog,
    selectIncident, activateMap: mapController.activate,
    closeDrawer,
    printSelectedBriefing: drawerController.printSelected,
    openIncident,
    cycleIncident,
    beginMapPick: mapController.beginCoordinatePick,
    cancelMapPick: mapController.cancelCoordinatePick,
    getIncidents: () => incidents.slice(),
  });
}
