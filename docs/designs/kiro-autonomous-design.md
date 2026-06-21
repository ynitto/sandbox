# kiro-autonomous — 設計書（統合版）

> 最終更新: 2026-06-21 ／ 関連: `tools/kiro-autonomous/`（`kiro-autonomous.py` / `README.md` /
> `GUIDE.md` / `charter.md.example` / `backlog.md.example` / `tests/`）, `tools/kiro-flow/`
>
> 本書は kiro-autonomous の**唯一の設計正典**。従来の分冊（mvp / ops / per-task-autonomy /
> project-loop / multi-project）を統合し、重複を排して再構成した。実装と差が出たら本書を更新する。

`kiro-autonomous` は、**バックログを自律的に優先順位付け・実行・検証・収束させ、人の判断が要る分だけ
差し戻す制御層**。人がプロンプトを毎サイクル投げ込まなくても回り続け、人が境界で下した判断は決定記録に残す。
`kiro-` 接頭辞は実行を kiro-flow（＝kiro-cli）へ委譲することを表す（kiro-loop / kiro-flow に倣う）。

---

## 1. 位置づけと全体像（3 層）

Loop Engineering（「プロンプトを書く人」をやめ「プロンプトを出し続けるループ＝システム」を設計する）の実装。
仕様駆動（SDD）の枠組みは意図的に外す。役割の異なる 3 層で捉える。

```
         ┌─ プロジェクト層（charter 駆動・複数プロジェクト）── §11–12 ─────────────────┐
   人 →  │  目標(charter) → backlog を生成 → 評価(acceptance) → 改善 を長期に回す      │
         └───────────────┬───────────────────────────────────────────────────────────┘
                         │ enqueue / 収束は milestone gate で人へ
         ┌─ 外側＝制御層（kiro-autonomous 本体）── §2–10 ────────────────────────────┐
   人 →  │  backlog を優先順位付け → kiro-flow で act → verify ゲート → done は archive/ │
         │  へ退避・NG は積み直す → drained/budget/cost で停止。判断は needs/・decisions/ │
         └───────────────┬───────────────────────────────────────────────────────────┘
                         │ act（最優先タスクの実行）を委譲
         ┌─ 内側＝実行層（kiro-flow run）─────────────────────────────────────────────┐
         │  タスクの分解 → 並列ワーカー → 内側 verify ループ（7 パターン・敵対的レビュー）  │
         └───────────────────────────────────────────────────────────────────────────┘
```

| 層 | 担当 | 実体 |
|----|------|------|
| プロジェクト層 | 目標→backlog 生成 / 達成評価 / 改善サイクル / 複数プロジェクト | `kiro-autonomous project` |
| 外側（制御） | 優先順位付け / 検証ゲート / 積み直し / 収束 / 決定記録 / 安全ゲート | `kiro-autonomous run` |
| 内側（実行） | タスクの分解 → act → 内側 verify ループ | `kiro-flow run`（別ツール） |

**構成は「プロジェクト > バックログ」**。`<root>/projects/<name>/` に 1 プロジェクト＝1 セットを集約し、
複数プロジェクトを併存できる（§12）。`policy.md`＝人による上書き、`decisions/`＝判断の台帳、
`charter.md`＝プロジェクト定義。

---

## 2. 正準ループ（仕様の背骨・5 点）

他のすべての機構はこの 5 点に従属する。

1. **`backlog/`（案件毎ファイル `backlog/<id>.md`）を読み優先順位をつけ、最優先タスクを kiro-flow に投げる。**
2. **優先順位付けは `--planner` で選ぶ**（`kiro`＝エージェントが外部 `priority` も加味／`none`＝priority 降順→
   同値は最古 FIFO）。人間は `policy.md` で上書きでき、上書きは決定記録に残る（§8）。
3. **kiro-flow の結果を verify ゲートで検証**。done は `archive/` へ退避、NG は積み直す。done の唯一の根拠は
   タスク自身の `verify` の終了コード 0（§5）。
4. **backlog が尽きるか予算（サイクル/実時間/コスト）が尽きるまで反復**。`--watch` なら尽きてもプロセスは生存して
   監視を続ける（**idle 中はエージェントを起動しない**）（§6）。
5. **人の判断は案件毎 `decisions/<id>.md` に保存**。`needs/<id>.md` のフィードバック欄に書けば拾って再開する（§8）。

```
while backlog/ に消化可能タスクがあり、かつ予算が残る:
   ⓪ needs/ のフィードバック取込（ブロック解除）＋ inbox 取込 ＋ triage（rot 含む）
   ① 優先順位付け（kiro / none）＋ policy 上書き（人が勝つ）
   ② 最優先タスクを kiro-flow へ（act。location=local/daemon/remote）
   ③ verify ゲート → 回帰ゲート → パス保護 → 偽done/flake 検査
        PASS かつ全ゲート通過 → done（archive/ へ退避・納品書）
        NG → 積み直す（retry）／繰り返し NG・判断不能は人へ（DR学習→自律裁定→needs/）
終了: drained / budget / cost。--watch なら以後も backlog/ を監視（エージェントは待機しない）
```

---

## 3. 不変条件（外周を足しても破らないもの）

すべての機能追加はこの不変条件を保つ。本書の各機構はこれを「緩める」ことはせず、安全側に倒す。

1. **done は verify（acceptance）の終了コード 0 でしか確定しない。** 投入・スキル・設定・敵対的レビューの
   どれも自己申告 done を作れない。安全ゲートはタスクを「足す／止める」方向のみ。
2. **必ず有限回で止まる。** 内側 `run`（drained / budget=cycles・time / cost=tokens・usd）＋プロジェクト層
   （改善サイクル上限・stall）。`--watch` でも idle はエージェント非起動。
3. **人の policy ＞ エージェント提案。** 設定ファイルは「既定」レイヤであり、人の `policy.md`
   （deny/pin/defer/offload/gate/protect）と決定記録の優先関係には介入しない。
4. **標準ライブラリのみ・pip 依存なし**（PyYAML は任意の上乗せ。無ければ JSON）。
5. **決定的なファイル操作で完結**。レジストリ・設定読込・発見・enqueue・収束計算は LLM を起動しない。
   知能（分解・優先順位・裁定・敵対的レビュー）は kiro-flow / kiro-cli へ委譲する。

---

## 4. 優先順位付け（正準ループ ①②）

タスクは `priority`（整数・大きいほど高優先）を**外部付与**できる（`- priority: N`）。これを踏まえ 2 段で決める。

```
① 基本順位（--planner）
     kiro（既定）… エージェント(kiro-cli)が重要度・依存・priority を加味（失敗時は none へフォールバック）
     none        … priority 降順 → 同値は最古（mtime）。決定的・kiro-cli 不要
② policy.md の人間ルールで上書き（★人間が必ず勝つ。透明性のため適用ログを残す）
     deny  … 自動実行させない → 人の判断待ちへ（実行前で止める）
     pin   … 最優先へ固定
     defer … 後回し
③ 先頭タスクを kiro-flow へ。policy 追記（hold/pin/defer）は人の判断なので決定記録に残す（§8）
```

`policy.md` の記法（値はタスク ID／タイトルの部分一致）:

```yaml
deny:    prod        # "prod" を含むタスクは自動実行しない（実行前に人の判断待ち）
pin:     T3          # T3 を最優先
defer:   cleanup     # "cleanup" を含むタスクは後回し
offload: heavy       # "heavy" を含むタスクは分散環境へ移譲（§10・--git-bus 設定時）
gate:    release     # verify PASS でも done 前に人の承認を要する（検収ゲート・§7）
protect: auth/**     # act が auth/ 配下を変更したら done せず人の承認へ（パス保護・§5）
```

### 4.1 rot 検知（古い/重複/実行不能の掃除）

triage 時に **rot** を検出して**人の判断（blocked＋needs/）へ回す**（消さず棚卸し）。

| rot | 判定 |
|-----|------|
| `unverifiable` | 消化可能だが `verify` 未定義（done 不能） |
| `duplicate` | 正規化タイトルが先行タスクと一致（先行を残し後続を回す） |
| `stale` | 消化可能だが mtime が `--rot-age-days`（既定 14）日より古い |

`run --rot` で毎 triage に組込み、`rot` サブコマンドで随時レポート（`--fix` で blocked 化）。

---

## 5. 実行と検証（正準ループ ②③）

### 5.1 act の委譲と location

最優先タスクから要求文（**完了条件＝`verify` を明示**し loop-until-done を促す）を組み立て、kiro-flow へ委譲する。
**「どこで・どう動かすか」は `--location`（既定 `auto`）に集約**。

| location | 委譲方法 | daemon | 用途 |
|----------|---------|--------|------|
| `local` | `kiro-flow run`（単発・同期） | 不要 | 既定の実体 |
| `daemon` | `submit` → `result` で done 待ち | ローカル daemon（無ければ local にフォールバック） | warm worker 再利用 |
| `remote` | `submit`（`--git`）→ `result` で done 待ち | 共有 git バスの remote daemon が必須 | 別マシンへオフロード |

- `auto` = offload 一致＋`--git-bus` → remote ／ ローカル daemon 稼働 → daemon ／ 他 → local。
  明示指定はそれを優先（remote は git-bus 必須、無ければ local）。daemon 検知は kiro-flow と同一の `flock`。
- どちらの経路でも **verify は act 完了後**に走る（submit 経路は対象 run が終端に達するまで `result` を待つ）。
- `run` 経路の kiro-flow planner は `--flow-planner`（既定 `flow-planner`。kiro-autonomous 自身の `--planner`
  とは別軸）。kiro-flow 実行体は `--kiro-flow` > `PATH` > 同梱 `tools/kiro-flow/kiro-flow.py` の順で解決。
- **要求文へのプロジェクト文脈注入**（§11.4）: charter（定義）と `decisions/<id>.md`（判断結果）、`## links`
  先の定義＋learn を有界に付与し、ワーカーがプロジェクトの目標・制約・過去の判断を踏まえて働く。

### 5.2 レーン減速（pace）

`--pace P` を 1 サイクルの下限間隔とし、実時間予算 `--max-seconds` があれば `max_seconds/max_cycles` の間隔に
均してバーストを防ぐ。`decide_pace(elapsed) = max(0, max(pace, max_seconds/max_cycles) − elapsed)`。
待機は注入可能な sleeper（テストは実時間を消費しない）。

### 5.3 検証ゲート（done 確定の唯一の根拠）

タスク自身の `verify` をローカルで実行し、**終了コード 0 のみ**を done とする。内側 LLM が「できました」と言っても
verify が通らなければ done にしない（自己申告 done の禁止）。

```
PASS（exit 0）→ 回帰ゲート → パス保護 → 偽done/flake 検査 を通れば done（archive/<id>.md へ退避）
NG（exit≠0）  → 積み直し（status=ready・retry）。--max-retries 超で人へ
verify 未定義  → done 不能。人の判断へ（needs/<id>.md）
```

### 5.4 偽 done 対策（履歴一致 verify）

`git log | grep refactor` のように **verify が「履歴の絶対状態」を見る**と、過去コミットにマッチして act が
何もしなくても done 確定する。3 層で対策（鉄則は「履歴でなく最終状態/差分を assert する」）。

- **成果参照の真正化（常時）**: DELIVERY/needs の成果参照は **act 前(baseline)以降の新規コミット/未コミット
  変更のみ**を載せ、無ければ `(変更なし)`。kiro-autonomous 自身の状態ファイルは差分から除外。
- **差分基準の環境変数（常時）**: verify 実行時に **`$KIRO_BASE_REV`（act 前 HEAD）** を渡す。
  `git log $KIRO_BASE_REV..HEAD --grep …` で差分スコープ verify が書ける。
- **no-progress ガード（opt-in）**: `--require-progress`／per-task `- expect: changes` で、verify=PASS でも
  baseline 以降に変更が無ければ done せず人へ。正当な無変更は `- expect: none` で opt-out。

### 5.5 フレーク耐性（verify_confirm）

`--verify-confirm N`（既定 1）は verify を最大 N 回再実行し、PASS/FAIL が跨いだら **flake** と判定して
**自動修正せず人へ隔離**（retry を増やさない）。揺れる verify を NG 誤読する churn や flaky PASS の偽 done を防ぐ
（flake は「テスト/環境の問題」でコード修正案件ではない、という原則）。

### 5.6 回帰ゲート（done 前のグローバル検査）

per-task の `verify` は通っても別所を壊す（巻き込み事故）。`--regression-cmd` を与えると **verify PASS 後・
done 確定前**に共通検査を走らせ、失敗したら done にせず人へ。`--regression-revert` は未コミットの作業ツリー
変更のみ best-effort で戻す（コミット/push 済みは対象外・既定 off）。

### 5.7 パス保護（safety denylist）

`policy.md` の `protect: <glob>` に一致するファイルを act が**変更したら**、verify=PASS でも done せず検収待ち
（review）に落とす。`gate`（§7）がタスク（ID/タイトル）一致なのに対し、`protect` は**変更されたパス**一致。
glob は自前実装（`*`=非スラッシュ / `**`=スラッシュ含む・`**/` は 0 階層許容）。`.env`/`**/secrets/**`/`auth/**`/
`payments/**`/`**/migrations/**`/infra 等を無人運用で自動編集させない最低ライン。remote/daemon 実行は workdir に
差分が出ないため best-effort（実行先側で守る）。

### 5.8 コスト計上（@cost）

per-task のコストは act 出力の `@cost tokens=… usd=…` 行を加算（決定的・LLM 不要。エージェントが吐かなければ 0）。
予算ゲート（§6）の根拠になり、done 時に納品書へ `- cost:` を残すので `stats` が archive 横断で累計を出す。

### 5.9 納品書（検収サマリー）

done 時に 2 段で残す（成果物は kiro-flow 経由で各リポジトリへ push される前提）:

- **個票**: `archive/<id>.md` に「## 納品書」（verify=PASS・成果参照・完了時刻・cost）。backlog と 1:1。
- **一覧（受領書）**: `DELIVERY.md` に 1 行追記（id・タイトル・検収・成果参照・完了）。

**成果参照**は決定的に取得: act 出力の PR/MR URL → commit SHA → workdir の `git log -1`（baseline 以降のみ）。

---

## 6. 収束と予算（正準ループ ④）

1 パスは必ず止まる。Loop Engineering の暴走・予算溶かしをここで潰す。

| 停止理由 | 意味 | フラグ |
|----------|------|--------|
| `drained` | 消化可能タスクが尽きた | — |
| `budget` | サイクル数 / 実時間が尽きた | `--max-cycles`(既定 20) / `--max-seconds`(0=無制限) |
| `cost` | トークン / 金額が尽きた | `--max-tokens` / `--max-cost`（0=無制限） |
| `throttle` | ソフト予算比率超過（watch は report 降格） | `--throttle`（0=off。例 0.8） |

- `blocked`/`review`（人の対応待ち）タスクは消化可能集合から外れ、ループを無限占有しない。
- **自動スロットル**: `--throttle` は `max_tokens`/`max_cost` の比率に達したらハード上限の手前で run を打ち切り、
  `--watch` 中は以降 report レベルへ降格して spend を止めつつ監視は続ける（緩やかなブレーキ）。

### watch（プロセス常駐・エージェント非待機）

`--watch` は 1 パスが終わってもプロセスを残し `backlog/` を監視する。idle 中は kiro-cli/kiro-flow を一切起動せず
（安価な FS ポーリングのみ）、`--poll` 間隔で「消化可能タスク or 新規 inbox or フィードバック」を検知して次パスを
起こす。予算は 1 パス毎に与え直す。**サブコマンド省略時（`kiro-autonomous` 単体）は `run --watch` にディスパッチ**
（PC 起動時の常駐用途を一級化。明示 `run` は不変）。

### 終了コード（非 watch 時）

| code | 条件 |
|------|------|
| 0 | `drained` かつ人の対応待ち（blocked/review）無し |
| 1 | 人の対応待ち（blocked/review）あり |
| 2 | `budget` / `cost` で停止 |

---

## 7. 自律度（信頼を段階的に明け渡す）

「いきなり無人運用にしない」を一級化する。`--level` が run 全体のダイヤル、タスク毎に上書きでき、実績で自動調整できる。

| level | act | done | 用途 |
|-------|-----|------|------|
| `report` | しない | — | 「何を・どの順で回すか」だけ報告（消化しない）。actionable から除外され `drained` で収束 |
| `assisted` | する | 人が `approve`（全件 review） | 実行するが done は必ず人が承認。検証つき小修正で慣らす |
| `unattended`（既定） | する | 自動（ゲート通過時） | protect/gate/regression を通れば自動 done |

### 7.1 タスク単位の自律度（`- level:` / `- track:`）

実効 level = **`- level:`（明示・ピン）＞ track の自動昇格 ＞ グローバル `--level`**。安全網（`protect`/`gate`/
`review: human`/`regression`）は level に依らず**締める方向で常時上乗せ**（緩めても残る）。同じ backlog に
「決済は assisted・typo は unattended・risky は report」を混在できる。

### 7.2 実績連動の自動昇格（opt-in `--auto-level`）

`- track: <名前>` を付けた同種群の**手戻り率**で level を自動で上げ下げする。直近 `level_window`（既定 10）件で
連続 clean が `level_promote_after`（既定 5）に達し `rework_rate ≤ level_rework_max`（既定 0）なら 1 段昇格、
手戻り（差し戻し/回帰/偽done/revert）で 1 段降格・**2 回で `assisted` にピンして自動管理を停止**。
ceiling は既定 `assisted`（`--auto-level-max unattended` で完全無人化への自動到達を解禁）。track の状態は
`autonomy/<track>.json`、遷移は `decisions/` に根拠付きで監査記録。`max_retries` 内で自己回復した NG retry は
手戻りに数えない。track はタイトル類似でなく**明示 opt-in のみ**（曖昧マッチで高リスクが緩むのを避ける）。

### 7.3 検収ゲート（verify=PASS でも人の承認）

verify は機械的合否でしかない。本番反映・不可逆・課金・質的受け入れ・巻き込み事故のために、done 確定の手前で
止めて承認待ち（`review`）にできる（既定はゲート無し）。タスク単位 `- review: human`／policy 単位 `gate:`。
`approve <id>` で done 確定（保持した成果参照で納品書）、フィードバックで差し戻し。`deny`（実行前で止める）との
違いは**止める位置**（gate は実行・verify は通すが done 確定前）。

### 7.4 Loop Readiness 監査（audit）

backlog/policy/config/state から**決定的に採点**し「いまどの自律度で無人運用してよいか」を機械判定する
（エージェント不要）。レベル `L0 Draft→L1 Report→L2 Assisted→L3 Unattended`・スコア 0–100・赤旗・提案を出す。
L3 は verify 健全＋コスト予算＋パス保護デニーリスト＋掃除が揃い critical 赤旗が無いときのみ。`audit --strict` は
スコア<40 か critical 赤旗で exit 2（CI ゲート）。

---

## 8. 人の判断とフィードバック（needs / decisions）

タスクが「人の判断へ」回ると案件毎 `needs/<id>.md`（判断待ち／検収待ち）を生成する。人とループの非同期接点。

### 8.1 フィードバック往復（人 → ループ）

`needs/<id>.md` の「## フィードバック」欄に記入して保存すると、次パス先頭で `ingest_feedback` が拾い、対象を
ブロック解除（ready）→ 本文を次 act の要求文へ添付 → `decisions/<id>.md` に記録 → needs を消費。
**書きかけ誤発火を 3 層で防ぐ**: ①チェックボックス `[x]` で確定（必須シグナル。空でも `[x]` なら「そのまま再実行」）
②新規は `status: draft`（消化対象外・watch も起こさない）③`--watch` は最終保存から `--debounce`（既定 3 秒）待つ。

### 8.2 決定記録（DR）

人が境界で判断した瞬間を承認操作と**不可分**に記録する（痕跡なしに承認できない）。案件毎 `decisions/<id>.md` に
append-only（ADR の task 版）。操作: `needs` 記入（feedback-resume）／`approve`／`hold`（policy deny 追加）／
`reprioritize --pin|--defer`。

```
## DR-0001  2026-06-17  actor: <user>
- context : T12 に人のフィードバック
- action  : feedback-resume
- reason  : テスト側の期待値が誤っていた
- affects : T12 → ready
- learn   : <タイトル> :: <次回への指示>     # 任意。DR 学習の材料
```

### 8.3 自律裁定（needs へ送る前の kiro-cli 門番・既定 on）

人へ回す唯一の経路（verify 失敗 → escalate）に門番を挟む。kiro-cli に「ループ内で積み直して解けるか（requeue）／
人が要るか（escalate）」を判断させ、requeue なら needs を作らず ready に戻し guidance を次 act へ注入する。
**判断材料**は失敗理由＋`decisions/<id>.md`（過去の判断）＋journal の当該行＋feedback/note（決定的・有界）。
**安全側フォールバック**: 例外・不正出力・kiro-cli 不在・少しでも意思決定/承認/リスクが絡めば必ず escalate。
1 タスクあたり `--adjudicate-max`（既定 1）回まで＝必ず有限回で人へ。`policy.deny`/`hold`/`rot`・verify 未定義は
裁定対象外（人の上書き・鉄則を維持）。`--no-auto-adjudicate` で無効化。

### 8.4 DR 学習（通知を減らす）

タスクが繰り返し NG で人へ回りそうになると、他案件の `learn` から**タイトル類似（Jaccard ≥ `--learn-threshold`、
既定 0.5）**の過去の指示を探し、見つかれば blocked にせず反映して自動再実行（`auto-resolve` を記録し通知抑制）。
自動適用は 1 タスク 1 回。**順序は DR 学習（決定的）→ 自律裁定（kiro-cli）→ 人**の三段で人の判断を絞る。`--no-learn` で無効化。

### 8.5 ltm-use への学習昇格（プロジェクト横断・LLM 不要）

`--ltm` で `decisions/` の学習を `ltm-use`（セッション横断の長期記憶）へ**昇格**し、別プロジェクトからも再利用する。
すべて決定的なファイル操作（LLM を起動しない）。**昇格の根拠は実績**: ある `learn` が `auto-resolve` で実際に効いた
回数が `--promote-threshold`（既定 2）以上で、`<ltm-home>/memory/home/memories/kiro-autonomous/` へ frontmatter 付き
Markdown を書く（`ltm-home` = `--ltm-home`→`KIRO_LTM_HOME`→`~/.claude`）。recall は「ローカル `decisions/` →
ヒット無しなら ltm-use home」の順でフォールバック。冪等（`- promoted:` で二重昇格しない）・グレースフル（`--ltm`
無し/home 未解決なら何もしない）。入口は `run --ltm`（末尾で自動昇格）／`promote`（手動）。

---

## 9. タスクの自走（backlog の継ぎ足し）

### 9.1 自己生成（followup）

完了タスクから派生を生み、人の投入に依存せずループが仕事を継ぎ足す。2 経路: 静的（タスクの
`- followup: <title> :: <verify>`）／動的（act 出力の `@followup …` 行）。verify があれば `ready`（同 run で自走）、
無ければ `inbox`（人へ）。`--max-spawn`（既定 20）で 1 run の生成数を上限＝暴走しない。生成は `decisions/` に記録。

### 9.2 依存（DAG・`- after:`）

`- after: T1, T2` の依存が done（=archive へ退避）になるまで消化対象に入らない。依存が blocked/review で止まれば
従属も待つ。平坦な priority＋古さにトポロジカル順序を重ねる。

### 9.3 取り込み口（enqueue / inbox）

外部ソース（webhook/メール/issue 抽出）は**薄いアダプタで取り込み口へ流し込む**（コアは stdlib のみ・ネットワーク
非依存）。`enqueue` は CLI フラグ or stdin/JSON（1 件/配列）から検証して投入。`<project>/inbox/` に置かれた
`.json`/`.md` は run/watch の各パス冒頭で取り込み元ファイルを消す。**verify を持たない投入は必ず `inbox`**＝人の
triage 行き（鉄則）。

---

## 10. 並列・分散・多重稼働

### 10.1 並列消費（concurrency）

依存解決済みのタスクは互いに独立なので、`--concurrency N`（既定 1）で先頭から最大 N 件を **daemon/remote へ並行
submit** し、実体の並列は kiro-flow の worker に委ねる。**実行の重い部分だけ並列化**し、verify と
done/archive/decisions/派生生成といったローカル状態変更は逐次のまま（競合回避）。local 単発 run は逐次。
1 サイクル=1 タスクの計上・予算は不変（バッチ幅は残サイクル予算も超えない）。

### 10.2 原子的クレーム（二重実行防止）

`--concurrency` や同一 backlog を複数プロセス/ホストで回すと取り合いが起きる。実行前に `claims/<id>.lock` を
`O_CREAT|O_EXCL` で原子的に確保できた者だけが回す。取得後に disk を再検証（既に archive/非 consumable なら実行
せず解放）。owner 失踪は TTL（act+verify+猶予）超で奪取、正常時は即解放。同一 backlog を複数ホストで回しても同一
タスクは二度実行されない。

### 10.3 分散移譲（remote）

`--git-bus <共有 git リポジトリ>`＋`policy.md` の `offload: <パターン>` 一致タスクは `remote` に解決され、kiro-flow
の `--git` 分散バス越しに別マシンの daemon へ submit してオフロードする（完了を待ってから verify）。

### 10.4 稼働インスタンスの発見（グローバルレジストリ）

`run`（特に `--watch`）中、監視中の root と OS/WSL 情報を**グローバル**な共通 home
（`$KIRO_AUTONOMOUS_HOME`→`~/.kiro-autonomous`）の `instances/<pid>.json` に登録し、終了で消す（死活は PID、一覧時に
prune）。`instances [--json]` で外部操作者が「いまどのフォルダ（プロジェクト root）を見ているか」を発見し、
WSL/Windows をまたいで同じ backlog/needs へ読み書きできる（`runtime`/`wsl_distro`/`root_windows` を best-effort 併記）。
**別ホスト発見**は共有レジストリ（NFS/同期/git チェックアウト・`--registry`/`KIRO_AUTONOMOUS_REGISTRY`）へも各ホストが
レコードを書き、生死は自ホスト=PID・別ホスト=heartbeat 鮮度（ttl=`max(90s, poll×3)`）で判定。core は決定的ファイル
操作のみ・ネットワークは共有先の仕組みが担う。

### 10.5 常駐ライフサイクル（start / stop / restart）

レジストリの上に常駐の明示操作を載せる。`start` は `run --watch` を `start_new_session` で切り離し、ログを
`~/.kiro-autonomous/logs/<root>.log` へ（重複監視は既定で拒否・`--force`）。`stop` は graceful（SIGTERM→居残りのみ
SIGKILL・自分自身は止めない）。`restart` は同じプロジェクトを止めてから起動。いずれも `--project` でプロジェクトを
選ぶ（§12）。実行時設定は設定ファイルに寄せる思想で `start` は個別 run フラグを取らない。

---

## 11. プロジェクト層（charter 駆動の長期改善ループ）

backlog の上に、人が書く**目標**から逆算する evaluator-optimizer のもう一段（`kiro-autonomous project`）。backlog を
消化して `drained` で止まる正準ループに対し、「**枯渇**」と「**目標達成**」を分離して長期に回す。

### 11.1 charter.md（人が書く唯一の最上位入力）

`<project>/charter.md`（人専管・正典は `charter.md.example`）。

```markdown
# Charter: <name>          # name から project id を生成（ASCII 推奨。日本語のみは "project"）
## goal                    # 北極星（自然言語）
## constraints             # 守る境界
## assumptions             # 前提
## deliverables            # 成果物
## acceptance              # 受入 verify＝**プロジェクト done の唯一の根拠**（各行 exit 0 で PASS）
- `pytest -q tests/`
## links                   # 任意。他プロジェクトの定義＋判断を横展開で取り込む（§12.3）
- shared-conventions
```

`acceptance` はタスクの verify と同じ鉄則（履歴でなく最終状態/差分・`$KIRO_BASE_REV` 利用可）。acceptance を持たない
charter は done 判定不能＝必ず人へ。

### 11.2 三相ループ

```
① plan     charter をエージェントに分解させ [{title, verify}] を enqueue（冪等＝既存と類似タイトルは投入しない）
② execute  既存の正準ループ run を drained まで回す（§2–10 のゲートは全て温存・無改造で内側呼び出し）
③ evaluate acceptance を実行 → 全 PASS か判定（＋opt-in 敵対的レビュー）
              未達/指摘 → 改善タスクを生成して次サイクル（未達 acceptance はそれ自体を verify とする）
              全 PASS かつ改善ゼロ → milestone gate（needs/<project>.md）で人へ
```

- **plan/評価の知能は委譲**（kiro-cli。`kiro-flow run --planner flow-planner` へ差し替えは planner 注入点の交換で可能）。
  enqueue・acceptance 実行・収束計算は本体が決定的に行う。
- **敵対的レビュー（opt-in `--review-project`）**: acceptance 全 PASS でも「短絡的達成（弱い verify を通しただけ）」を
  疑い、成果物群 vs goal/deliverables を批判させて改善タスク化する。

### 11.3 収束・milestone gate

| 停止理由 | 意味 | 条件 |
|----------|------|------|
| `accepted` | 人が milestone を承認（プロジェクト done） | acceptance 全 PASS かつ受領 |
| `converged` | 全 PASS・改善ゼロ → 人へ提示 | milestone gate（人待ち） |
| `project-budget` | 改善サイクル/内側予算の上限 | `--max-project-cycles`（既定 5） |
| `project-cost` | 累計コスト上限 | `--max-project-cost`（run のコストを横断加算） |
| `no-progress` | acceptance PASS 数が増えない | 連続 `--project-stall`（既定 2）で人へ |

収束候補は即 done にせず `needs/<project>.md` を生成。人は **`approve <project>` で完了確定（最終納品書）**／
**charter.md を更新して次フェーズへ続行**／policy・feedback で方向修正／`hold` で保留。`--watch` は milestone 提示後も
常駐し charter 更新/フィードバックを poll で拾って再開。状態は `<project>/project.json`、各評価は `decisions/` に
`project-evaluate` で監査記録。終了コードは 0=accepted／1=人の対応待ち／2=予算停止。

### 11.4 ワーカーへの定義/判断の注入

kiro-flow へ委譲する act 依頼（`build_request`）に、**charter（定義）と `decisions/<id>.md`（needs の判断結果）**を
有界に注入する（charter 1400 字・decisions 末尾 1000 字）。**`project` でも通常 `run` でも**、charter.md が存在すれば
全 act に定義が乗る（無ければ従来どおり空＝後方互換）。`## links` 先プロジェクトの定義（`charter_context`）と判断
=`- learn:`（`linked_learnings_context`）も横展開で取り込む。注入は依頼文字列の組み立てのみ＝決定的・不変条件不変。

---

## 12. 複数プロジェクト構成（プロジェクト > バックログ）

### 12.1 レイアウトと `--project`

**プロジェクトが最上位コンテナ**で、`<root>/projects/<name>/` に 1 プロジェクト＝1 セットを集約する。**全て
per-project**（backlog/needs/decisions/archive/charter/policy/journal/DELIVERY/project.json/autonomy/bus/inbox/claims）。
横断は instances レジストリ（§10.4・グローバル）と charter リンク（§12.3）のみ。全サブコマンドに `--project <name>`
（既定 `default`）。effective root = `<root>/projects/<safe(name)>/`。ディレクトリ名は unicode を保つ FS セーフ化
（`/ \ : * ? " < > |`・制御文字を `_` 化）。実装は build_config の root 計算を 1 段深くするだけで、全 per-project パスは
`backlog.parent`（=project root）から派生するため自動的に配下へ移る（Config 構造・既存ロジックは不変）。

### 12.2 enqueue とプロジェクト作成 / 分離

`enqueue --project X` でそのプロジェクトへ積む（`ensure_dirs` が無ければ作成）。**未指定なら default を作成**して積む。
needs/decisions/policy/journal/検収ゲート/自律裁定/DR 学習は**そのプロジェクト内に閉じる**（別プロジェクトの判断が
混ざらない）。`approve`/`needs`/`run`/`project`/`start` も `--project` で選ぶ。milestone/state の id は project 名を
一次採用し、未設定（Config 直接構築のテスト等）は charter 名から導出（後方互換）。

### 12.3 横展開リンク（charter `## links`）

charter に `## links` を書くと、**リンク先プロジェクトの定義（charter）と判断（decisions の `- learn:`）**を act
ワーカー文脈に取り込む（横断 recall）。名前は `<root>/projects/<name>`、`/`・`..` を含めば project root からの相対。
1 階層・自己/重複は無視・有界。ltm-use（実績で自動昇格する横断記憶）に対し、charter リンクは**人が明示した参照先**を
確実に引く（予測可能な opt-in）。

> 方針（本構成の前提）: **全て per-project ／ 新レイアウトのみ（flat 互換は持たない）／ リンクは定義＋判断を取込**。

---

## 13. データモデル・ディレクトリ構成

### backlog/<id>.md（案件毎 1 ファイル。stem が id の正）

```markdown
## <id>: <タイトル>
- status : inbox | draft | ready | doing | done | blocked | review
- source : human | triage | followup | enqueue | inbox | charter
- priority: 0          # 外部付与の優先度（大ほど高）
- verify : `終了コード0をPASSとみなすシェルコマンド`   # done の唯一の根拠（履歴でなく最終状態/差分）
- retries: 0
- review : human       # 任意。検収ゲート（§7.3）
- level  : report|assisted|unattended   # 任意。タスク単位の自律度（§7.1）
- track  : <名前>      # 任意。--auto-level の同種群（§7.2）
- after  : T1, T2      # 任意。依存（§9.2）
- expect : changes|none # 任意。偽 done 対策（§5.4）
- followup: 次の作業 :: `verify`         # 任意・複数可（§9.1）
- note   : 任意（保持される）
```

`verify` のバッククォートは除去。既知外フィールドは順序保持で書き戻す。`ready` を消化、`inbox` は triage で `ready` 化、
`draft` は消化対象外。**done は `archive/<id>.md` へ退避**（`--no-archive` で削除）。

### ディレクトリ構成

```
.kiro-autonomous/                  ← コンテナ（--root）。projects/ を束ねる
  projects/
    default/                       ← 1 プロジェクト（--project。未指定はこれを作成）
      charter.md  project.json  policy.md  journal.md  DELIVERY.md  run-log.jsonl
      backlog/  needs/  decisions/  archive/  inbox/  claims/  autonomy/  bus/
    <other>/                       ← 併存可（同じ一式）
~/.kiro-autonomous/                ← グローバル。instances/（稼働発見）・logs/
```

`journal.md`＝機械の人間可読ログ、`policy.md`＝人の常設指示、`needs/decisions/archive`＝案件毎、`bus/`＝kiro-flow の
run 状態（一時。local run 後に `_cleanup_bus` が runs/inbox を削除。`--no-cleanup` で保持）。

---

## 14. CLI・設定ファイル

| コマンド | 役割 |
|----------|------|
| （省略）/ `run` [`--watch`] | 正準ループ（省略時は `run --watch`）。`--watch` で常駐監視 |
| `project` [`--watch`] | charter 駆動の plan→execute→evaluate（§11） |
| `triage` | 優先順位付けのみ（inbox→ready 昇格・policy 適用） |
| `needs` | 人の判断待ち（blocked / 検収待ち review）を表示 |
| `enqueue` | 取り込み口（CLI/stdin/JSON から backlog タスクを作る・`--project`） |
| `approve <id>` / `hold <id>` / `reprioritize <id> --pin\|--defer` | 決定記録を残す人の操作 |
| `stats` / `runlog` / `audit` | 計測・構造化ログ・Loop Readiness 採点（§15） |
| `rot` [`--fix`] / `promote` | rot 検知・掃除 ／ 学習の ltm 昇格 |
| `instances` / `start` / `stop` / `restart` | 稼働発見・常駐ライフサイクル（§10.4–10.5） |

主なフラグ（抜粋）: `--project` `--root` `--planner{kiro,none}` `--flow-planner` `--location{auto,local,daemon,remote}`
`--executor{kiro,stub}` `--level` `--auto-level[-max]` `--max-cycles/-seconds/-tokens/-cost` `--throttle`
`--concurrency` `--verify-confirm` `--require-progress` `--regression-cmd[-revert]` `--auto-adjudicate` `--learn`
`--ltm` `--rot` `--watch` `--poll` `--charter` `--review-project` `--max-project-cycles/-cost` `--project-stall`。

### 設定ファイル（CLI > 設定ファイル > 組み込み既定）

環境ごと・常駐ごとに決まる値を `.kiro/kiro-autonomous.{yaml,yml,json}` に書ける（探索: `--config` 明示 → `./.kiro/`
→ `~/.kiro/`）。YAML は PyYAML 任意・無ければ JSON フォールバック。CLI default を None にし `resolve_config` が
「CLI 未指定キーだけ 設定ファイル→既定 で埋める」。スカラ＋真偽フラグ（三値 `--flag`/`--no-flag`）が対象、個別パス
上書きと実行限定フラグは CLI 専用。常駐は systemd の `ExecStart` を `kiro-autonomous --project <name>` だけにして
調整はこのファイルで完結できる（プロジェクト毎にユニットを分ける）。

---

## 15. 計測・テスト・設計史

### 計測（stats / runlog）

`stats` は archive/decisions/DELIVERY/backlog から決定的に KPI を集計（完了・納品・status 別 backlog・人対応待ち・
**自動化率**=auto-resolve＋auto-adjudicate÷自動＋人・**一発 done 率**=retry 0・累計トークン/金額）。`run-log.jsonl` は
run 毎 1 行 JSON（reason/done/blocked/review/archived/escalations/tokens/cost/duration/level）で監視に流せる。

### テスト方針

`tools/kiro-autonomous/tests/test_kiro_autonomous.py`（標準 `unittest`）。kiro-flow/kiro-cli を呼ばずに検証（stub・
注入）。パース/書き戻し・verify ゲート・状態機械・優先順位/収束・location/pace・フィードバック往復・watch・決定記録・
コスト予算・followup/依存・回帰/パス保護・自己監査・自律度（per-task/auto-level）・原子的クレーム・run-log/throttle・
flake 耐性・偽 done 対策・プロジェクト層（plan/evaluate/収束/milestone/承認/敵対的レビュー/文脈注入）・複数プロジェクト
（per-project 分離/FS セーフ/charter リンク）。kiro-flow stub 統合は無ければ skip。

```bash
KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-autonomous/tests
```

### 設計史（本書が統合した分冊）

本書は次の分冊を統合・再構成したもの（個別ファイルは廃止。git 履歴に残る）:

- `2026-06-16-kiro-autonomous-mvp-design`（正準ループ本体）
- `2026-06-19-kiro-autonomous-ops-design`（外部操作レイヤ＋中核機能 §8.1–8.15）
- `2026-06-21-kiro-autonomous-per-task-autonomy-design`（タスク単位の自律度・自動昇格）
- `2026-06-21-kiro-autonomous-project-loop-design`（charter 駆動のプロジェクト層・文脈注入）
- `2026-06-21-kiro-autonomous-multi-project-design`（プロジェクト最上位化・複数併存・リンク）

### 非目標・拡張余地

- plan 分解の kiro-flow バックエンド差し替え（現状は planner 注入で kiro-cli）。
- 複数プロジェクト横断の優先度調整・ポートフォリオスケジューラ（まずは各プロジェクト独立）。
- charter リンクの循環/推移解決（MVP は 1 階層）。flat→projects 自動移行（方針: 新レイアウトのみ）。
- 既製の外部アダプタ同梱（GitHub issue/メール → `enqueue --json`）・回帰ゲートの本格ロールバック。

いずれも本書の不変条件（§3）を保ったまま「外周を足す」方針で段階追加できる。
