'use strict';

// 「設定 > 利用状況」: 集めた記録の集計と、定型化したものの共有・改善。
// **数字はここで作らない。** 集計と判定は main 経由で agent-audit（ホスト側）が出し、
// ここは並べて、押せる操作を出すだけ（storage.js と同じ作法）。
(function initAudit() {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  // status … audit:status（周期と連鎖の状態）。summary … 集計（開くまで null）
  let summaryRequest = 0;
  const state = { status: null, summary: null, artifacts: null, error: '', busy: false };

  const KIND_LABEL = { skill: 'スキル', task: 'タスク', workflow: 'ワークフロー' };
  const VERDICT = { qualified: '基準を満たす', trial: '様子見', blocked: '使わない', unknown: '未測定' };

  function tokens(n) {
    const value = Number(n) || 0;
    if (value >= 1000000000) return `${(value / 1000000000).toFixed(1)}B`;
    if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
    if (value >= 1000) return `${Math.round(value / 1000)}k`;
    return String(value);
  }

  function line(label, value) {
    const row = el('div', 'row');
    row.append(el('span', '', label), el('span', 'spacer'), el('small', 'sub', value));
    return row;
  }

  // 連鎖の状態と、足りないものの 1 行。
  function renderStatus() {
    const s = state.status;
    const box = $('audit-status');
    $('audit-run').disabled = !s || s.running || state.busy;
    if (!s) { box.textContent = ''; return; }
    const parts = [];
    if (s.running) parts.push(`${s.step || '集めています'}…`);
    else if (s.available === false) parts.push('agent-audit が見つかりません（agent-tools の install.sh で入ります）');
    else if (s.lastError) parts.push(s.lastError);
    else if (!s.lastRunAt) parts.push('まだ集めていません');
    else parts.push(`${Fmt.checkedAt(s.lastRunAt)} に集めました`);
    if (!s.running && s.artifacts && s.artifacts.count) parts.push(`定型化したもの ${s.artifacts.count} 件`);
    box.textContent = parts.join(' · ');
  }

  const AI_LABEL = { claude: 'Claude', codex: 'Codex', copilot: 'Copilot', kiro: 'Kiro' };
  const WORKLOAD_LABEL = { chat: '会話', task: 'タスク', workflow: 'ワークフロー', shared: '共有の依頼' };

  function renderLimits() {
    const box = $('audit-limits');
    const data = state.summary;
    if (!data || state.error || data.available === false) { box.replaceChildren(); return; }
    if (data.limitsError) { box.replaceChildren(el('span', 'sub', '利用枠を取得できませんでした')); return; }
    const limits = data.agentLimits || [];
    const names = [...new Set([...Object.keys(AI_LABEL), ...limits.map((item) => item.agent_cli)])];
    box.replaceChildren(...names.map((name) => {
      const item = limits.find((limit) => limit.agent_cli === name) || {};
      const group = el('div', 'usage-limit');
      const raw = item.quota_used_percent;
      const reset = Date.parse(item.reset_at);
      // 期限切れの観測は現在の使用率として出さない。手動上限からも推測しない。
      const expired = Number.isFinite(reset) && reset <= Date.now();
      const percent = !expired && raw !== null && raw !== undefined && raw !== '' && Number.isFinite(Number(raw))
        ? Math.max(0, Math.min(100, Number(raw))) : null;
      const row = el('div', 'row');
      row.append(el('strong', '', AI_LABEL[name] || name), el('span', 'spacer'),
        el('span', '', percent === null ? (expired ? '更新待ち' : '取得できず') : `${percent}% 使用`));
      group.append(row);
      if (percent !== null) {
        const bar = el('progress', 'usage-meter');
        bar.max = 100;
        bar.value = percent;
        bar.setAttribute('aria-label', `${AI_LABEL[name] || name} の利用枠使用率`);
        if (percent >= 90) bar.classList.add('is-high');
        group.append(bar);
      }
      // period は手動上限の更新時刻なので、サービスのリセット日時と混同しない。
      const serviceReset = item.reset_source !== 'period' && Number.isFinite(reset);
      const notes = [];
      if (serviceReset && !expired) notes.push(`リセット ${new Date(reset).toLocaleString('ja-JP', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}${item.reset_estimated ? '（推定）' : ''}`);
      if (item.observed_at) notes.push(`取得 ${Fmt.checkedAt(item.observed_at)}`);
      if (notes.length) group.append(el('small', 'sub', notes.join(' · ')));
      if (Number(item.max_tokens) > 0) {
        const period = { day: '日', month: '月', total: '全期間' }[item.period] || item.period;
        group.append(el('small', 'sub', `手動上限（${period}）: ${tokens(item.used_tokens)} / ${tokens(item.max_tokens)} トークン`));
      }
      return group;
    }));
  }

  function renderUsage() {
    const box = $('audit-usage');
    const breakdown = $('audit-breakdown');
    breakdown.replaceChildren();
    renderLimits();
    if (state.error) { box.replaceChildren(el('div', 'sub', state.error)); return; }
    if (!state.summary) { box.replaceChildren(el('div', 'sub', '集計しています…')); return; }
    if (state.summary.available === false) {
      box.replaceChildren(el('div', 'sub', '利用状況の収集ツールが見つかりません'));
      return;
    }
    const data = state.summary;
    if (!data.usage || !data.totals) {
      box.replaceChildren(el('div', 'sub', '使用量を取得できませんでした。もう一度集めてください。'));
      return;
    }
    const rows = data.usage.rows || [];
    const total = data.totals;
    const out = [];
    if (rows.length) {
      const overview = el('div', 'usage-overview');
      for (const [label, value] of [
        ['実測トークン', total.measured_in + total.measured_out > 0 || !total.unmeasured_runs
          ? tokens(total.measured_in + total.measured_out) : '未計測'],
        ['推定トークン', tokens(total.estimated_tokens)],
      ]) {
        const metric = el('div', 'usage-metric');
        metric.append(el('small', 'sub', label), el('strong', '', value));
        overview.append(metric);
      }
      out.push(overview, el('small', 'sub', `実測の内訳：入力 ${tokens(total.measured_in)} / 出力 ${tokens(total.measured_out)}`));
      if (total.unmeasured_runs) out.push(el('small', 'sub', `実測できなかった呼び出し ${total.unmeasured_runs} 件`));
      breakdown.replaceChildren(...rows.map((item) => {
        const name = data.by === 'agent_cli' ? AI_LABEL[item.group] : data.by === 'workload' ? WORKLOAD_LABEL[item.group] : '';
        const group = el('div', 'usage-breakdown-item');
        const detail = [`入力 ${tokens(item.measured_in)}`, `出力 ${tokens(item.measured_out)}`];
        if (item.estimated_tokens) detail.push(`推定 ${tokens(item.estimated_tokens)}`);
        if (item.unmeasured_runs) detail.push(`未計測 ${item.unmeasured_runs} 件`);
        group.append(line(name || item.group || '未記録', `${item.runs || 0} 回`), el('small', 'sub', detail.join(' · ')));
        return group;
      }));
    } else out.push(el('div', 'sub', 'この期間の記録はありません'));
    const led = (data.quality || {}).ledger;
    if (led && led.runs) out.push(line('実行の成功率', `${Math.round((led.pass_rate || 0) * 100)}%（${(led.status || {}).done || 0} / ${led.runs} 件）`));
    if (data.error || !data.quality) out.push(el('small', 'sub', '一部の集計を取得できませんでした'));
    box.replaceChildren(...out);
  }

  // 成果物 1 件 1 行。基準を割ったものにだけ「改善案を出す」を出す（押せる操作だけ並べる）。
  function renderArtifacts() {
    const box = $('audit-artifacts');
    const doc = state.artifacts;
    if (!doc) { box.replaceChildren(el('div', 'sub', '確認しています…')); return; }
    const shared = new Map((doc.share || []).map((item) => [`${item.kind}/${item.name}`, item]));
    const items = doc.items || [];
    if (!items.length) {
      box.replaceChildren(el('div', 'sub', '会話から定型化したものが、初めて成功するとここに並びます'));
      return;
    }
    box.replaceChildren(...items.map((item) => {
      const row = el('div', 'row');
      const share = shared.get(`${item.kind}/${item.name}`) || {};
      // 右端は判定だけを短く出し、回数と共有の状態は補助の文字にする（長いチップにしない）
      const notes = [];
      if (item.samples) notes.push(`${item.samples} 回中 ${item.passed || 0} 成功`);
      if (share.improveBranch && !share.improveMergedAt) notes.push('改善案を出しました');
      else if (share.submittedBranch) notes.push('共有しました');
      // 判定とボタンは右端に置く（行ごとに位置が動かないよう、回数は名前側へ寄せる）
      row.append(
        el('span', '', item.name),
        el('small', 'sub', [KIND_LABEL[item.kind] || item.kind, ...notes].join(' · ')),
        el('span', 'spacer'),
        el('span', 'status', VERDICT[item.status] || item.status),
      );
      if ((item.status === 'trial' || item.status === 'blocked')
          && !(share.improveBranch && !share.improveMergedAt)) {
        const button = el('button', 'small', '改善案を出す');
        button.type = 'button';
        button.onclick = () => improve(item, button);
        row.append(button);
      }
      return row;
    }));
  }

  function render() {
    renderStatus();
    renderUsage();
    renderArtifacts();
  }

  async function loadSummary() {
    const request = ++summaryRequest;
    state.summary = null;
    state.error = '';
    renderUsage();
    try {
      const summary = await api.audit.summary({ by: $('audit-by').value, period: $('audit-period').value });
      if (request !== summaryRequest) return;
      state.summary = summary;
    } catch (error) {
      if (request !== summaryRequest) return;
      state.error = error.message;
    }
    renderUsage();
  }

  async function loadArtifacts() {
    try { state.artifacts = await api.audit.artifacts(); } catch { state.artifacts = { items: [] }; }
    renderArtifacts();
  }

  async function run() {
    state.busy = true;
    renderStatus();
    try {
      await api.audit.run();
      await Promise.all([loadSummary(), loadArtifacts()]);
    } catch (error) {
      $('settings-error').textContent = error.message;
      $('settings-error').hidden = false;
    } finally {
      state.busy = false;
      renderStatus();
    }
  }

  async function improve(item, button) {
    button.disabled = true;
    button.textContent = '出しています…';
    try {
      const result = await api.audit.improve({
        origin: item.origin, kind: item.kind, name: item.name,
        evidence: (item.failure_modes || []).map((mode) => ({ status: 'failed', error_class: mode })),
      });
      if (result.skipped) {
        $('settings-status').textContent = result.skipped === 'no-share-repo'
          ? '共有先を入れてください' : `出せませんでした（${result.error || result.skipped}）`;
      } else {
        $('settings-status').textContent = `${result.branch} を出しました`;
      }
      await loadArtifacts();
    } catch (error) {
      $('settings-error').textContent = error.message;
      $('settings-error').hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = '改善案を出す';
    }
  }

  // 設定の値を画面へ。共有先が空なら「main へ直接」は出さない（要るまで出さない）。
  function fill(config) {
    const cfg = (config && config.audit) || {};
    $('audit-enabled').checked = cfg.enabled !== false;
    $('audit-interval').value = String([0, 30, 60, 360, 1440].includes(cfg.intervalMinutes) ? cfg.intervalMinutes : 60);
    $('audit-share-repo').value = cfg.shareRepo || '';
    $('audit-push-main').checked = !!cfg.pushToMain;
    renderShareRepoRow();
  }

  function renderShareRepoRow() {
    $('audit-push-main-row').hidden = !$('audit-share-repo').value.trim();
  }

  function patch() {
    return {
      enabled: $('audit-enabled').checked,
      intervalMinutes: Number($('audit-interval').value),
      shareRepo: $('audit-share-repo').value.trim(),
      pushToMain: $('audit-push-main').checked,
    };
  }

  // 設定ダイアログを開くたびに数え直す（前に開いたときの数は出さない）
  function reset() {
    summaryRequest += 1;
    state.summary = null;
    state.artifacts = null;
    state.error = '';
    state.busy = false;
  }

  // 「利用状況」のタブを開いたとき
  function open() {
    if (!state.status) api.audit.status().then((got) => { state.status = got; renderStatus(); }).catch(() => {});
    if (!state.summary) loadSummary();
    if (!state.artifacts) loadArtifacts();
  }

  function init() {
    $('audit-run').onclick = run;
    $('audit-by').onchange = loadSummary;
    $('audit-period').onchange = loadSummary;
    $('audit-share-repo').oninput = renderShareRepoRow;
    api.audit.onChanged((got) => { state.status = got; renderStatus(); });
  }

  window.Audit = { init, open, reset, fill, patch, render };
})();
