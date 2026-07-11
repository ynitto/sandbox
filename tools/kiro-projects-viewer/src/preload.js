'use strict';

const { contextBridge, ipcRenderer } = require('electron');

async function invoke(channel, args) {
  const res = await ipcRenderer.invoke(channel, args);
  if (!res || !res.ok) {
    throw new Error(res && res.error ? res.error : `${channel} が失敗しました`);
  }
  return res.data;
}

contextBridge.exposeInMainWorld('api', {
  getConfig: () => invoke('config:get'),
  saveConfig: (config) => invoke('config:save', { config }),

  discover: () => invoke('kiro:discover'),
  removeProject: (dir) => invoke('kiro:removeProject', { dir }),
  readProject: (dir) => invoke('kiro:project', { dir }),
  gitPull: (dir, force) => invoke('git:pull', { dir, force }),
  gitCommitPush: (dir, message) => invoke('git:commitPush', { dir, message }),
  deleteTask: (dir, id) => invoke('kiro:deleteTask', { dir, id }),

  // dir（プロジェクトフォルダ）は run アーカイブの置き場（<dir>/flow-archive/）に使う
  flowRuns: (dir, busDir, limit) => invoke('flow:runs', { dir, busDir, limit }),
  flowRun: (dir, busDir, runId) => invoke('flow:run', { dir, busDir, runId }),
  flowResubmit: (busDir, runId) => invoke('flow:resubmit', { busDir, runId }),
  flowDeleteRun: (busDir, runId) => invoke('flow:deleteRun', { busDir, runId }),
  flowCancel: (busDir, runId, reason) => invoke('flow:cancel', { busDir, runId, reason }),
  glFindIssueByToken: (args) => invoke('gitlab:findIssueByToken', args),

  submitFeedback: (file, feedback) => invoke('kiro:feedback', { file, feedback }),
  enqueueTask: (dir, spec) => invoke('kiro:enqueue', { dir, spec }),
  runAction: (args) => invoke('kiro:action', args),
  requestReplan: (dir, reason) => invoke('kiro:replan', { dir, reason }),
  requestLifecycle: (dir, action, reason) => invoke('kiro:lifecycle', { dir, action, reason }),
  // dir = プロジェクトルート（消す対象）、workspace = 登録フォルダ（バスの解決に使う）
  resetProject: (dir, workspace) => invoke('kiro:reset', { dir, workspace }),

  createProject: (spec) => invoke('kiro:createProject', { spec }),
  promoteCharter: (dir, name) => invoke('kiro:promoteCharter', { dir, name }),
  readProjectFile: (dir, name) => invoke('kiro:readFile', { dir, name }),
  writeProjectFile: (dir, name, content) => invoke('kiro:writeFile', { dir, name, content }),
  charterTemplate: (name) => invoke('kiro:charterTemplate', { name }),
  // フォーム編集（charter / policy / repos を構造化データで読み書き）
  readCharterFields: (dir, name) => invoke('kiro:readCharterFields', { dir, name }),
  writeCharterFields: (dir, name, fields) => invoke('kiro:writeCharterFields', { dir, name, fields }),
  readPolicy: (dir) => invoke('kiro:readPolicy', { dir }),
  writePolicy: (dir, rules) => invoke('kiro:writePolicy', { dir, rules }),
  readRepos: (dir) => invoke('kiro:readRepos', { dir }),
  writeRepos: (dir, rows) => invoke('kiro:writeRepos', { dir, rows }),
  agentCharter: (args) => invoke('agent:charter', args),
  agentResolve: (dir) => invoke('agent:resolve', { dir }),

  glEnrich: (urls) => invoke('gitlab:enrich', { urls }),
  glProjectIssues: (args) => invoke('gitlab:projectIssues', args),
  glReconcileRun: (args) => invoke('gitlab:reconcileRun', args),

  openReview: (target) => invoke('review:open', { target }),
  openExternal: (url) => invoke('shell:openExternal', { url }),
  openPath: (target) => invoke('shell:openPath', { target }),

  // ディープリンク（kiro-projects-viewer://open?...）からの遷移通知
  onOpenTarget: (cb) => ipcRenderer.on('app:openTarget', (_ev, payload) => cb(payload)),
});
