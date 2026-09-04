'use strict';

// **実機の起動**。Electron を本当に立ち上げ、画面が描画されることを確かめる。
//
// 画面の描画はブラウザに window.api を差し替えた確認では担保できない（preload・
// contextBridge・IPC・CSP という Electron 固有の層を丸ごと飛ばすため）。実際、
// contextBridge のグローバルと renderer の const がぶつかって**真っ白**になる事故は
// その方法をすり抜けた。ここは通しで起こして、中身が出ていることまで見る。
//
// 走る条件（満たさないときは skip）:
//   - electron のバイナリが入っている（`npm install` で開発依存まで入れた状態）
//   - 表示先がある（Linux は $DISPLAY。無ければ `xvfb-run -a npm test`）
//   - playwright がある（`_electron` で起動と DOM の確認に使う）
// CI の dashboard 系ジョブは実行時依存だけを入れる方針なので、CI では skip される。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const APP = path.join(__dirname, '..');

function electronBinary() {
  try {
    const p = require.resolve('electron');
    const dist = path.join(path.dirname(p), 'dist', process.platform === 'win32' ? 'electron.exe' : 'electron');
    return fs.existsSync(dist) ? dist : '';
  } catch { return ''; }
}

function playwright() {
  for (const id of ['playwright', 'playwright-core']) {
    try { return require(id); } catch { /* 次を試す */ }
  }
  const global = path.join('/opt/node22/lib/node_modules', 'playwright');
  try { return require(global); } catch { return null; }
}

function whySkip(binary, pw) {
  if (!binary) return 'electron のバイナリが無い（npm install で開発依存も入れる）';
  if (!pw || !pw._electron) return 'playwright が無い（起動の確認に使う）';
  if (process.platform === 'linux' && !process.env.DISPLAY) return '表示先が無い（xvfb-run -a npm test で走る）';
  return '';
}

test('実機: 起動して一覧が描画され、定義を開くと工程が並ぶ', async (t) => {
  const binary = electronBinary();
  const pw = playwright();
  const skip = whySkip(binary, pw);
  if (skip) { t.skip(skip); return; }

  // 起動時に開くフォルダを設定へ仕込む（フォルダ選択はネイティブの窓で、自動では押せない）。
  const store = require('../src/main/store');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-smoke-'));
  store.save(root, {
    name: '煙試験', machine: 'smoke', purpose: '起動して描画されることを見る',
    steps: [
      { kind: 'agent', title: '考える', detail: '考える' },
      { kind: 'command', target: 'python noop.py', detail: '' },
    ],
  });
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-userdata-'));
  fs.writeFileSync(path.join(userData, 'config.json'),
    JSON.stringify({ recentRoots: [root], skillDir: '', agent: 'claude', model: '' }), 'utf8');

  const app = await pw._electron.launch({
    executablePath: binary,
    args: [APP, '--no-sandbox', `--user-data-dir=${userData}`],
    env: { ...process.env, SMK_USER_DATA: userData },
  });
  const errors = [];
  try {
    const win = await app.firstWindow();
    win.on('pageerror', (e) => errors.push(`${e.name}: ${e.message}`));
    win.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

    // 画面に中身が出ていること。ここが空なら「真っ白」の再発。
    await win.waitForSelector('.machine-card', { timeout: 20000 });
    assert.ok((await win.evaluate(() => document.getElementById('main').innerHTML)).length > 0,
      '#main が空（renderer が動いていない＝真っ白）');
    assert.strictEqual(await win.evaluate(() => typeof window.api), 'object', 'preload の窓口が無い');

    await win.click('.machine-card[data-open="smoke"]');
    await win.waitForSelector('[data-step="1"]', { timeout: 20000 });
    // 畳んだカードは 1 文の要約、カードの間に遷移が出る
    assert.match(await win.textContent('[data-step="0"] .sentence'), /考える/);
    assert.match(await win.textContent('.edge[data-edge="0"]'), /OK/);

    // カードを押すと開いて編集欄になる
    await win.click('[data-step="0"] .step-head');
    await win.waitForSelector('[data-step="0"] .step-body [data-field="detail"]', { timeout: 10000 });

    assert.deepStrictEqual(errors, [], '画面でエラーが出ている');
  } finally {
    await app.close();
  }
});
