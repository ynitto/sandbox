'use strict';

// 更新元へ配布物を置く（自動更新の送り手。受け手は src/main/update.js）。
//
//   npm run dist:portable
//   node scripts/publish-update.js <更新元> [--notes "<1 行>"] [--exe <path>]
//
// 更新元は共有フォルダ（\\server\share\agent-app / /mnt/share/agent-app）か、社内の HTTP で
// 配るならその文書ルート。書くのは 2 つ:
//   agent-app-<版>.exe   … release/agent-app.exe（package.json の version を版とする）
//   manifest.json        … 版・ファイル名・sha256・大きさ
//
// WSL 側の agent-tools はここでは配らない。agent-project の自己更新（git のリポジトリから
// sparse-checkout して install.sh）に乗るので、送り手は git push だけでよい。
// ビルド環境（CI）は無い前提で、手元で実行する。外部サービスは使わない。

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT = path.join(__dirname, '..');

function parseArgs(argv) {
  const opts = { dest: '', notes: '', exe: '' };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--notes') { opts.notes = String(argv[i + 1] || ''); i += 1; }
    else if (a === '--exe') { opts.exe = String(argv[i + 1] || ''); i += 1; }
    else if (a.startsWith('-')) throw new Error(`不明な引数: ${a}`);
    else if (!opts.dest) opts.dest = a;
    else throw new Error(`引数が多すぎます: ${a}`);
  }
  if (!opts.dest) throw new Error('使い方: node scripts/publish-update.js <更新元> [--notes "<1 行>"]');
  return opts;
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function publishApp(dest, { exe = '' } = {}) {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
  const source = exe || path.join(ROOT, 'release', `${pkg.name}.exe`);
  if (!fs.existsSync(source)) throw new Error(`portable 版がありません: ${source}（npm run dist:portable を先に）`);
  const target = path.join(dest, `${pkg.name}-${pkg.version}.exe`);
  fs.copyFileSync(source, target);
  return { version: pkg.version, file: path.basename(target), sha256: sha256(target), size: fs.statSync(target).size };
}

function publish(opts) {
  const dest = path.resolve(opts.dest);
  fs.mkdirSync(dest, { recursive: true });
  const manifest = {
    schema: 1,
    publishedAt: new Date().toISOString(),
    notes: opts.notes || '',
    app: publishApp(dest, opts),
  };
  const target = path.join(dest, 'manifest.json');
  const temp = `${target}.tmp-${process.pid}`;
  fs.writeFileSync(temp, `${JSON.stringify(manifest, null, 2)}\n`);
  fs.renameSync(temp, target);
  return manifest;
}

if (require.main === module) {
  try {
    const manifest = publish(parseArgs(process.argv.slice(2)));
    console.log(`app: ${manifest.app.version}  ${manifest.app.file}  (${manifest.app.size} bytes)`);
    console.log(`manifest.json を書きました: ${path.resolve(process.argv[2] || '')}`);
  } catch (err) {
    console.error(`publish-update: ${err.message}`);
    process.exit(1);
  }
}

module.exports = { parseArgs, publish, publishApp };
