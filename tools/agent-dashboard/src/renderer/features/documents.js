'use strict';

// ドキュメント領域 — 文書ルールに沿ってエージェント CLI に文書を作らせる画面。
//
// タブは 3 つ（他の領域と同じ「見る／動かす」と「決める」の分け方）:
//   文書       … 文書の一覧と詳細（成果物・改訂履歴）。作成・続き・検証・フィードバックの入口
//   文書ルール … ルールファイルの一覧と本文。作成（原案を AI が膨らませる）と編集
//   設定       … 置き場（文書フォルダ・ルールフォルダ）と、使うエージェントの確認
//
// 作成・続き・検証は**外部ターミナルの対話セッション**で進む（質問に答える・指摘を選ぶのは
// 人との対話が本体）。この画面はその入口と、結果（フォルダの成果物・サイドカーの履歴）の
// 読み取りだけを持つ。ルールの下書きだけはヘッドレスの助言で、本文を人が編集して保存する。
(function expose(root, factory) {
  const feature = factory(root);
  if (typeof module !== 'undefined' && module.exports) module.exports = feature;
  if (typeof root.registerFeatureTab === 'function') {
    root.registerFeatureTab('documents', {
      refresh: feature.refresh,
      available: () => true,
      open: () => 'document-list',
    });
    // 各タブは自分のペインだけを描く（コアは登録名ごとに render を呼ぶ）。
    root.registerFeatureTab('document-list', { render: () => feature.renderTab('document-list'), available: () => true });
    root.registerFeatureTab('document-rules', { render: () => feature.renderTab('document-rules'), available: () => true });
    root.registerFeatureTab('document-settings', { render: () => feature.renderTab('document-settings'), available: () => true });
  }
  if (typeof root.registerPortalCard === 'function') {
    root.registerPortalCard('documents', { order: 35, html: feature.portalCardHtml });
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, (root) => {
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ESC[c]);
  const $ = (id) => document.getElementById(id);
  const api = () => root.api || {};
  const guard = (label, fn) => (typeof root.guard === 'function' ? root.guard(label, fn) : fn());
  const toast = (msg, ok) => (typeof root.toast === 'function' ? root.toast(msg, ok) : undefined);
  // renderer.js の state は const（グローバルオブジェクトに載らない）。同じスコープの
  // 古典スクリプトからは名前で届く。テスト（Node）では root.state を見る。
  const appState = () => (typeof state !== 'undefined' ? state : (root.state || {}));

  // 操作名・進め方の表示名は main（documents.js の表）が正典。overview で受け取る。
  const catalogLabel = (list, id) => {
    const row = ((st.overview && st.overview[list]) || []).find((x) => (x.kind || x.id) === id);
    return row ? row.label : String(id || '');
  };
  const modeLabel = (id) => catalogLabel('modes', id);
  const actionLabel = (id) => catalogLabel('actions', id);

  const st = {
    overview: null,
    loaded: false,
    error: '',
    selected: '',
    detail: null,
    detailError: '',
    ruleSelected: '',
    rule: null,
    busy: '',
    notice: null,        // { text, ok }
    settings: null,      // { workspaceDir, rulesDir }（未保存の入力）
    createInputs: [],    // 作成ダイアログで選んだ入力ファイルのパス
    editor: null,        // { file, name, kind }（ルール編集ダイアログの対象）
    wired: false,
  };

  function fmtWhen(iso) {
    if (!iso) return '';
    const t = new Date(iso);
    if (Number.isNaN(t.getTime())) return String(iso);
    return typeof root.fmtTime === 'function' ? root.fmtTime(iso) : t.toLocaleString();
  }

  function fmtSize(bytes) {
    const n = Number(bytes) || 0;
    if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    if (n >= 1024) return `${Math.round(n / 1024)} KB`;
    return `${n} B`;
  }

  function formatLabel(id) {
    const row = ((st.overview && st.overview.formats) || []).find((f) => f.id === id);
    return row ? row.label : String(id || '');
  }

  function prose(text) {
    const src = String(text || '');
    if (!src.trim()) return '';
    if (typeof root.proseHtml === 'function') return root.proseHtml(src);
    return `<pre class="docs-pre">${esc(src)}</pre>`;
  }

  function noticeHtml() {
    if (st.busy) return `<p class="docs-notice" role="status">${esc(st.busy)}</p>`;
    if (!st.notice) return '';
    return `<p class="docs-notice ${st.notice.ok ? 'is-ok' : 'is-error'}" role="${st.notice.ok ? 'status' : 'alert'}">${esc(st.notice.text)}</p>`;
  }

  function setNotice(text, ok) {
    st.notice = text ? { text, ok: !!ok } : null;
    render();
  }

  // -------------------------------------------------------------------------
  // 取得
  // -------------------------------------------------------------------------

  async function refresh() {
    if (!api().documentsOverview) return;
    try {
      st.overview = await api().documentsOverview();
      st.error = '';
      st.loaded = true;
    } catch (e) {
      st.error = String((e && e.message) || e);
      st.loaded = true;
    }
    if (st.selected && !(st.overview && st.overview.sets.some((s) => s.id === st.selected))) {
      st.selected = '';
      st.detail = null;
    }
    if (st.selected) await loadDetail(st.selected, { silent: true });
    if (st.ruleSelected && !(st.overview && st.overview.rules.some((r) => r.file === st.ruleSelected))) {
      st.ruleSelected = '';
      st.rule = null;
    }
    render();
  }

  async function loadDetail(id, { silent = false } = {}) {
    st.selected = id;
    if (!silent) {
      st.detail = null;
      st.detailError = '';
      render();
    }
    try {
      st.detail = await api().documentsGet({ id });
      st.detailError = '';
    } catch (e) {
      st.detail = null;
      st.detailError = String((e && e.message) || e);
    }
    render();
  }

  async function loadRule(file) {
    st.ruleSelected = file;
    st.rule = null;
    render();
    try {
      st.rule = await api().documentsRuleRead({ file });
    } catch (e) {
      st.rule = { error: String((e && e.message) || e), file };
    }
    render();
  }

  // -------------------------------------------------------------------------
  // 文書タブ
  // -------------------------------------------------------------------------

  function agentLine() {
    const a = st.overview && st.overview.agent;
    if (!a) return '';
    const model = a.model ? `（${a.model}）` : '';
    const how = a.interactive ? '対話セッション' : '単発実行';
    return `<p class="muted docs-agent-line">作成と検証は <strong>${esc(a.cli)}</strong>${esc(model)} を${how}で起動します。変えるには全体設定の実行制御で「文書」の担当を指定します。</p>`;
  }

  function setItemHtml(s) {
    const on = s.id === st.selected;
    const formats = (s.formats || []).map((f) => `<span class="label-chip">${esc(formatLabel(f))}</span>`).join('');
    const last = s.lastAction ? `${actionLabel(s.lastAction.kind)} ${fmtWhen(s.lastAction.at)}` : '';
    return `<button type="button" class="docs-list-item ${on ? 'active' : ''}" data-docs-set="${esc(s.id)}" aria-current="${on ? 'true' : 'false'}">
      <span class="docs-list-title">${esc(s.name)}</span>
      <span class="docs-list-meta">${formats}${s.outputCount ? `<span class="muted">成果物 ${s.outputCount}</span>` : '<span class="muted">成果物なし</span>'}</span>
      ${last ? `<span class="docs-list-sub muted">${esc(last)}</span>` : ''}
    </button>`;
  }

  function outputsHtml(outputs) {
    if (!(outputs || []).length) {
      return '<p class="muted">成果物はまだありません。作成のウィンドウで質問に答えると、このフォルダに書き出されます。</p>';
    }
    const rows = outputs.map((o) => `<tr>
      <td><button type="button" class="linklike" data-docs-open="${esc(o.path)}" title="開く">${esc(o.file)}</button></td>
      <td>${o.format ? `<span class="label-chip">${esc(formatLabel(o.format))}</span>` : '<span class="muted">その他</span>'}</td>
      <td>${esc(o.role || '')}${o.relatedTo && o.relatedTo.length ? `<div class="muted docs-relation">関連: ${esc(o.relatedTo.join(', '))}${o.relation ? ` — ${esc(o.relation)}` : ''}</div>` : (o.relation ? `<div class="muted docs-relation">${esc(o.relation)}</div>` : '')}</td>
      <td class="muted">${esc(fmtSize(o.size))}</td>
      <td class="muted">${esc(fmtWhen(o.updatedAt))}</td>
    </tr>`).join('');
    return `<div class="docs-table-wrap"><table class="docs-table">
      <thead><tr><th>ファイル</th><th>形式</th><th>役割と関係</th><th>サイズ</th><th>更新</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  }

  function detailHtml() {
    if (!st.selected) {
      return '<div class="empty compact"><strong>左の文書を選ぶと、成果物と改訂履歴を表示します。</strong><p>「文書を作る」から新しい文書を始められます。</p></div>';
    }
    if (st.detailError) return `<p class="docs-notice is-error" role="alert">${esc(st.detailError)}</p>`;
    const d = st.detail;
    if (!d) return '<p class="muted">読み込んでいます…</p>';
    const rule = d.rule
      ? `<button type="button" class="linklike" data-docs-rule-jump="${esc(d.rule.file)}">${esc(d.rule.name || d.rule.file)}</button>${d.rule.error ? ` <span class="docs-warn">（読めません: ${esc(d.rule.error)}）</span>` : ''}`
      : '<span class="muted">なし</span>';
    const inputs = (d.inputs || []).length
      ? `<ul class="docs-inputs">${d.inputs.map((it) => `<li><button type="button" class="linklike" data-docs-open="${esc(it.path)}">${esc(it.name)}</button></li>`).join('')}</ul>`
      : '<span class="muted">なし</span>';
    return `<div class="docs-detail">
      <div class="docs-detail-head">
        <div>
          <span class="summary-kicker">${esc(modeLabel(d.mode))}</span>
          <h3>${esc(d.name)}</h3>
          <p class="muted">${(d.formats || []).map((f) => `<span class="label-chip">${esc(formatLabel(f))}</span>`).join(' ')}</p>
        </div>
        <div class="docs-actions">
          <button type="button" class="primary-inline" data-docs-action="resume" ${st.busy ? 'disabled' : ''}>続きを依頼</button>
          <button type="button" data-docs-action="verify" ${st.busy ? 'disabled' : ''}>検証する</button>
          <button type="button" data-docs-action="feedback" ${st.busy ? 'disabled' : ''}>フィードバックしてルールにする</button>
          <button type="button" data-docs-action="rule-from-history" ${st.busy ? 'disabled' : ''}>改訂履歴からルールを起こす</button>
          <button type="button" data-docs-open="${esc(d.dir)}">フォルダを開く</button>
        </div>
      </div>
      <dl class="docs-facts">
        <dt>文書ルール</dt><dd>${rule}</dd>
        <dt>依頼内容</dt><dd>${d.request ? prose(d.request) : '<span class="muted">（入力ファイルから作成）</span>'}</dd>
        <dt>入力ファイル</dt><dd>${inputs}</dd>
        <dt>作成</dt><dd class="muted">${esc(fmtWhen(d.createdAt))}</dd>
      </dl>
      <section class="docs-section">
        <h4>成果物</h4>
        ${outputsHtml(d.outputs)}
      </section>
      <section class="docs-section">
        <h4>改訂履歴 <span class="muted docs-sidecar"><button type="button" class="linklike" data-docs-open="${esc(d.sidecar)}">${esc(d.id)}.history.md</button></span></h4>
        <p class="muted">変更・利用者の意図・指摘事項を時系列で残します。エージェントも同じファイルへ追記します。</p>
        <div class="docs-history">${d.history ? prose(d.history) : '<p class="muted">まだ記録がありません。</p>'}</div>
      </section>
    </div>`;
  }

  function listPaneHtml() {
    if (!st.loaded) return '<p class="muted">読み込んでいます…</p>';
    if (st.error) return `<p class="docs-notice is-error" role="alert">${esc(st.error)}</p>`;
    const ov = st.overview || { sets: [] };
    const items = (ov.sets || []).map(setItemHtml).join('');
    return `<section class="docs-shell">
      <div class="docs-toolbar">
        <button type="button" class="primary-inline" id="btn-docs-new" ${st.busy ? 'disabled' : ''}>文書を作る</button>
        <button type="button" data-docs-open="${esc(ov.workspaceDir)}">文書フォルダを開く</button>
        <span class="muted docs-toolbar-note">置き場: ${esc(ov.workspaceDir)}</span>
      </div>
      ${agentLine()}
      ${(ov.errors || []).map((e) => `<p class="docs-notice is-error">${esc(e)}</p>`).join('')}
      ${noticeHtml()}
      <div class="master-detail docs-master-detail">
        <div class="master-list docs-list">
          ${items || '<div class="empty compact"><strong>文書はまだありません</strong><p>「文書を作る」から始めます。</p></div>'}
        </div>
        <div class="detail-panel docs-detail-panel">${detailHtml()}</div>
      </div>
    </section>`;
  }

  // -------------------------------------------------------------------------
  // 文書ルールタブ
  // -------------------------------------------------------------------------

  function ruleItemHtml(r) {
    const on = r.file === st.ruleSelected;
    return `<button type="button" class="docs-list-item ${on ? 'active' : ''}" data-docs-rule="${esc(r.file)}" aria-current="${on ? 'true' : 'false'}">
      <span class="docs-list-title">${esc(r.name)}</span>
      <span class="docs-list-meta">${(r.formats || []).map((f) => `<span class="label-chip">${esc(formatLabel(f))}</span>`).join('')}<span class="muted">区分 ${r.divisions}</span></span>
      ${(r.missing || []).length ? `<span class="docs-list-sub docs-warn">未記入の節 ${r.missing.length}</span>` : ''}
    </button>`;
  }

  function ruleDetailHtml() {
    if (!st.ruleSelected) {
      return '<div class="empty compact"><strong>左のルールを選ぶと、本文を表示します。</strong><p>「ルールを作る」で原案から新しいルールを起こせます。</p></div>';
    }
    const r = st.rule;
    if (!r) return '<p class="muted">読み込んでいます…</p>';
    if (r.error) return `<p class="docs-notice is-error" role="alert">${esc(r.error)}</p>`;
    const sections = (st.overview && st.overview.sections) || [];
    const missing = (r.parsed && r.parsed.missing) || [];
    const missingLabels = missing.map((k) => (sections.find((s) => s.key === k) || {}).label || k);
    return `<div class="docs-detail">
      <div class="docs-detail-head">
        <div>
          <span class="summary-kicker">文書ルール</span>
          <h3>${esc(r.name)}</h3>
          <p class="muted">${(r.formats || []).map((f) => `<span class="label-chip">${esc(formatLabel(f))}</span>`).join(' ')} <span class="docs-file">${esc(r.file)}</span></p>
        </div>
        <div class="docs-actions">
          <button type="button" class="primary-inline" data-docs-rule-edit="${esc(r.file)}" ${st.busy ? 'disabled' : ''}>編集</button>
          <button type="button" data-docs-open="${esc(r.file)}">ファイルを開く</button>
        </div>
      </div>
      ${missingLabels.length ? `<p class="docs-warn">未記入の節: ${esc(missingLabels.join('、'))}</p>` : ''}
      <div class="docs-rule-body">${prose(r.content)}</div>
    </div>`;
  }

  function rulesPaneHtml() {
    if (!st.loaded) return '<p class="muted">読み込んでいます…</p>';
    if (st.error) return `<p class="docs-notice is-error" role="alert">${esc(st.error)}</p>`;
    const ov = st.overview || { rules: [] };
    const items = (ov.rules || []).map(ruleItemHtml).join('');
    return `<section class="docs-shell">
      <div class="docs-toolbar">
        <button type="button" class="primary-inline" id="btn-docs-rule-new" ${st.busy ? 'disabled' : ''}>ルールを作る</button>
        <button type="button" data-docs-open="${esc(ov.rulesDir)}">ルールのフォルダを開く</button>
        <span class="muted docs-toolbar-note">1 ルール = 1 ファイル（${esc(ov.rulesDir)}）。コピーや削除はフォルダで直接行います。</span>
      </div>
      ${noticeHtml()}
      <div class="master-detail docs-master-detail">
        <div class="master-list docs-list">
          ${items || '<div class="empty compact"><strong>文書ルールはまだありません</strong><p>原案やテンプレートを入力すると、AI が節ごとに膨らませます。</p></div>'}
        </div>
        <div class="detail-panel docs-detail-panel">${ruleDetailHtml()}</div>
      </div>
    </section>`;
  }

  // -------------------------------------------------------------------------
  // 設定タブ
  // -------------------------------------------------------------------------

  function settingsPaneHtml() {
    if (!st.loaded) return '<p class="muted">読み込んでいます…</p>';
    const ov = st.overview || {};
    const cfg = (appState().config || {}).documents || {};
    const draft = st.settings || { workspaceDir: cfg.workspaceDir || '', rulesDir: cfg.rulesDir || '' };
    return `<section class="docs-shell docs-settings">
      ${noticeHtml()}
      <div class="global-settings-card">
        <div class="global-settings-card-heading"><h3>置き場</h3>
          <p class="muted">空欄のときは既定の場所を使います。</p></div>
        <div class="field">
          <label for="docs-set-workspace">文書を置くフォルダ</label>
          <div class="docs-path-row">
            <input id="docs-set-workspace" value="${esc(draft.workspaceDir)}" placeholder="${esc(ov.workspaceDir || '')}" />
            <button type="button" data-docs-pick="workspace">選ぶ</button>
          </div>
          <small class="muted">1 文書 = 1 サブフォルダ。成果物・入力の写し・改訂履歴をここに置きます。</small>
        </div>
        <div class="field">
          <label for="docs-set-rules">文書ルールのフォルダ</label>
          <div class="docs-path-row">
            <input id="docs-set-rules" value="${esc(draft.rulesDir)}" placeholder="${esc(ov.rulesDir || '')}" />
            <button type="button" data-docs-pick="rules">選ぶ</button>
          </div>
          <small class="muted">1 ルール = 1 Markdown ファイル。コピー・削除はフォルダで直接行います。</small>
        </div>
        <div class="dialog-actions">
          <button type="button" class="primary-inline" id="btn-docs-settings-save" ${st.busy ? 'disabled' : ''}>保存</button>
        </div>
      </div>
      <div class="global-settings-card">
        <div class="global-settings-card-heading"><h3>使うエージェント</h3></div>
        ${agentLine() || '<p class="muted">エージェントを解決できませんでした。全体設定を確認してください。</p>'}
        <p class="muted">文書の作成と検証は文書フォルダの中だけを書き換える対話セッションで動きます。ルールの下書きは読み取り専用の助言として動き、保存は人が本文を確認してから行います。</p>
      </div>
    </section>`;
  }

  // -------------------------------------------------------------------------
  // 描画と配線
  // -------------------------------------------------------------------------

  const PANES = {
    'document-list': listPaneHtml,
    'document-rules': rulesPaneHtml,
    'document-settings': settingsPaneHtml,
  };

  function renderTab(name) {
    const html = PANES[name];
    const pane = $(`tab-${name}`);
    if (!html || !pane) return;
    pane.innerHTML = html();
    wirePane(pane);
    wireDialogs();
  }

  // 状態が変わったときは 3 タブとも描き直す（選択・通知はタブをまたいで共有している）。
  function render() {
    for (const name of Object.keys(PANES)) renderTab(name);
  }

  function wirePane(pane) {
    for (const btn of pane.querySelectorAll('[data-docs-set]')) {
      btn.addEventListener('click', () => loadDetail(btn.dataset.docsSet));
    }
    for (const btn of pane.querySelectorAll('[data-docs-rule]')) {
      btn.addEventListener('click', () => loadRule(btn.dataset.docsRule));
    }
    for (const btn of pane.querySelectorAll('[data-docs-open]')) {
      btn.addEventListener('click', () => guard('開く', () => api().openPath(btn.dataset.docsOpen)));
    }
    for (const btn of pane.querySelectorAll('[data-docs-rule-jump]')) {
      btn.addEventListener('click', async () => {
        await loadRule(btn.dataset.docsRuleJump);
        if (typeof root.switchTab === 'function') root.switchTab('document-rules');
      });
    }
    for (const btn of pane.querySelectorAll('[data-docs-rule-edit]')) {
      btn.addEventListener('click', () => openRuleEditor({ file: btn.dataset.docsRuleEdit }));
    }
    for (const btn of pane.querySelectorAll('[data-docs-action]')) {
      btn.addEventListener('click', () => runAction(btn.dataset.docsAction));
    }
    for (const btn of pane.querySelectorAll('[data-docs-pick]')) {
      btn.addEventListener('click', () => pickFolder(btn.dataset.docsPick));
    }
    const newBtn = pane.querySelector('#btn-docs-new');
    if (newBtn) newBtn.addEventListener('click', openCreateDialog);
    const newRule = pane.querySelector('#btn-docs-rule-new');
    if (newRule) newRule.addEventListener('click', () => openRuleEditor({}));
    const save = pane.querySelector('#btn-docs-settings-save');
    if (save) save.addEventListener('click', saveSettings);
    for (const id of ['docs-set-workspace', 'docs-set-rules']) {
      const input = pane.querySelector(`#${id}`);
      if (input) input.addEventListener('input', () => {
        st.settings = {
          workspaceDir: (pane.querySelector('#docs-set-workspace') || {}).value || '',
          rulesDir: (pane.querySelector('#docs-set-rules') || {}).value || '',
        };
      });
    }
  }

  function on(id, event, fn) {
    const el = $(id);
    if (el) el.addEventListener(event, fn);
  }

  function wireDialogs() {
    if (st.wired || !$('dlg-docs-create')) return;
    st.wired = true;
    on('btn-docs-create-cancel', 'click', () => $('dlg-docs-create').close());
    on('btn-docs-create-ok', 'click', (ev) => { ev.preventDefault(); submitCreate(); });
    on('btn-docs-create-add-input', 'click', addInputs);
    on('btn-docs-resume-cancel', 'click', () => $('dlg-docs-resume').close());
    on('btn-docs-resume-ok', 'click', (ev) => { ev.preventDefault(); submitResume(); });
    on('btn-docs-verify-cancel', 'click', () => $('dlg-docs-verify').close());
    on('btn-docs-verify-ok', 'click', (ev) => { ev.preventDefault(); submitVerify(); });
    on('btn-docs-feedback-cancel', 'click', () => $('dlg-docs-feedback').close());
    on('btn-docs-feedback-ok', 'click', (ev) => { ev.preventDefault(); submitFeedback(); });
    on('btn-docs-rule-cancel', 'click', () => $('dlg-docs-rule').close());
    on('btn-docs-rule-expand', 'click', (ev) => { ev.preventDefault(); expandRule(); });
    on('btn-docs-rule-save', 'click', (ev) => { ev.preventDefault(); saveRule(); });
    for (const radio of document.querySelectorAll('input[name="docs-feedback-target"]')) {
      radio.addEventListener('change', updateFeedbackFields);
    }
  }

  // -------------------------------------------------------------------------
  // 作成
  // -------------------------------------------------------------------------

  function formatChecksHtml(prefix, checked) {
    const formats = (st.overview && st.overview.formats) || [];
    return formats.map((f) => `<label class="check docs-check"><input type="checkbox" name="${esc(prefix)}" value="${esc(f.id)}" ${checked.includes(f.id) ? 'checked' : ''} /> ${esc(f.label)}</label>`).join('');
  }

  function checkedValues(name) {
    return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((el) => el.value);
  }

  function ruleOptionsHtml(selectedFile, { allowNone = true } = {}) {
    const rulesList = (st.overview && st.overview.rules) || [];
    const none = allowNone ? '<option value="">使わない（質問で決める）</option>' : '';
    return none + rulesList.map((r) => `<option value="${esc(r.file)}" ${r.file === selectedFile ? 'selected' : ''}>${esc(r.name)}${(r.formats || []).length ? `（${esc(r.formats.map(formatLabel).join('・'))}）` : ''}</option>`).join('');
  }

  function renderCreateInputs() {
    const box = $('docs-create-inputs');
    if (!box) return;
    box.innerHTML = st.createInputs.length
      ? `<ul class="docs-inputs">${st.createInputs.map((p, i) => `<li><span class="docs-file">${esc(p)}</span> <button type="button" class="linklike" data-docs-input-remove="${i}">外す</button></li>`).join('')}</ul>`
      : '<p class="muted">入力ファイルはありません。</p>';
    for (const btn of box.querySelectorAll('[data-docs-input-remove]')) {
      btn.addEventListener('click', () => {
        st.createInputs.splice(Number(btn.dataset.docsInputRemove), 1);
        renderCreateInputs();
      });
    }
  }

  function openCreateDialog() {
    const dlg = $('dlg-docs-create');
    if (!dlg) return;
    st.createInputs = [];
    $('docs-create-name').value = '';
    $('docs-create-prompt').value = '';
    $('docs-create-formats').innerHTML = formatChecksHtml('docs-create-format', ['docx']);
    $('docs-create-rule').innerHTML = ruleOptionsHtml(st.ruleSelected || '');
    const whole = document.querySelector('input[name="docs-create-mode"][value="whole"]');
    if (whole) whole.checked = true;
    $('docs-create-status').textContent = '';
    renderCreateInputs();
    dlg.showModal();
  }

  async function addInputs() {
    const res = await guard('入力ファイルの選択', () => api().documentsPickInputs());
    if (!res || res.canceled) return;
    for (const f of res.files || []) if (!st.createInputs.includes(f)) st.createInputs.push(f);
    renderCreateInputs();
  }

  async function submitCreate() {
    const status = $('docs-create-status');
    const payload = {
      name: $('docs-create-name').value,
      formats: checkedValues('docs-create-format'),
      ruleFile: $('docs-create-rule').value,
      mode: (document.querySelector('input[name="docs-create-mode"]:checked') || {}).value || 'whole',
      prompt: $('docs-create-prompt').value,
      inputs: [...st.createInputs],
    };
    status.textContent = 'エージェントを起動しています…';
    st.busy = '文書の作成を始めています…';
    render();
    try {
      const res = await api().documentsCreate(payload);
      $('dlg-docs-create').close();
      st.busy = '';
      afterLaunch(res);
    } catch (e) {
      st.busy = '';
      status.textContent = String((e && e.message) || e);
      render();
    }
  }

  function afterLaunch(res) {
    const launch = (res && res.launch) || {};
    const msg = launch.ok === false ? (launch.error || '起動できませんでした')
      : (launch.message || '別ウィンドウでエージェントを起動しました');
    st.notice = { text: msg, ok: launch.ok !== false };
    toast(msg, launch.ok !== false);
    refresh().then(() => {
      if (res && res.set && res.set.id) loadDetail(res.set.id, { silent: true });
    });
  }

  // -------------------------------------------------------------------------
  // 続き・検証・フィードバック・履歴からルール
  // -------------------------------------------------------------------------

  function runAction(kind) {
    if (!st.detail) return;
    if (kind === 'resume') {
      $('docs-resume-title').textContent = `続きを依頼: ${st.detail.name}`;
      $('docs-resume-instruction').value = '';
      $('docs-resume-status').textContent = '';
      $('dlg-docs-resume').showModal();
      return;
    }
    if (kind === 'verify') {
      $('docs-verify-title').textContent = `検証する: ${st.detail.name}`;
      $('docs-verify-review').value = '';
      $('docs-verify-status').textContent = '';
      $('dlg-docs-verify').showModal();
      return;
    }
    if (kind === 'feedback') {
      $('docs-feedback-title').textContent = `フィードバック: ${st.detail.name}`;
      $('docs-feedback-text').value = '';
      $('docs-feedback-name').value = `${st.detail.name}のルール`;
      $('docs-feedback-rule').innerHTML = ruleOptionsHtml((st.detail.rule && st.detail.rule.file) || '', { allowNone: false });
      const hasRule = !!((st.overview && st.overview.rules) || []).length;
      const existing = document.querySelector('input[name="docs-feedback-target"][value="existing"]');
      const fresh = document.querySelector('input[name="docs-feedback-target"][value="new"]');
      if (existing) {
        existing.disabled = !hasRule;
        existing.checked = hasRule && !!(st.detail.rule && st.detail.rule.file);
      }
      if (fresh) fresh.checked = !(existing && existing.checked);
      updateFeedbackFields();
      $('docs-feedback-status').textContent = '';
      $('dlg-docs-feedback').showModal();
      return;
    }
    if (kind === 'rule-from-history') ruleFromHistory();
  }

  async function submitResume() {
    const status = $('docs-resume-status');
    status.textContent = 'エージェントを起動しています…';
    try {
      const res = await api().documentsResume({ id: st.selected, instruction: $('docs-resume-instruction').value });
      $('dlg-docs-resume').close();
      afterLaunch(res);
    } catch (e) {
      status.textContent = String((e && e.message) || e);
    }
  }

  async function submitVerify() {
    const status = $('docs-verify-status');
    status.textContent = 'エージェントを起動しています…';
    try {
      const res = await api().documentsVerify({ id: st.selected, review: $('docs-verify-review').value });
      $('dlg-docs-verify').close();
      afterLaunch(res);
    } catch (e) {
      status.textContent = String((e && e.message) || e);
    }
  }

  function feedbackTarget() {
    return (document.querySelector('input[name="docs-feedback-target"]:checked') || {}).value || 'new';
  }

  function updateFeedbackFields() {
    const existing = feedbackTarget() === 'existing';
    const ruleField = $('docs-feedback-rule-field');
    const nameField = $('docs-feedback-name-field');
    if (ruleField) ruleField.hidden = !existing;
    if (nameField) nameField.hidden = existing;
  }

  async function submitFeedback() {
    const status = $('docs-feedback-status');
    status.textContent = 'ルールの案を作っています…（しばらくかかります）';
    const target = feedbackTarget();
    try {
      const res = await api().documentsFeedback({
        id: st.selected,
        feedback: $('docs-feedback-text').value,
        target,
        ruleFile: target === 'existing' ? $('docs-feedback-rule').value : '',
        name: $('docs-feedback-name').value,
      });
      $('dlg-docs-feedback').close();
      openRuleEditor({ file: res.file || '', name: res.name, content: res.content, kind: 'feedback' });
      refresh();
    } catch (e) {
      status.textContent = String((e && e.message) || e);
    }
  }

  async function ruleFromHistory() {
    st.busy = '改訂履歴からルールの案を作っています…（しばらくかかります）';
    render();
    try {
      const res = await api().documentsRuleFromHistory({ id: st.selected });
      st.busy = '';
      render();
      openRuleEditor({ file: '', name: res.name, content: res.content, kind: 'history' });
    } catch (e) {
      st.busy = '';
      setNotice(String((e && e.message) || e), false);
    }
  }

  // -------------------------------------------------------------------------
  // ルール編集（新規・既存編集・AI 案の確定）
  // -------------------------------------------------------------------------

  async function openRuleEditor({ file = '', name = '', content = '', kind = 'new' } = {}) {
    const dlg = $('dlg-docs-rule');
    if (!dlg) return;
    let text = content;
    let ruleName = name;
    let formats = [];
    if (file && !content) {
      const r = await guard('ルールの読込', () => api().documentsRuleRead({ file }));
      if (!r) return;
      text = r.content;
      ruleName = r.name;
      formats = r.formats || [];
      kind = 'edit';
    }
    st.editor = { file, kind };
    $('docs-rule-title').textContent = file ? `ルールを編集: ${ruleName}` : (content ? `ルールの案を確認: ${ruleName}` : 'ルールを作る');
    $('docs-rule-name').value = ruleName || '';
    $('docs-rule-formats').innerHTML = formatChecksHtml('docs-rule-format', formats.length ? formats : ['docx']);
    $('docs-rule-template').value = '';
    $('docs-rule-draft').value = '';
    $('docs-rule-content').value = text || '';
    $('docs-rule-status').textContent = content
      ? 'AI の案です。内容を確認・編集してから保存してください。'
      : (file ? '' : '原案を入力して「AI で膨らませる」を押すか、本文を直接書きます。');
    const draftBox = $('docs-rule-draft-box');
    if (draftBox) draftBox.hidden = !!file;
    dlg.showModal();
  }

  async function expandRule() {
    const status = $('docs-rule-status');
    status.textContent = 'AI が節ごとに膨らませています…（しばらくかかります）';
    const btn = $('btn-docs-rule-expand');
    if (btn) btn.disabled = true;
    try {
      const res = await api().documentsRuleDraft({
        name: $('docs-rule-name').value,
        formats: checkedValues('docs-rule-format'),
        draft: $('docs-rule-draft').value,
        template: $('docs-rule-template').value,
      });
      $('docs-rule-content').value = res.content;
      if (res.parsed && res.parsed.name && !$('docs-rule-name').value.trim()) $('docs-rule-name').value = res.parsed.name;
      status.textContent = `${res.cli}${res.model ? `（${res.model}）` : ''} の案です。編集してから保存してください。`;
    } catch (e) {
      status.textContent = String((e && e.message) || e);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function saveRule() {
    const status = $('docs-rule-status');
    try {
      const res = await api().documentsRuleSave({
        file: (st.editor && st.editor.file) || '',
        name: $('docs-rule-name').value,
        content: $('docs-rule-content').value,
      });
      $('dlg-docs-rule').close();
      st.notice = { text: res.created ? `文書ルール「${res.name}」を作成しました` : `文書ルール「${res.name}」を更新しました`, ok: true };
      toast(st.notice.text, true);
      await refresh();
      await loadRule(res.file);
      if (typeof root.switchTab === 'function' && root.activeTab && root.activeTab() !== 'document-rules') {
        root.switchTab('document-rules');
      }
    } catch (e) {
      status.textContent = String((e && e.message) || e);
    }
  }

  // -------------------------------------------------------------------------
  // 設定
  // -------------------------------------------------------------------------

  async function pickFolder(kind) {
    const res = await guard('フォルダの選択', () => api().documentsPickFolder({ kind }));
    if (!res || res.canceled) return;
    const input = $(kind === 'rules' ? 'docs-set-rules' : 'docs-set-workspace');
    if (input) input.value = res.dir;
    st.settings = {
      workspaceDir: ($('docs-set-workspace') || {}).value || '',
      rulesDir: ($('docs-set-rules') || {}).value || '',
    };
  }

  async function saveSettings() {
    const payload = {
      workspaceDir: ($('docs-set-workspace') || {}).value || '',
      rulesDir: ($('docs-set-rules') || {}).value || '',
    };
    st.busy = '保存しています…';
    render();
    try {
      const saved = await api().documentsSaveSettings(payload);
      if (saved) appState().config = saved;
      st.settings = null;
      st.busy = '';
      st.notice = { text: '設定を保存しました', ok: true };
      await refresh();
    } catch (e) {
      st.busy = '';
      setNotice(String((e && e.message) || e), false);
    }
  }

  // -------------------------------------------------------------------------
  // ホームのカード
  // -------------------------------------------------------------------------

  function portalCardHtml() {
    const n = ((st.overview && st.overview.sets) || []).length;
    return `<div class="portal-card-heading">
        <span class="summary-kicker">文書を作らせる</span>
        <h3>ドキュメント</h3>
      </div>
      <p class="portal-card-count">${n ? `<strong>${n}</strong> 件の文書` : '文書はまだありません'}</p>
      <div class="portal-card-actions">
        <button type="button" class="primary-inline" data-portal-area="documents">開く</button>
      </div>`;
  }

  return {
    refresh,
    render,
    renderTab,
    portalCardHtml,
    listPaneHtml,
    rulesPaneHtml,
    settingsPaneHtml,
    outputsHtml,
    detailHtml,
    ruleDetailHtml,
    state: st,
  };
});
