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

test('依頼文に組み込みのテスト設計の観点が入り、外すと元の 1 行に戻る', () => {
  const p = buildPrompt({ conditions: 'c' });
  assert.match(p, /## テスト設計の観点/);
  assert.match(p, /境界ちょうどと 1 つ外側/);
  assert.match(p, /下の観点に沿って決める/);
  assert.ok(p.indexOf('## テスト設計の観点') < p.indexOf('# テストケースファイルの書式'), '観点は書式の前に置く');
  const off = buildPrompt({ conditions: 'c', defaultGuide: false });
  assert.doesNotMatch(off, /## テスト設計の観点/);
  assert.match(off, /異常系・境界値のケースも作る/);
});

test('prompt: 設定の generate.guide と --guide の観点を足し、--no-default-guide で組み込みを外す', async (t) => {
  const dir = tmpDir(t);
  fs.mkdirSync(path.join(dir, 'docs'));
  fs.writeFileSync(path.join(dir, 'docs', 'team.md'), '金額は 0 円と上限 999,999 円を必ず試す\n');
  fs.writeFileSync(path.join(dir, 'extra.md'), '管理者と一般利用者の両方で確かめる\n');
  fs.writeFileSync(path.join(dir, 'webui-test.config.yaml'), 'envs:\n  local: {}\ngenerate:\n  guide: docs/team.md\n');
  const r = await cli(['prompt', '条件', '--guide', 'extra.md'], { cwd: dir });
  assert.strictEqual(r.code, 0, r.err);
  assert.match(r.out, /## テスト設計の観点/);
  assert.match(r.out, /## 追加の観点（docs[\\/]team\.md）\n金額は 0 円/);
  assert.match(r.out, /## 追加の観点（extra\.md）\n管理者と一般利用者/);
  assert.ok(r.out.indexOf('team.md') < r.out.indexOf('extra.md'), '設定の観点が先');
  const off = await cli(['prompt', '条件', '--no-default-guide'], { cwd: dir });
  assert.doesNotMatch(off.out, /## テスト設計の観点/);
  assert.match(off.out, /## 追加の観点（docs[\\/]team\.md）/);
});

test('prompt: 観点の設定の誤りと、無いファイルを知らせる', async (t) => {
  const dir = tmpDir(t);
  fs.writeFileSync(path.join(dir, 'webui-test.config.yaml'), 'envs:\n  local: {}\ngenerate:\n  guides: a.md\n  defaultGuide: "no"\n');
  const bad = await cli(['prompt', '条件'], { cwd: dir });
  assert.notStrictEqual(bad.code, 0);
  assert.match(bad.err, /知らないキー「guides」/);
  assert.match(bad.err, /generate\.defaultGuide: true か false/);
  fs.writeFileSync(path.join(dir, 'webui-test.config.yaml'), 'envs:\n  local: {}\n');
  const missing = await cli(['prompt', '条件', '--guide', 'nothing.md'], { cwd: dir });
  assert.strictEqual(missing.code, 2);
  assert.match(missing.err, /観点のファイルがありません/);
});

test('generate: --guide の観点をエージェントへの依頼に入れる', async (t) => {
  const dir = tmpDir(t);
  const log = path.join(dir, 'log.txt');
  fs.writeFileSync(path.join(dir, 'g.md'), '退会の確認ダイアログを必ず確かめる\n');
  process.env.FAKE_AGENT_LOG = log;
  t.after(() => { delete process.env.FAKE_AGENT_LOG; });
  const r = await cli(['generate', '条件', '-o', path.join(dir, 'g.yaml'), '--agent-cmd', FAKE, '--guide', 'g.md'], { cwd: dir });
  assert.strictEqual(r.code, 0, r.err);
  assert.match(fs.readFileSync(log, 'utf8'), /## 追加の観点（g\.md）\n退会の確認ダイアログ/);
});

test('generate: エージェントの出力を検査して保存し、baseUrl を補う', async (t) => {
  const dir = tmpDir(t);
  const out = path.join(dir, 'gen.yaml');
  const r = await cli(['generate', 'ログイン画面が出ること', '-o', out, '--agent-cmd', FAKE, '--base-url', 'http://example.test'], { cwd: dir });
  assert.strictEqual(r.code, 0, r.err);
  const text = fs.readFileSync(out, 'utf8');
  assert.match(text, /^# webui-test generate で作成/);
  assert.match(text, /baseUrl: http:\/\/example.test/);
  assert.ok(!fs.existsSync(path.join(dir, '.webui-test')), '依頼ファイルの置き場を片付ける');
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

test('generate --explore: playwright-cli のブラウザを開いてエージェントに操作させ、終わったら閉じる', async (t) => {
  const app = await startSampleApp();
  t.after(app.close);
  const dir = tmpDir(t);
  const log = path.join(dir, 'log.txt');
  process.env.FAKE_AGENT_MODE = 'explore';
  process.env.FAKE_AGENT_LOG = log;
  t.after(() => { delete process.env.FAKE_AGENT_MODE; delete process.env.FAKE_AGENT_LOG; });
  const ep = executablePath() ? ['--executable-path', executablePath()] : [];
  const r = await cli(['generate', 'ログインして一覧まで', '-o', path.join(dir, 'g.yaml'), '--agent-cmd', FAKE, '--url', app.baseUrl + '/', '--explore', '--no-snapshot', ...ep], { cwd: dir });
  assert.strictEqual(r.code, 0, r.err);
  const text = fs.readFileSync(log, 'utf8');
  assert.match(text, /## 画面を操作して確かめる/);
  assert.match(text, /SNAPSHOT:[\s\S]*textbox "メールアドレス"/);
  const session = /-s=(webui-test-[\w-]+)/.exec(text)[1];
  const { execFileSync } = require('child_process');
  const listed = execFileSync(process.execPath, [require.resolve('@playwright/cli/playwright-cli.js'), 'list'], { cwd: dir, encoding: 'utf8' });
  assert.ok(!listed.includes(session) || /closed/i.test(listed.split('\n').find((l) => l.includes(session)) || ''), `セッションが閉じていない: ${listed}`);
});
