'use strict';

// 受信箱を Electron 実機で通す: 正典（会話・タスクの実行履歴・agent-flow の bus）だけを置いて起動し、
// メニューの「受信箱」に件数が出ること、領域を開くと「要対応」「未読」が並ぶこと、項目から既存の画面へ
// 行けること、開いたら「見た」が config.json に足されて未読が消えることを確かめる。

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

test('実機: 受信箱に要対応と未読が並び、項目から既存の画面へ行き、開いたものは未読から消える', async (t) => {
  const binary = electronBinary();
  const pw = playwright();
  if (!binary) { t.skip('electron のバイナリが無い'); return; }
  if (!pw || !pw._electron) { t.skip('Playwright の Electron ドライバが無い'); return; }
  if (process.platform === 'linux' && !process.env.DISPLAY) { t.skip('表示先が無い'); return; }

  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-attention-repo-'));
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-attention-userdata-'));
  const appStore = require('../src/main/store');
  const machineStore = require('../src/main/automation/store');
  const flowStore = require('../src/main/automation/flow-store');
  const runHistory = require('../src/main/automation/run-history');

  // 受信箱の基準時刻を過去に置く（起動時に「今」が基準になると、置いた結果が全部既読になる）
  appStore.saveConfig(userData, { repos: [repo], lastRepo: repo, area: 'conversation', transport: 'headless', share: { enabled: false },
    attentionSeen: { since: '2026-01-01T00:00:00.000Z' } });

  // 会話: 応答で終わっている（未読）
  const done = appStore.createSession(userData, { repo, cli: 'codex', model: 'gpt-test', transport: 'headless' });
  appStore.appendMessage(userData, done.id, { role: 'user', text: '画面を確認して' });
  appStore.appendMessage(userData, done.id, { role: 'assistant', cli: 'codex', text: '確認できました。' });
  // 会話: 依頼で終わっている（結果なし。受信箱に出ない）
  const asked = appStore.createSession(userData, { repo, cli: 'codex', model: 'gpt-test', transport: 'headless' });
  appStore.appendMessage(userData, asked.id, { role: 'user', text: 'まだ答えていない依頼' });

  // タスク: 実行履歴に完了の記録（未読）
  machineStore.save(repo, {
    name: '月次集計', machine: 'report', purpose: '集計',
    steps: [{ kind: 'agent', title: '集計', detail: '集計する' }],
  });
  runHistory.append(userData, repo, { runId: 'r1', taskId: 'machine:report', machine: 'report', source: 'manual', ok: true, finishedAt: '2026-09-06T02:00:00.000Z' });

  // ワークフロー: 承認待ちの実行（要対応）
  flowStore.save(repo, {
    version: 2, id: 'monthly', name: '月次レポート', description: '月次レポートを作る',
    purpose: 'implementation', entry: ['draft'], exit: ['draft'],
    nodes: [{ id: 'draft', label: '下書き', kind: 'work', goal: '{{request}} を書く', deps: [], tier: 'auto' }],
  }, 'create');
  const flowBus = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-attention-bus-'));
  const runId = 'app-attention-test';
  const runDir = path.join(flowBus, 'runs', runId);
  fs.mkdirSync(path.join(flowBus, 'inbox'), { recursive: true });
  fs.mkdirSync(path.join(runDir, 'interactions', 'ix-0123456789abcdef'), { recursive: true });
  fs.writeFileSync(path.join(flowBus, 'inbox', `${runId}.json`), JSON.stringify({
    id: runId, title: '月次レポートの実行', request: '9 月分を作る', submitter: 'agent-app', readonly: true,
    submitted_at: '2026-09-06T01:00:00Z',
    submitter_context: { root: repo, workflow: 'monthly', parameters: {}, agent: 'codex', model: 'gpt-test' },
  }));
  fs.writeFileSync(path.join(runDir, 'meta.json'), JSON.stringify({
    status: 'running', phase: 'executing', created_at: '2026-09-06T01:00:00Z', updated_at: '2026-09-06T01:01:00Z',
    request: '9 月分を作る', orch_lease_until: Date.now() / 1000 + 3600,
  }));
  fs.writeFileSync(path.join(runDir, 'graph.json'), JSON.stringify({
    nodes: { draft: { id: 'draft', kind: 'work', goal: '9 月分を書く', deps: [] }, approve: { id: 'approve', kind: 'human', goal: '下書きを承認する', deps: ['draft'] } },
  }));
  fs.writeFileSync(path.join(runDir, 'interactions', 'ix-0123456789abcdef', 'request.json'), JSON.stringify({
    node_id: 'approve', mode: 'approval', prompt: '下書きを承認しますか', created_at: '2026-09-06T01:01:00Z',
  }));

  const fakeBin = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-attention-fakebin-'));
  fs.writeFileSync(path.join(fakeBin, 'agent-flow'), '#!/bin/sh\ncase "$1" in patterns) echo "[]";; esac\nexit 0\n');
  fs.chmodSync(path.join(fakeBin, 'agent-flow'), 0o755);

  const electron = await pw._electron.launch({
    executablePath: binary,
    args: [APP, '--no-sandbox', `--user-data-dir=${userData}`],
    env: { ...process.env, AGENT_APP_FLOW_BUS: flowBus, PATH: `${fakeBin}${path.delimiter}${process.env.PATH || ''}` },
  });
  const errors = [];
  try {
    const win = await electron.firstWindow();
    win.on('pageerror', (err) => errors.push(err.message));
    win.setDefaultTimeout(20000);

    // メニューの件数（「共有」と同じ印）→ 領域を開く
    await win.waitForFunction(() => document.querySelector('#area-inbox .unread')?.textContent === '3');
    await win.click('#area-inbox');
    await win.waitForSelector('#inbox-area:not([hidden])');
    assert.strictEqual((await win.textContent('#area-list-title')).trim(), '受信箱');
    assert.ok(await win.$eval('#session-new', (node) => node.hidden), '受信箱には作る操作が無い');
    // IPC: 投影そのものを preload の窓口から読む（判定は main。renderer は出すだけ）
    const view = await win.evaluate(() => window.api.attention.list());
    assert.strictEqual(view.action, 1);
    assert.strictEqual(view.unread, 2);
    // 要対応を先に、未読は新しい結果から（会話は今つくったので、置いた日付のタスクより新しい）
    assert.deepStrictEqual(view.items.map((item) => [item.kind, item.queue]), [['workflow', 'action'], ['conversation', 'unread'], ['task', 'unread']]);
    assert.ok(!view.items.some((item) => item.target.id === asked.id), '依頼で終わっている会話は出ない');
    assert.strictEqual((await win.textContent('#inbox-title')).trim(), '要対応 1 · 未読 2');
    const rows = await win.$$eval('#inbox-items li', (nodes) => nodes.map((node) => ({ cls: node.className, text: node.textContent })));
    assert.strictEqual(rows.length, 3);
    assert.ok(rows[0].cls.includes('attention') && rows[0].text.includes('月次レポートの実行') && rows[0].text.includes('承認待ち'), JSON.stringify(rows[0]));
    assert.ok(rows[1].text.includes('画面を確認して') && rows[1].text.includes('完了'), JSON.stringify(rows[1]));
    assert.ok(rows[2].text.includes('月次集計') && rows[2].text.includes('完了') && rows[2].text.includes(path.basename(repo)), JSON.stringify(rows[2]));
    if (process.env.SMOKE_OUT) {
      fs.mkdirSync(process.env.SMOKE_OUT, { recursive: true });
      await win.screenshot({ path: path.join(process.env.SMOKE_OUT, 'attention-inbox-full.png') });
    }

    // 要対応 → ワークフロー画面へ（答える場所は既存の画面。受信箱には残る）
    await win.click('#inbox-items li:nth-child(1) .list-pick');
    await win.waitForSelector('#automation:not([hidden])');
    await win.waitForFunction(() => document.getElementById('area-workflows').classList.contains('on'));
    await win.waitForFunction(() => (document.querySelector('#workflows li.active')?.textContent || '').includes('月次レポート'));
    assert.strictEqual((await win.textContent('#area-inbox .unread')).trim(), '3');

    // 未読（タスク）→ タスク画面でその項目を選ぶ。開いたので未読から消える
    await win.click('#area-inbox');
    await win.waitForSelector('#inbox-area:not([hidden])');
    await win.click('#inbox-items li:nth-child(3) .list-pick');
    await win.waitForFunction(() => document.getElementById('area-tasks').classList.contains('on'));
    await win.waitForFunction(() => (document.querySelector('#tasks li.active')?.textContent || '').includes('月次集計'));
    await win.waitForFunction(() => document.querySelector('#area-inbox .unread')?.textContent === '2');

    // 未読（会話）→ 会話を開く（通知と同じ経路）。config.json に「見た」だけが足される
    await win.click('#area-inbox');
    await win.waitForSelector('#inbox-area:not([hidden])');
    await win.click('#inbox-items li:nth-child(2) .list-pick');
    await win.waitForFunction(() => document.getElementById('area-work').classList.contains('on'));
    await win.waitForFunction(() => document.getElementById('chat-title').textContent.includes('画面を確認して'));
    await win.waitForFunction(() => document.querySelector('#area-inbox .unread')?.textContent === '1');
    const after = await win.evaluate(() => window.api.attention.list());
    assert.deepStrictEqual(after.items.map((item) => item.queue), ['action']);
    const cfg = JSON.parse(fs.readFileSync(path.join(userData, 'config.json'), 'utf8'));
    assert.deepStrictEqual(Object.keys(cfg.attentionSeen.items).sort(), [`conversation:${done.id}`, `task:${repo}:report`]);
    assert.strictEqual(cfg.attentionSeen.since, '2026-01-01T00:00:00.000Z');
    assert.ok(!fs.existsSync(path.join(userData, 'inbox')), '受信箱の保存先は作らない');

    // 答えが届けば要対応から消える（正典の側で閉じる。受信箱は状態を持たない）
    fs.mkdirSync(path.join(runDir, 'interactions', 'ix-0123456789abcdef', 'responses'), { recursive: true });
    fs.writeFileSync(path.join(runDir, 'interactions', 'ix-0123456789abcdef', 'responses', 'response-1.json'), JSON.stringify({ answer: { decision: 'approved' } }));
    const resolved = await win.evaluate(() => window.api.attention.list());
    assert.deepStrictEqual(resolved, { action: 0, unread: 0, items: [] });
    await win.click('#area-inbox');
    await win.waitForFunction(() => document.getElementById('inbox-title').textContent === '受信箱は空です');
    assert.strictEqual(await win.$('#area-inbox .unread'), null);

    if (process.env.SMOKE_OUT) {
      fs.mkdirSync(process.env.SMOKE_OUT, { recursive: true });
      await win.screenshot({ path: path.join(process.env.SMOKE_OUT, 'attention-inbox.png') });
    }
  } finally {
    await electron.close();
  }
  assert.deepStrictEqual(errors, []);
});
