export function createCommunityControllers({
  store, byId, getJSON, setBadge, recordFreshness, fillStream,
  elapsed, formatTime, escapeHtml, safeHttpUrl, playCue, getBitcoinPrice,
}) {
  const $ = byId;
  const fgetJSON = getJSON;
  const ago = elapsed;
  const fmtTime = formatTime;


  // ============================================================
  // BITCOIN PULSE (mempool.space)
  // ============================================================

  let LAST_BLOCK_HEIGHT = null;

  async function refreshBitcoin() {
    if (!store.state.ui.panels.btc) return;  // skip work if hidden
    const { body, fresh } = await fgetJSON('/api/mempool');
    setBadge('bd-btc', fresh);
    recordFreshness('bitcoin', fresh);
    if (!body) return;

    const fees   = body.fees      || {};
    const mp     = body.mempool   || {};
    const blocks = Array.isArray(body.blocks) ? body.blocks : [];
    const adj    = body.difficulty || {};

    // BTC price comes from the crypto endpoint cache; refreshCrypto stores
    // the latest BTC tick in getBitcoinPrice() on its way through.
    if (typeof getBitcoinPrice() === 'number' && getBitcoinPrice() > 0) {
      $('btc-price').textContent = '$' + Math.round(getBitcoinPrice()).toLocaleString();
    }

    $('btc-mempool-count').textContent = (mp.count != null) ? mp.count.toLocaleString() : '--';
    $('btc-mempool-vsize').textContent = mp.vsize ? Math.round(mp.vsize / 1e6) + ' MB' : '--';

    $('btc-fast-fee').textContent = fees.fastestFee != null ? fees.fastestFee + ' sat/vB' : '--';
    $('btc-econ-fee').textContent = fees.economyFee != null ? fees.economyFee + ' sat/vB' : '--';

    if (blocks.length) {
      const top = blocks[0];
      $('btc-height').textContent = top.height ? top.height.toLocaleString() : '--';
      if (top.timestamp) {
        const a = Math.max(0, Math.floor(Date.now() / 1000) - top.timestamp);
        $('btc-last-block').textContent = ago(a) + ' ago';
      }
      if (LAST_BLOCK_HEIGHT != null && top.height > LAST_BLOCK_HEIGHT) playCue('bitcoin_block');
      LAST_BLOCK_HEIGHT = top.height || LAST_BLOCK_HEIGHT;
    }
    if (adj && adj.remainingBlocks != null) {
      $('btc-adj').textContent = adj.remainingBlocks + ' blocks';
      const dc = adj.difficultyChange;
      const cd = $('btc-diff-change');
      cd.textContent = (dc != null) ? (dc > 0 ? '+' : '') + dc.toFixed(2) + '%' : '--';
      cd.style.color = dc > 0 ? 'var(--good)' : dc < 0 ? 'var(--hot)' : 'var(--text)';
    }
  }


  // ============================================================
  // WIKIPEDIA edit stream
  // ============================================================

  let LAST_WIKI_TS = 0;
  const WIKI_BUF = [];

  async function refreshWiki() {
    if (!store.state.ui.panels.wiki) return;
    const { body, fresh } = await fgetJSON('/api/wiki/recent?limit=80');
    setBadge('bd-wiki', fresh);
    recordFreshness('wikimedia', fresh);
    if (!body || !body.events) return;
    const events = body.events;
    const dot = $('stat-wiki').querySelector('.dot');
    const txt = $('stat-wiki').querySelector('span:last-child');
    if (events.length) {
      dot.className = 'dot';
      txt.textContent = events.length + ' edits';
    }
    // Maintain a sliding buffer of latest 30 events for the stream.
    for (const e of events) {
      if ((e.ts || 0) <= LAST_WIKI_TS) continue;
      WIKI_BUF.unshift(e);
    }
    if (events.length) LAST_WIKI_TS = events[events.length - 1].ts || LAST_WIKI_TS;
    while (WIKI_BUF.length > 30) WIKI_BUF.pop();

    let html = '';
    for (const e of WIKI_BUF) {
      const t = fmtTime(new Date((e.ts || 0) * 1000));
      const who = (e.user || '?').slice(0, 14);
      const sz = (e.length && e.length.new != null && e.length.old != null)
        ? (e.length.new - e.length.old) : 0;
      const base = safeHttpUrl(e.serverurl || 'https://en.wikipedia.org') || 'https://en.wikipedia.org/';
      const url = safeHttpUrl(`${base.replace(/\/$/, '')}/wiki/${encodeURIComponent((e.title || '').replace(/ /g, '_'))}`);
      html += `<a class="ln wk" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"><span class="t">${t}</span><span class="who">${escapeHtml(who)}</span><span class="ttl">${escapeHtml(e.title || '?')}</span><span class="sz">${sz >= 0 ? '+' : ''}${sz}</span></a>`;
    }
    fillStream($('body-wiki'), html);
  }

  // ============================================================
  // GITHUB
  // ============================================================

  const GH_VERBS = {
    PushEvent:        'pushed',
    PullRequestEvent: 'PR',
    IssuesEvent:      'issue',
    CreateEvent:      'created',
    ReleaseEvent:     'released',
    ForkEvent:        'forked',
    WatchEvent:       'starred',
  };

  async function refreshGitHub() {
    if (!store.state.ui.panels.github) return;
    const { body, fresh } = await fgetJSON('/api/github');
    setBadge('bd-gh', fresh);
    recordFreshness('github', fresh);
    if (!Array.isArray(body)) return;
    let html = '';
    for (const ev of body.slice(0, 30)) {
      const verb = GH_VERBS[ev.type] || ev.type;
      const t = fmtTime(new Date(ev.created_at));
      const who = (ev.actor && ev.actor.login) || '?';
      const repo = (ev.repo && ev.repo.name) || '?';
      const url = safeHttpUrl(`https://github.com/${repo}`);
      html += `<a class="ln gh" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"><span class="t">${t}</span><span class="who">${escapeHtml(who)}</span><span class="evt">${escapeHtml(verb)}</span><span class="ttl">${escapeHtml(repo)}</span></a>`;
    }
    fillStream($('body-gh'), html);
  }

  return { refreshBitcoin, refreshWiki, refreshGitHub };
}
