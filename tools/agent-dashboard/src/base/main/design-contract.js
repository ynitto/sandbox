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
  // 終端 goal（設計 run への指示文）に添える補足。判定には使わない。
  hint: '（例: 読み取り専用は agent-flow の起動引数で強制、dashboard は表示のみ）。'
    + '文言でしか守られていない契約が残っていないか自己点検し、残るなら強制する層を決めてください。',
}];

// --- 契約の宣言（カスタム設計フロー向けの汎用インターフェース） -------------------
//
// 設計フロー定義は contract: { sections: [...], items: [{ section, label, pattern, hint? }] }
// を宣言でき、無指定はこのファイルの既定（必須4節＋変更対象の強制レイヤー）になる。
// pattern は JSON で運べるよう正規表現の**文字列**で宣言する。判定（documentIssues）と
// 設計 run への指示文（adhoc-flow の designOutputContract）は同じ解決結果を見る。

function defaultContract() {
  return {
    sections: [...REQUIRED_SECTIONS],
    items: REQUIRED_ITEMS.map((item) => ({
      section: item.section,
      label: item.label,
      pattern: item.pattern.source,
      ...(item.hint ? { hint: item.hint } : {}),
    })),
  };
}

// フロー定義が宣言した contract の正規化。壊れた宣言は黙って既定へ落とさず弾く
// （宣言したつもりの契約が無検査で走るのを防ぐ）。
function normalizeContract(raw) {
  if (!raw || typeof raw !== 'object') throw new Error('設計契約が不正です');
  const sections = (Array.isArray(raw.sections) ? raw.sections : [])
    .map((section) => String(section || '').trim()).filter(Boolean);
  if (!sections.length) throw new Error('設計契約には必須節を1つ以上宣言してください');
  if (new Set(sections).size !== sections.length) throw new Error('設計契約の必須節が重複しています');
  const items = (Array.isArray(raw.items) ? raw.items : []).map((item) => {
    const section = String((item && item.section) || '').trim();
    const label = String((item && item.label) || '').trim();
    const pattern = String((item && item.pattern) || '').trim();
    const hint = String((item && item.hint) || '').trim();
    if (!section || !label || !pattern) {
      throw new Error('設計契約の必須項目には section・label・pattern が必要です');
    }
    if (!sections.includes(section)) {
      throw new Error(`設計契約の必須項目が宣言していない節を指しています: ${section}`);
    }
    try {
      RegExp(pattern);
    } catch {
      throw new Error(`設計契約の必須項目の pattern が正規表現として不正です: ${pattern}`);
    }
    return { section, label, pattern, ...(hint ? { hint } : {}) };
  });
  return { sections, items };
}

// 判定・指示文の生成が使う解決済み契約。宣言（正規化済み or 生）が無ければ既定。
function resolveContract(contract) {
  if (!contract) return defaultContract();
  return normalizeContract(contract);
}

function itemPattern(item) {
  return item.pattern instanceof RegExp ? item.pattern : new RegExp(String(item.pattern));
}

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

function missingSections(document, alias = false, contract = null) {
  return resolveContract(contract).sections
    .filter((section) => sectionIndex(document, section, alias) < 0);
}

// 節はあるのに中の必須項目が無いもの。節が無い場合はここでは数えない（節の不足として出る）。
function missingItems(document, alias = false, contract = null) {
  return resolveContract(contract).items
    .filter((item) => sectionIndex(document, item.section, alias) >= 0
      && !itemPattern(item).test(sectionBody(document, item.section, alias)))
    .map((item) => `${item.section}の${item.label}`);
}

// 実装へ渡せる設計書かどうか。空配列 = 渡せる。contract 無指定は既定契約で判定する。
function documentIssues(document, alias = false, contract = null) {
  return [...missingSections(document, alias, contract), ...missingItems(document, alias, contract)];
}

module.exports = {
  REQUIRED_SECTIONS,
  SECTION_ALIASES,
  REQUIRED_ITEMS,
  defaultContract,
  normalizeContract,
  resolveContract,
  sectionBody,
  missingSections,
  missingItems,
  documentIssues,
};
