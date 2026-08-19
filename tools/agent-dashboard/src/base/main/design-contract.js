'use strict';

// coherence: doc=docs/plans/2026-08-15-workflow-feature-improvement-proposals.md, test=tools/agent-dashboard/test/adhoc-flow.test.js

// 設計書の書式（必須節と、節の中に要る項目）を機械で数える唯一の実装。
//
// 書式そのものはここに持たない。正典は手法カタログの `design-document-format`
// （`methods/design-document-format.json`）で、設計 run への指示（fragments）と、
// 実装へ渡す前のゲートが数える構造（format）が同じ 1 ファイルに並ぶ。リポジトリの
// `.agents/methods/` に同じ id を置けば、そのリポジトリの書式へ丸ごと差し替えられる。
// このファイルが持つのは「宣言をどう読み、文書をどう数えるか」だけ。
//
// 設計セッション（adhoc-flow/main/design-session.js）と作業準備（preparation/main/preparation.js）
// は同じ「実行できる設計書か」を判定する。判定が 2 実装に分かれていると、片方だけ育って
// 「設計セッションは通すが作業準備は弾く」という食い違いが起きるため、ここに 1 本化する。
// Renderer 側の実行前チェック（features/adhoc-flow.js の readinessCheck）は**別の判定**——
// 外部 CLI / IDE が書いた設計書を弾かないための緩い助言で、実行をブロックしない。

// 書式を宣言する手法の id。カタログから引く側（adhoc-flow/main/adhoc.js）と共有する。
const FORMAT_METHOD_ID = 'design-document-format';

// カタログから書式が引けないときに数える不足。黙って全部通す（＝ゲートが消える）よりも、
// 書式が無いことを不足として見せる（フェイルクローズ）。
const FORMAT_MISSING = `設計書の書式（${FORMAT_METHOD_ID}）が見つかりません`;

function normalizeFormat(raw) {
  if (!raw || typeof raw !== 'object') throw new Error('設計書の書式が不正です');
  const sections = (Array.isArray(raw.sections) ? raw.sections : [])
    .map((section) => String(section || '').trim()).filter(Boolean);
  if (!sections.length) throw new Error('設計書の書式には必須節を1つ以上宣言してください');
  if (new Set(sections).size !== sections.length) throw new Error('設計書の書式の必須節が重複しています');
  // 言い換え見出し（依頼欄など、書式が多様な入力を読むとき用）。宣言が無い節は見出し名そのもの。
  const aliasesRaw = raw.aliases && typeof raw.aliases === 'object' ? raw.aliases : {};
  const aliases = {};
  for (const section of sections) {
    const names = (Array.isArray(aliasesRaw[section]) ? aliasesRaw[section] : [])
      .map((name) => String(name || '').trim()).filter(Boolean);
    aliases[section] = names.length ? [...new Set([section, ...names])] : [section];
  }
  const items = (Array.isArray(raw.items) ? raw.items : []).map((item) => {
    const section = String((item && item.section) || '').trim();
    const label = String((item && item.label) || '').trim();
    const pattern = String((item && item.pattern) || '').trim();
    if (!section || !label || !pattern) {
      throw new Error('設計書の書式の必須項目には section・label・pattern が必要です');
    }
    if (!sections.includes(section)) {
      throw new Error(`設計書の書式の必須項目が宣言していない節を指しています: ${section}`);
    }
    try {
      RegExp(pattern);
    } catch {
      throw new Error(`設計書の書式の必須項目の pattern が正規表現として不正です: ${pattern}`);
    }
    return { section, label, pattern };
  });
  return { sections, aliases, items };
}

// 手法 1 件（カタログの JSON）から書式を読む。宣言が無ければ null。
function readFormat(method) {
  if (!method || typeof method !== 'object' || !method.format) return null;
  return normalizeFormat(method.format);
}

function headingNames(format, section, alias) {
  if (!alias) return [section];
  return (format.aliases && format.aliases[section]) || [section];
}

function headingLevel(line) {
  const heading = String(line).match(/^(#{1,6})\s*(.+?)\s*$/);
  return heading ? { level: heading[1].length, title: heading[2] } : null;
}

function sectionIndex(document, format, section, alias = false) {
  const names = headingNames(format, section, alias);
  const lines = String(document || '').replace(/\r\n/g, '\n').split('\n');
  return lines.findIndex((line) => {
    const heading = headingLevel(line);
    return heading && names.includes(heading.title);
  });
}

// 節の本文（見出しの次行から、同じか上位の見出しの手前まで）。
function sectionBody(document, format, section, alias = false) {
  const lines = String(document || '').replace(/\r\n/g, '\n').split('\n');
  const at = sectionIndex(document, format, section, alias);
  if (at < 0) return '';
  const level = headingLevel(lines[at]).level;
  const body = [];
  for (const line of lines.slice(at + 1)) {
    const heading = headingLevel(line);
    if (heading && heading.level <= level) break;
    body.push(line);
  }
  return body.join('\n');
}

function missingSections(document, format, alias = false) {
  if (!format) return [FORMAT_MISSING];
  return format.sections.filter((section) => sectionIndex(document, format, section, alias) < 0);
}

// 節はあるのに中の必須項目が無いもの。節が無い場合はここでは数えない（節の不足として出る）。
function missingItems(document, format, alias = false) {
  if (!format) return [];
  return format.items
    .filter((item) => sectionIndex(document, format, item.section, alias) >= 0
      && !new RegExp(item.pattern).test(sectionBody(document, format, item.section, alias)))
    .map((item) => `${item.section}の${item.label}`);
}

// 実装へ渡せる設計書かどうか。空配列 = 渡せる。
function documentIssues(document, format, alias = false) {
  return [...missingSections(document, format, alias), ...missingItems(document, format, alias)];
}

module.exports = {
  FORMAT_METHOD_ID,
  FORMAT_MISSING,
  normalizeFormat,
  readFormat,
  sectionBody,
  missingSections,
  missingItems,
  documentIssues,
};
