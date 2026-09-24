'use strict';

// Electron 実機で評価（§19）を通す: 受信箱の課題カード（未達の条件・元を開く・依頼欄の実行設定）、
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
    evaluation: { mode: 'off', strategy: 'legacy' }, audit: { enabled: false },
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
      kind: 'quality-review', occurrences: 3, confidence: 'low',
      statement: `スクロール確認 ${i + 1}: 手順が不足しています。`, observation_ids: ['obs-scroll'],
      scope: { target: { kind: 'skill', name: `skill-${i}` } }, exported: false,
      improvement: { version: 1, target: { kind: 'skill', name: `skill-${i}` }, criteria: [{ requirement: '成果物を作る', evidence: '未作成' }] },
    }));
  }
  electron = await pw._electron.launch({ executablePath: binary,
    args: [APP, '--no-sandbox', `--user-data-dir=${userData}`],
  });
  const win = await electron.firstWindow();
  await win.waitForFunction(() => document.querySelector('#area-inbox .unread')?.textContent === '16');
  await win.locator('#area-inbox').click();
  await win.waitForFunction(() => document.querySelectorAll('#inbox-issues .execution-card').length === 16);
  await win.waitForFunction(() => document.querySelectorAll('#inbox-issues .inbox-sources button').length === 16);
  assert.ok(!(await win.locator('#inbox-issues').innerText()).includes(externalId), '開けない記録のIDを出さない');
  const sourceButton = win.locator('#inbox-issues .inbox-sources button').first();
  assert.equal(await sourceButton.innerText(), '元の会話を開く');
  assert.equal(await sourceButton.getAttribute('title'), longTitle);
  assert.match(await win.locator('#inbox-issues .inbox-card-foot').first().innerText(), /開けない参照元 1 件/);
  const header = win.locator('#inbox-area > .area-head');
  const cards = win.locator('#inbox-issues .execution-card');
  assert.equal(await win.locator('#inbox-sub').isVisible(), false, '課題カードがあるときは案内を重ねない');
  assert.equal(await cards.first().locator('.primary').innerText(), '修正開始');
  assert.ok(await cards.first().locator('.primary').isDisabled(), '事前プロンプトが空なら始められない');
  assert.equal(await cards.first().locator('.execution-card-head p').innerText(), '課題 · 未達の条件 1 つ');
  assert.ok(!(await cards.first().innerText()).includes('スクロール確認'), '未達の条件の連結文は出さない');
  assert.ok(!(await cards.first().innerText()).includes('確度 low'), '内部の評価値を本文に混ぜない');
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
    await cards.last().locator('.run-settings > summary').click();
    await cards.last().locator('.settings-popover select').first().waitFor();
    assert.ok(await win.locator('#inbox-body').evaluate((body) => body.scrollWidth <= body.clientWidth), '実行設定を開いても横にはみ出さない');
    await cards.last().locator('.run-settings > summary').click();
    await win.mouse.move(area.x + area.width / 2, area.y + area.height - 50);
    await win.mouse.wheel(0, -100000);
    await win.waitForFunction(() => document.querySelector('#inbox-issues .execution-card').getBoundingClientRect().top > 0);
  }
  await cards.first().locator('.inbox-sources button').click();
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
    evaluation: { mode: 'off', strategy: 'legacy' }, attentionSeen: { since: '2026-01-01T00:00:00.000Z' } });
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
    id: 'ins-1', ts: '2026-09-18T02:00:00Z', updated_at: '2026-09-18T02:00:00Z', kind: 'quality-review', occurrences: 3, confidence: 'low',
    statement: 'スキル statemachine-use で手順が足りず、依頼を満たせていない（3 件）', observation_ids: ['obs-1'],
    scope: { target: { kind: 'skill', name: 'statemachine-use' } }, exported: false,
    improvement: { version: 1, target: { kind: 'skill', name: 'statemachine-use' }, criteria: [{ requirement: '出力工程', evidence: '出力は未作成' }] },
  }));
  fs.writeFileSync(path.join(storeDir, 'observations', '20260918.jsonl'), `${JSON.stringify({ id: 'obs-1', record_id: 'rec-1', evidence: ['rec-1'], kind: 'skill-gap' })}\n`);
  fs.writeFileSync(path.join(storeDir, 'records', '20260918.jsonl'), `${JSON.stringify({ id: 'rec-1', ts: '2026-09-18T01:00:00Z', kind: 'ledger', tool: 'agent-app', workload: 'evaluation', purpose: 'chat', ref: s1.id,
    evaluation: { proposal: { schema_version: 1, checks: [{ text: '出力工程', status: 'unmet', evidence_id: 'e1' }],
      evidence: [{ id: 'e1', text: '出力は未作成' }] } } })}\n`);

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

    // 受信箱: 課題が未読として並び、本文はカード（対象・未達の条件と起きたこと・元を開く・依頼欄）
    await win.waitForFunction(() => document.querySelector('#area-inbox .unread')?.textContent === '3');
    await win.click('#area-inbox');
    await win.waitForSelector('#inbox-issues:not([hidden])');
    const view = await win.evaluate(() => window.api.attention.list());
    const issue = view.items.find((item) => item.kind === 'issue');
    assert.ok(issue, JSON.stringify(view.items));
    assert.deepStrictEqual(issue.issue.target, { kind: 'skill', name: 'statemachine-use' });
    assert.ok((await win.textContent('#inbox-items')).includes('スキル statemachine-use'));
    const card = await win.textContent('#inbox-issues .execution-card');
    assert.ok(card.includes('スキル statemachine-use') && card.includes('3 件') && card.includes('修正開始'), card);
    assert.ok(!card.includes('rules.md'), '改善案の文は置かない');
    assert.ok(!card.includes('手順が足りず'), '課題の連結文は出さず、条件を 1 件ずつ並べる');
    assert.strictEqual((await win.textContent('#inbox-issues .finding-excerpts li')).trim(), '未達出力工程出力は未作成');
    assert.strictEqual(await win.locator('#inbox-issues .quality-evidence').count(), 0, '評価の根拠の折りたたみは置かない');
    // 元を開く: 観測 → record → 会話 s1
    await win.waitForSelector('#inbox-issues .inbox-sources button');
    assert.strictEqual((await win.textContent('#inbox-issues .inbox-sources button')).trim(), '元の会話を開く');
    assert.ok((await win.getAttribute('#inbox-issues .inbox-sources button', 'title')).includes('設定画面の見直し'));
    await win.click('#inbox-issues .inbox-sources button');
    await win.waitForFunction(() => document.getElementById('chat-title').textContent.includes('設定画面の見直し'));

    // 依頼欄: タスクの「作成開始」と同じ実行設定の折りたたみと主ボタン。ダイアログは挟まない。
    // 課題の作業フォルダを使わない設定なので、行き先のリポジトリを実行設定で選ぶ
    await win.click('#area-inbox');
    await win.waitForSelector('#inbox-issues:not([hidden])');
    const issueCard = win.locator('#inbox-issues .execution-card').first();
    assert.ok(await issueCard.locator('.primary').isDisabled(), '事前プロンプトが空なら始められない');
    await issueCard.locator('.run-settings > summary').click();
    const labels = await issueCard.locator('.settings-popover label').evaluateAll((nodes) => nodes.map((n) => n.firstChild.textContent));
    assert.deepStrictEqual(labels, ['リポジトリ', 'エージェント', 'モデル', '権限']);
    assert.strictEqual(await issueCard.locator('.settings-popover select').first().inputValue(), repo);
    await issueCard.locator('.settings-popover input').fill('gpt-test');
    assert.ok((await issueCard.locator('.run-settings > summary').innerText()).includes('/ gpt-test'), '選んだモデルを折りたたみの 1 行に出す');
    await issueCard.locator('.run-settings > summary').click();
    await issueCard.locator('textarea').fill('原因を調べてください');
    assert.strictEqual(await win.locator('#search-transfer-dialog[open]').count(), 0);
    if (process.env.SMOKE_OUT) {
      fs.mkdirSync(process.env.SMOKE_OUT, { recursive: true });
      await win.screenshot({ path: path.join(process.env.SMOKE_OUT, 'inbox-issue.png') });
    }
    await issueCard.locator('textarea').fill('');

    // 評価するときだけ選択欄を開き、選んだ会話を評価する。
    await win.click('#session-search-open');
    await win.waitForSelector('#session-search:not([hidden])');
    await win.fill('#search-repo', path.basename(repo));
    await win.locator('#search-more > summary').click();
    await win.selectOption('#search-source', 'app');
    await win.locator('#search-more > summary').click();
    await win.waitForFunction(() => document.getElementById('search-batch-start').textContent === 'まとめて評価' && document.querySelectorAll('#search-results .list-pick').length === 2 && !document.getElementById('search-batch-start').disabled);
    assert.equal(await win.locator('#search-results input[type="checkbox"]:visible').count(), 0);
    assert.equal(await win.locator('#search-batch-status').isVisible(), false, '実行前の説明は表示しない');
    assert.equal(await win.locator('#search-batch select, #search-batch input').count(), 0, '評価のエージェント・モデル選択は置かない');
    await win.click('#search-batch-start');
    assert.equal(await win.locator('#search-results .row-check:visible').count(), 2);
    assert.equal(await win.locator('#search-results .row-check:checked').count(), 0);
    assert.ok(await win.locator('#search-batch-start').isDisabled(), '初期状態では実行できない');
    await win.click('#search-batch-cancel');
    assert.equal(await win.locator('#search-results .row-check:visible').count(), 0);
    assert.ok(!fs.existsSync(judgeLog), '選択をキャンセルしただけでは評価しない');
    await win.click('#search-batch-start');
    const checks = win.locator('#search-results .row-check');
    assert.equal(await win.locator('#search-results .row-check:checked').count(), 0);
    assert.ok(await win.locator('#search-batch-start').isDisabled(), '未選択では実行できない');
    await checks.nth(0).check();
    await checks.nth(1).check();
    await win.click('#search-batch-start');
    await win.waitForFunction(() => document.querySelectorAll('#search-results .row-check:not([hidden])').length === 0);
    await win.waitForFunction(() => document.getElementById('search-batch-status').textContent.startsWith('評価完了 2件'));
    assert.ok((await win.textContent('#search-batch-status')).includes('課題あり 2件'));
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
