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
