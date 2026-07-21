# agent-dashboard / agent-project 本番運用ハードニング計画

> **目的**: 本番運用に向けて、繰り返し発生しているバグの根本原因を潰し、
> 機能や制約を削ってでも全体をシンプルにする再設計計画。
> 全てをシステムで解決せず、負荷の低い作業は人間の運用に寄せる。

> **実装状況（2026-07-21）**:
> - **Phase 1「操作の受理確認(ack)」実装済み**。エンジンは commands 取り込み成功時に
>   `commands/processed/<name>.json` へ受理レシートを残し(`commands.py` の
>   `_write_command_receipt`)、dashboard は `listCommandReceipts` でそれを読み、要対応
>   カードに「受理されました」を表示する(`project.js` / `renderer/sections/needs.js`)。
>   テスト: エンジン 3 件、dashboard 5 件(`command-receipt.test.js`)。
> - **Phase 2.5「ノード割当」の静的割当(案 6-A)を先行実装済み**。エンジンにノード名
>   (`--node` / 環境変数 `AGENT_PROJECT_NODE`、共有 yaml には載せない)を与え、タスクの
>   `- node:` 割当に一致するタスク＋未割当タスク(`default_node` 規則)だけを消化する
>   (`prioritize.py` の `task_runnable_here`、`loop.py` の選択・`has_work`)。無名エンジンは
>   従来どおり全消化(後方互換)。dashboard は revise で `node` を付け替え可能
>   (`REVISE_KEYS` + revise フォームの実行ノード欄)。テスト: エンジン 5 件。
>   注: claim 調停の push 化(案 6-3)・`status/<node>.json` 分割・停止ノード回収 UI は未実装
>   (未割当タスクを default_node 空のまま複数の名前付きエンジンで走らせると従来同様の
>   二重実行リスクが残る。回避は各タスクへ node 割当か default_node を 1 台に設定）。
> - **Phase 2「状態リポジトリ分離(案 1)」のコード+移行手順を実装済み(適用は保留)**。
>   `state_repo` 設定で状態を成果物リポジトリの worktree ではなく専用リポジトリの通常 clone に
>   置き(`state.py` の `_redirect_root_to_state_repo`)、本体 main へのミラー(`backup_state`)を
>   無効化する。clone は普通の git リポジトリなので既存の `DirectStateGit` 同期がそのまま効く。
>   未設定なら従来の worktree 方式、clone 失敗時も worktree 方式へ自動フォールバック(後方互換)。
>   移行スクリプト `migrate-state-repo.sh` と手順書 `docs/guides/state-repo-migration.md` あり。
>   テスト: エンジン 4 件(`TestStateRepoSeparation`)。**worktree コードの削除・既存プロジェクト
>   への本適用は実機(多 PC/Gitea)確認まで保留**(方針: コード+手順を用意し適用は保留)。
> - **Phase 1「操作経路の file-drop 一本化+CLI 完全削除(案 2 後半)」実装済み**。
>   dashboard の `runAction`/`requestReplan` から `actionMode`(auto/file/cli)分岐・CLI 直接実行
>   (`runActionViaCli`)・サイレントフォールバックを撤去し、commands ドロップ一本にした
>   (`actions.js`)。稼働中の本体が同期越しに取り込み、受理レシート(ack)でカードへ反映。停止中は
>   取り込み待ちで残り「押しても何も起きない」原因不明の停滞を排除。併せて file-drop 経路で
>   落ちていた approve の `complete` フラグ(「承認して完了にする」)を修正。`actionMode` 設定と
>   設定 UI も削除。本体起動(`startProject`)のみ CLI を残す(停止中エンジンは commands を読めない
>   ため。既存の手動コマンド案内フォールバックあり)。テスト: dashboard 3 件追加・関連更新。
> - **案5「バックログ構造化+作成時 lint」実装済み**。作成フォームに構造化フィールド
>   (verify テンプレート・why/scope/out_of_scope)を追加し、投入前に情報不足・曖昧 accept を
>   警告する(非ブロック。`authoring.js` の `lintTaskSpec` + IPC `dashboard:lintTask`)。
>   enqueue が `node` フィールドも通す。テスト: dashboard 4 件。
> - **案3「verify テンプレ第一級化+検収経路の AI 撤去」実装済み**。作成フォームに verify
>   テンプレート選択(決定的展開のカタログ)を第一級で追加。検収(review)経路から dashboard 側の
>   WSL ヘッドレス AI(「変更理由を説明」「フォローアップ案」「AIに相談」)を撤去
>   (`needs.js`)。accept→verify の AI 合成はエンジン側ループのまま。
> - **案6「停止耐性」の可視化を実装済み**。エンジンがノード別 `status/<node>.json` を書き
>   (`loop.py`)、dashboard が `readNodeStatuses` で読んで概要に実行ノード一覧(稼働/応答なし)を
>   表示(`project.js`/`overview.js`)。停止ノードのタスク回収は既存 resume-run/revise に乗る。
>   テスト: エンジン 2 件・dashboard 2 件。**claim 調停の push 化(二重実行の完全排除)は多 PC
>   実機検証が要るため見送り**(要各タスクへの node 割当 or default_node 設定)。
> - **案4「viewer/engineer 役割分離+セットアップ診断」実装済み**。`role` 設定(既定 engineer)を
>   追加し、viewer では本体起動ボタンを隠す(`overview.js`)。設定に役割選択とセットアップ診断
>   ボタン(登録 clone の有効性を赤/緑表示。`git.js` の `diagnostics` + IPC `setup:diagnostics`)を
>   追加。テスト: dashboard 2 件。**役割による UI 出し分けの網羅・設定のプロジェクト側移管は
>   段階的に拡張予定**。
>
> 注: dashboard の描画層(renderer)は本環境に画面が無く実行検証ができないため、フル
> テストスイート(構文・文字列契約・分離 eval)での検証に留まる。UI の手動スモークテストを推奨。

## 前提となる運用形態

- agent-dashboard は Windows、agent-project などのエンジンは WSL で動作する。
- 複数の異なる PC から git を介して状況を監視する。
- バックログ毎に監視者を決め、監視者の指示のもと(システム外)、他のメンバーは
  dashboard からレビューコメントを投稿する。
- 最後に監視者がバックログの再実行 or 検収を判断して進める。
- **バックログ単位で、エンジンを動かす PC を変えられるようにしたい。**
- **ワーカー(act 実行)の作業も可能なら PC で分担したい。**
- **どの PC にも停止時間がある(常時稼働の PC は存在しない)前提とする。**

## 問題と根本原因

調査は実装 (`tools/agent-project/agent_project/`, `tools/agent-dashboard/src/`)、
設計文書 (`docs/designs/agent-project-design.md`, `docs/designs/agent-dashboard-project-ux-improvements.md`)、
および CHANGELOG / 直近 300 コミットのバグ修正履歴に基づく。

### P1. `-agent-state` worktree の作成エラー

**症状**: コミット汚染防止のため `<repo>-agent-state` worktree を作るが、
タイミングや Windows/WSL 環境差でエラーになる。

**根本原因**:

- worktree の生成・パス解決ロジックが **Python と JS の二重実装** になっており、
  両者がバイト単位で同じパスを算出し続ける必要がある
  (`agent_project/state.py:50 _ensure_state_worktree` と
  `agent-dashboard/src/features/agent-project/main/project.js:1488 ensureStateWorktree`)。
- WSL(POSIX 絶対パス)と Windows(UNC `\\wsl.localhost\...`)の混在を避けるため
  `--show-prefix` ベースの純関数パス再構築という繊細な仕組みが必要になっている
  (`project.js:1360-1435`)。
- `worktree add --no-checkout` → sparse-checkout → checkout の多段手順は、
  ブランチ未 fetch・リモートのみ存在・途中失敗の各タイミングで壊れる。
- 失敗時は **サイレントにメインツリーへ書き戻す** フォールバックがあり
  (`state.py:67-70`)、worktree が存在する目的(コミット汚染防止)を静かに裏切る。

CHANGELOG 上でも state-root 解決系の修正が繰り返されている
(`a11530a` 自動作成、`fromStateWorktree` 二重リダイレクト、resume-run の書き込み先誤りなど)。

### P2. verify / accept の CLI と AI サポート経路の停滞

**症状**: verify や accept のコマンドを人間が書くのは難しく AI サポートが必要だが、
記法や処理経路が複雑で、原因不明の停滞を招く。

**根本原因**:

- dashboard 側の AI サポートは Electron から `wsl.exe -e sh -lc "..."` を直接叩く
  同期パイプラインで、**最大 4 段のネストシェル**
  (Electron spawn → `cmd.exe /c start` → `wsl.exe bash -lc` → `timeout bash -c` → CLI)
  を通る (`agent.js:409 runCommand`, `loopProvider.js:215 windowStartCommand`)。
  各段が独自の quoting・エンコーディング・timeout・cwd 変換を持ち、
  どこかが固まると「起動を待っています」で止まる。
- kiro-cli の「位置引数プロンプトで stdin 無効」制約を回避するための
  temp ファイル退避 (`agent.js:354-381`)、tmux `capture-pane` を 0.5 秒×120 回
  grep するプロンプト検出ループ (`loopProvider.js:388-405`) など、
  迂回の上に迂回が積まれている。文字化け・send-keys 系の修正は直近だけで 7 回以上
  (`4f89504`, `67fda5a`, `f6f29aa`, `95c61ef`, `9c6565e`, `b16d191`, `d0d147f`)。
- 操作コマンドの経路が `actionMode` (auto/file/cli) の 3 択+サイレントフォールバック
  (`actions.js:442-480`) で、**承認が「成功したように見えて何も起きない」** 事象を生む。
- 同種の wsl.exe 実行実装が 3 箇所に重複
  (`agent.js`, `cowork/main/loopProvider.js`, `kiro-loop/main/exec.js`)。

### P3. 複数 PC の環境構築の複雑化

**症状**: 閲覧・操作 PC の環境構築が複雑で、想定外の設定により全体が壊れる。

**根本原因**:

- 閲覧するだけの PC にも「正しい clone パス + state worktree 解決 + CLI パス +
  flowBus + distro」が要求され、どれを誤っても **サイレントに壊れる**:
  CLI パス誤り→ file-drop へ無言フォールバック、flowBus 誤り→キャンセルが空振り、
  main 側 `.agent-project` を開く→古いバックアップを見る (`project.js:1459-1467`)。
- 統合されたオンボーディング手順書が存在しない(手順が README 各所と
  `docs/guides/gitea-windows-setup.md` に分散)。
- 「閲覧専用 PC」と「エンジン PC」の役割区別がなく、全 PC がフル設定を持つ前提。

### P4. バックログの情報量不足による計画レビュー困難

**症状**: バックログの情報が少なく計画レビューが困難。成果物が期待と合わず
試行錯誤コストが激しい。

**根本原因**:

- ガイド用フィールド (`why/desc/scope/out_of_scope/constraints/hints/demo`) は
  既にデータモデルに存在する (`model.py:148 TASK_GUIDE_KEYS`, PR #543) が、
  dashboard の作成 UI が構造化入力を促さず、自由記入の title + verify 程度で
  作成できてしまう。
- 曖昧な自然文 `accept:` が弱い verify に合成され手戻りの根本原因になるが、
  作成・編集時の lint がない(UX 改善設計の未解決ギャップ G6)。
- 計画レビュー (proposed → approve) 時に判断材料(分解結果・スコープ・
  期待成果物)がカードに揃っておらず、承認が形骸化する。

### P5. agent-state ブランチのドリフトと履歴肥大

**症状**: agent-state ブランチは状態のみ管理するが成果物リポジトリと同居するため、
main からドリフトし手動メンテが必要。コミット履歴も膨大。

**根本原因**:

- 状態と成果物が **同一リポジトリ** にある構造そのものが原因。
  - `backup_state` (`state.py:253`) が状態を main へ plumbing でバックアップし続け、
    main 側の履歴とドリフトを生む。
  - 5 秒毎に書き換わる `status.json`/`journal.md` のノイズコミットが
    成果物リポジトリの履歴を汚す(300 秒バッチでも積もる)。
  - 多重コミッタ(engine / viewer / agent-flow bus / 他ホスト)の競合解決のため、
    CAS update-ref・plumbing 3-way import・self-heal という深い機構
    (`stategit.py`) が必要になり、過去に完全スタック事故も起きている
    (`agent-project-design.md` §5.8)。

### P6. 実行 PC の固定と停止耐性の欠如(追加要件)

**要件**: バックログ(タスク)単位で実行 PC を選びたい。ワーカー作業も PC で
分担したい。どの PC にも停止時間がある。

**現状の制約**:

- タスクの実行権 (claim) は `claims/<id>.lock` の **ホスト内原子ファイル** で、
  同期対象から明示的に除外されている(`stategit.py:30`「同期遅延越しでは排他の
  意味を持たない」)。つまり **複数 PC のエンジンが同じバックログを分担する構成は
  現状サポート外**。同時に動かせば二重実行(ブランチ push・backlog 書き込みの
  衝突)が起きうる。
- `status.json` は単一ファイルで、複数エンジンが書くと競合する。
- 実行者失踪時の回収 (`batch.py:273 recover_stale_doing`) はホスト内の pid 生死
  判定に依存しており、**他 PC から見た「あの PC は死んでいるのか」を判断する
  仕組みがない**。PC が停止すると、そのタスクは doing のまま黙って止まる。
- どの PC でどのタスクを実行するかを指定するフィールド・UI がない。

## 設計方針

1. **消せる複雑性は機構ごと消す**。バグ修正の重ね塗りをやめ、バグの住処
   (worktree 二重実装・多段シェル・サイレントフォールバック)自体を撤去する。
2. **人間の低負荷作業は運用に寄せる**(状態リポジトリの初期作成、履歴リセット、
   閲覧 PC のセットアップ手順遵守など)。
3. **失敗は沈黙させず表に出す**。フォールバックで隠すのではなく、
   status / needs に「壊れている」と表示する。
4. 既存の不変条件は維持する:
   - done の根拠は verify のみ(UI から状態を書かない)。
   - 入力契約は `needs/` 記入・`inbox/` 投入・`commands/` 投入の 3 つのみ。
   - AI はファイルを書かない(人間の確認ボタンを経由する)。
   - レビューコメントは 1 ファイル 1 コメントの viewer 専用サイドカー。

## 修正案

### 案 1: 状態専用リポジトリへの分離(P1・P5 の根治)

状態(`.agent-project` 一式)を成果物リポジトリから切り離し、
**プロジェクト毎の状態専用リポジトリ**(例: `<project>-state`)の通常 clone で管理する。

| アプローチ | 実装コスト | リスク | 保守性 | シンプル化効果 | 推奨度 |
|---|---|---|---|---|---|
| A. 状態専用リポジトリに分離 | 中 | 中 | 高 | 大(worktree/バックアップ/ドリフト全廃) | ★★★ |
| B. 現行 worktree 方式の修正継続 | 低 | 高(再発) | 低 | なし | ★☆☆ |
| C. 状態を git 以外(共有フォルダ等)へ | 高 | 高 | 中 | 中(多 PC 同期を自作) | ★☆☆ |

B はこれまでの延長であり、二重実装と環境差起因の再発が続くことが履歴から明らか。
C は git が担っている多 PC 同期・履歴・競合解決を自作することになり本末転倒。
A は既存の managed-clone モード (`stategit.py:StateGit`, `.state-git` sparse clone) と
ほぼ同型で、実装資産を流用できる。

**A で消えるもの**:

- worktree 生成・sparse-checkout・パス再構築の **Python/JS 二重実装全部**
  (`state.py:_ensure_state_worktree`, `project.js:ensureStateWorktree/toStateWorktree/fromStateWorktree`)。
  状態リポジトリは普通の clone なので「開く場所を間違える」事故も消える。
- `backup_state` / `adopt_mirror_edits`(main へのバックアップとドリフト)。
- 成果物リポジトリの履歴汚染。状態履歴は状態リポジトリに隔離され、
  肥大したら**履歴リセット(re-init して現在ツリーだけ積み直す)を運用手順**として
  年数回実行すればよい(自動化しない)。
- 「viewer の git sync が main リポジトリを誤爆する」クラスの事故。

**変わらないもの**:

- 成果物のタスクブランチ `ap/<task-id>` は従来どおり成果物リポジトリ側。
- 多重コミッタ対策(CAS・3-way import・path-ownership 解決)は状態リポジトリ上で
  そのまま使う。対象が専用リポジトリになる分、事故半径が縮む。
- 検収 diff は従来どおり成果物リポジトリの `origin/<branch>` を fetch して見る。

**移行手順(人間運用)**:

1. 状態リポジトリを作成(Gitea/GitLab に空リポジトリ、低負荷・監視者作業)。
2. 既存 `agent-state` ブランチの内容を新リポジトリへ push
   (`git push <state-remote> agent-state:main`)。移行スクリプトを 1 本用意する。
3. `agent-project.yaml` に `state_repo:` を設定 → エンジン再起動。
4. 各 PC は状態リポジトリを clone し直して dashboard に登録
   (成果物リポジトリの clone は検収 diff 用に併存可)。
5. 安定後、旧 worktree コード・`backup_state` を削除。旧 `agent-state` ブランチと
   `<repo>-agent-state` フォルダは人間が削除。

### 案 2: 操作経路の一本化と受理確認(P2 の停滞撲滅・前半)

dashboard からエンジンへの操作を **`commands/*.json` の file-drop 一本** に統一する。

- `actionMode` (auto/file/cli) と CLI 実行・サイレントフォールバック
  (`actions.js:408-480`, `runProjectCli`)を削除する。
  file-drop は既に WSL UNC・リモートの主経路であり、git push で全構成に届く。
- **受理確認(ack)を追加する**: エンジンは `commands/` を ingest した際に
  処理結果レシート(例: `commands/processed/<name>.json` へ移動+結果記録)を残し、
  dashboard は各操作を「送信済み → 受理済み / 失敗(理由)」で表示する。
  「押したのに何も起きない」を構造的に排除する。
- エンジン停止中の操作は「送信済み(エンジン待ち)」と表示され、次回起動時に
  ingest される。即時性が要る場合の運用は「監視者がエンジン PC で起動を確認する」
  に寄せる(システムで解決しない)。

### 案 3: verify 記述の支援をテンプレート第一級+エンジン側 AI に再編(P2 後半)

- **verify_template カタログを UI の第一級にする**。人間はテンプレート+パラメタを
  選ぶだけにし、生シェルの記入は上級者向けの折りたたみに落とす。
  テンプレートは決定的展開 (`verify.py:expand_verify_template`) で既存機構のまま。
- **自然文 accept → verify の AI 合成はエンジン側 (`verify.py:synth_verify`) に一本化**し、
  dashboard 側から `wsl.exe` を同期で叩くヘッドレス AI 呼び出しを検収経路から撤去する。
  dashboard の役割は「accept 文とテンプレ候補を提示し、人間が確定したものを
  file-drop で送る」まで。AI の実行はエンジンのループ内(非同期・ログあり)で行う。
- これにより検収系から多段シェル・プロンプト検出ループ・temp ファイル退避が消える。
  残る wsl.exe 利用(ターミナルを開く等の補助機能)は **単一の WSL ブリッジ
  モジュールに集約**し、失敗を構造化ログで表示する。
  Doctor 等の対話系 AI 支援は当面現状維持とし、停滞報告が続く場合は撤去を判断する。

### 案 4: 閲覧専用ロールとセットアップ診断(P3)

- **PC の役割を `viewer` / `engineer` の 2 つに分ける**。
  - viewer PC の要件を「状態リポジトリを clone + dashboard に登録」の 2 手順に固定。
    WSL・CLI・flowBus・distro 設定は不要とし、role=viewer では該当機能を
    UI から非表示にする(中途半端に動いて壊れる状態を作らない)。
  - 操作(コメント・approve 等)は案 2 の file-drop + git push なので viewer で完結する。
- **設定の正をプロジェクト側に置く**。プロジェクト固有設定(state ブランチ名、
  flowBus 位置、verify テンプレカタログ等)は状態リポジトリ内の yaml を正とし、
  PC 側 config は「clone 一覧 + role」まで縮める。想定外のローカル設定で
  全体が壊れる面積を減らす。
- **セットアップ診断画面**を追加する: 登録 clone の有効性、追跡ブランチ、
  pull 成否、(engineer のみ)distro / CLI 解決 / flowBus を赤緑で表示し、
  誤設定を沈黙させない。既存 `git:health` (`git.js:405`) の拡張で実装する。
- **オンボーディング手順書を新設**(`docs/guides/agent-dashboard-onboarding.md`):
  viewer 2 手順 / engineer フル手順のチェックリスト。運用でカバーする部分を明文化。

### 案 5: バックログ作成の構造化と計画レビュー強化(P4)

- **作成フォームの構造化**: dashboard のバックログ作成/編集 UI を、既存ガイドキー
  (`why/desc/scope/out_of_scope/constraints/hints/demo`)+`accept`+`refs` の
  構造化フォームにする。データモデル変更は不要(`model.py` 既存キー)。
- **作成時 lint(G6 対応)**: `why`/`scope` 欠落、曖昧 accept(例:「いい感じに」
  「正しく動く」等の非検証可能表現)、verify もテンプレも無いタスクを警告する。
  警告は投入をブロックしない(判断は監視者)。
- **計画レビューカードの充実**: proposed カードに分解結果・`assess`・`spec`・
  `expect`・スコープを一覧表示し、監視者が「この計画で成果物が出るか」を
  投入前に判断できるようにする。差し戻しは既存 `revise`/feedback 契約を使う。
- **作成時の AI 計画批評**: 既存の plan-critique(B1 実装済み)を作成フォームでも
  呼び出し、「この情報量で成果物がぶれないか」の指摘を投入前に受けられるようにする
  (AI は書かない、人間が反映する)。

### 案 6: ノード割当による複数 PC 分散実行と停止耐性(P6)

各エンジン PC に安定した **ノード名** を与え、タスク単位で「どのノードが実行するか」
を割り当てる。調停はすべて **状態リポジトリへの git push の原子性** に委ね、
常時稼働のコーディネータを置かない。

| アプローチ | 実装コスト | リスク | 停止耐性 | シンプルさ | 推奨度 |
|---|---|---|---|---|---|
| A. 静的割当(node フィールド)+push 調停 | 中 | 低 | 高(待ちが可視) | 高 | ★★★ |
| B. 動的クレーム(空いている PC が自動で拾う) | 高 | 中(同期遅延の競合) | 高 | 低 | ★★☆ |
| C. 常駐ジョブサーバ/キューの導入 | 高 | 高 | 低(常時稼働前提) | 低 | ☆☆☆ |

C は「どの PC にも停止時間がある」前提と矛盾する。B は自動化として魅力的だが、
pull 間隔(最短 60 秒)の窓で複数 PC が同じタスクを拾う競合を push 調停で
弾き続けることになり、挙動が読みにくい。運用形態が「監視者がバックログ毎に
判断する」である以上、**割当も監視者の明示操作(A)に寄せる**のが一貫する。
B は A の上に後から足せる(未割当タスクだけ自動クレーム対象にする)ため、
まず A のみ実装する。

**設計**:

1. **ノード名**: エンジン PC のローカル設定に `node:`(例: `pc-a`)を持たせる。
   PC 側に置く設定はこれと clone 一覧・role のみ(案 4 の方針を維持)。
2. **割当フィールド**: タスクに `node: <名前>` を持たせる(既存の extra フィールド
   機構で追加、スキーマ変更は軽微)。プロジェクト設定に既定ノードを置ける。
   エンジンは「自ノード宛て(または未指定かつ自分が既定)」のタスクだけを
   消化対象にする。割当・変更は dashboard から既存 `revise` 契約
   (`fields.node`)で行う=監視者の操作。
3. **claim の調停を push に変更**: ready→doing の遷移コミットを状態リポジトリへ
   **push 成功させてから act を開始する**。push が拒否されたら fetch+3-way import
   して再確認し、既に他ノードが doing にしていれば手を引く。git の push 原子性が
   分散ロックの代わりになるため、ロックサーバも時計同期も不要。
   ホスト内の `claims/<id>.lock` は同一 PC 内の多重起動防止として存続。
4. **status のノード分離**: `status.json` を `status/<node>.json` に分割し、
   dashboard はノード一覧(最終確認時刻・実行中タスク)を表示する。
   journal は既存の union-merge で多ノード書き込みに耐える。
5. **停止時間の扱い(すべて可視化+人間判断)**:
   - 割当先ノードが停止中の ready タスク → dashboard に「`pc-a` 待ち
     (最終確認 N 分前)」と表示。急ぐ場合は監視者が `node` を付け替える。
   - doing のままノードの heartbeat が途絶したタスク → dashboard が
     「実行ノード応答なし」を警告表示し、監視者が「回収して再割当」ボタン
     (既存 `resume-run`/`revise` の commands 投函)で ready に戻す。
     **他ノードによる自動奪取はしない**(二重実行より停止の方が安全。
     回収は人間の 1 クリック運用に寄せる)。
   - 共有依存は git リモート(Gitea)のみ。Gitea 停止中は新規 claim ができず
     新規着手が止まる(安全側)が、実行中の act・ローカルコミットは継続し、
     復旧後の push 再試行で追いつく。
6. **ワーカー分担はエンジン同居で実現**: act(agent-flow ワーカー)は claim した
   ノード上で実行する。つまり **ノード割当=ワーカー負荷の分担** であり、
   エンジンとワーカーを別 PC に分けるリモートワーカー構成は導入しない
   (agent-flow の remote/daemon 経路を跨ぐ複雑性を避ける)。
   実行後の成果物は `ap/<task-id>` ブランチ push で共有される(既存どおり)。

**前提**: 案 1(状態専用リポジトリ)と案 2(file-drop+ack)の後に実装する。
push 調停は状態リポジトリが専用化されているほど競合が少なく、割当・回収の
操作は commands+ack の可視化に乗る。

## フェーズ計画

依存関係: 案 2(経路一本化)が最初。ack 経路が入ると以降の全操作の観測性が上がり、
案 1 の移行検証も楽になる。案 1 は最大の構造変更なので単独フェーズにする。
案 6(ノード分散)は案 1・案 2 の成果(専用状態リポジトリ+ack)の上に載せる。

### Phase 0 — 運用先行(コード変更なし、即時)

- オンボーディング手順書と viewer/engineer 役割の運用ルールを文書化。
- 状態リポジトリ運用の試行: 1 プロジェクトで状態リポジトリを手動作成し、
  既存 managed-clone モードで動くか確認(案 1 の先行検証)。
- 監視者向け: バックログ作成時にガイドキーを埋める運用を先行開始
  (フォームは Phase 3 だが、フィールド自体は今日から書ける)。

### Phase 1 — 停滞撲滅(案 2 + 案 3)

1. `commands/` レシート(ack)をエンジンに実装(ingest 時に結果を記録)。
2. dashboard の操作表示を「送信済み/受理済み/失敗」の 3 状態にする。
3. `actionMode`・CLI 実行・フォールバックを削除し file-drop 一本化。
4. 検収経路から dashboard 側 AI 呼び出し (`agent.js` の taskAssist 系) を外し、
   verify テンプレカタログ UI と accept 文の提示に置き換える。
5. 残す wsl.exe 呼び出しを単一ブリッジモジュールへ集約、構造化エラー表示。

対象: `agent_project/model.py (ingest_commands)`,
`agent-dashboard/src/features/agent-project/main/actions.js`,
`agent.js`, `renderer/sections/needs.js`, `cowork/main/loopProvider.js`,
`kiro-loop/main/exec.js`。

### Phase 2 — 状態リポジトリ分離(案 1)

1. `state_repo:` 設定の追加。managed-clone モードを状態リポジトリ前提に整備。
2. 移行スクリプト(既存 `agent-state` ブランチ → 状態リポジトリ)。
3. dashboard を「状態リポジトリ clone を直接開く」対応にし、worktree 解決を
   バイパス。
4. 全プロジェクト移行後、worktree 生成(Python/JS)・`backup_state`・
   `adopt_mirror_edits`・`DirectStateGit` の worktree 経路を削除。
5. 履歴リセット手順を運用文書化。

対象: `agent_project/state.py`, `stategit.py`, `configfile.py`,
`agent-dashboard/src/features/agent-project/main/project.js`。

### Phase 2.5 — ノード分散実行(案 6)

1. エンジンのローカル設定に `node:` を追加し、消化対象を「自ノード宛て+
   未指定(既定ノードのみ)」に絞る。
2. ready→doing の claim を「状態リポジトリへの push 成功」まで待つ方式に変更。
   push 拒否時は再 import して手を引く。
3. `status.json` → `status/<node>.json` 分割と dashboard のノード一覧表示。
4. dashboard に「node 割当/変更」(revise 契約)と「応答なしノードからの回収」
   (resume-run 契約)の UI を追加。
5. 「割当先ノード停止中」「heartbeat 途絶」の警告表示。

対象: `agent_project/batch.py (claim_task)`, `loop.py`, `state.py (status 書き出し)`,
`model.py (node フィールド)`,
`agent-dashboard/src/features/agent-project/main/project.js (status 読み)`,
`actions.js`, `renderer/sections/*.js`。

### Phase 3 — 入力品質(案 5)

1. バックログ構造化フォーム+作成時 lint。
2. proposed(計画レビュー)カードの判断材料充実。
3. 作成フォームからの plan-critique 呼び出し。

対象: `renderer/sections/*.js`, `actions.js (enqueueToInbox)`,
`project.js (パーサは既存キー対応済み)`。

### Phase 4 — セットアップ簡素化(案 4 残り)

1. role (viewer/engineer) 設定と UI の出し分け。
2. プロジェクト側 yaml への設定移管、PC 側 config の縮小。
3. セットアップ診断画面。

対象: `base/main/config.js`, `base/main/git.js`, `features/*/config.js`,
`renderer.js`。

## 削除一覧(シンプル化台帳)

| 削除対象 | 場所 | 代替 |
|---|---|---|
| state worktree 生成・sparse-checkout(二重実装) | `state.py`, `project.js` | 状態専用リポジトリの通常 clone |
| `backup_state` / `adopt_mirror_edits` | `state.py` | 不要(同居しないため) |
| `actionMode` (auto/cli) とフォールバック | `actions.js` | file-drop 一本+ack |
| dashboard 側ヘッドレス AI(検収経路) | `agent.js` | verify テンプレ UI+エンジン側合成 |
| wsl.exe 実行の 3 重実装 | `agent.js`/`loopProvider.js`/`exec.js` | 単一ブリッジモジュール |
| 状態履歴の自動メンテ(自動 squash 等の構想) | — | 年数回の手動履歴リセット(運用) |

## 見送り(今回やらないこと)

- メトリクス/計測 UI(手戻り率・リードタイム等、ギャップ G5): 価値はあるが
  複雑性削減が先。Phase 2 完了後に再検討。
- ポリシー駆動の自動承認(G3): 「done は verify のみ」の不変条件に触れるため
  本計画では扱わない。
- コメントのスレッド化・既読管理: 運用(監視者の指示系統)でカバー継続。
- agent-flow 内部 verify の CLI 化: 設計文書どおり将来課題のまま。
- 未割当タスクの自動分散(空いているノードが自動で拾う、案 6-B): まず静的割当で
  運用し、割当作業が負担になった時点で「未割当のみ自動クレーム」を検討する。
- エンジンとワーカーを別 PC に分けるリモートワーカー構成: ノード割当で
  ワーカー負荷分担は達成できるため導入しない。

## リスクと対策

- **移行期の二重モード**: Phase 2 中は worktree 方式と状態リポジトリ方式が併存する。
  プロジェクト単位で一括切替(混在させない)とし、切替はプロジェクトの
  アイドル時に監視者が実施する。
- **file-drop 一本化の即時性低下**: エンジン停止中は操作が保留される。
  ack 表示で保留が見えるため「原因不明の停滞」にはならない。起動確認は運用。
- **状態リポジトリの増加**: プロジェクト数ぶんリポジトリが増える。
  Gitea 上の作成はテンプレ化し、監視者の初期作業 1 回に抑える。
- **既存プロジェクトの後方互換**: 移行スクリプトと手順書で対応。
  旧構成の自動検出時は診断画面で「移行してください」と明示する(黙って動かさない)。
- **claim の push 必須化による着手遅延**: 新規着手のたびに push 往復が入る
  (数秒)。タスク実行は分単位なので許容。Gitea 停止中は新規着手が止まるが、
  これは仕様(安全側)であり、診断画面と ack 表示で「リモート不達」と見える。
- **ノード名の重複設定**: 2 台が同じ `node` 名を持つと同じタスクを取り合うが、
  push 調停により片方しか doing にできない(二重実行には至らない)。
  診断画面で同名ノードの同時 heartbeat を警告する。
- **停止 PC からの回収の判断ミス**: 実はまだ動いている PC から回収すると
  二重実行になりうる。回収 UI には最終 heartbeat 時刻と実行中 run の経過を
  併記し、猶予(例: 途絶後 10 分未満は回収ボタンを出さない)を設ける。

## 決定記録

- 状態と成果物の同居をやめ、状態専用リポジトリに分離する(案 1-A)。
  worktree 方式の修正継続(1-B)は再発履歴から棄却。
- dashboard→エンジンの操作は file-drop+ack に一本化し、CLI 直接実行は削除する。
- 検収経路の AI 実行はエンジン側に限定し、dashboard は提示と確定のみ行う。
- 閲覧 PC は「clone+登録」の 2 手順で完結させ、それ以外の設定を要求しない。
- バックログの情報量は新フィールドではなく、既存ガイドキーの入力導線と lint で解決する。
- 複数 PC の実行分担はタスクの `node` 静的割当+git push 調停で行い、
  常駐コーディネータ・自動奪取・リモートワーカー構成は導入しない。
  停止 PC からの回収は監視者の明示操作とする。
