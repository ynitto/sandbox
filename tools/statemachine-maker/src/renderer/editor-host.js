'use strict';

// 編集画面が動くアプリ固有の窓口を、一つの明示的なシームへ集約する。
// standalone は window.api、agent-app の埋め込み画面は window.api.automation
// または親画面の window.api.automation を使う。
(function exposeEditorHost(scope) {
  function safely(read) {
    try { return read(); } catch { return null; }
  }

  function resolve(target = scope, options = {}) {
    const embedded = options.embedded == null
      ? !!safely(() => target.document.body.classList.contains('embedded'))
      : !!options.embedded;
    const standalone = safely(() => target.api);
    const localAutomation = safely(() => target.api.automation);
    const parentAutomation = safely(() => target.parent.api.automation);
    const bridge = embedded
      ? (localAutomation || parentAutomation)
      : standalone;
    if (!bridge) throw new Error('編集機能の接続を初期化できません');
    return bridge;
  }

  const editorHost = Object.freeze({ resolve });
  if (typeof module !== 'undefined' && module.exports) module.exports = editorHost;
  if (scope) scope.StatemachineEditorHost = editorHost;
})(typeof window === 'undefined' ? globalThis : window);
