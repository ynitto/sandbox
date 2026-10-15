#!/usr/bin/env node
'use strict';
// testcase-lint をグローバルに入れる（Windows・macOS・Linux 共通。要るのは Node.js 18 以上と npm だけ）。
//
//   node tools/testcase-lint/install.js            # 入れる（入っていれば入れ直す）
//   node tools/testcase-lint/install.js --check    # 入れたあと、同梱の例で動くか確かめる
//
// フォルダを直接 `npm install -g .` すると、このフォルダへのリンクが置かれるだけで、リポジトリを動かす・消すと
// コマンドが壊れる。そこで `npm pack` で固めたものを入れる（依存の YAML・JSON Schema・形態素解析の辞書も一緒に入る）。

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const HERE = __dirname;
const WIN = process.platform === 'win32';
const say = (s) => process.stdout.write(`${s}\n`);
const die = (s) => { process.stderr.write(`[失敗] ${s}\n`); process.exit(1); };

// npm は Windows では npm.cmd なので、シェル経由で呼ぶ
function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { encoding: 'utf8', shell: WIN, ...opts });
  if (r.error) return { code: 1, out: '', err: String(r.error.message) };
  return { code: r.status, out: (r.stdout || '').trim(), err: (r.stderr || '').trim() };
}

function main(argv) {
  if (argv.includes('-h') || argv.includes('--help')) {
    say('使い方: node install.js [--check]\n  --check  入れたあと、同梱の例で動くか確かめる');
    return;
  }
  const major = Number(process.versions.node.split('.')[0]);
  if (major < 18) die(`Node.js 18 以上が要ります（今は ${process.versions.node}）`);
  if (run('npm', ['--version']).code !== 0) die('npm が見つかりません（Node.js と一緒に入れてください）');

  // 1. 固める
  const packDir = fs.mkdtempSync(path.join(os.tmpdir(), 'testcase-lint-pack-'));
  say('testcase-lint を固めます（npm pack）');
  const pack = run('npm', ['pack', '--silent', '--pack-destination', packDir], { cwd: HERE });
  const tarball = path.join(packDir, (pack.out.split(/\r?\n/).pop() || '').trim());
  if (pack.code !== 0 || !fs.existsSync(tarball)) die(`npm pack に失敗しました\n${pack.err}`);

  // 2. グローバルに入れる
  say('グローバルに入れます（npm install -g）');
  const inst = spawnSync('npm', ['install', '-g', tarball, '--omit=dev', '--no-audit', '--no-fund'], { stdio: 'inherit', shell: WIN });
  fs.rmSync(packDir, { recursive: true, force: true });
  if (inst.status !== 0) {
    die('npm install -g に失敗しました。権限の不足なら、npm の置き場をユーザーのフォルダにしてから入れ直してください\n'
      + '  npm config set prefix ~/.npm-global   （そのあと ~/.npm-global/bin を PATH に足す）');
  }

  // 3. コマンドが呼べるか
  const ver = run('testcase-lint', ['--version']);
  if (ver.code !== 0) {
    const prefix = run('npm', ['prefix', '-g']).out;
    const bin = WIN ? prefix : path.join(prefix, 'bin');
    die(`入れましたが、testcase-lint コマンドが見つかりません。次のフォルダを PATH に足してください: ${bin}`);
  }
  say(`[OK] ${ver.out} を入れました`);

  // 4. 同梱の例で確かめる（--check）
  if (argv.includes('--check')) {
    const ex = path.join(HERE, 'examples');
    const r = run('testcase-lint', ['--fail-on', 'never', '--min-severity', 'error'], { cwd: ex });
    if (r.code !== 0 || !/ケース: cases/.test(r.out)) die(`同梱の例で動きませんでした\n${r.out}\n${r.err}`);
    say(`[OK] 同梱の例で動きました: ${r.out.split(/\r?\n/).pop()}`);
  }
  say('\nこれからは、点検したいリポジトリで testcase-lint を実行します（最初に testcase-lint init で設定を作れる）。');
  say('外すときは npm uninstall -g testcase-lint');
}

main(process.argv.slice(2));
