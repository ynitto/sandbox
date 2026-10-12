'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { tmpDir } = require('./helpers');
const { loadEnv } = require('../src/config');
const { writeEvidence } = require('../src/evidence');

test('evidence: 設定なしの既定値、設定からの相対・絶対パス、不正な値', (t) => {
  const dir = tmpDir(t);
  assert.equal(loadEnv({ cwd: dir }).evidence, path.join(dir, 'results/webui-test-evidence.json'));
  const config = path.join(dir, 'config/ui.yaml');
  fs.mkdirSync(path.dirname(config));
  const load = () => loadEnv({ cwd: dir, configPath: 'config/ui.yaml' });
  fs.writeFileSync(config, 'envs: { local: {} }\nevidence: ../artifacts/ui.json\n');
  assert.equal(load().evidence, path.join(dir, 'artifacts/ui.json'));
  const absolute = path.join(dir, 'absolute.json');
  fs.writeFileSync(config, JSON.stringify({ envs: { local: {} }, evidence: absolute }));
  assert.equal(load().evidence, absolute);
  for (const value of [null, false, {}, [], 123, ' ']) {
    fs.writeFileSync(config, JSON.stringify({ envs: { local: {} }, evidence: value }));
    assert.throws(load, /evidence: 結果を書き出すファイルのパス/);
  }
});

test('evidence: 出力先を変えても root を解決でき、今回実行したケースだけ入れ替える', (t) => {
  const root = tmpDir(t);
  const report = (file, status) => ({ suites: [{ suite: file, file: path.join(root, file), cases: [{ id: 'S-01', title: file, status }] }] });
  const outDir = path.join(root, 'reports/run');
  const evidenceFile = path.join(root, 'artifacts/nested/ui.json');
  const opts = { root, outDir, evidenceFile };
  writeEvidence(report('login.yaml', 'passed'), [], opts);
  writeEvidence(report('home.yaml', 'passed'), [], opts);
  writeEvidence(report('login.yaml', 'failed'), [], opts);
  const data = JSON.parse(fs.readFileSync(evidenceFile, 'utf8'));
  assert.equal(path.resolve(path.dirname(evidenceFile), data.root), root);
  assert.deepEqual(data.items.map((i) => [i.file, i.status]), [['home.yaml', 'passed'], ['login.yaml', 'failed']]);
  assert.equal(JSON.parse(fs.readFileSync(path.join(outDir, 'evidence.json'), 'utf8')).items.length, 1);
  assert.equal(fs.existsSync(path.join(root, 'results')), false);
});

test('evidence: 既定出力では対象ファイルだけを無視し、既存の ignore を保つ', (t) => {
  const root = tmpDir(t);
  const dir = path.join(root, 'results');
  fs.mkdirSync(dir);
  fs.writeFileSync(path.join(dir, '.gitignore'), '/other-tool.tmp');
  const opts = { root, outDir: path.join(root, 'reports/run') };
  const report = { suites: [] };
  const file = writeEvidence(report, [], opts);
  assert.equal(file, path.join(dir, 'webui-test-evidence.json'));
  writeEvidence(report, [], opts);
  const ignored = fs.readFileSync(path.join(dir, '.gitignore'), 'utf8');
  assert.equal(ignored, '/other-tool.tmp\n/webui-test-evidence.json\n/.gitignore\n');
});

test('evidence: 落ちたケースに理由と失敗時の画面を書く', (t) => {
  const root = tmpDir(t);
  const outDir = path.join(root, 'reports/run');
  const report = { suites: [{ suite: 'ログイン', file: path.join(root, 'login.yaml'), cases: [
    { id: 'S-01', title: '落ちる', status: 'failed', error: 'Timeout: 商品一覧 が出ない',
      screenshots: [{ name: 'goto', file: 'login/S-01/01-goto.png' }, { name: 'failure', file: 'login/S-01/02-failure.png' },
        { name: 'failure', file: 'login/S-01/03-failure.png', explicit: true }] },   // 利用者が同じ名前で撮った画面は取り違えない
    { id: 'S-02', title: '通る', status: 'passed', error: null, screenshots: [] },
  ] }] };
  writeEvidence(report, [], { root, outDir, evidenceFile: path.join(root, 'ui.json') });
  const [failed, passed] = JSON.parse(fs.readFileSync(path.join(root, 'ui.json'), 'utf8')).items;
  assert.equal(failed.error, 'Timeout: 商品一覧 が出ない');
  assert.equal(failed.path, 'reports/run/login/S-01/02-failure.png');
  assert.equal(failed.sha256, undefined);   // 読む側がテストの出力（写しを差し替えるファイル）と取り違えない
  assert.equal(passed.error, undefined);
  assert.equal(passed.path, undefined);
});
