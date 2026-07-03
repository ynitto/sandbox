'use strict';

// gitlab-review-viewer へのレビュー引き継ぎ。
// 既定はカスタム URL スキーム（gitlab-review-viewer://open?url=...）で、
// gitlab-review-viewer 側のディープリンク対応（同リポジトリで追加済み）が
// OS にプロトコル登録されていれば、そのウィンドウで対象イシューが開く。
// 未登録環境向けに任意コマンド起動（mode: command）も用意する。

const { shell } = require('electron');
const { spawn } = require('child_process');
const { GitLabClient } = require('./gitlab');

function buildProtocolUrl(base, target) {
  const u = new URL(base);
  u.searchParams.set('url', target.url);
  return u.toString();
}

function substitute(template, target) {
  return template
    .replaceAll('{url}', target.url || '')
    .replaceAll('{projectPath}', target.projectPath || '')
    .replaceAll('{type}', target.type || '')
    .replaceAll('{iid}', target.iid != null ? String(target.iid) : '');
}

// target: {url} 必須。type/projectPath/iid は url から補完する。
async function openInReviewViewer(cfg, target) {
  const parsed = GitLabClient.parseUrl(target.url) || {};
  const full = {
    type: parsed.type || 'issue',
    projectPath: parsed.projectPath || '',
    iid: parsed.iid,
    ...target,
  };
  const rv = cfg.reviewViewer || {};
  if (rv.mode === 'command' && rv.command) {
    const cmd = substitute(rv.command, full);
    const child = spawn(cmd, { shell: true, detached: true, stdio: 'ignore' });
    child.unref();
    return { via: 'command', command: cmd };
  }
  const url = buildProtocolUrl(rv.protocol || 'gitlab-review-viewer://open', full);
  await shell.openExternal(url);
  return { via: 'protocol', url };
}

module.exports = { openInReviewViewer };
