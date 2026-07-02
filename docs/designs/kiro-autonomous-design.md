# kiro-autonomous — 設計書（統合版）

> 最終更新: 2026-06-21 ／ 関連: `tools/kiro-autonomous/`（`kiro-autonomous.py` / `README.md` /
> `GUIDE.md` / `charter.md.example` / `backlog.md.example` / `tests/`）, `tools/kiro-flow/`
>
> 本書は kiro-autonomous の**唯一の設計正典**。**処理フローとファイル構成を先に地図として示し、各機能・各設定が
> その「どのステージで効くか」を辿れる**構成にしてある。実装と差が出たら本書を更新する。

`kiro-autonomous` は、**バックログを自律的に優先順位付け・実行・検証・収束させ、人の判断が要る分だけ差し戻す
制御層**。人がプロンプトを毎サイクル投げ込まなくても回り続け、人が境界で下した判断は決定記録に残す。
`kiro-` 接頭辞は実行を kiro-flow（＝kiro-cli）へ委譲することを表す。

**読み方**: まず §1（全体像）→ §2（処理フロー全体図）→ §3（ファイル構成全体図）→ **§4（ステージ×機能×設定の対応表）**を
見れば全体地図が掴める。個々の挙動は §5（ステージ別詳細）、目標から回す上位ループは §6、複数プロジェクトは §7。

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
  ┌─ 外側＝制御層（kiro-autonomous 本体・正準ループ §2）────────────────────────────┐
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

**構成は「プロジェクト > バックログ」**。`<root>/projects/<name>/` に 1 プロジェクト＝1 セットを集約し、複数
プロジェクトを併存できる（§7）。

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
 S0 取り込み・再開                                             │ files: needs/ inbox/ backlog/ journal.md
    ・needs/<id> の [x] フィードバック → ready 復帰＋次 act に添付（ingest_feedback）  │ --debounce
    ・intake_cmd の stdout(JSON) を冪等取り込み（run_intake）     │ --intake-cmd --intake-interval
    ・inbox/ の .json/.md を backlog 化（ingest_inbox）          │
    ・triage（inbox→ready 昇格・rot 検知で blocked→needs）       │ --rot --rot-age-days
    ・verify の用意（accept→合成 / verify_template→展開）         │ task: - accept / - verify_template
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
       → review（検収待ち）で人へ（done 未確定）                  │ → needs/<id>.md
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
    notify（人の対応待ち遷移時のみ）→ --notify-cmd ／ ltm 昇格(--ltm) ／ bus 掃除(--cleanup) ／ run-log 追記
 ── --watch のとき ────────────────────────────────────────────
    パス終了後もプロセス常駐。idle は FS ポーリングのみ（エージェント非起動）。
    「消化可能 or 新規 inbox or フィードバック」を検知したら次パスを起こす（予算は 1 パス毎に与え直す）。
```

> **正準ループの 5 点（仕様の背骨）**: ①backlog を優先順位付けして最優先を kiro-flow へ（S1–S2）②順位は `--planner`、
> 人は `policy.md` で上書き（S1）③verify で検証し done は archive・NG は積み直し（S3–S4）④drained/budget/cost で停止
> （S7）⑤人の判断は `decisions/` に保存、`needs/` のフィードバックで再開（S0・S5）。

---

## 3. ファイル構成全体図（誰が・どのステージで読み書きするか）

すべて per-project（`<root>/projects/<name>/` 配下）。「人が書く / システムが書く」と「どのステージ」を併記する。

```
.kiro-autonomous/                    ← コンテナ（--root）。projects/ を束ねるだけ
  projects/
    default/                         ← 1 プロジェクト（--project。未指定はこれを作成）
      charter.md          人が書く │ プロジェクト定義（目標/制約/受入 verify/links）。S2 注入・§6 で読む
      policy.md           人が書く │ 順位・実行先・安全ゲートの上書き。S1(deny/pin/defer/offload)・S3(protect)・S4(gate)
      backlog/<id>.md     人＋系   │ タスク本体（1 ファイル=1 タスク）。S0–S4 で読み、done で archive/ へ移動
      inbox/              外部＋人 │ 取り込み待ちドロップ口（.json/.md）。S0 で backlog 化して消す
      claims/<id>.lock    系       │ 実行権の原子的クレーム。S1 で取得・S4 で解放（二重実行防止）
      needs/<id>.md       系→人→系 │ 判断待ち/検収待ちの通知＋フィードバック欄。S5/S4 で生成、S0 で取り込む
      decisions/<id>.md   系（人由来）│ 決定記録（append-only・learn 材料）。S4/S5 で追記、S2/S5 で読む
      archive/<id>.md     系       │ done の保全＋納品書（verify=PASS・成果参照）。S4 で生成
      DELIVERY.md         系       │ 納品一覧（受領書）。S4 で 1 行追記
      autonomy/<track>.json 系     │ track の自動昇格状態（clean 連続・手戻り）。S4 で更新（--auto-level 時）
      project.json        系       │ project の収束状態（acceptance PASS 履歴・stall・cost）。§6 で更新
      journal.md          系       │ 機械の人間可読サイクルログ。各ステージで追記
      run-log.jsonl       系       │ 構造化 run-log（run 毎 1 行 JSON）。ループ脱出時に追記
      bus/                系（一時）│ kiro-flow の run 状態。S2 で使い、local run 後に掃除（--no-cleanup で保持）
    payments-api/         …        │ もう 1 つのプロジェクト（同じ一式・併存可）
~/.kiro-autonomous/                 ← グローバル（プロジェクト横断）
  instances/<pid>.json   系        │ 稼働発見レコード（container/project/各パス/WSL 情報）。run 中だけ存在
  logs/<root>.log        系        │ start で起動した常駐のログ
```

横断は **instances レジストリ（グローバル）と charter `## links`** のみ。それ以外はプロジェクト内に閉じる。

---

## 4. ステージ × 機能 × 設定の対応表

「どの機能がフローのどこで効くか」「どの設定がどこに作用するか」を 1 枚にまとめた索引。詳細は各 §。

| ステージ | 何をする | 主な機能（節） | 効く設定 / policy / タスク欄 | 主に触るファイル |
|---------|---------|---------------|---------------------------|-----------------|
| **S0** 取り込み・再開 | フィードバック反映・intake/inbox 取込・triage・rot・verify 用意 | フィードバック往復(§5.1)・取り込み口(§5.1)・取り込みコマンド(§5.1)・rot(§5.1)・verify 用意(§5.1) | `--debounce` `--rot` `--intake-cmd[-interval]`／task `accept/verify_template` | needs/ inbox/ backlog/ |
| **S1** 優先順位付け・選択 | 順位決定・policy 上書き・依存/level 除外・claim | 優先順位(§5.2)・依存(§5.7)・原子的クレーム(§5.8)・level(§5.5) | `--planner` `--concurrency`／policy `deny/pin/defer`／task `priority/after/level` | policy.md claims/ |
| **S2** 実行 act | 要求文＋文脈注入・委譲先決定 | act 委譲・location(§5.3)・文脈注入(§6.4)・pace(§5.3) | `--location` `--flow-planner` `--executor` `--git-bus` `--pace` | charter.md decisions/ bus/ |
| **S3** 検証ゲート | verify・回帰・保護・進捗・コスト計上 | 検証(§5.4)・偽done対策(§5.4)・flake(§5.4)・回帰(§5.4)・保護(§5.4) | `--verify-confirm` `--regression-cmd` `--require-progress`／policy `protect`／task `verify/expect` | （workdir の git） |
| **S4** 判定（done/review/retry） | level とゲートで done・検収待ち・積み直しに分岐 | 検収ゲート(§5.5)・自律度(§5.5)・納品書(§5.4) | `--level` `--max-retries`／policy `gate`／task `level/review` | archive/ DELIVERY.md needs/ autonomy/ |
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

**妥当性（なぜこの 6 点か）**: どれも「決定的なファイル/プロセス境界」で切れており、外部 CLI が
ループの内部状態に触れない。E1/E2 は exit code、E3/E4 は JSON/ファイル、E5 は stdin、E6 は別ツールの
プラグイン機構——結合は**コマンド文字列と入出力契約だけ**なので、外しても戻り、更新も独立にできる。
逆に S1（優先順位）・S5（エスカレーション）・S7（予算）へのフックは**設けない**: そこは人の policy と
本体の決定性が支配すべき領域で、外部コマンドに開けると不変条件（人＞エージェント・必ず止まる）を
外から破れてしまう。適用例は codd-gate（E1+E2+E3 を使う一貫性ゲート。
[`codd-gate-design.md`](codd-gate-design.md) §4）。

---

## 5. ステージ別詳細

### 5.1 S0 取り込み・再開（needs / inbox / triage / rot）

- **フィードバック往復**: `needs/<id>.md` の「## フィードバック」欄に記入し `- [x]` で確定すると、`ingest_feedback` が
  対象を ready 復帰 → 本文を次 act の要求文へ添付 → `decisions/<id>.md` に記録 → needs を消費。**書きかけ誤発火を 3 層で
  防ぐ**: ①チェックボックス `[x]`（空でも「そのまま再実行」）②新規は `status: draft`（消化対象外）③`--watch` は最終保存から
  `--debounce`（既定 3 秒）待つ。
- **取り込み口（inbox）**: `<project>/inbox/` の `.json`（1 件/配列）/`.md`（タスク形式）を取り込み元ファイルを消す。外部
  ソース（webhook/メール/issue 抽出）は薄いアダプタでここへ流し込む（コアは stdlib・ネットワーク非依存）。`enqueue`
  コマンドも同経路。**verify を持たない投入は必ず `inbox`**＝人の triage 行き（鉄則）。
- **取り込みコマンド（intake_cmd・pull 型）**: push 型の inbox と対になる汎用フック。設定/CLI の `intake_cmd` を
  **パス開始時（S0）と watch の idle 中**に `intake_interval`（既定 600 秒・0 以下で毎回。`--project all` に備え
  backlog パス毎に律速）で実行し、stdout の enqueue --json 形式（spec 1 件/配列）を backlog へ取り込む（`run_intake`）。
  - **冪等**: spec の `id`（slug 化）が**現役 backlog**（blocked/review 含む）に居れば飛ばす。定期実行しても同じ発見が
    重複投入されない。done→archive 後に同じ発見が再発したら新タスクとして積み直せる（archive とは突合しない）。
  - **有限・無害**: `verify_timeout` で打ち切り。exit≠0・非 JSON・例外は journal に残して無視（ループは殺さない）。
    intake_cmd 自体は**単発・有界**であること（常駐＝長期実行は kiro-autonomous 側だけが持つ、の役割分担）。
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
`run` 経路の kiro-flow planner は `--flow-planner`（kiro-autonomous 自身の `--planner` とは別軸）。実行体は `--kiro-flow`
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
- **委譲 executor（gitlab）の完了・却下**: gitlab executor では成果物の実体は GitLab 上の MR で、**人が
  関連 MR を管理**する。**全 MR マージ＝承認**（executor がイシューをクローズし act 成功）→ kiro-autonomous が
  通常どおり verify ゲートを通して done を確定（kiro executor と対称性は持たせない）。**一つでも未マージで
  クローズ＝却下**→ executor が人コメント（無ければ自動判断）を `[gitlab-reject]` 付きで失敗にし、kiro-flow run は
  failed で非 0 終了（`cmd_run`）→ kiro-autonomous は **verify=NG 相当として通常リトライ**（`_settle_failure`）。
  その際、`read_reject_guidance` が直近 run の `[gitlab-reject]` 指示（人コメント）を読み、`feedback` に注入して
  次 act で活かす。委譲 executor では kiro-flow へ `--max-retries 0` を渡し、却下を kiro-flow 内部で再委譲せず
  即失敗化する（複数イシューの濫造を防ぎ、リトライは kiro-autonomous 側に一本化）。待機（`gitlab.timeout` /
  `gitlab.approved_timeout`）は長め・設定可能。

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
- learn   : <タイトル> :: <次回への指示>     # 任意。①の DR 学習の材料
```

操作: `needs` 記入（feedback-resume）／`approve`／`hold`（policy deny 追加）／`reprioritize --pin|--defer`。
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
- **稼働発見（instances・グローバル）**: run 中は監視対象を `~/.kiro-autonomous/instances/<pid>.json` に登録（**`container`=
  `--root` に渡す値 / `project`=`--project` 名 / 各パス / WSL 情報**）し終了で消す。外部操作者（スキル）が `instances [--json]`
  で発見し WSL/Windows をまたいで読み書きできる。別ホスト発見は共有レジストリ（`--registry`/`KIRO_AUTONOMOUS_REGISTRY`・
  NFS/同期/git）へも書き、生死は自ホスト=PID・別ホスト=heartbeat 鮮度で判定。
- **常駐ライフサイクル（start/stop/restart）**: `start` は `run --watch` を切り離して起動（ログは `logs/`・重複監視は拒否）。
  **daemon は既定で `--project all`**（1 プロセスで全プロジェクト）。watch の `cmd_run_all` は `<root>/projects/all` を表す
  「all」センチネル（実体の無い擬似 root）の instance も登録し、`start`/`stop`/`restart` の重複検出・停止・再起動が all-daemon に
  効く（`instances` では `sentinel` 印で「all-daemon の操作用・実フォルダ監視ではない」と明示し、各プロジェクトの監視レコードと区別する）。
  `run` 起動時には前回の異常終了で残った自ホストの死レコードを register 前に prune し、発見ノイズと偽の重複検出を防ぐ。`stop` は graceful
  （SIGTERM→居残りのみ SIGKILL・自分は止めない）。1 つだけ常駐したいなら `--project <name>` を明示。

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
（`kiro-autonomous` 単体）は `run --watch --project all` と同義（全プロジェクトを 1 プロセスで常駐監視。`--project all` を
前置きするだけなので、後続に明示 `--project <name>` があればそちらが勝つ）。

---

## 6. 上位ループ＝目標駆動（`run` の charter モード）

backlog の上に、人が書く**目標（charter）**から逆算する evaluator-optimizer のもう一段。backlog を消化して `drained` で
止まる正準ループに対し、「**枯渇**」と「**目標達成**」を分離して長期に回す。**プロセスは `run` に一本化**されており、
`<project>/charter.md` があれば `run`（および `run --watch`）が**自動でこの三相に入る**（charter 無しは従来の backlog ループ）。
専用の `project` サブコマンドは廃止した。

### 6.1 三相ループ（plan → execute → evaluate）

```
charter.md（goal / constraints / assumptions / deliverables / acceptance=受入 verify ／ 任意 links）
 ① plan     charter をエージェントに分解させ [{title, verify}] を enqueue（冪等＝既存と類似は投入しない）
 ② execute  §2 の正準ループ run を drained まで回す（S0–S7 のゲートは全て温存・無改造で内側呼び出し）
 ③ evaluate acceptance を実行 → 全 PASS か判定（＋opt-in 敵対的レビュー --review-project）
              未達/指摘 → 改善タスクを生成して次サイクル（未達 acceptance はそれ自体を verify とする）
              全 PASS かつ改善ゼロ → milestone gate（needs/<project>.md）で人へ
```

**plan/評価の知能は委譲**（kiro-cli。`kiro-flow run --planner flow-planner` への差し替えは注入点の交換で可能）。enqueue・
acceptance 実行・収束計算は本体が決定的に行う。**敵対的レビュー（`--review-project`）**は acceptance 全 PASS でも「短絡的
達成（弱い verify を通しただけ）」を疑い、成果物群 vs goal/deliverables を批判させて改善タスク化する。

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
書込先）と `--reference`（参照・複数）で渡す。設計の詳細は `tools/kiro-autonomous/ROUTING.md`。

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
拾って再開。状態は `<project>/project.json`、各評価は `decisions/` に `project-evaluate` で監査記録。

### 6.4 ワーカーへの定義/判断の注入（S2 で効く）

kiro-flow への act 依頼（`build_request`）に **charter（定義）と `decisions/<id>.md`（needs の判断結果）**を有界に注入する
（charter 1400 字・decisions 末尾 1000 字）。**`project` でも通常 `run` でも** charter.md があれば全 act に定義が乗る（無ければ
空＝後方互換）。`## links` 先プロジェクトの定義（`charter_context`）と判断 `- learn:`（`linked_learnings_context`）も横展開で
取り込む。注入は依頼文字列の組み立てのみ＝決定的・不変条件不変。

---

## 7. 複数プロジェクト構成（プロジェクト > バックログ）

- **レイアウトと `--project`**: `<root>/projects/<name>/` に**全て per-project**（backlog/needs/decisions/archive/charter/
  policy/journal/DELIVERY/project.json/autonomy/bus/inbox/claims）。全サブコマンドに `--project <name>`（既定 `default`）。
  effective root = `<root>/projects/<safe(name)>/`（unicode を保つ FS セーフ化）。実装は build_config の root 計算を 1 段深く
  するだけで、全 per-project パスは `backlog.parent`（=project root）から派生して自動的に配下へ移る（Config 構造は不変）。
- **作成・分離**: `enqueue --project X` で積む（無ければ作成）。未指定なら default を作成。needs/decisions/policy/検収ゲート/
  自律裁定/DR 学習は**そのプロジェクト内に閉じる**。milestone/state の id は project 名を一次採用（未設定は charter 名から導出）。
- **横展開リンク（charter `## links`）**: リンク先の定義（charter）と判断（decisions の `- learn:`）を act ワーカー文脈に取り込む
  （横断 recall・1 階層・有界）。名前は `<root>/projects/<name>`、`/`・`..` を含めば相対。ltm-use（実績で自動昇格）に対し
  charter リンクは**人が明示した参照先**を確実に引く。
- **発見の横断**: instances レジストリはグローバル（§5.8）。外部操作者は `container`/`project` を使って `--root <container>
  --project <name>` で操作する（per-project root を `--root` に渡すと二重ネストするので使わない）。
- **1 プロセスで全プロジェクト（`run --project all`）**: 1 つの kiro-autonomous がコンテナ配下の全プロジェクトを
  ラウンドロビンで回す。各プロジェクトは従来どおり独立（charter/policy/needs/予算）に駆動され、charter ありは
  `cmd_project`（目標駆動）、無しは `run_loop`（backlog 消化）が 1 単位。`--watch` では毎ラウンド projects/ を
  再走査して新規プロジェクトも拾い、どのプロジェクトにも仕事が無ければ idle（エージェント非起動）。instances は
  プロジェクト毎に登録（ファイル名に project を付与し同一 PID 内で衝突しない）。実装は `cmd_run_all` / `project_cfg`
  （per-project パスを差し替えた Config）/ `project_dir_names` / `_project_has_work`。

> 方針（本構成の前提）: **全て per-project ／ 新レイアウトのみ（flat 互換は持たない）／ リンクは定義＋判断を取込**。

---

## 8. データモデル（タスク書式）

`backlog/<id>.md`（案件毎 1 ファイル。stem が id の正）。正典は `backlog.md.example`。

```markdown
## <id>: <タイトル>
- status : inbox | draft | ready | doing | done | blocked | review
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
- note   : 任意（保持される）
```

`verify` のバッククォートは除去。既知外フィールドは順序保持で書き戻す。`ready` を消化、`inbox` は triage で `ready` 化、
`draft` は消化対象外。**done は `archive/<id>.md` へ退避**（`--no-archive` で削除）。

---

## 9. CLI・設定ファイル

| コマンド | 役割 | 主なステージ |
|----------|------|-------------|
| （省略）/ `run` [`--watch`] | 正準ループ（省略時は `run --watch`）。**charter.md があれば自動で目標駆動**（§6） | S0–S7／§6 |
| `triage` / `needs` / `rot` [`--fix`] | 順位付けのみ / 判断待ち表示 / rot 検出 | S0 |
| `enqueue` [`--title --verify …`\|`--json`] | 取り込み口（`--project`） | S0 |
| `approve <id>` / `hold <id>` / `reprioritize <id> --pin\|--defer` | 決定記録を残す人の操作 | S4/S5 |
| `stats` / `runlog` / `audit` [`--strict`] | 計測 / 構造化ログ / Loop Readiness 採点 | §10 |
| `doctor` [`--fix`] | 稼働診断（kiro-cli）。env/config 修正・program は gitlab-idd 起票 | §10 |
| `promote` | 効いた学習を ltm-use へ昇格（手動） | S5 |
| `instances` / `start` / `stop` / `restart` | 稼働発見・常駐ライフサイクル | §5.8 |

主なフラグ: `--project` `--root` `--planner{kiro,none}` `--flow-planner` `--location{auto,local,daemon,remote}`
`--executor{kiro,stub}` `--level` `--auto-level[-max]` `--max-cycles/-seconds/-tokens/-cost` `--throttle` `--pace`
`--concurrency` `--verify-confirm` `--require-progress` `--regression-cmd[-revert]` `--auto-adjudicate` `--learn[-threshold]`
`--ltm[-home]` `--promote-threshold` `--rot[-age-days]` `--max-spawn` `--watch` `--poll` `--debounce` `--notify-cmd`
`--git-bus/-branch/-subdir` `--charter` `--review-project` `--max-project-cycles/-cost` `--project-stall` `--dry-run` `--once`。

**設定ファイル（`CLI > 設定ファイル > 既定`）**: `.kiro/kiro-autonomous.{yaml,yml,json}` に書ける（探索: `--config` 明示 →
`./.kiro/` → `~/.kiro/`）。YAML は PyYAML 任意・無ければ JSON。CLI default を None にし `resolve_config` が「CLI 未指定キーだけ
設定ファイル→既定 で埋める」。スカラ＋真偽フラグ（三値 `--flag`/`--no-flag`）が対象、個別パス上書き・実行限定フラグ・
`--project` は CLI 専用。サンプルは `kiro-autonomous.yaml.example`。

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
  単独でも kiro-autonomous からの連携呼び出しでも使える。連携は決定的なサブプロセス呼び出し＋JSON 統合で不変条件を保つ。
- **テスト**: `tools/kiro-autonomous/tests/test_kiro_autonomous.py`（標準 `unittest`）。kiro-flow/kiro-cli を呼ばずに検証
  （stub・注入）。S0–S7 の各ゲート・自律度・原子的クレーム・偽 done/flake・プロジェクト層・複数プロジェクト/charter リンクを網羅。
  `KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-autonomous/tests`。
- **設計史**: 本書は分冊（mvp / ops / per-task-autonomy / project-loop / multi-project）を統合・再構成したもの（個別ファイルは
  廃止・git 履歴に残る）。
- **非目標・拡張余地**: plan 分解の kiro-flow バックエンド差し替え（現状は kiro-cli）／複数プロジェクト横断のポートフォリオ
  スケジューラ／charter リンクの循環・推移解決（MVP は 1 階層）／外部アダプタ同梱／回帰ゲートの本格ロールバック。いずれも
  §1 の不変条件を保ったまま「外周を足す」方針で段階追加できる。
