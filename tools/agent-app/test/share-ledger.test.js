'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { Ledger, RATE_LIMIT_MS } = require('../src/main/share/ledger');

function fresh() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'share-ledger-'));
  let now = Date.parse('2026-09-12T10:00:00Z');
  const ledger = new Ledger(dir, { now: () => now });
  return { dir, ledger, tick: (ms) => { now += ms; } };
}

test('台帳: 今日の件数・秒・依頼者別・CLI 別を数え、取り下げと lost は数えない', () => {
  const { dir, ledger } = fresh();
  ledger.record({ id: '1', posted_by: 'a', cli: 'claude', seconds: 12.6, status: 'done' });
  ledger.record({ id: '2', posted_by: 'a', cli: 'codex', seconds: 3, status: 'failed', error_class: 'cli' });
  ledger.record({ id: '3', posted_by: 'b', cli: 'claude', seconds: 100, status: 'cancelled' });
  ledger.record({ id: '4', posted_by: 'b', cli: 'claude', seconds: 100, status: 'lost' });
  assert.deepEqual(ledger.today(), { count: 2, seconds: 16, byRequester: { a: 2 }, byCli: { claude: 1, codex: 1 } });
  const again = new Ledger(dir, { now: () => Date.parse('2026-09-12T23:00:00Z') });
  assert.equal(again.today().count, 2, '追記専用のファイルから読み直せる');
  const tomorrow = new Ledger(dir, { now: () => Date.parse('2026-09-13T00:00:01Z') });
  assert.equal(tomorrow.today().count, 0, 'UTC の日付で切り替わる');
});

test('枠切れ: exhausted はその日の残り、rate_limit は 10 分だけ受けない', () => {
  const { ledger, tick } = fresh();
  ledger.markQuota('claude', 'exhausted');
  ledger.markQuota('codex', 'rate_limit');
  assert.equal(ledger.cliOk('claude'), false);
  assert.equal(ledger.cliOk('codex'), false);
  assert.equal(ledger.cliOk('kiro'), true);
  tick(RATE_LIMIT_MS + 1);
  assert.equal(ledger.cliOk('codex'), true);
  assert.equal(ledger.cliOk('claude'), false);
  tick(14 * 60 * 60 * 1000);
  assert.equal(ledger.cliOk('claude'), true, '翌日になれば戻る');
});

test('受けられるか: 参加 OFF・1 日の上限・同時数・CLI ごとの枠切れを理由の語彙で返す', () => {
  const { ledger } = fresh();
  assert.deepEqual(ledger.canAccept({ participate: true, clis: ['claude'], maxConcurrent: 1, dailyCap: 20, inflight: 0 }).reason_codes, ['ok']);
  assert.deepEqual(ledger.canAccept({ participate: false, clis: ['claude'], maxConcurrent: 1, dailyCap: 20, inflight: 0 }).reason_codes, ['unavailable']);
  assert.deepEqual(ledger.canAccept({ participate: true, clis: ['claude'], maxConcurrent: 1, dailyCap: 20, inflight: 1 }).reason_codes, ['soft']);
  ledger.record({ id: '1', posted_by: 'a', cli: 'claude', seconds: 1, status: 'done' });
  const capped = ledger.canAccept({ participate: true, clis: ['claude'], maxConcurrent: 1, dailyCap: 1, inflight: 0 });
  assert.equal(capped.can_accept, false);
  assert.deepEqual(capped.reason_codes, ['exceeded']);
  ledger.markQuota('claude', 'exhausted');
  const quota = ledger.canAccept({ participate: true, clis: ['claude', 'codex'], maxConcurrent: 1, dailyCap: 0, inflight: 0 });
  assert.equal(quota.can_accept, true, 'codex が残っている');
  assert.deepEqual(quota.clis.claude.reason_codes, ['exceeded']);
  assert.equal(quota.clis.claude.today, 1);
});
