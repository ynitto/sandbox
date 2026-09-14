'use strict';
const crypto = require('crypto');

// One local page and at most two peer pages per fetch. Buffered metadata stays small;
// full remote transcripts are fetched only by SessionBrowser.read/prepare.
class SharedSessionSearch {
  constructor(browser, share) { this.browser = browser; this.share = share; this.pages = new Map(); this.jobs = new Map(); }
  cancel(id) { this.jobs.get(id)?.abort(); this.browser.cancel(`${id}:local`); }
  async search(query, requestId, cursor = '') {
    let page, id, index = 0;
    if (cursor) {
      const parts = cursor.split(':'); id = parts[1]; index = Number(parts[2]); page = this.pages.get(id);
      if (parts.length !== 3 || !page || page.query !== JSON.stringify(query) || !Number.isInteger(index) || index < 0 || index > page.results.length) throw new Error('検索結果を更新してください');
      if (page.results[index]) return page.results[index];
      if (page.busy) throw new Error('検索中です');
    } else {
      id = crypto.randomUUID();
      let peers = [], errors = [];
      try { peers = this.share.publicPeers().map(p => ({ node: p.node, cursor: '', done: false })); }
      catch (err) { errors.push({ message: err.message }); }
      page = { query: JSON.stringify(query), localCursor: '', localDone: false, peers, buffer: [], results: [], errors, count: 0 };
      this.pages.set(id, page); while (this.pages.size > 5) this.pages.delete(this.pages.keys().next().value);
    }
    const controller = new AbortController(); this.jobs.set(requestId, controller); page.busy = true;
    // Mutate only the working copy; cancellation can retry the same cursor.
    const next = { ...page, peers: page.peers.map(p => ({ ...p })), buffer: [...page.buffer], errors: [...page.errors] };
    const { shared, ...localQuery } = query;
    try {
      if (next.buffer.length < 50) {
        const jobs = [];
        if (!next.localDone) jobs.push((async () => {
          const result = await this.browser.search(localQuery, `${requestId}:local`, next.localCursor);
          next.localCursor = result.cursor; next.localDone = !result.cursor;
          next.buffer.push(...result.sessions); next.errors.push(...result.errors);
        })());
        for (const peer of next.peers.filter(p => !p.done).slice(0, 2)) jobs.push((async () => {
          try {
            const result = await this.share.searchPublic(peer.node, localQuery, peer.cursor, controller.signal);
            peer.cursor = result.cursor; peer.done = !result.cursor;
            next.buffer.push(...result.sessions); next.errors.push(...result.errors);
          } catch (err) { peer.done = true; next.errors.push({ provider: peer.node, message: err.message }); }
        })());
        const results = await Promise.allSettled(jobs);
        for (const r of results) if (r.status === 'rejected') { next.localDone = true; next.errors.push({ message: r.reason.message }); }
      }
      if (controller.signal.aborted) throw new Error('検索を中止しました');
      next.buffer.sort((a, b) => b.updatedAt - a.updatedAt || a.key.localeCompare(b.key));
      const sessions = next.buffer.splice(0, 50);
      const more = next.buffer.length > 0 || !next.localDone || next.peers.some(p => !p.done);
      const result = { sessions, page: index + 1, cursor: more ? `shared:${id}:${index + 1}` : '',
        previous: index ? `shared:${id}:${index - 1}` : '', total: next.count + sessions.length,
        totalExact: !more, errors: next.errors.slice(-20), partial: next.errors.length > 0 };
      Object.assign(page, next, { count: result.total, errors: result.errors }); page.results.push(result);
      return result;
    } finally { page.busy = false; this.jobs.delete(requestId); }
  }
}
module.exports = { SharedSessionSearch };
