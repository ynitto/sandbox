'use strict';

const path = require('path');
const { ipcMain, shell } = require('electron');
const { loadConfig, saveConfig } = require('./config');
const kiro = require('./kiro');
const flow = require('./flow');
const { GitLabClient } = require('./gitlab');
const { openInReviewViewer } = require('./review');

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

function registerIpcHandlers() {
  handle('config:get', () => loadConfig());
  handle('config:save', ({ config }) => saveConfig(config));

  // 発見: 設定 roots + instances 自動発見 → コンテナ→プロジェクトのツリー
  handle('kiro:discover', () => kiro.discover(loadConfig()));

  // 1 プロジェクトの完全スナップショット
  handle('kiro:project', ({ dir }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return kiro.readProject(dir);
  });

  // kiro-flow バス（プロジェクト内 bus/ または設定の共有バス）
  handle('flow:runs', ({ busDir, limit }) => flow.listRuns(busDir, limit || 30));
  handle('flow:run', ({ busDir, runId }) => {
    const runDir = path.join(busDir, 'runs', runId);
    return {
      run: flow.readRun(runDir),
      events: flow.readRunEvents(runDir, 50),
    };
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

  // gitlab-review-viewer へレビューを引き継ぐ
  handle('review:open', ({ target }) => openInReviewViewer(loadConfig(), target || {}));

  handle('shell:openExternal', ({ url }) => {
    if (!/^https?:\/\//.test(url)) throw new Error(`外部で開けない URL です: ${url}`);
    return shell.openExternal(url);
  });

  handle('shell:openPath', ({ target }) => shell.openPath(target));
}

module.exports = { registerIpcHandlers };
