'use strict';

// 監査（agent-audit の呼び出しと閲覧）— 「利用状況」領域の面。
//
// 以前は全体設定の 1 節だった（扱う数字が**プロジェクトごとではない**のに、タブが
// プロジェクト単位だったため）。ナビが領域（ワークロード）単位になり、その制約が
// 無くなったので、agent-audit も他の agent-* と同じく自分の領域を持つ。差し込みの
// 仕組み（registerGlobalSettingsPanel('usage', …)）はそのままで、受け側だけが
// 全体設定から sections/usage.js へ移った。
//
// **利用状況の数字はここが 1 か所で出す。** 以前はこの節に 2 つの集計が並んでいた——
// ノード予算の台帳から画面が自分で足した「利用量」と、agent-audit が集計した「実測の
// トークン利用量」である。同じ話題の数字が 2 つ並ぶうえ、実測の裏取り（CLI の
// セッションログとの突き合わせ）がある分だけ後者が正しい。台帳は agent-audit の源泉の
// 1 つ（`budget-ledger`）なので、agent-audit の集計は台帳集計の上位互換にあたる。
// そこで**表示は agent-audit の集計に一本化**し、画面側の再集計をやめた（C7: 同じ判断の
// 根拠を 2 つ置かない）。上限・配分の設定はノード予算が正のままで、この面はその上限を
// ゲージの分母として読むだけ。agent-audit を入れていない・まだ収集していない端末では、
// 従来の台帳集計へフォールバックし、どちらを見ているかを画面に明示する。
//
// agent-audit CLI（Windows では WSL 経由）の LLM を使わない段だけを扱う:
// 収集（collect）・トークン利用量（usage --json）・実行品質（stats --json）・
// 点検（doctor）。トークン利用量の数字は agent-audit が収集・集計したものを
// そのまま表示し、集計ロジックをこの画面へ複製しない。extract / distill などの
// LLM 段はこの画面からは呼ばない（消費リズムは agent-audit 側のゲート設定が正）。
//
// 定期収集: 設定した間隔（分）で、アプリを開いている間だけ増分収集を実行する
// （refresh フック＝全体ポーリングの周期で経過を確認する）。collect の多重起動は
// main 側でも直列化される。
(function expose(root, factory) {
  const feature = factory(root);
  if (typeof module !== 'undefined' && module.exports) module.exports = feature;
  if (typeof root.registerGlobalSettingsPanel === 'function') {
    root.registerGlobalSettingsPanel('usage', {
      id: 'agent-audit',
      html: feature.panelHtml,
      wire: feature.wire,
      reveal: feature.reveal,
      refresh: feature.refresh,
    });
    // 収集の設定は「利用状況 → 設定」タブへ。見る画面と決める画面を分ける
    // （他の領域と同じ形）。面の実装は 1 つのままで、容れ物だけを 2 つにする。
    root.registerGlobalSettingsPanel('usage-settings', {
      id: 'agent-audit-settings',
      html: feature.settingsPanelHtml,
      wire: feature.wire,
    });
  }
  // ホーム（ポータル）へのサマリーカード。数字はここに出さない——利用状況の数字は
  // 利用状況領域の 1 か所で出す（C7: 同じ話題の数字を 2 か所に置かない）。
  // このカードは入口だけを担う。
  if (typeof root.registerPortalCard === 'function') {
    root.registerPortalCard('usage', { order: 50, html: feature.portalCardHtml });
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, (root) => {
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const escHtml = (value) => String(value == null ? '' : value)
    .replace(/[&<>"']/g, (char) => ESC[char]);

  const PERIODS = [['day', '今日'], ['month', '今月'], ['total', '全期間']];
  const GROUPS = [
    ['workload', '機能別'], ['tool', 'ツール別'], ['agent_cli', 'エージェントCLI別'],
    ['model', 'モデル別'], ['ref', '用途別'], ['node', 'ノード別'],
  ];
  const STATUS_LABELS = { done: '完了', failed: '失敗', cancelled: '中止' };

  // 期間の初期値はノード予算の期間に合わせる（上限は「その期間の消費」に対して掛かる
  // ので、別期間の集計にゲージを重ねると分母と分子が食い違う）。
  let period = '';
  let by = 'tool';
  let summaryData = null;
  let usageData = null;
  let statsData = null;
  let loadedOnce = false;
  let loading = false;
  let loadError = '';
  let collectBusy = false;
  let collectInfo = null;
  let doctorBusy = false;
  let doctorInfo = null;
  let settingsDraft = null;
  let settingsMessage = null;
  let agentLimitDraft = null;
  let agentLimitMessage = null;
  let agentLimitSaving = false;
  let lastAutoAt = 0;
  let autoBusy = false;
  let autoCollectTried = false;   // 初回の自動収集はセッション中 1 回だけ試す

  function fmtTokens(n) {
    const v = Number(n) || 0;
    if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(0)}k`;
    return String(Math.round(v));
  }

  function fmtSeconds(s) {
    const v = Number(s) || 0;
    if (v >= 3600) return `${(v / 3600).toFixed(1)}時間`;
    if (v >= 60) return `${(v / 60).toFixed(1)}分`;
    return `${Math.round(v)}秒`;
  }

  function fmtWhen(iso) {
    if (!iso) return '';
    const t = new Date(iso);
    return Number.isNaN(t.getTime()) ? String(iso) : t.toLocaleString();
  }

  function fmtShortWhen(iso) {
    const t = new Date(iso);
    if (!iso || Number.isNaN(t.getTime())) return String(iso || '');
    const now = new Date();
    const time = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
    if (t.toDateString() === now.toDateString()) return time;
    if (t.getFullYear() === now.getFullYear()) return `${t.getMonth() + 1}/${t.getDate()} ${time}`;
    return `${t.getFullYear()}/${t.getMonth() + 1}/${t.getDate()}`;
  }

  function pairsText(obj, labels) {
    const entries = Object.entries(obj || {});
    if (!entries.length) return '—';
    return entries
      .map(([key, value]) => `${(labels && labels[key]) || key} ${value}`)
      .join(' / ');
  }

  function auditConfig() {
    const appState = root.state || {};
    return (appState.config && appState.config.agentAudit) || {};
  }

  // ノード予算（上限・機能ごとの枠・期間）。無ければ null——上限の表示だけを落とし、
  // 集計そのものは agent-audit 側だけで成立する。
  function budgetState() {
    const orch = (root.state || {}).orchestration;
    return (orch && !orch.error && orch.budget) || null;
  }

  // 期間は「まだ選ばれていない」あいだ予算の期間へ寄せるだけで、**確定はしない**。
  // 予算の取得が 1 周期遅れた端末で 'month' を焼き付けると、見出しは予算どおり「今日」なのに
  // 数字は今月ぶん、という食い違いが残る（確定するのは実際に集計を取った loadData）。
  function currentPeriod() {
    if (period) return period;
    const declared = ((budgetState() || {}).config || {}).period;
    return PERIODS.some(([value]) => value === declared) ? declared : 'month';
  }

  function periodLabel(value) {
    const found = PERIODS.find(([key]) => key === value);
    return found ? found[1] : value;
  }

  function workloadLabel(name) {
    if (typeof root.amigosWorkloadLabel === 'function') return root.amigosWorkloadLabel(name);
    return name;
  }

  // 実測と推定は**合算しない**（agent-audit の設計不変条件）。合計の表示でも内訳を必ず添える。
  function tokenCellHtml(row) {
    const measured = (Number(row.measured_in) || 0) + (Number(row.measured_out) || 0);
    const estimated = Number(row.estimated_tokens) || 0;
    const parts = [];
    if (measured > 0) parts.push(`実測 ${fmtTokens(measured)}`);
    if (estimated > 0) parts.push(`推定 ${fmtTokens(estimated)}`);
    if (!parts.length) parts.push(Number(row.unmeasured_runs) > 0 ? '取得できず' : '0');
    return escHtml(parts.join(' + '));
  }

  // 割合の元になるトークン量（実測＋推定）。並べ替えと棒の長さで同じ値を使う。
  function rowTokens(row) {
    return (Number((row || {}).measured_in) || 0) + (Number((row || {}).measured_out) || 0)
      + (Number((row || {}).estimated_tokens) || 0);
  }

  function shareCellHtml(row, total) {
    const value = rowTokens(row);
    const share = total > 0 && value > 0 ? (value / total) * 100 : 0;
    return `<div class="orch-share"><div class="orch-bar" title="全体の ${share.toFixed(1)}%">
      <span class="orch-bar-measured" style="width:${share.toFixed(1)}%"></span>
    </div><span class="num mono">${value > 0 ? `${share.toFixed(1)}%` : '—'}</span></div>`;
  }

  // 上限に対する消費のゲージ。**期間が一致するときだけ出す**——ノード予算の上限は
  // その期間の消費に掛かるので、別期間の集計に重ねると嘘の残量になる。
  function gaugeHtml(totals, selected) {
    const shown = selected || currentPeriod();
    const budget = budgetState();
    const limit = Number((budget || {}).tokenLimit || 0);
    if (!budget || !(limit > 0)) {
      return '<p class="muted">上限は未設定です。</p>';
    }
    const budgetPeriod = (budget.config || {}).period;
    if (budgetPeriod !== shown) {
      return `<p class="muted">上限は「${escHtml(periodLabel(budgetPeriod))}」用です。残量を見るには期間を合わせてください。</p>`;
    }
    const measuredPct = Math.min(100, (totals.measured / limit) * 100);
    const estimatedPct = Math.min(100 - measuredPct, (totals.estimated / limit) * 100);
    const usedPct = (totals.total / limit) * 100;
    const remaining = Math.max(0, limit - totals.total);
    return `<div class="orch-gauge">
      <div class="orch-gauge-head">
        <strong>${usedPct.toFixed(1)}% 使用</strong>
        <span class="muted">残り ${escHtml(fmtTokens(remaining))} / 上限 ${escHtml(fmtTokens(limit))} トークン</span>
      </div>
      <div class="orch-bar orch-bar-lg" role="img" aria-label="上限の ${usedPct.toFixed(1)}% を使用">
        <span class="orch-bar-measured" style="width:${measuredPct.toFixed(1)}%"></span>
        <span class="orch-bar-estimated" style="width:${estimatedPct.toFixed(1)}%"></span>
      </div>
    </div>`;
  }

  function badgeHtml(kind, text) {
    return `<span class="orch-badge orch-badge-${escHtml(kind)}">${escHtml(text)}</span>`;
  }

  function agentNames(data) {
    const budget = budgetState() || {};
    const allocation = (((budget.config || {}).allocation || {}).agents) || {};
    const observed = (((budget.config || {}).computed || {}).agents) || {};
    const tiers = ((((root.state || {}).orchestration || {}).profiles || {}).tiers) || {};
    const inventory = (((root.state || {}).orchestration || {}).agents) || {};
    return [...new Set([
      ...((data && data.agents) || []).map((row) => String(row.group || '')),
      ...((data && data.agentLimits) || []).map((limit) => String(limit.agent_cli || '')),
      ...Object.keys(allocation),
      ...Object.keys(observed),
      ...(inventory.builtins || []).map(String),
      ...(inventory.dropins || []).map((entry) => String((entry || {}).name || '')),
      ...Object.values(tiers).flatMap((tier) => ((tier || {}).candidates || [])
        .map((candidate) => String(candidate.agent_cli || ''))),
    ])].filter(Boolean);
  }

  // 機能（ワークロード）別。行は agent-audit の集計、上限と状態はノード予算の設定から。
  // 記録が 1 件も無い機能も、予算に枠があるなら 0 として並べる（枠の存在が見えなくならない）。
  function workloadTableHtml(data) {
    const budget = budgetState() || {};
    const rows = (data && data.workloads) || [];
    const total = ((data && data.totals) || {}).total || 0;
    const byGroup = new Map(rows.map((row) => [String(row.group || ''), row]));
    // 割合の大きい順（棒グラフは長い順に並んでいないと比べられない）。同率は名前順。
    const names = [...new Set([
      ...(budget.knownWorkloads || []),
      ...rows.map((row) => String(row.group || '')),
    ])].filter(Boolean)
      .sort((a, b) => rowTokens(byGroup.get(b)) - rowTokens(byGroup.get(a)) || a.localeCompare(b));
    const body = names.map((name) => {
      const row = byGroup.get(name) || {};
      const wl = (budget.workloads || {})[name] || {};
      const cap = Number(wl.tokenCap || 0);
      const runs = Number(row.runs || 0);
      const badge = wl.tokenExceeded ? badgeHtml('over', '上限に到達')
        : wl.soft ? badgeHtml('soft', '節約モード')
          : wl.timeExceeded ? badgeHtml('over', '時間の上限に到達')
            : runs === 0 ? badgeHtml('muted', '記録なし') : badgeHtml('ok', '上限内');
      return `<tr>
        <td>${escHtml(workloadLabel(name))}</td>
        <td class="orch-bar-cell">${shareCellHtml(row, total)}</td>
        <td class="num mono">${tokenCellHtml(row)}${cap > 0 ? ` <span class="muted">/ 上限 ${escHtml(fmtTokens(cap))}</span>` : ''}</td>
        <td class="num mono">${escHtml(fmtSeconds(row.seconds))}</td>
        <td class="num mono">${runs}件</td>
        <td>${badge}</td>
      </tr>`;
    }).join('');
    return `<div class="table-scroll"><table class="list audit-table">
      <thead><tr><th>機能</th><th>全体に占める割合</th><th>トークン</th><th>実行時間</th><th>LLM呼び出し</th><th>状態</th></tr></thead>
      <tbody>${body || '<tr><td colspan="6" class="muted">この期間の記録はありません。</td></tr>'}</tbody>
    </table></div>`;
  }

  function agentTableHtml(data) {
    const rows = (data && data.agents) || [];
    const total = ((data && data.totals) || {}).total || 0;
    const budget = budgetState() || {};
    const allocation = (((budget.config || {}).allocation || {}).agents) || {};
    const observed = (((budget.config || {}).computed || {}).agents) || {};
    const auditLimits = new Map(((data && data.agentLimits) || [])
      .map((limit) => [String(limit.agent_cli || ''), limit]));
    const profiles = (((root.state || {}).orchestration || {}).profiles || {});
    const tiers = profiles.tiers || {};
    const tierNames = (cli) => Object.entries(tiers)
      .sort((a, b) => Number((b[1] || {}).order || 0) - Number((a[1] || {}).order || 0))
      .map(([name, tier]) => {
        const models = ((tier || {}).candidates || [])
          .filter((candidate) => candidate.agent_cli === cli)
          .map((candidate) => candidate.model || '既定');
        return models.length ? `${(tier.label || name)}: ${[...new Set(models)].join('・')}` : '';
      }).filter(Boolean).join(' / ');
    const byName = new Map(rows.map((row) => [String(row.group || ''), row]));
    const names = agentNames(data);
    const body = names.map((name) => byName.get(name) || { group: name })
      // 割合の大きい順（同じ棒グラフの並びを機能別と揃える）。同率は実行数の多い順。
      .sort((a, b) => rowTokens(b) - rowTokens(a) || (Number(b.runs) || 0) - (Number(a.runs) || 0))
      .map((row) => {
        const cli = String(row.group || '');
        const declared = allocation[cli] || {};
        const quota = observed[cli] || {};
        const audited = auditLimits.get(cli) || {};
        const quotaView = audited.observed_at ? audited : quota;
        const cap = Number(declared.max_tokens !== undefined
          ? declared.max_tokens : audited.max_tokens || 0);
        const quotaKind = String(quotaView.quota_kind || '');
        const quotaSource = String(quotaView.quota_source || '');
        const rawQuotaPercent = quotaView.quota_used_percent;
        const quotaPercent = rawQuotaPercent === null || rawQuotaPercent === undefined
          ? null : Number(rawQuotaPercent);
        const quotaSupported = ['claude', 'codex', 'copilot', 'kiro'].includes(cli)
          || Boolean(audited.quota_supported || quotaSource);
        const resetAt = String(quotaView.reset_at || '');
        const resetFuture = resetAt && Number.isFinite(Date.parse(resetAt)) && Date.parse(resetAt) > Date.now();
        const quotaBlocked = quotaKind === 'exhausted' || (quotaKind === 'rate_limit' && resetFuture);
        const capReached = cap > 0 && data && data.period === ((budget.config || {}).period)
          && rowTokens(row) >= cap;
        const quotaState = quotaBlocked
          ? badgeHtml('over', quotaKind === 'rate_limit' ? '一時制限中' : '枠を使い切りました') : '';
        const manualState = capReached ? badgeHtml('over', '到達')
          : cap > 0 ? badgeHtml('ok', '上限内') : '';
        const resetNote = audited.reset_source === 'period' ? '（設定期間）'
          : audited.reset_estimated ? '（推定）' : '';
        const quotaValue = Number.isFinite(quotaPercent)
          ? Math.max(0, Math.min(100, quotaPercent)) : quotaBlocked ? 100 : null;
        const quotaLabel = quotaValue === null ? ''
          : Number.isInteger(quotaValue) ? String(quotaValue) : quotaValue.toFixed(1);
        const quotaTone = quotaValue >= 90 ? 'danger' : quotaValue >= 70 ? 'warn' : 'safe';
        const recovery = resetAt ? `復旧日時 ${fmtShortWhen(resetAt)}${resetNote}`
          : `復旧日時 ${quotaBlocked ? '日時不明' : '—'}`;
        const quotaTitle = [
          quotaSource === 'codex-app-server' ? 'Codex CLIから取得' : quotaSource ? 'CLIから取得' : '',
          resetAt ? `復旧日時: ${fmtWhen(resetAt)} ${resetAt}${resetNote}` : '',
        ].filter(Boolean).join(' / ');
        const quotaHtml = quotaValue === null
          ? `<div class="orch-quota"><div class="orch-quota-meta"><span class="muted">${quotaSupported ? '取得できず' : 'CLI取得非対応'}</span><span class="muted">${recovery}</span></div></div>`
          : `<div class="orch-quota" title="${escHtml(quotaTitle)}">
              <div class="orch-quota-meta"><span class="mono">${quotaLabel}% 使用</span><span>${escHtml(recovery)}</span>${quotaState}</div>
              <div class="orch-bar orch-quota-bar" role="progressbar" aria-label="${escHtml(`${cli} クォータ ${quotaLabel}% 使用、${recovery}`)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${quotaLabel}">
                <span class="orch-quota-fill orch-quota-${quotaTone}" style="width:${quotaLabel}%"></span>
              </div>
            </div>`;
        return `<tr>
        <td><code>${escHtml(cli || '未記録')}</code></td>
        <td><span class="mono">${tokenCellHtml(row)}</span>${shareCellHtml(row, total)}</td>
        <td class="audit-quota-cell">${quotaHtml}</td>
        <td class="num mono">${cap > 0 ? escHtml(fmtTokens(cap)) : '<span class="muted">未設定</span>'}${manualState ? `<br>${manualState}` : ''}</td>
      </tr>`;
      }).join('');
    const strategies = names.map((cli) => {
      const strategy = tierNames(cli);
      return strategy ? `<li><code>${escHtml(cli)}</code>: ${escHtml(strategy)}</li>` : '';
    }).filter(Boolean).join('');
    return `<div class="table-scroll"><table class="list audit-table">
      <thead><tr><th>エージェント</th><th>利用量</th><th>クォータ</th><th>手動上限</th></tr></thead>
      <tbody>${body || '<tr><td colspan="4" class="muted">エージェント別の記録はありません。</td></tr>'}</tbody>
    </table></div>
    ${strategies ? `<details class="audit-detail"><summary>tier別の切替戦略</summary><ul>${strategies}</ul></details>` : ''}`;
  }

  function agentLimitSettingsHtml(data) {
    const budget = budgetState() || {};
    const allocation = (((budget.config || {}).allocation || {}).agents) || {};
    const names = agentNames(data).sort((a, b) => a.localeCompare(b));
    const selectedPeriod = (agentLimitDraft && agentLimitDraft.period)
      || (budget.config || {}).period || 'day';
    const values = (agentLimitDraft && agentLimitDraft.agents) || {};
    const rows = names.map((name) => {
      const configured = values[name] !== undefined ? values[name] : Number((allocation[name] || {}).max_tokens || 0);
      return `<tr><td><label for="audit-agent-limit-${escHtml(name)}"><code>${escHtml(name)}</code></label></td>
        <td><input id="audit-agent-limit-${escHtml(name)}" class="audit-agent-limit" type="number" min="0" step="1000"
          data-agent-cli="${escHtml(name)}" value="${escHtml(String(configured))}"></td></tr>`;
    }).join('');
    const options = PERIODS.map(([value, label]) =>
      `<option value="${value}"${value === selectedPeriod ? ' selected' : ''}>${label}</option>`).join('');
    return `<details class="audit-detail audit-agent-limit-settings"${agentLimitSaving || agentLimitMessage ? ' open' : ''}>
      <summary>エージェント別上限を設定</summary>
      <p class="muted">実行制御に使う手動上限です。CLIから取得するquotaとは別で、0は無制限です。</p>
      <label>対象期間 <select id="audit-agent-limit-period">${options}</select></label>
      ${rows ? `<div class="table-scroll"><table class="list audit-table"><thead><tr><th>エージェント</th><th>トークン上限</th></tr></thead><tbody>${rows}</tbody></table></div>`
        : '<p class="muted">設定できるエージェントがまだありません。</p>'}
      ${agentLimitMessage ? `<p class="${agentLimitMessage.ok ? 'muted' : 'audit-error'}" role="status">${escHtml(agentLimitMessage.text)}</p>` : ''}
      <button type="button" id="audit-agent-limits-save" class="primary-inline"${agentLimitSaving || !rows ? ' disabled' : ''}>${agentLimitSaving ? '保存しています…' : '上限を保存'}</button>
    </details>`;
  }

  // agent-audit の集計が取れないときの代替。台帳（ノード予算）だけの集計を、
  // **どちらを見ているかを明示して**出す（黙って別の数字に差し替えない）。
  function ledgerFallbackHtml() {
    const budget = budgetState();
    if (!budget || typeof root.orchBudgetPanelHtml !== 'function') {
      return '<p class="muted">利用状況を取得できません。設定で agent-audit の起動コマンドを確認してください。</p>';
    }
    return `<p class="muted">詳細を取得できないため、実行記録だけを表示しています。</p>
      ${root.orchBudgetPanelHtml(budget)}`;
  }

  function usageTableHtml(data) {
    const rows = (data && data.rows) || [];
    if (!rows.length) {
      return '<p class="muted">記録がありません。「利用状況を収集」を押してください。</p>';
    }
    const body = rows.map((row) => `<tr>
      <td>${escHtml(row.group)}</td>
      <td class="num">${escHtml(String(row.runs || 0))}</td>
      <td class="num">${escHtml(fmtTokens(row.measured_in))}</td>
      <td class="num">${escHtml(fmtTokens(row.measured_out))}</td>
      <td class="num">${escHtml(fmtTokens(row.estimated_tokens))}</td>
      <td class="num">${escHtml(fmtSeconds(row.seconds))}</td>
      <td class="num">${escHtml(String(row.unmeasured_runs || 0))}</td>
    </tr>`).join('');
    return `<table class="list audit-table">
      <thead><tr>
        <th>グループ</th><th>LLM呼び出し</th><th>実測トークン 入力</th><th>実測トークン 出力</th>
        <th>推定トークン</th><th>実行時間</th><th>実測なし</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table>`;
  }

  function statsTableHtml(data) {
    const tools = (data && data.tools) || [];
    if (!tools.length) {
      return '<p class="muted">実行品質の記録はありません。</p>';
    }
    const body = tools.map((tool) => `<tr>
      <td>${escHtml(tool.tool)}</td>
      <td class="num">${escHtml(String(tool.runs || 0))}</td>
      <td>${escHtml(pairsText(tool.status, STATUS_LABELS))}</td>
      <td>${escHtml(pairsText(tool.error_class, null))}</td>
      <td class="num">${escHtml(String(tool.retries || 0))}</td>
      <td>${escHtml(`合格 ${tool.verify_pass || 0} / 不合格 ${tool.verify_fail || 0}`)}</td>
    </tr>`).join('');
    return `<table class="list audit-table">
      <thead><tr>
        <th>ツール</th><th>実行数</th><th>結果</th><th>失敗の内訳</th><th>リトライ</th><th>検証</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table>`;
  }

  function collectStatusHtml(info, busy) {
    if (busy) return '<p class="muted" role="status">収集を実行しています…</p>';
    if (!info) return '';
    const when = info.at ? `${escHtml(fmtWhen(info.at))} に` : '';
    if (info.ok) {
      return `<p class="muted" role="status">最終収集: ${when}成功しました。</p>
      ${info.detail ? `<details class="audit-detail"><summary>収集の出力</summary><pre>${escHtml(info.detail)}</pre></details>` : ''}`;
    }
    return `<p class="audit-error" role="alert">最終収集: ${when}失敗しました — ${escHtml(info.error || '')}</p>
    ${info.detail ? `<details class="audit-detail"><summary>収集の出力</summary><pre>${escHtml(info.detail)}</pre></details>` : ''}`;
  }

  function settingsHtml(values) {
    const v = values || {};
    return `<section class="orch-panel audit-settings">
      <header class="row">
        <h3>収集の設定</h3>
      </header>
      <div class="field">
        <label for="audit-set-command">起動コマンド</label>
        <input id="audit-set-command" type="text" value="${escHtml(v.command || '')}"
          placeholder="空なら PATH の agent-audit を使います">
        <p class="field-help">未インストール時だけ指定します。</p>
      </div>
      <div class="field">
        <label for="audit-set-distro">WSL ディストロ</label>
        <input id="audit-set-distro" type="text" value="${escHtml(v.distro || '')}"
          placeholder="空なら既定のディストロで実行します">
        <p class="field-help">Windows でのみ使います。</p>
      </div>
      <div class="field">
        <label for="audit-set-config">agent-audit の設定ファイル</label>
        <input id="audit-set-config" type="text" value="${escHtml(v.configPath || '')}"
          placeholder="例: ~/.agents/agent-audit.yaml">
        <p class="field-help">空なら自動で探します。</p>
      </div>
      <div class="field">
        <label for="audit-set-dir">収集データの保存先</label>
        <input id="audit-set-dir" type="text" value="${escHtml(v.auditDir || '')}"
          placeholder="空なら ~/.agents/audit に保存します">
      </div>
      <div class="field">
        <label for="audit-set-interval">定期収集の間隔（分）</label>
        <input id="audit-set-interval" type="number" min="0" step="1" value="${escHtml(String(v.collectIntervalMin || 0))}">
        <p class="field-help">0 で無効。アプリを開いている間だけ収集します。</p>
      </div>
      ${settingsMessage ? `<p class="${settingsMessage.ok ? 'muted' : 'audit-error'}" role="status">${escHtml(settingsMessage.text)}</p>` : ''}
      <div class="settings-save-actions">
        <button type="button" id="audit-save" class="primary-inline">保存</button>
      </div>
    </section>`;
  }

  // 全体設定「利用状況」の面。上から「利用量（この端末の合計・機能別・エージェント別）」
  // →「内訳（ツール／モデル／用途／ノード別）」→「実行品質」→「収集の設定」。
  // 集計はすべて agent-audit の `usage --json` / `stats --json` が正で、この画面は
  // 描画だけをする（集計ロジックを複製しない）。
  function panelHtml() {
    const optionsHtml = (list, current) => list
      .map(([value, label]) => `<option value="${value}"${value === current ? ' selected' : ''}>${label}</option>`)
      .join('');
    const totals = (summaryData && summaryData.totals) || null;
    const unmeasured = totals ? Number(totals.unmeasuredRuns || 0) : 0;
    const body = totals ? `
      <div class="orch-usage-summary">
        <div><span>合計</span><strong>${escHtml(totals.total > 0 ? `${fmtTokens(totals.total)} トークン` : (unmeasured > 0 ? '取得できず' : '0 トークン'))}</strong></div>
        <div><span>実行時間</span><strong>${escHtml(fmtSeconds(totals.seconds))}</strong></div>
        <div><span>LLM呼び出し</span><strong>${Number(totals.runs || 0)}件</strong></div>
      </div>
      <p class="orch-usage-breakdown">
        <span class="orch-legend"><span class="orch-swatch orch-bar-measured"></span>実測 ${escHtml(fmtTokens(totals.measured))}（入力 ${escHtml(fmtTokens(totals.measuredIn))} / 出力 ${escHtml(fmtTokens(totals.measuredOut))}）</span>
        <span class="orch-legend"><span class="orch-swatch orch-bar-estimated"></span>推定 ${escHtml(fmtTokens(totals.estimated))}</span>
        ${unmeasured > 0 ? `<span class="muted">実測できなかったLLM呼び出し ${unmeasured}件</span>` : ''}
      </p>
      <p class="muted">実測は CLI の記録です。推定は同じ処理の実測値か実行時間を使います。</p>
      ${gaugeHtml(totals, currentPeriod())}
      ${workloadTableHtml(summaryData)}
      <h4 class="orch-usage-subheading">エージェント別</h4>
      ${agentTableHtml(summaryData)}` : (loading ? '' : ledgerFallbackHtml());
    return `<section class="orch-panel audit-usage" aria-labelledby="audit-usage-title">
      <header class="row">
        <h3 id="audit-usage-title">利用量（${escHtml(periodLabel(currentPeriod()))}）</h3>
        <div class="audit-actions">
          <label>期間 <select id="audit-period">${optionsHtml(PERIODS, currentPeriod())}</select></label>
          <button type="button" id="audit-collect" class="primary-inline"${collectBusy ? ' disabled' : ''}>${collectBusy ? '収集しています…' : '利用状況を収集'}</button>
          <button type="button" id="audit-reload"${loading ? ' disabled' : ''}>表示を更新</button>
          <button type="button" id="audit-doctor"${doctorBusy ? ' disabled' : ''}>設定を点検</button>
        </div>
      </header>
      <p class="muted">「利用状況を収集」は実行記録とquotaを更新します。Claude・Codex・Copilot・Kiroは各CLIの組み込みusage/statusから取得します。</p>
      ${collectStatusHtml(collectInfo, collectBusy)}
      ${doctorInfo ? `<details class="audit-detail" open><summary>点検結果${doctorInfo.ok ? '' : '（問題があります）'}</summary><pre>${escHtml(doctorInfo.detail || doctorInfo.error || '')}</pre></details>` : ''}
      ${loadError ? `<p class="audit-error" role="alert">${escHtml(loadError)}</p>` : ''}
      ${loading ? '<p class="muted" role="status">集計しています…</p>' : ''}
      ${body}
    </section>
    <section class="orch-panel audit-breakdown">
      <header class="row">
        <h3>内訳</h3>
        <div class="audit-controls">
          <label>集計 <select id="audit-by">${optionsHtml(GROUPS, by)}</select></label>
        </div>
      </header>
      ${usageTableHtml(usageData)}
    </section>
    <section class="orch-panel audit-stats">
      <header class="row"><h3>実行品質</h3></header>
      ${statsTableHtml(statsData)}
    </section>`;
  }

  // 「設定」タブの中身。手動上限と収集の設定を、閲覧側とは別の容れ物に入れる。
  function settingsPanelHtml() {
    return `${agentLimitSettingsHtml(summaryData)}${settingsHtml(settingsDraft || auditConfig())}`;
  }

  async function loadData() {
    if (!root.api || !root.api.agentAuditSummary || loading) return;
    loading = true;
    loadError = '';
    render();
    const p = currentPeriod();
    period = p;                 // 取りに行った期間で確定させる（表示と数字を必ず一致させる）
    try {
      // 3 本を並べて呼ぶ（利用量・内訳・実行品質）。1 本でも失敗したらその理由を出し、
      // 利用量は台帳集計へフォールバックする——数字が消えるより、どの集計かを言う。
      const [summary, usage, stats] = await Promise.all([
        root.api.agentAuditSummary({ period: p }),
        root.api.agentAuditUsage({ period: p, by }),
        root.api.agentAuditStats({ period: p }),
      ]);
      summaryData = summary;
      usageData = usage;
      statsData = stats;
      loadedOnce = true;
      if (!collectInfo && summary && summary.lastCollect) collectInfo = summary.lastCollect;
    } catch (error) {
      summaryData = null;
      loadError = error && error.message ? error.message : String(error);
    }
    loading = false;
    render();
  }

  async function runCollect() {
    if (!root.api || !root.api.agentAuditCollect || collectBusy) return;
    collectBusy = true;
    render();
    try {
      const result = await root.api.agentAuditCollect({});
      collectInfo = result;
      if (result && result.ok) {
        loadedOnce = false;
        collectBusy = false;
        await loadData();
        return;
      }
    } catch (error) {
      collectInfo = { ok: false, error: error && error.message ? error.message : String(error) };
    }
    collectBusy = false;
    render();
  }

  async function runDoctor() {
    if (!root.api || !root.api.agentAuditDoctor || doctorBusy) return;
    doctorBusy = true;
    render();
    try {
      doctorInfo = await root.api.agentAuditDoctor({});
    } catch (error) {
      doctorInfo = { ok: false, detail: '', error: error && error.message ? error.message : String(error) };
    }
    doctorBusy = false;
    render();
  }

  function readSettingsForm(pane) {
    const value = (id) => {
      const input = pane.querySelector(`#${id}`);
      return input ? input.value.trim() : '';
    };
    const interval = Number(value('audit-set-interval'));
    return {
      command: value('audit-set-command'),
      distro: value('audit-set-distro'),
      configPath: value('audit-set-config'),
      auditDir: value('audit-set-dir'),
      collectIntervalMin: Number.isFinite(interval) && interval > 0 ? Math.round(interval) : 0,
    };
  }

  async function saveSettings(pane) {
    if (!root.api || !root.api.saveConfig) return;
    const values = readSettingsForm(pane);
    const appState = root.state || {};
    const cfg = JSON.parse(JSON.stringify(appState.config || {}));
    cfg.agentAudit = { ...(cfg.agentAudit || {}), ...values };
    try {
      appState.config = await root.api.saveConfig(cfg);
      settingsDraft = null;
      settingsMessage = { ok: true, text: '設定を保存しました' };
    } catch (error) {
      settingsMessage = { ok: false, text: error && error.message ? error.message : String(error) };
    }
    render();
  }

  function readAgentLimitsForm(pane) {
    const periodInput = pane.querySelector('#audit-agent-limit-period');
    const agents = {};
    for (const input of pane.querySelectorAll('.audit-agent-limit')) {
      agents[input.dataset.agentCli] = input.value.trim();
    }
    return { period: periodInput ? periodInput.value : 'day', agents };
  }

  async function saveAgentLimits(pane) {
    if (!root.api || !root.api.orchestrationBudgetSave || agentLimitSaving) return;
    agentLimitDraft = readAgentLimitsForm(pane);
    const agents = Object.fromEntries(Object.entries(agentLimitDraft.agents)
      .map(([name, value]) => [name, { max_tokens: value === '' ? 0 : value }]));
    agentLimitSaving = true;
    agentLimitMessage = null;
    render();
    try {
      const saved = await root.api.orchestrationBudgetSave({
        period: agentLimitDraft.period,
        allocation: { agents },
      });
      const appState = root.state || (root.state = {});
      appState.orchestration = { ...(appState.orchestration || {}), budget: saved };
      agentLimitDraft = null;
      agentLimitMessage = { ok: true, text: 'エージェント別上限を保存しました' };
      loadedOnce = false;
    } catch (error) {
      agentLimitMessage = { ok: false, text: error && error.message ? error.message : String(error) };
    }
    agentLimitSaving = false;
    render();
    if (!agentLimitMessage || agentLimitMessage.ok) await loadData();
  }

  // 面が実際に見えているか。見えていないあいだは CLI を起こさない——タブを開いていない
  // 端末で毎周期 agent-audit を起動することになる。
  //
  // 判定は**自分が入っているタブ**を見る。以前は `tab-orchestration`（全体設定）が前面か
  // どうかを見ていて、面が利用状況領域へ移ったあとも条件だけが残っていた。結果、利用状況を
  // 開いても「見えていない」と判定され、初回の集計が永久に走らない（人が「今すぐ収集」を
  // 押すまで画面が空のまま）——起動直後に何も出ないのはこれが原因だった。
  function visible(container) {
    if (!container || !root.document || !container.closest) return false;
    const settingsPane = container.closest('.global-settings-pane');
    if (settingsPane && settingsPane.hidden) return false;
    const tabpane = container.closest('.tabpane');
    return Boolean(tabpane && tabpane.classList.contains('active'));
  }

  function wire(pane) {
    const on = (id, event, fn) => {
      const element = pane.querySelector(`#${id}`);
      if (element) element.addEventListener(event, fn);
    };
    on('audit-collect', 'click', runCollect);
    on('audit-reload', 'click', () => loadData());
    on('audit-doctor', 'click', runDoctor);
    on('audit-period', 'change', (event) => {
      period = event.target.value;
      loadData();
    });
    on('audit-by', 'change', (event) => {
      by = event.target.value;
      loadData();
    });
    on('audit-save', 'click', () => saveSettings(pane));
    on('audit-agent-limits-save', 'click', () => saveAgentLimits(pane));
    for (const input of pane.querySelectorAll('.audit-settings input')) {
      input.addEventListener('input', () => {
        settingsDraft = readSettingsForm(pane);
        settingsMessage = null;
      });
    }
    for (const input of pane.querySelectorAll('.audit-agent-limit-settings input, .audit-agent-limit-settings select')) {
      input.addEventListener('input', () => {
        agentLimitDraft = readAgentLimitsForm(pane);
        agentLimitMessage = null;
      });
    }
  }

  // 収集完了の反映などで後から描き直すとき、設定欄への入力中なら潰さない
  // （全体ポーリングと同じ配慮。draft は保持されるがフォーカスが失われるため）。
  function maybeRender() {
    if (!root.document) return;
    const activeElement = root.document.activeElement;
    if (activeElement && (activeElement.tagName === 'INPUT' || activeElement.tagName === 'TEXTAREA')) return;
    render();
  }

  // 自分の容れ物だけを描き直す。全体設定ごと描き直すと、他の節で入力中の欄が飛ぶ。
  // 容れ物は 2 つ（閲覧・設定）あり、どちらも描かれているとは限らない。
  function render() {
    if (!root.document) return;
    for (const [id, html] of [['agent-audit', panelHtml], ['agent-audit-settings', settingsPanelHtml]]) {
      const container = root.document.getElementById(`global-settings-slot-${id}`);
      if (!container) continue;
      container.innerHTML = html();
      wire(container);
    }
    const main = root.document.getElementById('global-settings-slot-agent-audit');
    if (main) reveal(main);
  }

  // 節が表示されたとき（描画直後・タブの切り替え）に呼ばれる。初回の集計取得はここで起こす
  // ——利用状況を開いていない間は agent-audit を起動しない。
  function reveal(container) {
    if (!visible(container)) return;
    if (!loadedOnce && !loading && !loadError) loadData().then(autoCollectIfEmpty);
    else autoCollectIfEmpty();
  }

  // **一度も収集していない端末では、開いた時点で 1 回だけ自動で収集する。**
  // 集計は収集済みの記録を読むだけなので、収集が 0 回の端末では画面が空になり、
  // 人が「今すぐ収集」を押すまで何も出ない——初回に必要な操作を人にさせない。
  // 試すのはセッション中 1 回だけ（agent-audit を入れていない端末で毎回起動しない）。
  async function autoCollectIfEmpty() {
    if (autoCollectTried || collectBusy || loading || loadError) return;
    if (!root.api || !root.api.agentAuditCollect) return;
    const totals = (summaryData && summaryData.totals) || null;
    const collected = !!(summaryData && summaryData.lastCollect) || Number((totals || {}).runs || 0) > 0;
    if (collected) return;
    autoCollectTried = true;
    await runCollect();
  }

  // 全体ポーリングから毎回呼ばれる。定期収集の間隔だけをここで確認し、
  // 集計の再取得はタブを開いたときに行う（ポーリングごとに CLI を起動しない）。
  function refresh() {
    const intervalMin = Number(auditConfig().collectIntervalMin || 0);
    if (!(intervalMin > 0)) return;
    if (!root.api || !root.api.agentAuditCollect || autoBusy || collectBusy) return;
    const now = Date.now();
    if (now - lastAutoAt < intervalMin * 60000) return;
    lastAutoAt = now;
    autoBusy = true;
    root.api.agentAuditCollect({}).then((result) => {
      collectInfo = result;
      if (result && result.ok) loadedOnce = false;
    }).catch((error) => {
      collectInfo = { ok: false, error: error && error.message ? error.message : String(error) };
    }).then(() => {
      autoBusy = false;
      maybeRender();
    });
  }

  // ホーム（ポータル）のカード。利用状況領域への入口だけを出す（数字は出さない）。
  function portalCardHtml() {
    return `<div class="portal-card-heading">
        <span class="summary-kicker">使いすぎを防ぐ</span>
        <h3>利用状況</h3>
      </div>
      <p class="portal-card-count">この端末で使ったトークンと、実行の品質</p>
      <div class="portal-card-actions">
        <button type="button" class="primary-inline" data-portal-area="usage">開く</button>
      </div>`;
  }

  return {
    escHtml, fmtTokens, fmtSeconds, fmtShortWhen, pairsText,
    usageTableHtml, statsTableHtml, settingsHtml, settingsPanelHtml, collectStatusHtml, panelHtml,
    agentLimitSettingsHtml,
    workloadTableHtml, agentTableHtml, gaugeHtml, ledgerFallbackHtml,
    render, refresh, wire, reveal, portalCardHtml,
  };
});
