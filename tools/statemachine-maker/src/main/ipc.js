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
const config = require('./config');
const agentLoop = require('./agent-loop');

const APP_ROOT = path.join(__dirname, '..', '..');

function userData() {
  return app.getPath('userData');
}

// すべてのハンドラを {ok, data|error} に揃える。
function handle(channel, fn) {
  ipcMain.handle(channel, async (event, args) => {
    try {
      return { ok: true, data: await fn(args || {}, event) };
    } catch (err) {
      return { ok: false, error: err && err.message ? err.message : String(err) };
    }
  });
}

function skillDirFor(root, settings = config, getUserData = userData, appRoot = APP_ROOT) {
  return tools.findSkillDir({ root, configured: settings.load(getUserData()).skillDir, appRoot });
}

// 触ってよいのは**登録したフォルダだけ**。登録に無いパスは、実在していても断る。
function requireRoot(payload, settings = config, getUserData = userData) {
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
  const settings = options.config || config;
  const getUserData = options.userData || userData;
  const appRoot = options.appRoot || APP_ROOT;
  const channel = (name) => `${channelPrefix}${name}`;
  const register = (name, fn) => handle(channel(name), fn);
  const selectedRoot = (payload) => requireRoot(payload, settings, getUserData);
  const selectedSkillDir = (root) => skillDirFor(root, settings, getUserData, appRoot);

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
    const started = runner.stream(spec.command, spec.args, {
      cwd: job.root,
      kind: 'ai',
      maxBytes: runner.MAX_STREAM_OUTPUT,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      onExit: ({ code, stdout, stderr, truncated }) => {
        if (!activeAi || activeAi.requestId !== job.requestId || job.cancelled) return;
        try {
          if (code !== 0) throw new Error((stderr || `agent-tools が終了コード ${code} で終了しました`).trim());
          if (truncated) throw new Error('AIの応答が大きすぎます');
          const result = ai.parseEnvelope(stdout, {
            mode: job.mode, baseSpec: job.baseSpec, scope: job.scope,
          });
          const changes = result.candidate && job.mode === 'review'
            ? aiDiff.diff(job.baseSpec, result.candidate)
            : [];
          finishAi(job, {
            ok: true,
            result: {
              ...result,
              changes,
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
  register('machine:openFolder', (p) => {
    const root = selectedRoot(p);
    return shell.openPath(store.machineDir(root, String(p.machine || '')));
  });

  register('tools:status', (p) => {
    const root = p.root ? selectedRoot(p) : '';
    return tools.toolStatus({ cwd: root, capture: runner.capture, skillDir: selectedSkillDir(root) });
  });
  register('agents:list', (p) => {
    const root = p.root ? selectedRoot(p) : '';
    return tools.agentDefinitions({ cwd: root, capture: runner.capture });
  });
  register('run:snapshot', (p) => agentLoop.inspect({ root: selectedRoot(p), capture: runner.capture }));
  register('run:schedule', (p) => agentLoop.saveSchedule({
    root: selectedRoot(p), payload: p.schedule, capture: runner.capture,
  }));
  register('run:daemon', (p) => {
    const root = selectedRoot(p);
    if (!['start', 'stop'].includes(p.action)) throw new Error('自動実行の操作が不正です');
    return p.action === 'stop'
      ? agentLoop.stopDaemon({ root, capture: runner.capture })
      : agentLoop.startDaemon({ root, startDetached: runner.startDetached });
  });
  register('run:log', (p) => agentLoop.readLog({
    root: selectedRoot(p), identity: p.identity, capture: runner.capture,
  }));

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
  register('recording:state', () => ({ windows: recording.windowsRecordingState() }));

  register('ai:start', async (p, event) => {
    const root = selectedRoot(p);
    const mode = p.mode === 'review' ? 'review' : 'draft';
    const cfg = settings.load(getUserData());
    const agent = String(p.agent || cfg.agent || 'aider');
    const definitions = await tools.agentDefinitions({ cwd: root, capture: runner.capture });
    if (!definitions.includes(agent)) throw new Error(`使う AI「${agent}」は agent-tools に定義されていません`);
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

  // 構成確認はスキル、本実行は agent-loop を入口に agent-tools の harness を使う。
  // 出力はどちらも行単位で renderer へ流す。
  register('run:start', async (p, event) => {
    const root = selectedRoot(p);
    const machine = String(p.machine || '');
    const workflow = path.join(store.machineDir(root, machine), 'workflow.yaml');
    const mode = p.mode === 'run' ? 'run' : 'check';
    let command;
    let args;
    if (mode === 'check') {
      const skillDir = selectedSkillDir(root);
      if (!skillDir) throw new Error('statemachine-use スキルのスクリプトが見つかりません（「実行環境」を確認してください）');
      const py = await pythonFor();
      if (!py) throw new Error('Python を起動できません（「実行環境」を確認してください）');
      command = py.command;
      args = [path.join(skillDir, 'scripts', 'run_machine.py'), workflow, '--dry-run'];
    } else {
      const cfg = settings.load(getUserData());
      const agent = String(p.agent || cfg.agent || 'aider');
      const definitions = await tools.agentDefinitions({ cwd: root, capture: runner.capture });
      if (!definitions.includes(agent)) throw new Error(`使う AI「${agent}」は agent-tools に定義されていません`);
      const parameters = p.parameters && typeof p.parameters === 'object'
        ? p.parameters
        : { ...(p.context && typeof p.context === 'object' ? p.context : {}), ...(p.input ? { input: p.input } : {}) };
      const spec = agentLoop.runSpec({
        root, machine, agent,
        model: p.model || cfg.model, parameters,
      });
      command = spec.command;
      args = spec.args;
    }
    const requestId = randomUUID();
    const sender = event.sender;
    const send = (channel, payload) => { if (!sender.isDestroyed()) sender.send(channel, payload); };
    const started = runner.stream(command, args, {
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
    return { ...started, requestId, mode };
  });
  register('run:stop', () => runner.stop('run'));
}

module.exports = { registerIpcHandlers };
