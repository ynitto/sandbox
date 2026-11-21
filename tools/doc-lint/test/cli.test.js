'use strict';

const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { resolvePackage } = require('../src/evaluators');

const here = path.join(__dirname, '..');
const cli = path.join(here, 'bin', 'doc-lint.js');

function run(cwd, args, env = process.env) {
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'doc-lint-output-'));
  const stdout = path.join(output, 'stdout');
  const stderr = path.join(output, 'stderr');
  const out = fs.openSync(stdout, 'w');
  const err = fs.openSync(stderr, 'w');
  try {
    const result = spawnSync(process.execPath, [cli, ...args], {
      cwd, env, timeout: 60_000, stdio: ['ignore', out, err],
    });
    if (result.error) throw result.error;
    return { status: result.status, stdout: fs.readFileSync(stdout, 'utf8'), stderr: fs.readFileSync(stderr, 'utf8') };
  } finally {
    fs.closeSync(out);
    fs.closeSync(err);
    fs.rmSync(output, { recursive: true, force: true });
  }
}

function expectCode(result, code) {
  assert.equal(result.status, code, result.stderr || result.stdout);
  return result.stdout;
}

function temp(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'doc-lint-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function setup(t, fixture) {
  const root = temp(t);
  expectCode(run(root, ['init']), 0);
  if (fixture) fs.copyFileSync(path.join(__dirname, 'fixtures', fixture), path.join(root, fixture));
  return root;
}

const configPath = path.join('.doc-lint', 'config.yaml');
const read = (root, file) => fs.readFileSync(path.join(root, file), 'utf8');
const manifest = (root) => JSON.parse(read(root, 'package.json'));

test('init は設定と依存を作り、既存の設定を保持する', (t) => {
  const root = setup(t);
  const config = read(root, configPath);
  assert.match(config, /preset: ja/);
  const { devDependencies } = manifest(root);
  assert.deepEqual(Object.keys(devDependencies).sort(), [
    '@plantuml/mcp-js', '@viz-js/viz', 'jsdom', 'markdownlint', 'mermaid',
    'textlint', 'textlint-rule-preset-ja-technical-writing',
  ]);
  const changed = config.replace('failOn: error', 'failOn: warning');
  fs.writeFileSync(path.join(root, configPath), changed);
  expectCode(run(root, ['init']), 0);
  assert.equal(read(root, configPath), changed);
  expectCode(run(root, ['init', '--force']), 0);
  assert.equal(read(root, configPath), config);
});

test('check は正常な Markdown と埋め込み図を検査する', (t) => {
  const root = setup(t, 'valid.md');
  assert.match(expectCode(run(root, ['check']), 0), /1 files checked, 0 errors/);
});

test('markdownlint の指摘は警告で、fail-on warning で失敗する', (t) => {
  const root = setup(t, 'invalid-style.md');
  assert.match(expectCode(run(root, ['check']), 0), /warning {2}markdownlint\/MD\d+/);
  expectCode(run(root, ['check', '--fail-on', 'warning']), 1);
});

test('textlint は日本語プリセットの文長ルールを適用する', (t) => {
  const root = setup(t, 'invalid-text.md');
  const output = expectCode(run(root, ['check', '--fail-on', 'never']), 0);
  assert.match(output, /textlint\/preset-ja-technical-writing\/sentence-length/);
});

function configureTools(root, ...names) {
  const config = read(root, configPath).replace('tools: []', ['tools:', ...names.map((name) => `  - ${name}`)].join('\n'));
  fs.writeFileSync(path.join(root, configPath), config);
}

function stubPackage(root, name, source) {
  const folder = path.join(root, 'node_modules', name);
  fs.mkdirSync(folder, { recursive: true });
  const file = path.join(folder, 'index.js');
  fs.writeFileSync(file, source);
  return file;
}

test('未解決の textlint ルールが tools にある場合は終了コード 2 を返す', (t) => {
  const root = setup(t, 'valid.md');
  const name = 'textlint-rule-does-not-exist-xyz';
  configureTools(root, name);
  const result = run(root, ['check', '--fail-on', 'never']);
  assert.equal(expectCode(result, 2), '');
  assert.ok(result.stderr.includes(`textlint の依存が見つかりません: ${name}`));
});

test('tools に登録したリポジトリの textlint ルールとプリセットを適用する', (t) => {
  const root = setup(t, 'valid.md');
  fs.writeFileSync(path.join(root, 'valid.md'), '# 検査用語\n');
  const rule = 'textlint-rule-local-fixture';
  const preset = 'textlint-rule-preset-local-fixture';
  stubPackage(root, rule, `
module.exports = ({ Syntax, report, RuleError, getSource }, options) => ({
  [Syntax.Str](node) {
    if (getSource(node).includes('検査用語')) {
      report(node, new RuleError(options.message ?? '固定語を検出しました'));
    }
  },
});
`);
  stubPackage(root, preset, `
const rule = require('${rule}');
module.exports = {
  rules: { enabled: rule, disabled: rule },
  rulesConfig: { enabled: { message: 'プリセットが固定語を検出しました' }, disabled: false },
};
`);
  const args = ['check', '--format', 'json', '--fail-on', 'never'];
  const before = JSON.parse(expectCode(run(root, args), 0));
  assert.ok(!before.findings.some(({ message }) => message === '固定語を検出しました'));

  configureTools(root, rule, preset, 'unresolved-non-textlint-fixture');
  const after = JSON.parse(expectCode(run(root, args), 0));
  const findings = after.findings.filter(({ message }) => message.endsWith('固定語を検出しました'));
  assert.deepEqual(findings.map(({ ruleId }) => ruleId).sort(), [
    'textlint/local-fixture', 'textlint/preset-local-fixture/enabled',
  ]);
});

test('依存の解決は同梱版よりリポジトリ版を優先する', (t) => {
  const root = temp(t);
  const name = 'textlint-rule-preset-ja-technical-writing';
  assert.equal(resolvePackage(root, name).source, 'bundled');
  const local = stubPackage(root, name, 'module.exports = {};');
  const { source, file } = resolvePackage(root, name);
  assert.equal(source, 'repo');
  assert.equal(file, fs.realpathSync(local));
});

test('textlint の情報メッセージとルール ID がないメッセージを警告として出力する', (t) => {
  const root = setup(t, 'valid.md');
  stubPackage(root, '@textlint/kernel', `
exports.TextlintKernel = class {
  async lintText() {
    return { messages: [
      { ruleId: 'info', severity: 0, message: 'info' },
      { severity: 1, message: 'unknown' },
    ] };
  }
};
`);
  const result = run(root, ['check', '--format', 'json', '--fail-on', 'warning']);
  const { findings } = JSON.parse(expectCode(result, 1));
  assert.deepEqual(findings.filter(({ source }) => source.evaluator === 'textlint')
    .map(({ ruleId, severity }) => [ruleId, severity]), [
    ['textlint/info', 'warning'], ['textlint/unknown', 'warning'],
  ]);
});

test('Mermaid の構文エラーを Markdown の行に換算する', (t) => {
  const root = setup(t, 'invalid-mermaid.md');
  const output = expectCode(run(root, ['check']), 1);
  assert.match(output, /invalid-mermaid\.md:5:1 {2}error {2}mermaid\/syntax/);
});

test('PlantUML の構文エラーを Markdown の行に換算する', (t) => {
  const root = setup(t, 'invalid-plantuml.md');
  const output = expectCode(run(root, ['check']), 1);
  assert.match(output, /invalid-plantuml\.md:5:1 {2}error {2}plantuml\/syntax/);
});

test('PlantUML の読み込み時と構文検査時のログを標準エラーへ送る', (t) => {
  const root = setup(t, 'invalid-plantuml.md');
  const entry = stubPackage(root, '@plantuml/mcp-js', '');
  fs.writeFileSync(path.join(path.dirname(entry), 'engine.js'), `
process.stdout.write('engine startup\\n');
exports.checkSyntax = () => {
  process.stdout.write('engine check\\n');
  return JSON.stringify({ valid: true });
};
`);
  const result = run(root, ['check', '--format', 'json']);
  assert.equal(JSON.parse(expectCode(result, 0)).summary.filesChecked, 1);
  assert.match(result.stderr, /engine startup/);
  assert.match(result.stderr, /engine check/);
});

test('markdownlint の標準出力への診断が JSON に混ざらない', (t) => {
  const root = setup(t, 'valid.md');
  stubPackage(root, 'markdownlint', `
process.stdout.write('markdownlint startup\\n');
exports.sync = () => {
  process.stdout.write('markdownlint check\\n');
  return { document: [] };
};
`);

  const result = run(root, ['check', '--format', 'json']);
  assert.equal(JSON.parse(expectCode(result, 0)).summary.filesChecked, 1);
  assert.match(result.stderr, /markdownlint startup/);
  assert.match(result.stderr, /markdownlint check/);
});

test('JSON と SARIF に指摘の位置が入る', (t) => {
  const root = setup(t, 'invalid-plantuml.md');
  const json = JSON.parse(expectCode(run(root, ['check', '--format', 'json']), 1));
  const { file, line, column, severity, ruleId, message, source } =
    json.findings.find(({ ruleId }) => ruleId === 'plantuml/syntax');
  assert.equal(json.version, 1);
  assert.deepEqual(json.tool, { name: 'doc-lint', version: require('../package.json').version });
  assert.equal(json.summary.filesChecked, 1);
  assert.equal(json.summary.errors, 1);
  assert.equal(file, 'invalid-plantuml.md');
  assert.equal(line, 5);
  assert.equal(column, 1);
  assert.equal(severity, 'error');
  assert.ok(message);
  assert.deepEqual(source, { evaluator: 'plantuml', rule: 'syntax' });

  const output = run(root, ['check', '--format', 'sarif', '--output', 'result.sarif']);
  assert.equal(expectCode(output, 1), '');
  const sarif = JSON.parse(read(root, 'result.sarif'));
  assert.equal(sarif.version, '2.1.0');
  assert.equal(sarif.runs[0].tool.driver.name, 'doc-lint');
  const result = sarif.runs[0].results.find((entry) => entry.ruleId === ruleId);
  assert.equal(result.level, severity);
  assert.deepEqual(result.locations[0].physicalLocation, {
    artifactLocation: { uri: file },
    region: { startLine: line, startColumn: column },
  });
});

test('設定が無ければ init を案内して終了コード 2 を返す', (t) => {
  const result = run(temp(t), ['check']);
  expectCode(result, 2);
  assert.match(result.stderr, /doc-lint init/);
});

function npmStub(root) {
  const bin = path.join(root, 'stub');
  fs.mkdirSync(bin);
  fs.writeFileSync(path.join(bin, 'npm-stub.js'), `
const fs = require('node:fs');
const args = process.argv.slice(2);
fs.writeFileSync('npm-args.json', JSON.stringify(args));
if (process.env.DOC_LINT_NPM_FAIL) process.exit(7);
const pkg = JSON.parse(fs.readFileSync('package.json'));
if (args[1] === '--save-dev') pkg.devDependencies[args[2]] = '^1.0.0';
if (args[0] === 'uninstall') delete pkg.devDependencies[args[1]];
fs.writeFileSync('package.json', JSON.stringify(pkg));
`);
  if (process.platform === 'win32') {
    fs.writeFileSync(path.join(bin, 'npm.cmd'), '@node "%~dp0npm-stub.js" %*\r\n');
  } else {
    fs.writeFileSync(path.join(bin, 'npm'), '#!/bin/sh\nexec node "$(dirname "$0")/npm-stub.js" "$@"\n', { mode: 0o755 });
  }
  return { ...process.env, PATH: `${bin}${path.delimiter}${process.env.PATH}` };
}

test('tools add/remove は npm の成功後に設定を更新する', (t) => {
  const root = setup(t);
  const env = npmStub(root);
  const subdir = fs.mkdirSync(path.join(root, 'docs'), { recursive: true });
  expectCode(run(subdir, ['tools', 'add', '@sample/rule'], env), 0);
  assert.deepEqual(JSON.parse(read(root, 'npm-args.json')), ['install', '--save-dev', '@sample/rule']);
  const afterAdd = read(root, configPath);
  assert.match(afterAdd, /- "@sample\/rule"/);
  assert.equal(manifest(root).devDependencies['@sample/rule'], '^1.0.0');

  expectCode(run(root, ['tools', 'remove', '@sample/rule'], { ...env, DOC_LINT_NPM_FAIL: '1' }), 2);
  assert.equal(read(root, configPath), afterAdd);
  expectCode(run(root, ['tools', 'remove', '@sample/rule'], env), 0);
  assert.deepEqual(JSON.parse(read(root, 'npm-args.json')), ['uninstall', '@sample/rule']);
  assert.match(read(root, configPath), /tools: \[\]/);
  assert.equal(manifest(root).devDependencies['@sample/rule'], undefined);
});

test('install は通常のインストールと npm ci を切り替える', (t) => {
  const root = setup(t);
  const env = npmStub(root);
  expectCode(run(root, ['install'], env), 0);
  assert.deepEqual(JSON.parse(read(root, 'npm-args.json')), ['install']);
  expectCode(run(root, ['install', '--frozen-lockfile'], env), 0);
  assert.deepEqual(JSON.parse(read(root, 'npm-args.json')), ['ci']);
});

test('doctor が全評価器の起動を検査する', () => {
  const output = expectCode(run(here, ['doctor']), 0);
  for (const name of ['textlint', 'markdownlint', 'mermaid', 'plantuml']) {
    assert.match(output, new RegExp(`^OK ${name}$`, 'm'));
  }
});

test('check は対象から設定を探し、除外ディレクトリを走査しない', (t) => {
  const root = setup(t);
  const config = read(root, configPath).replaceAll(': true', ': false');
  fs.writeFileSync(path.join(root, configPath), config);
  for (const directory of ['docs', '.git', 'node_modules', '.doc-lint']) {
    const folder = path.join(root, directory);
    fs.mkdirSync(folder, { recursive: true });
    fs.writeFileSync(path.join(folder, 'sample.md'), '# Sample\n');
  }
  const result = run(path.dirname(root), ['check', root, '--format', 'json']);
  assert.equal(JSON.parse(expectCode(result, 0)).summary.filesChecked, 1);
  const nested = run(path.join(root, 'docs'), ['check', 'sample.md', '--output', 'report.json', '--format', 'json']);
  assert.equal(expectCode(nested, 0), '');
  assert.equal(JSON.parse(read(root, 'report.json')).summary.filesChecked, 1);
});

test('--output はリポジトリルート外への相対パスと絶対パスを拒否する', (t) => {
  const root = setup(t, 'valid.md');
  const outside = path.join(temp(t), 'result.json');
  for (const output of [path.relative(root, outside), outside]) {
    const result = run(root, ['check', '--format', 'json', '--output', output]);
    assert.equal(expectCode(result, 2), '');
    assert.match(result.stderr, /--output/);
    assert.ok(!fs.existsSync(outside));
  }
});

test('--output はシンボリックリンク経由のリポジトリルート外への書き込みを拒否する', (t) => {
  const root = setup(t, 'valid.md');
  const outside = temp(t);
  fs.writeFileSync(path.join(outside, 'existing.json'), 'original');
  fs.symlinkSync(outside, path.join(root, 'linked-dir'), 'junction');
  const outputs = ['linked-dir/new.json'];
  if (process.platform !== 'win32') {
    fs.symlinkSync(path.join(outside, 'existing.json'), path.join(root, 'linked.json'));
    outputs.push('linked.json');
  }
  for (const output of outputs) {
    const result = run(root, ['check', '--format', 'json', '--output', output]);
    assert.equal(expectCode(result, 2), '');
    assert.match(result.stderr, /--output/);
  }
  assert.ok(!fs.existsSync(path.join(outside, 'new.json')));
  assert.equal(read(outside, 'existing.json'), 'original');
});
