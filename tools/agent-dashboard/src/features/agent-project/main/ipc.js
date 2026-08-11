'use strict';

// agent-project / agent-flow 制御面の IPC。
// base/main/ipc.js が features 経由で registerIpc(ctx) を呼ぶ。

const fs = require('fs');
const path = require('path');
const project = require('./project');
const engine = require('./engine');
const flow = require('./flow');
const git = require('../../../base/main/git');
const { openInReviewViewer } = require('./review');
const actions = require('./actions');
const authoring = require('./authoring');
const agent = require('./agent');
const reset = require('./reset');

const MAX_BACKLOG_DOCUMENT_BYTES = 64 * 1024;

async function pickBacklogDocument(dialog) {
  if (!dialog || typeof dialog.showOpenDialog !== 'function') {
    throw new Error('ファイル選択を利用できません');
  }
  const selected = await dialog.showOpenDialog({
    title: 'バックログ候補を作る文書を選択',
    properties: ['openFile'],
    filters: [{ name: 'Markdown / テキスト', extensions: ['md', 'txt'] }],
  });
  const file = selected && selected.filePaths && selected.filePaths[0];
  if (selected.canceled || !file) return { canceled: true };
  if (!['.md', '.txt'].includes(path.extname(file).toLowerCase())) {
    throw new Error('選択できるのは .md または .txt ファイルです');
  }
  const size = fs.statSync(file).size;
  if (size > MAX_BACKLOG_DOCUMENT_BYTES) {
    throw new Error(`文書が64 KiBを超えています（${Math.ceil(size / 1024)} KiB）。小さく分けてください`);
  }
  const bytes = fs.readFileSync(file);
  let content;
  try {
    content = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    throw new Error('文書をUTF-8テキストとして読めませんでした');
  }
  if (!content.trim()) throw new Error('文書が空です');
  if (content.includes('\0')) throw new Error('テキスト文書ではない内容が含まれています');
  return { canceled: false, name: path.basename(file), content };
}

// ゴミ箱へ移動（可能な環境ではリカバリできる）。ゴミ箱が無い環境では完全削除
async function removeToTrash(shell, target) {
  try {
    await shell.trashItem(target);
    return 'trash';
  } catch {
    fs.rmSync(target, { recursive: true, force: true });
    return 'delete';
  }
}

function registerIpc(ctx) {
  const { handle, loadConfig, shell, client, dialog } = ctx;
  const trash = (target) => removeToTrash(shell, target);

  // 発見: 実行エンジンの状況ファイル（engine/status.json）に載っているプロジェクト
  handle('dashboard:discover', () => project.discover(loadConfig()));

  // 実行エンジンの状況（稼働・共有の進み具合・直近のエラー・切り離したプロジェクト）。
  // 画面の稼働表示・同期表示はすべてこの 1 枚が根拠（実装計画 W2-3・W2-5）。
  handle('engine:status', () => {
    const status = engine.readStatus(loadConfig());
    return { ...status, ...engine.summarize(status) };
  });

  // 「今すぐ同期」。自動回復が既定で、これはその前倒し——投函するだけで実行は常駐体が行う。
  handle('engine:heal', ({ dir }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    const { file } = actions.dropCommand(dir, { action: 'heal', reason: 'agent-dashboard から今すぐ同期' });
    return { file, via: 'file' };
  });

  // セットアップ診断: 発見したプロジェクトの置き場が共有できる状態かを赤/緑で返す。
  handle('setup:diagnostics', () => {
    const cfg = loadConfig();
    return git.diagnostics(cfg.role, engine.projectRoots(cfg));
  });

  // 1 プロジェクトの完全スナップショット（バスの発見に設定 projects.flowBus も使う）
  handle('dashboard:project', ({ dir }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return project.readProject(dir, loadConfig());
  });

  handle('dashboard:pickBacklogDocument', () => pickBacklogDocument(dialog));

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
    const live = new Set(allLive.map((r) => r.runId));
    if (dir) {
      for (const r of allLive) {
        try {
          flow.archiveRunSnapshot(dir, busDir, r);
        } catch {
          /* アーカイブ失敗は一覧表示の失敗にしない */
        }
        // リトライ（世代交代）で bus から削除された旧世代の墓標（runs/<新>/inherited/）も
        // アーカイブへ写す。viewer が削除の瞬間にポーリングしていなくても旧世代の成果記録が
        // 残る。live 中にポーリングで撮れた終端スナップショット（イベント付き・より詳しい）が
        // 既にあればそちらを正とし、無い/実行途中の古い写ししか無いときだけ墓標で置き換える。
        let tombs = [];
        try {
          tombs = flow.readInheritedTombstones(busDir, r.runId);
        } catch {
          /* 墓標の読込失敗も一覧表示の失敗にしない */
        }
        for (const t of tombs) {
          if (live.has(t.runId)) continue;
          try {
            const existing = flow.readArchivedRun(dir, t.runId);
            const st = existing && existing.run ? String(existing.run.status || '') : '';
            if (!existing || !flow.TERMINAL.has(st)) flow.archiveRunSnapshot(dir, busDir, t);
          } catch {
            /* 補完失敗も致命的でない */
          }
        }
      }
    }
    const archived = dir
      ? flow.listArchivedRuns(dir).filter((a) => !live.has(a.runId))
      : [];
    const merged = [...runs, ...archived].sort((a, b) =>
      String(b.createdAt || '').localeCompare(String(a.createdAt || ''))
    );
    return { runs: merged };
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
  handle('flow:interactionResponse', ({ busDir, runId, interactionId, answer }) =>
    flow.writeInteractionResponse(busDir, runId, interactionId, answer));

  // 失敗した run の「やり直し」。
  //
  // agent-project 配下の run なら、bus へ投げ直すのではなく **タスクを積み直す**。
  // bus/inbox は板から受けた委譲を拾う契約で、agent-project 自身は run を都度起動する。
  // そこへ投入しても誰も拾わない＝押しても
  // 何も起きないボタンになる。しかも inbox 投入は agent-project のタスク状態に触らないため、
  // 仮に走っても結果が settle されず、タスクは doing のまま取り残される。
  // タスクを ready へ戻せば agent-project が新しい run を起こし、結果も正しく回収する。
  // （run-id にはタスク ID が埋まっている: req-<hash>-<task-id>-r<n>）
  //
  // agent-flow を単体で使っている run（タスクに紐づかない）は従来どおり inbox へ。
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
      via = await trash(runDir);
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

  // 不要なバックログタスクの削除（人の明示アクション）＝物理削除（ゴミ箱へ移動）。
  // 「作り直させない」意思表示は却下（reject）が担う（requestDeleteTask のコメントに理由）。
  handle('dashboard:deleteTask', async ({ dir, id, reason }) =>
    actions.requestDeleteTask(loadConfig(), { dir, id, reason }, trash));

  // 墓標の解除（削除＝却下の取り消し）。同じ題のタスクを入れ直せる状態へ戻す。
  handle('dashboard:revive', async ({ dir, title, charter }) =>
    actions.requestRevive(loadConfig(), { dir, title, charter }));

  // プロジェクトのリセット（人の明示アクション・危険操作）。charter.md 以外の全データを
  // ゴミ箱へ移動する。charter は「プロジェクト全体の前提
  // （マスター）」として残す＝分解されないので、リセット後は待機状態になり作業は計画バージョンの
  // 追加で再開する（初版 charter からマイルストーンが出てこない）。
  // 順序は「charter をマスター化 → 削除」:
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
    const res = await reset.executeReset(plan, trash);
    return { ...res, masterized, busDir: bus.busDir, busSource: bus.source };
  });

  // 実行中ノードの関連イシューを決定的タスクトークンで検索（gitlab executor 連動）
  handle('gitlab:findIssueByToken', ({ repoUrl, projectPath, token }) => {
    const gl = client();
    if (!gl.enabled) return { enabled: false, issue: null };
    return gl
      .findIssueByToken({ repoUrl, projectPath, token })
      .then((issue) => ({ enabled: true, issue }));
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


  // 監視担当の割り当て（チーム運用）。assignments.json（viewer 管理のサイドカー）だけを
  // 書き、タスク状態ファイルには触れない。空の owner で割り当て解除。
  handle('dashboard:setOwner', ({ dir, id, owner }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.setTaskOwner(dir, id, owner);
  });

  // 成果物レビューのコメント（reviews/<task-id>/）。複数メンバーが投稿し、監視担当者が
  // 確認・整理して承認/再実行を判断する。1 コメント = 1 ファイル（同時投稿が衝突しない）。
  handle('dashboard:addComment', ({ dir, taskId, author, text }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.addReviewComment(dir, taskId, { author, text });
  });
  handle('dashboard:editComment', ({ dir, taskId, commentId, text }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.editReviewComment(dir, taskId, commentId, text);
  });
  handle('dashboard:deleteComment', ({ dir, taskId, commentId }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.deleteReviewComment(dir, taskId, commentId, trash);
  });

  // 人のアクション（needs 回答・タスク投入・決定記録を残す CLI 操作）
  handle('dashboard:feedback', ({ file, feedback, stub }) => actions.submitFeedback(file, feedback, stub));
  handle('dashboard:enqueue', ({ dir, spec }) => actions.enqueueToInbox(dir, spec || {}));
  // 作成時 lint（案5）: 投入前に情報不足・曖昧 accept を警告する（非ブロック。判断は監視者）。
  handle('dashboard:lintTask', ({ spec }) => authoring.lintTaskSpec(spec || {}));
  handle('dashboard:action', (args) => actions.runAction(loadConfig(), args));

  // charter からのバックログ再分解を要求（エラー回復・やり直し）。プロジェクト単位（id 無し）。
  // 本体が次パスで charter を分解し直す。冪等照合は「done 以外」（処理中＋却下済み）と行い、
  // done と類似のタスクだけ再作成を許可する（過去の完了実績がやり直しを弾かない）。
  handle('dashboard:replan', ({ dir, reason, charter }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.requestReplan(loadConfig(), { dir, reason, charter });
  });

  // 観点メモ（notes/）。plan は自動では消費しないので、書いても計画は勝手に動かない。
  // 分解は人が明示的に押したときだけ（commands/distill-notes ドロップ）。
  handle('dashboard:listNotes', ({ dir }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.listNotes(dir);
  });
  handle('dashboard:writeNote', ({ dir, name, body }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.writeNote(dir, { name, body });
  });
  handle('dashboard:updateNote', ({ dir, name, body, mtime }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.updateNote(dir, { name, body, mtime });
  });
  handle('dashboard:markNoteBlocks', ({ dir, name, links }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.markNoteBlocks(dir, { name, links });
  });
  handle('dashboard:distillNotes', ({ dir, charter }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.requestDistillNotes(loadConfig(), { dir, charter });
  });

  // プロジェクト単位のライフサイクル操作（pause / resume / stop）。commands/ ドロップ
  // （＋都度 push）で届け、リモート本体（WSL・別ホスト）の watch が同期間隔内に取り込む。
  handle('dashboard:lifecycle', ({ dir, action, reason }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return actions.requestLifecycle(loadConfig(), { dir, action, reason });
  });

  // 本体を CLI で起こす経路（旧 dashboard:start）は削除した（実装計画 W2-2）。
  // 実行エンジンの起動・再起動は OS の起動系（systemd 等）の管轄で、この画面は
  // 止まっていることを案内表示するところまでを担う。

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
  handle('dashboard:deleteCharter', ({ dir, name }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return authoring.deleteCharterVersion(dir, name);
  });
  handle('dashboard:readFile', ({ dir, name }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return authoring.readProjectFile(dir, name);
  });
  handle('dashboard:writeFile', ({ dir, name, content }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    return authoring.writeProjectFile(dir, name, content);
  });

  // charter.md の雛形（新規・空ファイル編集時の挿入用。authoring.buildCharter と同一の書式）。
  // version 指定（charters/<name>.md 用）は空の制約・前提見出しを省く＝そのまま保存しても
  // マスターの制約・前提が「空に上書き」されない。
  handle('dashboard:charterTemplate', ({ name, version }) => ({
    content: authoring.buildCharter({ name: String(name || '').trim() || 'project', version: !!version }),
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
    // 実効レジストリは yaml → yml → json の優先順（本体と同じ）。yaml/yml が正のときは
    // フォームで repos.json を書いても本体に無視されるため、yamlFile を返して
    // レンダラを生テキスト編集へ誘導する（フォームでの読み書きはしない）。
    const name = authoring.reposFileName(dir);
    if (name !== 'repos.json') {
      const info = authoring.readProjectFile(dir, name);
      return { rows: [], exists: info.exists, file: info.file, yamlFile: name, generated: false };
    }
    const info = authoring.readProjectFile(dir, 'repos.json');
    return {
      rows: authoring.reposJsonToRows(info.content || ''),
      exists: info.exists,
      file: info.file,
      generated: info.generated,
    };
  });
  handle('dashboard:writeRepos', ({ dir, rows }) => {
    if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
    const name = authoring.reposFileName(dir);
    if (name !== 'repos.json') {
      throw new Error(`このプロジェクトは ${name} が正です。フォームではなくテキスト編集で ${name} を編集してください`);
    }
    authoring.validateRepoRows(rows || []);
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

  // 実行手法のカスタム追加フォームを補完する。提案だけを返し、保存は renderer の確定操作に任せる。
  handle('agent:methodDraft', ({ dir, brief, current }) =>
    agent.completeMethodDraft(loadConfig(), { dir: dir || null, brief, current: current || {} })
  );

  // 定期プロンプトの本文と受入条件の下書きを作る。段（tier）を選んで上位のエージェントに
  // 書かせられる。**下書きだけ**を返し、agent-loop.yaml への確定は人の承認操作に任せる。
  handle('agent:routineAcceptanceDraft', ({ dir, name, prompt, extra, tier }) =>
    agent.completeRoutineAcceptance(loadConfig(), {
      dir: dir || null, name: name || '', prompt: prompt || '',
      extra: extra || '', tier: tier || '',
    })
  );

  // 現在画面のスナップショットを読み取り専用CLIへ渡し、助言本文だけを返す。
  handle('agent:doctor', ({ dir, context, userPrompt, mode }) => {
    if (!context || typeof context !== 'object') throw new Error('画面の状態が指定されていません');
    return agent.completeDoctor(loadConfig(), { dir: dir || null, context, userPrompt, mode });
  });

  // 失敗診断を tmux の対話セッションで開く（S9-4）。ヘッドレスの agent:doctor と**同じ文脈**を
  // 受け取り、渡し方だけを変える（ブリーフ 1 行 ＋ 全文ファイルのパス）。読み取り専用・
  // セッション永続化なしで起動し、セッション名も作業用と別系統にする。
  handle('agent:doctorChat', ({ dir, context, needId, userPrompt }) => {
    if (!context || typeof context !== 'object') throw new Error('画面の状態が指定されていません');
    return agent.openDoctorChat(loadConfig(), { dir: dir || null, context, needId, userPrompt });
  });

  // フォローアップ案・依存/優先度提案。JSON を返すだけで inbox / backlog には書かない。
  handle('agent:taskAssist', ({ dir, mode, context, userPrompt }) => {
    if (!context || typeof context !== 'object') throw new Error('補助コンテキストが指定されていません');
    return agent.completeTaskAssist(loadConfig(), {
      dir: dir || null,
      mode,
      context,
      userPrompt,
    });
  });

  // 既存タスク調整案 → revise 差分の純関数（人確認前のプレビュー用。ファイルは書かない）。
  handle('agent:planAdjustments', ({ backlog, adjustments }) =>
    agent.planBacklogAdjustments(backlog || [], adjustments || [])
  );

  // ⚙ 設定画面の表示用: 今どの CLI / モデルで補完するかの解決結果（実行はしない）
  handle('agent:resolve', ({ dir }) => agent.resolveAgent(loadConfig(), dir));

  // 保存済みの全体エージェント設定で、選択中ワークスペースの対話CLIを外部 tmux に開く。
  // cwd は省略でプロジェクト（状態リポジトリ）、指定でこのノードの成果物クローン等（S3-4）。
  handle('agent:openChat', ({ dir, cwd }) => {
    if (!dir) throw new Error('プロジェクトを選択してください');
    return agent.openInteractiveChat(loadConfig(), dir, cwd);
  });

  // CLIチャットの起動先候補（プロジェクト + このノードにクローンがある成果物リポジトリ）。
  handle('agent:chatCwdChoices', ({ dir }) => agent.chatCwdChoices(loadConfig(), dir));

  // gitlab-review-viewer へレビューを引き継ぐ
  handle('review:open', ({ target }) => openInReviewViewer(loadConfig(), target || {}));


}

module.exports = { registerIpc, pickBacklogDocument, MAX_BACKLOG_DOCUMENT_BYTES };
