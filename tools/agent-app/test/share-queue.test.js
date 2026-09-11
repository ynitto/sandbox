'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const queue = require('../src/main/share/queue');

const T0 = Date.parse('2026-09-12T00:00:00Z');
const at = (min) => new Date(T0 + min * 60 * 1000).toISOString();
const req = (over) => ({ id: 'x', state: 'open', posted_by: 'a', posted_at: at(0), priority: 'normal', requires: { agent_cli: [] }, mode: 'read', requester_served_today: 0, ...over });

test('並び: 実効優先度 → 依頼者の今日の落札数 → 投函時刻', () => {
  const list = [
    req({ id: 'late-normal', posted_at: at(10) }),
    req({ id: 'early-normal', posted_at: at(0) }),
    req({ id: 'high', priority: 'high', posted_at: at(20) }),
    req({ id: 'low', priority: 'low', posted_at: at(0) }),
    req({ id: 'busy-requester', posted_by: 'b', requester_served_today: 5, posted_at: at(0) }),
  ];
  const now = T0 + 25 * 60 * 1000;
  assert.deepEqual(queue.order(list, now).map((r) => r.id), ['high', 'early-normal', 'late-normal', 'busy-requester', 'low']);
});

test('待ち時間で繰り上がる（30 分ごとに +1、上限 2）。low も待てば normal を追い越す', () => {
  const old = req({ id: 'old-low', priority: 'low', posted_at: at(0) });
  const fresh = req({ id: 'fresh-normal', posted_at: at(60) });
  const now = T0 + 61 * 60 * 1000;
  assert.equal(queue.effectivePriority(old, now), 2);
  assert.equal(queue.effectivePriority(fresh, now), 1);
  assert.deepEqual(queue.order([fresh, old], now).map((r) => r.id), ['old-low', 'fresh-normal']);
  assert.equal(queue.effectivePriority(req({ priority: 'high', posted_at: at(0) }), T0 + 10 * 60 * 60 * 1000), 4);
});

test('資格: 自分の依頼・CLI の不一致・書き込み・依頼者の上限・リポジトリ無しを弾き、CLI を選ぶ', () => {
  const ctx = { node: 'me', clis: ['claude', 'codex'], cliOk: (cli) => cli !== 'codex', acceptWrite: false, repoFor: (url) => (url === 'git@x:team/app.git' ? '/repo' : ''), servedToday: { heavy: 5 }, perRequesterCap: 5 };
  assert.equal(queue.eligible(req({ posted_by: 'me' }), ctx).reason, 'own');
  assert.equal(queue.eligible(req({ requires: { agent_cli: ['kiro'] } }), ctx).reason, 'cli');
  assert.equal(queue.eligible(req({ requires: { agent_cli: ['codex'] } }), ctx).reason, 'quota');
  assert.equal(queue.eligible(req({ mode: 'write' }), ctx).reason, 'write');
  assert.equal(queue.eligible(req({ posted_by: 'heavy' }), ctx).reason, 'requester_cap');
  assert.equal(queue.eligible(req({ workspace: { url: 'git@x:team/other.git' } }), ctx).reason, 'repo');
  assert.equal(queue.eligible(req({ state: 'working' }), ctx).reason, 'state');
  assert.deepEqual(queue.eligible(req({ requires: { agent_cli: ['codex', 'claude'] } }), ctx), { ok: true, cli: 'claude', mode: 'read' });
  assert.deepEqual(queue.eligible(req({ workspace: { url: 'git@x:team/app.git' } }), ctx), { ok: true, cli: 'claude', mode: 'read' });
  assert.equal(queue.eligible(req({ mode: 'write' }), { ...ctx, acceptWrite: true }).mode, 'write');
});

test('拾う候補は並び順に、空きの数だけ', () => {
  const ctx = { node: 'me', clis: ['claude'], cliOk: () => true, acceptWrite: false, repoFor: () => '', servedToday: {}, perRequesterCap: 0 };
  const list = [req({ id: 'n1', posted_at: at(0) }), req({ id: 'h', priority: 'high', posted_at: at(5) }), req({ id: 'mine', posted_by: 'me' }), req({ id: 'n2', posted_at: at(1) })];
  assert.deepEqual(queue.pick(list, ctx, { now: T0 + 6 * 60 * 1000, slots: 2 }).map((c) => c.request.id), ['h', 'n1']);
  assert.deepEqual(queue.pick(list, ctx, { now: T0, slots: 0 }), []);
});
