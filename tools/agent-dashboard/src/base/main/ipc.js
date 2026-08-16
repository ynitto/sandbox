'use strict';

// base IPC: 設定・git・シェルなど、制御スタックに依存しない共通チャネル。
// 制御面（agent-project / 将来の agent-loop）は features/*/main/ipc.js で登録する。

const { dialog, shell } = require('electron');
const { handle } = require('./handle');
const { loadConfig, saveConfig } = require('./config');
const git = require('./git');
const { GitLabClient } = require('./gitlab');
const shellActions = require('./shell-actions');
const notify = require('./notify');
const { loadFeatures } = require('../../features');

function client() {
  return new GitLabClient(loadConfig().gitlab);
}

function buildFeatureContext() {
  return {
    handle,
    loadConfig,
    saveConfig,
    GitLabClient,
    git,
    dialog,
    shell,
    client,
  };
}

function registerBaseIpcHandlers() {
  handle('config:get', () => loadConfig());
  handle('config:save', ({ config }) => saveConfig(config));

  // 状態を共有するリポジトリへの書き込み（pull / commitPush / 修復）は削除済み（W2-1）。
  // 書き手は常駐体（agent-project serve）だけで、dashboard は契約ファイルの投函と読み取りしか
  // 行わない。ここに git 書き込みの口を戻さないこと（設計 §4.6）。

  // 検収サブ画面: 作業ブランチの git 差分（複数リポジトリ対応）。
  // branch/fetch は「fetch 後に origin/<branch> を優先」する検収の鮮度更新に使う。
  // repoUrl は「delivery の path がこの PC に無いとき、host.yaml repos[] からクローンを
  // 引き直す」ための手掛かり（S4-e）。worker の作業ツリーは /tmp で消えるので、別マシンの
  // dashboard では path 単独では解決できない。
  handle('git:diff', ({ repo, base, ref, file, branch, fetch, maxBytes, workingTree, viewerRoot, repoUrl }) =>
    git.diffRange(repo, { base, ref, file, branch, fetch: !!fetch, maxBytes,
                          workingTree: !!workingTree, viewerRoot, repoUrl,
                          // host.yaml の置き場（実行エンジンのホーム）の解決に要る
                          cfg: loadConfig() })
  );

  // GitLab イシューの最新状態を API で補完（設定が無ければ enabled:false）
  handle('gitlab:enrich', async ({ urls }) => {
    const gl = client();
    if (!gl.enabled) return { enabled: false, issues: [] };
    const issues = [];
    for (const url of (urls || []).slice(0, 30)) {
      try {
        issues.push(await gl.getIssueByUrl(url));
      } catch (err) {
        issues.push({ url, error: err.message });
      }
    }
    return { enabled: true, issues };
  });

  handle('gitlab:projectIssues', ({ projectPath, state, labels }) => {
    const gl = client();
    if (!gl.enabled) return { enabled: false, issues: [] };
    return gl
      .listProjectIssues({ projectPath, state, labels })
      .then((issues) => ({ enabled: true, issues }));
  });

  handle('gitlab:mrDiscussions', async ({ urls }) => {
    const gl = client();
    if (!gl.enabled) return { enabled: false, comments: [] };
    const comments = [];
    for (const url of (urls || []).slice(0, 10)) {
      comments.push(...await gl.getMrDiscussionsByUrl(url));
    }
    return { enabled: true, comments };
  });

  handle('shell:openExternal', ({ url }) => {
    if (!/^https?:\/\//.test(url)) throw new Error(`外部で開けない URL です: ${url}`);
    return shell.openExternal(url);
  });

  handle('shell:openPath', ({ target }) => {
    // WSL 側の POSIX パス（検収画面の「開く」等）は UNC へ橋渡ししてから開く
    const { _isPosixAbs, toViewerPath } = require('../../features/agent-project/main/project');
    const t = String(target || '');
    const bridged = process.platform === 'win32' && _isPosixAbs(t) ? toViewerPath(t) : t;
    return shellActions.openPath(shell, bridged);
  });

  // OS 通知・タスクバーバッジ・ウィンドウフラッシュ（要対応の増分を renderer が検知して呼ぶ）。
  // 「何を通知するか」は renderer（agent-project の意味を知る側）が決め、ここは出すだけ。
  handle('app:notify', (payload) => notify.notify(payload || {}));
}

function registerIpcHandlers() {
  registerBaseIpcHandlers();
  const ctx = buildFeatureContext();
  for (const feature of loadFeatures()) {
    if (feature && typeof feature.registerIpc === 'function') {
      feature.registerIpc(ctx);
    }
  }
}

module.exports = { registerIpcHandlers, registerBaseIpcHandlers, buildFeatureContext };
