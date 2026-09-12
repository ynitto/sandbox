'use strict';

(function defineStatemachineWorkbench(global) {
  const tag = 'statemachine-workbench';
  if (!global.customElements || global.customElements.get(tag)) return;

  class StatemachineWorkbenchElement extends HTMLElement {
    constructor() {
      super();
      this.pendingNavigation = null;
      this.controller = null;
      const root = this.attachShadow({ mode: 'open' });
      const styles = [this.getAttribute('stylesheet'), this.getAttribute('host-stylesheet')]
        .filter(Boolean)
        .map((href) => `<link rel="stylesheet" href="${href}">`)
        .join('');
      root.innerHTML = `${styles}
        <header id="bar">
          <div class="bar-left"><button type="button" id="btn-home" class="ghost" title="一覧へ戻る" hidden>‹ 一覧</button><span class="brand">Statemachine Maker</span></div>
          <div class="bar-center" id="bar-center"></div><div class="bar-right" id="bar-right"></div>
        </header>
        <main id="main"></main>
        <dialog id="dlg-record"></dialog><dialog id="dlg-files"></dialog>
        <dialog id="dlg-ai-draft"></dialog><dialog id="dlg-ai"></dialog>
        <dialog id="dlg-run"></dialog><dialog id="dlg-settings"></dialog>
        <div id="toast" hidden></div>`;
    }

    setController(controller) {
      this.controller = controller;
      if (this.pendingNavigation) {
        const pending = this.pendingNavigation;
        this.pendingNavigation = null;
        controller.navigate(pending);
      }
    }

    navigate(payload) {
      if (!this.controller) { this.pendingNavigation = payload; return Promise.resolve(); }
      return this.controller.navigate(payload);
    }

    // 定義と実行状態を読み直す（親の会話で AI がファイルを書いたあと）。
    refresh() {
      if (!this.controller || typeof this.controller.refresh !== 'function') return Promise.resolve();
      return this.controller.refresh();
    }

    // ワークフローの下書き（教える会話が書いた定義）だけを読み直す。
    reloadFlowTeaching() {
      if (!this.controller || typeof this.controller.reloadFlowTeaching !== 'function') return Promise.resolve();
      return this.controller.reloadFlowTeaching();
    }
  }

  global.customElements.define(tag, StatemachineWorkbenchElement);
})(window);
