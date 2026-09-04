'use strict';

// `{{key}}` テンプレート入力の 1 実装。
//
// 出どころは定常業務（cowork）の実行パラメータ入力ダイアログ
// （docs/plans/2026-08-09-agent-dashboard-routine-parameters-dialog-design.md）で、
// 保存形ワークフローの `{{key}}` もここを共有する
// （docs/plans/2026-08-31-agent-session-reuse-rerun-design.md §4 — C7: 検出ロジックを
// 2 実装にしない）。検出・検証・置換の三つを 1 か所に置くのは、「画面が拾ったキー」と
// 「main が要求するキー」がずれると、入力したのに未入力で断られる／入れていないのに
// 実行できる、のどちらかが黙って起きるため。
//
// 入力型は文字列だけ（上記設計の初版どおり）。

// `{{key}}` 形式。ドット付き（`context.KEY`）も 1 キーとして拾う。
const TEMPLATE_PARAMETER_RE = /\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}/g;
// agent-loop の定常プロンプトは `{key}` の単重括弧も使う（`{{key}}` とは別記法）。
const LOOP_PARAMETER_RE = /\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}|(?<!\{)\{([A-Za-z_][A-Za-z0-9_.-]*)\}(?!\})/g;

// statemachine 実行器が実行時に注入する変数。人が入れる値ではないので入力欄にしない。
// 正典は statemachine-use の references/schema.md「Context Variable Reference」。
const RUNTIME_CONTEXT_KEYS = new Set([
  'today', 'now', 'history', 'step_count', 'last_output', 'current_state', 'context',
  'check_status', 'check_ok', 'check_output',
]);

// 予約語。テンプレートの中では従来どおりの意味を持ち、**入力パラメータとして扱わない**。
//   request … 投入 plan の goal で要求テキストへ置換される（置換はエンジンの 1 か所だけ。
//             agent-flow spec §3.2）。dashboard 側で値を差し込んではいけない。
//   その他  … statemachine の組み込み変数（上記）。
const RESERVED_TEMPLATE_KEYS = new Set(['request', ...RUNTIME_CONTEXT_KEYS]);

function collectKeys(re, texts) {
  const keys = [];
  const seen = new Set();
  for (const text of texts) {
    for (const match of String(text || '').matchAll(re)) {
      const key = match[1] || match[2];
      if (!key || seen.has(key)) continue;
      seen.add(key);
      keys.push(key);
    }
  }
  return keys;
}

function templateParameterKeys(...texts) {
  return collectKeys(TEMPLATE_PARAMETER_RE, texts);
}

function loopParameterKeys(...texts) {
  return collectKeys(LOOP_PARAMETER_RE, texts);
}

// 予約語を除いた入力パラメータ。保存形ワークフロー（goal / request）が使う。
function inputParameterKeys(...texts) {
  return templateParameterKeys(...texts).filter((key) => !RESERVED_TEMPLATE_KEYS.has(key));
}

function isPlainObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

// Renderer から届いた値は信頼しない。宣言に無いキー・未入力は main 側で断る。
function validateParameters(spec, raw) {
  if (spec.error) throw new Error(`入力パラメータを確認できません: ${spec.error}`);
  const values = raw == null ? {} : raw;
  if (!isPlainObject(values)) throw new Error('入力パラメータの形式が不正です');
  const unknown = Object.keys(values).filter((key) => !spec.keys.includes(key));
  if (unknown.length) throw new Error(`未定義の入力パラメータです: ${unknown.join(', ')}`);
  const missing = spec.keys.filter((key) => !Object.prototype.hasOwnProperty.call(values, key)
    || String(values[key]).trim() === '');
  if (missing.length) throw new Error(`入力してください: ${missing.join(', ')}`);
  return Object.fromEntries(spec.keys.map((key) => [key, String(values[key]).trim()]));
}

// 値のあるキーだけ置換する。宣言に無い `{{…}}`（予約語を含む）はそのまま残す
// ——後段（agent-flow の `{{request}}` 置換）の入力を画面が食い潰さないため。
function applyParameters(prompt, values) {
  return String(prompt || '').replace(LOOP_PARAMETER_RE, (whole, doubleKey, singleKey) => {
    const key = doubleKey || singleKey;
    return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : whole;
  });
}

module.exports = {
  TEMPLATE_PARAMETER_RE,
  LOOP_PARAMETER_RE,
  RUNTIME_CONTEXT_KEYS,
  RESERVED_TEMPLATE_KEYS,
  templateParameterKeys,
  loopParameterKeys,
  inputParameterKeys,
  validateParameters,
  applyParameters,
};
