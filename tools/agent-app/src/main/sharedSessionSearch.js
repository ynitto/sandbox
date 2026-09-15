'use strict';
// 共有ノードの検索。相手の負荷を増やさないよう、同時に問い合わせるのは 2 ノードまで。
// 取得した会話は呼び出し側の流れへ合流させる（本文は preview / fork のときだけ取りに行く）。
const PARALLEL = 2;

class SharedSessionSearch {
  constructor(browser, share) { this.browser = browser; this.share = share; this.jobs = new Map(); }
  cancel(id) { this.jobs.get(id)?.abort(); }
  // 共有の結果を、届いた順に onSessions へ渡す。戻り値は取得できなかった相手の記録。
  async collect(query, requestId, onSessions, signal) {
    const errors = [];
    let peers = [];
    try { peers = this.share.publicPeers().map(p => ({ node: p.node, cursor: '', done: false })); }
    catch (err) { return [{ message: err.message }]; }
    const controller = new AbortController();
    this.jobs.set(requestId, controller);
    signal?.addEventListener('abort', () => controller.abort(), { once: true });
    const { shared, ...peerQuery } = query;
    const pull = async peer => {
      while (!peer.done && !controller.signal.aborted) {
        try {
          const result = await this.share.searchPublic(peer.node, peerQuery, peer.cursor, controller.signal);
          peer.cursor = result.cursor; peer.done = !result.cursor;
          errors.push(...(result.errors || []));
          if (result.sessions?.length) onSessions(result.sessions);
        } catch (err) {
          peer.done = true;
          if (!controller.signal.aborted) errors.push({ provider: peer.node, message: err.message });
        }
      }
    };
    try {
      const queue = [...peers];
      const runners = Array.from({ length: Math.min(PARALLEL, queue.length) }, async () => {
        while (queue.length && !controller.signal.aborted) await pull(queue.shift());
      });
      await Promise.all(runners);
    } finally { this.jobs.delete(requestId); }
    return errors;
  }
}
module.exports = { SharedSessionSearch };
