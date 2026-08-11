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
  return `<div class="need-blocker" role="status">
    <strong>作業が停止されています</strong>
    <span>先に全体設定で作業を再開してください。</span>
    <button type="button" class="primary-inline" data-orch-open="1">全体設定を開く</button>
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
  const totalRecords = Number(budget.totalRecords || 0);
  const unknownRecords = Number(budget.totalUnestimatedRecords || 0);
  const totalTxt = tt.total > 0 ? `${orchTokens(tt.total)} トークン`
    : unknownRecords > 0 ? '推定不可' : '0 トークン';
  const timeTxt = cfg.execution_minutes > 0
    ? `${amigosMin(budget.totalSeconds)} / ${amigosMin(budget.limitSeconds)} 分`
    : `${amigosMin(budget.totalSeconds)} 分（上限なし）`;
  const overBadge = budget.exceeded
    ? orchBadge('over', '上限到達 — 新しい実行を制限中')
    : limit > 0 || cfg.execution_minutes > 0
      ? orchBadge('ok', '上限内')
      : orchBadge('muted', '上限なし');
  const gauge = limit > 0 ? (() => {
    const mPct = Math.min(100, (tt.measured / limit) * 100);
    const ePct = Math.min(100 - mPct, (tt.estimated / limit) * 100);
    const usedPct = (tt.total / limit) * 100;
    const remaining = Math.max(0, limit - tt.total);
    return `<div class="orch-gauge">
      <div class="orch-gauge-head">
        <strong>${usedPct.toFixed(1)}% 使用</strong>
        <span class="muted">残り ${esc(orchTokens(remaining))} / 上限 ${esc(orchTokens(limit))} トークン</span>
      </div>
      <div class="orch-bar orch-bar-lg" role="img" aria-label="上限の ${usedPct.toFixed(1)}% を使用">
        <span class="orch-bar-measured" style="width:${mPct.toFixed(1)}%"></span>
        <span class="orch-bar-estimated" style="width:${ePct.toFixed(1)}%"></span>
      </div>
    </div>`;
  })() : '<p class="muted orch-no-limit">トークン上限は設定されていません。利用量は引き続き記録されます。</p>';

  const wlRows = (budget.knownWorkloads || [])
    .concat(Object.keys(budget.workloads || {}).filter((w) => !(budget.knownWorkloads || []).includes(w)))
    .map((wl) => {
      const w = (budget.workloads || {})[wl] || {
        seconds: 0, recordCount: 0, unestimatedRecords: 0,
        measuredTokens: 0, estimatedTokens: 0, totalTokens: 0, tokenCap: 0,
      };
      const cap = Number(w.tokenCap || 0);
      const records = Number(w.recordCount || 0);
      const unknown = Number(w.unestimatedRecords || 0);
      const share = tt.total > 0 && w.totalTokens > 0 ? (w.totalTokens / tt.total) * 100 : 0;
      const tokenText = records === 0 ? '利用記録なし'
        : w.totalTokens > 0 ? `${orchTokens(w.totalTokens)}${unknown > 0 ? ' + 一部推定不可' : ''}${cap > 0 ? ` / 上限 ${orchTokens(cap)}` : ''}`
          : unknown > 0 ? '推定不可' : '0';
      let badge = '';
      if (w.tokenExceeded) badge = orchBadge('over', '上限に到達');
      else if (w.soft) badge = orchBadge('soft', '節約モード');
      else if (w.timeExceeded) badge = orchBadge('over', '時間上限に到達');
      else if (unknown > 0) badge = orchBadge('muted', 'トークン推定不可');
      else if (records === 0) badge = orchBadge('muted', '記録なし');
      return `<tr>
        <td>${esc(amigosWorkloadLabel(wl))}</td>
        <td class="orch-bar-cell">
          <div class="orch-share"><div class="orch-bar" title="全体の ${share.toFixed(1)}%">
            <span class="orch-bar-measured" style="width:${share.toFixed(1)}%"></span>
          </div><span class="num mono">${w.totalTokens > 0 ? `${share.toFixed(1)}%` : '—'}</span></div>
        </td>
        <td class="num mono" title="実測 ${esc(orchTokens(w.measuredTokens))} / 推定 ${esc(orchTokens(w.estimatedTokens))}">${esc(tokenText)}</td>
        <td class="num mono">${esc(amigosMin(w.seconds))} 分</td>
        <td class="num mono">${records}件</td>
        <td>${badge}</td>
      </tr>`;
    }).join('');
  const agentRows = Object.entries(budget.agents || {})
    .sort((a, b) => (b[1].totalTokens - a[1].totalTokens) || (b[1].seconds - a[1].seconds))
    .map(([name, agent]) => {
      const records = Number(agent.recordCount || 0);
      const unknown = Number(agent.unestimatedRecords || 0);
      const share = tt.total > 0 && agent.totalTokens > 0 ? (agent.totalTokens / tt.total) * 100 : 0;
      const tokenText = agent.totalTokens > 0
        ? `${orchTokens(agent.totalTokens)}${unknown > 0 ? ' + 一部推定不可' : ''}`
        : unknown > 0 ? '推定不可' : '0';
      return `<tr>
        <td><code>${esc(name === 'unknown' ? '未記録' : name)}</code></td>
        <td class="orch-bar-cell"><div class="orch-share"><div class="orch-bar" title="全体の ${share.toFixed(1)}%">
          <span class="orch-bar-measured" style="width:${share.toFixed(1)}%"></span>
        </div><span class="num mono">${agent.totalTokens > 0 ? `${share.toFixed(1)}%` : '—'}</span></div></td>
        <td class="num mono" title="実測 ${esc(orchTokens(agent.measuredTokens))} / 推定 ${esc(orchTokens(agent.estimatedTokens))}">${esc(tokenText)}</td>
        <td class="num mono">${esc(amigosMin(agent.seconds))} 分</td>
        <td class="num mono">${records}件</td>
      </tr>`;
    }).join('') || '<tr><td colspan="5" class="muted">エージェント別の利用記録はありません。</td></tr>';

  return `<section class="orch-panel">
    <header class="row">
      <div>
        <span class="summary-kicker">利用状況</span>
        <h3>利用量（${esc(periodLabel)}）</h3>
      </div>
      <div>${overBadge}</div>
    </header>
    <div class="orch-usage-summary">
      <div><span>合計</span><strong>${esc(totalTxt)}</strong></div>
      <div><span>実行時間</span><strong>${esc(timeTxt)}</strong></div>
      <div><span>記録</span><strong>${totalRecords}件</strong></div>
    </div>
    <p class="orch-usage-breakdown">
      <span class="orch-legend"><span class="orch-swatch orch-bar-measured"></span>実測 ${esc(orchTokens(tt.measured))}</span>
      <span class="orch-legend"><span class="orch-swatch orch-bar-estimated"></span>推定 ${esc(orchTokens(tt.estimated))}</span>
      ${unknownRecords > 0 ? `<span class="muted">推定できない記録 ${unknownRecords}件</span>` : ''}
    </p>
    ${unknownRecords > 0 ? '<p class="muted">実測トークンが記録されず、推定レートもない実行はトークン量を算出できません。実行時間と記録件数は集計しています。</p>' : ''}
    ${gauge}
    <div class="table-scroll"><table class="amigos-table orch-table">
      <thead><tr><th>機能</th><th>全体に占める割合</th><th>トークン</th><th>実行時間</th><th>記録</th><th>状態</th></tr></thead>
      <tbody>${wlRows}</tbody>
    </table></div>
    <h4 class="orch-usage-subheading">エージェント別</h4>
    <p class="muted">エージェントCLIごとの利用量です。トークンを取得できない実行も、時間と記録件数には含まれます。</p>
    <div class="table-scroll"><table class="amigos-table orch-table">
      <thead><tr><th>エージェント</th><th>全体に占める割合</th><th>トークン</th><th>実行時間</th><th>記録</th></tr></thead>
      <tbody>${agentRows}</tbody>
    </table></div>
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
        <p class="muted">機能ごとに利用上限を配分します。</p>
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

// 実行プロファイル自動選択（agent-profiles 契約）。対象は「用途ごとにエージェント/モデルを
// 選べる」project / flow に絞る（routine は選ばない・amigos は役割単位の宣言が別にある）。
const ORCH_PROFILE_WORKLOADS = ['project', 'flow'];
const ORCH_TIER_LABELS = { small: 'small', medium: 'medium', large: 'large' };
const ORCH_TIER_KEYS = Object.keys(ORCH_TIER_LABELS);

function orchProfileTierLabel(tiers, key) {
  if (ORCH_TIER_LABELS[key]) return ORCH_TIER_LABELS[key];
  const t = (tiers || {})[key];
  if (t && t.label) return t.label;
  return key || '（未選択）';
}

function orchProfileCandidateText(cand) {
  if (!cand) return '—';
  const text = [cand.agent_cli, cand.model].filter(Boolean).join(':');
  return text || '—';
}

function orchOrderedTiers(profiles) {
  const tiers = (profiles && profiles.tiers) || {};
  return ORCH_TIER_KEYS.map((key) => [key, tiers[key]]).filter(([, tier]) => tier)
    .sort((a, b) => (Number(b[1].order) || 0) - (Number(a[1].order) || 0));
}

function orchTierCandidateRowHtml(candidate = {}) {
  return `<div class="orch-tier-candidate">
    <label><span>エージェント</span><input type="text" class="orch-tier-candidate-cli mono"
      value="${esc(candidate.agent_cli || '')}" placeholder="claude" /></label>
    <label><span>モデル</span><input type="text" class="orch-tier-candidate-model mono"
      value="${esc(candidate.model || '')}" placeholder="opus" /></label>
    <button type="button" class="orch-tier-candidate-remove" aria-label="この候補を削除">削除</button>
  </div>`;
}

function orchTierEditorRowHtml(key, tier = {}) {
  const candidates = (tier.candidates || []).map(orchTierCandidateRowHtml).join('')
    || orchTierCandidateRowHtml();
  return `<li class="orch-profile-tier" data-orch-tier-key="${esc(key)}">
    <div class="orch-profile-tier-name"><span>段</span><strong class="mono">${esc(ORCH_TIER_LABELS[key])}</strong></div>
    <div class="orch-tier-candidates">
      <span>使う候補（上から優先）</span>
      <div class="orch-tier-candidate-list">${candidates}</div>
      <button type="button" class="orch-tier-candidate-add">候補を追加</button>
    </div>
  </li>`;
}

function orchTiersPanelHtml(overview) {
  const profiles = overview.profiles || { enabled: true, tiers: {}, policy: {}, state: {} };
  const tiers = profiles.tiers || {};
  const tierRows = ORCH_TIER_KEYS.map((key) => orchTierEditorRowHtml(key, tiers[key])).join('');
  return `<section class="orch-panel orch-profile-panel">
    <header class="row"><div>
      <span class="summary-kicker">エージェントとモデルの組み合わせ</span>
      <h3>段の設定</h3>
      <p class="muted">small・medium・largeごとに使用する候補を優先順で設定します。</p>
    </div></header>
    <ol class="orch-profile-tiers" id="orch-profile-tiers">${tierRows}</ol>
    <div class="settings-save-actions">
      <button type="button" id="btn-orch-tiers-save" class="primary-inline"${state.orchSaving ? ' disabled' : ''}>保存</button>
    </div>
  </section>`;
}

function orchProfileConditionRowHtml(key, tier, ratio, index) {
  const pct = ratio === '' || ratio === undefined || ratio === null
    ? '' : String(Math.round(Number(ratio) * 100));
  const candidates = (tier.candidates || []).map(orchProfileCandidateText).filter((text) => text !== '—').join(' → ');
  return `<li class="orch-profile-condition" data-orch-tier-key="${esc(key)}">
    <span class="orch-profile-tier-no">${index + 1}</span>
    <div><strong>${esc(orchProfileTierLabel(null, key))}</strong><small class="mono muted">${esc(candidates || '候補未設定')}</small></div>
    <label><span>予算の残りがこの割合以上のとき</span><span class="orch-profile-ratio-input">
      <input type="number" class="mono orch-tier-ratio" min="0" max="100" step="5" value="${esc(pct)}" /> %
    </span></label>
  </li>`;
}

// agent-profiles の policy だけを編集する。段の候補は「ワークフロー」タブが持つ。
function orchProfilePolicyPanelHtml(overview) {
  const profiles = overview.profiles || { enabled: true, tiers: {}, policy: {}, state: {} };
  const tiers = profiles.tiers || {};
  const policy = profiles.policy || {};
  const stateMap = profiles.state || {};
  const ratioByTier = new Map((policy.steps || []).map((s) => [String(s.tier || ''), Number(s.min_remaining_ratio || 0)]));
  const rows = orchOrderedTiers(profiles);
  const conditionRows = rows.map(([key, tier], index) =>
    orchProfileConditionRowHtml(key, tier, ratioByTier.has(key) ? ratioByTier.get(key) : '', index)
  ).join('') || '<li class="orch-profile-empty muted">先に「ワークフロー」で段を設定してください。</li>';

  const applyToRows = ORCH_PROFILE_WORKLOADS.map((wl) => `<label class="orch-profile-apply-to">
      <input type="checkbox" class="orch-profile-apply-to-cb" value="${esc(wl)}" ${(policy.apply_to || []).includes(wl) ? 'checked' : ''} />
      ${esc(amigosWorkloadLabel(wl))}
    </label>`).join('');

  const noCapOptions = ['<option value="">（選ばない）</option>']
    .concat(rows.map(([key, tier]) => `<option value="${esc(key)}"${policy.no_cap_tier === key ? ' selected' : ''}>${esc(tier.label || key)}</option>`))
    .join('');

  const decisionRows = ORCH_PROFILE_WORKLOADS.map((wl) => {
    const rec = stateMap[wl];
    if (!rec) {
      return `<tr><td>${esc(amigosWorkloadLabel(wl))}</td><td colspan="3" class="muted">まだ評価していません</td></tr>`;
    }
    return `<tr>
      <td>${esc(amigosWorkloadLabel(wl))}</td>
      <td>${esc(orchProfileTierLabel(tiers, rec.tier))}</td>
      <td class="mono">${esc(orchProfileCandidateText(rec.candidate))}</td>
      <td><small class="muted">${esc(rec.reason || '')}</small></td>
    </tr>`;
  }).join('');

  const preview = state.orchProfilesPreview;
  const previewRows = preview
    ? Object.entries(preview.decisions || {}).map(([wl, d]) => `<tr>
        <td>${esc(amigosWorkloadLabel(wl))}</td>
        <td>${esc(orchProfileTierLabel((preview.profiles || {}).tiers, d.tier))}</td>
        <td class="mono">${esc(orchProfileCandidateText(d.candidate))}</td>
        <td><small class="muted">${esc(d.reason || '')}</small></td>
      </tr>`).join('')
    : '';
  const previewHtml = preview
    ? `<div class="orch-profile-preview">
        <strong>いまの予算で試算した結果（まだ反映していません）</strong>
        <table class="amigos-table orch-table">
          <thead><tr><th>機能</th><th>段</th><th>使うエージェント</th><th>理由</th></tr></thead>
          <tbody>${previewRows || '<tr><td colspan="4" class="muted">決められませんでした（段と割合、対象の機能を確認してください）</td></tr>'}</tbody>
        </table>
      </div>`
    : '';

  return `<section class="orch-panel orch-profile-panel">
    <header class="row">
      <div>
        <span class="summary-kicker">段の自動切り替え</span>
        <h3>切り替え条件</h3>
        <p class="muted">残り予算に応じて、使用する段を切り替えます。</p>
      </div>
    </header>
    <label class="orch-profile-toggle">
      <input type="checkbox" id="orch-profile-enabled" ${profiles.enabled !== false ? 'checked' : ''} />
      <span>予算に合わせて自動で切り替える</span>
    </label>
    <div class="orch-profile-apply-to-row">
      <strong>切り替える対象</strong>
      ${applyToRows}
    </div>
    <ol class="orch-profile-conditions" id="orch-profile-conditions">${conditionRows}</ol>
    <details class="orch-profile-advanced" data-ui-key="orch-profile-advanced">
      <summary>細かい調整</summary>
      <div class="orch-profile-advanced-grid">
        <label>予算の上限が無いときに使う段
          <select id="orch-profile-no-cap-tier">${noCapOptions}</select>
        </label>
        <label>上の段へ戻すときの余裕（%）
          <input type="number" min="0" max="100" step="1" id="orch-profile-hysteresis" class="mono"
            value="${Math.round(Number(policy.hysteresis !== undefined ? policy.hysteresis : 0.05) * 100)}" />
          <small class="muted">戻した直後にまた下がる往復を防ぎます。</small>
        </label>
        <label>段を変えたら維持する時間（分）
          <input type="number" min="0" step="5" id="orch-profile-min-hold" class="mono"
            value="${Math.round(Number(policy.min_hold_sec !== undefined ? policy.min_hold_sec : 900) / 60)}" />
        </label>
      </div>
    </details>
    <div class="settings-save-actions">
      <div class="settings-secondary-actions">
        <button type="button" id="btn-orch-profile-evaluate">いまの予算で試算</button>
        <button type="button" id="btn-orch-profile-apply">いますぐ反映</button>
      </div>
      <button type="button" id="btn-orch-profile-policy-save" class="primary-inline"${state.orchSaving ? ' disabled' : ''}>保存</button>
    </div>
    ${previewHtml}
    <h4>いま選ばれている段</h4>
    <table class="amigos-table orch-table">
      <thead><tr><th>機能</th><th>段</th><th>使うエージェント</th><th>理由</th></tr></thead>
      <tbody>${decisionRows}</tbody>
    </table>
  </section>`;
}

// ワークロード別の「用途 / ロール / ノード種別」候補（agents.<key> 上書きのキー補完）。
// project: AGENT_PURPOSES / flow: 役割＋ノード kind / amigos: ロール id は動的（自由入力）/
// routine: 定常業務は 1 セッションに 1 つの対話 CLI を起こすだけで用途の区別が無いため、
//   機能単位（agent_cli / model）だけを持つ（その宣言は定常業務の起動に効く）。
const ORCH_AGENT_KEYS = {
  project: ['plan', 'review', 'prioritize', 'route', 'adjudicate', 'verify',
    'distill', 'assess', 'repo_map', 'doctor'],
  flow: ['planner', 'evaluator', 'worker', 'work', 'generate', 'classify',
    'synthesize', 'verify', 'filter', 'judge', 'reduce', 'split', 'map'],
  amigos: [],
  routine: [],
};

function timeoutSeconds(value) {
  const raw = String(value == null ? '' : value).trim();
  if (raw === '') return null;
  const minutes = Number(raw);
  return Number.isInteger(minutes) && minutes >= 1 ? minutes * 60 : NaN;
}

// 1 ワークロードの用途 / ロール別上書きの小テーブル（既存キーの編集・削除＋新規追加行）。
function orchAgentsEditorHtml(wl, wc) {
  const agents = wc.agents || {};
  const keys = Object.keys(agents);
  const known = ORCH_AGENT_KEYS[wl];
  const withTimeout = wl === 'flow';
  // routine は用途別の概念が無い（1 セッションに 1 つの CLI を起こすだけ）ため編集 UI を
  // 出さない。上の「エージェント / モデル」は定常業務の起動にそのまま効く。
  if (known && known.length === 0 && wl === 'routine') {
    return '<div class="orch-agents-none"><small class="muted">定常業務では、上のエージェントとモデルが'
      + 'そのまま起動に使われます。用途ごとの変更はできません。</small></div>';
  }
  const listId = `orch-keys-${esc(wl)}`;
  const datalist = (known && known.length)
    ? `<datalist id="${listId}">${known.map((k) => `<option value="${esc(k)}"></option>`).join('')}</datalist>`
    : '';
  const rows = keys.map((key) => {
    const ov = agents[key] || {};
    return `<tr class="orch-agent-row" data-orch-key="${esc(key)}">
      <td><code>${esc(key)}</code></td>
      <td><input type="text" class="orch-agent-cli" placeholder="既定" value="${esc(ov.agent_cli || '')}" /></td>
      <td><input type="text" class="orch-agent-model" placeholder="既定" value="${esc(ov.model || '')}" /></td>
      ${withTimeout ? `<td><input type="number" min="1" step="1" class="orch-agent-timeout"
        aria-label="${esc(key)}の1回の上限（分）" placeholder="共通値" value="${ov.timeout_sec ? Math.ceil(Number(ov.timeout_sec) / 60) : ''}" /></td>` : ''}
      <td><label class="orch-agent-rm"><input type="checkbox" class="orch-agent-remove" /> 削除</label></td>
    </tr>`;
  }).join('');
  const addRow = `<tr class="orch-agent-add">
      <td><input type="text" class="orch-agent-new-key" list="${listId}" placeholder="${(known && known.length) ? '用途 / 担当名' : '担当名'}" /></td>
      <td><input type="text" class="orch-agent-new-cli" placeholder="エージェント" /></td>
      <td><input type="text" class="orch-agent-new-model" placeholder="モデル" /></td>
      ${withTimeout ? '<td><input type="number" min="1" step="1" class="orch-agent-new-timeout" aria-label="追加する用途の1回の上限（分）" placeholder="共通値" /></td>' : ''}
      <td><small class="muted">保存で追加</small></td>
    </tr>`;
  const summaryLabel = keys.length ? `用途 / 担当ごとの変更 ${keys.length}件` : '用途 / 担当ごとの変更を追加';
  return `<details class="orch-agents" data-ui-key="orch-agents-${esc(wl)}"${keys.length ? ' open' : ''}>
    <summary>${esc(summaryLabel)}</summary>
    ${datalist}
    <table class="amigos-table orch-agents-table">
      <thead><tr><th>用途 / 担当 / 種別</th><th>エージェント</th><th>モデル</th>${withTimeout ? '<th>1回の上限（分）</th>' : ''}<th></th></tr></thead>
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
    const timeout = wc.timeout_sec ? Math.ceil(Number(wc.timeout_sec) / 60) : '';
    return `<div class="orch-ctrl-wl" data-orch-ctrl-wl="${esc(wl)}">
      <div class="orch-ctrl-head">
        <strong>${esc(amigosWorkloadLabel(wl))}</strong>
        <label>エージェント<input type="text" class="orch-ctrl-cli" placeholder="各機能の設定を使用" value="${esc(wc.agent_cli || '')}" /></label>
        <label>モデル<input type="text" class="orch-ctrl-model" placeholder="各機能の設定を使用" value="${esc(wc.model || '')}" /></label>
        <label>節約時のモデル<input type="text" class="orch-ctrl-degraded-model" placeholder="変更しない" value="${esc(deg.model || '')}" /></label>
        ${wl === 'flow' ? `<label>1回の上限（分）<input type="number" min="1" step="1" class="orch-ctrl-timeout"
          placeholder="各プロジェクトの設定を使用" value="${timeout}" /></label>` : ''}
      </div>
      ${orchAgentsEditorHtml(wl, wc)}
      ${wl === 'flow' ? '<p class="field-help">空欄は各プロジェクトの設定を使います。再試行が有効な場合、この上限が複数回適用されます。</p>' : ''}
    </div>`;
  }).join('');
  return `<section class="orch-panel">
    <header class="row">
      <div>
        <span class="summary-kicker">担当設定</span>
        <h3>機能ごとのエージェントとモデル</h3>
        <p class="muted">機能ごとに指定し、空欄は既定の設定を使います。</p>
      </div>
      <div>${orchBadge('muted', `設定版 ${Number(control.revision || 0)}`)}</div>
    </header>
    <div class="orch-ctrl-blocks">${blocks}</div>
    <div class="settings-save-actions">
      <button type="button" id="btn-orch-control-save" class="primary-inline"${state.orchSaving ? ' disabled' : ''}>保存</button>
    </div>
  </section>`;
}

// 3.5 同時実行数（agent-control の concurrency）。**この端末の資源の話**なので全体設定に置く。
// 従来の置き場（各プロジェクトの agent-flow.yaml の max_runs / workers）は、1 台の負荷を
// 下げたい人に全プロジェクトの yaml を直して回らせていた。ここで宣言すると、その端末の
// agent-flow が次の巡回で読む（pull 型。dashboard はプロセスを起こさない）。
function orchConcurrencyPanelHtml(overview) {
  const control = overview.control || { workloads: {} };
  const flow = (control.workloads || {}).flow || {};
  const cc = (flow.concurrency && typeof flow.concurrency === 'object') ? flow.concurrency : {};
  const maxRuns = cc.max_runs === undefined || cc.max_runs === null ? '' : Number(cc.max_runs);
  const workers = cc.workers === undefined || cc.workers === null ? '' : Number(cc.workers);
  const note = maxRuns === '' && workers === ''
    ? '<span class="muted">いまは各プロジェクトの設定に従っています。</span>'
    : orchBadge('ok', 'この端末の指定を使用中');
  return `<section class="orch-panel">
    <header class="row">
      <div>
        <span class="summary-kicker">実行のキャパシティ</span>
        <h3>同時に動かす数（自動実行）</h3>
        <p class="muted">この端末で同時に進める仕事と担当の数を設定します。</p>
      </div>
      <div>${note}</div>
    </header>
    <div class="row orch-alloc-controls">
      <label>同時に進める仕事の数
        <input type="number" min="0" step="1" id="orch-conc-max-runs" class="mono"
          value="${maxRuns}" placeholder="プロジェクト設定に従う" title="0 = 上限なし" />
      </label>
      <label>1つの仕事に付ける担当の数
        <input type="number" min="1" step="1" id="orch-conc-workers" class="mono"
          value="${workers}" placeholder="プロジェクト設定に従う" />
      </label>
    </div>
    <p class="field-help">0 は上限なしです。承認待ちは数えず、超えた依頼は空き次第始まります。</p>
    <div class="settings-save-actions">
      <button type="button" id="btn-orch-conc-save" class="primary-inline"${state.orchSaving ? ' disabled' : ''}>保存</button>
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
        <p class="muted">機能ごとに実行を許可・停止します。停止中は同種の呼び出しを拒否します。</p>
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
  // 取得結果が配列でない（IPC が壊れた・古い版）ときも画面を落とさない。ここで例外にすると
  // 全体設定ページごと空になる——1 つの一覧のために設定画面を丸ごと失う割に合わない。
  const inv = Array.isArray(state.orchSkillsInventory) ? state.orchSkillsInventory : [];
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
  return `<section class="orch-panel">
    <header class="row">
      <div>
        <span class="summary-kicker">共通指示</span>
        <h3>すべてのエージェントへの共通指示</h3>
        <p class="muted">全エージェントに共通する指示・スキル・ツールを設定し、個別の指示を優先します。</p>
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
      <label class="orch-sess-strategy" ${mode === 'chat' ? '' : 'hidden'} title="従来の 1 コマンド 1 ペースト式ではなく、同じ指定の行を 1 つの起動アクション束にまとめます">
        <input type="checkbox" class="orch-sess-bundle" ${strategy === 'bundle' ? 'checked' : ''} /><span>まとめて依頼</span>
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
          <input type="text" class="orch-sess-when-engines mono" placeholder="agent-loop, agent-flow" value="${esc(list(when.engines))}" />
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

function orchSessionCommandsPanelHtml(overview) {
  const sc = overview.sessionCommands || { enabled: true, commands: [], revision: 0, max_total_timeout: 120 };
  const commands = Array.isArray(sc.commands) ? sc.commands : [];
  const warnings = Array.isArray(sc.warnings) ? sc.warnings : [];
  const rows = commands.map((c, i) => orchSessionRowHtml(c, i)).join('')
    || '<p class="muted orch-sess-empty">登録されたコマンドはありません。</p>';
  return `<section class="orch-panel orch-sess-panel">
    <header class="row">
      <div>
        <span class="summary-kicker">使用するコマンド</span>
        <h3>エージェントを始める前のコマンド</h3>
        <p class="muted">通常は不要で、開始前に毎回必要なコマンドだけを追加します。</p>
      </div>
      <div>${sc.enabled ? orchBadge('ok', `有効・設定版 ${esc(String(sc.revision || 0))}`) : orchBadge('soft', '無効')}</div>
    </header>
    ${warnings.map((warning) => `<p class="notice warn" role="status">${esc(warning)}</p>`).join('')}
    <label class="orch-sess-enabled"><input type="checkbox" id="orch-sess-enabled" ${sc.enabled ? 'checked' : ''} /> 登録したコマンドを使用する</label>
    <div class="orch-sess-list" id="orch-sess-list">${rows}</div>
    <div class="row orch-sess-controls">
      <button type="button" id="btn-orch-sess-add">コマンドを追加</button>
      <label class="orch-sess-field orch-sess-narrow"><span>すべての合計の上限秒</span>
        <input type="number" id="orch-sess-max-total" min="1" max="600" value="${esc(String(sc.max_total_timeout || 120))}" />
      </label>
    </div>
    <ul class="orch-sess-notes muted">
      <li>上から順に実行し、変更は次のセッションから反映します。</li>
      <li>空白を含むパスは <code>"</code> で囲みます。</li>
      <li>「開始を中止する」は失敗時にエージェントを起動しません。</li>
    </ul>
    <div class="settings-save-actions">
      <button type="button" id="btn-orch-sess-save" class="primary-inline">保存</button>
    </div>
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
        <p class="muted">利用できるエージェントを確認・編集します。</p>
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

// 全体設定に残すのは「この端末のすべてのワークロードに効くもの」と「このアプリ自身の
// 設定」だけ。特定の agent-* だけに効く設定はその道具の領域へ置いた——利用状況
// （agent-audit）は利用状況領域へ、定常業務（cowork）は定常業務領域の設定タブへ、
// プロジェクトの定義はプロジェクト設定へ。どこを触ればどこに効くのかを、設定の置き場所
// そのもので示す（全部を 1 ページに集めると、効く範囲が読めなくなる）。
const GLOBAL_SETTINGS_SECTIONS = [
  { id: 'app', label: 'アプリ' },
  { id: 'agents', label: 'エージェント' },
  { id: 'instructions', label: '共通指示' },
  { id: 'methods', label: 'ワークフロー' },
  { id: 'control', label: '実行制御' },
  { id: 'sync', label: '同期と実行' },
  { id: 'integrations', label: '外部連携' },
];
// 領域へ移した設定（保存時の表示名の解決に使う。移設後も保存経路は 1 本のまま）
const RELOCATED_SETTINGS_LABELS = { usage: '利用状況', routine: '定常業務' };
const ORCHESTRATION_SETTINGS_SECTIONS = new Set(['agents', 'instructions', 'methods', 'control']);

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
      <p class="muted">画面更新と通知の動作を設定します。</p>
    </header>
    <p class="field-help">プロジェクト一覧は実行エンジンから自動で受け取ります。</p>
    <div class="row2">
      <div class="field"><label for="cfg-refresh">表示の更新間隔（秒）</label><input id="cfg-refresh" type="number" min="0" step="1" /></div>
      <div class="field"><label for="cfg-needs-sla">長時間未対応として知らせるまで（時間）</label><input id="cfg-needs-sla" type="number" min="1" step="1" /></div>
    </div>
    <div class="field"><label class="check"><input type="checkbox" id="cfg-notify" /> 対応が必要になったら通知する</label></div>
    <div class="field">
      <label for="cfg-role">この PC の役割</label>
      <select id="cfg-role">
        <option value="engineer">実行も行う</option>
        <option value="viewer">閲覧・レビュー専用</option>
      </select>
      <p class="field-help">閲覧専用では、監視・コメント・承認だけを行います。実行用の環境設定は不要です。</p>
    </div>
    <div class="field">
      <label>セットアップ診断</label>
      <p class="field-help">受け取ったプロジェクトのフォルダが正しく共有できるか確認します。</p>
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
      <p class="muted">使用するエージェントを選び、必要ならモデルと待ち時間を変更します。</p>
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
      <p class="muted">実行場所と端末間の共有先を設定します。</p>
    </header>
    <h3>実行エンジンの場所</h3>
    <p class="field-help">プロジェクト一覧と稼働状況を受け取る場所です。</p>
    <div class="row2">
      <div class="field"><label for="cfg-engine-distro">Linux 環境（WSL）の名前</label><input id="cfg-engine-distro" class="mono" placeholder="空欄なら既定の環境" /></div>
      <div class="field"><label for="cfg-engine-home">状況の保存先</label><input id="cfg-engine-home" class="mono" placeholder="空欄なら自動で探します" /></div>
    </div>
    <div class="field"><label for="cfg-node-commands-dir">この端末への指示の受け渡し先</label>
      <input id="cfg-node-commands-dir" class="mono" placeholder="空欄なら実行エンジンと同じ場所" /></div>
    <p class="field-help">画面からの操作依頼を実行エンジンへ渡す場所です。</p>
    <h3>実行データの共有先</h3>
    <div class="field"><label for="cfg-flow-bus">共通の共有先</label><input id="cfg-flow-bus" class="mono" placeholder="空欄なら自動で探します" /></div>
    <div class="field"><label for="cfg-flow-bus-by-project">プロジェクトごとの共有先（1行に1つ）</label>
      <textarea id="cfg-flow-bus-by-project" class="mono" rows="4" placeholder="alpha = /home/me/clones/alpha/agent-flow"></textarea></div>
    <div class="settings-save-actions"><button type="button" id="btn-save-sync-settings" class="primary-inline">保存</button></div>
    ${boardParticipationHtml()}
  </div>`;
}

// 「この端末は、ほかの端末と仕事をやり取りできているか」。
// 設定画面に置くのは**構成の確認**だから——動く公示の一覧はタスク画面（委任先）と
// 参加画面（引き受ける）に置く。ここは「参加できているか」だけを言う。
function boardParticipationHtml() {
  const board = state.boardStatus;
  if (!board || !board.configured) {
    return `<h3>ほかの端末との仕事のやり取り</h3>
      <p class="field-help">この端末は参加していません。参加先は実行エンジンで設定します。</p>`;
  }
  const nodes = state.boardNodes || [];
  const rows = nodes.length
    ? nodes.map((n) => `<tr class="${n.stale ? 'is-stale' : ''}">
        <td class="mono">${esc(n.name)}</td>
        <td>${n.stale ? '<span class="muted">応答なし</span>' : '稼働中'}</td>
        <td>${esc([...(n.workloads || []), ...(n.tags || []), ...(n.agentCli || [])].join('・') || '—')}</td>
        <td>${esc((n.repos || []).join('・') || '—')}</td>
        <td>${n.contractVersion ? esc(String(n.contractVersion)) : '<span class="muted">未宣言</span>'}</td>
      </tr>`).join('')
    : '<tr><td colspan="5" class="muted">まだどの端末も参加を宣言していません</td></tr>';
  const intake = (board.intakeProjects || []).length
    ? `落札した仕事は ${esc((board.intakeProjects || []).join('・'))} で実行します`
    : board.nodeDirect
      ? '引き受けた仕事はこの端末がじかに実行します'
      : 'この端末はまだ仕事を引き受けても実行できません（プロジェクトを 1 つも持っていません）';
  const err = board.lastError
    ? `<p class="need-error">やり取り先に接続できていません: ${esc(board.lastError)}</p>` : '';
  return `<h3>ほかの端末との仕事のやり取り</h3>
    <p class="field-help">端末名: <span class="mono">${esc(board.selfName || '')}</span> ／
    参加先: <span class="mono">${esc(board.location || '')}</span> ／ 募集中 ${board.openDelegations} 件 ／
    申込中 ${(board.myBids || []).length} 件。${esc(intake)}。</p>
    ${err}
    <div class="table-scroll"><table class="board-nodes">
      <thead><tr><th>端末</th><th>状態</th><th>引き受けられるもの</th>
        <th>手元にあるリポジトリ</th><th>版</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

// 登録済みの定常業務フォルダ（cowork.roots）。**定常業務画面ではなくここに置く**——
// 定常業務画面はプロジェクトを 1 つ選んでから開く画面なので、実行エンジンがまだ動いて
// いない初期状態（選べるプロジェクトが 1 つも無い）では最初の 1 件を登録できなかった。
// 全体設定はプロジェクト選択に依存しないので、そこからなら初期状態でも登録できる。
// 定常業務の対象フォルダ。**この画面で登録したフォルダ（cowork.roots）と、プロジェクトと
// して登録済みのフォルダの両方**を並べる。走査はもともと両方を回っていて、片方だけ出すと
// 「一覧に無いフォルダの作業が定常業務画面に出てくる」ことになり、どこから来たのか読めない。
// プロジェクト側は登録簿が違う（実行エンジンの host.yaml）ので、ここからは解除できない
// ——押せない理由を書いて出す方が、出さずに黙っているより短く済む。
function globalSettingsCoworkRootsHtml() {
  const roots = ((state.config && state.config.cowork) || {}).roots || [];
  const declared = new Set(roots.map((r) => coworkPathKey(r)));
  // ここで登録していないのに対象になっているフォルダ（プロジェクト・ユーザーホーム）も
  // 並べる。一覧に無いフォルダの作業が定常業務画面に出てくると、出どころが読めない。
  const managed = (state.discovery.projects || [])
    .filter((p) => p && p.dir && !declared.has(coworkPathKey(p.dir)));
  const managedNote = (p) => (p.kind === 'project'
    ? 'プロジェクトとして登録されているため、ここでは解除できません'
    : 'ユーザーホームは常に対象です（解除できません）');
  const rows = [
    ...roots.map((r) => `<li>
      <code class="mono">${esc(String(r))}</code>
      <button type="button" class="linklike" data-drop-cowork-root="${esc(String(r))}">登録を解除</button>
    </li>`),
    ...managed.map((p) => `<li>
      <code class="mono">${esc(String(p.dir))}</code>
      <span class="muted">${esc(managedNote(p))}</span>
    </li>`),
  ].join('');
  return `<div class="field">
    <label>定常業務のフォルダ</label>
    <p class="field-help">プロジェクト以外で定常業務を探すフォルダを登録します。</p>
    ${rows ? `<ul class="settings-root-list">${rows}</ul>`
      : '<p class="muted">対象のフォルダはありません。</p>'}
    <div class="row"><button type="button" id="btn-settings-cowork-add-root">フォルダを登録</button></div>
  </div>`;
}

function globalSettingsRoutineHtml() {
  return `<div class="global-settings-card">
    <header class="global-settings-card-heading">
      <span class="summary-kicker">定常業務</span>
      <h2>定期実行と定型処理</h2>
      <p class="muted">定常業務を動かすフォルダとコマンドを設定します。コマンドは通常は変更不要です。</p>
    </header>
    ${globalSettingsCoworkRootsHtml()}
    <div class="row2">
      <div class="field"><label for="cfg-cowork-loop-command">定期実行コマンド</label>
        <input id="cfg-cowork-loop-command" class="mono" placeholder="agent-loop" />
        <small class="field-help">未入力なら agent-loop を使います。</small>
      </div>
      <div class="field"><label for="cfg-cowork-sm-command">定型処理コマンド</label>
        <input id="cfg-cowork-sm-command" class="mono" placeholder="statemachine-use" />
        <small class="field-help">未入力なら statemachine-use を使います。</small>
      </div>
    </div>
    <div class="row"><button type="button" id="btn-settings-cowork-open">定常業務を開く</button></div>
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
      <div class="field"><label for="cfg-rv-exepath">実行ファイルの場所</label><input id="cfg-rv-exepath" class="mono" placeholder="例: C:\\Apps\\GitLab Review Viewer.exe" /></div>
    </div>
    <div class="field"><label for="cfg-rv-command">起動コマンド</label><input id="cfg-rv-command" class="mono" placeholder="{url} などの値を利用できます" /></div>
    <div class="settings-save-actions"><button type="button" id="btn-save-integrations-settings" class="primary-inline">保存</button></div>
  </div>`;
}

function globalSettingsAgentsHtml(overview) {
  if (!overview) return `${globalSettingsAssistantHtml()}<div class="empty compact">エージェント情報を読み込んでいます。</div>`;
  if (overview.error) return `${globalSettingsAssistantHtml()}<div class="empty compact"><strong>エージェント情報を読み込めませんでした</strong><span>${esc(overview.error)}</span></div>`;
  return `${globalSettingsAssistantHtml()}
    ${orchMatrixPanelHtml(overview)}
    ${orchInventoryPanelHtml(overview)}`;
}

// 「利用状況」= この端末の利用量をまとめて見る場所。**数字は agent-audit の集計へ一本化**した。
// 以前はここでノード予算の台帳を画面が自分で足した「利用量」を出し、その下に agent-audit が
// 集計した「実測のトークン利用量」が並んでいた——同じ話題の数字が 2 つあり、しかも台帳は
// agent-audit の源泉の 1 つ（budget-ledger）なので、実測の裏取りがある後者の方が正しい。
// 面の中身は features/agent-audit.js が出し、agent-audit が使えない端末では台帳集計
// （orchBudgetPanelHtml）へフォールバックする。上限・配分の設定は「実行制御」が持ち、
// この節はその上限をゲージの分母として読むだけ。
// 面の受け側は sections/usage.js（利用状況領域）へ移した。全体設定は「端末のすべての
// ワークロードに効く設定」だけを持つ場所になり、道具ごとの実績値はその道具の領域が出す。

function globalSettingsInstructionsHtml(overview) {
  if (!overview) return '<div class="empty compact">共通指示を読み込んでいます。</div>';
  if (overview.error) return `<div class="empty compact"><strong>共通指示を読み込めませんでした</strong><span>${esc(overview.error)}</span></div>`;
  return `${orchInstructionsPanelHtml(overview)}${orchSessionCommandsPanelHtml(overview)}`;
}

function orchMethodRoles(method) {
  return [...new Set((method.fragments || []).map((fragment) => String(fragment.role || '')).filter(Boolean))];
}

function orchMethodConditionsHtml(method) {
  const when = method.when || {};
  const labels = {
    tiers: '段', purposes: '用途', workloads: '機能', agent_cli: 'エージェント', models: 'モデル',
    engines: 'エンジン', roles: 'ロール',
  };
  const chips = Object.entries(labels).flatMap(([key, label]) => {
    const values = Array.isArray(when[key]) ? when[key] : [];
    return values.map((value) => `<span class="orch-method-condition">${esc(label)}: ${esc(key === 'tiers' ? (ORCH_TIER_LABELS[value] || value) : value)}</span>`);
  });
  if (Number.isFinite(Number(when.min_relative_cost))) {
    chips.push(`<span class="orch-method-condition">相対コスト: ${esc(when.min_relative_cost)}以上</span>`);
  }
  if (Number.isFinite(Number(when.max_relative_cost))) {
    chips.push(`<span class="orch-method-condition">相対コスト: ${esc(when.max_relative_cost)}以下</span>`);
  }
  return chips.join('') || '<span class="muted">すべてに適用</span>';
}

function orchMethodCardHtml(method, current) {
  const enabled = !!(current && current.enabled !== false);
  const roles = orchMethodRoles(method);
  const source = (current && current.source) || method.source || method.origin || '';
  const search = [method.id, method.description, source, roles.join(' '), JSON.stringify(method.when || {})]
    .join(' ').toLowerCase();
  return `<article class="orch-method-card" data-orch-method-search="${esc(search)}"
    data-orch-method-role="${esc(roles.join(' '))}" data-orch-method-state="${enabled ? 'enabled' : 'disabled'}">
    <header>
      <div><strong>${esc(method.id)}</strong><p>${esc(method.description || '')}</p></div>
      <button type="button" data-orch-method-id="${esc(method.id)}"
        data-orch-method-enabled="${enabled ? 'false' : 'true'}" aria-pressed="${enabled}">${enabled ? 'ON' : 'OFF'}</button>
    </header>
    <div class="orch-method-roles">${roles.map((role) => `<span>${esc(role)}</span>`).join('') || '<span>session</span>'}</div>
    <div class="orch-method-conditions">${orchMethodConditionsHtml(method)}</div>
    <small class="mono muted">${esc(source || '反映元不明')}</small>
  </article>`;
}

function orchMethodDialogHtml() {
  return `<dialog id="dlg-orch-method-add" class="orch-method-dialog">
    <form method="dialog" class="dialog-shell">
      <h2 class="dialog-heading dialog-heading-simple">カスタム手法を追加</h2>
      <div class="dialog-scroll-body">
        <div class="orch-method-ai-box">
          <label for="orch-method-brief">作りたい手法</label>
          <textarea id="orch-method-brief" rows="3" placeholder="例: 実装前に失敗条件を洗い出し、検証担当にも同じ観点を渡す"></textarea>
          <div class="row"><button type="button" id="btn-orch-method-ai">AIで補完</button>
            <span id="orch-method-ai-status" class="muted" aria-live="polite"></span></div>
        </div>
        <div class="row2">
          <label for="orch-method-id">ID<input id="orch-method-id" class="mono" placeholder="failure-first" /></label>
          <label for="orch-method-role">ロール<select id="orch-method-role">
            <option value="">選択してください</option><option>session</option><option>planner</option>
            <option>worker</option><option>verify</option><option>evaluator</option>
          </select></label>
        </div>
        <label for="orch-method-description">説明<input id="orch-method-description" placeholder="何を変える手法かを短く記述" /></label>
        <label for="orch-method-text">エージェントへ渡す指示<textarea id="orch-method-text" rows="5"></textarea></label>
        <details class="orch-method-when" data-ui-key="orch-method-when">
          <summary>適用条件</summary>
          <div class="row2">
            <label for="orch-method-tiers">段<input id="orch-method-tiers" class="mono" placeholder="small, medium, large" /></label>
            <label for="orch-method-purposes">用途<input id="orch-method-purposes" class="mono" placeholder="work, verify" /></label>
            <label for="orch-method-workloads">機能<input id="orch-method-workloads" class="mono" placeholder="project, flow" /></label>
            <label for="orch-method-agent-cli">エージェント<input id="orch-method-agent-cli" class="mono" placeholder="claude, codex" /></label>
            <label for="orch-method-models">モデル<input id="orch-method-models" class="mono" placeholder="opus, gpt-5" /></label>
          </div>
          <small class="muted">複数指定はカンマで区切ります。空欄の条件は制限しません。</small>
        </details>
        <p id="orch-method-form-status" class="orch-method-form-status" aria-live="polite"></p>
      </div>
      <div class="dialog-actions">
        <button value="cancel">キャンセル</button>
        <button type="button" id="btn-orch-method-add" class="primary-inline">追加</button>
      </div>
    </form>
  </dialog>`;
}

function orchMethodsPanelHtml(overview) {
  const tuning = overview.tuning || { revision: 0, methods: [] };
  const active = new Map((tuning.methods || []).map((method) => [String(method.id), method]));
  const catalog = overview.methodsCatalog || [];
  const ids = new Set(catalog.map((method) => String(method.id)));
  const methods = catalog.concat((tuning.methods || []).filter((method) => !ids.has(String(method.id))));
  const cards = methods.map((method) => orchMethodCardHtml(method, active.get(String(method.id)))).join('');
  return `<section class="orch-panel orch-method-market">
    <header class="row"><div><span class="summary-kicker">実行手法</span><h3>手法を探す</h3>
      <p class="muted">目的に合う手法を検索して有効にします。</p></div>
      <button type="button" id="btn-orch-method-dialog" class="primary-inline">カスタム手法を追加</button>
    </header>
    <div class="orch-method-search">
      <label for="orch-method-search">手法を検索<input type="search" id="orch-method-search"
        value="${esc(state.orchMethodSearch || '')}" placeholder="名前、説明、反映元で検索" autocomplete="off" /></label>
      <label for="orch-method-role-filter">ロール<select id="orch-method-role-filter">
        <option value="">すべて</option>${['session', 'planner', 'worker', 'verify', 'evaluator'].map((role) =>
    `<option value="${role}"${state.orchMethodRole === role ? ' selected' : ''}>${role}</option>`).join('')}
      </select></label>
      <label for="orch-method-state-filter">状態<select id="orch-method-state-filter">
        <option value="">すべて</option><option value="enabled"${state.orchMethodState === 'enabled' ? ' selected' : ''}>ON</option>
        <option value="disabled"${state.orchMethodState === 'disabled' ? ' selected' : ''}>OFF</option>
      </select></label>
      <span id="orch-method-result-count" class="muted" aria-live="polite">${methods.length}件</span>
    </div>
    <div class="orch-method-grid" id="orch-method-grid">${cards}</div>
    <p id="orch-method-empty" class="empty compact" hidden>一致する手法がありません。検索語や絞り込みを変更してください。</p>
    ${orchMethodDialogHtml()}
  </section>`;
}

function globalSettingsMethodsHtml(overview) {
  if (!overview) return '<div class="empty compact">ワークフローを読み込んでいます。</div>';
  if (overview.error) return `<div class="empty compact"><strong>ワークフローを読み込めませんでした</strong><span>${esc(overview.error)}</span></div>`;
  return `${orchTiersPanelHtml(overview)}${orchMethodsPanelHtml(overview)}`;
}

function globalSettingsControlHtml(overview) {
  if (!overview) return '<div class="empty compact">実行制御を読み込んでいます。</div>';
  if (overview.error) return `<div class="empty compact"><strong>実行制御を読み込めませんでした</strong><span>${esc(overview.error)}</span></div>`;
  return `${orchAllocationPanelHtml(overview.budget)}${orchConcurrencyPanelHtml(overview)}${orchProfilePolicyPanelHtml(overview)}${orchStatusPanelHtml(overview)}`;
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
        <div class="row global-settings-agent-refresh" ${ORCHESTRATION_SETTINGS_SECTIONS.has(section) ? '' : 'hidden'}><button id="btn-orch-refresh">最新の状態にする</button></div>
      </header>
      <div class="global-settings-tabs" role="tablist" aria-label="設定の種類">${tabs}</div>
      <label class="global-settings-select" for="global-settings-select">設定の種類
        <select id="global-settings-select">${options}</select>
      </label>
      ${globalSettingsPaneHtml('app', globalSettingsAppHtml())}
      ${globalSettingsPaneHtml('agents', globalSettingsAgentsHtml(ov))}
      ${globalSettingsPaneHtml('instructions', globalSettingsInstructionsHtml(ov))}
      ${globalSettingsPaneHtml('methods', globalSettingsMethodsHtml(ov))}
      ${globalSettingsPaneHtml('control', globalSettingsControlHtml(ov))}
      ${globalSettingsPaneHtml('sync', globalSettingsSyncHtml())}
      ${globalSettingsPaneHtml('integrations', globalSettingsIntegrationsHtml())}
    </div>`;
  populateSettingsFields();
  setupGlobalSettings(el);
  setupOrchestration(el);
  wireGlobalSettingsPanels(el);
  revealGlobalSettingsPanels(el, section);
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
  if (refresh) refresh.hidden = !ORCHESTRATION_SETTINGS_SECTIONS.has(section);
  // 差し込まれた面へ「表示された」と伝える（初回のデータ取得はここで起きる）。
  // 節の切り替えは hidden を付け替えるだけで描き直さないので、描画側の口では届かない。
  revealGlobalSettingsPanels(root, section);
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
    'btn-save-integrations-settings': 'integrations',
  };
  for (const [id, section] of Object.entries(saveButtons)) {
    const button = root.querySelector(`#${id}`);
    const label = (GLOBAL_SETTINGS_SECTIONS.find((item) => item.id === section) || {}).label || '全体';
    if (button) button.addEventListener('click', () => guard(`${label}設定の保存`, () => saveGlobalSettingsSection(section)));
  }
  // 定常業務の設定（roots の登録・解除・各コマンド）の配線は sections/routines.js が持つ。
  // 設定そのものを定常業務領域へ移したので、配線も一緒に移した。
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
    if (orchestrationDraftActive()) {
      return toast('入力中の設定を保存してから最新の状態にしてください');
    }
    return guard('エージェント情報の更新', async () => { await refreshOrchestration(); renderOrchestration(); });
  });

  const methodSearch = root.querySelector('#orch-method-search');
  const methodRole = root.querySelector('#orch-method-role-filter');
  const methodState = root.querySelector('#orch-method-state-filter');
  const filterMethods = () => {
    const query = String((methodSearch && methodSearch.value) || '').trim().toLowerCase();
    const role = String((methodRole && methodRole.value) || '');
    const enabled = String((methodState && methodState.value) || '');
    state.orchMethodSearch = query;
    state.orchMethodRole = role;
    state.orchMethodState = enabled;
    let shown = 0;
    for (const card of root.querySelectorAll('.orch-method-card')) {
      const visible = (!query || card.dataset.orchMethodSearch.includes(query))
        && (!role || card.dataset.orchMethodRole.split(' ').includes(role))
        && (!enabled || card.dataset.orchMethodState === enabled);
      card.hidden = !visible;
      if (visible) shown += 1;
    }
    const count = root.querySelector('#orch-method-result-count');
    const empty = root.querySelector('#orch-method-empty');
    if (count) count.textContent = `${shown}件`;
    if (empty) empty.hidden = shown > 0;
  };
  if (methodSearch) methodSearch.addEventListener('input', filterMethods);
  if (methodRole) methodRole.addEventListener('change', filterMethods);
  if (methodState) methodState.addEventListener('change', filterMethods);
  filterMethods();

  for (const btn of root.querySelectorAll('[data-orch-method-id]')) {
    btn.addEventListener('click', () => guard('手法設定の保存', async () => {
      await api.orchestrationMethodSet({ id: btn.dataset.orchMethodId,
        enabled: btn.dataset.orchMethodEnabled === 'true' });
      await refreshOrchestration();
      renderOrchestration();
    }));
  }
  const methodDialog = root.querySelector('#dlg-orch-method-add');
  const methodDialogOpen = root.querySelector('#btn-orch-method-dialog');
  if (methodDialogOpen && methodDialog) methodDialogOpen.addEventListener('click', () => methodDialog.showModal());
  const csv = (id) => String((root.querySelector(id) || {}).value || '')
    .split(',').map((value) => value.trim()).filter(Boolean);
  const readMethodForm = () => {
    const when = {};
    for (const [key, id] of Object.entries({
      tiers: '#orch-method-tiers', purposes: '#orch-method-purposes', workloads: '#orch-method-workloads',
      agent_cli: '#orch-method-agent-cli', models: '#orch-method-models',
    })) {
      const values = csv(id);
      if (values.length) when[key] = values;
    }
    return {
      id: String((root.querySelector('#orch-method-id') || {}).value || '').trim(),
      description: String((root.querySelector('#orch-method-description') || {}).value || '').trim(),
      role: String((root.querySelector('#orch-method-role') || {}).value || ''),
      text: String((root.querySelector('#orch-method-text') || {}).value || '').trim(),
      when,
    };
  };
  const setMethodFormStatus = (text, error = false) => {
    const status = root.querySelector('#orch-method-form-status');
    if (!status) return;
    status.textContent = text;
    status.classList.toggle('is-error', error);
    status.setAttribute('role', error ? 'alert' : 'status');
  };
  const methodAdd = root.querySelector('#btn-orch-method-add');
  if (methodAdd) methodAdd.addEventListener('click', async () => {
    const payload = readMethodForm();
    const missing = [!payload.id && 'ID', !payload.description && '説明', !payload.role && 'ロール',
      !payload.text && '指示'].filter(Boolean);
    if (missing.length) return setMethodFormStatus(`${missing.join('・')}を入力してください。`, true);
    methodAdd.disabled = true;
    setMethodFormStatus('追加しています…');
    try {
      await api.orchestrationMethodAdd(payload);
      if (methodDialog) methodDialog.close();
      toast('カスタム手法を追加しました', true);
      await refreshOrchestration();
      renderOrchestration();
    } catch (err) {
      setMethodFormStatus(`追加できませんでした: ${err.message || err}`, true);
    } finally {
      methodAdd.disabled = false;
    }
  });

  const methodAi = root.querySelector('#btn-orch-method-ai');
  if (methodAi) methodAi.addEventListener('click', async () => {
    const brief = String((root.querySelector('#orch-method-brief') || {}).value || '').trim();
    const status = root.querySelector('#orch-method-ai-status');
    if (!brief) {
      if (status) status.textContent = '作りたい手法を入力してください。';
      return;
    }
    methodAi.disabled = true;
    if (status) status.textContent = '補完しています…';
    try {
      const result = await api.agentMethodDraft({ dir: state.selectedDir, brief, current: readMethodForm() });
      const method = result.method || {};
      const fill = (id, value) => {
        const field = root.querySelector(id);
        if (field && !String(field.value || '').trim() && value) field.value = value;
      };
      fill('#orch-method-id', method.id);
      fill('#orch-method-description', method.description);
      fill('#orch-method-role', method.role);
      fill('#orch-method-text', method.text);
      for (const [key, id] of Object.entries({
        tiers: '#orch-method-tiers', purposes: '#orch-method-purposes', workloads: '#orch-method-workloads',
        agent_cli: '#orch-method-agent-cli', models: '#orch-method-models',
      })) {
        const values = ((method.when || {})[key] || [])
          .map((value) => (key === 'tiers' ? (ORCH_TIER_LABELS[value] || value) : value));
        fill(id, values.join(', '));
      }
      if (status) status.textContent = `補完しました${result.cli ? `（${result.cli}${result.model ? ` / ${result.model}` : ''}）` : ''}`;
    } catch (err) {
      if (status) {
        status.textContent = `補完できませんでした: ${err.message || err}`;
        status.setAttribute('role', 'alert');
      }
    } finally {
      methodAi.disabled = false;
    }
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
      const timeout = block.querySelector('.orch-ctrl-timeout');
      if (timeout) wc.timeout_sec = timeoutSeconds(timeout.value);
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
        const atimeout = arow.querySelector('.orch-agent-timeout');
        if (atimeout) agents[key].timeout_sec = timeoutSeconds(atimeout.value);
      }
      const addRow = block.querySelector('.orch-agent-add');
      if (addRow) {
        const nk = addRow.querySelector('.orch-agent-new-key').value.trim();
        if (nk) {
          agents[nk] = {
            agent_cli: addRow.querySelector('.orch-agent-new-cli').value.trim() || null,
            model: addRow.querySelector('.orch-agent-new-model').value.trim() || null,
          };
          const ntimeout = addRow.querySelector('.orch-agent-new-timeout');
          if (ntimeout) agents[nk].timeout_sec = timeoutSeconds(ntimeout.value);
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

  // 同時実行数（control の concurrency）の保存。空欄は「指定なし」＝キーを消して
  // 各プロジェクトの設定へ戻す（null が消去の合図・main 側の契約）。
  const concSave = root.querySelector('#btn-orch-conc-save');
  if (concSave) concSave.addEventListener('click', () => guard('同時実行数の保存', async () => {
    const read = (id) => {
      const el = root.querySelector(id);
      const raw = el ? el.value.trim() : '';
      return raw === '' ? null : Number(raw);
    };
    const concurrency = { max_runs: read('#orch-conc-max-runs'), workers: read('#orch-conc-workers') };
    state.orchSaving = true;
    try {
      await api.orchestrationControlSave({ workloads: { flow: { concurrency } } });
      toast('同時実行数を保存しました', true);
    } finally { state.orchSaving = false; }
    await refreshOrchestration();
    renderOrchestration();
  }));

  // 固定 3 段の候補設定。保存時も既存の内部キーを保ち、policy の参照を壊さない。
  const tierList = root.querySelector('#orch-profile-tiers');
  const markWorkflowDirty = () => { state.orchWorkflowDirty = true; };
  if (tierList) {
    tierList.addEventListener('input', markWorkflowDirty);
    tierList.addEventListener('change', markWorkflowDirty);
    tierList.addEventListener('click', (event) => {
      const add = event.target.closest('.orch-tier-candidate-add');
      if (add) {
        markWorkflowDirty();
        add.closest('.orch-tier-candidates').querySelector('.orch-tier-candidate-list')
          .insertAdjacentHTML('beforeend', orchTierCandidateRowHtml());
        return;
      }
      const removeCandidate = event.target.closest('.orch-tier-candidate-remove');
      if (removeCandidate) {
        markWorkflowDirty();
        const list = removeCandidate.closest('.orch-tier-candidate-list');
        removeCandidate.closest('.orch-tier-candidate').remove();
        if (!list.querySelector('.orch-tier-candidate')) list.innerHTML = orchTierCandidateRowHtml();
      }
    });
  }

  const tiersSave = root.querySelector('#btn-orch-tiers-save');
  if (tiersSave) tiersSave.addEventListener('click', () => guard('段の設定の保存', async () => {
    const rows = [...root.querySelectorAll('.orch-profile-tier')];
    const tiers = {};
    rows.forEach((row, i) => {
      const key = row.dataset.orchTierKey;
      const candidates = [...row.querySelectorAll('.orch-tier-candidate')].map((candidate) => ({
        agent_cli: candidate.querySelector('.orch-tier-candidate-cli').value.trim(),
        model: candidate.querySelector('.orch-tier-candidate-model').value.trim(),
      })).filter((candidate) => candidate.agent_cli || candidate.model);
      tiers[key] = { order: i + 1, label: ORCH_TIER_LABELS[key], candidates };
    });
    const currentPolicy = (((state.orchestration || {}).profiles || {}).policy) || {};
    const keys = new Set(Object.keys(tiers));
    const policy = {
      steps: (currentPolicy.steps || []).filter((step) => keys.has(String(step.tier || ''))),
      no_cap_tier: keys.has(currentPolicy.no_cap_tier) ? currentPolicy.no_cap_tier : '',
    };
    state.orchSaving = true;
    try {
      await api.orchestrationProfilesSave({ tiers, policy });
      state.orchWorkflowDirty = false;
      toast('段の設定を保存しました', true);
    } finally { state.orchSaving = false; }
    await refreshOrchestration();
    renderOrchestration();
  }));

  const profileSave = root.querySelector('#btn-orch-profile-policy-save');
  if (profileSave) profileSave.addEventListener('click', () => guard('自動切り替え条件の保存', async () => {
    const steps = [...root.querySelectorAll('.orch-profile-condition')].flatMap((row) => {
      const pct = row.querySelector('.orch-tier-ratio').value.trim();
      return pct === '' ? [] : [{
        min_remaining_ratio: Math.min(100, Math.max(0, Number(pct) || 0)) / 100,
        tier: row.dataset.orchTierKey,
      }];
    });
    const applyTo = [...root.querySelectorAll('.orch-profile-apply-to-cb')]
      .filter((cb) => cb.checked).map((cb) => cb.value);
    const noCap = (root.querySelector('#orch-profile-no-cap-tier') || {}).value || '';
    const hysteresisPct = Number((root.querySelector('#orch-profile-hysteresis') || {}).value);
    const minHoldMin = Number((root.querySelector('#orch-profile-min-hold') || {}).value);
    state.orchSaving = true;
    try {
      await api.orchestrationProfilesSave({
        enabled: (root.querySelector('#orch-profile-enabled') || {}).checked !== false,
        policy: {
          apply_to: applyTo,
          steps,
          no_cap_tier: noCap,
          hysteresis: Number.isFinite(hysteresisPct) ? Math.min(100, Math.max(0, hysteresisPct)) / 100 : 0.05,
          min_hold_sec: Number.isFinite(minHoldMin) ? Math.max(0, Math.round(minHoldMin * 60)) : 900,
        },
      });
      toast('自動切り替え条件を保存しました', true);
    } finally { state.orchSaving = false; }
    state.orchProfilesPreview = null;
    await refreshOrchestration();
    renderOrchestration();
  }));

  const profileEvaluate = root.querySelector('#btn-orch-profile-evaluate');
  if (profileEvaluate) profileEvaluate.addEventListener('click', () => guard('自動切り替えの試算', async () => {
    state.orchProfilesPreview = await api.orchestrationProfilesEvaluate();
    renderOrchestration();
  }));

  const profileApply = root.querySelector('#btn-orch-profile-apply');
  if (profileApply) profileApply.addEventListener('click', () => guard('自動切り替えの反映', async () => {
    const result = await api.orchestrationProfilesApply();
    toast(result && (result.controlWritten || result.stateWritten) ? '自動切り替えを反映しました' : '変更はありませんでした', true);
    state.orchProfilesPreview = null;
    await refreshOrchestration();
    renderOrchestration();
  }));

  // グローバル指示（agent-instructions）の保存
  const skillInput = root.querySelector('#orch-skill-add');
  const skillAdd = root.querySelector('#btn-orch-skill-add');
  const skillList = root.querySelector('#orch-skill-selected');
  const skillDescription = root.querySelector('#orch-skill-candidate-description');
  const skillInventory = new Map(
    (Array.isArray(state.orchSkillsInventory) ? state.orchSkillsInventory : []).map((s) => [String(s.name), s])
  );
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
      try { spec = JSON.parse(ta.value); } catch (e) { throw new Error(`JSON として読めません: ${e.message}`, { cause: e }); }
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
    catch (e) { throw new Error(`JSON として読めません: ${e.message}`, { cause: e }); }
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

// cowork のリポジトリパス比較キー。WSL UNC（\\wsl.localhost\<distro>\...）と POSIX、
// /mnt/<drive> と Windows ドライブ表記を同一視する。
// **main 側 _pathKey とは別物**（あちらは W2-4 で /mnt 折り畳みを廃止した）。cowork は
// Windows ドライブ上のリポジトリを wsl.exe 経由で回すので、この機能では /mnt 表記と
// ドライブ表記が同じ実体を指す——ここを main 側に合わせると、同じリポジトリの作業が
// 別プロジェクト扱いになって一覧から消える。
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
  return entries.filter(({ item }) => coworkPathKey(coworkItemFolder(item)) === key);
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
    openRoutineAgentTerminal({
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

// 作業タブと実行の記録タブは**同じ選択 UI**を使う（見た目も操作も揃える）。押したときに
// 何をするかだけが違うので、それだけを引数で受ける。
function bindCoworkRoutineSelector(root, onSelect = selectCoworkRoutine) {
  const buttons = [...root.querySelectorAll('[data-cowork-select]')];
  const search = root.querySelector('[data-cowork-search]');
  if (search) search.addEventListener('input', () => applyCoworkRoutineFilter(root));
  applyCoworkRoutineFilter(root);
  buttons.forEach((button) => {
    button.addEventListener('click', () => onSelect(button.dataset.coworkSelect));
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
      onSelect(next.dataset.coworkSelect);
      next.focus();
    });
  });
}

function coworkHasProjectConfig(cowork, projectFolder) {
  const key = coworkPathKey(projectFolder);
  return !!key && (
    ((cowork && cowork.discoveredRepos) || []).some((repo) => coworkPathKey(repo) === key)
    || ((cowork && cowork.items) || []).some((item) => coworkPathKey(coworkItemFolder(item)) === key)
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
      return `<div class="cowork-run-banner ok" role="status">「${esc(r.name || r.id)}」を別ウィンドウ（ターミナル / tmux）で開始しました — 開いたウィンドウで進行を確認できます</div>`;
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
  const parameterError = String(item.parameterError || '');
  const running = !!st.running || busyId === id;
  const status = running ? 'running' : (st.status || 'unknown');
  const run = state.coworkRun && String(state.coworkRun.id) === id ? state.coworkRun : null;
  const typeDetail = workTypeLabel(item.type);
  const prompt = String(item.prompt || item.instruction || '').trim();
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
      <button class="cowork-primary-run" data-cowork-run="${esc(id)}" data-cowork-type="${esc(item.type || 'loop')}" data-cowork-name="${esc(item.name || id)}"
        ${busyId || disabledWork || parameterError ? 'disabled' : ''}${parameterError ? ` title="${esc(parameterError)}"` : ''}>${busyId === id ? '実行中…' : '今すぐ実行'}</button>
    </div>
    <section class="cowork-prompt-preview">
      <h3>プロンプト</h3>
      <pre>${esc(prompt || '未設定')}</pre>
    </section>
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
    ${parameterError ? `<p class="cowork-item-error" role="alert">入力パラメータを確認できません: ${esc(parameterError)}</p>` : ''}
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
        ${(item.type !== 'state-machine' || pairedLoop) && item.repo && api.routineAgentListSessions
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

function coworkRunError(res) {
  return String((res && (res.stderr || res.stdout || res.error)) || '').trim()
    || (res && res.logFile ? `実行ログ: ${res.logFile}` : (res && res.ok ? '' : 'エラー詳細なし'));
}

async function executeCoworkRoutine({ id, type, name, routine, parameters, tier }) {
  state.coworkRun = { id, name, phase: 'running', message: '', detail: '', at: Date.now() };
  renderCowork();
  let res;
  try {
    res = type === 'state-machine'
      ? await api.coworkRunStateMachine(id, parameters, tier)
      : await api.coworkRunLoop(id, parameters, tier);
  } catch (err) {
    res = { ok: false, error: err.message || String(err) };
  }
  // 別ウィンドウ起動は stdout/stderr を持たない。ウィンドウが一瞬で閉じたときに
  // 「どこまで進んだか」を読めるよう、最新結果に実行ログの場所を残す。
  const detail = coworkRunError(res);
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
  if (routine) state.routineAgentStateCache.delete(coworkPathKey(routine.repo || routine.cwd));
  toast(
    res && res.ok
      ? (res.launched
        ? `「${name}」を別ウィンドウ（ターミナル / tmux）で開始しました`
        : `「${name}」を実行しました`)
      : `「${name}」を実行できませんでした: ${message}`,
    !!(res && res.ok)
  );
  await refreshCowork({ probe: true });
  renderCowork();
  return res;
}

function openCoworkParametersDialog(routine, run) {
  const keys = Array.isArray(routine && routine.parameters) ? routine.parameters : [];
  const tiers = Array.isArray(state.cowork && state.cowork.routineTiers) ? state.cowork.routineTiers : [];
  const dlg = $('dlg-cowork-parameters');
  const form = $('cowork-parameters-form');
  const fields = $('cowork-parameter-fields');
  const tierSelect = $('cowork-routine-tier');
  const error = $('cowork-parameters-error');
  const submit = $('btn-cowork-parameters-run');
  const cancel = $('btn-cowork-parameters-cancel');
  $('cowork-parameters-description').textContent = `「${routine.name || routine.id}」の実行条件を選んでください。`;
  tierSelect.innerHTML = tiers.length
    ? tiers.map((tier) => `<option value="${esc(tier.id)}">${esc(tier.label || tier.id)} — ${esc(tier.agent_cli || '既定')} / ${esc(tier.model || '既定')}</option>`).join('')
    : '<option value="">現在の全体設定</option>';
  const currentTier = String((state.cowork && state.cowork.currentRoutineTier) || '');
  if (tiers.some((tier) => tier.id === currentTier)) tierSelect.value = currentTier;
  fields.innerHTML = keys.map((key, index) => `<div class="field">
    <label for="cowork-parameter-${index}">${esc(key)}</label>
    <input id="cowork-parameter-${index}" data-cowork-parameter="${esc(key)}" type="text" autocomplete="off" required>
  </div>`).join('');
  const inputs = [...fields.querySelectorAll('[data-cowork-parameter]')];
  let running = false;
  const validate = () => {
    submit.disabled = running || inputs.some((input) => !input.value.trim());
    error.hidden = true;
    error.textContent = '';
  };
  const close = () => {
    dlg.removeEventListener('cancel', onCancel);
    form.onsubmit = null;
    cancel.onclick = null;
    if (dlg.open) dlg.close();
  };
  const onCancel = (event) => {
    event.preventDefault();
    if (!running) close();
  };
  inputs.forEach((input) => input.addEventListener('input', validate));
  cancel.onclick = close;
  dlg.addEventListener('cancel', onCancel);
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    running = true;
    submit.textContent = '実行中…';
    cancel.disabled = true;
    validate();
    const parameters = Object.fromEntries(inputs.map((input) => [input.dataset.coworkParameter, input.value.trim()]));
    const tier = tierSelect.value;
    let res;
    try {
      res = await run(parameters, tier);
    } catch (err) {
      res = { ok: false, error: err.message || String(err) };
    }
    if (res && res.ok) return close();
    running = false;
    submit.textContent = '実行';
    cancel.disabled = false;
    submit.disabled = inputs.some((input) => !input.value.trim());
    error.textContent = coworkRunError(res);
    error.hidden = false;
  };
  submit.textContent = '実行';
  cancel.disabled = false;
  validate();
  dlg.showModal();
  tierSelect.focus();
  return undefined;
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
    await openCoworkParametersDialog(routine, (parameters, tier) =>
      executeCoworkRoutine({ id, type, name, routine, parameters, tier }));
  }));
  root.querySelectorAll('[data-cowork-term-repo]').forEach((btn) => btn.addEventListener('click', () => {
    openRoutineAgentTerminal({
      id: btn.dataset.coworkTermId || '',
      repo: btn.dataset.coworkTermRepo,
      name: btn.dataset.coworkTermName || '',
    });
  }));
}

// 定常業務フォルダの登録。agent-project 管理外のフォルダ（agent-loop 設定や
// .statemachine/ を持つだけ）をこの画面で扱えるようにする。宣言は dashboard 設定
// `cowork.roots` が持つ——定常業務の実行側はこの dashboard 自身だから（「宣言は実行側が
// 持つ」の原則。agent-project の host.yaml に載せると、常駐体が管理しないものを常駐体の
// 宣言ファイルに書くねじれになる）。
//
// **登録の入口は定常業務領域の「設定」タブだけ**（btn-settings-cowork-add-root）。作業タブは
// 選択中フォルダの作業を見る画面なので、別フォルダの登録ボタンを置かない。選択中フォルダ
// 自身の「登録を解除」だけをバッジとして残す。
function coworkRootBadgeHtml(folder) {
  const p = (state.discovery && state.discovery.projects) || [];
  const sel = p.find((x) => x && x.dir === folder);
  if (!sel || sel.kind !== 'routine') return '';
  return '<span class="cowork-root-badge">この画面に登録したフォルダ'
    + '<button id="btn-cowork-drop-root" type="button" class="linklike">登録を解除</button></span>';
}

async function addCoworkRoot() {
  const picked = await guard('フォルダを登録できません', () => api.coworkPickRoot());
  if (!picked || picked.canceled) return;
  if (picked.registered) return toast('このフォルダは既に登録されています');
  if (picked.managedByEngine) {
    return toast('このフォルダは実行エンジンが担当しているので、登録しなくても表示されます');
  }
  // マーカーが無い＝まだ定常業務が無いフォルダ。登録は許す（登録しないと選択できず、
  // 「追加」で最初の 1 件を作ることもできない）が、次に何をすればよいかを明示する。
  const summary = picked.markers
    .map((m) => [m.loop, ...(m.stateMachines || [])].filter(Boolean).join(' / '))
    .filter(Boolean)
    .join('、');
  const body = summary
    ? `見つかった定常業務: ${summary}`
    : '定常業務の設定（agent-loop / .statemachine）はまだありません。'
      + '\n登録後、このフォルダを選んで「追加」から作成できます。';
  if (!confirm(`${picked.folder}\n\n${body}\n\nこのフォルダを登録しますか？`)) return;
  const res = await guard('フォルダを登録できません', () => api.coworkSetRoot(picked.folder, false));
  if (!res) return;
  toast(summary ? 'フォルダを登録しました' : 'フォルダを登録しました（「追加」から作業を作成できます）', true);
  await afterCoworkRootChange(res);
}

async function dropCoworkRoot(folder) {
  if (!folder) return;
  if (!confirm(`${folder}\n\nこのフォルダの登録を解除しますか？（フォルダの中身は消えません）`)) return;
  const res = await guard('登録を解除できません', () => api.coworkSetRoot(folder, true));
  if (!res) return;
  toast('登録を解除しました', true);
  await afterCoworkRootChange(res);
}

// 登録・解除のあと片付け。**編集中の下書きを捨てる**のが要点——下書きは前回の overview を
// 種に固定されるので、捨てないと新しく登録したフォルダの定常業務が一覧に出てこない。
async function afterCoworkRootChange(res) {
  if (res && Array.isArray(res.roots) && state.config) {
    state.config.cowork = { ...(state.config.cowork || {}), roots: res.roots };
  }
  await refreshDiscovery();
  await refreshCowork({ forceDiscover: true });
  state.coworkDraft = null;
  renderCowork();
  if ($('tab-orchestration') && state.orchestration) renderOrchestration();
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
  const scopeLabel = `このフォルダの作業 ${entries.length} 件`;
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
        ${coworkRootBadgeHtml(folder)}
      </div>
      ${coworkRunBannerHtml()}
      ${entries.length ? `<div class="cowork-split-view">
        <section class="cowork-list-pane" aria-label="定常業務の一覧">${coworkRoutineSelectorHtml(entries, selectedId)}</section>
        <div id="cowork-selected-slot" class="cowork-detail-pane" data-ui-scroll-key>${coworkSelectedDetailHtml(selected, observed, busyId)}</div>
      </div>`
      : '<div class="empty"><strong>このフォルダに登録された定常業務はありません</strong><span>「追加」から作業を作成できます。別のフォルダを扱うには「設定」タブの「フォルダを登録」から追加してください。</span></div>'}
    </div>`;
  bindCoworkRoutineSelector(el);
  const addBtn = $('btn-cowork-add');
  if (addBtn) addBtn.addEventListener('click', () => openCoworkWorkDialog(-1));
  const dropRootBtn = $('btn-cowork-drop-root');
  if (dropRootBtn) dropRootBtn.addEventListener('click', () => dropCoworkRoot(folder));
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
