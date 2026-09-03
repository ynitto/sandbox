'use strict';

// 文書機能がエージェント CLI へ渡す依頼文。すべて決定的（LLM を使わず組み立てる）。
//
// 2 系統ある:
//   対話ウィンドウ向け（createPrompt / resumePrompt / verifyPrompt）
//     … 文書フォルダを cwd にした書き込み可の CLI セッションへ送る。徹底的な質問→作成、
//        検証→修正、いずれも人との対話が本体なので、外部ターミナルで進める。
//   ヘッドレス向け（ruleDraftPrompt / ruleFromHistoryPrompt / feedbackRulePrompt）
//     … 読み取り専用の助言。返るのは文書ルールの本文だけで、ファイルへ書くのは人が
//        編集して確定したあとの dashboard（C4: AI 下書き＋人の確定）。

const { RULE_SECTIONS } = require('./rules');
const formats = require('./formats');
const sidecar = require('./sidecar');

// 質問で必ず埋める観点。「徹底的に質問する」の中身をここで固定する。
const QUESTION_AREAS = [
  '読者（誰が・どの立場で・どれくらいの前提知識で読むか）',
  '目的と、読み終えた読者に取ってほしい行動・判断',
  '範囲（含めるもの・含めないもの・前提として扱うもの）',
  '用語（正式名称・略語・表記の統一。社内語と一般語の使い分け）',
  '構成と分量（章立て・ページ数・スライド枚数・行数の目安）',
  '体裁（雛形・書式・図表・言い切り／敬体などの文体）',
  '根拠（引用してよい資料、数値の出典、確認が要る事実）',
  '書いてはいけないこと・触れ方に注意が要ること',
  '合否の基準（誰が何を見て「できた」と判断するか）',
  '納期・優先順位（完璧より先に要るものは何か）',
];

// 汎用の検証観点。ドメイン特化の観点は人がレビュー結果として対話で足す。
const VERIFY_CHECKS = [
  ['用語のブレ', '同じものを別の語で呼んでいないか。表記（英字・カナ・略語）が揺れていないか'],
  ['整合性', '数値・日付・名称・図表と本文が食い違っていないか。複数ファイル間でも一致しているか'],
  ['論理性', '主張と根拠がつながっているか。飛躍・循環・矛盾が無いか'],
  ['つながり', '章や段落の順序が自然か。前後の参照（前述・後述）が正しいか'],
  ['人間のわかりやすさ', '読者の前提知識で一読して分かるか。長すぎる文・多重否定・抽象語の連続が無いか'],
  ['AI 臭の排除', '定型的な言い回し（「重要です」「ぜひ」「以下に示します」の連発）、過剰な箇条書き、根拠の無い断定、空疎なまとめを削る'],
  ['ルール適合', '文書ルール（対象と目的・定型と体裁・記述内容・注意点）に沿っているか'],
];

function fence(text) {
  const body = String(text || '').trim();
  if (!body) return '（なし）';
  return `\`\`\`\n${body.replace(/```/g, '``​`')}\n\`\`\``;
}

function bullets(list) {
  return list.map((s) => `- ${s}`).join('\n');
}

// 形式ごとの作り方の手掛かりはカタログ（formats.js）が持つ。
function formatsBlock(formatIds) {
  return (formatIds || []).map((f) => `- ${formats.formatLabel(f)}: ${formats.formatHint(f)}`).join('\n');
}

function inputsBlock(inputs) {
  const rows = (inputs || []).map((it) => `- inputs/${it.name}${it.note ? `（${it.note}）` : ''}`);
  return rows.length ? rows.join('\n') : '（ファイルの入力はありません）';
}

function ruleBlock(rule) {
  if (!rule || !String(rule.content || '').trim()) {
    return '文書ルールは指定されていません。読者・目的・体裁は質問で確かめてください。';
  }
  return `文書ルール（${rule.name || rule.file}）。これが仕様の正典です。全文:\n${fence(rule.content)}`;
}

function sidecarRules(sidecarFile, manifestFile) {
  return [
    `改訂履歴はサイドカー \`${sidecarFile}\` に残します。**成果物を書き換えるたびに**次の形で末尾へ追記してください。`,
    '',
    '```',
    sidecar.entryTemplate(),
    '```',
    '',
    `成果物の一覧は \`${manifestFile}\` の \`outputs\` に書きます（配列。要素は`
      + ` \`{"file": "…", "format": "${formats.FORMATS.map((f) => f.id).join('|')}", "role": "その文書の役割",`
      + ' "relatedTo": ["関連するファイル名"], "relation": "関係の説明"}`）。'
      + '複数ファイルを作るときは、どれが主でどれが従か、参照関係（図は本文のどの章を表すか等）を relation に書きます。',
    '他のキーは変えないでください（dashboard が読みます）。',
  ].join('\n');
}

function workingRules(setDir) {
  return bullets([
    `作業フォルダは \`${setDir}\` です。ファイルの作成・変更は**このフォルダの中だけ**で行い、外のファイルは読むだけにしてください。`,
    'inputs/ の中身は入力です。上書きしないでください。',
    '実在しない資料・数値・固有名を作らないでください。分からないことは質問するか、「要確認」と明記してください。',
    '終わったら、作ったファイルの一覧と、未決のまま残した点を短く報告してください。',
  ]);
}

function questionProtocol(mode, divisions) {
  const areas = bullets(QUESTION_AREAS);
  const head = [
    '## 進め方',
    '',
    '### 1. まず徹底的に質問する',
    '文書ルールと入力を読んだうえで、書き始める前に次の観点を**すべて**確かめてください。',
    '分かっている点は「こう理解しました」と要約して確認し、分からない点は質問します。',
    '質問は一度にまとめて番号付きで出し、答えが返ったら次の不明点を出す、を**不明点が無くなるまで**繰り返します。',
    '推奨する答えがあれば添えてください（「未定なら A を勧めます。理由は…」）。',
    '',
    areas,
    '',
  ];
  if (mode === 'section') {
    const list = (divisions || []).length
      ? (divisions || []).map((d, i) => `${i + 1}. ${d.title}${d.note ? ` — ${d.note}` : ''}`).join('\n')
      : '（文書ルールに区分がありません。最初の質問で章立てを決め、それを区分として扱ってください）';
    return head.concat([
      '### 2. 区分ごとに作る',
      '文書は**意味的区分ごと**に進めます。区分は次の順です:',
      '',
      list,
      '',
      '区分ごとに「その区分に固有の質問 → 作成 → 提示して確認 → 直す → 次の区分へ」を回します。',
      '確認を取らずに次の区分へ進まないでください。全区分が終わったら全体を通して読み、つながりを整えます。',
      '',
    ]).join('\n');
  }
  return head.concat([
    '### 2. 一気に作る',
    '質問が出尽くしたら、全体の構成案を示して合意を取り、そのうえで**全体を一度に**作ります。',
    'できたら全体を提示し、指摘を受けて直します。',
    '',
  ]).join('\n');
}

function createPrompt({ name, setDir, mode, formats, rule, inputs, request, sidecarFile, manifestFile, divisions }) {
  return [
    `あなたは文書作成の専門家です。文書「${name}」を作ります。`,
    '',
    '## 作業の約束',
    workingRules(setDir),
    '',
    '## 文書ルール',
    ruleBlock(rule),
    '',
    '## 入力',
    '利用者からの依頼:',
    fence(request),
    '',
    '入力ファイル:',
    inputsBlock(inputs),
    '',
    '## 出力',
    '次の形式で成果物を作ります（複数のときは互いに関連づけ、命名を揃えます）:',
    formatsBlock(formats),
    '',
    questionProtocol(mode, divisions),
    '### 3. 記録',
    sidecarRules(sidecarFile, manifestFile),
  ].join('\n');
}

function resumePrompt({ name, setDir, instruction, rule, sidecarFile, manifestFile, outputs }) {
  const files = (outputs || []).map((o) => `- ${o.file}${o.role ? `（${o.role}）` : ''}`).join('\n');
  return [
    `文書「${name}」の作業を続けます。`,
    '',
    '## 作業の約束',
    workingRules(setDir),
    '',
    '## 文書ルール',
    ruleBlock(rule),
    '',
    '## いまの成果物',
    files || '（まだ成果物はありません）',
    '',
    `改訂履歴 \`${sidecarFile}\` を最初に読み、これまでの決めごと（利用者の意図）と指摘事項を踏まえてください。`,
    '',
    '## 今回の指示',
    fence(instruction),
    '',
    '不明点があれば作業の前に質問してください。変更したら提示して確認を取ります。',
    '',
    '## 記録',
    sidecarRules(sidecarFile, manifestFile),
  ].join('\n');
}

function verifyPrompt({ name, setDir, review, rule, sidecarFile, manifestFile, outputs }) {
  const files = (outputs || []).map((o) => `- ${o.file}${o.role ? `（${o.role}）` : ''}`).join('\n');
  const checks = VERIFY_CHECKS.map(([label, desc]) => `- **${label}**: ${desc}`).join('\n');
  return [
    `文書「${name}」を検証します。`,
    '',
    '## 作業の約束',
    workingRules(setDir),
    '',
    '## 文書ルール',
    ruleBlock(rule),
    '',
    '## 検証対象',
    files || '（成果物が見つかりません。フォルダを確認してください）',
    '',
    `改訂履歴 \`${sidecarFile}\` を最初に読み、過去の指摘の再発が無いかも見てください。`,
    '',
    '## 汎用の検証観点',
    checks,
    '',
    '## ドメイン固有のレビュー結果（利用者が入力）',
    fence(review),
    '',
    '## 進め方',
    bullets([
      '観点ごとに指摘を**箇所（ファイル・章・ページ）付き**で列挙し、重要度（高・中・低）を付けて提示します。',
      '直す前に、どれを直すか利用者に確認します。判断が要る指摘（内容の正否・方針）は勝手に直しません。',
      '確認が取れた指摘を直し、直した箇所と保留した指摘を報告します。',
      '検証中に文書ルールへ足すべき注意点が見つかったら、最後に「ルールへの追記案」として別に示します（ルールファイルは直接編集しません）。',
    ]),
    '',
    '## 記録',
    sidecarRules(sidecarFile, manifestFile),
    '検証で受けた指摘は、直したかどうかにかかわらず「指摘事項」へ残してください。',
  ].join('\n');
}

function ruleFormatSpec() {
  const sections = RULE_SECTIONS.map(([, label, help]) => `- \`## ${label}\` … ${help}`).join('\n');
  const formatList = formats.FORMATS.map((f) => `${f.id}（${f.label}）`).join(' / ');
  return [
    '文書ルールは 1 つの Markdown ファイルで、次の書式に**厳密に**従います。',
    '',
    '```',
    '---',
    'name: <ルールの名前>',
    'formats: <対応形式をカンマ区切り>',
    '---',
    '# 文書ルール: <ルールの名前>',
    '',
    '## 対象と目的',
    '…',
    '```',
    '',
    '節（すべて必須。この順で `##` 見出し）:',
    sections,
    '',
    `formats に書ける値: ${formatList}`,
    '「区分」は `- 名前 — 説明` の箇条書きで、文書を意味のまとまりで分けたものです（章立て）。',
    '出力は**ルール本文だけ**にしてください。前置き・後書き・コードフェンスは付けません。',
  ].join('\n');
}

function ruleDraftPrompt({ name, formats, draft, template }) {
  return [
    'あなたは文書作成の標準化を手伝う編集者です。利用者の原案を膨らませて、文書ルールを 1 本にまとめてください。',
    '',
    ruleFormatSpec(),
    '',
    '## 膨らませ方',
    bullets([
      '原案に書かれていることは尊重し、言い換えずに残す。足りない節は、原案から合理的に推測できる範囲で具体的に補う。',
      '推測で補った箇所は文末に「（要確認）」を付ける。実在が分からない固有名・数値・資料名を作らない。',
      '「注意点」には、この種類の文書で起こりがちな失敗（用語のブレ・根拠の欠落・読者の取り違え）を、この文書に即して書く。',
      '「区分」は 4〜10 個を目安に、読者の読む順で並べる。',
      '文は短く。命令形で、1 行 1 決めごと。',
    ]),
    '',
    `## ルールの名前\n${String(name || '').trim() || '（原案から決める）'}`,
    '',
    `## 対応形式\n${(formats || []).length ? formats.join(', ') : '（原案から決める）'}`,
    '',
    '## テンプレート（雛形・既存文書の説明。任意）',
    fence(template),
    '',
    '## 原案',
    fence(draft),
  ].join('\n');
}

function ruleFromHistoryPrompt({ name, formats, history, manifest, rule }) {
  return [
    `文書「${name}」の改訂履歴から、同じ種類の文書を次に作るときの文書ルールを起こしてください。`,
    '',
    ruleFormatSpec(),
    '',
    '## 起こし方',
    bullets([
      '改訂履歴の「利用者の意図」は、その文書で決まった方針です。ルールの「対象と目的」「記述内容」へ一般化して書く。',
      '「指摘事項」は次回の「注意点」になる。一度きりの事故ではなく、再発しうる形に言い換える。',
      '「変更」の履歴から体裁・構成の最終形を読み取り、「定型と体裁」「区分」に反映する。',
      '元の文書ルールがある場合は、それを土台に**差分だけ足す**（既存の決めごとを消さない）。',
      '個別案件の固有名（顧客名・日付・金額）はルールへ持ち込まない。必要なら「例:」として一般化する。',
    ]),
    '',
    `## ルールの名前\n${String(name || '').trim()}`,
    `## 対応形式\n${(formats || []).join(', ') || '（履歴から決める）'}`,
    '',
    '## 元の文書ルール',
    rule && rule.content ? fence(rule.content) : '（このときは文書ルールを使っていません）',
    '',
    '## 文書の定義（document.json）',
    fence(manifest),
    '',
    '## 改訂履歴',
    fence(history),
  ].join('\n');
}

function feedbackRulePrompt({ name, feedback, history, rule, target }) {
  const existing = target === 'existing' && rule && rule.content;
  return [
    `文書「${name}」が完成しました。利用者のフィードバックを文書ルールへ反映してください。`,
    '',
    ruleFormatSpec(),
    '',
    existing
      ? '## やること\n既存の文書ルールを**更新した全文**として返してください。既存の決めごとは消さず、フィードバックと履歴から分かった新しい決めごと・注意点を足します。変えた箇所には行末に「（更新）」を付けます。'
      : '## やること\nこの文書とフィードバックから、**新しい**文書ルールを起こしてください。個別案件の固有名は一般化します。',
    '',
    '## 利用者のフィードバック',
    fence(feedback),
    '',
    '## 既存の文書ルール',
    rule && rule.content ? fence(rule.content) : '（なし）',
    '',
    '## 改訂履歴',
    fence(history),
  ].join('\n');
}

module.exports = {
  QUESTION_AREAS,
  VERIFY_CHECKS,
  createPrompt,
  resumePrompt,
  verifyPrompt,
  ruleDraftPrompt,
  ruleFromHistoryPrompt,
  feedbackRulePrompt,
  ruleFormatSpec,
};
