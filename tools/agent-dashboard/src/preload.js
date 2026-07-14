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

  discover: () => invoke('dashboard:discover'),
  removeProject: (dir) => invoke('dashboard:removeProject', { dir }),
  readProject: (dir) => invoke('dashboard:project', { dir }),
  gitPull: (dir, force) => invoke('git:pull', { dir, force }),
  gitCommitPush: (dir, message, paths) => invoke('git:commitPush', { dir, message, paths }),
  gitHealth: (dir) => invoke('git:health', { dir }),
  gitHeal: (dir) => invoke('git:heal', { dir }),
  gitDiff: (args) => invoke('git:diff', args),
  deleteTask: (dir, id) => invoke('dashboard:deleteTask', { dir, id }),

  // dir（プロジェクトフォルダ）は run アーカイブの置き場（<dir>/flow-archive/）に使う
  flowRuns: (dir, busDir, limit) => invoke('flow:runs', { dir, busDir, limit }),
  flowRun: (dir, busDir, runId) => invoke('flow:run', { dir, busDir, runId }),
  // dir（プロジェクトルート）も渡す: agent-project 配下の run は「タスクの積み直し」で再実行する
  flowResubmit: (dir, busDir, runId) => invoke('flow:resubmit', { dir, busDir, runId }),
  flowDeleteRun: (dir, busDir, runId) => invoke('flow:deleteRun', { dir, busDir, runId }),
  flowCancel: (dir, busDir, runId, reason) =>
    invoke('flow:cancel', { dir, busDir, runId, reason }),
  glFindIssueByToken: (args) => invoke('gitlab:findIssueByToken', args),

  submitFeedback: (file, feedback, stub) => invoke('dashboard:feedback', { file, feedback, stub }),
  enqueueTask: (dir, spec) => invoke('dashboard:enqueue', { dir, spec }),
  runAction: (args) => invoke('dashboard:action', args),
  requestReplan: (dir, reason) => invoke('dashboard:replan', { dir, reason }),
  requestLifecycle: (dir, action, reason) => invoke('dashboard:lifecycle', { dir, action, reason }),
  startProject: (dir) => invoke('dashboard:start', { dir }),
  // dir = プロジェクトルート（消す対象）、workspace = 登録フォルダ（バスの解決に使う）
  resetProject: (dir, workspace) => invoke('dashboard:reset', { dir, workspace }),

  createProject: (spec) => invoke('dashboard:createProject', { spec }),
  promoteCharter: (dir, name) => invoke('dashboard:promoteCharter', { dir, name }),
  readProjectFile: (dir, name) => invoke('dashboard:readFile', { dir, name }),
  writeProjectFile: (dir, name, content) => invoke('dashboard:writeFile', { dir, name, content }),
  charterTemplate: (name) => invoke('dashboard:charterTemplate', { name }),
  // フォーム編集（charter / policy / repos を構造化データで読み書き）
  readCharterFields: (dir, name) => invoke('dashboard:readCharterFields', { dir, name }),
  writeCharterFields: (dir, name, fields) => invoke('dashboard:writeCharterFields', { dir, name, fields }),
  readPolicy: (dir) => invoke('dashboard:readPolicy', { dir }),
  writePolicy: (dir, rules) => invoke('dashboard:writePolicy', { dir, rules }),
  readRepos: (dir) => invoke('dashboard:readRepos', { dir }),
  writeRepos: (dir, rows) => invoke('dashboard:writeRepos', { dir, rows }),
  agentCharter: (args) => invoke('agent:charter', args),
  agentDoctor: (args) => invoke('agent:doctor', args),
  agentResolve: (dir) => invoke('agent:resolve', { dir }),

  glEnrich: (urls) => invoke('gitlab:enrich', { urls }),
  glProjectIssues: (args) => invoke('gitlab:projectIssues', args),
  glReconcileRun: (args) => invoke('gitlab:reconcileRun', args),

  openReview: (target) => invoke('review:open', { target }),
  openExternal: (url) => invoke('shell:openExternal', { url }),
  openPath: (target) => invoke('shell:openPath', { target }),

  // ディープリンク（agent-dashboard://open?...）からの遷移通知
  onOpenTarget: (cb) => ipcRenderer.on('app:openTarget', (_ev, payload) => cb(payload)),
});
