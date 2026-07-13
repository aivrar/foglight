import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const fixtureUrl = new URL('../fixtures/v1/provider_contracts.json', import.meta.url);

test('every V1 provider has valid, empty, and malformed fixtures', async () => {
  const catalog = JSON.parse(await readFile(fixtureUrl, 'utf8'));
  assert.equal(catalog.schema_version, 1);
  const entries = Object.entries(catalog.providers);
  assert.ok(entries.length >= 25);
  for (const [providerId, fixture] of entries) {
    assert.ok(fixture.format, `${providerId} is missing its format`);
    for (const variant of ['valid', 'empty', 'malformed']) {
      assert.ok(Object.hasOwn(fixture, variant), `${providerId} is missing ${variant}`);
    }
  }
});
