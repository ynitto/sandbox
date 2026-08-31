'use strict';

// `{{key}}` 入力欄の 1 実装（画面側）。
//
// 検出・検証・置換は main の base/main/template-parameters.js が正典で、こちらは
// 「キーの並び → 入力欄」と「入力欄 → 値」の対応だけを持つ。定常業務の実行条件
// ダイアログと、保存形ワークフローの投入・一括投函が同じ器を使う
// （docs/plans/2026-08-31-agent-session-reuse-rerun-design.md §4 — C7）。
//
// 入力型は文字列だけ・全項目必須（未入力のまま実行させない）。

(function expose(root, factory) {
  const api = factory(root);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.parameterFields = api;
})(typeof globalThis !== 'undefined' ? globalThis : window, (root) => {
  const esc = (value) => (typeof root.esc === 'function'
    ? root.esc(String(value == null ? '' : value))
    : String(value == null ? '' : value));

  // keys は main が検出した順。並べ替えず、Tab 順も表示順と一致させる。
  function fieldsHtml(keys, options = {}) {
    const prefix = String(options.prefix || 'parameter');
    const attribute = String(options.attribute || 'data-parameter');
    const values = options.values && typeof options.values === 'object' ? options.values : {};
    const list = Array.isArray(keys) ? keys : [];
    if (!list.length) return '';
    return list.map((key, index) => `<div class="field">
      <label for="${esc(prefix)}-${index}">${esc(key)}</label>
      <input id="${esc(prefix)}-${index}" ${attribute}="${esc(key)}" type="text"
        autocomplete="off" required value="${esc(values[key] == null ? '' : values[key])}"></div>`).join('');
  }

  function inputsIn(container, attribute = 'data-parameter') {
    if (!container) return [];
    return [...container.querySelectorAll(`[${attribute}]`)];
  }

  function readValues(container, attribute = 'data-parameter') {
    return Object.fromEntries(inputsIn(container, attribute)
      .map((input) => [input.getAttribute(attribute), String(input.value || '').trim()]));
  }

  // 全項目が非空になるまで実行させない（未入力のまま `{{key}}` を投函しない）。
  function complete(container, attribute = 'data-parameter') {
    return inputsIn(container, attribute).every((input) => String(input.value || '').trim());
  }

  return { fieldsHtml, inputsIn, readValues, complete };
});
