'use strict';

// エージェント CLI 連携層（charter の AI 下書き・補完）。kiro-project 本体と同じ
// エージェント CLI（kiro / claude / copilot）をヘッドレスで 1 回呼び出し、テキストだけを
// 受け取る。ファイルへの書き込みはこのビュアー側（authoring.js）が行う — 「人が書く
// 上位入力だけを書く」護りをエージェント経由で迂回しない。
//
// 使う CLI とモデルの解決順:
//   1. ⚙ 設定（agent.cli / agent.model）— 明示指定が最優先
//   2. プロジェクトの kiro-project.yaml（root 直下 → .kiro/ → ~/.kiro）の agent_cli / model
//      — 本体の分解・裁定と同じエージェントで補完するのが既定
//   3. どちらにも無ければ kiro（両ツールの既定と同じ）

const path = require('path');
const { spawn } = require('child_process');
const { readToolConfig } = require('./toolconfig');

const AGENT_CLIS = new Set(['kiro', 'claude', 'copilot']);

// プロジェクト設定（kiro-project.yaml → 無ければ kiro-flow.yaml）から agent_cli / model を拾う。
// 探索順は本体の _find_config と同じ root 直下 → .kiro/（readToolConfig が ~/.kiro も見る）。
function readProjectAgent(projectDir) {
  if (!projectDir) return {};
  const baseDirs = [projectDir, path.join(projectDir, '.kiro')];
  for (const name of ['kiro-project', 'kiro-flow']) {
    const cfg = readToolConfig(name, baseDirs);
    if (cfg && (cfg.values.agent_cli || cfg.values.model)) {
      return {
        cli: String(cfg.values.agent_cli || '').toLowerCase(),
        model: String(cfg.values.model || ''),
        file: cfg.file,
      };
    }
  }
  return {};
}

// ⚙ 設定 > プロジェクト設定 > 既定（kiro）の順で使う CLI・モデル・タイムアウトを確定する
function resolveAgent(cfg, projectDir) {
  const ac = (cfg && cfg.agent) || {};
  const proj = readProjectAgent(projectDir);
  const explicit = String(ac.cli || '').toLowerCase();
  const cli = AGENT_CLIS.has(explicit) ? explicit : AGENT_CLIS.has(proj.cli) ? proj.cli : 'kiro';
  const model = String(ac.model || '').trim() || (AGENT_CLIS.has(explicit) ? '' : String(proj.model || '').trim());
  const timeoutMs = Math.max(30, Number(ac.timeoutSec) || 180) * 1000;
  const source = AGENT_CLIS.has(explicit) ? 'settings' : AGENT_CLIS.has(proj.cli) ? 'project' : 'default';
  return { cli, model, timeoutMs, source, projectFile: proj.file || null };
}

// エージェント CLI のコマンドラインを組み立てる（kiro-project の _run_kiro_cli と同じ流儀）。
// claude はプロンプトを stdin 渡し、kiro / copilot は argv 渡し（charter 補完のプロンプトは
// 小さいためスピル退避は不要）。
function buildCommand(cli, model, prompt) {
  if (cli === 'claude') {
    const args = ['-p', '--output-format', 'text'];
    if (model) args.push('--model', model);
    return { command: 'claude', args, stdin: prompt };
  }
  if (cli === 'copilot') {
    // -s で応答本文のみ。--allow-all-tools は非対話モードの必須フラグ
    // （テキスト生成だけを期待するため --allow-all-paths は付けない）。
    const args = ['-s', '--allow-all-tools', '--no-color'];
    if (model) args.push('--model', model);
    args.push('-p', prompt);
    return { command: 'copilot', args, stdin: null };
  }
  const args = ['chat', '--no-interactive', '--trust-all-tools'];
  if (model) args.push('--model', model);
  args.push(prompt);
  return { command: 'kiro-cli', args, stdin: null };
}

function quote(arg) {
  const s = String(arg);
  if (/^[\w@%+=:,./-]+$/.test(s)) return s;
  return process.platform === 'win32' ? `"${s.replace(/"/g, '""')}"` : `'${s.replace(/'/g, "'\\''")}'`;
}

// ANSI エスケープを除去（kiro-cli 等が色付きで返しても JSON / markdown を壊さない）
function stripAnsi(s) {
  // eslint-disable-next-line no-control-regex
  return String(s || '').replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, '');
}

function runAgent({ cli, model, timeoutMs }, prompt) {
  const { command, args, stdin } = buildCommand(cli, model, prompt);
  // shell:true で PATH / Windows の .cmd 解決を OS に任せる（actions.runKiroCli と同じ）
  const cmdline = `${command} ${args.map(quote).join(' ')}`;
  return new Promise((resolve, reject) => {
    const child = spawn(cmdline, {
      shell: true,
      env: { ...process.env, NO_COLOR: '1', TERM: 'dumb' },
    });
    let out = '';
    let err = '';
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`${command} がタイムアウトしました（${Math.round(timeoutMs / 1000)} 秒）`));
    }, timeoutMs);
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (err += d));
    child.on('error', (e) => {
      clearTimeout(timer);
      reject(new Error(`${command} を起動できません（インストールと PATH を確認）: ${e.message}`));
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(stripAnsi(out).trim());
      else reject(new Error(`${command} が失敗しました (exit ${code}): ${stripAnsi(err || out).trim().slice(-400)}`));
    });
    if (stdin != null) {
      child.stdin.write(stdin);
    }
    child.stdin.end();
  });
}

// 応答から最初の JSON オブジェクト {...} を取り出す（説明文が混じっても拾う。
// kiro-project の _extract_json_obj と同じ流儀）
function extractJson(text) {
  const s = String(text || '');
  const start = s.indexOf('{');
  const end = s.lastIndexOf('}');
  if (start < 0 || end <= start) return null;
  try {
    const obj = JSON.parse(s.slice(start, end + 1));
    return obj && typeof obj === 'object' && !Array.isArray(obj) ? obj : null;
  } catch {
    return null;
  }
}

// コードフェンス（```markdown … ```）に包まれた応答から中身だけを取り出す
function stripFence(text) {
  const t = String(text || '').trim();
  const m = t.match(/^```[\w-]*\n([\s\S]*?)\n?```$/);
  return m ? m[1] : t;
}

// ---------------------------------------------------------------------------
// charter 補完プロンプト
// ---------------------------------------------------------------------------

// charter.md の書式契約（kiro-project の charter.md.example と authoring.buildCharter に一致）
const CHARTER_RULES = [
  '- goal は 1〜3 文。達成できたかを後から判定できる表現にする。',
  '- acceptance の各行は「終了コード 0 を PASS とみなすシェルコマンド」を最優先で書く。',
  '  コマンドにできない条件だけ "accept: <自然文>" 形式にする。',
  '- deliverables / constraints / assumptions は 1 行 1 項目の短い箇条書き。',
  '- 入力に既に書かれている内容は尊重して残し、空欄・不足だけを補う。',
  '- 実在が確認できないリポジトリ名・パス・コマンドを発明しない。不確かな前提は assumptions に書く。',
].join('\n');

// 下書きモード: フォームの書きかけ（goal・自由メモ等）から各セクションの JSON を作らせる
function charterDraftPrompt(spec) {
  const s = spec || {};
  const input = [
    `プロジェクト名: ${String(s.name || '').trim() || '(未定)'}`,
    `goal（書きかけ）: ${String(s.goal || '').trim() || '(空)'}`,
    `deliverables（書きかけ）:\n${String(s.deliverables || '').trim() || '(空)'}`,
    `constraints（書きかけ）:\n${String(s.constraints || '').trim() || '(空)'}`,
    `assumptions（書きかけ）:\n${String(s.assumptions || '').trim() || '(空)'}`,
    `acceptance（書きかけ）:\n${String(s.acceptance || '').trim() || '(空)'}`,
    `自由メモ（背景・要望）:\n${String(s.memo || '').trim() || '(なし)'}`,
  ].join('\n\n');
  return (
    'あなたはプロジェクト憲章（charter.md）の作成を手伝うアシスタントです。\n' +
    '以下の書きかけの入力から、各セクションを補完してください。\n\n' +
    '出力は次のキーを持つ JSON オブジェクト **のみ**（説明文・コードフェンスなし）:\n' +
    '{"goal": "...", "constraints": ["..."], "assumptions": ["..."], "deliverables": ["..."], "acceptance": ["..."]}\n\n' +
    `規約:\n${CHARTER_RULES}\n\n入力:\n${input}`
  );
}

// 補完モード: charter.md 全文を渡し、書式を保ったまま完成度を上げた全文を返させる
function charterRefinePrompt(content) {
  return (
    'あなたはプロジェクト憲章（charter.md）のレビュアー兼共同執筆者です。\n' +
    '以下の charter.md を、書式（# Charter: <name> 見出しと ' +
    '## goal / constraints / assumptions / deliverables / acceptance / repos / links の各セクション）を保ったまま、\n' +
    '不足セクションの補完・acceptance の検証可能化（シェルコマンド化）・曖昧な記述の明確化をして、\n' +
    '完成版の charter.md **全文だけ** を出力してください（前置き・説明文・コードフェンスなし）。\n\n' +
    `規約:\n${CHARTER_RULES}\n- ## repos の URL・owns 等は変更しない。\n\n` +
    `--- charter.md ---\n${String(content || '').trim() || '(空 — 雛形から書き起こしてください)'}`
  );
}

// draft 応答の JSON をフォームに流し込める形（文字列 or 改行区切り文字列）へ正規化する
function normalizeDraftFields(obj) {
  const lines = (v) =>
    (Array.isArray(v) ? v : String(v == null ? '' : v).split('\n'))
      .map((x) => String(x).replace(/^\s*-\s+/, '').trim())
      .filter(Boolean)
      .join('\n');
  return {
    goal: String((obj && obj.goal) || '').trim(),
    constraints: lines(obj && obj.constraints),
    assumptions: lines(obj && obj.assumptions),
    deliverables: lines(obj && obj.deliverables),
    acceptance: lines(obj && obj.acceptance),
  };
}

// charter 補完の入口（IPC agent:charter）。mode:
//   draft  … フォームの書きかけ（spec）→ 各セクションの JSON（fields）
//   refine … charter.md 全文（content）→ 完成版の全文（content）
async function completeCharter(cfg, { dir, mode, spec, content }) {
  const agent = resolveAgent(cfg, dir);
  if (mode === 'refine') {
    const raw = await runAgent(agent, charterRefinePrompt(content));
    const text = stripFence(raw);
    if (!/^#\s*Charter\s*:|\n##\s*goal/i.test(text)) {
      throw new Error(`エージェントの応答が charter.md の形式ではありません: ${text.slice(0, 120)}…`);
    }
    return { mode, content: `${text.replace(/\n+$/, '')}\n`, cli: agent.cli, model: agent.model, source: agent.source };
  }
  const raw = await runAgent(agent, charterDraftPrompt(spec));
  const obj = extractJson(raw);
  if (!obj) throw new Error(`エージェントの応答から JSON を取り出せませんでした: ${raw.slice(0, 120)}…`);
  return { mode: 'draft', fields: normalizeDraftFields(obj), cli: agent.cli, model: agent.model, source: agent.source };
}

module.exports = {
  AGENT_CLIS,
  resolveAgent,
  readProjectAgent,
  buildCommand,
  runAgent,
  extractJson,
  stripFence,
  charterDraftPrompt,
  charterRefinePrompt,
  normalizeDraftFields,
  completeCharter,
};
