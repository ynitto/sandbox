'use strict';

const fs = require('fs');
const path = require('path');
const { ipcMain, shell } = require('electron');
const { loadConfig, saveConfig } = require('./config');
const kiro = require('./kiro');
const flow = require('./flow');
const git = require('./git');
const { lookupScalar } = require('./toolconfig');
const { GitLabClient } = require('./gitlab');
const { openInReviewViewer } = require('./review');
const actions = require('./actions');
const authoring = require('./authoring');
const reset = require('./reset');

// すべてのハンドラを {ok, data|error} 形式に揃える（gitlab-review-viewer と同じ）
function handle(channel, fn) {
  ipcMain.handle(channel, async (_event, args) => {
    try {
      return { ok: true, data: await fn(args || {}) };
    } catch (err) {
      return { ok: false, error: err && err.message ? err.message : String(err) };
    }
  });
}

function client() {
  return new GitLabClient(loadConfig().gitlab);
}

// kiro-flow daemon ロックの置き場。⚙ 設定 > ~/.kiro の kiro-project/kiro-flow 設定の
// lock_dir > 両ツール共通の既定（tempdir 配下。daemonStatus 側で導出）。
function flowLockDir(cfg) {
  if (cfg.kiro && cfg.kiro.flowLockDir) return cfg.kiro.flowLockDir;
  const found = lookupScalar('lock_dir');
  return found ? found.value : null;
}

// ゴミ箱へ移動（可能な環境ではリカバリできる）。ゴミ箱が無い環境では完全削除
async function removeToTrash(target) {
  try {
    await shell.trashItem(target);
    return 'trash';
  } catch {
    fs.rmSync(target, { recursive: true, force: true });
    return 'delete';
  }
}

function registerIpcHandlers() {
  handle('config:get', () => loadConfig());
  handle('config:save', ({ config }) => saveConfig(config));

  // 選択中プロジェクトのリポジトリを git pull で最新化する。
  // 自動（force=false）は設定間隔・下限 60 秒のスロットリングでリモート負荷を抑える。
  // 都度プッシュ（gitAutoPush）が有効なときはローカルコミットと共存できる --rebase で取り込む
  handle('git:pull', ({ dir, force }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    const cfg = loadConfig();
    const intervalSec = (cfg.kiro && Number(cfg.kiro.gitPullSec)) || 300;
    const rebase = !!(cfg.kiro && cfg.kiro.gitAutoPush);
    return git.pull(dir, { intervalSec, force: !!force, rebase });
  });

  // ユーザー操作（指示・投入・記入・削除）の書き込みをコミットして push する
  // （状態共有 git への都度反映）。設定 gitAutoPush が無効なら何もしない
  handle('git:commitPush', ({ dir, message }) => {
    if (!dir) throw new Error('対象ディレクトリが指定されていません');
    const cfg = loadConfig();
    if (!(cfg.kiro && cfg.kiro.gitAutoPush)) return { skipped: true, disabled: true };
    return git.commitPush(dir, { message });
  });

  // 発見: 設定 roots + instances 自動発見 → コンテナ→プロジェクトのツリー
  handle('kiro:discover', () => kiro.discover(loadConfig()));

  // 1 プロジェクトの完全スナップショット（バスの発見に設定 kiro.flowBus も使う）
  handle('kiro:project', ({ dir }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return kiro.readProject(dir, loadConfig());
  });

  // kiro-flow バス（per-project bus/ または共有バス）。run 一覧に加えて daemon の
  // 稼働もロックファイルから判定して返す（kiro-flow CLI には一切聞かない）
  handle('flow:runs', ({ busDir, limit }) => ({
    runs: flow.listRuns(busDir, limit || 30),
    daemon: flow.daemonStatus(busDir, flowLockDir(loadConfig())),
  }));
  handle('flow:run', ({ busDir, runId }) => {
    const runDir = path.join(busDir, 'runs', runId);
    return {
      run: flow.readRun(runDir),
      events: flow.readRunEvents(runDir, 50),
      nodeEvents: flow.readNodeEvents(runDir), // ノード別タイムライン（開始・所要の根拠）
    };
  });

  // 失敗/完了した run を同じ要求で inbox へ再投入（人の明示アクション。新しい run になる）
  handle('flow:resubmit', ({ busDir, runId }) => flow.resubmitRun(busDir, runId));

  // 不要な run の削除（人の明示アクション）。実行中（orchestrator 生存）は拒否し、
  // 終端（done/failed）と応答なし（孤児）だけを runs/<id> ごとゴミ箱へ移動する
  handle('flow:deleteRun', async ({ busDir, runId }) => {
    const { runDir, status } = flow.prepareRunDeletion(busDir, runId);
    const via = await removeToTrash(runDir);
    return { runDir, status, via };
  });

  // run のキャンセル（人の明示アクション＝唯一の hard-stop）。cancel マーカーを inbox へ置き
  // （git 同期で他 PC / daemon へ伝わる）、run の meta を canceled に確定し、park 済みノードの
  // 再ポーリングを止める。承認待ちで park 中の run も暴走中の run も止められる。起票済みイシューは
  // 残す（追跡だけやめる＝kiro-flow の既定）。イシュークローズは daemon の cancel --close-issues か
  // gitlab-review-viewer に任せる（この viewer の GitLab クライアントは読み取り専用）。
  handle('flow:cancel', ({ busDir, runId, reason }) =>
    flow.cancelRun(busDir, runId, { reason: reason || '' })
  );

  // 不要なバックログタスクの削除（人の明示アクション）。backlog/<id>.md だけを
  // 対象にし、実行中（doing かつクレームあり）のタスクは拒否する。
  // クレームロック（claims/<id>.lock）は worker のクラッシュや review/blocked
  // 滞留で残骸が残るため（kiro-project 本体も approve 時に掃除する）、
  // ロックの存在だけでは拒否せず、削除時に残骸ロックも一緒に片付ける。
  // kiro-project に削除の公式契約は無いため、ファイルをゴミ箱へ移動する
  handle('kiro:deleteTask', async ({ dir, id }) => {
    const tid = String(id || '');
    if (!tid || tid !== path.basename(tid)) throw new Error(`不正なタスク ID です: ${id}`);
    const file = path.join(dir, 'backlog', `${tid}.md`);
    if (!fs.existsSync(file)) throw new Error(`タスクファイルがありません: ${file}`);
    const lockFile = path.join(dir, 'claims', `${tid}.lock`);
    const status = kiro.parseTask(fs.readFileSync(file, 'utf8'), tid).status;
    if (status === 'doing' && fs.existsSync(lockFile)) {
      throw new Error(`${tid} は実行中（doing・クレーム中）のため削除できません`);
    }
    const via = await removeToTrash(file);
    try {
      fs.rmSync(lockFile, { force: true }); // 残骸ロックを掃除（無ければ no-op）
    } catch {
      /* ロックの掃除失敗は削除自体の失敗にしない */
    }
    return { file, via };
  });

  // プロジェクトのリセット（人の明示アクション・危険操作）。charter.md 以外の全データを
  // ゴミ箱へ移動し、バスの kiro-flow daemon を停止する。charter が残るので、稼働中の
  // kiro-project は次パスで charter から再分解して最初からやり直す。
  // 順序は「daemon 停止 → 削除」: 先に止めないと worker が消したバスへ結果を書き戻す。
  // ドット始まりの同期内部（.state-git 等）は温存する — 管理クローンの manifest が残る
  // ことで、削除が次の同期で「ローカルの削除」としてリモートへ伝播する（データ復活を防ぐ）。
  handle('kiro:reset', async ({ dir }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    const cfg = loadConfig();
    const plan = reset.planReset(dir);
    const bus = kiro.resolveBusDir(dir, cfg);
    const daemon = await flow.stopDaemon(bus.busDir, flowLockDir(cfg));
    const res = await reset.executeReset(plan, removeToTrash);
    return { ...res, daemon, busDir: bus.busDir, busSource: bus.source };
  });

  // 実行中ノードの関連イシューを決定的タスクトークンで検索（gitlab executor 連動）
  handle('gitlab:findIssueByToken', ({ repoUrl, projectPath, token }) => {
    const gl = client();
    if (!gl.enabled) return { enabled: false, issue: null };
    return gl
      .findIssueByToken({ repoUrl, projectPath, token })
      .then((issue) => ({ enabled: true, issue }));
  });

  // GitLab イシューの最新状態を API で補完（設定が無ければ enabled:false）
  handle('gitlab:enrich', async ({ urls }) => {
    const gl = client();
    if (!gl.enabled) return { enabled: false, issues: [] };
    const issues = [];
    for (const url of (urls || []).slice(0, 30)) {
      try {
        issues.push(await gl.getIssueByUrl(url));
      } catch (err) {
        issues.push({ url, error: err.message });
      }
    }
    return { enabled: true, issues };
  });

  // run の非終端ノード（実行中など）を GitLab の「今」のイシュー状態と突き合わせて返す。
  //   ・クローズ済み → flow.reconcileNodeState（executor と同一規則）で完了/失敗を先読み反映。
  //     ラベル/MR だけで決着しないときだけ人コメントも取得して手掛かりにする（余計な API を避ける）。
  //   ・オープン中（レビュー待ち）→ reconciled=null でイシュー情報だけ返す（ノードに「レビュー中」表示）。
  // ノードの決定的タスクトークンで関連イシューを検索する。見つからなければそのノードは返さない。
  handle('gitlab:reconcileRun', async ({ repoUrl, projectPath, nodes }) => {
    const gl = client();
    if (!gl.enabled) return { enabled: false, nodes: [] };
    const list = Array.isArray(nodes) ? nodes.slice(0, 40) : []; // run 単位で有界化
    const out = [];
    for (const n of list) {
      const token = n && n.taskToken;
      if (!token) continue;
      let issue;
      try {
        issue = await gl.findIssueByToken({ repoUrl, projectPath, token });
      } catch {
        continue; // 起票先を解決できない/検索失敗のノードは黙って飛ばす（他ノードは続ける）
      }
      if (!issue) continue; // トークンで関連イシューが見つからない（起票前・非 gitlab タスク）
      let reconciled = null;
      if (issue.state === 'closed') {
        // ラベル / 関連 MR だけで決着するなら人コメントは取りに行かない。付かないときだけ補う。
        const mrDecision = flow.gitlabMrDecision((issue.relatedMrs || []).map((m) => m.state));
        const labelDecision = flow.gitlabClosedIssueDecision({ labels: issue.labels });
        if (!mrDecision && !labelDecision && issue.projectPath && issue.iid) {
          try {
            issue.comments = await gl.getIssueComments(issue.projectPath, issue.iid);
          } catch {
            issue.comments = [];
          }
        }
        reconciled = flow.reconcileNodeState({ state: n.state }, issue);
      }
      out.push({
        id: n.id,
        reconciled, // 'done' | 'failed'（クローズ済み）| null（オープン中＝レビュー待ち）
        url: issue.url || '',
        iid: issue.iid || null,
        title: issue.title || '',
        issueState: issue.state, // 'opened' | 'closed'
        labels: issue.labels || [],
        relatedMrs: issue.relatedMrs || [],
      });
    }
    return { enabled: true, nodes: out };
  });

  handle('gitlab:projectIssues', ({ projectPath, state, labels }) => {
    const gl = client();
    if (!gl.enabled) return { enabled: false, issues: [] };
    return gl
      .listProjectIssues({ projectPath, state, labels })
      .then((issues) => ({ enabled: true, issues }));
  });

  // 人のアクション（needs 回答・タスク投入・決定記録を残す CLI 操作）
  handle('kiro:feedback', ({ file, feedback }) => actions.submitFeedback(file, feedback));
  handle('kiro:enqueue', ({ dir, spec }) => actions.enqueueToInbox(dir, spec || {}));
  handle('kiro:action', (args) => actions.runAction(loadConfig(), args));

  // charter からのバックログ再分解を要求（エラー回復。done/既存と類似は投入しない）。
  // プロジェクト単位（id 無し）。本体が次パスで charter を分解し直し差分だけ入れる。
  handle('kiro:replan', ({ dir, reason }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.requestReplan(loadConfig(), { dir, reason });
  });

  // プロジェクト単位のライフサイクル操作（pause / resume / stop）。commands/ ドロップ
  // （＋都度 push）で届け、リモート本体（WSL・別ホスト）の watch が同期間隔内に取り込む。
  handle('kiro:lifecycle', ({ dir, action, reason }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.requestLifecycle(loadConfig(), { dir, action, reason });
  });

  // オーサリング（作成・編集）。人が書く上位入力ファイル（charter/policy/repos）だけを
  // 対象にし、タスク状態は触らない（done は verify のみが根拠の不変条件を壊さない）。
  //   createProject … <root>/projects/<name>/ に charter.md（＋ repos.json）を作る
  //   readFile/writeFile … charter.md / policy.md / repos.* の直接編集
  handle('kiro:createProject', ({ spec }) => authoring.createProject(spec || {}));
  handle('kiro:readFile', ({ dir, name }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return authoring.readProjectFile(dir, name);
  });
  handle('kiro:writeFile', ({ dir, name, content }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return authoring.writeProjectFile(dir, name, content);
  });

  // gitlab-review-viewer へレビューを引き継ぐ
  handle('review:open', ({ target }) => openInReviewViewer(loadConfig(), target || {}));

  handle('shell:openExternal', ({ url }) => {
    if (!/^https?:\/\//.test(url)) throw new Error(`外部で開けない URL です: ${url}`);
    return shell.openExternal(url);
  });

  handle('shell:openPath', ({ target }) => shell.openPath(target));
}

module.exports = { registerIpcHandlers };
