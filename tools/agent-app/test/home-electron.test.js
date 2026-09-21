'use strict';

// ホーム（1 つの入力欄から始める領域）を実機で通す。面は会話画面のものなので、ここで確かめるのは
// 「送った結果の受け方」だけ——振り分けがタスクの流用で止めたとき、案内を見せずにタスク画面でその
// タスクを開き、依頼から写した入力を添え、案内だけの会話を残さないこと。判定は本物の runTurn
// （agent-herd route）を通し、agent-herd は PATH の偽物が答える。

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');

const APP = path.resolve(__dirname, '..');

function playwright() {
  try { return require('playwright'); } catch { /* 次を試す */ }
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}

test('実機: ホームから送ると、タスクの流用は案内を挟まずタスク画面で開き、会話は残らない', async (t) => {
  const pw = playwright();
  let binary;
  try { binary = require('electron'); } catch { /* 無ければ skip */ }
  if (!pw?._electron || !binary || (process.platform === 'linux' && !process.env.DISPLAY)) return t.skip('Electron を表示できない');

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-home-'));
  const repo = path.join(dir, 'repo'), data = path.join(dir, 'userdata');
  fs.mkdirSync(repo);
  const store = require('../src/main/store');
  // 既定のままホームが開く（config を保存しても area は触らない）
  store.saveConfig(data, { repos: [repo], lastRepo: repo, lastCli: 'codex', useWorktree: false, transport: 'headless', share: { enabled: false }, evaluation: { mode: 'off' } });
  require('../src/main/automation/store').save(repo, {
    name: 'リリース確認', machine: 'release-check', purpose: '公開前の確認',
    steps: [{ kind: 'agent', title: '変更を確認', detail: '{{period}} の変更を確認する' }],
  });

  // 偽の agent-herd。route は「タスク『リリース確認』を流用（止めてよい）」と答え、extract は
  // 依頼文から写した入力を返す。どちらも本物と同じ形（stdout に JSON、終了コード 0）。
  const fakeBin = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-home-bin-'));
  fs.writeFileSync(path.join(fakeBin, 'agent-herd'), [
    '#!/bin/sh',
    'case "$1" in',
    '  route) echo \'{"handling":{"choice":"task","confidence":0.88},"task":{"choice":"release-check","confidence":0.83},"flow":null,"skills":[],"routine":null,"hold":true,"stage":"judge","abstained":[]}\';;',
    '  --purpose) echo \'{"period":"前月"}\';;',
    'esac',
    'exit 0',
    '',
  ].join('\n'));
  fs.chmodSync(path.join(fakeBin, 'agent-herd'), 0o755);

  const electron = await pw._electron.launch({
    executablePath: binary,
    args: [APP, '--no-sandbox', `--user-data-dir=${data}`],
    env: { ...process.env, PATH: `${fakeBin}${path.delimiter}${process.env.PATH || ''}` },
  });
  const errors = [];
  try {
    const win = await electron.firstWindow();
    win.setDefaultTimeout(30000);
    win.on('pageerror', (error) => errors.push(error.message));
    await win.waitForFunction(() => typeof document.getElementById('area-home')?.onclick === 'function');
    // 送信できる相手が 1 つある状態にする（起動そのものはここでは起きない——流用で止まるため）
    await electron.evaluate(({ ipcMain }) => {
      ipcMain.removeHandler('agents:list');
      ipcMain.handle('agents:list', () => ({ ok: true, data: [{ name: 'codex', available: true, interactive: false }] }));
    });
    await win.reload();
    await win.waitForFunction(() => typeof document.getElementById('area-home')?.onclick === 'function');

    // 初回起動の既定画面はホーム
    await win.waitForFunction(() => document.getElementById('chat-title').textContent === 'ホーム');
    assert.equal(await win.locator('#area-home').getAttribute('aria-current'), 'page');

    // 実行設定でこの PC の CLI を選ぶ（人がやるのと同じ。ホームでも入力欄の実行設定は会話と同じもの）
    await win.click('#run-settings > summary');
    await win.selectOption('#cli', 'codex');
    await win.click('#run-settings > summary');

    const request = '前月分のリリース確認をして';
    await win.fill('#prompt', request);
    await win.click('#send');

    await new Promise((r) => setTimeout(r, 8000));
    console.log('DIAG', JSON.stringify(await win.evaluate(() => ({
      notice: document.getElementById('notice').textContent, status: document.getElementById('input-status').textContent,
      area: state.area, automationHidden: document.getElementById('automation').hidden,
      messages: (state.current && state.current.messages || []).map((m) => m.role), current: !!state.current,
      actions: [...document.querySelectorAll('.message-action')].map((b) => b.textContent),
    }))));
    // タスク画面でそのタスクが開く（案内の 1 枚は出さない）
    await win.waitForFunction(() => !document.getElementById('automation').hidden);
    await win.waitForFunction(() => state.selectedTask === 'machine:release-check');
    assert.equal(await win.locator('.message-action', { hasText: 'タスクを開く' }).count(), 0, 'ホームでは案内を挟まない');
    // 依頼から写した入力が概要の実行条件に入っている（実行のボタンは人が押す）
    const panel = win.locator('#automation-workbench');
    await panel.locator('[data-run-param="period"]').waitFor();
    assert.equal(await panel.locator('[data-run-param="period"]').inputValue(), '前月');
    // 案内だけの会話は残さない。本文は入力欄に残る（戻って会話で送り直せる）
    assert.deepEqual(store.listSessions(data, repo), []);
    assert.equal(await win.inputValue('#prompt'), request);
    assert.deepEqual(errors, []);
  } finally {
    await electron.close();
    fs.rmSync(dir, { recursive: true, force: true });
    fs.rmSync(fakeBin, { recursive: true, force: true });
  }
});
