'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const { parseYaml, isPlainObject, scalarString } = require('../../../base/main/yaml');
const agentCli = require('../../agent-project/main/agentCli');

const MAX_TOOL_ROUNDS = 8;
const MAX_TOOL_TIMEOUT_SEC = 300;
const MAX_AUTO_READ_BYTES = 32768;
// ponytail: 初版は statemachine-use 1 経路だけなので固定上限。2つ目のskillで実測差が出たらhandler設定へ移す。
const SHELLS = new Set(['sh', 'bash', 'zsh', 'fish', 'cmd', 'cmd.exe', 'powershell', 'powershell.exe', 'pwsh']);

function inside(root, file) {
  const rel = path.relative(root, file);
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

function projectPath(cwd, value) {
  const root = fs.realpathSync(String(cwd));
  const raw = String(value || '').trim();
  if (!raw || raw.includes('\0')) throw new Error('空または不正なファイルパスです');
  const requested = path.resolve(String(cwd), raw);

  let parent = requested;
  while (!fs.existsSync(parent)) {
    const next = path.dirname(parent);
    if (next === parent) break;
    parent = next;
  }
  const realParent = fs.realpathSync(parent);
  const target = path.resolve(realParent, path.relative(parent, requested));
  if (!inside(root, target)) throw new Error(`作業フォルダ外のパスは使えません: ${raw}`);
  return target;
}

function sourceRoot() {
  let dir = __dirname;
  for (let i = 0; i < 10; i += 1) {
    if (fs.existsSync(path.join(dir, '.github', 'skills'))) return dir;
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  return '';
}

function resolveSkill(name, cwd) {
  const roots = [
    path.join(cwd, '.github', 'skills', name),
    sourceRoot() ? path.join(sourceRoot(), '.github', 'skills', name) : '',
    path.join(os.homedir(), '.agents', 'skills', name),
    path.join(os.homedir(), '.codex', 'skills', name),
  ].filter(Boolean);
  const root = roots.find((dir) => fs.existsSync(path.join(dir, 'SKILL.md')));
  return root ? { name, root, skillFile: path.join(root, 'SKILL.md') } : null;
}

function actionSkillNames(text) {
  const out = new Set();
  for (const match of String(text || '').matchAll(/`([A-Za-z0-9_.-]+)`\s*スキル/g)) out.add(match[1]);
  return [...out];
}

function actionProjectFiles(text, cwd) {
  const files = new Set();
  for (const match of String(text || '').matchAll(/`([^`\n]+)`/g)) {
    const raw = match[1].trim();
    if (!raw || raw.startsWith('-') || /^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) continue;
    try {
      const file = projectPath(cwd, raw);
      if (fs.existsSync(file) && fs.statSync(file).isFile()) files.add(file);
    } catch { /* コマンド例や作業フォルダ外の参照は割り当てない */ }
  }
  return [...files];
}

function skillScripts(skill) {
  const dir = path.join(skill.root, 'scripts');
  try {
    return fs.readdirSync(dir)
      .filter((name) => /\.(?:py|js|sh)$/i.test(name))
      .map((name) => path.join(dir, name));
  } catch {
    return [];
  }
}

function executableOnPath(command) {
  const pathParts = String(process.env.PATH || '').split(path.delimiter).filter(Boolean);
  const suffixes = process.platform === 'win32'
    ? String(process.env.PATHEXT || '.EXE;.CMD;.BAT;.COM').split(';') : [''];
  for (const dir of pathParts) {
    for (const suffix of suffixes) {
      const file = path.join(dir, process.platform === 'win32' ? `${command}${suffix}` : command);
      try {
        fs.accessSync(file, fs.constants.X_OK);
        return file;
      } catch { /* next candidate */ }
    }
  }
  return '';
}

function validateCommand(command, cwd, skillDirs) {
  const raw = String(command || '').trim();
  if (!raw || /\s|\0/.test(raw)) throw new Error('run.command は単一の実行ファイル名が必要です');
  if (SHELLS.has(path.basename(raw).toLowerCase())) throw new Error(`シェルの実行は許可されていません: ${raw}`);
  if (!path.isAbsolute(raw) && !raw.includes('/') && !raw.includes('\\')) {
    if (!executableOnPath(raw)) throw new Error(`PATH 上に実行ファイルがありません: ${raw}`);
    return raw;
  }
  for (const root of [cwd, ...skillDirs]) {
    try {
      const resolved = projectPath(root, raw);
      if (fs.existsSync(resolved)) return resolved;
    } catch { /* next allowed root */ }
  }
  throw new Error(`実行ファイルは作業フォルダまたはロード済みスキル内に限定されます: ${raw}`);
}

function validateArgPaths(args, cwd, skillDirs) {
  for (const arg of args) {
    if (arg.includes('\0')) throw new Error('run.args に NUL は使えません');
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(arg) || arg.startsWith('-')) continue;
    if (path.isAbsolute(arg) || arg.split(/[\\/]/).includes('..')) {
      const candidate = path.isAbsolute(arg) ? arg : path.resolve(cwd, arg);
      const allowed = [cwd, ...skillDirs].some((root) => {
        try { projectPath(root, candidate); return true; } catch { return false; }
      });
      if (!allowed) throw new Error(`作業フォルダ外の引数パスは使えません: ${arg}`);
    }
  }
}

function validateToolRequest(raw, cwd, skills) {
  if (!isPlainObject(raw)) throw new Error('Aider のツール要求が JSON オブジェクトではありません');
  const type = String(raw.type || '');
  const skillDirs = skills.map((skill) => skill.root || skill).filter(Boolean);
  if (type === 'read_files' || type === 'write_files') {
    if (!Array.isArray(raw.paths) || !raw.paths.length || raw.paths.some((file) => typeof file !== 'string')) {
      throw new Error(`${type}.paths は1件以上の文字列配列が必要です`);
    }
    return { type, paths: raw.paths.map((file) => projectPath(cwd, file)) };
  }
  if (type === 'run') {
    if (!Array.isArray(raw.args) || raw.args.some((arg) => typeof arg !== 'string')) {
      throw new Error('run.args は文字列配列が必要です');
    }
    let args = raw.args.map(String);
    validateArgPaths(args, cwd, skillDirs);
    let command = validateCommand(raw.command, cwd, skillDirs);
    if (/\.py$/i.test(command)) {
      args = [command, ...args];
      command = pythonCommand();
    }
    return {
      type,
      command,
      args,
      timeoutSec: Math.max(1, Math.min(Number(raw.timeout_sec) || 60, MAX_TOOL_TIMEOUT_SEC)),
    };
  }
  if (type === 'final') return { type, output: String(raw.output || '').trim() };
  throw new Error(`許可されていないツール要求です: ${type || '(空)'}`);
}

function parseJsonObject(text) {
  const value = String(text || '');
  const found = [];
  let start = -1;
  let depth = 0;
  let quoted = false;
  let escaped = false;
  for (let i = 0; i < value.length; i += 1) {
    const char = value[i];
    if (start < 0) {
      if (char === '{') { start = i; depth = 1; }
      continue;
    }
    if (quoted) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === '"') quoted = false;
      continue;
    }
    if (char === '"') quoted = true;
    else if (char === '{') depth += 1;
    else if (char === '}') {
      depth -= 1;
      if (depth === 0) {
        try {
          const parsed = JSON.parse(value.slice(start, i + 1));
          if (isPlainObject(parsed)) found.push(parsed);
        } catch { /* Aider の説明中にある JSON 風テキストは無視 */ }
        start = -1;
      }
    }
  }
  return found.at(-1) || null;
}

function parseToolRequest(text) {
  const request = parseJsonObject(text);
  if (!request || !request.type) throw new Error(`Aider のツール要求を JSON として読めません: ${String(text).slice(0, 160)}`);
  return request;
}

function appendLog(logFile, event) {
  fs.appendFileSync(logFile, `${JSON.stringify({ at: new Date().toISOString(), ...event })}\n`, 'utf8');
}

function execArgv(command, args, { cwd, timeoutMs, env = {}, stdin = null, outputFile = null, logFile }) {
  const started = Date.now();
  appendLog(logFile, { event: 'start', argv: [command, ...args], cwd, timeoutMs });
  return new Promise((resolve) => {
    let settled = false;
    let stdout = '';
    let stderr = '';
    const child = spawn(command, args, {
      cwd,
      shell: false,
      windowsHide: true,
      env: { ...process.env, NO_COLOR: '1', TERM: 'dumb', COLUMNS: '1000', ...env },
    });
    const finish = (result) => {
      if (settled) return;
      settled = true;
      appendLog(logFile, {
        event: 'finish', argv: [command, ...args], cwd, durationMs: Date.now() - started,
        status: result.status, error: result.error || '', stdout: result.stdout, stderr: result.stderr,
      });
      resolve(result);
    };
    const timer = setTimeout(() => {
      child.kill();
      finish({ status: null, stdout, stderr, error: `${command} がタイムアウトしました` });
    }, Math.max(1000, timeoutMs));
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', (err) => {
      clearTimeout(timer);
      finish({ status: null, stdout, stderr, error: err.message });
    });
    child.on('close', (status) => {
      clearTimeout(timer);
      let output = stdout;
      if (outputFile) {
        try { output = fs.readFileSync(outputFile, 'utf8') || output; } catch { /* stdout fallback */ }
        try { fs.unlinkSync(outputFile); } catch { /* optional cleanup */ }
      }
      finish({ status, stdout: output, stderr, error: '' });
    });
    if (stdin != null) child.stdin.write(stdin);
    child.stdin.end();
  });
}

async function runAider(agent, prompt, { cwd, readonly, readFiles, files, logFile }) {
  const built = agentCli.headlessCmd(agent.spec, agent.model, prompt, {
    readonly, noSession: true, readFiles, files,
  });
  const result = await execArgv(built.command, built.args, {
    cwd, stdin: built.stdin, env: built.env, outputFile: built.outputFile,
    timeoutMs: built.timeoutMs || agent.timeoutMs || 180000, logFile,
  });
  if (result.status !== 0 || result.error) {
    const detail = [result.error, result.stderr, result.stdout].filter(Boolean).join('\n');
    const classified = agentCli.classifyError(agent.spec, detail);
    throw new Error((classified && classified.hint) || detail || `${built.command} が失敗しました`);
  }
  const output = String(result.stdout || '').trim();
  if (!output) throw new Error('Aider が空の応答を返しました');
  return output;
}

function workflowAction(workflowPath, stateId, state) {
  const base = path.dirname(workflowPath);
  const actionFile = scalarString(state.action_file);
  const inline = scalarString(state.action);
  if (actionFile) {
    const file = path.resolve(base, actionFile);
    if (!inside(base, file)) throw new Error(`定義フォルダ外の action_file です: ${actionFile}`);
    return { text: fs.readFileSync(file, 'utf8'), file };
  }
  if (inline.startsWith('file:')) {
    const file = path.resolve(base, inline.slice(5).trim());
    if (!inside(base, file)) throw new Error(`定義フォルダ外の action 参照です: ${inline}`);
    return { text: fs.readFileSync(file, 'utf8'), file };
  }
  if (inline) return { text: inline, file: '' };
  const file = path.join(base, 'actions', `${stateId}.md`);
  return fs.existsSync(file) ? { text: fs.readFileSync(file, 'utf8'), file } : { text: '', file: '' };
}

function valueAt(context, key) {
  let value = context;
  for (const part of String(key).split('.')) {
    if (!isPlainObject(value) || !Object.prototype.hasOwnProperty.call(value, part)) return undefined;
    value = value[part];
  }
  return value;
}

function renderTemplate(text, context) {
  return String(text || '').replace(/\{\{([^}]+)\}\}/g, (whole, key) => {
    const value = valueAt(context, key.trim());
    return value === undefined ? whole : String(value);
  });
}

function setNested(target, key, value) {
  const parts = String(key).split('.');
  let cursor = target;
  for (const part of parts.slice(0, -1)) {
    if (!isPlainObject(cursor[part])) cursor[part] = {};
    cursor = cursor[part];
  }
  cursor[parts.at(-1)] = value;
}

function initialContext(workflow, parameters) {
  const declared = isPlainObject(workflow.context) ? { ...workflow.context } : {};
  const context = { ...declared, context: declared, input: '', history: {}, last_output: '', step_count: 0 };
  for (const [key, value] of Object.entries(parameters || {})) {
    if (key === 'input') context.input = value;
    else if (key.startsWith('context.')) setNested(declared, key.slice(8), value);
    else setNested(context, key, value);
  }
  return context;
}

function validates(output, rule) {
  const validator = String(rule || '');
  if (!validator.startsWith('startswith:')) return true;
  const first = String(output || '').split(/\r?\n/)[0].trim();
  return validator.slice(11).split(',').map((value) => value.trim()).some((value) => first.startsWith(value));
}

function validatedOutput(output, rule) {
  const text = String(output || '').trim();
  if (validates(text, rule)) return text;
  const validator = String(rule || '');
  if (!validator.startsWith('startswith:')) return text;
  const prefixes = validator.slice(11).split(',').map((value) => value.trim());
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const at = lines.findLastIndex((line) => prefixes.some((prefix) => line.startsWith(prefix)));
  return at < 0 ? '' : lines.slice(at, at + 4).join('\n');
}

function finalEvidenceError(output, cwd, evidence, executed) {
  const text = String(output || '').trim();
  if (/^(?:[A-Z][A-Z0-9_]*_)?(?:FAILED|ERROR)\b/i.test(text)) return '';
  const match = text.match(/^path:\s*(.+?)\s*$/mi);
  if (!match) return '';
  let file;
  try { file = projectPath(cwd, match[1]); } catch (err) { return err.message || String(err); }
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return `成功出力のファイルがありません: ${match[1]}`;
  if (!evidence.has(file)) return `このステートで確認・生成していないファイルです: ${match[1]}`;
  if (!executed.has(file)) return `この実行で生成・検証していないファイルです: ${match[1]}`;
  return '';
}

function fileStamp(file) {
  try {
    const stat = fs.statSync(file);
    return `${stat.size}:${stat.mtimeMs}`;
  } catch { return ''; }
}

function terminalStatus(stateId, output) {
  // ponytail: workflow schema に終端成否が無いので慣例名と出力を使う。outcome フィールド追加時に置換する。
  const failed = /(?:^|[_-])(?:fail(?:ed)?|error)(?:$|[_-])/i.test(String(stateId || ''))
    || /^(?:[A-Z][A-Z0-9_]*_)?FAILED\b/.test(String(output || '').trim());
  return { ok: !failed, error: failed ? String(output || '').trim() : '' };
}

function plannerPrompt({ action, cwd, skills, reads, history, retry }) {
  return `You execute exactly one state-machine action. Do not simulate it or claim work without a TOOL_RESULT. Do not work on later states.\n`
    + `Working folder: ${cwd}\n`
    + `Loaded skills (copy exact paths; do not guess script locations or add unrequested flags):\n${skills.map((skill) => {
      const scripts = skillScripts(skill);
      return `- ${skill.name}: ${skill.root}${scripts.length ? `\n  scripts: ${scripts.join(', ')}` : ''}`;
    }).join('\n') || '- none'}\n`
    + `Readable files already assigned:\n${reads.join('\n') || '- none'}\n`
    + `${retry ? `${retry}\n` : ''}Current action:\n---\n${action}\n---\n`
    + `${history.length ? `Previous tool results:\n${history.join('\n')}\n` : ''}`
    + 'A run TOOL_RESULT with status 0 already completed; do not run that command again. Request read_files only if inspection is still required.\n'
    + 'For run.args, put every CLI token in its own JSON string. Never combine a flag and value or add flags not requested by the action.\n'
    + 'Return exactly one JSON object and no markdown. Allowed forms:\n'
    + '{"type":"read_files","paths":["relative/path"]}\n'
    + '{"type":"write_files","paths":["relative/path"]}\n'
    + '{"type":"run","command":"executable","args":["arg"],"timeout_sec":60}\n'
    + '{"type":"final","output":"the exact action Output Contract"}';
}

async function executeAction({ workflowPath, stateId, state, context, cwd, agent, logFile, touched }) {
  const action = workflowAction(workflowPath, stateId, state);
  const rendered = renderTemplate(action.text, context);
  const skills = actionSkillNames(rendered).map((name) => resolveSkill(name, cwd)).filter(Boolean);
  const actionReads = actionProjectFiles(rendered, cwd);
  const reads = new Set([workflowPath, action.file, ...skills.map((skill) => skill.skillFile)].filter(Boolean));
  const maxAttempts = Math.max(1, Number(state.max_retries) + 1 || 1);

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const history = [];
    const evidence = new Set();
    for (let round = 0; round < MAX_TOOL_ROUNDS; round += 1) {
      const retry = attempt ? `Retry ${attempt}/${maxAttempts - 1}: the previous output violated the Output Contract.` : '';
      const raw = await runAider(agent, plannerPrompt({
        action: rendered, cwd, skills, reads: [...reads], history, retry,
      }), { cwd, readonly: true, readFiles: [...reads], files: [], logFile });
      let request;
      try {
        request = validateToolRequest(parseToolRequest(raw), cwd, skills);
      } catch (err) {
        const evidenceError = finalEvidenceError(raw, cwd, evidence, touched);
        const contract = validatedOutput(raw, state.output_validator);
        if (contract && !evidenceError) return contract;
        history.push(`TOOL_RESULT ${JSON.stringify({ rejected: true, error: err.message || String(err) })}`);
        continue;
      }
      if (request.type === 'final') {
        const evidenceError = finalEvidenceError(request.output, cwd, evidence, touched);
        if (evidenceError) {
          const declaredPath = request.output.match(/^path:\s*(.+?)\s*$/mi);
          const declaredFile = declaredPath ? projectPath(cwd, declaredPath[1]) : '';
          if (declaredFile && actionReads.some((file) => file !== declaredFile)
            && !/^(?:[A-Z][A-Z0-9_]*_)?(?:FAILED|ERROR)\b/i.test(request.output)) {
            request = { type: 'write_files', paths: [declaredFile] };
          } else {
            history.push(`TOOL_RESULT ${JSON.stringify({ rejected: true, error: evidenceError })}`);
            continue;
          }
        } else {
          if (validates(request.output, state.output_validator)) return request.output;
          break;
        }
      }
      if (request.type === 'read_files') {
        for (const file of request.paths) {
          if (!fs.existsSync(file)) throw new Error(`読み取り対象がありません: ${path.relative(cwd, file)}`);
          reads.add(file);
          evidence.add(file);
        }
        history.push(`TOOL_RESULT ${JSON.stringify({ type: request.type, paths: request.paths })}`);
        continue;
      }
      if (request.type === 'write_files') {
        const before = new Map(request.paths.map((file) => [file, fileStamp(file)]));
        for (const file of request.paths) {
          fs.mkdirSync(path.dirname(file), { recursive: true });
        }
        const output = await runAider(agent,
          `Execute only this action now. The editable files are stale: replace them from the assigned read-only inputs in this run. Do not merely describe or return the existing content. After editing, return only the action Output Contract.\n\n${rendered}`,
          {
            cwd, readonly: false,
            readFiles: [...new Set([...reads, ...actionReads])].filter((file) => !request.paths.includes(file)),
            files: request.paths, logFile,
          });
        const missing = request.paths.filter((file) => !fs.existsSync(file));
        const changed = request.paths.some((file) => fileStamp(file) !== before.get(file));
        if (missing.length || !changed) {
          history.push(`TOOL_RESULT ${JSON.stringify({
            rejected: true,
            error: missing.length ? `書き込み対象がありません: ${missing.map((file) => path.relative(cwd, file)).join(', ')}`
              : 'write_files が対象ファイルを変更しませんでした',
          })}`);
          continue;
        }
        for (const file of request.paths) {
          evidence.add(file);
          touched.add(file);
        }
        const evidenceError = finalEvidenceError(output, cwd, evidence, touched);
        if (evidenceError) {
          history.push(`TOOL_RESULT ${JSON.stringify({ rejected: true, error: evidenceError })}`);
          continue;
        }
        const contract = validatedOutput(output, state.output_validator);
        if (contract) return contract;
        break;
      }
      const tool = await execArgv(request.command, request.args, {
        cwd, timeoutMs: request.timeoutSec * 1000, logFile,
      });
      for (const arg of request.args) {
        try {
          const file = projectPath(cwd, arg);
          if (fs.existsSync(file) && fs.statSync(file).isFile()) {
            evidence.add(file);
            touched.add(file);
            // ponytail: 大きな成果物の自動再投入はローカルモデルを詰まらせる。必要なら Aider が read_files を要求する。
            if (fs.statSync(file).size <= MAX_AUTO_READ_BYTES) reads.add(file);
          }
        } catch { /* not a project file argument */ }
      }
      history.push(`TOOL_RESULT ${JSON.stringify({
        type: request.type, status: tool.status, error: tool.error,
        stdout: tool.stdout.slice(-4000), stderr: tool.stderr.slice(-2000), logFile,
      })}`);
    }
  }
  throw new Error(`ステート ${stateId} が Output Contract を満たしませんでした`);
}

function pythonCommand() {
  if (process.env.PYTHON) return process.env.PYTHON;
  return executableOnPath('python') ? 'python' : 'python3';
}

async function harness(script, args, { cwd, logFile }) {
  const result = await execArgv(pythonCommand(), [script, ...args], {
    cwd, timeoutMs: 30000, logFile,
  });
  if (result.status !== 0 || result.error) {
    throw new Error(result.error || result.stderr || result.stdout || `${path.basename(script)} が失敗しました`);
  }
  return result.stdout.trim();
}

async function nextState({ scripts, workflowPath, stateId, output, outputs, agent, cwd, logFile }) {
  const args = [workflowPath, '--state', stateId, '--list-conditions', '--last-output', output.split(/\r?\n/)[0]];
  for (const [key, value] of Object.entries(outputs)) args.push('--output', `${key}=${String(value).split(/\r?\n/)[0]}`);
  const listed = parseJsonObject(await harness(scripts.next, args, { cwd, logFile }));
  if (!listed || !Array.isArray(listed.conditions)) throw new Error('条件リストを解析できません');
  const pending = listed.conditions.filter((condition) => condition.needs_llm_eval === true);
  let evals = {};
  if (pending.length) {
    const raw = await runAider(agent,
      `Evaluate only these state-machine conditions against the completed action output. Return one JSON object mapping each index to true or false.\nOutput:\n${output}\nConditions:\n${JSON.stringify(pending)}`,
      { cwd, readonly: true, readFiles: [workflowPath], files: [], logFile });
    evals = parseJsonObject(raw);
    if (!evals) throw new Error('Aider の条件評価を JSON として読めません');
  }
  const decide = [workflowPath, '--state', stateId, '--evals', JSON.stringify(evals),
    '--last-output', output.split(/\r?\n/)[0]];
  for (const [key, value] of Object.entries(outputs)) decide.push('--output', `${key}=${String(value).split(/\r?\n/)[0]}`);
  return harness(scripts.next, decide, { cwd, logFile });
}

async function runSkill({ skillId, cwd, workflowPath, parameters = {}, agent }) {
  if (skillId !== 'statemachine-use') throw new Error(`headless 実行に未対応のスキルです: ${skillId}`);
  if (!agent || agent.cli !== 'aider') throw new Error('statemachine-use の headless 実行は Aider のみ対応しています');
  const root = fs.realpathSync(String(cwd));
  const workflowFile = projectPath(root, workflowPath);
  const workflow = parseYaml(fs.readFileSync(workflowFile, 'utf8'));
  if (!isPlainObject(workflow) || !isPlainObject(workflow.states)) throw new Error('workflow.yaml を解析できません');
  const harnessSkill = resolveSkill('statemachine-use', root);
  if (!harnessSkill) throw new Error('statemachine-use スキルの実体が見つかりません');
  const scripts = {
    dry: path.join(harnessSkill.root, 'scripts', 'run_machine.py'),
    next: path.join(harnessSkill.root, 'scripts', 'next_state.py'),
  };
  const logDir = path.join(root, '.statemachine-use', 'logs');
  fs.mkdirSync(logDir, { recursive: true });
  const logFile = path.join(logDir, `dashboard-${Date.now()}-${process.pid}.jsonl`);
  const touched = new Set();
  await harness(scripts.dry, [workflowFile, '--dry-run'], { cwd: root, logFile });
  let current = await harness(scripts.next, [workflowFile, '--initial-state'], { cwd: root, logFile });
  const context = initialContext(workflow, parameters);
  const outputs = {};
  const maxSteps = Math.max(1, Number((workflow.config || {}).max_steps) || 50);
  let lastOutput = '';

  for (let step = 0; step < maxSteps; step += 1) {
    const state = workflow.states[current];
    if (!isPlainObject(state)) throw new Error(`ステートが見つかりません: ${current}`);
    if (state.terminal === true) {
      appendLog(logFile, { event: 'terminal', state: current, files: [...touched] });
      return {
        ...terminalStatus(current, lastOutput),
        stdout: lastOutput, stderr: '', finalState: current, logFile, files: [...touched],
      };
    }
    appendLog(logFile, { event: 'state', state: current, step: step + 1 });
    lastOutput = (await executeAction({
      workflowPath: workflowFile, stateId: current, state, context, cwd: root, agent, logFile, touched,
    })).trim();
    context.last_output = lastOutput;
    context.step_count = step + 1;
    context.history[current] = lastOutput;
    const outputKey = scalarString(state.output_key);
    if (outputKey) {
      context[outputKey] = lastOutput;
      outputs[outputKey] = lastOutput;
    }
    let next = await nextState({
      scripts, workflowPath: workflowFile, stateId: current, output: lastOutput,
      outputs, agent, cwd: root, logFile,
    });
    if (next === 'NONE') {
      const onNone = scalarString((workflow.config || {}).on_no_transition) || 'error';
      if (onNone === 'stop') return { ok: true, stdout: lastOutput, stderr: '', finalState: current, logFile };
      if (workflow.states[onNone]) next = onNone;
      else throw new Error(`ステート ${current} から一致する遷移がありません`);
    }
    current = next;
  }
  if (scalarString((workflow.config || {}).on_max_steps) === 'stop') {
    return { ok: true, stdout: lastOutput, stderr: '', finalState: current, logFile, files: [...touched] };
  }
  throw new Error(`最大ステップ数 (${maxSteps}) に到達しました`);
}

module.exports = {
  projectPath,
  resolveSkill,
  validateToolRequest,
  parseToolRequest,
  terminalStatus,
  runSkill,
};
