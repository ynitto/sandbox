'use strict';

// 対応する文書形式のカタログ。形式を足すときはこの配列に 1 行足すだけでよい——
// 画面の選択肢（overview.formats）・成果物の判定（formatOf）・依頼文の手掛かり（hint）・
// 文書ルールの formats 値の検証（normalizeFormats）は、すべてここから引く。
//
//   id     … 定義ファイルと依頼文で使う識別子（拡張子と同じ綴り）
//   label  … 画面の表示名
//   ext    … 成果物の判定に使う拡張子（長いものを先に照合するので `.drawio.svg` と `.svg` を共存できる）
//   hint   … 依頼文に載せる作り方の手掛かり。skills は本リポジトリの skills/ にある名前
//            （あればスキル経由、無ければ同等のライブラリで自作させる）

const FORMATS = [
  {
    id: 'docx', label: 'Word', ext: '.docx', skills: ['doc-coauthoring'],
    hint: 'doc-coauthoring スキルがあればその流儀で構成を決め、生成は python-docx または docx ライブラリで行う。見出しスタイル・目次・ページ番号を雛形に合わせる。',
  },
  {
    id: 'pptx', label: 'PowerPoint', ext: '.pptx', skills: ['presenter'],
    hint: 'presenter スキル（JSON スペック → pptx）があれば使う。無ければ python-pptx。1 スライド 1 メッセージ、発表者ノートに根拠を残す。',
  },
  {
    id: 'xlsx', label: 'Excel', ext: '.xlsx', skills: ['xlsx-report-builder'],
    hint: 'xlsx-report-builder スキル（JSON スペック → xlsx）があれば使う。無ければ openpyxl。数式は値でなく式で入れ、シート名と見出し行を固定する。',
  },
  {
    id: 'md', label: 'Markdown', ext: '.md', skills: ['technical-writer'],
    hint: '見出し階層を 3 段まで、表は GFM。リンク先は相対パスで実在させる。',
  },
  {
    id: 'drawio.svg', label: 'draw.io 図（SVG）', ext: '.drawio.svg', skills: [],
    hint: 'SVG の中に draw.io の図データ（content 属性の mxfile）を埋め込んだ「編集できる SVG」で書く。図の要素名は本文の用語と一致させる。',
  },
];

const byId = new Map(FORMATS.map((f) => [f.id, f]));

// 文字列（"docx, pptx" / "[docx, pptx]"）でも配列でも受け、既知の id だけを順序を保って返す。
function normalizeFormats(raw) {
  const list = Array.isArray(raw) ? raw : String(raw || '').replace(/^\s*\[|\]\s*$/g, '').split(/[,\s]+/);
  const out = [];
  for (const item of list) {
    const id = String(item || '').trim().toLowerCase().replace(/^\.|^["']|["']$/g, '');
    if (byId.has(id) && !out.includes(id)) out.push(id);
  }
  return out;
}

function formatLabel(id) {
  const row = byId.get(String(id || ''));
  return row ? row.label : String(id || '');
}

function formatHint(id) {
  const row = byId.get(String(id || ''));
  return row ? row.hint : '';
}

// ファイル名から形式を判定する。長い拡張子（.drawio.svg）を先に見る。
const byExtLength = [...FORMATS].sort((a, b) => b.ext.length - a.ext.length);
function formatOf(file) {
  const lower = String(file || '').toLowerCase();
  const row = byExtLength.find((f) => lower.endsWith(f.ext));
  return row ? row.id : '';
}

// 画面へ渡す形（id と label だけ。hint は依頼文の都合なので画面へ出さない）。
function formatOptions() {
  return FORMATS.map((f) => ({ id: f.id, label: f.label }));
}

module.exports = { FORMATS, normalizeFormats, formatLabel, formatHint, formatOf, formatOptions };
