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

function skillDirFor(root) {
  return tools.findSkillDir({ root, configured: config.load(userData()).skillDir, appRoot: APP_ROOT });
}

// 触ってよいのは**登録したフォルダだけ**。登録に無いパスは、実在していても断る。
function requireRoot(payload) {
  const root = String(payload.root || '').trim();
  if (!root) throw new Error('フォルダを選んでください');
  if (!config.isRegistered(userData(), root)) throw new Error('登録していないフォルダです');
  if (!tools.isDir(root)) throw new Error('フォルダが見つかりません');
  return root;
}

function pythonFor() {
  return tools.findPython(runner.capture);
}

function registerIpcHandlers(getWindow) {
  let activeAi = null;

  function sendTo(sender, channel, payload) {
    if (sender && !sender.isDestroyed()) sender.send(channel, payload);
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

  handle('config:get', () => config.load(userData()));
  handle('config:save', (p) => config.save(userData(), p.config));
  handle('catalog:get', () => ({ kinds: model.catalog(), version: model.PROCEDURE_VERSION, platform: process.platform }));

  // フォルダの登録。**見に行くのは登録したフォルダの `.statemachine/` だけ**で、
  // 画面から届いたパスをそのまま開かない（登録に無ければ下の requireRoot が断る）。
  handle('root:add', async () => {
    const res = await dialog.showOpenDialog(getWindow(), {
      properties: ['openDirectory'], title: 'ステートマシンを置くフォルダを登録する',
    });
    if (res.canceled || !res.filePaths.length) return null;
    return config.addRoot(userData(), res.filePaths[0]);
  });
  handle('root:remove', (p) => config.removeRoot(userData(), p.root));
  handle('root:select', (p) => config.save(userData(), { lastRoot: String(p.root || '') }));

  handle('machine:list', (p) => store.list(requireRoot(p)));
  handle('machine:read', (p) => store.read(requireRoot(p), String(p.machine || '')));
  handle('machine:exists', (p) => store.exists(requireRoot(p), String(p.machine || '')));
  handle('machine:preview', (p) => {
    // 保存せずにコンパイルの結果だけ返す。検証エラーは投げずに一覧で返す（画面に並べる）。
    let spec;
    try { spec = model.normalizeProcedure(p.spec); } catch (err) { return { errors: [err.message], files: {}, warnings: [] }; }
    const { workflow, files } = model.compile(spec);
    return { spec, files, errors: model.validateWorkflow(workflow, files), warnings: model.portabilityWarnings(spec) };
  });
  handle('machine:save', (p) => {
    const root = requireRoot(p);
    const res = store.save(root, p.spec);
    return { dir: res.dir, written: res.written, warnings: res.warnings, machine: res.spec.machine };
  });
  handle('machine:openFolder', (p) => {
    const root = requireRoot(p);
    return shell.openPath(store.machineDir(root, String(p.machine || '')));
  });

  handle('tools:status', (p) => {
    const root = p.root ? requireRoot(p) : '';
    return tools.toolStatus({ cwd: root, capture: runner.capture, skillDir: skillDirFor(root) });
  });
  handle('agents:list', (p) => {
    const root = p.root ? requireRoot(p) : '';
    return tools.agentDefinitions({ cwd: root, capture: runner.capture });
  });

  handle('recording:start', (p) => {
    const root = p.root ? requireRoot(p) : '';
    if (p.source === 'windows') {
      return recording.recordWindowsStart({ cwd: root, app: p.app, spawnRecorder: runner.spawnRecorder });
    }
    return recording.recordBrowserStart({ cwd: root, url: p.url, capture: runner.capture });
  });
  handle('recording:stop', (p) => {
    const root = p.root ? requireRoot(p) : '';
    return p.source === 'windows'
      ? recording.recordWindowsStop({})
      : recording.recordBrowserStop({ cwd: root, url: p.url, capture: runner.capture });
  });
  handle('recording:import', (p) => recording.stepsFromRecording({ source: p.source, text: p.text, url: p.url, app: p.app }));
  handle('recording:state', () => ({ windows: recording.windowsRecordingState() }));

  handle('ai:start', async (p, event) => {
    const root = requireRoot(p);
    const mode = p.mode === 'review' ? 'review' : 'draft';
    const cfg = config.load(userData());
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
  handle('ai:stop', (p) => {
    if (!activeAi || (p.requestId && p.requestId !== activeAi.requestId)) return false;
    const job = activeAi;
    job.cancelled = true;
    activeAi = null;
    const stopped = runner.stop('ai');
    sendTo(job.sender, 'ai:result', { requestId: job.requestId, mode: job.mode, ok: false, cancelled: true, error: '中止しました' });
    return stopped;
  });
  handle('ai:apply', (p) => {
    const base = model.normalizeProcedure(p.base);
    if (!p.baseFingerprint || ai.fingerprint(base) !== p.baseFingerprint) {
      throw new Error('見直し後に内容が変わりました。もう一度AIで見直してください');
    }
    return aiDiff.apply({ base, candidate: p.candidate, ids: p.ids });
  });

  // 構成確認はスキルのスクリプト、本実行は agent-tools の harness を使う。
  // 出力はどちらも行単位で renderer へ流す。
  handle('run:start', async (p, event) => {
    const root = requireRoot(p);
    const machine = String(p.machine || '');
    const workflow = path.join(store.machineDir(root, machine), 'workflow.yaml');
    const mode = p.mode === 'run' ? 'run' : 'check';
    let command;
    let args;
    if (mode === 'check') {
      const skillDir = skillDirFor(root);
      if (!skillDir) throw new Error('statemachine-use スキルのスクリプトが見つかりません（「実行環境」を確認してください）');
      const py = await pythonFor();
      if (!py) throw new Error('Python を起動できません（「実行環境」を確認してください）');
      command = py.command;
      args = [path.join(skillDir, 'scripts', 'run_machine.py'), workflow, '--dry-run'];
    } else {
      const cfg = config.load(userData());
      const agent = String(p.agent || cfg.agent || 'aider');
      const definitions = await tools.agentDefinitions({ cwd: root, capture: runner.capture });
      if (!definitions.includes(agent)) throw new Error(`使う AI「${agent}」は agent-tools に定義されていません`);
      const spec = tools.agentHerdRunSpec({
        workflow, root, agent,
        model: p.model || cfg.model,
        input: p.input,
        context: p.context,
      });
      command = spec.command;
      args = spec.args;
    }
    const sender = event.sender;
    const send = (channel, payload) => { if (!sender.isDestroyed()) sender.send(channel, payload); };
    const started = runner.stream(command, args, {
      cwd: root,
      kind: 'run',
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      onLine: (kind, line) => send('run:line', { kind, line }),
      onExit: ({ code }) => send('run:exit', { code, mode }),
    });
    return { ...started, command: [command, ...args].join(' '), mode };
  });
  handle('run:stop', () => runner.stop('run'));
}

module.exports = { registerIpcHandlers };
