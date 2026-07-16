# agent-project 運用ガイド（熟練度別）

「いきなり無人運用にしない」ためのガイド。**L0 下見 → L1 試運転 → L2 日常運用 → L3 無人運用 → L4 スケール**の
順に、各段階で**何を設定し・どう動かし・いつ次へ進むか**をまとめる。詳細仕様は [README](README.md) と
[統合設計書](../../docs/designs/agent-project-design.md) を参照。目標から回す上位ループと複数プロジェクトは
[§プロジェクト層](#プロジェクト層charter-駆動-目標から回す複数プロジェクト)・[設計書 §6–7](../../docs/designs/agent-project-design.md)。

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

各レベルの設定は `.agent/agent-project.yaml` に書ける（**CLI > 設定ファイル > 既定**）。
下のスニペットはそのまま貼れる雛形。**結論だけ知りたい人は** → [§おすすめ構成（本番）](#おすすめ構成本番-pc-起動時に常駐--executorgitlab--busgit)
（PC 起動時に両 daemon 常駐／executor=gitlab／bus=git の完成形レシピ）。

> **この L0–L4 は「1 プロジェクトのバックログをどの自律度で回すか」の軸**。その上に、**目標（charter）から
> バックログを生成し、達成を評価して改善し続ける「プロジェクト層（`project`）」**がある（→ [§プロジェクト層](#プロジェクト層charter-駆動-目標から回す複数プロジェクト)）。
> 構成は **1 プロジェクト = 1 ディレクトリ = 1 プロセス**。プロジェクトルート（cwd）直下に charter.md /
> backlog/ / needs/ decisions/ が集約され、複数プロジェクトはディレクトリを並べてそれぞれで回す。

---

## L0 — 下見（何も壊さない）

**目的**: 既存 backlog を 1 文字も変えずに「何が・どの順で・実行可能か」を確認する。適性を採点する。

**設定**（`.agent/agent-project.yaml`）:
```yaml
level: report          # act しない＝backlog を変えない安全な下見
planner: none          # priority 降順→古い順で決定的（エージェント CLI 不要）
executor: stub         # act を無料スタブに（誤って実行しても無害）
```

**動かし方**:
```bash
agent-project triage              # 優先順位だけ表示（inbox→ready 昇格・policy 適用）
agent-project run --level report  # 「何を・どの順で回すか」だけ報告（消化しない）
agent-project run --dry-run       # act を呼ばず1巡（配線確認）
agent-project audit               # 無人運用に値するか L0–L3 で採点・赤旗・提案
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
executor: agent         # 実エージェント（本番の act）
max_cycles: 5          # 1 run の処理数を絞って様子を見る
do_archive: true       # done は archive/ へ退避（誤りを後から追える）
```

**動かし方**（常駐させず単発で回す）:
```bash
agent-project run                 # 1 run 消化（watch しない）。assisted なので done は保留
agent-project needs               # 検収待ち（review）・判断待ち（blocked）を一覧
agent-project approve <id> --reason "確認OK"   # 承認して done 確定（決定を記録）
# 差し戻すなら needs/<id>.md に方針を書いて [x] → ready で再実行
agent-project stats               # スループット・自動化率・retry・人対応待ちを計測
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
# <root>/policy.md （policy はプロジェクト毎）
gate: src/payments/**        # verify=PASS でも人の承認を要する（検収ゲート・質的レビュー向け）
protect: .github/**          # act がこのパスを触ったら done せず人へ（safety denylist）
protect: **/secrets/**
deny:  vendor/**             # そもそも積ませない
```
タスク単位なら `- review: human` / `- expect: changes`（変更必須）/ `- after: <id>`（依存順）。

**動かし方**:
```bash
agent-project run --watch         # 常駐監視（= 引数省略時の既定）
agent-project needs               # 上がってきた検収待ち・判断待ちを定期的に捌く
agent-project runlog --tail 20    # 何が起きたかを構造化ログで確認
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
agent-project run --level assisted --auto-level --auto-level-max unattended
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
auto_adjudicate: true   # needs に落とす前に エージェント CLI が積み直し可否を裁定（人の判断を減らす）
regression_revert: true # 回帰時に未コミット変更を自動で巻き戻す
rot: true               # 古い/重複/実行不能タスクを triage で掃除
```

**CI に自己監査を組み込む**（無人運用に値するかを門番に）:
```bash
agent-project audit --strict      # L0–L3 基準を満たさなければ非0で落とす（CI の1ステップに）
```

**OS 起動時から常駐**（lifecycle）:
```bash
agent-project start               # cwd のプロジェクトを常駐起動（重複は拒否）
agent-project instances           # いまどのプロジェクトを監視中か発見（all＋各プロジェクト）
agent-project stop                # cwd のプロジェクトの daemon を停止（--all で全部）
# systemd は ExecStart を `agent-project run --watch` にし、調整は .yaml で完結
```

**監視**: `runlog --json` を集計、`stats` で自動化率/コストを定点観測、`notify_cmd` で判断待ちを push。

**不調を感じたら診断**（ログ/状態/環境から エージェント CLI が原因を切り分ける）:
```bash
agent-project doctor              # 診断のみ（無害）。env/config/program に分類して提示
agent-project doctor --fix        # env/config を自動修正し、program の不具合は gitlab-idd で起票
```
`audit` が「設定が無人運用に値するか」を採点するのに対し、`doctor` は「**いま現に何が起きているか**」を
ログ・稼働シグナルから診断する。環境/設定の問題は直し、コードの不具合だけイシューに切り出す。既定では
実行層 `agent-flow doctor` も連携実行して所見を統合する（`[flow]` 印・`--no-flow` で本体のみ）。

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
agent-flow daemon &      # warm worker
agent-project run --location daemon --concurrency 3

# 分散（remote）: git バス経由で別ホストの worker に委譲
agent-project run --location remote

# 複数ホストを横断発見（共有レジストリ＝NFS/同期/git チェックアウト）
agent-project instances --registry /shared/agent-registry
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
cp tools/agent-project/charter.md.example ./charter.md   # プロジェクトルートに置いて編集
# 複数バージョンを並行管理するなら charter.md の代わりに charters/<バージョン>.md を並べる
# （全バージョンをラウンドロビンで駆動。タスクは charter タグでスコープされる）
```
```markdown
# Charter: my-project
## goal          # 北極星（1〜数文）
## constraints   # 守る境界（標準ライブラリのみ 等）
## assumptions   # 前提
## deliverables  # 成果物
## acceptance    # 受入 verify＝**プロジェクト done の唯一の根拠**（タスク verify と同じ鉄則）
- `pytest -q tests/`
- accept: README に使用例が載っている   # 自然文も可。run 時にエージェントが決定的 verify へ合成（不能なら人へ）
## links         # 任意。他プロジェクトの定義＋判断(learn)を横展開で取り込む
- shared-conventions
```

**回す**（プロセスは `run` に一本化。charter.md があれば自動で目標駆動になる。専用 `project` コマンドは廃止）:
```bash
agent-project run                           # charter あり→plan→execute→evaluate（収束→人へ）
agent-project run --watch                   # 目標を満たすまで回り続ける常駐（charter 更新も待つ）
agent-project run --review-project          # acceptance 全PASS でも敵対的レビューで短絡的達成を疑う
agent-project needs                         # milestone（収束候補）を確認
agent-project approve <project> --reason "受領"   # 収束候補を完了確定（最終納品書）／続行は charter を更新して再実行
```

- **三相**: ① plan（charter をエージェントに分解させ enqueue・冪等）→ ② execute（既存の正準ループ run を
  drained まで・L0–L4 のゲートは全て温存）→ ③ evaluate（acceptance 全 PASS 判定＋opt-in 敵対的レビュー、
  未達/指摘なら改善タスク生成で次サイクル）。
- **有限停止**: 内側 run（drained/budget）＋プロジェクト層（`max_project_cycles` 既定 5 / `max_project_cost` /
  `project_stall`＝PASS 数が増えない連続回数で人へ）。暴走改善チャーンを止める。
- **done は acceptance（=verify）全 PASS のみが根拠**。敵対的レビューはタスクを足す方向のみ（自己申告 done は作れない）。

**ワーカーは定義と判断を踏まえる**: agent-flow へ委譲する act 依頼に、**charter（定義）と `decisions/<id>.md`
（needs の判断結果）**が文脈として乗る。**`project` でも通常 `run` でも**、charter.md があれば全 act に定義が乗る
（無ければ従来どおり空＝後方互換）。`## links` があればリンク先プロジェクトの**定義＋判断（learn）**も横展開で取り込む。

**複数プロジェクトを併存させる**:
```bash
cd ~/projects/payments && agent-project enqueue --title "…" --verify '…'   # 別プロジェクトへ積む
cd ~/projects/payments && agent-project run                                 # そのプロジェクトを消化（charter あれば目標駆動）
cd ~/projects/payments && agent-project needs                               # そのプロジェクトの判断待ち
cd ~/projects/payments && agent-project start                               # そのプロジェクトを常駐監視
```

複数プロジェクトはディレクトリを並べ、`instances` で複数プロジェクト・複数ホストを横断発見できる。
束ねた可視化・操作は agent-dashboard が各ルートの clone を登録して行う。

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

## おすすめ構成（本番）— PC 起動時に常駐 ／ executor=gitlab ／ bus=git

L0–L4 を一通り通したら、最終的にはこの構成に落ち着くのがおすすめ。**両 daemon を OS 起動時から常駐**させ、
**実行は GitLab 越し（executor=gitlab）**、**協調は共有 git リポジトリ（bus=git）**で行う。狙いは 3 つ:

- **bus=git**: バスを共有 git リポジトリにすると、複数 PC が同じキューを見て協調できる（原子的クレームで二重実行
  なし）。マシンが落ちても状態は git に残るので復帰が容易。
- **executor=gitlab**: 各タスクを GitLab イシュー化し、レビュアーが `status:approved` を付けるまで待って完了とみなす。
  **ローカルに エージェント CLI が無くても**作業を委譲でき、人手の承認が自然なゲートになる。
- **常駐 daemon**: 投入即実行・warm worker 再利用。`agent-project`（生産者＝backlog/charter を回す）と
  `agent-flow daemon`（消費者＝バスを拾って実行）を分けて常駐させる。

```
 agent-project run --watch        共有 git バス          agent-flow daemon            GitLab
 （charter/backlog を回す）  ──submit──▶  (git repo)  ──claim──▶ （orchestrator/worker）──issue──▶ レビュー承認
   location: remote                                          executor: gitlab          status:approved → done
```

### 1) 設定ファイル

**`~/.agent/agent-flow.yaml`**（消費側 daemon。バスと実行委譲を定義）:
```yaml
git: git@example.com:team/flow-bus.git   # ← バスを共有 git リポジトリに（bus=git）
git_branch: main
# git_subdir: flow                       # 1 リポジトリを他用途と共有するならサブディレクトリに隔離
executor: gitlab                         # ← 実行を GitLab イシューへ委譲（executor=gitlab）
poll: 5.0                                # git バスはやや大きめが目安
lock_dir: /tmp/agent-flow-locks           # ← daemon ロックの置き場（autonomous 側と一致させる・後述）
gitlab:                                  # executor: gitlab のときだけ使う委譲設定
  conn_label: default                    # gitlab-idd の connections.yaml の接続ラベル
  repo_url: "https://gitlab.com/group/repo"
  labels: "status:open,assignee:any"
  priority: "priority:normal"
  poll_interval: 30
  timeout: 86400                         # approved 待ちのタイムアウト（秒）
  approved_label: "status:approved"      # この状態で完了とみなす
```
> 前提: `gitlab-idd` スキル（`.github/skills/gitlab-idd`）が導入済みで、`connections.yaml` か `GITLAB_TOKEN`
> で接続が設定されていること。

**`~/.agent/agent-project.yaml`**（生産側。同じ git バスへ offload）:
```yaml
level: unattended          # 無人運用（承認ゲートは GitLab 側の status:approved が担う）
watch: true                # 常駐。idle 中はエージェント非起動
executor: gitlab           # agent-flow へそのまま委譲（実行層と揃える）
location: remote           # ← 一致タスクを git バスへ submit（別ホスト/別daemonの worker が拾う）
git_bus: git@example.com:team/flow-bus.git   # ← agent-flow.yaml の git: と同一リポジトリ
git_branch: main
# git_subdir: flow                            #   agent-flow.yaml と揃える
lock_dir: /tmp/agent-flow-locks                # ← agent-flow 側 lock_dir と一致（外部 daemon を検知するため）
max_cost: 5.0              # 無人運用は必ず予算上限を入れる（必ず有限停止）
throttle: 0.8             # 上限の手前で減速
auto_adjudicate: true     # needs に落とす前に積み直し可否を裁定（人の判断を減らす）
```

> **`lock_dir` を両者で一致**させるのが要点。これで `agent-project` が外部起動の `agent-flow daemon` を
> 検知し、二重起動を避けつつ warm worker を再利用できる（既定は `$TMPDIR/agent-flow-locks` でプロセス毎にズレうる）。

### 2) PC 起動時から常駐（systemd / Linux）

`~/.config/systemd/user/` にユニットを置き、ユーザーサービスとして自動起動する（`loginctl enable-linger <user>`
でログイン前から常駐）。git バスへ SSH するため、鍵を `ssh-agent` 経由か `IdentityFile` で読めるようにしておく。
設定は上記のとおり `~/.agent/` に置けば**両ツールとも自動で読み込まれる**（検索順 `./.agent/` → `~/.agent/`）ので、
ExecStart に `--config` は不要。

**`agent-flow-daemon.service`**（消費側）:
```ini
[Unit]
Description=agent-flow daemon (git bus / gitlab executor)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=%h/.local/bin/agent-flow daemon
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

**`agent-project.service`**（生産側。全プロジェクトを 1 プロセスで監視）:
```ini
[Unit]
Description=agent-project watch (all projects)
After=agent-flow-daemon.service
Wants=agent-flow-daemon.service

[Service]
WorkingDirectory=%h/work/my-repo
ExecStart=%h/.local/bin/agent-project run --watch
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now agent-flow-daemon agent-project
loginctl enable-linger "$USER"     # ログアウト/再起動後も常駐させる
```

> **macOS** は launchd を使う（`~/Library/LaunchAgents/<label>.plist` に `RunAtLoad=true` /
> `KeepAlive=true`、`ProgramArguments` に上記 `ExecStart` 相当を並べる）。手元で単発に常駐させるだけなら
> `agent-flow daemon &` ＋ `agent-project run --watch &` でも可（ただし再起動で消える）。

### 3) 稼働確認

```bash
agent-project doctor          # 実行層 agent-flow daemon との連携まで含めて健康診断（[flow] 印で統合）
agent-project instances       # いまどのプロジェクトを監視中か（all＋各プロジェクト）
agent-project stats           # 自動化率・コストを定点観測
agent-project needs           # 人の判断待ち（承認は GitLab の status:approved 側で進む）
```

**注意**: remote/git バス実行は手元 workdir に差分が出ないため、`protect` / `require_progress` は best-effort
（厳密に効くのはローカル実行時）。質のゲートは **GitLab のレビュー承認**＋`policy.md: gate:` に寄せる。

---

## 設定の決め方・早見表

| キー | 既定 | 効く段階 | メモ |
|------|------|---------|------|
| `level` | `unattended` | L0–L1 | 最初は `report`→`assisted` で信頼を積む。タスク毎は `- level:` で上書き |
| `auto_level` / `auto_level_max` | `false` / `assisted` | L2+ | `- track:` 群を実績連動で自動昇格。無人化到達は max を `unattended` に |
| `planner` | `agent` | L0 | 様子見は `none`（決定的・エージェント不要） |
| `executor` | `agent` | L0 | 下見は `stub`（無料・無害） |
| `watch` / `poll` | `false` / `5.0` | L2+ | 常駐監視。idle 中はエージェント非起動 |
| `max_cycles` | `20` | L1 | 試運転は `5` 程度に絞る |
| `verify_confirm` | `1` | L2+ | flake が疑わしければ `2`（コストは回数分） |
| `verify_cwd` | なし | project | verify/acceptance の実行先（明示すると常に最優先）。git-bus 等で workdir に成果が無いとき repo のクローン先を指す。未指定でも `- workspace:` 指定タスクは該当 repo を指定 branch/path で自動 clone して検証し、acceptance も単一 repo を自動 clone する |
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

> 完全な書式・検索順序（`./.agent/` → `~/.agent/`）・全キーは README「設定ファイル」を参照。
> 操作対象のプロジェクトはカレントディレクトリ（または `--root`）で決まる。

---

## stub で新機能を一巡する（エージェント不要の動作確認）

採点（assess）・リスクダイジェスト・spec 連鎖・rules/context 注入を、**エージェント CLI 無しで**
決定的に確認する手順。空ディレクトリで以下を上から流すだけでよい（実行済みの検証記録。
⚠ リポジトリ直下では叩かない——cwd の設定を拾う。必ず専用の空ディレクトリで）。

```bash
mkdir stub-demo && cd stub-demo
cat > agent-project.yaml <<'EOF'
planner: none        # 決定的順位（エージェント不要）
flow_planner: stub
executor: stub       # act は無害スタブ
spec_track: true     # spec ルーティングを試す
EOF
```

**1) 採点と実行前レビュー票**（assess は stub では決定的ヒューリスティック）:
```bash
agent-project enqueue --title "挨拶メッセージ機能を追加する" --verify "true"
agent-project run --once
grep assess backlog/*.md        # → - assess: c=1 r=1 a=1（verify 有＝曖昧さ 1）
grep assess needs/*.md          # 実行前レビュー票（plan-review）にも載る
```

**2) リスクダイジェスト付きの検収**:
```bash
agent-project approve <id> --reason "実行許可"    # plan-review を承認
agent-project run --once                          # stub act → verify PASS → 検収待ち
head -8 needs/<id>.md                            # frontmatter に risk: low
grep -A3 "## リスク" needs/<id>.md               # 決定的な判断材料
agent-project approve <id> --reason "成果OK"      # done 確定（archive/ と DELIVERY.md に納品）
```

**3) spec 連鎖**（policy で強制。採点しきい値でも同じ経路）:
````bash
echo "spec: 認証" > policy.md
agent-project enqueue --title "認証つきAPIを追加する" --verify "true"
agent-project approve <id> --reason "実行許可"
agent-project run --once          # <id>-spec が前置され、<id> は after: <id>-spec で待つ
agent-project approve <id>-spec --reason "spec 作成を許可"
# stub は spec を書けないので、エージェントの代役として人が specs/<id>/ に 3 ファイルを書く:
mkdir -p specs/<id>
echo "# 要求仕様" > specs/<id>/spec.md
echo "# 設計"     > specs/<id>/design.md
cat > specs/<id>/tasks.md <<'EOF'
```json
[{"title": "JWT ミドルウェアを作る", "verify": "true"},
 {"title": "hello エンドポイントを作る", "verify": "true", "after": ["JWT ミドルウェアを作る"]}]
```
EOF
agent-project run --once                          # verify PASS → spec の検収待ち
agent-project approve <id>-spec --reason "spec OK"
agent-project run --once --dry-run                # tasks.md が実装タスクへ展開される
grep "after\|spec:" backlog/*.md   # after が title→id 解決・元タスクは総合検証（after: 実装群）
````

**4) rules.md / context の常時注入**（暗黙知の伝達を bus 上で観測）:
```bash
printf -- "- テストは必ず pytest -q で実行する\n" > rules.md
mkdir -p context && printf "src/ 配下が本体\n" > context/app.md
agent-project approve <実装タスクid> --reason "実行許可"
agent-project run --once --no-cleanup             # bus を掃除せず残す
grep -o "pytest -q で実行する\|src/ 配下が本体\|仕様（spec 前段の成果" bus/runs/*/meta.json
# → act 要求文にプロジェクトルール・リポジトリ理解・spec 本文が注入されている
```

stub で確認**できない**もの: assess の LLM 採点（ヒューリスティックのみ）・accept からの
verify 合成・repo-map の自動生成（stub では生成しない。手書き context/*.md の注入は上記で確認可）・
rules.md への learn 自動昇格（auto-resolve 実績が要る。単体テスト `ProjectRulesTests` が担保）。

---

## プロジェクト固有ルール（暗黙知）の記録と伝達

フローを回して判明した「このプロジェクトのやり方」は、届く範囲の違う 5 層で記録できる:

| 層 | 書き方 | 届く範囲 |
|----|--------|---------|
| `feedback` | needs 票に記入 / `revise --feedback` | そのタスクの**次の試行のみ**（次の差し戻しで上書き） |
| **run ブリーフ** | 差し戻し（feedback/revise/却下/cohort）＋ノード発見を自動蓄積（`<root>/brief/<id>.md`） | **そのタスク/ブランチの以後の全 run・全分散ノード**（追記のみ＝過去も残る・リトライ横断・一時） |
| `learn/avoid` | 差し戻し・承認理由から自動抽出（decisions/） | **タイトルが類似する**タスクのみ（Jaccard recall） |
| **`rules.md`** | **人が直接書く**（正本）＋効いた learn の自動昇格（既定 on） | **全タスク常時**（act / plan / verify 合成） |
| ltm | `promote` / `--ltm`（opt-in） | プロジェクト横断 |

`feedback` が「そのタスクの次の 1 試行だけ・上書き」なのに対し、**run ブリーフ**は同じタスク/ブランチの
差し戻し意図と各ノードの発見制約を**追記のみ**で溜め、リトライ後（新 run）でも全分散ノードへ伝播して
一貫性を保つ（詳細は README「run ブリーフ」）。**正本 `rules.md`（恒久・人が書く）↔ 一時 `brief/`
（タスク/ブランチ限り・自動蓄積）** の対比。ブリーフは同じ `<root>` 直下に置かれ、done/マージで役目を
終える（一般化できる項目は learn→rules 昇格で正本へ格上げ）。

「同じ指摘を何度もしている」と感じたら rules.md に一行書くのが最短。システム側も、learn が
auto-resolve で `promote_threshold`（既定 2）回効いたら rules.md の `## 自動昇格` 節へ出典コメント
付きで追記する（人がいつでも編集・削除できる。追記はプロンプト文脈が増えるだけで、done の条件や
policy には影響しない）。

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
