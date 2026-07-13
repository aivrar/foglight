#!/usr/bin/env node
import { spawn } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

import { chromium } from '@playwright/test';

const sampleArg = process.argv.indexOf('--samples');
const sampleCount = sampleArg >= 0 ? Number(process.argv[sampleArg + 1]) : 20;
if (!Number.isInteger(sampleCount) || sampleCount < 5 || sampleCount > 100) {
  throw new Error('--samples must be an integer in 5..100');
}

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const port = 19878;
const origin = `http://127.0.0.1:${port}`;
const startedAt = performance.now();
const server = spawn('python', ['scripts/run_test_server.py', '--port', String(port)], {
  cwd: root,
  env: { ...process.env, PYTHONUNBUFFERED: '1' },
  stdio: ['ignore', 'ignore', 'pipe'],
  windowsHide: true,
});

async function waitUntilReady() {
  let lastError;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(`${origin}/api/ping`);
      if (response.ok) return performance.now() - startedAt;
    } catch (error) {
      lastError = error;
    }
    if (server.exitCode !== null) throw new Error(`test server exited with ${server.exitCode}`);
    await delay(50);
  }
  throw new Error(`test server did not become ready: ${lastError || 'timeout'}`);
}

const fixtureBodies = {
  '/api/session': { token: 'baseline-session-token' },
  '/api/settings': {
    keys: { nasa_firms: false }, audio: { master: false },
    panels: { tv: true, conflict: true, cyclones: true, relief: true, iss: true },
    tv_channel: 'aljazeera', watchlist: [], annotations: [], rss_feeds: [], first_run_done: true,
  },
  '/api/usgs': {
    type: 'FeatureCollection',
    features: [{
      type: 'Feature', geometry: { type: 'Point', coordinates: [139.7, 35.6, 20] },
      properties: { id: 'baseline-quake', mag: 6.2, place: 'Baseline Coast', time: 1783700000000 },
    }],
  },
  '/api/nws': { type: 'FeatureCollection', features: [] },
};

let browser;
try {
  const serverStartupMs = await waitUntilReady();
  browser = await chromium.launch();
  const samples = [];
  for (let sample = 0; sample < sampleCount; sample += 1) {
    const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/api/')) {
        const body = fixtureBodies[url.pathname] ?? {};
        return route.fulfill({
          status: 200,
          headers: { 'X-Foglight-Freshness': 'live' },
          contentType: 'application/json',
          body: JSON.stringify(body),
        });
      }
      if (url.origin === origin) return route.continue();
      return route.abort();
    });
    const navigationStarted = performance.now();
    await page.goto(origin, { waitUntil: 'domcontentloaded' });
    await page.locator('#topbar').waitFor({ state: 'visible' });
    const firstShellPaintMs = performance.now() - navigationStarted;
    await page.locator('#map.leaflet-container').waitFor({ state: 'visible' });
    await page.locator('#map-status').getByText('Offline world base ready.').waitFor();
    const mapRenderMs = performance.now() - navigationStarted;
    await page.locator('#body-quakes').getByText('Baseline Coast').waitFor();
    const firstIncidentPaintMs = performance.now() - navigationStarted;
    const heap = await page.evaluate(() => ({
      used: performance.memory?.usedJSHeapSize ?? null,
      total: performance.memory?.totalJSHeapSize ?? null,
      domContentLoaded: performance.getEntriesByType('navigation')[0]?.domContentLoadedEventEnd ?? null,
    }));
    samples.push({ firstShellPaintMs, mapRenderMs, firstIncidentPaintMs, heap });
    await page.close();
  }
  const percentile = (values, ratio) => {
    const ordered = [...values].sort((a, b) => a - b);
    const index = Math.min(ordered.length - 1, Math.max(0, Math.round((ordered.length - 1) * ratio)));
    return ordered[index];
  };
  const metric = key => {
    const values = samples.map(item => item[key]);
    return {
      median: percentile(values, 0.5),
      p95: percentile(values, 0.95),
    };
  };
  const shell = metric('firstShellPaintMs');
  const map = metric('mapRenderMs');
  const incident = metric('firstIncidentPaintMs');
  const heapValues = samples.map(item => item.heap.used).filter(Number.isFinite);
  const domValues = samples.map(item => item.heap.domContentLoaded).filter(Number.isFinite);
  const result = {
    schema_version: 1,
    measured_at: new Date().toISOString(),
    samples: samples.length,
    server_process_spawn_to_ping_ms: Number(serverStartupMs.toFixed(3)),
    first_shell_paint_median_ms: Number(shell.median.toFixed(3)),
    first_shell_paint_p95_ms: Number(shell.p95.toFixed(3)),
    map_render_median_ms: Number(map.median.toFixed(3)),
    map_render_p95_ms: Number(map.p95.toFixed(3)),
    first_incident_paint_median_ms: Number(incident.median.toFixed(3)),
    first_incident_paint_p95_ms: Number(incident.p95.toFixed(3)),
    browser_used_js_heap_median_bytes: heapValues.length ? percentile(heapValues, 0.5) : null,
    browser_used_js_heap_p95_bytes: heapValues.length ? percentile(heapValues, 0.95) : null,
    dom_content_loaded_median_ms: domValues.length ? percentile(domValues, 0.5) : null,
    dom_content_loaded_p95_ms: domValues.length ? percentile(domValues, 0.95) : null,
  };
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
} finally {
  if (browser) await browser.close();
  server.kill();
}
