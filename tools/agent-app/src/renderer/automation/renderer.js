'use strict';

(function initStatemachineWorkbench() {

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
// preload の窓口は agent-app の window.api.automation。タスクを AI と作る会話（tmux）は
// 親の agent-app が持ち、ここは置き場（slot）と見出しを描く（teaching.js）。

const workbenchHost = document.querySelector('[data-statemachine-workbench]');
const workbenchRoot = workbenchHost ? workbenchHost.shadowRoot : document;
const workbenchBody = workbenchHost || document.body;
const $ = (id) => workbenchRoot.getElementById(id);
const embedded = !!workbenchHost;
const automationHost = window.api.automation;
const POPUP_MENU_SELECTOR = 'details.more-menu[open], details.run-settings[open]';

function closePopupMenus(event = null) {
  const path = event && typeof event.composedPath === 'function' ? event.composedPath() : [];
  for (const menu of workbenchRoot.querySelectorAll(POPUP_MENU_SELECTOR)) {
    if (!event || !path.includes(menu)) menu.open = false;
  }
}

workbenchRoot.addEventListener('click', (event) => closePopupMenus(event));
workbenchRoot.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closePopupMenus();
});

const state = {
  config: { roots: [], lastRoot: '' },
  root: '',
  machines: [],
  agents: [],
  editAgent: '',
  agentsLoading: false,   // AI 一覧（agent-herd defs）の返事待ち。空と「まだ聞いていない」を区別する
  catalog: { kinds: [], platform: '' },
  view: 'home',
  homeTab: 'teach',
  execution: { loading: false, snapshot: null, selected: '', detailTab: 'overview', editing: false, scheduleOpen: false, scheduleDraft: null, log: null },
  current: null,     // { machine, isNew, spec, dirty, warnings, dir }
  open: null,        // 選択中の工程番号、'workflow'、または未選択
  pickerAt: -1,      // 追加の種類を選んでいる位置
  preview: null, tools: null,
  aiDraft: { mode: 'draft', phase: 'input', requestId: '', busy: false, request: '', history: [], questions: [], answers: {}, result: null, error: '', message: '' },
  aiReview: { mode: 'review', phase: 'input', requestId: '', busy: false, focus: '', scope: null, history: [], questions: [], answers: {}, result: null, error: '', message: '' },
  recording: { source: 'browser', url: '', app: '', text: '', active: false, busy: false, message: '', ok: true, pick: null, extracts: 0 },
  run: { lines: [], running: false, policy: '', agent: '', model: '', skillMode: '', skills: [], skillPreview: [], parameters: {}, parametersFor: null, requestId: '', result: null, error: '' },
  fileTab: '',
};

// light DOM に端末を置き、一覧の描き直しでも xterm の実体を保持する。
const runTerm = embedded && window.createTerm ? window.createTerm({
  termKeys: (id, data) => automationHost.runKeys(id, data),
  termResize: (id, cols, rows) => automationHost.runResize(id, cols, rows),
  termScroll: (id, lines) => automationHost.runScroll(id, lines),
  termWatch: async () => {}, termUnwatch: async () => {},
}) : null;
const runTermHost = runTerm ? document.createElement('div') : null;
if (runTermHost) {
  runTermHost.slot = 'task-run-terminal';
  runTermHost.style.cssText = 'height:420px;min-height:200px;overflow:hidden';
  workbenchHost.appendChild(runTermHost);
  runTerm.configure({ onError: (error) => toast(error.message, true) });
}

function renderRunTerminal() {
  if (!runTerm) return;
  runTermHost.hidden = !state.run.terminal;
  if (state.run.terminal && runTerm.current() !== state.run.requestId) {
    runTerm.attach(state.run.requestId, runTermHost).then(() => {
      if (state.run.screen) runTerm.applyScreen(state.run.screen);
    });
  }
  runTerm.setInputEnabled(state.run.running && !!state.run.terminal);
  runTerm.refit();
}

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

function notifyHost(area, selected = '') {
  if (!embedded) return;
  const detail = { type: 'agent-app:changed', area, root: state.root, selected };
  workbenchHost.dispatchEvent(new CustomEvent('statemachine:changed', { detail, bubbles: true }));
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
  // 設定・実行方針で選んである名前は、一覧に無くても捨てない（別の AI へ黙って倒さない——
  // 会話と同じ規則）。実行しようとすれば「この環境で使えません」と断られ、原因が名前で分かる。
  // 捨てると「エージェント未設定」に見え、実際に使う名前とも食い違う。
  if (preferred) return preferred;
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
  automationHost.aiStop(requestId).catch(() => {});
  resetAi(flow, true);
}

function agentOptions(preferred = '') {
  const selected = selectedAgent(preferred);
  // 「まだ聞いている途中」と「聞いた結果 0 件」を混同しない（待たせない代わりに、途中だと分かる）
  if (!state.agents.length) return `<option value="">${state.agentsLoading ? '確認中…' : '利用できる AI がありません'}</option>`;
  // 設定・実行方針の名前が一覧に無くても捨てない（selectedAgent と同じ規則）。選択肢には「現在は利用不可」と出す
  const extra = selected && !state.agents.includes(selected) ? `<option value="${esc(selected)}" selected>${esc(selected)}（現在は利用不可）</option>` : '';
  return extra + state.agents.map((name) => `<option value="${esc(name)}" ${name === selected ? 'selected' : ''}>${esc(name)}</option>`).join('');
}

// 描き直してよいか。入力欄に文字を打っている最中に、遅れて届いた返事で画面を組み直すと入力が消える。
function editingInMain() {
  const active = workbenchRoot.activeElement;
  return !!active && ['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName) && !!active.closest('#main');
}

function renderIfIdle() {
  if (state.view === 'home' && !editingInMain()) render();
}

// AI の一覧（会話と同じ定義の一覧。Windows では WSL の PATH を引くのに数秒かかる）。**画面を待たせない**——
// 呼んだ側は await せず、届いたら描き直す。リポジトリを移っていたら捨てる（token）。
// 1 回のタスク表示で init・navigate・リポジトリ切替から重ねて呼ばれるので、同じリポジトリの
// 問い合わせが走っている間は相乗りする（WSL 越しの起動を 1 回で済ませる）。
let agentsToken = 0;
let agentsInFlight = null;
function loadAgents() {
  if (agentsInFlight && agentsInFlight.root === state.root) return agentsInFlight.promise;
  const token = (agentsToken += 1);
  const root = state.root;
  state.agentsLoading = true;
  const promise = automationHost.listAgents(root).then((names) => {
    if (token !== agentsToken) return;
    state.agents = Array.isArray(names) ? names : [];
    state.agentsLoading = false;
    state.run.agent = selectedAgent(state.run.agent || state.config.agent);
    renderIfIdle();
  }, (err) => {
    if (token !== agentsToken) return;
    state.agents = [];
    state.agentsLoading = false;
    toast(`AI 一覧: ${(err && err.message) || err}`, true);
  }).finally(() => {
    if (agentsInFlight && agentsInFlight.token === token) agentsInFlight = null;
  });
  agentsInFlight = { token, root, promise };
  return promise;
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
  return { id: '', kind, title: '', detail: '', target: '', check: '', checkRetries: 1, outcomes: [], recorded: [], extend: {}, rawTransitions: false };
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
  state.machines = state.root ? ((await guard('一覧の取得', () => automationHost.listMachines(state.root))) || []) : [];
  // 実行詳細の状態バッジ（利用可能・変更中）は教示一覧から決めるので、定義一覧と一緒に読み直す。
  if (state.root) await teachingFeature.loadItems();
}

// 実行状態（agent-loop inspect。設定とファイル実体を確かめるぶん遅く、Windows では WSL 越し）。
// 定義（.statemachine/ の一覧）だけで先に画面を描き、届いたら重ねる。await するのは、
// 保存や実行のあとに結果を待って描き直したい場面だけ。初回表示・リポジトリの切替では待たない。
let snapshotToken = 0;
async function loadExecutionSnapshot() {
  if (!state.root) { state.execution.snapshot = null; state.execution.loading = false; return; }
  const token = (snapshotToken += 1);
  const root = state.root;
  state.execution.loading = true;
  const snapshot = await guard('実行情報', () => automationHost.runSnapshot(root));
  if (token !== snapshotToken || root !== state.root) return;
  state.execution.loading = false;
  state.execution.snapshot = snapshot || {
    available: false, machines: [], history: [], daemon: { running: false }, error: '実行情報を取得できませんでした',
  };
  const machines = executionMachines();
  if (!machines.some((machine) => taskIdentity(machine) === state.execution.selected)) {
    state.execution.selected = machines[0] ? taskIdentity(machines[0]) : '';
    state.execution.scheduleDraft = null;
    state.execution.log = null;
  }
}

// 待たずに取りに行き、届いたら描き直す。
function refreshExecutionSnapshot() {
  return loadExecutionSnapshot().then(renderIfIdle);
}

// 前回の手動実行で入れた実行条件（config.json の lastTaskInputs）。値だけを持ち、パスは持たない。
function rememberedInputs(machine) {
  const all = (state.config && state.config.taskInputs) || {};
  const perRepo = all[state.root] && typeof all[state.root] === 'object' ? all[state.root] : {};
  const key = String((machine && machine.machine) || taskIdentity(machine) || '');
  const values = perRepo[key];
  return values && typeof values === 'object' ? values : {};
}

// 選んでいるタスクが変わったら、実行条件を初期値へ戻す。タスクは一覧からも、親（会話画面の
// サイドバー）からも選ばれるので、判定は描くたびに行う（どの経路でも取りこぼさない）。
function ensureRunParameters(machine) {
  // 実行条件の顔ぶれは、実行情報（agent-loop）が届いて初めて分かる。名前まで込みで見分け、
  // 届いた時点で前回の値を入れ直す（空のまま固定しない）。
  const id = `${taskIdentity(machine)}#${((machine && machine.parameters) || []).join(',')}`;
  if (state.run.parametersFor === id) return;
  state.run.parametersFor = id;
  state.run.parameters = initialRunParameters(machine);
}

// 実行条件の初期値。**今回打った値 → 前回の値 → 定期実行の既定値** の順。
function initialRunParameters(machine) {
  if (!machine) return {};
  const defaults = (machine.parameterDefaults && typeof machine.parameterDefaults === 'object') ? machine.parameterDefaults : {};
  const previous = rememberedInputs(machine);
  const values = {};
  for (const name of machine.parameters || []) {
    const value = previous[name] != null ? previous[name] : defaults[name];
    if (value != null && String(value) !== '') values[name] = String(value);
  }
  return values;
}

// 実行したときの値を覚える（次回の既定になる）。空の値は覚えない。
async function rememberRunParameters(machine, parameters) {
  const key = String((machine && machine.machine) || taskIdentity(machine) || '');
  if (!key || !state.root) return;
  const kept = {};
  for (const [name, value] of Object.entries(parameters || {})) {
    if (String(value || '').trim()) kept[name] = String(value);
  }
  const all = { ...((state.config && state.config.taskInputs) || {}) };
  const perRepo = { ...(all[state.root] || {}) };
  if (Object.keys(kept).length) perRepo[key] = kept;
  else delete perRepo[key];
  all[state.root] = perRepo;
  const saved = await guard('実行条件の記憶', () => automationHost.saveConfig({ ...state.config, taskInputs: all }));
  if (saved) state.config = saved;
}

function executionMachines() {
  const tasks = state.execution.snapshot && state.execution.snapshot.tasks;
  if (Array.isArray(tasks) && tasks.length) return tasks;
  const remote = state.execution.snapshot && state.execution.snapshot.machines;
  if (Array.isArray(remote) && remote.length) return remote.map((machine) => ({
    id: `machine:${machine.machine}`, kind: 'statemachine', schedules: machine.schedule ? [machine.schedule] : [], ...machine,
  }));
  return state.machines.map((machine) => ({ id: `machine:${machine.machine}`, kind: 'statemachine', ...machine, parameters: [], schedule: null, schedules: [], history: [] }));
}

function taskIdentity(task) { return String(task && (task.id || task.machine) || ''); }
function taskSchedules(task) {
  if (Array.isArray(task && task.schedules)) return task.schedules;
  return task && task.schedule ? [task.schedule] : [];
}
function taskKindLabel(task) {
  return task.kind === 'prompt' ? 'プロンプト' : task.kind === 'hook' ? 'フック' : task.kind === 'command' ? 'コマンド' : task.kind === 'broken' ? '要修正' : 'ステートマシン';
}

const RUN_POLICIES = {
  recommended: { label: 'おすすめ', tier: 'medium' },
  saving: { label: '節約', tier: 'small' },
  quality: { label: '品質重視', tier: 'large' },
  direct: { label: '直接指定', tier: '' },
};

// 最適化が効いていないときに選べる起動方針（会話画面・settings.BASIC_POLICIES と同じ規則）。
const BASIC_POLICIES = ['recommended'];

// 「エージェントを最適化する」が効いているか（設定 × ローカル実行系 herd の有無）。一覧が届く前は
// 「効いている」とみなす（先に薄くして後で戻すより目立たない）。
function optimized() {
  const execution = state.config.execution && typeof state.config.execution === 'object' ? state.config.execution : {};
  if (execution.optimizeAgents === false) return false;
  if (state.agentsLoading || !state.agents.length) return true;
  return state.agents.includes('herd');
}

function effectivePolicy(policy) {
  const name = String(policy || '');
  if (name === 'direct') return name;
  if (!RUN_POLICIES[name]) return 'recommended';
  return optimized() || BASIC_POLICIES.includes(name) ? name : 'recommended';
}

function taskRunExecution() {
  const execution = state.config.execution && typeof state.config.execution === 'object' ? state.config.execution : {};
  const policy = effectivePolicy(state.run.policy || execution.defaultPolicy || 'recommended');
  if (policy === 'direct') {
    return { policy, agent: selectedAgent(state.run.agent || state.config.agent), model: state.run.model || state.config.model || '' };
  }
  const view = RUN_POLICIES[policy] || RUN_POLICIES.recommended;
  const tier = execution.tiers && execution.tiers[view.tier] || {};
  return { policy, agent: selectedAgent(tier.cli || state.config.agent), model: tier.model || state.config.model || '' };
}

function taskRunSettingsLabel() {
  const selected = taskRunExecution();
  const policy = RUN_POLICIES[selected.policy] || RUN_POLICIES.recommended;
  const agent = selected.agent || 'エージェント未設定';
  const selection = state.config.instructions && state.config.instructions.skillSelection || {};
  const skillMode = state.run.skillMode || selection.defaultMode || 'auto';
  const skillLabel = { auto: 'スキル 自動', manual: 'スキル 手動選択', off: 'スキル 使用しない' }[skillMode] || 'スキル 自動';
  return [policy.label, `${agent}${selected.model ? ` / ${selected.model}` : ''}`, skillLabel].join(' · ');
}

function taskSkillCandidates() {
  const selection = state.config.instructions && state.config.instructions.skillSelection;
  return selection && Array.isArray(selection.candidates) ? selection.candidates : [];
}

function taskSkillChoicesHtml() {
  const selection = state.config.instructions && state.config.instructions.skillSelection || {};
  const mode = state.run.skillMode || selection.defaultMode || 'auto';
  if (mode === 'off') return '';
  if (mode === 'auto') return `<span class="muted small">${esc(state.run.skillPreview.map((item) => item.name).join(' · ') || '該当なし')}</span>`;
  return taskSkillCandidates().map((name) => `<label class="skill-choice"><input type="checkbox" data-run-skill="${esc(name)}" ${state.run.skills.includes(name) ? 'checked' : ''}><span>${esc(name)}</span></label>`).join('') || '<span class="muted small">候補なし</span>';
}

async function refreshTaskSkillPreview() {
  const machine = selectedExecutionMachine();
  const selection = state.config.instructions && state.config.instructions.skillSelection || {};
  const mode = state.run.skillMode || selection.defaultMode || 'auto';
  if (!machine || mode !== 'auto' || !automationHost.selectSkills) { state.run.skillPreview = []; return; }
  try {
    const result = await automationHost.selectSkills(state.root, JSON.stringify({ task: machine, parameters: state.run.parameters }), 'auto', []);
    state.run.skillPreview = result.selected || [];
    const list = workbenchRoot.querySelector('#run-skill-list');
    if (list) list.innerHTML = taskSkillChoicesHtml();
  } catch { state.run.skillPreview = []; }
}

function selectedExecutionMachine() {
  return executionMachines().find((machine) => taskIdentity(machine) === state.execution.selected) || null;
}

async function selectRoot(root) {
  if (!root || root === state.root) return;
  cancelAi(state.aiDraft);
  cancelAi(state.aiReview);
  state.root = root;
  await guard('フォルダ', () => automationHost.selectRoot(root));
  // 教示一覧は定義一覧と一緒に読み直す（loadMachines）ので、片付けはその前に済ませる。
  flowFeature.rootChanged();
  teachingFeature.rootChanged();
  await afterRootChange();
}

// フォルダを選び直したあと。手元のファイルで分かる定義の一覧だけを待って描き、
// ホスト（WSL）に聞くもの（AI の一覧・実行状態）は待たずに裏で取りに行く。
async function afterRootChange() {
  state.execution.snapshot = null;
  state.execution.selected = '';
  await loadMachines();
  const first = executionMachines()[0];
  state.execution.selected = first ? taskIdentity(first) : '';
  if (state.homeTab === 'teach') await teachingFeature.activate();
  render();
  loadAgents();
  refreshExecutionSnapshot();
}

async function addFolder() {
  cancelAi(state.aiDraft);
  cancelAi(state.aiReview);
  const cfg = await guard('フォルダの登録', () => automationHost.addRoot());
  if (!cfg) return;
  state.config = cfg;
  state.root = cfg.lastRoot;
  flowFeature.rootChanged();
  teachingFeature.rootChanged();
  await afterRootChange();
}

async function removeFolder(root) {
  if (!confirm(`${folderName(root)} を一覧から外しますか？（フォルダの中身は消えません）`)) return;
  cancelAi(state.aiDraft);
  cancelAi(state.aiReview);
  const cfg = await guard('フォルダ', () => automationHost.removeRoot(root));
  if (!cfg) return;
  state.config = cfg;
  if (state.root === root) state.root = cfg.lastRoot;
  flowFeature.rootChanged();
  teachingFeature.rootChanged();
  await afterRootChange();
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
  const res = await guard('読み込み', () => automationHost.readMachine(state.root, machine));
  if (!res) return;
  const raw = res.raw;
  raw.steps = raw.steps.map((s) => ({ ...emptyStep(s.kind), ...s, outcomes: s.outcomes || [], recorded: s.recorded || [], extend: s.extend || {} }));
  state.current = { machine, isNew: false, spec: raw, dirty: false, warnings: res.warnings || [], dir: res.dir };
  state.view = 'editor';
  if (embedded && selectedExecutionMachine()?.machine === machine) state.execution.detailTab = 'steps';
  else state.execution.editing = false;
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
  const t = workbenchRoot.querySelector('.title-input');
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

function isEmbeddedTaskEditor() {
  return !!(embedded && state.view === 'editor' && state.current && !state.current.isNew
    && selectedExecutionMachine()?.machine === state.current.machine);
}

function editorControlsHtml() {
  const spec = state.current.spec;
  return {
    center: `<input class="title-input" id="m-name" value="${esc(spec.name)}" placeholder="名前を付ける（例: 月次の勤怠集計）" aria-label="名前">`,
    right: `<span id="dirty-mark" class="dirty" ${state.current.dirty ? '' : 'hidden'}>● 未保存</span>
      ${embedded && !state.current.isNew ? '<div class="edit-controls"><button type="button" id="b-assist" class="ghost">編集</button></div>' : '<button type="button" id="b-ai" class="ghost">AIで見直す</button>'}
      <button type="button" id="b-run" class="ghost" ${state.current.isNew ? 'disabled title="保存すると実行できます"' : ''}>テスト</button>
      <details class="more-menu"><summary>その他</summary><div class="menu-panel">
        ${embedded ? '' : '<button type="button" id="b-record" class="ghost">操作を記録</button>'}
        <button type="button" id="b-files" class="ghost">生成ファイル</button>
        <button type="button" id="b-settings" class="ghost">実行環境</button>
      </div></details>
      <button type="button" id="b-save" class="primary">保存</button>`,
  };
}

function bindEditorControls(scope) {
  const get = (id) => scope.querySelector(`#${id}`);
  const spec = state.current.spec;
  let touched = !state.current.isNew || !!spec.machine;
  get('m-name').addEventListener('input', (event) => {
    spec.name = event.target.value;
    if (!touched) { spec.machine = saveNameFrom(event.target.value); const saveName = get('m-save-name'); if (saveName) saveName.value = spec.machine; }
    markDirty();
  });
  const saveName = get('m-save-name');
  if (saveName) saveName.addEventListener('input', () => { touched = true; });
  const assist = get('b-assist');
  if (assist) assist.addEventListener('click', () => {
    const selected = Number.isInteger(state.open) && spec.steps[state.open];
    state.aiReview.scope = selected ? { type: 'step', stepId: selected.id } : { type: 'workflow' };
    startEditing();
  });
  const record = get('b-record');
  if (record) record.addEventListener('click', openRecord);
  get('b-files').addEventListener('click', openFiles);
  const ai = get('b-ai');
  if (ai) ai.addEventListener('click', openAiReview);
  get('b-run').addEventListener('click', () => goRun(state.current.machine));
  get('b-settings').addEventListener('click', openSettings);
  get('b-save').addEventListener('click', saveMachine);
}

function render() {
  renderBar();
  teachingFeature.beginRender();
  const main = $('main');
  const editing = state.view === 'editor' && state.current;
  const taskEditing = isEmbeddedTaskEditor();
  workbenchBody.classList.toggle('is-editing', !!editing);
  workbenchBody.classList.toggle('is-task-editor', taskEditing);
  main.innerHTML = editing ? (taskEditing ? embeddedTaskEditorHtml() : editorHtml()) : homeHtml();
  if (editing) {
    // 「AIと編集」のときは工程の編集器そのものを描いていないので、その操作は結び付けない。
    const teachingCard = taskEditing && state.execution.editing;
    if (!teachingCard) {
      bindEditorControls(workbenchRoot);
      bindEditor(main);
    }
    if (taskEditing) {
      bindTaskDetailTabs(main);
      for (const button of main.querySelectorAll('[data-task-delete]')) button.addEventListener('click', () => deleteTask(selectedExecutionMachine()));
      for (const button of main.querySelectorAll('[data-task-metadata]')) button.addEventListener('click', () => openTaskMetadata(selectedExecutionMachine()));
      const back = main.querySelector('[data-edit-back]');
      if (back) back.addEventListener('click', () => stopEditing());
      const target = main.querySelector('#editing-target');
      if (target) target.addEventListener('change', () => {
        state.aiReview.scope = target.value === 'workflow'
          ? { type: 'workflow' }
          : { type: 'step', stepId: target.value.slice(5) };
        render();
      });
    }
  } else bindHome(main);
  teachingFeature.endRender();
  renderRunTerminal();
  const runLogDetails = $('run-log-details');
  if (runLogDetails) runLogDetails.addEventListener('toggle', () => {
    if (runLogDetails.isConnected) state.run.logOpen = runLogDetails.open;
  });
}

function renderBar() {
  const editing = state.view === 'editor' && state.current;
  $('btn-home').hidden = !editing || embedded;
  const center = $('bar-center');
  const right = $('bar-right');
  if (!editing) {
    center.innerHTML = '';
    right.innerHTML = '<button type="button" id="b-settings" class="ghost">実行環境</button>';
    $('b-settings').addEventListener('click', openSettings);
    return;
  }
  if (isEmbeddedTaskEditor()) {
    center.innerHTML = '';
    right.innerHTML = '';
    return;
  }
  const controls = editorControlsHtml();
  center.innerHTML = controls.center;
  right.innerHTML = controls.right;
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
  const workflowActions = state.homeTab === 'workflows'
    ? '<div class="row"><button type="button" class="primary" id="h-ai-draft">AIで下書き</button><button type="button" id="h-new">手動で作成</button></div>'
    : '';
  const selectedTask = selectedExecutionMachine();
  // 「タスク」のホームタブは、定義がまだ無いもの（新しいタスク・作成中の下書き）の画面。
  // 定義があるタスクは実行詳細（概要 / 手順 / 履歴）で開く。
  const homeContent = state.homeTab === 'teach'
    ? teachingFeature.html()
    : state.homeTab === 'run' ? executionHtml()
    : state.homeTab === 'flows'
      ? flowFeature.html()
      : `<div class="matrix">${cards}</div>`;
  const body = state.root
    ? `<div class="machine-head">
        <div><h1>${esc(folderName(state.root))}</h1><div class="where">${esc(state.root)}</div></div>${workflowActions}
      </div>
      <div class="home-tabs" role="tablist"><button type="button" data-home-tab="teach" class="${state.homeTab === 'teach' ? 'is-on' : ''}">タスク</button><button type="button" data-home-tab="run" class="${state.homeTab === 'run' ? 'is-on' : ''}">実行</button><button type="button" data-home-tab="workflows" class="${state.homeTab === 'workflows' ? 'is-on' : ''}">高度な編集</button><button type="button" data-home-tab="flows" class="${state.homeTab === 'flows' ? 'is-on' : ''}">AIワークフロー</button></div>
      ${homeContent}`
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
  for (const b of main.querySelectorAll('[data-home-tab]')) b.addEventListener('click', async () => {
    state.homeTab = b.dataset.homeTab;
    render();
    if (state.homeTab === 'teach') await teachingFeature.activate();
    if (state.homeTab === 'flows') await flowFeature.activate();
  });
  for (const b of main.querySelectorAll('[data-run-machine]')) b.addEventListener('click', () => {
    state.execution.selected = b.dataset.runMachine;
    state.execution.detailTab = 'overview';
    state.execution.scheduleOpen = false;
    state.execution.scheduleDraft = null;
    state.run.result = null;
    state.run.error = '';
    render();
  });
  bindTaskDetailTabs(main);
  for (const button of main.querySelectorAll('[data-task-delete]')) button.addEventListener('click', () => deleteTask(selectedExecutionMachine()));
  for (const button of main.querySelectorAll('[data-task-metadata]')) button.addEventListener('click', () => openTaskMetadata(selectedExecutionMachine()));
  on('run-edit', () => { const machine = selectedExecutionMachine(); if (machine) openMachine(machine.machine); });
  for (const button of main.querySelectorAll('[data-run-teach]')) button.addEventListener('click', () => {
    const machine = selectedExecutionMachine();
    if (machine && machine.machine) openTeaching(machine.machine);
  });
  on('run-start', () => startRun('run'));
  on('run-check', () => startRun('check'));
  on('run-stop', () => automationHost.runStop());
  on('schedule-toggle', () => { state.execution.scheduleOpen = !state.execution.scheduleOpen; render(); });
  on('schedule-save', saveSchedule);
  for (const button of main.querySelectorAll('[data-schedule-edit]')) button.addEventListener('click', () => {
    const machine = selectedExecutionMachine();
    const schedule = machine && taskSchedules(machine)[Number(button.dataset.scheduleEdit)];
    if (!machine || !schedule) return;
    state.execution.scheduleDraft = scheduleDraftFor(machine, schedule);
    state.execution.scheduleOpen = true;
    render();
  });
  on('daemon-toggle', toggleDaemon);
  for (const button of main.querySelectorAll('[data-history-log]')) button.addEventListener('click', () => openHistoryLog(button.dataset.historyLog));
  for (const button of main.querySelectorAll('[data-history-fix]')) button.addEventListener('click', () => handFailureToAi(button.dataset.historyFix));
  for (const input of main.querySelectorAll('[data-run-param]')) input.addEventListener('input', () => {
    state.run.parameters[input.dataset.runParam] = input.value;
    refreshTaskSkillPreview();
  });
  const runAgent = main.querySelector('#run-agent');
  const runModel = main.querySelector('#run-model');
  const runPolicy = main.querySelector('#run-policy');
  const refreshRunSettings = () => {
    const direct = main.querySelector('#run-direct-settings');
    const summary = main.querySelector('#task-run-settings-summary');
    if (direct) direct.hidden = (state.run.policy || runPolicy?.value) !== 'direct';
    if (summary) summary.textContent = taskRunSettingsLabel();
  };
  if (runPolicy) runPolicy.addEventListener('change', () => { state.run.policy = runPolicy.value; refreshRunSettings(); });
  if (runAgent) runAgent.addEventListener('change', () => { state.run.agent = runAgent.value; refreshRunSettings(); });
  if (runModel) runModel.addEventListener('input', () => { state.run.model = runModel.value; refreshRunSettings(); });
  const runSkillMode = main.querySelector('#run-skill-mode');
  const bindSkillChoices = () => {
    for (const input of main.querySelectorAll('[data-run-skill]')) input.addEventListener('change', () => {
      state.run.skills = input.checked
        ? [...new Set([...state.run.skills, input.dataset.runSkill])]
        : state.run.skills.filter((name) => name !== input.dataset.runSkill);
      refreshRunSettings();
    });
  };
  if (runSkillMode) runSkillMode.addEventListener('change', () => {
    state.run.skillMode = runSkillMode.value;
    state.run.skills = [];
    state.run.skillPreview = [];
    const list = main.querySelector('#run-skill-list');
    if (list) { list.hidden = state.run.skillMode === 'off'; list.innerHTML = taskSkillChoicesHtml(); }
    bindSkillChoices();
    refreshRunSettings();
    refreshTaskSkillPreview();
  });
  bindSkillChoices();
  refreshTaskSkillPreview();
  bindScheduleEditor(main);
  if (state.homeTab === 'teach') teachingFeature.bind(main);
  if (state.homeTab === 'flows') flowFeature.bind(main);
}

function bindTaskDetailTabs(main) {
  for (const button of main.querySelectorAll('[data-task-tab]')) button.addEventListener('click', async () => {
    const tab = button.dataset.taskTab;
    if (tab === state.execution.detailTab && !state.execution.editing && !(tab === 'steps' && state.view !== 'editor')) return;
    const machine = selectedExecutionMachine();
    if (!machine) return;
    if (state.view === 'editor' && state.current?.dirty
      && !confirm('保存していない変更があります。別のタブへ移動しますか？')) return;
    if (tab === 'steps') {
      state.execution.editing = false;
      if (machine.kind === 'statemachine') await openMachine(machine.machine);
      return;
    }
    cancelAi(state.aiReview);
    state.execution.editing = false;
    state.view = 'home';
    state.current = null;
    state.homeTab = 'run';
    state.execution.detailTab = tab;
    render();
  });
}

function goRun(machine) {
  if (!machine) return;
  state.view = 'home';
  state.current = null;
  state.homeTab = 'run';
  state.execution.selected = String(machine).startsWith('machine:') ? machine : `machine:${machine}`;
  state.execution.detailTab = 'overview';
  state.execution.scheduleDraft = null;
  state.run.result = null;
  state.run.error = '';
  // 教示から来るときは定義が増えている（利用可能になった直後）ので、定義一覧も読み直す。
  loadMachines().then(() => { render(); refreshExecutionSnapshot(); });
}

// 実行詳細から「AIに変更を相談」。教示画面をそのタスクで開く（今の版はそのまま実行できる）。
// 定義があるタスクを AI と編集する（「手順」タブの編集面を開く）。定義がまだ無いものは
// 作成の続き（teachingFeature の画面）で開く。
async function openTeaching(machine) {
  const name = String(machine || '').replace(/^machine:/, '');
  if (!name) return;
  if (embedded && state.machines.some((item) => item.machine === name)) {
    state.homeTab = 'run';
    state.execution.selected = `machine:${name}`;
    state.execution.detailTab = 'steps';
    state.execution.editing = true;
    await openMachine(name);
    return;
  }
  state.view = 'home';
  state.current = null;
  state.homeTab = 'teach';
  render();
  await teachingFeature.activate();
  await teachingFeature.select(name);
}

function scheduleLabel(schedule) {
  if (!schedule) return '未設定';
  if (schedule.kind === 'interval') return `${schedule.minutes} 分ごと`;
  if (schedule.kind === 'daily') return `毎日 ${schedule.time}`;
  if (schedule.kind === 'weekly') {
    const labels = ['日', '月', '火', '水', '木', '金', '土'];
    return `${(schedule.days || []).map((day) => labels[day]).join('・')} ${schedule.time}`;
  }
  return '詳細設定あり';
}

function dateLabel(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('ja-JP', { dateStyle: 'short', timeStyle: 'short' });
}

const teachingFeature = window.createTeachingFeature({
  root: () => state.root,
  machines: () => state.machines,
  editAgent: () => state.editAgent || selectedAgent(state.config.agent),
  isActive: () => state.view === 'home' && state.homeTab === 'teach',
  refresh: render,
  guard,
  escape: esc,
  toast,
  changed: (area, selected) => notifyHost(area, selected),
  // 定義ができた下書きから「手順を見る」
  edit: (machine) => openMachine(machine),
  // 親へ「いまこのタスクの会話を出している」を伝える。親は自分の端末ミラーを slot に載せる。
  view: (detail) => {
    if (!embedded) return;
    workbenchHost.dispatchEvent(new CustomEvent('statemachine:teaching-view', {
      detail: detail ? { type: 'agent-app:teaching-view', ...detail } : { type: 'agent-app:teaching-view', machine: '', creating: false, hidden: true },
      bubbles: true,
    }));
  },
  bridge: {
    list: (root) => automationHost.teachingList(root),
    remove: (root, machine) => automationHost.deleteMachine(root, machine),
  },
});

// 会話の入力欄へ本文を置いてもらう（1 回きり）。置き場を出すのは teachingFeature.view なので、
// それとは別の合図にする——描き直しのたびに入れ直さないため。
function notifyTeachingPrefill(text) {
  if (!embedded || !text) return;
  workbenchHost.dispatchEvent(new CustomEvent('statemachine:teaching-prefill', {
    detail: { type: 'agent-app:teaching-prefill', text },
    bubbles: true,
  }));
}

const flowFeature = window.createFlowFeature({
  name: embedded ? 'ワークフロー' : 'AIワークフロー',
  changed: (area, selected) => notifyHost(area, selected),
  root: () => state.root,
  config: () => state.config,
  agents: () => state.agents,
  isActive: () => state.view === 'home' && state.homeTab === 'flows',
  refresh: render,
  guard,
  toast,
  escape: esc,
  dateLabel,
  query: (selector) => workbenchRoot.querySelector(selector),
  activeElement: () => workbenchRoot.activeElement || document.activeElement,
  // 親へ「いまこのワークフローの会話を出している」を伝える。親は自分の端末ミラーを slot に載せる。
  teachView: (detail) => {
    if (!embedded) return;
    workbenchHost.dispatchEvent(new CustomEvent('statemachine:flow-teaching-view', {
      detail: detail ? { type: 'agent-app:flow-teaching-view', ...detail } : { type: 'agent-app:flow-teaching-view', workflowId: '', hidden: true },
      bubbles: true,
    }));
  },
  // 新しく教える: 親が会話（tmux）を用意し、保存名を返す
  teachCreate: (payload) => window.FlowTeaching.create(payload),
  bridge: {
    catalog: () => automationHost.flowCatalog(),
    list: (root) => automationHost.flowList(root),
    read: (root, id) => automationHost.flowRead(root, id),
    save: (root, workflow, mode) => automationHost.flowSave(root, workflow, mode),
    remove: (root, id) => automationHost.flowDelete(root, id),
    preview: (root, workflow, request, parameters) => automationHost.flowPreview(root, workflow, request, parameters),
    teachingList: (root) => automationHost.flowTeachingList(root),
    teachAdopt: (root, workflowId) => automationHost.flowTeachAdopt(root, workflowId),
    teachingRead: (root, workflowId) => automationHost.flowTeachingRead(root, workflowId),
    teachingSave: (root, workflowId, session) => automationHost.flowTeachingSave(root, workflowId, session),
    teachingRecordTrial: (root, workflowId, trial) => automationHost.flowTeachingRecordTrial(root, workflowId, trial),
    teachingConfirm: (root, workflowId, generationId, digest) => automationHost.flowTeachingConfirm(root, workflowId, generationId, digest),
    aiStart: (payload) => automationHost.aiStart(payload),
    context: (root) => automationHost.flowContext(root),
    runStart: (payload) => automationHost.flowRunStart(payload),
    runList: (root, limit) => automationHost.flowRunList(root, limit),
    runRead: (root, runId) => automationHost.flowRunRead(root, runId),
    runCancel: (root, runId, reason) => automationHost.flowRunCancel(root, runId, reason),
    runRespond: (root, runId, interactionId, answer) => automationHost.flowRunRespond(root, runId, interactionId, answer),
    runResult: (root, runId) => automationHost.flowRunResult(root, runId),
    runLog: (root, runId) => automationHost.flowRunLog(root, runId),
    runDelete: (root, runId) => automationHost.flowRunDelete(root, runId),
    openDelivery: (root, runId) => automationHost.flowRunOpenDelivery(root, runId),
  },
});

function taskPresentation(machine) {
  const present = machine.kind === 'statemachine' && machine.machine ? teachingFeature.statusOf(machine.machine) : null;
  const badges = present
    ? `<span class="status ${present.status === 'ready' ? 'ok' : ''}">${esc(window.teachingStatusLabel(present.status))}</span>`
    : '';
  const eyebrow = embedded ? '' : '<span class="eyebrow">タスク</span>';
  return {
    present,
    header: `<div>${eyebrow}<h2>${esc(machine.name)}${badges ? ` <span class="task-badges">${badges}</span>` : ''}</h2>${machine.description ? `<p>${esc(machine.description)}</p>` : ''}</div>`,
  };
}

function taskDetailTabsHtml(machine, activeTab) {
  return `<nav class="task-detail-tabs" role="tablist" aria-label="タスク詳細">
    <button type="button" role="tab" id="task-tab-overview" aria-controls="task-tab-panel" data-task-tab="overview" aria-selected="${activeTab === 'overview'}" class="${activeTab === 'overview' ? 'is-on' : ''}">概要</button>
    ${machine.kind === 'statemachine' ? `<button type="button" role="tab" id="task-tab-steps" aria-controls="task-tab-panel" data-task-tab="steps" aria-selected="${activeTab === 'steps'}" class="${activeTab === 'steps' ? 'is-on' : ''}">手順</button>` : ''}
    <button type="button" role="tab" id="task-tab-history" aria-controls="task-tab-panel" data-task-tab="history" aria-selected="${activeTab === 'history'}" class="${activeTab === 'history' ? 'is-on' : ''}" ${state.execution.snapshot && state.execution.snapshot.available === false ? 'disabled' : ''}>履歴</button>
  </nav>`;
}

function taskDetailShellHtml(machine, activeTab, content, { editor = false, teaching = false } = {}) {
  const presentation = taskPresentation(machine);
  const teachAction = !embedded && presentation.present
    ? '<button type="button" data-run-teach>AIに変更を相談</button>'
    : '';
  const deleteAction = machine.kind === 'statemachine' && machine.machine ? '<button type="button" class="danger ghost" data-task-delete>削除</button>' : '';
  const metadataAction = machine.kind === 'statemachine' && machine.machine ? '<button type="button" class="ghost" data-task-metadata>名前と説明を編集</button>' : '';
  const header = `<header class="execution-title">${presentation.header}${teachAction || metadataAction || deleteAction ? `<div class="row">${teachAction}${metadataAction}${deleteAction}</div>` : ''}</header>`;
  return `<div class="task-detail-shell${editor ? ' is-editor' : ''}${teaching ? ' is-teaching' : ''}">${header}${taskDetailTabsHtml(machine, activeTab)}<div class="task-tab-panel" id="task-tab-panel" role="tabpanel" aria-labelledby="task-tab-${activeTab}">${content}</div></div>`;
}

function openTaskMetadata(machine) {
  const dlg = dialog('dlg-run', 'タスクの名前と説明', 'record', `
    <div class="field"><label>名前</label><input id="task-meta-name" value="${esc(machine.name || '')}" autofocus></div>
    <div class="field"><label>説明</label><textarea id="task-meta-description" rows="3" placeholder="このタスクで行うこと">${esc(machine.description || '')}</textarea></div>
    <div class="field"><label>識別名 <span class="muted small">フォルダ名</span></label><input id="task-meta-machine" value="${esc(machine.machine)}" spellcheck="false"></div>
    <p class="msg err" id="task-meta-error" hidden></p>
    <div class="row"><button type="button" class="primary" id="task-meta-save">保存</button></div>`);
  dlg.querySelector('#task-meta-save').addEventListener('click', async () => {
    const values = { name: dlg.querySelector('#task-meta-name').value, description: dlg.querySelector('#task-meta-description').value, machine: dlg.querySelector('#task-meta-machine').value };
    try {
      const saved = await automationHost.updateMachineMetadata(state.root, machine.machine, values);
      dlg.close();
      await loadMachines();
      await loadExecutionSnapshot();
      state.execution.selected = `machine:${saved.machine}`;
      state.current = null;
      state.view = 'home';
      notifyHost('tasks', saved.machine);
      render();
      toast('名前と説明を保存しました');
    } catch (err) {
      const message = dlg.querySelector('#task-meta-error');
      message.textContent = err.message;
      message.hidden = false;
    }
  });
}

// 「手順」タブで「編集」を押した状態。会話画面と同じく、上部ツールバーの下へ
// 端末とコンポーザーを縦に並べる（カードの中へ二重に囲わない）。
function editingCardHtml(machine) {
  const spec = state.current.spec;
  const selected = reviewScopeValue(state.aiReview.scope);
  const targets = spec.steps.map((step, index) => `<option value="step:${esc(step.id)}" ${selected === `step:${step.id}` ? 'selected' : ''}>工程 ${index + 1}: ${esc(step.title || kindOf(step.kind).label)}</option>`).join('');
  return `<section class="task-conversation-editor">
    <div class="task-conversation-toolbar"><label class="editing-target" for="editing-target"><strong>編集</strong><span>編集対象</span><select id="editing-target"><option value="workflow" ${selected === 'workflow' ? 'selected' : ''}>全体</option>${targets}</select></label><button type="button" class="tiny" data-edit-back>‹ 工程に戻る</button></div>
    ${teachingFeature.editorSlotHtml(machine, selected === 'workflow' ? 'タスク全体' : `工程 ${selected.slice(5)}`)}
  </section>`;
}

async function deleteTask(machine) {
  if (!machine || !machine.machine || !confirm(`「${machine.name}」を削除しますか？\n定義、作成中の会話情報、操作の見本も削除されます。`)) return;
  const deleted = await guard('タスクの削除', () => automationHost.deleteMachine(state.root, machine.machine));
  if (!deleted) return;
  state.current = null;
  state.view = 'home';
  state.execution.editing = false;
  state.execution.selected = '';
  await loadMachines();
  await teachingFeature.activate();
  notifyHost('tasks', '');
  toast('タスクを削除しました');
  render();
}

function embeddedTaskEditorHtml() {
  const machine = selectedExecutionMachine();
  if (!machine) return editorHtml();
  if (state.execution.editing) return taskDetailShellHtml(machine, 'steps', editingCardHtml(machine), { editor: true, teaching: true });
  const controls = editorControlsHtml();
  return taskDetailShellHtml(machine, 'steps', `<div class="embedded-editor-toolbar"><div class="bar-center">${controls.center}</div><div class="bar-right">${controls.right}</div></div><div class="embedded-task-editor">${editorHtml()}</div>`, { editor: true });
}

// 「編集」: AI と手順を直す。手で直した未保存の変更は AI の書き換えで消えるので先に確かめる。
function startEditing() {
  if (state.current && state.current.dirty
    && !confirm('保存していない変更があります。AI との編集に移ると失われます。続けますか？')) return;
  state.execution.editing = true;
  render();
}

// 「工程に戻る」: AI が書き換えた定義を読み直してから工程へ戻る。
async function stopEditing() {
  const machine = selectedExecutionMachine();
  state.execution.editing = false;
  if (machine && machine.machine) await openMachine(machine.machine);
  else render();
}

function executionHtml() {
  const machines = executionMachines();
  if (state.execution.loading && !machines.length) return '<div class="blank compact"><p>実行情報を読み込んでいます…</p></div>';
  if (!machines.length) return '<div class="blank compact"><h2>実行できるワークフローがありません</h2><p>ワークフローを作成すると、ここから実行できます。</p></div>';
  const selected = selectedExecutionMachine() || machines[0];
  // 実行状態が届く前は定義だけで描いている。履歴も定期実行もまだ分からないので、確定した
  // 「未実行」「予定なし」とは書かない。
  const pending = state.execution.loading && !state.execution.snapshot;
  const list = machines.map((machine) => {
    const latest = (machine.history || [])[0];
    const status = pending ? '確認中…' : latest ? (latest.ok ? '完了' : latest.escalate ? '要確認' : '失敗') : '未実行';
    const schedules = taskSchedules(machine);
    const scheduleStatus = pending ? '確認中…' : schedules.length
      ? `${schedules.filter((item) => item.effective !== false).length}/${schedules.length} 件の予定`
      : '予定なし';
    return `<button type="button" class="execution-item ${taskIdentity(machine) === taskIdentity(selected) ? 'is-on' : ''}" data-run-machine="${esc(taskIdentity(machine))}"><strong>${esc(machine.name)}</strong><span>${esc(taskKindLabel(machine))} · ${esc(status)} · ${esc(scheduleStatus)}</span></button>`;
  }).join('');
  return `<div class="execution-layout"><aside class="execution-list" aria-label="実行するワークフロー">${list}</aside><section class="execution-detail">${executionDetailHtml(selected)}</section></div>`;
}

function executionDetailHtml(machine) {
  ensureRunParameters(machine);
  const snapshot = state.execution.snapshot || {};
  const daemon = snapshot.daemon || { running: false };
  const schedules = taskSchedules(machine);
  const scheduleRows = schedules.map((item, index) => {
    const where = item.source && item.source.scope === 'global' ? '共通設定' : 'このリポジトリ';
    const active = item.effective === false ? '<span class="status warn">未適用</span>' : '<span class="status ok">適用中</span>';
    const next = item.nextAt ? ` · 次回 ${esc(dateLabel(item.nextAt))}` : '';
    const edit = ['statemachine', 'prompt'].includes(machine.kind) ? `<button type="button" class="tiny" data-schedule-edit="${index}">編集</button>` : '';
    return `<li><div><strong>${esc(item.entryName || `予定 ${index + 1}`)}</strong><small>${esc(scheduleLabel(item))}${next} · ${esc(where)}</small></div>${active}${edit}</li>`;
  }).join('');
  const checking = state.execution.loading && !state.execution.snapshot;
  const daemonStatus = checking ? '実行状態を確認しています…' : snapshot.available === false ? '定期実行と履歴には agent-loop が要ります' : daemon.activeCount
    ? `${daemon.activeCount} 件を実行中${daemon.queueDepth ? `、${daemon.queueDepth} 件待機` : ''}`
    : daemon.running ? (daemon.queueDepth ? `${daemon.queueDepth} 件待機` : '自動実行は稼働中') : '自動実行は停止中';
  const parameters = machine.parameters || [];
  // 実行条件は前回の値を既定にする（優先順位は initialRunParameters）。前回の値は、いま入って
  // いるものと違うときだけ 1 行添える——同じものを 2 回言わない。
  const previous = rememberedInputs(machine);
  const inputs = parameters.length ? `<div class="run-inputs"><h3>実行条件</h3><div class="run-input-grid">${parameters.map((name) => {
    const value = state.run.parameters[name] || '';
    const hint = previous[name] && previous[name] !== value ? `<small class="muted">前回: ${esc(previous[name])}</small>` : '';
    return `<div class="field"><label>${esc(name)}</label><input data-run-param="${esc(name)}" value="${esc(value)}">${hint}</div>`;
  }).join('')}</div></div>` : '';
  // 失敗した行からは「手順」→「編集」と同じ会話を起こし、失敗の中身を入力欄へ置く
  const canTeach = !!(machine.machine && machine.kind === 'statemachine');
  const history = (machine.history || []).map((item) => {
    const status = item.ok ? '完了' : item.escalate ? '要確認' : '失敗';
    const cls = item.ok ? 'ok' : item.escalate ? 'warn' : 'ng';
    // 行の操作は 1 つの列にまとめる（行の骨格は 3 列のまま）
    const actions = [
      item.logFile ? `<button type="button" class="tiny" data-history-log="${esc(item.runId)}">ログ</button>` : '',
      !item.ok && canTeach ? `<button type="button" class="tiny" data-history-fix="${esc(item.runId)}">AIに直してもらう</button>` : '',
    ].filter(Boolean).join('');
    return `<li><span class="status ${cls}">${status}</span><div><strong>${item.source === 'scheduled' ? '定期実行' : '手動実行'}</strong><small>${esc(dateLabel(item.finishedAt || item.startedAt))}${item.agentCli ? ` · ${esc(item.agentCli)}` : ''}${item.model ? ` / ${esc(item.model)}` : ''}</small>${item.error ? `<p>${esc(item.error)}</p>` : ''}</div>${actions ? `<div class="row">${actions}</div>` : ''}</li>`;
  }).join('');
  const historyLog = state.execution.log
    ? `<div class="history-log"><div class="execution-card-head"><strong>実行ログ</strong><button type="button" class="tiny" data-history-log="">閉じる</button></div>${state.execution.log.error ? `<p class="run-result ng">${esc(state.execution.log.error)}</p>` : `<pre>${esc(state.execution.log.text || '')}</pre>${state.execution.log.truncated ? '<small class="muted">末尾のみ表示しています。</small>' : ''}`}</div>`
    : '';
  const result = state.run.result
    ? `<p class="run-result ${state.run.result.ok ? 'ok' : state.run.result.escalate ? 'warn' : 'ng'}">${state.run.result.ok ? '実行が完了しました' : state.run.result.escalate ? `確認が必要です${state.run.result.error ? `: ${esc(state.run.result.error)}` : ''}` : esc(state.run.result.error || '実行に失敗しました')}</p>`
    : state.run.error ? `<p class="run-result ng">${esc(state.run.error)}</p>` : '';
  const log = state.run.lines.map((line) => `<div class="${line.kind === 'stderr' ? 'e' : ''}">${esc(line.line)}</div>`).join('') || (state.run.terminal ? '' : '<span class="muted">実行すると、ここに進行状況が表示されます。</span>');
  const logBody = `<div class="log" id="run-log">${log}</div>`;
  const logView = state.run.terminal
    ? `<details id="run-log-details" ${state.run.logOpen ? 'open' : ''} ${state.run.lines.length ? '' : 'hidden'}><summary>${state.run.lines.some((line) => line.kind === 'stderr') ? '実行ログ（警告・エラーあり）' : '実行ログ'}</summary>${logBody}</details>`
    : logBody;
  const canRun = ['statemachine', 'prompt'].includes(machine.kind || 'statemachine') && !machine.error;
  const selectedRun = taskRunExecution();
  const direct = (state.run.policy || (state.config.execution && state.config.execution.defaultPolicy) || 'recommended') === 'direct';
  const policyOn = optimized();
  const policyOptions = Object.entries(RUN_POLICIES).map(([value, item]) => `<option value="${value}" ${selectedRun.policy === value ? 'selected' : ''} ${policyOn || BASIC_POLICIES.includes(value) || value === 'direct' ? '' : 'disabled'}>${item.label}</option>`).join('');
  const selection = state.config.instructions && state.config.instructions.skillSelection || {};
  const skillMode = state.run.skillMode || selection.defaultMode || 'auto';
  const runFields = `<details id="task-run-settings" class="run-settings task-run-settings"><summary><span id="task-run-settings-summary">${esc(taskRunSettingsLabel())}</span></summary><div class="settings-popover"><div class="popover-head">今回の実行設定</div><label>起動方針<select id="run-policy">${policyOptions}</select></label><div id="run-direct-settings" class="direct-agent-settings" ${direct ? '' : 'hidden'}><label>エージェント<select id="run-agent" ${state.agents.length ? '' : 'disabled'}>${agentOptions(state.run.agent || state.config.agent)}</select></label><label>モデル<input id="run-model" class="mono" value="${esc(state.run.model || state.config.model || '')}" placeholder="自動"></label></div><p class="muted small">手動実行ではツールを自動承認します。</p><label>スキル<select id="run-skill-mode"><option value="auto" ${skillMode === 'auto' ? 'selected' : ''}>自動</option><option value="manual" ${skillMode === 'manual' ? 'selected' : ''}>手動選択</option><option value="off" ${skillMode === 'off' ? 'selected' : ''}>使用しない</option></select></label><div id="run-skill-list" class="skill-choice-list" ${skillMode === 'off' ? 'hidden' : ''}>${taskSkillChoicesHtml()}</div></div></details>`;
  const taskWarning = machine.error
    ? `<p class="run-result ng">${esc(machine.error)}</p>`
    : machine.kind === 'hook' ? '<p class="run-result warn">フックだけのタスクは定期実行で起動します。</p>'
      : machine.kind === 'command' ? '<p class="run-result warn">コマンドのタスクは定期実行で起動します。</p>' : '';
  const detail = state.execution.detailTab === 'history'
    ? `<section class="execution-card"><div class="execution-card-head"><div><h3>実行履歴</h3><p>直近の手動実行と定期実行</p></div></div>${history ? `<ul class="run-history">${history}</ul>` : '<p class="muted small">実行履歴はまだありません。</p>'}${historyLog}</section>`
    : state.execution.detailTab === 'overview' ? `${!checking && snapshot.available === false && machine.kind !== 'statemachine' ? `<p class="run-result warn">${esc(snapshot.error || '実行基盤に接続できませんでした')}</p>` : ''}
      <section class="execution-card run-card"><div class="execution-card-head"><h3>手動実行</h3><span class="status ${state.run.running ? 'active' : ''}">${state.run.running ? '実行中' : '待機中'}</span></div>
        ${taskWarning}<div class="run-toolbar">${runFields}<span class="run-toolbar-spacer"></span><button type="button" class="primary" id="run-start" ${state.run.running || (snapshot.available === false && machine.kind !== 'statemachine') || !state.agents.length || !canRun ? 'disabled' : ''}>実行</button>${machine.kind === 'statemachine' ? `<button type="button" id="run-check" ${state.run.running ? 'disabled' : ''}>構成を確認</button>` : ''}<button type="button" class="danger" id="run-stop" ${state.run.running ? '' : 'disabled'}>停止</button></div>${inputs}${result}${state.run.terminal ? '<slot name="task-run-terminal"></slot>' : ''}${logView}</section>
      <section class="execution-card ${snapshot.available === false ? 'is-off' : ''}"><div class="execution-card-head"><div><h3>定期実行</h3><p>${schedules.length ? `${schedules.length} 件の予定` : '予定なし'} · ${esc(daemonStatus)}</p></div><div class="row"><button type="button" id="daemon-toggle" ${snapshot.available === false || (!schedules.length && !daemon.running) ? 'disabled' : ''}>${daemon.running ? '自動実行を停止' : '自動実行を開始'}</button>${['statemachine', 'prompt'].includes(machine.kind) ? `<button type="button" id="schedule-toggle" ${snapshot.available === false ? 'disabled' : ''}>${state.execution.scheduleOpen ? '閉じる' : schedules.length ? '予定を編集' : '予定を追加'}</button>` : ''}</div></div>${scheduleRows ? `<ul class="run-history schedule-list">${scheduleRows}</ul>` : ''}${state.execution.scheduleOpen ? scheduleEditorHtml(machine) : ''}</section>` : '';
  return taskDetailShellHtml(machine, state.execution.detailTab, detail);
}

function ensureScheduleDraft(machine) {
  if (state.execution.scheduleDraft && state.execution.scheduleDraft.taskId === taskIdentity(machine)) return state.execution.scheduleDraft;
  const existing = taskSchedules(machine).find((item) => item.effective !== false) || taskSchedules(machine)[0] || null;
  state.execution.scheduleDraft = scheduleDraftFor(machine, existing);
  return state.execution.scheduleDraft;
}

function scheduleDraftFor(machine, existing) {
  const schedule = existing || { enabled: true, kind: 'daily', time: '09:00', days: [1], input: {} };
  return {
    taskId: taskIdentity(machine), entryRef: schedule.entryRef || '', fingerprint: schedule.fingerprint || '',
    entryName: schedule.entryName || `${machine.name} の定期実行`,
    destination: schedule.source && schedule.source.scope === 'global' ? 'global' : 'repository',
    originalDestination: schedule.source && schedule.source.scope === 'global' ? 'global' : 'repository',
    operation: existing ? 'save' : 'create', enabled: schedule.enabled !== false, kind: schedule.kind || 'daily',
    time: schedule.time || '09:00', minutes: schedule.minutes || 60,
    days: [...(schedule.days || [1])], input: { ...(schedule.input || {}) },
  };
}

function scheduleEditorHtml(machine) {
  const draft = ensureScheduleDraft(machine);
  if (taskSchedules(machine).some((item) => item.entryRef === draft.entryRef && item.advanced)) return '<p class="run-result warn">この予定は詳細設定で管理されています。画面からは変更できません。</p>';
  const timing = draft.kind === 'interval'
    ? `<div class="field"><label>間隔（分）</label><input id="schedule-minutes" type="number" min="1" value="${esc(draft.minutes)}"></div>`
    : `<div class="field"><label>時刻</label><input id="schedule-time" type="time" value="${esc(draft.time)}"></div>${draft.kind === 'weekly' ? `<div class="weekday-row">${['日', '月', '火', '水', '木', '金', '土'].map((label, day) => `<label><input type="checkbox" data-schedule-day="${day}" ${draft.days.includes(day) ? 'checked' : ''}>${label}</label>`).join('')}</div>` : ''}`;
  const inputs = (machine.parameters || []).map((name) => `<div class="field"><label>${esc(name)}</label><input data-schedule-param="${esc(name)}" value="${esc(draft.input[name] || '')}"></div>`).join('');
  return `<div class="schedule-editor"><div class="grid2"><div class="field"><label>予定名</label><input id="schedule-name" value="${esc(draft.entryName)}"></div><div class="field"><label>保存先</label><select id="schedule-destination"><option value="repository" ${draft.destination === 'repository' ? 'selected' : ''}>このリポジトリ</option><option value="global" ${draft.destination === 'global' ? 'selected' : ''}>共通設定</option></select></div></div><label class="check-label"><input id="schedule-enabled" type="checkbox" ${draft.enabled ? 'checked' : ''}>有効にする</label><div class="grid2"><div class="field"><label>繰り返し</label><select id="schedule-kind"><option value="daily" ${draft.kind === 'daily' ? 'selected' : ''}>毎日</option><option value="weekly" ${draft.kind === 'weekly' ? 'selected' : ''}>毎週</option><option value="interval" ${draft.kind === 'interval' ? 'selected' : ''}>一定間隔</option></select></div>${timing}</div>${inputs ? `<div class="run-inputs"><h3>実行条件</h3><div class="run-input-grid">${inputs}</div></div>` : ''}<div class="row"><button type="button" class="primary" id="schedule-save">保存</button><button type="button" id="schedule-new">別の予定を追加</button></div></div>`;
}

function bindScheduleEditor(main) {
  const machine = selectedExecutionMachine();
  if (!machine || !state.execution.scheduleOpen) return;
  const draft = ensureScheduleDraft(machine);
  const enabled = main.querySelector('#schedule-enabled');
  const kind = main.querySelector('#schedule-kind');
  const time = main.querySelector('#schedule-time');
  const minutes = main.querySelector('#schedule-minutes');
  const name = main.querySelector('#schedule-name');
  const destination = main.querySelector('#schedule-destination');
  if (enabled) enabled.addEventListener('change', () => { draft.enabled = enabled.checked; });
  if (kind) kind.addEventListener('change', () => { draft.kind = kind.value; render(); });
  if (time) time.addEventListener('input', () => { draft.time = time.value; });
  if (minutes) minutes.addEventListener('input', () => { draft.minutes = Number(minutes.value); });
  if (name) name.addEventListener('input', () => { draft.entryName = name.value; });
  if (destination) destination.addEventListener('change', () => { draft.destination = destination.value; });
  const create = main.querySelector('#schedule-new');
  if (create) create.addEventListener('click', () => {
    state.execution.scheduleDraft = scheduleDraftFor(machine, null);
    render();
  });
  for (const day of main.querySelectorAll('[data-schedule-day]')) day.addEventListener('change', () => {
    const value = Number(day.dataset.scheduleDay);
    draft.days = day.checked ? [...new Set([...draft.days, value])].sort() : draft.days.filter((item) => item !== value);
  });
  for (const input of main.querySelectorAll('[data-schedule-param]')) input.addEventListener('input', () => { draft.input[input.dataset.scheduleParam] = input.value; });
}

async function saveSchedule() {
  const machine = selectedExecutionMachine();
  if (!machine) return;
  const draft = ensureScheduleDraft(machine);
  const schedule = draft.kind === 'interval'
    ? { kind: 'interval', minutes: draft.minutes }
    : { kind: draft.kind, time: draft.time, ...(draft.kind === 'weekly' ? { days: draft.days } : {}) };
  const result = await guard('定期実行の保存', () => automationHost.saveRunSchedule(state.root, {
    workflow: machine.workflow, entry: machine.entry, entryName: draft.entryName, enabled: draft.enabled, schedule, input: draft.input,
    destination: draft.destination,
    operation: draft.operation === 'create' || draft.destination !== draft.originalDestination ? 'create' : 'save',
    ...(draft.destination === draft.originalDestination ? { entryRef: draft.entryRef, fingerprint: draft.fingerprint } : {}),
    agentCli: selectedAgent(state.config.agent), model: state.config.model || '',
  }));
  if (!result) return;
  await loadExecutionSnapshot();
  state.execution.scheduleOpen = false;
  state.execution.scheduleDraft = null;
  render();
  notifyHost('tasks', taskIdentity(machine));
  toast(result.applied ? '定期実行を保存し、反映しました' : '定期実行を保存しました');
}

async function toggleDaemon() {
  const daemon = (state.execution.snapshot && state.execution.snapshot.daemon) || { running: false };
  const action = daemon.running ? 'stop' : 'start';
  const result = await guard(action === 'start' ? '自動実行の開始' : '自動実行の停止', () => automationHost.setRunDaemon(state.root, action));
  if (!result) return;
  toast(action === 'start' ? '自動実行の起動を受け付けました' : '自動実行の停止を受け付けました');
  setTimeout(async () => { await loadExecutionSnapshot(); if (state.view === 'home') render(); }, 500);
}

// 失敗した実行を、そのままAIへ渡す。会話は「手順」→「編集」と同じもので、最初の依頼は
// **入力欄に置くだけ**（送るのは利用者。何を直したいかを足せるように）。
// ログは、リポジトリの中にあるときだけ所在（相対パス）を添え、いつでも末尾を本文に載せる
// ——所在だけでは、agent-loop の置き場が会話の作業フォルダの外にあるときに読めない。
const FIX_LOG_LINES = 60;
const FIX_LOG_CHARS = 2000;

function repoRelative(file) {
  const norm = (value) => String(value || '').replace(/\\/g, '/').replace(/\/+$/, '');
  const root = norm(state.root);
  const target = norm(file);
  if (!root || !target) return '';
  return target.toLowerCase().startsWith(`${root.toLowerCase()}/`) ? target.slice(root.length + 1) : '';
}

function failurePrompt(machine, item, logText) {
  const tail = String(logText || '').split(/\r?\n/).filter((line) => line.trim()).slice(-FIX_LOG_LINES).join('\n').slice(-FIX_LOG_CHARS);
  const relative = repoRelative(item.logFile);
  const facts = [
    `- タスク: ${machine.name || machine.machine}`,
    `- 実行: ${item.source === 'scheduled' ? '定期実行' : '手動実行'} ${dateLabel(item.finishedAt || item.startedAt)}${item.agentCli ? ` · ${item.agentCli}` : ''}`,
    item.error ? `- エラー: ${item.error}` : '',
    relative ? `- ログ: ${relative}` : '',
  ].filter(Boolean).join('\n');
  return [
    'このタスクの実行が失敗しました。原因を調べて、手順を直してください。',
    facts,
    tail ? `実行ログ（末尾）:\n\`\`\`\n${tail}\n\`\`\`` : '',
  ].filter(Boolean).join('\n\n');
}

async function handFailureToAi(runId) {
  const machine = selectedExecutionMachine();
  const item = (machine && (machine.history || []).find((entry) => entry.runId === runId)) || null;
  if (!machine || !item) return;
  let log = null;
  if (item.logFile) {
    log = await guard('ログ', () => automationHost.runLog(state.root, { workflow: machine.workflow, runId }));
  }
  const prefill = failurePrompt(machine, item, log && log.text);
  await openTeaching(machine.machine);
  notifyTeachingPrefill(prefill);
  toast('編集を開始すると、失敗の内容が入力欄に入ります');
}

async function openHistoryLog(runId) {
  if (!runId) { state.execution.log = null; render(); return; }
  const machine = selectedExecutionMachine();
  if (!machine) return;
  state.execution.log = { runId, text: '読み込んでいます…', truncated: false };
  render();
  try {
    const result = await automationHost.runLog(state.root, { workflow: machine.workflow, runId });
    state.execution.log = { runId, ...result };
  } catch (err) {
    state.execution.log = { runId, error: String((err && err.message) || err) };
  }
  render();
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
  for (const tag of extendTags(step)) sub.push(`<span class="ext">${esc(tag)}</span>`);
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

// --- 記録の拡張（繰り返す・読み取る・確認する・失敗したら） -----------------------------
// 記録は人が 1 回通った経路しか持たない。広げ方は自由記述にせず、選択肢から選ばせて main が決まった文を書く。

const EXTRACT_MODES = [['text', '全文'], ['table', '表'], ['list', '一覧の各項目']];
const LOOP_COUNTS = [['n', '件数を決める'], ['all', 'すべて'], ['pages', '次のページも含めてすべて']];
const LOOP_BACKS = [['', '戻らない'], ['history', '履歴を 1 つ戻る'], ['goto', '一覧の URL を開き直す']];
const EXPECT_KINDS = [['visible', '要素が見える'], ['text', '要素に文字が含まれる'], ['count', '要素が N 件以上ある']];
const ON_ERROR_ITEM = [['skip', 'その件を飛ばして続ける'], ['abort', '中止する']];
const ON_ERROR_STEP = [['abort', 'すぐ中止する'], ['retry', 'やり直す'], ['agent', 'AI に別の操作を任せる']];

function extractsOf(step) {
  return (step.recorded || []).filter((op) => op.op === 'extract');
}

function extendTags(step) {
  const ext = step.extend || {};
  const tags = [];
  if (ext.loop) tags.push(ext.loop.count === 'n' ? `↻ ${ext.loop.n || 3} 件` : ext.loop.count === 'pages' ? '↻ 全ページ' : '↻ すべて');
  const n = extractsOf(step).length;
  if (n) tags.push(`⤓ 読み取り ${n}`);
  if (ext.expect) tags.push('◎ 確認');
  if (ext.onError) tags.push('⚑ 失敗時');
  return tags;
}

function options(list, value) {
  return list.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(value == null ? '' : value) ? 'selected' : ''}>${esc(l)}</option>`).join('');
}

function extendHtml(step) {
  const ext = step.extend || {};
  const loop = ext.loop;
  const expect = ext.expect;
  const onError = ext.onError || { item: 'abort', step: 'abort', retries: 1 };
  const firstClick = (step.recorded || []).find((op) => op.op === 'click' && op.target);
  const overHint = firstClick ? firstClick.target.replace(/,\s*\{\s*name:[^}]*\}\s*\)$/, ')') : "getByRole('link')";
  const extracts = extractsOf(step);
  return `<div class="section-title">記録を広げる</div>
    <div class="ext-block">
      <label class="check-label"><input type="checkbox" data-ext-on="loop" ${loop ? 'checked' : ''}> 繰り返す（同じ形の要素を順に）</label>
      ${loop ? `<div class="ext-grid">
        <div class="field"><label>対象（同じ形の要素）</label><input data-ext="loop.over" class="mono" value="${esc(loop.over || '')}" placeholder="${esc(overHint)}"></div>
        <div class="field"><label>件数</label><div class="row"><select data-ext="loop.count">${options(LOOP_COUNTS, loop.count || 'n')}</select>${(loop.count || 'n') === 'n' ? `<input data-ext="loop.n" type="number" min="1" max="50" value="${esc(loop.n || 3)}" style="width:72px">` : ''}</div></div>
        ${loop.count === 'pages' ? `<div class="field"><label>次のページの要素</label><input data-ext="loop.next" class="mono" value="${esc(loop.next || '')}" placeholder="getByRole('link', { name: '次へ' })"></div>` : ''}
        <div class="field"><label>各件のあと</label><select data-ext="loop.back">${options(LOOP_BACKS, loop.back || '')}</select></div>
      </div>` : ''}
    </div>
    <div class="ext-block">
      <label>読み取る（画面の内容を後の工程へ渡す）</label>
      ${extracts.length ? `<ol class="rec-list">${extracts.map((op) => `<li>${esc(op.target)} → <span class="mono">${esc(op.key || 'text')}</span>（${esc((EXTRACT_MODES.find(([v]) => v === op.mode) || EXTRACT_MODES[0])[1])}）<button type="button" class="tiny" data-ext-unextract="${esc(op.target)}|${esc(op.key || 'text')}" title="外す">✕</button></li>`).join('')}</ol>` : ''}
      <div class="ext-grid ext-add">
        <div class="field"><label>要素</label><input id="ext-target" class="mono" placeholder="getByRole('article')"></div>
        <div class="field"><label>形</label><select id="ext-mode">${options(EXTRACT_MODES, 'text')}</select></div>
        <div class="field"><label>出力名</label><input id="ext-key" class="mono" placeholder="body"></div>
        <div class="field"><label>&nbsp;</label><button type="button" class="tiny" data-ext-extract>＋ 追加</button></div>
      </div>
    </div>
    <div class="ext-block">
      <label class="check-label"><input type="checkbox" data-ext-on="expect" ${expect ? 'checked' : ''}> 確認する（確定の後に確かめる）</label>
      ${expect ? `<div class="ext-grid">
        <div class="field"><label>何を</label><select data-ext="expect.kind">${options(EXPECT_KINDS, expect.kind)}</select></div>
        <div class="field"><label>要素</label><input data-ext="expect.target" class="mono" value="${esc(expect.target || '')}" placeholder="getByRole('heading', { name: '完了' })"></div>
        ${expect.kind !== 'visible' ? `<div class="field"><label>${expect.kind === 'count' ? '件数' : '含まれる文字'}</label><input data-ext="expect.value" value="${esc(expect.value || '')}"></div>` : ''}
      </div>` : ''}
    </div>
    <div class="ext-block">
      <label class="check-label"><input type="checkbox" data-ext-on="onError" ${ext.onError ? 'checked' : ''}> 失敗したら</label>
      ${ext.onError ? `<div class="ext-grid">
        ${loop ? `<div class="field"><label>1 件で失敗したら</label><select data-ext="onError.item">${options(ON_ERROR_ITEM, onError.item)}</select></div>` : ''}
        <div class="field"><label>工程が失敗したら</label><div class="row"><select data-ext="onError.step">${options(ON_ERROR_STEP, onError.step)}</select>${onError.step === 'retry' ? `<input data-ext="onError.retries" type="number" min="1" max="5" value="${esc(onError.retries || 1)}" style="width:64px"> 回まで` : ''}</div></div>
      </div>` : ''}
    </div>`;
}

function stepBodyHtml(spec, index) {
  const step = spec.steps[index];
  const kind = kindOf(step.kind);
  const seg = state.catalog.kinds.map((k) => `<button type="button" data-kind="${esc(k.id)}" class="${k.id === step.kind ? 'is-on' : ''}" title="${esc(k.description)}"><span class="dot k-${esc(k.id)}"></span>${esc(k.label)}</button>`).join('');
  const target = kind.target ? `<div class="field"><label>${esc(kind.target.label)}${kind.target.required ? '' : '（任意）'}</label><input data-field="target" class="mono" value="${esc(step.target)}" placeholder="${esc(kind.target.placeholder || '')}"></div>` : '';
  const recorded = step.recorded && step.recorded.length ? `<div class="field"><label>記録した操作（${step.recorded.length} 件）</label>
    <ol class="rec-list">${step.recorded.map((op) => `<li>${esc(op.op)} ${esc(op.label || op.target)}${op.value ? ` ${esc(op.value)}` : ''}${op.op === 'extract' ? ` → <span class="mono">${esc(op.key || 'text')}</span>` : ''}${op.example ? ` <span class="muted">(例: ${esc(op.example)})</span>` : ''}</li>`).join('')}</ol>
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
    ${kind.recordable ? extendHtml(step) : ''}
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

// 修飾の編集。チェックで節を開き、値は step.extend の入れ子に書く。読み取りは recorded の extract に足す。
function bindExtend(body, step, index) {
  step.extend = step.extend || {};
  const defaults = {
    loop: () => ({ over: '', count: 'n', n: 3, back: 'goto', next: '' }),
    expect: () => ({ kind: 'visible', target: '', value: '' }),
    onError: () => ({ item: 'skip', step: 'abort', retries: 1 }),
  };
  for (const el of body.querySelectorAll('[data-ext-on]')) {
    el.addEventListener('change', () => {
      const key = el.dataset.extOn;
      if (el.checked) step.extend[key] = defaults[key]();
      else delete step.extend[key];
      markDirty();
      render();
    });
  }
  for (const el of body.querySelectorAll('[data-ext]')) {
    const [group, field] = el.dataset.ext.split('.');
    const rerender = el.tagName === 'SELECT';
    el.addEventListener(rerender ? 'change' : 'input', () => {
      if (!step.extend[group]) step.extend[group] = defaults[group]();
      step.extend[group][field] = el.type === 'number' ? Number(el.value) : el.value;
      markDirty();
      if (rerender) render(); else refreshHead(index);
    });
  }
  const add = body.querySelector('[data-ext-extract]');
  if (add) {
    add.addEventListener('click', () => {
      const target = body.querySelector('#ext-target').value.trim();
      const key = body.querySelector('#ext-key').value.trim() || 'text';
      const mode = body.querySelector('#ext-mode').value;
      if (!target) { toast('読み取る要素を入力してください'); return; }
      const role = (/^getByRole\('([a-z]+)'/.exec(target) || [])[1] || '';
      step.recorded = [...(step.recorded || []), { op: 'extract', target, role, label: '', mode, key }];
      markDirty();
      render();
    });
  }
  for (const b of body.querySelectorAll('[data-ext-unextract]')) {
    b.addEventListener('click', () => {
      const [target, key] = b.dataset.extUnextract.split('|');
      const at = step.recorded.findIndex((op) => op.op === 'extract' && op.target === target && (op.key || 'text') === key);
      if (at >= 0) step.recorded.splice(at, 1);
      markDirty();
      render();
    });
  }
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
      if (!next.recordable) { step.recorded = []; step.extend = {}; }
      markDirty();
      render();
    });
  }
  bindExtend(body, step, index);
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
    const inputs = workbenchRoot.querySelectorAll('.inspector [data-branch] input');
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
  const card = workbenchRoot.querySelector(`[data-step="${index}"]`);
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
  const edge = workbenchRoot.querySelector(`[data-edge="${index}"] .lines`);
  if (!edge) return;
  edge.innerHTML = nextsOf(state.current.spec, index).map((e) => `<span class="t"><span class="lbl ${e.cls}">${esc(e.label)}</span><span>→</span><span class="to ${e.cls}">${esc(e.text)}</span></span>`).join('');
}

function scrollToStep(index) {
  const el = workbenchRoot.querySelector(`[data-step="${index}"]`);
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
  const first = workbenchRoot.querySelector('.inspector [data-field="title"]');
  if (first) first.focus();
}

// --- 検査・保存 -------------------------------------------------------------------------

async function previewMachine() {
  if (!state.current) return null;
  const res = await guard('確認', () => automationHost.previewMachine(specPayload()));
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
    const exists = await guard('確認', () => automationHost.machineExists(state.root, payload.machine));
    if (exists && !confirm(`「${payload.machine}」は既にあります。置き換えますか？`)) return;
  }
  const res = await guard('保存', () => automationHost.saveMachine(state.root, payload));
  if (!res) return;
  Object.assign(state.current, { machine: res.machine, isNew: false, dirty: false, dir: res.dir, warnings: res.warnings || [] });
  state.current.spec.machine = res.machine;
  toast('保存しました');
  await loadMachines();
  render();
  notifyHost('tasks', res.machine);
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
    ${rec.active && !windows ? recordPickHtml(rec) : ''}
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
  const snap = dlg.querySelector('#r-snapshot');
  if (snap) snap.addEventListener('click', () => recordingAction('snapshot'));
  const pick = dlg.querySelector('#r-pick');
  if (pick) {
    pick.addEventListener('change', () => { rec.pick.ref = pick.value; });
    dlg.querySelector('#r-pick-mode').addEventListener('change', (e) => { rec.pick.mode = e.target.value; });
    dlg.querySelector('#r-pick-key').addEventListener('input', (e) => { rec.pick.key = e.target.value; });
    dlg.querySelector('#r-extract').addEventListener('click', () => recordingAction('extract'));
  }
}

// 記録中に「いま見えている要素」を読み取りとして挿す。要素は snapshot の一覧から選ぶ（ref は残さない）。
function recordPickHtml(rec) {
  const pick = rec.pick;
  const head = `<div class="row"><button type="button" id="r-snapshot" ${rec.busy ? 'disabled' : ''}>いま見えている要素を読み取る</button>
    <span class="small muted">${rec.extracts ? `読み取り ${rec.extracts} 件を挿しました。` : '記事の本文や表など、後の工程で使う内容を選びます。'}</span></div>`;
  if (!pick) return head;
  const label = (c) => `${'　'.repeat(Math.min(c.depth, 6))}${c.role}${c.name ? ` 「${c.name.slice(0, 40)}」` : ''}`;
  return `${head}<div class="ext-grid" style="margin-top:8px">
    <div class="field" style="grid-column: 1 / -1"><label>要素（${pick.candidates.length} 件）</label><select id="r-pick" size="8" class="mono">${pick.candidates.map((c) => `<option value="${esc(c.ref)}" ${c.ref === pick.ref ? 'selected' : ''}>${esc(label(c))}</option>`).join('')}</select></div>
    <div class="field"><label>形</label><select id="r-pick-mode">${options(EXTRACT_MODES, pick.mode)}</select></div>
    <div class="field"><label>出力名</label><input id="r-pick-key" class="mono" value="${esc(pick.key)}" placeholder="body"></div>
    <div class="field"><label>&nbsp;</label><button type="button" id="r-extract" class="primary" ${rec.busy ? 'disabled' : ''}>この要素を読み取りにする</button></div>
  </div>`;
}

async function recordingAction(action) {
  const rec = state.recording;
  if (rec.busy) return;
  const payload = { root: state.root, source: rec.source, url: rec.url, app: rec.app };
  if (action === 'import') {
    payload.text = rec.text;
    if (!String(rec.text || '').trim()) { rec.message = '記録を貼り付けてください'; rec.ok = false; openRecord(); return; }
  }
  if (action === 'extract') {
    if (!rec.pick || !rec.pick.ref) { rec.message = '読み取る要素を一覧から選んでください'; rec.ok = false; openRecord(); return; }
    Object.assign(payload, { ref: rec.pick.ref, mode: rec.pick.mode, key: rec.pick.key });
  }
  rec.busy = true;
  rec.ok = true;
  rec.message = { start: '始めています…', snapshot: '画面を読み取っています…', extract: '読み取りを挿しています…' }[action] || '工程にしています…';
  openRecord();
  let res;
  try {
    res = action === 'start' ? await automationHost.recordingStart(payload)
      : action === 'stop' ? await automationHost.recordingStop(payload)
        : action === 'snapshot' ? await automationHost.recordingSnapshot(payload)
          : action === 'extract' ? await automationHost.recordingExtract(payload)
            : await automationHost.recordingImport(payload);
  } catch (err) { res = { error: String((err && err.message) || err) }; }
  rec.busy = false;
  if (!res || res.error) {
    rec.message = (res && res.error) || 'うまくいきませんでした';
    rec.ok = false;
    if (action === 'stop') { rec.active = false; rec.pick = null; rec.extracts = 0; }
    openRecord();
    return;
  }
  if (action === 'start') {
    rec.active = true;
    rec.pick = null;
    rec.extracts = 0;
    rec.message = '操作後に「終了して工程を作成」を押してください。';
    openRecord();
    return;
  }
  if (action === 'snapshot') {
    const candidates = res.candidates || [];
    const preferred = candidates.find((c) => ['article', 'main', 'table', 'list'].includes(c.role)) || candidates[0];
    rec.pick = { candidates, ref: preferred ? preferred.ref : '', mode: 'text', key: '' };
    rec.message = '';
    openRecord();
    return;
  }
  if (action === 'extract') {
    rec.extracts = res.extracts || rec.extracts + 1;
    rec.pick = null;
    rec.message = `「${res.op && (res.op.label || res.op.target)}」を読み取りにしました。続けて操作できます。`;
    openRecord();
    return;
  }
  if (action === 'stop') { rec.active = false; rec.pick = null; rec.extracts = 0; }
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
    if (open) open.addEventListener('click', () => guard('フォルダ', () => automationHost.openMachineFolder(state.root, state.current.machine)));
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
    await guard('中止', () => automationHost.aiStop(flow.requestId === 'pending' ? '' : flow.requestId));
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
    root: state.root, mode: flow.mode, agent: selectedAgent(flow.mode === 'review' ? state.editAgent || state.config.agent : state.config.agent), history: flow.history,
    ...(flow.mode === 'draft'
      ? { request: flow.request }
      : { spec: specPayload(), scope: flow.scope, focus: flow.focus }),
  };
  try {
    const started = await automationHost.aiStart(payload);
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
  if (flowFeature.onAiProgress(payload)) return;
  const flow = payload.mode === 'draft' ? state.aiDraft : payload.mode === 'review' ? state.aiReview
    : (state.aiDraft.requestId === payload.requestId ? state.aiDraft : state.aiReview);
  if (!flow.busy || (flow.requestId !== 'pending' && flow.requestId !== payload.requestId)) return;
  flow.requestId = payload.requestId;
  flow.message = payload.message || flow.message;
  const dlg = $(flow.mode === 'draft' ? 'dlg-ai-draft' : 'dlg-ai');
  if (dlg.open) (flow.mode === 'draft' ? openAiDraft : openAiReview)();
}

function receiveAiResult(payload) {
  if (flowFeature.onAiResult(payload)) return;
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
  const res = await guard('提案の反映', () => automationHost.aiApply({
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

async function startRun(mode) {
  const run = state.run;
  const machine = selectedExecutionMachine();
  if (!machine) return;
  const selected = taskRunExecution();
  const runAgent = selectedAgent(selected.agent);
  if (mode === 'run' && !runAgent) { toast('実行環境で使う AI を確認してください', true); return; }
  if (mode === 'run') {
    const defaults = machine.parameterDefaults || {};
    const missing = (machine.parameters || []).filter((name) => !String(state.run.parameters[name] || defaults[name] || '').trim());
    if (missing.length) { openRunInputDialog(machine, missing); return; }
    state.run.parameters = { ...defaults, ...state.run.parameters };
    await rememberRunParameters(machine, state.run.parameters);
  }
  if (run.running) return;
  run.lines = [];
  run.requestId = '';
  run.terminal = false;
  run.logOpen = false;
  run.screen = null;
  if (runTerm) runTerm.detach();
  run.result = null;
  run.error = '';
  run.running = true;
  render();
  const res = await guard('実行', () => automationHost.runStart({
    root: state.root, taskId: taskIdentity(machine), machine: machine.machine || '', mode,
    agent: runAgent, model: selected.model, parameters: run.parameters, autoApprove: true,
    skillMode: run.skillMode || (state.config.instructions && state.config.instructions.skillSelection && state.config.instructions.skillSelection.defaultMode) || 'auto',
    skills: run.skillMode === 'manual' ? run.skills : [],
  }));
  if (!res) { run.running = false; render(); return; }
  run.requestId = res.requestId || '';
  run.terminal = res.transport === 'tmux';
  render();
  if (res.skillSelection && Array.isArray(res.skillSelection.selected)) {
    run.skillPreview = res.skillSelection.selected;
  }
  for (const item of Array.isArray(res.executionInformation) ? res.executionInformation : []) {
    if (item.type === 'skill') appendLog({ kind: item.status === 'error' ? 'stderr' : 'stdout', line: `適用スキル: ${item.title}（${item.detail}）` });
  }
  if (res.warning) appendLog({ kind: 'stderr', line: res.warning });
}

function openRunInputDialog(machine, fields) {
  const dlg = dialog('dlg-run', '実行前の入力', 'record', `
    <p class="muted small">このタスクを始めるために必要な内容を入力してください。</p>
    <div class="run-input-grid">${fields.map((name) => `<div class="field"><label>${esc(name)}</label><input data-required-input="${esc(name)}"></div>`).join('')}</div>
    <p class="msg err" data-input-error hidden>すべて入力してください。</p>
    <div class="row"><button type="button" class="primary" data-input-run>入力して実行</button></div>`);
  dlg.querySelector('[data-input-run]').addEventListener('click', () => {
    for (const input of dlg.querySelectorAll('[data-required-input]')) state.run.parameters[input.dataset.requiredInput] = input.value.trim();
    if (fields.some((name) => !state.run.parameters[name])) { dlg.querySelector('[data-input-error]').hidden = false; return; }
    dlg.close();
    startRun('run');
  });
}

function appendLog(entry) {
  state.run.lines.push(entry);
  if (state.run.lines.length > 2000) state.run.lines.shift();
  const log = $('run-log');
  if (!log) return;
  const details = $('run-log-details');
  if (details) {
    details.hidden = false;
    if (entry.kind === 'stderr') details.querySelector('summary').textContent = '実行ログ（警告・エラーあり）';
  }
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
      <div class="field"><label>使う AI</label><select id="c-agent" ${state.agents.length ? '' : 'disabled'}>${agentOptions(agent)}</select></div>
      <div class="field"><label>モデル（任意）</label><input id="c-model" class="mono" value="${esc(cfg.model || '')}"></div>
    </div>
    <div class="field"><label>構成確認用スキルの場所（任意）</label><input id="c-skill" class="mono" value="${esc(cfg.skillDir || '')}" placeholder="通常は自動で検出します"></div>
    <div class="row"><button type="button" id="c-save" class="primary">保存</button><button type="button" id="tools-check">接続を確認</button></div>
    <div id="tools-list">${state.tools ? toolsHtml(state.tools) : ''}</div>`);
  dlg.querySelector('#c-save').addEventListener('click', async () => {
    const next = { ...cfg, agent: dlg.querySelector('#c-agent').value || cfg.agent, model: dlg.querySelector('#c-model').value.trim(), skillDir: dlg.querySelector('#c-skill').value.trim() };
    const saved = await guard('保存', () => automationHost.saveConfig(next));
    if (saved) { state.config = saved; toast('保存しました'); }
  });
  dlg.querySelector('#tools-check').addEventListener('click', async () => {
    const btn = dlg.querySelector('#tools-check');
    btn.disabled = true;
    btn.textContent = '確認中…';
    const res = await guard('確認', () => automationHost.toolStatus(state.root));
    state.tools = res || state.tools;
    const definitions = await guard('AI 一覧', () => automationHost.listAgents(state.root));
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
  // 任意の道具（無くても本体は動くもの）は未準備でも警告色にしない
  const badge = (t) => (t.ok ? ['ok', '使えます'] : t.optional ? ['opt', '任意'] : ['ng', '未準備']);
  return `<ul class="tool-list">${tools.map((t) => { const [cls, text] = badge(t); return `<li><span><span class="st ${cls}">${text}</span><strong>${esc(t.label)}</strong></span><small>${esc(t.summary || '')}</small>${t.hint ? `<small>${esc(t.hint)}</small>` : ''}</li>`; }).join('')}</ul>`;
}

// --- 起動 -----------------------------------------------------------------------------

let initPromise;
let navigationToken = 0;

async function navigateEmbedded(payload) {
  if (!embedded) return;
  const token = (navigationToken += 1);
  if (initPromise) await initPromise;
  if (token !== navigationToken) return;
  const area = payload.area === 'workflows' ? 'workflows' : 'tasks';
  const root = String(payload.root || '');

  state.view = 'home';
  state.current = null;
  if (root && root !== state.root) await selectRoot(root);
  if (token !== navigationToken) return;
  if (!root && state.root) {
    state.root = '';
    state.machines = [];
    state.execution.snapshot = null;
    flowFeature.rootChanged();
    teachingFeature.rootChanged();
  }

  // 設定の再読込は選択したタスクを描くためには不要。IPC の返答を待つあいだ前のタスク名を
  // 残さず、現在の選択を先に描く。連続して選んだ場合は古い返答を token で捨てる。
  guard('設定', () => automationHost.getConfig()).then((latestConfig) => {
    if (token !== navigationToken || !latestConfig) return;
    state.config = latestConfig;
    renderIfIdle();                    // 実行方針・モデルは設定から出すので、届いたら描き直す
    loadAgents();                      // 待たない（WSL 越しで遅い）。届いたら描き直す
  });

  if (area === 'workflows') {
    state.homeTab = 'flows';
    await flowFeature.activate();
    if (token !== navigationToken) return;
    if (payload.selected) await flowFeature.select(payload.selected);
    if (token !== navigationToken) return;
    if (payload.action === 'new') flowFeature.create();
    else render();
    return;
  }

  // 定義があるタスク（実行できるもの）は実行詳細から開く。AI との会話を開くのは、新しいタスク
  // （action: new。会話からの intent も親がここへ送る）・「AIに変更を相談」（action: teach）・
  // まだ定義の無い下書きを選んだときだけ。
  const machines = executionMachines();
  // 親の一覧は実行基盤が無いとき id を持たず machine 名だけで選ぶので、id と machine 名のどちらでも見つける。
  const wanted = String(payload.selected || '');
  const selectedTask = wanted ? machines.find((machine) => taskIdentity(machine) === wanted
    || taskIdentity(machine) === `machine:${wanted}` || (machine.machine && machine.machine === wanted.replace(/^machine:/, ''))) : null;
  // 「編集」（action: teach）は、定義があれば手順タブの編集面、無ければ作成の続きを開く。
  if (payload.action === 'teach' && payload.selected) {
    teachingFeature.cancelCreate();
    await teachingFeature.activate();
    if (token !== navigationToken) return;
    await openTeaching(payload.selected);
    return;
  }
  // 定義がまだ無いもの（新しいタスク・作成中の下書き）は、ホームタブの作成画面で開く。
  const teachesTask = payload.action === 'new'
    || (!selectedTask && (!!payload.selected || !machines.length));
  state.homeTab = teachesTask ? 'teach' : 'run';
  if (teachesTask) {
    await teachingFeature.activate();
    if (token !== navigationToken) return;
    if (payload.action === 'new') teachingFeature.create();
    else if (payload.selected) {
      await teachingFeature.select(String(payload.selected).replace(/^machine:/, ''));
      if (token !== navigationToken) return;
    }
    else render();
    return;
  }
  teachingFeature.cancelCreate();

  if (selectedTask) {
    const identity = taskIdentity(selectedTask);
    if (state.execution.selected !== identity) state.execution.detailTab = 'overview';
    state.execution.selected = identity;
  }
  if (payload.action === 'new') newMachine();
  else render();
}

// 親の会話（AI がファイルを書いた）が終わるたびに、定義と実行状態を読み直す。
async function refreshEmbedded() {
  if (!state.root) return;
  await loadMachines();
  renderIfIdle();
  refreshExecutionSnapshot();
}

if (workbenchHost) workbenchHost.setController({
  navigate: navigateEmbedded,
  refresh: refreshEmbedded,
  // 親のワークフロー会話が 1 ターン終わったあと、AI が書いた定義と下書きを読み直す
  reloadFlowTeaching: () => flowFeature.reloadTeaching(),
});

async function init() {
  state.catalog = (await guard('準備', () => automationHost.catalog())) || state.catalog;
  state.config = (await guard('設定', () => automationHost.getConfig())) || state.config;
  $('btn-home').addEventListener('click', goHome);
  automationHost.onRunLine((p) => appendLog(p));
  automationHost.onRunScreen((p) => {
    if (state.run.requestId && state.run.requestId !== p.requestId) return;
    state.run.screen = p;
    if (runTerm) runTerm.applyScreen(p);
  });
  automationHost.onAiProgress((p) => receiveAiProgress(p));
  automationHost.onAiResult((p) => receiveAiResult(p));
  automationHost.onRunExit(async (p) => {
    if (state.run.requestId && p.requestId && state.run.requestId !== p.requestId) return;
    state.run.running = false;
    state.run.result = p.result || { ok: p.code === 0 };
    state.run.error = p.error || '';
    appendLog({ kind: state.run.result.ok ? 'stdout' : 'stderr', line: state.run.result.ok ? (p.mode === 'check' ? '— 構成を確認しました' : '— 実行が完了しました') : '— 実行を完了できませんでした' });
    notifyHost('tasks', state.execution.selected);
    if (state.view === 'home' && state.homeTab === 'run') render();
    refreshExecutionSnapshot();
  });
  window.addEventListener('beforeunload', (e) => { if (state.current && state.current.dirty) { e.preventDefault(); e.returnValue = ''; } });
  state.root = state.config.lastRoot || (state.config.roots || [])[0] || '';
  // 手元のファイル（定義の一覧）だけを待って描く。AI の一覧と実行状態はホスト（Windows では WSL）に
  // 聞くので待たない——待つと、親が最初に「タスク」を開いたときにここの initPromise で止まる。
  if (state.root) await loadMachines();
  const first = executionMachines()[0];
  if (first && !state.execution.selected) state.execution.selected = taskIdentity(first);
  if (state.root && state.homeTab === 'teach') await teachingFeature.activate();
  render();
  loadAgents();
  if (state.root) refreshExecutionSnapshot();
}

initPromise = init();

})();
