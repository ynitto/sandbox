'use strict';

// coherence: doc=docs/plans/2026-08-15-workflow-feature-improvement-proposals.md, test=tools/agent-dashboard/test/adhoc-flow.test.js

// 設計成果の契約（必須節と、節の中に要る項目）の唯一の実装。
//
// 設計セッション（adhoc-flow/main/design-session.js）と作業準備（preparation/main/preparation.js）
// は同じ「実行できる設計書か」を判定する。判定が 2 実装に分かれていると、片方だけ育って
// 「設計セッションは通すが作業準備は弾く」という食い違いが起きるため、ここに 1 本化する。
// Renderer 側の実行前チェック（features/adhoc-flow.js の readinessCheck）は**別の判定**——
// 外部 CLI / IDE が書いた設計書を弾かないための緩い助言で、実行をブロックしない。
//
// 必須項目（REQUIRED_ITEMS）は、節見出しがあるだけでは防げない失敗のための検査。
// 「設計 run は読み取り専用」という契約が dashboard の文言と plan までで、実行エンジンには
// 強制が無かった実例に対して、契約ごとの強制レイヤーを設計の段階で書き分けさせる。

const REQUIRED_SECTIONS = ['目的', '変更対象', '受入基準', '検証方法'];

// 見出しの言い換え（依頼欄など、書式が多様な入力を読むとき用）。
const SECTION_ALIASES = {
  目的: ['目的', '狙い'],
  変更対象: ['変更対象', '対象', 'スコープ'],
  受入基準: ['受入基準', '完了条件'],
  検証方法: ['検証方法', '検証', 'テスト方法'],
};

const REQUIRED_ITEMS = [{
  section: '変更対象',
  label: '強制レイヤー',
  // 「どの層で強制するか」を書いていれば通す。言い回しは縛らない（強制レイヤー/強制する層/強制箇所）。
  pattern: /強制(?:レイヤー?|する層|箇所|ポイント)/,
}];

function headingNames(section, alias) {
  return alias ? (SECTION_ALIASES[section] || [section]) : [section];
}

function headingLevel(line) {
  const heading = String(line).match(/^(#{1,6})\s*(.+?)\s*$/);
  return heading ? { level: heading[1].length, title: heading[2] } : null;
}

function sectionIndex(document, section, alias = false) {
  const names = headingNames(section, alias);
  const lines = String(document || '').replace(/\r\n/g, '\n').split('\n');
  return lines.findIndex((line) => {
    const heading = headingLevel(line);
    return heading && names.includes(heading.title);
  });
}

// 節の本文（見出しの次行から、同じか上位の見出しの手前まで）。
function sectionBody(document, section, alias = false) {
  const lines = String(document || '').replace(/\r\n/g, '\n').split('\n');
  const at = sectionIndex(document, section, alias);
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

function missingSections(document, alias = false) {
  return REQUIRED_SECTIONS.filter((section) => sectionIndex(document, section, alias) < 0);
}

// 節はあるのに中の必須項目が無いもの。節が無い場合はここでは数えない（節の不足として出る）。
function missingItems(document, alias = false) {
  return REQUIRED_ITEMS
    .filter((item) => sectionIndex(document, item.section, alias) >= 0
      && !item.pattern.test(sectionBody(document, item.section, alias)))
    .map((item) => `${item.section}の${item.label}`);
}

// 実装へ渡せる設計書かどうか。空配列 = 渡せる。
function documentIssues(document, alias = false) {
  return [...missingSections(document, alias), ...missingItems(document, alias)];
}

module.exports = {
  REQUIRED_SECTIONS,
  SECTION_ALIASES,
  REQUIRED_ITEMS,
  sectionBody,
  missingSections,
  missingItems,
  documentIssues,
};
