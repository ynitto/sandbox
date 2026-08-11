'use strict';

// agent-project 制御面が preload 経由で window.api に載せる表面。
// base の preload が Object.assign で合成する。

module.exports = {
  discover: (invoke) => () => invoke('dashboard:discover'),
  // 稼働状況（engine/status.json）。プロジェクト発見・同期の状況表示の唯一の根拠
  engineStatus: (invoke) => () => invoke('engine:status'),
  requestHeal: (invoke) => (dir) => invoke('engine:heal', { dir }),
  setupDiagnostics: (invoke) => () => invoke('setup:diagnostics'),
  readProject: (invoke) => (dir) => invoke('dashboard:project', { dir }),
  deleteTask: (invoke) => (dir, id, reason) => invoke('dashboard:deleteTask', { dir, id, reason }),
  // 墓標の解除（削除＝却下の取り消し）。同じ題のタスクを入れ直せる状態へ戻す
  reviveTombstone: (invoke) => (dir, title, charter) =>
    invoke('dashboard:revive', { dir, title, charter }),

  // dir（プロジェクトフォルダ）は run アーカイブの置き場（<dir>/flow-archive/）に使う
  flowRuns: (invoke) => (dir, busDir, limit) => invoke('flow:runs', { dir, busDir, limit }),
  flowRun: (invoke) => (dir, busDir, runId) => invoke('flow:run', { dir, busDir, runId }),
  flowInteractionResponse: (invoke) => (busDir, runId, interactionId, answer) =>
    invoke('flow:interactionResponse', { busDir, runId, interactionId, answer }),
  // dir（プロジェクトルート）も渡す: agent-project 配下の run は「タスクの積み直し」で再実行する
  flowResubmit: (invoke) => (dir, busDir, runId) => invoke('flow:resubmit', { dir, busDir, runId }),
  flowDeleteRun: (invoke) => (dir, busDir, runId) => invoke('flow:deleteRun', { dir, busDir, runId }),
  flowCancel: (invoke) => (dir, busDir, runId, reason) =>
    invoke('flow:cancel', { dir, busDir, runId, reason }),
  glFindIssueByToken: (invoke) => (args) => invoke('gitlab:findIssueByToken', args),

  // 監視担当の割り当て（assignments.json）。空の owner で解除
  setTaskOwner: (invoke) => (dir, id, owner) => invoke('dashboard:setOwner', { dir, id, owner }),
  // 成果物レビューのコメント（reviews/<task-id>/）。複数メンバーで投稿・整理する
  addReviewComment: (invoke) => (dir, taskId, author, text) =>
    invoke('dashboard:addComment', { dir, taskId, author, text }),
  editReviewComment: (invoke) => (dir, taskId, commentId, text) =>
    invoke('dashboard:editComment', { dir, taskId, commentId, text }),
  deleteReviewComment: (invoke) => (dir, taskId, commentId) =>
    invoke('dashboard:deleteComment', { dir, taskId, commentId }),
  submitFeedback: (invoke) => (file, feedback, stub) =>
    invoke('dashboard:feedback', { file, feedback, stub }),
  enqueueTask: (invoke) => (dir, spec) => invoke('dashboard:enqueue', { dir, spec }),
  lintTask: (invoke) => (spec) => invoke('dashboard:lintTask', { spec }),
  runAction: (invoke) => (args) => invoke('dashboard:action', args),
  requestReplan: (invoke) => (dir, reason, charter) => invoke('dashboard:replan', { dir, reason, charter }),
  listNotes: (invoke) => (dir) => invoke('dashboard:listNotes', { dir }),
  writeNote: (invoke) => (dir, name, body) => invoke('dashboard:writeNote', { dir, name, body }),
  updateNote: (invoke) => (dir, name, body, mtime) =>
    invoke('dashboard:updateNote', { dir, name, body, mtime }),
  markNoteBlocks: (invoke) => (dir, name, links) =>
    invoke('dashboard:markNoteBlocks', { dir, name, links }),
  pickBacklogDocument: (invoke) => () => invoke('dashboard:pickBacklogDocument'),
  distillNotes: (invoke) => (dir, charter) => invoke('dashboard:distillNotes', { dir, charter }),
  requestLifecycle: (invoke) => (dir, action, reason) =>
    invoke('dashboard:lifecycle', { dir, action, reason }),
  // dir = プロジェクトルート（消す対象）、workspace = 登録フォルダ（バスの解決に使う）
  resetProject: (invoke) => (dir, workspace) => invoke('dashboard:reset', { dir, workspace }),

  createProject: (invoke) => (spec) => invoke('dashboard:createProject', { spec }),
  promoteCharter: (invoke) => (dir, name) => invoke('dashboard:promoteCharter', { dir, name }),
  deleteCharter: (invoke) => (dir, name) => invoke('dashboard:deleteCharter', { dir, name }),
  readProjectFile: (invoke) => (dir, name) => invoke('dashboard:readFile', { dir, name }),
  writeProjectFile: (invoke) => (dir, name, content) =>
    invoke('dashboard:writeFile', { dir, name, content }),
  charterTemplate: (invoke) => (name, version) => invoke('dashboard:charterTemplate', { name, version }),
  // フォーム編集（charter / policy / repos を構造化データで読み書き）
  readCharterFields: (invoke) => (dir, name) => invoke('dashboard:readCharterFields', { dir, name }),
  writeCharterFields: (invoke) => (dir, name, fields) =>
    invoke('dashboard:writeCharterFields', { dir, name, fields }),
  readPolicy: (invoke) => (dir) => invoke('dashboard:readPolicy', { dir }),
  writePolicy: (invoke) => (dir, rules) => invoke('dashboard:writePolicy', { dir, rules }),
  readRepos: (invoke) => (dir) => invoke('dashboard:readRepos', { dir }),
  writeRepos: (invoke) => (dir, rows) => invoke('dashboard:writeRepos', { dir, rows }),
  agentCharter: (invoke) => (args) => invoke('agent:charter', args),
  agentMethodDraft: (invoke) => (args) => invoke('agent:methodDraft', args),
  agentRoutineAcceptanceDraft: (invoke) => (args) => invoke('agent:routineAcceptanceDraft', args),
  agentDoctor: (invoke) => (args) => invoke('agent:doctor', args),
  agentDoctorChat: (invoke) => (args) => invoke('agent:doctorChat', args),
  agentTaskAssist: (invoke) => (args) => invoke('agent:taskAssist', args),
  agentPlanAdjustments: (invoke) => (args) => invoke('agent:planAdjustments', args),
  agentResolve: (invoke) => (dir) => invoke('agent:resolve', { dir }),
  agentOpenChat: (invoke) => (args) => invoke('agent:openChat', args || {}),
  agentChatCwdChoices: (invoke) => (dir) => invoke('agent:chatCwdChoices', { dir }),

  glReconcileRun: (invoke) => (args) => invoke('gitlab:reconcileRun', args),

  openReview: (invoke) => (target) => invoke('review:open', { target }),
};
