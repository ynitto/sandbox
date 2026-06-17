# task-loop — Loop Engineering MVP 設計書

> 作成日: 2026-06-16 ／ 更新日: 2026-06-17（方針変更を反映して全面改訂）
> 対象ブランチ: `claude/determined-cray-dthvbi`
> 関連ファイル: `tools/task-loop/task-loop.py`, `tools/task-loop/tests/test_task_loop.py`,
> `tools/task-loop/README.md`, `tools/task-loop/queue.md.example`,
> `.github/instructions/task-loop.instructions.md`, `tools/kiro-flow/`
>
> ※名称 `task-loop` は**暫定**。重心が「verify ゲート」から「判断＋通知の dispatcher」へ
> 移ったため改名予定（候補: ratchet / gate / foreman / dispatcher 系）。本書では暫定名を使う。

---

## 1. 概要

Loop Engineering（「プロンプトを書く人」をやめ「プロンプトを出し続けるループ＝システム」を
設計する）の **MVP**。仕様駆動（SDD）の枠組みは意図的に外す。

> **コンセプト（確定）**: 複数の系（人が追加／triage 生成／実行後の差し戻し）から来る
> タスクを、**透明な判断ルールでレーンに振り分け・実行・収束させ、人の判断が要る瞬間だけを
> 通知で差し出す**ことで、バックログ維持の手作業を最小化する制御層。
> 実行は `kiro-flow`、定期起動は cron。

道具の重心は **「判断（decide）＋ 通知（notify）」**。done を機械的に確定する verify ゲートは
その判断の一部（done/not-done の決定）として内包する。

```
[Trigger]  いつ走らせるか        ← cron / systemd timer / 手動 / イベント
   │
[Control]  task-loop（本書）     ← 判断(decide)・レーン振り分け・通知・決定記録・収束
   │
[Execute]  kiro-flow run         ← 各タスクの act（分解・並列・内側反復）
   │
[future]   pace / location       ← 予算でレーン減速 / 分散環境へ移譲（decide の拡張次元）
```

---

## 2. 背景・目的

このリポジトリは Loop Engineering の 6 プリミティブ（Scheduling / Worktrees / Skills / MCP /
Sub-agents / Memory・State）を満たす部品（`kiro-loop`, `kiro-flow`, `ltm-use`,
`council-system`, `statemachine-use` 等）を既に持つが、**自律ループとして閉じておらず**、
人間がトリガを引き・合否を見て・次を指示し・バックログを手で仕分ける手作業が残っていた。

### 解くべき問題 — タスクは「複数の系」で循環する

バックログは均質な1本のリストではない。出自もライフサイクルも違う3系統が混ざり、
**人 → loop → 人 → loop** と双方向に循環する。

1. **triage 系**（機械）— daily triage が backlog を優先度付けして loop が実行。
2. **人が追加する系**（インバウンド）— その場で増える。曖昧・verify 未定義が多い。
3. **実行後に人の判断を促す系**（アウトバウンド）— blocked・要レビュー・要修正。人が直して差し戻す。

### メンテコストの正体

人が手でやらされていること＝削るべきコスト:

- blocked をどこかに集めて見に行き、直したら戻す
- verify が書けていないタスクを探す
- 古い・重複・実行不能なタスクを掃除する
- done が溜まった一覧をスクロールして「今 何が要対応か」を探す

**これを消す**のが本書の道具。人間の接点を後述の**2境界だけ**に絞る。

| 要件 | 実現方法 |
|------|---------|
| 自己申告 done を防ぐ | done 確定の根拠を `verify` コマンドの終了コード 0 **のみ**に限定 |
| 暴走・予算溶かしを防ぐ | 収束ガード（max_cycles / no_progress / blocked_ratio / budget）＋ retries 上限 |
| 系をまたぐ手仕分けを消す | レーン遷移を**システムが握る**（人は2境界のみ） |
| 注意を集中させる | 「要対応」だけを通知（遷移時 dedup、全体は見せない） |
| 人の判断を統制下に置く | エージェント順位付けを `policy.md` で**人間が上書き**＋ `DECISIONS.md` に記録 |
| 実行系を作り直さない | act を `kiro-flow run`（loop-until-done）に委譲 |
| kiro-cli 無しでも検証可能 | `--executor stub` ／ `--dry-run`（verify のみ）／ 順位付けの決定的フォールバック |

---

## 3. 系とレーンモデル

タスクに `source`（human / triage / followup）と `status`（inbox / ready / doing / done /
blocked）の2軸を持たせ、**レーン間の移動は全部システムが行う**。

```
     人が追加 ─┐                         ┌─ 実行後の差し戻し ─┐
              ▼                         ▼                    │
        [inbox] ──triage──▶ [ready] ──loop──▶ [done]→archive │
          │ 優先度付け/        │ verifyゲート     │             │
          │ verify有無判定     │                 ▼             │
          └─ verify無し ───────┴────────▶ [blocked]───────────┘
                  ▲ 人の判断が要る境界は2つだけ ▲
            intake: 何を/どう検証(acceptance)   escalation: 判断・修正
```

| 人の境界 | いつ | 人がやること |
|---|---|---|
| **intake** | inbox に verify 無し/曖昧なタスク | acceptance（verify）を定義、または却下 |
| **escalation** | verify fail ×K / 検証不能 / policy で hold | 判断・修正して差し戻す、または打ち切る |

その他の遷移（inbox→ready の優先度付け、ready→doing→done/blocked、done のアーカイブ）は
すべて機械が行う。

---

## 4. 判断の仕組み（decide）

タスク／サイクルごとに **1つの決定関数**が、型付きの判断と**理由**を返す。判断は LLM の気分では
なく**ルールベースで説明可能**にする（人が通知を信用でき、デバッグできる）。

```
decide(task) → { action, reason }
  inbox かつ verify 無し            → need_intake   （理由: acceptance 未定義）
  inbox かつ verify 有り            → route_ready   （優先度 = §4.1）
  ready 先頭                        → run
  run 後 verify pass               → mark_done
  verify fail / retries<K          → retry
  verify fail / retries≥K・検証不能  → escalate       （→ blocked）
  policy の deny/hold に該当         → escalate(held) （実行せず blocked、通知）
  収束ガード発火                     → stop
```

**全判断に reason を残す**のが肝。これがそのまま通知文面と `journal.md` になる。

### 4.1 優先度 — エージェント順位付け ＋ 人間の上書き

```
① エージェントが inbox を順位付け（LLM）   → 提案順位 ＋ reason
② policy.md の人間ルールで上書き           → 最終順位   ★人間ルールが必ず勝つ
       deny  … 自動実行させない（denylist）
       pin   … 強制的に上へ
       defer … 下げる
③ deny に当たったタスク → 実行せず blocked/held（通知）
```

- **precedence は厳格に「人間 policy ＞ エージェント提案」**。エージェントは面倒な順位付けを
  肩代わりするだけで、最終権限は人間の小さなルールファイルにある。
- 透明性のため **エージェント提案順位と、どの policy ルールが上書きしたかを両方ログ**する。
- エージェント順位付けは**差し替え可能な戦略**として実装する。既定はエージェント、
  フォールバック＝`source=human` 優先 → 古い順。これで `--planner stub` のテストも決定的に保つ。

### 4.2 拡張次元（後段・本 MVP では作らない）

同じ `decide` に次の出力次元を後付けする。判断を1点に集約してあるので拡張が局所で済む。

- **pace**: 予算（トークン/時間/コスト）に応じてサイクル間スリープを返し、**レーンを減速**。
  → `estimation`（予算初期値）/ `slo-designer`（エラーバジェット）と接続。
- **location**: `local` か `分散`（kiro-flow `--git` バス）かを返し、act を**移譲**。
  → kiro-flow git モード / `gitlab-idd` / `multi-agent-shogun-kiro` と接続。

---

## 5. 人間が触る3面

すべて人間が低コストで編集できるファイル。これが「人とループの接点」。

| ファイル | 役割 | 書く主体 |
|---|---|---|
| `queue.md` | タスク本体（人が追加＝インバウンド系） | 人＋システム |
| `policy.md` | **エージェント判断への上書きルール**（denylist 等） | **人だけ** |
| `DECISIONS.md` | 人の判断・承認の**決定記録**（append-only） | システム（人の操作から生成） |

### policy.md の記法（MVP）

`deny` / `pin` / `defer` の3種。値は**タスクID またはタイトル部分一致**でマッチ。

```yaml
deny:  prod        # タイトル/IDに "prod" を含むタスクは自動実行しない（人の承認待ち）
pin:   T3          # T3 を最優先に固定
defer: cleanup     # "cleanup" を含むタスクは後回し
```

---

## 6. 人への通知（要対応だけを push）

| 項目 | MVP |
|---|---|
| トリガー | `need_intake` / `escalate` / `stop` が出た時 |
| 中身 | 要対応ダイジェスト = {タスクID, タイトル, **なぜ**, 推奨アクション} の一覧 |
| 配信 | 既定: `NEEDS_YOU.md` 生成 ＋ stdout。`--notify-cmd` で差替（teams-use / outlook-use / issue-mailbox へパイプ） |
| dedup | **状態遷移時だけ通知**。毎ポーリングでは出さない（アラート疲れ自体がメンテコスト） |

通知先は MVP では file＋stdout を既定にし、teams/メール/issue は `--notify-cmd` フックで後付け。

---

## 7. 決定記録（Decision Record）

人が境界で判断した瞬間を、承認操作と**不可分**に記録する。＝「承認コマンドが決定記録を生む」
ので、痕跡なしに承認できない。`journal.md`（機械のサイクルログ）とは別に、**人の統治ログ**として
`DECISIONS.md` に append-only で残す（ADR の task 版）。

```
## DR-0007  2026-06-17  actor: devilrabbit.jp@gmail.com
- context : T12 が verify fail ×3 で escalate
- action  : approve-and-fix（verify を修正し ready へ差し戻し）
- reason  : テスト側の期待値が誤っていた
- affects : T12 → ready
```

操作コマンド（いずれも DR を自動追記）:

- `approve <id> --reason …` … blocked を修正承認して差し戻し
- `hold <id> --reason …` … policy に hold/deny を追加（denylist 化）＋ DR
- `reprioritize <id> --pin|--defer --reason …` … policy に pin/defer ＋ DR

**北極星との接続**: 蓄積した DR は将来、似た事案をエージェントが過去の人間判断に倣って
自動解決する材料になり、**通知件数そのものを減らす**（メンテコスト低減ループが閉じる）。
MVP では記録まで。学習（`ltm-use` への promote）は後段。

---

## 8. データモデル

### queue.md（State）

```markdown
## <id>: <タイトル>
- status: inbox | ready | doing | done | blocked
- source: human | triage | followup
- verify: `終了コード0をPASSとみなすシェルコマンド`
- retries: 0
- note: 任意（保持される）
```

- パーサは `## <id>: <title>` を見出し、直後の `- key: value` をメタデータとして読む。
- `verify` のバッククォートは除去してそのまま実行可能にする。
- 既知フィールド以外（`note` 等）は順序保持で退避・書き戻し（正準形へ寄せつつ自由記述を失わない）。

### journal.md（Memory／機械の申し送り）

各サイクルの判断（action＋reason）と開始・停止イベントを 1 行ずつ追記。次サイクル・次セッションが
読む短期ワーキングメモリ。

### DECISIONS.md / policy.md

§5・§7 の通り。いずれも人間が直接編集でき、システムも読み書きする。

---

## 9. 実行ループと収束（verify ゲート ＋ 停止条件）

### ループ本体

```
preamble, tasks = load_queue()
triage(tasks, policy)              # inbox を順位付け・ルート（§4.1）
while 停止条件に未到達:
    task = pick_next(ready 先頭)
    if task 無し: → drained で終了
    task.status = doing; save()
    act_via_kiro_flow(task)         # Execute（--dry-run では skip）
    ok = run_verify(task.verify)    # ★ done 確定の唯一の根拠（決定的・LLM外）
    decide → mark_done / retry / escalate
    save(); journal 追記; 必要なら notify
```

- **verify ゲート**（`run_verify`）はシェル実行し終了コード 0 を PASS とする。verify が空なら
  即 FAIL（=「自己申告では done にできない」を構造で表現）→ escalate。
- **act は差し替え可能**（`run_loop(cfg, act=...)`）。テストは偽 act を注入し kiro-flow 抜きで
  判断・状態機械を検証する。

### 停止条件（収束ガード）

| 停止理由 | 既定 | 判定 |
|----------|------|------|
| `drained` | — | `ready`/`inbox` が尽きた（実質完了） |
| `max_cycles` | 20 | 外側サイクル数の上限 |
| `no_progress` | 3 | `done` 件数が N サイクル連続で増えない |
| `blocked_ratio` | 0.5 | `blocked / 全タスク` がしきい値以上 |
| `budget` | 無制限 | 実時間（`--max-seconds`）超過 |

タスク単位は `retries > max_retries`（既定 2）で `blocked`。verify 未定義は 1 回で即 `blocked`。

---

## 10. トリガー（いつ走らせるか）

**task-loop はトリガー非依存**。「叩かれたら ready を一巡消化して止まる」プロセスなので、
何が叩くかは差し替え自由。

| トリガー | 何をする | daily triage 適性 |
|---|---|---|
| **cron / systemd timer** | `task-loop run` を定刻に1プロセス起動 | ◎ 本命 |
| 手動 | 人が叩く | 開発・検証 |
| イベント（issue-mailbox / gitlab-idd / PR webhook） | inbox へ enqueue → 起動 | 反応型 |
| CI ステップ | push 時に走らせ exit code で gate | 受け入れ確認 |
| ~~kiro-loop~~ | kiro-cli に**プロンプト投入のみ**。任意プロセスを exec できず、batch 消化ループの起動には不適 | ✗ |

```cron
# 毎朝9時にバックログを一巡消化（triage→verifyゲート→有限回で停止→要対応は通知）
0 9 * * *  cd ~/proj && task-loop run --queue backlog.md --max-cycles 30 --executor kiro
```

> 注: 旧版に「kiro-loop で周期起動できる」とあったのは**誤り**。kiro-loop は tmux 上の kiro-cli に
> `send-keys` でプロンプトを送るだけで、ratchet/task-loop のような CLI を exec できない。
> kiro-loop が向くのは「長寿命の対話エージェントを生かし続けて小突く」モードで、本ループとは
> トリガーとして競合する。daily triage は cron に寄せる。

---

## 11. CLI（予定）

| サブコマンド | 役割 |
|---|---|
| `run` | triage → ready 消化ループ（verify ゲート・収束・通知） |
| `triage` | inbox の順位付け・ルートのみ（エージェント＋policy） |
| `review` / `needs` | 要対応ワークリスト（blocked＋need_intake）を描画 |
| `approve <id>` | blocked を修正承認して差し戻し＋ DR |
| `hold <id>` | policy に hold/deny 追加＋ DR |
| `reprioritize <id>` | policy に pin/defer ＋ DR |

主なフラグ: `--queue` `--policy` `--decisions` `--journal` `--workdir` `--bus`
`--executor{kiro,stub}` `--planner{flow-planner,kiro,stub}` `--max-cycles` `--max-retries`
`--no-progress` `--blocked-ratio` `--max-seconds` `--notify-cmd` `--dry-run` `--once`。

終了コード: `0`=drained かつ blocked 無し ／ `1`=blocked あり（人間の判断が必要） ／
`2`=ガード停止。CI ステップに組める。

---

## 12. 検証（テスト方針）

`tools/task-loop/tests/test_task_loop.py`（標準 `unittest`）で kiro-flow を呼ばずに検証する。

- **既に実装・テスト済み（loop コア）**: パース/書き戻し・verify ゲート（空=FAIL）・状態機械
  （全PASS→drained／失敗→retries超過でblocked／verify無で即blocked）・停止条件
  （max_cycles/no_progress/blocked_ratio）・act 注入・kiro-flow stub 統合（11 ケース）。
- **本改訂で追加するテスト**: triage の順位付け（policy 上書きの precedence＝人間が勝つ）・
  deny/pin/defer のマッチ・need_intake/escalate の通知発火と遷移時 dedup・
  `approve`/`hold`/`reprioritize` が `DECISIONS.md` に DR を追記すること・
  エージェント順位付けの決定的フォールバック。

```bash
KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/task-loop/tests -v
```

---

## 13. 成功基準（MVP の達成定義）

1. 人間がプロンプトを **0 回**追加投入して、`queue.md` のタスクが N 件 `done` になる。
2. **誤った done が verify ゲートで止まる**（自己申告では通らない）。
3. 暴走せず**有限回で必ず止まり**、停止理由を返す。
4. 人間の接点が **intake / escalation の2境界だけ**に絞られる（全バックログを手で見ない）。
5. エージェントの順位付けを **`policy.md` で人間が上書きでき**、その precedence が保証される。
6. 人の承認・判断が **`DECISIONS.md` に痕跡として必ず残る**。
7. 通知は **状態遷移時だけ**届く（毎ポーリングで鳴らない）。

---

## 14. MVP 境界

| | MVP | 後段 |
|---|---|---|
| 判断 | route/run/done/retry/escalate/stop ＋ reason、エージェント順位付け＋policy上書き | pace（予算減速）, location（分散移譲） |
| 通知 | 要対応ダイジェスト（file+stdout, 遷移時 dedup, --notify-cmd） | teams/メール/issue 連携, 双方向往復の自動化 |
| 決定記録 | approve/hold/reprioritize が DECISIONS.md に追記 | 過去 DR からの自動解決学習（ltm-use 連携） |
| 系 | inbox/ready/doing/done/blocked ＋ source 2軸, triage, 要対応ビュー | rot 自動検知, webhook enqueue, done 自動アーカイブ |
| 実行 | kiro-flow（local） | kiro-flow `--git`（分散バス）へ移譲 |
