'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// 定常業務領域の「実行の記録」と「定常業務の設定」
// ---------------------------------------------------------------------------
//
// 定常業務（cowork / kiro-loop / agent-loop）は、この dashboard 自身が実行側になる
// 唯一のワークロードである（README「定常業務だけのフォルダは別」）。だから領域として
// 「動かす（作業）」「動いた結果を追う（実行の記録）」「動かし方を決める（設定）」の
// 3 つが揃う。他の領域と違い、起動の導線がこの画面にあるのはそのためで、
// **実行エンジン（agent-project serve）を起こす経路はここにも無い**（設計 §2.1 の非目標）。
//
// 設定がここにあるのは、cowork 固有の設定（roots / loopProvider / 各コマンド）が
// 「端末のすべてのワークロードに効く」全体設定ではないため。全体設定には agent-control /
// agent-instructions / 予算のような**横断して効くもの**だけを残す。

// 実行の記録タブ。左に作業、右にその作業の記録（この画面からの実行 + ログ）。
function renderRoutineRuns() {
  const pane = $('tab-routine-runs');
  if (!pane) return;
  const folder = selectedProjectFolder();
  const entries = coworkItemsForFolder(folder);
  const selected = state.coworkHistory ? state.coworkHistory.id : '';
  if (!entries.length) {
    pane.innerHTML = `<section class="routine-page">
      ${routinePageHeaderHtml('実行の記録', 'この端末から動かした記録と、リポジトリに残ったログを読みます。')}
      <div class="empty compact">この作業フォルダにはまだ作業がありません。「作業」タブから追加してください。</div>
    </section>`;
    return;
  }
  const list = entries
    .map((item) => `<button type="button" role="tab" class="cowork-routine-option ${selected === item.id ? 'selected' : ''}"
      aria-selected="${selected === item.id ? 'true' : 'false'}" data-routine-run="${esc(item.id)}"
      title="${esc(item.name || item.id)}">
      <span class="name">${esc(item.name || item.id)}</span>
      <span class="muted">${esc(coworkKindLabel(item.type))}</span>
    </button>`)
    .join('');
  pane.innerHTML = `<section class="routine-page">
    ${routinePageHeaderHtml('実行の記録', 'この端末から動かした記録と、リポジトリに残ったログを読みます。')}
    <div class="cowork-split-view">
      <section class="cowork-list-pane" role="tablist" aria-label="記録を見る作業">${list}</section>
      <section class="cowork-detail-pane" id="routine-runs-body">${coworkHistoryBodyHtml(state.coworkHistory)}</section>
    </div>
  </section>`;
  for (const btn of pane.querySelectorAll('[data-routine-run]')) {
    btn.addEventListener('click', () => {
      const item = entries.find((x) => x.id === btn.dataset.routineRun);
      loadCoworkHistory(btn.dataset.routineRun, item ? item.name : '');
    });
  }
  bindCoworkHistoryBody(pane);
}

function coworkKindLabel(type) {
  return type === 'state-machine' ? '手順付き作業' : '繰り返し作業';
}

function routinePageHeaderHtml(title, desc) {
  return `<header class="area-page-header">
    <h2>${esc(title)}</h2>
    <p class="muted">${esc(desc)}</p>
  </header>`;
}

// 定常業務の設定タブ。全体設定にあった「定常業務」節をそのまま領域の中へ移した。
// HTML の組み立ては元の関数（globalSettingsRoutineHtml）を使い回し、配線だけこちらが持つ。
function renderRoutineSettings() {
  const pane = $('tab-routine-settings');
  if (!pane) return;
  pane.innerHTML = `<section class="routine-page">
    ${routinePageHeaderHtml('定常業務の設定',
      'この端末で定常業務を動かすための設定です。ほかのワークロードには影響しません。')}
    ${globalSettingsRoutineHtml()}
  </section>`;
  populateSettingsFields();
  setupRoutineSettings(pane);
}

// 全体設定ページの setupGlobalSettings から、定常業務に関わる配線だけを取り出したもの。
function setupRoutineSettings(root) {
  for (const el of root.querySelectorAll('[id^="cfg-"]')) {
    el.addEventListener('input', () => {
      state.globalSettingsDirty = true;
    });
    el.addEventListener('change', () => {
      state.globalSettingsDirty = true;
    });
  }
  const save = root.querySelector('#btn-save-routine-settings');
  if (save) {
    save.addEventListener('click', () =>
      guard('定常業務設定の保存', () => saveGlobalSettingsSection('routine')));
  }
  const add = root.querySelector('#btn-settings-cowork-add-root');
  if (add) add.addEventListener('click', () => addCoworkRoot());
  for (const btn of root.querySelectorAll('[data-drop-cowork-root]')) {
    btn.addEventListener('click', () => dropCoworkRoot(btn.dataset.dropCoworkRoot));
  }
  const open = root.querySelector('#btn-settings-cowork-open');
  if (open) open.addEventListener('click', openCoworkFromSettings);
}
