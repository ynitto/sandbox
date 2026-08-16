'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// 知識領域（記憶 3 層 + moltbook の可視化・quiet 運転の承認キュー）
// ---------------------------------------------------------------------------
//
// 数字はこの端末のもの（プロジェクトごとではない）なので、利用状況と同じ理由で
// 独立した領域を持つ（全体設定へは置かない）。中身は features/agent-knowledge.js が
// registerGlobalSettingsPanel('knowledge', …) で差し込む面そのままで、この画面は
// その容れ物を置き換えただけ。
//
// 計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md §3.4（K3）

function renderKnowledge() {
  const pane = $('tab-knowledge');
  if (!pane) return;
  pane.innerHTML = `<section class="usage-page">
    ${globalSettingsPanelsHtml('knowledge')}
  </section>`;
  wireGlobalSettingsPanels(pane);
  if (activeTab() === 'knowledge') revealGlobalSettingsPanels(pane, 'knowledge');
}

// 承認キューの設定は別タブ（見る画面と決める画面を分ける、利用状況と同じ形）。
function renderKnowledgeSettings() {
  const pane = $('tab-knowledge-settings');
  if (!pane) return;
  pane.innerHTML = `<section class="usage-page">${globalSettingsPanelsHtml('knowledge-settings')}</section>`;
  wireGlobalSettingsPanels(pane);
}
