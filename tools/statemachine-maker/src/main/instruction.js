'use strict';

// statemachine-use スキルの**作成モードへ渡す指示文**。画面が組んだ定義を AI に補完させたい
// とき（待機・読み取り・想定外の画面の扱いを足す、文面を整える）に使う。文面は agent-dashboard の
// 手順ビルダーと同じ分解原則（1 ステート 1 工程・出力契約・分岐は遷移・移譲先スキルの名指し・check）。
// YAML はここでは書かない——このツールのコンパイラが既に書いており、AI には**既存の定義を
// 読んで直す**ことを頼む。

const model = require('./model');

const KIND_GUIDANCE = {
  browser: '- ブラウザの操作は `playwright-cli` スキル（`playwright-cli` コマンド）で行う。操作の前に snapshot で要素を確かめ、操作の後も snapshot か画面のテキストで結果を読み取る。',
  windows: '- Windows アプリの操作は `windows-app-automation` スキル（`winauto` コマンド）で行う。`winauto tree` / `winauto wait` で要素を確かめてから `click` / `type` / `select` を送り、結果は `get-text` で読み取る。',
  skill: '- スキルに任せる工程は、名指ししたスキルの SKILL.md に書かれた使い方とスクリプトだけを使う。',
  command: '- コマンドは書いてある argv をそのまま実行する。シェル（パイプ・リダイレクト）は介さない。',
  agent: '- AI の処理の工程は、前の工程の出力（{{last_output}}）だけを材料にし、画面やファイルを勝手に操作しない。',
};

const COMMON_GUIDANCE = [
  '- 画面操作・スキル・コマンドの工程は、結果を機械で確かめられるなら `check` を宣言する（宣言した工程からは `equals:check_ok:true` で進む）。',
  '- どの工程でも、秘密情報（パスワード・トークン）をアクション本文に書かない。必要なら入力パラメータにする。',
  '- 「記録した操作」は人が 1 回やって通った操作の列である。同じ順・同じ要素で再現し、記録に無い操作を足さない。ref（e15 など）や座標を定義に書かない。',
];

function kindLabel(id) {
  const kind = model.STEP_KINDS.find((k) => k.id === id);
  return kind ? kind.label : id;
}

function stepSection(spec, index) {
  const step = spec.steps[index];
  const lines = [`### 工程 ${index + 1}（${step.id}）: ${step.title || kindLabel(step.kind)}（${kindLabel(step.kind)}）`];
  if (step.kind === 'command') lines.push(`- 実行するコマンド（argv）: \`${step.target}\``);
  else if (step.kind === 'skill') lines.push(`- アクション本文で \`${step.target}\` スキルへ移譲すると明記する。`);
  else if (step.target) lines.push(`- 対象: ${step.target}`);
  if (step.detail) lines.push(`- 内容:\n  ${step.detail.replace(/\n/g, '\n  ')}`);
  if (step.recorded.length) {
    const kind = model.STEP_KINDS.find((k) => k.id === step.kind);
    lines.push('- 記録した操作（人がやった順。要素は記録どおり role と名前で指す）:');
    for (const op of step.recorded) {
      const example = op.example ? ` （記録時の値の例: ${op.example}）` : '';
      lines.push(`  - \`${kind && kind.recordedLine ? kind.recordedLine(op) : `${op.op} ${op.target}`}\`${example}`);
    }
  }
  if (step.check) lines.push(`- 完了の確認: \`check: ${step.check}\`（check_retries: ${step.checkRetries}）。通ったときだけ次へ進む。`);
  const transitions = model.stepTransitions(spec, index);
  lines.push(`- 出力契約: 第 1 行に ${model.stepLabels(step).join(' / ')} のいずれかだけを書く。`);
  lines.push('- 遷移（分岐はアクション本文ではなく transitions に書く）:');
  for (const t of transitions) {
    lines.push(`  - ${t.label || '検査が通る'} → ${model.describeTarget(spec, index, t.to)}（${t.target}）`);
  }
  return lines.join('\n');
}

// 作成モードへ渡す指示文（Markdown）。
function creationInstruction(spec, { machineDir = '' } = {}) {
  const used = new Set(spec.steps.map((s) => s.kind));
  const guidance = [...model.STEP_KINDS.filter((k) => used.has(k.id)).map((k) => KIND_GUIDANCE[k.id]), ...COMMON_GUIDANCE];
  const parts = [];
  parts.push(`## 目的\n${spec.purpose || spec.name}`);
  if (machineDir) {
    parts.push(`## 既存の定義\n\`${machineDir}\` に statemachine-maker が書いた workflow.yaml と actions/*.md がある。`
      + 'ステートの列・出力契約・遷移はそのまま保ち、アクション本文の**待機・読み取り・想定外の画面での FAILED・check の宣言**を補ってください。'
      + 'ステートを増減する場合は、その理由を最後に報告してください。');
  }
  parts.push(`## 使う道具\n${guidance.join('\n')}`);
  parts.push(`## 工程（この順に 1 ステート 1 工程）\n\n${spec.steps.map((_s, i) => stepSection(spec, i)).join('\n\n')}`);
  if (spec.parameters.length) {
    parts.push('## 入力パラメータ\n実行時に人が値を入れる。アクション本文では `{{key}}` で参照し、値の既定は書かない。\n'
      + spec.parameters.map((key) => `- {{${key}}}`).join('\n'));
  }
  parts.push(`## 終了条件\n${spec.finish || '最後の工程が OK で終わったら完了。途中で FAILED になったら失敗として止める。'}`);
  const rules = [
    '- 工程の順と内容を勝手に足したり減らしたりしない。判断の分岐は遷移に書き、アクション本文には書かない。',
    `- 同じ工程へ戻る遷移があるなら、回数の上限を context のカウンタと config.max_steps（${spec.maxSteps}）で置く。`,
    '- 画面操作の途中で想定と違う画面・要素が出たら、別の操作を試さずに FAILED を返す。',
    '- 定義は OS に依らない形にする（パスは `/` 区切り、シェル組み込みや `.exe` を check に書かない）。',
    '- 作成後に `python .github/skills/statemachine-use/scripts/run_machine.py <workflow> --dry-run` で検証し、通らない定義は残さない。',
  ];
  if (spec.notes) rules.push(`- 注意事項: ${spec.notes.replace(/\n/g, '\n  ')}`);
  parts.push(`## 守ること\n${rules.join('\n')}`);
  return parts.join('\n\n');
}

function creationPrompt(spec, { machineDir = '', machine = '' } = {}) {
  const target = machine || spec.machine || 'routine';
  return `statemachine-use スキルの作成モードで、次の指示から「${spec.name}」ステートマシンを${machineDir ? '整えて' : '作成して'}ください。\n`
    + `生成先は .statemachine/${target}/ とし、作成だけを行って実行はしないでください。\n\n`
    + `指示:\n${creationInstruction(spec, { machineDir })}`;
}

module.exports = { creationInstruction, creationPrompt };
