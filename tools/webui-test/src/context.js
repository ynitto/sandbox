'use strict';
// 実行記録。あとから「どの版のアプリ・テストで、どのコマンドで、どこに向けて動かしたか」を
// 辿れるよう、レポートと一緒に残す。git が無い・リポジトリでない場所では該当項目を空にする。

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

function git(cwd, args) {
  try {
    return execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 5000 }).trim();
  } catch (_) {
    return null;
  }
}

// dir が属する git リポジトリの場所・コミット・未コミットの変更の有無
function gitInfo(dir) {
  const root = git(dir, ['rev-parse', '--show-toplevel']);
  if (!root) return null;
  const status = git(root, ['status', '--porcelain']);
  return {
    root,
    sha: git(root, ['rev-parse', 'HEAD']),
    branch: git(root, ['rev-parse', '--abbrev-ref', 'HEAD']),
    dirty: status === null ? null : status.length > 0,
  };
}

function sha256(file) {
  try {
    return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  } catch (_) {
    return null;
  }
}

function packageVersion(name) {
  try {
    return require(`${name}/package.json`).version;
  } catch (_) {
    return null;
  }
}

function collect({ argv, env, files, sources = [] }) {
  const repos = new Map();
  const addRepo = (dir, role) => {
    const info = gitInfo(dir);
    if (!info) return;
    const cur = repos.get(info.root) || { ...info, roles: [] };
    if (!cur.roles.includes(role)) cur.roles.push(role);
    repos.set(info.root, cur);
  };
  addRepo(process.cwd(), '作業ディレクトリ');
  for (const f of files) addRepo(path.dirname(f), 'テストケース');
  // 仕様・実装の置き場（--source で渡す）。別のフォルダ・別のリポジトリでもよい
  for (const s of sources) addRepo(path.resolve(s), '参照元');
  return {
    command: ['webui-test', ...argv].join(' '),
    cwd: process.cwd(),
    env: env ? { name: env.name, config: env.file, baseUrl: env.settings && env.settings.baseUrl } : null,
    node: process.version,
    playwright: packageVersion('playwright'),
    webuiTest: require('../package.json').version,
    os: `${os.platform()} ${os.release()} ${os.arch()}`,
    host: os.hostname(),
    files: files.map((f) => ({ path: path.relative(process.cwd(), f) || f, sha256: sha256(f) })),
    repos: [...repos.values()],
  };
}

module.exports = { collect, gitInfo };
