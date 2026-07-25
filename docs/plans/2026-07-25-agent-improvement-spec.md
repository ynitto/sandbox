# agent シリーズ改良 仕様案

ステータス: ドラフト(レビュー待ち)
入力: `docs/plans/2026-07-25-imrovement-agent.md`(要件の種)
参照した既存設計: `2026-07-24-single-resident-controller-design.md` / `2026-07-23-delegation-board-distributed-bidding-design.md` / `2026-07-12-agent-spec-flow-integration.md` / `2026-07-25-flow-planner-granularity-design.md`

---

## 1. 要件の種の整理

要件の種 12 件(C1〜C12)を現実装と突き合わせ、重複を畳んで 9 件の仕様(S1〜S9)に再構成した。

| # | 要件の種(原文の行) | 真の要求 | 仕様 |
|---|---|---|---|
| C1 | 管理対象フォルダの整理・state repo 設定の簡素化 (:8) | 状態専用リポジトリを唯一の方式にし、設定の層を減らす | S1 |
| C2 | dashboard で管理外フォルダ(kiro-loop/ステートマシン)も扱いたい (:9) | 「定常業務だけのフォルダ」を宣言する正規の口 | S2 |
| C3 | flow/amigos の git clone のリモート負荷 (:10) | ノード固有のローカルクローンを全経路で使う | S3 |
| C4 | CLI 起動 cwd を repos.json から選択・ローカルパスを push できない (:11) | 同上(共有レジストリとノード固有パスの分離) | S3 |
| C5 | 検収がローカル前提・リモートレビューの差し戻しタイミング (:12) | 検収のリモート寄せと決着契約の明文化 | S4 |
| C12 | verify が機能していない (:19) | 検証の現実路線化(試行錯誤の正式化・環境ゲート) | S5 |
| C7 | 計画の試行錯誤環境が無い(抽象的・重複・削除・プロンプト固定) (:14) | バックログのライフサイクル管理と計画のカスタマイズ口 | S6 |
| C8 | 突発バックログの整合的な取り込み (:15) | 随時入力の「整合パス」 | S6 |
| C9 | 気になる観点の書き溜め→バックログ化 (:16) | 同上(蓄積→人の合図で分解、という変種) | S6 |
| C6 | スペック駆動×ブラウンフィールドのギャップ (:13) | spec の軽量段階の導入 | S7 |
| C10 | board の UI(状況確認・手動入札・キャンセル) (:17) | board 観測/操作の UI と入札経路 | S8 |
| C11 | 診断を tmux/CLI チャット呼び出しに (:18) | 診断の対話化 | S9 |

### 重複・依存の分析

- **C3 と C4 は同じ問題の両面**。clone 効率化の機構(URL 単位共有 bare ミラー + detached worktree、`agent_flow/gitcache.py`)は実装済みで、`repos.json` の `local` キーも agent-flow まで伝搬済み(`request.py:492-504` → `workspace.py:157-158` → `gitcache.py:172-212`)。未解決なのは「`local` はホスト固有の絶対パスなのに、repos.json は state repo 経由で全 PC に push される」という宣言場所の矛盾のみ。よって S3 は「ノード固有宣言層の新設」1 本に畳む。
- **C8 と C9 は「随時入力→既存計画と整合を取ってバックログ化」の変種**(即時か蓄積かの違い)。整合を取る機構(重複照合・charter 帰属)は共通なので S6 に統合。
- **C5 と C12 は連続した問題**。verify(機械検証)が環境差で失敗して人検収に倒れる → その人検収がローカル前提で見られない、という因果。S4(検収)が先、S5(verify)がそれを正規フォールバックとして使う。
- **C1 は single-resident-controller 設計の続き**。「状態ルートは常に git リポジトリ」(同設計 §4.1)が既に方針化されており、S1 はその完遂 + 設定キーの削減。
- **C10 は bidding 設計 P1(未着手)そのもの** + 手動入札(IPC 自体が無い)の追加。常駐体の board tick(実装計画 W1-11、未実装)に依存する。
- **C2 は W2-4(一覧の単一ソース化)の副作用**。表示側は非プロジェクトフォルダの分岐(`renderer.js:1605-1618`)を既に持っており、宣言の口だけが失われている。

### 依存グラフと導入順序

```
Phase 1(基盤・宣言層):   S1 ── S2      S3
                          │  (host.yaml 拡張を共有)
Phase 2(検収と検証):          S4 → S5   (S4 は S3 の local 解決を利用)
Phase 3(計画):                S6 → S7
Phase 4(UI/対話):             S8(常駐体 board tick に依存)   S9(独立)
```

S1/S3 が他のすべての足場になる。S6/S7 と S9 は独立に着手できる。

---

## 2. 現実装の要点(調査結果サマリ)

仕様の前提となる事実のみ列挙する(詳細な行番号は各仕様の節に記載)。

- **agent-project**: 状態専用リポジトリ(`state_repo`)は opt-in。未設定なら旧 worktree 方式にフォールバック(`configfile.py:340-344`)。設定時は `state_backup_branch` 無効化・誤ディレクトリ拒否・専用 clone へのリダイレクトが強制される。プロジェクト宣言の単一ソースは `~/.agents/agent-project.host.yaml`。
- **repos.json**: charter から自動生成され(`charter.py:370-397`)、状態同期の対象(`state.py:203-207`、リモート優先ファイル)。スキーマにはホスト固有の `local`/`dir` キーが存在するが、自動生成では書き出されず、書けば全 PC に伝播する。
- **agent-flow**: 1 run = 1 ワークスペース。作業ツリーは `/tmp` の mkdtemp 配下で、終了時に必ず消える(`workspace.py:145`, `:203-213`)。共有 bare ミラー(`$TMPDIR/kiro-git-cache`、agent-project/flow-worker と共用)から detached worktree を切る 3 段フォールバックが実装済み。板(agent-board)経由の請負では公示の workspace をそのまま使い、自ノードの `local` をマージしない(`agent_flow/board.py:272-277`)。
- **agent-amigos**: 作業リポジトリを clone しない(文書・調査成果物専用)。設計上のコードブランチ配送(`amigos/<mid>/<role>`)は未実装で、納品書に参照文字列を書くのみ(`delivery.py:127-131`)。
- **verify(agent-project)**: 人の `verify:` / 決定的 `verify_template` / 自然文 `accept` からのエージェント合成の 3 系統。合成は 1 回の LLM 呼び出し + 静的スクリーニングのみで、実行して直すループを持たない(`verify.py:491-521`)。失敗はリトライ消費 → `_escalate` で人へ。agent-flow 側の `verify` ノードは別物(LLM による検算ゲート、フェイルクローズ)。
- **計画パイプライン**: charter → backlog の分解プロンプトはハードコード(`plan.py:101-125`)。重複照合はタイトルの Jaccard 類似(閾値 0.5)のみ。削除タスクの墓標は無く、drained/charter 変更/replan で同種タスクが再生成されうる。内側 flow-planner はスキル名固定・`--granularity` 非伝播。
- **agent-dashboard**: Electron。プロジェクト一覧は常駐体が書く `engine/status.json` の `children[]` のみ(登録 UI は廃止済み)。CLI チャットの cwd は選択中プロジェクトのフォルダ 1 択。検収 diff はローカルパス前提(`git.js:226-227`)で、MR は外部ブラウザに開くだけ。GitLab コメント/ラベルからの決着推定はフロー画面の表示先読み専用。board の IPC(list/post/award/cancel)はあるが UI ゼロ、bid の IPC は無い。診断(doctor)はヘッドレス 1 発実行。
- **agent-board**: 実行プロセスを持たない git リポジトリ + ファイル契約。常駐一本化設計で「板が必須・PC 内 1 クローン・push は常駐体のみ・落札はノード直轄ワーカーで実行」に移行予定。board tick(W1-11)は二重落札リスクを理由に意図的に未実装。
- **kiro-loop / ステートマシン**: dashboard の cowork(定常業務)機能が `.kiro/kiro-loop.yaml` と `.statemachine/*/workflow.yaml` を自動発見して実行する仕組みは実装済み。走査対象が engine/status.json の children に限られることだけが制約。

---

## 3. 仕様

### S1: 設定の簡素化 — 状態専用リポジトリを唯一の方式にする(C1)

**現状の問題**
- `state_repo` が opt-in のため、worktree 方式(`state_worktree_dir` / `state_branch` / `state_commit` / `state_push` / `state_backup_branch` の 5 キー)と 2 系統が併存し、フォールバック分岐(`configfile.py:313-344`)・テスト・移行手順書が複雑化している。
- `state_repo:` は「状態 clone を作る前に読める場所」に必要なため、成果物リポジトリ側の `agent-project.yaml` がブートストラップを兼ねる(`docs/guides/state-repo-migration.md:115-168`)。「設定がどこにあり、どれが正か」が分かりにくい直接の原因。

**仕様**
1. **状態ルートは常に状態専用リポジトリとする**(single-resident-controller 設計 §4.1「状態ルートは常に git リポジトリ」の完遂)。worktree 方式と関連 5 キーを廃止。旧キーを検出したら fail-fast でエラーにし、`migrate-state-repo.sh` への誘導メッセージを出す。
2. **ブートストラップの一本化**: `agent-project.host.yaml` の projects エントリで状態リポジトリを直接宣言する。
   ```yaml
   projects:
     - name: example
       state_repo: https://git.example.com/example-state.git   # 必須
       root: /home/me/projects/example-state                    # 省略可(既定: ~/.agents/projects/<name>)
   ```
   常駐体が clone・root 解決を担い、成果物リポジトリ側の `agent-project.yaml` はブートストラップ役を失う(置いてあっても無視、警告のみ)。動作パラメータ(`agent_cli` / `model` / ゲート類)は**状態リポジトリ直下の `agent-project.yaml`** が正となる。
3. **設定の 3 層を明文化**: ① `host.yaml` = ノード宣言(どの PC で何を動かすか + S3 のローカル環境)、② 状態リポジトリの `agent-project.yaml` = プロジェクトの動作(全 PC 共有)、③ CLI フラグ = 一時上書き。profile(`PROFILE_LOCAL_KEYS`)は host.yaml に吸収して廃止する。
4. 単発実行(`agent-project run` を cwd で直接叩く)のためには `--state-repo <url>` 指定または cwd が状態 clone であることを要求する。cwd が成果物リポジトリのときの暗黙リダイレクトは廃止。

**移行**: 既存プロジェクトは `state_repo` 設定済みなら host.yaml への転記のみ。ドキュメント不整合(README の探索順・`.agent/` 表記・dashboard README の旧「ワークスペース登録」記述)もこの機で修正する。

---

### S2: dashboard 管理対象の一般化 — 定常業務専用フォルダの宣言(C2)

**現状の問題**
- 一覧の唯一の源が `engine/status.json` の `children[]`(= host.yaml の projects)になった結果、agent-project 管理外のフォルダ(kiro-loop 設定や `.statemachine/` を持つだけのフォルダ)を定常業務画面に出す経路が消えた。表示側は非プロジェクト分岐(`renderer.js:1605-1618`、既定タブ cowork)を既に持っている。

**仕様**
1. host.yaml に **`routines:` リストを追加**する(projects と別リスト。kind フィールドではなくリスト分離とし、必須項目の違いを型で表す)。
   ```yaml
   routines:
     - name: ops-scripts
       root: /home/me/ops-scripts        # kiro-loop.yaml / .statemachine を持つフォルダ
   ```
2. 常駐体は routines エントリを**子プロセス化しない**。`engine/status.json` に `children[].kind: "project" | "routine"` を付けて列挙のみ行う。
3. dashboard は kind=routine のエントリを既存の `isProject=false` 分岐に流す(cowork タブのみ表示)。cowork の走査ルート(`discover.js:270-284`)は routine root を含める。
4. 「プロジェクト宣言は host.yaml が単一ソース、dashboard は映すだけ」という W2-4 の原則は維持する(dashboard 側 roots 設定の復活は行わない)。

**責務の整理**: 「そのフォルダで何が動きうるか」の宣言は実行側(host.yaml)、発見(マーカー走査)と操作は dashboard の cowork、という分担。kiro-loop デーモン自体を常駐体 tick に統合する案は本仕様のスコープ外とする(現行どおり tmux 常駐)。

---

### S3: ノード固有ローカルリポジトリ層(C3・C4)

**現状の問題**
- clone 効率化(共有 bare ミラー + worktree、`local` によるローカル worktree 切り出し)は実装済みだが、`local` の宣言場所が共有 repos.json しかなく、書くとホスト固有絶対パスが全 PC へ push される。
- 板経由の請負では公示 workspace に依頼側の `local` が載らない(正しい)一方、請負側が自ノードの `local` をマージする実装が無い(`agent_flow/board.py:272-277`。bidding 設計 §5.1 に設計意図のみ存在)。
- dashboard の CLI チャット cwd は選択中プロジェクトフォルダ 1 択で、repos.json のリポジトリを選べない。

**仕様**
1. **repos.json から `local` を撤去**する(スキーマ上 deprecated とし、読んだら警告)。共有レジストリは「リモートの同一性と関与範囲」(url/path/base/target/owns/desc)のみを持つ。`dir`(codd-gate 用)も同様に移設する。
2. **`agent-project.host.yaml` の `repos:` をノード固有ローカル宣言の正典にする**(HostConfig.repos は既に存在し、現在は能力宣言への転記のみ)。
   ```yaml
   repos:
     - url: https://git.example.com/app.git
       local: /home/me/mirrors/app      # このノードにあるクローンの絶対パス
   ```
3. **共通リゾルバを agentcore に置く**: URL 正規化一致(既存 `_same_git_remote` / `_same_repo` と同じ吸収規則)で workspace spec に `local` をマージする関数を 1 実装にし、以下の全経路で使う。
   - agent-project → agent-flow の `--workspace` 組み立て(`request.py:492-504`)
   - agent-project の verify 用 clone(`verify.py:140` 付近)
   - agent-flow の provision(`workspace.py:157-158`) — 直接 spec に無くても解決
   - **板の請負側**: `poll_board` が公示 workspace に自ノードの local をマージしてから submit する(欠落の修正)
   - dashboard の検収 diff(S4)と CLI 起動(下記 4)
4. **CLI チャットの cwd 選択**: 起動ボタンに cwd 候補のドロップダウンを追加する。候補 = ①選択中プロジェクトのフォルダ(既定・従来動作) ② repos.json の各リポジトリのうちノード local 解決に成功したパス。local が無いリポジトリは非活性表示とし、パス手入力(その場限り)も許す。ノード local 宣言は dashboard から読むだけ(host.yaml の編集はしない)。
5. 板の `nodes/<node-id>.json` の `repos[].local` は host.yaml から転記する(bidding 設計 §5.1 の実装)。入札可否判定は従来どおり url ベースで行い、local は速度最適化のヒントに留める。

**非目標**: リモートへの fetch 回数削減(鮮度不変条件 INV-1 は維持。`git-worktree-cache-pattern.md` の非目標を踏襲)。

---

### S4: 検収のリモート寄せと決着契約(C5)

**現状の問題**
- 検収 diff はローカルパス前提(`git.js:226-227` で `fs.existsSync(root)` 必須)。worker は `/tmp` の一時 worktree で作業して push 後に消すため、needs 票の `delivery.path` が dashboard のマシンに存在しないと差分が出せない。
- GitLab コメント/ラベルからの承認・却下推定(`flow.js:115-176`)はフロー画面の表示先読み専用で、タスク状態には反映されない。人のレビューコメントを差し戻しに変換するタイミングの契約が無い。

**仕様**
1. **差分表示の 3 段フォールバック**(dashboard):
   1. needs 票の `delivery.path` が存在すればローカル diff(従来)
   2. S3 のノード local 解決で同 URL のクローンが見つかれば、そこへ `fetch` して `base...tip` の三点比較(読み取り git 操作のみ。dashboard は git に書かない原則を維持)
   3. どちらも無ければ MR/PR 画面への誘導を第一動線に昇格(現状のフォールバック文言を正式 UI にする)
2. **リモート決着契約の明文化**: 「人のレビューコメントを拾って差し戻すタイミング」を、曖昧なキーワード推定ではなく**決定的シグナル**で定義する。
   | フォージ側の事象 | agent-project の決着 |
   |---|---|
   | MR/PR がマージされた | approve(done 確定) |
   | MR/PR が未マージでクローズされた | reject |
   | `status:changes-requested` ラベル付与、または Changes Requested レビュー | revise(未解決レビューコメント本文を feedback として注入し ready へ) |
   | 上記以外(コメントのみ等) | 何もしない(人の明示操作を待つ) |
   差し戻しタイミングは「人がラベル/レビュー状態を明示的に付けたとき」と定める。コメント本文のキーワードマッチ(`GITLAB_REJECT_HINTS` 等)は決着には使わず、表示の先読み専用に格下げする。
3. **ポーリングと反映の責務**: フォージ照会と決着の書き込み(revise/approve 契約ファイルの投函)は agent-project の sync 周期(常駐体)が担う。既存の `reconcileRun` IPC は表示専用として残す。dashboard から git・フォージへの書き込みは引き続き行わない。
4. **設定**: プロジェクト設定に `remote_review: off | observe | settle`(既定 observe)を追加。settle で上表の自動決着が有効になる。

**依存**: 2 の revise への feedback 注入は既存の revise 契約(`commands.py`)をそのまま使う。1-ii は S3 に依存。

---

### S5: verify の現実路線化(C12)

**現状の問題**
- 自然文 `accept` からの verify 合成は「1 回の LLM 呼び出し + 静的スクリーニング(sh -n / 恒真式 / Windows シェル / 散文)」のみで、**実際に実行して直すループが無い**(`verify.py:491-521`)。環境差(ツール未導入・ノード差)で大半が失敗し、リトライ(既定 2)を焼いて人へ倒れる。
- 一方 agent-flow の verify ノードは「エージェントが検算する」方式で、これは現実に機能している。要件の種の言う「エージェントによるコマンドの試行錯誤によって成り立っている」現実と一致する。

**仕様** — 「done の根拠は機械検証」という不変条件は維持したまま、検証の作り方と実行可否判定を現実に合わせる。

1. **checker(試行錯誤型検証)を第 4 の verify 系統として正式化する**。解決順: `verify:`(人) → `verify_template` → `reused`(学習済み) → **`checker`** → 人検収。
   - checker = read-only 制約のエージェント run。作業ツリー(verify 用 clone)上で `accept` の充足をコマンドの試行錯誤込みで確認し、`verify=pass|fail` + 根拠 JSON を返す(agent-flow の verify ノードと同じフェイルクローズ正規化を流用)。
   - checker が「再現可能な 1 行コマンド」に到達した場合はそれを学習ライブラリ(`save_validated_verify`)へ保存し、**次回から決定的 verify(`reused`)に昇格**する。「試行錯誤 → 決定化」の一方向パイプラインとして、現状の synth(一発合成)は checker に置き換えて廃止する。
2. **環境ゲート(実行可能性の事前判定)**: verify/checker 実行前に、コマンドが要求するツール群と板の `nodes/<node-id>.json` の能力宣言・`detect_repo_context` の検出結果を突き合わせる。
   - 実行不能と判明したら、リトライを焼かずに (a) 実行可能なノードがあれば板へ委譲、(b) 無ければ理由を明示して人検収へ直行する。環境要因失敗がリトライを消費しない既存の扱い(`mr.py:429-448`)を verify 系にも広げる。
3. **人検収を「失敗」ではなく正規経路として整理する**: needs 票の文言・分類で「verify 不能(環境)」「verify 失敗(内容)」「検収待ち(PASS 済み)」を明確に分ける(既存 `diagnose_verify_failure` の分類に「環境不能」を追加)。S4 のリモート決着が人検収の出口になる。

**非目標**: verify の完全自動化。人の `verify:` 記述が最良である原則は変えない。

---

### S6: 計画の試行錯誤環境(C7・C8・C9)

**現状の問題**
- 重複照合がタイトルの Jaccard 類似(0.5)のみで、言い回し違いを弾けない(`charter.py:756-758`)。
- 削除タスクの墓標が無く、backlog+archive に無いタスクは次の plan で再生成されうる(`charter.py:724-753`)。ユーザーの削除意思が介入できない。
- plan の分解プロンプトはハードコードで差し替え口が無い(`plan.py:101-125`)。内側 flow-planner もスキル名固定・granularity 非伝播。
- 突発タスクの投入口(enqueue/inbox/intake)はあるが、既存 backlog・charter との整合を取る機構が無い。charter タグ無しタスクは `has_consumable` に数えられず再分解の誤発火要因になる(`project.py:480-482`)。
- 「気になる観点を書き溜めて後でバックログ化する」入口が存在しない。

**仕様**

1. **バックログ台帳(ledger)と墓標**: `<root>/backlog-ledger.jsonl` に生成・承認・却下・削除のイベントを追記する。dashboard/CLI からの削除は墓標イベント(正規化タイトル指紋 + 理由)として記録し、plan の既存照合(`_existing_titles`)に墓標を含める。**人が削除したタスクと同種のものは再生成しない**。墓標の解除は replan 時の明示フラグ(`--revive`)のみ。
2. **重複判定の 2 段化**: ① 決定的照合 = 指紋(正規化タイトル + workspace + charter タグ)の一致、② plan プロンプトへの既存タスク一覧注入(タイトル + why の要約を渡し「既存と重複する項目は出力しない」を指示)。投入側の Jaccard 照合は最終防衛線として維持。
3. **計画レビューできる粒度・情報**: plan 出力の `why` / `out_of_scope` を必須化し、欠落タスクは proposed に入れず再生成を要求する(flow-planner の決定的ゲートと同じ方式を外側にも導入)。dashboard の plan-review カードに why/scope/verify の充足度を表示する。
4. **プロンプト/スキルのカスタマイズ口**:
   - 外側: `planner_skill` 設定を追加し、flow-planner と同じスキル解決機構(`_find_skill_script` の検索順)で分解プロンプトを差し替え可能にする。簡易口として `<root>/prompts/plan.md` があれば分解プロンプトに追記注入する。
   - 内側: agent-flow に `planner_skill` 設定キーを追加(名前固定の解消)。agent-project から `--granularity` を agent-flow へ伝播する(現状欠落)。
5. **随時取り込みの整合パス(C8)**: enqueue/inbox/intake で入るタスクは投入前に「整合ステップ」を通す。
   - 既存タスクとの重複照合(上記 2 と同じ) → 重複なら新規作成せず既存タスクへ feedback/refs として追記する案を needs で提示
   - charter バージョンへの帰属推定(LLM) → `- charter:` タグを付与して投入(タグ無しタスクによる再分解誤発火も同時に修正: `has_consumable` はタグ無しタスクを default 扱いで数える)
6. **観点メモ(C9)**: `<root>/notes/` に自由記述の md を書き溜める(dashboard にメモ追加 UI)。plan は notes を**自動では消費しない**。人が「メモを分解」操作(CLI `agent-project distill` / dashboard ボタン)をしたときのみ、notes 群を plan 入力に注入してバックログ候補を生成し(整合パス経由・proposed 投入)、消費済みメモは `notes/archive/` へ移す。

---

### S7: スペック駆動のブラウンフィールド適合(C6)

**現状の問題**
- spec 連携(G1-G5)は実装済みだが、opt-in の `spec_track` + 採点しきい値(`spec_threshold` 既定 3)で「フル spec(spec/design/tasks の 3 点セット)を書くか書かないか」の二択しかない。既存コードベースでは 3 点セットのオーバーヘッドが大きい。

**仕様**
1. **spec を 2 段階にする**: フル spec(現行)に加えて**ライト spec** = `design.md` 相当 1 枚(変更方針・影響範囲・受入条件の差分記述)のみを導入する。
2. **3 段ルーティング**: 既存の assess 採点(c/r/a)で スキップ / ライト / フル を選ぶ。`spec_threshold` を `spec_threshold_light` / `spec_threshold_full` の 2 閾値に拡張(既定: light=2, full=4 相当。既存設定は full に読み替え)。`policy.md` の `spec:` ルールで強制も可能(現行踏襲)。
3. **既存コード文脈の前置**: ライト/フル spec タスクの前に、対象リポジトリの `context/<repo>.md`(repo-map)が無い・古い場合は read-only の調査 run を自動前置して更新する。ブラウンフィールドで spec の質を担保する supply 側の仕組みであり、S6-3(計画のレビュー可能性)にも寄与する。
4. ライト spec の tasks 展開は行わない(元タスクをそのまま実行し、design.md を act の文脈注入に使う)。展開が要る規模ならフル spec に採点で寄る、という整理。

---

### S8: board の観測・操作 UI(C10)

**現状の問題**
- board の正規化ビュー導出(`board-adapter.js`)と IPC(list/post/award/cancel)は実装済みだが renderer 露出ゼロ(bidding 設計 P1 未着手)。手動入札は IPC 自体が無い。`delegation-ui.test.js` が「UI を置かない」を固定している。
- single-resident-controller 設計では「板への push は常駐体のみ」「落札実行はノード直轄ワーカー」に移行予定で、board tick(W1-11)は二重落札リスクを理由に意図的に未実装。

**仕様**
1. **観測 UI(先行)**: orchestration(全体)タブ内に board セクションを追加し、`delegation:list` のビュー(phase / bids / status / result)を表示する。委譲の独立タブは設けない(既存方針維持)。`delegation-ui.test.js` は「独立タブを置かない」の検証に緩める。
2. **キャンセル**: 既存 `delegation:cancel` をカードの操作に接続する。ただし書き込み経路は single-resident 設計に合わせ、**dashboard は常駐体への指示ファイル投函に変更**し、板への `cancelled.json` 書き込みと push は常駐体が行う(board-adapter の直接書き込みは移行期のみ)。
3. **手動入札**: 「このノードで請け負う」操作を追加する。実装は bid ファイルの直接書き込みではなく、**常駐体の board tick への指示投函**(`commands/` 契約)とし、bid の名義・lease 管理は常駐体に一元化する。二重落札防止の判断(claim 規則)を UI 側に複製しない。
4. **前提**: 本仕様の 2・3 は常駐体の board tick(W1-11)実装後に着手する。観測 UI(1)は現行の board-adapter 読み取りだけで先行実装できる。

---

### S9: 診断の対話化(C11)

**現状の問題**
- doctor は読み取り専用フラグ付きのヘッドレス 1 発実行(`agent.js:277-330`, `:409`)で、深掘りの追加質問ができない。tmux セッション起動基盤(`runChatWindow` / `chatSessionName` / `chatWindowScript`)は CLI チャットと cowork で稼働済み。

**仕様**
1. **診断の 2 モード化**:
   - **対話診断(新設・既定)**: doctor のコンテキスト(`buildDoctorContext` + spill ファイル)を初回プロンプトとして `runChatWindow` で tmux セッションを開く。セッション名は `agent-doctor-<digest>` とし、同一 need の再診断は既存セッションへ attach する。CLI には読み取り専用の対話フラグ(kiro: `--trust-tools=fs_read` 等、ヘッドレス版と同等の制約の対話版)を渡す。
   - **文面生成(現行維持)**: 「差し戻し文面案」など構造化出力の抽出が要る用途はヘッドレス 1 発実行を残す(`## 差し戻し文面案` 抽出フローは対話化できないため)。
2. 失敗診断ボタン(`needs.js:1273`)は対話診断を開き、文面案が欲しい場合の「文面を生成」ボタンを併設する。
3. 状況の可視化: 開いた診断セッションは kiro-loop feature の tmux 視聴(capture-pane)で dashboard 内からも覗けるようにする(既存 IPC `kiroLoop:capture` の流用)。

---

## 4. 段階導入計画

| フェーズ | 仕様 | 主な変更先 | 備考 |
|---|---|---|---|
| 1 | S1 + S3 | agent-project(configfile/state/host)、agentcore(リゾルバ)、agent-flow(board マージ)、schemas | host.yaml 拡張(projects 必須化・repos・routines)を 1 回で行う |
| 1' | S2 | agent-project(resident/status)、agent-dashboard(cowork) | S1 の host.yaml 拡張に相乗り |
| 2 | S4 → S5 | agent-project(mr/verify/needs)、agent-dashboard(needs/git) | S4-1-ii は S3 に依存 |
| 3 | S6 → S7 | agent-project(plan/charter/prioritize)、agent-flow(planner_skill)、agent-dashboard(plan-review/notes UI) | S9 と並行可 |
| 4 | S8、S9 | agent-dashboard、agent-project(常駐体) | S8-2/3 は W1-11(board tick)後 |

## 5. 未決事項

1. **S1**: 単発利用(host.yaml 無しで cwd 直叩き)をどこまで残すか。ワーカーノード(lite)の `worker init` との整合。
2. **S3**: `local` の鮮度責務(worker が毎回 `fetch` する現行方式を維持するか、ノード側で定期 fetch するか)。
3. **S4**: GitLab 以外のフォージ(GitHub/Gitea)への決着契約の展開順序。現行 GitLab クライアントは読み取り専用のため、ラベル操作を「人の操作」に限定する本仕様なら書き込み API 追加は不要のはず — 要確認。
4. **S5**: checker の実行コスト(LLM 呼び出し)と `verify_confirm`(flake 判定の複数回実行)の両立。checker 結果の flake 扱い。
5. **S6**: 墓標の適用範囲(タイトル指紋の衝突で「作りたい新タスク」まで抑止しないか)。`--revive` の UI 動線。
6. **S8**: 手動入札の「ノード直轄ワーカーで実行」への接続(落札後の実行系が W1-11 に含まれるため、単独では操作だけ増えて実行できない状態になり得る)。
