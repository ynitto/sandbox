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

// agent-project の run-id 生成（_req_id_for）と同じ task.id 正規化。バックログの task.id を
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
    'タスクは廃止されて履歴に残り、同種のタスクを避ける学習も記録されます。' +
    '似た内容のタスクは、次にバックログを分解しても提案されなくなります。' +
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
    state.flowRevisionId = null;
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
    b.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const dlg = $('dlg-task');
      if (dlg && dlg.open) {
        await requestTaskDialogClose();
        if (dlg.open) return;
      }
      gotoRun(b.dataset.gotoRun);
    });
  }
  for (const b of root.querySelectorAll('[data-goto-task]')) {
    b.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const dlg = $('dlg-task');
      if (dlg && dlg.open) {
        await requestTaskDialogClose();
        if (dlg.open) return;
      }
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

// ---------------------------------------------------------------------------
// 監視担当（チーム運用）: ミーティングで各タスクに「見る人」を割り当て、進捗確認・
// 検収・要対応の監視を分担する。実作業（エージェント）の分担とは別軸で、
// assignments.json（viewer 管理のサイドカー）にだけ書く＝タスク状態には触れない。
// ---------------------------------------------------------------------------

const OWNER_UNASSIGNED = '__none__'; // 「未担当」フィルタの番兵値（表示専用）

// 担当フィルタのチップ定義。メンバーが誰もいなければ空（チップ行ごと出さない）。
function ownerFilterChoices(p) {
  const members = (p && p.assignments && p.assignments.members) || [];
  if (!members.length) return [];
  return [['', '全員'], ...members.map((m) => [m, m]), [OWNER_UNASSIGNED, '未担当']];
}

function filterTasksByOwner(tasks, key) {
  if (!key) return tasks;
  if (key === OWNER_UNASSIGNED) return tasks.filter((t) => !String(t.owner || '').trim());
  return tasks.filter((t) => String(t.owner || '').trim() === key);
}

function ownerBadgeHtml(owner) {
  const name = String(owner || '').trim();
  if (!name) return '';
  return `<span class="owner-badge" title="監視担当（このタスクの進捗・要対応を見る人）">👤 ${esc(name)}</span>`;
}

// 委任（offloaded）タスクの「いま誰が持っているか」を板から読む。
//
// 委譲 id はタスクの `last_run` に入る（実行側の run-id / mission-id と同一——共通 id に
// 対応表を持たない delegation 契約 D1 の帰結）。板の正規化ビューを id で引くだけで、
// dashboard は板へ何も書かない。板を使っていなければ null（画面には何も足さない）。
function boardDelegationView(task) {
  const did = String((task && task.extra && task.extra.last_run) || '').trim();
  if (!did) return null;
  return (state.boardViews || []).find((v) => String(v.id) === did) || null;
}

function boardDelegationSummary(task) {
  const view = boardDelegationView(task);
  if (!view) return '';
  const assignee = (view.units && view.units[0] && view.units[0].assignee) || '';
  const bids = ((view.units && view.units[0] && view.units[0].bids) || [])
    .filter((b) => b.state !== 'expired').length;
  if (view.phase === 'open') {
    return bids ? `委任先: 未定（入札 ${bids} 件）` : '委任先: 未定（入札を待っています）';
  }
  if (view.phase === 'done') return `委任先: ${assignee || '不明'} — 完了`;
  if (view.phase === 'failed') return `委任先: ${assignee || '不明'} — 失敗`;
  if (view.phase === 'cancelled') return '委任: 中止されました';
  if (view.phase === 'waiting') return `委任先: ${assignee || '不明'} — 待機中`;
  return `委任先: ${assignee || '不明'} — 実行中`;
}

// タスク詳細の「委任」行。中止は板へ直接書かず、この端末の常駐体へ指示を投函する。
function boardDelegationRowHtml(task) {
  const view = boardDelegationView(task);
  if (!view) return '';
  const sent = (state.boardCommands || {})[String(view.id)];
  const note = sent && sent.state === 'pending' ? '<span class="muted">（指示を送信済み）</span>'
    : sent && sent.state === 'done' ? '<span class="muted">（指示は受理されました）</span>'
    : sent && sent.state === 'error'
      ? `<span class="need-error">（指示が通りませんでした: ${esc(sent.error || '')}）</span>`
      : '';
  const canCancel = !['done', 'failed', 'cancelled'].includes(String(view.phase));
  const cancel = canCancel
    ? `<button type="button" data-board-cancel="${esc(view.id)}"
        title="この委任を板の上で中止します（実行中なら次の巡回で止まります）">委任を中止</button>`
    : '';
  return `<tr><th>委任</th><td>${esc(boardDelegationSummary(task))}
    <span class="mono muted">${esc(view.id)}</span> ${cancel} ${note}</td></tr>`;
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
    owner: String(task.owner || '').trim(),
    // 委任中のタスクは「次の行動」より「いま誰が持っているか」が知りたい情報になる。
    nextAction: (task.status === 'offloaded' && boardDelegationSummary(task))
      || String((hint && hint.completeHow) || '詳細を確認してください'),
  };
}

function taskListItemHtml(item, scope) {
  return `<button type="button" class="task-list-item" data-task="${esc(item.id)}" data-scope="${esc(scope)}" role="listitem" aria-label="${esc(item.title)}の詳細を開く">
    <span class="task-list-status" data-label="状態" aria-label="状態 ${esc(item.statusText)}">${statusChip(item.status)}</span>
    <span class="task-list-title" data-label="タスク">${esc(item.title)}${item.owner ? ` ${ownerBadgeHtml(item.owner)}` : ''}</span>
    <span class="task-list-priority" data-label="優先度" aria-label="優先度 ${esc(item.priorityText)}">${esc(item.priorityText)}</span>
    <span class="task-list-next" data-label="次の行動">${esc(item.nextAction)}</span>
    <svg class="task-list-chevron" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m9 18 6-6-6-6" /></svg>
  </button>`;
}

// 却下済み（墓標）— 削除（＝却下）したタスクの「作り直さない」記録。ここに出すのは
// 削除を取り返しのつく操作にするため: 墓標がある限り同じ題は再投入も再分解もされないので、
// 一覧と解除が無いと「消したら二度と入れ直せない」＝試行錯誤ができない。
function tombstonesHtml(p) {
  const graves = p.tombstones || [];
  if (!graves.length) return '';
  const rows = graves
    .map(
      (g) => `
      <div class="tombstone-row" role="listitem">
        <span class="tombstone-title">${esc(g.title)}</span>
        <span class="muted tombstone-meta">${esc([g.reason, g.date, g.charter && `バージョン: ${g.charter}`]
          .filter(Boolean)
          .join(' ／ '))}</span>
        <button data-revive="${esc(g.title)}" data-revive-charter="${esc(g.charter || '')}"
                title="この題を再び提案・投入できる状態へ戻します">解除</button>
      </div>`
    )
    .join('');
  return `
    <details class="tombstones">
      <summary>却下済み（${graves.length} 件） — 同じタスクを作り直させない記録</summary>
      <div class="tombstone-list" role="list">${rows}</div>
    </details>`;
}

function bindTombstones(root, p) {
  for (const btn of root.querySelectorAll('[data-revive]')) {
    btn.addEventListener('click', async () => {
      const title = btn.dataset.revive;
      const yes = await confirmDialog(
        `「${title}」の却下を解除します。\n` +
          '同じ題のタスクを追加でき、計画の作り直しでも再び提案されるようになります。よろしいですか？'
      );
      if (!yes) return;
      const ok = await guard('却下の解除', async () => {
        await api.reviveTombstone(p.dir, title, btn.dataset.reviveCharter || '');
        toast(`「${title}」の却下解除を要求しました（稼働中の agent-project が取り込みます）`, true);
        return true;
      });
      if (ok) await reloadProject();
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

  // 監視担当フィルタ（チーム運用）。ミーティングで「自分の担当だけ」に絞って進捗を追える。
  // メンバーが誰もいなくなったらフィルタも解除する（チップ無しで絞り込みが残留しない）
  const ownerChipDefs = ownerFilterChoices(p);
  if (!ownerChipDefs.length) state.backlogOwner = '';
  tasks = filterTasksByOwner(tasks, state.backlogOwner || '');
  const ownerChips = ownerChipDefs.length
    ? `<span class="muted" style="margin-left:8px">担当:</span>` +
      ownerChipDefs
        .map(
          ([v, label]) =>
            `<button class="chip ${((state.backlogOwner || '') === v) ? 'active' : ''}" data-owner-filter="${esc(v)}" aria-pressed="${((state.backlogOwner || '') === v)}">${esc(label)}</button>`
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
          ${replanPending ? '<span class="badge info" title="バックログの分解を依頼済みです。次の実行で反映されます">分解 反映待ち</span>' : ''}
        </div>
        ${charterChips ? `<div class="filters task-version-filters" aria-label="計画バージョンで絞り込む">${charterChips}</div>` : ''}
        ${ownerChips ? `<div class="filters task-owner-filters" aria-label="監視担当で絞り込む">${ownerChips}</div>` : ''}
      </div>
      <div class="task-toolbar-actions">
        <button id="btn-notes" title="気になったことをメモに書き溜めます（計画は勝手に動きません）">メモ</button>
        <button id="btn-document-tasks" title="ローカルのMarkdown・テキスト文書からタスク候補を作ります">文書から作る</button>
        <button id="btn-replan"${replanPending ? ' disabled' : ''} title="プロジェクト憲章からタスクを作ります。自動では作られないので、最初の分解もここから始めます">バックログを分解</button>
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
    }
    ${tombstonesHtml(p)}`;

  bindTombstones(el, p);
  $('btn-enqueue').addEventListener('click', () => openEnqueueDialog());
  const replanBtn = $('btn-replan');
  if (replanBtn && !replanPending) replanBtn.addEventListener('click', openReplanDialog);
  const notesBtn = $('btn-notes');
  if (notesBtn) notesBtn.addEventListener('click', openNotesDialog);
  const documentBtn = $('btn-document-tasks');
  if (documentBtn) documentBtn.addEventListener('click', openDocumentTaskDialog);

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
  for (const chip of el.querySelectorAll('.chip[data-owner-filter]')) {
    chip.addEventListener('click', () => {
      state.backlogOwner = chip.dataset.ownerFilter;
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
    return `<div class="muted" style="margin-top:8px">修正内容を送信済みです（反映されると再度編集できます）</div>`;
  }
  const doingNote =
    t.status === 'doing'
      ? '<div class="muted">実行中のタスクです。送信すると現在の作業を打ち切り、修正内容と指示でやり直します（早い軌道修正に使えます）。</div>'
      : t.status === 'offloaded'
        ? '<div class="muted">委任先で実行中のタスクです。送信すると今回の結果は採用されず、修正を反映してやり直します（切り替えは今回の作業が終わり次第）。</div>'
        : '<div class="muted">修正は次の実行から反映されます。依存関係を変えると作業の順序も変わります。</div>';
  const level = String(t.extra.level || '');
  const levelChoices = [
    ['', '指定しない'],
    ['report', '結果だけ報告'],
    ['assisted', '確認しながら進める'],
    ['unattended', '確認なしで進める'],
  ];
  if (level && !levelChoices.some(([value]) => value === level)) levelChoices.push([level, level]);
  const levelOptions = levelChoices
    .map(([value, label]) => `<option value="${esc(value)}" ${value === level ? 'selected' : ''}>${esc(label)}</option>`)
    .join('');
  return `<div class="revise-area">
    <h3>タスクを修正</h3>
    ${doingNote}
    <details open>
      <summary>内容と完了条件</summary>
      <div class="field"><label for="rv-feedback">変更してほしいこと</label>
        <textarea rows="2" id="rv-feedback" placeholder="例: e2e はローカルサーバでなく実サーバに配備して実施すること"></textarea>
        <p class="field-help">次の実行に反映します。</p></div>
      <div class="field"><label for="rv-title">タスク名</label><input id="rv-title" value="${esc(t.title)}" /></div>
      <div class="field"><label for="rv-acceptance">受入基準</label>
        <textarea rows="4" id="rv-acceptance" placeholder="1 行 1 基準で書きます">${esc(
          acceptanceList(t).join('\n')
        )}</textarea>
        <p class="field-help">すべて満たしたときに完了と判断します。空欄にすると削除します。</p></div>
    </details>
    <details>
      <summary>実行順と担当</summary>
      <div class="row2">
        <div class="field"><label for="rv-priority">着手の優先度</label><input id="rv-priority" type="number" step="1" value="${t.priority}" />
          <p class="field-help">数字が大きいほど先に着手します。</p></div>
        <div class="field"><label for="rv-level">進め方</label>
          <select id="rv-level">${levelOptions}</select>
        </div>
      </div>
      <div class="field"><label for="rv-after">先に完了するタスク</label><input id="rv-after" class="mono" value="${esc(t.extra.after || '')}" />
        <p class="field-help">タスク番号をカンマ区切りで指定します。空欄にすると解除します。</p></div>
      <div class="field"><label for="rv-track">関連タスクのグループ</label><input id="rv-track" value="${esc(t.extra.track || '')}" />
        <p class="field-help">同じ種類のタスクをまとめる名前です。空欄にすると削除します。</p></div>
      <div class="field"><label for="rv-node">実行する PC</label><input id="rv-node" value="${esc(t.extra.node || '')}" />
        <p class="field-help">複数の PC で分担するときに指定します。空欄なら自動で選びます。</p></div>
      <div class="field"><label for="rv-note">補足</label><input id="rv-note" value="${esc(t.extra.note || '')}" />
        <p class="field-help">空欄にすると削除します。</p></div>
    </details>
    <details>
      <summary>確認方法を固定</summary>
      <div class="field"><label for="rv-verify">実行するコマンド</label><input id="rv-verify" class="mono" value="${esc(fixedVerifyCommand(t))}" />
        <p class="field-help">コマンドが決まっている場合だけ設定します。空欄にすると削除します。</p></div>
    </details>
    <details class="revise-guide" ${GUIDE_KEYS.some((k) => t.extra[k]) ? 'open' : ''}>
      <summary>目的と変更範囲</summary>
      <p class="field-help">必要な項目だけ入力します。空欄にした項目は削除します。</p>
      <div class="row need-buttons">
        <span class="muted">入力済みの内容から下書きを作れます。</span>
        <span class="spacer"></span>
        <button type="button" id="btn-guide-assist">下書きを作る</button>
      </div>
      <div class="muted" id="guide-assist-status"></div>
      ${GUIDE_KEYS.map(
        (k) => k === 'risks'
          ? `<div class="field"><label for="rv-risks">${esc(GUIDE_LABELS[k])}</label>
              <textarea id="rv-risks" rows="3" placeholder="1 行 1 リスク。該当しない場合は「なし」">${esc(t.extra[k] || '')}</textarea></div>`
          : `<div class="field"><label for="rv-${k}">${esc(GUIDE_LABELS[k])}</label><input id="rv-${k}" value="${esc(t.extra[k] || '')}" /></div>`
      ).join('')}
    </details>
    <div class="row need-buttons">
      <span class="muted">入力した変更内容は履歴に残ります</span>
      <span class="spacer"></span>
      <button class="primary-inline" id="btn-revise-send">修正を送信</button>
    </div>
  </div>`;
}

const TASK_EXTRA_LABELS = {
  workspace: '作業フォルダ',
  refs: '関連情報',
  after: '先に完了するタスク',
  level: '進め方',
  track: '関連タスクのグループ',
  node: '実行する PC',
  note: '補足',
  flow_run: '関連する実行',
  charter: '計画バージョン',
  feedback: 'フィードバック',
  needs_reason: '対応が必要な理由',
  acceptance: '受入基準（旧形式）',
  accept: '受入基準（旧形式）',
  task_acceptance_criteria: '受入基準',
  verification_commands: '検証コマンド',
};

function taskDialogInputSnapshot(dialog) {
  return JSON.stringify(
    [...dialog.querySelectorAll('input, textarea, select')].map((field) => [
      field.id,
      field.type === 'checkbox' ? field.checked : field.value,
    ])
  );
}

async function requestTaskDialogClose() {
  const dialog = $('dlg-task');
  if (!dialog.open) return;
  if (dialog._inputSnapshot !== taskDialogInputSnapshot(dialog)) {
    const discard = await confirmDialog('入力中の変更を破棄して閉じますか？');
    if (!discard) return;
  }
  dialog._inputSnapshot = null;
  dialog.close();
}

function showTaskDialog(id, scope) {
  const p = state.project;
  const list = scope === 'archive' ? p.archive : p.backlog;
  const t = list.find((x) => x.id === id);
  if (!t) return;
  const extraRows = Object.entries(t.extra)
    .filter(([k]) => k !== 'owner') // 監視担当は専用行（下の「監視担当」）で表示・編集する
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
      const label = GUIDE_LABELS[k] || TASK_EXTRA_LABELS[k] || `詳細（${k}）`;
      return `<tr><th>${esc(label)}</th><td>${cell}</td></tr>`;
    })
    .join('');
  // 決定記録を残す人の操作（backlog のタスクのみ。archive は閲覧のみ）
  const canApprove = ['blocked', 'review', 'proposed'].includes(t.status);
  const deps = String(t.extra.after || '').trim();
  const downs = dependentsOf(p.backlog, t.id);
  const depRow = `<tr><th>実行順</th><td class="muted">先に完了するタスク: ${deps ? esc(deps) : '（なし）'} ／ この後に実行するタスク: ${
    downs.length ? downs.map((x) => `${esc(x.id)}[${esc(statusLabel(x.status))}]`).join(', ') : '（なし）'
  }</td></tr>`;
  const rr = runsForTask(t.id);
  const hint = taskCompletionHint(t, { runs: rr, archived: scope === 'archive' });
  const statusCell = hint.statusNote
    ? `${statusChip(t.status)} <span class="badge warn" title="${esc(hint.completeHow)}">${esc(hint.statusNote)}</span>`
    : statusChip(t.status);
  const acceptance = acceptanceList(t);
  const acceptanceHtml = acceptance.length
    ? `<ul>${acceptance.map((item) => `<li>${esc(item)}</li>`).join('')}</ul>`
    : '<span class="muted">（未定義）</span>';
  const verificationDetailsRow = t.verify
    ? `<tr><th>検証の詳細</th><td><details><summary>固定した検証方法</summary><pre class="mono">${esc(t.verify)}</pre></details></td></tr>`
    : '';
  // 削除を拒むのは「実行中」だけ。クレームロックは worker クラッシュや
  // review/blocked 滞留で残骸が残るため、doing 以外ではロックがあっても削除できる
  const claimed = p.claims.includes(t.id) && t.status === 'doing';
  const actionArea =
    scope === 'archive'
      ? `<div class="need-actions">
          <div class="row need-buttons">
            <span class="muted">完了（履歴）のタスクです。内容を編集して新しいタスクとしてやり直せます（履歴は残ります）。</span>
            <span class="spacer"></span>
            <button class="primary-inline" id="btn-task-reinject" title="このタスクの内容を編集して、新しいタスクとして追加し直します">編集してやり直す</button>
          </div>
        </div>`
      : `<div class="need-actions">
          <div class="task-complete-banner">${esc(hint.completeHow)}</div>
          <label for="task-reason">操作の理由</label>
          <textarea rows="2" id="task-reason" class="need-input" placeholder="操作の理由（決定記録に残ります）"></textarea>
          <div class="row need-buttons">
            ${canApprove ? `<button class="primary-inline" data-taskact="approve">承認</button>` : ''}
            ${t.status === 'doing' ? '' : `<button class="danger" data-taskact="reject" data-confirm-reject="1" title="タスクを廃止します。依存するタスクは計画の再確認に戻り、似た内容のタスクは次の分解でも提案されなくなります">却下</button>`}
            <button data-taskact="pin" title="他より先に着手させます">最優先にする</button>
            <button data-taskact="defer" title="優先度を下げて後に回します">後回しにする</button>
            <button data-taskact="hold" title="実行を止めて保留にします（再開には承認が必要）">保留にする</button>
            <span class="spacer"></span>
            <button class="danger" id="btn-task-delete" ${claimed ? 'disabled' : ''}
              title="${claimed ? '実行中のタスクは削除できません' : 'タスクをゴミ箱へ移動します（決定記録は残りません）'}">削除</button>
          </div>
        </div>`;
  const ownerEditArea = scope === 'archive' ? '' : `<div class="task-owner-editor">
    <h3>監視担当</h3>
    <p class="muted">エージェントの実作業とは別に、進捗確認と検収を担当する人です。</p>
    <div class="row owner-edit">
      <label for="task-owner">担当者</label>
      <input id="task-owner" list="task-owner-list" value="${esc(t.owner || '')}"
        placeholder="担当者名（空にして保存で解除）" />
      <datalist id="task-owner-list">${((p.assignments && p.assignments.members) || [])
        .map((m) => `<option value="${esc(m)}"></option>`)
        .join('')}</datalist>
      <button id="btn-task-owner" title="このタスクの進捗・要対応を見る人を決めます">担当を保存</button>
    </div>
  </div>`;
  $('dlg-task-title').innerHTML = `<span class="mono">${esc(t.id)}</span>: ${esc(t.title)}`;
  $('dlg-task-body').innerHTML = `
    <div class="task-dialog-tabs" role="tablist" aria-label="タスク詳細の表示内容">
      <button type="button" id="task-tab-overview" role="tab" aria-selected="true" aria-controls="task-panel-overview" data-task-section="overview">概要</button>
      ${scope === 'archive' ? '' : '<button type="button" id="task-tab-edit" role="tab" aria-selected="false" aria-controls="task-panel-edit" data-task-section="edit" tabindex="-1">編集</button>'}
      <button type="button" id="task-tab-actions" role="tab" aria-selected="false" aria-controls="task-panel-actions" data-task-section="actions" tabindex="-1">操作</button>
    </div>
    <section id="task-panel-overview" role="tabpanel" aria-labelledby="task-tab-overview" data-task-panel="overview">
      ${relationshipStrip({ taskId: t.id })}
      <table class="list task-summary-table">
        <tr><th>状態</th><td>${statusCell}</td></tr>
        <tr><th>完了まで</th><td class="task-complete-how">${esc(hint.completeHow)}</td></tr>
        <tr><th>出自</th><td>${esc(t.source)}</td></tr>
        <tr><th>優先度</th><td>${t.priority}</td></tr>
        <tr><th>監視担当</th><td>${t.owner ? ownerBadgeHtml(t.owner) : '<span class="muted">（未担当）</span>'}</td></tr>
        <tr><th>再試行</th><td>${t.retries}</td></tr>
        ${boardDelegationRowHtml(t)}
        <tr><th>受入基準</th><td>${acceptanceHtml}</td></tr>
        ${verificationDetailsRow}
        ${depRow}
      </table>
      ${relatedRunsBlock(t.id, { archived: scope === 'archive' })}
      <details class="task-technical-details">
        <summary>詳細情報</summary>
        <table class="list">
          ${extraRows}
          <tr><th>タスクファイル</th><td><a href="#" id="task-open-file" class="mono">${esc(t.file)}</a></td></tr>
        </table>
      </details>
    </section>
    ${scope === 'archive' ? '' : `<section id="task-panel-edit" role="tabpanel" aria-labelledby="task-tab-edit" data-task-panel="edit" hidden>
      ${ownerEditArea}
      ${reviseAreaHtml(t)}
    </section>`}
    <section id="task-panel-actions" role="tabpanel" aria-labelledby="task-tab-actions" data-task-panel="actions" hidden>
      ${actionArea}
    </section>`;
  const taskBody = $('dlg-task-body');
  for (const tab of taskBody.querySelectorAll('[data-task-section]')) {
    tab.addEventListener('click', () => {
      const selected = tab.dataset.taskSection;
      for (const item of taskBody.querySelectorAll('[data-task-section]')) {
        item.setAttribute('aria-selected', String(item === tab));
        item.tabIndex = item === tab ? 0 : -1;
      }
      for (const panel of taskBody.querySelectorAll('[data-task-panel]')) {
        panel.hidden = panel.dataset.taskPanel !== selected;
      }
    });
    tab.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      const tabs = [...taskBody.querySelectorAll('[data-task-section]')];
      const offset = event.key === 'ArrowRight' ? 1 : -1;
      const next = tabs[(tabs.indexOf(tab) + offset + tabs.length) % tabs.length];
      next.focus();
      next.click();
    });
  }
  bindRelationship($('dlg-task-body')); // パンくず・関連 run のクリック配線
  const link = $('task-open-file');
  if (link) link.addEventListener('click', (e) => {
    e.preventDefault();
    guard('ファイルを開く', () => api.openPath(t.file));
  });
  for (const btn of document.querySelectorAll('#dlg-task-body button[data-board-cancel]')) {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.boardCancel;
      const view = (state.boardViews || []).find((v) => String(v.id) === id);
      const yes = await confirmDialog(
        `この委任（${id}）を中止しますか？\n実行中の端末があれば、次の巡回で止まります。`);
      if (!yes) return;
      await guard('委任の中止', async () => {
        await api.delegationCancel({
          target: 'board', id, boardRepo: (view && view.boardRepo) || '',
          workload: (view && view.workload) || 'flow',
          reason: 'agent-dashboard から中止',
        });
        toast('中止を依頼しました（板への反映はこの端末の実行エンジンが行います）', true);
        return true;
      });
      await refreshBoard();
      renderBacklog();
    });
  }
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
        const yes = await confirmDialog(rejectConfirmMessage(p, t.id, '廃止して記録に残す'));
        if (!yes) return;
      }
      const ok = await guard('操作', async () => {
        const res = await api.runAction({ dir: p.dir, action: btn.dataset.taskact, id: t.id, reason });
        uiLog('taskAction', btn.dataset.taskact, t.id, res);
        toast(`${TASK_ACT_DONE[btn.dataset.taskact] || '操作しました'}（反映まで少し時間がかかることがあります）`, true);
        return true;
      });
      if (ok) {
        $('dlg-task').close();
        await reloadProject();
      }
    });
  }
  // 監視担当の割り当て（assignments.json への書き込みのみ。タスク状態には触れない）
  const ownBtn = $('btn-task-owner');
  if (ownBtn) {
    ownBtn.addEventListener('click', async () => {
      const owner = $('task-owner').value.trim();
      if (owner === String(t.owner || '').trim()) return toast('担当は変わっていません');
      const ok = await guard('監視担当の設定', async () => {
        const res = await api.setTaskOwner(p.dir, t.id, owner);
        uiLog('setOwner', t.id, res);
        toast(
          owner ? `${t.id} の監視担当を「${owner}」にしました` : `${t.id} の監視担当を解除しました`,
          true
        );
        return true;
      });
      if (ok) {
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
        ['level', $('rv-level').value.trim(), String(t.extra.level || '')],
        ['track', $('rv-track').value.trim(), String(t.extra.track || '')],
        ['node', $('rv-node').value.trim(), String(t.extra.node || '')],
        ['note', $('rv-note').value.trim(), String(t.extra.note || '')],
        ...GUIDE_KEYS.filter((k) => k !== 'risks')
          .map((k) => [k, $(`rv-${k}`).value.trim(), String(t.extra[k] || '')]),
      ];
      for (const [key, cur, orig] of cmp) {
        if (key === 'priority' && cur === '') continue; // 空欄は「変更なし」（priority に削除は無い）
        if (cur !== orig.trim()) fields[key] = cur;
      }
      // 固定検証コマンドの書き込みは正規形（verification_commands）のみ。旧 verify: が残っていたら
      // 同時に消す（正本を 2 か所にしない）。
      const verifyNow = $('rv-verify').value.trim();
      if (verifyNow !== fixedVerifyCommand(t)) {
        fields.verification_commands = verifyNow ? [verifyNow] : [''];
        if (String(t.verify || '').trim()) fields.verify = '';
      }
      // 受入基準は複数行フィールド＝**行の集合を丸ごと**送る（本体側も全行置換）。
      // 単値と同じ扱いにすると "a,b" の 1 行に潰れる。空なら [''] を送って削除。
      // 書き込みは正規形（task_acceptance_criteria）のみ。旧 acceptance 行が残っていたら
      // 同時に消し、正本が 2 か所にならないようにする（dual-write 禁止の逆向き）。
      const acceptanceNow = $('rv-acceptance')
        .value.split('\n')
        .map((s) => s.trim())
        .filter(Boolean);
      const acceptanceWas = acceptanceList(t);
      if (acceptanceNow.join('\n') !== acceptanceWas.join('\n')) {
        fields.task_acceptance_criteria = acceptanceNow.length ? acceptanceNow : [''];
        if (String((t.extra || {}).acceptance || '').trim()) fields.acceptance = [''];
      }
      const risksNow = $('rv-risks').value.split('\n').map((s) => s.trim()).filter(Boolean);
      const risksWas = String((t.extra || {}).risks || '').split('\n').map((s) => s.trim()).filter(Boolean);
      if (risksNow.join('\n') !== risksWas.join('\n')) {
        fields.risks = risksNow.length ? risksNow : [''];
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
              acceptance: $('rv-acceptance')
                .value.split('\n')
                .map((s) => s.trim())
                .filter(Boolean),
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
  // 削除（人の明示アクション）＝物理削除（ゴミ箱へ移動・needs も一緒に掃除）。
  // 分解は人の明示操作でしか走らないので、消したタスクが勝手に作り直されることはない。
  // 「二度と作り直させない」意思表示は ✕ 却下（archive・墓標・決定記録に残る）を使う。
  // 実行中（doing）・委譲実行中（offloaded）は main 側でも拒否される。
  const delBtn = $('btn-task-delete');
  if (delBtn) {
    delBtn.addEventListener('click', async () => {
      const downs = dependentsOf(p.backlog, t.id);
      const detach = downs.length
        ? `後続タスク（${downs.map((x) => x.id).join(', ')}）の先行指定からは自動で外れます。\n`
        : '';
      const yes = await confirmDialog(
        `タスク ${t.id}「${t.title}」をゴミ箱へ移動します。\n` +
          'バックログと要対応カードから外れます。操作の記録は残りません。\n' +
          detach +
          '「バックログを分解」を実行すると、似た内容のタスクがまた提案されることがあります。\n' +
          '作り直させたくないなら「✕ 却下」を、一時的に止めたいだけなら' +
          '「⏸ 保留にする」を使ってください。よろしいですか？'
      );
      if (!yes) return;
      const ok = await guard('タスク削除', async () => {
        await api.deleteTask(p.dir, t.id);
        toast(`${t.id} を削除しました（ゴミ箱へ移動）`, true);
        return true;
      });
      if (ok) {
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
  const dialog = $('dlg-task');
  dialog._inputSnapshot = taskDialogInputSnapshot(dialog);
  dialog.showModal();
}

// charter からのバックログ分解を要求する（分解はこの明示操作でしか走らない）。本体が次パスで
// charter を分解し、差分だけを backlog へ入れる（処理中・却下済みと類似は投入しない）。
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
    `${p.name}: ${versionText}からタスクを分解します。\n` +
      '進行中・却下済みと内容の重なるタスクは追加されません。完了済みと同種の作業は、やり直しとして作り直されます。\n' +
      'タスクの状態は書き換えません。反映は次の実行サイクルです（即時ではありません）。よろしいですか？'
  );
  if (!yes) return;
  const ok = await guard('バックログの分解', async () => {
    const res = await api.requestReplan(p.dir, 'agent-dashboard から再分解を要求', charter);
    uiLog('replan', res);
    toast('バックログの分解を依頼しました（次の実行で反映されます）', true);
    return true;
  });
  if (ok) {
    await reloadProject();
  }
}

// ---------------------------------------------------------------------------
// 観点メモ（notes/）。編集とタスク化を分け、選択したブロックだけを候補へ変換する。
// ---------------------------------------------------------------------------
const notesWorkspace = {
  notes: [],
  selectedName: '',
  savedBody: '',
  mode: 'edit',
  selectedBlocks: [],
  candidates: [],
  candidateSource: 'note',
};

const documentWorkspace = { name: '', content: '' };

function currentNote() {
  return notesWorkspace.notes.find((note) => note.name === notesWorkspace.selectedName) || null;
}

function noteIsDirty() {
  return $('note-body').value !== notesWorkspace.savedBody;
}

function setNotesStatus(message, error = false) {
  const el = $('notes-status');
  el.textContent = message || '';
  if (error) el.setAttribute('role', 'alert');
  else el.removeAttribute('role');
}

async function openNotesDialog() {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const select = $('notes-charter');
  fillCharterSelect(select, p, state.backlogCharter || '');
  updateCharterSelectContext('notes-charter', 'notes-charter-description');
  notesWorkspace.mode = 'edit';
  await renderNotesList(notesWorkspace.selectedName);
  $('dlg-notes').showModal();
  $('note-body').focus();
}

function updateCharterSelectContext(selectId, descriptionId) {
  const select = $(selectId);
  for (const option of select.options) option.textContent = option.value || '初版';
  const context = charterAssistContext(state.project, select.value);
  const description = $(descriptionId);
  if (context.goal) description.innerHTML = mdToHtml(context.goal);
  else description.textContent = 'このバージョンには説明がありません。';
}

async function renderNotesList(preferredName = '') {
  const p = state.project;
  const el = $('notes-list');
  if (!p || !el) return;
  try {
    notesWorkspace.notes = await api.listNotes(p.dir);
  } catch (err) {
    el.innerHTML = `<div class="muted" role="alert">メモを読めませんでした: ${esc(String(err.message || err))}</div>`;
    return;
  }
  const names = new Set(notesWorkspace.notes.map((note) => note.name));
  notesWorkspace.selectedName = names.has(preferredName)
    ? preferredName
    : names.has(notesWorkspace.selectedName)
      ? notesWorkspace.selectedName
      : (notesWorkspace.notes[0] && notesWorkspace.notes[0].name) || '';
  el.innerHTML = notesWorkspace.notes.length
    ? notesWorkspace.notes.map((note) => {
        const linked = Object.keys(note.links || {}).length;
        return `<button type="button" class="note-list-item ${note.name === notesWorkspace.selectedName ? 'active' : ''}"
          data-note-name="${esc(note.name)}" aria-pressed="${note.name === notesWorkspace.selectedName}">
          <span>${esc(note.name)}</span>${linked ? `<small>${linked} 項目タスク化済み</small>` : ''}
        </button>`;
      }).join('')
    : '<div class="muted notes-empty">まだメモはありません。</div>';
  for (const button of el.querySelectorAll('[data-note-name]')) {
    button.addEventListener('click', () => selectNote(button.dataset.noteName));
  }
  renderNoteWorkspace();
}

function renderNoteWorkspace() {
  const note = currentNote();
  notesWorkspace.savedBody = note ? String(note.body || '').replace(/\n$/, '') : '';
  $('note-body').value = notesWorkspace.savedBody;
  $('note-current-name').textContent = note ? note.name : '新しいメモ';
  setNotesMode(notesWorkspace.mode, true);
  setNotesStatus(note ? '保存済み' : '内容を書いて保存するとメモが作成されます');
}

async function selectNote(name) {
  if (name === notesWorkspace.selectedName) return;
  if (noteIsDirty()) {
    const discard = await confirmDialog('保存していない変更があります。破棄して別のメモを開きますか？');
    if (!discard) return;
  }
  notesWorkspace.selectedName = name;
  notesWorkspace.mode = 'edit';
  await renderNotesList(name);
}

async function newNote() {
  if (noteIsDirty()) {
    const discard = await confirmDialog('保存していない変更があります。破棄して新しいメモを作りますか？');
    if (!discard) return;
  }
  notesWorkspace.selectedName = '';
  notesWorkspace.savedBody = '';
  notesWorkspace.mode = 'edit';
  $('note-body').value = '';
  $('note-current-name').textContent = '新しいメモ';
  setNotesMode('edit', true);
  setNotesStatus('内容を書いて保存するとメモが作成されます');
  $('note-body').focus();
}

async function closeNotesDialog() {
  if (noteIsDirty()) {
    const discard = await confirmDialog('保存していない変更があります。破棄して閉じますか？');
    if (!discard) return;
  }
  $('dlg-notes').close();
}

async function saveNote() {
  const p = state.project;
  if (!p) return;
  const body = $('note-body').value.trim();
  if (!body) return setNotesStatus('メモの内容を入力してください', true);
  const button = $('btn-note-save');
  button.disabled = true;
  setNotesStatus('保存しています…');
  try {
    const note = currentNote();
    const result = note
      ? await api.updateNote(p.dir, note.name, body, note.mtime)
      : await api.writeNote(p.dir, '', body);
    uiLog(note ? 'updateNote' : 'writeNote', result);
    await renderNotesList(result.name);
    toast(`メモを保存しました（${result.name}）`, true);
  } catch (err) {
    setNotesStatus(`保存できませんでした: ${String(err.message || err)}`, true);
  } finally {
    button.disabled = false;
  }
}

function setNotesMode(mode, force = false) {
  if (mode === 'task' && !force && noteIsDirty()) {
    setNotesStatus('タスク化する前に変更を保存してください', true);
    return;
  }
  notesWorkspace.mode = mode === 'task' ? 'task' : 'edit';
  const taskMode = notesWorkspace.mode === 'task';
  $('notes-mode-edit').setAttribute('aria-pressed', String(!taskMode));
  $('notes-mode-task').setAttribute('aria-pressed', String(taskMode));
  $('note-edit-panel').classList.toggle('hidden', taskMode);
  $('note-task-panel').classList.toggle('hidden', !taskMode);
  $('btn-note-save').classList.toggle('hidden', taskMode);
  $('btn-note-candidates').classList.toggle('hidden', !taskMode);
  if (taskMode) renderNoteBlocks();
}

function taskExists(id) {
  const p = state.project;
  return [...((p && p.backlog) || []), ...((p && p.archive) || [])].some((task) => task.id === id);
}

function renderNoteBlocks() {
  const panel = $('note-task-panel');
  const note = currentNote();
  if (!note) {
    panel.innerHTML = '<div class="empty">先にメモを保存してください</div>';
    updateNoteSelectionCount();
    return;
  }
  const blocks = noteBlocks.parseNoteBlocks(notesWorkspace.savedBody);
  panel.innerHTML = blocks.length
    ? `<div class="note-block-list">${blocks.map((block) => {
        const linked = (note.links || {})[block.fingerprint];
        const taskIds = linked && Array.isArray(linked.taskIds) ? linked.taskIds : [];
        const ready = taskIds.length && taskIds.every(taskExists);
        const links = taskIds.map((id) =>
          `<button type="button" class="linklike mono" data-note-task-id="${esc(id)}">${esc(id)}</button>`
        ).join(' ');
        return `<label class="note-block ${taskIds.length ? 'linked' : ''}">
          <input type="checkbox" class="note-block-check" data-note-fingerprint="${esc(block.fingerprint)}"
            ${taskIds.length ? 'disabled' : ''} />
          <span class="note-block-body">
            ${block.heading ? `<small class="muted">${esc(block.heading)}</small>` : ''}
            <span class="task-prose">${proseHtml(block.text)}</span>
            ${taskIds.length ? `<span class="note-block-state">${ready ? 'タスク化済み' : '追加待ち'} ${links}</span>` : ''}
          </span>
        </label>`;
      }).join('')}</div>`
    : '<div class="empty">選択できる段落がありません</div>';
  for (const checkbox of panel.querySelectorAll('.note-block-check')) {
    checkbox.addEventListener('change', updateNoteSelectionCount);
  }
  for (const button of panel.querySelectorAll('[data-note-task-id]')) {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      $('dlg-notes').close();
      gotoTask(button.dataset.noteTaskId);
    });
  }
  updateNoteSelectionCount();
}

function selectedNoteBlocks() {
  const selected = new Set(
    [...document.querySelectorAll('#note-task-panel .note-block-check:checked')]
      .map((input) => input.dataset.noteFingerprint)
  );
  return noteBlocks.parseNoteBlocks(notesWorkspace.savedBody)
    .filter((block) => selected.has(block.fingerprint));
}

function updateNoteSelectionCount() {
  const count = selectedNoteBlocks().length;
  const button = $('btn-note-candidates');
  button.disabled = count === 0;
  button.textContent = count ? `選択した ${count} 項目から候補を作る` : '選択した項目から候補を作る';
}

async function buildNoteCandidates() {
  const p = state.project;
  const blocks = selectedNoteBlocks();
  if (!p || !blocks.length) return;
  const items = blocks.map(({ heading, text }) => ({ heading, text }));
  return buildSourceCandidates({
    source: { kind: 'note', name: currentNote().name, content: items, fallbackItems: items },
    charter: $('notes-charter').value,
    button: $('btn-note-candidates'),
    setStatus: setNotesStatus,
    selectedBlocks: blocks,
  });
}

function setDocumentStatus(message, error = false) {
  const el = $('document-task-status');
  el.textContent = message || '';
  if (error) el.setAttribute('role', 'alert');
  else el.removeAttribute('role');
}

function openDocumentTaskDialog() {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  documentWorkspace.name = '';
  documentWorkspace.content = '';
  $('document-task-name').textContent = '文書が選択されていません';
  $('document-task-body').value = '';
  $('btn-document-candidates').disabled = true;
  setDocumentStatus('Markdown またはテキスト文書を選択してください');
  fillCharterSelect($('document-charter'), p, state.backlogCharter || '');
  updateCharterSelectContext('document-charter', 'document-charter-description');
  $('dlg-document-task').showModal();
  $('btn-document-pick').focus();
}

function closeDocumentTaskDialog() {
  documentWorkspace.name = '';
  documentWorkspace.content = '';
  $('dlg-document-task').close();
}

async function pickDocumentForTasks() {
  setDocumentStatus('文書を読み込んでいます…');
  try {
    const result = await api.pickBacklogDocument();
    if (result.canceled) return setDocumentStatus('文書の選択をキャンセルしました');
    documentWorkspace.name = result.name;
    documentWorkspace.content = result.content;
    $('document-task-name').textContent = result.name;
    $('document-task-body').value = result.content;
    $('btn-document-candidates').disabled = false;
    setDocumentStatus('文書を読み込みました');
  } catch (err) {
    setDocumentStatus(`文書を読めませんでした: ${String(err.message || err)}`, true);
  }
}

async function buildDocumentCandidates() {
  const p = state.project;
  if (!p || !documentWorkspace.content) return;
  return buildSourceCandidates({
    source: { kind: 'document', name: documentWorkspace.name, content: documentWorkspace.content },
    charter: $('document-charter').value,
    button: $('btn-document-candidates'),
    setStatus: setDocumentStatus,
  });
}

async function buildSourceCandidates({ source, charter, button, setStatus, selectedBlocks = [] }) {
  const p = state.project;
  button.disabled = true;
  setStatus('タスク候補を作っています…');
  try {
    const result = await api.agentTaskAssist({
      dir: p.dir,
      mode: 'source-task-candidates',
      context: {
        charter: charterAssistContext(p, charter),
        backlog: backlogAssistRows(p),
        source,
      },
    });
    const fields = result.fields || {};
    const tasks = fields.tasks || [];
    if (!tasks.length) {
      setStatus(fields.rationale || '実行可能な候補は見つかりませんでした');
      return;
    }
    notesWorkspace.candidateSource = source.kind;
    notesWorkspace.selectedBlocks = selectedBlocks;
    notesWorkspace.candidates = tasks.map((task) => ({ ...task, addedId: '' }));
    renderNoteCandidates(fields.rationale || '');
    $('dlg-note-candidates').showModal();
    setStatus(`${tasks.length} 件の候補を作成しました`);
  } catch (err) {
    const retry = source.kind === 'note' ? '選択したまま' : '文書を保持したまま';
    setStatus(`候補を作成できませんでした: ${String(err.message || err)}。${retry}再試行できます`, true);
  } finally {
    button.disabled = false;
  }
}

function renderNoteCandidates(rationale = '') {
  const list = $('note-candidates-list');
  list.innerHTML = `${rationale ? `<p class="muted">${esc(rationale)}</p>` : ''}${notesWorkspace.candidates
    .map((task, index) => `<section class="note-candidate" data-note-candidate="${index}">
      <label class="note-candidate-select"><input type="checkbox" class="note-candidate-check" checked />追加する</label>
      <div class="field"><label>タスク名</label><input class="note-candidate-title" value="${esc(task.title)}" /></div>
      <div class="field"><label>変更してほしいこと</label><textarea class="note-candidate-desc" rows="3">${esc(String(task.desc || '').replace(/ ⏎ /g, '\n'))}</textarea></div>
      <div class="field"><label>完了条件</label><textarea class="note-candidate-accept" rows="3">${esc((task.acceptance || []).join('\n'))}</textarea></div>
    </section>`).join('')}`;
  $('note-candidates-status').textContent = '';
}

async function submitNoteCandidates() {
  const p = state.project;
  const source = notesWorkspace.candidateSource;
  const note = source === 'note' ? currentNote() : null;
  if (!p || (source === 'note' && !note)) return;
  const rows = [...document.querySelectorAll('#note-candidates-list [data-note-candidate]')]
    .filter((row) => row.querySelector('.note-candidate-check').checked);
  if (!rows.length) {
    $('note-candidates-status').textContent = '追加する候補を選択してください';
    return;
  }
  const button = $('btn-note-candidates-submit');
  button.disabled = true;
  const added = [];
  const failed = [];
  for (const [position, row] of rows.entries()) {
    const index = Number(row.dataset.noteCandidate);
    const candidate = notesWorkspace.candidates[index];
    if (candidate.addedId) continue;
    const title = row.querySelector('.note-candidate-title').value.trim();
    if (!title) {
      failed.push('タスク名が空の候補');
      continue;
    }
    const id = `${source}-${Date.now().toString(36)}-${position + 1}`;
    const description = row.querySelector('.note-candidate-desc').value.trim();
    const acceptance = row.querySelector('.note-candidate-accept').value
      .split('\n').map((value) => value.trim()).filter(Boolean);
    try {
      await api.enqueueTask(p.dir, {
        id,
        title,
        desc: description.replace(/\r?\n/g, ' ⏎ '),
        why: candidate.why || '',
        task_acceptance_criteria: acceptance,
        priority: candidate.priority,
        after: (candidate.after || []).join(', '),
        charter: $(source === 'note' ? 'notes-charter' : 'document-charter').value,
      });
      candidate.addedId = id;
      added.push(id);
      row.classList.add('added');
      row.querySelector('.note-candidate-check').disabled = true;
    } catch (err) {
      failed.push(`${title}: ${String(err.message || err)}`);
    }
  }
  const allTaskIds = notesWorkspace.candidates.map((candidate) => candidate.addedId).filter(Boolean);
  if (added.length) {
    if (note) {
      await api.markNoteBlocks(p.dir, note.name, notesWorkspace.selectedBlocks.map((block) => ({
        fingerprint: block.fingerprint,
        taskIds: allTaskIds,
      })));
      await renderNotesList(note.name);
      setNotesMode('task', true);
    }
    toast(`${added.length} 件のタスクを追加しました（承認するまで実行されません）`, true);
  }
  $('note-candidates-status').textContent = failed.length
    ? `${added.length} 件を追加しました。失敗: ${failed.join(' / ')}`
    : `${added.length} 件をタスク一覧へ追加しました`;
  if (!failed.length) {
    $('dlg-note-candidates').close();
    if (source === 'document') closeDocumentTaskDialog();
  }
  button.disabled = false;
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
  let planned;
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
  // 構造化フォームの入力（案5・案3）。再投入では元の値を引き継ぎ、通常は空にする。
  // desc（作業内容の概要）は複数行 textarea なので ⏎ 規約を改行へ戻して見せる。
  const setEnq = (id, v) => { const el = $(id); if (el) el.value = v || ''; };
  setEnq('enq-verify-template', prefill.verify_template);
  setEnq('enq-desc', String(prefill.desc || '').replace(/\s*⏎\s*/g, '\n'));
  setEnq('enq-why', prefill.why);
  setEnq('enq-scope', prefill.scope);
  setEnq('enq-out_of_scope', prefill.out_of_scope);
  setEnq('enq-risks', Array.isArray(prefill.risks) ? prefill.risks.join('\n') : prefill.risks);
  setEnq('enq-size', prefill.size);
  fillCharterSelect($('enq-charter'), state.project, prefill.charter || '');
  updateCharterSelectContext('enq-charter', 'enq-charter-description');
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

async function aiEnqueueGuideAssist() {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const title = $('enq-title').value.trim();
  if (!title) return toast('タスク名を書いてから必須項目を補完してください');
  if (state.assistBusy) return;
  state.assistBusy = true;
  const button = $('btn-enq-guide-assist');
  const status = $('enq-guide-assist-status');
  button.disabled = true;
  status.textContent = '計画レビュー情報を補完しています…';
  try {
    const current = {
      why: $('enq-why').value.trim(),
      desc: $('enq-desc').value.trim(),
      scope: $('enq-scope').value.trim(),
      risks: $('enq-risks').value.trim(),
      acceptance: $('enq-accept').value.split('\n').map((s) => s.trim()).filter(Boolean),
      size: $('enq-size').value,
    };
    const res = await api.agentTaskAssist({
      dir: p.dir,
      mode: 'task-guide',
      context: {
        charter: charterAssistContext(p, $('enq-charter').value),
        backlog: backlogAssistRows(p),
        task: { title, ...current },
      },
    });
    const fields = res.fields || {};
    let filled = 0;
    for (const [id, key] of [
      ['enq-why', 'why'], ['enq-desc', 'desc'], ['enq-scope', 'scope'], ['enq-risks', 'risks'],
    ]) {
      if (!$(id).value.trim() && String(fields[key] || '').trim()) {
        $(id).value = String(fields[key]).trim().replace(/\s*⏎\s*/g, '\n');
        filled += 1;
      }
    }
    if (!current.acceptance.length && Array.isArray(fields.acceptance) && fields.acceptance.length) {
      $('enq-accept').value = fields.acceptance.join('\n');
      filled += 1;
    }
    if (!current.size && fields.size) {
      $('enq-size').value = fields.size;
      filled += 1;
    }
    status.textContent = filled
      ? `${filled} 項目を補完しました。内容を確認してから追加してください`
      : '補完できる項目はありませんでした';
  } catch (err) {
    status.textContent = '';
    toast(`計画レビュー情報の補完に失敗しました: ${err.message || err}`);
  } finally {
    state.assistBusy = false;
    button.disabled = false;
  }
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
  // 統一 verify の正規形だけを書く: 受入基準は task_acceptance_criteria（1 行 1 基準）、
  // 固定コマンドは verification_commands。旧 verify / accept キーは新規書き込みに使わない。
  const acceptLines = $('enq-accept').value.split('\n').map((s) => s.trim()).filter(Boolean);
  const verifyCmd = $('enq-verify').value.trim();
  const spec = {
    title: $('enq-title').value,
    task_acceptance_criteria: acceptLines,
    verification_commands: verifyCmd ? [verifyCmd] : [],
    priority: $('enq-priority').value,
    note: $('enq-note').value,
    id: $('enq-id').value,
    after: $('enq-after').value,
    charter: $('enq-charter').value,
    workspace: $('enq-workspace') ? $('enq-workspace').value : '',
    ...Object.fromEntries(ENQUEUE_PASSTHROUGH_KEYS.map((k) => [k, extra[k] || ''])),
  };
  // 構造化フォーム（案5・案3）。passthrough の空値を上書きするため spread の後に読む。
  // desc（複数行 textarea）は md の 1 行 = 1 フィールド規約に合わせ改行を ⏎ へ畳む。
  const enqField = (id) => { const el = $(id); return el ? el.value.trim() : ''; };
  spec.verify_template = enqField('enq-verify-template') || spec.verify_template || '';
  spec.desc = enqField('enq-desc').replace(/\r?\n/g, ' ⏎ ') || spec.desc || '';
  spec.why = enqField('enq-why') || spec.why || '';
  spec.scope = enqField('enq-scope') || spec.scope || '';
  spec.out_of_scope = enqField('enq-out_of_scope') || spec.out_of_scope || '';
  spec.risks = enqField('enq-risks').split('\n').map((s) => s.trim()).filter(Boolean);
  spec.size = enqField('enq-size');
  // 作成時 lint（案5・非ブロック）: 情報不足・曖昧 accept を投入前に見せ、続行するか監視者が選ぶ。
  try {
    const warnings = await api.lintTask(spec);
    if (Array.isArray(warnings) && warnings.length) {
      const body = warnings.map((w) => `・${w.message}`).join('\n');
      const proceed = await confirmDialog(
        `このタスクは次の点で情報が不足しています:\n\n${body}\n\nこのまま追加しますか？（あとで編集もできます）`
      );
      if (!proceed) return; // 追加を中断してフォームに戻る（ブロックではなく監視者の選択）
    }
  } catch (e) {
    uiLog('lint skipped', String((e && e.message) || e));
  }
  const ok = await guard('タスク追加', async () => {
    const res = await api.enqueueTask(p.dir, spec);
    uiLog('enqueue', res);
    toast(
      `タスクを追加しました: ${res.spec.title}\n` +
        ((res.spec.verification_commands || []).length || (res.spec.task_acceptance_criteria || []).length
          ? '（次の実行サイクルで一覧に載ります）'
          : '（完了条件が無いため、取り込み後に内容の確認が必要になります）'),
      true
    );
    return true;
  });
  if (ok) {
    $('dlg-enqueue').close();
    await reloadProject();
  }
}
