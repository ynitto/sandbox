'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// 利用状況領域（agent-audit）
// ---------------------------------------------------------------------------
//
// 以前は全体設定の「利用状況」節だった。**プロジェクトごとの話ではない**から独立タブを
// 持たない、という判断だったが、タブがプロジェクト単位でなくなり領域単位になった今、
// その理由は無くなった。トークン利用量と実行品質は agent-audit という 1 つの道具の面なので、
// 他の agent-* と同じく自分の領域を持つ。全体設定に残すのは、端末のすべてのワークロードに
// 効く設定（agent-control / 共通指示 / 予算 / 接続先）だけにする。
//
// 中身は features/agent-audit.js が registerGlobalSettingsPanel('usage', …) で差し込む面
// そのままで、この画面はその容れ物を置き換えただけ（面の実装は動かしていない）。

function renderUsage() {
  const pane = $('tab-usage');
  if (!pane) return;
  const ov = state.orchestration || {};
  pane.innerHTML = `<section class="usage-page">
    <header class="area-page-header">
      <h2>利用状況</h2>
      <p class="muted">この端末で使ったトークンと実行の品質を、収集済みの実行証跡から集計します。</p>
    </header>
    ${ov.error ? `<p class="muted">${esc(String(ov.error))}</p>` : ''}
    ${globalSettingsPanelsHtml('usage')}
  </section>`;
  wireGlobalSettingsPanels(pane);
  // 収集は CLI を起こすので、画面を開いたときだけ取りに行く（描き直すたびには走らせない）。
  if (activeTab() === 'usage') revealGlobalSettingsPanels(pane, 'usage');
}
