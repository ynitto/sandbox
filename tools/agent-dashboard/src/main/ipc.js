'use strict';

const fs = require('fs');
const path = require('path');
const { ipcMain, shell } = require('electron');
const { loadConfig, saveConfig } = require('./config');
const project = require('./project');
const flow = require('./flow');
const git = require('./git');
const { lookupScalar } = require('./toolconfig');
const { GitLabClient } = require('./gitlab');
const { openInReviewViewer } = require('./review');
const actions = require('./actions');
const authoring = require('./authoring');
const agent = require('./agent');
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

// agent-flow daemon ロックの置き場。⚙ 設定 > ~/.agent の agent-project/agent-flow 設定の
// lock_dir > 両ツール共通の既定（tempdir 配下。daemonStatus 側で導出）。
function flowLockDir(cfg) {
  if (cfg.projects && cfg.projects.flowLockDir) return cfg.projects.flowLockDir;
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
    const intervalSec = (cfg.projects && Number(cfg.projects.gitPullSec)) || 300;
    const rebase = !!(cfg.projects && cfg.projects.gitAutoPush);
    return git.pull(dir, { intervalSec, force: !!force, rebase });
  });

  // ユーザー操作（指示・投入・記入・削除）の書き込みをコミットして push する
  // （状態共有 git への都度反映）。設定 gitAutoPush が無効なら何もしない
  handle('git:commitPush', ({ dir, message, paths }) => {
    if (!dir) throw new Error('対象ディレクトリが指定されていません');
    const cfg = loadConfig();
    if (!(cfg.projects && cfg.projects.gitAutoPush)) return { skipped: true, disabled: true };
    return git.commitPush(dir, { message, paths });
  });

  // 同期の健康状態（ローカル参照のみ）と、一発修復（🩺 ボタン）
  handle('git:health', ({ dir, refreshRemote }) => {
    if (!dir) throw new Error('対象ディレクトリが指定されていません');
    // 状態確認の fetch は作業ツリーを変更しない。自動 pull が無効でも最低60秒間隔で
    // 追跡情報を更新し、「同期は正常」という古い判定を表示し続けない。
    // ただし明示的な「表示を更新」はローカル再読込だけ、というUI契約を守る。
    return git.health(dir, { refreshRemote: !!refreshRemote, intervalSec: 60 });
  });
  handle('git:heal', ({ dir }) => {
    if (!dir) throw new Error('対象ディレクトリが指定されていません');
    return git.heal(dir);
  });

  // 検収サブ画面: 作業ブランチの git 差分（複数リポジトリ対応）
  handle('git:diff', ({ repo, base, ref, file, maxBytes }) =>
    git.diffRange(repo, { base, ref, file, maxBytes })
  );

  // 発見: 設定 roots + instances 自動発見 → コンテナ→プロジェクトのツリー
  handle('dashboard:discover', () => project.discover(loadConfig()));

  // プロジェクトの登録を実体に即して直接消す（config.roots のエントリ削除、または
  // ~/.agent-project/instances/*.json の該当レコード削除）。ファイル・ディレクトリ本体は
  // 一切触らない。親フォルダのスキャンで見つかった子は個別の登録が無いためエラーにする
  // （親フォルダの登録自体を ⚙ 設定のプロジェクトルートから編集してもらう）。
  handle('dashboard:removeProject', ({ dir }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    const cfg = loadConfig();
    const result = project.removeProjectRegistration(cfg, dir);
    if (result.removedFrom === 'roots') {
      cfg.projects = cfg.projects || {};
      cfg.projects.roots = result.roots;
      saveConfig(cfg);
      return { removedFrom: 'roots' };
    }
    if (result.removedFrom === 'instance') {
      return { removedFrom: 'instance', file: result.file };
    }
    throw new Error(
      '登録元が見つかりません（親フォルダ登録の配下で自動発見されたプロジェクトは個別に削除できません。' +
        '⚙ 設定のプロジェクトルートから親フォルダの登録を編集してください）'
    );
  });

  // 1 プロジェクトの完全スナップショット（バスの発見に設定 projects.flowBus も使う）
  handle('dashboard:project', ({ dir }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return project.readProject(dir, loadConfig());
  });

  // agent-flow バス（per-project bus/ または共有バス）。run 一覧に加えて daemon の
  // 稼働もロックファイルから判定して返す（agent-flow CLI には一切聞かない）。
  // bus の run はポーリングのたびにプロジェクト配下（<dir>/flow-archive/）へスナップショットし、
  // 掃除で bus から消えた run も archived: true 付きで一覧に残す（完了直後に表示が消える問題の対策）。
  handle('flow:runs', ({ dir, busDir, limit }) => {
    // live 判定はバス上の全 run で行う。limit 適用後の短い一覧だけだと、
    // 31 件目以降の生きた run が archived 扱いになり UI が誤表示する。
    const allLive = flow.listRuns(busDir, 0);
    const lim = Math.max(0, Number(limit) || 30);
    const runs = lim > 0 ? allLive.slice(0, lim) : allLive;
    if (dir) {
      for (const r of allLive) {
        try {
          flow.archiveRunSnapshot(dir, busDir, r);
        } catch {
          /* アーカイブ失敗は一覧表示の失敗にしない */
        }
      }
    }
    const live = new Set(allLive.map((r) => r.runId));
    const archived = dir
      ? flow.listArchivedRuns(dir).filter((a) => !live.has(a.runId))
      : [];
    const merged = [...runs, ...archived].sort((a, b) =>
      String(b.createdAt || '').localeCompare(String(a.createdAt || ''))
    );
    return {
      runs: merged,
      daemon: flow.daemonStatus(busDir, flowLockDir(loadConfig())),
    };
  });
  handle('flow:run', ({ dir, busDir, runId }) => {
    const runDir = path.join(busDir, 'runs', runId);
    if (!fs.existsSync(runDir)) {
      // bus からは掃除済み → アーカイブのスナップショットで応える（読み取り専用の写し）
      const snap = dir ? flow.readArchivedRun(dir, runId) : null;
      if (!snap) throw new Error(`run が見つかりません（bus にもアーカイブにも無し）: ${runId}`);
      return {
        run: { ...snap.run, alive: null, archived: true, archivedAt: snap.savedAt || null },
        events: snap.events || [],
        nodeEvents: snap.nodeEvents || {},
        archived: true,
      };
    }
    return {
      run: flow.readRun(runDir),
      events: flow.readRunEvents(runDir, 50),
      nodeEvents: flow.readNodeEvents(runDir), // ノード別タイムライン（開始・所要の根拠）
    };
  });

  // 失敗した run の「やり直し」。
  //
  // agent-project 配下の run なら、bus へ投げ直すのではなく **タスクを積み直す**。
  // bus/inbox は agent-flow の daemon が拾う契約だが、agent-project は daemon を使わず run を
  // 都度起動する（manage_flow_daemon の既定は false）。そこへ投入しても誰も拾わない＝押しても
  // 何も起きないボタンになる。しかも inbox 投入は agent-project のタスク状態に触らないため、
  // 仮に走っても結果が settle されず、タスクは doing のまま取り残される。
  // タスクを ready へ戻せば agent-project が新しい run を起こし、結果も正しく回収する。
  // （run-id にはタスク ID が埋まっている: req-<hash>-<task-id>-r<n>）
  //
  // agent-flow を単体で使っている run（タスクに紐づかない・daemon 運用）は従来どおり inbox へ。
  handle('flow:resubmit', async ({ dir, busDir, runId }) => {
    const meta = flow.readRunMeta(busDir, runId);
    // run-id にタスクが埋まっていない旧形式（run-<ts>-<rand>）でも、作業ブランチ ap/<task-id>
    // からタスクを引く。ここで諦めると inbox 投入へ落ちて無反応ボタンになる。
    const taskId = flow.taskIdOfRun(runId, meta);
    if (dir && taskId && fs.existsSync(path.join(dir, 'backlog', `${taskId}.md`))) {
      // 「続きから」やり直す: resume-run（last_run の固定 + ready への積み直しを本体側で
      // 原子的に行う正規の口）。以前は viewer が backlog ファイルを直接書き換えていたが、
      // それは状態リポジトリへの第二の書き手＝コミット競合の源だった。指示ファイル 1 枚に
      // 畳むことで、分散構成でも「追加ファイルのコミット」しか発生しない。
      const res = await actions.runAction(loadConfig(), {
        dir,
        action: 'resume-run',
        id: taskId,
        run: runId,
        reason: `実行画面から再実行（${runId} の続きから・失敗ノードのみやり直し）`,
      });
      return { ...res, viaTask: true, taskId, resumedFrom: runId };
    }
    return flow.resubmitRun(busDir, runId);
  });

  // 不要な run の削除（人の明示アクション）。実行中（orchestrator 生存）は拒否し、
  // 終端（done/failed）と応答なし（孤児）だけを runs/<id> ごとゴミ箱へ移動する。
  //
  // アーカイブのスナップショット（flow-archive/<run-id>.json）も一緒に消す。bus から消えても
  // これが残っていると、run 一覧は「live に無いアーカイブ」として拾い直して表示し続ける
  // ＝ 削除したのに消えない。人から見れば削除が効いていないのと同じ。
  handle('flow:deleteRun', async ({ dir, busDir, runId }) => {
    // bus に実体がある run と、アーカイブだけが残った run の両方を消せるようにする。
    // 実体を必須にすると、bus から消えた run のスナップショットが永久に消せず、一覧に居座る。
    let runDir = null;
    let status = 'archived';
    let via = null;
    const hasRun = fs.existsSync(path.join(busDir, 'runs', runId, 'meta.json'));
    if (hasRun) {
      const prep = flow.prepareRunDeletion(busDir, runId);   // 実行中ならここで拒否される
      runDir = prep.runDir;
      status = prep.status;
      via = await removeToTrash(runDir);
    }
    const archived = dir ? flow.removeArchivedRun(dir, runId) : null;
    if (!via && !archived) throw new Error(`run が見つかりません: ${runId}`);
    return { runDir, status, via, archived };
  });

  // run のキャンセル（人の明示アクション＝唯一の hard-stop）。cancel マーカーを inbox へ置き
  // （git 同期で他 PC / daemon へ伝わる）、run の meta を canceled に確定し、park 済みノードの
  // 再ポーリングを止める。承認待ちで park 中の run も暴走中の run も止められる。起票済みイシューは
  // 残す（追跡だけやめる＝agent-flow の既定）。イシュークローズは daemon の cancel --close-issues か
  // gitlab-review-viewer に任せる（この viewer の GitLab クライアントは読み取り専用）。
  handle('flow:cancel', async ({ dir, busDir, runId, reason }) => {
    const res = flow.cancelRun(busDir, runId, { reason: reason || '' });
    // bus だけ canceled にしても project が offloaded / flow_run のままだと UI が割れる。
    // revise（feedback）コマンドで本体と同じ detach→ready 契約に乗せる。
    // 既に終端の run への「中止」は waits 掃除だけで、タスクを ready に積み直さない
    // （done/failed/canceled の archival cancel で settled タスクが再キューされるのを防ぐ）。
    if (dir && !(res && res.alreadyTerminal)) {
      const meta = flow.readRunMeta(busDir, runId) || {};
      const taskId = flow.taskIdOfRun(runId, meta);
      if (taskId && fs.existsSync(path.join(dir, 'backlog', `${taskId}.md`))) {
        try {
          await actions.runAction(loadConfig(), {
            dir,
            action: 'revise',
            id: taskId,
            feedback: `agent-dashboard が run ${runId} をキャンセル`,
            reason: reason || `cancel ${runId}`,
          });
        } catch {
          /* タスク同期失敗は cancel 自体の失敗にしない */
        }
      }
    }
    return res;
  });

  // 不要なバックログタスクの削除（人の明示アクション）。backlog/<id>.md だけを
  // 対象にし、実行中（doing かつクレームあり）のタスクは拒否する。
  // クレームロック（claims/<id>.lock）は worker のクラッシュや review/blocked
  // 滞留で残骸が残るため（agent-project 本体も approve 時に掃除する）、
  // ロックの存在だけでは拒否せず、削除時に残骸ロックも一緒に片付ける。
  // agent-project に削除の公式契約は無いため、ファイルをゴミ箱へ移動する
  handle('dashboard:deleteTask', async ({ dir, id }) => {
    const tid = String(id || '');
    if (!tid || tid !== path.basename(tid)) throw new Error(`不正なタスク ID です: ${id}`);
    const file = path.join(dir, 'backlog', `${tid}.md`);
    if (!fs.existsSync(file)) throw new Error(`タスクファイルがありません: ${file}`);
    const lockFile = path.join(dir, 'claims', `${tid}.lock`);
    const status = project.parseTask(fs.readFileSync(file, 'utf8'), tid).status;
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
  // ゴミ箱へ移動し、バスの agent-flow daemon を停止する。charter は「プロジェクト全体の前提
  // （マスター）」として残す＝分解されないので、リセット後は待機状態になり作業は計画バージョンの
  // 追加で再開する（初版 charter からマイルストーンが出てこない）。
  // 順序は「daemon 停止 → charter をマスター化 → 削除」:
  //   - 先に daemon を止めないと worker が消したバスへ結果を書き戻す。
  //   - 削除の前に charter をマスター化しておくと、削除中に本体が非マスター charter を分解して
  //     マイルストーンを作る取りこぼしを防げる（削除がその残骸も一緒に片付ける）。
  // ドット始まりの同期内部（.state-git 等）は温存する — 管理クローンの manifest が残る
  // ことで、削除が次の同期で「ローカルの削除」としてリモートへ伝播する（データ復活を防ぐ）。
  // dir はプロジェクトルート（状態の置き場）、workspace は登録フォルダ（設定 .agent/ の在り処。
  // バスを設定 bus: から引くのに要る）。workspace 省略時はプロジェクトルートで代用する。
  handle('dashboard:reset', async ({ dir, workspace }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    const cfg = loadConfig();
    const plan = reset.planReset(dir);
    const bus = project.resolveBusDir(dir, workspace || dir, cfg);
    const daemon = await flow.stopDaemon(bus.busDir, flowLockDir(cfg));
    let masterized = false;
    try {
      const info = authoring.readProjectFile(dir, 'charter.md');
      if (info.exists) {
        const fields = authoring.charterToFields(info.content);
        if (!fields.master) {
          fields.master = true; // マスター化（fieldsToCharter は master のとき acceptance を書かない）
          authoring.writeProjectFile(dir, 'charter.md', authoring.fieldsToCharter(fields));
          masterized = true;
        }
      }
    } catch {
      /* マスター化に失敗してもリセット自体は続行する */
    }
    const res = await reset.executeReset(plan, removeToTrash);
    return { ...res, daemon, masterized, busDir: bus.busDir, busSource: bus.source };
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
  handle('dashboard:feedback', ({ file, feedback, stub }) => actions.submitFeedback(file, feedback, stub));
  handle('dashboard:enqueue', ({ dir, spec }) => actions.enqueueToInbox(dir, spec || {}));
  handle('dashboard:action', (args) => actions.runAction(loadConfig(), args));

  // charter からのバックログ再分解を要求（エラー回復・やり直し）。プロジェクト単位（id 無し）。
  // 本体が次パスで charter を分解し直す。冪等照合は「done 以外」（処理中＋却下済み）と行い、
  // done と類似のタスクだけ再作成を許可する（過去の完了実績がやり直しを弾かない）。
  handle('dashboard:replan', ({ dir, reason }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.requestReplan(loadConfig(), { dir, reason });
  });

  // プロジェクト単位のライフサイクル操作（pause / resume / stop）。commands/ ドロップ
  // （＋都度 push）で届け、リモート本体（WSL・別ホスト）の watch が同期間隔内に取り込む。
  handle('dashboard:lifecycle', ({ dir, action, reason }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.requestLifecycle(loadConfig(), { dir, action, reason });
  });

  // 本体（agent-project）の起動。停止中の本体は commands/ を読めないため、ファイルドロップ
  // でなくこの PC の CLI で `agent-project start` を実行する（detach され即座に戻る）。
  handle('dashboard:start', ({ dir }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.startProject(loadConfig(), { dir });
  });

  // オーサリング（作成・編集）。人が書く上位入力ファイル（charter/policy/repos）だけを
  // 対象にし、タスク状態は触らない（done は verify のみが根拠の不変条件を壊さない）。
  //   createProject … <root>/projects/<name>/ に charter.md（＋ repos.json）を作る
  //   readFile/writeFile … charter.md / policy.md / repos.* の直接編集
  handle('dashboard:createProject', ({ spec }) => authoring.createProject(spec || {}));
  // 初版 charter.md へ後からバージョン名を付ける（charters/<name>.md へ昇格）。
  // charters/ 運用では charter.md が駆動対象から外れるため、初版を並行駆動に含める正規の口。
  handle('dashboard:promoteCharter', ({ dir, name }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return authoring.promoteCharterVersion(dir, name);
  });
  handle('dashboard:readFile', ({ dir, name }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return authoring.readProjectFile(dir, name);
  });
  handle('dashboard:writeFile', ({ dir, name, content }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return authoring.writeProjectFile(dir, name, content);
  });

  // charter.md の雛形（新規・空ファイル編集時の挿入用。authoring.buildCharter と同一の書式）
  handle('dashboard:charterTemplate', ({ name }) => ({
    content: authoring.buildCharter({ name: String(name || '').trim() || 'project' }),
  }));

  // フォーム編集: charter / policy / repos を構造化データで読み書きする（マークダウン/JSON を
  // ユーザーに直接書かせず、入力欄で編集するための橋渡し。パース・シリアライズは authoring が持つ）。
  handle('dashboard:readCharterFields', ({ dir, name }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    const info = authoring.readProjectFile(dir, name);
    return { fields: authoring.charterToFields(info.content || ''), exists: info.exists, file: info.file };
  });
  handle('dashboard:writeCharterFields', ({ dir, name, fields }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return authoring.writeProjectFile(dir, name, authoring.fieldsToCharter(fields || {}));
  });
  handle('dashboard:readPolicy', ({ dir }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    const info = authoring.readProjectFile(dir, 'policy.md');
    return { rules: authoring.policyToRules(info.content || ''), exists: info.exists, file: info.file };
  });
  handle('dashboard:writePolicy', ({ dir, rules }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return authoring.writeProjectFile(dir, 'policy.md', authoring.rulesToPolicy(rules || []));
  });
  handle('dashboard:readRepos', ({ dir }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    const info = authoring.readProjectFile(dir, 'repos.json');
    return { rows: authoring.reposJsonToRows(info.content || ''), exists: info.exists, file: info.file };
  });
  handle('dashboard:writeRepos', ({ dir, rows }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    // フォーム編集は _meta 無し（手管理）で書く＝ repos.json が正になり本体が上書きしない
    return authoring.writeProjectFile(dir, 'repos.json', authoring.exportReposJson(rows || [], false));
  });

  // エージェント CLI（kiro / claude / copilot）による charter の下書き・補完。
  // 応答テキストを返すだけで、ファイルへの書き込みは既存の dashboard:writeFile /
  // dashboard:createProject（人の保存操作）に任せる。dir はエージェント解決
  // （プロジェクトの agent-project.yaml の agent_cli / model に従う）にだけ使う。
  handle('agent:charter', ({ dir, mode, spec, content }) =>
    agent.completeCharter(loadConfig(), { dir, mode, spec, content })
  );

  // 現在画面のスナップショットを読み取り専用CLIへ渡し、助言本文だけを返す。
  handle('agent:doctor', ({ dir, context }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    if (!context || typeof context !== 'object') throw new Error('画面の状態が指定されていません');
    return agent.completeDoctor(loadConfig(), { dir, context });
  });

  // ⚙ 設定画面の表示用: 今どの CLI / モデルで補完するかの解決結果（実行はしない）
  handle('agent:resolve', ({ dir }) => agent.resolveAgent(loadConfig(), dir));

  // gitlab-review-viewer へレビューを引き継ぐ
  handle('review:open', ({ target }) => openInReviewViewer(loadConfig(), target || {}));

  handle('shell:openExternal', ({ url }) => {
    if (!/^https?:\/\//.test(url)) throw new Error(`外部で開けない URL です: ${url}`);
    return shell.openExternal(url);
  });

  handle('shell:openPath', ({ target }) => shell.openPath(target));
}

module.exports = { registerIpcHandlers };
