# agent-project

> 旧 `kiro-project` 系統から移行した後継実装。設計正典は
> [`docs/designs/agent-project-design.md`](../../docs/designs/agent-project-design.md)。
> 改称方針: [`docs/designs/agent-tools-rename-design.md`](../../docs/designs/agent-tools-rename-design.md)。

**単一プロジェクトのバックログを自律的に優先順位付け・実行・検証・収束させ、人の判断が要る分だけ差し戻す制御層。**
カレントディレクトリ（または `--root`）をプロジェクトルートとし、`charter.md` / `repos.json` を入力に
成果物（`archive/`・`DELIVERY.md`・`needs/`・`decisions/`）を出力する。
最優先タスクを agent-flow に実行させ、**`verify` をローカルで実行して PASS したものだけ done に確定**
（`archive/` へ退避）、NG なら積み直す。backlog が尽きるか予算が尽きるまで繰り返し、人の判断は案件毎の
`needs/<id>.md`（フィードバック欄つき）で差し出し、判断は `decisions/<id>.md` に残す。

> - 設計の正典: [`docs/designs/agent-project-design.md`](../../docs/designs/agent-project-design.md)（統合設計書。本書は運用リファレンス）
> - 熟練度別の導入手順: [`GUIDE.md`](GUIDE.md)（L0 下見 → L1 試運転 → L2 日常運用 → L3 無人運用 → L4 スケール）
> - タスク書式の正典: [`backlog.md.example`](backlog.md.example) ／ プロジェクト憲章: [`charter.md.example`](charter.md.example)
> - 実行（コード変更・検証）は agent-flow（＝エージェント CLI）へ委譲する。

## 全体像

役割の異なる 3 層で動く。**構成は「1 プロジェクト = 1 ディレクトリ = 1 プロセス」**。複数プロジェクトは
ディレクトリ（通常は状態リポジトリの clone）を並べてそれぞれで回し、束ねた可視化・操作は
[agent-dashboard](../agent-dashboard/) が git 越しに担う。

| 層 | 担当 | 実体 |
|----|------|------|
| 上位（目標駆動） | 目標(charter)→backlog 生成 / 達成評価 / 改善サイクル | `run`（charter あり） |
| 外側（制御） | 優先順位付け / 検証ゲート / 積み直し / 収束 / 決定記録 / 安全ゲート | `run`（charter 無し） |
| 内側（実行） | タスクの分解 → act → 内側 verify ループ | `agent-flow run`（別ツール） |

> **プロセスは `run` に一本化**。`<root>/charter.md` があれば `run` が自動で目標駆動（plan→execute→evaluate）に入る。
> charter 無しは従来の backlog 消化ループ。`--watch` がそのまま「目標を満たすまで回り続ける常駐」になる。

**正準ループ（5 点）**:

1. `backlog/<id>.md` を読み優先順位をつけ、最優先を agent-flow に投げる。
2. 優先順位付けは `--planner agent`（エージェントが `priority` も加味）/ `none`（priority 降順→最古）。人は `policy.md` で上書きできる。
3. agent-flow の結果を verify ゲートで検証。done は `archive/` へ退避、NG なら積み直す。
4. backlog が尽きるか予算（サイクル/実時間/コスト）が尽きるまで反復（`--watch` なら尽きても監視を続ける）。
5. 人の判断・フィードバックは案件毎 `decisions/<id>.md` に保存する。

> **鉄則**: done は **verify の終了コード 0 のみ**が根拠（自己申告 done の禁止）。必ず有限回で止まる。
> 人の `policy.md` ＞ エージェント提案。本体は標準ライブラリのみ・決定的（知能は agent-flow / エージェント CLI へ委譲）。

## 依存・インストール

- `python3`（標準ライブラリのみ。pip 依存なし）
- `agent-flow`（act の委譲先。PATH か `tools/agent-flow/agent-flow.py` を自動解決。`--dry-run` なら不要）
- エージェント CLI（LLM 呼び出し＝分解・優先順位・裁定・ルーティングに使用。設定 `agent_cli` / CLI `--agent-cli` で切替）
  - `kiro`（既定）… `kiro-cli chat`。`--planner none` なら不要
  - `claude` … Claude Code ヘッドレス（`claude -p`・プロンプトは stdin 渡し）
  - `copilot` … GitHub Copilot CLI（`copilot -p`・standalone 版）
  - `codex` … OpenAI Codex CLI（`codex exec`・最終応答は `--output-last-message` 経由で取得）
  - モデルは設定 `model:` で指定（省略時は各 CLI の既定。実行層 agent-flow 側は agent-flow.yaml の `agent_cli` / `model` で揃える）

```bash
bash tools/agent-project/install.sh           # ~/.local/bin/agent-project
```
未インストールでも `python3 tools/agent-project/agent-project.py ...` で代用可。

## クイックスタート

```bash
mkdir my-proj && cd my-proj                          # プロジェクトルート（cwd）を用意

# バックログへ積む（<root>/backlog に作られる）
agent-project enqueue --title "README に概要見出しを追加" --verify 'grep -q "## 概要" README.md'
agent-project run --executor agent                     # 自律消化

# 目標から回す。charter.md を置けば run が自動で plan→execute→evaluate に入る（専用コマンド不要）
cp tools/agent-project/charter.md.example ./charter.md
agent-project run --executor agent

# 常駐: 新規タスク/フィードバックを監視して自動消化（idle 中はエージェント非起動）
agent-project run --watch --poll 10 --executor agent

# エージェント CLI 無しでプロトコル確認（決定的・無料）
agent-project run --planner none --flow-planner stub --executor stub
```

`backlog/<id>.md` に `- priority: N`（大ほど高）で外部から順序を制御できる。サブコマンド省略
（`agent-project` 単体）は **`run --watch` と同義**（cwd のプロジェクトを常駐監視）。

## ディレクトリ構成（プロジェクトルート直下フラット）

**プロジェクトルート（`--root`・既定 cwd）の直下にすべて集約**される（各パスは `--backlog` 等で個別上書きも可）。

```
<root>/                    ← プロジェクトルート（cwd。通常は状態リポジトリの clone）
  charter.md           プロジェクト憲章（人が書く・最上位入力。正典 charter.md.example）
  repos.yaml|json      リポジトリレジストリ（共通スキーマ schemas/repos.schema.json）。手書きが
                       あればそれが正（charter の ## repos は互換入力）。無ければ charter から
                       repos.json を自動生成（_meta 付き・正は charter に追従）＝codd-gate 等の
                       外部ツールへ「ファイルとして渡す」。charter 無しでもルーティングに効く
  project.json         project のサイクル状態（PASS 履歴・stall・cost。run が増分更新）
  policy.md            優先順位・実行先・安全ゲートの上書き（人だけが書く）
  backlog/<id>.md      タスク本体（案件毎・人が追加できる。done で archive/ へ退避）
  needs/<id>.md        判断待ち/検収待ちの通知＋決定記入欄（MADR 互換 ADR。人が記入→自動再開）
  decisions/<id>.md    人の判断・承認・フィードバックの決定記録（learn＝学習材料。append-only）
  archive/<id>.md      完了タスクの保全先（検収用「納品書」付き。backlog と 1:1）
  DELIVERY.md          納品一覧（受領書）。done を1行ずつ追記
  journal.md           機械のサイクルログ（人間可読・閾値超過で journal-archive/ へ自動ローテーション。
                       設定 journal_max_bytes（既定 256KB・0 で無効）/ journal_keep（保持世代・既定 20））
                       ／ run-log.jsonl  構造化 run-log（JSON）
  status.json          daemon の生存信号（watch/level/paused/updated_iso）。git 同期経由でリモート
                       viewer の稼働判定に使う（[daemon の生存信号](#daemon-の生存信号statusjson--リモート-viewer-の稼働判定)）
  paused.json          一時停止マーカー（commands の pause で生成・resume で削除）
  inbox/  claims/  autonomy/  bus/   取り込み口 / 原子的クレーム / track 状態 / agent-flow 一時バス
  commands/<name>.json 人の指示（approve/hold/pin/defer/revise/replan/pause/resume/stop）の
                       ドロップ口（CLI 不要。run/watch が取り込む）
  .state-git/          状態 git 同期の管理クローン（ルートが git でなく state_git 設定時のみ）
```

複数プロジェクトはこのディレクトリを並べ、それぞれで `agent-project start` する。稼働インスタンスの
レジストリ（`~/.agent-project/instances/`・`logs/`）は**グローバル**で、各プロジェクト root を監視先として
登録する＝`instances` で複数プロジェクト・複数ホストを横断発見できる（後述）。

## 実行の委譲（`--location`）

「どこで・どう動かすか」は `--location`（既定 `auto`）に集約。

| location | 委譲方法 | daemon | 用途 |
|----------|---------|--------|------|
| `local` | `agent-flow run`（単発・同期） | 不要 | 既定の実体。逐次処理はこれで十分 |
| `daemon` | `submit` → `result` で done 待ち | ローカル daemon（無ければ local にフォールバック） | warm worker 再利用 |
| `remote` | `submit`（`--git`）→ `result` で done 待ち | 共有 git バスの remote daemon が必須 | 別マシンへオフロード |

**非ブロッキング委譲（`act_async`）**: `daemon`/`remote` は既定では結果を待つ（ブロック）。`act_async: true` に
すると **submit して待たず**タスクを `offloaded` に退避し、次パスで `result` を1回だけポーリングして
**終端した run だけ**を消化する。`executor: gitlab` のように MR 承認まで数日かかる委譲でループを塞がず、
同じプロジェクトの他タスク・他プロジェクトを並行に進められる。専用 daemon が run を保持するので待たなくても
結果は取りこぼさず、submit は決定的 run_id なので agent-project が再起動しても同じ run に再合流する。
`act_timeout: 0`（＋ agent-flow `gitlab.timeout/approved_timeout: 0`）と併用すると、誤タイムアウト由来の
retry ループが完全に消える。既定 off＝従来どおり同期で待つ（完全後方互換）。

`auto` = offload 一致＋`--git-bus` → remote ／ ローカル daemon 稼働 → daemon ／ 他 → local。daemon 検知は
agent-flow と同じロックで行う：バスを `realpath` で正規化したキーで `flock` を見て、`flock` が使えない環境
（Windows・一部の異種FS）では daemon が記録した PID の生存で補完する。**外部で起動した daemon を取りこぼさない
ため、起動側とこちらでロック置き場を一致させること**——既定は `$TMPDIR/agent-flow-locks/` だが、`TMPDIR` が
食い違う場合は両者の設定ファイルで `lock_dir`（CLI `--lock-dir`）に同じ絶対パスを指定する。どちらの経路でも
verify は act 完了後に走る。

> **外部常駐の agent-flow daemon を使うには**：既定のバスは agent-project が `<root>/bus`、agent-flow が `./bus` で、
> **プロジェクトルートで `agent-flow daemon` を起動すれば設定なしで同じバスに一致**し、`location=auto/daemon` が
> daemon を検知して warm worker を再利用できる。プロジェクト外の共有バスを使うときだけ両者の `bus:` を同じ値に
> 明示する。TMPDIR が食い違う構成では併せて `lock_dir` も一致させる。

**並列消費（`--concurrency N`、既定 1）**: 依存解決済みの独立タスクを先頭から最大 N 件 daemon/remote へ並行
submit し、実体の並列は agent-flow の worker に委ねる。**実行の重い部分だけ並列化し、verify・done/archive・
決定記録・派生生成は逐次のまま**（競合回避）。local 単発 run は逐次。1 サイクル=1 タスクの計上・予算は不変。

**原子的クレーム（二重実行防止）**: 各タスクは実行前に `claims/<id>.lock` を `O_CREAT|O_EXCL` で確保した者だけが
回す。**同じ backlog を複数プロセス/ホストで回しても同一タスクは二度実行されない**。取得後に disk を再検証し、
owner 失踪は TTL 超で奪取、終了で解放。

**分散移譲（remote）**: `--git-bus <共有 git リポジトリ>`＋`policy.md` の `offload: <パターン>` 一致タスクは
`remote` に解決され、agent-flow の `--git` 分散バス越しに別マシンの daemon へ submit する（完了を待って verify）。

```bash
agent-project run --executor agent                              # 既定 local（単発 run）
agent-flow --bus .agent-project-bus daemon --workers 3 &       # warm worker
agent-project run --location daemon --concurrency 3 --executor agent
```

**executor プラグイン**: `--executor`（設定 `executor`）には組み込みの `agent` / `stub` に加えて、
agent-flow の executor プラグイン名（例 `gitlab`）や `.py` パスをそのまま渡せる。値は `agent-flow run --executor <値>`
へ委譲され、プラグイン固有設定は agent-flow 側の設定（例 `gitlab:` ブロック）で行う。

```bash
agent-project run --executor gitlab               # 各タスクを GitLab イシュー化し approved まで待つ
agent-project run --executor /path/to/my_exec.py  # 任意の executor プラグイン（.py パス）
```

## 検証ゲートと安全（done を守る）

verify は done 確定の唯一の根拠だが機械的合否でしかない。以下のゲートが多層で守る（既定はいずれも最小限）。

### verify を人が書かなくてよくする（accept / verify_template）

完了条件の決定的シェルは人には書きにくい。タスクは `verify` の代わりに次を持てる（最終的に concrete な `verify` に
materialize され、「done は verify のみが根拠」の鉄則は不変）:

- **`- verify_template: <名前> :: <引数…>`** … 決定的に展開（**エージェント不要**）。`file-contains :: <path> :: <文字列>` /
  `file-exists :: <path>` / `defines :: <symbol> :: <path>` / `diff-contains :: <文字列>`（act 後の差分・`$KIRO_BASE_REV`）/
  `cmd-succeeds :: <コマンド>`。enqueue 時に即展開。
- **`- accept: <自然言語の完了条件>`** … 実行時にエージェントが**偽 done 防止規則を織り込んで決定的 verify を合成**し、
  タスクへ書き戻す（`verify_source: synth`）。合成できなければ verify 空のまま＝従来どおり人へ。

```bash
agent-project enqueue --title "規約に最終更新日を表示" --verify-template 'file-contains :: web/terms.html :: 最終更新'
agent-project enqueue --title "概要見出しを追加"       --accept "README に ## 概要 の見出しがある"
```

> verify を自分で書ければそれが最良（最も確実）。accept/template は「書けない人」の入口で、生成物はレビューできる
> （タスクに残る）。シェルで検証できないものは auto-done させず検収ゲート（`- review: human`）で人承認に回すとよい。

### タスクに意図と境界を書く（why / desc / scope / out_of_scope / constraints / hints / demo）

verify は「合否」を守るが「やり方・範囲・意図」は縛れない。一般的なバックログ項目に倣った任意の記述
フィールドで、**人のレビュー材料**と**ワーカーの誘導**を同時に強化できる（詳細は
[`backlog.md.example`](backlog.md.example)）:

- `why`（背景・価値）/ `desc`（作業内容の詳細）… 実装の判断基準と具体の指示。
- `scope` / `out_of_scope` … 変更してよい範囲と**やらないこと**。スコープ膨張・過剰実装を防ぐ
  （範囲外の気づきは `@followup` 提案へ誘導される）。
- `constraints` … このタスク固有の制約（`rules.md`=全タスク共通・charter=プロジェクト共通への上乗せ層）。
- `hints` … 実装の手がかり（関連ファイル・参考実装）。
- `demo` … 人の検収観点（検収で何をどう確かめるか。ワーカーにも「人がここを見る」前提が伝わる）。

書けば act 要求文へ整形注入され、実行前レビュー・検収の票（`needs/<id>.md`）にも載る。plan（charter 分解）は
`why` を必ず付けて提案するので、実行前レビューで「なぜこのタスクか」から判断できる。いずれも**誘導であって
完了条件ではない**（done の根拠は verify のみ）。`enqueue --why … --scope …` / `revise <id> --out-of-scope …` で
CLI からも付与・修正できる。

### verify の鉄則と偽 done 対策

`git log | grep refactor` のように **verify が「履歴の絶対状態」を見る**と、過去コミットにマッチして act が
何もしなくても done 確定する。鉄則は **「履歴でなく望む最終状態/差分を assert する」**。3 層で対策:

- **成果参照の真正化（常時）**: DELIVERY/needs の成果参照は **act 前(baseline)以降の新規変更のみ**を載せ、無ければ
  `(変更なし)`（既存コミットを成果物と偽らない）。agent-project 自身の状態ファイルは差分から除外。
- **差分基準（常時）**: verify 実行時に `$KIRO_BASE_REV`（act 前 HEAD）を渡す。`git log $KIRO_BASE_REV..HEAD --grep …`
  で差分スコープ verify が書ける。
- **no-progress ガード（opt-in）**: `--require-progress` / per-task `- expect: changes` で、verify=PASS でも変更が
  無ければ done せず人へ。正当な無変更は `- expect: none` で opt-out。

### タスクブランチと成果物レビュー（task_branch / delivery_review・既定 on）

- 各タスクの成果は **`ap/<task-id>`** ブランチに集約される（リトライも同一ブランチ。agent-flow の
  workspace `branch` として伝搬）。
- verify PASS 後は**常に検収待ち（review）**になり、人の承認で done 確定する。review 到達時に
  GitLab へ到達できる設定（`GITLAB_TOKEN`/`GL_TOKEN`）があれば **ap/<task-id> → target の MR を自動作成**し、
  承認時に**クリーン（コンフリクト無し・未解決レビューコメント無し）なら自動マージ**（差分なしはクローズ・
  未クリーンは差し戻しコメントを付けて review のまま）。却下（`reject`）は MR クローズ＋ブランチ削除。
- 従来の自動 done へは `--no-delivery-review`／設定 `delivery_review: false`、ブランチ集約の無効化は
  `--no-task-branch`。

### run ブリーフ（差し戻し意図とノード発見制約の伝播・task_branch 有効時）

分散生成した成果の一貫性は、**事後の集約ノード**（agent-flow の `reduce`/`synthesize` は依存ノードの
全出力を 1 コンテキストへ読む＝規模が大きくなるほどコンテキスト制約で損失的になる）だけには頼れない。
そこで agent-project は、リトライ（差し戻し）の意図と各ノードが実行中に発見した恒常制約を、タスクの
ターゲットブランチ **`ap/<task-id>` と同じキー**で **`<root>/brief/<task-id>.md`**（`rules.md` と同じ
`<root>` 直下）に**追記のみ**で蓄積した「**run ブリーフ**」にまとめ、`build_request` 経由で**以後の
全 run・全分散ノードへ均一に注入**する（＝**事前伝播**）。各ノードはこの小さく正規化された共有ブリーフに
個別準拠すればよく、集約ノードが全出力を読み直す必要がない。

- **正本 `rules.md` との関係**: `rules.md` は人が書く**恒久**ルール（全タスク常時）。run ブリーフは
  その一段手前——**タスク/ブランチ・スコープで一時・自動蓄積・追記のみ**——の層で、成果が done/マージ
  したら役目を終える（一般化できる項目は learn→rules 昇格で正本へ格上げ）。置き場所も `rules.md` と
  同じ `<root>` 直下に並べ「正本 `rules.md` ↔ 一時 `brief/`」の対比を明確にする。
- **なぜブリーフか**: `feedback` フィールドは差し戻しのたびに上書きされ過去の指摘が消える。`rules.md`
  は hit 閾値の昇格を要し即時には効かない。ブリーフは両者の隙間を埋め、ブランチと同じキーなので
  **リトライ（新 run-id）でも指摘がブランチと一緒に引き継がれる**。
- **蓄積の入口**: 検収差し戻し（needs feedback）・`revise`・gitlab 却下コメント・cohort 波及（兄弟へ横展開）。
  いずれも正規化・重複排除して追記する（冪等・決定的）。
- **ノード発見制約の環流**: 各ノードは「他ノードも従うべき恒常的な制約・規約」（命名・配置・様式・前提の
  統一など）を発見したら、最終成果に機械可読な JSON `{"constraints": ["…"]}` を添えて提示する。
  agent-project は run 終了時に `agent-flow result --json` から回収し run ブリーフへ環流する（次 run 以降の全ノードへ伝播）。
- **無効化**: `--no-task-branch`（ブリーフのライフサイクルは task_branch に連動）。ブリーフファイルは人が編集・削除してよい。

### フレーク耐性 / 回帰 / 検収 / パス保護

- **フレーク耐性** `--verify-confirm N`（既定 1）: verify を最大 N 回再実行し PASS/FAIL が跨いだら **flake** と判定して
  自動修正せず人へ隔離（retry を増やさない）。揺れる verify の NG churn や flaky PASS の偽 done を防ぐ。
- **回帰ゲート** `--regression-cmd "<cmd>"`: verify PASS 後・done 確定前に共通検査を走らせ、失敗したら done にせず
  人へ。`--regression-revert` は未コミットの作業ツリー変更のみ best-effort で戻す（既定 off）。
- **検収ゲート**（verify=PASS でも人の承認）: タスク `- review: human` か policy `gate: <パターン>`。対象は archive せず
  `review`（検収待ち）になり `needs/<id>.md` を生成。`approve <id>` で done 確定／フィードバックで差し戻し。
- **パス保護**（safety denylist）: policy `protect: <glob>` に一致するファイルを act が**変更したら** verify=PASS でも
  done せず検収待ちへ。`gate` がタスク一致なのに対し `protect` は**変更されたパス**一致。
- **一貫性ゲート（codd-gate 連携・オプション）**: ドキュメント・コード・テストの整合は**完全独立**の
  ツール [`codd-gate`](../codd-gate/README.md)（本ツールの install.sh が隣にあれば同梱インストールする）で
  護れる。境界の不変条件として、`agent_project` パッケージは汎用フックだけを提供し、codd-gate を
  名指し・import・自動配線しない。結合は共通スキーマ（`schemas/`）と、人か install 手順が E1〜E3 の
  汎用フックに設定する codd-gate コマンド文字列だけ。リポジトリ定義は本ツールが charter から自動生成する
  `<root>/repos.json` を codd-gate が `--repos` で読む。**人か install 手順が yaml または CLI にコマンドを
  明示設定した場合だけ連携が有効になる。自動検出だけでは有効にならない。** E2 と E3 の設定例:

  ```yaml
  regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos <root>/repos.json'
  intake_cmd: 'codd-gate tasks --debt --repos <root>/repos.json'
  ```

  CLI なら同じ値を `--regression-cmd 'codd-gate verify …'` と
  `--intake-cmd 'codd-gate tasks --debt …'` に渡す。agent-project は `regression_cmd` を毎タスクの verify
  PASS 後・done 確定前に実行し、NG なら done を止める。`codd-gate tasks` は既存負債を JSON の修復タスクへ
  変換し、agent-project がパス開始時と watch の idle 中に backlog へ冪等に取り込む。`codd-gate tasks` は
  E1 の task verify に `codd-gate check …` を置き、期待状態に戻ったことを確認する。人は charter acceptance に
  `codd-gate verify --debt --max-broken N …` を置き、受入時の負債ラチェットに使う。

  `codd_gate_*.py` は `tools/agent-project/` 直下の任意 sibling 部品で、人か install 手順が明示起動したときだけ
  codd-gate を検出し、`codd_gate_routing.py` が実引数を組み立てる。現在の
  `codd_gate_regression.py` が永続化前に確認するのは codd-gate の実体だけで、バージョン、
  repos schema 互換性、対応機能は検査しない。同ツールが永続化するのは、
  `.agent/agent-project.yaml` の `regression_cmd` 1行だけ。`intake_cmd` は書かず、
  人か install 手順が設定する。生成ツールはリポジトリルートで
  `python3 tools/agent-project/codd_gate_regression.py --config .agent/agent-project.yaml` と明示実行する。
  パッケージは sibling 部品を探索・import せず、`build_config` から値を差し込む自動配線も持たない。
  未設定のフック値は空のまま（＝連携なし）で通過する。`.agent/agent-project.yaml` は人専有ファイルで、
  人か、人が明示起動した `codd_gate_regression.py` だけが書き込む。sibling 部品を削除してもパッケージの
  挙動は変わらない。codd-gate が未検出なら、生成ツールは値を書かず終了する（詳細は
  [`codd-gate-design.md`](../../docs/designs/codd-gate-design.md) §4「agent-project との結合点」・
  §4.1「値の組み立てと永続化を担う任意部品」・
  §4.2「境界の完了条件」）。
  整理完了の条件は、次の決定的ゲートが exit 0 を返すこと。

  ```bash
  ! git grep -nE '_apply_codd_gate|_codd_gate|import codd_gate' -- tools/agent-project/agent_project
  ```

### policy.md（人による上書き・per-project）

```yaml
deny:    prod        # "prod" を含むタスクは自動実行しない（実行前に止める）
pin:     T3          # 最優先 ／ defer: cleanup（後回し）
offload: heavy       # 分散環境へ移譲（--git-bus 設定時）
gate:    release     # verify PASS でも done 前に人の承認（検収ゲート・タスク一致）
protect: auth/**     # act が触ったら done せず承認へ（パス一致。glob: *=非/ **=/含む・**/ は0階層可）
route:   API -> app  # タスク（id/タイトル一致）の書込先ワークスペースを charter の repo 名へ割り当てる
```

`deny` は**実行前**で止め、`gate`/`protect` は**実行・verify は通すが done 確定前**で止める（止める位置が違う）。
無人運用の推奨デニーリスト: `.env` / `.env.*` / `**/secrets/**` / `**/credentials/**` / `**/*_key*` /
`**/migrations/**` / `auth/**` / `payments/**` / `k8s/production/**`。
> 変更検出は workdir の git（未コミット＋act 後差分）で best-effort。remote/daemon オフロードは workdir に差分が
> 出ないため対象外（実行先側で守る）。

## 収束と予算（必ず止まる）

| 停止理由 | 意味 | フラグ |
|----------|------|--------|
| `drained` | 消化可能タスクが尽きた | — |
| `budget` | サイクル数 / 実時間が尽きた | `--max-cycles`(20) / `--max-seconds`(0=無制限) |
| `cost` | トークン / 金額が尽きた | `--max-tokens` / `--max-cost`（0=無制限） |
| `throttle` | ソフト予算比率超過（watch は report 降格で spend を止め監視継続） | `--throttle`（例 0.8） |

- **コスト計上**は act 出力の `@cost tokens=… usd=…` 行を加算（決定的・吐かなければ 0）。done 時に納品書へ `- cost:`
  を残すので `stats` が累計を出す。検証 NG は `--max-retries`（既定 2）超で人へ。
- **レーン減速** `--pace <秒>` で 1 サイクルの下限間隔。`--max-seconds` 併用で `max_seconds/max_cycles` に均す。

**終了コード（非 watch 時）**: `0`＝drained かつ人の対応待ち無し ／ `1`＝人の対応待ち（blocked/review）あり ／
`2`＝budget/cost 停止。

## 自律度（信頼を段階的に明け渡す）

| level | act | done | 用途 |
|-------|-----|------|------|
| `report` | しない | — | 「何を・どの順で回すか」だけ報告（消化せず計画を出す安全な下見） |
| `assisted` | する | 人が `approve`（全件 review） | 実行するが done は必ず人が承認 |
| `unattended`（既定） | する | 自動（ゲート通過時） | protect/gate/regression を通れば自動 done |

- **タスク単位の上書き**: タスク行 `- level: …`。実効 = `- level:`（明示）＞ track の自動昇格 ＞ グローバル `--level`。
  `protect`/`gate`/`regression` は level に依らず常に上乗せ。`report` のタスクは実行せず計画に保留。
- **実績連動の自動昇格（opt-in）** `--auto-level` ＋ `- track: <名前>`: 同種群の手戻り率が低ければ level を自動で 1 段
  上げ、手戻り（差し戻し/回帰/偽done）で下げる。ceiling 既定 `assisted`（`--auto-level-max unattended` で完全無人化を
  解禁）。track 状態は `<root>/autonomy/<track>.json`、遷移は `decisions/` に監査記録。
- **適性の採点** `audit`: backlog/policy/config/state から決定的に L0–L3 を採点（スコア・赤旗・提案）。`audit --strict` は
  スコア<40 か critical 赤旗で exit 2（CI ゲート）。L3 は verify 健全＋コスト予算＋保護デニーリスト＋掃除が揃うときのみ。

```bash
agent-project run --level report                 # 計画だけ（act しない）
agent-project run --level assisted               # 実行するが done は approve 待ち
agent-project run --auto-level --auto-level-max unattended   # 実績で自動昇格
agent-project audit --strict                     # 無人運用に値するかの門番
```

## 人の判断とフィードバック

タスクが人の判断へ回ると案件毎 `needs/<id>.md` が生成される。

- **実行前レビュー（plan_review・既定 on）**: 新規タスクはすべて `proposed` で入り、**人の承認を通る
  まで実行されない**。needs/<id>.md（実行前レビュー票・タスク定義つき）で三値の決着ができる:
  - **承認** … `approve <id>`（または票を空のまま `[x]`）→ ready になり実行対象へ
  - **差し戻し** … 票に修正指示を書いて `[x]` → agent-project がタスク定義を修正して**再提案**（再び proposed）
  - **却下** … `reject <id> --reason ...` → 廃止（archive へ退避・avoid 記録）。**依存先（after 逆辺・推移）は
    proposed に戻して再審査**にかけ、charter があれば再計画を要求する
  従来の自動投入（verify ありは即 ready）へは `--no-plan-review`／設定 `plan_review: false` で戻せる。
- **影響範囲の一覧（impact）**: `agent-project impact <id>` で前提（after 上流）と依存先（下流・推移）を
  一覧表示。revise / reject 時にも影響先が出力・DR に添えられる。

- **承認 = 完了か、積み直しか**: `approve <id>` は既定で「ブロックを解いて積み直す」。
  成果を受け入れて**完了（done 確定）にする**ときは `approve <id> --complete`
  （commands ドロップなら `{"command":"approve","complete":true}`）を使う。
  以前は承認理由の文面から完了意図を推定していたが、推定が外れると黙って積み直され、
  同じ工程を再実行してまた要対応に戻る往復になっていた。**意図は呼び出し側が明示する**。
  agent-dashboard の「承認して完了にする」はこの `complete` を送る。

- **フィードバック往復**: 「## Decision Outcome」欄（MADR 互換。旧「## フィードバック」も可）に方針を書き `- [ ] 確定` を `- [x]` にして保存すると、次パスで拾われ
  ブロック解除＋内容を次 act に反映し `decisions/<id>.md` に記録。**誤発火防止**は ①チェックボックス `[x]`（空でも「そのまま
  再実行」）②`status: draft`（消化対象外）③`--debounce`（既定 3 秒）。
- **決定記録（DR）**: 人の判断は承認操作と不可分に `decisions/<id>.md` へ append-only。`approve`（修正承認）/
  `hold`（policy deny 追加）/ `reprioritize --pin|--defer`。DR の `- learn:` 行が下記の学習材料になる。
- **判断の自動抽出（learn/avoid・既定 on）** `--learn-capture`: 承認/保留の**理由をそのまま横断知識に蓄積**する。
  `approve`（差し戻し修正・検収承認いずれも）の理由は `- learn:`（＝どう解けば良いか。DR 学習・ltm が使う）、
  `hold` の理由は `- avoid:`（＝この種は自動実行させない。下記リコールが使う）として残す。`--no-learn-capture` で
  抑止（DR の本文は従来どおり残る）。従来 `- learn:` は差し戻し系にしか付かず、承認・保留の判断は横断的に死蔵していた。
- **予防リコール（投入/triage の shift-left・既定 on）** `--intake-recall`: `enqueue`／`triage` の時点で、新規タスクが
  過去の `hold`（`- avoid:`）とタイトル類似（Jaccard ≥ `--learn-threshold`）なら、**ready にせず実行前に人の判断へ回す**
  （`blocked`＋`needs/<id>.md`。verify を持つタスクでも triage の inbox→ready 自動昇格に呑まれない）。人は `approve` で
  実行許可／`hold` で恒久デニー化。DR 学習が「一度失敗してから」人を絞るのに対し、これは**投入の時点で先回りして止める**。
  `--no-intake-recall` で無効。決定的なファイル走査＋Jaccard のみ（エージェント不要）。
- **能動フィードバック（revise）**: needs はループが人へ回した時の**受動**の口。対して `revise` は、
  人が気づいた時点で**能動的に**タスクを修正し指示を届ける口（例: LLM がローカルサーバで e2e を
  始めたのに気づいた →「実サーバに配備して実施」へ即座に軌道修正）。
  `revise <id> [--title|--priority|--verify|--accept|--after|--note|--level|--track|--why|--desc|--scope|--out-of-scope|--constraints|--hints|--demo] [--feedback 指示]`
  でフィールドを置換（`''`/`none` で削除。`after` の循環は拒否）し、`--feedback` は次の act に
  必ず反映される。効き方はタスクの状態で変わる:
  - `ready`/`inbox`/`draft` … 即時反映（次の選択・実行から効く。依存 `after`・優先度の変更もすぐ効く）
  - `blocked`/`review` … 反映して ready に積み直す（needs 記入＋`[x]` と同じ復帰。needs は消える）
  - `doing`（実行中） … 反映を予約し、**現在の試行の結果は確定しない**（verify も done もせず
    修正内容とフィードバックで積み直す）。daemon/remote 実行なら結果待ちも打ち切って早く回す。
  watch のパス途中でも取り込まれる（後続タスクの実行前に効く）。決定記録（DR `action: revise`）と
  `- learn:`（feedback がある場合）を残す。
- **指示のファイルドロップ（commands/）**: CLI を実行できない環境（ビュアーが Windows・本体が WSL 内、など）
  向けに、同じ指示を `<root>/commands/<name>.json`
  （`{"command": "approve|hold|pin|defer|revise", "id": "<task-id>", "reason": "..."}`。revise は加えて
  `title/priority/verify/accept/after/note/level/track/why/desc/scope/out_of_scope/constraints/hints/demo/feedback`
  キーを受ける）のドロップでも渡せる。
  run/watch が拾って **CLI と同一のロジック・同一の DR** で実行し、処理したファイルは消す
  （壊れた JSON・未知の指示は `.err` に退避して journal に記録）。**読める指示は watch 中でも即座に
  取り込む**。`--debounce` は読めなかったファイル（書きかけ）だけの再試行猶予で、猶予後もダメなら
  `.err` へ退避する。読める指示を先送りすると、承認を処理しないまま再評価するパスが生まれ、
  承認直後にマイルストーンが復活する。
- **バックログ再分解の要求（`replan`・エラー回復）**: `{"command": "replan", "reason": "..."}`
  （**プロジェクト単位＝`id` 不要**）のドロップ、または CLI `agent-project replan --reason ...` で、
  charter からのバックログ再分解を **次パスに一発だけ**要求できる（`.replan.request` マーカーを立て、
  DR を残す）。通常の再分解は「消化可能タスクが無い」か「charter が変わった」ときに自動で走るが、
  plan 失敗・タスクの取りこぼし/誤削除・完了後のやり直しなどのエラー回復では **charter 無変更のまま**作り直したい。
  再分解の冪等照合は **done 以外**（現行処理中のバックログ＋却下済み）と行う: 処理中タスクの二重投入や
  却下済み（人の明示判断）の復活はさせず、`archive/`（done）と類似のタスクだけ**やり直しとして再作成を
  許可**する（過去の完了実績が回復のための再分解を弾かない）。charter が無い（backlog ループ）
  プロジェクトでは対象が無いため拒否。
- **自律裁定（needs の手前・既定 on）**: 人へ回す前に エージェント CLI が「ループ内で積み直して解けるか（requeue）／人が要るか
  （escalate）」を判断。requeue なら needs を作らず guidance を注入して再実行。例外・エージェント CLI 不在・意思決定/リスク絡みは
  必ず人へ。1 タスク `--adjudicate-max`（既定 1）回まで。`--no-auto-adjudicate` で無効化。
- **DR 学習（通知を減らす）**: 繰り返し NG で人へ回りそうな時、他案件の `learn` からタイトル類似（Jaccard ≥
  `--learn-threshold` 既定 0.5）の過去指示を探し、あれば blocked にせず反映して自動再実行（1 タスク 1 回）。
  > 順序は **DR 学習（決定的）→ 自律裁定（エージェント CLI）→ 人**の三段で人の判断を絞る。投入側では逆に
  > **予防リコール（決定的）**が過去 hold に似た案件を先回りで人へ回し、無駄な実行と手戻りを未然に防ぐ。
- **ltm 昇格（横断・LLM 不要）** `--ltm`: ある `learn` が `auto-resolve` で実際に効いた回数が `--promote-threshold`
  （既定 2）以上で `ltm-use` home（`$KIRO_LTM_HOME`→`~/.claude`）へ昇格。recall は「ローカル decisions → ltm home」の順で
  フォールバックし別プロジェクトでも効く。`promote` で手動昇格。

- **通知**: 人の対応待ちへの**遷移時だけ**要約を標準出力に出す（毎サイクルでは鳴らさない）。`--notify-cmd '<cmd>'` で
  teams-use / outlook-use / issue-mailbox 等へダイジェストをパイプできる。永続の対応窓口は `needs/<id>.md`。

```bash
agent-project needs                              # 何が判断待ち/検収待ちか
agent-project approve T12 --reason "テスト側を修正"
agent-project hold prod-deploy --reason "本番は手動"
# 実行中でも気づいた時点で軌道修正（現在の試行は確定せず、修正内容で積み直される）
agent-project revise e2e-test --feedback "ローカルサーバでなく実サーバに配備して e2e を実施すること"
agent-project revise deploy --after e2e-test --priority 5 --reason "e2e 完了後に回す"
```

## backlog の自走

- **取り込み口（enqueue / inbox）**: `enqueue` は CLI フラグ or stdin/JSON（1 件/配列）から投入。`<root>/inbox/` に
  置かれた `.json`/`.md` は run/watch が取り込み元ファイルを消す。**verify を持たない投入は必ず `inbox`**＝人の triage 行き。
  外部ソース（webhook/メール/issue 抽出）は薄いアダプタでここへ流し込む。
- **取り込みコマンド（intake_cmd）**: 外部の決定的ゲート/検出器を **watch の周期で pull** する汎用フック（push 型の
  inbox と対）。設定 `intake_cmd:`（CLI `--intake-cmd`）のコマンドをパス開始時と idle 中に `intake_interval`（既定
  600 秒・0 以下で毎回）で律速して実行し、stdout の enqueue --json 形式を**冪等に**取り込む（spec の `id` が現役
  backlog に居れば飛ばす＝同じ発見の重複投入を防ぐ）。exit≠0・非 JSON・タイムアウト
  （verify_timeout）は journal に残して無視（ループは殺さない）。**コマンドは単発・有界であること**（常駐はこちらが
  持つ）。例: `intake_cmd: codd-gate tasks --debt`（doc/code/test 一貫性の負債を修復タスク化して自動返済）。
  > 外部 CLI を差し込める公式の口（verify/acceptance・regression_cmd・intake_cmd・inbox/enqueue・
  > notify_cmd・executor）の契約は設計書 §4.1「外部 CLI の差し込み点」にカタログ化してある。
- **依存（DAG）** `- after: T1, T2`: 依存が done（archive へ退避）になるまで消化対象に入らない。依存が blocked/review で
  止まれば従属も待つ。
- **自己生成（followup）**: 完了タスクから派生を生む。静的（タスクの `- followup: <title> :: <verify>`）／動的（act 出力の
  `@followup …` 行）。verify があれば `ready`（同 run で自走）、無ければ `inbox`。`--max-spawn`（既定 20）で上限。
- **rot 検知**: 古い/重複/実行不能を triage で検出し人へ回す（消さず棚卸し）。`rot [--fix]` 単体実行 ／ `run --rot` で毎回。

```bash
agent-project enqueue --title "レポート生成を直す" --verify 'pytest -q tests/report'
echo '{"title":"X","verify":"make test","priority":5,"after":"T1"}' | agent-project enqueue --json
cp task.md ./inbox/
```

## 複数バージョンの並行開発（charters/）

1 プロジェクトで複数バージョン（v1 保守と v2 開発など）を並行管理するには、`charter.md` の代わりに
**`charters/<バージョン名>.md`** を並べる。`run --watch` が全 charter をラウンドロビンで
plan→execute→evaluate し、それぞれが独立の acceptance / milestone（`needs/<プロジェクト>-<名前>.md`）/
収束状態（project.json の `charters` マップ）を持つ。

- plan が投入するタスクには `charter: <名前>` タグが付き、再分解の重複排除・消化判定・評価は
  そのバージョンに閉じる（実行そのものは 1 つのバックログを共有）
- 特定バージョンだけ再分解するには `agent-project replan --charter <名前>`（viewer の replan も同様）
- 単一 `charter.md` は従来どおり動く（charters/ が無いときのフォールバック）

### マスター憲章（`## master`）

ルートの `charter.md` に **`## master` セクション**を書くと、その憲章は「プロジェクト全体の
普遍的な前提（マスター）」になり、**それ自体はバックログへ分解されない**。やるべきことは
`charters/<名前>.md`（計画バージョン）に書き、そこからタスクが作られる。

- バージョンはマスターを**継承**する: goal / deliverables / acceptance はバージョン側が優先
  （空ならマスターの値を使う）、constraints / assumptions / links / repos はマスター∪バージョン
- マスターを編集すると継承合成後の内容が変わるため、各バージョンの再計画・accepted 再開の
  判定にもマスター編集が効く
- バージョンが 1 つも無い間は分解対象なし＝backlog 消化と指示の取り込みだけが回り、
  `charters/<名前>.md` が置かれた時点で charter 駆動が始まる（`run --watch` が検知する）

## 目標駆動（charter）— `run` の charter モード（長期改善ループ）

backlog の上に、人が書く**目標（charter）**から逆算する evaluator-optimizer のもう一段。backlog を消化して
`drained` で止まる正準ループに対し、「**枯渇**」と「**目標達成**」を分離して長期に回す。**プロセスは `run` に一本化**され、
`<root>/charter.md` があれば `run` が自動でこの三相に入る（専用 `project` コマンドは廃止）。

```
charter.md（goal / constraints / assumptions / deliverables / acceptance=受入 verify ／ 任意 links）
   ① plan     charter をエージェントに分解させ enqueue（冪等。verify 必須）
              ＊消化可能タスクが無いとき、または charter の分解内容が前回計画時から変わったときに起こす
                （内容署名で検知。編集すれば消化可能タスクがあっても差分を再計画。既存/archive と冪等重複排除）
   ② execute  既存の正準ループ run を drained まで回す（検収/回帰/protect/予算は全て温存）
   ③ evaluate acceptance 全 PASS か判定（＋opt-in 敵対的レビュー --review-project）
        未達/指摘 → 改善タスクを生成して次サイクル（未達 acceptance はそれ自体を verify とする）
        全 PASS かつ改善ゼロ → milestone gate（needs/<project>.md）で人へ
```

- **done の唯一の根拠は `acceptance`（=verify）全 PASS**（タスク verify と同じ鉄則）。acceptance 無しの charter は
  done 判定不能＝必ず人へ。検証コマンドを書けない条件は **自然文でも可**（`- accept: …` か散文の箇条書き）。run 時に
  エージェントが決定的なシェル verify へ合成し（結果は安定キャッシュ＝done 基準がブレない）、合成できなければ人へ。
- **acceptance の実行先**: 既定は workdir だが、offload で worker が対象 repo を temp に clone・push して消すと workdir に
  成果が出ない。実行先は **明示 `--verify-cwd`（設定 `verify_cwd`）> 単一対象 repo の一時 clone（charter の非 readonly repo が
  1 つなら target ブランチを毎評価で `git clone --depth 1`）> workdir** の順で解決。clone 失敗は全 NG 扱い（成果の無い場所で
  偽判定しない）。複数 repo は曖昧なので自動 clone せず `--verify-cwd` で指定。
  **有限停止**: 内側 run ＋ `--max-project-cycles`（既定 5）/`--max-project-cost`/
  `--project-stall`（PASS 数が増えない連続回数で人へ・既定 2）。**知能は委譲**し enqueue・acceptance・収束は決定的。
- **収束候補は人へ**: `approve <project> --reason …` で完了確定（最終納品書）／charter を更新して次フェーズへ続行／
  policy・feedback で方向修正。`--watch` は milestone 提示後も常駐し charter 更新を待つ。状態は `<root>/project.json`、
  各評価は `decisions/` に `project-evaluate` で監査記録。
- **ワーカーへの定義/判断の注入**: agent-flow への act 依頼に **charter（定義）と `decisions/<id>.md`（判断結果）**を有界に
  注入（charter 1400 字・decisions 末尾 1000 字）。charter.md があれば全 act に乗る（無ければ空＝後方互換）。`## links` 先
  プロジェクトの定義＋判断（learn）も横展開で取り込む。
- **ワークスペース・ルーティング（repos レジストリの `owns:` ＋ policy `route:`）**: リポジトリ定義は
  独立スキーマ（`schemas/repos.schema.json`）で管理する。手書きの `<root>/repos.{yaml,yml,json}` が
  あれば**それがレジストリの正**（charter の `## repos` は互換入力で、内部的には同じ形に正規化して
  引き回す。charter 無しの backlog 消化でもルーティングに効く）。手書きが無ければ **charter から
  repos.json を自動生成**して外部ツール（codd-gate の `--repos` 等）へ渡す（`_meta` マーカー付き・
  正は charter のまま追従。手で管理したくなったら `_meta` を消す）。以下の `## repos` の説明は
  レジストリの内容の説明としてそのまま当てはまる。大規模・複数リポジトリ運用で「どのタスクを
  どのリポジトリへコミットするか」を**制御層（agent-project）が1つに決め**、agent-flow へ `--workspace`（唯一の書込先）として
  渡す。charter の `## repos` を repo レジストリとし、各 repo に `- owns:`（担当パスのグロブ）を付けると**書込先候補
  （ワークスペース）**になる。**owns を書かない repo は参照リポジトリ（読むだけ）**で、書込先にはせず agent-flow へ
  `--reference` で構造化伝搬する（clone しない。エージェントのプロンプトと gitlab イシューの参照節に描画される）。
  1 タスク（=1 agent-flow run）が書き込むのはちょうど 1 リポジトリ。複数 repo にまたがる変更は repo 別タスクへ
  分割し `after` で順序付ける。
  - **解決順（上が優先・決定はタスク md の `- workspace:`/`- routed_by:` に書き戻して安定/監査可能）**:
    1. タスクの `- workspace: <name>`（明示）  2. `policy.md` の `route: <パターン> -> <name>`（決定論）
    3. `owns:` のパスグロブ × タスクの `- paths:` ヒント（決定論推定）  4. auto-route（`route_planner: agent` のとき LLM が
    desc/owns から1つ推定）  5. `default_workspace` 設定 / 書込先候補が1つだけならそれ。
  - **リポジトリの同一性は (url, path, base)**：モノレポは「同じ url で path と owns を変えた複数エントリ」でフォルダ別の
    ワークスペースに、ブランチ別は base を変えて区別する。`path`/`base`/`target`/`desc` は構造化 `--workspace`（JSON）として
    agent-flow へ伝搬し、worker は `af/<run-id>` ブランチを base から作って作業、変更があれば agent-flow が commit/push する。
  - **verify の実行先もワークスペースに従う**: `- workspace:` を持つタスクは成果が workdir（git-bus ルート）でなく該当 repo の
    作業ブランチへ push されるため、verify/回帰を workdir で回すと「成果の無い場所」で偽 NG になる。そこで verify は**該当 repo を
    指定ブランチ（`target`→`base`）で取得し、`path` 指定があればそれをルートに**したクローン内で実行する
    （差分基準 `$KIRO_BASE_REV` はクローンの HEAD に取り直す）。取得は **URL 単位のホスト共有 bare ミラー
    （`--mirror --filter=blob:none`）から detached worktree を生やす**方式で、毎回 fetch してから最新で worktree を作るので
    都度 clone と鮮度は同等のまま GitLab の pack 生成負荷を抑える（ミラー root は `KIRO_GIT_CACHE_DIR`、既定
    `$TMPDIR/kiro-git-cache`、agent-flow と共有。詳細は
    [docs/designs/git-worktree-cache-pattern.md](../../docs/designs/git-worktree-cache-pattern.md)）。ミラーが使えなければ
    従来の `git clone --depth 1` に自動フォールバック。取得失敗・`path` 不在は黙って workdir に倒さず NG 扱い（成果の無い場所で
    偽判定しない）。明示 `--verify-cwd`（設定 `verify_cwd`）は常に最優先。
  - gitlab executor 経由なら**起票先プロジェクトをワークスペース URL から解決**し、フォルダ・作業ブランチ・参照リポジトリが
    イシュー本文に構造的に表現される。
- **cohort（pilot-then-batch）**: 「同じ手順を多数の対象に繰り返す」タスクを、**まず 1 件だけ走らせて指示を固めてから残りを
  生成・実行**する。`cohort_items` を持つ spec を投入すると、先頭要素が **pilot** として `review: human` 付きで 1 件だけ作られ、
  verify→検収ゲートで人が `approve`（必要なら feedback）して指示を固める。承認時にその定義を元に**残りのタスクを生成**し、
  各メンバには固めた指示（承認理由＋feedback）が `feedback` として乗って act に必ず反映される。`title`/`verify` 中の `{item}` に
  各対象が差し込まれる。状態は `cohorts/<id>.json`。**実行は act 非依存**＝残りは通常ループが任意の location（local/daemon/remote）
  で消化する。charter のプランナーも「繰り返しタスクは `cohort_items` でまとめよ」と指示され、分解から自然に cohort を作れる。
  手積みは `enqueue --title "{item} を移行" --verify "test -f {item}" --cohort-items a,b,c`。
  （人を介さない自動版＝「1件先行→自動検証→残り展開」は agent-flow の `exemplar_first` が担う。）
  選択肢としての when_to_use / when_not_to_use / 例示 / 適用具体例は flow-planner カタログの
  `variants.pilot-then-batch`（`.github/skills/flow-planner/patterns-catalog.yaml`）にまとめてある。

```bash
agent-project run                          # charter があれば plan→execute→evaluate（収束で人へ）
agent-project run --watch                  # 目標を満たすまで回り続ける常駐（charter 更新も待つ）
agent-project run --review-project         # acceptance 全 PASS でも短絡的達成を疑う
agent-project approve <project> --reason "受領"   # 完了確定（最終納品書）／続行は charter を更新して再実行
```

### 横展開リンク（charter.md の `## links`）

```markdown
## links
- ../shared-conventions   # 兄弟ディレクトリ（root の親からの相対でも解決される）
- /srv/projects/infra-rules   # 絶対パスも可
```

リンク先の定義（goal/constraints）と判断（decisions の `- learn:`）を act ワーカー文脈に取り込む（横断 recall・有界・
1 階層）。ltm-use（実績で自動昇格）に対し、charter リンクは**人が明示した参照先**を確実に引く。

## 複数プロジェクト（1 プロジェクト = 1 ディレクトリ = 1 プロセス）

複数プロジェクトはプロジェクトルートを並べて、それぞれで daemon を回す。needs/decisions/policy/journal/
検収ゲート/自律裁定/DR 学習は**そのルート内に閉じる**（別プロジェクトの判断が混ざらない）。束ねた可視化・
操作（検収・指示・停止/再開）は [agent-dashboard](../agent-dashboard/) が各ルートの clone を
登録して git 越しに行う。

```bash
cd ~/projects/payments && agent-project start        # payments プロジェクトの常駐
cd ~/projects/webapp   && agent-project start        # webapp プロジェクトの常駐（別プロセス）
agent-project instances                          # 稼働中の全プロジェクト root を横断一覧
```

別プロジェクトの定義・判断を参照したいときは charter の `## links` にパス（兄弟ディレクトリ名や
相対/絶対パス）を書く（[横展開リンク](#横展開リンクchartermd-の--links)）。

## 状態の git 保存・共有 — リモートの viewer と結果/指示を往復する

ワークの内容（プロジェクトルート直下の状態＝backlog / needs / decisions / journal / DELIVERY / run-log …）を
**共有 git リポジトリへ双方向同期**する。リモートサーバで回している agent-project の結果を手元の
[agent-dashboard](../agent-dashboard/) で眺め、viewer からの指示（承認・フィードバック・タスク投入・
一時停止/停止）をサーバへ届ける、を git だけで往復できる。

### direct モード（推奨・設定不要）

**プロジェクトルート自体を共有リポジトリの clone にする**と、agent-project はそのリポジトリの
ブランチへ state コミットを積んで push し、viewer 側の commit（指示・検収）を取り込む。管理クローンは
作らない。ルートのチェックアウトには触れない: コミットは detached worktree（専用 index）で組み立てて
update-ref の CAS でブランチを進めるため、人の `git add`/`git commit` と衝突しない。

```bash
git clone git@example.com:team/proj-state.git ~/projects/proj
cd ~/projects/proj && vi charter.md && agent-project start
```

- リモートの取り込みは fetch → ff-only 優先・分岐時のみ rebase（--autostash 不使用＝未コミット変更と
  衝突するなら見送る）。push 競合は fetch + rebase → 再 push の指数バックオフで吸収し、**force push はしない**。
- `journal.md` は `.git/info/attributes` に `merge=union` を自動宣言（冪等・リポジトリローカル）。
  複数ホスト/viewer が同時に追記しても rebase で EOF 衝突せず、両方の行が残る。
- 同期対象はルート直下の状態のみ。一時状態（`bus/`・`claims/`）とドット始まりは同期しない。
- fetch/push は `state_git_interval`（既定 300 秒）で律速。push は共有すべきコミットがあるときだけ
  （run のパス直後は間隔を待たずに押し出す）。
- リモート（origin）が無いローカルだけの git リポジトリでも、コミット履歴として状態が残る（push はスキップ）。

### 管理クローン方式（ルートが git でない場合のフォールバック）

ルートを git にできない構成では、従来どおり `state_git` を設定すると専用の管理クローン
（`<root>/.state-git`。`state_git_subdir` だけの sparse-checkout・`--filter=blob:none`）で鏡写しする。

```yaml
# .agents/agent-project.yaml（サーバ側）
state_git: git@example.com:team/agent-state.git   # 共有リポジトリ（URL/パス）
state_git_subdir: agent-project                   # リポジトリ内の保存先（名前空間）
state_git_interval: 300                          # fetch/push の最短間隔（秒）
```

- **リモートサーバに負荷をかけない**: fetch/push は間隔で律速。idle 中は間隔ごとの pull 1 本に収まる。
- **他のプログラムが同一リポジトリにコミットしてよい**: ステージは自分の `state_git_subdir` 配下のみ、
  push 競合は `pull --rebase` → 再 push で吸収し、**force push は決してしない**。
- **双方向で、衝突は決定的に裁定**: 前回同期スナップショット（manifest）基準の 3-way で「どちらが変えたか」を
  判定して橋渡しする。同時変更だけを **人の入力パス（`commands/`・`inbox/`・`needs/`・`policy.md`・
  `charter.md`・`repos.{json,yaml,yml}`）はリモート優先／機械状態（backlog・journal・decisions …）は
  ローカル優先**の規則で決める。

同期は run のパス開始（指示の取り込み）・パス終了（結果の押し出し）・watch の idle（間隔律速の pull）で走る。
ネットワーク断・リポジトリ不通でも**ループは殺さず** journal に残して続行する（done の確定・消化は同期に
一切依存しない）。

**viewer 側（別マシン）の組み方**:

1. 状態リポジトリを clone する
2. viewer の ⚙ 設定「プロジェクトのパス」にその clone を登録する（複数プロジェクト = 複数 clone を 1 行ずつ）
3. viewer の操作（needs 記入・commands/ ドロップ・inbox/ 投入）はファイルとして書かれ commit/push される →
   サーバ側の agent-project が idle の pull で取り込み、watch が次パスを起こす

`coordination` 未設定では、従来どおり `claims/` は同一ホスト/共有FSだけの排他であり、Git越しの
多重実行防止にはならない。複数PCが同じbacklogを直接消化する場合だけ、次の `git-cas` モードを明示する。

### 複数PCで1プロジェクトを分担（Git CAS）

全PCで同じ状態リポジトリを通常cloneし、共有 `agent-project.yaml` に以下を置く。

```yaml
coordination: git-cas
controller_heartbeat_sec: 30
controller_lease_sec: 120
coordination_retries: 3
```

PC固有値は共有設定や環境変数へ置かず、各PCのローカルprofileへ置く。サンプル:
[`agent-project.profile.yaml.example`](agent-project.profile.yaml.example)。

```bash
agent-project doctor --profile /home/me/.agents/agent-project/profiles/proj.yaml
agent-project start --profile /home/me/.agents/agent-project/profiles/proj.yaml
agent-project stop --profile /home/me/.agents/agent-project/profiles/proj.yaml --drain --deadline 300
```

- controllerはremote HEADへのfast-forward CASで1ノードだけが保持し、停止・drain・lease失効後は別ノードが自動取得する。
- controllerだけがcharter計画・inbox/commands/feedback・triage・自動割当を行う。workerは割当済みtaskだけを実行する。
- taskは `ready → doing` のCAS時に `claim_owner/token/generation` を確定する。古いtokenの結果は採用しない。
- 未割当readyはactiveノードのready+doing件数が最小になるよう配る。同数はnode名順。手動割当とdoingは動かさない。
- `daily_stop - drain_before_sec` で新規claimを止めcontrollerを解放する。異常停止したdoingは自動盗取せずblockedへ隔離する。
- Gitが取得不能ならcontroller取得・新規claimはfail closedする。`doctor` はnode、origin、heartbeat/leaseを検査する。
- `run-log/<node>/<run-id>.json` は不変レコード、`DELIVERY.md` はarchive集合から再構築可能。

**実行層 agent-flow のバス（run）も同じリポジトリへ**: agent-project に agent-flow daemon を管理させる
（`manage_flow_daemon: true`）と、「このバス（`<root>/bus`）を状態リポジトリの `agent-flow` 名前空間へ
鏡写しせよ」という routing（`--state-git` の remote/branch/interval。direct モードならルートの origin）を
daemon 起動時に注入する。agent-flow の設定値（executor / state_git_subdir / gitlab.* / defer_waits 等）は
`flow_config` で渡す agent-flow.yaml に集約する。agent-project を止めても daemon は detached で残り、
in-flight run（gitlab の長期委譲・夜間停止からの孤児再開）は daemon 側でそのまま継続する。daemon 不在の
バスは `agent-project doctor` が warn で知らせる。

## リモート操作（commands/ のライフサイクル指示）

viewer（または任意の外部ツール）は `commands/<name>.json` のドロップ → git push だけで、
タスク単位の指示（approve/hold/pin/defer/revise）に加えて**プロジェクト単位のライフサイクル操作**ができる:

| 指示 | 効果 |
|------|------|
| `{"command": "pause", "reason": "..."}` | watch の消化を一時停止（`paused.json` を生成。idle 監視・指示の取り込みは継続し、status.json に `paused: true` が載る） |
| `{"command": "resume"}` | 一時停止を解除して消化を再開 |
| `{"command": "stop"}` | プロセスを graceful 停止（停止前に状態を push。再開は本体側で `agent-project start`） |
| `{"command": "replan"}` | charter からのバックログ再分解を次パスに要求（エラー回復） |

pause 中も commands/ は取り込まれるため、リモートから resume / stop を届けられる。

### daemon の生存信号（status.json）— リモート viewer の稼働判定

リモート（別ホスト・state_git 越し）の viewer からは、本体のローカル生存レジストリ
（`~/.agent-project/instances/`）が見えないため、従来「稼働中」バッジが出せなかった。
`<root>/status.json` に最小の生存スナップショット（`watch` / `level` / `updated_iso` /
`fresh_after_sec`）を書き、これも state_git で同期することで、リモートの viewer が
「同期経由の推定」として稼働判定・最終確認時刻を出せるようにしている。

```json
{"host": "myserver", "watch": true, "level": "unattended",
 "updated_iso": "2026-07-05T21:03:11", "fresh_after_sec": 600}
```

- **idle 中の追加コミットはデフォルトで発生しない**: `write_status` は実パス（backlog 等の実データが
  変わり得たタイミング）完了時にのみ呼ばれ、その他ファイルの変更と**同じコミットに相乗り**する
  （state_git の「差分があれば commit」に任せる。単体では何も追加しない）。watch の idle 中は
  `--status-interval`（既定 `0`＝無効）を明示指定しない限り status.json に一切触れない。
- **`--status-interval N`**（任意）: idle 中も N 秒間隔で status.json だけを更新し、実パスが
  長時間発生しない場合でも viewer 側で「生きている」ことを近い間隔で確認できるようにする。
  この間だけ state_git の追加コミットが増える（負荷とリモートでの鮮度のトレードオフ）。
  例: `--state-git-interval 300 --status-interval 3600` なら、実際の作業が無くても
  1 時間おきに 1 コミットだけ増える。
- `fresh_after_sec` は本体が自分の同期間隔（`state_git_interval` と `status_interval` の大きい方の
  2 倍・下限 120 秒）から計算して埋め込むため、**viewer 側は単純な経過時間比較だけで済む**
  （同期間隔を変えても viewer 側の調整は不要）。
- 実データ（backlog / needs / decisions / run-log 等）は既に state_git で同期されているため、
  status.json はそれらを重複させない（生存信号だけの最小ファイル）。

## 常駐運用（watch / lifecycle / 発見 / OS 自動起動）

- **watch**: 1 パスが終わってもプロセスを残し backlog を監視。idle 中は エージェント CLI/agent-flow を起動せず（安価な FS
  ポーリングのみ）、`--poll` 間隔で「消化可能タスク or 新規 inbox or フィードバック」を検知して次パスを起こす。
  予算は 1 パス毎に与え直す。サブコマンド省略（`agent-project`）は `run --watch` と同義（cwd のプロジェクトを常駐監視）。
- **lifecycle（start / stop / restart）**: 常駐の明示操作。`start` は cwd（または `--root`/設定/profile の root）の
  プロジェクトの `run --watch` を detached 起動（ログは `~/.agent-project/logs/`・重複監視は拒否・`--force`）。
  `stop` は graceful（SIGTERM→居残りのみ SIGKILL・自分は止めない）。`stop --drain --deadline 300` は
  新規claimを止め、実行中taskを期限まで待ってから停止する。実行時設定は設定ファイルに寄せる思想で
  `start` は個別 run フラグを取らない。リモートからの停止/一時停止は commands/ の
  [ライフサイクル指示](#リモート操作commands-のライフサイクル指示)を使う。
- **稼働発見（instances）**: `run` 中は監視中の root と OS/WSL 情報を `~/.agent-project/instances/` に登録し
  終了で消す。`instances [--json]` で外部操作者（スキル）が「いまどのプロジェクト root を見ているか」を発見し、WSL/Windows を
  またいで読み書きできる（`runtime`/`wsl_distro`/`root_windows` を best-effort 併記）。**別ホスト発見**は共有レジストリ
  （`--registry`/`AGENT_PROJECT_REGISTRY`・NFS/同期/git）へも書き、自ホスト=PID・別ホスト=heartbeat 鮮度で生死判定。

```bash
agent-project start                          # cwd のプロジェクトを detached 常駐起動
agent-project start --profile /abs/proj.yaml # PC固有profileで起動（AGENT_PROJECT_* を参照しない）
agent-project instances                      # 稼働中の全プロジェクト root を横断一覧
agent-project stop                           # cwd のプロジェクトを停止（--root / --pid / --all も可）
```

**OS 自動起動（Linux systemd ユーザーユニット）** — `~/.config/systemd/user/agent-project-<name>.service`:

```ini
[Service]
ExecStart=%h/.local/bin/agent-project run --watch --profile %h/.agents/agent-project/profiles/proj.yaml
ExecStop=%h/.local/bin/agent-project stop --profile %h/.agents/agent-project/profiles/proj.yaml --drain --deadline 300
TimeoutStopSec=330
Restart=on-failure
```
```bash
systemctl --user enable --now agent-project-proj   # 起動＋ログイン時自動起動／ loginctl enable-linger "$USER"
```
macOS は launchd（`ProgramArguments` に `run --watch --profile /絶対パス`・`RunAtLoad`/`KeepAlive`）、
Windows はタスクスケジューラの「ログオン時」トリガで同等に登録する。

夜間停止時刻そのものはOS側のshutdown/sleepスケジュールで管理する。agent-projectはprofileの
`daily_stop` と `drain_before_sec` から先行drainするが、PCの電源管理は行わない。

## 設定ファイル

共有値を `agent-project.{yaml,yml,json}`、PC固有値をprofileに書ける
（**CLI > ローカルprofile > 共有設定 > 既定**）。profileでは `AGENT_PROJECT_*` を参照しない。
探索順: `--config` 明示 → `./.agents/` → `~/.agents/`。YAML は PyYAML 任意・無ければ JSON フォールバック。サンプルは
[`agent-project.yaml.example`](agent-project.yaml.example)（実運用の組み方＝WSL 常駐＋gitlab executor 分散＋
viewer 監視＋GitLab バックアップは [`agent-project.state-git.yaml.example`](agent-project.state-git.yaml.example)）。
スカラ＋真偽フラグ（三値 `--flag`/`--no-flag`）が対象で、
個別パス上書き（`--backlog` 等）・実行限定フラグ（`--json`/`--fix`/`--pin`）は CLI 専用。

## 計測（stats / runlog）

```bash
agent-project stats [--json]     # 完了/納品/未消化/人対応待ち・自動化率・一発done率・累計コスト
agent-project runlog [--json --tail N]   # run 毎1行 JSON（reason/done/escalations/tokens/cost/duration）
```
`stats` は archive/decisions/DELIVERY/backlog から決定的に集計（**自動化率**=auto-resolve＋auto-adjudicate÷自動＋人、
**一発 done**=retry 0、コストは納品書 `- cost:` の累計で予算と突合）。`run-log.jsonl` は監視/スプレッドシートに流せる。

## 稼働診断（doctor）

```bash
agent-project doctor [--json]     # ログ/状態/環境から稼働を診断（既定は診断のみ・無害）
agent-project doctor --fix        # env/config を修正し、program の不具合を gitlab-idd で起票
```

`doctor` は **収集と適用を決定的に・診断と分類は エージェント CLI へ委譲** して稼働の問題を洗い出し、原因を 3 つに分類する。

- **env**（ユーザー環境固有）… エージェント CLI（既定 `kiro-cli`）/`agent-flow`/`git` の不在・PATH・workdir が git でない等。
- **config**（設定）… verify 欠落・コスト予算未設定・保護パス未設定・必須ディレクトリ未作成等（`audit` の未達も取り込む）。
- **program**（プログラム上の不具合）… 正しい環境・設定でも再現する不具合。**コード修正が必要なものだけ**。

材料は決定的チェック（依存コマンド・ディレクトリ・`audit` 結果）＋稼働シグナル（`stats`/`run-log`/`journal` 末尾/`needs`/
blocked タスク）。これを エージェント CLI に渡して分類済みの所見を得る（エージェント CLI 不在・解析不能なら**決定的チェックのみ**で続行）。

`--fix` のとき:
- **env/config** … 既知の修正アクションを適用（`create-dirs`＝backlog/needs/decisions 作成、`policy-protect`＝policy.md に
  既定の保護デニーリストを追記）。判断が要るもの（コスト予算・git 初期化等）は提案表示のみ。
- **program** … `gitlab-idd` スキルのリクエスター役（エージェント CLI 委譲）で **GitLab イシューを起票**。
  **スキルが見つからなければ起票せず出力のみ**（`$KIRO_SKILLS_HOME` → cwd 上方向の `.github/skills` → `~/.claude/skills` の順で探索）。

**実行層 agent-flow との連携**（`--with-flow`・既定 on／`--no-flow` で本体のみ）: 内側＝act の実体である `agent-flow doctor --json` を
同じバスに対して呼び、その所見を `[flow]` 印で統合する。`--fix` のときは agent-flow 側にも `--fix` を委譲し、**agent-flow が自分の
env/config 修正と program 起票を担う**（本体は agent-flow 由来の所見を再修正・再起票しない＝二重作業を避ける）。agent-flow が不在・
タイムアウト・解析不能なら無害にスキップする。

終了コード: `0`=健康（所見なし）／`1`=未解決の所見あり／`2`=未解決の critical あり。`--fix` 無しは常に診断のみ（既定）。

## 自動アップデート（既定 on）

スキルリポジトリ（このツールの配布元）の **main ブランチに更新が入ったら、`run --watch` のアイドル時に自動で取り込む**。
**既定で有効**（6 時間ごと。前回チェック時刻は `~/.agents/agent-project.update.json` に持続化され、
**再起動を跨いで間隔が尊重される**——前回から間隔ぶん経っていれば起動後の最初のアイドルで実施する）。
止めたいときは `update_enabled: false` か `update_check_interval: 0`。手順は doctor と同じ流儀で**決定的**——
知能は使わず、ファイル操作だけで完結する。

1. `git ls-remote` でスキルリポジトリ main の先頭コミットを確認する
2. 適用済み SHA（`~/.agents/agent-project.update.json`）と違えば「更新候補」
3. **アイドル時（消化待ち/フィードバックが無いとき）だけ**、temp 領域へ `tools/agent-project/` だけを **sparse-checkout**（無関係ファイルは取得しない）
4. **取得した本体の内容ダイジェストが前回適用時と同一なら適用せず、ベースライン SHA だけ進める**。
   direct state-git 構成では自分の state sync push でリポジトリの SHA が進むため、SHA だけで
   判定すると「自分の push → 更新検出 → 再起動 → また push」の自己増殖ループになる
5. その中の `install.sh` を実行して `~/.local/bin` の本体を更新する
6. **動いていたカレントディレクトリのまま** `os.execv` で新しい本体へ **graceful 再起動**する（レジストリ登録は再起動前に後始末）

再起動後の watch は **plan/act を始める前に状態 git を 1 回取り込む**（charter 駆動も同様）。
停止していた間に viewer が push した charter 更新・コマンド・フィードバックを、古いローカル状態のまま
計画してしまう前に反映するため（`run_loop` 入口の同期は plan の後になるので、再起動直後だけ先んじて import する）。

**更新元 URL は通常は設定不要**。`install.py` がインストール時に生成する `skill-registry.json`
（`~/.kiro` / `~/.claude` / `~/.copilot` / `~/.codex` のいずれか）の `repositories.origin.url`
（無ければ `install_dir` のローカルクローン）から自動解決する。別リポジトリを使うときだけ `update_repo` を明示する。

```bash
agent-project update --check    # 更新の有無だけ表示（取り込まない）
agent-project update --now      # 更新があれば install.sh を実行して再起動
```

設定ファイル（`~/.agents/agent-project.yaml`）で調整できる（すべて任意。**既定のままで有効**）。

```yaml
update_enabled: true                  # 自動アップデートの ON/OFF（false で完全に止める。既定 on）
update_check_interval: 21600          # 更新チェック間隔（秒）。既定 6 時間。0 以下で自動チェック無効
update_repo: ""                       # 空なら skill-registry.json から自動解決。別 repo を使うときだけ指定
update_branch: main                   # 追従するブランチ（空/既定なら registry の branch を採用）
update_subdir: tools/agent-project  # リポジトリ内のこのツールのサブディレクトリ
update_installer: install.sh          # サブディレクトリ内で実行するインストーラ
```

> 初回チェックは「いま動いている本体が最新」とみなし、その時点の SHA をベースラインとして記録するだけ
> （更新はしない）。以降、main がそこから進んだときに更新を検出する。タスク実行中は何もしない。

## CLI 一覧

| コマンド | 役割 |
|----------|------|
| （省略）/ `run` [`--watch`] | 正準ループ（省略時は `run --watch`）。**charter.md があれば自動で目標駆動** |
| `triage` / `needs` / `rot` [`--fix`] | 優先順位付けのみ / 判断待ち表示 / rot 検出 |
| `enqueue` [`--title --verify\|--accept\|--verify-template …`\|`--json`] | 取り込み口 |
| `approve <id>` / `hold <id>` / `reprioritize <id> --pin\|--defer` | 決定記録を残す人の操作 |
| `reject <id> --reason` | 却下（廃止・依存先を再審査へ・charter があれば再計画要求） |
| `impact <id>` [`--json`] | 依存関係（前提／依存先・推移）の一覧 |
| `stats` / `runlog` / `audit` [`--strict`] | 計測 / 構造化ログ / Loop Readiness 採点 |
| `doctor` [`--fix --json`] | 稼働診断（エージェント CLI）。env/config は修正・program は gitlab-idd で起票 |
| `update` [`--check --now`] | スキルリポジトリ(main)の更新を確認・取り込み再起動（[自動アップデート](#自動アップデートopt-in)） |
| `promote` | 効いた学習を ltm-use へ昇格（手動） |
| `instances` [`--json --registry`] | 稼働中プロジェクトを横断一覧 |
| `start` / `stop` / `restart` [`--root --force`／`--pid --all`] | 常駐の起動/停止/再起動 |

主なフラグ（抜粋）: `--root` `--planner{agent,none}` `--flow-planner` `--location{auto,local,daemon,remote}`
`--executor{agent,stub}` `--level` `--auto-level[-max]` `--max-cycles/-seconds/-tokens/-cost` `--throttle` `--pace`
`--concurrency` `--verify-confirm` `--require-progress` `--regression-cmd[-revert]` `--intake-cmd[-interval]`
`--auto-adjudicate` `--learn[-threshold]` `--learn-capture` `--intake-recall`
`--ltm[-home]` `--promote-threshold` `--rot[-age-days]` `--max-spawn` `--watch` `--poll` `--debounce` `--notify-cmd`
`--git-bus/-branch/-subdir` `--state-git[-branch/-subdir/-interval]` `--charter` `--review-project`
`--max-project-cycles/-cost` `--project-stall` `--dry-run` `--once`。

## テスト

```bash
AGENT_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/agent-project/tests
```
agent-flow/エージェント CLI を呼ばずに検証（stub・act 注入）。優先順位/検証ゲート/積み直し/収束/location/pace/フィードバック往復/
watch/決定記録/コスト予算/followup・依存/回帰・パス保護/自己監査/自律度/原子的クレーム/run-log・throttle/flake/偽 done/
プロジェクト層/charter リンク/状態 git 同期（direct・管理クローン）/pause・resume・stop を網羅。agent-flow stub 統合は無ければ skip。
