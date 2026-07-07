# kiro-projects-viewer

kiro-projects のプロジェクト状態をダッシュボードとして可視化する Electron アプリ。
[gitlab-review-viewer](../gitlab-review-viewer/) と同じ構成（プレーン Electron・
ランタイム依存なし・main / preload / renderer の 3 層）で作られている。

```
┌ サイドバー ────────┐┌ メイン ─────────────────────────────────────────┐
│ コンテナ(--root)    ││ 概要      charter / acceptance 達成状況 / 統計    │
│  └ プロジェクト     ││ バックログ  タスク一覧（status / priority / verify）│
│     ● 稼働中        ││ 要対応     needs/（人の判断待ち・検収待ち）        │
│     [needs] [tasks] ││ フロー     kiro-flow run のタスクグラフ（DAG）     │
│                     ││ レビュー待ち repos のオープンイシュー → レビューへ │
│                     ││ 履歴      run-log / 決定記録 / 納品 / journal     │
└─────────────────────┘└──────────────────────────────────────────────┘
```

## 何が見えるか（データソース）

すべて **読み取り専用**。kiro-projects / kiro-flow のファイルを直接読む
（両ツールの稼働は不要。稼働中なら自動更新で追従する）。

| タブ | データソース |
|------|-------------|
| 概要 | `charter.md`（goal / deliverables / acceptance）・`project.json`（acceptance PASS 履歴）・`backlog/` 集計・`policy.md`・`claims/`・`run-log.jsonl`・`DELIVERY.md`・`status.json`（daemon の生存信号。instances に無ければこちらへフォールバック） |
| バックログ | `backlog/<id>.md`（1 ファイル = 1 タスク。status / priority / verify / after 等）・`archive/<id>.md`（done） |
| 要対応 | `needs/<id>.md`（MADR 形式。blocked / review / milestone。「ファイルを開いて回答」でエディタへ） |
| フロー | `<bus>/runs/<run-id>/`（`graph.json` + `results/` + `claims/` からノード状態を導出し DAG を描画。`events/*.jsonl` のアクティビティ付き）。バスは `<project>/bus` → `<container>/bus` → ⚙ 設定 → kiro-projects 設定ファイル（`.kiro/`）の `bus:` の順に自動発見。run の生存（orchestrator 応答なし）は `meta.json` の生存リース（`orch_lease_until`）から、daemon の稼働はロックファイル（`$TMPDIR/kiro-flow-locks/daemon-<sha1>.lock` の pid。同一ホストのみ）から、無ければ `<bus>/status.json`（生存信号。state_git 同期経由の推定）から判定 — **kiro-flow CLI には一切聞かない**。ノード詳細では進捗（開始・経過・worker heartbeat/lease・所要・作り直し回数・claimed/result のタイムライン）と、gitlab executor の**関連イシュー**（承認は `data`、却下は output の URL、実行中は決定的タスクトークンの GitLab 検索）を表示し「レビューで開く」で gitlab-review-viewer へ引き継ぐ。run 表示ペインは**概要 / タスクグラフ / ノード情報**の縦 3 段に分かれ、各段が独立して縦スクロールする（グラフが縦に長くても概要・ノード詳細を見失わない） |
| レビュー待ち | `repos.json` の GitLab リポジトリのオープンイシュー＋関連 MR（API 設定時）。プロジェクトが扱うリポジトリの「いまレビュー待ち・作業中」を横断一覧し gitlab-review-viewer へ引き継ぐ。既定では **kiro-flow 由来のイシュー**（gitlab executor が起票 = 本文の `task-token` マーカー）だけに絞る（「kiro-flow 由来のみ」チップで解除可）。各行の **「関連 run」列**は、イシュー本文の `task-token` をロード済み run 一覧の各ノードの決定的トークンと突き合わせて起票元の run/ノードを特定し、クリックでフロー画面のその run・ノードを直接開く（イシュー URL は承認/却下まで bus に現れないため、レビュー待ち中の対応付けはこのトークン一致で行う。追加の API/走査コストは無し）。run/ノード単位の委譲イシューの決着（承認/却下）はフロータブのノード詳細が担当 |
| 履歴 | `run-log.jsonl`・`decisions/<id>.md`（DR）・`DELIVERY.md`・`journal.md` |

### 関係性のたどり（charter → backlog → run → issue）

タブ構成はそのままに、**どのタスクがどの run（GitLab イシュー）につながっているか**を可視化し、
クリックで関連画面へ遷移できる。鍵は kiro-flow の決定的 run-id
`req-<backlogハッシュ>-<taskid>-r<retries>` — ここから紐づくバックログタスクとリトライ系統を復元する。

- **リトライは「意味的に同一」なので束ねる**: 同一タスクの `…-r0 / …-r1 / …` は 1 系統として
  フロー一覧にまとめ、最新試行を見出しに、過去の試行は色付きピル（`r0` `r1` …）で畳む。
  `--inherit-from` で先行 run を引き継いだ run には「↩ 引き継ぎ元」を併記する。
- **パンくず**（タスクダイアログ・run 詳細）: `🎯 charter ▸ 🗒 task ▸ ⚙ run(系統) ▸ 🔗 issue`。
  各セグメントはクリックで対応する画面へ飛ぶ（run→フロー、task→バックログ、issue→GitLab）。
- **相互リンク**: バックログ各行に関連 run バッジ `⚙N`（クリックでフローへ）、フロー一覧に
  タスクリンク `🗒 <taskid>`（クリックでバックログのタスクダイアログへ）、タスクダイアログに
  「関連する kiro-flow run（リトライ系統）」一覧。

プロジェクトの発見は次の 2 系統:

1. **設定の roots** — ⚙ 設定に `.kiro-projects` コンテナ（kiro-projects の `--root` に渡す値）を登録
2. **自動発見** — `~/.kiro-projects/instances/*.json`（稼働発見レコード）から稼働中コンテナを検出。
   heartbeat が新鮮なプロジェクトには ● 稼働中マークが付く

`<root>/projects/<name>/` の標準レイアウトと、`projects/` を持たない旧フラット構成の両方に対応。

### リモートで稼働する kiro-projects を見る（state_git 経由）

本体が別マシン（リモートサーバ・WSL 外のホスト等）で稼働していてコンテナを直接読めない場合は、
kiro-projects の **状態 git 同期（`state_git`）** で共有できる（本体側 README の
「状態の git 保存・共有」参照）。本体がコンテナ状態を共有 git リポジトリへ双方向同期するので、
viewer 側は:

1. 共有リポジトリを clone し、[git-file-sync](../git-file-sync/) の pair
   （`repo_subpath: kiro-projects`・`bidirectional`）などで定期同期する
2. ⚙ 設定「コンテナのパス」に `<clone>/kiro-projects`（本体側 `state_git_subdir` のパス）を登録する

viewer の操作（needs 記入・commands/ ドロップ・inbox/ 投入）は通常どおりファイルとして書かれ、
git-file-sync（または手動 commit/push）が同一リポジトリへコミットすると、本体側が idle の pull で
取り込んで次パスを起こす。指示の反映は同期間隔（本体側 `state_git_interval`・既定 300 秒）ぶん遅れる。

同期は viewer 自身でもできる（git-file-sync なしで完結する）:

- **pull（取り込み）** — サイドバーの **⇣ ボタン**で選択中プロジェクトを含むリポジトリを
  即時最新化、⚙ 設定「git pull 間隔」（既定 300 秒・0 で自動なし）で自動 pull。
  自動 pull はポーリング（既定 5 秒）とは独立にリポジトリ単位でスロットリングされ
  （下限 60 秒。失敗時も間隔を空ける）、**リモートサーバへ数秒おきに fetch を投げる
  ことはない**。git リポジトリでないプロジェクトでは黙ってスキップされる
- **push（都度反映）** — ⚙ 設定「操作を都度コミットしてプッシュ」（既定オフ）を有効に
  すると、ユーザー操作（指示ドロップ・inbox 投入・needs 記入・タスク/run 削除）のたびに
  操作したディレクトリの変更をコミットして push する。有効時は pull も
  `--rebase` になり、未 push のローカルコミットと共存して取り込める
  - **run（バス）操作の反映先に注意**: バスは kiro-projects の state_git から除外され
    （`bus`/`claims`）、kiro-flow 側の state_git が別クローンへ同期する。そのため
    `<project>/bus` のようなローカル daemon バスは **viewer から直接 push できない**
    （その場合 run 削除/再投入は kiro-flow daemon の state_git 同期に反映が委ねられ、
    viewer はその旨をトーストで知らせる）。viewer から直接反映したいときは、⚙ 設定
    `flowBusByProject` で **バスの git クローン**（`プロジェクト名 = <clone>/kiro-flow`）を
    登録する。登録済みなら run 削除/再投入もそのクローンへコミット・push される
- **多重コミッタとの共存**（本体の state_git / kiro-flow GitBus と同じ護り）:
  ステージ・コミットとも**操作したディレクトリの pathspec に限定**し、同じクローンへ
  コミットする他プロセスの変更を巻き込まない。push 競合は `pull --rebase --autostash` →
  再 push の指数バックオフ（最大 3 回）で吸収し、**force push はしない**。pathspec 限定
  コミットの結果、他プロセスの未コミット変更で作業ツリーが汚れているのが常態のため、
  `--autostash` で退避→取り込み→復帰させて rebase を走らせる（他プロセスの変更は
  巻き込まずそのまま作業ツリーへ戻る）。ロック起因の失敗
  （`index.lock` 等）はリトライし、30 秒以上古い残骸ロックは自己回復する。
  自プロセス内の pull / push はリポジトリ単位の直列化キューで重ねない。
  rebase が進められない（コンフリクト）ときは abort して作業ツリーを壊さず、
  エラーをトーストで知らせる（本体側の次の 3-way 同期が裁定する）

フロータブも同様: kiro-flow 側の `state_git`（kiro-flow README「状態の git 保存・共有」）で
ローカルバス（`runs/`・`inbox/`）が同じ共有リポジトリの別 subdir（既定 `kiro-flow`）に同期される
ので、⚙ 設定（または `.kiro/` の kiro-flow 設定の `bus:`）でバスとして `<clone>/kiro-flow` を
指すと、リモートの run の進捗/結果を DAG で追える（run の生存は meta の生存リース
`orch_lease_until` から従来どおり判定される。daemon 自体の稼働判定は下記参照）。

#### プロジェクトごとに別リポジトリで共有する（`state_git_projects`）

本体が**プロジェクトごとに別々のリポジトリ**へ状態を分けている場合（本体 README
「プロジェクト単位で保存先リポジトリを分ける」。kiro-flow 側は kiro-projects が per-project daemon を
起動して各バスをそのリポジトリへ鏡写しする）——例えば
`default` は個人リポジトリ、`alpha` はチーム共有リポジトリ——viewer は各リポジトリの clone を
それぞれ登録するだけで、全プロジェクトを 1 画面に束ねて見られる。使う人ごとにアサインされる
プロジェクトが違っても、**自分がアクセスできるリポジトリの clone を足すだけ**でドライブできる。

1. **コンテナ**: プロジェクトごとの clone の `<clone>/kiro-projects` を、⚙ 設定「コンテナのパス」に
   **1 行ずつ**追加登録する（リポジトリ内も `projects/<name>/` レイアウトを保つので、従来の
   コンテナと同じように各プロジェクトが並ぶ）。個人リポジトリとチームリポジトリを混在登録してよい。
2. **フローバス**: プロジェクトごとの kiro-flow リポジトリの clone を割り当てる。ローカルに daemon が
   いない pure-remote 監視では、⚙ 設定「プロジェクト単位バス」に 1 行 1 件
   `プロジェクト名 = <clone>/kiro-flow` を書く（`kiro.flowBusByProject`）。ローカルの
   `<project>/bus` に `runs/` が実在するときはそちらが優先され、clone だけのときにこの写像が効く。

指示の書き戻し（needs 記入・commands ドロップ・inbox 投入）は各プロジェクトが属する clone へ
コミット／push され、そのプロジェクトを回している本体（担当者の daemon）が同期間隔内に取り込む。

#### daemon の稼働判定（同期経由の推定）

本体が別ホストの場合、従来はサイドバーの ● 稼働中バッジも概要タブの実行状況も出せなかった
（`~/.kiro-projects/instances/` はローカルの生存レジストリで、同期対象に含まれないため）。
本体が `<project>/status.json`（生存信号。本体側 README「daemon の生存信号」参照）を書くように
なったため、これも state_git で同期されてくる。viewer は次の順で稼働を判定する:

1. **instances**（同一ホスト・heartbeat 鮮度）— 確定判定。従来どおり
2. **status.json**（同期経由）— instances に無ければこちらにフォールバックし、
   `updated_iso` が本体自身の計算した鮮度窓（`fresh_after_sec`）以内なら「稼働中」とみなす

サイドバーの ● は判定根拠を区別して表示する（同期経由の推定は輪郭のみの◯＋プロジェクト名に
`~` を付け、確定判定と見分けられるようにする）。プロジェクトの**概要タブ**には「daemon の生存」
カードを追加し、判定根拠・最終確認からの経過時間・`watch`/`level`・最終サイクル（`run-log.jsonl`
の末尾。これも既に同期対象）を表示する。

status.json は本体側の実パス完了時にのみ更新されるため、**長時間 idle が続くと最終確認時刻は
古いまま**になる（本体側の設計: idle 中の追加 git コミットを既定でゼロに保つトレードオフ）。
より新鮮な生存表示が要る場合は本体側で `--status-interval`（例 `3600`）を指定してもらう
（idle 中もその間隔で status.json だけ更新され、その分だけ state_git のコミットが増える）。

#### フロータブの daemon 稼働判定（kiro-flow・同期経由の推定）

kiro-flow の daemon 稼働もロックファイル（`$TMPDIR/kiro-flow-locks/`）判定は同一ホスト限定
——state_git（鏡）越しにバスを見ているときは daemon の一時領域に届かず、常に判定不能だった。
kiro-flow 本体が `<bus>/status.json`（生存信号。kiro-flow README「daemon の生存信号」参照）を
書くようになったため、フロータブの daemon バッジも同じ二段判定に対応する:

1. **ロックファイル**（同一ホスト・pid 生存）— 確定判定。従来どおり
2. **status.json**（同期経由）— ロックが無ければこちらにフォールバックし、`updated_iso` が
   `fresh_after_sec` 以内なら「稼働中（推定）」、超過なら「不明（同期経由）」と表示する
   （実行中の run 数・worker 数もツールチップに出す）

kiro-projects 側と同じトレードオフ: 既定はアイドル中の追加 git 負荷ゼロで、鮮度が要るなら
kiro-flow daemon 側で `--status-interval` を指定する。GitBus（`--git`。バス自体を共有 git に
して実行を分散するモード）はこの機能の対象外（sparse-checkout が対象外パスになるため
daemon 側が書かない）——今のところ state_git（鏡）でリモートから run を眺める構成のみが対象。

## 人のアクション（見るだけでなく、その場で判断を返せる）

kiro-projects の人間ループはこのアプリ内で完結できる。いずれも kiro-projects の
**公式な入力契約だけ**を使い、done の確定条件（verify のみが根拠）を迂回しない。

| 操作 | 場所 | 実装（入力契約） |
|------|------|-----------------|
| フィードバックして再開 | 要対応カード | `needs/<id>.md` の「## Decision Outcome」に記入 + `- [x]` 確定（`ingest_feedback` の正規ルート） |
| そのまま再実行 | 要対応カード（blocked） | 空記入で `- [x]` 確定 |
| 承認して done 確定 | 要対応カード（review / milestone） | `commands/<name>.json` ドロップ（`ingest_commands` が CLI と同一ロジック・同一 DR で実行）。稼働していなければ CLI にフォールバック |
| 差し戻す | 要対応カード（review） | 修正方針の記入必須 → feedback として確定（手戻り扱い） |
| 保留（hold） | 要対応カード・タスク詳細 | 同上（`{"command":"hold"}` ドロップ → policy.deny 追加） |
| 最優先へ / 後回し | タスク詳細 | 同上（`{"command":"pin"/"defer"}` ドロップ → policy 追記） |
| ✎ 修正して指示（revise） | タスク詳細（backlog のみ） | 同上（`{"command":"revise"}` ドロップ）。タイトル・優先度・依存 after・verify・accept の**置換**とフィードバック注入。**実行中（doing）のタスクにも送れ**、本体は現在の試行の結果を確定せず（verify も done もせず）修正内容で積み直す＝気づいた時点の早い軌道修正。変更した項目だけが送られ、DR（`action: revise`）に記録される |
| ＋ バックログに追加 | バックログタブ | `inbox/<name>.json` ドロップ（E4 push 型取り込み口）で**バックログにタスクを 1 件追加**（本体が次サイクルで `backlog/<id>.md` にする）。verify / accept / priority / note / id / after 付き |
| ✎ タスクグラフを積み直す（after 含む revise） | タスク詳細（backlog のみ） | revise（`commands/`）で **依存 after** を含む項目（title / 優先度 / verify / accept / after / note / level / track）を置換。本体が取り込むと `rev` を上げ、kiro-flow に**新しいタスクグラフ（run の DAG）**を作らせる（実行中タスクは現在の試行を破棄して積み直し）。after 編集は DAG 循環を本体側が拒否。状態（done 等）は書かない |
| ＋ 新規プロジェクト | サイドバー ＋（コンテナが無い空状態にも導線） | `<root>/projects/<name>/charter.md`（goal / constraints / deliverables / acceptance / repos をフォームから）と、repos があれば `repos.json`（`_meta.generated_from` 付き＝正は charter）を作成。以後は kiro-projects の run が charter から backlog を生成する（専用の作成コマンドは無く、charter を置くだけが公式手順）。コンテナが未登録なら設定 roots に追加して発見対象にする |
| ✎ プロジェクトファイル編集 | 概要タブ「プロジェクトファイル」 | 人が書く**上位入力だけ**をアプリ内で直接編集: `charter.md`（最上位入力）／`policy.md`（運用ルール）／`repos.json`（レジストリ）。保存すると次の run で後段データ（backlog 生成・ルーティング）に反映される。repos.json が charter からの自動生成物（`_meta`）のときは「run 時に charter で上書きされる」旨を警告する。JSON は保存前に構文検証。タスク状態ファイル（`backlog/*.md` の status 等）は編集対象にしない — done の不変条件を壊さないため |
| ↻ revise して再投入 | タスク詳細（**archive のみ**） | archive（done）タスクの内容（title / verify / accept / priority / note / after / level / track）を prefill した投入フォームを開き、編集して `inbox/<name>.json` ドロップ。**新しいタスク**として triage→verify を通す（archive の記録はそのまま残る）。誤 done などの**エラー復帰用途**。元 ID を引き継ぐが衝突時は本体が採番し直す |
| レビュー操作（承認/差し戻し/コメント） | レビュー待ちタブ／フロータブのノード詳細 →「レビューで開く」 | gitlab-review-viewer へ引き継ぎ |
| 🗑 タスク削除 | タスク詳細（backlog のみ） | **例外的にファイル操作**（削除の公式契約が無いため）。確認のうえ `backlog/<id>.md` をゴミ箱へ移動。実行中（**doing かつクレーム中**）だけ拒否 — クレームロックは worker クラッシュや review/blocked 滞留で残骸が残るため、doing 以外ではロックがあっても削除でき、残骸ロックも一緒に掃除する。決定記録 DR は残らない — 記録を残したい場合は「保留（hold）」を使う |
| 🗑 run 削除 | フロータブの run 詳細 | 同上のファイル操作。確認のうえ `<bus>/runs/<run-id>/` をゴミ箱へ移動。終端（done/failed）と応答なし（孤児）のみ — orchestrator が生存している実行中 run は拒否 |

- 理由・方針の記入はすべて決定記録（`decisions/` の DR）や次 act への feedback として
  kiro-projects 側に残る
- 承認 / 保留の指示は needs ファイル自体を変えない（commands/ 経由）ため、送信後の
  カードは「**指示送信済み（取り込み待ち）**」表示になり操作ボタンを出さない
  （二重送信防止。ファイルパス + mtime で照合し、ファイルが書き換わったら解除）。
  revise も同様に、送信後はタスク行に ✎ バッジ・詳細に「修正指示送信済み」を出し、
  本体が取り込んでタスクファイルが書き換わるまで再送を防ぐ
- ファイル書き込み（needs / inbox / commands）は稼働中の kiro-projects の watch が自動で
  取り込む。**指示（承認/保留/優先度変更）は既定でファイルドロップ**（本体が WSL 内で
  稼働していても届く。CLI は不要）。届け方は ⚙ 設定「指示の届け方」で制御できる:
  auto（稼働中はファイル・停止中は CLI・CLI 不可ならファイルに退避）／file（常にファイル）／
  cli（常に CLI。PATH に無ければ `python3 /path/to/kiro-projects.py` 形式で指定）
- 入力中は自動更新を一時停止する（書きかけのフィードバックが消えない）

## エラー時の流れとビュアーの役割

kiro-flow でタスクが失敗したとき、どの層が何をし、人（ビュアー）はどこで関与するか。

```
ノード失敗（実行エラー / verify fail / gitlab 却下）
  │ kiro-flow 内で自動回復: 評価役が [retry] 置換ノードを追加して再実行
  │ 同一系統の作り直しが max_retries（既定 3）に達すると打ち切り
  ▼
run は done で終端（失敗ノードを含んだまま。run が failed になるのは
orchestrator の消失＝クラッシュ/シャットダウンで自動再開も尽きたときだけ）
  │ kiro-projects が結果を verify ゲートで検証 → NG なら retry
  │ （gitlab 却下 [gitlab-reject] は人コメントを feedback として次 act に注入）
  ▼
task の retries 上限 → blocked ＋ needs/<id>.md 生成 ＝ ここで初めて人の出番
  │
  ▼ ビュアーの「要対応」タブで方針を記入して再開 / 承認 / 保留
```

- **kiro-flow 単体では人は関与しない**（自動 retry → サーキットブレーカーで打ち切り）。
  人へのエスカレーションは kiro-projects の役割（needs/ 経由）で、ビュアーの
  「要対応」タブが対応窓口。
- **gitlab executor だけは実行中に人の判断を待つ**: タスクをイシュー化し、関連 MR の
  決着（全マージ＝承認 ／ 未マージクローズ＝却下）をポーリングする。ここでの
  ユーザーアクションは GitLab 上（MR のマージ/クローズ・イシューへのコメント）。
  - **承認**: イシューを status:done でクローズ → ノード done（`data` に issue_iid /
    web_url / decision / merged_mrs が残る）
  - **却下**: 人コメントを取り込み → イシューをクローズ → ノード failed。承認と対称の
    構造化データ（`data` に issue_iid / web_url / decision: rejected / reason /
    guidance（人コメント）/ merged_mrs）が残り、output にも
    `[gitlab-reject] …（イシュー URL）やり直し指示: <人コメント>` が残る（旧 run 互換）。
    kiro-projects 管理下ならコメントを feedback に注入して自動で再委譲される
- **ビュアーの役割**:
  | 場面 | 見る場所 | できる指示 |
  |------|---------|-----------|
  | ノードの進捗・失敗理由 | フロータブ → ノード詳細（経過・heartbeat・output・タイムライン） | — |
  | gitlab 委譲の判断待ち | ノード詳細の「関連イシュー」（実行中はタスクトークン検索）／レビュー待ちタブ | 「レビューで開く」→ gitlab-review-viewer で MR のマージ/差し戻し・コメント |
  | クローズ済みイシューが未反映 | RUN 概要の「⟳ GitLab と突き合わせ」 | 実行中ノードの関連イシューが GitLab で既にクローズ（承認/却下）済みなら、タスクグラフへ完了/失敗を先読み反映（下記） |
  | 却下されたタスク | ノード詳細（output の却下理由・イシューリンク） | イシューのコメントが次の act に効く（レビューで開いて記入） |
  | run 自体の失敗（orchestrator 消失・再開上限） | フロータブ run 詳細（失敗理由・自動再開回数） | 「↻ 同じ要求で再投入」（inbox へ新しい run として投入） |
  | retry が尽きて人待ち（blocked/review） | 要対応タブ（needs/） | フィードバックして再開・承認・保留 |

### クローズ済みイシューのタスクグラフ反映（GitLab と突き合わせ）

gitlab executor は「関連イシューがクローズされた（承認/却下で決着した）」ことを result で bus に
書くが、それを検知するのは worker が決着ループを回しているとき **だけ**。非ブロッキング委譲
（act_async）＋PC の日次停止などで worker が止まっている間に人がイシューを承認クローズすると、
bus に result が無いまま残り、タスクグラフはノードを「実行中」のまま表示してしまう（完了に
できない）。

RUN 概要の **「⟳ GitLab と突き合わせ」** を押すと、その run の非終端ノードの関連イシュー（本文の
決定的タスクトークンで検索）を GitLab の「今」の状態と突き合わせ、**クローズ済みなら executor と
同じ規則**（関連 MR の状態 → `status:approved`/`status:done` ラベル → 人コメントの承認/却下語。
手掛かり無しのクローズは取り下げ＝却下）で承認/却下を判定し、タスクグラフのノードを **完了/失敗
として先読み反映** する。反映されたノードは **破線枠**＋詳細の「GitLab 反映」チップで区別でき、
bus に result が届けば通常表示に確定する（bus が常に正で、反映は暫定表示）。GitLab の Base URL /
トークン（⚙ 設定）が必要。

## gitlab-review-viewer との連携（レビューの引き継ぎ）

レビュー待ちタブ（またはフロータブのノード詳細）の「**レビューで開く**」を押すと、そのイシューを gitlab-review-viewer で開く。

- 既定は **カスタム URL スキーム**（`protocol`）: `gitlab-review-viewer://open?url=<イシューの web_url>` を
  OS 経由で開く。gitlab-review-viewer 側はディープリンク対応済み（シングルインスタンス化
  されており、起動済みならそのウィンドウで対象イシュー + 関連 MR を開く。未起動なら起動する）。
  プロトコル登録はインストーラ（NSIS）版で行われる。
- **portable exe 版の gitlab-review-viewer では `protocol` は使えない**。portable はインストーラを
  通らず、起動ごとに一時ディレクトリへ展開されるため、カスタム URL スキームを OS に恒久登録できない
  （登録先が毎回消える一時パスになる）。この場合は ⚙ 設定 → 起動方法を **「実行ファイル直接」（`exe`）**
  にし、gitlab-review-viewer の実行ファイルパスを指定する。ディープリンク URL を実行ファイルへ
  argv として直接渡すため、プロトコル登録に依存せず連携起動できる（gitlab-review-viewer は
  `deepLinkFromArgv` / `second-instance` で受け取り、未起動でも起動済みでも同じ挙動になる）。
  - **起動済みなら exe を再起動せず即ハンドオフ**（`exe` モードの高速経路）: portable exe を
    argv 付きで再起動すると、既に起動済みでも OS が毎回「自己展開 → Electron 起動 →
    single-instance で argv 転送 → 即終了」の 2 個目プロセス立ち上げコスト（数秒）を必ず払う。
    これを避けるため、`exe` モードではまず gitlab-review-viewer が起動時に開く**ローカル IPC
    エンドポイント**（Windows: 名前付きパイプ／その他: Unix ドメインソケット。ユーザーごとに
    決定的な名前）へ接続を試み、**届けば URL を送るだけで即座に既存ウィンドウが対象を開く**
    （exe は spawn しない・トーストは「起動中の gitlab-review-viewer に引き継ぎました」）。
    接続に失敗した＝未起動のときだけ、従来どおり exe を argv 付きで起動する（＝cold start の
    ときにだけ自己展開コストを払う）。設定不要・自動。古い gitlab-review-viewer（エンドポイント
    非対応）が相手でも接続に失敗して従来の argv 起動へ素通りする（後方互換）。
- それ以外の任意起動が必要なら **コマンド起動**（`command`）:
  `"C:\Apps\GitLab Review Viewer.exe" "{protocolUrl}"`
  （`{url}` `{projectPath}` `{type}` `{iid}` に加え、組み立て済みディープリンク `{protocolUrl}` を置換）

逆方向として、本アプリ自身も `kiro-projects-viewer://open?root=<container>&project=<name>` の
ディープリンクを受け付ける（他ツールから特定プロジェクトのダッシュボードを直接開ける）。

## セットアップ

```bash
cd tools/kiro-projects-viewer
npm install
npm start                # 開発起動
npm run dist             # Windows 向けビルド（portable + NSIS → release/）
```

初回起動後、⚙ 設定で:

1. **コンテナのパス** を 1 行 1 つで登録（例 `C:\work\repo\.kiro-projects`）。
   kiro-projects が稼働していれば自動発見だけでも表示される。
2. （任意）**GitLab の Base URL / トークン**（read_api で十分）。イシューの最新状態
   （ラベル・関連 MR）の補完と、repos のイシュー一覧に使う。未設定でも bus 上の
   情報だけで動く。
3. （任意）自動更新間隔（既定 5 秒。0 で手動 ⟳ のみ）。

設定は `userData/config.json`（Windows: `%APPDATA%/kiro-projects-viewer/config.json`）に保存される。

## 実装メモ

- `src/main/kiro.js` … kiro-projects データ層。パース規則は kiro-projects.py の
  `HEAD_RE` / `FIELD_RE` / `parse_charter` / `parse_policy` と同じ（書式の正典は
  `tools/kiro-projects/backlog.md.example` / `charter.md.example`）
- `src/main/flow.js` … kiro-flow バスのリーダー。状態はファイル存在から導出
  （`results/` → done/failed、lease 内 `claims/` → claimed、依存未達 → waiting）。
  claim 勝者の決定的タイブレーク `(ts, who)` も kiro-flow 本体と同じ。
  run の生存判定は kiro-flow の `run_is_orphaned` と同じ導出（`orch_lease_until`
  のリース、無ければ `updated_at` の age）。daemon 稼働はロックパスの同一導出
  （`sha1("local::" + realpath(bus))`）＋記録 pid の生存確認（kiro-projects の
  fcntl 不在時フォールバックと同じ根拠）で、CLI を起動せずに判定する
- `src/main/toolconfig.js` … `.kiro/` の kiro-projects / kiro-flow 設定ファイルから
  `bus` / `lock_dir` などトップレベルのスカラだけを読む簡易リーダー
  （共有バス構成・ロック置き場の自動発見に使う）
- `src/main/gitlab.js` … GitLab REST v4 の読み取り専用クライアント（net.fetch・プロキシ対応）。
  実行中ノードの関連イシューは、gitlab executor と同一導出の決定的タスクトークン
  （`kf-<sha1(run_id/node_id)[:12]>`）でイシュー本文の隠しマーカーを検索して見つける
- `src/main/review.js` … gitlab-review-viewer へのレビュー引き継ぎ（protocol / exe / command）。
  exe は実行ファイルへディープリンクを argv 直渡し（portable exe 向け・プロトコル登録に依存しない）
- `src/main/actions.js` … 人のアクション層。needs 記入（Decision Outcome + `[x]`）・
  inbox JSON ドロップ・commands JSON ドロップ（approve/hold/pin/defer/revise。稼働していなければ
  CLI にフォールバック）の 3 契約のみを使う
- `src/main/authoring.js` … オーサリング層（新規作成・上位入力ファイルの編集）。
  charter.md の雛形生成（`buildCharter`）と repos.json 生成（`exportReposJson` は kiro-projects の
  `export_repo_registry` と同じ `_meta.generated_from` 付き・キーソート）、`<root>/projects/<name>/` への
  プロジェクト作成、charter/policy/repos のホワイトリスト読み書き（repos.json は JSON 構文検証）。
  **タスク状態は書かない** — actions.js と同じく done の不変条件を壊さない。archive タスクの
  「revise して再投入」は actions.js の inbox 契約をそのまま使う（新タスクとして verify を通す）
- IPC は gitlab-review-viewer と同じ `{ok, data|error}` 形式・`window.api` 公開

## 制限事項

- タスク本文の編集は「✎ 修正して指示（revise）」から公式契約（commands/）経由で行える
  （title・優先度・依存 after・verify・accept の置換とフィードバック注入）。それ以外の
  フィールドはファイルで編集する（詳細ダイアログから開ける）。
  状態遷移を直接書き換える操作は意図的に持たない（done は verify のみが根拠、の
  不変条件をアプリから壊さないため。revise も状態を書かず、本体側の同一ロジックが遷移を決める）。
  例外は 🗑 削除（タスク / run）のみ —
  削除の公式契約が無いため、確認ダイアログのうえゴミ箱への移動として行う
- **編集できるのは「人が書く上位入力」だけ**（charter.md / policy.md / repos.*）。これらは
  kiro-projects の入力ファイルなので、アプリから編集しても後段（backlog 生成・ルーティング）は
  本体の run が決定的に作り直す＝done の不変条件は保たれる。**タスク状態ファイル
  （`backlog/*.md`・`archive/*.md`・`project.json` 等）はアプリから書き換えない**。
  archive（done）タスクをやり直したいときは「↻ revise して再投入」で**新しいタスク**として
  inbox へ入れる（archive のファイルは消さず、verify を通して done を取り直す）
- 新規プロジェクト作成は charter.md を置くだけ（＋任意で repos.json）。plan / backlog 生成・
  acceptance 実行・収束判定は本体の run が行う（アプリは backlog を生成しない）
- approve / hold / reprioritize / revise は既定でファイルドロップ（`commands/`）のため CLI 不要。
  本体が稼働していないときだけ CLI を試み、CLI も使えなければ指示ファイルを置いて
  次回の kiro-projects 起動時の取り込みに委ねる（即時には反映されない）
- `bus/` は kiro-projects が local run 後に掃除するため（`--no-cleanup` で保持）、
  フロータブは稼働中の run が主対象
- kiro-flow の状態（run 一覧・生存・daemon 稼働）はすべてファイルから判定するため
  kiro-flow CLI は不要。ただし daemon 稼働の pid 判定は同一ホスト上でのみ有効
  （Windows のビュアーから WSL 内の daemon は temp 領域が別のため見えない — その
  場合も run の生存リースによる「応答なし」判定は共有バスのファイルだけで機能する）
- GitLab 書き込み操作は持たない（レビュー操作は gitlab-review-viewer の役割）
