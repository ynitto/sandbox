'use strict';

// 工程列（procedure）と statemachine-use の定義（workflow.yaml + actions/*.md）を
// **決定的に**往復させる 1 か所。
//
//   compile   … 工程列 → 定義。statemachine-use の作成モードの原則に沿って書く:
//               1 ステート 1 工程／出力契約（第 1 行のラベル）を output_validator に／
//               分岐は transitions に（condition_rule で決定的に）／成果は check で測る／
//               移譲先スキルを本文で名指しする／本文の末尾に単一指示を付ける。
//   decompile … 定義 → 工程列。maker.json（画面が残す写し）があればそれを使い、無ければ
//               YAML と actions から読み戻す。画面で表せない遷移・ステート（自然言語条件・
//               ワイルドカード等）は原文のまま保持して書き戻す（消さない）。
//   validate  … スキルの engine.validate_workflow と同じ規則の構造検査（投入前に落とす）。
//               本物の検証はスキルのスクリプト（run_machine.py --dry-run）に任せ、
//               ここは画面がその場で出せる範囲を持つ。
//
// このモジュールは Electron・ファイルシステムに触れない（単体で検査できる）。
// 生成する定義は OS に依らない: パスは `/` 区切り、改行は LF、シェルを介す記述は断る。

const YAML = require('yaml');
const templateParameters = require('./template-parameters');

const PROCEDURE_VERSION = 3;
const MAX_STEPS = 40;
const MAX_OUTCOMES = 8;
const MAX_TEXT = 6000;
const MAX_RECORDED = 40;

// statemachine-use の `check` はシェルを介さない。含まれていたら投入前に断る。
const SHELL_META_RE = /[|&;<>`\n]|\$\(/;
const STATE_ID_RE = /^[A-Za-z][A-Za-z0-9_]*$/;
const MACHINE_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;
const SKILL_NAME_RE = /^[a-z0-9][a-z0-9._-]*$/;
const RESERVED_STATE_IDS = new Set(['complete', 'failed']);

// 作成モードの原則 4: 全アクション本文の末尾に付ける単一指示。
const TRAILER = 'この指示に従ってタスクを実行してください。\n'
  + '完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。';

const DEFAULT_LABELS = ['OK', 'FAILED'];

// --- 工程の種類（正典） ----------------------------------------------------------
// label / description / target / detail / check … 画面が入力欄を描く材料
// skill / skillFromTarget                        … 本文で名指しする移譲先
// preamble(step)                                 … 本文の先頭に置く「何で・どう」の案内
// rules                                          … 本文に載せる守ること
const STEP_KINDS = [
  {
    id: 'browser',
    label: '画面操作（ブラウザ）',
    short: 'ブラウザ',
    description: 'Web 画面を開いて操作し、表示内容を読み取る',
    skill: 'playwright-cli',
    target: { label: 'URL', placeholder: 'https://…', required: false },
    detail: { label: '内容', required: true, placeholder: '例: ログイン後に「申請一覧」を開き、今日の日付の行を読み取る' },
    check: { placeholder: '例: python scripts/check_list.py' },
    recordable: true,
    preamble: (step) => ['`playwright-cli` スキル（`playwright-cli` コマンド）でブラウザを操作します。',
      step.target ? `対象 URL: ${step.target}` : ''].filter(Boolean).join('\n'),
    rules: [
      '操作の前に snapshot で要素を確かめ、操作の後も snapshot か画面のテキストで結果を読み取る。',
      'セレクタや URL はこの指示に書いたものだけを使い、想定と違う画面が出たら別の操作を試さずに FAILED を返す。',
    ],
    recordedLine: (op) => {
      const value = op.value ? ` ${JSON.stringify(op.value)}` : '';
      return op.op === 'goto' ? `goto ${op.target}` : `${op.op} ${op.target}${value}`;
    },
    recordedHint: '記録の行は `<操作> <ロケータ式> [値]` で、`playwright-cli <操作> "<ロケータ式>" [値]` として実行できます。',
  },
  {
    id: 'windows',
    label: '画面操作（Windows アプリ）',
    short: 'Windows',
    description: 'Windows のデスクトップアプリを操作し、表示内容を読み取る（実行は Windows 上）',
    skill: 'windows-app-automation',
    target: { label: 'アプリ', placeholder: '例: 勤怠管理', required: false },
    detail: { label: '内容', required: true, placeholder: '例: メニュー「集計」→「月次」を開き、対象月 {{month}} を入力して「出力」を押す' },
    check: { placeholder: '例: winauto wait name:=完了 --app 勤怠管理' },
    recordable: true,
    preamble: (step) => ['`windows-app-automation` スキル（`winauto` コマンド）で Windows アプリを操作します。',
      step.target ? `対象アプリ: ${step.target}` : ''].filter(Boolean).join('\n'),
    rules: [
      '`winauto tree` / `winauto wait` で要素を確かめてから `click` / `type` / `select` / `keys` を送り、結果は `get-text` で読み取る。',
      'セレクタは `auto_id:=` を優先し、無ければ `name:=` を使う。想定と違う画面が出たら別の操作を試さずに FAILED を返す。',
    ],
    recordedLine: (op) => {
      const value = op.value ? ` ${JSON.stringify(op.value)}` : '';
      return `${op.op} ${JSON.stringify(op.target)}${value}`;
    },
    recordedHint: '記録の行は `<操作> <セレクタ> [値]` です。`launch` → `winauto launch`、`window` → `winauto wait` で待つ、'
      + '`click` / `check` / `uncheck` → `winauto click`、`fill` / `type` → `winauto type`、`select` → `winauto select`、`keys` → `winauto keys`。',
  },
  {
    id: 'skill',
    label: 'スキルに任せる',
    short: 'スキル',
    description: '社内システム向けのスキル（redmine-use、outlook-use など）に工程を任せる',
    skill: '',
    skillFromTarget: true,
    target: { label: 'スキル名', placeholder: '例: redmine-use', required: true, pattern: 'skill' },
    detail: { label: '内容', required: true, placeholder: '例: 対象月のチケット一覧を取得し、未完了のものを表にする' },
    check: { placeholder: '例: python scripts/check_tickets.py' },
    preamble: (step) => `\`${step.target}\` スキルに任せます。SKILL.md に書かれた使い方とスクリプトだけを使ってください。`,
    rules: ['スキルが見つからなければ FAILED を返し、代わりの手段を探さない。'],
  },
  {
    id: 'command',
    label: 'コマンド実行',
    short: 'コマンド',
    description: '決まったコマンドを実行する（シェルは介さない）',
    skill: '',
    target: { label: 'コマンド', placeholder: '例: python scripts/export.py --month {{month}}', required: true, pattern: 'argv' },
    detail: { label: '補足', required: false, placeholder: '補足（任意）' },
    check: { placeholder: '例: python scripts/check_report.py' },
    preamble: (step) => `次のコマンドをそのまま実行します（シェルは介さず、書いてある argv だけ）:\n\n\`${step.target}\``,
    rules: ['コマンドの終了コードが 0 でなければ FAILED を返す。'],
  },
  {
    id: 'agent',
    label: 'AI の処理（生成・判断）',
    short: 'AI',
    description: '前の工程の結果を材料に、文章を作る・分類する・次へ進むか判断する',
    skill: '',
    target: null,
    detail: { label: '内容', required: true, placeholder: '例: 読み取った申請内容を要約し、差し戻しが必要か判断する' },
    check: { placeholder: '例: python -m pytest tests -q（成果物を作る工程なら）' },
    preamble: () => '',
    rules: ['前の工程の出力（{{last_output}}）だけを材料にし、画面やファイルを勝手に操作しない。'],
  },
];
const STEP_KIND_BY_ID = new Map(STEP_KINDS.map((k) => [k.id, k]));

function catalog() {
  return STEP_KINDS.map((k) => ({
    id: k.id, label: k.label, short: k.short, description: k.description,
    target: k.target ? { label: k.target.label, placeholder: k.target.placeholder, required: !!k.target.required } : null,
    detail: { label: k.detail.label, placeholder: k.detail.placeholder, required: !!k.detail.required },
    check: k.check ? { placeholder: k.check.placeholder } : null,
    recordable: !!k.recordable,
  }));
}

// --- 正規化 -------------------------------------------------------------------------

function text(value, max = MAX_TEXT) {
  return String(value == null ? '' : value).replace(/\r\n?/g, '\n').trim().slice(0, max);
}

// 判断のラベル。出力契約は「第 1 行がこのラベルで始まる」なので空白を含めない。
function outcomeLabel(raw) {
  return text(raw, 40).replace(/\s+/g, '_');
}

const OUTCOME_TARGETS = ['next', 'done', 'abort'];

// 次にどこへ行くかの決め方。既存の定義を読み戻しても画面で直せるように 4 つ持つ。
//   label  … 結果の第 1 行が〈語〉で始まるとき     → condition_rule: startswith:last_output:<語>
//   text   … 文章で書いた条件（AI が YES/NO で見る） → condition: <文章>
//   always … いつでも（無条件）                     → 条件を付けない
//   rule   … 条件式そのまま（既存の定義を保つ口）    → condition_rule: <式>
// text と always を持たなかったころは、この形の定義を「画面で直せない」として原文のまま
// 抱えていた。手で書いた定義はほとんどがこの形なので、読み戻して直せる意味が薄かった。
const OUTCOME_WHENS = ['label', 'text', 'always', 'rule'];

// 行き先は next / done / abort / step:n のほか、`end:<ステートID>` も取る。
// 手で書いた定義は終わり方を 2 つより多く持つ（承認・条件付き承認・差し戻し…）ので、
// 終端を 2 つに決め打つと、そこへ行く工程がまるごと「画面で直せない」になっていた。
function parseOutcomeTarget(raw, index, stepCount, endIds) {
  const value = text(raw, 80);
  const lower = value.toLowerCase();
  if (OUTCOME_TARGETS.includes(lower)) return lower;
  const m = /^step:(\d+)$/.exec(lower);
  if (m) {
    const n = Number(m[1]);
    if (n < 1 || n > stepCount) throw new Error(`工程 ${index + 1} の行き先が存在しない工程 ${n} を指しています`);
    return `step:${n}`;
  }
  const e = /^end:(.+)$/.exec(value);
  if (e && endIds && endIds.has(e[1])) return `end:${e[1]}`;
  throw new Error(`工程 ${index + 1} の行き先が不正です: ${raw}`);
}

function rejectShellMeta(value, what, index) {
  if (SHELL_META_RE.test(value)) {
    throw new Error(`工程 ${index + 1} の${what}にシェル記号は使えません（パイプやリダイレクトが要るならスクリプトにまとめてください）`);
  }
}

function normalizeTarget(kind, raw, index) {
  if (!kind.target) return '';
  const value = text(raw, 500);
  if (!value) {
    if (kind.target.required) throw new Error(`工程 ${index + 1}（${kind.label}）に${kind.target.label}を入力してください`);
    return '';
  }
  if (kind.target.pattern === 'argv') rejectShellMeta(value, 'コマンド', index);
  if (kind.target.pattern === 'skill' && !SKILL_NAME_RE.test(value)) {
    throw new Error(`工程 ${index + 1} のスキル名が不正です: ${value}（英小文字・数字・ハイフン）`);
  }
  return value;
}

const RECORDED_OPS = new Set(['goto', 'launch', 'click', 'dblclick', 'fill', 'type', 'press', 'select', 'check', 'uncheck', 'hover', 'keys', 'window']);

function normalizeRecorded(raw, index, kind) {
  const list = Array.isArray(raw) ? raw : [];
  if (!list.length) return [];
  if (!kind.recordable) throw new Error(`工程 ${index + 1}（${kind.label}）は操作の記録を持てません`);
  if (list.length > MAX_RECORDED) throw new Error(`工程 ${index + 1} の記録した操作は ${MAX_RECORDED} 件までです`);
  return list.map((op, n) => {
    const item = op && typeof op === 'object' ? op : {};
    const name = String(item.op || '');
    if (!RECORDED_OPS.has(name)) throw new Error(`工程 ${index + 1} の記録 ${n + 1} の操作が不正です: ${name}`);
    const target = text(item.target, 300);
    if (/^e\d+$/.test(target)) {
      throw new Error(`工程 ${index + 1} の記録 ${n + 1} は要素を ref（${target}）で指しています。role と名前で指してください`);
    }
    const out = { op: name, target, label: text(item.label, 120) };
    if (item.role) out.role = text(item.role, 40);
    if (item.value) out.value = text(item.value, 300);
    if (item.example) out.example = text(item.example, 120);
    return out;
  });
}

function defaultStepId(index) {
  return `step_${index + 1}`;
}

// 1 行ぶんの「次にどこへ行くか」。`when` の無い古い形（{label, to}）は label として読む。
function normalizeOutcome(raw, index, stepCount, seen, endIds) {
  const o = raw && typeof raw === 'object' ? raw : {};
  const when = OUTCOME_WHENS.includes(o.when) ? o.when : 'label';
  const to = parseOutcomeTarget(o.to, index, stepCount, endIds);
  if (when === 'text') {
    const body = text(o.text, 1000).replace(/\n+/g, ' ');
    if (!body) throw new Error(`工程 ${index + 1} に、条件の文章が空の行があります`);
    return { when, text: body, to };
  }
  if (when === 'rule') {
    const rule = text(o.rule, 300).replace(/\n+/g, ' ');
    if (!rule) throw new Error(`工程 ${index + 1} に、条件式が空の行があります`);
    return { when, rule, to };
  }
  if (when === 'always') return { when, to };
  const label = outcomeLabel(o.label);
  if (!label) throw new Error(`工程 ${index + 1} に、結果の名前が空の行があります`);
  if (seen.has(label)) throw new Error(`工程 ${index + 1} の結果の名前が重複しています: ${label}`);
  seen.add(label);
  return { when: 'label', label, to };
}

function normalizeStep(raw, index, stepCount, usedIds, endIds) {
  const item = raw && typeof raw === 'object' ? raw : {};
  const kind = STEP_KIND_BY_ID.get(String(item.kind || ''));
  if (!kind) throw new Error(`工程 ${index + 1} の種類が不正です`);
  const id = text(item.id, 60) || defaultStepId(index);
  if (!STATE_ID_RE.test(id)) throw new Error(`工程 ${index + 1} のステート ID が不正です: ${id}（英字始まり・英数字とアンダースコア）`);
  if (RESERVED_STATE_IDS.has(id)) throw new Error(`工程 ${index + 1} のステート ID ${id} は終端ステート用に予約されています`);
  if (usedIds.has(id)) throw new Error(`ステート ID が重複しています: ${id}`);
  usedIds.add(id);
  const detail = text(item.detail);
  if (kind.detail.required && !detail) throw new Error(`工程 ${index + 1}（${kind.label}）の${kind.detail.label}を入力してください`);
  const check = kind.check ? text(item.check, 500) : '';
  if (check) rejectShellMeta(check, '確認コマンド', index);
  const checkRetries = Math.max(0, Math.min(5, Number(item.checkRetries == null ? 1 : item.checkRetries) || 0));
  // 遷移を原文のまま持つ工程（自然言語条件・無条件遷移など、画面で表せない遷移を持つ既存の定義）。
  // 判断は持たず、コンパイルは遷移を生成しない（preserved.transitions がそのまま書かれる）。
  const rawTransitions = !!item.rawTransitions;
  const rawOutcomes = rawTransitions ? [] : (Array.isArray(item.outcomes) ? item.outcomes : []);
  if (rawOutcomes.length > MAX_OUTCOMES) throw new Error(`工程 ${index + 1} の判断は ${MAX_OUTCOMES} 件までです`);
  const seen = new Set();
  const outcomes = rawOutcomes.map((o) => normalizeOutcome(o, index, stepCount, seen, endIds));
  return {
    id, kind: kind.id, title: text(item.title, 80), detail,
    target: normalizeTarget(kind, item.target, index),
    check, checkRetries, outcomes, rawTransitions,
    recorded: normalizeRecorded(item.recorded, index, kind),
  };
}

function normalizeTerminal(raw, fallbackId, fallbackDesc) {
  const item = raw && typeof raw === 'object' ? raw : {};
  const id = text(item.id, 60) || fallbackId;
  if (!STATE_ID_RE.test(id)) throw new Error(`終端ステート ID が不正です: ${id}`);
  return { id, description: text(item.description, 120) || fallbackDesc };
}

// 画面から届いた工程列を検査して正規形にする。落ちるときは人が直せる文言で落とす。
function normalizeProcedure(raw) {
  const src = raw && typeof raw === 'object' ? raw : {};
  const version = src.version == null ? PROCEDURE_VERSION : Number(src.version);
  if (!(version >= 1 && version <= PROCEDURE_VERSION)) throw new Error(`この工程列は対応していない版です（version ${src.version}）`);
  const steps = Array.isArray(src.steps) ? src.steps : [];
  if (!steps.length) throw new Error('工程を 1 つ以上追加してください');
  if (steps.length > MAX_STEPS) throw new Error(`工程は ${MAX_STEPS} 件までです`);
  const machine = text(src.machine, 80);
  if (machine && (!MACHINE_ID_RE.test(machine) || machine === '.' || machine === '..')) {
    throw new Error(`識別名が不正です: ${machine}（英数字・ハイフン・アンダースコア・ドット）`);
  }
  const usedIds = new Set();
  const preserved = normalizePreserved(src.preserved);
  const ends = normalizeEnds(src.ends, preserved);
  const endIds = new Set(ends.map((e) => e.id));
  const spec = {
    version: PROCEDURE_VERSION,
    name: text(src.name, 120),
    machine,
    purpose: text(src.purpose, 2000),
    finish: text(src.finish, 2000),
    notes: text(src.notes, 2000),
    maxSteps: Math.max(1, Math.min(500, Number(src.maxSteps) || 30)),
    ends,
    steps: steps.map((step, index) => normalizeStep(step, index, steps.length, usedIds, endIds)),
    terminals: {
      done: normalizeTerminal(src.terminals && src.terminals.done, 'complete', '完了'),
      abort: normalizeTerminal(src.terminals && src.terminals.abort, 'failed', '失敗として終了'),
    },
    preserved,
  };
  if (spec.terminals.done.id === spec.terminals.abort.id) throw new Error('完了と失敗の終端ステート ID が同じです');
  for (const t of [spec.terminals.done.id, spec.terminals.abort.id]) {
    if (usedIds.has(t)) throw new Error(`ステート ID が終端ステートと重複しています: ${t}`);
  }
  if (!spec.name) throw new Error('名前を入力してください');
  spec.parameters = parameterKeys(spec);
  return spec;
}

// 完了・中止のほかの終わり方（手で書いた定義が持つ終端）。原文の states に在るものだけを許す。
function normalizeEnds(raw, preserved) {
  const list = Array.isArray(raw) ? raw : [];
  const out = [];
  const seen = new Set();
  for (const item of list) {
    const id = text(item && item.id, 60);
    const state = preserved.states[id];
    if (!id || seen.has(id) || !state || !state.terminal) continue;
    seen.add(id);
    out.push({ id, description: text(item.description, 120) || text(state.description, 120) || id });
  }
  return out;
}

// 画面で表せない部分（原文のまま保持して書き戻す）。
function normalizePreserved(raw) {
  const src = raw && typeof raw === 'object' ? raw : {};
  return {
    context: src.context && typeof src.context === 'object' && !Array.isArray(src.context) ? src.context : {},
    config: src.config && typeof src.config === 'object' && !Array.isArray(src.config) ? src.config : {},
    states: src.states && typeof src.states === 'object' && !Array.isArray(src.states) ? src.states : {},
    transitions: Array.isArray(src.transitions) ? src.transitions : [],
    files: src.files && typeof src.files === 'object' && !Array.isArray(src.files) ? src.files : {},
  };
}

// 実行時に注入される変数は入力にしない（正典は template-parameters）。
function parameterKeys(spec) {
  const texts = [spec.purpose, spec.finish, spec.notes];
  for (const step of spec.steps) {
    texts.push(step.title, step.detail, step.target, step.check);
    for (const op of step.recorded || []) texts.push(op.value);
  }
  return templateParameters.inputParameterKeys(...texts);
}

// --- 遷移の解決（画面表示とコンパイルの両方が使う） ------------------------------------

// 出力契約（第 1 行の語）に使えるのは label の行だけ。文章の条件や無条件だけの工程には
// 契約が無い——そこへ勝手に契約を足すと、手で書いた定義の意味を変えてしまう。
function stepLabels(step) {
  if (!step.outcomes.length) return DEFAULT_LABELS.slice();
  return step.outcomes.filter((o) => o.when === 'label').map((o) => o.label);
}

// 工程 index の遷移一覧。{ label, to: 'next'|'done'|'abort'|'step:n', target: state_id, gated }
function stepTransitions(spec, index) {
  const step = spec.steps[index];
  const nextId = index + 1 < spec.steps.length ? spec.steps[index + 1].id : spec.terminals.done.id;
  const resolve = (to) => {
    if (to === 'next') return nextId;
    if (to === 'done') return spec.terminals.done.id;
    if (to === 'abort') return spec.terminals.abort.id;
    if (to.startsWith('end:')) return to.slice('end:'.length);
    return spec.steps[Number(to.slice('step:'.length)) - 1].id;
  };
  if (step.rawTransitions) return [];
  if (step.outcomes.length) {
    return step.outcomes.map((o) => ({ ...o, target: resolve(o.to), gated: !!step.check }));
  }
  if (step.check) {
    // 検査を宣言した工程は、ハーネスが測った事実で進む（モデルの OK は材料に入れない）。
    return [{ when: 'always', to: 'next', target: nextId, gated: true }];
  }
  return [
    { when: 'label', label: 'OK', to: 'next', target: nextId, gated: false },
    { when: 'label', label: 'FAILED', to: 'abort', target: spec.terminals.abort.id, gated: false },
  ];
}

function describeTarget(spec, index, to) {
  const count = spec.steps.length;
  if (to === 'done') return '完了';
  if (to === 'abort') return '失敗として終了';
  if (to.startsWith('end:')) {
    const id = to.slice('end:'.length);
    const end = (spec.ends || []).find((e) => e.id === id);
    return end ? end.description : id;
  }
  if (to === 'next') return index + 1 < count ? `工程 ${index + 2} へ` : '完了';
  const n = Number(String(to).slice('step:'.length));
  if (n === index + 1) return 'この工程をやり直す';
  return n <= index ? `工程 ${n} へ戻る` : `工程 ${n} へ`;
}

// --- コンパイル（工程列 → 定義） -------------------------------------------------------

function kindOf(step) {
  return STEP_KIND_BY_ID.get(step.kind);
}

function outputFormatLine(step) {
  if (step.rawTransitions) return '**出力形式:** 第1行に結果を表す一語（遷移条件が見る語）を返してください。';
  if (!step.outcomes.length) return '**出力形式:** 第1行に OK（完了）または FAILED（できなかった）だけを返してください。';
  const labels = stepLabels(step);
  // 文章の条件だけの工程には第 1 行の契約が無い。無いものを在るように書かない。
  if (!labels.length) return '**出力形式:** 何をどうしたか、結果が判別できるように短く返してください。';
  const rest = step.outcomes.length - labels.length;
  return `**出力形式:** 第1行に ${labels.join(' / ')} のいずれか一語`
    + (rest ? 'を書き、続けて結果の要点を短く書いてください。' : 'だけを返してください。');
}

function actionMarkdown(spec, index) {
  const step = spec.steps[index];
  const kind = kindOf(step);
  const heading = `## [${step.id}: ${step.title || kind.label}]`;
  const parts = [heading];
  const preamble = kind.preamble(step);
  if (preamble) parts.push(preamble);
  if (step.detail) parts.push(step.detail);
  if (step.recorded.length && kind.recordedLine) {
    const lines = ['記録した操作（人が 1 回やって通った順。要素は記録どおり role と名前で指し、記録に無い操作を足さない）:'];
    for (const op of step.recorded) {
      const example = op.example ? `（記録時の値の例: ${op.example}）` : '';
      lines.push(`- \`${kind.recordedLine(op)}\`${example}`);
    }
    lines.push('');
    lines.push(kind.recordedHint);
    lines.push('各操作の前に要素が現れるのを待ち、確定の操作（ボタン・リンク・Enter）の後は結果の画面を読み取って確かめてください。'
      + ' `{{key}}` は実行時に人が入れる入力パラメータで、記録時の値の例は形の参考です（既定値にしない）。');
    parts.push(lines.join('\n'));
  }
  const rules = kind.rules.slice();
  if (step.check) rules.push('この工程の完了は検査コマンドで測られます。検査が通る状態になってから出力を返してください。');
  rules.push('秘密情報（パスワード・トークン）を出力に書かない。');
  parts.push(`守ること:\n${rules.map((r) => `- ${r}`).join('\n')}`);
  parts.push('**入力:** {{input}}\n**前のステートの出力:** {{last_output}}');
  parts.push(outputFormatLine(step));
  parts.push(TRAILER);
  return `${parts.join('\n\n')}\n`;
}

// YAML の states / transitions を組む。順序は工程順（YAML は人も読む）。
function buildWorkflow(spec) {
  const preserved = spec.preserved || normalizePreserved(null);
  const states = {};
  for (const step of spec.steps) {
    const kind = kindOf(step);
    const node = { description: step.title || kind.label, action_file: `actions/${step.id}.md` };
    // 第 1 行の契約を書けるのは、行き先が**すべて**結果の名前で決まるときだけ。
    // 文章の条件や無条件が混ざる工程に契約を足すと、名前で始まらない出力が失敗扱いになり、
    // 元の定義では通っていた道が通らなくなる。
    const labels = stepLabels(step);
    const byLabelOnly = !step.outcomes.length || step.outcomes.every((o) => o.when === 'label');
    if (!step.rawTransitions && labels.length && byLabelOnly) {
      node.output_validator = `startswith:${labels.join(',')}`;
      node.max_retries = 1;
    }
    if (step.check) {
      node.check = step.check;
      node.check_retries = step.checkRetries;
    }
    // 画面が持たない項目（output_key / write / max_tool_rounds 等）は原文から引き継ぐ。
    const keep = preserved.files && preserved.files.stateExtras && preserved.files.stateExtras[step.id];
    if (keep && typeof keep === 'object') Object.assign(node, keep);
    states[step.id] = node;
  }
  const transitions = [];
  spec.steps.forEach((step, index) => {
    stepTransitions(spec, index).forEach((t, n) => {
      const item = { from: step.id, to: t.target };
      const rules = [];
      // 検査の関門は決定論の側にだけ足す。文章の条件と混ぜると、実行側は condition_rule を
      // 優先して文章を読まない——「両方を満たしたとき」のつもりが片方だけになる。
      if (t.gated && t.when !== 'text') rules.push('equals:check_ok:true');
      if (t.when === 'label') rules.push(`startswith:last_output:${t.label}`);
      if (t.when === 'rule') rules.push(t.rule);
      if (rules.length) item.condition_rule = rules.join(';');
      if (t.when === 'text') item.condition = t.text;
      item.priority = n + 1;
      transitions.push(item);
    });
  });
  for (const raw of preserved.transitions) transitions.push(raw);

  // 終わり方。**原文にあったものはそのまま書き戻す**（本文を持つ終端もあるので、
  // 説明だけの形に作り直すと中身が消える）。行き先から外れても消さない。
  for (const [id, raw] of Object.entries(preserved.states)) {
    if (!states[id]) states[id] = raw;
  }
  // 原文に無い終わり方は、どこかから行くときだけ作る（誰も行かない終端を足さない）。
  const referenced = new Set(transitions.map((t) => t.to));
  for (const key of ['done', 'abort']) {
    const term = spec.terminals[key];
    if (states[term.id]) continue;
    if (referenced.has(term.id) || (key === 'done' && !spec.steps.some((s) => !s.rawTransitions) && !preserved.transitions.length)) {
      states[term.id] = { description: term.description, terminal: true };
    }
  }

  const config = { on_no_transition: 'error', ...preserved.config };
  config.max_steps = spec.maxSteps;
  const doc = { name: spec.name };
  if (spec.purpose) doc.description = spec.purpose;
  doc.initial_state = spec.steps[0].id;
  if (Object.keys(preserved.context).length) doc.context = preserved.context;
  doc.config = config;
  doc.states = states;
  doc.transitions = transitions;
  return doc;
}

function workflowYaml(spec, workflow) {
  const doc = new YAML.Document(workflow);
  doc.commentBefore = ` ${spec.name} — statemachine-maker が生成した定義。\n`
    + ' 実行: python .github/skills/statemachine-use/scripts/run_machine.py <このファイル> --agent claude\n'
    + ' 検証: 同スクリプトに --dry-run。書式の正典は statemachine-use スキル（references/schema.md）。';
  return doc.toString({ lineWidth: 0 });
}

// 工程列 → 書き出すファイル一式。キーはフォルダからの相対パス（`/` 区切り）。
function compile(spec) {
  const workflow = buildWorkflow(spec);
  const files = { 'workflow.yaml': workflowYaml(spec, workflow) };
  spec.steps.forEach((_step, index) => {
    files[`actions/${spec.steps[index].id}.md`] = actionMarkdown(spec, index);
  });
  files['maker.json'] = `${JSON.stringify(makerSidecar(spec), null, 2)}\n`;
  return { workflow, files };
}

// 画面が残す写し（round-trip を正確にする）。preserved は YAML 側が正なので入れない。
function makerSidecar(spec) {
  return {
    version: PROCEDURE_VERSION,
    tool: 'statemachine-maker',
    name: spec.name,
    purpose: spec.purpose,
    finish: spec.finish,
    notes: spec.notes,
    maxSteps: spec.maxSteps,
    terminals: spec.terminals,
    ends: spec.ends,
    steps: spec.steps,
  };
}

// --- 構造検査（engine.validate_workflow の写し） --------------------------------------

const CHECK_CONTEXT_KEYS = ['check_status', 'check_ok', 'check_output'];
const CHECK_ON_EXHAUSTED = ['escalate', 'continue', 'error'];

function validateWorkflow(workflow, files = {}) {
  const errors = [];
  const states = workflow && workflow.states && typeof workflow.states === 'object' ? workflow.states : {};
  const transitions = Array.isArray(workflow && workflow.transitions) ? workflow.transitions : [];
  if (!workflow || !workflow.initial_state) errors.push('initial_state がありません');
  else if (!states[workflow.initial_state]) errors.push(`initial_state '${workflow.initial_state}' が states に存在しません`);
  for (const t of transitions) {
    if (!t || typeof t !== 'object') { errors.push('トランジションの形が不正です'); continue; }
    if (t.from !== '*' && !states[t.from]) errors.push(`未知のステート '${t.from}' からのトランジション`);
    if (!states[t.to]) errors.push(`未知のステート '${t.to}' へのトランジション`);
  }
  for (const [id, state] of Object.entries(states)) {
    const s = state && typeof state === 'object' ? state : {};
    if (s.terminal) {
      if (s.check) errors.push(`終端ステート '${id}' は check を持てません（アクションが無い）`);
      continue;
    }
    if (!transitions.some((t) => t && (t.from === id || t.from === '*'))) {
      errors.push(`非終端ステート '${id}' に出力トランジションがありません`);
    }
    if (s.action_file && Object.keys(files).length && !files[s.action_file]) {
      errors.push(`ステート '${id}' の action_file が見つかりません: ${s.action_file}`);
    }
    if (typeof s.check === 'string' && SHELL_META_RE.test(s.check)) {
      errors.push(`ステート '${id}' の check にシェル記号があります（argv だけを書く）`);
    }
    if (s.check_on_exhausted != null && !CHECK_ON_EXHAUSTED.includes(String(s.check_on_exhausted))) {
      errors.push(`ステート '${id}' の check_on_exhausted が不正です: '${s.check_on_exhausted}'`);
    }
    if (s.check_retries != null && Number(s.check_retries) < 0) errors.push(`ステート '${id}' の check_retries は 0 以上で指定してください`);
    if (Array.isArray(s.write) && s.write.length > 1) errors.push(`ステート '${id}' の write は 1 ファイルだけです`);
  }
  for (const t of transitions) {
    const rule = String((t && t.condition_rule) || '');
    if (!CHECK_CONTEXT_KEYS.some((key) => rule.includes(key))) continue;
    const sources = t.from === '*' ? Object.entries(states).filter(([, s]) => !(s && s.terminal)) : [[t.from, states[t.from]]];
    for (const [sid, source] of sources) {
      if (source && !source.check) {
        errors.push(`トランジション '${t.from}' → '${t.to}' は検査結果で分岐しますが、ステート '${sid}' に check がありません`);
      }
    }
  }
  return errors;
}

// OS に依らない定義のための注意（エラーにはしない）。
const PORTABILITY_RULES = [
  { re: /^(test|\[|sh|bash|zsh|cmd|cmd\.exe|powershell|pwsh)\b/i, note: 'シェル組み込み・シェル起動はハーネスが拒みます。Python などのスクリプトにまとめてください' },
  { re: /\\/, note: 'パスの区切りは `/` にすると Windows でも他の OS でも通ります' },
  { re: /\.(exe|bat|cmd)\b/i, note: 'Windows 専用の実行ファイル名です。他の OS では動きません' },
  { re: /^python3\b/, note: 'Windows では `python3` が無いことがあります。`python` の方が通りやすい環境もあります' },
];

function portabilityWarnings(spec) {
  const out = [];
  spec.steps.forEach((step, index) => {
    const texts = [['確認コマンド', step.check], ['コマンド', step.kind === 'command' ? step.target : '']];
    for (const [what, value] of texts) {
      if (!value) continue;
      for (const rule of PORTABILITY_RULES) {
        if (rule.re.test(value)) out.push(`工程 ${index + 1} の${what}: ${rule.note}`);
      }
    }
    if (step.kind === 'windows') out.push(`工程 ${index + 1} は Windows アプリの操作なので、実行は Windows 上に限られます`);
    for (const o of step.outcomes) {
      const n = o.to.startsWith('step:') ? Number(o.to.slice(5)) : 0;
      if (n && n <= index + 1) out.push(`工程 ${index + 1} の「${o.label}」は工程 ${n} へ戻ります。無限ループは config.max_steps（${spec.maxSteps}）で止まります`);
    }
  });
  return [...new Set(out)];
}

// --- デコンパイル（定義 → 工程列） -----------------------------------------------------

const FAILURE_RE = /fail|error|abort|reject|escalat|cancel|失敗|中止|差し戻|却下|異常/i;

function classifyTerminal(id, state) {
  return FAILURE_RE.test(`${id} ${(state && state.description) || ''}`) ? 'abort' : 'done';
}

// 本文からこのツールが足した定型を外し、人が書いた「内容」だけにする。
function stripBoilerplate(body, kind) {
  let s = String(body || '').replace(/\r\n?/g, '\n');
  s = s.replace(/^## \[[^\]]*\]\s*\n/, '');
  const cut = s.search(/^\*\*(入力|前のステートの出力|出力形式):\*\*/m);
  if (cut >= 0) s = s.slice(0, cut);
  s = s.replace(/^守ること:\n(?:- .*\n?)+/m, '');
  s = s.replace(/この指示に従ってタスクを実行してください。[\s\S]*$/m, '');
  if (kind && kind.preamble) {
    // 先頭の案内（スキル名指し・対象）はコンパイルで再生成するので落とす。
    s = s.replace(/^`[a-z0-9._-]+` スキル[^\n]*\n(?:対象(?: URL|アプリ): [^\n]*\n)?/, '');
    s = s.replace(/^次のコマンドをそのまま実行します[^\n]*\n\n`[^\n]*`\n/, '');
  }
  const recorded = s.search(/^記録した操作（/m);
  if (recorded >= 0) s = s.slice(0, recorded);
  return s.trim();
}

// 本文から種類と対象（URL / アプリ / スキル名 / argv）を読む。コンパイルが書いた形だけを読み、
// 手書きの本文は AI の処理として扱う（対象は空のまま。人が画面で直す）。
function detectKind(body) {
  const src = String(body || '');
  const cmd = /^次のコマンドをそのまま実行します[^\n]*\n\n`([^\n`]+)`/m.exec(src);
  if (cmd) return { kind: 'command', target: cmd[1] };
  const m = /`([a-z0-9._-]+)` スキル/.exec(src);
  if (!m) return { kind: 'agent', target: '' };
  const url = /^対象 URL: ([^\n]+)$/m.exec(src);
  const app = /^対象アプリ: ([^\n]+)$/m.exec(src);
  if (m[1] === 'playwright-cli') return { kind: 'browser', target: url ? url[1].trim() : '' };
  if (m[1] === 'windows-app-automation') return { kind: 'windows', target: app ? app[1].trim() : '' };
  return { kind: 'skill', target: m[1] };
}

// 条件式を「検査の関門」「結果の名前」「そのまま保つ残り」に分ける。
// 結果の名前として読むのは `startswith:last_output:<語>`（このアプリが書く形）だけ。
// `equals:analysis_result:PASS` のような別の形は、名前に読み替えると書き戻しで別物になる
// ——そういうものは式のまま持って、画面では式として見せる。
function parseRule(rule) {
  const parts = String(rule || '').split(';').map((s) => s.trim()).filter(Boolean);
  const gated = parts.includes('equals:check_ok:true');
  const rest = parts.filter((p) => p !== 'equals:check_ok:true');
  const m = rest.length === 1 ? /^startswith:last_output:(.+)$/.exec(rest[0]) : null;
  return { gated, label: m ? m[1] : '', rest: m ? [] : rest, empty: !parts.length };
}

// 遷移をたどって工程の順を決める（initial から主経路 → 残り）。
function orderStates(workflow) {
  const states = workflow.states || {};
  const transitions = Array.isArray(workflow.transitions) ? workflow.transitions : [];
  const nonTerminal = Object.keys(states).filter((id) => !(states[id] && states[id].terminal));
  const order = [];
  const seen = new Set();
  const byPriority = (a, b) => Number(a.priority || 0) - Number(b.priority || 0);
  let cur = workflow.initial_state;
  while (cur && !seen.has(cur) && nonTerminal.includes(cur)) {
    order.push(cur);
    seen.add(cur);
    const next = transitions.filter((t) => t.from === cur && nonTerminal.includes(t.to) && !seen.has(t.to)).sort(byPriority)[0];
    cur = next ? next.to : '';
  }
  for (const id of nonTerminal) if (!seen.has(id)) { order.push(id); seen.add(id); }
  return order;
}

// workflow.yaml（テキスト）と actions/*.md（相対パス → 本文）から工程列を起こす。
// maker.json があれば工程はそれを正とし、YAML は preserved（画面で表せない部分）の材料にだけ使う。
function decompile({ workflowText, files = {}, makerJson = '' } = {}) {
  const warnings = [];
  const workflow = YAML.parse(String(workflowText || '')) || {};
  const states = workflow.states && typeof workflow.states === 'object' ? workflow.states : {};
  const transitions = Array.isArray(workflow.transitions) ? workflow.transitions : [];

  let sidecar = null;
  if (makerJson) {
    try {
      const parsed = JSON.parse(makerJson);
      const ids = Array.isArray(parsed.steps) ? parsed.steps.map((s) => s.id) : [];
      const inYaml = ids.every((id) => states[id] && !states[id].terminal);
      if (parsed.tool === 'statemachine-maker' && ids.length && inYaml) sidecar = parsed;
      else warnings.push('maker.json が workflow.yaml と合わないので、YAML から読み戻しました');
    } catch {
      warnings.push('maker.json を読めないので、YAML から読み戻しました');
    }
  }

  const terminalIds = Object.keys(states).filter((id) => states[id] && states[id].terminal);
  const terminals = { done: null, abort: null };
  for (const id of terminalIds) {
    const role = classifyTerminal(id, states[id]);
    if (!terminals[role]) terminals[role] = { id, description: text(states[id].description, 120) || (role === 'done' ? '完了' : '失敗として終了') };
  }
  if (!terminals.done) terminals.done = { id: 'complete', description: '完了' };
  if (!terminals.abort) terminals.abort = { id: 'failed', description: '失敗として終了' };
  // 完了・中止のほかの終わり方も、そのまま行き先に選べるようにする。
  const ends = terminalIds
    .filter((id) => id !== terminals.done.id && id !== terminals.abort.id)
    .map((id) => ({ id, description: text(states[id].description, 120) || id }));
  const endIds = new Set(ends.map((e) => e.id));

  const order = sidecar ? sidecar.steps.map((s) => s.id) : orderStates(workflow);
  const managed = new Set(order);
  const indexOf = new Map(order.map((id, i) => [id, i]));
  const stateExtras = {};
  const KNOWN = new Set(['description', 'action_file', 'action', 'output_validator', 'max_retries', 'check', 'check_retries', 'terminal']);
  // 遷移を原文で持つ工程は、出力契約も原文のまま（画面のラベルからは作れない）。
  const KNOWN_RAW = new Set(['description', 'action_file', 'action', 'check', 'check_retries', 'terminal']);

  const steps = order.map((id, index) => {
    const state = states[id] || {};
    const actionFile = state.action_file || `actions/${id}.md`;
    const body = files[actionFile] != null ? String(files[actionFile]) : String(state.action || '');
    const detected = detectKind(body);
    const fromSidecar = sidecar ? sidecar.steps[index] : null;
    const kindId = fromSidecar ? fromSidecar.kind : detected.kind;
    const kind = STEP_KIND_BY_ID.get(kindId) || STEP_KIND_BY_ID.get('agent');
    // 遷移 → 「次にどこへ行くか」。条件式・文章の条件・無条件のどれも画面の行に写す。
    // 原文のまま抱えるのは、画面に置き場が無いものだけ（外部ファイルの条件・知らない行き先）。
    const own = transitions.filter((t) => t.from === id).sort((a, b) => Number(a.priority || 0) - Number(b.priority || 0));
    const outcomes = [];
    let gatedAlways = false;
    let raw = false;
    for (const t of own) {
      const rule = parseRule(t.condition_rule);
      const target = t.to;
      let to;
      if (target === terminals.done.id) to = 'done';
      else if (target === terminals.abort.id) to = 'abort';
      else if (endIds.has(target)) to = `end:${target}`;
      else if (indexOf.has(target)) to = indexOf.get(target) === index + 1 ? 'next' : `step:${indexOf.get(target) + 1}`;
      if (to === 'done' && index + 1 >= order.length) to = 'next';
      if (!to || t.condition_file) { raw = true; break; }
      if (rule.rest.length) { outcomes.push({ when: 'rule', rule: rule.rest.join(';'), to }); continue; }
      if (rule.label) { outcomes.push({ when: 'label', label: rule.label, to }); continue; }
      if (t.condition) { outcomes.push({ when: 'text', text: String(t.condition).trim().replace(/\s+/g, ' '), to }); continue; }
      if (rule.gated && to === 'next') { gatedAlways = true; continue; }
      outcomes.push({ when: 'always', to });
    }
    if (raw && !fromSidecar) {
      warnings.push(`ステート '${id}' には、この画面に置き場の無い遷移（別ファイルの条件・知らない行き先）があるので原文のまま保持します`);
    }
    const isLabel = (o, label, to) => o.when === 'label' && o.label === label && o.to === to;
    const isDefault = outcomes.length === 2 && isLabel(outcomes[0], 'OK', 'next') && isLabel(outcomes[1], 'FAILED', 'abort');
    const isGatedDefault = gatedAlways && !outcomes.length && state.check;
    const stepOutcomes = fromSidecar ? fromSidecar.outcomes : (raw || isDefault || isGatedDefault ? [] : outcomes);

    const isRaw = fromSidecar ? !!fromSidecar.rawTransitions : raw;
    // 第 1 行の契約を書き直さない工程では、元の宣言をそのまま持ち回る（消さない）。
    const byLabelOnly = !stepOutcomes.length || stepOutcomes.every((o) => o.when === 'label');
    const extras = {};
    const known = isRaw || !byLabelOnly ? KNOWN_RAW : KNOWN;
    for (const [k, v] of Object.entries(state)) if (!known.has(k)) extras[k] = v;
    if (Object.keys(extras).length) stateExtras[id] = extras;

    const step = fromSidecar ? { ...fromSidecar } : {
      id, kind: kind.id,
      title: [id, kind.label].includes(text(state.description, 80)) ? '' : text(state.description, 80),
      detail: stripBoilerplate(body, kind) || (kind.detail.required ? '（内容を入力してください）' : ''),
      target: detected.target || '',
      check: typeof state.check === 'string' ? state.check : (state.check && state.check.command
        ? [state.check.command, ...(state.check.args || [])].join(' ') : (Array.isArray(state.check) ? state.check.join(' ') : '')),
      checkRetries: state.check_retries == null ? 1 : Number(state.check_retries),
      outcomes: stepOutcomes,
      rawTransitions: raw,
      recorded: [],
    };
    step.id = id;
    if (fromSidecar && state.check && !step.check && typeof state.check === 'string') step.check = state.check;
    return step;
  });

  const unmanagedStates = {};
  for (const [id, st] of Object.entries(states)) {
    // 終わり方は原文のまま持ち回る（本文を持つ終端を、説明だけの形に作り直さない）。
    if (managed.has(id)) continue;
    unmanagedStates[id] = st;
    // 終わり方は行き先として選べるので知らせない。作業のあるステートだけ知らせる。
    if (!(st && st.terminal)) warnings.push(`ステート '${id}' は画面で編集できないので原文のまま保持します`);
  }
  const rawIds = new Set(steps.filter((s) => s.rawTransitions).map((s) => s.id));
  const unmanagedTransitions = transitions.filter((t) => !managed.has(t.from) || rawIds.has(t.from));

  const preserved = {
    context: workflow.context && typeof workflow.context === 'object' ? workflow.context : {},
    config: workflow.config && typeof workflow.config === 'object' ? workflow.config : {},
    states: unmanagedStates,
    transitions: unmanagedTransitions,
    files: { stateExtras },
  };

  const raw = {
    version: PROCEDURE_VERSION,
    name: sidecar ? sidecar.name : text(workflow.name, 120),
    purpose: sidecar ? sidecar.purpose : text(workflow.description, 2000),
    finish: sidecar ? sidecar.finish : '',
    notes: sidecar ? sidecar.notes : '',
    maxSteps: Number((workflow.config && workflow.config.max_steps) || (sidecar && sidecar.maxSteps) || 30),
    terminals,
    ends,
    steps,
    preserved,
  };
  return { raw, warnings, workflow };
}

module.exports = {
  PROCEDURE_VERSION,
  MAX_STEPS,
  MAX_OUTCOMES,
  STEP_KINDS,
  OUTCOME_TARGETS,
  TRAILER,
  catalog,
  normalizeProcedure,
  parameterKeys,
  stepLabels,
  stepTransitions,
  describeTarget,
  actionMarkdown,
  buildWorkflow,
  compile,
  validateWorkflow,
  portabilityWarnings,
  decompile,
  stripBoilerplate,
  detectKind,
};
