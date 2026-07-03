'use strict';

/* global api */

const $ = (id) => document.getElementById(id);

const state = {
  config: null,
  discovery: { containers: [], instances: [] },
  selectedDir: null, // 選択中プロジェクトのディレクトリ
  project: null, // readProject のスナップショット
  flowRuns: [],
  flowRunId: null,
  flowRun: null, // {run, events}
  flowNodeId: null,
  backlogFilter: 'active',
  gitlab: { enabled: false, byUrl: {}, repoIssues: [], loading: false },
  timer: null,
  busy: false,
};

// ---------------------------------------------------------------------------
// ユーティリティ
// ---------------------------------------------------------------------------

function esc(s) {
  return String(s ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function toast(msg, ok = false) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.toggle('ok', ok);
  el.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add('hidden'), ok ? 3000 : 8000);
}

async function guard(label, fn) {
  try {
    return await fn();
  } catch (err) {
    toast(`${label}: ${err.message || err}`);
    return undefined;
  }
}

function fmtTime(v) {
  if (!v) return '';
  const d = typeof v === 'number' ? new Date(v * 1000) : new Date(v);
  if (isNaN(d.getTime())) return String(v);
  return d.toLocaleString('ja-JP', { hour12: false });
}

function fmtAgo(v) {
  const t = typeof v === 'number' ? v * 1000 : Date.parse(v);
  if (!t || isNaN(t)) return '';
  const sec = Math.max(0, (Date.now() - t) / 1000);
  if (sec < 60) return `${Math.floor(sec)}秒前`;
  if (sec < 3600) return `${Math.floor(sec / 60)}分前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}時間前`;
  return `${Math.floor(sec / 86400)}日前`;
}

// 最小限の Markdown 描画（見出し・箇条書き・コード・リンクをエスケープ済みで）
function mdToHtml(src) {
  const lines = String(src || '').split('\n');
  const out = [];
  let inCode = false;
  let inList = false;
  const closeList = () => {
    if (inList) {
      out.push('</ul>');
      inList = false;
    }
  };
  const inline = (s) =>
    esc(s)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(https?:\/\/[^\s)&"<>]+)/g, '<a href="#" data-ext="$1">$1</a>');
  for (const line of lines) {
    if (line.trim().startsWith('```')) {
      closeList();
      out.push(inCode ? '</pre>' : '<pre class="mono">');
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      out.push(esc(line));
      continue;
    }
    const h = line.match(/^(#{1,3})\s+(.*)$/);
    if (h) {
      closeList();
      const lv = h[1].length;
      out.push(`<h${lv}>${inline(h[2])}</h${lv}>`);
      continue;
    }
    const li = line.match(/^\s*-\s+(.*)$/);
    if (li) {
      if (!inList) {
        out.push('<ul>');
        inList = true;
      }
      out.push(`<li>${inline(li[1])}</li>`);
      continue;
    }
    closeList();
    if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  if (inCode) out.push('</pre>');
  return `<div class="md">${out.join('\n')}</div>`;
}

function statusChip(status) {
  return `<span class="status-chip st-${esc(status)}">${esc(status)}</span>`;
}

// git URL ("git@host:group/proj.git" / "https://host/group/proj.git") →
// {host, projectPath}
function parseRepoUrl(url) {
  const s = String(url || '').trim();
  let m = s.match(/^git@([^:]+):(.+?)(?:\.git)?$/);
  if (m) return { host: m[1], projectPath: m[2] };
  m = s.match(/^https?:\/\/([^/]+)\/(.+?)(?:\.git)?\/?$/);
  if (m) return { host: m[1], projectPath: m[2] };
  return null;
}

// クリック委譲: data-ext 属性のリンクは既定ブラウザで開く
document.addEventListener('click', (ev) => {
  const a = ev.target.closest('a[data-ext]');
  if (a) {
    ev.preventDefault();
    guard('外部リンク', () => api.openExternal(a.dataset.ext));
  }
});

// ---------------------------------------------------------------------------
// 発見・プロジェクト選択
// ---------------------------------------------------------------------------

async function refreshDiscovery() {
  state.discovery = await api.discover();
  renderTree();
}

function renderTree() {
  const tree = $('tree');
  const { containers, instances } = state.discovery;
  if (!containers.length) {
    tree.innerHTML =
      '<div class="empty">コンテナが見つかりません。<br>⚙ 設定で .kiro-projects のパスを追加するか、<br>kiro-projects を稼働させてください。</div>';
  } else {
    tree.innerHTML = containers
      .map((c) => {
        const projects = c.projects
          .map((p) => {
            const badges = [];
            if (p.needsCount) badges.push(`<span class="badge warn" title="要対応">${p.needsCount}</span>`);
            if (p.backlogCount) badges.push(`<span class="badge" title="バックログ">${p.backlogCount}</span>`);
            if (p.hasCharter) badges.push('<span class="badge info" title="charter あり">C</span>');
            return `<div class="project-item ${state.selectedDir === p.dir ? 'selected' : ''}" data-dir="${esc(p.dir)}">
              <span class="dot ${p.running ? 'running' : ''}" title="${p.running ? '稼働中' : '停止中'}"></span>
              <span class="name">${esc(p.name)}</span>${badges.join('')}
            </div>`;
          })
          .join('');
        return `<div class="container-item">
          <div class="container-label" title="${esc(c.root)}">${esc(c.root)}${c.exists ? '' : '（見つかりません）'}</div>
          ${projects || '<div class="project-item muted"><span class="name">プロジェクトなし</span></div>'}
        </div>`;
      })
      .join('');
  }
  const live = instances.filter((i) => i.fresh && !i.sentinel).length;
  $('sidebar-footer').textContent = `稼働インスタンス: ${live} ／ 最終更新 ${new Date().toLocaleTimeString('ja-JP')}`;

  for (const el of tree.querySelectorAll('.project-item[data-dir]')) {
    el.addEventListener('click', () => selectProject(el.dataset.dir));
  }
}

async function selectProject(dir) {
  state.selectedDir = dir;
  localStorage.setItem('kpv:selected', dir);
  renderTree();
  await reloadProject();
}

async function reloadProject() {
  if (!state.selectedDir) return;
  const project = await guard('プロジェクト読込', () => api.readProject(state.selectedDir));
  if (!project) return;
  state.project = project;
  if (project.hasBus) {
    state.flowRuns = (await guard('フロー読込', () => api.flowRuns(project.busDir))) || [];
    if (state.flowRunId && !state.flowRuns.some((r) => r.runId === state.flowRunId)) {
      state.flowRunId = null;
      state.flowRun = null;
    }
    if (state.flowRunId) {
      state.flowRun = await guard('run 読込', () => api.flowRun(project.busDir, state.flowRunId));
    }
  } else {
    state.flowRuns = [];
    state.flowRunId = null;
    state.flowRun = null;
  }
  renderHeader();
  renderAllTabs();
}

function renderHeader() {
  const p = state.project;
  if (!p) return;
  const charterName = p.charter && p.charter.name ? ` — ${p.charter.name}` : '';
  $('project-name').textContent = `${p.name}${charterName}`;
  $('project-name').classList.remove('muted');
  const ps = p.projectState;
  const badges = [];
  if (ps && ps.status) badges.push(statusChip(ps.status));
  $('project-badges').innerHTML = badges.join(' ');
  const lastLog = p.runLog.length ? p.runLog[p.runLog.length - 1] : null;
  const metaBits = [`${esc(p.dir)}`];
  if (lastLog) metaBits.push(`最終 run: ${esc(lastLog.reason || '')} (${fmtAgo(lastLog.ts)})`);
  $('project-meta').innerHTML = metaBits.join(' ｜ ');
  const needsBadge = $('needs-badge');
  const undecided = p.needs.filter((n) => !n.decided).length;
  needsBadge.textContent = undecided;
  needsBadge.classList.toggle('hidden', !undecided);
  needsBadge.classList.toggle('warn', undecided > 0);
}

// ---------------------------------------------------------------------------
// タブ: 概要
// ---------------------------------------------------------------------------

const STATUS_ORDER = ['ready', 'doing', 'review', 'blocked', 'inbox', 'draft'];

function renderOverview() {
  const p = state.project;
  const el = $('tab-overview');
  if (!p) {
    el.innerHTML = '<div class="empty">左のツリーからプロジェクトを選択してください</div>';
    return;
  }
  const parts = [];

  // ステータスタイル
  const tiles = STATUS_ORDER.map(
    (st) =>
      `<div class="tile st-${st}"><div class="num">${p.byStatus[st] || 0}</div><div class="label">${st}</div></div>`
  );
  tiles.push(
    `<div class="tile st-done"><div class="num">${p.archive.length}</div><div class="label">done（累計）</div></div>`
  );
  parts.push(`<div class="card full"><h3>バックログ</h3><div class="tiles">${tiles.join('')}</div></div>`);

  // charter
  if (p.charter) {
    const ps = p.projectState || {};
    const total = Number(ps.acceptance_total || (p.charter.acceptanceItems || []).length || 0);
    const hist = Array.isArray(ps.history)
      ? ps.history
          .map((h) =>
            typeof h === 'number' ? h : h && typeof h === 'object' ? Number(h.pass ?? h.passed ?? h.ok ?? NaN) : NaN
          )
          .filter((n) => !isNaN(n))
      : [];
    const best = Number(ps.best ?? (hist.length ? Math.max(...hist) : 0));
    const pct = total ? Math.round((best / total) * 100) : 0;
    const spark = hist.length
      ? `<div class="sparkline">${hist
          .slice(-24)
          .map((n) => `<div style="height:${total ? Math.max(6, (n / total) * 100) : 6}%" title="${n}/${total}"></div>`)
          .join('')}</div>`
      : '';
    parts.push(`
      <div class="cards">
        <div class="card" style="flex:2">
          <h3>CHARTER: ${esc(p.charter.name || '')}</h3>
          <div class="charter-goal">${esc(p.charter.goal || '(goal なし)')}</div>
          ${p.charter.deliverables ? `<div class="section-title">deliverables</div>${mdToHtml(p.charter.deliverables)}` : ''}
          ${p.charter.constraints ? `<div class="section-title">constraints</div>${mdToHtml(p.charter.constraints)}` : ''}
        </div>
        <div class="card">
          <h3>ACCEPTANCE（プロジェクト done の根拠）</h3>
          <div class="big">${best} / ${total} PASS</div>
          <div class="progress ${total && best >= total ? 'ok' : ''}" title="${pct}%"><div style="width:${pct}%"></div></div>
          ${spark}
          <div class="muted" style="margin-top:6px">
            状態: ${esc(ps.status || '-')} ／ サイクル: ${esc(String(ps.cycles ?? '-'))} ／ 累計コスト: ${esc(String(ps.cost ?? '-'))}
          </div>
          <div style="margin-top:8px">${(p.charter.acceptanceItems || [])
            .map((a) => `<div class="acceptance-item">・<code class="mono">${esc(a)}</code></div>`)
            .join('')}</div>
        </div>
      </div>`);
  } else {
    parts.push(
      `<div class="card full"><h3>CHARTER</h3><div class="muted">charter.md はありません（バックログ消化モード）</div></div>`
    );
  }

  // 実行中・run-log サマリ
  const doing = p.claims.length
    ? p.claims.map((id) => `<code class="mono">${esc(id)}</code>`).join(' ')
    : '<span class="muted">なし</span>';
  const last = p.runLog.slice(-5).reverse();
  const runRows = last
    .map(
      (r) => `<tr>
        <td>${fmtTime(r.ts)}</td><td>${esc(r.reason || '')}</td><td>${r.done ?? 0}</td>
        <td>${r.blocked ?? 0}</td><td>${r.review ?? 0}</td>
        <td>${r.tokens ?? 0}</td><td>${r.cost ?? 0}</td><td>${Math.round(r.duration_s ?? 0)}s</td>
      </tr>`
    )
    .join('');
  parts.push(`
    <div class="cards">
      <div class="card">
        <h3>実行中クレーム</h3>${doing}
        <h3 style="margin-top:12px">ポリシー</h3>
        ${
          p.policy.length
            ? p.policy
                .map((r) => `<div><span class="label-chip">${esc(r.kind)}</span> <code class="mono">${esc(r.value)}</code></div>`)
                .join('')
            : '<span class="muted">policy.md なし</span>'
        }
      </div>
      <div class="card" style="flex:2">
        <h3>直近の run（run-log.jsonl）</h3>
        ${
          runRows
            ? `<table class="list"><tr><th>時刻</th><th>停止理由</th><th>done</th><th>blocked</th><th>review</th><th>tokens</th><th>cost</th><th>時間</th></tr>${runRows}</table>`
            : '<span class="muted">run 履歴なし</span>'
        }
      </div>
    </div>`);

  // 納品（DELIVERY.md）
  if (p.delivery.length) {
    const rows = p.delivery
      .slice(-10)
      .reverse()
      .map(
        (cells) =>
          `<tr>${cells.map((c) => `<td>${linkify(c)}</td>`).join('')}</tr>`
      )
      .join('');
    parts.push(
      `<div class="card full"><h3>納品（DELIVERY.md 直近 10 件）</h3><table class="list">${rows}</table></div>`
    );
  }

  el.innerHTML = parts.join('\n');
}

function linkify(text) {
  return esc(text).replace(/(https?:\/\/[^\s)&"<>]+)/g, '<a href="#" data-ext="$1">$1</a>');
}

// ---------------------------------------------------------------------------
// タブ: バックログ
// ---------------------------------------------------------------------------

const BACKLOG_FILTERS = [
  ['active', '進行中'],
  ['ready', 'ready'],
  ['doing', 'doing'],
  ['review', 'review'],
  ['blocked', 'blocked'],
  ['inbox', 'inbox'],
  ['draft', 'draft'],
  ['archive', 'done（archive）'],
];

function renderBacklog() {
  const p = state.project;
  const el = $('tab-backlog');
  if (!p) {
    el.innerHTML = '';
    return;
  }
  const chips = BACKLOG_FILTERS.map(
    ([key, label]) =>
      `<button class="chip ${state.backlogFilter === key ? 'active' : ''}" data-filter="${key}">${label}</button>`
  ).join('');

  let tasks;
  if (state.backlogFilter === 'archive') tasks = p.archive;
  else if (state.backlogFilter === 'active') tasks = p.backlog;
  else tasks = p.backlog.filter((t) => t.status === state.backlogFilter);

  // priority 降順 → 古い順（planner none と同じ感覚）
  tasks = [...tasks].sort((a, b) => b.priority - a.priority || a.mtime - b.mtime);

  const rows = tasks
    .map((t) => {
      const extras = [];
      if (t.extra.after) extras.push(`after: ${t.extra.after}`);
      if (t.extra.level) extras.push(`level: ${t.extra.level}`);
      if (t.extra.track) extras.push(`track: ${t.extra.track}`);
      if (t.extra.review) extras.push(`review: ${t.extra.review}`);
      return `<tr class="clickable" data-task="${esc(t.id)}" data-scope="${state.backlogFilter === 'archive' ? 'archive' : 'backlog'}">
        <td class="mono">${esc(t.id)}</td>
        <td>${esc(t.title)}</td>
        <td>${statusChip(t.status)}${p.claims.includes(t.id) ? ' <span class="badge info" title="実行中">▶</span>' : ''}</td>
        <td>${t.priority}</td>
        <td>${t.retries}</td>
        <td>${t.verify ? '✓' : t.extra.accept || t.extra.verify_template ? '△' : '—'}</td>
        <td class="muted">${esc(extras.join(' ／ '))}</td>
      </tr>`;
    })
    .join('');

  el.innerHTML = `
    <div class="filters">${chips}<span class="muted">${tasks.length} 件</span></div>
    ${
      rows
        ? `<table class="list"><tr><th>ID</th><th>タイトル</th><th>状態</th><th>優先度</th><th>retry</th><th>verify</th><th>属性</th></tr>${rows}</table>`
        : '<div class="empty">タスクなし</div>'
    }`;

  for (const chip of el.querySelectorAll('.chip')) {
    chip.addEventListener('click', () => {
      state.backlogFilter = chip.dataset.filter;
      renderBacklog();
    });
  }
  for (const row of el.querySelectorAll('tr[data-task]')) {
    row.addEventListener('click', () => showTaskDialog(row.dataset.task, row.dataset.scope));
  }
}

function showTaskDialog(id, scope) {
  const p = state.project;
  const list = scope === 'archive' ? p.archive : p.backlog;
  const t = list.find((x) => x.id === id);
  if (!t) return;
  const extraRows = Object.entries(t.extra)
    .map(([k, v]) => `<tr><th>${esc(k)}</th><td><pre class="mono">${esc(v)}</pre></td></tr>`)
    .join('');
  $('dlg-task-body').innerHTML = `
    <h2><span class="mono">${esc(t.id)}</span>: ${esc(t.title)}</h2>
    <table class="list">
      <tr><th>状態</th><td>${statusChip(t.status)}</td></tr>
      <tr><th>出自</th><td>${esc(t.source)}</td></tr>
      <tr><th>優先度</th><td>${t.priority}</td></tr>
      <tr><th>retries</th><td>${t.retries}</td></tr>
      <tr><th>verify</th><td>${t.verify ? `<pre class="mono">${esc(t.verify)}</pre>` : '<span class="muted">（未定義）</span>'}</td></tr>
      ${extraRows}
      <tr><th>ファイル</th><td><a href="#" id="task-open-file" class="mono">${esc(t.file)}</a></td></tr>
    </table>`;
  const link = $('task-open-file');
  if (link) link.addEventListener('click', (e) => {
    e.preventDefault();
    guard('ファイルを開く', () => api.openPath(t.file));
  });
  $('dlg-task').showModal();
}

// ---------------------------------------------------------------------------
// タブ: 要対応（needs）
// ---------------------------------------------------------------------------

function renderNeeds() {
  const p = state.project;
  const el = $('tab-needs');
  if (!p) {
    el.innerHTML = '';
    return;
  }
  if (!p.needs.length) {
    el.innerHTML = '<div class="empty">人の判断待ちはありません 🎉</div>';
    return;
  }
  const cards = [...p.needs]
    .sort((a, b) => Number(a.decided) - Number(b.decided) || b.mtime - a.mtime)
    .map(
      (n) => `<div class="need-card kind-${esc(n.kind || 'blocked')}">
        <div class="need-head">
          <span class="badge ${n.decided ? '' : 'warn'}">${esc(n.kind || 'blocked')}</span>
          <span class="title">${esc(n.title || n.id)}</span>
          <span class="muted">${esc(n.date || '')}</span>
          ${n.decided ? '<span class="status-chip st-done">記入済み</span>' : '<span class="status-chip st-blocked">未対応</span>'}
          <button data-open="${esc(n.file)}">ファイルを開いて回答</button>
        </div>
        <div class="body">${mdToHtml(n.body)}</div>
      </div>`
    )
    .join('');
  el.innerHTML = `<div class="muted" style="margin-bottom:8px">
      needs/&lt;id&gt;.md の「## Decision Outcome」に記入し <code>- [x]</code> にすると kiro-projects が取り込みます。</div>${cards}`;
  for (const btn of el.querySelectorAll('button[data-open]')) {
    btn.addEventListener('click', () => guard('ファイルを開く', () => api.openPath(btn.dataset.open)));
  }
}

// ---------------------------------------------------------------------------
// タブ: フロー（kiro-flow のタスクグラフ）
// ---------------------------------------------------------------------------

const FLOW_STATE_LABEL = {
  done: '完了',
  failed: '失敗',
  claimed: '実行中',
  pending: '待機（実行可能）',
  waiting: '依存待ち',
};

function renderFlow() {
  const p = state.project;
  const el = $('tab-flow');
  if (!p) {
    el.innerHTML = '';
    return;
  }
  if (!p.hasBus && !state.flowRuns.length) {
    el.innerHTML =
      '<div class="empty">kiro-flow の run がありません。<br>（bus/ は run 完了後に掃除されるため、稼働中または --no-cleanup 時に表示されます）</div>';
    return;
  }
  const runList = state.flowRuns
    .map((r) => {
      const pct = Math.round(r.progress * 100);
      return `<div class="run-item ${state.flowRunId === r.runId ? 'selected' : ''}" data-run="${esc(r.runId)}">
        <div class="row2"><span class="mono">${esc(r.runId)}</span><span>${statusChip(r.status)}</span></div>
        <div class="req">${esc((r.request || '').slice(0, 120))}</div>
        <div class="progress"><div style="width:${pct}%"></div></div>
        <div class="muted">${r.counts.done}✓ ${r.counts.failed}✗ ${r.counts.claimed}▶ ／ ${r.total} ノード ｜ ${fmtAgo(r.updatedAt || r.createdAt)}</div>
      </div>`;
    })
    .join('');

  el.innerHTML = `<div id="flow-layout">
    <div id="flow-runs">${runList || '<div class="empty">run なし</div>'}</div>
    <div id="flow-detail">${renderFlowDetail()}</div>
  </div>`;

  for (const item of el.querySelectorAll('.run-item[data-run]')) {
    item.addEventListener('click', () => selectFlowRun(item.dataset.run));
  }
  bindFlowDetail(el);
}

async function selectFlowRun(runId) {
  state.flowRunId = runId;
  state.flowNodeId = null;
  state.flowRun = await guard('run 読込', () => api.flowRun(state.project.busDir, runId));
  renderFlow();
}

function renderFlowDetail() {
  const fr = state.flowRun;
  if (!fr || !fr.run) return '<div class="empty">run を選択するとタスクグラフを表示します</div>';
  const run = fr.run;
  const strat = run.strategy
    ? `${(run.strategy.patterns || []).join(' + ')} ／ 並列 ${run.strategy.parallelism ?? '-'} ／ iteration ${run.iteration}`
    : '';
  const pct = Math.round(run.progress * 100);
  const legend = Object.entries(FLOW_STATE_LABEL)
    .map(
      ([st, label]) =>
        `<span class="key"><span class="sw state-sw-${st}" style="background:${swColor(st)}"></span>${label}</span>`
    )
    .join('');
  const node = state.flowNodeId ? run.nodes[state.flowNodeId] : null;
  const nodeDetail = node
    ? `<div class="card full">
        <h3><span class="mono">${esc(node.id)}</span> [${esc(node.kind)}] — ${esc(FLOW_STATE_LABEL[node.state] || node.state)}${node.who ? ` @${esc(node.who)}` : ''}</h3>
        <div>${esc(node.goal)}</div>
        ${node.deps.length ? `<div class="muted" style="margin-top:4px">依存: ${node.deps.map(esc).join(', ')}</div>` : ''}
        ${node.output ? `<div class="section-title">output</div><pre class="mono">${esc(node.output.slice(0, 3000))}</pre>` : ''}
        ${node.data ? `<div class="section-title">data</div><pre class="mono">${esc(JSON.stringify(node.data, null, 2).slice(0, 2000))}</pre>` : ''}
      </div>`
    : '';
  const events = (fr.events || [])
    .map(
      (ev) =>
        `<div>${fmtTime(ev.ts)} <strong>${esc(ev.who || '')}</strong> ${esc(ev.kind || '')} ${esc(
          summarizeEvent(ev)
        )}</div>`
    )
    .join('');
  return `
    <div class="card full">
      <h3>RUN <span class="mono">${esc(run.runId)}</span> — ${statusChip(run.status)} ${esc(strat)}</h3>
      <div>${esc(run.request || '')}</div>
      ${run.failureReason ? `<div style="color:var(--red)">失敗理由: ${esc(run.failureReason)}</div>` : ''}
      <div class="row2" style="align-items:center;margin-top:6px">
        <div class="progress" style="flex:1"><div style="width:${pct}%"></div></div>
        <span class="muted">${run.counts.done + run.counts.failed}/${run.total} (${pct}%)</span>
      </div>
    </div>
    <div id="graph-box">${renderGraphSvg(run)}</div>
    <div class="legend">${legend}</div>
    ${nodeDetail}
    <div class="section-title">アクティビティ</div>
    <div class="events">${events || '<span class="muted">イベントなし</span>'}</div>`;
}

function summarizeEvent(ev) {
  const skip = new Set(['ts', 'who', 'kind']);
  const rest = Object.entries(ev)
    .filter(([k]) => !skip.has(k))
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
    .join(' ');
  return rest.slice(0, 160);
}

function swColor(st) {
  return { done: '#3fb950', failed: '#f85149', claimed: '#4cc2b0', pending: '#58a6ff', waiting: '#3a4048' }[st] || '#3a4048';
}

// トポロジカル深さでノードを列に並べ、SVG で DAG を描く
function renderGraphSvg(run) {
  const nodes = Object.values(run.nodes);
  if (!nodes.length) return '<div class="empty">ノードなし</div>';
  const depthMemo = {};
  const visiting = new Set();
  const depth = (id) => {
    if (depthMemo[id] !== undefined) return depthMemo[id];
    if (visiting.has(id)) return 0; // 循環はサニタイズ済みのはずだが防御
    visiting.add(id);
    const n = run.nodes[id];
    const d = n && n.deps.length ? 1 + Math.max(...n.deps.map((x) => (run.nodes[x] ? depth(x) : 0))) : 0;
    visiting.delete(id);
    depthMemo[id] = d;
    return d;
  };
  const cols = new Map();
  for (const n of nodes) {
    const d = depth(n.id);
    if (!cols.has(d)) cols.set(d, []);
    cols.get(d).push(n);
  }
  const NW = 168;
  const NH = 46;
  const GX = 70;
  const GY = 18;
  const PAD = 16;
  const pos = {};
  let maxRows = 0;
  const sortedCols = [...cols.keys()].sort((a, b) => a - b);
  for (const d of sortedCols) {
    const list = cols.get(d);
    list.sort((a, b) => a.id.localeCompare(b.id));
    list.forEach((n, i) => {
      pos[n.id] = { x: PAD + d * (NW + GX), y: PAD + i * (NH + GY) };
    });
    maxRows = Math.max(maxRows, list.length);
  }
  const width = PAD * 2 + sortedCols.length * NW + (sortedCols.length - 1) * GX;
  const height = PAD * 2 + maxRows * NH + (maxRows - 1) * GY;

  const edges = [];
  for (const n of nodes) {
    for (const d of n.deps) {
      const from = pos[d];
      const to = pos[n.id];
      if (!from || !to) continue;
      const x1 = from.x + NW;
      const y1 = from.y + NH / 2;
      const x2 = to.x;
      const y2 = to.y + NH / 2;
      const mx = (x1 + x2) / 2;
      edges.push(`<path class="edge" d="M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}" />`);
    }
  }
  const boxes = nodes.map((n) => {
    const { x, y } = pos[n.id];
    const idLabel = n.id.length > 20 ? `${n.id.slice(0, 19)}…` : n.id;
    const goal = n.goal.length > 24 ? `${n.goal.slice(0, 23)}…` : n.goal;
    return `<g class="node state-${n.state} ${state.flowNodeId === n.id ? 'selected' : ''}" data-node="${esc(n.id)}" transform="translate(${x},${y})">
      <rect width="${NW}" height="${NH}" rx="6"></rect>
      <text x="8" y="17" class="mono">${esc(idLabel)}${n.who ? ` @${esc(n.who).slice(0, 8)}` : ''}</text>
      <text x="8" y="31">${esc(goal)}</text>
      <text x="8" y="42" class="kind">[${esc(n.kind)}]</text>
    </g>`;
  });
  return `<svg class="graph" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">${edges.join('')}${boxes.join('')}</svg>`;
}

function bindFlowDetail(root) {
  for (const g of root.querySelectorAll('g.node[data-node]')) {
    g.addEventListener('click', () => {
      state.flowNodeId = g.dataset.node;
      renderFlow();
    });
  }
}

// ---------------------------------------------------------------------------
// タブ: GitLab
// ---------------------------------------------------------------------------

function collectBusIssues() {
  // 全 run の results から gitlab executor の成果（issue）を集める
  const byUrl = new Map();
  for (const r of state.flowRuns) {
    for (const gi of r.gitlabIssues || []) {
      if (gi.url && !byUrl.has(gi.url)) byUrl.set(gi.url, { ...gi, runId: r.runId });
    }
  }
  return [...byUrl.values()];
}

function charterGitlabRepos() {
  const p = state.project;
  const out = [];
  if (p && p.repos && typeof p.repos === 'object') {
    for (const [name, spec] of Object.entries(p.repos)) {
      if (name === '_meta' || !spec || typeof spec !== 'object') continue;
      const parsed = parseRepoUrl(spec.url);
      if (parsed) out.push({ name, ...parsed, url: spec.url });
    }
  }
  return out;
}

function renderGitLab() {
  const p = state.project;
  const el = $('tab-gitlab');
  if (!p) {
    el.innerHTML = '';
    return;
  }
  const busIssues = collectBusIssues();
  const repos = charterGitlabRepos();
  const gl = state.gitlab;

  const issueRow = (it, extra = '') => {
    const enriched = gl.byUrl[it.url];
    const labels = (enriched ? enriched.labels : it.labels) || [];
    const stateStr = enriched ? enriched.state : it.decision || it.state || '';
    const mrs = enriched && enriched.relatedMrs ? enriched.relatedMrs : [];
    return `<tr>
      <td class="mono">${it.issueIid || it.iid ? `#${it.issueIid || it.iid}` : ''}</td>
      <td>${enriched && enriched.title ? esc(enriched.title) : linkify(it.url)}${extra}</td>
      <td>${stateStr ? statusChip(stateStr) : ''}</td>
      <td>${labels.map((l) => `<span class="label-chip">${esc(l)}</span>`).join('')}</td>
      <td>${mrs
        .map((mr) => `<span class="status-chip st-${esc(mr.state)}" title="${esc(mr.title)}">!${mr.iid} ${esc(mr.state)}</span>`)
        .join(' ')}</td>
      <td class="row">
        <button data-review="${esc(it.url)}" title="gitlab-review-viewer でレビュー">レビューで開く</button>
        <button data-ext-btn="${esc(it.url)}" title="ブラウザで開く">↗</button>
      </td>
    </tr>`;
  };

  const busSection = busIssues.length
    ? `<table class="list"><tr><th>IID</th><th>イシュー</th><th>状態</th><th>ラベル</th><th>関連 MR</th><th></th></tr>
        ${busIssues.map((it) => issueRow(it, ` <span class="muted">(run ${esc(it.runId)} / ${esc(it.nodeId)})</span>`)).join('')}</table>`
    : '<div class="muted">bus 上に gitlab executor の起票イシューはありません</div>';

  const repoIssuesSection = gl.repoIssues.length
    ? `<table class="list"><tr><th>IID</th><th>イシュー</th><th>状態</th><th>ラベル</th><th>関連 MR</th><th></th></tr>
        ${gl.repoIssues.map((it) => issueRow(it)).join('')}</table>`
    : `<div class="muted">${gl.enabled === false ? '⚙ 設定で GitLab の URL とトークンを設定すると、charter の repos からイシュー一覧を取得できます' : 'イシューなし'}</div>`;

  el.innerHTML = `
    <div class="toolbar">
      <span class="muted">タスクに紐づく GitLab イシュー（gitlab executor 委譲分と charter repos のイシュー）</span>
      <span class="spacer"></span>
      <button id="btn-gl-refresh" ${gl.loading ? 'disabled' : ''}>${gl.loading ? '取得中…' : 'GitLab から最新化'}</button>
    </div>
    <div class="section-title">kiro-flow が委譲したイシュー（bus の結果から）</div>
    ${busSection}
    <div class="section-title">charter repos のオープンイシュー ${repos.map((r) => `<span class="label-chip">${esc(r.projectPath)}</span>`).join('')}</div>
    ${repoIssuesSection}`;

  $('btn-gl-refresh').addEventListener('click', () => refreshGitLab(true));
  for (const btn of el.querySelectorAll('button[data-review]')) {
    btn.addEventListener('click', () =>
      guard('レビュー起動', async () => {
        const res = await api.openReview({ url: btn.dataset.review });
        toast(`gitlab-review-viewer を起動しました（${res.via}）`, true);
      })
    );
  }
  for (const btn of el.querySelectorAll('button[data-ext-btn]')) {
    btn.addEventListener('click', () => guard('外部リンク', () => api.openExternal(btn.dataset.extBtn)));
  }
}

async function refreshGitLab(force) {
  const gl = state.gitlab;
  if (gl.loading) return;
  const busIssues = collectBusIssues();
  const repos = charterGitlabRepos();
  if (!force && !busIssues.length && !repos.length) return;
  gl.loading = true;
  renderGitLab();
  try {
    const urls = busIssues.map((i) => i.url).filter(Boolean);
    if (urls.length) {
      const res = await api.glEnrich(urls);
      gl.enabled = res.enabled;
      for (const issue of res.issues || []) {
        if (issue && issue.url && !issue.error) gl.byUrl[issue.url] = issue;
      }
    }
    const seen = new Set();
    const repoIssues = [];
    for (const repo of repos) {
      if (seen.has(repo.projectPath)) continue;
      seen.add(repo.projectPath);
      const res = await api.glProjectIssues({ projectPath: repo.projectPath, state: 'opened' });
      gl.enabled = res.enabled;
      if (!res.enabled) break;
      repoIssues.push(...(res.issues || []));
    }
    gl.repoIssues = repoIssues;
  } catch (err) {
    toast(`GitLab 取得: ${err.message}`);
  } finally {
    gl.loading = false;
    renderGitLab();
  }
}

// ---------------------------------------------------------------------------
// タブ: 履歴
// ---------------------------------------------------------------------------

function renderHistory() {
  const p = state.project;
  const el = $('tab-history');
  if (!p) {
    el.innerHTML = '';
    return;
  }
  const runRows = [...p.runLog]
    .reverse()
    .map(
      (r) => `<tr>
      <td>${fmtTime(r.ts)}</td><td>${esc(r.reason || '')}</td><td>${esc(r.level || '')}</td>
      <td>${r.cycles ?? ''}</td><td>${r.done ?? ''}</td><td>${r.blocked ?? ''}</td><td>${r.review ?? ''}</td>
      <td>${r.escalations ?? ''}</td><td>${r.tokens ?? ''}</td><td>${r.cost ?? ''}</td><td>${Math.round(r.duration_s ?? 0)}s</td>
    </tr>`
    )
    .join('');
  const drRows = p.decisions
    .map(
      (d) => `<tr>
      <td class="mono">${esc(d.dr)}</td><td>${esc(d.date)}</td><td class="mono">${esc(d.taskId)}</td>
      <td>${esc(d.fields.action || '')}</td><td>${esc(d.fields.reason || d.fields.context || '')}</td>
      <td>${d.learn ? `<code>${esc(d.learn)}</code>` : ''}</td>
    </tr>`
    )
    .join('');
  const journal = p.journal
    .slice(-80)
    .reverse()
    .map((l) => `<div>${linkify(l.replace(/^-\s*/, ''))}</div>`)
    .join('');
  const deliveryRows = [...p.delivery]
    .reverse()
    .map((cells) => `<tr>${cells.map((c) => `<td>${linkify(c)}</td>`).join('')}</tr>`)
    .join('');

  el.innerHTML = `
    <div class="section-title">run-log.jsonl</div>
    ${
      runRows
        ? `<table class="list"><tr><th>時刻</th><th>停止理由</th><th>level</th><th>cycles</th><th>done</th><th>blocked</th><th>review</th><th>escalation</th><th>tokens</th><th>cost</th><th>時間</th></tr>${runRows}</table>`
        : '<div class="muted">なし</div>'
    }
    <div class="section-title">決定記録（decisions/）</div>
    ${
      drRows
        ? `<table class="list"><tr><th>DR</th><th>日付</th><th>タスク</th><th>action</th><th>理由</th><th>learn</th></tr>${drRows}</table>`
        : '<div class="muted">なし</div>'
    }
    <div class="section-title">納品（DELIVERY.md）</div>
    ${deliveryRows ? `<table class="list">${deliveryRows}</table>` : '<div class="muted">なし</div>'}
    <div class="section-title">ジャーナル（journal.md 直近 80 行）</div>
    <div class="events">${journal || '<span class="muted">なし</span>'}</div>`;
}

// ---------------------------------------------------------------------------
// タブ制御・設定・ポーリング
// ---------------------------------------------------------------------------

function renderAllTabs() {
  renderOverview();
  renderBacklog();
  renderNeeds();
  renderFlow();
  renderGitLab();
  renderHistory();
}

function activeTab() {
  const el = document.querySelector('.tab.active');
  return el ? el.dataset.tab : 'overview';
}

function initTabs() {
  for (const tab of document.querySelectorAll('.tab')) {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
      document.querySelectorAll('.tabpane').forEach((p) => p.classList.remove('active'));
      tab.classList.add('active');
      $(`tab-${tab.dataset.tab}`).classList.add('active');
      if (tab.dataset.tab === 'gitlab') refreshGitLab(false);
    });
  }
}

function openSettings() {
  const cfg = state.config;
  $('cfg-roots').value = ((cfg.kiro && cfg.kiro.roots) || []).join('\n');
  $('cfg-autodiscover').checked = !cfg.kiro || cfg.kiro.autoDiscover !== false;
  $('cfg-refresh').value = cfg.kiro ? cfg.kiro.refreshSec : 5;
  $('cfg-gl-url').value = cfg.gitlab.baseUrl || '';
  $('cfg-gl-token').value = cfg.gitlab.token || '';
  $('cfg-rv-mode').value = cfg.reviewViewer.mode || 'protocol';
  $('cfg-rv-command').value = cfg.reviewViewer.command || '';
  $('dlg-settings').showModal();
}

async function saveSettings() {
  const cfg = state.config;
  cfg.kiro = cfg.kiro || {};
  cfg.kiro.roots = $('cfg-roots')
    .value.split('\n')
    .map((s) => s.trim())
    .filter(Boolean);
  cfg.kiro.autoDiscover = $('cfg-autodiscover').checked;
  cfg.kiro.refreshSec = Math.max(0, parseInt($('cfg-refresh').value, 10) || 0);
  cfg.gitlab.baseUrl = $('cfg-gl-url').value.trim();
  cfg.gitlab.token = $('cfg-gl-token').value.trim();
  cfg.reviewViewer.mode = $('cfg-rv-mode').value;
  cfg.reviewViewer.command = $('cfg-rv-command').value.trim();
  state.config = await api.saveConfig(cfg);
  setupPolling();
  await refreshAll();
  toast('設定を保存しました', true);
}

async function refreshAll() {
  if (state.busy) return;
  state.busy = true;
  try {
    await refreshDiscovery();
    if (state.selectedDir) await reloadProject();
  } finally {
    state.busy = false;
  }
}

function setupPolling() {
  clearInterval(state.timer);
  const sec = state.config && state.config.kiro ? Number(state.config.kiro.refreshSec) : 5;
  if (sec > 0) {
    state.timer = setInterval(() => {
      // ダイアログを開いている間は更新しない（入力を消さない）
      if ($('dlg-settings').open || $('dlg-task').open) return;
      refreshAll();
    }, sec * 1000);
  }
}

// ディープリンク: kiro-projects-viewer://open?root=<container>&project=<name>
function handleOpenTarget({ url }) {
  guard('ディープリンク', async () => {
    const u = new URL(url);
    const root = u.searchParams.get('root');
    const name = u.searchParams.get('project');
    await refreshDiscovery();
    for (const c of state.discovery.containers) {
      if (root && c.root !== root) continue;
      const p = c.projects.find((x) => x.name === name) || (!name && c.projects[0]);
      if (p) {
        await selectProject(p.dir);
        return;
      }
    }
    toast(`プロジェクトが見つかりません: ${name || ''}`);
  });
}

// ---------------------------------------------------------------------------
// 起動
// ---------------------------------------------------------------------------

async function init() {
  state.config = await guard('設定読込', () => api.getConfig());
  initTabs();
  $('btn-refresh').addEventListener('click', refreshAll);
  $('btn-settings').addEventListener('click', openSettings);
  $('btn-save-settings').addEventListener('click', () => saveSettings());
  $('btn-task-close').addEventListener('click', () => $('dlg-task').close());
  api.onOpenTarget(handleOpenTarget);

  await refreshDiscovery();
  const last = localStorage.getItem('kpv:selected');
  const all = state.discovery.containers.flatMap((c) => c.projects);
  const target = all.find((p) => p.dir === last) || all[0];
  if (target) await selectProject(target.dir);
  else renderAllTabs();
  setupPolling();
}

init();
