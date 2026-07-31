'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// タブ: 履歴
// ---------------------------------------------------------------------------

function renderHistory() {
  const p = state.project;
  const el = $('tab-history');
  if (!p) {
    el.innerHTML = '';
    return;
  }
  const hasDecisions = p.decisions.length > 0;
  const deliveryRows = [...p.delivery]
    .reverse()
    .map((cells) => `<tr>${cells.map((c) => `<td>${linkify(c)}</td>`).join('')}</tr>`)
    .join('');

  el.innerHTML = `<div class="history-shell">
    <header class="page-heading">
      <div><span class="summary-kicker">完了したこと</span><h2>成果</h2></div>
      <button type="button" class="subtle-action" data-open-technical-info>内部ログを開く</button>
    </header>
    <section class="content-section">
      <h3>納品物</h3>
      ${deliveryRows ? `<div class="table-scroll"><table class="list">${deliveryRows}</table></div>` : '<div class="empty compact">まだ成果はありません。</div>'}
    </section>
    <section class="content-section">
      <h3>判断の記録</h3>
      ${hasDecisions
        ? `<div class="table-scroll"><table class="list"><tr><th>日付</th><th>タスク</th><th>操作</th><th>理由</th></tr>${p.decisions.map((d) => `<tr><td>${esc(d.date)}</td><td class="mono">${esc(d.taskId)}</td><td>${esc(d.fields.action || '')}</td><td>${esc(d.fields.reason || d.fields.context || '')}</td></tr>`).join('')}</table></div>`
        : '<div class="empty compact">判断の記録はありません。</div>'}
    </section>
  </div>`;
  const technicalInfo = el.querySelector('[data-open-technical-info]');
  if (technicalInfo) technicalInfo.addEventListener('click', openTechnicalInfo);
}
