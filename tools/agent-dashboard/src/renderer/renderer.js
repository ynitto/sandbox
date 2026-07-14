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
  flowFilter: 'active', // フロータブの run フィルタ（active＝非終端のみ／done＝完了・アーカイブ／all）
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
  // GitLab issue / MR
  opened: 'オープン',
  merged: 'マージ済み',
  closed: 'クローズ',
};

function statusLabel(status) {
  const s = String(status || '');
  return STATUS_LABELS[s] || s;
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
const PROSE_EXTRA_KEYS = new Set(['feedback', 'needs_reason', 'note', 'accept']);

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
  const tree = $('tree');
  const prevScroll = tree.scrollTop; // 再描画（ポーリング）でサイドバーのスクロールを失わない
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
          <span class="name">${esc(displayName)}${remoteGuess && p.running ? '~' : ''}${p.paused ? ' ⏸' : ''}</span>${badges.join('')}
          ${removeBtn}
        </div>`;
      })
      .join('');
  }
  tree.scrollTop = prevScroll;
  const live = instances.filter((i) => i.fresh).length;
  $('sidebar-footer').textContent = `稼働インスタンス: ${live} ／ 最終更新 ${new Date().toLocaleTimeString('ja-JP')}`;

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

async function reloadProject() {
  if (!state.selectedDir) return;
  const project = await guard('プロジェクト読込', () => api.readProject(state.selectedDir));
  if (!project) return;
  project.needs = stabilizeMilestoneNeeds(state.project, project);
  state.project = project;
  // 同期の健康状態（ローカル参照のみ・リモートへは触らない）。失敗しても表示を欠くだけ。
  state.gitHealth = await api.gitHealth(project.dir).catch(() => null);
  // バスが未作成でも daemon の稼働はロックファイルから判定できるため常に読む。
  // project.dir は run アーカイブ（<dir>/flow-archive/）の置き場として渡す。
  const fr = (await guard('フロー読込', () => api.flowRuns(project.dir, project.busDir))) || {};
  state.flowRuns = fr.runs || [];
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
  $('btn-doctor').disabled = false;
  const charterName = p.charter && p.charter.name ? p.charter.name : '';
  $('project-name').textContent = charterName && charterName !== p.name
    ? `${charterName} (${p.name})`
    : p.name;
  $('project-name').classList.remove('muted');
  const ps = p.projectState;
  const badges = [];
  if (ps && ps.status) badges.push(statusChip(ps.status));
  if (p.liveness && p.liveness.paused) badges.push('<span class="status-chip st-review">⏸ 一時停止中</span>');
  $('project-badges').innerHTML = badges.join(' ');
  const lastLog = p.runLog.length ? p.runLog[p.runLog.length - 1] : null;
  // ワークスペース（登録したフォルダ）を主に出し、状態の置き場が別ならプロジェクトルートも添える。
  // 同じなら 1 つだけ（状態フォルダを直接登録している従来構成では冗長になるため）。
  const metaBits = [`${esc(p.workspace || p.dir)}`];
  if (p.workspace && p.dir !== p.workspace) {
    metaBits.push(`プロジェクトルート: ${esc(p.dir)}`);
  }
  if (lastLog) metaBits.push(`最終実行: ${esc(statusLabel(lastLog.reason))} (${fmtAgo(lastLog.ts)})`);
  // 同期の健康状態を平易な一文で常時表示する。異常（error）は要対応として目立たせ、
  // 「なぜ画面が最新でないのか」「次に何を押せばよいのか」を人が推測しなくて済むようにする。
  const gh = state.gitHealth;
  if (gh && !gh.notRepo) {
    const icon = gh.level === 'error' ? '🔴' : gh.level === 'warn' ? '🟡' : '🟢';
    const cls = gh.level === 'error' ? 'sync-error' : gh.level === 'warn' ? 'sync-warn' : 'sync-ok';
    metaBits.push(`<span class="${cls}" title="${esc(gh.summary)}">${icon} 同期: ${esc(gh.summary)}</span>`);
  }
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
      <h3>稼働操作</h3>
      <div class="row">
        <button class="chip primary-inline" data-start-kiro
          title="この PC で agent-project の常駐（watch）を起動します">▶ 本体を起動</button>
        <span class="muted">⏻ 本体（agent-project）は停止中です — 起動するまでタスクは進みません</span>
      </div>
    </div>`;
  }
  return `
    <div class="card full">
      <h3>稼働操作</h3>
      <div class="row">
        ${
          paused
            ? '<button class="chip" data-lifecycle="resume" title="一時停止を解除して作業を再開します">▶ 再開</button>'
            : '<button class="chip" data-lifecycle="pause" title="タスクの実行を一時停止します（指示や回答の受け付けは続きます）">⏸ 一時停止</button>'
        }
        <button class="chip danger" data-lifecycle="stop"
          title="自動実行を停止します。再開はこの画面の「▶ 本体を起動」か、プロジェクトのマシンでの起動操作">⏹ 停止</button>
        <span class="muted">操作は自動で本体に届きます（反映まで少し時間がかかることがあります）</span>
      </div>
      ${paused ? '<div class="muted" style="margin-top:4px">⏸ 一時停止中です（再開まで作業は進みません。回答・指示の送信はできます）</div>' : ''}
    </div>`;
}

// 本体（agent-project）の起動。確認 → CLI 実行 → 結果を平易に伝える。
// 本体が別マシンの構成では「この PC が実行役になる」ことを事前に言い、
// CLI が無ければ人が本体マシンで打つコマンドをそのまま見せる。
async function startAgentProject() {
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
    : '<button class="summary-link" data-start-kiro>本体を起動</button>';

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
    </div>`;

  for (const btn of el.querySelectorAll('button[data-summary-tab]')) {
    btn.addEventListener('click', () => switchTab(btn.dataset.summaryTab));
  }
  bindLifecycleButtons(el);
}

function openProjectSettings() {
  const p = state.project;
  if (!p) return;
  const isMaster = !!(p.charter && p.charter.master);
  const versions = (p.charters || [])
    .map((ch) => `<li><span><strong>${esc(ch.name)}</strong>${ch.goal ? ` — ${esc(ch.goal)}` : ''}</span>
      <button data-edit="charters/${esc(ch.name)}.md">編集</button></li>`)
    .join('');
  const promote = p.charter && !isMaster && p.charters && p.charters.length
    ? '<button id="btn-settings-promote-charter">初版に名前を付ける</button>'
    : '';
  const danger = p.charter
    ? `<section class="project-settings-section danger-zone">
        <h3>リセット</h3>
        <p class="muted">計画、タスク、履歴を消して最初からやり直します。憲章は残ります。</p>
        <button class="danger" id="btn-settings-reset">プロジェクトをリセット</button>
      </section>`
    : '';

  $('project-settings-body').innerHTML = `
    <h2>プロジェクト設定</h2>
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
      <div class="settings-section-heading">
        <h3>計画バージョン</h3>
        <button id="btn-settings-add-version">追加</button>
      </div>
      ${versions ? `<ul class="settings-version-list">${versions}</ul>` : '<p class="muted">計画バージョンはまだありません。</p>'}
      ${promote}
    </section>
    ${danger}`;

  for (const btn of $('project-settings-body').querySelectorAll('button[data-edit]')) {
    btn.addEventListener('click', () => {
      $('dlg-project-settings').close();
      openProjectFile(btn.dataset.edit);
    });
  }
  const add = $('btn-settings-add-version');
  if (add) add.addEventListener('click', () => {
    $('dlg-project-settings').close();
    openAddCharterVersion();
  });
  const promoteBtn = $('btn-settings-promote-charter');
  if (promoteBtn) promoteBtn.addEventListener('click', () => {
    $('dlg-project-settings').close();
    openPromoteCharter();
  });
  const reset = $('btn-settings-reset');
  if (reset) reset.addEventListener('click', () => {
    $('dlg-project-settings').close();
    resetProject();
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
function relatedRunsBlock(taskId) {
  const rr = runsForTask(taskId);
  if (!rr.length) return '';
  const items = rr
    .map(
      (r) => `<div class="rel-run-row">
        <button class="linklike mono" data-goto-run="${esc(r.runId)}">${esc(r.runId)}</button>
        ${statusChip(r.status)}
        <span class="muted">${r.total} 工程中 完了 ${r.counts.done}・失敗 ${r.counts.failed}</span>
        ${r.inheritedFrom ? `<span class="muted" title="引き継ぎ元の実行">↩ ${esc(r.inheritedFrom)}</span>` : ''}
      </div>`
    )
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
      else if (charterNames.length) extras.push('バージョン: 初版'); // 複数バージョン運用でのタグ無し＝charter.md 由来
      if (t.extra.after) extras.push(`依存: ${t.extra.after}`);
      if (t.extra.level) extras.push(`自動化レベル: ${t.extra.level}`);
      if (t.extra.track) extras.push(`系列: ${t.extra.track}`);
      if (t.extra.review) extras.push(`検収: ${t.extra.review}`);
      if (t.status === 'offloaded' && t.extra.flow_loc) {
        extras.push('委任先で実行中'); // act_async: agent-flow daemon で結果待ち（所在はタスク詳細で見る）
      }
      const rr = runsForTask(t.id); // 紐づく agent-flow run（リトライ系統）
      const runBadge = rr.length
        ? ` <button class="badge run-link" data-goto-run="${esc(rr[0].runId)}" title="関連する実行 ${rr.length} 件（最新: ${esc(statusLabel(rr[0].status))}）を開く">⚙${rr.length}</button>`
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
        <td class="muted">${esc(extras.join(' ／ '))}</td>
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
        cell = `<div class="task-prose">${proseHtml(v)}</div>`;
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
  $('dlg-task-body').innerHTML = `
    <h2><span class="mono">${esc(t.id)}</span>: ${esc(t.title)}</h2>
    ${relationshipStrip({ taskId: t.id })}
    <table class="list">
      <tr><th>状態</th><td>${statusChip(t.status)}</td></tr>
      <tr><th>出自</th><td>${esc(t.source)}</td></tr>
      <tr><th>優先度</th><td>${t.priority}</td></tr>
      <tr><th>再試行</th><td>${t.retries}</td></tr>
      <tr><th>検証コマンド</th><td>${t.verify ? `<pre class="mono">${esc(t.verify)}</pre>` : '<span class="muted">（未定義）</span>'}</td></tr>
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
    `${p.name}: プロジェクト憲章からタスクを作り直します。\n` +
      '進行中・却下済みと重複するタスクは追加されません（完了済みと同種のやり直しは作り直されます）。\n' +
      'タスクの状態は書き換えません。反映は次の実行サイクルです（即時ではありません）。よろしいですか？'
  );
  if (!yes) return;
  const ok = await guard('計画の作り直し', async () => {
    const res = await api.requestReplan(p.dir, 'agent-dashboard から再分解を要求');
    uiLog('replan', res);
    toast('計画の作り直しを依頼しました（次の実行で反映されます）', true);
    return true;
  });
  if (ok) {
    gitPushAfterWrite('agent-dashboard: replan request', p.dir);
    await reloadProject();
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

// 新規プロジェクトの repos 行を 1 つ追加する（任意・複数可）
function addRepoRow(prefill = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'np-repo-row';
  wrap.innerHTML = `
    <input class="np-r-name mono" placeholder="名前" value="${esc(prefill.name || '')}" />
    <input class="np-r-url mono" placeholder="git URL（必須）" value="${esc(prefill.url || '')}" />
    <input class="np-r-base mono" placeholder="ベースブランチ 例 main" value="${esc(prefill.base || '')}" />
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
  // 新規バージョン追加時は、前バージョン（または憲章）から引き継いだ やること/完了条件/成果物 を
  // 初期値にする（既存ファイルの編集では上書きしない＝res.exists のときは seed を使わない）。
  if (!res.exists && opts) {
    if (opts.seedGoal) fields.goal = opts.seedGoal;
    if (Array.isArray(opts.seedAcceptance)) fields.acceptance = opts.seedAcceptance;
    if (Array.isArray(opts.seedDeliverables)) fields.deliverables = opts.seedDeliverables;
  }
  charterForm = { dir: p.dir, name, fields, isVersion, isMaster, exists: res.exists };

  // 見出し・説明
  const verName = isVersion ? name.replace(/^charters\//, '').replace(/\.md$/, '') : '';
  $('ec-title').textContent = isVersion
    ? `計画バージョンを編集: ${verName}`
    : isMaster
      ? 'マスター憲章を編集'
      : '憲章を編集';
  $('ec-desc').textContent = isVersion
    ? 'このバージョンで達成すること（やること）と完了条件を書きます。制約・前提・対象リポジトリはマスター憲章から引き継がれます。'
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

  // 制約・前提はマスター/単一のみ（バージョンは継承）
  const showConstraints = !isVersion;
  $('ec-constraints-field').classList.toggle('hidden', !showConstraints);
  $('ec-assumptions-field').classList.toggle('hidden', !showConstraints);
  renderSimpleList($('ec-constraints'), fields.constraints, '例: 標準ライブラリのみ');
  renderSimpleList($('ec-assumptions'), fields.assumptions, '例: 入力は UTF-8');

  $('ec-inherit-note').classList.toggle('hidden', !isVersion);
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
  if (!cf.isVersion) {
    f.constraints = readSimpleList($('ec-constraints'));
    f.assumptions = readSimpleList($('ec-assumptions'));
  }
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
      `<input class="er-base mono" placeholder="ベースブランチ 例 main" value="${esc((r && r.base) || '')}" />` +
      `<input class="er-owns mono" placeholder="担当範囲（省略=参照のみ）" value="${esc((r && r.owns) || '')}" />` +
      `<input class="er-desc" placeholder="説明" value="${esc((r && r.desc) || '')}" />` +
      `<button type="button" class="np-r-del" title="削除">✕</button>`;
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
  reposForm = { dir: p.dir };
  renderRepoRows($('er-rows'), res.rows);
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
      owns: row.querySelector('.er-owns').value.trim(),
      desc: row.querySelector('.er-desc').value.trim(),
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
    ? `バージョン名を決めると、続けて内容を入力する画面が開きます（${src}の やること・完了条件・成果物 を引き継いだ状態で開くので、そこから編集できます。制約・前提・対象リポジトリはマスター憲章から自動継承されます）。`
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
  // ようにする（制約・前提・対象リポジトリはマスターから自動継承なのでフォームには出さない）。
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
  const res = await guard('雛形の取得', () => api.charterTemplate(m ? m[1] : fallback));
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
function needActionsHtml(n) {
  const kind = n.kind || 'blocked';
  const buttons = [];
  if (kind === 'plan-review') {
    buttons.push(`<button class="primary-inline" data-act="approve" data-id="${esc(n.id)}">✓ 承認（実行を許可）</button>`);
    buttons.push(`<button data-act="feedback" data-id="${esc(n.id)}" data-require="1" title="修正指示を記入して計画を練り直させます">↩ 差し戻す（修正指示を記入）</button>`);
    buttons.push(`<button class="danger" data-act="reject" data-id="${esc(n.id)}" data-require="1" title="このタスクを廃止し、計画を作り直させます">✕ 却下</button>`);
  } else if (kind === 'review') {
    buttons.push(`<button class="primary-inline" data-act="approve" data-id="${esc(n.id)}">✓ 承認して完了にする</button>`);
    buttons.push(`<button data-act="feedback" data-id="${esc(n.id)}" data-require="1" title="修正方針を記入してやり直させます">↩ 差し戻す（修正方針を記入）</button>`);
    buttons.push(`<button class="danger" data-act="reject" data-id="${esc(n.id)}" data-require="1" title="この成果を採用せず廃止し、計画を作り直させます">✕ 却下</button>`);
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
    buttons.push(`<button class="primary-inline" data-act="feedback" data-id="${esc(n.id)}">➤ 指示を送って再開</button>`);
    buttons.push(`<button data-act="rerun" data-id="${esc(n.id)}">↻ そのまま再実行</button>`);
    buttons.push(`<button data-act="hold" data-id="${esc(n.id)}" title="このタスクを止めて保留にします">⏸ 保留にする</button>`);
  }
  const ph =
    kind === 'plan-review'
      ? '差し戻しの修正指示・却下の理由（承認だけなら空欄のままで構いません）'
      : kind === 'review'
        ? '差し戻しの修正方針・却下の理由（承認だけなら空欄のままで構いません）'
        : '修正方針・指示（空のまま再実行もできます）';
  return `<div class="need-actions" data-need="${esc(n.id)}">
    <textarea rows="2" class="need-input" placeholder="${esc(ph)}"></textarea>
    <div class="row need-buttons">${buttons.join('')}
      <span class="spacer"></span>
      <button data-open="${esc(n.file)}" title="エディタで直接編集">ファイルを開く</button>
    </div>
  </div>`;
}

// 種別ごとの「何を確認するか」。カードの先頭で確認の目的を一文で示す
const NEED_ASK = {
  'plan-review': 'このタスクを実行してよいか確認してください。',
  review: '成果物を確認し、完了にしてよいか判断してください。',
  milestone: 'プロジェクトを完了にしてよいか確認してください。',
  blocked: '作業が止まっています。対応方法を指示してください。',
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
  const match = [...(flowRuns || [])].find((run) => String(run.taskId || '') === taskId);
  return match ? String(match.runId || '') : '';
}

function taskForNeed(project, need) {
  const taskId = String((need && (need.taskId || need.id)) || '');
  return ((project && project.backlog) || []).find((task) => String(task.id) === taskId) || null;
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
  const sections = [`# 要対応の原文\n\n${String((need && need.body) || '原文はありません')}`];
  const run = flowResponse && flowResponse.run;
  if (!run) {
    sections.push('# 関連する実行ログ\n\n関連するrunは見つかりませんでした。');
    return sections.join('\n\n');
  }
  const runFacts = [`run: ${run.runId || '-'}`, `状態: ${run.status || '-'}`];
  if (run.failureReason) runFacts.push(`失敗理由: ${run.failureReason}`);
  sections.push(`# 関連する実行\n\n${runFacts.join('\n')}`);
  for (const node of Object.values(run.nodes || {})) {
    const output = node.output == null ? '' : String(node.output);
    const error = node.error == null ? '' : String(node.error);
    if (!output && !error) continue;
    const text = [output, error ? `stderr / error:\n${error}` : ''].filter(Boolean).join('\n\n');
    sections.push(`# 工程 ${node.id || '-'} — ${node.goal || node.title || ''}\n\n${text}`);
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

function renderDeliveryRepo(entry, idx) {
  const role = deliveryRoleLabel(entry.role);
  const total = entry.files_total || (entry.files || []).length;
  const files = entry.files || [];
  const mr = entry.mr_url || '';
  // ローカル差分は解決済み ref があるときだけ（branch 名だけでは fetch 失敗時に誤誘導する）
  const canDiff = Boolean(entry.path && entry.base && entry.ref && entry.role !== 'reference');
  const unresolved = entry.role !== 'reference' && entry.branch && !entry.ref;
  const fileBtns = files
    .slice(0, 40)
    .map((f) => {
      const abs = entry.path ? `${String(entry.path).replace(/[/\\]$/, '')}/${f}` : '';
      const openBtn = abs
        ? `<button data-open="${esc(abs)}" title="${esc(abs)}">開く</button>`
        : '';
      const diffBtn = canDiff
        ? `<button data-delivery-diff="${esc(idx)}" data-file="${esc(f)}">差分</button>`
        : '';
      return `<li><code>${esc(f)}</code> ${openBtn}${diffBtn}</li>`;
    })
    .join('');
  const more =
    total > files.length ? `<li class="muted">…他 ${total - files.length} 件</li>` : '';
  return `<section class="delivery-repo" data-delivery-idx="${esc(idx)}">
    <header class="delivery-repo-head">
      <h3>${esc(entry.name || 'repo')} <span class="muted">（${esc(role)}）</span></h3>
      <div class="row">
        ${mr ? `<button class="primary-inline" data-delivery-mr="${esc(mr)}">GitLab MR を開く</button>` : ''}
        ${canDiff ? `<button data-delivery-diff="${esc(idx)}" data-file="">ブランチ差分</button>` : ''}
      </div>
    </header>
    <div class="muted delivery-repo-meta">
      ${entry.branch ? `ブランチ <code>${esc(entry.branch)}</code>` : ''}
      ${entry.base ? ` · base <code>${esc(entry.base)}</code>` : ''}
      ${entry.path ? ` · <code>${esc(entry.path)}</code>` : ''}
      ${entry.url && entry.role === 'reference' ? ` · ${esc(entry.url)}` : ''}
    </div>
    ${
      entry.role === 'reference'
        ? '<p class="muted">参照リポジトリです。成果差分は書込先を確認してください。</p>'
        : unresolved
          ? '<p class="muted">作業ブランチの ref をローカルで解決できていません。MR があればそちらで差分を確認してください。</p>'
        : files.length || total
          ? `<ul class="delivery-files">${fileBtns}${more}</ul>`
          : '<p class="muted">変更ファイルはありません。</p>'
    }
    ${entry.diff_cmd ? `<pre class="mono delivery-cmd">${esc(entry.diff_cmd)}</pre>` : ''}
  </section>`;
}

function openDeliveryReview(needId) {
  const need = state.project && state.project.needs.find((item) => item.id === needId);
  if (!need) return toast('要対応項目が見つかりません');
  const entries = need.delivery && need.delivery.length ? need.delivery : [];
  const mrs = need.mrUrls && need.mrUrls.length ? need.mrUrls : need.mrUrl ? [need.mrUrl] : [];
  $('delivery-review-title').textContent = `検収物を確認 — ${needDisplayTitle(need)}`;
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
  $('delivery-review-body').innerHTML = `${mrBlock}
    <div class="delivery-repos">${repos}</div>
    <pre id="delivery-diff-view" class="mono delivery-diff-view hidden" tabindex="0"></pre>`;
  wireDeliveryReview($('dlg-delivery-review'), need);
  $('dlg-delivery-review').showModal();
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
  for (const btn of root.querySelectorAll('[data-delivery-diff]')) {
    btn.addEventListener('click', async () => {
      const idx = Number(btn.getAttribute('data-delivery-diff'));
      const entry = (need.delivery || [])[idx];
      if (!entry || !entry.path) return toast('ローカル path が無いため差分を取得できません');
      if (!entry.ref) return toast('作業ブランチの ref が未解決のため差分を取得できません');
      const file = btn.getAttribute('data-file') || '';
      const tip = entry.ref;
      const view = $('delivery-diff-view');
      view.classList.remove('hidden');
      view.textContent = '差分を取得しています…';
      try {
        const res = await api.gitDiff({
          repo: entry.path,
          base: entry.base || 'main',
          ref: tip,
          file: file || undefined,
        });
        view.textContent = res.text || '(差分なし)';
        view.scrollTop = 0;
      } catch (err) {
        view.textContent = `差分の取得に失敗しました: ${err && err.message ? err.message : err}`;
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

function needsViewModel(needs, filter, selectedId, sentFn) {
  const sorted = [...(needs || [])].sort(
    (a, b) =>
      String(b.date || '').localeCompare(String(a.date || '')) ||
      String(a.id).localeCompare(String(b.id))
  );
  const counts = { open: 0, sent: 0, done: 0 };
  for (const n of sorted) counts[needBucket(n, sentFn)] += 1;
  const items = filter === 'gitlab' ? [] : sorted.filter((n) => needBucket(n, sentFn) === filter);
  const selected = items.find((n) => n.id === selectedId) || items[0] || null;
  return { counts, items, selected, selectedId: selected ? selected.id : null };
}

function renderNeedFacts(n) {
  const facts = [];
  if (n.failureSummary) {
    facts.push(`<div class="need-diag"><span class="label-chip">失敗の要因</span> ${inlineMd(n.failureSummary)}</div>`);
  }
  if (n.why) facts.push(`<div><span class="label-chip">理由</span>${proseHtml(n.why)}</div>`);
  if (n.summary) facts.push(`<div><span class="label-chip">概況</span>${proseHtml(n.summary)}</div>`);
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
  if (n.mrUrl || (n.delivery && n.delivery.length)) {
    const repos = (n.delivery || []).length;
    const label = n.mrUrl
      ? 'GitLab MR あり'
      : repos > 1
        ? `検収物 ${repos} リポジトリ`
        : '検収物あり';
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
  return `<article class="need-detail-card kind-${esc(n.kind || 'blocked')}">
    <button class="mobile-master-back" data-needs-back>一覧へ戻る</button>
    <header class="need-detail-head">
      <div>
        <div class="need-detail-badges">
          <span class="badge" title="${esc(n.kind || 'blocked')}">${esc(needKindLabel(n.kind))}</span>
          ${riskBadgeHtml(n)} ${chip}
        </div>
        <h2>${esc(needDisplayTitle(n))}</h2>
      </div>
      <span class="muted">${esc(n.date || '')}</span>
    </header>
    <section class="need-decision">
      <h3>判断すること</h3>
      <p>${esc(NEED_ASK[n.kind] || NEED_ASK.blocked)}</p>
    </section>
    <section class="need-facts">
      <h3>状況</h3>
      ${renderNeedFacts(n) || '<p class="muted">追加の状況説明はありません。</p>'}
    </section>
    ${settled ? '' : `<section class="need-response"><h3>回答</h3>${needActionsHtml(n)}${needVerifyRevisionHtml(p, n)}</section>`}
    <section class="need-evidence">
      <h3>成果物</h3>
      ${specFilesHtml(p, n) || '<p class="muted">関連するSpecはありません。</p>'}
      ${detailBlock}
      <button class="need-output-button" data-need-output="${esc(n.id)}">出力全体を見る</button>
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

function renderNeeds() {
  const p = state.project;
  const el = $('tab-needs');
  if (!p) {
    el.innerHTML = '';
    return;
  }

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
  const sig = JSON.stringify([
    state.needsFilter,
    state.needsSelectedId,
    state.needsMobileDetail,
    filters.map((x) => x[2]),
    p.needs.map((n) => [n.id, n.kind, n.decided, isNeedSent(n), n.why, n.summary, n.risk, n.failureSummary || '', (n.detail || '').length]),
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
      return `<button class="need-list-item ${selected ? 'selected' : ''}" data-need-select="${esc(n.id)}"
        aria-pressed="${selected}">
        <span class="need-list-meta">
          <span class="badge">${esc(needKindLabel(n.kind))}</span>
          ${riskBadgeHtml(n)}
        </span>
        <strong>${esc(needDisplayTitle(n))}</strong>
        <span>${esc(NEED_ASK[n.kind] || NEED_ASK.blocked)}</span>
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

  for (const btn of el.querySelectorAll('[data-needs-filter]')) {
    btn.addEventListener('click', () => {
      state.needsFilter = btn.dataset.needsFilter;
      state.needsSelectedId = null;
      state.needsMobileDetail = false;
      el.dataset.sig = '';
      renderNeeds();
      if (state.needsFilter === 'gitlab') renderGitLab();
    });
  }
  for (const btn of el.querySelectorAll('[data-need-select]')) {
    btn.addEventListener('click', () => {
      state.needsSelectedId = btn.dataset.needSelect;
      state.needsMobileDetail = true;
      el.dataset.sig = '';
      renderNeeds();
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
      const res = await api.runAction({ dir: p.dir, action: 'approve', id, reason: text });
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
//   via='lock'        … 同一ホストのロックファイル（pid 生存）で確定判定
//   via='status-sync' … state_git（鏡）越しに同期された status.json による推定（同期遅延を許容）
//   via='none'         … 判定材料なし
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
    const title = synced ? `別マシンで稼働（最終確認 ${fmtAgoSec(d.ageSec)}）` : 'このマシンで稼働中';
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
        return { kind: 'restart', cls: 'warn', chip: '⏸ 一時停止中', stopped: false,
          text: `プロジェクトが一時停止中のため、まだ再実行されません。「▶ 再開」を押すと本体が${how}。` };
      }
      if (live.running) {
        return { kind: 'auto', cls: 'ok', chip: '⏳ まもなく自動でやり直されます',
          text: `操作は不要です。本体（agent-project）が${how}。` +
            '本体が別の作業を実行中のときは、その完了後に順番に実行されます' +
            '（急ぐ場合の ↻ も同じ予約として扱われます）。' };
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
      return { kind: 'restart', cls: 'warn', chip: '⏻ 本体が停止中', stopped: true,
        text: `${ago}本体（agent-project）が動いていないため、このままでは再開されません。` +
          `「▶ 本体を起動」を押すと自動で${how}` +
          '（↻ は予約として残り、本体が動き出すと実行されます）。' };
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
  const busLine = `<details class="flow-source"><summary>実行環境</summary>
    <div class="muted">実行データ: <code class="mono">${esc(p.busDir)}</code> ${daemonBadge()}</div>
  </details>`;
  if (!state.flowRuns.length) {
    el.innerHTML = `${busLine}<div class="empty">実行はまだありません。<br>
      （完了した実行はこのビュアーに記録として残ります）</div>`;
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
        ? ` <button class="badge task-link" data-goto-task="${esc(r.taskId)}" title="元のタスクを開く">🗒 ${esc(r.taskId)}</button>`
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
        ? ' <span class="badge" title="完了後に保存された記録です（閲覧のみ）">📦</span>'
        : '';
      return `<div class="run-item ${state.flowRunId === r.runId ? 'selected' : ''}" data-run="${esc(r.runId)}"
        role="button" tabindex="0" aria-pressed="${state.flowRunId === r.runId}">
        <div class="run-item-head"><span>${statusChip(r.status)}${archivedBadge}${adviceBit}</span><span class="mono muted">${esc(shortRunId(r.runId))}</span></div>
        <div class="req">${prosePreview(r.request, 110) || '<span class="muted">内容なし</span>'}</div>
        <div class="progress"><div style="width:${pct}%"></div></div>
        <div class="muted">完了 ${r.counts.done}/${r.total}・失敗 ${r.counts.failed}・実行中 ${r.counts.claimed} ｜ ${fmtAgo(r.updatedAt || r.createdAt)}${taskLink}</div>
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
  el.innerHTML = `<div class="queue-summary flow-summary">${filterChips}</div>${busLine}
  <div id="flow-layout" class="${state.flowMobileDetail ? 'show-detail' : ''}">
    <div id="flow-runs">${runList || `<div class="empty">該当する run がありません（フィルタ: ${esc((FLOW_FILTERS.find(([k]) => k === state.flowFilter) || ['', state.flowFilter])[1])}）</div>`}</div>
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
  state.flowRun = await guard('run 読込', () => api.flowRun(state.project.dir, state.project.busDir, runId));
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
      ? '<button class="chip primary-inline" data-start-kiro>▶ 本体を起動</button>'
      : '',
    advice.kind === 'restart' && advice.stopped === false
      ? '<button class="chip primary-inline" data-resume-kiro>▶ 再開</button>'
      : '',
  ].join(' ');
  const adviceBanner = `<div class="advice-banner advice-${advice.cls}">
    ${adviceChip(advice)} <span>${esc(advice.text)}</span> ${adviceActions}
  </div>`;
  const resumed = run.resumeCount > 0 ? `（自動再開 ${run.resumeCount} 回）` : '';
  // 「この後どうなるか」は adviceBanner が言い切る。ここは事実（最終応答時刻）だけを出す
  //（以前ここにあった「再起動すると自動で再開されます」は、needs 待ち等では嘘になっていた）。
  const heartbeat =
    run.alive !== null && run.heartbeatAt
      ? `<div class="muted">最終応答: ${esc(fmtAgo(run.heartbeatAt))}${resumed}</div>`
      : '';
  // アーカイブ表示（bus からは掃除済み）: 読み取り専用の写しなので run への操作
  // （再投入・キャンセル・削除・GitLab 突き合わせ）は出さない。
  const archived = !!run.archived;
  const archivedBadge = archived
    ? ' <span class="badge" title="完了後に保存された記録です（閲覧のみ）">📦 記録</span>'
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
        <span class="summary-kicker">選択中の実行</span>
        <h2>${req.title ? `<span class="prose-inline">${inlineMd(req.title)}</span>` : '内容のない実行'}</h2>
      </div>
      <span>${statusChip(run.status)}${archivedBadge}</span>
    </div>
    ${req.body ? `<div class="flow-request-body">${proseHtml(req.body)}</div>` : ''}
    ${adviceBanner}
    ${relationshipStrip({ run })}
    ${archived ? '<p class="muted">完了後に保存された記録です。この画面からの操作はできません。</p>' : ''}
    ${run.failureReason ? `<div class="flow-failure">失敗理由: ${esc(String(run.failureReason).replace(/\[agent-error:[a-z]+\]\s*/g, ''))}</div>` : ''}
    <div class="flow-progress-block">
      <div class="progress"><div style="width:${pct}%"></div></div>
      <strong>${run.counts.done + run.counts.failed}/${run.total}（${pct}%）</strong>
    </div>
    <div class="flow-counts">
      <div><strong>${run.counts.done || 0}</strong><span>完了</span></div>
      <div><strong>${run.counts.claimed || 0}</strong><span>実行中</span></div>
      <div><strong>${run.counts.failed || 0}</strong><span>失敗</span></div>
      <div><strong>${(run.counts.pending || 0) + (run.counts.waiting || 0)}</strong><span>これから</span></div>
    </div>
    <div class="flow-primary-actions">${resubmit} ${reconcileBtn} ${cancelBtn} ${deleteBtn}</div>
  </section>`;

  const graphView = `<div class="flow-graph-workspace">
    <section class="flow-graph-surface">
      <div class="flow-section-heading">
        <div><span class="summary-kicker">工程</span><h2>タスクグラフ</h2></div>
        <span class="muted">工程を選ぶと詳細を表示します</span>
      </div>
      <div id="graph-box">${renderGraphSvg(run)}</div>
      <div class="legend">${legend}</div>
    </section>
    <aside id="flow-node" class="flow-node-detail">
      <span class="summary-kicker">選択した工程</span>
      ${nodeDetail || '<div class="empty">グラフから工程を選択してください</div>'}
    </aside>
  </div>`;

  const historyView = `<section class="flow-history-view">
    <div class="flow-section-heading">
      <div><span class="summary-kicker">履歴</span><h2>アクティビティ</h2></div>
      <span>${daemonBadge()}</span>
    </div>
    <div class="events flow-events">${events || '<span class="muted">イベントはありません</span>'}</div>
    <details class="flow-technical" data-ui-key="flow-technical:${esc(run.runId)}">
      <summary>技術情報を見る</summary>
      <dl>
        <div><dt>run ID</dt><dd class="mono">${esc(run.runId)}</dd></div>
        <div><dt>戦略</dt><dd>${esc(strat || '未設定')}</dd></div>
        <div><dt>状態</dt><dd>${statusChip(run.status)}</dd></div>
        ${run.inheritedFrom ? `<div><dt>引き継ぎ元</dt><dd class="mono">${esc(run.inheritedFrom)}</dd></div>` : ''}
      </dl>
      ${heartbeat}
    </details>
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

// 失敗/中止した run のやり直し。
// agent-project 配下の run は、bus へ投げ直すのではなくタスクを積み直す（本体が新しい run を
// 起こし、結果も回収する）。bus/inbox は daemon が拾う契約で、daemon を使わない構成では
// 誰も拾わない＝押しても何も起きないため（res.viaTask がその判別）。
async function resubmitFlowRun() {
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
  const ok = await guard('実行の中止', async () => {
    const res = await api.flowCancel(
      state.project.dir,
      state.project.busDir,
      run.runId,
      'agent-dashboard から手動キャンセル'
    );
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
  const runRows = [...p.runLog]
    .reverse()
    .map(
      (r) => `<tr>
      <td>${fmtTime(r.ts)}</td><td title="${esc(r.reason || '')}">${esc(statusLabel(r.reason))}</td><td>${esc(r.level || '')}</td>
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
    <div class="section-title">自動実行の履歴</div>
    ${
      runRows
        ? `<table class="list"><tr><th>時刻</th><th>結果</th><th>自動化レベル</th><th>サイクル</th><th>完了</th><th>要対応</th><th>検収待ち</th><th>エスカレーション</th><th>トークン</th><th>コスト</th><th>時間</th></tr>${runRows}</table>`
        : '<div class="muted">なし</div>'
    }
    <div class="section-title">決定記録</div>
    ${
      drRows
        ? `<table class="list"><tr><th>記録番号</th><th>日付</th><th>タスク</th><th>操作</th><th>理由</th><th>学習</th></tr>${drRows}</table>`
        : '<div class="muted">なし</div>'
    }
    <div class="section-title">納品物</div>
    ${deliveryRows ? `<table class="list">${deliveryRows}</table>` : '<div class="muted">なし</div>'}
    <div class="section-title">動作ログ（直近 80 行）</div>
    <div class="events">${journal || '<span class="muted">なし</span>'}</div>`;
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
    });
  }
}

function openSettings() {
  const cfg = state.config;
  $('cfg-roots').value = ((cfg.projects && cfg.projects.roots) || []).join('\n');
  $('cfg-autodiscover').checked = !cfg.projects || cfg.projects.autoDiscover !== false;
  $('cfg-refresh').value = cfg.projects ? cfg.projects.refreshSec : 5;
  $('cfg-git-pull').value = cfg.projects && cfg.projects.gitPullSec !== undefined ? cfg.projects.gitPullSec : 300;
  $('cfg-git-autopush').checked = !!(cfg.projects && cfg.projects.gitAutoPush);
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
  $('dlg-settings').showModal();
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

// 手動（⇣ ボタン）: スロットリングを無視して即 pull し、結果をトーストで知らせる
async function manualGitPull() {
  const pullDir = gitStateDir();
  if (!pullDir) return toast('プロジェクトを選択してください');
  const res = await guard('git pull', () => api.gitPull(pullDir, true));
  if (!res) return;
  lastGitPullError = null;
  toast(`git pull: ${res.output || '完了'}`, true);
  await refreshAll();
}

// 手動（🩺 ボタン）: 同期の詰まり（中断 rebase・ロック残骸・履歴の食い違い・未送信）を
// まとめて自動修復し、やったことを平易な文で知らせる。force push はせず人の作業は壊さない
async function manualGitHeal() {
  const healDir = gitStateDir();
  if (!healDir) return toast('プロジェクトを選択してください');
  const res = await guard('同期の修復', () => api.gitHeal(healDir));
  if (!res) return;
  uiLog('gitHeal', res);
  const steps = (res.steps || []).join(' → ');
  toast(`同期の修復: ${res.summary}${steps ? `（${steps}）` : ''}`, res.level !== 'error');
  await refreshAll();
}

function activeTabName() {
  const tab = document.querySelector('.tab.active');
  return tab ? tab.dataset.tab : 'overview';
}

async function buildDoctorContext() {
  const p = state.project;
  const tab = activeTabName();
  const context = {
    capturedAt: new Date().toISOString(),
    tab,
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
    const need = p.needs.find((item) => item.id === state.needsSelectedId) || null;
    if (need) {
      const output = await loadNeedFullOutput(need);
      context.selected = {
        type: 'need',
        id: need.id,
        kind: need.kind,
        title: needDisplayTitle(need),
        why: need.why,
        summary: need.summary,
        failureSummary: need.failureSummary,
        state: need.stateNote,
        fullOutput: output.text,
      };
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

async function askDoctor() {
  if (state.doctorBusy) return;
  if (!state.project) return toast('プロジェクトを選択してください');
  state.doctorBusy = true;
  $('btn-doctor').disabled = true;
  $('doctor-status').textContent = '現在の画面を読み取り、助言を作成しています…';
  $('doctor-response').innerHTML = '';
  $('dlg-doctor').showModal();
  try {
    const context = await buildDoctorContext();
    const res = await api.agentDoctor({ dir: state.project.dir, context });
    const model = res.model ? ` / ${res.model}` : '';
    $('doctor-status').textContent = `${res.cli}${model} の助言 — ${context.tab} 画面を分析`;
    $('doctor-response').innerHTML = mdToHtml(res.content || '助言はありませんでした。');
  } catch (err) {
    $('doctor-status').textContent = 'Doctorを実行できませんでした';
    $('doctor-response').innerHTML = `<div class="doctor-error" role="alert">${esc(err.message)}</div>`;
  } finally {
    state.doctorBusy = false;
    $('btn-doctor').disabled = false;
  }
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
  const sec = state.config && state.config.projects ? Number(state.config.projects.refreshSec) : 5;
  if (sec > 0) {
    state.timer = setInterval(() => {
      // ダイアログを開いている間・入力中は更新しない（書きかけの入力を消さない）
      if (
        $('dlg-settings').open ||
        $('dlg-task').open ||
        $('dlg-enqueue').open ||
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

// ---------------------------------------------------------------------------
// 起動
// ---------------------------------------------------------------------------

async function init() {
  state.config = await guard('設定読込', () => api.getConfig());
  initTabs();
  $('btn-refresh').addEventListener('click', refreshAll);
  $('btn-git-pull').addEventListener('click', manualGitPull);
  $('btn-git-heal').addEventListener('click', manualGitHeal);
  $('btn-doctor').addEventListener('click', askDoctor);
  $('btn-doctor-close').addEventListener('click', () => $('dlg-doctor').close());
  $('btn-need-output-close').addEventListener('click', () => $('dlg-need-output').close());
  $('btn-delivery-review-close').addEventListener('click', () => $('dlg-delivery-review').close());
  $('btn-settings').addEventListener('click', openSettings);
  $('btn-project-settings').addEventListener('click', openProjectSettings);
  $('btn-project-settings-close').addEventListener('click', () => $('dlg-project-settings').close());
  $('btn-save-settings').addEventListener('click', () => saveSettings());
  $('btn-task-close').addEventListener('click', () => $('dlg-task').close());
  $('btn-enq-cancel').addEventListener('click', () => $('dlg-enqueue').close());
  $('btn-enq-submit').addEventListener('click', submitEnqueue);
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
  const last = localStorage.getItem('kpv:selected');
  const all = state.discovery.projects;
  const target = all.find((p) => p.dir === last) || all[0];
  if (target) await selectProject(target.dir);
  else renderAllTabs();
  setupPolling();
}

init();
