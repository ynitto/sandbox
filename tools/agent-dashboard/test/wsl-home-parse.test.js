'use strict';

// WSL ホーム解決の出力読み取り（`base/main/wsl.js` の extractWindowsPath）。
// 追加依存なしで `node test/wsl-home-parse.test.js` で走る。
//
// 背景（実際に起きた不具合）: ホームの場所はディストロのユーザー設定次第なので、
// 推測せず `wsl.exe … sh -lc 'wslpath -w "$HOME/.agents"'` に聞いている。この出力を
// **先頭 1 行で決め打ち**し、パス形式でなければ捨てていた。ログインシェル（-lc）は
// プロファイルを読むので、標準出力にはコマンドの結果より前に初期化メッセージが混ざる
// （motd の転載・nvm/conda のバナー・.profile の echo・更新の案内）。そういう端末では
// ホームの解決が丸ごと失敗し、ローカルの ~/.agents へ落ちる。engine/status.json は
// プロジェクト発見の唯一の根拠なので、読めない＝1 件も無い、と同じ見え方になる——
// **画面からプロジェクト一覧が消える**。しかも原因はバナーを出す設定を入れた日であって、
// dashboard 側の変更とは何の関係もない。
//
// 直し方は「全行からパス行だけを拾う」。ここではその読み取りを固定する。

const assert = require('assert');
const { extractWindowsPath } = require('../src/base/main/wsl');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('素直な出力（1 行だけ）はそのまま採る', () => {
  assert.strictEqual(extractWindowsPath('\\\\wsl.localhost\\Ubuntu\\home\\me\\.agents\n'),
    '\\\\wsl.localhost\\Ubuntu\\home\\me\\.agents');
  assert.strictEqual(extractWindowsPath('C:\\Users\\me\\.agents'), 'C:\\Users\\me\\.agents');
});

test('シェル初期化メッセージが混ざってもパス行を拾う（この不具合の本体）', () => {
  const stdout = [
    'Welcome to Ubuntu 24.04.1 LTS (GNU/Linux 5.15.167.4-microsoft-standard-WSL2 x86_64)',
    '',
    ' * Documentation:  https://help.ubuntu.com',
    'nvm is not compatible with the "npm config" setting',
    '',
    '\\\\wsl.localhost\\Ubuntu\\home\\me\\.agents',
  ].join('\r\n');
  assert.strictEqual(extractWindowsPath(stdout), '\\\\wsl.localhost\\Ubuntu\\home\\me\\.agents');
});

test('前後の空白と CRLF を落とす', () => {
  assert.strictEqual(extractWindowsPath('  \r\n\t\\\\wsl$\\Ubuntu\\home\\me\\.agents  \r\n'),
    '\\\\wsl$\\Ubuntu\\home\\me\\.agents');
});

test('パスらしき行が複数あれば最後を採る（コマンドの結果は最後に来る）', () => {
  // バナーが Windows のパスを含むことはある（インストーラの案内など）。
  // プロファイルの出力は先、目当ての wslpath の結果は後。
  const stdout = [
    'ヒント: C:\\Program Files\\WSL\\wsl.exe を更新してください',
    '\\\\wsl.localhost\\Ubuntu\\home\\me\\.agents',
  ].join('\n');
  assert.strictEqual(extractWindowsPath(stdout), '\\\\wsl.localhost\\Ubuntu\\home\\me\\.agents');
});

test('パス行が無ければ空（呼び出し側はローカルの ~ へ落とす）', () => {
  assert.strictEqual(extractWindowsPath('wslpath: command not found'), '');
  assert.strictEqual(extractWindowsPath('/home/me/.agents'), '', 'POSIX パスは採らない');
  assert.strictEqual(extractWindowsPath(''), '');
  assert.strictEqual(extractWindowsPath(null), '');
  assert.strictEqual(extractWindowsPath(undefined), '');
});

test('ホーム解決は全行走査を通す（先頭行の決め打ちに戻さない）', () => {
  const fs = require('fs');
  const path = require('path');
  for (const rel of [
    ['src', 'features', 'agent-project', 'main', 'engine.js'],
    ['src', 'features', 'agent-project', 'main', 'project.js'],
  ]) {
    const src = fs.readFileSync(path.join(__dirname, '..', ...rel), 'utf8');
    assert.ok(src.includes('extractWindowsPath'), `${rel.join('/')} が共通の読み取りを使う`);
    assert.ok(!/split\(\/\\r\?\\n\/\)\[0\]/.test(src), `${rel.join('/')} に先頭行の決め打ちが残っている`);
  }
});

console.log(`\n${passed} tests passed`);
