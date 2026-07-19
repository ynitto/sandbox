'use strict';

/* global api, registerFeatureTab */

// 委譲タブの renderer モジュール。renderer.js のコアには触らず、registerFeatureTab で
// 自分のタブ描画を差し込む（フィーチャー単位のモジュール分割 — 保守性向上の新パターン）。
//
// workload（flow / amigos）を選ぶだけで、同じフォームから両エンジンへ公示（post）でき、
// 一覧では入札状況（units[].bids）を正規化ビューで見て、owner-picks の落札（award）・
// 受入（accept/reject）・中止（cancel）を同じ操作面から行える。
// データ契約は schemas/delegation.schema.json、投函口は preload の delegation* API。

(function () {
  const S = {
    items: [],
    errors: [],
    loaded: false,
    busy: false,
    notice: '',
    error: '',
    filter: 'all', // all / flow / amigos
    rejectFor: null, // 差し戻し入力を開いているミッション id
    rejectText: '',
    form: {
      workload: 'amigos',
      goal: '',
      title: '',
      design: '',
      assignment: 'first-come',
      roles: '[\n  { "id": "impl", "title": "実装", "required": true }\n]',
      home: '',
      busDir: '',
      executor: '',
    },
  };

  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ESC[c]);
  const pane = () => document.getElementById('tab-delegation');

  const PHASE_LABEL = {
    open: '公示中', working: '進行中', waiting: '承認待ち', reviewing: '受入待ち',
    done: '完了', failed: '失敗', cancelled: '中止',
  };
  const UNIT_LABEL = { open: '入札受付中', claimed: '実行中', waiting: '保留', done: '完了', failed: '失敗' };
  const BID_LABEL = { winner: '落札', applied: '応募中', lost: '不落', expired: '失効' };
  const WORKLOAD_LABEL = { flow: 'flow（タスク分散）', amigos: 'amigos（役割協働）' };

  async function refresh() {
    if (!api || typeof api.delegationList !== 'function') return;
    try {
      const res = await api.delegationList();
      S.items = (res && res.items) || [];
      S.errors = (res && res.errors) || [];
      S.loaded = true;
      S.error = '';
    } catch (e) {
      S.error = e && e.message ? e.message : String(e);
      S.loaded = true;
    }
  }

  // --- フォーム（新規委譲） --------------------------------------------------

  function formHtml() {
    const f = S.form;
    const isAmigos = f.workload === 'amigos';
    // flow は owner-picks 未対応（契約が投函前に拒否する）。UI でも選べないようにする。
    const ownerPicksDisabled = !isAmigos;
    return `
    <form id="deleg-form" class="card" style="padding:12px;margin-bottom:16px;">
      <h3 style="margin:0 0 8px;">新規委譲</h3>
      <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;">
        <label>委譲先エンジン
          <select id="deleg-workload">
            <option value="amigos"${isAmigos ? ' selected' : ''}>${esc(WORKLOAD_LABEL.amigos)}</option>
            <option value="flow"${!isAmigos ? ' selected' : ''}>${esc(WORKLOAD_LABEL.flow)}</option>
          </select>
        </label>
        <label>入札方式
          <select id="deleg-assignment">
            <option value="first-come"${f.assignment === 'first-come' ? ' selected' : ''}>先着（即落札）</option>
            <option value="owner-picks"${f.assignment === 'owner-picks' ? ' selected' : ''}${ownerPicksDisabled ? ' disabled' : ''}>応募→選定（owner-picks）</option>
          </select>
        </label>
      </div>
      <label style="display:block;margin-top:8px;">目標（goal）
        <textarea id="deleg-goal" rows="2" style="width:100%;">${esc(f.goal)}</textarea>
      </label>
      <label style="display:block;margin-top:8px;">表示名（title・任意）
        <input id="deleg-title" type="text" style="width:100%;" value="${esc(f.title)}">
      </label>
      <label style="display:block;margin-top:8px;">設計（design・任意。省略時は goal から合成）
        <textarea id="deleg-design" rows="3" style="width:100%;">${esc(f.design)}</textarea>
      </label>
      <div id="deleg-amigos-fields" style="${isAmigos ? '' : 'display:none;'}margin-top:8px;">
        <label style="display:block;">役割ミッション表（roles・JSON 配列）
          <textarea id="deleg-roles" rows="4" style="width:100%;font-family:monospace;">${esc(f.roles)}</textarea>
        </label>
        <label style="display:block;margin-top:8px;">投函先ホーム（amigos ホームのパス）
          <input id="deleg-home" type="text" style="width:100%;" value="${esc(f.home)}" placeholder="/path/to/amigos-home">
        </label>
      </div>
      <div id="deleg-flow-fields" style="${isAmigos ? 'display:none;' : ''}margin-top:8px;">
        <label style="display:block;">投函先バス（flow バスのパス）
          <input id="deleg-busdir" type="text" style="width:100%;" value="${esc(f.busDir)}" placeholder="/path/to/flow-bus">
        </label>
        <label style="display:block;margin-top:8px;">executor（任意・例 gitlab）
          <input id="deleg-executor" type="text" style="width:100%;" value="${esc(f.executor)}">
        </label>
      </div>
      <div style="margin-top:12px;">
        <button type="submit" id="deleg-submit" class="btn"${S.busy ? ' disabled' : ''}>公示する</button>
      </div>
    </form>`;
  }

  // --- 一覧（入札状況） ------------------------------------------------------

  function bidsHtml(item, unit) {
    if (!unit.bids || !unit.bids.length) return '';
    const rows = unit.bids.map((b) => {
      const canAward = item.workload === 'amigos' && item.bids_open && b.state === 'applied';
      const awardBtn = canAward
        ? ` <button class="btn deleg-award" data-id="${esc(item.id)}" data-unit="${esc(unit.unit)}" data-node="${esc(b.who)}" data-home="${esc(item.home || '')}">落札</button>`
        : '';
      return `<li><code>${esc(b.who)}</code> <span class="badge">${esc(BID_LABEL[b.state] || b.state)}</span>${awardBtn}</li>`;
    }).join('');
    return `<ul class="deleg-bids" style="margin:4px 0 0;padding-left:16px;">${rows}</ul>`;
  }

  function unitsHtml(item) {
    if (!item.units || !item.units.length) return '<div class="muted">ユニットなし</div>';
    return item.units.map((u) => {
      const assignee = u.assignee ? ` — <code>${esc(u.assignee)}</code>` : '';
      return `<div class="deleg-unit" style="margin:6px 0;">
        <strong>${esc(u.kind || u.unit)}</strong>
        <span class="badge">${esc(UNIT_LABEL[u.state] || u.state)}</span>${assignee}
        ${bidsHtml(item, u)}
      </div>`;
    }).join('');
  }

  function actionsHtml(item) {
    const terminal = item.phase === 'done' || item.phase === 'failed' || item.phase === 'cancelled';
    const btns = [];
    if (item.workload === 'amigos' && item.phase === 'reviewing') {
      btns.push(`<button class="btn deleg-accept" data-id="${esc(item.id)}" data-home="${esc(item.home || '')}">受入</button>`);
      btns.push(`<button class="btn deleg-reject" data-id="${esc(item.id)}" data-home="${esc(item.home || '')}">差し戻し</button>`);
    }
    if (!terminal) {
      const loc = item.workload === 'amigos'
        ? `data-home="${esc(item.home || '')}"`
        : `data-busdir="${esc(item.busDir || '')}"`;
      btns.push(`<button class="btn deleg-cancel" data-id="${esc(item.id)}" data-workload="${esc(item.workload)}" ${loc}>中止</button>`);
    }
    const bar = btns.length ? `<div class="deleg-actions" style="margin-top:8px;display:flex;gap:6px;">${btns.join('')}</div>` : '';
    // 差し戻しはインライン入力で受ける（window.prompt は Electron で動かないため）。
    const rejectBox = S.rejectFor === item.id
      ? `<div class="deleg-reject-box" style="margin-top:8px;">
           <textarea id="deleg-reject-text" rows="2" style="width:100%;" placeholder="差し戻し理由（修正依頼）">${esc(S.rejectText)}</textarea>
           <div style="margin-top:4px;display:flex;gap:6px;">
             <button class="btn deleg-reject-send" data-id="${esc(item.id)}" data-home="${esc(item.home || '')}">差し戻しを送る</button>
             <button class="btn deleg-reject-cancel">取消</button>
           </div>
         </div>`
      : '';
    return bar + rejectBox;
  }

  function itemHtml(item) {
    const p = item.progress || {};
    const staleBadge = item.stale ? ' <span class="badge" style="background:#c0392b;color:#fff;">応答なし</span>' : '';
    const bidsOpen = item.bids_open ? ' <span class="badge" style="background:#f39c12;color:#fff;">落札待ち</span>' : '';
    return `<div class="card deleg-item" style="padding:12px;margin-bottom:12px;">
      <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap;">
        <div>
          <strong>${esc(item.title || item.id)}</strong>
          <span class="badge">${esc(item.workload)}</span>
          <span class="badge">${esc(PHASE_LABEL[item.phase] || item.phase)}</span>${staleBadge}${bidsOpen}
        </div>
        <code class="muted">${esc(item.id)}</code>
      </div>
      ${item.goal ? `<div class="muted" style="margin:4px 0;">${esc(item.goal).slice(0, 160)}</div>` : ''}
      <div class="muted" style="font-size:12px;">進捗: ${p.units_done || 0}/${p.units_total || 0} 完了・${p.units_open || 0} 入札受付中${p.units_failed ? `・${p.units_failed} 失敗` : ''}</div>
      ${unitsHtml(item)}
      ${actionsHtml(item)}
    </div>`;
  }

  function listHtml() {
    const items = S.filter === 'all' ? S.items : S.items.filter((i) => i.workload === S.filter);
    if (!S.loaded) return '<div class="muted">読み込み中…</div>';
    if (!items.length) return '<div class="muted">委譲はありません。</div>';
    return items.map(itemHtml).join('');
  }

  function render() {
    const el = pane();
    if (!el) return;
    const notice = S.notice ? `<div class="notice" style="color:#27ae60;margin-bottom:8px;">${esc(S.notice)}</div>` : '';
    const error = S.error ? `<div class="notice" style="color:#c0392b;margin-bottom:8px;">${esc(S.error)}</div>` : '';
    const busErrors = (S.errors && S.errors.length)
      ? `<details style="margin-bottom:8px;"><summary class="muted">読み取り警告 ${S.errors.length} 件</summary><ul>${S.errors.map((e) => `<li class="muted">${esc(e)}</li>`).join('')}</ul></details>`
      : '';
    const filters = ['all', 'amigos', 'flow'].map((k) =>
      `<button class="btn deleg-filter${S.filter === k ? ' active' : ''}" data-filter="${k}">${k === 'all' ? 'すべて' : k}</button>`
    ).join(' ');
    el.innerHTML = `
      <div style="padding:12px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <h2 style="margin:0;">委譲</h2>
          <div><button id="deleg-refresh" class="btn">更新</button></div>
        </div>
        <p class="muted" style="margin-top:0;">agent-flow / agent-amigos へ同じ封筒で公示・入札・落札・受入を行います。</p>
        ${notice}${error}${busErrors}
        ${formHtml()}
        <div style="margin-bottom:8px;">${filters}</div>
        <div id="deleg-list">${listHtml()}</div>
      </div>`;
    wire(el);
  }

  // --- イベント配線 ----------------------------------------------------------

  function readForm(el) {
    const val = (id) => {
      const n = el.querySelector(`#${id}`);
      return n ? n.value : '';
    };
    S.form.workload = val('deleg-workload') || 'amigos';
    S.form.assignment = val('deleg-assignment') || 'first-come';
    S.form.goal = val('deleg-goal');
    S.form.title = val('deleg-title');
    S.form.design = val('deleg-design');
    S.form.roles = val('deleg-roles');
    S.form.home = val('deleg-home');
    S.form.busDir = val('deleg-busdir');
    S.form.executor = val('deleg-executor');
  }

  function flash(msg) {
    S.notice = msg;
    setTimeout(() => { S.notice = ''; render(); }, 4000);
  }

  async function submitPost(el) {
    readForm(el);
    const f = S.form;
    if (!f.goal.trim()) { S.error = '目標（goal）を入力してください'; render(); return; }
    const payload = {
      workload: f.workload,
      goal: f.goal,
      title: f.title,
      design: f.design,
      policy: { assignment: f.assignment },
    };
    if (f.workload === 'amigos') {
      let roles;
      try {
        roles = JSON.parse(f.roles);
      } catch (e) {
        S.error = `roles が不正な JSON です: ${e.message}`; render(); return;
      }
      payload.engine = { amigos: { roles } };
      payload.home = f.home;
    } else {
      payload.busDir = f.busDir;
      if (f.executor.trim()) payload.engine = { flow: { executor: f.executor.trim() } };
    }
    S.busy = true; S.error = ''; render();
    try {
      const res = await api.delegationPost(payload);
      await refresh();
      flash(`公示しました: ${res.id}`);
    } catch (e) {
      S.error = e && e.message ? e.message : String(e);
    } finally {
      S.busy = false;
      render();
    }
  }

  async function runAction(fn, okMsg) {
    S.busy = true; S.error = ''; render();
    try {
      await fn();
      await refresh();
      flash(okMsg);
    } catch (e) {
      S.error = e && e.message ? e.message : String(e);
    } finally {
      S.busy = false;
      render();
    }
  }

  function wire(el) {
    const refreshBtn = el.querySelector('#deleg-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', () => runAction(() => Promise.resolve(), '更新しました'));

    const workloadSel = el.querySelector('#deleg-workload');
    if (workloadSel) {
      workloadSel.addEventListener('change', () => {
        readForm(el);
        // flow に切り替えたら owner-picks は選べないので first-come に戻す
        if (S.form.workload === 'flow' && S.form.assignment === 'owner-picks') {
          S.form.assignment = 'first-come';
        }
        render();
      });
    }

    const form = el.querySelector('#deleg-form');
    if (form) form.addEventListener('submit', (ev) => { ev.preventDefault(); submitPost(el); });

    for (const b of el.querySelectorAll('.deleg-filter')) {
      b.addEventListener('click', () => { S.filter = b.dataset.filter; render(); });
    }
    for (const b of el.querySelectorAll('.deleg-award')) {
      b.addEventListener('click', () => runAction(
        () => api.delegationAward({
          workload: 'amigos', id: b.dataset.id, unit: b.dataset.unit,
          node: b.dataset.node, home: b.dataset.home,
        }),
        `落札しました: ${b.dataset.unit} → ${b.dataset.node}`
      ));
    }
    for (const b of el.querySelectorAll('.deleg-accept')) {
      b.addEventListener('click', () => runAction(
        () => api.delegationAccept({ workload: 'amigos', id: b.dataset.id, home: b.dataset.home }),
        '受入を投函しました'
      ));
    }
    for (const b of el.querySelectorAll('.deleg-reject')) {
      b.addEventListener('click', () => { S.rejectFor = b.dataset.id; S.rejectText = ''; render(); });
    }
    const rejectText = el.querySelector('#deleg-reject-text');
    if (rejectText) rejectText.addEventListener('input', () => { S.rejectText = rejectText.value; });
    for (const b of el.querySelectorAll('.deleg-reject-send')) {
      b.addEventListener('click', () => {
        const feedback = String(S.rejectText || '').trim();
        if (!feedback) { S.error = '差し戻しには修正依頼の内容が必要です'; render(); return; }
        S.rejectFor = null;
        runAction(
          () => api.delegationReject({ workload: 'amigos', id: b.dataset.id, feedback, home: b.dataset.home }),
          '差し戻しを投函しました'
        );
      });
    }
    const rejectCancel = el.querySelector('.deleg-reject-cancel');
    if (rejectCancel) rejectCancel.addEventListener('click', () => { S.rejectFor = null; S.rejectText = ''; render(); });
    for (const b of el.querySelectorAll('.deleg-cancel')) {
      b.addEventListener('click', () => runAction(
        () => api.delegationCancel({
          workload: b.dataset.workload, id: b.dataset.id,
          home: b.dataset.home || undefined, busDir: b.dataset.busdir || undefined,
        }),
        '中止しました'
      ));
    }
  }

  // renderer.js が先に読み込まれて登録簿を公開している前提。単体 require では未定義なので守る。
  if (typeof registerFeatureTab === 'function') {
    registerFeatureTab('delegation', { render, refresh });
  }

  // テスト用にモジュール内部を露出（Electron 実行時は module 未定義で無害）。
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { S, render, refresh, esc, PHASE_LABEL, __wire: wire };
  }
})();
