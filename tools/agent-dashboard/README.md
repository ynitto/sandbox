# agent-dashboard

> 旧 `kiro-projects-viewer` 系統から移行した後継実装。
> 改称方針: [`docs/designs/agent-tools-rename-design.md`](../../docs/designs/agent-tools-rename-design.md)。

**複数の agent-project プロセスを束ねて可視化し、入出力を git 経由で編集・検収する GUI。**
各プロジェクト（1 プロジェクト = 1 ディレクトリ = 1 プロセス）の状態共有リポジトリの clone を
登録するだけで、Windows から WSL・別ホストで稼働する本体をドライブできる——上位からの指示・
確認・方針変更・一時停止/停止・回復（再分解・再投入）をこのアプリで完結する。
[gitlab-review-viewer](../gitlab-review-viewer/) と同じ構成（プレーン Electron・
ランタイム依存なし・main / preload / renderer の 3 層）で作られている。

**ソース構成（制御面分離）**: Electron シェル等の共通部は `src/base/`、
agent-project / agent-flow の制御は `src/features/agent-project/` に置き、
kiro-loop 制御（`src/features/kiro-loop/`）・定常業務（`src/features/cowork/`）・
agent-amigos ミッションとノード予算（`src/features/amigos/`）を同じ形で差し込んでいる
（動的プラグインではない。列挙合成のみ。詳細は
[`docs/designs/agent-dashboard-feature-split-design.md`](../../docs/designs/agent-dashboard-feature-split-design.md)）。

**Amigos タブ**（`src/features/amigos/`）: agent-amigos ミッションの進行
（phase / 名簿 / ミッション予算 / 未回答質問）を**読み取り専用**で一覧し、
**ノード予算**（[node-budget 契約](../../schemas/node-budget.schema.json) —
定常業務・プロジェクト・フロー・Amigos の実行時間合計に上限。0 = 無制限、
依頼側・請負側どちらのノードでも同じ）をワークロード別の消費内訳つきで表示・編集する。
ミッションも予算データも無いときはタブ自体を隠す。

**概要から詳細へ**: 最初の画面で「現在の状態／あなたの対応／進捗／成果」を把握し、
必要な箇所だけ「タスクを見る」「実行を見る」「成果を見る」から深掘りできる。
プロジェクト定義の編集やリセットは「プロジェクト設定」にまとめ、日常の確認画面から分離している。

```
┌ サイドバー ────────┐┌ メイン ─────────────────────────────────────────┐
│ プロジェクト        ││ 概要      charter / acceptance 達成状況 / 稼働操作 │
│  ● 稼働中 ⏸一時停止 ││ バックログ  タスク一覧（status / priority / verify）│
│  [needs] [tasks]    ││ 要対応     needs/（人の判断待ち・検収待ち）        │
│  （1 行 = 1 clone） ││ フロー     agent-flow run のタスクグラフ（DAG）     │
│                     ││ レビュー待ち repos のオープンイシュー → レビューへ │
│                     ││ 履歴      run-log / 決定記録 / 納品 / journal     │
└─────────────────────┘└──────────────────────────────────────────────┘
```

## 何が見えるか（データソース）

すべて **読み取り専用**。agent-project / agent-flow のファイルを直接読む
（両ツールの稼働は不要。稼働中なら自動更新で追従する）。

| タブ | データソース |
|------|-------------|
| 概要 | `charter.md`（goal / deliverables / acceptance）・`project.json`（acceptance PASS 履歴）・`backlog/` 集計・`policy.md`・`claims/`・`run-log.jsonl`・`DELIVERY.md`・`status.json`（daemon の生存信号。instances に無ければこちらへフォールバック） |
| バックログ | `backlog/<id>.md`（1 ファイル = 1 タスク。status / priority / verify / after 等）・`archive/<id>.md`（done） |
| 要対応 | `needs/<id>.md`（MADR 形式。blocked / review / milestone。「ファイルを開いて回答」でエディタへ） |
| フロー | `<bus>/runs/<run-id>/`（`graph.json` + `results/` + `claims/` + `waits/` からノード状態を導出し DAG を描画。`events/*.jsonl` のアクティビティ付き）。run のスナップショットはポーリングのたびに**プロジェクト配下の `flow-archive/<run-id>.json`** へ写し取り、掃除で bus から消えた run も「アーカイブ」として一覧・閲覧できる（プロジェクトのデータなのでリセットで一緒に消える。state_git の同期対象からは bus と同様に外れる）。gitlab executor の **park & poll**（承認待ちで worker スロットを解放し保留）に対応し、生存 lease を持つ `waits/<node>.json` を「**承認待ち（parked）**」ノード（オレンジ・レビュー中アイコン）として、同時イシュー上限での「起票見送り（throttle）」も区別して表示する（lease 失効は pending へ縮退＝本体と同じ）。`canceled` run も終端として正しく扱う（応答なしに誤分類しない）。バスは `<root>/bus` → `<root>/bus` → ⚙ 設定 → agent-project 設定ファイル（`.agent/`）の `bus:` の順に自動発見。run の生存（orchestrator 応答なし）は `meta.json` の生存リース（`orch_lease_until`）から、daemon の稼働はロックファイル（`$TMPDIR/agent-flow-locks/daemon-<sha1>.lock` の pid。同一ホストのみ）から、無ければ `<bus>/status.json`（生存信号。state_git 同期経由の推定）から判定 — **agent-flow CLI には一切聞かない**。ノード詳細では進捗（開始・経過・worker heartbeat/lease・所要・作り直し回数・claimed/result のタイムライン）と、gitlab executor の**関連イシュー**（承認は `data`、却下は output の URL、実行中は決定的タスクトークンの GitLab 検索）を表示し「レビューで開く」で gitlab-review-viewer へ引き継ぐ。run 表示ペインは**概要 / タスクグラフ / ノード情報**の縦 3 段に分かれ、各段が独立して縦スクロールする（グラフが縦に長くても概要・ノード詳細を見失わない） |
| レビュー待ち | `repos.json` の GitLab リポジトリのオープンイシュー＋関連 MR（API 設定時）。プロジェクトが扱うリポジトリの「いまレビュー待ち・作業中」を横断一覧し gitlab-review-viewer へ引き継ぐ。既定では **agent-flow 由来のイシュー**（gitlab executor が起票 = 本文の `task-token` マーカー）だけに絞る（「agent-flow 由来のみ」チップで解除可）。各行の **「関連 run」列**は、イシュー本文の `task-token` をロード済み run 一覧の各ノードの決定的トークンと突き合わせて起票元の run/ノードを特定し、クリックでフロー画面のその run・ノードを直接開く（イシュー URL は承認/却下まで bus に現れないため、レビュー待ち中の対応付けはこのトークン一致で行う。追加の API/走査コストは無し）。run/ノード単位の委譲イシューの決着（承認/却下）はフロータブのノード詳細が担当 |
| 履歴 | `run-log.jsonl`・`decisions/<id>.md`（DR）・`DELIVERY.md`・`journal.md` |

### 関係性のたどり（charter → backlog → run → issue）

タブ構成はそのままに、**どのタスクがどの run（GitLab イシュー）につながっているか**を可視化し、
クリックで関連画面へ遷移できる。鍵は agent-flow の決定的 run-id
`req-<backlogハッシュ>-<taskid>-r<retries>` — ここから紐づくバックログタスクとリトライ系統を復元する。

- **リトライは「意味的に同一」なので束ねる**: 同一タスクの `…-r0 / …-r1 / …` は 1 系統として
  フロー一覧にまとめ、最新試行を見出しに、過去の試行は色付きピル（`r0` `r1` …）で畳む。
  `--inherit-from` で先行 run を引き継いだ run には「↩ 引き継ぎ元」を併記する。
- **パンくず**（タスクダイアログ・run 詳細）: `🎯 charter ▸ 🗒 task ▸ ⚙ run(系統) ▸ 🔗 issue`。
  各セグメントはクリックで対応する画面へ飛ぶ（run→フロー、task→バックログ、issue→GitLab）。
- **相互リンク**: バックログ各行に関連 run バッジ `⚙N`（クリックでフローへ）、フロー一覧に
  タスクリンク `🗒 <taskid>`（クリックでバックログのタスクダイアログへ）、タスクダイアログに
  「関連する agent-flow run（リトライ系統）」一覧。

### ワークスペースとプロジェクトルート

登録するのは**ワークスペース** — `.agent/agent-project.yaml`（または直下の `agent-project.yaml`）を持つ
開発フォルダで、agent-project CLI を起動する場所（CLI から見た cwd）。人が普段開いているフォルダを
そのまま登録すればよい。

ビュアーはそこから設定の `root:`（相対はワークスペース基準）を読み、**プロジェクトルート** — 状態の
置き場 — を導く。`charter.md` / `backlog/` / `needs/` / `bus/` / `flow-archive/` はすべてこの直下で、
CLI の `--root`・`~/.agent-project/instances/*.json` の `root` と同じものを指す。承認・投入・リセット
などの操作はすべてプロジェクトルートを基準に行う。

```
/home/me/src/webapp           ← ワークスペース（これを登録する）
├── .agent/agent-project.yaml   ← root: .agent-project
└── .agent-project/            ← プロジェクトルート（状態の置き場）
    ├── charter.md  charters/  backlog/  needs/  decisions/
    └── bus/  flow-archive/
```

`root:` が無ければ登録したフォルダ自体がプロジェクトルート＝**状態フォルダを直接登録する従来の
使い方もそのまま動く**（instances 由来の自動発見もこの経路）。表示名はワークスペース名になるので、
`.agent-project` のような技術的なフォルダ名が一覧に出ることはない。

プロジェクトの発見は次の 2 系統:

1. **設定の roots** — ⚙ 設定「ワークスペース」に 1 行 1 つを登録。**ワークスペースでもプロジェクト
   でもないフォルダを登録すると「束ねる親フォルダ」とみなし、配下から `agent-project.yaml`（ルート
   直下 / `.agent/`。または charter.md / backlog/ 等のマーカー）を持つディレクトリを自動発見**して、
   それぞれ 1 件として一括追加する（探索の深さは設定 `projects.scanDepth`・既定 2 階層。プロジェクトと
   判定したディレクトリの配下はそれ以上掘らない）
2. **自動発見** — `~/.agent-project/instances/*.json`（稼働発見レコード）から稼働中プロジェクトを
   検出。heartbeat が新鮮なプロジェクトには ● 稼働中マークが付く（一時停止中は ⏸）

レイアウトはプロジェクトルート直下フラット（charter.md / backlog/ / needs/ … が直下）のみ。

### リモートで稼働する agent-project を見る（git 経由・一次経路）

本体（agent-project）は**プロジェクトルート自体を状態共有リポジトリの clone**として動かすのが
推奨構成（direct モード。本体側 README「状態の git 保存・共有」参照）。本体が状態を直接
コミット・push するので、viewer 側は:

1. 同じリポジトリを clone する
2. ⚙ 設定「ワークスペース」にその clone を登録する（clone 自体がプロジェクトルート＝状態フォルダ直指定。複数プロジェクト = 複数 clone を 1 行ずつ）

viewer の操作（needs 記入・commands/ ドロップ・inbox/ 投入・一時停止/停止）はファイルとして
書かれてコミット・push され（既定で「操作を都度コミットしてプッシュ」が有効）、本体側が idle の
pull で取り込んで次パスを起こす。指示の反映は同期間隔（本体側 `state_git_interval`・既定 300 秒）
ぶん遅れる。ルートが git でない構成（本体の管理クローン方式）でも、`state_git_subdir` の clone を
登録すれば同じように見える。

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
  - **反映先が git 作業ツリーかに注意**: push は「操作したディレクトリ」をコミットするため、
    そのディレクトリが **git 作業ツリーでない**と `commitPush` は `notRepo` で何もしない。本体の
    state_git が「作業ディレクトリ→別クローン」方式で同期する構成（ローカル daemon など）では
    作業ディレクトリ自体が git リポジトリでないため、**バックログ修正・タスク操作・needs 記入・
    run 削除など viewer からは直接 push できず**、daemon 側の state_git 同期に反映が委ねられる。
    この場合 viewer は「直接反映できなかった／daemon 同期に委ねられる」旨をトーストで知らせる
    （**ディレクトリごとに一度だけ**）。viewer から直接反映したいときは、**状態共有リポジトリの git
    クローン上でプロジェクトを開く**（バスは ⚙ 設定 `flowBusByProject` で `プロジェクト名 =
    <clone>/agent-flow` を登録）。git クローン上（pure-remote 構成）なら全操作がコミット・push される
- **多重コミッタとの共存**（本体の state_git / agent-flow GitBus と同じ護り）:
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

フロータブも同様: agent-flow 側の `state_git`（agent-flow README「状態の git 保存・共有」）で
ローカルバス（`runs/`・`inbox/`）が同じ共有リポジトリの別 subdir（既定 `agent-flow`）に同期される
ので、⚙ 設定（または `.agent/` の agent-flow 設定の `bus:`）でバスとして `<clone>/agent-flow` を
指すと、リモートの run の進捗/結果を DAG で追える（run の生存は meta の生存リース
`orch_lease_until` から従来どおり判定される。daemon 自体の稼働判定は下記参照）。

#### 複数プロジェクトを束ねる

プロジェクトごとに別々の状態リポジトリを使う構成（`default` は個人リポジトリ、`alpha` はチーム
共有リポジトリ等）でも、viewer は**各リポジトリの clone を 1 行ずつ登録するだけ**で全プロジェクトを
1 画面に束ねて見られる。使う人ごとにアサインされるプロジェクトが違っても、**自分がアクセスできる
リポジトリの clone を足すだけ**でドライブできる。

1. **ワークスペース**: 各 clone を ⚙ 設定「ワークスペース」に **1 行ずつ**追加登録する（clone 自体がプロジェクトルート）。
2. **フローバス**: agent-flow のバスは既定で `<root>/bus`。pure-remote 監視（ローカルに daemon が
   いない）でバスがリポジトリの `agent-flow/` 名前空間に鏡写しされる構成では、⚙ 設定
   「プロジェクト単位バス」に 1 行 1 件 `プロジェクト名 = <clone>/agent-flow` を書く
   （`kiro.flowBusByProject`）。ローカルの `<root>/bus` に `runs/` が実在するときはそちらが優先される。

指示の書き戻し（needs 記入・commands ドロップ・inbox 投入・一時停止/停止）は各プロジェクトの
clone へコミット／push され、そのプロジェクトを回している本体（担当者の daemon）が同期間隔内に
取り込む。

#### daemon の稼働判定（同期経由の推定）

本体が別ホストの場合、従来はサイドバーの ● 稼働中バッジも概要タブの実行状況も出せなかった
（`~/.agent-project/instances/` はローカルの生存レジストリで、同期対象に含まれないため）。
本体が `<root>/status.json`（生存信号。本体側 README「daemon の生存信号」参照）を書くように
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

#### フロータブの daemon 稼働判定（agent-flow・同期経由の推定）

agent-flow の daemon 稼働もロックファイル（`$TMPDIR/agent-flow-locks/`）判定は同一ホスト限定
——state_git（鏡）越しにバスを見ているときは daemon の一時領域に届かず、常に判定不能だった。
agent-flow 本体が `<bus>/status.json`（生存信号。agent-flow README「daemon の生存信号」参照）を
書くようになったため、フロータブの daemon バッジも同じ二段判定に対応する:

1. **ロックファイル**（同一ホスト・pid 生存）— 確定判定。従来どおり
2. **status.json**（同期経由）— ロックが無ければこちらにフォールバックし、`updated_iso` が
   `fresh_after_sec` 以内なら「稼働中（推定）」、超過なら「不明（同期経由）」と表示する
   （実行中の run 数・worker 数もツールチップに出す）

agent-project 側と同じトレードオフ: 既定はアイドル中の追加 git 負荷ゼロで、鮮度が要るなら
agent-flow daemon 側で `--status-interval` を指定する。GitBus（`--git`。バス自体を共有 git に
して実行を分散するモード）はこの機能の対象外（sparse-checkout が対象外パスになるため
daemon 側が書かない）——今のところ state_git（鏡）でリモートから run を眺める構成のみが対象。

### 気づく — 要対応の OS 通知（張り付き監視の解消）

ダッシュボードは既定 5 秒ポーリングの純プル型なので、画面を見ていないと新しい要対応
（人の判断待ち）に気づけない。これを補うため、**新しい要対応が現れたら OS 通知・タスクバー
バッジ・ウィンドウのフラッシュ**で知らせる（⚙ 設定「要対応が増えたら OS 通知で知らせる」・
既定 on）。

- **増分検知**: `discover()` の `needsCount`（サイドバーの要対応バッジと同じ数）を前回と
  突き合わせ、**観測済みプロジェクトで数が増えたときだけ**通知する。起動直後の既存分では
  通知しない（初回はベースライン取得のみ）。減少・新規発見でも通知しない。
- **騒音を出さない**: ウィンドウを見ている間はポップアップとフラッシュを抑制し、バッジ
  （未対応の総数）だけを更新する。
- **クリックで対象へ**: 通知をクリックすると窓を前面化し、既存のディープリンク
  （`agent-dashboard://open?root=…`）でそのプロジェクトを開く。
- **層**: base が汎用の通知プリミティブ（`app:notify` / `src/base/main/notify.js`）を提供し、
  「何を・なぜ通知するか」は agent-project の意味を知る renderer が決める。Windows は
  `setBadgeCount` 非対応のため通知とフラッシュで補う。

さらに、要対応タブでは各カードに**待ち時間バッジ**を出し、未対応を**滞留の長い順**に並べる。
needs の最終更新からの経過を「N 分/時間/日待ち」で表示し、SLA しきい値（⚙ 設定
「要対応 SLA」・既定 24h）を超えると赤、1/3 を超えると黄で**停滞**（人待ちで下流が止まっている
時間）を警告する。既定選択も最も停滞したカードになり、最優先の判断へ誘導する。

## 人のアクション（見るだけでなく、その場で判断を返せる）

agent-project の人間ループはこのアプリ内で完結できる。いずれも agent-project の
**公式な入力契約だけ**を使い、done の確定条件（verify のみが根拠）を迂回しない。

| 操作 | 場所 | 実装（入力契約） |
|------|------|-----------------|
| 表示を更新 | サイドバー ⟳ | この PC 上の状態ファイルだけを読み直す。共有先との通信は行わない |
| 共有先と同期／同期を修復 | ヘッダの同期状態の横（必要な場合だけ表示） | 取得・履歴合流・送信を一つの操作で行う。書き込み中でも一時 worktree でコミット済み履歴を隔離合流し、作業中ファイルには触れない。ローカル反映を待つ場合は赤い食い違いではなく黄色の反映待ちとして通知する。健康状態は作業ツリーを変更しない fetch で60秒ごとに再確認し、最終確認時刻または確認失敗を併記する |
| 実行前レビュー（承認・差し戻し・却下） | 要対応カード（kind=plan-review） | 新規タスクは承認されるまで実行されない（本体の plan_review・既定 on）。**承認**=`approve` 指示ドロップ（実行を許可）／**差し戻し**=修正指示を記入して `[x]`（agent-project がタスク定義を修正して再提案）／**却下**=`reject` 指示ドロップ（廃止・avoid 記録・依存タスクは再審査へ・charter があれば再計画）。却下の確認ダイアログに影響範囲（依存タスクの推移一覧）を表示 |
| 成果物レビュー（承認・差し戻し・却下） | 要対応カード（kind=review）／タスク詳細 | verify PASS 後の検収（本体の delivery_review・既定 on）。成果は ap/<task-id> ブランチに集約され、GitLab 設定時は MR が自動作成される（needs に URL）。**承認**=`approve`（本体がクリーンなら MR を自動マージして done 確定）／**差し戻し**=記入必須 `[x]`（同一ブランチに次試行）／**却下**=`reject`（MR クローズ＋ブランチ削除＋廃止＋再計画） |
| フィードバックして再開 | 要対応カード | `needs/<id>.md` の「## Decision Outcome」に記入 + `- [x]` 確定（`ingest_feedback` の正規ルート） |
| そのまま再実行 | 要対応カード（blocked） | 空記入で `- [x]` 確定 |
| 検証コマンドを変更して再実行 | 要対応詳細（blocked） | 関連タスクの現在の `verify` を表示し、変更後は既存の `revise`（`fields.verify`）で置換して新しい試行を開始する。タスク分解・依存関係・既存成果物は維持し、古い run は履歴に残す。送信前に旧／新コマンドと再実行範囲を確認する |
| 承認して done 確定 | 要対応カード（review / milestone） | `commands/<name>.json` ドロップ（`ingest_commands` が CLI と同一ロジック・同一 DR で実行）。稼働していなければ CLI にフォールバック |
| 差し戻す | 要対応カード（review） | 修正方針の記入必須 → feedback として確定（手戻り扱い） |
| 保留（hold） | 要対応カード・タスク詳細 | 同上（`{"command":"hold"}` ドロップ → policy.deny 追加） |
| 最優先へ / 後回し | タスク詳細 | 同上（`{"command":"pin"/"defer"}` ドロップ → policy 追記） |
| ✎ 修正して指示（revise） | タスク詳細（backlog）／要対応詳細（blocked の verify 変更） | 同上（`{"command":"revise"}` ドロップ）。タイトル・優先度・依存 after・verify・accept の**置換**とフィードバック注入。**実行中（doing）のタスクにも送れ**、本体は現在の試行の結果を確定せず（verify も done もせず）修正内容で積み直す＝気づいた時点の早い軌道修正。変更した項目だけが送られ、DR（`action: revise`）に記録される |
| ＋ バックログに追加 | バックログタブ | `inbox/<name>.json` ドロップ（E4 push 型取り込み口）で**バックログにタスクを 1 件追加**（本体が次サイクルで `backlog/<id>.md` にする）。verify / accept / priority / note / id / after 付き。ダイアログでは既存 backlog 一覧・先行タスク datalist に加え、「AIで依存・優先度を提案」で after/priority の下書きもできる（投入は人の「追加」） |
| AIで計画を批評 | 要対応（plan-review） | 読み取り専用 Doctor（`plan-critique`）。charter / 兄弟タスクとの整合を批評し、推薦と差し戻し文面案を返す |
| 変更理由を説明 / フォローアップ案 | 要対応（review）・検収ダイアログ | 読み取り専用（`delivery-rationale` / `followup-suggest`）。差分の意図説明と次タスク案。承認・inbox 投入は人が確定 |
| ↻ charter から再分解 | バックログタブ | `commands/<name>.json`（`{"command":"replan"}`・**プロジェクト単位＝id 無し**）ドロップで**バックログの再分解を要求**（`ingest_commands` が CLI `replan` と同一ロジック・同一 DR で実行）。本体が次パスで `charter.md` を分解し直し、取りこぼした差分だけを backlog に入れる。**既に done / 既存と類似のタスクは投入しない**（既存＋`archive/`（done）タイトルで冪等に重複排除）。plan 失敗・タスクの誤削除・取りこぼしなどの**エラー回復用途**。稼働していなければ CLI にフォールバック。要求中は「再分解 取り込み待ち」バッジを出しボタンを二重送信防止で無効化する（本体が再分解まで進めると解除）。状態（done 等）は書き換えない |
| ✎ タスクグラフを積み直す（after 含む revise） | タスク詳細（backlog のみ） | revise（`commands/`）で **依存 after** を含む項目（title / 優先度 / verify / accept / after / note / level / track）を置換。本体が取り込むと `rev` を上げ、agent-flow に**新しいタスクグラフ（run の DAG）**を作らせる（実行中タスクは現在の試行を破棄して積み直し）。after 編集は DAG 循環を本体側が拒否。状態（done 等）は書かない |
| ＋ 新規プロジェクト | サイドバー ＋（プロジェクトが無い空状態にも導線） | `<親フォルダ>/<名前>/charter.md`（goal / constraints / assumptions / deliverables / acceptance / repos をフォームから）と、repos があれば `repos.json`（`_meta.generated_from` 付き＝正は charter）を作成。以後は agent-project の run が charter から backlog を生成する（専用の作成コマンドは無く、charter を置くだけが公式手順）。作成したルートは設定 roots に追加して発見対象にする |
| ✨ charter の AI 下書き | 新規プロジェクト作成フォーム | goal・自由メモ（背景・要望の自然文）・書きかけの各欄をエージェント CLI に渡し、constraints / assumptions / deliverables / acceptance を**フォームへ下書き**する（→[charter 入力補助](#charter-入力補助ai-下書き補完)）。応答はフォームに流し込むだけで、ファイル作成は従来どおり「作成」ボタン（人の確定操作）のみ |
| ✨ charter の AI 補完・📋 雛形挿入 | 編集ダイアログ（charter.md / charters/&lt;name&gt;.md のみ） | 編集中の charter 全文をエージェント CLI に渡し、書式を保ったまま不足セクションの補完・acceptance のコマンド化・曖昧な記述の明確化をした**完成版に置き換える**（「↩ 補完前に戻す」で復元・保存するまでファイルには書かない）。雛形挿入は `## goal`〜`## links` のスケルトンを挿入（書きかけがあるときは確認してから置換）。ダイアログ上部にセクションの意味のガイドを常時表示 |
| バージョン（複数 charter）の一覧・編集 | 概要タブ「バージョン」／バックログの charter フィルタ | `charters/<名前>.md`（1 ファイル = 1 バージョン）を一覧し、バージョンごとの acceptance 達成状況・状態を表示。各 charter は「✎ 編集」から直接編集できる。バックログはタスクの `charter:` タグでバージョン別に絞り込める。新規プロジェクト作成でも charter 名（バージョン）を指定可能 |
| ✎ プロジェクトファイル編集 | 概要タブ「プロジェクトファイル」 | 人が書く**上位入力だけ**をアプリ内で直接編集: `charter.md`（最上位入力）／`policy.md`（運用ルール）／`repos.json`（レジストリ）。保存すると次の run で後段データ（backlog 生成・ルーティング）に反映される。repos.json が charter からの自動生成物（`_meta`）のときは「run 時に charter で上書きされる」旨を警告する。JSON は保存前に構文検証。タスク状態ファイル（`backlog/*.md` の status 等）は編集対象にしない — done の不変条件を壊さないため |
| ↻ revise して再投入 | タスク詳細（**archive のみ**） | archive（done）タスクの内容（title / verify / accept / priority / note / after / level / track）を prefill した投入フォームを開き、編集して `inbox/<name>.json` ドロップ。**新しいタスク**として triage→verify を通す（archive の記録はそのまま残る）。誤 done などの**エラー復帰用途**。元 ID を引き継ぐが衝突時は本体が採番し直す |
| レビュー操作（承認/差し戻し/コメント） | レビュー待ちタブ／フロータブのノード詳細 →「レビューで開く」 | gitlab-review-viewer へ引き継ぎ |
| 🗑 タスク削除 | タスク詳細（backlog のみ） | **例外的にファイル操作**（削除の公式契約が無いため）。確認のうえ `backlog/<id>.md` をゴミ箱へ移動。実行中（**doing かつクレーム中**）だけ拒否 — クレームロックは worker クラッシュや review/blocked 滞留で残骸が残るため、doing 以外ではロックがあっても削除でき、残骸ロックも一緒に掃除する。決定記録 DR は残らない — 記録を残したい場合は「保留（hold）」を使う |
| ■ run キャンセル | フロータブの run 詳細 | run を **canceled** に終端化する唯一の hard-stop。`inbox/cancels/<run-id>.json` に cancel マーカーを置き（git 同期で他 PC / daemon へ伝わる）、`meta.json` を canceled に確定し、`waits/`（承認待ち）を掃除して監視の再ポーリングを止める。**承認待ちで park 中の run も暴走中の run も止められる**。起票済みの GitLab イシューは残す（追跡だけやめる＝agent-flow の既定。イシュークローズは daemon の `cancel --close-issues` か gitlab-review-viewer に任せる — この viewer の GitLab クライアントは読み取り専用）。終端済み run には効かない（不可逆） |
| 🗑 run 削除 | フロータブの run 詳細 | 同上のファイル操作。確認のうえ `<bus>/runs/<run-id>/` をゴミ箱へ移動。終端（done/failed/canceled）と応答なし（孤児）のみ — orchestrator が生存している実行中 run は拒否 |
| ⏸ 一時停止 / ▶ 再開 | 概要タブ「稼働操作」 | `commands/<name>.json` ドロップ（`{"command":"pause"/"resume"}`・プロジェクト単位＝id 無し）。本体は `paused.json` を立てて watch の消化を止める（idle 監視・指示の取り込みは継続）。status.json の `paused` がサイドバー ⏸ とヘッダのバッジに出る |
| ⏹ 停止 | 概要タブ「稼働操作」 | 同上（`{"command":"stop"}`）。本体は状態を push してから graceful 停止する。**再開はプロジェクトのマシン（WSL 等）で `agent-project start`**（プロセス起動はファイル契約の外） |
| ⚠ リセット（charter 以外を全消去 + agent-flow 停止） | 概要タブ「危険な操作」 | プロジェクトを **charter からゼロにやり直す**危険操作。①バスの agent-flow daemon を停止（同一ホストのロック pid へ SIGTERM。別ホスト稼働は停止できない旨を報告）→ ②`charter.md` **以外**の全データ（backlog / archive / needs / decisions / journal / run-log / DELIVERY / inbox / commands / bus 直下 / flow-archive 等）をゴミ箱へ移動。charter が残るため、本体（agent-project）が稼働中なら次パスで charter から再分解して最初からやり直す。ドット始まりの同期内部（プロジェクトの `.state-git` と **バスの `bus/.state-git`**）は温存 — 管理クローンの manifest が残ることで削除が state_git 同期で「ローカルの削除」としてリモートへ伝播する（クローンごと消すと次の同期でリモートから旧データ・旧 run が**復活**してしまう）。charter.md が無いプロジェクトでは出さない（残すものが無く、プロジェクト削除になるため）。共有バス構成では daemon 停止が他プロジェクトにも影響する旨を確認ダイアログで警告する |

- 理由・方針の記入はすべて決定記録（`decisions/` の DR）や次 act への feedback として
  agent-project 側に残る
- 承認 / 保留の指示は needs ファイル自体を変えない（commands/ 経由）ため、送信後の
  カードは「**指示送信済み（取り込み待ち）**」表示になり操作ボタンを出さない
  （二重送信防止。ファイルパス + mtime で照合し、ファイルが書き換わったら解除）。
  revise も同様に、送信後はタスク行に ✎ バッジ・詳細に「修正指示送信済み」を出し、
  本体が取り込んでタスクファイルが書き換わるまで再送を防ぐ
- ファイル書き込み（needs / inbox / commands）は稼働中の agent-project の watch が自動で
  取り込む。**指示（承認/保留/優先度変更）は既定でファイルドロップ**（本体が WSL 内で
  稼働していても届く。CLI は不要）。届け方は ⚙ 設定「指示の届け方」で制御できる:
  auto（稼働中はファイル・停止中は CLI・CLI 不可ならファイルに退避）／file（常にファイル）／
  cli（常に CLI。PATH に無ければ `python3 /path/to/agent-project.py` 形式で指定）
- 入力中は自動更新を一時停止する（書きかけのフィードバックが消えない）

### Viewer アシスタント（AI 下書き・Doctor）

このビュアーは、共通設定で選んだ**エージェント CLI をヘッドレスで 1 回呼ぶ**補助機能を持つ
（`src/main/agent.js`）。同じ CLI とモデルを charter 入力補助と Doctor で共有する:

- **下書き（新規作成フォーム）** … goal か自由メモに一言書いて「✨ AI で下書き」。
  書きかけの全欄をプロンプトに渡し、JSON（goal / constraints / assumptions /
  deliverables / acceptance）で受けてフォームへ流し込む。既存の記入は尊重される。
- **補完（編集ダイアログ）** … charter 全文を渡し、書式（`# Charter:` と各 `## セクション`）を
  保った完成版全文で受けて**エディタの内容だけ**を置き換える（`## repos` は変更しない
  規約付き）。「↩ 補完前に戻す」でワンタッチ復元。
- **書き込みはしない** … エージェントの応答はテキストのみ。ファイルへの書き込みは
  従来どおり人の「作成」「保存」ボタンだけが行う（authoring 層の「人が書く上位入力
  だけを書く」護りを AI で迂回しない）。acceptance には「exit 0 = PASS のシェルコマンドを
  最優先、書けない条件のみ `accept: 自然文`」の規約をプロンプトで強制する。
- **Doctor（現在画面の相談）** … サイドバー上部の共通操作「AI相談」からダイアログを開く。
  補足文は任意で、空欄でも現在の画面について相談できる。プロジェクト選択中は現在のタブ、
  選択中の要対応・run・ノードと関連出力を渡し、未選択時はアプリ全体の状態を渡す。
  「現在起きていること」「次にすること」「判断の根拠」の順で助言を表示する。Doctor は
  読み取り専用モードで CLI を起動し、コマンド実行・ファイル編集・外部操作を許可しない。
  入力コンテキストは最大 120,000 文字。画面スナップショットは kiro では一時ファイルへ
  退避して `--trust-tools=fs_read`（読み取りのみ）で読ませる — kiro-cli は positional
  プロンプト併用時に stdin を読まないため（WSL プロジェクトではディストロの `/tmp` に
  書き、実行後に削除する）。claude 等の stdin を読む CLI へは従来どおり stdin で渡す。
- **計画批評** … 計画レビュー（plan-review）カードの「AIで計画を批評」。提案タスクを
  charter の goal/acceptance と兄弟 proposed タスクと突き合わせ、取りこぼし・重複・依存・
  推薦・差し戻し文面案を返す。文面案は「差し戻し文面を回答欄へ」でコピーできる（送信は人）。
- **検収の変更理由** … 検収カード／「検収物を確認」の「変更理由を説明」。差分と
  verify/accept/charter から「なぜ変えたか」・acceptance 対応・リスク・承認推薦を返す。
- **フォローアップ案** … 検収ダイアログの「フォローアップ案」。追加タスク案を JSON で提案し、
  「タスク追加フォームへ」で人が確認してから inbox 投入できる（自動投入しない）。
- **依存・優先度の提案** … タスク追加ダイアログの「AIで依存・優先度を提案」。既存 backlog を
  見ながら after / priority / note を下書きし、既存タスクへの調整案も示す。調整案は選択して
  「選択した調整を反映」で公式の revise として送る（人確認必須）。手動では先行タスク ID の
  datalist と既存 backlog 一覧も使える。
- **失敗出力の深掘り** … 要対応カードは最初に短い理由だけを示し、詳細画面の
  「出力全体を見る」で needs の原文と関連 run の全ノード出力・エラーを遅延読込する。
  画面表示側では省略しないため、概要から必要な失敗だけを深掘りできる。
- **検収物の差分確認** … 「検収物を確認」ダイアログの「すべての差分を表示」で、変更ファイル
  一覧が省略されていても書込先リポジトリの差分全体を一つのビューへまとめる。複数リポジトリは
  見出しで区切り、作業ブランチがある場合は base との差分、旧形式の未コミット成果は HEAD との
  差分を表示する。個別ファイルの「開く」に失敗した場合は理由を画面へ通知する。

**使う CLI とモデル**（⚙ 設定「Viewer アシスタント」）:

`kiro`（既定）/ `claude` / `copilot` / `codex` / `cursor` / `ollama` から選ぶ。
モデルは任意指定で、この設定は全プロジェクトの charter 補助と Doctor に共通して効く。
プロジェクトごとの `agent_cli` / `model` は Viewer の選択には使わない。

CLI が PATH に無い・タイムアウト（既定 180 秒・⚙ で変更可）の場合はトーストで通知して
フォームの内容はそのまま残る。

> 本体（agent-project / agent-flow）が run で使うエージェント・モデルの切替は、各プロジェクトの
> `agent-project.yaml` / `agent-flow.yaml` の `agent_cli` と `model:` で行う。
> Viewer アシスタントの設定とは独立している。

## エラー時の流れとビュアーの役割

agent-flow でタスクが失敗したとき、どの層が何をし、人（ビュアー）はどこで関与するか。

```
ノード失敗（実行エラー / verify fail / gitlab 却下）
  │ agent-flow 内で自動回復: 評価役が [retry] 置換ノードを追加して再実行
  │ 同一系統の作り直しが max_retries（既定 3）に達すると打ち切り
  ▼
run は done で終端（失敗ノードを含んだまま。run が failed になるのは
orchestrator の消失＝クラッシュ/シャットダウンで自動再開も尽きたときだけ）
  │ agent-project が結果を verify ゲートで検証 → NG なら retry
  │ （gitlab 却下 [gitlab-reject] は人コメントを feedback として次 act に注入）
  ▼
task の retries 上限 → blocked ＋ needs/<id>.md 生成 ＝ ここで初めて人の出番
  │
  ▼ ビュアーの「要対応」タブで方針を記入して再開 / 承認 / 保留
```

- **agent-flow 単体では人は関与しない**（自動 retry → サーキットブレーカーで打ち切り）。
  人へのエスカレーションは agent-project の役割（needs/ 経由）で、ビュアーの
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
    agent-project 管理下ならコメントを feedback に注入して自動で再委譲される
- **ビュアーの役割**:
  | 場面 | 見る場所 | できる指示 |
  |------|---------|-----------|
  | ノードの進捗・失敗理由 | フロータブ → ノード詳細（経過・heartbeat・output・タイムライン） | — |
  | gitlab 委譲の判断待ち | ノード詳細の「関連イシュー」（実行中はタスクトークン検索）／レビュー待ちタブ | 「レビューで開く」→ gitlab-review-viewer で MR のマージ/差し戻し・コメント |
  | クローズ済みイシューが未反映 | RUN 概要の「⟳ GitLab と突き合わせ」 | 実行中ノードの関連イシューが GitLab で既にクローズ（承認/却下）済みなら、タスクグラフへ完了/失敗を先読み反映（下記） |
  | 却下されたタスク | ノード詳細（output の却下理由・イシューリンク） | イシューのコメントが次の act に効く（レビューで開いて記入） |
  | run 自体の失敗（orchestrator 消失・再開上限） | フロータブ run 詳細（失敗理由・自動再開回数） | 「↻ 同じ要求で再投入」（inbox へ新しい run として投入） |
  | retry が尽きて人待ち（blocked/review） | 要対応タブ（needs/） | フィードバックして再開・承認・保留 |
  | plan 失敗・タスクの取りこぼし/誤削除 | バックログタブ | 「↻ charter から再分解」→ 本体が charter を分解し直し**取りこぼした差分だけ**を投入（done / 既存と類似は重複排除で入れない）。charter を編集しなくても再分解を一発で起こせる |

### クローズ済みイシューのタスクグラフ反映（GitLab と突き合わせ）

gitlab executor は「関連イシューがクローズされた（承認/却下で決着した）」ことを result で bus に
書くが、それを検知するのは worker が決着ループを回しているとき **だけ**。非ブロッキング委譲
（act_async）＋PC の日次停止などで worker が止まっている間に人がイシューを承認クローズすると、
bus に result が無いまま残り、タスクグラフはノードを「実行中」のまま表示してしまう（完了に
できない）。

その run の非終端ノードの関連イシュー（本文の決定的タスクトークンで検索）を GitLab の「今」の
状態と突き合わせ、タスクグラフのノードにイシュー状態を出す。

- **クローズ済み**: **executor と同じ規則**（関連 MR の状態 → `status:approved`/`status:done`
  ラベル → 人コメントの承認/却下語。手掛かり無しのクローズは取り下げ＝却下）で承認/却下を判定し、
  ノードを **完了/失敗として先読み反映** する。反映されたノードは **破線枠**＋詳細の「GitLab 反映」
  チップで区別でき、bus に result が届けば通常表示に確定する（bus が常に正で、反映は暫定表示）。
- **オープン中（レビュー待ち）**: ノードに「レビュー中」チップと**青系のイシューアイコン**を出す
  （状態は変えない＝実行中のまま。リンクから 1 クリックでレビューへ）。

**取得は自動**: run を開いたとき／ポーリング更新時に、GitLab 設定済みなら一度だけ自動で突き合わせる
（同一 run は **60 秒の律速**でキャッシュを使い、ポーリング毎回は API を叩かない。結果は run 単位で
キャッシュし、run を切り替えても保持）。RUN 概要の **「⟳ GitLab 最新化」** は手動の即時再取得用。
GitLab の Base URL / トークン（⚙ 設定）が未設定なら自動取得は走らない。追加の API 呼び出しは
非終端ノードのみ・最大 40 件・直列・60 秒律速で有界。

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

逆方向として、本アプリ自身も `agent-dashboard://open?root=<container>&project=<name>` の
ディープリンクを受け付ける（他ツールから特定プロジェクトのダッシュボードを直接開ける）。

## セットアップ

```bash
cd tools/agent-dashboard
npm install
npm start                # 開発起動
npm run dist             # Windows 向けビルド（portable + NSIS → release/）
```

初回起動後、⚙ 設定で:

1. **プロジェクトルート** を 1 行 1 つで登録（例 `C:\clones\payments`＝状態共有リポジトリの clone）。
   agent-project が稼働していれば自動発見だけでも表示される。
2. （任意）**GitLab の Base URL / トークン**（read_api で十分）。イシューの最新状態
   （ラベル・関連 MR）の補完と、repos のイシュー一覧に使う。未設定でも bus 上の
   情報だけで動く。
3. （任意）自動更新間隔（既定 5 秒。0 で手動 ⟳ のみ）。
4. （任意）**Viewer アシスタント**: charter の AI 下書き・補完と Doctor に使う共通の
   エージェント CLI（kiro〔既定〕／claude／copilot／codex／cursor／ollama）、モデル、
   タイムアウト（→[Viewer アシスタント](#viewer-アシスタントai-下書きdoctor)）。

設定は `userData/config.json`（Windows: `%APPDATA%/agent-dashboard/config.json`）に保存される。

## 実装メモ

- `src/main/project.js` … agent-project データ層。パース規則は agent-project.py の
  `HEAD_RE` / `FIELD_RE` / `parse_charter` / `parse_policy` と同じ（書式の正典は
  `tools/agent-project/backlog.md.example` / `charter.md.example`）
- `src/main/flow.js` … agent-flow バスのリーダー。状態はファイル存在から導出
  （`results/` → done/failed、lease 内 `claims/` → claimed、依存未達 → waiting）。
  claim 勝者の決定的タイブレーク `(ts, who)` も agent-flow 本体と同じ。
  run の生存判定は agent-flow の `run_is_orphaned` と同じ導出（`orch_lease_until`
  のリース、無ければ `updated_at` の age）。daemon 稼働はロックパスの同一導出
  （`sha1("local::" + realpath(bus))`）＋記録 pid の生存確認（agent-project の
  fcntl 不在時フォールバックと同じ根拠）で、CLI を起動せずに判定する
- `src/main/toolconfig.js` … `.agent/` の agent-project / agent-flow 設定ファイルから
  `bus` / `lock_dir` などトップレベルのスカラだけを読む簡易リーダー
  （共有バス構成・ロック置き場の自動発見に使う）
- `src/main/agent.js` … charter 入力補助と Doctor のエージェント CLI 連携層。
  共通設定から 6 CLI を解決し、Doctor は各 CLI の読み取り専用・ツール無効モードで起動する。
  応答のパース（JSON 抽出・コードフェンス剥がし）まで。ファイルは書かない
- `src/main/gitlab.js` … GitLab REST v4 の読み取り専用クライアント（net.fetch・プロキシ対応）。
  実行中ノードの関連イシューは、gitlab executor と同一導出の決定的タスクトークン
  （`kf-<sha1(run_id/node_id)[:12]>`）でイシュー本文の隠しマーカーを検索して見つける
- `src/main/review.js` … gitlab-review-viewer へのレビュー引き継ぎ（protocol / exe / command）。
  exe は実行ファイルへディープリンクを argv 直渡し（portable exe 向け・プロトコル登録に依存しない）
- `src/main/actions.js` … 人のアクション層。needs 記入（Decision Outcome + `[x]`）・
  inbox JSON ドロップ・commands JSON ドロップ（approve/hold/pin/defer/revise。稼働していなければ
  CLI にフォールバック）の 3 契約のみを使う。`requestReplan` は charter からのバックログ再分解を
  `commands/`（`{"command":"replan"}`・id 無し）／CLI `replan` で要求する（エラー回復。本体が
  既存＋archive（done）タイトルで重複排除するので done と類似は投入されない）
- `src/main/authoring.js` … オーサリング層（新規作成・上位入力ファイルの編集）。
  charter.md の雛形生成（`buildCharter`）と repos.json 生成（`exportReposJson` は agent-project の
  `export_repo_registry` と同じ `_meta.generated_from` 付き・キーソート）、`<親フォルダ>/<名前>/` への
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
  agent-project の入力ファイルなので、アプリから編集しても後段（backlog 生成・ルーティング）は
  本体の run が決定的に作り直す＝done の不変条件は保たれる。**タスク状態ファイル
  （`backlog/*.md`・`archive/*.md`・`project.json` 等）はアプリから書き換えない**。
  archive（done）タスクをやり直したいときは「↻ revise して再投入」で**新しいタスク**として
  inbox へ入れる（archive のファイルは消さず、verify を通して done を取り直す）
- 新規プロジェクト作成は charter.md を置くだけ（＋任意で repos.json）。plan / backlog 生成・
  acceptance 実行・収束判定は本体の run が行う（アプリは backlog を生成しない）
- approve / hold / reprioritize / revise は既定でファイルドロップ（`commands/`）のため CLI 不要。
  本体が稼働していないときだけ CLI を試み、CLI も使えなければ指示ファイルを置いて
  次回の agent-project 起動時の取り込みに委ねる（即時には反映されない）
- `bus/` は agent-project が local run 後に掃除するため（`--no-cleanup` で保持）、
  フロータブは稼働中の run が主対象
- agent-flow の状態（run 一覧・生存・daemon 稼働）はすべてファイルから判定するため
  agent-flow CLI は不要。ただし daemon 稼働の pid 判定は同一ホスト上でのみ有効
  （Windows のビュアーから WSL 内の daemon は temp 領域が別のため見えない — その
  場合も run の生存リースによる「応答なし」判定は共有バスのファイルだけで機能する）
- GitLab 書き込み操作は持たない（レビュー操作は gitlab-review-viewer の役割）
