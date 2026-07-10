'use strict';

// kiro-flow のバス（<bus>/runs/<run-id>/）を読み取り専用で解析する。
// 状態は kiro-flow 本体と同じく「ファイルの存在」から導出する（CLI には聞かない）:
//   results/<id>.json があれば その status（done/failed）
//   claims/<id>/ に lease 内の claim があれば claimed
//   tasks/<id>.json（または graph.json のノード）だけなら pending
// 依存未達の pending は表示上 waiting として区別する（kiro-flow に明示状態は無い）。
// run の生存（orchestrator が駆動中か）も meta.json の生存リース
// （orch_lease_until / heartbeat_at）から、daemon の稼働はロックファイル
// （$TMPDIR/kiro-flow-locks/daemon-<sha1>.lock。同一ホストのみ）から、無ければ
// <bus>/status.json（state_git 越しに同期された生存信号。別ホスト構成のフォールバック）
// から、いずれもファイルだけで判定する。

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

// 終端 status（kiro-flow 本体と一致させる）。canceled は人の明示指示による恒久停止。
// これに含めないと canceled run が「応答なし/実行中」に誤分類され、再投入/削除の可否もずれる。
const TERMINAL = new Set(['done', 'failed', 'canceled']);

// 生存リース未記録の run（heartbeat 前に owner が死んだ／古い kiro-flow の run）を
// 停止扱いにするまでの猶予秒。kiro-flow の孤児回収リース（poll*10、最低 120s）より
// 保守的に長くとる（表示専用で、誤って「応答なし」と見せないため）。
const NO_LEASE_GRACE_SEC = 600;

function parseTsSec(ts) {
  const t = Date.parse(ts || '');
  return isNaN(t) ? null : t / 1000;
}

// run の orchestrator が生きているか。kiro-flow の run_is_orphaned と同じ導出:
// 非終端で orch_lease_until があればリースで、無ければ updated_at の age で判定。
// 戻り値: true=駆動中 / false=応答なし（孤児の可能性） / null=終端（判定対象外）
function runAlive(meta, now) {
  if (!meta || TERMINAL.has(String(meta.status))) return null;
  const lease = meta.orch_lease_until;
  if (typeof lease === 'number') return lease >= now;
  const ts = parseTsSec(meta.updated_at || meta.created_at);
  if (ts === null) return false;
  return now - ts <= NO_LEASE_GRACE_SEC;
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

function safeList(dir) {
  try {
    return fs.readdirSync(dir);
  } catch {
    return [];
  }
}

// claims/<id>/ から勝者を決める。kiro-flow と同じ決定的タイブレーク:
// lease 内の claim のうち (ts, who) が最小の 1 件。
function claimWinner(claimDir, now) {
  const claims = [];
  for (const f of safeList(claimDir)) {
    if (!f.endsWith('.json')) continue;
    const c = readJson(path.join(claimDir, f));
    if (!c || typeof c !== 'object') continue;
    const lease = Number(c.lease_until || 0);
    if (lease && lease < now) continue; // 期限切れは無視（孤児回収）
    claims.push(c);
  }
  if (!claims.length) return null;
  claims.sort((a, b) => (a.ts - b.ts) || String(a.who).localeCompare(String(b.who)));
  return claims[0];
}

// gitlab executor が output に残すイシュー URL（却下時は data が無いため output から拾う）
const ISSUE_URL_RE = /https?:\/\/[^\s)）」』]+\/-\/issues\/\d+/;

// gitlab executor の決定的タスクトークン（_task_token と同一導出）。
// 実行中（result 未確定）のノードでも、このトークンでイシュー本文の隠しマーカー
// `<!-- kiro-flow:task-token:kf-... -->` を検索すれば関連イシューへたどり着ける。
function nodeTaskToken(runId, nodeId) {
  return 'kf-' + crypto.createHash('sha1').update(`${runId}/${nodeId}`).digest('hex').slice(0, 12);
}

// ---------------------------------------------------------------------------
// gitlab executor の決着（承認/却下）をビュアー側で「先読み」する（クローズ済みイシューの反映）
// ---------------------------------------------------------------------------
// gitlab executor は「関連イシューがクローズされた」ことを result で bus に書くが、それは
// worker が決着ループでクローズを検知したときだけ。非ブロッキング委譲（act_async）＋PC の
// 日次停止などで worker が止まっている間に人がイシューを承認クローズすると、bus には result が
// 無いままなので、ビュアーのタスクグラフはノードを「実行中」のまま表示してしまう（＝完了に
// できない）。そこで、executor が result を書くのと同じ信号（関連 MR の状態 → status ラベル →
// 人コメントの語）から executor と同じ決着を推定し、クローズ済みイシューに紐づくノードを
// 完了/失敗として先に反映する。判定規則は executors/gitlab.py の _mr_decision /
// _closed_issue_decision / _decision_from_comments と一致させる（乖離させない）。
const GITLAB_APPROVED_LABELS = ['status:approved', 'status:done'];
// 外部クローズ時の承認/却下推定の手掛かり語（executor と同一。却下語を承認語より優先）。
const GITLAB_REJECT_HINTS = ['却下', 'リジェクト', '取り下げ', '取下げ', '不採用', 'やり直し',
  '作り直し', '見送り', 'reject', 'wontfix', "won't fix", 'not merging', "won't merge"];
const GITLAB_APPROVE_HINTS = ['承認', 'approve', 'approved', 'lgtm', '採用', '問題ありません',
  '問題なし', 'マージしました', 'merged', '完了', 'close as done'];

// 関連 MR の状態だけから決着を推定する（executor の _mr_decision と同一）。
//   'approved': MR が 1 つ以上ありすべて merged／'rejected': 未マージの closed が 1 つでもある／
//   '': 未決着（open な MR がある／MR 未作成）
function gitlabMrDecision(states) {
  const list = Array.isArray(states) ? states.map((s) => String(s || '')) : [];
  const opened = list.filter((s) => s === 'opened' || s === 'locked');
  const closedUnmerged = list.filter((s) => s === 'closed');
  const merged = list.filter((s) => s === 'merged');
  if (opened.length) return '';
  if (closedUnmerged.length) return 'rejected';
  if (merged.length && merged.length === list.length) return 'approved';
  return '';
}

// 人コメントを新しい順に走査し承認/却下の手掛かりで判定する（executor の _decision_from_comments
// と同一。system note / kiro-flow 自身の自動コメントは無視。却下語を承認語より優先）。
function gitlabDecisionFromComments(comments) {
  const list = Array.isArray(comments) ? comments : [];
  for (let i = list.length - 1; i >= 0; i--) {
    const n = list[i];
    if (!n || n.system) continue;
    const body = String(n.body || '');
    if (!body || body.includes('gitlab-idd:creator-node-id') || body.startsWith('kiro-flow:')) continue;
    const low = body.toLowerCase();
    if (GITLAB_REJECT_HINTS.some((h) => body.includes(h) || low.includes(h))) return 'rejected';
    if (GITLAB_APPROVE_HINTS.some((h) => body.includes(h) || low.includes(h))) return 'approved';
  }
  return '';
}

// 外部クローズ時に承認/却下を推定する（executor の _closed_issue_decision と同一の優先順:
// ラベル → 人コメント）。手掛かりが無ければ '' を返す（呼び出し側が「取り下げ＝却下」に落とす）。
function gitlabClosedIssueDecision(issue) {
  const labels = (issue && Array.isArray(issue.labels) && issue.labels) || [];
  if (GITLAB_APPROVED_LABELS.some((l) => labels.includes(l))) return 'approved';
  return gitlabDecisionFromComments(issue && issue.comments);
}

// ビュアーのノード（readRun 由来）と取得済み GitLab イシューから、executor が書くであろう終端
// 状態を先読みする。戻り値: 'done' | 'failed' | null（変更なし）。
//   - 既に終端（done/failed）のノードは bus が正なので触らない（null）。
//   - イシューが閉じていなければ未決着（null）。executor は open のうちに result を書くため、
//     「クローズ済みイシューがまだ bus に反映されていない」ケースだけを先読みする。
//   - 閉じている: 関連 MR 状態 → ラベル/コメント の順で承認/却下を推定（executor と同じ優先度）。
//     判断材料が無いクローズは executor と同じく「取り下げ＝却下」＝ failed。
function reconcileNodeState(node, issue) {
  if (!node || TERMINAL.has(String(node.state))) return null;
  if (!issue || issue.state !== 'closed') return null;
  const states = (Array.isArray(issue.relatedMrs) ? issue.relatedMrs : []).map((m) => (m && m.state) || '');
  let decision = gitlabMrDecision(states);
  if (!decision) decision = gitlabClosedIssueDecision(issue) || 'rejected'; // 取り下げ＝却下
  if (decision === 'approved') return 'done';
  if (decision === 'rejected') return 'failed';
  return null;
}

// kiro-project が付ける決定的 run-id `req-<backlogハッシュ>-<taskid>-r<retries>[-v<rev>]` を
// 分解する。バックログのタスク（安定オブジェクト）と、そのリトライ/リバイズ系統を UI が
// 突き合わせられるようにする。素の `run-<ts>-<rand>`（手動/単発）は taskId 無しで単独扱い。
const REQ_ID_RE = /^req-([0-9a-f]{6,})-(.+)-r(\d+)(?:-v(.+))?$/;
function parseRunId(runId) {
  const m = REQ_ID_RE.exec(runId);
  if (!m) return { taskId: null, retries: null, rev: null, lineageId: null };
  const [, hash, taskId, retries, rev] = m;
  return {
    taskId, // 一意化前の task.id（バックログ突き合わせは同じ正規化を掛けて行う）
    retries: Number(retries),
    rev: rev || null,
    lineageId: `req-${hash}-${taskId}`, // リトライ/リバイズを束ねる系統キー（＝同一タスク）
  };
}

// 1 つの run ディレクトリを読み、グラフ＋状態＋進捗のスナップショットにする
function readRun(runDir) {
  const runId = path.basename(runDir);
  const meta = readJson(path.join(runDir, 'meta.json')) || {};
  const graph = readJson(path.join(runDir, 'graph.json')) || {};
  const finalJson = readJson(path.join(runDir, 'final.json'));
  const nodesIn = (graph && typeof graph.nodes === 'object' && graph.nodes) || {};
  const now = Date.now() / 1000;

  const nodes = {};
  for (const [id, spec] of Object.entries(nodesIn)) {
    const result = readJson(path.join(runDir, 'results', `${id}.json`));
    let state = 'pending';
    let who = null;
    let finishedAt = null;
    let output = null;
    let data = null;
    let heartbeatAt = null; // 実行中: 直近の心拍時刻（Heartbeat が claim を書き換える）
    let leaseUntil = null; // 実行中: claim の lease 期限（now < lease = worker 生存）
    let parked = false; // park & poll: 承認待ち等で保留中（claim を解放してスロットを空けている）
    let throttled = false; // 同時イシュー上限で起票を見送っている（イシュー未作成）
    let parkIssue = null; // park 中の関連イシュー座標（{host,project,iid,url}）
    let parkActiveSeen = false; // 人の作業（MR 出現/ラベル）を検知済みか
    if (result) {
      state = result.status === 'failed' ? 'failed' : 'done';
      who = result.who || null;
      finishedAt = result.finished_at || null;
      output = typeof result.output === 'string' ? result.output : null;
      data = result.data !== undefined ? result.data : null;
    } else {
      const winner = claimWinner(path.join(runDir, 'claims', id), now);
      if (winner) {
        state = 'claimed';
        who = winner.who || null;
        heartbeatAt = winner.claimed_at || null;
        leaseUntil = Number(winner.lease_until || 0) || null;
      } else {
        // park 記録（承認待ち）。kiro-flow と同じく wait_lease_until が生存なら waiting 相当。
        // 失効していれば pending へ縮退（full worker が再アタッチで拾い直す）＝ここでは park 扱いにしない。
        const wrec = readJson(path.join(runDir, 'waits', `${id}.json`));
        if (wrec && Number(wrec.wait_lease_until || 0) >= now) {
          parked = true;
          throttled = Boolean(wrec.throttled);
          parkActiveSeen = Boolean(wrec.active_seen);
          parkIssue = (wrec.issue && typeof wrec.issue === 'object') ? wrec.issue : null;
          who = wrec.who || null;
          state = 'parked';
        }
      }
    }
    // 関連 GitLab イシュー: 承認済みは data、却下（failed）は output の URL、park 中は wait 記録から拾う
    const issueUrl =
      (data && typeof data === 'object' && !Array.isArray(data) && data.web_url) ||
      (output && (output.match(ISSUE_URL_RE) || [])[0]) ||
      (parkIssue && parkIssue.url) ||
      null;
    nodes[id] = {
      id,
      goal: String(spec.goal || ''),
      deps: Array.isArray(spec.deps) ? spec.deps.map(String) : [],
      kind: String(spec.kind || 'work'),
      retries: Number(spec.retries || 0),
      state,
      who,
      finishedAt,
      heartbeatAt,
      leaseUntil,
      output,
      data,
      issueUrl,
      parked, // 承認待ちで park 中（claim を解放しスロットを空けている）
      throttled, // 同時イシュー上限で起票を見送り中（イシュー未作成）
      parkActiveSeen, // 人の作業（MR/ラベル）を検知済み
      rejected: Boolean(
        (data && typeof data === 'object' && data.decision === 'rejected') ||
          (output && output.includes('[gitlab-reject]'))
      ),
      taskToken: nodeTaskToken(runId, id),
    };
  }

  // 依存未達の pending は waiting に落とす（可視化用の区別。claim 不能）
  for (const n of Object.values(nodes)) {
    if (n.state !== 'pending') continue;
    const unmet = n.deps.filter((d) => {
      const dep = nodes[d];
      return dep && dep.state !== 'done';
    });
    if (unmet.length) n.state = 'waiting';
  }

  const counts = { done: 0, failed: 0, claimed: 0, pending: 0, waiting: 0, parked: 0 };
  for (const n of Object.values(nodes)) counts[n.state] = (counts[n.state] || 0) + 1;
  const total = Object.keys(nodes).length;

  // gitlab executor の成果（issue_iid / web_url / decision / merged_mrs）を拾い上げる。
  // 却下（failed）は data が無いため output のイシュー URL から拾う（decision=rejected）
  const gitlabIssues = [];
  for (const n of Object.values(nodes)) {
    const d = n.data;
    if (d && typeof d === 'object' && !Array.isArray(d) && (d.issue_iid || d.web_url)) {
      gitlabIssues.push({
        nodeId: n.id,
        issueIid: d.issue_iid || null,
        url: d.web_url || '',
        decision: d.decision || null,
        mergedMrs: Array.isArray(d.merged_mrs) ? d.merged_mrs : [],
        state: n.state,
      });
    } else if (n.issueUrl) {
      gitlabIssues.push({
        nodeId: n.id,
        issueIid: null,
        url: n.issueUrl,
        decision: n.rejected ? 'rejected' : null,
        mergedMrs: [],
        state: n.state,
      });
    }
  }

  const status = String(meta.status || (finalJson ? 'done' : 'unknown'));
  const idParts = parseRunId(runId);
  return {
    runId,
    status,
    taskId: idParts.taskId, // 紐づくバックログタスク（req- 形式のときのみ）
    retries: idParts.retries, // この試行のリトライ世代（req- 形式のときのみ）
    rev: idParts.rev, // 人の revise 世代（あれば）
    lineageId: idParts.lineageId, // 同一タスクのリトライ/リバイズを束ねる系統キー
    inheritedFrom: meta.inherited_from || null, // --inherit-from で引き継いだ先行 run-id
    // orchestrator の生存（meta の生存リースから）。false は「running のまま
    // owner が消えた」孤児の可能性を示す（kiro-flow が回収するまでの間の表示）
    alive: TERMINAL.has(status) ? null : runAlive(meta, now),
    heartbeatAt: meta.heartbeat_at || null,
    resumeCount: Number(meta.resume_count || 0), // daemon が孤児を自動再開した回数（進捗でリセット）
    workspace: meta.workspace || null, // 唯一の書込先（gitlab executor の起票先解決に使う）
    references: Array.isArray(meta.references) ? meta.references : [],
    request: String(meta.request || ''),
    createdAt: meta.created_at || null,
    updatedAt: meta.updated_at || null,
    failureReason: meta.failure_reason || null,
    strategy: graph.strategy || null,
    iteration: Number(graph.iteration || 0),
    nodes,
    counts,
    total,
    progress: total ? (counts.done + counts.failed) / total : 0,
    gitlabIssues,
    final: finalJson
      ? { finishedAt: finalJson.finished_at || null, summary: finalJson.summary || '' }
      : null,
  };
}

// events/*.jsonl を新しい順に最大 limit 件マージして返す
function readRunEvents(runDir, limit = 50) {
  const dir = path.join(runDir, 'events');
  const events = [];
  for (const f of safeList(dir)) {
    if (!f.endsWith('.jsonl')) continue;
    let raw = '';
    try {
      raw = fs.readFileSync(path.join(dir, f), 'utf8');
    } catch {
      continue;
    }
    for (const line of raw.split('\n')) {
      const s = line.trim();
      if (!s) continue;
      try {
        const ev = JSON.parse(s);
        if (ev && typeof ev === 'object') events.push(ev);
      } catch {
        /* 壊れた行は無視 */
      }
    }
  }
  // ts は ISO 文字列（now_iso）。UTC Z 固定なので辞書順＝時刻順で新しい順に並べる
  events.sort((a, b) => String(b.ts || '').localeCompare(String(a.ts || '')));
  return events.slice(0, limit);
}

// ノード別のタイムライン（claimed / result イベント）。開始時刻・所要時間の根拠になる
// （claims/ の claimed_at は Heartbeat が書き換えるため「開始」には使えない）。
function readNodeEvents(runDir, perNode = 10) {
  const byNode = {};
  for (const ev of readRunEvents(runDir, 5000)) {
    const nid = ev.node;
    if (!nid || !['claimed', 'result'].includes(ev.kind)) continue;
    (byNode[nid] = byNode[nid] || []).push({
      ts: ev.ts || null,
      kind: ev.kind,
      who: ev.who || '',
      status: ev.status || null,
    });
  }
  for (const nid of Object.keys(byNode)) {
    byNode[nid] = byNode[nid].slice(0, perNode); // 新しい順（readRunEvents が降順）
  }
  return byNode;
}

// 失敗した run を「同じ要求の新しい run」として inbox へ再投入する（人の明示アクション）。
// kiro-flow の公式な入力契約（inbox/<req-id>.json = submit_request と同形）だけを使い、
// 稼働中の daemon が新規要求として拾う。結果の再利用はしない（新しい run として最初から）。
function resubmitRun(busDir, runId) {
  const runDir = path.join(busDir, 'runs', runId);
  const meta = readJson(path.join(runDir, 'meta.json'));
  if (!meta) throw new Error(`run が見つかりません: ${runId}`);
  if (!TERMINAL.has(String(meta.status))) {
    throw new Error(`run はまだ終端していません（status=${meta.status}）。再投入は失敗/完了後に使えます`);
  }
  const request = String(meta.request || '').trim();
  if (!request) throw new Error('meta.json に request がありません（再投入できません）');
  const newId = `${runId}-retry-${Date.now()}`.slice(-80);
  const inbox = path.join(busDir, 'inbox');
  fs.mkdirSync(inbox, { recursive: true });
  const rec = {
    id: newId,
    request,
    submitter: 'kiro-projects-viewer',
    workspace: meta.workspace || null,
    references: Array.isArray(meta.references) ? meta.references : [],
    submitted_at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
  };
  const file = path.join(inbox, `${newId}.json`);
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(rec, null, 2), 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return { runId: newId, file };
}

// run の削除可否を検証して対象ディレクトリを返す（実削除は ipc 側 = Electron shell）。
// 実行中（orchestrator の生存リースが有効）の run は削除できない。
// 終端（done/failed）と応答なし（リース切れ＝孤児）の run だけが対象。
function prepareRunDeletion(busDir, runId) {
  const id = String(runId || '');
  if (!id || id !== path.basename(id)) {
    throw new Error(`不正な run ID です: ${runId}`);
  }
  const runDir = path.join(busDir, 'runs', id);
  if (!fs.existsSync(runDir)) throw new Error(`run が見つかりません: ${id}`);
  const meta = readJson(path.join(runDir, 'meta.json')) || {};
  const status = String(meta.status || 'unknown');
  if (!TERMINAL.has(status) && runAlive(meta, Date.now() / 1000) === true) {
    throw new Error(
      `run は実行中です（status=${status}）。終端（done/failed）または応答なしの run だけ削除できます`
    );
  }
  return { runDir, status };
}

function writeJsonAtomic(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(obj, null, 2), 'utf8');
  fs.renameSync(`${file}.tmp`, file);
}

// run を canceled に終端化する（人の明示指示による恒久停止）。kiro-flow の cmd_cancel と同じ 3 手を
// ファイル操作で行う: (1) cancel マーカーを inbox/cancels/ に書く（git 同期で他 PC / daemon へ伝わる）、
// (2) run が存在すれば meta を canceled に確定（daemon 不在でも即停止）、(3) park 記録を掃除して
// 監視の再ポーリングを止める。起票済みイシューのクローズは呼び出し側（ipc）が gitlab API で行う（任意）。
// 返り値の issues は掃除前の park 済みイシュー座標（--close-issues 相当の後始末に使う）。
function cancelRun(busDir, runId, { reason } = {}) {
  const id = String(runId || '');
  if (!id || id !== path.basename(id)) throw new Error(`不正な run ID です: ${runId}`);
  const runDir = path.join(busDir, 'runs', id);
  const meta = readJson(path.join(runDir, 'meta.json'));
  const curStatus = meta ? String(meta.status || 'unknown') : null;
  // 掃除前に park 済みイシュー座標を集める（任意のクローズ後始末に渡す）
  const waitsDir = path.join(runDir, 'waits');
  const issues = [];
  for (const f of safeList(waitsDir)) {
    if (!f.endsWith('.json')) continue;
    const w = readJson(path.join(waitsDir, f));
    const iss = w && w.issue;
    if (iss && iss.iid != null) issues.push(iss);
  }
  if (meta && TERMINAL.has(curStatus)) {
    // 既に終端（done/failed/canceled）＝ cancel は不要（不可逆）。
    return { status: curStatus, alreadyTerminal: true, marked: false, cleared: 0, issues };
  }
  // (1) cancel マーカー（close_issues は viewer 側で閉じるため false で置く＝daemon の二重クローズを避ける）
  writeJsonAtomic(path.join(busDir, 'inbox', 'cancels', `${id}.json`), {
    id, who: `viewer-${os.hostname()}`, reason: reason || '',
    close_issues: false, requested_at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
  });
  // (2) run が存在すれば meta を canceled に確定（監視主体が居なくても止まる）
  let marked = false;
  if (meta && !TERMINAL.has(curStatus)) {
    meta.status = 'canceled';
    meta.updated_at = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
    if (reason) meta.cancel_reason = reason;
    writeJsonAtomic(path.join(runDir, 'meta.json'), meta);
    marked = true;
  }
  // (3) park 記録を掃除して再ポーリングを止める
  let cleared = 0;
  for (const f of safeList(waitsDir)) {
    if (!f.endsWith('.json')) continue;
    try {
      fs.unlinkSync(path.join(waitsDir, f));
      cleared += 1;
    } catch {
      /* 消せなくても致命的でない */
    }
  }
  return { status: marked ? 'canceled' : curStatus, marked, cleared, issues };
}

// バス配下の run を新しい順に一覧する（各 run はサマリのみ）
function listRuns(busDir, limit = 30) {
  const runsDir = path.join(busDir, 'runs');
  const entries = [];
  for (const name of safeList(runsDir)) {
    const runDir = path.join(runsDir, name);
    try {
      if (!fs.statSync(runDir).isDirectory()) continue;
    } catch {
      continue;
    }
    const run = readRun(runDir);
    entries.push(run);
  }
  entries.sort((a, b) => String(b.createdAt || '').localeCompare(String(a.createdAt || '')));
  return entries.slice(0, limit);
}

// ---------------------------------------------------------------------------
// kiro-flow daemon の稼働検知（CLI 不要・ロックファイルだけで判定）
// ---------------------------------------------------------------------------

// kiro-flow / kiro-project と完全に同じ導出でロックパスを組む:
//   sha1("local::" + realpath(bus)) → <lock_dir>/daemon-<hash>.lock
// lock_dir 未指定時の既定も両ツールと同じ tempdir 配下。
function daemonLockPath(busDir, lockDir) {
  let real;
  try {
    real = fs.realpathSync(busDir);
  } catch {
    real = path.resolve(busDir); // バス未作成でも Python の realpath と同じ値になる
  }
  const h = crypto.createHash('sha1').update(`local::${real}`).digest('hex');
  const base = lockDir || path.join(os.tmpdir(), 'kiro-flow-locks');
  return path.join(base, `daemon-${h}.lock`);
}

function pidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === 'EPERM'; // 別ユーザの生存プロセス（シグナルを送れないだけ）
  }
}

// <busDir>/status.json — kiro-flow の生存信号（write_daemon_status が書く）。本体が state_git
// （鏡）越しにバス状態を同期する別ホスト構成のとき、ロックファイルは本体側の一時領域にあって
// ここには絶対に無い（sha1 の元になる bus パス自体が別ホストの --bus 値で、このクローンの
// busDir とは無関係）。その場合の唯一の生存根拠がこれ。kiro-project の readStatus と同じ考え方。
function readDaemonStatus(busDir) {
  const rec = readJson(path.join(busDir, 'status.json'));
  if (!rec || typeof rec !== 'object') return null;
  const updatedMs = Date.parse(rec.updated_iso || '');
  if (isNaN(updatedMs)) return null;
  const ageSec = (Date.now() - updatedMs) / 1000;
  const freshSec = Number(rec.fresh_after_sec) || 120;
  return { ...rec, ageSec, fresh: ageSec >= 0 && ageSec <= freshSec };
}

// 対象バスの kiro-flow daemon が稼働中か。
//  1. 同一ホストのロックファイル（pid 生存）で確定判定（従来どおり。kiro-project の
//     daemon_running と同じく pid のみ判定＝fcntl 不在時フォールバックと同じ根拠）
//  2. ロックが無ければ status.json（state_git 越しの同期・同期遅延を許容した推定）へ
//     フォールバック（GitBus 分散実行のバスは対象外＝write_daemon_status が書かないため
//     status.json 自体が存在せず、自然に判定不能へ落ちる）
// running: true=稼働中 / false=停止 / null=判定不能（ロックはあるが pid を読めない等）
// via: 'lock'（確定）／'status-sync'（同期経由の推定）／'none'（判定材料なし）
function daemonStatus(busDir, lockDir) {
  const lockPath = daemonLockPath(busDir, lockDir);
  let raw;
  try {
    raw = fs.readFileSync(lockPath, 'utf8');
  } catch {
    const status = readDaemonStatus(busDir);
    if (status) {
      return {
        running: status.fresh, pid: status.pid || 0, lockPath, via: 'status-sync',
        ageSec: Math.round(status.ageSec), nodeId: status.node_id,
        orchestrators: status.orchestrators, workers: status.workers,
      };
    }
    return { running: false, pid: 0, lockPath, via: 'none' };
  }
  const pid = parseInt(raw.trim().split('\n')[0], 10) || 0;
  if (!pid) return { running: null, pid: 0, lockPath, via: 'lock' };
  return { running: pidAlive(pid), pid, lockPath, via: 'lock' };
}

// 対象バスの kiro-flow daemon を停止する（人の明示アクション。プロジェクトのリセットで使う）。
// kiro-flow に stop コマンドは無く、daemon は SIGTERM で graceful に終了する
// （子 orchestrator/worker を terminate してから抜ける）設計なので、同一ホストの
// ロックファイルから pid を取り SIGTERM を送って終了を待つ。
//   ・稼働していない → {running:false, stopped:true}（何もしない・冪等）
//   ・同一ホスト（via=lock）→ SIGTERM 送信 → timeoutMs まで生存確認 → {stopped}
//   ・別ホスト（via=status-sync）→ このプロセスからは止められない → {remote:true, stopped:false}
async function stopDaemon(busDir, lockDir, { timeoutMs = 5000 } = {}) {
  const st = daemonStatus(busDir, lockDir);
  if (!st.running) return { running: false, stopped: true, via: st.via, pid: st.pid || 0 };
  if (st.via !== 'lock' || !st.pid) {
    return { running: true, stopped: false, remote: true, via: st.via, pid: st.pid || 0 };
  }
  try {
    process.kill(st.pid, 'SIGTERM');
  } catch (err) {
    if (err.code === 'ESRCH') return { running: false, stopped: true, via: 'lock', pid: st.pid };
    throw new Error(`kiro-flow daemon（pid=${st.pid}）へ SIGTERM を送れません: ${err.message}`);
  }
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!pidAlive(st.pid)) return { running: true, stopped: true, via: 'lock', pid: st.pid };
    await new Promise((r) => setTimeout(r, 100));
  }
  return { running: true, stopped: !pidAlive(st.pid), via: 'lock', pid: st.pid };
}

module.exports = {
  readRun,
  parseRunId,
  readRunEvents,
  readNodeEvents,
  listRuns,
  daemonStatus,
  stopDaemon,
  readDaemonStatus,
  runAlive,
  resubmitRun,
  prepareRunDeletion,
  cancelRun,
  nodeTaskToken,
  reconcileNodeState,
  gitlabMrDecision,
  gitlabClosedIssueDecision,
  GITLAB_APPROVED_LABELS,
};
