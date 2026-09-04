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

function requireRoot(payload) {
  const root = String(payload.root || '').trim();
  if (!root || !tools.isDir(root)) throw new Error('フォルダを選んでください');
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

  handle('root:choose', async () => {
    const res = await dialog.showOpenDialog(getWindow(), { properties: ['openDirectory'], title: 'ステートマシンを置くフォルダを選ぶ' });
    if (res.canceled || !res.filePaths.length) return null;
    const root = res.filePaths[0];
    config.remember(userData(), root);
    return root;
  });
  handle('workflow:choose', async () => {
    // 既存の workflow.yaml を直接指す。ルートは `.statemachine/<識別名>/workflow.yaml` から逆算する。
    const res = await dialog.showOpenDialog(getWindow(), {
      properties: ['openFile'], title: '既存の workflow.yaml を選ぶ', filters: [{ name: 'workflow.yaml', extensions: ['yaml', 'yml'] }],
    });
    if (res.canceled || !res.filePaths.length) return null;
    const file = res.filePaths[0];
    const dir = path.dirname(file);
    const parent = path.dirname(dir);
    if (path.basename(parent) !== store.DIR) {
      throw new Error(`選んだファイルは .statemachine/<識別名>/workflow.yaml の形ではありません: ${file}`);
    }
    const root = path.dirname(parent);
    config.remember(userData(), root);
    return { root, machine: path.basename(dir) };
  });

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
    const root = String(p.root || '').trim();
    return tools.toolStatus({ cwd: root, capture: runner.capture, skillDir: skillDirFor(root) });
  });

  handle('recording:start', (p) => {
    const root = String(p.root || '').trim();
    if (p.source === 'windows') {
      return recording.recordWindowsStart({ cwd: root, app: p.app, spawnRecorder: runner.spawnRecorder });
    }
    return recording.recordBrowserStart({ cwd: root, url: p.url, capture: runner.capture });
  });
  handle('recording:stop', (p) => {
    const root = String(p.root || '').trim();
    return p.source === 'windows'
      ? recording.recordWindowsStop({})
      : recording.recordBrowserStop({ cwd: root, url: p.url, capture: runner.capture });
  });
  handle('recording:import', (p) => recording.stepsFromRecording({ source: p.source, text: p.text, url: p.url, app: p.app }));
  handle('recording:state', () => ({ windows: recording.windowsRecordingState() }));

  handle('instruction:get', (p) => {
    const spec = model.normalizeProcedure(p.spec);
    const root = String(p.root || '').trim();
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
    const mode = p.mode === 'run' ? 'run' : 'dry-run';
    if (mode === 'dry-run') args.push('--dry-run');
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
  handle('run:command', async (p) => {
    // 手で実行するときのコマンド（このツール無しで動くことの案内）。
    const root = requireRoot(p);
    const machine = String(p.machine || '');
    const skillDir = skillDirFor(root);
    const rel = skillDir ? path.relative(root, path.join(skillDir, 'scripts', 'run_machine.py')).split(path.sep).join('/') : '.github/skills/statemachine-use/scripts/run_machine.py';
    const cfg = config.load(userData());
    return {
      cwd: root,
      dryRun: `python ${rel} .statemachine/${machine}/workflow.yaml --dry-run`,
      run: `python ${rel} .statemachine/${machine}/workflow.yaml --agent ${cfg.agent || 'claude'}`,
      skillDir,
    };
  });

  handle('shell:openPath', (p) => shell.openPath(String(p.target || '')));
  handle('shell:openTerminal', (p) => openTerminal(requireRoot(p)));
  handle('clipboard:write', (p) => { clipboard.writeText(String(p.text || '')); return true; });
}

module.exports = { registerIpcHandlers };
