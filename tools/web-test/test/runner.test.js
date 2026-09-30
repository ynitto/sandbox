'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { cli, tmpDir, startSampleApp, executablePath } = require('./helpers');

const EXAMPLE = path.join(__dirname, '..', 'examples', 'login.yaml');
const ep = () => (executablePath() ? ['--executable-path', executablePath()] : []);

function onlyDir(d) {
  const [name] = fs.readdirSync(d);
  return path.join(d, name);
}

test('run: 例がすべて通り、ステップごとの画像とレポートが出る', async (t) => {
  const app = await startSampleApp();
  t.after(app.close);
  const dir = tmpDir(t);
  const r = await cli(['run', EXAMPLE, '--base-url', app.baseUrl, '--out', dir, '--workers', '2', ...ep()]);
  assert.strictEqual(r.code, 0, r.err);
  assert.match(r.out, /合計 4: 合格 4/);
  const out = onlyDir(dir);
  const report = JSON.parse(fs.readFileSync(path.join(out, 'results.json'), 'utf8'));
  const tc1 = report.suites[0].cases.find((c) => c.id === 'TC-001');
  assert.deepStrictEqual(tc1.screenshots.map((s) => s.name), ['goto', 'fill', 'fill', 'click', '商品一覧']);
  for (const s of tc1.screenshots) assert.ok(fs.existsSync(path.join(out, s.file)), s.file);
  const html = fs.readFileSync(path.join(out, 'report.html'), 'utf8');
  assert.match(html, /TC-003/);
  assert.match(html, /<img[^>]+src="[^"]+05-/);
  assert.match(fs.readFileSync(path.join(out, 'report.md'), 'utf8'), /\| TC-001 \| ✅ 合格/);
});

test('run: 失敗は理由と失敗時の画像つきで報告し、終了コード 1', async (t) => {
  const app = await startSampleApp();
  t.after(app.close);
  const dir = tmpDir(t);
  const file = path.join(dir, 'fail.yaml');
  fs.writeFileSync(file, `suite: 失敗の確認
baseUrl: ${app.baseUrl}
timeout: 1500
screenshot: failure
cases:
  - id: F-1
    title: 無い文字を待つ
    steps:
      - goto: /
      - expect: { target: { role: heading, level: 1 }, text: 違う見出し }
  - id: F-2
    title: 飛ばす
    skip: まだ作っていない
    steps:
      - goto: /
`.replace('role: heading, level: 1', 'role: heading'));
  const r = await cli(['run', file, '--out', dir, ...ep()]);
  assert.strictEqual(r.code, 1);
  const report = JSON.parse(fs.readFileSync(path.join(onlyDir(dir), 'results.json'), 'utf8'));
  const [f1, f2] = report.suites[0].cases;
  assert.strictEqual(f1.status, 'failed');
  assert.match(f1.error, /文字が "違う見出し" ではありません（実際: "ログイン"）/);
  assert.deepStrictEqual(f1.screenshots.map((s) => s.name), ['failure']);
  assert.strictEqual(f2.status, 'skipped');
  assert.deepStrictEqual(report.summary, { total: 2, passed: 0, failed: 1, skipped: 1 });
});

test('run: localStorage はアプリより先に入り、ケースをまたいで残らない', async (t) => {
  const app = await startSampleApp();
  t.after(app.close);
  const dir = tmpDir(t);
  const file = path.join(dir, 'ls.yaml');
  fs.writeFileSync(file, `suite: 状態の分離
baseUrl: ${app.baseUrl}
screenshot: off
cases:
  - id: A
    title: 英語
    localStorage: { lang: en, obj: { a: 1 } }
    steps:
      - goto: /
      - expect: { title: Sample Store }
      - expect: { target: { css: "#brand" }, text: Sample Store }
      - eval: "if (localStorage.getItem('obj') !== '{\\"a\\":1}') throw new Error('obj')"
  - id: B
    title: 既定の日本語
    steps:
      - goto: /
      - expect: { title: サンプルストア }
`);
  const r = await cli(['run', file, '--out', dir, '--workers', '2', ...ep()]);
  assert.strictEqual(r.code, 0, r.err);
});

test('capture: screenshot ステップの画像だけを名前で書き出し、path: にも写す', async (t) => {
  const app = await startSampleApp();
  t.after(app.close);
  const dir = tmpDir(t);
  const file = path.join(dir, 'spec.yaml');
  fs.writeFileSync(file, `suite: 仕様書の画像
baseUrl: ${app.baseUrl}
cases:
  - id: S-1
    title: ログイン画面
    steps:
      - goto: /
      - fill: { target: { label: メールアドレス }, value: user@example.com }
      - screenshot: { name: login-form, target: { css: main }, path: docs/img/login.png }
`);
  const out = path.join(dir, 'shots');
  const r = await cli(['capture', file, '--out', out, '--capture-root', dir, ...ep()]);
  assert.strictEqual(r.code, 0, r.err);
  assert.deepStrictEqual(fs.readdirSync(out), ['login-form.png']);
  assert.ok(fs.existsSync(path.join(dir, 'docs', 'img', 'login.png')));
});

test('validate: 誤りがあれば終了コード 2 で場所を示す', async (t) => {
  const dir = tmpDir(t);
  const file = path.join(dir, 'bad.yaml');
  fs.writeFileSync(file, 'cases:\n  - id: X\n    title: t\n    steps:\n      - clik: a\n');
  const r = await cli(['validate', file]);
  assert.strictEqual(r.code, 2);
  assert.match(r.err, /bad.yaml: cases\[0\]\.steps\[0\]: 知らないキー「clik」/);
});
