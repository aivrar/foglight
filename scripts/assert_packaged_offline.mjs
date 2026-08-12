#!/usr/bin/env node
import { chromium } from '@playwright/test';

const base = process.argv[2];
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base || '')) {
  throw new Error('expected a loopback Foglight URL');
}

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.locator('#overview-surface').waitFor({ state: 'visible' });
  const offlineLabel = 'Cached local history — not live.';
  const agePattern = /Oldest source cache 2h old\./;
  let history = '';
  {
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      history = (await page.locator('#overview-history-status').textContent()) || '';
      if (history.includes(offlineLabel) && agePattern.test(history)) break;
      await page.waitForTimeout(500);
    }
  }
  if (!history.includes(offlineLabel)) {
    throw new Error(`packaged offline label was not explicit: ${history}`);
  }
  if (!agePattern.test(history)) {
    throw new Error(`packaged cached age was not rendered accurately: ${history}`);
  }
  const incidentCount = await page.locator('#overview-now-list .overview-incident').count();
  if (incidentCount < 1) throw new Error('packaged cached incident was not rendered');
  if (pageErrors.length) throw new Error(`packaged page error: ${pageErrors.join('; ')}`);
  process.stdout.write(JSON.stringify({ history, incidentCount }));
} finally {
  await browser.close();
}
