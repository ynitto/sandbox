'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { extractYaml, splitCommand, buildPrompt } = require('../src/generate');
const { cli, tmpDir, startSampleApp, executablePath } = require('./helpers');

const FAKE = `node ${JSON.stringify(path.join(__dirname, 'fixtures', 'fake-agent.js'))}`;

test('出力から最後の yaml ブロックを取り出す（色の制御文字・Kiro の > 付きも）', () => {
  const out = '\x1b[1mメモ\x1b[0m\n```yaml\nfoo: 1\n```\n```yaml\n> suite: s\n> cases:\n>   - id: a\n```\n';
  const got = extractYaml(out);
  assert.strictEqual(got.data.suite, 's');
  assert.strictEqual(extractYaml('ただの文章'), null);
});

test('コマンド文字列を引用を保って分ける', () => {
  assert.deepStrictEqual(splitCommand(`node "a b.js" --x 'c d' ""`), ['node', 'a b.js', '--x', 'c d', '']);
});

test('依頼文に書式・条件・画面の要素一覧が入る', () => {
  const p = buildPrompt({ conditions: 'ログインを確かめる', url: 'http://h/login', pageInfo: { title: 'T', url: 'http://h/login', aria: '- button "ログイン"' } });
  assert.match(p, /## 条件\nログインを確かめる/);
  assert.match(p, /baseUrl: http:\/\/h/);
  assert.match(p, /- button "ログイン"/);
  assert.match(p, /# テストケースファイルの書式/);
});

test('generate: エージェントの出力を検査して保存し、baseUrl を補う', async (t) => {
  const dir = tmpDir(t);
  const out = path.join(dir, 'gen.yaml');
  const r = await cli(['generate', 'ログイン画面が出ること', '-o', out, '--agent-cmd', FAKE, '--base-url', 'http://example.test'], { cwd: dir });
  assert.strictEqual(r.code, 0, r.err);
  const text = fs.readFileSync(out, 'utf8');
  assert.match(text, /^# web-test generate で作成/);
  assert.match(text, /baseUrl: http:\/\/example.test/);
  assert.ok(!fs.existsSync(path.join(dir, '.web-test')), '依頼ファイルの置き場を片付ける');
});

test('generate: 書式の誤りはエラーを添えて頼み直す', async (t) => {
  const dir = tmpDir(t);
  const log = path.join(dir, 'log.txt');
  process.env.FAKE_AGENT_MODE = 'bad-then-good';
  process.env.FAKE_AGENT_LOG = log;
  t.after(() => { delete process.env.FAKE_AGENT_MODE; delete process.env.FAKE_AGENT_LOG; });
  const r = await cli(['generate', '条件', '-o', path.join(dir, 'g.yaml'), '--agent-cmd', FAKE], { cwd: dir });
  assert.strictEqual(r.code, 0, r.err);
  const prompts = fs.readFileSync(log, 'utf8').split('\n=====\n').filter(Boolean);
  assert.strictEqual(prompts.length, 2);
  assert.match(prompts[1], /前回の出力の問題[\s\S]*知らないキー「tap」/);
});

test('generate: 直らなければ保存せずに失敗する', async (t) => {
  const dir = tmpDir(t);
  process.env.FAKE_AGENT_MODE = 'always-bad';
  t.after(() => { delete process.env.FAKE_AGENT_MODE; });
  const out = path.join(dir, 'g.yaml');
  const r = await cli(['generate', '条件', '-o', out, '--agent-cmd', FAKE, '--retries', '0'], { cwd: dir });
  assert.strictEqual(r.code, 1);
  assert.match(r.err, /書式に合いませんでした/);
  assert.ok(!fs.existsSync(out));
});

test('generate --url: 画面の要素一覧を依頼に入れ、作ったケースがそのまま通る', async (t) => {
  const app = await startSampleApp();
  t.after(app.close);
  const dir = tmpDir(t);
  const log = path.join(dir, 'log.txt');
  process.env.FAKE_AGENT_LOG = log;
  t.after(() => { delete process.env.FAKE_AGENT_LOG; });
  const out = path.join(dir, 'g.yaml');
  const ep = executablePath() ? ['--executable-path', executablePath()] : [];
  const r = await cli(['generate', 'ログイン画面', '-o', out, '--agent-cmd', FAKE, '--url', app.baseUrl + '/', ...ep], { cwd: dir });
  assert.strictEqual(r.code, 0, r.err);
  assert.match(fs.readFileSync(log, 'utf8'), /textbox "メールアドレス"/);
  const run = await cli(['run', out, '--out', path.join(dir, 'res'), ...ep], { cwd: dir });
  assert.strictEqual(run.code, 0, run.err);
});
