'use strict';

// gitlab-review-viewer へのレビュー引き継ぎ。
// 既定はカスタム URL スキーム（gitlab-review-viewer://open?url=...）で、
// gitlab-review-viewer 側のディープリンク対応（同リポジトリで追加済み）が
// OS にプロトコル登録されていれば、そのウィンドウで対象イシューが開く。
//
// ただし portable exe はインストーラを通らず、起動ごとに一時ディレクトリへ
// 展開されるため、カスタム URL スキームを OS に恒久登録できない
// （setAsDefaultProtocolClient の登録先が毎回消える一時パスになる）。
// このため protocol モードでは portable 版 gitlab-review-viewer を連携起動できない。
// mode: exe はこの制約を回避する経路で、gitlab-review-viewer の実行ファイルへ
// ディープリンク URL を argv として直接渡す（プロトコル登録に依存しない）。
// gitlab-review-viewer 側は deepLinkFromArgv / second-instance で受け取るため、
// 未起動でも既に起動済みでも同じウィンドウで対象イシューが開く。
// 未登録環境向けに任意コマンド起動（mode: command）も従来どおり用意する。

const fs = require('fs');
const { shell } = require('electron');
const { spawn } = require('child_process');
const { GitLabClient } = require('./gitlab');
const { tryHandoff } = require('./reviewHandoff');

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
    .replaceAll('{iid}', target.iid != null ? String(target.iid) : '')
    .replaceAll('{protocolUrl}', target.protocolUrl || '');
}

// target: {url} 必須。type/projectPath/iid は url から補完する。
async function openInReviewViewer(cfg, target) {
  const parsed = GitLabClient.parseUrl(target.url) || {};
  const rv = cfg.reviewViewer || {};
  const protocolUrl = buildProtocolUrl(rv.protocol || 'gitlab-review-viewer://open', target);
  const full = {
    type: parsed.type || 'issue',
    projectPath: parsed.projectPath || '',
    iid: parsed.iid,
    protocolUrl,
    ...target,
  };

  // exe: 実行ファイルへディープリンクを直接渡す（portable exe でも動く経路）。
  // shell を介さず argv に protocolUrl を 1 要素として渡すので、パスの空白や
  // URL 内の特殊文字でクォートが壊れない。exePath 未設定なら protocol へフォールバック。
  if (rv.mode === 'exe' && rv.exePath) {
    // 既に gitlab-review-viewer が起動していればローカル IPC で即ハンドオフする。
    // portable exe を再び spawn すると、起動済みでも自己展開＋Electron 起動＋
    // single-instance 転送の数秒コストを必ず払うため、それを回避する経路。
    // 起動していなければ接続に失敗するので、下の exe 起動へフォールバックする。
    if (await tryHandoff(protocolUrl)) {
      return { via: 'exe-running', url: protocolUrl };
    }
    if (!fs.existsSync(rv.exePath)) {
      throw new Error(
        `gitlab-review-viewer の実行ファイルが見つかりません（⚙ 設定 > 実行ファイルのパス）: ${rv.exePath}`
      );
    }
    const child = spawn(rv.exePath, [protocolUrl], { detached: true, stdio: 'ignore' });
    child.on('error', () => {}); // 起動失敗（EACCES 等）で未捕捉例外にしない
    child.unref();
    return { via: 'exe', exePath: rv.exePath, url: protocolUrl };
  }
  if (rv.mode === 'command' && rv.command) {
    const cmd = substitute(rv.command, full);
    const child = spawn(cmd, { shell: true, detached: true, stdio: 'ignore' });
    child.unref();
    return { via: 'command', command: cmd };
  }
  await shell.openExternal(protocolUrl);
  return { via: 'protocol', url: protocolUrl };
}

module.exports = { openInReviewViewer };
