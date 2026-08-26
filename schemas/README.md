# schemas/ — ツール横断の共通スキーマ

agent-project・agent-flow・codd-gate・agent-amigos が**データ契約だけで**結合するための独立スキーマ。
ツール同士は互いの実装を知らず、ここで定義する形式だけを読む/書く（結合は常に一方向×データ）。

| スキーマ | 何の契約か | 所有者（変更の主導） |
|----------|-----------|--------------------|
| [`repos.schema.json`](repos.schema.json) | リポジトリレジストリ（identity = **(url, path, base)**＝パス＋ブランチで一意） | 共有（本ディレクトリが正典） |
| [`task.schema.json`](task.schema.json) | 制御層タスク（バックログ 1 件）の JSON 表現 | agent-project（Markdown 形の正典は `tools/agent-project/backlog.md.example`） |
| [`agent-interaction.schema.json`](agent-interaction.schema.json) | agent-flow の human 工程における request / append-only response / immutable resolution | 共有（検証と決着は `tools/agent-tools/agentcore/agentcore/interaction.py`） |
| [`agent-workflow.schema.json`](agent-workflow.schema.json) | 人が描いた工程グラフ。**2 段で形が違う**——ライブラリ定義（`workflows/<id>.json` 同梱 / 登録フォルダの `.agents/workflows/` / ユーザー領域。agent-dashboard が編集・保存）と、投入 plan（inbox 要求の `plan` / `--plan-file`。agent-flow が planner を通さず検証だけして固定する）。変換は dashboard の `planFromWorkflow` が行い、工程の作業ルールは本文へ畳まれて goal へ入る（plan に methods フィールドは無い）。**グラフ不変条件（id 一意・deps の実在・循環なし・entry=ルート / exit=末端・split の後段を静的に張らない）は JSON Schema で表現できないため実装が正典** | 共有（本ディレクトリが文書の正典。保存側の 1 実装は agent-dashboard の `normalizeWorkflow`、実行側は agent-flow の `plan_strategy_user`＝丸めずに失敗させる。語彙の一致は両側のテストで担保 — flow `WorkflowSchemaAgreementTests` / dashboard `adhoc-flow.test.js`） |
| [`agent-node-data.schema.json`](agent-node-data.schema.json) | 公開ノード kind と human / extract / retrieve の根拠付き結果 | 共有（検証は `tools/agent-tools/agentcore/agentcore/nodecontract.py`） |
| [`verification-plan.schema.json`](verification-plan.schema.json) | 統一 verify の検証計画 — 受入基準（自然文 criterion・出現順 C1, C2, … 採番）＋任意の固定検証コマンドを canonical JSON の SHA-256 digest 付きで直列化。agent-project が確定し、agent-flow の専用 runner が成果 revision 上で一度だけ実行する | 共有（本ディレクトリが正典。設計は `docs/plans/2026-07-30-unified-task-verify-design.md`。digest・採番の 1 実装は `agentcore/verifycontract.py`） |
| [`verification-receipt.schema.json`](verification-receipt.schema.json) | 統一 verify の receipt — plan digest・result revision・command 終了コード・criterion ごとの verdict（pass / fail / inconclusive）と証拠。agent-project が検算し、一致した PASS だけを done 候補に採用（fail-close）。他ノードへの検証委譲（external.json）も同じ schema | 共有（本ディレクトリが正典。全体判定の再導出と検算は `agentcore/verifycontract.py` の `receipt_overall` / `receipt_errors`） |
| [`knowledge-observation.schema.json`](knowledge-observation.schema.json) | 知識観測 envelope（Phase 3）— observation ID・rules hash / skill 参照・receipt の plan_digest/result_rev 参照。既存 brief/decisions と run meta.knowledge へ additive。agent-flow は解釈せず素通し | 共有（本ディレクトリが正典。設計は `docs/plans/2026-07-29-agent-tools-distributed-credit-knowledge-plan.md` §3.2） |
| [`node-budget.schema.json`](node-budget.schema.json) | ノード単位の予算 v2 — トークン一次（実行時間上限は v1 互換で AND）＋配分宣言（`$AGENT_BUDGET_DIR`＝既定 `~/.agents/budget/` の config.json ＋ ledger/<YYYYMMDD>.jsonl） | 共有（本ディレクトリが正典。初出は agent-amigos 仕様書 §5.2、v2 は `docs/plans/2026-07-19-agent-dashboard-orchestration-token-budget-design.md`） |
| [`agent-control.schema.json`](agent-control.schema.json) | 管理面→各エンジンの宣言的オーケストレーション（`$AGENT_CONTROL_DIR`＝既定 `~/.agents/control/` の control.json ＋ status/<tool>-<pid>.json）。エージェント CLI / モデルの横断上書き・縮退・一時停止 / 停止・委譲誘導。優先順位は control > CLI 引数 > 設定ファイル > 組み込み既定。version 2 で `workloads.<wl>.selection_policy`（候補ベースのコンパイル済み選択方針）を additive に追加 | 共有（本ディレクトリが正典。設計は `docs/plans/2026-07-19-agent-dashboard-orchestration-token-budget-design.md`、selection_policy は `docs/plans/2026-08-15-agent-tools-candidate-execution-policy-dashboard-design.md` §6.3） |
| [`agent-profiles.schema.json`](agent-profiles.schema.json) | agent-dashboard 専有の実行プロファイル自動選択（`$AGENT_CONTROL_DIR` の profiles.json）。単純作業/軽量/標準/高性能の実行レベル別に候補（agent_cli+model）を宣言し、ワークロードの予算残率（node-budget）と agent CLI ごとの枠（`node-budget` の `allocation.agents`）から純関数でレベルと候補を決定し、agent-control へ選択結果だけを投函する。**不変条件: エンジンはこの契約を読まない**。書き手は agent-dashboard と、実測から候補列を昇格・退役させる `agent-audit tune --apply`（`tiers.<name>.candidates` だけ）の 2 つ | agent-dashboard（本ディレクトリが正典。設計は `docs/plans/2026-08-05-phase1-token-efficiency-detailed-design.md` §1、還流は `docs/designs/agent-audit-design.md` §4.2） |
| [`agent-recommendation.schema.json`](agent-recommendation.schema.json) | 評価 archive から決定的に生成する**読み取り専用のおすすめ構成**（`~/.agents/recommendation.json`）。ローカル主体で回すために人が踏む手順のうち、端末に依存しない定数（実行レベルの構成・実行方針・同時実行数・適格性 seed・必要なモデル・根拠）を 1 個のデータ資産へ畳む。**制御面ではない**——`profiles.json` / `control.json` / `qualifications.json` は端末ごとの実測と枠に依存するので、この文書は「何が推奨か」だけを言い、適用は agent-dashboard（実行レベル・方針・同時実行数）と agent-audit（適格性）が行う。したがってインストーラが配ってよい（`agents/*.json` と同じ経路）。ローカルの候補は **`herd` の 1 語**で、クラウドは実測できないので`slots`（枠）として宣言し値は適用時に人が選ぶ | 共有（本ディレクトリが正典。生成の 1 実装は `tools/agent-tools/eval/recommend.py`——適格性の生成は `qualification_seed.py` を再利用し第 2 実装を作らない。設計は `docs/plans/2026-08-26-agent-tools-recommended-setup-simplification-design.md` §3.2 / §3.3） |
| [`agent-candidate-qualifications.schema.json`](agent-candidate-qualifications.schema.json) | 候補適格性 — 実行候補（`agent_cli + model`）×処理種別の実測格付け（qualified / trial / blocked / unknown）と、昇格条件を所有する version 付き evaluation profile。**管理面専用でエンジンは読まない**。writer は agent-audit のみ（revision 付き原子的置換）、readers は agent-dashboard / Resource Controller / Execution Policy Compiler | agent-audit（本ディレクトリが正典。検証の 1 実装は `agentcore/executioncontract.py`。設計は `docs/plans/2026-08-15-agent-tools-candidate-execution-policy-dashboard-design.md` §6.2） |
| [`execution-receipt.schema.json`](execution-receipt.schema.json) | 実行 receipt 共通ブロック — flow result / loop run / amigos turn / audit call へ加算的に埋め込む「どの候補が・なぜ選ばれ・検査がどうなったか」（execution_decision / verification / resource_snapshot）。append-only。Dashboard と agent-audit は設定から実モデルを再推測せず、この receipt を正典にする | 共有（本ディレクトリが正典。writer は実行した各 Adapter だけ。検証の 1 実装は `agentcore/executioncontract.py`。設計は同上 §6.5） |
| [`agent-instructions.schema.json`](agent-instructions.schema.json) | 管理面→各エンジンのノード共通指示（`$AGENT_INSTRUCTIONS_DIR`＝既定 `~/.agents/instructions/` の instructions.json）。指示文・推奨スキル（名前参照）・ツール方針を各エンジンが決定的に描画して実行エージェントのプロンプトへ前置。agent-flow は run の meta.json スナップショットで委譲先ノードへ伝播。適用状況は agent-control status の `instructions_revision_applied` に相乗り。最弱の層（タスク > brief > charter/rules > 共通指示） | 共有（本ディレクトリが正典。設計は `docs/plans/2026-07-19-agent-dashboard-global-instructions-design.md`） |
| [`agent-session-commands.schema.json`](agent-session-commands.schema.json) | 管理面→各エンジンのセッション開始コマンド（`$AGENT_SESSION_DIR`＝既定 `~/.agents/session/` の session.json）。セッションが始まった直後に配列順で 1 回だけ実行する前準備。`process` はホストのシェルで実行して完了を待ち、`chat` はセッションへ最初のプロンプトとして送る（単発系にはセッションが無いのでスキップ）。`when` で engines / workloads / agent_cli を絞れる。適用状況は agent-control status の `session_commands_revision_applied` に相乗り。**agent-instructions と違い委譲先ノードへ伝播しない** — 副作用のあるコマンドの到達範囲を各ノードのローカル設定へ閉じ込める | 共有（本ディレクトリが正典。設計は `docs/plans/2026-07-20-agent-dashboard-session-commands-design.md`） |
| [`agent-tuning.schema.json`](agent-tuning.schema.json) | agent-loop の汎用注入・起動環境と、agent-flow / agent-loop の手法パック・2 variant trial（`$AGENT_TUNING_DIR`＝既定 `~/.agents/tuning/` の tuning.json）。`methods[].when` は engine / workload / CLI / model / role / purpose / 実行段 / 相対コストで絞る。カタログ有効化は snapshot と `source: methods/<id>@<hash>` を保存するため、カタログ更新だけでは稼働が変わらない。`profiles.external-facing` の文体注入なしは従来どおり | 共有。読み手は agent-flow / agent-loop、書き手は人・agent-dashboard・`agent-loop methods`・`agent-audit tune --apply`（許可パスのみ）。設計は `docs/plans/2026-08-08-agent-tools-resource-efficiency-plan.md` F9 / F17 |
| [`agent-node-command.schema.json`](agent-node-command.schema.json) | 管理面→常駐体（`agent-project serve`）のノード宛て指示ドロップ（`$AGENT_COMMANDS_DIR`＝既定 `~/.agents/commands/` の `<name>.json` ＋ `processed/<name>.json` ＋ `<name>.json.err`）。委譲公示板への入札 / 中止 / 落札（`board-bid` / `board-cancel` / `board-award`）。**板はプロジェクトに属さない**ので、プロジェクト配下の `commands/` ではなくノードスコープに置く（プロジェクトを 1 つも持たないワーカーノードからも板を操作できるように）。宣言的な agent-control と違い**一度きりの行為**で、取り込まれたらファイルは消える。書き手は板へ直接書かない — 板への書き込みと push は常駐体だけ | 共有（本ディレクトリが正典。設計は `docs/plans/2026-07-26-s8-s9-4-board-ui-and-doctor-chat-detailed-design.md`。取り込みの正典は `agent_project/resident_cli.py` の board tick、土台は `agentcore/commands.py`） |
| [`mission.schema.json`](mission.schema.json) | 協働ミッションの公示（agent-amigos の `post --roles` に渡すミッション + 役割ミッション表）。バスへ書かれる読取契約（外部ビュアーが読む `mission.json` / `MANIFEST.json` / `final.json` / `cancelled.json`）は `$defs` に文書化 | agent-amigos（検証は stdlib パーサ `normalize_mission`。スキーマは文書化とテスト突き合わせ — enum/既定値の一致をテストで担保） |
| [`amigos-command.schema.json`](amigos-command.schema.json) | agent-amigos への指示ドロップ（`<home>/.agents/agent-amigos/commands/*.json` — post / claim / assign / accept / reject / cancel / say）。投函側は人・agent-dashboard、取り込み側は常駐デーモン | agent-amigos（取り込みの正典は `agent_amigos/commands.py`。コマンド一覧の一致を両側のテストで担保 — Python `CommandSchemaTests` / dashboard `amigos.test.js`） |
| [`delivery.schema.json`](delivery.schema.json) | agent-amigos の納品書（accept 時にオーナーホームの `deliveries/<mid>/delivery.json` へ書かれる受領記録）。バスの `MANIFEST.json` が integrator の組み立て記録（gc 対象）なのに対し、こちらは受入という事実と搬出先の永続記録 | agent-amigos（書き手は owner デーモン。読み手は agent-dashboard の納品一覧と `agent-amigos deliveries`） |
| [`delegation.schema.json`](delegation.schema.json) | agent-dashboard から agent-flow / agent-amigos への委譲をエンジン非依存に扱う封筒（post / award / accept / reject / cancel）と正規化ビュー（`$defs.delegation_view` — 公示→入札→落札→受入の観測）。バス・claim プロトコルは統一せず、dashboard のエンジン別アダプタがネイティブ形式（amigos-command / flow inbox）へ決定的に変換する。共通 id を両エンジンの native id に採用（対応表なし）。additive: `requires`（入札資格 tags/agent_cli/repos）・`speculation`（投機同時実行）は委譲公示板（agent-board）だけが解釈する。dashboard 側実装済み（`tools/agent-dashboard/src/features/delegation/`） | 共有（本ディレクトリが正典。設計は `docs/plans/2026-07-19-delegation-contract-design.md`。契約一致は dashboard `test/delegation.test.js` で `amigos-command` enum と突き合わせ） |
| [`board.schema.json`](board.schema.json) | 委譲公示板（agent-board）のバス契約 — 専用リポジトリ（またはローカル dir）に置く板のファイルレイアウト（`nodes/<id>` 登録・`delegations/<id>/{post,bids,award,status,results,result,cancelled}`）。**agent-board は処理を持たず「リポジトリ＋契約」だけ**で、入札・引き渡しは請負側デーモン（agent-flow の `agent_flow/board.py` / agent-amigos の `agent_amigos/board.py`）が担う。公示本体は `delegation.schema.json` の op=post 封筒そのまま、入札は両エンジンと同一仕様の名前空間付き claim ＋ `(ts, who)` タイブレーク（同じ仕様・別実装）。真実は板のファイル・中央（forge）は転送のみ。成果物リポジトリでノードを選別（node.repos × workspace.url を identity 照合） | 共有（本ディレクトリが正典。公示は agent-project `board-offload` / dashboard、入札は agent-flow / agent-amigos の daemon。契約一致は各ツールのテスト＝flow `BoardParticipationTests` / amigos `BoardParticipationTests` / dashboard `test/delegation-board.test.js`） |
| [`audit-record.schema.json`](audit-record.schema.json) | agent-audit の正規化レコード — 源泉を読み取り専用で正規化した `records/<YYYYMMDD>.jsonl`（追記専用）の 1 行。kind ∈ {ledger, event, run, result, session}。適用手法セットと trial variant も保持する。transcript 本文は入れず、実測（measured）と推定は集計で混ぜない | agent-audit（設計は docs/designs/agent-audit-design.md、契約は docs/specs/agent-audit-spec.md §2。書き手は agent-audit のみ） |
| [`audit-session-log.schema.json`](audit-session-log.schema.json) | agent-audit の統一セッションログ — collect（`--with-transcripts` / 設定 `with_transcripts`）が CLI ネイティブセッションを正規化して書く `transcripts/<agent_cli>/<session_id>.jsonl`。1 ファイル = 1 セッション、先頭 1 行が `type:meta`（エージェント・モデル・期間・実測トークン・`record_id`）、以降が `type:message`（クリーニング済み本文）。`record_id` で `audit-record` の kind:session と突き合わせる。**ローカル専用**——会話本文はノード外へ出さない。保持は `gc_keep_days.transcripts` のローテーション | agent-audit（書き手は collect / reclean のみ。読み手はローカルの解析モジュール） |
| [`audit-insight.schema.json`](audit-insight.schema.json) | agent-audit の観測（extract=map の出力・`observations/*.jsonl`）と洞察（distill=reduce の出力・`insights/<id>.json`）。洞察 → 観測 → レコードの参照鎖を欠かさず、review が refuted の洞察は改善タスク（task.schema.json 形の `agent-audit tasks` 出力）から除外される | agent-audit（同上。intake 側は agent-project の汎用 intake_cmd / enqueue --json） |

## node-budget — 誰がどう読む/書くか

- **各ツール（記帳・抑制側）**: 1 回の agent CLI 実行ごとに ledger へ 1 行追記
  （O_APPEND・追記専用）し、実行前に「合計消費 ≥ 上限」なら新規実行を控える。
  workload は `routine`（agent-loop 定常業務）/ `project`（agent-project）/
  `flow`（agent-flow）/ `amigos`（agent-amigos）。**全ワークロード実装済み**:
  - `amigos`: ターン前チェック → 超過中は amigo を paused にし owner へ通知。ターンの
    CLI 実行秒を記帳。
  - `flow` / `project`: LLM 単一チョークポイント（`run_agent` / `_run_agent_cli`）で
    実行前チェック → 超過は `[agent-error:quota] [node-budget]` として既存の環境要因
    フローに乗る（run 即終端・リトライを焼かない／裁定を呼ばず needs へ）。成功実行の
    実測秒を記帳。
  - `routine`（agent-loop）: スケジューラがサイクル先頭でチェックし、超過中は定期送信・
    webhook キューの dispatch を停止（10 分ごとに警告ログ・キューは保持）。実行秒は
    **セマフォスロットの保持時間**（送信 → 完了検知）で近似して解放時に記帳する
    （`max_concurrent <= 0` でセマフォ未使用のときは計測点が無く記帳されない、が既知の制約。
    タイムアウト強制解放は実行時間として数えない）。
- **管理面（agent-dashboard / 各ツール CLI）**: config.json を書き（合計上限
  `execution_minutes`・期間 `period: day|month|total`・ワークロード別内訳上限。
  **0 = 無制限**）、ledger を読んで消費内訳を表示する。依頼側・請負側どちらの
  ノードでも同じ契約（CLI 例: `agent-amigos budget node --limit-minutes 240`）。
  実装済み: agent-dashboard の **Amigos タブ**（`tools/agent-dashboard/src/features/amigos/`）
  がこの契約でワークロード別消費の表示と上限編集を行う。
- 超過チェックはロックなしの読み合計で、上振れは「進行中実行 × 同時実行数」に有界。
  台帳は日付ファイル分割なので日次/月次の集計と gc（古い日付の削除）が安い。
- **v2（トークン一次・設計済み、段階導入中）**: 台帳の必須項目は従来どおり `seconds` のまま、
  実測できた実行だけ `tokens_in` / `tokens_out`（＋ `agent_cli` / `model` / `usd`）を追記する。
  トークン未報告の行は読む側が config の `rates`（tokens/秒。解決は `cli:model` → `cli` →
  default）で**読み出し時に推定**する——台帳には事実のみ、推定値は書かない。config には
  `tokens`（期間内トークン合計上限）と `allocation`（weight / min_tokens / max_tokens /
  `on_exhausted: pause|stop|degrade` / soft_ratio）を宣言でき、実効上限の再計算
  （work-conserving な再配分）と rates の較正は**管理面だけ**が行って `computed` /
  `rates` へ書き戻す。エンジンの判定は v1 と同じ単純比較のまま。v1 しか知らない
  リーダは分上限だけを執行し続ける（additive・安全側）。
  `allocation.agents.<cli>.max_tokens` は agent CLI（＝アカウント）ごとの枠——
  ワークロードの枠とは独立の軸で、**agent-profiles（実行プロファイル自動選択）だけが
  候補の枠判定に読む**（エンジンは引き続き自ワークロードの枠しか見ない）。

## agent-control — 誰がどう読む/書くか

- **管理面（agent-dashboard / 各ツール CLI / 人）**: `$AGENT_CONTROL_DIR`（既定
  `~/.agents/control/`）の `control.json` に望ましい状態を原子書換で書く（`revision` 単調増加）。
  内容は (1) エージェント CLI / モデルの横断上書き（ワークロード既定＋各エンジンの既存語彙
  — project の purpose / flow の planner/evaluator/worker/kind / amigos のロール id — 別）、
  (2) `degraded`（node-budget soft_ratio 到達中の縮退指定）、(3) `lifecycle: run|pause|stop`、
  (4) `delegation`（flow のみ解釈: prefer local|remote / max_open_issues）。
- **各エンジン（適用側）**: 既存のチョークポイント / サイクル先頭で mtime を見て再読込し、
  優先順位 **control > CLI 引数 > 設定ファイル > 組み込み既定** で解決する（push 型 IPC なし）。
  `lifecycle` は desired state — `stop` のまま再起動されたエンジンは起動時チェックで即終了する。
  適用状況は `status/<tool>-<pid>.json` へハートビート書換（`revision_applied` / `effective` /
  `lifecycle` / `budget.soft|exceeded` / `fresh_after_sec`）し、管理面が desired との乖離を
  可視化する。未知のワークロード・未知のキーは無害に無視（repos と同じ規則）。

## 候補ベース実行（selection_policy / qualifications / execution receipt）— 誰がどう読む/書くか

実行の選択単位を「ローカル / クラウド」でなく `agent_cli + model` 候補にする 3 契約
（設計: `docs/plans/2026-08-15-agent-tools-candidate-execution-policy-dashboard-design.md`、
実装順序: `docs/plans/2026-08-15-candidate-execution-parallel-implementation-plan.md`）。
語彙と検証の 1 実装は `agentcore/executioncontract.py`、各 schema の `examples` が
エンジン側（Resolver）と制御面（Compiler）の契約テスト共通 fixture。

- **書き手は各 1 つ**（C7）: qualifications = agent-audit、`selection_policy` = Execution
  Policy Compiler、execution receipt = 実行した各 Adapter。agent-audit は control を
  直接書かず、適格性の変更は次回の Compiler 評価で反映する。
- **新 reader の候補解決順**: (1) run の Execution Envelope の明示固定 →
  (2) `selection_policy`（version 2）→ (3) 無い場合だけ既存 purpose override →
  (4) 既存 workload 単一 `agent_cli / model` → (5) agent-profiles 既定。
  `selection_policy` がある限り legacy fallback を再解釈しない。
- **dual-write（移行契約）**: 移行中の Compiler は `selection_policy` と旧 reader 向けの
  単一 fallback を併記する。rollback は v2 出力を止めて fallback へ戻す。全 Adapter が
  version 2 を申告し、legacy reader の receipt が観測されなくなるまで legacy フィールドを
  削除しない。未知の version を読んだエンジンは推測で実行せず、最後に対応できた control を
  `valid_until` まで使い、その後 park する。
- **執行の不変条件**（形はここ、執行は agentcore の Resolver）: `blocked / unknown` を
  自動選択しない・`trial` は Envelope 明示承認 run 限定・明示固定でも lifecycle / hard
  budget / scope / gate を迂回しない・適格候補ゼロは park（弱い候補へ黙って降格しない）。

## repos — 誰がどう読むか

```yaml
# repos.yaml（YAML は PyYAML 任意・無ければ JSON。トップレベルは「repo 名 → エントリ」）
app:
  url: git@example.com:team/app.git
  desc: アプリ本体（API・UI）
  base: main
  target: develop        # 省略時 base
  owns: [src/**]         # agent-project: 書込先ルーティングの根拠（無指定=参照リポジトリ）
  docs: [docs/**, README.md]   # codd-gate: 分類グロブ（他ツールは無視）
  tests: [tests/**]
shop-api:                # モノレポ: 同じ url を path 別に分ければ別エントリ（identity は url+path+base）
  url: git@example.com:team/shop.git
  path: apps/api
  base: main
  desc: API 側
```

- **agent-project**: 手書きの `<project>/repos.{yaml,yml,json}` があればそれをレジストリの正として
  読む（charter.md の `## repos` は**互換入力**。内部的にはこのスキーマの形へ正規化して引き回す）。
  手書きが無ければ **charter の `## repos` から `repos.json` を自動生成**する（`_meta.generated_from`
  マーカー付き・正は charter のまま追従。手で管理するなら `_meta` を消す）——外部ツールへは常に
  「このスキーマのファイル」として渡る。
- **agent-flow**: `--workspace` / `--reference` の値は本スキーマの**1 エントリの射影**
  （`{url, path, base, target, desc}`）。agent-project がレジストリから選んで渡す。
- **codd-gate**: `--repos <file>`（設定 `repos_file`）でこのファイルを読む（**charter は読まない＝
  agent-project から完全独立**）。`docs/tests/code/dir` は codd-gate 拡張キー（他ツールは未知キー
  として無害に無視——additionalProperties: true が互換性の要）。
- **メタデータ予約**: トップレベルの `_` 接頭辞キー（例 `_meta`）はメタデータ予約で、全消費側が
  repo エントリとして扱わずスキップする。

## task — 誰がどう読む/書くか

```json
{"id": "codd-doc-x-1a2b3c", "title": "src/util.py の変更を docs/util.md へ反映する",
 "verify": "codd-gate check --repo-dir app=. --doc docs/util.md --code src/util.py --fresh",
 "priority": 1, "paths": "docs/util.md", "expect": "changes"}
```

- **agent-project が契約の所有者**（読む側）: `enqueue --json` / `inbox/*.json` / `intake_cmd` の
  stdout がこの形式。**未知キーは保持**（前方互換）。verify 無しは inbox=人の triage 行き。
- **供給側**（codd-gate `tasks`・webhook/issue 抽出等）は**この共通スキーマへ直接出力する**
  （特定ツール向け「アダプタ」ではない——スキーマを読める消化先なら何でもよい）。自ツールの内部形式
  （codd-gate なら所見 JSON）を正とし、スキーマ外の消化先へはそこから変換する。
- **agent-flow は対象外**: agent-flow のタスクグラフノード `{id, goal, deps, kind}` は実行層内部の
  分解ステップで層が違う（agent-project → agent-flow の境界は「要求文＋workspace」であって
  task spec ではない）。

## 互換性の規則

1. **未知キーは無視せず保持する**（task）／**無害に無視する**（repos）。キーの削除・意味変更は不可、
   追加のみ可（additive evolution）。
2. スキーマを変えるときは本ディレクトリを先に更新し、各ツールの正典
   （`backlog.md.example` / `charter.md.example` / 各設計書）から参照を張る。
3. 検証は各ツールの stdlib パーサが行う（jsonschema への実行時依存は持たない。スキーマファイルは
   契約の文書化とテストでの突き合わせに使う）。
