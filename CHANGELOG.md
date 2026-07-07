# CHANGELOG

All notable changes to this project are documented in this file.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) — versions use [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### kiro-projects: 管理 daemon の state_git サブディレクトリを設定可能に（`flow_state_subdir`）

- `manage_flow_daemon` で起動する kiro-flow daemon の `--state-git-subdir` は `kiro-flow` にハードコード
  されており、`flow_config` 経由の kiro-flow.yaml で `state_git_subdir` を変えても CLI 注入に上書きされて
  効かなかった。設定 `flow_state_subdir`（既定 `kiro-flow`）で変更できるようにした。
- 補足: run の実行はバスから行われるため、サブディレクトリを変えても **run は止まらない**（変わるのは
  鏡写し先のパスだけ。viewer が別サブディレクトリを見ていると run が「見えない」ことはある）。
  README / `kiro-projects.yaml.example` / 移行手順書の FAQ に注記、テストを追加。

### kiro-projects-viewer: タスクグラフノードのイシュー状態を自動表示（クリック不要）

- **背景**: 関連イシューの「今」の状態は GitLab API 由来のため、従来は「⟳ GitLab と突き合わせ」
  ボタンを押さないとノードに出なかった（グラフ状態は bus のファイルだけから作るため）。
- **自動突き合わせ**: run を開いたとき／ポーリング更新時に、GitLab 設定済みなら**一度だけ自動で
  突き合わせ**る（同一 run は **60 秒の律速**でキャッシュを使い、ポーリング毎回は叩かない）。
  結果は **run 単位でキャッシュ**し、run を切り替えても保持する（再取得を避ける）。
- **オープン中イシューも表示**: 突き合わせ結果にクローズ済みだけでなく**オープン中（レビュー待ち）**の
  イシューも含め、ノードに「レビュー中」チップとイシューアイコン（青系）を出す。クローズ済みは
  従来どおり完了/失敗を先読み反映（承認/却下）。ノード詳細のチップも 却下／承認／レビュー中／
  クローズ を明示的に色分けする。
- 手動ボタンは「⟳ GitLab 最新化」に改称（自動取得の即時再取得用）。追加の API 呼び出しは
  非終端ノードのみ・最大 40 件・直列・60 秒律速で有界。

### kiro-projects-viewer: 状態共有 git への push が黙ってスキップされる問題を可視化

- **バグ修正**: ユーザー操作の状態共有 git 反映（`gitAutoPush`）が、操作したディレクトリが
  **git 作業ツリーでない**と `commitPush` の `notRepo` で**黙ってスキップ**され、変更が共有
  リポジトリへ反映されないのに何も知らされなかった。最初に run（バス）削除で表面化したが、
  **バックログ修正・タスク操作・needs 記入など `p.dir` への操作も同じ**で、本体の state_git が
  「作業ディレクトリ→別クローン」方式で同期する構成では作業ディレクトリ自体が git リポジトリでない
  ため、viewer からは直接 push できず daemon 側の state_git 同期に委ねられる（バスは
  `_STATE_EXCLUDE_DIRS = {"bus","claims"}` で本体 state_git から除外され、kiro-flow 側が別クローンへ
  同期）。
- **対応**: `notRepo` スキップの検知を `gitPushAfterWrite` に集約し、**全操作**で「共有リポジトリへ
  直接反映できなかった／daemon の state_git 同期に委ねられる／viewer から直接反映するには git
  クローン上でプロジェクト（バスは `flowBusByProject`）を開く」ことをトーストで明示する（**沈黙の
  no-op をなくす**）。通知は**ディレクトリごとに一度だけ**（操作のたびには出さない）。git 追跡下の
  作業ツリー（pure-remote 構成・`flowBusByProject` の `<clone>/kiro-flow`）では従来どおり
  コミット・push される。
- `gitPushAfterWrite` は commitPush の結果 Promise を返すようにした（従来の fire-and-forget
  呼び出しは戻り値を無視するだけで挙動不変）。バス操作は `gitPushBusOp`（`kind:'bus'` でヒント切替）。

### kiro-projects-viewer: gitlab executor のクローズ済みイシューをタスクグラフへ反映

- **バグ修正**: gitlab executor の場合、関連イシューが GitLab で既にクローズ（承認/却下で決着）
  されていても、worker が決着ループでそれを検知して `result` を bus に書くまでは、タスクグラフの
  ノードが「実行中」のまま完了表示にできなかった。非ブロッキング委譲（`act_async`）＋PC の日次停止
  などで worker が止まっている間に人がイシューを承認クローズするケースで顕著。
- **対応**: RUN 概要に **「⟳ GitLab と突き合わせ」** を追加。その run の非終端ノードの関連イシュー
  （本文の決定的タスクトークンで検索）を GitLab の「今」の状態と突き合わせ、クローズ済みなら
  **executor と同一規則**（関連 MR の状態 → `status:approved`/`status:done` ラベル → 人コメントの
  承認/却下語。手掛かり無しのクローズは取り下げ＝却下）で承認/却下を判定し、ノードを
  **完了/失敗として先読み反映**する。判定ロジックは `flow.js` の純関数
  `reconcileNodeState`（executors/gitlab.py の `_mr_decision` / `_closed_issue_decision` /
  `_decision_from_comments` と一致）に切り出し、単体テスト（`test/flow-reconcile.test.js`）で固定。
- **表示**: 反映されたノードはタスクグラフで**破線枠**、ノード詳細で「GitLab 反映」チップと注記で
  区別する（bus が常に正で、反映は暫定表示。bus に `result` が届けば通常表示へ確定）。反映で URL が
  判明したノードには、グラフのイシューアイコン（1クリックでレビュー起動）も出るようにした。

### kiro-projects-viewer: 非ブロッキング委譲（`offloaded`）の表示対応

- **バグ修正**: パーサの既知ステータス一覧に `offloaded` が無く、offloaded タスクが既定 `inbox` に
  化けていた（`TASK_STATUSES` に追加）。
- **表示整合**: 概要タブのステータスタイル（`STATUS_ORDER`）・バックログのフィルタ（`BACKLOG_FILTERS`）に
  `offloaded` を追加。status-chip / tile に `.st-offloaded` 色（doing と同系＝機械稼働中）を追加。
- **run 連携**: offloaded タスクは `flow_run`（委譲先 run-id）を持つので、バックログ行に「▶ run」バッジ、
  タスク詳細の `flow_run` をクリックでフロータブの該当 run へ移動できるようにした。extras に
  「委譲実行中: <loc>」を表示。revise ダイアログに offloaded 用の注記（反映は run 完了時）を追加。

### kiro-projects: 非ブロッキング委譲（`act_async`）— gitlab 長期委譲でループを塞がない

- **背景**: `executor: gitlab` は MR 承認まで数日かかる。従来は act が結果を待つ（ブロック）ため、
  `act_timeout`（既定 30 分）が承認より先に切れて「タイムアウト→retry」を繰り返し、他タスクも
  待たされていた。専用 daemon が run を保持するようになったので、**待たずに次へ進める**ようにした。
- **`act_async`（opt-in）**: daemon/remote への submit で**結果を待たず**タスクを新状態 `offloaded` に退避し、
  次パスで `kiro-flow result` を1回だけポーリングして**終端した run だけ settle**する（未終端は次パスへ）。
  ループを塞がないので、同じプロジェクトの他タスクや他プロジェクトを並行に進められる。run の本当の
  失敗（却下・orchestrator 異常）は終端ステータスで検知されるため、待ち上限（タイムアウト）を安全網に
  する必要がない＝`act_timeout: 0` ＋ kiro-flow `gitlab.timeout/approved_timeout: 0` と併用で
  **誤タイムアウト由来の retry ループが完全に消える**。
- submit は決定的 run_id なので、`offloaded` のまま kiro-projects が再起動しても同じ run に再合流する
  （二重実行・イシュー二重起票なし）。`offloaded` は watch を起こし続け（ポーリング継続）、CONSUMABLE
  ではない（再 submit しない）。既定 off＝**完全後方互換**（従来どおり同期で待つ）。
- CLI `--act-async`、設定 `act_async`。テストと `*.yaml.example`（gitlab 委譲サンプル）を更新。

### kiro-projects / viewer: プロジェクト単位で保存先リポジトリを分ける（`state_git_projects`）

- **背景・目的**: これまで状態の git 同期（`state_git`）は**コンテナ丸ごと**（全プロジェクト）を 1
  リポジトリへ同期していた。プロジェクトごとに**別々のリポジトリ**へ分け、プロジェクト固有リポジトリで
  kiro-projects / kiro-flow の情報をメンバーと共有し、誰でも kiro-projects-viewer でドライブできるように
  する。`default` はユーザー個人リポジトリで管理し、他プロジェクトはプロジェクト固有リポジトリで共有・
  可視化する構成。**使う人ごとにアサインされるプロジェクトが違う点は、各自の設定で写像を変えるだけ**で
  吸収できる（リポジトリの設定で解決）。
- **kiro-projects の状態**: 設定 `state_git_projects`（`{プロジェクト名: URL/パス}` または
  `{名前: {remote/branch/subdir/interval}}`）を追加。写像に載ったプロジェクトは**そのプロジェクトの
  subtree だけ**をスコープして固有リポジトリ（`<subdir>/projects/<name>/…`。従来レイアウトを維持）へ
  同期し、未記載（`default` 含む）は既定の `state_git`（個人リポジトリ・未設定なら無効）へ落ちる。
  各プロジェクトは自分専用の管理クローン（`<container>/projects/<name>/.state-git`）を使い、多重
  コミッタの護りはそのまま。写像未設定なら従来どおりコンテナ丸ごと（**完全後方互換**）。
- **実行層 kiro-flow の run（kiro-flow は無改修）**: kiro-flow に「プロジェクト」の概念は持ち込まない。
  代わりに **kiro-projects が per-project の kiro-flow daemon を起動・監視**し、「このバスを、このプロジェクト
  のリポジトリの `kiro-flow` 名前空間へ鏡写しせよ」を**daemon 起動時の CLI（`--bus`/`--state-git*`）で
  注入**する（kiro-flow 側の設定ファイルや宣言ファイルは不要）。設定 `manage_flow_daemon: true`（opt-in）で
  watch ループが各プロジェクトの daemon を不在なら起動（バスロックで冪等）、`flow_max_workers` をマシン
  全体の予算として対象プロジェクト数で割り各 daemon の上限にする。`flow_config` で共有 kiro-flow.yaml を
  `--config` として渡せる。**kiro-projects を止めても daemon は detached で残る**ので、in-flight run
  （gitlab の長期委譲・夜間停止からの孤児再開）は daemon 側でそのまま継続し、再起動時はロックで再検知して
  二重起動しない。`doctor` は各プロジェクトバスに daemon がいるかを warn で点検する。プロジェクト固有
  リポジトリは `kiro-projects/projects/<name>/`（状態）と `kiro-flow/`（run）の 2 名前空間を持つ。
- **kiro-projects-viewer**: コンテナ（`roots`）は従来から複数登録できるため、プロジェクト固有リポジトリの
  clone `<clone>/kiro-projects` を 1 行ずつ足すだけで全プロジェクトを 1 画面に束ねられる。フローバスは
  設定 `flowBusByProject`（⚙「プロジェクト単位バス」・`プロジェクト名 = <clone>/kiro-flow`）を追加し、
  pure-remote 監視でプロジェクトごとの kiro-flow clone を割り当てられるようにした（ローカル `<project>/bus`
  に `runs/` があればそちらを優先）。
- **テスト・ドキュメント**: kiro-projects の per-project 同期・裁定、kiro-flow daemon の起動コマンド注入・
  冪等・予算分配・doctor 点検、viewer のバス解決テストを追加。README と `*.yaml.example` に構成方法を追記。
  既存の 1 リポジトリ複数プロジェクト構成からの**移行手順書**
  [`docs/guides/migrate-per-project-repos.md`](docs/guides/migrate-per-project-repos.md) を追加。

### kiro-projects-viewer: バックログ操作の明確化（ボタン名・UI）と revise の柔軟化

- **背景**: 「＋ タスクを追加」が**バックログを 1 件追加する**機能だと UI から分かりにくかった
  （実体は inbox に 1 件投入 → 本体が次サイクルで `backlog/<id>.md` にする）。現状の設計思想
  （**公式契約だけを使い、タスク状態＝done は直接書かない**）は崩さず、名前と UI を分かりやすくした
- **ボタン名・UI の明確化**: 「＋ タスクを追加」→「**＋ バックログに追加**」に改称し、ダイアログ見出しも
  「バックログにタスクを 1 件追加（inbox 経由）」に。バックログタブに折りたたみヘルプ
  「バックログの変え方」を追加し、**追加＝inbox／変更＝revise／タスクグラフ再構築＝revise**、いずれも
  状態（done 等）は直接書き換えない、という関係を一貫して示す
- **revise の柔軟化（既存バックログの更新）**: 修正フォームに **note / level / track** を追加
  （kiro-projects の `REVISE_FIELDS` 全項目に対応）。依存 **after** の編集は従来どおり本体側が DAG 循環を拒否
- **タスクグラフ再構築の明示**: revise は本体が取り込むと `rev` を上げて kiro-flow に**新しいタスク
  グラフ（run の DAG）**を作らせる（実行中タスクは現在の試行を破棄して積み直し）ことを、修正フォームに明記
- **実装**: renderer の UI 文言・revise フォームのみの変更。**main 側の契約・kiro-projects 本体は変更なし**
  （追加は既存の `inbox` 投入、更新は既存の `commands/` revise のまま）

### kiro-projects-viewer / gitlab-review-viewer: 起動済み portable exe への即時ハンドオフ（連携起動の高速化）

- **症状**: kiro-projects-viewer（portable exe）の「レビューで開く」で `exe` モードを使うと、
  gitlab-review-viewer（portable exe）が**既に起動していても**引き継ぎ表示までに数秒かかる
- **原因**: portable exe を argv 付きで再起動すると、起動済みでも OS が毎回「自己展開（一時
  ディレクトリ）→ Electron 起動 → single-instance で argv 転送 → 即終了」の 2 個目プロセス
  立ち上げコストを必ず払う。argv 転送自体は機能するが、その前段の自己展開が遅い
- **変更**: gitlab-review-viewer が起動時に**ローカル IPC エンドポイント**（Windows: 名前付き
  パイプ／その他: Unix ドメインソケット。username から決定的に導出＝ユーザーごとに分離）を開き、
  `gitlab-review-viewer://…` を 1 行受け取ると `second-instance` と同じく対象を開く
  （`src/main/handoff.js`）。kiro-projects-viewer の `exe` モードは exe を spawn する前にこの
  エンドポイントへ接続を試み、**届けば URL を送るだけで即ハンドオフ**（exe を再起動しない・
  トーストは「起動中の gitlab-review-viewer に引き継ぎました」）。未起動＝接続失敗のときだけ
  従来どおり exe を起動する（cold start のときにだけ自己展開コストを払う）
- **後方互換 / 安全性**: 設定不要・自動。エンドポイント非対応の古い gitlab-review-viewer が
  相手でも接続に失敗して従来の argv 起動へ素通りする。ローカルユーザー限定ソケットで、扱う URL は
  `gitlab-review-viewer://` のみ（既存の argv / protocol 経路と同じ信頼境界）。アプリ終了時に閉じる
- **実装**: gitlab-review-viewer に `src/main/handoff.js`（サーバ）を追加し main で起動/停止。
  kiro-projects-viewer に electron 非依存の `src/main/reviewHandoff.js`（クライアント）を追加し
  `review.js` の `exe` モードから利用。両側のエンドポイント導出一致と往復を検証する
  `test/review-handoff.test.js`（クライアントとサーバを実ソケットでつなぐ）を追加

### kiro-projects-viewer: プロジェクトの新規作成・上位入力ファイルの編集・archive タスクの再投入

- **背景**: これまでビュアーは既存プロジェクトの**閲覧**と、公式契約経由の人アクション
  （needs 記入・inbox 投入・commands 指示）に限られ、プロジェクトの**立ち上げ**や
  charter の**編集**、誤 done の**復帰**はアプリ外（エディタ・CLI）で行う必要があった
- **追加**: 3 つのオーサリング機能を、いずれも「人が書く入力だけを書き、タスク状態
  （done の不変条件）は触らない」原則を守って実装した
  - **＋ 新規プロジェクト**（サイドバー ＋・空状態にも導線）: フォーム（goal /
    constraints / deliverables / acceptance / repos）から `<root>/projects/<name>/charter.md` を
    生成し、repos があれば `repos.json`（kiro-projects の `export_repo_registry` と同一の
    `_meta.generated_from` 付き・キーソート）も作る。作成後はコンテナを設定 roots へ登録して
    発見対象にし、そのプロジェクトを選択する。backlog 生成は従来どおり本体の run が行う
  - **✎ プロジェクトファイル編集**（概要タブ）: `charter.md` / `policy.md` / `repos.json` を
    アプリ内で直接編集。保存すると次の run で後段（backlog 生成・ルーティング）に反映される。
    自動生成 repos.json（`_meta`）は「run 時に charter で上書きされる」旨を警告し、JSON は
    保存前に構文検証する。編集対象はホワイトリスト（人が書く上位入力）に限定
  - **↻ revise して再投入**（タスク詳細・archive のみ）: archive（done）タスクの内容を
    prefill した投入フォームを開き、編集して inbox へ**新しいタスク**として投入する
    （triage→verify を通す＝done を取り直す。archive の記録は残す）。誤 done などの
    エラー復帰用途。inbox 投入フォームには id / after 欄を追加した
- **実装**: `src/main/authoring.js`（charter 雛形生成・repos.json 生成・作成・
  ホワイトリスト読み書き）を追加し、IPC（`kiro:createProject` / `kiro:readFile` /
  `kiro:writeFile`）と `window.api` に公開。archive 再投入は既存の inbox 契約
  （`actions.enqueueToInbox`）を流用。`test/authoring.test.js` を追加
- **リモート連携（state_git 経由のファイルドロップ）**: 3 操作はすべて既存の状態共有 git
  （⚙ 設定「操作を都度コミットしてプッシュ」）に乗る — 編集/投入したディレクトリを
  pathspec 限定でコミット＆プッシュし、リモートの kiro-projects が state_git 同期で取り込む。
  charter.md / policy.md / inbox は既に「人の入力＝リモート優先」で裁定され、新規プロジェクトは
  ディレクトリ丸ごとの追加として同期され、`--project all` 常駐が watch ループで新規発見して回す。
  これに合わせ kiro-projects 側の同時変更裁定に **`repos.{json,yaml,yml}` をリモート優先**へ追加
  （手書きレジストリの viewer 編集を取りこぼさない。自動生成 repos.json は次 run が charter から
  再生成するので charter が正のまま）。`TestStateGitSync.test_conflict_repos_registry_prefers_remote` を追加

### kiro-flow: 孤児 run の resume で orchestrator が usage エラーで即死する不具合を修正

- **症状**: daemon が孤児 run を「同じ run-id で再開」した直後に
  `usage: kiro-flow [-h] …` とオプション不正のようなログを出して orchestrator が
  即終了し、引き継ぎ（resume）が静かに失敗していた
- **原因**: `_spawn_orchestrator` が組み立てる子プロセス argv で、`--inherit-from`
  （`orchestrate` サブコマンドの引数）を `orchestrate` トークン**より前**に置いていた。
  グローバル引数として親パーサに拾われ、`argument cmd: invalid choice` で exit 2 になっていた
  （`--inherit-from` を持つ＝リトライ引き継ぎ由来の run を resume したときに発現）
- **修正**: `cmd_run` の起動と同じく `--inherit-from` を `orchestrate` の**後ろ**へ移動。
  子プロセス argv が実 CLI パーサでそのまま parse できることを検証する回帰テスト
  （`SpawnArgvTests`）を追加し、パーサ構築を `build_parser()` として切り出して共有

### gitlab-review-viewer / kiro-projects-viewer: exe アイコンを追加

- これまで未設定（Electron既定のアイコン）だった Windows exe / ウィンドウの
  アイコンを設定。[Fluent UI System Icons](https://github.com/microsoft/fluentui-system-icons)
  （Microsoft・MIT license）のグリフに角丸カラー背景を合成して `assets/icon.ico`
  として生成し、`electron-builder` の `build.win.icon` と `BrowserWindow` の
  `icon` オプション（開発起動時用）の両方から参照する
  - gitlab-review-viewer: `clipboard_checkmark`（レビュー承認）＋ 赤系背景
  - kiro-projects-viewer: `board`（ダッシュボード）＋ 青系背景

### kiro-projects-viewer: タスクグラフの gitlab ノードにイシューアイコン（1 クリックでレビュー起動）

- **背景**: これまでタスクグラフのノードをクリックすると詳細パネルが開くだけで、関連 GitLab
  イシューを開くには詳細内の「レビューで開く」を**もう一度**押す 2 ステップだった
- **変更**: gitlab executor 由来で**関連イシュー URL が確定済み**のノードには、右上に小さな
  イシューアイコン（↗）を重ね、**1 クリックで gitlab-review-viewer を起動**する（`api.openReview`）。
  ノード選択（詳細表示）とは伝播を分離し、アイコンはイシュー起動を優先。却下ノードは赤で示す。
  実行中で URL 未確定のノードは対象外（従来どおり詳細パネルの「関連イシューを探す」が担当）

### kiro-projects: 優先順位付けでタスク 0/1 件のとき LLM 呼び出しをスキップ

- **背景**: `prioritize`（planner=kiro）は ready なタスクを kiro-cli（LLM）に並べ替えさせるが、
  対象が 0 件または 1 件のときは**並べ替えの余地が無く順序が自明**なのに、毎サイクル kiro-cli を
  起動していた（コスト・レイテンシの無駄）
- **変更**: `prioritize` は `len(ready) <= 1` のとき planner を問わず LLM を呼ばず決定的順序
  （priority＋古さ）にする。LLM 境界の `rank_agent` も 0/1 件は入力をそのまま返して短絡する。
  policy（pin/defer）は 1 件でも後段で必ず効く
- テスト: `test_rank_agent_skips_llm_for_zero_or_one` / `test_prioritize_skips_llm_for_single_task`

### kiro-projects-viewer: charter → backlog → run → issue の関係性を可視化・相互遷移

- **背景**: 従来はタブ（概要/バックログ/要対応/フロー/レビュー/履歴）が独立し、**バックログのタスクと
  kiro-flow の run（＝GitLab イシュー）を結ぶリンクが UI に無かった**。run-id はただの文字列として
  表示され、リトライ（`…-r0`/`…-r1`）も個別の run として並ぶだけだった
- **run-id の解析**（`flow.js` `parseRunId`）: 決定的 run-id `req-<hash>-<taskid>-r<retries>[-v<rev>]` を
  `taskId`/`retries`/`rev`/`lineageId`（同一タスクの系統キー）に分解し、`readRun` が surface する。
  `meta.inherited_from`（`--inherit-from` の引き継ぎ元）も返す
- **リトライを束ねる**: フロー一覧を系統（同一タスク）でまとめ、最新試行を見出しに過去試行を色付き
  ピル（`r0`/`r1`…）で畳む。「意味的に同一のオブジェクトはまとめる」を実装
- **パンくずと相互遷移**: タスクダイアログ・run 詳細に `🎯 charter ▸ 🗒 task ▸ ⚙ run ▸ 🔗 issue` の
  クリック可能なパンくずを追加。バックログ行の `⚙N` バッジ→フロー、フロー一覧の `🗒 taskid`→
  バックログ、issue→GitLab へワンクリック遷移（`switchTab`/`gotoRun`/`gotoTask`）
- テスト: `test/flow-relationship.test.js`（`npm test`）

### kiro-projects: `act_timeout=0` でタイムアウト無効（長時間委譲の空リトライを根治）＋ kiro-flow: リトライ時の run データ引き継ぎ・掃除

- **背景**: gitlab executor のような委譲は、人のレビュー往復で数日かかりうる（gitlab
  executor 側の待ちは `timeout=7日`/`approved_timeout=14日`）。一方 kiro-projects は run の結果を
  `act_timeout`（既定 1800 秒）しか待たず、**待ち切れずに retry を空増やし＆イシューを二重起票**し、
  `max_retries` 超過で誤エスカレーションしていた（`req-…-r2` のように「verify 未到達なのに
  リトライ番号だけ増える」症状の正体）
- **`act_timeout=0`＝無制限待ち**: `_act_submit`（daemon 待ち）・`_act_run`（都度起動）を「0 以下なら
  タイムアウトせず完了まで待つ」に変更。`_claim_ttl` も `act_timeout=0` のとき無限にし、長時間委譲中に
  他インスタンスへ claim を奪われて二重実行するのを防ぐ。設定例
  （`kiro-projects.yaml.example` / `kiro-projects.state-git.yaml.example`）の gitlab 委譲欄に
  `act_timeout: 0` 推奨を明記
- **kiro-flow `--inherit-from <先行run-id>`**: リトライ run 作成時に、タイムアウト/失敗した先行 run から
  **確定済み（done）ノードの結果・計画（graph）・中間成果物（artifacts）を引き継ぎ**、workspace 付き run
  では新 run の作業ブランチを旧 `kf/<old>` から派生させて**確定済み commit を失わない**。引き継ぎ後は
  **先行 run を掃除**（`runs/`＋inbox 要求＋claim を削除）。安全条件として、走っている run には触れず、
  「完全 done」（verify=NG 相当）の先行 run は状態を引き継がず掃除だけ行う（同一出力で即 done→再 NG の
  無限ループを防ぐ）。判断はすべて kiro-flow の `Bus.inherit_from` に閉じ込め、kiro-projects は直前試行の
  run-id を渡すだけ（`_prev_req_id`）
- 設計: `docs/designs/kiro-flow-retry-inheritance-design.md`。テスト: `InheritTests`（kiro-flow）/
  `TestActTimeoutZeroAndInherit`（kiro-projects）

### kiro-flow / kiro-projects-viewer: フロータブでもリモート daemon の生存信号（status.json）を追加

- **背景**: kiro-projects 側に実装した daemon 生存信号（state_git 経由でリモート viewer が稼働判定
  できるようにする機能）と同じギャップが、kiro-flow の daemon にもあった——フロータブの daemon
  稼働判定はロックファイル（`$TMPDIR/kiro-flow-locks/`）の pid 判定のみで、**同一ホスト限定**。
  state_git（鏡）越しにバスを見ているリモート viewer からは daemon の一時領域に届かず、常に
  「判定不能」になっていた
- **`<bus>/status.json`**: kiro-flow の daemon が `host`/`pid`/`node_id`/`orchestrators`/`workers`/
  `updated_iso`/`fresh_after_sec` を書く。`StateGit._scan()` はバスのツリー全体を走査するため、
  `bus.root` 直下に置くだけで既存の state_git がそのまま同期対象に含める（GitBus 側のような
  sparse-checkout の追加設定は不要）
- **idle 中の追加コミットは既定でゼロ**: 起動時に一度だけローカルへ書き、以降は実イベント
  （run 終端・「駆動中の run の生存リース」push）時に既存の sync/push へ相乗りする。
  `--status-interval`（`daemon` サブコマンドの引数。既定 `0`＝無効）を指定したときだけ、
  アイドル中もその間隔で status.json を更新する（kiro-projects 側と同じトレードオフ）
- **GitBus（`--git`）モードでは書かない**: sparse-checkout が `runs/`/`inbox/`（or
  `--git-subdir`）しか作業ツリーに展開せず、対象外パスへの書き込みが `sync_push()` の
  `git add -A` を壊しかねないため（state_git と `--git` は元々ここでも相互排他）
- **kiro-projects-viewer（フロータブ）**: `daemonStatus()` がロックファイル（同一ホスト・確定）→
  status.json（同期経由・推定）の順でフォールバックするようになった。daemon バッジは
  判定根拠を区別して表示（「稼働中（推定）」／「不明（同期経由）」＋最終確認からの経過時間・
  run/worker 数）

### kiro-projects / kiro-projects-viewer: リモート daemon の生存信号（status.json）— 別ホストでも稼働判定できるように

- **背景**: kiro-projects-viewer を daemon の稼働ホストとは別の PC で使う場合（`state_git` 経由でリモート本体の
  結果を見る構成）、操作（approve/hold/revise 等）は既存の `commands/`/`needs`/`inbox` ファイル契約でリモートでも
  同等に効いていたが、**daemon が今も生きているか」は分からなかった** — `~/.kiro-projects/instances/` はローカルの
  生存レジストリで state_git の同期対象外のため、リモートの viewer では「● 稼働中」バッジも概要の実行状況も
  常に空白になっていた
- **`status.json`（生存信号）**: 本体が `<project>/status.json`（`watch`/`level`/`updated_iso`/`fresh_after_sec`）を
  書き、これも state_git で同期する。実データ（backlog/needs/decisions/run-log 等）は既に同期されているため
  重複させず、生存信号だけの最小ファイルにした
- **idle 中の git 負荷は既定でゼロ**: `write_status` は実パス完了時にのみ呼ばれ、その他ファイルの変更と
  **同じコミットに相乗り**する（単体では追加の commit/push を生まない）。watch の idle 中は
  `--status-interval`（既定 `0`＝無効）を明示指定しない限り status.json に一切触れない。指定すればその間隔で
  idle 中も生存信号を更新でき、鮮度と git 負荷のトレードオフを利用者が選べる
- **`fresh_after_sec` は書き手が計算**: 本体が自分の同期間隔（`state_git_interval`/`status_interval` の大きい方の
  2 倍・下限 120 秒）から計算して埋め込むため、viewer 側は単純な経過時間比較だけで済む
- **kiro-projects-viewer**: instances（同一ホスト・確定）に無ければ status.json（同期経由・推定）へ
  フォールバックして稼働判定する。サイドバーの ● は判定根拠を区別して表示（同期経由の推定は輪郭のみの
  ◯＋プロジェクト名に `~`）。概要タブに「daemon の生存」カードを追加し、判定根拠・最終確認からの経過時間・
  `watch`/`level`・最終サイクル（`run-log.jsonl`）を表示する

### kiro-projects / kiro-projects-viewer: 人の即時フィードバック（revise）— 実行中でも気づいた時点で軌道修正

- **背景**: 自律バックログ消化中に人が「方向が違う」と気づいても（例: LLM がローカルサーバを
  立てて e2e を始めたが、実サーバに配備して実施してほしい）、従来はループがブロック（needs）
  するまで指示を届ける口が無かった。needs は**ループ起点（受動）**の往復であり、
  **人起点（能動）**でタスク内容やバックログ間の依存を直す経路が欠けていた
- **`revise` サブコマンド（CLI）**: `revise <id> [--title|--priority|--verify|--accept|--after|--note|--level|--track] [--feedback 指示] [--reason 理由]`。
  フィールドは置換（`''`/`none` で削除。`after` の自己依存・循環は拒否）、`--feedback` は次の act の
  要求文に必ず添付される。決定記録（DR `action: revise`）と `- learn:`（学習材料）を残す
- **効き方はタスク状態で決まる**: ready 等は即時反映 ／ blocked・review は ready へ積み直し
  （needs 消費・review からは手戻り記録）／ **doing（実行中）は `revised` マーカーで予約**し、
  実行側が settle 時に検知して**現在の試行の結果を確定しない**（verify も done もせず修正内容で
  積み直す）。daemon/remote の結果待ちもマーカー検知で早期に打ち切る。`rev` 世代番号が act の
  req_id に載るため、積み直し後の試行が修正前の古い run に合流しない
- **実行ループの即応性を強化**: ①パス途中（サイクル間）でも commands/・needs 記入を取り込む
  （長いパスでも人の修正が次のサイクルから効く）②claim 直後にディスク内容を採用してから
  doing 化（パス途中の CLI revise・直接編集を in-memory の古い内容で上書きしない）
  ③宙に浮いた `revised`（クラッシュ等）はパス開始時に回収して ready へ戻す（自己回復）
- **commands/ ドロップ契約に `revise` を追加**: `{"command": "revise", "id": ..., "feedback": ...,
  "after": ..., ...}`。CLI と同一ロジック・同一 DR（ビュアーや WSL 境界越しの操作向け）
- **kiro-projects-viewer**: タスク詳細に「✎ 修正して指示（revise）」フォームを追加
  （タイトル・優先度・依存 after・verify・accept の置換＋フィードバック。変更した項目だけ送信）。
  **実行中（doing）のタスクにも送れる**。送信後はタスク行に ✎ バッジ・詳細に「修正指示送信済み
  （取り込み待ち）」を表示し、本体が取り込むまで再送を防ぐ（needs と同じ file+mtime 照合）。
  経路は既存の指示と同じ auto/file/cli（既定はファイルドロップ・CLI 不要）
- **スキル更新**: `kiro-projects` スキルに「軌道修正（revise）」モードを追加
  （「タスクを直して」「やり方を変えさせて」「依存を付けて」等で発動）

### kiro-flow: git バスクローンの index.lock 残骸を自己回復（daemon の再 claim 無限ループを解消）

- **背景**: kiro-projects（autonomous）と kiro-flow を同じリポジトリのバスで併用中、前プロセスの
  異常終了（SIGKILL・電源断・daemon の terminate）がノードクローンに `.git/index.lock` を残すと、
  orchestrator の run 作成（`sync_push` の `git add`）が「File exists」で恒久的に失敗。run の meta が
  一度も push されず `run_exists` が偽のままなので、daemon が毎 poll 同じ要求を
  再 claim → commit → push → orchestrator 起動 → 即死 と繰り返す無限ループに陥っていた
- **ロック残骸の自己回復**: 管理クローンの再利用時に、十分古い（`GIT_LOCK_STALE_SEC`=30s 以上
  更新の無い）`index.lock` 等のロック残骸と中断 rebase（`rebase-merge/`）を除去してから使う。
  実行中に遭遇したロックも、新しいうちは短いバックオフで解放を待ち（稼働中の他 git を壊さない）、
  残骸と判明したら除去して再試行する（`git` 呼び出し共通のリトライ）。ロック検知を決定的にするため
  バスの git は `LC_ALL=C` で実行
- **使えないクローンは作り直す**: ロック除去でも回復できない管理クローン（index 破損等）は
  削除して再クローンする（バスの真実はリモート側にあるため使い捨てで安全）
- **daemon の終端化フォールバック（`fail_request`）**: orchestrator が run の meta を一度も
  書けずに死に続けた要求は、failed run を新規作成して終端化する。`run_exists` が真になり
  再 claim ループが有限回で必ず止まる。要求内容（request/workspace/references）は meta に
  引き写すので、消費者（kiro-projects の submit 待ち）も失敗を即検知できる
- **並行 submit の隔離**: submit のノード ID に pid を付与し、並行 submit が同じクローン
  作業ツリーを共有して index.lock を取り合う事故を予防

### gitlab-review-viewer: 起動時の「初期化に失敗しました」を修正

- `config.json` が想定外の形（全体が `null`・セクションが `null` や非オブジェクト等）に
  なっていると、設定マージ（`deepMerge`）が既定値を守らずそのまま通し、起動直後の
  `state.config.searchCache` / `state.config.gitlab.token` 参照で
  「初期化に失敗しました: Cannot read properties of null …」になっていた
- `deepMerge` を**既定値の型を保つマージ**に変更 — 既定値がオブジェクト / 配列のキーに
  型の合わない保存値（`null` 含む）が来た場合は既定値を採用し、壊れた設定ファイルでも
  起動できるようにした
- renderer 側の初期化も防御的に変更 — 設定の取得失敗時は最小構成で起動して
  「⚙ 設定から保存し直してください」と案内し、受け取った設定は形を検証してから使う。
  前回の検索条件の復元失敗も起動を妨げない

### kiro-projects-viewer: GitLab タブを「レビュー待ち」に特化

- GitLab タブを「レビュー待ち」に改名し、**repos のオープンイシュー＋関連 MR の
  横断一覧**（レビュー待ち・作業中）に特化。bus 由来の委譲イシュー一覧セクションは
  廃止 — run/ノード単位の決着（承認/却下）はフロータブのノード詳細が担当し、
  役割の重複を解消（bus は run 後に掃除されるため一覧としても不完全だった）
- 関連 MR の補完（glEnrich）を repos のオープンイシューに対して行うように変更
  （レビュー対象の MR チップが「レビュー待ち」一覧に出る）

### gitlab-review-viewer: 却下を「MR クローズ＋ブランチ削除・イシューは閉じる」に一本化

- 却下の「削除 / 閉じる」の 3 択を廃止し、**イシューは常に閉じる（削除しない）**に統一。
  コメント・経緯が記録として残り、委譲元ツール（kiro-flow はイシューのクローズで却下を
  検知し人コメントをやり直し指示として取り込む）にも決着が正しく伝わる。イシュー削除
  API（`glDeleteIssue`）は廃止
- 関連するマージリクエストは**クローズしてソースブランチを削除**する。対象はイシューの
  `related_merge_requests`（open）のうち**イシュー名と似たタイトルの MR のみ**
  （タブ選択と同じ `titleSimilarity` ≥ 0.5。本文で言及しただけの無関係な MR は対象外）。
  クローズ対象はダイアログに事前表示され、確認してから実行できる
- イシューのクローズは表示キャッシュの state に頼らず常に明示的に行う（委譲元の
  自動クローズは daemon 停止中は走らないため。クローズ済みなら no-op）
- **kiro-flow gitlab executor（防御）**: 決着待ち中にイシューが削除（404）されても
  一般エラーでなく**取り下げ＝却下**として決着させる（`decision: rejected`・
  guidance 空＝自動判断でやり直し）。404 以外のエラー（ネットワーク断・権限）は
  従来どおり失敗として送出

### kiro-flow: gitlab executor の却下を機械可読な決着に（data 付き failed）

- 却下時の failed result に、承認と対称の構造化データ（`issue_iid` / `web_url` /
  `decision: rejected` / `reason` / `guidance`（人コメント）/ `merged_mrs` / `closed`）を
  `data` として残す（却下例外に `data` 属性を載せ、worker が failed result に書く）。
  **status は failed のまま**——done は「後続が成果に依存してよい」契約であり、成果の無い
  却下では満たせない（却下=done にすると verify が緩いタスクで「人が却下したのに done 確定」の
  取り違えが起き得る）。やり直しの判断とループは従来どおり上位（kiro-projects）が担う
- kiro-projects の `read_reject_guidance` は構造化 data（`decision=rejected` の `guidance`）を
  優先し、無ければ従来の `[gitlab-reject]` 文字列マーカーにフォールバック（旧 run 互換）
- viewer は却下判定を `data.decision` からも導出し、ノード詳細に**却下理由と
  「やり直し指示（人コメント）」**を明示表示

### kiro-projects-viewer: ノード進捗の可視化・失敗時の人の指示・GitLab イシュー連動

- **ノード毎の進捗**: フロータブのノード詳細に、開始時刻・経過（実行中）・worker の
  heartbeat 鮮度と lease 生存・完了時刻と所要・作り直し回数（`retries`）・
  claimed/result のタイムライン（`events/*.jsonl` から）を表示
- **関連 GitLab イシュー（gitlab executor 連動）**: ノード詳細に関連イシューを表示し
  「レビューで開く」で gitlab-review-viewer へ引き継ぎ。承認済みは result の `data`、
  却下は output のイシュー URL（`decision=rejected` として GitLab タブにも並ぶ）、
  **実行中ノードは gitlab executor と同一導出の決定的タスクトークン**
  （`kf-<sha1(run_id/node_id)[:12]>`・イシュー本文の隠しマーカー）を GitLab API で
  検索して発見する（起票直後から追える）
- **失敗 run への指示**: run 詳細に「↻ 同じ要求で再投入」を追加。meta の要求・
  ワークスペース・参照リポジトリをそのまま新しい run として `inbox/` へ投入する
  （kiro-flow の公式入力契約のみ。daemon が新規要求として拾う）
- **README**: 「エラー時の流れとビュアーの役割」を追加 — kiro-flow 内の自動回復
  （retry → サーキットブレーカー）、gitlab executor の承認/却下と `[gitlab-reject]` の
  feedback 連携、人の出番（needs）とビュアーの対応窓口を 1 枚に整理
- 修正: アクティビティのイベント並び順が ISO タイムスタンプで正しくソートされて
  いなかった（数値減算前提だった）のを修正

### kiro-flow: PC の毎日シャットダウンに耐える（孤児 run を failed でなく自動再開）

- **孤児 run の引き継ぎ（resume）**: owning daemon が消失した（生存リース切れの）非終端 run を、
  次に起動した daemon が reclaim して**同じ run-id で orchestrator を再起動**する。確定済みの
  `results/` はバスに残っているため、未完了ノードだけが続きから実行される（従来は
  `orphaned: owning daemon が消失` として即 failed に確定していた）
- **暴走ガード `max_resumes`**（設定/`--max-resumes`・既定 3）: 「進捗なしの連続再開回数」で
  数え、前回の再開以降に results が増えていれば 1 から数え直す＝進捗のある長期 run は毎日の
  シャットダウンを跨いで何日でも継続できる。上限超過・要求ファイル欠損・無効化（0 以下）の
  ときだけ従来どおり failed に確定し、result を待つ消費者の永久待機を防ぐ
- daemon 稼働中の orchestrator 異常終了（クラッシュ）も同じ資格（max_resumes）で即時再開する
- 新 Bus API: `reclaim_request`（run が存在していても引き継ぎ claim できる）・
  `record_resume`（進捗リセット付きの再開カウンタ。meta の `resume_count` / `resume_progress`）。
  再開時は `run-resumed` イベントを events に記録

### kiro-projects: daemon 委譲の submit をリブート跨ぎで再接続可能に

- `_act_submit` の req_id を決定的に（`req-<backlogハッシュ>-<task.id>-r<retries>`）。
  PC のシャットダウンで submit の待機ごと消えても、再起動後の同じ試行は同じ req_id を
  再 submit して kiro-flow 側の既存 run（daemon が自動再開）に合流する＝**二重実行しない**。
  リトライ（retries+1）は新しい run になる

### kiro-projects-viewer: 自動再開の可視化

- run 詳細の heartbeat 行に自動再開回数（`resume_count`）を表示。「応答なし」の説明を
  「daemon が再起動すれば続きから自動再開されます」に更新

### kiro-projects: 指示のファイルドロップ口（commands/）を追加

- **新しい入力契約** `<project>/commands/<name>.json`
  （`{"command": "approve|hold|pin|defer", "id": "<task-id>", "reason": "..."}`）:
  CLI を実行できない環境（操作側が Windows・本体が WSL 内で稼働、など）から
  approve / hold / reprioritize と同じ人の指示をファイルだけで渡せる
- run/watch が取り込み、**CLI と同一のロジック（`cmd_approve` / `cmd_hold` /
  `cmd_reprioritize`）・同一の決定記録（DR）**で実行する（二重実装しない）。
  処理したファイルは削除、壊れた JSON・未知の指示・対象不在は `.err` へ退避して
  journal に記録（無限再試行を防ぐ）。watch 中は `--debounce` の静穏化が効く
- `has_work` が commands/ のドロップでも watch を起こす。`ensure_dirs` が口を作成し、
  instances レコードに `commands` パスを追加（外部操作者が発見できる）

### kiro-projects-viewer: 指示（承認/保留/優先度変更）をファイルベース化

- approve / hold / pin / defer を CLI 起動から `commands/<name>.json` ドロップに変更
  （上記の新契約）。**本体が WSL 内で稼働していてもファイル共有経由で届く**
- 届け方は ⚙ 設定「指示の届け方」で制御: auto（既定。instances の heartbeat で稼働中なら
  ファイル、停止中は CLI、CLI 不可ならファイルに退避）／file（常にファイル）／cli（従来）
- 書きかけ保護のため `.tmp` に書いてから rename（watch の debounce と二重の保護）
- 稼働判定は WSL 内の本体が登録する `root_windows`（`\\wsl.localhost\...`）にも一致

### kiro-projects-viewer: kiro-flow の状態を CLI に聞かずファイルだけで判定

- **run の生存判定**: `meta.json` の生存リース（`orch_lease_until` / `heartbeat_at`）から
  orchestrator の駆動中 / 応答なし（孤児の可能性）を導出（kiro-flow の `run_is_orphaned` と
  同じ規則。リース未記録の古い run は `updated_at` の age で判定）。running のまま owner が
  消えた run にフロータブで「応答なし」チップと heartbeat 経過を表示
- **daemon 稼働検知**: kiro-flow / kiro-projects と同一導出のロックパス
  （`sha1("local::" + realpath(bus))` → `<lock_dir>/daemon-<hash>.lock`）を読み、記録 pid の
  生存でバスごとの daemon 稼働をバッジ表示（kiro-projects の fcntl 不在時フォールバックと
  同じ根拠。CLI は起動しない）
- **共有バスの自動発見**: フロータブのバスを `<project>/bus` → `<container>/bus` →
  ⚙ 設定 `kiro.flowBus` → kiro-projects 設定ファイル（`<workdir>/.kiro` → `~/.kiro` の
  `bus:`）の順にファイルの存在だけで解決（`--bus` の共有バス構成でも run が見える）。
  run が無いときは探索した候補パスを表示
- **新設定**: `kiro.flowBus`（共有バスの明示指定）・`kiro.flowLockDir`（daemon ロック置き場。
  空なら `.kiro/` 設定の `lock_dir` → 既定 `$TMPDIR/kiro-flow-locks` を導出）
- 新モジュール `src/main/toolconfig.js`: `.kiro/` の kiro-projects / kiro-flow 設定から
  トップレベルのスカラ（`bus` / `lock_dir`）だけを読む簡易リーダー

### kiro-projects-viewer: プロジェクトダッシュボードを新規追加

- **新規ツール** `tools/kiro-projects-viewer/`: kiro-projects のプロジェクト状態を可視化する
  Electron アプリ（gitlab-review-viewer と同じプレーン Electron・実行時依存なしの構成）
- **概要タブ**: charter（goal / deliverables / constraints）・acceptance 達成状況
  （`project.json` の PASS 履歴スパークライン付き）・バックログの status 別集計・
  実行中クレーム・policy・直近 run（`run-log.jsonl`）・納品（`DELIVERY.md`）
- **バックログタブ**: `backlog/` / `archive/` のタスク一覧（status / priority / verify /
  after / level 等。フィルタチップ・詳細ダイアログ・ファイルを開く）
- **要対応タブ**: `needs/`（MADR 形式）の判断待ち / 検収待ちをカード表示。
  「ファイルを開いて回答」でエディタへ
- **フロータブ**: kiro-flow バス（`bus/runs/<run-id>/`）のタスクグラフを SVG の DAG で描画。
  ノード状態（done / failed / claimed / pending / 依存待ち）はファイル存在から kiro-flow と
  同じ規則で導出（lease 内 claim の決定的タイブレーク含む）。ノード詳細・進捗バー・
  アクティビティ（`events/*.jsonl`）付き
- **GitLab タブ**: gitlab executor が委譲したイシュー（results の issue_iid / web_url /
  decision / merged_mrs）と `repos.json` の GitLab リポジトリのオープンイシューを一覧。
  GitLab API（read）設定時はラベル・関連 MR の最新状態を補完
- **履歴タブ**: run-log・決定記録（`decisions/` の DR / learn）・納品・journal
- **プロジェクト発見**: 設定の roots に加え `~/.kiro-projects/instances/*.json`
  （稼働発見レコード）から稼働中コンテナを自動発見（heartbeat 鮮度で ● 稼働中表示）。
  `<root>/projects/<name>/` 標準レイアウトと旧フラット構成の両対応
- **ディープリンク**: `kiro-projects-viewer://open?root=<container>&project=<name>` で
  特定プロジェクトを直接開ける（シングルインスタンス）
- **人のアクション層**: 可視化だけでなく、人間ループの判断をアプリ内で完結できる。
  kiro-projects の公式な入力契約のみを使用（done 確定の不変条件を迂回しない）:
  - 要対応カードから **フィードバックして再開 / そのまま再実行**（needs の
    「## Decision Outcome」記入 + `- [x]` 確定 = `ingest_feedback` の正規ルート。
    本体の `read_feedback` / `feedback_submitted` で取り込み可能なことを相互検証済み）
  - **承認して done 確定**（review / milestone）・**保留（hold）**・
    **最優先へ / 後回し（pin / defer）** は kiro-projects CLI へ委譲（決定記録 DR が残る。
    CLI コマンドは設定可能）
  - **＋ タスクを追加**: `inbox/<name>.json` ドロップ（E4 push 型取り込み口）で投入。
    inbox 取り込み待ち件数もバックログタブに表示
  - 差し戻し（review）は修正方針の記入必須ガード付き。入力中は自動更新を一時停止し
    書きかけの回答を保護

### gitlab-review-viewer: ディープリンク対応（kiro-projects-viewer 連携）

- **カスタム URL スキーム** `gitlab-review-viewer://open?url=<web_url>` で外部ツールから
  特定イシュー / MR をレビュー画面として開けるように（対象は API で解決 → 候補一覧の
  先頭へ挿入 → 自動選択で関連イシュー / MR ごと左右ペインへ展開）
- **シングルインスタンス化**: 二重起動時は既存ウィンドウへディープリンクを転送
  （`second-instance` / macOS `open-url`）。electron-builder に `protocols` を宣言
- kiro-projects-viewer の GitLab タブ「レビューで開く」がこの入り口を使い、
  タスク→イシュー→レビューをシームレスに接続する

### gitlab-review-viewer: kiro-projects 連携を削除し、レビュー特化に再設計（破壊的変更）

- **削除**: kiro-projects needs（判断待ち/検収待ち）連携を全面削除（Needs タブ・
  フィードバック確定・approve・needs 要約と関連設定 `kiroAutonomous` / `needsPromptTemplate`）。
  GitLab のイシュー / MR レビューに特化する
- **プロキシ引き継ぎ**: `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` / `NO_PROXY` 環境変数を
  Chromium に引き継ぎ、webview 表示と GitLab API 呼び出し（`net.fetch` 化）の両方に適用
- **検索条件のキャッシュ**: グループ / プロジェクトの取得結果と選択値を含む検索条件すべてを
  `config.searchCache` に自動保存し、次回起動時に復元
- **作成者フィルタ**: イシュー / MR をユーザー名（`author_username`）で絞り込み可能に
- **ペイン振り分けの変更**: 種別（イシュー / MR）条件は候補一覧の絞り込みのみに適用。
  候補を選択すると紐づくイシューを左ペイン・MR を右ペインにタブ表示。イシューに紐づく
  MR が複数ある場合は、イシューとタイトルが同じ MR（`Draft:` / `Resolve "…"` 形式は
  同一視）のタブを自動でアクティブにする
- **スプリッター**: 左右ペイン間をドラッグしてサイズ変更可能に
- **URL バーメニュー（☰）**: 各ペインに「リーダーモード（本文テキストのみをタブ表示）」
  「要約を作成してタブ表示」「Obsidian へ送る」を追加。生成されたローカルタブは × で閉じられる。
  Obsidian 送信はアクションバーから ☰ メニューへ移動し、アクティブなタブの内容
  （GitLab ページのタブはリーダーモードと同等の本文抽出テキスト）を書き出す
- **アクションバー再編（承認 / 差し戻し / 却下 / 変更）**: 操作対象は表示中のイシュー
  （無ければ MR）に自動決定。旧マージ / クローズ / リオープンボタンと操作対象セレクト・
  常設ラベルプリセット行を撤去
  - **承認**: `status:elaborated` → `status:open`。`status:approved` は同タイトル MR を
    マージしてイシューをクローズ（コンフリクト / 未解決レビューコメント / 他ステータスは
    グレーアウト。可否は MR の `has_conflicts` / `blocking_discussions_resolved` で判定）
  - **差し戻し**: `status:elaborated` → `status:draft`、`status:approved` → `status:needs-rework`
    （他ステータスはグレーアウト）
  - **却下**: 削除 / 閉じる / キャンセルの 3 択ダイアログ。両者とも同タイトル MR を
    クローズし、削除はソースブランチ削除 + イシュー削除、閉じるはイシューをクローズ
  - **変更**: ラベルプリセット（従来の下ペイン UI）をダイアログ表示し「実行」で適用
  - いずれも入力テキストを本文として `# ボタン名` 見出し付きコメントを対象へ投稿
  - 実行後（コメント投稿・ショートカットのラベル変更を含む）は左右ペインの
    イシュー / MR 表示を自動で再読み込みして結果を反映
- **要約の高速化と出力フィルター**: 既定プロンプトを簡潔化（出力のみ・ツール実行禁止・
  400 字目安の分量上限）し、入力も自動で切り詰め（本文 4,000 字・直近コメント 20 件×400 字・
  変更ファイル 50 件）。要約は `===SUMMARY_START===` / `===SUMMARY_END===` マーカーで
  挟ませ、エージェント出力からマーカー間の要約本文のみを抽出（マーカーが無い場合は
  スピナー・枠線・進捗表示などのノイズ行のみ除去するフォールバック）

### codd-gate v1.0.0 — doc/code/test 一貫性ゲート（単体 CLI・kiro-projects 連携はオプション）

[CoDD (Coherence-Driven Development)](https://github.com/yohey-w/codd-dev) の設計
（Trace＝接続マップ / Impact＝Green・Amber・Gray 分類 / Verify＝no fake green）を翻案した
決定的ツールを追加。**kiro-projects に依存しない独立ツール**（python3＋git のみ・独立インストーラ
`install.sh`）として単体で CI / git hook から使え、kiro-projects とは**本体無改造**の一方向
オプション連携（既存フック regression_cmd・charter acceptance・タスク verify・enqueue --json / inbox
のみで結合するプラグイン方式）。ブラウンフィールド前提で、既存負債は止めずに
「棚卸し→ラチェット→backlog 返済」、新規変更だけを差分ゲートで護る。

- **新規ツール `tools/codd-gate/`**（stdlib のみ・LLM 不要）: `scan`（doc↔code↔test の接続マップと
  壊れた参照/未文書化/未テストの負債棚卸し）/ `impact`（差分の Green/Amber/Gray/**Followup** 分類）/
  `verify`（差分ゲート＋ `--debt` 負債ラチェット。exit 0/1）/ `tasks`（ドリフト・負債を共通 task
  スキーマの修復タスクへ変換。同一 repo は決定的 verify、別 repo は accept＋workspace で
  ルーティングに乗せる）/ `check`（修復タスク verify 用の状態アサーション: 接続・参照解決・鮮度）。
- **複数リポジトリ（外部フォーマット非依存）**: レジストリは共通スキーマ（`--repos` ファイル /
  設定 `repos:`。dir / docs / tests / code を per-repo 指定）。identity は (url, path, base)＝
  パス＋ブランチで一意。リポジトリ横断参照は `repo名:相対パス`。charter.md は読まない。
- **接続の推定は決定的**: 明示注釈 `coherence: doc|code|test=…`（最優先）＞ md のインラインコード/
  リンク ＞ Python import ＞ 命名規約（一意時のみ）。曖昧は接続も負債もしない。
- **git アクセスの原則**: 通常動作はローカル読み取りのみ（clone/fetch ゼロ・フル clone はどの経路にも
  無い）。url-only repo は `--sync`（opt-in）で git-worktree-cache-pattern 準拠に実体化——共有 bare
  ミラー（初回のみ blob:none・以後増分 fetch。`KIRO_GIT_CACHE_DIR` で kiro ツール群と共有）から
  **fetch 後の SHA** で detached worktree（INV-1 鮮度）を生やし、run 後に worktree だけ回収。
  実体化不能は黙って PASS 側に倒さない。`dir:` 指定 repo には触れない（判定対象は作業ツリーそのもの）。
- **kiro-projects に汎用取り込みフック `intake_cmd` を追加**（設定/CLI `--intake-cmd[-interval]`）:
  外部の決定的ゲート/検出器を watch の周期で pull し、stdout の enqueue --json を**冪等取り込み**
  （id が現役 backlog に居れば飛ばす）。パス開始時と idle 中に間隔律速で実行、失敗は journal に残して
  無視。**常駐は kiro-projects 側だけが持ち、intake_cmd（codd-gate 含む）は単発・有界**という役割
  分担を固定。有効化は設定だけ: `regression_cmd`（差分ゲート）＋`intake_cmd: codd-gate tasks --debt`
  （負債の自動返済）＋charter acceptance（ラチェット）。kiro-projects の install.sh は隣に
  codd-gate があれば同梱インストールする。
- **`tasks --debt --cohort`**: 未文書化/未テストのような同種負債の山を repo 単位の cohort
  （`cohort_items`＋`{item}`）に集約し、後段の分解を kiro-projects の pilot-then-batch に委ねる。
  タスク id は発見内容から決定的（48 字・末尾ハッシュ）＝intake の冪等キー。
- **共通スキーマ `schemas/` を新設（repos / task をツール横断の独立スキーマとして管理）**:
  `repos.schema.json`（リポジトリレジストリ。identity = (url, path, base)）と `task.schema.json`
  （制御層タスクの JSON 表現。Markdown 形の正典は backlog.md.example・未知キー保持）。
  kiro-projects は手書きの `<project>/repos.{yaml,yml,json}` があれば**レジストリの正**として読み
  （charter の `## repos` は互換入力＝内部で同形に正規化して引き回す）、**無ければ charter から
  repos.json を自動生成**して外部ツールへ「ファイルとして渡す」（_meta マーカー付き・正は charter に
  追従・## repos が消えれば生成物も消す。分類グロブ docs/tests/code も損失なく引き継ぐ）。
  repos ファイル単独では charter モードは発動しないがルーティング/参照解決には効く。kiro-flow の
  `--workspace`/`--reference` はこのスキーマの 1 エントリの射影。codd-gate のタスク出力がスキーマに
  適合することはテストで突き合わせる。
- **codd-gate は kiro-projects から完全独立に**: charter アダプタ（--charter）を廃止し、レジストリは
  共通スキーマ（--repos ファイル / 設定 repos:）のみに。`tasks` は共通 task スキーマへの**直接出力**
  であり特定ツール向けアダプタではない。結合は入力（repos スキーマ）・出力（task スキーマ）とも
  `schemas/` のデータ契約だけ。
- **タスク追加の責務境界を明文化**: kiro-projects は元よりタスクを入力とする設計（enqueue＝汎用の
  取り込み口・外部ソースは薄いアダプタで流し込む思想）で、タスク契約（正典 `backlog.md.example`・
  未知キー保持の前方互換）の所有者は kiro-projects。codd-gate コアの正は**所見**（`impact --json` /
  `verify --debt --json`）で、`tasks` はそれを共通 task スキーマへ直接出力する。
- **外部 CLI の差し込み点をカタログ化**: kiro-projects 設計書 §4.1 に公式の 6 点（E1 verify/
  acceptance・E2 regression_cmd・E3 intake_cmd・E4 inbox/enqueue・E5 notify_cmd・E6 executor）の契約
  （入出力・環境・制約）と選び方・妥当性を明文化。暗黙の拡張点は作らない（S1 優先順位・S5 エスカレー
  ション・S7 予算にはフックを設けない理由も記載）。codd-gate は E1+E2+E3 を使う適用例。
- **新規スキル `codd-gate`**: 単体運用（git hook / CI）を主、kiro-projects 結線
  （regression_cmd → acceptance ラチェット → intake_cmd 返済）を追加情報として整理。
- 設計書 `docs/designs/codd-gate-design.md`（codd-dev からの翻案対応表・差し込み点選択の妥当性検証
  つき）とテスト（codd-gate 28 件＋kiro-projects intake 5 件）を同梱。

### agentic-search v1.0.0 — 反復探索を共有スキル化し検索系スキルへ一括導入

検索を **単発の retrieve** から **エージェント（Claude）が「検索 → 評価 → 再構成 → 再検索 → 統合」を
反復する** agentic search へ引き上げた。反復ループの「頭脳」を検索系スキル横断の共有スキルとして切り出し、
コーパスごとの検索（retrieve）は各スキルに残す構成とした。各スキルの哲学（Markdown の読み書きだけ・
ループの駆動役はエージェント）に従い、スクリプトは反復を内蔵せず
**「1 ステップの検索 ＋ 次の一手の手がかり」を返すプリミティブ** に徹する。

- **新規スキル `agentic-search`（tier: core）**: `scripts/hints.py` がバックエンド非依存のヒント
  エンジン。正規化済み結果リスト＋クエリから `next_action`（synthesize/refine/expand/broaden）、
  `suggested_queries`、`related_ids`、`gap_keywords`、`sufficient` を計算する（ライブラリ／CLI 両対応）。
  反復ループ・収束条件の正典は `references/protocol.md`。
- **ltm-use v5.4.0**: `recall_memory.py` に `--json` / `--suggest` / `--ids`（マルチホップ取得）を追加し、
  ヒント計算を agentic-search に委譲（未導入時はローカル実装にフォールバック）。探索中は `--no-track` で
  `access_count`／忘却曲線を汚さない運用とする。
- **wiki-use**: `wiki_query.py search` に `--json` / `--suggest` を追加。被覆率を score、本文の
  `[[wikilink]]` を related（マルチホップの種）として正規化する。
- **moltbook-use**: `moltbook.py search` に `--json` / `--suggest` を追加。連邦検索（issues/blobs/notes）の
  ヒットを正規化し、トピックラベルを tags として扱う。
- **オプショナル依存**: wiki-use / moltbook-use は agentic-search 未導入時はヒントを省略して通常検索のみ
  返す（graceful degradation）。

### kiro-flow: git バスのクローンをリトライ化（不安定なネットワークでの起動失敗を修正）

イシュー委譲のような分散構成では、daemon／orchestrator／worker が**起動毎に git バスを clone** する。
従来この初回 clone（`GitBus._ensure_clone`）には**リトライが無く**、一過性のネットワーク障害で
即 `RuntimeError` となり「移譲側が起動できない」原因になっていた（push/pull は指数バックオフで
リトライ済みだったのに、clone だけ未対応だった）。

- `GitBus._clone_with_retry` を新設し、初回 clone を **push/pull と同じ指数バックオフ（2,4,8,16s・
  `CLONE_RETRIES` 回）**でリトライする。再試行の前に部分 clone の残骸を消す（`_reset_clone_dir`）ので
  「宛先が空でない」で二次失敗しない。blob フィルタ非対応サーバ向けフォールバックは従来どおり。
- **委譲される側（実作業ノード）**も同様に脆かったため、ワークスペースの per-task clone
  （`_clone_repo`）にも同じバックオフリトライを追加。clone 失敗で即タスク失敗にならないようにした。

### kiro-flow: gitlab executor の起票を冪等化（再 claim 時の二重起票を修正）

`--executor gitlab` で各タスクを GitLab イシューに委譲する際、ワーカーが MR の決着待ち（最長 7 日）の
最中に**夜間停止などで殺される**と、result が書かれないまま claim の `lease`（既定 30 分）が失効し、
タスクが `pending` に戻って**別の（リモートの）ワーカーに再 claim** される。従来はそのとき
`execute()` が無条件に新規イシューを起票していたため、**同一タスクのイシューが二重に立つ**現象が起きていた。

- **冪等な起票に修正。** イシュー本文にタスクごとの決定的トークン（`art_dir` ＝ `runs/<run>/artifacts/<node>`
  由来の `kf-<hex12>`）を隠しマーカーとして埋め込む。起票前に同じトークンの **open イシュー**を検索し、
  見つかれば**新規起票せず再アタッチ**してポーリングを再開する（`_task_token` / `_task_marker` /
  `_find_open_issue_by_token`、ポーリングループを `_wait_for_decision` に分離）。
- 検索の取りこぼし・別タスクの誤ヒットに備え、検索後にマーカーが description に**実在することを検証**して
  から再アタッチする。`art_dir` が想定形でない場合は従来どおり毎回新規起票（後方互換）。

### kiro-flow: gitlab executor が外部クローズの承認/却下を判定してタスクグラフへ反映

イシューが（人手・自動化など）**外部でクローズ**されることがある。従来は MR で決着がつかないまま
クローズされると一律「取り下げ＝却下」にしていたため、人が手動マージ後にクローズしたケースなどを
取りこぼしていた。MR の状態 → `status:approved`/`status:done` ラベル → イシューコメントの内容
（承認語/却下語）の順で承認・却下を推定し、結果をタスクグラフへ反映するよう改めた。

- `_mr_decision` は MR の状態のみで判定する責務に縮小（外部クローズの扱いを分離）。新設の
  `_closed_issue_decision` がラベル→コメント（`_decision_from_comments`、却下語を承認語より優先）の順で
  推定する。判断材料が無いクローズは従来どおり取り下げ＝却下。
- 承認なら `done` 成果として下流へ、却下なら `[gitlab-reject]` 例外で上位（kiro-projects）が
  コメントを活かしてやり直す。承認/却下の根拠（reason）をログ・成果テキスト・例外メッセージに出す。

### gitlab-gatekeeper（旧 review-concierge をリネーム＋門番化・破壊的変更）

AI が量産する MR/イシューのレビュー負荷を下げるため、`review-concierge` スキルを **`gitlab-gatekeeper`** に
リネームし、マージ承認の「門番」として 3 モード構成に拡張した。判断は人間が下し、スキルは執行に徹する。

- **packet モード（既定）に Gate A を追加。** レビューパケット生成を指示されたら、判断材料を集める**前に**
  紐づく MR の未対応レビューコメント（`get-mr-discussions --unresolved`）を確認し、**1 件でもあれば
  `status:needs-review` へ差し戻し、未対応スレッドの要点をコメントして終了**（パケットは作らない）。
- **decision モードを新設。** ユーザーの承認/否認を受け取り GitLab へ執行する。
  - **承認** → マージ可否（未対応コメント無し・非ドラフト・コンフリクト無し・CI 成功）を事前確認して
    `merge-mr` → イシューを `--state-event close`。**マージできない場合（事前不可、または merge-mr が非 2xx）は
    `status:needs-review` へ差し戻し、不可理由を具体的にコメント**（「マージした」と誤報告しない）。
  - **否認** → `status:needs-review` へ差し戻し、**ユーザーの自然文コメントを解釈して実行可能な差し戻しコメントを
    生成・投稿**（ユーザーが述べていない要求は足さない。曖昧なら 1 問確認）。
- ラベル/マージのポリシー（`needs_review_label` 既定 `status:needs-review`、`ready_labels` 既定
  `status:review-ready`、`merge.squash`/`merge.remove_source_branch`、`require_ci_success`）は呼び出し側が上書き可能。
- 後方互換は取らない（`review-concierge` のディレクトリ/スキル名は廃止）。GitLab 操作は従来どおり
  `gitlab-idd` の `gl.py` を再利用し、レビュー観点は `agent-reviewer` の references を再利用する。

### マルチリポジトリ・ルーティング（kiro-projects × kiro-flow・破壊的変更）

大規模・複数リポジトリのプロジェクトを自律運用するため、「タスク → コミット先リポジトリ」のルーティングを導入した。
**判断は制御層（kiro-projects）に集約し、執行は実行層（kiro-flow）が担保する。** 設計の詳細は
`tools/kiro-projects/ROUTING.md`。後方互換は取らない（旧 `--repo`／タスク `- repos:` は廃止）。

#### kiro-flow

- **1 run（=バックログ単位）= 1 ワークスペース（唯一の書込先）に固定。** `--repo`（複数・成果物リポジトリ）を
  廃止し、`--workspace`（ちょうど1つ・素の URL か JSON `{url,path,base,target,desc}`）へ刷新。**リポジトリの同一性は
  (url, path, base)**（同 URL でも path・ブランチが違えば別ワークスペース。`_workspace_clone` のキャッシュキーも修正）。
- **kiro-flow が作業ブランチを作ってワーカーへ渡す。** worker はワークスペースを clone し、`kf/<run-id>` を base から作成。
  エージェントは作業ツリーを編集するだけで、**変更があれば kiro-flow が commit して push**（分散 worker は同じ
  `kf/<run-id>` へ push し rebase リトライで統合）。**変更が無ければブランチを push しない**＝調査だけの読み取り専用
  グラフでは何も書き込まない。デリバリ（branch/commit/target）を result に記録。
- ノード単位の repo 割り当て（`resolve_node_repos`／プランナーの repos 注釈）を撤廃し、run 内の全ノードが同一
  ワークスペースを共有する形に単純化。参照だけのリポジトリは kiro-flow では扱わず、要求本文（goal）として伝搬する。
- executor 契約に構造化 `workspace`（spec dict）引数を追加。**gitlab executor は起票先 GitLab プロジェクトを
  ワークスペース URL から解決**（SSH/https 両形）し、無ければ `gitlab.repo_url` をフォールバックに使う。
- 孤立 clone の janitor 接頭辞を `kiro-flow-repos-` → `kiro-flow-ws-` に変更。
- **参照リポジトリ（読むだけ）を `--reference` で構造化伝搬**（run メタ `references`）。worker がエージェントの
  プロンプト（参照節）と **gitlab イシュー本文の『## 参照リポジトリ』節**に描画する。従来は要求本文へ畳んで
  いたため、分解後の各ノード/イシューに参照情報が届かなかったのを解消。gitlab イシューの対象/参照リポジトリ節は
  構造化 spec から Markdown 整形し、ローカルの clone パス（作業ディレクトリ）は載せない。
- **gitlab executor の完了判定を「関連 MR の状態」ベースに（人が MR を管理）**: kiro-flow は MR を
  **自動マージしない**。リモート worker が MR を用意し、人が関連 MR を管理する。**全 MR マージ＝承認**
  （イシューをクローズして成功）／**一つでも未マージでクローズ＝却下**（人コメントを取り込み元イシューを
  クローズし `[gitlab-reject]` 付きで失敗。コメントが無ければ自動判断）。MR が open のうちは待機。人の確認は
  時間がかかるため待機は長め・設定可能（`gitlab.timeout` 既定 7 日 / `gitlab.approved_timeout` 既定 14 日・0=無限）。
- run が `failed` で終端したら `kiro-flow run` は**非 0 終了**（委譲先の却下を上位が act 失敗として検知できる）。

#### kiro-projects

- **ルーティング解決を新設**（`resolve_workspace`）: タスクを**ちょうど1つの書込先ワークスペース**へ。解決順は
  明示 `- workspace:` > policy `route:` > charter `owns:` 推定 > auto-route（LLM）> `default_workspace`／候補1つ。
  決定はタスク md（`- workspace:` / `- routed_by:`）へ書き戻して安定・監査可能にする。owns 推定は
  タスクの `- paths:` ヒントに加え **verify コマンドが操作するパス**からも行う。
- **plan/review フェーズで書込先を必ず明示**（`assign_plan_workspace`）: charter からバックログを生成する時点で、
  各タスクの workspace を **verify が操作するパスの owns を持つ repo** として決定論的に確定し、それ以外（charter の
  他 repo・プランナーが挙げた repo）は参照（`refs`）へ振り分ける。生成直後から書込先が明示され、route 層は
  それを尊重する。`task_reference_specs` は `- refs:` に加え `- repos:` のトークンも参照として扱い、書込先 url は除外する。
- charter `## repos` に **`owns:`（担当パスのグロブ）** を追加。**owns 有り=書込先候補、owns 無し=参照リポジトリ**
  （読むだけ・`--reference` で伝搬・clone しない）。policy に **`route: <パターン> -> <repo名>`** ルールを追加。
- 設定 `route_planner`（kiro/none）と `default_workspace` を追加。タスクに `- workspace:` / `- paths:` / `- refs:` /
  `- routed_by:` フィールドを追加。kiro-flow へは `--workspace`（単一）と `--reference`（参照・複数）を渡す
  （旧 `--repo` 列を廃止）。参照は要求本文へ畳まず構造化伝搬する（`_reference_cmd_args`）。
- **【修正】`- workspace:` 指定タスクの verify を該当ワークスペースのクローン内で実行するようにした**（バグ修正）。
  ワークスペースへルーティングされたタスクは成果が workdir（git-bus ルート）でなく該当 repo の作業ブランチへ push される
  ため、verify／回帰を従来どおり workdir で回すと「成果の無い場所」で偽 NG になっていた。`_task_verify_cwd` を新設し、
  verify の実行先を **明示 `verify_cwd` > タスクの `- workspace:` 該当 repo の一時 clone（`target`→`base` ブランチ・`path`
  をルート）> workdir** の順で解決（`_acceptance_cwd` と同流儀）。差分基準 `$KIRO_BASE_REV` はクローンの HEAD に取り直し、
  clone は worker の push 先を反映するため都度取り直す。clone 失敗・`path` 不在は黙って workdir に倒さず NG 扱い
  （成果の無い場所での偽判定を防ぐ）。単体テスト 5 件（clone 実行先・`path` をルート・未指定は workdir・明示 `verify_cwd`
  優先・clone 失敗で RuntimeError）を追加。README / GUIDE に追記。
- **委譲 executor（gitlab）の却下→やり直し連携**: gitlab の却下（未マージ MR クローズ）を kiro-flow 内部で
  再委譲せず即失敗化するため、委譲 executor へ `--max-retries 0` を渡す（複数イシューの濫造を防止）。act 失敗時は
  `read_reject_guidance` が直近 run の `[gitlab-reject]` 指示（人コメント）を読み、`_settle_failure` が `feedback` に
  注入して通常リトライの次 act で活かす（コメントが無ければ自動判断）。
- 単体テストを新 API へ更新（kiro-flow・kiro-projects 両スイート、計 494 件 green）。

### kiro-projects

#### Added
- **acceptance/verify を「対象 repo のクローン先」で実行できるようにした**（offload で worker が対象 repo を temp に
  clone・push して消すと workdir に成果が出ず、verify を git-bus 等の workdir で実行してエラーになる問題への対処）。
  実行先を **明示 `--verify-cwd`（設定 `verify_cwd`）> 単一対象 repo の一時 clone > workdir** の順で解決
  （`_acceptance_cwd`）。charter の非 readonly repo がちょうど 1 つなら、その `target` ブランチ（worker の push 先）を
  毎評価で `git clone --depth 1` し `$KIRO_BASE_REV`＝clone HEAD で検証して後始末する。clone 失敗は workdir へ黙って
  フォールバックせず**全 NG 扱い**（成果の無い場所での偽判定を防ぐ）。複数 repo は曖昧なので自動 clone せず `--verify-cwd`
  で明示。タスク verify／回帰検査も `--verify-cwd` 指定時はその先で実行。CLI `--verify-cwd` / 設定 `verify_cwd` を追加。
  単体テスト 5 件（cwd 解決・明示上書き・単一 repo clone・clone 失敗で全 NG・複数 repo は workdir）を追加。
- **charter `## acceptance` に自然文を書けるようにした**（検証コマンドを書けない人向け。タスクの `accept:` と同じ流儀）。
  `- accept: <自然言語の完了条件>` か、全角句読点を含む散文の箇条書きを自然言語とみなし、run 時に `resolve_charter_acceptance`
  がエージェント（`synth_verify` 共用）で**決定的なシェル verify へ合成**する。合成結果は `project.json` の `acceptance_synth`
  （原文→コマンド）に**キャッシュ**してサイクル/再実行をまたいで done 基準を安定させる（再合成のブレ防止）。合成できない
  自然言語が残れば `no-acceptance`（done 判定不能）で人へ回す＝**「done は acceptance 全 PASS のみが根拠」の鉄則を保全**。
  散文を shell へ誤って流す事故は `_looks_like_shell_command` の二段チェックで防止。charter.md.example / README / GUIDE /
  design に追記。単体テスト 5 件（`_acceptance_kind` 分類・合成・キャッシュ安定・収束・合成不能で人へ）を追加。
- **自動アップデート（既定 on・6 時間毎・起動直後にも実施）**。スキルリポジトリ（配布元）の `main` に更新が
  入ったら、`run --watch` の **アイドル時** に取り込む。停止中に入った更新も起動直後の初回アイドルで拾う。doctor と同じ流儀で決定的: `git ls-remote` で main の先頭を
  確認 → 適用済み SHA（`~/.kiro/kiro-projects.update.json`）と違えば、temp 領域へ `tools/kiro-projects/`
  だけを **sparse-checkout** → `install.sh` 実行 → **動いていた cwd のまま `os.execv` で graceful 再起動**
  （レジストリ登録は再起動前に後始末）。手動は `update [--check|--now]`。**更新元 URL は `install.py` が
  生成する `skill-registry.json`（`repositories.origin.url` → `install_dir`）から自動解決**（`update_repo`
  未指定でよい）。設定キー `update_enabled`（off スイッチ・既定 on）/ `update_check_interval`（既定 21600=6h） / `update_repo` /
  `update_branch` / `update_subdir` / `update_installer`。初回はベースライン記録のみ（無更新）。タスク実行中は
  何もしない。単体テスト `SelfUpdateTests`（11 件）を追加。
- charter `## repos` に `path`（作業フォルダ）属性を追加。**モノレポを「同じ url で name と path を変えた複数
  エントリ」に分けてフォルダ別の役割を表現できる**（`desc` に役割、`path` に作業フォルダ）。プランナー提示
  （`build_charter_request`）に path＋役割(desc) を載せ、worker 文脈（`_charter_definition`）にも path を伝搬。
  同一 url を複数エントリで使う場合は distinct な `path` を必須化する検証を追加（曖昧さ防止）。`desc` は `役割`/`role`
  の別名も受ける。charter.md.example / README にモノレポの書き方を追記。
- charter `## repos` に `readonly`（参照のみ）フラグを追加（`- readonly: true` /『- 参照のみ:』。値なしでも True）。
- **repos のメタ（path/base/target/readonly/役割）をタスク単位で構造化 `--repo`（JSON）として kiro-flow へ伝搬**。
  従来は URL のみ・全 repo 一覧のテキストだけだったのを、`task_repo_specs`/`_repo_token`/`charter_repo_spec_map` で
  そのタスクの repo だけを構造化して渡す。kiro-flow 側は `parse_repo_token`（URL or JSON）/`_clone_repo`（base ブランチを
  checkout）/`ensure_work_repos`（spec＋clone パスを返す）/`repo_instruction`（フォルダ・作業ブランチ・push 先・参照のみを
  出し分け）を実装。**この指示は gitlab executor 経由でイシュー本文（## 目的）にも載る**ため、フォルダ/ブランチ/参照のみが
  イシューに構造的に表現される。参照のみは push を指示しない。後方互換: 素の URL トークンは従来どおり。
- **必要な repo だけを必要なノードで clone**（kiro-flow）。repo を run 全体ではなくノード（タスク）単位で割り当て、
  worker は `resolve_node_repos` でそのノードに割り当てられた repo だけを clone する（空配列＝何も clone しない・未注釈は
  全 repo にフォールバック）。計画時に `_assign_node_repos` で割当: **stub プランナーは全 repo（安全側）、kiro プランナーは
  利用可能 repo 一覧（フォルダ/役割/参照のみ込み）を見て各タスクに必要な repo だけを判断**（`_repos_planner_note`）。
  ノードに `repos` フィールドを追加（`_node_entry`/`_coerce_tasks` が保持）。fan-out で多数ノードに分解されても各ノードは
  自分に必要な repo だけ clone する（URL 単位の重複排除と併せ無駄 clone を最小化）。
  kiro-projects 5 件・kiro-flow 7 件のテストを追加（全 240 / 152 OK）。
- **成果物リポジトリ clone の削除機構を強化**（kiro-flow）。temp clone 名に所有 pid を埋め込み
  （`kiro-flow-repos-<pid>-…`）、daemon の定期掃除に `sweep_work_repo_dirs` を追加: **SIGKILL/OOM/電源断で
  finally が走らず残った孤立 clone を「所有 pid 死亡」を根拠に回収**（稼働中・`--keep-alive` 長命 worker の clone は
  経過時間に関わらず残す）。`cleanup_per_node`（CLI `--cleanup-per-node`）で各ノード完了/失敗ごとの即時削除を
  opt-in（長命 worker のディスク抑制）。`atexit` でプロセス終了時削除を二重化。テスト 2 件追加（kiro-flow 154 OK）。
  既存の削除経路（正常終了/エラー/agent タイムアウト/SIGTERM の finally＋signal）は従来どおり。
- 黒箱 CLI 統合テスト（`TestCliEndToEnd`）。`kiro-projects.py` を実プロセスとして argv 起動し、
  ループ機構を end-to-end で検証: drain→exit 0・成果物退避（archive）、verify 失敗→blocked→exit 1＋
  needs ファイル生成、予算超過→budget→exit 2、`--no-archive` で退避せず削除。`run_loop()` の in-process
  テスト（`TestRunLoop`）に対し、CLI 配線（argparse・パス解決・停止理由→exit code）を実バイナリで担保する。
- クロスツール統合テスト（`TestCliKiroFlowDelegation`）。autonomous CLI の act が実際に `kiro-flow.py` へ
  サブプロセス委譲して完走することを検証する。`--kiro-flow` にラッパを噛ませ、委譲 argv
  （`run --planner stub --executor stub …`）と委譲先 kiro-flow の正常終了（exit 0）を捕捉して assert する。
- GUIDE に「おすすめ構成（本番）」セクションを追加。**PC 起動時に両 daemon 常駐 ／ executor=gitlab ／
  bus=git** の完成形レシピ（kiro-flow.yaml / kiro-projects.yaml の雛形、systemd ユーザーサービス 2 本、
  `lock_dir` 一致・git 認証・`~/.kiro/` 自動探索の勘所、稼働確認コマンド）。L0–L4 を通した後の到達点を明示する。
- `--executor`（設定 `executor`）に kiro-flow の executor プラグインを指定できるようにした。組み込みの
  `kiro` / `stub` に加え、プラグイン名（例 `gitlab`）や `.py` パスをそのまま `kiro-flow run --executor <値>`
  へ委譲する（`choices` 制限を撤廃）。`kiro-projects.yaml.example` / README にも記載。`doctor` の
  kiro-flow 解決チェックも `executor != stub` の全 executor（プラグイン含む）を対象に拡張した。
- `doctor` サブコマンド。ログ/状態/環境から稼働を診断し、原因を **env（ユーザー環境固有）/
  config（設定）/ program（プログラム上の不具合）** に分類する。収集・修正・起票の駆動は決定的に、
  診断と分類は kiro-cli へ委譲（kiro-cli 不在時は決定的チェックのみで続行）。`--fix` で env/config を
  修正（`create-dirs` / policy への保護デニーリスト追記）し、program の不具合は `gitlab-idd` スキルで
  GitLab イシューを起票する。**スキルが見つからなければ出力のみ**。終了コード `0`=健康/`1`=所見あり/
  `2`=未解決の critical。既定（`--fix` 無し）は無害な診断のみ。
- `doctor` の **実行層 kiro-flow との連携**（`--with-flow`・既定 on／`--no-flow` で本体のみ）。
  同じバスに対して `kiro-flow doctor --json` を呼び、実行層の所見を `[flow]` 印で統合する。`--fix` 時は
  kiro-flow 側にも委譲し、kiro-flow が自分の env/config 修正と program 起票を担う（二重作業を避ける）。

#### Changed
- **charter `## repos` で同一 URL のエントリを base/target（ブランチ）でも区別できるようにした**。従来は同じ URL を
  複数エントリで使うと distinct な `path`（作業フォルダ）が必須だったが、`validate_charter` の一意キーを `path` 単独から
  `(path, base, target)` に拡張。ブランチ違い（例：`main` への修正と `release/1.x` へのバックポート）なら path 無しでも
  別エントリとして成立し、path も branch も全て一致するものだけを曖昧な重複として弾く。charter.md.example / README に
  ブランチ別の書き方を追記、単体テスト 1 件（ブランチ/target での区別）を追加し既存テストを新仕様へ更新。
- 内部リファクタリング（振る舞い不変・全機能維持・226 テスト green）。パッチ的に肥大化した実装を整理:
  重複していた `_pid_alive` 定義を削除、タイムスタンプ整形を `_now_ts()` に集約、kiro-flow コマンド構築の
  重複を `_kf_base` に統一。長い関数を凝集したヘルパに分割（`_settle_task`→review/done/failure、
  `run_loop`→`_run_setup`/`_budget_reason`、`cmd_project`→`_project_evaluate`）。外部挙動・CLI・出力は不変。
- `run` 起動時に、前回の異常終了（`kill -9` / クラッシュ / マシン再起動で `finally` が走らず残った）
  自ホストの死インスタンスレコードを register 前に prune するようにした。`instances` の発見ノイズと
  `start` の偽の重複検出を防ぐ。
- all-daemon の「all」センチネル（実体の無い擬似 root `<container>/projects/all`）を `instances` で
  `all-daemon` 印（`sentinel` フラグ）として表示し、実プロジェクトの監視レコードと明確に区別するように
  した。`projectA/default` 等は実プロジェクトの監視として従来どおり全件表示する。
- バスを明示設定（CLI `--bus` / 設定 `bus:`）したときは **`--project all` でも per-project バスへ上書きせず、
  全プロジェクトでその共有バスを使う**ようにした。従来は `--project all` が常に `<root>/projects/<name>/bus` へ
  上書きしていたため、別途常駐させた**単一の kiro-flow daemon を全プロジェクトから検知できなかった**
  （`location=auto/daemon` が常に local へフォールバック）。共有バスにすると同じ daemon ロックを全プロジェクトで
  参照でき、kiro-flow daemon を同じ bus で起動すれば warm worker を共有・再利用できる（submit の run_id は
  一意採番のため衝突しない）。example / README にも設定方法を追記。

#### Added
- charter の `## repos` / `## links` を**構造化サブ箇条書き**に対応。`- name = url` の下にインデントして
  `- desc:`（説明）/ `- base:`（ベースブランチ）/ `- target:`（ターゲット・既定 base）を付けられる（日本語キー
  desc=説明 / base=ベース / target=ターゲット も可）。複数リポジトリそれぞれに「内容物の説明」と「base/target
  ブランチ」を明示でき、タスクは説明を見て関係する repo を選び、その情報を個別タスク（gitlab イシュー等）へ
  伝搬できる。`## links` は wiki/ドキュメント URL 等も `- desc:` 付きで置ける。
- repos の必須項目検証。charter 駆動の実行開始時（`cmd_project`）に、各 repo の **`desc`（説明）と `base`
  ブランチが必須**であることを検証し、欠けていればエラーで停止して人へ知らせる（`target` は省略可・既定 base）。

#### Changed
- needs（判断待ち）と DELIVERY/archive（受領）の記述を充実化。人が成果物を見に行かずに判断できるよう、
  **「成果物の所在（リポジトリ/ブランチ/コミット・PR/MR）・差分（変更ファイル）・検証結果（PASS/FAIL）」**を
  まとめた「判断材料」を、blocked/review の needs ファイルと archive の納品書に載せるようにした（`delivery_evidence`）。
  DELIVERY.md の成果参照にも所在ブランチを併記。これまで「どこに成果物があるか・何が差分か・なぜ止めた/
  スキップしたか」が分からず判断できなかった問題を解消する。

#### Fixed
- **charter 駆動 watch が kiro-flow run の失敗終了を検知できず execute フェーズで永久待機する不具合を修正**。
  daemon/remote へ submit した run を待つ `_act_submit` は、`result --json` の `done`（＝終端 done/failed の両方）
  だけを見て **failed を success と取り違えて**いた。`status == "failed"` を act 失敗として返すようにし、
  orchestrator が異常終了して daemon が run を `failed` に確定したケースも**1 ポーリングで即検知**して
  `act_timeout` までの空待ちを避ける（verify=NG 相当で後段が retry/エスカレーション）。単体テスト 3 件
  （failed→失敗・done→成功・非終端は act_timeout で必ず返り永久待機しない）を追加。
- charter の `## repos`（対象リポジトリ）/`## links`（参考リンク）が act ワーカーへ渡る文脈
  （`charter_context`/`build_request`）に含まれていなかった不具合を修正。これらは parse 済みだったが
  `_charter_definition` が goal/constraints/assumptions/deliverables しか出力していなかったため、
  gitlab executor のイシュー等で**対象リポジトリ/ブランチ/説明が欠落**していた。goal 直後（truncation で
  落ちにくい位置）に、各 repo の説明・base/target ブランチと関連リンク（desc 付き）を含めるようにした。
- all-daemon の watch ループで heartbeat をラウンド毎に1回だけ更新するよう修正（従来は内側ループに
  あり、登録数 N に対し毎ラウンド N×(N+1) 回の無駄なファイル書き込みが発生していた）。
- `approve` / `hold`（`_block`）で古い claim ロック（`claims/<id>.lock`）を解放するよう修正。worker の
  クラッシュや review/blocked 滞留で残ったロックが人手解決後も残留し、TTL 切れまで次の実行を阻害しうる
  不備を解消（`release_claim` は冪等のため通常ケースは無害）。

### kiro-flow

#### Added
- **gitlab executor を GitLab REST 直叩き（native）化し、起票先 URL を kiro-flow.yaml から
  確実に渡すようにした**。従来は gitlab-idd スキルの外部 `gl.py` を subprocess 起動して
  イシュー化しており、起票先プロジェクトの解決が gl.py 側の `GL_PROJECT_URL`／
  connections.yaml／**git remote origin** フォールバックに依存していた（誤プロジェクトへの
  起票を招きうる）。`gl.py` 相当の必要処理（create-issue / get-issue / get-comments と REST
  呼び出し・ページング）を **stdlib のみ**でプラグインへ移植（`gl_api`/`gl_api_list`/
  `_parse_project_url` 等）。**gl.py への起動・フォールバックは廃止**（native 一本）。
  - **起票先 URL**: kiro-flow.yaml の `gitlab.repo_url` を権威とし、その URL をそのまま使う
    （git remote origin へは流れない）。未設定/解釈不能は明示エラー。
  - **トークン**: kiro-flow.yaml には置かず、**gl.py と同じ場所・同じ優先順**で解決する
    — connections.yaml（接続ラベル `conn_label`・config_loader 経由）→ 環境変数
    `GITLAB_TOKEN`/`GL_TOKEN` → シェル rc ファイル（`~/.bashrc` 等）。秘密情報を設定
    ファイルに残さない運用に合わせた。
  - イシュー操作は `_create_issue`/`_get_issue`/`_get_comments` に集約。kiro-flow.yaml.example /
    CONFIG_DEFAULTS のコメントを更新（`gitlab.token` は設けない）。
  単体テスト 20 件（起票/ポーリング/承認・クローズ完了・タイムアウト・repo_url 必須/SSH 拒否・
  URL 解析・REST リクエスト組立・HTTP エラー処理・トークン解決の優先順＝connections.yaml＞
  環境変数＞シェル rc、および kiro-flow.yaml の token を読まないこと）を追加。
- **自動アップデート（既定 on・6 時間毎・起動直後にも実施）**。スキルリポジトリ（配布元）の `main` に更新が
  入ったら、**daemon のアイドル時**（要求も子プロセスも無いとき）に取り込む。停止中に入った更新も起動直後に拾う。doctor と同じ流儀で決定的:
  `git ls-remote` で main の先頭を確認 → 適用済み SHA（`~/.kiro/kiro-flow.update.json`）と違えば、temp
  領域へ `tools/kiro-flow/` だけを **sparse-checkout** → `install.sh` 実行 → **動いていた cwd のまま
  `os.execv` で graceful 再起動**（子の terminate と daemon ロック解放を経て再起動）。手動は
  `update [--check|--now]`。**更新元 URL は `install.py` が生成する `skill-registry.json`
  （`repositories.origin.url` → `install_dir`）から自動解決**（`update_repo` 未指定でよい）。設定キー
  `update_enabled`（off スイッチ・既定 on）/ `update_check_interval`（既定 21600=6h） / `update_repo` / `update_branch` /
  `update_subdir` / `update_installer`。初回はベースライン記録のみ（無更新）。仕事中は何もしない。
  単体テスト `SelfUpdateTests`（11 件）を追加。
- `doctor` サブコマンド。run 状態/イベント/環境から稼働を診断し、原因を **env / config / program** に
  分類する。収集・修正・起票の駆動は決定的に、診断と分類は kiro-cli へ委譲（不在時は決定的チェックのみ）。
  `--fix` で env/config を修正（`ensure-bus`＝バス作成）し、program の不具合は `gitlab-idd` スキルで
  GitLab イシューを起票する（スキルが無ければ出力のみ）。`--json` の findings は kiro-projects の doctor と
  同一スキーマで、単独でも kiro-projects からの連携呼び出しでも使える。終了コード `0`/`1`/`2`。
- executor（ワーカーバス）のプラグイン化。kiro-loop の hooks（event_hook）と同じ流儀で、
  `--executor` に組み込み名（`kiro`/`stub`）に加えてプラグイン名（例 `gitlab`）や `.py` パスを
  指定できる。プラグインは標準ライブラリのみの単一ファイルで `execute(kind, goal, dep_results,
  model, art_dir, dep_arts)` を公開し、本体が `importlib` で動的ロードする（mtime キャッシュ付き）。
  検索順は スクリプト同階層 `executors/` → リポジトリ `tools/kiro-flow/executors/` →
  `~/.kiro/kiro-flow/executors/`（インストーラ配置）→ 設定 `executor_dir`。プラグイン固有設定は
  同名のトップレベル設定ブロックを JSON 化し環境変数 `KIRO_FLOW_EXECUTOR_CONFIG` で渡す。
  `install.sh` は同梱プラグインを `~/.kiro/kiro-flow/executors/` へコピーする。
- gitlab ワーカーバス（opt-in・`executors/gitlab.py` プラグイン）。`--executor gitlab` /
  設定 `executor: gitlab` を選ぶと、各ワーカータスクを gitlab-idd スキルの `gl.py` で GitLab
  イシュー化して委譲し、リモートのワーカーが実装・レビュアーが承認した結果を `get-issue` で
  ポーリングする。`status:approved`（または `status:done` / クローズ）に達したらそのタスクを
  完了とみなす。ポーリング間隔・タイムアウト・付与ラベルは設定 `gitlab:` ブロックで調整可。
  既定の executor は `kiro` のままで、明示選択時のみ有効になる。
- 作業後に sparse-checkout クローンを自動削除（既定 ON）。各コマンド終了時に
  ノード専用クローンを丸ごと掃除しクローンの溜まり込みを防ぐ。`--keep-clone` /
  設定 `cleanup_clone: false` で従来どおり残して再利用も可能。
- 中間成果物のファイル参照プロトコル。`output`/`data` に乗らない大きな成果物は
  決定的なディレクトリ `runs/<run-id>/artifacts/<node-id>/` に書き出し、後続タスクは
  依存ノードの同じパスを読んで発見できる。ワーカーは生成した成果物を result に記録し、
  `result` コマンドでも一覧できる。

#### Fixed
- **stub プランナーが構造化された複数行の要求を 1 行ずつ別タスクへ細切れにし、charter の
  対象リポジトリ一覧などが gitlab executor の各イシューのタイトル/目的を埋める不具合を修正**。
  kiro-cli が無い委譲シナリオでは LLM プランナーが `plan_stub` にフォールバックするが、`plan_stub`
  は改行をすべてタスク境界として扱っていたため、`build_request` が組み立てる charter 文脈（目標・
  完了条件・**対象リポジトリ行**・制約…）の 1 行 1 行が別タスク＝別イシューになり、repos の内容が
  タイトルや各節に繰り返し現れていた。改行は**空行を含まないフラットな簡易リストのときだけ**区切りと
  みなすようにし（`"\n\n"` を含む構造化要求は 1 件の要求として扱う）、見出し（タイトル相当）は
  `_first_line` で**先頭の非空行**に統一して本来の目的が 1 行で読めるようにした。明示区切り
  `;`/`->` は従来どおり。回帰テスト 6 件（簡易リストは従来どおり分割・構造化要求は細切れにしない・
  既定 fan-out パターンでも goal に repos が出ない・`_first_line`）を追加。
- **成果物リポジトリの clone 指示が goal 先頭に結合され、gitlab executor のイシュー タイトル/目的が
  指示テキストで埋まって本来の goal が見えなくなる不具合を修正**。`cmd_work` が clone 指示
  （`repo_instruction`）を `goal` の先頭へ文字列結合してから executor に渡していたため、gitlab は
  タイトル（`goal[:80]`）も本文の『## 目的』も clone 指示で占有されていた。executor 契約に任意の
  `repo_instruction` 引数を追加し、新設の `call_executor` が **clone 指示を goal とは別引数で渡す**
  ように変更（受け取れない旧プラグインには従来どおり goal 先頭へ結合してフォールバック＝後方互換）。
  `execute_kiro`/`execute_stub` は `repo_instruction` を受理（kiro はプロンプトへ別途付与）。gitlab は
  タイトルと『## 目的』に**本来の goal のみ**を出し、clone 指示は本文の独立節『## 成果物リポジトリ』に
  載せる。単体テスト 8 件（引数受理判定・新/旧 executor の分岐・kiro プロンプト・イシュー本文の節分離・
  タイトル/目的が本来の goal）を追加。
- **daemon が再起動すると孤児 run（owning daemon が消失した非終端 run）を復旧できず永久待機する不具合を修正**。
  上記の異常終了検知は「死んだ子（orchestrator）を自分で刈り取れる」前提で、**daemon プロセス自体が落ちて
  再起動した**ケース（remote/分散実行）を救えていなかった。再起動した新プロセスは `orchestrators` を引き継がず、
  前プロセスが残した `status:running` を見て `run_exists` で受理をスキップするだけ＝何もせず、remote へ
  `submit` した消費者は `act_timeout`（既定 1800s）まで待たされていた。**run 生存リース（heartbeat）**を導入し、
  daemon は駆動中の run の `meta` に `orch_lease_until`/`heartbeat_at` を毎 poll 更新（git バスへは間引いて push）。
  各 poll で **inbox 由来・自分が回しておらず・リース切れ**の run を `mark_run_failed` で `failed` 確定する
  （`Bus.touch_run`/`run_is_orphaned`/`_recover_orphan_runs`、リース窓 `_run_lease_window` ＝ `max(poll×10, 120s)`）。
  リース未記録の旧 run／heartbeat 前に死んだ run は作成 age で判定し、作成直後の run は孤児扱いしない（spawn 直後の
  race と他デーモンの生存 run の誤回収を防止）。これで再起動／別デーモンが ~リース窓内に run を `failed` 化し、
  消費者（PR の `_act_submit` 失敗検知と連携）が `act_timeout` を待たず復旧できる。単体テスト 11 件を追加。
- **daemon が orchestrator の異常終了を run の失敗として確定せず、run が非終端のまま放置される不具合を修正**。
  orchestrator（`orchestrate`）が `done` を書く前にクラッシュ／kill／起動失敗で終了すると、daemon は死んだ子を
  `del orchestrators[rid]` するだけで run の `status` を更新せず、`result`/`status` を待つ消費者
  （kiro-projects の charter 駆動 watch 等）が**永久待機**に陥っていた。死んだ orchestrator を刈り取る際に
  exit code を確認し、run がまだ終端でなければ `Bus.mark_run_failed` で `failed`（`failure_reason` 付き）に
  確定して `run-failed` イベントを記録・push するようにした（正常完了済みの run は上書きしない冪等動作）。
  これで `result --json` の `done=True`/`status=failed` として失敗終了が即座に消費者へ伝わる。単体テスト 4 件を追加。
- gitlab executor プラグインで、イシューの起票先が設定 `gitlab.repo_url` にならず git remote origin に
  フォールバックする不具合を修正。`run`/`daemon` が子プロセス（orchestrator/worker）へ **`--config` を
  引き継いでいなかった**ため、実際に `execute()` を呼ぶ worker が `gitlab:` ブロック（`repo_url` 含む）を
  再解決できず既定（空）になっていた。親が解決した設定パスを絶対パスで全子プロセスへ伝搬するようにした
  （プラグイン固有設定全般に効く）。
- 上記をさらに堅牢化: daemon が worker を起動する `_spawn_worker` で、親（daemon）が解決した executor
  プラグイン設定ブロック（例 `gitlab:` の `repo_url`/`conn_label`）を **`KIRO_FLOW_EXECUTOR_CONFIG` として
  worker の起動 env に明示注入**するようにした。worker が `--config` を再解決できない／別の設定ファイルを
  拾う状況でも親の設定が確実に届く。解決ロジックを `resolve_executor_config_json(args)` に集約し
  `make_executor` と共有。worker 側 `make_executor` は自分で設定を解決できたときだけ env を更新し、
  解決できない（空/None）ときは親が注入した値を上書きしない。テスト 4 件を追加。
- judge/評価役のサーキットブレーカー。同一系統の作り直し（verify=fail の再生成・
  失敗タスクの retry）が `--max-retries`（設定 `max_retries`, 既定 3）に達したら
  打ち切る。達成不可能な完了条件に対し無限に再タスクを積み続ける暴走を防ぐ
  （`--max-iterations` と二重ガード）。
- 依存タスクの成果物が大きいとき、kiro-cli へ渡すプロンプトが OS のコマンドライン長
  制限（ARG_MAX）に達して起動失敗する不具合を修正。一定サイズを超えるプロンプトは
  一時ファイルへ退避し参照渡しに切り替える（設定 `argv_limit` / `--argv-limit` で調整、既定 100000）。
- `GitBus._ensure_clone()` の sparse-checkout が親リポジトリに作用しうる不具合を修正。クローン先
  （`<bus>/<node>`）が親リポジトリの作業ツリー配下にある場合、workdir 直下に自前の `.git` が無いと
  git が親へ遡って最寄りの `.git` を掴み、`sparse-checkout` が**親リポジトリの作業ツリーを cone 化して
  隠してしまう**ことがあった。再利用は「`self.remote` を origin とする自前クローンのルート」に限定し、
  それ以外（親/別リポジトリ・非空の他ディレクトリ）には sparse-checkout を適用せず明示的に中断する。
- `GitBus` が **同一 remote の既存フルチェックアウト**（ユーザーの作業リポジトリ等）を `--bus` のクローン先に
  指定された場合に、`sparse-checkout`（cone）で **subdir 以外の追跡ファイルを作業ツリーから隠してしまう**
  不具合を修正（kiro-projects の `--git-bus`/`--git-subdir` 経由で発生しうる）。自前管理のバスクローンに
  目印（git config `kiro-flow.busclone=1`）を付け、再利用は「目印付き／既に sparse 済みの自前クローン」に
  限定。kiro-flow 管理外の既存チェックアウトには sparse-checkout せず明示的に中断する。あわせて全ての
  `git -C <workdir>` 実行に `GIT_CEILING_DIRECTORIES` を設定し、workdir 直下に `.git` が無くても親リポジトリへ
  遡れないよう多重防御した。

#### Added
- daemon/submit の黒箱統合テスト（`DaemonE2ETests`）。`daemon` を実プロセスとして常駐させ、`submit` 投入から
  orchestrator/worker のオンデマンド起動を経て `final.json` 生成（全ノード done）まで通す。複数 submit を
  並行に独立 run として完走させる経路も検証。bus プリミティブの in-process テスト（`DaemonPrimitiveTests`）に
  対し、常駐プロセス＋オンデマンド起動の配線を実プロセスで担保する。

#### Changed
- 内部リファクタリング（振る舞い不変・全機能維持・144 テスト green）。kiro-projects と同様に、
  パッチ的に重複した実装を整理: 子プロセス argv 構築を `_child_base()` に統一（`cmd_run`/`cmd_daemon` の重複解消）、
  モード表記を `_mode_string()` に集約、daemon の singleton ロック取得を `_acquire_daemon_lock()`・
  orchestrator/worker 起動を `_spawn_orchestrator()`/`_spawn_worker()` に分割、`cmd_orchestrate` の統合処理を
  `_finalize_run()` に分割。CLI・出力・挙動は不変（argparse は `--model`/`--model_opt` 等の差があるため共通化せず温存）。
- `install.sh` の executor プラグイン配置先を **本体（kiro-flow バイナリ）と同じフォルダ**
  （`<install-prefix>/executors/`、既定 `~/.local/bin/executors/`）に変更（旧: `~/.kiro/kiro-flow/executors/`）。
  kiro-loop と同じ「本体隣」の補助アセット配置に揃え、検索順 #1「スクリプト同階層の `executors/`」で
  名前解決できるようにした。`~/.kiro/kiro-flow/executors/` は後方互換の検索先として残す。

---

## [v1.0.0] — 2026-06-20

Initial release. 188 tests passing (kiro-flow + kiro-projects).

### kiro-projects

#### Added
- 並列消費 — kiro-flow の worker 並列へ寄せる（§11）
- 共有レジストリ越しの別ホスト発見（§11-7）
- 汎用の取り込み口 enqueue / inbox（§11-5）
- 常駐ライフサイクル start / stop / restart（§11-4）
- 自律裁定の判断材料を拡充（§11-3）
- 真偽フラグを設定ファイル対応（§11-1）
- コスト予算（トークン/金額の上限と per-task 計上）（§11-2）
- Loop Engineering 中核4機能（計測・自己生成・依存・回帰ゲート）
- 検収ゲート — verify=PASS でも人の承認を要する review 状態
- 自律裁定フック（needs 直前で kiro-cli が積み直し可否を判断）
- 設定ファイル対応（YAML 任意 / JSON フォールバック）＋サンプル
- 稼働インスタンスのレジストリ追加＋スキルを WSL/Windows 対応に
- サブコマンド省略時を `run --watch`（常駐監視）の既定に
- ltm-use への学習昇格（プロジェクト横断・エージェント不要）
- 編集完了の明示検知と成果物の納品書
- ファイルを `.kiro-projects/` に集約・一時バスを自動クリーンアップ
- DR 学習と rot 検知

#### Changed
- `auto_adjudicate` の既定を on に変更

### kiro-flow

#### Added
- flow-planner をデフォルト planner に変更し `~/.kiro/skills` のフォールバック追加
- flow-planner スキル — kiro-flow orchestrator 向け 3 フェーズパイプライン
- タスクタイムアウト機構（kiro-cli 呼び出しの無限ハング防止）
- 最終結果プレゼンテーションとコマンドアップデート
- 一時ファイルの自動クリーンアップ

---

[v1.0.0]: https://github.com/ynitto/sandbox/releases/tag/v1.0.0
