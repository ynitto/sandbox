// CLI を子プロセスで起動し、模造の Figma API に対して通しで確かめる。
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, before, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { FILE_KEY, startMock, TOKEN } from './helpers/mock-figma.mjs';

const CLI = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'scripts', 'figma_cli.mjs');
const URL_ = `https://www.figma.com/design/${FILE_KEY}/Login-App?node-id=1-2&t=share-secret`;

function tmp(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `figma-use-${prefix}-`));
}

function run(args, { apiBase, env = {}, cwd } = {}) {
  const work = cwd || tmp('cwd');
  const fullEnv = {
    PATH: process.env.PATH,
    HOME: work,
    SKILL_CONNECTIONS_FILE: path.join(work, 'no-connections.yaml'),
    FIGMA_USE_CACHE_DIR: path.join(work, 'cache'),
    FIGMA_USE_BACKOFF_MS: '1',
    ...(apiBase ? { FIGMA_API_BASE: apiBase, FIGMA_TOKEN: TOKEN } : {}),
    ...env,
  };
  return new Promise((resolve) => {
    execFile(process.execPath, [CLI, ...args], { cwd: work, env: fullEnv }, (error, stdout, stderr) => {
      resolve({ code: error ? error.code : 0, stdout, stderr, cwd: work });
    });
  });
}

function allFiles(dir) {
  return fs.readdirSync(dir, { recursive: true, withFileTypes: true }).filter((e) => e.isFile()).map((e) => path.join(e.parentPath ?? e.path, e.name));
}

function assertNoToken(...texts) {
  for (const t of texts) assert.ok(!String(t).includes(TOKEN), 'トークンが出力に含まれている');
}

let mock;
before(async () => {
  mock = await startMock();
});
after(async () => {
  await mock.close();
});

test('--help と各コマンドの --help が動く', async () => {
  const r = await run(['--help']);
  assert.equal(r.code, 0);
  for (const cmd of ['doctor', 'inspect', 'context', 'screenshot', 'assets', 'export']) {
    assert.match(r.stdout, new RegExp(cmd));
    const sub = await run([cmd, '--help']);
    assert.equal(sub.code, 0, cmd);
    assert.match(sub.stdout, /使い方/);
  }
});

test('doctor は認証情報が無いと 3 で終わり、あれば出所だけを示して値を出さない', async () => {
  const none = await run(['doctor']);
  assert.equal(none.code, 3);
  assert.match(none.stdout, /認証情報: 見つかりません/);
  const ok = await run(['doctor'], { env: { FIGMA_TOKEN: TOKEN } });
  assert.equal(ok.code, 0);
  assert.match(ok.stdout, /設定あり（環境変数 FIGMA_TOKEN）/);
  assertNoToken(ok.stdout, ok.stderr);
});

test('doctor --network は API を 1 回だけ呼んで疎通を示す', async () => {
  const before_ = mock.requests.length;
  const r = await run(['doctor', '--network', URL_], { apiBase: mock.apiBase });
  assert.equal(r.code, 0, r.stderr);
  assert.match(r.stdout, /API 疎通: 「Login App」/);
  assert.equal(mock.requests.length - before_, 1);
  assertNoToken(r.stdout, r.stderr);
});

test('connections.yaml の token（環境変数参照）で認証する', async () => {
  const work = tmp('conn');
  const conn = path.join(work, 'connections.yaml');
  fs.writeFileSync(conn, 'figma:\n  - label: default\n    token: ${MY_FIGMA}\n');
  const r = await run(['inspect', FILE_KEY], { cwd: work, env: { SKILL_CONNECTIONS_FILE: conn, MY_FIGMA: TOKEN, FIGMA_API_BASE: mock.apiBase } });
  assert.equal(r.code, 0, r.stderr);
  assert.match(r.stdout, /\[FRAME\] Login \(1:2\)/);
});

test('inspect はページとフレームを depth までで一覧にする', async () => {
  const r = await run(['inspect', FILE_KEY, '--depth', '2'], { apiBase: mock.apiBase });
  assert.equal(r.code, 0, r.stderr);
  assert.match(r.stdout, /\[CANVAS\] Screens \(0:1\)/);
  assert.match(r.stdout, /\[FRAME\] Home \(1:9\)/);
  assert.doesNotMatch(r.stdout, /\(1:3\)/);
  const json = await run(['inspect', URL_, '--depth', '1', '--json'], { apiBase: mock.apiBase });
  const parsed = JSON.parse(json.stdout);
  assert.equal(parsed.roots[0].id, '1:2');
  assert.equal(parsed.roots[0].children[0].id, '1:3');
});

test('export でモックから成果物一式を作り、トークンをどこにも書かない', async () => {
  const work = tmp('export');
  const r = await run(['export', URL_, '--node', '1:9', '--svg', '1-6', '--out', 'output'], { apiBase: mock.apiBase, cwd: work });
  assert.equal(r.code, 0, r.stderr);
  const out = path.join(work, 'output');
  for (const rel of ['manifest.json', 'design-summary.md', 'missing-information.md', 'raw/nodes-1-2.json', 'normalized/1-2.json', 'normalized/1-2.md', 'normalized/1-9.md', 'screenshots/1-2.png', 'screenshots/1-9.png', 'assets/assets-map.json', 'assets/images/abc123imageref.jpg', 'assets/svg/icon_close--1-6.svg']) {
    assert.ok(fs.existsSync(path.join(out, rel)), `${rel} がない`);
  }
  const manifest = JSON.parse(fs.readFileSync(path.join(out, 'manifest.json'), 'utf8'));
  assert.equal(manifest.status, 'complete');
  assert.equal(manifest.source.fileKey, FILE_KEY);
  assert.deepEqual(manifest.source.nodeIds, ['1:2', '1:9']);
  assert.equal(manifest.file.version, '1234567890');
  assert.equal(manifest.cliVersion, '1.0.0');
  assert.doesNotMatch(manifest.source.url, /share-secret/);
  assert.equal(manifest.artifacts.byNode['1:2'].screenshot, 'screenshots/1-2.png');
  const map = JSON.parse(fs.readFileSync(path.join(out, 'assets/assets-map.json'), 'utf8'));
  assert.deepEqual(map.imageFills[0], { imageRef: 'abc123imageref', nodeIds: ['1:5'], scaleMode: 'FILL', file: 'assets/images/abc123imageref.jpg' });
  assert.equal(map.svg[0].nodeId, '1:6');
  const summary = fs.readFileSync(path.join(out, 'design-summary.md'), 'utf8');
  assert.match(summary, /## 事実（Figma API 由来）/);
  assert.match(summary, /## 判断（このファイルには書かない）/);
  const missing = fs.readFileSync(path.join(out, 'missing-information.md'), 'utf8');
  assert.match(missing, /## 未解決/);
  assert.match(missing, /API では取れない情報/);
  // 画像のダウンロードにはトークンを送っていない。
  const downloads = mock.requests.filter((q) => q.path.startsWith('/render/') || q.path.startsWith('/fill/'));
  assert.ok(downloads.length >= 3);
  assert.ok(downloads.every((q) => !('x-figma-token' in q.headers)));
  for (const f of allFiles(out)) assertNoToken(fs.readFileSync(f));
  for (const f of allFiles(path.join(work, 'cache'))) assertNoToken(fs.readFileSync(f));
  assertNoToken(r.stdout, r.stderr);
});

test('2 回目はキャッシュを使い、nodes と保存済みの画像に API を呼ばない。--refresh で取り直す', async () => {
  const work = tmp('cache');
  await run(['export', URL_, '--out', 'o1'], { apiBase: mock.apiBase, cwd: work });
  const mark = mock.requests.length;
  const second = await run(['export', URL_, '--out', 'o2'], { apiBase: mock.apiBase, cwd: work });
  assert.equal(second.code, 0, second.stderr);
  const calls = mock.requests.slice(mark).map((q) => q.path);
  assert.deepEqual(calls, []);
  assert.match(second.stderr, /キャッシュを使用: .*version 1234567890/);
  const manifest = JSON.parse(fs.readFileSync(path.join(work, 'o2', 'manifest.json'), 'utf8'));
  assert.equal(manifest.cache.used, true);
  const mark2 = mock.requests.length;
  await run(['export', URL_, '--out', 'o3', '--refresh'], { apiBase: mock.apiBase, cwd: work });
  assert.ok(mock.requests.slice(mark2).some((q) => q.path.endsWith('/nodes')));
});

test('--check-update は version が変わっていればキャッシュを使わない', async () => {
  const work = tmp('check');
  await run(['context', URL_, '--out', 'o1'], { apiBase: mock.apiBase, cwd: work });
  const changed = await startMock({ latestVersion: '999' });
  try {
    // 同じキャッシュを、別ポートの API で読む（キャッシュのキーはパスと問い合わせだけ）。
    const r = await run(['context', URL_, '--out', 'o2', '--check-update'], { apiBase: changed.apiBase, cwd: work });
    assert.equal(r.code, 0, r.stderr);
    assert.match(r.stderr, /取り直します/);
    assert.ok(changed.requests.some((q) => q.path.endsWith('/meta')));
    assert.ok(changed.requests.some((q) => q.path.endsWith('/nodes')));
  } finally {
    await changed.close();
  }
});

test('レンダリング失敗・ダウンロード失敗・存在しないノードは部分失敗（終了コード 6）として残す', async () => {
  const broken = await startMock({ nullRender: ['1:9'], brokenDownload: ['1:2'] });
  try {
    const work = tmp('partial');
    const r = await run(['export', URL_, '--node', '1:9', '--node', '77:77', '--out', 'out', '--retries', '0'], { apiBase: broken.apiBase, cwd: work });
    assert.equal(r.code, 6, r.stderr);
    const manifest = JSON.parse(fs.readFileSync(path.join(work, 'out', 'manifest.json'), 'utf8'));
    assert.equal(manifest.status, 'partial');
    const targets = manifest.failures.map((f) => `${f.target}:${f.stage}`);
    assert.ok(targets.includes('77:77:nodes'));
    assert.ok(targets.includes('1:9:render-png'));
    assert.ok(targets.includes('1:2:download-png'));
    assert.match(fs.readFileSync(path.join(work, 'out', 'missing-information.md'), 'utf8'), /取得失敗/);
  } finally {
    await broken.close();
  }
});

test('429 は Retry-After に従って再試行し、成功すれば成功扱い', async () => {
  const limited = await startMock({ failures: { [`/v1/files/${FILE_KEY}/nodes`]: [{ status: 429, headers: { 'retry-after': '0' } }] } });
  try {
    const r = await run(['context', URL_, '--out', 'o'], { apiBase: limited.apiBase });
    assert.equal(r.code, 0, r.stderr);
    assert.match(r.stderr, /HTTP 429/);
    assert.equal(limited.requests.filter((q) => q.path.endsWith('/nodes')).length, 2);
  } finally {
    await limited.close();
  }
});

test('再試行し尽くした 429 は終了コード 5、無効なトークンは 3、無いファイルは 4', async () => {
  const limited = await startMock({ failures: { '/v1/': Array(5).fill({ status: 429, headers: { 'retry-after': '0' } }) } });
  try {
    const r = await run(['context', URL_, '--retries', '1'], { apiBase: limited.apiBase });
    assert.equal(r.code, 5, r.stderr);
    assert.match(r.stderr, /レート制限/);
    const inspectRes = await run(['inspect', FILE_KEY, '--retries', '1'], { apiBase: limited.apiBase });
    assert.equal(inspectRes.code, 5, inspectRes.stderr);
  } finally {
    await limited.close();
  }
  const bad = await run(['inspect', FILE_KEY], { apiBase: mock.apiBase, env: { FIGMA_TOKEN: 'wrong' } });
  assert.equal(bad.code, 3);
  assert.match(bad.stderr, /対処: /);
  const missing = await run(['inspect', 'ZZZZZZZZZZZZZZZZZZZZZZ'], { apiBase: mock.apiBase });
  assert.equal(missing.code, 4);
  assertNoToken(bad.stderr, missing.stderr);
});

test('Figma Make の URL や node 未指定は引数の誤り（終了コード 2）として拒否する', async () => {
  const make = await run(['export', `https://www.figma.com/make/${FILE_KEY}/x`], { apiBase: mock.apiBase });
  assert.equal(make.code, 2);
  assert.match(make.stderr, /Figma Make/);
  const noNode = await run(['export', FILE_KEY], { apiBase: mock.apiBase });
  assert.equal(noNode.code, 2);
  assert.match(noNode.stderr, /inspect/);
  const badFlag = await run(['export', FILE_KEY, '--token', 'x']);
  assert.equal(badFlag.code, 2);
});

test('--variables は 403 ならバインド ID を残してアクセス不可と記録し、許可されていれば値を解決する', async () => {
  const work = tmp('vars');
  const denied = await run(['export', URL_, '--variables', '--out', 'denied'], { apiBase: mock.apiBase, cwd: work });
  assert.equal(denied.code, 0, denied.stderr);
  assert.match(fs.readFileSync(path.join(work, 'denied', 'missing-information.md'), 'utf8'), /## アクセス不可/);
  const allowed = await startMock({ variables: true });
  try {
    const ok = await run(['export', URL_, '--variables', '--out', 'ok', '--refresh'], { apiBase: allowed.apiBase, cwd: work });
    assert.equal(ok.code, 0, ok.stderr);
    const md = fs.readFileSync(path.join(work, 'ok', 'normalized', '1-2.md'), 'utf8');
    assert.match(md, /color\/primary/);
    assert.ok(fs.existsSync(path.join(work, 'ok', 'raw', 'variables-local.json')));
  } finally {
    await allowed.close();
  }
});

test('Code Connect のファイルからコンポーネントの対応を読む', async () => {
  const work = tmp('cc');
  fs.mkdirSync(path.join(work, 'src'), { recursive: true });
  fs.writeFileSync(path.join(work, 'src', 'Button.figma.tsx'), `import figma from '@figma/code-connect'\nimport { Button } from './Button'\nfigma.connect(Button, "https://www.figma.com/design/${FILE_KEY}/App?node-id=5-0", {})\n`);
  const r = await run(['context', URL_, '--code-connect-dir', 'src', '--out', 'o'], { apiBase: mock.apiBase, cwd: work });
  assert.equal(r.code, 0, r.stderr);
  assert.match(fs.readFileSync(path.join(work, 'o', 'normalized', '1-2.md'), 'utf8'), /Button（code-connect）/);
});
