'use strict';

// base IPC: 設定・git・シェルなど、制御スタックに依存しない共通チャネル。
// 制御面（agent-project / 将来の kiro-loop）は features/*/main/ipc.js で登録する。

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

  // 選択中プロジェクトのリポジトリを git pull で最新化する。
  // 自動（force=false）は設定間隔・下限 60 秒のスロットリングでリモート負荷を抑える。
  // 都度プッシュ（gitAutoPush）が有効なときはローカルコミットと共存できる --rebase で取り込む
  handle('git:pull', ({ dir, force }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    const cfg = loadConfig();
    const intervalSec = (cfg.projects && Number(cfg.projects.gitPullSec)) || 300;
    const rebase = !!(cfg.projects && cfg.projects.gitAutoPush);
    return git.pull(dir, { intervalSec, force: !!force, rebase });
  });

  // ユーザー操作（指示・投入・記入・削除）の書き込みをコミットして push する
  // （状態共有 git への都度反映）。設定 gitAutoPush が無効なら何もしない
  handle('git:commitPush', ({ dir, message, paths }) => {
    if (!dir) throw new Error('対象ディレクトリが指定されていません');
    const cfg = loadConfig();
    if (!(cfg.projects && cfg.projects.gitAutoPush)) return { skipped: true, disabled: true };
    return git.commitPush(dir, { message, paths });
  });

  // 同期の健康状態（ローカル参照のみ）と、一発修復（🩺 ボタン）
  handle('git:health', ({ dir, refreshRemote }) => {
    if (!dir) throw new Error('対象ディレクトリが指定されていません');
    // 状態確認の fetch は作業ツリーを変更しない。自動 pull が無効でも最低60秒間隔で
    // 追跡情報を更新し、「同期は正常」という古い判定を表示し続けない。
    // ただし明示的な「表示を更新」はローカル再読込だけ、というUI契約を守る。
    return git.health(dir, { refreshRemote: !!refreshRemote, intervalSec: 60 });
  });
  handle('git:heal', ({ dir }) => {
    if (!dir) throw new Error('対象ディレクトリが指定されていません');
    return git.heal(dir);
  });
  // セットアップ診断（案4）: 登録 clone の有効性・追跡ブランチを役割つきで返す。
  handle('setup:diagnostics', () => git.diagnostics(loadConfig()));

  // 検収サブ画面: 作業ブランチの git 差分（複数リポジトリ対応）。
  // branch/fetch は「fetch 後に origin/<branch> を優先」する検収の鮮度更新に使う。
  handle('git:diff', ({ repo, base, ref, file, branch, fetch, maxBytes, workingTree, viewerRoot }) =>
    git.diffRange(repo, { base, ref, file, branch, fetch: !!fetch, maxBytes, workingTree: !!workingTree, viewerRoot })
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
