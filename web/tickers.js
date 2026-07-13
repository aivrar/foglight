export function createTickerControllers({
  store, byId, request, getJSON, setBadge, recordFreshness, fillStream,
  combineFreshness, formatTime, escapeHtml, safeHttpUrl,
  getCommodities, setBitcoinPrice,
}) {
  const $ = byId;
  const fget = request;
  const fgetJSON = getJSON;
  const fmtTime = formatTime;


  // ============================================================
  // SEC EDGAR
  // ============================================================

  async function refreshSEC() {
    if (!store.state.ui.panels.sec) return;
    const r = await fget('/api/sec');
    const fresh = r.headers.get('X-Foglight-Freshness') || 'unknown';
    setBadge('bd-sec', fresh);
    recordFreshness('sec', fresh);
    const text = await r.text();
    const entries = text.split(/<entry>/i).slice(1, 41);
    if (!entries.length) {
      $('body-sec').innerHTML = '<div class="empty">No filings.</div>';
      return;
    }
    let html = '';
    for (const e of entries) {
      const m = (re) => { const r = e.match(re); return r ? r[1].replace(/<[^>]*>/g, '').trim() : ''; };
      const title = m(/<title[^>]*>([\s\S]*?)<\/title>/i);
      const updated = m(/<updated[^>]*>([\s\S]*?)<\/updated>/i);
      const cat = m(/term="([^"]+)"/i);
      const linkM = e.match(/<link[^>]*href="([^"]+)"/i);
      const link = linkM ? linkM[1] : '';
      if (!title) continue;
      const dt = updated ? new Date(updated) : null;
      const t = dt ? fmtTime(dt) : '--';
      const url = safeHttpUrl(link);
      const tag = url ? 'a' : 'div';
      const href = url ? ` href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"` : '';
      html += `<${tag} class="ln sec"${href}><span class="t">${t}</span><span class="who">${escapeHtml(cat || 'FILE')}</span><span class="ttl">${escapeHtml(title.slice(0,110))}</span></${tag}>`;
    }
    fillStream($('body-sec'), html);
  }

  // ============================================================
  // HACKER NEWS + REDDIT
  // ============================================================

  async function refreshTalk() {
    if (!store.state.ui.panels.talk) return;
    const hn = await fgetJSON('/api/hn/top');
    recordFreshness('hacker-news', hn.fresh);
    let hnList = [];
    if (Array.isArray(hn.body)) {
      const ids = hn.body.slice(0, 8);
      const items = await Promise.all(
        ids.map(id => fgetJSON('/api/hn/item/' + id).then(x => x.body))
      );
      hnList = items.filter(x => x && x.title);
    }
    const reddit = await fgetJSON('/api/reddit');
    recordFreshness('reddit', reddit.fresh);
    const rd = reddit.body && Array.isArray(reddit.body.items)
      ? reddit.body.items.slice(0, 8) : [];
    setBadge('bd-talk', combineFreshness([hn.fresh, reddit.fresh]));

    let html = '';
    for (const it of hnList) {
      const url = safeHttpUrl(it.url || `https://news.ycombinator.com/item?id=${it.id}`);
      if (!url) continue;
      html += `<a class="ln tlk" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"><span class="t">HN</span><span class="who">+${it.score || 0}</span><span class="ttl">${escapeHtml((it.title || '').slice(0,110))}</span></a>`;
    }
    for (const it of rd) {
      const url = safeHttpUrl(it.link);
      if (!url) continue;
      html += `<a class="ln tlk" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"><span class="t">r/</span><span class="who">popular</span><span class="ttl">${escapeHtml((it.title || '').slice(0,110))}</span></a>`;
    }
    fillStream($('body-talk'), html);
  }

  // ============================================================
  // CRYPTO + FOREX + NEWS TICKERS (slow)
  // ============================================================

  async function refreshCrypto() {
    const { body, fresh } = await fgetJSON('/api/crypto');
    recordFreshness('crypto', fresh);
    if (!Array.isArray(body)) return;
    const top = body.slice(0, 30);
    const btc = body.find(t => t && (t.symbol === 'BTC' || t.id === 'btc-bitcoin'));
    if (btc && btc.quotes && btc.quotes.USD && btc.quotes.USD.price) {
      setBitcoinPrice(btc.quotes.USD.price);
    }
    // Commodities (oil, gas, gold, etc.) prepended to the ticker so they
    // appear before the crypto block --- generals care about WTI more than DOGE.
    let commodityHtml = '';
    if (getCommodities()) {
      for (const [label, c] of Object.entries(getCommodities())) {
        const cls = c.chg >= 0 ? 'up' : 'down';
        const sign = c.chg >= 0 ? '+' : '';
        commodityHtml +=
          `<span class="ti-crypto"><span class="sym">${escapeHtml(label)}</span>` +
          `<span>$${c.close.toFixed(2)}</span>` +
          `<span class="chg ${cls}">${sign}${c.chg.toFixed(2)}%</span></span>`;
      }
    }
    let cryptoHtml = top.map(t => {
      const px = Number((t.quotes && t.quotes.USD && t.quotes.USD.price) || 0);
      const ch = Number((t.quotes && t.quotes.USD && t.quotes.USD.percent_change_24h) || 0);
      const cls = ch >= 0 ? 'up' : 'down';
      const sign = ch >= 0 ? '+' : '';
      const price = px >= 1000 ? '$' + Math.round(px).toLocaleString()
                  : px >= 1 ? '$' + px.toFixed(2)
                  : '$' + px.toFixed(4);
      return `<span class="ti-crypto"><span class="sym">${escapeHtml(t.symbol || t.id)}</span><span>${price}</span><span class="chg ${cls}">${sign}${ch.toFixed(2)}%</span></span>`;
    }).join('');

    // Append forex pairs from the cached forex response if present.
    if (LAST_FOREX && LAST_FOREX.rates) {
      const want = ['EUR', 'GBP', 'JPY', 'CHF', 'CNY', 'AUD', 'CAD'];
      cryptoHtml += want.map(c => {
        const r = LAST_FOREX.rates[c];
        if (r == null) return '';
        const inv = (c === 'JPY' ? r : (1 / r));
        return `<span class="ti-crypto"><span class="sym">USD/${c}</span><span>${inv.toFixed(c === 'JPY' ? 2 : 4)}</span></span>`;
      }).join('');
    }
    // Commodities first, then crypto, then forex (already appended). Double
    // the whole thing for seamless scroll loop.
    const combined = commodityHtml + cryptoHtml;
    $('ticker-crypto-track').innerHTML = combined + combined;
  }

  let LAST_FOREX = null;
  async function refreshForex() {
    const { body, fresh } = await fgetJSON('/api/forex');
    recordFreshness('forex', fresh);
    if (body && body.rates) LAST_FOREX = body;
  }

  async function refreshNews() {
    let feeds;
    try {
      const s = await fgetJSON('/api/settings');
      feeds = (s.body && s.body.rss_feeds) || [];
    } catch { feeds = []; }

    const headlines = [];
    const results = await Promise.all(feeds.map(async url => {
      try {
        const r = await fget('/api/rss?url=' + encodeURIComponent(url));
        const fresh = r.headers.get('X-Foglight-Freshness') || 'unknown';
        const text = await r.text();
        const src = (url.match(/\/\/([^/]+)\//) || [, ''])[1].replace(/^www\./, '').split('.')[0] || 'rss';
        const items = [];
        const re = /<item[^>]*>([\s\S]*?)<\/item>/gi;
        let m, n = 0;
        while ((m = re.exec(text)) && n < 8) {
          const blk = m[1];
          const t = (blk.match(/<title[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/title>/i) || [, ''])[1].trim();
          const d = (blk.match(/<pubDate[^>]*>([\s\S]*?)<\/pubDate>/i) || [, ''])[1].trim();
          if (t) items.push({ src, t, d });
          n++;
        }
        return { fresh, items };
      } catch { return { fresh: 'error', items: [] }; }
    }));
    if (results.length) {
      recordFreshness('news-rss', combineFreshness(results.map(item => item.fresh)));
    } else {
      recordFreshness('news-rss', null);
    }
    results.forEach(result => headlines.push(...result.items));
    if (!headlines.length) return;
    for (let i = headlines.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [headlines[i], headlines[j]] = [headlines[j], headlines[i]];
    }
    const html = headlines.slice(0, 40).map(it => {
      const tsStr = it.d ? (() => { try { return fmtTime(new Date(it.d)); } catch { return ''; } })() : '';
      return `<span class="ti-news"><span class="src">${escapeHtml(it.src)}</span><span class="sep"></span><span>${escapeHtml(it.t)}</span>${tsStr ? `<span class="ts">${tsStr}</span>` : ''}</span>`;
    }).join('');
    $('ticker-news-track').innerHTML = html + html;
  }

  return { refreshSEC, refreshTalk, refreshCrypto, refreshForex, refreshNews };
}
