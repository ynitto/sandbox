'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { cli, tmpDir, startSampleApp, executablePath } = require('./helpers');
const { loadEnv } = require('../src/config');

test('init: 既定の設定を作り、既存の設定とケースには触れない', async (t) => {
  const dir = tmpDir(t);
  const r = await cli(['init'], { cwd: dir });
  assert.equal(r.code, 0, r.err);
  const env = loadEnv({ cwd: dir });
  assert.equal(env.settings.baseUrl, 'http://localhost:3000');
  assert.equal(env.serve, null);
  assert.equal(env.evidence, path.join(dir, 'results/webui-test-evidence.json'));
  assert.deepEqual(env.check.cases, [path.join(dir, 'tests/e2e')]);
  assert.equal(fs.existsSync(path.join(dir, 'tests')), false);
  const file = env.file;
  const before = fs.readFileSync(file, 'utf8');
  const again = await cli(['init', '--base-url', 'http://localhost:9000'], { cwd: dir });
  assert.notEqual(again.code, 0);
  assert.match(again.err, /上書きしません/);
  assert.equal(fs.readFileSync(file, 'utf8'), before);
});

test('init: 別の置き先・JSON・起動コマンド・複数ケースを指定できる', async (t) => {
  const dir = tmpDir(t);
  const r = await cli(['init', 'app', '--config', 'config/webui-test.config.json', '--base-url', 'http://localhost:4000',
    '--serve', 'npm run dev', '--cases', '../tests/login.yaml', '--cases', '../tests/home.yaml'], { cwd: dir });
  assert.equal(r.code, 0, r.err);
  const env = loadEnv({ cwd: dir, configPath: 'app/config/webui-test.config.json' });
  assert.equal(env.serve.command, 'npm run dev');
  assert.equal(env.serve.cwd, path.join(dir, 'app/config'));
  assert.deepEqual(env.check.cases, [path.join(dir, 'app/tests/login.yaml'), path.join(dir, 'app/tests/home.yaml')]);
});

test('init: 別拡張子の既存設定も保護し、不正な入力では作らない', async (t) => {
  const dir = tmpDir(t);
  const existing = path.join(dir, 'webui-test.config.yml');
  fs.writeFileSync(existing, 'envs: { local: {} }\n');
  assert.notEqual((await cli(['init'], { cwd: dir })).code, 0);
  assert.equal(fs.existsSync(path.join(dir, 'webui-test.config.yaml')), false);
  for (const args of [['--base-url', 'file:///tmp'], ['--serve', ' '], ['--cases', ''], ['--config', 'new.txt']]) {
    assert.notEqual((await cli(['init', 'new', ...args], { cwd: dir })).code, 0);
    assert.equal(fs.existsSync(path.join(dir, 'new')), false);
  }
});

test('init → capture: 設計書側から実装側の設定を読み、文書用の画像を作れる', async (t) => {
  const dir = tmpDir(t);
  const app = await startSampleApp();
  t.after(() => app.close());
  const initialized = await cli(['init', 'app', '--base-url', app.baseUrl], { cwd: dir });
  assert.equal(initialized.code, 0, initialized.err);
  const design = path.join(dir, 'design');
  fs.mkdirSync(path.join(design, 'docs'), { recursive: true });
  fs.writeFileSync(path.join(design, 'docs/screens.yaml'),
    'suite: 仕様書の画像\ncases:\n  - id: S-01\n    title: ログイン画面\n    steps:\n      - goto: /\n      - screenshot: { name: login-form }\n');
  const ep = executablePath();
  const r = await cli(['capture', 'docs/screens.yaml', '--config', '../app/webui-test.config.yaml',
    '--out', 'docs/images', ...(ep ? ['--executable-path', ep] : [])], { cwd: design });
  assert.equal(r.code, 0, r.err);
  const image = fs.readFileSync(path.join(design, 'docs/images/login-form.png'));
  assert.equal(image.subarray(1, 4).toString(), 'PNG');
  assert.equal(fs.existsSync(path.join(design, 'webui-test-results/evidence.json')), false);
  assert.equal(fs.existsSync(path.join(design, 'results/webui-test-evidence.json')), false);
});
