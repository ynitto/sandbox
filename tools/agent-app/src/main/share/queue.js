'use strict';

// 共有の列の「並び」と「資格」。純関数だけ（ファイルもネットワークも触らない）。
//
// 列のファイルは無い。参加者は LAN で知った全員の依頼を集め、ここで並べ、上から拾う。
// 全員が同じ規則で並べるので、誰が見ても同じ列になる。
//
//   並び鍵 = ( 実効優先度 降順, 依頼者の今日の落札数 昇順, 投函時刻 昇順, id )
//   実効優先度 = 宣言（high 2 / normal 1 / low 0）+ 待ち 30 分ごとに 1（上限 2）
//
// 依頼者の今日の落札数は依頼者が自分で数えて依頼に添える（LAN の同僚を信頼する）。
// 少数の大量依頼者は自然に後ろへ回り、low は待てば繰り上がる。

const PRIORITY = { high: 2, normal: 1, low: 0 };
const PRIORITIES = Object.keys(PRIORITY);
const AGING_MS = 30 * 60 * 1000;
const AGING_MAX = 2;

function priorityOf(value) {
  return PRIORITIES.includes(value) ? value : 'normal';
}

function effectivePriority(request, now = Date.now()) {
  const posted = Date.parse(request.posted_at || '') || now;
  const waited = Math.max(0, now - posted);
  return PRIORITY[priorityOf(request.priority)] + Math.min(AGING_MAX, Math.floor(waited / AGING_MS));
}

function order(requests, now = Date.now()) {
  return [...requests].sort((a, b) => (effectivePriority(b, now) - effectivePriority(a, now))
    || ((Number(a.requester_served_today) || 0) - (Number(b.requester_served_today) || 0))
    || ((Date.parse(a.posted_at || '') || 0) - (Date.parse(b.posted_at || '') || 0))
    || String(a.id).localeCompare(String(b.id)));
}

// 1 件の依頼を、この参加者が拾えるか。
//   ctx.node            … 自分の名前
//   ctx.clis            … 提供できる CLI（この PC で使えるもの ∩ 設定で提供すると決めたもの）
//   ctx.cliOk(cli)      … その CLI がいま受けられるか（quota 切れでない）
//   ctx.acceptWrite     … 書き込みの依頼を受けるか
//   ctx.repoFor(url)    … その URL のリポジトリを登録しているか（登録フォルダ or ''）
//   ctx.servedToday     … 依頼者ごとに今日この PC が答えた数（{ node: n }）
//   ctx.perRequesterCap … 依頼者 1 人あたりの 1 日の上限（0 = 無制限）
// 空き（同時数・1 日の上限）は列全体の話なので pick 側で見る。
function eligible(request, ctx) {
  if (!request || request.state !== 'open') return { ok: false, reason: 'state' };
  if (request.posted_by === ctx.node) return { ok: false, reason: 'own' };
  const mode = request.mode === 'write' ? 'write' : 'read';
  if (mode === 'write' && !ctx.acceptWrite) return { ok: false, reason: 'write' };
  const offered = Array.isArray(ctx.clis) ? ctx.clis : [];
  const wanted = Array.isArray(request.requires && request.requires.agent_cli) ? request.requires.agent_cli : [];
  const candidates = wanted.length ? wanted.filter((cli) => offered.includes(cli)) : offered;
  if (!candidates.length) return { ok: false, reason: 'cli' };
  const cli = candidates.find((name) => (ctx.cliOk ? ctx.cliOk(name) : true));
  if (!cli) return { ok: false, reason: 'quota' };
  const cap = Number(ctx.perRequesterCap) || 0;
  const served = (ctx.servedToday && Number(ctx.servedToday[request.posted_by])) || 0;
  if (cap > 0 && served >= cap) return { ok: false, reason: 'requester_cap' };
  const url = request.workspace && request.workspace.url ? String(request.workspace.url) : '';
  if (url && !(ctx.repoFor ? ctx.repoFor(url) : '')) return { ok: false, reason: 'repo' };
  return { ok: true, cli, mode };
}

// 拾う候補を並び順に返す。空きの数（slots）だけ返す。
function pick(requests, ctx, { now = Date.now(), slots = 1 } = {}) {
  if (slots <= 0) return [];
  const out = [];
  for (const request of order(requests, now)) {
    const verdict = eligible(request, ctx);
    if (!verdict.ok) continue;
    out.push({ request, cli: verdict.cli, mode: verdict.mode });
    if (out.length >= slots) break;
  }
  return out;
}

module.exports = { PRIORITY, PRIORITIES, AGING_MS, AGING_MAX, priorityOf, effectivePriority, order, eligible, pick };
