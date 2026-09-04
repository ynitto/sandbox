'use strict';

// 定型手順（Routine Procedure）— 社内システムやローカルルールに特化した定型業務を、
// 画面操作（ブラウザ / Windows アプリ）・スキルへの移譲・コマンド実行・AI の処理（生成・判断）
// の工程列として組み立て、statemachine-use スキルの**作成モードへ渡す指示文**へ決定的に変換する。
//
// 担当は 4 つ。どれも新しい実行系・状態ストアを作らない。
//   1. 種別カタログ（STEP_KINDS / catalog）… 工程の種類の正典。表示名・入力欄・移譲先スキル・
//      指示文の案内・道具の診断をここ 1 か所で宣言し、renderer は IPC で受け取って描く。
//      種類を足すときはこの配列へ 1 項目足すだけで、画面・指示文・診断がそろって増える。
//   2. 正規化（normalizeProcedure）… renderer から届いた工程列を信頼せず、種類・分岐先・
//      検査コマンドの形をここで検査する（画面が拾えなかった不備を main が最後に断る）。
//   3. 指示文（procedureInstruction）… スキルの分解原則（1 ステート 1 工程・成功は機械が
//      測れる形・分岐は遷移に書く・移譲先スキルを名指しする）に沿う Markdown を組む。
//      **YAML は書かない。** `.statemachine/<machine>/workflow.yaml` の書式の正典は
//      statemachine-use スキルで、書式をここに写すと 2 実装になる（C7）。工程列（正規形）は
//      実行系に依らない形にしてあり、指示文の組み立てだけが statemachine-use 向けである。
//   4. 道具の確認（toolStatus）… 工程が頼る CLI がこの端末から呼べるかを、LLM を使わない
//      診断コマンドだけで確かめる。
//
// 工程が頼る道具はどれも**エージェント CLI のシェルから呼べる CLI**である。ヘッドレスの
// ハーネス（agent-loop statemachine）はシェルを介さず argv を直接実行するので、
// 指示文でもパイプ・リダイレクトを使わない形に倒す（コマンドと検査コマンドはここで断る）。
//
// このモジュールが依存するのは template-parameters（`{{key}}` の 1 実装）だけで、
// cowork.js・Electron・ファイルシステムには触れない（単体で検査できる）。

const templateParameters = require('../../../base/main/template-parameters');
const recording = require('./recording');

// 工程列（正規形）の版。項目に保存した工程列を後から読むときの目印で、形を変えるときに上げる。
//   1 … 初版
//   2 … 工程に `recorded[]`（人の操作の記録。recording.js が起こす）を任意で持てる
const PROCEDURE_VERSION = 2;

// 上限。画面が組める量であると同時に、作成モードが 1 セッションで読み切れる量。
const MAX_STEPS = 30;
const MAX_OUTCOMES = 8;
const MAX_TEXT = 4000;
const MAX_RECORDED = 40;

// statemachine-use の `check` はシェルを介さない。含まれていたら投入前に断る
// （黙って別物を実行させない——検知装置が別物を測るのは検知が無いより悪い）。
const SHELL_META_RE = /[|&;<>`\n]|\$\(/;

// スキル名。`.github/skills/<name>/` のフォルダ名と同じ字種。
const SKILL_NAME_RE = /^[a-z0-9][a-z0-9._-]*$/;

// 道具の診断。`probe` は出力の読み方（version = 1 行目を版として示す / doctor = JSON の checks を読む）。
const TOOL_PLAYWRIGHT = {
  id: 'playwright-cli',
  label: 'ブラウザ操作（playwright-cli）',
  command: 'playwright-cli',
  args: ['--version'],
  probe: 'version',
  hint: 'install.py が @playwright/cli を入れます（npm が必要）。',
};
const TOOL_WINAUTO = {
  id: 'winauto',
  label: 'Windows アプリ操作（winauto）',
  command: 'winauto',
  args: ['doctor', '--output', 'json'],
  probe: 'doctor',
  hint: 'tools/winauto/install.py を実行し、winauto doctor で橋渡しを確認します。',
};

// 工程の種類の正典。
//   label / description / target / detail / check … renderer が入力欄を描く材料（catalog() で渡す）
//   skill / skillFromTarget                        … 指示文が名指しする移譲先
//   guidance                                       … 指示文「使う道具」節に載せる案内（使う種類のぶんだけ）
//   tool                                           … 道具の診断（無ければ診断しない）
const STEP_KINDS = [
  {
    id: 'browser',
    label: '画面操作（ブラウザ）',
    description: 'Web 画面を開いて操作し、表示内容を読み取る',
    skill: 'playwright-cli',
    target: { label: 'URL', placeholder: 'https://…', required: false },
    detail: { label: '内容', required: true,
      placeholder: '例: ログイン後に「申請一覧」を開き、今日の日付の行を読み取る' },
    check: { placeholder: '例: python3 scripts/check_list.py' },
    guidance: '- ブラウザの操作は `playwright-cli` スキル（`playwright-cli` コマンド）で行う。'
      + ' 操作の前に snapshot で要素を確かめ、操作の後も snapshot か画面のテキストで結果を読み取る。'
      + ' セレクタや URL は工程に書いたものだけを使い、想定と違う画面が出たら FAILED を返す。',
    tool: TOOL_PLAYWRIGHT,
    // 人の操作の記録を持てる。記録の要素は Playwright のロケータ式（`getByRole(...)`）。
    recordable: true,
    recordedLine: (op) => {
      const value = op.value ? ` ${JSON.stringify(op.value)}` : '';
      return op.op === 'goto' ? `goto ${op.target}` : `${op.op} ${op.target}${value}`;
    },
  },
  {
    id: 'windows',
    label: '画面操作（Windows アプリ）',
    description: 'Windows のデスクトップアプリを操作し、表示内容を読み取る',
    skill: 'windows-app-automation',
    target: { label: 'アプリ', placeholder: '例: 勤怠管理', required: false },
    detail: { label: '内容', required: true,
      placeholder: '例: メニュー「集計」→「月次」を開き、対象月 {{month}} を入力して「出力」を押す' },
    check: { placeholder: '例: winauto wait name:=完了 --app 勤怠管理' },
    guidance: '- Windows アプリの操作は `windows-app-automation` スキル（`winauto` コマンド）で行う。'
      + ' `winauto tree` / `winauto wait` で要素を確かめてから `click` / `type` / `keys` を送り、'
      + ' 結果は `get-text` で読み取る。セレクタは `auto_id:=` を優先し、無ければ `name:=` を使う。',
    tool: TOOL_WINAUTO,
    // 人の操作の記録を持てる。記録の要素は winauto のセレクタ（`auto_id:=` / `name:=`）。
    recordable: true,
    recordedLine: (op) => {
      if (op.op === 'launch') return `winauto launch ${JSON.stringify(op.target)}`;
      if (op.op === 'window') return `winauto wait ${JSON.stringify(`name:=${op.target}`)}`;
      const verb = { click: 'click', dblclick: 'click', fill: 'type', type: 'type', keys: 'keys', press: 'keys', select: 'type', check: 'click', uncheck: 'click' }[op.op] || op.op;
      const value = op.value ? ` ${JSON.stringify(op.value)}` : '';
      return `winauto ${verb} ${JSON.stringify(op.target)}${value}`;
    },
  },
  {
    id: 'skill',
    label: 'スキルに任せる',
    description: '社内システム向けのスキル（redmine-use、outlook-use など）に工程を任せる',
    skill: '',
    skillFromTarget: true,
    target: { label: 'スキル名', placeholder: '例: redmine-use', required: true, pattern: 'skill' },
    detail: { label: '内容', required: true,
      placeholder: '例: 対象月のチケット一覧を取得し、未完了のものを表にする' },
    check: { placeholder: '例: test -s out/tickets.md' },
    guidance: '- スキルに任せる工程は、名指ししたスキルの SKILL.md に書かれた使い方とスクリプトだけを使う。'
      + ' スキルが見つからなければ FAILED を返し、代わりの手段を探さない。',
    tool: null,
  },
  {
    id: 'command',
    label: 'コマンド実行',
    description: '決まったコマンドを実行する（シェルは介さない）',
    skill: '',
    target: { label: 'コマンド', placeholder: '例: python3 scripts/export.py --month {{month}}',
      required: true, pattern: 'argv' },
    detail: { label: '補足', required: false, placeholder: '補足（任意）' },
    check: { placeholder: '例: test -s out/report.csv' },
    guidance: '- コマンドは書いてある argv をそのまま実行する。シェル（パイプ・リダイレクト）は介さない。',
    tool: null,
  },
  {
    id: 'agent',
    label: 'AI の処理（生成・判断）',
    description: '前の工程の結果を材料に、文章を作る・分類する・次へ進むか判断する',
    skill: '',
    target: null,
    detail: { label: '内容', required: true,
      placeholder: '例: 読み取った申請内容を要約し、差し戻しが必要か判断する' },
    check: null,
    guidance: '- AI の処理の工程は、前の工程の出力（{{last_output}} / output_key）だけを材料にし、画面やファイルを勝手に操作しない。',
    tool: null,
  },
];
const STEP_KIND_IDS = STEP_KINDS.map((k) => k.id);
const STEP_KIND_BY_ID = new Map(STEP_KINDS.map((k) => [k.id, k]));

// renderer へ渡す種別カタログ。入力欄を描くのに要る項目だけで、移譲先・案内・診断は含めない
// （画面が知る必要が無く、知らせると画面側に写しが生まれる）。
function catalog() {
  return STEP_KINDS.map((k) => ({
    id: k.id,
    label: k.label,
    description: k.description,
    target: k.target ? { label: k.target.label, placeholder: k.target.placeholder, required: !!k.target.required } : null,
    detail: { label: k.detail.label, placeholder: k.detail.placeholder, required: !!k.detail.required },
    check: k.check ? { placeholder: k.check.placeholder } : null,
    recordable: !!k.recordable,
  }));
}

// 判断の行き先。`step:<n>` は 1 始まりの工程番号。
const OUTCOME_TARGETS = ['next', 'done', 'abort'];

// 実行時に注入される変数は入力にしない（正典は template-parameters）。
function parameterKeys(spec) {
  const texts = [spec.purpose, spec.finish, spec.notes];
  for (const step of spec.steps) {
    texts.push(step.title, step.detail, step.target, step.check);
    for (const op of step.recorded || []) texts.push(op.value);
  }
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

function rejectShellMeta(value, what, stepIndex) {
  if (SHELL_META_RE.test(value)) {
    throw new Error(`工程 ${stepIndex + 1} の${what}にシェル記号は使えません`
      + '（パイプやリダイレクトが要るならスクリプトにまとめてください）');
  }
}

function normalizeTarget(kind, raw, index) {
  if (!kind.target) return '';
  const value = text(raw, 500);
  if (!value) {
    if (kind.target.required) {
      throw new Error(`工程 ${index + 1}（${kind.label}）に${kind.target.label}を入力してください`);
    }
    return '';
  }
  if (kind.target.pattern === 'argv') rejectShellMeta(value, 'コマンド', index);
  if (kind.target.pattern === 'skill' && !SKILL_NAME_RE.test(value)) {
    throw new Error(`工程 ${index + 1} のスキル名が不正です: ${value}（英小文字・数字・ハイフン）`);
  }
  return value;
}

// 人の操作の記録（recording.js の正規形）。画面操作の工程だけが持ち、要素は role と名前で
// 持つ（ref や座標は受けない）。値の `{{key}}` は入力パラメータとして拾う。
const RECORDED_OP_SET = new Set(recording.OPS);
function normalizeRecorded(raw, index, kind) {
  const list = Array.isArray(raw) ? raw : [];
  if (!list.length) return [];
  if (!kind.recordable) throw new Error(`工程 ${index + 1}（${kind.label}）は操作の記録を持てません`);
  if (list.length > MAX_RECORDED) throw new Error(`工程 ${index + 1} の記録した操作は ${MAX_RECORDED} 件までです`);
  return list.map((op, n) => {
    const item = op && typeof op === 'object' ? op : {};
    const name = String(item.op || '');
    if (!RECORDED_OP_SET.has(name)) throw new Error(`工程 ${index + 1} の記録 ${n + 1} の操作が不正です: ${name}`);
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

function normalizeStep(raw, index, stepCount) {
  const item = raw && typeof raw === 'object' ? raw : {};
  const kind = STEP_KIND_BY_ID.get(String(item.kind || ''));
  if (!kind) throw new Error(`工程 ${index + 1} の種類が不正です`);
  const detail = text(item.detail);
  if (kind.detail.required && !detail) {
    throw new Error(`工程 ${index + 1}（${kind.label}）の${kind.detail.label}を入力してください`);
  }
  const check = kind.check ? text(item.check, 500) : '';
  if (check) rejectShellMeta(check, '確認コマンド', index);
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
    kind: kind.id,
    title: text(item.title, 80),
    detail,
    target: normalizeTarget(kind, item.target, index),
    check,
    outcomes,
    recorded: normalizeRecorded(item.recorded, index, kind),
  };
}

// renderer から届いた手順を検査して正規形にする。落ちるときは人が直せる文言で落とす。
function normalizeProcedure(raw) {
  const src = raw && typeof raw === 'object' ? raw : {};
  const version = src.version == null ? PROCEDURE_VERSION : Number(src.version);
  if (!(version >= 1 && version <= PROCEDURE_VERSION)) {
    throw new Error(`この工程列は対応していない版です（version ${src.version}）`);
  }
  const steps = Array.isArray(src.steps) ? src.steps : [];
  if (!steps.length) throw new Error('工程を 1 つ以上追加してください');
  if (steps.length > MAX_STEPS) throw new Error(`工程は ${MAX_STEPS} 件までです`);
  const spec = {
    version: PROCEDURE_VERSION,
    purpose: text(src.purpose, 2000),
    steps: steps.map((step, index) => normalizeStep(step, index, steps.length)),
    finish: text(src.finish, 2000),
    notes: text(src.notes, 2000),
  };
  if (!spec.purpose) throw new Error('この業務の目的を入力してください');
  spec.parameters = parameterKeys(spec);
  return spec;
}

// --- 指示文（statemachine-use の作成モード向け） ----------------------------------

// 工程が名指しする移譲先スキル。種類に固定のもの（ブラウザ / Windows）と、工程が名前で
// 指定するもの（スキルに任せる）がある。
function skillFor(step) {
  const kind = STEP_KIND_BY_ID.get(step.kind);
  if (!kind) return '';
  return kind.skill || (kind.skillFromTarget ? step.target : '');
}

// 記録を持つ工程があるときだけ載せる案内。記録は「うまく行った 1 回」なので、再現に要る待機・
// 確認・想定外への対処を作成モードの AI に補わせ、操作そのものは足させない（汎用化の境界）。
const RECORDED_GUIDANCE = [
  '- 「記録した操作」は人が 1 回やって通った操作の列である。同じ順・同じ要素で再現し、記録に無い操作を足さない。',
  '- 記録の要素は role と名前（`getByRole` / `auto_id:=` / `name:=`）で指す。snapshot の ref（e15 など）や座標を定義に書かない。'
    + ' ブラウザの記録の行は `playwright-cli <操作> "<ロケータ式>" [値]` の形でそのまま再現できる（ロケータ式は引用符で囲んで渡す）。',
  '- 各操作の前に要素が現れるのを待ち（snapshot / `winauto wait`）、確定の操作（ボタン・リンク・Enter）の後は結果の画面を読み取って確かめる。',
  '- 記録の `{{key}}` は実行時に人が入れる入力パラメータで、「記録時の値の例」は形の参考に過ぎない。例を既定値にしない。',
  '- 記録の名前が選択肢の文字列を含むなど脆い（例: `getByLabel(\'種別 通常緊急\')`）なら、同じ要素を指す role と名前へ言い換えてよい。',
];

const COMMON_GUIDANCE = [
  '- 画面操作・スキル・コマンドの工程は、結果を機械で確かめられるなら `check` を宣言する（宣言した工程からは `equals:check_ok:true` で進む）。',
  '- どの工程でも、秘密情報（パスワード・トークン）をアクション本文に書かない。必要なら入力パラメータにする。',
];

function toolGuidance(steps) {
  const used = new Set(steps.map((s) => s.kind));
  const lines = STEP_KINDS.filter((k) => used.has(k.id) && k.guidance).map((k) => k.guidance);
  const recorded = steps.some((s) => s.recorded && s.recorded.length) ? RECORDED_GUIDANCE : [];
  return [...lines, ...recorded, ...COMMON_GUIDANCE];
}

function outcomeTargetLabel(to, index, stepCount) {
  if (to === 'next') return index + 1 < stepCount ? `工程 ${index + 2} へ進む` : '完了として終了する';
  if (to === 'done') return '完了として終了する';
  if (to === 'abort') return '失敗として終了する';
  const n = Number(String(to).slice('step:'.length));
  return n === index + 1 ? `工程 ${n}（この工程）をやり直す` : `工程 ${n} へ進む`;
}

function stepSection(step, index, stepCount) {
  const kind = STEP_KIND_BY_ID.get(step.kind);
  // 名前を省いた工程は種類だけで見出しにする（「画面操作 2（画面操作）」の重複を作らない）。
  const lines = [step.title
    ? `### 工程 ${index + 1}: ${step.title}（${kind.label}）`
    : `### 工程 ${index + 1}: ${kind.label}`];
  if (kind.target && kind.target.pattern === 'argv') {
    lines.push(`- 実行するコマンド（argv）: \`${step.target}\``);
  } else if (step.target && !kind.skillFromTarget) {
    lines.push(`- 対象（${kind.target.label}）: ${step.target}`);
  }
  if (step.detail) {
    lines.push(`- ${kind.detail.label}:\n  ${step.detail.replace(/\n/g, '\n  ')}`);
  }
  if (step.recorded.length && kind.recordedLine) {
    lines.push('- 記録した操作（人がやった順。要素は記録どおり role と名前で指す）:');
    for (const op of step.recorded) {
      const example = op.example ? ` （記録時の値の例: ${op.example}）` : '';
      lines.push(`  - \`${kind.recordedLine(op)}\`${example}`);
    }
  }
  const skill = skillFor(step);
  if (skill) lines.push(`- アクション本文で \`${skill}\` スキルへ移譲すると明記する。`);
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
  const stepCount = spec.steps.length;
  const parts = [];
  parts.push(`## 目的\n${spec.purpose}`);
  parts.push(`## 使う道具\n${toolGuidance(spec.steps).join('\n')}`);
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

// kinds が頼る道具を（同じ道具は 1 回だけ）並べる。kinds が空なら診断できる道具すべて。
function toolsFor(kinds) {
  const wanted = new Set(Array.isArray(kinds) ? kinds : []);
  const out = [];
  const seen = new Set();
  for (const kind of STEP_KINDS) {
    if (!kind.tool || (wanted.size && !wanted.has(kind.id)) || seen.has(kind.tool.id)) continue;
    seen.add(kind.tool.id);
    out.push(kind.tool);
  }
  return out;
}

// `capture(command, args, { cwd, timeoutMs })` は loopProvider.runCommandCapture と同じ形
// （テストでは差し替える）。LLM を使わない診断コマンドだけを呼ぶ（`doctor` / `--version` は
// 読み取り専用で、winauto のデスクトップロックも取らない）。
async function toolStatus({ cwd = '', kinds = [], capture, timeoutMs = 20000 } = {}) {
  if (typeof capture !== 'function') throw new Error('道具の確認に使う実行関数がありません');
  const out = [];
  for (const tool of toolsFor(kinds)) {
    let res;
    try {
      res = await capture(tool.command, tool.args, { cwd, timeoutMs });
    } catch (err) {
      res = { ok: false, status: -1, stdout: '', stderr: '', error: String((err && err.message) || err) };
    }
    let verdict;
    if (!res || res.status === -1 || res.error) {
      verdict = { ok: false, summary: `コマンドを起動できません: ${(res && res.error) || tool.command}` };
    } else if (tool.probe === 'doctor') {
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
  PROCEDURE_VERSION,
  MAX_RECORDED,
  STEP_KINDS,
  STEP_KIND_IDS,
  OUTCOME_TARGETS,
  MAX_STEPS,
  MAX_OUTCOMES,
  catalog,
  normalizeProcedure,
  procedureInstruction,
  parameterKeys,
  outcomeLabel,
  skillFor,
  toolsFor,
  toolStatus,
};
