'use strict';

// クイック実行（adhoc-flow / S21・S22）— 「クイック実行」領域の面。
//
// プロジェクト（charter・バックログ・受入基準）を立てずに、agent-flow の単発 run を
// その場で投入・監視する。フロービルダーで保存したプリセット（ユーザー定義フロー）は
// 投入契約（submit_request の plan）へ変換され、F17 の手法（methods カタログ + 独自手法）を
// run 専用スナップショットとして添えられる。
//
// **アドホックの成果は done を名乗らない（C5）。** 受入基準も verify も無いので、終端した
// run は「終了（未検収）」と表示し、正式な仕事にしたいときだけ「タスクへ昇格」で
// agent-project の inbox 投函契約（通常の検証つき経路）へ載せ替える（S22）。
//
// ビルダーのテンプレートは編集の**種**にすぎない。パターンの正典は agent-flow の
// patterns.py（7 パターン）で、ここに形を写しても実行意味は plan（ノード列）だけが運ぶ。
(function expose(root, factory) {
  const feature = factory(root);
  if (typeof module !== 'undefined' && module.exports) module.exports = feature;
  if (typeof root.registerFeatureTab === 'function') {
    root.registerFeatureTab('quick-flow', {
      render: feature.render,
      refresh: feature.refresh,
      available: () => true,
    });
  }
})(typeof globalThis !== 'undefined' ? globalThis : window, (root) => {
  const KINDS = ['work', 'generate', 'classify', 'synthesize', 'verify',
    'filter', 'judge', 'reduce', 'split', 'map'];

  // ビルダーの種（テンプレート）。実行時の正典は agent-flow patterns.py の 7 パターン。
  const TEMPLATES = {
    'fan-out-and-synthesize': {
      label: '並列分担 → 統合',
      nodes: [
        { id: 't1', goal: '{{request}}（観点1）', kind: 'work', deps: [] },
        { id: 't2', goal: '{{request}}（観点2）', kind: 'work', deps: [] },
        { id: 't3', goal: '{{request}}（観点3）', kind: 'work', deps: [] },
        { id: 'synth', goal: '統合: {{request}}', kind: 'synthesize', deps: ['t1', 't2', 't3'] },
      ],
    },
    'adversarial-verification': {
      label: '生成 → 敵対的検証',
      nodes: [
        { id: 'gen1', goal: '{{request}}', kind: 'generate', deps: [] },
        { id: 'verify1', goal: '成果を批判的に検証', kind: 'verify', deps: ['gen1'] },
      ],
    },
    'generate-and-filter': {
      label: '候補を量産 → 絞り込み',
      nodes: [
        { id: 'g1', goal: '候補1: {{request}}', kind: 'generate', deps: [] },
        { id: 'g2', goal: '候補2: {{request}}', kind: 'generate', deps: [] },
        { id: 'g3', goal: '候補3: {{request}}', kind: 'generate', deps: [] },
        { id: 'filter', goal: '候補を基準でフィルタ', kind: 'filter', deps: ['g1', 'g2', 'g3'] },
      ],
    },
    tournament: {
      label: '複数案 → 最良を選ぶ',
      nodes: [
        { id: 'c1', goal: '案1: {{request}}', kind: 'generate', deps: [] },
        { id: 'c2', goal: '案2: {{request}}', kind: 'generate', deps: [] },
        { id: 'c3', goal: '案3: {{request}}', kind: 'generate', deps: [] },
        { id: 'judge', goal: '比較して最良案を選ぶ', kind: 'judge', deps: ['c1', 'c2', 'c3'] },
      ],
    },
    'loop-until-done': {
      label: '完了条件まで反復（評価役つき）',
      evaluate: true,
      nodes: [
        { id: 'work1', goal: '{{request}}', kind: 'work', deps: [] },
        { id: 'check1', goal: '完了条件を確認', kind: 'verify', deps: ['work1'] },
      ],
    },
    'map-reduce': {
      label: 'データ駆動 fan-out（split→map→reduce）',
      nodes: [{ id: 'split1', goal: '分解: {{request}}', kind: 'split', deps: [] }],
    },
    'classify-and-act': {
      label: '分類 → 振り分け（評価役つき）',
      evaluate: true,
      nodes: [{ id: 'classify', goal: '分類: {{request}}', kind: 'classify', deps: [] }],
    },
  };

  const st = {
    overview: null,     // adhocFlow:overview の最新値
    selectedRun: '',    // 選択中の run-id
    runDetail: null,    // adhocFlow:run の最新値
    builderOpen: false,
    preset: null,       // ビルダー編集中のプリセット（保存前の作業コピー）
    promoteOpen: false,
    busy: '',
    notice: '',
  };

  const esc = (s) => root.esc(String(s == null ? '' : s));
  const $id = (i) => document.getElementById(i);
  const api = () => root.api;

  function statusLabel(s) {
    // done を「完了」と言わない——アドホックは verify を通っていない（C5）
    const map = {
      done: '終了（未検収）', failed: '失敗', cancelled: '中止',
      running: '実行中', planning: '計画中',
    };
    return map[String(s)] || String(s || '—');
  }

  function emptyPreset() {
    return {
      id: '', name: '', description: '', evaluate: false,
      nodes: [], methods: [], agentCli: '', model: '', planner: '',
    };
  }

  async function refresh() {
    const pane = $id('tab-quick-flow');
    if (!pane || !pane.classList.contains('active')) return;
    try {
      st.overview = await api().adhocFlowOverview({ limit: 30 });
      if (st.selectedRun) {
        try {
          st.runDetail = await api().adhocFlowRun({ runId: st.selectedRun });
        } catch {
          st.runDetail = null;
        }
      }
      render();
    } catch (err) {
      st.notice = `読み込みに失敗しました: ${String((err && err.message) || err)}`;
      render();
    }
  }

  // --- ビルダー ---------------------------------------------------------------

  function nodeRowHtml(n, i, agents) {
    const agentOpts = ['', ...agents].map((a) =>
      `<option value="${esc(a)}" ${a === (n.agentCli || '') ? 'selected' : ''}>${a ? esc(a) : '（既定）'}</option>`).join('');
    const kindOpts = KINDS.map((k) =>
      `<option value="${k}" ${k === n.kind ? 'selected' : ''}>${k}</option>`).join('');
    return `<tr data-node-index="${i}">
      <td><input type="text" data-nf="id" value="${esc(n.id)}" size="8" aria-label="ノード id"></td>
      <td><select data-nf="kind" aria-label="種別">${kindOpts}</select></td>
      <td><input type="text" data-nf="goal" value="${esc(n.goal)}" aria-label="ゴール"
        placeholder="{{request}} で要求テキストを差し込み"></td>
      <td><input type="text" data-nf="deps" value="${esc((n.deps || []).join(', '))}" size="10"
        aria-label="依存（カンマ区切り）" placeholder="依存 id"></td>
      <td><select data-nf="agentCli" aria-label="エージェント CLI">${agentOpts}</select></td>
      <td><input type="text" data-nf="model" value="${esc(n.model || '')}" size="10"
        aria-label="モデル" placeholder="（既定）"></td>
      <td><button type="button" data-node-del="${i}" aria-label="ノードを削除">✕</button></td>
    </tr>`;
  }

  function builderPreviewHtml(p) {
    if (!p.nodes.length) {
      return '<div class="empty">ノードがありません。テンプレートを選ぶか「ノードを追加」してください。'
        + 'ノード無しで保存すると、実行時の計画は planner（自動）に任されます。</div>';
    }
    // 実行状態を持たない擬似 run で既存のグラフ描画を再利用する（描き手を増やさない）
    try {
      const nodes = Object.fromEntries(p.nodes.map((n) => [n.id, {
        id: n.id, goal: n.goal, deps: (n.deps || []).filter((d) => p.nodes.some((x) => x.id === d)),
        kind: n.kind || 'work', state: '',
      }]));
      return root.renderGraphSvg({ nodes, planRevisions: [] });
    } catch {
      return `<ul>${p.nodes.map((n) =>
        `<li><code>${esc(n.id)}</code> [${esc(n.kind)}] ${esc(n.goal)}${(n.deps || []).length ? ` ← ${esc(n.deps.join(', '))}` : ''}</li>`).join('')}</ul>`;
    }
  }

  function builderHtml(ov) {
    const p = st.preset || emptyPreset();
    const agents = ov.agents || [];
    const presetOpts = (ov.presets || []).map((x) =>
      `<option value="${esc(x.id)}" ${st.preset && st.preset.id === x.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('');
    const tmplOpts = Object.entries(TEMPLATES).map(([k, t]) =>
      `<option value="${k}">${esc(t.label)}（${k}）</option>`).join('');
    const methodRows = (ov.methods || []).map((m) => `
      <label class="qf-method" title="${esc(m.origin)}">
        <input type="checkbox" data-method-id="${esc(m.id)}" ${p.methods.includes(m.id) ? 'checked' : ''}>
        <code>${esc(m.id)}</code> <span class="muted">${esc(m.description)}${m.from === 'tuning' ? '（独自）' : ''}</span>
      </label>`).join('');
    const agentOpts = ['', ...agents].map((a) =>
      `<option value="${esc(a)}" ${a === p.agentCli ? 'selected' : ''}>${a ? esc(a) : '（既定）'}</option>`).join('');
    return `<details id="qf-builder" ${st.builderOpen ? 'open' : ''}>
      <summary>フロービルダー — テンプレート + 手法 + エージェント/モデルでフローを組む</summary>
      <div class="qf-builder-body">
        <div class="qf-row">
          <label>保存済み: <select id="qf-preset-load"><option value="">（新規）</option>${presetOpts}</select></label>
          <label>テンプレートを流し込む: <select id="qf-template"><option value="">選択…</option>${tmplOpts}</select></label>
        </div>
        <div class="qf-row">
          <label>名前 <input type="text" id="qf-name" value="${esc(p.name)}" placeholder="例: 二段検証つき調査"></label>
          <label>説明 <input type="text" id="qf-desc" value="${esc(p.description)}" size="30"></label>
        </div>
        <table class="qf-nodes"><thead><tr>
          <th>id</th><th>種別</th><th>ゴール</th><th>依存</th><th>エージェント</th><th>モデル</th><th></th>
        </tr></thead><tbody id="qf-nodes">${p.nodes.map((n, i) => nodeRowHtml(n, i, agents)).join('')}</tbody></table>
        <div class="qf-row">
          <button type="button" id="qf-node-add">ノードを追加</button>
          <label><input type="checkbox" id="qf-evaluate" ${p.evaluate ? 'checked' : ''}>
            評価役の再計画を許可（loop-until-done 等の反復・ルーティングに必要）</label>
        </div>
        <div id="qf-preview" class="qf-preview">${builderPreviewHtml(p)}</div>
        <h4>手法（F17）</h4>
        <p class="muted">選んだ手法はこの run 専用の複製（source 付きスナップショット）として効きます。
          選択があるとこの run では端末全体の tuning は読まれません。適用条件（when）は各手法の
          宣言どおり——役割・種別（purpose）単位で効き、ノード個別には効きません。</p>
        <div class="qf-methods">${methodRows || '<span class="muted">手法カタログが見つかりません</span>'}</div>
        <div class="qf-row">
          <label>フロー全体のエージェント <select id="qf-agent">${agentOpts}</select></label>
          <label>モデル <input type="text" id="qf-model" value="${esc(p.model)}" size="14" placeholder="（既定）"></label>
          <label>planner <select id="qf-planner">${['', 'flow-planner', 'agent', 'stub'].map((x) =>
            `<option value="${x}" ${x === p.planner ? 'selected' : ''}>${x || '（既定）'}</option>`).join('')}</select>
            <span class="muted">（ノード無しプリセットの自動計画に効く）</span></label>
        </div>
        <div class="qf-row">
          <button type="button" id="qf-preset-save">プリセットを保存</button>
          ${st.preset && st.preset.id ? '<button type="button" id="qf-preset-delete">このプリセットを削除</button>' : ''}
        </div>
      </div>
    </details>`;
  }

  // --- run 一覧・詳細 ---------------------------------------------------------

  function runsHtml(ov) {
    const runs = ov.runs || [];
    if (!runs.length) return '<div class="empty">まだ実行がありません</div>';
    return `<table class="qf-runs"><thead><tr>
      <th>run</th><th>状態</th><th>フェーズ</th><th>更新</th>
    </tr></thead><tbody>${runs.map((r) => `
      <tr class="${st.selectedRun === r.runId ? 'selected' : ''}" data-run-id="${esc(r.runId)}">
        <td><code>${esc(r.runId)}</code></td>
        <td>${esc(statusLabel(r.status))}</td>
        <td>${esc(r.phase || '—')}</td>
        <td class="muted">${esc(r.updatedAt || r.createdAt || '')}</td>
      </tr>`).join('')}</tbody></table>`;
  }

  function promoteFormHtml(ov, detail) {
    const projects = ov.projects || [];
    if (!projects.length) {
      return '<p class="muted">昇格先にできるプロジェクトがありません（実行エンジンが担当している'
        + 'プロジェクトだけが宛先になります）。</p>';
    }
    const run = detail.run || {};
    const req = String(run.request || '').split('\n')[0].slice(0, 60);
    return `<div class="qf-promote">
      <label>昇格先 <select id="qf-promote-project">${projects.map((p) =>
        `<option value="${esc(p.dir)}">${esc(p.name)}</option>`).join('')}</select></label>
      <label>タイトル <input type="text" id="qf-promote-title" value="${esc(req)}"></label>
      <label>受入基準（1 行 1 項目・空なら下書きで入る）
        <textarea id="qf-promote-acceptance" rows="3" placeholder="例: ○○のテストが通る"></textarea></label>
      <button type="button" id="qf-promote-go">この成果を根拠にタスクを起票する</button>
      <p class="muted">昇格したタスクは通常の受入基準と verify を通ります。アドホックの成果自体は
        未検収のままです。</p>
    </div>`;
  }

  function runDetailHtml(ov, detail) {
    if (!detail || !detail.run) return '<div class="empty">run を選択してください</div>';
    const run = detail.run;
    const status = String(run.status || '');
    const plan = detail.inbox && detail.inbox.plan ? detail.inbox.plan : null;
    let graph = '';
    try {
      graph = root.renderTaskFlow ? root.renderTaskFlow(run) : '';
    } catch {
      // グラフ描画の失敗は詳細表示全体の失敗にしない（結果テキストは出す）
    }
    const results = Object.values(run.nodes || {})
      .filter((n) => n.output || n.data)
      .map((n) => `<details><summary><code>${esc(n.id)}</code> [${esc(n.kind || 'work')}]
          ${esc(statusLabel(n.state))}</summary>
        <pre class="qf-output">${esc(String(n.output || JSON.stringify(n.data || '', null, 2)).slice(0, 4000))}</pre>
      </details>`).join('');
    return `<div class="qf-detail">
      <div class="qf-row">
        <strong><code>${esc(run.runId || st.selectedRun)}</code></strong>
        <span>${esc(statusLabel(status))}</span>
        ${plan ? `<span class="muted">ユーザー定義フロー「${esc(plan.name || '')}」</span>` : '<span class="muted">自動計画</span>'}
      </div>
      ${run.failureReason ? `<p class="qf-failure">${esc(run.failureReason)}</p>` : ''}
      ${run.final && run.final.summary ? `<details open><summary>結果サマリ（参考成果・未検収）</summary>
        <pre class="qf-output">${esc(String(run.final.summary).slice(0, 3000))}</pre></details>` : ''}
      <div class="qf-row">
        <button type="button" id="qf-run-cancel">キャンセル</button>
        <button type="button" id="qf-run-resubmit">同条件で再実行</button>
        <button type="button" id="qf-run-delete">記録を削除</button>
        <button type="button" id="qf-run-promote">${st.promoteOpen ? '昇格フォームを閉じる' : 'タスクへ昇格…'}</button>
      </div>
      ${st.promoteOpen ? promoteFormHtml(ov, detail) : ''}
      <div id="qf-graph" class="qf-graph">${graph}</div>
      ${results ? `<h4>ノードの出力（参考成果・未検収）</h4>${results}` : ''}
    </div>`;
  }

  // --- 描画とイベント ---------------------------------------------------------

  function render() {
    const pane = $id('tab-quick-flow');
    if (!pane) return;
    const ov = st.overview || { runs: [], presets: [], methods: [], agents: [], projects: [] };
    const presetOpts = (ov.presets || []).map((p) =>
      `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
    pane.innerHTML = `<section class="qf" aria-label="クイック実行">
      <p class="muted">プロジェクトを立てずに agent-flow を単発実行します。成果は<strong>未検収</strong>のまま
        バス（<code>${esc(ov.busDir || '')}</code>）に残り、バックログの状態には一切書きません。
        正式な仕事にするときは run 詳細から「タスクへ昇格」します。</p>
      ${st.notice ? `<p class="qf-notice">${esc(st.notice)}</p>` : ''}
      <div class="qf-submit">
        <textarea id="qf-request" rows="3" placeholder="要求（例: このリポジトリの X の仕組みを調べて 3 案比較する）"></textarea>
        <div class="qf-row">
          <label>フロー: <select id="qf-run-preset">
            <option value="">自動計画（planner に任せる）</option>${presetOpts}
          </select></label>
          <button type="button" id="qf-submit" ${st.busy ? 'disabled' : ''}>${st.busy || '実行'}</button>
        </div>
      </div>
      ${builderHtml(ov)}
      <h4>実行一覧</h4>
      <div id="qf-runs">${runsHtml(ov)}</div>
      <div id="qf-run-detail">${runDetailHtml(ov, st.runDetail)}</div>
    </section>`;
    wire(pane, ov);
  }

  function collectBuilderInputs() {
    // ビルダー入力 → 作業コピー（st.preset）へ吸い上げる（再描画で入力が消えないように）
    const p = st.preset || (st.preset = emptyPreset());
    p.name = ($id('qf-name') || {}).value || p.name;
    p.description = ($id('qf-desc') || {}).value || '';
    p.evaluate = !!($id('qf-evaluate') || {}).checked;
    p.agentCli = ($id('qf-agent') || {}).value || '';
    p.model = ($id('qf-model') || {}).value || '';
    p.planner = ($id('qf-planner') || {}).value || '';
    const rows = document.querySelectorAll('#qf-nodes tr[data-node-index]');
    const nodes = [];
    for (const row of rows) {
      const f = (name) => {
        const el = row.querySelector(`[data-nf="${name}"]`);
        return el ? el.value : '';
      };
      nodes.push({
        id: f('id').trim(),
        kind: f('kind').trim() || 'work',
        goal: f('goal').trim(),
        deps: f('deps').split(',').map((s) => s.trim()).filter(Boolean),
        agentCli: f('agentCli').trim(),
        model: f('model').trim(),
      });
    }
    p.nodes = nodes;
    p.methods = [...document.querySelectorAll('#qf-builder [data-method-id]')]
      .filter((el) => el.checked).map((el) => el.dataset.methodId);
    return p;
  }

  function wire(pane, ov) {
    const on = (id, ev, fn) => {
      const el = $id(id);
      if (el) el.addEventListener(ev, fn);
    };
    on('qf-submit', 'click', async () => {
      const request = ($id('qf-request') || {}).value || '';
      const presetId = ($id('qf-run-preset') || {}).value || '';
      const preset = (ov.presets || []).find((p) => p.id === presetId) || null;
      st.busy = '投入中…';
      st.notice = '';
      render();
      try {
        const res = await api().adhocFlowSubmit({ request, preset });
        st.selectedRun = res.runId;
        st.notice = `投入しました: ${res.runId}`;
      } catch (err) {
        st.notice = `投入に失敗しました: ${String((err && err.message) || err)}`;
      }
      st.busy = '';
      await refresh();
    });
    on('qf-builder', 'toggle', () => {
      st.builderOpen = $id('qf-builder').open;
    });
    on('qf-preset-load', 'change', () => {
      const id = $id('qf-preset-load').value;
      const found = (ov.presets || []).find((p) => p.id === id);
      st.preset = found ? JSON.parse(JSON.stringify(found)) : emptyPreset();
      st.builderOpen = true;
      render();
    });
    on('qf-template', 'change', () => {
      const t = TEMPLATES[$id('qf-template').value];
      if (!t) return;
      const p = collectBuilderInputs();
      p.nodes = JSON.parse(JSON.stringify(t.nodes));
      if (t.evaluate) p.evaluate = true;
      st.builderOpen = true;
      render();
    });
    on('qf-node-add', 'click', () => {
      const p = collectBuilderInputs();
      p.nodes.push({ id: `n${p.nodes.length + 1}`, goal: '', kind: 'work', deps: [], agentCli: '', model: '' });
      st.builderOpen = true;
      render();
    });
    for (const btn of pane.querySelectorAll('[data-node-del]')) {
      btn.addEventListener('click', () => {
        const p = collectBuilderInputs();
        p.nodes.splice(Number(btn.dataset.nodeDel), 1);
        st.builderOpen = true;
        render();
      });
    }
    on('qf-preset-save', 'click', async () => {
      const p = collectBuilderInputs();
      try {
        const res = await api().adhocFlowSavePreset({ preset: p });
        st.preset = JSON.parse(JSON.stringify(res.saved));
        st.notice = `プリセット「${res.saved.name}」を保存しました`;
      } catch (err) {
        st.notice = `保存に失敗しました: ${String((err && err.message) || err)}`;
      }
      await refresh();
    });
    on('qf-preset-delete', 'click', async () => {
      if (!st.preset || !st.preset.id) return;
      try {
        await api().adhocFlowDeletePreset({ id: st.preset.id });
        st.preset = emptyPreset();
      } catch (err) {
        st.notice = `削除に失敗しました: ${String((err && err.message) || err)}`;
      }
      await refresh();
    });
    for (const row of pane.querySelectorAll('#qf-runs tr[data-run-id]')) {
      row.addEventListener('click', async () => {
        st.selectedRun = row.dataset.runId;
        st.promoteOpen = false;
        try {
          st.runDetail = await api().adhocFlowRun({ runId: st.selectedRun });
        } catch (err) {
          st.runDetail = null;
          st.notice = String((err && err.message) || err);
        }
        render();
      });
    }
    on('qf-run-cancel', 'click', async () => {
      try {
        await api().adhocFlowCancel({ runId: st.selectedRun });
        st.notice = 'キャンセルを要求しました';
      } catch (err) {
        st.notice = String((err && err.message) || err);
      }
      await refresh();
    });
    on('qf-run-resubmit', 'click', async () => {
      try {
        const res = await api().adhocFlowResubmit({ runId: st.selectedRun });
        st.selectedRun = res.runId;
        st.notice = `再投入しました: ${res.runId}`;
      } catch (err) {
        st.notice = String((err && err.message) || err);
      }
      await refresh();
    });
    on('qf-run-delete', 'click', async () => {
      try {
        await api().adhocFlowDeleteRun({ runId: st.selectedRun });
        st.selectedRun = '';
        st.runDetail = null;
      } catch (err) {
        st.notice = String((err && err.message) || err);
      }
      await refresh();
    });
    on('qf-run-promote', 'click', () => {
      st.promoteOpen = !st.promoteOpen;
      render();
    });
    on('qf-promote-go', 'click', async () => {
      const detail = st.runDetail || {};
      const run = detail.run || {};
      const summary = String((run.final && run.final.summary) || '').slice(0, 1500);
      const acceptance = (($id('qf-promote-acceptance') || {}).value || '')
        .split('\n').map((s) => s.trim()).filter(Boolean);
      const spec = {
        title: ($id('qf-promote-title') || {}).value || '',
        why: 'アドホック実行（クイック実行）の成果を正式な仕事として引き取る',
        desc: `元 run: ${st.selectedRun}\n要求: ${String(run.request || '').slice(0, 500)}`,
        hints: `アドホック run の参考成果（未検収）: ${st.selectedRun}`
          + (summary ? ` ⏎ 結果サマリ: ${summary}` : ''),
      };
      if (acceptance.length) spec.task_acceptance_criteria = acceptance;
      try {
        await api().adhocFlowPromote({
          projectDir: ($id('qf-promote-project') || {}).value || '',
          spec,
        });
        st.notice = 'タスクとして起票しました（プロジェクトの通常の受入・verify を通ります）';
        st.promoteOpen = false;
      } catch (err) {
        st.notice = `昇格に失敗しました: ${String((err && err.message) || err)}`;
      }
      render();
    });
  }

  async function ensureLoaded() {
    if (!st.overview) await refresh();
    else render();
  }

  return {
    render: ensureLoaded,
    refresh,
    statusLabel,
    TEMPLATES,
    _state: st,
  };
});
