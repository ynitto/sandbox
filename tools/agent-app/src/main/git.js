'use strict';

// 変更ビュー用。作業ツリーの状態と差分を読むだけで、git に書き込む操作は持たない。

const { execFile } = require('child_process');

function git(repo, args) {
  return new Promise((resolve) => {
    execFile('git', ['-C', repo, ...args], { maxBuffer: 32 * 1024 * 1024, windowsHide: true }, (err, stdout, stderr) => {
      resolve({ ok: !err, stdout: String(stdout || ''), stderr: String(stderr || (err && err.message) || '') });
    });
  });
}

function parseStatus(text) {
  return text.split('\n').filter(Boolean).map((line) => {
    const code = line.slice(0, 2);
    const file = line.slice(3).replace(/^"(.*)"$/, '$1');
    const label = code === '??' ? '新規' : /D/.test(code) ? '削除' : /R/.test(code) ? '改名' : /A/.test(code) ? '追加' : '変更';
    return { code, file, label };
  });
}

async function changes(repo) {
  const status = await git(repo, ['status', '--porcelain', '--untracked-files=all']);
  if (!status.ok) return { files: [], diff: '', error: status.stderr.trim() || 'git リポジトリではありません' };
  const files = parseStatus(status.stdout);
  const tracked = await git(repo, ['diff', 'HEAD', '--no-color', '--no-ext-diff']);
  // HEAD が無い（初回コミット前）ときは index との差分に倒す
  const diff = tracked.ok ? tracked.stdout : (await git(repo, ['diff', '--no-color', '--no-ext-diff'])).stdout;
  return { files, diff, error: '' };
}

async function fileDiff(repo, file) {
  const tracked = await git(repo, ['diff', 'HEAD', '--no-color', '--no-ext-diff', '--', file]);
  if (tracked.ok && tracked.stdout) return tracked.stdout;
  // 未追跡はそのまま「全行追加」として見せる
  const raw = await git(repo, ['diff', '--no-color', '--no-index', '--', '/dev/null', file]);
  return raw.stdout;
}

module.exports = { changes, fileDiff, parseStatus };
