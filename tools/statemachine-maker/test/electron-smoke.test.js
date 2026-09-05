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
    const binary = require('electron');
    return typeof binary === 'string' && fs.existsSync(binary) ? binary : '';
  } catch { return ''; }
}

function playwright() {
  for (const id of ['playwright', 'playwright-core']) {
    try { return require(id); } catch { /* 次を試す */ }
  }
  const nodePrefix = path.dirname(path.dirname(process.execPath));
  const bundled = path.join(nodePrefix, 'lib', 'node_modules', '@playwright', 'cli', 'node_modules', 'playwright-core');
  try { return require(bundled); } catch { /* 次を試す */ }
  const global = path.join('/opt/node22/lib/node_modules', 'playwright');
  try { return require(global); } catch { return null; }
}

function whySkip(binary, pw) {
  if (!binary) return 'electron のバイナリが無い（npm install で開発依存も入れる）';
  if (!pw || !pw._electron) return 'playwright が無い（起動の確認に使う）';
  if (process.platform === 'linux' && !process.env.DISPLAY) return '表示先が無い（xvfb-run -a npm test で走る）';
  return '';
}

test('実機: 起動して一覧が描画され、開くと工程が並ぶ', async (t) => {
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
  const fakeBin = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-agent-tools-'));
  const fakeAvailable = process.platform !== 'win32';
  if (fakeAvailable) {
    const candidate = {
      schemaVersion: 1, status: 'candidate', summary: '確認工程の下書きを作りました', questions: [], assumptions: [], findings: [],
      candidate: { name: 'AI下書き', machine: 'ai-draft', purpose: '依頼内容を確認する', steps: [{ kind: 'agent', title: '内容を確認', detail: '依頼内容を確認する' }] },
    };
    const review = {
      schemaVersion: 1, status: 'candidate', summary: '確認内容を明確にしました', questions: [], assumptions: [],
      findings: [{ category: 'consistency', severity: 'suggestion', stepId: 'step_1', title: '確認内容を具体化', detail: '工程の目的が伝わりやすくなります' }],
      candidate: {
        name: '煙試験', machine: 'smoke', purpose: '起動して描画されることを見る',
        steps: [
          { id: 'step_1', kind: 'agent', title: '考える', detail: 'よく考える' },
          { id: 'step_2', kind: 'command', target: 'python noop.py', detail: '' },
        ],
      },
    };
    const executable = path.join(fakeBin, 'agent-herd');
    fs.writeFileSync(executable, `#!/usr/bin/env node
if (process.argv.includes('defs')) process.stdout.write(JSON.stringify({ definitions: ['fake'] }));
else {
  const prompt = process.argv[process.argv.indexOf('-p') + 1] || '';
  if (prompt.includes('レビュー担当')) process.stdout.write(${JSON.stringify(JSON.stringify(review))});
  else if (prompt.includes('前回の応答は契約違反でした')) process.stdout.write(${JSON.stringify(JSON.stringify(candidate))});
  else process.stdout.write('JSONではない応答');
}
`, 'utf8');
    fs.chmodSync(executable, 0o755);
  }
  fs.writeFileSync(path.join(userData, 'config.json'),
    JSON.stringify({ roots: [root], lastRoot: root, skillDir: '', agent: fakeAvailable ? 'fake' : 'claude', model: '' }), 'utf8');

  const app = await pw._electron.launch({
    executablePath: binary,
    args: [APP, '--no-sandbox', `--user-data-dir=${userData}`],
    env: { ...process.env, PATH: fakeAvailable ? `${fakeBin}${path.delimiter}${process.env.PATH || ''}` : process.env.PATH, SMK_USER_DATA: userData },
  });
  const errors = [];
  try {
    const win = await app.firstWindow();
    win.on('pageerror', (e) => errors.push(`${e.name}: ${e.message}`));
    win.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    const resize = async (width) => {
      await app.evaluate(({ BrowserWindow }, size) => BrowserWindow.getAllWindows()[0].setSize(size.width, size.height), { width, height: 800 });
      await win.waitForTimeout(100);
    };
    const assertNoHorizontalOverflow = async (label) => {
      const metrics = await win.evaluate(() => ({
        viewport: document.documentElement.clientWidth,
        page: document.documentElement.scrollWidth,
        mainClient: document.getElementById('main').clientWidth,
        mainScroll: document.getElementById('main').scrollWidth,
      }));
      assert.ok(metrics.page <= metrics.viewport && metrics.mainScroll <= metrics.mainClient,
        `${label} で横スクロールが発生: ${JSON.stringify(metrics)}`);
    };

    // 画面に中身が出ていること。ここが空なら「真っ白」の再発。
    await win.waitForSelector('.execution-item', { timeout: 20000 });
    assert.ok((await win.evaluate(() => document.getElementById('main').innerHTML)).length > 0,
      '#main が空（renderer が動いていない＝真っ白）');
    assert.strictEqual(await win.evaluate(() => typeof window.api), 'object', 'preload の窓口が無い');
    // 左は登録したフォルダ、右はそのフォルダのワークフロー
    assert.strictEqual(await win.evaluate(() => document.querySelectorAll('.folder-list li').length), 1,
      '登録したフォルダだけが並ぶ');
    for (const width of [760, 980, 1440]) {
      await resize(width);
      await assertNoHorizontalOverflow(`ホーム ${width}px`);
    }
    await resize(760);
    if (process.env.SMK_SCREENSHOT_HOME) await win.screenshot({ path: process.env.SMK_SCREENSHOT_HOME });
    await win.click('[data-home-tab="workflows"]');
    await win.waitForSelector('.machine-card[data-open="smoke"]');
    await win.click('#h-ai-draft');
    await win.waitForFunction(() => document.getElementById('dlg-ai-draft').open);
    assert.match(await win.textContent('#dlg-ai-draft'), /作りたいワークフロー/);
    const draftBox = await win.evaluate(() => {
      const dlg = document.getElementById('dlg-ai-draft');
      const body = dlg.querySelector('.dlg-body');
      const rect = dlg.getBoundingClientRect();
      return { left: rect.left, right: rect.right, viewport: innerWidth, client: body.clientWidth, scroll: body.scrollWidth };
    });
    assert.ok(draftBox.left >= 0 && draftBox.right <= draftBox.viewport && draftBox.scroll <= draftBox.client,
      `dlg-ai-draft: ${JSON.stringify(draftBox)}`);
    if (process.env.SMK_SCREENSHOT_DRAFT) await win.screenshot({ path: process.env.SMK_SCREENSHOT_DRAFT });
    if (fakeAvailable) {
      await win.fill('#ai-draft-request', '依頼内容を確認する流れを作りたい');
      await win.click('[data-ai-start-draft]');
      await win.waitForSelector('[data-ai-open-draft]', { timeout: 10000 });
      assert.match(await win.textContent('#dlg-ai-draft'), /下書きができました.*AI下書き/s);
      assert.ok(!fs.existsSync(path.join(root, '.statemachine', 'ai-draft')), 'AIは下書きを直接保存しない');
      await win.click('[data-ai-back]');
    }
    await win.evaluate(() => document.getElementById('dlg-ai-draft').close());

    await win.click('.machine-card[data-open="smoke"]');
    await win.waitForSelector('[data-step="1"]', { timeout: 20000 });
    // 畳んだカードは 1 文の要約、カードの間に「次にどこへ行くか」が人の言葉で出る
    assert.match(await win.textContent('[data-step="0"] .sentence'), /考える/);
    assert.match(await win.textContent('.edge[data-edge="0"]'), /できた/);

    // カードを押すと右側の編集パネルに設定が出る
    await win.click('[data-step="0"] .step-head');
    await win.waitForSelector('.inspector .step-body [data-field="detail"]', { timeout: 10000 });

    for (const width of [760, 980, 1440]) {
      await resize(width);
      await assertNoHorizontalOverflow(`${width}px`);
    }

    await resize(760);
    assert.strictEqual(await win.isVisible('.flow-pane'), false, '狭い画面では選択中の編集だけを表示する');
    assert.strictEqual(await win.isVisible('.inspector'), true);
    if (process.env.SMK_SCREENSHOT_NARROW) await win.screenshot({ path: process.env.SMK_SCREENSHOT_NARROW });
    await win.click('[data-inspector-back]');
    assert.strictEqual(await win.isVisible('.flow-pane'), true, '「流れに戻る」で工程一覧へ戻る');

    await resize(1440);
    await win.click('[data-step="0"] .step-head');
    const columns = await win.evaluate(() => {
      const flow = document.querySelector('.flow-pane').getBoundingClientRect();
      const inspector = document.querySelector('.inspector').getBoundingClientRect();
      return { flow: Math.round(flow.width), inspector: Math.round(inspector.width) };
    });
    assert.ok(columns.flow > columns.inspector && Math.abs(columns.inspector - 400) <= 2, JSON.stringify(columns));
    if (process.env.SMK_SCREENSHOT) await win.screenshot({ path: process.env.SMK_SCREENSHOT });

    // もっとも狭い対応幅で全ダイアログの横あふれを確認する。
    await resize(760);
    for (const id of ['b-record', 'b-files', 'b-settings', 'b-ai']) {
      await win.evaluate((buttonId) => document.getElementById(buttonId).click(), id);
      const dialogId = { 'b-record': 'dlg-record', 'b-files': 'dlg-files', 'b-ai': 'dlg-ai', 'b-settings': 'dlg-settings' }[id];
      await win.waitForFunction((target) => document.getElementById(target).open, dialogId);
      const box = await win.evaluate((target) => {
        const dlg = document.getElementById(target);
        const body = dlg.querySelector('.dlg-body');
        const rect = dlg.getBoundingClientRect();
        return { left: rect.left, right: rect.right, viewport: innerWidth, client: body.clientWidth, scroll: body.scrollWidth };
      }, dialogId);
      assert.ok(box.left >= 0 && box.right <= box.viewport && box.scroll <= box.client, `${dialogId}: ${JSON.stringify(box)}`);
      if (id === 'b-ai' && fakeAvailable) {
        await win.click('[data-ai-start-review]');
        await win.waitForSelector('[data-ai-apply]', { timeout: 10000 });
        assert.match(await win.textContent('#dlg-ai'), /確認内容を具体化.*内容を変更/s);
        await win.click('[data-ai-apply]');
        await win.waitForFunction(() => !document.getElementById('dlg-ai').open);
        assert.strictEqual(await win.inputValue('.inspector [data-field="detail"]'), 'よく考える');
        assert.strictEqual(store.read(root, 'smoke').raw.steps[0].detail, '考える', 'AI提案の反映だけでは保存しない');
      } else {
        await win.evaluate((target) => document.getElementById(target).close(), dialogId);
      }
    }
    if (fakeAvailable) {
      win.once('dialog', (confirmation) => confirmation.accept());
      await win.click('#btn-home');
      await win.waitForSelector('.machine-card[data-open="smoke"]');
    }

    // 実際に描かれた文字にも内部の用語を出さない
    const shown = await win.evaluate(() => document.body.innerText);
    for (const term of ['output_validator', 'condition_rule', 'check_ok', 'last_output', '--dry-run', 'workflow.yaml', '遷移']) {
      assert.ok(!shown.includes(term), `画面に内部の用語が出ています: ${term}`);
    }

    assert.deepStrictEqual(errors, [], '画面でエラーが出ている');
  } finally {
    await app.close();
  }
});
