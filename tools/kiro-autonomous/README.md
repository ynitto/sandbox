# kiro-autonomous

**Loop Engineering MVP** — `backlog/`（案件毎ファイル）を優先順位付けし、最優先タスクを kiro-flow に
実行させ、**`verify` をローカルで実行して PASS したものだけ done に確定**（archive/ へ退避）、NG なら
積み直す。backlog が尽きるか予算が尽きるまで繰り返し、人の判断が要った分は案件毎の
`needs/<id>.md`（フィードバック欄つき）で差し出し、判断は `decisions/<id>.md` に残す。

> タスク書式（backlog/<id>.md）の規約は [`backlog.md.example`](backlog.md.example)、
> 設計は [`docs/designs/2026-06-16-kiro-autonomous-mvp-design.md`](../../docs/designs/2026-06-16-kiro-autonomous-mvp-design.md)。
> `kiro-` 接頭辞は実行を kiro-flow＝kiro-cli に委譲するため。
>
> 🚦 **熟練度別の設定・動かし方は [`GUIDE.md`](GUIDE.md)**（L0 下見 → L1 試運転 → L2 日常運用 → L3 無人運用 → L4 スケール）。

## 正準ループ（5点）

1. `backlog/<id>.md` を読み優先順位をつけ、最優先を kiro-flow に投げる。
2. 優先順位付けは `--planner kiro`（エージェントが外部 `priority` も加味）/ `none`（priority 降順→最古）。人間は `policy.md` で上書きできる。
3. kiro-flow の結果を verify ゲートで検証。done は `archive/` へ退避、NG なら積み直す。
4. backlog が尽きるか予算が尽きるまで繰り返す（`--watch` なら尽きても監視を続ける）。
5. ユーザーの判断・フィードバックは案件毎 `decisions/<id>.md` に保存する。

## 二層構成

| 層 | 担当 | 実体 |
|----|------|------|
| 外側（制御） | 優先順位付け / 検証ゲート / 積み直し / 収束 / 決定記録 | `kiro-autonomous` |
| 内側（実行） | タスクの分解 → act → 内側 verify ループ | `kiro-flow run` |

done を**自己申告で確定させない**（verify の終了コード0のみが根拠）ことが MVP の存在意義。

## プロジェクト層（`project`）— charter 駆動の長期改善ループ

backlog（タスク）の上に、**人が書く目標**から逆算して回す**もう一段のループ**を載せる
（設計: [project-loop 設計メモ](../../docs/designs/2026-06-21-kiro-autonomous-project-loop-design.md)）。
backlog を消化して `drained` で止まる正準ループに対し、`project` は「**枯渇**」と「**目標達成**」を分離し、
未達なら改善タスクを生成して長期に回す。

```
人が書く charter.md（goal / constraints / assumptions / deliverables / acceptance=受入 verify）
   │
   ├ ① plan     charter をエージェントに分解させ enqueue（冪等。verify 必須）
   ├ ② execute  既存の正準ループ run を drained まで回す（検収/回帰/protect/予算は全て温存）
   └ ③ evaluate acceptance 全 PASS かを判定（＋opt-in 敵対的レビュー）
         未達/指摘 → 改善タスクを生成して次サイクル（長期改善）
         全 PASS かつ改善ゼロ → milestone gate で人へ（needs/<project>.md）
```

- **done の唯一の根拠は `acceptance`（=verify）全 PASS**。タスクの verify と同じ鉄則（履歴でなく最終状態/差分）。
- **必ず有限停止**: 内側 `run`（drained/budget）＋プロジェクト層（`--max-project-cycles` 既定 5 /
  `--max-project-cost` / `--project-stall`＝PASS 数が増えない連続回数で人へ）。
- **知能は委譲**: plan の分解・evaluate の敵対的レビューはエージェントへ、enqueue・acceptance 実行・収束判定は
  本体が決定的に行う（stdlib のみ）。`project` を呼ばない限り従来挙動は完全不変。

### ワーカーへの定義/判断の伝播（charter + decisions の注入）

kiro-flow へ委譲する act 依頼（`build_request`）に、**プロジェクト定義（charter）**と**過去の判断結果
（`decisions/<id>.md`）**を文脈として注入する。これにより kiro-flow で動くワーカーが、個票だけでなく
**プロジェクトの目標・制約・前提・成果物と、人の過去の承認/差し戻し/learn を踏まえて**働く。

- **`project` か通常 `run` かを問わない**: `charter.md` が存在すれば全 act に定義が乗る（無ければ従来どおり空）。
  判断記録もタスクに `decisions/<id>.md` があれば project/backlog に関わらず注入される。
- **有界**: charter は目標/制約優先で要約（既定 1400 字）、decisions は末尾＝直近優先（既定 1000 字）。
- 注入は**依頼文字列の組み立てだけ**で、本体の不変条件（done は verify のみ・決定的）には触れない。

### 横展開リンク（charter.md の `## links`）

charter.md に `## links` を書くと、**他プロジェクトの定義（charter）と判断（decisions の learn）**を act
ワーカーの文脈に取り込める（横断 recall）。共通規約・認証作法などを別プロジェクトへ活かす。

```markdown
## links
- shared-conventions      # <root>/projects/shared-conventions を参照
- ../infra-rules          # '/' や '..' を含めば相対パス
```

- リンク先の goal/constraints（定義）は `charter_context` が、`- learn:`（人の判断）は `linked_learnings_context`
  が、それぞれ有界に注入する。1 階層・自己/重複は無視。リンクが無ければ従来どおり空（疎結合）。
- ltm-use（実績で昇格した学習の横断記憶）が「自動」なのに対し、charter リンクは**人が明示した参照先**を確実に引く。

```bash
mkdir -p .kiro-autonomous/projects/default
cp tools/kiro-autonomous/charter.md.example .kiro-autonomous/projects/default/charter.md   # 目標を書く（正典はこのテンプレ）
kiro-autonomous project                       # plan→execute→evaluate を回す（収束で人へ）
kiro-autonomous project --watch               # 収束/人待ちでも常駐し charter 更新を待つ
kiro-autonomous project --review-project      # acceptance 全 PASS でも敵対的レビューで短絡的達成を疑う
kiro-autonomous needs                         # milestone（収束候補）を確認
kiro-autonomous approve <project> --reason "受領"   # 収束候補を完了確定（最終納品書）／続行は charter を更新して再実行
```

- **charter の書式**は [`charter.md.example`](charter.md.example)（このテンプレが正典）。`# Charter: <name>` の
  name から project id を生成（ASCII 推奨。日本語のみだと既定 `project`）。`acceptance` を持たない charter は
  done 判定不能＝必ず人へ回る。
- 状態は `<root>/project.json`（サイクル・PASS 履歴・stall・cost）、各評価は `decisions/` に `project-evaluate`
  として監査記録。終了コードは `0`＝完了受領 / `1`＝人の対応待ち（収束候補・停滞・内側エスカレーション）/
  `2`＝予算停止。

## 依存

- `python3`（標準ライブラリのみ。pip 依存なし）
- `kiro-flow`（act の委譲先。PATH か `tools/kiro-flow/kiro-flow.py` を自動解決。`--dry-run` なら不要）
- `kiro-cli`（`--planner kiro`＝既定の優先順位付け／実行 executor=kiro 用。`--planner none` なら順位付けには不要）

## インストール

```bash
bash tools/kiro-autonomous/install.sh           # ~/.local/bin/kiro-autonomous
```

未インストールでも `python3 tools/kiro-autonomous/kiro-autonomous.py ...` で代用可。

## 設定ファイル（任意・kiro-flow と同じ流儀）

毎回フラグを並べる代わりに、環境ごと・常駐ごとに決まる値を設定ファイルに書ける。**優先順位は
`CLI > 設定ファイル > 組み込み既定`**。サンプルは [`kiro-autonomous.yaml.example`](kiro-autonomous.yaml.example)。

```bash
cp tools/kiro-autonomous/kiro-autonomous.yaml.example .kiro/kiro-autonomous.yaml   # 編集して使う
kiro-autonomous run                       # 設定を読み込んで起動
kiro-autonomous run --executor stub       # その場限りの上書きだけ CLI で
kiro-autonomous run --config ./my.yaml    # 明示パス指定も可
```

- **検索順序**: `--config` 明示 → `./.kiro/kiro-autonomous.{yaml,yml,json}` → `~/.kiro/…`（kiro-flow と同じ `.kiro`）。
- **形式**: YAML（**PyYAML 必要**）または JSON（標準ライブラリのみ。キーは同じ）。PyYAML 非導入の環境で
  `.yaml` を指定するとエラーになるので、その場合は `kiro-autonomous.json` を使う。
- **書けるキー**: `executor` / `planner` / `flow_planner` / `location` / `model` / `root` / `workdir` /
  `poll` / `concurrency` / `level` / `throttle` / `debounce` / `pace` / `max_cycles` / `max_seconds` / `max_tokens` / `max_cost` /
  `max_retries` / `max_iterations` /
  `verify_timeout` / `verify_confirm` / `act_timeout` / `git_bus` / `git_branch` / `git_subdir` / `kiro_flow` /
  `notify_cmd` / `actor` / `learn_threshold` / `promote_threshold` / `ltm_home` / `rot_age_days` /
  `max_spawn` / `regression_cmd` / `auto_level_max` / `level_promote_after` / `level_window` / `level_rework_max` /
  `max_project_cycles` / `max_project_cost` / `project_stall`（project 用）。
- **真偽フラグも書ける**: `watch` / `once` / `dry_run` / `rot` / `ltm` / `regression_revert` / `require_progress` / `auto_level` / `review_project`（既定 false）・
  `do_archive` / `learn` / `cleanup`（既定 true）・`auto_adjudicate`（既定 true）。CLI の `--flag`/`--no-flag`
  が常に勝つ（例: config で `watch: true` にしつつ、その場だけ `--no-watch`）。退避可否は `--archive` が
  パス用のため config キーは `do_archive`。
- **書けないもの**: 個別パス上書き（`--backlog` 等）と実行限定フラグ（`--json` / `--fix` / `--pin`/`--defer`）は CLI 専用。

常駐運用では systemd の `ExecStart` を `kiro-autonomous` だけにして、調整はこのファイルで完結できる。

## ファイル/ディレクトリ構成（プロジェクト > バックログ）

**プロジェクトが最上位コンテナ**で、`cwd の ./.kiro-autonomous/projects/<name>/` 配下に 1 プロジェクト＝1 セットが
集約される（`--root` でコンテナを、`--project` でプロジェクトを選ぶ。各パスは `--backlog` 等で個別上書きも可）。
**複数プロジェクトを併存**でき、needs/decisions も per-project に閉じる。

```
.kiro-autonomous/                  ← コンテナ（--root）。projects/ を束ねるだけ
  projects/
    default/                       ← 1 プロジェクト（--project。未指定はこれを作成）
      charter.md           プロジェクト憲章（人が書く・project の最上位入力。正典 charter.md.example）
      project.json         project のサイクル状態（PASS 履歴・stall・cost。project が増分更新）
      backlog/<id>.md      タスク本体（案件毎・人が追加できる。done で archive/ へ退避）
      claims/<id>.lock     実行権の原子的クレーム（二重実行防止。doing 中だけ存在し終了で解放）
      inbox/               取り込み待ちのドロップ口（外部ソースが .json/.md を置く→run/watch が backlog 化）
      archive/<id>.md      完了タスクの保全先（done で backlog から移動。検収用「納品書」付き）
      policy.md            優先順位・実行先の上書き（人だけが書く）
      needs/<id>.md        判断待ちの通知＋フィードバック記入欄（人が記入→自動再開）
      decisions/<id>.md    人の判断・承認・フィードバックの決定記録（learn＝学習材料。append-only）
                           └ --ltm 時、実績ある learn は ltm-use home へ昇格（横断再利用）
      DELIVERY.md          納品一覧（受領書）。done を1行ずつ追記
      run-log.jsonl        構造化 run-log（run 毎に1行 JSON。reason/done/escalations/tokens/cost/duration）
      journal.md           機械のサイクルログ（人間可読）
      bus/                 kiro-flow バス（一時。run 後に自動クリーンアップ。--no-cleanup で保持）
    payments-api/          ← もう 1 つのプロジェクト（同じ一式・併存可）
      charter.md  …
```

稼働インスタンスのレジストリ（`~/.kiro-autonomous/instances/`）は**グローバル**で、各プロジェクト root を
監視先として登録する＝`instances` で複数プロジェクト・複数ホストを横断発見できる。

```bash
kiro-autonomous enqueue --title "…" --verify '…'                 # default プロジェクトへ（無ければ作成）
kiro-autonomous enqueue --project payments-api --title "…" --verify '…'  # 別プロジェクトへ
kiro-autonomous project --project payments-api                  # そのプロジェクトの charter ループ
kiro-autonomous needs   --project payments-api                  # per-project の判断待ち
kiro-autonomous start   --project payments-api                  # そのプロジェクトを常駐監視
```

## kiro-flow への委譲（`--location` で local / daemon / remote）

「どこで・どう動かすか」は `--location`（既定 `auto`）に集約：

| location | 委譲方法 | daemon | 用途 |
|----------|---------|--------|------|
| `local` | `kiro-flow run`（単発・同期） | 不要 | 既定の実体 |
| `daemon` | `kiro-flow submit` → `result` で done 待ち | ローカル daemon（無ければ local にフォールバック） | warm worker 再利用 |
| `remote` | `submit`（`--git`）→ `result` で done 待ち | 共有 git バスの remote daemon が必須 | 別マシンへオフロード |

`auto` は「offload 一致＋`--git-bus` → remote ／ ローカル daemon 稼働 → daemon ／ 他 → local」。
daemon 検知は kiro-flow と同じロック（`flock`）。逐次処理では **local（run）で十分＝daemon 不要**。

```bash
# 既定（local: 単発 run）
kiro-autonomous run --executor kiro

# warm worker を再利用したいなら daemon を立てて submit 経路に
kiro-flow --bus .kiro-autonomous-bus daemon &
kiro-autonomous run --location daemon --executor kiro
```

### 並列消費（`--concurrency`：kiro-flow の worker 並列へ寄せる）

依存（`after`）が解決済みのタスクは互いに独立なので、`--concurrency N`（既定 1）で**先頭から最大 N 件を
daemon/remote へ並行 submit** し、kiro-flow の worker 並列に実行させる。**実行の重い部分だけを並列化し、
verify と done/archive/decisions/派生生成といったローカル状態の変更は逐次のまま**にして、workdir や決定記録の
競合を避ける（不変条件をそのまま維持）。`local`（単発 run）実行は逐次のまま＝並列化しない（daemon を立てて
submit 経路にしたときだけ効く）。隔離は kiro-flow の worker に委ねる前提。

```bash
# ローカル daemon を立て、独立タスクを最大3並行で消化
kiro-flow --bus .kiro-autonomous-bus daemon --workers 3 &
kiro-autonomous run --location daemon --concurrency 3 --executor kiro
```

- 1サイクル=1タスクの計上は不変（`max_cycles`/予算はそのまま効く。バッチ幅は残サイクル予算も超えない）。
- `--once` のときは並行せず1件だけ。`--concurrency 1`（既定）は従来どおり完全な逐次。
- **二重実行防止（原子的クレーム）**: 各タスクは実行前に `claims/<id>.lock` を `O_CREAT\|O_EXCL` で原子的に
  確保した worker/インスタンスだけが回す。**同じ backlog を複数プロセス（や複数ホスト）で同時に回しても
  同一タスクが二度実行されない**。ロック取得後は disk を再検証し、別インスタンスが既に消化済み（archive/状態変更）
  なら実行しない。owner 失踪時は TTL（act+verify を上回る猶予）超で奪取。終了時に解放（クラッシュ時の残骸も再利用可）。

### 自律度の段階導入（`--level`：report → assisted → unattended）

Loop Engineering の「**L1 report → L2 assisted → L3 unattended** を一足飛びにしない」段階導入を一級化。
新しい backlog やパターンを本番に載せるとき、いきなり自動 done させずに信頼を積み上げる。

| level | act | done | 用途 |
|-------|-----|------|------|
| `report` | **しない** | — | 「何を・どの順で回すか」だけ報告（消化しない）。week 1 の様子見・計画確認 |
| `assisted` | する | **人が承認**（全件 review） | 実行はするが done は必ず人が `approve`。検証つき小修正の段階 |
| `unattended`（既定） | する | 自動（既存ゲートに従う） | 現行。protect/gate/regression を通れば自動 done |

```bash
kiro-autonomous run --level report      # 計画だけ出す（act しない）
kiro-autonomous run --level assisted    # 実行するが done は approve 待ち（全件 review）
kiro-autonomous run                     # 既定 unattended（従来どおり）
```

- `report` は正常終了（exit 0）で計画一覧を出すだけ＝既存 backlog を一切変えない安全な下見。
- `assisted` は verify=PASS でも `review` に落とす（`approve` で done／フィードバックで差し戻し）。`protect`(後述) や
  `review: human` の上位ゲートとも自然に重なる。`unattended` は既定なので**従来挙動は不変**。
- いま無人運用に値するかは `audit`（後述）が L0–L3 で採点する。`--level` はその「実際に動かす自律度」を選ぶ側。

### タスク単位の自律レベルと実績連動の自動昇格（`- level:` / `- track:` / `--auto-level`）

実運用では自律度は backlog 毎に違う（決済コードは承認必須、typo 修正は無人で良い）。`--level` は run 全体の
既定だが、**タスク毎に上書き**でき、さらに**実績で自動調整**できる。設計詳細は
[per-task autonomy 設計メモ](../../docs/designs/2026-06-21-kiro-autonomous-per-task-autonomy-design.md)。

- **`- level: report|assisted|unattended`**（タスク行）: そのタスクの自律度をグローバルより優先（**上書き**）。
  実効 = `- level:`（明示）＞ track の自動昇格 ＞ グローバル `--level`。`protect`/`gate`/`regression` は常に上乗せ。
  ```text
  ## PAY-12: 決済ロジック変更
  - level: assisted        # この案件だけ done は人が承認
  ## DOC-3: README の typo
  - level: unattended      # 同じ backlog でも雑魚は自動 done
  ```
  `report` のタスクは実行せず「計画」に保留（塩漬け）。グローバル `report` でも明示 `unattended` は実行される。

- **`--auto-level`（opt-in）＋ `- track: <名前>`**: 同種タスク群の**手戻り率**で level を自動で上げ下げ。
  直近 `level_window`（既定 10）件で手戻りが無く連続 clean が `level_promote_after`（既定 5）に達したら 1 段昇格、
  手戻り（差し戻し/回帰/偽done）で 1 段降格・**2 回で `assisted` にピンして自動管理を停止**。**ceiling は既定
  `assisted`**（`--auto-level-max unattended` を明示したときだけ完全無人化へ到達）。track 毎の状態は
  `<root>/autonomy/<track>.json`、昇降格は `decisions/` に監査記録。
  ```bash
  kiro-autonomous run --level assisted --auto-level --auto-level-max unattended
  #   docs-typo の様な低リスク track は実績が貯まると自動で unattended に昇格、
  #   手戻りが出れば assisted に自動で引き戻す（信頼は得るだけでなく失う）
  ```
  既定（`--auto-level` off・`- level:`/`- track:` 無し）では**従来挙動は完全不変**。

## サブコマンド

| コマンド | 役割 |
|----------|------|
| （省略） | **`run --watch` と同義**。常駐監視で起動し backlog 投入を待ち続ける（PC 起動時の常駐用） |
| `run` [`--watch`] | 正準ループ。`--watch` で終了条件後も常駐監視（idle はエージェント非起動） |
| `project` [`--watch`] | **charter 駆動の長期改善ループ**（下記）。目標→分解→消化→評価→改善を回す |
| `triage` | 優先順位付けのみ（inbox→ready 昇格・policy 適用）。順位を表示 |
| `needs` | 人の判断待ち（blocked / acceptance 未定義 / 検収待ち）を表示 |
| `enqueue` [`--title` `--verify` …\| `--json`] | 汎用の取り込み口。CLI/stdin/JSON から backlog タスクを作る |
| `stats` [`--json`] | ループの計測値（スループット・自動化率・retry・人対応待ち） |
| `audit` [`--json` `--strict`] | Loop Readiness を採点（L0–L3・スコア・赤旗・提案）。`--strict` で CI ゲート化 |
| `runlog` [`--json` `--tail N`] | 構造化 run-log（run-log.jsonl）の末尾を表示（運用判断の土台） |
| `rot` [`--fix`] | 古い/重複/実行不能タスクを検出して報告（`--fix` で人の判断へ回す） |
| `approve <id> --reason …` | 判断待ちを修正承認して積み直し（決定記録） |
| `hold <id> --reason …` | `policy.md` に `deny` 追加し保留（決定記録） |
| `reprioritize <id> --pin\|--defer --reason …` | `policy.md` に `pin`/`defer` 追加（決定記録） |
| `instances` [`--json` `--registry`] | 稼働中の kiro-autonomous を一覧（共有レジストリで別ホストも横断） |
| `start` [`--root` `--config` `--force` `--registry`] | `run --watch` を切り離して常駐起動（detached。重複監視は拒否） |
| `stop` [`--root` \| `--pid` \| `--all`] | 稼働インスタンスを停止（SIGTERM→必要なら SIGKILL・登録掃除） |
| `restart` [`--root` `--config`] | 同じ root の監視を停止してから起動し直す |

### 稼働インスタンスの発見（外部操作者向け）

`run`（特に `--watch`）の間、監視中のルートと OS/WSL 情報を共通 home
（`$KIRO_AUTONOMOUS_HOME` → `~/.kiro-autonomous`）の `instances/<pid>.json` に登録する。外部のツール
（例: kiro-autonomous スキル）はこれを読んで「いまどのフォルダを見ているか」を発見し、同じ `backlog/`・
`needs/` 等へ読み書きできる。死んだ PID のレコードは一覧時に自動で掃除される。

```bash
kiro-autonomous instances           # 人が読む一覧（pid・runtime・root、WSL なら Windows パスも）
kiro-autonomous instances --json    # 機械処理用（root/backlog/needs/archive… の絶対パスと runtime/wsl_distro）
```

WSL で稼働中の場合、レコードには `runtime: "wsl"`・`wsl_distro` と、可能なら `wslpath -w` で得た
`root_windows`（`\\wsl.localhost\<distro>\…`）も含まれる。プロセスは WSL・操作側は Windows という構成で
パスを橋渡しできる。

#### 別ホストの発見（共有レジストリ）

複数マシンの稼働インスタンスを横断発見したいときは、**共有レジストリ**（NFS / 同期フォルダ / git バスの
チェックアウト等、複数ホストから見える1ディレクトリ）を指す。各ホストはそこへも自分のレコードを書き、
`instances` はローカル home＋共有先を横断して一覧する。**core は決定的なファイル操作のみで、ネットワークは
共有先の仕組み（NFS/同期/git）が担う**＝「標準ライブラリのみ・ネットワーク非依存」の不変条件を保つ。

```bash
# 各ホストで（共有先にも自分を登録。env KIRO_AUTONOMOUS_REGISTRY でも可）
kiro-autonomous start --registry /mnt/shared/kiro-registry
# どのホストからでも横断一覧（別ホストは @host(remote) 印つき）
kiro-autonomous instances --registry /mnt/shared/kiro-registry
```

- 生死判定はホスト別: **自ホストは PID**、**別ホストは heartbeat の鮮度**（`ttl`、既定 90s 以上・`poll` の3倍）。
  watch は各パス/idle で heartbeat を更新する。鮮度切れの別ホストは一覧から消える（長期間死んだものは掃除）。
- `stop`/`restart` は**自ホストのみ**を対象にする（別ホストの PID へシグナルは送れない＝そのホストで停止する）。
- レコードファイルは衝突回避のため `instances/<host>-<pid>.json`。

### 取り込み口の多様化（enqueue / inbox）

backlog へタスクを入れる経路を一級化した。**コアは標準ライブラリのみ・ネットワーク非依存**を保ち、外部
ソース（webhook / メール / GitHub issue 抽出 …）は**薄いアダプタで取り込み口へ流し込む**設計。

```bash
# CLI から1件（verify が無いと inbox=人の triage 行き）
kiro-autonomous enqueue --title "レポート生成を直す" --verify 'pytest -q tests/report'

# stdin/JSON（1件 or 配列）。外部ソースのアダプタはここへパイプするだけ
echo '{"title":"X","verify":"make test","priority":5,"after":"T1"}' | kiro-autonomous enqueue --json
gh issue list --label kiro --json title | adapter.sh | kiro-autonomous enqueue --json

# ドロップ口: <root>/inbox/ に .json（obj/配列）や .md（タスク形式）を置くと、run/watch が取り込む
cp task.md .kiro-autonomous/inbox/
```

- `--json` の各オブジェクトのキー: `title`(必須) / `verify` / `priority` / `source` / `status` /
  `after` / `review` / `note` / `id`（未知キーも保持）。`status` 未指定なら **verify 有→`ready` / 無→`inbox`**。
- `inbox/` のファイルは取り込むと消える。watch は inbox に何か置かれると起きる（idle のまま放置しない）。
- いずれも **verify を持たないタスクは `inbox`** に入り、triage で人へ回る（done は verify でしか確定しない鉄則）。

### 常駐の起動・停止・再起動（lifecycle）

レジストリ（上記）の上に、常駐プロセスの**起動/停止/再起動**を一級コマンドにしている。スキルや人が
「いま動かす／止める」を明示操作できる。

```bash
kiro-autonomous start --root /work          # run --watch を detached 起動（重複監視は拒否。--force で許可）
kiro-autonomous start --config ./my.yaml    # 設定はファイルに寄せる（CLI 個別フラグは start では渡さない）
kiro-autonomous stop  --root /work          # SIGTERM（→ 居残りは SIGKILL）。登録も掃除
kiro-autonomous stop  --pid 12345           # PID 指定（instances で確認）／ --all で全停止
kiro-autonomous restart --root /work        # 同じ root を止めてから起動し直す
```

- **`start`** は子を `start_new_session` で切り離し、ログを `~/.kiro-autonomous/logs/<root>.log` に流す。
  起動後にレジストリ出現を確認して pid を報告する。実行時設定は**設定ファイル**（`--config` か `.kiro/`）に
  寄せる思想（§設定ファイル）— `start` は個別の run フラグを取らない。
- **`stop`** は graceful（daemon 側は SIGTERM を受けて後始末＝registry を消して終了）。居残りだけ
  `SIGKILL`（POSIX）。自分自身は決して止めない安全ガード付き。
- `--root` は**作業ルートでもその配下の `.kiro-autonomous` でも**一致する。Windows ネイティブでは
  SIGTERM の挙動が限定的（`stop` はベストエフォート）。

## クイックスタート

```bash
# enqueue で default プロジェクトへ積む（<root>/projects/default/backlog に作られる。無ければ作成）
kiro-autonomous enqueue --title "README に概要見出しを追加" --verify 'grep -q "## 概要" README.md'
kiro-autonomous run --executor kiro                         # 自律消化（default プロジェクトを消化）

# 別プロジェクトへ積む / そのプロジェクトを回す
kiro-autonomous enqueue --project payments --title "…" --verify '…'
kiro-autonomous run --project payments --executor kiro

# 常駐: 新規タスク/フィードバックを監視して自動消化（idle 中はエージェントを起動しない）
kiro-autonomous run --watch --poll 10 --executor kiro

# 優先度＋古さで決定的に（kiro-cli 不要）。kiro-flow も stub に
kiro-autonomous run --planner none --flow-planner stub --executor stub
```

`backlog/<id>.md` に `- priority: N`（大きいほど高優先）を書くと外部から順序を制御できる。
`--planner none` は priority 降順→同値は最古、`--planner kiro`（既定）はエージェントが priority も加味する。

## 常駐起動（PC 起動時から待ち受ける）

サブコマンドを**省略して呼ぶと `run --watch` と同義**になり、常駐監視で起動して backlog 投入を待ち続ける。
PC 起動時に立ち上げっぱなしにしておき、`backlog/<id>.md` を置くだけで自動消化させる使い方を一級にしている。

```bash
kiro-autonomous                       # = run --watch（常駐。backlog 投入を待つ）
kiro-autonomous --poll 10             # フラグだけ渡しても常駐（run の各フラグはそのまま効く）
kiro-autonomous run                   # 明示 run は従来どおり単発（drained/budget で終了）
```

idle 中はエージェント（kiro-cli/flow）を起動しないので、待機中の常駐は安価。停止は `Ctrl-C` か SIGTERM。
`--root` は cwd 相対なので、**常駐は backlog を置きたい作業ディレクトリで起動**する（または `--root /abs/path`）。

### OS の自動起動に登録する

**Linux（systemd ユーザーユニット）** — `~/.config/systemd/user/kiro-autonomous.service`:

```ini
[Unit]
Description=kiro-autonomous（backlog を待ち受ける常駐ループ）

[Service]
WorkingDirectory=%h/work               # backlog を置く作業ディレクトリ
ExecStart=%h/.local/bin/kiro-autonomous --poll 10 --executor kiro
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now kiro-autonomous     # 今すぐ起動＋ログイン時に自動起動
loginctl enable-linger "$USER"                    # ログアウト後も常駐させたい場合
journalctl --user -u kiro-autonomous -f           # ログ追従
```

**macOS（launchd）** — `~/Library/LaunchAgents/local.kiro-autonomous.plist` に
`ProgramArguments=[kiro-autonomous の絶対パス, --poll, 10, --executor, kiro]`、`WorkingDirectory`、
`RunAtLoad=true`、`KeepAlive=true` を設定して `launchctl load` する。

**Windows** — タスク スケジューラで「ログオン時」トリガに
`python C:\path\to\kiro-autonomous.py --poll 10 --executor kiro`（`開始（作業フォルダ）` に backlog ディレクトリ）を登録する。

## 人の判断とフィードバック往復

タスクが判断待ち（blocked）になると `needs/<id>.md` が生成される。**「## フィードバック」欄に方針を
書き、`- [ ] 確定` を `- [x]` にして保存**すると、次パス（`--watch` なら次 poll）で拾われ、ブロック
解除＋内容を次の実行に反映し、`decisions/<id>.md` に記録される。

**書きかけでの誤発火を防ぐ仕組み**（途中保存しても発動しない）:
- **チェックボックス**: `[x]` にした時だけ確定（明示シグナル）。
- **draft 状態**: 新規タスクは `status: draft` にしておくと消化対象外（書き終えたら `ready` に）。
- **debounce**: `--watch` 中は最終保存から `--debounce`（既定 3 秒）経過するまで待つ。

コマンドでも操作できる:

```bash
kiro-autonomous needs                                  # 何が判断待ちか
kiro-autonomous approve T12 --reason "テスト側を修正"
kiro-autonomous hold prod-deploy --reason "本番は手動"
```

## 検収ゲート（verify=PASS でも人の承認を要する）

verify は機械的な合否でしかない。**verify が通っても人の承認・サインオフが要る**ケース
（本番反映・不可逆操作・課金・質的なレビューなど）のために、タスクを **done 確定の手前で止めて
承認待ち（`review`）**にできる（既定はゲート無し＝従来どおり verify PASS で即 done）。

- **タスク単位**: `backlog/<id>.md` に `- review: human` を書く（その案件だけゲート）。
- **policy 単位**: `policy.md` に `gate: <パターン>` を書く（ID/タイトル部分一致で一括ゲート）。

ゲート対象は verify PASS でも archive せず `review` になり、`needs/<id>.md`（検収待ち）を生成する。

```bash
kiro-autonomous needs               # 検収待ちが「## 検収待ち」として並ぶ（成果参照つき）
kiro-autonomous approve <id> --reason "本番OK"   # 承認＝done 確定（納品書＋archive）
# 差し戻すなら needs/<id>.md に方針を書いて [x]（→ ready で再実行）
```

非 watch の終了コードは、`review`（承認待ち）が残ると `blocked` と同様に `1`（人の対応待ち）。

## 計測（stats）— ループを「engineering」する土台

`stats` で archive・decisions・DELIVERY・backlog から決定的に KPI を集計する。ループの調整はまず計測から。

```bash
kiro-autonomous stats          # スループット・自動化率・retry・人対応待ち
kiro-autonomous stats --json   # 機械処理用
```

- 完了(archive) / 納品(DELIVERY) / 未消化 backlog（status 別）/ 人の対応待ち（blocked+review）
- **自動化率** = 自動解決(auto-resolve＋auto-adjudicate) / (自動＋人の対応)
- **一発 done** = retry 0 で done になった割合 / retry 累計（pending・archived）
- **コスト** = archive 横断の累計トークン / 金額(USD)（納品書 `- cost:` を集計。コスト予算と突合できる）

## タスク依存（`- after:` で DAG 順序）

`backlog/<id>.md` に `- after: T1, T2` を書くと、**その依存が done になるまで消化対象に入らない**
（依存未達のタスクは prioritize で除外）。done は archive へ退避＝backlog から消えるので、依存解決は
自動で進む。依存が blocked/review で止まっていれば従属タスクも待つ。

## タスクの自己生成（followup）— backlog の自走

完了タスクから派生タスクを backlog に生み、ループが自分で仕事を継ぎ足せる（`source: followup`）。2 経路:

- **静的**: タスクに `- followup: <タイトル> :: <verify>`（複数可）。done 時に生成。
- **動的**: act 出力に `@followup <タイトル> :: <verify>` 行（エージェントが「ついでに見つけた」を吐く）。

verify があれば `ready`（同じ run で自走消化）、無ければ `inbox`（triage で人へ）。**`--max-spawn`（既定 20）で
1 run の生成数を上限**＝暴走しない（`0` で無効）。生成は `decisions/` に `spawn-followup` として残る。

## 回帰ゲート（done 確定前のグローバル検査）

per-task の `verify` は通っても**別の所を壊す**（巻き込み事故）ことがある。`--regression-cmd`（または設定
`regression_cmd`）を与えると、**verify PASS 後・done 確定前に共通検査を走らせ**、失敗したら done にせず
人へ回す（`review`/`done` どちらにもせず blocked）。

```bash
kiro-autonomous run --regression-cmd "make -s smoke"          # done 前に毎回スモーク
kiro-autonomous run --regression-cmd "pytest -q" --regression-revert  # 回帰時に未コミット変更を巻き戻す
```

`--regression-revert` は **未コミットの作業ツリー変更のみ** best-effort で戻す（コミット/push 済みは対象外）。既定 off。

## 自律裁定（人の判断を減らす・kiro-cli 門番）

人の判断（`needs`）の**手前にフック**し、**ループ内で自律的に積み直して解けるか／人が要るか**を
kiro-cli に判断させる仕組み（既定 **on**。`--no-auto-adjudicate` で無効化、設定ファイルの
`auto_adjudicate: false` でも切替）。kiro-cli が無い環境では各エスカレーションで一度試して失敗し、
そのまま人へフォールバックする（挙動は従来と同じだが、明示的に切るなら `--no-auto-adjudicate`）。

- 対象は**ループ内の verify 失敗**（繰り返し NG / verify 未定義）。kiro-cli が `requeue`（積み直し）と
  判断したら **needs を作らず ready に戻し**、指示（guidance）を次の試行へ feedback として注入する。
  `escalate`（人へ）や判断不能・kiro-cli 不在は**必ず人へ**フォールバックする（安全側）。
- **判断材料**: 失敗理由に加え、`decisions/<id>.md`（過去の人の判断・auto-adjudicate 履歴）・`journal` の
  当該タスク行（これまでの試行）・`feedback`/`note` を文脈として門番へ渡す（決定的・有界）。「過去に積み直して
  解けていないなら escalate」を効かせ、的外れな積み直しや同じ失敗での再裁定ループを抑える。
- **有限停止**: 1 タスクあたりの自律裁定は `--adjudicate-max`（既定 1）回まで。超えたら従来どおり人へ。
- **人の意思は飛ばさない**: `policy.md` の `deny` や `hold`・`rot` による判断待ちは裁定対象外
  （人の上書きが常に勝つ原則を維持）。`verify` を持たないタスクは「ループでは解けない」ため対象外＝必ず人へ。
- 決定は `decisions/<id>.md` に `auto-adjudicate` として記録される。DR 学習（下記）が先に効けばそちらを優先。

```bash
kiro-autonomous run                       # 既定 on: 人へ回す前に kiro-cli が一次裁定
kiro-autonomous run --adjudicate-max 2    # 1タスクの裁定回数を増やす
kiro-autonomous run --no-auto-adjudicate  # 無効化して常に人へ回す
```

## DR 学習（通知を減らす）

`feedback`/`approve` の決定記録には `- learn: <タイトル> :: <指示>` が残る。タスクが繰り返し NG で
人へ回りそうになると、他案件の `learn` から**タイトルが十分似た過去の指示**（Jaccard ≥ `--learn-threshold`、
既定 0.5）を探し、見つかれば **blocked にせず**その指示を反映して自動的に再実行する（`auto-resolve` を
決定記録に残し通知を抑制）。自動適用は **1 タスク 1 回**まで。`--no-learn` で無効化。

> **裁定と学習の順序**: 繰り返し NG ではまず **DR 学習（決定的・kiro-cli 不要）**を試し、効かなければ
> **自律裁定（kiro-cli）**、それも `requeue` でなければ人へ、の三段で人の判断を絞り込む。

### ltm-use への学習昇格（プロジェクト横断・エージェント不要）

`decisions/` の学習は**その作業ディレクトリ内**だけで効く。`--ltm` を付けると、これを
`ltm-use`（セッション横断の長期記憶）へ**昇格**し、別プロジェクトからも再利用できる。すべて
**決定的なファイル操作**で完結し、LLM／エージェントは一切起動しない:

- **昇格の根拠は実績**: ある `learn` ルールが `auto-resolve` で実際に効いた**回数**が
  `--promote-threshold`（既定 2）以上になったら昇格。`ltm-use` の home
  （`<ltm-home>/memory/home/memories/kiro-autonomous/`）へ frontmatter 付き Markdown を書く。
- **横断 recall**: 学習照合は「ローカル `decisions/` → ヒット無しなら **ltm-use home**」の順に
  フォールバック（同じ Jaccard 照合）。別リポジトリで同種の詰まりが起きると過去の指示を再利用する。
- **冪等・グレースフル**: 昇格済みは出典 DR に `- promoted:` マーカを残し二重昇格しない。
  `--ltm` 無し（既定）や home 未解決なら**何もしない**（home の外へ書かないのが既定）。

```bash
kiro-autonomous run  --ltm                 # run 末尾で実績のある学習を自動昇格＋横断 recall
kiro-autonomous promote                    # 昇格だけ手動実行（明示操作なので常に有効）
#   --ltm-home PATH   ストアのルート（既定 $KIRO_LTM_HOME → ~/.claude）
#   --promote-threshold N   昇格に要する実績回数（既定 2）
```

## 納品書（成果物の検収）

タスク完了時に、検収用のサマリーを2段で残す（人の検品向け。`backlog` と対になる）:
- **個票**: `archive/<id>.md` に「## 納品書」を付す（verify=PASS・**成果参照**・完了時刻）。
- **一覧（受領書）**: `DELIVERY.md` に1行追記（id・タイトル・検収・成果参照・完了）。

**成果参照**は決定的に取得：act 出力の **PR/MR URL** → **commit SHA** → workdir の `git log -1` の順。
成果物が kiro-flow 経由で各リポジトリへ push される前提で、その PR/コミットへ辿れる。

## rot 検知（バックログの掃除）

古い/重複/実行不能タスクを検出して**人の判断へ回す**（消さず棚卸し）:

```bash
kiro-autonomous rot           # 検出して報告（unverifiable / duplicate / stale）
kiro-autonomous rot --fix     # 検出した rot を blocked にして needs/ へ
kiro-autonomous run --rot     # 毎 run の triage に組み込む（--rot-age-days で stale しきい値）
```

## Loop Readiness 監査（`audit`）

[Loop Engineering の Loop Design Checklist / Quick Red Flags](https://github.com/cobusgreyling/loop-engineering/blob/main/docs/loop-design-checklist.md)
を決定的に採点し、「いまどの自律度で無人運用してよいか」を機械判定する（stdlib のみ・エージェント不要）。

```bash
kiro-autonomous audit            # 人が読む（レベル・スコア・チェック・赤旗・提案）
kiro-autonomous audit --json     # 機械処理用
kiro-autonomous audit --strict   # スコア<40 か critical 赤旗で exit 2（CI ゲート）
```

- **レベル**: `L0 Draft → L1 Report → L2 Assisted → L3 Unattended`。各レベルの必須チェックが揃うと昇格。
- **チェック例**: ready タスクは全て verify を持つ（鉄則）／有限停止（max_cycles）／リトライ上限→escalate／
  needs/ エスカレーション先／**コスト予算**（max_tokens|max_cost）／**パス保護**（policy `protect:`）／`--rot` での掃除。
- **赤旗**: 「verify 無し ready タスク」（critical）・「無人運用(watch)なのに予算/保護が未設定」など。
- L3（無人運用可）は **verify 健全＋コスト予算＋保護デニーリスト＋掃除**が揃い、critical 赤旗が無いときだけ宣言される。

```bash
# CI で「無人運用に値するか」を門番に（例: GitHub Actions の1ステップ）
kiro-autonomous audit --strict || echo "Loop がまだ unattended 基準を満たしていない"
```

## 構造化 run-log と自動スロットル（`runlog` / `--throttle`）

[Loop Engineering の operating-loops](https://github.com/cobusgreyling/loop-engineering/blob/main/docs/operating-loops.md)
の「per-run ログ」「slow down/pause の発火条件」を取り込む。`journal.md` が人間可読なのに対し、
**`run-log.jsonl` は run 毎に1行 JSON**（`reason`/`done`/`escalations`/`tokens`/`cost`/`duration_s` …）で、
スプレッドシートや監視に流せる機械可読ログ。

```bash
kiro-autonomous runlog              # 直近10件を表示
kiro-autonomous runlog --json --tail 50
```

**自動スロットル** `--throttle <比率>`（既定 0=off）: `max_tokens`/`max_cost` の比率（例 `0.8`）に達したら
**ハード上限の手前で run を打ち切り**（`reason=throttle`）、`--watch` 中は以降 **report レベルへ降格**して
spend を止めつつ監視は続ける。日次予算に当たって途中のタスクで急停止するのを避ける「緩やかなブレーキ」。

```bash
# 1日2ドルのソフト上限を超えたら act を止めて報告のみに（監視は継続）
kiro-autonomous run --watch --max-cost 2.0 --throttle 0.8
```

## 履歴一致 verify の偽 done 対策（成果差分・`$KIRO_BASE_REV`・`--require-progress`）

`verify: git log | grep -q refactor` のように **verify が「履歴の絶対状態」を見ている**と、過去のコミットに
マッチして **act が何もしなくても done 確定**してしまう（DELIVERY の成果参照も既存コミットを指して紛らわしい）。
対策を3層で入れている。

- **成果参照の真正化（常時）**: DELIVERY/needs の「成果参照」は **act 前(baseline)以降の新規コミット/未コミット
  変更のみ**を載せ、無ければ `(変更なし)` と明記（既存コミットを成果物と偽らない。no-op が一目で分かる）。
  kiro-autonomous 自身の状態ファイル（backlog/・journal・DELIVERY 等）は成果差分から除外する。
- **差分基準の環境変数（常時）**: verify 実行時に **`$KIRO_BASE_REV`（act 前の HEAD）** を渡す。verify を
  `git log $KIRO_BASE_REV..HEAD --grep ...` のように**差分スコープ**で書けば履歴に騙されない。
- **no-progress ガード（opt-in）**: `--require-progress`（または per-task `- expect: changes`）で、verify=PASS でも
  baseline 以降に変更が無ければ **done せず人へ**（履歴一致の偽 done を自動で捕捉）。正当な無変更タスクは
  `- expect: none` で opt-out。

```bash
kiro-autonomous run --require-progress         # 変更を生まない done を偽 done 疑いとして人へ
# タスク側（推奨）: 望む最終状態を assert する verify を書く
#   verify: `grep -q "def extracted_helper" util.py`   ← 履歴ではなくコードの結果を見る
```

## フレーク耐性 verify（`--verify-confirm`）

揺れる（非決定的な）verify を NG と誤読すると、無限の retry churn や、逆に flaky な PASS で未完了タスクを
done 確定する事故につながる（Loop Engineering の "infinite fix loop" / "fixing flakes with code"）。
`--verify-confirm N`（既定 1）は verify を**最大 N 回再実行し、PASS/FAIL が跨いだら flake と判定**して、
**自動修正せず人へ隔離**（blocked・`flake` マーカ付き。retry は増やさない）。一致すればその結果で確定する。

```bash
kiro-autonomous run --verify-confirm 2   # verify を2回見て不安定なら人へ（コストは回数分）
```

- 既定 `1` は従来どおり1回（挙動不変）。`2` 以上で安定性チェックが有効。
- flake は「テストや環境の問題」であって「コードを直す」案件ではない、という Loop Engineering の原則に沿う。
## policy.md（優先順位・実行先の上書き）

```yaml
deny:    prod      # "prod" を含むタスクは自動実行しない（実行前に人の判断待ち）
pin:     T3        # T3 を最優先
defer:   cleanup   # "cleanup" を含むタスクは後回し
offload: heavy     # "heavy" を含むタスクは分散環境へ移譲（--git-bus 設定時）
gate:    release   # "release" を含むタスクは verify PASS でも done 前に人の承認を要する（検収ゲート）
protect: auth/**   # act が auth/ 配下を**変更したら** verify PASS でも done せず人の承認へ（安全ゲート）
```

- `deny` は**実行前**（タスク選択）で止め、`gate` は**実行・verify は通すが done 確定前**で止める（止める位置が違う）。
- **`protect`**（パスのデニーリスト）は `gate` と同じ done 直前に効くが、**判定対象がタスクではなく「act が触ったファイル」**。
  glob で書け（`*`=スラッシュ以外 / `**`=スラッシュ含む。`**/` は 0 階層も一致）、一致した変更があれば検収待ち(review)に落とし、
  `approve` で done 確定／フィードバックで差し戻し。無人運用で `.env`・`secrets/`・`auth/`・`payments/`・`**/migrations/**`・
  infra 等を「自動で書き換えさせない」ための最低ラインの安全策（Loop Engineering の safety denylist）。

```yaml
# 推奨デニーリスト例（必要に応じて1行ずつ）
protect: .env
protect: .env.*
protect: **/secrets/**
protect: **/credentials/**
protect: **/*_key*
protect: **/migrations/**
protect: auth/**
protect: payments/**
protect: k8s/production/**
```

> 変更ファイルの検出は `cfg.workdir` の git（未コミット＋act 後コミット差分）で best-effort。git でない／
> remote・daemon にオフロードした実行は workdir に変更が出ないため対象外（その場合は実行先側で守る）。

## 分散移譲（remote）

`--git-bus <共有gitリポジトリ>` を設定し、`policy.md` に `offload: <パターン>` を書くと、一致した
タスクは `--location` が `remote` に解決され、kiro-flow の `--git` 分散バス越しに別マシンの daemon へ
**submit してオフロード**する（その run の完了を待ってから verify）。それ以外は local 実行。

## 収束（必ず止まる）

| 停止理由 | 意味 | フラグ |
|----------|------|--------|
| `drained` | 消化可能タスクが尽きた | — |
| `budget` | 予算が尽きた（サイクル数 / 実時間） | `--max-cycles 20` / `--max-seconds 0` |
| `cost` | 予算が尽きた（トークン / 金額） | `--max-tokens 0` / `--max-cost 0`（0=無制限） |

**コスト予算**: 無人運用で暴走課金を止める安全弁。`--max-tokens` / `--max-cost`（設定ファイル可）を超えると
`cost` で停止（終了コード 2）。計上は **act 出力の `@cost tokens=… usd=…` 行**を加算する決定的方式
（エージェントが吐かなければ 0）。done 時に納品書へ `- cost:` を残すので `stats` が archive 横断で累計
トークン/金額を出す。

検証 NG は積み直して再挑戦。`--max-retries 2` を超えると人の判断（blocked）へ回す。
`--watch` の場合は終了条件後もプロセスは生存して backlog/ を監視する（**idle 中は kiro-cli/flow を
起動しない**＝エージェントは待機しない）。

**レーン減速（pace）**: `--pace <秒>` で1サイクルの下限間隔を設けてバーストを防ぐ。`--max-seconds`
を併用すると `max_seconds/max_cycles` のペースに均す。

## 通知

人の判断待ちへの**遷移時だけ**、要約を標準出力に出す（毎サイクルでは鳴らさない）。
案件毎の `needs/<id>.md` が永続的な対応窓口。`--notify-cmd '<cmd>'` で teams-use / outlook-use /
issue-mailbox 等へダイジェストをパイプできる。

## 終了コード（非 watch 時）

| code | 意味 |
|------|------|
| 0 | `drained` かつ判断待ち無し（完走） |
| 1 | 判断待ち（blocked）あり |
| 2 | `budget` / `cost` で停止 |

## テスト

```bash
KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-autonomous/tests -v
```

優先順位付け・検証ゲート・積み直し・収束・location/pace・フィードバック往復・watch・案件毎の
決定記録を kiro-flow 抜きで検証し、kiro-flow stub を 1 回叩く統合テストも含む（無ければ skip）。
