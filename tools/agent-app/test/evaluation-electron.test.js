'use strict';

// Electron 実機で評価（§19）を通す: 受信箱の課題カード（根拠のリンク・会話で扱うのダイアログ）、
// 会話を検索の足元から「まとめて評価」（偽の agent-herd judge）、終わったら受信箱に未読で届くこと。
// 判定 AI と agent-audit は PATH に置いた偽のシェルスクリプト。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const APP = path.join(__dirname, '..');

function electronBinary() {
  try {
    const binary = require('electron');
    return typeof binary === 'string' && fs.existsSync(binary) ? binary : '';
  } catch { return ''; }
}

function playwright() {
  for (const id of ['playwright', 'playwright-core']) {
    try { return require(id); } catch { /* 次を試す */ }
  }
  const nodePrefix = path.dirname(path.dirname(process.execPath));
  try { return require(path.join(nodePrefix, 'lib', 'node_modules', '@playwright', 'cli', 'node_modules', 'playwright-core')); } catch { /* 次を試す */ }
  try { return require('/opt/node22/lib/node_modules/playwright'); } catch { return null; }
}

test('受信箱: 長い参照元でも崩れず、IDを表示せずに元の会話を開け、最後の課題までスクロールできる', { timeout: 30000 }, async (t) => {
  const binary = electronBinary();
  const pw = playwright();
  if (!binary || !pw?._electron) return t.skip('Electron / Playwright がありません');
  if (process.platform === 'linux' && !process.env.DISPLAY) return t.skip('表示先がありません');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'inbox-scroll-'));
  const repo = path.join(root, 'repo');
  const userData = path.join(root, 'data');
  fs.mkdirSync(repo);
  let electron;
  t.after(async () => {
    try { if (electron) await electron.close(); }
    finally { fs.rmSync(root, { recursive: true, force: true }); }
  });
  require('../src/main/store').saveConfig(userData, {
    repos: [repo], lastRepo: repo, transport: 'headless', share: { enabled: false },
    evaluation: { mode: 'off' }, audit: { enabled: false },
    attentionSeen: { since: '2026-01-01T00:00:00.000Z' },
  });
  const appStore = require('../src/main/store');
  const source = appStore.createSession(userData, { repo, cli: 'codex', transport: 'headless' });
  const longTitle = `元の会話-${'LongTitle'.repeat(60)}`;
  appStore.updateSession(userData, source.id, { title: longTitle });
  const storeDir = require('../src/main/audit').storeDir(userData);
  const insights = path.join(storeDir, 'insights');
  fs.mkdirSync(insights, { recursive: true });
  fs.mkdirSync(path.join(storeDir, 'observations'), { recursive: true });
  fs.mkdirSync(path.join(storeDir, 'records'), { recursive: true });
  const externalId = `external-record-${'a'.repeat(600)}`;
  fs.writeFileSync(path.join(storeDir, 'observations', 'test.jsonl'), JSON.stringify({
    id: 'obs-scroll', record_id: externalId, evidence: ['record-source', 'record-duplicate'],
  }) + '\n');
  fs.writeFileSync(path.join(storeDir, 'records', 'test.jsonl'), [
    { id: externalId, ref: externalId, tool: 'external-tool' },
    { id: 'record-source', tool: 'agent-app', workload: 'chat', ref: source.id },
    { id: 'record-duplicate', tool: 'agent-app', workload: 'chat', ref: source.id },
  ].map((record) => JSON.stringify(record)).join('\n') + '\n');
  for (let i = 0; i < 16; i += 1) {
    fs.writeFileSync(path.join(insights, `issue-${i}.json`), JSON.stringify({
      id: `issue-${i}`, ts: new Date().toISOString(), updated_at: new Date().toISOString(),
      kind: 'skill-improvement', occurrences: 3, confidence: 'low',
      statement: `スクロール確認 ${i + 1}: 手順が不足しています。`, observation_ids: ['obs-scroll'],
      scope: { target: { kind: 'skill', name: `skill-${i}` } }, exported: false,
    }));
  }
  electron = await pw._electron.launch({ executablePath: binary,
    args: [APP, '--no-sandbox', `--user-data-dir=${userData}`],
  });
  const win = await electron.firstWindow();
  await win.waitForFunction(() => document.querySelector('#area-inbox .unread')?.textContent === '16');
  await win.locator('#area-inbox').click();
  await win.waitForFunction(() => document.querySelectorAll('#inbox-issues .execution-card').length === 16);
  await win.waitForFunction(() => document.querySelectorAll('#inbox-issues .issue-evidence .message-action').length === 16);
  assert.ok(!(await win.locator('#inbox-issues').innerText()).includes(externalId), '開けない記録のIDを出さない');
  assert.equal(await win.locator('#inbox-issues .issue-evidence .message-action').first().getAttribute('title'), longTitle);
  assert.match(await win.locator('#inbox-issues .issue-evidence').first().innerText(), /このアプリから開けない記録 1 件/);
  const header = win.locator('#inbox-area > .area-head');
  const cards = win.locator('#inbox-issues .execution-card');
  for (const size of [{ width: 1200, height: 800 }, { width: 700, height: 600 }]) {
    await win.setViewportSize(size);
    assert.ok(await win.locator('#inbox-body').evaluate((body) => body.scrollWidth <= body.clientWidth), '長い参照元で横にはみ出さない');
    const before = await header.boundingBox();
    const area = await win.locator('#inbox-area').boundingBox();
    await win.mouse.move(area.x + area.width / 2, area.y + area.height - 50);
    await win.mouse.wheel(0, 100000);
    await win.waitForFunction(() => {
      const last = document.querySelector('#inbox-issues .execution-card:last-child').getBoundingClientRect();
      return last.bottom <= window.innerHeight && last.top >= 0;
    }, null, { timeout: 3000 });
    assert.equal((await header.boundingBox()).y, before.y, '見出しはスクロールしない');
    await cards.last().locator('.primary').click();
    await win.locator('#search-transfer-dialog[open]').waitFor();
    await win.locator('#search-transfer-close').click();
    await win.mouse.move(area.x + area.width / 2, area.y + area.height - 50);
    await win.mouse.wheel(0, -100000);
    await win.waitForFunction(() => document.querySelector('#inbox-issues .execution-card').getBoundingClientRect().top > 0);
  }
  await cards.first().locator('.issue-evidence .message-action').click();
  await win.waitForFunction((title) => document.getElementById('chat-title').textContent.includes(title), longTitle);
});

test('実機: 課題が受信箱に並び、根拠から会話へ行け、まとめて評価が判定 AI を呼んで台帳に残り、受信箱に届く', async (t) => {
  const binary = electronBinary();
  const pw = playwright();
  if (!binary) { t.skip('electron のバイナリが無い'); return; }
  if (!pw || !pw._electron) { t.skip('Playwright の Electron ドライバが無い'); return; }
  if (process.platform === 'linux' && !process.env.DISPLAY) { t.skip('表示先が無い'); return; }

  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-eval-repo-'));
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-eval-userdata-'));
  const appStore = require('../src/main/store');
  const audit = require('../src/main/audit');

  appStore.saveConfig(userData, { repos: [repo], lastRepo: repo, area: 'conversation', transport: 'headless', share: { enabled: false },
    evaluation: { mode: 'off' }, attentionSeen: { since: '2026-01-01T00:00:00.000Z' } });
  const s1 = appStore.createSession(userData, { repo, cli: 'codex', model: 'gpt-test', transport: 'headless' });
  appStore.appendMessage(userData, s1.id, { role: 'user', text: '設定画面の見直し' });
  appStore.appendMessage(userData, s1.id, { role: 'assistant', cli: 'codex', text: '並びを整理しました。' });
  const s2 = appStore.createSession(userData, { repo, cli: 'codex', model: 'gpt-test', transport: 'headless' });
  appStore.appendMessage(userData, s2.id, { role: 'user', text: 'ログ整形の依頼' });
  appStore.appendMessage(userData, s2.id, { role: 'assistant', cli: 'codex', text: '整形しました。' });

  // agent-audit のストア: 洞察 1 つと、その根拠（観測 → record → 会話 s1 の行）
  const storeDir = audit.storeDir(userData);
  fs.mkdirSync(path.join(storeDir, 'insights'), { recursive: true });
  fs.mkdirSync(path.join(storeDir, 'observations'), { recursive: true });
  fs.mkdirSync(path.join(storeDir, 'records'), { recursive: true });
  fs.writeFileSync(path.join(storeDir, 'insights', 'ins-1.json'), JSON.stringify({
    id: 'ins-1', ts: '2026-09-18T02:00:00Z', updated_at: '2026-09-18T02:00:00Z', kind: 'skill-improvement', occurrences: 3, confidence: 'low',
    statement: 'スキル statemachine-use で手順が足りず、依頼を満たせていない（3 件）', observation_ids: ['obs-1'],
    scope: { target: { kind: 'skill', name: 'statemachine-use' } }, exported: false,
  }));
  fs.writeFileSync(path.join(storeDir, 'observations', '20260918.jsonl'), `${JSON.stringify({ id: 'obs-1', record_id: 'rec-1', evidence: ['rec-1'], kind: 'skill-gap' })}\n`);
  fs.writeFileSync(path.join(storeDir, 'records', '20260918.jsonl'), `${JSON.stringify({ id: 'rec-1', ts: '2026-09-18T01:00:00Z', kind: 'ledger', tool: 'agent-app', workload: 'evaluation', purpose: 'chat', ref: s1.id })}\n`);

  // 偽の道具: agent-herd（config / judge）・agent-audit（scrub / tasks）・agent-flow
  const fakeBin = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-eval-fakebin-'));
  const judgeLog = path.join(fakeBin, 'judge.log');
  fs.writeFileSync(path.join(fakeBin, 'agent-herd'), [
    '#!/bin/sh',
    'case "$1" in',
    '  config) echo \'{"path":"x","judge":{"mode":"auto","model":""}}\';;',
    `  judge) cat >> "${judgeLog}"; echo '{"answers":{"quality":{"type":"score","score":1.2,"bucket":"1","confidence":0.9,"coverage":0.9,"method":"logprobs"},"issue":{"type":"choice","choice":"tool-failure","confidence":0.8,"coverage":0.9,"method":"logprobs"}},"abstained":[]}';;`,
    'esac', 'exit 0', '',
  ].join('\n'));
  fs.writeFileSync(path.join(fakeBin, 'agent-audit'), '#!/bin/sh\ncase "$2" in scrub) cat;; esac\ncase "$3" in scrub) cat;; tasks) echo "[]";; esac\nexit 0\n');
  fs.writeFileSync(path.join(fakeBin, 'agent-flow'), '#!/bin/sh\ncase "$1" in patterns) echo "[]";; esac\nexit 0\n');
  for (const name of ['agent-herd', 'agent-audit', 'agent-flow']) fs.chmodSync(path.join(fakeBin, name), 0o755);

  const electron = await pw._electron.launch({
    executablePath: binary,
    args: [APP, '--no-sandbox', `--user-data-dir=${userData}`],
    env: { ...process.env, PATH: `${fakeBin}${path.delimiter}${process.env.PATH || ''}` },
  });
  const errors = [];
  try {
    const win = await electron.firstWindow();
    win.on('pageerror', (err) => errors.push(err.message));
    win.setDefaultTimeout(20000);

    // 受信箱: 課題が未読として並び、本文はカード（対象・課題・根拠・会話で扱う）
    await win.waitForFunction(() => document.querySelector('#area-inbox .unread')?.textContent === '3');
    await win.click('#area-inbox');
    await win.waitForSelector('#inbox-issues:not([hidden])');
    const view = await win.evaluate(() => window.api.attention.list());
    const issue = view.items.find((item) => item.kind === 'issue');
    assert.ok(issue, JSON.stringify(view.items));
    assert.deepStrictEqual(issue.issue.target, { kind: 'skill', name: 'statemachine-use' });
    assert.ok((await win.textContent('#inbox-items')).includes('課題: スキル statemachine-use'));
    const card = await win.textContent('#inbox-issues .execution-card');
    assert.ok(card.includes('スキル statemachine-use') && card.includes('観測 3 件') && card.includes('会話で扱う'), card);
    assert.ok(!card.includes('rules.md'), '改善案の文は置かない');
    // 根拠: 観測 → record → 会話 s1 へのリンク
    await win.waitForFunction(() => document.querySelector('#inbox-issues .issue-evidence .message-action'));
    assert.strictEqual((await win.textContent('#inbox-issues .issue-evidence .message-action')).trim(), '会話 設定画面の見直し');
    await win.click('#inbox-issues .issue-evidence .message-action');
    await win.waitForFunction(() => document.getElementById('chat-title').textContent.includes('設定画面の見直し'));

    // 会話で扱う: フォークと同じダイアログ（位置とフォーク先は隠す）。閉じただけなら課題は残る
    await win.click('#area-inbox');
    await win.waitForSelector('#inbox-issues:not([hidden])');
    await win.click('#inbox-issues .execution-card .primary');
    await win.waitForSelector('#search-transfer-dialog[open]');
    assert.strictEqual((await win.textContent('#search-transfer-title')).trim(), '課題を会話で扱う');
    assert.ok(await win.$eval('#search-boundary', (n) => n.closest('label').hidden), 'フォークする位置は隠す');
    assert.ok(await win.$eval('#search-intent', (n) => n.closest('label').hidden), 'フォーク先は隠す');
    assert.strictEqual((await win.textContent('#search-transfer-start')).trim(), '会話を始める');
    await win.click('#search-transfer-close');
    await win.waitForFunction(() => !document.getElementById('search-transfer-dialog').open);
    const still = await win.evaluate(() => window.api.attention.list());
    assert.ok(still.items.some((item) => item.kind === 'issue'), '閉じただけでは消えない');

    // まとめて評価: 検索の行のチェック → 足元の操作 → 偽の judge が呼ばれ、台帳に評価の行が残る
    await win.click('#session-search-open');
    await win.waitForSelector('#session-search:not([hidden])');
    await win.waitForFunction(() => document.querySelectorAll('#search-results .row-check').length >= 2);
    assert.ok(await win.$eval('#search-batch-start', (n) => n.disabled), '選ぶまで押せない');
    const checks = await win.$$('#search-results .row-check');
    await checks[0].check();
    await checks[1].check();
    await win.waitForFunction(() => document.getElementById('search-batch-count').textContent === '選んだ 2 件');
    assert.ok((await win.textContent('#search-batch-summary')).includes('herd'));
    await win.click('#search-batch-start');
    await win.waitForFunction(() => document.getElementById('search-batch-status').textContent.startsWith('評価しました 2 件'));
    assert.ok((await win.textContent('#search-batch-status')).includes('課題あり 2 件'));
    const asked = fs.readFileSync(judgeLog, 'utf8');
    assert.ok(asked.includes('## 依頼\n設定画面の見直し') && asked.includes('## 依頼\nログ整形の依頼'), asked);
    const rows = fs.readdirSync(audit.feedDir(userData)).flatMap((name) => fs.readFileSync(path.join(audit.feedDir(userData), name), 'utf8').trim().split('\n').map((l) => JSON.parse(l)));
    const evaluations = rows.filter((r) => r.workload === 'evaluation');
    assert.strictEqual(evaluations.length, 2);
    assert.deepStrictEqual(evaluations.map((r) => r.evaluation.issue), ['tool-failure', 'tool-failure']);
    assert.deepStrictEqual(evaluations.map((r) => r.ref).sort(), [s1.id, s2.id].sort());
    await win.click('#session-search-close');

    // 終わったら受信箱に未読で届く。押すと検索画面へ
    await win.click('#area-inbox');
    await win.waitForFunction(() => (document.getElementById('inbox-items').textContent || '').includes('まとめて評価 2 件（課題あり 2 件）'));
    const batches = JSON.parse(fs.readFileSync(path.join(userData, 'evaluation', 'batches.json'), 'utf8'));
    assert.strictEqual(batches.length, 1);
    assert.strictEqual(batches[0].done, 2);
    await win.click('#inbox-items li:nth-child(1) .list-pick');
    await win.waitForSelector('#session-search:not([hidden])');
    if (process.env.SMOKE_OUT) {
      fs.mkdirSync(process.env.SMOKE_OUT, { recursive: true });
      await win.screenshot({ path: path.join(process.env.SMOKE_OUT, 'evaluation-search.png') });
    }
  } finally {
    await electron.close();
  }
  assert.deepStrictEqual(errors, []);
});
