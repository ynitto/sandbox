'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// エージェント管理タブ。この端末全体の設定で、プロジェクト選択に依存しない。
// 色だけに頼らず状態語を併記する（既存 UI 方針を踏襲）。
// ---------------------------------------------------------------------------

async function refreshOrchestration() {
  if (!api.orchestrationOverview) return;
  try {
    state.orchestration = await api.orchestrationOverview();
    // 共通指示のスキル候補。取得に失敗しても画面表示は続ける。
    if (api.orchestrationSkillsInventory) {
      try { state.orchSkillsInventory = await api.orchestrationSkillsInventory(); }
      catch { state.orchSkillsInventory = []; }
    }
  } catch (err) {
    state.orchestration = { error: err.message };
  }
}

// AI 利用量を読みやすく（1.20M / 340k / 512）。
function orchTokens(n) {
  const v = Number(n || 0);
  if (v >= 1e6) return `${(v / 1e6).toFixed(2).replace(/\.?0+$/, '')}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1).replace(/\.0$/, '')}k`;
  return String(Math.round(v));
}

function orchLifecycleLabel(lc) {
  return { run: '稼働', pause: '一時停止', stop: '停止' }[lc] || lc || '未指定';
}

// タスクの実行に必要なワークロード。どちらかが止まっていれば承認も再実行も着手前に弾かれる
// （project=agent-project のループ / flow=agent-flow のノード実行）。
const ORCH_BLOCKING_WORKLOADS = ['project', 'flow'];

function orchOnExhaustedLabel(v) {
  return { pause: '一時停止', stop: '停止', degrade: '縮退' }[v] || v;
}

// この端末の実行制御（control.json）で止まっているワークロード。
// pause / stop の間は、承認も再実行も「送れるが実行されない」——本体は着手前に
// [agent-control] で弾き、その失敗が誤分類されて「利用上限」等の別の理由として
// 画面に出ることがあった。人は上限が空くのを待ち、実際に必要な操作（稼働に戻す）へ
// 辿り着けない。押す前にここで見えるようにして、その往復を断つ。
function orchBlockedWorkloads() {
  const ov = state.orchestration;
  const workloads = (ov && ov.control && ov.control.workloads) || {};
  const blocked = [];
  for (const name of ORCH_BLOCKING_WORKLOADS) {
    const lifecycle = String((workloads[name] || {}).lifecycle || 'run');
    if (lifecycle === 'pause' || lifecycle === 'stop') blocked.push({ workload: name, lifecycle });
  }
  return blocked;
}

// 実行が止まっているときの警告。止まっていなければ空文字（通常時は何も足さない）。
function orchBlockedBannerHtml() {
  const blocked = orchBlockedWorkloads();
  if (!blocked.length) return '';
  const detail = blocked
    .map((b) => `${b.workload} = ${orchLifecycleLabel(b.lifecycle)}`)
    .join(' / ');
  return `<div class="need-blocker" role="status">
    <strong>⏸ 実行が管理面で止まっています（${esc(detail)}）</strong>
    <span>このまま承認・再実行を送っても、着手前に止められて同じ要対応に戻ります。</span>
    <button type="button" class="primary-inline" data-orch-open="1">エージェント管理を開いて稼働に戻す</button>
  </div>`;
}

// ブロッカー警告内の導線。タブ切替は .tab のクリックハンドラが正（二重実装しない）。
function bindOrchBlockedBanner(root) {
  for (const btn of (root || document).querySelectorAll('[data-orch-open]')) {
    btn.addEventListener('click', () => {
      const tab = document.querySelector('.tab[data-tab="orchestration"]');
      if (tab) tab.click();
    });
  }
}

// 状態バッジ（色 + 状態語）。kind は ok / soft / over / muted。
function orchBadge(kind, text) {
  return `<span class="orch-badge orch-badge-${esc(kind)}">${esc(text)}</span>`;
}

// 1. 利用量（実測/推定の内訳 + 時間消費 + 機能別バー）
function orchBudgetPanelHtml(budget) {
  if (!budget) return '<section class="orch-panel"><p class="muted">利用量の情報がありません。</p></section>';
  const cfg = budget.config || {};
  const periodLabel = { day: '今日', month: '今月', total: '累計' }[cfg.period] || cfg.period;
  const tt = budget.totalTokens || { measured: 0, estimated: 0, total: 0 };
  const limit = Number(budget.tokenLimit || 0);
  const limitTxt = limit > 0 ? `${orchTokens(limit)} トークン` : '無制限';
  // 全体トークンゲージ（実測 + 推定の積み上げ）
  const denom = limit > 0 ? limit : Math.max(tt.total, 1);
  const mPct = Math.min(100, (tt.measured / denom) * 100);
  const ePct = Math.min(100 - mPct, (tt.estimated / denom) * 100);
  const timeTxt = cfg.execution_minutes > 0
    ? `${amigosMin(budget.totalSeconds)} / ${amigosMin(budget.limitSeconds)} 分`
    : `${amigosMin(budget.totalSeconds)} 分（上限なし）`;
  const overBadge = budget.exceeded
    ? orchBadge('over', '上限到達 — 新しい実行を制限中')
    : orchBadge('ok', '余裕あり');

  const wlRows = (budget.knownWorkloads || [])
    .concat(Object.keys(budget.workloads || {}).filter((w) => !(budget.knownWorkloads || []).includes(w)))
    .map((wl) => {
      const w = (budget.workloads || {})[wl] || { measuredTokens: 0, estimatedTokens: 0, totalTokens: 0, tokenCap: 0 };
      const cap = Number(w.tokenCap || 0);
      const capTxt = cap > 0 ? `${orchTokens(cap)} トークン` : '無制限';
      const d = cap > 0 ? cap : Math.max(w.totalTokens, 1);
      const mp = Math.min(100, (w.measuredTokens / d) * 100);
      const ep = Math.min(100 - mp, (w.estimatedTokens / d) * 100);
      let badge = '';
      if (w.tokenExceeded) badge = orchBadge('over', '上限に到達');
      else if (w.soft) badge = orchBadge('soft', '節約モード');
      else if (w.timeExceeded) badge = orchBadge('over', '時間上限に到達');
      return `<tr>
        <td>${esc(amigosWorkloadLabel(wl))}</td>
        <td class="orch-bar-cell">
          <div class="orch-bar" title="実測 ${esc(orchTokens(w.measuredTokens))} / 推定 ${esc(orchTokens(w.estimatedTokens))}">
            <span class="orch-bar-measured" style="width:${mp.toFixed(1)}%"></span>
            <span class="orch-bar-estimated" style="width:${ep.toFixed(1)}%"></span>
          </div>
        </td>
        <td class="num mono">${esc(orchTokens(w.totalTokens))} / ${esc(capTxt)}</td>
        <td>${badge}</td>
      </tr>`;
    }).join('');

  return `<section class="orch-panel">
    <header class="row">
      <div>
        <span class="summary-kicker">利用状況</span>
        <h3>AI利用量（${esc(periodLabel)}）</h3>
        <p class="muted">すべての機能で使った量を、取得できた値と推定値に分けて表示します。</p>
      </div>
      <div>${overBadge}</div>
    </header>
    <div class="orch-gauge">
      <div class="orch-gauge-head">
        <strong>合計 ${esc(orchTokens(tt.total))} トークン</strong>
        <span class="muted">/ 上限 ${esc(limitTxt)}</span>
        <span class="orch-legend"><span class="orch-swatch orch-bar-measured"></span>実測 ${esc(orchTokens(tt.measured))}</span>
        <span class="orch-legend"><span class="orch-swatch orch-bar-estimated"></span>推定 ${esc(orchTokens(tt.estimated))}</span>
        <span class="muted">・時間 ${esc(timeTxt)}</span>
      </div>
      <div class="orch-bar orch-bar-lg">
        <span class="orch-bar-measured" style="width:${mPct.toFixed(1)}%"></span>
        <span class="orch-bar-estimated" style="width:${ePct.toFixed(1)}%"></span>
      </div>
    </div>
    <table class="amigos-table orch-table">
      <thead><tr><th>機能</th><th>内訳</th><th>利用量 / 上限</th><th>状態</th></tr></thead>
      <tbody>${wlRows}</tbody>
    </table>
  </section>`;
}

// 2. 利用上限と配分
function orchAllocationPanelHtml(budget) {
  if (!budget) return '';
  const alloc = (budget.config && budget.config.allocation) || {};
  const allocWl = (alloc.workloads && typeof alloc.workloads === 'object') ? alloc.workloads : {};
  const mode = alloc.mode === 'auto' ? 'auto' : 'static';
  const soft = Number(alloc.soft_ratio);
  const softVal = Number.isFinite(soft) ? soft : 0.9;
  const rows = (budget.knownWorkloads || []).map((wl) => {
    const a = allocWl[wl] || {};
    const onEx = ['pause', 'stop', 'degrade'].includes(a.on_exhausted) ? a.on_exhausted : 'pause';
    return `<tr data-orch-alloc-wl="${esc(wl)}">
      <td>${esc(amigosWorkloadLabel(wl))}</td>
      <td><input type="number" min="0" step="1" class="mono orch-alloc-weight" value="${Number(a.weight !== undefined ? a.weight : 1)}" /></td>
      <td><input type="number" min="0" step="1000" class="mono orch-alloc-min" value="${Number(a.min_tokens || 0)}" title="0 = 下限なし" /></td>
      <td><input type="number" min="0" step="1000" class="mono orch-alloc-max" value="${Number(a.max_tokens || 0)}" title="0 = 上限なし" /></td>
      <td><select class="orch-alloc-onex">
        ${['pause', 'stop', 'degrade'].map((v) => `<option value="${v}"${v === onEx ? ' selected' : ''}>${esc(orchOnExhaustedLabel(v))}</option>`).join('')}
      </select></td>
    </tr>`;
  }).join('');
  return `<section class="orch-panel">
    <header class="row">
      <div>
        <span class="summary-kicker">利用上限</span>
        <h3>機能ごとの利用量を設定</h3>
        <p class="muted">全体の上限を各機能へ配分します。配分比と最低保証、上限を指定できます。</p>
      </div>
    </header>
    <div class="row orch-alloc-controls">
      <label>全体の上限（トークン）
        <input type="number" min="0" step="10000" id="orch-token-limit" class="mono" value="${Number(budget.tokenLimit || 0)}" title="0 = 無制限" />
      </label>
      <label>節約モードを始める割合
        <input type="number" min="0" max="1" step="0.05" id="orch-soft-ratio" class="mono" value="${softVal}" />
      </label>
      <label>配分方法
        <select id="orch-alloc-mode">
          <option value="static"${mode === 'static' ? ' selected' : ''}>手動</option>
          <option value="auto"${mode === 'auto' ? ' selected' : ''}>自動</option>
        </select>
      </label>
    </div>
    <table class="amigos-table orch-table">
      <thead><tr><th>機能</th><th>配分比</th><th>最低保証</th><th>上限</th><th>上限到達時</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="settings-save-actions">
      <div class="settings-secondary-actions">
        <button type="button" id="btn-orch-rebalance">配分を更新</button>
        <button type="button" id="btn-orch-calibrate">推定値を調整</button>
      </div>
      <button type="button" id="btn-orch-alloc-save" class="primary-inline"${state.orchSaving ? ' disabled' : ''}>保存</button>
    </div>
  </section>`;
}

// ワークロード別の「用途 / ロール / ノード種別」候補（agents.<key> 上書きのキー補完）。
// project: AGENT_PURPOSES / flow: 役割＋ノード kind / amigos: ロール id は動的（自由入力）/
// routine（kiro-loop）: エージェント選択をしない（tmux 送信）ため用途別なし。
const ORCH_AGENT_KEYS = {
  project: ['plan', 'review', 'prioritize', 'route', 'adjudicate', 'verify',
    'distill', 'assess', 'repo_map', 'doctor'],
  flow: ['planner', 'evaluator', 'worker', 'work', 'generate', 'classify',
    'synthesize', 'verify', 'filter', 'judge', 'reduce', 'split', 'map'],
  amigos: [],
  routine: [],
};

// 1 ワークロードの用途 / ロール別上書きの小テーブル（既存キーの編集・削除＋新規追加行）。
function orchAgentsEditorHtml(wl, wc) {
  const agents = wc.agents || {};
  const keys = Object.keys(agents);
  const known = ORCH_AGENT_KEYS[wl];
  // routine は用途別の概念が無い（kiro-loop は CLI/モデルを選ばない）ため編集 UI を出さない。
  if (known && known.length === 0 && wl === 'routine') {
    return '<div class="orch-agents-none"><small class="muted">定常業務では、用途ごとの変更はできません。</small></div>';
  }
  const listId = `orch-keys-${esc(wl)}`;
  const datalist = (known && known.length)
    ? `<datalist id="${listId}">${known.map((k) => `<option value="${esc(k)}"></option>`).join('')}</datalist>`
    : '';
  const rows = keys.map((key) => {
    const ov = agents[key] || {};
    return `<tr class="orch-agent-row" data-orch-key="${esc(key)}">
      <td><code>${esc(key)}</code></td>
      <td><input type="text" class="orch-agent-cli" placeholder="（既定）" value="${esc(ov.agent_cli || '')}" /></td>
      <td><input type="text" class="orch-agent-model" placeholder="（既定）" value="${esc(ov.model || '')}" /></td>
      <td><label class="orch-agent-rm"><input type="checkbox" class="orch-agent-remove" /> 削除</label></td>
    </tr>`;
  }).join('');
  const addRow = `<tr class="orch-agent-add">
      <td><input type="text" class="orch-agent-new-key" list="${listId}" placeholder="${(known && known.length) ? '用途 / 担当名' : '担当名'}" /></td>
      <td><input type="text" class="orch-agent-new-cli" placeholder="エージェント" /></td>
      <td><input type="text" class="orch-agent-new-model" placeholder="モデル" /></td>
      <td><small class="muted">保存で追加</small></td>
    </tr>`;
  const summaryLabel = keys.length ? `用途 / 担当ごとの変更（${keys.length}）` : '用途 / 担当ごとの変更を追加';
  return `<details class="orch-agents" data-ui-key="orch-agents-${esc(wl)}"${keys.length ? ' open' : ''}>
    <summary>${esc(summaryLabel)}</summary>
    ${datalist}
    <table class="amigos-table orch-agents-table">
      <thead><tr><th>用途 / 担当 / 種別</th><th>エージェント</th><th>モデル</th><th></th></tr></thead>
      <tbody>${rows}${addRow}</tbody>
    </table>
  </details>`;
}

// 3. 機能ごとのエージェント設定
function orchMatrixPanelHtml(overview) {
  const control = overview.control || { workloads: {} };
  const wls = (overview.budget && overview.budget.knownWorkloads) || ['routine', 'project', 'flow', 'amigos'];
  const blocks = wls.map((wl) => {
    const wc = (control.workloads || {})[wl] || {};
    const deg = wc.degraded || {};
    return `<div class="orch-ctrl-wl" data-orch-ctrl-wl="${esc(wl)}">
      <div class="orch-ctrl-head">
        <strong>${esc(amigosWorkloadLabel(wl))}</strong>
        <label>エージェント<input type="text" class="orch-ctrl-cli" placeholder="各機能の設定を使用" value="${esc(wc.agent_cli || '')}" /></label>
        <label>モデル<input type="text" class="orch-ctrl-model" placeholder="各機能の設定を使用" value="${esc(wc.model || '')}" /></label>
        <label>節約時のモデル<input type="text" class="orch-ctrl-degraded-model" placeholder="変更しない" value="${esc(deg.model || '')}" /></label>
      </div>
      ${orchAgentsEditorHtml(wl, wc)}
    </div>`;
  }).join('');
  return `<section class="orch-panel">
    <header class="row">
      <div>
        <span class="summary-kicker">担当設定</span>
        <h3>機能ごとのエージェントとモデル</h3>
        <p class="muted">空欄の項目は各機能の設定を使います。必要な場合だけ、用途や担当ごとに変更できます。</p>
      </div>
      <div>${orchBadge('muted', `設定版 ${Number(control.revision || 0)}`)}</div>
    </header>
    <div class="orch-ctrl-blocks">${blocks}</div>
    <div class="settings-save-actions">
      <button type="button" id="btn-orch-control-save" class="primary-inline"${state.orchSaving ? ' disabled' : ''}>保存</button>
    </div>
  </section>`;
}

// 4. 実行サービスの状態と操作
function orchStatusPanelHtml(overview) {
  const status = overview.status || [];
  const control = overview.control || { workloads: {}, revision: 0 };
  const desiredRev = Number(control.revision || 0);
  const workloads = [...new Set([
    ...((overview.budget && overview.budget.knownWorkloads) || []),
    ...Object.keys(control.workloads || {}),
    ...status.map((s) => String(s.workload || '')).filter(Boolean),
  ])];
  const rows = workloads.length ? workloads.map((wl) => {
    const records = status.filter((s) => String(s.workload || '') === wl);
    const active = records.filter((s) => s.fresh);
    const stale = records.length - active.length;
    const tools = [...new Set(records.map((s) => String(s.tool || '')).filter(Boolean))];
    const desired = ((control.workloads || {})[wl] || {}).lifecycle || 'run';
    const applied = active.map((s) => Number(s.revision_applied)).filter(Number.isFinite);
    const oldestApplied = applied.length ? Math.min(...applied) : null;
    const revBadge = oldestApplied === null
      ? orchBadge('muted', '稼働中のサービスなし')
      : (oldestApplied >= desiredRev
        ? orchBadge('ok', `反映済み（設定版 ${oldestApplied}）`)
        : orchBadge('soft', `未反映（${oldestApplied}/${desiredRev}）`));
    const exceeded = active.some((s) => (s.budget || {}).exceeded);
    const soft = active.some((s) => (s.budget || {}).soft);
    const budgetBadge = exceeded ? orchBadge('over', '超過') : soft ? orchBadge('soft', '縮退中') : orchBadge('ok', '正常');
    const activity = active.length
      ? `${orchBadge('ok', `稼働中 ${active.length}件`)}${stale ? ` <small class="muted">終了済み記録 ${stale}件は非表示</small>` : ''}`
      : `${orchBadge('muted', '現在は稼働なし')}${stale ? ` <small class="muted">終了済み記録 ${stale}件は非表示</small>` : ''}`;
    return `<tr>
      <td><strong>${esc(amigosWorkloadLabel(wl))}</strong>${tools.length ? `<br><small class="muted">${esc(tools.join(', '))}</small>` : ''}</td>
      <td>${orchBadge(desired === 'run' ? 'ok' : 'soft', `端末全体: ${orchLifecycleLabel(desired)}`)}<br>${activity}</td>
      <td>${revBadge}</td>
      <td>${budgetBadge}</td>
      <td class="orch-lc-actions">
        <button type="button" data-orch-lc="run" data-orch-wl="${esc(wl)}"${desired === 'run' ? ' disabled' : ''}>実行を許可</button>
        <button type="button" class="danger" data-orch-lc="stop" data-orch-wl="${esc(wl)}"${desired === 'stop' ? ' disabled' : ''}>端末全体で停止</button>
      </td>
    </tr>`;
  }).join('') : '<tr><td colspan="5" class="muted">稼働中の実行サービスはありません。</td></tr>';
  return `<section class="orch-panel">
    <header class="row">
      <div>
        <span class="summary-kicker">稼働状況</span>
        <h3>実行の許可・停止</h3>
        <p class="muted">機能の種類ごとに1行表示します。「端末全体で停止」は個別のrunやPIDではなく、この端末にある同種のエージェント呼び出しをすべて拒否します。</p>
      </div>
    </header>
    <table class="amigos-table orch-table">
      <thead><tr><th>サービス</th><th>稼働状態</th><th>設定の反映</th><th>利用量 / 接続</th><th>操作</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </section>`;
}

// 4.5 共通指示: すべてのエージェントへ渡す指示と適用状況。
function orchInstructionsPanelHtml(overview) {
  const gi = overview.instructions || { enabled: true, text: '', skills: [], tools: {}, revision: 0, max_chars: 2000 };
  const preview = overview.instructionsPreview || '';
  const inv = state.orchSkillsInventory || [];
  const selected = new Map();
  for (const s of gi.skills || []) {
    if (typeof s === 'string') selected.set(s, '');
    else if (s && s.name) selected.set(s.name, s.note || '');
  }
  const inventoryByName = new Map(inv.map((s) => [s.name, s]));
  const skillRows = [...selected.keys()].sort().map((name) => {
    const note = selected.get(name) || '';
    return orchSkillRowHtml(name, note, inventoryByName.get(name));
  }).join('');
  const skillOptions = inv.slice().sort((a, b) => String(a.name).localeCompare(String(b.name)))
    .filter((s) => !selected.has(s.name))
    .map((s) => `<option value="${esc(s.name)}"></option>`).join('');
  const allow = (gi.tools && Array.isArray(gi.tools.allow)) ? gi.tools.allow.join(', ') : '';
  const denyNote = (gi.tools && gi.tools.deny_note) || '';
  const appliedRows = (overview.status || []).map((s) => {
    const applied = Number(s.instructions_revision_applied);
    const badge = Number.isFinite(applied)
      ? (applied >= Number(gi.revision || 0) ? orchBadge('ok', `反映済み（設定版 ${applied}）`) : orchBadge('soft', `未反映（${applied}/${gi.revision}）`))
      : orchBadge('muted', '反映待ち');
    return `<tr><td>${esc(s.tool || '?')} <small class="muted">${esc(amigosWorkloadLabel(String(s.workload || '')))}</small></td><td>${badge}</td></tr>`;
  }).join('') || '<tr><td colspan="2" class="muted">稼働中の実行サービスはありません。</td></tr>';
  return `<section class="orch-panel">
    <header class="row">
      <div>
        <span class="summary-kicker">共通指示</span>
        <h3>すべてのエージェントへの共通指示</h3>
        <p class="muted">この端末で実行するエージェントへ、共通の指示・推奨スキル・利用できるツールを設定します。
          個別のタスクやプロジェクトに指示がある場合は、そちらが優先されます。</p>
      </div>
      <div>${gi.enabled ? orchBadge('ok', `有効・設定版 ${esc(String(gi.revision || 0))}`) : orchBadge('soft', '無効')}</div>
    </header>
    <label class="orch-instr-enabled"><input type="checkbox" id="orch-instr-enabled" ${gi.enabled ? 'checked' : ''} /> 共通指示を使用する</label>
    <label class="orch-instr-field">指示内容
      <textarea id="orch-instr-text" class="mono" rows="6" placeholder="例: 回答は日本語。破壊的変更の前に必ず既存テストを確認する。">${esc(gi.text || '')}</textarea>
    </label>
    <div class="orch-instr-field">推奨スキル
      <div class="orch-skill-picker">
        <label for="orch-skill-add">候補から追加</label>
        <div class="orch-skill-add-row">
          <input type="text" id="orch-skill-add" list="orch-skill-options" autocomplete="off"
            aria-describedby="orch-skill-add-help orch-skill-candidate-description" placeholder="名前を入力して候補から選択" />
          <datalist id="orch-skill-options">${skillOptions}</datalist>
          <button type="button" id="btn-orch-skill-add" disabled>追加</button>
        </div>
        <p id="orch-skill-candidate-description" class="orch-skill-candidate-description" aria-live="polite" hidden></p>
        <small id="orch-skill-add-help" class="muted">名前の一部を入力してください。各エージェントと共通のスキル置き場から候補を表示します。</small>
      </div>
      <div class="orch-skill-list" id="orch-skill-selected">
        ${skillRows || '<p class="muted orch-skill-empty">追加されたスキルはありません。</p>'}
      </div>
    </div>
    <div class="row">
      <label class="orch-instr-field">使ってほしいツール（カンマ区切り）
        <input type="text" id="orch-instr-allow" placeholder="fs_read, fs_write, execute_bash" value="${esc(allow)}"
          aria-describedby="orch-instr-allow-help" />
        <small id="orch-instr-allow-help" class="muted">指示文として伝えるだけで、実行時に制限はかかりません。</small>
      </label>
      <label class="orch-instr-field">ツール利用時の注意
        <input type="text" id="orch-instr-deny" placeholder="外部への push 系は人の確認を経る" value="${esc(denyNote)}" />
      </label>
      <label class="orch-instr-field">指示の最大文字数
        <input type="number" id="orch-instr-max" min="0" max="8000" value="${esc(String(gi.max_chars || 2000))}" />
      </label>
    </div>
    <div class="settings-save-actions">
      <button type="button" id="btn-orch-instr-save" class="primary-inline">保存</button>
    </div>
    <details class="orch-instr-preview" data-ui-key="orch-instr-preview">
      <summary>エージェントに渡される内容を確認</summary>
      <pre class="mono orch-instr-preview-body">${esc(preview || '（現在、エージェントへ渡す共通指示はありません）')}</pre>
    </details>
    <details class="orch-instr-applied" data-ui-key="orch-instr-applied">
      <summary>実行サービスへの反映状況</summary>
      <table class="amigos-table orch-table"><thead><tr><th>サービス</th><th>反映</th></tr></thead><tbody>${appliedRows}</tbody></table>
    </details>
  </section>`;
}

function orchSkillRowHtml(name, note = '', skill = null) {
  const info = skill || {};
  const sourceLabels = { kiro: 'Kiro', copilot: 'Copilot', claude: 'Claude', codex: 'Codex', agents: '共通' };
  const metadata = [];
  if (info.category) metadata.push(`分類: ${info.category}`);
  if (info.version) metadata.push(`バージョン: ${info.version}`);
  if (Array.isArray(info.tags) && info.tags.length) metadata.push(`タグ: ${info.tags.join(', ')}`);
  if (Array.isArray(info.sources) && info.sources.length) {
    metadata.push(`利用元: ${info.sources.map((source) => sourceLabels[source] || source).join(', ')}`);
  }
  const hasDetails = info.description || metadata.length;
  return `<div class="orch-skill-row" data-orch-skill="${esc(name)}">
    <div class="orch-skill-identity"><strong>${esc(name)}</strong></div>
    <label class="orch-skill-note-label"><span>使う場面（任意）</span>
      <input type="text" class="orch-skill-note" placeholder="例: コード修正時" value="${esc(note)}" />
    </label>
    <button type="button" class="orch-skill-remove" aria-label="${esc(name)}を削除">削除</button>
    ${hasDetails ? `<details class="orch-skill-details" data-ui-key="orch-skill-details-${esc(name)}">
      <summary>説明と属性を表示</summary>
      ${info.description ? `<p class="orch-skill-description">${esc(info.description)}</p>` : ''}
      ${metadata.length ? `<small class="muted orch-skill-meta">${esc(metadata.join(' · '))}</small>` : ''}
    </details>` : ''}
  </div>`;
}

function shortSkillDescription(text) {
  const chars = Array.from(String(text || '').trim());
  return chars.length > 100 ? `${chars.slice(0, 100).join('')}…` : chars.join('');
}

// 4.6 セッション開始コマンド: セッションが始まった直後に 1 回だけ走らせる前準備。
// 正典は agent-session-commands 契約（schemas/agent-session-commands.schema.json）。
// 共通指示（テキスト注入）と違い副作用があるため、委譲先ノードへは伝播しない。
const ORCH_SESSION_ENGINES = [
  { id: 'kiro-loop', label: '定常業務（kiro-loop）', chat: true },
  { id: 'agent-loop', label: '定常業務（agent-loop）', chat: true },
  { id: 'dashboard', label: 'このアプリ（定常業務ウィンドウ）', chat: true },
  { id: 'agent-flow', label: 'タスク実行（agent-flow）', chat: false },
];

function orchSessionRowHtml(cmd, index) {
  const c = cmd || {};
  const mode = c.mode === 'chat' ? 'chat' : 'process';
  const when = c.when || {};
  const strategy = c.strategy === 'bundle' ? 'bundle' : 'paste';
  const list = (v) => (Array.isArray(v) ? v.join(', ') : '');
  return `<div class="orch-sess-row" data-orch-sess="${index}" data-orch-sess-mode="${mode}">
    <div class="orch-sess-head">
      <label class="orch-sess-field orch-sess-id"><span>名前</span>
        <input type="text" class="orch-sess-id-input mono" placeholder="sync-repo" value="${esc(c.id || '')}" />
      </label>
      <label class="orch-sess-field"><span>実行方法</span>
        <select class="orch-sess-mode">
          <option value="process"${mode === 'process' ? ' selected' : ''}>コマンドを実行する</option>
          <option value="chat"${mode === 'chat' ? ' selected' : ''}>エージェントに送る</option>
        </select>
      </label>
      <label class="orch-sess-field"><span>失敗したとき</span>
        <select class="orch-sess-onerror">
          <option value="warn"${c.on_error !== 'fail' ? ' selected' : ''}>続行する</option>
          <option value="fail"${c.on_error === 'fail' ? ' selected' : ''}>開始を中止する</option>
        </select>
      </label>
      <label class="orch-sess-field orch-sess-strategy" ${mode === 'chat' ? '' : 'hidden'} title="従来の 1 コマンド 1 ペースト式ではなく、同じ指定の行を 1 つの起動アクション束にまとめます">
        <input type="checkbox" class="orch-sess-bundle" ${strategy === 'bundle' ? 'checked' : ''} /> まとめて依頼
      </label>
      <button type="button" class="orch-sess-remove" aria-label="${esc(c.id || `${index + 1}行目`)}を削除">削除</button>
    </div>
    <label class="orch-sess-field orch-sess-run-field"><span class="orch-sess-run-label">${mode === 'chat' ? 'エージェントへ送る内容' : '実行するコマンド'}</span>
      <textarea class="orch-sess-run mono" rows="2" placeholder="git -C &quot;{cwd}&quot; fetch --prune">${esc(c.run || '')}</textarea>
    </label>
    <div class="orch-sess-process-only" ${mode === 'chat' ? 'hidden' : ''}>
      <label class="orch-sess-field"><span>実行する場所（省略可）</span>
        <input type="text" class="orch-sess-cwd mono" placeholder="{workspace}" value="${esc(c.cwd || '')}" />
      </label>
      <label class="orch-sess-field orch-sess-narrow"><span>上限秒</span>
        <input type="number" class="orch-sess-timeout" min="1" max="600" placeholder="60" value="${esc(c.timeout === undefined ? '' : String(c.timeout))}" />
      </label>
    </div>
    <details class="orch-sess-when" data-ui-key="orch-sess-when-${index}">
      <summary>使う条件をしぼる（空欄ならすべてに適用）</summary>
      <div class="orch-sess-when-grid">
        <label class="orch-sess-field"><span>実行サービス</span>
          <input type="text" class="orch-sess-when-engines mono" placeholder="kiro-loop, agent-flow" value="${esc(list(when.engines))}" />
        </label>
        <label class="orch-sess-field"><span>ワークロード</span>
          <input type="text" class="orch-sess-when-workloads mono" placeholder="routine, flow" value="${esc(list(when.workloads))}" />
        </label>
        <label class="orch-sess-field"><span>エージェント</span>
          <input type="text" class="orch-sess-when-cli mono" placeholder="kiro, claude" value="${esc(list(when.agent_cli))}" />
        </label>
      </div>
    </details>
  </div>`;
}

function orchSessionPreviewHtml() {
  const pv = state.orchSessionPreview;
  if (!pv) return '<p class="muted">「この内容で確認」を押すと、実行サービスごとの実行内容を表示します。</p>';
  if (pv.error) return `<p class="muted">プレビューを作れませんでした: ${esc(pv.error)}</p>`;
  const entries = pv.entries || [];
  if (!entries.length) return '<p class="muted">このサービスで実行されるコマンドはありません。</p>';
  const reason = {
    when: '条件に合わないため実行しません',
    'no-session': 'このサービスにはセッションがないため送れません',
    budget: '合計の上限秒を超えるため実行しません',
  };
  const rows = entries.map((e, i) => {
    const skipped = !!e.skip;
    const label = e.mode === 'chat' ? (e.strategy === 'bundle' ? 'まとめて依頼' : 'エージェントに送る') : 'コマンドを実行';
    const note = skipped
      ? `<small class="muted">${esc(reason[e.skip] || 'スキップします')}</small>`
      : `<small class="muted">${esc(e.mode === 'chat' ? (e.strategy === 'bundle' ? `含む行: ${(e.bundled_ids || []).join(', ')}` : '従来式: 1 コマンド 1 ペースト') : `${e.cwd || '（セッションの場所）'} / 上限 ${e.timeout} 秒 / ${e.on_error === 'fail' ? '失敗したら開始を中止' : '失敗しても続行'}`)}</small>`;
    return `<li class="orch-sess-preview-item${skipped ? ' orch-sess-preview-skipped' : ''}">
      <div class="orch-sess-preview-head"><strong>${esc(String(i + 1))}. ${esc(e.id)}</strong> ${orchBadge(skipped ? 'muted' : 'ok', label)}</div>
      <pre class="mono">${esc(e.run)}</pre>${note}
    </li>`;
  }).join('');
  return `<ol class="orch-sess-preview-list">${rows}</ol>`;
}

function orchSessionCommandsPanelHtml(overview) {
  const sc = overview.sessionCommands || { enabled: true, commands: [], revision: 0, max_total_timeout: 120 };
  const commands = Array.isArray(sc.commands) ? sc.commands : [];
  const rows = commands.map((c, i) => orchSessionRowHtml(c, i)).join('')
    || '<p class="muted orch-sess-empty">登録されたコマンドはありません。</p>';
  const engineOptions = ORCH_SESSION_ENGINES.map((e) => {
    const selected = (state.orchSessionPreview || {}).engine === e.id;
    return `<option value="${esc(e.id)}"${selected ? ' selected' : ''}>${esc(e.label)}</option>`;
  }).join('');
  const appliedRows = (overview.status || []).map((s) => {
    const applied = Number(s.session_commands_revision_applied);
    const badge = Number.isFinite(applied)
      ? (applied >= Number(sc.revision || 0) ? orchBadge('ok', `反映済み（設定版 ${applied}）`) : orchBadge('soft', `未反映（${applied}/${sc.revision}）`))
      : orchBadge('muted', '反映待ち');
    return `<tr><td>${esc(s.tool || '?')} <small class="muted">${esc(amigosWorkloadLabel(String(s.workload || '')))}</small></td><td>${badge}</td></tr>`;
  }).join('') || '<tr><td colspan="2" class="muted">稼働中の実行サービスはありません。</td></tr>';
  return `<section class="orch-panel orch-sess-panel">
    <header class="row">
      <div>
        <span class="summary-kicker">使用するコマンド</span>
        <h3>エージェントを始める前のコマンド</h3>
        <p class="muted">通常は設定しなくても使えます。リポジトリの取得や開発環境の起動など、
          毎回必要な下準備がある場合だけ追加してください。上から順に 1 回実行します。</p>
      </div>
      <div>${sc.enabled ? orchBadge('ok', `有効・設定版 ${esc(String(sc.revision || 0))}`) : orchBadge('soft', '無効')}</div>
    </header>
    <label class="orch-sess-enabled"><input type="checkbox" id="orch-sess-enabled" ${sc.enabled ? 'checked' : ''} /> 登録したコマンドを使用する</label>
    <div class="orch-sess-list" id="orch-sess-list">${rows}</div>
    <div class="row orch-sess-controls">
      <button type="button" id="btn-orch-sess-add">コマンドを追加</button>
      <label class="orch-sess-field orch-sess-narrow"><span>すべての合計の上限秒</span>
        <input type="number" id="orch-sess-max-total" min="1" max="600" value="${esc(String(sc.max_total_timeout || 120))}" />
      </label>
    </div>
    <ul class="orch-sess-notes muted">
      <li>コマンドはそのままシェルへ渡します。空白を含む場所を指す <code>{cwd}</code> などは <code>"</code> で囲んでください。</li>
      <li>「失敗したとき: 開始を中止する」を選ぶと、そのコマンドが失敗したときエージェントが起動しなくなります。</li>
      <li>「エージェントに送る」は、行ごとに従来の 1 コマンド 1 ペースト式と、チェックした行を 1 つにまとめる依頼式を選べます。</li>
      <li>設定を変えても、すでに動いているセッションには反映されません。次に始まるセッションから有効になります。</li>
    </ul>
    <div class="settings-save-actions">
      <button type="button" id="btn-orch-sess-save" class="primary-inline">保存</button>
    </div>
    <details class="orch-sess-preview" data-ui-key="orch-sess-preview" open>
      <summary>実行される内容を確認</summary>
      <div class="row orch-sess-preview-controls">
        <label class="orch-sess-field"><span>確認する実行サービス</span>
          <select id="orch-sess-preview-engine">${engineOptions}</select>
        </label>
        <button type="button" id="btn-orch-sess-preview">この内容で確認</button>
      </div>
      <div id="orch-sess-preview-body">${orchSessionPreviewHtml()}</div>
    </details>
    <details class="orch-sess-applied" data-ui-key="orch-sess-applied">
      <summary>実行サービスへの反映状況</summary>
      <table class="amigos-table orch-table"><thead><tr><th>サービス</th><th>反映</th></tr></thead><tbody>${appliedRows}</tbody></table>
    </details>
  </section>`;
}

// 画面の入力を agent-session-commands 契約の形へ読み出す。保存とプレビューで同じものを使う
// （プレビューが「保存したら何が起きるか」と一致する）。
function readSessionCommandsForm(root) {
  const csv = (el) => (el && el.value ? el.value.split(',').map((s) => s.trim()).filter(Boolean) : []);
  const commands = [...root.querySelectorAll('.orch-sess-row')].map((row) => {
    const mode = row.querySelector('.orch-sess-mode').value === 'chat' ? 'chat' : 'process';
    const cmd = {
      id: row.querySelector('.orch-sess-id-input').value.trim(),
      mode,
      run: row.querySelector('.orch-sess-run').value.trim(),
      on_error: row.querySelector('.orch-sess-onerror').value === 'fail' ? 'fail' : 'warn',
    };
    if (mode === 'chat' && row.querySelector('.orch-sess-bundle') && row.querySelector('.orch-sess-bundle').checked) {
      cmd.strategy = 'bundle';
    }
    if (mode === 'process') {
      const cwd = row.querySelector('.orch-sess-cwd').value.trim();
      if (cwd) cmd.cwd = cwd;
      const timeout = row.querySelector('.orch-sess-timeout').value.trim();
      if (timeout) cmd.timeout = Number(timeout);
    }
    const when = {
      engines: csv(row.querySelector('.orch-sess-when-engines')),
      workloads: csv(row.querySelector('.orch-sess-when-workloads')),
      agent_cli: csv(row.querySelector('.orch-sess-when-cli')),
    };
    if (when.engines.length || when.workloads.length || when.agent_cli.length) cmd.when = when;
    return cmd;
  });
  const enabled = root.querySelector('#orch-sess-enabled');
  const maxTotal = root.querySelector('#orch-sess-max-total');
  return {
    enabled: enabled ? enabled.checked : true,
    commands,
    max_total_timeout: Number((maxTotal && maxTotal.value) || 120),
  };
}

// 5. 利用できるエージェントの一覧と追加定義。
function orchInventoryPanelHtml(overview) {
  const inv = overview.agents || { builtins: [], dropins: [] };
  const builtins = (inv.builtins || []).map((b) => orchBadge('muted', b)).join(' ');
  const dropins = (inv.dropins || []).map((d, i) => {
    const errs = (d.errors || []).length
      ? `<div class="orch-errors">${d.errors.map((e) => `<div class="orch-error">${esc(e)}</div>`).join('')}</div>`
      : '';
    const shadow = d.shadowed ? orchBadge('soft', '同名の設定があるため無効') : orchBadge('ok', '利用可能');
    const specText = d.spec ? JSON.stringify(d.spec, null, 2) : '';
    return `<details class="orch-dropin" data-orch-dropin="${i}" data-ui-key="orch-dropin-${esc(d.name)}">
      <summary><strong>${esc(d.name)}</strong> ${shadow}
        <small class="muted">${esc(d.dir || '')}</small></summary>
      ${errs}
      <textarea class="orch-dropin-spec mono" rows="8" data-orch-name="${esc(d.name)}" data-orch-dir="${esc(d.dir || '')}">${esc(specText)}</textarea>
      <div class="settings-save-actions">
        <button type="button" class="orch-dropin-delete danger" data-orch-name="${esc(d.name)}" data-orch-dir="${esc(d.dir || '')}">削除</button>
        <button type="button" class="orch-dropin-save primary-inline" data-orch-name="${esc(d.name)}" data-orch-dir="${esc(d.dir || '')}">保存</button>
      </div>
    </details>`;
  }).join('') || '<p class="muted">追加したエージェントはありません。</p>';
  const sample = JSON.stringify({ command: ['cursor', 'run', '{model}'], prompt_via: 'stdin', output: 'stdout' }, null, 2);
  return `<section class="orch-panel">
    <header class="row">
      <div>
        <span class="summary-kicker">利用可能なエージェント</span>
        <h3>エージェント一覧</h3>
        <p class="muted">最初から利用できるエージェントと、この端末に追加したエージェントを確認・編集できます。</p>
      </div>
    </header>
    <p>標準: ${builtins}</p>
    <div class="orch-dropins">${dropins}</div>
    <details class="orch-dropin orch-new" data-ui-key="orch-dropin-new">
      <summary><strong>新しいエージェントを追加</strong></summary>
      <label class="orch-new-name">エージェント名
        <input type="text" id="orch-new-name" placeholder="cursor" />
      </label>
      <textarea id="orch-new-spec" class="mono" rows="8">${esc(sample)}</textarea>
      <div class="settings-save-actions">
        <button type="button" id="btn-orch-new-save" class="primary-inline">追加</button>
      </div>
      <p class="muted">コマンド形式などの詳細を JSON で指定します。</p>
    </details>
  </section>`;
}

const GLOBAL_SETTINGS_SECTIONS = [
  { id: 'app', label: 'アプリ' },
  { id: 'agents', label: 'エージェント' },
  { id: 'sync', label: '同期と実行' },
  { id: 'routine', label: '定常業務' },
  { id: 'integrations', label: '外部連携' },
];

function globalSettingsPaneHtml(id, content) {
  const active = state.globalSettingsSection === id;
  return `<section id="global-settings-panel-${id}" class="global-settings-pane"
    role="tabpanel" aria-labelledby="global-settings-tab-${id}" ${active ? '' : 'hidden'}>${content}</section>`;
}

function globalSettingsAppHtml() {
  return `<div class="global-settings-card">
    <header class="global-settings-card-heading">
      <span class="summary-kicker">アプリ</span>
      <h2>表示と通知</h2>
      <p class="muted">プロジェクトの探し方と、画面更新・通知の動作を設定します。</p>
    </header>
    <div class="field">
      <label for="cfg-roots">プロジェクトを探すフォルダ（1行に1つ）</label>
      <textarea id="cfg-roots" rows="4" placeholder="例: C:\src\payments&#10;/home/me/src/webapp"></textarea>
      <p class="field-help">親フォルダを登録すると、その中のプロジェクトも自動で見つけます。</p>
    </div>
    <div class="row2">
      <div class="field"><label class="check"><input type="checkbox" id="cfg-autodiscover" /> 稼働中のプロジェクトを自動で追加</label></div>
      <div class="field"><label for="cfg-refresh">表示の更新間隔（秒）</label><input id="cfg-refresh" type="number" min="0" step="1" /></div>
    </div>
    <div class="row2">
      <div class="field"><label class="check"><input type="checkbox" id="cfg-notify" /> 対応が必要になったら通知する</label></div>
      <div class="field"><label for="cfg-needs-sla">長時間未対応として知らせるまで（時間）</label><input id="cfg-needs-sla" type="number" min="1" step="1" /></div>
    </div>
    <div class="field">
      <label for="cfg-role">この PC の役割</label>
      <select id="cfg-role">
        <option value="engineer">実行も行う（すべての機能）</option>
        <option value="viewer">閲覧・レビュー専用</option>
      </select>
      <p class="field-help">閲覧専用では、状態を共有するフォルダを登録するだけで監視・コメント・承認ができます。実行用の環境設定は不要です。</p>
    </div>
    <div class="field">
      <label>セットアップ診断</label>
      <p class="field-help">登録したフォルダが正しく同期できるか確認します。</p>
      <div class="row"><button type="button" id="btn-setup-diagnostics">診断する</button></div>
      <div id="setup-diagnostics-result" class="muted" aria-live="polite"></div>
    </div>
    <div class="settings-save-actions"><button type="button" id="btn-save-app-settings" class="primary-inline">保存</button></div>
  </div>`;
}

function globalSettingsAssistantHtml() {
  return `<section class="orch-panel">
    <header class="row"><div>
      <span class="summary-kicker">画面内AI</span>
      <h3>AIアシスタント</h3>
      <p class="muted">まず「使用するエージェント」を選んでください。モデルと待ち時間は、必要な場合だけ変更します。</p>
    </div></header>
    <div class="row2">
      <div class="field">
        <label for="cfg-agent-cli">使用するエージェント</label>
        <input id="cfg-agent-cli" class="mono" list="cfg-agent-cli-options" placeholder="プロジェクト設定に従う" />
        <datalist id="cfg-agent-cli-options">
          <option value="kiro">kiro</option><option value="claude">Claude Code</option>
          <option value="copilot">GitHub Copilot CLI</option><option value="codex">Codex CLI</option>
          <option value="cursor">Cursor Agent</option><option value="ollama">ローカルモデル</option>
        </datalist>
      </div>
      <div class="field"><label for="cfg-agent-model">モデル</label><input id="cfg-agent-model" class="mono" placeholder="エージェントの既定を使用" /></div>
    </div>
    <div class="field global-settings-short-field"><label for="cfg-agent-timeout">応答を待つ時間（秒）</label><input id="cfg-agent-timeout" type="number" min="30" step="10" /></div>
    <div class="settings-save-actions"><button type="button" id="btn-save-agent-settings" class="primary-inline">保存</button></div>
  </section>`;
}

function globalSettingsSyncHtml() {
  return `<div class="global-settings-card">
    <header class="global-settings-card-heading">
      <span class="summary-kicker">同期と実行</span>
      <h2>変更の共有と実行場所</h2>
      <p class="muted">複数の環境で状態を共有する場合の動作を設定します。</p>
    </header>
    <h3>変更の共有</h3>
    <div class="row2">
      <div class="field"><label for="cfg-git-pull">共有先を確認する間隔（秒・0で自動確認なし）</label><input id="cfg-git-pull" type="number" min="0" step="1" /></div>
      <div class="field"><label class="check"><input type="checkbox" id="cfg-git-autopush" /> 操作した変更を自動で共有する</label></div>
    </div>
    <div class="field"><label for="cfg-project-command">プロジェクト操作コマンド（本体の起動に使用）</label><input id="cfg-project-command" class="mono" placeholder="agent-project" /></div>
    <h3>実行データの共有先</h3>
    <div class="row2">
      <div class="field"><label for="cfg-flow-bus">共通の共有先</label><input id="cfg-flow-bus" class="mono" placeholder="空欄なら自動で探します" /></div>
      <div class="field"><label for="cfg-flow-lockdir">実行状態の保存先</label><input id="cfg-flow-lockdir" class="mono" placeholder="空欄なら既定の場所を使用" /></div>
    </div>
    <div class="field"><label for="cfg-flow-bus-by-project">プロジェクトごとの共有先（1行に1つ）</label>
      <textarea id="cfg-flow-bus-by-project" class="mono" rows="4" placeholder="alpha = /home/me/clones/alpha/agent-flow"></textarea></div>
    <div class="settings-save-actions"><button type="button" id="btn-save-sync-settings" class="primary-inline">保存</button></div>
  </div>`;
}

function globalSettingsRoutineHtml() {
  return `<div class="global-settings-card">
    <header class="global-settings-card-heading">
      <span class="summary-kicker">定常業務</span>
      <h2>定期実行と定型処理</h2>
      <p class="muted">定常業務を動かすコマンドを設定します。通常は変更不要です。</p>
    </header>
    <div class="row2">
      <div class="field"><label for="cfg-cowork-loop-provider">定期実行の種類</label><input id="cfg-cowork-loop-provider" class="mono" placeholder="kiro-loop" /></div>
      <div class="field"><label for="cfg-cowork-loop-command">定期実行コマンド</label><input id="cfg-cowork-loop-command" class="mono" placeholder="kiro-loop" /></div>
    </div>
    <div class="row2">
      <div class="field"><label for="cfg-cowork-sm-command">定型処理コマンド</label><input id="cfg-cowork-sm-command" class="mono" placeholder="statemachine-use" /></div>
      <div class="field global-settings-open-field"><button type="button" id="btn-settings-cowork-open">定常業務を開く</button></div>
    </div>
    <div class="settings-save-actions"><button type="button" id="btn-save-routine-settings" class="primary-inline">保存</button></div>
  </div>`;
}

function globalSettingsIntegrationsHtml() {
  return `<div class="global-settings-card">
    <header class="global-settings-card-heading">
      <span class="summary-kicker">外部連携</span>
      <h2>GitLabとレビュー画面</h2>
      <p class="muted">レビュー待ちの取得と、専用レビュー画面の開き方を設定します。</p>
    </header>
    <h3>GitLab</h3>
    <div class="row2">
      <div class="field"><label for="cfg-gl-url">GitLabのURL</label><input id="cfg-gl-url" placeholder="https://gitlab.example.com" /></div>
      <div class="field"><label for="cfg-gl-token">アクセストークン</label><input id="cfg-gl-token" type="password" placeholder="glpat-..." /></div>
    </div>
    <h3>レビュー画面</h3>
    <div class="row2">
      <div class="field"><label for="cfg-rv-mode">起動方法</label><select id="cfg-rv-mode">
        <option value="protocol">アプリ連携</option><option value="exe">実行ファイルを指定</option><option value="command">コマンドを指定</option>
      </select></div>
      <div class="field"><label for="cfg-rv-exepath">実行ファイルの場所</label><input id="cfg-rv-exepath" class="mono" placeholder="例: C:\Apps\GitLab Review Viewer.exe" /></div>
    </div>
    <div class="field"><label for="cfg-rv-command">起動コマンド</label><input id="cfg-rv-command" class="mono" placeholder="{url} などの値を利用できます" /></div>
    <div class="settings-save-actions"><button type="button" id="btn-save-integrations-settings" class="primary-inline">保存</button></div>
  </div>`;
}

function globalSettingsAgentsHtml(overview) {
  if (!overview) return `${globalSettingsAssistantHtml()}<div class="empty compact">エージェント情報を読み込んでいます。</div>`;
  if (overview.error) return `${globalSettingsAssistantHtml()}<div class="empty compact"><strong>エージェント情報を読み込めませんでした</strong><span>${esc(overview.error)}</span></div>`;
  return `${globalSettingsAssistantHtml()}
    <section class="agent-management-section" aria-labelledby="agent-management-settings-title">
      <header class="agent-management-section-heading"><span class="summary-kicker">必要に応じて</span><h2 id="agent-management-settings-title">共通設定</h2>
        <p class="muted">すべてのエージェントへ共通の指示・コマンド・利用上限・担当が必要な場合だけ設定します。</p></header>
      ${orchInstructionsPanelHtml(overview)}${orchSessionCommandsPanelHtml(overview)}${orchAllocationPanelHtml(overview.budget)}${orchMatrixPanelHtml(overview)}
    </section>
    <section class="agent-management-section" aria-labelledby="agent-management-status-title">
      <header class="agent-management-section-heading"><span class="summary-kicker">確認</span><h2 id="agent-management-status-title">利用状況</h2>
        <p class="muted">AIの利用量と、現在動いている実行サービスを確認します。</p></header>
      ${orchBudgetPanelHtml(overview.budget)}${orchStatusPanelHtml(overview)}
    </section>
    <section class="agent-management-section" aria-labelledby="agent-management-agents-title">
      <header class="agent-management-section-heading"><span class="summary-kicker">登録内容</span><h2 id="agent-management-agents-title">エージェント一覧</h2>
        <p class="muted">この端末で選択できるエージェントを管理します。</p></header>
      ${orchInventoryPanelHtml(overview)}
    </section>`;
}

function renderOrchestration() {
  const ui = captureUiState();
  const el = $('tab-orchestration');
  if (!el) return;
  const ov = state.orchestration;
  const section = GLOBAL_SETTINGS_SECTIONS.some((item) => item.id === state.globalSettingsSection)
    ? state.globalSettingsSection : 'app';
  state.globalSettingsSection = section;
  const tabs = GLOBAL_SETTINGS_SECTIONS.map((item) => `<button type="button" role="tab"
    id="global-settings-tab-${item.id}" data-global-settings-section="${item.id}"
    aria-controls="global-settings-panel-${item.id}" aria-selected="${item.id === section}"
    tabindex="${item.id === section ? '0' : '-1'}" class="${item.id === section ? 'active' : ''}">${item.label}</button>`).join('');
  const options = GLOBAL_SETTINGS_SECTIONS.map((item) => `<option value="${item.id}"${item.id === section ? ' selected' : ''}>${item.label}</option>`).join('');
  el.innerHTML = `
    <div class="orch-shell global-settings-shell">
      <header class="cowork-header">
        <div>
          <span class="summary-kicker">すべてのプロジェクトに適用</span>
          <h2>全体設定</h2>
          <p class="muted">この端末で使うアプリ、エージェント、連携機能をまとめて管理します。</p>
        </div>
        <div class="row global-settings-agent-refresh" ${section === 'agents' ? '' : 'hidden'}><button id="btn-orch-refresh">最新の状態にする</button></div>
      </header>
      <div class="global-settings-tabs" role="tablist" aria-label="設定の種類">${tabs}</div>
      <label class="global-settings-select" for="global-settings-select">設定の種類
        <select id="global-settings-select">${options}</select>
      </label>
      ${globalSettingsPaneHtml('app', globalSettingsAppHtml())}
      ${globalSettingsPaneHtml('agents', globalSettingsAgentsHtml(ov))}
      ${globalSettingsPaneHtml('sync', globalSettingsSyncHtml())}
      ${globalSettingsPaneHtml('routine', globalSettingsRoutineHtml())}
      ${globalSettingsPaneHtml('integrations', globalSettingsIntegrationsHtml())}
    </div>`;
  populateSettingsFields();
  setupGlobalSettings(el);
  setupOrchestration(el);
  restoreUiState(ui);
}

function selectGlobalSettingsSection(root, section, { focus = false } = {}) {
  if (!GLOBAL_SETTINGS_SECTIONS.some((item) => item.id === section)) return;
  state.globalSettingsSection = section;
  for (const tab of root.querySelectorAll('[data-global-settings-section]')) {
    const selected = tab.dataset.globalSettingsSection === section;
    tab.classList.toggle('active', selected);
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected && focus) tab.focus();
  }
  for (const pane of root.querySelectorAll('.global-settings-pane')) {
    pane.hidden = pane.id !== `global-settings-panel-${section}`;
  }
  const select = root.querySelector('#global-settings-select');
  if (select) select.value = section;
  const refresh = root.querySelector('.global-settings-agent-refresh');
  if (refresh) refresh.hidden = section !== 'agents';
  root.closest('.tabpane').scrollTop = 0;
}

function setupGlobalSettings(root) {
  const tabs = [...root.querySelectorAll('[data-global-settings-section]')];
  for (const tab of tabs) {
    tab.addEventListener('click', () => selectGlobalSettingsSection(root, tab.dataset.globalSettingsSection));
    tab.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const current = tabs.indexOf(tab);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
        : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      selectGlobalSettingsSection(root, tabs[next].dataset.globalSettingsSection, { focus: true });
    });
  }
  const select = root.querySelector('#global-settings-select');
  if (select) select.addEventListener('change', () => selectGlobalSettingsSection(root, select.value));
  for (const field of root.querySelectorAll('.global-settings-pane [id^="cfg-"]')) {
    field.addEventListener('input', () => { state.globalSettingsDirty = true; });
    field.addEventListener('change', () => { state.globalSettingsDirty = true; });
  }
  const saveButtons = {
    'btn-save-app-settings': 'app',
    'btn-save-agent-settings': 'agents',
    'btn-save-sync-settings': 'sync',
    'btn-save-routine-settings': 'routine',
    'btn-save-integrations-settings': 'integrations',
  };
  for (const [id, section] of Object.entries(saveButtons)) {
    const button = root.querySelector(`#${id}`);
    const label = (GLOBAL_SETTINGS_SECTIONS.find((item) => item.id === section) || {}).label || '全体';
    if (button) button.addEventListener('click', () => guard(`${label}設定の保存`, () => saveGlobalSettingsSection(section)));
  }
  const coworkOpen = root.querySelector('#btn-settings-cowork-open');
  if (coworkOpen) coworkOpen.addEventListener('click', openCoworkFromSettings);
  const diagBtn = root.querySelector('#btn-setup-diagnostics');
  if (diagBtn) diagBtn.addEventListener('click', () => runSetupDiagnostics(root));
}

// セットアップ診断: 登録フォルダの有効性を赤/緑で表示し、誤設定を沈黙させない。
async function runSetupDiagnostics(root) {
  const out = root.querySelector('#setup-diagnostics-result');
  if (out) out.textContent = '診断中…';
  try {
    const res = await api.setupDiagnostics();
    if (!out) return;
    if (!res || !res.clones || !res.clones.length) {
      out.innerHTML = '<p class="muted">登録されたフォルダがありません。⚙ 全体設定の「プロジェクトを探すフォルダ」に追加してください。</p>';
      return;
    }
    const rows = res.clones
      .map((c) => `<li class="diag-row diag-${esc(c.level)}"><b>${esc(c.root)}</b><br><span class="muted">${esc(c.summary)}</span></li>`)
      .join('');
    const roleNote = res.role === 'viewer' ? '現在の役割: 閲覧・レビュー専用' : '現在の役割: 実行も行う';
    out.innerHTML = `<ul class="diag-list">${rows}</ul><p class="muted">${esc(roleNote)}</p>`;
  } catch (e) {
    if (out) out.textContent = `診断できませんでした: ${String((e && e.message) || e)}`;
  }
}

function setupOrchestration(root) {
  const refreshBtn = root.querySelector('#btn-orch-refresh');
  if (refreshBtn) refreshBtn.addEventListener('click', () => {
    if (state.globalSettingsDirty || state.orchInstructionsDirty || state.orchSessionDirty) {
      return toast('入力中の設定を保存してから最新の状態にしてください');
    }
    return guard('エージェント情報の更新', async () => { await refreshOrchestration(); renderOrchestration(); });
  });

  // 配分の保存
  const allocSave = root.querySelector('#btn-orch-alloc-save');
  if (allocSave) allocSave.addEventListener('click', () => guard('配分の保存', async () => {
    const workloads = {};
    for (const tr of root.querySelectorAll('[data-orch-alloc-wl]')) {
      const wl = tr.getAttribute('data-orch-alloc-wl');
      workloads[wl] = {
        weight: Number(tr.querySelector('.orch-alloc-weight').value || 0),
        min_tokens: Number(tr.querySelector('.orch-alloc-min').value || 0),
        max_tokens: Number(tr.querySelector('.orch-alloc-max').value || 0),
        on_exhausted: tr.querySelector('.orch-alloc-onex').value,
      };
    }
    state.orchSaving = true;
    try {
      await api.orchestrationBudgetSave({
        tokens: Number((root.querySelector('#orch-token-limit') || {}).value || 0),
        allocation: {
          mode: (root.querySelector('#orch-alloc-mode') || {}).value || 'static',
          soft_ratio: Number((root.querySelector('#orch-soft-ratio') || {}).value || 0.9),
          workloads,
        },
      });
      toast('配分を保存しました', true);
    } finally { state.orchSaving = false; }
    await refreshOrchestration();
    renderOrchestration();
  }));

  const rebalanceBtn = root.querySelector('#btn-orch-rebalance');
  if (rebalanceBtn) rebalanceBtn.addEventListener('click', () => guard('再配分', async () => {
    await api.orchestrationRebalance();
    toast('再配分しました', true);
    await refreshOrchestration();
    renderOrchestration();
  }));

  const calibrateBtn = root.querySelector('#btn-orch-calibrate');
  if (calibrateBtn) calibrateBtn.addEventListener('click', () => guard('レート較正', async () => {
    await api.orchestrationCalibrate();
    toast('レートを較正しました', true);
    await refreshOrchestration();
    renderOrchestration();
  }));

  // 割当（control）の保存
  const controlSave = root.querySelector('#btn-orch-control-save');
  if (controlSave) controlSave.addEventListener('click', () => guard('割当の保存', async () => {
    const workloads = {};
    for (const block of root.querySelectorAll('[data-orch-ctrl-wl]')) {
      const wl = block.getAttribute('data-orch-ctrl-wl');
      const cli = block.querySelector('.orch-ctrl-cli').value.trim();
      const model = block.querySelector('.orch-ctrl-model').value.trim();
      const degModel = block.querySelector('.orch-ctrl-degraded-model').value.trim();
      const wc = {
        agent_cli: cli || null,
        model: model || null,
        degraded: degModel ? { model: degModel } : null,
      };
      // 用途 / ロール別の上書き（agents.<key>）を集める。削除チェックは null（＝キー削除）。
      const agents = {};
      for (const arow of block.querySelectorAll('.orch-agent-row')) {
        const key = arow.getAttribute('data-orch-key');
        if (arow.querySelector('.orch-agent-remove').checked) {
          agents[key] = null;
          continue;
        }
        const acli = arow.querySelector('.orch-agent-cli').value.trim();
        const amodel = arow.querySelector('.orch-agent-model').value.trim();
        agents[key] = { agent_cli: acli || null, model: amodel || null };
      }
      const addRow = block.querySelector('.orch-agent-add');
      if (addRow) {
        const nk = addRow.querySelector('.orch-agent-new-key').value.trim();
        if (nk) {
          agents[nk] = {
            agent_cli: addRow.querySelector('.orch-agent-new-cli').value.trim() || null,
            model: addRow.querySelector('.orch-agent-new-model').value.trim() || null,
          };
        }
      }
      if (Object.keys(agents).length) wc.agents = agents;
      workloads[wl] = wc;
    }
    state.orchSaving = true;
    try {
      await api.orchestrationControlSave({ workloads });
      toast('割当を保存しました', true);
    } finally { state.orchSaving = false; }
    await refreshOrchestration();
    renderOrchestration();
  }));

  // グローバル指示（agent-instructions）の保存
  const skillInput = root.querySelector('#orch-skill-add');
  const skillAdd = root.querySelector('#btn-orch-skill-add');
  const skillList = root.querySelector('#orch-skill-selected');
  const skillDescription = root.querySelector('#orch-skill-candidate-description');
  const skillInventory = new Map((state.orchSkillsInventory || []).map((s) => [String(s.name), s]));
  const updateSkillAdd = () => {
    if (skillAdd) skillAdd.disabled = !skillInput || !skillInput.value.trim();
    if (skillDescription) {
      const candidate = skillInput ? skillInventory.get(skillInput.value.trim()) : null;
      skillDescription.textContent = shortSkillDescription(candidate && candidate.description);
      skillDescription.hidden = !skillDescription.textContent;
    }
  };
  const addSkill = () => {
    if (!skillInput || !skillList) return;
    const name = skillInput.value.trim();
    if (!name) return;
    const candidate = skillInventory.get(name);
    if (!candidate) {
      toast('候補にあるスキル名を選択してください');
      skillInput.focus();
      return;
    }
    const existing = [...skillList.querySelectorAll('.orch-skill-row')]
      .find((row) => row.getAttribute('data-orch-skill') === name);
    if (existing) {
      toast('そのスキルは追加済みです');
      existing.querySelector('.orch-skill-note').focus();
      return;
    }
    const empty = skillList.querySelector('.orch-skill-empty');
    if (empty) empty.remove();
    skillList.insertAdjacentHTML('beforeend', orchSkillRowHtml(name, '', candidate));
    skillInput.value = '';
    updateSkillAdd();
    state.orchInstructionsDirty = true;
    skillInput.focus();
  };
  if (skillInput) {
    skillInput.addEventListener('input', updateSkillAdd);
    skillInput.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      addSkill();
    });
  }
  if (skillAdd) skillAdd.addEventListener('click', addSkill);
  if (skillList) skillList.addEventListener('click', (event) => {
    const remove = event.target.closest('.orch-skill-remove');
    if (!remove) return;
    remove.closest('.orch-skill-row').remove();
    if (!skillList.querySelector('.orch-skill-row')) {
      skillList.innerHTML = '<p class="muted orch-skill-empty">追加されたスキルはありません。</p>';
    }
    state.orchInstructionsDirty = true;
  });
  for (const field of root.querySelectorAll(
    '#orch-instr-enabled, #orch-instr-text, #orch-instr-allow, #orch-instr-deny, #orch-instr-max, .orch-skill-note'
  )) {
    field.addEventListener('input', () => { state.orchInstructionsDirty = true; });
    field.addEventListener('change', () => { state.orchInstructionsDirty = true; });
  }

  const instrSave = root.querySelector('#btn-orch-instr-save');
  if (instrSave) instrSave.addEventListener('click', () => guard('共通指示の保存', async () => {
    const skills = [];
    for (const row of root.querySelectorAll('.orch-skill-row')) {
      const name = row.getAttribute('data-orch-skill');
      const note = row.querySelector('.orch-skill-note').value.trim();
      skills.push(note ? { name, note } : name);
    }
    const allow = (root.querySelector('#orch-instr-allow').value || '')
      .split(',').map((s) => s.trim()).filter(Boolean);
    const denyNote = root.querySelector('#orch-instr-deny').value.trim();
    const payload = {
      enabled: root.querySelector('#orch-instr-enabled').checked,
      text: root.querySelector('#orch-instr-text').value,
      skills,
      tools: { allow, deny_note: denyNote },
      max_chars: Number(root.querySelector('#orch-instr-max').value || 2000),
    };
    state.orchSaving = true;
    try {
      await api.orchestrationInstructionsSave(payload);
      toast('共通指示を保存しました', true);
      state.orchInstructionsDirty = false;
    } finally { state.orchSaving = false; }
    await refreshOrchestration();
    renderOrchestration();
  }));

  // セッション開始コマンド（agent-session-commands）の編集・プレビュー・保存
  const sessList = root.querySelector('#orch-sess-list');
  const markSessionDirty = () => { state.orchSessionDirty = true; };
  // 実行方法の切替でラベルと process 専用欄の出し分けを合わせる（保存前に見た目で分かるように）。
  const syncSessionRow = (row) => {
    const mode = row.querySelector('.orch-sess-mode').value === 'chat' ? 'chat' : 'process';
    row.setAttribute('data-orch-sess-mode', mode);
    row.querySelector('.orch-sess-run-label').textContent =
      mode === 'chat' ? 'エージェントへ送る内容' : '実行するコマンド';
    row.querySelector('.orch-sess-process-only').hidden = mode === 'chat';
    const strategy = row.querySelector('.orch-sess-strategy');
    if (strategy) strategy.hidden = mode !== 'chat';
  };
  if (sessList) {
    sessList.addEventListener('input', markSessionDirty);
    sessList.addEventListener('change', (event) => {
      markSessionDirty();
      const mode = event.target.closest('.orch-sess-mode');
      if (mode) syncSessionRow(mode.closest('.orch-sess-row'));
    });
    sessList.addEventListener('click', (event) => {
      const remove = event.target.closest('.orch-sess-remove');
      if (!remove) return;
      remove.closest('.orch-sess-row').remove();
      if (!sessList.querySelector('.orch-sess-row')) {
        sessList.innerHTML = '<p class="muted orch-sess-empty">登録されたコマンドはありません。</p>';
      }
      markSessionDirty();
    });
  }
  const sessAdd = root.querySelector('#btn-orch-sess-add');
  if (sessAdd && sessList) sessAdd.addEventListener('click', () => {
    const empty = sessList.querySelector('.orch-sess-empty');
    if (empty) empty.remove();
    const index = sessList.querySelectorAll('.orch-sess-row').length;
    sessList.insertAdjacentHTML('beforeend', orchSessionRowHtml({ mode: 'process', on_error: 'warn' }, index));
    markSessionDirty();
    const added = sessList.lastElementChild;
    if (added) added.querySelector('.orch-sess-id-input').focus();
  });
  for (const field of root.querySelectorAll('#orch-sess-enabled, #orch-sess-max-total')) {
    field.addEventListener('input', markSessionDirty);
    field.addEventListener('change', markSessionDirty);
  }

  const sessPreview = root.querySelector('#btn-orch-sess-preview');
  if (sessPreview) sessPreview.addEventListener('click', () => guard('実行内容の確認', async () => {
    const engine = (root.querySelector('#orch-sess-preview-engine') || {}).value || 'kiro-loop';
    const data = readSessionCommandsForm(root);
    let entries;
    try {
      entries = await api.orchestrationSessionCommandsPreview({
        data,
        // 実際のセッションでは cwd / workspace は起動時に決まる。ここでは何が入るかを示す。
        context: { engine, workload: engine === 'agent-flow' ? 'flow' : 'routine' },
      });
      state.orchSessionPreview = { engine, entries };
    } catch (err) {
      state.orchSessionPreview = { engine, error: err.message };
    }
    const body = root.querySelector('#orch-sess-preview-body');
    if (body) body.innerHTML = orchSessionPreviewHtml();
  }));

  const sessSave = root.querySelector('#btn-orch-sess-save');
  if (sessSave) sessSave.addEventListener('click', () => guard('セッション開始時のコマンドの保存', async () => {
    const payload = readSessionCommandsForm(root);
    state.orchSaving = true;
    try {
      await api.orchestrationSessionCommandsSave(payload);
      toast('セッション開始時のコマンドを保存しました', true);
      state.orchSessionDirty = false;
    } finally { state.orchSaving = false; }
    await refreshOrchestration();
    renderOrchestration();
  }));

  // lifecycle 操作
  for (const btn of root.querySelectorAll('[data-orch-lc]')) {
    btn.addEventListener('click', () => guard('実行サービスの操作', async () => {
      const workload = btn.getAttribute('data-orch-wl');
      const action = btn.getAttribute('data-orch-lc');
      if (action === 'stop') {
        const yes = await confirmDialog(
          `「${amigosWorkloadLabel(workload)}」のエージェント呼び出しを、この端末全体で停止します。\n\n` +
          '個別のrunを止める操作ではありません。進行中のrunも次の呼び出しで失敗する場合があります。よろしいですか？'
        );
        if (!yes) return;
      }
      await api.orchestrationLifecycle({ workload, action });
      toast(action === 'run' ? '端末全体で実行を許可しました' : '端末全体で実行を停止しました', true);
      await refreshOrchestration();
      renderOrchestration();
    }));
  }

  // 追加エージェントの保存・削除
  for (const btn of root.querySelectorAll('.orch-dropin-save')) {
    btn.addEventListener('click', () => guard('エージェント設定の保存', async () => {
      const details = btn.closest('.orch-dropin');
      const ta = details.querySelector('.orch-dropin-spec');
      let spec;
      try { spec = JSON.parse(ta.value); } catch (e) { throw new Error(`JSON として読めません: ${e.message}`); }
      await api.orchestrationAgentSave({ name: btn.getAttribute('data-orch-name'), dir: btn.getAttribute('data-orch-dir'), spec });
      toast('エージェント設定を保存しました', true);
      await refreshOrchestration();
      renderOrchestration();
    }));
  }
  for (const btn of root.querySelectorAll('.orch-dropin-delete')) {
    btn.addEventListener('click', () => guard('エージェント設定の削除', async () => {
      await api.orchestrationAgentDelete({ name: btn.getAttribute('data-orch-name'), dir: btn.getAttribute('data-orch-dir') });
      toast('エージェント設定を削除しました', true);
      await refreshOrchestration();
      renderOrchestration();
    }));
  }
  const newSave = root.querySelector('#btn-orch-new-save');
  if (newSave) newSave.addEventListener('click', () => guard('エージェントの追加', async () => {
    const name = (root.querySelector('#orch-new-name') || {}).value || '';
    let spec;
    try { spec = JSON.parse((root.querySelector('#orch-new-spec') || {}).value || '{}'); }
    catch (e) { throw new Error(`JSON として読めません: ${e.message}`); }
    await api.orchestrationAgentSave({ name: name.trim(), spec });
    toast('エージェントを追加しました', true);
    await refreshOrchestration();
    renderOrchestration();
  }));
}

function coworkStatusClass(status) {
  const s = String(status || 'unknown');
  if (s === 'running') return 'st-running';
  if (s === 'failed') return 'st-failed';
  if (s === 'done') return 'st-done';
  if (s === 'idle') return 'st-ready';
  return 'st-draft';
}

function coworkRepoLabel(repo) {
  const s = String(repo || '');
  if (!s) return '';
  const parts = s.replace(/\\/g, '/').split('/').filter(Boolean);
  return parts.length ? parts[parts.length - 1] : s;
}

// リポジトリパスの比較キー。WSL UNC（\\wsl.localhost\<distro>\...）と POSIX、
// /mnt/<drive> と Windows ドライブ表記を同一視する（main 側 _pathKey の縮約版）。
function coworkPathKey(p) {
  let s = String(p || '').trim().replace(/\\/g, '/');
  if (!s) return '';
  const unc = s.match(/^\/\/wsl(?:\$|\.localhost)\/[^/]+(\/.*)?$/i);
  if (unc) s = unc[1] || '/';
  const mnt = s.match(/^\/mnt\/([a-z])(\/.*)?$/i);
  if (mnt) s = `${mnt[1]}:${mnt[2] || '/'}`;
  return s.replace(/\/+/g, '/').replace(/\/$/, '').toLowerCase();
}

// 表示対象の絞り込み: 選択中プロジェクトの作業だけを出す。
// 返り値は { item, index } — index は draft 配列の位置（編集/削除がそのまま使える）。
function coworkVisibleEntries(draft, selectedDir) {
  const entries = (draft || []).map((item, index) => ({ item, index }));
  if (!selectedDir) return [];
  const key = coworkPathKey(selectedDir);
  return entries.filter(({ item }) => coworkPathKey(item.repo || item.cwd) === key);
}

function coworkEntryId(item, index) {
  return String((item && (item.id || item.name)) || `${(item && item.type) || 'loop'}-${index + 1}`);
}

function coworkSelectedEntry(entries, projectFolder) {
  if (!entries.length) return null;
  const projectKey = coworkPathKey(projectFolder);
  const selectedId = state.coworkSelections[projectKey];
  const selected = entries.find(({ item, index }) => coworkEntryId(item, index) === selectedId);
  if (selected) return selected;
  const observed = new Map(((state.cowork && state.cowork.items) || []).map((item, index) => [coworkEntryId(item, index), item]));
  const preferred = entries.find(({ item, index }) => {
    const live = observed.get(coworkEntryId(item, index)) || item;
    const st = live.state || {};
    return st.running || ['failed', 'error', 'blocked'].includes(String(st.status || ''));
  }) || entries[0];
  state.coworkSelections[projectKey] = coworkEntryId(preferred.item, preferred.index);
  return preferred;
}

function selectCoworkRoutine(id, { openStatus = false } = {}) {
  const folder = selectedProjectFolder();
  const entries = coworkVisibleEntries(coworkDraft(), folder);
  const entry = entries.find(({ item, index }) => coworkEntryId(item, index) === String(id));
  if (!entry) return;
  state.coworkSelections[coworkPathKey(folder)] = coworkEntryId(entry.item, entry.index);
  if (activeTab() === 'cowork') updateCoworkSelectedDetail(entry, folder);
  if (openStatus) {
    openKiroLoopTerminal({
      id: coworkEntryId(entry.item, entry.index),
      repo: entry.item.repo || entry.item.cwd || '',
      name: entry.item.name || coworkEntryId(entry.item, entry.index),
    });
  }
}

function coworkRoutineSelectorHtml(
  entries,
  selectedId,
  label = '定常業務を選択',
  labelId = 'cowork-routine-picker-label',
  searchKey = 'cowork'
) {
  if (!entries.length) return '';
  return `<section class="cowork-routine-picker" aria-labelledby="${esc(labelId)}">
    <div class="cowork-routine-picker-heading">
      <div><span class="summary-kicker">選択</span><h3 id="${esc(labelId)}">${esc(label)}</h3></div>
      <div class="cowork-routine-tools">
        <input type="search" class="cowork-routine-search" data-cowork-search="${esc(searchKey)}"
          aria-label="定常業務を名前で検索" placeholder="名前で検索" value="${esc(state.coworkSearches[searchKey] || '')}">
        <span class="muted" data-cowork-visible-count>${entries.length} 件</span>
      </div>
    </div>
    <div id="cowork-routine-selector-${esc(searchKey)}" class="cowork-routine-selector" data-ui-scroll-key role="tablist" aria-label="${esc(label)}">${entries.map(({ item, index }) => {
      const id = coworkEntryId(item, index);
      const st = item.state || {};
      const running = !!st.running;
      const status = running ? 'running' : (st.status || 'unknown');
      const selected = id === selectedId;
      return `<button type="button" class="cowork-routine-option ${selected ? 'selected' : ''}"
        role="tab" aria-selected="${selected}" tabindex="${selected ? '0' : '-1'}" data-cowork-select="${esc(id)}"
        data-cowork-search-text="${esc(String(item.name || id).toLocaleLowerCase('ja-JP'))}" aria-label="${esc(item.name || id)}">
        <span class="cowork-routine-option-head"><strong title="${esc(item.name || id)}">${esc(item.name || id)}</strong>
          <span class="status-chip ${coworkStatusClass(status)}">${esc(statusLabel(status))}</span></span>
        <span class="cowork-routine-option-meta"><span>${esc(item.schedule || '予定なし')}</span><span>${st.lastLogAt ? `最終 ${esc(fmtAgo(st.lastLogAt))}` : '未実行'}</span></span>
      </button>`;
    }).join('')}</div>
  </section>`;
}

function applyCoworkRoutineFilter(root) {
  const input = root.querySelector('[data-cowork-search]');
  if (!input) return;
  const query = input.value.trim().toLocaleLowerCase('ja-JP');
  state.coworkSearches[input.dataset.coworkSearch] = input.value;
  let visible = 0;
  for (const button of root.querySelectorAll('[data-cowork-select]')) {
    const matches = !query || String(button.dataset.coworkSearchText || '').includes(query);
    button.hidden = !matches;
    if (matches) visible += 1;
  }
  const count = root.querySelector('[data-cowork-visible-count]');
  if (count) count.textContent = query ? `${visible} / ${root.querySelectorAll('[data-cowork-select]').length} 件` : `${visible} 件`;
}

function bindCoworkRoutineSelector(root) {
  const buttons = [...root.querySelectorAll('[data-cowork-select]')];
  const search = root.querySelector('[data-cowork-search]');
  if (search) search.addEventListener('input', () => applyCoworkRoutineFilter(root));
  applyCoworkRoutineFilter(root);
  buttons.forEach((button) => {
    button.addEventListener('click', () => selectCoworkRoutine(button.dataset.coworkSelect));
    button.addEventListener('keydown', (ev) => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(ev.key)) return;
      ev.preventDefault();
      const navigable = buttons.filter((candidate) => !candidate.hidden);
      const currentIndex = navigable.indexOf(button);
      if (currentIndex < 0 || !navigable.length) return;
      const backward = ev.key === 'ArrowLeft' || ev.key === 'ArrowUp';
      const nextIndex = ev.key === 'Home'
        ? 0
        : ev.key === 'End'
          ? navigable.length - 1
          : (currentIndex + (backward ? -1 : 1) + navigable.length) % navigable.length;
      const next = navigable[nextIndex];
      selectCoworkRoutine(next.dataset.coworkSelect);
      next.focus();
    });
  });
}

function coworkHasProjectConfig(cowork, projectFolder) {
  const key = coworkPathKey(projectFolder);
  return !!key && (
    ((cowork && cowork.discoveredRepos) || []).some((repo) => coworkPathKey(repo) === key)
    || ((cowork && cowork.items) || []).some((item) => coworkPathKey(item.repo || item.cwd) === key)
  );
}

function coworkRunBannerHtml() {
  const r = state.coworkRun;
  if (!r) return '';
  if (r.phase === 'running') {
    return `<div class="cowork-run-banner running" role="status">「${esc(r.name || r.id)}」を実行中…</div>`;
  }
  if (r.phase === 'ok') {
    if (r.launched) {
      return `<div class="cowork-run-banner ok" role="status">「${esc(r.name || r.id)}」を別ウィンドウ（WSL tmux）で開始しました — 開いたウィンドウで進行を確認できます</div>`;
    }
    return `<div class="cowork-run-banner ok" role="status">「${esc(r.name || r.id)}」の実行が完了しました${r.message ? ` — ${esc(r.message)}` : ''}</div>`;
  }
  return `<div class="cowork-run-banner error" role="alert">「${esc(r.name || r.id)}」の実行に失敗しました${r.message ? `: ${esc(r.message)}` : ''}</div>`;
}

function coworkSelectedDetailHtml(entry, observed, busyId) {
  if (!entry) return '';
  const { item, index } = entry;
  const id = coworkEntryId(item, index);
  const live = observed.get(id) || {};
  const st = live.state || item.state || {};
  const discovered = item.source === 'discovered';
  const pairedLoop = !!(item._src && item._src.loop);
  const disabledWork = item.enabled === false;
  const running = !!st.running || busyId === id;
  const status = running ? 'running' : (st.status || 'unknown');
  const run = state.coworkRun && String(state.coworkRun.id) === id ? state.coworkRun : null;
  const typeDetail = workTypeLabel(item.type);
  const lastResult = running
    ? '実行中です'
    : run && run.phase === 'error'
      ? run.message || '実行に失敗しました'
      : st.lastLogAt
        ? `${statusLabel(status)}・${fmtTime(st.lastLogAt)}`
        : 'まだ実行記録がありません';
  return `<article class="cowork-selected-detail ${running ? 'is-running' : ''} ${run && run.phase === 'error' ? 'is-error' : ''}" aria-labelledby="cowork-selected-title">
    <div class="cowork-selected-hero">
      <div class="cowork-selected-heading">
        <span class="summary-kicker">選択中の定常業務</span>
        <h2 id="cowork-selected-title" title="${esc(item.name || id)}">${esc(item.name || id)}</h2>
        <div class="row"><span class="status-chip ${coworkStatusClass(status)}">${esc(statusLabel(status))}</span>
          <span class="label-chip">${esc(workTypeLabel(item.type))}</span>${disabledWork ? '<span class="label-chip">無効</span>' : ''}</div>
      </div>
      <button class="cowork-primary-run" data-cowork-run="${esc(id)}" data-cowork-type="${esc(item.type || 'loop')}" data-cowork-name="${esc(item.name || id)}" ${busyId || disabledWork ? 'disabled' : ''}>${busyId === id ? '実行中…' : '今すぐ実行'}</button>
    </div>
    <div class="cowork-status-grid">
      <div><span>現在</span><strong>${esc(statusLabel(status))}</strong></div>
      <div><span>最終結果</span><strong title="${esc(lastResult)}">${esc(lastResult)}</strong></div>
      <div><span>実行予定</span><strong>${esc(item.schedule || '手動実行')}</strong></div>
      <div><span>対象</span><strong title="${esc(item.repo || '')}">${esc(coworkRepoLabel(item.repo))}</strong></div>
    </div>
    <section class="cowork-latest-result">
      <div><span class="summary-kicker">最新結果</span><p>${esc(lastResult)}</p></div>
      <button data-cowork-history="${esc(id)}" data-cowork-name="${esc(item.name || id)}" ${api.coworkItemLogs ? '' : 'disabled'}>履歴とログ</button>
    </section>
    ${run && run.phase === 'error' ? `<p class="cowork-item-error" role="alert">${esc(run.message || '実行できませんでした。')}</p>` : ''}
    <section class="cowork-more-details" aria-label="設定とその他の操作">
      <h3>設定とその他の操作</h3>
      <dl class="cowork-facts">
        <div><dt>種類</dt><dd>${esc(typeDetail)}</dd></div>
        <div><dt>登録元</dt><dd>${discovered ? '設定ファイル' : '手動登録'}</dd></div>
        <div><dt>定期実行</dt><dd>${pairedLoop ? '有効' : (item.schedule ? '設定あり' : 'なし')}</dd></div>
        <div><dt>リポジトリ</dt><dd title="${esc(item.repo || '')}">${esc(item.repo || '未設定')}</dd></div>
      </dl>
      <div class="cowork-secondary-actions">
        ${(item.type !== 'state-machine' || pairedLoop) && item.repo && api.kiroLoopListSessions
          ? `<button data-cowork-term-repo="${esc(item.repo)}" data-cowork-term-name="${esc(item.name || id)}" data-cowork-term-id="${esc(id)}">実行状況を見る</button>`
          : ''}
        <button data-cowork-edit="${index}" ${busyId ? 'disabled' : ''}>編集</button>
        ${discovered ? '' : `<button data-cowork-delete="${index}" ${busyId ? 'disabled' : ''}>削除</button>`}
      </div>
    </section>
  </article>`;
}

function updateCoworkSelectedDetail(entry, folder) {
  const root = $('tab-cowork');
  const slot = $('cowork-selected-slot');
  if (!root || !slot || !entry) return;
  const selectedId = coworkEntryId(entry.item, entry.index);
  for (const button of root.querySelectorAll('[data-cowork-select]')) {
    const selected = button.dataset.coworkSelect === selectedId;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
  }
  const observed = new Map(((state.cowork && state.cowork.items) || [])
    .map((item, index) => [coworkEntryId(item, index), item]));
  const busyId = state.coworkRun && state.coworkRun.phase === 'running' ? String(state.coworkRun.id) : '';
  slot.innerHTML = coworkSelectedDetailHtml(entry, observed, busyId);
  slot.scrollTop = 0;
  bindCoworkDetailActions(slot, folder);
}

function bindCoworkDetailActions(root, folder) {
  root.querySelectorAll('[data-cowork-history]').forEach((btn) => btn.addEventListener('click', () =>
    openCoworkHistory(btn.dataset.coworkHistory, btn.dataset.coworkName || '')));
  root.querySelectorAll('[data-cowork-edit]').forEach((btn) => btn.addEventListener('click', () =>
    openCoworkWorkDialog(Number(btn.dataset.coworkEdit))));
  root.querySelectorAll('[data-cowork-delete]').forEach((btn) => btn.addEventListener('click', () => {
    const index = Number(btn.dataset.coworkDelete);
    const deleting = coworkDraft()[index];
    coworkDraft().splice(index, 1);
    if (deleting) {
      state.coworkHistoryCache.delete(coworkEntryId(deleting, index));
      delete state.coworkSelections[coworkPathKey(folder)];
    }
    updateCoworkTabVisibility();
    renderCowork();
  }));
  root.querySelectorAll('[data-cowork-run]').forEach((btn) => btn.addEventListener('click', async () => {
    const id = btn.dataset.coworkRun;
    const type = btn.dataset.coworkType;
    const name = btn.dataset.coworkName || id;
    const routine = coworkDraft().find((item, index) => coworkEntryId(item, index) === id);
    state.coworkRun = { id, name, phase: 'running', message: '', detail: '', at: Date.now() };
    renderCowork();
    let res;
    try {
      res = type === 'state-machine'
        ? await api.coworkRunStateMachine(id, '')
        : await api.coworkRunLoop(id);
    } catch (err) {
      res = { ok: false, error: err.message || String(err) };
    }
    const detail = String((res && (res.stderr || res.stdout || res.error)) || '').trim();
    const message = detail ? detail.slice(0, 240) : (res && res.ok ? '' : 'エラー詳細なし');
    state.coworkRun = {
      id,
      name,
      phase: res && res.ok ? 'ok' : 'error',
      launched: !!(res && res.launched),
      message,
      detail: detail.slice(0, 1200),
      at: Date.now(),
    };
    state.coworkHistoryCache.delete(id);
    if (routine) state.kiroLoopStateCache.delete(coworkPathKey(routine.repo || routine.cwd));
    toast(
      res && res.ok
        ? (res.launched
          ? `「${name}」を別ウィンドウ（WSL tmux）で開始しました`
          : `「${name}」を実行しました`)
        : `「${name}」を実行できませんでした: ${message}`,
      !!(res && res.ok)
    );
    await refreshCowork({ probe: true });
    renderCowork();
  }));
  root.querySelectorAll('[data-cowork-term-repo]').forEach((btn) => btn.addEventListener('click', () => {
    openKiroLoopTerminal({
      id: btn.dataset.coworkTermId || '',
      repo: btn.dataset.coworkTermRepo,
      name: btn.dataset.coworkTermName || '',
    });
  }));
}

function renderCowork() {
  const ui = captureUiState();
  const el = $('tab-cowork');
  if (!el) return;
  updateCoworkTabVisibility();
  const cw = state.cowork;
  const draft = coworkDraft();
  const observed = new Map(((cw && cw.items) || []).map((x) => [String(x.id), x]));
  const busyId = state.coworkRun && state.coworkRun.phase === 'running' ? String(state.coworkRun.id) : '';
  if (cw && cw.error) {
    el.innerHTML = `<div class="empty"><strong>定常業務を読み込めませんでした</strong><span>${esc(cw.error)}</span></div>`;
    restoreUiState(ui);
    return;
  }
  // 選択中プロジェクトの作業だけを表示する（従来は全プロジェクトの作業が常に並んでいた）。
  const folder = selectedProjectFolder();
  const entries = (coworkHasProjectConfig(cw, folder) || state.coworkForcedOpen)
    ? coworkVisibleEntries(draft, folder) : [];
  const selected = coworkSelectedEntry(entries, folder);
  const selectedId = selected ? coworkEntryId(selected.item, selected.index) : '';
  const scopeLabel = `このプロジェクトの作業 ${entries.length} 件`;
  el.innerHTML = `
    <div class="cowork-shell">
      <header class="cowork-header">
        <div>
          <span class="summary-kicker">自動化</span>
          <h2>定常業務</h2>
          <p class="muted">繰り返し実行する作業を確認・実行できます。</p>
        </div>
        <div class="row">
          <button id="btn-cowork-add">追加</button>
          <button id="btn-cowork-save">保存</button>
          <button id="btn-cowork-refresh" title="最新の状態を確認">更新</button>
        </div>
      </header>
      <div class="cowork-scope muted">
        <span>${esc(scopeLabel)}</span>
      </div>
      ${coworkRunBannerHtml()}
      ${entries.length ? `<div class="cowork-split-view">
        <section class="cowork-list-pane" aria-label="定常業務の一覧">${coworkRoutineSelectorHtml(entries, selectedId)}</section>
        <div id="cowork-selected-slot" class="cowork-detail-pane" data-ui-scroll-key>${coworkSelectedDetailHtml(selected, observed, busyId)}</div>
      </div>`
      : '<div class="empty"><strong>このプロジェクトに登録された定常業務はありません</strong><span>プロジェクトの設定ファイルに作業を追加してください。</span></div>'}
    </div>`;
  bindCoworkRoutineSelector(el);
  const addBtn = $('btn-cowork-add');
  if (addBtn) addBtn.addEventListener('click', () => openCoworkWorkDialog(-1));
  const saveBtn = $('btn-cowork-save');
  if (saveBtn) saveBtn.addEventListener('click', openCoworkSaveDialog);
  const refreshBtn = $('btn-cowork-refresh');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      state.coworkHistoryCache.clear();
      await refreshCowork({ probe: true, forceDiscover: true });
      state.coworkDraft = null;
      renderCowork();
    });
  }
  bindCoworkDetailActions(el, folder);
  restoreUiState(ui);
}
