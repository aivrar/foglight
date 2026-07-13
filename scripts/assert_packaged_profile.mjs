#!/usr/bin/env node
import { chromium } from '@playwright/test';

const [base, profile] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base || '')) {
  throw new Error('expected a loopback Foglight URL');
}
if (!['standard', 'first-offline', 'overview-default'].includes(profile)) {
  throw new Error('expected standard, first-offline, or overview-default profile');
}

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  if (profile === 'standard') {
    await page.locator('body.mode-standard').waitFor();
    await page.locator('#map.leaflet-container').waitFor();
  } else {
    if (profile === 'overview-default') {
      await page.locator('body.mode-overview').waitFor();
    }
    await page.locator('#overview-surface').waitFor({ state: 'visible' });
    await page.waitForFunction(() => {
      const state = document.getElementById('overview-surface')?.dataset.viewState;
      return Boolean(state && state !== 'loading');
    });
    await page.locator('#overview-state-title').waitFor({ state: 'visible' });
  }
  if (pageErrors.length) {
    throw new Error(`packaged page error: ${pageErrors.join('; ')}`);
  }
  process.stdout.write(JSON.stringify({ profile, title: await page.title() }));
} finally {
  await browser.close();
}
