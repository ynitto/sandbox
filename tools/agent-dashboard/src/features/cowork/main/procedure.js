'use strict';

// 定型手順（Routine Procedure）— 社内システムやローカルルールに特化した定型業務を、
// 画面操作（ブラウザ / Windows アプリ）・コマンド実行・AI の処理（生成・判断）の工程列として
// 組み立て、statemachine-use スキルの**作成モードへ渡す指示文**へ決定的に変換する。
//
// 担当は 3 つ。どれも新しい実行系・状態ストアを作らない。
//   1. 正規化（normalizeProcedure）… renderer から届いた工程列を信頼せず、種類・分岐先・
//      検査コマンドの形をここで検査する（画面が拾えなかった不備を main が最後に断る）。
//   2. 指示文（procedureInstruction）… スキルの分解原則（1 ステート 1 工程・成功は機械が
//      測れる形・分岐は遷移に書く・移譲先スキルを名指しする）に沿う Markdown を組む。
//      **YAML は書かない。** `.statemachine/<machine>/workflow.yaml` の書式の正典は
//      statemachine-use スキルで、書式をここに写すと 2 実装になる（C7）。
//   3. 道具の確認（toolStatus）… 画面操作の工程が頼る CLI（playwright-cli / winauto）が
//      この端末から呼べるかを、LLM を使わない診断コマンドだけで確かめる。
//
// 工程が頼る道具はどれも**エージェント CLI のシェルから呼べる CLI**である。ヘッドレスの
// ハーネス（agent-loop statemachine）はシェルを介さず argv を直接実行するので、
// 指示文でもパイプ・リダイレクトを使わない形に倒す（検査コマンドはここで断る）。

const templateParameters = require('../../../base/main/template-parameters');

// 工程の種類。`skill` は作成モードがアクション本文へ名指しする移譲先
// （`` `skill-name` スキル `` の記法が無いとハーネスはスキルを読み込まない）。
const STEP_KINDS = {
  browser: {
    label: '画面操作（ブラウザ）',
    skill: 'playwright-cli',
    tool: 'playwright-cli',
    targetLabel: 'URL',
  },
  windows: {
    label: '画面操作（Windows アプリ）',
    skill: 'windows-app-automation',
    tool: 'winauto',
    targetLabel: 'アプリ',
  },
  command: {
    label: 'コマンド実行',
    skill: '',
    tool: '',
    targetLabel: 'コマンド',
  },
  agent: {
    label: 'AI の処理（生成・判断）',
    skill: '',
    tool: '',
    targetLabel: '',
  },
};
const STEP_KIND_IDS = Object.keys(STEP_KINDS);

// 判断の行き先。`step:<n>` は 1 始まりの工程番号。
const OUTCOME_TARGETS = ['next', 'done', 'abort'];

// 上限。画面が組める量であると同時に、作成モードが 1 セッションで読み切れる量。
const MAX_STEPS = 30;
const MAX_OUTCOMES = 8;
const MAX_TEXT = 4000;

// statemachine-use の `check` はシェルを介さない。含まれていたら投入前に断る
// （黙って別物を実行させない——検知装置が別物を測るのは検知が無いより悪い）。
const SHELL_META_RE = /[|&;<>`\n]|\$\(/;

// 実行時に注入される変数は入力にしない（正典は template-parameters）。
function parameterKeys(spec) {
  const texts = [spec.purpose, spec.finish, spec.notes];
  for (const step of spec.steps) texts.push(step.title, step.detail, step.target, step.check);
  return templateParameters.inputParameterKeys(...texts);
}

function text(value, max = MAX_TEXT) {
  return String(value == null ? '' : value).replace(/\r\n/g, '\n').trim().slice(0, max);
}

// 判断のラベル。出力契約は「第 1 行がこのラベルで始まる」なので空白を含めない
// （`startswith:verdict:承認 済` のような揺れを作らない）。
function outcomeLabel(raw) {
  return text(raw, 40).replace(/\s+/g, '_');
}

function parseOutcomeTarget(raw, stepIndex, stepCount) {
  const value = text(raw, 20).toLowerCase();
  if (OUTCOME_TARGETS.includes(value)) return value;
  const m = /^step:(\d+)$/.exec(value);
  if (m) {
    const n = Number(m[1]);
    if (n < 1 || n > stepCount) {
      throw new Error(`工程 ${stepIndex + 1} の判断が存在しない工程 ${n} を指しています`);
    }
    return `step:${n}`;
  }
  throw new Error(`工程 ${stepIndex + 1} の判断の行き先が不正です: ${raw}`);
}

function normalizeCheck(raw, stepIndex) {
  const value = text(raw, 500);
  if (!value) return '';
  if (SHELL_META_RE.test(value)) {
    throw new Error(`工程 ${stepIndex + 1} の確認コマンドにシェル記号は使えません`
      + '（パイプやリダイレクトが要るならスクリプトにまとめてください）');
  }
  return value;
}

function normalizeStep(raw, index, stepCount) {
  const item = raw && typeof raw === 'object' ? raw : {};
  const kind = STEP_KIND_IDS.includes(String(item.kind || '')) ? String(item.kind) : '';
  if (!kind) throw new Error(`工程 ${index + 1} の種類が不正です`);
  const title = text(item.title, 80);
  const detail = text(item.detail);
  const target = text(item.target, 500);
  if (kind === 'command' && !target) {
    throw new Error(`工程 ${index + 1}（コマンド実行）にコマンドを入力してください`);
  }
  if (kind === 'command' && SHELL_META_RE.test(target)) {
    throw new Error(`工程 ${index + 1} のコマンドにシェル記号は使えません`
      + '（パイプやリダイレクトが要るならスクリプトにまとめてください）');
  }
  if (kind !== 'command' && !detail) {
    throw new Error(`工程 ${index + 1}（${STEP_KINDS[kind].label}）の内容を入力してください`);
  }
  const rawOutcomes = Array.isArray(item.outcomes) ? item.outcomes : [];
  if (rawOutcomes.length > MAX_OUTCOMES) {
    throw new Error(`工程 ${index + 1} の判断は ${MAX_OUTCOMES} 件までです`);
  }
  const seen = new Set();
  const outcomes = rawOutcomes.map((o) => {
    const label = outcomeLabel(o && o.label);
    if (!label) throw new Error(`工程 ${index + 1} の判断にラベルの無い行があります`);
    if (seen.has(label)) throw new Error(`工程 ${index + 1} の判断ラベルが重複しています: ${label}`);
    seen.add(label);
    return { label, to: parseOutcomeTarget(o && o.to, index, stepCount) };
  });
  return {
    id: `step_${index + 1}`,
    kind,
    title,
    detail,
    target,
    check: normalizeCheck(item.check, index),
    outcomes,
  };
}

// renderer から届いた手順を検査して正規形にする。落ちるときは人が直せる文言で落とす。
function normalizeProcedure(raw) {
  const src = raw && typeof raw === 'object' ? raw : {};
  const steps = Array.isArray(src.steps) ? src.steps : [];
  if (!steps.length) throw new Error('工程を 1 つ以上追加してください');
  if (steps.length > MAX_STEPS) throw new Error(`工程は ${MAX_STEPS} 件までです`);
  const spec = {
    purpose: text(src.purpose, 2000),
    steps: steps.map((step, index) => normalizeStep(step, index, steps.length)),
    finish: text(src.finish, 2000),
    notes: text(src.notes, 2000),
  };
  if (!spec.purpose) throw new Error('この業務の目的を入力してください');
  spec.parameters = parameterKeys(spec);
  return spec;
}

// --- 指示文 -----------------------------------------------------------------

function toolGuidance(kinds) {
  const lines = [];
  if (kinds.has('browser')) {
    lines.push(
      '- ブラウザの操作は `playwright-cli` スキル（`playwright-cli` コマンド）で行う。'
      + ' 操作の前に snapshot で要素を確かめ、操作の後も snapshot か画面のテキストで結果を読み取る。'
      + ' セレクタや URL は工程に書いたものだけを使い、想定と違う画面が出たら FAILED を返す。',
    );
  }
  if (kinds.has('windows')) {
    lines.push(
      '- Windows アプリの操作は `windows-app-automation` スキル（`winauto` コマンド）で行う。'
      + ' `winauto tree` / `winauto wait` で要素を確かめてから `click` / `type` / `keys` を送り、'
      + ' 結果は `get-text` で読み取る。セレクタは `auto_id:=` を優先し、無ければ `name:=` を使う。',
    );
  }
  if (kinds.has('command')) {
    lines.push('- コマンドは書いてある argv をそのまま実行する。シェル（パイプ・リダイレクト）は介さない。');
  }
  if (kinds.has('agent')) {
    lines.push('- AI の処理の工程は、前の工程の出力（{{last_output}} / output_key）だけを材料にし、画面やファイルを勝手に操作しない。');
  }
  lines.push('- 画面操作とコマンドの工程は、結果を機械で確かめられるなら `check` を宣言する（宣言した工程からは `equals:check_ok:true` で進む）。');
  lines.push('- どの工程でも、秘密情報（パスワード・トークン）をアクション本文に書かない。必要なら入力パラメータにする。');
  return lines;
}

function outcomeTargetLabel(to, index, stepCount) {
  if (to === 'next') return index + 1 < stepCount ? `工程 ${index + 2} へ進む` : '完了として終了する';
  if (to === 'done') return '完了として終了する';
  if (to === 'abort') return '失敗として終了する';
  const n = Number(String(to).slice('step:'.length));
  return n === index + 1 ? `工程 ${n}（この工程）をやり直す` : `工程 ${n} へ進む`;
}

function stepSection(step, index, stepCount) {
  const kind = STEP_KINDS[step.kind];
  // 名前を省いた工程は種類だけで見出しにする（「画面操作 2（画面操作）」の重複を作らない）。
  const lines = [step.title
    ? `### 工程 ${index + 1}: ${step.title}（${kind.label}）`
    : `### 工程 ${index + 1}: ${kind.label}`];
  if (step.kind === 'command') {
    lines.push(`- 実行するコマンド（argv）: \`${step.target}\``);
    if (step.detail) lines.push(`- 補足: ${step.detail.replace(/\n/g, '\n  ')}`);
  } else {
    if (step.target) lines.push(`- 対象${kind.targetLabel ? `（${kind.targetLabel}）` : ''}: ${step.target}`);
    lines.push(`- 内容:\n  ${step.detail.replace(/\n/g, '\n  ')}`);
  }
  if (kind.skill) {
    lines.push(`- アクション本文で \`${kind.skill}\` スキルへ移譲すると明記する。`);
  }
  if (step.check) {
    lines.push(`- 完了の確認: \`check: ${step.check}\` を宣言し、通ったときだけ次へ進む（check_retries: 1）。`);
  }
  if (step.outcomes.length) {
    lines.push(`- 出力契約: 第 1 行に次のいずれかだけを書く — ${step.outcomes.map((o) => o.label).join(' / ')}`);
    lines.push('- 遷移（分岐はアクション本文ではなく transitions に書く）:');
    for (const o of step.outcomes) {
      lines.push(`  - ${o.label} → ${outcomeTargetLabel(o.to, index, stepCount)}`);
    }
  } else {
    lines.push('- 出力契約: 第 1 行に OK（できた）または FAILED（できなかった）だけを書く。');
    lines.push(`- 遷移: OK → ${outcomeTargetLabel('next', index, stepCount)}、FAILED → 失敗として終了する。`);
  }
  return lines.join('\n');
}

// 作成モードへ渡す指示文。読む相手はスキルを持つエージェントで、人が読んでも手順書として通る形にする。
function procedureInstruction(spec) {
  const kinds = new Set(spec.steps.map((s) => s.kind));
  const stepCount = spec.steps.length;
  const parts = [];
  parts.push(`## 目的\n${spec.purpose}`);
  parts.push(`## 使う道具\n${toolGuidance(kinds).join('\n')}`);
  parts.push(`## 工程（この順に 1 ステート 1 工程で割る）\n\n${spec.steps.map((s, i) => stepSection(s, i, stepCount)).join('\n\n')}`);
  if (spec.parameters.length) {
    parts.push('## 入力パラメータ\n'
      + '実行時に人が値を入れる。アクション本文では `{{key}}` で参照し、値の既定は書かない。\n'
      + spec.parameters.map((key) => `- {{${key}}}`).join('\n'));
  }
  parts.push(`## 終了条件\n${spec.finish || '最後の工程が OK で終わったら完了。途中で FAILED になったら失敗として止める。'}`);
  const rules = [
    '- 工程の順と内容を勝手に足したり減らしたりしない。判断の分岐は遷移に書き、アクション本文には書かない。',
    '- 同じ工程へ戻る遷移があるなら、回数の上限を context のカウンタと config.max_steps で置く。',
    '- 画面操作の途中で想定と違う画面・要素が出たら、別の操作を試さずに FAILED を返す。',
    '- 作成後に `python .github/skills/statemachine-use/scripts/run_machine.py <workflow> --dry-run` で検証し、通らない定義は残さない。',
  ];
  if (spec.notes) rules.push(`- 注意事項: ${spec.notes.replace(/\n/g, '\n  ')}`);
  parts.push(`## 守ること\n${rules.join('\n')}`);
  return parts.join('\n\n');
}

// --- 道具の確認 ---------------------------------------------------------------

// LLM を使わない診断コマンドだけを呼ぶ。`doctor` / `--version` は読み取り専用で、
// winauto のデスクトップロックも取らない。
const TOOLS = [
  {
    id: 'playwright-cli',
    kinds: ['browser'],
    label: 'ブラウザ操作（playwright-cli）',
    command: 'playwright-cli',
    args: ['--version'],
    hint: 'install.py が @playwright/cli を入れます（npm が必要）。',
  },
  {
    id: 'winauto',
    kinds: ['windows'],
    label: 'Windows アプリ操作（winauto）',
    command: 'winauto',
    args: ['doctor', '--output', 'json'],
    hint: 'tools/winauto/install.py を実行し、winauto doctor で橋渡しを確認します。',
  },
];

function summarizeDoctor(stdout, stderr) {
  const raw = String(stdout || '').trim();
  try {
    const obj = JSON.parse(raw.slice(raw.indexOf('{')));
    const checks = Array.isArray(obj.checks) ? obj.checks : [];
    const failed = checks.filter((c) => c && c.ok === false);
    if (obj.ok === true && !failed.length) return { ok: true, summary: `診断 ${checks.length} 項目すべて OK` };
    const names = failed.map((c) => String(c.name || c.id || c.detail || c.message || '')).filter(Boolean);
    return { ok: false, summary: names.length ? `不備: ${names.join(' / ')}` : '診断に失敗した項目があります' };
  } catch {
    const line = (String(stderr || '').trim() || raw).split(/\r?\n/).find(Boolean) || '';
    return { ok: false, summary: line.slice(0, 200) || '診断の出力を読めませんでした' };
  }
}

function summarizeVersion(stdout, stderr) {
  const line = (String(stdout || '').trim() || String(stderr || '').trim()).split(/\r?\n/).find(Boolean) || '';
  return { ok: true, summary: line ? `利用可能（${line.slice(0, 80)}）` : '利用可能' };
}

// `capture(command, args, { cwd, timeoutMs })` は loopProvider.runCommandCapture と同じ形
// （テストでは差し替える）。kinds を渡すと、その工程が頼る道具だけを確かめる。
async function toolStatus({ cwd = '', kinds = [], capture, timeoutMs = 20000 } = {}) {
  if (typeof capture !== 'function') throw new Error('道具の確認に使う実行関数がありません');
  const wanted = new Set(Array.isArray(kinds) ? kinds : []);
  const targets = wanted.size ? TOOLS.filter((t) => t.kinds.some((k) => wanted.has(k))) : TOOLS;
  const out = [];
  for (const tool of targets) {
    let res;
    try {
      res = await capture(tool.command, tool.args, { cwd, timeoutMs });
    } catch (err) {
      res = { ok: false, status: -1, stdout: '', stderr: '', error: String((err && err.message) || err) };
    }
    const failedToRun = !res || res.status === -1 || res.error;
    let verdict;
    if (failedToRun) {
      verdict = { ok: false, summary: `コマンドを起動できません: ${(res && res.error) || tool.command}` };
    } else if (tool.id === 'winauto') {
      verdict = summarizeDoctor(res.stdout, res.stderr);
    } else if (res.ok) {
      verdict = summarizeVersion(res.stdout, res.stderr);
    } else {
      verdict = { ok: false, summary: (String(res.stderr || res.stdout || '').split(/\r?\n/).find(Boolean) || '終了コードが 0 ではありません').slice(0, 200) };
    }
    out.push({ id: tool.id, label: tool.label, ok: verdict.ok, summary: verdict.summary, hint: verdict.ok ? '' : tool.hint });
  }
  return out;
}

module.exports = {
  STEP_KINDS,
  STEP_KIND_IDS,
  OUTCOME_TARGETS,
  MAX_STEPS,
  MAX_OUTCOMES,
  TOOLS,
  normalizeProcedure,
  procedureInstruction,
  parameterKeys,
  outcomeLabel,
  toolStatus,
};
