export function createApiClient({ base = '', fetchImpl = globalThis.fetch } = {}) {
  let sessionToken = '';

  async function request(url, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    const headers = { ...(options.headers || {}) };
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && sessionToken) {
      headers['X-Foglight-Token'] = sessionToken;
    }
    return fetchImpl(base + url, { cache: 'no-store', ...options, headers });
  }

  async function loadSession() {
    const response = await fetchImpl(base + '/api/session', { cache: 'no-store' });
    if (!response.ok) throw new Error(`session bootstrap failed (${response.status})`);
    const body = await response.json();
    if (!body || typeof body.token !== 'string' || !body.token) {
      throw new Error('session bootstrap returned no token');
    }
    sessionToken = body.token;
  }

  async function getJSON(url) {
    const response = await request(url);
    const fresh = response.headers.get('X-Foglight-Freshness') || 'unknown';
    let body = null;
    try { body = await response.json(); } catch { body = null; }
    return { body, fresh, status: response.status };
  }

  return Object.freeze({ request, loadSession, getJSON });
}
