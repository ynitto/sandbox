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
// 見出しは領域ヘッダーとタブが担うので、ペインは中身から始める。
function renderRoutineRuns() {
  const pane = $('tab-routine-runs');
  if (!pane) return;
  const folder = selectedProjectFolder();
  const entries = coworkItemsForFolder(folder);
  const selected = state.coworkHistory ? state.coworkHistory.id : '';
  if (!entries.length) {
    pane.innerHTML = `<section class="routine-page">
      <div class="empty compact">まだ作業がありません。「作業」タブから追加すると、動かした記録をここで読めます。</div>
    </section>`;
    return;
  }
  // 選択 UI は作業タブと同じ部品を使う。以前はここだけ独自の行を並べていて、作業タブの
  // カード（名前 ＋ 状態 ＋ 予定 ＋ 最終実行）と形が揃わず、同じ「作業を選ぶ」操作なのに
  // 別の画面に見えていた（検索も件数も無かった）。
  const picker = coworkRoutineSelectorHtml(
    entries.map((item, index) => ({ item, index })),
    selected,
    '記録を見る作業',
    'routine-runs-picker-label',
    'routine-runs'
  );
  pane.innerHTML = `<section class="routine-page">
    <div class="cowork-split-view">
      <section class="cowork-list-pane">${picker}</section>
      <section class="cowork-detail-pane" id="routine-runs-body">${coworkHistoryBodyHtml(state.coworkHistory)}</section>
    </div>
  </section>`;
  bindCoworkRoutineSelector(pane, (id) => {
    const item = entries.find((x) => x.id === id);
    loadCoworkHistory(id, item ? item.name : '');
  });
  bindCoworkHistoryBody(pane);
}

// 定常業務の設定タブ。全体設定にあった「定常業務」節をそのまま領域の中へ移した。
// HTML の組み立ては元の関数（globalSettingsRoutineHtml）を使い回し、配線だけこちらが持つ
// （カード自身が見出しを持つので、ペイン側の見出しは足さない）。
function renderRoutineSettings() {
  const pane = $('tab-routine-settings');
  if (!pane) return;
  pane.innerHTML = `<section class="routine-page">${globalSettingsRoutineHtml()}</section>`;
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
