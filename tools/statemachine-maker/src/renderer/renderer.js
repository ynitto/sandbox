'use strict';

// 画面。持つのは「選んだフォルダ」「一覧」「編集中の定義（工程列）」だけで、コンパイル・検査・
// 読み戻し・記録の変換はすべて main（model.js / store.js / recording.js）に頼む。
// 中央は工程のフロー（各工程が何をするか・どう遷移するかを 1 枚ずつ）、右は選んだ工程の編集。

const api = window.api;
const $ = (id) => document.getElementById(id);

const state = {
  root: '',
  machines: [],
  catalog: { kinds: [], platform: '' },
  config: {},
  current: null,      // { machine, isNew, spec(raw), dirty, warnings, dir }
  selected: -1,
  tab: 'step',
  preview: null,      // machine:preview の結果
  tools: null,
  recording: { source: 'browser', url: '', app: '', text: '', active: false, busy: false, message: '', ok: true },
  run: { lines: [], running: false, mode: '', input: '', agent: '', command: null },
  ai: null,
  fileTab: 'workflow.yaml',
};

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', '\'': '&#39;' }[c]));
}

let toastTimer = null;
function toast(message, error = false) {
  const el = $('toast');
  el.textContent = message;
  el.className = error ? 'error' : '';
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, error ? 6000 : 3000);
}

async function guard(what, fn) {
  try {
    return await fn();
  } catch (err) {
    toast(`${what}: ${(err && err.message) || err}`, true);
    return null;
  }
}

function kindOf(id) {
  return state.catalog.kinds.find((k) => k.id === id)
    || { id, label: id, short: id, target: null, detail: { label: '内容', required: true, placeholder: '' }, check: null, recordable: false };
}

function machineIdFrom(name) {
  const ascii = String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return ascii || `routine-${Date.now().toString(36)}`;
}

// --- 定義（工程列）の形 ------------------------------------------------------------------

function emptyStep(kind) {
  return { id: '', kind, title: '', detail: '', target: '', check: '', checkRetries: 1, outcomes: [], recorded: [] };
}

function newSpec() {
  return {
    version: 3, name: '', machine: '', purpose: '', finish: '', notes: '', maxSteps: 30,
    terminals: { done: { id: 'complete', description: '完了' }, abort: { id: 'failed', description: '失敗として終了' } },
    steps: [], preserved: null,
  };
}

// ステート ID は工程の位置で自動採番する（人が付け直したものは尊重する）。
function assignIds(spec) {
  const used = new Set(spec.steps.map((s) => s.id).filter(Boolean));
  spec.steps.forEach((s, i) => {
    if (s.id) return;
    let n = i + 1;
    let id = `step_${n}`;
    while (used.has(id)) { n += 1; id = `step_${n}`; }
    s.id = id;
    used.add(id);
  });
}

function specPayload() {
  const spec = state.current.spec;
  assignIds(spec);
  return {
    ...spec,
    machine: spec.machine || machineIdFrom(spec.name),
    steps: spec.steps.map((s) => ({ ...s, checkRetries: Number(s.checkRetries) || 0 })),
  };
}

// 遷移の表示（model.stepTransitions と同じ規則を画面用に写す。判定は保存時に main が行う）。
function transitionsOf(spec, index) {
  const step = spec.steps[index];
  const count = spec.steps.length;
  const describe = (to) => {
    if (to === 'done') return { text: '完了', cls: 'done' };
    if (to === 'abort') return { text: '失敗として終了', cls: 'abort' };
    if (to === 'next') return index + 1 < count ? { text: `工程 ${index + 2} へ`, cls: '' } : { text: '完了', cls: 'done' };
    const n = Number(String(to).slice(5));
    if (n === index + 1) return { text: 'この工程をやり直す', cls: 'back' };
    return n <= index ? { text: `工程 ${n} へ戻る`, cls: 'back' } : { text: `工程 ${n} へ`, cls: '' };
  };
  if (step.rawTransitions) {
    const preserved = (spec.preserved && spec.preserved.transitions) || [];
    const own = preserved.filter((t) => t && t.from === step.id);
    if (!own.length) return [{ label: 'YAML', cls: 'raw', text: '遷移は原文のまま（画面では編集できません）', cls2: '' }];
    return own.map((t) => ({ label: t.condition_rule ? String(t.condition_rule).slice(0, 24) : '条件（自然言語）', cls: 'raw', text: `${t.to}${t.condition ? `: ${String(t.condition).slice(0, 60)}` : ''}` }));
  }
  if (step.outcomes.length) {
    return step.outcomes.map((o) => ({ label: o.label, cls: step.check ? 'gate' : '', ...describe(o.to) }));
  }
  if (step.check) return [{ label: '検査が通る', cls: 'gate', ...describe('next') }];
  return [{ label: 'OK', cls: 'ok', ...describe('next') }, { label: 'FAILED', cls: 'ng', ...describe('abort') }];
}

// --- 一覧 -------------------------------------------------------------------------------

async function loadMachines() {
  if (!state.root) { state.machines = []; renderSidebar(); return; }
  const list = await guard('一覧の取得', () => api.listMachines(state.root));
  state.machines = list || [];
  renderSidebar();
}

function renderSidebar() {
  const ul = $('machine-list');
  if (!state.root) { ul.innerHTML = '<li class="muted small">フォルダを選ぶと一覧が出ます</li>'; return; }
  if (!state.machines.length) { ul.innerHTML = '<li class="muted small">まだ定義がありません。「新しいステートマシン」から作れます</li>'; return; }
  ul.innerHTML = state.machines.map((m) => `<li class="${state.current && state.current.machine === m.machine ? 'is-active' : ''}">
    <button type="button" data-machine="${esc(m.machine)}">
      <span class="name">${esc(m.name)}</span>
      <span class="id mono">${esc(m.machine)}${m.maker ? ' <span class="tag">maker</span>' : ''}</span>
    </button></li>`).join('');
  for (const btn of ul.querySelectorAll('[data-machine]')) {
    btn.addEventListener('click', () => openMachine(btn.dataset.machine));
  }
}

function renderRoot() {
  const el = $('root-path');
  el.textContent = state.root || '未選択';
  el.title = state.root || '';
  const sel = $('recent-roots');
  const recent = (state.config.recentRoots || []);
  sel.innerHTML = '<option value="">最近開いたフォルダ</option>' + recent.map((r) => `<option value="${esc(r)}">${esc(r)}</option>`).join('');
}

async function setRoot(root) {
  if (!root) return;
  if (state.current && state.current.dirty && !confirm('保存していない変更があります。フォルダを切り替えますか？')) return;
  state.root = root;
  state.current = null;
  state.selected = -1;
  state.config = (await guard('設定', () => api.getConfig())) || state.config;
  renderRoot();
  await loadMachines();
  renderEditor();
  renderPanel();
}

// --- 定義を開く・新規 -------------------------------------------------------------------

async function openMachine(machine) {
  if (!state.root) return;
  if (state.current && state.current.dirty && !confirm('保存していない変更があります。別の定義を開きますか？')) return;
  const res = await guard('定義の読み込み', () => api.readMachine(state.root, machine));
  if (!res) return;
  const raw = res.raw;
  raw.steps = raw.steps.map((s) => ({ ...emptyStep(s.kind), ...s, outcomes: s.outcomes || [], recorded: s.recorded || [] }));
  state.current = { machine, isNew: false, spec: raw, dirty: false, warnings: res.warnings || [], dir: res.dir };
  state.selected = raw.steps.length ? 0 : -1;
  state.preview = null;
  state.ai = null;
  state.run.lines = [];
  if (res.warnings && res.warnings.length) toast(`読み戻しの注意 ${res.warnings.length} 件（下の状態欄に出ます）`);
  renderSidebar();
  renderEditor();
  renderPanel();
}

function newMachine() {
  if (!state.root) { toast('先にフォルダを選んでください', true); return; }
  if (state.current && state.current.dirty && !confirm('保存していない変更があります。新しい定義を始めますか？')) return;
  state.current = { machine: '', isNew: true, spec: newSpec(), dirty: true, warnings: [], dir: '' };
  state.selected = -1;
  state.preview = null;
  state.ai = null;
  renderSidebar();
  renderEditor();
  renderPanel();
  const name = $('m-name');
  if (name) name.focus();
}

function markDirty() {
  if (!state.current) return;
  state.current.dirty = true;
  state.preview = null;
  state.ai = null;
  const el = $('dirty-mark');
  if (el) el.hidden = false;
}

// --- 中央: 定義の頭 + フロー ---------------------------------------------------------------

function renderEditor() {
  const root = $('editor');
  if (!state.current) {
    root.innerHTML = `<div class="empty">
      <h3>${state.root ? '定義を選ぶか、新しく作ります' : 'まずフォルダを選びます'}</h3>
      <p>工程（画面操作・スキル・コマンド・AI の判断）を上から順に並べると、statemachine-use スキルで動く
      <code>.statemachine/&lt;識別名&gt;/</code> の定義（workflow.yaml + actions/*.md）になります。<br>
      人がやって見せた操作の記録（playwright-cli / winauto）からも工程を起こせます。</p>
      <div class="row">
        ${state.root ? '<button type="button" class="primary" id="btn-empty-new">＋ 新しいステートマシン</button>' : '<button type="button" class="primary" id="btn-empty-root">フォルダを選ぶ</button>'}
        <button type="button" id="btn-empty-yaml">既存の workflow.yaml を開く</button>
      </div></div>`;
    const n = $('btn-empty-new'); if (n) n.addEventListener('click', newMachine);
    const r = $('btn-empty-root'); if (r) r.addEventListener('click', chooseRoot);
    const y = $('btn-empty-yaml'); if (y) y.addEventListener('click', chooseWorkflow);
    return;
  }
  const spec = state.current.spec;
  root.innerHTML = `${headHtml(spec)}
    <section class="flow" id="flow" aria-label="工程のフロー">${flowHtml(spec)}</section>
    ${statusHtml()}`;
  bindHead();
  bindFlow();
  bindStatus();
}

function headHtml(spec) {
  const cur = state.current;
  return `<section class="machine-head">
    <div class="grid3">
      <div class="field"><label for="m-name">名前</label><input id="m-name" data-top="name" value="${esc(spec.name)}" placeholder="例: 月次の勤怠集計"></div>
      <div class="field"><label for="m-machine">識別名（フォルダ名）</label><input id="m-machine" data-top="machine" class="mono" value="${esc(spec.machine)}" placeholder="英数字・ハイフン" ${cur.isNew ? '' : 'readonly title="既存の定義の識別名は変えられません"'}></div>
      <div class="field"><label for="m-max">最大遷移数</label><input id="m-max" data-top="maxSteps" type="number" min="1" max="500" value="${esc(spec.maxSteps)}"></div>
    </div>
    <div class="field"><label for="m-purpose">目的</label><textarea id="m-purpose" data-top="purpose" rows="2" placeholder="例: 毎月 1 日に勤怠システムから月次集計を出力し、異常値があれば差し戻し候補を一覧にする">${esc(spec.purpose)}</textarea></div>
    <details><summary>終了条件・注意事項（AI 補完に渡す補足）</summary>
      <div class="row2" style="margin-top:8px">
        <div class="field"><label for="m-finish">終了条件</label><textarea id="m-finish" data-top="finish" rows="2">${esc(spec.finish)}</textarea></div>
        <div class="field"><label for="m-notes">注意事項</label><textarea id="m-notes" data-top="notes" rows="2">${esc(spec.notes)}</textarea></div>
      </div></details>
    <div class="head-status">
      <span class="muted small">${cur.isNew ? '新規（保存すると .statemachine/ に書かれます）' : `保存先: <span class="mono">${esc(cur.dir)}</span>`}</span>
      <span id="dirty-mark" class="dirty" ${cur.dirty ? '' : 'hidden'}>● 未保存</span>
    </div>
  </section>`;
}

function bindHead() {
  const spec = state.current.spec;
  let machineTouched = !state.current.isNew || !!spec.machine;
  for (const el of document.querySelectorAll('[data-top]')) {
    el.addEventListener('input', () => {
      const key = el.dataset.top;
      spec[key] = key === 'maxSteps' ? Number(el.value) || 30 : el.value;
      if (key === 'machine') machineTouched = true;
      if (key === 'name' && !machineTouched) { spec.machine = machineIdFrom(el.value); $('m-machine').value = spec.machine; }
      markDirty();
    });
  }
}

function cardHtml(spec, index) {
  const step = spec.steps[index];
  const kind = kindOf(step.kind);
  const trans = transitionsOf(spec, index);
  const what = step.kind === 'command' ? step.target : step.detail;
  const target = step.kind !== 'command' && step.target ? `<span class="target">${esc(kind.target ? kind.target.label : '対象')}: ${esc(step.target)}</span>\n` : '';
  return `<article class="flow-card k-${esc(step.kind)} ${index === state.selected ? 'is-selected' : ''}" data-step="${index}" tabindex="0">
    <div class="card-head">
      <span class="card-num">${index + 1}</span>
      <span class="card-title">${step.title ? esc(step.title) : `<span class="placeholder">${esc(kind.label)}</span>`}</span>
      <span class="card-id mono">${esc(step.id || '')}</span>
      <span class="kind-chip k-${esc(step.kind)}">${esc(kind.short)}</span>
      <span class="card-tools">
        <button type="button" data-move="up" title="上へ" ${index === 0 ? 'disabled' : ''}>↑</button>
        <button type="button" data-move="down" title="下へ" ${index === spec.steps.length - 1 ? 'disabled' : ''}>↓</button>
        <button type="button" data-remove title="削除" class="danger">✕</button>
      </span>
    </div>
    <div class="card-body">
      <div class="card-what">${target}${what ? esc(what) : '<span class="muted">（内容を右の欄に入力）</span>'}</div>
      <div class="card-meta">
        ${step.check ? `<span class="check">✓ 検査: <span class="mono">${esc(step.check)}</span></span>` : ''}
        ${step.recorded && step.recorded.length ? `<span class="rec">● 記録した操作 ${step.recorded.length} 件</span>` : ''}
      </div>
      <div class="card-trans">${trans.map((t) => `<div class="t"><span class="lbl ${t.cls}">${esc(t.label)}</span><span class="arrow">→</span><span class="to ${['ok', 'ng', 'gate', 'raw'].includes(t.cls) ? '' : t.cls}">${esc(t.text)}</span></div>`).join('')}</div>
    </div>
  </article>`;
}

function addMenuHtml(at) {
  return `<div class="add-menu" data-at="${at}">${state.catalog.kinds.map((k) =>
    `<button type="button" data-add="${esc(k.id)}" title="${esc(k.description)}"><span class="dot k-${esc(k.id)}"></span>${esc(k.label)}</button>`).join('')}</div>`;
}

function flowHtml(spec) {
  if (!spec.steps.length) {
    return `<div class="empty" style="padding:24px"><h3>工程を追加します</h3><p>種類を選んで最初の工程を置くか、右の「記録」タブで人の操作から起こします。</p>${addMenuHtml(0)}</div>
      <div class="terminals">${terminalsHtml(spec)}</div>`;
  }
  const parts = [];
  spec.steps.forEach((_s, i) => {
    parts.push(cardHtml(spec, i));
    parts.push(`<div class="connector"><div class="add"><button type="button" data-insert="${i + 1}" title="この間に工程を追加">＋</button></div><div class="arrowhead"></div></div>`);
  });
  parts.push(`<div class="terminals">${terminalsHtml(spec)}</div>`);
  return parts.join('');
}

function terminalsHtml(spec) {
  return `<div class="terminal-card done">${esc(spec.terminals.done.description)}<small class="mono">${esc(spec.terminals.done.id)}</small></div>
    <div class="terminal-card abort">${esc(spec.terminals.abort.description)}<small class="mono">${esc(spec.terminals.abort.id)}</small></div>`;
}

function bindFlow() {
  const flow = $('flow');
  for (const card of flow.querySelectorAll('[data-step]')) bindCard(card);
  for (const btn of flow.querySelectorAll('[data-add]')) {
    btn.addEventListener('click', () => insertStep(Number(btn.closest('[data-at]').dataset.at), btn.dataset.add));
  }
  for (const btn of flow.querySelectorAll('[data-insert]')) {
    btn.addEventListener('click', () => {
      const at = Number(btn.dataset.insert);
      const holder = btn.closest('.connector');
      if (holder.querySelector('.add-menu')) { holder.querySelector('.add-menu').remove(); return; }
      holder.insertAdjacentHTML('beforeend', addMenuHtml(at));
      for (const b of holder.querySelectorAll('[data-add]')) {
        b.addEventListener('click', () => insertStep(at, b.dataset.add));
      }
    });
  }
}

function bindCard(card) {
  const spec = state.current.spec;
  const index = Number(card.dataset.step);
  card.addEventListener('click', (ev) => {
    if (ev.target.closest('button')) return;
    selectStep(index);
  });
  card.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' && ev.target === card) selectStep(index); });
  for (const btn of card.querySelectorAll('[data-move]')) {
    btn.addEventListener('click', () => {
      const to = btn.dataset.move === 'up' ? index - 1 : index + 1;
      if (to < 0 || to >= spec.steps.length) return;
      const [moved] = spec.steps.splice(index, 1);
      spec.steps.splice(to, 0, moved);
      retargetOutcomes(spec, index, to);
      state.selected = to;
      markDirty();
      renderEditor();
      renderPanel();
    });
  }
  for (const btn of card.querySelectorAll('[data-remove]')) {
    btn.addEventListener('click', () => {
      if (!confirm(`工程 ${index + 1} を削除しますか？`)) return;
      spec.steps.splice(index, 1);
      dropOutcomesTo(spec, index);
      state.selected = Math.min(index, spec.steps.length - 1);
      markDirty();
      renderEditor();
      renderPanel();
    });
  }
}

// 工程を動かしたとき、`step:n` の行き先番号を追随させる。
function retargetOutcomes(spec, from, to) {
  const map = (n) => {
    const i = n - 1;
    if (i === from) return to + 1;
    if (from < to && i > from && i <= to) return n - 1;
    if (from > to && i >= to && i < from) return n + 1;
    return n;
  };
  for (const s of spec.steps) for (const o of s.outcomes) if (o.to.startsWith('step:')) o.to = `step:${map(Number(o.to.slice(5)))}`;
}

function dropOutcomesTo(spec, removed) {
  for (const s of spec.steps) {
    for (const o of s.outcomes) {
      if (!o.to.startsWith('step:')) continue;
      const n = Number(o.to.slice(5)) - 1;
      if (n === removed) o.to = 'next';
      else if (n > removed) o.to = `step:${n}`;
    }
  }
}

function insertStep(at, kindId) {
  const spec = state.current.spec;
  spec.steps.splice(at, 0, emptyStep(kindId));
  for (const s of spec.steps) for (const o of s.outcomes) {
    if (o.to.startsWith('step:') && Number(o.to.slice(5)) - 1 >= at) o.to = `step:${Number(o.to.slice(5)) + 1}`;
  }
  state.selected = at;
  state.tab = 'step';
  markDirty();
  renderEditor();
  renderPanel();
  const first = document.querySelector('#panel input, #panel textarea');
  if (first) first.focus();
}

function selectStep(index) {
  state.selected = index;
  state.tab = 'step';
  for (const card of document.querySelectorAll('[data-step]')) card.classList.toggle('is-selected', Number(card.dataset.step) === index);
  renderPanel();
}

// 入力に追随してカードの文言だけ差し替える（描き直さない）。
function refreshCard(index) {
  const card = document.querySelector(`[data-step="${index}"]`);
  if (!card) return;
  card.outerHTML = cardHtml(state.current.spec, index);
  bindCard(document.querySelector(`[data-step="${index}"]`));
}

// --- 下部: 状態と保存 ---------------------------------------------------------------------

function statusHtml() {
  const cur = state.current;
  const warnings = [...(cur.warnings || []), ...((state.preview && state.preview.warnings) || [])];
  const errors = (state.preview && state.preview.errors) || [];
  return `<section class="status-bar">
    <div class="row">
      <div class="row">
        <button type="button" id="btn-save" class="primary">保存（.statemachine/ に書く）</button>
        <button type="button" id="btn-check">検査だけ行う</button>
        ${cur.isNew ? '' : '<button type="button" id="btn-folder">フォルダを開く</button>'}
      </div>
      <span class="muted small">${cur.spec.steps.length} 工程</span>
    </div>
    <div id="status-msg">
      ${errors.length ? `<p class="msg error">定義が検証を通りません:</p><ul class="plain msg error">${errors.map((e) => `<li>${esc(e)}</li>`).join('')}</ul>` : ''}
      ${warnings.length ? `<ul class="plain msg warn">${warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}
      ${state.preview && !errors.length ? '<p class="msg ok">構造の検査を通りました。スキルの --dry-run は「実行」タブから行えます。</p>' : ''}
    </div>
  </section>`;
}

function bindStatus() {
  $('btn-save').addEventListener('click', saveMachine);
  $('btn-check').addEventListener('click', () => previewMachine(true));
  const f = $('btn-folder');
  if (f) f.addEventListener('click', () => api.openMachineFolder(state.root, state.current.machine));
}

async function previewMachine(showToast = false) {
  if (!state.current) return null;
  const res = await guard('検査', () => api.previewMachine(specPayload()));
  if (!res) return null;
  state.preview = res;
  const bar = document.querySelector('.status-bar');
  if (bar) { bar.outerHTML = statusHtml(); bindStatus(); }
  if (showToast) toast(res.errors.length ? `検証エラー ${res.errors.length} 件` : '検査を通りました', !!res.errors.length);
  if (state.tab === 'files') renderPanel();
  return res;
}

async function saveMachine() {
  if (!state.current) return;
  const preview = await previewMachine(false);
  if (!preview || preview.errors.length) { toast('検証エラーがあるので保存しません', true); return; }
  const payload = specPayload();
  if (state.current.isNew) {
    const exists = await guard('確認', () => api.machineExists(state.root, payload.machine));
    if (exists && !confirm(`.statemachine/${payload.machine}/ は既にあります。上書きしますか？`)) return;
  }
  const res = await guard('保存', () => api.saveMachine(state.root, payload));
  if (!res) return;
  state.current.spec.machine = res.machine;
  state.current.machine = res.machine;
  state.current.isNew = false;
  state.current.dirty = false;
  state.current.dir = res.dir;
  state.current.warnings = res.warnings || [];
  toast(`保存しました: ${res.dir}`);
  await loadMachines();
  renderEditor();
  renderPanel();
}

// --- 右パネル -----------------------------------------------------------------------------

function renderTabs() {
  for (const b of document.querySelectorAll('#tabs button')) b.classList.toggle('is-active', b.dataset.tab === state.tab);
}

function renderPanel() {
  renderTabs();
  const panel = $('panel');
  if (!state.current) { panel.innerHTML = '<p class="muted">定義を開くと、ここで工程を編集します。</p>'; return; }
  const render = { step: stepPanel, record: recordPanel, files: filesPanel, ai: aiPanel, run: runPanel, tools: toolsPanel }[state.tab] || stepPanel;
  render(panel);
}

function stepPanel(panel) {
  const spec = state.current.spec;
  const index = state.selected;
  const step = spec.steps[index];
  if (!step) {
    panel.innerHTML = `<p class="muted">中央の工程を選ぶと編集できます。</p>${addMenuHtml(spec.steps.length)}`;
    for (const b of panel.querySelectorAll('[data-add]')) b.addEventListener('click', () => insertStep(spec.steps.length, b.dataset.add));
    return;
  }
  const kind = kindOf(step.kind);
  const kindOptions = state.catalog.kinds.map((k) => `<option value="${esc(k.id)}" ${k.id === step.kind ? 'selected' : ''}>${esc(k.label)}</option>`).join('');
  const targetHtml = kind.target ? `<div class="field"><label for="s-target">${esc(kind.target.label)}${kind.target.required ? '' : '（任意）'}</label>
    <input id="s-target" data-field="target" class="mono" value="${esc(step.target)}" placeholder="${esc(kind.target.placeholder || '')}"></div>` : '';
  const checkHtml = kind.check ? `<div class="field"><label for="s-check">完了の確認コマンド（任意・終了コード 0 で通過）</label>
    <input id="s-check" data-field="check" class="mono" value="${esc(step.check)}" placeholder="${esc(kind.check.placeholder || '')}">
    <small>ハーネスがシェルを介さず実行します（パイプ・リダイレクト不可）。宣言すると、モデルの OK ではなく検査の結果で次へ進みます。</small>
    <div class="row" style="margin-top:4px"><label for="s-retries" class="muted small">検査が落ちたときのやり直し回数</label><input id="s-retries" data-field="checkRetries" type="number" min="0" max="5" value="${esc(step.checkRetries)}" style="width:70px"></div></div>` : '';
  const destOptions = (to) => {
    const opts = [['next', index + 1 < spec.steps.length ? `次へ（工程 ${index + 2}）` : '次へ（完了）'], ['done', '完了として終了'], ['abort', '失敗として終了']];
    spec.steps.forEach((s, i) => { opts.push([`step:${i + 1}`, `工程 ${i + 1} へ${i < index ? '戻る' : i === index ? '（やり直す）' : ''}`]); });
    return opts.map(([v, l]) => `<option value="${v}" ${v === to ? 'selected' : ''}>${esc(l)}</option>`).join('');
  };
  const outcomesHtml = step.outcomes.map((o, i) => `<div class="outcome-row" data-outcome="${i}">
    <input data-ofield="label" class="mono" value="${esc(o.label)}" placeholder="ラベル（例: APPROVED）">
    <select data-ofield="to">${destOptions(o.to)}</select>
    <button type="button" data-oremove title="削除">✕</button></div>`).join('');
  const recordedHtml = step.recorded && step.recorded.length ? `<div class="panel-section"><h3>記録した操作（${step.recorded.length} 件）</h3>
    <ol class="recorded-list mono">${step.recorded.map((op) => `<li>${esc(op.op)} ${esc(op.label || op.target)}${op.value ? ` ${esc(op.value)}` : ''}${op.example ? ` <span class="muted">(例: ${esc(op.example)})</span>` : ''}</li>`).join('')}</ol>
    <button type="button" class="small" id="s-unrecord" style="margin-top:6px">記録を外す（内容の文章だけ残す）</button></div>` : '';
  const outcomesSection = step.rawTransitions
    ? `<div class="panel-section"><h3>遷移</h3>
      <p class="muted small" style="margin:0 0 8px">この工程の遷移は YAML に自然言語条件や無条件遷移で書かれているため、原文のまま保持しています（保存しても変わりません）。画面で判断として編集したいときは、既定の OK / FAILED に置き換えます。</p>
      <button type="button" class="small" id="s-unraw">画面で編集できる形にする（原文の遷移を捨てる）</button></div>`
    : `<div class="panel-section"><h3>判断（出力の第 1 行で分岐する）</h3>
      <p class="muted small" style="margin:0 0 8px">空欄なら OK → 次へ、FAILED → 失敗として終了。ラベルを置くと、その語で始まる出力ごとに行き先を決めます。分岐は遷移（transitions）に書かれ、本文には書きません。</p>
      <div id="s-outcomes">${outcomesHtml}</div>
      <button type="button" class="small" id="s-add-outcome">＋ 判断を追加</button>
    </div>`;
  panel.innerHTML = `
    <div class="panel-section"><h3>工程 ${index + 1}</h3>
      <div class="field"><label for="s-kind">種類</label><select id="s-kind" data-field="kind">${kindOptions}</select><small>${esc(kind.description)}</small></div>
      <div class="row2">
        <div class="field"><label for="s-title">名前（任意）</label><input id="s-title" data-field="title" value="${esc(step.title)}" placeholder="例: 申請一覧を開く"></div>
        <div class="field"><label for="s-id">ステート ID</label><input id="s-id" data-field="id" class="mono" value="${esc(step.id)}" placeholder="step_${index + 1}"></div>
      </div>
      ${targetHtml}
      <div class="field"><label for="s-detail">${esc(kind.detail.label)}${kind.detail.required ? '' : '（任意）'}</label>
        <textarea id="s-detail" data-field="detail" rows="6" placeholder="${esc(kind.detail.placeholder || '')}">${esc(step.detail)}</textarea>
        <small>本文の <code>{{key}}</code> は実行時に人が入れる入力パラメータになります。</small></div>
      ${recordedHtml}
      ${checkHtml}
    </div>
    ${outcomesSection}
    <div class="panel-section row">
      <button type="button" id="s-move-up" ${index === 0 ? 'disabled' : ''}>↑ 上へ</button>
      <button type="button" id="s-move-down" ${index === spec.steps.length - 1 ? 'disabled' : ''}>↓ 下へ</button>
      <button type="button" id="s-remove" class="danger">削除</button>
    </div>`;
  bindStepPanel(panel, step, index);
}

function bindStepPanel(panel, step, index) {
  const spec = state.current.spec;
  for (const el of panel.querySelectorAll('[data-field]')) {
    const handler = () => {
      const key = el.dataset.field;
      if (key === 'kind') {
        const next = kindOf(el.value);
        step.kind = el.value;
        if (!next.target) step.target = '';
        if (!next.check) step.check = '';
        if (!next.recordable) step.recorded = [];
        markDirty();
        renderPanel();
        refreshCard(index);
        return;
      }
      step[key] = key === 'checkRetries' ? Number(el.value) : el.value;
      markDirty();
      refreshCard(index);
    };
    el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', handler);
  }
  for (const row of panel.querySelectorAll('[data-outcome]')) {
    const i = Number(row.dataset.outcome);
    for (const el of row.querySelectorAll('[data-ofield]')) {
      el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', () => {
        step.outcomes[i][el.dataset.ofield] = el.value;
        markDirty();
        refreshCard(index);
      });
    }
    row.querySelector('[data-oremove]').addEventListener('click', () => {
      step.outcomes.splice(i, 1);
      markDirty();
      renderPanel();
      refreshCard(index);
    });
  }
  const addOutcome = $('s-add-outcome');
  if (addOutcome) addOutcome.addEventListener('click', () => {
    step.outcomes.push({ label: '', to: 'next' });
    markDirty();
    renderPanel();
    const inputs = panel.querySelectorAll('[data-outcome] input');
    if (inputs.length) inputs[inputs.length - 1].focus();
  });
  const unraw = $('s-unraw');
  if (unraw) unraw.addEventListener('click', () => {
    if (!confirm('この工程の原文の遷移を捨てて、既定の OK / FAILED に置き換えますか？')) return;
    step.rawTransitions = false;
    if (spec.preserved && Array.isArray(spec.preserved.transitions)) {
      spec.preserved.transitions = spec.preserved.transitions.filter((t) => !(t && t.from === step.id));
    }
    markDirty();
    renderPanel();
    refreshCard(index);
  });
  const un = $('s-unrecord');
  if (un) un.addEventListener('click', () => { step.recorded = []; markDirty(); renderPanel(); refreshCard(index); });
  $('s-move-up').addEventListener('click', () => document.querySelector(`[data-step="${index}"] [data-move="up"]`).click());
  $('s-move-down').addEventListener('click', () => document.querySelector(`[data-step="${index}"] [data-move="down"]`).click());
  $('s-remove').addEventListener('click', () => document.querySelector(`[data-step="${index}"] [data-remove]`).click());
  for (const s of spec.steps) if (!s.id) assignIds(spec);
}

// --- 記録 -----------------------------------------------------------------------------

function recordPanel(panel) {
  const rec = state.recording;
  const kinds = state.catalog.kinds.filter((k) => k.recordable);
  const windows = rec.source === 'windows';
  const onWindows = state.catalog.platform === 'win32';
  const target = windows
    ? { field: 'app', value: rec.app, label: 'アプリ（ウィンドウ名・プロセス名・PID）', placeholder: '例: 勤怠管理' }
    : { field: 'url', value: rec.url, label: '記録を始める URL', placeholder: 'https://…' };
  panel.innerHTML = `
    <div class="panel-section"><h3>人の操作を記録して工程に起こす</h3>
      <p class="muted small" style="margin:0 0 8px">要素は名前と種類で残し、入力した値は <code>{{key}}</code> の入力パラメータに置き換えます（パスワードらしい値は例にも残しません）。起こした工程は末尾に足すので、名前・内容・判断を直してから保存します。</p>
      <div class="field"><label for="r-source">記録の種類</label>
        <select id="r-source" ${rec.active ? 'disabled' : ''}>${kinds.map((k) => `<option value="${esc(k.id)}" ${rec.source === k.id ? 'selected' : ''}>${esc(k.label)}</option>`).join('')}</select></div>
      <div class="field"><label for="r-target">${esc(target.label)}</label>
        <input id="r-target" class="mono" data-rec="${target.field}" value="${esc(target.value)}" placeholder="${esc(target.placeholder)}" ${rec.active ? 'disabled' : ''}></div>
      <div class="row">
        <button type="button" id="r-start" ${rec.active || rec.busy || (windows && !onWindows) ? 'disabled' : ''}>記録を開始</button>
        <button type="button" id="r-stop" class="primary" ${!rec.active || rec.busy ? 'disabled' : ''}>記録を終了して工程に起こす</button>
      </div>
      <p class="muted small">${windows
    ? (onWindows ? 'この端末で <code>winauto record</code> を走らせます。アプリを操作してから終了を押します。' : 'Windows アプリの記録は Windows 上でだけ取れます。別の端末で取った記録を下に貼り付けてください。')
    : '見える形でブラウザが開きます（<code>playwright-cli</code>）。開かないときは下の貼り付けを使ってください。'}</p>
      <p id="r-message" class="msg ${rec.ok ? '' : 'error'}" ${rec.message ? '' : 'hidden'}>${esc(rec.message)}</p>
    </div>
    <div class="panel-section"><h3>別の端末で取った記録を貼り付ける</h3>
      <small class="muted">${windows
    ? `対象の PC で <code>winauto record --app ${esc(rec.app || '<アプリ>')} --output events.jsonl</code> を実行し、操作して Ctrl+C で止め、できたファイルの中身を貼り付けます。`
    : '<code>playwright-cli recording-start</code> → 操作 → <code>recording-stop</code> が印字した内容を貼り付けます。'}</small>
      <textarea id="r-text" class="mono" rows="6" placeholder="${windows ? '{&quot;event&quot;:&quot;invoke&quot;,&quot;app&quot;:&quot;勤怠管理&quot;,...}' : 'await page.getByRole(\'button\', { name: \'ログイン\' }).click();'}">${esc(rec.text)}</textarea>
      <div class="row" style="margin-top:6px"><button type="button" id="r-import" ${rec.busy ? 'disabled' : ''}>貼り付けた記録を工程に起こす</button></div>
    </div>`;
  $('r-source').addEventListener('change', (e) => { rec.source = e.target.value; renderPanel(); });
  $('r-target').addEventListener('input', (e) => { rec[e.target.dataset.rec] = e.target.value; });
  $('r-text').addEventListener('input', (e) => { rec.text = e.target.value; });
  $('r-start').addEventListener('click', () => recordingAction('start'));
  $('r-stop').addEventListener('click', () => recordingAction('stop'));
  $('r-import').addEventListener('click', () => recordingAction('import'));
}

async function recordingAction(action) {
  const rec = state.recording;
  if (rec.busy) return;
  const payload = { root: state.root, source: rec.source, url: rec.url, app: rec.app };
  if (action === 'import') {
    payload.text = rec.text;
    if (!String(rec.text || '').trim()) { rec.message = '記録を貼り付けてください'; rec.ok = false; renderPanel(); return; }
  }
  rec.busy = true;
  rec.ok = true;
  rec.message = action === 'start' ? (rec.source === 'windows' ? '記録を開始しています…' : 'ブラウザを開いています…') : '記録を工程に起こしています…';
  renderPanel();
  let res;
  try {
    res = action === 'start' ? await api.recordingStart(payload) : action === 'stop' ? await api.recordingStop(payload) : await api.recordingImport(payload);
  } catch (err) {
    res = { error: String((err && err.message) || err) };
  }
  rec.busy = false;
  if (!res || res.error) {
    rec.message = `${action === 'start' ? '記録を開始できませんでした' : '記録を工程に起こせませんでした'}: ${(res && res.error) || '原因不明'}`;
    rec.ok = false;
    if (action === 'stop') rec.active = false;
    renderPanel();
    return;
  }
  if (action === 'start') {
    rec.active = true;
    rec.message = rec.source === 'windows' ? 'winauto が記録中です。アプリを操作してから「記録を終了して工程に起こす」を押します' : '開いたブラウザで操作してください。終わったら「記録を終了して工程に起こす」を押します';
    renderPanel();
    return;
  }
  if (action === 'stop') rec.active = false;
  if (action === 'import') rec.text = '';
  const spec = state.current.spec;
  const steps = Array.isArray(res.steps) ? res.steps : [];
  for (const s of steps) spec.steps.push({ ...emptyStep(s.kind), ...s });
  assignIds(spec);
  markDirty();
  const params = res.parameters && res.parameters.length ? `。入力パラメータの候補: ${res.parameters.map((k) => `{{${k}}}`).join(' ')}` : '';
  rec.message = `${res.operations || 0} 件の操作から ${steps.length} 工程を起こしました${params}`;
  rec.ok = true;
  state.selected = spec.steps.length - steps.length;
  renderEditor();
  renderPanel();
}

// --- 定義（生成されるファイル） ------------------------------------------------------------

async function filesPanel(panel) {
  if (!state.preview) {
    panel.innerHTML = '<p class="muted">コンパイルしています…</p>';
    const res = await previewMachine(false);
    if (!res) { panel.innerHTML = '<p class="msg error">コンパイルできませんでした</p>'; return; }
    if (state.tab !== 'files') return;
  }
  const files = state.preview.files || {};
  const names = Object.keys(files);
  if (!names.includes(state.fileTab)) state.fileTab = names[0] || '';
  panel.innerHTML = `<div class="panel-section"><h3>保存すると書かれるファイル（.statemachine/${esc(state.current.spec.machine || machineIdFrom(state.current.spec.name))}/）</h3>
    ${state.preview.errors && state.preview.errors.length ? `<ul class="plain msg error">${state.preview.errors.map((e) => `<li>${esc(e)}</li>`).join('')}</ul>` : ''}
    <div class="file-tabs">${names.map((n) => `<button type="button" data-file="${esc(n)}" class="${n === state.fileTab ? 'is-active' : ''}">${esc(n)}</button>`).join('')}</div>
    <pre>${esc(files[state.fileTab] || '')}</pre>
    <p class="muted small">書式の正典は statemachine-use スキル（references/schema.md）。maker.json はこのツールが読み戻すための写しで、実行には使いません。</p></div>`;
  for (const b of panel.querySelectorAll('[data-file]')) b.addEventListener('click', () => { state.fileTab = b.dataset.file; renderPanel(); });
}

// --- AI 補完（作成モードへ渡す指示文） -----------------------------------------------------

async function aiPanel(panel) {
  if (!state.ai) {
    panel.innerHTML = '<p class="muted">指示文を組んでいます…</p>';
    const res = await guard('指示文', () => api.instruction(state.root, specPayload()));
    if (!res) { panel.innerHTML = '<p class="msg error">指示文を組めませんでした（名前と工程を確認してください）</p>'; return; }
    state.ai = res;
    if (state.tab !== 'ai') return;
  }
  panel.innerHTML = `<div class="panel-section"><h3>statemachine-use の作成モードに補完させる</h3>
    <p class="muted small" style="margin:0 0 8px">このツールが書いた定義は AI 無しで動きます。待機・読み取り・想定外の画面の扱いを AI に補わせたいときは、この指示文をエージェント CLI（claude / copilot / kiro など）に貼ります。先に保存しておくと、既存の定義を読んで直す指示になります。</p>
    <div class="row" style="margin-bottom:8px">
      <button type="button" id="ai-copy" class="primary">指示文をコピー</button>
      <button type="button" id="ai-terminal">フォルダで端末を開く</button>
    </div>
    <pre>${esc(state.ai.prompt)}</pre></div>`;
  $('ai-copy').addEventListener('click', async () => { await api.copyText(state.ai.prompt); toast('指示文をコピーしました。エージェント CLI に貼り付けてください'); });
  $('ai-terminal').addEventListener('click', () => guard('端末', () => api.openTerminal(state.root)));
}

// --- 実行 -----------------------------------------------------------------------------

async function runPanel(panel) {
  const run = state.run;
  const cur = state.current;
  if (!run.command && !cur.isNew) run.command = await guard('コマンド', () => api.runCommand(state.root, cur.machine));
  if (state.tab !== 'run') return;
  const agent = run.agent || state.config.agent || 'claude';
  panel.innerHTML = `<div class="panel-section"><h3>スキルのスクリプトで検証・実行する</h3>
    ${cur.isNew || cur.dirty ? '<p class="msg warn">保存してから実行してください（実行するのは保存された定義です）。</p>' : ''}
    <div class="row" style="margin-bottom:8px">
      <button type="button" id="run-dry" ${cur.isNew || run.running ? 'disabled' : ''}>検証（--dry-run）</button>
      <button type="button" id="run-go" class="primary" ${cur.isNew || run.running ? 'disabled' : ''}>実行</button>
      <button type="button" id="run-stop" class="danger" ${run.running ? '' : 'disabled'}>停止</button>
    </div>
    <div class="row2">
      <div class="field"><label for="run-agent">エージェント（--agent）</label>
        <select id="run-agent">${['claude', 'copilot', 'kiro', 'anthropic'].map((a) => `<option value="${a}" ${a === agent ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
      <div class="field"><label for="run-input">入力（--input・任意）</label><input id="run-input" value="${esc(run.input)}" placeholder="{{input}} に入る文"></div>
    </div>
    <div class="log" id="run-log">${run.lines.map((l) => `<div class="${l.kind === 'stderr' ? 'err' : ''}">${esc(l.line)}</div>`).join('') || '<span class="muted">出力はここに流れます</span>'}</div>
    ${run.command ? `<p class="muted small" style="margin-top:8px">このツール無しで動かすには、<span class="mono">${esc(run.command.cwd)}</span> で:<br>
      <code>${esc(run.command.dryRun)}</code><br><code>${esc(run.command.run)}</code></p>` : ''}
  </div>`;
  $('run-agent').addEventListener('change', (e) => { run.agent = e.target.value; });
  $('run-input').addEventListener('input', (e) => { run.input = e.target.value; });
  $('run-dry').addEventListener('click', () => startRun('dry-run'));
  $('run-go').addEventListener('click', () => startRun('run'));
  $('run-stop').addEventListener('click', () => api.runStop());
}

async function startRun(mode) {
  const run = state.run;
  run.lines = [];
  run.running = true;
  run.mode = mode;
  renderPanel();
  const res = await guard('実行', () => api.runStart({
    root: state.root, machine: state.current.machine, mode, agent: run.agent || state.config.agent, input: run.input,
  }));
  if (!res) { run.running = false; renderPanel(); return; }
  run.lines.push({ kind: 'stdout', line: `$ ${res.command}` });
  renderPanel();
}

function appendLog(entry) {
  state.run.lines.push(entry);
  if (state.run.lines.length > 2000) state.run.lines.shift();
  const log = $('run-log');
  if (!log) return;
  const div = document.createElement('div');
  if (entry.kind === 'stderr') div.className = 'err';
  div.textContent = entry.line;
  if (log.firstChild && log.firstChild.tagName === 'SPAN') log.innerHTML = '';
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

// --- 道具・設定 ------------------------------------------------------------------------

async function toolsPanel(panel) {
  const cfg = state.config;
  panel.innerHTML = `<div class="panel-section"><h3>道具の確認（LLM は使いません）</h3>
    <div class="row" style="margin-bottom:8px"><button type="button" id="tools-check">確認する</button><span class="muted small">python / スキルのスクリプト / playwright-cli / winauto</span></div>
    <div id="tools-list">${state.tools ? toolsListHtml(state.tools) : '<p class="muted small">「確認する」で診断します。</p>'}</div></div>
    <div class="panel-section"><h3>設定</h3>
      <div class="field"><label for="cfg-skill">statemachine-use スキルのフォルダ（自動で見つからないとき）</label>
        <input id="cfg-skill" class="mono" value="${esc(cfg.skillDir || '')}" placeholder="例: C:/work/sandbox/.github/skills/statemachine-use"></div>
      <div class="field"><label for="cfg-agent">実行に使うエージェント（既定）</label>
        <select id="cfg-agent">${['claude', 'copilot', 'kiro', 'anthropic'].map((a) => `<option value="${a}" ${a === (cfg.agent || 'claude') ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
      <div class="field"><label for="cfg-model">モデル（任意・--model）</label><input id="cfg-model" class="mono" value="${esc(cfg.model || '')}"></div>
      <button type="button" id="cfg-save">設定を保存</button>
    </div>`;
  $('tools-check').addEventListener('click', async () => {
    const btn = $('tools-check');
    btn.disabled = true; btn.textContent = '確認中…';
    const res = await guard('道具の確認', () => api.toolStatus(state.root));
    state.tools = res || state.tools;
    renderPanel();
  });
  $('cfg-save').addEventListener('click', async () => {
    const next = { ...cfg, skillDir: $('cfg-skill').value.trim(), agent: $('cfg-agent').value, model: $('cfg-model').value.trim() };
    const saved = await guard('設定の保存', () => api.saveConfig(next));
    if (saved) { state.config = saved; toast('設定を保存しました'); }
  });
}

function toolsListHtml(tools) {
  return `<ul class="tool-list">${tools.map((t) => `<li><span class="st ${t.ok ? 'ok' : 'ng'}">${t.ok ? '利用可能' : '未準備'}</span><strong>${esc(t.label)}</strong>
    <small>${esc(t.summary || '')}</small>${t.hint ? `<small>${esc(t.hint)}</small>` : ''}</li>`).join('')}</ul>`;
}

// --- 起動 -----------------------------------------------------------------------------

async function chooseRoot() {
  const root = await guard('フォルダの選択', () => api.chooseRoot());
  if (root) await setRoot(root);
}

async function chooseWorkflow() {
  const res = await guard('workflow.yaml', () => api.chooseWorkflow());
  if (!res) return;
  await setRoot(res.root);
  await openMachine(res.machine);
}

async function init() {
  state.catalog = (await guard('種類の取得', () => api.catalog())) || state.catalog;
  state.config = (await guard('設定', () => api.getConfig())) || {};
  renderRoot();
  renderSidebar();
  renderEditor();
  renderPanel();
  $('btn-root').addEventListener('click', chooseRoot);
  $('btn-open-yaml').addEventListener('click', chooseWorkflow);
  $('btn-new').addEventListener('click', newMachine);
  $('btn-reload').addEventListener('click', loadMachines);
  $('recent-roots').addEventListener('change', (e) => { if (e.target.value) setRoot(e.target.value); });
  for (const b of document.querySelectorAll('#tabs button')) {
    b.addEventListener('click', () => { state.tab = b.dataset.tab; renderPanel(); });
  }
  api.onRunLine((p) => appendLog(p));
  api.onRunExit((p) => {
    state.run.running = false;
    appendLog({ kind: p.code === 0 ? 'stdout' : 'stderr', line: `--- 終了（コード ${p.code}）${p.mode === 'dry-run' ? (p.code === 0 ? ' 定義は有効です' : ' 検証に失敗しました') : ''}` });
    if (state.tab === 'run') renderPanel();
  });
  window.addEventListener('beforeunload', (e) => {
    if (state.current && state.current.dirty) { e.preventDefault(); e.returnValue = ''; }
  });
  const recent = (state.config.recentRoots || [])[0];
  if (recent) await setRoot(recent);
}

init();
