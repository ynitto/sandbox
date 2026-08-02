'use strict';

// git URL 正規形のゴールデン（P2-5）。同じ規則の実装が 2 つある——Python の
// `agentcore.repolocal.normalize_repo_url` と、UI の応答性のために dashboard が持つ
// `nodeRepos.normalizeRepoUrl`。**同じ URL が経路によって違って読める**と、
// 「host.yaml に宣言したのにローカルクローンが使われない（＝速度が出ない）」が
// 理由の分からない形で出る。
//
// 期待値は Python 側のテーブル（tools/agent-tools/agentcore/tests/test_repolocal.py の
// NORMALIZE_GOLDEN）と同じものを置き、片方を直したらもう片方が落ちるようにしてある。
//
// 追加依存なしで `node test/repo-url-golden.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const nodeRepos = require('../src/features/agent-project/main/nodeRepos');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const GOLDEN = [
  ['https://h/a/b.git', 'https://h/a/b'],
  ['https://h/a/b/', 'https://h/a/b'],
  ['https://Example.com/A/B.git', 'https://example.com/a/b'],
  ['git@h:t/app.git', 'git@h:t/app'],
  ['git@H:T/App', 'git@h:t/app'],
  ['h:t/app.git', 'h:t/app'],
  ['ssh://git@h:22/t/app.git', 'ssh://git@h:22/t/app'],
  ['', ''],
  ['   ', ''],
];

test('URL 正規形が Python 側と一致する', () => {
  for (const [raw, want] of GOLDEN) {
    assert.strictEqual(nodeRepos.normalizeRepoUrl(raw), want, `入力: ${JSON.stringify(raw)}`);
  }
});

test('ローカルパスは symlink まで解決する（Python の Path.resolve と同じ）', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-repourl-'));
  try {
    const real = path.join(tmp, 'real');
    const link = path.join(tmp, 'link');
    fs.mkdirSync(real);
    try {
      fs.symlinkSync(real, link, 'dir');
    } catch {
      console.log('- skip - この環境では symlink を作れない');
      return;
    }
    assert.ok(nodeRepos.sameRepo(real, link),
      'symlink 経由で宣言した同じクローンが別リポジトリに見えてはいけない');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('実体の無いローカルパスは絶対化だけする（resolve(strict=False) と同じ）', () => {
  const missing = path.join(os.tmpdir(), 'kpv-does-not-exist-12345', 'repo');
  assert.strictEqual(nodeRepos.normalizeRepoUrl(missing), missing.toLowerCase());
  assert.ok(nodeRepos.sameRepo(missing, `${missing}.git`));
});

console.log(`\n${passed} tests passed (repo-url-golden)`);
