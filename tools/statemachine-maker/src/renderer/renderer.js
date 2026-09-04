'use strict';

// 画面は 2 つ。
//   一覧 … 左に登録したフォルダ、右にそのフォルダのステートマシン（マトリクス）。
//          見に行くのは登録したフォルダの `.statemachine/` だけ。
//   編集 … 工程カードを縦 1 列に並べ、選んだカードだけがその場で開いて設定欄になる。
//          カードの間に「次にどこへ行くか」を出す。
// 記録・中身・AI・動かす・設定はダイアログ。組み立てと検査は main に頼む。
//
// **画面に出す言葉に内部の用語を持ち込まない**（YAML の項目名・コマンドの綴り・ステートの
// 呼び名など）。人が読む言葉に直してから出す。綴りそのものが要る欄（確認コマンドなど）だけ
// が例外で、そこは何を書くかを日本語で添える。
//
// `api` は preload が window へ置いた窓口。**宣言し直さない**（再定義できないので、
// const で受けるとスクリプトごと落ちて画面が真っ白になる。test/preload-contract.test.js）。

const $ = (id) => document.getElementById(id);

const state = {
  config: { roots: [], lastRoot: '' },
  root: '',
  machines: [],
  catalog: { kinds: [], platform: '' },
  theme: null,
  view: 'home',
  current: null,     // { machine, isNew, spec, dirty, warnings, dir }
  open: -1,          // 開いている工程
  pickerAt: -1,      // 追加の種類を選んでいる位置
  preview: null, ai: null, tools: null,
  recording: { source: 'browser', url: '', app: '', text: '', active: false, busy: false, message: '', ok: true },
  run: { lines: [], running: false, input: '', agent: '' },
  fileTab: '',
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

function folderName(p) {
  return String(p || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || p;
}

// 一覧の 2 行目は「どこに置いてあるか」だけ分かればよいので、親フォルダまで。
function folderWhere(p) {
  const clean = String(p || '').replace(/[\\/]+$/, '');
  const cut = clean.lastIndexOf(clean.includes('\\') ? '\\' : '/');
  return cut > 0 ? clean.slice(0, cut) : clean;
}

function saveNameFrom(name) {
  const ascii = String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return ascii || `sm-${Date.now().toString(36)}`;
}

// --- 見た目（設定とユーザー CSS） --------------------------------------------------------

function applyTheme(res) {
  if (!res) return;
  state.theme = res;
  const root = document.documentElement;
  for (const [k, v] of Object.entries(res.variables || {})) root.style.setProperty(k, v);
  $('custom-css').textContent = res.customCss || '';
}

// --- 手順の形 -------------------------------------------------------------------------

function emptyStep(kind) {
  return { id: '', kind, title: '', detail: '', target: '', check: '', checkRetries: 1, outcomes: [], recorded: [], rawTransitions: false };
}

function newSpec() {
  return {
    version: 3, name: '', machine: '', purpose: '', finish: '', notes: '', maxSteps: 30,
    terminals: { done: { id: 'complete', description: '完了' }, abort: { id: 'failed', description: '中止' } },
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
  return { ...spec, machine: spec.machine || saveNameFrom(spec.name), steps: spec.steps.map((s) => ({ ...s, checkRetries: Number(s.checkRetries) || 0 })) };
}

// カードの間に出す「次にどこへ行くか」。判定の正典は main（表示だけここで組む）。
function nextsOf(spec, index) {
  const step = spec.steps[index];
  const count = spec.steps.length;
  const where = (to) => {
    if (to === 'done') return { text: '完了', cls: 'done' };
    if (to === 'abort') return { text: '中止', cls: 'abort' };
    if (to === 'next') return index + 1 < count ? { text: `次へ（${index + 2}）`, cls: '' } : { text: '完了', cls: 'done' };
    const n = Number(String(to).slice(5));
    if (n === index + 1) return { text: 'この工程をやり直す', cls: 'back' };
    return n <= index ? { text: `${n} へ戻る`, cls: 'back' } : { text: `${n} へ`, cls: '' };
  };
  if (step.rawTransitions) return [{ label: '文章の条件', cls: 'raw', text: 'この画面では直せません' }];
  if (step.outcomes.length) return step.outcomes.map((o) => ({ label: o.label || '（結果）', cls: step.check ? 'gate' : '', ...where(o.to) }));
  if (step.check) return [{ label: '確認できたら', cls: 'gate', ...where('next') }];
  return [{ label: 'できた', cls: 'ok', ...where('next') }, { label: 'できなかった', cls: 'ng', ...where('abort') }];
}

// 畳んだカードの 1 文。動詞で始め、細かいことは開いてから。
function summary(step) {
  const kind = kindOf(step.kind);
  const first = String(step.detail || '').split('\n').map((l) => l.replace(/^\d+\.\s*/, '').trim()).find(Boolean) || '';
  const what = step.title || (step.kind === 'command' ? step.target : first);
  if (!what) return { v: '', text: kind.label, empty: true };
  const verb = { browser: 'ブラウザで', windows: `${step.target || 'アプリ'} で`, skill: `${step.target || 'スキル'} に任せて`, command: '実行:', agent: 'AI が' }[step.kind] || '';
  return { v: verb, text: what, empty: false };
}

// --- フォルダと一覧 ---------------------------------------------------------------------

async function loadMachines() {
  state.machines = state.root ? ((await guard('一覧の取得', () => api.listMachines(state.root))) || []) : [];
}

async function selectRoot(root) {
  if (!root || root === state.root) return;
  state.root = root;
  await guard('フォルダ', () => api.selectRoot(root));
  await loadMachines();
  render();
}

async function addFolder() {
  const cfg = await guard('フォルダの登録', () => api.addRoot());
  if (!cfg) return;
  state.config = cfg;
  state.root = cfg.lastRoot;
  await loadMachines();
  render();
}

async function removeFolder(root) {
  if (!confirm(`${folderName(root)} を一覧から外しますか？（フォルダの中身は消えません）`)) return;
  const cfg = await guard('フォルダ', () => api.removeRoot(root));
  if (!cfg) return;
  state.config = cfg;
  if (state.root === root) state.root = cfg.lastRoot;
  await loadMachines();
  render();
}

function goHome() {
  if (state.current && state.current.dirty && state.view === 'editor' && !confirm('保存していない変更があります。一覧へ戻りますか？')) return;
  state.view = 'home';
  state.current = null;
  render();
}

async function openMachine(machine) {
  const res = await guard('読み込み', () => api.readMachine(state.root, machine));
  if (!res) return;
  const raw = res.raw;
  raw.steps = raw.steps.map((s) => ({ ...emptyStep(s.kind), ...s, outcomes: s.outcomes || [], recorded: s.recorded || [] }));
  state.current = { machine, isNew: false, spec: raw, dirty: false, warnings: res.warnings || [], dir: res.dir };
  state.view = 'editor';
  state.open = -1;
  state.preview = null;
  state.ai = null;
  state.run.lines = [];
  render();
}

function newMachine() {
  if (!state.root) { toast('先にフォルダを登録してください', true); return; }
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
  const editing = state.view === 'editor' && state.current;
  main.innerHTML = editing ? editorHtml() : homeHtml();
  if (editing) bindEditor(main); else bindHome(main);
}

function renderBar() {
  const editing = state.view === 'editor' && state.current;
  $('btn-home').hidden = !editing;
  const center = $('bar-center');
  const right = $('bar-right');
  if (!editing) {
    center.innerHTML = '';
    right.innerHTML = '<button type="button" id="b-settings" class="ghost" title="設定">⚙</button>';
    $('b-settings').addEventListener('click', openSettings);
    return;
  }
  const spec = state.current.spec;
  center.innerHTML = `<input class="title-input" id="m-name" value="${esc(spec.name)}" placeholder="名前を付ける（例: 月次の勤怠集計）" aria-label="名前">`;
  right.innerHTML = `<span id="dirty-mark" class="dirty" ${state.current.dirty ? '' : 'hidden'}>● 未保存</span>
    <button type="button" id="b-record" class="ghost">記録</button>
    <button type="button" id="b-files" class="ghost">中身</button>
    <button type="button" id="b-ai" class="ghost">AI</button>
    <button type="button" id="b-run" class="ghost" ${state.current.isNew ? 'disabled title="保存すると動かせます"' : ''}>動かす</button>
    <button type="button" id="b-settings" class="ghost" title="設定">⚙</button>
    <button type="button" id="b-save" class="primary">保存</button>`;
  let touched = !state.current.isNew || !!spec.machine;
  $('m-name').addEventListener('input', (e) => {
    spec.name = e.target.value;
    if (!touched) { spec.machine = saveNameFrom(e.target.value); const m = $('m-save-name'); if (m) m.value = spec.machine; }
    markDirty();
  });
  const saveName = $('m-save-name');
  if (saveName) saveName.addEventListener('input', () => { touched = true; });
  $('b-record').addEventListener('click', openRecord);
  $('b-files').addEventListener('click', openFiles);
  $('b-ai').addEventListener('click', openAi);
  $('b-run').addEventListener('click', openRun);
  $('b-settings').addEventListener('click', openSettings);
  $('b-save').addEventListener('click', saveMachine);
}

// --- 一覧（左: フォルダ／右: ステートマシン） -----------------------------------------------

function homeHtml() {
  const roots = state.config.roots || [];
  if (!roots.length) {
    return `<div class="blank">
      <h2>フォルダを登録します</h2>
      <p>登録したフォルダの中に置いたステートマシンだけを扱います。</p>
      <div class="row"><button type="button" class="primary" id="h-add">フォルダを登録</button></div>
    </div>`;
  }
  const list = roots.map((r) => `<li class="${r === state.root ? 'is-on' : ''}">
    <button type="button" class="pick" data-root="${esc(r)}" title="${esc(r)}">
      <span class="name">${esc(folderName(r))}</span><span class="where">${esc(folderWhere(r))}</span>
    </button>
    <button type="button" class="drop" data-drop="${esc(r)}" title="一覧から外す" aria-label="${esc(folderName(r))} を一覧から外す">✕</button>
  </li>`).join('');
  const cards = state.machines.map((m) => `<button type="button" class="machine-card" data-open="${esc(m.machine)}">
    <span class="name">${esc(m.name)}</span>
    <span class="desc">${esc(m.description || '')}</span>
    <span class="meta">${m.steps ? `${m.steps} 工程` : ''}</span>
  </button>`).join('');
  const body = state.root
    ? `<div class="machine-head">
        <div><h1>${esc(folderName(state.root))}</h1><div class="where">${esc(state.root)}</div></div>
        <button type="button" class="primary" id="h-new">＋ 新規</button>
      </div>
      <div class="matrix">${cards}<button type="button" class="machine-card new" id="h-new-card">＋ 新しいステートマシン</button></div>`
    : '<div class="blank"><h2>左のフォルダを選びます</h2></div>';
  return `<div class="home">
    <aside class="folder-pane">
      <div class="pane-head"><h2>フォルダ</h2><button type="button" class="tiny" id="h-add" title="フォルダを登録">＋</button></div>
      <ul class="folder-list">${list}</ul>
    </aside>
    <section class="machine-pane">${body}</section>
  </div>`;
}

function bindHome(main) {
  const on = (id, fn) => { const el = main.querySelector(`#${id}`); if (el) el.addEventListener('click', fn); };
  on('h-add', addFolder);
  on('h-new', newMachine);
  on('h-new-card', newMachine);
  for (const b of main.querySelectorAll('[data-root]')) b.addEventListener('click', () => selectRoot(b.dataset.root));
  for (const b of main.querySelectorAll('[data-drop]')) b.addEventListener('click', () => removeFolder(b.dataset.drop));
  for (const b of main.querySelectorAll('[data-open]')) b.addEventListener('click', () => openMachine(b.dataset.open));
}

// --- 編集 -----------------------------------------------------------------------------

function editorHtml() {
  const spec = state.current.spec;
  const parts = [];
  parts.push(`<div class="overview">
    <textarea class="purpose" id="m-purpose" rows="1" placeholder="何をする手順かを 1〜2 文で">${esc(spec.purpose)}</textarea>
    <details><summary>詳しい設定</summary><div class="grid2" style="margin-top:8px">
      <div class="field"><label for="m-save-name">保存名</label><input id="m-save-name" class="mono" value="${esc(spec.machine)}" placeholder="英数字とハイフン" ${state.current.isNew ? '' : 'readonly title="作ったあとは変えられません"'}><small>フォルダの中でこの名前で保存されます。</small></div>
      <div class="field"><label for="m-max">進める回数の上限</label><input id="m-max" type="number" min="1" max="500" value="${esc(spec.maxSteps)}"><small>やり直しが続いたときに、ここで止まります。</small></div>
      <div class="field"><label for="m-finish">終わりの目安</label><textarea id="m-finish" rows="2" placeholder="どうなったら終わりか">${esc(spec.finish)}</textarea></div>
      <div class="field"><label for="m-notes">気をつけること</label><textarea id="m-notes" rows="2" placeholder="例: 承認は押さない（人が行う）">${esc(spec.notes)}</textarea></div>
    </div></details>
  </div>`);
  if (!spec.steps.length) {
    parts.push(`<div class="empty-steps"><p>最初の工程を選びます。</p>${pickerHtml(0)}</div>`);
  } else {
    spec.steps.forEach((_s, i) => { parts.push(stepHtml(spec, i)); parts.push(edgeHtml(spec, i)); });
  }
  parts.push(`<div class="terminal"><span class="icon">✓</span><span class="name">完了</span></div>`);
  parts.push(`<div class="terminal abort"><span class="icon">✕</span><span class="name">中止</span></div>`);
  parts.push(`<div class="notes" id="notes">${notesHtml()}</div>`);
  return `<div class="editor">${parts.join('')}</div>`;
}

function notesHtml() {
  const cur = state.current;
  const errors = (state.preview && state.preview.errors) || [];
  const warnings = [...(cur.warnings || []), ...((state.preview && state.preview.warnings) || [])];
  return `${errors.length ? `<div class="err"><ul>${errors.map((e) => `<li>${esc(e)}</li>`).join('')}</ul></div>` : ''}
    ${warnings.length ? `<div class="warn"><ul>${warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}`;
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
  if (step.check) sub.push('<span class="chk">✓ 確認あり</span>');
  if (step.recorded && step.recorded.length) sub.push(`<span class="rec">● 記録 ${step.recorded.length} 件</span>`);
  return `<div class="step" data-step="${index}"><div class="step-card ${open ? 'is-open' : ''}">
    <div class="step-head" role="button" tabindex="0" aria-expanded="${open}">
      <span class="step-icon k-${esc(step.kind)}">${index + 1}</span>
      <span class="step-summary">
        <div class="sentence">${s.empty ? `<span class="v">${esc(s.text)}（内容を入れます）</span>` : `${s.v ? `<span class="v">${esc(s.v)}</span> ` : ''}${esc(s.text)}`}</div>
        <div class="sub">${sub.join('<span>·</span>')}</div>
      </span>
      <span class="step-right"><span class="chev">›</span></span>
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
    <div><button type="button" class="tiny" data-unrecord>記録を外す</button></div></div>` : '';
  const check = kind.check ? `<div class="field"><label>できたか確かめるコマンド（任意）</label>
    <input data-field="check" class="mono" value="${esc(step.check)}" placeholder="${esc(kind.check.placeholder || '')}">
    <small>うまくいったときだけ次へ進みます。書かなければ AI の申告で進みます。</small></div>` : '';
  const dest = (to) => {
    const opts = [['next', index + 1 < spec.steps.length ? `次へ（${index + 2}）` : '次へ（完了）'], ['done', '完了'], ['abort', '中止']];
    spec.steps.forEach((_s, i) => { opts.push([`step:${i + 1}`, `${i + 1} へ${i < index ? '戻る' : i === index ? '（やり直す）' : ''}`]); });
    return opts.map(([v, l]) => `<option value="${v}" ${v === to ? 'selected' : ''}>${esc(l)}</option>`).join('');
  };
  const branches = step.rawTransitions
    ? `<div class="section-title">次にどこへ行くか</div>
      <small class="muted">文章で書かれた条件が入っているため、この画面では直せません。そのまま残します。</small>
      <div><button type="button" class="tiny" data-unraw>この画面で直せる形にする</button></div>`
    : `<div class="section-title">結果で分ける（任意）</div>
      <small class="muted">分けないときは「できた → 次へ」「できなかった → 中止」です。</small>
      ${step.outcomes.map((o, i) => `<div class="branch-row" data-branch="${i}"><input data-bfield="label" value="${esc(o.label)}" placeholder="結果の名前"><select data-bfield="to">${dest(o.to)}</select><button type="button" data-bremove title="削除">✕</button></div>`).join('')}
      <div><button type="button" class="tiny" data-badd>＋ 分け方を足す</button></div>`;
  return `<div class="step-body">
    <div class="field"><label>種類</label><div class="seg">${seg}</div></div>
    <div class="field"><label>名前（任意）</label><input data-field="title" value="${esc(step.title)}" placeholder="例: 申請一覧を開く"></div>
    ${target}
    <div class="field"><label>${esc(kind.detail.label)}${kind.detail.required ? '' : '（任意）'}</label><textarea data-field="detail" rows="5" placeholder="${esc(kind.detail.placeholder || '')}">${esc(step.detail)}</textarea>
      <small>毎回変わる値は <code>{{ }}</code> で囲みます（例: <code>{{month}}</code>）。動かすときに聞かれます。</small></div>
    ${recorded}
    ${check}
    ${branches}
    <details class="more"><summary>詳しい設定</summary><div class="grid2" style="margin-top:8px">
      <div class="field"><label>工程の記号</label><input data-field="id" class="mono" value="${esc(step.id)}" placeholder="step_${index + 1}"><small>他の工程から戻るときの目印です。</small></div>
      ${kind.check ? `<div class="field"><label>やり直す回数</label><input data-field="checkRetries" type="number" min="0" max="5" value="${esc(step.checkRetries)}"><small>確認に通らなかったとき、この回数まで同じ工程をやり直します。</small></div>` : ''}
    </div></details>
    <div class="step-actions">
      <div class="left"><button type="button" class="tiny" data-move="up" ${index === 0 ? 'disabled' : ''}>↑ 上へ</button><button type="button" class="tiny" data-move="down" ${index === spec.steps.length - 1 ? 'disabled' : ''}>↓ 下へ</button></div>
      <button type="button" class="tiny danger" data-remove>削除</button>
    </div>
  </div>`;
}

function edgeHtml(spec, index) {
  const nexts = nextsOf(spec, index);
  const at = index + 1;
  return `<div class="edge" data-edge="${index}">
    <span class="plus"><button type="button" data-insert="${at}" title="ここに工程を足す">+</button></span>
    <div class="lines">${nexts.map((e) => `<span class="t"><span class="lbl ${e.cls}">${esc(e.label)}</span><span>→</span><span class="to ${e.cls}">${esc(e.text)}</span></span>`).join('')}</div>
    ${state.pickerAt === at ? pickerHtml(at) : ''}
  </div>`;
}

function bindEditor(main) {
  const spec = state.current.spec;
  const bindTop = (id, key, num) => {
    const el = main.querySelector(`#${id}`);
    if (el) el.addEventListener('input', () => { spec[key] = num ? (Number(el.value) || 30) : el.value; markDirty(); });
  };
  bindTop('m-purpose', 'purpose');
  bindTop('m-save-name', 'machine');
  bindTop('m-max', 'maxSteps', true);
  bindTop('m-finish', 'finish');
  bindTop('m-notes', 'notes');
  const purpose = main.querySelector('#m-purpose');
  if (purpose) { const grow = () => { purpose.style.height = 'auto'; purpose.style.height = `${purpose.scrollHeight + 2}px`; }; purpose.addEventListener('input', grow); grow(); }
  for (const b of main.querySelectorAll('[data-add]')) b.addEventListener('click', () => insertStep(Number(b.closest('[data-at]').dataset.at), b.dataset.add));
  for (const b of main.querySelectorAll('[data-insert]')) b.addEventListener('click', () => { const at = Number(b.dataset.insert); state.pickerAt = state.pickerAt === at ? -1 : at; render(); });
  for (const card of main.querySelectorAll('[data-step]')) bindStep(card);
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
  on('[data-badd]', () => {
    step.outcomes.push({ label: '', to: 'next' });
    markDirty();
    render();
    const inputs = document.querySelectorAll(`[data-step="${index}"] [data-branch] input`);
    if (inputs.length) inputs[inputs.length - 1].focus();
  });
  on('[data-unrecord]', () => { step.recorded = []; markDirty(); render(); });
  on('[data-unraw]', () => {
    if (!confirm('文章の条件を捨てて、「できた → 次へ」「できなかった → 中止」に置き換えますか？')) return;
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
    if (!confirm(`${index + 1} 番目の工程を削除しますか？`)) return;
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
  card.querySelector('.step-head').replaceWith(fresh);
  const toggle = () => { state.open = state.open === index ? -1 : index; render(); };
  fresh.addEventListener('click', toggle);
  fresh.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
  refreshEdge(index);
}

function refreshEdge(index) {
  const edge = document.querySelector(`[data-edge="${index}"] .lines`);
  if (!edge) return;
  edge.innerHTML = nextsOf(state.current.spec, index).map((e) => `<span class="t"><span class="lbl ${e.cls}">${esc(e.label)}</span><span>→</span><span class="to ${e.cls}">${esc(e.text)}</span></span>`).join('');
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
  const res = await guard('確認', () => api.previewMachine(specPayload()));
  if (!res) return null;
  state.preview = res;
  const notes = $('notes');
  if (notes) notes.innerHTML = notesHtml();
  return res;
}

async function saveMachine() {
  if (!state.current) return;
  const preview = await previewMachine();
  if (!preview || preview.errors.length) { toast(preview ? preview.errors[0] : '保存できません', true); return; }
  const payload = specPayload();
  if (state.current.isNew) {
    const exists = await guard('確認', () => api.machineExists(state.root, payload.machine));
    if (exists && !confirm(`「${payload.machine}」は既にあります。置き換えますか？`)) return;
  }
  const res = await guard('保存', () => api.saveMachine(state.root, payload));
  if (!res) return;
  Object.assign(state.current, { machine: res.machine, isNew: false, dirty: false, dir: res.dir, warnings: res.warnings || [] });
  state.current.spec.machine = res.machine;
  toast('保存しました');
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
  const target = windows ? { field: 'app', value: rec.app, label: 'アプリ', placeholder: '例: 勤怠管理' } : { field: 'url', value: rec.url, label: '始める URL', placeholder: 'https://…' };
  const dlg = dialog('dlg-record', '操作を記録して工程にする', `
    <p class="small muted" style="margin:0">人がやって見せた操作から工程を作ります。入力した値は <code>{{ }}</code> の毎回変える値になります（パスワードらしい値は残しません）。</p>
    <div class="grid2">
      <div class="field"><label>記録するもの</label><select id="r-source" ${rec.active ? 'disabled' : ''}>${kinds.map((k) => `<option value="${esc(k.id)}" ${rec.source === k.id ? 'selected' : ''}>${esc(k.label)}</option>`).join('')}</select></div>
      <div class="field"><label>${esc(target.label)}</label><input id="r-target" class="mono" data-rec="${target.field}" value="${esc(target.value)}" placeholder="${esc(target.placeholder)}" ${rec.active ? 'disabled' : ''}></div>
    </div>
    <div class="row"><button type="button" id="r-start" ${rec.active || rec.busy || (windows && !onWindows) ? 'disabled' : ''}>記録を始める</button>
      <button type="button" id="r-stop" class="primary" ${!rec.active || rec.busy ? 'disabled' : ''}>終えて工程にする</button>
      <span class="small muted">${windows ? (onWindows ? '操作したあとに終えてください。' : 'Windows のアプリは Windows でだけ記録できます。') : '見える形でブラウザが開きます。'}</span></div>
    <p id="r-message" class="msg ${rec.ok ? '' : 'err'}" ${rec.message ? '' : 'hidden'}>${esc(rec.message)}</p>
    <details ${rec.text ? 'open' : ''}><summary>別のパソコンで取った記録を貼り付ける</summary>
      <div class="field" style="margin-top:8px">
        <textarea id="r-text" class="mono" rows="6" placeholder="記録した内容を貼り付けます">${esc(rec.text)}</textarea>
        <div><button type="button" id="r-import" ${rec.busy ? 'disabled' : ''}>貼り付けた記録を工程にする</button></div>
      </div></details>`);
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
  rec.busy = true;
  rec.ok = true;
  rec.message = action === 'start' ? '始めています…' : '工程にしています…';
  openRecord();
  let res;
  try {
    res = action === 'start' ? await api.recordingStart(payload) : action === 'stop' ? await api.recordingStop(payload) : await api.recordingImport(payload);
  } catch (err) { res = { error: String((err && err.message) || err) }; }
  rec.busy = false;
  if (!res || res.error) {
    rec.message = (res && res.error) || 'うまくいきませんでした';
    rec.ok = false;
    if (action === 'stop') rec.active = false;
    openRecord();
    return;
  }
  if (action === 'start') {
    rec.active = true;
    rec.message = '操作してください。終わったら「終えて工程にする」を押します。';
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
  toast(`${steps.length} 工程を作りました`);
}

async function openFiles() {
  const dlg = dialog('dlg-files', 'できあがるもの', '<p class="muted small">組み立てています…</p>');
  const res = state.preview || await previewMachine();
  if (!res) { dlg.querySelector('.dlg-body').innerHTML = '<p class="msg err">組み立てられませんでした</p>'; return; }
  const files = res.files || {};
  const names = Object.keys(files);
  if (!names.includes(state.fileTab)) state.fileTab = names[0] || '';
  const paint = () => {
    dlg.querySelector('.dlg-body').innerHTML = `<div class="row" style="justify-content:space-between">
        <span class="small muted">保存すると、フォルダにこの中身で書き出されます。このアプリが無くても動きます。</span>
        ${state.current.isNew ? '' : '<button type="button" class="tiny" id="f-open">フォルダを開く</button>'}
      </div>
      ${res.errors && res.errors.length ? `<div class="msg err">${res.errors.map(esc).join('\n')}</div>` : ''}
      <div class="file-tabs">${names.map((n) => `<button type="button" data-file="${esc(n)}" class="${n === state.fileTab ? 'is-on' : ''}">${esc(n)}</button>`).join('')}</div>
      <pre>${esc(files[state.fileTab] || '')}</pre>`;
    for (const b of dlg.querySelectorAll('[data-file]')) b.addEventListener('click', () => { state.fileTab = b.dataset.file; paint(); });
    const open = dlg.querySelector('#f-open');
    if (open) open.addEventListener('click', () => guard('フォルダ', () => api.openMachineFolder(state.root, state.current.machine)));
  };
  paint();
}

async function openAi() {
  const dlg = dialog('dlg-ai', 'AI に仕上げてもらう', '<p class="muted small">用意しています…</p>');
  const res = state.ai || await guard('用意', () => api.instruction(state.root, specPayload()));
  if (!res) { dlg.querySelector('.dlg-body').innerHTML = '<p class="msg err">用意できませんでした（名前と工程を確かめてください）</p>'; return; }
  state.ai = res;
  dlg.querySelector('.dlg-body').innerHTML = `<p class="small muted" style="margin:0">この文をコピーして AI のターミナルに貼ると、待ち時間の入れ方や結果の読み取り方を補ってくれます。作った手順は AI 無しでも動きます。</p>
    <div class="row"><button type="button" id="ai-copy" class="primary">コピー</button><button type="button" id="ai-terminal">フォルダでターミナルを開く</button></div>
    <pre>${esc(res.prompt)}</pre>`;
  dlg.querySelector('#ai-copy').addEventListener('click', async () => { await api.copyText(res.prompt); toast('コピーしました'); });
  dlg.querySelector('#ai-terminal').addEventListener('click', () => guard('ターミナル', () => api.openTerminal(state.root)));
}

function openRun() {
  const run = state.run;
  const cur = state.current;
  if (cur.isNew) return;
  const agent = run.agent || state.config.agent || 'claude';
  const dlg = dialog('dlg-run', '動かす', `
    ${cur.dirty ? '<p class="msg" style="color:var(--warn)">保存していない変更があります。動くのは保存した内容です。</p>' : ''}
    <div class="row"><button type="button" id="run-check" ${run.running ? 'disabled' : ''}>点検する</button><button type="button" id="run-go" class="primary" ${run.running ? 'disabled' : ''}>動かす</button><button type="button" id="run-stop" class="danger" ${run.running ? '' : 'disabled'}>止める</button>
      <span class="small muted">点検は、手順に抜けがないかを見るだけです。</span></div>
    <div class="grid2">
      <div class="field"><label>使う AI</label><select id="run-agent">${['claude', 'copilot', 'kiro', 'anthropic'].map((a) => `<option value="${a}" ${a === agent ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
      <div class="field"><label>最初に渡す文（任意）</label><input id="run-input" value="${esc(run.input)}"></div>
    </div>
    <div class="log" id="run-log">${run.lines.map((l) => `<div class="${l.kind === 'stderr' ? 'e' : ''}">${esc(l.line)}</div>`).join('') || '<span class="muted">ここに様子が出ます</span>'}</div>`);
  dlg.querySelector('#run-agent').addEventListener('change', (e) => { run.agent = e.target.value; });
  dlg.querySelector('#run-input').addEventListener('input', (e) => { run.input = e.target.value; });
  dlg.querySelector('#run-check').addEventListener('click', () => startRun('check'));
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

const ACCENTS = ['#3b5bdb', '#0f8b8d', '#7c3aed', '#c2410c', '#1f9d55', '#1c1f26'];

function openSettings() {
  const cfg = state.config;
  const defaults = (state.theme && state.theme.defaults) || { kindColors: {} };
  const th = (state.theme && state.theme.theme) || { accent: '#3b5bdb', density: 'comfortable', fontSize: 14, kindColors: {} };
  const kinds = state.catalog.kinds;
  const dlg = dialog('dlg-settings', '設定', `
    <div class="section-title">見た目</div>
    <div class="field"><label>色</label><div class="swatches">${ACCENTS.map((c) => `<button type="button" class="swatch ${c === th.accent ? 'is-on' : ''}" data-accent="${c}" style="background:${c}" title="${c}"></button>`).join('')}<input type="color" id="t-accent" value="${esc(th.accent)}" style="width:44px;height:28px;padding:2px"></div></div>
    <div class="grid2">
      <div class="field"><label>間隔</label><select id="t-density"><option value="comfortable" ${th.density === 'comfortable' ? 'selected' : ''}>ゆったり</option><option value="compact" ${th.density === 'compact' ? 'selected' : ''}>詰める</option></select></div>
      <div class="field"><label>文字の大きさ</label><input id="t-font" type="number" min="11" max="20" value="${esc(th.fontSize)}"></div>
    </div>
    <div class="field"><label>種類ごとの色</label><div class="kind-colors">${kinds.map((k) => `<label>${esc(k.short)}<input type="color" data-kind-color="${esc(k.id)}" value="${esc((th.kindColors || {})[k.id] || (defaults.kindColors || {})[k.id] || '#888888')}"></label>`).join('')}</div></div>
    <div class="row"><button type="button" id="t-save" class="primary">見た目を保存</button><button type="button" id="t-css">自分で書く（CSS）</button><button type="button" id="t-reload">読み直す</button><button type="button" id="t-reset" class="ghost">元に戻す</button></div>
    <div class="section-title">動かすとき</div>
    <div class="grid2">
      <div class="field"><label>使う AI</label><select id="c-agent">${['claude', 'copilot', 'kiro', 'anthropic'].map((a) => `<option value="${a}" ${a === (cfg.agent || 'claude') ? 'selected' : ''}>${a}</option>`).join('')}</select></div>
      <div class="field"><label>モデル（任意）</label><input id="c-model" class="mono" value="${esc(cfg.model || '')}"></div>
    </div>
    <div class="field"><label>手順を動かす仕組みの置き場（自動で見つからないときだけ）</label><input id="c-skill" class="mono" value="${esc(cfg.skillDir || '')}"></div>
    <div class="row"><button type="button" id="c-save">保存</button></div>
    <div class="section-title">準備の確認</div>
    <div class="row"><button type="button" id="tools-check">確かめる</button></div>
    <div id="tools-list">${state.tools ? toolsHtml(state.tools) : ''}</div>`);
  const pick = (c) => { dlg.querySelector('#t-accent').value = c; for (const s of dlg.querySelectorAll('.swatch')) s.classList.toggle('is-on', s.dataset.accent === c); };
  for (const s of dlg.querySelectorAll('.swatch')) s.addEventListener('click', () => pick(s.dataset.accent));
  const themeForm = () => ({
    accent: dlg.querySelector('#t-accent').value, density: dlg.querySelector('#t-density').value, fontSize: Number(dlg.querySelector('#t-font').value),
    kindColors: Object.fromEntries([...dlg.querySelectorAll('[data-kind-color]')].map((el) => [el.dataset.kindColor, el.value])),
  });
  dlg.querySelector('#t-save').addEventListener('click', async () => {
    const saved = await guard('保存', () => api.saveTheme(themeForm()));
    if (saved) { applyTheme({ ...state.theme, theme: saved.theme, variables: saved.variables }); toast('保存しました'); }
  });
  dlg.querySelector('#t-reset').addEventListener('click', async () => {
    const saved = await guard('見た目', () => api.saveTheme(defaults));
    if (saved) { applyTheme({ ...state.theme, theme: saved.theme, variables: saved.variables }); openSettings(); }
  });
  dlg.querySelector('#t-css').addEventListener('click', () => guard('CSS', () => api.openCustomCss()));
  dlg.querySelector('#t-reload').addEventListener('click', async () => { applyTheme(await guard('見た目', () => api.getTheme())); toast('読み直しました'); });
  dlg.querySelector('#c-save').addEventListener('click', async () => {
    const next = { ...cfg, agent: dlg.querySelector('#c-agent').value, model: dlg.querySelector('#c-model').value.trim(), skillDir: dlg.querySelector('#c-skill').value.trim() };
    const saved = await guard('保存', () => api.saveConfig(next));
    if (saved) { state.config = saved; toast('保存しました'); }
  });
  dlg.querySelector('#tools-check').addEventListener('click', async () => {
    const btn = dlg.querySelector('#tools-check');
    btn.disabled = true;
    btn.textContent = '確かめています…';
    const res = await guard('確認', () => api.toolStatus(state.root));
    state.tools = res || state.tools;
    btn.disabled = false;
    btn.textContent = '確かめる';
    dlg.querySelector('#tools-list').innerHTML = state.tools ? toolsHtml(state.tools) : '';
  });
}

function toolsHtml(tools) {
  return `<ul class="tool-list">${tools.map((t) => `<li><span><span class="st ${t.ok ? 'ok' : 'ng'}">${t.ok ? '使えます' : '未準備'}</span><strong>${esc(t.label)}</strong></span><small>${esc(t.summary || '')}</small>${t.hint ? `<small>${esc(t.hint)}</small>` : ''}</li>`).join('')}</ul>`;
}

// --- 起動 -----------------------------------------------------------------------------

async function init() {
  state.catalog = (await guard('準備', () => api.catalog())) || state.catalog;
  state.config = (await guard('設定', () => api.getConfig())) || state.config;
  applyTheme(await guard('見た目', () => api.getTheme()));
  $('btn-home').addEventListener('click', goHome);
  api.onRunLine((p) => appendLog(p));
  api.onRunExit((p) => {
    state.run.running = false;
    appendLog({ kind: p.code === 0 ? 'stdout' : 'stderr', line: p.code === 0 ? (p.mode === 'check' ? '— 点検しました。抜けはありません' : '— 終わりました') : '— 止まりました' });
    const dlg = $('dlg-run');
    if (dlg.open) { dlg.querySelector('#run-check').disabled = false; dlg.querySelector('#run-go').disabled = false; dlg.querySelector('#run-stop').disabled = true; }
  });
  window.addEventListener('beforeunload', (e) => { if (state.current && state.current.dirty) { e.preventDefault(); e.returnValue = ''; } });
  state.root = state.config.lastRoot || (state.config.roots || [])[0] || '';
  if (state.root) await loadMachines();
  render();
}

init();
