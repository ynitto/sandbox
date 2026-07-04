'use strict';

const path = require('path');
const { ipcMain, shell } = require('electron');
const { loadConfig, saveConfig } = require('./config');
const kiro = require('./kiro');
const flow = require('./flow');
const { lookupScalar } = require('./toolconfig');
const { GitLabClient } = require('./gitlab');
const { openInReviewViewer } = require('./review');
const actions = require('./actions');

// すべてのハンドラを {ok, data|error} 形式に揃える（gitlab-review-viewer と同じ）
function handle(channel, fn) {
  ipcMain.handle(channel, async (_event, args) => {
    try {
      return { ok: true, data: await fn(args || {}) };
    } catch (err) {
      return { ok: false, error: err && err.message ? err.message : String(err) };
    }
  });
}

function client() {
  return new GitLabClient(loadConfig().gitlab);
}

// kiro-flow daemon ロックの置き場。⚙ 設定 > ~/.kiro の kiro-projects/kiro-flow 設定の
// lock_dir > 両ツール共通の既定（tempdir 配下。daemonStatus 側で導出）。
function flowLockDir(cfg) {
  if (cfg.kiro && cfg.kiro.flowLockDir) return cfg.kiro.flowLockDir;
  const found = lookupScalar('lock_dir');
  return found ? found.value : null;
}

function registerIpcHandlers() {
  handle('config:get', () => loadConfig());
  handle('config:save', ({ config }) => saveConfig(config));

  // 発見: 設定 roots + instances 自動発見 → コンテナ→プロジェクトのツリー
  handle('kiro:discover', () => kiro.discover(loadConfig()));

  // 1 プロジェクトの完全スナップショット（バスの発見に設定 kiro.flowBus も使う）
  handle('kiro:project', ({ dir }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return kiro.readProject(dir, loadConfig());
  });

  // kiro-flow バス（per-project bus/ または共有バス）。run 一覧に加えて daemon の
  // 稼働もロックファイルから判定して返す（kiro-flow CLI には一切聞かない）
  handle('flow:runs', ({ busDir, limit }) => ({
    runs: flow.listRuns(busDir, limit || 30),
    daemon: flow.daemonStatus(busDir, flowLockDir(loadConfig())),
  }));
  handle('flow:run', ({ busDir, runId }) => {
    const runDir = path.join(busDir, 'runs', runId);
    return {
      run: flow.readRun(runDir),
      events: flow.readRunEvents(runDir, 50),
      nodeEvents: flow.readNodeEvents(runDir), // ノード別タイムライン（開始・所要の根拠）
    };
  });

  // 失敗/完了した run を同じ要求で inbox へ再投入（人の明示アクション。新しい run になる）
  handle('flow:resubmit', ({ busDir, runId }) => flow.resubmitRun(busDir, runId));

  // 実行中ノードの関連イシューを決定的タスクトークンで検索（gitlab executor 連動）
  handle('gitlab:findIssueByToken', ({ repoUrl, projectPath, token }) => {
    const gl = client();
    if (!gl.enabled) return { enabled: false, issue: null };
    return gl
      .findIssueByToken({ repoUrl, projectPath, token })
      .then((issue) => ({ enabled: true, issue }));
  });

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

  // 人のアクション（needs 回答・タスク投入・決定記録を残す CLI 操作）
  handle('kiro:feedback', ({ file, feedback }) => actions.submitFeedback(file, feedback));
  handle('kiro:enqueue', ({ dir, spec }) => actions.enqueueToInbox(dir, spec || {}));
  handle('kiro:action', (args) => actions.runAction(loadConfig(), args));

  // gitlab-review-viewer へレビューを引き継ぐ
  handle('review:open', ({ target }) => openInReviewViewer(loadConfig(), target || {}));

  handle('shell:openExternal', ({ url }) => {
    if (!/^https?:\/\//.test(url)) throw new Error(`外部で開けない URL です: ${url}`);
    return shell.openExternal(url);
  });

  handle('shell:openPath', ({ target }) => shell.openPath(target));
}

module.exports = { registerIpcHandlers };
