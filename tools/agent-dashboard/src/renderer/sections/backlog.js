'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// タブ: バックログ
// ---------------------------------------------------------------------------

const BACKLOG_FILTERS = [
  ['active', '未完了'],
  ['ready', '実行待ち'],
  ['doing', '実行中'],
  ['offloaded', '実行中（委任）'],
  ['review', '検収待ち'],
  ['blocked', '要対応'],
  ['inbox', '受付待ち'],
  ['draft', '下書き'],
  ['archive', '完了（履歴）'],
];

// ---------------------------------------------------------------------------
// 関係性（charter → backlog → run → issue）の突き合わせと画面遷移
//   run-id `req-<hash>-<taskid>-r<retries>` を鍵に、バックログのタスク（安定オブジェクト）と
//   その agent-flow run（リトライ系統）を結ぶ。リトライは「意味的に同一」なので系統でまとめる。
// ---------------------------------------------------------------------------

// agent-project の run-id 生成（_submit_req_id）と同じ task.id 正規化。バックログの task.id を
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
    ? `\n影響を受けるタスク（このタスクに依存）: ${downs.map((t) => `${t.id}[${statusLabel(t.status)}]`).join(', ')}\n` +
      'これらのタスクは計画の再確認（承認待ち）に戻します。'
    : '\nこのタスクに依存するタスクはありません。';
  return (
    `${id} を却下します（${what}）。\n` +
    'タスクは廃止されて履歴に残り、同種のタスクを避ける学習も記録されます。憲章があれば計画の作り直しを依頼します。' +
    impact +
    '\nよろしいですか？'
  );
}

function sanitizeTaskId(id) {
  return String(id == null ? '' : id)
    .replace(/[^\w.-]+/g, '_')
    .slice(0, 60);
}

// あるバックログタスクに紐づく agent-flow run を、リトライ世代の新しい順で返す。
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
function switchTab(name) {
  document
    .querySelectorAll('.tab')
    .forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tabpane').forEach((pane) => pane.classList.remove('active'));
  const pane = $(`tab-${name}`);
  if (pane) pane.classList.add('active');
  if (name === 'needs') refreshGitLab(false); // 要対応タブに GitLab レビュー待ちを併載しているため
  if (featureTabs.has(name)) renderFeatureTab(name); // 登録済みフィーチャータブは遷移時に描画
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
    state.flowDetailView = 'graph';
    state.flowMobileDetail = true;
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

// レビュー待ちイシュー（本文の task-token）→ 起票した agent-flow run/ノードの索引。
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
  else toast(`タスク ${taskId} は現在の一覧に見つかりません（完了済みか削除済みの可能性があります）`);
}

// run 1 件を表す小さなクリップ（リトライ世代＋状態色）。クリックで run へ遷移。
function runPill(r, current = false) {
  const gen = r.retries != null ? `r${r.retries}` : 'run';
  const rev = r.rev ? `·v${r.rev}` : '';
  return `<button class="rel-pill st-${esc(r.status)}${current ? ' current' : ''}"
    data-goto-run="${esc(r.runId)}" title="${esc(r.runId)} — ${esc(statusLabel(r.status))}">${gen}${rev}</button>`;
}

// 関係性のパンくず: charter ▸ task ▸ run(系統) ▸ issue。各セグメントはクリックで該当画面へ。
function relationshipStrip({ taskId, run } = {}) {
  const p = state.project;
  const segs = [];
  if (p && p.charter && p.charter.name) {
    segs.push(`<span class="rel-seg charter" title="プロジェクト憲章">🎯 ${esc(p.charter.name)}</span>`);
  }
  const tid = taskId || (run && run.taskId);
  if (tid) {
    segs.push(
      `<button class="rel-seg task" data-goto-task="${esc(tid)}" title="元のタスクを開く">🗒 ${esc(tid)}</button>`
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
function relatedRunsBlock(taskId, { archived = false } = {}) {
  const rr = runsForTask(taskId);
  if (!rr.length) return '';
  const items = rr
    .map((r) => {
      const cap = runStatusCaption(r.status, { taskArchived: archived });
      const chipCls = String(r.status) === 'done' && !archived ? 'st-review' : '';
      return `<div class="rel-run-row">
        <button class="linklike mono" data-goto-run="${esc(r.runId)}">${esc(r.runId)}</button>
        <span class="status-chip ${chipCls || `st-${esc(r.status)}`}" title="${esc(statusLabel(r.status))}">${esc(cap)}</span>
        <span class="muted">${r.total} 工程中 完了 ${r.counts.done}・失敗 ${r.counts.failed}</span>
        ${r.inheritedFrom ? `<span class="muted" title="引き継ぎ元の実行">↩ ${esc(r.inheritedFrom)}</span>` : ''}
      </div>`;
    })
    .join('');
  return `<div class="section-title">関連する実行（やり直し履歴）</div>
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

// パイプラインリボン: 概念フロー（計画 → Spec → 実装 → 承認 → 完了）上のタスクの現在地。
// 既存 status＋タグからの純粋な写像で、新しい状態は導入しない。
// Spec 段は spec ルーティング運用時（該当タスクか specs/ 成果物があるとき）だけ現れる。
function taskPipelineStage(t) {
  const ex = t.extra || {};
  if (ex.spec_for) return 'spec'; // spec 作成タスク
  if (ex.route === 'spec' && !ex.spec_expanded) return 'spec'; // spec の決着待ちの元タスク
  if (['inbox', 'draft', 'proposed'].includes(t.status)) return 'plan';
  if (['review', 'blocked'].includes(t.status)) return 'approve';
  return 'implement'; // ready / doing / offloaded
}

const PIPELINE_STAGES = [
  ['plan', '計画', '取り込み・実行前レビュー待ち（inbox / proposed）'],
  ['spec', 'Spec', 'spec 前段の作成・承認待ち（specs/<id>/ の spec / design / tasks）'],
  ['implement', '実装', '実行待ち・実行中（ready / doing / 委任先で実行中）'],
  ['approve', '承認', 'あなたの確認待ち（検収・判断待ち）'],
  ['done', '完了', '納品済み（アーカイブ）'],
];

function pipelineRibbonHtml(p) {
  const counts = { plan: 0, spec: 0, implement: 0, approve: 0, done: p.archive.length };
  for (const t of p.backlog) counts[taskPipelineStage(t)]++;
  const hasSpec = counts.spec > 0 || (p.specs || []).length > 0;
  const cells = PIPELINE_STAGES.filter(([k]) => k !== 'spec' || hasSpec)
    .map(
      ([k, label, tip]) =>
        `<span class="pipe-stage ${counts[k] ? 'on' : ''} pipe-${k}" title="${esc(tip)}">${esc(label)}<span class="pipe-count">${counts[k]}</span></span>`
    )
    .join('<span class="pipe-arrow">→</span>');
  return `<div class="pipeline">${cells}</div>`;
}

function taskListItemViewModel(task, hint) {
  const priority = Number(task.priority) || 0;
  const priorityLevel = priority >= 8 ? '高' : priority >= 4 ? '中' : '低';
  return {
    id: String(task.id || ''),
    title: String(task.title || '名称未設定のタスク'),
    status: String(task.status || 'unknown'),
    statusText: statusLabel(task.status || 'unknown'),
    priority,
    priorityText: `${priorityLevel} ${priority}`,
    nextAction: String((hint && hint.completeHow) || '詳細を確認してください'),
  };
}

function taskListItemHtml(item, scope) {
  return `<button type="button" class="task-list-item" data-task="${esc(item.id)}" data-scope="${esc(scope)}" role="listitem" aria-label="${esc(item.title)}の詳細を開く">
    <span class="task-list-status" data-label="状態" aria-label="状態 ${esc(item.statusText)}">${statusChip(item.status)}</span>
    <span class="task-list-title" data-label="タスク">${esc(item.title)}</span>
    <span class="task-list-priority" data-label="優先度" aria-label="優先度 ${esc(item.priorityText)}">${esc(item.priorityText)}</span>
    <span class="task-list-next" data-label="次の行動">${esc(item.nextAction)}</span>
    <svg class="task-list-chevron" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m9 18 6-6-6-6" /></svg>
  </button>`;
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
      `<button class="chip ${state.backlogFilter === key ? 'active' : ''}" data-filter="${key}" aria-pressed="${state.backlogFilter === key}">${label}</button>`
  ).join('');

  let tasks;
  if (state.backlogFilter === 'archive') tasks = p.archive;
  else if (state.backlogFilter === 'active') tasks = p.backlog;
  else tasks = p.backlog.filter((t) => t.status === state.backlogFilter);

  // 複数 charter 運用: charter（バージョン）でさらに絞り込む。
  // 「初版」チップはタグ無し（charter.md 由来）のタスクに絞る（'__initial__' は表示専用の番兵値）。
  const charterNames = (p.charters || []).map((c) => c.name);
  if (charterNames.length && state.backlogCharter) {
    tasks =
      state.backlogCharter === '__initial__'
        ? tasks.filter((t) => !(t.extra.charter || '').trim())
        : tasks.filter((t) => (t.extra.charter || '') === state.backlogCharter);
  }
  const charterChipDefs = charterNames.length
    ? [['', '全部'], ...(p.charter ? [['__initial__', '初版']] : []), ...charterNames.map((n) => [n, n])]
    : [];
  const charterChips = charterChipDefs.length
    ? `<span class="muted" style="margin-left:8px">バージョン:</span>` +
      charterChipDefs
        .map(
          ([v, label]) =>
            `<button class="chip ${((state.backlogCharter || '') === v) ? 'active' : ''}" data-charter-filter="${esc(v)}" aria-pressed="${((state.backlogCharter || '') === v)}">${esc(label)}</button>`
        )
        .join('')
    : '';

  // priority 降順 → 古い順（planner none と同じ感覚）
  tasks = [...tasks].sort((a, b) => b.priority - a.priority || a.mtime - b.mtime);

  const taskItems = tasks
    .map((t) => {
      const rr = runsForTask(t.id); // 紐づく agent-flow run（リトライ系統）
      const hint =
        state.backlogFilter === 'archive'
          ? taskCompletionHint(t, { runs: rr, archived: true })
          : taskCompletionHint(t, { runs: rr });
      return taskListItemHtml(
        taskListItemViewModel(t, hint),
        state.backlogFilter === 'archive' ? 'archive' : 'backlog'
      );
    })
    .join('');

  const replanPending = !!p.replanPending;
  el.innerHTML = `
    ${pipelineRibbonHtml(p)}
    <div class="task-toolbar">
      <div class="task-toolbar-filters">
        <div class="filters" aria-label="タスクの状態で絞り込む">${chips}<span class="task-count">${tasks.length} 件</span>
          ${p.inboxFiles && p.inboxFiles.length ? `<span class="badge info" title="追加したタスクは次の実行サイクルで一覧に載ります">追加待ち ${p.inboxFiles.length}</span>` : ''}
          ${replanPending ? '<span class="badge info" title="計画の作り直しを依頼済みです。次の実行で反映されます">再計画 反映待ち</span>' : ''}
        </div>
        ${charterChips ? `<div class="filters task-version-filters" aria-label="計画バージョンで絞り込む">${charterChips}</div>` : ''}
      </div>
      <div class="task-toolbar-actions">
        <button id="btn-replan"${replanPending ? ' disabled' : ''} title="プロジェクト憲章からタスクを作り直します">計画を作り直す</button>
        <button id="btn-enqueue" class="primary-inline" title="タスクを1件追加します">タスクを追加</button>
      </div>
    </div>
    ${
      taskItems
        ? `<div class="task-list-grid" role="list" aria-label="タスク一覧">
            <div class="task-list-header" aria-hidden="true">
              <span>状態</span><span>タスク</span><span>優先度</span><span>次の行動</span><span></span>
            </div>
            <div class="task-list-items">${taskItems}</div>
          </div>`
        : '<div class="empty task-list-empty">この条件に一致するタスクはありません</div>'
    }`;

  $('btn-enqueue').addEventListener('click', () => openEnqueueDialog());
  const replanBtn = $('btn-replan');
  if (replanBtn && !replanPending) replanBtn.addEventListener('click', openReplanDialog);

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
  for (const row of el.querySelectorAll('.task-list-item[data-task]')) {
    row.addEventListener('click', () => showTaskDialog(row.dataset.task, row.dataset.scope));
  }
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
    return `<div class="muted" style="margin-top:8px">✎ 修正指示を送信済みです（反映されると再度編集できます）</div>`;
  }
  const doingNote =
    t.status === 'doing'
      ? '<div class="muted">実行中のタスクです。送信すると現在の作業を打ち切り、修正内容と指示でやり直します（早い軌道修正に使えます）。</div>'
      : t.status === 'offloaded'
        ? '<div class="muted">委任先で実行中のタスクです。送信すると今回の結果は採用されず、修正を反映してやり直します（切り替えは今回の作業が終わり次第）。</div>'
        : '<div class="muted">修正は次の実行から反映されます。依存関係を変えると作業の順序も変わります。</div>';
  return `<details class="revise-area"><summary>✎ 修正を指示</summary>
    ${doingNote}
    <div class="field"><label>作業への指示（次の実行に必ず伝わります）</label>
      <textarea rows="2" id="rv-feedback" placeholder="例: e2e はローカルサーバでなく実サーバに配備して実施すること"></textarea></div>
    <div class="field"><label>タイトル</label><input id="rv-title" value="${esc(t.title)}" /></div>
    <div class="row2">
      <div class="field"><label>優先度（数字が大きいほど先に着手）</label><input id="rv-priority" type="number" step="1" value="${t.priority}" /></div>
      <div class="field"><label>先行タスク（このタスクより先に終えるべき ID。カンマ区切り。空にすると解除）</label><input id="rv-after" class="mono" value="${esc(t.extra.after || '')}" /></div>
    </div>
    <div class="field"><label>検証コマンド（完了判定に使うコマンド。空にすると削除）</label><input id="rv-verify" class="mono" value="${esc(t.verify || '')}" /></div>
    <div class="field"><label>完了条件（文章で。検証コマンドが書けないとき。空にすると削除）</label><input id="rv-accept" value="${esc(t.extra.accept || '')}" /></div>
    <div class="row2">
      <div class="field"><label>自動化レベル（report=報告のみ / assisted=確認しながら / unattended=全自動。空にすると削除）</label>
        <input id="rv-level" list="rv-level-list" value="${esc(t.extra.level || '')}" />
        <datalist id="rv-level-list"><option value="report"></option><option value="assisted"></option><option value="unattended"></option></datalist>
      </div>
      <div class="field"><label>系列（同種タスクのグループ名。空にすると削除）</label><input id="rv-track" value="${esc(t.extra.track || '')}" /></div>
    </div>
    <div class="field"><label>メモ（空にすると削除）</label><input id="rv-note" value="${esc(t.extra.note || '')}" /></div>
    <details class="revise-guide" ${GUIDE_KEYS.some((k) => t.extra[k]) ? 'open' : ''}>
      <summary>意図と境界（レビュー材料 兼 実行ワーカーへの誘導。空にすると削除）</summary>
      <div class="row need-buttons">
        <span class="muted">改行は ⏎ で書きます。AI がタスクと憲章から下書きできます（送信前に人が確認）</span>
        <span class="spacer"></span>
        <button type="button" id="btn-guide-assist">✦ AI で補完</button>
      </div>
      <div class="muted" id="guide-assist-status"></div>
      ${GUIDE_KEYS.map(
        (k) =>
          `<div class="field"><label>${esc(GUIDE_LABELS[k])}</label><input id="rv-${k}" value="${esc(t.extra[k] || '')}" /></div>`
      ).join('')}
    </details>
    <div class="row need-buttons">
      <span class="muted">変更した項目と指示だけが送られ、決定記録に残ります</span>
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
      let cell;
      if (k === 'flow_run' && String(v).trim()) {
        cell = `<button class="linklike mono" data-goto-run="${esc(String(v).trim())}" title="実行中の作業を開く">${esc(v)}</button>`;
      } else if (PROSE_EXTRA_KEYS.has(k)) {
        // ⏎ は「1 行 = 1 フィールド」規約の改行マーカー（feedback/note/誘導記述で共通）→ 表示は改行に戻す
        cell = `<div class="task-prose">${proseHtml(String(v).replace(/\s*⏎\s*/g, '\n'))}</div>`;
      } else {
        cell = `<pre class="mono">${esc(v)}</pre>`;
      }
      return `<tr><th>${esc(k)}</th><td>${cell}</td></tr>`;
    })
    .join('');
  // 決定記録を残す人の操作（backlog のタスクのみ。archive は閲覧のみ）
  const canApprove = ['blocked', 'review', 'proposed'].includes(t.status);
  const deps = String(t.extra.after || '').trim();
  const downs = dependentsOf(p.backlog, t.id);
  const depRow = `<tr><th>依存関係</th><td class="muted">先行タスク: ${deps ? esc(deps) : '（なし）'} ／ 後続タスク（このタスクの変更が影響）: ${
    downs.length ? downs.map((x) => `${esc(x.id)}[${esc(statusLabel(x.status))}]`).join(', ') : '（なし）'
  }</td></tr>`;
  const rr = runsForTask(t.id);
  const hint = taskCompletionHint(t, { runs: rr, archived: scope === 'archive' });
  const statusCell = hint.statusNote
    ? `${statusChip(t.status)} <span class="badge warn" title="${esc(hint.completeHow)}">${esc(hint.statusNote)}</span>`
    : statusChip(t.status);
  // 削除を拒むのは「実行中」だけ。クレームロックは worker クラッシュや
  // review/blocked 滞留で残骸が残るため、doing 以外ではロックがあっても削除できる
  const claimed = p.claims.includes(t.id) && t.status === 'doing';
  const actionArea =
    scope === 'archive'
      ? `<div class="need-actions">
          <div class="row need-buttons">
            <span class="muted">完了（履歴）のタスクです。内容を編集して新しいタスクとしてやり直せます（履歴は残ります）。</span>
            <span class="spacer"></span>
            <button class="primary-inline" id="btn-task-reinject" title="このタスクの内容を編集して、新しいタスクとして追加し直します">↻ 編集してやり直す</button>
          </div>
        </div>`
      : `<div class="need-actions">
          <div class="task-complete-banner">${esc(hint.completeHow)}</div>
          <textarea rows="2" id="task-reason" class="need-input" placeholder="操作の理由（決定記録に残ります）"></textarea>
          <div class="row need-buttons">
            ${canApprove ? `<button class="primary-inline" data-taskact="approve">✓ 承認</button>` : ''}
            ${t.status === 'doing' ? '' : `<button class="danger" data-taskact="reject" data-confirm-reject="1" title="タスクを廃止します。依存するタスクは計画の再確認に戻り、憲章があれば計画の作り直しを依頼します">✕ 却下</button>`}
            <button data-taskact="pin" title="他より先に着手させます">▲ 最優先にする</button>
            <button data-taskact="defer" title="優先度を下げて後に回します">▽ 後回しにする</button>
            <button data-taskact="hold" title="実行を止めて保留にします（再開には承認が必要）">⏸ 保留にする</button>
            <span class="spacer"></span>
            <button class="danger" id="btn-task-delete" ${claimed ? 'disabled' : ''}
              title="${claimed ? '実行中のタスクは削除できません' : 'タスクをゴミ箱へ移動します（決定記録は残りません）'}">🗑 削除</button>
          </div>
        </div>`;
  $('dlg-task-title').innerHTML = `<span class="mono">${esc(t.id)}</span>: ${esc(t.title)}`;
  $('dlg-task-body').innerHTML = `
    ${relationshipStrip({ taskId: t.id })}
    <table class="list">
      <tr><th>状態</th><td>${statusCell}</td></tr>
      <tr><th>完了まで</th><td class="task-complete-how">${esc(hint.completeHow)}</td></tr>
      <tr><th>出自</th><td>${esc(t.source)}</td></tr>
      <tr><th>優先度</th><td>${t.priority}</td></tr>
      <tr><th>再試行</th><td>${t.retries}</td></tr>
      <tr><th>検証コマンド</th><td>${t.verify ? `<pre class="mono">${esc(t.verify)}</pre>` : '<span class="muted">（未定義）</span>'}</td></tr>
      ${depRow}
      ${extraRows}
      <tr><th>ファイル</th><td><a href="#" id="task-open-file" class="mono">${esc(t.file)}</a></td></tr>
    </table>
    ${relatedRunsBlock(t.id, { archived: scope === 'archive' })}
    ${actionArea}
    ${scope === 'archive' ? '' : reviseAreaHtml(t)}`;
  bindRelationship($('dlg-task-body')); // パンくず・関連 run のクリック配線
  const link = $('task-open-file');
  if (link) link.addEventListener('click', (e) => {
    e.preventDefault();
    guard('ファイルを開く', () => api.openPath(t.file));
  });
  const TASK_ACT_DONE = {
    approve: '承認を送信しました',
    reject: '却下を送信しました',
    pin: '最優先に設定しました',
    defer: '後回しに設定しました',
    hold: '保留にしました',
  };
  for (const btn of document.querySelectorAll('#dlg-task-body button[data-taskact]')) {
    btn.addEventListener('click', async () => {
      const reason = $('task-reason') ? $('task-reason').value.trim() : '';
      if (btn.dataset.confirmReject) {
        if (!reason) return toast('却下には理由の記入が必要です（決定記録に残ります）');
        const yes = await confirmDialog(rejectConfirmMessage(p, t.id, '廃止して計画を作り直す'));
        if (!yes) return;
      }
      const ok = await guard('操作', async () => {
        const res = await api.runAction({ dir: p.dir, action: btn.dataset.taskact, id: t.id, reason });
        uiLog('taskAction', btn.dataset.taskact, t.id, res);
        toast(`${TASK_ACT_DONE[btn.dataset.taskact] || '操作しました'}（反映まで少し時間がかかることがあります）`, true);
        return true;
      });
      if (ok) {
        gitPushAfterWrite(`agent-dashboard: ${btn.dataset.taskact} ${t.id}`, p.dir);
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
        ...GUIDE_KEYS.map((k) => [k, $(`rv-${k}`).value.trim(), String(t.extra[k] || '')]),
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
      const ok = await guard('修正の指示', async () => {
        const res = await api.runAction({ dir: p.dir, action: 'revise', id: t.id, reason, fields, feedback });
        markReviseSent(t);
        uiLog('revise', t.id, res);
        toast(`${t.id} の修正指示を送信しました（次の実行で反映されます）`, true);
        return true;
      });
      if (ok) {
        gitPushAfterWrite(`agent-dashboard: revise ${t.id}`, p.dir);
        $('dlg-task').close();
        await reloadProject();
      }
    });
  }
  // 意図と境界（誘導・レビュー記述）の AI 補完。読み取り専用の提案を入力欄へ流し込むだけで、
  // 送信（revise）は従来どおり人が確認して行う（enqueue-assist と同じ人確認前提の契約）
  const gaBtn = $('btn-guide-assist');
  if (gaBtn) {
    gaBtn.addEventListener('click', async () => {
      if (state.assistBusy) return;
      state.assistBusy = true;
      gaBtn.disabled = true;
      const status = $('guide-assist-status');
      status.textContent = '意図と境界の記述を補完しています…';
      try {
        const current = {};
        for (const k of GUIDE_KEYS) current[k] = $(`rv-${k}`).value.trim();
        const res = await api.agentTaskAssist({
          dir: p.dir,
          mode: 'task-guide',
          context: {
            charter: charterAssistContext(p),
            backlog: backlogAssistRows(p),
            task: {
              id: t.id,
              title: $('rv-title').value.trim() || t.title,
              verify: $('rv-verify').value.trim() || t.verify || '',
              accept: $('rv-accept').value.trim(),
              note: $('rv-note').value.trim(),
              ...current,
            },
          },
        });
        const f = res.fields || {};
        let filled = 0;
        for (const k of GUIDE_KEYS) {
          const v = String(f[k] || '').trim();
          if (v && v !== current[k]) {
            $(`rv-${k}`).value = v;
            filled += 1;
          }
        }
        status.textContent = filled
          ? `${filled} 項目を補完しました（${res.cli}${res.model ? ` / ${res.model}` : ''}）` +
            (f.rationale ? ` — ${f.rationale}` : '') +
            '。内容を確認・修正してから「修正を送信」してください'
          : '補完できる項目はありませんでした（根拠を読み取れた項目だけ提案されます）';
      } catch (err) {
        status.textContent = '';
        toast(`意図と境界の補完に失敗しました: ${err.message || err}`);
      } finally {
        state.assistBusy = false;
        gaBtn.disabled = false;
      }
    });
  }
  // 削除（人の明示アクション）。agent-project に削除の公式契約は無いため、
  // backlog/<id>.md をゴミ箱へ移動する。実行中（クレーム中）は main 側でも拒否される
  const delBtn = $('btn-task-delete');
  if (delBtn) {
    delBtn.addEventListener('click', async () => {
      const yes = await confirmDialog(
        `タスク ${t.id}「${t.title}」を削除します。\n` +
          'タスクはゴミ箱へ移動します（決定記録は残りません）。\n' +
          '一時的に止めたいだけなら「⏸ 保留にする」を使ってください。よろしいですか？'
      );
      if (!yes) return;
      const ok = await guard('タスク削除', async () => {
        const res = await api.deleteTask(p.dir, t.id);
        toast(`${t.id} を削除しました（${res.via === 'trash' ? 'ゴミ箱へ移動' : '完全削除'}）`, true);
        return true;
      });
      if (ok) {
        gitPushAfterWrite(`agent-dashboard: delete task ${t.id}`, p.dir);
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
        charter: t.extra.charter || '',
        workspace: t.extra.workspace || '',
        // ルーティング・検収・誘導フィールドは網羅的に引き継ぐ（task.schema.json の
        // 「未知キーは保持」契約。system 管理の routed_by/cohort* は新タスクへ持ち込まない）
        ...Object.fromEntries(ENQUEUE_PASSTHROUGH_KEYS.map((k) => [k, t.extra[k] || ''])),
      });
    });
  }
  $('dlg-task').showModal();
}

// charter からのバックログ再分解を要求する（エラー回復用）。本体が次パスで charter を
// 分解し直し、取りこぼした差分だけを backlog へ入れる（done / 既存と類似は投入しない）。
// 状態（done 等）は書き換えず、公式契約（commands/replan・CLI replan）だけで届ける。
function fillCharterSelect(select, p, selected) {
  if (!select) return '';
  const versions = (p && p.charters) || [];
  select.replaceChildren();
  if (!versions.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = '初版（プロジェクト憲章）';
    select.appendChild(option);
    select.disabled = true;
    return '';
  }
  select.disabled = false;
  for (const version of versions) {
    const option = document.createElement('option');
    option.value = version.name;
    option.textContent = version.goal ? `${version.name} — ${version.goal}` : version.name;
    select.appendChild(option);
  }
  const names = new Set(versions.map((version) => version.name));
  const preferred = names.has(selected)
    ? selected
    : names.has(state.backlogCharter)
      ? state.backlogCharter
      : versions[0].name;
  select.value = preferred;
  return preferred;
}

function openReplanDialog() {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  fillCharterSelect($('replan-charter'), p, state.backlogCharter || '');
  $('dlg-replan').showModal();
}

async function requestReplan(charter = '') {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  if ($('dlg-replan').open) $('dlg-replan').close();
  const versionText = charter ? `計画バージョン「${charter}」` : 'プロジェクト憲章';
  const yes = await confirmDialog(
    `${p.name}: ${versionText}からタスクを作り直します。\n` +
      '進行中・却下済みと重複するタスクは追加されません（完了済みと同種のやり直しは作り直されます）。\n' +
      'タスクの状態は書き換えません。反映は次の実行サイクルです（即時ではありません）。よろしいですか？'
  );
  if (!yes) return;
  const ok = await guard('計画の作り直し', async () => {
    const res = await api.requestReplan(p.dir, 'agent-dashboard から再分解を要求', charter);
    uiLog('replan', res);
    toast('計画の作り直しを依頼しました（次の実行で反映されます）', true);
    return true;
  });
  if (ok) {
    gitPushAfterWrite('agent-dashboard: replan request', p.dir);
    await reloadProject();
  }
}

function backlogAssistRows(p) {
  const active = (p && p.backlog) || [];
  const archive = ((p && p.archive) || []).slice(0, 20);
  return [...active, ...archive].map((t) => ({
    id: t.id,
    title: t.title,
    status: t.status,
    priority: t.priority,
    after: Array.isArray(t.after) ? t.after : String(t.after || '')
      .split(/[,，\s]+/)
      .map((x) => x.trim())
      .filter(Boolean),
  }));
}

function charterAssistContext(p, charterName = '') {
  if (!p) return { goal: '', acceptance: '' };
  const version = charterName ? (p.charters || []).find((c) => c.name === charterName) : null;
  const ch = version || p.charter || (p.charters || []).find((c) => c.goal) || (p.charters || [])[0] || {};
  // マスター憲章からの継承（本体 _merge_master_charter と同じ規則）:
  //   goal / acceptance … バージョン側が空ならマスターへフォールバック
  //   constraints / assumptions … バージョン側に**見出しが無ければ**マスターへフォールバック
  //     （見出しがあって空＝「継承値を空に上書き」の明示の意思なので、空でも埋め戻さない。
  //     parseCharter はセクションを見出しの在るキーだけ持つため in 判定で見出しの有無が分かる）
  const master = version && p.charter && p.charter.master ? p.charter : null;
  const acceptanceOf = (c) =>
    Array.isArray(c.acceptanceItems)
      ? c.acceptanceItems.join('\n')
      : Array.isArray(c.acceptance)
        ? c.acceptance.join('\n')
        : String(c.acceptance || '');
  const acceptance = acceptanceOf(ch) || (master ? acceptanceOf(master) : '');
  const inherited = (key) =>
    key in ch ? String(ch[key] || '') : master ? String(master[key] || '') : '';
  return {
    name: ch.name || p.name || '',
    goal: String(ch.goal || (master && master.goal) || ''),
    acceptance,
    constraints: master ? inherited('constraints') : String(ch.constraints || ''),
    assumptions: master ? inherited('assumptions') : String(ch.assumptions || ''),
  };
}

function fillEnqueueAfterOptions(p) {
  const list = $('enq-after-options');
  if (!list) return;
  list.replaceChildren();
  for (const t of backlogAssistRows(p)) {
    if (!t.id) continue;
    const opt = document.createElement('option');
    opt.value = t.id;
    opt.label = `${t.id} — ${t.title || ''} (p${t.priority ?? 0})`;
    list.appendChild(opt);
  }
}

function renderEnqueueBacklogSummary(p) {
  const el = $('enq-backlog-summary');
  if (!el) return;
  const rows = backlogAssistRows(p).filter((t) => t.status !== 'rejected').slice(0, 40);
  if (!rows.length) {
    el.textContent = 'まだバックログがありません。';
    return;
  }
  el.innerHTML = `<ul>${rows
    .map((t) => {
      const after = (t.after || []).length ? ` ← ${(t.after || []).join(', ')}` : '';
      return `<li><code>${esc(t.id)}</code> p${esc(t.priority ?? 0)} [${esc(t.status || '?')}] ${esc(t.title || '')}${esc(after)}</li>`;
    })
    .join('')}</ul>`;
}

async function refreshEnqueueAdjustmentPlan() {
  const el = $('enq-ai-adjustments');
  if (!el) return;
  const adjustments = state.enqueueAdjustments || [];
  if (!adjustments.length) {
    el.classList.add('hidden');
    el.innerHTML = '';
    return;
  }
  const p = state.project;
  let planned = { apply: [], skipped: [] };
  try {
    planned = await api.agentPlanAdjustments({
      backlog: (p && p.backlog) || [],
      adjustments,
    });
  } catch (err) {
    el.classList.remove('hidden');
    el.innerHTML = `<div class="doctor-error" role="alert">調整案の整理に失敗しました: ${esc(err.message || err)}</div>`;
    return;
  }
  const apply = planned.apply || [];
  const skipped = planned.skipped || [];
  if (!apply.length && !skipped.length) {
    el.classList.add('hidden');
    el.innerHTML = '';
    return;
  }
  el.classList.remove('hidden');
  const applyRows = apply
    .map(
      (a) => `<li class="enq-adj-item">
        <label>
          <input type="checkbox" class="enq-adj-check" data-adj-id="${esc(a.id)}" checked />
          <code>${esc(a.id)}</code> ${esc(a.title || '')}
          <span class="muted">${esc(a.summary)}</span>
          ${a.reason ? `<span class="muted">— ${esc(a.reason)}</span>` : ''}
        </label>
      </li>`
    )
    .join('');
  const skipRows = skipped
    .map((s) => `<li class="muted"><code>${esc(s.id)}</code> — ${esc(s.reason)}</li>`)
    .join('');
  el.innerHTML =
    '<strong>既存タスクへの調整案</strong>' +
    (apply.length
      ? `<p class="muted">選択した変更を「修正を指示」（revise）として送ります。タスク状態は書き換えず、次の実行で反映されます。</p>
        <ul class="enq-adj-list">${applyRows}</ul>
        <div class="enq-adj-actions">
          <button type="button" id="btn-enq-adj-apply" class="primary-inline">選択した調整を反映</button>
          <button type="button" id="btn-enq-adj-clear">提案を破棄</button>
        </div>`
      : '<p class="muted">反映できる差分はありません（現状と同じか対象外）。</p>') +
    (skipRows ? `<details class="enq-adj-skipped"><summary>スキップ ${skipped.length} 件</summary><ul>${skipRows}</ul></details>` : '');
  const applyBtn = $('btn-enq-adj-apply');
  if (applyBtn) applyBtn.addEventListener('click', () => applySelectedEnqueueAdjustments(apply));
  const clearBtn = $('btn-enq-adj-clear');
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      state.enqueueAdjustments = [];
      refreshEnqueueAdjustmentPlan();
      const status = $('enq-ai-status');
      if (status) status.textContent = '既存タスクの調整案を破棄しました';
    });
  }
}

function renderEnqueueAdjustments(adjustments) {
  state.enqueueAdjustments = Array.isArray(adjustments) ? adjustments : [];
  return refreshEnqueueAdjustmentPlan();
}

async function applySelectedEnqueueAdjustments(applyList) {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  if (state.assistBusy) return;
  const selectedIds = new Set(
    [...document.querySelectorAll('#enq-ai-adjustments .enq-adj-check:checked')].map((el) => el.dataset.adjId)
  );
  const selected = (applyList || []).filter((a) => selectedIds.has(a.id));
  if (!selected.length) return toast('反映する調整を選択してください');
  const lines = selected.map((a) => `・${a.id}: ${a.summary}`).join('\n');
  const yes = await confirmDialog(
    `次の ${selected.length} 件の既存タスクを修正します（revise）。\n` +
      'タスク状態ファイルは直接書き換えず、公式の修正指示として送ります。\n\n' +
      `${lines}\n\nよろしいですか？`
  );
  if (!yes) return;
  state.assistBusy = true;
  const applyBtn = $('btn-enq-adj-apply');
  const status = $('enq-ai-status');
  if (applyBtn) applyBtn.disabled = true;
  if (status) status.textContent = '既存タスクの調整を送信しています…';
  const sent = [];
  const failed = [];
  try {
    for (const item of selected) {
      try {
        const feedback = item.reason
          ? `AI提案の依存・優先度調整: ${item.reason}`
          : 'AI提案の依存・優先度調整（人確認済み）';
        const res = await api.runAction({
          dir: p.dir,
          action: 'revise',
          id: item.id,
          reason: 'agent-dashboard: AI提案の依存・優先度調整（人確認済み）',
          fields: item.fields,
          feedback,
        });
        const task = (p.backlog || []).find((t) => t.id === item.id);
        if (task) markReviseSent(task);
        uiLog('enqueueAdjust', item.id, res);
        sent.push(item.id);
      } catch (err) {
        failed.push(`${item.id}: ${err.message || err}`);
      }
    }
    if (sent.length) {
      gitPushAfterWrite(`agent-dashboard: revise deps/priority ${sent.join(',')}`, p.dir);
      state.enqueueAdjustments = (state.enqueueAdjustments || []).filter((a) => !sent.includes(a.id));
      await reloadProject();
      fillEnqueueAfterOptions(state.project);
      renderEnqueueBacklogSummary(state.project);
      await refreshEnqueueAdjustmentPlan();
    }
    if (failed.length) {
      toast(`一部失敗: ${failed.join(' / ')}`);
    } else if (sent.length) {
      toast(`${sent.length} 件の調整を送信しました（次の実行で反映）`, true);
    }
    if (status) {
      status.textContent = sent.length
        ? `既存タスク ${sent.length} 件の調整を送信しました`
        : '調整の送信に失敗しました';
    }
  } finally {
    state.assistBusy = false;
    if (applyBtn) applyBtn.disabled = false;
  }
}

// タスク追加ダイアログを開く。prefill.reinject が真のときは archive タスクの
// 「revise して再投入」モード（エラー復帰用途）— 元タスクの内容を編集して inbox へ入れる。
function openEnqueueDialog(prefill = {}) {
  const reinject = !!prefill.reinject;
  $('enq-heading').textContent = reinject
    ? '完了タスクを編集してやり直す'
    : 'タスクを追加';
  const note = $('enq-reinject-note');
  if (reinject) {
    note.textContent =
      `完了タスク ${prefill.id || ''} の内容を引き継いで、新しいタスクとして追加します。` +
      '完了の記録はそのまま残ります（誤って完了になった場合のやり直しに使えます）。';
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
  $('enq-after').value = Array.isArray(prefill.after) ? prefill.after.join(', ') : (prefill.after || '');
  fillCharterSelect($('enq-charter'), state.project, prefill.charter || '');
  fillWorkspaceSelect($('enq-workspace'), state.project, prefill.workspace || '');
  // level / track と誘導・レビュー記述（why 等）・ルーティング/検収系（refs/paths/review/expect/
  // followup/verify_template）はフォームに出さないが、再投入・フォローアップ提案では
  // 元の値を引き継いで送る（task.schema.json の「未知キーは保持」契約を UI 経由でも守る）
  state.enqueueExtra = Object.fromEntries(
    ENQUEUE_PASSTHROUGH_KEYS.map((k) => [
      k,
      Array.isArray(prefill[k]) ? prefill[k].join(', ') : prefill[k] || '',
    ])
  );
  fillEnqueueAfterOptions(state.project);
  renderEnqueueBacklogSummary(state.project);
  state.enqueueAdjustments = [];
  void refreshEnqueueAdjustmentPlan();
  const status = $('enq-ai-status');
  if (status) status.textContent = '';
  $('dlg-enqueue').showModal();
}

async function aiEnqueueAssist() {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const title = $('enq-title').value.trim();
  if (!title) return toast('タイトルを書いてから依存・優先度を提案してください');
  if (state.assistBusy) return;
  state.assistBusy = true;
  const btn = $('btn-enq-ai');
  const status = $('enq-ai-status');
  btn.disabled = true;
  status.textContent = '依存・優先度を提案しています…';
  try {
    const res = await api.agentTaskAssist({
      dir: p.dir,
      mode: 'enqueue-assist',
      context: {
        charter: charterAssistContext(p, $('enq-charter').value),
        backlog: backlogAssistRows(p),
        draft: {
          title,
          verify: $('enq-verify').value.trim(),
          accept: $('enq-accept').value.trim(),
          priority: $('enq-priority').value,
          after: $('enq-after').value.trim(),
          note: $('enq-note').value.trim(),
          id: $('enq-id').value.trim(),
        },
      },
    });
    const f = res.fields || {};
    if (f.after && f.after.length) $('enq-after').value = f.after.join(', ');
    if (f.priority != null) $('enq-priority').value = String(f.priority);
    if (f.note) $('enq-note').value = f.note;
    await renderEnqueueAdjustments(f.adjustments || []);
    const adjCount = (state.enqueueAdjustments || []).length;
    status.textContent =
      `提案を反映しました（${res.cli}${res.model ? ` / ${res.model}` : ''}）` +
      (f.rationale ? ` — ${f.rationale}` : '') +
      (adjCount
        ? `。既存タスクの調整案 ${adjCount} 件を確認し、よければ「選択した調整を反映」を押してください`
        : '。内容を確認してから追加してください');
  } catch (err) {
    status.textContent = '';
    toast(`依存・優先度の提案に失敗しました: ${err.message || err}`);
  } finally {
    state.assistBusy = false;
    btn.disabled = false;
  }
}

// 書込先（workspace）の選択肢: リポジトリ一覧（repos.json）のうち owns を持つ＝書込先の
// エントリ名。空 = 自動ルーティング（owns と paths の突き合わせ）。モノレポは path 別の
// エントリ名で担当フォルダを指せる。既存値がリストに無くても消さない（選択肢に足す）。
function fillWorkspaceSelect(select, p, selected) {
  if (!select) return;
  const names = [];
  if (p && p.repos && typeof p.repos === 'object') {
    for (const [name, e] of Object.entries(p.repos)) {
      if (name.startsWith('_') || !e || typeof e !== 'object') continue;
      const owns = Array.isArray(e.owns) ? e.owns.length : String(e.owns || '').trim();
      if (owns) names.push(name);
    }
  }
  if (selected && !names.includes(selected)) names.push(selected);
  select.replaceChildren();
  const auto = document.createElement('option');
  auto.value = '';
  auto.textContent = '自動（担当範囲から推定）';
  select.appendChild(auto);
  for (const name of names) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    select.appendChild(option);
  }
  select.value = selected || '';
  const field = $('enq-workspace-field');
  if (field) field.classList.toggle('hidden', !names.length);
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
    charter: $('enq-charter').value,
    workspace: $('enq-workspace') ? $('enq-workspace').value : '',
    ...Object.fromEntries(ENQUEUE_PASSTHROUGH_KEYS.map((k) => [k, extra[k] || ''])),
  };
  const ok = await guard('タスク追加', async () => {
    const res = await api.enqueueTask(p.dir, spec);
    uiLog('enqueue', res);
    toast(
      `タスクを追加しました: ${res.spec.title}\n` +
        (res.spec.verify || res.spec.accept
          ? '（次の実行サイクルで一覧に載ります）'
          : '（完了条件が無いため、取り込み後に内容の確認が必要になります）'),
      true
    );
    return true;
  });
  if (ok) {
    gitPushAfterWrite(`agent-dashboard: enqueue ${spec.title || ''}`.trim(), p.dir);
    $('dlg-enqueue').close();
    await reloadProject();
  }
}
