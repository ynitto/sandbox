'use strict';

// 画面は 2 つ。
//   一覧 … 左に登録したフォルダ、右にそのフォルダのワークフロー（マトリクス）。
//          見に行くのは登録したフォルダの `.statemachine/` だけ。
//   編集 … 左に工程の流れ、右に選んだ工程の設定を置く。
// 記録・生成ファイル・AI 支援・実行環境はダイアログ。組み立てと検査は main に頼む。
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
  agents: [],
  catalog: { kinds: [], platform: '' },
  view: 'home',
  current: null,     // { machine, isNew, spec, dirty, warnings, dir }
  open: null,        // 選択中の工程番号、'workflow'、または未選択
  pickerAt: -1,      // 追加の種類を選んでいる位置
  preview: null, tools: null,
  aiDraft: { mode: 'draft', phase: 'input', requestId: '', busy: false, request: '', history: [], questions: [], answers: {}, result: null, error: '', message: '' },
  aiReview: { mode: 'review', phase: 'input', requestId: '', busy: false, focus: '', scope: null, history: [], questions: [], answers: {}, result: null, error: '', message: '' },
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

function selectedAgent(preferred = '') {
  if (state.agents.includes(preferred)) return preferred;
  if (state.agents.includes('aider')) return 'aider';
  return state.agents[0] || '';
}

function resetAi(flow, keepInput = false) {
  const kept = keepInput
    ? { request: flow.request || '', focus: flow.focus || '', scope: flow.scope || null }
    : {};
  Object.assign(flow, {
    phase: 'input', requestId: '', busy: false, request: '', focus: '', scope: null,
    history: [], questions: [], answers: {}, result: null, error: '', message: '', ...kept,
  });
}

function cancelAi(flow) {
  if (!flow.busy) return;
  const requestId = flow.requestId === 'pending' ? '' : flow.requestId;
  api.aiStop(requestId).catch(() => {});
  resetAi(flow, true);
}

function agentOptions(preferred = '') {
  const selected = selectedAgent(preferred);
  if (!state.agents.length) return '<option value="">利用できる AI がありません</option>';
  return state.agents.map((name) => `<option value="${esc(name)}" ${name === selected ? 'selected' : ''}>${esc(name)}</option>`).join('');
}

async function loadAgents() {
  state.agents = (await guard('AI 一覧', () => api.listAgents(state.root))) || [];
  state.run.agent = selectedAgent(state.run.agent || state.config.agent);
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

// --- 手順の形 -------------------------------------------------------------------------

function emptyStep(kind) {
  return { id: '', kind, title: '', detail: '', target: '', check: '', checkRetries: 1, outcomes: [], recorded: [], rawTransitions: false };
}

function newSpec() {
  return {
    version: 3, name: '', machine: '', purpose: '', finish: '', notes: '', maxSteps: 30,
    terminals: { done: { id: 'complete', description: '完了' }, abort: { id: 'failed', description: '中止' } },
    ends: [], steps: [], preserved: null,
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
    if (to.startsWith('end:')) return { text: endName(spec, to.slice(4)), cls: 'done' };
    if (to === 'next') return index + 1 < count ? { text: `次へ（${index + 2}）`, cls: '' } : { text: '完了', cls: 'done' };
    const n = Number(String(to).slice(5));
    if (n === index + 1) return { text: 'この工程をやり直す', cls: 'back' };
    return n <= index ? { text: `${n} へ戻る`, cls: 'back' } : { text: `${n} へ`, cls: '' };
  };
  if (step.rawTransitions) return [{ label: '別ファイルの条件', cls: 'raw', text: 'この画面では直せません' }];
  if (step.outcomes.length) return step.outcomes.map((o) => ({ ...whenChip(o), cls: step.check ? 'gate' : '', ...where(o.to) }));
  if (step.check) return [{ label: '確認できたら', cls: 'gate', ...where('next') }];
  return [{ label: 'できた', cls: 'ok', ...where('next') }, { label: 'できなかった', cls: 'ng', ...where('abort') }];
}

// 完了・中止のほかの終わり方（手で書いた定義が持つもの）の呼び名。
function endName(spec, id) {
  const end = (spec.ends || []).find((e) => e.id === id);
  return (end && end.description) || id;
}

// 行き先の決め方の 4 つ。画面ではこの言葉で見せる。
const WHENS = [
  { id: 'label', label: '回答が指定の言葉で始まる', hint: '回答の先頭を確認します', placeholder: '例: APPROVED' },
  { id: 'text', label: '条件に当てはまる', hint: '入力した条件を AI が確認します', placeholder: '例: 回答に「保留」が含まれる' },
  { id: 'always', label: '常に', hint: '条件なしで進みます', placeholder: '' },
  { id: 'rule', label: '詳細条件', hint: '読み込んだ詳細条件を保持します', placeholder: '詳細条件' },
];

function whenOf(o) {
  return WHENS.find((w) => w.id === (o.when || 'label')) || WHENS[0];
}

// 行に入れた言葉。「いつでも」には言葉が要らないが、決め方を戻したときに書き直させない
// ように、前に入れていたものを覚えておく（保存時には main が落とす）。
function outcomeValue(o) {
  if (o.when === 'text') return o.text || '';
  if (o.when === 'rule') return o.rule || '';
  if (o.when === 'always') return o.text || o.rule || o.label || '';
  return o.label || '';
}

function whenChip(o) {
  if (o.when === 'text') { const t = String(o.text || ''); return { label: `もし「${t.length > 18 ? `${t.slice(0, 18)}…` : t}」` }; }
  if (o.when === 'always') return { label: '常に' };
  if (o.when === 'rule') return { label: '詳細条件' };
  return { label: o.label || '（指定の言葉）' };
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
  cancelAi(state.aiDraft);
  cancelAi(state.aiReview);
  state.root = root;
  await guard('フォルダ', () => api.selectRoot(root));
  await Promise.all([loadMachines(), loadAgents()]);
  render();
}

async function addFolder() {
  cancelAi(state.aiDraft);
  cancelAi(state.aiReview);
  const cfg = await guard('フォルダの登録', () => api.addRoot());
  if (!cfg) return;
  state.config = cfg;
  state.root = cfg.lastRoot;
  await Promise.all([loadMachines(), loadAgents()]);
  render();
}

async function removeFolder(root) {
  if (!confirm(`${folderName(root)} を一覧から外しますか？（フォルダの中身は消えません）`)) return;
  cancelAi(state.aiDraft);
  cancelAi(state.aiReview);
  const cfg = await guard('フォルダ', () => api.removeRoot(root));
  if (!cfg) return;
  state.config = cfg;
  if (state.root === root) state.root = cfg.lastRoot;
  await Promise.all([loadMachines(), loadAgents()]);
  render();
}

function goHome() {
  if (state.current && state.current.dirty && state.view === 'editor' && !confirm('保存していない変更があります。一覧へ戻りますか？')) return;
  cancelAi(state.aiReview);
  state.view = 'home';
  state.current = null;
  render();
}

async function openMachine(machine) {
  cancelAi(state.aiDraft);
  cancelAi(state.aiReview);
  const res = await guard('読み込み', () => api.readMachine(state.root, machine));
  if (!res) return;
  const raw = res.raw;
  raw.steps = raw.steps.map((s) => ({ ...emptyStep(s.kind), ...s, outcomes: s.outcomes || [], recorded: s.recorded || [] }));
  state.current = { machine, isNew: false, spec: raw, dirty: false, warnings: res.warnings || [], dir: res.dir };
  state.view = 'editor';
  state.open = null;
  state.preview = null;
  resetAi(state.aiReview);
  state.run.lines = [];
  render();
}

function newMachine() {
  if (!state.root) { toast('先にフォルダを登録してください', true); return; }
  cancelAi(state.aiDraft);
  cancelAi(state.aiReview);
  state.current = { machine: '', isNew: true, spec: newSpec(), dirty: true, warnings: [], dir: '' };
  state.view = 'editor';
  state.open = null;
  state.preview = null;
  resetAi(state.aiReview);
  render();
  const t = document.querySelector('.title-input');
  if (t) t.focus();
}

function markDirty() {
  if (!state.current) return;
  state.current.dirty = true;
  state.preview = null;
  if (!state.aiReview.busy) resetAi(state.aiReview, true);
  const el = $('dirty-mark');
  if (el) el.hidden = false;
}

// --- 描画 ---------------------------------------------------------------------------

function render() {
  renderBar();
  const main = $('main');
  const editing = state.view === 'editor' && state.current;
  document.body.classList.toggle('is-editing', !!editing);
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
    right.innerHTML = '<button type="button" id="b-settings" class="ghost">実行環境</button>';
    $('b-settings').addEventListener('click', openSettings);
    return;
  }
  const spec = state.current.spec;
  center.innerHTML = `<input class="title-input" id="m-name" value="${esc(spec.name)}" placeholder="名前を付ける（例: 月次の勤怠集計）" aria-label="名前">`;
  right.innerHTML = `<span id="dirty-mark" class="dirty" ${state.current.dirty ? '' : 'hidden'}>● 未保存</span>
    <button type="button" id="b-ai" class="ghost">AIで見直す</button>
    <button type="button" id="b-run" class="ghost" ${state.current.isNew ? 'disabled title="保存すると実行できます"' : ''}>テスト・実行</button>
    <details class="more-menu"><summary>その他</summary><div class="menu-panel">
      <button type="button" id="b-record" class="ghost">操作を記録</button>
      <button type="button" id="b-files" class="ghost">生成ファイル</button>
      <button type="button" id="b-settings" class="ghost">実行環境</button>
    </div></details>
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
  $('b-ai').addEventListener('click', openAiReview);
  $('b-run').addEventListener('click', openRun);
  $('b-settings').addEventListener('click', openSettings);
  $('b-save').addEventListener('click', saveMachine);
}

// --- 一覧（左: フォルダ／右: ワークフロー） -----------------------------------------------

function homeHtml() {
  const roots = state.config.roots || [];
  if (!roots.length) {
    return `<div class="blank">
      <h2>フォルダを登録します</h2>
      <p>登録したフォルダのワークフローを表示します。</p>
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
        <div class="row"><button type="button" class="primary" id="h-ai-draft">AIで下書き</button><button type="button" id="h-new">手動で作成</button></div>
      </div>
      <div class="matrix">${cards}</div>`
    : '<div class="blank"><h2>左のフォルダを選んでください</h2></div>';
  // 登録したフォルダを左、ワークフローを右に置く。読む順（切り替え → 内容）に合わせて
  // DOM もこの順にする（タブ移動と読み上げが見た目とずれない）。
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
  on('h-ai-draft', openAiDraft);
  on('h-new', newMachine);
  for (const b of main.querySelectorAll('[data-root]')) b.addEventListener('click', () => selectRoot(b.dataset.root));
  for (const b of main.querySelectorAll('[data-drop]')) b.addEventListener('click', () => removeFolder(b.dataset.drop));
  for (const b of main.querySelectorAll('[data-open]')) b.addEventListener('click', () => openMachine(b.dataset.open));
}

// --- 編集 -----------------------------------------------------------------------------

function editorHtml() {
  const spec = state.current.spec;
  const parts = [];
  parts.push(`<button type="button" class="workflow-card ${state.open === 'workflow' ? 'is-selected' : ''}" data-workflow-settings>
    <span class="workflow-card-title">ワークフロー設定</span>
    <span class="workflow-card-purpose">${esc(spec.purpose || '目的と終了条件を設定')}</span>
  </button>`);
  if (!spec.steps.length) {
    parts.push(`<div class="empty-steps"><p>最初の工程を選びます。</p>${pickerHtml(0)}</div>`);
  } else {
    spec.steps.forEach((_s, i) => { parts.push(stepHtml(spec, i)); parts.push(edgeHtml(spec, i)); });
  }
  parts.push('<div class="terminal"><span class="icon">✓</span><span class="name">完了</span></div>');
  for (const e of spec.ends || []) {
    parts.push(`<div class="terminal"><span class="icon">✓</span><span class="name">${esc(e.description)}</span></div>`);
  }
  parts.push('<div class="terminal abort"><span class="icon">✕</span><span class="name">中止</span></div>');
  parts.push(`<div class="notes" id="notes">${notesHtml()}</div>`);
  const mode = state.open == null ? 'no-selection' : 'is-inspecting';
  return `<div class="editor-shell ${mode}">
    <section class="flow-pane"><div class="flow-content">${parts.join('')}</div></section>
    <aside class="inspector" aria-label="編集パネル">${inspectorHtml(spec)}</aside>
  </div>`;
}

function inspectorHtml(spec) {
  const back = '<button type="button" class="ghost inspector-back" data-inspector-back>‹ 流れに戻る</button>';
  if (state.open === 'workflow') {
    return `${back}<div class="inspector-head"><div><span class="eyebrow">ワークフロー</span><h2>基本設定</h2></div></div>
      <div class="inspector-body workflow-body">
        <div class="field"><label for="m-purpose">目的</label><textarea id="m-purpose" rows="3" placeholder="このワークフローで行うこと">${esc(spec.purpose)}</textarea></div>
        <div class="field"><label for="m-save-name">保存名</label><input id="m-save-name" class="mono" value="${esc(spec.machine)}" placeholder="英数字とハイフン" ${state.current.isNew ? '' : 'readonly title="作成後は変更できません"'}></div>
        <div class="field"><label for="m-finish">終了条件</label><textarea id="m-finish" rows="3" placeholder="どの状態になったら完了か">${esc(spec.finish)}</textarea></div>
        <div class="field"><label for="m-notes">注意事項</label><textarea id="m-notes" rows="3" placeholder="例: 承認操作は行わない">${esc(spec.notes)}</textarea></div>
        <details class="more"><summary>詳細設定</summary><div class="field details-body"><label for="m-max">最大工程数</label><input id="m-max" type="number" min="1" max="500" value="${esc(spec.maxSteps)}"></div></details>
      </div>`;
  }
  if (Number.isInteger(state.open) && spec.steps[state.open]) {
    const step = spec.steps[state.open];
    return `${back}<div class="inspector-head"><div><span class="eyebrow">工程 ${state.open + 1}</span><h2>${esc(step.title || kindOf(step.kind).label)}</h2></div></div>
      <div class="inspector-body">${stepBodyHtml(spec, state.open)}</div>`;
  }
  return '<div class="inspector-empty"><strong>工程を選択</strong><span>左のカードを選ぶと、ここで内容を編集できます。</span></div>';
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
  const selected = state.open === index;
  const sub = [kind.label];
  if (step.target && step.kind !== 'command') sub.push(`<span class="mono">${esc(step.target)}</span>`);
  if (step.check) sub.push('<span class="chk">✓ 確認あり</span>');
  if (step.recorded && step.recorded.length) sub.push(`<span class="rec">● 記録 ${step.recorded.length} 件</span>`);
  return `<div class="step" data-step="${index}"><div class="step-card ${selected ? 'is-selected' : ''}">
    <div class="step-head" role="button" tabindex="0" aria-pressed="${selected}">
      <span class="step-icon k-${esc(step.kind)}">${index + 1}</span>
      <span class="step-summary">
        <div class="sentence">${s.empty ? `<span class="v">${esc(s.text)}（内容を入れます）</span>` : `${s.v ? `<span class="v">${esc(s.v)}</span> ` : ''}${esc(s.text)}`}</div>
        <div class="sub">${sub.join('<span>·</span>')}</div>
      </span>
      <span class="step-right"><span class="chev">›</span></span>
    </div>
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
  const check = kind.check ? `<div class="field"><label>完了確認（任意）</label>
    <input data-field="check" class="mono" value="${esc(step.check)}" placeholder="${esc(kind.check.placeholder || '')}">
    <small>成功した場合だけ次へ進みます。</small></div>` : '';
  const dest = (to) => {
    const opts = [['next', index + 1 < spec.steps.length ? `次へ（${index + 2}）` : '次へ（完了）'], ['done', '完了'], ['abort', '中止']];
    for (const e of spec.ends || []) opts.push([`end:${e.id}`, e.description]);
    spec.steps.forEach((_s, i) => { opts.push([`step:${i + 1}`, `${i + 1} へ${i < index ? '戻る' : i === index ? '（やり直す）' : ''}`]); });
    return opts.map(([v, l]) => `<option value="${v}" ${v === to ? 'selected' : ''}>${esc(l)}</option>`).join('');
  };
  const branchRow = (o, i) => {
    const w = whenOf(o);
    const value = o.when === 'always'
      ? '<input disabled placeholder="（条件なし）">'
      : `<input data-bfield="value" value="${esc(outcomeValue(o))}" placeholder="${esc(w.placeholder)}" ${o.when === 'rule' ? 'class="mono"' : ''}>`;
    return `<div class="branch-row" data-branch="${i}">
      <span class="branch-if">もし</span>
      <select class="branch-when" data-bfield="when" title="${esc(w.hint)}">${WHENS.map((x) => `<option value="${x.id}" ${x.id === w.id ? 'selected' : ''}>${esc(x.label)}</option>`).join('')}</select>
      <span class="branch-value">${value}</span>
      <span class="branch-then">なら</span>
      <select class="branch-to" data-bfield="to">${dest(o.to)}</select>
      <button type="button" data-bremove title="削除">✕</button>
    </div>`;
  };
  const branches = step.rawTransitions
    ? `<div class="section-title">次の工程</div>
      <small class="muted">この画面で編集できない条件を保持しています。</small>
      <div><button type="button" class="tiny" data-unraw>標準の条件に置き換える</button></div>`
    : `<div class="section-title">次の工程</div>
      ${step.outcomes.map(branchRow).join('')}
      <div><button type="button" class="tiny" data-badd>＋ 条件を追加</button></div>`;
  return `<div class="step-body">
    <div class="field"><label>実行方法</label><div class="seg">${seg}</div></div>
    <div class="field"><label>工程名</label><input data-field="title" value="${esc(step.title)}" placeholder="例: 申請一覧を開く"></div>
    ${target}
    <div class="field"><label>${esc(kind.detail.label)}${kind.detail.required ? '' : '（任意）'}</label><textarea data-field="detail" rows="5" placeholder="${esc(kind.detail.placeholder || '')}">${esc(step.detail)}</textarea>
      <small>毎回変わる値は <code>{{month}}</code> のように入力します。</small></div>
    ${recorded}
    ${check}
    ${branches}
    <details class="more"><summary>詳細設定</summary><div class="grid2" style="margin-top:8px">
      <div class="field"><label>工程ID</label><input data-field="id" class="mono" value="${esc(step.id)}" placeholder="step_${index + 1}"></div>
      ${kind.check ? `<div class="field"><label>再試行回数</label><input data-field="checkRetries" type="number" min="0" max="5" value="${esc(step.checkRetries)}"></div>` : ''}
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
  const workflowSettings = main.querySelector('[data-workflow-settings]');
  if (workflowSettings) workflowSettings.addEventListener('click', () => { state.open = 'workflow'; state.pickerAt = -1; render(); });
  const inspectorBack = main.querySelector('[data-inspector-back]');
  if (inspectorBack) inspectorBack.addEventListener('click', () => { state.open = null; render(); });
  for (const b of main.querySelectorAll('[data-add]')) b.addEventListener('click', () => insertStep(Number(b.closest('[data-at]').dataset.at), b.dataset.add));
  for (const b of main.querySelectorAll('[data-insert]')) b.addEventListener('click', () => { const at = Number(b.dataset.insert); state.pickerAt = state.pickerAt === at ? -1 : at; render(); });
  for (const card of main.querySelectorAll('[data-step]')) bindStep(card);
  const stepBody = main.querySelector('.inspector .step-body');
  if (stepBody && Number.isInteger(state.open)) bindStepBody(stepBody, state.open);
}

function bindStep(card) {
  const index = Number(card.dataset.step);
  const head = card.querySelector('.step-head');
  const select = () => { state.open = index; state.pickerAt = -1; render(); scrollToStep(index); };
  head.addEventListener('click', select);
  head.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(); } });
}

function bindStepBody(body, index) {
  const spec = state.current.spec;
  const step = spec.steps[index];
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
    const outcome = step.outcomes[i];
    for (const el of row.querySelectorAll('[data-bfield]')) {
      el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', () => {
        const field = el.dataset.bfield;
        if (field === 'to') { outcome.to = el.value; markDirty(); refreshEdge(index); return; }
        if (field === 'value') { setOutcomeValue(outcome, el.value); markDirty(); refreshEdge(index); return; }
        // 決め方を変えたら、入れていた言葉は持ち越す（入れ直させない）
        const carried = outcomeValue(outcome);
        outcome.when = el.value;
        setOutcomeValue(outcome, carried);
        markDirty();
        render();
      });
    }
    row.querySelector('[data-bremove]').addEventListener('click', () => { step.outcomes.splice(i, 1); markDirty(); render(); });
  }
  const on = (sel, fn) => { const el = body.querySelector(sel); if (el) el.addEventListener('click', fn); };
  on('[data-badd]', () => {
    step.outcomes.push({ when: 'label', label: '', to: 'next' });
    markDirty();
    render();
    const inputs = document.querySelectorAll('.inspector [data-branch] input');
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
    state.open = null;
    markDirty();
    render();
  });
}

function setOutcomeValue(o, value) {
  if (o.when === 'text') o.text = value;
  else if (o.when === 'rule') o.rule = value;
  else if (o.when !== 'always') o.label = value;
}

function refreshHead(index) {
  const card = document.querySelector(`[data-step="${index}"]`);
  if (!card) return;
  const tmp = document.createElement('div');
  tmp.innerHTML = stepHtml(state.current.spec, index);
  const fresh = tmp.querySelector('.step-head');
  card.querySelector('.step-head').replaceWith(fresh);
  const select = () => { state.open = index; state.pickerAt = -1; render(); };
  fresh.addEventListener('click', select);
  fresh.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(); } });
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
  const first = document.querySelector('.inspector [data-field="title"]');
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

function dialog(id, title, size, bodyHtml) {
  const dlg = $(id);
  dlg.className = `dlg-${size}`;
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
  const dlg = dialog('dlg-record', '操作を記録', 'record', `
    <p class="small muted" style="margin:0">入力値は変数として取り込み、パスワードは保存しません。</p>
    <div class="grid2">
      <div class="field"><label>記録するもの</label><select id="r-source" ${rec.active ? 'disabled' : ''}>${kinds.map((k) => `<option value="${esc(k.id)}" ${rec.source === k.id ? 'selected' : ''}>${esc(k.label)}</option>`).join('')}</select></div>
      <div class="field"><label>${esc(target.label)}</label><input id="r-target" class="mono" data-rec="${target.field}" value="${esc(target.value)}" placeholder="${esc(target.placeholder)}" ${rec.active ? 'disabled' : ''}></div>
    </div>
    <div class="row"><button type="button" id="r-start" ${rec.active || rec.busy || (windows && !onWindows) ? 'disabled' : ''}>記録を始める</button>
      <button type="button" id="r-stop" class="primary" ${!rec.active || rec.busy ? 'disabled' : ''}>終了して工程を作成</button>
      <span class="small muted">${windows ? (onWindows ? '操作したあとに終えてください。' : 'Windows のアプリは Windows でだけ記録できます。') : '見える形でブラウザが開きます。'}</span></div>
    <p id="r-message" class="msg ${rec.ok ? '' : 'err'}" ${rec.message ? '' : 'hidden'}>${esc(rec.message)}</p>
    <details ${rec.text ? 'open' : ''}><summary>記録を貼り付ける</summary>
      <div class="field" style="margin-top:8px">
        <textarea id="r-text" class="mono" rows="6" placeholder="記録した内容を貼り付けます">${esc(rec.text)}</textarea>
        <div><button type="button" id="r-import" ${rec.busy ? 'disabled' : ''}>工程を作成</button></div>
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
    rec.message = '操作後に「終了して工程を作成」を押してください。';
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
  state.open = steps.length ? spec.steps.length - steps.length : null;
  render();
  if (state.open != null) scrollToStep(state.open);
  toast(`${steps.length} 工程を作りました`);
}

async function openFiles() {
  const dlg = dialog('dlg-files', '生成ファイル', 'files', '<p class="muted small">生成中…</p>');
  const res = state.preview || await previewMachine();
  if (!res) { dlg.querySelector('.dlg-body').innerHTML = '<p class="msg err">組み立てられませんでした</p>'; return; }
  const files = res.files || {};
  const names = Object.keys(files);
  if (!names.includes(state.fileTab)) state.fileTab = names[0] || '';
  const paint = () => {
    dlg.querySelector('.dlg-body').innerHTML = `${state.current.isNew ? '' : '<div class="row"><button type="button" class="tiny" id="f-open">フォルダを開く</button></div>'}
      ${res.errors && res.errors.length ? `<div class="msg err">${res.errors.map(esc).join('\n')}</div>` : ''}
      <div class="file-tabs">${names.map((n) => `<button type="button" data-file="${esc(n)}" class="${n === state.fileTab ? 'is-on' : ''}">${esc(n)}</button>`).join('')}</div>
      <pre>${esc(files[state.fileTab] || '')}</pre>`;
    for (const b of dlg.querySelectorAll('[data-file]')) b.addEventListener('click', () => { state.fileTab = b.dataset.file; paint(); });
    const open = dlg.querySelector('#f-open');
    if (open) open.addEventListener('click', () => guard('フォルダ', () => api.openMachineFolder(state.root, state.current.machine)));
  };
  paint();
}

function aiAgentHtml() {
  const agent = selectedAgent(state.config.agent);
  return `<p class="ai-agent">使うAI: <strong>${esc(agent || '未設定')}</strong></p>`;
}

function aiBusyHtml(flow) {
  return `<div class="ai-busy" role="status"><span class="spinner" aria-hidden="true"></span><div><strong>${esc(flow.message || 'AIが検討しています…')}</strong><span>この画面を閉じても処理は続きます。</span></div></div>
    <div class="row"><button type="button" class="danger" data-ai-stop>中止</button></div>`;
}

function aiQuestionsHtml(flow) {
  return `<p class="muted small">判断に必要な点だけ確認します。回答すると、内容を含めてもう一度見直します。</p>
    <div class="ai-question-list">${flow.questions.map((question) => `<div class="ai-question">
      <label for="answer-${esc(question.id)}">${esc(question.text)}</label>
      ${question.reason ? `<p>${esc(question.reason)}</p>` : ''}
      <textarea id="answer-${esc(question.id)}" data-ai-answer="${esc(question.id)}" rows="2" placeholder="${esc(question.example || '回答を入力')}">${esc(flow.answers[question.id] || '')}</textarea>
    </div>`).join('')}</div>
    <div class="row"><button type="button" class="primary" data-ai-answer-send>回答して続ける</button><button type="button" data-ai-back>最初からやり直す</button></div>`;
}

function assumptionsHtml(items) {
  return items && items.length
    ? `<div class="ai-assumptions"><strong>前提</strong><ul>${items.map((item) => `<li>${esc(item)}</li>`).join('')}</ul></div>`
    : '';
}

function openAiDraft() {
  if (!state.root) { toast('先にフォルダを登録してください', true); return; }
  const flow = state.aiDraft;
  let body;
  if (flow.busy) {
    body = `${aiAgentHtml()}${aiBusyHtml(flow)}`;
  } else if (flow.phase === 'questions') {
    body = `${aiAgentHtml()}${flow.result && flow.result.summary ? `<p>${esc(flow.result.summary)}</p>` : ''}${aiQuestionsHtml(flow)}`;
  } else if (flow.phase === 'result' && flow.result && flow.result.candidate) {
    const candidate = flow.result.candidate;
    body = `${aiAgentHtml()}<div class="ai-summary"><span class="ai-kicker">下書きができました</span><h3>${esc(candidate.name)}</h3><p>${esc(flow.result.summary || candidate.purpose || '')}</p></div>
      <ol class="ai-step-list">${candidate.steps.map((step) => `<li><span>${esc(step.title || kindOf(step.kind).label)}</span><small>${esc(kindOf(step.kind).short)}</small></li>`).join('')}</ol>
      ${assumptionsHtml(flow.result.assumptions)}
      ${(flow.result.warnings || []).length ? `<p class="msg" style="color:var(--warn)">${flow.result.warnings.map(esc).join('\n')}</p>` : ''}
      <div class="row"><button type="button" class="primary" data-ai-open-draft>編集画面で確認</button><button type="button" data-ai-back>作り直す</button></div>`;
  } else {
    body = `${aiAgentHtml()}${flow.error ? `<p class="msg err">${esc(flow.error)}</p>` : ''}
      <div class="field"><label for="ai-draft-request">作りたいワークフロー</label><textarea id="ai-draft-request" rows="6" placeholder="例: 毎朝、申請一覧を確認し、不備がある申請をまとめて担当者へ知らせたい">${esc(flow.request)}</textarea><small>目的と大まかな流れだけで始められます。</small></div>
      <div class="row"><button type="button" class="primary" data-ai-start-draft ${state.agents.length ? '' : 'disabled'}>下書きを作る</button></div>`;
  }
  const dlg = dialog('dlg-ai-draft', 'AIで下書き', 'work', `<div class="ai-flow">${body}</div>`);
  bindAiCommon(dlg, flow, openAiDraft);
  const request = dlg.querySelector('#ai-draft-request');
  if (request) request.addEventListener('input', (event) => { flow.request = event.target.value; });
  const start = dlg.querySelector('[data-ai-start-draft]');
  if (start) start.addEventListener('click', () => startAi(flow));
  const open = dlg.querySelector('[data-ai-open-draft]');
  if (open) open.addEventListener('click', importAiDraft);
}

function reviewScopeValue(scope) {
  return scope && scope.type === 'step' ? `step:${scope.stepId}` : 'workflow';
}

function openAiReview() {
  const flow = state.aiReview;
  assignIds(state.current.spec);
  if (!flow.scope) {
    const selected = Number.isInteger(state.open) && state.current.spec.steps[state.open];
    flow.scope = selected ? { type: 'step', stepId: selected.id } : { type: 'workflow' };
  }
  let body;
  if (flow.busy) {
    body = `${aiAgentHtml()}${aiBusyHtml(flow)}`;
  } else if (flow.phase === 'questions') {
    body = `${aiAgentHtml()}${flow.result && flow.result.summary ? `<p>${esc(flow.result.summary)}</p>` : ''}${aiQuestionsHtml(flow)}`;
  } else if (flow.phase === 'result' && flow.result) {
    body = reviewResultHtml(flow.result);
  } else {
    const options = state.current.spec.steps.map((step, index) => `<option value="step:${esc(step.id)}" ${reviewScopeValue(flow.scope) === `step:${step.id}` ? 'selected' : ''}>工程 ${index + 1}: ${esc(step.title || kindOf(step.kind).label)}</option>`).join('');
    body = `${aiAgentHtml()}${flow.error ? `<p class="msg err">${esc(flow.error)}</p>` : ''}
      <div class="grid2"><div class="field"><label for="ai-review-scope">見直す範囲</label><select id="ai-review-scope"><option value="workflow" ${reviewScopeValue(flow.scope) === 'workflow' ? 'selected' : ''}>ワークフロー全体</option>${options}</select></div>
      <div class="field"><label for="ai-review-focus">特に見てほしい点（任意）</label><input id="ai-review-focus" value="${esc(flow.focus)}" placeholder="例: 再試行が多すぎないか"></div></div>
      <div class="ai-checks"><span>整合性</span><span>効率性</span><span>エラー処理</span><span>エッジケース</span></div>
      <div class="row"><button type="button" class="primary" data-ai-start-review ${state.agents.length ? '' : 'disabled'}>見直す</button></div>`;
  }
  const dlg = dialog('dlg-ai', 'AIで見直す', 'work', `<div class="ai-flow">${body}</div>`);
  bindAiCommon(dlg, flow, openAiReview);
  const scope = dlg.querySelector('#ai-review-scope');
  if (scope) scope.addEventListener('change', (event) => {
    flow.scope = event.target.value === 'workflow' ? { type: 'workflow' } : { type: 'step', stepId: event.target.value.slice(5) };
  });
  const focus = dlg.querySelector('#ai-review-focus');
  if (focus) focus.addEventListener('input', (event) => { flow.focus = event.target.value; });
  const start = dlg.querySelector('[data-ai-start-review]');
  if (start) start.addEventListener('click', () => startAi(flow));
  const all = dlg.querySelector('[data-ai-all]');
  if (all) all.addEventListener('change', () => {
    for (const item of dlg.querySelectorAll('[data-ai-change]')) item.checked = all.checked;
  });
  const apply = dlg.querySelector('[data-ai-apply]');
  if (apply) apply.addEventListener('click', () => applyAiReview(dlg));
}

function reviewResultHtml(result) {
  const findings = (result.findings || []).map((item) => {
    const severity = { error: '要対応', warning: '確認', suggestion: '提案' }[item.severity] || '提案';
    return `<li class="${esc(item.severity)}"><span>${esc(severity)}</span><div><strong>${esc(item.title)}</strong>${item.detail ? `<p>${esc(item.detail)}</p>` : ''}</div></li>`;
  }).join('');
  const changes = (result.changes || []).map((item) => `<label class="ai-change">
    <input type="checkbox" data-ai-change value="${esc(item.id)}" checked>
    <span><strong>${esc(item.title)}</strong><details><summary>変更内容</summary><div class="ai-compare"><pre>${esc(JSON.stringify(item.before, null, 2))}</pre><span aria-hidden="true">→</span><pre>${esc(JSON.stringify(item.after, null, 2))}</pre></div></details></span>
  </label>`).join('');
  return `${aiAgentHtml()}<div class="ai-summary"><span class="ai-kicker">見直し結果</span><p>${esc(result.summary || '確認が終わりました。')}</p></div>
    ${findings ? `<ul class="ai-findings">${findings}</ul>` : ''}
    ${assumptionsHtml(result.assumptions)}
    ${changes ? `<div class="ai-select-head"><strong>反映する提案</strong><label><input type="checkbox" data-ai-all checked> すべて選択</label></div><div class="ai-change-list">${changes}</div>
      <div class="row"><button type="button" class="primary" data-ai-apply>選んだ提案を反映</button><button type="button" data-ai-back>見直し直す</button></div>`
    : '<p class="msg ai-no-change">変更の提案はありません。現在の内容で問題ありません。</p><div class="row"><button type="button" data-ai-back>もう一度見直す</button></div>'}`;
}

function bindAiCommon(dlg, flow, repaint) {
  const stop = dlg.querySelector('[data-ai-stop]');
  if (stop) stop.addEventListener('click', async () => {
    stop.disabled = true;
    await guard('中止', () => api.aiStop(flow.requestId === 'pending' ? '' : flow.requestId));
  });
  for (const answer of dlg.querySelectorAll('[data-ai-answer]')) {
    answer.addEventListener('input', (event) => { flow.answers[event.target.dataset.aiAnswer] = event.target.value; });
  }
  const send = dlg.querySelector('[data-ai-answer-send]');
  if (send) send.addEventListener('click', () => {
    const missing = flow.questions.find((question) => !String(flow.answers[question.id] || '').trim());
    if (missing) { toast('すべての質問に回答してください', true); return; }
    flow.history.push(...flow.questions.map((question) => ({ question: question.text, answer: flow.answers[question.id].trim() })));
    flow.questions = [];
    flow.answers = {};
    startAi(flow);
  });
  const back = dlg.querySelector('[data-ai-back]');
  if (back) back.addEventListener('click', () => { resetAi(flow, true); repaint(); });
}

async function startAi(flow) {
  if (flow.busy) return;
  if (flow.mode === 'draft' && !String(flow.request || '').trim()) { toast('作りたいワークフローを入力してください', true); return; }
  flow.busy = true;
  flow.phase = 'processing';
  flow.error = '';
  flow.result = null;
  flow.requestId = 'pending';
  flow.message = 'AIが検討しています…';
  const repaint = flow.mode === 'draft' ? openAiDraft : openAiReview;
  repaint();
  const payload = {
    root: state.root, mode: flow.mode, agent: selectedAgent(state.config.agent), history: flow.history,
    ...(flow.mode === 'draft'
      ? { request: flow.request }
      : { spec: specPayload(), scope: flow.scope, focus: flow.focus }),
  };
  try {
    const started = await api.aiStart(payload);
    if (flow.busy && flow.requestId === 'pending') flow.requestId = started.requestId;
  } catch (err) {
    flow.busy = false;
    flow.phase = 'input';
    flow.requestId = '';
    flow.error = String((err && err.message) || err);
    repaint();
  }
}

function receiveAiProgress(payload) {
  const flow = payload.mode === 'draft' ? state.aiDraft : payload.mode === 'review' ? state.aiReview
    : (state.aiDraft.requestId === payload.requestId ? state.aiDraft : state.aiReview);
  if (!flow.busy || (flow.requestId !== 'pending' && flow.requestId !== payload.requestId)) return;
  flow.requestId = payload.requestId;
  flow.message = payload.message || flow.message;
  const dlg = $(flow.mode === 'draft' ? 'dlg-ai-draft' : 'dlg-ai');
  if (dlg.open) (flow.mode === 'draft' ? openAiDraft : openAiReview)();
}

function receiveAiResult(payload) {
  const flow = payload.mode === 'draft' ? state.aiDraft : state.aiReview;
  if (!flow.busy || (flow.requestId !== 'pending' && flow.requestId !== payload.requestId)) return;
  flow.requestId = payload.requestId;
  flow.busy = false;
  if (payload.cancelled) {
    flow.phase = 'input';
    flow.error = '';
  } else if (!payload.ok) {
    flow.phase = 'input';
    flow.error = payload.error || 'AIの処理に失敗しました';
  } else {
    flow.result = payload.result;
    flow.questions = payload.result.questions || [];
    flow.phase = payload.result.status === 'questions' ? 'questions' : 'result';
  }
  const dlg = $(flow.mode === 'draft' ? 'dlg-ai-draft' : 'dlg-ai');
  if (dlg.open) (flow.mode === 'draft' ? openAiDraft : openAiReview)();
}

function importAiDraft() {
  const result = state.aiDraft.result;
  if (!result || !result.candidate) return;
  const spec = JSON.parse(JSON.stringify(result.candidate));
  state.current = { machine: spec.machine || '', isNew: true, spec, dirty: true, warnings: result.warnings || [], dir: '' };
  state.view = 'editor';
  state.open = null;
  state.preview = null;
  $('dlg-ai-draft').close();
  resetAi(state.aiDraft);
  resetAi(state.aiReview);
  render();
}

async function applyAiReview(dlg) {
  const flow = state.aiReview;
  const ids = [...dlg.querySelectorAll('[data-ai-change]:checked')].map((input) => input.value);
  if (!ids.length) { toast('反映する提案を選んでください', true); return; }
  const button = dlg.querySelector('[data-ai-apply]');
  button.disabled = true;
  button.textContent = '確認中…';
  const res = await guard('提案の反映', () => api.aiApply({
    base: specPayload(), candidate: flow.result.candidate, ids, baseFingerprint: flow.result.baseFingerprint,
  }));
  if (!res) { button.disabled = false; button.textContent = '選んだ提案を反映'; return; }
  state.current.spec = res.spec;
  state.current.dirty = true;
  state.current.warnings = res.warnings || [];
  state.preview = null;
  resetAi(flow);
  dlg.close();
  render();
  toast(`${ids.length} 件の提案を反映しました（未保存）`);
}

function openRun() {
  const run = state.run;
  const cur = state.current;
  if (cur.isNew) return;
  const agent = selectedAgent(run.agent || state.config.agent);
  run.agent = agent;
  const dlg = dialog('dlg-run', 'テスト・実行', 'work', `
    ${cur.dirty ? '<p class="msg" style="color:var(--warn)">保存していない変更があります。動くのは保存した内容です。</p>' : ''}
    <div class="row"><button type="button" id="run-check" ${run.running ? 'disabled' : ''}>構成を確認</button><button type="button" id="run-go" class="primary" ${run.running ? 'disabled' : ''}>実行</button><button type="button" id="run-stop" class="danger" ${run.running ? '' : 'disabled'}>停止</button></div>
    <div class="grid2">
      <div class="field"><label>使う AI</label><select id="run-agent" ${state.agents.length ? '' : 'disabled'}>${agentOptions(agent)}</select></div>
      <div class="field"><label>最初に渡す文（任意）</label><input id="run-input" value="${esc(run.input)}"></div>
    </div>
    <div class="log" id="run-log">${run.lines.map((l) => `<div class="${l.kind === 'stderr' ? 'e' : ''}">${esc(l.line)}</div>`).join('') || '<span class="muted">ここに様子が出ます</span>'}</div>`);
  dlg.querySelector('#run-agent').addEventListener('change', (e) => { run.agent = e.target.value; });
  dlg.querySelector('#run-input').addEventListener('input', (e) => { run.input = e.target.value; });
  dlg.querySelector('#run-check').addEventListener('click', () => startRun('check'));
  const go = dlg.querySelector('#run-go');
  if (!state.agents.length) go.disabled = true;
  go.addEventListener('click', () => startRun('run'));
  dlg.querySelector('#run-stop').addEventListener('click', () => api.runStop());
}

async function startRun(mode) {
  const run = state.run;
  if (mode === 'run' && !run.agent) { toast('実行環境で使う AI を確認してください', true); return; }
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

function openSettings() {
  const cfg = state.config;
  const agent = selectedAgent(cfg.agent);
  const dlg = dialog('dlg-settings', '実行環境', 'settings', `
    <div class="grid2">
      <div class="field"><label>使う AI（agent-tools）</label><select id="c-agent" ${state.agents.length ? '' : 'disabled'}>${agentOptions(agent)}</select></div>
      <div class="field"><label>モデル（任意）</label><input id="c-model" class="mono" value="${esc(cfg.model || '')}"></div>
    </div>
    <div class="field"><label>構成確認用スキルの場所（任意）</label><input id="c-skill" class="mono" value="${esc(cfg.skillDir || '')}" placeholder="通常は自動で検出します"></div>
    <div class="row"><button type="button" id="c-save" class="primary">保存</button><button type="button" id="tools-check">接続を確認</button></div>
    <div id="tools-list">${state.tools ? toolsHtml(state.tools) : ''}</div>`);
  dlg.querySelector('#c-save').addEventListener('click', async () => {
    const next = { ...cfg, agent: dlg.querySelector('#c-agent').value || cfg.agent, model: dlg.querySelector('#c-model').value.trim(), skillDir: dlg.querySelector('#c-skill').value.trim() };
    const saved = await guard('保存', () => api.saveConfig(next));
    if (saved) { state.config = saved; toast('保存しました'); }
  });
  dlg.querySelector('#tools-check').addEventListener('click', async () => {
    const btn = dlg.querySelector('#tools-check');
    btn.disabled = true;
    btn.textContent = '確認中…';
    const res = await guard('確認', () => api.toolStatus(state.root));
    state.tools = res || state.tools;
    const definitions = await guard('AI 一覧', () => api.listAgents(state.root));
    if (definitions) {
      const current = dlg.querySelector('#c-agent').value || cfg.agent;
      state.agents = definitions;
      const select = dlg.querySelector('#c-agent');
      select.innerHTML = agentOptions(current);
      select.disabled = !state.agents.length;
    }
    btn.disabled = false;
    btn.textContent = '接続を確認';
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
  $('btn-home').addEventListener('click', goHome);
  api.onRunLine((p) => appendLog(p));
  api.onAiProgress((p) => receiveAiProgress(p));
  api.onAiResult((p) => receiveAiResult(p));
  api.onRunExit((p) => {
    state.run.running = false;
    appendLog({ kind: p.code === 0 ? 'stdout' : 'stderr', line: p.code === 0 ? (p.mode === 'check' ? '— 点検しました。抜けはありません' : '— 終わりました') : '— 止まりました' });
    const dlg = $('dlg-run');
    if (dlg.open) { dlg.querySelector('#run-check').disabled = false; dlg.querySelector('#run-go').disabled = !state.agents.length; dlg.querySelector('#run-stop').disabled = true; }
  });
  window.addEventListener('beforeunload', (e) => { if (state.current && state.current.dirty) { e.preventDefault(); e.returnValue = ''; } });
  state.root = state.config.lastRoot || (state.config.roots || [])[0] || '';
  await Promise.all([state.root ? loadMachines() : Promise.resolve(), loadAgents()]);
  render();
}

init();
