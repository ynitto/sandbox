'use strict';

/* global api, Diff2HtmlUI, RoutineUiCache */

const $ = (id) => document.getElementById(id);

const state = {
  config: null,
  discovery: { projects: [], instances: [] },
  selectedDir: null, // 選択中プロジェクトのディレクトリ
  project: null, // readProject のスナップショット
  flowRuns: [],
  engine: null, // engine/status.json のスナップショット（稼働・共有の状況・切り離し）
  flowRunId: null,
  flowRun: null, // {run, events, nodeEvents}
  flowNodeId: null,
  flowRevisionId: null,
  flowGraphMode: 'plan', // plan（計画の変遷）/ dependencies（依存関係）
  flowDetailView: 'overview', // 選択中 run の内部ビュー（overview / graph / history）
  flowMobileDetail: false,
  flowNodeIssue: null, // {token, issue|null}（実行中ノードのイシュー検索結果キャッシュ）
  // GitLab 突き合わせ結果を run 単位でキャッシュする（run を切り替えても保持し、再取得を避ける）。
  // { [runId]: { loading, at, byNode: {[id]:{reconciled,url,issueState,labels,relatedMrs,...}} } }
  flowReconcile: {},
  backlogFilter: 'active',
  backlogOwner: '', // 監視担当フィルタ（'' = 全員 / '__none__' = 未担当 / それ以外 = 担当者名）
  needsFilter: 'open', // open / sent / done / gitlab
  needsSelectedId: null,
  needsMobileDetail: false,
  needsDrafts: {}, // フィルターや選択を切り替えても回答の下書きを保持する
  needOutputCache: {}, // needs file+mtime → 関連runを含む全出力（明示操作時だけロード）
  doctorBusy: false,
  doctorMode: 'consultation', // consultation / failure-diagnosis / plan-critique / delivery-rationale
  doctorNeedId: null, // 診断・批評後の追加質問でも同じ要対応を参照する
  doctorFeedbackDraft: '', // 差し戻し文面案（回答欄へ流し込み用）
  assistBusy: false, // 構造化 Assist（フォローアップ / 依存優先度）の実行中
  enqueueAdjustments: [], // AI が出した既存タスク調整案（人確認後に revise）
  flowFilter: 'active', // フロータブの run フィルタ（active＝非終端のみ／done＝完了・アーカイブ／all）
  gitlab: { enabled: false, byUrl: {}, repoIssues: [], loading: false, flowOnly: true },
  editFile: null, // {dir, name, file}（編集中のプロジェクトファイル）
  enqueueExtra: null, // {level, track}（再投入で引き継ぐが UI に出さない値）
  cowork: null,
  coworkDraft: null,
  coworkEditIndex: -1,
  coworkSelections: {}, // project path → selected routine id
  coworkSearches: { cowork: '' },
  coworkHistoryCache: RoutineUiCache.createBoundedAsyncCache({ max: 12, ttlMs: 60000 }),
  // 委譲公示板（agent-board）の観測。板は端末単位なのでプロジェクト選択と独立。
  boardStatus: null,   // engine/status.json の board ブロック（参加状況・手動入札の可否）
  boardViews: [],      // 板の公示（正規化ビュー）
  boardNodes: [],      // 板の参加ノード（nodes/<id>.json）
  boardCommands: {},   // 投函したノード宛て指示の状況（送信済み → 受理済み / 失敗）
  amigos: null, // amigos:overview のスナップショット { missions, budget, errors }
  amigosBudgetSaving: false,
  amigosReject: null, // 修正依頼ダイアログの対象 { home, missionId }
  // kiro-loop 端末
  kiroLoopTerm: null, // { repo, name, target, session, items, text, error, at }
  kiroLoopCache: RoutineUiCache.createBoundedAsyncCache({ max: 8, ttlMs: 30000 }), // repo → session list
  kiroLoopStateCache: RoutineUiCache.createBoundedAsyncCache({ max: 8, ttlMs: 10000 }), // repo → status summary
  kiroLoopTimer: null,
  coworkRun: null,       // { id, phase: 'running'|'ok'|'error', message, at }
  coworkHistory: null,   // 履歴ダイアログのモデル { id, name, logs, history, file, text }
  timer: null,
  busy: false,
  globalSettingsSection: 'app', // app / agents / sync / routine / integrations
  globalSettingsDirty: false,
  orchInstructionsDirty: false, // 共通指示・推奨スキルの未保存入力（ポーリング再描画から保護）
  orchSessionDirty: false,      // セッション開始コマンドの未保存入力（同上）
  orchSessionPreview: null,     // { engine, entries } プレビューの取得結果
  // 要対応（needs）の前回カウント。増分を検知して OS 通知する（張り付き監視の解消）。
  // initialized=false の初回はベースライン取得のみで通知しない（起動時の殺到を避ける）。
  notify: { counts: {}, initialized: false },
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

// ダイアログの本文だけをスクロール領域にまとめる。既存のフォーム構造と
// イベント配線を保ったまま、すべての画面で見出しと操作ボタンを常に表示する。
function setupDialogLayouts() {
  for (const dialog of document.querySelectorAll('dialog')) {
    const shell = dialog.querySelector(':scope > form') || dialog;
    if (shell.querySelector(':scope > .dialog-scroll-body')) continue;

    shell.classList.add('dialog-shell');
    const children = [...shell.children];
    const heading = children.find((el) => el.classList.contains('dialog-heading') || el.matches('h2'));
    const actions = children.find((el) => el.classList.contains('dialog-actions'));
    if (heading && heading.matches('h2')) heading.classList.add('dialog-heading', 'dialog-heading-simple');

    const body = document.createElement('div');
    body.className = 'dialog-scroll-body';
    const content = children.filter((el) => el !== heading && el !== actions);
    if (content.length) {
      content[0].before(body);
      for (const el of content) body.appendChild(el);
    } else if (actions) {
      actions.before(body);
    } else {
      shell.appendChild(body);
    }
  }
}

// 内部動作の詳細（配送経路・ファイルパス・判定根拠など）はユーザー向け UI に出さず、
// 開発者コンソールへ記録する。UI に見せる文言はプロジェクト管理の言葉に揃える。
function uiLog(...args) {
  console.info('[kpv]', ...args);
}

// 同じ内部詳細をポーリングのたびに記録しない（変化したときだけ uiLog する）
const _loggedOnce = new Map();
function uiLogOnChange(key, detail) {
  const s = JSON.stringify(detail);
  if (_loggedOnce.get(key) === s) return;
  _loggedOnce.set(key, s);
  uiLog(key, detail);
}

// 状態の表示ラベル（UI はプロジェクト管理の言葉、内部の状態名は chip の title で参照できる）。
// タスク（backlog）・プロジェクト（project.json の status / run-log の停止理由）・
// 実行（agent-flow run）・GitLab（issue/MR）の各状態をまとめて引く。
const STATUS_LABELS = {
  // タスクの状態
  inbox: '受付待ち',
  draft: '下書き',
  proposed: '計画承認待ち',
  ready: '実行待ち',
  doing: '実行中',
  offloaded: '実行中',
  review: '検収待ち',
  blocked: '要対応',
  done: '完了',
  rejected: '却下',
  // プロジェクトの状態・自動実行の停止理由
  converged: '完了確認待ち',
  accepted: '承認済み',
  stall: '停滞',
  budget: '回数上限',
  cost: 'コスト上限',
  'no-acceptance': '完了条件が未定義',
  'no-progress': '停滞',
  'awaiting-plan': '分解待ち',
  drained: '消化完了',
  throttle: '予算超過',
  // 実行（run）の状態
  failed: '失敗',
  cancelled: '中止',
  canceled: '中止',   // 語彙統一前に書かれた meta.json の旧綴り
  running: '実行中',
  unknown: '不明',
  idle: '待機中',
  // GitLab issue / MR
  opened: 'オープン',
  merged: 'マージ済み',
  closed: 'クローズ',
};

function statusLabel(status) {
  const s = String(status || '');
  return STATUS_LABELS[s] || s;
}

// タスクを「完了（納品）」にするまでに人が何をすべきか。
// run の done とタスクの archive は別物なので、一覧・詳細・要対応で同じ文言を出す。
// runs は runsForTask 相当（新しい順）。archived=true は履歴タブ／archive 側。
function taskCompletionHint(task, { runs = [], archived = false } = {}) {
  const st = String((task && task.status) || '');
  const extra = (task && task.extra) || {};
  const lastRunId = String(extra.last_run || '').trim();
  const lastRun =
    (lastRunId && runs.find((r) => String(r.runId) === lastRunId)) || runs[0] || null;
  const lastRunDone = !!(lastRun && String(lastRun.status) === 'done');

  if (archived || st === 'done') {
    return {
      unsettledDone: false,
      statusNote: null,
      completeHow: '完了済みです。人の操作は不要です。',
      needAsk: null,
    };
  }
  if (st === 'rejected') {
    return {
      unsettledDone: false,
      statusNote: null,
      completeHow: '却下済みです。やり直すなら内容を編集して再追加してください。',
      needAsk: null,
    };
  }
  if (st === 'review') {
    return {
      unsettledDone: false,
      statusNote: null,
      completeHow: '完了にするには: 要対応で「承認して完了にする」を押す',
      needAsk:
        '成果物を確認し、「承認して完了にする」で納品確定してください。この承認でタスクが完了します。',
    };
  }
  if (st === 'proposed') {
    return {
      unsettledDone: false,
      statusNote: null,
      completeHow:
        'まず要対応で計画を承認してください。実行後に成果を確認します',
      needAsk: 'この承認では完了になりません。実行が許可されるだけです。',
    };
  }
  if (st === 'blocked') {
    const env =
      String(extra.env_resume || '') === '1' ||
      /\[agent-error:/.test(String(extra.needs_reason || ''));
    // verify 未定義の確認待ち: 工程は完了済み。人の承認（approve）が完了の根拠になる
    const verifyPending =
      !env &&
      !String((task && task.verify) || '').trim() &&
      /verify\s*未定義/i.test(String(extra.needs_reason || ''));
    if (verifyPending) {
      return {
        unsettledDone: lastRunDone,
        statusNote: lastRunDone ? '実行済み・未確定' : null,
        completeHow: '完了にするには: 成果を確認して要対応で「承認して完了にする」を押す',
        needAsk: '成果物を確認し、問題なければ「承認して完了にする」で納品確定してください。この承認でタスクが完了します。',
      };
    }
    return {
      unsettledDone: lastRunDone,
      statusNote: lastRunDone ? '実行済み・未確定' : null,
      completeHow: env
        ? '完了にするには: 要対応で環境を直してから「そのまま再実行」または承認'
        : '完了にするには: 要対応で指示を送るか「そのまま再実行」',
      needAsk: lastRunDone
        ? '実行自体は終わっていますが、タスクは未完了です。対応後に再確認・再実行が必要です。'
        : null,
    };
  }
  if (st === 'ready' && lastRunDone) {
    return {
      unsettledDone: true,
      statusNote: '実行済み・未確定',
      completeHow:
        '要対応が残っていれば対応してください。あとは再実行の完了を待ちます',
      needAsk: null,
    };
  }
  if (['ready', 'inbox', 'draft'].includes(st)) {
    return {
      unsettledDone: false,
      statusNote: null,
      completeHow: '実行の完了を待ってください',
      needAsk: null,
    };
  }
  if (['doing', 'offloaded'].includes(st)) {
    return {
      unsettledDone: false,
      statusNote: null,
      completeHow: '実行の完了を待ってください',
      needAsk: null,
    };
  }
  return {
    unsettledDone: false,
    statusNote: null,
    completeHow: '完了にするには: タスクの状態を確認してください',
    needAsk: null,
  };
}

// 関連 run の状態表示。タスク未納品のとき done を「完了」と出さない（タスク完了と誤認させない）。
function runStatusCaption(runStatus, { taskArchived = false } = {}) {
  if (String(runStatus) !== 'done') return statusLabel(runStatus);
  return taskArchived ? '納品済み' : '実行済み・確認待ち';
}

// run の機械状態と agent-project タスクの業務状態を混ぜずに表示するためのモデル。
// agent-flow 単体 run は関連タスクを推測せず、タスクとの関連なしとして扱う。
function runTaskOutcome(project, run) {
  const key = sanitizeTaskId(run && run.taskId);
  const backlogTask = key
    ? ((project && project.backlog) || []).find((task) => sanitizeTaskId(task.id) === key)
    : null;
  const archivedTask = !backlogTask && key
    ? ((project && project.archive) || []).find((task) => sanitizeTaskId(task.id) === key)
    : null;
  const task = backlogTask || archivedTask;
  const taskArchived = Boolean(archivedTask);
  return {
    runLabel: String(run && run.status) === 'done' ? '実行完了' : statusLabel(run && run.status),
    runStatus: String((run && run.status) || ''),
    taskLabel: task ? (taskArchived ? 'タスク完了' : statusLabel(task.status)) : 'タスクとの関連なし',
    taskStatus: task ? String(task.status || '') : '',
    taskArchived,
    taskId: task ? task.id : null,
    note:
      String(run && run.status) === 'done' && task && !taskArchived
        ? '実行は完了しましたが、タスクはまだ完了していません。'
        : '',
  };
}

function runTaskOutcomeHtml(outcome) {
  const taskClass = outcome.taskArchived
    ? 'st-done'
    : outcome.taskStatus
      ? `st-${outcome.taskStatus}`
      : '';
  return `<section class="flow-outcome-status" aria-label="実行とタスクの状態">
    <div><span>実行</span><strong class="status-chip st-${esc(outcome.runStatus || '')}">${esc(outcome.runLabel)}</strong></div>
    <div><span>タスク</span><strong class="status-chip ${esc(taskClass)}">${esc(outcome.taskLabel)}</strong></div>
    ${outcome.note ? `<p>${esc(outcome.note)}</p>` : ''}
  </section>`;
}

function runTaskOutcomeCompactHtml(outcome) {
  const taskClass = outcome.taskArchived
    ? 'st-done'
    : outcome.taskStatus
      ? `st-${outcome.taskStatus}`
      : '';
  return `<span class="flow-outcome-compact">
    <span class="status-chip st-${esc(outcome.runStatus || '')}">${esc(outcome.runLabel)}</span>
    <span class="status-chip ${esc(taskClass)}">タスク: ${esc(outcome.taskLabel)}</span>
  </span>`;
}

// agent-flow の全工程は成功した一方、その後に agent-project が実行するタスクの
// 最終検証だけが失敗した状態を識別する。run.status=done だけでは成果確定と誤認するため、
// 最新の完了 run・blocked の検証証跡がそろった場合に限って表示する。
// 工程集計の有無や要対応／実行タブの違いで表示が割れないよう、共通判定へ委譲する。
function runFinalVerificationFailure(project, run) {
  if (!project || !run || String(run.status) !== 'done') return null;
  const key = sanitizeTaskId(run.taskId);
  const task = ((project.backlog || [])).find((item) => sanitizeTaskId(item.id) === key);
  if (!task || String(task.status) !== 'blocked') return null;
  const lastRun = String((task.extra && task.extra.last_run) || '');
  if (lastRun && lastRun !== String(run.runId || '')) return null;
  const need = (project.needs || []).find((item) =>
    sanitizeTaskId(item.taskId || item.id) === key && String(item.kind || 'blocked') === 'blocked'
  );
  if (!need) return null;
  const prose = [need.failureSummary, need.why, need.detail].filter(Boolean).join('\n');
  // verify 未定義は「失敗」ではない: 工程は完了し、完了条件が無いため人の確認を待っている
  // だけ。赤いエラー風バナーで出すと失敗と誤読される（実際に分かりにくいという指摘を受けた）。
  if (/verify\s*未定義/i.test(prose)) {
    return {
      kind: 'info',
      title: '工程は全て成功・完了の確認待ち',
      summary: '完了を自動確認できません。'
        + '成果を確認し、問題なければ要対応の「承認して完了にする」で完了できます。',
      taskId: task.id,
    };
  }
  return needFinalVerificationFailure(project, need, [run]);
}

function finalVerificationFailureHtml(failure, compact = false) {
  if (!failure) return '';
  const info = failure.kind === 'info';
  const body = compact
    ? (info
      ? '成果は揃っています。内容を確認して承認すると完了になります。'
      : '工程の成果は残っていますが、タスクは未完了です。')
    : (info
      ? esc(failure.summary)
      : `全工程の処理は成功しましたが、その後の最終検証で失敗しました。タスクは未完了です。 ${esc(failure.summary)}`);
  return `<div class="final-verification-failure ${info ? 'info' : ''} ${compact ? 'compact' : ''}" role="status">
    <strong>${esc(failure.title)}</strong>
    <span>${body}</span>
  </div>`;
}

// project.json の charter state から acceptance の PASS 履歴（数値列）を取り出す。
function passHistory(st) {
  if (!st || !Array.isArray(st.history)) return [];
  return st.history
    .map((h) =>
      typeof h === 'number' ? h : h && typeof h === 'object' ? Number(h.pass ?? h.passed ?? h.ok ?? NaN) : NaN
    )
    .filter((n) => !isNaN(n));
}

// 「n / m 達成」の n（過去最高 PASS 数）。本体の best が正だが、収束したサイクルで best を
// 更新しないまま保存された state（全 PASS で完了しているのに best: 0）が残っているため、
// PASS 履歴の最大でも補う。完了しているのに「0 / 1 達成」と出るのを防ぐ。
function achieved(st) {
  const hist = passHistory(st);
  return Math.max(Number((st && st.best) || 0), hist.length ? Math.max(...hist) : 0);
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

// 説明文の正規化: 本体が1行に畳むときに使う "⏎" / 実改行 / 空白隣接の "\n" を本物の改行に戻す。
// "\n" の無差別置換はしない（C:\newfolder や path\name を壊すため）。
function normalizeProse(src) {
  return String(src ?? '')
    .replace(/\r\n/g, '\n')
    // 畳み込みで残った "\n" トークン。直後/直前がパス断片に見えるときは触らない。
    .replace(/(?<![A-Za-z0-9_.-])(?<![A-Za-z]:)\\n/g, '\n')
    .replace(/\u21B5|\u23CE|⏎/g, '\n');
}

// インライン Markdown（コード・太字・リンク）。常にエスケープ済み HTML を返す。
function inlineMd(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(https?:\/\/[^\s)&"<>]+)/g, '<a href="#" data-ext="$1">$1</a>');
}

// 最小限の Markdown 描画（見出し・箇条書き・番号付き・コード・リンクをエスケープ済みで）
function mdToHtml(src) {
  const lines = normalizeProse(src).split('\n');
  const out = [];
  let inCode = false;
  let inList = null; // 'ul' | 'ol' | null
  const closeList = () => {
    if (inList) {
      out.push(`</${inList}>`);
      inList = null;
    }
  };
  const openList = (tag) => {
    if (inList !== tag) {
      closeList();
      out.push(`<${tag}>`);
      inList = tag;
    }
  };
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
      out.push(`<h${lv}>${inlineMd(h[2])}</h${lv}>`);
      continue;
    }
    const oli = line.match(/^\s*\d+\.\s+(.*)$/);
    if (oli) {
      openList('ol');
      out.push(`<li>${inlineMd(oli[1].trim())}</li>`);
      continue;
    }
    const li = line.match(/^\s*[-*]\s+(.*)$/);
    if (li) {
      openList('ul');
      out.push(`<li>${inlineMd(li[1].trim())}</li>`);
      continue;
    }
    closeList();
    if (line.trim()) out.push(`<p>${inlineMd(line)}</p>`);
  }
  closeList();
  if (inCode) out.push('</pre>');
  return `<div class="md">${out.join('\n')}</div>`;
}

// 一覧向けの短い説明（先頭の意味のある行だけ。インライン装飾付き）。
function prosePreview(src, max = 100) {
  const text = normalizeProse(src).trim();
  if (!text) return '';
  const first = text.split('\n').map((l) => l.trim()).find(Boolean) || '';
  const clipped = first.length > max ? `${first.slice(0, Math.max(0, max - 1))}…` : first;
  return `<span class="prose-inline">${inlineMd(clipped)}</span>`;
}

// 本文向け。正規化して Markdown として描画する。
function proseHtml(src) {
  const text = normalizeProse(src).trim();
  return text ? mdToHtml(text) : '';
}

// フロー要求文: 先頭行＝題名、残り＝本文（loop-until-done 指示など）。
function splitRequest(request) {
  const lines = normalizeProse(request)
    .split('\n')
    .map((l) => l.trimEnd());
  const nonempty = lines.map((l, i) => ({ l: l.trim(), i })).filter((x) => x.l);
  if (!nonempty.length) return { title: '', body: '' };
  const title = nonempty[0].l;
  const body = lines.slice(nonempty[0].i + 1).join('\n').trim();
  return { title, body };
}

// タスク extra のうち文章として読む項目（⏎ 畳み込み・Markdown が多い）
// 誘導・レビュー記述フィールド（agent-project の TASK_GUIDE_KEYS と同じ。
// 意味論の正典は tools/agent-project/backlog.md.example。値は 1 行・改行は ⏎）
const GUIDE_KEYS = ['why', 'desc', 'scope', 'out_of_scope', 'risks', 'constraints', 'hints', 'demo'];
const GUIDE_LABELS = {
  why: '目的・背景',
  desc: '作業内容の詳細',
  scope: '変更してよい範囲',
  out_of_scope: 'やらないこと',
  risks: 'リスクと対策',
  constraints: 'タスク固有の制約',
  hints: '実装の手がかり',
  demo: '人の確認観点',
};

const PROSE_EXTRA_KEYS = new Set(['feedback', 'needs_reason', 'note', 'accept', ...GUIDE_KEYS]);

// 受入基準。正規形は task_acceptance_criteria（統一 verify）で、旧 acceptance / accept も
// 読み取り側だけ互換で拾う（新規書き込みは正規形のみ）。**複数行フィールド**なので md パーサが
// `\n` で連結した文字列として届く（project.js の extra 連結）。1 行 1 基準へ戻して扱う。
function acceptanceList(task) {
  const ex = (task && task.extra) || {};
  const raw = String(ex.task_acceptance_criteria || '').trim()
    || String(ex.acceptance || '').trim() || String(ex.accept || '').trim();
  return raw
    ? raw.split('\n').map((s) => s.trim()).filter(Boolean)
    : [];
}

// 固定検証コマンド。正規形は verification_commands（複数行フィールド・UI は先頭 1 件を編集）で、
// 旧 verify: も読み取り側だけ互換で拾う（新規書き込みは正規形のみ）。
function fixedVerifyCommand(task) {
  const ex = (task && task.extra) || {};
  const raw = String(ex.verification_commands || '').trim();
  if (raw) return raw.split('\n').map((s) => s.trim()).filter(Boolean)[0] || '';
  return String((task && task.verify) || '').trim();
}

// タスク追加・再投入でフォームに出さずに引き継ぐフィールド（task.schema.json の人が書ける
// フィールドのうち、専用入力欄が無いもの。system 管理の routed_by/cohort* は引き継がない）
const ENQUEUE_PASSTHROUGH_KEYS = [
  'level', 'track', 'verify_template', 'review', 'expect', 'followup', 'refs', 'paths',
  ...GUIDE_KEYS,
];

function statusChip(status) {
  // 表示はプロジェクト管理の言葉、内部の状態名は title（ホバー）で確認できる
  return `<span class="status-chip st-${esc(status)}" title="${esc(status)}">${esc(statusLabel(status))}</span>`;
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

// クリック委譲: data-ext 属性のリンクは既定ブラウザで開く。
// capture で止めて、親の .run-item 選択クリック等へ伝播させない。
document.addEventListener(
  'click',
  (ev) => {
    const a = ev.target.closest('a[data-ext]');
    if (!a) return;
    ev.preventDefault();
    ev.stopPropagation();
    guard('外部リンク', () => api.openExternal(a.dataset.ext));
  },
  true
);

// ---------------------------------------------------------------------------
// 発見・プロジェクト選択
// ---------------------------------------------------------------------------

async function refreshDiscovery() {
  // 稼働状況（engine/status.json）が発見の根拠でもあるので、一覧より先に取る。
  state.engine = await api.engineStatus().catch(() => null);
  state.discovery = await api.discover();
  await refreshBoard();
  renderTree();
  checkNeedsNotifications();
}

// 委譲公示板の観測。参加できるか・出した仕事が誰に拾われたかを、
// 板のファイルだけから読む（dashboard は板へ書かない）。板未設定なら空で通す。
async function refreshBoard() {
  state.boardStatus = (state.engine && state.engine.board) || null;
  if (!state.boardStatus || !state.boardStatus.configured) {
    state.boardViews = [];
    state.boardCommands = {};
    state.boardNodes = [];
    return;
  }
  const list = await api.delegationList({ only: 'board' }).catch(() => null);
  state.boardViews = (list && list.items) || [];
  state.boardCommands = (list && list.commands) || {};
  const nodes = await api.delegationNodes().catch(() => null);
  state.boardNodes = (nodes && nodes.nodes) || [];
}

// 要対応カウントの増分を計算する純関数（副作用なし・テスト対象）。
//   prevCounts: { dir -> count }（前回。初回は空）
//   projects:   discover() が返す projects（各に needsCount・root・name を含む）
// 返り値: { counts, total, notifications:[{name, root, added, total}] }
// exists:false（登録が実在しない）プロジェクトは総数・通知の対象外。
function computeNeedsDelta(prevCounts, projects) {
  const counts = {};
  let total = 0;
  const notifications = [];
  for (const p of projects || []) {
    if (!p || p.exists === false) continue;
    const c = Math.max(0, Math.floor(Number(p.needsCount) || 0));
    counts[p.dir] = c;
    total += c;
    const before = prevCounts ? prevCounts[p.dir] : undefined;
    // 前回観測済みのプロジェクトで数が増えたときだけ通知する（新規発見・減少では通知しない）。
    if (before != null && c > before) {
      notifications.push({
        name: p.charterName || p.name || p.dir,
        root: p.root || p.dir,
        added: c - before,
        total: c,
      });
    }
  }
  return { counts, total, notifications };
}

// 通知は base の app:notify に文言を渡すだけ（フォーカス中の抑制・バッジ・フラッシュは
// main 側が判断する）。preload に notify が無い旧ビルドでも黙って何もしない。
function safeNotify(payload) {
  try {
    if (api && typeof api.notify === 'function') return api.notify(payload);
  } catch (err) {
    uiLog('notify failed', String((err && err.message) || err));
  }
  return null;
}

// discover() の needsCount 増分を検知して OS 通知する（純関数 computeNeedsDelta の外殻）。
function checkNeedsNotifications() {
  const enabled = !(
    state.config &&
    state.config.notifications &&
    state.config.notifications.enabled === false
  );
  const projects = (state.discovery && state.discovery.projects) || [];
  const { counts, total, notifications } = computeNeedsDelta(state.notify.counts, projects);
  const seeded = state.notify.initialized;
  state.notify.counts = counts;
  state.notify.initialized = true;

  if (!enabled) {
    // 通知オフ: バッジを消し、増分は無視する（カウントは追い続けるので再オンで殺到しない）。
    safeNotify({ badgeCount: 0, silent: true });
    return;
  }
  if (!seeded || !notifications.length) {
    // 初回のベースライン取得、または増分なし: バッジ（総数）だけ合わせる。
    safeNotify({ badgeCount: total, silent: true });
    return;
  }
  for (const r of notifications) {
    safeNotify({
      title: `${r.name}: 要対応 ${r.added} 件`,
      body:
        r.total > r.added
          ? `確認が必要な項目が増えました。現在 ${r.total} 件あります。クリックして開いてください。`
          : '新しく人の判断待ちが発生しました。クリックで開きます。',
      target: { root: r.root, name: r.name },
      badgeCount: total,
      flash: true,
    });
  }
}

function renderTree() {
  const navigation = $('tree');
  const tree = $('project-list');
  const prevScroll = navigation.scrollTop; // 再描画（ポーリング）でサイドバーのスクロールを失わない
  // 実体が無いもの（exists:false）は選べないが、**消さずに理由付きで並べる**。
  // 以前はここで捨てていた。すると別ディストロを指しているなどの設定のずれ 1 つでサイドバーが
  // 空になり、画面は「このエンジンにはプロジェクトが登録されていません」と案内する——実際には
  // 登録されており、人は host.yaml を見に行っても間違いを見つけられない（直しようのない表示）。
  // 実行側が宣言したパスを出せば、どこを直せばよいかが画面から分かる。
  const all = state.discovery.projects || [];
  const projects = all.filter((p) => p.exists);
  const unreachable = all.filter((p) => !p.exists);
  if (!all.length) {
    tree.innerHTML =
      `<div class="empty">${esc(engineEmptyMessage())}<br><br><button id="btn-empty-new" class="primary-inline">＋ 新規プロジェクトを作成</button></div>`;
    const nb = $('btn-empty-new');
    if (nb) nb.addEventListener('click', openNewProject);
  } else {
    tree.innerHTML = projects
      .map((p) => {
        const badges = [];
        if (p.needsCount) badges.push(`<span class="badge warn" title="要対応 ${p.needsCount} 件">${p.needsCount}</span>`);
        if (p.backlogCount) badges.push(`<span class="badge" title="タスク ${p.backlogCount} 件">${p.backlogCount}</span>`);
        if (p.hasCharter) badges.push('<span class="badge info" title="プロジェクト憲章あり">C</span>');
        // via='status-sync' はリモート本体を git 同期越しに推定した稼働判定（同期遅延を許容）。
        // ローカル確定（instances）と見分けられるよう dot に補助クラスと ~ 印を付ける
        const live = p.liveness || { via: p.running ? 'instances' : 'none' };
        const remoteGuess = live.via === 'status-sync';
        const dotTitle = p.paused
          ? '一時停止中'
          : p.running
            ? remoteGuess
              ? `別のマシンで稼働中・約${Math.round((live.ageSec || 0) / 60)}分前に確認`
              : '稼働中'
            : remoteGuess
              ? `状態不明・約${Math.round((live.ageSec || 0) / 60)}分前に確認`
              : '停止中';
        // 表示名は charter.md の `# Charter: <name>` を優先する（無ければフォルダ名）。
        // `.agent-project` のような技術的なフォルダ名でも、charter.md を編集するだけで
        // サイドバーに任意の名前を出せる（✎ charter.md から編集）。フォルダ名は行の
        // title 属性（フルパス）で見られるので、括弧併記はしない。
        const displayName = p.charterName || p.name;
        // 繰り返し失敗して切り離されたプロジェクトは、止まっていることが人に見えないと
        // そのまま忘れられる。行そのものに印を出す（詳しい説明はヘッダの状況表示が担う）。
        const held = p.quarantined
          ? '<span class="badge warn" title="繰り返し失敗したため一時的に切り離されています">停止中</span>'
          : p.offHours
            ? '<span class="badge" title="稼働時間外のため休止中です（時間になると自動で再開します）">休止中</span>'
            : '';
        return `<button type="button" class="project-item ${state.selectedDir === p.dir ? 'selected' : ''}" data-dir="${esc(p.dir)}"
          aria-current="${state.selectedDir === p.dir ? 'true' : 'false'}" title="${esc(p.dir)}">
          <span class="dot ${p.running ? 'running' : ''} ${remoteGuess ? 'synced' : ''} ${p.paused ? 'paused' : ''}" title="${esc(dotTitle)}"></span>
          <span class="name">${esc(displayName)}</span>${badges.join('')}${held}
        </button>`;
      })
      .join('') + unreachableRows(unreachable);
  }
  navigation.scrollTop = prevScroll;
  const running = projects.filter((p) => p.running).length;
  $('sidebar-footer').textContent = `稼働中 ${running} ／ 更新 ${new Date().toLocaleTimeString('ja-JP')}`;

  for (const el of tree.querySelectorAll('.project-item[data-dir]')) {
    el.addEventListener('click', () => selectProject(el.dataset.dir));
  }
}

// 実行エンジンは宣言しているが、この PC からはフォルダに届かないプロジェクトの行。
// 選択させない（data-dir を持たせない＝クリック配線の対象外）が、名前と実行側が宣言した
// パスは見せる。届かない理由はほぼ常に「⚙ 設定のディストロ／ベースパスが実際の置き場と
// 食い違っている」なので、次に見る場所まで書く。
function unreachableRows(projects) {
  return (projects || [])
    .map((p) => {
      const why = 'この PC からはこのフォルダに届きません: ' + (p.dir || '(不明)')
        + ' — ⚙ 設定のディストロ（例 Ubuntu）とベースパスが、実行する PC の置き場と'
        + '合っているか確認してください';
      return `<div class="project-item unreachable" title="${esc(why)}">
          <span class="dot"></span>
          <span class="name">${esc(p.charterName || p.name)}</span>
          <span class="badge warn" title="${esc(why)}">届きません</span>
        </div>`;
    })
    .join('');
}

// プロジェクトが 1 件も無いときの案内。原因（エンジンが動いていない / 動いてはいるが
// プロジェクトを持っていない）で次にすることが違うので、言い分けて次の一手だけを示す。
// 「届かない」プロジェクトはこの案内には来ない（上で理由付きの行として並ぶ）ので、
// ここで「登録されていません」と言い切ってよい。
function engineEmptyMessage() {
  const e = state.engine;
  if (!e || !e.exists) {
    return '実行エンジンが動いていません。実行する PC で「agent-project serve」を起動してください。';
  }
  if (!e.running) {
    return '実行エンジンの応答が止まっています。実行する PC で「agent-project serve」が動いているか確認してください。';
  }
  return 'このエンジンにはプロジェクトが登録されていません。実行する PC の agent-project.host.yaml にプロジェクトを追加してください。';
}

async function selectProject(dir) {
  state.selectedDir = dir;
  localStorage.setItem('kpv:selected', dir);
  // 起動先候補はプロジェクトごとに違う（レジストリとノード宣言の交差）。選択のたびに引き直す。
  state.cliChatCwdChoices = [];
  renderTree();
  await Promise.all([reloadProject(), refreshCliChatCwdChoices()]);
}

async function reloadProject() {
  if (!state.selectedDir) return;
  const project = await guard('プロジェクト読込', () => api.readProject(state.selectedDir));
  if (!project) return;
  project.needs = stabilizeMilestoneNeeds(state.project, project);
  // project.dir は run アーカイブ（<dir>/flow-archive/）の置き場として渡す。
  const fr = (await guard('フロー読込', () => api.flowRuns(project.dir, project.busDir))) || {};
  // project だけ先に差し替えると、flowRuns が前プロジェクト分または空の短い時間が生じ、
  // 完了runを根拠にする承認ボタンが消える。両方を読み終えてから表示状態へ反映する。
  state.flowRuns = fr.runs || [];
  state.project = project;
  if (state.flowRunId && !state.flowRuns.some((r) => r.runId === state.flowRunId)) {
    state.flowRunId = null;
    state.flowRun = null;
  }
  if (state.flowRunId) {
    state.flowRun = await guard('run 読込', () => api.flowRun(project.dir, project.busDir, state.flowRunId));
  } else if (state.flowRuns.length) {
    const groups = lineageGroups(state.flowRuns);
    const first = groups.find((g) => flowGroupBucket(g) === state.flowFilter) || groups[0];
    if (first) {
      state.flowRunId = first.latest.runId;
      state.flowRun = await guard('run 読込', () => api.flowRun(project.dir, project.busDir, state.flowRunId));
    }
  }
  renderHeader();
  renderAllTabs();
  // 復元/更新された選択中 run も、開いたときと同様に一度だけ自動突き合わせる（律速でポーリング毎回は叩かない）
  if (state.flowRun && state.flowRun.run) maybeAutoReconcile(state.flowRun.run);
}

function renderHeader() {
  const p = state.project;
  if (!p) return;
  const settingsTab = $('tab-btn-project-settings');
  settingsTab.classList.remove('hidden');
  settingsTab.hidden = false;
  const charterName = p.charter && p.charter.name ? p.charter.name : '';
  $('project-name').textContent = charterName && charterName !== p.name
    ? `${charterName} (${p.name})`
    : p.name;
  $('project-name').classList.remove('muted');
  const ps = p.projectState;
  const badges = [];
  if (ps && ps.status) badges.push(statusChip(ps.status));
  if (p.liveness && p.liveness.paused) badges.push('<span class="status-chip st-review">一時停止中</span>');
  $('project-badges').innerHTML = badges.join(' ');
  const lastLog = p.runLog.length ? p.runLog[p.runLog.length - 1] : null;
  const metaBits = [];
  if (lastLog) metaBits.push(`最終更新: ${esc(statusLabel(lastLog.reason))}・${fmtAgo(lastLog.ts)}`);
  // 実行エンジンが自動で保つ「共有の状況」を平易な一文で常時表示する。取り込みも送信も
  // エンジンが行うので、ここに出るのは結果であって操作ではない。ボタンはその自動回復を
  // 待たずに前倒しする「今すぐ同期」だけ。
  const eng = state.engine;
  if (eng) {
    const cls = eng.level === 'error' ? 'sync-error' : eng.level === 'warn' ? 'sync-warn' : 'sync-ok';
    const checkedLabel = eng.exists && eng.ageSec !== null && eng.ageSec !== undefined
      ? `最終確認: ${eng.ageSec} 秒前`
      : '';
    // 停止中は投函しても誰も読まない。押せるのは「動いてはいるが揃っていない」ときだけ。
    const action = eng.running && eng.level !== 'ok'
      ? '<button id="btn-sync-now" class="sync-action">今すぐ同期</button>'
      : '';
    metaBits.push(
      `<span class="sync-status ${cls}" title="${esc(eng.summary)}">` +
        `<span class="status-dot" aria-hidden="true"></span> ${esc(eng.summary)} ` +
        `${checkedLabel ? `<small class="sync-checked">${esc(checkedLabel)}</small>` : ''} ${action}</span>`
    );
  }
  $('project-meta').innerHTML = metaBits.join(' ｜ ');
  const syncButton = $('btn-sync-now');
  if (syncButton) syncButton.addEventListener('click', requestHealNow);
  const needsBadge = $('needs-badge');
  const undecided = p.needs.filter((n) => !n.decided).length;
  needsBadge.textContent = undecided;
  needsBadge.classList.toggle('hidden', !undecided);
  needsBadge.classList.toggle('warn', undecided > 0);
}

// ---------------------------------------------------------------------------
// タブ制御・設定・ポーリング
// ---------------------------------------------------------------------------

// 再描画（ポーリング・操作後のリロード）は各タブの innerHTML を作り直すため、素のままでは
// スクロール位置と <details> の開閉が毎回初期化されてしまう。明示キーがない details も
// 所属画面・見出し・同名内の位置から安定キーを作り、機能追加のたびに保存指定を足さなくてよいようにする。
function detailsUiKey(d) {
  if (d.dataset.uiKey) return `key:${d.dataset.uiKey}`;
  if (d.id) return `id:${d.id}`;
  const root = d.closest('.tabpane, dialog');
  const summary = d.querySelector(':scope > summary');
  if (!root || !root.id || !summary) return '';
  const signature = `${d.className}|${summary.textContent.trim()}`;
  const same = [...root.querySelectorAll('details')].filter((item) => {
    const itemSummary = item.querySelector(':scope > summary');
    return item.className === d.className && itemSummary && itemSummary.textContent.trim() === summary.textContent.trim();
  });
  return `auto:${root.id}:${signature}:${same.indexOf(d)}`;
}

function captureUiState() {
  const scroll = {};
  for (const el of document.querySelectorAll('.tabpane, [data-ui-scroll-key], #tree, #flow-runs, #flow-view-body, #graph-box')) {
    if (el.id) scroll[el.id] = { top: el.scrollTop, left: el.scrollLeft };
  }
  const open = [];
  const details = {};
  for (const d of document.querySelectorAll('details')) {
    const key = detailsUiKey(d);
    if (!key) continue;
    details[key] = d.open;
    if (d.open) open.push(key);
  }
  return { scroll, open: new Set(open), details };
}

function restoreUiState(ui) {
  if (!ui) return;
  // details を先に開いてスクロール範囲を確定する。閉じたまま scrollTop を代入すると、
  // ブラウザが短いレイアウトの最大値へ丸め、その後 details を開いても元の位置へ戻らない。
  for (const d of document.querySelectorAll('details')) {
    const key = detailsUiKey(d);
    if (!key) continue;
    if (ui.details && Object.prototype.hasOwnProperty.call(ui.details, key)) {
      d.open = ui.details[key];
    } else if (ui.open.has(key)) {
      d.open = true;
    }
  }
  for (const [id, pos] of Object.entries(ui.scroll)) {
    const el = document.getElementById(id);
    if (el) {
      el.scrollTop = pos.top;
      el.scrollLeft = pos.left;
    }
  }
}

// フィーチャータブの登録簿。外部の feature モジュール（src/renderer/features/*.js）が
// registerFeatureTab(name, { render, refresh }) で自分のタブを差し込む。renderer.js の
// コアを触らずタブを増やせる（開放閉鎖）。render はタブ描画、refresh は非同期データ取得。
const featureTabs = new Map();
function registerFeatureTab(name, hooks) {
  featureTabs.set(String(name), hooks || {});
}
globalThis.registerFeatureTab = registerFeatureTab;

// 登録済みフィーチャータブのうち name に対応するものを描画する（未登録なら何もしない）。
function renderFeatureTab(name) {
  const h = featureTabs.get(String(name));
  if (h && typeof h.render === 'function') h.render();
}

function renderCliChatButton() {
  const button = $('btn-cli-chat');
  if (!button) return;
  button.disabled = !selectedProjectFolder() || button.dataset.busy === 'true';
  renderCliChatCwdOptions();
}

// 起動先（cwd）の候補。既定はプロジェクト（状態リポジトリ）で、**選択中プロジェクトの
// repos レジストリにある**成果物リポジトリも選べる（他プロジェクト用のクローンは並べない）。
// S1 以降プロジェクトのフォルダは状態リポジトリの clone なので、コードを触りたくて CLI を
// 開いてもそこには 1 行もコードが無い。
//
// 宣言が無い／実体が見つからないリポジトリは **消さずに非活性で並べる**——一覧から消えると
// 「なぜ選べないのか」が分からず、host.yaml への宣言し忘れに気付けない。
function renderCliChatCwdOptions() {
  const sel = $('cli-chat-cwd');
  if (!sel) return;
  const dir = selectedProjectFolder();
  const choices = state.cliChatCwdChoices || [];
  sel.disabled = !dir || choices.length <= 1;
  const keep = sel.value;
  sel.innerHTML = '';
  for (const c of choices) {
    const opt = document.createElement('option');
    opt.value = c.path || '';
    opt.textContent = c.enabled ? c.label : `${c.label}（選べません）`;
    opt.disabled = !c.enabled;
    if (c.reason) opt.title = c.reason;
    sel.appendChild(opt);
  }
  if (keep && choices.some((c) => c.enabled && c.path === keep)) sel.value = keep;
}

async function refreshCliChatCwdChoices() {
  const dir = selectedProjectFolder();
  if (!dir) {
    state.cliChatCwdChoices = [];
    return renderCliChatCwdOptions();
  }
  try {
    state.cliChatCwdChoices = (await api.agentChatCwdChoices(dir)) || [];
  } catch {
    state.cliChatCwdChoices = [];   // 候補が出せなくても既定（プロジェクト）で開ける
  }
  renderCliChatCwdOptions();
}

async function openCliChat() {
  const dir = selectedProjectFolder();
  if (!dir) return toast('CLIチャットを開くプロジェクトを選択してください');
  const button = $('btn-cli-chat');
  const sel = $('cli-chat-cwd');
  const cwd = sel && sel.value && sel.value !== dir ? sel.value : '';
  button.dataset.busy = 'true';
  button.setAttribute('aria-busy', 'true');
  renderCliChatButton();
  try {
    const result = await guard('CLIチャットを開けません', () => api.agentOpenChat({ dir, cwd }));
    if (result) {
      const where = result.cwd && result.cwd !== dir ? `（${result.cwd}）` : '';
      toast(`${result.cli}${result.model ? ` / ${result.model}` : ''} のCLIチャットを開きました${where}`, true);
    }
  } finally {
    delete button.dataset.busy;
    button.removeAttribute('aria-busy');
    renderCliChatButton();
  }
}

function renderAllTabs() {
  const ui = captureUiState();
  renderCliChatButton();
  renderOverview();
  renderBacklog();
  renderNeeds();
  renderFlow();
  renderGitLab();
  renderHistory();
  renderCowork();
  renderAmigos();
  renderProjectSettings();
  if ((!state.globalSettingsDirty && !state.orchInstructionsDirty && !state.orchSessionDirty) || !$('tab-orchestration').childElementCount) renderOrchestration();
  renderKiroLoopTerminal();
  for (const [name] of featureTabs) renderFeatureTab(name);
  restoreUiState(ui);
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
      if (tab.dataset.tab === 'needs') refreshGitLab(false);
      // 登録済みフィーチャータブは切り替え時に即描画（初回表示を空にしない）
      if (featureTabs.has(tab.dataset.tab)) renderFeatureTab(tab.dataset.tab);
    });
  }
}

function populateSettingsFields() {
  const cfg = state.config;
  $('cfg-refresh').value = cfg.projects ? cfg.projects.refreshSec : 5;
  $('cfg-engine-distro').value = (cfg.engine && cfg.engine.distro) || '';
  $('cfg-engine-home').value = (cfg.engine && cfg.engine.home) || '';
  if ($('cfg-node-commands-dir')) {
    $('cfg-node-commands-dir').value = (cfg.delegation && cfg.delegation.nodeCommandsDir) || '';
  }
  $('cfg-notify').checked = !(cfg.notifications && cfg.notifications.enabled === false);
  $('cfg-needs-sla').value = cfg.projects && cfg.projects.needsSlaHours !== undefined ? cfg.projects.needsSlaHours : 24;
  if ($('cfg-role')) $('cfg-role').value = cfg.role === 'viewer' ? 'viewer' : 'engineer';
  $('cfg-flow-bus').value = (cfg.projects && cfg.projects.flowBus) || '';
  $('cfg-flow-bus-by-project').value = Object.entries(
    (cfg.projects && cfg.projects.flowBusByProject) || {}
  )
    .map(([name, bus]) => `${name} = ${bus}`)
    .join('\n');
  // 空欄 = 未設定（プロジェクト設定 → 既定 kiro のフォールバック）。'kiro' で埋めると
  // 保存時に「明示 kiro」が固定され、プロジェクトの agent_cli が二度と効かなくなる。
  $('cfg-agent-cli').value = (cfg.agent && cfg.agent.cli) || '';
  $('cfg-agent-model').value = (cfg.agent && cfg.agent.model) || '';
  $('cfg-agent-timeout').value = (cfg.agent && cfg.agent.timeoutSec) || 180;
  $('cfg-gl-url').value = cfg.gitlab.baseUrl || '';
  $('cfg-gl-token').value = cfg.gitlab.token || '';
  $('cfg-rv-mode').value = cfg.reviewViewer.mode || 'protocol';
  $('cfg-rv-exepath').value = cfg.reviewViewer.exePath || '';
  $('cfg-rv-command').value = cfg.reviewViewer.command || '';
  const cw = cfg.cowork || {};
  $('cfg-cowork-loop-provider').value = cw.loopProvider || 'kiro-loop';
  $('cfg-cowork-loop-command').value = cw.loopCommand || 'kiro-loop';
  $('cfg-cowork-sm-command').value = cw.stateMachineCommand || 'statemachine-use';
}

function openGlobalSettings(section = 'app') {
  state.globalSettingsSection = GLOBAL_SETTINGS_SECTIONS.some((item) => item.id === section) ? section : 'app';
  state.globalSettingsDirty = false;
  state.orchInstructionsDirty = false;
  state.orchSessionDirty = false;
  switchTab('orchestration');
  renderOrchestration();
}

function strategyDisplayLabel(strategy) {
  if (strategy == null || strategy === '') return '未設定';
  if (typeof strategy !== 'object') return String(strategy);
  if (Array.isArray(strategy)) return strategy.map(String).join(' + ') || '未設定';
  const parts = [];
  const patterns = Array.isArray(strategy.patterns) ? strategy.patterns.filter(Boolean).map(String) : [];
  if (patterns.length) parts.push(patterns.join(' + '));
  if (strategy.parallelism != null && strategy.parallelism !== '') parts.push(`並列 ${strategy.parallelism}`);
  if (strategy.review === true) parts.push('レビューあり');
  if (strategy.review === false) parts.push('レビューなし');
  if (parts.length) return parts.join(' / ');
  try {
    return JSON.stringify(strategy);
  } catch {
    return '形式不明';
  }
}

// 一貫性ゲート（codd-gate 連携）の結線状態。結線判定は main 側 consistencyGateStatus() が
// 設定 yaml の regression_cmd / intake_cmd を読んで済ませてあるので、ここは表示だけを行う。
// UI から設定を書き換えない（done 不変条件）。有効化は「設定ファイルを開く」か
// 「人が打つコマンドを見せる」に留める（startAgentProject と同じ考え方）。
function consistencyGateHtml(p) {
  const gate = p && p.consistencyGate;
  if (!gate) return '';
  const regressionConfigured = gate.regressionConfigured ?? Boolean(gate.regressionCmd && String(gate.regressionCmd).trim());
  const intakeConfigured = gate.intakeConfigured ?? Boolean(gate.intakeCmd && String(gate.intakeCmd).trim());
  // 未結線でも値が入っていることはある（regression_cmd は codd-gate 専用キーではなく汎用フック）。
  // そのときバッジだけ出して値を隠すと「未結線＝何も設定されていない」と読めてしまうので、
  // 設定されているコマンドは必ず見せたうえで「一貫性ゲートの検査ではない」と添える。
  const rows = [
    ['regression_cmd', '完了前の回帰検査', regressionConfigured, gate.regressionWired, gate.regressionCmd,
      'このコマンドが失敗すると done の確定を止めます。原因は要対応の失敗要約を確認してください。'],
    ['intake_cmd', 'ドリフトの取り込み', intakeConfigured, gate.intakeWired, gate.intakeCmd,
      'このコマンドが正常に実行された場合、検出したズレを修復タスクとしてバックログへ積みます。'],
  ].map(([key, label, configuredFlag, wired, cmd, note]) => {
    return `<div>
      <dt>${esc(label)}<br><span class="mono">${key}</span></dt>
      <dd>
        <span class="badge ${wired ? 'info' : 'warn'}">${wired ? '結線済み' : '未結線'}</span>
        <span class="muted">設定: ${configuredFlag ? 'あり' : 'なし'}</span>
        ${cmd ? `<code>${esc(cmd)}</code>` : ''}
        <span class="muted">${!wired && configuredFlag
          ? '別のコマンドが設定されています。一貫性ゲートの検査ではありません。'
          : esc(note)}</span>
      </dd>
    </div>`;
  }).join('');

  // 未結線のフックが 1 つでもあれば有効化導線を出す。書く行・CLI・注意書きは
  // tools/agent-project/README.md「一貫性ゲート（codd-gate 連携・オプション）」の原文に合わせる
  // （画面と README で手順が食い違うと、どちらが正か人が判断できなくなる）。
  //   - 有効化は設定ファイルへ未結線の行を書く。`<root>/repos.json` は README 同様プレースホルダのまま出す
  //     （このプロジェクトの root は結線判定に使っていないので、ここで勝手に埋めない）。
  //   - regression_cmd の行だけは sibling CLI codd_gate_regression.py で冪等 upsert できる。
  //     intake_cmd に対応する注入 CLI は無いので yaml を直接編集する。
  //   - CLI の --config は**既存の**設定ファイルを指すこと（無ければエラーで止まる）。
  //     よって設定ファイル未検出のときは CLI を勧めず、作成手順だけを出す。
  //   - viewer の p.dir/configFile は Windows では WSL UNC になることがあるため、実行環境の
  //     シェルへそのまま貼れるとは限らない。README と同じプレースホルダを示す。
  const wiredAll = gate.wired;
  const jsonConfig = gate.configFile && /\.json$/i.test(gate.configFile);
  const reposArg = '<root>/repos.json';
  const settings = [
    !gate.regressionWired && ['regression_cmd', `codd-gate verify --base "$KIRO_BASE_REV" --repos ${reposArg}`],
    !gate.intakeWired && ['intake_cmd', `codd-gate tasks --debt --repos ${reposArg}`],
  ].filter(Boolean);
  const lines = jsonConfig
    ? JSON.stringify(Object.fromEntries(settings), null, 2)
    : settings.map(([key, value]) => `${key}: '${value}'`).join('\n');
  // CLI は install.sh 導入版だと単体ファイルとして置かれない（install.sh:50-52 が zipapp ルートへ
  // 同梱するだけ）。どこで打てば動くかを書かないと、コピーして必ず No such file になる。
  const cliHint = gate.configFile && !gate.configError && !jsonConfig && !regressionConfigured
    ? `<p><code>regression_cmd</code> の行は手書きの代わりに CLI で入れてもよい（リポジトリルートで実行する）:
        <code>python3 tools/agent-project/codd_gate_regression.py --config &lt;状態 clone&gt;/agent-project.yaml</code>
        （codd-gate を実測してこの 1 キーだけを冪等 upsert する。<code>--dry-run</code> なら書かずに結果だけ出す。
        codd-gate が未検出・バージョン/schema 非互換なら何も書かない）。</p>`
    : '';
  const intakeHint = !jsonConfig && !intakeConfigured
    ? `<p><code>intake_cmd</code> に対応する注入 CLI は無いので、こちらは yaml を直接編集する。</p>`
    : '';
  const replacementWarning = settings.some(([key]) =>
    key === 'regression_cmd' ? regressionConfigured : intakeConfigured)
    ? `<p><strong>注意:</strong> 設定済みのキーをこの例へ置換すると、現在の処理は失われます。
        既存処理も必要なら、置換せず両方を実行するコマンドへまとめてください。</p>`
    : '';
  const enable = settings.length === 0
    ? ''
    : `<div class="need-resolution">
        <span class="label-chip">有効化</span>
        ${gate.configError
          ? `設定ファイル <span class="mono">${esc(gate.configFile)}</span> は JSON として読めません。修復してから次の設定を追加してください:`
          : jsonConfig
          ? `自動検出した設定候補 <span class="mono">${esc(gate.configFile)}</span> の既存トップレベル object で、次のプロパティを追加または置換する（ファイル全体は置き換えない）:`
          : gate.configFile
          ? `設定ファイル <span class="mono">${esc(gate.configFile)}</span> で次の行を追加または置換する:`
          : `agent-project の設定ファイルが見つかりません。ワークスペース直下に
             <span class="mono">.agents/agent-project.yaml</span> を作り、次の行を書く:`}
        <pre class="mono">${esc(lines)}</pre>
        <p><code>&lt;root&gt;</code> は対象プロジェクトのルートパスへ置き換えてください。</p>
        ${replacementWarning}
        ${cliHint}${intakeHint}
        ${gate.configFile
          ? `<div class="summary-actions">
              <button class="summary-link secondary" data-gate-open="${esc(gate.configFile)}">自動検出した設定ファイルを開く</button>
            </div>`
          : ''}
      </div>`;

  // 見出しバッジは 3 値。2 値だと「一度も有効化していない既定状態」まで『一部のみ』＝
  // 部分的に動作中と読めてしまい、このセクションが最も助けたい相手を取り違える。
  const wiredNone = !gate.regressionWired && !gate.intakeWired;
  const headLabel = wiredAll ? '結線済み' : wiredNone ? '未結線' : '一部結線';
  const headNote = wiredAll
    ? '設定上は両方のフックへ結線済みです。codd-gate の実行可否や成功はこの画面では確認していません。'
    : wiredNone
      ? 'codd-gate は両方のフックに未結線です。これらのフックからは実行されません。'
      : 'codd-gate は一方のフックだけに結線済みです。実行可否や成功はこの画面では確認していません。';
  const configScopeNote = gate.explicitConfigUnknown
    ? '<p class="muted">この状態は dashboard が自動探索した設定候補です。agent-project が <code>--config</code> で別の設定を使っているかは確認できません。</p>'
    : '';
  return `<section class="overview-version-section" aria-labelledby="consistency-gate-title">
    <div class="overview-version-heading">
      <div>
        <h2 id="consistency-gate-title">一貫性ゲート</h2>
        <p>${headNote}</p>
      </div>
      <div class="summary-actions"><span class="badge ${wiredAll ? 'info' : 'warn'}">${headLabel}</span></div>
    </div>
    ${configScopeNote}
    <dl class="need-failure-context">${rows}</dl>
    ${enable}
  </section>`;
}

// 有効化導線のボタン。設定ファイルを OS の既定エディタで開くだけ（読み書きは行わない）。
function bindConsistencyGate(root) {
  for (const btn of root.querySelectorAll('button[data-gate-open]')) {
    btn.addEventListener('click', () => guard('設定ファイルを開く', () => api.openPath(btn.dataset.gateOpen)));
  }
}

function technicalProjectInfoHtml() {
  const p = state.project;
  if (!p) {
    return '<div class="empty compact">プロジェクトを選ぶと、実行状態とログをここで確認できます。</div>';
  }
  const run = state.flowRun && state.flowRun.run;
  const journal = (p.journal || []).slice(-80).reverse().map((line) => `<div>${linkify(line.replace(/^-\s*/, ''))}</div>`).join('');
  const runRows = [...(p.runLog || [])].reverse().slice(0, 40).map((entry) => `<tr>
    <td>${fmtTime(entry.ts)}</td><td>${esc(statusLabel(entry.reason))}</td><td>${esc(entry.level || '')}</td>
    <td>${entry.cycles ?? ''}</td><td>${entry.tokens ?? ''}</td><td>${entry.cost ?? ''}</td>
  </tr>`).join('');
  const selectedNode = run && state.flowNodeId ? (run.nodes || {})[state.flowNodeId] : null;
  const nodeInfo = selectedNode
    ? `<details data-ui-key="developer-node" open><summary>選択中の工程: ${esc(selectedNode.id)}</summary>
        ${selectedNode.output ? `<h4>output</h4><pre class="mono developer-output">${esc(selectedNode.output)}</pre>` : '<p class="muted">出力はありません。</p>'}
        ${selectedNode.data ? `<h4>data</h4><pre class="mono developer-output">${esc(JSON.stringify(selectedNode.data, null, 2))}</pre>` : ''}
      </details>`
    : '';
  const runInfo = run
    ? `<dl class="developer-facts">
        <div><dt>run ID</dt><dd class="mono">${esc(run.runId)}</dd></div>
        <div><dt>内部状態</dt><dd>${esc(run.status || 'unknown')}</dd></div>
        <div><dt>戦略</dt><dd>${esc(strategyDisplayLabel(run.strategy || run.meta?.strategy))}</dd></div>
        <div><dt>最終応答</dt><dd>${run.heartbeatAt ? esc(fmtAgo(run.heartbeatAt)) : '記録なし'}</dd></div>
      </dl>${nodeInfo}`
    : '<p class="muted">実行タブで作業を選ぶと、その内部情報を表示します。</p>';
  return `<section class="developer-summary">
      <div class="settings-section-heading"><div><span class="summary-kicker">選択中</span><h3>${esc(p.name)}</h3></div>
        <div class="row"><button type="button" data-technical-tab="flow">実行を開く</button><button type="button" data-technical-tab="history">成果を開く</button></div>
      </div>
      <dl class="developer-facts">
        <div><dt>ワークスペース</dt><dd class="mono">${esc(p.workspace || p.dir || '')}</dd></div>
        <div><dt>状態ディレクトリ</dt><dd class="mono">${esc(p.dir || '')}</dd></div>
        <div><dt>実行データ</dt><dd class="mono">${esc(p.busDir || '未検出')}</dd></div>
        <div><dt>検出方法</dt><dd>${esc(p.busSource || '不明')}</dd></div>
        <div><dt>実行エンジン</dt><dd>${daemonBadge()}</dd></div>
      </dl>
      ${runInfo}
      <details data-ui-key="developer-run-log"><summary>自動実行の記録</summary>
        ${runRows ? `<div class="table-scroll"><table class="list"><tr><th>時刻</th><th>結果</th><th>レベル</th><th>回数</th><th>トークン</th><th>コスト</th></tr>${runRows}</table></div>` : '<p class="muted">記録はありません。</p>'}
      </details>
      <details data-ui-key="developer-journal"><summary>内部ログ</summary><div class="events developer-log">${journal || '<span class="muted">ログはありません。</span>'}</div></details>
    </section>`;
}

function openTechnicalInfo() {
  $('technical-info-kicker').textContent = '選択中';
  $('technical-info-title').textContent = '詳細情報';
  $('technical-project-info').innerHTML = technicalProjectInfoHtml();
  for (const btn of $('technical-project-info').querySelectorAll('[data-technical-tab]')) {
    btn.addEventListener('click', () => {
      $('dlg-technical-info').close();
      switchTab(btn.dataset.technicalTab);
    });
  }
  $('dlg-technical-info').showModal();
}

async function saveGlobalSettingsSection(section) {
  const cfg = state.config;
  if (section === 'app') {
    cfg.projects = cfg.projects || {};
    cfg.projects.refreshSec = Math.max(0, parseInt($('cfg-refresh').value, 10) || 0);
    cfg.notifications = cfg.notifications || {};
    cfg.notifications.enabled = $('cfg-notify').checked;
    cfg.projects.needsSlaHours = Math.max(1, parseInt($('cfg-needs-sla').value, 10) || 24);
    if ($('cfg-role')) cfg.role = $('cfg-role').value === 'viewer' ? 'viewer' : 'engineer';
  } else if (section === 'agents') {
    cfg.agent = cfg.agent || {};
    cfg.agent.cli = $('cfg-agent-cli').value.trim();
    cfg.agent.model = $('cfg-agent-model').value.trim();
    cfg.agent.timeoutSec = Math.max(30, parseInt($('cfg-agent-timeout').value, 10) || 180);
  } else if (section === 'sync') {
    cfg.projects = cfg.projects || {};
    cfg.engine = cfg.engine || {};
    cfg.engine.distro = $('cfg-engine-distro').value.trim();
    cfg.engine.home = $('cfg-engine-home').value.trim();
    if ($('cfg-node-commands-dir')) {
      cfg.delegation = cfg.delegation || {};
      cfg.delegation.nodeCommandsDir = $('cfg-node-commands-dir').value.trim();
    }
    cfg.projects.flowBus = $('cfg-flow-bus').value.trim();
    cfg.projects.flowBusByProject = $('cfg-flow-bus-by-project').value.split('\n').map((line) => {
      const i = line.indexOf('=');
      if (i < 0) return null;
      const name = line.slice(0, i).trim();
      const bus = line.slice(i + 1).trim();
      return name && bus ? [name, bus] : null;
    }).filter(Boolean).reduce((acc, [name, bus]) => ((acc[name] = bus), acc), {});
  } else if (section === 'routine') {
    cfg.cowork = cfg.cowork || {};
    cfg.cowork.loopProvider = $('cfg-cowork-loop-provider').value.trim() || 'kiro-loop';
    cfg.cowork.loopCommand = $('cfg-cowork-loop-command').value.trim() || cfg.cowork.loopProvider;
    cfg.cowork.stateMachineCommand = $('cfg-cowork-sm-command').value.trim() || 'statemachine-use';
  } else if (section === 'integrations') {
    cfg.gitlab = cfg.gitlab || {};
    cfg.reviewViewer = cfg.reviewViewer || {};
    cfg.gitlab.baseUrl = $('cfg-gl-url').value.trim();
    cfg.gitlab.token = $('cfg-gl-token').value.trim();
    cfg.reviewViewer.mode = $('cfg-rv-mode').value;
    cfg.reviewViewer.exePath = $('cfg-rv-exepath').value.trim();
    cfg.reviewViewer.command = $('cfg-rv-command').value.trim();
  } else {
    throw new Error('保存する設定の種類が不明です');
  }
  state.config = await api.saveConfig(cfg);
  state.globalSettingsDirty = false;
  setupPolling();
  await refreshAll();
  const label = (GLOBAL_SETTINGS_SECTIONS.find((item) => item.id === section) || {}).label || '設定';
  toast(`${label}の設定を保存しました`, true);
}

// ---------------------------------------------------------------------------
// 同期の状況表示と「今すぐ同期」
// ---------------------------------------------------------------------------
// この画面は状態を共有するリポジトリへ **書かない**。取り込み（pull）も送信（push）も
// 修復も、常駐体（agent-project serve）が自動で行う。ここに残るのは
//   ・その結果を見せること（engine/status.json の sync_health）
//   ・自動回復を待たずに前倒しする「今すぐ同期」を commands/heal として投函すること
// の 2 つだけ。git を直接叩く経路を戻さないこと（過去に、本体の書き込みと衝突して
// 状態ファイルへコンフリクトマーカーが書き込まれ、プロジェクトが状態を失った）。

function gitStateDir() {
  return (state.project && state.project.dir) || state.selectedDir;
}

// 「今すぐ同期」。投函するだけで、実際の取り込み・送信は常駐体が行う。
async function requestHealNow() {
  const dir = gitStateDir();
  if (!dir) return toast('プロジェクトを選択してください');
  const res = await guard('今すぐ同期', () => api.requestHeal(dir));
  if (!res) return;
  uiLog('heal', res);
  toast('同期を始めました。反映までお待ちください', true);
  await refreshAll();
}

function activeTabName() {
  const tab = document.querySelector('.tab.active');
  return tab ? tab.dataset.tab : 'overview';
}

function appDoctorSummary(discovery) {
  const projects = ((discovery && discovery.projects) || []).filter((p) => p.exists !== false);
  return {
    projects: projects.length,
    running: projects.filter((p) => p.running).length,
    needs: projects.reduce((sum, p) => sum + (Number(p.needsCount) || 0), 0),
  };
}

async function buildDoctorContext() {
  const p = state.project;
  if (!p) {
    return {
      capturedAt: new Date().toISOString(),
      tab: 'app',
      scope: 'app',
      app: appDoctorSummary(state.discovery),
      selected: null,
    };
  }
  const focusedModes = new Set(['failure-diagnosis', 'plan-critique', 'delivery-rationale']);
  const focusedNeedId = focusedModes.has(state.doctorMode) ? state.doctorNeedId : null;
  const tab = focusedNeedId ? 'needs' : activeTabName();
  const context = {
    capturedAt: new Date().toISOString(),
    tab,
    scope: focusedNeedId ? state.doctorMode : 'project',
    charter: charterAssistContext(p),
    project: {
      name: p.name,
      status: p.projectState && p.projectState.status,
      liveness: p.liveness,
      taskCounts: p.byStatus,
      completed: (p.archive || []).length,
      needs: (p.needs || []).filter((need) => !need.decided).length,
    },
  };
  if (tab === 'needs') {
    const needId = focusedNeedId || state.needsSelectedId;
    const need = p.needs.find((item) => item.id === needId) || null;
    if (need) {
      const output = await loadNeedFullOutput(need);
      const task = taskForNeed(p, need);
      context.selected = {
        type: 'need',
        id: need.id,
        kind: need.kind,
        title: needDisplayTitle(need),
        why: need.why,
        summary: need.summary,
        failureSummary: need.failureSummary,
        failureResolution: need.failureResolution,
        failureContext: need.failureContext,
        state: need.stateNote,
        risk: need.risk,
        task: task
          ? {
              id: task.id,
              title: task.title,
              status: task.status,
              verify: task.verify,
              accept: task.accept,
              priority: task.priority,
              after: task.after,
              retries: task.retries,
            }
          : null,
        diff: need.diff,
        delivery: need.delivery,
        mrUrls: need.mrUrls,
        fullOutput: output.text,
      };
      if (state.doctorMode === 'plan-critique') {
        context.proposedSiblings = (p.backlog || [])
          .filter((t) => t.status === 'proposed')
          .slice(0, 40)
          .map((t) => ({
            id: t.id,
            title: t.title,
            verify: t.verify,
            accept: t.accept,
            priority: t.priority,
            after: t.after,
          }));
        context.backlog = backlogAssistRows(p);
      }
      if (state.doctorMode === 'delivery-rationale') {
        context.backlog = backlogAssistRows(p);
        const hydrated = need.delivery && need.delivery.length
          ? need.delivery
          : (await hydrateDeliveryEntries(need.delivery || []));
        const withDelivery = { ...need, delivery: hydrated };
        const diffs = await collectDeliveryDiffSections(withDelivery, { maxChars: 50000 });
        context.selected.diffSections = diffs.map((d) => ({
          name: d.name,
          label: d.label,
          files: d.files,
          text: d.text,
        }));
      }
    }
  } else if (tab === 'flow') {
    const run = state.flowRun && state.flowRun.run;
    const node = run && state.flowNodeId ? run.nodes[state.flowNodeId] : null;
    context.selected = run
      ? {
          type: 'run',
          view: state.flowDetailView,
          runId: run.runId,
          request: run.request,
          status: run.status,
          failureReason: run.failureReason,
          counts: run.counts,
          selectedNode: node
            ? { id: node.id, goal: node.goal, state: node.state, output: node.output, error: node.error }
            : null,
        }
      : null;
  } else if (tab === 'backlog') {
    context.selected = {
      type: 'task-list',
      filter: state.backlogFilter,
      tasks: (p.backlog || []).slice(0, 40).map((task) => ({
        id: task.id,
        title: task.title,
        status: task.status,
        retries: task.retries,
      })),
    };
  } else if (tab === 'history') {
    context.selected = {
      type: 'history',
      recentRuns: (p.runLog || []).slice(-10),
      recentDeliveries: (p.delivery || []).slice(-10),
    };
  } else {
    context.selected = { type: 'overview', summary: overviewSummary(p, state.flowRuns) };
  }
  return context;
}

function setDoctorApplyFeedbackVisible(visible) {
  const btn = $('btn-doctor-apply-feedback');
  if (!btn) return;
  btn.classList.toggle('hidden', !visible);
}

function doctorStatusPending(mode) {
  if (mode === 'failure-diagnosis') return '失敗ログを分析し、原因と対処方法を作成しています…';
  if (mode === 'plan-critique') return '計画を charter と突き合わせて批評しています…';
  if (mode === 'delivery-rationale') return '差分と完了条件から変更理由を整理しています…';
  return '現在の画面を読み取り、助言を作成しています…';
}

function doctorStatusFailed(mode) {
  if (mode === 'failure-diagnosis') return '失敗診断を実行できませんでした';
  if (mode === 'plan-critique') return '計画批評を実行できませんでした';
  if (mode === 'delivery-rationale') return '変更理由の説明を実行できませんでした';
  return 'Doctorを実行できませんでした';
}

function doctorTargetLabel(mode, context) {
  if (mode === 'failure-diagnosis') return `タスク ${state.doctorNeedId} の失敗`;
  if (mode === 'plan-critique') return `計画レビュー ${state.doctorNeedId}`;
  if (mode === 'delivery-rationale') return `検収 ${state.doctorNeedId}`;
  return context.scope === 'app' ? 'アプリ全体' : `${context.tab} 画面`;
}

function openDoctor() {
  state.doctorMode = 'consultation';
  state.doctorNeedId = null;
  state.doctorFeedbackDraft = '';
  setDoctorApplyFeedbackVisible(false);
  $('doctor-title').textContent = '現在の画面を相談';
  $('btn-doctor-submit').textContent = '相談する';
  $('doctor-status').textContent = state.project
    ? '現在の画面を読み取って相談できます。'
    : 'プロジェクト未選択のため、アプリ全体の状態について相談します。';
  $('doctor-response').innerHTML = '';
  if (!$('dlg-doctor').open) $('dlg-doctor').showModal();
  $('doctor-prompt').focus();
}

async function openFailureDiagnosis(needId) {
  if (state.doctorBusy) return;
  const need = state.project && state.project.needs.find((item) => item.id === needId);
  if (!need || !canDiagnoseNeed(need)) return toast('診断できる失敗情報が見つかりません');
  state.doctorMode = 'failure-diagnosis';
  state.doctorNeedId = need.id;
  state.doctorFeedbackDraft = '';
  setDoctorApplyFeedbackVisible(false);
  $('doctor-title').textContent = `タスク失敗を診断 — ${needDisplayTitle(need)}`;
  $('btn-doctor-submit').textContent = '追加で質問する';
  $('doctor-prompt').value = '';
  $('doctor-status').textContent = '失敗ログと実行条件を読み込んでいます…';
  $('doctor-response').innerHTML = '';
  if (!$('dlg-doctor').open) $('dlg-doctor').showModal();
  await askDoctor();
  $('doctor-prompt').focus();
}

// 失敗診断を対話セッションで開く。ヘッドレスの openFailureDiagnosis と**同じ文脈**を
// 使う（buildDoctorContext の 1 実装）。違うのは渡し方だけ——ブリーフ 1 行 ＋ 全文ファイルの
// パスを tmux セッションへ送り、以後は人がその窓で会話する。
async function openFailureDiagnosisChat(needId) {
  if (state.doctorBusy) return;
  const need = state.project && state.project.needs.find((item) => item.id === needId);
  if (!need || !canDiagnoseNeed(need)) return toast('診断できる失敗情報が見つかりません');
  const prevMode = state.doctorMode;
  const prevNeed = state.doctorNeedId;
  state.doctorBusy = true;
  state.doctorMode = 'failure-diagnosis';
  state.doctorNeedId = need.id;
  try {
    const context = await buildDoctorContext();
    const res = await api.agentDoctorChat({
      dir: state.project ? state.project.dir : null,
      context,
      needId: need.id,
    });
    // 読み取り専用を「保証できない」CLI では、その事実を人に見せる。
    // 防御は持たない代わりに、判断材料は渡す。
    if (res.readonlyWarning) toast(`${res.readonlyWarning}。開いた窓の操作にご注意ください`);
    toast(res.message || `${res.cli} で対話診断を開きました`, !res.readonlyWarning);
  } catch (err) {
    toast(`対話診断を開けませんでした: ${err.message || err}`);
  } finally {
    state.doctorBusy = false;
    state.doctorMode = prevMode;
    state.doctorNeedId = prevNeed;
  }
}

async function openPlanCritique(needId) {
  if (state.doctorBusy) return;
  const need = state.project && state.project.needs.find((item) => item.id === needId);
  if (!need || need.kind !== 'plan-review') return toast('計画レビュー対象が見つかりません');
  state.doctorMode = 'plan-critique';
  state.doctorNeedId = need.id;
  state.needsSelectedId = need.id;
  state.doctorFeedbackDraft = '';
  setDoctorApplyFeedbackVisible(false);
  $('doctor-title').textContent = `計画を批評 — ${needDisplayTitle(need)}`;
  $('btn-doctor-submit').textContent = '追加で質問する';
  $('doctor-prompt').value = '';
  $('doctor-status').textContent = '計画と charter を読み込んでいます…';
  $('doctor-response').innerHTML = '';
  if (!$('dlg-doctor').open) $('dlg-doctor').showModal();
  await askDoctor();
  $('doctor-prompt').focus();
}

async function openDeliveryRationale(needId) {
  if (state.doctorBusy) return;
  const need = state.project && state.project.needs.find((item) => item.id === needId);
  if (!need || need.kind !== 'review') return toast('検収対象が見つかりません');
  state.doctorMode = 'delivery-rationale';
  state.doctorNeedId = need.id;
  state.needsSelectedId = need.id;
  state.doctorFeedbackDraft = '';
  setDoctorApplyFeedbackVisible(false);
  $('doctor-title').textContent = `変更理由を説明 — ${needDisplayTitle(need)}`;
  $('btn-doctor-submit').textContent = '追加で質問する';
  $('doctor-prompt').value = '';
  $('doctor-status').textContent = '差分と完了条件を読み込んでいます…';
  $('doctor-response').innerHTML = '';
  if (!$('dlg-doctor').open) $('dlg-doctor').showModal();
  await askDoctor();
  $('doctor-prompt').focus();
}

function applyDoctorFeedbackDraft() {
  const draft = String(state.doctorFeedbackDraft || '').trim();
  if (!draft) return toast('差し戻し文面案がありません');
  const needId = state.doctorNeedId || state.needsSelectedId;
  if (!needId) return toast('対象の要対応がありません');
  state.needsDrafts[needId] = draft;
  let filled = false;
  for (const root of [$('tab-needs'), $('dlg-delivery-review')]) {
    if (!root) continue;
    for (const box of root.querySelectorAll('.need-actions')) {
      if (box.dataset.need !== needId) continue;
      const input = box.querySelector('.need-input');
      if (input) {
        input.value = draft;
        filled = true;
      }
    }
  }
  toast(filled ? '差し戻し案を回答欄へ入れました。内容を確認して送信してください' : '下書きを保存しました。回答欄で確認できます', true);
}

async function askDoctor() {
  if (state.doctorBusy) return;
  state.doctorBusy = true;
  $('btn-doctor').disabled = true;
  $('doctor-prompt').disabled = true;
  $('btn-doctor-submit').disabled = true;
  setDoctorApplyFeedbackVisible(false);
  $('doctor-status').textContent = doctorStatusPending(state.doctorMode);
  $('doctor-response').innerHTML = '';
  try {
    const context = await buildDoctorContext();
    const userPrompt = $('doctor-prompt').value;
    const res = await api.agentDoctor({
      dir: state.project ? state.project.dir : null,
      context,
      userPrompt,
      mode: state.doctorMode,
    });
    const model = res.model ? ` / ${res.model}` : '';
    $('doctor-status').textContent = `${res.cli}${model} の助言 — ${doctorTargetLabel(state.doctorMode, context)}を分析`;
    $('doctor-response').innerHTML = mdToHtml(res.content || '助言はありませんでした。');
    state.doctorFeedbackDraft = String(res.feedbackDraft || '').trim();
    setDoctorApplyFeedbackVisible(Boolean(state.doctorFeedbackDraft));
  } catch (err) {
    state.doctorFeedbackDraft = '';
    $('doctor-status').textContent = doctorStatusFailed(state.doctorMode);
    $('doctor-response').innerHTML = `<div class="doctor-error" role="alert">${esc(err.message)}</div>`;
  } finally {
    state.doctorBusy = false;
    $('btn-doctor').disabled = false;
    $('doctor-prompt').disabled = false;
    $('btn-doctor-submit').disabled = false;
  }
}

async function refreshAll() {
  if (state.busy) return;
  state.busy = true;
  try {
    await refreshDiscovery();
    // Cowork は軽量 overview（ログ推定のみ・発見キャッシュ利用）。重いプロセス探査は実行直後/手動更新のみ。
    await refreshCowork();
    await refreshAmigos();
    await refreshOrchestration();
    if (state.selectedDir) await reloadProject();
    if (activeTab() === 'cowork') renderCowork();
    if (activeTab() === 'amigos') renderAmigos();
    if (activeTab() === 'orchestration' && !state.globalSettingsDirty && !state.orchInstructionsDirty && !state.orchSessionDirty) renderOrchestration();
    // 登録済みフィーチャータブ: 非同期取得（refresh）してから表示中なら描画する。
    for (const [name, h] of featureTabs) {
      if (typeof h.refresh === 'function') {
        try {
          await h.refresh();
        } catch {
          /* 取得失敗はモジュール側で表示。ポーリングは止めない */
        }
      }
      if (activeTab() === name) renderFeatureTab(name);
    }
  } finally {
    state.busy = false;
  }
}

function setupPolling() {
  clearInterval(state.timer);
  const sec = state.config && state.config.projects ? Number(state.config.projects.refreshSec) : 5;
  if (sec > 0) {
    state.timer = setInterval(() => {
      // ダイアログを開いている間・入力中は更新しない（書きかけの入力を消さない）
      if (
        $('dlg-technical-info').open ||
        $('dlg-task').open ||
        $('dlg-enqueue').open ||
        $('dlg-replan').open ||
        $('dlg-confirm').open ||
        $('dlg-new-project').open ||
        $('dlg-edit-file').open ||
        $('dlg-new-charter').open ||
        $('dlg-edit-charter').open ||
        $('dlg-edit-policy').open ||
        $('dlg-edit-repos').open
        || $('dlg-need-output').open
        || $('dlg-delivery-review').open
        || $('dlg-doctor').open
        || ($('dlg-cowork-work') && $('dlg-cowork-work').open)
        || ($('dlg-cowork-save') && $('dlg-cowork-save').open)
        || ($('dlg-cowork-history') && $('dlg-cowork-history').open)
        || ($('dlg-amigos-post') && $('dlg-amigos-post').open)
        || ($('dlg-amigos-detail') && $('dlg-amigos-detail').open)
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

// ディープリンク: agent-dashboard://open?root=<プロジェクトルート>（旧 project= も名前一致で受ける）
function handleOpenTarget({ url }) {
  guard('ディープリンク', async () => {
    const u = new URL(url);
    const root = u.searchParams.get('root');
    const name = u.searchParams.get('project');
    await refreshDiscovery();
    const p =
      (root &&
        state.discovery.projects.find(
          (x) => x.dir === root || x.root === root
        )) ||
      (name && state.discovery.projects.find((x) => x.name === name)) ||
      null;
    if (p) {
      await selectProject(p.dir);
      return;
    }
    toast(`プロジェクトが見つかりません: ${name || root || ''}`);
  });
}


function configuredCoworkItems() {
  const cw = (state.config && state.config.cowork) || {};
  return Array.isArray(cw.items) ? cw.items.map((x) => ({ ...x })) : [];
}

function coworkDraft() {
  // 発見項目（source:'discovered'）も編集できるよう、overview のマージ済み一覧を種にする。
  // **overview 未取得のうちは種を確定させない**——初回描画は refreshCowork より先に走るので、
  // ここで空配列をキャッシュすると、あとから発見項目が届いても画面が空のまま固まる
  // （.kiro/kiro-loop.yml があるのに定常業務が出ない、の正体）。
  const merged = (state.cowork && Array.isArray(state.cowork.items)) ? state.cowork.items : null;
  if (!merged) return configuredCoworkItems();
  if (!state.coworkDraft) state.coworkDraft = merged.map((x) => ({ ...x }));
  return state.coworkDraft;
}

function coworkRepos() {
  return (state.discovery.projects || [])
    .filter((p) => p && p.exists !== false)
    .map((p) => ({ dir: p.workspace || p.dir, label: p.charterName || p.name || p.dir }))
    .filter((p) => p.dir);
}

function coworkItemCount() {
  const live = (state.cowork && Array.isArray(state.cowork.items)) ? state.cowork.items.length : 0;
  const draft = state.coworkDraft ? state.coworkDraft.length : 0;
  const cfg = configuredCoworkItems().length;
  return Math.max(live, draft, cfg);
}

function selectedProjectFolder() {
  if (state.project && (state.project.workspace || state.project.dir)) {
    return state.project.workspace || state.project.dir;
  }
  const selected = (state.discovery.projects || []).find((project) => project && project.dir === state.selectedDir);
  return (selected && (selected.workspace || selected.dir)) || state.selectedDir || '';
}

// 選択中workspaceが提供する画面を決める。設定rootsはagent-project以外の
// kiro-loop専用フォルダも含むため、登録されているだけではagent-project扱いにしない。
function workspaceFeatureModel(discovery, selectedDir, coworkCount) {
  const projects = (discovery && discovery.projects) || [];
  const selected = projects.find((project) => project && project.dir === selectedDir) || null;
  // 初期ロードの選択前は従来画面を維持し、選択後に実体のマーカーで切り替える。
  const agentProject = selected ? Boolean(selected.isProject) : true;
  const cowork = Number(coworkCount || 0) > 0;
  return {
    agentProject,
    cowork,
    defaultTab: !agentProject && cowork ? 'cowork' : agentProject ? 'overview' : cowork ? 'cowork' : null,
  };
}

// 定常業務タブは常に表示する（作業が未登録のプロジェクトでも、空状態から追加へ導ける）。
// agent-project 系タブだけはワークスペースの内容に応じて出し分ける。
function updateCoworkTabVisibility() {
  const btn = $('tab-btn-cowork');
  const pane = $('tab-cowork');
  if (!btn || !pane) return;
  const folder = selectedProjectFolder();
  const coworkAvailable = coworkHasProjectConfig(state.cowork, folder) || state.coworkForcedOpen;
  const features = workspaceFeatureModel(state.discovery, state.selectedDir, coworkAvailable ? 1 : 0);
  for (const el of document.querySelectorAll('.tab[data-feature="agent-project"], .tabpane[data-feature="agent-project"]')) {
    el.classList.toggle('hidden', !features.agentProject);
    el.hidden = !features.agentProject;
  }
  // 定常業務タブ・ペインは常時表示（隠さない）。
  btn.classList.remove('hidden');
  btn.hidden = false;
  pane.classList.remove('hidden');
  pane.hidden = false;
  const current = document.querySelector('.tab.active');
  if (!current || current.hidden || current.classList.contains('hidden')) {
    // 現在のタブが隠れたときは、既定タブ（無ければ常時表示の定常業務）へ退避する。
    switchTab(features.defaultTab || 'cowork');
  }
}

async function refreshCowork({ probe = false, forceDiscover = false } = {}) {
  if (!api.coworkOverview) return;
  try {
    state.cowork = await api.coworkOverview({
      probeProcess: !!probe,
      forceDiscover: !!forceDiscover,
    });
  } catch (err) {
    state.cowork = { error: err.message, items: [] };
  }
  updateCoworkTabVisibility();
}
