# agent シリーズ改良 仕様案

ステータス: ドラフト(第 2 版・レビュー反映済み)
入力: `docs/plans/2026-07-25-imrovement-agent.md`(要件の種)
参照した既存設計: `2026-07-24-single-resident-controller-design.md` / `2026-07-23-delegation-board-distributed-bidding-design.md` / `2026-07-12-agent-spec-flow-integration.md` / `2026-07-25-flow-planner-granularity-design.md`

改訂履歴:
- 第 4 版: Phase 1'(S9-1〜3)と Phase 2(S4 → S5)の詳細設計を追加。§4 の表に詳細設計へのリンクを足し、§5 の未決 3・4・7 を決着済みにした
- 第 3 版: Phase 1(S1 + S3 + S2)の実装完了を反映。§4 に状態列・詳細設計へのリンク・積み残し表を追加、§5 の未決 1・2 を決着済みに、§2 の記述に「Phase 1 で解消」の印を付けた
- 第 2 版: レビュー反映。S1 = 設定 2 層の専有項目を明確化 / S2 = dashboard 管理へ変更 / S4 = MR/PR 一本化 / S5 = 証跡ベース検証へコンセプト変更 / S6 = 「人は charter・メモ、エージェントがバックログ記述」のフローと作業概要・スキル化 / S9 = エージェント CLI 差分吸収レイヤの新設

---

## 1. 要件の種の整理

要件の種 12 件(C1〜C12)を現実装と突き合わせ、重複を畳んで 9 件の仕様(S1〜S9)に再構成した。

| # | 要件の種(原文の行) | 真の要求 | 仕様 |
|---|---|---|---|
| C1 | 管理対象フォルダの整理・state repo 設定の簡素化 (:8) | 状態専用リポジトリを唯一の方式にし、設定の層と専有項目を明確にする | S1 |
| C2 | dashboard で管理外フォルダ(kiro-loop/ステートマシン)も扱いたい (:9) | 定常業務フォルダを dashboard 自身が登録・管理する口 | S2 |
| C3 | flow/amigos の git clone のリモート負荷 (:10) | ノード固有のローカルクローンを全経路で使う | S3 |
| C4 | CLI 起動 cwd を repos.json から選択・ローカルパスを push できない (:11) | 同上(共有レジストリとノード固有パスの分離) | S3 |
| C5 | 検収がローカル前提・リモートレビューの差し戻しタイミング (:12) | 検収を MR/PR に一本化し、決着契約を明文化する | S4 |
| C12 | verify が機能していない (:19) | 検証コンセプトの置き換え(証跡ベースのエージェント検証) | S5 |
| C7 | 計画の試行錯誤環境が無い(抽象的・重複・削除・プロンプト固定) (:14) | 「エージェントが書き、人が直す」バックログと計画のスキル化 | S6 |
| C8 | 突発バックログの整合的な取り込み (:15) | 随時入力の「整合パス」 | S6 |
| C9 | 気になる観点の書き溜め→バックログ化 (:16) | 同上(蓄積→人の合図で分解、という変種) | S6 |
| C6 | スペック駆動×ブラウンフィールドのギャップ (:13) | spec の軽量段階の導入 | S7 |
| C10 | board の UI(状況確認・手動入札・キャンセル) (:17) | board 観測/操作の UI と入札経路 | S8 |
| C11 | 診断を tmux/CLI チャット呼び出しに (:18) | 診断の対話化 + エージェント CLI 差分吸収レイヤ | S9 |

### 重複・依存の分析

- **C3 と C4 は同じ問題の両面**。clone 効率化の機構(URL 単位共有 bare ミラー + detached worktree、`agent_flow/gitcache.py`)は実装済みで、`repos.json` の `local` キーも agent-flow まで伝搬済み(`request.py:492-504` → `workspace.py:157-158` → `gitcache.py:172-212`)。未解決なのは「`local` はホスト固有の絶対パスなのに、repos.json は state repo 経由で全 PC に push される」という宣言場所の矛盾のみ。よって S3 は「ノード固有宣言層の新設」1 本に畳む。
- **C8 と C9 は「随時入力→既存計画と整合を取ってバックログ化」の変種**(即時か蓄積かの違い)。整合を取る機構(重複照合・charter 帰属)は共通なので S6 に統合。
- **C5 と C12 は連続した問題**。verify(機械検証)が環境差で失敗して人検収に倒れる → その人検収がローカル前提で見られない、という因果。S4(検収)が先、S5(検証)がそれを人レビューの出口として使う。
- **C1 は single-resident-controller 設計の続き**。「状態ルートは常に git リポジトリ」(同設計 §4.1)が既に方針化されており、S1 はその完遂 + 設定の 2 層化と専有項目の明確化。
- **C10 は bidding 設計 P1(未着手)そのもの** + 手動入札(IPC 自体が無い)の追加。常駐体の board tick(実装計画 W1-11、未実装)に依存する。
- **C2 は W2-4(一覧の単一ソース化)の副作用**。ただし定常業務のエンジン(kiro-loop / statemachine-use スキル)は agent-project と独立に動作し、起動も dashboard(cowork)が担うため、宣言の置き場は host.yaml ではなく dashboard 側が適切(S2 で詳述)。
- **C11 の背後にある共通課題**として、tmux 経由のエージェント CLI 起動が CLI ごとのハードコード分岐(対話コマンド・読み取り専用フラグ・入力受付検出)に散っている。S9 はこれを差分吸収レイヤとして切り出す。

### 依存グラフと導入順序

```
Phase 1(基盤・宣言層):  S1      S3          S2(独立・dashboard のみ)
Phase 2(検収と検証):    S4 → S5             S9(独立・S5 の checker とは無関係)
Phase 3(計画):          S6 → S7   (S6 の受入基準チェックリストは S5 と接続)
Phase 4(UI):            S8(常駐体 board tick に依存)
```

S1/S3 が実行系の足場。S2・S9 は独立に着手できる。S5 と S6 は「受入基準チェックリスト」を共有する(S6 が生成・人が修正、S5 が検証に使う)。

---

## 2. 現実装の要点(調査結果サマリ)

仕様の前提となる事実のみ列挙する(詳細な行番号は各仕様の節に記載)。

> **この節は仕様策定時点(2026-07-25)のスナップショット**。Phase 1(S1・S3・S2)で解消したものには
> 「→ Phase 1 で解消」と印を付けた。**現在の実装を知りたい場合は §4 の詳細設計を見ること**
> ——ここを書き換えると「なぜこの仕様を書いたか」の記録が消えるので、印だけを足してある。

- **agent-project**: 状態専用リポジトリ(`state_repo`)は opt-in。未設定なら旧 worktree 方式にフォールバック(`configfile.py:340-344`)。設定時は `state_backup_branch` 無効化・誤ディレクトリ拒否・専用 clone へのリダイレクトが強制される。プロジェクト宣言の単一ソースは `~/.agents/agent-project.host.yaml`。設定キーは約 110 個が単一ファイルに平置きされている(`CONFIG_DEFAULTS`)。**→ Phase 1 で解消**(S1: 状態専用リポジトリの唯一化・設定 2 層・profile 廃止)。
- **repos.json**: charter から自動生成され(`charter.py:370-397`)、状態同期の対象(`state.py:203-207`、リモート優先ファイル)。スキーマにはホスト固有の `local`/`dir` キーが存在するが、自動生成では書き出されず、書けば全 PC に伝播する。**→ Phase 1 で解消**(S3: 宣言は host.yaml `repos[]` へ。repos.json の `local` は警告して無視)。
- **agent-flow**: 1 run = 1 ワークスペース。作業ツリーは `/tmp` の mkdtemp 配下で、終了時に必ず消える(`workspace.py:145`, `:203-213`)。共有 bare ミラーから detached worktree を切る 3 段フォールバックが実装済み。板(agent-board)経由の請負では公示の workspace をそのまま使い、自ノードの `local` をマージしない(`agent_flow/board.py:272-277`)。**→ Phase 1 で解消**(S3: `poll_board` が submit 前に自ノードの `local` を載せる)。
- **verify(agent-project)**: 人の `verify:` / 決定的 `verify_template` / 自然文 `accept` からのエージェント合成の 3 系統。合成は 1 回の LLM 呼び出し + 静的スクリーニングのみで、実行して直すループを持たない(`verify.py:491-521`)。失敗はリトライ消費 → `_escalate` で人へ。agent-flow 側の `verify` ノードは別物で、「エージェントが依存成果を独立に検算し `verify=pass|fail` + JSON を返す」フェイルクローズ方式(`agent.py:910-915`, `waits.py:271-285`)であり、こちらは機能している。
- **計画パイプライン**: charter → backlog の分解プロンプトはハードコード(`plan.py:101-125`)。重複照合はタイトルの Jaccard 類似(閾値 0.5)のみ。削除タスクの墓標は無く、drained/charter 変更/replan で同種タスクが再生成されうる。内側 flow-planner はスキルとして分離済み(`.github/skills/flow-planner/`)だが名前固定・`--granularity` 非伝播。
- **agent-dashboard**: Electron。プロジェクト一覧は常駐体が書く `engine/status.json` の `children[]` のみ(登録 UI は廃止済み)。**→ Phase 1 で一部解消**(S2: 定常業務専用フォルダだけ `cowork.roots` で登録可。agent-project プロジェクト一覧は engine/status.json のまま)。表示側は非プロジェクトフォルダの分岐(`renderer.js:1605-1618`)を既に持つ。CLI チャットの cwd は選択中プロジェクトのフォルダ 1 択。**→ Phase 1 で解消**(S3-4: 起動先ドロップダウン)。検収 diff はローカルパス前提(`git.js:226-227`)で、MR は外部ブラウザに開くだけ。GitLab コメント/ラベルからの決着推定はフロー画面の表示先読み専用。board の IPC(list/post/award/cancel)はあるが UI ゼロ、bid の IPC は無い。診断(doctor)はヘッドレス 1 発実行で、対話コマンド・読み取り専用フラグは CLI ごとにハードコード(`agent.js:218-247`, `:277-330`)。
- **エージェント CLI プラグイン**: `schemas/agent-cli.schema.json` + `agents/<name>.json` という宣言的差し込み口が既にあるが、**ヘッドレス片道実行(prompt 渡し・出力取り出し・エラー分類)のみ**を契約化しており、対話モードの情報(対話 argv・読み取り専用フラグ・入力受付検出)を持たない。
- **agent-board**: 実行プロセスを持たない git リポジトリ + ファイル契約。常駐一本化設計で「板が必須・PC 内 1 クローン・push は常駐体のみ・落札はノード直轄ワーカーで実行」に移行予定。board tick(W1-11)は二重落札リスクを理由に意図的に未実装。
- **kiro-loop / ステートマシン**: dashboard の cowork(定常業務)機能が `.kiro/kiro-loop.yaml` と `.statemachine/*/workflow.yaml` を自動発見して tmux で実行する仕組みは実装済み。走査対象が engine/status.json の children に限られることだけが制約。これらのエンジンは agent-project の常駐体・状態管理と無関係に動く。

---

## 3. 仕様

### S1: 設定の簡素化 — 状態専用リポジトリの唯一化と設定 2 層の責務分離(C1)

**現状の問題**
- `state_repo` が opt-in のため、worktree 方式(`state_worktree_dir` / `state_branch` / `state_commit` / `state_push` / `state_backup_branch` の 5 キー)と 2 系統が併存し、フォールバック分岐(`configfile.py:313-344`)・テスト・移行手順書が複雑化している。
- `state_repo:` は「状態 clone を作る前に読める場所」に必要なため、成果物リポジトリ側の `agent-project.yaml` がブートストラップを兼ねる(`docs/guides/state-repo-migration.md:115-168`)。設定ファイルが「成果物側 yaml・状態側 yaml・profile・host.yaml」の 4 か所に散り、どこに何を書くべきかが不明瞭。

**仕様**

1. **状態ルートは常に状態専用リポジトリとする**(single-resident-controller 設計 §4.1 の完遂)。worktree 方式と関連 5 キーを廃止。旧キーを検出したら fail-fast でエラーにし、`migrate-state-repo.sh` への誘導メッセージを出す。成果物リポジトリ側 `agent-project.yaml` のブートストラップ役も廃止する(置いてあっても無視、警告のみ)。profile(`PROFILE_LOCAL_KEYS`)は host.yaml に吸収して廃止する。

2. **設定は 2 ファイルに集約し、責務を「ノード固有 vs プロジェクト共有」で分ける。**

   | ファイル | 置き場所 | 責務 | 共有範囲 |
   |---|---|---|---|
   | `agent-project.host.yaml` | `~/.agents/`(各 PC) | **このノードの宣言**: 何を動かすか・ノードの資源・ローカル環境 | 共有しない(PC 固有) |
   | `agent-project.yaml` | 状態専用リポジトリ直下 | **プロジェクトの合意事項**: どう動かすか(全ノードで同一であるべき動作) | state repo で全 PC 共有 |

3. **専有項目(そこにしか書けないキー)を契約として固定する。**

   host.yaml **専有**(プロジェクト yaml に書いたらエラー):
   - `node_id`
   - `projects[]`(`name` / `state_repo` / `root`)— どの状態リポジトリをこのノードで駆動するか。`state_repo` は clone 前に必要なブートストラップ情報なのでここが唯一の置き場
   - `repos[]`(S3 のノード固有ローカルクローン宣言)
   - `availability`(在席・稼働時間帯)
   - ノード資源の上限: ワーカー同時実行数(`max_concurrent`)、ノード予算参照
   - 板への参加宣言(参加する board、`board_tags`)

   プロジェクト yaml **専有**(host.yaml に書いたらエラー):
   - 計画・ゲート系: `planner` / `flow_planner` / `granularity` / `plan_review` / `delivery_review` / `spec_track` / `remote_review`(S4) / 検証設定(S5)
   - 予算・収束系: `max_cycles` / `max_retries` / `max_iterations` / `level` 系
   - タスク運用系: `task_branch` / `intake` / `followup` 系
   - 理由: これらは「プロジェクトとしてどう進めるか」の合意であり、ノードごとに食い違うと動作が非決定になる

   **重複を許すキー**(両方に書ける。優先順位: CLI > host.yaml > プロジェクト yaml > 組み込み既定):
   - `agent_cli` / `model` / `act_timeout` / `verify_timeout` / `location` / `concurrency`
   - 理由: ノードごとに導入済み CLI・マシン性能が異なるため、共有既定をプロジェクト yaml に置き、ノード事情による上書きを host.yaml に書く。「敢えて重複して上書きする」のはこの群だけであり、上書きは host.yaml の `projects[].overrides:`(プロジェクト単位)または `defaults:`(ノード全体)に書く
   ```yaml
   # agent-project.host.yaml
   node_id: pc-a
   defaults:
     agent_cli: codex          # このノードの既定 CLI(全プロジェクト)
   projects:
     - name: example
       state_repo: https://git.example.com/example-state.git
       overrides:
         model: gpt-5.6-sol    # このノード×このプロジェクトだけの上書き
   ```

4. **単発実行**: `agent-project run` を cwd 直叩きする場合は cwd が状態リポジトリの clone であることを要求する(`--state-repo <url>` で新規 clone も可)。成果物リポジトリ cwd からの暗黙リダイレクトは廃止。

**移行**: 既存プロジェクトは `state_repo` 設定済みなら host.yaml への転記のみ。ドキュメント不整合(README の探索順・`.agent/` 表記・dashboard README の旧「ワークスペース登録」記述)もこの機で修正する。

---

### S2: 定常業務フォルダの dashboard 管理(C2)

**現状の問題**
- 一覧の唯一の源が `engine/status.json` の `children[]`(= host.yaml の projects)になった結果、agent-project 管理外のフォルダ(kiro-loop 設定や `.statemachine/` を持つだけのフォルダ)を定常業務画面に出す経路が消えた。表示側は非プロジェクト分岐(`renderer.js:1605-1618`、既定タブ cowork)を既に持っている。

**方針の整理 — なぜ host.yaml ではなく dashboard か**
- 定常業務のエンジン(kiro-loop / statemachine-use スキル)は agent-project の常駐体・状態リポジトリ・バックログと**無関係に動作する**。起動・tmux セッション管理・履歴記録もすべて dashboard の cowork feature が担っている(`cowork.js:483-549`)。
- W2-4 で廃止したのは「**agent-project プロジェクト一覧**の二重管理」であり、その原則は「宣言は実行側が持つ」。定常業務の実行側は dashboard(cowork)自身なので、**宣言も dashboard 設定に置くのが原則に合致する**。host.yaml に載せると、常駐体が管理しないものを常駐体の宣言ファイルに書くねじれが生じる。

**仕様**
1. dashboard 設定に **`cowork.roots[]`**(定常業務ワークスペースのフォルダパス一覧)を追加する。
2. **登録 UI を定常業務タブに設ける**: フォルダ選択 → マーカー検出(`.kiro/kiro-loop.{yaml,yml,json}` / `.statemachine/*/workflow.yaml`、既存 `detectMarkers` を流用) → 検出結果のプレビュー → 登録。マーカーが無いフォルダは「ステートマシン新規作成」動線(既存 `stateMachineCreationPrompt`)へ誘導する。登録解除も同 UI で行う。
3. プロジェクトセレクタには `cowork.roots` のエントリを **kind=routine** として合流させ、既存の `isProject=false` 分岐(cowork タブのみ表示)に流す。cowork の走査ルート(`discover.js:270-284`)は engine children + `cowork.roots` の和集合にする。
4. **agent-project プロジェクトの一覧は従来どおり engine/status.json のみ**を源とする(W2-4 維持)。`cowork.roots` に project root と同じパスが登録された場合は project 側を正として重複を畳む。
5. host.yaml は変更しない(S1 から routines 案を撤回)。

---

### S3: ノード固有ローカルリポジトリ層(C3・C4)

**現状の問題**
- clone 効率化(共有 bare ミラー + worktree、`local` によるローカル worktree 切り出し)は実装済みだが、`local` の宣言場所が共有 repos.json しかなく、書くとホスト固有絶対パスが全 PC へ push される。
- 板経由の請負では公示 workspace に依頼側の `local` が載らない(正しい)一方、請負側が自ノードの `local` をマージする実装が無い(`agent_flow/board.py:272-277`。bidding 設計 §5.1 に設計意図のみ存在)。
- dashboard の CLI チャット cwd は選択中プロジェクトフォルダ 1 択で、repos.json のリポジトリを選べない。

**仕様**
1. **repos.json から `local` を撤去**する(スキーマ上 deprecated とし、読んだら警告)。共有レジストリは「リモートの同一性と関与範囲」(url/path/base/target/owns/desc)のみを持つ。`dir`(codd-gate 用)も同様に移設する。
2. **`agent-project.host.yaml` の `repos:` をノード固有ローカル宣言の正典にする**(S1 の host.yaml 専有項目。HostConfig.repos は既に存在し、現在は能力宣言への転記のみ)。
   ```yaml
   repos:
     - url: https://git.example.com/app.git
       local: /home/me/mirrors/app      # このノードにあるクローンの絶対パス
   ```
3. **共通リゾルバを agentcore に置く**: URL 正規化一致(既存 `_same_git_remote` / `_same_repo` と同じ吸収規則)で workspace spec に `local` をマージする関数を 1 実装にし、以下の全経路で使う。
   - agent-project → agent-flow の `--workspace` 組み立て(`request.py:492-504`)
   - agent-project の検証用 clone(S5 の checker ワークスペース)
   - agent-flow の provision(`workspace.py:157-158`) — 直接 spec に無くても解決
   - **板の請負側**: `poll_board` が公示 workspace に自ノードの local をマージしてから submit する(欠落の修正)
   - dashboard の CLI 起動(下記 4)
4. **CLI チャットの cwd 選択**: 起動ボタンに cwd 候補のドロップダウンを追加する。候補 = ①選択中プロジェクトのフォルダ(既定・従来動作) ② repos.json の各リポジトリのうちノード local 解決に成功したパス。local が無いリポジトリは非活性表示とし、パス手入力(その場限り)も許す。ノード local 宣言は dashboard から読むだけ(host.yaml の編集はしない)。
5. 板の `nodes/<node-id>.json` の `repos[].local` は host.yaml から転記する(bidding 設計 §5.1 の実装)。入札可否判定は従来どおり url ベースで行い、local は速度最適化のヒントに留める。

**非目標**: リモートへの fetch 回数削減(鮮度不変条件 INV-1 は維持。`git-worktree-cache-pattern.md` の非目標を踏襲)。

---

### S4: 検収の MR/PR 一本化と決着契約(C5)

**現状の問題**
- 検収 diff はローカルパス前提(`git.js:226-227` で `fs.existsSync(root)` 必須)。worker は `/tmp` の一時 worktree で作業して push 後に消すため、needs 票の `delivery.path` が dashboard のマシンに存在しないと差分が出せない。
- GitLab コメント/ラベルからの承認・却下推定(`flow.js:115-176`)はフロー画面の表示先読み専用で、タスク状態には反映されない。人のレビューコメントを差し戻しに変換するタイミングの契約が無い。

**方針 — MR/PR への一本化**
ローカル diff とリモートの併存(フォールバック多段)は複雑さの源になるため、**成果物レビューの正はフォージ(MR/PR)一本**とする。根拠:
- 成果物は必ずリモートへ push される(変更ゼロなら push しない = レビュー対象も無い)。ローカルパスは一時 worktree で常に消えるため、リモートだけが常に存在する唯一のビューである。
- レビューコメント・承認状態・行コメントなどレビューに必要な道具はフォージ側が揃えており、dashboard 内 diff 表示(diff2html)を保守する理由が薄い。

**仕様**
1. **MR/PR の存在を納品の一部にする**: 書込先があるタスクは、settle 時に MR/PR が未作成なら agent-project が作成する(GitLab は既存 mr.py の延長。作成に必要な書き込み API を追加する)。needs 票(検収カード)には MR URL を必須項目として載せる。
2. **dashboard のローカル diff 表示(`#dlg-delivery-review` の diff2html 経路・`git.js diffRange` の検収用途)を廃止する**。検収カードは「受入基準チェックリスト(S5/S6) + 検証レポート要約 + MR リンク」の構成にし、差分レビューは MR 画面(または gitlab-review-viewer 連携、既存)で行う。
3. **決着契約の明文化**: 「人のレビューコメントを拾って差し戻すタイミング」を、曖昧なキーワード推定ではなく**決定的シグナル**で定義する。
   | フォージ側の事象 | agent-project の決着 |
   |---|---|
   | MR/PR がマージされた | approve(done 確定) |
   | MR/PR が未マージでクローズされた | reject |
   | `status:changes-requested` ラベル付与、または Changes Requested レビュー | revise(未解決レビューコメント本文を feedback として注入し ready へ) |
   | 上記以外(コメントのみ等) | 何もしない(人の明示操作を待つ) |
   差し戻しタイミングは「人がラベル/レビュー状態を明示的に付けたとき」と定める。コメント本文のキーワードマッチ(`GITLAB_REJECT_HINTS` 等)は決着には使わず廃止する。
4. **ポーリングと反映の責務**: フォージ照会と決着の書き込み(revise/approve 契約ファイルの投函)は agent-project の sync 周期(常駐体)が担う。dashboard は決着状態の表示に徹し、git・フォージへの書き込みは行わない。dashboard 上の承認/差し戻しボタン(既存 revise 契約)は「フォージを使わない判断」の口として残す — つまり決着の口は「フォージのシグナル」か「dashboard のボタン」の 2 つで、どちらも同じ revise/approve 契約に合流する。
5. **設定**: プロジェクト yaml に `remote_review: observe | settle`(既定 settle)を追加。observe はフォージ決着を表示のみに留める(移行用)。
6. **フォージ無し運用**(リモートに GitLab/GitHub/Gitea いずれも無い場合)は本仕様のスコープ外とし、従来どおり dashboard のボタン決着のみで運用する(diff はローカル git を人が直接見る)。

**S3 への依存は無くなった**(ローカル diff 復元パスの撤回による)。

---

### S5: 検証コンセプトの置き換え — 証跡ベースのエージェント検証(C12)

**現状の問題と、前版(checker 案)の再考**
- 現行の「自然文 accept → LLM 一発合成 → 静的スクリーニング → 決定的シェルコマンドを done の唯一の根拠にする」は、環境差で大半が失敗し人へ倒れる。
- 前版で提案した「試行錯誤で見つけたコマンドを決定的 verify に昇格する」案は、**人間がそのコマンドの良し悪しを判断できない**という根本問題を解決しない。昇格したコマンドが「たまたま通る劣化した検証」でも、人にはそれを見抜く材料が無い。また設定ファイルで環境を縛る(能力宣言による事前ゲート)アプローチも、実行時に試行して探し当てる作業が現実には必要である以上、根本策にならない。
- 一方、agent-flow の verify ノード(エージェントが検算し `verify=pass|fail` + JSON を返す、フェイルクローズ)は現実に機能している。

**コンセプトの変更**
「**1 行のシェルコマンドの exit 0**」を done の根拠にする設計をやめ、「**受入基準チェックリストに対する、検証エージェントの証跡付き判定**」を done の根拠にする。人間がレビューする対象を「コマンド(良し悪しを判断できない)」から「**基準と証跡(判断できる)**」に変える。

**仕様**

1. **受入基準チェックリスト(acceptance criteria)を検証の一次表現にする**。
   - バックログ生成時(S6)にタスクごとの `acceptance:` チェックリスト(自然文の基準 3〜7 項目。「〜が動作する」「〜のテストが通る」「〜を壊していない」)をエージェントが書き、計画レビューで人が修正する。
   - 人の `verify:`(決定的コマンド)・`verify_template` は「基準 1 項目の決定的な実装」として引き続き書ける(最速・最優先)。自然文 `accept` 1 行は 1 項目のチェックリストとして扱う(後方互換)。

2. **verifier run(検証エージェント)が判定する**。
   - settle 時、verifier をタスクの成果ブランチのワークスペース(S3 のリゾルバで確保)上で起動する。verifier は基準ごとに、**実行時にコマンドを試行錯誤して**(ビルド・テスト・grep・起動確認など。作業ツリー内に副作用を限定)充足を確認し、次を構造化した**検証レポート**を書く:
     - 基準ごとの判定(pass / fail / 検証不能)とその**証跡**(実行したコマンド、出力の要約、参照したファイル)
     - フェイルクローズ正規化(agent-flow の `_normalize_verify` と同じ規則: 明示の pass 表明が無ければ fail)
   - 全基準 pass のみ機械 done 候補。fail は失敗基準と証跡を feedback にして積み直し。「検証不能」(環境にツールが無い等)は**リトライを焼かずに**、理由付きで (a) 板で他ノードへ検証委譲、または (b) 人検収へ直行する(環境要因失敗の既存の扱い `mr.py:429-448` を検証に拡張)。
   - 決定的 `verify:` がある基準は verifier を介さず従来どおり直接実行する(コスト最小の fast path)。red-green(変更を弁別しない検証の検出)は「差分が基準の対象範囲に存在すること」を必須基準としてチェックリストに常設することで代替する。
   - 検証レポートは状態リポジトリ(`verifications/<task-id>/<rev>.md`)に保存し、needs 票・検収カード(S4)に要約を載せる。**人検収では人はレポート(基準×証跡)を読む** — これが「人がコマンドの良し悪しを判断できない」問題への答えであり、検収の材料が「差分 + 基準 + 証跡」に揃う。
   - verifier が見つけた有効なコマンド列は「**検証レシピ**」として保存する(`find_learned_verify` の置き換え)。レシピは次回 verifier への**参考情報**(まずこれを試せ)であり、独立した決定的ゲートには昇格させない。環境が変われば壊れるものを盲信しない。

3. **廃止するもの**: `synth_verify`(一発合成)とその静的スクリーニング群、`verify_validate`(red-green の別実行)、能力宣言による事前実行可否ゲート(前版 S5-2。事前に縛るのではなく、verifier の「検証不能」判定として実行時に扱う)。

**コスト**: verifier は 1 settle あたり LLM run 1 回。現行の「合成 → 失敗 → リトライ×2 → 診断 → 人」の連鎖より総コストは下がる見込み。`verify_confirm`(flake 判定)は決定的 fast path のみに適用し、verifier 判定の flake は同一レポート内の再試行として verifier 自身に扱わせる。

**不変条件の維持**: 「done は機械検証の PASS のみが根拠」「必ず有限回で止まる」(verifier は 1 run・時間/トークン予算内)は変えない。変わるのは検証の表現(コマンド 1 行 → 基準リスト)と実行者(シェル → エージェント)である。

---

### S6: 「エージェントが書き、人が直す」バックログ(C7・C8・C9)

**現状の問題**
- バックログの中身が抽象的で、タスクグラフ作成前に人が計画レビューする材料が無い。
- 重複照合がタイトルの Jaccard 類似(0.5)のみで、言い回し違いを弾けない(`charter.py:756-758`)。削除タスクの墓標が無く再生成されうる。分解プロンプトはハードコードで差し替え口が無い(`plan.py:101-125`)。
- 突発タスクの投入口はあるが既存計画との整合機構が無く、「気になる観点の書き溜め」の入口も無い。

**目指すフロー**(人の入力は charter とメモ書き程度に留める):

```
人: charter を書く / notes にメモを書き溜める
  ↓
エージェント(backlog-planner スキル): バックログ md を全文記述(作業概要込み)
  ↓ proposed(計画レビュー)
人: 生成されたバックログを修正・加筆して承認(または却下・削除=墓標)
  ↓
実行(タスクグラフ構築へ) / 人の編集は以後の replan から保護される
```

**仕様**

1. **バックログ作成のスキル化 — `backlog-planner`**。
   - flow-planner と同型のスキル(`.github/skills/backlog-planner/` に SKILL.md + scripts)として分解ロジックを agent-project 本体から分離する。解決順は flow-planner と同じ(`_find_skill_script` の検索順: プロジェクト → git root → `~/.agents/skills` → skill-registry)。**上位にプロジェクト独自の backlog-planner を置けば全面カスタマイズできる**。
   - スキルへの入力契約: charter(マージ済み)、rules.md、context/*.md(repo-map)、既存タスク一覧(タイトル + 概要)、墓標一覧、granularity、notes(distill 時のみ)。出力契約: 下記 2 の必須セクションを満たすタスク spec の JSON 配列(`schemas/task.schema.json` 拡張)。
   - agent-project の `plan_via_agent` はスキル呼び出しに置き換え、ハードコードプロンプトは既定スキルの中身として移す。内側にも対称に `planner_skill` 設定キーを追加し(flow-planner の名前固定を解消)、`--granularity` を agent-flow へ伝播する(現状欠落)。

2. **バックログ md に「作業概要」を必須化し、計画レビューの材料にする**。タスクグラフ作成前でも読める粒度で、生成時に以下のセクションを必ず埋める:
   - `why:` このタスクが charter のどの目標に効くか(1〜2 文)
   - `作業概要:` 変更対象(リポジトリと主要ファイル/モジュールの見込み)、作業ステップの概略(3〜7 行の箇条書き)、影響範囲
   - `acceptance:` 受入基準チェックリスト(S5 の一次表現。3〜7 項目)
   - `out_of_scope:` やらないこと
   - `規模感:` S/M/L(分解の妥当性判断用)
   - 欠落セクションのあるタスクは proposed に入れず再生成を要求する(flow-planner の決定的ゲートと同じ方式)。変更対象の見込みを書くには repo 文脈が要るため、context/*.md が無い・古いリポジトリには調査 run を自動前置する(S7 と共通機構)。

3. **計画レビュー = 人がバックログ md を直接修正・加筆する**。
   - dashboard の plan-review カードにバックログ md のインライン編集(作業概要・acceptance の項目単位の編集含む)を追加する。エディタで md を直接編集してもよい(ファイルが正)。
   - 人が編集したタスクは台帳(下記 4)に `edited: human` を記録し、**以後の replan で上書き・再生成の対象外**とする(人の記述 > エージェント提案、という既存原則の計画への適用)。

4. **バックログ台帳(ledger)と墓標**: `<root>/backlog-ledger.jsonl` に生成・人編集・承認・却下・削除のイベントを追記する。削除は墓標イベント(正規化タイトル指紋 + 理由)として記録し、既存照合(`_existing_titles`)に墓標を含める。**人が削除したタスクと同種のものは再生成しない**。解除は replan 時の明示フラグ(`--revive`)のみ。
5. **重複判定の 2 段化**: ① 決定的照合 = 指紋(正規化タイトル + workspace + charter タグ)の一致、② backlog-planner への既存タスク一覧・墓標一覧の注入(「既存と重複する項目は出力しない」をスキル入力契約に含める)。投入側の Jaccard 照合は最終防衛線として維持。
6. **随時取り込みの整合パス(C8)**: enqueue/inbox/intake で入るタスクは投入前に「整合ステップ」を通す。既存タスクとの重複照合(上記 5) → 重複なら新規作成せず既存タスクへ feedback/refs として追記する案を needs で提示。charter バージョンへの帰属推定 → `- charter:` タグを付与して投入(タグ無しタスクが `has_consumable` に数えられず再分解が誤発火する問題も同時に修正)。
7. **観点メモ(C9)**: `<root>/notes/` に自由記述の md を書き溜める(dashboard にメモ追加 UI)。plan は notes を**自動では消費しない**。人が「メモを分解」操作(CLI `agent-project distill` / dashboard ボタン)をしたときのみ、notes 群を backlog-planner の入力に注入してバックログ候補を生成し(整合パス経由・proposed 投入)、消費済みメモは `notes/archive/` へ移す。メモ→バックログの流れは上記フロー図の第 2 の入口であり、人がバックログを直接書く必要はない。

---

### S7: スペック駆動のブラウンフィールド適合(C6)

**現状の問題**
- spec 連携(G1-G5)は実装済みだが、opt-in の `spec_track` + 採点しきい値(`spec_threshold` 既定 3)で「フル spec(spec/design/tasks の 3 点セット)を書くか書かないか」の二択しかない。既存コードベースでは 3 点セットのオーバーヘッドが大きい。

**仕様**
1. **spec を 2 段階にする**: フル spec(現行)に加えて**ライト spec** = `design.md` 相当 1 枚(変更方針・影響範囲・受入条件の差分記述)のみを導入する。
2. **3 段ルーティング**: 既存の assess 採点(c/r/a)で スキップ / ライト / フル を選ぶ。`spec_threshold` を `spec_threshold_light` / `spec_threshold_full` の 2 閾値に拡張(既定: light=2, full=4 相当。既存設定は full に読み替え)。`policy.md` の `spec:` ルールで強制も可能(現行踏襲)。
3. **既存コード文脈の前置**: ライト/フル spec タスクの前に、対象リポジトリの `context/<repo>.md`(repo-map)が無い・古い場合は read-only の調査 run を自動前置して更新する(S6-2 の作業概要生成と共通機構)。ブラウンフィールドで spec と計画の質を担保する supply 側の仕組み。
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

### S9: エージェント CLI 差分吸収レイヤと診断の対話化(C11)

**現状の問題**
- doctor は読み取り専用フラグ付きのヘッドレス 1 発実行(`agent.js:277-330`, `:409`)で、深掘りの追加質問ができない。
- より根の深い問題として、**tmux 経由でエージェント CLI を起動する処理の CLI ごとの差分がコードに散っている**: 対話コマンドの組み立て(`agent.js:218-247` の kiro/claude/copilot/codex/cursor/ollama 分岐)、読み取り専用フラグ(`agent.js:277-330` の CLI 別ハードコード)、入力受付の検出(`loopProvider.js:388-403` の capture-pane ポーリング)。CLI の挙動・作法が変わるたびに複数箇所の修正が要る。
- 一方、ヘッドレス片道実行については宣言的プラグイン契約(`schemas/agent-cli.schema.json` + `agents/<name>.json`)が既にあり、組み込み以外の CLI はデータ定義だけで差し込める。**対話モードだけが契約の空白**になっている。

**仕様**

1. **agent-cli プラグイン契約を対話モードへ拡張する**(`agent-cli.schema.json` に `interactive` セクションを追加。additive なので既存定義はそのまま有効):
   ```jsonc
   {
     "command": ["…"],              // 既存: ヘッドレス片道
     "interactive": {
       "command": ["…", "{model}"],   // 対話起動 argv
       "readonly_args": ["…"],        // 読み取り専用(助言のみ)モードにする追加フラグ
       "no_session_args": ["…"],      // セッション永続化を切るフラグ(診断など使い捨て用)
       "ready_pattern": "…",          // capture-pane 出力から入力受付を検出する正規表現
       "prompt_via": "send-keys" | "file"  // 初回プロンプトの注入方法
     }
   }
   ```
   - `ready_pattern` / `prompt_via` は現在 loopProvider がハードコードしている待ち受け・注入ロジックのデータ化。`file` は長大プロンプトを一時ファイルに書き「このファイルを読んで」を send-keys する方式(doctor のコンテキスト spill と同型)。
   > 上の jsonc は草案。**確定した契約は [S9 詳細設計 §2](2026-07-26-s9-agent-cli-layer-detailed-design.md) を見ること**
   > (`interactive.prompt_via` → `interactive.prompt_inject` へ改名、`readonly_args` / `no_session_args` をトップレベルへ、`spill` ブロックを追加)。
2. **組み込み CLI(kiro/claude/copilot/codex)も同梱の `agents/<name>.json` に移す**。dashboard の `buildInteractiveCommand` / `buildDoctorCommand` の CLI 分岐、および各ツールの対話起動箇所は、この定義を読むローダ 1 本(dashboard 側は JS、Python 側は agentcore)に集約する。**CLI の挙動・作法の変更は JSON 1 ファイルの修正で完結する**ことが受入条件。
3. **適用範囲**: dashboard の CLI チャット(`openInteractiveChat`)、cowork の tmux 実行、S9 の対話診断、kiro-loop の chat 起動 — tmux 経由の全エージェント起動がこのレイヤを通る。
4. **診断の 2 モード化**(このレイヤの最初の利用者):
   - **対話診断(新設・既定)**: doctor のコンテキスト(`buildDoctorContext` + spill ファイル)を初回プロンプトとして `runChatWindow` で tmux セッションを開く。起動 argv は `interactive.command + readonly_args + no_session_args` で組む。セッション名は `agent-doctor-<digest>` とし、同一 need の再診断は既存セッションへ attach する。
   - **文面生成(現行維持)**: 「差し戻し文面案」など構造化出力の抽出が要る用途はヘッドレス 1 発実行(既存契約)を残す。
   - 失敗診断ボタンは対話診断を開き、「文面を生成」ボタンを併設する。開いた診断セッションは kiro-loop feature の tmux 視聴(`kiroLoop:capture`)で dashboard 内からも覗ける。

---

## 4. 段階導入計画

| フェーズ | 仕様 | 状態 | 主な変更先 | 備考 |
|---|---|---|---|---|
| 1 | S1 + S3 | **実装済み** | agent-project(configfile/state/host)、agentcore(リゾルバ)、agent-flow(board マージ)、schemas | host.yaml 拡張(projects/repos/overrides)を 1 回で行う |
| 1' | S2 | **実装済み** | agent-dashboard(cowork) | 独立 |
| 1' | S9-1〜3 | **実装済み** | schemas(agent-cli)、agents/、agentcore(ローダ)、agent-project / agent-flow / agent-amigos / agent-dashboard | 独立。S9 のレイヤは 4 の診断より先に整備 |
| 2 | S4 → S5 | **実装済み** | agent-project(mr/verify/needs)、.github/skills(backlog-verifier)、agent-dashboard(needs) | acceptance チェックリスト書式は **S5 側で確定させ S6 が従う**（詳細設計 §2.3） |
| 3 | S6 → S7 | 未着手 | agent-project(plan/charter/prioritize)、.github/skills(backlog-planner)、agent-flow(planner_skill)、agent-dashboard(plan-review/notes UI) | S9-4(対話診断)と並行可 |
| 4 | S8、S9-4 | 未着手 | agent-dashboard、agent-project(常駐体) | S8-2/3 は W1-11(board tick)後 |

### 詳細設計と実装の所在

| 仕様 | 詳細設計 | 実装 |
|---|---|---|
| S1 | [`2026-07-26-s1-config-two-layer-detailed-design.md`](2026-07-26-s1-config-two-layer-detailed-design.md) | 実装済み(移行手順: `docs/guides/state-repo-migration.md`) |
| S3 / S2 | [`2026-07-26-s3-s2-node-repos-and-cowork-roots-design.md`](2026-07-26-s3-s2-node-repos-and-cowork-roots-design.md) | 実装済み |
| S9-1〜3 | [`2026-07-26-s9-agent-cli-layer-detailed-design.md`](2026-07-26-s9-agent-cli-layer-detailed-design.md) | 実装済み |
| S4 / S5 | [`2026-07-26-s4-s5-review-and-verification-detailed-design.md`](2026-07-26-s4-s5-review-and-verification-detailed-design.md) | 実装済み |

### Phase 1 の積み残し(次フェーズ以降へ持ち越し)

| # | 内容 | 待ち先 |
|---|---|---|
| P1-a | **S3-5: 板の `nodes/<node-id>.json` への `repos[].local` 転記** — その JSON を書く実装自体が無い(W1-11 残)ため、書き手ができるまで転記先が無い | S8 / W1-11 |
| P1-b | **S3-4 のパス手入力 UI** — main 側は任意パスを受けて実在検査までするが、画面はドロップダウンのみ。入口を足すだけで有効になる | 必要が出たとき |
| P1-c | **dashboard の repos.yaml/yml 読み取り** — CLIチャット候補の「宣言し忘れ」行は repos.json からのみ作る(このアプリは YAML パーサを持たない)。候補が減るだけで害は無い | 必要が出たとき |
| P1-d | **`cowork.roots` の掃除の口** — project になったフォルダの登録解除の動線が無い(表示は自動で正しくなる) | 必要が出たとき |
| P1-e | **`_source_repo` の共有 bare ミラーは blobless** — フォージ無し運用の自動マージで blob の遅延取得にネットワークが要る。確実にしたいノードは `repos[].local` にフルクローンを宣言する | S4(レビューと決着が MR/PR へ寄れば出番が縮む) |

いずれも「動作は正しいが最適でない / 別の実装待ち」で、Phase 2 以降を止めるものは無い。

## 5. 未決事項

~~1. **S1**: ワーカーノード(lite)の `worker init` と host.yaml 専有項目の整合。`projects[].overrides` に許すキーの最終リスト。~~
   → **決着**(S1 詳細設計 §7): worker init も同一スキーマを書き検証コードを共通化。overrides は SHARED 群 12 キー。
~~2. **S3**: `local` の鮮度責務(worker が毎回 `fetch` する現行方式を維持するか、ノード側で定期 fetch するか)。~~
   → **決着**(S3/S2 詳細設計 §6-2): 現行方式を維持(鮮度不変条件 INV-1)。ノード側の定期 fetch は非目標に触れるため見送り。
~~3. **S4**: MR/PR 自動作成のフォージ別対応順序(GitLab 先行、GitHub/Gitea の扱い)。フォージ書き込み API の認証情報の置き場。~~
   → **決着**(S4/S5 詳細設計 §1.7): GitLab 先行。GitHub/Gitea は `forge` アダプタ境界だけ切って未実装。
   認証情報は既存の環境変数/rc ファイル方式を踏襲し、host.yaml にもプロジェクト yaml にも置かない。
   あわせて **MR を誰が作るか**を比較検討し(同 §1.2)、agent-project 常駐体を採用。
   その帰結としてローカル diff は「MR が無いタスクに限り、S3 のノード宣言から解決したクローンで表示」とした(同 §1.3)。
~~4. **S5**: verifier の副作用の許容範囲(テスト実行は作業ツリー内に限定できるが、DB・外部サービスに触る検証の扱い)。verifier 自体の暴走・自己欺瞞への防御(検証レポートの抜き取り監査を人検収に組み込むか)。~~
   → **決着**(S4/S5 詳細設計 §4): 既定は作業ツリー内のみ(`verify_side_effects`)。DB・外部サービスへの書き込みは
   どの設定でも不可(要るなら人が `verify:` に書く)。自己欺瞞への防御は 4 段(証跡必須・フェイルクローズ・
   差分の常設基準・検収カードでの抜き取り監査)。監査は別機能にせず人が毎回見る 1 枚に載せる。
5. **S6**: 人編集タスクの保護と charter 大改訂の衝突(charter が根本から変わったとき人編集タスクをどう扱うか。`--revive` 同様の明示操作とするか)。墓標の指紋衝突で「作りたい新タスク」まで抑止しないか。
6. **S8**: 手動入札の「ノード直轄ワーカーで実行」への接続(落札後の実行系が W1-11 に含まれるため、単独では操作だけ増えて実行できない状態になり得る)。
~~7. **S9**: `readonly_args` の強制力は CLI の実装依存(フラグを無視する CLI への防御は持たない)。対話セッションの副作用は人が見ている前提で許容するか。~~
   → **決着**(S9 詳細設計 §6): 防御は持たない。代わりに定義へ `readonly: enforced | best-effort` を持たせ、
   保証できない CLI で読み取り専用を要求したときは画面に明示する。対話セッションの副作用は許容し、
   診断だけ `no_session_args` + 別セッション名で使い捨てにする。
