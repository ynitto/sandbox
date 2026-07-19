'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// ノード詳細（進捗・タイムライン・関連イシュー）
// ---------------------------------------------------------------------------

// ノードのタイムライン（events の claimed / result。新しい順で届く）
function nodeTimeline(nodeId) {
  return ((state.flowRun && state.flowRun.nodeEvents) || {})[nodeId] || [];
}

// この工程が「やり直し」でどう扱われるかを言い切る行。
// グラフで赤いノードを見た人は「この工程だけ再実行したい」と考えるが、単体再実行は
// 存在しない — やり直しの単位は run で、agent-flow が失敗・未実行の工程だけを pending へ
// 戻して done を温存する。その規則をノード詳細でその場で伝える（run が止まっているときだけ。
// 実行中の run では紛らわしいので出さない）。
function nodeFateLine(run, effState, retryUi, advice) {
  const runStopped =
    TERMINAL_RUN_STATES.has(String(run.status)) || (run.alive === false && run.status !== 'done');
  if (!runStopped || run.archived) return '';
  const msg =
    effState === 'failed'
      ? retryUi && retryUi.show
        ? `⟳ この工程は「概要」タブの「${esc(retryUi.label)}」で<b>必ず再実行されます</b>` +
          '（この工程だけの単体再実行はありません。完了済みの工程は作り直されません）'
        : advice && advice.kind === 'human'
          ? '⏸ この工程は失敗しています。現在は再実行ではなく、先に「要対応」タブで確認・回答してください。'
          : '⏸ この工程は失敗しています。実行全体の案内に従って対応してください。'
      : effState === 'done'
        ? '✓ この工程は完了済みです。やり直しても<b>作り直されません</b>（成果はそのまま使われます）'
        : ['pending', 'waiting', 'claimed'].includes(effState)
          ? '… この工程は未完了のまま止まっています。やり直し（または自動再開）で<b>再実行されます</b>'
          : '';
  return msg ? `<div class="muted" style="margin-top:4px">${msg}</div>` : '';
}

// park（承認待ち）ノードの説明行。承認待ちで保留中＝worker スロットを空けて監視主体が
// 定期確認していること、throttle（起票見送り）や人の作業検知を人に伝える。
function nodeParkLine(node) {
  if (!node.parked) return '';
  if (node.throttled) {
    return `<div class="muted" style="margin-top:4px">⏸ 開始待ち（同時に進められる件数の上限に達しています。空き次第、自動で始まります）</div>`;
  }
  const active = node.parkActiveSeen
    ? 'レビューでの作業（MR など）を確認済み — マージ待ちです'
    : 'レビューまたは MR の作成を待っています';
  return `<div class="muted" style="margin-top:4px">⏳ レビュー待ち — 状況は定期的に自動確認しています。${active}</div>`;
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
        `最終応答 ${fmtAgo(node.heartbeatAt)} ${aliveLease ? '<span class="status-chip st-running">応答あり</span>' : '<span class="status-chip st-stalled">応答なし（自動で引き継がれます）</span>'}`
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
  if (!run.gitlabish) return ''; // gitlab executor の run 以外にイシュー UI は出さない
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
        `<div class="muted">GitLab 側の決着（${reconciled === 'done' ? '承認' : '却下'}）を先に表示しています。正式な反映は実行エンジン側で確定します。</div>`
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
        `<div class="muted">却下されたため、この工程は失敗扱いです。レビューコメントを引き継いで自動でやり直します（やり直し回数の上限に達すると「要対応」になります）。</div>`
      );
    }
  } else if (repoUrl && node.state === 'claimed') {
    // 実行中（result 未確定）: イシュー URL はまだ bus に無い。タスクトークンで検索できる
    if (cached && found === null) {
      rows.push(`<div class="muted">関連イシューは見つかりませんでした（イシュー作成前か、GitLab 連携外の作業です）</div>`);
    } else {
      rows.push(
        `<button id="btn-find-issue" data-token="${esc(node.taskToken)}" data-repo="${esc(repoUrl)}"
          title="この工程に対応する GitLab イシューを検索します">関連イシューを探す</button>`
      );
    }
  }
  if (!rows.length) return '';
  return `<div class="section-title">関連する GitLab イシュー</div>${rows.join('\n')}`;
}

function renderFlowNode(run, node, retryUi, advice) {
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
    (reconciled ? ' <span class="status-chip st-reconciled" title="GitLab 側の決着を先に表示しています（正式な反映待ち）">GitLab 反映</span>' : '');
  return `<div class="card full">
      <h3><span class="mono">${esc(node.id)}</span> [${esc(node.kind)}] — ${stateLabel}${node.who ? ` @${esc(node.who)}` : ''}</h3>
      <div class="node-goal">${proseHtml(node.goal) || '<span class="muted">（目標なし）</span>'}</div>
      ${node.deps.length ? `<div class="muted" style="margin-top:4px">依存: ${node.deps.map(esc).join(', ')}</div>` : ''}
      ${nodeFateLine(run, effState, retryUi, advice)}
      ${nodeParkLine(node)}
      ${nodeProgressLine(node)}
      ${nodeIssueBlock(run, node)}
      ${node.output || node.data ? '<button type="button" class="subtle-action" data-open-technical-info>出力の詳細を開く</button>' : ''}
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

// 失敗/中止した run のやり直し。
// agent-project 配下の run は、bus へ投げ直すのではなくタスクを積み直す（本体が新しい run を
// 起こし、結果も回収する）。bus/inbox は daemon が拾う契約で、daemon を使わない構成では
// 誰も拾わない＝押しても何も起きないため（res.viaTask がその判別）。
async function resubmitFlowRun() {
  return withActionLock('resubmit-flow-run', _resubmitFlowRun);
}

async function _resubmitFlowRun() {
  const run = state.flowRun && state.flowRun.run;
  if (!run) return;
  // 押す前に「正確に何が起きるか」を工程名で見せる。グラフの赤いノードが確実に
  // 再実行されるのか・完了分が捨てられないか、を推測させない。
  const nodes = Object.values(run.nodes || {});
  const rerun = nodes.filter((n) => !TERMINAL_NODE_STATES.has(n.state) || n.state === 'failed');
  const keep = nodes.filter((n) => n.state === 'done');
  const nameList = (list) =>
    list.slice(0, 8).map((n) => n.id).join(', ') + (list.length > 8 ? ` …（計 ${list.length} 件）` : '');
  const failedNames = rerun.filter((n) => n.state === 'failed');
  const canceled = run.status === 'canceled';
  const plan = canceled
    ? `中止した実行の続きからは再開できません。\nタスクを積み直して新しい実行を始めます（完了済み ${keep.length} 件も温存されません）。`
    : keep.length
    ? `やり直す工程（${rerun.length} 件）: ${nameList(rerun)}` +
      (failedNames.length ? `\n（うち失敗していた工程 ${nameList(failedNames)} は必ず再実行されます）` : '') +
      `\nそのまま使う完了済み（${keep.length} 件）: ${nameList(keep)}` +
      `\n\n新しい run は作らず、この run（${run.runId}）の中で再開します。`
    : `全 ${nodes.length || '?'} 工程をやり直します（完了済みの工程はありません）。`;
  const yes = await confirmDialog(`この実行をやり直します。\n\n${plan}\nよろしいですか？`);
  if (!yes) return;
  // 状態の置き場は project.dir（resolveProjectRoot / 状態 worktree）。selectedDir は
  // 登録ワークスペースで、backlog/commands が無いことが多い。そこに書くと resume-run が
  // 見つからず inbox 投入へ落ち、daemon 無し構成では誰も拾わない＝無反応ボタンになる。
  const projectDir = state.project && state.project.dir;
  const res = await guard('やり直し', () =>
    api.flowResubmit(projectDir, state.project.busDir, run.runId)
  );
  if (res) {
    const d = state.flowDaemon;
    uiLog('resubmit', res);
    if (res.viaTask) {
      const live = (state.project && state.project.liveness) || {};
      const when = live.running
        ? '本体がまもなく実行します'
        : '本体（agent-project）が次に動いたときに実行されます（今は停止中）';
      toast(
        canceled
          ? `タスク ${res.taskId} を新しい実行として積み直しました。${when}`
          : keep.length > 0
          ? `この run の中で失敗・未実行の ${rerun.length} 工程だけをやり直します（完了済み ${keep.length} 件は温存・新しい run は増えません）。${when}`
          : `タスク ${res.taskId} を積み直しました。${when}`,
        true
      );
    } else {
      toast(
        `新しい実行として開始を依頼しました${d && d.running === false ? '（実行エンジンが停止中のため、起動後に始まります）' : ''}`,
        true
      );
    }
    if (res.viaTask) {
      // resume-run の指示ファイルはプロジェクト側（commands/）に落ちる。bus は触っていない
      await gitPushAfterWrite(`agent-dashboard: resume run ${run.runId}`, projectDir);
    } else {
      // bus/inbox への再投入ファイルだけを反映（bus 全体のスナップショットは撮らない）
      await gitPushBusOp(`agent-dashboard: resubmit run ${run.runId}`, ['inbox']);
    }
    await reloadProject();
  }
}

// run をキャンセルする（人の明示アクション＝唯一の hard-stop）。承認待ちで park 中でも暴走中でも止まる。
async function cancelFlowRun() {
  const run = state.flowRun && state.flowRun.run;
  if (!run) return;
  const parked = Object.values(run.nodes || {}).filter((n) => n.parked).length;
  const note = parked
    ? `\nレビュー待ちの工程が ${parked} 件あります。監視は止めますが、作成済みの GitLab イシューは残ります（人がクローズできます）。`
    : '\n作成済みの GitLab イシューがあれば残ります。';
  const yes = await confirmDialog(
    `この実行（${run.runId}）を中止します。\n以後の作業・レビュー待ちの監視・自動再開をすべて止めます。${note}\nよろしいですか？`
  );
  if (!yes) return;
  let cancelRes = null;
  const ok = await guard('実行の中止', async () => {
    const res = await api.flowCancel(
      state.project.dir,
      state.project.busDir,
      run.runId,
      'agent-dashboard から手動キャンセル'
    );
    cancelRes = res;
    uiLog('cancel', run.runId, res);
    if (res && res.alreadyTerminal) {
      toast(`この実行は既に終了していました（${statusLabel(res.status)}）。中止は不要です。`, true);
    } else {
      toast(`実行を中止しました${res && res.cleared ? `（レビュー待ち ${res.cleared} 件の監視を停止）` : ''}`, true);
    }
    return true;
  });
  if (ok) {
    // cancel マーカー・meta・waits/ 削除を反映。waits を落とすと、git 同期後に
    // リモート側で park 済みノードが復活して見える瞬間を防げる。
    await gitPushBusOp(`agent-dashboard: cancel run ${run.runId}`,
      ['inbox/cancels', `runs/${run.runId}/meta.json`, `runs/${run.runId}/waits`]);
    // revise（detach）コマンドも state 側へ送る。bus だけ push すると remote project が
    // cancel を刈って新 act を始めたあとに revise が遅れ、すぐまた切り離される。
    if (state.project && state.project.dir && !(cancelRes && cancelRes.alreadyTerminal)) {
      await gitPushAfterWrite(`agent-dashboard: cancel detach ${run.runId}`, state.project.dir);
    }
    await reloadProject();
  }
}

// 不要な run を削除する（人の明示アクション）。実行中は main 側でも拒否される
async function deleteFlowRun() {
  const run = state.flowRun && state.flowRun.run;
  if (!run) return;
  // canceled は終端。done/failed 以外を一律「応答なし」と言うと誤り。
  const warn =
    !TERMINAL_RUN_STATES.has(run.status) && run.alive === false
      ? '\nこの実行はまだ終了していません（応答なし）。削除すると自動での再開もできなくなります。'
      : '';
  const trashHint = run.archived
    ? 'アーカイブのスナップショットを削除します。'
    : '実行データをゴミ箱へ移動します。';
  const yes = await confirmDialog(
    `この実行（${run.runId}）を削除します。\n${trashHint}${warn}\nよろしいですか？`
  );
  if (!yes) return;
  const ok = await guard('実行の削除', async () => {
    // dir も渡す: アーカイブのスナップショット（flow-archive/<id>.json）を消さないと、
    // bus から消えても run 一覧が「アーカイブ」として拾い直し、削除が効かないように見える
    const res = await api.flowDeleteRun(state.project.dir, state.project.busDir, run.runId);
    uiLog('deleteRun', run.runId, res);
    toast(`実行を削除しました（${res.via === 'trash' ? 'ゴミ箱へ移動' : '完全削除'}）`, true);
    return true;
  });
  if (ok) {
    // 消した run のディレクトリだけを反映（他 run の揮発ファイルを巻き込まない）
    await gitPushBusOp(`agent-dashboard: delete run ${run.runId}`, [`runs/${run.runId}`]);
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
  if (!nodes.length) return '<div class="empty">工程がありません</div>';
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

  // 完了したノード同士を繋ぐエッジは「消化済みの経路」として強調する（done クラス）。
  // GitLab 突き合わせの先読み反映（reconciled）があれば表示上の状態はそちらを優先する。
  const effStateOf = (id) => {
    const nd = run.nodes[id];
    return (nd && (reconciledStateFor(run, id) || nd.state)) || '';
  };
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
      const doneEdge = effStateOf(d) === 'done' && effStateOf(n.id) === 'done';
      edges.push(`<path class="edge${doneEdge ? ' done' : ''}" d="M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}" />`);
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
  for (const tab of root.querySelectorAll('[data-flow-view]')) {
    tab.addEventListener('click', () => {
      state.flowDetailView = tab.dataset.flowView;
      renderFlow();
    });
  }
  const back = root.querySelector('[data-flow-back]');
  if (back) {
    back.addEventListener('click', () => {
      state.flowMobileDetail = false;
      renderFlow();
    });
  }
  const technicalInfo = root.querySelector('[data-open-technical-info]');
  if (technicalInfo) technicalInfo.addEventListener('click', openTechnicalInfo);
  for (const btn of root.querySelectorAll('button[data-run-artifacts]')) {
    btn.addEventListener('click', () => openRunArtifacts(btn.dataset.runArtifacts));
  }
  for (const g of root.querySelectorAll('g.node[data-node]')) {
    g.addEventListener('click', () => {
      state.flowNodeId = g.dataset.node;
      state.flowDetailView = 'graph';
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
  // advice バナーの誘導ボタン: 判断待ち → 要対応タブ ／ 古い試行 → 最新の試行へ ／
  // 本体停止・一時停止 → その場で起動・再開
  for (const btn of root.querySelectorAll('button[data-goto-needs]')) {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const tid = btn.dataset.gotoNeeds || '';
      if (tid) {
        // needs の id は通常タスク id。task-id 照合でフォールバックする
        const match = (state.project && state.project.needs || []).find(
          (n) => n.id === tid || n.taskId === tid
        );
        state.needsSelectedId = match ? match.id : tid;
        state.needsFilter = 'open';
        state.needsMobileDetail = true;
      }
      switchTab('needs');
    });
  }
  for (const btn of root.querySelectorAll('button[data-goto-run]')) {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      gotoRun(btn.dataset.gotoRun);
    });
  }
  for (const btn of root.querySelectorAll('button[data-start-kiro]')) {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      startAgentProject();
    });
  }
  for (const btn of root.querySelectorAll('button[data-resume-kiro]')) {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const p = state.project;
      if (!p) return;
      const ok = await guard('再開', async () => {
        const res = await api.requestLifecycle(p.dir, 'resume', 'フロー画面から再開');
        uiLog('lifecycle', 'resume', res);
        toast('再開を依頼しました（反映まで少し時間がかかることがあります）', true);
        return true;
      });
      if (ok) {
        gitPushAfterWrite('agent-dashboard: resume', p.dir);
        await reloadProject();
      }
    });
  }
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
