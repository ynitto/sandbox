'use strict';

// 画面は 2 つだけ。一覧（フォルダの .statemachine/*）と、1 列の編集。
//   - 編集は Zapier 型: 工程カードを縦に 1 列に並べ、選んだカードだけがその場で開いて設定欄になる。
//   - 畳んだカードは Shortcuts 型: 動詞で始まる 1 文の要約と、補足（種類・対象・検査・記録）だけ。
//   - カードの間に遷移（ラベル → 行き先）を置く。分岐は横に広げず縦に積む（Power Automate の新デザイナー型）。
// 記録・定義・AI 補完・実行・設定はダイアログ。コンパイル・検査・読み戻し・記録の変換は main に頼む。
// 色・余白・文字は CSS 変数で、設定（theme.json）とユーザー CSS（custom.css）が上書きする。

const api = window.api;
const $ = (id) => document.getElementById(id);

const state = {
  root: '', machines: [], catalog: { kinds: [], platform: '' }, config: {}, theme: null,
  view: 'home',
  current: null,     // { machine, isNew, spec, dirty, warnings, dir }
  open: -1,          // 開いている工程
  pickerAt: -1,      // 挿入の種類選択を開いている位置
  preview: null, ai: null, tools: null,
  recording: { source: 'browser', url: '', app: '', text: '', active: false, busy: false, message: '', ok: true },
  run: { lines: [], running: false, input: '', agent: '', command: null },
  fileTab: 'workflow.yaml',
};

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', '\'': '&#39;' }[c]));

let toastTimer = null;
function toast(message, error = false) {
  const el = $('toast');
  el.textContent = message;
  el.className = error ? 'err' : '';
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, error ? 6000 : 2600);
}

async function guard(what, fn) {
  try { return await fn(); } catch (err) { toast(`${what}: ${(err && err.message) || err}`, true); return null; }
}

function kindOf(id) {
  return state.catalog.kinds.find((k) => k.id === id)
    || { id, label: id, short: id, target: null, detail: { label: '内容', required: true, placeholder: '' }, check: null, recordable: false };
}

function machineIdFrom(name) {
  const ascii = String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return ascii || `routine-${Date.now().toString(36)}`;
}

// --- 見た目（theme.json + custom.css） ---------------------------------------------------

function applyTheme(res) {
  if (!res) return;
  state.theme = res;
  const root = document.documentElement;
  for (const [k, v] of Object.entries(res.variables || {})) root.style.setProperty(k, v);
  $('custom-css').textContent = res.customCss || '';
}

// --- 工程列 -------------------------------------------------------------------------

function emptyStep(kind) {
  return { id: '', kind, title: '', detail: '', target: '', check: '', checkRetries: 1, outcomes: [], recorded: [], rawTransitions: false };
}

function newSpec() {
  return {
    version: 3, name: '', machine: '', purpose: '', finish: '', notes: '', maxSteps: 30,
    terminals: { done: { id: 'complete', description: '完了' }, abort: { id: 'failed', description: '失敗として終了' } },
    steps: [], preserved: null,
  };
}

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
  return { ...spec, machine: spec.machine || machineIdFrom(spec.name), steps: spec.steps.map((s) => ({ ...s, checkRetries: Number(s.checkRetries) || 0 })) };
}

// カードの間に見せる遷移（判定の正典は main の model.stepTransitions。ここは表示だけ）。
function edgesOf(spec, index) {
  const step = spec.steps[index];
  const count = spec.steps.length;
  const describe = (to) => {
    if (to === 'done') return { text: '完了', cls: 'done' };
    if (to === 'abort') return { text: '失敗として終了', cls: 'abort' };
    if (to === 'next') return index + 1 < count ? { text: `次へ（${index + 2}）`, cls: '' } : { text: '完了', cls: 'done' };
    const n = Number(String(to).slice(5));
    if (n === index + 1) return { text: 'この工程をやり直す', cls: 'back' };
    return n <= index ? { text: `工程 ${n} へ戻る`, cls: 'back' } : { text: `工程 ${n} へ`, cls: '' };
  };
  if (step.rawTransitions) {
    const own = ((spec.preserved && spec.preserved.transitions) || []).filter((t) => t && t.from === step.id);
    if (!own.length) return [{ label: 'YAML', cls: 'raw', text: '遷移は原文のまま' }];
    return own.map((t) => ({ label: t.condition_rule ? String(t.condition_rule).slice(0, 26) : '条件（自然言語）', cls: 'raw', text: `${t.to}${t.condition ? `: ${String(t.condition).slice(0, 56)}` : ''}` }));
  }
  if (step.outcomes.length) return step.outcomes.map((o) => ({ label: o.label || '（ラベル）', cls: step.check ? 'gate' : '', ...describe(o.to) }));
  if (step.check) return [{ label: '検査が通る', cls: 'gate', ...describe('next') }];
  return [{ label: 'OK', cls: 'ok', ...describe('next') }, { label: 'FAILED', cls: 'ng', ...describe('abort') }];
}

// Shortcuts 型の要約: 動詞で始まる 1 文。名前があればそれ、無ければ内容の 1 行目。
function summary(step) {
  const kind = kindOf(step.kind);
  const first = String(step.detail || '').split('\n').map((l) => l.replace(/^\d+\.\s*/, '').trim()).find(Boolean) || '';
  const what = step.title || (step.kind === 'command' ? step.target : first);
  if (!what) return { v: '', text: kind.label, empty: true };
  const verb = { browser: 'ブラウザで', windows: `${step.target || 'アプリ'} で`, skill: `${step.target || 'スキル'} に任せて`, command: '実行:', agent: 'AI が' }[step.kind] || '';
  return { v: verb, text: what, empty: false };
}

// --- 起動・ルート ------------------------------------------------------------------------

async function setRoot(root) {
  if (!root) return;
  if (state.current && state.current.dirty && !confirm('保存していない変更があります。フォルダを切り替えますか？')) return;
  state.root = root;
  state.current = null;
  state.config = (await guard('設定', () => api.getConfig())) || state.config;
  await loadMachines();
  goHome();
}

async function loadMachines() {
  state.machines = state.root ? ((await guard('一覧の取得', () => api.listMachines(state.root))) || []) : [];
}

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

function goHome() {
  if (state.current && state.current.dirty && state.view === 'editor' && !confirm('保存していない変更があります。一覧へ戻りますか？')) return;
  state.view = 'home';
  state.current = null;
  render();
}

async function openMachine(machine) {
  const res = await guard('定義の読み込み', () => api.readMachine(state.root, machine));
  if (!res) return;
  const raw = res.raw;
  raw.steps = raw.steps.map((s) => ({ ...emptyStep(s.kind), ...s, outcomes: s.outcomes || [], recorded: s.recorded || [] }));
  state.current = { machine, isNew: false, spec: raw, dirty: false, warnings: res.warnings || [], dir: res.dir };
  state.view = 'editor';
  state.open = -1;
  state.preview = null;
  state.ai = null;
  state.run.lines = [];
  state.run.command = null;
  render();
}

function newMachine() {
  if (!state.root) { toast('先にフォルダを選んでください', true); return; }
  state.current = { machine: '', isNew: true, spec: newSpec(), dirty: true, warnings: [], dir: '' };
  state.view = 'editor';
  state.open = -1;
  state.preview = null;
  state.ai = null;
  render();
  const t = document.querySelector('.title-input');
  if (t) t.focus();
}

function markDirty() {
  if (!state.current) return;
  state.current.dirty = true;
  state.preview = null;
  state.ai = null;
  const el = $('dirty-mark');
  if (el) el.hidden = false;
}

// --- 描画 ---------------------------------------------------------------------------

function render() {
  renderBar();
  const main = $('main');
  main.innerHTML = state.view === 'editor' && state.current ? editorHtml() : homeHtml();
  if (state.view === 'editor' && state.current) bindEditor(main); else bindHome(main);
}

function renderBar() {
  const editing = state.view === 'editor' && state.current;
  $('btn-home').hidden = !editing;
  const center = $('bar-center');
  const right = $('bar-right');
  if (!editing) {
    center.innerHTML = '';
    right.innerHTML = '<button type="button" id="b-settings" class="ghost" title="設定">⚙ 設定</button>';
    $('b-settings').addEventListener('click', openSettings);
    return;
  }
  const spec = state.current.spec;
  center.innerHTML = `<input class="title-input" id="m-name" value="${esc(spec.name)}" placeholder="名前を付ける（例: 月次の勤怠集計）" aria-label="名前">`;
  right.innerHTML = `<span id="dirty-mark" class="dirty" ${state.current.dirty ? '' : 'hidden'}>● 未保存</span>
    <button type="button" id="b-record" class="ghost">記録</button>
    <button type="button" id="b-files" class="ghost">定義</button>
    <button type="button" id="b-ai" class="ghost">AI 補完</button>
    <button type="button" id="b-run" class="ghost" ${state.current.isNew ? 'disabled title="保存すると実行できます"' : ''}>実行</button>
    <button type="button" id="b-settings" class="ghost" title="設定">⚙</button>
    <button type="button" id="b-save" class="primary">保存</button>`;
  let machineTouched = !state.current.isNew || !!spec.machine;
  $('m-name').addEventListener('input', (e) => {
    spec.name = e.target.value;
    if (!machineTouched) { spec.machine = machineIdFrom(e.target.value); const m = $('m-machine'); if (m) m.value = spec.machine; }
    markDirty();
  });
  const mid = $('m-machine');
  if (mid) mid.addEventListener('input', () => { machineTouched = true; });
  $('b-record').addEventListener('click', openRecord);
  $('b-files').addEventListener('click', openFiles);
  $('b-ai').addEventListener('click', openAi);
  $('b-run').addEventListener('click', openRun);
  $('b-settings').addEventListener('click', openSettings);
  $('b-save').addEventListener('click', saveMachine);
}

// --- 一覧 -----------------------------------------------------------------------------

function homeHtml() {
  if (!state.root) {
    return `<div class="home"><div class="empty-home">
      <h2>ステートマシンを置くフォルダを選びます</h2>
      <p>フォルダ直下の <code>.statemachine/&lt;識別名&gt;/</code> が一覧になります。定義は statemachine-use スキルだけで動きます。</p>
      <div class="home-actions"><button type="button" class="primary" id="h-root">フォルダを選ぶ</button><button type="button" id="h-yaml">既存の workflow.yaml を開く</button>
        ${(state.config.recentRoots || []).length ? `<select id="h-recent"><option value="">最近開いたフォルダ</option>${state.config.recentRoots.map((r) => `<option value="${esc(r)}">${esc(r)}</option>`).join('')}</select>` : ''}</div>
    </div></div>`;
  }
  const cards = state.machines.map((m) => `<button type="button" class="machine-card" data-open="${esc(m.machine)}">
    <span class="name">${esc(m.name)}</span>
    <span class="desc">${esc(m.description || '（説明なし）')}</span>
    <span class="meta"><span class="mono">${esc(m.machine)}</span>${m.maker ? '<span>このアプリで作成</span>' : '<span>手書きの定義</span>'}</span>
  </button>`).join('');
  return `<div class="home">
    <div class="home-head">
      <div><h1>ステートマシン</h1><div class="path mono">${esc(state.root)}</div></div>
      <div class="home-actions"><button type="button" id="h-root">フォルダを選ぶ</button><button type="button" id="h-yaml">workflow.yaml を開く</button><button type="button" class="primary" id="h-new">＋ 新規</button></div>
    </div>
    <div class="machine-grid">${cards}<button type="button" class="machine-card new" id="h-new-card">＋ 新しいステートマシン</button></div>
  </div>`;
}

function bindHome(main) {
  const on = (id, fn) => { const el = main.querySelector(`#${id}`); if (el) el.addEventListener('click', fn); };
  on('h-root', chooseRoot);
  on('h-yaml', chooseWorkflow);
  on('h-new', newMachine);
  on('h-new-card', newMachine);
  const recent = main.querySelector('#h-recent');
  if (recent) recent.addEventListener('change', (e) => { if (e.target.value) setRoot(e.target.value); });
  for (const b of main.querySelectorAll('[data-open]')) b.addEventListener('click', () => openMachine(b.dataset.open));
}

// --- 編集 -----------------------------------------------------------------------------

function editorHtml() {
  const spec = state.current.spec;
  const cur = state.current;
  const parts = [];
  parts.push(`<div class="overview">
    <textarea class="purpose" id="m-purpose" rows="1" placeholder="目的を 1〜2 文で（例: 毎月 1 日に勤怠システムから月次集計を出力し、差し戻し候補を一覧にする）">${esc(spec.purpose)}</textarea>
    <div class="meta"><span>識別名</span><input id="m-machine" class="mono" value="${esc(spec.machine)}" placeholder="英数字・ハイフン" ${cur.isNew ? '' : 'readonly title="既存の定義の識別名は変えられません"'}>
      <span>最大遷移数</span><input id="m-max" type="number" min="1" max="500" value="${esc(spec.maxSteps)}" style="width:80px">
      ${cur.isNew ? '<span>新規</span>' : `<span class="mono">${esc(cur.dir)}</span>`}</div>
    <details><summary>終了条件・注意事項（AI 補完に渡す補足）</summary><div class="grid2">
      <div class="field"><label for="m-finish">終了条件</label><textarea id="m-finish" rows="2">${esc(spec.finish)}</textarea></div>
      <div class="field"><label for="m-notes">注意事項</label><textarea id="m-notes" rows="2">${esc(spec.notes)}</textarea></div></div></details>
  </div>`);
  if (!spec.steps.length) {
    parts.push(`<div class="empty-steps"><p>最初の工程を置きます。種類を選ぶか、上の「記録」で人の操作から起こします。</p>${pickerHtml(0)}</div>`);
  } else {
    spec.steps.forEach((step, i) => {
      parts.push(stepHtml(spec, i));
      parts.push(edgeHtml(spec, i));
    });
  }
  parts.push(`<div class="terminal"><span class="icon">✓</span><span><span class="name">${esc(spec.terminals.done.description)}</span> <span class="mono small">${esc(spec.terminals.done.id)}</span></span></div>`);
  parts.push(`<div class="terminal abort"><span class="icon">✕</span><span><span class="name">${esc(spec.terminals.abort.description)}</span> <span class="mono small">${esc(spec.terminals.abort.id)}</span></span></div>`);
  parts.push(`<div class="notes" id="notes">${notesHtml()}</div>`);
  return `<div class="editor">${parts.join('')}</div>`;
}

function notesHtml() {
  const cur = state.current;
  const errors = (state.preview && state.preview.errors) || [];
  const warnings = [...(cur.warnings || []), ...((state.preview && state.preview.warnings) || [])];
  return `${errors.length ? `<div class="err">定義が検証を通りません:<ul>${errors.map((e) => `<li>${esc(e)}</li>`).join('')}</ul></div>` : ''}
    ${warnings.length ? `<div class="warn"><ul>${warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}
    ${state.preview && !errors.length ? '<div class="ok">構造の検査を通りました。スキルの --dry-run は「実行」から行えます。</div>' : ''}`;
}

function pickerHtml(at) {
  return `<div class="picker" data-at="${at}">${state.catalog.kinds.map((k) =>
    `<button type="button" data-add="${esc(k.id)}" title="${esc(k.description)}"><span class="dot k-${esc(k.id)}"></span>${esc(k.label)}</button>`).join('')}</div>`;
}

function stepHtml(spec, index) {
  const step = spec.steps[index];
  const kind = kindOf(step.kind);
  const s = summary(step);
  const open = state.open === index;
  const sub = [kind.label];
  if (step.target && step.kind !== 'command') sub.push(`<span class="mono">${esc(step.target)}</span>`);
  if (step.check) sub.push(`<span class="chk">✓ 検査あり</span>`);
  if (step.recorded && step.recorded.length) sub.push(`<span class="rec">● 記録 ${step.recorded.length} 件</span>`);
  return `<div class="step" data-step="${index}"><div class="step-card ${open ? 'is-open' : ''}">
    <div class="step-head" role="button" tabindex="0" aria-expanded="${open}">
      <span class="step-icon k-${esc(step.kind)}">${index + 1}</span>
      <span class="step-summary">
        <div class="sentence">${s.empty ? `<span class="v">${esc(s.text)}（内容を入力）</span>` : `${s.v ? `<span class="v">${esc(s.v)}</span> ` : ''}${esc(s.text)}`}</div>
        <div class="sub">${sub.join('<span>·</span>')}</div>
      </span>
      <span class="step-right"><span class="id">${esc(step.id || '')}</span><span class="chev">›</span></span>
    </div>
    ${open ? stepBodyHtml(spec, index) : ''}
  </div></div>`;
}

function stepBodyHtml(spec, index) {
  const step = spec.steps[index];
  const kind = kindOf(step.kind);
  const seg = state.catalog.kinds.map((k) => `<button type="button" data-kind="${esc(k.id)}" class="${k.id === step.kind ? 'is-on' : ''}" title="${esc(k.description)}"><span class="dot k-${esc(k.id)}"></span>${esc(k.label)}</button>`).join('');
  const target = kind.target ? `<div class="field"><label>${esc(kind.target.label)}${kind.target.required ? '' : '（任意）'}</label><input data-field="target" class="mono" value="${esc(step.target)}" placeholder="${esc(kind.target.placeholder || '')}"></div>` : '';
  const recorded = step.recorded && step.recorded.length ? `<div class="field"><label>記録した操作（${step.recorded.length} 件）</label>
    <ol class="rec-list">${step.recorded.map((op) => `<li>${esc(op.op)} ${esc(op.label || op.target)}${op.value ? ` ${esc(op.value)}` : ''}${op.example ? ` <span class="muted">(例: ${esc(op.example)})</span>` : ''}</li>`).join('')}</ol>
    <div><button type="button" class="tiny" data-unrecord>記録を外す（文章だけ残す）</button></div></div>` : '';
  const check = kind.check ? `<div class="field"><label>完了の確認コマンド（任意）</label>
    <div class="grid2" style="grid-template-columns: 1fr 130px"><input data-field="check" class="mono" value="${esc(step.check)}" placeholder="${esc(kind.check.placeholder || '')}">
      <input data-field="checkRetries" type="number" min="0" max="5" value="${esc(step.checkRetries)}" title="検査が落ちたときのやり直し回数"></div>
    <small>終了コード 0 で通過。シェルは介さない（パイプ・リダイレクト不可）。宣言すると、モデルの OK ではなく検査の結果で次へ進みます。右はやり直し回数。</small></div>` : '';
  const dest = (to) => {
    const opts = [['next', index + 1 < spec.steps.length ? `次へ（工程 ${index + 2}）` : '次へ（完了）'], ['done', '完了として終了'], ['abort', '失敗として終了']];
    spec.steps.forEach((_s, i) => { opts.push([`step:${i + 1}`, `工程 ${i + 1} へ${i < index ? '戻る' : i === index ? '（やり直す）' : ''}`]); });
    return opts.map(([v, l]) => `<option value="${v}" ${v === to ? 'selected' : ''}>${esc(l)}</option>`).join('');
  };
  const branches = step.rawTransitions
    ? `<div class="section-title">遷移</div><small class="muted">YAML に自然言語条件や無条件遷移で書かれているため、原文のまま保持しています。画面で分岐として編集するには既定の OK / FAILED に置き換えます。</small>
      <div><button type="button" class="tiny" data-unraw>画面で編集できる形にする（原文の遷移を捨てる）</button></div>`
    : `<div class="section-title">分岐（出力の第 1 行で決める）</div>
      <small class="muted">空なら OK → 次へ、FAILED → 失敗。ラベルを置くと、その語で始まる出力ごとに行き先を決めます。</small>
      ${step.outcomes.map((o, i) => `<div class="branch-row" data-branch="${i}"><input data-bfield="label" class="mono" value="${esc(o.label)}" placeholder="ラベル（例: APPROVED）"><select data-bfield="to">${dest(o.to)}</select><button type="button" data-bremove title="削除">✕</button></div>`).join('')}
      <div><button type="button" class="tiny" data-badd>＋ 分岐を追加</button></div>`;
  return `<div class="step-body">
    <div class="field"><label>種類</label><div class="seg">${seg}</div></div>
    <div class="grid2">
      <div class="field"><label>名前（任意・要約になります）</label><input data-field="title" value="${esc(step.title)}" placeholder="例: 申請一覧を開く"></div>
      <div class="field"><label>ステート ID</label><input data-field="id" class="mono" value="${esc(step.id)}" placeholder="step_${index + 1}"></div>
    </div>
    ${target}
    <div class="field"><label>${esc(kind.detail.label)}${kind.detail.required ? '' : '（任意）'}</label><textarea data-field="detail" rows="5" placeholder="${esc(kind.detail.placeholder || '')}">${esc(step.detail)}</textarea><small>本文の <code>{{key}}</code> は実行時に人が入れる入力パラメータになります。</small></div>
    ${recorded}
    ${check}
    ${branches}
    <div class="step-actions">
      <div class="left"><button type="button" class="tiny" data-move="up" ${index === 0 ? 'disabled' : ''}>↑ 上へ</button><button type="button" class="tiny" data-move="down" ${index === spec.steps.length - 1 ? 'disabled' : ''}>↓ 下へ</button></div>
      <button type="button" class="tiny danger" data-remove>この工程を削除</button>
    </div>
  </div>`;
}

function edgeHtml(spec, index) {
  const edges = edgesOf(spec, index);
  const at = index + 1;
  return `<div class="edge" data-edge="${index}">
    <span class="plus"><button type="button" data-insert="${at}" title="ここに工程を追加">+</button></span>
    <div class="lines">${edges.map((e) => `<span class="t"><span class="lbl ${e.cls}">${esc(e.label)}</span><span>→</span><span class="to ${e.cls}">${esc(e.text)}</span></span>`).join('')}</div>
    ${state.pickerAt === at ? pickerHtml(at) : ''}
  </div>`;
}

function bindEditor(main) {
  const spec = state.current.spec;
  const cur = state.current;
  const bindTop = (id, key, num) => {
    const el = main.querySelector(`#${id}`);
    if (el) el.addEventListener('input', () => { spec[key] = num ? (Number(el.value) || 30) : el.value; markDirty(); });
  };
  bindTop('m-purpose', 'purpose');
  bindTop('m-machine', 'machine');
  bindTop('m-max', 'maxSteps', true);
  bindTop('m-finish', 'finish');
  bindTop('m-notes', 'notes');
  const purpose = main.querySelector('#m-purpose');
  if (purpose) { const grow = () => { purpose.style.height = 'auto'; purpose.style.height = `${purpose.scrollHeight + 2}px`; }; purpose.addEventListener('input', grow); grow(); }
  for (const b of main.querySelectorAll('[data-add]')) b.addEventListener('click', () => insertStep(Number(b.closest('[data-at]').dataset.at), b.dataset.add));
  for (const b of main.querySelectorAll('[data-insert]')) b.addEventListener('click', () => { const at = Number(b.dataset.insert); state.pickerAt = state.pickerAt === at ? -1 : at; render(); });
  for (const card of main.querySelectorAll('[data-step]')) bindStep(card);
  if (cur.isNew && !spec.steps.length) { const t = main.querySelector('#m-purpose'); if (t) t.focus(); }
}

function bindStep(card) {
  const spec = state.current.spec;
  const index = Number(card.dataset.step);
  const step = spec.steps[index];
  const head = card.querySelector('.step-head');
  const toggle = () => { state.open = state.open === index ? -1 : index; state.pickerAt = -1; render(); scrollToStep(index); };
  head.addEventListener('click', toggle);
  head.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
  const body = card.querySelector('.step-body');
  if (!body) return;
  for (const el of body.querySelectorAll('[data-field]')) {
    el.addEventListener('input', () => {
      step[el.dataset.field] = el.dataset.field === 'checkRetries' ? Number(el.value) : el.value;
      markDirty();
      refreshHead(index);
    });
  }
  for (const b of body.querySelectorAll('[data-kind]')) {
    b.addEventListener('click', () => {
      const next = kindOf(b.dataset.kind);
      step.kind = b.dataset.kind;
      if (!next.target) step.target = '';
      if (!next.check) step.check = '';
      if (!next.recordable) step.recorded = [];
      markDirty();
      render();
    });
  }
  for (const row of body.querySelectorAll('[data-branch]')) {
    const i = Number(row.dataset.branch);
    for (const el of row.querySelectorAll('[data-bfield]')) {
      el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', () => { step.outcomes[i][el.dataset.bfield] = el.value; markDirty(); refreshEdge(index); });
    }
    row.querySelector('[data-bremove]').addEventListener('click', () => { step.outcomes.splice(i, 1); markDirty(); render(); });
  }
  const on = (sel, fn) => { const el = body.querySelector(sel); if (el) el.addEventListener('click', fn); };
  on('[data-badd]', () => { step.outcomes.push({ label: '', to: 'next' }); markDirty(); render(); const inputs = document.querySelectorAll(`[data-step="${index}"] [data-branch] input`); if (inputs.length) inputs[inputs.length - 1].focus(); });
  on('[data-unrecord]', () => { step.recorded = []; markDirty(); render(); });
  on('[data-unraw]', () => {
    if (!confirm('この工程の原文の遷移を捨てて、既定の OK / FAILED に置き換えますか？')) return;
    step.rawTransitions = false;
    if (spec.preserved && Array.isArray(spec.preserved.transitions)) spec.preserved.transitions = spec.preserved.transitions.filter((t) => !(t && t.from === step.id));
    markDirty();
    render();
  });
  for (const b of body.querySelectorAll('[data-move]')) {
    b.addEventListener('click', () => {
      const to = b.dataset.move === 'up' ? index - 1 : index + 1;
      if (to < 0 || to >= spec.steps.length) return;
      const [moved] = spec.steps.splice(index, 1);
      spec.steps.splice(to, 0, moved);
      retarget(spec, index, to);
      state.open = to;
      markDirty();
      render();
      scrollToStep(to);
    });
  }
  on('[data-remove]', () => {
    if (!confirm(`工程 ${index + 1} を削除しますか？`)) return;
    spec.steps.splice(index, 1);
    dropTargets(spec, index);
    state.open = -1;
    markDirty();
    render();
  });
}

function refreshHead(index) {
  const card = document.querySelector(`[data-step="${index}"]`);
  if (!card) return;
  const tmp = document.createElement('div');
  tmp.innerHTML = stepHtml(state.current.spec, index);
  const fresh = tmp.querySelector('.step-head');
  const old = card.querySelector('.step-head');
  old.replaceWith(fresh);
  const toggle = () => { state.open = state.open === index ? -1 : index; render(); };
  fresh.addEventListener('click', toggle);
  fresh.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
  refreshEdge(index);
}

function refreshEdge(index) {
  const edge = document.querySelector(`[data-edge="${index}"] .lines`);
  if (!edge) return;
  edge.innerHTML = edgesOf(state.current.spec, index).map((e) => `<span class="t"><span class="lbl ${e.cls}">${esc(e.label)}</span><span>→</span><span class="to ${e.cls}">${esc(e.text)}</span></span>`).join('');
}

function scrollToStep(index) {
  const el = document.querySelector(`[data-step="${index}"]`);
  if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function retarget(spec, from, to) {
  const map = (n) => {
    const i = n - 1;
    if (i === from) return to + 1;
    if (from < to && i > from && i <= to) return n - 1;
    if (from > to && i >= to && i < from) return n + 1;
    return n;
  };
  for (const s of spec.steps) for (const o of s.outcomes) if (o.to.startsWith('step:')) o.to = `step:${map(Number(o.to.slice(5)))}`;
}

function dropTargets(spec, removed) {
  for (const s of spec.steps) for (const o of s.outcomes) {
    if (!o.to.startsWith('step:')) continue;
    const n = Number(o.to.slice(5)) - 1;
    if (n === removed) o.to = 'next';
    else if (n > removed) o.to = `step:${n}`;
  }
}

function insertStep(at, kindId) {
  const spec = state.current.spec;
  spec.steps.splice(at, 0, emptyStep(kindId));
  for (const s of spec.steps) for (const o of s.outcomes) if (o.to.startsWith('step:') && Number(o.to.slice(5)) - 1 >= at) o.to = `step:${Number(o.to.slice(5)) + 1}`;
  state.open = at;
  state.pickerAt = -1;
  markDirty();
  render();
  const first = document.querySelector(`[data-step="${at}"] [data-field="title"]`);
  if (first) first.focus();
}

// --- 検査・保存 -------------------------------------------------------------------------

async function previewMachine() {
  if (!state.current) return null;
  const res = await guard('検査', () => api.previewMachine(specPayload()));
  if (!res) return null;
  state.preview = res;
  const notes = $('notes');
  if (notes) notes.innerHTML = notesHtml();
  return res;
}

async function saveMachine() {
  if (!state.current) return;
  const preview = await previewMachine();
  if (!preview || preview.errors.length) { toast('検証エラーがあるので保存しません', true); return; }
  const payload = specPayload();
  if (state.current.isNew) {
    const exists = await guard('確認', () => api.machineExists(state.root, payload.machine));
    if (exists && !confirm(`.statemachine/${payload.machine}/ は既にあります。上書きしますか？`)) return;
  }
  const res = await guard('保存', () => api.saveMachine(state.root, payload));
  if (!res) return;
  Object.assign(state.current, { machine: res.machine, isNew: false, dirty: false, dir: res.dir, warnings: res.warnings || [] });
  state.current.spec.machine = res.machine;
  state.run.command = null;
  toast(`保存しました: ${res.dir}`);
  await loadMachines();
  render();
}

// --- ダイアログ -------------------------------------------------------------------------

function dialog(id, title, bodyHtml) {
  const dlg = $(id);
  dlg.innerHTML = `<div class="dlg-head"><h2>${esc(title)}</h2><button type="button" class="ghost" data-close>閉じる</button></div><div class="dlg-body">${bodyHtml}</div>`;
  dlg.querySelector('[data-close]').addEventListener('click', () => dlg.close());
  if (!dlg.open) dlg.showModal();
  return dlg;
}

function openRecord() {
  const rec = state.recording;
  const kinds = state.catalog.kinds.filter((k) => k.recordable);
  const windows = rec.source === 'windows';
  const onWindows = state.catalog.platform === 'win32';
  const target = windows ? { field: 'app', value: rec.app, label: 'アプリ（ウィンドウ名・プロセス名・PID）', placeholder: '例: 勤怠管理' } : { field: 'url', value: rec.url, label: '記録を始める URL', placeholder: 'https://…' };
  const dlg = dialog('dlg-record', '人の操作を記録して工程に起こす', `
    <p class="small muted" style="margin:0">要素は名前と種類で残し、入力した値は <code>{{key}}</code> の入力パラメータに置き換えます（パスワードらしい値は例にも残しません）。起こした工程は末尾に足すので、名前・内容・分岐を直してから保存します。</p>
    <div class="grid2">
      <div class="field"><label>記録の種類</label><select id="r-source" ${rec.active ? 'disabled' : ''}>${kinds.map((k) => `<option value="${esc(k.id)}" ${rec.source === k.id ? 'selected' : ''}>${esc(k.label)}</option>`).join('')}</select></div>
      <div class="field"><label>${esc(target.label)}</label><input id="r-target" class="mono" data-rec="${target.field}" value="${esc(target.value)}" placeholder="${esc(target.placeholder)}" ${rec.active ? 'disabled' : ''}></div>
    </div>
    <div class="row"><button type="button" id="r-start" ${rec.active || rec.busy || (windows && !onWindows) ? 'disabled' : ''}>記録を開始</button>
      <button type="button" id="r-stop" class="primary" ${!rec.active || rec.busy ? 'disabled' : ''}>記録を終了して工程に起こす</button>
      <span class="small muted">${windows ? (onWindows ? 'この端末で winauto record を走らせます。' : 'Windows アプリの記録は Windows 上でだけ取れます。下の貼り付けを使ってください。') : '見える形でブラウザが開きます（playwright-cli）。'}</span></div>
    <p id="r-message" class="msg ${rec.ok ? '' : 'err'}" ${rec.message ? '' : 'hidden'}>${esc(rec.message)}</p>
    <div class="field"><label>別の端末で取った記録を貼り付ける</label>
      <small>${windows ? `対象の PC で <code>winauto record --app ${esc(rec.app || '<アプリ>')} --output events.jsonl</code> を実行し、操作して Ctrl+C で止め、できたファイルの中身を貼り付けます。` : '<code>playwright-cli recording-start</code> → 操作 → <code>recording-stop</code> が印字した内容を貼り付けます。'}</small>
      <textarea id="r-text" class="mono" rows="6">${esc(rec.text)}</textarea>
      <div><button type="button" id="r-import" ${rec.busy ? 'disabled' : ''}>貼り付けた記録を工程に起こす</button></div></div>`);
  dlg.querySelector('#r-source').addEventListener('change', (e) => { rec.source = e.target.value; openRecord(); });
  dlg.querySelector('#r-target').addEventListener('input', (e) => { rec[e.target.dataset.rec] = e.target.value; });
  dlg.querySelector('#r-text').addEventListener('input', (e) => { rec.text = e.target.value; });
  dlg.querySelector('#r-start').addEventListener('click', () => recordingAction('start'));
  dlg.querySelector('#r-stop').addEventListener('click', () => recordingAction('stop'));
  dlg.querySelector('#r-import').addEventListener('click', () => recordingAction('import'));
}

async function recordingAction(action) {
  const rec = state.recording;
  if (rec.busy) return;
  const payload = { root: state.root, source: rec.source, url: rec.url, app: rec.app };
  if (action === 'import') {
    payload.text = rec.text;
    if (!String(rec.text || '').trim()) { rec.message = '記録を貼り付けてください'; rec.ok = false; openRecord(); return; }
  }
  rec.busy = true; rec.ok = true;
  rec.message = action === 'start' ? (rec.source === 'windows' ? '記録を開始しています…' : 'ブラウザを開いています…') : '記録を工程に起こしています…';
  openRecord();
  let res;
  try {
    res = action === 'start' ? await api.recordingStart(payload) : action === 'stop' ? await api.recordingStop(payload) : await api.recordingImport(payload);
  } catch (err) { res = { error: String((err && err.message) || err) }; }
  rec.busy = false;
  if (!res || res.error) {
    rec.message = `${action === 'start' ? '記録を開始できませんでした' : '記録を工程に起こせませんでした'}: ${(res && res.error) || '原因不明'}`;
    rec.ok = false;
    if (action === 'stop') rec.active = false;
    openRecord();
    return;
  }
  if (action === 'start') {
    rec.active = true;
    rec.message = rec.source === 'windows' ? 'winauto が記録中です。アプリを操作してから「記録を終了して工程に起こす」を押します' : '開いたブラウザで操作してください。終わったら「記録を終了して工程に起こす」を押します';
    openRecord();
    return;
  }
  if (action === 'stop') rec.active = false;
  if (action === 'import') rec.text = '';
  const spec = state.current.spec;
  const steps = Array.isArray(res.steps) ? res.steps : [];
  for (const s of steps) spec.steps.push({ ...emptyStep(s.kind), ...s });
  assignIds(spec);
  markDirty();
  rec.message = '';
  $('dlg-record').close();
  state.open = spec.steps.length - steps.length;
  render();
  scrollToStep(state.open);
  const params = res.parameters && res.parameters.length ? `。入力パラメータ: ${res.parameters.map((k) => `{{${k}}}`).join(' ')}` : '';
  toast(`${res.operations || 0} 件の操作から ${steps.length} 工程を起こしました${params}`);
}

async function openFiles() {
  const dlg = dialog('dlg-files', '保存すると書かれるファイル', '<p class="muted small">コンパイルしています…</p>');
  const res = state.preview || await previewMachine();
  if (!res) { dlg.querySelector('.dlg-body').innerHTML = '<p class="msg err">コンパイルできませんでした（名前と工程を確認してください）</p>'; return; }
  const files = res.files || {};
  const names = Object.keys(files);
  if (!names.includes(state.fileTab)) state.fileTab = names[0] || '';
  const paint = () => {
    dlg.querySelector('.dlg-body').innerHTML = `<div class="small muted">.statemachine/${esc(state.current.spec.machine || machineIdFrom(state.current.spec.name))}/ — 書式の正典は statemachine-use スキル。maker.json は読み戻し用の写しで、実行には使いません。</div>
      ${res.errors && res.errors.length ? `<div class="msg err">${res.errors.map(esc).join('\n')}</div>` : ''}
      <div class="file-tabs">${names.map((n) => `<button type="button" data-file="${esc(n)}" class="${n === state.fileTab ? 'is-on' : ''}">${esc(n)}</button>`).join('')}</div>
      <pre>${esc(files[state.fileTab] || '')}</pre>`;
    for (const b of dlg.querySelectorAll('[data-file]')) b.addEventListener('click', () => { state.fileTab = b.dataset.file; paint(); });
  };
  paint();
}

async function openAi() {
  const dlg = dialog('dlg-ai', 'statemachine-use の作成モードに補完させる', '<p class="muted small">指示文を組んでいます…</p>');
  const res = state.ai || await guard('指示文', () => api.instruction(state.root, specPayload()));
  if (!res) { dlg.querySelector('.dlg-body').innerHTML = '<p class="msg err">指示文を組めませんでした（名前と工程を確認してください）</p>'; return; }
  state.ai = res;
  dlg.querySelector('.dlg-body').innerHTML = `<p class="small muted" style="margin:0">このアプリが書いた定義は AI 無しで動きます。待機・読み取り・想定外の画面の扱いを AI に補わせたいときは、この指示文をエージェント CLI（claude / copilot / kiro など）に貼ります。先に保存しておくと、既存の定義を読んで直す指示になります。</p>
    <div class="row"><button type="button" id="ai-copy" class="primary">指示文をコピー</button><button type="button" id="ai-terminal">フォルダで端末を開く</button></div>
    <pre>${esc(res.prompt)}</pre>`;
  dlg.querySelector('#ai-copy').addEventListener('click', async () => { await api.copyText(res.prompt); toast('指示文をコピーしました。エージェント CLI に貼り付けてください'); });
  dlg.querySelector('#ai-terminal').addEventListener('click', () => guard('端末', () => api.openTerminal(state.root)));
}

async function openRun() {
  const run = state.run;
  const cur = state.current;
  if (cur.isNew) return;
  if (!run.command) run.command = await guard('コマンド', () => api.runCommand(state.root, cur.machine));
  const agent = run.agent || state.config.agent || 'claude';
  const dlg = dialog('dlg-run', 'スキルのスクリプトで検証・実行する', `
    ${cur.dirty ? '<p class="msg" style="color:var(--warn)">保存していない変更があります。実行するのは保存された定義です。</p>' : ''}
    <div class="row"><button type="button" id="run-dry" ${run.running ? 'disabled' : ''}>検証（--dry-run）</button><button type="button" id="run-go" class="primary" ${run.running ? 'disabled' : ''}>実行</button><button type="button" id="run-stop" class="danger" ${run.running ? '' : 'disabled'}>停止</button></div>
    <div class="grid2">
      <div class="field"><label>エージェント（--agent）</label><select id="run-agent">${['claude', 'copilot', 'kiro', 'anthropic'].map((a) => `<option value="${a}" ${a === agent ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
      <div class="field"><label>入力（--input・任意）</label><input id="run-input" value="${esc(run.input)}" placeholder="{{input}} に入る文"></div>
    </div>
    <div class="log" id="run-log">${run.lines.map((l) => `<div class="${l.kind === 'stderr' ? 'e' : ''}">${esc(l.line)}</div>`).join('') || '<span class="muted">出力はここに流れます</span>'}</div>
    ${run.command ? `<div class="small muted">このアプリ無しで動かすには <span class="mono">${esc(run.command.cwd)}</span> で:<br><code>${esc(run.command.dryRun)}</code><br><code>${esc(run.command.run)}</code></div>` : ''}`);
  dlg.querySelector('#run-agent').addEventListener('change', (e) => { run.agent = e.target.value; });
  dlg.querySelector('#run-input').addEventListener('input', (e) => { run.input = e.target.value; });
  dlg.querySelector('#run-dry').addEventListener('click', () => startRun('dry-run'));
  dlg.querySelector('#run-go').addEventListener('click', () => startRun('run'));
  dlg.querySelector('#run-stop').addEventListener('click', () => api.runStop());
}

async function startRun(mode) {
  const run = state.run;
  run.lines = [];
  run.running = true;
  openRun();
  const res = await guard('実行', () => api.runStart({ root: state.root, machine: state.current.machine, mode, agent: run.agent || state.config.agent, input: run.input }));
  if (!res) { run.running = false; openRun(); return; }
  appendLog({ kind: 'stdout', line: `$ ${res.command}` });
}

function appendLog(entry) {
  state.run.lines.push(entry);
  if (state.run.lines.length > 2000) state.run.lines.shift();
  const log = $('run-log');
  if (!log) return;
  if (log.firstChild && log.firstChild.tagName === 'SPAN') log.innerHTML = '';
  const div = document.createElement('div');
  if (entry.kind === 'stderr') div.className = 'e';
  div.textContent = entry.line;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

// 設定: 見た目（theme.json / custom.css）・実行（agent / model / skillDir）・道具の確認
const ACCENTS = ['#3b5bdb', '#0f8b8d', '#7c3aed', '#c2410c', '#1f9d55', '#1c1f26'];

function openSettings() {
  const cfg = state.config;
  const defaults = (state.theme && state.theme.defaults) || { kindColors: {} };
  const th = (state.theme && state.theme.theme) || { accent: '#3b5bdb', density: 'comfortable', fontSize: 14, kindColors: {} };
  const kinds = state.catalog.kinds;
  const dlg = dialog('dlg-settings', '設定', `
    <div class="section-title">見た目</div>
    <div class="field"><label>アクセント色</label><div class="swatches">${ACCENTS.map((c) => `<button type="button" class="swatch ${c === th.accent ? 'is-on' : ''}" data-accent="${c}" style="background:${c}" title="${c}"></button>`).join('')}<input type="color" id="t-accent" value="${esc(th.accent)}" style="width:44px;height:28px;padding:2px"></div></div>
    <div class="grid2">
      <div class="field"><label>密度</label><select id="t-density"><option value="comfortable" ${th.density === 'comfortable' ? 'selected' : ''}>ゆったり</option><option value="compact" ${th.density === 'compact' ? 'selected' : ''}>詰める</option></select></div>
      <div class="field"><label>文字サイズ（px）</label><input id="t-font" type="number" min="11" max="20" value="${esc(th.fontSize)}"></div>
    </div>
    <div class="field"><label>種類の色</label><div class="kind-colors">${kinds.map((k) => `<label>${esc(k.short)}<input type="color" data-kind-color="${esc(k.id)}" value="${esc((th.kindColors || {})[k.id] || (defaults.kindColors || {})[k.id] || '#888888')}"></label>`).join('')}</div></div>
    <div class="row"><button type="button" id="t-save" class="primary">見た目を保存</button><button type="button" id="t-css">ユーザー CSS を開く</button><button type="button" id="t-reload">再読み込み</button><button type="button" id="t-reset" class="ghost">既定に戻す</button>
      <span class="small muted">ユーザー CSS（custom.css）は色・余白・幅の変数を何でも上書きできます。</span></div>
    <div class="section-title">実行</div>
    <div class="grid2">
      <div class="field"><label>エージェント（既定の --agent）</label><select id="c-agent">${['claude', 'copilot', 'kiro', 'anthropic'].map((a) => `<option value="${a}" ${a === (cfg.agent || 'claude') ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
      <div class="field"><label>モデル（任意・--model）</label><input id="c-model" class="mono" value="${esc(cfg.model || '')}"></div>
    </div>
    <div class="field"><label>statemachine-use スキルのフォルダ（自動で見つからないとき）</label><input id="c-skill" class="mono" value="${esc(cfg.skillDir || '')}" placeholder="例: C:/work/sandbox/.github/skills/statemachine-use"></div>
    <div class="row"><button type="button" id="c-save">実行の設定を保存</button></div>
    <div class="section-title">道具</div>
    <div class="row"><button type="button" id="tools-check">確認する</button><span class="small muted">python / スキルのスクリプト / playwright-cli / winauto（LLM は使いません）</span></div>
    <div id="tools-list">${state.tools ? toolsHtml(state.tools) : ''}</div>`);
  const pick = (c) => { dlg.querySelector('#t-accent').value = c; for (const s of dlg.querySelectorAll('.swatch')) s.classList.toggle('is-on', s.dataset.accent === c); };
  for (const s of dlg.querySelectorAll('.swatch')) s.addEventListener('click', () => pick(s.dataset.accent));
  const themeForm = () => ({
    accent: dlg.querySelector('#t-accent').value, density: dlg.querySelector('#t-density').value, fontSize: Number(dlg.querySelector('#t-font').value),
    kindColors: Object.fromEntries([...dlg.querySelectorAll('[data-kind-color]')].map((el) => [el.dataset.kindColor, el.value])),
  });
  dlg.querySelector('#t-save').addEventListener('click', async () => {
    const saved = await guard('見た目の保存', () => api.saveTheme(themeForm()));
    if (saved) { applyTheme({ ...state.theme, theme: saved.theme, variables: saved.variables }); toast('見た目を保存しました'); }
  });
  dlg.querySelector('#t-reset').addEventListener('click', async () => {
    const saved = await guard('見た目', () => api.saveTheme((state.theme && state.theme.defaults) || {}));
    if (saved) { applyTheme({ ...state.theme, theme: saved.theme, variables: saved.variables }); openSettings(); }
  });
  dlg.querySelector('#t-css').addEventListener('click', () => guard('ユーザー CSS', () => api.openCustomCss()));
  dlg.querySelector('#t-reload').addEventListener('click', async () => { applyTheme(await guard('見た目', () => api.getTheme())); toast('見た目を読み直しました'); });
  dlg.querySelector('#c-save').addEventListener('click', async () => {
    const next = { ...cfg, agent: dlg.querySelector('#c-agent').value, model: dlg.querySelector('#c-model').value.trim(), skillDir: dlg.querySelector('#c-skill').value.trim() };
    const saved = await guard('設定の保存', () => api.saveConfig(next));
    if (saved) { state.config = saved; toast('設定を保存しました'); }
  });
  dlg.querySelector('#tools-check').addEventListener('click', async () => {
    const btn = dlg.querySelector('#tools-check');
    btn.disabled = true; btn.textContent = '確認中…';
    const res = await guard('道具の確認', () => api.toolStatus(state.root));
    state.tools = res || state.tools;
    btn.disabled = false; btn.textContent = '確認する';
    dlg.querySelector('#tools-list').innerHTML = state.tools ? toolsHtml(state.tools) : '';
  });
}

function toolsHtml(tools) {
  return `<ul class="tool-list">${tools.map((t) => `<li><span><span class="st ${t.ok ? 'ok' : 'ng'}">${t.ok ? '利用可能' : '未準備'}</span><strong>${esc(t.label)}</strong></span><small>${esc(t.summary || '')}</small>${t.hint ? `<small>${esc(t.hint)}</small>` : ''}</li>`).join('')}</ul>`;
}

// --- 起動 -----------------------------------------------------------------------------

async function init() {
  state.catalog = (await guard('種類の取得', () => api.catalog())) || state.catalog;
  state.config = (await guard('設定', () => api.getConfig())) || {};
  applyTheme(await guard('見た目', () => api.getTheme()));
  $('btn-home').addEventListener('click', goHome);
  api.onRunLine((p) => appendLog(p));
  api.onRunExit((p) => {
    state.run.running = false;
    appendLog({ kind: p.code === 0 ? 'stdout' : 'stderr', line: `--- 終了（コード ${p.code}）${p.mode === 'dry-run' ? (p.code === 0 ? ' 定義は有効です' : ' 検証に失敗しました') : ''}` });
    const dlg = $('dlg-run');
    if (dlg.open) { dlg.querySelector('#run-dry').disabled = false; dlg.querySelector('#run-go').disabled = false; dlg.querySelector('#run-stop').disabled = true; }
  });
  window.addEventListener('beforeunload', (e) => { if (state.current && state.current.dirty) { e.preventDefault(); e.returnValue = ''; } });
  const recent = (state.config.recentRoots || [])[0];
  if (recent) { state.root = recent; await loadMachines(); }
  render();
}

init();
