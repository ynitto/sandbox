# kiro-autonomous — Loop Engineering MVP 設計書

> 作成日: 2026-06-16 ／ 更新日: 2026-06-17（正準ループ確定に伴い全面改訂）
> 対象ブランチ: `claude/determined-cray-dthvbi`
> 関連ファイル: `tools/kiro-autonomous/kiro-autonomous.py`, `tools/kiro-autonomous/tests/test_kiro_autonomous.py`,
> `tools/kiro-autonomous/README.md`, `tools/kiro-autonomous/backlog.md.example`,
> `.github/instructions/kiro-autonomous.instructions.md`, `tools/kiro-flow/`
>
> 名称 `kiro-autonomous`: バックログを**自律的に**優先順位付け・実行・検証・収束させ、人の判断が
> 要る分だけ差し戻すループ。人がプロンプトを毎回投げ込まなくても回り続けることを表す。
> `policy.md`＝人による上書き、`decisions/`＝判断の台帳。
> `kiro-` 接頭辞は実行を kiro-flow＝kiro-cli に委譲するため（kiro-loop / kiro-flow に倣う）。

---

## 1. 概要

Loop Engineering（「プロンプトを書く人」をやめ「プロンプトを出し続けるループ＝システム」を
設計する）の **MVP**。仕様駆動（SDD）の枠組みは意図的に外す。

kiro-autonomous は **バックログを優先順位付けし、最優先タスクを kiro-flow に実行させ、結果を
検証し、ダメなら積み直す——これを backlog が尽きるか予算が尽きるまで繰り返す制御層**。
人間がプロンプトを毎サイクル投げ込まなくても自律的に回り、人の判断が要った時はそれを
決定記録に残す。

```
[Trigger]  いつ走らせるか   ← cron / systemd timer / 手動 / イベント
   │
[Control]  kiro-autonomous     ← 優先順位付け・検証ゲート・積み直し・収束・決定記録（本書）
   │
[Execute]  kiro-flow run    ← 最優先タスクの act（分解・並列・内側反復）
   │
[Where]    location          ← local（run）/ daemon・remote（submit）で kiro-flow を起動
```

---

## 2. 正準ループ（この 5 点が仕様の背骨）

kiro-autonomous の動作は以下に**正準化**される。他のすべての機構（優先順位付け・通知・決定記録）は
この 5 点に従属する。

1. **kiro-autonomous は `backlog/`（案件毎ファイル `backlog/<id>.md`）を読み優先順位をつけ、
   最も優先順位の高いタスクを kiro-flow に投げる。**
2. **優先順位付けは `--planner` で選ぶ。`kiro`（既定）はエージェントが外部付与の `priority` も
   加味して並べ替え、`none` は `priority` 降順→同値は最古（FIFO）。** 人間は `policy.md` で
   この順位を上書きでき、その上書きは決定記録に残る（§4）。
3. **kiro-autonomous は kiro-flow の結果を確認し、検証する。done はファイルを archive/ へ退避し、
   検証 NG であれば backlog に積み直す。** 検証＝タスク自身の `verify` の終了コード 0 のみを done の根拠とする（§5）。
4. **上記を backlog が無くなるか予算（サイクル数/実時間）が尽きるまで繰り返す。`--watch` の場合は
   尽きてもプロセスは生存して backlog/ を監視し続ける（ただし idle 中はエージェントを起動しない）（§6）。**
5. **ユーザーによる判断は kiro-autonomous が案件毎の決定記録 `decisions/<id>.md` に保存する。
   `needs/<id>.md` のフィードバック欄に書き込めば拾って再開する（§7・§8）。**

```
        ┌─────────────────────────────────────────────────────────────┐
        │ while backlog/ に消化可能タスクがあり、かつ予算が残る:          │
        │   ⓪ needs/<id>.md のフィードバックを取り込み（ブロック解除）    │
        │   ① 優先順位付け（kiro=エージェント / none=priority＋古さ）＋ policy │
        │   ② 最優先タスクを kiro-flow run に投げる（act）                │
        │   ③ verify ゲートで検証                                        │
        │        PASS → done（backlog/<id>.md を archive/ へ退避）        │
        │        NG   → 積み直す（retry）／判断不能なら人へ（needs/）      │
        │ 終了: drained または budget。--watch なら以後も backlog/ を監視 │
        │       （新規タスク/フィードバック待ち。エージェントは待機しない）  │
        └─────────────────────────────────────────────────────────────┘
   人の判断（上書き・承認・打ち切り・フィードバック）は decisions/<id>.md に記録（⑤）
```

---

## 3. 背景・目的

このリポジトリは Loop Engineering の 6 プリミティブ（Scheduling / Worktrees / Skills / MCP /
Sub-agents / Memory・State）を満たす部品（`kiro-loop`, `kiro-flow`, `ltm-use`,
`council-system`, `statemachine-use` 等）を既に持つが、**自律ループとして閉じておらず**、
人間がトリガを引き・合否を見て・次を指示し・バックログを手で仕分ける手作業が残っていた。

### バックログは複数の系で循環する

`backlog.md` は均質な1本のリストではない。出自の違う系が混ざり、**人 → loop → 人 → loop** と
双方向に循環する。`source` 列でこの系を表す。

| source | 出自 | 例 |
|--------|------|----|
| `human` | 人が追加（インバウンド） | その場の依頼。verify 未定義のことがある |
| `triage` | 機械が生成 | 定期 triage が積んだもの |
| `followup` | 実行後の差し戻し | 検証 NG・要修正で戻ってきたもの |

### メンテコストの正体（削るもの）

人が手でやらされていること＝決定記録・通知で消すコスト:

- 検証 NG や判断保留のタスクを集めて見に行き、直したら戻す
- 「今 何が自分の判断待ちか」をバックログ全体から探す
- 優先順位を 50 件手で並べ替える

→ **優先順位付けは kiro-cli が肩代わり**、**人の判断が要る分だけ通知**、**判断は決定記録に残す**。

---

## 4. 優先順位付け（正準ループ ①②）

タスクは `priority`（整数・大きいほど高優先）を**外部で付与**できる（`backlog/<id>.md` の
`- priority: N`）。これを踏まえて2段で順位を決める:

```
① 基本順位（--planner で戦略を選ぶ）
     kiro（既定）… エージェント（kiro-cli）が重要度・依存に加え **priority も加味**して並べ替え
                    （失敗時は none と同じ順にフォールバック）
     none        … **priority 降順 → 同値は最古（mtime）優先**の決定的順位（kiro-cli 不要）
② policy.md の人間ルールで上書き                     → 最終順位   ★人間ルールが必ず勝つ
       deny  … 自動実行させない（denylist）→ 人の判断待ちへ
       pin   … 強制的に上へ
       defer … 下げる
③ 最終順位の先頭タスクを kiro-flow に投げる
```

- **precedence は厳格に「人間 policy ＞ 基本順位（priority/エージェント）」**。
- `--planner none` は kiro-cli 無しで決定的（旧 `stub` を改名）。`priority` を外部で書けば順序を制御できる。
- 透明性のため **基本順位と、どの policy ルールが上書きしたかをログ**する。
- `policy.md` への追記（hold/pin/defer）は**人間の判断**なので決定記録に残す（§7）。

### policy.md の記法（MVP）

`deny` / `pin` / `defer` の3種。値は**タスクID またはタイトル部分一致**でマッチ。

```yaml
deny:    prod      # タイトル/IDに "prod" を含むタスクは自動実行しない（人の判断待ち）
pin:     T3        # T3 を最優先に固定
defer:   cleanup   # "cleanup" を含むタスクは後回し
offload: heavy     # "heavy" を含むタスクは分散環境（git バス）へ移譲（§5）
```

---

## 5. 実行と検証（正準ループ ②③）

### act の委譲（kiro-flow の起動方法を location に統合）

最優先タスクから要求文（**完了条件＝`verify` を明示**し loop-until-done を促す）を組み立て、
kiro-flow へ委譲する。**「どこで・どう動かすか」は `location`（local / daemon / remote）に集約**する。

#### kiro-flow の run / submit / daemon（仕様の再確認）

| 起動 | 同期/非同期 | daemon 要否 | planner/executor の制御 | 用途 |
|------|------------|------------|------------------------|------|
| `run` | 同期（自分で orchestrator＋worker を起動し終端まで待つ） | **不要** | kiro-autonomous が指定可 | 単発・自己完結 |
| `submit` | 非同期（inbox に投函するだけ） | **必要**（daemon が拾って処理） | **daemon 側が保持**（submit では渡せない） | daemon/分散 |
| `daemon` | 常駐（inbox 監視→orchestrator/worker をオンデマンド起動。git バスで複数マシン分散） | — | daemon 起動時に固定 | warm pool・分散 |

→ **結論（daemon 連携の必要性）**: 逐次処理の MVP では **`run`（local）で十分**＝daemon 不要。
daemon/submit が要るのは **(a) 別マシンへ真にオフロード（remote）** か **(b) warm worker を再利用したい
（daemon）** 場合のみ。よって3モードを `location` に統合する。

#### location の3モード

| location | 委譲方法 | daemon | 説明 |
|----------|---------|--------|------|
| `local` | `kiro-flow --bus <bus> run …` | 不要 | 単発・同期。既定の実体 |
| `daemon` | `submit` → `result --json` ポーリングで done 待ち | ローカルバスの daemon（無ければ `local` にフォールバック） | warm worker 再利用 |
| `remote` | `submit`（`--git <bus>`）→ `result` ポーリング | **共有 git バスの remote daemon が必須** | 別マシンへオフロード（フォールバックしない） |

- 決定 `decide_location(task)`:
  - `--location auto`（既定）= offload 一致かつ git-bus → `remote` ／ ローカル daemon 稼働 → `daemon` ／ 他 → `local`。
  - 明示（`local`/`daemon`/`remote`）はそれを優先（`remote` は git-bus 必須、無ければ `local`）。
  - 移譲対象は `policy.md` の `offload:`（`deny/pin/defer` と同じ人間ルール）で指定。
- **daemon 検知**は kiro-flow と同一規則のロック（`{tempdir}/kiro-flow-locks/daemon-<hash>.lock` を
  `flock` 非ブロックで試行）で行う。
- どちらの経路でも **verify ゲートは act 完了後**に走る。submit 経路は対象 run が終端に達するまで
  `result --run-id <id> --json` を待ってから検証する（非同期 submit を同期境界に揃える）。
- `run` 経路の planner は `--flow-planner`（kiro-flow 用、既定 `flow-planner`）。これは kiro-autonomous
  自身の優先順位付け `--planner{kiro,none}` とは別。submit 経路の planner/executor は daemon 側が持つ。
- kiro-flow 実行体は `--kiro-flow` > `PATH` > 同梱 `tools/kiro-flow/kiro-flow.py` の順で解決する。

### 5.2 レーン減速（pace 次元 / 予算で均す）

`decide` のもう一つの拡張次元として **サイクル間の待機（レーン速度）** を決める。`--pace P` を
1サイクルの下限間隔（レート制限）とし、実時間予算 `--max-seconds` があれば
`max_seconds / max_cycles` の間隔に均してバーストを防ぐ（予算を一気に消費しない）。
既に間隔ぶん経過していれば待たない。待機は注入可能な sleeper で行い、テストでは実時間を消費しない。

```
decide_pace(cycle_elapsed) = max(0, max(pace, max_seconds/max_cycles) − cycle_elapsed)
```

### 検証ゲート（done 確定の唯一の根拠）

kiro-flow が結果を返したら、kiro-autonomous が**タスク自身の `verify` をローカルで実行**し、
**終了コード 0 のみ**を done とする。kiro-flow（内側 LLM）が「できました」と言っても、
verify が通らなければ done にしない（自己申告 done の禁止）。

```
PASS（exit 0） → done（backlog/<id>.md を archive/<id>.md へ退避＝アーカイブ化）
NG（exit≠0）   → backlog に積み直す（status を ready に戻す＝retry）
verify 未定義   → done 不能。人の判断へ（needs/<id>.md 生成、§8）
```

- **done は backlog/<id>.md を `archive/<id>.md` へ移動（アーカイブ化）**（正準ループ ③）。
  backlog/ には未完だけが残り、完了は archive/ に保全される（`--no-archive` で削除に切替）。
- **検証 NG は積み直し**。次サイクル以降で再び拾われる。
- ただし **kiro-autonomous が機械的に判断できない**ケース（verify 未定義／同一タスクが繰り返し NG）は、
  無限の積み直しにせず**人の判断へ回す**（status=blocked → `needs/<id>.md` 生成 §8 →
  ユーザーが approve/フィードバックして決定記録 §7）。これが点⑤「ユーザーによる判断」の入口。

---

## 6. 収束と監視（正準ループ ④）

1パスは **2 条件のいずれか**で必ず止まる。Loop Engineering の暴走・予算溶かしをここで潰す。

| 停止理由 | 意味 | 既定 |
|----------|------|------|
| `drained` | backlog に**消化可能なタスク**（実行待ち）が無くなった | — |
| `budget` | 設定した予算（サイクル数 `--max-cycles`=20 / 実時間 `--max-seconds`=0=無制限）が尽きた | — |

- `blocked`（人の判断待ち）になったタスクは**消化可能集合から外れる**ため、ループを無限に占有しない。
- 繰り返し NG でも予算が残る限り積み直し、予算が尽きれば `budget` で停止する。

### watch（プロセス常駐・エージェント非待機）

`--watch` のとき、1パスが drained/budget で終わっても**プロセスは終了せず** `backlog/` を監視し続ける。

- idle 中は **kiro-cli/kiro-flow を一切起動しない**（`time.sleep` による安価な FS ポーリングのみ）＝
  「終了条件を満たしてもプロセスは残るが、**エージェントは待機しない**」。
- `--poll` 間隔で「消化可能タスク or 新規 inbox or フィードバック」を検知したら次のパスを起こす。
- 予算は**1パス毎**に与え直す。長寿命の常駐（cron の代替）に使える。

### 終了コード（CI 連携・非 watch 時）

| code | 条件 |
|------|------|
| 0 | `drained` かつ人の判断待ち（blocked）無し |
| 1 | 人の判断待ち（blocked）あり |
| 2 | `budget` で停止（消化未了で打ち切り） |

### 終了コード（CI 連携）

| code | 条件 |
|------|------|
| 0 | `drained` かつ人の判断待ち（blocked）無し |
| 1 | 人の判断待ち（blocked）あり |
| 2 | `budget` で停止（消化未了で打ち切り） |

---

## 7. 決定記録（正準ループ ⑤）

人が境界で判断した瞬間を、承認操作と**不可分**に記録する。＝「承認コマンドが決定記録を生む」
ので、痕跡なしに承認できない。`journal.md`（機械のサイクルログ）とは別に、**人の統治ログ**として
**案件毎の `decisions/<id>.md`** に append-only で残す（ADR の task 版。DR 番号は案件内で連番）。

```
## DR-0001  2026-06-17  actor: devilrabbit.jp@gmail.com
- context : T12 に人のフィードバック
- action  : feedback-resume（needs/T12.md の記入を反映して再開）
- reason  : テスト側の期待値が誤っていた
- affects : T12 → ready
```

操作（いずれも `decisions/<id>.md` に DR を自動追記）:

- **needs/<id>.md のフィードバック欄に記入**（最も低コスト）… §8。ブロック解除＋次 act に反映
- `approve <id> --reason …` … 人の判断待ちを修正承認して積み直し
- `hold <id> --reason …` … policy に deny を追加（denylist 化）＋ blocked
- `reprioritize <id> --pin|--defer --reason …` … policy に pin/defer

**北極星との接続**: 蓄積した DR は将来、似た事案をエージェントが過去の人間判断に倣って
自動解決する材料になり、人の判断件数そのものを減らす。MVP では記録まで。学習
（`ltm-use` への promote）は後段。

---

## 8. 通知とフィードバック往復（案件毎 needs/<id>.md）

タスクが「人の判断へ」回ると、**案件毎に `needs/<id>.md`** を生成する。これが人とループの非同期接点。

| 項目 | MVP |
|---|---|
| 生成 | タスクが `blocked`（verify 未定義／繰り返し NG／policy deny）へ遷移した時、`needs/<id>.md` を書く |
| 中身 | なぜ blocked か ＋ **「## フィードバック」記入欄**（HTML コメントのガイド付き） |
| 要約 push | 遷移時に stdout（と `--notify-cmd`）へダイジェストも出す。**遷移時だけ**（dedup） |

### フィードバック往復（人 → ループ）

人が `needs/<id>.md` の「## フィードバック」欄に記入して保存すると、次パスの先頭で
`ingest_feedback` が拾う:

1. 対象タスクを **ブロック解除（ready）**。
2. フィードバック本文を **次の act の要求文に添付**（`build_request` が「人からのフィードバック」として渡す）。
3. `decisions/<id>.md` に記録（action=feedback-resume）。
4. `needs/<id>.md` を**消費（削除）**。

これにより「実行後に人の判断を促し、修正して差し戻す」系がファイルだけで完結する。`--watch` と
組み合わせれば、人が記入した瞬間（次の poll）に自動で再開する。

---

## 9. 人間が触る面

すべて人間が低コストで編集できる。これが「人とループの接点」。

| パス | 役割 | 書く主体 |
|---|---|---|
| `backlog/<id>.md` | タスク本体（案件毎ファイル。人が追加） | 人＋システム |
| `policy.md` | 順位・実行先の上書き（`deny`/`pin`/`defer`/`offload`） | **人だけ** |
| `needs/<id>.md` | 判断待ちの通知＋**フィードバック記入欄** | システム生成・人が記入 |
| `decisions/<id>.md` | 人の判断・承認の決定記録（案件毎・append-only） | システム（人の操作から生成） |

---

## 10. データモデル

### backlog/<id>.md（State・案件毎1ファイル）

ファイル名の stem を `id` の正とする。1ファイル＝1タスク。

```markdown
## <id>: <タイトル>
- status: inbox | ready | doing | done | blocked
- source: human | triage | followup
- priority: 0          # 外部で付与する優先度（整数・大きいほど高優先。省略時 0）
- verify: `終了コード0をPASSとみなすシェルコマンド`
- retries: 0
- note: 任意（保持される）
```

- `verify` のバッククォートは除去してそのまま実行可能にする。
- 既知フィールド以外（`note`/`feedback` 等）は順序保持で書き戻す。
- `ready`（実行待ち）を消化。順序は `--planner`（kiro=エージェント／none=priority 降順→最古）。`inbox` は triage で `ready` 化。
- **done は `archive/<id>.md` へ退避**（§5）。`backlog/` には常に未完だけが残る。

### journal.md / policy.md / needs/ / decisions/ / archive/

§7・§8・§9 の通り。journal は機械の単一ログ、policy は人の常設指示（単一）、
needs・decisions・archive は**案件毎ディレクトリ**（archive は完了タスクの保全先・append-only 的）。

---

## 11. トリガー（いつ走らせるか）

**kiro-autonomous は2通りで動く**。短命（cron 等で叩く）と、常駐（`--watch`）。

| トリガー | 何をする | 適性 |
|---|---|---|
| **`--watch`（常駐）** | プロセスが生存し backlog/ を監視。新規/フィードバックで自動再開（idle はエージェント非起動） | ◎ 長寿命運用 |
| cron / systemd timer | `kiro-autonomous run` を定刻に1プロセス起動 | ◎ daily triage |
| 手動 | 人が叩く | 開発・検証 |
| イベント（issue-mailbox / gitlab-idd / PR webhook） | `backlog/<id>.md` を enqueue → watch が拾う | 反応型 |
| CI ステップ | push 時に走らせ exit code で gate | 受け入れ確認 |
| ~~kiro-loop~~ | kiro-cli にプロンプト投入のみ。CLI を exec できず不適 | ✗ |

```bash
# 常駐（新規タスク/フィードバックを監視して自動消化。idle 中はエージェントを起動しない）
kiro-autonomous run --watch --poll 10 --executor kiro

# あるいは毎朝の単発消化
0 9 * * *  cd ~/proj && kiro-autonomous run --max-cycles 30 --executor kiro
```

---

## 12. CLI

| サブコマンド | 役割 |
|---|---|
| `run` [`--watch`] | 正準ループ（優先順位付け → 実行 → 検証 → 積み直し → 収束）。`--watch` で常駐監視 |
| `triage` | 優先順位付けのみ（`--planner kiro`/`none`）＋ policy 上書き |
| `needs` | 人の判断待ち（blocked）を描画 |
| `approve <id>` | 判断待ちを修正承認して積み直し＋ DR |
| `hold <id>` | policy に deny 追加＋ DR |
| `reprioritize <id> --pin\|--defer` | policy に pin/defer ＋ DR |

主なフラグ: `--backlog`(dir) `--policy` `--decisions`(dir) `--journal` `--needs`(dir) `--workdir` `--bus`
`--planner{kiro,none}` `--flow-planner{flow-planner,kiro,stub}` `--location{auto,local,daemon,remote}`
`--executor{kiro,stub}` `--max-cycles` `--max-seconds`
`--max-iterations` `--notify-cmd` `--git-bus` `--git-branch` `--git-subdir`
`--pace` `--watch` `--poll` `--archive`(dir) `--no-archive` `--dry-run` `--once`。

---

## 13. 検証（テスト方針）

`tools/kiro-autonomous/tests/test_kiro_autonomous.py`（標準 `unittest`・20 ケース）で kiro-flow を
呼ばずに検証する（`--executor stub`／act 注入）。

- パース/書き戻し（案件毎ファイル・priority）・最古優先ロード・verify ゲート（空=NG）。
- 状態機械（PASS→done で archive/ へ退避／NG→積み直し→繰り返しで人の判断＋ `needs/<id>.md` 生成）。
- act 経路（daemon 検知で submit/run 切替・daemon ロック判定）。
- 優先順位（none=priority＋古さ／policy 上書き precedence／agent フォールバック）・収束（drained／budget）。
- location（offload→`--git`）・pace（decide_pace／sleeper 呼び出し）。
- **フィードバック往復**（`ingest_feedback` でブロック解除＋ `decisions/<id>.md` 記録＋ needs 消費）。
- **watch**（idle に投入された新規タスクを次パスで消化）。
- `approve`/`hold`/`reprioritize` が案件毎 `decisions/<id>.md` に DR を連番追記。
- kiro-flow stub 統合（無ければ skip）。

```bash
KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-autonomous/tests -v
```

---

## 14. 成功基準（MVP の達成定義）

1. 人間がプロンプトを **0 回**追加投入して、`backlog/` のタスクが N 件 `done`（archive/ へ退避）になる。
2. **誤った done が verify ゲートで止まる**（自己申告では通らない）。
3. 暴走せず**必ず止まる**（drained or 予算切れ）。`--watch` でも idle 中はエージェントを起動しない。
4. 優先順位付けが `kiro`（priority 加味）／`none`（**priority＋古さ**）で選べ、外部 priority が効く。
5. 優先順位を **`policy.md` で人間が上書きでき**、その precedence が保証される。
6. 検証 NG が **積み直され**、判断不能は人へ（`needs/<id>.md`）回る。
7. 人の承認・判断・**フィードバック**が **`decisions/<id>.md` に痕跡として必ず残る**。
8. `needs/<id>.md` への記入が次パスで拾われ、内容が次の act に反映される。

---

## 15. MVP 境界と拡張次元

| | MVP | 後段 |
|---|---|---|
| 優先順位 | **外部 priority** ＋ kiro（加味）／none（priority＋古さ）／policy 上書き | 過去 DR からの自動解決学習（ltm-use） |
| 実行・検証 | kiro-flow（local）＋ ローカル verify ゲート | — |
| 収束 | drained / budget（cycles・time）、**pace**、**`--watch` 常駐監視** | コスト予算 |
| 系 | inbox/ready/doing/done/blocked ＋ source、**案件毎ファイル＋done を archive/ へ退避** | rot 自動検知, webhook enqueue |
| 実行委譲 | **location: local=run / daemon・remote=submit＋結果待ち** | コスト連動の自動 location |
| 通知 | **案件毎 `needs/<id>.md`＋フィードバック往復**＋stdout（遷移時 dedup） | teams/メール/issue 連携 |
| 決定記録 | approve/hold/reprioritize/**feedback** → 案件毎 `decisions/<id>.md` | 過去 DR からの自動解決学習（ltm-use） |
| 実行先 | local ／ **location（offload 規則で kiro-flow `--git` 分散バスへ移譲）** | コスト連動の自動 offload 判断 |

**拡張次元**: 実行モード `location`（§5、local/daemon/remote）とレーン減速 `pace`（§5.2、
`--pace`／実時間予算で均す）はいずれも実装済み。判断を1点（`decide`）に集約してあるため、
コスト予算など今後の次元も局所で拡張できる。
