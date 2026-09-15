'use strict';

// 保存データの整理（src/main/cleanup.js）。
// 数える対象と消す対象が同じであること、使用中のものを巻き込まないことを押さえる。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const cleanup = require('../src/main/cleanup');

function tmp(prefix) { return fs.mkdtempSync(path.join(os.tmpdir(), prefix)); }
function write(file, body) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, body);
  return file;
}
function itemOf(result, key) { return result.items.find((i) => i.key === key); }

function fixture() {
  const userData = tmp('cleanup-ud-');
  const home = tmp('cleanup-home-');
  const tmpdir = tmp('cleanup-tmp-');
  const repo = '/work/repo';
  const sessions = [{
    id: 'keep-me',
    messages: [{ role: 'user', attachments: [{ id: '11111111-1111-1111-1111-111111111111', name: 'a.txt' }] }],
    terminalSnapshots: [{ screenText: 'あ'.repeat(100) }],
  }];
  // 参照されている添付と、どの会話も参照していない添付
  write(path.join(userData, 'attachments', '11111111-1111-1111-1111-111111111111', 'a.txt'), 'keep');
  write(path.join(userData, 'attachments', '22222222-2222-2222-2222-222222222222', 'b.txt'), 'x'.repeat(500));
  // 会話が残っている再開情報と、消えた会話の再開情報
  write(path.join(home, '.local/state/agent-app/cli-sessions/keep-me/codex/session.json'), '{}');
  write(path.join(home, '.local/state/agent-app/cli-sessions/gone/codex/session.json'), 'x'.repeat(300));
  // 登録中のフォルダの実行履歴と、登録を外したフォルダの実行履歴
  const hash = (value) => crypto.createHash('sha256').update(value).digest('hex');
  write(path.join(userData, 'run-history', `${hash(repo)}.json`), '[]');
  write(path.join(userData, 'run-history', `${hash('/work/gone')}.json`), 'x'.repeat(200));
  write(path.join(userData, 'updates', 'agent-app.exe'), 'x'.repeat(1000));
  write(path.join(userData, 'recording-browser-profile', 'Default', 'Cookies'), 'x'.repeat(80));
  // 一時ファイル（別の起動が残したもの／いま動いている自分のもの）
  write(path.join(tmpdir, 'agent-app-999-abc.txt'), 'x'.repeat(50));
  write(path.join(tmpdir, 'agent-app-4242-now.txt'), 'x'.repeat(70));
  write(path.join(tmpdir, 'statemachine-maker', 'record-1.jsonl'), 'x'.repeat(30));
  write(path.join(tmpdir, 'other-tool.txt'), 'x'.repeat(900));
  // 名前が似ているだけの他人の置き場（試験の作業場・別のツールのフォルダ）
  write(path.join(tmpdir, 'agent-app-automation-userdata-xyz', 'config.json'), 'x'.repeat(4000));
  return { userData, sessions, repos: [repo], home, tmpdir, pid: 4242, now: Date.parse('2026-09-15T00:00:00Z') };
}

test('数える: 種類ごとに、参照されていないものだけを数える', () => {
  const input = fixture();
  const result = cleanup.scan(input);
  assert.strictEqual(itemOf(result, 'attachments').count, 1, '参照中の添付は数えない');
  assert.strictEqual(itemOf(result, 'attachments').bytes, 500);
  assert.strictEqual(itemOf(result, 'cliSessions').count, 1, '会話が残っている再開情報は数えない');
  assert.strictEqual(itemOf(result, 'runHistory').count, 1, '登録中のフォルダの履歴は数えない');
  assert.strictEqual(itemOf(result, 'updates').bytes, 1000);
  assert.strictEqual(itemOf(result, 'snapshots').bytes, Buffer.byteLength('あ'.repeat(100), 'utf8'));
  assert.strictEqual(itemOf(result, 'browserProfile').bytes, 80);
  assert.strictEqual(itemOf(result, 'browserProfile').defaultOn, false, 'ログインし直しになるものは既定で外す');
  // 一時ファイル: 別の起動の分と記録の作業場だけ（いま動いている自分の出力と、他のツールの分は残す）
  assert.strictEqual(itemOf(result, 'temp').bytes, 80);
  // 行の並びは毎回同じ（0 の種類も消えない）
  assert.deepStrictEqual(cleanup.scan({ userData: tmp('cleanup-empty-') }).items.map((i) => i.key),
    result.items.map((i) => i.key));
});

test('消す: 選んだ種類だけ消し、空けた大きさを返す', () => {
  const input = fixture();
  const before = cleanup.scan(input);
  const cleared = [];
  const result = cleanup.remove(input, ['updates', 'temp', 'snapshots'], { clearSnapshots: (id) => cleared.push(id) });
  assert.strictEqual(result.freed, itemOf(before, 'updates').bytes + itemOf(before, 'temp').bytes + itemOf(before, 'snapshots').bytes);
  assert.deepStrictEqual(cleared, ['keep-me']);
  assert.strictEqual(fs.existsSync(path.join(input.userData, 'updates', 'agent-app.exe')), false);
  assert.strictEqual(fs.existsSync(path.join(input.tmpdir, 'agent-app-999-abc.txt')), false);
  assert.strictEqual(fs.existsSync(path.join(input.tmpdir, 'agent-app-4242-now.txt')), true, '自分が使っている一時ファイルは残す');
  assert.strictEqual(fs.existsSync(path.join(input.tmpdir, 'other-tool.txt')), true, '他のツールのファイルは触らない');
  assert.strictEqual(fs.existsSync(path.join(input.tmpdir, 'agent-app-automation-userdata-xyz', 'config.json')), true,
    '名前が似ているだけのフォルダは触らない');
  // 選ばなかった種類は残る
  const after = cleanup.scan(input);
  assert.strictEqual(itemOf(after, 'attachments').count, 1);
  assert.strictEqual(itemOf(after, 'updates').bytes, 0);
});

test('消す: 動いている会話の端末画面は残す', () => {
  const input = fixture();
  input.sessions = [{ id: 'live-one', live: { cli: 'codex' }, terminalSnapshots: [{ screenText: 'x'.repeat(10) }] }];
  assert.strictEqual(cleanup.scan(input).items.find((i) => i.key === 'snapshots').count, 0);
  const cleared = [];
  cleanup.remove(input, ['snapshots'], { clearSnapshots: (id) => cleared.push(id) });
  assert.deepStrictEqual(cleared, []);
});

test('消す: 古い共有の記録だけを消す（当日分は残す）', () => {
  const input = fixture();
  const day = (offset) => new Date(input.now - offset * 86400000).toISOString().slice(0, 10).replace(/-/g, '');
  write(path.join(input.userData, 'share', 'ledger', `${day(0)}.jsonl`), 'x'.repeat(10));
  write(path.join(input.userData, 'share', 'ledger', `${day(60)}.jsonl`), 'x'.repeat(20));
  write(path.join(input.userData, 'share', 'scratch', 'req-1', 'attachments', 'a.txt'), 'x'.repeat(40));
  assert.strictEqual(cleanup.scan(input).items.find((i) => i.key === 'share').bytes, 60);
  cleanup.remove(input, ['share']);
  assert.strictEqual(fs.existsSync(path.join(input.userData, 'share', 'ledger', `${day(0)}.jsonl`)), true);
  assert.strictEqual(fs.existsSync(path.join(input.userData, 'share', 'ledger', `${day(60)}.jsonl`)), false);
  assert.strictEqual(fs.existsSync(path.join(input.userData, 'share', 'scratch', 'req-1')), false);
});

test('数える: リンクの先は数えない（他人の大きさを自分の分に足さない）', () => {
  const input = fixture();
  const outside = write(path.join(input.home, 'real-settings.json'), 'x'.repeat(5000));
  fs.symlinkSync(outside, path.join(input.home, '.local/state/agent-app/cli-sessions/gone', 'settings.json'));
  assert.strictEqual(cleanup.scan(input).items.find((i) => i.key === 'cliSessions').bytes, 300);
  cleanup.remove(input, ['cliSessions']);
  assert.strictEqual(fs.existsSync(outside), true, 'リンクの先は消さない');
});
