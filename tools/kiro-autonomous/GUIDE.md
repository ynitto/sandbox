# kiro-autonomous 運用ガイド（熟練度別）

「いきなり無人運用にしない」ためのガイド。**L0 下見 → L1 試運転 → L2 日常運用 → L3 無人運用 → L4 スケール**の
順に、各段階で**何を設定し・どう動かし・いつ次へ進むか**をまとめる。詳細仕様は [README](README.md) と
[統合設計書](../../docs/designs/kiro-autonomous-design.md) を参照。目標から回す上位ループと複数プロジェクトは
[§プロジェクト層](#プロジェクト層charter-駆動-目標から回す複数プロジェクト)・[設計書 §6–7](../../docs/designs/kiro-autonomous-design.md)。

> 大原則: **done は verify の終了コード 0 だけが根拠**。信頼は `--level` と各種ゲートで段階的に明け渡す。
> 迷ったら一段下のレベルで回し、`audit` のスコアが上がってから次へ進む。

---

## 0. 2 分で要点

| 段階 | 一言 | `--level` | act | done | 常駐 |
|------|------|-----------|-----|------|------|
| **L0 下見** | 何を・どの順で回すか見るだけ | `report` | しない | — | しない |
| **L1 試運転** | 手元で単発、done は承認 | `assisted` | する | 人が `approve` | しない |
| **L2 日常運用** | 常駐・ゲート付きで半自動 | `unattended` | する | 自動（ゲート通過時） | する |
| **L3 無人運用** | 予算・自己監査つきで放任 | `unattended` | する | 自動 | する（OS 自動起動） |
| **L4 スケール** | 並列・分散・横断学習 | `unattended` | する（remote 可） | 自動 | する（複数ホスト） |

各レベルの設定は `.kiro/kiro-autonomous.yaml` に書ける（**CLI > 設定ファイル > 既定**）。
下のスニペットはそのまま貼れる雛形。

> **この L0–L4 は「1 プロジェクトのバックログをどの自律度で回すか」の軸**。その上に、**目標（charter）から
> バックログを生成し、達成を評価して改善し続ける「プロジェクト層（`project`）」**がある（→ [§プロジェクト層](#プロジェクト層charter-駆動-目標から回す複数プロジェクト)）。
> 構成は **プロジェクト > バックログ**で、`<root>/projects/<name>/` に 1 プロジェクト＝1 セット。**複数プロジェクトを
> 併存**でき、`--project <name>`（未指定は `default`）で選ぶ。needs/decisions も per-project に閉じる。

---

## L0 — 下見（何も壊さない）

**目的**: 既存 backlog を 1 文字も変えずに「何が・どの順で・実行可能か」を確認する。適性を採点する。

**設定**（`.kiro/kiro-autonomous.yaml`）:
```yaml
level: report          # act しない＝backlog を変えない安全な下見
planner: none          # priority 降順→古い順で決定的（kiro-cli 不要）
executor: stub         # act を無料スタブに（誤って実行しても無害）
```

**動かし方**:
```bash
kiro-autonomous triage              # 優先順位だけ表示（inbox→ready 昇格・policy 適用）
kiro-autonomous run --level report  # 「何を・どの順で回すか」だけ報告（消化しない）
kiro-autonomous run --dry-run       # act を呼ばず1巡（配線確認）
kiro-autonomous audit               # 無人運用に値するか L0–L3 で採点・赤旗・提案
```

**やること**: タスクに**実行可能な `verify`** が付いているか確認（無いと done 不能 → 人へ回る）。
`audit` の赤旗（verify 欠落・retry 多発など）を潰す。

**卒業の目安**: `audit` が L1 以上、主要タスクに verify が付き、`triage` の順序に納得できた。

---

## L1 — 試運転（手元で単発・done は承認）

**目的**: 実際に act させるが、**done は必ず人が承認**。検証つき小修正で信頼を積む。

**設定**:
```yaml
level: assisted        # 実行はするが done は全件 review（approve 待ち）
executor: kiro         # 実エージェント（本番の act）
max_cycles: 5          # 1 run の処理数を絞って様子を見る
do_archive: true       # done は archive/ へ退避（誤りを後から追える）
```

**動かし方**（常駐させず単発で回す）:
```bash
kiro-autonomous run                 # 1 run 消化（watch しない）。assisted なので done は保留
kiro-autonomous needs               # 検収待ち（review）・判断待ち（blocked）を一覧
kiro-autonomous approve <id> --reason "確認OK"   # 承認して done 確定（決定を記録）
# 差し戻すなら needs/<id>.md に方針を書いて [x] → ready で再実行
kiro-autonomous stats               # スループット・自動化率・retry・人対応待ちを計測
```

**verify の鉄則**（ここで身につける。[偽 done 対策](#verify-の鉄則偽-done-を防ぐ)も参照）:
- **「履歴」でなく「望む最終状態」を assert** する。
  - 悪い例: `git log | grep -q refactor`（過去コミットにマッチ＝やってないのに PASS）
  - 良い例: `grep -q "def extracted_helper" util.py`（コードの結果を見る）
- 小さく分解する。verify が書けないタスクは分解が粗い兆候。

**卒業の目安**: 数十タスクを assisted で回し、`approve` した done に差し戻しがほぼ無い。`stats` の自動化率が安定。

---

## L2 — 日常運用（常駐・ゲート付きで半自動）

**目的**: `unattended` で自動 done させつつ、**危険な変更だけ人に上げる**多重ゲートで守る。常駐監視に移行。

**設定**:
```yaml
level: unattended           # ゲートを通れば自動 done（既定）
watch: true                 # 終了条件後も常駐し backlog 投入を待つ（idle 中はエージェント非起動）
poll: 5.0
regression_cmd: "pytest -q" # done 確定前のグローバル回帰検査（巻き込み事故を検知）
verify_confirm: 2           # verify を2回実行し PASS/FAIL が跨いだら flake として人へ隔離
require_progress: true      # verify=PASS でも変更が無ければ done せず人へ（偽 done を捕捉）
learn: true                 # 過去の人の判断から類似案件を自動解決（通知を減らす）
notify_cmd: "..."           # 判断待ち発生時の通知（Slack 等。任意）
```

**ゲートは `policy.md` で宣言**（パス単位の一括指定）:
```text
# .kiro-autonomous/projects/default/policy.md （policy は per-project）
gate: src/payments/**        # verify=PASS でも人の承認を要する（検収ゲート・質的レビュー向け）
protect: .github/**          # act がこのパスを触ったら done せず人へ（safety denylist）
protect: **/secrets/**
deny:  vendor/**             # そもそも積ませない
```
タスク単位なら `- review: human` / `- expect: changes`（変更必須）/ `- after: <id>`（依存順）。

**動かし方**:
```bash
kiro-autonomous run --watch         # 常駐監視（= 引数省略時の既定）
kiro-autonomous needs               # 上がってきた検収待ち・判断待ちを定期的に捌く
kiro-autonomous runlog --tail 20    # 何が起きたかを構造化ログで確認
```

**安全装置の役割**（→ [早見表](#安全装置の早見表)）: `gate`=質の承認 / `protect`=危険パスの番人 /
`regression_cmd`=巻き込み検知 / `verify_confirm`=flake 隔離 / `require_progress`=偽 done 捕捉。

**自律度はタスク毎に変えてよい**（実運用では backlog 毎に違う）。グローバル `--level` は既定で、タスク行
`- level:` が**上書き**する（実効＝明示 > 自動 > グローバル。`protect`/`gate` は常に上乗せ）:
```text
## PAY-12: 決済ロジック変更
- level: assisted      # この案件だけ done は人が承認
## DOC-3: README の typo
- level: unattended    # 同じ backlog でも雑魚は自動 done
## RISKY-9: まだ自動化しない
- level: report        # 実行せず計画に保留（塩漬け）
```
**自動昇格（opt-in）**: `- track: <名前>` を付けた同種群は `--auto-level` で、手戻り率が低ければ level を自動で
上げ、手戻り（差し戻し/回帰/偽done）で下げる。ceiling 既定 `assisted`、`--auto-level-max unattended` で完全
無人化への自動到達を解禁。「assisted で慣らし→実績で unattended」を**人手の昇格判断なしに**回せる。
```bash
kiro-autonomous run --level assisted --auto-level --auto-level-max unattended
```

**卒業の目安**: 1 週間 watch で回して赤旗ゼロ、人対応待ちが詰まらない、回帰ゲートが効いている。

---

## L3 — 無人運用（予算・自己監査つきで放任）

**目的**: 人が見ていない時間も安全に回す。**有限性（必ず止まる）**と**自己監査**を効かせる。

**設定**:
```yaml
level: unattended
watch: true
max_cost: 5.0           # 1 run の金額(USD)上限。超えたら act 停止（0=無制限）
max_tokens: 0           # 同・トークン上限
throttle: 0.8           # ソフト上限: 予算の80%で run 打ち切り・watch は report 降格
auto_adjudicate: true   # needs に落とす前に kiro-cli が積み直し可否を裁定（人の判断を減らす）
regression_revert: true # 回帰時に未コミット変更を自動で巻き戻す
rot: true               # 古い/重複/実行不能タスクを triage で掃除
```

**CI に自己監査を組み込む**（無人運用に値するかを門番に）:
```bash
kiro-autonomous audit --strict      # L0–L3 基準を満たさなければ非0で落とす（CI の1ステップに）
```

**OS 起動時から常駐**（lifecycle）:
```bash
kiro-autonomous start               # 既定で全プロジェクト（--project all）を1プロセスで常駐起動（重複は拒否）
kiro-autonomous instances           # いまどのプロジェクトを監視中か発見（all＋各プロジェクト）
kiro-autonomous stop                # all daemon を停止（--project <name> で個別・--all で全部）
# systemd は ExecStart を `kiro-autonomous run --watch --project all` にし、調整は .yaml で完結
```

**監視**: `runlog --json` を集計、`stats` で自動化率/コストを定点観測、`notify_cmd` で判断待ちを push。

**不調を感じたら診断**（ログ/状態/環境から kiro-cli が原因を切り分ける）:
```bash
kiro-autonomous doctor              # 診断のみ（無害）。env/config/program に分類して提示
kiro-autonomous doctor --fix        # env/config を自動修正し、program の不具合は gitlab-idd で起票
```
`audit` が「設定が無人運用に値するか」を採点するのに対し、`doctor` は「**いま現に何が起きているか**」を
ログ・稼働シグナルから診断する。環境/設定の問題は直し、コードの不具合だけイシューに切り出す。既定では
実行層 `kiro-flow doctor` も連携実行して所見を統合する（`[flow]` 印・`--no-flow` で本体のみ）。

**卒業の目安**: 予算内で安定収束、`audit --strict` が常時グリーン、夜間放任でも事故ゼロ。

---

## L4 — スケール（並列・分散・横断学習）

**目的**: スループットを上げ、複数ホスト・複数リポジトリへ広げる。

**設定**:
```yaml
location: daemon        # warm worker を再利用（auto/local/daemon/remote）
concurrency: 3          # 依存解決済みの独立タスクを最大3並行で submit（1=逐次）
ltm: true               # ltm-use 長期記憶へ昇格＋プロジェクト横断 recall
promote_threshold: 2    # learn ルールがこの回数効いたら横断記憶へ昇格
```

**動かし方**:
```bash
# ローカル daemon を立て、独立タスクを並行消化
kiro-flow daemon &      # warm worker
kiro-autonomous run --location daemon --concurrency 3

# 分散（remote）: git バス経由で別ホストの worker に委譲
kiro-autonomous run --location remote

# 複数ホストを横断発見（共有レジストリ＝NFS/同期/git チェックアウト）
kiro-autonomous instances --registry /shared/kiro-registry
```

**原子的クレーム**で二重実行を防ぐので、同じ backlog を複数インスタンスが見ても安全。
**注意**: remote/daemon 実行は workdir に差分が出ないため、`protect`/`require_progress` は best-effort
（パス保護・進捗判定はローカル実行のときに最も厳密に効く）。

**卒業の目安**: 並列でも順序・依存・クレームが破綻しない。横断学習で通知がさらに減る。

---

## プロジェクト層（charter 駆動）— 目標から回す／複数プロジェクト

L0–L4 が「**人が積んだバックログ**をどの自律度で消化するか」なら、`project` は「**人が書いた目標**から
バックログを起こし、達成を評価して改善し続ける」もう一段上のループ。バックログが尽きる（`drained`）と止まる
正準ループに対し、`project` は「**枯渇**」と「**目標達成**」を分離して長期に回す。

**目標を書く（charter.md・人が書く唯一の最上位入力）**:
```bash
mkdir -p .kiro-autonomous/projects/default
cp tools/kiro-autonomous/charter.md.example .kiro-autonomous/projects/default/charter.md   # 編集
```
```markdown
# Charter: my-project
## goal          # 北極星（1〜数文）
## constraints   # 守る境界（標準ライブラリのみ 等）
## assumptions   # 前提
## deliverables  # 成果物
## acceptance    # 受入 verify＝**プロジェクト done の唯一の根拠**（タスク verify と同じ鉄則）
- `pytest -q tests/`
## links         # 任意。他プロジェクトの定義＋判断(learn)を横展開で取り込む
- shared-conventions
```

**回す**（プロセスは `run` に一本化。charter.md があれば自動で目標駆動になる。専用 `project` コマンドは廃止）:
```bash
kiro-autonomous run                           # charter あり→plan→execute→evaluate（収束→人へ）
kiro-autonomous run --watch                   # 目標を満たすまで回り続ける常駐（charter 更新も待つ）
kiro-autonomous run --review-project          # acceptance 全PASS でも敵対的レビューで短絡的達成を疑う
kiro-autonomous needs                         # milestone（収束候補）を確認
kiro-autonomous approve <project> --reason "受領"   # 収束候補を完了確定（最終納品書）／続行は charter を更新して再実行
```

- **三相**: ① plan（charter をエージェントに分解させ enqueue・冪等）→ ② execute（既存の正準ループ run を
  drained まで・L0–L4 のゲートは全て温存）→ ③ evaluate（acceptance 全 PASS 判定＋opt-in 敵対的レビュー、
  未達/指摘なら改善タスク生成で次サイクル）。
- **有限停止**: 内側 run（drained/budget）＋プロジェクト層（`max_project_cycles` 既定 5 / `max_project_cost` /
  `project_stall`＝PASS 数が増えない連続回数で人へ）。暴走改善チャーンを止める。
- **done は acceptance（=verify）全 PASS のみが根拠**。敵対的レビューはタスクを足す方向のみ（自己申告 done は作れない）。

**ワーカーは定義と判断を踏まえる**: kiro-flow へ委譲する act 依頼に、**charter（定義）と `decisions/<id>.md`
（needs の判断結果）**が文脈として乗る。**`project` でも通常 `run` でも**、charter.md があれば全 act に定義が乗る
（無ければ従来どおり空＝後方互換）。`## links` があればリンク先プロジェクトの**定義＋判断（learn）**も横展開で取り込む。

**複数プロジェクトを併存させる**:
```bash
kiro-autonomous enqueue --project payments --title "…" --verify '…'   # 別プロジェクトへ積む（無ければ作成）
kiro-autonomous run     --project payments                            # そのプロジェクトを消化（charter あれば目標駆動）
kiro-autonomous needs   --project payments                            # per-project の判断待ち
kiro-autonomous start   --project payments                            # そのプロジェクトを常駐監視
kiro-autonomous instances                                            # 稼働中の全プロジェクト root を横断発見
kiro-autonomous run     --project all --watch                         # ★1プロセスで全プロジェクトを常駐監視
```
needs/decisions/policy/journal/archive/DELIVERY は per-project に閉じる。`instances` レジストリだけはグローバルで、
複数プロジェクト・複数ホストを横断発見できる。**1 プロセスで全部を回したいなら `--project all`**（プロジェクト毎に
プロセスを分けたいなら `start --project <name>` を個別に）。`--project all --watch` は新規プロジェクトも毎ラウンド自動で拾う。

---

## verify の鉄則（偽 done を防ぐ）

履歴一致 verify は「やってないのに done」を生む最大の落とし穴。3 層で守られているが、**第一は verify の書き方**。

1. **最終状態/差分を assert する**（履歴の絶対状態を見ない）。
   ```yaml
   verify: `grep -q "def new_helper" util.py`            # ◎ コードの結果
   verify: `test -n "$(git log $KIRO_BASE_REV..HEAD --grep refactor)"`  # ◎ 差分スコープ
   verify: `git log | grep -q refactor`                  # ✗ 過去コミットにマッチ
   ```
   `$KIRO_BASE_REV`（act 前の HEAD）は verify 実行時に自動で渡る。
2. **変更が出るはずの作業には `- expect: changes`** を付ける（無変更 done を人へ）。逆に正当な無変更タスクは
   `- expect: none`。全体で強制するなら `--require-progress`（または `require_progress: true`）。
3. **成果参照は自動で真正化**される。DELIVERY/needs には act 前以降の新規差分のみが載り、無ければ `(変更なし)`。

**verify を書くのが難しいとき**: 自分で書けるのが最良だが、`- accept: <自然言語>`（実行時にエージェントが決定的 verify を
合成）や `- verify_template: file-contains :: path :: 文字列`（決定的展開・エージェント不要）で代替できる。最終的に
concrete な verify に変換されるので「done は verify のみが根拠」の鉄則は保たれる。シェルで検証できないものは `- review: human` で
人承認に回す。

---

## 安全装置の早見表

| 装置 | 設定 | 何を止めるか | 推奨レベル |
|------|------|--------------|-----------|
| 自律度 | `level: report/assisted/unattended` | act/done の権限そのもの | 全段階 |
| タスク単位の自律度 | `- level:`（上書き）/ `- track:`＋`--auto-level` | 案件毎にゲートを出し入れ・実績で自動調整 | L2+ |
| 検収ゲート | `policy.md: gate:` / `- review: human` | verify=PASS でも質的に要承認 | L2+ |
| パス保護 | `policy.md: protect:` | 危険パス（CI/秘密等）への変更 | L2+ |
| 回帰ゲート | `regression_cmd` (+`regression_revert`) | done が他を壊す巻き込み事故 | L2+ |
| flake 隔離 | `verify_confirm: 2` | 揺れる verify の誤 done / retry 暴走 | L2+ |
| 偽 done 捕捉 | `require_progress` / `expect: changes` | 変更ゼロの done（履歴一致 verify） | L2+ |
| 予算停止 | `max_cost` / `max_tokens` / `throttle` | コスト暴走（必ず有限停止） | L3+ |
| 自己監査 | `audit --strict` | 適性未達のまま無人運用すること | L3+ |
| 裁定 | `auto_adjudicate` | 人の判断行きを減らす（積み直し可否） | L2+ |

---

## 設定の決め方・早見表

| キー | 既定 | 効く段階 | メモ |
|------|------|---------|------|
| `level` | `unattended` | L0–L1 | 最初は `report`→`assisted` で信頼を積む。タスク毎は `- level:` で上書き |
| `auto_level` / `auto_level_max` | `false` / `assisted` | L2+ | `- track:` 群を実績連動で自動昇格。無人化到達は max を `unattended` に |
| `planner` | `kiro` | L0 | 様子見は `none`（決定的・エージェント不要） |
| `executor` | `kiro` | L0 | 下見は `stub`（無料・無害） |
| `watch` / `poll` | `false` / `5.0` | L2+ | 常駐監視。idle 中はエージェント非起動 |
| `max_cycles` | `20` | L1 | 試運転は `5` 程度に絞る |
| `verify_confirm` | `1` | L2+ | flake が疑わしければ `2`（コストは回数分） |
| `require_progress` | `false` | L2+ | 偽 done を全体で弾く。`expect:` で個別調整 |
| `regression_cmd` | なし | L2+ | done 前のグローバル検査（例 `pytest -q`） |
| `max_cost`/`max_tokens` | `0` | L3+ | 無人運用は必ず上限を入れる |
| `throttle` | `0.0` | L3+ | 上限の手前で減速（例 `0.8`） |
| `concurrency` | `1` | L4 | daemon/remote と併用で並列消化 |
| `location` | `auto` | L4 | `daemon`=warm 再利用 / `remote`=分散 |
| `ltm` | `false` | L4 | プロジェクト横断の学習（home へ書く） |
| `max_project_cycles` | `5` | project | 改善サイクルの上限（必ず有限停止） |
| `project_stall` | `2` | project | acceptance PASS 数が増えない連続回数→人へ（自動チャーン停止） |
| `max_project_cost` | `0.0` | project | プロジェクト累計コスト上限(USD・0=無制限) |
| `review_project` | `false` | project | evaluate で敵対的レビューを上乗せ（短絡的達成を疑う・opt-in） |

> 完全な書式・検索順序（`./.kiro/` → `~/.kiro/`）・全キーは README「設定ファイル」を参照。
> `--project <name>` で操作対象プロジェクトを選ぶ（未指定は `default`。実体は `<root>/projects/<name>/`）。

---

## 困ったとき

| 症状 | 原因の典型 | 対処 |
|------|-----------|------|
| done になるが成果物が無い | 履歴一致 verify（偽 done） | verify を最終状態/差分へ。`--require-progress` / `expect: changes` |
| 同じタスクが何度も retry | verify が flaky | `verify_confirm: 2` で隔離。テスト/環境側を直す |
| 人対応待ちが詰まる | ゲート過剰 / verify 欠落 | `needs` を捌く。`auto_adjudicate`・`learn` を有効化 |
| いつまでも止まらない不安 | 有限性の確認不足 | `max_cycles`/`max_seconds`/`max_cost` と `throttle` を設定 |
| 無人運用してよいか不安 | 適性が未採点 | `audit`（CI は `audit --strict`）で L レベルを上げてから進む |
| 二重に実行されそう | 複数インスタンス | 原子的クレームで安全。`instances` で監視先を確認 |

---

要するに: **`report` で覗き、`assisted` で承認しながら慣らし、`unattended`＋ゲートで日常運用、予算と
`audit --strict` を足して無人化、最後に並列・分散へ**。各段で `audit` のスコアを上げてから一段進むのが安全。
