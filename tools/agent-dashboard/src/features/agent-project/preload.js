'use strict';

// agent-project 制御面が preload 経由で window.api に載せる表面。
// base の preload が Object.assign で合成する。

module.exports = {
  discover: (invoke) => () => invoke('dashboard:discover'),
  removeProject: (invoke) => (dir) => invoke('dashboard:removeProject', { dir }),
  readProject: (invoke) => (dir) => invoke('dashboard:project', { dir }),
  deleteTask: (invoke) => (dir, id) => invoke('dashboard:deleteTask', { dir, id }),

  // dir（プロジェクトフォルダ）は run アーカイブの置き場（<dir>/flow-archive/）に使う
  flowRuns: (invoke) => (dir, busDir, limit) => invoke('flow:runs', { dir, busDir, limit }),
  flowRun: (invoke) => (dir, busDir, runId) => invoke('flow:run', { dir, busDir, runId }),
  // dir（プロジェクトルート）も渡す: agent-project 配下の run は「タスクの積み直し」で再実行する
  flowResubmit: (invoke) => (dir, busDir, runId) => invoke('flow:resubmit', { dir, busDir, runId }),
  flowDeleteRun: (invoke) => (dir, busDir, runId) => invoke('flow:deleteRun', { dir, busDir, runId }),
  flowCancel: (invoke) => (dir, busDir, runId, reason) =>
    invoke('flow:cancel', { dir, busDir, runId, reason }),
  glFindIssueByToken: (invoke) => (args) => invoke('gitlab:findIssueByToken', args),

  submitFeedback: (invoke) => (file, feedback, stub) =>
    invoke('dashboard:feedback', { file, feedback, stub }),
  enqueueTask: (invoke) => (dir, spec) => invoke('dashboard:enqueue', { dir, spec }),
  runAction: (invoke) => (args) => invoke('dashboard:action', args),
  requestReplan: (invoke) => (dir, reason) => invoke('dashboard:replan', { dir, reason }),
  requestLifecycle: (invoke) => (dir, action, reason) =>
    invoke('dashboard:lifecycle', { dir, action, reason }),
  startProject: (invoke) => (dir) => invoke('dashboard:start', { dir }),
  // dir = プロジェクトルート（消す対象）、workspace = 登録フォルダ（バスの解決に使う）
  resetProject: (invoke) => (dir, workspace) => invoke('dashboard:reset', { dir, workspace }),

  createProject: (invoke) => (spec) => invoke('dashboard:createProject', { spec }),
  promoteCharter: (invoke) => (dir, name) => invoke('dashboard:promoteCharter', { dir, name }),
  readProjectFile: (invoke) => (dir, name) => invoke('dashboard:readFile', { dir, name }),
  writeProjectFile: (invoke) => (dir, name, content) =>
    invoke('dashboard:writeFile', { dir, name, content }),
  charterTemplate: (invoke) => (name) => invoke('dashboard:charterTemplate', { name }),
  // フォーム編集（charter / policy / repos を構造化データで読み書き）
  readCharterFields: (invoke) => (dir, name) => invoke('dashboard:readCharterFields', { dir, name }),
  writeCharterFields: (invoke) => (dir, name, fields) =>
    invoke('dashboard:writeCharterFields', { dir, name, fields }),
  readPolicy: (invoke) => (dir) => invoke('dashboard:readPolicy', { dir }),
  writePolicy: (invoke) => (dir, rules) => invoke('dashboard:writePolicy', { dir, rules }),
  readRepos: (invoke) => (dir) => invoke('dashboard:readRepos', { dir }),
  writeRepos: (invoke) => (dir, rows) => invoke('dashboard:writeRepos', { dir, rows }),
  agentCharter: (invoke) => (args) => invoke('agent:charter', args),
  agentDoctor: (invoke) => (args) => invoke('agent:doctor', args),
  agentTaskAssist: (invoke) => (args) => invoke('agent:taskAssist', args),
  agentPlanAdjustments: (invoke) => (args) => invoke('agent:planAdjustments', args),
  agentResolve: (invoke) => (dir) => invoke('agent:resolve', { dir }),

  glReconcileRun: (invoke) => (args) => invoke('gitlab:reconcileRun', args),

  openReview: (invoke) => (target) => invoke('review:open', { target }),
};
