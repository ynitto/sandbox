'use strict';

// 設定 > 実行制御「判定と評価」の「判定モデルを残す時間（分）」。値は agent-herd の設定に
// あるので、画面は読んだ分を出し、変えたときだけ書きに行く（分で書けない値は触らない）。

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs'), path = require('path'), os = require('os');
const store = require('../src/main/store');

function playwright() {
  try { return require('playwright'); } catch { /* 次を試す */ }
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}

test('Electron: 判定モデルを残す時間は分で出し、変えたときだけ書く', { timeout: 120000 }, async (t) => {
  const pw = playwright();
  if (!pw?._electron) return t.skip('Playwright unavailable');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-keep-alive-'));
  const repo = path.join(root, 'repo'), data = path.join(root, 'data');
  fs.mkdirSync(repo);
  store.saveConfig(data, {
    repos: [repo], lastRepo: repo, area: 'conversation', lastCli: 'claude', transport: 'headless', useWorktree: false,
    audit: { enabled: false }, share: { enabled: false }, update: { onStartup: false },
  });
  const app = await pw._electron.launch({
    executablePath: require('electron'),
    args: [path.resolve(__dirname, '..'), '--no-sandbox', `--user-data-dir=${data}`],
  });
  try {
    const win = await app.firstWindow();
    win.setDefaultTimeout(15000);
    await win.waitForFunction(() => typeof document.getElementById('settings-open')?.onclick === 'function');
    // 判定の設定は agent-herd の設定ファイル。読みは 30 分が入っている状態にする。
    await app.evaluate(({ ipcMain }) => {
      globalThis.__judgeWrites = [];
      ipcMain.removeHandler('judge:get');
      ipcMain.handle('judge:get', () => ({ ok: true, data: { available: true, value: { mode: 'auto', model: '', keepMinutes: 30 }, error: '' } }));
      ipcMain.removeHandler('judge:set');
      ipcMain.handle('judge:set', (_event, payload) => {
        globalThis.__judgeWrites.push(payload);
        return { ok: true, data: { ...payload.value, model: payload.value.model || '' } };
      });
    });
    await win.reload();
    const errors = [];
    win.on('pageerror', (error) => errors.push(error.message));
    await win.waitForFunction(() => typeof document.getElementById('settings-open')?.onclick === 'function');
    await win.click('#settings-open');
    await win.click('[data-settings-tab="execution"]');
    await win.waitForFunction(() => document.getElementById('judge-keep-alive')?.value === '30');

    const field = await win.evaluate(() => {
      const input = document.getElementById('judge-keep-alive');
      const row = input.closest('.setting-field');
      return {
        label: row.querySelector('strong').textContent,
        type: input.type, min: input.min, max: input.max,
        // 同じグループの既存行と同じ形で組む（新しい部品を作らない）
        sameShapeAsJudgeMode: document.getElementById('judge-mode').closest('.setting-field').className === row.className,
        hint: row.querySelector('small') ? row.querySelector('small').textContent : '',
      };
    });
    assert.equal(field.label, '判定モデルを残す時間（分）');
    assert.equal(field.type, 'number');
    assert.deepEqual([field.min, field.max], ['0', '1440']);
    assert.equal(field.sameShapeAsJudgeMode, true, '判定の行と同じ .setting-field で組む');
    assert.equal(field.hint, '', '説明文は画面に常駐させない');

    // 触らずに保存 → 残す時間は書きに行かない
    await win.click('#settings-save');
    await win.waitForFunction(() => document.getElementById('settings-status').textContent === '保存しました');
    assert.deepEqual(await app.evaluate(() => globalThis.__judgeWrites), [], '変えていない設定は書かない');

    // 45 分へ変えて保存 → 残す時間つきで 1 回だけ書く
    await win.fill('#judge-keep-alive', '45');
    await win.click('#settings-save');
    await win.waitForFunction(() => state.judge.value.keepMinutes === 45);
    const writes = await app.evaluate(() => globalThis.__judgeWrites);
    assert.equal(writes.length, 1);
    assert.equal(writes[0].keepAlive, true, '残す時間を変えたときは書く');
    assert.deepEqual(writes[0].value, { mode: 'auto', model: '', keepMinutes: 45 });
    assert.equal(await win.inputValue('#judge-keep-alive'), '45');

    // 空へ戻すと既定（ollama の 5 分）へ。書く値は agent-herd 側で空文字になる
    await win.fill('#judge-keep-alive', '');
    await win.click('#settings-save');
    await win.waitForFunction(() => state.judge.value.keepMinutes === '');
    const cleared = await app.evaluate(() => globalThis.__judgeWrites.at(-1));
    assert.equal(cleared.value.keepMinutes, '');
    assert.deepEqual(errors, []);
    if (process.env.AGENT_APP_KEEP_ALIVE_SCREENSHOT) {
      await win.locator('#judge-keep-alive').scrollIntoViewIfNeeded();
      await win.screenshot({ path: process.env.AGENT_APP_KEEP_ALIVE_SCREENSHOT });
    }
  } finally {
    await app.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});
