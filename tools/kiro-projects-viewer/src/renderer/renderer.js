'use strict';

/* global api */

const $ = (id) => document.getElementById(id);

const state = {
  config: null,
  discovery: { containers: [], instances: [] },
  selectedDir: null, // 選択中プロジェクトのディレクトリ
  project: null, // readProject のスナップショット
  flowRuns: [],
  flowDaemon: null, // {running, pid, lockPath}（ロックファイルからの判定）
  flowRunId: null,
  flowRun: null, // {run, events, nodeEvents}
  flowNodeId: null,
  flowNodeIssue: null, // {token, issue|null}（実行中ノードのイシュー検索結果キャッシュ）
  backlogFilter: 'active',
  gitlab: { enabled: false, byUrl: {}, repoIssues: [], loading: false, flowOnly: true },
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

// window.confirm は Electron でダイアログを閉じた後にキーボード入力が効かなくなる
// 既知問題があるため、<dialog> ベースの確認を使う（gitlab-review-viewer と同じ流儀）
function confirmDialog(message) {
  return new Promise((resolve) => {
    const dlg = $('dlg-confirm');
    $('confirm-desc').textContent = message;
    const finish = (ok) => {
      cleanup();
      if (dlg.open) dlg.close();
      resolve(ok);
    };
    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    const onClose = () => finish(false); // Esc キーで閉じた場合
    function cleanup() {
      $('btn-confirm-ok').removeEventListener('click', onOk);
      $('btn-confirm-cancel').removeEventListener('click', onCancel);
      dlg.removeEventListener('close', onClose);
    }
    $('btn-confirm-ok').addEventListener('click', onOk);
    $('btn-confirm-cancel').addEventListener('click', onCancel);
    dlg.addEventListener('close', onClose);
    dlg.showModal();
  });
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
  // バスが未作成でも daemon の稼働はロックファイルから判定できるため常に読む
  const fr = (await guard('フロー読込', () => api.flowRuns(project.busDir))) || {};
  state.flowRuns = fr.runs || [];
  state.flowDaemon = fr.daemon || null;
  if (state.flowRunId && !state.flowRuns.some((r) => r.runId === state.flowRunId)) {
    state.flowRunId = null;
    state.flowRun = null;
  }
  if (state.flowRunId) {
    state.flowRun = await guard('run 読込', () => api.flowRun(project.busDir, state.flowRunId));
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
    <div class="filters">${chips}<span class="muted">${tasks.length} 件</span>
      ${p.inboxFiles && p.inboxFiles.length ? `<span class="badge info" title="${esc(p.inboxFiles.join(', '))}">inbox 取り込み待ち ${p.inboxFiles.length}</span>` : ''}
      <span class="spacer"></span>
      <button id="btn-enqueue" class="primary-inline">＋ タスクを追加</button>
    </div>
    ${
      rows
        ? `<table class="list"><tr><th>ID</th><th>タイトル</th><th>状態</th><th>優先度</th><th>retry</th><th>verify</th><th>属性</th></tr>${rows}</table>`
        : '<div class="empty">タスクなし</div>'
    }`;

  $('btn-enqueue').addEventListener('click', () => {
    $('enq-title').value = '';
    $('enq-verify').value = '';
    $('enq-accept').value = '';
    $('enq-priority').value = '0';
    $('enq-note').value = '';
    $('dlg-enqueue').showModal();
  });

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
  // 決定記録を残す人の操作（backlog のタスクのみ。archive は閲覧のみ）
  const canApprove = ['blocked', 'review'].includes(t.status);
  const claimed = p.claims.includes(t.id);
  const actionArea =
    scope === 'archive'
      ? ''
      : `<div class="need-actions">
          <textarea rows="2" id="task-reason" class="need-input" placeholder="操作の理由（決定記録 decisions/ に残ります）"></textarea>
          <div class="row need-buttons">
            ${canApprove ? `<button class="primary-inline" data-taskact="approve">✓ 承認</button>` : ''}
            <button data-taskact="pin">▲ 最優先へ（pin）</button>
            <button data-taskact="defer">▽ 後回し（defer）</button>
            <button data-taskact="hold">⏸ 保留（hold）</button>
            <span class="spacer"></span>
            <button class="danger" id="btn-task-delete" ${claimed ? 'disabled' : ''}
              title="${claimed ? '実行中（クレーム中）のタスクは削除できません' : 'backlog のタスクファイルをゴミ箱へ移動します（決定記録は残りません）'}">🗑 削除</button>
          </div>
        </div>`;
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
    </table>
    ${actionArea}`;
  const link = $('task-open-file');
  if (link) link.addEventListener('click', (e) => {
    e.preventDefault();
    guard('ファイルを開く', () => api.openPath(t.file));
  });
  for (const btn of document.querySelectorAll('#dlg-task-body button[data-taskact]')) {
    btn.addEventListener('click', async () => {
      const reason = $('task-reason') ? $('task-reason').value.trim() : '';
      const ok = await guard('操作', async () => {
        const res = await api.runAction({ dir: p.dir, action: btn.dataset.taskact, id: t.id, reason });
        toast(res.output || '操作しました', true);
        return true;
      });
      if (ok) {
        gitPushAfterWrite(`kiro-projects-viewer: ${btn.dataset.taskact} ${t.id}`, p.dir);
        $('dlg-task').close();
        await reloadProject();
      }
    });
  }
  // 削除（人の明示アクション）。kiro-projects に削除の公式契約は無いため、
  // backlog/<id>.md をゴミ箱へ移動する。実行中（クレーム中）は main 側でも拒否される
  const delBtn = $('btn-task-delete');
  if (delBtn) {
    delBtn.addEventListener('click', async () => {
      const yes = await confirmDialog(
        `タスク ${t.id}「${t.title}」を削除します。\n` +
          'backlog のタスクファイルをゴミ箱へ移動します（決定記録 DR は残りません）。\n' +
          '一時的に止めたいだけなら「⏸ 保留（hold）」を使ってください。よろしいですか？'
      );
      if (!yes) return;
      const ok = await guard('タスク削除', async () => {
        const res = await api.deleteTask(p.dir, t.id);
        toast(`${t.id} を削除しました（${res.via === 'trash' ? 'ゴミ箱へ移動' : '完全削除'}）`, true);
        return true;
      });
      if (ok) {
        gitPushAfterWrite(`kiro-projects-viewer: delete task ${t.id}`, p.dir);
        $('dlg-task').close();
        await reloadProject();
      }
    });
  }
  $('dlg-task').showModal();
}

async function submitEnqueue() {
  const p = state.project;
  if (!p) return;
  const spec = {
    title: $('enq-title').value,
    verify: $('enq-verify').value,
    accept: $('enq-accept').value,
    priority: $('enq-priority').value,
    note: $('enq-note').value,
  };
  const ok = await guard('タスク追加', async () => {
    const res = await api.enqueueTask(p.dir, spec);
    toast(
      `inbox に投入しました: ${res.spec.title}\n` +
        (res.spec.verify || res.spec.accept
          ? '（次のサイクルで backlog 化されます）'
          : '（verify / accept が無いので取り込み後は人の triage 行きです）'),
      true
    );
    return true;
  });
  if (ok) {
    gitPushAfterWrite(`kiro-projects-viewer: enqueue ${spec.title || ''}`.trim(), p.dir);
    $('dlg-enqueue').close();
    await reloadProject();
  }
}

// ---------------------------------------------------------------------------
// タブ: 要対応（needs）
// ---------------------------------------------------------------------------

// needs の種類ごとに出すアクション。
//   blocked   … フィードバック再開（[x] 記入）/ そのまま再実行 / 保留（hold）
//   review    … 承認して done 確定（approve CLI）/ 差し戻し（フィードバック必須）
//   milestone … プロジェクト承認（approve <project>）
function needActionsHtml(n) {
  const kind = n.kind || 'blocked';
  const buttons = [];
  if (kind === 'review') {
    buttons.push(`<button class="primary-inline" data-act="approve" data-id="${esc(n.id)}">✓ 承認して done 確定</button>`);
    buttons.push(`<button data-act="feedback" data-id="${esc(n.id)}" data-require="1">↩ 差し戻す（記入必須）</button>`);
  } else if (kind === 'milestone') {
    buttons.push(`<button class="primary-inline" data-act="approve" data-id="${esc(n.id)}">✓ プロジェクトを承認（完了確定）</button>`);
    buttons.push(`<button data-act="feedback" data-id="${esc(n.id)}">↩ フィードバックを送る</button>`);
  } else {
    buttons.push(`<button class="primary-inline" data-act="feedback" data-id="${esc(n.id)}">➤ フィードバックして再開</button>`);
    buttons.push(`<button data-act="rerun" data-id="${esc(n.id)}">↻ そのまま再実行</button>`);
    buttons.push(`<button data-act="hold" data-id="${esc(n.id)}">⏸ 保留（hold）</button>`);
  }
  const ph =
    kind === 'review'
      ? '差し戻す場合の修正方針（承認だけなら空欄で OK。approve の理由にも使われます）'
      : '修正方針・指示（空のまま再実行も可）';
  return `<div class="need-actions" data-need="${esc(n.id)}">
    <textarea rows="2" class="need-input" placeholder="${esc(ph)}"></textarea>
    <div class="row need-buttons">${buttons.join('')}
      <span class="spacer"></span>
      <button data-open="${esc(n.file)}" title="エディタで直接編集">ファイルを開く</button>
    </div>
  </div>`;
}

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
          ${n.decided ? '<span class="status-chip st-done">記入済み（取り込み待ち）</span>' : '<span class="status-chip st-blocked">未対応</span>'}
        </div>
        <div class="body">${mdToHtml(n.body)}</div>
        ${n.decided ? '' : needActionsHtml(n)}
      </div>`
    )
    .join('');
  el.innerHTML = `<div class="muted" style="margin-bottom:8px">
      回答はこの画面から送信できます（needs/&lt;id&gt;.md の「## Decision Outcome」記入 + <code>- [x]</code> 確定と同じ。
      稼働中の kiro-projects が自動で取り込みます）。</div>${cards}`;

  for (const btn of el.querySelectorAll('button[data-open]')) {
    btn.addEventListener('click', () => guard('ファイルを開く', () => api.openPath(btn.dataset.open)));
  }
  for (const btn of el.querySelectorAll('button[data-act]')) {
    btn.addEventListener('click', () => handleNeedAction(btn));
  }
}

async function handleNeedAction(btn) {
  const p = state.project;
  const id = btn.dataset.id;
  const act = btn.dataset.act;
  const need = p.needs.find((n) => n.id === id);
  if (!need) return;
  const box = btn.closest('.need-actions');
  const text = box ? box.querySelector('.need-input').value.trim() : '';
  if (btn.dataset.require && !text) {
    return toast('差し戻しには修正方針の記入が必要です');
  }
  const ok = await guard('操作', async () => {
    if (act === 'feedback') {
      await api.submitFeedback(need.file, text);
      toast(text ? 'フィードバックを確定しました（次のサイクルで再開）' : '確定しました', true);
    } else if (act === 'rerun') {
      await api.submitFeedback(need.file, '');
      toast('そのまま再実行として確定しました', true);
    } else if (act === 'approve') {
      const res = await api.runAction({ dir: p.dir, action: 'approve', id, reason: text });
      toast(res.output || '承認しました', true);
    } else if (act === 'hold') {
      const res = await api.runAction({ dir: p.dir, action: 'hold', id, reason: text });
      toast(res.output || '保留（policy.deny）にしました', true);
    }
    return true;
  });
  if (ok) {
    gitPushAfterWrite(`kiro-projects-viewer: ${act} ${id}`, p.dir);
    await reloadProject();
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

// kiro-flow daemon の稼働バッジ（ロックファイル＋pid のファイル判定。CLI 不要）
function daemonBadge() {
  const d = state.flowDaemon;
  if (!d) return '';
  if (d.running === true)
    return `<span class="status-chip st-running" title="pid ${d.pid}（${esc(d.lockPath)}）">daemon 稼働中</span>`;
  if (d.running === false)
    return `<span class="status-chip st-closed" title="${esc(d.lockPath)}">daemon 停止</span>`;
  return `<span class="status-chip" title="ロックはあるが pid を読めない: ${esc(d.lockPath)}">daemon 不明</span>`;
}

function renderFlow() {
  const p = state.project;
  const el = $('tab-flow');
  if (!p) {
    el.innerHTML = '';
    return;
  }
  const busLine = `<div class="muted" style="margin-bottom:8px">
    バス: <code class="mono">${esc(p.busDir)}</code>${p.busSource && p.busSource !== 'project' ? `（${esc(p.busSource)} から発見）` : ''}
    ${daemonBadge()}
  </div>`;
  if (!state.flowRuns.length) {
    const checked = (p.busCandidates || [])
      .map((c) => `<code class="mono">${esc(c.dir)}</code>`)
      .join('<br>');
    el.innerHTML = `${busLine}<div class="empty">kiro-flow の run がありません。<br>
      （bus/ は run 完了後に掃除されるため、稼働中または --no-cleanup 時に表示されます）<br>
      ${checked ? `<span class="muted">探索したバス候補:<br>${checked}</span>` : ''}</div>`;
    return;
  }
  const runList = state.flowRuns
    .map((r) => {
      const pct = Math.round(r.progress * 100);
      const stalled =
        r.alive === false
          ? ` <span class="status-chip st-stalled" title="orchestrator の生存リースが切れています（heartbeat: ${esc(fmtAgo(r.heartbeatAt) || 'なし')}）">応答なし</span>`
          : '';
      return `<div class="run-item ${state.flowRunId === r.runId ? 'selected' : ''}" data-run="${esc(r.runId)}">
        <div class="row2"><span class="mono">${esc(r.runId)}</span><span>${statusChip(r.status)}${stalled}</span></div>
        <div class="req">${esc((r.request || '').slice(0, 120))}</div>
        <div class="progress"><div style="width:${pct}%"></div></div>
        <div class="muted">${r.counts.done}✓ ${r.counts.failed}✗ ${r.counts.claimed}▶ ／ ${r.total} ノード ｜ ${fmtAgo(r.updatedAt || r.createdAt)}</div>
      </div>`;
    })
    .join('');

  // 左右ペインは独立スクロール。再描画（ポーリング）でスクロール位置を失わないよう
  // 描画前の位置を控えて復元する
  const prevScroll = {
    runs: ($('flow-runs') || {}).scrollTop || 0,
    detail: ($('flow-detail') || {}).scrollTop || 0,
  };
  el.innerHTML = `${busLine}<div id="flow-layout">
    <div id="flow-runs">${runList || '<div class="empty">run なし</div>'}</div>
    <div id="flow-detail">${renderFlowDetail()}</div>
  </div>`;
  $('flow-runs').scrollTop = prevScroll.runs;
  $('flow-detail').scrollTop = prevScroll.detail;

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
  const nodeDetail = node ? renderFlowNode(run, node) : '';
  const events = (fr.events || [])
    .map(
      (ev) =>
        `<div>${fmtTime(ev.ts)} <strong>${esc(ev.who || '')}</strong> ${esc(ev.kind || '')} ${esc(
          summarizeEvent(ev)
        )}</div>`
    )
    .join('');
  const stalled =
    run.alive === false
      ? ` <span class="status-chip st-stalled">応答なし</span>`
      : '';
  const resumed = run.resumeCount > 0 ? `（自動再開 #${run.resumeCount}）` : '';
  const heartbeat =
    run.alive !== null && run.heartbeatAt
      ? `<div class="muted">orchestrator heartbeat: ${esc(fmtAgo(run.heartbeatAt))}${resumed}${run.alive === false ? '（生存リース切れ — daemon が再起動すれば続きから自動再開されます）' : ''}</div>`
      : '';
  // 失敗した run は人が「同じ要求で再投入」できる（新しい run として inbox へ。公式契約のみ）
  const resubmit =
    run.status === 'failed'
      ? `<button class="chip" id="flow-resubmit" title="meta の要求・ワークスペースをそのまま新しい run として inbox へ投入します（daemon が拾う）">↻ 同じ要求で再投入</button>`
      : '';
  // 不要な run の削除。実行中（orchestrator 生存）は不可 — 終端と応答なし（孤児）のみ
  const deletable = run.status === 'done' || run.status === 'failed' || run.alive === false;
  const deleteBtn = deletable
    ? `<button class="chip danger" id="flow-delete" title="この run のディレクトリ（runs/${esc(run.runId)}）をゴミ箱へ移動します">🗑 削除</button>`
    : '';
  return `
    <div class="card full">
      <h3>RUN <span class="mono">${esc(run.runId)}</span> — ${statusChip(run.status)}${stalled} ${esc(strat)} ${resubmit} ${deleteBtn}</h3>
      <div>${esc(run.request || '')}</div>
      ${heartbeat}
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

// ---------------------------------------------------------------------------
// ノード詳細（進捗・タイムライン・関連イシュー）
// ---------------------------------------------------------------------------

// ノードのタイムライン（events の claimed / result。新しい順で届く）
function nodeTimeline(nodeId) {
  return ((state.flowRun && state.flowRun.nodeEvents) || {})[nodeId] || [];
}

// ノードの進捗行: 実行中は 開始/経過/heartbeat/lease、終端は 所要/完了時刻 を出す
function nodeProgressLine(node) {
  const evs = nodeTimeline(node.id);
  const claims = evs.filter((e) => e.kind === 'claimed');
  const lastClaimTs = claims.length ? claims[0].ts : null; // 直近の claim（この試行の開始）
  const bits = [];
  if (node.retries > 0) bits.push(`作り直し #${node.retries}`);
  if (node.state === 'claimed') {
    if (lastClaimTs) bits.push(`開始 ${fmtTime(lastClaimTs)}（経過 ${fmtAgo(lastClaimTs)}）`);
    if (node.heartbeatAt) {
      const aliveLease = node.leaseUntil && node.leaseUntil * 1000 > Date.now();
      bits.push(
        `heartbeat ${fmtAgo(node.heartbeatAt)} ${aliveLease ? '<span class="status-chip st-running">生存</span>' : '<span class="status-chip st-stalled">lease 切れ（再クレーム待ち）</span>'}`
      );
    }
  } else if (node.finishedAt) {
    const dur =
      lastClaimTs && Date.parse(node.finishedAt) > Date.parse(lastClaimTs)
        ? `（所要 ${Math.round((Date.parse(node.finishedAt) - Date.parse(lastClaimTs)) / 1000)}s）`
        : '';
    bits.push(`完了 ${fmtTime(node.finishedAt)}${dur}`);
  }
  return bits.length ? `<div class="muted" style="margin-top:4px">${bits.join(' ／ ')}</div>` : '';
}

// 関連 GitLab イシューのブロック。承認/却下は結果から、実行中は決定的タスクトークンで検索
function nodeIssueBlock(run, node) {
  const cached =
    state.flowNodeIssue && state.flowNodeIssue.token === node.taskToken
      ? state.flowNodeIssue
      : null;
  const found = cached ? cached.issue : undefined;
  const repoUrl = run.workspace && run.workspace.url;

  const rows = [];
  const url = node.issueUrl || (found && found.url);
  if (url) {
    const d = node.data && typeof node.data === 'object' ? node.data : {};
    const decision = node.rejected ? 'rejected' : d.decision || (found && found.state) || '';
    const chip = decision
      ? `<span class="status-chip ${node.rejected ? 'st-blocked' : 'st-done'}">${esc(node.rejected ? '却下' : decision)}</span>`
      : '';
    rows.push(`<div class="row2" style="align-items:center;gap:8px">
      <a href="#" data-ext="${esc(url)}" class="mono">${esc(url)}</a> ${chip}
      <button data-review="${esc(url)}" title="gitlab-review-viewer で開く">レビューで開く</button>
      <button data-ext-btn="${esc(url)}" title="ブラウザで開く">↗</button>
    </div>`);
    if (found && found.title) {
      rows.push(
        `<div class="muted">#${found.iid} ${esc(found.title)}（${esc(found.state)}${found.labels && found.labels.length ? ` ／ ${found.labels.map(esc).join(', ')}` : ''}）</div>`
      );
    }
    const mrs = (found && found.relatedMrs) || [];
    if (mrs.length) {
      rows.push(
        `<div>${mrs
          .map(
            (mr) =>
              `<span class="status-chip st-${esc(mr.state)}" title="${esc(mr.title)}">!${mr.iid} ${esc(mr.state)}</span>`
          )
          .join(' ')}</div>`
      );
    }
    if (node.rejected) {
      if (d.reason) rows.push(`<div class="muted">却下理由: ${esc(String(d.reason))}</div>`);
      if (d.guidance) {
        rows.push(
          `<div><span class="label-chip">やり直し指示（人コメント）</span> ${esc(String(d.guidance).slice(0, 500))}</div>`
        );
      }
      rows.push(
        `<div class="muted">却下（未マージクローズ等）→ ノードは failed。kiro-projects 管理下なら
        イシューの人コメントを feedback に注入して自動で再委譲されます（retries 上限で「要対応」へ）。</div>`
      );
    }
  } else if (repoUrl && node.state === 'claimed') {
    // 実行中（result 未確定）: イシュー URL はまだ bus に無い。タスクトークンで検索できる
    if (cached && found === null) {
      rows.push(`<div class="muted">関連イシューは見つかりませんでした（起票前か、gitlab executor 以外のタスク）</div>`);
    } else {
      rows.push(
        `<button id="btn-find-issue" data-token="${esc(node.taskToken)}" data-repo="${esc(repoUrl)}"
          title="イシュー本文の隠しマーカー（task-token）で検索します">関連イシューを探す（GitLab API）</button>`
      );
    }
  }
  if (!rows.length) return '';
  return `<div class="section-title">関連イシュー（gitlab executor）</div>${rows.join('\n')}`;
}

function renderFlowNode(run, node) {
  const evs = nodeTimeline(node.id);
  const timeline = evs.length
    ? `<div class="section-title">タイムライン</div><div class="events">${evs
        .map(
          (e) =>
            `<div>${fmtTime(e.ts)} <strong>${esc(e.who || '')}</strong> ${esc(e.kind)}${e.status ? ` [${esc(e.status)}]` : ''}</div>`
        )
        .join('')}</div>`
    : '';
  return `<div class="card full">
      <h3><span class="mono">${esc(node.id)}</span> [${esc(node.kind)}] — ${esc(FLOW_STATE_LABEL[node.state] || node.state)}${node.who ? ` @${esc(node.who)}` : ''}</h3>
      <div>${esc(node.goal)}</div>
      ${node.deps.length ? `<div class="muted" style="margin-top:4px">依存: ${node.deps.map(esc).join(', ')}</div>` : ''}
      ${nodeProgressLine(node)}
      ${nodeIssueBlock(run, node)}
      ${node.output ? `<div class="section-title">output</div><pre class="mono">${esc(node.output.slice(0, 3000))}</pre>` : ''}
      ${node.data ? `<div class="section-title">data</div><pre class="mono">${esc(JSON.stringify(node.data, null, 2).slice(0, 2000))}</pre>` : ''}
      ${timeline}
    </div>`;
}

// 実行中ノードの関連イシューをタスクトークンで検索して表示に反映する
async function findNodeIssue(btn) {
  const token = btn.dataset.token;
  const res = await guard('イシュー検索', () =>
    api.glFindIssueByToken({ repoUrl: btn.dataset.repo, token })
  );
  if (res === undefined) return;
  if (!res.enabled) {
    toast('GitLab API が未設定です（⚙ 設定で Base URL とトークンを設定してください）');
    return;
  }
  state.flowNodeIssue = { token, issue: res.issue };
  renderFlow();
}

// 失敗 run を同じ要求で inbox へ再投入（新しい run として最初から実行される）
async function resubmitFlowRun() {
  const run = state.flowRun && state.flowRun.run;
  if (!run) return;
  const res = await guard('再投入', () => api.flowResubmit(state.project.busDir, run.runId));
  if (res) {
    const d = state.flowDaemon;
    toast(
      `再投入しました: ${res.runId}${d && d.running === false ? '（daemon 停止中 — 起動後に拾われます）' : ''}`,
      true
    );
    gitPushAfterWrite(`kiro-projects-viewer: resubmit run ${run.runId}`, state.project.busDir);
    await reloadProject();
  }
}

// 不要な run を削除する（人の明示アクション）。実行中は main 側でも拒否される
async function deleteFlowRun() {
  const run = state.flowRun && state.flowRun.run;
  if (!run) return;
  const warn =
    run.status !== 'done' && run.status !== 'failed'
      ? '\nこの run は終端していません（応答なし）。削除すると daemon 再起動時の自動再開もできなくなります。'
      : '';
  const yes = await confirmDialog(
    `run ${run.runId} を削除します。\nバスの runs/ ディレクトリごとゴミ箱へ移動します。${warn}\nよろしいですか？`
  );
  if (!yes) return;
  const ok = await guard('run 削除', async () => {
    const res = await api.flowDeleteRun(state.project.busDir, run.runId);
    toast(`run を削除しました（${res.via === 'trash' ? 'ゴミ箱へ移動' : '完全削除'}）: ${run.runId}`, true);
    return true;
  });
  if (ok) {
    gitPushAfterWrite(`kiro-projects-viewer: delete run ${run.runId}`, state.project.busDir);
    state.flowRunId = null;
    state.flowRun = null;
    state.flowNodeId = null;
    await reloadProject();
  }
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
      state.flowNodeIssue = null; // ノードを切り替えたら検索結果を破棄
      renderFlow();
    });
  }
  const rs = root.querySelector('#flow-resubmit');
  if (rs) rs.addEventListener('click', () => resubmitFlowRun());
  const fd = root.querySelector('#flow-delete');
  if (fd) fd.addEventListener('click', () => deleteFlowRun());
  const fi = root.querySelector('#btn-find-issue');
  if (fi) fi.addEventListener('click', () => findNodeIssue(fi));
  for (const btn of root.querySelectorAll('#flow-detail button[data-review]')) {
    btn.addEventListener('click', () =>
      guard('レビュー起動', async () => {
        const res = await api.openReview({ url: btn.dataset.review });
        toast(`gitlab-review-viewer を起動しました（${res.via}）`, true);
      })
    );
  }
  for (const btn of root.querySelectorAll('#flow-detail button[data-ext-btn]')) {
    btn.addEventListener('click', () => guard('外部リンク', () => api.openExternal(btn.dataset.extBtn)));
  }
}

// ---------------------------------------------------------------------------
// タブ: レビュー待ち（charter repos のオープンイシュー）
// ---------------------------------------------------------------------------
// プロジェクトが扱うリポジトリ（repos.json）の「いまレビュー待ち・作業中のイシュー」を
// GitLab API で横断一覧し、gitlab-review-viewer へ引き継ぐ入口。bus に依存しないため
// kiro-flow が起票したもの以外（人が直接立てたイシュー）も見える。
// run/ノード単位の委譲イシューの決着（承認/却下）はフロータブのノード詳細が担当。

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
  const repos = charterGitlabRepos();
  const gl = state.gitlab;

  const issueRow = (it) => {
    const enriched = gl.byUrl[it.url];
    const labels = (enriched ? enriched.labels : it.labels) || [];
    const stateStr = enriched ? enriched.state : it.state || '';
    const mrs = enriched && enriched.relatedMrs ? enriched.relatedMrs : [];
    return `<tr>
      <td class="mono">${it.iid ? `#${it.iid}` : ''}</td>
      <td>${it.title ? esc(it.title) : linkify(it.url)} <span class="muted">${esc(it.projectPath || '')}</span></td>
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

  // kiro-flow 由来（gitlab executor が起票 = 本文に task-token マーカー）だけに絞る。
  // 人が直接立てたイシューも見たいときはチップで解除できる
  const flowOnly = gl.flowOnly !== false;
  const shown = flowOnly ? gl.repoIssues.filter((it) => it.kiroFlow) : gl.repoIssues;
  const hiddenCount = gl.repoIssues.length - shown.length;

  const repoIssuesSection = shown.length
    ? `<table class="list"><tr><th>IID</th><th>イシュー</th><th>状態</th><th>ラベル</th><th>関連 MR</th><th></th></tr>
        ${shown.map((it) => issueRow(it)).join('')}</table>`
    : `<div class="muted">${
        gl.enabled === false
          ? '⚙ 設定で GitLab の URL とトークンを設定すると、repos のオープンイシューを一覧できます'
          : !repos.length
            ? 'repos が未定義です（charter の ## repos か <project>/repos.{yaml,json} で定義）'
            : flowOnly && hiddenCount
              ? `kiro-flow 由来のレビュー待ちはありません（フィルタを解除すると ${hiddenCount} 件表示されます）`
              : 'レビュー待ちのイシューはありません'
      }</div>`;

  el.innerHTML = `
    <div class="toolbar">
      <span class="muted">repos のオープンイシュー（レビュー待ち・作業中）。委譲イシューの決着（承認/却下）はフロータブのノード詳細へ</span>
      <span class="spacer"></span>
      <button id="btn-gl-flowonly" class="chip ${flowOnly ? 'active' : ''}"
        title="kiro-flow の gitlab executor が起票したイシュー（本文の task-token マーカー）だけに絞る">kiro-flow 由来のみ</button>
      <button id="btn-gl-refresh" ${gl.loading ? 'disabled' : ''}>${gl.loading ? '取得中…' : 'GitLab から最新化'}</button>
    </div>
    <div class="section-title">レビュー待ち ${[...new Set(repos.map((r) => r.projectPath))]
      .map((path) => `<span class="label-chip">${esc(path)}</span>`)
      .join('')}
      ${flowOnly && hiddenCount ? `<span class="muted">（kiro-flow 由来以外 ${hiddenCount} 件を非表示）</span>` : ''}</div>
    ${repoIssuesSection}`;

  $('btn-gl-flowonly').addEventListener('click', () => {
    gl.flowOnly = !flowOnly;
    renderGitLab();
  });
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
  const repos = charterGitlabRepos();
  if (!force && !repos.length) return;
  gl.loading = true;
  renderGitLab();
  try {
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
    // 関連 MR（レビュー対象）を補完する。「レビュー待ち」の主目的なので repo イシューに行う
    const urls = repoIssues.map((i) => i.url).filter(Boolean);
    if (urls.length && gl.enabled !== false) {
      const res = await api.glEnrich(urls);
      for (const issue of res.issues || []) {
        if (issue && issue.url && !issue.error) gl.byUrl[issue.url] = issue;
      }
    }
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
  $('cfg-git-pull').value = cfg.kiro && cfg.kiro.gitPullSec !== undefined ? cfg.kiro.gitPullSec : 300;
  $('cfg-git-autopush').checked = !!(cfg.kiro && cfg.kiro.gitAutoPush);
  $('cfg-kiro-command').value = (cfg.kiro && cfg.kiro.command) || 'kiro-projects';
  $('cfg-action-mode').value = (cfg.kiro && cfg.kiro.actionMode) || 'auto';
  $('cfg-flow-bus').value = (cfg.kiro && cfg.kiro.flowBus) || '';
  $('cfg-flow-lockdir').value = (cfg.kiro && cfg.kiro.flowLockDir) || '';
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
  cfg.kiro.gitPullSec = Math.max(0, parseInt($('cfg-git-pull').value, 10) || 0);
  cfg.kiro.gitAutoPush = $('cfg-git-autopush').checked;
  cfg.kiro.command = $('cfg-kiro-command').value.trim() || 'kiro-projects';
  cfg.kiro.actionMode = $('cfg-action-mode').value;
  cfg.kiro.flowBus = $('cfg-flow-bus').value.trim();
  cfg.kiro.flowLockDir = $('cfg-flow-lockdir').value.trim();
  cfg.gitlab.baseUrl = $('cfg-gl-url').value.trim();
  cfg.gitlab.token = $('cfg-gl-token').value.trim();
  cfg.reviewViewer.mode = $('cfg-rv-mode').value;
  cfg.reviewViewer.command = $('cfg-rv-command').value.trim();
  state.config = await api.saveConfig(cfg);
  setupPolling();
  await refreshAll();
  toast('設定を保存しました', true);
}

// ---------------------------------------------------------------------------
// git pull（選択中プロジェクトのリポジトリ最新化）
// ---------------------------------------------------------------------------
// 自動: ポーリングのたびに呼ぶが、実際の pull は main 側が設定間隔（下限 60 秒）で
// スロットリングする（リモートサーバへ負荷をかけない）。git リポジトリでない
// プロジェクトは黙ってスキップされる。エラーは同じ内容を繰り返しトーストしない。
let lastGitPullError = null;

async function maybeAutoGitPull() {
  const sec = state.config && state.config.kiro ? Number(state.config.kiro.gitPullSec) : 0;
  if (!sec || !state.selectedDir) return;
  try {
    const res = await api.gitPull(state.selectedDir, false);
    if (res && !res.skipped) lastGitPullError = null;
  } catch (err) {
    const msg = err.message || String(err);
    if (lastGitPullError !== msg) {
      lastGitPullError = msg;
      toast(`git pull（自動）: ${msg}`);
    }
  }
}

// 管理ファイルを書き換えた操作（指示ドロップ・inbox 投入・needs 記入・削除）の後に呼ぶ。
// 設定 gitAutoPush が有効なら、操作したディレクトリの変更をコミットして push する
// （状態共有 git への都度反映）。書き込み本体は成功済みなので待たずに走らせ、
// 失敗（push 不可など）だけトーストで知らせる。
function gitPushAfterWrite(message, dir) {
  const cfg = state.config;
  if (!cfg || !cfg.kiro || !cfg.kiro.gitAutoPush) return;
  const target = dir || state.selectedDir;
  if (!target) return;
  api.gitCommitPush(target, message).catch((err) => {
    toast(`git 同期（プッシュ）: ${err.message || err}`);
  });
}

// 手動（⇣ ボタン）: スロットリングを無視して即 pull し、結果をトーストで知らせる
async function manualGitPull() {
  if (!state.selectedDir) return toast('プロジェクトを選択してください');
  const res = await guard('git pull', () => api.gitPull(state.selectedDir, true));
  if (!res) return;
  lastGitPullError = null;
  toast(`git pull: ${res.output || '完了'}`, true);
  await refreshAll();
}

async function refreshAll() {
  if (state.busy) return;
  state.busy = true;
  try {
    await maybeAutoGitPull();
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
      // ダイアログを開いている間・入力中は更新しない（書きかけの入力を消さない）
      if ($('dlg-settings').open || $('dlg-task').open || $('dlg-enqueue').open || $('dlg-confirm').open) return;
      const ae = document.activeElement;
      if (ae && (ae.tagName === 'TEXTAREA' || ae.tagName === 'INPUT')) return;
      const typed = [...document.querySelectorAll('#content .need-input')].some((t) => t.value.trim());
      if (typed) return;
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
  $('btn-git-pull').addEventListener('click', manualGitPull);
  $('btn-settings').addEventListener('click', openSettings);
  $('btn-save-settings').addEventListener('click', () => saveSettings());
  $('btn-task-close').addEventListener('click', () => $('dlg-task').close());
  $('btn-enq-cancel').addEventListener('click', () => $('dlg-enqueue').close());
  $('btn-enq-submit').addEventListener('click', submitEnqueue);
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
