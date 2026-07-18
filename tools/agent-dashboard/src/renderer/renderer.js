'use strict';

/* global api, Diff2HtmlUI */

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
  flowDetailView: 'overview', // 選択中 run の内部ビュー（overview / graph / history）
  flowMobileDetail: false,
  flowNodeIssue: null, // {token, issue|null}（実行中ノードのイシュー検索結果キャッシュ）
  // GitLab 突き合わせ結果を run 単位でキャッシュする（run を切り替えても保持し、再取得を避ける）。
  // { [runId]: { loading, at, byNode: {[id]:{reconciled,url,issueState,labels,relatedMrs,...}} } }
  flowReconcile: {},
  backlogFilter: 'active',
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
  amigos: null, // amigos:overview のスナップショット { missions, budget, errors }
  amigosBudgetSaving: false,
  // kiro-loop 端末（Phase A: capture-pane 視聴）
  kiroLoopTerm: null, // { repo, name, target, session, items, text, error, at }
  kiroLoopTimer: null,
  coworkRun: null,       // { id, phase: 'running'|'ok'|'error', message, at }
  coworkHistory: null,   // 履歴ダイアログのモデル { id, name, logs, history, file, text }
  timer: null,
  busy: false,
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
  offloaded: '実行中（委任）',
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
  drained: '消化完了',
  throttle: '予算超過（縮退）',
  // 実行（run）の状態
  failed: '失敗',
  canceled: '中止',
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
        '完了にするには: まず要対応で計画を承認し、実行完了を待つ（検収ゲートなら最後に承認）',
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
        '完了にするには: 修正後の再実行完了を待つ（操作不要）。要対応が残っていれば先に対応',
      needAsk: null,
    };
  }
  if (['ready', 'inbox', 'draft'].includes(st)) {
    return {
      unsettledDone: false,
      statusNote: null,
      completeHow: '完了にするには: 本体の実行完了を待つ（通常は操作不要）',
      needAsk: null,
    };
  }
  if (['doing', 'offloaded'].includes(st)) {
    return {
      unsettledDone: false,
      statusNote: null,
      completeHow: '完了にするには: 実行完了を待つ（操作不要）',
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
  return taskArchived ? '納品済み' : '実行完了（タスク未確定）';
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
      summary: '検証コマンド（verify）が未定義のため、自動では完了にできません。'
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
const GUIDE_KEYS = ['why', 'desc', 'scope', 'out_of_scope', 'constraints', 'hints', 'demo'];
const GUIDE_LABELS = {
  why: '背景・目的（なぜやるか。実装判断とレビューの基準）',
  desc: '作業内容の詳細（タイトルで足りない具体の指示）',
  scope: '変更してよい範囲（この外は変更させない）',
  out_of_scope: 'やらないこと（スコープ外・非目標）',
  constraints: 'タスク固有の制約（守るべき規約・禁止事項）',
  hints: '実装の手がかり（関連ファイル・参考実装）',
  demo: '人の確認観点（検収で何をどう確かめるか）',
};

const PROSE_EXTRA_KEYS = new Set(['feedback', 'needs_reason', 'note', 'accept', ...GUIDE_KEYS]);

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
  state.discovery = await api.discover();
  renderTree();
  checkNeedsNotifications();
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
          ? `新しく人の判断待ちが増えました（このプロジェクト計 ${r.total} 件）。クリックで開きます。`
          : '新しく人の判断待ちが発生しました。クリックで開きます。',
      target: { root: r.root, name: r.name },
      badgeCount: total,
      flash: true,
    });
  }
}

// プロジェクトの登録を実体に即して直接消す（config.roots のエントリ削除、または
// ~/.agent-project/instances/*.json の該当レコード削除。main/project.js の
// removeProjectRegistration 参照）。ファイル・ディレクトリ本体は一切削除しない。
// 親フォルダのスキャンで見つかった子は個別の登録が無いため、guard がエラーを表示する。
async function removeProject(dir) {
  const p = (state.discovery.projects || []).find((x) => x.dir === dir);
  const label = (p && (p.charterName || p.name)) || dir;
  const yes = await confirmDialog(
    `${label} の登録をこのビュアーから削除します。\n` +
      'プロジェクトのファイル・ディレクトリは一切削除しません。\n' +
      'よろしいですか？'
  );
  if (!yes) return;
  const res = await guard('プロジェクトの削除', () => api.removeProject(dir));
  if (!res) return;
  // config.roots が変わった可能性があるので設定キャッシュも同期しておく
  // （そのままだと後で設定ダイアログを保存したときに古い roots で上書きしてしまう）。
  state.config = await guard('設定読込', () => api.getConfig());
  toast(`${label} の登録を削除しました`, true);
  await refreshDiscovery();
  await refreshCowork();
  if (state.selectedDir === dir) {
    const next = (state.discovery.projects || []).find((x) => x.exists);
    if (next) {
      await selectProject(next.dir);
    } else {
      state.selectedDir = null;
      state.project = null;
      localStorage.removeItem('kpv:selected');
      renderAllTabs();
    }
  }
}

function renderTree() {
  const navigation = $('tree');
  const tree = $('project-list');
  const prevScroll = navigation.scrollTop; // 再描画（ポーリング）でサイドバーのスクロールを失わない
  const { instances } = state.discovery;
  // 実体が無い登録（exists:false）はここで弾く。過去に登録した config.roots のゴーストパスや、
  // 稼働していない/実在しないホストの instances/*.json（自動発見）が典型で、直せる見込みが無い
  // ままサイドバーに残り続けるだけなので、手動で消させるより最初から出さない方が親切。
  const projects = (state.discovery.projects || []).filter((p) => p.exists);
  if (!projects.length) {
    tree.innerHTML =
      '<div class="empty">プロジェクトが見つかりません。<br>⚙ 設定でワークスペース（.agent/agent-project.yaml のある開発フォルダ）を追加するか、<br>agent-project を稼働させてください。<br><br><button id="btn-empty-new" class="primary-inline">＋ 新規プロジェクトを作成</button></div>';
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
              ? `稼働中（別マシン・約${Math.round((live.ageSec || 0) / 60)}分前に確認）`
              : '稼働中'
            : remoteGuess
              ? `不明（最終確認 約${Math.round((live.ageSec || 0) / 60)}分前）`
              : '停止中';
        // 表示名は charter.md の `# Charter: <name>` を優先する（無ければフォルダ名）。
        // `.agent-project` のような技術的なフォルダ名でも、charter.md を編集するだけで
        // サイドバーに任意の名前を出せる（✎ charter.md から編集）。フォルダ名は行の
        // title 属性（フルパス）で見られるので、括弧併記はしない。
        const displayName = p.charterName || p.name;
        // 削除ボタンは config.roots に直接登録されたプロジェクト（source: 'config'）だけに出す。
        // scan（親フォルダ配下の自動発見）はそのプロジェクト個別の登録が無く、instance
        // （~/.agent-project/instances/ 自動発見）は稼働中プロセスが自分で書き直す一時的な
        // レコードなので、どちらも「消す」という操作の対象として筋が悪い（scan は親フォルダの
        // 登録ごと削除することになり、instance は生きていれば次のハートビートで復活する）。
        const removeBtn = p.source === 'config'
          ? `<button class="project-item-remove" data-remove-dir="${esc(p.dir)}" title="プロジェクトの登録をこのビュアーから削除する（ファイルは削除しません）">×</button>`
          : '';
        return `<div class="project-item ${state.selectedDir === p.dir ? 'selected' : ''}" data-dir="${esc(p.dir)}" title="${esc(p.dir)}">
          <span class="dot ${p.running ? 'running' : ''} ${remoteGuess ? 'synced' : ''} ${p.paused ? 'paused' : ''}" title="${esc(dotTitle)}"></span>
          <span class="name">${esc(displayName)}</span>${badges.join('')}
          ${removeBtn}
        </div>`;
      })
      .join('');
  }
  navigation.scrollTop = prevScroll;
  const live = instances.filter((i) => i.fresh).length;
  $('sidebar-footer').textContent = `稼働中 ${live} ／ 更新 ${new Date().toLocaleTimeString('ja-JP')}`;

  for (const el of tree.querySelectorAll('.project-item[data-dir]')) {
    el.addEventListener('click', () => selectProject(el.dataset.dir));
  }
  for (const btn of tree.querySelectorAll('button[data-remove-dir]')) {
    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();   // 親の project-item クリック（選択）を発火させない
      removeProject(btn.dataset.removeDir);
    });
  }
}

async function selectProject(dir) {
  state.selectedDir = dir;
  localStorage.setItem('kpv:selected', dir);
  renderTree();
  await reloadProject();
}

async function reloadProject({ refreshRemoteHealth = true } = {}) {
  if (!state.selectedDir) return;
  const project = await guard('プロジェクト読込', () => api.readProject(state.selectedDir));
  if (!project) return;
  project.needs = stabilizeMilestoneNeeds(state.project, project);
  // 同期の健康状態（ローカル参照のみ・リモートへは触らない）。失敗しても表示を欠くだけ。
  const gitHealth = await api.gitHealth(project.dir, refreshRemoteHealth).catch(() => null);
  // バスが未作成でも daemon の稼働はロックファイルから判定できるため常に読む。
  // project.dir は run アーカイブ（<dir>/flow-archive/）の置き場として渡す。
  const fr = (await guard('フロー読込', () => api.flowRuns(project.dir, project.busDir))) || {};
  // project だけ先に差し替えると、flowRuns が前プロジェクト分または空の短い時間が生じ、
  // 完了runを根拠にする承認ボタンが消える。両方を読み終えてから表示状態へ反映する。
  state.flowRuns = fr.runs || [];
  state.project = project;
  state.gitHealth = gitHealth;
  state.flowDaemon = fr.daemon || null;
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
  $('btn-project-settings').classList.remove('hidden');
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
  // 同期の健康状態を平易な一文で常時表示する。異常（error）は要対応として目立たせ、
  // 「なぜ画面が最新でないのか」「次に何を押せばよいのか」を人が推測しなくて済むようにする。
  const gh = state.gitHealth;
  if (gh && !gh.notRepo) {
    const cls = gh.level === 'error' ? 'sync-error' : gh.level === 'warn' ? 'sync-warn' : 'sync-ok';
    const checkedAgo = gh.remoteCheckedAt
      ? fmtAgo(new Date(gh.remoteCheckedAt).toISOString())
      : '';
    const checkedLabel = gh.remoteCheckError
      ? `共有先確認: ${checkedAgo ? `${checkedAgo}（再確認失敗）` : '失敗'}`
      : checkedAgo
        ? `共有先確認: ${checkedAgo}`
        : '';
    let action = '';
    if (gh.level === 'error') {
      action = '<button id="btn-sync-now" class="sync-action">同期を修復</button>';
    } else if (gh.level === 'warn' && !(gh.behind > 0 && gh.dirty > 0)) {
      action = '<button id="btn-sync-now" class="sync-action">共有先と同期</button>';
    }
    metaBits.push(
      `<span class="sync-status ${cls}" title="${esc(gh.summary)}">` +
        `<span class="status-dot" aria-hidden="true"></span> 同期: ${esc(gh.summary)} ` +
        `${checkedLabel ? `<small class="sync-checked">${esc(checkedLabel)}</small>` : ''} ${action}</span>`
    );
  }
  $('project-meta').innerHTML = metaBits.join(' ｜ ');
  const syncButton = $('btn-sync-now');
  if (syncButton) syncButton.addEventListener('click', manualGitHeal);
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

// 初版（charter.md）に後からバージョン名を付けて charters/<名前>.md へ移す（昇格）。
// 既存タスクの帰属タグ・project.json の収束状態（承認済み等）・milestone カードも引き継ぐ。
async function openPromoteCharter() {
  const p = state.project;
  if (!p || !p.charter) return toast('初版の憲章（charter.md）がありません');
  $('nc-title').textContent = '初版にバージョン名を付ける';
  $('nc-desc').textContent =
    '初版の憲章に名前を付けて、計画バージョンの一覧に加えます（内容は変わりません）。' +
    '初版のタスクや承認状態も引き継がれ、他のバージョンと並行して進むようになります。';
  $('nc-name').value = '';
  $('dlg-new-charter').dataset.mode = 'promote';
  $('dlg-new-charter').showModal();
  $('nc-name').focus();
}

async function submitPromoteCharter(name) {
  const p = state.project;
  if (!p) return;
  const res = await guard('バージョン名を付ける', () => api.promoteCharter(p.dir, name));
  if (!res) return;
  uiLog('promoteCharter', res);
  toast(`初版をバージョン「${res.name}」にしました（タスク ${res.tagged} 件を引き継ぎ）`, true);
  gitPushAfterWrite(`agent-dashboard: promote charter.md to charters/${res.name}.md`, p.dir);
  await reloadProject();
}

// 稼働操作（起動 / pause / resume / stop）。pause/resume/stop は commands/ ドロップ
// （＋git push）で届き、リモート本体（WSL・別ホスト）の watch が同期間隔内に取り込む。
// 起動だけはドロップでは届かない（停止中の本体は commands/ を読めない）ため、
// この PC の CLI で `agent-project start` を実行する（startAgentProject）。
function lifecycleCardHtml(p) {
  const live = p.liveness || {};
  const paused = !!live.paused;
  if (!live.running) {
    // 本体が停止中: pause/stop を出しても届かない（誰も読まない）。起動だけを出す
    return `
    <div class="card full">
      <h3>自動実行</h3>
      <div class="row">
        <button class="chip primary-inline" data-start-kiro
          title="このPCで自動実行を開始します">自動実行を開始</button>
        <span class="muted">停止中です。開始するまでタスクは進みません。</span>
      </div>
    </div>`;
  }
  return `
    <div class="card full">
      <h3>自動実行</h3>
      <div class="row">
        ${
          paused
            ? '<button class="chip" data-lifecycle="resume" title="一時停止を解除して作業を再開します">再開</button>'
            : '<button class="chip" data-lifecycle="pause" title="タスクの実行を一時停止します">一時停止</button>'
        }
        <button class="chip danger" data-lifecycle="stop"
          title="自動実行を停止します">停止</button>
      </div>
      ${paused ? '<div class="muted" style="margin-top:4px">一時停止中です。再開まで作業は進みません。</div>' : ''}
    </div>`;
}

// 実行中アクションの再入ガード。ボタン連打・IPC 応答待ち中の再クリックで同じ操作
// （start の二重起動・resubmit の二重積み直し）が並走するのを防ぐ。
const _inflightActions = new Set();
async function withActionLock(key, fn) {
  if (_inflightActions.has(key)) return null;
  _inflightActions.add(key);
  try {
    return await fn();
  } finally {
    _inflightActions.delete(key);
  }
}

// 本体（agent-project）の起動。確認 → CLI 実行 → 結果を平易に伝える。
// 本体が別マシンの構成では「この PC が実行役になる」ことを事前に言い、
// CLI が無ければ人が本体マシンで打つコマンドをそのまま見せる。
async function startAgentProject() {
  return withActionLock('start-project', _startAgentProject);
}

async function _startAgentProject() {
  const p = state.project;
  if (!p) return;
  const yes = await confirmDialog(
    `${p.name}: この PC で本体（agent-project の常駐）を起動します。\n` +
      '以後この PC がタスクを実行します。\n' +
      'プロジェクトの本体が別のマシン（WSL・別 PC）にある場合は、そちらで\n' +
      '  agent-project start\nを実行するほうが適切です。\nこの PC で起動しますか？'
  );
  if (!yes) return;
  try {
    const res = await api.startProject(p.dir);
    uiLog('start', res);
    toast('本体を起動しました（タスクの消化が始まります。表示への反映まで少し時間がかかります）', true);
  } catch (err) {
    uiLog('start failed', String(err.message || err));
    await confirmDialog(
      'この PC からは起動できませんでした（agent-project CLI が見つからないか失敗）。\n' +
        `理由: ${String(err.message || err).slice(0, 200)}\n\n` +
        '本体のマシンで次のコマンドを実行してください（このプロジェクトのフォルダで）:\n' +
        '  agent-project start\n\n' +
        'CLI の場所は ⚙ 設定の「agent-project CLI」でも指定できます。'
    );
    return;
  }
  await refreshAll();
}

const LIFECYCLE_CONFIRMS = {
  pause: (p) => `${p.name}: watch の消化を一時停止します（idle 監視・指示の取り込みは継続）。よろしいですか？`,
  resume: (p) => `${p.name}: 一時停止を解除して消化を再開します。よろしいですか？`,
  stop: (p) =>
    `${p.name}: 本体プロセスを停止します。\n再開はプロジェクトのマシン（WSL 等）で agent-project start を実行してください。よろしいですか？`,
};

function bindLifecycleButtons(root) {
  for (const b of root.querySelectorAll('button[data-start-kiro]')) {
    b.addEventListener('click', () => startAgentProject());
  }
  for (const b of root.querySelectorAll('button[data-lifecycle]')) {
    b.addEventListener('click', async () => {
      const p = state.project;
      if (!p) return;
      const action = b.dataset.lifecycle;
      const yes = await confirmDialog(LIFECYCLE_CONFIRMS[action](p));
      if (!yes) return;
      const labels = { pause: '一時停止を依頼しました', resume: '再開を依頼しました', stop: '停止を依頼しました' };
      const ok = await guard('稼働操作', async () => {
        const res = await api.requestLifecycle(p.dir, action, 'agent-dashboard から操作');
        uiLog('lifecycle', action, res);
        toast(`${labels[action] || '操作を送信しました'}（反映まで少し時間がかかることがあります）`, true);
        return true;
      });
      if (ok) {
        gitPushAfterWrite(`agent-dashboard: ${action}`, p.dir);
        await reloadProject();
      }
    });
  }
}

// 概要は「現在 → 人の対応 → 進捗 → 成果」の順に読むためのハブとする。
// 機械状態の細目は詳細タブへ送り、ここでは全体像と次の一手だけを返す。
function overviewSummary(p, flowRuns) {
  const live = p.liveness || { running: false };
  const undecided = (p.needs || []).filter((n) => !n.decided);
  const byStatus = p.byStatus || {};
  const working = Math.max((byStatus.doing || 0) + (byStatus.offloaded || 0), (p.claims || []).length);
  const waiting = (byStatus.ready || 0) + (byStatus.inbox || 0) + (byStatus.draft || 0) + (byStatus.proposed || 0);
  const done = (p.archive || []).length;
  const total = done + (p.backlog || []).filter((t) => t.status !== 'rejected').length;
  const progress = total ? Math.round((done / total) * 100) : 0;
  const activeRuns = (flowRuns || []).filter((r) => !['done', 'failed', 'canceled'].includes(String(r.status))).length;

  let headline;
  let tone;
  if (live.paused) {
    headline = '作業を一時停止しています';
    tone = 'warn';
  } else if (undecided.length) {
    headline = `${undecided.length} 件の確認を待っています`;
    tone = 'action';
  } else if (!live.running) {
    headline = '自動実行は停止しています';
    tone = 'warn';
  } else if (working) {
    headline = `${working} 件のタスクを進めています`;
    tone = 'running';
  } else if (waiting) {
    headline = `次の ${waiting} 件を順番に進めます`;
    tone = 'running';
  } else {
    headline = '現在の作業は完了しています';
    tone = 'ok';
  }

  return { live, undecided, working, waiting, done, total, progress, activeRuns, headline, tone };
}

function overviewGoal(p) {
  if (p.charter && (p.charter.goal || p.charter.name)) return p.charter.goal || p.charter.name;
  const current = (p.charters || []).find((c) => c.goal) || (p.charters || [])[0];
  return current ? current.goal || current.name : '目標はまだ設定されていません';
}

function versionUsage(p, name) {
  const belongsTo = (task) => String((task.extra && task.extra.charter) || '').trim() === name;
  return {
    active: (p.backlog || []).filter(belongsTo).length,
    done: (p.archive || []).filter(belongsTo).length,
  };
}

function overviewVersionsHtml(p) {
  const versions = p.charters || [];
  const stateByVersion = (p.projectState && p.projectState.charters) || {};
  const cards = versions.map((ch) => {
    const usage = versionUsage(p, ch.name);
    const used = usage.active + usage.done;
    const versionState = stateByVersion[ch.name] || {};
    const status = versionState.status
      ? statusLabel(versionState.status)
      : usage.active
        ? '進行中'
        : usage.done
          ? '作業完了'
          : '未開始';
    const deleteHelp = used
      ? `<p class="version-delete-note">関連する作業が ${used} 件あるため削除できません。</p>`
      : '';
    return `<article class="overview-version-card">
      <div class="version-card-heading">
        <div>
          <h3>${esc(ch.name)}</h3>
          <span class="version-state">${esc(status)}</span>
        </div>
      </div>
      <div class="version-card-goal">${
        ch.goal
          ? proseHtml(ch.goal)
          : p.charter && p.charter.master && p.charter.goal
            ? `${proseHtml(p.charter.goal)}<div class="muted">（共通設定の目標を継承）</div>`
            : '<span class="muted">やることは未設定です。</span>'
      }</div>
      <div class="version-card-counts" aria-label="作業状況">
        <span><strong>${usage.active}</strong> 進行中</span>
        <span><strong>${usage.done}</strong> 完了</span>
      </div>
      ${deleteHelp}
      <div class="version-card-actions">
        <button class="summary-link secondary" data-version-edit="${esc(ch.name)}">編集</button>
        <button class="danger" data-version-delete="${esc(ch.name)}"${used ? ' disabled' : ''}>削除</button>
      </div>
    </article>`;
  }).join('');
  const canPromote = p.charter && !p.charter.master && versions.length;
  return `<section class="overview-version-section" aria-labelledby="overview-versions-title">
    <div class="overview-version-heading">
      <div>
        <h2 id="overview-versions-title">計画バージョン</h2>
        <p>目的ごとの計画と進み具合を管理します。</p>
      </div>
      <div class="summary-actions">
        ${canPromote ? '<button class="summary-link secondary" id="btn-overview-promote-version">初版に名前を付ける</button>' : ''}
        <button class="summary-link" id="btn-overview-add-version">バージョンを追加</button>
      </div>
    </div>
    ${cards ? `<div class="overview-version-grid">${cards}</div>` : '<div class="overview-version-empty">計画バージョンはまだありません。最初のバージョンを追加してください。</div>'}
  </section>`;
}

async function deleteOverviewVersion(name) {
  const p = state.project;
  if (!p) return;
  const yes = await confirmDialog(
    `計画バージョン「${name}」を削除します。\nこの操作は元に戻せません。よろしいですか？`
  );
  if (!yes) return;
  const res = await guard('計画バージョンの削除', () => api.deleteCharter(p.dir, name));
  if (!res) return;
  toast(`計画バージョン「${name}」を削除しました`, true);
  gitPushAfterWrite(`agent-dashboard: delete charters/${name}.md`, p.dir);
  await reloadProject();
}

function renderOverview() {
  const p = state.project;
  const el = $('tab-overview');
  if (!p) {
    el.innerHTML = '<div class="empty">左の一覧からプロジェクトを選択してください</div>';
    return;
  }

  const s = overviewSummary(p, state.flowRuns);
  const goalText = overviewGoal(p);
  const deliveryRows = (p.delivery || [])
    .slice(-3)
    .reverse()
    .map((cells) => `<tr>${cells.map((c) => `<td>${linkify(c)}</td>`).join('')}</tr>`)
    .join('');
  const lifecycle = s.live.running
    ? s.live.paused
      ? '<button class="summary-link" data-lifecycle="resume">再開</button>'
      : '<button class="summary-link secondary" data-lifecycle="pause">一時停止</button>'
    : '<button class="summary-link" data-start-kiro>自動実行を開始</button>';

  el.innerHTML = `
    <div class="overview-shell">
      <section class="summary-hero tone-${esc(s.tone)}" aria-labelledby="summary-now-title">
        <h2 class="summary-kicker" id="summary-now-title">現在の状態</h2>
        <div class="summary-hero-main">
          <div>
            <div class="summary-headline">${esc(s.headline)}</div>
            <div class="summary-goal">${proseHtml(goalText)}</div>
          </div>
          <div class="summary-actions">${lifecycle}</div>
        </div>
        <div class="summary-progress" aria-label="全体進捗 ${s.progress}%">
          <div style="width:${s.progress}%"></div>
        </div>
        <div class="summary-progress-label">${s.total ? `${s.done} / ${s.total} 件完了（${s.progress}%）` : 'タスクはまだありません'}</div>
      </section>

      <div class="overview-grid">
        <section class="summary-card action-card ${s.undecided.length ? 'has-action' : ''}" aria-labelledby="summary-action-title">
          <h2 class="summary-kicker" id="summary-action-title">あなたの対応</h2>
          ${s.undecided.length
            ? `<div class="summary-number">${s.undecided.length}<span>件</span></div>
               <p>確認または判断が必要です。</p>
               <button class="summary-link" data-summary-tab="needs">対応する</button>`
            : `<div class="summary-status-ok">対応はありません</div>
               <p class="muted">このまま進行を見守れます。</p>`}
        </section>

        <section class="summary-card progress-card" aria-labelledby="summary-progress-title">
          <h2 class="summary-kicker" id="summary-progress-title">進捗</h2>
          <div class="summary-stats">
            <div><strong>${s.done}</strong><span>完了</span></div>
            <div><strong>${s.working}</strong><span>作業中</span></div>
            <div><strong>${s.waiting}</strong><span>これから</span></div>
          </div>
          <div class="summary-actions">
            <button class="summary-link" data-summary-tab="backlog">タスクを見る</button>
            <button class="summary-link secondary" data-summary-tab="flow">実行を見る${s.activeRuns ? `（${s.activeRuns}）` : ''}</button>
          </div>
        </section>

        <section class="summary-card deliveries-card" aria-labelledby="summary-deliveries-title">
          <h2 class="summary-kicker" id="summary-deliveries-title">成果</h2>
          ${deliveryRows
            ? `<div class="summary-deliveries"><table class="list">${deliveryRows}</table></div>`
            : '<p class="muted">まだ成果は記録されていません。</p>'}
          <button class="summary-link secondary" data-summary-tab="history">成果を見る</button>
        </section>
      </div>
      ${overviewVersionsHtml(p)}
    </div>`;

  for (const btn of el.querySelectorAll('button[data-summary-tab]')) {
    btn.addEventListener('click', () => switchTab(btn.dataset.summaryTab));
  }
  const addVersion = $('btn-overview-add-version');
  if (addVersion) addVersion.addEventListener('click', openAddCharterVersion);
  const promoteVersion = $('btn-overview-promote-version');
  if (promoteVersion) promoteVersion.addEventListener('click', openPromoteCharter);
  for (const btn of el.querySelectorAll('button[data-version-edit]')) {
    btn.addEventListener('click', () => openProjectFile(`charters/${btn.dataset.versionEdit}.md`));
  }
  for (const btn of el.querySelectorAll('button[data-version-delete]')) {
    btn.addEventListener('click', () => deleteOverviewVersion(btn.dataset.versionDelete));
  }
  bindLifecycleButtons(el);
}

function openProjectSettings() {
  const p = state.project;
  if (!p) return;
  const isMaster = !!(p.charter && p.charter.master);
  const danger = p.charter
    ? `<section class="project-settings-section danger-zone">
        <h3>リセット</h3>
        <p class="muted">計画、タスク、履歴を消して最初からやり直します。憲章は残ります。</p>
        <button class="danger" id="btn-settings-reset">プロジェクトをリセット</button>
      </section>`
    : '';

  $('project-settings-body').innerHTML = `
    <p class="muted">${esc(p.name)}</p>
    <section class="project-settings-section">
      <h3>プロジェクト定義</h3>
      <div class="settings-action-grid">
        <button data-edit="charter.md">${isMaster ? 'マスター憲章' : '憲章'}</button>
        <button data-edit="policy.md">運用ルール</button>
        <button data-edit="rules.md">プロジェクトルール</button>
        <button data-edit="repos.json">リポジトリ</button>
      </div>
    </section>
    <section class="project-settings-section">
      <h3>調査と高度な設定</h3>
      <p class="muted">実行ID、内部ログ、同期方式などは通常の操作には必要ありません。</p>
      <button id="btn-project-technical-info">詳細情報を開く</button>
    </section>
    ${danger}`;

  for (const btn of $('project-settings-body').querySelectorAll('button[data-edit]')) {
    btn.addEventListener('click', () => {
      $('dlg-project-settings').close();
      openProjectFile(btn.dataset.edit);
    });
  }
  const reset = $('btn-settings-reset');
  if (reset) reset.addEventListener('click', () => {
    $('dlg-project-settings').close();
    resetProject();
  });
  const technicalInfo = $('btn-project-technical-info');
  if (technicalInfo) technicalInfo.addEventListener('click', () => {
    $('dlg-project-settings').close();
    openTechnicalInfo();
  });
  $('dlg-project-settings').showModal();
}

// プロジェクトのリセット（危険操作）。charter.md 以外の全データを削除し、バスの
// agent-flow daemon を停止する。charter が残るので、稼働中の agent-project は次パスで
// charter から再分解して最初からやり直す（done の記録・needs・決定記録もすべて消える）。
async function resetProject() {
  const p = state.project;
  if (!p || !p.charter) return;
  const sharedBusNote =
    p.busSource && p.busSource !== 'project'
      ? '\n⚠ 実行基盤を他プロジェクトと共有しています: 停止は他プロジェクトの実行にも影響します。'
      : '';
  const yes = await confirmDialog(
    `${p.name}: プロジェクト憲章（charter.md）以外の全データを削除し、実行エンジンを停止します。\n` +
      `削除対象: 計画バージョン・タスク ${p.backlog.length} 件・完了記録 ${p.archive.length} 件・要対応 ${p.needs.length} 件・` +
      `実行中 ${p.claims.length} 件、および履歴・納品記録などの全ファイル。\n` +
      `ファイルはゴミ箱へ移動します（ゴミ箱の無い環境では完全削除）。${sharedBusNote}\n` +
      `憲章はプロジェクト全体の前提（マスター）として残ります。マスターは分解されないので、` +
      `リセット後は待機状態になり、計画バージョンを追加すると作業が再開します。よろしいですか？`
  );
  if (!yes) return;
  const ok = await guard('プロジェクトのリセット', async () => {
    const res = await api.resetProject(p.dir, p.workspace);
    uiLog('reset', res);
    const d = res.daemon || {};
    const daemonMsg = !d.running
      ? '実行エンジンは稼働していませんでした'
      : d.stopped
        ? '実行エンジンを停止しました'
        : d.remote
          ? '実行エンジンは別のマシンで稼働中のため、そちらで停止してください'
          : '実行エンジンを停止できませんでした';
    const errMsg = res.errors && res.errors.length ? `／削除できなかったもの ${res.errors.length} 件` : '';
    const masterMsg = res.masterized ? '／憲章をマスターに整えました' : '';
    toast(`${p.name}: ${res.removed.length} 件を削除（憲章は残しました）${masterMsg}${errMsg}。${daemonMsg}`, !errMsg);
    return true;
  });
  if (ok) {
    gitPushAfterWrite('agent-dashboard: project reset (keep charter)', p.dir);
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
  if (name === 'kiro-loop') startKiroLoopCapturePoll();
  else stopKiroLoopCapturePoll();
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
            `<button class="chip ${((state.backlogCharter || '') === v) ? 'active' : ''}" data-charter-filter="${esc(v)}">${esc(label)}</button>`
        )
        .join('')
    : '';

  // priority 降順 → 古い順（planner none と同じ感覚）
  tasks = [...tasks].sort((a, b) => b.priority - a.priority || a.mtime - b.mtime);

  const rows = tasks
    .map((t) => {
      const extras = [];
      if (t.extra.charter) extras.push(`バージョン: ${t.extra.charter}`);
      if (t.extra.why) extras.push(`目的: ${t.extra.why}`);
      else if (charterNames.length) extras.push('バージョン: 初版'); // 複数バージョン運用でのタグ無し＝charter.md 由来
      if (t.extra.after) extras.push(`依存: ${t.extra.after}`);
      if (t.extra.level) extras.push(`自動化レベル: ${t.extra.level}`);
      if (t.extra.track) extras.push(`系列: ${t.extra.track}`);
      if (t.extra.review) extras.push(`検収: ${t.extra.review}`);
      if (t.status === 'offloaded' && t.extra.flow_loc) {
        extras.push('委任先で実行中'); // act_async: agent-flow daemon で結果待ち（所在はタスク詳細で見る）
      }
      const rr = runsForTask(t.id); // 紐づく agent-flow run（リトライ系統）
      const hint =
        state.backlogFilter === 'archive'
          ? taskCompletionHint(t, { runs: rr, archived: true })
          : taskCompletionHint(t, { runs: rr });
      if (hint.statusNote) extras.unshift(hint.statusNote);
      extras.push(hint.completeHow);
      const runBadge = rr.length
        ? ` <button class="badge run-link" data-goto-run="${esc(rr[0].runId)}" title="関連する実行 ${rr.length} 件（最新: ${esc(runStatusCaption(rr[0].status, { taskArchived: state.backlogFilter === 'archive' }))}）を開く">⚙${rr.length}</button>`
        : '';
      // 非ブロッキング委譲（offloaded）は flow_run（実行中の run-id）へ直接リンクする
      // （runsForTask が拾えない＝フローバス未登録でも辿れるように明示リンクを出す）。
      const offloadRun = t.status === 'offloaded' ? String(t.extra.flow_run || '').trim() : '';
      const offloadBadge =
        offloadRun && !(rr.length && rr[0].runId === offloadRun)
          ? ` <button class="badge run-link" data-goto-run="${esc(offloadRun)}" title="実行中の作業を開く">▶ 実行</button>`
          : '';
      return `<tr class="clickable" data-task="${esc(t.id)}" data-scope="${state.backlogFilter === 'archive' ? 'archive' : 'backlog'}">
        <td class="mono">${esc(t.id)}</td>
        <td>${esc(t.title)}</td>
        <td>${statusChip(t.status)}${p.claims.includes(t.id) ? ' <span class="badge info" title="実行中">▶</span>' : ''}${isReviseSent(t) ? ' <span class="badge" title="修正指示を送信済み（反映待ち）">✎</span>' : ''}${runBadge}${offloadBadge}</td>
        <td>${t.priority}</td>
        <td>${t.retries}</td>
        <td>${t.verify ? '✓' : t.extra.accept || t.extra.verify_template ? '△' : '—'}</td>
        <td class="muted task-complete-how">${esc(extras.join(' ／ '))}</td>
      </tr>`;
    })
    .join('');

  const replanPending = !!p.replanPending;
  el.innerHTML = `
    ${pipelineRibbonHtml(p)}
    <div class="filters">${chips}${charterChips}<span class="muted">${tasks.length} 件</span>
      ${p.inboxFiles && p.inboxFiles.length ? `<span class="badge info" title="追加したタスクは次の実行サイクルで一覧に載ります">追加待ち ${p.inboxFiles.length}</span>` : ''}
      ${replanPending ? '<span class="badge info" title="計画の作り直しを依頼済みです。次の実行で反映されます">再計画 反映待ち</span>' : ''}
      <span class="spacer"></span>
      <button id="btn-replan" class="primary-inline"${replanPending ? ' disabled' : ''} title="プロジェクト憲章からタスクを作り直します（やり直し・復旧用）。進行中・却下済みと重複するタスクは追加されません（完了済みと同種のやり直しは作り直されます）">↻ 計画を作り直す</button>
      <button id="btn-enqueue" class="primary-inline" title="タスクを 1 件追加します（次の実行サイクルで一覧に載ります）">＋ タスクを追加</button>
    </div>
    <details class="backlog-help" data-ui-key="backlog-help">
      <summary>タスク一覧の変え方（反映は次の実行サイクル・即時ではありません）</summary>
      <div class="muted">
        <b>追加</b>: 「＋ タスクを追加」→ 次の実行サイクルで一覧に載ります。<br>
        <b>変更</b>: 行をクリック →「✎ 修正を指示」でタイトル・優先度・完了条件・依存関係の変更と、作業への指示ができます。実行中のタスクに送ると、現在の作業を打ち切って修正内容でやり直します。<br>
        <b>計画の作り直し</b>: 「↻ 計画を作り直す」→ プロジェクト憲章からタスクを分解し直します。進行中・却下済みと重複するタスクは追加されません（完了済みと同種のやり直しは作り直されます）。計画の失敗やタスクの誤削除、完了後のやり直しからの復旧に使います。<br>
        タスクの完了は検証結果だけで決まるため、この画面から状態（完了など）を直接書き換えることはできません。
      </div>
    </details>
    ${
      rows
        ? `<table class="list"><tr><th>ID</th><th>タイトル</th><th>状態</th><th>優先度</th><th>再試行</th><th>検証</th><th>属性</th></tr>${rows}</table>`
        : '<div class="empty">タスクなし</div>'
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

// ---------------------------------------------------------------------------
// オーサリング: 新規プロジェクト作成・プロジェクトファイル編集
// ---------------------------------------------------------------------------

// 既知プロジェクトの親フォルダ ＋ 設定 roots の親（新規作成先の候補）
function knownRoots() {
  const roots = new Set();
  for (const p of state.discovery.projects || []) {
    if (p.dir) roots.add(p.dir.replace(/[\\/][^\\/]+$/, ''));
  }
  for (const r of (state.config && state.config.projects && state.config.projects.roots) || []) {
    if (r) roots.add(String(r).replace(/[\\/][^\\/]+$/, ''));
  }
  return [...roots].filter(Boolean);
}

// 新規プロジェクトの repos 行を 1 つ追加する（任意・複数可）。
// path はモノレポ内の担当フォルダ＝同じ URL を役割別に複数エントリへ分ける識別子
// （schemas/repos.schema.json の (url, path, base) identity）。target は MR/PR 先（省略=base）。
function addRepoRow(prefill = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'np-repo-row';
  wrap.innerHTML = `
    <input class="np-r-name mono" placeholder="名前" value="${esc(prefill.name || '')}" />
    <input class="np-r-url mono" placeholder="git URL（必須）" value="${esc(prefill.url || '')}" />
    <input class="np-r-base mono" placeholder="ベース 例 main" value="${esc(prefill.base || '')}" />
    <input class="np-r-target mono" placeholder="MR先（省略=ベース）" value="${esc(prefill.target || '')}" />
    <input class="np-r-path mono" placeholder="モノレポ内フォルダ 例 apps/api" value="${esc(prefill.path || '')}" />
    <input class="np-r-owns mono" placeholder="担当範囲（省略=参照のみ）" value="${esc(prefill.owns || '')}" />
    <input class="np-r-desc" placeholder="説明" value="${esc(prefill.desc || '')}" />
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
  $('np-memo').value = '';
  $('np-deliverables').value = '';
  $('np-constraints').value = '';
  $('np-assumptions').value = '';
  $('np-acceptance').value = '';
  $('np-repos').innerHTML = '';
  $('np-ai-status').textContent = '';
  $('btn-np-ai').disabled = false;
  $('dlg-new-project').showModal();
}

// フォームの書きかけ（goal・自由メモ・各欄）からエージェントに各セクションを
// 下書きさせ、返ってきたフィールドだけを流し込む（応答はテキストのみ・保存はしない）。
// 新規作成時はまだプロジェクトが無いので、CLI の解決は ⚙ 設定 → 既定 kiro。
async function aiDraftCharter() {
  const btn = $('btn-np-ai');
  const status = $('np-ai-status');
  const spec = {
    name: $('np-name').value.trim() || ($('np-charter') ? $('np-charter').value.trim() : ''),
    goal: $('np-goal').value,
    memo: $('np-memo').value,
    deliverables: $('np-deliverables').value,
    constraints: $('np-constraints').value,
    assumptions: $('np-assumptions').value,
    acceptance: $('np-acceptance').value,
  };
  if (!spec.goal.trim() && !spec.memo.trim()) {
    return toast('目標か自由メモに、やりたいことを一言書いてから実行してください');
  }
  btn.disabled = true;
  status.textContent = 'エージェントに問い合わせ中…（モデル応答まで数十秒かかることがあります）';
  try {
    const res = await api.agentCharter({ mode: 'draft', spec });
    const f = res.fields || {};
    if (f.goal) $('np-goal').value = f.goal;
    if (f.deliverables) $('np-deliverables').value = f.deliverables;
    if (f.constraints) $('np-constraints').value = f.constraints;
    if (f.assumptions) $('np-assumptions').value = f.assumptions;
    if (f.acceptance) $('np-acceptance').value = f.acceptance;
    status.textContent = `下書きしました（${res.cli}${res.model ? ` / ${res.model}` : ''}）— 内容を確認・修正してから作成してください`;
  } catch (err) {
    status.textContent = '';
    toast(`AI 下書きに失敗しました: ${err.message || err}`);
  } finally {
    btn.disabled = false;
  }
}

async function submitNewProject() {
  const repos = [...document.querySelectorAll('#np-repos .np-repo-row')]
    .map((row) => ({
      name: row.querySelector('.np-r-name').value.trim(),
      url: row.querySelector('.np-r-url').value.trim(),
      base: row.querySelector('.np-r-base').value.trim(),
      target: row.querySelector('.np-r-target').value.trim(),
      path: row.querySelector('.np-r-path').value.trim(),
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
    assumptions: $('np-assumptions').value,
    acceptance: $('np-acceptance').value,
    repos,
    // 新規プロジェクトはマスター運用で作る: charter.md は全バージョン共通の憲章（分解されない）、
    // やるべきことは計画バージョン（charters/<名前>.md）に書く。
    master: true,
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
    cfg.projects = cfg.projects || {};
    cfg.projects.roots = cfg.projects.roots || [];
    if (!cfg.projects.roots.includes(res.dir)) {
      cfg.projects.roots.push(res.dir);
      state.config = await api.saveConfig(cfg);
    }
  }
  gitPushAfterWrite(`agent-dashboard: create project ${spec.name}`, res.dir);
  $('dlg-new-project').close();
  await refreshDiscovery();
  await selectProject(res.dir);
}

// charter.md / policy.md / repos.json の直接編集ダイアログを開く。
// これらは agent-project の「人が書く入力」— 編集して保存すると次の run で後段
// （backlog 生成・ルーティング）に反映される。タスク状態は編集対象にしない。
// charter ファイル（charter.md / charters/<name>.md）か。編集ダイアログの
// 入力補助（雛形挿入・AI 補完・セクションガイド）を出すかどうかの判定に使う
function isCharterFile(name) {
  return name === 'charter.md' || /^charters\/[^/\\]+\.md$/.test(name);
}

// ---------------------------------------------------------------------------
// フォーム編集（マークダウン/JSON を直接書かせず、入力欄で編集する）
//   charter → 目標・制約・前提・成果物・完了条件のフォーム（マスター/バージョンで項目を切替）
//   policy  → 運用ルールの行リスト（種類 + 対象）
//   repos   → リポジトリの行リスト（名前/URL/ベース/担当範囲/説明）
//   各フォームには「テキストで編集」があり、必要なら従来の生テキスト編集へ切り替えられる。
// ---------------------------------------------------------------------------

// 編集ボタン（data-edit）のルーティング: 種類ごとにフォームを開く。
function openProjectFile(name, opts) {
  if (name === 'policy.md') return openPolicyForm();
  if (name === 'repos.json') return openReposForm();
  if (isCharterFile(name)) return openCharterForm(name, opts);
  return openEditFile(name, opts); // その他は生テキスト編集
}

// 単一入力の行リスト。値の配列を描画し、各行に入力＋削除。追加は container._add('') で。
function renderSimpleList(container, items, placeholder) {
  container.innerHTML = '';
  const add = (val) => {
    const row = document.createElement('div');
    row.className = 'list-row';
    row.innerHTML =
      `<input class="list-input mono" value="${esc(val || '')}" placeholder="${esc(placeholder || '')}" />` +
      `<button type="button" class="list-del" title="削除">✕</button>`;
    row.querySelector('.list-del').addEventListener('click', () => row.remove());
    container.appendChild(row);
  };
  (Array.isArray(items) ? items : []).forEach(add);
  container._add = add;
}

function readSimpleList(container) {
  return [...container.querySelectorAll('.list-input')].map((i) => i.value.trim()).filter(Boolean);
}

// -------- charter フォーム --------

// 現在編集中の charter フォーム状態（保持セクション・master・version 名を持ち回る）
let charterForm = null;

async function openCharterForm(name, opts) {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const res = await guard('憲章の読込', () => api.readCharterFields(p.dir, name));
  if (!res) return;
  const fields = res.fields;
  const isVersion = /^charters\//.test(name);
  const isMaster = !isVersion && !!fields.master;
  // 継承の判定材料（プレビューで fields を書き換える前に控える）。
  // 見出しの無いバージョンはマスターへ**動的に**追従する（本体 _merge_master_charter の
  // 「見出しの有無」規則）。画面には実際に適用されるマスター値を初期表示し、保存時は
  // 「値を変えたときだけ」明示値として見出しを書く（変えなければ追従を維持）。
  const origConstraintsDefined = !!fields._constraintsDefined;
  const origAssumptionsDefined = !!fields._assumptionsDefined;
  let inheritedConstraints = null;
  let inheritedAssumptions = null;
  if (isVersion && (!origConstraintsDefined || !origAssumptionsDefined)) {
    const inherited = await guard('共通設定の読込', () => api.readCharterFields(p.dir, 'charter.md'));
    // 継承元になるのは charter.md がマスター（## master 付き）のときだけ。
    // 非マスターの charter.md から本体は継承しないので、値を「継承」として見せない。
    if (inherited && inherited.fields && inherited.fields.master) {
      inheritedConstraints = inherited.fields.constraints || [];
      inheritedAssumptions = inherited.fields.assumptions || [];
      if (!origConstraintsDefined) fields.constraints = inheritedConstraints.slice();
      if (!origAssumptionsDefined) fields.assumptions = inheritedAssumptions.slice();
    }
  }
  // 新規バージョン追加時は、前バージョン（または憲章）から引き継いだ やること/完了条件/成果物 を
  // 初期値にする（既存ファイルの編集では上書きしない＝res.exists のときは seed を使わない）。
  // 制約・前提はコピーせず、上の継承表示に任せる（コピーすると追従が切れた明示値になる）。
  if (!res.exists && opts) {
    if (opts.seedGoal) fields.goal = opts.seedGoal;
    if (Array.isArray(opts.seedAcceptance)) fields.acceptance = opts.seedAcceptance;
    if (Array.isArray(opts.seedDeliverables)) fields.deliverables = opts.seedDeliverables;
  }
  charterForm = {
    dir: p.dir, name, fields, isVersion, isMaster, exists: res.exists,
    origConstraintsDefined, origAssumptionsDefined, inheritedConstraints, inheritedAssumptions,
  };

  // 見出し・説明
  const verName = isVersion ? name.replace(/^charters\//, '').replace(/\.md$/, '') : '';
  $('ec-title').textContent = isVersion
    ? `計画バージョンを編集: ${verName}`
    : isMaster
      ? 'マスター憲章を編集'
      : '憲章を編集';
  $('ec-desc').textContent = isVersion
    ? 'このバージョンで達成すること、完了条件、制約、前提を設定します。新規作成時は共通設定を引き継ぎ、ここで個別に変更できます。'
    : isMaster
      ? '全バージョン共通の前提です。ここからタスクは作られません（完了条件は各バージョンが持ちます）。'
      : '目標と完了条件、制約・前提・成果物を記入します。';

  // 名前（バージョンはファイル名が識別子なので隠す。マスター/単一はプロジェクト名として編集可）
  $('ec-name-field').classList.toggle('hidden', isVersion);
  $('ec-name').value = fields.name || (isVersion ? verName : p.name || '');

  // 目標/やること
  $('ec-goal-label').textContent = isVersion ? 'やること（このバージョンで達成すること）' : '目標';
  $('ec-goal').value = fields.goal || '';

  // 完了条件（acceptance）はバージョン、または「マスターでない単一 charter」に出す。マスターは非表示。
  const showAcceptance = !isMaster;
  $('ec-acceptance-field').classList.toggle('hidden', !showAcceptance);
  renderSimpleList($('ec-acceptance'), fields.acceptance, '例: pytest -q tests/ または accept: 使用例が載っている');

  // 成果物は常に出す
  renderSimpleList($('ec-deliverables'), fields.deliverables, '例: report.py');

  // 制約・前提は新規版で共通設定をコピーするが、保存後は各バージョン固有の値になる。
  $('ec-constraints-field').classList.remove('hidden');
  $('ec-assumptions-field').classList.remove('hidden');
  renderSimpleList($('ec-constraints'), fields.constraints, '例: 標準ライブラリのみ');
  renderSimpleList($('ec-assumptions'), fields.assumptions, '例: 入力は UTF-8');

  // 継承の状態を実態に合わせて表示する:
  //   追従中（見出し無し・マスターあり）→ 変更しない限り共通設定に追従し続ける
  //   明示値（見出しあり）→ このバージョン固有・共通設定の変更には追従しない
  const note = $('ec-inherit-note');
  if (isVersion && inheritedConstraints !== null) {
    note.textContent =
      origConstraintsDefined && origAssumptionsDefined
        ? '制約・前提はこのバージョン固有の値です（共通設定の変更には追従しません）。対象リポジトリは共通設定を使用します。'
        : '制約・前提は共通設定（マスター）の値を表示しています。変更しなければ共通設定に追従し続け、' +
          '変更するとこのバージョンだけの値になります（すべて削除すると「空で上書き」として保存されます）。' +
          '対象リポジトリは共通設定を使用します。';
    note.classList.remove('hidden');
  } else if (isVersion) {
    note.textContent = '制約・前提はこのバージョン固有の値です。対象リポジトリは共通設定を使用します。';
    note.classList.remove('hidden');
  } else {
    note.classList.add('hidden');
  }
  $('ec-hint').textContent = res.exists
    ? '保存した内容は次回の自動実行から反映されます'
    : '未作成 — 保存すると新規作成します';
  $('dlg-edit-charter').showModal();
}

async function saveCharterForm() {
  const cf = charterForm;
  if (!cf) return;
  if (cf.isVersion && !$('ec-goal').value.trim()) {
    return toast('やること（このバージョンで達成すること）を記入してください');
  }
  // 完了条件が無いバージョンは done を判定できず、要対応に「完了条件を追加」が出続ける。
  // 保存前に確認して、うっかり空のまま作るのを防ぐ（意図的なら続行できる）。
  if (cf.isVersion && !readSimpleList($('ec-acceptance')).length) {
    const yes = await confirmDialog(
      '完了条件が未設定です。\nこのままだと完了を判定できず、要対応に「完了条件を追加」が出続けます。\n' +
        'このまま保存しますか？（後から追加もできます）'
    );
    if (!yes) return;
  }
  // フォームの値をフィールドへ反映（保持セクション _reposRaw/_linksRaw/_masterRaw はそのまま残す）
  const f = { ...cf.fields };
  f.master = cf.isMaster;
  if (!cf.isVersion) f.name = $('ec-name').value.trim() || f.name;
  else f.name = cf.name.replace(/^charters\//, '').replace(/\.md$/, ''); // バージョンはファイル名を名前に
  f.goal = $('ec-goal').value.trim();
  f.deliverables = readSimpleList($('ec-deliverables'));
  if (!cf.isMaster) f.acceptance = readSimpleList($('ec-acceptance'));
  const cons = readSimpleList($('ec-constraints'));
  const assum = readSimpleList($('ec-assumptions'));
  f.constraints = cons;
  f.assumptions = assum;
  // 見出しの扱い（本体の継承規則「見出しがあれば明示値・無ければマスターへ追従」と対）:
  //   元々見出しがある → 明示値のまま維持。
  //   マスターへ追従中 → 値を変えていなければ見出しを書かず追従を維持、変えたときだけ明示化
  //   （全削除は「継承を空に上書き」の明示の意思として空見出しを書く）。
  //   継承元が無い → 値を入れたときだけ見出しを書く。
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  f._constraintsDefined = cf.origConstraintsDefined
    || (cf.inheritedConstraints !== null ? !same(cons, cf.inheritedConstraints) : cons.length > 0);
  f._assumptionsDefined = cf.origAssumptionsDefined
    || (cf.inheritedAssumptions !== null ? !same(assum, cf.inheritedAssumptions) : assum.length > 0);
  const ok = await guard('保存', async () => {
    await api.writeCharterFields(cf.dir, cf.name, f);
    return true;
  });
  if (ok) {
    toast(`${cf.isVersion ? '計画バージョン' : '憲章'}を保存しました`, true);
    gitPushAfterWrite(`agent-dashboard: edit ${cf.name}`, cf.dir);
    $('dlg-edit-charter').close();
    await reloadProject();
  }
}

// フォームから生テキスト編集へ切り替える（込み入った編集や、フォームが扱わない項目の調整用）。
function charterFormToRaw() {
  const cf = charterForm;
  if (!cf) return;
  $('dlg-edit-charter').close();
  openEditFile(cf.name);
}

// -------- policy フォーム --------

const POLICY_KIND_OPTIONS = [
  ['deny', '自動実行しない（deny）'],
  ['pin', '最優先にする（pin）'],
  ['defer', '後回しにする（defer）'],
  ['offload', '委任で実行（offload）'],
  ['gate', '承認を必須にする（gate）'],
  ['protect', '保護する（protect）'],
  ['route', '振り分け先を指定（route）'],
];

let policyForm = null;

function renderPolicyRules(container, rules) {
  container.innerHTML = '';
  const opts = (sel) =>
    POLICY_KIND_OPTIONS.map(([k, label]) => `<option value="${k}"${sel === k ? ' selected' : ''}>${esc(label)}</option>`).join('');
  const add = (r) => {
    const row = document.createElement('div');
    row.className = 'list-row';
    row.innerHTML =
      `<select class="pol-kind">${opts(r && r.kind)}</select>` +
      `<input class="pol-value mono" value="${esc((r && r.value) || '')}" placeholder="対象（タスクのタイトルや ID にマッチする語）" />` +
      `<button type="button" class="list-del" title="削除">✕</button>`;
    row.querySelector('.list-del').addEventListener('click', () => row.remove());
    container.appendChild(row);
  };
  (Array.isArray(rules) ? rules : []).forEach(add);
  container._add = add;
}

async function openPolicyForm() {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const res = await guard('運用ルールの読込', () => api.readPolicy(p.dir));
  if (!res) return;
  policyForm = { dir: p.dir };
  renderPolicyRules($('ep-rules'), res.rules);
  $('dlg-edit-policy').showModal();
}

async function savePolicyForm() {
  const pf = policyForm;
  if (!pf) return;
  const rules = [...$('ep-rules').querySelectorAll('.list-row')]
    .map((row) => ({
      kind: row.querySelector('.pol-kind').value,
      value: row.querySelector('.pol-value').value.trim(),
    }))
    .filter((r) => r.value);
  const ok = await guard('保存', async () => {
    await api.writePolicy(pf.dir, rules);
    return true;
  });
  if (ok) {
    toast('運用ルールを保存しました', true);
    gitPushAfterWrite('agent-dashboard: edit policy.md', pf.dir);
    $('dlg-edit-policy').close();
    await reloadProject();
  }
}

// -------- repos フォーム --------

let reposForm = null;

function renderRepoRows(container, rows) {
  container.innerHTML = '';
  const add = (r) => {
    const row = document.createElement('div');
    row.className = 'np-repo-row';
    row.innerHTML =
      `<input class="er-name mono" placeholder="名前" value="${esc((r && r.name) || '')}" />` +
      `<input class="er-url mono" placeholder="git URL（必須）" value="${esc((r && r.url) || '')}" />` +
      `<input class="er-base mono" placeholder="ベース 例 main" value="${esc((r && r.base) || '')}" />` +
      `<input class="er-target mono" placeholder="MR先（省略=ベース）" value="${esc((r && r.target) || '')}" />` +
      `<input class="er-path mono" placeholder="モノレポ内フォルダ 例 apps/api" value="${esc((r && r.path) || '')}" />` +
      `<input class="er-owns mono" placeholder="担当範囲（省略=参照のみ）" value="${esc((r && r.owns) || '')}" />` +
      `<input class="er-desc" placeholder="説明" value="${esc((r && r.desc) || '')}" />` +
      `<button type="button" class="np-r-del" title="削除">✕</button>`;
    // フォームが列を持たないキー（readonly/local/docs 等 = _extra）は行の DOM に持ち回り、
    // 保存時にそのまま書き戻す（フォームを開いて保存しただけで消えないように）。
    row._readonly = !!(r && r.readonly);
    row._extra = (r && r._extra) || null;
    if (row._extra) {
      row.querySelector('.er-desc').title =
        `フォーム外の設定を保持しています: ${Object.keys(row._extra).join(', ')}（保存時にそのまま残ります）`;
    }
    row.querySelector('.np-r-del').addEventListener('click', () => row.remove());
    container.appendChild(row);
  };
  (Array.isArray(rows) ? rows : []).forEach(add);
  container._add = add;
}

async function openReposForm() {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const res = await guard('リポジトリ一覧の読込', () => api.readRepos(p.dir));
  if (!res) return;
  // repos.yaml / repos.yml が正のプロジェクトはフォームで扱えない（保存すると repos.json が
  // できるが本体は yaml 優先で無視する）。生テキスト編集へ誘導する。
  if (res.yamlFile) {
    toast(`このプロジェクトは ${res.yamlFile} が正です。テキスト編集で開きます`);
    return openEditFile(res.yamlFile);
  }
  reposForm = { dir: p.dir };
  renderRepoRows($('er-rows'), res.rows);
  const warn = $('er-warning');
  if (warn) {
    if (res.generated) {
      warn.textContent =
        '⚠ この repos.json は charter.md の ## repos から自動生成されています。ここで保存すると' +
        '手管理（repos.json が正）に切り替わり、以後 charter の ## repos は反映されなくなります。' +
        'charter 主導のままにするなら、charter.md の ## repos を編集してください。';
      warn.classList.remove('hidden');
    } else {
      warn.classList.add('hidden');
    }
  }
  $('dlg-edit-repos').showModal();
}

async function saveReposForm() {
  const rf = reposForm;
  if (!rf) return;
  const rows = [...$('er-rows').querySelectorAll('.np-repo-row')]
    .map((row) => ({
      name: row.querySelector('.er-name').value.trim(),
      url: row.querySelector('.er-url').value.trim(),
      base: row.querySelector('.er-base').value.trim(),
      target: row.querySelector('.er-target').value.trim(),
      path: row.querySelector('.er-path').value.trim(),
      owns: row.querySelector('.er-owns').value.trim(),
      desc: row.querySelector('.er-desc').value.trim(),
      readonly: row._readonly || false,
      ...(row._extra ? { _extra: row._extra } : {}),
    }))
    .filter((r) => r.url);
  const ok = await guard('保存', async () => {
    await api.writeRepos(rf.dir, rows);
    return true;
  });
  if (ok) {
    toast('リポジトリ一覧を保存しました', true);
    gitPushAfterWrite('agent-dashboard: edit repos.json', rf.dir);
    $('dlg-edit-repos').close();
    await reloadProject();
  }
}

const CHARTER_SECTION_GUIDE =
  '書式（セクション）: ## goal（目標）/ ## constraints（制約）/ ## assumptions（前提）/ ' +
  '## deliverables（成果物）/ ## acceptance（完了条件 — 成功で終わるコマンド、または accept: 文章）/ ' +
  '## repos（対象リポジトリ）/ ## links（参考リンク）';

async function openEditFile(name, opts) {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const info = await guard('ファイル読込', () => api.readProjectFile(p.dir, name));
  if (!info) return;
  // seedContent: 新規 charter バージョン追加時に、前バージョンの内容を書きかけとして
  // 差し込む（openAddCharterVersion 参照）。まだファイルが無いときだけ使う＝既存ファイルの
  // 編集では絶対に上書きしない。
  const seeded = !info.exists && opts && opts.seedContent;
  state.editFile = { dir: p.dir, name, file: info.file, aiBackup: null };
  $('ef-title').textContent = `編集: ${info.label}`;
  $('ef-content').value = seeded ? opts.seedContent : info.content || '';
  const warn = $('ef-warning');
  if (info.generated) {
    warn.textContent =
      '⚠ この repos.json は charter.md の ## repos から自動生成されています（_meta.generated_from）。' +
      '直接編集しても run 時に charter から上書きされます。恒久的に手で管理するなら _meta を消すか、' +
      'charter の ## repos を編集してください。';
    warn.classList.remove('hidden');
  } else if (isCharterFile(name)) {
    warn.textContent = CHARTER_SECTION_GUIDE;
    warn.classList.remove('hidden');
  } else {
    warn.classList.add('hidden');
  }
  // charter だけに入力補助（雛形挿入・AI 補完）を出す
  $('ef-ai-row').classList.toggle('hidden', !isCharterFile(name));
  $('btn-ef-ai-undo').classList.add('hidden');
  $('ef-ai-status').textContent = '';
  $('btn-ef-ai').disabled = false;
  $('ef-hint').textContent = info.exists
    ? `${info.file}｜保存した内容は次回の自動実行から反映されます`
    : seeded
      ? `${info.file}（未作成 — 前バージョンの内容をコピーしています。保存すると新規作成します）`
      : `${info.file}（未作成 — 保存すると新規作成します）`;
  $('dlg-edit-file').showModal();
}

// charters/<name>.md のバージョン名として使える文字か（authoring.js の BAD_NAME_RE と揃える。
// スラッシュ等の path traversal はサーバ側 editablePath でも弾かれるが、ここで先に弾いて
// わかりやすいエラーにする）
const BAD_CHARTER_NAME_RE = /[\s/\\<>:"|?*-]/;
function isValidCharterVersionName(name) {
  return !!name && name !== '.' && name !== '..' && !BAD_CHARTER_NAME_RE.test(name);
}

// 既存プロジェクトに新しい charter バージョン（charters/<名前>.md）を追加する。
// 「新規プロジェクト作成」時にしか charter 名を指定できなかったギャップを埋める入口。
// 実体の作成は openEditFile → 保存（saveEditFile）が行う＝ここでは名前を確定するだけ。
// 注意: charters/*.md ができると agent-project は charter.md（初版）を駆動対象から外す。
// 初版がまだ charter.md 単体のときは、その旨と「⤴ バージョン化」の案内を説明文に出す。
async function openAddCharterVersion() {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const master = !!(p.charter && p.charter.master);
  const src = p.charters && p.charters.length ? '直近のバージョン' : 'マスター憲章';
  $('nc-title').textContent = '計画バージョンを追加';
  $('nc-desc').textContent = master
    ? `バージョン名を決めると、続けて内容を入力する画面が開きます（${src}の やること・完了条件・成果物 と、共通の制約・前提を引き継ぎます。すべてこのバージョン用に変更できます）。`
    : p.charter && !(p.charters && p.charters.length)
      ? '新しい計画バージョンを作成します。作成後はバージョン一覧の計画だけが実行され、' +
        '初版は実行の対象から外れます（概要タブの「⤴ バージョン名を付ける」で初版も並行して進められます）。'
      : `新しい計画バージョンを作成します（${src}の内容を引き継いだ状態でフォームが開きます。既存のバージョンはそのまま並行して進みます）。`;
  $('nc-name').value = '';
  $('dlg-new-charter').dataset.mode = 'add';
  $('dlg-new-charter').showModal();
  $('nc-name').focus();
}

async function submitNewCharterVersion() {
  const p = state.project;
  if (!p) return $('dlg-new-charter').close();
  const mode = $('dlg-new-charter').dataset.mode || 'add';
  const name = $('nc-name').value.trim();
  if (!isValidCharterVersionName(name)) {
    toast('バージョン名が不正です（空白・スラッシュ・ハイフン等は使えません）');
    return;
  }
  const existing = new Set((p.charters || []).map((c) => c.name));
  if (existing.has(name)) {
    toast(`バージョン「${name}」はすでに存在します`);
    return;
  }
  $('dlg-new-charter').close();
  if (mode === 'promote') {
    await submitPromoteCharter(name);
    return;
  }
  // 初期値の引き継ぎ元: 直近の計画バージョン（あれば）、無ければマスター/初版の憲章。
  // その やること/完了条件/成果物 をフォームの初期状態に入れて、前バージョンから編集して作れる
  // ようにする。制約・前提はここでコピーしない — マスターがあれば openCharterForm が
  // 「継承値の表示」として出し、変更しない限りマスターへの追従が保たれる（コピーすると
  // その時点のスナップショットで固定され、以後の共通設定の変更が伝わらなくなる）。
  const srcName =
    p.charters && p.charters.length ? `charters/${p.charters[p.charters.length - 1].name}.md` : 'charter.md';
  let seed = {};
  const src = await guard('引き継ぎ元の読込', () => api.readCharterFields(p.dir, srcName));
  if (src && src.fields) {
    seed = {
      seedGoal: src.fields.goal || '',
      seedAcceptance: Array.isArray(src.fields.acceptance) ? src.fields.acceptance : [],
      seedDeliverables: Array.isArray(src.fields.deliverables) ? src.fields.deliverables : [],
    };
  }
  // 名前を決めたら、続けて内容（やること・完了条件）を入力するバージョンのフォームを開く（保存で新規作成）
  await openCharterForm(`charters/${name}.md`, seed);
}

// charter.md の雛形を挿入する（空のときだけ即挿入。書きかけがあるときは確認してから置換）
async function insertCharterTemplate() {
  const ef = state.editFile;
  if (!ef) return;
  const current = $('ef-content').value;
  if (current.trim()) {
    const ok = await confirmDialog('編集中の内容を破棄して charter の雛形に置き換えます。よろしいですか？');
    if (!ok) return;
  }
  const m = /^charters\/([^/\\]+)\.md$/.exec(ef.name);
  const fallback = (state.project && state.project.name) || 'project';
  // バージョン（charters/<name>.md）の雛形は空の制約・前提見出しを持たない
  // （そのまま保存してもマスターの制約・前提を「空に上書き」しない）
  const res = await guard('雛形の取得', () => api.charterTemplate(m ? m[1] : fallback, !!m));
  if (!res) return;
  $('ef-content').value = res.content;
  $('ef-ai-status').textContent = '雛形を挿入しました — 各セクションを埋めるか、✨ AI 補完で下書きできます';
}

// エディタの charter 全文をエージェントに渡し、書式を保った完成版へ補完する。
// 置換のみでファイルには書かない（保存は人の「保存」ボタン）。補完前の内容は
// aiBackup に取り置き、「↩ 補完前に戻す」で戻せる。
async function aiRefineCharter() {
  const ef = state.editFile;
  if (!ef) return;
  const btn = $('btn-ef-ai');
  const status = $('ef-ai-status');
  const before = $('ef-content').value;
  btn.disabled = true;
  status.textContent = 'エージェントに問い合わせ中…（モデル応答まで数十秒かかることがあります）';
  try {
    const res = await api.agentCharter({ dir: ef.dir, mode: 'refine', content: before });
    ef.aiBackup = before;
    $('ef-content').value = res.content;
    $('btn-ef-ai-undo').classList.remove('hidden');
    status.textContent =
      `補完しました（${res.cli}${res.model ? ` / ${res.model}` : ''}）— 内容を確認して保存してください`;
  } catch (err) {
    status.textContent = '';
    toast(`AI 補完に失敗しました: ${err.message || err}`);
  } finally {
    btn.disabled = false;
  }
}

function undoAiRefine() {
  const ef = state.editFile;
  if (!ef || ef.aiBackup == null) return;
  $('ef-content').value = ef.aiBackup;
  ef.aiBackup = null;
  $('btn-ef-ai-undo').classList.add('hidden');
  $('ef-ai-status').textContent = '補完前の内容に戻しました';
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
    gitPushAfterWrite(`agent-dashboard: edit ${ef.name}`, ef.dir);
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
  } else if (n.kind === 'blocked' && canManuallyCompleteNeed(p, n, state.flowRuns)) {
    line = '成果生成は完了しています。検証失敗を確認・受容できる場合は、承認するとこのタスクを完了できます。';
  } else if (!line && n.kind === 'blocked') {
    line = '指示を送ると、作業を再開します。';
  }
  if (!line) return '';
  return `<div class="task-complete-banner need-complete-how">${esc(line)}</div>`;
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
    // verify 未定義の確認待ち: 承認 = done 確定（本体 cmd_approve が完了させる）。
    // 従来はこのボタンが無く、成果が揃った run を人の承認で完了にできなかった。
    const verifyPending = isVerifyPendingNeed(state.project, n);
    const manualCompletion = canManuallyCompleteNeed(state.project, n, state.flowRuns);
    const canApproveCompletion = verifyPending || manualCompletion;
    if (canApproveCompletion) {
      const title = manualCompletion
        ? '成果と検証失敗を確認・受容し、このタスクを完了（納品確定）にします'
        : '成果を確認済みならこのタスクを完了（納品確定）にします';
      if (manualCompletion && !inReview) {
        buttons.push(`<button type="button" class="primary-inline" data-need-artifacts="${esc(n.id)}" title="ファイル差分を確認してから承認します">差分を確認して承認</button>`);
      } else {
        buttons.push(`<button class="primary-inline" data-act="approve" data-id="${esc(n.id)}" title="${esc(title)}">承認して完了にする</button>`);
      }
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
  return `${needCompleteHowHtml(n)}<div class="need-actions" data-need="${esc(n.id)}">
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

function canManuallyCompleteNeed(project, need, flowRuns) {
  return Boolean(needFinalVerificationFailure(project, need, flowRuns));
}

function needApprovalReason(project, need, flowRuns, input) {
  const note = String(input || '').trim();
  if (!canManuallyCompleteNeed(project, need, flowRuns)) return note;
  return ['検証失敗を確認・受容して完了', note].filter(Boolean).join(': ');
}

function needArtifactsButtonHtml(project, need, flowRuns) {
  // 未承認の検証失敗は回答欄の主操作から同じ検収ダイアログへ進むため、重複表示しない。
  if (canManuallyCompleteNeed(project, need, flowRuns)) return '';
  // リトライ中（最新試行が未完）でも、系統内に done 世代があれば成果への導線を残す
  if (!completedTaskForNeed(project, need) && !artifactRunForNeed(project, need, flowRuns)) return '';
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
    (entry) => entry.role !== 'reference' && entry.path && (entry.ref || !entry.branch)
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
  return `<h3>要確認コメント・操作</h3>${
    need.decided || isNeedSent(need)
      ? '<p class="muted">この要確認項目には回答済みです。</p>'
      : needActionsHtml(need, { inReview: true })
  }`;
}

function deliveryRepoMetaHtml(entry) {
  // ブランチ名・パスは長くなりがちなので 1 項目 1 行で出す（中黒区切りの 1 行連結は読めない）
  const bits = [];
  if (entry.branch) bits.push(`作業ブランチ <code>${esc(entry.branch)}</code>`);
  if (entry.target || entry.base) bits.push(`ターゲット <code>${esc(entry.target || entry.base)}</code>`);
  if (entry.base) bits.push(`ベース <code>${esc(entry.base)}</code>`);
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
    entry.path && (entry.ref || !entry.branch) && entry.role !== 'reference'
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
    (entry) => entry.role !== 'reference' && entry.path && (entry.ref || !entry.branch)
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

function deliveryDiffRequest(entry, file = '') {
  return {
    repo: entry.path,
    base: entry.base || 'main',
    ref: entry.ref || undefined,
    file: file || undefined,
    workingTree: !entry.ref,
  };
}

function isDeliveryArtifactFile(file) {
  return !/(^|\/)\.agent-project\//.test(String(file || '').replace(/\\/g, '/'));
}

async function hydrateDeliveryEntries(entries) {
  return Promise.all((entries || []).map(async (entry) => {
    const fallbackFiles = (entry.files || []).filter(isDeliveryArtifactFile);
    const fallback = { ...entry, files: fallbackFiles, files_total: fallbackFiles.length };
    const canLoad = entry.role !== 'reference' && entry.path && (entry.ref || !entry.branch);
    if (!canLoad) return { ...fallback, discovery: 'unavailable' };
    try {
      const result = await api.gitDiff(deliveryDiffRequest(entry));
      const files = (result.files || []).filter(isDeliveryArtifactFile);
      return { ...entry, files, files_total: files.length, discovery: 'complete' };
    } catch (err) {
      uiLog('delivery file list fallback', entry.name || 'repo', err && err.message ? err.message : err);
      return { ...fallback, discovery: 'failed' };
    }
  }));
}

function deliveryDiffOutputFormat(viewportWidth) {
  return Number(viewportWidth) < 768 ? 'line-by-line' : 'side-by-side';
}

function renderDeliveryDiff(diffText) {
  const view = $('delivery-diff-view');
  view.replaceChildren();
  if (!diffText) {
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
  const outputFormat = deliveryDiffOutputFormat(window.innerWidth);
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
}

async function collectDeliveryDiffSections(need, { maxChars = 80000 } = {}) {
  const entries = (need.delivery || []).filter(
    (entry) => entry.role !== 'reference' && entry.path && (entry.ref || !entry.branch)
  );
  const sections = await Promise.all(
    entries.map(async (entry) => {
      const label = entry.ref
        ? `${entry.base || 'main'}...${entry.ref}`
        : '現在の作業ツリー（HEADとの差分）';
      const files = (entry.files || []).slice(0, 40);
      try {
        const res = await api.gitDiff(deliveryDiffRequest(entry));
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
      if (!entry.ref && entry.branch) return toast('作業ブランチの ref が未解決のため差分を取得できません');
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

function needFailureViewModel(need) {
  if (!need) return null;
  const prose = [need.failureSummary, need.why, need.detail].filter(Boolean).join('\n');
  if (/verify\s*未定義/i.test(prose)) return null;
  const context = need.failureContext || null;
  const hasFailureSignal = Boolean(
    need.failureSummary ||
    context ||
    /(?:検証|verify|テスト|test|回帰|コマンド)[^\n]*(?:失敗|FAIL|NG|exit\s*=\s*[1-9]\d*)/i.test(prose)
  );
  if (!hasFailureSignal) return null;
  const exitCode = context && String(context.exitCode || '').trim();
  return {
    summary: String(
      need.failureSummary ||
      (exitCode
        ? `検証コマンドが失敗しました（終了コード ${exitCode}）。`
        : '検証コマンドが失敗しました。')
    ),
    resolution: String(need.failureResolution || ''),
    context,
  };
}

function canDiagnoseNeed(need) {
  return Boolean(needFailureViewModel(need));
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

function renderNeedDetail(p, n) {
  if (!n) return '<div class="empty need-detail-empty">この状態の項目はありません</div>';
  const settled = n.decided || isNeedSent(n);
  const chip = n.decided
    ? '<span class="status-chip st-done">回答済み</span>'
    : isNeedSent(n)
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
          ${riskBadgeHtml(n)} ${chip}${unsettle}
        </div>
        <h2>${esc(needDisplayTitle(n))}</h2>
      </div>
      <span class="muted">${esc(n.date || '')}</span>
    </header>
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
  </article>`;
}

function bindNeedDetail(root) {
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
    p.needs.map((n) => [n.id, n.kind, n.decided, isNeedSent(n), n.why, n.summary, n.risk, n.failureSummary || '', n.failureResolution || '', n.failureContext || null, (n.detail || '').length]),
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
      const ageBadge = age && age.label
        ? `<span class="need-age ${age.level}" title="最終更新からの経過時間（SLA ${slaHours}h 超で赤）">${esc(age.label)}</span>`
        : '';
      return `<button class="need-list-item ${selected ? 'selected' : ''}" data-need-select="${esc(n.id)}"
        aria-pressed="${selected}"${selected ? ' aria-current="true"' : ''}>
        <span class="need-list-meta">
          <span class="badge">${esc(needKindLabel(n.kind))}</span>
          ${riskBadgeHtml(n)}
          ${ageBadge}
        </span>
        <strong>${esc(needDisplayTitle(n))}</strong>
        <span class="need-list-summary ${needFailureViewModel(n) ? 'failure' : ''}">${esc(needListSummary(n))}</span>
      </button>`;
    })
    .join('');

  const gitlab = state.needsFilter === 'gitlab'
    ? '<div class="queue-single"><div id="needs-gitlab"></div></div>'
    : `<div class="master-detail ${state.needsMobileDetail ? 'show-detail' : ''}">
        <aside class="master-list" aria-label="要対応一覧">
          ${list || '<div class="empty">この状態の項目はありません</div>'}
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
      await api.submitFeedback(need.file, '', feedbackStub);
      toast('そのまま再実行するよう回答しました', true);
    } else if (act === 'approve') {
      const reason = needApprovalReason(p, need, state.flowRuns, text);
      const res = await api.runAction({ dir: p.dir, action: 'approve', id, reason });
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
  const tri = /\[agent-error:(quota|auth|env)\]/.exec(String(run.failureReason || ''));
  if (!tri) return null;
  const map = {
    quota: ['⏲ 利用上限', 'AI の利用上限に達したため止まりました。時間をおく（またはプランを' +
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
  // 「次に何が起きるか・あなたの出番はあるか」を最上部で言い切る（runAdvice）。
  // 状態チップ・応答なしバッジの読み解きを人に要求しない。
  const group = lineageGroups(state.flowRuns).find((g) =>
    g.attempts.some((a) => a.runId === run.runId));
  const advice = runAdvice(run, group);
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
  const doneCount = (run.counts && run.counts.done) || 0;
  const failedCount = (run.counts && run.counts.failed) || 0;
  // 停滞（orchestrator が消えて非終端のまま止まった run）も、失敗と同じくやり直せる。
  // status だけを見ると救えない: orchestrator が落ちると run は status=running のまま残り、
  // 失敗ノードも pending ノードも誰も進めない（実際 25 ノード中 14 done / 1 failed のまま
  // 「実行中」に見え続け、やり直しボタンが出なかった）。生存リース（alive）で実態を見る。
  // 上の `stalled` は「応答なし」バッジ用の HTML 断片。ここでは判定そのものを使う。
  const isStalled = run.alive === false && run.status !== 'done';
  const canRetry = run.status === 'failed' || run.status === 'canceled' || isStalled;
  const remainCount =
    failedCount +
    ((run.counts && run.counts.pending) || 0) +
    ((run.counts && run.counts.waiting) || 0) +
    ((run.counts && run.counts.parked) || 0);
  // canceled は続きから再開できない（agent-project は新 run を作る）。部分やり直し表記を出さない。
  const partial = canRetry && doneCount > 0 && run.status !== 'canceled';
  const resubmitLabel = run.status === 'canceled'
    ? '↻ 新しくやり直す'
    : partial
    ? `↻ 失敗した工程だけやり直す（残り ${remainCount} 件）`
    : '↻ 同じ内容でやり直す';
  const resubmitTitle = run.status === 'canceled'
    ? '中止した実行の続きからは再開できません。タスクを積み直して新しい実行を始めます'
    : partial
    ? `失敗・未実行の工程だけを実行し直します。成功した ${doneCount} 件はそのまま使います（作り直しません）`
      + (advice.kind === 'auto' ? '\n※ 放置しても本体が自動で同じことをします（このボタンは前倒し指示）' : '')
    : '同じ内容でやり直します（タスクを積み直して本体に実行させます）';
  // ボタンの出し分けも advice に従う:
  //  - human（判断待ち）: 出さない。ここで積み直すと人の判断ゲートを素通りしてしまう
  //    （正しい導線は要対応タブ — バナーのボタンが誘導する）
  //  - old（古い試行）: 出さない。最新の試行側で操作する
  //  - manual/restart: 主要操作として強調 ／ auto: 通常表示（押さなくてもよい）
  const showResubmit = !archived && canRetry && !['human', 'old'].includes(advice.kind);
  const resubmit = showResubmit
    ? `<button class="chip ${['manual', 'restart'].includes(advice.kind) ? 'primary-inline' : ''}"
        id="flow-resubmit" title="${esc(resubmitTitle)}">${esc(resubmitLabel)}</button>`
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
function nodeFateLine(run, effState) {
  const runStopped =
    TERMINAL_RUN_STATES.has(String(run.status)) || (run.alive === false && run.status !== 'done');
  if (!runStopped || run.archived) return '';
  const msg =
    effState === 'failed'
      ? '⟳ この工程は「↻ 失敗した工程だけやり直す」で<b>必ず再実行されます</b>' +
        '（この工程だけの単体再実行はありません。完了済みの工程は作り直されません）'
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
    (reconciled ? ' <span class="status-chip st-reconciled" title="GitLab 側の決着を先に表示しています（正式な反映待ち）">GitLab 反映</span>' : '');
  return `<div class="card full">
      <h3><span class="mono">${esc(node.id)}</span> [${esc(node.kind)}] — ${stateLabel}${node.who ? ` @${esc(node.who)}` : ''}</h3>
      <div class="node-goal">${proseHtml(node.goal) || '<span class="muted">（目標なし）</span>'}</div>
      ${node.deps.length ? `<div class="muted" style="margin-top:4px">依存: ${node.deps.map(esc).join(', ')}</div>` : ''}
      ${nodeFateLine(run, effState)}
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

// ---------------------------------------------------------------------------
// タブ: レビュー待ち（charter repos のオープンイシュー）
// ---------------------------------------------------------------------------
// プロジェクトが扱うリポジトリ（repos.json）の「いまレビュー待ち・作業中のイシュー」を
// GitLab API で横断一覧し、gitlab-review-viewer へ引き継ぐ入口。bus に依存しないため
// agent-flow が起票したもの以外（人が直接立てたイシュー）も見える。
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
  // 要対応タブ内の併載コンテナへ描く（renderNeeds が先に描画してから呼ばれる前提。
  // レビュー待ちの独立タブは要対応へ統合した）。
  const el = $('needs-gitlab');
  if (!el) return;
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
        title="この工程をフロー画面で開く">⚙ ${esc(shortRunId(rel.runId))} ▸ ${esc(rel.nodeId)}</button>`;
    }
    if (it.taskToken) {
      return `<span class="muted" title="対応する実行が見つかりません（一覧の範囲外か、削除済みの可能性があります）">—</span>`;
    }
    return '<span class="muted" title="自動実行が作成したイシューではありません"></span>';
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

  // agent-flow 由来（gitlab executor が起票 = 本文に task-token マーカー）だけに絞る。
  // 人が直接立てたイシューも見たいときはチップで解除できる
  const flowOnly = gl.flowOnly !== false;
  const shown = flowOnly ? gl.repoIssues.filter((it) => it.kiroFlow) : gl.repoIssues;
  const hiddenCount = gl.repoIssues.length - shown.length;

  const repoIssuesSection = shown.length
    ? `<table class="list"><tr><th>IID</th><th>イシュー</th><th>状態</th><th>ラベル</th><th>関連 MR</th><th>関連する実行</th><th></th></tr>
        ${shown.map((it) => issueRow(it)).join('')}</table>`
    : `<div class="muted">${
        gl.enabled === false
          ? '⚙ 設定で GitLab の URL とトークンを設定すると、対象リポジトリのオープンイシューを一覧できます'
          : !repos.length
            ? '対象リポジトリが未定義です（プロジェクト憲章の「対象リポジトリ」で定義します）'
            : flowOnly && hiddenCount
              ? `自動実行が作成したレビュー待ちはありません（フィルタを解除すると ${hiddenCount} 件表示されます）`
              : 'レビュー待ちのイシューはありません'
      }</div>`;

  el.innerHTML = `
    <div class="toolbar">
      <span class="muted">対象リポジトリのオープンイシュー。「関連する実行」列から作業の元をフロー画面で開けます</span>
      <span class="spacer"></span>
      <button id="btn-gl-flowonly" class="chip ${flowOnly ? 'active' : ''}"
        title="自動実行が作成したイシューだけに絞ります（人が直接立てたものを隠します）">自動実行によるもののみ</button>
      <button id="btn-gl-refresh" ${gl.loading ? 'disabled' : ''}>${gl.loading ? '取得中…' : 'GitLab から最新化'}</button>
    </div>
    <div class="muted" style="margin-bottom:4px">${[...new Set(repos.map((r) => r.projectPath))]
      .map((path) => `<span class="label-chip">${esc(path)}</span>`)
      .join('')}
      ${flowOnly && hiddenCount ? `<span class="muted">（自動実行によるもの以外 ${hiddenCount} 件を非表示）</span>` : ''}</div>
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
    const needs = $('tab-needs');
    if (needs) needs.dataset.sig = '';
    renderNeeds();
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
  const hasDecisions = p.decisions.length > 0;
  const deliveryRows = [...p.delivery]
    .reverse()
    .map((cells) => `<tr>${cells.map((c) => `<td>${linkify(c)}</td>`).join('')}</tr>`)
    .join('');

  el.innerHTML = `<div class="history-shell">
    <header class="page-heading">
      <div><span class="summary-kicker">完了したこと</span><h2>成果</h2></div>
      <button type="button" class="subtle-action" data-open-technical-info>内部ログを開く</button>
    </header>
    <section class="content-section">
      <h3>納品物</h3>
      ${deliveryRows ? `<div class="table-scroll"><table class="list">${deliveryRows}</table></div>` : '<div class="empty compact">まだ成果はありません。</div>'}
    </section>
    <section class="content-section">
      <h3>判断の記録</h3>
      ${hasDecisions
        ? `<div class="table-scroll"><table class="list"><tr><th>日付</th><th>タスク</th><th>操作</th><th>理由</th></tr>${p.decisions.map((d) => `<tr><td>${esc(d.date)}</td><td class="mono">${esc(d.taskId)}</td><td>${esc(d.fields.action || '')}</td><td>${esc(d.fields.reason || d.fields.context || '')}</td></tr>`).join('')}</table></div>`
        : '<div class="empty compact">判断の記録はありません。</div>'}
    </section>
  </div>`;
  const technicalInfo = el.querySelector('[data-open-technical-info]');
  if (technicalInfo) technicalInfo.addEventListener('click', openTechnicalInfo);
}

// ---------------------------------------------------------------------------
// タブ制御・設定・ポーリング
// ---------------------------------------------------------------------------

// 再描画（ポーリング・操作後のリロード）は各タブの innerHTML を作り直すため、素のままでは
// スクロール位置と <details> の開閉が毎回初期化されてしまう。描画前に id 付きスクロール要素の
// 位置と data-ui-key 付き <details> の開閉を控え、描画後に復元する（存在しなくなった要素は無視）。
function captureUiState() {
  const scroll = {};
  for (const el of document.querySelectorAll('.tabpane, #tree, #flow-runs, #flow-view-body, #graph-box')) {
    if (el.id) scroll[el.id] = { top: el.scrollTop, left: el.scrollLeft };
  }
  const open = [];
  for (const d of document.querySelectorAll('details[data-ui-key]')) {
    if (d.open) open.push(d.dataset.uiKey);
  }
  return { scroll, open: new Set(open) };
}

function restoreUiState(ui) {
  if (!ui) return;
  for (const [id, pos] of Object.entries(ui.scroll)) {
    const el = document.getElementById(id);
    if (el) {
      el.scrollTop = pos.top;
      el.scrollLeft = pos.left;
    }
  }
  for (const d of document.querySelectorAll('details[data-ui-key]')) {
    if (ui.open.has(d.dataset.uiKey)) d.open = true;
  }
}

function renderAllTabs() {
  const ui = captureUiState();
  renderOverview();
  renderBacklog();
  renderNeeds();
  renderFlow();
  renderGitLab();
  renderHistory();
  renderCowork();
  renderAmigos();
  renderKiroLoopTerminal();
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
      if (tab.dataset.tab === 'kiro-loop') startKiroLoopCapturePoll();
      else stopKiroLoopCapturePoll();
    });
  }
}

function populateSettingsFields() {
  const cfg = state.config;
  $('cfg-roots').value = ((cfg.projects && cfg.projects.roots) || []).join('\n');
  $('cfg-autodiscover').checked = !cfg.projects || cfg.projects.autoDiscover !== false;
  $('cfg-refresh').value = cfg.projects ? cfg.projects.refreshSec : 5;
  $('cfg-git-pull').value = cfg.projects && cfg.projects.gitPullSec !== undefined ? cfg.projects.gitPullSec : 300;
  $('cfg-git-autopush').checked = !!(cfg.projects && cfg.projects.gitAutoPush);
  $('cfg-notify').checked = !(cfg.notifications && cfg.notifications.enabled === false);
  $('cfg-needs-sla').value = cfg.projects && cfg.projects.needsSlaHours !== undefined ? cfg.projects.needsSlaHours : 24;
  $('cfg-project-command').value = (cfg.projects && cfg.projects.command) || 'agent-project';
  $('cfg-action-mode').value = (cfg.projects && cfg.projects.actionMode) || 'auto';
  $('cfg-flow-bus').value = (cfg.projects && cfg.projects.flowBus) || '';
  $('cfg-flow-lockdir').value = (cfg.projects && cfg.projects.flowLockDir) || '';
  $('cfg-flow-bus-by-project').value = Object.entries(
    (cfg.projects && cfg.projects.flowBusByProject) || {}
  )
    .map(([name, bus]) => `${name} = ${bus}`)
    .join('\n');
  $('cfg-agent-cli').value = (cfg.agent && cfg.agent.cli) || 'kiro';
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

function openSettings() {
  populateSettingsFields();
  $('dlg-settings').showModal();
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

function technicalProjectInfoHtml() {
  const p = state.project;
  if (!p) {
    return '<div class="empty compact">プロジェクトを選ぶと、実行状態とログをここで確認できます。</div>';
  }
  const run = state.flowRun && state.flowRun.run;
  const journal = (p.journal || []).slice(-80).reverse().map((line) => `<div>${linkify(line.replace(/^\-\s*/, ''))}</div>`).join('');
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

function openAdvancedSettings() {
  populateSettingsFields();
  renderAdvancedBudgetSettings();
  $('dlg-advanced-settings').showModal();
}

function openTechnicalInfo() {
  $('technical-project-info').innerHTML = technicalProjectInfoHtml();
  for (const btn of $('technical-project-info').querySelectorAll('[data-technical-tab]')) {
    btn.addEventListener('click', () => {
      $('dlg-technical-info').close();
      switchTab(btn.dataset.technicalTab);
    });
  }
  $('dlg-technical-info').showModal();
}

async function saveSettings() {
  const cfg = state.config;
  cfg.projects = cfg.projects || {};
  cfg.projects.roots = $('cfg-roots')
    .value.split('\n')
    .map((s) => s.trim())
    .filter(Boolean);
  cfg.projects.autoDiscover = $('cfg-autodiscover').checked;
  cfg.projects.refreshSec = Math.max(0, parseInt($('cfg-refresh').value, 10) || 0);
  cfg.projects.gitPullSec = Math.max(0, parseInt($('cfg-git-pull').value, 10) || 0);
  cfg.projects.gitAutoPush = $('cfg-git-autopush').checked;
  cfg.notifications = cfg.notifications || {};
  cfg.notifications.enabled = $('cfg-notify').checked;
  cfg.projects.needsSlaHours = Math.max(1, parseInt($('cfg-needs-sla').value, 10) || 24);
  cfg.projects.command = $('cfg-project-command').value.trim() || 'agent-project';
  cfg.projects.actionMode = $('cfg-action-mode').value;
  cfg.projects.flowBus = $('cfg-flow-bus').value.trim();
  cfg.projects.flowLockDir = $('cfg-flow-lockdir').value.trim();
  // 1 行 1 件「プロジェクト名 = バスパス」を写像へ。空行・不正行は無視する。
  cfg.projects.flowBusByProject = $('cfg-flow-bus-by-project')
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
  cfg.agent = cfg.agent || {};
  cfg.agent.cli = $('cfg-agent-cli').value;
  cfg.agent.model = $('cfg-agent-model').value.trim();
  cfg.agent.timeoutSec = Math.max(30, parseInt($('cfg-agent-timeout').value, 10) || 180);
  cfg.gitlab.baseUrl = $('cfg-gl-url').value.trim();
  cfg.gitlab.token = $('cfg-gl-token').value.trim();
  cfg.reviewViewer.mode = $('cfg-rv-mode').value;
  cfg.reviewViewer.exePath = $('cfg-rv-exepath').value.trim();
  cfg.reviewViewer.command = $('cfg-rv-command').value.trim();
  cfg.cowork = cfg.cowork || {};
  cfg.cowork.loopProvider = $('cfg-cowork-loop-provider').value.trim() || 'kiro-loop';
  cfg.cowork.loopCommand = $('cfg-cowork-loop-command').value.trim() || cfg.cowork.loopProvider;
  cfg.cowork.stateMachineCommand = $('cfg-cowork-sm-command').value.trim() || 'statemachine-use';
  state.config = await api.saveConfig(cfg);
  setupPolling();
  await refreshAll();
  if ($('dlg-settings').open) $('dlg-settings').close();
  if ($('dlg-advanced-settings').open) $('dlg-advanced-settings').close();
  if ($('dlg-technical-info').open) $('dlg-technical-info').close();
  toast('設定を保存しました', true);
}

// ---------------------------------------------------------------------------
// git pull（選択中プロジェクトのリポジトリ最新化）
// ---------------------------------------------------------------------------
// 自動: ポーリングのたびに呼ぶが、実際の pull は main 側が設定間隔（下限 60 秒）で
// スロットリングする（リモートサーバへ負荷をかけない）。git リポジトリでない
// プロジェクトは黙ってスキップされる。エラーは同じ内容を繰り返しトーストしない。
let lastGitPullError = null;

// 状態同期の pull 先は project.dir（状態 worktree）。selectedDir＝登録ワークスペースだけ
// 引くと、agent-state 側の backlog/commands/bus が更新されず、リモートの指示・進捗が
// 画面に反映されない。
function gitStateDir() {
  return (state.project && state.project.dir) || state.selectedDir;
}

async function maybeAutoGitPull() {
  const sec = state.config && state.config.projects ? Number(state.config.projects.gitPullSec) : 0;
  const dir = gitStateDir();
  if (!sec || !dir) return;
  try {
    const res = await api.gitPull(dir, false);
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
  // 仕組みの詳細（git 作業ツリーでない・state_git 同期・設定の対処法）はログへ
  uiLog('pushSkipped', {
    dir,
    kind,
    reason: 'git 作業ツリーでないため viewer から直接 push できない（本体の state_git 同期に委ねる）',
    hint:
      kind === 'bus'
        ? '⚙ 設定 flowBusByProject でバスの git クローンを登録すると直接反映できます'
        : '状態共有リポジトリの git クローン上でプロジェクトを開くと直接反映できます',
  });
  toast(
    '変更は保存しましたが、この画面から共有先へは直接反映できないため、本体の同期に任せます。' +
      '（詳細は開発者ログを参照。この通知はプロジェクトごとに一度だけ出ます）'
  );
}

// 管理ファイルを書き換えた操作（指示ドロップ・inbox 投入・needs 記入・削除など）の後に呼ぶ。
// 設定 gitAutoPush が有効なら、操作したディレクトリの変更をコミットして push する
// （状態共有 git への都度反映）。書き込み本体は成功済みなので待たずに走らせ、失敗（push 不可）や
// notRepo による「黙ってスキップ」だけトーストで知らせる（後者はディレクトリごとに一度だけ）。
// 戻り値は commitPush の結果 Promise（gitAutoPush 無効/対象なしのときは null）。
// opts.kind は notRepo 通知の対処ヒント切り替え用（'bus'（バス）／既定 'project'）。
// opts.paths は「操作が触ったパス（dir 相対）」の限定コミット（bus 操作で必須 —
// 全体スナップショットを commit すると本体の state 同期と同じファイルを取り合う）。
function gitPushAfterWrite(message, dir, opts) {
  const cfg = state.config;
  if (!cfg || !cfg.projects || !cfg.projects.gitAutoPush) return null;
  const target = dir || state.selectedDir;
  if (!target) return null;
  const kind = (opts && opts.kind) || 'project';
  return api
    .gitCommitPush(target, message, (opts && opts.paths) || null)
    .then((res) => {
      if (res && res.skipped && res.notRepo) warnPushSkipped(target, kind);
      return res;
    })
    .catch((err) => {
      toast(`git 同期（プッシュ）: ${err.message || err}`);
      return null;
    });
}

// バス操作（run の削除・再投入・中止）の git 反映。バスは agent-project の state 同期が
// 鏡写しする（bus は同期対象・claims だけ除外）ため、busDir が git 作業ツリーでなければ
// notRepo で黙ってスキップして本体の同期に委ねる。notRepo 通知は gitPushAfterWrite が
// バス向けのヒント付きで出す（ここは busDir を対象にするだけ）。
// paths（busDir 相対）で「操作が触った場所」だけを反映する。省略すると bus 全体の
// スナップショットがコミットされ、本体が鏡写しする run の揮発ファイル（meta / claims /
// events）を取り合って履歴の食い違いを量産する（実運用で発生した）。
function gitPushBusOp(message, paths) {
  const busDir = state.project && state.project.busDir;
  return gitPushAfterWrite(message, busDir, { kind: 'bus', paths });
}

// 同期状態の横に必要な場合だけ出す操作。取り込み・履歴修復・送信を一つにまとめる。
// まとめて自動修復し、やったことを平易な文で知らせる。force push はせず人の作業は壊さない
async function manualGitHeal() {
  const healDir = gitStateDir();
  if (!healDir) return toast('プロジェクトを選択してください');
  const res = await guard('共有先との同期', () => api.gitHeal(healDir));
  if (!res) return;
  uiLog('gitHeal', res);
  const steps = (res.steps || []).join(' → ');
  toast(`${res.summary}${steps ? `（${steps}）` : ''}`, res.level !== 'error');
  await refreshAll({ sync: false });
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
  toast(filled ? '差し戻し文面を回答欄へ入れました（送信は人が確定します）' : '下書きを保存しました（回答欄を開くと入ります）', true);
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

async function refreshAll({ sync = true } = {}) {
  if (state.busy) return;
  state.busy = true;
  try {
    if (sync) await maybeAutoGitPull();
    await refreshDiscovery();
    // Cowork は軽量 overview（ログ推定のみ・発見キャッシュ利用）。重いプロセス探査は実行直後/手動更新のみ。
    await refreshCowork();
    await refreshAmigos();
    if (state.selectedDir) await reloadProject({ refreshRemoteHealth: sync });
    if (activeTab() === 'cowork') renderCowork();
    if (activeTab() === 'amigos') renderAmigos();
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
        $('dlg-settings').open ||
        $('dlg-advanced-settings').open ||
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
  // overview 未取得時のみ設定の手動項目にフォールバック。
  if (!state.coworkDraft) {
    const merged = (state.cowork && Array.isArray(state.cowork.items)) ? state.cowork.items : null;
    state.coworkDraft = (merged || configuredCoworkItems()).map((x) => ({ ...x }));
  }
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

// 作業（発見 or 手動）が無いときは Cowork タブを隠す。設定から明示オープン中は例外。
function updateCoworkTabVisibility() {
  const btn = $('tab-btn-cowork');
  const pane = $('tab-cowork');
  if (!btn || !pane) return;
  const folder = selectedProjectFolder();
  const coworkAvailable = coworkHasProjectConfig(state.cowork, folder);
  const features = workspaceFeatureModel(state.discovery, state.selectedDir, coworkAvailable ? 1 : 0);
  for (const el of document.querySelectorAll('.tab[data-feature="agent-project"], .tabpane[data-feature="agent-project"]')) {
    el.classList.toggle('hidden', !features.agentProject);
    el.hidden = !features.agentProject;
  }
  btn.classList.toggle('hidden', !features.cowork);
  btn.hidden = !features.cowork;
  pane.classList.toggle('hidden', !features.cowork);
  pane.hidden = !features.cowork;
  const current = document.querySelector('.tab.active');
  if (!current || current.hidden || current.classList.contains('hidden')) {
    if (features.defaultTab) switchTab(features.defaultTab);
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

// ---------------------------------------------------------------------------
// ミッションタブ（agent-amigos ミッションの読み取りビュー）
// ---------------------------------------------------------------------------

async function refreshAmigos() {
  if (!api.amigosOverview) return;
  try {
    state.amigos = await api.amigosOverview();
  } catch (err) {
    state.amigos = { error: err.message, missions: [], budget: null, errors: [] };
  }
  updateAmigosTabVisibility();
}

// agent-amigos の設定ホームとミッションを、現在選択しているプロジェクトだけに限定する。
// home が無いミッションは全体バス由来で所属を確定できないため表示しない。
function amigosForProject(amigos, projectFolder) {
  const source = amigos || {};
  const key = coworkPathKey(projectFolder);
  if (!key) return { ...source, homes: [], missions: [], errors: [] };
  const homes = (source.homes || []).filter(
    (home) => !!home.configFile && coworkPathKey(home.dir) === key
  );
  const allowed = new Set(homes.map((home) => coworkPathKey(home.dir)));
  const missions = (source.missions || []).filter((mission) => allowed.has(coworkPathKey(mission.home)));
  return { ...source, homes, missions, errors: [] };
}

// ミッションも依頼先ホームも無いときはタブを隠す（cowork と同じ流儀）。
function updateAmigosTabVisibility() {
  const btn = $('tab-btn-amigos');
  const pane = $('tab-amigos');
  if (!btn || !pane) return;
  const a = amigosForProject(state.amigos, selectedProjectFolder());
  const show = !!(
    a &&
    ((a.missions && a.missions.length) || (a.homes && a.homes.length))
  );
  btn.classList.toggle('hidden', !show);
  btn.hidden = !show;
  pane.classList.toggle('hidden', !show);
  pane.hidden = !show;
  if (!show && btn.classList.contains('active')) {
    const fallback = [...document.querySelectorAll('.tab')]
      .find((tab) => tab !== btn && !tab.hidden && !tab.classList.contains('hidden'));
    if (fallback) switchTab(fallback.dataset.tab);
  }
}

function amigosMin(sec) {
  return (Number(sec || 0) / 60).toFixed(1);
}

function amigosPhaseLabel(phase) {
  return (
    {
      open: '担当者を募集中',
      working: '作業中',
      integrating: '成果をまとめています',
      reviewing: '確認待ち',
      done: '完了',
      failed: '要確認',
      cancelled: '中止',
    }[phase] || phase
  );
}

function amigosWorkloadLabel(wl) {
  return (
    { routine: '定常業務', project: 'プロジェクト', flow: 'フロー', amigos: 'ミッション' }[wl] || wl
  );
}

function amigosBudgetPanelHtml(budget) {
  if (!budget) return '';
  const cfg = budget.config || { execution_minutes: 0, period: 'day', workloads: {} };
  const workloads = [...new Set([...(budget.knownWorkloads || []), ...Object.keys(budget.totals || {})])];
  const periodLabel = { day: '今日', month: '今月', total: '累計' }[cfg.period] || cfg.period;
  const limitTxt = cfg.execution_minutes > 0 ? `${amigosMin(budget.limitSeconds)}分` : '無制限';
  const rows = workloads
    .map((wl) => {
      const spent = amigosMin((budget.totals || {})[wl]);
      const lim = Number((cfg.workloads || {})[wl] || 0);
      const over = (budget.exceededWorkloads || []).includes(wl);
      return `<tr${over ? ' class="amigos-over"' : ''}>
        <td>${esc(amigosWorkloadLabel(wl))}</td>
        <td class="num mono">${esc(spent)} 分</td>
        <td><input type="number" min="0" step="1" class="mono amigos-wl-limit" data-wl="${esc(wl)}"
             value="${lim || 0}" title="0 = 無制限" /></td>
        <td class="muted">${over ? '超過中' : lim > 0 ? '' : '無制限'}</td>
      </tr>`;
    })
    .join('');
  return `
    <section class="amigos-budget">
      <header class="row">
        <div>
          <span class="summary-kicker">ノード予算</span>
          <h3>このノードの実行時間（${esc(periodLabel)}: 合計 ${esc(amigosMin(budget.totalSeconds))} 分 / 上限 ${esc(limitTxt)}
            ${budget.exceeded ? '<span class="amigos-over-badge">超過中 — ミッションの担当は一時停止</span>' : ''}</h3>
          <p class="muted">定常業務・プロジェクト・フロー・ミッションの合計に上限を掛けます（0 = 無制限）。
            設定は依頼側・請負側どちらのノードでも同じ契約（${esc(budget.dir)}）です。</p>
        </div>
      </header>
      <div class="row amigos-budget-controls">
        <label>合計上限（分）
          <input type="number" min="0" step="1" id="amigos-budget-total" class="mono"
            value="${cfg.execution_minutes || 0}" title="0 = 無制限" />
        </label>
        <label>期間
          <select id="amigos-budget-period">
            <option value="day" ${cfg.period === 'day' ? 'selected' : ''}>日次（day）</option>
            <option value="month" ${cfg.period === 'month' ? 'selected' : ''}>月次（month）</option>
            <option value="total" ${cfg.period === 'total' ? 'selected' : ''}>累計（total）</option>
          </select>
        </label>
        <button type="button" id="btn-amigos-budget-save" ${state.amigosBudgetSaving ? 'disabled' : ''}>上限を保存</button>
      </div>
      <table class="amigos-table">
        <thead><tr><th>ワークロード</th><th>消費</th><th>内訳上限（分・0=無制限）</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </section>`;
}

function setupAmigosBudgetSave(root) {
  const saveBtn = root && root.querySelector('#btn-amigos-budget-save');
  if (!saveBtn) return;
  saveBtn.addEventListener('click', () =>
    guard('ノード予算の保存', async () => {
      const workloads = {};
      for (const input of root.querySelectorAll('.amigos-wl-limit')) {
        workloads[input.dataset.wl] = Number(input.value || 0);
      }
      state.amigosBudgetSaving = true;
      try {
        const budget = await api.amigosBudgetSave({
          executionMinutes: Number((root.querySelector('#amigos-budget-total') || {}).value || 0),
          period: (root.querySelector('#amigos-budget-period') || {}).value || 'day',
          workloads,
        });
        state.amigos = { ...(state.amigos || {}), budget };
        toast('ノード予算を保存しました', true);
      } finally {
        state.amigosBudgetSaving = false;
      }
      renderAdvancedBudgetSettings();
    })
  );
}

function renderAdvancedBudgetSettings() {
  const el = $('advanced-budget-body');
  if (!el) return;
  const amigos = state.amigos;
  if (amigos && amigos.error) {
    el.innerHTML = `<p class="muted">予算情報を読み込めませんでした: ${esc(amigos.error)}</p>`;
    return;
  }
  if (!amigos || !amigos.budget) {
    el.innerHTML = '<p class="muted">予算情報を読み込み中です。</p>';
    return;
  }
  el.innerHTML = amigosBudgetPanelHtml(amigos.budget);
  setupAmigosBudgetSave(el);
}

function amigosNextStep(m) {
  return (
    {
      open: '担当者が揃うと作業を開始します。',
      working: '担当メンバーが作業を進めています。',
      integrating: '各担当の成果をひとつにまとめています。',
      reviewing: '成果の確認を待っています。',
      done: 'このミッションは完了しました。',
      failed: '作業を続けるために確認が必要です。',
      cancelled: 'このミッションは中止されました。',
    }[m.phase] || '状況を確認しています。'
  );
}

function amigosMemberStatus(role) {
  if (role.done) return '完了';
  if (!role.node) return '参加待ち';
  if (role.state === 'paused') return '一時停止';
  if (role.state === 'idle') return '待機中';
  return '作業中';
}

function amigosFriendlyNote(note) {
  return String(note || '')
    .replace(/\[node-budget\]\s*/gi, '利用時間の上限に達したため、')
    .replace(/\bamigo\b/gi, '担当エージェント')
    .trim();
}

function amigosMessageTypeLabel(type) {
  return (
    {
      question: '質問',
      answer: '回答',
      request: '依頼',
      review: '確認結果',
      'decision-request': '判断のお願い',
      status: '進捗共有',
      info: '共有',
      'wrap-up': 'まとめ',
      approve: '確認完了',
      feedback: '修正依頼',
    }[type] || '共有'
  );
}

function amigosMissionProgress(m) {
  const members = m.roles || [];
  const done = members.filter((r) => r.done).length;
  return { done, total: members.length };
}

function amigosMissionAttention(m) {
  const paused = (m.roles || []).filter((r) => r.state === 'paused').length;
  const count = Number(m.attentionCount || 0) + paused;
  if (!count) return '';
  return `<div class="amigos-attention" role="status">確認が必要な項目が ${count} 件あります</div>`;
}

function amigosMissionCardHtml(m) {
  const progress = amigosMissionProgress(m);
  const progressText = progress.total
    ? `${progress.total}人中${progress.done}人が完了`
    : '担当者を確認中';
  const goal = m.goal ? `<p class="amigos-card-goal">${esc(m.goal)}</p>` : '';
  return `<article class="amigos-mission-card">
    <div class="amigos-card-heading">
      <div>
        <span class="amigos-phase amigos-phase-${esc(m.phase)}">${esc(amigosPhaseLabel(m.phase))}</span>
        <h3>${esc(m.title)}</h3>
      </div>
    </div>
    ${goal}
    <p class="amigos-next-step">${esc(amigosNextStep(m))}</p>
    <div class="amigos-card-meta">
      <span>${esc(progressText)}</span>
      ${(m.messages || []).length ? `<span>やりとり ${(m.messages || []).length} 件</span>` : '<span>やりとりはまだありません</span>'}
    </div>
    ${amigosMissionAttention(m)}
    <div class="amigos-card-actions">
      <button type="button" class="primary-inline" data-amigos-detail="${esc(m.id)}">詳しく見る</button>
    </div>
  </article>`;
}

function amigosMemberHtml(role, mission) {
  const active = !['done', 'failed', 'cancelled'].includes(mission.phase);
  const claim = !role.node && mission.home && active
    ? `<button type="button" class="amigos-claim-btn" data-home="${esc(mission.home)}"
        data-mission="${esc(mission.id)}" data-role="${esc(role.id)}">この担当を引き受ける</button>`
    : '';
  return `<article class="amigos-member${role.state === 'paused' ? ' is-paused' : ''}">
    <div class="amigos-member-heading">
      <h4>${esc(role.displayName || role.title || '担当メンバー')}</h4>
      <span class="amigos-member-status">${esc(amigosMemberStatus(role))}</span>
    </div>
    ${role.responsibility ? `<p>${esc(role.responsibility)}</p>` : '<p class="muted">担当内容を確認中です。</p>'}
    ${role.note ? `<p class="amigos-member-note">${esc(amigosFriendlyNote(role.note))}</p>` : ''}
    ${claim}
  </article>`;
}

function amigosMessageHtml(message) {
  const route = message.toLabel === '全員'
    ? `${message.fromLabel}から全員へ`
    : `${message.fromLabel}から${message.toLabel}へ`;
  const answered = message.type === 'question' && message.answered
    ? '<span class="amigos-message-answer">回答済み</span>'
    : '';
  const attention = message.requiresAttention ? ' is-attention' : '';
  return `<details class="amigos-message${attention}">
    <summary>
      <span class="amigos-message-kind">${esc(amigosMessageTypeLabel(message.type))}</span>
      <span class="amigos-message-summary"><strong>${esc(message.summary || '内容を確認')}</strong><small>${esc(route)}</small></span>
      ${answered}
    </summary>
    <div class="amigos-message-body">
      ${message.body ? `<p>${esc(message.body)}</p>` : '<p class="muted">本文はありません。</p>'}
      ${message.createdAt ? `<time datetime="${esc(message.createdAt)}">${esc(fmtTime(message.createdAt))}</time>` : ''}
    </div>
  </details>`;
}

function amigosMissionDetailHtml(m) {
  const progress = amigosMissionProgress(m);
  const progressText = progress.total ? `${progress.total}人中${progress.done}人が完了` : '担当者を確認中';
  const members = (m.roles || []).map((role) => amigosMemberHtml(role, m)).join('');
  const conversation = (m.messages || []).length
    ? (m.messages || []).map(amigosMessageHtml).join('')
    : '<div class="empty compact">まだやりとりはありません。</div>';
  return `<div class="amigos-detail-content">
    ${amigosMissionAttention(m)}
    <section class="amigos-detail-section">
      <h3>現在の状況</h3>
      <div class="amigos-detail-status">
        <span class="amigos-phase amigos-phase-${esc(m.phase)}">${esc(amigosPhaseLabel(m.phase))}</span>
        <strong>${esc(progressText)}</strong>
      </div>
      ${m.goal ? `<p class="amigos-detail-goal">${esc(m.goal)}</p>` : ''}
      <p>${esc(amigosNextStep(m))}</p>
    </section>
    <section class="amigos-detail-section">
      <h3>メンバーの作業状況</h3>
      <div class="amigos-member-grid">${members || '<div class="empty compact">担当者を確認中です。</div>'}</div>
    </section>
    <section class="amigos-detail-section">
      <h3>やりとり</h3>
      <p class="muted">要点を時系列で表示しています。発言を選ぶと全文を確認できます。</p>
      <div class="amigos-conversation">${conversation}</div>
    </section>
  </div>`;
}

function setupAmigosClaimButtons(root) {
  for (const btn of root.querySelectorAll('.amigos-claim-btn')) {
    btn.addEventListener('click', () =>
      guard('担当の引き受け', async () => {
        btn.disabled = true;
        await api.amigosClaim(btn.dataset.home, btn.dataset.mission, btn.dataset.role);
        toast('担当の引き受けを依頼しました', true);
      })
    );
  }
}

function openAmigosDetail(missionId) {
  const scoped = amigosForProject(state.amigos, selectedProjectFolder());
  const mission = (scoped.missions || []).find((m) => m.id === missionId);
  if (!mission) return;
  $('amigos-detail-title').textContent = mission.title || 'ミッション詳細';
  $('amigos-detail-body').innerHTML = amigosMissionDetailHtml(mission);
  setupAmigosClaimButtons($('amigos-detail-body'));
  $('dlg-amigos-detail').showModal();
}

const AMIGOS_ROLES_SAMPLE = JSON.stringify(
  [
    { id: 'architect', mission: '設計を確定し質問に回答する', deliverables: ['architecture.md'] },
    { id: 'impl', mission: '実装する', deliverables: ['src/'], collaborates_with: ['architect'] },
    { id: 'reviewer', mission: '成果物をレビューする', approver: true },
  ],
  null,
  2
);

function openAmigosRequestDialog() {
  const dlg = $('dlg-amigos-post');
  if (!dlg) return;
  const homes = amigosForProject(state.amigos, selectedProjectFolder()).homes || [];
  const sel = $('amigos-post-home');
  sel.innerHTML = homes
    .map((h, index) => `<option value="${esc(h.dir)}">${esc(coworkRepoLabel(h.dir) || `実行先 ${index + 1}`)}</option>`)
    .join('');
  if (!$('amigos-post-roles').value.trim()) $('amigos-post-roles').value = AMIGOS_ROLES_SAMPLE;
  dlg.showModal();
}

function setupAmigosDialogs() {
  const dlg = $('dlg-amigos-post');
  if (!dlg) return;
  $('btn-amigos-post-cancel').addEventListener('click', () => dlg.close());
  $('btn-amigos-detail-close').addEventListener('click', () => $('dlg-amigos-detail').close());
  dlg.addEventListener('submit', (ev) => {
    ev.preventDefault();
    guard('タスクの依頼', async () => {
      let roles;
      try {
        roles = JSON.parse($('amigos-post-roles').value);
      } catch (e) {
        toast(`担当チームの設定を読み取れません: ${e.message}`);
        return;
      }
      await api.amigosRequest({
        home: $('amigos-post-home').value,
        title: $('amigos-post-title').value.trim(),
        goal: $('amigos-post-goal').value.trim(),
        design: $('amigos-post-design').value,
        roles,
      });
      dlg.close();
      toast('依頼を投函しました（常駐デーモンが取り込み、公示します）', true);
      await refreshAmigos();
      renderAmigos();
    });
  });
}

function renderAmigos() {
  const el = $('tab-amigos');
  if (!el) return;
  updateAmigosTabVisibility();
  const a = amigosForProject(state.amigos, selectedProjectFolder());
  if (!a) return;
  if (a.error) {
    el.innerHTML = `<div class="empty"><strong>ミッションを読み込めませんでした</strong><span>${esc(a.error)}</span></div>`;
    return;
  }
  const missions = a.missions || [];
  const missionsHtml = missions.length
    ? `<div class="amigos-mission-grid">${missions.map(amigosMissionCardHtml).join('')}</div>`
    : `<div class="empty"><strong>ミッションがありません</strong>
        <span>新しい作業を始めるには「ミッションを依頼」を選んでください。</span></div>`;
  const errorsHtml = (a.errors || []).length
    ? '<p class="muted">一部のミッションを読み込めませんでした。更新しても直らない場合は詳細情報を確認してください。</p>'
    : '';
  el.innerHTML = `
    <div class="amigos-shell">
      <header class="cowork-header">
        <div>
          <span class="summary-kicker">協働</span>
          <h2>ミッション</h2>
          <p class="muted">複数の担当メンバーで進める作業の状況を確認できます。</p>
        </div>
        <div class="row">
          ${(a.homes || []).length ? '<button id="btn-amigos-request">ミッションを依頼…</button>' : ''}
          <button id="btn-amigos-refresh">更新</button>
        </div>
      </header>
      <section>
        <h3>ミッション（${missions.length} 件）</h3>
        ${missionsHtml}
        ${errorsHtml}
      </section>
    </div>`;

  const refreshBtn = $('btn-amigos-refresh');
  if (refreshBtn)
    refreshBtn.addEventListener('click', () =>
      guard('ミッション更新', async () => {
        await refreshAmigos();
        renderAmigos();
      })
    );
  const requestBtn = $('btn-amigos-request');
  if (requestBtn) requestBtn.addEventListener('click', () => openAmigosRequestDialog());
  for (const btn of el.querySelectorAll('[data-amigos-detail]')) {
    btn.addEventListener('click', () => openAmigosDetail(btn.dataset.amigosDetail));
  }
}

function workTypeLabel(type) {
  return type === 'state-machine' ? '定型業務' : '定期実行';
}

function coworkStatusClass(status) {
  const s = String(status || 'unknown');
  if (s === 'running') return 'st-running';
  if (s === 'failed') return 'st-failed';
  if (s === 'done') return 'st-done';
  if (s === 'idle') return 'st-ready';
  return 'st-draft';
}

function coworkRepoLabel(repo) {
  const s = String(repo || '');
  if (!s) return '';
  const parts = s.replace(/\\/g, '/').split('/').filter(Boolean);
  return parts.length ? parts[parts.length - 1] : s;
}

// リポジトリパスの比較キー。WSL UNC（\\wsl.localhost\<distro>\...）と POSIX、
// /mnt/<drive> と Windows ドライブ表記を同一視する（main 側 _pathKey の縮約版）。
function coworkPathKey(p) {
  let s = String(p || '').trim().replace(/\\/g, '/');
  if (!s) return '';
  const unc = s.match(/^\/\/wsl(?:\$|\.localhost)\/[^/]+(\/.*)?$/i);
  if (unc) s = unc[1] || '/';
  const mnt = s.match(/^\/mnt\/([a-z])(\/.*)?$/i);
  if (mnt) s = `${mnt[1]}:${mnt[2] || '/'}`;
  return s.replace(/\/+/g, '/').replace(/\/$/, '').toLowerCase();
}

// 表示対象の絞り込み: 選択中プロジェクトの作業だけを出す。
// 返り値は { item, index } — index は draft 配列の位置（編集/削除がそのまま使える）。
function coworkVisibleEntries(draft, selectedDir) {
  const entries = (draft || []).map((item, index) => ({ item, index }));
  if (!selectedDir) return [];
  const key = coworkPathKey(selectedDir);
  return entries.filter(({ item }) => coworkPathKey(item.repo || item.cwd) === key);
}

function coworkHasProjectConfig(cowork, projectFolder) {
  const key = coworkPathKey(projectFolder);
  return !!key && ((cowork && cowork.discoveredRepos) || []).some((repo) => coworkPathKey(repo) === key);
}

function coworkRunBannerHtml() {
  const r = state.coworkRun;
  if (!r) return '';
  if (r.phase === 'running') {
    return `<div class="cowork-run-banner running" role="status">「${esc(r.name || r.id)}」を実行中…</div>`;
  }
  if (r.phase === 'ok') {
    if (r.launched) {
      return `<div class="cowork-run-banner ok" role="status">「${esc(r.name || r.id)}」を別ウィンドウ（WSL tmux）で開始しました — 開いたウィンドウで進行を確認できます</div>`;
    }
    return `<div class="cowork-run-banner ok" role="status">「${esc(r.name || r.id)}」の実行が完了しました${r.message ? ` — ${esc(r.message)}` : ''}</div>`;
  }
  return `<div class="cowork-run-banner error" role="alert">「${esc(r.name || r.id)}」の実行に失敗しました${r.message ? `: ${esc(r.message)}` : ''}</div>`;
}

function renderCowork() {
  const el = $('tab-cowork');
  if (!el) return;
  updateCoworkTabVisibility();
  const cw = state.cowork;
  const draft = coworkDraft();
  const observed = new Map(((cw && cw.items) || []).map((x) => [String(x.id), x]));
  const busyId = state.coworkRun && state.coworkRun.phase === 'running' ? String(state.coworkRun.id) : '';
  if (cw && cw.error) {
    el.innerHTML = `<div class="empty"><strong>定常業務を読み込めませんでした</strong><span>${esc(cw.error)}</span></div>`;
    return;
  }
  // 選択中プロジェクトの作業だけを表示する（従来は全プロジェクトの作業が常に並んでいた）。
  const folder = selectedProjectFolder();
  const entries = coworkHasProjectConfig(cw, folder) ? coworkVisibleEntries(draft, folder) : [];
  const scopeLabel = `このプロジェクトの作業 ${entries.length} 件`;
  el.innerHTML = `
    <div class="cowork-shell">
      <header class="cowork-header">
        <div>
          <span class="summary-kicker">自動化</span>
          <h2>定常業務</h2>
          <p class="muted">繰り返し実行する作業を確認・実行できます。</p>
        </div>
        <div class="row">
          <button id="btn-cowork-add">追加</button>
          <button id="btn-cowork-save">保存…</button>
          <button id="btn-cowork-refresh" title="最新の状態を確認">更新</button>
          <button type="button" class="subtle-action" data-open-technical-info>詳細情報</button>
        </div>
      </header>
      <div class="cowork-scope muted">
        <span>${esc(scopeLabel)}</span>
      </div>
      ${coworkRunBannerHtml()}
      ${entries.length ? `<div class="cowork-list" role="list">${entries.map(({ item, index: i }) => {
        const id = String(item.id || item.name || `${item.type || 'loop'}-${i + 1}`);
        const live = observed.get(id) || {};
        const st = live.state || item.state || {};
        const discovered = item.source === 'discovered';
        const pairedLoop = !!(item._src && item._src.loop);
        const disabledWork = item.enabled === false;
        const running = !!st.running || busyId === id;
        const status = running ? 'running' : (st.status || 'unknown');
        const run = state.coworkRun && String(state.coworkRun.id) === id ? state.coworkRun : null;
        // 統合項目（kiro-loop の対エントリを持つステートマシン）は schedule も併記する
        const detail = item.type === 'state-machine'
          ? [item.workflow ? `workflow ${item.workflow}` : 'workflow 未設定',
             item.schedule ? `schedule ${item.schedule}` : ''].filter(Boolean).join(' ／ ')
          : (item.schedule ? `schedule ${item.schedule}` : 'schedule 未設定');
        return `<article class="cowork-item ${running ? 'is-running' : ''} ${run && run.phase === 'error' ? 'is-error' : ''}" role="listitem">
          <div class="cowork-item-main">
            <div class="cowork-item-title">
              <span class="dot ${running ? 'running' : ''}" title="${running ? '実行中' : '停止中'}"></span>
              <strong>${esc(item.name || id)}</strong>
              <span class="status-chip ${coworkStatusClass(status)}" title="${esc(status)}">${esc(statusLabel(status))}</span>
              <span class="label-chip">${esc(workTypeLabel(item.type))}</span>
              ${discovered ? '<span class="label-chip">設定ファイル</span>' : '<span class="label-chip">手動</span>'}
              ${pairedLoop ? '<span class="label-chip">定期実行つき</span>' : ''}
              ${disabledWork ? '<span class="label-chip">無効</span>' : ''}
            </div>
            <div class="cowork-item-sub muted">
              <span title="${esc(item.repo || '')}">${esc(coworkRepoLabel(item.repo))}</span>
              <span>${esc(detail)}</span>
              ${st.lastLogAt ? `<span>最終ログ ${esc(fmtTime(st.lastLogAt))}</span>` : ''}
            </div>
            ${run && run.phase === 'error' ? '<p class="cowork-item-error">実行できませんでした。詳細情報で原因を確認してください。</p>' : ''}
          </div>
          <div class="cowork-item-actions">
            <button data-cowork-run="${esc(id)}" data-cowork-type="${esc(item.type || 'loop')}" data-cowork-name="${esc(item.name || id)}" ${busyId ? 'disabled' : ''}>${busyId === id ? '実行中…' : '実行'}</button>
            ${(item.type !== 'state-machine' || pairedLoop) && item.repo && api.kiroLoopListSessions
              ? `<button data-cowork-term-repo="${esc(item.repo)}" data-cowork-term-name="${esc(item.name || id)}" ${busyId ? 'disabled' : ''}>端末</button>`
              : ''}
            ${api.coworkItemLogs ? `<button data-cowork-history="${esc(id)}" data-cowork-name="${esc(item.name || id)}">履歴</button>` : ''}
            <button data-cowork-edit="${i}" ${busyId ? 'disabled' : ''}>編集</button>
            ${discovered ? '' : `<button data-cowork-delete="${i}" ${busyId ? 'disabled' : ''}>削除</button>`}
          </div>
        </article>`;
      }).join('')}</div>`
      : '<div class="empty"><strong>このプロジェクトに登録された定常業務はありません</strong><span>プロジェクトの設定ファイルに作業を追加してください。</span></div>'}
    </div>`;
  const technicalInfo = el.querySelector('[data-open-technical-info]');
  if (technicalInfo) technicalInfo.addEventListener('click', openTechnicalInfo);
  const addBtn = $('btn-cowork-add');
  if (addBtn) addBtn.addEventListener('click', () => openCoworkWorkDialog(-1));
  const saveBtn = $('btn-cowork-save');
  if (saveBtn) saveBtn.addEventListener('click', openCoworkSaveDialog);
  const refreshBtn = $('btn-cowork-refresh');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      await refreshCowork({ probe: true, forceDiscover: true });
      state.coworkDraft = null;
      renderCowork();
    });
  }
  el.querySelectorAll('[data-cowork-history]').forEach((btn) => btn.addEventListener('click', () =>
    openCoworkHistory(btn.dataset.coworkHistory, btn.dataset.coworkName || '')));
  el.querySelectorAll('[data-cowork-edit]').forEach((btn) => btn.addEventListener('click', () => openCoworkWorkDialog(Number(btn.dataset.coworkEdit))));
  el.querySelectorAll('[data-cowork-delete]').forEach((btn) => btn.addEventListener('click', () => {
    coworkDraft().splice(Number(btn.dataset.coworkDelete), 1);
    updateCoworkTabVisibility();
    renderCowork();
  }));
  el.querySelectorAll('[data-cowork-run]').forEach((btn) => btn.addEventListener('click', async () => {
    const id = btn.dataset.coworkRun;
    const type = btn.dataset.coworkType;
    const name = btn.dataset.coworkName || id;
    state.coworkRun = { id, name, phase: 'running', message: '', detail: '', at: Date.now() };
    renderCowork();
    let res;
    try {
      res = type === 'state-machine'
        ? await api.coworkRunStateMachine(id, '')
        : await api.coworkRunLoop(id);
    } catch (err) {
      res = { ok: false, error: err.message || String(err) };
    }
    const detail = String((res && (res.stderr || res.stdout || res.error)) || '').trim();
    const message = detail ? detail.slice(0, 240) : (res && res.ok ? '' : 'エラー詳細なし');
    state.coworkRun = {
      id,
      name,
      phase: res && res.ok ? 'ok' : 'error',
      launched: !!(res && res.launched),
      message,
      detail: detail.slice(0, 1200),
      at: Date.now(),
    };
    toast(
      res && res.ok
        ? (res.launched
          ? `「${name}」を別ウィンドウ（WSL tmux）で開始しました`
          : `「${name}」を実行しました`)
        : `「${name}」を実行できませんでした: ${message}`,
      !!(res && res.ok)
    );
    await refreshCowork({ probe: true });
    renderCowork();
  }));
  el.querySelectorAll('[data-cowork-term-repo]').forEach((btn) => btn.addEventListener('click', () => {
    openKiroLoopTerminal({ repo: btn.dataset.coworkTermRepo, name: btn.dataset.coworkTermName || '' });
  }));
}

// ---------------------------------------------------------------------------
// 定常業務の実行履歴とログ
// ---------------------------------------------------------------------------

function fmtLogSize(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

async function openCoworkHistory(id, name) {
  const dlg = $('dlg-cowork-history');
  if (!dlg || !api.coworkItemLogs) return;
  state.coworkHistory = { id, name, loading: true };
  $('cowork-history-title').textContent = `実行履歴とログ — ${name || id}`;
  $('cowork-history-body').innerHTML = '<p class="muted">読み込んでいます…</p>';
  if (!dlg.open) dlg.showModal();
  const res = await guard('実行履歴の読込', () => api.coworkItemLogs(id));
  if (!state.coworkHistory || state.coworkHistory.id !== id) return; // 閉じられた/切替済み
  if (!res) {
    $('cowork-history-body').innerHTML = '<p class="muted">実行履歴を読み込めませんでした。</p>';
    return;
  }
  state.coworkHistory = { id, name: res.name || name, ...res, file: '', text: '' };
  renderCoworkHistory();
  // 最新ログがあれば自動で開く
  const first = (res.logs || [])[0];
  if (first) await selectCoworkLog(first.file);
}

function renderCoworkHistory() {
  const m = state.coworkHistory;
  const body = $('cowork-history-body');
  if (!m || !body) return;
  const historyRows = (m.history || []).map((h) => `
    <tr>
      <td class="mono">${esc(fmtTime(h.at))}</td>
      <td><span class="status-chip ${h.ok ? 'st-done' : 'st-failed'}">${h.ok ? '成功' : '失敗'}</span></td>
      <td class="cowork-history-msg">${esc(h.message || '')}</td>
    </tr>`).join('');
  const logRows = (m.logs || []).map((l) => `
    <li>
      <button class="cowork-log-file ${m.file === l.file ? 'selected' : ''}" data-cowork-log="${esc(l.file)}" title="${esc(l.file)}">
        <code>${esc(l.name)}</code>
      </button>
      <span class="muted">${esc(fmtTime(new Date(l.mtimeMs).toISOString()))} ・ ${esc(fmtLogSize(l.size))}</span>
    </li>`).join('');
  body.innerHTML = `
    <section class="cowork-history-runs">
      <h3>この画面からの実行（新しい順）</h3>
      ${historyRows
        ? `<div class="cowork-history-table-wrap"><table class="list"><tr><th>日時</th><th>結果</th><th>メッセージ</th></tr>${historyRows}</table></div>`
        : '<p class="muted">この画面からの実行記録はまだありません。</p>'}
    </section>
    <section class="cowork-history-logs">
      <h3>ログファイル（リポジトリの実行ログ・新しい順）</h3>
      ${logRows
        ? `<div class="cowork-history-log-layout">
            <ul class="cowork-log-list">${logRows}</ul>
            <div class="cowork-log-view-wrap">
              <div class="muted" id="cowork-log-meta">${m.file ? esc(m.file) : 'ログを選択してください'}</div>
              <pre id="cowork-log-view" class="mono full-output" tabindex="0">${esc(m.text || '')}</pre>
            </div>
          </div>`
        : '<p class="muted">ログファイルが見つかりません（.kiro-loop/logs・.statemachine-use/logs 等）。</p>'}
    </section>`;
  body.querySelectorAll('[data-cowork-log]').forEach((btn) =>
    btn.addEventListener('click', () => selectCoworkLog(btn.dataset.coworkLog)));
}

async function selectCoworkLog(file) {
  const m = state.coworkHistory;
  if (!m || !api.coworkReadLog) return;
  const res = await guard('ログの読込', () => api.coworkReadLog(m.id, file));
  if (!state.coworkHistory || state.coworkHistory.id !== m.id) return;
  if (!res) return;
  state.coworkHistory.file = res.file;
  state.coworkHistory.text = (res.truncated ? `…（先頭 ${fmtLogSize(res.size - res.text.length)} を省略・末尾のみ表示）\n` : '') + (res.text || '（空のログ）');
  renderCoworkHistory();
}

// ---------------------------------------------------------------------------
// kiro-loop 端末（Phase A: capture-pane 視聴）
// ---------------------------------------------------------------------------

function stripAnsi(s) {
  return String(s || '')
    .replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, '')
    .replace(/\u001b\][^\u0007]*(?:\u0007|\u001b\\)/g, '')
    .replace(/\r/g, '');
}

function setKiroLoopTabVisible(show) {
  const btn = $('tab-btn-kiro-loop');
  if (!btn) return;
  btn.classList.toggle('hidden', !show);
  btn.hidden = !show;
  if (!show && activeTab() === 'kiro-loop') {
    stopKiroLoopCapturePoll();
    switchTab('cowork');
  }
}

function stopKiroLoopCapturePoll() {
  if (state.kiroLoopTimer) {
    clearInterval(state.kiroLoopTimer);
    state.kiroLoopTimer = null;
  }
}

function kiroLoopCaptureSec() {
  const n = Number(state.config && state.config.kiroLoop && state.config.kiroLoop.captureSec);
  return Number.isFinite(n) && n > 0 ? n : 2;
}

function startKiroLoopCapturePoll() {
  stopKiroLoopCapturePoll();
  if (!state.kiroLoopTerm || !state.kiroLoopTerm.target) return;
  const tick = async () => {
    if (activeTab() !== 'kiro-loop' || !state.kiroLoopTerm || !state.kiroLoopTerm.target) return;
    if (!api.kiroLoopCapture) return;
    const target = state.kiroLoopTerm.target;
    const repo = state.kiroLoopTerm.repo;
    const res = await api.kiroLoopCapture({ target, lines: 200, repo }).catch((err) => ({ ok: false, error: err.message, text: '' }));
    if (!state.kiroLoopTerm || state.kiroLoopTerm.target !== target) return;
    state.kiroLoopTerm.text = res && res.text != null ? res.text : '';
    state.kiroLoopTerm.error = res && res.ok === false ? (res.error || 'capture に失敗') : '';
    state.kiroLoopTerm.at = Date.now();
    const pre = $('kiro-loop-capture');
    const meta = $('kiro-loop-term-meta');
    if (pre) {
      const next = stripAnsi(state.kiroLoopTerm.text);
      if (pre.textContent !== next) {
        const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 24;
        pre.textContent = next;
        if (stick) pre.scrollTop = pre.scrollHeight;
      }
    }
    if (meta) {
      meta.textContent = state.kiroLoopTerm.error
        ? state.kiroLoopTerm.error
        : `更新 ${new Date(state.kiroLoopTerm.at).toLocaleTimeString('ja-JP')} ／ 読み取り専用（capture-pane）`;
      meta.classList.toggle('sync-error', !!state.kiroLoopTerm.error);
    }
  };
  tick();
  state.kiroLoopTimer = setInterval(tick, kiroLoopCaptureSec() * 1000);
}

async function openKiroLoopTerminal({ repo, name } = {}) {
  if (!api.kiroLoopListSessions) {
    toast('kiro-loop 端末 API がありません');
    return;
  }
  setKiroLoopTabVisible(true);
  state.kiroLoopTerm = {
    repo: repo || '',
    name: name || '',
    target: '',
    session: '',
    items: [],
    text: '',
    error: 'セッションを検索しています…',
    at: Date.now(),
  };
  switchTab('kiro-loop');
  renderKiroLoopTerminal();
  const listed = await guard('tmux セッション', () => api.kiroLoopListSessions({ repo: repo || '' }));
  if (!listed) {
    state.kiroLoopTerm.error = 'セッション一覧の取得に失敗しました';
    renderKiroLoopTerminal();
    return;
  }
  const items = listed.items || [];
  const first = items[0] || null;
  state.kiroLoopTerm.items = items;
  state.kiroLoopTerm.session = first ? first.session : '';
  state.kiroLoopTerm.target = first ? first.target : '';
  state.kiroLoopTerm.error = first ? '' : (listed.error || 'このリポジトリに紐づく kiro-loop tmux セッションはありません');
  renderKiroLoopTerminal();
  if (first) startKiroLoopCapturePoll();
}

function renderKiroLoopTerminal() {
  const el = $('tab-kiro-loop');
  if (!el) return;
  const term = state.kiroLoopTerm;
  if (!term) {
    el.innerHTML = '<div class="empty">Cowork の「端末」から kiro-loop の tmux を開いてください。<br>Windows dashboard → WSL の tmux を capture-pane で視聴します（入力はできません）。</div>';
    return;
  }
  const opts = (term.items || []).map((it) =>
    `<option value="${esc(it.target)}" ${it.target === term.target ? 'selected' : ''}>${esc(it.session)}${it.name ? `（${esc(it.name)}）` : ''}${it.cwd ? ` — ${esc(it.cwd)}` : ''}</option>`
  ).join('');
  el.innerHTML = `
    <div class="kiro-loop-term">
      <header class="kiro-loop-term-header">
        <div>
          <h2 class="summary-kicker">kiro-loop 端末</h2>
          <p class="muted">${esc(term.name || 'tmux')} ／ ${esc(term.repo || '（リポジトリ未指定）')}</p>
        </div>
        <div class="row">
          <button id="btn-kiro-loop-refresh">再読込</button>
          <button id="btn-kiro-loop-close">閉じる</button>
        </div>
      </header>
      <div class="kiro-loop-term-toolbar">
        <label class="muted">セッション
          <select id="kiro-loop-target" ${opts ? '' : 'disabled'}>${opts || '<option value="">（なし）</option>'}</select>
        </label>
        <span id="kiro-loop-term-meta" class="muted">${esc(term.error || '読み取り専用（capture-pane）')}</span>
      </div>
      <pre id="kiro-loop-capture" class="kiro-loop-capture mono" aria-live="polite">${esc(stripAnsi(term.text || (term.error && !term.target ? '' : '…')))}</pre>
    </div>`;
  const sel = $('kiro-loop-target');
  if (sel) {
    sel.addEventListener('change', () => {
      const item = (term.items || []).find((x) => x.target === sel.value);
      state.kiroLoopTerm.target = sel.value;
      state.kiroLoopTerm.session = item ? item.session : sel.value;
      state.kiroLoopTerm.error = '';
      startKiroLoopCapturePoll();
    });
  }
  const refresh = $('btn-kiro-loop-refresh');
  if (refresh) {
    refresh.addEventListener('click', () => openKiroLoopTerminal({ repo: term.repo, name: term.name }));
  }
  const close = $('btn-kiro-loop-close');
  if (close) {
    close.addEventListener('click', () => {
      stopKiroLoopCapturePoll();
      state.kiroLoopTerm = null;
      setKiroLoopTabVisible(false);
      switchTab('cowork');
    });
  }
}

async function openCoworkFromSettings() {
  if ($('dlg-settings').open) $('dlg-settings').close();
  if ($('dlg-advanced-settings').open) $('dlg-advanced-settings').close();
  if ($('dlg-technical-info').open) $('dlg-technical-info').close();
  await refreshCowork({ forceDiscover: true });
  if (!coworkHasProjectConfig(state.cowork, selectedProjectFolder())) {
    toast('このプロジェクトには定常業務の設定ファイルがありません');
    return;
  }
  switchTab('cowork');
  renderCowork();
  if (!coworkDraft().length) openCoworkWorkDialog(-1);
}

function openCoworkWorkDialog(index) {
  const editing = index >= 0 ? coworkDraft()[index] : null;
  const discovered = !!(editing && editing.source === 'discovered');
  const repos = coworkRepos();
  if (!discovered && !repos.length) {
    toast('先に全体設定でリポジトリ（ワークスペース）を登録してください');
    return;
  }
  state.coworkEditIndex = index;
  const item = editing || { type: 'loop', repo: (repos[0] && repos[0].dir) || '' };
  $('cowork-work-title').textContent = index >= 0 ? '作業を編集' : '作業を追加';
  // 発見項目は当該 repo を固定表示（登録済みリポジトリ一覧に無いこともある）
  let repoOpts = repos.slice();
  if (discovered && item.repo && !repoOpts.some((r) => r.dir === item.repo)) {
    repoOpts = [{ dir: item.repo, label: item.repo }, ...repoOpts];
  }
  $('cw-repo').innerHTML = repoOpts.map((r) => `<option value="${esc(r.dir)}">${esc(r.label)} — ${esc(r.dir)}</option>`).join('');
  $('cw-repo').value = item.repo || (repoOpts[0] && repoOpts[0].dir) || '';
  $('cw-repo').disabled = discovered;
  $('cw-type').value = item.type || 'loop';
  $('cw-type').disabled = discovered;
  $('cw-name').value = item.name || item.id || '';
  $('cw-schedule').value = item.schedule || item.cron || '';
  // 発見項目のスケジュールは、書き戻せる物理フィールドがあるときだけ編集可:
  //   loop → 自身の scheduleKey / state-machine → 対となる kiro-loop エントリの scheduleKey
  const pairedLoop = !!(item._src && item._src.loop);
  $('cw-schedule').disabled = !!(discovered && (
    item.type === 'loop'
      ? (item._src && item._src.scheduleKey === '')
      : (!pairedLoop || item._src.loop.scheduleKey === '')
  ));
  $('cw-workflow').value = item.workflow || item.file || '';
  $('cw-workflow').disabled = discovered;
  $('cw-description').value = item.description || '';
  $('cw-enabled').checked = item.enabled !== false;
  const enField = $('cw-enabled-field');
  // enabled は kiro-loop の物理フィールド → loop と、対エントリを持つ統合ステートマシンで編集可
  if (enField) enField.style.display = ((item.type || 'loop') === 'loop' || pairedLoop) ? '' : 'none';
  $('dlg-cowork-work').showModal();
}

function applyCoworkWorkDialog() {
  const idx = state.coworkEditIndex;
  const existing = idx >= 0 ? coworkDraft()[idx] : null;
  const discovered = !!(existing && existing.source === 'discovered');
  const type = $('cw-type').value;
  const name = $('cw-name').value.trim() || (type === 'state-machine' ? '定型業務' : '定期実行');
  let item;
  if (discovered) {
    // id/source/_src/type/repo/workflow は保持し、編集可能フィールドのみ上書き
    item = {
      ...existing,
      name,
      schedule: $('cw-schedule').value.trim(),
      description: $('cw-description').value.trim(),
      enabled: $('cw-enabled').checked,
    };
  } else {
    item = {
      id: (existing && existing.id) || name.replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-|-$/g, '') || `cowork-${Date.now()}`,
      type,
      name,
      repo: $('cw-repo').value,
      schedule: $('cw-schedule').value.trim(),
      workflow: $('cw-workflow').value.trim(),
      description: $('cw-description').value.trim(),
      source: 'config',
    };
  }
  if (idx >= 0) coworkDraft()[idx] = item;
  else coworkDraft().push(item);
  $('dlg-cowork-work').close();
  updateCoworkTabVisibility();
  renderCowork();
}

function openCoworkSaveDialog() {
  $('cw-save-branch').value = '';
  $('cw-save-create').checked = false;
  $('cw-save-push').checked = false;
  $('dlg-cowork-save').showModal();
}

async function saveCoworkDraft() {
  const payload = {
    items: coworkDraft(),
    branch: $('cw-save-branch').value.trim(),
    createBranch: $('cw-save-create').checked,
    push: $('cw-save-push').checked,
  };
  const res = await guard('作業の保存', () => api.coworkSaveWork(payload));
  if (!res) return;
  state.config = res.config;
  state.coworkDraft = null;
  $('dlg-cowork-save').close();
  await refreshCowork({ forceDiscover: true });
  updateCoworkTabVisibility();
  renderCowork();
  const failed = (res.git || []).filter((x) => x.result && x.result.ok === false);
  const wbErrors = (res.writeback && res.writeback.errors) || [];
  const ok = failed.length === 0 && wbErrors.length === 0;
  let msg = '作業の変更を保存しました';
  if (wbErrors.length) msg = `実体ファイルの書き戻しに一部失敗: ${wbErrors[0]}`;
  else if (failed.length) msg = `保存しましたが git 操作に失敗したリポジトリがあります: ${failed[0].repo}`;
  toast(msg, ok);
}

// ---------------------------------------------------------------------------
// 起動
// ---------------------------------------------------------------------------

async function init() {
  setupDialogLayouts();
  state.config = await guard('設定読込', () => api.getConfig());
  initTabs();
  $('btn-refresh').addEventListener('click', () => refreshAll({ sync: false }));
  $('btn-doctor').addEventListener('click', openDoctor);
  $('btn-doctor-submit').addEventListener('click', askDoctor);
  $('btn-doctor-apply-feedback').addEventListener('click', applyDoctorFeedbackDraft);
  $('btn-doctor-close').addEventListener('click', () => $('dlg-doctor').close());
  $('btn-need-output-close').addEventListener('click', () => $('dlg-need-output').close());
  $('btn-delivery-review-close').addEventListener('click', () => $('dlg-delivery-review').close());
  $('btn-settings').addEventListener('click', openSettings);
  $('btn-project-settings').addEventListener('click', openProjectSettings);
  $('btn-project-settings-close').addEventListener('click', () => $('dlg-project-settings').close());
  $('btn-save-settings').addEventListener('click', () => saveSettings());
  $('btn-open-advanced-settings').addEventListener('click', () => {
    $('dlg-settings').close();
    openAdvancedSettings();
  });
  $('btn-advanced-settings-close').addEventListener('click', () => $('dlg-advanced-settings').close());
  $('btn-save-advanced-settings').addEventListener('click', () => saveSettings());
  $('btn-technical-info-close').addEventListener('click', () => $('dlg-technical-info').close());
  $('btn-cw-cancel').addEventListener('click', () => $('dlg-cowork-work').close());
  const chClose = $('btn-cowork-history-close');
  if (chClose) {
    chClose.addEventListener('click', () => {
      state.coworkHistory = null;
      $('dlg-cowork-history').close();
    });
  }
  $('btn-cw-ok').addEventListener('click', (ev) => { ev.preventDefault(); applyCoworkWorkDialog(); });
  setupAmigosDialogs();
  $('btn-cw-save-cancel').addEventListener('click', () => $('dlg-cowork-save').close());
  $('btn-cw-save-ok').addEventListener('click', (ev) => { ev.preventDefault(); saveCoworkDraft(); });
  const btnCoworkOpen = $('btn-settings-cowork-open');
  if (btnCoworkOpen) btnCoworkOpen.addEventListener('click', (ev) => { ev.preventDefault(); openCoworkFromSettings(); });
  $('btn-task-close').addEventListener('click', () => $('dlg-task').close());
  $('btn-enq-cancel').addEventListener('click', () => $('dlg-enqueue').close());
  $('btn-enq-submit').addEventListener('click', submitEnqueue);
  $('btn-enq-ai').addEventListener('click', aiEnqueueAssist);
  $('btn-replan-cancel').addEventListener('click', () => $('dlg-replan').close());
  $('btn-replan-submit').addEventListener('click', () => requestReplan($('replan-charter').value));
  // 新規プロジェクト作成
  $('btn-new-project').addEventListener('click', openNewProject);
  $('btn-np-cancel').addEventListener('click', () => $('dlg-new-project').close());
  $('btn-np-submit').addEventListener('click', submitNewProject);
  $('np-add-repo').addEventListener('click', () => addRepoRow());
  $('btn-np-ai').addEventListener('click', aiDraftCharter);
  // charter バージョン追加（既存プロジェクトに charters/<名前>.md を後から追加する）
  $('btn-nc-cancel').addEventListener('click', () => $('dlg-new-charter').close());
  $('btn-nc-ok').addEventListener('click', submitNewCharterVersion);
  // プロジェクトファイル編集
  $('btn-ef-cancel').addEventListener('click', () => $('dlg-edit-file').close());
  $('btn-ef-save').addEventListener('click', saveEditFile);
  $('btn-ef-template').addEventListener('click', insertCharterTemplate);
  $('btn-ef-ai').addEventListener('click', aiRefineCharter);
  $('btn-ef-ai-undo').addEventListener('click', undoAiRefine);
  $('btn-ef-open').addEventListener('click', () => {
    if (state.editFile) guard('ファイルを開く', () => api.openPath(state.editFile.file));
  });
  // フォーム編集（憲章 / 運用ルール / リポジトリ一覧）
  $('btn-ec-cancel').addEventListener('click', () => $('dlg-edit-charter').close());
  $('btn-ec-save').addEventListener('click', saveCharterForm);
  $('btn-ec-raw').addEventListener('click', charterFormToRaw);
  $('btn-ep-cancel').addEventListener('click', () => $('dlg-edit-policy').close());
  $('btn-ep-save').addEventListener('click', savePolicyForm);
  $('btn-ep-add').addEventListener('click', () => $('ep-rules')._add && $('ep-rules')._add());
  $('btn-ep-raw').addEventListener('click', () => {
    $('dlg-edit-policy').close();
    openEditFile('policy.md');
  });
  $('btn-er-cancel').addEventListener('click', () => $('dlg-edit-repos').close());
  $('btn-er-save').addEventListener('click', saveReposForm);
  $('btn-er-add').addEventListener('click', () => $('er-rows')._add && $('er-rows')._add());
  $('btn-er-raw').addEventListener('click', () => {
    $('dlg-edit-repos').close();
    openEditFile('repos.json');
  });
  // list-editor の「＋ 追加」ボタン（憲章フォームの各リスト）
  for (const btn of document.querySelectorAll('button[data-add-list]')) {
    btn.addEventListener('click', () => {
      const c = $(btn.dataset.addList);
      if (c && c._add) c._add('');
    });
  }
  api.onOpenTarget(handleOpenTarget);

  await refreshDiscovery();
  await refreshCowork();
  const last = localStorage.getItem('kpv:selected');
  const all = state.discovery.projects;
  const target = all.find((p) => p.dir === last) || all[0];
  if (target) await selectProject(target.dir);
  else renderAllTabs();
  setupPolling();
}

init();
