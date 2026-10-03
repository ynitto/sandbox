'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { planSuite } = require('../src/plan');
const { normalize } = require('../src/casefile');
const { loadEnv } = require('../src/config');
const { cli, tmpDir, startSampleApp, executablePath } = require('./helpers');

const ep = () => (executablePath() ? ['--executable-path', executablePath()] : []);

function suiteOf(data) {
  const { suite, errors } = normalize(data);
  assert.deepStrictEqual(errors, []);
  return suite;
}

test('plan: ケース × variant に展開し、スイート → 環境 → variant → ケースの順で重ねる', () => {
  const suite = suiteOf({
    baseUrl: 'http://suite',
    locale: 'ja-JP',
    setup: { localStorage: { a: 'suite', b: 'suite' } },
    variants: [{ name: 'ja', localStorage: { lang: 'ja' } }, { name: 'en', locale: 'en-US', localStorage: { lang: 'en', b: 'variant' } }],
    cases: [
      { id: 'A', title: 'a', steps: [{ goto: '/' }] },
      { id: 'B', title: 'b', variants: ['en'], localStorage: { b: 'case' }, steps: [{ goto: '/' }] },
    ],
  });
  const env = { name: 'stg', settings: { baseUrl: 'http://env', localStorage: { a: 'env' } } };
  const runs = planSuite(suite, { env });
  assert.deepStrictEqual(runs.map((r) => r.key), ['A [ja]', 'A [en]', 'B [en]']);
  const [aJa, aEn, bEn] = runs;
  assert.strictEqual(aJa.settings.baseUrl, 'http://env');
  assert.strictEqual(aJa.settings.locale, 'ja-JP');
  assert.strictEqual(aEn.settings.locale, 'en-US');
  assert.deepStrictEqual(aEn.settings.localStorage, { a: 'env', b: 'variant', lang: 'en' });
  assert.deepStrictEqual(bEn.settings.localStorage, { a: 'env', b: 'case', lang: 'en' });
  assert.strictEqual(planSuite(suite, { env, baseUrl: 'http://cli' })[0].settings.baseUrl, 'http://cli');
});

test('plan: envs に無い環境・モック禁止の環境ではケースを飛ばす', () => {
  const suite = suiteOf({
    cases: [
      { id: 'L', title: 'ローカルだけ', envs: ['local'], steps: [{ goto: '/' }] },
      { id: 'M', title: 'モックを使う', mocks: [{ url: '**/api', status: 500 }], steps: [{ goto: '/' }] },
      { id: 'N', title: 'ふつう', steps: [{ goto: '/' }] },
    ],
  });
  const local = planSuite(suite, { env: { name: 'local', settings: {} } });
  assert.deepStrictEqual(local.map((r) => r.skip), [null, null, null]);
  const stg = planSuite(suite, { env: { name: 'staging', settings: { mocks: false } } });
  assert.match(stg[0].skip, /local のみ/);
  assert.match(stg[1].skip, /モックを使わない/);
  assert.strictEqual(stg[2].skip, null);
});

test('variants の誤り（名前の重複・無い名前の参照）を挙げる', () => {
  const { errors } = normalize({
    variants: [{ name: 'ja' }, { name: 'ja', lang: 'x' }],
    cases: [{ id: 'A', title: 'a', variants: ['fr'], envs: 'local', steps: [{ goto: '/' }] }],
  });
  const all = errors.join('\n');
  assert.match(all, /name「ja」が重複/);
  assert.match(all, /知らないキー「lang」/);
  assert.match(all, /「fr」は variants にありません/);
  assert.match(all, /envs: 環境名の配列/);
});

test('config: 環境を選び、${VAR} を環境変数で置き換える。足りなければ止める', (t) => {
  const dir = tmpDir(t);
  fs.writeFileSync(path.join(dir, 'webui-test.config.yaml'), 'defaultEnv: local\nenvs:\n  local: { baseUrl: http://localhost:3000 }\n  staging:\n    baseUrl: https://stg.example.com\n    headers: { Authorization: "Bearer ${WT_TOKEN}" }\n    storageState: auth/stg.json\n');
  assert.strictEqual(loadEnv({ cwd: dir }).settings.baseUrl, 'http://localhost:3000');
  assert.throws(() => loadEnv({ cwd: dir, envName: 'staging' }), /WT_TOKEN が設定されていません/);
  process.env.WT_TOKEN = 'abc';
  t.after(() => { delete process.env.WT_TOKEN; });
  const stg = loadEnv({ cwd: dir, envName: 'staging' });
  assert.strictEqual(stg.settings.headers.Authorization, 'Bearer abc');
  assert.strictEqual(stg.settings.storageState, path.join(dir, 'auth', 'stg.json'));
  assert.throws(() => loadEnv({ cwd: dir, envName: 'prod' }), /環境「prod」がありません/);
  const empty = tmpDir(t);
  assert.deepStrictEqual(loadEnv({ cwd: empty }), { name: 'local', settings: {}, file: null, dir: null, serve: null, check: null,
    evidence: path.join(empty, 'results/webui-test-evidence.json') });
});

test('run: variants で言語と画面幅を変えて同じケースを回し、実行記録を残す', async (t) => {
  const app = await startSampleApp();
  t.after(app.close);
  const dir = tmpDir(t);
  fs.writeFileSync(path.join(dir, 'webui-test.config.yaml'), `envs:\n  local: { baseUrl: ${app.baseUrl} }\n  staging: { baseUrl: http://127.0.0.1:9, mocks: false }\n`);
  const file = path.join(dir, 'i18n.yaml');
  fs.writeFileSync(file, `suite: 多言語
screenshot: off
variants:
  - { name: ja, locale: ja-JP, localStorage: { lang: ja } }
  - { name: en-narrow, locale: en-US, localStorage: { lang: en }, viewport: { width: 360, height: 640 } }
cases:
  - id: I-1
    title: ヘッダーの文言
    steps:
      - goto: /
      - expect: { target: { css: "#brand" }, notContains: "brand." }
      - screenshot: { name: header, target: { css: header } }
  - id: I-2
    title: 通信エラー（ローカルだけ）
    envs: [local]
    mocks: [{ url: "**/api/items", status: 500 }]
    steps:
      - goto: /
`);
  const out = path.join(dir, 'res');
  const r = await cli(['run', file, '--out', out, ...ep()], { cwd: dir });
  assert.strictEqual(r.code, 0, r.err);
  assert.match(r.err, /I-1 \[en-narrow\]/);
  const [stamp] = fs.readdirSync(out);
  const report = JSON.parse(fs.readFileSync(path.join(out, stamp, 'results.json'), 'utf8'));
  assert.deepStrictEqual(report.suites[0].cases.map((c) => `${c.id}/${c.variant}`), ['I-1/ja', 'I-1/en-narrow', 'I-2/ja', 'I-2/en-narrow']);
  assert.strictEqual(report.context.env.name, 'local');
  assert.match(report.context.command, /^webui-test run /);
  assert.strictEqual(report.context.files[0].sha256.length, 64);
  assert.match(fs.readFileSync(path.join(out, stamp, 'report.html'), 'utf8'), /実行記録/);

  // capture は variant ごとに名前を分ける
  const shots = path.join(dir, 'shots');
  const c = await cli(['capture', file, '--out', shots, '--only', 'I-1', ...ep()], { cwd: dir });
  assert.strictEqual(c.code, 0, c.err);
  assert.deepStrictEqual(fs.readdirSync(shots).sort(), ['header.en-narrow.png', 'header.ja.png']);

  // 検証環境ではローカル専用のケースを飛ばす（接続先は存在しないので I-1 は失敗するはず）
  const s = await cli(['run', file, '--out', out, '--env', 'staging', '--only', 'I-2', ...ep()], { cwd: dir });
  assert.strictEqual(s.code, 0, s.err);
  assert.match(s.out, /スキップ 2/);
});
