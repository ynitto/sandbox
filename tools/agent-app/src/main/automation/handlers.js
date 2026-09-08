'use strict';

const { ipcMain, dialog, shell, app } = require('electron');
const { randomUUID } = require('crypto');
const path = require('path');
const model = require('./model');
const store = require('./store');
const recording = require('./recording');
const tools = require('./tools');
const runner = require('./runner');
const ai = require('./ai');
const aiDiff = require('./ai-diff');
const agentLoop = require('./agent-loop');
const flowModel = require('./flow-model');
const flowStore = require('./flow-store');
const agentFlow = require('./agent-flow');
const flowTeaching = require('./flow-teaching-model');
const flowTeachingStore = require('./flow-teaching-store');
const teaching = require('./teaching');

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
  const runStream = (name, args, opts = {}) => runner.stream(name, args, { ...opts, spawnSpec: commandSpawnSpec(name) });
  const runStartDetached = (name, args, opts = {}) => runner.startDetached(name, args, { ...opts, spawnSpec: commandSpawnSpec(name) });
  // 選ばれた名前を、実際に起こす定義の名前へ写す（`herd` → aider / ollama）。無ければそのまま。
  //   purpose … 'task'（タスクの実行）| 'flow'（ワークフローの実行）| 'plan'（AI 支援。読み取り専用）
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

  function launchAi(job, prompt) {
    const spec = tools.agentAssistRunSpec({
      root: job.root, agent: job.agent, model: job.model, prompt,
    });
    const started = runStream(spec.command, spec.args, {
      cwd: job.root,
      kind: 'ai',
      maxBytes: runner.MAX_STREAM_OUTPUT,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      onExit: ({ code, stdout, stderr, truncated }) => {
        if (!activeAi || activeAi.requestId !== job.requestId || job.cancelled) return;
        try {
          if (code !== 0) throw new Error((stderr || `agent-tools が終了コード ${code} で終了しました`).trim());
          if (truncated) throw new Error('AIの応答が大きすぎます');
          const result = job.mode === 'flow-teach'
            ? ai.parseFlowTeachingEnvelope(stdout, { workflowId: job.workflowId })
            : ai.parseEnvelope(stdout, { mode: job.mode, baseSpec: job.baseSpec, scope: job.scope });
          const changes = result.candidate && job.mode === 'review'
            ? aiDiff.diff(job.baseSpec, result.candidate)
            : [];
          let session = null;
          if (job.mode === 'flow-teach') {
            const messages = [...job.flowTeachingSession.messages];
            if (result.summary) messages.push({ role: 'assistant', text: result.summary, kind: result.status });
            session = flowTeaching.normalizeSession({ ...job.flowTeachingSession, messages });
            if (result.status === 'questions') {
              session.understanding.unknowns = result.questions.map((item) => item.text);
            } else {
              session.understanding = result.workflowSpec;
              session = flowTeaching.addGeneration(session, {
                id: randomUUID(), summary: result.summary, workflowSpec: result.workflowSpec,
                workflow: result.candidate, digest: result.preview && result.preview.digest,
              });
            }
            session = flowTeachingStore.save(job.root, job.workflowId, session);
          }
          finishAi(job, {
            ok: true,
            result: {
              ...result,
              changes,
              ...(session ? { session } : {}),
              baseFingerprint: job.baseSpec ? ai.fingerprint(job.baseSpec) : '',
            },
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
  register('machine:openFolder', (p) => {
    const root = selectedRoot(p);
    return shell.openPath(store.machineDir(root, String(p.machine || '')));
  });

  // タスクの下書き（AI との tmux 会話で作っている途中で、まだ定義の無いもの）。作成・変更の会話
  // そのものは agent-app の会話基盤（automation:teach:*）が担う。
  register('teaching:list', (p) => teaching.list(selectedRoot(p)));

  register('tools:status', (p) => {
    const root = p.root ? selectedRoot(p) : '';
    return tools.toolStatus({ cwd: root, capture: runCapture, skillDir: selectedSkillDir(root) });
  });
  register('agents:list', (p) => {
    const root = p.root ? selectedRoot(p) : '';
    return agentDefinitions({ cwd: root, capture: runCapture });
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
  register('flow:teaching:create', (p) => {
    const root = selectedRoot(p);
    const purpose = String(p.purpose || '').trim();
    if (!purpose) throw new Error('教えたいワークフローを入力してください');
    const workflowId = String(p.workflowId || `flow-${randomUUID().slice(0, 8)}`).trim();
    const title = String(p.title || purpose.split(/\r?\n/)[0]).trim().slice(0, 80);
    return flowTeachingStore.create(root, { workflowId, title, purpose });
  });
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
  register('run:snapshot', (p) => agentLoop.inspect({ root: selectedRoot(p), capture: runCapture }));
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
    const mode = p.mode === 'review' ? 'review' : p.mode === 'flow-teach' ? 'flow-teach' : 'draft';
    const cfg = settings.load(getUserData());
    const requestedAgent = String(p.agent || cfg.agent || 'aider');
    const definitions = await agentDefinitions({ cwd: root, capture: runCapture });
    if (!definitions.includes(requestedAgent)) throw new Error(`使う AI「${requestedAgent}」は agent-tools に定義されていません`);
    const agent = await resolveAgent(requestedAgent, 'plan', root);
    if (runner.isRunning()) throw new Error('別の実行が進行中です。終わるか停止してから始めてください');

    let baseSpec = null;
    let scope = { type: 'workflow' };
    let prompt;
    let flowTeachingSession = null;
    let workflowId = '';
    if (mode === 'flow-teach') {
      workflowId = String(p.workflowId || '').trim();
      flowTeachingSession = flowTeachingStore.load(root, workflowId);
      const message = String(p.message || '').trim();
      if (message) {
        flowTeachingSession = flowTeaching.normalizeSession({
          ...flowTeachingSession,
          messages: [...flowTeachingSession.messages, { role: 'user', text: message }],
        });
        flowTeachingSession = flowTeachingStore.save(root, workflowId, flowTeachingSession);
      }
      prompt = ai.flowTeachingPrompt({ session: flowTeachingSession, catalog: agentFlow.catalog(runCapture) });
    } else if (mode === 'review') {
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
      flowTeachingSession, workflowId,
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

  // 構成確認はスキル、本実行は agent-loop を入口に agent-tools の harness を使う。
  // 出力はどちらも行単位で renderer へ流す。
  register('run:start', async (p, event) => {
    const root = selectedRoot(p);
    const taskId = String(p.taskId || p.machine || '');
    const snapshot = await agentLoop.inspect({ root, capture: runCapture });
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
      const requestedAgent = String(p.agent || cfg.agent || 'aider');
      const definitions = await agentDefinitions({ cwd: root, capture: runCapture });
      if (!definitions.includes(requestedAgent)) throw new Error(`使う AI「${requestedAgent}」は agent-tools に定義されていません`);
      const agent = await resolveAgent(requestedAgent, 'task', root);
      const parameters = p.parameters && typeof p.parameters === 'object'
        ? p.parameters
        : { ...(p.context && typeof p.context === 'object' ? p.context : {}), ...(p.input ? { input: p.input } : {}) };
      preparation = options.hooks && options.hooks.prepareRun
        ? await options.hooks.prepareRun({
          root, task, agent, model: p.model || cfg.model, parameters,
          skillMode: p.skillMode, selectedSkills: p.skills,
        })
        : {};
      const spec = agentLoop.taskRunSpec({
        root, task, agent,
        model: p.model || cfg.model, parameters,
        instruction: preparation.instruction || '',
      });
      command = spec.command;
      args = spec.args;
    }
    const requestId = randomUUID();
    const sender = event.sender;
    const send = (channel, payload) => { if (!sender.isDestroyed()) sender.send(channel, payload); };
    const started = runStream(command, args, {
      cwd: root,
      kind: 'run',
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      onLine: (kind, line) => send(channel('run:line'), { requestId, machine, kind, line }),
      onExit: ({ code, stdout, stderr, truncated }) => send(channel('run:exit'), {
        requestId, machine, code, mode,
        result: mode === 'run' ? agentLoop.parseResult(stdout, code) : { ok: code === 0 },
        error: truncated ? '実行ログが大きいため一部を省略しました' : '',
        stderr,
      }),
    });
    return {
      ...started, requestId, mode,
      executionInformation: Array.isArray(preparation.information) ? preparation.information : [],
      skillSelection: preparation.skillSelection || null,
      warning: String(preparation.warning || ''),
    };
  });
  register('run:stop', () => runner.stop('run'));
}

module.exports = { registerIpcHandlers };
