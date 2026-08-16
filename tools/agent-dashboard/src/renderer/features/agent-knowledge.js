'use strict';

// 知識（記憶 3 層の可視化・quiet 運転の承認キュー）— 「知識」領域の面。
//
// 計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md §3.4（K3）
//
// **数字はここが 1 か所で出す。** 記憶 3 層（persona/ltm/wiki）+ moltbook の集計は
// agent-audit の `report --kind knowledge --json` をそのまま描くだけで、この画面では
// 再集計しない（C7: 同じ判断の根拠を 2 か所に置かない）。改善タスク（`agent-audit tasks`）
// も読み取りのみ——agent-project への取り込みはこの画面からは行わない。
//
// この feature が固有に持つのは**承認キュー**だけ: quiet 運転で reply --autonomous が
// ブロックされたときに置かれた下書き（moltbook-use の outbox/drafts/）を、本文・
// privacy gate の判定を 1 画面に揃えて見せ、承認/却下は 1 ボタンで済ませる（C4）。
// AI はここでは何も生成しない——下書きの本文生成は「Moltbook 当番」（agent-loop の
// 定期プロンプト）が済ませたものをそのまま見せるだけで、この画面のアシスタントは
// 増やさない。
(function expose(root, factory) {
  const feature = factory(root);
  if (typeof module !== 'undefined' && module.exports) module.exports = feature;
  if (typeof root.registerGlobalSettingsPanel === 'function') {
    root.registerGlobalSettingsPanel('knowledge', {
      id: 'agent-knowledge',
      html: feature.panelHtml,
      wire: feature.wire,
      reveal: feature.reveal,
      refresh: feature.refresh,
    });
    root.registerGlobalSettingsPanel('knowledge-settings', {
      id: 'agent-knowledge-settings',
      html: feature.settingsPanelHtml,
      wire: feature.wire,
    });
  }
  if (typeof root.registerPortalCard === 'function') {
    root.registerPortalCard('knowledge', { order: 55, html: feature.portalCardHtml });
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, (root) => {
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const escHtml = (value) => String(value == null ? '' : value)
    .replace(/[&<>"']/g, (char) => ESC[char]);

  let knowledgeData = null;
  let tasksData = null;
  let draftsData = null;       // { configured, drafts }
  let loadedOnce = false;
  let loading = false;
  let loadError = '';
  let draftBusyFile = '';      // 承認/却下を実行中のファイル名（多重クリック防止）
  let draftMessage = null;
  let settingsDraft = null;
  let settingsMessage = null;

  function knowledgeConfig() {
    const appState = root.state || {};
    return (appState.config && appState.config.agentKnowledge) || {};
  }

  function fmtWhen(iso) {
    if (!iso) return '';
    const t = new Date(iso);
    return Number.isNaN(t.getTime()) ? String(iso) : t.toLocaleString();
  }

  function badgeHtml(kind, text) {
    return `<span class="orch-badge orch-badge-${escHtml(kind)}">${escHtml(text)}</span>`;
  }

  function summaryGridHtml(items) {
    const body = items.map(([label, value]) =>
      `<div><span>${escHtml(label)}</span><strong>${escHtml(value)}</strong></div>`).join('');
    return `<div class="orch-usage-summary">${body}</div>`;
  }

  // --- 3 層 + moltbook の集計（読み取りのみ） ---------------------------------

  function ltmSectionHtml(layer) {
    if (!layer || !layer.configured) {
      return `<p class="muted">ltm: ${escHtml((layer && layer.status) || '未収集（memory_stores.ltm_dirs が未設定）')}</p>`;
    }
    const stores = layer.stores || [];
    if (!stores.length) return '<p class="muted">ltm: 記憶ファイルがありません。</p>';
    return stores.map((s) => `
      <div class="knowledge-store">
        <h4><code>${escHtml(s.path)}</code></h4>
        ${summaryGridHtml([
          ['記憶件数', `${s.total} 件（active ${s.active} / archived ${s.archived}）`],
          ['publish 待ち', `${s.publish_waiting} 件`],
          ['忘却リスク', `${s.forgetting_risk} 件`],
          ['退役候補', `${s.dormant} 件${s.dormant ? `（最古 ${s.dormant_oldest_days} 日）` : ''}`],
          ['類似クラスタ', `${s.similar_clusters} 塊（${s.similar_clustered} 件）`],
        ])}
        ${s.index_drift === null || s.index_drift === undefined
          ? '<p class="muted">索引: 未構築</p>'
          : s.index_drift ? `<p>${badgeHtml('soft', `索引乖離 ${s.index_drift} 件`)}</p>` : ''}
        ${(s.notes || []).map((n) => `<p class="muted">${escHtml(n)}</p>`).join('')}
      </div>`).join('');
  }

  function wikiSectionHtml(layer) {
    if (!layer || !layer.configured) {
      return `<p class="muted">wiki: ${escHtml((layer && layer.status) || '未収集（memory_stores.wiki_root が未設定）')}</p>`;
    }
    const stores = layer.stores || [];
    if (!stores.length) return '<p class="muted">wiki: ページがありません。</p>';
    return stores.map((s) => {
      const rate = s.queries_hit_rate === null || s.queries_hit_rate === undefined
        ? '—' : `${(s.queries_hit_rate * 100).toFixed(0)}%`;
      return `
      <div class="knowledge-store">
        <h4><code>${escHtml(s.path)}</code></h4>
        ${summaryGridHtml([
          ['ページ数', `atoms ${s.atoms} / topics ${s.topics}`],
          ['直近7日', `新規 ${s.new_last_7d} / 更新 ${s.updated_last_7d}`],
          ['index 乖離', `${s.index_drift} 件`],
          ['lint 違反', `${s.lint_violations} 件`],
          ['queries ヒット率', rate],
        ])}
        ${(s.notes || []).map((n) => `<p class="muted">${escHtml(n)}</p>`).join('')}
      </div>`;
    }).join('');
  }

  function personaSectionHtml(layer) {
    if (!layer || !layer.configured) {
      return `<p class="muted">persona: ${escHtml((layer && layer.status) || '未収集（memory_stores.persona_home が未設定）')}</p>`;
    }
    const stores = layer.stores || [];
    if (!stores.length) return '';
    // persona は件数と滞留日数だけ（本文・タイトルは agent-audit 側から出ない。C1）。
    return stores.map((s) => `
      <div class="knowledge-store">
        <h4><code>${escHtml(s.path)}</code></h4>
        ${summaryGridHtml([
          ['未反映の観察ログ', `${s.pending_updates} 件`],
          ['最古', s.pending_oldest_days ? `${s.pending_oldest_days} 日` : '—'],
          ['管理ファイル', `${s.managed_files}/3`],
        ])}
      </div>`).join('');
  }

  function moltbookSectionHtml(layer) {
    if (!layer || !layer.configured) {
      return `<p class="muted">moltbook: ${escHtml((layer && layer.status) || '未収集（memory_stores.moltbook_home が未設定）')}</p>`;
    }
    const stores = layer.stores || [];
    if (!stores.length) return '';
    return stores.map((s) => `
      <div class="knowledge-store">
        <h4><code>${escHtml(s.path)}</code></h4>
        ${summaryGridHtml([
          ['outbox 滞留', `${s.outbox_pending} 件${s.outbox_pending ? `（最古 ${s.outbox_oldest_days} 日）` : ''}`],
          ['公開済み（ローカル）', `${s.published_local} 件`],
          ['inbox 未取込', `${s.inbox_pending} 件`],
        ])}
        ${(s.uncollected || []).length
          ? `<p class="muted">未収集（ローカルからは測れません）: ${escHtml(s.uncollected.join('・'))}</p>` : ''}
      </div>`).join('');
  }

  function knowledgeSummaryHtml() {
    if (!knowledgeData) return loading ? '' : '<p class="muted">「表示を更新」を押してください。</p>';
    const layers = knowledgeData.layers || {};
    return `
      <section class="orch-panel knowledge-layer">
        <header class="row"><h3>ltm（記憶）</h3></header>
        ${ltmSectionHtml(layers.ltm)}
      </section>
      <section class="orch-panel knowledge-layer">
        <header class="row"><h3>wiki</h3></header>
        ${wikiSectionHtml(layers.wiki)}
      </section>
      <section class="orch-panel knowledge-layer">
        <header class="row"><h3>persona</h3></header>
        ${personaSectionHtml(layers.persona)}
      </section>
      <section class="orch-panel knowledge-layer">
        <header class="row"><h3>moltbook（共有）</h3></header>
        ${moltbookSectionHtml(layers.moltbook)}
      </section>`;
  }

  // --- 整理タスク（agent-audit tasks・読み取りのみ） --------------------------

  function tasksTableHtml() {
    const rows = tasksData || [];
    if (!rows.length) {
      return '<p class="muted">整理タスクはありません（extract → distill を回すと出てきます）。</p>';
    }
    const body = rows.map((t) => `<tr>
      <td><code>${escHtml(t.id || '')}</code></td>
      <td>${escHtml(t.title || '')}</td>
      <td>${escHtml(t.desc || '')}</td>
      <td class="muted">${escHtml(t.why || '')}</td>
    </tr>`).join('');
    return `<div class="table-scroll"><table class="list audit-table">
      <thead><tr><th>id</th><th>タイトル</th><th>提案</th><th>根拠</th></tr></thead>
      <tbody>${body}</tbody>
    </table></div>
    <p class="muted">取り込みは agent-project 側の intake の仕事です（この画面からは投入しません）。</p>`;
  }

  // --- quiet 運転の承認キュー ---------------------------------------------------

  function draftRowHtml(draft) {
    const gate = draft.gate || {};
    const busy = draftBusyFile === draft.file;
    const flag = gate.allowed ? badgeHtml('ok', 'ALLOW') : badgeHtml('over', 'BLOCK');
    return `<div class="knowledge-draft" data-draft-file="${escHtml(draft.file)}">
      <header class="row">
        <span>#${escHtml(draft.iid)}（${escHtml(draft.author)}・${escHtml(draft.created)}）</span>
        ${flag}
      </header>
      <p class="knowledge-draft-body">${escHtml(draft.body)}</p>
      <p class="muted">${escHtml(gate.summary || '')}</p>
      <div class="row">
        <button type="button" class="primary-inline knowledge-draft-approve"${busy || !gate.allowed ? ' disabled' : ''}
          title="${gate.allowed ? '' : 'privacy gate に flag されています。却下するか本文を直してください。'}">
          ${busy ? '処理しています…' : '承認'}
        </button>
        <button type="button" class="knowledge-draft-discard"${busy ? ' disabled' : ''}>却下</button>
      </div>
    </div>`;
  }

  function draftsQueueHtml() {
    if (!draftsData || !draftsData.configured) {
      return '<p class="muted">moltbook-use が設定されていません（「知識 → 設定」で承認キューのコマンドを指定してください）。</p>';
    }
    const rows = draftsData.drafts || [];
    if (!rows.length) return '<p class="muted">承認待ちの下書きはありません。</p>';
    return rows.map(draftRowHtml).join('');
  }

  // --- 全体の面 ------------------------------------------------------------

  function panelHtml() {
    return `<section class="orch-panel knowledge-header" aria-labelledby="knowledge-title">
      <header class="row">
        <h3 id="knowledge-title">記憶と共有</h3>
        <div class="audit-actions">
          <button type="button" id="knowledge-collect" class="primary-inline">収集</button>
          <button type="button" id="knowledge-reload"${loading ? ' disabled' : ''}>表示を更新</button>
        </div>
      </header>
      <p class="muted">記憶 3 層（persona / ltm / wiki）と moltbook（共有）の健全性です。集計は agent-audit
        （LLM 不使用）が正で、この画面は描画だけを行います。</p>
      ${loadError ? `<p class="audit-error" role="alert">${escHtml(loadError)}</p>` : ''}
      ${loading ? '<p class="muted" role="status">集計しています…</p>' : ''}
    </section>
    ${knowledgeSummaryHtml()}
    <section class="orch-panel knowledge-tasks">
      <header class="row"><h3>整理タスク</h3></header>
      ${tasksTableHtml()}
    </section>
    <section class="orch-panel knowledge-queue" aria-labelledby="knowledge-queue-title">
      <header class="row"><h3 id="knowledge-queue-title">承認キュー（quiet 運転の返信下書き）</h3></header>
      <p class="muted">reply_mode=quiet で自律返信がブロックされた下書きです。承認しても
        送信直前にもう一度 privacy gate を通ります——承認は gate の代わりにはなりません。</p>
      ${draftMessage ? `<p class="${draftMessage.ok ? 'muted' : 'audit-error'}" role="status">${escHtml(draftMessage.text)}</p>` : ''}
      ${draftsQueueHtml()}
    </section>`;
  }

  function settingsHtml(values) {
    const v = values || {};
    return `<section class="orch-panel knowledge-settings">
      <header class="row"><h3>承認キューの設定</h3></header>
      <p class="muted">quiet 運転の返信下書き（moltbook-use）の一覧・承認・却下に使うコマンドです。
        空のままなら承認キューは出ません（記憶 3 層の集計には影響しません）。</p>
      <div class="field">
        <label for="knowledge-set-command">起動コマンド</label>
        <input id="knowledge-set-command" type="text" value="${escHtml(v.draftsCommand || '')}"
          placeholder="例: python3 ~/repo/.github/skills/moltbook-use/scripts/moltbook_drafts.py">
      </div>
      <div class="field">
        <label for="knowledge-set-distro">WSL ディストロ</label>
        <input id="knowledge-set-distro" type="text" value="${escHtml(v.distro || '')}"
          placeholder="空なら既定のディストロで実行します">
      </div>
      <div class="field">
        <label for="knowledge-set-drafts-dir">下書きディレクトリ</label>
        <input id="knowledge-set-drafts-dir" type="text" value="${escHtml(v.draftsDir || '')}"
          placeholder="空なら {agent_home}/.moltbook/outbox/drafts">
      </div>
      ${settingsMessage ? `<p class="${settingsMessage.ok ? 'muted' : 'audit-error'}" role="status">${escHtml(settingsMessage.text)}</p>` : ''}
      <div class="settings-save-actions">
        <button type="button" id="knowledge-save" class="primary-inline">保存</button>
      </div>
    </section>`;
  }

  function settingsPanelHtml() {
    return settingsHtml(settingsDraft || knowledgeConfig());
  }

  async function loadData() {
    if (!root.api || !root.api.agentAuditKnowledge || loading) return;
    loading = true;
    loadError = '';
    render();
    try {
      const [knowledgeResult, tasksResult, draftsResult] = await Promise.all([
        root.api.agentAuditKnowledge({}),
        root.api.agentAuditTasks ? root.api.agentAuditTasks({}) : Promise.resolve([]),
        root.api.agentKnowledgeDraftsList ? root.api.agentKnowledgeDraftsList({})
          : Promise.resolve({ configured: false, drafts: [] }),
      ]);
      knowledgeData = knowledgeResult;
      tasksData = tasksResult;
      draftsData = draftsResult;
      loadedOnce = true;
    } catch (error) {
      knowledgeData = null;
      loadError = error && error.message ? error.message : String(error);
    }
    loading = false;
    render();
  }

  async function runCollect() {
    if (!root.api || !root.api.agentAuditCollect) return;
    try {
      const result = await root.api.agentAuditCollect({});
      if (result && result.ok) {
        loadedOnce = false;
        await loadData();
      }
    } catch {
      /* 収集の詳細は「利用状況」タブが持つ。ここでは再取得の失敗として loadError に出る */
    }
  }

  async function actOnDraft(file, action, reason) {
    if (draftBusyFile) return;
    draftBusyFile = file;
    draftMessage = null;
    render();
    try {
      if (action === 'approve') {
        await root.api.agentKnowledgeDraftsApprove({ file });
        draftMessage = { ok: true, text: `承認しました: ${file}` };
      } else {
        await root.api.agentKnowledgeDraftsDiscard({ file, reason: reason || '' });
        draftMessage = { ok: true, text: `却下しました: ${file}` };
      }
      draftsData = await root.api.agentKnowledgeDraftsList({});
    } catch (error) {
      draftMessage = { ok: false, text: error && error.message ? error.message : String(error) };
    }
    draftBusyFile = '';
    render();
  }

  function readSettingsForm(pane) {
    const value = (id) => {
      const input = pane.querySelector(`#${id}`);
      return input ? input.value.trim() : '';
    };
    return {
      draftsCommand: value('knowledge-set-command'),
      distro: value('knowledge-set-distro'),
      draftsDir: value('knowledge-set-drafts-dir'),
    };
  }

  async function saveSettings(pane) {
    if (!root.api || !root.api.saveConfig) return;
    const values = readSettingsForm(pane);
    const appState = root.state || {};
    const cfg = JSON.parse(JSON.stringify(appState.config || {}));
    cfg.agentKnowledge = { ...(cfg.agentKnowledge || {}), ...values };
    try {
      appState.config = await root.api.saveConfig(cfg);
      settingsDraft = null;
      settingsMessage = { ok: true, text: '設定を保存しました' };
      loadedOnce = false;
    } catch (error) {
      settingsMessage = { ok: false, text: error && error.message ? error.message : String(error) };
    }
    render();
    if (!settingsMessage || settingsMessage.ok) await loadData();
  }

  function visible(container) {
    if (!container || !root.document || !container.closest) return false;
    const settingsPane = container.closest('.global-settings-pane');
    if (settingsPane && settingsPane.hidden) return false;
    const tabpane = container.closest('.tabpane');
    return Boolean(tabpane && tabpane.classList.contains('active'));
  }

  function wire(pane) {
    const on = (id, event, fn) => {
      const element = pane.querySelector(`#${id}`);
      if (element) element.addEventListener(event, fn);
    };
    on('knowledge-collect', 'click', runCollect);
    on('knowledge-reload', 'click', () => loadData());
    on('knowledge-save', 'click', () => saveSettings(pane));
    for (const input of pane.querySelectorAll('.knowledge-settings input')) {
      input.addEventListener('input', () => {
        settingsDraft = readSettingsForm(pane);
        settingsMessage = null;
      });
    }
    for (const el of pane.querySelectorAll('.knowledge-draft')) {
      const file = el.dataset.draftFile;
      const approveBtn = el.querySelector('.knowledge-draft-approve');
      const discardBtn = el.querySelector('.knowledge-draft-discard');
      if (approveBtn) approveBtn.addEventListener('click', () => actOnDraft(file, 'approve'));
      if (discardBtn) discardBtn.addEventListener('click', () => {
        const reason = root.prompt ? root.prompt('却下理由（任意）') : '';
        actOnDraft(file, 'discard', reason || '');
      });
    }
  }

  function maybeRender() {
    if (!root.document) return;
    const activeElement = root.document.activeElement;
    if (activeElement && (activeElement.tagName === 'INPUT' || activeElement.tagName === 'TEXTAREA')) return;
    render();
  }

  function render() {
    if (!root.document) return;
    for (const [id, html] of [['agent-knowledge', panelHtml], ['agent-knowledge-settings', settingsPanelHtml]]) {
      const container = root.document.getElementById(`global-settings-slot-${id}`);
      if (!container) continue;
      container.innerHTML = html();
      wire(container);
    }
  }

  function reveal(container) {
    if (!visible(container)) return;
    if (!loadedOnce && !loading && !loadError) loadData();
  }

  // 全体ポーリングからは能動的に叩かない——承認キューはファイル移動を伴うので、
  // 開いていない間に裏で状態が動くと、表示と実体がずれる（agent-audit の usage も
  // 同じ理由でタブを開いたときだけ再取得する）。
  function refresh() {}

  // ホーム（ポータル）への入口だけを担うカード。数字はここに出さない——記憶と共有の
  // 数字は「知識」領域の 1 か所で出す（agent-audit の usage カードと同じ約束。C7）。
  // 領域を開く前でも常に出す（利用状況カードと同じく、取得済みかどうかで出し分けない）。
  function portalCardHtml() {
    return `<div class="portal-card-heading">
        <span class="summary-kicker">保存したのに眠っていないか</span>
        <h3>記憶と共有</h3>
      </div>
      <p class="portal-card-count">記憶3層（persona/ltm/wiki）とMoltbook共有の状況</p>
      <div class="portal-card-actions">
        <button type="button" class="primary-inline" data-portal-area="knowledge">開く</button>
      </div>`;
  }

  return {
    escHtml, panelHtml, settingsHtml, settingsPanelHtml,
    ltmSectionHtml, wikiSectionHtml, personaSectionHtml, moltbookSectionHtml,
    tasksTableHtml, draftRowHtml, draftsQueueHtml,
    render, refresh, wire, reveal, portalCardHtml,
    loadData, actOnDraft, runCollect,
  };
});
