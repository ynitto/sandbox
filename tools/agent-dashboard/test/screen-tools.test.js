'use strict';

// 画面操作の道具（playwright-cli / winauto）を**どちら側で呼ぶか**の 1 実装。
//
// 護るもの:
//   1. win32 では Windows 側の実体を先に探す（PATHEXT を補う。拡張子なしの名前だけを
//      見る POSIX 流の探索では `winauto.bat` も `playwright-cli.cmd` も見つからない）。
//   2. 見つからなければ WSL 経由へ落ちる（片側にしか入れていない端末でも動く）。
//   3. **どちら側かは一時ファイルの綴りと読み書きの経路も決める**（Windows 側の実体に
//      /tmp/... を渡しても書けない）。
//   4. `.bat` / `.cmd` は直接 spawn できないので cmd.exe に載せる。その 1 行の組み立て。

const assert = require('assert');
const path = require('path');

const screenTools = require('../src/features/cowork/main/screen-tools');
const { nativeSpawnSpec, nativeWindowScript } = require('../src/features/cowork/main/loopProvider');

let passed = 0;
function test(name, fn) {
  const done = fn();
  const finish = () => { passed += 1; console.log(`ok - ${name}`); };
  return done && typeof done.then === 'function' ? done.then(finish) : Promise.resolve(finish());
}

// 偽の Windows PATH。npm のグローバル導入は .cmd、winauto の Windows インストーラは .bat。
const WIN_ENV = {
  PATH: 'C:\\Windows\\system32;C:\\Users\\me\\AppData\\Roaming\\npm;C:\\Users\\me\\.local\\bin\\winauto',
  PATHEXT: '.COM;.EXE;.BAT;.CMD',
  TEMP: 'C:\\Users\\me\\AppData\\Local\\Temp',
};
const WIN_FILES = new Set([
  'C:\\Users\\me\\AppData\\Roaming\\npm\\playwright-cli.cmd',
  'C:\\Users\\me\\.local\\bin\\winauto\\winauto.bat',
]);
const winExists = (file) => WIN_FILES.has(file);

async function main() {
  test('Windows の PATH からは拡張子を補って探す', () => {
    assert.strictEqual(
      screenTools.windowsExecutable('winauto', { env: WIN_ENV, exists: winExists }),
      'C:\\Users\\me\\.local\\bin\\winauto\\winauto.bat');
    assert.strictEqual(
      screenTools.windowsExecutable('playwright-cli', { env: WIN_ENV, exists: winExists }),
      'C:\\Users\\me\\AppData\\Roaming\\npm\\playwright-cli.cmd');
    assert.strictEqual(screenTools.windowsExecutable('nope', { env: WIN_ENV, exists: winExists }), '');
    // 順は PATHEXT のまま（Windows 自身がその順で解決する。ここで独自の順を作らない）
    const both = new Set(['C:\\bin\\t.exe', 'C:\\bin\\t.cmd']);
    assert.strictEqual(
      screenTools.windowsExecutable('t', { env: { PATH: 'C:\\bin', PATHEXT: '.CMD;.EXE' }, exists: (f) => both.has(f) }),
      'C:\\bin\\t.cmd');
    assert.strictEqual(
      screenTools.windowsExecutable('t', { env: { PATH: 'C:\\bin', PATHEXT: '.EXE;.CMD' }, exists: (f) => both.has(f) }),
      'C:\\bin\\t.exe');
  });

  test('win32 は Windows 側を先に見て、無ければ WSL 経由へ落ちる', () => {
    const found = screenTools.resolveScreenTool('winauto',
      { platform: 'win32', env: WIN_ENV, exists: winExists });
    assert.deepStrictEqual(found, { name: 'winauto', native: true, where: 'windows',
      command: 'C:\\Users\\me\\.local\\bin\\winauto\\winauto.bat' });

    const missing = screenTools.resolveScreenTool('winauto',
      { platform: 'win32', env: WIN_ENV, exists: () => false });
    assert.deepStrictEqual(missing, { name: 'winauto', native: false, command: 'winauto', where: 'wsl' },
      'Windows 側に無い端末は従来どおり WSL 側の実体を呼ぶ');

    const linux = screenTools.resolveScreenTool('winauto', { platform: 'linux' });
    assert.deepStrictEqual(linux, { name: 'winauto', native: false, command: 'winauto', where: 'local' });

    assert.strictEqual(screenTools.whereLabel(found), 'Windows 側');
    assert.strictEqual(screenTools.whereLabel(missing), 'WSL 側');
    assert.strictEqual(screenTools.whereLabel(linux), '', 'Windows 以外では側を言わない（1 つしか無い）');
    // AI の解釈・拡張の道具はこの扱いをしない（あちらは WSL 側が正しい）
    assert.deepStrictEqual(screenTools.SCREEN_TOOLS, ['playwright-cli', 'winauto']);
  });

  test('一時ファイルの置き場は実体を呼ぶ側の綴りになる', () => {
    const native = { native: true };
    assert.strictEqual(screenTools.tempDir(native, { env: WIN_ENV }),
      'C:\\Users\\me\\AppData\\Local\\Temp\\agent-dashboard');
    assert.strictEqual(screenTools.tempDir({ native: false }, { env: WIN_ENV }), '/tmp/agent-dashboard');
    assert.strictEqual(screenTools.tempDir(native, { env: {} }), 'C:\\Windows\\Temp\\agent-dashboard',
      'TEMP が無い環境でも書ける場所へ落ちる');
  });

  await test('ネイティブ側の一時ファイルは main が直接触り、WSL 側は実行と同じ経路で触る', async () => {
    const disk = new Map();
    const fsImpl = {
      mkdirSync: (dir) => { disk.set(`dir:${dir}`, true); },
      writeFileSync: (f, b) => { disk.set(f, b); },
      readFileSync: (f) => { if (!disk.has(f)) throw new Error('ENOENT'); return disk.get(f); },
      unlinkSync: (f) => { disk.delete(f); },
    };
    const native = screenTools.fileOps({ native: true }, { env: WIN_ENV, fsImpl });
    assert.strictEqual(native.join('a.jsonl'), 'C:\\Users\\me\\AppData\\Local\\Temp\\agent-dashboard\\a.jsonl');
    await native.prepare();
    await native.touch(native.join('a.stop'));
    assert.strictEqual(await native.read(native.join('a.stop')), '');
    assert.strictEqual(await native.read(native.join('missing')), '', '読めないファイルは空');
    await native.remove([native.join('a.stop')]);
    assert.strictEqual(disk.has(native.join('a.stop')), false);

    const calls = [];
    const capture = async (command, args) => {
      calls.push([command, ...args]);
      return { ok: true, status: 0, stdout: 'body', stderr: '' };
    };
    const wsl = screenTools.fileOps({ native: false }, { capture, cwd: '/r' });
    assert.strictEqual(wsl.join('a.jsonl'), '/tmp/agent-dashboard/a.jsonl');
    await wsl.prepare();
    await wsl.touch('/tmp/agent-dashboard/a.stop');
    assert.strictEqual(await wsl.read('/tmp/agent-dashboard/a.jsonl'), 'body');
    await wsl.remove(['/tmp/agent-dashboard/a.jsonl']);
    assert.deepStrictEqual(calls, [
      ['mkdir', '-p', '/tmp/agent-dashboard'],
      ['touch', '/tmp/agent-dashboard/a.stop'],
      ['cat', '/tmp/agent-dashboard/a.jsonl'],
      ['rm', '-f', '/tmp/agent-dashboard/a.jsonl'],
    ]);

    const failed = screenTools.fileOps({ native: false },
      { capture: async () => ({ ok: false, error: 'permission denied' }) });
    await assert.rejects(failed.prepare(), /置き場を作れませんでした/);
    assert.throws(() => screenTools.fileOps({ native: false }, {}), /実行関数がありません/);
  });

  test('.bat / .cmd は cmd.exe に載せる（直接 spawn できない）', () => {
    const spec = nativeSpawnSpec('C:\\Tools\\winauto.bat',
      ['record', '--app', '勤怠 管理', '--output', 'C:\\Temp\\a.jsonl'], 'C:\\work');
    assert.ok(/cmd\.exe$/i.test(spec.command));
    assert.deepStrictEqual(spec.args.slice(0, 3), ['/d', '/s', '/c']);
    assert.strictEqual(spec.args[3],
      '""C:\\Tools\\winauto.bat" "record" "--app" "勤怠 管理" "--output" "C:\\Temp\\a.jsonl""',
      '全体を引用符で包む（cmd /s は最初と最後の引用符だけを剥がす）');
    assert.strictEqual(spec.options.windowsVerbatimArguments, true, 'Node に引用を足させない');
    assert.strictEqual(spec.options.cwd, 'C:\\work');
    // 渡せる形（ドライブパス）のときだけ渡す。POSIX パスは論外だし、UNC は Windows が
    // プロセスの cwd に持てない（cmd は既定のフォルダへ落ちる）。
    assert.strictEqual('cwd' in nativeSpawnSpec('x.bat', [], '/home/me').options, false);
    assert.strictEqual('cwd' in nativeSpawnSpec('x.bat', [], '\\\\wsl$\\Ubuntu\\home\\me').options, false);
    assert.strictEqual('cwd' in nativeSpawnSpec('x.bat', [], '').options, false);
    // 引数の引用符は "" で畳む（コマンドラインが途中で切れない）
    assert.ok(nativeSpawnSpec('x.bat', ['a"b'], '').args[3].includes('"a""b"'));
  });

  test('ネイティブの窓は Windows のコンソールで開く .cmd を書く', () => {
    const script = nativeWindowScript({
      command: 'C:\\Tools\\winauto.bat', args: ['record', '--app', '勤怠 管理'],
      cwd: 'C:\\work', title: '操作の記録',
    });
    const lines = script.split('\r\n');
    assert.strictEqual(lines[0], '@echo off');
    assert.ok(lines.includes('chcp 65001 > nul'), '日本語の出力が化けないようにする');
    assert.ok(lines.includes('title 操作の記録'));
    assert.ok(lines.includes('cd /d "C:\\work"'));
    assert.ok(lines.includes('"C:\\Tools\\winauto.bat" "record" "--app" "勤怠 管理"'));
    assert.ok(lines.includes('pause > nul'), '終了してもすぐ閉じない（原因を持ち去らせない）');
    // 渡せない cwd へは cd しない（POSIX パスは意味を成さず、UNC は cmd が受け付けない）
    assert.ok(!nativeWindowScript({ command: 'x.bat', args: [], cwd: '/home/me' }).includes('cd /d'));
    assert.ok(!nativeWindowScript({ command: 'x.bat', args: [], cwd: '\\\\wsl$\\Ubuntu\\home' }).includes('cd /d'));
    // タイトルの cmd メタ文字は落とす（引用の段を増やさない）
    assert.ok(nativeWindowScript({ command: 'x.bat', args: [], title: 'a&b|c>d' }).includes('title a b c d'));
  });

  test('画面操作の道具だけがこの扱いを受ける（AI 側の道具は WSL のまま）', () => {
    const fs = require('fs');
    const cowork = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'features', 'cowork', 'main', 'cowork.js'), 'utf8');
    const kit = cowork.slice(cowork.indexOf('function screenToolKit('), cowork.indexOf('function procedureRecording('));
    assert.ok(kit.includes('screenTools.resolveScreenTool(name)'));
    // エージェント CLI・agent-loop の起動は素通し（native を渡さない＝従来どおり wsl.exe 経由）
    const provider = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'features', 'cowork', 'main', 'loopProvider.js'), 'utf8');
    assert.ok(provider.includes("if (process.platform === 'win32' && options.native) {"));
    assert.ok(provider.includes('function cliSpawnSpec(command, args, cwd, options = {}) {'));
  });

  console.log(`\n${passed} screen-tools tests passed`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
