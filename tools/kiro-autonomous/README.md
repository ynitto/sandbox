# kiro-autonomous

**バックログを自律的に優先順位付け・実行・検証・収束させ、人の判断が要る分だけ差し戻す制御層。**
最優先タスクを kiro-flow に実行させ、**`verify` をローカルで実行して PASS したものだけ done に確定**
（`archive/` へ退避）、NG なら積み直す。backlog が尽きるか予算が尽きるまで繰り返し、人の判断は案件毎の
`needs/<id>.md`（フィードバック欄つき）で差し出し、判断は `decisions/<id>.md` に残す。

> - 設計の正典: [`docs/designs/kiro-autonomous-design.md`](../../docs/designs/kiro-autonomous-design.md)（統合設計書。本書は運用リファレンス）
> - 熟練度別の導入手順: [`GUIDE.md`](GUIDE.md)（L0 下見 → L1 試運転 → L2 日常運用 → L3 無人運用 → L4 スケール）
> - タスク書式の正典: [`backlog.md.example`](backlog.md.example) ／ プロジェクト憲章: [`charter.md.example`](charter.md.example)
> - `kiro-` 接頭辞は実行を kiro-flow（＝kiro-cli）に委譲することを表す。

## 全体像

役割の異なる 3 層で動く。**構成は「プロジェクト > バックログ」**で、`<root>/projects/<name>/` に
1 プロジェクト＝1 セットを集約し、複数プロジェクトを併存できる。

| 層 | 担当 | 実体 |
|----|------|------|
| 上位（目標駆動） | 目標(charter)→backlog 生成 / 達成評価 / 改善サイクル | `run`（charter あり） |
| 外側（制御） | 優先順位付け / 検証ゲート / 積み直し / 収束 / 決定記録 / 安全ゲート | `run`（charter 無し） |
| 内側（実行） | タスクの分解 → act → 内側 verify ループ | `kiro-flow run`（別ツール） |

> **プロセスは `run` に一本化**。`<project>/charter.md` があれば `run` が自動で目標駆動（plan→execute→evaluate）に入る。
> charter 無しは従来の backlog 消化ループ。`--watch` がそのまま「目標を満たすまで回り続ける常駐」になる。

**正準ループ（5 点）**:

1. `backlog/<id>.md` を読み優先順位をつけ、最優先を kiro-flow に投げる。
2. 優先順位付けは `--planner kiro`（エージェントが `priority` も加味）/ `none`（priority 降順→最古）。人は `policy.md` で上書きできる。
3. kiro-flow の結果を verify ゲートで検証。done は `archive/` へ退避、NG なら積み直す。
4. backlog が尽きるか予算（サイクル/実時間/コスト）が尽きるまで反復（`--watch` なら尽きても監視を続ける）。
5. 人の判断・フィードバックは案件毎 `decisions/<id>.md` に保存する。

> **鉄則**: done は **verify の終了コード 0 のみ**が根拠（自己申告 done の禁止）。必ず有限回で止まる。
> 人の `policy.md` ＞ エージェント提案。本体は標準ライブラリのみ・決定的（知能は kiro-flow / kiro-cli へ委譲）。

## 依存・インストール

- `python3`（標準ライブラリのみ。pip 依存なし）
- `kiro-flow`（act の委譲先。PATH か `tools/kiro-flow/kiro-flow.py` を自動解決。`--dry-run` なら不要）
- `kiro-cli`（`--planner kiro`＝既定の優先順位付け／`--executor kiro` 用。`--planner none` なら不要）

```bash
bash tools/kiro-autonomous/install.sh           # ~/.local/bin/kiro-autonomous
```
未インストールでも `python3 tools/kiro-autonomous/kiro-autonomous.py ...` で代用可。

## クイックスタート

```bash
# default プロジェクトへ積む（<root>/projects/default/backlog に作られる。無ければ作成）
kiro-autonomous enqueue --title "README に概要見出しを追加" --verify 'grep -q "## 概要" README.md'
kiro-autonomous run --executor kiro                    # 自律消化（default プロジェクト）

# 別プロジェクトへ積む / そのプロジェクトを回す（複数併存可）
kiro-autonomous enqueue --project payments --title "…" --verify '…'
kiro-autonomous run     --project payments --executor kiro

# 目標から回す。charter.md を置けば run が自動で plan→execute→evaluate に入る（専用コマンド不要）
cp tools/kiro-autonomous/charter.md.example .kiro-autonomous/projects/default/charter.md
kiro-autonomous run --executor kiro

# 常駐: 新規タスク/フィードバックを監視して自動消化（idle 中はエージェント非起動）
kiro-autonomous run --watch --poll 10 --executor kiro

# kiro-cli 無しでプロトコル確認（決定的・無料）
kiro-autonomous run --planner none --flow-planner stub --executor stub
```

`backlog/<id>.md` に `- priority: N`（大ほど高）で外部から順序を制御できる。サブコマンド省略
（`kiro-autonomous` 単体）は **`run --watch` と同義**。

## ディレクトリ構成（プロジェクト > バックログ）

**プロジェクトが最上位コンテナ**で、`./.kiro-autonomous/projects/<name>/` 配下に集約される（`--root` でコンテナ、
`--project` でプロジェクトを選ぶ。各パスは `--backlog` 等で個別上書きも可）。needs/decisions も per-project に閉じる。

```
.kiro-autonomous/                  ← コンテナ（--root）。projects/ を束ねる
  projects/
    default/                       ← 1 プロジェクト（--project。未指定はこれを作成）
      charter.md           プロジェクト憲章（人が書く・project の最上位入力。正典 charter.md.example）
      project.json         project のサイクル状態（PASS 履歴・stall・cost。project が増分更新）
      policy.md            優先順位・実行先・安全ゲートの上書き（人だけが書く）
      backlog/<id>.md      タスク本体（案件毎・人が追加できる。done で archive/ へ退避）
      needs/<id>.md        判断待ち/検収待ちの通知＋フィードバック記入欄（人が記入→自動再開）
      decisions/<id>.md    人の判断・承認・フィードバックの決定記録（learn＝学習材料。append-only）
      archive/<id>.md      完了タスクの保全先（検収用「納品書」付き。backlog と 1:1）
      DELIVERY.md          納品一覧（受領書）。done を1行ずつ追記
      journal.md           機械のサイクルログ（人間可読）／ run-log.jsonl  構造化 run-log（JSON）
      inbox/  claims/  autonomy/  bus/   取り込み口 / 原子的クレーム / track 状態 / kiro-flow 一時バス
    payments-api/          ← もう 1 つのプロジェクト（同じ一式・併存可）
```

稼働インスタンスのレジストリ（`~/.kiro-autonomous/instances/`・`logs/`）は**グローバル**で、各プロジェクト root を
監視先として登録する＝`instances` で複数プロジェクト・複数ホストを横断発見できる（後述）。

## 実行の委譲（`--location`）

「どこで・どう動かすか」は `--location`（既定 `auto`）に集約。

| location | 委譲方法 | daemon | 用途 |
|----------|---------|--------|------|
| `local` | `kiro-flow run`（単発・同期） | 不要 | 既定の実体。逐次処理はこれで十分 |
| `daemon` | `submit` → `result` で done 待ち | ローカル daemon（無ければ local にフォールバック） | warm worker 再利用 |
| `remote` | `submit`（`--git`）→ `result` で done 待ち | 共有 git バスの remote daemon が必須 | 別マシンへオフロード |

`auto` = offload 一致＋`--git-bus` → remote ／ ローカル daemon 稼働 → daemon ／ 他 → local。daemon 検知は
kiro-flow と同じ `flock`。どちらの経路でも verify は act 完了後に走る。

**並列消費（`--concurrency N`、既定 1）**: 依存解決済みの独立タスクを先頭から最大 N 件 daemon/remote へ並行
submit し、実体の並列は kiro-flow の worker に委ねる。**実行の重い部分だけ並列化し、verify・done/archive・
決定記録・派生生成は逐次のまま**（競合回避）。local 単発 run は逐次。1 サイクル=1 タスクの計上・予算は不変。

**原子的クレーム（二重実行防止）**: 各タスクは実行前に `claims/<id>.lock` を `O_CREAT|O_EXCL` で確保した者だけが
回す。**同じ backlog を複数プロセス/ホストで回しても同一タスクは二度実行されない**。取得後に disk を再検証し、
owner 失踪は TTL 超で奪取、終了で解放。

**分散移譲（remote）**: `--git-bus <共有 git リポジトリ>`＋`policy.md` の `offload: <パターン>` 一致タスクは
`remote` に解決され、kiro-flow の `--git` 分散バス越しに別マシンの daemon へ submit する（完了を待って verify）。

```bash
kiro-autonomous run --executor kiro                              # 既定 local（単発 run）
kiro-flow --bus .kiro-autonomous-bus daemon --workers 3 &       # warm worker
kiro-autonomous run --location daemon --concurrency 3 --executor kiro
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
kiro-autonomous enqueue --title "規約に最終更新日を表示" --verify-template 'file-contains :: web/terms.html :: 最終更新'
kiro-autonomous enqueue --title "概要見出しを追加"       --accept "README に ## 概要 の見出しがある"
```

> verify を自分で書ければそれが最良（最も確実）。accept/template は「書けない人」の入口で、生成物はレビューできる
> （タスクに残る）。シェルで検証できないものは auto-done させず検収ゲート（`- review: human`）で人承認に回すとよい。

### verify の鉄則と偽 done 対策

`git log | grep refactor` のように **verify が「履歴の絶対状態」を見る**と、過去コミットにマッチして act が
何もしなくても done 確定する。鉄則は **「履歴でなく望む最終状態/差分を assert する」**。3 層で対策:

- **成果参照の真正化（常時）**: DELIVERY/needs の成果参照は **act 前(baseline)以降の新規変更のみ**を載せ、無ければ
  `(変更なし)`（既存コミットを成果物と偽らない）。kiro-autonomous 自身の状態ファイルは差分から除外。
- **差分基準（常時）**: verify 実行時に `$KIRO_BASE_REV`（act 前 HEAD）を渡す。`git log $KIRO_BASE_REV..HEAD --grep …`
  で差分スコープ verify が書ける。
- **no-progress ガード（opt-in）**: `--require-progress` / per-task `- expect: changes` で、verify=PASS でも変更が
  無ければ done せず人へ。正当な無変更は `- expect: none` で opt-out。

### フレーク耐性 / 回帰 / 検収 / パス保護

- **フレーク耐性** `--verify-confirm N`（既定 1）: verify を最大 N 回再実行し PASS/FAIL が跨いだら **flake** と判定して
  自動修正せず人へ隔離（retry を増やさない）。揺れる verify の NG churn や flaky PASS の偽 done を防ぐ。
- **回帰ゲート** `--regression-cmd "<cmd>"`: verify PASS 後・done 確定前に共通検査を走らせ、失敗したら done にせず
  人へ。`--regression-revert` は未コミットの作業ツリー変更のみ best-effort で戻す（既定 off）。
- **検収ゲート**（verify=PASS でも人の承認）: タスク `- review: human` か policy `gate: <パターン>`。対象は archive せず
  `review`（検収待ち）になり `needs/<id>.md` を生成。`approve <id>` で done 確定／フィードバックで差し戻し。
- **パス保護**（safety denylist）: policy `protect: <glob>` に一致するファイルを act が**変更したら** verify=PASS でも
  done せず検収待ちへ。`gate` がタスク一致なのに対し `protect` は**変更されたパス**一致。

### policy.md（人による上書き・per-project）

```yaml
deny:    prod        # "prod" を含むタスクは自動実行しない（実行前に止める）
pin:     T3          # 最優先 ／ defer: cleanup（後回し）
offload: heavy       # 分散環境へ移譲（--git-bus 設定時）
gate:    release     # verify PASS でも done 前に人の承認（検収ゲート・タスク一致）
protect: auth/**     # act が触ったら done せず承認へ（パス一致。glob: *=非/ **=/含む・**/ は0階層可）
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
  解禁）。track 状態は `<project>/autonomy/<track>.json`、遷移は `decisions/` に監査記録。
- **適性の採点** `audit`: backlog/policy/config/state から決定的に L0–L3 を採点（スコア・赤旗・提案）。`audit --strict` は
  スコア<40 か critical 赤旗で exit 2（CI ゲート）。L3 は verify 健全＋コスト予算＋保護デニーリスト＋掃除が揃うときのみ。

```bash
kiro-autonomous run --level report                 # 計画だけ（act しない）
kiro-autonomous run --level assisted               # 実行するが done は approve 待ち
kiro-autonomous run --auto-level --auto-level-max unattended   # 実績で自動昇格
kiro-autonomous audit --strict                     # 無人運用に値するかの門番
```

## 人の判断とフィードバック

タスクが人の判断へ回ると案件毎 `needs/<id>.md` が生成される。

- **フィードバック往復**: 「## フィードバック」欄に方針を書き `- [ ] 確定` を `- [x]` にして保存すると、次パスで拾われ
  ブロック解除＋内容を次 act に反映し `decisions/<id>.md` に記録。**誤発火防止**は ①チェックボックス `[x]`（空でも「そのまま
  再実行」）②`status: draft`（消化対象外）③`--debounce`（既定 3 秒）。
- **決定記録（DR）**: 人の判断は承認操作と不可分に `decisions/<id>.md` へ append-only。`approve`（修正承認）/
  `hold`（policy deny 追加）/ `reprioritize --pin|--defer`。DR の `- learn:` 行が下記の学習材料になる。
- **自律裁定（needs の手前・既定 on）**: 人へ回す前に kiro-cli が「ループ内で積み直して解けるか（requeue）／人が要るか
  （escalate）」を判断。requeue なら needs を作らず guidance を注入して再実行。例外・kiro-cli 不在・意思決定/リスク絡みは
  必ず人へ。1 タスク `--adjudicate-max`（既定 1）回まで。`--no-auto-adjudicate` で無効化。
- **DR 学習（通知を減らす）**: 繰り返し NG で人へ回りそうな時、他案件の `learn` からタイトル類似（Jaccard ≥
  `--learn-threshold` 既定 0.5）の過去指示を探し、あれば blocked にせず反映して自動再実行（1 タスク 1 回）。
  > 順序は **DR 学習（決定的）→ 自律裁定（kiro-cli）→ 人**の三段で人の判断を絞る。
- **ltm 昇格（横断・LLM 不要）** `--ltm`: ある `learn` が `auto-resolve` で実際に効いた回数が `--promote-threshold`
  （既定 2）以上で `ltm-use` home（`$KIRO_LTM_HOME`→`~/.claude`）へ昇格。recall は「ローカル decisions → ltm home」の順で
  フォールバックし別プロジェクトでも効く。`promote` で手動昇格。

- **通知**: 人の対応待ちへの**遷移時だけ**要約を標準出力に出す（毎サイクルでは鳴らさない）。`--notify-cmd '<cmd>'` で
  teams-use / outlook-use / issue-mailbox 等へダイジェストをパイプできる。永続の対応窓口は `needs/<id>.md`。

```bash
kiro-autonomous needs                              # 何が判断待ち/検収待ちか
kiro-autonomous approve T12 --reason "テスト側を修正"
kiro-autonomous hold prod-deploy --reason "本番は手動"
```

## backlog の自走

- **取り込み口（enqueue / inbox）**: `enqueue` は CLI フラグ or stdin/JSON（1 件/配列）から投入。`<project>/inbox/` に
  置かれた `.json`/`.md` は run/watch が取り込み元ファイルを消す。**verify を持たない投入は必ず `inbox`**＝人の triage 行き。
  外部ソース（webhook/メール/issue 抽出）は薄いアダプタでここへ流し込む。
- **依存（DAG）** `- after: T1, T2`: 依存が done（archive へ退避）になるまで消化対象に入らない。依存が blocked/review で
  止まれば従属も待つ。
- **自己生成（followup）**: 完了タスクから派生を生む。静的（タスクの `- followup: <title> :: <verify>`）／動的（act 出力の
  `@followup …` 行）。verify があれば `ready`（同 run で自走）、無ければ `inbox`。`--max-spawn`（既定 20）で上限。
- **rot 検知**: 古い/重複/実行不能を triage で検出し人へ回す（消さず棚卸し）。`rot [--fix]` 単体実行 ／ `run --rot` で毎回。

```bash
kiro-autonomous enqueue --title "レポート生成を直す" --verify 'pytest -q tests/report'
echo '{"title":"X","verify":"make test","priority":5,"after":"T1"}' | kiro-autonomous enqueue --json
cp task.md .kiro-autonomous/projects/default/inbox/
```

## 目標駆動（charter）— `run` の charter モード（長期改善ループ）

backlog の上に、人が書く**目標（charter）**から逆算する evaluator-optimizer のもう一段。backlog を消化して
`drained` で止まる正準ループに対し、「**枯渇**」と「**目標達成**」を分離して長期に回す。**プロセスは `run` に一本化**され、
`<project>/charter.md` があれば `run` が自動でこの三相に入る（専用 `project` コマンドは廃止）。

```
charter.md（goal / constraints / assumptions / deliverables / acceptance=受入 verify ／ 任意 links）
   ① plan     charter をエージェントに分解させ enqueue（冪等。verify 必須）
   ② execute  既存の正準ループ run を drained まで回す（検収/回帰/protect/予算は全て温存）
   ③ evaluate acceptance 全 PASS か判定（＋opt-in 敵対的レビュー --review-project）
        未達/指摘 → 改善タスクを生成して次サイクル（未達 acceptance はそれ自体を verify とする）
        全 PASS かつ改善ゼロ → milestone gate（needs/<project>.md）で人へ
```

- **done の唯一の根拠は `acceptance`（=verify）全 PASS**（タスク verify と同じ鉄則）。acceptance 無しの charter は
  done 判定不能＝必ず人へ。**有限停止**: 内側 run ＋ `--max-project-cycles`（既定 5）/`--max-project-cost`/
  `--project-stall`（PASS 数が増えない連続回数で人へ・既定 2）。**知能は委譲**し enqueue・acceptance・収束は決定的。
- **収束候補は人へ**: `approve <project> --reason …` で完了確定（最終納品書）／charter を更新して次フェーズへ続行／
  policy・feedback で方向修正。`--watch` は milestone 提示後も常駐し charter 更新を待つ。状態は `<project>/project.json`、
  各評価は `decisions/` に `project-evaluate` で監査記録。
- **ワーカーへの定義/判断の注入**: kiro-flow への act 依頼に **charter（定義）と `decisions/<id>.md`（判断結果）**を有界に
  注入（charter 1400 字・decisions 末尾 1000 字）。charter.md があれば全 act に乗る（無ければ空＝後方互換）。`## links` 先
  プロジェクトの定義＋判断（learn）も横展開で取り込む。

```bash
kiro-autonomous run                          # charter があれば plan→execute→evaluate（収束で人へ）
kiro-autonomous run --watch                  # 目標を満たすまで回り続ける常駐（charter 更新も待つ）
kiro-autonomous run --review-project         # acceptance 全 PASS でも短絡的達成を疑う
kiro-autonomous approve <project> --reason "受領"   # 完了確定（最終納品書）／続行は charter を更新して再実行
```

### 横展開リンク（charter.md の `## links`）

```markdown
## links
- shared-conventions      # <root>/projects/shared-conventions を参照
- ../infra-rules          # '/' や '..' を含めば相対パス
```

リンク先の定義（goal/constraints）と判断（decisions の `- learn:`）を act ワーカー文脈に取り込む（横断 recall・有界・
1 階層）。ltm-use（実績で自動昇格）に対し、charter リンクは**人が明示した参照先**を確実に引く。

## 複数プロジェクト（`--project`）

全サブコマンドに `--project <name>`（既定 `default`）。`enqueue --project X` でそのプロジェクトへ積む（無ければ作成）、
未指定なら default を作成。needs/decisions/policy/journal/検収ゲート/自律裁定/DR 学習は**そのプロジェクト内に閉じる**
（別プロジェクトの判断が混ざらない）。ディレクトリ名は unicode を保つ FS セーフ化（パス/制御文字のみ `_` 化）。

```bash
kiro-autonomous enqueue --project payments --title "…" --verify '…'
kiro-autonomous run     --project payments        # charter があれば目標駆動・無ければ backlog 消化
kiro-autonomous needs   --project payments        # per-project の判断待ち
kiro-autonomous start   --project payments        # そのプロジェクトを常駐監視
```

### 1 プロセスで全プロジェクトを回す（`--project all`）

複数プロジェクトを 1 つの kiro-autonomous で扱える。`run --project all` はコンテナ配下の全プロジェクトを**ラウンド
ロビン**で回す（各プロジェクトは独立に＝charter ありは目標駆動 `cmd_project`、無しは backlog 消化 `run_loop`）。
`--watch` は毎ラウンド `projects/` を再走査して**新規プロジェクトも自動で拾い**、どこにも仕事が無ければ idle。
`instances` はプロジェクト毎に登録されるので、外部操作（スキル）は各プロジェクトを `--project <name>` で個別に操作できる。

```bash
kiro-autonomous run --project all                 # 全プロジェクトを1プロセスで順に消化
kiro-autonomous run --project all --watch         # 全プロジェクトを1プロセスで常駐監視（新規も自動追従）
kiro-autonomous start --project all               # 上を detached 常駐起動（systemd を1ユニットで済ませられる）
```

## 常駐運用（watch / lifecycle / 発見 / OS 自動起動）

- **watch**: 1 パスが終わってもプロセスを残し backlog を監視。idle 中は kiro-cli/kiro-flow を起動せず（安価な FS
  ポーリングのみ）、`--poll` 間隔で「消化可能タスク or 新規 inbox or フィードバック」を検知して次パスを起こす。
  予算は 1 パス毎に与え直す。サブコマンド省略（`kiro-autonomous`）は `run --watch` と同義。
- **lifecycle（start / stop / restart）**: 常駐の明示操作。`start` は `run --watch` を detached 起動（ログは
  `~/.kiro-autonomous/logs/`・重複監視は拒否・`--force`）。`stop` は graceful（SIGTERM→居残りのみ SIGKILL・自分は止めない）。
  プロジェクトは `--project` で選ぶ。実行時設定は設定ファイルに寄せる思想で `start` は個別 run フラグを取らない。
- **稼働発見（instances）**: `run` 中は監視中の root と OS/WSL 情報を `~/.kiro-autonomous/instances/<pid>.json` に登録し
  終了で消す。`instances [--json]` で外部操作者（スキル）が「いまどのプロジェクト root を見ているか」を発見し、WSL/Windows を
  またいで読み書きできる（`runtime`/`wsl_distro`/`root_windows` を best-effort 併記）。**別ホスト発見**は共有レジストリ
  （`--registry`/`KIRO_AUTONOMOUS_REGISTRY`・NFS/同期/git）へも書き、自ホスト=PID・別ホスト=heartbeat 鮮度で生死判定。

```bash
kiro-autonomous start --project default        # detached 常駐起動
kiro-autonomous instances                      # 稼働中の全プロジェクト root を横断一覧
kiro-autonomous stop  --project default        # 停止（--pid / --all も可）
```

**OS 自動起動（Linux systemd ユーザーユニット）** — `~/.config/systemd/user/kiro-autonomous.service`:

```ini
[Service]
WorkingDirectory=%h/work        # <root>=./.kiro-autonomous を置く作業ディレクトリ
ExecStart=%h/.local/bin/kiro-autonomous --project default --poll 10 --executor kiro
Restart=on-failure
# 複数プロジェクトは --project 毎にユニットを分ける（kiro-autonomous@.service テンプレ等）
```
```bash
systemctl --user enable --now kiro-autonomous   # 起動＋ログイン時自動起動／ loginctl enable-linger "$USER"
```
macOS は launchd（`ProgramArguments` に `--project default --poll 10 --executor kiro`・`RunAtLoad`/`KeepAlive`）、
Windows はタスクスケジューラの「ログオン時」トリガで同等に登録する。

## 設定ファイル

環境ごと・常駐ごとに決まる値を `.kiro/kiro-autonomous.{yaml,yml,json}` に書ける（**CLI > 設定ファイル > 既定**）。
探索順: `--config` 明示 → `./.kiro/` → `~/.kiro/`。YAML は PyYAML 任意・無ければ JSON フォールバック。サンプルは
[`kiro-autonomous.yaml.example`](kiro-autonomous.yaml.example)。スカラ＋真偽フラグ（三値 `--flag`/`--no-flag`）が対象で、
個別パス上書き（`--backlog` 等）・実行限定フラグ（`--json`/`--fix`/`--pin`）・`--project` は CLI 専用。

## 計測（stats / runlog）

```bash
kiro-autonomous stats [--json]     # 完了/納品/未消化/人対応待ち・自動化率・一発done率・累計コスト
kiro-autonomous runlog [--json --tail N]   # run 毎1行 JSON（reason/done/escalations/tokens/cost/duration）
```
`stats` は archive/decisions/DELIVERY/backlog から決定的に集計（**自動化率**=auto-resolve＋auto-adjudicate÷自動＋人、
**一発 done**=retry 0、コストは納品書 `- cost:` の累計で予算と突合）。`run-log.jsonl` は監視/スプレッドシートに流せる。

## CLI 一覧

| コマンド | 役割 |
|----------|------|
| （省略）/ `run` [`--watch`] | 正準ループ（省略時は `run --watch`）。**charter.md があれば自動で目標駆動** |
| `triage` / `needs` / `rot` [`--fix`] | 優先順位付けのみ / 判断待ち表示 / rot 検出 |
| `enqueue` [`--title --verify\|--accept\|--verify-template …`\|`--json`] | 取り込み口（`--project`） |
| `approve <id>` / `hold <id>` / `reprioritize <id> --pin\|--defer` | 決定記録を残す人の操作 |
| `stats` / `runlog` / `audit` [`--strict`] | 計測 / 構造化ログ / Loop Readiness 採点 |
| `promote` | 効いた学習を ltm-use へ昇格（手動） |
| `instances` [`--json --registry`] | 稼働中プロジェクトを横断一覧 |
| `start` / `stop` / `restart` [`--project --root --force`／`--pid --all`] | 常駐の起動/停止/再起動 |

主なフラグ（抜粋）: `--project` `--root` `--planner{kiro,none}` `--flow-planner` `--location{auto,local,daemon,remote}`
`--executor{kiro,stub}` `--level` `--auto-level[-max]` `--max-cycles/-seconds/-tokens/-cost` `--throttle` `--pace`
`--concurrency` `--verify-confirm` `--require-progress` `--regression-cmd[-revert]` `--auto-adjudicate` `--learn[-threshold]`
`--ltm[-home]` `--promote-threshold` `--rot[-age-days]` `--max-spawn` `--watch` `--poll` `--debounce` `--notify-cmd`
`--git-bus/-branch/-subdir` `--charter` `--review-project` `--max-project-cycles/-cost` `--project-stall` `--dry-run` `--once`。

## テスト

```bash
KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-autonomous/tests
```
kiro-flow/kiro-cli を呼ばずに検証（stub・act 注入）。優先順位/検証ゲート/積み直し/収束/location/pace/フィードバック往復/
watch/決定記録/コスト予算/followup・依存/回帰・パス保護/自己監査/自律度/原子的クレーム/run-log・throttle/flake/偽 done/
プロジェクト層/複数プロジェクト・charter リンクを網羅。kiro-flow stub 統合は無ければ skip。
