'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const teaching = require('../src/main/teaching-model');

test('目的から再開可能な教示セッションを作る', () => {
  const session = teaching.createSession({
    machine: 'monthly-report',
    title: '月次レポート',
    purpose: '売上画面から月次レポートを作る',
  });

  assert.strictEqual(session.version, 1);
  assert.strictEqual(session.status, 'draft');
  assert.strictEqual(session.machine, 'monthly-report');
  assert.deepStrictEqual(session.understanding, {
    purpose: '売上画面から月次レポートを作る',
    variables: [],
    expectedResults: [],
    importantActions: [],
    unknowns: [],
  });
  assert.strictEqual(session.messages[0].role, 'user');
  assert.strictEqual(session.messages[0].text, '売上画面から月次レポートを作る');
});

test('読み戻した教示内容を正規化し秘密値を残さない', () => {
  const session = teaching.normalizeSession({
    version: 99,
    machine: ' report ',
    status: 'unknown',
    messages: [{ role: 'user', text: 'token=abc123 を使う' }, { role: 'system', text: '除外' }],
    evidence: [{ id: 'e1', type: 'browser', summary: 'ログイン', actions: [{ target: 'password', value: 'secret-value' }] }],
    understanding: { purpose: '確認', variables: [{ key: 'api_key', label: 'API key', example: 'live-key' }] },
  });

  assert.strictEqual(session.version, 1);
  assert.strictEqual(session.machine, 'report');
  assert.strictEqual(session.status, 'draft');
  assert.deepStrictEqual(session.messages, [{ role: 'user', text: 'token=*** を使う' }]);
  assert.strictEqual(session.evidence[0].actions[0].value, '***');
  assert.strictEqual(session.understanding.variables[0].example, '***');
});

test('候補を試運転して承認し、後の失敗から最後の成功版へ戻す', () => {
  let session = teaching.createSession({ machine: 'report', purpose: 'レポートを作る' });
  session = teaching.addGeneration(session, { id: 'g1', jobSpec: { purpose: 'レポートを作る' }, makerSpec: { machine: 'report' } });
  assert.strictEqual(session.status, 'needs-trial');

  session = teaching.recordTrial(session, { id: 't1', generationId: 'g1', outcome: 'passed', summary: '期待どおり' });
  assert.strictEqual(session.status, 'awaiting-confirmation');
  session = teaching.confirmReady(session, 'g1');
  assert.strictEqual(session.status, 'ready');
  assert.strictEqual(teaching.normalizeSession({ understanding: null }).understanding.purpose, '');
  assert.strictEqual(teaching.normalizeSession(null).status, 'draft');
  assert.strictEqual(teaching.redact(undefined), undefined);
  assert.strictEqual(session.lastSuccessfulGenerationId, 'g1');

  session = teaching.addGeneration(session, { id: 'g2', jobSpec: { purpose: '変更版' }, makerSpec: { machine: 'report' } });
  session = teaching.recordTrial(session, { id: 't2', generationId: 'g2', outcome: 'failed', summary: '結果が違う' });
  assert.strictEqual(session.status, 'needs-trial');

  session = teaching.restoreLastSuccessful(session);
  assert.strictEqual(session.activeGenerationId, 'g1');
  assert.strictEqual(session.status, 'ready');
});

test('録画は工程ではなく秘密化した証拠として教示セッションへ追加する', () => {
  const session = teaching.addEvidence(teaching.createSession({ machine: 'login' }), {
    id: 'e1',
    type: 'browser',
    summary: 'ログインして一覧を開く',
    actions: [{ op: 'fill', target: 'password', value: 'real-secret' }, { op: 'click', target: '一覧' }],
  });

  assert.strictEqual(session.evidence.length, 1);
  assert.strictEqual(session.evidence[0].actions[0].value, '***');
  assert.strictEqual(session.messages.at(-1).kind, 'evidence');
  assert.match(session.messages.at(-1).text, /見本を受け取りました/);
});

test('AIからの質問や見本依頼を保存し、アプリ再起動後も再開できる', () => {
  const session = teaching.normalizeSession({
    machine: 'report',
    pendingRequest: {
      kind: 'questions',
      questions: [{ id: 'q1', text: '対象月は毎回変わりますか？', reason: '汎化に必要です' }],
    },
  });
  assert.strictEqual(session.pendingRequest.kind, 'questions');
  assert.strictEqual(session.pendingRequest.questions[0].text, '対象月は毎回変わりますか？');
});

test('不正な世代・試運転・利用可能化を拒否する', () => {
  const empty = teaching.createSession();
  assert.throws(() => teaching.recordTrial(empty, {}), /候補/);
  assert.throws(() => teaching.confirmReady(empty), /成功した試運転/);
  assert.throws(() => teaching.restoreLastSuccessful(empty), /成功版/);

  const generated = teaching.addGeneration(empty, {});
  assert.strictEqual(generated.activeGenerationId, 'generation-1');
  assert.throws(() => teaching.addGeneration(generated, { id: 'generation-1' }), /既に/);
  const failed = teaching.recordTrial(generated, { outcome: 'unexpected' });
  assert.strictEqual(failed.trials[0].outcome, 'failed');
  assert.strictEqual(teaching.addEvidence(empty).evidence[0].summary, '操作の見本');
});

test('任意項目の空値と全種類の理解内容を安全な既定へ揃える', () => {
  const session = teaching.normalizeSession({
    status: 'ready',
    messages: [null, { role: 'assistant', text: ' 回答 ', kind: 'questions' }, { role: 'user', text: '' }],
    evidence: [null, { type: 'file', actions: null }],
    understanding: {
      variables: [null, { key: 'month', label: '対象月', required: false }],
      expectedResults: ['', 'ファイルができる'],
      importantActions: [null, { id: 'send', action: 'submit', target: '申請', effect: '送信する' }],
      unknowns: ['', '対象範囲'],
    },
    generations: [null, {}],
    pendingApproval: {},
    trials: [null, {}],
  });
  assert.strictEqual(session.status, 'ready');
  assert.deepStrictEqual(session.understanding.importantActions[0], { id: 'send', action: 'submit', target: '申請', effect: '送信する' });
  assert.strictEqual(session.understanding.variables[0].required, false);
  assert.deepStrictEqual(teaching.redact(['token=value', null, 3]), ['token=***', null, 3]);

  const generated = teaching.addGeneration(teaching.createSession(), { id: 'g', jobSpec: null, makerSpec: null });
  const waiting = teaching.recordTrial(generated, { outcome: 'approval-required', expected: null, observed: null });
  assert.strictEqual(waiting.status, 'needs-trial');
  assert.throws(() => teaching.restoreLastSuccessful({ ...generated, lastSuccessfulGenerationId: 'missing' }), /成功版/);
});
