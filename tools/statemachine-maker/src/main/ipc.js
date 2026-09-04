'use strict';

const { ipcMain, dialog, shell, clipboard, app } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const model = require('./model');
const store = require('./store');
const recording = require('./recording');
const tools = require('./tools');
const runner = require('./runner');
const instruction = require('./instruction');
const config = require('./config');
const theme = require('./theme');

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

// OS の端末をそのフォルダで開く（AI 補完の指示文を貼る先）。できなければフォルダを開く。
function openTerminal(cwd) {
  const opts = { cwd, detached: true, stdio: 'ignore', shell: false };
  let child;
  try {
    if (process.platform === 'win32') child = spawn('cmd', ['/c', 'start', '', 'cmd', '/k', 'cd', '/d', cwd], opts);
    else if (process.platform === 'darwin') child = spawn('open', ['-a', 'Terminal', cwd], opts);
    else child = spawn('x-terminal-emulator', [], opts);
    child.on('error', () => shell.openPath(cwd));
    child.unref();
    return true;
  } catch {
    shell.openPath(cwd);
    return false;
  }
}

function registerIpcHandlers(getWindow) {
  handle('config:get', () => config.load(userData()));
  handle('config:save', (p) => config.save(userData(), p.config));
  handle('catalog:get', () => ({ kinds: model.catalog(), version: model.PROCEDURE_VERSION, platform: process.platform }));
  // 見た目: theme.json の値（CSS 変数へ）と custom.css（そのまま）。定義には混ぜない。
  handle('theme:get', () => {
    const loaded = theme.load(userData());
    return { ...loaded, variables: theme.cssVariables(loaded.theme), defaults: theme.DEFAULTS };
  });
  handle('theme:save', (p) => {
    const saved = theme.save(userData(), p.theme);
    return { theme: saved, variables: theme.cssVariables(saved) };
  });
  handle('theme:openCss', () => shell.openPath(theme.ensureCustomCss(userData())));

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

  handle('instruction:get', (p) => {
    const spec = model.normalizeProcedure(p.spec);
    const root = p.root ? requireRoot(p) : '';
    const machineDir = root && spec.machine && store.exists(root, spec.machine) ? `.statemachine/${spec.machine}/` : '';
    return { instruction: instruction.creationInstruction(spec, { machineDir }), prompt: instruction.creationPrompt(spec, { machineDir }) };
  });

  // スキルのスクリプトで検証・実行する。出力は行単位で renderer へ流す。
  handle('run:start', async (p, event) => {
    const root = requireRoot(p);
    const machine = String(p.machine || '');
    const workflow = path.join(store.machineDir(root, machine), 'workflow.yaml');
    const skillDir = skillDirFor(root);
    if (!skillDir) throw new Error('statemachine-use スキルのスクリプトが見つかりません（「道具」を確認してください）');
    const py = await pythonFor();
    if (!py) throw new Error('Python を起動できません（「道具」を確認してください）');
    const script = path.join(skillDir, 'scripts', 'run_machine.py');
    const args = [script, workflow];
    const mode = p.mode === 'run' ? 'run' : 'check';
    if (mode === 'check') args.push('--dry-run');
    else {
      const cfg = config.load(userData());
      const agent = String(p.agent || cfg.agent || 'claude');
      args.push('--agent', agent);
      if (p.model || cfg.model) args.push('--model', String(p.model || cfg.model));
      if (p.input) args.push('--input', String(p.input));
      for (const [k, v] of Object.entries(p.context || {})) args.push('--context', `${k}=${v}`);
      if (p.verbose) args.push('--verbose');
    }
    const sender = event.sender;
    const send = (channel, payload) => { if (!sender.isDestroyed()) sender.send(channel, payload); };
    const started = runner.stream(py.command, args, {
      cwd: root,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      onLine: (kind, line) => send('run:line', { kind, line }),
      onExit: ({ code }) => send('run:exit', { code, mode }),
    });
    return { ...started, command: [py.command, ...args].join(' '), mode };
  });
  handle('run:stop', () => runner.stop());

  handle('shell:openTerminal', (p) => openTerminal(requireRoot(p)));
  handle('clipboard:write', (p) => { clipboard.writeText(String(p.text || '')); return true; });
}

module.exports = { registerIpcHandlers };
