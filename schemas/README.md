# schemas/ — ツール横断の共通スキーマ（repos / task / node-budget / agent-control / agent-instructions / agent-session-commands / mission / amigos-command / delivery / delegation / board）

agent-project・agent-flow・codd-gate・agent-amigos が**データ契約だけで**結合するための独立スキーマ。
ツール同士は互いの実装を知らず、ここで定義する形式だけを読む/書く（結合は常に一方向×データ）。

| スキーマ | 何の契約か | 所有者（変更の主導） |
|----------|-----------|--------------------|
| [`repos.schema.json`](repos.schema.json) | リポジトリレジストリ（identity = **(url, path, base)**＝パス＋ブランチで一意） | 共有（本ディレクトリが正典） |
| [`task.schema.json`](task.schema.json) | 制御層タスク（バックログ 1 件）の JSON 表現 | kiro-projects（Markdown 形の正典は `tools/kiro-projects/backlog.md.example`） |
| [`node-budget.schema.json`](node-budget.schema.json) | ノード単位の予算 v2 — トークン一次（実行時間上限は v1 互換で AND）＋配分宣言（`$AGENT_BUDGET_DIR`＝既定 `~/.agents/budget/` の config.json ＋ ledger/<YYYYMMDD>.jsonl） | 共有（本ディレクトリが正典。初出は agent-amigos 設計書 §3.3、v2 は `docs/plans/2026-07-19-agent-dashboard-orchestration-token-budget-design.md`） |
| [`agent-control.schema.json`](agent-control.schema.json) | 管理面→各エンジンの宣言的オーケストレーション（`$AGENT_CONTROL_DIR`＝既定 `~/.agents/control/` の control.json ＋ status/<tool>-<pid>.json）。エージェント CLI / モデルの横断上書き・縮退・一時停止 / 停止・委譲誘導。優先順位は control > CLI 引数 > 設定ファイル > 組み込み既定 | 共有（本ディレクトリが正典。設計は `docs/plans/2026-07-19-agent-dashboard-orchestration-token-budget-design.md`） |
| [`agent-instructions.schema.json`](agent-instructions.schema.json) | 管理面→各エンジンのノード共通指示（`$AGENT_INSTRUCTIONS_DIR`＝既定 `~/.agents/instructions/` の instructions.json）。指示文・推奨スキル（名前参照）・ツール方針を各エンジンが決定的に描画して実行エージェントのプロンプトへ前置。agent-flow は run の meta.json スナップショットで委譲先ノードへ伝播。適用状況は agent-control status の `instructions_revision_applied` に相乗り。最弱の層（タスク > brief > charter/rules > 共通指示） | 共有（本ディレクトリが正典。設計は `docs/plans/2026-07-19-agent-dashboard-global-instructions-design.md`） |
| [`agent-session-commands.schema.json`](agent-session-commands.schema.json) | 管理面→各エンジンのセッション開始コマンド（`$AGENT_SESSION_DIR`＝既定 `~/.agents/session/` の session.json）。セッションが始まった直後に配列順で 1 回だけ実行する前準備。`process` はホストのシェルで実行して完了を待ち、`chat` はセッションへ最初のプロンプトとして送る（単発系にはセッションが無いのでスキップ）。`when` で engines / workloads / agent_cli を絞れる。適用状況は agent-control status の `session_commands_revision_applied` に相乗り。**agent-instructions と違い委譲先ノードへ伝播しない** — 副作用のあるコマンドの到達範囲を各ノードのローカル設定へ閉じ込める | 共有（本ディレクトリが正典。設計は `docs/plans/2026-07-20-agent-dashboard-session-commands-design.md`） |
| [`mission.schema.json`](mission.schema.json) | 協働ミッションの公示（agent-amigos の `post --roles` に渡すミッション + 役割ミッション表）。バスへ書かれる読取契約（外部ビュアーが読む `mission.json` / `MANIFEST.json` / `final.json` / `cancelled.json`）は `$defs` に文書化 | agent-amigos（検証は stdlib パーサ `normalize_mission`。スキーマは文書化とテスト突き合わせ — enum/既定値の一致をテストで担保） |
| [`amigos-command.schema.json`](amigos-command.schema.json) | agent-amigos への指示ドロップ（`<home>/.agents/agent-amigos/commands/*.json` — post / claim / assign / accept / reject / cancel / say）。投函側は人・agent-dashboard、取り込み側は常駐デーモン | agent-amigos（取り込みの正典は `agent_amigos/commands.py`。コマンド一覧の一致を両側のテストで担保 — Python `CommandSchemaTests` / dashboard `amigos.test.js`） |
| [`delivery.schema.json`](delivery.schema.json) | agent-amigos の納品書（accept 時にオーナーホームの `deliveries/<mid>/delivery.json` へ書かれる受領記録）。バスの `MANIFEST.json` が integrator の組み立て記録（gc 対象）なのに対し、こちらは受入という事実と搬出先の永続記録 | agent-amigos（書き手は owner デーモン。読み手は agent-dashboard の納品一覧と `agent-amigos deliveries`） |
| [`delegation.schema.json`](delegation.schema.json) | agent-dashboard から agent-flow / agent-amigos への委譲をエンジン非依存に扱う封筒（post / award / accept / reject / cancel）と正規化ビュー（`$defs.delegation_view` — 公示→入札→落札→受入の観測）。バス・claim プロトコルは統一せず、dashboard のエンジン別アダプタがネイティブ形式（amigos-command / flow inbox）へ決定的に変換する。共通 id を両エンジンの native id に採用（対応表なし）。additive: `requires`（入札資格 tags/agent_cli/repos）・`speculation`（投機同時実行）は委譲公示板（agent-board）だけが解釈する。dashboard 側実装済み（`tools/agent-dashboard/src/features/delegation/`） | 共有（本ディレクトリが正典。設計は `docs/plans/2026-07-19-delegation-contract-design.md`。契約一致は dashboard `test/delegation.test.js` で `amigos-command` enum と突き合わせ） |
| [`board.schema.json`](board.schema.json) | 委譲公示板（agent-board）のバス契約 — 専用リポジトリ（またはローカル dir）に置く板のファイルレイアウト（`nodes/<id>` 登録・`delegations/<id>/{post,bids,award,status,results,result,cancelled}`）。公示本体は `delegation.schema.json` の op=post 封筒そのまま、入札は agent-flow / agent-amigos と同一仕様の名前空間付き claim ＋ `(ts, who)` タイブレーク（同じ仕様・別実装）。真実は板のファイル・中央（forge/hub）は転送のみ。成果物リポジトリでノードを選別（node.repos × workspace.url を identity 照合） | agent-board（`tools/agent-board/`。検証は stdlib パーサ。契約一致は `tests/test_agent_board.py` で `amigos-command` enum と突き合わせ・dashboard `test/delegation-board.test.js`） |

## node-budget — 誰がどう読む/書くか

- **各ツール（記帳・抑制側）**: 1 回の agent CLI 実行ごとに ledger へ 1 行追記
  （O_APPEND・追記専用）し、実行前に「合計消費 ≥ 上限」なら新規実行を控える。
  workload は `routine`（kiro-loop / agent-loop 定常業務）/ `project`（agent-project）/
  `flow`（agent-flow）/ `amigos`（agent-amigos）。**全ワークロード実装済み**:
  - `amigos`: ターン前チェック → 超過中は amigo を paused にし owner へ通知。ターンの
    CLI 実行秒を記帳。
  - `flow` / `project`: LLM 単一チョークポイント（`run_agent` / `_run_agent_cli`）で
    実行前チェック → 超過は `[agent-error:quota] [node-budget]` として既存の環境要因
    フローに乗る（run 即終端・リトライを焼かない／裁定を呼ばず needs へ）。成功実行の
    実測秒を記帳。
  - `routine`（kiro-loop）: スケジューラがサイクル先頭でチェックし、超過中は定期送信・
    webhook キューの dispatch を停止（10 分ごとに警告ログ・キューは保持）。実行秒は
    **セマフォスロットの保持時間**（送信 → 完了検知）で近似して解放時に記帳する
    （`max_concurrent <= 0` でセマフォ未使用のときは計測点が無く記帳されない、が既知の制約。
    タイムアウト強制解放は実行時間として数えない）。`agent-loop`（未統合クローン）へは
    次回のクローン同期で反映する。
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

## repos — 誰がどう読むか

```yaml
# repos.yaml（YAML は PyYAML 任意・無ければ JSON。トップレベルは「repo 名 → エントリ」）
app:
  url: git@example.com:team/app.git
  desc: アプリ本体（API・UI）
  base: main
  target: develop        # 省略時 base
  owns: [src/**]         # kiro-projects: 書込先ルーティングの根拠（無指定=参照リポジトリ）
  docs: [docs/**, README.md]   # codd-gate: 分類グロブ（他ツールは無視）
  tests: [tests/**]
shop-api:                # モノレポ: 同じ url を path 別に分ければ別エントリ（identity は url+path+base）
  url: git@example.com:team/shop.git
  path: apps/api
  base: main
  desc: API 側
```

- **kiro-projects**: 手書きの `<project>/repos.{yaml,yml,json}` があればそれをレジストリの正として
  読む（charter.md の `## repos` は**互換入力**。内部的にはこのスキーマの形へ正規化して引き回す）。
  手書きが無ければ **charter の `## repos` から `repos.json` を自動生成**する（`_meta.generated_from`
  マーカー付き・正は charter のまま追従。手で管理するなら `_meta` を消す）——外部ツールへは常に
  「このスキーマのファイル」として渡る。
- **agent-flow**: `--workspace` / `--reference` の値は本スキーマの**1 エントリの射影**
  （`{url, path, base, target, desc}`）。kiro-projects がレジストリから選んで渡す。
- **codd-gate**: `--repos <file>`（設定 `repos_file`）でこのファイルを読む（**charter は読まない＝
  kiro-projects から完全独立**）。`docs/tests/code/dir` は codd-gate 拡張キー（他ツールは未知キー
  として無害に無視——additionalProperties: true が互換性の要）。
- **メタデータ予約**: トップレベルの `_` 接頭辞キー（例 `_meta`）はメタデータ予約で、全消費側が
  repo エントリとして扱わずスキップする。

## task — 誰がどう読む/書くか

```json
{"id": "codd-doc-x-1a2b3c", "title": "src/util.py の変更を docs/util.md へ反映する",
 "verify": "codd-gate check --repo-dir app=. --doc docs/util.md --code src/util.py --fresh",
 "priority": 1, "paths": "docs/util.md", "expect": "changes"}
```

- **kiro-projects が契約の所有者**（読む側）: `enqueue --json` / `inbox/*.json` / `intake_cmd` の
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
