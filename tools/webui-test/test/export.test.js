'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { normalize } = require('../src/casefile');
const { specFor, locatorCode } = require('../src/export');
const { cli, tmpDir, startSampleApp, executablePath } = require('./helpers');

const EXAMPLE = path.join(__dirname, '..', 'examples', 'login.yaml');

test('対象の書き方を Playwright のロケーターに写す', () => {
  assert.strictEqual(locatorCode({ role: 'button', name: '保存' }), 'page.getByRole("button", { name: "保存" })');
  assert.strictEqual(locatorCode({ text: 'OK', exact: true, nth: 1 }), 'page.getByText("OK", { exact: true }).nth(1)');
  assert.strictEqual(locatorCode('#a "b"'), 'page.locator("#a \\"b\\"")');
  assert.strictEqual(locatorCode({ label: 'メール' }), 'page.getByLabel("メール")');
});

test('spec: ケースごとに test.use で設定を閉じ込め、ステップを test.step にする', () => {
  const { suite } = normalize({
    suite: 'S', baseUrl: 'http://h', screenshot: 'failure',
    variants: [{ name: 'en', locale: 'en-US' }],
    cases: [
      { id: 'A', title: 'a', requirement: 'REQ-1', steps: [{ goto: '/x' }, { expect: { url: '/x?y', title: '/^T/i' } }, { screenshot: { name: 'n', path: 'docs/a.png' } }] },
      { id: 'B', title: 'b', skip: 'まだ', steps: [{ goto: '/' }] },
    ],
  });
  const code = specFor(suite);
  assert.match(code, /test\.describe\("\[en\]"/);
  assert.match(code, /"locale":"en-US"/);
  assert.match(code, /test\("A a", \{ tag: \["@REQ-1"\] \}/);
  assert.match(code, /await page\.goto\("http:\/\/h\/x"\);/);
  assert.match(code, /toHaveURL\(new RegExp\("\/x\\\\\?y"\)/);
  assert.match(code, /toHaveTitle\(new RegExp\("\^T", "i"\)/);
  assert.match(code, /copyTo: "docs\/a\.en\.png"/);
  assert.match(code, /test\.skip\("B b"/);
  assert.doesNotMatch(code, /shot\(page, testInfo, "goto"\)/, 'screenshot: failure では操作ごとに撮らない');
});

test('pwtest: 書き出したテストが npx playwright test で通り、HTML レポートが出る', async (t) => {
  const app = await startSampleApp();
  t.after(app.close);
  const dir = tmpDir(t);
  const out = path.join(dir, 'pw');
  const ep = executablePath() ? ['--executable-path', executablePath()] : [];
  const r = await cli(['pwtest', EXAMPLE, '--base-url', app.baseUrl, '--out', out, ...ep, '--', '--workers=2'], { cwd: dir });
  assert.strictEqual(r.code, 0, r.out + r.err);
  assert.match(r.out, /4 passed/);
  assert.ok(fs.existsSync(path.join(out, 'playwright-report', 'index.html')));
  assert.ok(fs.existsSync(path.join(out, 'login.spec.ts')));
});

test('pwtest: 失敗すると終了コード 1', async (t) => {
  const app = await startSampleApp();
  t.after(app.close);
  const dir = tmpDir(t);
  const file = path.join(dir, 'f.yaml');
  fs.writeFileSync(file, `suite: F\nbaseUrl: ${app.baseUrl}\ntimeout: 1000\ncases:\n  - id: F\n    title: 無い見出し\n    steps:\n      - goto: /\n      - expect: { visible: { role: heading, name: 無い } }\n`);
  const ep = executablePath() ? ['--executable-path', executablePath()] : [];
  const r = await cli(['pwtest', file, '--out', path.join(dir, 'pw'), ...ep], { cwd: dir });
  assert.strictEqual(r.code, 1);
  assert.match(r.out, /1 failed/);
});
