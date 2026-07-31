# agent-project 設計修正計画 — レビュー 24 件への対応

> 日付: 2026-07-31（verify 統一計画を統合）
> 入力: [`2026-07-29-agent-project-design-review.md`](./2026-07-29-agent-project-design-review.md)（指摘 24 件＋小項目 6 件）、
> [`2026-07-30-unified-task-verify-design.md`](./2026-07-30-unified-task-verify-design.md)
> 対象: [`agent-project-design.md`](../designs/agent-project-design.md)、
> [`agent-flow-design.md`](../designs/agent-flow-design.md)、
> [`agent-dashboard-design.md`](../designs/agent-dashboard-design.md)
> 位置づけ: 本書は**修正計画**。確定した内容は agent-project 設計書へ反映し、本書は「なぜそう直したか」の
> 履歴として残す。設計判断の正典は常に設計書側。レビュー結果・修正計画は `docs/plans/` に置き、
> **設計書（`docs/designs/`）からはリンクしない**（設計書の独立性）。
> 効く柱・原則: **柱 2 / C5・C3・C7** — done の意味を層をまたいで一本化し（C5）、人へ送る条件と止まる条件を
> 明示し（C3）、判断根拠と書き手を増やさずに閉じる（C7）。

## この計画の考え方

レビューは 24 件あるが、24 通りの機構を足すと設計書が壊れる。指摘の大半は
「不変条件が層をまたぐと曖昧になる」「時刻と状態のどちらが正か書いていない」「単位が書いていない」
「投影が片方向で止まっている」の 4 つに帰着する。被る実現目標はあらかじめ 1 節・1 実装に畳み、
**作業単位は梁 4 本＋付帯契約**に固定する。

**増やさないもの**:

- 新しい常駐プロセス・新しいバス・新しいイベント台帳
- 新しい状態ファイル形式（既存の `backlog/` `needs/` `verifications/` `project.json` で足る範囲）
- 新しい語彙（「検証の強さ」「信頼レベル」など）
- 判定の 2 実装（既存の 1 実装へ経路を寄せるだけ）
- 新しい停止出口（止めるときは既存の throttle→report 降格だけ）
- doctor / dashboard への露出パイプの複製（所見の種類だけ増やす）

### verify 統一設計の所在と実装差分

設計判断はすでに次の設計書へ反映している。本書には、その設計を実装へ落とす順序だけを統合する。

| 正典 | 書いてあること |
|---|---|
| [`agent-project-design.md`](../designs/agent-project-design.md) | 受入基準と固定コマンドの所有、verification plan の生成、receipt の検算、done の確定 |
| [`agent-flow-design.md`](../designs/agent-flow-design.md) | 成果 revision 上の verifier runner、criterion の判定、receipt の返却、fail の修正ループ |
| [`agent-dashboard-design.md`](../designs/agent-dashboard-design.md) | 達成条件・受入基準を通常入力にする UI、固定コマンドの折りたたみ、基準と証拠の結果表示 |
| [`2026-07-30-unified-task-verify-design.md`](./2026-07-30-unified-task-verify-design.md) | plan / receipt の構造、責務境界、後方互換、切替順序 |

2026-07-31 時点で、dashboard の通常入力と charter 下書きは自然文へ変更済み。ただし保存と実行は
旧経路のままである。task は `accept` / `acceptance` / `verify`、charter は `## acceptance` を書き、
charter の自然文は agent-project がコマンドへ一発合成して実行する。UI の変更を契約移行の完了とは
数えない。

---

## 梁 1 — done の根拠は 2 表現。人の役割を同じ節で書く

**契約**: 機械が done を確定できる根拠は 2 表現だけ。**(a) 決定的コマンドの終了コード 0**、
**(b) 受入基準 × 証跡の全 pass**。タスク verify・charter `## acceptance`・他ノードの検証委譲の
**3 経路すべてで同一**で、経路によって強さが変わらない。

人のプロジェクト done は「この方向でよいか」の**価値判断**であって品質保証ではない（C5）。
目標駆動（charter）モードでは分解・未達 acceptance・プロジェクト done は人——無人で回るのは
backlog 消化（正準ループ）だけ。目標節の「backlog が空か予算まで無人運転」は**タスク層の目標**
に限定する。

### W1. 人の役割と 3 層の関係（P0）— #1 #12

設計書に 1 節で書く:

- 機械 done＝上記 2 表現
- 人のプロジェクト done＝価値判断（機械検証の代替ではない）
- charter モードでは人が律速（分解は明示要求のみ、未達は awaiting-plan、プロジェクト done も人）

### W2. acceptance 二表現 ＋ フック契約カタログ E1〜E6（P0 / P1）— #1 #3 #17 #24

**1 カタログ・1 経路**にまとめる（別節にゲート順を書かない）:

| フック | 内容 |
|---|---|
| E1 | `task_acceptance_criteria` / `project_acceptance_criteria` と任意の `verification_commands`。自然文は verifier が証跡付きで判定し、固定コマンドは文字列を変えず実行する。**自然文→コマンドの一発合成は廃止**。負債ラチェット（`codd-gate verify --debt`）は E1 の固定コマンドに載る |
| E2 | `regression_cmd` |
| E3 | `intake_cmd` |
| E4 | `enqueue` / `inbox` |
| E5 | `notify_cmd` |
| E6 | executor |

S3 ゲート順と失敗時の行き先も**このカタログに含める**:
verify（E1）→ regression（E2）→ パス保護 → 進捗。
**regression NG → done せず review**、**パス保護違反 → retry せず人へ**、**進捗 NG → retry**。
verify PASS は列の入場条件であり、通過しても done 確定ではない。

agent-project は E1 と後段の固定 gate を `verification_plan` に正規化し、canonical JSON の SHA-256
digest を付ける。agent-flow は plan を planner の自由記述へ混ぜず、成果 revision が確定した後に
専用 runner で一度だけ実行する。receipt には少なくとも plan digest、result revision、command の
終了コード、criterion ごとの verdict と証拠を含める。`fail` は同じ agent-flow run の修正ループへ
戻し、`inconclusive` は修正回数を消費せず別ノード検証または人へ送る。

旧 `acceptance` / `accept` / `verify` / `verify_template` は agent-project の読み取り境界だけで正規形へ
変換する。新規書き込みは正規形だけとし、dual-write はしない。charter の `## acceptance` も読み取り時に
`project_acceptance_criteria` へ変換する。

### W3. 証跡チェック 1 実装（P1）— #2 #3 #4 #15

receipt の採否は agent-project の 1 実装へ集約する。決定的チェックは次の 5 つ:

1. schema version を解釈でき、plan digest が投入時の値と一致する
2. result revision が採用対象の成果 revision と一致する
3. 固定コマンドがすべて終了コード 0
4. 各 criterion の pass に、対象 revision 上で得たコマンド、ファイル、差分、ログ、画面のいずれかの証拠が対応づく
5. 差分基準を満たす

内蔵 verifier・`verifier_skill`・他ノードの `external.json` の**すべて**が同じチェックを通り、
receipt が無い、古い、壊れている、または上の照合を通らない pass は採用しない。

これに吸収する方針文（P0、別実装なし）:

- 外部受理は「誰が」ではなく「何を出したか」＝上のチェックを同じだけ課す。板のノード契約版が合わない判定は fail（#4）
- `verify_side_effects` の禁止は**強制しない**（非目標）。担保は ④ の証拠確認による事後検知（#15）

### W4. `no_diff` による差分基準の差し替え（P1）— #16

タスクに `- no_diff: <理由>` があると、W3 の⑤が参照する `DIFF_CRITERION` は
**「宣言した成果物ファイルの実在とその内容の参照」へ差し替わる**（基準は消えない）。
調査・方針・ドキュメント・「変更しないこと」系はここに載る。チェック経路は W3 のまま。

**なぜ W3 で足りるか**: 証跡の真正性は LLM に証明させない。
「正本と一致しないものを証跡と認めない」フェイルクローズだけで、既存の「証跡の無い pass は fail」の延長。
再実行検算はしない（非目標）。

---

## 梁 2 — 正は状態。実行権の正本を 1 節に書く

**契約**: リース時刻は「そろそろ諦めてよい」という**ヒント**。実行権の正本は remote の
`owner/token/generation` を CAS で確認できたときだけ。正は常に `remote の backlog + archive`。
その他は投影として毎パスの整合点が作り直す。

### W5. 実行権の正本（P0）— #6 #10 #11 ＋小項目（延長スレッド・一斉復帰）

設計書に 1 節で書く:

- リース失効＝奪取を**試みてよい**（奪取成立ではない）。成立は CAS 経由だけ。skew は二重実行にならず、早期奪取は push 失敗として現れる
- `board` 委譲は claim を握ったまま実行先が board になるだけ（`offloaded`）。fencing は local と同一。**検証だけの委譲は成果を動かさないので claim を取らない**（受理点は `external.json`）——「同一タスクが両方の意味で実行中」に見える窓の正体
- `claims/` は同期しないホスト局所キャッシュで正本ではない。孤児 claim は既存の投影掃除に載る
- controller 延長スレッド死亡 → act は続行しうるが settle は既存の `lost` / `unknown` に落ちる（新経路なし）
- 全 PC 一斉復帰の割当ストームは、controller 選出 CAS 1 本＋controller が決定的に配るので起きない

### W6. settle 1 コミットと投影復旧（P0 / P1）— #8

archive・納品書・needs・verifications・claim 解放は **1 コミット**にまとめ、push が通った時点で確定。
途中死は「未 push のコミットがローカルに 1 つある」状態に落ち、次パスは remote を正として再突合する。
専用リカバリ手順・ジャーナル巻き戻しは持たない——復旧＝投影の再計算。

### W7. `unknown` 隔離の上限（P1）— #7

隔離件数に上限を持ち、超えたそのノードは **既存の throttle→report 降格**で新規 claim を止める
（梁 3 の予算 throttle と**同じ出口**。停止機構を増やさない）。
自動再試行は次パスの fencing 再確認 1 回だけで、それ以上は人待ち。

---

## 梁 3 — 予算はノード別、進行はプロジェクト共有

### W8. 単位と枠（P0）— #9 #21 ＋小項目（複数 charter）

- 財布に紐づく上限（トークン・コスト・実時間）は**ノード別**（host.yaml の `budget` が正）。合算しない（C1）
- 進行に紐づく上限（改善サイクル数・停滞の連続回数・acceptance の PASS 数）は**プロジェクト共有**（`project.json` が正）
- 二重計上は「別の財布を別に数えている」ので正しい。throttle→report はノード局所——他ノードは走り続ける（意図した挙動）。出口は W7 と共有
- **C1 の帰結**: 人が押した入札でも `budget.max_concurrent` と契約版は越えられない。入札専用ルールは新設しない（選別の素通しは維持）
- 複数 charter 並行の予算もノード別（上と同じ）。同一 repo への書き込み競合は agent-flow の claim が正

---

## 梁 4 — 投影を一段広げる

### W9. 削除時の後続再審査（P1）— #5 ＋小項目（物理削除の窓）

物理削除時、`after` に削除 id を持つ後続は `proposed` へ落とす。墓標も決定記録も増やさない。
`unmet_deps`（無い id ＝満たし）は変えない——前提を失った後続は実行可能のまま放置せず、人の目を通る。
実行中タスクの削除は拒否する。判断 5-c「後続を再審査へ戻さない」だけを撤回。

### W10. learn のスコープと失効（P1）— #14

`- learn:` にスコープ（repo / charter / 全体）を付け、連続不発が閾値に達した learn と人が無効化した
learn は適用しない。無効化は決定記録で行う（新ファイルなし）。learn の適用は決定的側に留め、
LLM の裁定ゲートは「人が要るか」だけ（現行の役割分担）。

### W11. 保持契約と gc（P1）— #22

`verifications/` は最新 rev ＋直近 N（settle 対象 rev は消さない——W3 との順序の約束）、
`journal.md` / `run-log.jsonl` は期間ローテーション、`verify-recipes/` は最終使用日で失効、
`archive/` は保持。`gc` は保持契約の実行者で、契約は設計書側に置く。

---

## 付帯契約（梁に載らない残り）

### W12. フォージと検収決着（P0）— #13 ＋小項目（コメント差し戻し）

フォージ実装は GitLab のみ（他は非目標）。未対応リモートでは dashboard のボタン決着が正式契約。
同時操作は「決定的シグナル（マージ／クローズ／changes-requested）が勝ち、ボタンはそれが無いときだけ」。
コメントのみは検収に使わない——差し戻しはラベルまたは dashboard ボタン（決着経路を 2 つにしない）。

### W13. `verify_template`（P0）— #18

プロジェクト yaml の名前付き verify の**展開規則**。展開後は `verify:` と完全同一（red-green 対象）。
ノードごとに展開結果が変わる書き方は禁止（正は状態リポジトリの yaml。host.yaml は関与しない）。

### W14. 重複防衛（P0 / 契機 P1・P3）— #19

意図の同一性は機械スコアでは判定しない。正は S6 どおり:

1. **一次**: 同一バージョンのバックログ（現役＋却下理由付き・却下は有界）をプランナー入力へ載せ、
   「意図が同じものは出さない」はスキル責務
2. **機械の硬抑止**: 正規化タイトルの**完全一致のみ**（墓標・既存）。類似は抑止せず提示／needs 注記
3. **天井を隠さない**: カスタム `planner_skill` 前提では言い換え防衛は鉄則級ではない。偽の鉄則（閾値）を足さない
4. **コンテキスト**: title/status の**全件索引は落とさない**。重いときは summary だけ高リスク
   （doing / offloaded / review / `edited: human` / 直近却下）へ寄せる（B1・1 ショット）。
   多ターン開示・チャンク分割・生成後の別 LLM 裁定・embedding はしない
5. **契機**: コンテキスト圧 → B1 実装（P1）。日本語の表面類似すり抜け実害 → 投入側 Jaccard の
   トークン化衛生（bigram 等）を「ほぼ同一タイトルの最終防衛」に限定して検討（P3）。意図判定には使わない

### W15. doctor 露出パイプ（P0 / P1 / P2）— #20 #23

**パイプは 1 本**: 所見 → `engine/status.json` の横断エラー → dashboard。

- host.yaml トップレベル綻び: 起動は警告どまりを維持（フリート全台停止を避ける）。所見をパイプへ載せる
- 断片合成の死んだ経路: パッケージ化は非目標。構造テストで「公開機能に CLI 入口」「到達不能な断片」を数え、所見を同じパイプへ
- E への昇格（綻びを起動失敗にするか）は doctor の題別内訳が溜まってから（P2）

### W16. 憲章衝突（P0）— 小項目

マスター憲章とバージョン憲章の制約衝突は**自動解決しない**（人が直す）。

---

## 変更する既存の設計判断

**撤回・改訂は 2 点だけ**:

1. **判断 5-c の一部撤回** — 削除は後続を `proposed` へ戻す（W9）。記録を残さない・墓標を積まない性格は維持
2. **判断 1 末尾とプロジェクト層** — charter acceptance の一発合成を廃止し、タスク層と同じ二表現へ（W2）

明文化のみ（判断は変えない）: 予算の単位（W8）、人が律速（W1）、リース解釈と実行権の正本（W5）、
外部受理と副作用非強制（W3 の方針文）、重複防衛の一次＝プランナー入力（W14）、選別素通し＋枠は C1（W8）。

## 非目標

- 証跡の再実行検算
- 外部検証ノードの allowlist
- verifier のサンドボックス／許可コマンド列挙
- 断片合成のパッケージ化
- GitLab 以外のフォージ、イベント台帳の再導入、予算のノード横断合算、分解の自動起動
- 意図同一性の機械スコア／生成後 LLM 裁定／embedding（W14）
- 既存バックログの多ターン開示・チャンク分割分解（W14）
- 停止出口・doctor パイプ・入札専用ルールの新設（増やさないもの）

---

## 段階と完了条件

**P0 — 設計契約の反映** — [x] 完了

W1（人の役割）、W2 のカタログ記述（E1〜E6＋ゲート順）、W3 の方針文、W5、W6 の契約、W8、
W12〜W16、変更判断・非目標。W14 は天井と B1 方針まで。verify の責務境界は agent-project、
agent-flow、agent-dashboard の各設計書へ反映済み。以後の実装判断は 7 月 30 日の plan 単独ではなく、
本書の P1-A を追う。

*完了条件*: レビュー 24 件＋小項目それぞれについて、設計書のどの節を読めばよいかが指せる
（複数指摘が 1 節を共有してよい）。verify については定義、実行、確定の担当と plan / receipt の
入出力が 3 設計書で一致する。

**P1-A — verify 契約の切替（依存順）**

- [x] 1. **schema と digest**: `verification_plan` / receipt の versioned schema、canonical JSON、
  SHA-256 digest、criterion id の採番規則を定義する。task / charter / board で同じ schema を使う。
  実装は `agentcore.verifycontract` の 1 実装（両ツールが共有）。
- [x] 2. **agent-flow runner**: plan を構造化入力で受け、成果 revision 上で固定コマンドと criterion を一度だけ
  検証して receipt を返す。verifier は criterion を変更せず、成果物も変更しない。plan は
  `--verification-plan`（argv）または inbox 要求の `verification_plan` で渡す。
- [x] 3. **agent-project 読み取りアダプタと検算**: 旧形式を正規形へ変換し、W3 の照合を 1 実装に集約する。
  digest、revision、証拠のいずれかが欠けた receipt では done にしない。
- [x] 4. **修正ループ**: criterion の `fail` を同じ agent-flow run の修正へ戻す。`inconclusive` は修正回数を
  消費せず、別ノード検証か人待ちへ送る。
- [x] 5. **charter 移行**: `project_acceptance_criteria` を同じ protocol で評価する。旧
  `resolve_charter_acceptance` と `acceptance_synth` キャッシュは廃止した。
- [x] 6. **dashboard の保存と結果表示**: 新規 task / charter は
  `task_acceptance_criteria` / `project_acceptance_criteria` / `verification_commands` だけで書く。
  タスク詳細の折りたたみ再構成（digest・revision・command を「検証の詳細」へ）は dashboard 側の残作業。
- [x] 7. **shadow 比較**: 新 runner と旧 project verify を同じ task / revision で比較し、差異だけを記録した。
- [x] 8. **旧経路の撤去**: 旧 project verify（task.verify のローカル直実行と LLM verifier）を削除。
  receipt を返せない run は agent-project 自身が local runner として同じ実行セマンティクスで確定する。
- [x] 9. **重複実行の解消**: task 固有 command と regression が完全一致する場合は plan 正規化時に digest で
  一つへ畳む。

*完了条件*:

- 同じ task / result revision / plan digest の command 実行が一回だけ
- receipt の digest または revision が違えば done にならない
- task の fail が同じ agent-flow run の修正ループへ戻る
- task と charter の自然文基準が criterion / receipt で評価される
- 新規データに裸の `acceptance` / `accept` / `verify` が書かれない
- 通常 UI がコマンド入力を要求せず、receipt の基準と証拠を表示する

**P1-B — 残りの決定的な実装（依存順）**

- [x] 1. **W6** settle 1 コミット化。実装中に `git add` が削除済みパスで pathspec ごと失敗し、
  settle が「backlog を消しただけのコミット」に割れていたのを発見して直した。途中死は
  巻き戻さず前へ倒す（`heal_partial_settles`）。「remote を正に再突合」は取り下げ——
  機械が書く状態の裁定はローカル優先で、remote が正なのは実行権だけ。
- [x] 2. **W9** 削除時の後続 proposed 化（実行中削除の拒否は dashboard 側に既存）
- [x] 3. **W7** `unknown` 隔離上限 → 既存 report 降格。次パスの fencing 再確認 1 回も実装。
- [x] 4. **W4** `no_diff`（W3 ⑤の述語差し替え＋決定的 no-progress ガードの opt-out）
- [x] 5. **W10** learn のスコープと失効（`scope=charter:|repo:`、結末の書き戻し、連続不発と人の無効化）
- [x] 6. **W11** 保持契約と gc（`verifications_keep` / `gc_retention_days`。`verify-recipes/` は
  P1-A8 で廃止済みのため対象から外し、設計書の構成表からも削除）
- [x] 7. **W15** 構造テスト＋綻び／到達不能所見をパイプへ
- [x] 8. **W14** `existing[]` 契約テスト（却下理由の同梱・上限・組み込みプロンプト・完全一致抑止）
- [ ] 9. （契機）**W14 B1** 密度段階 — コンテキスト圧が実害になってから

*完了条件*: 各項目にテスト。W3 は「証跡の無い pass が fail」「外部判定も同じチェック」を最低線。
どちらも満たした（外部判定は 7/31 に receipt 契約へ寄せた）。

**P2 — 観測してから**

- [ ] W15 の E 昇格（host.yaml の綻びを起動失敗にするか。doctor の題別内訳が溜まってから）
- [ ] 外部検証受理の実運用
- [ ] 削除と実行側 write の競合窓

**P3 — 契機待ち（完了条件外）**

- [ ] W14 の投入側トークン化衛生（ほぼ同一タイトル用。意図判定には使わない）

---

## 積み残し（2026-07-31 時点）

設計書と実装の食い違いは 2 件を解消した。残りは下の 2 件。

- [x] **外部判定が receipt 検算を通っていない**（P1-B 完了条件の未達分）— 検証委譲の公示に
  `verification_plan` を載せ、請負側 agent-flow の receipt を板の result で返す形にした。
  依頼側は `receipt_errors` を通してから受理し、receipt が返らない終端は成功でも人へ回す。
  証跡ゼロの `{"verdict": "pass"}` を組み立てていた `_accepted_external_verification` は削除。
- [x] **現役タスク相手の重複抑止が Jaccard 0.5** — `_is_duplicate` を正規化タイトルの完全一致へ
  寄せ、類似は止めずに注記（「既存タスクに似ています」）へ回した。見送りは journal に残す。
  7/26 の詳細設計が「投入側は最終防衛線として残す」と決めた前提を、7/29 のレビュー #19 が
  崩していた（差し替え可能なプランナー相手に言い換えは止められない）。決定は下りていたが、
  P1-B 8 を「実装済みの回帰」と読んでテストしか足していなかった。
- [ ] **実装委譲（board）の plan / receipt 伝搬** — タスクの実装を板へ委譲した run には
  まだ plan を渡していない。委譲先で done になった成果は、依頼元の local runner が固定コマンド
  だけ確かめて確定する（自然文基準は inconclusive）。配管（`submit_request` の
  `verification_plan`、result の `receipt`）は検証委譲で通したので、載せる側を足すだけ。
- [ ] **dashboard のタスク詳細の再構成**（P1-A6 の残り）— 保存側は正規形に寄った。
  受入基準・判定・証拠を通常表示にし、plan digest・成果 revision・実行コマンドを
  「検証の詳細」へ折りたたむ画面側の作業が残っている。

---

## 追跡表

| # | 要旨 | 作業 | 段階 |
|---|---|---|---|
| 1 | done の三層 | W1 / W2 | P0 / P1 |
| 2 | 証跡の真正性 | W3 | P1 |
| 3 | 弱い verify / acceptance | W2 / W3 | P1 |
| 4 | 外部検証の信頼境界 | W3 | P0 / P1 |
| 5 | 削除＝依存充足 | W9 | P1 |
| 6 | 時計 skew とリース | W5 | P0 |
| 7 | `unknown` のスケール | W7 | P1 |
| 8 | クラッシュ一貫性 | W6 | P0 / P1 |
| 9 | 予算の共有単位 | W8 | P0 |
| 10 | 割当と板委譲 | W5 | P0 |
| 11 | claims と二重実行 | W5 / W6 | P0 |
| 12 | 無人運転と charter | W1 | P0 |
| 13 | review の正と GitLab | W12 | P0 |
| 14 | learn の抑制 | W10 | P1 |
| 15 | 副作用の enforce | W3 | P0 / P1 |
| 16 | 無差分タスク | W4 | P1 |
| 17 | S3 ゲート列 | W2 | P0 |
| 18 | `verify_template` | W13 | P0 |
| 19 | 日本語タイトルの重複 | W14 | P0 / 契機 |
| 20 | host.yaml の綻び | W15 | P0 / P1 / P2 |
| 21 | 人手入札の素通し | W8 | P0 |
| 22 | 状態リポジトリの成長 | W11 | P1 |
| 23 | 断片 exec 合成 | W15 | P0 / P1 |
| 24 | codd-gate 接点 | W2 | P0 |
| 統 | plan / receipt schema と digest | W2 / W3 | P1-A |
| 統 | agent-flow runner と同一 run の修正ループ | W2 | P1-A |
| 統 | task / charter の正規形保存と旧経路撤去 | W2 / W3 | P1-A |
| 統 | dashboard の criterion / evidence 表示 | W2 / W3 | P1-A |
| 小 | 延長スレッド・一斉復帰 | W5 | P0 |
| 小 | 複数 charter・予算 | W8 | P0 |
| 小 | 憲章衝突 | W16 | P0 |
| 小 | 物理削除の窓 | W9 | P1 |
| 小 | コメント差し戻し | W12 | P0 |
