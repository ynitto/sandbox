'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// 手順ビルダー（定型手順）: 画面操作（ブラウザ / Windows アプリ）・スキルへの移譲・
// コマンド実行・AI の処理（生成・判断）を並べて、statemachine-use の作成モードへ渡す
// 指示文を組む。
//
// 画面は工程列（procedure）を持ち回るだけで、工程の種類（入力欄・表示名）は main の
// 種別カタログ（`cowork:procedureCatalog`）から受け取り、指示文の組み立てと検査は main
// （features/cowork/main/procedure.js）の 1 実装に任せる——種類を足すのは main の 1 か所。
// 作成の起動は自由文と同じ `cowork:generateStateMachine`（payload.procedure）を通し、
// 入口を増やさない。組んだ工程列は作業項目（procedure）に残るので、作り直しは同じ画面から
// 始められる。
//
// このファイルが cowork 側に頼るのは、作業項目の下書き（coworkDraft）・選択中フォルダ
// （selectedProjectFolder）・描き直し（renderCowork / updateCoworkTabVisibility）だけ。
// ---------------------------------------------------------------------------

function routineProcedureCatalog() {
  return (state.procedureCatalog && Array.isArray(state.procedureCatalog.kinds)) ? state.procedureCatalog.kinds : [];
}

// 種別が取れない（古い項目・カタログ未取得）ときも描けるよう、最低限の形で代える。
function routineStepKind(id) {
  return routineProcedureCatalog().find((k) => k.id === id)
    || { id, label: String(id || ''), target: null, detail: { label: '内容', required: true, placeholder: '' }, check: null };
}

async function loadRoutineProcedureCatalog() {
  if (state.procedureCatalog || !api.coworkProcedureCatalog) return;
  const res = await guard('工程の種類の取得', () => api.coworkProcedureCatalog());
  if (res && Array.isArray(res.kinds)) state.procedureCatalog = res;
}

function emptyRoutineProcedure(repo, index = -1) {
  return {
    index, repo, name: '', machine: '', machineTouched: false,
    purpose: '', steps: [], finish: '', notes: '',
    preview: null, tools: null, message: '', ok: true, busy: false,
  };
}

function routineProcedureDraft() {
  if (!state.routineProcedure) state.routineProcedure = emptyRoutineProcedure('');
  return state.routineProcedure;
}

function routineMachineId(name) {
  return String(name || '').trim().toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, '-').replace(/^[-.]+|[-.]+$/g, '').slice(0, 60);
}

// 判断欄の 1 行 = 「ラベル: 行き先」。行き先を省いた行は次の工程へ進む。
function parseRoutineOutcomes(textValue) {
  return String(textValue || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const m = /^(.+?)\s*(?::|=|→|->)\s*(\S+)\s*$/.exec(line);
    return m ? { label: m[1].trim(), to: m[2].trim() } : { label: line, to: 'next' };
  });
}

function routineOutcomesText(outcomes) {
  return (Array.isArray(outcomes) ? outcomes : []).map((o) => `${o.label}: ${o.to}`).join('\n');
}

function emptyRoutineStep(kind) {
  return { kind, title: '', detail: '', target: '', check: '', outcomesText: '' };
}

// 作業項目に残した工程列から編集用の下書きを起こす。
function routineProcedureFromItem(item, index) {
  const draft = emptyRoutineProcedure(item.repo || selectedProjectFolder(), index);
  const src = item.procedure || {};
  draft.name = String(item.name || '');
  draft.machine = String(item.workflow || item.file || routineMachineId(item.name) || '');
  draft.machineTouched = true;
  draft.purpose = String(src.purpose || '');
  draft.finish = String(src.finish || '');
  draft.notes = String(src.notes || '');
  draft.steps = (Array.isArray(src.steps) ? src.steps : []).map((step) => ({
    ...emptyRoutineStep(String(step.kind || '')),
    title: String(step.title || ''),
    detail: String(step.detail || ''),
    target: String(step.target || ''),
    check: String(step.check || ''),
    outcomesText: routineOutcomesText(step.outcomes),
  }));
  return draft;
}

// main へ渡す工程列。検査（分岐先・シェル記号・必須）は main が行う。
function routineProcedurePayload(draft) {
  return {
    version: (state.procedureCatalog && state.procedureCatalog.version) || undefined,
    purpose: draft.purpose,
    finish: draft.finish,
    notes: draft.notes,
    steps: draft.steps.map((step) => ({
      kind: step.kind,
      title: step.title,
      detail: step.detail,
      target: step.target,
      check: step.check,
      outcomes: parseRoutineOutcomes(step.outcomesText),
    })),
  };
}

function routineProcedureStepHtml(step, index, count) {
  const kind = routineStepKind(step.kind);
  const id = (field) => `rp-step-${index}-${field}`;
  const targetHtml = kind.target ? `<div class="field">
        <label for="${id('target')}">${esc(kind.target.label)}${kind.target.required ? '' : '（任意）'}</label>
        <input id="${id('target')}" data-rp-field="target" class="mono" value="${esc(step.target)}" placeholder="${esc(kind.target.placeholder || '')}">
      </div>` : '';
  const checkHtml = kind.check ? `<div class="field">
      <label for="${id('check')}">完了の確認コマンド（任意）</label>
      <input id="${id('check')}" data-rp-field="check" class="mono" value="${esc(step.check)}" placeholder="${esc(kind.check.placeholder || '')}">
      <small class="muted">終了コード 0 で通過します。パイプやリダイレクトは使えません。</small>
    </div>` : '';
  return `<li class="routine-procedure-step" data-rp-step="${index}">
    <div class="routine-procedure-step-head">
      <div class="row"><span class="label-chip">工程 ${index + 1}</span><strong>${esc(kind.label)}</strong></div>
      <div class="row">
        <button type="button" data-rp-move="up" ${index === 0 ? 'disabled' : ''} aria-label="工程 ${index + 1} を上へ">上へ</button>
        <button type="button" data-rp-move="down" ${index === count - 1 ? 'disabled' : ''} aria-label="工程 ${index + 1} を下へ">下へ</button>
        <button type="button" data-rp-remove aria-label="工程 ${index + 1} を削除">削除</button>
      </div>
    </div>
    <div class="row2">
      <div class="field">
        <label for="${id('title')}">名前（任意）</label>
        <input id="${id('title')}" data-rp-field="title" value="${esc(step.title)}" placeholder="例: 申請一覧を開く">
      </div>
      ${targetHtml}
    </div>
    <div class="field">
      <label for="${id('detail')}">${esc(kind.detail.label)}${kind.detail.required ? '' : '（任意）'}</label>
      <textarea id="${id('detail')}" data-rp-field="detail" rows="3" placeholder="${esc(kind.detail.placeholder || '')}">${esc(step.detail)}</textarea>
    </div>
    ${checkHtml}
    <div class="field">
      <label for="${id('outcomes')}">判断（任意・1 行に 1 つ）</label>
      <textarea id="${id('outcomes')}" data-rp-field="outcomesText" rows="2" class="mono" placeholder="APPROVED: next&#10;REJECTED: step:1&#10;ERROR: abort">${esc(step.outcomesText)}</textarea>
      <small class="muted">「ラベル: 行き先」。行き先は next（次の工程）/ step:番号 / done（完了）/ abort（失敗）。空欄なら OK / FAILED で次へ進みます。</small>
    </div>
  </li>`;
}

function routineProcedureToolsHtml(tools) {
  if (!tools) return '';
  if (!tools.length) return '<p class="muted">この手順に診断できる道具を使う工程はありません。</p>';
  return `<ul class="routine-procedure-tools">${tools.map((t) => `<li class="${t.ok ? 'is-ok' : 'is-ng'}">
    <span class="status-chip ${t.ok ? 'st-done' : 'st-failed'}">${t.ok ? '利用可能' : '未準備'}</span>
    <strong>${esc(t.label)}</strong><span class="muted">${esc(t.summary || '')}</span>${t.hint ? `<small class="muted">${esc(t.hint)}</small>` : ''}
  </li>`).join('')}</ul>`;
}

function routineProcedureHtml() {
  const draft = routineProcedureDraft();
  const kinds = routineProcedureCatalog();
  const parameters = draft.preview ? draft.preview.parameters : null;
  const addButtons = kinds.map((kind) =>
    `<button type="button" data-rp-add="${esc(kind.id)}" title="${esc(kind.description || '')}">＋ ${esc(kind.label)}</button>`).join('');
  return `<section class="routine-procedure" aria-labelledby="routine-procedure-dialog-title">
    <p class="muted">画面操作（ブラウザ / Windows アプリ）・スキル・コマンド・AI の処理を順に並べます。定義は外部ターミナルのエージェントが作り、作成後はこの一覧に現れます。</p>
    <div class="row2">
      <div class="field">
        <label for="rp-name">名前</label>
        <input id="rp-name" data-rp-top="name" value="${esc(draft.name)}" placeholder="例: 月次の勤怠集計">
      </div>
      <div class="field">
        <label for="rp-machine">識別名</label>
        <input id="rp-machine" data-rp-top="machine" class="mono" value="${esc(draft.machine)}" placeholder="英小文字・数字・ハイフン">
        <small class="muted">定義を置くフォルダ名になります。</small>
      </div>
    </div>
    <div class="field">
      <label for="rp-purpose">目的</label>
      <textarea id="rp-purpose" data-rp-top="purpose" rows="2" placeholder="例: 毎月 1 日に勤怠システムから月次集計を出力し、異常値があれば差し戻し候補を一覧にする">${esc(draft.purpose)}</textarea>
    </div>
    <section class="routine-procedure-steps" aria-label="工程">
      <div class="cowork-section-heading"><h3>工程</h3></div>
      ${kinds.length ? `<div class="row routine-procedure-add">${addButtons}</div>`
    : '<p class="cowork-item-error">工程の種類を取得できませんでした。ダイアログを開き直してください。</p>'}
      ${draft.steps.length
    ? `<ol id="rp-steps" class="routine-procedure-list">${draft.steps.map((s, i) => routineProcedureStepHtml(s, i, draft.steps.length)).join('')}</ol>`
    : '<div class="empty compact">工程がありません。上のボタンから追加してください。</div>'}
    </section>
    <div class="row2">
      <div class="field">
        <label for="rp-finish">終了条件（任意）</label>
        <textarea id="rp-finish" data-rp-top="finish" rows="2" placeholder="例: 集計ファイルが出力され、差し戻し候補の一覧が出来たら完了">${esc(draft.finish)}</textarea>
      </div>
      <div class="field">
        <label for="rp-notes">注意事項（任意）</label>
        <textarea id="rp-notes" data-rp-top="notes" rows="2" placeholder="例: 申請の承認・却下は押さない（人が行う）">${esc(draft.notes)}</textarea>
      </div>
    </div>
    <div class="routine-procedure-meta">
      <div><span class="muted">入力パラメータ</span> ${parameters
    ? (parameters.length ? parameters.map((k) => `<code>{{${esc(k)}}}</code>`).join(' ') : '<span class="muted">なし</span>')
    : '<span class="muted">「指示文を確認」で検出します（本文の {{key}} が実行時の入力になります）</span>'}</div>
      <div class="row"><button type="button" id="btn-rp-tools" ${api.coworkProcedureTools ? '' : 'disabled'}>道具を確認</button>
        <span class="muted">画面操作に使う CLI がこの端末から呼べるかを診断します。</span></div>
      ${routineProcedureToolsHtml(draft.tools)}
    </div>
    ${draft.preview ? `<details class="routine-procedure-preview" open>
      <summary>作成モードへ渡す指示文</summary>
      <pre>${esc(draft.preview.instruction)}</pre>
    </details>` : ''}
    <p id="rp-message" class="${draft.ok ? 'muted' : 'cowork-item-error'}" ${draft.message ? '' : 'hidden'}>${esc(draft.message)}</p>
  </section>`;
}

function renderRoutineProcedureBody() {
  const body = $('routine-procedure-dialog-body');
  if (!body) return;
  body.innerHTML = routineProcedureHtml();
  bindRoutineProcedureBody(body);
}

function bindRoutineProcedureBody(body) {
  const draft = routineProcedureDraft();
  // 入力は下書きへ直接書く（描き直さない）。構造が変わる操作だけ描き直す。
  // 容れ物（body）は描き直しても同じ要素なので、addEventListener で積まずに差し替える。
  body.oninput = (event) => {
    const el = event.target;
    if (!el || typeof el.value !== 'string') return;
    if (el.dataset.rpTop) {
      draft[el.dataset.rpTop] = el.value;
      if (el.dataset.rpTop === 'machine') draft.machineTouched = true;
      if (el.dataset.rpTop === 'name' && !draft.machineTouched) {
        draft.machine = routineMachineId(el.value);
        const machine = body.querySelector('#rp-machine');
        if (machine) machine.value = draft.machine;
      }
      return;
    }
    const card = el.closest('[data-rp-step]');
    if (card && el.dataset.rpField) {
      const step = draft.steps[Number(card.dataset.rpStep)];
      if (step) step[el.dataset.rpField] = el.value;
    }
  };
  for (const btn of body.querySelectorAll('[data-rp-add]')) {
    btn.addEventListener('click', () => {
      draft.steps.push(emptyRoutineStep(btn.dataset.rpAdd));
      draft.preview = null;
      renderRoutineProcedureBody();
      const last = $('routine-procedure-dialog-body').querySelector(`[data-rp-step="${draft.steps.length - 1}"] input`);
      if (last) last.focus();
    });
  }
  for (const btn of body.querySelectorAll('[data-rp-move]')) {
    btn.addEventListener('click', () => {
      const index = Number(btn.closest('[data-rp-step]').dataset.rpStep);
      const to = btn.dataset.rpMove === 'up' ? index - 1 : index + 1;
      if (to < 0 || to >= draft.steps.length) return;
      const [moved] = draft.steps.splice(index, 1);
      draft.steps.splice(to, 0, moved);
      draft.preview = null;
      renderRoutineProcedureBody();
    });
  }
  for (const btn of body.querySelectorAll('[data-rp-remove]')) {
    btn.addEventListener('click', () => {
      draft.steps.splice(Number(btn.closest('[data-rp-step]').dataset.rpStep), 1);
      draft.preview = null;
      renderRoutineProcedureBody();
    });
  }
  const tools = body.querySelector('#btn-rp-tools');
  if (tools) tools.addEventListener('click', () => checkRoutineProcedureTools());
}

function setRoutineProcedureMessage(message, ok) {
  const draft = routineProcedureDraft();
  draft.message = message;
  draft.ok = ok;
  const el = $('rp-message');
  if (!el) return;
  el.textContent = message;
  el.hidden = !message;
  el.className = ok ? 'muted' : 'cowork-item-error';
}

async function checkRoutineProcedureTools() {
  const draft = routineProcedureDraft();
  if (!api.coworkProcedureTools) return;
  const kinds = [...new Set(draft.steps.map((s) => s.kind))];
  const btn = $('btn-rp-tools');
  if (btn) { btn.disabled = true; btn.textContent = '確認中…'; }
  const res = await guard('道具の確認', () => api.coworkProcedureTools({ repo: draft.repo, kinds }));
  draft.tools = Array.isArray(res) ? res : (draft.tools || []);
  renderRoutineProcedureBody();
}

async function previewRoutineProcedure() {
  const draft = routineProcedureDraft();
  if (!api.coworkProcedurePreview) return null;
  draft.preview = null;
  try {
    const res = await api.coworkProcedurePreview({
      name: draft.name, machine: draft.machine, procedure: routineProcedurePayload(draft),
    });
    draft.preview = res;
    draft.message = '';
    draft.ok = true;
  } catch (err) {
    draft.message = String((err && err.message) || err);
    draft.ok = false;
  }
  renderRoutineProcedureBody();
  return draft.preview;
}

// 起動が通ったあとに作業項目へ残す形。発見項目（実体ファイルが正）は名前と工程列だけ差し替え、
// 手動項目として二重登録しない。
function routineProcedureItem(existing, { name, machine, repo, instruction, procedure }) {
  if (existing && existing.source === 'discovered') return { ...existing, name, instruction, procedure };
  return {
    ...(existing || {}),
    id: (existing && existing.id) || machine,
    type: 'state-machine',
    name,
    repo,
    schedule: (existing && existing.schedule) || '',
    workflow: machine,
    instruction,
    procedure,
    prompt: (existing && existing.prompt) || '',
    managed: true,
    source: 'config',
  };
}

// 作成を開始する。起動は自由文の手順付き作業と同じ経路で、成功したら作業項目へ工程列を残す
// （設定変更ダイアログの「定型業務の手順」には生成した指示文が映る）。反映は「変更を保存」。
async function createRoutineProcedure() {
  const draft = routineProcedureDraft();
  if (draft.busy) return;
  const name = String(draft.name || '').trim();
  if (!name) { setRoutineProcedureMessage('名前を入力してください', false); $('rp-name')?.focus(); return; }
  const machine = routineMachineId(draft.machine || name) || `routine-${Date.now()}`;
  if (!draft.steps.length) { setRoutineProcedureMessage('工程を 1 つ以上追加してください', false); return; }
  draft.busy = true;
  const create = $('btn-rp-create');
  if (create) { create.disabled = true; create.textContent = '起動中…'; }
  let launched;
  try {
    launched = await api.coworkGenerateStateMachine({
      repo: draft.repo, name, machine, procedure: routineProcedurePayload(draft),
    });
  } catch (err) {
    launched = { ok: false, error: String((err && err.message) || err) };
  }
  draft.busy = false;
  if (create) { create.disabled = false; create.textContent = '作成を開始'; }
  if (!launched || !launched.ok) {
    setRoutineProcedureMessage(`定型業務の作成を開始できませんでした: ${(launched && launched.error) || '原因不明'}`, false);
    return;
  }
  const existing = draft.index >= 0 ? coworkDraft()[draft.index] : null;
  const item = routineProcedureItem(existing, {
    name,
    machine,
    repo: draft.repo,
    instruction: String(launched.instruction || ''),
    procedure: launched.procedure || routineProcedurePayload(draft),
  });
  if (draft.index >= 0) coworkDraft()[draft.index] = item;
  else coworkDraft().push(item);
  state.routineProcedure = null;
  const dialog = $('dlg-routine-procedure');
  if (dialog && dialog.open) dialog.close();
  toast('外部ターミナルで定型業務の作成を開始しました。作成が終わったら「変更を保存」で予定を登録できます', true);
  updateCoworkTabVisibility();
  renderCowork();
}

async function openRoutineProcedureDialog(index = -1) {
  const dialog = $('dlg-routine-procedure');
  if (!dialog) return;
  const editing = index >= 0 ? coworkDraft()[index] : null;
  const repo = (editing && editing.repo) || selectedProjectFolder();
  if (!repo) {
    toast('プロジェクトを選択してください');
    return;
  }
  await loadRoutineProcedureCatalog();
  state.routineProcedure = editing ? routineProcedureFromItem(editing, index) : emptyRoutineProcedure(repo, -1);
  $('routine-procedure-dialog-title').textContent = editing ? '手順を組み立て直す' : '手順を組み立てる';
  renderRoutineProcedureBody();
  const close = () => { state.routineProcedure = null; if (dialog.open) dialog.close(); };
  $('btn-rp-cancel').onclick = close;
  $('btn-rp-preview').onclick = () => previewRoutineProcedure();
  $('btn-rp-create').onclick = () => createRoutineProcedure();
  dialog.oncancel = (event) => { event.preventDefault(); close(); };
  if (!dialog.open) dialog.showModal();
  const first = dialog.querySelector('#rp-name');
  if (first) first.focus();
}
