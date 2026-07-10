'use strict';

/* global api */

const $ = (id) => document.getElementById(id);

const state = {
  config: null,
  discovery: { projects: [], instances: [] },
  selectedDir: null, // 選択中プロジェクトのディレクトリ
  project: null, // readProject のスナップショット
  flowRuns: [],
  flowDaemon: null, // {running, pid, lockPath}（ロックファイルからの判定）
  flowRunId: null,
  flowRun: null, // {run, events, nodeEvents}
  flowNodeId: null,
  flowNodeIssue: null, // {token, issue|null}（実行中ノードのイシュー検索結果キャッシュ）
  // GitLab 突き合わせ結果を run 単位でキャッシュする（run を切り替えても保持し、再取得を避ける）。
  // { [runId]: { loading, at, byNode: {[id]:{reconciled,url,issueState,labels,relatedMrs,...}} } }
  flowReconcile: {},
  backlogFilter: 'active',
  gitlab: { enabled: false, byUrl: {}, repoIssues: [], loading: false, flowOnly: true },
  editFile: null, // {dir, name, file}（編集中のプロジェクトファイル）
  enqueueExtra: null, // {level, track}（再投入で引き継ぐが UI に出さない値）
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

// レビュー引き継ぎ結果のトースト。exe-running は「起動」ではなく既に起動中の
// gitlab-review-viewer への即時ハンドオフ（portable exe の再起動コストを回避した経路）。
function reviewToast(via) {
  toast(
    via === 'exe-running'
      ? '起動中の gitlab-review-viewer に引き継ぎました'
      : `gitlab-review-viewer を起動しました（${via}）`,
    true
  );
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
  return fmtAgoSec((Date.now() - t) / 1000);
}

function fmtAgoSec(sec) {
  if (sec === null || sec === undefined || isNaN(sec)) return '';
  sec = Math.max(0, sec);
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
  const { projects, instances } = state.discovery;
  if (!projects.length) {
    tree.innerHTML =
      '<div class="empty">プロジェクトが見つかりません。<br>⚙ 設定でプロジェクトルート（状態共有リポジトリの clone）を追加するか、<br>kiro-project を稼働させてください。<br><br><button id="btn-empty-new" class="primary-inline">＋ 新規プロジェクトを作成</button></div>';
    const nb = $('btn-empty-new');
    if (nb) nb.addEventListener('click', openNewProject);
  } else {
    tree.innerHTML = projects
      .map((p) => {
        const badges = [];
        if (p.needsCount) badges.push(`<span class="badge warn" title="要対応">${p.needsCount}</span>`);
        if (p.backlogCount) badges.push(`<span class="badge" title="バックログ">${p.backlogCount}</span>`);
        if (p.hasCharter) badges.push('<span class="badge info" title="charter あり">C</span>');
        // via='status-sync' はリモート本体を git 同期越しに推定した稼働判定（同期遅延を許容）。
        // ローカル確定（instances）と見分けられるよう dot に補助クラスと ~ 印を付ける
        const live = p.liveness || { via: p.running ? 'instances' : 'none' };
        const remoteGuess = live.via === 'status-sync';
        const dotTitle = p.paused
          ? '一時停止中'
          : p.running
            ? remoteGuess
              ? `稼働中（同期経由の推定・約${Math.round((live.ageSec || 0) / 60)}分前に確認）`
              : '稼働中'
            : remoteGuess
              ? `不明（最終確認 約${Math.round((live.ageSec || 0) / 60)}分前・同期経由）`
              : '停止中';
        const missing = !p.exists ? '（見つかりません）' : '';
        return `<div class="project-item ${state.selectedDir === p.dir ? 'selected' : ''}" data-dir="${esc(p.dir)}" title="${esc(p.dir)}">
          <span class="dot ${p.running ? 'running' : ''} ${remoteGuess ? 'synced' : ''} ${p.paused ? 'paused' : ''}" title="${esc(dotTitle)}"></span>
          <span class="name">${esc(p.name)}${remoteGuess && p.running ? '~' : ''}${p.paused ? ' ⏸' : ''}${missing}</span>${badges.join('')}
        </div>`;
      })
      .join('');
  }
  const live = instances.filter((i) => i.fresh).length;
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
  // 復元/更新された選択中 run も、開いたときと同様に一度だけ自動突き合わせる（律速でポーリング毎回は叩かない）
  if (state.flowRun && state.flowRun.run) maybeAutoReconcile(state.flowRun.run);
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
  if (p.liveness && p.liveness.paused) badges.push('<span class="status-chip st-review">⏸ 一時停止中</span>');
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

const STATUS_ORDER = ['proposed', 'ready', 'doing', 'offloaded', 'review', 'blocked', 'inbox', 'draft'];

function renderOverview() {
  const p = state.project;
  const el = $('tab-overview');
  if (!p) {
    el.innerHTML = '<div class="empty">左のツリーからプロジェクトを選択してください</div>';
    return;
  }
  if (state.simpleMode) {
    renderOverviewSimple(el, p);
    return;
  }
  const parts = [];

  // プロジェクトファイルの編集（人が書く上位入力: charter / policy / repos）
  parts.push(`<div class="card full edit-toolbar">
    <h3>プロジェクトファイル</h3>
    <div class="row">
      <button class="chip" data-edit="charter.md">✎ charter.md</button>
      <button class="chip" data-edit="policy.md">✎ policy.md</button>
      <button class="chip" data-edit="repos.json">✎ repos.json</button>
      <span class="muted">charter を編集すると次の run で backlog（後段データ）に反映されます</span>
    </div>
  </div>`);

  // ステータスタイル
  const tiles = STATUS_ORDER.map(
    (st) =>
      `<div class="tile st-${st}"><div class="num">${p.byStatus[st] || 0}</div><div class="label">${st}</div></div>`
  );
  tiles.push(
    `<div class="tile st-done"><div class="num">${p.archive.length}</div><div class="label">done（累計）</div></div>`
  );
  parts.push(`<div class="card full"><h3>バックログ</h3><div class="tiles">${tiles.join('')}</div></div>`);

  // 複数 charter（charters/<name>.md = バージョン）の一覧
  if (p.charters && p.charters.length) {
    const chStates = (p.projectState && p.projectState.charters) || {};
    const rows = p.charters
      .map((ch) => {
        const st = chStates[ch.name] || {};
        const total = Number(st.acceptance_total || (ch.acceptanceItems || []).length || 0);
        const best = Number(st.best || 0);
        return `<tr>
          <td class="mono">${esc(ch.name)}</td>
          <td>${esc(ch.goal || ch.name || '')}</td>
          <td>${total ? `${best} / ${total} PASS` : '—'}</td>
          <td>${st.status ? statusChip(st.status) : '<span class="muted">未実行</span>'}</td>
          <td><button class="chip" data-edit="charters/${esc(ch.name)}.md">✎ 編集</button></td>
        </tr>`;
      })
      .join('');
    parts.push(`<div class="card full">
      <h3>バージョン（charters/ — 全バージョンを並行駆動）</h3>
      <table class="list"><tr><th>charter</th><th>goal</th><th>acceptance</th><th>状態</th><th></th></tr>${rows}</table>
    </div>`);
  }

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

  // daemon 生存（liveness）。同一ホストなら instances で確定、リモートは status.json
  // （state_git 経由の同期・遅延許容の推定）でしか分からない。最終サイクルは run-log.jsonl
  // （既に同期済み）から補う — idle 中は status.json だけが唯一の「生きている」根拠になる
  const live = p.liveness || { running: false, via: 'none', ageSec: null };
  const lastRunLog = p.runLog.length ? p.runLog[p.runLog.length - 1] : null;
  const liveDesc =
    live.via === 'instances'
      ? '<span class="status-chip st-done">● 稼働中</span><span class="muted">（同一ホスト・instances で確定）</span>'
      : live.via === 'status-sync'
        ? `<span class="status-chip ${live.running ? 'st-done' : 'st-blocked'}">${live.running ? '● 稼働中（推定）' : '○ 不明'}</span>` +
          `<span class="muted">（status.json 同期経由・最終確認 ${fmtAgoSec(live.ageSec)}` +
          (live.watch !== undefined ? `・watch=${live.watch ? 'on' : 'off'}` : '') +
          (live.level ? `・level=${esc(live.level)}` : '') +
          '）</span>'
        : '<span class="status-chip st-blocked">○ 判定不能</span><span class="muted">（instances も status.json も無し。ローカルで稼働させるか state_git 同期を設定してください）</span>';
  parts.push(`
    <div class="card full">
      <h3>daemon の生存</h3>
      <div>${liveDesc}</div>
      <div class="muted" style="margin-top:4px">
        最終サイクル: ${lastRunLog ? `${fmtTime(lastRunLog.ts)}（${esc(lastRunLog.reason || '')}・${fmtAgo(lastRunLog.ts)}）` : 'run 履歴なし'}
      </div>
    </div>`);

  parts.push(lifecycleCardHtml(p));

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

  // 危険な操作: プロジェクトのリセット（charter 以外を全消去して kiro-flow を停止）。
  // charter.md が無い（バックログ消化モード）と「残すものが無い」ためリセットは出せない。
  if (p.charter) {
    parts.push(`
    <div class="card full">
      <h3>危険な操作</h3>
      <div class="row">
        <button class="chip danger" id="btn-reset-project"
          title="charter.md 以外の全データ（backlog / archive / needs / decisions / journal / run-log / DELIVERY / inbox / commands / bus など）をゴミ箱へ移動し、kiro-flow daemon を停止します">
          ⚠ リセット（charter 以外を全消去 + kiro-flow 停止）</button>
        <span class="muted">charter.md だけを残して最初からやり直します。本体（kiro-project）が稼働中なら次パスで charter から再分解されます</span>
      </div>
    </div>`);
  }

  el.innerHTML = parts.join('\n');

  for (const b of el.querySelectorAll('button[data-edit]')) {
    b.addEventListener('click', () => openEditFile(b.dataset.edit));
  }
  const resetBtn = $('btn-reset-project');
  if (resetBtn) resetBtn.addEventListener('click', () => resetProject());
  bindLifecycleButtons(el);
}

// 稼働操作（pause / resume / stop）。commands/ ドロップ（＋git push）で届き、
// リモート本体（WSL・別ホスト）の watch が同期間隔内に取り込む。
function lifecycleCardHtml(p) {
  const paused = !!(p.liveness && p.liveness.paused);
  return `
    <div class="card full">
      <h3>稼働操作</h3>
      <div class="row">
        ${
          paused
            ? '<button class="chip" data-lifecycle="resume" title="一時停止を解除して消化を再開します">▶ 再開</button>'
            : '<button class="chip" data-lifecycle="pause" title="watch の消化を一時停止します（監視・指示の取り込みは継続）">⏸ 一時停止</button>'
        }
        <button class="chip danger" data-lifecycle="stop"
          title="プロセスを graceful 停止します。再開はプロジェクトのマシンで kiro-project start を実行してください">⏹ 停止</button>
        <span class="muted">指示は commands/ ドロップ（＋git push）で届き、本体が同期間隔内に取り込みます</span>
      </div>
      ${paused ? '<div class="muted" style="margin-top:4px">⏸ 一時停止中（resume 待ち。needs 検収・指示の投入はそのまま可能です）</div>' : ''}
    </div>`;
}

const LIFECYCLE_CONFIRMS = {
  pause: (p) => `${p.name}: watch の消化を一時停止します（idle 監視・指示の取り込みは継続）。よろしいですか？`,
  resume: (p) => `${p.name}: 一時停止を解除して消化を再開します。よろしいですか？`,
  stop: (p) =>
    `${p.name}: 本体プロセスを停止します。\n再開はプロジェクトのマシン（WSL 等）で kiro-project start を実行してください。よろしいですか？`,
};

function bindLifecycleButtons(root) {
  for (const b of root.querySelectorAll('button[data-lifecycle]')) {
    b.addEventListener('click', async () => {
      const p = state.project;
      if (!p) return;
      const action = b.dataset.lifecycle;
      const yes = await confirmDialog(LIFECYCLE_CONFIRMS[action](p));
      if (!yes) return;
      const ok = await guard('稼働操作', async () => {
        const res = await api.requestLifecycle(p.dir, action, 'kiro-projects-viewer から操作');
        toast(res.output, true);
        return true;
      });
      if (ok) {
        gitPushAfterWrite(`kiro-projects-viewer: ${action}`, p.dir);
        await reloadProject();
      }
    });
  }
}

// かんたんモードの概要: 非技術者向けに「いま何をしているか / あなたの番 / できあがったもの」
// の 3 面へ絞る（技術情報はメンテナンスモードで見る）。
function renderOverviewSimple(el, p) {
  const parts = [];
  const live = p.liveness || { running: false };
  const undecided = p.needs.filter((n) => !n.decided);
  const working = (p.byStatus.doing || 0) + (p.byStatus.offloaded || 0) + p.claims.length;
  const waiting = (p.byStatus.ready || 0) + (p.byStatus.inbox || 0);
  const doneCount = p.archive.length;

  let now;
  if (live.paused) now = '⏸ 一時停止中です（「▶ 再開」で続きから動き出します）。';
  else if (!live.running) now = '停止中です（このプロジェクトのマシンで起動されるのを待っています）。';
  else if (working) now = `いま ${working} 件の作業を進めています。`;
  else if (undecided.length) now = 'あなたの確認・判断を待っています。';
  else if (waiting) now = `次の作業（残り ${waiting} 件）に取りかかるところです。`;
  else now = '新しい仕事を待っています（やることはすべて完了しています）。';

  parts.push(`
    <div class="card full">
      <h3>いま何をしているか</h3>
      <div class="big">${esc(now)}</div>
      <div class="muted" style="margin-top:6px">
        目標: ${esc((p.charter && (p.charter.goal || p.charter.name)) || '（charter 未設定）')}
      </div>
      <div class="muted">進み具合: 完了 ${doneCount} 件 ／ 作業中 ${working} 件 ／ これから ${waiting} 件</div>
    </div>`);

  parts.push(`
    <div class="card full">
      <h3>あなたの番です${undecided.length ? `（${undecided.length} 件）` : ''}</h3>
      ${
        undecided.length
          ? `<div>確認・判断が必要な項目があります。</div>
             <div class="row" style="margin-top:6px"><button class="chip" id="btn-simple-needs">要対応を開く（${undecided.length} 件）</button></div>`
          : '<div class="muted">いま対応が必要なものはありません。</div>'
      }
    </div>`);

  const rows = p.delivery
    .slice(-5)
    .reverse()
    .map((cells) => `<tr>${cells.map((c) => `<td>${linkify(c)}</td>`).join('')}</tr>`)
    .join('');
  parts.push(`
    <div class="card full">
      <h3>できあがったもの（直近 5 件）</h3>
      ${rows ? `<table class="list">${rows}</table>` : '<div class="muted">まだ納品はありません。</div>'}
    </div>`);

  parts.push(lifecycleCardHtml(p));
  el.innerHTML = parts.join('\n');
  const nb = $('btn-simple-needs');
  if (nb) nb.addEventListener('click', () => switchTab('needs'));
  bindLifecycleButtons(el);
}

// プロジェクトのリセット（危険操作）。charter.md 以外の全データを削除し、バスの
// kiro-flow daemon を停止する。charter が残るので、稼働中の kiro-project は次パスで
// charter から再分解して最初からやり直す（done の記録・needs・決定記録もすべて消える）。
async function resetProject() {
  const p = state.project;
  if (!p || !p.charter) return;
  const sharedBusNote =
    p.busSource && p.busSource !== 'project'
      ? '\n⚠ 共有バス構成です: kiro-flow daemon の停止は他プロジェクトの実行にも影響します。'
      : '';
  const yes = await confirmDialog(
    `${p.name}: charter.md 以外の全データを削除し、kiro-flow daemon を停止します。\n` +
      `削除対象: backlog ${p.backlog.length} 件・archive（done）${p.archive.length} 件・needs ${p.needs.length} 件・` +
      `実行中クレーム ${p.claims.length} 件、および decisions / journal / run-log / DELIVERY / inbox / commands / bus 等の全ファイル。\n` +
      `ファイルはゴミ箱へ移動します（ゴミ箱の無い環境では完全削除）。${sharedBusNote}\n` +
      `charter.md は残るため、本体（kiro-project）が稼働中なら次パスで charter から再分解して最初からやり直します。よろしいですか？`
  );
  if (!yes) return;
  const ok = await guard('プロジェクトのリセット', async () => {
    const res = await api.resetProject(p.dir);
    const d = res.daemon || {};
    const daemonMsg = !d.running
      ? 'kiro-flow daemon は稼働していませんでした'
      : d.stopped
        ? `kiro-flow daemon を停止しました（pid=${d.pid}）`
        : d.remote
          ? 'kiro-flow daemon は別ホストで稼働中のためここからは停止できません（本体側で停止してください）'
          : `kiro-flow daemon を停止できませんでした（pid=${d.pid}）`;
    const errMsg = res.errors && res.errors.length ? `／削除失敗 ${res.errors.length} 件（${res.errors.map((e) => e.name).join(', ')}）` : '';
    toast(`${p.name}: ${res.removed.length} 件を削除（charter.md は残しました）${errMsg}。${daemonMsg}`, !errMsg);
    return true;
  });
  if (ok) {
    gitPushAfterWrite('kiro-projects-viewer: project reset (keep charter)', p.dir);
    await reloadProject();
  }
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
  ['offloaded', 'offloaded'],
  ['review', 'review'],
  ['blocked', 'blocked'],
  ['inbox', 'inbox'],
  ['draft', 'draft'],
  ['archive', 'done（archive）'],
];

// ---------------------------------------------------------------------------
// 関係性（charter → backlog → run → issue）の突き合わせと画面遷移
//   run-id `req-<hash>-<taskid>-r<retries>` を鍵に、バックログのタスク（安定オブジェクト）と
//   その kiro-flow run（リトライ系統）を結ぶ。リトライは「意味的に同一」なので系統でまとめる。
// ---------------------------------------------------------------------------

// kiro-project の run-id 生成（_submit_req_id）と同じ task.id 正規化。バックログの task.id を
// run-id 内の taskId 断片へ合わせるために使う。
// tid に依存するタスク（after 逆辺・推移）。却下・修正の影響一覧に使う
function dependentsOf(tasks, tid) {
  const deps = (t) =>
    String((t.extra && t.extra.after) || '')
      .split(/[\s,]+/)
      .filter(Boolean);
  const out = [];
  const seen = new Set([tid]);
  let frontier = new Set([tid]);
  while (frontier.size) {
    const next = new Set();
    for (const t of tasks) {
      if (seen.has(t.id)) continue;
      if (deps(t).some((d) => frontier.has(d))) {
        out.push(t);
        seen.add(t.id);
        next.add(t.id);
      }
    }
    frontier = next;
  }
  return out;
}

function rejectConfirmMessage(p, id, what) {
  const downs = dependentsOf(p.backlog, id);
  const impact = downs.length
    ? `\n影響範囲（このタスクに依存・推移）: ${downs.map((t) => `${t.id}[${t.status}]`).join(', ')}\n` +
      '依存タスクは実行前レビュー（proposed）に戻して再審査にかけます。'
    : '\n依存しているタスクはありません。';
  return (
    `${id} を却下（${what}）します。\n` +
    'タスクは廃止（archive へ退避・avoid 記録）され、charter があれば再計画が要求されます。' +
    impact +
    '\nよろしいですか？'
  );
}

function sanitizeTaskId(id) {
  return String(id == null ? '' : id)
    .replace(/[^\w.-]+/g, '_')
    .slice(0, 60);
}

// あるバックログタスクに紐づく kiro-flow run を、リトライ世代の新しい順で返す。
function runsForTask(taskId) {
  const key = sanitizeTaskId(taskId);
  return state.flowRuns
    .filter((r) => r.taskId && sanitizeTaskId(r.taskId) === key)
    .sort(
      (a, b) =>
        (b.retries || 0) - (a.retries || 0) ||
        String(b.createdAt || '').localeCompare(String(a.createdAt || ''))
    );
}

// run 一覧を「系統（lineageId＝同一タスク）」でまとめる。req- 形式でない run（手動/単発）は単独系統。
function lineageGroups(runs) {
  const groups = new Map();
  for (const r of runs) {
    const key = r.lineageId || r.runId; // 素の run は自分だけの系統
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  const out = [];
  for (const [key, list] of groups) {
    list.sort(
      (a, b) =>
        (b.retries || 0) - (a.retries || 0) ||
        String(b.createdAt || '').localeCompare(String(a.createdAt || ''))
    );
    out.push({ key, latest: list[0], attempts: list });
  }
  out.sort((a, b) =>
    String(b.latest.updatedAt || b.latest.createdAt || '').localeCompare(
      String(a.latest.updatedAt || a.latest.createdAt || '')
    )
  );
  return out;
}

// タブを切り替える（initTabs のクリックと同じ DOM 操作をプログラムから行う）。
const SIMPLE_TABS = new Set(['overview', 'needs']);

function switchTab(name) {
  if (state.simpleMode && !SIMPLE_TABS.has(name)) name = 'overview';
  document
    .querySelectorAll('.tab')
    .forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tabpane').forEach((pane) => pane.classList.remove('active'));
  const pane = $(`tab-${name}`);
  if (pane) pane.classList.add('active');
  if (name === 'gitlab') refreshGitLab(false);
}

// run を選んでフロータブへ遷移。
function gotoRun(runId) {
  switchTab('flow');
  selectFlowRun(runId);
}

// run とノードを選んでフロータブへ遷移し、そのノードの詳細を開く。
// レビュー待ち画面から「このイシューを起票した run/ノード」を一発で開くのに使う。
async function gotoRunNode(runId, nodeId) {
  switchTab('flow');
  await selectFlowRun(runId); // 内部で flowNodeId を null にして再描画する
  if (nodeId) {
    state.flowNodeId = nodeId;
    state.flowNodeIssue = null;
    renderFlow();
    const pane = $('flow-node');
    if (pane) pane.scrollTop = 0;
  }
}

// req-<hash>-<task>-r<n> の先頭ハッシュを畳んで読みやすい短い run 表記にする
// （素の run-… やその他はそのまま）。関連 run チップの表示に使う。
function shortRunId(runId) {
  const m = /^req-[0-9a-f]{6,}-(.+)$/.exec(String(runId || ''));
  return m ? m[1] : String(runId || '');
}

// レビュー待ちイシュー（本文の task-token）→ 起票した kiro-flow run/ノードの索引。
// flowRuns は reloadProject で常にロード済みで、各ノードは決定的タスクトークン
// （nodeTaskToken）を持つため、追加の API/走査コストなしで対応付けられる。
// イシュー URL は承認/却下まで bus に現れないので、レビュー待ち中の対応付けは
// この token 一致が唯一確実な手がかりになる。
function flowNodeByToken() {
  const map = {};
  for (const r of state.flowRuns) {
    for (const n of Object.values(r.nodes || {})) {
      if (n.taskToken && !map[n.taskToken]) {
        map[n.taskToken] = { runId: r.runId, nodeId: n.id, status: r.status, taskId: r.taskId };
      }
    }
  }
  return map;
}

// バックログタスク（run-id 内の taskId 断片でも可）を開いてバックログタブへ遷移。
function gotoTask(taskId) {
  const p = state.project;
  if (!p) return;
  const key = sanitizeTaskId(taskId);
  let t = p.backlog.find((x) => sanitizeTaskId(x.id) === key);
  let scope = 'backlog';
  if (!t) {
    t = p.archive.find((x) => sanitizeTaskId(x.id) === key);
    scope = 'archive';
  }
  switchTab('backlog');
  if (scope === 'archive') {
    state.backlogFilter = 'archive';
    renderBacklog();
  }
  if (t) showTaskDialog(t.id, scope);
  else toast(`タスク ${taskId} は現在のバックログに見つかりません（gc/archive 済みかも）`);
}

// run 1 件を表す小さなクリップ（リトライ世代＋状態色）。クリックで run へ遷移。
function runPill(r, current = false) {
  const gen = r.retries != null ? `r${r.retries}` : 'run';
  const rev = r.rev ? `·v${r.rev}` : '';
  return `<button class="rel-pill st-${esc(r.status)}${current ? ' current' : ''}"
    data-goto-run="${esc(r.runId)}" title="${esc(r.runId)} — ${esc(r.status)}">${gen}${rev}</button>`;
}

// 関係性のパンくず: charter ▸ task ▸ run(系統) ▸ issue。各セグメントはクリックで該当画面へ。
function relationshipStrip({ taskId, run } = {}) {
  const p = state.project;
  const segs = [];
  if (p && p.charter && p.charter.name) {
    segs.push(`<span class="rel-seg charter" title="プロジェクト定義">🎯 ${esc(p.charter.name)}</span>`);
  }
  const tid = taskId || (run && run.taskId);
  if (tid) {
    segs.push(
      `<button class="rel-seg task" data-goto-task="${esc(tid)}" title="バックログのタスクへ">🗒 ${esc(tid)}</button>`
    );
  }
  const attempts = tid ? runsForTask(tid) : run ? [run] : [];
  if (attempts.length) {
    const pills = attempts
      .slice()
      .reverse()
      .map((r) => runPill(r, run && r.runId === run.runId))
      .join('');
    segs.push(`<span class="rel-seg runs">⚙ ${pills}</span>`);
  } else if (run) {
    segs.push(`<span class="rel-seg runs">⚙ ${runPill(run, true)}</span>`);
  }
  const issues = run ? run.gitlabIssues || [] : attempts.flatMap((r) => r.gitlabIssues || []);
  const url = issues[0] && issues[0].url;
  if (url) {
    segs.push(
      `<button class="rel-seg issue" data-open-ext="${esc(url)}" title="GitLab イシューを開く">🔗 issue${issues.length > 1 ? ` ×${issues.length}` : ''}</button>`
    );
  }
  if (segs.length < 2) return ''; // 単独セグメントだけならパンくずの意味がない
  return `<div class="rel-strip">${segs.join('<span class="rel-arrow">▸</span>')}</div>`;
}

// タスクダイアログ用: 関連する run（リトライ系統）を一覧する。
function relatedRunsBlock(taskId) {
  const rr = runsForTask(taskId);
  if (!rr.length) return '';
  const items = rr
    .map(
      (r) => `<div class="rel-run-row">
        <button class="linklike mono" data-goto-run="${esc(r.runId)}">${esc(r.runId)}</button>
        ${statusChip(r.status)}
        <span class="muted">${r.counts.done}✓ ${r.counts.failed}✗ ／ ${r.total} ノード</span>
        ${r.inheritedFrom ? `<span class="muted" title="このリトライが引き継いだ先行 run">↩ ${esc(r.inheritedFrom)}</span>` : ''}
      </div>`
    )
    .join('');
  return `<div class="section-title">関連する kiro-flow run（リトライ系統）</div>
    <div class="rel-runs">${items}</div>`;
}

// パンくず／リンクのクリック配線（dialog・detail・backlog 各ルートから呼ぶ）。
function bindRelationship(root) {
  for (const b of root.querySelectorAll('[data-goto-run]')) {
    b.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const dlg = $('dlg-task');
      if (dlg && dlg.open) dlg.close();
      gotoRun(b.dataset.gotoRun);
    });
  }
  for (const b of root.querySelectorAll('[data-goto-task]')) {
    b.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const dlg = $('dlg-task');
      if (dlg && dlg.open) dlg.close();
      gotoTask(b.dataset.gotoTask);
    });
  }
  for (const b of root.querySelectorAll('[data-open-ext]')) {
    b.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      guard('リンクを開く', () => api.openExternal(b.dataset.openExt));
    });
  }
}

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

  // 複数 charter 運用: charter（バージョン）でさらに絞り込む
  const charterNames = (p.charters || []).map((c) => c.name);
  if (charterNames.length && state.backlogCharter) {
    tasks = tasks.filter((t) => (t.extra.charter || '') === state.backlogCharter);
  }
  const charterChips = charterNames.length
    ? `<span class="muted" style="margin-left:8px">charter:</span>` +
      ['', ...charterNames]
        .map(
          (n) =>
            `<button class="chip ${((state.backlogCharter || '') === n) ? 'active' : ''}" data-charter-filter="${esc(n)}">${n ? esc(n) : '全部'}</button>`
        )
        .join('')
    : '';

  // priority 降順 → 古い順（planner none と同じ感覚）
  tasks = [...tasks].sort((a, b) => b.priority - a.priority || a.mtime - b.mtime);

  const rows = tasks
    .map((t) => {
      const extras = [];
      if (t.extra.charter) extras.push(`charter: ${t.extra.charter}`);
      if (t.extra.after) extras.push(`after: ${t.extra.after}`);
      if (t.extra.level) extras.push(`level: ${t.extra.level}`);
      if (t.extra.track) extras.push(`track: ${t.extra.track}`);
      if (t.extra.review) extras.push(`review: ${t.extra.review}`);
      if (t.status === 'offloaded' && t.extra.flow_loc) {
        extras.push(`委譲実行中: ${t.extra.flow_loc}`); // act_async: kiro-flow daemon で結果待ち
      }
      const rr = runsForTask(t.id); // 紐づく kiro-flow run（リトライ系統）
      const runBadge = rr.length
        ? ` <button class="badge run-link" data-goto-run="${esc(rr[0].runId)}" title="関連 run ${rr.length} 件（最新 ${esc(rr[0].runId)} — ${esc(rr[0].status)}）へ移動">⚙${rr.length}</button>`
        : '';
      // 非ブロッキング委譲（offloaded）は flow_run（実行中の run-id）へ直接リンクする
      // （runsForTask が拾えない＝フローバス未登録でも辿れるように明示リンクを出す）。
      const offloadRun = t.status === 'offloaded' ? String(t.extra.flow_run || '').trim() : '';
      const offloadBadge =
        offloadRun && !(rr.length && rr[0].runId === offloadRun)
          ? ` <button class="badge run-link" data-goto-run="${esc(offloadRun)}" title="委譲実行中の run ${esc(offloadRun)} へ移動">▶ run</button>`
          : '';
      return `<tr class="clickable" data-task="${esc(t.id)}" data-scope="${state.backlogFilter === 'archive' ? 'archive' : 'backlog'}">
        <td class="mono">${esc(t.id)}</td>
        <td>${esc(t.title)}</td>
        <td>${statusChip(t.status)}${p.claims.includes(t.id) ? ' <span class="badge info" title="実行中">▶</span>' : ''}${isReviseSent(t) ? ' <span class="badge" title="修正指示送信済み（取り込み待ち）">✎</span>' : ''}${runBadge}${offloadBadge}</td>
        <td>${t.priority}</td>
        <td>${t.retries}</td>
        <td>${t.verify ? '✓' : t.extra.accept || t.extra.verify_template ? '△' : '—'}</td>
        <td class="muted">${esc(extras.join(' ／ '))}</td>
      </tr>`;
    })
    .join('');

  const replanPending = !!p.replanPending;
  el.innerHTML = `
    <div class="filters">${chips}${charterChips}<span class="muted">${tasks.length} 件</span>
      ${p.inboxFiles && p.inboxFiles.length ? `<span class="badge info" title="${esc(p.inboxFiles.join(', '))}">inbox 取り込み待ち ${p.inboxFiles.length}</span>` : ''}
      ${replanPending ? '<span class="badge info" title="charter からの再分解を要求済み。本体が次パスで取り込みます">再分解 取り込み待ち</span>' : ''}
      <span class="spacer"></span>
      <button id="btn-replan" class="primary-inline"${replanPending ? ' disabled' : ''} title="charter からバックログを再分解します（エラー回復用）。既に done / 既存と類似のタスクは投入しません">↻ charter から再分解</button>
      <button id="btn-enqueue" class="primary-inline" title="バックログにタスクを 1 件追加します（inbox 経由）">＋ バックログに追加</button>
    </div>
    <details class="backlog-help">
      <summary>バックログの変え方（すべて公式契約・即時ではありません）</summary>
      <div class="muted">
        <b>追加</b>: 「＋ バックログに追加」→ <code class="mono">inbox</code> に 1 件投入。本体が次サイクルで backlog タスク（<code class="mono">backlog/&lt;id&gt;.md</code>）にします。<br>
        <b>変更</b>: 行をクリック →「✎ 修正して指示（revise）」で title・優先度・verify・accept・依存 after・note・level・track を置換＋フィードバック注入。<br>
        <b>タスクグラフの再構築</b>: revise は本体が取り込むと <code class="mono">rev</code> を上げ、kiro-flow に新しいタスクグラフ（run の DAG）を作らせます（実行中タスクは現在の試行を破棄して積み直し）。依存 after を変えるとグラフの形が変わります。<br>
        <b>再分解（エラー回復）</b>: 「↻ charter から再分解」→ 本体が <code class="mono">charter.md</code> を分解し直し、取りこぼした差分だけを backlog に入れます。<b>既に done / 既存と類似のタスクは投入しません</b>（重複排除）。plan 失敗・タスクの誤削除などからの復帰用です。<br>
        いずれも状態（done 等）は直接書き換えません（done は verify のみが根拠、の不変条件を保つため）。
      </div>
    </details>
    ${
      rows
        ? `<table class="list"><tr><th>ID</th><th>タイトル</th><th>状態</th><th>優先度</th><th>retry</th><th>verify</th><th>属性</th></tr>${rows}</table>`
        : '<div class="empty">タスクなし</div>'
    }`;

  $('btn-enqueue').addEventListener('click', () => openEnqueueDialog());
  const replanBtn = $('btn-replan');
  if (replanBtn && !replanPending) replanBtn.addEventListener('click', () => requestReplan());

  for (const chip of el.querySelectorAll('.chip[data-filter]')) {
    chip.addEventListener('click', () => {
      state.backlogFilter = chip.dataset.filter;
      renderBacklog();
    });
  }
  for (const chip of el.querySelectorAll('.chip[data-charter-filter]')) {
    chip.addEventListener('click', () => {
      state.backlogCharter = chip.dataset.charterFilter;
      renderBacklog();
    });
  }
  for (const row of el.querySelectorAll('tr[data-task]')) {
    row.addEventListener('click', () => showTaskDialog(row.dataset.task, row.dataset.scope));
  }
  bindRelationship(el); // 行内の run バッジ（⚙N）クリックでフロータブへ（行クリックより優先）
}

// revise（人の即時フィードバック）も commands/ 経由で届くためタスクファイル自体は
// すぐには変わらない。needs と同じく「送信済み（取り込み待ち）」をファイルパス + mtime で
// 覚え、本体が取り込んでファイルが書き換わる（mtime 変化）まで再送を防ぐ。
function loadReviseSent() {
  try {
    const v = JSON.parse(localStorage.getItem('kpv:reviseSent') || '{}');
    return v && typeof v === 'object' ? v : {};
  } catch {
    return {};
  }
}

const reviseSent = loadReviseSent();

function markReviseSent(t) {
  reviseSent[t.file] = t.mtime;
  localStorage.setItem('kpv:reviseSent', JSON.stringify(reviseSent));
}

function isReviseSent(t) {
  if (reviseSent[t.file] === undefined) return false;
  if (reviseSent[t.file] === t.mtime) return true;
  // 本体が取り込んでファイルが書き換わった → マーカーは古い（掃除して再度操作可能に）
  delete reviseSent[t.file];
  localStorage.setItem('kpv:reviseSent', JSON.stringify(reviseSent));
  return false;
}

// revise フォーム。フィールドは「置換」で、変更した項目 + フィードバックだけを送る。
// 実行中（doing）のタスクにも送れる: 本体は現在の試行を確定せず修正内容で積み直す。
function reviseAreaHtml(t) {
  if (isReviseSent(t)) {
    return `<div class="muted" style="margin-top:8px">✎ 修正指示を送信済みです（本体の取り込み待ち。取り込まれると再度操作できます）</div>`;
  }
  const doingNote =
    t.status === 'doing'
      ? '<div class="muted">実行中のタスクです: 送信すると現在の試行の結果は確定されず、修正内容とフィードバックでタスクグラフ（kiro-flow run）を積み直します（早い軌道修正）。</div>'
      : t.status === 'offloaded'
        ? '<div class="muted">委譲実行中（kiro-flow daemon で結果待ち・act_async）です: 送信するとこの run の結果は確定されず、修正を反映した新しいタスクグラフが作られます（反映は run 完了時に行われます）。</div>'
        : '<div class="muted">本体が取り込むと <code class="mono">rev</code> を上げ、次の実行で新しいタスクグラフ（kiro-flow run）が作られます。依存 after を変えるとグラフの形が変わります。</div>';
  return `<details class="revise-area"><summary>✎ 修正して指示（revise）</summary>
    ${doingNote}
    <div class="field"><label>フィードバック（次の実行に必ず反映される指示）</label>
      <textarea rows="2" id="rv-feedback" placeholder="例: e2e はローカルサーバでなく実サーバに配備して実施すること"></textarea></div>
    <div class="field"><label>タイトル</label><input id="rv-title" value="${esc(t.title)}" /></div>
    <div class="row2">
      <div class="field"><label>優先度（整数・大ほど高）</label><input id="rv-priority" type="number" step="1" value="${t.priority}" /></div>
      <div class="field"><label>依存 after（タスクグラフの形。カンマ区切り。空にすると解除）</label><input id="rv-after" class="mono" value="${esc(t.extra.after || '')}" /></div>
    </div>
    <div class="field"><label>verify（空にすると削除）</label><input id="rv-verify" class="mono" value="${esc(t.verify || '')}" /></div>
    <div class="field"><label>accept（空にすると削除）</label><input id="rv-accept" value="${esc(t.extra.accept || '')}" /></div>
    <div class="row2">
      <div class="field"><label>level（report/assisted/unattended。空にすると削除）</label>
        <input id="rv-level" list="rv-level-list" value="${esc(t.extra.level || '')}" />
        <datalist id="rv-level-list"><option value="report"></option><option value="assisted"></option><option value="unattended"></option></datalist>
      </div>
      <div class="field"><label>track（同種タスクの群名。空にすると削除）</label><input id="rv-track" value="${esc(t.extra.track || '')}" /></div>
    </div>
    <div class="field"><label>note（メモ。空にすると削除）</label><input id="rv-note" value="${esc(t.extra.note || '')}" /></div>
    <div class="row need-buttons">
      <span class="muted">変更した項目とフィードバックだけが送られ、決定記録（DR）に残ります</span>
      <span class="spacer"></span>
      <button class="primary-inline" id="btn-revise-send">➤ 修正を送信</button>
    </div>
  </details>`;
}

function showTaskDialog(id, scope) {
  const p = state.project;
  const list = scope === 'archive' ? p.archive : p.backlog;
  const t = list.find((x) => x.id === id);
  if (!t) return;
  const extraRows = Object.entries(t.extra)
    .map(([k, v]) => {
      // flow_run（offloaded の委譲先 run-id）はフロータブの該当 run へのリンクにする
      const cell =
        k === 'flow_run' && String(v).trim()
          ? `<button class="linklike mono" data-goto-run="${esc(String(v).trim())}" title="委譲実行中の run へ移動">${esc(v)}</button>`
          : `<pre class="mono">${esc(v)}</pre>`;
      return `<tr><th>${esc(k)}</th><td>${cell}</td></tr>`;
    })
    .join('');
  // 決定記録を残す人の操作（backlog のタスクのみ。archive は閲覧のみ）
  const canApprove = ['blocked', 'review', 'proposed'].includes(t.status);
  const deps = String(t.extra.after || '').trim();
  const downs = dependentsOf(p.backlog, t.id);
  const depRow = `<tr><th>依存関係</th><td class="muted">前提（after）: ${deps ? esc(deps) : '（なし）'} ／ 依存先（このタスクの変更が影響・推移）: ${
    downs.length ? downs.map((x) => `${esc(x.id)}[${esc(x.status)}]`).join(', ') : '（なし）'
  }</td></tr>`;
  // 削除を拒むのは「実行中」だけ。クレームロックは worker クラッシュや
  // review/blocked 滞留で残骸が残るため、doing 以外ではロックがあっても削除できる
  const claimed = p.claims.includes(t.id) && t.status === 'doing';
  const actionArea =
    scope === 'archive'
      ? `<div class="need-actions">
          <div class="row need-buttons">
            <span class="muted">done として archive 済み。誤 done などの復帰に、内容を編集して inbox へ再投入できます。</span>
            <span class="spacer"></span>
            <button class="primary-inline" id="btn-task-reinject" title="この archive タスクの内容を編集して inbox へ再投入します（新しいタスクとして triage→verify を通ります）">↻ revise して再投入</button>
          </div>
        </div>`
      : `<div class="need-actions">
          <textarea rows="2" id="task-reason" class="need-input" placeholder="操作の理由（決定記録 decisions/ に残ります）"></textarea>
          <div class="row need-buttons">
            ${canApprove ? `<button class="primary-inline" data-taskact="approve">✓ 承認</button>` : ''}
            ${t.status === 'doing' ? '' : `<button class="danger" data-taskact="reject" data-confirm-reject="1" title="廃止して archive へ退避。依存タスクは再審査に戻り、charter があれば再計画を要求します">✕ 却下</button>`}
            <button data-taskact="pin">▲ 最優先へ（pin）</button>
            <button data-taskact="defer">▽ 後回し（defer）</button>
            <button data-taskact="hold">⏸ 保留（hold）</button>
            <span class="spacer"></span>
            <button class="danger" id="btn-task-delete" ${claimed ? 'disabled' : ''}
              title="${claimed ? '実行中（doing・クレーム中）のタスクは削除できません' : 'backlog のタスクファイルをゴミ箱へ移動します（決定記録は残りません）'}">🗑 削除</button>
          </div>
        </div>`;
  $('dlg-task-body').innerHTML = `
    <h2><span class="mono">${esc(t.id)}</span>: ${esc(t.title)}</h2>
    ${relationshipStrip({ taskId: t.id })}
    <table class="list">
      <tr><th>状態</th><td>${statusChip(t.status)}</td></tr>
      <tr><th>出自</th><td>${esc(t.source)}</td></tr>
      <tr><th>優先度</th><td>${t.priority}</td></tr>
      <tr><th>retries</th><td>${t.retries}</td></tr>
      <tr><th>verify</th><td>${t.verify ? `<pre class="mono">${esc(t.verify)}</pre>` : '<span class="muted">（未定義）</span>'}</td></tr>
      ${depRow}
      ${extraRows}
      <tr><th>ファイル</th><td><a href="#" id="task-open-file" class="mono">${esc(t.file)}</a></td></tr>
    </table>
    ${relatedRunsBlock(t.id)}
    ${actionArea}
    ${scope === 'archive' ? '' : reviseAreaHtml(t)}`;
  bindRelationship($('dlg-task-body')); // パンくず・関連 run のクリック配線
  const link = $('task-open-file');
  if (link) link.addEventListener('click', (e) => {
    e.preventDefault();
    guard('ファイルを開く', () => api.openPath(t.file));
  });
  for (const btn of document.querySelectorAll('#dlg-task-body button[data-taskact]')) {
    btn.addEventListener('click', async () => {
      const reason = $('task-reason') ? $('task-reason').value.trim() : '';
      if (btn.dataset.confirmReject) {
        if (!reason) return toast('却下には理由の記入が必要です（決定記録・avoid 学習に残ります）');
        const yes = await confirmDialog(rejectConfirmMessage(p, t.id, '廃止して関連バックログを再計画'));
        if (!yes) return;
      }
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
  // 修正して指示（revise）。変更したフィールド + フィードバックだけを届ける
  const rvBtn = $('btn-revise-send');
  if (rvBtn) {
    rvBtn.addEventListener('click', async () => {
      const fields = {};
      const cmp = [
        ['title', $('rv-title').value.trim(), String(t.title || '')],
        ['priority', $('rv-priority').value.trim(), String(t.priority)],
        ['after', $('rv-after').value.trim(), String(t.extra.after || '')],
        ['verify', $('rv-verify').value.trim(), String(t.verify || '')],
        ['accept', $('rv-accept').value.trim(), String(t.extra.accept || '')],
        ['level', $('rv-level').value.trim(), String(t.extra.level || '')],
        ['track', $('rv-track').value.trim(), String(t.extra.track || '')],
        ['note', $('rv-note').value.trim(), String(t.extra.note || '')],
      ];
      for (const [key, cur, orig] of cmp) {
        if (key === 'priority' && cur === '') continue; // 空欄は「変更なし」（priority に削除は無い）
        if (cur !== orig.trim()) fields[key] = cur;
      }
      const feedback = $('rv-feedback').value.trim();
      if (!Object.keys(fields).length && !feedback) {
        return toast('変更する項目かフィードバックを入力してください');
      }
      const reason = $('task-reason') ? $('task-reason').value.trim() : '';
      const ok = await guard('修正（revise）', async () => {
        const res = await api.runAction({ dir: p.dir, action: 'revise', id: t.id, reason, fields, feedback });
        markReviseSent(t);
        toast(res.output || `${t.id} の修正を送信しました`, true);
        return true;
      });
      if (ok) {
        gitPushAfterWrite(`kiro-projects-viewer: revise ${t.id}`, p.dir);
        $('dlg-task').close();
        await reloadProject();
      }
    });
  }
  // 削除（人の明示アクション）。kiro-project に削除の公式契約は無いため、
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
  // archive（done）タスクの revise 再投入。元タスクの内容を prefill した inbox 投入
  // ダイアログを開く（エラー復帰用途。archive の記録は消さず新しいタスクとして通す）
  const reBtn = $('btn-task-reinject');
  if (reBtn) {
    reBtn.addEventListener('click', () => {
      $('dlg-task').close();
      openEnqueueDialog({
        reinject: true,
        id: t.id,
        title: t.title,
        verify: t.verify,
        accept: t.extra.accept || '',
        priority: t.priority,
        note: t.extra.note || '',
        after: t.extra.after || '',
        level: t.extra.level || '',
        track: t.extra.track || '',
      });
    });
  }
  $('dlg-task').showModal();
}

// charter からのバックログ再分解を要求する（エラー回復用）。本体が次パスで charter を
// 分解し直し、取りこぼした差分だけを backlog へ入れる（done / 既存と類似は投入しない）。
// 状態（done 等）は書き換えず、公式契約（commands/replan・CLI replan）だけで届ける。
async function requestReplan() {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const yes = await confirmDialog(
    `${p.name}: charter からバックログを再分解します（エラー回復）。\n` +
      '本体が charter.md を分解し直し、取りこぼした差分だけを backlog に入れます。\n' +
      '既に done / 既存と類似のタスクは投入しません（重複排除）。状態は書き換えません。\n' +
      '反映は本体の次パス（即時ではありません）。よろしいですか？'
  );
  if (!yes) return;
  const ok = await guard('再分解の要求', async () => {
    const res = await api.requestReplan(p.dir, 'kiro-projects-viewer から再分解を要求');
    toast(res.output || 'charter からの再分解を要求しました', true);
    return true;
  });
  if (ok) {
    gitPushAfterWrite('kiro-projects-viewer: replan request', p.dir);
    await reloadProject();
  }
}

// タスク追加ダイアログを開く。prefill.reinject が真のときは archive タスクの
// 「revise して再投入」モード（エラー復帰用途）— 元タスクの内容を編集して inbox へ入れる。
function openEnqueueDialog(prefill = {}) {
  const reinject = !!prefill.reinject;
  $('enq-heading').textContent = reinject
    ? 'archive タスクを revise して再投入'
    : 'バックログにタスクを 1 件追加（inbox 経由）';
  const note = $('enq-reinject-note');
  if (reinject) {
    note.textContent =
      `archive/${prefill.id || ''}.md を編集して inbox へ再投入します。新しいタスクとして triage→verify を通り、` +
      'アーカイブの記録はそのまま残ります（誤 done などのエラー復帰用途）。';
    note.classList.remove('hidden');
  } else {
    note.classList.add('hidden');
  }
  $('enq-title').value = prefill.title || '';
  $('enq-verify').value = prefill.verify || '';
  $('enq-accept').value = prefill.accept || '';
  $('enq-priority').value = prefill.priority != null && prefill.priority !== '' ? String(prefill.priority) : '0';
  $('enq-note').value = prefill.note || '';
  $('enq-id').value = prefill.id || '';
  $('enq-after').value = prefill.after || '';
  // level / track はフォームに出さないが、再投入では元タスクの値を引き継いで送る
  state.enqueueExtra = { level: prefill.level || '', track: prefill.track || '' };
  $('dlg-enqueue').showModal();
}

async function submitEnqueue() {
  const p = state.project;
  if (!p) return;
  const extra = state.enqueueExtra || {};
  const spec = {
    title: $('enq-title').value,
    verify: $('enq-verify').value,
    accept: $('enq-accept').value,
    priority: $('enq-priority').value,
    note: $('enq-note').value,
    id: $('enq-id').value,
    after: $('enq-after').value,
    level: extra.level,
    track: extra.track,
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
// オーサリング: 新規プロジェクト作成・プロジェクトファイル編集
// ---------------------------------------------------------------------------

// 既知プロジェクトの親フォルダ ＋ 設定 roots の親（新規作成先の候補）
function knownRoots() {
  const roots = new Set();
  for (const p of state.discovery.projects || []) {
    if (p.dir) roots.add(p.dir.replace(/[\\/][^\\/]+$/, ''));
  }
  for (const r of (state.config && state.config.kiro && state.config.kiro.roots) || []) {
    if (r) roots.add(String(r).replace(/[\\/][^\\/]+$/, ''));
  }
  return [...roots].filter(Boolean);
}

// 新規プロジェクトの repos 行を 1 つ追加する（任意・複数可）
function addRepoRow(prefill = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'np-repo-row';
  wrap.innerHTML = `
    <input class="np-r-name mono" placeholder="name" value="${esc(prefill.name || '')}" />
    <input class="np-r-url mono" placeholder="git URL（必須）" value="${esc(prefill.url || '')}" />
    <input class="np-r-base mono" placeholder="base 例 main" value="${esc(prefill.base || '')}" />
    <input class="np-r-owns mono" placeholder="owns グロブ（省略=参照のみ）" value="${esc(prefill.owns || '')}" />
    <input class="np-r-desc" placeholder="説明（desc）" value="${esc(prefill.desc || '')}" />
    <button type="button" class="np-r-del" title="この行を削除">✕</button>`;
  wrap.querySelector('.np-r-del').addEventListener('click', () => wrap.remove());
  $('np-repos').appendChild(wrap);
}

function openNewProject() {
  const roots = knownRoots();
  $('np-root-list').innerHTML = roots.map((r) => `<option value="${esc(r)}"></option>`).join('');
  $('np-root').value = state.selectedDir
    ? state.selectedDir.replace(/[\\/][^\\/]+$/, '') || roots[0] || ''
    : roots[0] || '';
  $('np-name').value = '';
  if ($('np-charter')) $('np-charter').value = '';
  $('np-goal').value = '';
  $('np-deliverables').value = '';
  $('np-constraints').value = '';
  $('np-acceptance').value = '';
  $('np-repos').innerHTML = '';
  $('dlg-new-project').showModal();
}

async function submitNewProject() {
  const repos = [...document.querySelectorAll('#np-repos .np-repo-row')]
    .map((row) => ({
      name: row.querySelector('.np-r-name').value.trim(),
      url: row.querySelector('.np-r-url').value.trim(),
      base: row.querySelector('.np-r-base').value.trim(),
      owns: row.querySelector('.np-r-owns').value.trim(),
      desc: row.querySelector('.np-r-desc').value.trim(),
    }))
    .filter((r) => r.url);
  const spec = {
    root: $('np-root').value.trim(),
    name: $('np-name').value.trim(),
    charterName: $('np-charter') ? $('np-charter').value.trim() : '',
    goal: $('np-goal').value,
    deliverables: $('np-deliverables').value,
    constraints: $('np-constraints').value,
    acceptance: $('np-acceptance').value,
    repos,
  };
  const res = await guard('プロジェクト作成', async () => {
    const r = await api.createProject(spec);
    toast(`作成しました: ${r.dir}`, true);
    return r;
  });
  if (!res) return;
  // 発見対象に入るよう、作成したプロジェクトルートを設定 roots に追加する
  // （discovery は config roots を resolve して並べるため、生パスの追加で表示される）
  const known = (state.discovery.projects || []).some((p) => p.dir === res.dir);
  if (!known) {
    const cfg = state.config;
    cfg.kiro = cfg.kiro || {};
    cfg.kiro.roots = cfg.kiro.roots || [];
    if (!cfg.kiro.roots.includes(res.dir)) {
      cfg.kiro.roots.push(res.dir);
      state.config = await api.saveConfig(cfg);
    }
  }
  gitPushAfterWrite(`kiro-projects-viewer: create project ${spec.name}`, res.dir);
  $('dlg-new-project').close();
  await refreshDiscovery();
  await selectProject(res.dir);
}

// charter.md / policy.md / repos.json の直接編集ダイアログを開く。
// これらは kiro-project の「人が書く入力」— 編集して保存すると次の run で後段
// （backlog 生成・ルーティング）に反映される。タスク状態は編集対象にしない。
async function openEditFile(name) {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const info = await guard('ファイル読込', () => api.readProjectFile(p.dir, name));
  if (!info) return;
  state.editFile = { dir: p.dir, name, file: info.file };
  $('ef-title').textContent = `編集: ${info.label}`;
  $('ef-content').value = info.content || '';
  const warn = $('ef-warning');
  if (info.generated) {
    warn.textContent =
      '⚠ この repos.json は charter.md の ## repos から自動生成されています（_meta.generated_from）。' +
      '直接編集しても run 時に charter から上書きされます。恒久的に手で管理するなら _meta を消すか、' +
      'charter の ## repos を編集してください。';
    warn.classList.remove('hidden');
  } else {
    warn.classList.add('hidden');
  }
  $('ef-hint').textContent = info.exists
    ? `${info.file}｜保存すると次の kiro-project run から後段データに反映されます`
    : `${info.file}（未作成 — 保存すると新規作成します）`;
  $('dlg-edit-file').showModal();
}

async function saveEditFile() {
  const ef = state.editFile;
  if (!ef) return;
  const content = $('ef-content').value;
  const ok = await guard('保存', async () => {
    await api.writeProjectFile(ef.dir, ef.name, content);
    toast(`${ef.name} を保存しました`, true);
    return true;
  });
  if (ok) {
    gitPushAfterWrite(`kiro-projects-viewer: edit ${ef.name}`, ef.dir);
    $('dlg-edit-file').close();
    await reloadProject();
  }
}

// ---------------------------------------------------------------------------
// タブ: 要対応（needs）
// ---------------------------------------------------------------------------

// 承認 / 保留は commands/ ドロップ（または CLI）で届けるため needs/<id>.md 自体は
// 変わらず、本体が取り込んでファイルを消すまでカードが「未対応」のまま残って
// ボタンも再送できてしまう。送信済みをファイルパス + mtime で覚えておき
// （localStorage — 再起動しても保持）、「指示送信済み（取り込み待ち）」表示に変える。
// ファイルが書き換わったら（mtime 変化）マーカーは無効になり、操作は再び可能になる。
function loadNeedsSent() {
  try {
    const v = JSON.parse(localStorage.getItem('kpv:needsSent') || '{}');
    return v && typeof v === 'object' ? v : {};
  } catch {
    return {};
  }
}

const needsSent = loadNeedsSent();

function markNeedSent(need) {
  needsSent[need.file] = need.mtime;
  localStorage.setItem('kpv:needsSent', JSON.stringify(needsSent));
}

function isNeedSent(need) {
  if (needsSent[need.file] === undefined) return false;
  if (needsSent[need.file] === need.mtime) return true;
  // ファイルが書き換わった → マーカーは古い（掃除して操作を再度出す）
  delete needsSent[need.file];
  localStorage.setItem('kpv:needsSent', JSON.stringify(needsSent));
  return false;
}

// needs の種類ごとに出すアクション。
//   plan-review … 実行前レビュー: 承認して実行を許可 / 差し戻し（kiro-project が修正・記入必須）/ 却下
//   blocked   … フィードバック再開（[x] 記入）/ そのまま再実行 / 保留（hold）
//   review    … 成果物レビュー: 承認して done 確定 / 差し戻し（記入必須）/ 却下
//   milestone … プロジェクト承認（approve <project>）
function needActionsHtml(n) {
  const kind = n.kind || 'blocked';
  const buttons = [];
  if (kind === 'plan-review') {
    buttons.push(`<button class="primary-inline" data-act="approve" data-id="${esc(n.id)}">✓ 承認（実行を許可）</button>`);
    buttons.push(`<button data-act="feedback" data-id="${esc(n.id)}" data-require="1">↩ 差し戻す（kiro-project が修正・記入必須）</button>`);
    buttons.push(`<button class="danger" data-act="reject" data-id="${esc(n.id)}" data-require="1">✕ 却下（廃止して再計画）</button>`);
  } else if (kind === 'review') {
    buttons.push(`<button class="primary-inline" data-act="approve" data-id="${esc(n.id)}">✓ 承認して done 確定</button>`);
    buttons.push(`<button data-act="feedback" data-id="${esc(n.id)}" data-require="1">↩ 差し戻す（記入必須）</button>`);
    buttons.push(`<button class="danger" data-act="reject" data-id="${esc(n.id)}" data-require="1">✕ 却下（廃止して再計画）</button>`);
  } else if (kind === 'milestone') {
    buttons.push(`<button class="primary-inline" data-act="approve" data-id="${esc(n.id)}">✓ プロジェクトを承認（完了確定）</button>`);
    buttons.push(`<button data-act="feedback" data-id="${esc(n.id)}">↩ フィードバックを送る</button>`);
  } else {
    buttons.push(`<button class="primary-inline" data-act="feedback" data-id="${esc(n.id)}">➤ フィードバックして再開</button>`);
    buttons.push(`<button data-act="rerun" data-id="${esc(n.id)}">↻ そのまま再実行</button>`);
    buttons.push(`<button data-act="hold" data-id="${esc(n.id)}">⏸ 保留（hold）</button>`);
  }
  const ph =
    kind === 'plan-review'
      ? '差し戻しの修正指示・却下の理由（承認だけなら空欄で OK）'
      : kind === 'review'
        ? '差し戻す場合の修正方針・却下の理由（承認だけなら空欄で OK。approve の理由にも使われます）'
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
  const settled = (n) => n.decided || isNeedSent(n); // 対応済み（本体の取り込み待ち）
  const cards = [...p.needs]
    .sort((a, b) => Number(settled(a)) - Number(settled(b)) || b.mtime - a.mtime)
    .map((n) => {
      const chip = n.decided
        ? '<span class="status-chip st-done">記入済み（取り込み待ち）</span>'
        : isNeedSent(n)
          ? '<span class="status-chip st-review">指示送信済み（取り込み待ち）</span>'
          : '<span class="status-chip st-blocked">未対応</span>';
      return `<div class="need-card kind-${esc(n.kind || 'blocked')}">
        <div class="need-head">
          <span class="badge ${settled(n) ? '' : 'warn'}">${esc(n.kind || 'blocked')}</span>
          <span class="title">${esc(n.title || n.id)}</span>
          <span class="muted">${esc(n.date || '')}</span>
          ${chip}
        </div>
        <div class="body">${mdToHtml(n.body)}</div>
        ${settled(n) ? '' : needActionsHtml(n)}
      </div>`;
    })
    .join('');
  el.innerHTML = `<div class="muted" style="margin-bottom:8px">
      回答はこの画面から送信できます（needs/&lt;id&gt;.md の「## Decision Outcome」記入 + <code>- [x]</code> 確定と同じ。
      稼働中の kiro-project が自動で取り込みます）。</div>${cards}`;

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
      // 指示は commands/CLI 経由で needs ファイル自体は変わらない。取り込みまで
      // カードが未対応のまま残らないよう送信済みマーカーを付ける
      markNeedSent(need);
      toast(res.output || '承認しました', true);
    } else if (act === 'hold') {
      const res = await api.runAction({ dir: p.dir, action: 'hold', id, reason: text });
      markNeedSent(need);
      toast(res.output || '保留（policy.deny）にしました', true);
    } else if (act === 'reject') {
      const yes = await confirmDialog(rejectConfirmMessage(p, id, '廃止して関連バックログを再計画'));
      if (!yes) return false;
      const res = await api.runAction({ dir: p.dir, action: 'reject', id, reason: text });
      markNeedSent(need);
      toast(res.output || '却下しました（依存タスクは再審査へ・再計画を要求）', true);
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
  parked: '承認待ち',
  pending: '待機（実行可能）',
  waiting: '依存待ち',
};

const TERMINAL_NODE_STATES = new Set(['done', 'failed']);

// run 一括の突き合わせ結果（glReconcileRun のノード要素）を、found と同じ形のイシュー情報にする
function recToIssue(rec) {
  if (!rec || !rec.url) return undefined;
  return {
    url: rec.url,
    iid: rec.iid || null,
    title: rec.title || '',
    state: rec.issueState || '',
    labels: rec.labels || [],
    relatedMrs: rec.relatedMrs || [],
  };
}

// kiro-flow daemon の稼働バッジ。
//   via='lock'        … 同一ホストのロックファイル（pid 生存）で確定判定
//   via='status-sync' … state_git（鏡）越しに同期された status.json による推定（同期遅延を許容）
//   via='none'         … 判定材料なし
function daemonBadge() {
  const d = state.flowDaemon;
  if (!d) return '';
  const synced = d.via === 'status-sync';
  if (d.running === true) {
    const detail = synced
      ? `同期経由の推定・最終確認 ${fmtAgoSec(d.ageSec)}${d.orchestrators !== undefined ? `・run ${d.orchestrators}/worker ${d.workers}` : ''}`
      : `pid ${d.pid}（${esc(d.lockPath)}）`;
    return `<span class="status-chip st-running" title="${esc(detail)}">daemon 稼働中${synced ? '（推定）' : ''}</span>`;
  }
  if (d.running === false) {
    if (synced) {
      return `<span class="status-chip" title="status.json 同期経由・最終確認 ${fmtAgoSec(d.ageSec)}が鮮度窓を超過">daemon 不明（同期経由）</span>`;
    }
    if (d.via === 'none') {
      return `<span class="status-chip" title="ロックも status.json も無し">daemon 停止/判定不能</span>`;
    }
    return `<span class="status-chip st-closed" title="${esc(d.lockPath)}">daemon 停止</span>`;
  }
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
  // 同一タスクのリトライ（req-…-r0/r1/…）は「意味的に同一」なので系統でまとめ、
  // 最新試行を見出しにして過去の試行はリトライ・ピルで畳む。素の run は単独系統。
  const runList = lineageGroups(state.flowRuns)
    .map((g) => {
      const r = g.latest;
      const pct = Math.round(r.progress * 100);
      const stalled =
        r.alive === false
          ? ` <span class="status-chip st-stalled" title="orchestrator の生存リースが切れています（heartbeat: ${esc(fmtAgo(r.heartbeatAt) || 'なし')}）">応答なし</span>`
          : '';
      const taskLink = r.taskId
        ? ` <button class="badge task-link" data-goto-task="${esc(r.taskId)}" title="バックログのタスクへ移動">🗒 ${esc(r.taskId)}</button>`
        : '';
      const retryStrip =
        g.attempts.length > 1
          ? `<div class="run-retries" title="このタスクのリトライ系統">試行 ${g.attempts.length}: ${g.attempts
              .slice()
              .reverse()
              .map((a) => runPill(a, a.runId === state.flowRunId))
              .join('')}</div>`
          : r.inheritedFrom
            ? `<div class="muted" title="引き継いだ先行 run">↩ 引き継ぎ元 <span class="mono">${esc(r.inheritedFrom)}</span></div>`
            : '';
      return `<div class="run-item ${state.flowRunId === r.runId ? 'selected' : ''}" data-run="${esc(r.runId)}">
        <div class="row2"><span class="mono">${esc(r.runId)}</span><span>${statusChip(r.status)}${stalled}</span></div>
        <div class="req">${esc((r.request || '').slice(0, 120))}</div>
        <div class="progress"><div style="width:${pct}%"></div></div>
        <div class="muted">${r.counts.done}✓ ${r.counts.failed}✗ ${r.counts.claimed}▶ ／ ${r.total} ノード ｜ ${fmtAgo(r.updatedAt || r.createdAt)}${taskLink}</div>
        ${retryStrip}
      </div>`;
    })
    .join('');

  // run 一覧と RUN 表示ペイン（概要 / タスクグラフ / ノード情報の 3 分割）は
  // 再描画（ポーリング・ノード選択）でスクロール位置を失わないよう、描画前の
  // 位置を控えて復元する。グラフは縦横どちらのスクロールも保つ。
  const prevGraph = $('graph-box');
  const prevScroll = {
    runs: ($('flow-runs') || {}).scrollTop || 0,
    overview: ($('flow-overview') || {}).scrollTop || 0,
    graphPane: ($('flow-graph') || {}).scrollTop || 0,
    nodePane: ($('flow-node') || {}).scrollTop || 0,
    graphX: prevGraph ? prevGraph.scrollLeft : 0,
    graphY: prevGraph ? prevGraph.scrollTop : 0,
  };
  el.innerHTML = `${busLine}<div id="flow-layout">
    <div id="flow-runs">${runList || '<div class="empty">run なし</div>'}</div>
    <div id="flow-detail">${renderFlowDetail()}</div>
  </div>`;
  $('flow-runs').scrollTop = prevScroll.runs;
  if ($('flow-overview')) $('flow-overview').scrollTop = prevScroll.overview;
  if ($('flow-graph')) $('flow-graph').scrollTop = prevScroll.graphPane;
  if ($('flow-node')) $('flow-node').scrollTop = prevScroll.nodePane;
  const graph = $('graph-box');
  if (graph) {
    graph.scrollLeft = prevScroll.graphX;
    graph.scrollTop = prevScroll.graphY;
  }

  for (const item of el.querySelectorAll('.run-item[data-run]')) {
    item.addEventListener('click', () => selectFlowRun(item.dataset.run));
  }
  bindFlowDetail(el);
  bindRelationship(el); // リトライ・ピル／タスクリンク／パンくずのクリック配線（行クリックより優先）
}

async function selectFlowRun(runId) {
  state.flowRunId = runId;
  state.flowNodeId = null;
  state.flowRun = await guard('run 読込', () => api.flowRun(state.project.busDir, runId));
  renderFlow();
  // run を開いたら関連イシューの「今」を一度だけ自動で突き合わせる（律速あり・GitLab 設定時のみ）。
  // これで実行中/クローズ済みのイシュー状態がクリック無しでノードに出る（キャッシュに載る）。
  if (state.flowRun && state.flowRun.run) maybeAutoReconcile(state.flowRun.run);
}

// run 単位の突き合わせキャッシュ（無ければ undefined）。
function reconcileEntry(runId) {
  return state.flowReconcile[runId];
}

// この run で GitLab クローズ反映が有効なノードの終端状態（'done'|'failed'）を返す。無ければ null。
function reconciledStateFor(run, nodeId) {
  const e = run && reconcileEntry(run.runId);
  const rec = e && e.byNode && e.byNode[nodeId];
  return rec && rec.reconciled ? rec.reconciled : null;
}

// 突き合わせ対象ノード（waiting は起票前が確定なので除外、終端は bus が正なので除外）。
function reconcilableNodes(run) {
  return Object.values(run.nodes || {}).filter(
    (n) => n.state !== 'waiting' && !TERMINAL_NODE_STATES.has(n.state) && n.taskToken
  );
}

// GitLab の Base URL / トークンが設定済みか（未設定なら突き合わせは無駄なので走らせない）。
function gitlabConfigured() {
  const gl = state.config && state.config.gitlab;
  return Boolean(gl && gl.baseUrl && gl.token);
}

// 同じ run を短時間に何度も自動突き合わせしない律速（手動ボタンは無視して即実行）。
const AUTO_RECONCILE_THROTTLE_MS = 60000;

// run を開いたときに一度だけ自動で突き合わせる（クリック無しでイシュー状態を出す）。
// GitLab 未設定・対象ノード無し・律速内・取得中はスキップ。トーストは出さない（自動なので静か）。
function maybeAutoReconcile(run) {
  if (!run || !gitlabConfigured()) return;
  if (!(run.workspace && run.workspace.url)) return;
  if (!reconcilableNodes(run).length) return;
  const e = reconcileEntry(run.runId);
  if (e && e.loading) return; // 取得中
  if (e && e.at && Date.now() - e.at < AUTO_RECONCILE_THROTTLE_MS) return; // 律速内＝キャッシュを使う
  reconcileFlowRun({ auto: true });
}

// 選択中 run の非終端ノードを GitLab の「今」と突き合わせ、イシュー状態をノードに反映する。
// クローズ済みは完了/失敗として先読み反映（gitlab executor が result を書く前でも映す）、
// オープン中（レビュー待ち）はリンク＋状態を出す。auto=true は自動発火（トーストを出さない）。
async function reconcileFlowRun(opts) {
  const auto = !!(opts && opts.auto);
  const run = state.flowRun && state.flowRun.run;
  if (!run) return;
  const repoUrl = run.workspace && run.workspace.url;
  if (!repoUrl) {
    if (!auto) toast('この run には突き合わせ先リポジトリ（workspace）がありません');
    return;
  }
  const nodes = reconcilableNodes(run).map((n) => ({ id: n.id, taskToken: n.taskToken, state: n.state }));
  const prev = reconcileEntry(run.runId) || {};
  state.flowReconcile[run.runId] = { loading: true, at: prev.at || 0, byNode: prev.byNode || {} };
  renderFlow();
  const res = await guard('GitLab 突き合わせ', () => api.glReconcileRun({ repoUrl, nodes }));
  if (res === undefined) {
    state.flowReconcile[run.runId] = { loading: false, at: Date.now(), byNode: prev.byNode || {} };
    renderFlow();
    return;
  }
  if (!res.enabled) {
    state.flowReconcile[run.runId] = { loading: false, at: Date.now(), byNode: {} };
    if (!auto) toast('GitLab API が未設定です（⚙ 設定で Base URL とトークンを設定してください）');
    renderFlow();
    return;
  }
  const byNode = {};
  for (const rec of res.nodes || []) byNode[rec.id] = rec;
  state.flowReconcile[run.runId] = { loading: false, at: Date.now(), byNode };
  if (!auto) {
    const hits = (res.nodes || []).filter((n) => n.reconciled).length;
    const open = (res.nodes || []).filter((n) => !n.reconciled).length;
    toast(
      hits
        ? `クローズ済みイシューを ${hits} 件反映しました（完了/失敗）${open ? `／レビュー中 ${open} 件` : ''}`
        : open
          ? `レビュー中のイシュー ${open} 件を表示しました（未決着）`
          : '関連イシューは見つかりませんでした',
      hits > 0
    );
  }
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
  const deletable = run.status === 'done' || run.status === 'failed' || run.status === 'canceled' || run.alive === false;
  const deleteBtn = deletable
    ? `<button class="chip danger" id="flow-delete" title="この run のディレクトリ（runs/${esc(run.runId)}）をゴミ箱へ移動します">🗑 削除</button>`
    : '';
  // run のキャンセル（人の明示アクション＝唯一の hard-stop）。まだ終端していない run に出す。
  // 承認待ちで park 中の run も暴走中の run も止められる。起票済みイシューは残す（追跡だけやめる）。
  const cancelable = !['done', 'failed', 'canceled'].includes(run.status);
  const parkedCount = Object.values(run.nodes || {}).filter((n) => n.parked).length;
  const cancelBtn = cancelable
    ? `<button class="chip danger" id="flow-cancel" title="この run を canceled に終端化します（新規起票・承認待ちの監視・自動再開をすべて停止）。起票済みの GitLab イシューは残ります（追跡だけやめます）">■ キャンセル${parkedCount ? `（承認待ち ${parkedCount}）` : ''}</button>`
    : '';
  // gitlab executor 連動: 非終端ノードがあれば「GitLab と突き合わせ」で関連イシューの今の状態
  // （クローズ済み＝完了/失敗を先読み反映／オープン＝レビュー中を表示）を取り込める。run を開いた
  // ときに自動で一度走る（律速あり）ので、ボタンは手動の再取得（最新化）用。
  const hasOpenNodes = reconcilableNodes(run).length > 0;
  const rec = reconcileEntry(run.runId) || null;
  const recHits = rec ? Object.values(rec.byNode || {}).filter((r) => r.reconciled).length : 0;
  const reconcileBtn =
    hasOpenNodes && run.workspace && run.workspace.url
      ? `<button class="chip" id="flow-reconcile" ${rec && rec.loading ? 'disabled' : ''}
          title="実行中ノードの関連イシューの今の状態を GitLab から取得して反映します（クローズ済み＝完了/失敗の先読み、オープン＝レビュー中）">${
            rec && rec.loading ? '突き合わせ中…' : '⟳ GitLab 最新化'
          }${recHits ? `（反映 ${recHits}）` : ''}</button>`
      : '';
  // RUN 表示ペインを縦 3 分割する: 概要 / タスクグラフ / ノード情報。
  // それぞれ独立して縦スクロールできる（.flow-pane が overflow-y を持つ）ので、
  // グラフが縦に長くても概要やノード詳細を見失わない。
  return `
    <div id="flow-overview" class="flow-pane">
      <div class="flow-pane-title">概要</div>
      <div class="card full">
        <h3>RUN <span class="mono">${esc(run.runId)}</span> — ${statusChip(run.status)}${stalled} ${esc(strat)} ${reconcileBtn} ${resubmit} ${cancelBtn} ${deleteBtn}</h3>
        ${relationshipStrip({ run })}
        <div>${esc(run.request || '')}</div>
        ${run.inheritedFrom ? `<div class="muted" title="このリトライが引き継いだ先行 run">↩ 引き継ぎ元 <span class="mono">${esc(run.inheritedFrom)}</span></div>` : ''}
        ${heartbeat}
        ${run.failureReason ? `<div style="color:var(--red)">失敗理由: ${esc(run.failureReason)}</div>` : ''}
        <div class="row2" style="align-items:center;margin-top:6px">
          <div class="progress" style="flex:1"><div style="width:${pct}%"></div></div>
          <span class="muted">${run.counts.done + run.counts.failed}/${run.total} (${pct}%)</span>
        </div>
      </div>
      <div class="section-title">アクティビティ</div>
      <div class="events">${events || '<span class="muted">イベントなし</span>'}</div>
    </div>
    <div id="flow-graph" class="flow-pane">
      <div class="flow-pane-title">タスクグラフ</div>
      <div id="graph-box">${renderGraphSvg(run)}</div>
      <div class="legend">${legend}</div>
    </div>
    <div id="flow-node" class="flow-pane">
      <div class="flow-pane-title">ノード情報</div>
      ${nodeDetail || '<div class="empty">タスクグラフでノードを選択すると詳細を表示します</div>'}
    </div>`;
}

// ---------------------------------------------------------------------------
// ノード詳細（進捗・タイムライン・関連イシュー）
// ---------------------------------------------------------------------------

// ノードのタイムライン（events の claimed / result。新しい順で届く）
function nodeTimeline(nodeId) {
  return ((state.flowRun && state.flowRun.nodeEvents) || {})[nodeId] || [];
}

// park（承認待ち）ノードの説明行。承認待ちで保留中＝worker スロットを空けて監視主体が
// 定期確認していること、throttle（起票見送り）や人の作業検知を人に伝える。
function nodeParkLine(node) {
  if (!node.parked) return '';
  if (node.throttled) {
    return `<div class="muted" style="margin-top:4px">⏸ 起票見送り中（同時イシュー上限に達したため一時停止。枠が空くと自動で起票されます）</div>`;
  }
  const active = node.parkActiveSeen
    ? '人の作業（MR/ラベル）を検知済み — マージ待ち'
    : 'レビュー/MR 作成待ち';
  return `<div class="muted" style="margin-top:4px">⏳ 承認待ち（park）— worker スロットを解放し、監視主体が定期確認中。${active}</div>`;
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

// 関連 GitLab イシューのブロック。承認/却下は結果から、実行中は決定的タスクトークンで検索。
// GitLab と突き合わせ済み（クローズ反映）なら、その結果もイシュー情報の供給源にする。
function nodeIssueBlock(run, node) {
  const cached =
    state.flowNodeIssue && state.flowNodeIssue.token === node.taskToken
      ? state.flowNodeIssue
      : null;
  // 単発の「探す」で得た完全なイシュー、または run 一括の突き合わせ結果のどちらかを found とする
  const e = reconcileEntry(run.runId);
  const rec = e && e.byNode ? e.byNode[node.id] : null;
  const found = cached ? cached.issue : rec ? recToIssue(rec) : undefined;
  const reconciled = rec && rec.reconciled ? rec.reconciled : null; // 'done' | 'failed' | null
  const repoUrl = run.workspace && run.workspace.url;

  const rows = [];
  const url = node.issueUrl || (found && found.url);
  if (url) {
    const d = node.data && typeof node.data === 'object' ? node.data : {};
    const isRejected = node.rejected || reconciled === 'failed';
    const isApproved = !isRejected && (d.decision === 'approved' || reconciled === 'done');
    // イシュー状態のチップ: 却下→st-blocked ／ 承認→st-done ／ オープン（レビュー中）→st-review ／
    // それ以外の決着（bus の decision）→ st-done
    let chip = '';
    if (isRejected) chip = `<span class="status-chip st-blocked">却下</span>`;
    else if (isApproved) chip = `<span class="status-chip st-done">承認</span>`;
    else if (found && found.state === 'opened')
      chip = `<span class="status-chip st-review">レビュー中</span>`;
    else if (found && found.state === 'closed')
      chip = `<span class="status-chip st-closed">クローズ</span>`;
    else if (d.decision) chip = `<span class="status-chip st-done">${esc(d.decision)}</span>`;
    else if (node.parked) chip = `<span class="status-chip st-parked">承認待ち</span>`;
    rows.push(`<div class="row2" style="align-items:center;gap:8px">
      <a href="#" data-ext="${esc(url)}" class="mono">${esc(url)}</a> ${chip}
      <button data-review="${esc(url)}" title="gitlab-review-viewer で開く">レビューで開く</button>
      <button data-ext-btn="${esc(url)}" title="ブラウザで開く">↗</button>
    </div>`);
    // bus に result が来る前の先読み反映であることを明示する（bus が正・反映は暫定）
    if (reconciled && !TERMINAL_NODE_STATES.has(node.state)) {
      rows.push(
        `<div class="muted">GitLab でクローズ済み（${reconciled === 'done' ? '承認' : '却下'}）を先読み反映しました。bus の result 反映後に確定します。</div>`
      );
    }
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
        `<div class="muted">却下（未マージクローズ等）→ ノードは failed。kiro-project 管理下なら
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
  const reconciled = reconciledStateFor(run, node.id);
  const effState = reconciled || node.state;
  const stateLabel =
    esc(FLOW_STATE_LABEL[effState] || effState) +
    (reconciled ? ' <span class="status-chip st-reconciled" title="GitLab のクローズ済みイシューから先読み反映（bus 反映待ち）">GitLab 反映</span>' : '');
  return `<div class="card full">
      <h3><span class="mono">${esc(node.id)}</span> [${esc(node.kind)}] — ${stateLabel}${node.who ? ` @${esc(node.who)}` : ''}</h3>
      <div>${esc(node.goal)}</div>
      ${node.deps.length ? `<div class="muted" style="margin-top:4px">依存: ${node.deps.map(esc).join(', ')}</div>` : ''}
      ${nodeParkLine(node)}
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
    await gitPushBusOp(`kiro-projects-viewer: resubmit run ${run.runId}`);
    await reloadProject();
  }
}

// run をキャンセルする（人の明示アクション＝唯一の hard-stop）。承認待ちで park 中でも暴走中でも止まる。
async function cancelFlowRun() {
  const run = state.flowRun && state.flowRun.run;
  if (!run) return;
  const parked = Object.values(run.nodes || {}).filter((n) => n.parked).length;
  const note = parked
    ? `\n承認待ちで保留中のノードが ${parked} 件あります。監視を止めますが、起票済みの GitLab イシューは残します（追跡だけやめます。人がクローズできます）。`
    : '\n起票済みの GitLab イシューがあれば残します（追跡だけやめます）。';
  const yes = await confirmDialog(
    `run ${run.runId} をキャンセルします。\n新規起票・承認待ちの監視・自動再開をすべて停止し、status を canceled に確定します。${note}\nよろしいですか？`
  );
  if (!yes) return;
  const ok = await guard('run キャンセル', async () => {
    const res = await api.flowCancel(state.project.busDir, run.runId, 'kiro-projects-viewer から手動キャンセル');
    if (res && res.alreadyTerminal) {
      toast(`run は既に終端（${res.status}）でした。キャンセルは不要です。`, true);
    } else {
      toast(`run をキャンセルしました（承認待ち ${res ? res.cleared : 0} 件の監視を停止）: ${run.runId}`, true);
    }
    return true;
  });
  if (ok) {
    await gitPushBusOp(`kiro-projects-viewer: cancel run ${run.runId}`);
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
    await gitPushBusOp(`kiro-projects-viewer: delete run ${run.runId}`);
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
  return { done: '#3fb950', failed: '#f85149', claimed: '#4cc2b0', parked: '#d29922', pending: '#58a6ff', waiting: '#3a4048' }[st] || '#3a4048';
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
    // GitLab クローズ反映があれば表示上の状態はそちらを優先する（bus に result が届く前でも
    // 完了/失敗を映す）。反映で状態が変わったノードは reconciled クラスで区別できるようにする。
    const reconciled = reconciledStateFor(run, n.id);
    const effState = reconciled || n.state;
    const recClass = reconciled ? ' reconciled' : '';
    // gitlab executor で関連イシュー URL が確定済みのノード、または突き合わせで URL が判明した
    // ノード（クローズ済み/レビュー中どちらも）には、1 クリックでレビューを起動するイシュー
    // アイコンを右上に重ねる。レビュー中（オープン）は青系、却下は赤で色分けする。
    const recEntry = reconcileEntry(run.runId);
    const rec = recEntry && recEntry.byNode ? recEntry.byNode[n.id] : null;
    const issueUrl = n.issueUrl || (rec && rec.url) || '';
    // park 中（承認待ち）のノードは定義上オープンなイシューをレビュー待ちにしている＝突き合わせ前でも
    // レビュー中（青系）として表示する。throttled（起票見送り）はイシュー未作成なので対象外。
    const issueOpen =
      (rec && rec.issueState === 'opened' && !reconciled) || (n.parked && !n.throttled && !reconciled);
    const idMax = issueUrl ? 17 : 20; // アイコン分だけ id ラベルを詰める
    const idLabel = n.id.length > idMax ? `${n.id.slice(0, idMax - 1)}…` : n.id;
    const goal = n.goal.length > 24 ? `${n.goal.slice(0, 23)}…` : n.goal;
    const issueRejected = n.rejected || reconciled === 'failed';
    const issueCls = issueRejected ? ' rejected' : issueOpen ? ' review' : '';
    const issueTitle = issueOpen
      ? '関連 GitLab イシューはレビュー中（オープン）— クリックでレビューを開く'
      : '関連 GitLab イシューをレビューで開く（gitlab-review-viewer 起動）';
    const issueIcon = issueUrl
      ? `<g class="node-issue${issueCls}" data-issue-open="${esc(issueUrl)}" transform="translate(${NW - 22},4)">
          <title>${issueTitle}</title>
          <circle cx="9" cy="9" r="9"></circle>
          <text x="9" y="13" text-anchor="middle" class="node-issue-glyph">↗</text>
        </g>`
      : '';
    return `<g class="node state-${effState}${recClass} ${state.flowNodeId === n.id ? 'selected' : ''}" data-node="${esc(n.id)}" transform="translate(${x},${y})">
      <rect width="${NW}" height="${NH}" rx="6"></rect>
      <text x="8" y="17" class="mono">${esc(idLabel)}${n.who ? ` @${esc(n.who).slice(0, 8)}` : ''}</text>
      <text x="8" y="31">${esc(goal)}</text>
      <text x="8" y="42" class="kind">[${esc(n.kind)}]</text>
      ${issueIcon}
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
  // ノード右上のイシューアイコン: 1 クリックでレビュー（gitlab-review-viewer）を起動する。
  // ノード選択（詳細表示）より優先させるため伝播を止める。
  for (const g of root.querySelectorAll('.node-issue[data-issue-open]')) {
    g.addEventListener('click', (e) => {
      e.stopPropagation();
      guard('レビュー起動', async () => {
        const res = await api.openReview({ url: g.dataset.issueOpen });
        reviewToast(res.via);
      });
    });
  }
  const rs = root.querySelector('#flow-resubmit');
  if (rs) rs.addEventListener('click', () => resubmitFlowRun());
  const cn = root.querySelector('#flow-cancel');
  if (cn) cn.addEventListener('click', () => cancelFlowRun());
  const fd = root.querySelector('#flow-delete');
  if (fd) fd.addEventListener('click', () => deleteFlowRun());
  const rc = root.querySelector('#flow-reconcile');
  if (rc) rc.addEventListener('click', () => reconcileFlowRun());
  const fi = root.querySelector('#btn-find-issue');
  if (fi) fi.addEventListener('click', () => findNodeIssue(fi));
  for (const btn of root.querySelectorAll('#flow-detail button[data-review]')) {
    btn.addEventListener('click', () =>
      guard('レビュー起動', async () => {
        const res = await api.openReview({ url: btn.dataset.review });
        reviewToast(res.via);
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
  const tokenMap = flowNodeByToken(); // 追加コストなし（flowRuns は常にロード済み）

  // 関連 run セル: イシュー本文の task-token を、ロード済み flowRuns の各ノードが持つ
  // 決定的タスクトークンと突き合わせる。ヒットすれば run/ノードのチップを出し、
  // クリックでフロー画面のその run・ノードを直接開く（レビュー待ち→フローの導線）。
  const relatedRunCell = (it) => {
    const rel = it.taskToken ? tokenMap[it.taskToken] : null;
    if (rel) {
      return `<button class="linklike mono rel-run-chip st-${esc(rel.status)}"
        data-goto-run="${esc(rel.runId)}" data-goto-node="${esc(rel.nodeId)}"
        title="フロー画面で run ${esc(rel.runId)} のノード ${esc(rel.nodeId)} を開く">⚙ ${esc(shortRunId(rel.runId))} ▸ ${esc(rel.nodeId)}</button>`;
    }
    if (it.taskToken) {
      return `<span class="muted" title="task-token ${esc(it.taskToken)}｜対応する run はロード済みの一覧（最新 30 件）に無いか、bus 掃除済みです">—</span>`;
    }
    return '<span class="muted" title="kiro-flow 由来ではないイシュー（task-token なし）"></span>';
  };

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
      <td>${relatedRunCell(it)}</td>
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
    ? `<table class="list"><tr><th>IID</th><th>イシュー</th><th>状態</th><th>ラベル</th><th>関連 MR</th><th>関連 run</th><th></th></tr>
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
      <span class="muted">repos のオープンイシュー（レビュー待ち・作業中）。「関連 run」列から起票元の run/ノードをフロー画面で開けます</span>
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
  for (const btn of el.querySelectorAll('button[data-goto-run]')) {
    btn.addEventListener('click', () => gotoRunNode(btn.dataset.gotoRun, btn.dataset.gotoNode || null));
  }
  for (const btn of el.querySelectorAll('button[data-review]')) {
    btn.addEventListener('click', () =>
      guard('レビュー起動', async () => {
        const res = await api.openReview({ url: btn.dataset.review });
        reviewToast(res.via);
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
  $('cfg-kiro-command').value = (cfg.kiro && cfg.kiro.command) || 'kiro-project';
  $('cfg-action-mode').value = (cfg.kiro && cfg.kiro.actionMode) || 'auto';
  $('cfg-flow-bus').value = (cfg.kiro && cfg.kiro.flowBus) || '';
  $('cfg-flow-lockdir').value = (cfg.kiro && cfg.kiro.flowLockDir) || '';
  $('cfg-flow-bus-by-project').value = Object.entries(
    (cfg.kiro && cfg.kiro.flowBusByProject) || {}
  )
    .map(([name, bus]) => `${name} = ${bus}`)
    .join('\n');
  $('cfg-gl-url').value = cfg.gitlab.baseUrl || '';
  $('cfg-gl-token').value = cfg.gitlab.token || '';
  $('cfg-rv-mode').value = cfg.reviewViewer.mode || 'protocol';
  $('cfg-rv-exepath').value = cfg.reviewViewer.exePath || '';
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
  cfg.kiro.command = $('cfg-kiro-command').value.trim() || 'kiro-project';
  cfg.kiro.actionMode = $('cfg-action-mode').value;
  cfg.kiro.flowBus = $('cfg-flow-bus').value.trim();
  cfg.kiro.flowLockDir = $('cfg-flow-lockdir').value.trim();
  // 1 行 1 件「プロジェクト名 = バスパス」を写像へ。空行・不正行は無視する。
  cfg.kiro.flowBusByProject = $('cfg-flow-bus-by-project')
    .value.split('\n')
    .map((line) => {
      const i = line.indexOf('=');
      if (i < 0) return null;
      const name = line.slice(0, i).trim();
      const bus = line.slice(i + 1).trim();
      return name && bus ? [name, bus] : null;
    })
    .filter(Boolean)
    .reduce((acc, [name, bus]) => ((acc[name] = bus), acc), {});
  cfg.gitlab.baseUrl = $('cfg-gl-url').value.trim();
  cfg.gitlab.token = $('cfg-gl-token').value.trim();
  cfg.reviewViewer.mode = $('cfg-rv-mode').value;
  cfg.reviewViewer.exePath = $('cfg-rv-exepath').value.trim();
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

// commitPush が notRepo（＝そのディレクトリが git 作業ツリーでない）で「黙ってスキップ」した
// ことを、ディレクトリごとに一度だけ知らせる（操作のたびに出すと煩いのでセッション内で重複排除）。
// バックログ修正・タスク操作・needs 記入・run 削除など、gitAutoPush 有効なのに反映されない全操作が
// 対象。ローカル daemon バス（<project>/bus）や、本体の state_git が「作業ディレクトリ→別クローン」
// 方式で同期する構成では作業ディレクトリ自体が git リポジトリでないため、viewer からは直接 push
// できず daemon 側の state_git 同期に委ねられる。git クローン上で viewer を動かせば直接反映される。
const _pushSkipWarned = new Set();
function warnPushSkipped(dir, kind) {
  if (!dir || _pushSkipWarned.has(dir)) return;
  _pushSkipWarned.add(dir);
  const hint =
    kind === 'bus'
      ? '（viewer から直接反映するには ⚙ 設定 flowBusByProject でバスの git クローンを登録してください）'
      : '（viewer から直接反映するには、状態共有リポジトリの git クローン上でプロジェクトを開いてください）';
  toast(
    `「${dir}」は git 作業ツリーではないため、変更を共有リポジトリへ直接 push できませんでした。` +
      `kiro-project / kiro-flow daemon の state_git 同期に反映が委ねられます${hint}。` +
      `（この通知はディレクトリごとに一度だけ出ます）`
  );
}

// 管理ファイルを書き換えた操作（指示ドロップ・inbox 投入・needs 記入・削除など）の後に呼ぶ。
// 設定 gitAutoPush が有効なら、操作したディレクトリの変更をコミットして push する
// （状態共有 git への都度反映）。書き込み本体は成功済みなので待たずに走らせ、失敗（push 不可）や
// notRepo による「黙ってスキップ」だけトーストで知らせる（後者はディレクトリごとに一度だけ）。
// 戻り値は commitPush の結果 Promise（gitAutoPush 無効/対象なしのときは null）。
// opts.kind は notRepo 通知の対処ヒント切り替え用（'bus'（バス）／既定 'project'）。
function gitPushAfterWrite(message, dir, opts) {
  const cfg = state.config;
  if (!cfg || !cfg.kiro || !cfg.kiro.gitAutoPush) return null;
  const target = dir || state.selectedDir;
  if (!target) return null;
  const kind = (opts && opts.kind) || 'project';
  return api
    .gitCommitPush(target, message)
    .then((res) => {
      if (res && res.skipped && res.notRepo) warnPushSkipped(target, kind);
      return res;
    })
    .catch((err) => {
      toast(`git 同期（プッシュ）: ${err.message || err}`);
      return null;
    });
}

// バス操作（run の削除・再投入）の git 反映。バスは kiro-project の state_git から除外され
// （_STATE_EXCLUDE_DIRS）、kiro-flow 側の state_git が別クローンへ同期するため、busDir が git
// 作業ツリーでないと notRepo で黙ってスキップされる。notRepo 通知は gitPushAfterWrite が
// バス向けのヒント付きで出す（ここは busDir を対象にするだけ）。
function gitPushBusOp(message) {
  const busDir = state.project && state.project.busDir;
  return gitPushAfterWrite(message, busDir, { kind: 'bus' });
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
      if (
        $('dlg-settings').open ||
        $('dlg-task').open ||
        $('dlg-enqueue').open ||
        $('dlg-confirm').open ||
        $('dlg-new-project').open ||
        $('dlg-edit-file').open
      )
        return;
      const ae = document.activeElement;
      if (ae && (ae.tagName === 'TEXTAREA' || ae.tagName === 'INPUT')) return;
      const typed = [...document.querySelectorAll('#content .need-input')].some((t) => t.value.trim());
      if (typed) return;
      refreshAll();
    }, sec * 1000);
  }
}

// ディープリンク: kiro-projects-viewer://open?root=<プロジェクトルート>（旧 project= も名前一致で受ける）
function handleOpenTarget({ url }) {
  guard('ディープリンク', async () => {
    const u = new URL(url);
    const root = u.searchParams.get('root');
    const name = u.searchParams.get('project');
    await refreshDiscovery();
    const p =
      (root && state.discovery.projects.find((x) => x.dir === root)) ||
      (name && state.discovery.projects.find((x) => x.name === name)) ||
      null;
    if (p) {
      await selectProject(p.dir);
      return;
    }
    toast(`プロジェクトが見つかりません: ${name || root || ''}`);
  });
}

// ---------------------------------------------------------------------------
// 起動
// ---------------------------------------------------------------------------

// かんたん/メンテナンス表示の切り替え。body.simple で技術タブ（バックログ/フロー/
// レビュー待ち/履歴）を隠し、概要をかんたん表示に切り替える。設定に永続化する。
function applyMode() {
  document.body.classList.toggle('simple', !!state.simpleMode);
  const btn = $('btn-mode');
  if (btn) {
    btn.textContent = state.simpleMode ? '🔧' : '👀';
    btn.title = state.simpleMode
      ? 'メンテナンス表示へ切り替え（全タブ・技術情報）'
      : 'かんたん表示へ切り替え（概要と要対応だけに絞る）';
  }
  if (state.simpleMode && !SIMPLE_TABS.has(activeTab())) switchTab('overview');
  renderAllTabs();
}

async function toggleMode() {
  state.simpleMode = !state.simpleMode;
  applyMode();
  const cfg = state.config;
  cfg.kiro = cfg.kiro || {};
  cfg.kiro.simpleMode = state.simpleMode;
  state.config = await api.saveConfig(cfg);
}

async function init() {
  state.config = await guard('設定読込', () => api.getConfig());
  state.simpleMode = !!(state.config && state.config.kiro && state.config.kiro.simpleMode);
  initTabs();
  applyMode();
  $('btn-mode').addEventListener('click', toggleMode);
  $('btn-refresh').addEventListener('click', refreshAll);
  $('btn-git-pull').addEventListener('click', manualGitPull);
  $('btn-settings').addEventListener('click', openSettings);
  $('btn-save-settings').addEventListener('click', () => saveSettings());
  $('btn-task-close').addEventListener('click', () => $('dlg-task').close());
  $('btn-enq-cancel').addEventListener('click', () => $('dlg-enqueue').close());
  $('btn-enq-submit').addEventListener('click', submitEnqueue);
  // 新規プロジェクト作成
  $('btn-new-project').addEventListener('click', openNewProject);
  $('btn-np-cancel').addEventListener('click', () => $('dlg-new-project').close());
  $('btn-np-submit').addEventListener('click', submitNewProject);
  $('np-add-repo').addEventListener('click', () => addRepoRow());
  // プロジェクトファイル編集
  $('btn-ef-cancel').addEventListener('click', () => $('dlg-edit-file').close());
  $('btn-ef-save').addEventListener('click', saveEditFile);
  $('btn-ef-open').addEventListener('click', () => {
    if (state.editFile) guard('ファイルを開く', () => api.openPath(state.editFile.file));
  });
  api.onOpenTarget(handleOpenTarget);

  await refreshDiscovery();
  const last = localStorage.getItem('kpv:selected');
  const all = state.discovery.projects;
  const target = all.find((p) => p.dir === last) || all[0];
  if (target) await selectProject(target.dir);
  else renderAllTabs();
  setupPolling();
}

init();
