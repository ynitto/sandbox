'use strict';

// 自動 git pull の対象契約を検証する（エンジンの動いていないリモート PC で状況が更新されない
// 不具合の回帰防止）。以前は選択中プロジェクトの 1 件しか pull せず、別プロジェクト選択中や
// 未選択時にリモートの状態（サイドバー件数・要対応）が止まったままになっていた。
//   - maybeAutoGitPull: 発見済み全プロジェクトの状態ルート（p.root）を pull 対象に含める
//   - スロットリングは main 側 git.pull がリポジトリ toplevel 単位で行う（ここでは per-dir に
//     投げるだけでよい）— その契約が git.js に残っていることも確認する

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const renderer = require('./helpers/renderer-src').read();
const gitSrc = fs.readFileSync(path.join(__dirname, '..', 'src', 'base', 'main', 'git.js'), 'utf8');

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer に function ${name} が見つかりません`);
  let i = renderer.indexOf('{', at);
  let depth = 0;
  for (; i < renderer.length; i++) {
    if (renderer[i] === '{') depth++;
    else if (renderer[i] === '}') {
      depth--;
      if (depth === 0) return renderer.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

(async () => {
await test('maybeAutoGitPull は発見済み全プロジェクトの状態ルートを pull する', async () => {
  const pulled = [];
  // eslint-disable-next-line no-new-func
  const maybeAutoGitPull = new Function(
    'state', 'api', 'gitStateDir', 'toast',
    `let lastGitPullError = null; const maybeAutoGitPull = async ${grab('maybeAutoGitPull').replace('function maybeAutoGitPull', 'function')}; return maybeAutoGitPull;`
  )(
    {
      config: { projects: { gitPullSec: 300 } },
      discovery: {
        projects: [
          { dir: '/ws/app', root: '/ws/app-state', exists: true },
          { dir: '/ws/other', root: '/ws/other-agent-state/.agent-project', exists: true },
          { dir: '/ws/gone', root: '/ws/gone-state', exists: false }, // 実在しない登録は引かない
        ],
      },
    },
    { gitPull: async (dir) => { pulled.push(dir); return { skipped: false }; } },
    () => '/ws/app-state',                       // 選択中（重複はSetで畳まれる）
    () => {}
  );
  await maybeAutoGitPull();
  assert.deepStrictEqual(
    pulled.sort(),
    ['/ws/app-state', '/ws/other-agent-state/.agent-project'],
    '選択中+発見済み全件（exists:false 除く）を root 基準で pull する'
  );
});

await test('gitPullSec=0 は自動 pull を無効にする（設定どおり）', async () => {
  const pulled = [];
  // eslint-disable-next-line no-new-func
  const maybeAutoGitPull = new Function(
    'state', 'api', 'gitStateDir', 'toast',
    `let lastGitPullError = null; const maybeAutoGitPull = async ${grab('maybeAutoGitPull').replace('function maybeAutoGitPull', 'function')}; return maybeAutoGitPull;`
  )(
    { config: { projects: { gitPullSec: 0 } }, discovery: { projects: [{ root: '/x', exists: true }] } },
    { gitPull: async (dir) => { pulled.push(dir); } },
    () => '/sel',
    () => {}
  );
  await maybeAutoGitPull();
  assert.deepStrictEqual(pulled, []);
});

await test('git.pull は toplevel 単位で間隔スロットリングする（全件投げてよい根拠）', () => {
  assert.match(gitSrc, /lastRemoteAt\.get\(top\)/, 'リポジトリ単位の最終 pull 時刻で間引く');
  assert.match(gitSrc, /skipped:\s*true,\s*toplevel:\s*top/, '間隔内は skipped を返すだけ');
});

console.log(`\n${passed} passed`);
})().catch((e) => {
  console.error('FAILED:', e);
  process.exit(1);
});
