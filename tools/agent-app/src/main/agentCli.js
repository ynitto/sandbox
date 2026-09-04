'use strict';

// agents/<name>.json（正典: schemas/agent-cli.schema.json）を読み、会話 1 ターン分の argv を組む。
// 完全なローダは agentcore/agentcli.py（Python）と agent-dashboard の agentCli.js にある。
// ここは会話に要る分だけ: command / 権限フラグ / モデル / プロンプトの渡し方 / セッション継続。
//
// 組み立て順は正典と同じ:
//   command + (write_args | readonly_args) + model_flag model + command_suffix + argv 渡しのプロンプト
// continue / resume だけは先頭の非オプション列（サブコマンド）の直後へ差し込む
// （codex の継続は `codex exec resume <id>` で、オプションの後ろに置くと別の意味になる）。

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');

// ponytail: CLI 固有のセッション ID の作法。定義ファイル（schema）へ昇格するまでここに置く。
//   mint    … こちらで UUID を発行し、初回に newArgs で渡す。2 回目以降は resume_args
//   capture … 初回の出力からセッション ID を拾う（codex は --json の thread.started）
//   list    … ターンのあと CLI のセッション一覧から最新を拾う（kiro）
// どれにも無い CLI は、continue_args があればそれ（直前セッション＝並行運転で混線する）、
// 無ければ会話履歴をプロンプトへ再送する。
const SESSION = {
  claude: { kind: 'mint', newArgs: ['--session-id', '{session}'] },
  copilot: { kind: 'mint', newArgs: ['--session-id', '{session}'] },
  codex: { kind: 'capture', extraArgs: ['--json'], pattern: /"thread_id"\s*:\s*"([^"]+)"/ },
  kiro: {
    kind: 'list',
    resumeArgs: ['--resume-id', '{session}'],
    listArgs: ['kiro-cli', 'chat', '--list-sessions', '--format', 'json'],
  },
};

function repoAgentsDir() {
  let dir = __dirname;
  for (let i = 0; i < 6; i += 1) {
    const cand = path.join(dir, 'agents');
    if (fs.existsSync(path.join(cand, 'kiro.json'))) return cand;
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  return '';
}

// 探索順は agents/README.md のとおり（環境変数 → リポジトリ → ~/.agents → ~/.kiro → 同梱）。
function searchDirs(repo) {
  const dirs = [];
  if (process.env.KIRO_AGENTS_DIR) dirs.push(String(process.env.KIRO_AGENTS_DIR));
  if (repo) dirs.push(path.join(String(repo), 'agents'));
  dirs.push(path.join(os.homedir(), '.agents', 'agents'));
  dirs.push(path.join(os.homedir(), '.kiro', 'agents'));
  const bundled = repoAgentsDir();
  if (bundled && !dirs.includes(bundled)) dirs.push(bundled);
  return dirs;
}

function strs(v) {
  return Array.isArray(v) ? v.map(String) : [];
}

function normalize(raw, name, file) {
  if (!raw || typeof raw !== 'object' || !Array.isArray(raw.command) || !raw.command.length) {
    throw new Error(`エージェント定義 ${file}: command は 1 要素以上の文字列配列が必須です`);
  }
  return {
    name: String(raw.name || name),
    file,
    command: strs(raw.command),
    commandSuffix: strs(raw.command_suffix),
    promptVia: raw.prompt_via === 'argv' ? 'argv' : 'stdin',
    promptFlag: raw.prompt_flag != null ? String(raw.prompt_flag) : null,
    modelFlag: raw.model_flag != null ? String(raw.model_flag) : null,
    defaultModel: raw.default_model != null ? String(raw.default_model) : '',
    output: raw.output === 'file' ? 'file' : 'stdout',
    env: raw.env && typeof raw.env === 'object' ? raw.env : {},
    writeArgs: strs(raw.write_args),
    readonlyArgs: strs(raw.readonly_args),
    readonly: raw.readonly === 'enforced' ? 'enforced' : 'best-effort',
    continueArgs: strs(raw.continue_args),
    resumeArgs: strs(raw.resume_args),
    errors: (Array.isArray(raw.errors) ? raw.errors : []).map((e) => ({
      cls: String((e && e.class) || 'env'),
      re: new RegExp(String((e && e.match) || ''), 'i'),
      hint: String((e && e.hint) || ''),
    })),
    session: SESSION[String(raw.name || name)] || null,
  };
}

function load(name, repo) {
  const key = String(name || '').trim().toLowerCase();
  if (!/^[\w.-]+$/.test(key)) throw new Error(`agent_cli の名前が不正です: ${name}`);
  const dirs = searchDirs(repo);
  for (const dir of dirs) {
    const file = path.join(dir, `${key}.json`);
    let text;
    try { text = fs.readFileSync(file, 'utf8'); } catch { continue; }
    let raw;
    try { raw = JSON.parse(text); } catch (e) { throw new Error(`エージェント定義 ${file}: JSON として読めません: ${e.message}`); }
    return normalize(raw, key, file);
  }
  throw new Error(`未知の agent_cli です: ${key}（探索順: ${dirs.join(' → ')}）`);
}

// PATH からその名前の実体を引く（無ければ空文字）。画面の「使える」印と、Windows の .cmd 判定に使う。
function resolvePath(cmd, env = process.env) {
  if (/[\\/]/.test(cmd)) return fs.existsSync(cmd) ? cmd : '';
  const sep = process.platform === 'win32' ? ';' : ':';
  const exts = process.platform === 'win32'
    ? String(env.PATHEXT || '.COM;.EXE;.BAT;.CMD').split(';').map((e) => e.toLowerCase()) : [''];
  for (const dir of String(env.PATH || env.Path || '').split(sep)) {
    if (!dir) continue;
    for (const ext of exts) {
      const file = path.join(dir, cmd + ext);
      try { if (fs.statSync(file).isFile()) return file; } catch { /* 次へ */ }
    }
  }
  return '';
}

// 定義を全部並べる（同名は先勝ち）。会話に使えるのは command を持つ定義だけ。
function list(repo) {
  const seen = new Map();
  for (const dir of searchDirs(repo)) {
    let names;
    try { names = fs.readdirSync(dir); } catch { continue; }
    for (const f of names.filter((n) => n.endsWith('.json')).sort()) {
      const key = f.slice(0, -5).toLowerCase();
      if (seen.has(key)) continue;
      try {
        const spec = normalize(JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')), key, path.join(dir, f));
        seen.set(key, {
          name: key,
          available: !!resolvePath(spec.command[0]),
          readonly: spec.readonly,
          session: spec.session ? spec.session.kind : (spec.continueArgs.length ? 'continue' : 'replay'),
        });
      } catch { /* 壊れた定義は一覧に出さない（選んだときに load が理由を言う） */ }
    }
  }
  return [...seen.values()];
}

// 先頭の非オプション列（`codex exec` / `kiro-cli chat`）の直後に差し込む。
function insertAfterSubcommand(argv, frag) {
  let i = 0;
  while (i < argv.length && !argv[i].startsWith('-')) i += 1;
  return [...argv.slice(0, i), ...frag, ...argv.slice(i)];
}

function expand(tokens, vars, holder) {
  const out = [];
  for (const tok of tokens) {
    let t = tok;
    if (t.includes('{model}')) {
      if (!vars.model) continue;
      t = t.split('{model}').join(vars.model);
    }
    if (t.includes('{session}')) t = t.split('{session}').join(vars.session);
    if (t.includes('{output_file}')) {
      if (!holder.path) {
        holder.path = path.join(os.tmpdir(), `agent-app-${process.pid}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}.txt`);
      }
      t = t.split('{output_file}').join(holder.path);
    }
    out.push(t);
  }
  return out;
}

// セッション機能の無い CLI 向け: これまでの会話をプロンプトへ再送する。
function replayPrompt(history, prompt) {
  const lines = history.map((m) => `[${m.role === 'user' ? 'user' : 'assistant'}] ${m.text}`);
  return `これまでの会話（同じセッションの続きとして扱うこと）:\n${lines.join('\n\n')}\n\n---\n新しい依頼:\n${prompt}`;
}

// 1 ターン分の起動仕様（実行はしない・決定的。UUID の発行だけは乱数）。
//   cliSession … 既に分かっている CLI 側のセッション ID（空なら初回）
//   history    … これまでの会話（再送フォールバック用）
function turnCmd(spec, { prompt, model = '', readonly = false, cliSession = '', history = [] } = {}) {
  const vars = { model: String(model || spec.defaultModel || ''), session: cliSession };
  const holder = {};
  const strategy = spec.session;
  let mintedSession = '';
  let frag = [];
  let text = String(prompt || '');
  if (cliSession) {
    frag = (strategy && strategy.resumeArgs) || spec.resumeArgs;
    if (!frag.length) throw new Error(`${spec.name} はセッション再開の argv を持ちません`);
  } else if (strategy && strategy.kind === 'mint') {
    mintedSession = crypto.randomUUID();
    vars.session = mintedSession;
    frag = strategy.newArgs;
  } else if (strategy) {
    frag = [];                                   // capture / list は初回に何も足さない
  } else if (history.length && spec.continueArgs.length) {
    // ponytail: --continue は「直前のセッション」で、同じ CLI を並行して使うと混線する。
    // 直すなら SESSION に ID の拾い方を足す。
    frag = spec.continueArgs;
  } else if (history.length) {
    text = replayPrompt(history, text);
  }
  let argv = expand(spec.command, vars, holder);
  if (strategy && strategy.extraArgs) argv = argv.concat(strategy.extraArgs);
  argv = insertAfterSubcommand(argv, expand(frag, vars, holder));
  argv = argv.concat(expand(readonly ? spec.readonlyArgs : spec.writeArgs, vars, holder));
  if (vars.model && spec.modelFlag && !spec.command.some((t) => t.includes('{model}'))) {
    argv.push(spec.modelFlag, vars.model);
  }
  argv = argv.concat(expand(spec.commandSuffix, vars, holder));
  let stdin = null;
  if (spec.promptVia === 'argv') {
    if (spec.promptFlag) argv.push(spec.promptFlag, text);
    else argv.push(text);
  } else {
    stdin = text;
  }
  return {
    command: argv[0],
    args: argv.slice(1),
    argv,
    stdin,
    outputFile: holder.path || null,
    env: spec.env,
    mintedSession,
    capture: strategy && strategy.kind === 'capture' ? strategy.pattern : null,
    listArgs: strategy && strategy.kind === 'list' ? strategy.listArgs : null,
    readonlyWarning: (readonly && spec.readonly !== 'enforced')
      ? `${spec.name} は読み取り専用を保証しません（ファイル変更やコマンド実行が起こりえます）` : '',
  };
}

// kiro の一覧（[{cwd, sessions:[{sessionId, updatedAt}]}]）から、このターンの後に更新された最新を選ぶ。
function pickListedSession(json, cwd, sinceMs) {
  let groups;
  try { groups = JSON.parse(json); } catch { return ''; }
  const norm = (p) => path.resolve(String(p || ''));
  const sessions = (Array.isArray(groups) ? groups : [])
    .filter((g) => !cwd || norm(g.cwd) === norm(cwd))
    .flatMap((g) => (Array.isArray(g.sessions) ? g.sessions : []))
    .filter((s) => s && s.sessionId && Date.parse(s.updatedAt) >= sinceMs)
    .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt));
  return sessions.length ? String(sessions[0].sessionId) : '';
}

function classifyError(spec, blob) {
  const text = String(blob || '');
  for (const rule of spec.errors) if (rule.re.test(text)) return rule;
  return null;
}

module.exports = {
  SESSION, searchDirs, load, list, resolvePath, turnCmd, insertAfterSubcommand,
  replayPrompt, pickListedSession, classifyError,
};
