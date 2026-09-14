'use strict';

// 更新元へ配布物を置く（自動更新の送り手。受け手は src/main/update.js）。
//
//   npm run dist:portable
//   node scripts/publish-update.js <更新元> [--app-only | --tools-only] [--notes "<1 行>"] [--exe <path>]
//
// 更新元は共有フォルダ（\\server\share\agent-app / /mnt/share/agent-app）か、社内の HTTP で
// 配るならその文書ルート。書くのは 3 つ:
//   agent-app-<版>.exe        … release/agent-app.exe（package.json の version を版とする）
//   agent-tools-<版>.tar.gz   … agent-app が呼ぶ 3 本（agent-herd / agent-loop / agent-flow）の元を HEAD から git archive
//   manifest.json             … 上の 2 つの版・ファイル名・sha256・大きさ。片方だけ置き直すときは
//                               もう片方の項目を前の manifest から引き継ぐ
//
// ビルド環境（CI）は無い前提で、手元で実行する。外部サービスは使わない。

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const REPO = path.join(ROOT, '..', '..');
// agent-app が呼ぶのは agent-herd（tools/agent-tools/agentcore に住む）・agent-loop・agent-flow の 3 本だけ。
// それ以外のエンジン（agent-project / agent-amigos / agent-audit …）は入れず、入れ直しもしない。
// agents/（CLI 定義）と commands/（用途コマンド）は install.sh が 3 本の共通の置き場へ配る分で、
// 無いと入れた道具が組み込み CLI を「未知」と言う。
const TOOL_DIRS = ['tools/agent-tools', 'tools/agent-flow', 'tools/agent-loop', 'agents', 'commands'];

function parseArgs(argv) {
  const opts = { dest: '', app: true, tools: true, notes: '', exe: '' };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--app-only') opts.tools = false;
    else if (a === '--tools-only') opts.app = false;
    else if (a === '--notes') { opts.notes = String(argv[i + 1] || ''); i += 1; }
    else if (a === '--exe') { opts.exe = String(argv[i + 1] || ''); i += 1; }
    else if (a.startsWith('-')) throw new Error(`不明な引数: ${a}`);
    else if (!opts.dest) opts.dest = a;
    else throw new Error(`引数が多すぎます: ${a}`);
  }
  if (!opts.dest) throw new Error('使い方: node scripts/publish-update.js <更新元> [--app-only | --tools-only] [--notes "<1 行>"]');
  return opts;
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function git(args, cwd = REPO) {
  return execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
}

function describe(file) {
  return { file: path.basename(file), sha256: sha256(file), size: fs.statSync(file).size };
}

function publishApp(dest, { exe = '' } = {}) {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
  const source = exe || path.join(ROOT, 'release', `${pkg.name}.exe`);
  if (!fs.existsSync(source)) throw new Error(`portable 版がありません: ${source}（npm run dist:portable を先に）`);
  const target = path.join(dest, `${pkg.name}-${pkg.version}.exe`);
  fs.copyFileSync(source, target);
  return { version: pkg.version, ...describe(target) };
}

function toolsVersion(repo = REPO) {
  const sha = git(['rev-parse', '--short', 'HEAD'], repo);
  const date = git(['log', '-1', '--format=%cd', '--date=format:%Y%m%d'], repo);
  return `${date}-${sha}`;
}

function publishTools(dest, { repo = REPO } = {}) {
  const dirs = TOOL_DIRS.filter((d) => fs.existsSync(path.join(repo, d)));
  if (!dirs.includes('tools/agent-tools')) throw new Error(`tools/agent-tools がありません: ${repo}`);
  const dirty = git(['status', '--porcelain', '--', ...dirs], repo);
  if (dirty) console.warn(`publish-update: コミットしていない変更は入りません（HEAD から作る）:\n${dirty}`);
  const version = toolsVersion(repo);
  const target = path.join(dest, `agent-tools-${version}.tar.gz`);
  git(['archive', '--format=tar.gz', '-o', target, 'HEAD', '--', ...dirs], repo);
  return { version, ...describe(target) };
}

function readExisting(dest) {
  try { return JSON.parse(fs.readFileSync(path.join(dest, 'manifest.json'), 'utf8')); } catch { return {}; }
}

function publish(opts) {
  const dest = path.resolve(opts.dest);
  fs.mkdirSync(dest, { recursive: true });
  const previous = readExisting(dest);
  const manifest = {
    schema: 1,
    publishedAt: new Date().toISOString(),
    notes: opts.notes || '',
    app: opts.app ? publishApp(dest, opts) : (previous.app || null),
    tools: opts.tools ? publishTools(dest, opts) : (previous.tools || null),
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
    for (const key of ['app', 'tools']) {
      const item = manifest[key];
      console.log(`${key}: ${item ? `${item.version}  ${item.file}  (${item.size} bytes)` : 'なし'}`);
    }
    console.log(`manifest.json を書きました: ${path.resolve(process.argv[2] || '')}`);
  } catch (err) {
    console.error(`publish-update: ${err.message}`);
    process.exit(1);
  }
}

module.exports = { parseArgs, publish, publishApp, publishTools, toolsVersion, TOOL_DIRS };
