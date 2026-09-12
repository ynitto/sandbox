'use strict';

const { contextBridge, ipcRenderer } = require('electron');

async function invoke(channel, args) {
  const res = await ipcRenderer.invoke(channel, args);
  if (!res || !res.ok) {
    const err = new Error(res && res.error ? res.error : `${channel} が失敗しました`);
    if (res && res.code) err.code = res.code;
    if (res && res.detail) err.detail = res.detail;
    if (res && res.issues) err.issues = res.issues;
    throw err;
  }
  return res.data;
}

const on = (channel) => (cb) => ipcRenderer.on(channel, (_ev, p) => cb(p));

contextBridge.exposeInMainWorld('api', {
  platform: process.platform,
  hostInfo: () => invoke('host:info'),
  getConfig: () => invoke('config:get'),
  saveConfig: (patch) => invoke('config:save', { patch }),
  addRepo: () => invoke('repo:add'),
  removeRepo: (repo) => invoke('repo:remove', { repo }),
  listAgents: (repo) => invoke('agents:list', { repo }),
  listSkills: (repo) => invoke('skills:list', { repo }),
  selectSkills: (repo, text, mode, selected) => invoke('skills:select', { repo, text, mode, selected }),
  listSessions: (repo) => invoke('session:list', { repo }),
  createSession: (payload) => invoke('session:create', payload),
  readSession: (id) => invoke('session:read', { id }),
  updateSession: (id, patch) => invoke('session:update', { id, patch }),
  removeSession: (id) => invoke('session:remove', { id }),
  // 別のリポジトリへ分岐する。payload: { originId, repo, prompt, index? }
  forkSession: (payload) => invoke('session:fork', payload),
  // opts: { policy, cli?, model?, readonly, attachments }。cli/model は direct のときだけ使う。
  send: (id, prompt, opts) => invoke('turn:send', { id, prompt, ...(opts || {}) }),
  pickAttachments: () => invoke('attach:pick'),
  stageAttachment: (name, bytes) => invoke('attach:stage', { name, bytes }),
  discardAttachment: (id) => invoke('attach:discard', { id }),
  openAttachment: (id, name) => invoke('attach:open', { id, name }),
  stop: (id) => invoke('turn:stop', { id }),
  running: () => invoke('turn:running'),
  // 共有（LAN の参加者に依頼を回す）。投函は send の policy: 'shared' で行い、ここは列と参加者の観測・調整だけ
  share: {
    status: () => invoke('share:status'),
    cancel: (id) => invoke('share:cancel', { id }),
    setPriority: (id, priority) => invoke('share:priority', { id, priority }),
    // 引き受け方（'auto' 自動で受ける / 'manual' 選んで受ける / 'off' 受けない）
    setMode: (mode) => invoke('share:mode', { mode }),
    accept: (id) => invoke('share:accept', { id }),
    stopAccepted: (id) => invoke('share:stop', { id }),
    screen: (id) => invoke('share:screen', { id }),
    // 人と人のひとこと（CLI には入らない）と、引き受けた依頼の端末へのキー
    say: (id, text) => invoke('share:say', { id, text }),
    keys: (id, data) => invoke('share:keys', { id, data }),
    onChanged: on('share:changed'),
    // 実行中の端末の画面（引き受けた側から届く分と、自分が引き受けている分）
    onScreen: on('share:screen'),
  },
  termOpen: (id, cols, rows) => invoke('term:open', { id, cols, rows }),
  termRestart: (id, cols, rows) => invoke('term:restart', { id, cols, rows }),
  termState: (id) => invoke('term:state', { id }),
  termWatch: (id) => invoke('term:watch', { id }),
  termUnwatch: (id) => invoke('term:unwatch', { id }),
  termSubmit: (id, text) => invoke('term:submit', { id, text }),
  termKeys: (id, data) => invoke('term:keys', { id, data }),
  termScroll: (id, lines) => invoke('term:scroll', { id, lines }),
  termResize: (id, cols, rows) => invoke('term:resize', { id, cols, rows }),
  termKill: (id) => invoke('term:kill', { id }),
  // opts: { withStatus }。false なら変更数・先行コミット数を数えない（速い）
  listWorktrees: (repo, opts) => invoke('wt:list', { repo, ...(opts || {}) }),
  createWorktree: (repo, branch, base, name) => invoke('wt:create', { repo, branch, base, name }),
  removeWorktree: (repo, name, opts) => invoke('wt:remove', { repo, name, ...(opts || {}) }),
  listDir: (repo, worktree, rel) => invoke('fs:list', { repo, worktree, rel }),
  readFile: (repo, worktree, rel) => invoke('fs:read', { repo, worktree, rel }),
  // opts: { refresh }。true なら名前検索の索引を作り直す。返り値は { hits, truncated, indexed }
  findFiles: (repo, worktree, query, opts) => invoke('fs:find', { repo, worktree, query, ...(opts || {}) }),
  changes: (repo, worktree, scope) => invoke('git:changes', { repo, worktree, scope }),
  fileDiff: (repo, worktree, file, scope) => invoke('git:file', { repo, worktree, file, scope }),
  openFolder: (repo, worktree) => invoke('shell:openFolder', { repo, worktree }),
  openFile: (repo, worktree, rel) => invoke('shell:openFile', { repo, worktree, rel }),
  showFile: (repo, worktree, rel) => invoke('shell:showFile', { repo, worktree, rel }),
  automation: {
    getConfig: () => invoke('automation:config:get'),
    saveConfig: (config) => invoke('automation:config:save', { config }),
    catalog: () => invoke('automation:catalog:get'),
    addRoot: () => invoke('automation:root:add'),
    removeRoot: (root) => invoke('automation:root:remove', { root }),
    selectRoot: (root) => invoke('automation:root:select', { root }),
    listMachines: (root) => invoke('automation:machine:list', { root }),
    readMachine: (root, machine) => invoke('automation:machine:read', { root, machine }),
    machineExists: (root, machine) => invoke('automation:machine:exists', { root, machine }),
    previewMachine: (spec) => invoke('automation:machine:preview', { spec }),
    saveMachine: (root, spec) => invoke('automation:machine:save', { root, spec }),
    updateMachineMetadata: (root, machine, values) => invoke('automation:machine:updateMetadata', { root, machine, values }),
    deleteMachine: (root, machine) => invoke('automation:machine:delete', { root, machine }),
    openMachineFolder: (root, machine) => invoke('automation:machine:openFolder', { root, machine }),
    listAgents: (root) => invoke('automation:agents:list', { root }),
    selectSkills: (root, text, mode, selected) => invoke('automation:skills:select', { root, text, mode, selected }),
    toolStatus: (root) => invoke('automation:tools:status', { root }),
    capabilities: (root) => invoke('automation:capabilities', { root }),
    recordingStart: (payload) => invoke('automation:recording:start', payload),
    recordingStop: (payload) => invoke('automation:recording:stop', payload),
    recordingImport: (payload) => invoke('automation:recording:import', payload),
    recordingSnapshot: (payload) => invoke('automation:recording:snapshot', payload),
    recordingExtract: (payload) => invoke('automation:recording:extract', payload),
    recordingState: () => invoke('automation:recording:state'),
    aiStart: (payload) => invoke('automation:ai:start', payload),
    aiStop: (requestId) => invoke('automation:ai:stop', { requestId }),
    aiApply: (payload) => invoke('automation:ai:apply', payload),
    teachingList: (root) => invoke('automation:teaching:list', { root }),
    // タスクを AI と作る会話（tmux）。会話の送受信は上の send / term* をそのまま使う
    teachPrepare: (payload) => invoke('automation:teach:prepare', payload),
    teachStart: (payload) => invoke('automation:teach:start', payload),
    teachSession: (repo, machine) => invoke('automation:teach:session', { repo, machine }),
    teachDemonstration: (repo, machine, recording, requested) => invoke('automation:teach:demonstration', { repo, machine, recording, requested }),
    // ブラウザの見本: Edge をリモートデバッグ付きで起こす。固定文の送信は send / termSubmit で行う
    teachBrowser: (url) => invoke('automation:teach:browser', { url }),
    teachBrowserPage: () => invoke('automation:teach:browser:page'),
    onAiProgress: on('automation:ai:progress'),
    onAiResult: on('automation:ai:result'),
    flowCatalog: () => invoke('automation:flow:catalog'),
    flowList: (root) => invoke('automation:flow:list', { root }),
    flowRead: (root, id) => invoke('automation:flow:read', { root, id }),
    flowSave: (root, workflow, mode) => invoke('automation:flow:save', { root, workflow, mode }),
    flowDelete: (root, id) => invoke('automation:flow:delete', { root, id }),
    flowPreview: (root, workflow, request, parameters) => invoke('automation:flow:preview', { root, workflow, request, parameters }),
    // ワークフローを AI と作る会話（tmux）。送受信はタスクと同じく send / term* を使う
    flowTeachPrepare: (payload) => invoke('automation:flow:teach:prepare', payload),
    flowTeachStart: (payload) => invoke('automation:flow:teach:start', payload),
    flowTeachSession: (repo, workflowId) => invoke('automation:flow:teach:session', { repo, workflowId }),
    flowTeachAdopt: (repo, workflowId) => invoke('automation:flow:teach:adopt', { repo, workflowId }),
    flowTeachingList: (root) => invoke('automation:flow:teaching:list', { root }),
    flowTeachingRead: (root, workflowId) => invoke('automation:flow:teaching:read', { root, workflowId }),
    flowTeachingSave: (root, workflowId, session) => invoke('automation:flow:teaching:save', { root, workflowId, session }),
    flowTeachingRecordTrial: (root, workflowId, trial) => invoke('automation:flow:teaching:trial', { root, workflowId, trial }),
    flowTeachingConfirm: (root, workflowId, generationId, digest) => invoke('automation:flow:teaching:confirm', { root, workflowId, generationId, digest }),
    flowContext: (root) => invoke('automation:flow:context', { root }),
    flowRunStart: (payload) => invoke('automation:flow:run:start', payload),
    flowRunList: (root, limit) => invoke('automation:flow:run:list', { root, limit }),
    flowRunRead: (root, runId) => invoke('automation:flow:run:read', { root, runId }),
    flowRunCancel: (root, runId, reason) => invoke('automation:flow:run:cancel', { root, runId, reason }),
    flowRunRespond: (root, runId, interactionId, answer) => invoke('automation:flow:run:respond', { root, runId, interactionId, answer }),
    flowRunResult: (root, runId) => invoke('automation:flow:run:result', { root, runId }),
    flowRunLog: (root, runId, bytes) => invoke('automation:flow:run:log', { root, runId, bytes }),
    flowRunDelete: (root, runId) => invoke('automation:flow:run:delete', { root, runId }),
    flowRunOpenDelivery: (root, runId) => invoke('automation:flow:run:openDelivery', { root, runId }),
    runSnapshot: (root) => invoke('automation:run:snapshot', { root }),
    saveRunSchedule: (root, schedule) => invoke('automation:run:schedule', { root, schedule }),
    setRunDaemon: (root, action) => invoke('automation:run:daemon', { root, action }),
    runLog: (root, identity) => invoke('automation:run:log', { root, identity }),
    runStart: (payload) => invoke('automation:run:start', payload),
    runStop: () => invoke('automation:run:stop'),
    onRunLine: on('automation:run:line'),
    onRunExit: on('automation:run:exit'),
  },
  onTurnStarted: on('turn:started'),
  onTurnProgress: on('turn:progress'),
  onTurnInfo: on('turn:info'),
  onTurnLine: on('turn:line'),
  onTurnDone: on('turn:done'),
  onTermScreen: on('term:screen'),
  onTermPhase: on('term:phase'),
});
