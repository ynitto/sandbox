# kiro-steward — Loop Engineering MVP 設計書

> 作成日: 2026-06-16 ／ 更新日: 2026-06-17（正準ループ確定に伴い全面改訂）
> 対象ブランチ: `claude/determined-cray-dthvbi`
> 関連ファイル: `tools/kiro-steward/kiro-steward.py`, `tools/kiro-steward/tests/test_kiro_steward.py`,
> `tools/kiro-steward/README.md`, `tools/kiro-steward/backlog.md.example`,
> `.github/instructions/kiro-steward.instructions.md`, `tools/kiro-flow/`
>
> 名称 `kiro-steward`: 持ち主（人間）に代わってバックログを整え・管理し・説明責任を持つ
> 「執事/管財人」。`policy.md`＝持ち主の常設指示、`DECISIONS.md`＝伺いを立てた台帳に対応する。
> `kiro-` 接頭辞は実行を kiro-flow＝kiro-cli に委譲するため（kiro-loop / kiro-flow に倣う）。

---

## 1. 概要

Loop Engineering（「プロンプトを書く人」をやめ「プロンプトを出し続けるループ＝システム」を
設計する）の **MVP**。仕様駆動（SDD）の枠組みは意図的に外す。

kiro-steward は **バックログを優先順位付けし、最優先タスクを kiro-flow に実行させ、結果を
検証し、ダメなら積み直す——これを backlog が尽きるか予算が尽きるまで繰り返す制御層**。
人間がプロンプトを毎サイクル投げ込まなくても自律的に回り、人の判断が要った時はそれを
決定記録に残す。

```
[Trigger]  いつ走らせるか   ← cron / systemd timer / 手動 / イベント
   │
[Control]  kiro-steward     ← 優先順位付け・検証ゲート・積み直し・収束・決定記録（本書）
   │
[Execute]  kiro-flow run    ← 最優先タスクの act（分解・並列・内側反復）
   │
[future]   pace / location  ← 予算でレーン減速 / 分散環境へ移譲（拡張次元）
```

---

## 2. 正準ループ（この 5 点が仕様の背骨）

kiro-steward の動作は以下に**正準化**される。他のすべての機構（優先順位付け・通知・決定記録）は
この 5 点に従属する。

1. **kiro-steward は `backlog.md` を読み優先順位をつけ、最も優先順位の高いタスクを
   kiro-flow に投げる。**
2. **優先順位付けは原則 kiro-cli（エージェント）で行う。`stub` を設定した場合は最も古いものを
   優先する（FIFO）。** 人間は `policy.md` でこの順位を上書きでき、その上書きは決定記録に残る（§4）。
3. **kiro-steward は kiro-flow の結果を確認し、検証する。検証の結果 NG であれば backlog に
   積み直す。** 検証＝タスク自身の `verify` コマンドの終了コード 0 のみを done の根拠とする（§5）。
4. **上記のループを、backlog が無くなるか、設定した予算が尽きるまで繰り返す。**
   予算＝サイクル数 / 実時間 /（将来）コスト（§6）。
5. **ユーザーによる判断は kiro-steward が決定記録（`DECISIONS.md`）に保存する。**（§7）

```
        ┌─────────────────────────────────────────────────────────────┐
        │ while backlog に消化可能タスクがあり、かつ予算が残る:           │
        │   ① 優先順位付け（kiro-cli / stub=最古）＋ policy.md 上書き     │
        │   ② 最優先タスクを kiro-flow run に投げる（act）                │
        │   ③ 結果を確認し verify ゲートで検証                            │
        │        PASS → done                                            │
        │        NG   → backlog に積み直す（retry）／判断不能なら人へ      │
        │   ④ 申し送りを journal に追記                                  │
        │ 終了: backlog 枯渇（drained）または 予算切れ（budget）          │
        └─────────────────────────────────────────────────────────────┘
   人の判断（優先度上書き・積み直しの承認・打ち切り）は DECISIONS.md に記録（⑤）
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

```
① エージェント（kiro-cli）が backlog を順位付け      → 提案順位 ＋ reason
   ※ --planner stub 時は最古優先（FIFO）の決定的順位
② policy.md の人間ルールで上書き                     → 最終順位   ★人間ルールが必ず勝つ
       deny  … 自動実行させない（denylist）→ 人の判断待ちへ
       pin   … 強制的に上へ
       defer … 下げる
③ 最終順位の先頭タスクを kiro-flow に投げる
```

- **precedence は厳格に「人間 policy ＞ エージェント提案」**。エージェントは面倒な順位付けを
  肩代わりするだけで、最終権限は人間の小さなルールファイルにある。
- 透明性のため **エージェント提案順位と、どの policy ルールが上書きしたかを両方ログ**する。
- 順位付け戦略は差し替え可能。既定はエージェント（kiro-cli）、`stub` は最古優先。これにより
  オフライン（kiro-cli 無し）でも決定的に検証できる。
- `policy.md` への追記（hold/pin/defer）は**人間の判断**なので決定記録に残す（§7）。

### policy.md の記法（MVP）

`deny` / `pin` / `defer` の3種。値は**タスクID またはタイトル部分一致**でマッチ。

```yaml
deny:    prod      # タイトル/IDに "prod" を含むタスクは自動実行しない（人の判断待ち）
pin:     T3        # T3 を最優先に固定
defer:   cleanup   # "cleanup" を含むタスクは後回し
offload: heavy     # "heavy" を含むタスクは分散環境（git バス）へ移譲（§5.1）
```

---

## 5. 実行と検証（正準ループ ②③）

### act の委譲（kiro-flow）

最優先タスクから要求文を組み立て、`kiro-flow run` を同期実行する。

```
kiro-flow --bus <bus> run "<request>" --planner <p> --executor <e> --max-iterations <m>
```

要求文には**完了条件として `verify` コマンドを明示**し、「完了条件を満たすまで反復
（loop-until-done）」を促す。kiro-flow 実行体は `--kiro-flow` > `PATH` > 同梱
`tools/kiro-flow/kiro-flow.py` の順で解決する。

### 5.1 実行先の決定（location 次元 / 分散移譲）

`decide` の拡張次元として **act の実行先（local / remote）** を決める。`--git-bus`（共有 git
リポジトリ）が設定され、かつタスクが `policy.md` の `offload:` 規則に当たる場合のみ **remote**：
kiro-flow を `--git <bus>` 付きで起動し、共有バス越しに分散実行する（kiro-flow の既存分散機構を
そのまま活用）。それ以外は local。判断は journal に記録する（透明性）。

```
decide_location(task) = remote  if (git-bus 設定あり) かつ (offload 規則に一致)
                      = local   otherwise
```

人間制御の一貫性のため、移譲対象は `deny/pin/defer` と同じく **`policy.md` の人間ルール**
（`offload:` 部分一致）で指定する。

### 検証ゲート（done 確定の唯一の根拠）

kiro-flow が結果を返したら、kiro-steward が**タスク自身の `verify` をローカルで実行**し、
**終了コード 0 のみ**を done とする。kiro-flow（内側 LLM）が「できました」と言っても、
verify が通らなければ done にしない（自己申告 done の禁止）。

```
PASS（exit 0） → done
NG（exit≠0）   → backlog に積み直す（status を ready に戻す＝retry）
verify 未定義   → done 不能。人の判断へ（§7）
```

- **検証 NG は backlog への積み直し**（正準ループ ③）。次サイクル以降で再び拾われる。
- ただし **kiro-steward が機械的に判断できない**ケース（verify 未定義／同一タスクが繰り返し NG で
  自動解決の見込みなし）は、無限の積み直しにせず**人の判断へ回す**（status=blocked → 通知 §8 →
  ユーザーが承認・修正して決定記録 §7）。これが点⑤「ユーザーによる判断」の入口。

---

## 6. 収束（正準ループ ④）

ループは **2 条件のいずれか**で必ず止まる。Loop Engineering の暴走・予算溶かしをここで潰す。

| 停止理由 | 意味 | 既定 |
|----------|------|------|
| `drained` | backlog に**消化可能なタスク**（実行待ち）が無くなった | — |
| `budget` | 設定した予算が尽きた | 下記 |

**予算の表現**（いずれも budget の一形態。複数指定可、最初に尽きたもので停止）:

| 予算 | フラグ | 既定 |
|------|--------|------|
| サイクル数 | `--max-cycles` | 20 |
| 実時間 | `--max-seconds` | 0（無制限） |
| コスト（将来） | — | 後段 |

- `blocked`（人の判断待ち）になったタスクは**消化可能集合から外れる**ため、ループを無限に
  占有しない。消化可能タスクが尽きれば `drained`。
- タスクが繰り返し NG でも、予算が残る限り積み直して再挑戦し、予算が尽きれば `budget` で停止する。

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
`DECISIONS.md` に append-only で残す（ADR の task 版）。

```
## DR-0007  2026-06-17  actor: devilrabbit.jp@gmail.com
- context : T12 が verify NG ×3 で人の判断へ
- action  : approve-and-fix（verify を修正し backlog へ積み直し）
- reason  : テスト側の期待値が誤っていた
- affects : T12 → ready
```

操作コマンド（いずれも DR を自動追記）:

- `approve <id> --reason …` … 人の判断待ちを修正承認して積み直し
- `hold <id> --reason …` … policy に hold/deny を追加（denylist 化）＋ DR
- `reprioritize <id> --pin|--defer --reason …` … policy に pin/defer ＋ DR

**北極星との接続**: 蓄積した DR は将来、似た事案をエージェントが過去の人間判断に倣って
自動解決する材料になり、人の判断件数そのものを減らす。MVP では記録まで。学習
（`ltm-use` への promote）は後段。

---

## 8. 通知（人の判断を要する時だけ push）

正準ループが「人の判断へ」回したタスク（verify 未定義／繰り返し NG／policy deny）と、予算切れの
打ち切りを、要対応ダイジェストで人へ届ける。

| 項目 | MVP |
|---|---|
| トリガー | タスクが `blocked`（人の判断待ち）へ遷移、または `budget` 停止 |
| 中身 | {タスクID, タイトル, **なぜ**, 推奨アクション} の一覧 |
| 配信 | 既定: `NEEDS_YOU.md` 生成 ＋ stdout。`--notify-cmd` で差替（teams-use / outlook-use / issue-mailbox へパイプ） |
| dedup | **状態遷移時だけ通知**。毎サイクルでは鳴らさない（アラート疲れもメンテコスト） |

---

## 9. 人間が触る3面

すべて人間が低コストで編集できるファイル。これが「人とループの接点」。

| ファイル | 役割 | 書く主体 |
|---|---|---|
| `backlog.md` | タスク本体（人が追加＝インバウンド系） | 人＋システム |
| `policy.md` | エージェント順位付けへの上書きルール（denylist 等） | **人だけ** |
| `DECISIONS.md` | 人の判断・承認の決定記録（append-only） | システム（人の操作から生成） |

---

## 10. データモデル

### backlog.md（State）

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
- `ready`（実行待ち）を上から消化。`done`/`blocked` は飛ばす。`inbox` は triage で `ready` 化する。

### journal.md / DECISIONS.md / policy.md

§7・§9 の通り。journal は機械の申し送り、DECISIONS は人の統治ログ、policy は人の常設指示。

---

## 11. トリガー（いつ走らせるか）

**kiro-steward はトリガー非依存**。「叩かれたら ready を一巡消化して予算内で止まる」プロセス。

| トリガー | 何をする | daily triage 適性 |
|---|---|---|
| **cron / systemd timer** | `kiro-steward run` を定刻に1プロセス起動 | ◎ 本命 |
| 手動 | 人が叩く | 開発・検証 |
| イベント（issue-mailbox / gitlab-idd / PR webhook） | inbox へ enqueue → 起動 | 反応型 |
| CI ステップ | push 時に走らせ exit code で gate | 受け入れ確認 |
| ~~kiro-loop~~ | kiro-cli に**プロンプト投入のみ**。CLI を exec できず batch 消化ループの起動には不適 | ✗ |

```cron
# 毎朝9時にバックログを一巡消化（優先順位付け→検証ゲート→予算内で停止→要対応は通知）
0 9 * * *  cd ~/proj && kiro-steward run --backlog backlog.md --max-cycles 30 --executor kiro
```

> 注: kiro-loop は tmux 上の kiro-cli に `send-keys` でプロンプトを送るだけで、kiro-steward の
> ような CLI を exec できない。daily triage の起動は cron に寄せる。

---

## 12. CLI（予定）

| サブコマンド | 役割 |
|---|---|
| `run` | 正準ループ（優先順位付け → 実行 → 検証ゲート → 積み直し → 収束・通知） |
| `triage` | 優先順位付けのみ（kiro-cli / stub）＋ policy 上書き |
| `review` / `needs` | 人の判断待ち（blocked）を描画 |
| `approve <id>` | 人の判断待ちを修正承認して積み直し＋ DR |
| `hold <id>` | policy に hold/deny 追加＋ DR |
| `reprioritize <id>` | policy に pin/defer ＋ DR |

主なフラグ: `--backlog` `--policy` `--decisions` `--journal` `--workdir` `--bus`
`--executor{kiro,stub}` `--planner{flow-planner,kiro,stub}` `--max-cycles` `--max-seconds`
`--max-iterations` `--notify-cmd` `--git-bus` `--git-branch` `--git-subdir` `--dry-run` `--once`。

---

## 13. 検証（テスト方針）

`tools/kiro-steward/tests/test_kiro_steward.py`（標準 `unittest`）で kiro-flow を呼ばずに検証する。

- **既に実装・テスト済み（loop コア）**: パース/書き戻し・verify ゲート（空=NG）・状態機械
  （PASS→done／NG→積み直し→人の判断へ）・act 注入・kiro-flow stub 統合（11 ケース）。
- **本改訂で追加するテスト**: 優先順位付け（kiro-cli 戦略 ／ stub=最古優先 ／ policy 上書きの
  precedence＝人間が勝つ）・収束（drained ／ budget=max-cycles・max-seconds）・
  人の判断待ち遷移と遷移時 dedup 通知・`approve`/`hold`/`reprioritize` が `DECISIONS.md` に
  DR を追記すること。
- **現行コードとの差分（実装で整理）**: 現行 loop コアは停止条件に `no_progress` /
  `blocked_ratio` を持つが、正準ループ §6 では収束を **drained / budget** に単純化する。
  これらは予算（cycles/time）に畳み、第一級の停止条件からは外す。

```bash
KIRO_FLOW_STUB_SLEEP_MAX=0 python -m unittest discover -s tools/kiro-steward/tests -v
```

---

## 14. 成功基準（MVP の達成定義）

1. 人間がプロンプトを **0 回**追加投入して、`backlog.md` のタスクが N 件 `done` になる。
2. **誤った done が verify ゲートで止まる**（自己申告では通らない）。
3. 暴走せず**必ず止まる**（backlog 枯渇 or 予算切れ）。
4. 優先順位付けが **原則 kiro-cli**、`stub` で**最古優先**になる。
5. 優先順位を **`policy.md` で人間が上書きでき**、その precedence が保証される。
6. 検証 NG が **backlog に積み直され**、判断不能は人へ回る。
7. 人の承認・判断が **`DECISIONS.md` に痕跡として必ず残る**。
8. 通知は **状態遷移時だけ**届く（毎サイクルで鳴らない）。

---

## 15. MVP 境界と拡張次元

| | MVP | 後段 |
|---|---|---|
| 優先順位 | kiro-cli ／ stub=最古 ／ policy 上書き | 過去 DR からの自動解決学習（ltm-use） |
| 実行・検証 | kiro-flow（local）＋ ローカル verify ゲート | — |
| 収束 | drained / budget（cycles・time） | コスト予算、pace（予算でレーン減速） |
| 系 | inbox/ready/doing/done/blocked ＋ source | rot 自動検知, webhook enqueue, done 自動アーカイブ |
| 通知 | NEEDS_YOU.md＋stdout（遷移時 dedup） | teams/メール/issue 連携 |
| 決定記録 | approve/hold/reprioritize → DECISIONS.md | — |
| 実行先 | local ／ **location（offload 規則で kiro-flow `--git` 分散バスへ移譲）** | コスト連動の自動 offload 判断 |

**拡張次元**: 分散移譲 `location` は実装済み（§5.1、`policy.md` の `offload:` で人間が指定）。
予算でレーンを減速する `pace` は、収束（§6）の延長として後段で足す。判断を1点（`decide`）に
集約してあるため、いずれも局所で拡張できる。
