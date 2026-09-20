'use strict';

// 受信箱の投影（src/main/attention.js）と「見た」の保存（store.attentionSeen）。
// 正典（会話・タスクの実行履歴・ワークフローの実行）から未読・要対応を派生させるだけで、
// 状態を複製しないことを純粋関数の単位で押さえる。

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const attention = require('../src/main/attention');
const store = require('../src/main/store');
const runHistory = require('../src/main/automation/run-history');

const T0 = '2026-09-10T00:00:00.000Z';
const T1 = '2026-09-11T00:00:00.000Z';
const T2 = '2026-09-12T00:00:00.000Z';

function conversation(id, messages, extra = {}) {
  return { id, repo: '/repo', kind: 'conversation', title: `会話 ${id}`, supersededBy: '', result: attention.conversationResult(messages), ...extra };
}

test('会話の結果: 末尾の応答だけが結果。依頼で終わっていれば結果なし', () => {
  assert.equal(attention.conversationResult([]), null);
  assert.equal(attention.conversationResult([{ role: 'user', at: T1, text: 'a' }]), null);
  assert.deepEqual(attention.conversationResult([{ role: 'user', at: T0 }, { role: 'assistant', at: T1, text: 'b' }]), { at: T1, outcome: 'done' });
  assert.deepEqual(attention.conversationResult([{ role: 'assistant', at: T1, error: 'x' }]), { at: T1, outcome: 'failed' });
  assert.deepEqual(attention.conversationResult([{ role: 'assistant', at: T1, stopped: true }]), { at: T1, outcome: 'stopped' });
});

test('completed + unseen → unread、completed + seen → none', () => {
  const [source] = attention.conversationSources([conversation('a', [{ role: 'user', at: T0 }, { role: 'assistant', at: T1 }])]);
  assert.equal(attention.classify(source, {}), 'unread');
  assert.equal(attention.classify(source, { 'conversation:a': { resultAt: T1 } }), 'none');
  // 見たあとに新しい結果が来れば、また未読
  assert.equal(attention.classify(source, { 'conversation:a': { resultAt: T0 } }), 'unread');
  // 精度の違う ISO でも時刻として比べる
  assert.equal(attention.classify(source, { 'conversation:a': { resultAt: '2026-09-11T00:00:00Z' } }), 'none');
});

test('running → none（応答中の会話は未読にも要対応にもならない）', () => {
  const [source] = attention.conversationSources(
    [conversation('a', [{ role: 'user', at: T0 }, { role: 'assistant', at: T1 }])],
    { runningIds: ['a'], phaseOf: () => ({ phase: 'attention', detail: 'y/n?' }) },
  );
  assert.equal(source.running, true);
  assert.equal(attention.classify(source, {}), 'none');
});

test('会話の確認待ち（tmux の phase attention）→ action。phase が戻れば消える', () => {
  const sessions = [conversation('a', [{ role: 'user', at: T0 }, { role: 'assistant', at: T1 }])];
  const asking = attention.conversationSources(sessions, { phaseOf: () => ({ phase: 'attention', detail: 'Proceed? [y/N]' }) })[0];
  assert.equal(attention.classify(asking, { 'conversation:a': { resultAt: T2 } }), 'action');
  assert.equal(asking.interaction.mode, 'terminal');
  const idle = attention.conversationSources(sessions, { phaseOf: () => ({ phase: 'ready', detail: '' }) })[0];
  assert.equal(attention.classify(idle, { 'conversation:a': { resultAt: T1 } }), 'none');
});

test('会話: 失敗した応答は未読（要対応ではない）、利用者が止めたものは none', () => {
  const failed = attention.conversationSources([conversation('a', [{ role: 'assistant', at: T1, error: 'boom' }])])[0];
  assert.equal(attention.classify(failed, {}), 'unread');
  assert.equal(failed.outcome, 'failed');
  const stopped = attention.conversationSources([conversation('b', [{ role: 'assistant', at: T1, stopped: true }])])[0];
  assert.equal(attention.classify(stopped, {}), 'none');
});

test('会話: タスク・ワークフローの編集用の会話と置き換えられた会話は材料にしない', () => {
  const sources = attention.conversationSources([
    conversation('a', [{ role: 'assistant', at: T1 }], { kind: 'task' }),
    conversation('b', [{ role: 'assistant', at: T1 }], { supersededBy: 'c' }),
    conversation('c', [{ role: 'assistant', at: T1 }]),
  ]);
  assert.deepEqual(sources.map((s) => s.key), ['conversation:c']);
  assert.deepEqual(sources[0].target, { kind: 'conversation', repo: '/repo', id: 'c' });
});

test('ワークフロー: approval / choice / input が open → action、答えが届けば消える', () => {
  for (const mode of ['approval', 'choice', 'input']) {
    const run = { runId: 'r1', title: '月次レポート', workflowId: 'monthly', state: 'waiting', terminal: false, createdAt: T0, updatedAt: T1,
      interactions: [{ interactionId: 'ix-0123456789abcdef', mode, prompt: '確認してください', state: 'open' }] };
    const [source] = attention.workflowSources('/repo', [run]);
    assert.equal(attention.classify(source, {}), 'action', mode);
    assert.equal(source.interaction.mode, mode);
    assert.deepEqual(source.target, { kind: 'workflow', repo: '/repo', id: 'monthly', runId: 'r1' });
    for (const state of ['answered', 'resolved', 'expired']) {
      const answered = attention.workflowSources('/repo', [{ ...run, state: 'executing', interactions: [{ ...run.interactions[0], state }] }])[0];
      assert.equal(answered.interaction, null, state);
      assert.equal(attention.classify(answered, {}), 'none', `${mode}/${state}: 実行中は none`);
    }
  }
});

test('ワークフロー: 終了した実行は未読（失敗も未読であって要対応ではない）、停止は none、実行中は none', () => {
  const rows = [
    { runId: 'done', title: 'a', workflowId: 'w', state: 'done', terminal: true, createdAt: T0, updatedAt: T1 },
    { runId: 'failed', title: 'b', workflowId: 'w', state: 'failed', terminal: true, createdAt: T0, updatedAt: T1 },
    { runId: 'cancelled', title: 'c', workflowId: 'w', state: 'cancelled', terminal: true, createdAt: T0, updatedAt: T1 },
    { runId: 'executing', title: 'd', workflowId: 'w', state: 'executing', terminal: false, createdAt: T0, updatedAt: T1 },
    { runId: 'stalled', title: 'e', workflowId: 'w', state: 'stalled', terminal: false, createdAt: T0, updatedAt: T1 },
    { runId: 'launch-failed', title: 'f', workflowId: null, state: 'launch-failed', terminal: true, createdAt: T0, updatedAt: null },
  ];
  const queues = Object.fromEntries(attention.workflowSources('/repo', rows).map((s) => [s.target.runId, attention.classify(s, {})]));
  assert.deepEqual(queues, { done: 'unread', failed: 'unread', cancelled: 'none', executing: 'none', stalled: 'none', 'launch-failed': 'unread' });
  const seen = { 'workflow:/repo:done': { resultAt: T1 } };
  assert.equal(attention.classify(attention.workflowSources('/repo', rows)[0], seen), 'none');
});

test('タスク: 保存名ごとに最新の記録だけ。完了・失敗は未読、要確認で終わったものも未読（答える口が無い）', () => {
  const records = [
    { runId: 'x2', machine: 'report', ok: false, escalate: true, finishedAt: T2 },
    { runId: 'x1', machine: 'report', ok: true, finishedAt: T1 },
    { runId: 'y1', taskId: 'machine:other', ok: false, finishedAt: T1 },
    { runId: 'broken' },
  ];
  const sources = attention.taskSources('/repo', records, { report: '月次集計' });
  assert.deepEqual(sources.map((s) => [s.key, s.title, s.outcome, s.resultAt]), [
    ['task:/repo:report', '月次集計', 'escalated', T2],
    ['task:/repo:other', 'other', 'failed', T1],
  ]);
  assert.equal(attention.classify(sources[0], {}), 'unread');
  assert.equal(attention.classify(sources[0], { 'task:/repo:report': { resultAt: T2 } }), 'none');
  assert.deepEqual(sources[0].target, { kind: 'task', repo: '/repo', id: 'report' });
});

test('古いデータでも壊れない: 欠けた項目・壊れた時刻・基準時刻より前の結果', () => {
  assert.equal(attention.classify(null), 'none');
  assert.equal(attention.classify({ key: 'x' }), 'none');
  assert.equal(attention.classify({ key: 'x', resultAt: 'not a date' }), 'none');
  assert.deepEqual(attention.conversationSources(null), []);
  assert.deepEqual(attention.taskSources('/repo', 'nope'), []);
  assert.deepEqual(attention.workflowSources('/repo', [null, {}, { runId: '' }]), []);
  // 受信箱を使い始めた時刻（since）より前の結果は、見た記録が無くても既読扱い（一斉に未読にならない）
  const old = attention.conversationSources([conversation('a', [{ role: 'assistant', at: T0 }])])[0];
  const fresh = attention.conversationSources([conversation('b', [{ role: 'assistant', at: T2 }])])[0];
  assert.equal(attention.classify(old, {}, T1), 'none');
  assert.equal(attention.classify(fresh, {}, T1), 'unread');
  // 要対応は基準時刻に関係なく出す
  const asking = attention.conversationSources([conversation('c', [{ role: 'assistant', at: T0 }])], { phaseOf: () => ({ phase: 'attention', detail: '' }) })[0];
  assert.equal(attention.classify(asking, {}, T1), 'action');
});

test('project: 要対応を先に、未読は新しい順。件数は列ごと', () => {
  const sources = [
    ...attention.conversationSources([
      conversation('old', [{ role: 'assistant', at: T0 }]),
      conversation('new', [{ role: 'assistant', at: T2 }]),
      conversation('seen', [{ role: 'assistant', at: T1 }]),
    ], { phaseOf: (id) => (id === 'old' ? { phase: 'attention', detail: 'y?' } : null) }),
    ...attention.workflowSources('/repo', [{ runId: 'r', title: 'w', workflowId: 'w', state: 'done', terminal: true, createdAt: T0, updatedAt: T1 }]),
  ];
  const view = attention.project(sources, { seen: { 'conversation:seen': { resultAt: T1 } } });
  assert.deepEqual(view.items.map((i) => [i.key, i.queue]), [
    ['conversation:old', 'action'], ['conversation:new', 'unread'], ['workflow:/repo:r', 'unread'],
  ]);
  assert.equal(view.action, 1);
  assert.equal(view.unread, 2);
  assert.deepEqual(attention.project([]), { action: 0, unread: 0, items: [] });
});

test('markSeen: 新しい時刻だけ進める。上限を超えたら古い結果から落とす', () => {
  let seen = attention.markSeen({}, 'a', T1);
  assert.deepEqual(seen, { a: { resultAt: T1 } });
  seen = attention.markSeen(seen, 'a', T0);
  assert.equal(seen.a.resultAt, T1, '古い時刻へ戻さない');
  seen = attention.markSeen(seen, '', T2);
  seen = attention.markSeen(seen, 'b', 'garbage');
  assert.deepEqual(Object.keys(seen), ['a']);
  let many = {};
  for (let i = 0; i < attention.MAX_SEEN + 5; i += 1) many = attention.markSeen(many, `k${i}`, new Date(Date.parse(T0) + i * 1000).toISOString());
  assert.equal(Object.keys(many).length, attention.MAX_SEEN);
  assert.ok(!many.k0 && many[`k${attention.MAX_SEEN + 4}`]);
});

test('store: 「見た」は config.json に足すだけ（新しい保存先を作らない）。旧設定でも既定で読める', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'attention-store-'));
  fs.writeFileSync(path.join(ud, 'config.json'), JSON.stringify({ repos: ['/repo'], lastRepo: '/repo' }));
  assert.deepEqual(store.loadConfig(ud).attentionSeen, { since: '', items: {} });
  const baseline = store.attentionBaseline(ud, new Date(T1));
  assert.equal(baseline.since, T1);
  assert.equal(store.attentionBaseline(ud, new Date(T2)).since, T1, '基準は最初の 1 回だけ書く');
  store.markAttentionSeen(ud, 'conversation:a', T2);
  store.markAttentionSeen(ud, 'task:/repo:report', T1);
  const cfg = store.loadConfig(ud);
  assert.deepEqual(cfg.attentionSeen, { since: T1, items: { 'conversation:a': { resultAt: T2 }, 'task:/repo:report': { resultAt: T1 } } });
  assert.deepEqual(cfg.repos, ['/repo'], '他の設定はそのまま');
  // 壊れた保存値は落とす
  fs.writeFileSync(path.join(ud, 'config.json'), JSON.stringify({ attentionSeen: { since: 'x', items: { a: { resultAt: 'y' }, b: null, c: { resultAt: T0 } } } }));
  assert.deepEqual(store.loadConfig(ud).attentionSeen, { since: '', items: { c: { resultAt: T0 } } });
  assert.ok(!fs.existsSync(path.join(ud, 'inbox')) && !fs.existsSync(path.join(ud, 'attention')));
});

test('store: 会話の要約（session:list）が末尾の応答を result として持つ', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'attention-summary-'));
  const s = store.createSession(ud, { repo: '/repo', cli: 'codex', transport: 'headless' });
  assert.equal(store.listSessions(ud, '/repo')[0].result, null);
  store.appendMessage(ud, s.id, { role: 'user', text: '依頼' });
  store.appendMessage(ud, s.id, { role: 'assistant', text: '応答' });
  const summary = store.listSessions(ud, '/repo')[0];
  assert.equal(summary.result.outcome, 'done');
  assert.equal(summary.result.at, store.readSession(ud, s.id).messages[1].at);
});

test('正典の往復: 実行履歴に書かれた結果が未読になり、見たら消え、次の結果でまた出る', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'attention-roundtrip-'));
  const repo = '/repo';
  store.saveConfig(ud, { repos: [repo], lastRepo: repo });
  const seen0 = store.attentionBaseline(ud, new Date(T0));
  runHistory.append(ud, repo, { runId: 'r1', machine: 'report', ok: true, finishedAt: T1 });
  const project = () => {
    const seen = store.loadConfig(ud).attentionSeen;
    return attention.project(attention.taskSources(repo, runHistory.read(ud, repo)), { seen: seen.items, since: seen0.since });
  };
  assert.equal(project().unread, 1);
  store.markAttentionSeen(ud, 'task:/repo:report', T1);
  assert.equal(project().unread, 0);
  runHistory.append(ud, repo, { runId: 'r2', machine: 'report', ok: false, finishedAt: T2 });
  assert.deepEqual(project().items.map((i) => [i.queue, i.outcome]), [['unread', 'failed']]);
});

test('課題: agent-audit の洞察を受信箱の材料にする。反証・渡したものは出さず、改訂されればまた未読', () => {
  const insights = [
    { id: 'ins-1', statement: '見本の記録をやり直している', kind: 'quality-review',
      improvement: { version: 1, target: { kind: 'skill', name: 'statemachine-use' }, criteria: [{ requirement: '成果物を作る', evidence: '未作成と記録' }] }, occurrences: 6, confidence: 'medium',
      observation_ids: ['o1', 'o2'], scope: { target: { kind: 'skill', name: 'statemachine-use' } }, ts: T1, updated_at: T1 },
    { id: 'ins-2', statement: '反証', ts: T1, review: { verdict: 'refuted' } },
    { id: 'ins-3', statement: '渡した', ts: T1, exported: true },
    { id: 'ins-4', statement: '全体', ts: T1 },
    { id: 'long-old', kind: 'usage-optimization', statement: 'codex:model の session が 30 turn または 1800 秒を超える（2 件）', ts: T1 },
    { id: 'trend', kind: 'usage-optimization', statement: '利用傾向', actionable: false, ts: T1 },
  ];
  const sources = attention.insightSources(insights);
  assert.deepEqual(sources.map((s) => s.key), ['issue:ins-1']);
  assert.equal(sources[0].kind, 'issue');
  assert.equal(sources[0].title, '見本の記録をやり直している');
  assert.deepEqual(sources[0].target, { kind: 'issue', id: 'ins-1' });
  assert.deepEqual(sources[0].issue.target, { kind: 'skill', name: 'statemachine-use' });
  assert.deepEqual(sources[0].issue.evidence, ['o1', 'o2']);
  assert.equal(attention.insightSources([{ id: 'retry', kind: 'usage-optimization',
    statement: '作業が 2 回以上再試行される', ts: T1 }]).length, 0, '再試行だけでは課題にしない');
  assert.equal(attention.classify(sources[0], {}), 'unread');
  assert.equal(attention.classify(sources[0], { 'issue:ins-1': { resultAt: T1 } }), 'none');
  const revised = attention.insightSources([{ ...insights[0], updated_at: T2 }])[0];
  assert.equal(attention.classify(revised, { 'issue:ins-1': { resultAt: T1 } }), 'unread');
  // 会話・実行の材料と同じ列に混ざる（未読として数える）
  const projected = attention.project([...sources], { seen: {} });
  assert.equal(projected.unread, 1);
});


test('手順の改善候補: 対象・未達条件・根拠が揃ったものだけを受信箱へ出す', () => {
  const base = { id: 'candidate', kind: 'quality-review', ts: T1, observation_ids: ['obs'],
    scope: { target: { kind: 'task', name: 'daily' } },
    improvement: { version: 1, target: { kind: 'task', name: 'daily' },
      criteria: [{ requirement: 'レポートを保存する', evidence: '保存できなかった' }] } };
  for (const kind of ['skill', 'task', 'workflow']) {
    const target = { kind, name: 'daily' };
    assert.equal(attention.insightSources([{ ...base, scope: { target }, improvement: { ...base.improvement, target } }]).length, 1);
  }
  for (const change of [
    { improvement: undefined }, { kind: 'skill-improvement' }, { observation_ids: [] },
    { scope: {} }, { scope: { target: { kind: 'tool', name: 'daily' } } },
    { scope: { target: { kind: 'task', name: 'other' } } },
    { improvement: { ...base.improvement, criteria: [] } },
    { improvement: { ...base.improvement, criteria: [{ requirement: '保存', evidence: '' }] } },
  ]) assert.equal(attention.insightSources([{ ...base, ...change }]).length, 0);
});
