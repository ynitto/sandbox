'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const net = require('net');
const path = require('path');
const { loadEnv } = require('../src/config');
const { startServer, reachable } = require('../src/serve');
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

// サンプルアプリを写した小さなプロジェクト。webui-test.config.yaml に起動と e2e を書く（仕様書は持たない）
async function project(t) {
  const dir = tmpDir(t);
  fs.cpSync(SAMPLE, path.join(dir, 'app'), { recursive: true });
  const port = await freePort();
  write(path.join(dir, 'webui-test.config.yaml'), [
    `serve: { command: "node app/server.js ${port}", url: "http://127.0.0.1:${port}/" }`,
    'check:',
    '  cases: [tests]',
    'envs:',
    '  local: {}',
    '  staging: { baseUrl: "https://stg.example.test" }',
    '',
  ].join('\n'));
  write(path.join(dir, 'tests', 'login.yaml'), [
    'suite: ログイン画面',
    'cases:',
    '  - id: S-01',
    '    title: ログイン画面',
    '    steps:',
    '      - goto: /',
    '      - expect: { visible: { role: heading, name: ログイン } }',
    '      - measure: { name: 表示, steps: 2, max: 60000 }',
    '      - screenshot: { name: login, target: { css: "#login" } }',
    '',
  ].join('\n'));
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

test('check: 起動して e2e → 前回の画面と比べる。変わっても落とさず、何が変わったかを evidence で渡す', async (t) => {
  const { dir } = await project(t);
  const evidence = () => Object.fromEntries(JSON.parse(fs.readFileSync(path.join(dir, 'webui-test-results', 'evidence.json'), 'utf8')).items.map((i) => [i.id, i]));

  let r = await cli(['check', ...ep()], { cwd: dir });
  assert.strictEqual(r.code, 0, r.out);
  assert.doesNotMatch(r.out, /単体テスト|仕様書/, '単体テストも仕様書も扱わない');
  assert.match(r.out, /e2e: 1 件中 合格 1/);
  assert.match(r.out, /画面: 1 枚（前回と同じ 0・変わった 0・新しい 1・なくなった 0）/);
  assert.strictEqual(r.err, '', '結果は標準出力の最後にまとめる');
  assert.strictEqual(fs.readFileSync(path.join(dir, 'webui-test-results', '.gitignore'), 'utf8').split('\n').slice(-2)[0], '*',
    '結果の置き場はリポジトリの変更に数えさせない');
  assert.match(r.out, /テストで得たもの（振る舞い・時間・画面）: webui-test-results\/evidence.json/);
  let ev = evidence();
  assert.deepStrictEqual([ev['login/S-01'].kind, ev['login/S-01'].status, ev['login/S-01'].file], ['behavior', 'passed', 'tests/login.yaml']);
  assert.strictEqual(ev['login/S-01/表示'].unit, 'ms');
  assert.ok(ev['login/S-01/load'].value > 0, 'ページの読み込み時間');
  const first = ev['login/S-01/login'];
  assert.deepStrictEqual([first.kind, first.status, first.history.length], ['image', 'new', 1]);
  assert.match(first.path, /^webui-test-results\/screens\/login\/S-01\/login\.png$/);
  assert.ok(fs.existsSync(path.join(dir, first.path)));
  const root = JSON.parse(fs.readFileSync(path.join(dir, 'webui-test-results', 'evidence.json'), 'utf8')).root;
  assert.ok(fs.existsSync(path.join(dir, 'webui-test-results', root, first.path)), 'パスの起点を root で渡す（読む側は置き場を知らなくてよい）');

  r = await cli(['check', ...ep()], { cwd: dir });
  assert.match(r.out, /前回と同じ 1・変わった 0/);
  ev = evidence();
  assert.strictEqual(ev['login/S-01/login'].status, 'same');
  assert.strictEqual(ev['login/S-01/login'].sha256, first.sha256, '同じ画面なら同じファイルのまま');

  // 実装を変える（ボタンの文言）。ケースは通り、画面が変わったことを渡す
  const html = path.join(dir, 'app', 'index.html');
  fs.writeFileSync(html, fs.readFileSync(html, 'utf8').replace('<button type="submit">ログイン</button>', '<button type="submit">ログインする（新しい文言）</button>'));
  r = await cli(['check', ...ep()], { cwd: dir });
  assert.strictEqual(r.code, 0, r.out);
  assert.match(r.out, /変わった 1/);
  assert.match(r.out, /↻ login\/S-01\/login — \d+ 画素が違います/);
  const changed = evidence()['login/S-01/login'];
  assert.strictEqual(changed.status, 'changed');
  assert.notStrictEqual(changed.sha256, first.sha256);
  assert.deepStrictEqual(changed.history, [changed.sha256, first.sha256], 'これまでの版を渡す（受け取る側が古い画像を見分ける）');
  assert.ok(fs.existsSync(path.join(dir, changed.previous)) && fs.existsSync(path.join(dir, changed.diff)), '前の画像と差分を残す');

  // 撮らなくなった画面
  const yaml = path.join(dir, 'tests', 'login.yaml');
  fs.writeFileSync(yaml, fs.readFileSync(yaml, 'utf8').replace(/ +- screenshot:.*\n/, ''));
  r = await cli(['check', ...ep()], { cwd: dir });
  assert.match(r.out, /なくなった 1/);
  assert.strictEqual(evidence()['login/S-01/login'].status, 'removed');
});

test('check: ケースが落ちれば落とす。測った時間が目安を超えても落とす。ケースが無ければ使い方の誤り', async (t) => {
  const { dir } = await project(t);
  const yaml = path.join(dir, 'tests', 'login.yaml');
  fs.writeFileSync(yaml, fs.readFileSync(yaml, 'utf8').replace('max: 60000', 'max: 0.001'));
  let r = await cli(['check', ...ep()], { cwd: dir });
  assert.match(r.out, /「表示」が \d+ms かかり、目安の 0.001ms を超えました/);
  fs.writeFileSync(yaml, fs.readFileSync(yaml, 'utf8').replace('max: 0.001', 'max: 60000').replace('name: ログイン }', 'name: 無い見出し }'));
  r = await cli(['check', ...ep()], { cwd: dir });
  assert.strictEqual(r.code, 1, r.out);
  assert.match(r.out, /e2e: 1 件中 合格 0 \/ 不合格 1/);
  const empty = tmpDir(t);
  write(path.join(empty, 'webui-test.config.yaml'), 'envs: { local: {} }\n');
  r = await cli(['check'], { cwd: empty });
  assert.strictEqual(r.code, 2);
  assert.match(r.err, /動かすテストケースがありません/);
});
