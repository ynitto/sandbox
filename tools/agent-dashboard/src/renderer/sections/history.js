'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// タブ: 履歴（成果）＋ Phase5 知識裁定（一画面）
// ---------------------------------------------------------------------------

function knowledgeRulesHtml(rules) {
  const list = Array.isArray(rules) ? rules : [];
  if (!list.length) {
    return '<div class="empty compact">ルール候補はまだありません。</div>';
  }
  const rows = list.map((r) => {
    const ev = r.evidenceOk ? '根拠あり' : '根拠なし';
    const conflict = r.conflict ? ' <span class="need-error">競合</span>' : '';
    const outcomes = `PASS ${r.pass || 0} / fail ${r.fail || 0} / suppressed ${r.suppressed || 0}`;
    const rid = esc(r.ruleId || '');
    return `<tr data-rule-id="${rid}">
      <td class="mono">${rid}</td>
      <td>${esc(r.state || '')}${conflict}</td>
      <td>${esc(outcomes)}（hit ${esc(String(r.hits || 0))}）</td>
      <td>${esc(ev)}</td>
      <td class="rule-actions">
        <button type="button" data-rule-act="rule-promote" data-rule-id="${rid}">promote</button>
        <button type="button" data-rule-act="rule-suspend" data-rule-id="${rid}">suspend</button>
        <button type="button" data-rule-act="rule-revise" data-rule-id="${rid}">revise</button>
        <button type="button" data-rule-act="rule-deprecate" data-rule-id="${rid}">deprecate</button>
      </td>
    </tr>`;
  }).join('');
  return `<div class="table-scroll"><table class="list">
    <tr><th>rule</th><th>状態</th><th>適用・outcome</th><th>根拠</th><th>裁定</th></tr>
    ${rows}
  </table></div>
  <p class="muted">操作は commands/ への契約投函のみです（dashboard は第二の書き手になりません）。</p>`;
}

function bindKnowledgeRuleActions(root, p) {
  for (const btn of root.querySelectorAll('button[data-rule-act]')) {
    btn.addEventListener('click', async () => {
      const action = btn.dataset.ruleAct;
      const ruleId = btn.dataset.ruleId;
      if (!action || !ruleId || !p) return;
      const reason = window.prompt(
        `${action.replace(/^rule-/, '')} の理由（決定記録に残ります）`,
        'agent-dashboard から裁定'
      );
      if (reason == null) return;
      const ok = await guard('ルール裁定', async () => {
        const res = await api.runAction({
          dir: p.dir, action, id: ruleId, ruleId, reason: String(reason || '').trim(),
        });
        uiLog('rule-command', action, ruleId, res);
        toast(`${action} を投函しました（本体が取り込みます）`, true);
        return true;
      });
      if (ok) await reloadProject();
    });
  }
}

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
    <section class="content-section" aria-label="知識ルール裁定">
      <h3>知識・ルール（裁定）</h3>
      <p class="muted">provenance / 適用数 / PASS·fail·rollback(suppressed) / 競合 / 状態遷移を一画面に集約します。</p>
      ${knowledgeRulesHtml(p.knowledgeRules)}
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
  bindKnowledgeRuleActions(el, p);
}
