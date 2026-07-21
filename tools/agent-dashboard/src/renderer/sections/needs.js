'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

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

// milestone カード（needs/<pid>.md）の対象プロジェクト/バージョンの「今」の状態。
// カードはファイルとして残るため、run が進んだ後も前回評価時の内容で表示され続ける。
// cmd_approve は収束候補（converged）しか受け付けないので、それ以外の状態で承認ボタンを
// 出すと必ず exit 2 で失敗する（「押しても何も起きない」）。ボタンの表示判定に使う。
function milestoneStatusFor(p, id) {
  const ps = (p && p.projectState) || {};
  if (ps.id === id) return ps.status || '';
  for (const st of Object.values(ps.charters || {})) {
    if (st && st.id === id) return st.status || '';
  }
  return null; // 状態が見つからない（判定材料なし）＝従来どおりボタンを出す
}

// agent-project は各パスの再評価中、前回の milestone をいったん削除し、判断が必要なら
// パス末尾で同じファイルを作り直す。Viewer のポーリングがその間を読むとカードが点滅する。
// project.json の status は再評価中も判断待ちのまま維持されるため、そちらを正として、同じ
// プロジェクトの直前スナップショットにあった milestone だけを一時的に補う。
// accepted への遷移やバージョン削除では有効 ID から外れるので、古いカードは保持しない。
function stabilizeMilestoneNeeds(previousProject, nextProject) {
  const current = [...((nextProject && nextProject.needs) || [])];
  if (!previousProject || !nextProject || previousProject.dir !== nextProject.dir) return current;

  const waitingStatuses = new Set([
    'converged',
    'no-acceptance',
    'blocked',
    'no-progress',
    'project-budget',
    'project-cost',
  ]);
  const ps = nextProject.projectState || {};
  const validIds = new Set();
  const versions = nextProject.charters || [];
  if (versions.length) {
    for (const version of versions) {
      const st = (ps.charters || {})[version.name] || {};
      if (st.id && waitingStatuses.has(String(st.status || ''))) validIds.add(String(st.id));
    }
  } else if (!(nextProject.charter && nextProject.charter.master)) {
    if (ps.id && waitingStatuses.has(String(ps.status || ''))) validIds.add(String(ps.id));
  }

  const present = new Set(current.map((need) => need.id));
  for (const need of previousProject.needs || []) {
    if (need.kind === 'milestone' && validIds.has(String(need.id)) && !present.has(need.id)) {
      current.push(need);
    }
  }
  return current;
}

// milestone id（<project>-<version>）に対応する計画バージョン名を project.json から引く。
// no-acceptance の milestone から「そのバージョンに完了条件を追加」フォームを開くのに使う。
function milestoneVersionName(p, id) {
  const ps = (p && p.projectState) || {};
  for (const [name, st] of Object.entries(ps.charters || {})) {
    if (st && st.id === id) return name;
  }
  return null;
}

// needs（要対応）の種別ラベル。内部の kind 名は UI に出さない
const NEED_KIND_LABELS = {
  'plan-review': '計画レビュー',
  review: '検収',
  milestone: 'マイルストーン',
  blocked: '対応依頼',
};

function needKindLabel(kind) {
  return NEED_KIND_LABELS[String(kind || 'blocked')] || String(kind);
}

// needs の種類ごとに出すアクション。
//   plan-review … 実行前レビュー: 承認して実行を許可 / 差し戻し（修正指示の記入必須）/ 却下
//   blocked   … 指示して再開 / そのまま再実行 / 保留
//   review    … 成果物レビュー: 承認して完了 / 差し戻し（記入必須）/ 却下
//   milestone … プロジェクト承認 — 完了確認待ち（converged）のときだけ
function needCompleteHowHtml(n) {
  const p = state.project;
  const task = taskForNeed(p, n);
  const runs = task ? runsForTask(task.id) : [];
  const hint = task
    ? taskCompletionHint(task, { runs })
    : null;
  // needs 種別ごとの「この操作で完了するか」を先頭に出す（task が無い milestone 等は種別文言）
  let line = hint && hint.completeHow;
  if (n.kind === 'review') {
    line = '承認すると、このタスクは完了します。';
  } else if (n.kind === 'plan-review') {
    line = '承認すると、タスクの実行を開始します。';
  } else if (n.kind === 'milestone') {
    const status = milestoneStatusFor(p, n.id);
    line =
      status === null || status === 'converged'
        ? '承認すると、プロジェクトは完了します。'
        : 'まだプロジェクト完了の段階ではありません。';
  } else if (n.kind === 'blocked' && isVerifyPendingNeed(p, n)) {
    line = '承認すると、このタスクは完了します（検証コマンド未定義のため、人の確認が完了の根拠になります）。';
  } else if (n.kind === 'blocked' && needHasArtifacts(p, n, state.flowRuns)) {
    line = '成果はできています。内容を確認して問題なければ、承認するとこのタスクを完了できます。';
  } else if (n.kind === 'blocked' && needHasDeliverable(p, n, state.flowRuns)) {
    // 完了は選べるが、成果物は確認できていない（実行はしたが差分が無い・取得できない）。
    // ここで「成果はできています」と言うと、着手前に止まった run でも成果があることに
    // なってしまう。断定せず、確認してから判断してもらう。
    line = '成果物は確認できていません。内容を確かめたうえで、完了にしてよければ承認できます。';
  } else if (!line && n.kind === 'blocked') {
    line = '指示を送ると、作業を再開します。';
  }
  if (!line) return '';
  return `<div class="task-complete-banner need-complete-how">${esc(line)}</div>`;
}

const COMMAND_ACTION_LABELS = {
  approve: '承認',
  hold: '保留',
  reject: '却下',
  revise: '修正指示',
  pin: '優先度変更',
  defer: '優先度変更',
  'resume-run': '再実行',
};

// 直前の指示（承認・保留など）が本体で取り込みに失敗した（commands/*.err）ことを知らせる
// バナー。取り込みは非同期なので送信時トーストは成功しか言えない——ここで理由を見せないと、
// カードが未対応へ戻る理由が分からず同じボタンが繰り返し押される（実際そうなっていた）。
function commandFailureHtml(n) {
  const cf = n && n.commandFailure;
  if (!cf) return '';
  const label = COMMAND_ACTION_LABELS[cf.action] || cf.action || '指示';
  return `<div class="need-command-failure" role="alert">
    <strong>「${esc(label)}」は届きましたが、処理に失敗しました${cf.failedAt ? `（${esc(cf.failedAt)}）` : ''}</strong>
    <span>${esc(cf.error || '')}</span>
    <span class="muted">原因を解消してから、もう一度同じ操作を送ってください。</span>
  </div>`;
}

// 直前の指示（承認・保留など）が本体に届き、取り込みに成功した（commands/processed/*.json）
// ことを知らせる確認。取り込みは非同期（ドロップ→後で本体が処理）で、成功時は元ファイルが
// 消えるだけなので、以前は「保留中（本体未取り込み）」と「受理済み」を画面で区別できず、
// 押しても何も起きないように見えた。失敗（commandFailure）が無いときだけ出す＝失敗表示を上書きしない。
function commandReceiptHtml(n) {
  const cr = n && n.commandReceipt;
  if (!cr || (n && n.commandFailure)) return '';
  const label = COMMAND_ACTION_LABELS[cr.action] || cr.action || '指示';
  return `<div class="need-command-receipt">
    <span>「${esc(label)}」は本体に届き、受理されました${cr.processedAt ? `（${esc(cr.processedAt)}）` : ''}。反映まで少し待ってください。</span>
  </div>`;
}

function needActionsHtml(n, options) {
  const inReview = Boolean(options && options.inReview);
  const kind = n.kind || 'blocked';
  const buttons = [];
  if (kind === 'plan-review') {
    buttons.push(`<button class="primary-inline" data-act="approve" data-id="${esc(n.id)}">承認して実行</button>`);
    buttons.push(`<button data-act="feedback" data-id="${esc(n.id)}" data-require="1" title="修正指示を記入して計画を練り直させます">差し戻す</button>`);
    buttons.push(`<button class="danger" data-act="reject" data-id="${esc(n.id)}" data-require="1" title="このタスクを廃止し、計画を作り直させます">却下</button>`);
  } else if (kind === 'review') {
    buttons.push(`<button class="primary-inline" data-act="approve" data-id="${esc(n.id)}">承認して完了にする</button>`);
    buttons.push(`<button data-act="feedback" data-id="${esc(n.id)}" data-require="1" title="修正方針を記入してやり直させます">差し戻す</button>`);
    buttons.push(`<button class="danger" data-act="reject" data-id="${esc(n.id)}" data-require="1" title="この成果を採用せず廃止し、計画を作り直させます">却下</button>`);
  } else if (kind === 'milestone') {
    const status = milestoneStatusFor(state.project, n.id);
    if (status === null || status === 'converged') {
      // 完了確認待ち（converged）: 承認して完了にできる
      buttons.push(`<button class="primary-inline" data-act="approve" data-id="${esc(n.id)}">✓ プロジェクトを完了として承認</button>`);
      buttons.push(`<button data-act="feedback" data-id="${esc(n.id)}">↩ 指示を送る</button>`);
    } else if (status === 'no-acceptance') {
      // 完了条件が無い＝承認できない。承認ではなく「完了条件を追加」へ誘導する
      // （承認を押しても失敗し、マイルストーンが消えず何度も出るのを防ぐ）。
      const ver = milestoneVersionName(state.project, n.id);
      buttons.push(
        `<span class="muted">このバージョンには完了条件がありません。完了を判定できないため、完了条件を追加してください。</span>`
      );
      if (ver) {
        buttons.push(`<button class="primary-inline" data-open-version="${esc(ver)}">✎ 完了条件を追加</button>`);
      }
      buttons.push(`<button data-act="feedback" data-id="${esc(n.id)}">↩ 指示を送る</button>`);
    } else {
      // blocked / 停滞 / 予算到達など: 承認前の段階。内容を確認して対応する
      buttons.push(
        `<span class="muted">まだ完了確認の段階ではありません（現在: ${esc(statusLabel(status) || '未実行')}）。内容を確認して、必要なら計画バージョンを編集してください。</span>`
      );
      buttons.push(`<button data-act="feedback" data-id="${esc(n.id)}">↩ 指示を送る</button>`);
    }
  } else {
    // 検収物が少しでもあれば「承認して完了にする」を出す（本体は complete で完了確定する）。
    // 差分を先に見たい人向けに「差分を確認して承認」も併置し、承認そのものは常に押せる形にする
    // ——「見当たらないから完了できない」を作らないことを、細かい出し分けより優先する。
    // 成果を見る導線は needArtifactsButtonHtml（「成果を確認」）が別に出すので、
    // ここは承認そのものだけを置く（同じ操作を 2 つ並べない）。
    const canApproveCompletion = needHasDeliverable(state.project, n, state.flowRuns);
    if (canApproveCompletion) {
      buttons.push(`<button class="primary-inline" data-act="approve" data-id="${esc(n.id)}" title="成果を確認済みとして、このタスクを完了（納品確定）にします">承認して完了にする</button>`);
    }
    buttons.push(`<button class="${canApproveCompletion ? '' : 'primary-inline'}" data-act="feedback" data-id="${esc(n.id)}">指示を送って再開</button>`);
    buttons.push(`<button data-act="rerun" data-id="${esc(n.id)}">そのまま再実行</button>`);
    buttons.push(`<button data-act="hold" data-id="${esc(n.id)}" title="このタスクを止めて保留にします">保留にする</button>`);
  }
  const ph =
    kind === 'plan-review'
      ? '差し戻しの修正指示・却下の理由（承認だけなら空欄のままで構いません）'
      : kind === 'review'
        ? '差し戻しの修正方針・却下の理由（承認だけなら空欄のままで構いません）'
        : '修正方針・指示（空のまま再実行もできます）';
  // 実行制御で止まっているなら、操作を出す前にそう言う。押せなくはしない——止めたのも
  // 人なので、承認だけ先に送っておく判断はあり得る。「送っても動かない」を隠さないことが要点。
  return `${orchBlockedBannerHtml()}${needCompleteHowHtml(n)}<div class="need-actions" data-need="${esc(n.id)}">
    <textarea rows="2" class="need-input" placeholder="${esc(ph)}"></textarea>
    <div class="row need-buttons">${buttons.join('')}
      <span class="spacer"></span>
      <button data-open="${esc(n.file)}" title="エディタで直接編集">ファイルを開く</button>
    </div>
  </div>`;
}

// 種別ごとの「何を確認するか」。カードの先頭で確認の目的を一文で示す
const NEED_ASK = {
  'plan-review': 'このタスクの実行を始めてよいか確認してください。',
  review: '成果物を確認し、完了にしてよいか判断してください。',
  milestone: 'プロジェクトを完了にしてよいか確認してください。',
  blocked: '作業を再開するための対応を指示してください。',
};

// カード見出し用にタイトルの定型接頭辞（種別バッジと重複する）を落とす
function needDisplayTitle(n) {
  return String(n.title || n.id).replace(/^(要対応|実行前レビュー|マイルストーン)\s*[:：]\s*/, '');
}

// リスクダイジェスト総合値（frontmatter risk: low/med/high）のバッジ。
// 詳細（## リスク の材料）は「判断材料を見る」の折りたたみに含まれる
const RISK_LABELS = { low: 'リスク低', med: 'リスク中', high: 'リスク高' };
function riskBadgeHtml(n) {
  const risk = String(n.risk || '');
  if (!RISK_LABELS[risk]) return '';
  return `<span class="risk-badge risk-${esc(risk)}" title="リスクダイジェスト（詳細は判断材料内の「リスク」）">${RISK_LABELS[risk]}</span>`;
}

// needs カードに対応する spec 成果物（specs/<task-id>/）。spec 作成タスク（<id>-spec）の
// 検収カードと、展開後の総合検証カードの両方から同じ specs/<元タスク id>/ を引けるよう、
// -spec（採番付き -spec-2 等も）を剥がした id でも照合する
function specForNeed(p, n) {
  const tid = String(n.taskId || n.id || '');
  const base = tid.replace(/-spec(-\d+)?$/, '');
  const specs = p.specs || [];
  return specs.find((s) => s.id === tid) || specs.find((s) => s.id === base) || null;
}

function relatedRunIdForNeed(project, need, flowRuns) {
  const taskId = String((need && (need.taskId || need.id)) || '');
  const tasks = [...((project && project.backlog) || []), ...((project && project.archive) || [])];
  const task = tasks.find((item) => String(item.id) === taskId);
  const lastRun = task && task.extra ? String(task.extra.last_run || '') : '';
  if (lastRun) return lastRun;
  const match = [...(flowRuns || [])]
    .filter((run) => String(run.taskId || '') === taskId)
    .sort((a, b) => String(b.updatedAt || b.createdAt || '').localeCompare(
      String(a.updatedAt || a.createdAt || '')
    ))[0];
  return match ? String(match.runId || '') : '';
}

function taskForNeed(project, need) {
  const taskId = String((need && (need.taskId || need.id)) || '');
  return ((project && project.backlog) || []).find((task) => String(task.id) === taskId) || null;
}

// 「そのまま再実行」をどの口へ送るか。
//   resume-run … 再開できる run がある。本体が last_run を固定して ready へ積み直す正規の口で、
//                 失敗した工程だけやり直し done は温存される。指示ファイルが残るので
//                 失敗すれば journal と .err に理由が残る。
//   feedback   … 再開できる run が無い票（run を持たない blocked・合成カード）。
//                 needs ファイルの空 [x] で作業を再開させる従来の口。
// 経路の選択をここに閉じ込める（呼び出し側で条件を書くと、片方だけ直して食い違う）。
function needRerunPlan(project, need) {
  const task = taskForNeed(project, need);
  const run = String(((task && task.extra) || {}).last_run || '').trim();
  if (task && run) return { via: 'resume-run', id: String(task.id), run };
  return { via: 'feedback' };
}

function completedTaskForNeed(project, need) {
  const taskId = String((need && (need.taskId || need.id)) || '');
  const archived = ((project && project.archive) || []).find((task) => String(task.id) === taskId);
  if (archived) return archived;
  return ((project && project.backlog) || []).find(
    (task) => String(task.id) === taskId && String(task.status || '') === 'done'
  ) || null;
}

function completedRunForNeed(project, need, flowRuns) {
  const runId = relatedRunIdForNeed(project, need, flowRuns);
  const run = (flowRuns || []).find((item) => String(item.runId || '') === String(runId || ''));
  return run && String(run.status || '') === 'done' ? run : null;
}

// 同一タスクの系統内でいちばん新しい done の run（リトライ世代の降順）。
// last_run（最新試行）が実行中・失敗のときでも、旧世代の完了成果を見る導線に使う
// （リトライ中に「成果を確認」が消える＝成果物が消失したように見える問題の対策）。
// 完了承認の可否判定（completedRunForNeed）はあくまで最新試行の done を根拠にするため、
// このフォールバックは成果の閲覧にだけ使う。
function newestDoneRunForNeed(project, need, flowRuns) {
  const taskId = String((need && (need.taskId || need.id)) || '');
  if (!taskId) return null;
  const key = sanitizeTaskId(taskId);
  return (
    (flowRuns || [])
      .filter((r) => r.taskId && sanitizeTaskId(r.taskId) === key && String(r.status) === 'done')
      .sort(
        (a, b) =>
          (b.retries || 0) - (a.retries || 0) ||
          String(b.updatedAt || b.createdAt || '').localeCompare(String(a.updatedAt || a.createdAt || ''))
      )[0] || null
  );
}

// 成果の閲覧に使う run: 最新試行が done ならそれ、でなければ系統内の最新 done 世代。
function artifactRunForNeed(project, need, flowRuns) {
  return (
    completedRunForNeed(project, need, flowRuns) || newestDoneRunForNeed(project, need, flowRuns)
  );
}

// 要対応詳細のバナー、完了承認ボタン、検収ダイアログを同じ判定へ揃える。
// status=done は orchestrator がフローを終了した一次情報。検証をフローノードとして数える
// 形式では counts.failed / waiting が残るため、ノード集計を完了承認の可否に重ねない。
// env_resume も検証失敗で付くことがあるため、通常の環境障害との区別はバックエンドで
// 「検証差異を明示受容した理由」と agent-error 記録を使って行う。
function needFinalVerificationFailure(project, need, flowRuns) {
  if (!need || String(need.kind || 'blocked') !== 'blocked') return null;
  const task = taskForNeed(project, need);
  if (!task || String(task.status || '') !== 'blocked') return null;
  const failure = needFailureViewModel(need);
  if (!failure) return null;
  const run = completedRunForNeed(project, need, flowRuns);
  if (!run) return null;
  return {
    title: '工程は全て成功・最終検証で失敗',
    summary: failure.summary,
    taskId: task.id,
  };
}

// 承認 = 完了にできるか。「検収物が少しでもあるなら人が完了を選べる」の 1 条件に絞る。
//
// 以前は「blocked かつ task.status が blocked かつ検証失敗の解析に成功しかつ完了 run が
// 見つかった」という AND 連鎖で出し分けており、どれか 1 つ欠けると承認ボタンごと消えて
// 完了できなくなっていた（何度直しても別の抜け道で再発した）。完了させてよいかの判断は
// 人がするので、画面は「見せるものがあるか」だけを見る。
function needHasDeliverable(project, need, flowRuns) {
  if (!need || String(need.kind || 'blocked') !== 'blocked') return false;
  if (isVerifyPendingNeed(project, need)) return true;
  if (completedTaskForNeed(project, need)) return true;
  // タスク自身が持つ実行履歴で判定する。run 一覧（flowRuns）は非同期に読むので、
  // それだけに頼ると読み込み前は「成果なし」と誤判定してボタンが消える
  // （画面を開くたびに出たり消えたりする、と報告された症状の一因）。
  const task = taskForNeed(project, need);
  if (task && String(((task.extra || {}).last_run) || '').trim()) return true;
  return Boolean(artifactRunForNeed(project, need, flowRuns));
}

// 成果物が**実際にある**か。上の needHasDeliverable（＝人が完了を選べるか）とは別の問い。
//
// 両者を 1 つの述語で兼ねていたため、「実行を 1 回試した」ことを示すだけの last_run が
// 成果の根拠に使われ、着手前に止まって成果物ゼロの票にも「成果はできています。」と
// 表示していた。ボタンを出すかどうかは人の判断に委ねてよい（見せるものが無くても
// 完了を選ぶ権利はある）が、**何があるかの断定には実データだけを使う**。
// ここで見るのは「あると分かるもの」だけで、分からない場合は false を返す
// （ref 未解決などで差分を取れないときは、無いのではなく分からない）。
function needHasArtifacts(project, need, flowRuns) {
  if (!need || String(need.kind || 'blocked') !== 'blocked') return false;
  if (isVerifyPendingNeed(project, need)) return true;   // 工程は完了済み＝成果はある
  if (completedTaskForNeed(project, need)) return true;
  const diff = need.diff;
  if (diff && diff.hasDiff && (diff.artifacts || []).length) return true;
  for (const e of need.delivery || []) {
    if ((e.files || []).length || String(e.mr_url || '').trim()) return true;
  }
  return Boolean(artifactRunForNeed(project, need, flowRuns));
}

// 承認理由は決定記録として残すだけ。**本体の分岐材料にはしない**
// （文面から「完了か積み直しか」を推定させない — 意図は complete フラグで明示する）。
function needApprovalReason(project, need, flowRuns, input) {
  return String(input || '').trim() || '成果を確認して完了を承認';
}

// 成果（差分）を見る導線。承認できるかとは独立に、見るものがあるなら常に出す
// （承認状態で出し分けると「成果も承認も見当たらない」状態が生まれる）。
// delivery（検収物）に中身があるか。done run が無くても、コメント付き再実行などで
// delivery だけが記録されている票の成果を確認できるようにする。
function hasDeliveryContent(need) {
  return ((need && need.delivery) || []).some(
    (e) => e && e.role !== 'reference'
      && ((e.files || []).length || String(e.mr_url || '').trim() || (e.path && (e.ref || e.branch)))
  );
}

function needArtifactsButtonHtml(project, need, flowRuns) {
  // リトライ中（最新試行が未完）でも、系統内に done 世代があれば成果への導線を残す。
  // done run が無くても、delivery に中身があれば「成果を確認」を出す（検収時に成果物が
  // 見られない、と報告された症状の対策）。
  if (!completedTaskForNeed(project, need)
    && !artifactRunForNeed(project, need, flowRuns)
    && !hasDeliveryContent(need)) return '';
  return `<button type="button" class="primary-inline" data-need-artifacts="${esc(need.id)}">成果を確認</button>`;
}

// verify 未定義のまま工程が完了し、人の確認待ちで blocked になっている票。
// この票の承認（approve）は本体側で done 確定になるため「承認して完了にする」を出す。
// 環境要因（env_resume）の blocked は「環境を直して続きから再開」の契約なので対象外。
function isVerifyPendingNeed(project, n) {
  if (!n || String(n.kind || 'blocked') !== 'blocked') return false;
  const task = taskForNeed(project, n);
  if (task && String(task.verify || '').trim()) return false;
  if (task && String(((task.extra || {}).env_resume) || '') === '1') return false;
  const prose = [
    n.failureSummary, n.why, n.detail,
    task && task.extra ? task.extra.needs_reason : '',
  ].filter(Boolean).join('\n');
  return /verify\s*未定義/i.test(prose);
}

function buildNeedVerifyRevision(project, need, nextVerify, feedback) {
  const task = taskForNeed(project, need);
  const verify = String(nextVerify || '').trim();
  const note = String(feedback || '').trim();
  if (verify === String(task.verify || '').trim() && !note) return null;
  return {
    action: 'revise',
    id: task.id,
    fields: verify === String(task.verify || '').trim() ? {} : { verify },
    feedback: note,
    reason: '要対応画面で検証コマンドを変更',
  };
}

function verifyRevisionConfirmMessage(task, revision) {
  const before = String(task.verify || '').trim() || '（未設定）';
  const after = Object.prototype.hasOwnProperty.call(revision.fields || {}, 'verify')
    ? String(revision.fields.verify || '').trim() || '（削除）'
    : before;
  return (
    `タスク ${task.id} の検証コマンドを変更して再実行します。\n\n` +
    `変更前:\n${before}\n\n変更後:\n${after}\n\n` +
    'タスク分解はやり直しません。完了済み成果物と依存関係を維持します。\n' +
    '古い実行は履歴に残り、新しい試行を開始します。よろしいですか？'
  );
}

function needVerifyRevisionHtml(project, need) {
  const task = taskForNeed(project, need);
  if (!task || need.kind !== 'blocked') return '';
  return `<details class="need-verify-revision" data-ui-key="need-verify:${esc(need.id)}">
    <summary>検証コマンドを変更</summary>
    <p class="muted">タスク分解と完了済み成果物は維持し、新しい検証コマンドで次の試行を開始します。</p>
    <div class="field"><label>検証コマンド</label>
      <textarea rows="2" class="mono need-verify-input">${esc(task.verify || '')}</textarea></div>
    <div class="field"><label>補足指示（任意）</label>
      <textarea rows="2" class="need-verify-feedback" placeholder="例: CI環境では直列実行する"></textarea></div>
    <div class="row need-buttons">
      <span class="muted">古い実行は履歴に残ります</span><span class="spacer"></span>
      <button class="primary-inline" data-verify-revise="${esc(need.id)}">変更して再実行</button>
    </div>
  </details>`;
}

function formatNeedFullOutput(need, flowResponse) {
  // 工程出力は抜粋（冒頭＋末尾）で載せる。全文を連結すると完了 run の詳細情報が巨大に
  // なって読めない（「出力全部が含まれサイズが大きすぎる」という指摘への対処）。
  // 結論・エラーは末尾に出ることが多いため末尾を厚めに取る。
  const HEAD = 1200;
  const TAIL = 2400;
  const excerpt = (text) => {
    const s = String(text == null ? '' : text);
    if (s.length <= HEAD + TAIL) return s;
    return `${s.slice(0, HEAD)}\n…（中略 ${s.length - HEAD - TAIL} 文字）…\n${s.slice(-TAIL)}`;
  };
  const sections = [`# 要対応の原文\n\n${String((need && need.body) || '原文はありません')}`];
  const run = flowResponse && flowResponse.run;
  if (!run) {
    sections.push('# 関連する実行ログ\n\n関連するrunは見つかりませんでした。');
    return sections.join('\n\n');
  }
  const runFacts = [`run: ${run.runId || '-'}`, `状態: ${run.status || '-'}`];
  if (run.failureReason) runFacts.push(`失敗理由: ${run.failureReason}`);
  sections.push(`# 関連する実行\n\n${runFacts.join('\n')}`);
  let truncated = false;
  for (const node of Object.values(run.nodes || {})) {
    const output = node.output == null ? '' : String(node.output);
    const error = node.error == null ? '' : String(node.error);
    if (!output && !error) continue;
    if (output.length > HEAD + TAIL || error.length > HEAD + TAIL) truncated = true;
    const text = [excerpt(output), error ? `stderr / error:\n${excerpt(error)}` : '']
      .filter(Boolean).join('\n\n');
    sections.push(`# 工程 ${node.id || '-'} — ${node.goal || node.title || ''}\n\n${text}`);
  }
  if (truncated) {
    sections.push(`# 注記\n\n長い工程出力は冒頭と末尾のみ表示しています。全文は bus/runs/${run.runId || '<run-id>'}/results/ を参照してください。`);
  }
  if (run.final && Object.keys(run.final).length) {
    sections.push(`# final\n\n${JSON.stringify(run.final, null, 2)}`);
  }
  return sections.join('\n\n');
}

async function loadNeedFullOutput(need) {
  const key = `${need.file || need.id}:${need.mtime || 0}`;
  if (state.needOutputCache[key]) return state.needOutputCache[key];
  const runId = relatedRunIdForNeed(state.project, need, state.flowRuns);
  let flowResponse = null;
  if (runId && state.flowRun && state.flowRun.run && state.flowRun.run.runId === runId) {
    flowResponse = state.flowRun;
  } else if (runId) {
    flowResponse = await guard('関連runの読込', () =>
      api.flowRun(state.project.dir, state.project.busDir, runId)
    );
  }
  const result = { runId, flowResponse, text: formatNeedFullOutput(need, flowResponse) };
  state.needOutputCache[key] = result;
  return result;
}

async function openNeedFullOutput(needId) {
  const need = state.project && state.project.needs.find((item) => item.id === needId);
  if (!need) return toast('要対応項目が見つかりません');
  $('need-output-title').textContent = `${needDisplayTitle(need)} — 出力全体`;
  $('need-output-body').textContent = '関連する実行ログを読み込んでいます…';
  $('dlg-need-output').showModal();
  const result = await loadNeedFullOutput(need);
  $('need-output-body').textContent = result.text;
  $('need-output-body').scrollTop = 0;
}

function deliveryRoleLabel(role) {
  return role === 'reference' ? '参照（読取）' : '書込先';
}

function deliveryReviewState(entries, mrs) {
  const list = entries || [];
  const fileCount = list.reduce((count, entry) => count + (entry.files || []).length, 0);
  const hasMr = Boolean((mrs || []).length || list.some((entry) => entry.mr_url));
  const canDiscover = list.some(
    (entry) => entry.role !== 'reference' && entry.path
  );
  return { fileCount, hasMr, canDiscover, hasContent: fileCount > 0 || hasMr };
}

// 完了 run から成果ダイアログへ渡す読み取り専用モデル。検収中なら needs に既にある
// 複数リポジトリ・MR・差分情報を正として再利用する。
function runArtifactViewModel(project, run) {
  const key = sanitizeTaskId(run && run.taskId);
  const tasks = [...((project && project.backlog) || []), ...((project && project.archive) || [])];
  const task = key ? tasks.find((item) => sanitizeTaskId(item.id) === key) : null;
  const need = key
    ? ((project && project.needs) || []).find((item) =>
        sanitizeTaskId(item.taskId || item.id) === key)
    : null;
  const needDelivery = (need && need.delivery) || [];
  const workspace = (run && run.workspace) || null;
  const workspaceDelivery = workspace && ((project && project.workspace) || workspace.url)
    ? [{
        name: workspace.desc || '成果リポジトリ',
        role: 'write',
        url: workspace.url || '',
        path: (project && project.workspace) || '',
        base: workspace.base || '',
        target: workspace.target || workspace.base || '',
        branch: workspace.branch || '',
        ref: workspace.branch || '',
        files: [],
      }]
    : [];
  const needMrs = (need && need.mrUrls) || (need && need.mrUrl ? [need.mrUrl] : []);
  const runMrs = [...new Set(((run && run.gitlabIssues) || []).flatMap((issue) =>
    (issue.mergedMrs || []).map((mr) => String((mr && (mr.web_url || mr.url)) || '')).filter(Boolean)
  ))];
  return {
    id: `run:${String((run && run.runId) || '')}`,
    taskId: task ? task.id : (run && run.taskId) || null,
    taskStatus: task ? String(task.status || '') : '',
    title: (need && need.title) || (task && task.title) || String((run && run.runId) || '成果'),
    summary: String((run && run.final && run.final.summary) || ''),
    readOnly: true,
    decided: true,
    delivery: needDelivery.length ? needDelivery : workspaceDelivery,
    mrUrls: needMrs.length ? needMrs : runMrs,
  };
}

// 要対応から成果を開く場合は、完了runの差分情報と要対応の判断状態を合成する。
// 未承認の検証失敗は readOnly にせず、ファイル差分を見ながら同じダイアログ内で
// 「承認して完了」「差し戻し」を実行できるようにする。
function needArtifactReviewViewModel(project, need, run) {
  const artifacts = runArtifactViewModel(project, run);
  const completed = Boolean(completedTaskForNeed(project, need));
  const needDelivery = (need && need.delivery) || [];
  const needMrs = (need && need.mrUrls) || (need && need.mrUrl ? [need.mrUrl] : []);
  return {
    ...need,
    ...artifacts,
    id: need.id,
    taskId: need.taskId || need.id,
    kind: need.kind || 'blocked',
    file: need.file || '',
    decided: Boolean(need.decided),
    readOnly: completed || Boolean(need.decided),
    delivery: artifacts.delivery && artifacts.delivery.length ? artifacts.delivery : needDelivery,
    mrUrls: artifacts.mrUrls && artifacts.mrUrls.length ? artifacts.mrUrls : needMrs,
  };
}

function runArtifactsButtonHtml(run) {
  if (!run || String(run.status) !== 'done') return '';
  return `<button type="button" class="primary-inline" data-run-artifacts="${esc(run.runId)}">成果を見る</button>`;
}

function deliveryReviewFooterHtml(need) {
  if (need.readOnly) {
    const label = need.taskStatus ? statusLabel(need.taskStatus) : '関連タスクなし';
    return `<h3>タスクの状態</h3><p><span class="status-chip st-${esc(need.taskStatus || '')}">${esc(label)}</span></p>`;
  }
  return `<h3>要確認コメント・操作</h3>${commandFailureHtml(need)}${commandReceiptHtml(need)}${
    need.decided || (!need.commandFailure && isNeedSent(need))
      ? '<p class="muted">この要確認項目には回答済みです。</p>'
      : needActionsHtml(need, { inReview: true })
  }`;
}

function deliveryRepoMetaHtml(entry) {
  // ブランチ名・パスは長くなりがちなので 1 項目 1 行で出す（中黒区切りの 1 行連結は読めない）
  const bits = [];
  if (entry.branch) bits.push(`作業ブランチ <code>${esc(entry.branch)}</code>`);
  if (entry.target || entry.base) bits.push(`ターゲット <code>${esc(entry.target || entry.base)}</code>`);
  if (entry.base && entry.base !== entry.target) bits.push(`ベース <code>${esc(entry.base)}</code>`);
  if (entry.path) bits.push(`所在 <code>${esc(entry.path)}</code>`);
  if (entry.url && entry.role === 'reference') bits.push(`URL ${esc(entry.url)}`);
  return bits.map((b) => `<div class="delivery-repo-meta-line">${b}</div>`).join('');
}

function renderDeliveryRepo(entry, idx) {
  const role = deliveryRoleLabel(entry.role);
  const files = entry.files || [];
  const mr = entry.mr_url || '';
  // 解決済み ref、または branch 指定のない現在の作業ツリーだけをローカル表示する。
  // branch 名だけでは fetch 失敗時に誤誘導するため表示しない。
  const canDiff = Boolean(
    entry.path && entry.role !== 'reference'
  );
  const unresolved = entry.role !== 'reference' && entry.branch && !entry.ref;
  const fileBtns = files
    .map((f) => {
      const abs = entry.path ? `${String(entry.path).replace(/[/\\]$/, '')}/${f}` : '';
      const openBtn = abs
        ? `<button class="delivery-file-open" data-open="${esc(abs)}" title="エディタで開く: ${esc(abs)}">開く</button>`
        : '';
      const diffBtn = canDiff
        ? `<button class="delivery-file-button" data-delivery-file data-delivery-diff="${esc(idx)}" data-file="${esc(f)}" aria-selected="false" title="${esc(f)} の差分を表示"><code>${esc(f)}</code></button>`
        : '';
      return `<li>${diffBtn || `<code class="delivery-file-label">${esc(f)}</code>`}${openBtn}</li>`;
    })
    .join('');
  return `<section class="delivery-repo" data-delivery-idx="${esc(idx)}">
    <header class="delivery-repo-head">
      <h3>${esc(entry.name || 'repo')} <span class="muted">（${esc(role)}）</span></h3>
      <div class="row">
        ${mr ? `<button class="primary-inline" data-delivery-mr="${esc(mr)}">GitLab MR を開く</button>` : ''}
        ${canDiff ? `<button data-delivery-diff="${esc(idx)}" data-file="">リポジトリ全体</button>` : ''}
      </div>
    </header>
    <div class="muted delivery-repo-meta">
      ${deliveryRepoMetaHtml(entry)}
    </div>
    ${
      entry.role === 'reference'
        ? '<p class="muted">参照リポジトリです。成果差分は書込先を確認してください。</p>'
        : unresolved
          ? '<p class="muted">作業ブランチの ref をローカルで解決できていません。MR があればそちらで差分を確認してください。</p>'
        : files.length
          ? `<ul class="delivery-files">${fileBtns}</ul>`
          : '<p class="muted">変更ファイルはありません。</p>'
    }
    ${entry.diff_cmd ? `<pre class="mono delivery-cmd">${esc(entry.diff_cmd)}</pre>` : ''}
  </section>`;
}

async function openDeliveryReview(needId) {
  const need = state.project && state.project.needs.find((item) => item.id === needId);
  if (!need) return toast('要対応項目が見つかりません');
  return openDeliveryArtifactsModel(need, `検収物を確認 — ${needDisplayTitle(need)}`);
}

async function openRunArtifacts(runId) {
  const selected = state.flowRun && state.flowRun.run;
  const run = selected && selected.runId === runId
    ? selected
    : state.flowRuns.find((item) => item.runId === runId);
  if (!run || String(run.status) !== 'done') return toast('完了runの成果が見つかりません');
  const model = runArtifactViewModel(state.project, run);
  return openDeliveryArtifactsModel(model, `成果を見る — ${model.title}`);
}

async function openNeedArtifacts(needId) {
  const project = state.project;
  const need = project && project.needs.find((item) => item.id === needId);
  if (!need) return toast('要対応項目が見つかりません');
  // 閲覧は系統フォールバック付き（リトライ中でも旧世代の完了成果を見られる）。
  // 承認可否の判定は completedRunForNeed（最新試行の done）側が担い、ここでは変えない。
  const run = artifactRunForNeed(project, need, state.flowRuns);
  const task = completedTaskForNeed(project, need) || taskForNeed(project, need);
  let artifactRun = run;
  if (run && state.flowRun && state.flowRun.run && state.flowRun.run.runId === run.runId) {
    artifactRun = state.flowRun.run;
  } else if (run) {
    const loaded = await guard('成果runの読込', () =>
      api.flowRun(project.dir, project.busDir, run.runId)
    );
    if (loaded && loaded.run) artifactRun = loaded.run;
  }
  const model = artifactRun
    ? needArtifactReviewViewModel(project, need, artifactRun)
    : {
        ...need,
        taskStatus: task ? String(task.status || 'done') : 'done',
        readOnly: Boolean(completedTaskForNeed(project, need) || need.decided),
        decided: Boolean(need.decided),
      };
  return openDeliveryArtifactsModel(model, `成果を確認 — ${needDisplayTitle(need)}`);
}

async function openDeliveryArtifactsModel(need, title) {
  const rawEntries = need.delivery && need.delivery.length ? need.delivery : [];
  const mrs = need.mrUrls && need.mrUrls.length ? need.mrUrls : need.mrUrl ? [need.mrUrl] : [];
  $('delivery-review-title').textContent = title;
  $('delivery-review-body').innerHTML = '<p class="muted">変更ファイル一覧を取得しています…</p>';
  if (!$('dlg-delivery-review').open) $('dlg-delivery-review').showModal();
  const entries = await hydrateDeliveryEntries(rawEntries);
  if (!$('dlg-delivery-review').open) return;
  const reviewState = deliveryReviewState(entries, mrs);
  const discoveryFailed = entries.some((entry) => entry.discovery === 'failed');
  const mrBlock = mrs.length
    ? `<section class="delivery-mr-banner">
        <p>GitLab 上で差分を確認できます（gitlab executor / タスク MR）。</p>
        <div class="row">${mrs
          .map(
            (u, i) =>
              `<button class="primary-inline" data-delivery-mr="${esc(u)}">GitLab MR を開く${
                mrs.length > 1 ? ` #${i + 1}` : ''
              }</button>`
          )
          .join('')}</div>
      </section>`
    : '';
  const repos =
    entries.length > 0
      ? entries.map((e, i) => renderDeliveryRepo(e, i)).join('')
      : '<p class="muted">構造化された検収物情報がありません。判断材料の本文を確認してください。</p>';
  const canShowAllDiffs = reviewState.fileCount > 0 && entries.some(
    (entry) => entry.role !== 'reference' && entry.path
  );
  const allDiffs = canShowAllDiffs
    ? `<button class="primary-inline" data-delivery-all-diff>すべての差分を表示</button>`
    : '';
  const assistToolbar = !need.decided && !isNeedSent(need)
    ? `<div class="delivery-review-toolbar delivery-assist-toolbar">
        ${allDiffs}
        <button type="button" data-delivery-rationale="${esc(need.id)}">変更理由を説明</button>
        <button type="button" data-delivery-followup="${esc(need.id)}">フォローアップ案</button>
      </div>`
    : allDiffs
      ? `<div class="delivery-review-toolbar">${allDiffs}</div>`
      : '';
  const emptyNotice = reviewState.hasContent
    ? ''
    : `<section class="delivery-empty-state" role="status">
        <h3>${discoveryFailed ? '変更ファイル一覧を取得できませんでした' : '変更ファイルはありません'}</h3>
        <p>${discoveryFailed
          ? 'リポジトリの情報またはGitの状態を確認し、もう一度開いてください。'
          : '現在の比較条件では、検収対象となる変更を検出しませんでした。'}</p>
      </section>`;
  // このダイアログの主目的はファイル差分の検収。run の summary は複数ノード分が長くなり、
  // overflow:hidden の本文内で差分レイアウト全体を画面外へ押し出すため、ここには載せない。
  $('delivery-review-body').innerHTML = `${mrBlock}${emptyNotice}
    <div id="delivery-assist-panel" class="delivery-assist-panel hidden" aria-live="polite"></div>
    <div class="delivery-review-layout">
      <aside class="delivery-file-panel" aria-label="変更ファイル">
        <div class="delivery-file-panel-title">
          <strong>変更ファイル</strong>
          <span class="muted">${entries.reduce((n, entry) => n + Number(entry.files_total || (entry.files || []).length), 0)}件</span>
        </div>
        ${assistToolbar}
        <div class="delivery-repos">${repos}</div>
      </aside>
      <section class="delivery-diff-panel" aria-label="ファイル差分">
        <header class="delivery-diff-head">
          <strong id="delivery-diff-title">${reviewState.hasContent ? '差分を表示するファイルを選択してください' : '表示できる差分はありません'}</strong>
          <span class="muted delivery-diff-mode">比較表示</span>
        </header>
        <div id="delivery-diff-view" class="delivery-diff-view" tabindex="0" aria-live="polite"></div>
        <footer class="delivery-review-actions">
          ${deliveryReviewFooterHtml(need)}
        </footer>
      </section>
    </div>`;
  wireDeliveryReview($('dlg-delivery-review'), { ...need, delivery: entries });
  const reviewInput = $('dlg-delivery-review').querySelector('.delivery-review-actions .need-input');
  if (reviewInput) {
    reviewInput.value = state.needsDrafts[need.id] || '';
    reviewInput.addEventListener('input', () => {
      state.needsDrafts[need.id] = reviewInput.value;
    });
  }
  const firstFile = $('dlg-delivery-review').querySelector('[data-delivery-file]');
  if (firstFile) firstFile.click();
}

function deliveryDiffRequest(entry, file = '', opts) {
  return {
    repo: entry.path,
    base: entry.target || entry.base || 'main',
    ref: entry.ref || undefined,
    // 作業ブランチが分かれば origin/<branch> を優先（fetch 後は今 push されている最新を検収する）。
    branch: entry.branch || undefined,
    fetch: Boolean(opts && opts.fetch),
    file: file || undefined,
    // ref も branch も無い＝ローカル作業ツリー比較。branch があれば origin/<branch> で range 比較。
    workingTree: !entry.ref && !entry.branch,
  };
}

function isDeliveryArtifactFile(file) {
  return !/(^|\/)\.agent-project\//.test(String(file || '').replace(/\\/g, '/'));
}

async function hydrateDeliveryEntries(entries) {
  return Promise.all((entries || []).map(async (entry) => {
    const fallbackFiles = (entry.files || []).filter(isDeliveryArtifactFile);
    const fallback = { ...entry, files: fallbackFiles, files_total: fallbackFiles.length };
    const canLoad = entry.role !== 'reference' && entry.path;
    if (!canLoad) return { ...fallback, discovery: 'unavailable' };
    try {
      // 検収を開いた最初の解決でリモートを取り込む（以降のファイル選択は取得済みの origin を使う）。
      const result = await api.gitDiff(deliveryDiffRequest(entry, '', { fetch: true }));
      const files = (result.files || []).filter(isDeliveryArtifactFile);
      return { ...entry, files, files_total: files.length, discovery: 'complete' };
    } catch (err) {
      uiLog('delivery file list fallback', entry.name || 'repo', err && err.message ? err.message : err);
      return { ...fallback, discovery: 'failed' };
    }
  }));
}

function deliveryDiffOutputFormat(diffPaneWidth) {
  // ウィンドウ幅ではなく、ファイル一覧を除いた実際の描画領域で判定する。
  // diff2html の左右表示は各ペインが約 360px を下回ると、行番号・強調表示・長い行が
  // 同じ狭い領域へ押し込まれるため、720px 未満では一列表示へ切り替える。
  return Number(diffPaneWidth) < 720 ? 'line-by-line' : 'side-by-side';
}

let deliveryDiffResizeObserver = null;

function observeDeliveryDiffWidth(view) {
  if (typeof ResizeObserver !== 'function') return;
  if (deliveryDiffResizeObserver) deliveryDiffResizeObserver.disconnect();
  deliveryDiffResizeObserver = new ResizeObserver((entries) => {
    const width = entries[0] && entries[0].contentRect ? entries[0].contentRect.width : view.clientWidth;
    const next = deliveryDiffOutputFormat(width);
    const current = view.dataset.diffOutputFormat || '';
    if (current && next !== current && view._deliveryDiffText) {
      renderDeliveryDiff(view._deliveryDiffText);
    }
  });
  deliveryDiffResizeObserver.observe(view);
}

function renderDeliveryDiff(diffText) {
  const view = $('delivery-diff-view');
  if (deliveryDiffResizeObserver) deliveryDiffResizeObserver.disconnect();
  view._deliveryDiffText = diffText;
  view.replaceChildren();
  if (!diffText) {
    delete view.dataset.diffOutputFormat;
    view.textContent = '(差分なし)';
    return;
  }
  if (typeof Diff2HtmlUI !== 'function') {
    const fallback = document.createElement('pre');
    fallback.className = 'mono delivery-diff-fallback';
    fallback.textContent = diffText;
    view.appendChild(fallback);
    return;
  }
  const outputFormat = deliveryDiffOutputFormat(view.clientWidth);
  view.dataset.diffOutputFormat = outputFormat;
  const mode = $('dlg-delivery-review').querySelector('.delivery-diff-mode');
  if (mode) mode.textContent = outputFormat === 'side-by-side' ? '左右比較' : '行単位';
  const ui = new Diff2HtmlUI(view, diffText, {
    drawFileList: false,
    fileContentToggle: false,
    matching: 'lines',
    outputFormat,
    synchronisedScroll: true,
    highlight: true,
    colorScheme: 'dark',
  });
  ui.draw();
  observeDeliveryDiffWidth(view);
}

async function collectDeliveryDiffSections(need, { maxChars = 80000 } = {}) {
  const entries = (need.delivery || []).filter(
    (entry) => entry.role !== 'reference' && entry.path
  );
  const sections = await Promise.all(
    entries.map(async (entry) => {
      const compareBase = entry.target || entry.base;
      const label = entry.ref
        ? `${entry.base || 'main'}...${entry.ref}`
        : entry.branch
          ? `${compareBase || 'main'}...origin/${entry.branch}`
          : compareBase
            ? `${compareBase} との差分（作業ツリー）`
            : '現在の作業ツリー（HEADとの差分）';
      const files = (entry.files || []).slice(0, 40);
      try {
        const res = await api.gitDiff(deliveryDiffRequest(entry, '', { fetch: true }));
        let text = res.text || '(差分なし)';
        if (text.length > maxChars) {
          text = `${text.slice(0, maxChars)}\n…（差分が長いため省略）`;
        }
        return {
          name: entry.name || 'repo',
          label,
          files,
          text,
        };
      } catch (err) {
        const message = err && err.message ? err.message : String(err);
        return {
          name: entry.name || 'repo',
          label,
          files,
          text: `差分の取得に失敗しました: ${message}`,
        };
      }
    })
  );
  return sections;
}

function showDeliveryAssistPanel(html) {
  const panel = $('delivery-assist-panel');
  if (!panel) return;
  panel.classList.remove('hidden');
  panel.innerHTML = html;
}

function renderFollowupSuggestions(needId, fields, meta = {}) {
  const suggestions = (fields && fields.suggestions) || [];
  if (!suggestions.length) {
    showDeliveryAssistPanel(
      `<section class="delivery-assist-card">
        <h3>フォローアップ案</h3>
        <p class="muted">${esc((fields && fields.rationale) || '追加タスク案はありませんでした。')}</p>
      </section>`
    );
    return;
  }
  const model = meta.model ? ` / ${meta.model}` : '';
  showDeliveryAssistPanel(
    `<section class="delivery-assist-card">
      <header class="delivery-assist-head">
        <h3>フォローアップ案</h3>
        <span class="muted">${esc(meta.cli || '')}${esc(model)}</span>
      </header>
      ${fields.rationale ? `<p>${esc(fields.rationale)}</p>` : ''}
      <ul class="followup-suggest-list">${suggestions
        .map(
          (s, i) => `<li>
            <div><strong>${esc(s.title)}</strong>
              ${s.after && s.after.length ? `<span class="muted">after: ${esc(s.after.join(', '))}</span>` : ''}
              <span class="muted">p${esc(s.priority)}</span>
            </div>
            ${s.why ? `<div class="muted">なぜ: ${esc(s.why)}</div>` : ''}
            <div class="muted">${esc(s.verify || s.accept || '')}</div>
            <button type="button" class="primary-inline" data-followup-enqueue="${i}">タスク追加フォームへ</button>
          </li>`
        )
        .join('')}</ul>
    </section>`
  );
  const panel = $('delivery-assist-panel');
  for (const btn of panel.querySelectorAll('[data-followup-enqueue]')) {
    btn.addEventListener('click', () => {
      const s = suggestions[Number(btn.dataset.followupEnqueue)];
      if (!s) return;
      $('dlg-delivery-review').close();
      openEnqueueDialog({
        title: s.title,
        verify: s.verify,
        accept: s.accept,
        priority: s.priority,
        after: s.after,
        note: s.note || `検収 ${needId} のフォローアップ`,
        ...Object.fromEntries(GUIDE_KEYS.map((k) => [k, s[k] || ''])),
      });
    });
  }
}

async function openDeliveryFollowup(needId) {
  const p = state.project;
  const need = p && p.needs.find((item) => item.id === needId);
  if (!need) return toast('要対応項目が見つかりません');
  if (state.assistBusy) return;
  state.assistBusy = true;
  showDeliveryAssistPanel('<p class="muted">フォローアップ案を作成しています…</p>');
  try {
    const task = taskForNeed(p, need);
    const diffs = await collectDeliveryDiffSections(need, { maxChars: 40000 });
    const res = await api.agentTaskAssist({
      dir: p.dir,
      mode: 'followup-suggest',
      context: {
        charter: charterAssistContext(p),
        backlog: backlogAssistRows(p),
        selected: {
          needId: need.id,
          title: needDisplayTitle(need),
          risk: need.risk,
          why: need.why,
          summary: need.summary,
          task: task
            ? {
                id: task.id,
                title: task.title,
                verify: task.verify,
                accept: task.accept,
                priority: task.priority,
                after: task.after,
              }
            : null,
          files: diffs.flatMap((d) => (d.files || []).map((f) => `${d.name}:${f}`)),
          diffExcerpt: diffs.map((d) => `===== ${d.name} · ${d.label} =====\n${d.text}`).join('\n\n'),
        },
      },
    });
    renderFollowupSuggestions(need.id, res.fields || {}, { cli: res.cli, model: res.model });
  } catch (err) {
    showDeliveryAssistPanel(
      `<div class="doctor-error" role="alert">フォローアップ案を作成できませんでした: ${esc(err.message || err)}</div>`
    );
  } finally {
    state.assistBusy = false;
  }
}

function wireDeliveryReview(root, need) {
  for (const btn of root.querySelectorAll('[data-delivery-mr]')) {
    btn.addEventListener('click', () => {
      const url = btn.getAttribute('data-delivery-mr');
      guard('GitLab MR を開く', () => api.openExternal(url));
    });
  }
  for (const btn of root.querySelectorAll('[data-open]')) {
    btn.addEventListener('click', () => guard('ファイルを開く', () => api.openPath(btn.dataset.open)));
  }
  for (const btn of root.querySelectorAll('button[data-act]')) {
    btn.addEventListener('click', async () => {
      const ok = await handleNeedAction(btn);
      if (ok) $('dlg-delivery-review').close();
    });
  }
  for (const btn of root.querySelectorAll('[data-delivery-rationale]')) {
    btn.addEventListener('click', () => openDeliveryRationale(btn.dataset.deliveryRationale));
  }
  for (const btn of root.querySelectorAll('[data-delivery-followup]')) {
    btn.addEventListener('click', () => openDeliveryFollowup(btn.dataset.deliveryFollowup));
  }
  const allDiffs = root.querySelector('[data-delivery-all-diff]');
  if (allDiffs) {
    allDiffs.addEventListener('click', async () => {
      const view = $('delivery-diff-view');
      root.querySelectorAll('[data-delivery-file]').forEach((item) => {
        item.classList.remove('active');
        item.setAttribute('aria-selected', 'false');
      });
      $('delivery-diff-title').textContent = 'すべての変更';
      view.setAttribute('aria-busy', 'true');
      view.textContent = 'すべての差分を取得しています…';
      allDiffs.disabled = true;
      try {
        const sections = await collectDeliveryDiffSections(need);
        renderDeliveryDiff(
          sections.map((s) => `===== ${s.name} · ${s.label} =====\n${s.text}`).join('\n\n')
        );
        view.scrollTop = 0;
        view.focus();
      } finally {
        view.removeAttribute('aria-busy');
        allDiffs.disabled = false;
      }
    });
  }
  for (const btn of root.querySelectorAll('[data-delivery-diff]')) {
    btn.addEventListener('click', async () => {
      const idx = Number(btn.getAttribute('data-delivery-diff'));
      const entry = (need.delivery || [])[idx];
      if (!entry || !entry.path) return toast('ローカル path が無いため差分を取得できません');
      const file = btn.getAttribute('data-file') || '';
      const view = $('delivery-diff-view');
      root.querySelectorAll('[data-delivery-file]').forEach((item) => {
        const selected = item === btn;
        item.classList.toggle('active', selected);
        item.setAttribute('aria-selected', String(selected));
      });
      $('delivery-diff-title').textContent = file || `${entry.name || 'repo'} のすべての変更`;
      view.setAttribute('aria-busy', 'true');
      view.textContent = '差分を取得しています…';
      try {
        const res = await api.gitDiff(deliveryDiffRequest(entry, file));
        renderDeliveryDiff(res.text || '');
        view.scrollTop = 0;
      } catch (err) {
        view.textContent = `差分の取得に失敗しました: ${err && err.message ? err.message : err}`;
      } finally {
        view.removeAttribute('aria-busy');
      }
    });
  }
}

function specFilesHtml(p, n) {
  const spec = specForNeed(p, n);
  if (!spec) return '';
  const buttons = spec.files
    .map((f) => `<button data-open="${esc(f.path)}" title="${esc(f.path)}">📄 ${esc(f.name)}</button>`)
    .join('');
  return `<div class="row" style="gap:6px;margin-top:4px"><span class="label-chip">Spec</span>${buttons}</div>`;
}

function needBucket(n, sentFn) {
  if (n.decided) return 'done';
  // 送信後に本体側で取り込みが失敗した指示（commands/*.err → n.commandFailure）は
  // 「送信済み」に隠さない。失敗の事実と理由を見せて、次の操作をできるようにする。
  if (n.commandFailure) return 'open';
  return sentFn(n) ? 'sent' : 'open';
}

// 待ち時間（ミリ秒）を人間可読ラベルにする純関数。
function humanizeAge(ms) {
  const m = Math.floor((Number(ms) || 0) / 60000);
  if (m < 1) return 'たった今';
  if (m < 60) return `${m}分待ち`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}時間待ち`;
  const d = Math.floor(h / 24);
  return `${d}日待ち`;
}

// 要対応の待ち時間と SLA レベルを求める純関数（テスト対象）。
//   need.mtime（最終更新＝この判断待ちが最後に立った時刻の近似）、無ければ date を使う。
//   slaHours 以上で 'danger'（赤）、その 1/3 以上で 'warn'（黄）、それ未満は ''（色なし）。
// 停滞している判断待ちを一目で分かるようにする（人待ちで下流が止まっている時間の可視化）。
function needAgeInfo(need, nowMs, slaHours) {
  const sla = Math.max(1, Number(slaHours) || 24);
  let since = Number(need && need.mtime) || 0;
  if (!since && need && need.date) {
    const t = Date.parse(need.date);
    if (!Number.isNaN(t)) since = t;
  }
  if (!since) return { ms: 0, label: '', level: '' };
  const ms = Math.max(0, (Number(nowMs) || 0) - since);
  const hours = ms / 3600000;
  const level = hours >= sla ? 'danger' : hours >= sla / 3 ? 'warn' : '';
  return { ms, label: humanizeAge(ms), level };
}

function needsViewModel(needs, filter, selectedId, sentFn) {
  const sorted = [...(needs || [])].sort(
    (a, b) =>
      String(b.date || '').localeCompare(String(a.date || '')) ||
      String(a.id).localeCompare(String(b.id))
  );
  const counts = { open: 0, sent: 0, done: 0 };
  for (const n of sorted) counts[needBucket(n, sentFn)] += 1;
  let items = filter === 'gitlab' ? [] : sorted.filter((n) => needBucket(n, sentFn) === filter);
  // 未対応（open）は「待ち時間の長い順」＝停滞している判断待ちを上に出す（省力トリアージ）。
  // 既定の選択（items[0]）も最も停滞したカードになり、最優先の判断へ自然に誘導する。
  if (filter === 'open') {
    items = [...items].sort((a, b) => (Number(a.mtime) || 0) - (Number(b.mtime) || 0));
  }
  const selected = items.find((n) => n.id === selectedId) || items[0] || null;
  return { counts, items, selected, selectedId: selected ? selected.id : null };
}

// 検証失敗の表示モデル。**解析済みの事実（failureSummary / failureContext）だけを使う。**
//
// 以前はここが散文を正規表現で読み、「検証」と「FAIL」が同じ行にあれば検証失敗と断定して
// 要約文を組み立てていた。判定が必ず何かを返そうとするので「分からない」が表現できず、
// 実行制御で着手前に止まった run にも「検証コマンドが失敗しました。」と出す——verify は
// 一度も走っていないのに、人は存在しないテスト失敗を探しに行くことになる。
// 解析は producer（main/project.js の _diagnoseFailure）に一本化し、ここは運ぶだけにする。
// 解析できなかった失敗は要約を作らず、生の判断材料と「なぜ」をそのまま読ませる。
function needFailureViewModel(need) {
  if (!need) return null;
  const summary = String((need && need.failureSummary) || '').trim();
  if (!summary) return null;
  if (/verify\s*未定義/i.test(summary)) return null;
  return {
    summary,
    resolution: String(need.failureResolution || ''),
    context: need.failureContext || null,
  };
}

// AI 診断を出すか。解析できなかった失敗こそ診断の出番なので、ここでは散文からの推測を残す。
// 「助けを出すか」の判断に外れがあっても、余計なボタンが 1 つ出るだけで害はない。
// 起きたことの断定（needFailureViewModel）には使わない——そちらの外れは、走っていない
// 検証を「失敗しました」と言い切ることになる。推測してよい場所を、この 2 つで分けている。
function canDiagnoseNeed(need) {
  if (!need) return false;
  if (needFailureViewModel(need)) return true;
  const prose = [need.why, need.detail].filter(Boolean).join('\n');
  if (/verify\s*未定義/i.test(prose)) return false;
  return /(?:検証|verify|テスト|test|回帰|コマンド)[^\n]*(?:失敗|FAIL|NG|exit\s*=\s*[1-9]\d*)/i.test(prose);
}

function needAssistActionsHtml(need, settled) {
  const specialized = [];
  if (!settled && need.kind === 'plan-review') {
    specialized.push(`<button class="primary-inline" data-plan-critique="${esc(need.id)}">AIで計画を批評</button>`);
  }
  if (!settled && need.kind === 'review') {
    specialized.push(`<button type="button" data-delivery-rationale="${esc(need.id)}">変更理由を説明</button>`);
  }
  if (!settled && canDiagnoseNeed(need)) {
    specialized.push(`<button class="primary-inline" data-failure-diagnose="${esc(need.id)}">AIで失敗を診断</button>`);
  }
  if (specialized.length) return specialized.join('');
  return `<button type="button" data-need-consult="${esc(need.id)}">AIに相談</button>`;
}

function needListSummary(need) {
  const failure = needFailureViewModel(need);
  return failure ? failure.summary : (NEED_ASK[need.kind] || NEED_ASK.blocked);
}

function needListItemViewModel(need, bucket, age) {
  const stateText = { open: '未対応', sent: '送信済み', done: '回答済み' }[bucket] || String(bucket || '未対応');
  const risk = String((need && need.risk) || '');
  return {
    id: String((need && need.id) || ''),
    state: String(bucket || 'open'),
    stateText,
    kindText: needKindLabel(need && need.kind),
    title: needDisplayTitle(need || {}),
    decision: need && need.commandFailure
      ? `${COMMAND_ACTION_LABELS[need.commandFailure.action] || need.commandFailure.action}の取り込みに失敗しました — 詳細を開いて理由を確認してください`
      : needListSummary(need || {}),
    failure: Boolean(needFailureViewModel(need)) || Boolean(need && need.commandFailure),
    owner: String((need && need.owner) || '').trim(),
    risk,
    riskText: RISK_LABELS[risk] || 'リスク未設定',
    ageText: String((age && age.label) || '—'),
    ageLevel: String((age && age.level) || ''),
  };
}

function needListItemHtml(item, selected, slaHours) {
  const stateClass = { open: 'st-blocked', sent: 'st-review', done: 'st-done' }[item.state] || '';
  const riskClass = item.risk ? ` risk-${esc(item.risk)}` : '';
  const ageTitle = item.ageText === '—'
    ? '待ち時間の計測対象外です'
    : `最終更新からの経過時間（SLA ${esc(slaHours)}h 超で赤）`;
  return `<button type="button" class="need-list-item ${selected ? 'selected' : ''}" data-need-select="${esc(item.id)}"
    role="listitem" aria-pressed="${selected}"${selected ? ' aria-current="true"' : ''} aria-label="${esc(item.title)}の詳細を開く">
    <span class="need-list-type" data-label="状態と種類">
      <span class="status-chip ${stateClass}">${esc(item.stateText)}</span>
      <span class="need-list-kind">${esc(item.kindText)}</span>
      <span class="risk-badge${riskClass}">${esc(item.riskText)}</span>
      ${item.owner ? ownerBadgeHtml(item.owner) : ''}
    </span>
    <strong class="need-list-title" data-label="確認事項">${esc(item.title)}</strong>
    <span class="need-list-summary ${item.failure ? 'failure' : ''}" data-label="判断すること">${esc(item.decision)}</span>
    <span class="need-list-age ${esc(item.ageLevel)}" data-label="待ち時間" title="${ageTitle}">${esc(item.ageText)}</span>
    <svg class="need-list-chevron" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m9 18 6-6-6-6" /></svg>
  </button>`;
}

function renderNeedFacts(n) {
  const facts = [];
  const failure = needFailureViewModel(n);
  if (failure) {
    facts.push(`<div class="need-diag"><span class="label-chip">検証失敗</span><strong>${inlineMd(failure.summary)}</strong></div>`);
    if (failure.resolution) {
      facts.push(`<div class="need-resolution"><span class="label-chip">確認・対処</span>${inlineMd(failure.resolution)}</div>`);
    }
    if (failure.context) {
      const contextRows = [
        ['分類', failure.context.category],
        ['対処対象', failure.context.owner],
        ['コマンド', failure.context.command],
        ['作業場所', failure.context.workdir],
        ['終了コード', failure.context.exitCode],
        ['確認対象', failure.context.resolvedTarget || failure.context.target],
      ].filter(([, value]) => String(value || '').trim());
      if (contextRows.length) {
        facts.push(`<dl class="need-failure-context">${contextRows.map(([label, value]) =>
          `<div><dt>${esc(label)}</dt><dd>${label === 'コマンド' ? `<code>${esc(value)}</code>` : esc(value)}</dd></div>`
        ).join('')}</dl>`);
      }
    }
  }
  if (n.why) facts.push(`<div><span class="label-chip">理由</span>${prosePreview(n.why, 240)}</div>`);
  if (n.summary) facts.push(`<div><span class="label-chip">概況</span>${prosePreview(n.summary, 280)}</div>`);
  const d = n.diff;
  if (d && d.hasDiff && (d.artifacts.length || d.internal.length)) {
    const parts = [
      d.artifacts.length ? `成果物 ${d.artifacts.length} 件` : '<b>成果物の変更なし</b>',
    ];
    if (d.internal.length) parts.push(`実行記録 ${d.internal.length} 件`);
    if (d.truncated) parts.push(`ほか ${d.truncated} 件`);
    facts.push(`<div><span class="label-chip">変更</span> ${parts.join(' / ')}</div>`);
    if (d.artifacts.length) {
      const files = d.artifacts
        .slice(0, 8)
        .map((f) => `<button data-open="${esc(f)}" title="${esc(f)}">${esc(f.split('/').pop())}</button>`)
        .join('');
      facts.push(`<div class="row need-files">${files}</div>`);
    }
  }
  const deliveryMrs = n.mrUrls && n.mrUrls.length ? n.mrUrls : n.mrUrl ? [n.mrUrl] : [];
  const deliveryState = deliveryReviewState(n.delivery || [], deliveryMrs);
  if (deliveryState.hasContent || deliveryState.canDiscover) {
    const label = deliveryState.fileCount
      ? `変更ファイル ${deliveryState.fileCount} 件`
      : deliveryState.hasMr
        ? 'GitLab MR'
        : '変更内容を取得して確認';
    facts.push(
      `<div class="row need-delivery-cta">` +
        `<span class="label-chip">検収物</span> ${esc(label)}` +
        `<button class="primary-inline" data-delivery-review="${esc(n.id)}">検収物を確認</button>` +
        `</div>`
    );
  }
  if (n.evidenceThin) {
    const onlyInternal = d && d.hasDiff && !d.artifacts.length && d.internal.length;
    facts.push(
      onlyInternal
        ? '<div class="muted ev-thin-note">変更されたのは実行記録だけで、コードやドキュメントは書き換わっていません。</div>'
        : '<div class="muted ev-thin-note">この実行には成果物リンクや差分がありません。</div>'
    );
  }
  return facts.join('');
}

// 成果物レビューのコメント（チーム運用）: 複数メンバーが成果物にコメントを入れ、
// 監視担当者が確認・整理して承認/再実行を判断する。投稿者名は localStorage に覚える
// （設定画面を増やさない最小構成。空なら「匿名」で保存される）。
function reviewerName() {
  try {
    return localStorage.getItem('kpv:reviewerName') || '';
  } catch {
    return '';
  }
}
function setReviewerName(v) {
  try {
    localStorage.setItem('kpv:reviewerName', String(v || '').trim());
  } catch {
    /* localStorage 不可でも致命的でない */
  }
}

function reviewCommentTime(ts) {
  const s = String(ts || '');
  return s ? s.slice(0, 16).replace('T', ' ') : '';
}

function reviewCommentItemHtml(c) {
  return `<li class="rc-item" data-rc-id="${esc(c.id)}">
    <div class="rc-head">
      <span class="rc-author">👤 ${esc(c.author)}</span>
      <span class="rc-time muted">${esc(reviewCommentTime(c.ts))}${c.editedTs ? '（編集済み）' : ''}</span>
      <span class="spacer"></span>
      <button class="linklike rc-edit-btn" data-rc-edit="${esc(c.id)}">編集</button>
      <button class="linklike danger" data-rc-del="${esc(c.id)}">削除</button>
    </div>
    <div class="rc-text">${esc(c.text)}</div>
  </li>`;
}

// 成果物レビュー（検収待ち＝review／人待ち＝blocked）でだけ出す。plan-review 等では出さない。
function reviewCommentsHtml(n) {
  if (!['review', 'blocked'].includes(n.kind || 'blocked')) return '';
  const comments = n.comments || [];
  const list = comments.length
    ? `<ul class="rc-list">${comments.map(reviewCommentItemHtml).join('')}</ul>`
    : '<p class="muted">まだレビューコメントはありません。メンバーが成果物へコメントを残せます。</p>';
  return `<section class="need-comments" data-rc-need="${esc(n.id)}" data-rc-task="${esc(n.taskId || n.id)}">
    <h3>レビューコメント <span class="muted">（${comments.length}）</span></h3>
    ${list}
    <div class="rc-add">
      <input class="rc-author" placeholder="あなたの名前（コメントに付きます）" value="${esc(reviewerName())}" />
      <textarea class="rc-input" rows="2" placeholder="成果物へのコメント（他のメンバーと担当者が確認できます）"></textarea>
      <div class="row need-buttons"><span class="spacer"></span>
        <button class="primary-inline" data-rc-add>コメントを追加</button></div>
    </div>
  </section>`;
}

function bindReviewComments(root) {
  const section = root.querySelector('.need-comments');
  if (!section) return;
  const p = state.project;
  if (!p) return;
  const needId = section.dataset.rcNeed;
  const taskId = section.dataset.rcTask;
  const authorInput = section.querySelector('.rc-author');
  if (authorInput) {
    authorInput.addEventListener('change', () => setReviewerName(authorInput.value));
  }
  const addBtn = section.querySelector('[data-rc-add]');
  if (addBtn) {
    addBtn.addEventListener('click', async () => {
      const author = authorInput ? authorInput.value.trim() : '';
      const ta = section.querySelector('.rc-input');
      const text = ta ? ta.value.trim() : '';
      if (!text) return toast('コメントを入力してください');
      setReviewerName(author);
      const ok = await guard('コメント追加', async () => {
        const res = await api.addReviewComment(p.dir, taskId, author, text);
        uiLog('addComment', taskId, res);
        toast('コメントを追加しました', true);
        return true;
      });
      if (ok) {
        gitPushAfterWrite(`agent-dashboard: review comment ${taskId}`, p.dir);
        await reloadProject();
      }
    });
  }
  for (const btn of section.querySelectorAll('[data-rc-del]')) {
    btn.addEventListener('click', async () => {
      const yes = await confirmDialog('このレビューコメントを削除します。よろしいですか？');
      if (!yes) return;
      const ok = await guard('コメント削除', async () => {
        const res = await api.deleteReviewComment(p.dir, taskId, btn.dataset.rcDel);
        uiLog('deleteComment', taskId, res);
        toast('コメントを削除しました', true);
        return true;
      });
      if (ok) {
        gitPushAfterWrite(`agent-dashboard: delete comment ${taskId}`, p.dir);
        await reloadProject();
      }
    });
  }
  for (const btn of section.querySelectorAll('[data-rc-edit]')) {
    btn.addEventListener('click', () => {
      const item = btn.closest('.rc-item');
      if (!item || item.querySelector('.rc-edit-box')) return; // 既に編集中
      const need = (p.needs || []).find((x) => x.id === needId);
      const c = ((need && need.comments) || []).find((x) => x.id === btn.dataset.rcEdit);
      if (!c) return;
      const textDiv = item.querySelector('.rc-text');
      const box = document.createElement('div');
      box.className = 'rc-edit-box';
      box.innerHTML = `<textarea class="rc-edit-input" rows="3"></textarea>
        <div class="row need-buttons"><span class="spacer"></span>
          <button class="rc-edit-cancel">キャンセル</button>
          <button class="primary-inline rc-edit-save">保存</button></div>`;
      box.querySelector('.rc-edit-input').value = c.text;
      textDiv.after(box);
      textDiv.style.display = 'none';
      box.querySelector('.rc-edit-cancel').addEventListener('click', () => {
        box.remove();
        textDiv.style.display = '';
      });
      box.querySelector('.rc-edit-save').addEventListener('click', async () => {
        const text = box.querySelector('.rc-edit-input').value.trim();
        if (!text) return toast('コメントを入力してください');
        const ok = await guard('コメント編集', async () => {
          const res = await api.editReviewComment(p.dir, taskId, c.id, text);
          uiLog('editComment', taskId, res);
          toast('コメントを編集しました', true);
          return true;
        });
        if (ok) {
          gitPushAfterWrite(`agent-dashboard: edit comment ${taskId}`, p.dir);
          await reloadProject();
        }
      });
    });
  }
}

function renderNeedDetail(p, n) {
  if (!n) return '<div class="empty need-detail-empty">この状態の項目はありません</div>';
  // 取り込み失敗（commandFailure）があるカードは送信済み扱いにしない＝操作を出し直す
  const settled = n.decided || (!n.commandFailure && isNeedSent(n));
  const chip = n.decided
    ? '<span class="status-chip st-done">回答済み</span>'
    : settled
      ? '<span class="status-chip st-review">送信済み</span>'
      : '<span class="status-chip st-blocked">未対応</span>';
  const detail = (n.detail || '').trim();
  const detailBlock = detail
    ? `<details class="need-detail" data-ui-key="need-detail:${esc(n.id)}">
        <summary>判断材料を見る</summary>
        <div class="body">${mdToHtml(detail)}</div>
      </details>`
    : '';
  const task = taskForNeed(p, n);
  const hint = task ? taskCompletionHint(task, { runs: runsForTask(task.id) }) : null;
  const finalVerificationFailure = needFinalVerificationFailure(p, n, state.flowRuns);
  const ask =
    (hint && hint.needAsk) || NEED_ASK[n.kind] || NEED_ASK.blocked;
  const unsettle =
    hint && hint.unsettledDone
      ? ' <span class="badge warn">実行済み・未確定</span>'
      : '';
  return `<article class="need-detail-card kind-${esc(n.kind || 'blocked')}">
    <button class="mobile-master-back" data-needs-back>一覧へ戻る</button>
    <header class="need-detail-head">
      <div>
        <div class="need-detail-badges">
          <span class="badge" title="${esc(n.kind || 'blocked')}">${esc(needKindLabel(n.kind))}</span>
          ${riskBadgeHtml(n)} ${ownerBadgeHtml(n.owner)} ${chip}${unsettle}
        </div>
        <h2>${esc(needDisplayTitle(n))}</h2>
      </div>
      <span class="muted">${esc(n.date || '')}</span>
    </header>
    ${commandFailureHtml(n)}
    ${commandReceiptHtml(n)}
    ${finalVerificationFailureHtml(finalVerificationFailure)}
    <section class="need-decision">
      <h3>判断すること</h3>
      <p>${esc(ask)}</p>
    </section>
    <section class="need-facts">
      <div class="need-facts-heading">
        <h3>状況</h3>
        <div class="need-assist-actions">
          ${needAssistActionsHtml(n, settled)}
        </div>
      </div>
      ${renderNeedFacts(n) || '<p class="muted">追加の状況説明はありません。</p>'}
    </section>
    ${settled ? '' : `<section class="need-response"><h3>回答</h3>${needActionsHtml(n)}${needVerifyRevisionHtml(p, n)}</section>`}
    <section class="need-evidence">
      <h3>成果物</h3>
      ${specFilesHtml(p, n) || '<p class="muted">関連するSpecはありません。</p>'}
      ${needArtifactsButtonHtml(p, n, state.flowRuns)}
      ${detailBlock}
      <button class="need-output-button subtle-action" data-need-output="${esc(n.id)}">詳細情報を開く</button>
    </section>
    ${reviewCommentsHtml(n)}
  </article>`;
}

function bindNeedDetail(root) {
  bindOrchBlockedBanner(root);
  bindReviewComments(root);
  for (const btn of root.querySelectorAll('button[data-open]')) {
    btn.addEventListener('click', () => guard('ファイルを開く', () => api.openPath(btn.dataset.open)));
  }
  for (const btn of root.querySelectorAll('button[data-act]')) {
    btn.addEventListener('click', () => handleNeedAction(btn));
  }
  for (const btn of root.querySelectorAll('button[data-open-version]')) {
    btn.addEventListener('click', () => openCharterForm(`charters/${btn.dataset.openVersion}.md`));
  }
  for (const btn of root.querySelectorAll('button[data-need-output]')) {
    btn.addEventListener('click', () => openNeedFullOutput(btn.dataset.needOutput));
  }
  for (const btn of root.querySelectorAll('button[data-delivery-review]')) {
    btn.addEventListener('click', () => openDeliveryReview(btn.dataset.deliveryReview));
  }
  for (const btn of root.querySelectorAll('button[data-need-artifacts]')) {
    btn.addEventListener('click', () => openNeedArtifacts(btn.dataset.needArtifacts));
  }
  for (const btn of root.querySelectorAll('button[data-need-consult]')) {
    btn.addEventListener('click', () => {
      state.needsSelectedId = btn.dataset.needConsult;
      openDoctor();
    });
  }
  for (const btn of root.querySelectorAll('button[data-failure-diagnose]')) {
    btn.addEventListener('click', () => openFailureDiagnosis(btn.dataset.failureDiagnose));
  }
  for (const btn of root.querySelectorAll('button[data-plan-critique]')) {
    btn.addEventListener('click', () => openPlanCritique(btn.dataset.planCritique));
  }
  for (const btn of root.querySelectorAll('button[data-delivery-rationale]')) {
    btn.addEventListener('click', () => openDeliveryRationale(btn.dataset.deliveryRationale));
  }
  for (const btn of root.querySelectorAll('button[data-verify-revise]')) {
    btn.addEventListener('click', async () => {
      const p = state.project;
      const need = p && p.needs.find((item) => item.id === btn.dataset.verifyRevise);
      const task = taskForNeed(p, need);
      if (!need || !task) return toast('関連するタスクが見つかりません');
      const panel = btn.closest('.need-verify-revision');
      const revision = buildNeedVerifyRevision(
        p,
        need,
        panel.querySelector('.need-verify-input').value,
        panel.querySelector('.need-verify-feedback').value
      );
      if (!revision) return toast('検証コマンドを変更するか、補足指示を入力してください');
      const yes = await confirmDialog(verifyRevisionConfirmMessage(task, revision));
      if (!yes) return;
      btn.disabled = true;
      const ok = await guard('検証コマンドの変更', async () => {
        const res = await api.runAction({ dir: p.dir, ...revision });
        markNeedSent(need);
        markReviseSent(task);
        uiLog('needVerifyRevision', task.id, res);
        toast(`${task.id} の検証コマンドを変更し、再実行を依頼しました`, true);
        return true;
      });
      if (ok) {
        gitPushAfterWrite(`agent-dashboard: revise verify ${task.id}`, p.dir);
        await reloadProject();
      } else {
        btn.disabled = false;
      }
    });
  }
  const back = root.querySelector('[data-needs-back]');
  if (back) {
    back.addEventListener('click', () => {
      state.needsMobileDetail = false;
      renderNeeds();
    });
  }
}

function captureNeedsScroll(root) {
  const list = root && root.querySelector ? root.querySelector('.master-list') : null;
  const detail = root && root.querySelector ? root.querySelector('.detail-panel') : null;
  return {
    list: list ? Number(list.scrollTop) || 0 : 0,
    detail: detail ? Number(detail.scrollTop) || 0 : 0,
  };
}

function restoreNeedsScroll(root, snapshot, options) {
  const opts = options || {};
  const list = root && root.querySelector ? root.querySelector('.master-list') : null;
  const detail = root && root.querySelector ? root.querySelector('.detail-panel') : null;
  const resetAll = Boolean(opts.resetAll);
  if (list) list.scrollTop = resetAll ? 0 : Number(snapshot && snapshot.list) || 0;
  if (detail) {
    detail.scrollTop = resetAll || opts.resetDetail
      ? 0
      : Number(snapshot && snapshot.detail) || 0;
  }
}

function renderNeeds(options) {
  const renderOptions = options || {};
  const p = state.project;
  const el = $('tab-needs');
  if (!p) {
    el.innerHTML = '';
    return;
  }
  const scrollSnapshot = captureNeedsScroll(el);

  const ae = document.activeElement;
  if (ae && el.contains(ae) && /^(TEXTAREA|INPUT)$/.test(ae.tagName)) return;
  for (const box of el.querySelectorAll('.need-actions')) {
    const input = box.querySelector('.need-input');
    if (input) state.needsDrafts[box.dataset.need] = input.value;
  }

  const model = needsViewModel(p.needs, state.needsFilter, state.needsSelectedId, isNeedSent);
  state.needsSelectedId = model.selectedId;
  const gitlabCount = (state.gitlab.repoIssues || []).length;
  const filters = [
    ['open', '未対応', model.counts.open],
    ['sent', '送信済み', model.counts.sent],
    ['done', '回答済み', model.counts.done],
    ['gitlab', 'GitLab', gitlabCount],
  ];
  // 未対応カードに待ち時間・SLA バッジを出す（停滞の可視化）。id → {label, level} を一度だけ計算し
  // 一覧と署名（sig）で共有する。sig にラベルを含めることで、時間経過でラベルが変わったときにだけ
  // 再描画する（毎分の無駄な再描画を避ける）。
  const now = Date.now();
  const slaHours = (state.config && state.config.projects && Number(state.config.projects.needsSlaHours)) || 24;
  const ages = {};
  if (state.needsFilter === 'open') {
    for (const n of model.items) ages[n.id] = needAgeInfo(n, now, slaHours);
  }
  const sig = JSON.stringify([
    state.needsFilter,
    state.needsSelectedId,
    state.needsMobileDetail,
    filters.map((x) => x[2]),
    p.needs.map((n) => [n.id, n.kind, n.decided, isNeedSent(n), n.why, n.summary, n.risk, n.owner || '', (n.comments || []).map((c) => `${c.id}:${c.editedTs || ''}`).join(','), n.failureSummary || '', n.failureResolution || '', n.failureContext || null, n.commandFailure || null, (n.detail || '').length]),
    model.items.map((n) => (ages[n.id] ? `${ages[n.id].level}|${ages[n.id].label}` : '')),
  ]);
  if (el.dataset.sig === sig && el.childElementCount) return;
  el.dataset.sig = sig;

  const filterButtons = filters
    .map(([key, label, count]) =>
      `<button class="queue-filter ${state.needsFilter === key ? 'active' : ''}"
        data-needs-filter="${key}" aria-pressed="${state.needsFilter === key}">
        <span>${label}</span><strong>${count}</strong>
      </button>`)
    .join('');
  const list = model.items
    .map((n) => {
      const selected = n.id === state.needsSelectedId;
      const age = ages[n.id];
      return needListItemHtml(
        needListItemViewModel(n, needBucket(n, isNeedSent), age),
        selected,
        slaHours
      );
    })
    .join('');

  const gitlab = state.needsFilter === 'gitlab'
    ? '<div class="queue-single"><div id="needs-gitlab"></div></div>'
    : `<div class="master-detail needs-layout ${state.needsMobileDetail ? 'show-detail' : ''}">
        <aside class="master-list" aria-label="要対応一覧">
          ${list
            ? `<div class="need-list-grid" role="list">
                <div class="need-list-header" aria-hidden="true">
                  <span>状態・種類</span><span>確認事項</span><span>判断すること</span><span>待ち時間</span><span></span>
                </div>
                <div class="need-list-items">${list}</div>
              </div>`
            : '<div class="empty need-list-empty">この状態の項目はありません</div>'}
        </aside>
        <main class="detail-panel">${renderNeedDetail(p, model.selected)}</main>
      </div>`;

  el.innerHTML = `<div class="queue-summary" aria-label="要対応の状態">${filterButtons}</div>${gitlab}`;
  restoreNeedsScroll(el, scrollSnapshot, renderOptions);

  for (const btn of el.querySelectorAll('[data-needs-filter]')) {
    btn.addEventListener('click', () => {
      state.needsFilter = btn.dataset.needsFilter;
      state.needsSelectedId = null;
      state.needsMobileDetail = false;
      el.dataset.sig = '';
      renderNeeds({ resetAll: true });
      if (state.needsFilter === 'gitlab') renderGitLab();
    });
  }
  for (const btn of el.querySelectorAll('[data-need-select]')) {
    btn.addEventListener('click', () => {
      state.needsSelectedId = btn.dataset.needSelect;
      state.needsMobileDetail = true;
      el.dataset.sig = '';
      renderNeeds({ resetDetail: true });
    });
  }
  const input = el.querySelector('.need-actions .need-input');
  if (input && state.needsDrafts[state.needsSelectedId]) input.value = state.needsDrafts[state.needsSelectedId];
  bindNeedDetail(el);
  if (state.needsFilter === 'gitlab') renderGitLab();
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
    const feedbackStub = need.synthesized
      ? { id: need.id, kind: need.kind, title: need.title, why: need.why }
      : undefined;
    if (act === 'feedback') {
      await api.submitFeedback(need.file, text, feedbackStub);
      toast(text ? '回答を送信しました（次の実行で反映されます）' : '回答を確定しました', true);
    } else if (act === 'rerun') {
      // 「そのまま再実行」は resume-run（本体が last_run の固定と ready への積み直しを
      // 原子的に行う正規の口）を使う。実行画面の再実行ボタンは以前からこの口だが、
      // 要対応カードだけが needs ファイルへ空フィードバックを書く旧経路のままだった——
      // コマンドも journal も残らないので、失敗しても画面には何も出ず、同じ状態に
      // 戻ったようにしか見えない。再開できる run が無い票だけ従来の口へ落とす。
      const plan = needRerunPlan(p, need);
      if (plan.via === 'resume-run') {
        const res = await api.runAction({
          dir: p.dir,
          action: 'resume-run',
          id: plan.id,
          run: plan.run,
          reason: '要対応画面から再実行（失敗した工程だけやり直し）',
        });
        markNeedSent(need);
        uiLog('needAction rerun', id, res);
        toast('再実行を送信しました（失敗した工程だけやり直します）', true);
      } else {
        await api.submitFeedback(need.file, '', feedbackStub);
        toast('そのまま再実行するよう回答しました', true);
      }
    } else if (act === 'approve') {
      const reason = needApprovalReason(p, need, state.flowRuns, text);
      // 検収待ち（成果がある blocked / review）の承認は完了確定の意図を明示して送る。
      // 本体に文面から推測させない（推測が外れると黙って積み直され、同じ工程を
      // 再実行してまた要対応に戻る往復になっていた）。
      const complete = String(need.kind || 'blocked') === 'review'
        || needHasDeliverable(p, need, state.flowRuns);
      const res = await api.runAction({ dir: p.dir, action: 'approve', id, reason, complete });
      // 指示は commands/CLI 経由で needs ファイル自体は変わらない。取り込みまで
      // カードが未対応のまま残らないよう送信済みマーカーを付ける
      markNeedSent(need);
      uiLog('needAction approve', id, res);
      toast('承認を送信しました（反映まで少し時間がかかることがあります）', true);
    } else if (act === 'hold') {
      const res = await api.runAction({ dir: p.dir, action: 'hold', id, reason: text });
      markNeedSent(need);
      uiLog('needAction hold', id, res);
      toast('保留にしました', true);
    } else if (act === 'reject') {
      const yes = await confirmDialog(rejectConfirmMessage(p, id, '廃止して計画を作り直す'));
      if (!yes) return false;
      const res = await api.runAction({ dir: p.dir, action: 'reject', id, reason: text });
      markNeedSent(need);
      uiLog('needAction reject', id, res);
      toast('却下しました（依存するタスクは計画の再確認に戻ります）', true);
    }
    return true;
  });
  if (ok) {
    gitPushAfterWrite(`agent-dashboard: ${act} ${id}`, p.dir);
    await reloadProject();
  }
  return ok;
}
