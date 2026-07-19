'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// タブ: フロー（agent-flow のタスクグラフ）
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

// run の終端 status（flow.js の TERMINAL と同一）。フロータブのフィルタ判定に使う
const TERMINAL_RUN_STATES = new Set(['done', 'failed', 'canceled']);

// フロータブの run フィルタ。完了 run は agent-flow の掃除後もアーカイブ（ビュアー保管庫）から
// 表示できるため、既定は「進行中（非終端）」に絞って一覧のノイズを抑える。
const FLOW_FILTERS = [
  ['active', '実行中'],
  ['action', '要確認'],
  ['done', '完了'],
];

function flowGroupBucket(group) {
  const advice = runAdvice(group.latest, group);
  if (['human', 'manual', 'restart'].includes(advice.kind)) return 'action';
  return TERMINAL_RUN_STATES.has(String(group.latest.status)) ? 'done' : 'active';
}

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

// agent-flow daemon の稼働バッジ。
//   via='lock'          … 同一ホストのロックファイル（pid 生存）で確定判定
//   via='status-local'  … 同一マシン（ホスト一致 or Windows×WSL）の status.json
//   via='status-sync'   … state_git（鏡）越しに同期された status.json による推定（同期遅延を許容）
//   via='none'          … 判定材料なし
function daemonBadge() {
  const d = state.flowDaemon;
  if (!d) return '';
  // 判定根拠（ロックファイル・pid・同期経由の生存信号）は内部情報なのでログへ
  uiLogOnChange('flowDaemon', d);
  const synced = d.via === 'status-sync';
  if (d.running === true) {
    // 稼働中は「別マシンか」「orchestrator/worker が何基か」を1つの括弧にまとめて添える
    // （数は status.json 由来のベストエフォート。取れないときは従来どおり別マシン表記のみ）。
    const bits = [];
    if (synced) bits.push('別マシン');
    if (Number.isFinite(d.orchestrators)) bits.push(`orchestrator ${d.orchestrators}`);
    if (Number.isFinite(d.workers)) bits.push(`worker ${d.workers}`);
    const suffix = bits.length ? `（${bits.join('・')}）` : '';
    const title = synced
      ? `別マシンで稼働（最終確認 ${fmtAgoSec(d.ageSec)}）`
      : d.via === 'status-local'
        ? `このマシンで稼働（最終確認 ${fmtAgoSec(d.ageSec)}）`
        : 'このマシンで稼働中';
    return `<span class="status-chip st-running" title="${title}">実行エンジン: 稼働中${suffix}</span>`;
  }
  if (d.running === false) {
    if (synced) {
      return `<span class="status-chip" title="最終確認 ${fmtAgoSec(d.ageSec)}・最近の稼働を確認できません">実行エンジン: 不明</span>`;
    }
    if (d.via === 'none') {
      return `<span class="status-chip" title="このマシンでは稼働を確認できません">実行エンジン: 停止中か不明</span>`;
    }
    return `<span class="status-chip st-closed">実行エンジン: 停止</span>`;
  }
  return `<span class="status-chip" title="稼働状態を読み取れませんでした">実行エンジン: 不明</span>`;
}

// run に対応するバックログ／アーカイブのタスク（{ task, scope } か null）。
function taskOfRun(run) {
  const p = state.project;
  if (!p || !run || !run.taskId) return null;
  const key = sanitizeTaskId(run.taskId);
  const t = (p.backlog || []).find((x) => sanitizeTaskId(x.id) === key);
  if (t) return { task: t, scope: 'backlog' };
  const a = (p.archive || []).find((x) => sanitizeTaskId(x.id) === key);
  return a ? { task: a, scope: 'archive' } : null;
}

// タスクが人の判断待ち（検収・実行前レビュー・要対応）のときの advice。
// delivery_review で verify=PASS したあとは run 自体が done になるが、タスクは review のまま
// 残る。run.status=done を先に見ると「完了」扱いになり操作待ちから消えるため、タスク状態を優先する。
function humanWaitingAdvice(task) {
  if (task.status === 'review') {
    return {
      kind: 'human',
      cls: 'act',
      chip: '🖐 検収待ち',
      taskId: task.id,
      text:
        `実行の成果は揃っています。元のタスク ${task.id} が検収待ちのため、ここで待っていても完了しません。` +
        '「要対応」タブで成果を確認して承認すると完了になります。',
    };
  }
  if (task.status === 'proposed') {
    return {
      kind: 'human',
      cls: 'act',
      chip: '🖐 計画承認待ち',
      taskId: task.id,
      text:
        `元のタスク ${task.id} が実行前レビュー待ちのため、ここで待っていても動きません。` +
        '「要対応」タブで計画を承認すると実行が始まります。',
    };
  }
  return {
    kind: 'human',
    cls: 'act',
    chip: '🖐 あなたの判断待ち',
    taskId: task.id,
    text:
      `元のタスク ${task.id} が人の判断待ち（${statusLabel(task.status)}）のため、ここで待っていても再実行されません。` +
      '「要対応」タブで回答すると動き出します。',
  };
}

// 失敗トリアージ（agent-flow が meta.failure_reason に載せる決定的タグ [agent-error:<class>]）。
// 環境要因ならタスク状態（blocked 等）より先に「何を直すか」を言い切る。
function agentErrorAdvice(run, found) {
  const failure = String(run.failureReason || '');
  const tri = /\[agent-error:(control|quota|auth|env)\]/.exec(failure);
  if (!tri) return null;
  // 旧 run は meta.failure_reason が quota の汎用文言まで丸められている。
  // ノード出力には発生元タグが残るため、それも合わせて判定する。
  const nodeOutputs = Object.values(run.nodes || {})
    .map((node) => String((node && node.output) || ''))
    .join('\n');
  const detail = `${failure}\n${nodeOutputs}`;
  if (tri[1] === 'control' || (tri[1] === 'quota' && /\[agent-control\]/.test(detail))) {
    const paused = /lifecycle=pause\b/.test(detail);
    return {
      kind: 'human',
      cls: 'act',
      chip: paused ? '⏸ 実行一時停止中' : '⏹ 実行停止中',
      text:
        `AI の利用上限ではありません。オーケストレーション設定で対象の実行が${paused ? '一時停止' : '停止'}されたため、` +
        'run が失敗終了しました。全体設定の「エージェント」で対象ワークロードを「実行」に戻し、' +
        '要対応タブから再開してください（完了済みの工程は温存されています）。',
      taskId: found && found.task ? found.task.id : null,
    };
  }
  if (tri[1] === 'quota' && /\[node-budget\]/.test(detail)) {
    return {
      kind: 'human',
      cls: 'act',
      chip: '⏲ ノード予算上限',
      text:
        'AI サービス側の利用上限ではありません。このマシンに設定した実行時間またはトークン予算に達しました。' +
        '全体設定の「エージェント」にあるオーケストレーション予算を確認し、上限を変更するか期間更新後に再開してください。',
      taskId: found && found.task ? found.task.id : null,
    };
  }
  const map = {
    quota: ['⏲ AI 利用上限', 'AI サービス側の利用上限に達したため止まりました。時間をおく（またはプランを' +
      '見直す）と回復します。回復後、要対応タブで該当タスクを承認すると続きから再開します' +
      '（完了済みの工程は温存されています）。'],
    auth: ['🔑 認証切れ', 'エージェント CLI の認証が切れたため止まりました。再ログインしてから、' +
      '要対応タブで該当タスクを承認すると続きから再開します（完了済みの工程は温存されています）。'],
    env: ['⚙ 実行環境の問題', 'エージェント CLI の実行環境（CLI の導入・モデル名・PATH）に問題が' +
      'あり止まりました。環境を直してから、要対応タブで該当タスクを承認すると続きから再開します。'],
  };
  const [chip, text] = map[tri[1]];
  return {
    kind: 'human',
    cls: 'act',
    chip,
    text,
    taskId: found && found.task ? found.task.id : null,
  };
}

// この run について「次に何が起きるか・あなたの出番はあるか」を決定的に言い切る。
// フロー画面の第一言語。状態チップ（実行中/失敗/応答なし）は機械の状態でしかなく、
// 「放置すれば自動で直るのか・自分が押すべきなのか」を人が推測させられていた
// （同じ「応答なし」でも、本体が動いていれば自動再開・needs 待ちなら人の番、と正解が違う）。
// 判定材料は run（status/alive/counts）・系統（最新試行か）・タスク（status/last_run）・
// 本体の稼働（liveness）で、すべて手元のデータから決定的に出す。
// kind: watch=見守る / none=何もしなくてよい / auto=自動でやり直される（操作不要）
//       restart=本体を動かせば自動 / human=要対応タブで判断 / manual=あなたの操作待ち
//       old=古い試行（見るだけ）
function runAdvice(run, group) {
  const p = state.project || {};
  const live = p.liveness || {};
  const st = String(run.status);
  const latest = group ? group.latest : run;
  if (latest.runId !== run.runId) {
    return { kind: 'old', cls: 'muted', chip: '🗂 古い試行', latestId: latest.runId,
      text: `新しい試行（${shortRunId(latest.runId)}）に引き継ぎ済みです。この画面は記録 — 操作は不要で、削除しても安全です。` };
  }
  const found = taskOfRun(run);
  if (found && found.scope === 'archive') {
    return { kind: 'none', cls: 'ok', chip: '✔ タスクは完了済み',
      text: `元のタスク ${found.task.id} は既に完了しています。この run は途中の記録 — 操作は不要です。` };
  }
  // 環境要因の失敗は blocked/review より先（認証切れ等を「判断待ち」で誤誘導しない）。
  // done（検収待ち）には付けない — delivery_review の完了 run を環境障害扱いにしない。
  const stalled = run.alive === false;
  const envAdvice = agentErrorAdvice(run, found);
  if (envAdvice && (stalled || st === 'failed')) return envAdvice;
  // 人の判断待ちは run の done / 実行中 / 記録 より優先する（検収待ちが要確認から消えないように）
  if (found && ['review', 'blocked', 'proposed'].includes(found.task.status)) {
    return humanWaitingAdvice(found.task);
  }
  if (run.archived) {
    return { kind: 'none', cls: 'ok', chip: '📦 記録',
      text: '完了後に保存された記録です。見るだけで、操作はありません。' };
  }
  if (st === 'done') {
    return { kind: 'none', cls: 'ok', chip: '✔ 完了',
      text: '成果は確定済みです。操作は要りません。' };
  }
  if (!stalled && !TERMINAL_RUN_STATES.has(st)) {
    // park & poll: 承認待ちで保留中（lease 生存）＝実行エンジンは動いているが人の番
    if ((run.counts && run.counts.parked) > 0) {
      return {
        kind: 'human',
        cls: 'act',
        chip: '🖐 承認待ち',
        taskId: found && found.task ? found.task.id : null,
        text:
          '工程が承認待ちで保留中です。GitLab のレビューを進めるか、要対応タブ／工程詳細から対応してください。',
      };
    }
    return { kind: 'watch', cls: 'ok', chip: '▶ 実行中',
      text: '実行エンジンが応答しています。操作は不要 — このまま見守れます。' };
  }
  // ここから「止まっている」（failed / canceled / 非終端なのに応答なし）
  if (found) {
    const task = found.task;
    const lastRun = String((task.extra && task.extra.last_run) || '');
    const doneCount = (run.counts && run.counts.done) || 0;
    if (['ready', 'doing', 'offloaded', 'inbox'].includes(task.status)) {
      // canceled は続きから再開できない（新 run 固定）。failed/stalled だけ部分やり直し。
      const resume = st !== 'canceled' && (!lastRun || lastRun === run.runId);
      const how = resume
        ? `失敗・未実行の工程だけをやり直します（完了済み ${doneCount} 件は温存）`
        : '新しい実行としてやり直します';
      if (live.paused) {
        return { kind: 'restart', cls: 'warn', chip: '一時停止中', stopped: false,
          text: `一時停止中です。「再開」を押すと、${how}。` };
      }
      if (live.running) {
        return { kind: 'auto', cls: 'ok', chip: '自動で再試行します',
          text: `操作は不要です。${how}。ほかの作業が実行中なら、その後に順番に進みます。` };
      }
      const ago = live.ageSec != null && live.ageSec > 0
        ? `最終確認は ${Math.max(1, Math.round(live.ageSec / 60))} 分前です。` : '';
      if (live.via === 'status-sync') {
        // 別マシンの本体は、長い作業（LLM 実行）中は status.json を更新できない＝
        // 「停止」と言い切れない。予約（↻）は本体が生きていれば拾われる。
        return { kind: 'restart', cls: 'warn', chip: '📡 本体（別マシン）の応答が途絶えています',
          stopped: true,
          text: `${ago}長い作業の途中か、停止しています。↻ を押すと予約として受け付けられ、` +
            `本体が動いていれば順番に${how}。動いていなければ本体のマシンで agent-project start を` +
            '実行してください（「▶ 本体を起動」はこの PC で起動します）。' };
      }
      return { kind: 'restart', cls: 'warn', chip: '自動実行は停止中', stopped: true,
        text: `${ago}「自動実行を開始」を押すと、${how}。` };
    }
    if (task.status === 'rejected') {
      return { kind: 'none', cls: 'muted', chip: '✋ 却下済み',
        text: `元のタスク ${task.id} は却下されています。この run はその記録です。` };
    }
  }
  if (st === 'canceled') {
    return { kind: 'manual', cls: 'muted', chip: '■ 中止済み',
      text: '人が止めた実行です。やり直したいときだけ ↻ を押してください（自動では動きません）。' };
  }
  return { kind: 'manual', cls: 'act', chip: '🖱 あなたの操作待ち',
    text: 'この実行は自動では再開されません。「↻ 失敗した工程だけやり直す」を押すと、失敗・未実行の工程だけが再実行されます（完了済みは温存）。' };
}

// run の再実行操作について、表示可否・文言・説明を一つのモデルにまとめる。
// 工程詳細と概要の操作欄が別々に条件判定すると、存在しないボタンを案内してしまうため。
function flowRetryUi(run, advice) {
  const doneCount = (run.counts && run.counts.done) || 0;
  const failedCount = (run.counts && run.counts.failed) || 0;
  const isStalled = run.alive === false && run.status !== 'done';
  const canRetry = run.status === 'failed' || run.status === 'canceled' || isStalled;
  const remainCount = failedCount
    + ((run.counts && run.counts.pending) || 0)
    + ((run.counts && run.counts.waiting) || 0)
    + ((run.counts && run.counts.parked) || 0);
  const partial = canRetry && doneCount > 0 && run.status !== 'canceled';
  const label = run.status === 'canceled'
    ? '↻ 新しくやり直す'
    : partial
      ? `↻ 失敗した工程だけやり直す（残り ${remainCount} 件）`
      : '↻ 同じ内容でやり直す';
  const title = run.status === 'canceled'
    ? '中止した実行の続きからは再開できません。タスクを積み直して新しい実行を始めます'
    : partial
      ? `失敗・未実行の工程だけを実行し直します。成功した ${doneCount} 件はそのまま使います（作り直しません）`
        + (advice.kind === 'auto' ? '\n※ 放置しても本体が自動で同じことをします（このボタンは前倒し指示）' : '')
      : '同じ内容でやり直します（タスクを積み直して本体に実行させます）';
  return {
    canRetry,
    partial,
    doneCount,
    remainCount,
    label,
    title,
    show: !run.archived && canRetry && !['human', 'old'].includes(advice.kind),
  };
}

// 一覧の行・詳細バナー共通の advice チップ HTML（見守り系は一覧では出さない＝騒がない）
function adviceChip(a) {
  return `<span class="advice-chip advice-${a.cls}" title="${esc(a.text)}">${esc(a.chip)}</span>`;
}

function renderFlow() {
  const p = state.project;
  const el = $('tab-flow');
  if (!p) {
    el.innerHTML = '';
    return;
  }
  // 実行データの発見経緯（探索した候補パス）は内部情報なのでログへ
  uiLogOnChange(`flowBus:${p.dir}`, { busDir: p.busDir, source: p.busSource, candidates: p.busCandidates });
  if (!state.flowRuns.length) {
    el.innerHTML = '<div class="empty"><strong>実行中の作業はありません</strong><span>タスクが開始されると、ここに進捗が表示されます。</span></div>';
    return;
  }
  // 同一タスクのリトライ（req-…-r0/r1/…）は「意味的に同一」なので系統でまとめ、
  // 最新試行を見出しにして過去の試行はリトライ・ピルで畳む。素の run は単独系統。
  // フィルタ（既定: アクティブ）は系統の最新試行の status で判定する。
  const groups = lineageGroups(state.flowRuns);
  const matchesFilter = (g) => flowGroupBucket(g) === state.flowFilter;
  const shownGroups = groups.filter(matchesFilter);
  const filterCount = (key) => groups.filter((g) => flowGroupBucket(g) === key).length;
  const filterChips = FLOW_FILTERS.map(
    ([key, label]) =>
      `<button class="queue-filter ${state.flowFilter === key ? 'active' : ''}" data-flow-filter="${key}"
        aria-pressed="${state.flowFilter === key}"><span>${label}</span><strong>${filterCount(key)}</strong></button>`
  ).join('');
  const runList = shownGroups
    .map((g) => {
      const r = g.latest;
      const pct = Math.round(r.progress * 100);
      // 「応答なし」だけでは放置してよいのか押すべきなのか分からない。
      // 一覧では advice（次に起きること）を状態チップの代わりに言い切る。
      // 見守り系（実行中/完了/記録）は statusChip が既に言っているので重ねない。
      const advice = runAdvice(r, g);
      const adviceBit = ['watch', 'none'].includes(advice.kind) ? '' : ` ${adviceChip(advice)}`;
      const adviceLine = ['human', 'manual', 'restart'].includes(advice.kind)
        ? `<div class="advice-line advice-${advice.cls}">${esc(advice.text)}</div>`
        : '';
      const taskLink = r.taskId
        ? ` <button class="badge task-link" data-goto-task="${esc(r.taskId)}" title="元のタスクを開く">タスク ${esc(r.taskId)}</button>`
        : '';
      const retryStrip =
        g.attempts.length > 1
          ? `<div class="run-retries" title="この作業のやり直し履歴">試行 ${g.attempts.length}: ${g.attempts
              .slice()
              .reverse()
              .map((a) => runPill(a, a.runId === state.flowRunId))
              .join('')}</div>`
          : r.inheritedFrom
            ? `<div class="muted" title="引き継ぎ元の実行">↩ 引き継ぎ元 <span class="mono">${esc(r.inheritedFrom)}</span></div>`
            : '';
      const archivedBadge = r.archived
        ? ' <span class="badge" title="完了後に保存された記録です">記録</span>'
        : '';
      const outcome = runTaskOutcome(p, r);
      const finalVerificationFailure = runFinalVerificationFailure(p, r);
      return `<div class="run-item ${state.flowRunId === r.runId ? 'selected' : ''}" data-run="${esc(r.runId)}"
        role="button" tabindex="0" aria-pressed="${state.flowRunId === r.runId}">
        <div class="run-item-head"><span>${runTaskOutcomeCompactHtml(outcome)}${archivedBadge}${adviceBit}</span><span class="muted">${fmtAgo(r.updatedAt || r.createdAt)}</span></div>
        <div class="req">${prosePreview(r.request, 110) || '<span class="muted">内容なし</span>'}</div>
        <div class="progress"><div style="width:${pct}%"></div></div>
        <div class="muted">工程完了 ${r.counts.done}/${r.total}・失敗 ${r.counts.failed}・実行中 ${r.counts.claimed}${taskLink}</div>
        ${finalVerificationFailureHtml(finalVerificationFailure, true)}
        ${adviceLine}
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
    detail: ($('flow-view-body') || {}).scrollTop || 0,
    graphX: prevGraph ? prevGraph.scrollLeft : 0,
    graphY: prevGraph ? prevGraph.scrollTop : 0,
  };
  el.innerHTML = `<div class="queue-summary flow-summary">${filterChips}</div>
  <div id="flow-layout" class="${state.flowMobileDetail ? 'show-detail' : ''}">
    <div id="flow-runs">${runList || `<div class="empty"><strong>該当する作業はありません</strong><span>${esc((FLOW_FILTERS.find(([k]) => k === state.flowFilter) || ['', state.flowFilter])[1])}の作業がここに表示されます。</span></div>`}</div>
    <div id="flow-detail">${renderFlowDetail()}</div>
  </div>`;
  $('flow-runs').scrollTop = prevScroll.runs;
  if ($('flow-view-body')) $('flow-view-body').scrollTop = prevScroll.detail;
  const graph = $('graph-box');
  if (graph) {
    graph.scrollLeft = prevScroll.graphX;
    graph.scrollTop = prevScroll.graphY;
  }

  for (const chip of el.querySelectorAll('[data-flow-filter]')) {
    chip.addEventListener('click', async () => {
      state.flowFilter = chip.dataset.flowFilter;
      const first = groups.find((g) => flowGroupBucket(g) === state.flowFilter);
      const currentVisible = groups.some(
        (g) => g.latest.runId === state.flowRunId && flowGroupBucket(g) === state.flowFilter
      );
      if (!currentVisible) {
        if (first) await selectFlowRun(first.latest.runId);
        else {
          state.flowRunId = null;
          state.flowRun = null;
          renderFlow();
        }
      } else {
        renderFlow();
      }
    });
  }
  for (const item of el.querySelectorAll('.run-item[data-run]')) {
    item.addEventListener('click', (ev) => {
      // プレビュー内リンク等の操作は run 選択にしない
      if (ev.target.closest('a, button')) return;
      selectFlowRun(item.dataset.run);
    });
    item.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        selectFlowRun(item.dataset.run);
      }
    });
  }
  bindFlowDetail(el);
  bindRelationship(el); // リトライ・ピル／タスクリンク／パンくずのクリック配線（行クリックより優先）
}

async function selectFlowRun(runId) {
  state.flowRunId = runId;
  state.flowNodeId = null;
  state.flowDetailView = 'overview';
  state.flowMobileDetail = true;
  state.flowRun = await guard('実行内容の読み込み', () => api.flowRun(state.project.dir, state.project.busDir, runId));
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
  if (!run || run.archived || !gitlabConfigured()) return; // アーカイブは読み取り専用の写し＝突き合わせ対象外
  if (!run.gitlabish) return; // gitlab executor の run 以外にイシューは存在しない＝API を叩かない
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
    if (!auto) toast('この実行には対応する GitLab リポジトリがありません');
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
  if (!fr || !fr.run) return '<div class="empty">左の一覧から実行を選択するとタスクグラフを表示します</div>';
  const run = fr.run;
  const outcome = runTaskOutcome(state.project, run);
  const finalVerificationFailure = runFinalVerificationFailure(state.project, run);
  const pct = Math.round(run.progress * 100);
  const legend = Object.entries(FLOW_STATE_LABEL)
    .map(
      ([st, label]) =>
        `<span class="key"><span class="sw state-sw-${st}" style="background:${swColor(st)}"></span>${label}</span>`
    )
    .join('');
  // 工程説明と概要の操作欄で、同じ再実行可否・文言を共有する。
  const group = lineageGroups(state.flowRuns).find((g) =>
    g.attempts.some((a) => a.runId === run.runId));
  const advice = runAdvice(run, group);
  const retryUi = flowRetryUi(run, advice);
  const node = state.flowNodeId ? run.nodes[state.flowNodeId] : null;
  const nodeDetail = node ? renderFlowNode(run, node, retryUi, advice) : '';
  const events = (fr.events || [])
    .map(
      (ev) =>
        `<div>${fmtTime(ev.ts)} <strong>${esc(ev.who || '')}</strong> ${esc(ev.kind || '')} ${esc(
          summarizeEvent(ev)
        )}</div>`
    )
    .join('');
  // 「次に何が起きるか・あなたの出番はあるか」を最上部で言い切る（runAdvice）。
  // 状態チップ・応答なしバッジの読み解きを人に要求しない。
  const adviceActions = [
    advice.kind === 'human'
      ? `<button class="chip primary-inline" data-goto-needs="${esc(advice.taskId || '')}">${
          advice.chip && advice.chip.includes('検収')
            ? '要対応タブで検収する'
            : '要対応タブで回答する'
        }</button>`
      : '',
    advice.kind === 'old' && advice.latestId
      ? `<button class="chip" data-goto-run="${esc(advice.latestId)}">最新の試行を開く</button>`
      : '',
    // 「本体が停止中/一時停止中」は、その場で解決する操作を出す（概要タブへ探しに行かせない）
    advice.kind === 'restart' && advice.stopped
      ? '<button class="chip primary-inline" data-start-kiro>自動実行を開始</button>'
      : '',
    advice.kind === 'restart' && advice.stopped === false
      ? '<button class="chip primary-inline" data-resume-kiro>再開</button>'
      : '',
  ].join(' ');
  const adviceBanner = `<div class="advice-banner advice-${advice.cls}">
    ${adviceChip(advice)} <span>${esc(advice.text)}</span> ${adviceActions}
  </div>`;
  // アーカイブ表示（bus からは掃除済み）: 読み取り専用の写しなので run への操作
  // （再投入・キャンセル・削除・GitLab 突き合わせ）は出さない。
  const archived = !!run.archived;
  const archivedBadge = archived
    ? ' <span class="badge" title="完了後に保存された記録です">記録</span>'
    : '';
  // 失敗した run と、中止した run（＝停滞していたので人が止めたもの）はやり直せる。
  // 停滞した run は「■ 中止」で終端させてから、このボタンでやり直す導線になる。
  //
  // 失敗 run のやり直しは **失敗した工程だけ** を対象にする（成功した工程は温存して続きから）。
  // タスクを積み直すと本体が同じ run を再開し、agent-flow が失敗ノードだけ pending へ戻すため。
  // ボタンの文言は実際に起きることに合わせる（「最初からやり直す」と読めると、成功した工程まで
  // 捨てられると誤解する — 実際 25 ノード中 1 つの失敗で 14 ノード分の成果を捨てていた）。
  // ボタンの出し分けも advice に従う:
  //  - human（判断待ち）: 出さない。ここで積み直すと人の判断ゲートを素通りしてしまう
  //    （正しい導線は要対応タブ — バナーのボタンが誘導する）
  //  - old（古い試行）: 出さない。最新の試行側で操作する
  //  - manual/restart: 主要操作として強調 ／ auto: 通常表示（押さなくてもよい）
  const resubmit = retryUi.show
    ? `<button class="chip ${['manual', 'restart'].includes(advice.kind) ? 'primary-inline' : ''}"
        id="flow-resubmit" title="${esc(retryUi.title)}">${esc(retryUi.label)}</button>`
    : '';
  // 不要な run の削除。実行中（orchestrator 生存）は不可 — 終端と応答なし（孤児）のみ。
  // アーカイブ（bus に実体が無く記録だけ残ったもの）も消せる: 消せないと一覧に永久に居座る。
  const deletable =
    archived ||
    run.status === 'done' || run.status === 'failed' || run.status === 'canceled' || run.alive === false;
  const deleteBtn = deletable
    ? `<button class="chip danger" id="flow-delete" title="${
        archived
          ? 'この実行の記録（アーカイブ）を削除します'
          : 'この実行のデータをゴミ箱へ移動します'
      }">🗑 削除</button>`
    : '';
  // run のキャンセル（人の明示アクション＝唯一の hard-stop）。まだ終端していない run に出す。
  // 承認待ちで park 中の run も暴走中の run も止められる。起票済みイシューは残す（追跡だけやめる）。
  const cancelable = !archived && !['done', 'failed', 'canceled'].includes(run.status);
  const parkedCount = Object.values(run.nodes || {}).filter((n) => n.parked).length;
  const cancelBtn = cancelable
    ? `<button class="chip danger" id="flow-cancel" title="この実行を中止します（レビュー待ちの監視や自動再開も止まります。作成済みの GitLab イシューは残ります）">■ 中止${parkedCount ? `（レビュー待ち ${parkedCount}）` : ''}</button>`
    : '';
  // gitlab executor 連動: 非終端ノードがあれば「GitLab と突き合わせ」で関連イシューの今の状態
  // （クローズ済み＝完了/失敗を先読み反映／オープン＝レビュー中を表示）を取り込める。run を開いた
  // ときに自動で一度走る（律速あり）ので、ボタンは手動の再取得（最新化）用。
  // GitLab 連携 UI は gitlab executor の run にだけ出す（run.gitlabish が正）。
  // agent/stub executor の run に「GitLab 最新化」や「イシューを探す」が並んでも、
  // 探す対象のイシューが存在しない＝押しても無意味なボタンでしかない。
  const hasOpenNodes = !archived && run.gitlabish && reconcilableNodes(run).length > 0;
  const rec = reconcileEntry(run.runId) || null;
  const recHits = rec ? Object.values(rec.byNode || {}).filter((r) => r.reconciled).length : 0;
  const reconcileBtn =
    hasOpenNodes && run.workspace && run.workspace.url
      ? `<button class="chip" id="flow-reconcile" ${rec && rec.loading ? 'disabled' : ''}
          title="関連する GitLab イシューの最新状態を取得して表示に反映します">${
            rec && rec.loading ? '取得中…' : '⟳ GitLab 最新化'
          }${recHits ? `（反映 ${recHits}）` : ''}</button>`
      : '';
const viewTabs = [
    ['overview', '概要'],
    ['graph', '工程'],
    ['history', '履歴'],
  ]
    .map(
      ([key, label]) =>
        `<button role="tab" class="flow-view-tab ${state.flowDetailView === key ? 'active' : ''}"
          data-flow-view="${key}" aria-selected="${state.flowDetailView === key}">${label}</button>`
    )
    .join('');

  const req = splitRequest(run.request);
  const overviewView = `<section class="flow-overview-view">
    <div class="flow-run-heading">
      <div>
        <span class="summary-kicker">選択中の作業</span>
        <h2>${req.title ? `<span class="prose-inline">${inlineMd(req.title)}</span>` : '内容のない実行'}</h2>
      </div>
      <span>${archivedBadge}</span>
    </div>
    ${runTaskOutcomeHtml(outcome)}
    ${finalVerificationFailureHtml(finalVerificationFailure)}
    ${req.body ? `<div class="flow-request-body">${proseHtml(req.body)}</div>` : ''}
    ${adviceBanner}
    ${relationshipStrip({ run })}
    ${
      run.tombstone
        ? '<p class="muted">リトライ（世代交代）で置き換えられた実行の記録です。工程出力は抜粋で、成果物の実体・イベントは新しい実行に引き継がれています。</p>'
        : archived
          ? '<p class="muted">完了済みの記録です。</p>'
          : ''
    }
    ${run.failureReason ? `<div class="flow-failure">失敗理由: ${esc(String(run.failureReason).replace(/\[agent-error:[a-z]+\]\s*/g, ''))}</div>` : ''}
    <div class="flow-progress-block">
      <div class="progress"><div style="width:${pct}%"></div></div>
      <strong>${run.counts.done + run.counts.failed}/${run.total}（${pct}%）</strong>
    </div>
    <div class="flow-counts">
      <div><strong>${run.counts.done || 0}</strong><span>工程完了</span></div>
      <div><strong>${run.counts.claimed || 0}</strong><span>実行中</span></div>
      <div><strong>${run.counts.failed || 0}</strong><span>失敗</span></div>
      <div><strong>${(run.counts.pending || 0) + (run.counts.waiting || 0)}</strong><span>これから</span></div>
    </div>
    <div class="flow-primary-actions">${runArtifactsButtonHtml(run)} ${resubmit} ${reconcileBtn} ${cancelBtn} ${deleteBtn}</div>
  </section>`;

  const graphView = `<div class="flow-graph-workspace">
    <section class="flow-graph-surface">
      <div class="flow-section-heading">
        <div><span class="summary-kicker">作業の流れ</span><h2>工程</h2></div>
        <span class="muted">工程を選ぶと内容を表示します</span>
      </div>
      <div id="graph-box">${renderGraphSvg(run)}</div>
      <div class="legend">${legend}</div>
    </section>
    <aside id="flow-node" class="flow-node-detail">
      <span class="summary-kicker">工程の内容</span>
      ${nodeDetail || '<div class="empty">グラフから工程を選択してください</div>'}
    </aside>
  </div>`;

  const historyView = `<section class="flow-history-view">
    <div class="flow-section-heading">
      <div><span class="summary-kicker">これまでの動き</span><h2>更新履歴</h2></div>
    </div>
    <div class="events flow-events">${events || '<span class="muted">イベントはありません</span>'}</div>
    <button type="button" class="subtle-action" data-open-technical-info>詳細情報を開く</button>
  </section>`;

  const body =
    state.flowDetailView === 'graph'
      ? graphView
      : state.flowDetailView === 'history'
        ? historyView
        : overviewView;

  return `<div class="flow-detail-shell">
    <button class="mobile-master-back" data-flow-back>一覧へ戻る</button>
    <div class="flow-view-tabs" role="tablist" aria-label="実行の詳細">${viewTabs}</div>
    <div id="flow-view-body">${body}</div>
  </div>`;

}
