'use strict';

// ワークフローを AI と**tmux の会話で**作る・変えるための本文。タスク（`automation/teaching.js`）の
// 対になる。違うのは書かせる先だけで、進め方（目的を聞く → 定義を書く → 短く報告）は同じ。
//
//   下書き   … `.agents/workflows/.teaching/<保存名>.json`（会話 ID と、まだ利用可能でない印）
//   定義     … `.agents/workflows/<保存名>.json`。AI はこの会話の中でこのファイルを直接書く
//   試運転   … 画面の「試運転する」。実行は利用者が押す（AI は実行しない）

const flowModel = require('./flow-model');

function text(value, max = 6000) { return String(value || '').trim().slice(0, max); }

function kindLines() {
  return flowModel.KIND_INFOS.map((info) => `   - \`${info.kind}\`（${info.label}）… ${info.description}`);
}

// 会話の最初に送る本文。
//   id       … 保存名（.agents/workflows/<id>.json）
//   purpose  … 利用者が書いた目的（新規のとき）
//   existing … 既にある定義を変える会話か
function prompt({ id, purpose = '', existing = false } = {}) {
  const name = String(id || '').trim();
  const file = `.agents/workflows/${name}.json`;
  return [
    `あなたはこのリポジトリで「ワークフロー」（agent-flow が実行する工程の DAG）を${existing ? '変更する' : '作る'}担当です。`,
    `保存先は \`${file}\` の 1 ファイルで、この中だけを書き換えてください。`,
    '',
    '進め方:',
    existing
      ? `1. まず \`${file}\` を読み、いまの工程を短く要約してから、変更したい点を利用者に聞いてください。`
      : '1. 利用者の目的を読み、曖昧な点（何を受け取り何を返すか・途中で人の確認が要るか・並列にする単位）だけを質問してください。分かることは聞かずに進めます。',
    `2. 定義は JSON で、次の形です（保存名 id は \`${name}\` 固定）:`,
    '   { "version": 2, "id": "…", "name": "画面に出す名前", "description": "1 行の説明",',
    '     "nodes": [ { "id": "…", "label": "…", "kind": "…", "goal": "…", "deps": ["前の工程の id"] } ],',
    '     "rework": [ { "id": "…", "from": "…", "to": "…", "trigger": "human-rejected | verification-failed",',
    '                   "instruction": "やり直すときの指示", "maxIterations": 2, "onExhausted": "human | fail | continue" } ] }',
    '3. 工程の種類（kind）は次から選びます:',
    ...kindLines(),
    '4. `goal` には、その工程が何を終わらせるかを 1〜2 文で書きます。利用者が実行時に入れる依頼は `{{request}}`、',
    '   それ以外の毎回変わる値は `{{key}}` で受けます。`deps` は前の工程の id で、循環させないでください。',
    '5. 人に確認する工程（`kind: "human"`）には `interaction` が要ります（`mode` は approval / choice / input、`prompt` は質問文、',
    '   choice のときは `options` を 2 つ以上）。失敗したときに前へ戻す線は `rework` に書きます（差し戻し元は human か verify）。',
    '6. 書いたら内容を読み直し、工程の並び・並列にした単位・人の確認の位置を短く報告してください。実行はしません',
    '   （試運転は利用者が画面の「試運転する」で行います）。',
    '',
    ...(existing ? [] : ['利用者の目的:', text(purpose) || '（未記入。まず何をさせたいかを聞いてください）']),
  ].join('\n');
}

// 下書きを開き直したとき・公開済みのワークフローを編集するときに送る本文。
function resumePrompt({ id, purpose = '', existing = false, context = '' } = {}) {
  const name = String(id || '').trim();
  const file = `.agents/workflows/${name}.json`;
  return [
    existing ? 'このワークフローの編集を開始します。' : 'このワークフローの下書き作成を再開します。',
    `対象は \`${file}\` です。まず内容を読み直し、現在の工程を会話の前提として引き継いでください。`,
    purpose ? `ワークフローの目的: ${text(purpose)}` : '',
    text(context, 1000) ? `今回の編集対象: ${text(context, 1000)}` : '',
    existing
      ? '現在の工程を短く要約し、今回変更したい内容を利用者に確認してください。まだファイルは変更しないでください。'
      : 'これまでの会話と保存済みの内容を踏まえ、未確定の点だけを質問して作成を続けてください。',
  ].filter(Boolean).join('\n');
}

// 目的の 1 行目から保存名を作る（英数字だけ。flow-model の ID_RE に通る形にする）。
function workflowIdFor(purpose) {
  const ascii = String(purpose || '').split(/\r?\n/)[0].toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
  const trimmed = ascii.replace(/^[^a-z0-9]+/, '');
  return trimmed.length >= 3 ? trimmed : `flow-${Math.random().toString(36).slice(2, 10)}`;
}

module.exports = { prompt, resumePrompt, workflowIdFor };
