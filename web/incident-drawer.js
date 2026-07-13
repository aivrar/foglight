import { element, safeHttpUrl } from './core.js';
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
} from './incident-model.js';
import { formatLocation, priorityExplanation } from './overview-model.js';

function append(parent, tag, className, text) {
  const node = element(tag, className, text);
  parent.appendChild(node);
  return node;
}

function section(parent, title) {
  const node = append(parent, 'section', 'drawer-section', '');
  append(node, 'h3', '', title);
  return node;
}

function definitionList(parent, rows) {
  const list = append(parent, 'dl', 'drawer-facts', '');
  for (const [label, value] of rows) {
    append(list, 'dt', '', label);
    append(list, 'dd', '', value);
  }
  return list;
}

function uniqueSources(incident) {
  const seen = new Set();
  return (Array.isArray(incident?.sources) ? incident.sources : []).filter(source => {
    const key = `${source?.provider_id || ''}:${source?.provider_record_id || ''}:${source?.source_url || ''}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 30);
}

function timelineEntryKey(item) {
  return `${item?.revision ?? ''}:${item?.changed_at || ''}:${item?.cursor ?? ''}`;
}

export function createIncidentDrawerController({ getJSON, now = () => Date.now() }) {
  let detail = null;
  let timeline = [];
  let timelineAvailable = true;
  let healthRows = [];
  let relatedRows = [];
  let selectedId = null;
  let windowHours = 24;
  let requestVersion = 0;
  let lastFocus = null;
  let started = false;

  const drawer = () => document.getElementById('incident-drawer');
  const body = () => document.getElementById('incident-drawer-body');
  const live = () => document.getElementById('incident-drawer-live');

  function setBackgroundInert(value) {
    for (const id of ['topbar', 'overview-surface']) {
      const node = document.getElementById(id);
      if (node) node.inert = value;
    }
  }

  function setPreview(entries, index, previousByKey) {
    const safeIndex = Math.max(0, Math.min(entries.length - 1, Number(index) || 0));
    const item = entries[safeIndex];
    const preview = document.getElementById('incident-timeline-preview');
    if (!preview || !item) return;
    const previous = previousByKey.get(timelineEntryKey(item)) || null;
    const changes = revisionChanges(previous, item);
    preview.replaceChildren();
    append(preview, 'strong', '', `Revision ${item.revision} · ${String(item.change_type || 'updated').replaceAll('_', ' ')}`);
    append(preview, 'span', '', formatUtcTimestamp(item.changed_at));
    append(preview, 'span', '', `Status ${item.incident?.status || 'unknown'} · severity ${item.incident?.severity || 'Unknown'} · priority ${item.incident?.priority_score ?? 0}`);
    append(preview, 'span', '', `Changed: ${changes.join(', ') || 'no user-visible field'}`);
    for (const button of document.querySelectorAll('[data-timeline-index]')) {
      button.setAttribute('aria-current', String(Number(button.dataset.timelineIndex) === safeIndex));
    }
    const range = document.getElementById('incident-timeline-scrubber');
    if (range) range.value = String(safeIndex);
  }

  function renderTimeline(parent) {
    const container = section(parent, 'Revision timeline');
    if (!timelineAvailable) {
      append(container, 'p', 'drawer-empty', 'Revision history is temporarily unavailable. Current incident details remain usable.');
      return;
    }
    const controls = append(container, 'div', 'timeline-window-controls', '');
    controls.setAttribute('role', 'group');
    controls.setAttribute('aria-label', 'Timeline window');
    for (const option of TIMELINE_WINDOWS) {
      const button = append(controls, 'button', '', option.label);
      button.type = 'button';
      button.dataset.timelineHours = String(option.hours);
      button.setAttribute('aria-pressed', String(option.hours === windowHours));
      button.addEventListener('click', () => {
        windowHours = option.hours;
        render({ focusWindow: option.hours });
      });
    }
    const allEntries = normalizeTimeline(timeline);
    const previousByKey = new Map(allEntries.map((item, index) => [
      timelineEntryKey(item), index > 0 ? allEntries[index - 1] : null,
    ]));
    const entries = filterTimeline(allEntries, windowHours, now());
    append(container, 'p', 'drawer-help', `${entries.length} retained revisions in this window. Previewing never changes the live incident.`);
    if (!entries.length) {
      append(container, 'p', 'drawer-empty', 'No retained revision falls inside this time window.');
      return;
    }
    const rangeLabel = append(container, 'label', 'timeline-scrubber-label', 'Preview revision');
    const range = append(rangeLabel, 'input', '', '');
    range.id = 'incident-timeline-scrubber';
    range.type = 'range';
    range.min = '0';
    range.max = String(entries.length - 1);
    range.step = '1';
    range.value = String(entries.length - 1);
    range.addEventListener('input', () => setPreview(entries, range.value, previousByKey));
    const preview = append(container, 'div', 'timeline-preview', '');
    preview.id = 'incident-timeline-preview';
    preview.setAttribute('aria-live', 'polite');
    const list = append(container, 'ol', 'timeline-list', '');
    entries.forEach((item, index) => {
      const row = append(list, 'li', `change-${item.change_type || 'updated'}`, '');
      const button = append(row, 'button', '', '');
      button.type = 'button';
      button.dataset.timelineIndex = String(index);
      append(button, 'strong', '', `Revision ${item.revision} · ${String(item.change_type || 'updated').replaceAll('_', ' ')}`);
      append(button, 'span', '', formatUtcTimestamp(item.changed_at));
      const changes = revisionChanges(previousByKey.get(timelineEntryKey(item)) || null, item);
      append(button, 'span', '', changes.join(', ') || 'No user-visible field changed');
      button.addEventListener('click', () => setPreview(entries, index, previousByKey));
    });
    setPreview(entries, entries.length - 1, previousByKey);
  }

  function renderSources(parent) {
    const container = section(parent, 'Sources and provenance');
    const list = append(container, 'ul', 'drawer-source-list', '');
    for (const source of uniqueSources(detail)) {
      const row = append(list, 'li', '', '');
      const label = source.attribution || source.provider_id || 'Source evidence';
      append(row, 'strong', '', label);
      append(row, 'span', '', source.provider_record_id || 'Record ID not reported');
      const url = safeHttpUrl(source.source_url);
      if (url) {
        const link = append(row, 'a', '', 'Open source evidence');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
      }
    }
    if (!list.childElementCount) append(list, 'li', 'drawer-empty', 'No source reference was reported.');
  }

  function renderObservations(parent) {
    const container = section(parent, 'Normalized observations');
    append(container, 'p', 'drawer-help', `${detail.observation_count ?? 0} linked observations${detail.observations_truncated ? '; this view is capped at 200' : ''}.`);
    const list = append(container, 'ol', 'drawer-observation-list', '');
    const attribution = new Map(uniqueSources(detail).map(item => [item.provider_id, item.attribution || item.provider_id]));
    const observations = Array.isArray(detail.observations) ? detail.observations : [];
    for (const observation of observations.slice(0, 200)) {
      const row = append(list, 'li', '', '');
      const provenance = provenanceLabel(observation);
      append(row, 'span', `provenance-badge provenance-${provenance.id}`, provenance.label);
      append(row, 'strong', '', observation.headline || 'Untitled observation');
      append(row, 'p', '', observation.summary || 'No observation summary reported.');
      append(row, 'span', 'observation-meta', `${attribution.get(observation.provider_id) || observation.provider_id || 'Source not reported'} · ${expirationState(observation, now())}`);
      append(row, 'span', 'observation-meta', `Event ${formatUtcTimestamp(observation.event_at)} · effective ${formatUtcTimestamp(observation.effective_at)} · expires ${formatUtcTimestamp(observation.expires_at)}`);
      const url = safeHttpUrl(observation.source_url);
      if (url) {
        const link = append(row, 'a', '', 'Open observation evidence');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
      }
    }
    if (!list.childElementCount) append(list, 'li', 'drawer-empty', 'No normalized observation detail is available.');
  }

  function renderMetrics(parent) {
    const rows = metricRows(detail.observations);
    const container = section(parent, 'Reported metrics');
    if (!rows.length) {
      append(container, 'p', 'drawer-empty', 'No structured metric was reported.');
      return;
    }
    definitionList(container, rows.map(item => [
      item.key.replaceAll('_', ' '), `${item.value} ${item.unit} · ${item.provenance}`.trim(),
    ]));
  }

  function renderRelations(parent) {
    const container = section(parent, 'Related incidents');
    const list = append(container, 'ul', 'drawer-relation-list', '');
    const relations = Array.isArray(detail.relations) ? detail.relations : [];
    for (const relation of relations.slice(0, 20)) {
      const related = relatedRows.find(item => item?.incident_id === relation.target_incident_id);
      const row = append(list, 'li', '', '');
      append(row, 'span', '', String(relation.relation_type || 'related to').replaceAll('_', ' '));
      if (related) {
        const provenance = provenanceLabel(related);
        append(row, 'span', `provenance-badge provenance-${provenance.id}`, provenance.label);
      }
      append(row, 'strong', '', related?.headline || relation.target_incident_id);
      if (related) append(row, 'span', '', `${related.severity || 'Unknown'} · ${related.status || 'unknown'}`);
    }
    if (!list.childElementCount) append(list, 'li', 'drawer-empty', 'No related incident is recorded.');
  }

  function renderHealth(parent) {
    const container = section(parent, 'Source health');
    const list = append(container, 'ul', 'drawer-health-list', '');
    for (const item of healthRows) {
      const row = append(list, 'li', `health-${item?.status || 'unknown'}`, '');
      append(row, 'strong', '', item?.attribution || item?.provider_id || 'Unknown source');
      append(row, 'span', '', `${item?.status || 'unknown'}${item?.detail ? ` · ${item.detail}` : ''}`);
      append(row, 'span', '', `Last success ${formatUtcTimestamp(item?.last_success_at)}`);
    }
    if (!list.childElementCount) append(list, 'li', 'drawer-empty', 'No source health detail is available.');
  }

  function render({ focusWindow = null } = {}) {
    if (!detail) return;
    const content = body();
    content.replaceChildren();
    const provenance = provenanceLabel(detail);
    const header = append(content, 'section', 'drawer-summary', '');
    append(header, 'span', `provenance-badge provenance-${provenance.id}`, provenance.label);
    append(header, 'h2', '', detail.headline || 'Untitled incident');
    append(header, 'p', '', detail.summary || 'No incident summary reported.');
    const incidentStatus = String(detail.status || 'unknown');
    const lifecycle = expirationState(detail, now());
    const lifecycleSuffix = lifecycle.toLowerCase() === incidentStatus.toLowerCase()
      ? '' : ` · ${lifecycle}`;
    append(header, 'p', 'drawer-status-line', `${detail.severity || 'Unknown'} severity · ${incidentStatus}${lifecycleSuffix}`);
    const actions = append(header, 'div', 'drawer-actions', '');
    const copy = append(actions, 'button', '', 'Copy deterministic summary');
    copy.type = 'button';
    copy.addEventListener('click', copySummary);
    const print = append(actions, 'button', '', 'Print incident briefing');
    print.type = 'button';
    print.addEventListener('click', printSelected);

    const facts = section(content, 'Facts and times');
    definitionList(facts, [
      ['Location', detail.location_name || formatLocation(detail)],
      ['First seen', formatUtcTimestamp(detail.first_seen_at)],
      ['Last changed', formatUtcTimestamp(detail.last_changed_at)],
      ['Last observed', formatUtcTimestamp(detail.last_observed_at)],
      ['Urgency / certainty', `${detail.urgency || 'Unknown'} / ${detail.certainty || 'Unknown'}`],
      ['Revision', String(detail.revision || 1)],
    ]);
    const priority = section(content, 'Priority evidence');
    append(priority, 'strong', 'drawer-priority-score', `${detail.priority_score ?? 0} / 100`);
    append(priority, 'p', '', priorityExplanation(detail));
    append(priority, 'p', 'drawer-help', 'Priority is explainable triage, not a prediction.');
    renderMetrics(content);
    renderSources(content);
    renderRelations(content);
    renderHealth(content);
    renderTimeline(content);
    renderObservations(content);
    if (focusWindow != null) {
      document.querySelector(`[data-timeline-hours="${focusWindow}"]`)?.focus();
    }
  }

  async function fetchEnrichment(version) {
    const sources = Array.isArray(detail.sources) ? detail.sources : [];
    const observations = Array.isArray(detail.observations) ? detail.observations : [];
    const relations = Array.isArray(detail.relations) ? detail.relations : [];
    const providerIds = [...new Set([
      ...sources.map(item => item?.provider_id),
      ...observations.map(item => item?.provider_id),
    ].filter(Boolean))].slice(0, 10);
    const relationIds = [...new Set(relations.map(
      item => item?.target_incident_id,
    ).filter(Boolean))].slice(0, 10);
    const [health, related] = await Promise.all([
      Promise.all(providerIds.map(async id => {
        try {
          const response = await getJSON(`/api/v2/source-health/${encodeURIComponent(id)}`);
          return response.status === 200 ? response.body : null;
        } catch { return null; }
      })),
      Promise.all(relationIds.map(async id => {
        try {
          const response = await getJSON(`/api/v2/incidents/${encodeURIComponent(id)}`);
          return response.status === 200 ? response.body : null;
        } catch { return null; }
      })),
    ]);
    if (version !== requestVersion) return;
    healthRows = health.filter(Boolean);
    relatedRows = related.filter(Boolean);
  }

  async function open(incidentId, { opener = document.activeElement } = {}) {
    selectedId = String(incidentId || '');
    if (!selectedId) return false;
    const version = ++requestVersion;
    lastFocus = opener;
    detail = null;
    timeline = [];
    timelineAvailable = true;
    healthRows = [];
    relatedRows = [];
    windowHours = 24;
    drawer().hidden = false;
    drawer().setAttribute('aria-hidden', 'false');
    document.getElementById('incident-drawer-backdrop').hidden = false;
    setBackgroundInert(true);
    body().replaceChildren(element('p', 'drawer-loading', 'Loading local incident history…'));
    document.getElementById('incident-drawer-title').focus();
    try {
      const encoded = encodeURIComponent(selectedId);
      const [detailResult, timelineResult] = await Promise.all([
        getJSON(`/api/v2/incidents/${encoded}`).then(response => ({ response })).catch(() => ({ response: null })),
        getJSON(`/api/v2/incidents/${encoded}/timeline?limit=200`).then(response => ({ response })).catch(() => ({ response: null })),
      ]);
      if (version !== requestVersion) return false;
      const detailResponse = detailResult.response;
      const timelineResponse = timelineResult.response;
      if (detailResponse?.status !== 200 || !detailResponse.body?.incident_id) throw new Error('detail unavailable');
      detail = detailResponse.body;
      timelineAvailable = timelineResponse?.status === 200 && Array.isArray(timelineResponse.body?.items);
      timeline = timelineAvailable ? timelineResponse.body.items : [];
      await fetchEnrichment(version);
      if (version !== requestVersion) return false;
      render();
      live().textContent = timelineAvailable
        ? `Incident details loaded for ${detail.headline || 'incident'}.`
        : `Incident details loaded for ${detail.headline || 'incident'}; revision history is unavailable.`;
      return true;
    } catch {
      if (version !== requestVersion) return false;
      const content = body();
      content.replaceChildren();
      append(content, 'h2', '', 'Incident details unavailable');
      append(content, 'p', '', 'The local incident history could not be read. The Now card remains available.');
      const retry = append(content, 'button', '', 'Retry');
      retry.type = 'button';
      retry.addEventListener('click', () => open(selectedId, { opener: lastFocus }));
      live().textContent = 'Incident details could not be loaded.';
      return false;
    }
  }

  function close() {
    if (drawer().hidden) return;
    requestVersion += 1;
    drawer().hidden = true;
    drawer().setAttribute('aria-hidden', 'true');
    document.getElementById('incident-drawer-backdrop').hidden = true;
    setBackgroundInert(false);
    if (lastFocus?.isConnected) lastFocus.focus();
    else document.getElementById('overview-title')?.focus();
  }

  async function copySummary() {
    if (!detail) return false;
    try {
      await navigator.clipboard.writeText(buildDeterministicSummary(detail, timeline));
      live().textContent = 'Deterministic incident summary copied.';
      return true;
    } catch {
      live().textContent = 'Clipboard access is unavailable.';
      return false;
    }
  }

  function printSelected() {
    if (!detail) return false;
    const popup = window.open('', '_blank', 'width=850,height=900');
    if (!popup) {
      live().textContent = 'Allow pop-ups to open the printable incident briefing.';
      return false;
    }
    popup.document.write(buildIncidentBriefingHtml(detail, timeline, now()));
    popup.document.close();
    popup.document.getElementById('incident-briefing-print')?.addEventListener('click', () => popup.print());
    popup.document.getElementById('incident-briefing-close')?.addEventListener('click', () => popup.close());
    return true;
  }

  function trapFocus(event) {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = [...drawer().querySelectorAll('button, a[href], input, [tabindex]:not([tabindex="-1"])')]
      .filter(node => !node.disabled && !node.hidden && node.getClientRects().length);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable.at(-1);
    const title = document.getElementById('incident-drawer-title');
    if (event.shiftKey && (document.activeElement === first || document.activeElement === title)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function start() {
    if (started) return;
    started = true;
    document.getElementById('incident-drawer-close').addEventListener('click', close);
    document.getElementById('incident-drawer-backdrop').addEventListener('click', close);
    drawer().addEventListener('keydown', trapFocus);
  }

  return Object.freeze({ start, open, close, copySummary, printSelected });
}
