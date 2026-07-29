'use strict';

// WSL ディストロ名の解決（base/main/wsl.js）。
//
// 回帰の芯: `wsl --list --quiet` の出力エンコーディングを決め打つと、化けた文字列が
// そのままディストロ名として通り、`wsl.exe -d <化け文字>` が
// 「指定された名前のディストリビューションはありません。」で即死する（ウィンドウが
// 一瞬で開いて閉じる）。UNC 翻訳側では存在しないフォルダを指す。

const assert = require('assert');
const wsl = require('../src/base/main/wsl');

const tests = [];
function test(name, fn) { tests.push([name, fn]); }

const NUL = String.fromCharCode(0);
const LIST = 'Ubuntu-22.04\r\ndocker-desktop\r\ndocker-desktop-data\r\n';

test('UTF-16LE の一覧（wsl の既定）を名前へ解読する', () => {
  const names = wsl.parseWslDistroList(Buffer.from(LIST, 'utf16le'));
  assert.deepStrictEqual(names, ['Ubuntu-22.04', 'docker-desktop', 'docker-desktop-data']);
});

test('UTF-8 の一覧（WSL_UTF8=1 等）も名前へ解読する', () => {
  // ここが今回の不具合の芯。UTF-8 の出力を UTF-16LE として解くと
  // "Ubuntu-22.04" が "払湵畴㈭⸲㐰" のような化け文字列になり、そのまま -d へ渡っていた。
  const names = wsl.parseWslDistroList(Buffer.from(LIST, 'utf8'));
  assert.deepStrictEqual(names, ['Ubuntu-22.04', 'docker-desktop', 'docker-desktop-data']);
});

test('BOM 付きでも先頭のディストロ名を壊さない', () => {
  const names = wsl.parseWslDistroList(Buffer.from(`\uFEFF${LIST}`, 'utf16le'));
  assert.strictEqual(names[0], 'Ubuntu-22.04');
});

test('化けた出力・空出力からは名前を作らない（推測を -d へ渡さない）', () => {
  // 名前として成立しないものは全部落とす。呼び出し側は '' ＝ -d 無しへ倒れる。
  assert.deepStrictEqual(wsl.parseWslDistroList(Buffer.from('払湵畴㈭⸲㐰', 'utf8')), []);
  assert.deepStrictEqual(wsl.parseWslDistroList(Buffer.from('')), []);
  assert.deepStrictEqual(wsl.parseWslDistroList(null), []);
  // NUL 混じりの断片（奇数バイトで切れた出力など）も名前にしない
  assert.deepStrictEqual(wsl.parseWslDistroList(Buffer.from(`${NUL}${NUL}`, 'utf8')), []);
});

test('ディストロ名の判定は ASCII の登録名だけを通す', () => {
  const ok = ['Ubuntu', 'Ubuntu-22.04', 'openSUSE-Leap-15.5', 'kali-linux', 'my_distro.v2'];
  for (const n of ok) assert.ok(wsl.DISTRO_NAME_RE.test(n), `${n} は名前として通す`);
  const ng = ['払湵畴㈭⸲㐰', '', ' Ubuntu', 'Ubuntu 22.04', '-Ubuntu', 'a'.repeat(65)];
  for (const n of ng) assert.ok(!wsl.DISTRO_NAME_RE.test(n), `${JSON.stringify(n)} は弾く`);
});

test('bash を持たない補助ディストロは起動先に選ばない', () => {
  // 既定が docker-desktop の環境で -e bash を撃つと即死し、ウィンドウが一瞬で閉じる。
  for (const n of ['docker-desktop', 'docker-desktop-data', 'rancher-desktop', 'podman-machine-default']) {
    assert.ok(wsl.UTILITY_DISTRO_RE.test(n), `${n} は補助ディストロとして避ける`);
  }
  for (const n of ['Ubuntu', 'Debian', 'docker-ubuntu']) {
    assert.ok(!wsl.UTILITY_DISTRO_RE.test(n), `${n} は通常ディストロとして選べる`);
  }
});

test('補助ディストロしか無ければ名前を決めない（既定へ倒す）', () => {
  const names = wsl.parseWslDistroList(
    Buffer.from('docker-desktop\r\ndocker-desktop-data\r\n', 'utf16le')
  );
  assert.strictEqual(names.find((n) => !wsl.UTILITY_DISTRO_RE.test(n)) || '', '',
    '通常ディストロが無ければ -d を付けない');
});

test('win32 以外では一覧を引かない（wsl.exe を撃たない）', () => {
  wsl.clearCache();
  assert.deepStrictEqual(wsl.listWslDistros(), []);
  assert.strictEqual(wsl.defaultWslDistro(), '');
});

// ---------------------------------------------------------------------------
// 起動前の実地検査（verifyWslLaunch）
// ---------------------------------------------------------------------------
// 「ウィンドウが一瞬で開いて閉じる」の原因は wsl/ディストロ・bash・スクリプトの可視性の
// どれか。閉じた後では読めないので、開く前に確かめて理由を画面へ返す。

const OK = { status: 0, stdout: '', stderr: '', error: '' };
function recorder(reply) {
  const calls = [];
  return {
    calls,
    run(command, args) {
      calls.push({ command, args });
      return reply(args, calls.length);
    },
  };
}

test('検査は起動と同じ形（wsl.exe [-d X] -e bash -lc …）で撃つ', () => {
  // 形が違うと「検査は通るのに本番だけ落ちる」が起こる。
  const r = recorder(() => OK);
  const res = wsl.verifyWslLaunch('/mnt/c/t/run.sh', 'Ubuntu', r.run);
  assert.strictEqual(res.ok, true);
  assert.strictEqual(res.distro, 'Ubuntu');
  assert.strictEqual(r.calls.length, 1, '通ったら 1 回で終わる');
  assert.deepStrictEqual(r.calls[0].args.slice(0, 5),
    ['-d', 'Ubuntu', '-e', 'bash', '-lc'], '起動と同じ argv の形');
  assert.ok(r.calls[0].args[5].includes("test -f '/mnt/c/t/run.sh'"),
    'スクリプトが WSL から見えるかまで確かめる');
});

test('希望のディストロがだめなら既定へ落として起動できる形を選ぶ', () => {
  // 推測（一覧の先頭）が外れても、既定で起動できるならそれで開く。
  const r = recorder((args) => (args[0] === '-d'
    ? { status: 1, stdout: '', stderr: '指定された名前のディストリビューションはありません。', error: '' }
    : OK));
  const res = wsl.verifyWslLaunch('/mnt/c/t/run.sh', 'Ubuntu-bad', r.run);
  assert.strictEqual(res.ok, true, '既定で起動できるなら通す');
  assert.strictEqual(res.distro, '', '-d を付けない形を選ぶ');
  assert.strictEqual(r.calls.length, 2, '-d 付き → -d 無しの順で試す');
});

test('スクリプトが見えないときはディストロを変えて粘らない', () => {
  // /mnt が見えないのはディストロを変えても直らない。無駄な待ちを作らず理由を返す。
  const r = recorder(() => ({ status: wsl.SCRIPT_MISSING_EXIT, stdout: '', stderr: '', error: '' }));
  const res = wsl.verifyWslLaunch('/mnt/c/t/run.sh', 'Ubuntu', r.run);
  assert.strictEqual(res.ok, false);
  assert.strictEqual(r.calls.length, 1, '1 回で打ち切る');
  assert.ok(res.error.includes('/mnt/c/t/run.sh'), 'どのパスが見えないかを示す');
  assert.ok(res.error.includes('automount'), '確認先（automount）まで示す');
});

test('どれもだめなら試した内容とインストール済みディストロを添えて返す', () => {
  const r = recorder((args) => (args[0] === '--list'
    ? { status: 0, stdout: '  NAME      STATE     VERSION\n* Ubuntu    Running   2', stderr: '', error: '' }
    : { status: 1, stdout: '', stderr: 'bash: command not found', error: '' }));
  const res = wsl.verifyWslLaunch('/mnt/c/t/run.sh', 'Ubuntu', r.run);
  assert.strictEqual(res.ok, false);
  assert.ok(res.error.includes('-d Ubuntu'), '希望のディストロでの結果を残す');
  assert.ok(res.error.includes('既定ディストロ'), '既定での結果も残す');
  assert.ok(res.error.includes('bash: command not found'), 'wsl 側のメッセージをそのまま出す');
  assert.ok(res.error.includes('* Ubuntu'), 'インストール済み一覧を添える');
});

test('wsl.exe 自体を起動できないときはインストールを疑うよう伝える', () => {
  const r = recorder(() => ({ status: null, stdout: '', stderr: '', error: 'spawnSync wsl.exe ENOENT' }));
  const res = wsl.verifyWslLaunch('/mnt/c/t/run.sh', '', r.run);
  assert.strictEqual(res.ok, false);
  assert.ok(res.error.includes('WSL がインストールされているか'), '次に確認することを書く');
  assert.ok(res.error.includes('ENOENT'), '生のエラーも残す');
});

test('wsl.exe の UTF-16LE メッセージを読めるように解読する', () => {
  // wsl.exe 自身のメッセージは UTF-16LE、ディストロ内のコマンド出力は UTF-8。
  const jp = '指定された名前のディストリビューションはありません。';
  assert.strictEqual(wsl.decodeWslOutput(Buffer.from(jp, 'utf16le')), jp);
  assert.strictEqual(wsl.decodeWslOutput(Buffer.from('bash: not found', 'utf8')), 'bash: not found');
  assert.strictEqual(wsl.decodeWslOutput(Buffer.alloc(0)), '');
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (e) {
    failed += 1;
    console.error(`not ok - ${name}\n  ${e.message}`);
  }
}
if (failed) {
  console.error(`\n${failed} wsl-distro tests failed`);
  process.exit(1);
}
console.log(`\n${tests.length} wsl-distro tests passed`);
