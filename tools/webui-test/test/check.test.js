'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const net = require('net');
const path = require('path');
const { loadEnv } = require('../src/config');
const { startServer, reachable } = require('../src/serve');
const { scanDocs } = require('../src/docimages');
const { buildPrompt, traceLines } = require('../src/generate');
const { cli, tmpDir, executablePath } = require('./helpers');

const ep = () => (executablePath() ? ['--executable-path', executablePath()] : []);
const SAMPLE = path.join(__dirname, '..', 'examples', 'sample-app');

function freePort() {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => resolve(port)); });
  });
}

function write(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text);
}

// サンプルアプリを写した小さなプロジェクト。webui-test.config.yaml に起動・e2e・仕様書を書く
async function project(t) {
  const dir = tmpDir(t);
  fs.cpSync(SAMPLE, path.join(dir, 'app'), { recursive: true });
  const port = await freePort();
  write(path.join(dir, 'webui-test.config.yaml'), [
    `serve: { command: "node app/server.js ${port}", url: "http://127.0.0.1:${port}/" }`,
    'check:',
    '  cases: [tests]',
    '  docs: [docs]',
    'envs:',
    '  local: {}',
    '  staging: { baseUrl: "https://stg.example.test" }',
    '',
  ].join('\n'));
  write(path.join(dir, 'tests', 'login.yaml'), [
    '# coherence: doc=docs/login.md',
    'suite: ログイン画面',
    'cases:',
    '  - id: S-01',
    '    title: ログイン画面',
    '    steps:',
    '      - goto: /',
    '      - expect: { visible: { role: heading, name: ログイン } }',
    '      - screenshot: { name: login, path: docs/images/login.png, target: { css: "#login" } }',
    '',
  ].join('\n'));
  write(path.join(dir, 'docs', 'login.md'), '# ログイン\n\n![ログイン画面](images/login.png)\n');
  return { dir, port };
}

test('config: serve と check を読み、パスは設定ファイルから。全体の serve は別の接続先の環境には効かない', async (t) => {
  const { dir, port } = await project(t);
  const env = loadEnv({ cwd: dir });
  assert.strictEqual(env.name, 'local');
  assert.strictEqual(env.serve.command, `node app/server.js ${port}`);
  assert.strictEqual(env.serve.cwd, dir);
  assert.strictEqual(env.settings.baseUrl, `http://127.0.0.1:${port}/`, '接続先が無ければ serve の URL');
  assert.deepStrictEqual(env.check.cases, [path.join(dir, 'tests')]);
  assert.strictEqual(env.check.maxDiffRatio, 0, '既定は 1 画素でも違えば落とす（色の近さの許容はある）');
  const stg = loadEnv({ cwd: dir, envName: 'staging' });
  assert.strictEqual(stg.serve, null, '検証環境を動かすときにローカルを起動しない');
  write(path.join(dir, 'webui-test.config.yaml'), 'serve: { url: http://x }\ncheck: { unit: npm test }\nenvs: { local: {} }\n');
  assert.throws(() => loadEnv({ cwd: dir }), (e) => /serve\.command/.test(e.message) && /知らないキー「unit」/.test(e.message));
});

test('serve: 応答するまで待って起動し、止める。起動済みならそのまま使う。終わってしまえば理由を出す', async (t) => {
  const port = await freePort();
  const url = `http://127.0.0.1:${port}/`;
  const serve = { command: `node ${JSON.stringify(path.join(SAMPLE, 'server.js'))} ${port}`, url, cwd: SAMPLE, env: {}, timeout: 20000 };
  const s = await startServer(serve);
  assert.strictEqual(s.started, true);
  assert.ok(await reachable(url));
  const again = await startServer(serve);
  assert.strictEqual(again.started, false);
  await s.stop();
  assert.strictEqual(await reachable(url), false, '止めたあとは応答しない');
  await assert.rejects(startServer({ ...serve, command: 'node -e "console.log(\'boom\'); process.exit(3)"' }), /起動のコマンドが終わってしまいました（3）[\s\S]*boom/);
});

test('scanDocs: 仕様書が貼っている画像を拾い、無い先を見つける（コードブロックと外部 URL は見ない）', (t) => {
  const dir = tmpDir(t);
  write(path.join(dir, 'a.png'), 'x');
  write(path.join(dir, 'docs', 'spec.md'), [
    '![ある](../a.png) <img src="gone.png">',
    '```', '![例](nothing.png)', '```',
    '![外](https://example.test/x.png) `![例](inline.png)`',
  ].join('\n'));
  const r = scanDocs([path.join(dir, 'docs')]);
  assert.strictEqual(r.docs, 1);
  assert.deepStrictEqual(r.links.map((l) => [l.target, l.exists]), [['../a.png', true], ['gone.png', false]]);
});

test('check: 起動して e2e → 仕様書の画像。無い画像は撮り直すまで落とし、画面が変われば落とす', async (t) => {
  const { dir } = await project(t);
  const image = path.join(dir, 'docs', 'images', 'login.png');

  let r = await cli(['check', ...ep()], { cwd: dir });
  assert.strictEqual(r.code, 1, r.out);
  assert.doesNotMatch(r.out, /単体テスト/, '単体テストは codd-statemachine など呼び出し側が動かす');
  assert.match(r.out, /e2e: 1 件中 合格 1/);
  assert.match(r.out, /仕様書の画像: 1 枚中 1 枚が今の画面と違う[\s\S]*docs\/images\/login.png — まだ無い/);
  assert.match(r.out, /仕様書の画像のリンク: 1 文書中 1 件の先がない[\s\S]*docs\/login.md:3 → images\/login.png/);
  assert.ok(!fs.existsSync(image), 'check は仕様書の画像を書き換えない');
  assert.strictEqual(r.err, '', '結果は標準出力の最後にまとめる');
  assert.strictEqual(fs.readFileSync(path.join(dir, 'webui-test-results', '.gitignore'), 'utf8').split('\n').slice(-2)[0], '*',
    '結果の置き場はリポジトリの変更に数えさせない');

  r = await cli(['check', '--update', ...ep()], { cwd: dir });
  assert.strictEqual(r.code, 0, r.out);
  assert.match(r.out, /1 枚中 1 枚を撮り直した[\s\S]*login.png（新規）/);
  assert.ok(fs.existsSync(image));

  r = await cli(['check', ...ep()], { cwd: dir });
  assert.strictEqual(r.code, 0, r.out);
  assert.match(r.out, /== webui-test check: 整合している/);
  assert.match(r.out, /仕様書の画像: 1 枚とも今の画面と同じ/);
  assert.match(r.out, /1 文書、1 件とも実在する/);

  // 実装を変える（ボタンの文言）。ケースは通るが、仕様書の画像が古くなる
  const html = path.join(dir, 'app', 'index.html');
  fs.writeFileSync(html, fs.readFileSync(html, 'utf8').replace('<button type="submit">ログイン</button>', '<button type="submit">ログインする（新しい文言）</button>'));
  r = await cli(['check', ...ep()], { cwd: dir });
  assert.strictEqual(r.code, 1, r.out);
  assert.match(r.out, /e2e: 1 件中 合格 1/);
  assert.match(r.out, /login.png — \d+ 画素が違います[\s\S]*--update/);
  const results = JSON.parse(fs.readFileSync(path.join(dir, r.out.match(/レポート: (\S+)report\.html/)[1], 'results.json'), 'utf8'));
  const doc = results.suites[0].cases[0].docImages[0];
  assert.strictEqual(doc.status, 'changed');
  assert.ok(doc.diff, '差分の画像を残す');
});

test('check: ケースが落ちれば落とす。ケースが無ければ使い方の誤り', async (t) => {
  const { dir } = await project(t);
  write(path.join(dir, 'tests', 'login.yaml'), fs.readFileSync(path.join(dir, 'tests', 'login.yaml'), 'utf8').replace('name: ログイン }', 'name: 無い見出し }'));
  let r = await cli(['check', ...ep()], { cwd: dir });
  assert.strictEqual(r.code, 1, r.out);
  assert.match(r.out, /e2e: 1 件中 合格 0 \/ 不合格 1/);
  const empty = tmpDir(t);
  write(path.join(empty, 'webui-test.config.yaml'), 'envs: { local: {} }\n');
  r = await cli(['check'], { cwd: empty });
  assert.strictEqual(r.code, 2);
  assert.match(r.err, /動かすテストケースがありません/);
});

test('generate --doc / --code: 仕様書を依頼に入れ、つながりの注記を書く（--update で今の注記も残す）', () => {
  const p = buildPrompt({ conditions: 'c', docs: [{ name: 'docs/login.md', text: '# ログイン\nボタンは「ログイン」' }] });
  assert.match(p, /## 仕様書: docs\/login.md[\s\S]*ボタンは「ログイン」/);
  assert.deepStrictEqual(traceLines({ docs: ['docs/login.md'], code: ['src/login.tsx'] }, '# coherence: doc=docs/login.md\n# coherence: test=x.yaml\n'),
    ['# coherence: doc=docs/login.md', '# coherence: test=x.yaml', '# coherence: code=src/login.tsx']);
});
