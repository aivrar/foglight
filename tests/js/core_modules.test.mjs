import assert from 'node:assert/strict';
import test from 'node:test';

import { createApiClient } from '../../web/api.js';
import {
  byId, elapsed, element, escapeHtml, formatUtcTime, safeHttpUrl,
  latestKpValue, runWithConcurrency, updateSourceFreshness, validMapCoordinates,
} from '../../web/core.js';
import { createAppStore } from '../../web/store.js';
import { createSettingsPatch, normalizeInitialSettings } from '../../web/settings.js';

test('pure formatting helpers preserve V1 output and reject unsafe links', () => {
  assert.equal(elapsed(59), '59s');
  assert.equal(elapsed(60), '1m');
  assert.equal(elapsed(3600), '1h');
  assert.equal(elapsed(86400), '1d');
  assert.equal(formatUtcTime(new Date('2026-07-10T03:04:00Z')), '03:04');
  assert.equal(escapeHtml(`<a x="'">&`), '&lt;a x=&quot;&#039;&quot;&gt;&amp;');
  assert.equal(escapeHtml(null), '');
  assert.equal(safeHttpUrl(''), '');
  assert.equal(safeHttpUrl('/story', 'https://example.test'), 'https://example.test/story');
  assert.equal(safeHttpUrl('https://example.test/path'), 'https://example.test/path');
  assert.equal(
    safeHttpUrl('https://example.test/path?token=sensitive&view=full#api_key=hidden'),
    'https://example.test/path?token=%3Credacted%3E&view=full#%3Credacted%3E',
  );
  assert.equal(safeHttpUrl('https://user:pass@example.test/path'), '');
  assert.equal(safeHttpUrl('javascript:alert(1)', 'https://example.test'), '');
  assert.equal(safeHttpUrl('http://[invalid', 'https://example.test'), '');
});

test('DOM helpers create and locate nodes without hidden application state', () => {
  const fixture = { id: 'fixture' };
  globalThis.document = {
    getElementById: id => id === 'fixture' ? fixture : null,
    createElement: tag => ({ tag, className: '', textContent: '' }),
  };
  assert.equal(byId('fixture'), fixture);
  assert.deepEqual(element('div', 'row', 'text'), {
    tag: 'div', className: 'row', textContent: 'text',
  });
  assert.deepEqual(element('span', '', null), {
    tag: 'span', className: '', textContent: '',
  });
  delete globalThis.document;
});

test('source freshness tracks current sources instead of refresh attempts', () => {
  const states = new Map();
  assert.deepEqual(updateSourceFreshness(states, 'usgs', 'live'), {
    live: 1, cached: 0, errored: 0,
  });
  assert.deepEqual(updateSourceFreshness(states, 'usgs', 'live'), {
    live: 1, cached: 0, errored: 0,
  });
  assert.deepEqual(updateSourceFreshness(states, 'nws', 'stale'), {
    live: 1, cached: 1, errored: 0,
  });
  assert.deepEqual(updateSourceFreshness(states, 'usgs', 'error'), {
    live: 0, cached: 1, errored: 1,
  });
  assert.deepEqual(updateSourceFreshness(states, 'usgs', null), {
    live: 0, cached: 1, errored: 0,
  });
  assert.throws(() => updateSourceFreshness({}, 'usgs', 'live'), /must be a Map/);
});

test('space-weather Kp selection follows timestamps instead of payload order', () => {
  assert.equal(latestKpValue([
    { time_tag: '2026-08-30T03:00:00Z', Kp: '2.5' },
    { time_tag: '2026-08-30T01:00:00Z', Kp: '7.0' },
    { time_tag: 'not-a-time', Kp: '9.0' },
  ]), 2.5);
  assert.equal(latestKpValue([
    ['time_tag', 'Kp'],
    ['2026-08-30T03:00:00Z', '6.0'],
    ['2026-08-30T01:00:00Z', '1.0'],
  ]), 6);
  assert.equal(latestKpValue([{ kp: '1.0' }, { Kp: null, kp: '4.5' }]), 4.5);
  assert.equal(latestKpValue([null, 'invalid', { Kp: '' }, ['bad', 'nope']]), null);
  assert.equal(latestKpValue(null), null);
});

test('map coordinates reject missing, nonnumeric, and out-of-range values', () => {
  assert.deepEqual(validMapCoordinates('12.5', '-30.25'), [12.5, -30.25]);
  assert.deepEqual(validMapCoordinates('22.9N', '79.9W'), [22.9, -79.9]);
  assert.deepEqual(validMapCoordinates(-90, 180), [-90, 180]);
  assert.deepEqual(validMapCoordinates(0, 0), [0, 0]);
  assert.equal(validMapCoordinates(null, 0), null);
  assert.equal(validMapCoordinates(0, undefined), null);
  assert.equal(validMapCoordinates('', 0), null);
  assert.equal(validMapCoordinates(false, 0), null);
  assert.equal(validMapCoordinates('22.9W', '79.9N'), null);
  assert.equal(validMapCoordinates(91, 0), null);
  assert.equal(validMapCoordinates(0, -181), null);
  assert.equal(validMapCoordinates(Number.POSITIVE_INFINITY, 0), null);
});

test('bounded task runner preserves order and caps concurrent refresh work', async () => {
  let active = 0;
  let peak = 0;
  const result = await runWithConcurrency([3, 1, 2, 4], async (value, index) => {
    active += 1;
    peak = Math.max(peak, active);
    await new Promise(resolve => setTimeout(resolve, value));
    active -= 1;
    return `${index}:${value}`;
  }, 2);
  assert.deepEqual(result, ['0:3', '1:1', '2:2', '3:4']);
  assert.equal(peak, 2);
  assert.deepEqual(await runWithConcurrency([], value => value), []);
  await assert.rejects(runWithConcurrency(null, () => {}), /items must be an array/);
  await assert.rejects(runWithConcurrency([], null), /worker must be a function/);
  await assert.rejects(runWithConcurrency([], () => {}, 0), /positive integer/);
  await assert.rejects(runWithConcurrency([], () => {}, 1.5), /positive integer/);
});

test('application store owns shared UI, user, and lifecycle state', () => {
  const store = createAppStore({ ui: { theater: 'ukr' } });
  const events = [];
  const unsubscribe = store.subscribe((_state, section) => events.push(section));
  store.update('ui', { tvChannel: 'dw' });
  store.update('lifecycle', { shuttingDown: true });
  unsubscribe();
  store.update('ui', { theater: 'global' });
  assert.deepEqual(events, ['ui', 'lifecycle']);
  assert.equal(store.state.ui.tvChannel, 'dw');
  assert.equal(store.state.lifecycle.shuttingDown, true);
  assert.throws(() => store.update('missing', {}), /invalid store update/);
  assert.throws(() => store.update('ui', null), /invalid store update/);
});

test('settings normalization applies explicit panel and channel defaults', () => {
  const normalized = normalizeInitialSettings(
    { panels: { tv: false }, tv_channel: 'unknown', watchlist: ['storm'] },
    {
      panelIds: ['tv', 'relief', 'wiki'],
      defaultVisible: new Set(['tv', 'relief']),
      tvChannelIds: new Set(['aljazeera', 'dw']),
    },
  );
  assert.deepEqual(normalized.panels, { tv: false, relief: true, wiki: false });
  assert.equal(normalized.tvChannel, 'aljazeera');
  assert.equal(normalized.displayMode, 'overview');
  assert.deepEqual(normalized.audio, { master: false });
  assert.deepEqual(normalized.watchlist, ['storm']);
  assert.equal(normalized.wallDisplay.interval_seconds, 30);
  assert.deepEqual(normalized.annotations, []);
  assert.deepEqual(createSettingsPatch('panels', { tv: true }), {
    panels: { tv: true },
  });
  assert.throws(() => createSettingsPatch('unknown', {}), /unsupported settings section/);

  const complete = normalizeInitialSettings(
    {
      panels: { tv: true },
      audio: { master: true },
      tv_channel: 'dw',
      display_mode: 'command',
      annotations: [{ lat: 1, lon: 2 }],
      wall_display: { interval_seconds: 120 },
    },
    {
      panelIds: ['tv'],
      defaultVisible: new Set(),
      tvChannelIds: new Set(['aljazeera', 'dw']),
      fallbackTvChannel: 'aljazeera',
    },
  );
  assert.equal(complete.tvChannel, 'dw');
  assert.equal(complete.displayMode, 'command');
  assert.deepEqual(complete.audio, { master: true });
  assert.deepEqual(complete.watchlist, []);
  assert.deepEqual(complete.annotations, [{ lat: 1, lon: 2 }]);
  assert.equal(complete.wallDisplay.interval_seconds, 120);

  const empty = normalizeInitialSettings(undefined, {
    panelIds: [], defaultVisible: new Set(), tvChannelIds: new Set(),
  });
  assert.deepEqual(empty.panels, {});
  assert.deepEqual(createSettingsPatch('audio', { master: true }), {
    audio: { master: true },
  });
  assert.deepEqual(createSettingsPatch('keys', { nasa_firms: 'x' }), {
    keys: { nasa_firms: 'x' },
  });
});

test('API client bootstraps a token, authenticates mutations, and reports freshness', async () => {
  const calls = [];
  const responses = [
    { ok: true, status: 200, json: async () => ({ token: 'fixture-token' }) },
    { ok: true, status: 200, headers: { get: () => null }, json: async () => ({}) },
    {
      ok: true,
      status: 200,
      headers: { get: name => name === 'X-Foglight-Freshness' ? 'cached' : null },
      json: async () => ({ items: [1] }),
    },
  ];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return responses.shift();
  };
  const client = createApiClient({ base: 'http://local', fetchImpl });
  await client.loadSession();
  await client.request('/api/settings', { method: 'POST', headers: { Existing: 'yes' } });
  const result = await client.getJSON('/api/data');
  assert.equal(calls[1].options.headers['X-Foglight-Token'], 'fixture-token');
  assert.equal(calls[1].options.headers.Existing, 'yes');
  assert.deepEqual(result, { body: { items: [1] }, fresh: 'cached', status: 200 });
});

test('API client rejects bad sessions and tolerates malformed JSON', async () => {
  const failure = createApiClient({ fetchImpl: async () => ({ ok: false, status: 500 }) });
  await assert.rejects(failure.loadSession(), /bootstrap failed/);

  const missing = createApiClient({
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({}) }),
  });
  await assert.rejects(missing.loadSession(), /returned no token/);

  const malformed = createApiClient({
    fetchImpl: async () => ({
      status: 502,
      headers: { get: () => 'error' },
      json: async () => { throw new Error('bad json'); },
    }),
  });
  assert.deepEqual(await malformed.getJSON('/api/data'), {
    body: null, fresh: 'error', status: 502,
  });
});
