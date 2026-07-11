# kiro-project — 設計書（統合版）

> 最終更新: 2026-07-11 ／ 関連: `tools/kiro-project/`（`kiro-project.py` / `README.md` /
> `GUIDE.md` / `charter.md.example` / `backlog.md.example` / `tests/`）, `tools/kiro-flow/`
>
> 本書は kiro-project の**唯一の設計正典**。**処理フローとファイル構成を先に地図として示し、各機能・各設定が
> その「どのステージで効くか」を辿れる**構成にしてある。実装と差が出たら本書を更新する。

`kiro-project` は、**単一プロジェクトのバックログを自律的に優先順位付け・実行・検証・収束させ、人の判断が
要る分だけ差し戻す制御層**。カレントディレクトリ（または `--root`）をプロジェクトルートとし、charter.md /
repos.json を入力に成果物を出力する。人がプロンプトを毎サイクル投げ込まなくても回り続け、人が境界で下した
判断は決定記録に残す。`kiro-` 接頭辞は実行を kiro-flow（＝kiro-cli）へ委譲することを表す。

**読み方**: まず §1（全体像）→ §2（処理フロー全体図）→ §3（ファイル構成全体図）→ **§4（ステージ×機能×設定の対応表）**を
見れば全体地図が掴める。個々の挙動は §5（ステージ別詳細）、目標から回す上位ループは §6、複数プロジェクトの並べ方は §7。

---

## 1. 全体像（3 層・2 つのループ）

役割の異なる 3 層で動く。下 2 層（外側＝制御 / 内側＝実行）が**正準ループ（`run`）**、その上に**目標から回す
上位ループ（`project`）**が乗る。

```
  ┌─ 上位ループ＝プロジェクト層（§6）──────────────────────────────────────────────┐
  │  charter(目標) → ① plan(分解→enqueue) → ② execute(run) → ③ evaluate(acceptance) │
  │  未達なら改善タスクを生成して反復 / 収束は milestone gate で人へ                  │
  └───────────────┬───────────────────────────────────────────────────────────────┘
                  │ backlog へ enqueue ／ 内側 run を呼ぶ
  ┌─ 外側＝制御層（kiro-project 本体・正準ループ §2）────────────────────────────┐
  │  backlog 優先順位付け → act 委譲 → verify ゲート → done は archive/ ・NG は積み直す │
  │  → drained/budget/cost で停止。人の判断は needs/・decisions/ で往復              │
  └───────────────┬───────────────────────────────────────────────────────────────┘
                  │ act（最優先タスクの実行）を委譲
  ┌─ 内側＝実行層（kiro-flow run・別ツール）────────────────────────────────────────┐
  │  タスクの分解 → 並列ワーカー → 内側 verify ループ（7 パターン・敵対的レビュー）     │
  └───────────────────────────────────────────────────────────────────────────────┘
```

| 層 | 担当 | 実体 | 本書 |
|----|------|------|------|
| 上位（目標駆動） | 目標→backlog 生成 / 達成評価 / 改善サイクル | `run`（charter あり） | §6 |
| 外側（制御） | 優先順位付け / 検証ゲート / 積み直し / 収束 / 決定記録 / 安全ゲート | `run`（charter 無し） | §2–5 |
| 内側（実行） | タスクの分解 → act → 内側 verify ループ | `kiro-flow run` | （別ツール） |

**構成は「1 プロジェクト = 1 ディレクトリ = 1 プロセス」**。プロジェクトルート直下に 1 プロジェクト＝1 セットを
集約し、複数プロジェクトはディレクトリを並べてそれぞれで回す（§7）。束ねた可視化・操作は kiro-projects-viewer
が git 越しに担う。

### 不変条件（外周を足しても破らないもの）

すべての機能はこれを「緩める」ことはせず、安全側に倒す。本書の各機構はこの 5 点に従属する。

1. **done は verify（acceptance）の終了コード 0 でしか確定しない。** 投入・スキル・設定・敵対的レビューのどれも
   自己申告 done を作れない。安全ゲートはタスクを「足す／止める」方向のみ。
2. **必ず有限回で止まる。** 内側 run（drained / budget / cost）＋上位ループ（改善サイクル上限・stall）。
   `--watch` でも idle はエージェント非起動。
3. **人の policy ＞ エージェント提案。** 設定ファイルは「既定」レイヤで、`policy.md` と決定記録の優先には介入しない。
4. **標準ライブラリのみ・pip 依存なし**（PyYAML は任意。無ければ JSON）。
5. **決定的なファイル操作で完結**。知能（分解・優先順位・裁定・敵対的レビュー）は kiro-flow / kiro-cli へ委譲する。

---

## 2. 処理フロー全体図（`run` の 1 サイクル）

正準ループの背骨。`run_loop` は **S0→S7 を 1 サイクルとして繰り返し**、各サイクル冒頭で収束判定する。各ステージの
右側に「効く設定 / policy / 触るファイル」を併記した。詳細は §5.x、対応一覧は §4。

```
 ── while サイクル予算が残る（毎回先頭で判定）────────────────────────────────────────────────
                                                              │効く設定 / policy / ファイル
 S7 収束判定 ── max_cycles / max_seconds / max_tokens /       │ --max-cycles/-seconds/-tokens/-cost
    max_cost / throttle 超過 → break（budget/cost/throttle）   │ --throttle  → run-log.jsonl
        │ 残あり
        ▼
 S0 取り込み・再開                                             │ files: needs/ inbox/ commands/ backlog/ journal.md
    ・needs/<id> の [x] フィードバック → ready 復帰＋次 act に添付（ingest_feedback）  │ --debounce
    ・commands/ の指示 .json を CLI と同一ロジックで実行（ingest_commands）           │ --debounce
    ・intake_cmd の stdout(JSON) を冪等取り込み（run_intake）     │ --intake-cmd --intake-interval
    ・inbox/ の .json/.md を backlog 化（ingest_inbox）          │
    ・triage（inbox→ready 昇格・rot 検知で blocked→needs）       │ --rot --rot-age-days
    ・verify の用意（accept→合成 / verify_template→展開）         │ task: - accept / - verify_template
    ・投入時アセスメント（c/r/a 採点 → - assess: に記録）         │ --assess（既定 on・情報のみ）
    ・spec ルーティング（採点/policy で spec 前段を前置）＋        │ --spec-track --spec-threshold（opt-in）
      承認済み spec の tasks.md → 実装タスク群へ展開（§5.10）      │ policy: spec: ／ task: - route: direct
        │
        ▼
 S1 優先順位付け・選択                                          │ --planner{kiro,none} / policy.md
    ・基本順位（kiro=エージェント / none=priority+古さ）          │   deny→実行前に block→needs
    ・policy 上書き（deny / pin / defer。人が必ず勝つ）           │ task: - priority / - after（依存）
    ・after 依存未達 / level=report のタスクは選択から除外        │ task: - level: report
    ・concurrency 分を先頭からバッチ選択 → 各タスクを原子的に claim │ --concurrency  → claims/<id>.lock
        │ claim できたタスクだけ
        ▼
 S2 実行 act（kiro-flow へ委譲）                                │ --location{auto,local,daemon,remote}
    ・要求文を組み立て（verify を完了条件として明示）             │ --flow-planner --executor --git-bus
    ・charter / decisions / links を文脈注入（§6.4）             │ files: charter.md decisions/ → bus/
    ・location で local(run) / daemon・remote(submit) を選ぶ      │ --pace（サイクル下限間隔）
        │ act 出力（@cost / PR・SHA / @followup）
        ▼
 S3 検証ゲート（PASS でも複数ゲートを通す）                      │ --verify-timeout --verify-confirm
    ① verify 実行（$KIRO_BASE_REV を渡す。N 回で flake 判定）     │ task: - verify  $KIRO_BASE_REV
    ② 回帰ゲート（PASS 後・グローバル検査）                       │ --regression-cmd [--regression-revert]
    ③ パス保護（act が触ったパスが protect 一致か）               │ policy.md: protect:
    ④ 進捗ガード（変更ゼロなら偽 done 疑い）                      │ --require-progress / task: - expect:
    ・コスト計上（@cost を加算）                                  │ → tokens/cost を予算 S7 へ
        │
        ▼
 S4 判定（実効 level と各ゲートで分岐）                          │ --level / task: - level: / - review:
    ・NG → 積み直し（status=ready, retry++）。max_retries 超で S5  │ --max-retries
    ・PASS かつ unattended かつ全ゲート通過 → done                │ → archive/<id>.md  DELIVERY.md
    ・PASS だが assisted / review:human / gate / protect/回帰/進捗 │ policy.md: gate:
       → review（検収待ち）で人へ（done 未確定）。検収票には      │ → needs/<id>.md
       リスクダイジェスト（## リスク・frontmatter risk・§5.4.6）を添付 │
    ・done/review/retry のいずれでも claim 解放・track 実績更新     │ --auto-level → autonomy/<track>.json
        │ S4 が「人へ」と決めた分（NG 超過 / verify 未定義 / flake）
        ▼
 S5 エスカレーション（人の判断を絞る三段）                       │ --learn[-threshold] / --ltm
    ① DR 学習（決定的）… 類似 learn があれば反映して ready 復帰    │ files: decisions/ ←→ ltm-use home
    ② 自律裁定（kiro-cli 門番）… requeue なら guidance 注入       │ --auto-adjudicate --adjudicate-max
    ③ どれも不可 → _block ＋ needs/<id> 生成（人へ）              │ → needs/<id>.md  decisions/<id>.md
        │
        ▼
 S6 自走（followup）… 完了タスクの派生を backlog に生成           │ --max-spawn  task: - followup:
        │                                                         │   act 出力 @followup
        └──────────────── 次サイクルへ（→ S7） ──────────────────
 ── ループ脱出後 ──────────────────────────────────────────────
    notify（人の対応待ち遷移時のみ）→ --notify-cmd ／ ltm 昇格(--ltm) ／ bus 掃除(--cleanup。直近
    --bus-keep-runs 件の run は viewer のフローのために残す) ／ run-log 追記
 ── --watch のとき ────────────────────────────────────────────
    パス終了後もプロセス常駐。idle は FS ポーリングのみ（エージェント非起動）。
    「消化可能 or 新規 inbox or 指示(commands) or フィードバック」を検知したら次パスを起こす（予算は 1 パス毎に与え直す）。
```

> **正準ループの 5 点（仕様の背骨）**: ①backlog を優先順位付けして最優先を kiro-flow へ（S1–S2）②順位は `--planner`、
> 人は `policy.md` で上書き（S1）③verify で検証し done は archive・NG は積み直し（S3–S4）④drained/budget/cost で停止
> （S7）⑤人の判断は `decisions/` に保存、`needs/` のフィードバックで再開（S0・S5）。

---

## 3. ファイル構成全体図（誰が・どのステージで読み書きするか）

すべてプロジェクトルート（`<root>`・既定 cwd）直下。「人が書く / システムが書く」と「どのステージ」を併記する。

```
<root>/                            ← プロジェクトルート（--root。既定 cwd。通常は状態リポジトリの clone）
  charter.md          人が書く │ プロジェクト定義（目標/制約/受入 verify/links）。S2 注入・§6 で読む
  repos.yaml|json     人＋系   │ リポジトリレジストリ（手書きが正・無ければ charter から自動生成）
  policy.md           人が書く │ 順位・実行先・安全ゲートの上書き。S1(deny/pin/defer/offload)・S3(protect)・S4(gate)
  backlog/<id>.md     人＋系   │ タスク本体（1 ファイル=1 タスク）。S0–S4 で読み、done で archive/ へ移動
  inbox/              外部＋人 │ 取り込み待ちドロップ口（.json/.md）。S0 で backlog 化して消す
  commands/<name>.json 外部＋人 │ 指示（approve/hold/pin/defer/revise/replan/pause/resume/stop）のドロップ口。
                               │ S0 で CLI と同一ロジックで実行して消す（pause/resume/stop はライフサイクル指示）
  claims/<id>.lock    系       │ 実行権の原子的クレーム。S1 で取得・S4 で解放（二重実行防止）
  needs/<id>.md       系→人→系 │ 判断待ち/検収待ちの通知＋フィードバック欄。S5/S4 で生成、S0 で取り込む
  decisions/<id>.md   系（人由来）│ 決定記録（append-only・learn/avoid 材料）。S4/S5 で追記、S0(予防リコール)/S2/S5 で読む
  archive/<id>.md     系       │ done の保全＋納品書（verify=PASS・成果参照）。S4 で生成
  DELIVERY.md         系       │ 納品一覧（受領書）。S4 で 1 行追記
  specs/<id>/         系（act）│ spec 前段の成果（spec.md/design.md/tasks.md・§5.10）。人が承認して展開
  context/<repo>.md   系＋人   │ リポジトリ理解（repo-map・§6.5）。生成は opt-in・手書きも可・注入は常時
  rules.md            人＋系   │ プロジェクトルール＝暗黙知の明文化先（§6.6）。人が書くのが正・
                               │ 効いた learn を自動昇格。全タスクの act/plan/verify 合成へ常時注入
  autonomy/<track>.json 系     │ track の自動昇格状態（clean 連続・手戻り）。S4 で更新（--auto-level 時）
  project.json        系       │ project の収束状態（acceptance PASS 履歴・stall・cost）。§6 で更新
  journal.md          系       │ 機械の人間可読サイクルログ。各ステージで追記
  run-log.jsonl       系       │ 構造化 run-log（run 毎 1 行 JSON）。ループ脱出時に追記
  status.json         系       │ daemon の生存信号（watch/level/paused/updated_iso/fresh_after_sec）。
                               │ 実パス完了時に上書き（他ファイルの変更と同じコミットに相乗り。
                               │ --status-interval で idle 中の任意更新も可）。§5.8 で git 同期越しに
                               │ 同期され、リモートの viewer が instances 不在時の稼働判定に使う
  paused.json         系       │ 一時停止マーカー（commands の pause で生成・resume で削除）
  bus/                系         │ kiro-flow の run 状態（viewer のフロータブの一次ソース）。local run 後に
                               │ 古い run を掃除するが直近 --bus-keep-runs 件（既定 20）は残す（--no-cleanup で全保持）
  .state-git/         系（任意）│ 管理クローン（ルートが git でなく state_git 設定時のフォールバックのみ・§5.8）
~/.kiro-project/                  ← グローバル（プロジェクト横断）
  instances/<host>-<pid>.json 系 │ 稼働発見レコード（root/各パス/WSL 情報）。run 中だけ存在
  logs/<root>.log     系        │ start で起動した常駐のログ
```

横断は **instances レジストリ（グローバル）と charter `## links`（パス参照）** のみ。それ以外はプロジェクト内に閉じる。

---

## 4. ステージ × 機能 × 設定の対応表

「どの機能がフローのどこで効くか」「どの設定がどこに作用するか」を 1 枚にまとめた索引。詳細は各 §。

| ステージ | 何をする | 主な機能（節） | 効く設定 / policy / タスク欄 | 主に触るファイル |
|---------|---------|---------------|---------------------------|-----------------|
| **S0** 取り込み・再開 | フィードバック反映・指示（commands）実行・intake/inbox 取込・triage・rot・verify 用意・採点・spec ルーティング/展開 | フィードバック往復(§5.1)・指示ドロップ(§5.1)・取り込み口(§5.1)・取り込みコマンド(§5.1)・rot(§5.1)・verify 用意(§5.1)・アセスメント/spec(§5.10) | `--debounce` `--rot` `--intake-cmd[-interval]` `--assess` `--spec-track[-threshold]`／policy `spec`／task `accept/verify_template/route` | needs/ inbox/ commands/ backlog/ specs/ |
| **S1** 優先順位付け・選択 | 順位決定・policy 上書き・依存/level 除外・claim | 優先順位(§5.2)・依存(§5.7)・原子的クレーム(§5.8)・level(§5.5) | `--planner` `--concurrency`／policy `deny/pin/defer`／task `priority/after/level` | policy.md claims/ |
| **S2** 実行 act | 要求文＋文脈注入・委譲先決定 | act 委譲・location(§5.3)・文脈注入(§6.4)・pace(§5.3) | `--location` `--flow-planner` `--executor` `--git-bus` `--pace` | charter.md decisions/ bus/ |
| **S3** 検証ゲート | verify・回帰・保護・進捗・コスト計上 | 検証(§5.4)・偽done対策(§5.4)・flake(§5.4)・回帰(§5.4)・保護(§5.4) | `--verify-confirm` `--regression-cmd` `--require-progress`／policy `protect`／task `verify/expect` | （workdir の git） |
| **S4** 判定（done/review/retry） | level とゲートで done・検収待ち・積み直しに分岐。検収票にリスクダイジェスト添付 | 検収ゲート(§5.5)・自律度(§5.5)・納品書(§5.4)・リスクダイジェスト(§5.4.6) | `--level` `--max-retries`／policy `gate`／task `level/review` | archive/ DELIVERY.md needs/ autonomy/ |
| **S5** エスカレーション | 人へ送る前に自動で解こうと試みる三段 | DR学習(§5.6)・自律裁定(§5.6)・ltm(§5.6)・決定記録(§5.6) | `--learn` `--auto-adjudicate` `--adjudicate-max` `--ltm` | decisions/ needs/ ltm-use home |
| **S6** 自走 | 完了から派生タスクを生成 | followup(§5.7) | `--max-spawn`／task `followup`／act `@followup` | backlog/ decisions/ |
| **S7** 収束 | 予算で必ず止める | 収束・予算(§5.9)・throttle(§5.9) | `--max-cycles/-seconds/-tokens/-cost` `--throttle` | run-log.jsonl |
| 横断 | 常駐・並列・分散・多重稼働 | watch(§5.9)・分散(§5.8)・発見/lifecycle(§5.8) | `--watch` `--poll` `--registry`／policy `offload` | instances/ logs/ |
| 横断 | 自律度の段階導入・適性採点 | level/auto-level(§5.5)・audit(§5.5) | `--auto-level[-max]` `--level`／task `track` | autonomy/ |
| 上位 | 目標から回す | プロジェクト層(§6) | `--charter` `--review-project` `--max-project-cycles/-cost` `--project-stall` | charter.md project.json |

> **設定の優先順位は常に `CLI > 設定ファイル > 既定`**、かつ**人の policy/決定記録 ＞ 設定の既定値**（§1 不変条件 3）。
> タスク欄（`- level:` 等）はそのタスクに限ってグローバル設定を上書きする（締める安全網は常に上乗せ・§5.5）。

### 4.1 外部 CLI の差し込み点（フック契約カタログ）

外部ツール（決定的なゲート/検出器/通知先。例: codd-gate・lint・スモークテスト・issue 抽出器）を
**コード改造なしで差し込める公式の口**は次の 6 つ。ここに列挙の無い場所へは差し込まない（暗黙の
拡張点を作らない）。すべて §1 の不変条件に従属する——**外部 CLI は「タスクを足す」「done を止める」
「外へ知らせる」ことしかできず、done を作る・予算を破る・人の policy を上書きすることはできない**。

| # | 差し込み点 | ステージ | 拡張する機能 | 契約（入力 → 出力） | 制約 |
|---|-----------|---------|-------------|--------------------|------|
| E1 | タスクの `- verify:` ／ charter `## acceptance` | S3 ／ 上位 evaluate | **done の根拠そのもの** | cwd=workdir/verify_cwd/ワークスペース clone・env `$KIRO_BASE_REV`（act 前 HEAD）→ exit 0=PASS | 履歴でなく状態/差分を見る（§5.4 鉄則）・有界 |
| E2 | `regression_cmd`（設定/CLI） | S3 の後・done 確定前 | 検証ゲート（全タスク共通の横断検査） | E1 と同じ env/cwd → exit≠0 で done せず人へ | タスク非依存・有界。「止める」方向のみ |
| E3 | `intake_cmd`（設定/CLI） | S0・watch idle | backlog の自走（**pull 型**のタスク供給） | cwd=workdir → stdout に enqueue --json（spec/配列）。`id` が冪等キー | 単発・有界・冪等。exit≠0/非 JSON は無視（ループ健在） |
| E4 | `inbox/` ドロップ ／ `enqueue --json` | S0 | 取り込み口（**push 型**のタスク供給） | .json/.md ファイル or stdin JSON | verify 無しは inbox=人の triage 行き |
| E5 | `notify_cmd`（設定/CLI） | 人の対応待ちへの遷移時 | 通知の出口 | stdin にダイジェスト | 送信のみ・失敗しても無害 |
| E6 | `--executor`（kiro-flow executor プラグイン名/.py） | S2 act | 実行バックエンドの差し替え | kiro-flow 側の executor 契約 | 契約の正典は kiro-flow 設計書 |

**選び方**: 判定を足したい→E1（タスク固有）/E2（全タスク横断）。仕事を足したい→E3（周期 pull・冪等）/
E4（イベント push）。人へ知らせたい→E5。実行のしかたを替えたい→E6。

**タスク契約の所有権（E3/E4）**: kiro-project は設計当初から**タスクを入力とするツール**であり
（enqueue＝「汎用の取り込み口」・外部ソースは薄いアダプタで流し込む思想）、その入力形式——タスク spec
（正典 `backlog.md.example`・JSON 表現の共通スキーマは `schemas/task.schema.json`・未知キー保持の
前方互換）——の所有者は kiro-project。供給側ツール（codd-gate 等）は自分の所見を正としつつ、
**この公開データ契約への変換アダプタ**を持てばよく、kiro-project 本体への依存は生まれない
（受け側も供給元ツールを知らない。結合は双方向ともデータ契約のみ）。

**repos レジストリの独立スキーマ（`schemas/repos.schema.json`）**: リポジトリ定義（identity =
(url, path, base)）はツール横断の共通スキーマとして切り出されている。**手書き**の
`<root>/repos.{yaml,yml,json}` があれば**それがレジストリの正**（charter の `## repos` は互換入力。
どちらも内部では同じ repo_specs 形に正規化して引き回す）。手書きが**無ければ charter の `## repos`
から `repos.json` を自動生成**（`export_repo_registry`。`_meta.generated_from` マーカー付き・正は
charter のまま毎ロードで同期、`## repos` が消えれば生成物も消す。手で管理したくなったら `_meta` を
消す＝以後は手書きが正）。これが**外部ツールへの受け渡し口**——codd-gate は charter を一切読まず、
この repos ファイルを `--repos` で読むだけ（**完全独立**。分類グロブ docs/tests/code も charter から
損失なく引き継がれる）。repos ファイル単独では charter モード（目標駆動）は**発動しない**が、
ワークスペース・ルーティング／参照リポジトリの解決には効く（`registry_specs`）。kiro-flow の
`--workspace` / `--reference` はこのスキーマの 1 エントリの射影（{url, path, base, target, desc}）。

**妥当性（なぜこの 6 点か）**: どれも「決定的なファイル/プロセス境界」で切れており、外部 CLI が
ループの内部状態に触れない。E1/E2 は exit code、E3/E4 は JSON/ファイル、E5 は stdin、E6 は別ツールの
プラグイン機構——結合は**コマンド文字列と入出力契約だけ**なので、外しても戻り、更新も独立にできる。
逆に S1（優先順位）・S5（エスカレーション）・S7（予算）へのフックは**設けない**: そこは人の policy と
本体の決定性が支配すべき領域で、外部コマンドに開けると不変条件（人＞エージェント・必ず止まる）を
外から破れてしまう。適用例は codd-gate（E1+E2+E3 を使う一貫性ゲート。
[`codd-gate-design.md`](codd-gate-design.md) §4）。

---

## 5. ステージ別詳細

### 5.1 S0 取り込み・再開（needs / inbox / triage / rot / 実行前レビュー）

- **実行前レビュー（plan_review・既定 on）**: 新規タスク（plan / enqueue / inbox / followup / intake /
  cohort の全経路。status 明示は除く）は `proposed` で入り、needs/<id>.md（kind=plan-review・タスク
  定義全文つき）が生成される。**人の承認を通るまで実行されない**（S1 の選択から除外・人待ち扱い）。
  三値の決着: **承認**（`approve <id>` か空のまま `[x]`）→ ready（verify を用意できなければ inbox）／
  **差し戻し**（needs に修正指示を書いて `[x]`）→ `plan_rework` が kiro-cli にタスク定義を修正させて
  **再び proposed** で再提案（kiro-cli 不在時は指摘を note に残して再提案）／**却下**（`reject <id>`）→
  `rejected` として archive へ退避＋理由を avoid 記録＋**依存先（after 逆辺・推移）を proposed に戻して
  再審査**＋charter があれば `.replan.request` を立てて再計画（rejected タイトルは `_existing_titles` に
  含まれるため同一タスクは再提案されない）。`--no-plan-review`（設定 `plan_review: false`）で従来の
  自動投入へ戻せる。
- **依存の影響範囲（一覧提示）**: `after:` を DAG の正とし、`dependents_of`（逆辺の推移閉包）で
  revise / reject 時に影響先を DR・出力に提示する。`impact <id>` で随時一覧できる（グラフ描画はしない）。

- **フィードバック往復**: `needs/<id>.md` の「## Decision Outcome」欄（MADR 互換。旧「## フィードバック」も可）に記入し `- [x]` で確定すると、`ingest_feedback` が
  対象を ready 復帰 → 本文を次 act の要求文へ添付 → `decisions/<id>.md` に記録 → needs を消費。**書きかけ誤発火を 3 層で
  防ぐ**: ①チェックボックス `[x]`（空でも「そのまま再実行」）②新規は `status: draft`（消化対象外）③`--watch` は最終保存から
  `--debounce`（既定 3 秒）待つ。
- **取り込み口（inbox）**: `<root>/inbox/` の `.json`（1 件/配列）/`.md`（タスク形式）を取り込み元ファイルを消す。外部
  ソース（webhook/メール/issue 抽出）は薄いアダプタでここへ流し込む（コアは stdlib・ネットワーク非依存）。`enqueue`
  コマンドも同経路。**verify を持たない投入は必ず `inbox`**＝人の triage 行き（鉄則）。
- **指示ドロップ（commands）**: `<root>/commands/<name>.json`
  （`{"command": "approve|hold|pin|defer|revise", "id": "<task-id>", "reason": "..."}`。revise は加えて
  `title/priority/verify/accept/after/note/level/track/feedback` キーを受ける）を `ingest_commands` が
  拾い、**CLI（approve/hold/reprioritize/revise）と同一の関数・同一の決定記録（DR）**で実行して消す
  （二重実装しない）。CLI を実行できない操作環境（ビュアーが Windows・本体が WSL 内、など）向けの
  ファイルだけの指示経路で、needs/inbox と同じ push 型契約。壊れた JSON・未知の指示・対象不在は
  `.err` へ退避して journal に記録（無限再試行を防ぐ）。**読める指示は watch 中でも即座に取り込む**
  （viewer は `.tmp` → rename でアトミックに置く）。`--debounce` は「読めなかったファイル」だけの
  再試行猶予＝書きかけを `.err` に飛ばして指示を失わないためのもので、猶予後もダメなら退避する。
  読める指示まで先送りすると、`has_work` が起こしたパスで承認が処理されず、そのパスが charter を
  再評価してマイルストーンを書き直す（承認したのに要対応が復活する）。**起床（`has_work`）と取り込み
  （`ingest_*`）は同じ述語（`_read_command` / `feedback_submitted` + `settled`）を共有する**のが不変条件で、
  食い違うと「何も処理しないのに再評価だけするパス」が生まれる。
  タスクの投入（E4）と違い**既存タスクへの人の判断**を運ぶ口。
- **ライフサイクル指示（commands の pause/resume/stop・id 不要）**: リモートの viewer が git 越しに
  watch を操作する口。`pause` は `paused.json` を生成して消化を一時停止（idle 監視・commands の
  取り込みは継続。status.json に `paused: true` が載る）、`resume` はマーカーを消して再開、`stop` は
  状態を push してから graceful 停止（`_StopRequested` → SIGTERM と同じ finally 経路）。pause 中も
  commands/ は取り込まれるため、リモートから resume/stop を届けられる。
- **能動フィードバック（revise）**: needs（ループが人へ回す・受動）の対になる**人起点**の口。
  `revise <id>` がタスクのフィールド（title/priority/verify/accept/依存 after/note/level/track）を
  **置換**（`''`/`none` で削除。after の自己依存・循環は拒否）し、`feedback` を次 act の要求文へ
  必ず添付する（DR `action: revise`＋`- learn:` 記録）。効き方はタスク状態で決まる:
  ready 等は即時反映／blocked・review は ready へ積み直し（needs 消費・review からは手戻り記録）／
  **doing（実行中・新鮮な claim あり）は `revised` マーカーで積み直しを予約**し、実行側は settle 時に
  検知して**現在の試行の結果を確定しない**（verify も done もせず、修正内容とフィードバックで積み直す）。
  daemon/remote の結果待ちループもマーカー検知で早期に打ち切る。`rev` フィールド（revise 毎に +1）が
  act 試行の req_id に載るため、積み直し後の試行が修正前の古い run に合流することはない。
  実行ループ側の即応性は 3 点で担保する: ①パス途中（サイクル間）でも commands/needs を取り込む
  ②claim 直後にディスク内容を採用してから doing 化する（パス開始時の in-memory で人の編集を
  上書きしない）③クラッシュ等で宙に浮いた `revised` は S0 で回収して ready へ戻す（自己回復）。
- **取り込みコマンド（intake_cmd・pull 型）**: push 型の inbox と対になる汎用フック。設定/CLI の `intake_cmd` を
  **パス開始時（S0）と watch の idle 中**に `intake_interval`（既定 600 秒・0 以下で毎回）で実行し、stdout の enqueue --json 形式（spec 1 件/配列）を backlog へ取り込む（`run_intake`）。
  - **冪等**: spec の `id`（slug 化）が**現役 backlog**（blocked/review 含む）に居れば飛ばす。定期実行しても同じ発見が
    重複投入されない。done→archive 後に同じ発見が再発したら新タスクとして積み直せる（archive とは突合しない）。
  - **有限・無害**: `verify_timeout` で打ち切り。exit≠0・非 JSON・例外は journal に残して無視（ループは殺さない）。
    intake_cmd 自体は**単発・有界**であること（常駐＝長期実行は kiro-project 側だけが持つ、の役割分担）。
  - 想定例: `intake_cmd: codd-gate tasks --debt`（doc/code/test 一貫性の負債→修復タスク。
    `docs/designs/codd-gate-design.md`）。issue 抽出・監視アラート等の決定的検出器も同じ口に乗る。
- **rot 検知**: triage 時に古い/重複/実行不能を検出して人へ回す（消さず棚卸し）。`unverifiable`（verify を用意できない）/
  `duplicate`（正規化タイトル一致）/ `stale`（mtime が `--rot-age-days` 既定 14 日より古い）。`run --rot` で毎回、`rot [--fix]`
  で随時。
- **verify の用意（人が書く負担を減らす）**: 完了条件の決定的シェルは人には書きにくい。タスクは concrete な `verify` の
  代わりに以下を持てる（`ensure_verify` が ready タスクに対し S0 で concrete 化し、`verify_source` を記録して persist）:
  - **`- verify_template: <名前> :: <引数…>`** … 決定的に展開（**エージェント不要**）。`file-contains` / `file-exists` /
    `defines` / `diff-contains`（`$KIRO_BASE_REV` 差分）/ `cmd-succeeds` 等。enqueue 時に即展開。
  - **`- accept: <自然言語の完了条件>`** … エージェント(kiro-cli)が**偽 done 防止規則を織り込んで決定的 verify を合成**。
  どちらも最終的に `verify`（exit 0=PASS）になり「**done は verify のみが根拠**」の不変条件を保つ。合成/展開できなければ
  verify は空のまま＝従来どおり人へ（done 不能）。accept/verify_template を持つタスクは「verify を用意できる」ので ready で
  良い（rot の unverifiable・inbox 落ちにしない）。

### 5.2 S1 優先順位付けと選択（planner / policy）

タスクは `priority`（整数・大ほど高）を外部付与できる。2 段で順位を決める。

```
① 基本順位（--planner）
     kiro（既定）… kiro-cli が重要度・依存・priority を加味（失敗時は none へフォールバック）
     none        … priority 降順 → 同値は最古（mtime）。決定的・kiro-cli 不要
② policy.md の人間ルールで上書き（★人が必ず勝つ。適用ログを残す）
     deny  … 自動実行させない → 実行前に止めて人の判断待ちへ
     pin   … 最優先へ固定 ／ defer … 後回し
```

`policy.md`（値はタスク ID／タイトルの部分一致。per-project）:

```yaml
deny:    prod        # 実行前に止める（S1）          pin: T3 / defer: cleanup
offload: heavy       # 分散環境へ移譲（S2・--git-bus 設定時）
gate:    release     # verify PASS でも done 前に承認（S4・検収ゲート）
protect: auth/**     # act が触ったら done せず承認（S3・パス一致）
spec:    auth        # 採点に依らず spec 前段を強制（S0・spec_track 時・§5.10）
```

選択段では **after 依存未達**（§5.7）と **実効 level=report**（§5.5）のタスクを除外し、残りの先頭から `--concurrency` 分を
バッチ選択して各タスクを claim（§5.8）する。

### 5.3 S2 実行 act（location / pace / 文脈注入）

最優先タスクから要求文（**完了条件＝`verify` を明示**し loop-until-done を促す）を組み立て、kiro-flow へ委譲する。
要求文には **charter（定義）と decisions（過去の判断）・links** が注入される（§6.4）。

| location | 委譲方法 | daemon | 用途 |
|----------|---------|--------|------|
| `local` | `kiro-flow run`（単発・同期） | 不要 | 既定の実体。逐次処理はこれで十分 |
| `daemon` | `submit` → `result` で done 待ち | ローカル daemon（無ければ local にフォールバック） | warm worker 再利用 |
| `remote` | `submit`（`--git`）→ `result` | 共有 git バスの remote daemon が必須 | 別マシンへオフロード |

`auto` = offload 一致＋`--git-bus`→remote ／ ローカル daemon 稼働→daemon ／ 他→local。どちらの経路でも **verify は act 完了後**。
`run` 経路の kiro-flow planner は `--flow-planner`（kiro-project 自身の `--planner` とは別軸）。実行体は `--kiro-flow`
> `PATH` > 同梱スクリプトの順で解決。**レーン減速** `--pace P` は 1 サイクルの下限間隔（`--max-seconds` 併用で
`max_seconds/max_cycles` に均す）。

### 5.4 S3 検証ゲート（done を守る多層）

verify は done の唯一の根拠だが機械的合否でしかない。PASS でも以下を順に通す。

- **検証ゲート**: タスクの `verify` を実行し**終了コード 0 のみ**を done とする。内側 LLM が「できました」と言っても
  verify が通らなければ done にしない。
- **偽 done 対策**（履歴一致 verify）: ①成果参照は **act 前(baseline)以降の新規変更のみ**を載せ無ければ `(変更なし)`
  ②verify 実行時に **`$KIRO_BASE_REV`（act 前 HEAD）** を渡す（`git log $KIRO_BASE_REV..HEAD …` で差分スコープ verify）
  ③`--require-progress` / task `- expect: changes` で変更ゼロなら done せず人へ（`- expect: none` で opt-out）。
  鉄則は「履歴でなく望む最終状態/差分を assert する」。
- **フレーク耐性** `--verify-confirm N`（既定 1）: verify を最大 N 回再実行し PASS/FAIL が跨いだら **flake** と判定して
  自動修正せず人へ隔離（retry を増やさない）。
- **回帰ゲート** `--regression-cmd`: verify PASS 後・done 確定前に共通検査を走らせ、失敗したら done にせず人へ。
  `--regression-revert` は未コミット変更のみ best-effort で戻す（既定 off）。
- **パス保護** policy `protect: <glob>`: act が一致パスを変更したら verify=PASS でも検収待ちへ（`gate` がタスク一致なのに対し
  protect は**変更パス**一致）。`.env`/`**/secrets/**`/`auth/**`/`payments/**`/`**/migrations/**` 等を無人運用で守る最低ライン。
  remote/daemon は workdir に差分が出ないため best-effort。
- **コスト計上**: act 出力の `@cost tokens=… usd=…` を加算（決定的・無ければ 0）。S7 の予算判定の根拠になり、done 時に
  納品書へ `- cost:` を残す。
- **納品書**: done 時に個票 `archive/<id>.md`（## 納品書・verify=PASS・成果参照・完了時刻・cost）＋一覧 `DELIVERY.md` に追記。
  **成果参照**は act 出力の PR/MR URL → commit SHA → `git log -1`（baseline 以降のみ）で決定的に取得。
- **委譲 executor（gitlab）の完了・却下**: gitlab executor では成果物の実体は GitLab 上の MR。
  レビューが `status:approved` に達したら executor が**クリーンな MR（コンフリクト無し・未解決
  レビューコメント無し）を自動マージしてイシューをクローズ**し act 成功（自動承認・`auto_merge` 既定 on。
  gitlab-review-viewer の承認と同じ規則。approved なのに未クリーンなら差し戻して修正ループへ。
  人が先に全マージした場合も承認）→ kiro-project が通常どおり verify ゲートを通して done を確定
  （kiro executor と対称性は持たせない）。**一つでも未マージでクローズ＝却下**→ executor が人コメント
  （無ければ自動判断）を `[gitlab-reject]` 付きで失敗にし、kiro-flow run は
  failed で非 0 終了（`cmd_run`）→ kiro-project は **verify=NG 相当として通常リトライ**（`_settle_failure`）。
  その際、`read_reject_guidance` が直近 run の `[gitlab-reject]` 指示（人コメント）を読み、`feedback` に注入して
  次 act で活かす。委譲 executor では kiro-flow へ `--max-retries 0` を渡し、却下を kiro-flow 内部で再委譲せず
  即失敗化する（複数イシューの濫造を防ぎ、リトライは kiro-project 側に一本化）。待機（`gitlab.timeout` /
  `gitlab.approved_timeout`）は長め・設定可能（レビュー遅延前提で即応性は求めない）。

### 5.4.5 タスク単位ターゲットブランチと成果物レビュー（task_branch / delivery_review・既定 on）

- **タスクブランチ（`task_branch`・既定 on）**: 各タスクの成果は **`kp/<task-id>`**（`task_branch_prefix`）
  に集約される。`_workspace_spec_for` が workspace spec に `branch` を注入し、kiro-flow は run 毎の
  `kf/<run-id>` の代わりにそこへ push（リトライ r0/r1… も同一ブランチに積み増し）。納品書・needs の
  所在（`gate_branch`）にも載る。
- **成果物レビュー（`delivery_review`・既定 on）**: verify PASS 後、level に依らず**常に review
  （検収待ち）**へ（従来の unattended 自動 done は `--no-delivery-review`）。review 到達時、GitLab に
  到達できれば（`GITLAB_TOKEN`/`GL_TOKEN`・workspace が GitLab URL）**kp/<task-id> → target の MR を
  自動作成**（`ensure_task_mr`・冪等）し、URL を needs / gate_ref に記載する。
- **三値の決着**: **承認**（approve）= `finalize_task_mr` が Stage 2（gitlab executor）と同一規則で
  MR を自動決着 — クリーン（コンフリクト無し・未解決ディスカッション無し）→ マージ（ソースブランチ
  削除）、差分なし → クローズ、**未クリーン → 差し戻しコメントを付けて done にしない**（review のまま
  人へ）。MR 無し（GitLab 未設定）は従来どおり done 確定のみ。**差し戻し** = needs feedback（同一
  ブランチに次試行が積まれる）。**却下** = `reject` が MR クローズ＋ソースブランチ削除
  （gitlab-review-viewer の却下と同じ規則）＋廃止＋依存先の再審査＋再計画。

### 5.4.6 リスクダイジェスト（検収票の判断材料・常時 on）

review（検収待ち）へ遷移するとき、needs/<id>.md に **決定的な材料だけで組み立てた `## リスク` 節**を
添付し、総合値（low/med/high）を frontmatter `risk:` に載せる（viewer がバッジ表示）。材料は
protect 接触・avoid 類似（`find_avoidance`）・リトライ回数・変更ファイル数（差分サンプル付き）・
verify の出自（自動合成か）・投入時採点（§5.10）・回帰ゲート結果・コスト。判定規則は決定的:
**protect/avoid=high、リトライ経験・10 ファイル以上の差分・合成 verify・採点 r=3=med、他は low**。
gitlab-gatekeeper の「人が 1 枚で決める判断パケット」の薄い移植で、承認フロー自体は変えない
（情報が増えるだけ。LLM 不使用・`risk_digest`）。

### 5.5 S4 判定と自律度（done / review / retry）

実効 level と各ゲートで分岐する。**実効 level = `- level:`（明示・ピン）＞ track の自動昇格 ＞ グローバル `--level`**。
安全網（`protect`/`gate`/`review: human`/`regression`/進捗）は level に依らず**締める方向で常時上乗せ**。

| level | act | done | 用途 |
|-------|-----|------|------|
| `report` | しない（S1 で除外） | — | 計画だけ報告（消化しない安全な下見） |
| `assisted` | する | 人が `approve`（全件 review） | 実行するが done は必ず人が承認 |
| `unattended`（既定） | する | 自動（ゲート通過時） | protect/gate/regression を通れば自動 done |

- **NG** → 積み直し（status=ready, retry++）。`--max-retries`（既定 2）超で S5 へ。
- **PASS かつ unattended かつ全ゲート通過** → done（archive＋DELIVERY、track を clean 計上）。
- **PASS だが assisted / `review: human` / policy `gate` / protect 一致 / 回帰 / 進捗ゼロ** → `review`（検収待ち）として
  `needs/<id>.md` を生成し人へ（done 未確定）。`approve <id>` で done 確定（保持した成果参照で納品書）、フィードバックで差し戻し。
- **検収ゲートと deny の違い**: `deny` は実行前（S1）で止める、`gate`/`protect` は実行・verify は通すが done 確定前（S4）で止める。
- **実績連動の自動昇格（opt-in `--auto-level` ＋ task `- track:`）**: 同種群の手戻り率で level を自動調整。直近
  `level_window`（10）件で連続 clean が `level_promote_after`（5）に達し `rework_rate ≤ level_rework_max`（0）なら 1 段昇格、
  手戻り（差し戻し/回帰/偽done）で 1 段降格・**2 回で `assisted` にピンして自動停止**。ceiling 既定 `assisted`
  （`--auto-level-max unattended` で完全無人化を解禁）。状態は `autonomy/<track>.json`、遷移は `decisions/` に監査記録。
- **適性採点 `audit`**: backlog/policy/config/state から決定的に L0–L3 を採点（スコア・赤旗・提案）。`audit --strict` は
  スコア<40 か critical 赤旗で exit 2（CI ゲート）。L3 は verify 健全＋コスト予算＋保護デニーリスト＋掃除が揃うときのみ。

### 5.6 S5 エスカレーション（人の判断を絞る三段）

S4 が「人へ」と決めた分（NG 超過 / verify 未定義 / flake）に、人へ送る前のフックを 3 段で挟む。

1. **DR 学習（決定的・kiro-cli 不要）**: 他案件の `learn` からタイトル類似（Jaccard ≥ `--learn-threshold` 既定 0.5）の過去
   指示を探し、あれば blocked にせず反映して自動再実行（`auto-resolve` を記録し通知抑制。1 タスク 1 回）。
2. **自律裁定（kiro-cli 門番・既定 on）**: 「ループ内で積み直して解けるか（requeue）／人が要るか（escalate）」を判断させ、
   requeue なら needs を作らず guidance を次 act へ注入。判断材料は失敗理由＋`decisions/`＋journal の当該行＋feedback/note。
   例外・kiro-cli 不在・意思決定/リスク絡みは必ず escalate。1 タスク `--adjudicate-max`（既定 1）回まで。`policy.deny`/`hold`/
   `rot`・verify 未定義は裁定対象外（人の上書き・鉄則を維持）。
3. **人へ（needs）**: 上記で解けなければ `_block` ＋ `needs/<id>.md` 生成。決定記録は `decisions/<id>.md`（DR）に append-only:

```
## DR-0001  2026-06-17  actor: <user>
- context : T12 に人のフィードバック / action : feedback-resume / reason : … / affects : T12 → ready
- learn   : <タイトル> :: <次回への指示>     # 任意。①の DR 学習の材料（どう解けば良いか）
- avoid   : <タイトル> :: <保留理由>         # 任意。hold 由来。予防リコール（下記）の材料（自動実行させない）
```

操作: `needs` 記入（feedback-resume）／`approve`／`hold`（policy deny 追加）／`reprioritize --pin|--defer`。

**判断の自動抽出（`--learn-capture`・既定 on）**: 従来 `- learn:` は差し戻し系（feedback/revise/approve-and-fix）にしか
付かず、**承認確定・hold・優先度変更の判断は横断的に死蔵していた**。これを解消するため、`approve` の理由は
`- learn:`（解法知識）、`hold` の理由は `- avoid:`（回避知識）として自動抽出し蓄積する。avoid は learn とは別軸で、
「この種は自動実行させない＝人へ」を運ぶ（`_best_learn_match` を pattern 差し替えで共用）。

**予防リコール（投入側の shift-left・`--intake-recall`・既定 on）**: S5 が「失敗してから」人を絞るのに対し、
`enqueue`／`triage` の時点で新規タスクを過去の `- avoid:`（hold 由来）とタイトル類似照合し、一致すれば
`_block` で `blocked`＋`needs/<id>.md` に落として**実行前に人へ回す**（`intake-recall` を DR に記録）。verify を持つ
タスクは triage が inbox→ready へ自動昇格するため、inbox でなく blocked が「人の裁定待ち」の正しい状態。人は
`approve`（実行許可）／`hold`（恒久デニー）で裁定する。決定的なファイル走査＋Jaccard のみ（エージェント不要）。

**ltm 昇格（横断・LLM 不要 `--ltm`）**: ある `learn` が `auto-resolve` で実際に効いた回数が `--promote-threshold`（既定 2）
以上で `ltm-use` home（`$KIRO_LTM_HOME`→`~/.claude`）へ昇格。recall は「ローカル decisions → ltm home」の順で別プロジェクトでも効く。

### 5.7 S6 自走と依存（followup / after）

- **自己生成（followup）**: 完了タスクから派生を生む。静的（task `- followup: <title> :: <verify>`）／動的（act 出力の
  `@followup …`）。verify があれば `ready`（同 run で自走）、無ければ `inbox`。`--max-spawn`（既定 20）で上限。生成は
  `decisions/` に記録。
- **依存（DAG `- after: T1, T2`）**: 依存が done（archive へ退避）になるまで S1 の選択から除外。依存が blocked/review で
  止まれば従属も待つ。平坦な priority＋古さにトポロジカル順序を重ねる。

### 5.8 横断: 並列・分散・多重稼働・発見

- **並列消費（`--concurrency N`）**: S1 で依存解決済みの独立タスクを先頭から N 件選び、daemon/remote へ並行 submit、実体の
  並列は kiro-flow の worker に委ねる。**実行の重い部分だけ並列化**し、verify・done/archive・決定記録・派生生成は逐次のまま
  （競合回避）。local 単発 run は逐次。1 サイクル=1 タスクの計上・予算は不変。
- **原子的クレーム**: 実行前に `claims/<id>.lock` を `O_CREAT|O_EXCL` で確保した者だけが回す。取得後に disk 再検証、owner
  失踪は TTL 超で奪取、終了で解放。**同一 backlog を複数プロセス/ホストで回しても同一タスクは二度実行されない**。
- **分散移譲（remote）**: `--git-bus`＋policy `offload:` 一致タスクは remote に解決され、kiro-flow の `--git` 分散バス越しに
  別マシンの daemon へ submit（完了を待って verify）。
- **稼働発見（instances・グローバル）**: run 中は監視対象を `~/.kiro-project/instances/<host>-<pid>.json` に登録
  （**`root`=プロジェクトルート / 各パス / WSL 情報**）し終了で消す。外部操作者（スキル）が `instances [--json]`
  で発見し WSL/Windows をまたいで読み書きできる。別ホスト発見は共有レジストリ（`--registry`/`KIRO_PROJECT_REGISTRY`・
  NFS/同期/git）へも書き、生死は自ホスト=PID・別ホスト=heartbeat 鮮度で判定。
- **状態の git 保存・共有（direct モード・既定）**: プロジェクトルート自体が git 作業ツリー（トップレベル）なら、
  `DirectStateGit` がそのリポジトリへ**直接コミット・push** し、リモート（viewer）の commit を pull --rebase
  --autostash で取り込む（管理クローンを作らない）。同期対象・除外規則（`bus/`・`claims/`・ドット始まりは同期しない）
  は管理クローン方式と同一。push 競合は pull --rebase → 再 push の指数バックオフで吸収し force push はしない。
  origin が無ければコミットのみ（ローカル履歴として残る）。発動条件は「ルート = git トップレベル」——リポジトリ内の
  深いサブディレクトリでは発動しない（無関係リポジトリへの自動コミットを防ぐ）。
- **状態の git 保存・共有（管理クローン方式・フォールバック）**: ルートが git でない場合は、`state_git` 設定で
  ワークの内容（プロジェクト状態＝backlog/needs/decisions/journal/…）を共有 git リポジトリへ双方向同期する。専用の
  管理クローン（`<root>/.state-git`・`state_git_subdir` だけの sparse・blob:none）を再利用し、**fetch/push は
  `state_git_interval`（既定 300s）で律速・push は共有すべきコミットがあるときだけ**（リモート負荷を一定に保つ）。
  同一リポジトリへの**多重コミッタを前提**とし、ステージは自 subdir のみ・push 競合は pull --rebase → 再 push の
  指数バックオフで吸収・force push はしない。同期は前回スナップショット（manifest）基準の 3-way で発生源を判定し、
  同時変更のみ「人の入力パス（commands/inbox/needs/policy.md/charter.md/repos.*）はリモート優先・機械状態はローカル優先」の
  決定的規則で裁定する。`bus/`・`claims/` は同期しない（＝git 越しの多重実行防止は非提供。実行は 1 箇所・閲覧と指示を
  多箇所、の共有に最適化）。どちらの方式も同期は run のパス開始/終了と watch idle で走り、失敗してもループは殺さない
  （done の確定は git 同期に一切依存しない）。
- **daemon の生存信号（status.json）**: instances（`~/.kiro-project/instances/`）はローカルの生存レジストリで
  git 同期の対象外のため、リモート（別ホスト・git 越し）の viewer からは本体の稼働を直接判定できない。
  `write_status` が `<root>/status.json`（`watch`/`level`/`paused`/`updated_iso`/`fresh_after_sec`）を書き、これも
  git 同期することで、viewer 側に instances 不在時のフォールバック判定材料を渡す。**idle 中の追加コミットを
  既定でゼロに保つ**ため、`write_status` は実パス完了時（他ファイルの変更と同じコミットに相乗り）にのみ呼び、
  `--status-interval`（既定 0＝無効）を明示指定したときだけ idle 中もその間隔で更新する。`fresh_after_sec`
  は書き手が自分の同期間隔（`state_git_interval`/`status_interval` の大きい方の 2 倍・下限 120s）から計算して
  埋め込むため、viewer 側は単純な経過時間比較だけで済む（同期間隔を変えても viewer 側の調整は不要）。
- **常駐ライフサイクル（start/stop/restart）**: `start` は cwd（または `--root`/設定の root）のプロジェクトの
  `run --watch` を切り離して起動（ログは `logs/`・重複監視は拒否）。`run` 起動時には前回の異常終了で残った自ホストの
  死レコードを register 前に prune し、発見ノイズと偽の重複検出を防ぐ。`stop` は graceful（SIGTERM→居残りのみ
  SIGKILL・自分は止めない）。リモートからの停止/一時停止は commands/ のライフサイクル指示（§5.1）が担う。

### 5.9 S7 収束・予算・watch

| 停止理由 | 意味 | フラグ |
|----------|------|--------|
| `drained` | 消化可能タスクが尽きた | — |
| `budget` | サイクル数 / 実時間が尽きた | `--max-cycles`(20) / `--max-seconds`(0=無制限) |
| `cost` | トークン / 金額が尽きた | `--max-tokens` / `--max-cost`（0=無制限） |
| `throttle` | ソフト予算比率超過（watch は report 降格） | `--throttle`（例 0.8） |

`blocked`/`review` は消化可能集合から外れループを無限占有しない。**自動スロットル**はハード上限の手前で run を打ち切り、
`--watch` 中は report 降格で spend を止め監視は継続。**終了コード（非 watch）**: `0`=drained かつ人の対応待ち無し ／
`1`=人の対応待ち（blocked/review）あり ／ `2`=budget/cost 停止。

**watch**: 1 パス終了後もプロセスを残し backlog を監視。idle は kiro-cli/kiro-flow を起動せず（FS ポーリングのみ）、`--poll`
間隔で「消化可能 or 新規 inbox or フィードバック」を検知して次パスを起こす。予算は 1 パス毎に与え直す。サブコマンド省略
（`kiro-project` 単体）は `run --watch` と同義（cwd のプロジェクトを常駐監視）。

### 5.10 投入時アセスメントと spec ルーティング（Spec Orchestrator / Spec Driven）

Issue→Spec→実装→学習フロー（[`2026-07-12-kiro-spec-flow-integration.md`](../plans/2026-07-12-kiro-spec-flow-integration.md)）
の G1/G2。**S0–S7 の背骨は無改造**で、すべて既存プリミティブ（after DAG・plan_review/
delivery_review・enqueue・文脈注入）の組み合わせとして実装されている。

- **投入時アセスメント（`assess`・既定 on）**: S0 で新規タスク（proposed/ready/inbox・未採点）を
  c=複雑さ / r=リスク / a=曖昧さ（各 1-3）で採点し `- assess:` に記録する（1 タスク 1 回・
  `assess_task`）。知能はエージェント委譲（`_assess_prompt`・クランプ付き）、stub/失敗時は
  決定的ヒューリスティック（cohort=c3・avoid 類似=r3・verify 有無=a1〜3）。**採点は情報であり
  実行可否・done 条件を変えない**。読むのは plan-review 票（`_task_definition_block`）・検収票の
  リスクダイジェスト（§5.4.6）・下記 spec ルーティング。
- **spec ルーティング（`spec_track`・既定 off）**: 採点 max(c,r,a) ≥ `spec_threshold`（既定 3）
  または policy `spec: <パターン>` 一致のタスク T に、spec 作成タスク T-spec を前置する
  （`route_spec_tasks`）。T-spec は `specs/<T>/spec.md・design.md・tasks.md` を書く act
  ＋決定的 verify（3 ファイル非空・tasks.md に JSON）＋`review: human`（人が必ず検収）。
  T は `after: T-spec` で待つ。**人の `- route: direct` とタスクの決定済み `route` が採点に
  常に勝つ**（不変条件 3）。ルーティングは「タスクを足す」方向のみ（不変条件 1・2 を保つ）。
- **展開（`expand_spec_tasks`）**: T-spec が **done（archive・却下は除外）**になったら、
  `specs/<T>/tasks.md` の JSON 配列（enqueue --json 互換。任意 `after`=先行タスクの title）を
  実装タスク群へ展開する（`- spec: <T>` タグ・charter/workspace 引き継ぎ・max_spawn の傘・
  title→id 解決・循環拒否）。**T は after: 実装群 へ付け替えられ、自らの verify を持つ総合検証
  として最後に走る**。JSON が無ければ展開なし＝T が spec を文脈注入されて自力実装（安全側）。
- **文脈注入**: spec 作成タスクの act 要求文には作成指示（`_spec_instructions`）、実装タスク・
  総合検証タスクには spec.md/design.md が有界注入される（`spec_context`・build_request）。
- **spec タスクは委譲しない**: 成果物 specs/<id>/ はローカルの workdir に要るため、
  `decide_location` が spec タスクを **location 設定に依らず local 固定**し、
  `build_kiro_flow_cmd` が **委譲 executor（gitlab 等）を組み込み agent へ差し替える**
  （通常タスクは従来どおり委譲される）。specs/ は状態リポジトリに載り git 同期で viewer から
  読める（viewer は needs カードに spec ファイルボタンを出す）。

---

## 6. 上位ループ＝目標駆動（`run` の charter モード）

### 6.0 複数 charter（charters/<name>.md）— 1 プロジェクトで複数バージョンを並行管理

`charters/` ディレクトリがあれば **1 ファイル = 1 バージョン**（stem が charter 名）として全 charter を
ラウンドロビンで plan→execute→evaluate する（`load_charters` / `charter_names`）。無ければ従来の
`charter.md`（"default"）— 完全後方互換。

- **タスクのスコープ**: plan/評価が投入するタスクに `charter: <name>` タグが付き、**plan の冪等照合
  （`_existing_titles(cfg, charter)`）・drained 判定・acceptance 評価は charter 単位**に閉じる
  （execute の run_loop＝backlog 消化は共有プール）。別バージョンの同名タスクは重複排除しない。
- **milestone / state**: milestone id は `<project>-<charter名>`（needs/<pid>.md が charter 別）。
  `project.json` は `{"charters": {name: state}}` にキー化（単一 charter.md は従来のトップレベル形）。
  charter 変更署名・acceptance 合成キャッシュも charter 単位。
- **replan**: 要求 payload に任意 `charter` キー（CLI `replan --charter <name>`・commands の
  `"charter"`）。指定があればその charter のパスだけが消化する（無指定はどの charter でも）。
- **文脈・ルーティング**: act の charter 注入・workspace/参照解決はタスクの `charter:` タグから
  該当 charter を引く（`charter_for_task`）。repos.json の自動生成は単一 charter 運用のみ
  （複数運用では手書きレジストリか各 charter の `## repos` を直接使う）。
- **ID 採番**: 自動採番は archive とも衝突回避し、archive への退避は既存があれば `-2, -3…` で
  逃がす（明示 id は intake の冪等キーなので改名しない）。

backlog の上に、人が書く**目標（charter）**から逆算する evaluator-optimizer のもう一段。backlog を消化して `drained` で
止まる正準ループに対し、「**枯渇**」と「**目標達成**」を分離して長期に回す。**プロセスは `run` に一本化**されており、
`<root>/charter.md` があれば `run`（および `run --watch`）が**自動でこの三相に入る**（charter 無しは従来の backlog ループ）。
専用の `project` サブコマンドは廃止した。

### 6.1 三相ループ（plan → execute → evaluate）

```
charter.md（goal / constraints / assumptions / deliverables / acceptance=受入 verify ／ 任意 links）
 ① plan     charter をエージェントに分解させ [{title, verify, 任意 after=[先行タスクの title]}] を
             enqueue（冪等＝既存と類似は投入しない）。after は enqueue 後に title→id へ決定的に解決され
             （_resolve_after_titles・未知 title は落とす・循環は破棄）、計画段階から依存 DAG が立つ。
             分解の粒度は設定 `granularity`（coarse=ストーリー相当・INVEST・既定 / fine=単機能 / finest=1ファイル/1関数）
 ② execute  §2 の正準ループ run を drained まで回す（S0–S7 のゲートは全て温存・無改造で内側呼び出し）
 ③ evaluate acceptance を実行 → 全 PASS か判定（＋opt-in 敵対的レビュー --review-project）
              未達/指摘 → 改善タスクを生成して次サイクル（未達 acceptance はそれ自体を verify とする）
              全 PASS かつ改善ゼロ → milestone gate（needs/<project>.md）で人へ
```

**plan/評価の知能は委譲**（エージェント CLI。既定 kiro-cli・設定 `agent_cli: claude` で Claude Code ヘッドレスへ切替。
`kiro-flow run --planner flow-planner` への差し替えは注入点の交換で可能）。enqueue・
acceptance 実行・収束計算は本体が決定的に行う。**敵対的レビュー（`--review-project`）**は acceptance 全 PASS でも「短絡的
達成（弱い verify を通しただけ）」を疑い、成果物群 vs goal/deliverables を批判させて改善タスク化する。

**`--executor stub`（エージェント不使用）時は plan/review もローカル完結に切り替わる**: ① は
`plan_via_stub`（charter の acceptance をそのまま初期タスクにする。verify は人が書いた受入条件
そのもの）、③ の敵対的レビューは `review_via_stub`（常に所見なし）になり、どちらも `_run_kiro_cli`
を呼ばない。stub は goal の文章を読めないため起票源は acceptance しかなく、**plan は未達判定の場では
ない**（それは ③ evaluate の役目）＝ 初回から PASS する acceptance（`echo ok` 等）でも起票する。
かつては plan 時に acceptance を実行して未達だけを起票していたため、そうした charter では backlog が
空のまま収束し「バージョンを足してもバックログが現れない」ように見えていた。二周目以降は
`_enqueue_specs` が backlog と archive のタイトルで冪等に弾くため積み直されない。`--planner none` は §2 の**タスク優先順位付け**だけに効く別の設定で、
charter 駆動の plan/review には効かないため、「エージェント無しで charter 駆動を回す」には
`--executor stub` を使うこと。

### 6.2 charter.md（人が書く唯一の最上位入力）

```markdown
# Charter: <name>          # name から project id を生成（ASCII 推奨。日本語のみは "project"）
## goal / constraints / assumptions / deliverables    # 自然言語（S2 でワーカーへ注入）
## acceptance              # 受入 verify＝プロジェクト done の唯一の根拠（各行 exit 0 で PASS）
- `pytest -q tests/`
- accept: README に使用例が載っている   # 自然文も可（run 時にエージェントが決定的 verify へ合成）
## links                   # 任意。他プロジェクトの定義＋判断を横展開で取り込む（§7）
```

`acceptance` はタスク verify と同じ鉄則（履歴でなく最終状態/差分・`$KIRO_BASE_REV` 利用可）。acceptance を持たない charter は
done 判定不能＝必ず人へ。**検証コマンドを書けない条件は自然文でも書ける**（`- accept: <自然言語>` か散文の箇条書き＝タスクの
`accept:` と同じ流儀）。`cmd_project` は内側ループに入る前に `resolve_charter_acceptance` で各行を解決する: 決定的コマンド
（`_looks_like_shell_command`）はそのまま、自然言語は `synth_verify`（タスクと共用）でエージェントが決定的シェル verify へ合成し、
結果を `project.json` の `acceptance_synth`（原文→コマンド）にキャッシュしてサイクル/再実行をまたいで done 基準を安定させる。
合成できない自然言語が残れば `no-acceptance`（done 判定不能）として milestone gate で人へ回る（鉄則を保全。散文を shell へ
誤って流す事故も `_looks_like_shell_command` の二段チェックで防ぐ）。

**acceptance の実行ディレクトリ**（`evaluate_acceptance`／`_acceptance_cwd`）: 既定は `workdir` だが、offload（git-bus/remote）で
worker が対象 repo を temp に clone・push して消すと workdir に成果が出ず検証できない。そこで実行先を **明示 `--verify-cwd`
（設定 `verify_cwd`）> 単一対象 repo の一時 clone（charter の非 readonly repo がちょうど 1 つなら、その `target` ブランチ＝
worker の push 先を毎評価で取得し、`$KIRO_BASE_REV`＝HEAD で検証して後始末）> `workdir`** の順で解決する。
取得は **URL 単位のホスト共有 bare ミラー（`--mirror --filter=blob:none`）から detached worktree を生やす**方式で、毎評価で
fetch してから最新コミットで worktree を作るため、都度 clone と鮮度は同等のまま GitLab の pack 生成負荷を抑える（kiro-flow と
ミラー root を共有。詳細は [git-worktree-cache-pattern.md](git-worktree-cache-pattern.md)）。ミラー不可なら従来の
`git clone --depth 1` へ自動フォールバック。clone 失敗は workdir へ黙ってフォールバックせず**全 NG 扱い**にする（成果の無い場所での偽判定を避ける）。対象 repo が複数だと
どれを cwd にするか曖昧なため自動 clone はせず、`--verify-cwd` の明示で対応する。タスク verify／回帰検査も `--verify-cwd` 指定時は
その先で実行する（明示時のみ。baseline/protect/no-progress の差分検知は従来どおり workdir の git を見る）。

**成果物リポジトリのルーティング（charter `## repos`）**: タスクは**ちょうど 1 つの書込先ワークスペース**へ
ルーティングされる（`resolve_workspace`）。charter の各 repo は `- owns:`（担当パスのグロブ）を持てば**書込先候補**、
`- owns:` 未指定（または `- 参照のみ:`）は**参照リポジトリ**（読むだけ）。解決順は明示 `- workspace:` > policy
`route:` > charter `owns:` 推定 > auto-route（LLM）> `default_workspace`。kiro-flow へは `--workspace`（唯一の
書込先）と `--reference`（参照・複数）で渡す。設計の詳細は `tools/kiro-project/ROUTING.md`。

### 6.3 収束・milestone gate

| 停止理由 | 意味 | 条件 |
|----------|------|------|
| `accepted` | 人が milestone を承認（プロジェクト done） | acceptance 全 PASS かつ受領 |
| `converged` | 全 PASS・改善ゼロ → 人へ提示 | milestone gate（人待ち） |
| `project-budget` | 改善サイクル/内側予算の上限 | `--max-project-cycles`（既定 5） |
| `project-cost` | 累計コスト上限 | `--max-project-cost`（run のコストを横断加算） |
| `no-progress` | acceptance PASS 数が増えない | 連続 `--project-stall`（既定 2）で人へ |

収束候補は即 done にせず `needs/<project>.md` を生成。人は **`approve <project>` で完了確定（最終納品書）**／**charter.md を
更新して次フェーズへ続行**／policy・feedback で方向修正／`hold` で保留。`--watch` は milestone 提示後も常駐し charter 更新を
拾って再開。状態は `<root>/project.json`、各評価は `decisions/` に `project-evaluate` で監査記録。

### 6.4 ワーカーへの定義/判断の注入（S2 で効く）

kiro-flow への act 依頼（`build_request`）に **charter（定義）と `decisions/<id>.md`（needs の判断結果）**を有界に注入する
（charter 1400 字・decisions 末尾 1000 字）。**`project` でも通常 `run` でも** charter.md があれば全 act に定義が乗る（無ければ
空＝後方互換）。`## links` 先プロジェクトの定義（`charter_context`）と判断 `- learn:`（`linked_learnings_context`）も横展開で
取り込む。注入は依頼文字列の組み立てのみ＝決定的・不変条件不変。

### 6.5 リポジトリ理解の成果物化（repo-map・opt-in `repo_map`）

plan の直前（分解トリガ時）に、charter の**書込先 repo ごと**に `context/<repo名>.md`
（構造・主要モジュール・ビルド/テストコマンド・規約）をエージェントに生成させる
（`ensure_repo_maps`。一時 worktree を用意して調査・有界 4000 字・失敗は空のまま＝従来動作）。
**HEAD sha を署名に埋め込み（`<!-- head: sha -->`）、変化が無ければ再生成しない**（ls-remote 律速・
stub executor では生成しない）。**生成だけが opt-in（既定 off）で読み出しは常時**
（`repo_map_context`）——人が手書きした `context/*.md` も同じ口で効く。注入先は 3 つ:
① plan 分解プロンプト（全ファイル・分解の粒度と verify の精度）② `build_request`
（workspace 指定タスクはその repo 分のみ・無指定は全ファイル有界）③ `synth_verify` の合成材料
（`detect_repo_context` に上乗せ・600 字）。context/ は状態リポジトリに載り git 同期される。

### 6.6 プロジェクトルール（rules.md）— 暗黙知の明文化先（常時注入層）

フローを回して判明した**プロジェクト固有の恒常ルール**（例: テストの回し方・コミット規約・
触ってはいけない領域の慣習）の置き場。learn/avoid（decisions/）の recall が **タイトル類似の
タスクにしか届かない**のに対し、`<root>/rules.md` は **全タスクの act（`build_request`）・
plan 分解・verify 合成へ常時・有界に注入**される（charter と同列。無ければ空＝後方互換）。

- **人が書くのが正**。記録の入口は 2 つ: ①人が直接 rules.md に書く（即・全タスクへ効く）
  ②システムの自動昇格（`promote_rules`・`rules_capture` 既定 on）——**効果が再現した learn**
  （auto-resolve が `promote_threshold` 回以上）を `## 自動昇格` 節へ出典コメント付きで
  決定的に追記する（冪等・DR に `- rules-promoted:` マーカー。人はいつでも編集・削除できる）。
- **知識の昇格ラダー**: `feedback`（同一タスクの次試行）→ `learn`（類似タスク・Jaccard recall）→
  **`rules.md`（全タスク・常時）** → ltm（プロジェクト横断・opt-in `--ltm`）。rules.md は
  「プロジェクト内の最上段」。charter constraints が「目標の制約」なのに対し rules は「やり方の規則」。
- **不変条件との整合**: 注入はプロンプト文脈を足すだけで、done 条件・予算・policy には触れない。
  state-git 同期では人の入力パス（リモート優先）として扱う（昇格追記は冪等なので、同時変更で
  人の編集が勝っても次パスで再追記される）。

---

## 7. 複数プロジェクトの並べ方（1 プロジェクト = 1 ディレクトリ = 1 プロセス）

- **レイアウト**: プロジェクトルート（`--root`・既定 cwd）直下に**全て集約**（backlog/needs/decisions/archive/
  charter/repos/policy/journal/DELIVERY/project.json/autonomy/bus/inbox/claims/commands）。複数プロジェクトは
  ディレクトリ（通常は各プロジェクトの状態リポジトリの clone）を並べ、それぞれで `run --watch` / `start` する。
- **分離**: needs/decisions/policy/検収ゲート/自律裁定/DR 学習は**そのルート内に閉じる**。milestone/state の id は
  ルートのディレクトリ名を一次採用（未設定は charter 名から導出）。
- **横展開リンク（charter `## links`）**: リンク先の定義（charter）と判断（decisions の `- learn:`）を act ワーカー
  文脈に取り込む（横断 recall・1 階層・有界）。リンクは**パス**で解決する: 絶対パス／ルートからの相対／兄弟
  ディレクトリ名（ルートの親からの相対）。ltm-use（実績で自動昇格）に対し charter リンクは**人が明示した参照先**を
  確実に引く。
- **発見の横断**: instances レジストリはグローバル（§5.8）。外部操作者はレコードの `root` を `--root` に渡して
  操作する。
- **束ねた可視化・操作**: kiro-projects-viewer が各プロジェクトの clone を登録して一覧・検収・指示・停止/再開を
  git 越しに行う（本体は viewer を知らず、入出力契約＝needs/commands/inbox/charter/policy と git 同期だけが結合点）。

> 方針（本構成の前提）: **旧 `<root>/projects/<name>/` 多重レイアウト・`--project` フラグ・`--project all` は廃止**
> （クリーンブレーク。移行は旧 `projects/<name>/` の中身を新しいルートへコピーするだけ）。

---

## 8. データモデル（タスク書式）

`backlog/<id>.md`（案件毎 1 ファイル。stem が id の正）。正典は `backlog.md.example`。

```markdown
## <id>: <タイトル>
- status : inbox | draft | proposed | ready | doing | done | blocked | review | rejected
- source : human | triage | followup | enqueue | inbox | charter
- priority: 0          # 外部付与の優先度（大ほど高）                    … S1
- verify : `終了コード0をPASSとみなすシェルコマンド`   # done の唯一の根拠（履歴でなく最終状態/差分） … S3
- accept : <自然言語の完了条件>          # 任意。verify が書けない人向け。S0 でエージェントが verify を合成 … S0
- verify_template: <名前> :: <引数…>      # 任意。決定的テンプレで verify を生成（エージェント不要）       … S0
- retries: 0
- review : human       # 任意。検収ゲート                               … S4
- level  : report|assisted|unattended   # 任意。タスク単位の自律度       … S1/S4
- track  : <名前>      # 任意。--auto-level の同種群                     … S4
- after  : T1, T2      # 任意。依存                                     … S1/S7
- expect : changes|none # 任意。偽 done 対策                            … S3
- followup: 次の作業 :: `verify`         # 任意・複数可                  … S6
- route  : direct      # 任意。spec ルーティング除外の人の明示            … S0（§5.10）
- note   : 任意（保持される）
```

system が書くフィールド（§5.10・人は直接書かない）: `assess`（投入時採点 c/r/a）・
`route: spec`/`spec_task`/`spec_expanded`（spec ルーティングの状態）・`spec_for`（spec 作成タスク）・
`spec`（展開で生まれた実装タスクの参照元）。

`verify` のバッククォートは除去。既知外フィールドは順序保持で書き戻す。`ready` を消化、`inbox` は triage で `ready` 化、
`draft` は消化対象外。**done は `archive/<id>.md` へ退避**（`--no-archive` で削除）。

---

## 9. CLI・設定ファイル

| コマンド | 役割 | 主なステージ |
|----------|------|-------------|
| （省略）/ `run` [`--watch`] | 正準ループ（省略時は `run --watch`）。**charter.md があれば自動で目標駆動**（§6） | S0–S7／§6 |
| `triage` / `needs` / `rot` [`--fix`] | 順位付けのみ / 判断待ち表示 / rot 検出 | S0 |
| `enqueue` [`--title --verify …`\|`--json`] | 取り込み口 | S0 |
| `approve <id>` / `hold <id>` / `reprioritize <id> --pin\|--defer` | 決定記録を残す人の操作 | S4/S5 |
| `reject <id> --reason` | 却下（廃止して archive へ退避・依存先を再審査へ・charter があれば再計画要求・avoid 記録） | S0/S4 |
| `impact <id>` [`--json`] | 依存関係（前提／依存先・推移）の一覧＝変更・却下の影響範囲 | 横断 |
| `stats` / `runlog` / `audit` [`--strict`] | 計測 / 構造化ログ / Loop Readiness 採点 | §10 |
| `doctor` [`--fix`] | 稼働診断（kiro-cli）。env/config 修正・program は gitlab-idd 起票 | §10 |
| `promote` | 効いた学習を ltm-use へ昇格（手動） | S5 |
| `instances` / `start` / `stop` / `restart` | 稼働発見・常駐ライフサイクル | §5.8 |

主なフラグ: `--root` `--planner{kiro,none}` `--flow-planner` `--location{auto,local,daemon,remote}`
`--executor{kiro,stub}` `--level` `--auto-level[-max]` `--max-cycles/-seconds/-tokens/-cost` `--throttle` `--pace`
`--concurrency` `--verify-confirm` `--require-progress` `--regression-cmd[-revert]` `--auto-adjudicate` `--learn[-threshold]`
`--ltm[-home]` `--promote-threshold` `--rot[-age-days]` `--max-spawn` `--watch` `--poll` `--debounce` `--notify-cmd`
`--git-bus/-branch/-subdir` `--state-git[-branch/-subdir/-interval]` `--charter` `--review-project`
`--max-project-cycles/-cost` `--project-stall` `--assess` `--spec-track` `--spec-threshold`
`--repo-map` `--rules-capture` `--dry-run` `--once`。

**設定ファイル（`CLI > 設定ファイル > 既定`）**: `kiro-project.{yaml,yml,json}` に書ける（探索: `--config` 明示 →
`./`（ルート直下＝プロジェクトのマニフェスト。viewer の自動発見マーカーを兼ねる）→ `./.kiro/` → `~/.kiro/`）。YAML は PyYAML 任意・無ければ JSON。CLI default を None にし `resolve_config` が「CLI 未指定キーだけ
設定ファイル→既定 で埋める」。スカラ＋真偽フラグ（三値 `--flag`/`--no-flag`）が対象、個別パス上書き・実行限定フラグは
CLI 専用。サンプルは `kiro-project.yaml.example`。

**処理毎のエージェント上書き（`agents:`・yaml 専用）**: LLM 呼び出しの単一チョークポイント
`_run_kiro_cli(prompt, model, purpose)` に処理名（purpose）が通っており、設定 `agents:` の
マップで**処理ごとに agent_cli / model を上書き**できる（`_agent_for`・未指定はグローバル
`agent_cli` / `model`）。有効キー（`AGENT_PURPOSES`）: `plan`（分解・再計画・差し戻し修正）/
`review`（敵対的レビュー）/ `prioritize` / `route` / `adjudicate` / `verify`（合成）/
`distill`（蒸留）/ `assess`（採点）/ `repo_map` / `doctor`。未知キー・不正値は黙って落とす
（設定ミスでループを殺さない）。用途例: 重い plan は opus・大量に走る assess は haiku。
実行層（act 本体）の使い分けは kiro-flow の `agents:`（planner / evaluator / worker / kind 別）
が担う（kiro-flow 設計書 §11.1）。

---

## 10. 計測・テスト・設計史

- **計測**: `stats` は archive/decisions/DELIVERY/backlog から決定的に KPI を集計（完了・納品・status 別・人対応待ち・
  **自動化率**=auto-resolve＋auto-adjudicate÷自動＋人・**一発 done 率**=retry 0・累計コスト）。`run-log.jsonl` は run 毎 1 行
  JSON（reason/done/blocked/review/archived/escalations/tokens/cost/duration/level）で監視に流せる。
- **稼働診断**: `doctor` は**収集・修正・起票の駆動を決定的に・診断と分類を kiro-cli へ委譲**して稼働の問題を洗い出し、
  原因を **env（ユーザー環境固有）/ config（設定）/ program（プログラム上の不具合）** に分類する。材料は決定的チェック
  （依存コマンド `kiro-cli`/`kiro-flow`/`git`・必須ディレクトリ・`audit` の未達）＋稼働シグナル（`stats`/`run-log`/`journal`
  末尾/`needs`/blocked）。kiro-cli 不在・解析不能なら**決定的チェックのみ**で続行。`--fix` のとき env/config は既知の修正
  アクション（`create-dirs`／`policy-protect` の既定保護デニーリスト追記）を適用し、判断が要るもの（コスト予算・git
  初期化等）は提案表示のみ。**program は `gitlab-idd` スキルのリクエスター役（kiro-cli 委譲）で GitLab イシューを起票**し、
  **スキルが見つからなければ出力のみ**（探索: `$KIRO_SKILLS_HOME`→cwd 上方向 `.github/skills`→`~/.claude/skills`）。
  適用/起票は journal に記録。終了コードは `0`=健康／`1`=未解決の所見／`2`=未解決の critical（`--fix` 無しは診断のみ）。
  知能（診断・分類・起票文面）の委譲と決定的なファイル操作の二層構成は §1 不変条件（done 確定を緩めず外周を足す）を保つ。
  **実行層 kiro-flow との連携**（`--with-flow`・既定 on）: 内側＝act の実体である `kiro-flow doctor --json` を同じバスに対して
  呼び、同一スキーマの findings を `[flow]` 印で統合する。`--fix` 時は kiro-flow 側にも `--fix` を委譲し、kiro-flow が自分の
  env/config 修正と program 起票を担う（本体は kiro-flow 由来を再修正・再起票しない＝二重作業を避ける）。kiro-flow は同じ
  doctor 機構を独立コマンドとしても持ち（run 状態/滞留/失敗ノード/kiro-cli エラーを材料に env/config/program へ分類）、
  単独でも kiro-project からの連携呼び出しでも使える。連携は決定的なサブプロセス呼び出し＋JSON 統合で不変条件を保つ。
- **テスト**: `tools/kiro-project/tests/test_kiro_projects.py`（標準 `unittest`）。kiro-flow/kiro-cli を呼ばずに検証
  （stub・注入）。S0–S7 の各ゲート・自律度・原子的クレーム・偽 done/flake・プロジェクト層・複数プロジェクト/charter リンクを網羅。
  `KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-project/tests`。
- **設計史**: 本書は分冊（mvp / ops / per-task-autonomy / project-loop / multi-project）を統合・再構成したもの（個別ファイルは
  廃止・git 履歴に残る）。
- **非目標・拡張余地**: plan 分解の kiro-flow バックエンド差し替え（現状は kiro-cli）／複数プロジェクト横断のポートフォリオ
  スケジューラ／charter リンクの循環・推移解決（MVP は 1 階層）／外部アダプタ同梱／回帰ゲートの本格ロールバック。いずれも
  §1 の不変条件を保ったまま「外周を足す」方針で段階追加できる。

---

## 11. 設計提案: フィードバックの全体還元と verify 品質改善（Draft・未実装）

> ステータス: Draft（設計案・未実装）。対になる kiro-flow 側は
> [`kiro-flow-design.md` §18](./kiro-flow-design.md)（gitlab 人コメントの人/エージェント判別・emit と分解への還元）。
> 責務境界は「**gitlab executor はコメントを運ぶだけ／蒸留・learn・recall・verify は本ツール**」。

### 11.0 背景（2 つの問題）

- **問題A — フィードバックの局所性**: gitlab executor 運用で、個々のイシューに投稿した**ユーザーコメント**は
  「同一タスクの次の試行」にしか活きず、**同様のタスクに還元されない**。指摘が **plan（分解）や verify の再考**にも及ばない。
- **問題B — verify CLI の品質**: 「verify を CLI で不確実性をなくす」思想は正しいが、ユーザーが CLI を書くのは難しく、
  **自動生成 verify の品質がイマイチ**（`synth_verify` は title+accept だけの単発 LLM・品質ゲートは構文チェックのみ）。

### 11.1 診断

- (A) 人ゲート/revise/needs は `append_decision(learn=)`（`1626/4425/4439/4629`）で learn 化され横断する
  （`find_learned_resolution:905-922` / `linked_learnings_context:2184-2200` / `promote_learnings:1435-1488`）。
  一方 **(B) gitlab 却下 guidance は `_settle_failure` 却下枝（`3378-3391`）で `task.feedback` に注入するだけ**で
  `learn=` 捕捉が無く、かつ act を再実行するだけで plan/verify に戻らない。＝問題A の根本原因。
- 問題B: `synth_verify:2020-2035` は文脈なし単発合成、`_looks_like_shell_command:2003-2017` は `sh -n` の構文チェックのみ。
  「変更前 fail・変更後 pass」の検証も、学習再利用も無い。

### 11.2 背骨 — 統一学習バス

**人のあらゆる判断・指摘を 1 本の学習ストア（`decisions/`＋ltm）へ集約し、複数の消費者が読む**多対多構造にする。
既存の learn 機構（Jaccard recall・ltm 昇格・links 横展開）を土台に流用する。

```
   人ゲート/revise/needs ─┐
   gitlab の人コメント     ┤→ 蒸留(episodic→semantic/procedural) → decisions/*.md（learn/avoid/verify種別）＋ ltm home
   (却下/承認/作業中)      ┘                                        │ recall（Jaccard / ltm）
        ┌──────┬──────────┬──────────┬───────────┬──────────┐
     次の act  plan(分解)  verify合成  triage/intake  cohort兄弟
```

**蒸留（`distill_learn` 新設）**: 生の人コメントを `<一般化した条件> :: <再利用可能な指針>` へ引き上げる
（ltm-use v5 の consolidate に相当）。作業中コメントは「durable な恒久指示か」を判定し一過性を落とす。
LLM 委譲（有界）＋失敗時は生 verbatim フォールバック。verify に効く指針は `verify` 種別 learn として分離（§11.6 が読む）。
**入力の人コメントは、kiro-flow §18 が emit する著者情報付き notes を受けて「人と確定したもののみ」**（gitlab-idd の
worker/reviewer エージェントコメントは除外。判別は kiro-flow §18.1・最終判定は本ツール）。

### 11.3 捕捉の統一 — gitlab の人コメントを learn 化

`_settle_failure` 却下枝（`3378-3391`）に、`task.feedback` 注入に加えて learn 捕捉を足す（最小変更・横断の起点）:

```python
if guidance:
    task.extra.append(("feedback", guidance.replace("\n", " ⏎ ")))
    if cfg.learn_capture and is_human_comment(...):   # ← 追加（人判定は kiro-flow emit を受けて確定）
        append_decision(cfg, task.id, "gitlab", action="gitlab-reject",
                        reason=guidance, learn=(task.title, distill_learn(cfg, task, guidance)))
```

**却下以外も**: kiro-flow §18.2 が emit する `data.notes`（承認決着・作業中増分・著者付き）を
`read_reject_guidance`（`2778-2804`）拡張で取り込み、却下＝`avoid` 寄り／承認・作業中＝`learn` 寄りに振り分け、
`note_id` で重複排除して同じ蒸留→learn ストアへ流す。

### 11.4 適用先の拡張 — act だけでなく plan / verify にも

既存 recall は `build_request`（次の act）にしか注入していない。読み手を増やす:

- **plan（分解の再考）**: charter モードの plan・再計画の分解に learn/avoid を注入し、flow-planner へ
  **`--learnings`**（構造化・有界）で伝搬（受け口は kiro-flow §18.3）。分解グラフ自体を変える。
- **verify 合成（verify の再考）**: `synth_verify`/`ensure_verify` に `verify` 種別 learn を注入（§11.6）。

### 11.5 昇格ラダー ＋ cohort 還流

- **昇格ラダー**: `①タスク feedback → ②横断 learn → ③横プロジェクト link/ltm → ④反復検知で人へ「系の再考」`。
  同一 Jaccard クラスタの却下が `--reject-recur`（既定 2）回超過で `needs/<id>.md` を起こし「分解/verify/policy を見直すか」を人へ。
- **cohort 還流**: gitlab で cohort メンバ/pilot が却下されたら `cohorts/<id>.json` を更新し、`materialize_cohort_rest`（`371-403`）
  と同じ経路で未実行メンバの feedback を上書き（現状の一方向・人ゲート限定を双方向化）。

### 11.6 verify を「検証された・文脈付き・学習される」パイプラインに（問題B）

- **Red-Green 検証（核）**: 合成候補を done 根拠にする前に、baseline（`$KIRO_BASE_REV`/act 前ツリー）で **FAIL**・
  post-act で **PASS** を実行確認し、**red かつ green のみ採用**。`true`・恒真式・既存状態マッチ・履歴一致の偽 done を
  実行レベルで排除（`require_progress:3455-3473` の上位互換）。baseline worktree は worktree-cache（`KIRO_GIT_CACHE_DIR`）で生やす。
  破壊的/高コストは `- verify_validate: none` で opt-out。
- **文脈つき合成 ＋ テンプレ拡充**: `synth_verify` に検出したテスト/ビルド基盤・`paths`/差分・過去 verify 例を渡す。
  `verify_template` を `test-passes` / `endpoint-returns` / `builds` / `exit-zero` 等へ拡充（決定的＝合成より優先）。
- **多候補 ＋ 敵対的妥当性**: N 候補を出し red-green ＋敵対的批評（false-done を 1 つ挙げよ）で選別（kiro-flow の
  generate-and-filter/adversarial を verify 著作に適用）。
- **学習・再利用**: red-green を通った verify を種別キーで `decisions`/ltm に **procedural memory** 保存
  （`verify_source: synth+validated`）。人が書いた verify をシードに最優先。新規 `accept:` はまず似た過去 verify を recall。
- **劣化時**: 自動 done せず、**候補コマンド＋red-green 実行証跡を添えて** `needs/<id>.md` へ。人は白紙でなく草案を承認/微修正。

### 11.7 段階導入・未決事項

| フェーズ | 内容 | 状況 |
|---------|------|------|
| **P0** | 人/エージェント判別の厳格化（kiro-flow §18.1。本ツールは emit を受けて最終判定）＝全フェーズの前提 | ✅ 実装 |
| **P1** | §11.3 人コメントの learn 捕捉（却下＋承認＋蒸留） | ✅ 実装（却下 `_settle_failure`＋承認 `capture_approve_learn`＋`distill_learn`） |
| **P2** | §11.6 Red-Green 検証／恒真式スクリーン | ✅ 実装（実行 red-green `verify_undiscriminating`＋恒真式棄却 `_verify_is_degenerate`。`--verify-validate`） |
| **P3** | §11.2 蒸留 ＋ §11.6 文脈つき合成・テンプレ拡充 | ✅ 実装（`detect_repo_context` で合成へ基盤注入・テンプレ拡充・蒸留） |
| **P4** | §11.4 verify 合成への learn recall ＋ verify 学習再利用 ＋ plan/act への recall | ✅ 実装（合成ヒント・検証済み verify 再利用・`build_request` が類似 learn を分解/実装へ注入） |
| **P5** | §11.5 昇格ラダー ＋ cohort 還流・§11.6 多候補 | ✅ 実装（昇格ラダー・`cohort_reflux`・合成の自己修復＝恒真式/散文を捨て再合成） |

**実装済み**（P0〜P3＋P4/P5 の一部）: gitlab 人コメントの learn 捕捉（却下 `_settle_failure`・承認
`capture_approve_learn`）・蒸留（`distill_learn`・LLM＋verbatim・`--distill-learn`）・承認/却下の著者付き
`notes` emit（kiro-flow）・**実行 red-green 検証**（`verify_undiscriminating`/`run_verify_at_rev`・
`--verify-validate off/synth/all`・per-task `- verify_validate: none`）・恒真式棄却（`_verify_is_degenerate`）・
テンプレ拡充（`test-passes`/`builds`/`exit-zero`/`endpoint-returns`）・**文脈つき合成**（`detect_repo_context` が
package.json/pytest/Makefile/go/cargo を検出して合成へ注入）・**verify 合成への learn recall**（`ensure_verify` が
`find_learned_resolution` の指針を合成ヒントに）・**verify 学習再利用**（`save_validated_verify`/
`find_learned_verify`＝done した自動生成 verify を `.verifylib.md` に保存し、類似タスクで合成前に再利用）・
**昇格ラダー**（`count_gitlab_reject_recur`＋`--reject-recur`＝同種却下の反復で silent 積み直しをやめ「系の再考」で人へ）・
**plan/act への recall**（`build_request` が `find_learned_resolution` の類似 learn を要求本文へ注入＝flow-planner が
分解時に、ワーカーが実装時に踏まえる）・**cohort 還流**（`cohort_reflux`＝gitlab で cohort メンバ/pilot が却下されたら
同 cohort の未完了メンバへ指摘を波及）・**合成の自己修復（多候補）**（`synth_verify` が散文/シェル非妥当/恒真式に
退化した候補を不採用理由つきで最大 attempts 回まで再合成）。
テスト: kiro-project `FeedbackReductionTests`（24）・kiro-flow `GitlabHumanAgentDiscriminationTests`（7）。
**残（任意・機能は代替手段で充足済み）**: flow-planner への構造化 `--learnings` channel（現状は要求本文経由で
planner に届く）・作業中コメントの逐次取り込み（決着時に全人コメントを掃くため学習漏れは無い・逐次はレイテンシ最適化）・
verify 著作の本格的な kiro-flow グラフ化（自己修復で軽量に代替済み）。

後方互換（`learn_capture` off・`distill_learn` off・`trust_unmarked_comments` で従来挙動）。P0→P1→P2 を薄く入れて検証を推奨。
**未決**: 蒸留の LLM 利用可否（推奨: LLM＋失敗時 verbatim）／作業中コメントの durable 判定既定／Red-Green のコスト・破壊性
（推奨: opt-out＋読み取り/テスト系に既定適用）／plan への learn 注入の有界化／`--reject-recur`・Jaccard しきい値
（既存 `--learn-threshold` 0.5 と揃えるか）／kiro-flow 内側 verify ノードの CLI 化（本案対象外・将来判断）。

### 11.8 影響ファイル（本ツール側）

| 箇所 | 変更 |
|------|------|
| `_settle_failure` 3378-3391 | §11.3 learn 捕捉・notes 取り込み・§11.5 反復検知 |
| `read_reject_guidance` 2778-2804 | 承認/作業中 notes（著者付き）の読み取り・人判定の最終確定 |
| `synth_verify` 2020-2035 / `_synth_verify_prompt` 1987-1996 | §11.6 文脈注入・多候補 |
| `ensure_verify` 2038-2058 / `run_verify` 1878-1904 | §11.6 red-green・学習再利用 |
| `expand_verify_template` 1965-1984 | §11.6 テンプレ拡充 |
| `build_request` 2203-2225 / `find_learned_resolution` 905-922 | §11.4 plan/verify への recall |
| cohort 371-403 / `append_decision` 849-872（新 `distill_learn`） | §11.5 cohort 還流・§11.2 蒸留 |
