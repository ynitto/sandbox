'use strict';

// 記録の数値は main 経由の観測値。実行制御の入力は保存までドラフトとして保持する。
(function initAudit() {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const AI_LABEL = { claude: 'Claude', codex: 'Codex', copilot: 'Copilot', kiro: 'Kiro', herd: 'ローカル' };
  const WORKLOAD_LABEL = UsagePresentation.WORKLOAD_LABEL;
  let context = {}, quotaData = null, quotaPromise = null, loadingKey = '', summaryRequest = 0;
  let originalTemporary = null, manualAgent = '', filled = false, draftChanged = false;
  const state = { status: null, summary: null, error: '', busy: false };

  function tokens(n) {
    const v = Number(n) || 0;
    if (v >= 1000000000) return `${(v / 1000000000).toFixed(1)}B`;
    if (v >= 1000000) return `${(v / 1000000).toFixed(1)}M`;
    if (v >= 1000) return `${(v / 1000).toFixed(1).replace(/\.0$/, '')}k`;
    return String(v);
  }
  function date(value) {
    return Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('ja-JP', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
  }
  function currentLimits() { return Allocation.limits(quotaData?.agentLimits, context.getConfig?.().audit?.manualLimits); }
  function setError(error) {
    $('usage-error').hidden = !error;
    $('usage-error').textContent = error ? error.message || String(error) : '';
  }
  function renderStatus() {
    const s = state.status;
    $('audit-run').disabled = !!(s?.running || state.busy);
    $('audit-status').textContent = s?.running ? '記録を集めています…'
      : s?.lastError || (quotaData?.limitsError ? '利用枠を取得できませんでした。手動で入力できます'
        : quotaData?.available === false ? '利用枠の収集が未設定です。手動で入力できます'
          : s?.lastRunAt ? `${Fmt.checkedAt(s.lastRunAt)} に集めました` : '');
  }
  function renderLimits() {
    const box = $('audit-limits'), editor = $('usage-manual-row');
    const focused = editor.contains(document.activeElement) ? document.activeElement : null;
    const limits = currentLimits();
    const names = [...new Set([...Object.keys(AI_LABEL), ...limits.map(r => r.agent_cli)])].filter(n => !Allocation.isLocal(n));
    const result = [];
    for (const name of names) {
      const observed = limits.filter(r => r.agent_cli === name);
      // A manual snapshot replaces an expired observation for the same service.
      const rows = observed.some(r => r.quota_source === 'manual' && Allocation.validLimit(r))
        ? observed.filter(r => r.quota_source === 'manual') : observed;
      for (const [index, item] of (rows.length ? rows : [null]).entries()) {
        const tr = el('tr');
        const service = el('td', '', AI_LABEL[name] || name);
        if (rows.length > 1) service.append(el('small', 'sub', `利用枠 ${index + 1}`));
        const percent = Allocation.validLimit(item) ? Math.round((100 - Number(item.quota_used_percent)) * 10) / 10 : null;
        const expired = item && Date.parse(item.reset_at) <= Date.now();
        const remaining = el('td', '', percent === null ? expired ? '更新待ち' : '未取得' : `残り ${percent}%${item.quota_source === 'manual' ? '（手動）' : ''}`);
        if (percent !== null) {
          const bar = el('progress', 'usage-meter'); bar.max = 100; bar.value = percent;
          bar.setAttribute('aria-label', `${AI_LABEL[name] || name} の利用枠残量`);
          if (percent <= 10) bar.classList.add('is-high');
          remaining.append(bar);
        }
        if (item?.observed_at) remaining.append(el('small', 'sub', `${item.quota_source === 'manual' ? '申告' : '取得'} ${Fmt.checkedAt(item.observed_at)}`));
        const reset = el('td', '', item?.reset_source === 'period' ? '—' : date(item?.reset_at));
        if (item?.reset_estimated) reset.append(el('small', 'sub', '推定'));
        const actions = el('td');
        if (index === 0) {
          const edit = el('button', 'small quiet', '手動入力'); edit.type = 'button'; edit.dataset.manualAgent = name;
          edit.setAttribute('aria-label', `${AI_LABEL[name] || name} の利用枠を手動入力`);
          edit.onclick = () => {
            manualAgent = name; tr.after(editor); editor.hidden = false; setError(null);
            $('usage-manual-remaining').value = ''; $('usage-manual-reset').value = '';
            $('usage-manual-remaining').focus();
          };
          actions.append(edit);
        }
        tr.append(service, remaining, reset, actions); result.push(tr);
        if (index === 0 && name === manualAgent) result.push(editor);
      }
    }
    if (!result.includes(editor)) result.push(editor);
    box.replaceChildren(...result);
    focused?.focus({ preventScroll: true });
    const local = context.localAvailable?.();
    $('usage-local-status').textContent = local === true ? '利用可能' : local === false ? '準備が必要' : '確認中';
    const valid = limits.filter(r => Allocation.validLimit(r));
    const risk = valid.filter(r => Number(r.quota_used_percent) >= 90).sort((a, b) => b.quota_used_percent - a.quota_used_percent)[0];
    const preference = Allocation.mode(context.getConfig?.().allocation);
    $('usage-indicator').textContent = '利用状況';
    $('usage-open').title = quotaData?.limitsError ? '利用枠を取得できませんでした'
      : preference !== 'configured' ? Allocation.LABELS[preference]
      : risk ? `${AI_LABEL[risk.agent_cli] || risk.agent_cli} 残りわずか` : '';
    $('usage-quota-note').textContent = risk ? `${AI_LABEL[risk.agent_cli] || risk.agent_cli} 残り ${100 - Number(risk.quota_used_percent)}% · リセット ${date(risk.reset_at)}`
      : valid.length ? '利用枠に余裕があります' : '利用枠は未取得です';
    const cfg = context.getConfig?.().allocation;
    $('usage-active-allocation').textContent = `使用方針：${Allocation.LABELS[Allocation.normalize(cfg).mode]}${Allocation.active(cfg) ? ' · 一時的にローカル優先' : ''}`;
    const select = $('usage-reset-agent'), previous = select.value;
    select.replaceChildren(...valid.filter(r => Date.parse(r.reset_at) > Date.now() && r.reset_source !== 'period')
      .map(r => new Option(`${AI_LABEL[r.agent_cli] || r.agent_cli} · ${date(r.reset_at)}`, r.reset_at)));
    // Do not silently substitute a different deadline while a form is being edited.
    if (previous && ![...select.options].some(o => o.value === previous)) select.append(new Option(`選択した利用枠 · ${date(previous)}`, previous));
    if (previous) select.value = previous;
  }

  function allocationPatch() {
    let temporary = null;
    const until = $('usage-until').value;
    if (until === 'keep') temporary = originalTemporary;
    else if (until !== 'off') {
      let deadline = null;
      if (until === 'today') { const end = new Date(); end.setHours(24, 0, 0, 0); deadline = end.toISOString(); }
      else if (until === 'reset') {
        deadline = $('usage-reset-agent').value;
        if (!currentLimits().some(r => r.reset_at === deadline && Allocation.validLimit(r) && r.reset_source !== 'period' && Date.parse(deadline) > Date.now())) {
          throw new Error('実行制御で、復帰を待つ有効な利用枠を選んでください');
        }
      }
      temporary = { mode: 'local', until: deadline };
    }
    return Allocation.normalize({ mode: $('usage-mode').value, localModel: $('usage-local-model').value, temporary });
  }
  function renderAllocation() {
    if (!filled) return;
    $('usage-reset-row').hidden = $('usage-until').value !== 'reset';
    try {
      const allocation = allocationPatch();
      const selected = context.preview?.(allocation);
      const local = ['local', 'local-only'].includes(Allocation.mode(allocation));
      $('usage-allocation-state').textContent = [
        selected?.allocation === 'auto' ? '新しい会話・手動実行：依頼内容からAIとモデルを選択' : selected ? `${draftChanged ? '保存後の' : ''}新しい実行：${AI_LABEL[selected.cli] || selected.cli} / ${selected.model || '既定のモデル'}` : '',
        Allocation.active(allocation) ? `一時的にローカル優先 · ${allocation.temporary.until ? date(allocation.temporary.until) + ' まで' : '解除するまで'}` : '',
        local && context.localAvailable?.() === false ? 'ローカルが未準備のため通常の配分を使用' : '',
      ].filter(Boolean).join(' · ');
    } catch (error) { $('usage-allocation-state').textContent = error.message; }
  }
  async function refreshLimits() {
    if (quotaPromise) return quotaPromise;
    quotaPromise = (async () => {
      try { quotaData = await api.audit.limits(); }
      catch { quotaData = { limitsError: true, agentLimits: [] }; }
      renderLimits(); renderAllocation(); renderStatus();
    })().finally(() => { quotaPromise = null; });
    return quotaPromise;
  }
  function renderUsage() {
    const box = $('audit-usage'), breakdown = $('audit-breakdown');
    breakdown.replaceChildren();
    $('audit-group-label').textContent = { agent_cli: 'AI', workload: '用途', model: 'モデル' }[$('audit-by').value];
    if (state.error) { box.replaceChildren(el('div', 'sub', state.error)); return; }
    if (!state.summary) { box.replaceChildren(el('div', 'sub', '集計しています…')); return; }
    const data = state.summary;
    if (data.available === false || !data.usage || !data.totals) { box.replaceChildren(el('div', 'sub', '使用量を取得できませんでした。記録の収集設定を確認してください')); return; }
    const rows = UsagePresentation.breakdown(data.usage.rows, data.by), total = data.totals, allocation = data.allocationUsage;
    if (!rows.length) { box.replaceChildren(el('div', 'sub', 'この期間の記録はありません')); return; }
    const overview = el('div', 'usage-overview');
    for (const [label, value] of [
      ['総実行回数', `${total.runs || 0} 回`],
      ['クラウド実測トークン', allocation ? allocation.cloud.unmeasured && !allocation.cloud.tokens ? '未計測' : tokens(allocation.cloud.tokens) : '未取得'],
      ['ローカル実行の割合', allocation && allocation.localPercent !== null ? `${allocation.localPercent}%` : '—'],
      ['未計測の実行', `${total.unmeasured_runs || 0} 件`],
    ]) {
      const metric = el('div', 'usage-metric'); metric.append(el('small', 'sub', label), el('strong', '', value)); overview.append(metric);
    }
    const out = [overview];
    if (data.error) out.push(el('small', 'sub', '一部の使用量を取得できませんでした'));
    box.replaceChildren(...out);
    for (const item of rows) {
      const tr = el('tr');
      const name = item.group === 'other' ? 'その他' : data.by === 'agent_cli' ? AI_LABEL[item.group] : data.by === 'workload' ? WORKLOAD_LABEL[item.group] : '';
      const measured = (Number(item.measured_in) || 0) + (Number(item.measured_out) || 0);
      for (const value of [name || item.group || '未記録', item.runs || 0, !measured && item.unmeasured_runs ? '未計測' : tokens(measured), tokens(item.estimated_tokens), item.unmeasured_runs || 0]) tr.append(el('td', '', value));
      const note = el('small', 'sub', `入力 ${tokens(item.measured_in)} / 出力 ${tokens(item.measured_out)}`);
      tr.children[2].append(note); breakdown.append(tr);
    }
  }
  function render() { renderStatus(); renderUsage(); }
  async function loadSummary() {
    const key = `${$('audit-by').value}:${$('audit-period').value}`;
    if (loadingKey === key) return;
    loadingKey = key;
    const request = ++summaryRequest;
    state.summary = null; state.error = ''; renderUsage();
    try {
      const summary = await api.audit.summary({ by: $('audit-by').value, period: $('audit-period').value });
      if (request !== summaryRequest) return;
      state.summary = summary;
    } catch (error) { if (request !== summaryRequest) return; state.error = error.message; }
    finally { if (request === summaryRequest) loadingKey = ''; }
    renderUsage();
  }
  async function run() {
    state.busy = true; renderStatus();
    try { await api.audit.run(); await refreshLimits(); await loadSummary(); }
    catch (error) { $('audit-status').textContent = error.message; }
    finally { state.busy = false; $('audit-run').disabled = !!state.status?.running; }
  }
  function fill(config) {
    const cfg = config.audit || {};
    $('audit-interval').value = cfg.enabled === false ? 'off' : String([0, 30, 60, 360, 1440].includes(cfg.intervalMinutes) ? cfg.intervalMinutes : 60);
    const allocation = Allocation.normalize(config.allocation);
    originalTemporary = Allocation.active(allocation);
    $('usage-mode').value = allocation.mode; $('usage-local-model').value = allocation.localModel;
    $('usage-until').querySelector('[value="keep"]')?.remove();
    if (originalTemporary) $('usage-until').append(new Option(originalTemporary.until ? `${date(originalTemporary.until)} まで（設定済み）` : '解除するまで（設定済み）', 'keep'));
    $('usage-until').value = originalTemporary ? 'keep' : 'off';
    filled = true; draftChanged = false; renderAllocation(); renderLimits();
  }
  function patch() {
    const value = $('audit-interval').value;
    return { enabled: value !== 'off', intervalMinutes: value === 'off' ? context.getConfig?.().audit?.intervalMinutes ?? 60 : Number(value) };
  }
  function reset() { summaryRequest += 1; loadingKey = ''; state.summary = null; state.error = ''; state.busy = false; filled = false; manualAgent = ''; $('usage-manual-row').hidden = true; setError(null); }
  function open() {
    if (!state.status) api.audit.status().then(got => { state.status = got; renderStatus(); }).catch(() => {});
    refreshLimits(); if (!state.summary) loadSummary();
  }
  function init(options = {}) {
    context = options;
    document.querySelector('[data-settings-panel="execution"]').addEventListener('input', () => { draftChanged = true; renderAllocation(); $('settings-status').textContent = '未保存の変更があります'; });
    document.querySelector('[data-settings-panel="execution"]').addEventListener('change', renderAllocation);
    $('usage-execution').onclick = () => context.openExecution?.();
    $('usage-manual-cancel').onclick = () => { $('usage-manual-row').hidden = true; setError(null); };
    $('usage-manual-save').onclick = async () => {
      const remaining = $('usage-manual-remaining'), reset = $('usage-manual-reset'); setError(null);
      if (!manualAgent || !remaining.value || !remaining.checkValidity() || !reset.value || Date.parse(reset.value) <= Date.now()) { setError('残量（0〜100%）と未来のリセット日時を入力してください'); return; }
      $('usage-manual-save').disabled = true;
      try {
        context.setConfig(await api.audit.manualLimit({ agent_cli: manualAgent, quota_used_percent: 100 - Number(remaining.value), reset_at: new Date(reset.value).toISOString() }));
        $('usage-manual-row').hidden = true; renderLimits();
      } catch (error) { setError(error); }
      finally { $('usage-manual-save').disabled = false; }
    };
    $('audit-run').onclick = run; $('audit-by').onchange = loadSummary; $('audit-period').onchange = loadSummary;
    renderLimits(); refreshLimits();
    setInterval(() => { renderLimits(); if ($('app-settings').open) renderAllocation(); context.refreshPreview?.(); }, 30000);
    api.audit.onChanged(got => { state.status = got; renderStatus(); if (!got.running) { refreshLimits(); if ($('app-settings').open && !document.querySelector('[data-settings-panel="audit"]').hidden) loadSummary(); } });
  }
  window.Audit = { init, open, reset, fill, patch, allocationPatch, render, renderAllocation, renderLimits };
})();
