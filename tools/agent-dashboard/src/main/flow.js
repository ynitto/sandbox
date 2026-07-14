'use strict';

// agent-flow のバス（<bus>/runs/<run-id>/）を読み取り専用で解析する。
// 状態は agent-flow 本体と同じく「ファイルの存在」から導出する（CLI には聞かない）:
//   results/<id>.json があれば その status（done/failed）
//   claims/<id>/ に lease 内の claim があれば claimed
//   tasks/<id>.json（または graph.json のノード）だけなら pending
// 依存未達の pending は表示上 waiting として区別する（agent-flow に明示状態は無い）。
// run の生存（orchestrator が駆動中か）も meta.json の生存リース
// （orch_lease_until / heartbeat_at）から、daemon の稼働はロックファイル
// （$TMPDIR/agent-flow-locks/daemon-<sha1>.lock。同一ホストのみ）から、無ければ
// <bus>/status.json（state_git 越しに同期された生存信号。別ホスト構成のフォールバック）
// から、いずれもファイルだけで判定する。

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

// 終端 status（agent-flow 本体と一致させる）。canceled は人の明示指示による恒久停止。
// これに含めないと canceled run が「応答なし/実行中」に誤分類され、再投入/削除の可否もずれる。
const TERMINAL = new Set(['done', 'failed', 'canceled']);

// 生存リース未記録の run（heartbeat 前に owner が死んだ／古い agent-flow の run）を
// 停止扱いにするまでの猶予秒。agent-flow の孤児回収リース（poll*10、最低 120s）より
// 保守的に長くとる（表示専用で、誤って「応答なし」と見せないため）。
const NO_LEASE_GRACE_SEC = 600;

function parseTsSec(ts) {
  const t = Date.parse(ts || '');
  return isNaN(t) ? null : t / 1000;
}

// run の orchestrator が生きているか。agent-flow の run_is_orphaned と同じ導出:
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

// claims/<id>/ から勝者を決める。agent-flow と同じ決定的タイブレーク:
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

// gitlab executor の決定的タスクトークン（executors/gitlab.py `_task_token` と同一導出）。
// 実行中（result 未確定）のノードでも、このトークンでイシュー本文の隠しマーカー
// `<!-- agent-flow:task-token:kf-... -->` を検索すれば関連イシューへたどり着ける。
// 世代接尾辞（-rN / -rN-vM）は落とす。リトライ／リバイズで run-id が変わっても同一
// イシューへ再アタッチできる（executor と同じ安定化）。落とさないと viewer の突合が外れ、
// クローズ済みイシューを「実行中」のまま表示し続ける。
function nodeTaskToken(runId, nodeId) {
  const stableRun = String(runId || '').replace(/-r\d+(?:-v\d+)?$/, '');
  const key = stableRun ? `${stableRun}/${nodeId}` : String(nodeId || '');
  return 'kf-' + crypto.createHash('sha1').update(key).digest('hex').slice(0, 12);
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
// と同一。system note / agent-flow 自身の自動コメントは無視。却下語を承認語より優先）。
function gitlabDecisionFromComments(comments) {
  const list = Array.isArray(comments) ? comments : [];
  for (let i = list.length - 1; i >= 0; i--) {
    const n = list[i];
    if (!n || n.system) continue;
    const body = String(n.body || '');
    if (!body || body.includes('gitlab-idd:creator-node-id') || body.startsWith('agent-flow:')) continue;
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

// agent-project が付ける決定的 run-id `req-<backlogハッシュ>-<taskid>-r<retries>[-v<rev>]` を
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

function readRunMeta(busDir, runId) {
  return readJson(path.join(busDir, 'runs', runId, 'meta.json'));
}

// run ↔ バックログタスクの突き合わせ。決定的 run-id（req-<hash>-<task-id>-r<n>）なら
// run-id から直接引ける。agent-flow が自動採番した run-<ts>-<rand> にはタスクが埋まって
// いないので、agent-project が付ける作業ブランチ ap/<task-id> から逆引きする。これが無いと
// 旧形式の run は「どのタスクのものか」を viewer が言えず、再実行が inbox 投入（＝誰も
// 拾わない無反応ボタン）へ落ちる。
// 旧データ互換で kp/<task-id>（kiro 改名前）も受ける。
// task_branch_prefix が設定で ap/ 以外のときも、単一段の prefix/<task-id> を受け取る
// （取れないと flow:resubmit が inbox 投入＝daemon 無し既定では無反応ボタンになる）。
function taskIdOfRun(runId, meta) {
  const fromId = parseRunId(runId).taskId;
  if (fromId) return fromId;
  const branch = String((meta && meta.workspace && meta.workspace.branch) || '');
  const m = /^(?:ap|kp)\/(.+)$/.exec(branch) || /^[^/]+\/([^/]+)$/.exec(branch);
  return m ? m[1] : null;
}

// 「この run の続きから」を agent-project へ伝える。agent-project は task の last_run を見て
// 再開先を決める（run_id_for）ので、人が viewer で選んだ run をそこへ書き込む。これをせずに
// ready へ戻すだけだと、成功済みノードごと新しい run を作り直してしまう。
function pinResumeRun(projectDir, taskId, runId) {
  const file = path.join(projectDir, 'backlog', `${taskId}.md`);
  const src = fs.readFileSync(file, 'utf8');
  const line = `- last_run: ${runId}`;
  const next = /^- last_run:.*$/m.test(src)
    ? src.replace(/^- last_run:.*$/m, line)
    : src.replace(/^(##[^\n]*\n)/, `$1${line}\n`);
  if (next === src) throw new Error(`last_run を書けませんでした: ${file}`);
  fs.writeFileSync(file, next, 'utf8');
  return file;
}

// 1 つの run ディレクトリを読み、グラフ＋状態＋進捗のスナップショットにする
function readRun(runDir) {
  const runId = path.basename(runDir);
  const meta = readJson(path.join(runDir, 'meta.json')) || {};
  const graph = readJson(path.join(runDir, 'graph.json')) || {};
  const finalJson = readJson(path.join(runDir, 'final.json'));
  const nodesIn = (graph && typeof graph.nodes === 'object' && graph.nodes) || {};
  const now = Date.now() / 1000;
  const runStatus = String(meta.status || 'unknown');
  const runTerminal = TERMINAL.has(runStatus);

  const nodes = {};
  // output のテキストから拾ったイシュー URL の候補（nodeId → url）。executor の証跡が
  // run 全体にあるときだけ採用する（下の gitlabUsed 判定を参照）。
  const outputIssueCandidates = {};
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
    } else if (!runTerminal) {
      const winner = claimWinner(path.join(runDir, 'claims', id), now);
      if (winner) {
        state = 'claimed';
        who = winner.who || null;
        heartbeatAt = winner.claimed_at || null;
        leaseUntil = Number(winner.lease_until || 0) || null;
      } else {
        // park 記録（承認待ち）。agent-flow と同じく wait_lease_until が生存なら waiting 相当。
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
    } else {
      // 終端 run: park 表示はしないが、残 waits のイシュー座標は Issue 導線用に拾う
      const wrec = readJson(path.join(runDir, 'waits', `${id}.json`));
      if (wrec && wrec.issue && typeof wrec.issue === 'object') {
        parkIssue = wrec.issue;
      }
    }
    // 関連 GitLab イシュー: 承認済みは data、park 中は wait 記録から拾う（どちらも executor が
    // 書いた確実な座標）。却下（failed）は data が無いため output のテキストに頼るしかないが、
    // 出力全文を正規表現で漁ると executor と無関係な URL まで拾う（gitlab.py のテストを流した
    // ノードの pytest ログにサンプル URL が出ており、gitlab executor を使っていない run に
    // Issue ボタンが出て、押すと実在しないリポジトリへ飛んでいた）。候補として控えるだけにし、
    // run 全体に executor の証跡があるときだけ採用する。
    const issueUrl =
      (data && typeof data === 'object' && !Array.isArray(data) && data.web_url) ||
      (parkIssue && parkIssue.url) ||
      null;
    if (!issueUrl && state === 'failed' && output) {
      const cand = (output.match(ISSUE_URL_RE) || [])[0];
      if (cand) outputIssueCandidates[id] = cand;
    }
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

  // gitlab executor を使った run か: data（承認済み）か wait 記録（park 中）に確実な座標が
  // 一つでもあるか。証跡があるときだけ、却下ノードの output から拾った候補を採用する。
  // 証跡がゼロなら、その run に GitLab のイシューは存在しない＝出力中の URL はテストログ等の
  // ただの文字列なので無視する。
  const gitlabUsed = Object.values(nodes).some((n) => n.issueUrl);
  if (gitlabUsed) {
    for (const [id, url] of Object.entries(outputIssueCandidates)) {
      if (nodes[id]) nodes[id].issueUrl = url;
    }
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
  // 旧形式 run-id は parse だけでは taskId が無い。作業ブランチ ap/<task-id> から補う
  // （流れ画面の助言・やり直し導線が inbox 落ちしないように）。
  const taskId = idParts.taskId || taskIdOfRun(runId, meta);
  return {
    runId,
    status,
    taskId,
    retries: idParts.retries, // この試行のリトライ世代（req- 形式のときのみ）
    rev: idParts.rev, // 人の revise 世代（あれば）
    lineageId: idParts.lineageId, // 同一タスクのリトライ/リバイズを束ねる系統キー
    inheritedFrom: meta.inherited_from || null, // --inherit-from で引き継いだ先行 run-id
    // orchestrator の生存（meta の生存リースから）。false は「running のまま
    // owner が消えた」孤児の可能性を示す（agent-flow が回収するまでの間の表示）
    alive: TERMINAL.has(status) ? null : runAlive(meta, now),
    heartbeatAt: meta.heartbeat_at || null,
    resumeCount: Number(meta.resume_count || 0), // daemon が孤児を自動再開した回数（進捗でリセット）
    workspace: meta.workspace || null, // 唯一の書込先（gitlab executor の起票先解決に使う）
    references: Array.isArray(meta.references) ? meta.references : [],
    executor: meta.executor || null, // この run を駆動した executor（orchestrator が記録）
    // GitLab 連携の UI（突き合わせ・イシュー検索・レビュー導線）を出すか。
    // gitlab executor を使っていない run に出しても意味がない（実在しないイシューを
    // 探すボタンが並ぶだけ）。meta.executor が正、旧 run（記録なし）は証跡から推定。
    gitlabish: meta.executor ? meta.executor === 'gitlab' : gitlabUsed,
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
// agent-flow の公式な入力契約（inbox/<req-id>.json = submit_request と同形）だけを使い、
// 稼働中の daemon が新規要求として拾う。結果の再利用はしない（新しい run として最初から）。
function resubmitRun(busDir, runId) {
  const runDir = path.join(busDir, 'runs', runId);
  const meta = readJson(path.join(runDir, 'meta.json'));
  if (!meta) throw new Error(`run が見つかりません: ${runId}`);
  // status だけで弾くと、いちばん救いたい run が救えない。orchestrator が消えた run は
  // status=running のまま固まるので「終端していない」を理由に再投入を拒否され、人は
  // 手も足も出なくなる。実際に駆動中（リースが生きている）run だけを拒否する
  // ＝ prepareRunDeletion と同じ規則で「実行中」と「応答なし」を区別する。
  if (!TERMINAL.has(String(meta.status)) && runAlive(meta, Date.now() / 1000) === true) {
    throw new Error(`run は実行中です（status=${meta.status}）。再実行は完了・失敗・応答なしの run に使えます`);
  }
  const request = String(meta.request || '').trim();
  if (!request) throw new Error('meta.json に request がありません（再投入できません）');
  // 末尾スライスだと長い req-… の接頭辞が落ちる。末尾に retry 接尾辞を足し、長すぎれば中央を落とす。
  const stamp = `retry-${Date.now()}`;
  let newId = `${runId}-${stamp}`;
  if (newId.length > 80) {
    const keep = 80 - stamp.length - 1;
    newId = `${runId.slice(0, Math.max(8, keep))}-${stamp}`;
  }
  const inbox = path.join(busDir, 'inbox');
  fs.mkdirSync(inbox, { recursive: true });
  const rec = {
    id: newId,
    request,
    submitter: 'agent-dashboard',
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
      `run は実行中です（status=${status}）。終端（done/failed/canceled）または応答なしの run だけ削除できます`
    );
  }
  return { runDir, status };
}

function writeJsonAtomic(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(obj, null, 2), 'utf8');
  fs.renameSync(`${file}.tmp`, file);
}

// run を canceled に終端化する（人の明示指示による恒久停止）。agent-flow の cmd_cancel と同じ 3 手を
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
    // 既に終端でも残 waits / sticky cancel を掃除（cmd_cancel の terminal 経路と同契約）。
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
    try {
      fs.unlinkSync(path.join(busDir, 'inbox', 'cancels', `${id}.json`));
    } catch {
      /* 無ければ no-op */
    }
    return { status: curStatus, alreadyTerminal: true, marked: false, cleared, issues };
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
  // (4) 適用済み cancel マーカーを消す（daemon 不在でも sticky にしない）。
  // orch は meta=canceled で止まる。remote daemon も meta を見て終端を知る。
  if (marked) {
    try {
      fs.unlinkSync(path.join(busDir, 'inbox', 'cancels', `${id}.json`));
    } catch {
      /* 残っても致命ではない */
    }
  }
  return { status: marked ? 'canceled' : curStatus, marked, cleared, issues };
}

// ---------------------------------------------------------------------------
// run のアーカイブ（プロジェクト配下の保管庫）
// ---------------------------------------------------------------------------
// agent-flow の run 状態（bus/runs/<id>/）は古いものから掃除されるため、完了した run の情報は
// いずれビュアーから消えてしまう。ポーリングのたびに run のスナップショット（サマリ＋イベント）を
// プロジェクトフォルダ配下（<projectDir>/flow-archive/<run-id>.json）へ書いておき、bus から
// 消えた run も「アーカイブ」として一覧・閲覧できるようにする。
// 読み取り専用の写しであり、アーカイブからの再実行・キャンセル等の操作はできない。
//
// 置き場をプロジェクト配下にしているのは、アーカイブがそのプロジェクトのデータだから:
// プロジェクトを移動・コピーすれば付いてくるし、リセット（charter.md 以外を消す）で一緒に消える。
// バス単位ではなくプロジェクト単位で持つため、バスのパスをハッシュ化して振り分ける必要もない。

const ARCHIVE_DIRNAME = 'flow-archive';
const ARCHIVE_KEEP = 100; // プロジェクトごとに保持する最大スナップショット数（古い順に削除）

function flowArchiveDir(projectDir) {
  return path.join(String(projectDir || ''), ARCHIVE_DIRNAME);
}

// 直近に保存した内容の署名（プロセス内キャッシュ）。ポーリング毎の無駄な read/write を避ける
const _archiveSig = new Map();

function _runSig(run) {
  return [run.status, run.updatedAt, run.progress, JSON.stringify(run.counts)].join('|');
}

// 1 run のスナップショットを保存する（内容が前回保存から変わったときだけ書く）。
// busDir は run の生イベント（bus/runs/<id>/events/）を読むために要る。
function archiveRunSnapshot(projectDir, busDir, run) {
  const dir = flowArchiveDir(projectDir);
  const file = path.join(dir, `${run.runId}.json`);
  // 中身の無いスナップショットを残さない。run が bus から消えた後に呼ばれると readRun は
  // status='unknown' / total=0 の空オブジェクトを返す。それを保存すると、実体も記録も持たない
  // 「不明」な run が一覧に永久に居座る（実際 11 件溜まっていた）。記録する価値があるのは
  // ノードを持つ run だけ。
  if (!run || !run.runId) return false;
  if (!run.total && String(run.status || 'unknown') === 'unknown') return false;
  const sig = _runSig(run);
  if (_archiveSig.get(file) === sig && fs.existsSync(file)) return false;
  const runDir = path.join(busDir, 'runs', run.runId);
  const snapshot = {
    savedAt: new Date().toISOString(),
    run,
    events: readRunEvents(runDir, 50),
    nodeEvents: readNodeEvents(runDir),
  };
  writeJsonAtomic(file, snapshot);
  _archiveSig.set(file, sig);
  pruneArchive(dir);
  return true;
}

function pruneArchive(dir, keep = ARCHIVE_KEEP) {
  const files = safeList(dir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => ({ f, mtime: (() => { try { return fs.statSync(path.join(dir, f)).mtimeMs; } catch { return 0; } })() }))
    .sort((a, b) => b.mtime - a.mtime);
  for (const { f } of files.slice(keep)) {
    try {
      fs.unlinkSync(path.join(dir, f));
    } catch {
      /* 掃除失敗は致命的でない */
    }
  }
}

function readArchivedRun(projectDir, runId) {
  const id = String(runId || '');
  if (!id || id !== path.basename(id)) return null;
  return readJson(path.join(flowArchiveDir(projectDir), `${id}.json`));
}

// アーカイブ済み run のサマリ一覧（archived: true 付き）。alive は判定対象外にする
// （orchestrator はもう居ない。孤児と誤表示しない）。
// アーカイブのスナップショットを消す（run 本体の削除と対で使う）。消せたらパスを返す。
// bus から run を消してもこれが残ると、一覧が「live に無いアーカイブ」として拾い直して
// 表示し続ける＝人から見れば削除が効いていない。
function removeArchivedRun(projectDir, runId) {
  const id = String(runId || '');
  if (!id || id !== path.basename(id)) return null;
  const file = path.join(flowArchiveDir(projectDir), `${id}.json`);
  try {
    fs.unlinkSync(file);
    _archiveSig.delete(file);       // 署名キャッシュも落とす（次のポーリングで書き戻さない）
    return file;
  } catch {
    return null;                    // 元から無い（アーカイブされていない run）
  }
}

function listArchivedRuns(projectDir) {
  const dir = flowArchiveDir(projectDir);
  const out = [];
  for (const f of safeList(dir)) {
    if (!f.endsWith('.json')) continue;
    const snap = readJson(path.join(dir, f));
    if (!snap || !snap.run || !snap.run.runId) continue;
    out.push({ ...snap.run, alive: null, archived: true, archivedAt: snap.savedAt || null });
  }
  return out;
}

// バス配下の run を新しい順に一覧する（各 run はサマリのみ）。
// limit<=0 は件数制限なし（live 集合の判定用）。
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
  if (!limit || limit <= 0) return entries;
  return entries.slice(0, limit);
}

// ---------------------------------------------------------------------------
// agent-flow daemon の稼働検知（CLI 不要・ロックファイルだけで判定）
// ---------------------------------------------------------------------------

// agent-flow / agent-project と完全に同じ導出でロックパスを組む:
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
  const base = lockDir || path.join(os.tmpdir(), 'agent-flow-locks');
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

// <busDir>/status.json — agent-flow の生存信号（write_daemon_status が書く）。本体が state_git
// （鏡）越しにバス状態を同期する別ホスト構成のとき、ロックファイルは本体側の一時領域にあって
// ここには絶対に無い（sha1 の元になる bus パス自体が別ホストの --bus 値で、このクローンの
// busDir とは無関係）。その場合の唯一の生存根拠がこれ。agent-project の readStatus と同じ考え方。
function readDaemonStatus(busDir) {
  const rec = readJson(path.join(busDir, 'status.json'));
  if (!rec || typeof rec !== 'object') return null;
  const updatedMs = Date.parse(rec.updated_iso || '');
  if (isNaN(updatedMs)) return null;
  const ageSec = (Date.now() - updatedMs) / 1000;
  const freshSec = Number(rec.fresh_after_sec) || 120;
  return { ...rec, ageSec, fresh: ageSec >= 0 && ageSec <= freshSec };
}

// 対象バスの agent-flow daemon が稼働中か。
//  1. 同一ホストのロックファイル（pid 生存）で確定判定（従来どおり。agent-project の
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
  const alive = pidAlive(pid);
  const out = { running: alive, pid, lockPath, via: 'lock' };
  // 生存判定はロック（pid）が正。加えて daemon がローカルにも書く status.json が新しければ、
  // orchestrator/worker 数をベストエフォートで添える（同一ホストでも「何基動いているか」を可視化する）。
  // status.json が無い/古い場合は数を付けない＝生存判定・従来挙動には一切影響しない。
  if (alive) {
    const status = readDaemonStatus(busDir);
    if (status && status.fresh) {
      if (Number.isFinite(status.orchestrators)) out.orchestrators = status.orchestrators;
      if (Number.isFinite(status.workers)) out.workers = status.workers;
      if (status.node_id) out.nodeId = status.node_id;
    }
  }
  return out;
}

// 対象バスの agent-flow daemon を停止する（人の明示アクション。プロジェクトのリセットで使う）。
// agent-flow に stop コマンドは無く、daemon は SIGTERM で graceful に終了する
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
    throw new Error(`agent-flow daemon（pid=${st.pid}）へ SIGTERM を送れません: ${err.message}`);
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
  archiveRunSnapshot,
  listArchivedRuns,
  removeArchivedRun,
  readArchivedRun,
  flowArchiveDir,
  ARCHIVE_DIRNAME,
  daemonStatus,
  stopDaemon,
  readDaemonStatus,
  runAlive,
  resubmitRun,
  readRunMeta,
  taskIdOfRun,
  pinResumeRun,
  prepareRunDeletion,
  cancelRun,
  nodeTaskToken,
  reconcileNodeState,
  gitlabMrDecision,
  gitlabClosedIssueDecision,
  GITLAB_APPROVED_LABELS,
};
