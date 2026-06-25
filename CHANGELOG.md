# CHANGELOG

All notable changes to this project are documented in this file.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) — versions use [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### マルチリポジトリ・ルーティング（kiro-autonomous × kiro-flow・破壊的変更）

大規模・複数リポジトリのプロジェクトを自律運用するため、「タスク → コミット先リポジトリ」のルーティングを導入した。
**判断は制御層（kiro-autonomous）に集約し、執行は実行層（kiro-flow）が担保する。** 設計の詳細は
`tools/kiro-autonomous/ROUTING.md`。後方互換は取らない（旧 `--repo`／タスク `- repos:` は廃止）。

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

#### kiro-autonomous

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
- 単体テストを新 API へ更新（kiro-flow・kiro-autonomous 両スイート、計 472 件 green）。

### kiro-autonomous

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
  確認 → 適用済み SHA（`~/.kiro/kiro-autonomous.update.json`）と違えば、temp 領域へ `tools/kiro-autonomous/`
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
  kiro-autonomous 5 件・kiro-flow 7 件のテストを追加（全 240 / 152 OK）。
- **成果物リポジトリ clone の削除機構を強化**（kiro-flow）。temp clone 名に所有 pid を埋め込み
  （`kiro-flow-repos-<pid>-…`）、daemon の定期掃除に `sweep_work_repo_dirs` を追加: **SIGKILL/OOM/電源断で
  finally が走らず残った孤立 clone を「所有 pid 死亡」を根拠に回収**（稼働中・`--keep-alive` 長命 worker の clone は
  経過時間に関わらず残す）。`cleanup_per_node`（CLI `--cleanup-per-node`）で各ノード完了/失敗ごとの即時削除を
  opt-in（長命 worker のディスク抑制）。`atexit` でプロセス終了時削除を二重化。テスト 2 件追加（kiro-flow 154 OK）。
  既存の削除経路（正常終了/エラー/agent タイムアウト/SIGTERM の finally＋signal）は従来どおり。
- 黒箱 CLI 統合テスト（`TestCliEndToEnd`）。`kiro-autonomous.py` を実プロセスとして argv 起動し、
  ループ機構を end-to-end で検証: drain→exit 0・成果物退避（archive）、verify 失敗→blocked→exit 1＋
  needs ファイル生成、予算超過→budget→exit 2、`--no-archive` で退避せず削除。`run_loop()` の in-process
  テスト（`TestRunLoop`）に対し、CLI 配線（argparse・パス解決・停止理由→exit code）を実バイナリで担保する。
- クロスツール統合テスト（`TestCliKiroFlowDelegation`）。autonomous CLI の act が実際に `kiro-flow.py` へ
  サブプロセス委譲して完走することを検証する。`--kiro-flow` にラッパを噛ませ、委譲 argv
  （`run --planner stub --executor stub …`）と委譲先 kiro-flow の正常終了（exit 0）を捕捉して assert する。
- GUIDE に「おすすめ構成（本番）」セクションを追加。**PC 起動時に両 daemon 常駐 ／ executor=gitlab ／
  bus=git** の完成形レシピ（kiro-flow.yaml / kiro-autonomous.yaml の雛形、systemd ユーザーサービス 2 本、
  `lock_dir` 一致・git 認証・`~/.kiro/` 自動探索の勘所、稼働確認コマンド）。L0–L4 を通した後の到達点を明示する。
- `--executor`（設定 `executor`）に kiro-flow の executor プラグインを指定できるようにした。組み込みの
  `kiro` / `stub` に加え、プラグイン名（例 `gitlab`）や `.py` パスをそのまま `kiro-flow run --executor <値>`
  へ委譲する（`choices` 制限を撤廃）。`kiro-autonomous.yaml.example` / README にも記載。`doctor` の
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
  GitLab イシューを起票する（スキルが無ければ出力のみ）。`--json` の findings は kiro-autonomous の doctor と
  同一スキーマで、単独でも kiro-autonomous からの連携呼び出しでも使える。終了コード `0`/`1`/`2`。
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
  （kiro-autonomous の charter 駆動 watch 等）が**永久待機**に陥っていた。死んだ orchestrator を刈り取る際に
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
  不具合を修正（kiro-autonomous の `--git-bus`/`--git-subdir` 経由で発生しうる）。自前管理のバスクローンに
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
- 内部リファクタリング（振る舞い不変・全機能維持・144 テスト green）。kiro-autonomous と同様に、
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

Initial release. 188 tests passing (kiro-flow + kiro-autonomous).

### kiro-autonomous

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
- ファイルを `.kiro-autonomous/` に集約・一時バスを自動クリーンアップ
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
