'use strict';

const { ipcMain, dialog, shell, app } = require('electron');
const { randomUUID } = require('crypto');
const fs = require('fs');
const path = require('path');
const model = require('./model');
const store = require('./store');
const recording = require('./recording');
const tools = require('./tools');
const runner = require('./runner');
const ai = require('./ai');
const aiDiff = require('./ai-diff');
const agentLoop = require('./agent-loop');
const directRun = require('./direct-run');
const sessionRun = require('./session-run');
const taskInputs = require('./task-inputs');
const flowModel = require('./flow-model');
const flowStore = require('./flow-store');
const agentFlow = require('./agent-flow');
const flowTeaching = require('./flow-teaching-model');
const flowTeachingStore = require('./flow-teaching-store');
const teaching = require('./teaching');
const terminalText = require('../text');

// 定義が宣言している確認コマンドの数。1 セッションで通す経路はこれを実行しないので、
// 宣言があるときだけ「足りないもの」を 1 行で言う（黙ると検査したつもりで受け取られる）。
function declaredChecks(root, machine) {
  try {
    const spec = store.read(root, String(machine || ''));
    const steps = spec && Array.isArray(spec.steps) ? spec.steps : [];
    return steps.filter((step) => step && String(step.check || '').trim()).length;
  } catch {
    return 0;   // 読めない定義は黙る（実行そのものは別の口が断る）
  }
}

// このアプリの置き場（appRoot/../../.github/skills/statemachine-use を最後の候補にする）。
const APP_ROOT = path.join(__dirname, '..', '..', '..');

function userData() {
  return app.getPath('userData');
}

// すべてのハンドラを {ok, data|error} に揃える。
function handle(channel, fn) {
  ipcMain.handle(channel, async (event, args) => {
    try {
      return { ok: true, data: await fn(args || {}, event) };
    } catch (err) {
      return {
        ok: false,
        error: err && err.message ? err.message : String(err),
        ...(err && err.code ? { code: err.code } : {}),
        ...(err && err.detail ? { detail: err.detail } : {}),
        ...(err && err.issues ? { issues: err.issues } : {}),
      };
    }
  });
}

// 手動実行を自動承認で回すときだけ、harness のシェル拒否から powershell.exe を外す。
// WSL から Windows 側の CLI（playwright-cli など）を起動する経路がここしか無く、その間は
// 利用者が画面の前にいて結果を見ている。許可はその 1 実行のコマンド引数として渡す
// ——環境に置くと、誰がいつ渡したのかが実行の外へ散る。構成確認や、承認を挟む実行には
// 渡さないので、harness の既定（シェルは全部拒否）がそのまま効く。
const RUN_ALLOWED_SHELL = 'powershell.exe';

function allowedShellsFor(mode, payload) {
  return mode === 'run' && payload.autoApprove ? [RUN_ALLOWED_SHELL] : [];
}

function skillDirFor(root, settings, getUserData = userData, appRoot = APP_ROOT) {
  return tools.findSkillDir({ root, configured: settings.load(getUserData()).skillDir, appRoot });
}

// 触ってよいのは**登録したフォルダだけ**。登録に無いパスは、実在していても断る。
function requireRoot(payload, settings, getUserData = userData) {
  const root = String(payload.root || '').trim();
  if (!root) throw new Error('フォルダを選んでください');
  if (!settings.isRegistered(getUserData(), root)) throw new Error('登録していないフォルダです');
  if (!tools.isDir(root)) throw new Error('フォルダが見つかりません');
  return root;
}

function pythonFor() {
  return tools.findPython(runner.capture);
}

function registerIpcHandlers(getWindow, options = {}) {
  let activeAi = null;
  const channelPrefix = String(options.channelPrefix || '');
  const settings = options.config;
  if (!settings || typeof settings.load !== 'function') throw new Error('設定の窓口（config adapter）が要ります');
  const getUserData = options.userData || userData;
  const appRoot = options.appRoot || APP_ROOT;
  const channel = (name) => `${channelPrefix}${name}`;
  const register = (name, fn) => handle(channel(name), fn);
  const selectedRoot = (payload) => requireRoot(payload, settings, getUserData);
  const selectedSkillDir = (root) => skillDirFor(root, settings, getUserData, appRoot);
  // 使える AI の名前の並び。既定は agent-herd に聞く。埋め込む側（agent-app）は仮想の
  // 名前（`herd`）を足した並びへ差し替えられる。
  const agentDefinitions = typeof options.agentDefinitions === 'function' ? options.agentDefinitions : tools.agentDefinitions;
  // コマンド名ごとの起動仕様の差し替え（既定は runner.js の command.spawnSpec のまま）。
  // 埋め込む側（agent-app）は、Windows で agent-herd / agent-loop / agent-flow だけを
  // WSL のログインシェル経由に載せ替えるのに使う。python や playwright-cli、winauto の
  // ような診断コマンドは対象にしない（この端末でそのまま探す）。
  const commandSpawnSpec = typeof options.commandSpawnSpec === 'function' ? options.commandSpawnSpec : () => undefined;
  // 登録したフォルダのパスを、実行基盤が動くホストから見た表記へ直す（既定は素通し）。
  // Windows から WSL の agent-flow を起こす構成で、bus へ書く workspace.local と、
  // そこから実行を見分ける突き合わせに使う。
  const hostPath = typeof options.hostPath === 'function' ? options.hostPath : (value) => String(value || '');
  const hostRootOf = (payload) => hostPath(selectedRoot(payload));
  const runCapture = (name, args, opts = {}) => runner.capture(name, args, { ...opts, spawnSpec: commandSpawnSpec(name) });
  // host … 一族の名前でなくても、Windows では WSL 経由で起こす（エージェント CLI を直接起こすとき）
  const runStream = (name, args, { host: onHost = false, ...opts } = {}) => runner.stream(name, args, { ...opts, spawnSpec: commandSpawnSpec(name, { host: onHost }) });
  const runStartDetached = (name, args, opts = {}) => runner.startDetached(name, args, { ...opts, spawnSpec: commandSpawnSpec(name) });
  // 選ばれた名前を、実際に起こす定義の名前へ写す（`herd` → aider / ollama）。無ければそのまま。
  //   purpose … 'task'（タスクの実行）| 'flow'（ワークフローの実行）| 'plan'（AI 支援。読み取り専用）
  //             | 'direct'（agent-loop の無いときの手動実行。定義を名指しして直接起こす）
  // 返る名前が '' なら「渡さない」（agent-loop / agent-herd の既定に任せる）。
  const resolveAgent = async (agent, purpose, root) => {
    const hook = options.hooks && options.hooks.resolveAgent;
    if (typeof hook !== 'function') return agent;
    const resolved = await hook({ root, agent, purpose });
    const name = resolved && typeof resolved === 'object' ? resolved.agent : resolved;
    return name == null ? agent : String(name);
  };

  function sendTo(sender, channel, payload) {
    if (sender && !sender.isDestroyed()) sender.send(`${channelPrefix}${channel}`, payload);
  }

  function finishAi(job, payload) {
    if (!activeAi || activeAi.requestId !== job.requestId || job.cancelled) return;
    activeAi = null;
    sendTo(job.sender, 'ai:result', { requestId: job.requestId, mode: job.mode, ...payload });
  }

  // AI 支援の起動仕様。埋め込む側（agent-app）は hooks.assistRunSpec で差し替える——定義から
  // 組んだ単発 argv でその CLI を直接起こし、agent-herd（agent-tools）が無くても動かす。
  // 既定は agent-herd の `--purpose plan`。
  const assistRunSpec = (payload) => (
    options.hooks && typeof options.hooks.assistRunSpec === 'function'
      ? options.hooks.assistRunSpec(payload)
      : tools.agentAssistRunSpec(payload)
  );

  function readOutputFile(file) {
    if (!file) return '';
    let text = '';
    try { text = fs.readFileSync(file, 'utf8'); } catch { /* 書かれなかった */ }
    try { fs.unlinkSync(file); } catch { /* 無ければよい */ }
    return text;
  }

  function launchAi(job, prompt) {
    const spec = assistRunSpec({
      root: job.root, agent: job.agent, model: job.model, prompt,
    });
    const started = runStream(spec.command, spec.args, {
      cwd: job.root,
      kind: 'ai',
      host: !!spec.host,
      input: spec.input || '',
      maxBytes: runner.MAX_STREAM_OUTPUT,
      env: { ...process.env, ...(spec.env || {}), PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      onExit: ({ code, stdout: rawStdout, stderr, truncated }) => {
        if (!activeAi || activeAi.requestId !== job.requestId || job.cancelled) return;
        const raw = spec.outputFile ? readOutputFile(spec.outputFile) : rawStdout;
        const stdout = typeof spec.extract === 'function' ? spec.extract(raw) : raw;
        try {
          if (code !== 0) throw new Error((stderr || `${spec.command} が終了コード ${code} で終了しました`).trim());
          if (truncated) throw new Error('AIの応答が大きすぎます');
          const result = ai.parseEnvelope(stdout, { mode: job.mode, baseSpec: job.baseSpec, scope: job.scope });
          const changes = result.candidate && job.mode === 'review'
            ? aiDiff.diff(job.baseSpec, result.candidate)
            : [];
          finishAi(job, {
            ok: true,
            result: { ...result, changes, baseFingerprint: job.baseSpec ? ai.fingerprint(job.baseSpec) : '' },
          });
        } catch (err) {
          if (job.attempt === 0) {
            job.attempt = 1;
            sendTo(job.sender, 'ai:progress', {
              requestId: job.requestId, mode: job.mode, phase: 'repair', message: '応答形式を修正しています…',
            });
            try {
              launchAi(job, ai.repairPrompt({ originalPrompt: job.prompt, output: stdout || stderr, error: err.message }));
            } catch (retryError) {
              finishAi(job, { ok: false, error: retryError.message });
            }
            return;
          }
          finishAi(job, { ok: false, error: `AIの提案を読み取れませんでした: ${err.message}` });
        }
      },
    });
    return started;
  }

  register('config:get', () => settings.load(getUserData()));
  register('config:save', (p) => settings.save(getUserData(), p.config));
  register('catalog:get', () => ({ kinds: model.catalog(), version: model.PROCEDURE_VERSION, platform: process.platform }));

  // フォルダの登録。**見に行くのは登録したフォルダの `.statemachine/` だけ**で、
  // 画面から届いたパスをそのまま開かない（登録に無ければ下の requireRoot が断る）。
  register('root:add', async () => {
    const res = await dialog.showOpenDialog(getWindow(), {
      properties: ['openDirectory'], title: 'ステートマシンを置くフォルダを登録する',
    });
    if (res.canceled || !res.filePaths.length) return null;
    return settings.addRoot(getUserData(), res.filePaths[0]);
  });
  register('root:remove', (p) => settings.removeRoot(getUserData(), p.root));
  register('root:select', (p) => settings.save(getUserData(), { lastRoot: String(p.root || '') }));

  register('machine:list', (p) => store.list(selectedRoot(p)));
  register('machine:read', (p) => store.read(selectedRoot(p), String(p.machine || '')));
  register('machine:exists', (p) => store.exists(selectedRoot(p), String(p.machine || '')));
  register('machine:preview', (p) => {
    // 保存せずにコンパイルの結果だけ返す。検証エラーは投げずに一覧で返す（画面に並べる）。
    let spec;
    try { spec = model.normalizeProcedure(p.spec); } catch (err) { return { errors: [err.message], files: {}, warnings: [] }; }
    const { workflow, files } = model.compile(spec);
    return { spec, files, errors: model.validateWorkflow(workflow, files), warnings: model.portabilityWarnings(spec) };
  });
  register('machine:save', (p) => {
    const root = selectedRoot(p);
    const res = store.save(root, p.spec);
    return { dir: res.dir, written: res.written, warnings: res.warnings, machine: res.spec.machine };
  });
  register('machine:delete', (p) => store.remove(selectedRoot(p), String(p.machine || '')));
  register('machine:updateMetadata', (p) => {
    const root = selectedRoot(p);
    const before = String(p.machine || '');
    const result = store.updateMetadata(root, before, p.values || {});
    taskInputs.renameReferences(root, before, result.machine);
    return result;
  });
  register('machine:openFolder', (p) => {
    const root = selectedRoot(p);
    return shell.openPath(store.machineDir(root, String(p.machine || '')));
  });

  // タスクの下書き（AI との tmux 会話で作っている途中で、まだ定義の無いもの）。作成・変更の会話
  // そのものは agent-app の会話基盤（automation:teach:*）が担う。
  register('teaching:list', (p) => teaching.list(selectedRoot(p)));

  register('tools:status', (p) => {
    const root = p.root ? selectedRoot(p) : '';
    return tools.toolStatus({
      cwd: root, capture: runCapture, skillDir: selectedSkillDir(root),
      agentDefinitions: () => agentDefinitions({ cwd: root, capture: runCapture }),
    });
  });
  register('agents:list', (p) => {
    const root = p.root ? selectedRoot(p) : '';
    return agentDefinitions({ cwd: root, capture: runCapture });
  });
  // 任意の道具の有無（画面が使えない機能を薄くするための 1 つの答え。会話画面とタスク画面が同じものを見る）。
  //   herd      … ローカル実行系（agent-herd の一族）が使える
  //   agentLoop … 定期実行と履歴（agent-loop）
  //   agentFlow … ワークフロー（agent-flow）
  // 起動を伴うので 60 秒覚える（tools:status と違い、診断の文言は持たない）。
  register('capabilities', (p) => {
    const root = p.root ? selectedRoot(p) : '';
    return tools.capabilities({
      cwd: root, capture: runCapture,
      agentDefinitions: () => agentDefinitions({ cwd: root, capture: runCapture }),
      flowAvailable: () => agentFlow.patterns(runCapture, root).then((found) => !!found.ok),
    });
  });

  // 複数 AI のワークフロー。定義は root 内、実行状態は agent-flow の共有 bus が正典。
  register('flow:catalog', () => agentFlow.catalog(runCapture));
  register('flow:list', (p) => flowStore.list(selectedRoot(p)));
  register('flow:read', (p) => flowStore.read(selectedRoot(p), p.id));
  register('flow:save', (p) => flowStore.save(selectedRoot(p), p.workflow, p.mode));
  register('flow:delete', (p) => flowStore.remove(selectedRoot(p), p.id));
  register('flow:preview', (p) => {
    selectedRoot(p);
    return flowModel.preview(p.workflow, p.request, p.parameters);
  });
  register('flow:teaching:list', (p) => flowTeachingStore.list(selectedRoot(p)));
  register('flow:teaching:read', (p) => flowTeachingStore.load(selectedRoot(p), String(p.workflowId || '')));
  register('flow:teaching:save', (p) => flowTeachingStore.save(selectedRoot(p), String(p.workflowId || ''), p.session));
  register('flow:teaching:trial', (p) => {
    const root = selectedRoot(p);
    const workflowId = String(p.workflowId || '');
    return flowTeachingStore.save(root, workflowId,
      flowTeaching.recordTrial(flowTeachingStore.load(root, workflowId), p.trial));
  });
  register('flow:teaching:confirm', (p) => {
    const root = selectedRoot(p);
    const workflowId = String(p.workflowId || '');
    const session = flowTeaching.confirmReady(
      flowTeachingStore.load(root, workflowId), p.generationId, p.digest,
    );
    const generation = session.generations.find((item) => item.id === session.activeGenerationId);
    if (!generation || !generation.workflow) throw new Error('利用可能にする生成内容がありません');
    const exists = flowStore.list(root).some((item) => item.id === generation.workflow.id);
    const saved = flowStore.save(root, generation.workflow, exists ? 'update' : 'create');
    if (!saved.saved) throw new Error('候補を保存できません');
    return flowTeachingStore.save(root, workflowId, session);
  });
  register('flow:context', async (p) => {
    const root = selectedRoot(p);
    const cfg = settings.load(getUserData());
    const result = await agentFlow.context({
      root, capture: runCapture, agentDefinitions,
      defaults: { agent: cfg.agent, model: cfg.model },
    });
    result.capabilities.openDelivery = !!(options.hooks && options.hooks.openDelivery);
    return result;
  });
  register('flow:run:start', async (p) => {
    const root = selectedRoot(p);
    const cfg = settings.load(getUserData());
    const getContext = () => agentFlow.context({
      root, capture: runCapture, agentDefinitions,
      defaults: { agent: cfg.agent, model: cfg.model },
    });
    // agent-flow に渡す `--agent-cli` は実在の定義名でなければならない（`herd` は写してから）
    const requestedAgent = String(p.agent || cfg.agent || '');
    const agent = requestedAgent ? await resolveAgent(requestedAgent, 'flow', root) : '';
    return agentFlow.start({ ...p, agent }, { root, getContext, startDetached: runStartDetached, hostPath });
  });
  register('flow:run:list', (p) => agentFlow.listRuns(selectedRoot(p), p.limit, hostRootOf(p)));
  register('flow:run:read', (p) => agentFlow.readRun(selectedRoot(p), p.runId, hostRootOf(p)));
  register('flow:run:cancel', (p) => agentFlow.cancel(selectedRoot(p), p.runId, p.reason, runCapture, hostRootOf(p)));
  register('flow:run:respond', (p) => agentFlow.respond(selectedRoot(p), p.runId, p.interactionId, p.answer, hostRootOf(p)));
  register('flow:run:result', (p) => agentFlow.result(selectedRoot(p), p.runId, runCapture, hostRootOf(p)));
  register('flow:run:log', (p) => agentFlow.readLog(selectedRoot(p), p.runId, p.bytes, hostRootOf(p)));
  register('flow:run:delete', (p) => agentFlow.deleteRun(selectedRoot(p), p.runId, hostRootOf(p)));
  register('flow:run:openDelivery', (p) => agentFlow.openDelivery(
    selectedRoot(p), p.runId, options.hooks && options.hooks.openDelivery, hostRootOf(p),
  ));
  register('run:snapshot', async (p) => {
    const root = selectedRoot(p);
    return taskInputs.enrichSnapshot(root, await agentLoop.inspect({ root, capture: runCapture }));
  });
  register('run:schedule', (p) => agentLoop.saveSchedule({
    root: selectedRoot(p), payload: p.schedule, capture: runCapture,
  }));
  register('run:daemon', (p) => {
    const root = selectedRoot(p);
    if (!['start', 'stop'].includes(p.action)) throw new Error('自動実行の操作が不正です');
    return p.action === 'stop'
      ? agentLoop.stopDaemon({ root, capture: runCapture })
      : agentLoop.startDaemon({ root, startDetached: runStartDetached });
  });
  register('run:log', (p) => agentLoop.readLog({
    root: selectedRoot(p), identity: p.identity, capture: runCapture,
  }));
  register('skills:select', async (p) => {
    if (options.hooks && options.hooks.selectSkills) return options.hooks.selectSkills(p);
    return { mode: p.mode || 'off', requested: [], selected: [], omitted: [] };
  });

  register('recording:start', (p) => {
    const root = p.root ? selectedRoot(p) : '';
    if (p.source === 'windows') {
      return recording.recordWindowsStart({ cwd: root, app: p.app, spawnRecorder: runner.spawnRecorder });
    }
    return recording.recordBrowserStart({ cwd: root, url: p.url, capture: runner.capture });
  });
  register('recording:stop', (p) => {
    const root = p.root ? selectedRoot(p) : '';
    return p.source === 'windows'
      ? recording.recordWindowsStop({})
      : recording.recordBrowserStop({ cwd: root, url: p.url, capture: runner.capture });
  });
  register('recording:import', (p) => recording.stepsFromRecording({ source: p.source, text: p.text, url: p.url, app: p.app }));
  register('recording:snapshot', (p) => recording.recordBrowserSnapshot({ cwd: p.root ? selectedRoot(p) : '', capture: runner.capture }));
  register('recording:extract', (p) => recording.recordBrowserExtract({
    cwd: p.root ? selectedRoot(p) : '', ref: p.ref, mode: p.mode, key: p.key, capture: runner.capture,
  }));
  register('recording:state', () => ({ windows: recording.windowsRecordingState(), browser: recording.browserRecordingState() }));

  register('ai:start', async (p, event) => {
    const root = selectedRoot(p);
    const mode = p.mode === 'review' ? 'review' : 'draft';
    const cfg = settings.load(getUserData());
    const requestedAgent = String(p.agent || cfg.agent || '');
    if (!requestedAgent) throw new Error('使う AI を選んでください（「実行環境」で確認できます）');
    const definitions = await agentDefinitions({ cwd: root, capture: runCapture });
    if (!definitions.includes(requestedAgent)) throw new Error(`使う AI「${requestedAgent}」はこの環境で使えません`);
    const agent = await resolveAgent(requestedAgent, 'plan', root);
    if (runner.isRunning()) throw new Error('別の実行が進行中です。終わるか停止してから始めてください');

    let baseSpec = null;
    let scope = { type: 'workflow' };
    let prompt;
    if (mode === 'review') {
      baseSpec = model.normalizeProcedure(p.spec);
      scope = ai.normalizeScope(p.scope, baseSpec);
      prompt = ai.reviewPrompt({ spec: baseSpec, scope, focus: p.focus, history: p.history });
    } else {
      const request = String(p.request || '').trim();
      if (!request) throw new Error('作りたいものを入力してください');
      prompt = ai.draftPrompt({ request, history: p.history });
    }
    const job = {
      requestId: randomUUID(), sender: event.sender, root, mode, baseSpec, scope, prompt,
      agent, model: String(p.model || cfg.model || ''), attempt: 0, cancelled: false,
    };
    activeAi = job;
    sendTo(job.sender, 'ai:progress', { requestId: job.requestId, mode: job.mode, phase: 'thinking', message: 'AIが検討しています…' });
    try {
      const started = launchAi(job, prompt);
      return { requestId: job.requestId, pid: started.pid };
    } catch (err) {
      if (activeAi && activeAi.requestId === job.requestId) activeAi = null;
      throw err;
    }
  });
  register('ai:stop', (p) => {
    if (!activeAi || (p.requestId && p.requestId !== activeAi.requestId)) return false;
    const job = activeAi;
    job.cancelled = true;
    activeAi = null;
    const stopped = runner.stop('ai');
    sendTo(job.sender, 'ai:result', { requestId: job.requestId, mode: job.mode, ok: false, cancelled: true, error: '中止しました' });
    return stopped;
  });
  register('ai:apply', (p) => {
    const base = model.normalizeProcedure(p.base);
    if (!p.baseFingerprint || ai.fingerprint(base) !== p.baseFingerprint) {
      throw new Error('見直し後に内容が変わりました。もう一度AIで見直してください');
    }
    return aiDiff.apply({ base, candidate: p.candidate, ids: p.ids });
  });

  // 構成確認はスキル、本実行は agent-loop を入口に agent-tools の harness を使う。agent-loop が
  // 無ければ（inspect が答えない）、同じスキルの run_machine.py に定義から組んだ argv を渡して
  // この場で回す（direct-run.js。履歴と定期実行は持たないが、タスクの本体は動く）。
  // 出力はどちらも行単位で renderer へ流す。
  register('run:start', async (p, event) => {
    const root = selectedRoot(p);
    const taskId = String(p.taskId || p.machine || '');
    const snapshot = taskInputs.enrichSnapshot(root, await agentLoop.inspect({ root, capture: runCapture }));
    const tasks = Array.isArray(snapshot.tasks) ? snapshot.tasks : [];
    const task = tasks.find((item) => String(item.id || item.machine) === taskId)
      || (p.machine ? { id: `machine:${p.machine}`, kind: 'statemachine', machine: String(p.machine) } : null);
    if (!task) throw new Error('実行するタスクが見つかりません');
    const machine = String(task.machine || '');
    const workflow = machine ? path.join(store.machineDir(root, machine), 'workflow.yaml') : '';
    const mode = p.mode === 'run' ? 'run' : 'check';
    let command;
    let args;
    let preparation = {};
    let onHost = false;
    let launchWarning = '';
    let launchInput = '';                // stdin で本文を渡す定義（codex 等）
    let launchOutputFile = '';           // 応答をファイルへ書く定義（codex）
    let launchEnv = {};
    let stripDecoration = false;
    let resultSource = 'result-line';    // 'result-line'（ハーネス） | 'exit-code'（1 セッション）
    if (mode === 'check') {
      if (task.kind !== 'statemachine') throw new Error('構成確認はステートマシンのタスクだけで使えます');
      const skillDir = selectedSkillDir(root);
      if (!skillDir) throw new Error('statemachine-use スキルのスクリプトが見つかりません（「実行環境」を確認してください）');
      const py = await pythonFor();
      if (!py) throw new Error('Python を起動できません（「実行環境」を確認してください）');
      command = py.command;
      args = [path.join(skillDir, 'scripts', 'run_machine.py'), workflow, '--dry-run'];
    } else {
      const cfg = settings.load(getUserData());
      const requestedAgent = String(p.agent || cfg.agent || '');
      if (!requestedAgent) throw new Error('使う AI を選んでください（「実行環境」で確認できます）');
      const definitions = await agentDefinitions({ cwd: root, capture: runCapture });
      if (!definitions.includes(requestedAgent)) throw new Error(`使う AI「${requestedAgent}」はこの環境で使えません`);
      const loopAvailable = snapshot.available !== false;
      // **経路は CLI の宣言だけで決める**（判定は session-run.js の 1 か所）。クラウドで自分の
      // ツールループを持つ CLI は 1 セッションで通し、それ以外は従来どおり工程ごとのハーネスで
      // 回す。工程ごとに起こすと起動・文脈の再構築・システムプロンプトの再送が工程の数だけ
      // 掛かり、クラウドではトークン消費が跳ねる——同じ分け方を agent-loop のデーモンと
      // agent-dashboard も持つ。仮想の名前（`herd`）は実体へ写してから判定する。
      const named = await resolveAgent(requestedAgent, 'direct', root);
      const oneSession = task.kind === 'statemachine'
        && sessionRun.runsInOneSession(named, root, String(p.model || cfg.model || ''));
      // ハーネスへ渡す名前は従来どおり（`herd` は '' ＝ agent-herd の既定と宣言に任せる）。
      const agent = oneSession || !loopAvailable
        ? named
        : await resolveAgent(requestedAgent, 'task', root);
      const parameters = p.parameters && typeof p.parameters === 'object'
        ? p.parameters
        : { ...(p.context && typeof p.context === 'object' ? p.context : {}), ...(p.input ? { input: p.input } : {}) };
      const input = taskInputs.requiredInput(task, parameters);
      if (input.missing.length) {
        const error = new Error('実行前に必要な入力があります');
        error.code = 'INPUT_REQUIRED';
        error.detail = { fields: input.missing, defaults: input.values };
        throw error;
      }
      Object.assign(parameters, input.values);
      preparation = options.hooks && options.hooks.prepareRun
        ? await options.hooks.prepareRun({
          root, task, agent, model: p.model || cfg.model, parameters,
          skillMode: p.skillMode, selectedSkills: p.skills,
        })
        : {};
      if (oneSession) {
        // 送るのは発動文 1 つ。工程の進行・検査・遷移は CLI 側のスキルが持つ。
        const spec = sessionRun.runSpec({
          root, machine, agent, model: p.model || cfg.model, parameters,
          instruction: preparation.instruction || '',
        });
        command = spec.command;
        args = spec.args;
        onHost = spec.host;
        launchInput = spec.input;
        launchOutputFile = spec.outputFile;
        launchEnv = spec.env || {};
        stripDecoration = true;          // CLI を直に起こすので端末の装飾が混ざる
        resultSource = 'exit-code';      // この経路は RESULT 行を出さない
        const checks = declaredChecks(root, machine);
        launchWarning = [
          spec.warning,
          checks ? `この AI は最初から最後まで通して実行します。確認コマンド（${checks} 件）は実行されません。` : '',
        ].filter(Boolean).join('\n');
      } else if (loopAvailable) {
        const spec = agentLoop.taskRunSpec({
          root, task, agent,
          model: p.model || cfg.model, parameters,
          instruction: preparation.instruction || '',
          allowShells: allowedShellsFor(mode, p),
        });
        command = spec.command;
        args = spec.args;
      } else {
        if (task.kind !== 'statemachine') throw new Error('このタスクの実行には agent-loop が要ります（「実行環境」を確認してください）');
        const skillDir = selectedSkillDir(root);
        const py = process.platform === 'win32' ? { command: directRun.WSL_PYTHON } : await pythonFor();
        if (!py) throw new Error('Python を起動できません（「実行環境」を確認してください）');
        const spec = directRun.runSpec({
          root, machine, agent, model: p.model || cfg.model, parameters,
          instruction: preparation.instruction || '', skillDir, python: py.command, hostPath,
        });
        command = spec.command;
        args = spec.args;
        onHost = spec.host;
        launchWarning = spec.warning || '';
      }
    }
    const requestId = randomUUID();
    const sender = event.sender;
    const send = (channel, payload) => { if (!sender.isDestroyed()) sender.send(channel, payload); };
    const started = runStream(command, args, {
      cwd: root,
      kind: 'run',
      host: onHost,
      input: launchInput,
      env: { ...process.env, ...launchEnv, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      onLine: (kind, line) => send(channel('run:line'), {
        requestId, machine, kind, line: stripDecoration ? terminalText.stripAnsi(line) : line,
      }),
      onExit: ({ code, stdout, stderr, truncated }) => {
        // 応答をファイルへ書く定義は、そこに本文がある（stdout は進行だけ）。
        for (const line of String(launchOutputFile ? readOutputFile(launchOutputFile) : '').split(/\r?\n/)) {
          if (line) send(channel('run:line'), { requestId, machine, kind: 'stdout', line: terminalText.stripAnsi(line) });
        }
        // 1 セッションの経路は RESULT 行を出さないので、成否は終了コードで見る
        // ——モデルの自己申告は受け取らない。
        const result = mode === 'run' && resultSource === 'result-line'
          ? agentLoop.parseResult(stdout, code)
          : { ok: code === 0 };
        send(channel('run:exit'), {
          requestId, machine, code, mode, result,
          error: truncated ? '実行ログが大きいため一部を省略しました' : '',
          stderr,
        });
        // 埋め込む側（agent-app）が、画面の外に居る利用者へ知らせるための合図。
        if (typeof options.onRunExit === 'function') {
          options.onRunExit({ name: String(task.name || machine || ''), mode, result });
        }
      },
    });
    return {
      ...started, requestId, mode,
      executionInformation: Array.isArray(preparation.information) ? preparation.information : [],
      skillSelection: preparation.skillSelection || null,
      warning: [preparation.warning, launchWarning].filter(Boolean).join('\n'),
    };
  });
  register('run:stop', () => runner.stop('run'));
}

module.exports = { registerIpcHandlers };
