'use strict';

// 監査（agent-audit の呼び出しと閲覧）— 全体設定タブの「利用状況」へ差し込む面。
//
// 独立したタブではなく全体設定へ置くのは、扱う数字が**プロジェクトごとではない**ため。
// この端末の実行証跡から集計した実測トークンと実行品質は、選択中プロジェクトと無関係
// なのにプロジェクトのタブ列へ並んでいた。全体設定にはノード予算から集計した「利用状況」
// が既にあり、同じ話題の数字が 2 か所へ分かれてもいた。同じ節に並べて 1 か所にする。
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

  let period = 'month';
  let by = 'agent_cli';
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
  let lastAutoAt = 0;
  let autoBusy = false;

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

  function fmtUsd(n) {
    const v = Number(n) || 0;
    return v > 0 ? `$${v.toFixed(2)}` : '—';
  }

  function fmtWhen(iso) {
    if (!iso) return '';
    const t = new Date(iso);
    return Number.isNaN(t.getTime()) ? String(iso) : t.toLocaleString();
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

  function usageTableHtml(data) {
    const rows = (data && data.rows) || [];
    if (!rows.length) {
      return '<p class="muted">まだ記録がありません。「今すぐ収集」で実行証跡とセッションログを取り込んでください。</p>';
    }
    const body = rows.map((row) => `<tr>
      <td>${escHtml(row.group)}</td>
      <td class="num">${escHtml(String(row.runs || 0))}</td>
      <td class="num">${escHtml(fmtTokens(row.measured_in))}</td>
      <td class="num">${escHtml(fmtTokens(row.measured_out))}</td>
      <td class="num">${escHtml(fmtTokens(row.estimated_tokens))}</td>
      <td class="num">${escHtml(fmtSeconds(row.seconds))}</td>
      <td class="num">${escHtml(String(row.unmeasured_runs || 0))}</td>
      <td class="num">${escHtml(fmtUsd(row.usd))}</td>
    </tr>`).join('');
    return `<table class="list audit-table">
      <thead><tr>
        <th>グループ</th><th>実行数</th><th>実測トークン 入力</th><th>実測トークン 出力</th>
        <th>推定トークン</th><th>実行時間</th><th>実測なし</th><th>概算費用</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table>
    <p class="muted">実測はエージェント CLI が報告した値、推定は実行時間からの換算です。性質が違うため合算していません。</p>`;
  }

  function statsTableHtml(data) {
    const tools = (data && data.tools) || [];
    if (!tools.length) {
      return '<p class="muted">実行品質の記録はまだありません。ツールの実行証跡が収集されると表示されます。</p>';
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
        <div>
          <span class="summary-kicker">実行証跡の収集</span>
          <h3>収集の設定</h3>
        </div>
      </header>
      <div class="field">
        <label for="audit-set-command">起動コマンド</label>
        <input id="audit-set-command" type="text" value="${escHtml(v.command || '')}"
          placeholder="空なら PATH の agent-audit を使います">
        <p class="field-help">インストールせずに使うときはインタープリタごと指定します。例: python3 ~/repo/tools/agent-audit/agent-audit.py</p>
      </div>
      <div class="field">
        <label for="audit-set-distro">WSL ディストロ</label>
        <input id="audit-set-distro" type="text" value="${escHtml(v.distro || '')}"
          placeholder="空なら既定のディストロで実行します">
        <p class="field-help">Windows でだけ使われます。agent-audit が入っているディストロ名を指定してください。</p>
      </div>
      <div class="field">
        <label for="audit-set-config">agent-audit の設定ファイル</label>
        <input id="audit-set-config" type="text" value="${escHtml(v.configPath || '')}"
          placeholder="例: ~/.agents/agent-audit.yaml">
        <p class="field-help">読み取る源泉の一覧などは agent-audit の設定ファイルが正です。空なら agent-audit 自身の探索順で見つけます。</p>
      </div>
      <div class="field">
        <label for="audit-set-dir">収集データの保存先</label>
        <input id="audit-set-dir" type="text" value="${escHtml(v.auditDir || '')}"
          placeholder="空なら ~/.agents/audit に保存します">
      </div>
      <div class="field">
        <label for="audit-set-interval">定期収集の間隔（分）</label>
        <input id="audit-set-interval" type="number" min="0" step="1" value="${escHtml(String(v.collectIntervalMin || 0))}">
        <p class="field-help">0 で無効。設定すると、このアプリを開いている間、その間隔で増分収集を実行します。</p>
      </div>
      ${settingsMessage ? `<p class="${settingsMessage.ok ? 'muted' : 'audit-error'}" role="status">${escHtml(settingsMessage.text)}</p>` : ''}
      <div class="settings-save-actions">
        <button type="button" id="audit-save" class="primary-inline">保存</button>
      </div>
    </section>`;
  }

  // 全体設定「利用状況」へ並べる面。既にある利用量（ノード予算の集計）と同じ
  // orch-panel の見た目に揃える——同じ節に別デザインの塊が挟まると、別画面が
  // 埋め込まれているように見えて 1 か所にまとめた意味が薄れる。
  function panelHtml() {
    const optionsHtml = (list, current) => list
      .map(([value, label]) => `<option value="${value}"${value === current ? ' selected' : ''}>${label}</option>`)
      .join('');
    return `<section class="orch-panel audit-usage" aria-labelledby="audit-usage-title">
      <header class="row">
        <div>
          <span class="summary-kicker">実行証跡から集計</span>
          <h3 id="audit-usage-title">実測のトークン利用量</h3>
        </div>
        <div class="audit-actions">
          <button type="button" id="audit-collect" class="primary-inline"${collectBusy ? ' disabled' : ''}>${collectBusy ? '収集しています…' : '今すぐ収集'}</button>
          <button type="button" id="audit-reload"${loading ? ' disabled' : ''}>表示を更新</button>
          <button type="button" id="audit-doctor"${doctorBusy ? ' disabled' : ''}>設定を点検</button>
        </div>
      </header>
      <p class="muted">エージェント CLI のセッションログと実行証跡を突き合わせた実績です。上の利用量が実行記録からの集計なのに対し、こちらは CLI が報告した実測値を含みます。ここから実行するのは収集と集計だけで、知見の蒸留は agent-audit 側の設定で動きます。</p>
      ${collectStatusHtml(collectInfo, collectBusy)}
      ${doctorInfo ? `<details class="audit-detail" open><summary>点検結果${doctorInfo.ok ? '' : '（問題があります）'}</summary><pre>${escHtml(doctorInfo.detail || doctorInfo.error || '')}</pre></details>` : ''}
      <div class="audit-controls">
        <label>期間 <select id="audit-period">${optionsHtml(PERIODS, period)}</select></label>
        <label>集計 <select id="audit-by">${optionsHtml(GROUPS, by)}</select></label>
      </div>
      ${loadError ? `<p class="audit-error" role="alert">${escHtml(loadError)}</p>` : ''}
      ${loading ? '<p class="muted" role="status">集計しています…</p>' : ''}
      ${usageTableHtml(usageData)}
    </section>
    <section class="orch-panel audit-stats">
      <header class="row">
        <div>
          <span class="summary-kicker">実行証跡から集計</span>
          <h3>実行品質</h3>
        </div>
      </header>
      ${statsTableHtml(statsData)}
    </section>
    ${settingsHtml(settingsDraft || auditConfig())}`;
  }

  async function loadData() {
    if (!root.api || !root.api.agentAuditUsage || loading) return;
    loading = true;
    loadError = '';
    render();
    try {
      const [usage, stats] = await Promise.all([
        root.api.agentAuditUsage({ period, by }),
        root.api.agentAuditStats({ period }),
      ]);
      usageData = usage;
      statsData = stats;
      loadedOnce = true;
      if (!collectInfo && usage && usage.lastCollect) collectInfo = usage.lastCollect;
    } catch (error) {
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

  // 面が実際に見えているか（全体設定タブが前面 かつ 「利用状況」の節が開いている）。
  // 見えていないあいだは CLI を起こさない——タブを開いていない端末で毎周期
  // agent-audit を起動することになる。
  function visible(container) {
    if (!container || !root.document) return false;
    const pane = container.closest ? container.closest('.global-settings-pane') : null;
    if (pane && pane.hidden) return false;
    const tab = root.document.getElementById('tab-orchestration');
    return Boolean(tab && tab.classList.contains('active'));
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
    for (const input of pane.querySelectorAll('.audit-settings input')) {
      input.addEventListener('input', () => {
        settingsDraft = readSettingsForm(pane);
        settingsMessage = null;
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
  function render() {
    if (!root.document) return;
    const container = root.document.getElementById('global-settings-slot-agent-audit');
    if (!container) return;
    container.innerHTML = panelHtml();
    wire(container);
    reveal(container);
  }

  // 節が表示されたとき（描画直後・節の切り替え）に呼ばれる。初回の集計取得はここで起こす
  // ——利用状況を開いていない間は agent-audit を起動しない。
  function reveal(container) {
    if (!visible(container)) return;
    if (!loadedOnce && !loading && !loadError) loadData();
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

  return {
    escHtml, fmtTokens, fmtSeconds, fmtUsd, pairsText,
    usageTableHtml, statsTableHtml, settingsHtml, collectStatusHtml, panelHtml,
    render, refresh, wire, reveal,
  };
});
