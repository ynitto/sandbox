'use strict';

// 改訂履歴のサイドカー `<id>.history.md` の書式。
//
// 書き手は 2 者——dashboard（人が起こした依頼）とエージェント（成果物を書き換えたとき）。
// 両者が同じ形で追記できるよう、**項目の見出しはここ 1 か所**で決め、実際に書く
// historyEntry と、依頼文に載せる雛形 entryTemplate を同じ表から作る。
//
//   ## <YYYY-MM-DD HH:MM> — <何をしたか>（<誰が>）
//   ### 変更
//   ### 利用者の意図
//   ### 指摘事項

const fs = require('fs');
const path = require('path');

const ENTRY_SECTIONS = [
  ['changes', '変更', '<ファイル名>: <何をどう変えたか>'],
  ['intent', '利用者の意図', '<質問への回答や指示から読み取った意図。決めごとはここに残す>'],
  ['findings', '指摘事項', '<レビューで受けた指摘と、どう扱ったか（対応／保留／却下と理由）>'],
];

const AUTHOR_USER = '利用者';
const AUTHOR_AGENT = 'エージェント';

function sidecarName(id) {
  return `${id}.history.md`;
}

function stamp(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function bulletList(items, empty = '- （なし）') {
  const rows = (items || []).map((s) => String(s || '').trim()).filter(Boolean);
  return rows.length ? rows.map((s) => `- ${s.replace(/\r?\n/g, '\n  ')}`).join('\n') : empty;
}

// 1 項目分の Markdown。
function historyEntry({ kind, by = AUTHOR_USER, at = new Date(), changes = [], intent = [], findings = [] }) {
  const values = { changes, intent, findings };
  const lines = [`## ${stamp(at)} — ${kind}（${by}）`, ''];
  for (const [key, label] of ENTRY_SECTIONS) {
    lines.push(`### ${label}`, bulletList(values[key]), '');
  }
  return lines.join('\n');
}

// 依頼文に載せる雛形（エージェントに同じ形で書かせる）。
function entryTemplate() {
  const lines = [`## <YYYY-MM-DD HH:MM> — <何をしたか 1 行>（${AUTHOR_AGENT}）`, ''];
  for (const [, label, placeholder] of ENTRY_SECTIONS) {
    lines.push(`### ${label}`, `- ${placeholder}`, '');
  }
  return lines.join('\n').trimEnd();
}

function appendSidecar(setDir, id, entry) {
  const file = path.join(setDir, sidecarName(id));
  const exists = fs.existsSync(file);
  const head = exists ? '' : `# 改訂履歴: ${id}\n\n`
    + '変更・利用者の意図・指摘事項を時系列で残す。文書ルールの元になる。\n\n';
  fs.appendFileSync(file, `${head}${exists ? '\n' : ''}${entry}`, 'utf8');
  return file;
}

function readSidecar(setDir, id) {
  try {
    return fs.readFileSync(path.join(setDir, sidecarName(id)), 'utf8');
  } catch {
    return '';
  }
}

module.exports = {
  ENTRY_SECTIONS,
  AUTHOR_USER,
  AUTHOR_AGENT,
  sidecarName,
  stamp,
  historyEntry,
  entryTemplate,
  appendSidecar,
  readSidecar,
};
