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
    // 人の操作の記録（ブラウザは playwright-cli の記録を開始・終了、Windows アプリは貼り付け）。
    recording: { url: '', app: '', source: 'browser', text: '', active: false, busy: false, message: '', ok: true },
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
  return { kind, title: '', detail: '', target: '', check: '', outcomesText: '', recorded: [] };
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
    recorded: Array.isArray(step.recorded) ? step.recorded : [],
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
      recorded: Array.isArray(step.recorded) ? step.recorded : [],
    })),
  };
}

// 記録から起こした工程（main の raw 形）を下書きの工程へ。記録（recorded）はそのまま持ち回る。
function routineStepFromRecorded(step) {
  return {
    ...emptyRoutineStep(String(step.kind || '')),
    title: String(step.title || ''),
    detail: String(step.detail || ''),
    target: String(step.target || ''),
    check: String(step.check || ''),
    outcomesText: routineOutcomesText(step.outcomes),
    recorded: Array.isArray(step.recorded) ? step.recorded : [],
  };
}

// 記録した操作の 1 行表示（工程カード）。指示文の綴りは main が組むので、ここは人が読む形だけ。
function routineRecordedLine(op) {
  const label = op.label || op.target || '';
  const value = op.value ? ` ${op.value}` : '';
  return `${op.op} ${label}${value}`;
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
  const recordedHtml = Array.isArray(step.recorded) && step.recorded.length
    ? `<details class="routine-procedure-recorded">
      <summary>記録した操作 ${step.recorded.length} 件（作成モードの AI が待機・確認を補って再現します）</summary>
      <ol class="mono">${step.recorded.map((op) => `<li>${esc(routineRecordedLine(op))}</li>`).join('')}</ol>
      <button type="button" data-rp-unrecord>記録を外す（内容の文章だけ残す）</button>
    </details>` : '';
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
    ${recordedHtml}
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
    : '<div class="empty compact">工程がありません。上のボタンから追加するか、下の「操作を記録する」で人の操作から起こしてください。</div>'}
    </section>
    ${routineRecordingHtml(draft)}
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

// 人の操作を記録して工程に起こす。ブラウザは playwright-cli の記録（開始 → 人が操作 → 終了）を
// main が呼び、Windows アプリは winauto の操作イベント（JSONL）の貼り付けを受ける。どちらも
// 返ってくるのは工程列で、上の一覧に足すだけ（作成・保存の経路には触れない）。
function routineRecordingHtml(draft) {
  const rec = draft.recording;
  const kinds = routineProcedureCatalog().filter((k) => k.recordable);
  if (!kinds.length || !api.coworkProcedureRecording) return '';
  const canRecordBrowser = kinds.some((k) => k.id === 'browser');
  const sourceOptions = kinds.map((k) => `<option value="${esc(k.id)}" ${rec.source === k.id ? 'selected' : ''}>${esc(k.label)}</option>`).join('');
  return `<details class="routine-procedure-recording" ${rec.active || rec.text || rec.message ? 'open' : ''}>
    <summary>操作を記録する（人がやって見せた操作を工程に起こす）</summary>
    <p class="muted">要素は名前と種類で残し、入力した値は <code>{{key}}</code> の入力パラメータに置き換えます。待機・確認・分岐は作成モードの AI が補います。</p>
    ${canRecordBrowser ? `<div class="row2">
      <div class="field">
        <label for="rp-rec-url">ブラウザで記録する URL</label>
        <input id="rp-rec-url" class="mono" data-rp-rec="url" value="${esc(rec.url)}" placeholder="https://…（記録を開始すると見える形でブラウザが開きます）" ${rec.active ? 'disabled' : ''}>
      </div>
      <div class="field routine-procedure-recording-actions">
        <label>&nbsp;</label>
        <div class="row">
          <button type="button" id="btn-rp-rec-start" ${rec.active || rec.busy ? 'disabled' : ''}>記録を開始</button>
          <button type="button" id="btn-rp-rec-stop" class="primary-inline" ${!rec.active || rec.busy ? 'disabled' : ''}>記録を終了して工程に起こす</button>
        </div>
      </div>
    </div>` : ''}
    <div class="row2">
      <div class="field">
        <label for="rp-rec-source">貼り付ける記録の種類</label>
        <select id="rp-rec-source" data-rp-rec="source">${sourceOptions}</select>
      </div>
      <div class="field">
        <label for="rp-rec-app">${rec.source === 'windows' ? 'アプリ名' : 'URL（任意）'}</label>
        <input id="rp-rec-app" data-rp-rec="app" value="${esc(rec.app)}" placeholder="${rec.source === 'windows' ? '例: 勤怠管理' : 'https://…'}">
      </div>
    </div>
    <div class="field">
      <label for="rp-rec-text">記録の貼り付け</label>
      <small class="muted">${rec.source === 'windows'
    ? `対象の PC で <code>winauto record --app ${esc(rec.app || '<アプリ>')} --output events.jsonl</code> を実行し、操作してから <code>Ctrl+C</code> で止めて、できたファイルの中身を貼り付けます。`
    : '別の端末で取った <code>playwright-cli recording-stop</code> の出力も貼り付けられます。'}</small>
      <textarea id="rp-rec-text" class="mono" data-rp-rec="text" rows="4" placeholder="${rec.source === 'windows'
    ? '例: {&quot;event&quot;:&quot;invoke&quot;,&quot;app&quot;:&quot;勤怠管理&quot;,&quot;window&quot;:&quot;月次集計&quot;,&quot;control_type&quot;:&quot;Button&quot;,&quot;name&quot;:&quot;出力&quot;,&quot;auto_id&quot;:&quot;btnExport&quot;}'
    : '例: playwright-cli recording-stop の出力（await page.getByRole(…).click(); の行）'}">${esc(rec.text)}</textarea>
      <div class="row"><button type="button" id="btn-rp-rec-import" ${rec.busy ? 'disabled' : ''}>貼り付けた記録を工程に起こす</button></div>
    </div>
    <p id="rp-rec-message" class="${rec.ok ? 'muted' : 'cowork-item-error'}" ${rec.message ? '' : 'hidden'}>${esc(rec.message)}</p>
  </details>`;
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
    if (el.dataset.rpRec) {
      draft.recording[el.dataset.rpRec] = el.value;
      return;
    }
    const card = el.closest('[data-rp-step]');
    if (card && el.dataset.rpField) {
      const step = draft.steps[Number(card.dataset.rpStep)];
      if (step) step[el.dataset.rpField] = el.value;
    }
  };
  const source = body.querySelector('#rp-rec-source');
  if (source) source.addEventListener('change', () => { draft.recording.source = source.value; renderRoutineProcedureBody(); });
  for (const btn of body.querySelectorAll('[data-rp-unrecord]')) {
    btn.addEventListener('click', () => {
      const step = draft.steps[Number(btn.closest('[data-rp-step]').dataset.rpStep)];
      if (step) step.recorded = [];
      draft.preview = null;
      renderRoutineProcedureBody();
    });
  }
  const recStart = body.querySelector('#btn-rp-rec-start');
  if (recStart) recStart.addEventListener('click', () => routineRecording('start'));
  const recStop = body.querySelector('#btn-rp-rec-stop');
  if (recStop) recStop.addEventListener('click', () => routineRecording('stop'));
  const recImport = body.querySelector('#btn-rp-rec-import');
  if (recImport) recImport.addEventListener('click', () => routineRecording('import'));
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

// 記録の開始・終了・貼り付け。返った工程列は一覧の末尾に足す（既存の工程は触らない）。
async function routineRecording(action) {
  const draft = routineProcedureDraft();
  const rec = draft.recording;
  if (rec.busy || !api.coworkProcedureRecording) return;
  const payload = action === 'import'
    ? { action, repo: draft.repo, source: rec.source, text: rec.text,
      url: rec.source === 'browser' ? rec.app : '', app: rec.source === 'windows' ? rec.app : '' }
    : { action, repo: draft.repo, url: rec.url };
  if (action === 'import' && !String(rec.text || '').trim()) {
    rec.message = '記録を貼り付けてください';
    rec.ok = false;
    renderRoutineProcedureBody();
    return;
  }
  rec.busy = true;
  rec.message = action === 'start' ? 'ブラウザを開いています…' : action === 'stop' ? '記録を工程に起こしています…' : '読み取っています…';
  rec.ok = true;
  renderRoutineProcedureBody();
  let res;
  try {
    res = await api.coworkProcedureRecording(payload);
  } catch (err) {
    res = { error: String((err && err.message) || err) };
  }
  rec.busy = false;
  if (!res || res.error) {
    rec.message = `${action === 'start' ? '記録を開始できませんでした' : '記録を工程に起こせませんでした'}: ${(res && res.error) || '原因不明'}`;
    rec.ok = false;
    if (action === 'stop') rec.active = false;
    renderRoutineProcedureBody();
    return;
  }
  if (action === 'start') {
    rec.active = true;
    rec.message = '開いたブラウザで操作してください。終わったら「記録を終了して工程に起こす」を押します';
    rec.ok = true;
    renderRoutineProcedureBody();
    return;
  }
  rec.active = false;
  if (action === 'import') rec.text = '';
  const steps = Array.isArray(res.steps) ? res.steps : [];
  for (const step of steps) draft.steps.push(routineStepFromRecorded(step));
  draft.preview = null;
  const params = Array.isArray(res.parameters) && res.parameters.length
    ? `。入力パラメータの候補: ${res.parameters.map((k) => `{{${k}}}`).join(' ')}（名前は工程の内容欄で直せます）` : '';
  rec.message = `${res.operations || 0} 件の操作から ${steps.length} 工程を起こしました${params}`;
  rec.ok = true;
  renderRoutineProcedureBody();
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
