# agent-amigos — 役割駆動マルチエージェント協働ツール 設計書

> 作成 2026-07-17 ／ 最終照合 2026-07-26（実装 `tools/agent-amigos/` と突き合わせ済み）
> 実装: `tools/agent-amigos/`（`agent-amigos.py` ＋ `agent_amigos/` パッケージ、テスト 176 件）
> 正典スキーマ: [`schemas/mission.schema.json`](../../schemas/mission.schema.json) /
> [`schemas/delivery.schema.json`](../../schemas/delivery.schema.json) /
> [`schemas/amigos-command.schema.json`](../../schemas/amigos-command.schema.json)
> 前提とする設計: [`agent-flow-design.md`](./agent-flow-design.md)（バス抽象・claim プロトコル）,
> [`agent-cli-plugin-design.md`](./agent-cli-plugin-design.md)（LLM 実行 CLI の共通契約）,
> [`agent-project-design.md`](./agent-project-design.md)（PC 1 本の常駐体と板の請負）

---

## 1. TL;DR

**何を作るか**: 役割ミッション表で公示した 1 つの成果物を、分散ノードがロールを引き受けて
型付きメッセージで協働しながら仕上げ、オーナーへ納品する基盤。

**主要な決定**:

1. バスはただのファイル空間で、担当決めも状態判定も各ノードが決定的に導く。中央は転送だけを担う。
2. 役割分担は人（または前段の team-builder）が先に決める。実行中に仕事を分解し直すことはしない。
3. 予算は wall-clock ではなく agent CLI の実質実行時間で数え、依頼側（ミッション）と請負側（ノード）の二層で持つ。

**却下した主要案**: 専用の中継サーバ（hub）を協働の土台にする案。一度実装したうえで、
常駐プロセスを PC に 1 本へ集約した時点で公開元を失い、撤去した（§3.1）。

**読むべき人**: agent-amigos を運用する人、ロールミッション表を書く人、
dashboard や常駐体から amigos を叩く実装者。
**読まなくてよい人**: タスクを機械的に分割して並列実行したいだけの人（それは
[agent-flow](./agent-flow-design.md) の領分。§2 の住み分けだけ読めばよい）。

---

## 2. 背景と課題

エージェント CLI を複数走らせる基盤は社内に既に 2 つある。agent-project は 1 プロジェクトの
バックログを回し、agent-flow は実行時に LLM がタスクグラフを組んで匿名ワーカーへ配る。
どちらも仕事を**分解して配る**形で、ワーカー同士は口をきかない。結果ファイルを置いて終わりだ。

設計判断やレビューの往復が要る仕事は、これで詰まる。API の実装者が仕様の穴に気づいても、
アーキテクトに聞く経路が無い。レビュアーの指摘を実装へ戻す経路も無い。人間のチームなら
数往復で片づく話が、全部オーナーの手作業になる。

そこで**チームを組んで作る**側の基盤を別に用意する。役割を先に決め、役割同士が質問・回答・
レビュー・決定をやり取りしながら 1 つの成果物へ収束させる。これが agent-amigos だ。

| 観点 | agent-flow | agent-amigos |
|---|---|---|
| 分解の主体 | LLM（実行時） | 人（役割ミッション表） |
| 実行単位 | 使い捨てタスク | 継続するロール |
| 通信 | 結果ファイルの受け渡し | 型付きの相互メッセージ |
| 向く仕事 | 分割統治できる一括処理 | 設計判断とレビューの往復が要る成果物づくり |

両者は補完関係にある。amigo が自分のミッションの中で `agent-flow submit` を呼び、大量の
並列作業を外注するのは想定内（例: API 実装ロールが 30 エンドポイントの雛形生成を投げる）。
逆向き（flow のタスクから amigos を起こす）は複雑さに見合わないのでやらない。

### 2.1 目標と非目標

先に範囲の外側を確定させる。**やらないこと**:

- **タスクグラフの動的生成**。仕事の構造は公示時点で決まっている前提を崩さない。
- **リアルタイムチャットの即時性**。1 往復はバスの同期間隔の 2 倍（GitBus の既定 pull 15 秒なら
  30 秒前後）。会話の粒度は質問と回答、レビュー依頼と指摘であって、チャットではない。
- **オーナーのフェイルオーバーと多重オーナー**。オーナーは単一障害点だが、状態はバスに残るので
  復帰すれば続きから進む。
- **インターネット越しのフェデレーション**。オンプレ限定。
- **人格の永続化**。amigo はミッション限りの実体。長期記憶が要るならロールのミッション文で
  ltm-use を指示する。

**やること**は次の 5 つで、いずれも後続の節で実現方法を示す。

1. 役割とミッションを与えた複数エージェントが、相互に会話しながら 1 つの成果物へ収束する。
2. 複数 PC に分散でき、しかも 1 ノードだけでも完結する。
3. オーナーが宣言した収束条件と予算の範囲で自律的に止まる。
4. ノードが毎晩シャットダウンしても担当が続く。
5. kiro / claude / copilot / codex / cursor など、どのエージェント CLI でも amigo を演じられる。

---

## 3. 主要な設計判断

ここが本文の核。他の選び方もあり得たのにこう決めた、という判断は次の 5 件だけで、
残りの節はここから機械的に導かれる。

### 3.1 中央は転送だけ。調整はしない（hub サーバを撤去）

**判断**: ミッションの全状態をバス上のファイルに置き、担当決めも状態判定も各ノードが同じ
入力から同じ答えを導く。中央（git リモート）は保管と転送しか担わない。

**文脈**: 参加ノードは社内 PC で、夜間に落ちる。中央サーバを立てるとそこが単一障害点になり、
運用も 1 つ増える。一方で agent-flow が既に「ファイル＋決定的アルゴリズム」で複数ノードの
タスク分配を回しており、実績がある。

**選択肢と却下理由**:

- *専用の中継サーバ（HubBus / `agent-amigos hub`）*: HTTP long-poll でメッセージ往復を秒オーダーに
  詰められる。**一度実装した**が、常駐プロセスを PC に 1 本（`agent-project serve`）へ集約した
  際に公開元だった `agent-amigos serve` が消え、対向だけ残しても繋ぐ先が無くなったので撤去した。
  `--bus hub+<url>` は移行案内を出して終了する。
- *成果物リポジトリの subdir を間借りする（agent-flow 方式）*: 新しいリポジトリを作らずに済む。
  却下。ミッションの会話はコミット頻度が高く、成果物リポジトリの履歴を汚す。バス（調整と会話）と
  成果物（コード）は最初からリポジトリで分ける。
- *state_git 方式の 3-way 裁定*: 却下ではなく**不要化**した。書き込み所有権をパス単位で
  分割し（§4.4）、ミッションごとにブランチを分けたので、同一ファイルの同時変更が起きない。
  rebase だけで足りる。

**トレードオフ**: 会話の往復が同期間隔に律速される。チャットの即時性は諦めた（§2.1）。
見直しの引き金は「往復レイテンシがミッションの完了時間を支配し始めたとき」。

**確信度**: 高い。1 ノード運用から複数ノードまで同一コードパスで動くことがテストで固定されている。

### 3.2 役割分担は先に決める。自動化するなら前段で（動的分解を却下）

**判断**: ロール構成はミッション公示の時点で確定する。実行中に仕事を分解し直す機構は持たない。
ロール設計を自動化したい場合は、公示の**前段**に team-builder を挟む（§7）。

**文脈**: 「LLM に役割まで考えさせる」は魅力的に見えるが、それは agent-flow が既にやっている
動的タスクグラフの再発明になる。二重発明は避けたい。

**選択肢と却下理由**: *実行時にロールを生成する案*は却下。ただし完全に閉じたわけではなく、
オプトインの自律コンダクタ（`mission.conductor`）が、実行中に不足ロールの追加と
機能していないロールの停止だけを行える（§7.3）。既定は off で、暴走止めとして
ラウンド律速・総操作数上限・ガードレールを噛ませてある。

**トレードオフ**: 役割表を書く手間が人に残る。それを埋めるのが §7 の build-team。

**確信度**: 中。conductor をどこまで働かせるかは運用実績が要る。

### 3.3 LLM はバスに直接書かない。ランナーが代書する

**判断**: エージェント CLI の出力は**アクション封筒**（実行したい操作の配列）として受け取り、
ランナーが検証してからバスへ書く。LLM にファイルを触らせない。

**文脈**: バスの書き込み規律（誰がどのパスを書いてよいか）は、破られると git バスで
コンフリクトが起き、他ノードの成果を壊す。これをプロンプトの言い聞かせで守らせるのは無理だ。

**選択肢と却下理由**: *プロンプトで規律を伝え、LLM に直接書かせる案*は却下。壊れ方が
「気づいたら他人の成果物が消えている」になる。代書にすると壊れ方が「不正アクションの棄却」
という観測可能なイベントに閉じ込められ、events に残って次ターンで LLM へ差し戻せる。

**トレードオフ**: LLM ができることが封筒の 4 種（`send` / `write_artifact` / `update_status` /
`declare_done`）に限られる。任意のシェルコマンドを走らせたいロールは、CLI プラグイン側の
権限で（つまり amigos の外で）やることになる。

**確信度**: 高い。

### 3.4 予算は実質実行時間。依頼側と請負側の二層で持つ

**判断**: 予算の単位を wall-clock ではなく **agent CLI の実行秒の総和**にする。そのうえで、
ミッション予算（依頼側がバスに宣言）とノード予算（請負側がローカルに持つ上限）を独立に持つ。

**文脈**: ノードは毎晩落ちる。wall-clock で 120 分と宣言すると、PC が落ちていた 12 時間で
予算が溶ける。さらに、1 台の PC で LLM を食うのは amigos だけではない。定常業務・
agent-project・agent-flow が同じ CLI 資源を奪い合う。

**選択肢と却下理由**:

- *wall-clock の締切だけ*: 却下。不在時間が予算になる。ただし wall-clock の締切が要る場面も
  あるので `deadline` を併記でき、超過はオーナーへの通知になる（自動 fail はしない。§8.2）。
- *中央の課金台帳*: 却下。集計プロセスが単一障害点になる。各 amigo がターンごとに
  `events/<who>.jsonl` へ `cli_seconds` を追記すれば、消費合計は「バス上の全 events の総和」で、
  誰が計算しても同じ値になる。
- *ノード予算をミッション予算に統合する*: 却下。上限の持ち主が違う。依頼側は「この仕事に
  いくらまで」、請負側は「この PC で合計いくらまで」を言いたい。前者はバス、後者は
  ツール横断の共有台帳（`schemas/node-budget.schema.json`）に置く。

**トレードオフ**: 二重帳簿になる。ただし対象が違うので混ざらない。ノード予算超過は
そのノードの amigo を paused にするだけで、ミッションは他ノードで進む。

**確信度**: 高い。

### 3.5 計画停止はクラッシュと区別する（away プロトコル）

**判断**: SIGTERM を受けたランナーは全 amigo を `state: away`（`resume_at` 付き）にして最後の
push をしてから終わる。away の間は lease が切れてもロールを奪わない。

**文脈**: 社内 PC の定時シャットダウンが日常運用の前提。lease 失効を一律「死んだ」と読むと、
毎晩ロールが再募集され、毎朝引き継ぎのやり直しになる。

**選択肢と却下理由**: *lease だけで判断する案*は却下。会話の文脈を持つ本人が翌朝続きから
やるほうが、引き継ぎより明らかに安い。ただし無限には待てないので、`resume_at` + grace
（既定 2 時間）を超えたら通常の再募集へ戻す。オーナーが待てないと判断して roster から外す
経路も残す。

これを成立させる前提が**ターンの原子性**だ。1 ターンの成果（封筒の適用、events 追記、
status 更新）を単一コミットにまとめてから push するので、任意のタイミングで電源が落ちても、
バスには「ターン全部」か「何もなし」しか残らない。やり直すのは高々 1 ターン。

**トレードオフ**: away 宣言を書けずに落ちた場合（強制電源断）はクラッシュ扱いになる。
そのために引き継ぎメモを毎ターン status へ書いておき、前ターン時点のメモは必ず残す。

**確信度**: 高い。テストで lease 失効時の away 保持と grace 超過後の再募集を固定してある。

---

## 4. 全体像

この節は概要の粒度。実装の詳細は §5 以降。

```
                 ┌──── 共有バス（ローカル dir / 専用 git リポジトリ）────────────────┐
 owner: post ──▶ │ missions/<mid>/                                                  │
                 │   mission.json + design-doc.md + roles/   … 公示（オーナーが書く） │
                 │   assignments/<role>/<who>.json           … 担当の claim          │
                 │   channels/ + inbox/<role>/               … エージェント間の会話   │
                 │   artifacts/<role>/ + deliverable/        … 成果物                │
 owner: accept ◀─│   final.json                              … 受入 → 納品棚へ搬出   │
                 └──▲──────────────────▲──────────────────▲─────────────────────────┘
          pull/push│          pull/push│          pull/push│
   ┌───────────────┴───┐  ┌────────────┴──────┐  ┌─────────┴─────────┐
   │ owner node (PC-A)  │  │ node PC-B         │  │ node PC-C         │
   │  ├ オーナー職務     │  │  └ amigo: impl-api│  │  ├ amigo: reviewer│
   │  └ amigo: architect│  │    (codex)        │  │  └ amigo: qa      │
   │    (claude)        │  │                   │  │    (kiro)         │
   └────────────────────┘  └───────────────────┘  └───────────────────┘
```

用語は 4 つだけ覚えればいい。**ミッション**は 1 つの成果物を作る協働の単位で、
ID は `am-<UTC タイムスタンプ>-<乱数 4 桁>`。**ロール**は役割ミッション表の 1 行。
**amigo** はあるロールを引き受けたエージェント実体で、`<node-id>--<role-id>` で一意になる。
**バス**はミッションの全状態が置かれるファイル空間で、真実は常にここにあり、プロセスは
ステートレスに保つ。

### 4.1 ミッションのライフサイクル

状態は専用フィールドを持たず、**ファイルの存在から導出**する（`derive_phase`）。
書き換え競合を設計段階で消すためだ。

```
 open（募集中）──必須ロール充足──▶ working ──収束──▶ integrating ──▶ reviewing
                                     │                                │accept  │reject
                                     │ cancel                         ▼        ▼
                                     ▼                              done    working へ差し戻し
                                 cancelled                                  （フィードバック付き）
```

終端は `done` / `cancelled` / `failed` の 3 つ。`failed` になるのは、予算が尽きて
`on_exhausted: fail` が指定されているときだけ。**done を作れるのはオーナーの accept だけ**という
不変条件は agent-project から引き継いでいる。差し戻しは `rejections/` にファイルを 1 つ増やし、
その件数がそのままラウンド番号になる。旧ラウンドの完了宣言は自動的に無効になる。

### 4.2 バスのレイアウト

```
<bus>/missions/<mission-id>/
  mission.json               # 公示本体（オーナーのみ書く）
  design-doc.md              # 設計書。改訂はオーナー経由
  roles/<role-id>.json       # 役割ミッション表の 1 行 = 1 ファイル
  assignments/<role-id>/<who>.json   # 担当の claim（応募者が自分名義ファイルだけ書く）
  roster.json                # 確定名簿（オーナーのみ書く）
  status/<who>.json          # amigo の自己申告状態・心拍・引き継ぎメモ
  channels/all/<who>/<ulid>.json     # 全体チャンネル（送信者の名前空間へ追記）
  inbox/<role-id>/<ulid>-<from>.json # ロール宛メッセージ（送信者が書く）
  artifacts/<role-id>/…      # 各ロールの成果物
  decisions.jsonl            # 決定記録（オーナーのみ追記）
  rejections/<NNNN>.json     # 差し戻し。件数 = ラウンド番号
  pruned/<role-id>.json      # 実行中に停止したロールの印
  conductor.json             # 自律コンダクタの評価状態
  deliverable/…              # 統合成果物（integrator のみ書く）
  final.json / cancelled.json # 受入 / 中止の記録（オーナーのみ書く）
  events/<who>.jsonl         # 追記専用の監査ログ。予算会計の原本
```

公示は正規化 **JSON** で置く。オーナーの入力形式は YAML でよいが、post の時点で変換する。
読み手に PyYAML を要求しないための割り切りだ。

### 4.3 書き込み所有権

git バスでコンフリクトを起こさないよう、書き込み権をパス単位で分割する。この表が
§3.1 の「3-way 裁定は不要」の根拠になっている。

| パス | 書く人 |
|---|---|
| `mission.json` / `design-doc.md` / `roles/*` / `roster.json` / `decisions.jsonl` / `rejections/*` / `pruned/*` / `final.json` / `cancelled.json` | オーナーのみ |
| `assignments/<role>/<who>.json` | 応募する各ノード（ファイル名が自分なので衝突しない） |
| `status/<who>.json` / `events/<who>.jsonl` / `channels/all/<who>/*` | 各 amigo が自分名義の分だけ |
| `inbox/<role>/<ulid>-<from>.json` | 送信者（ulid と送信者名で衝突しない） |
| `artifacts/<role>/*` | そのロールの確定 amigo のみ |
| `deliverable/*` | integrator のみ |

既読フラグは**バスに書かない**。各 amigo が自分の status にカーソル（最後に見た ulid）を持つだけ。
書き換え競合を作る余地を最初から与えない。

### 4.4 転送層

協働ロジックは転送層に依存しない。`Bus` 抽象の `sync_pull()` / `sync_push()` の裏に 2 実装がある。

| 実装 | 転送 | 想定 |
|---|---|---|
| `LocalBus` | no-op（同一ディレクトリ） | 1 マシン。テストと単機運用 |
| `GitBus`（`git+<url>`） | `pull --rebase` / `add`＋`commit`＋`push` | 複数ノード分散 |

GitBus はオンプレ git リモート（[ローカル GitLab](./plan-a-local-gitlab-design.md) / Gitea /
ssh の bare repo）に**専用のバスリポジトリ**を切って使う。`main` には公示インデックス
（`index/<mid>.json`）だけを置き、ミッション本体は `mission/<mid>` ブランチに分離する。
参加ノードは `main` を軽く poll して募集を見つけ、引き受けたミッションのブランチだけ clone する。
ミッション間で履歴も同期コストもコンフリクトも交差しない。gc はブランチ削除で済む。

同期の作法は agent-project / agent-flow の state_git から流用した。pull は間隔律速（既定 15 秒。
ただし claim の勝者確認だけは常に最新化する。鮮度がプロトコルの正しさに効くのはそこだけだから）。
push 競合は `pull --rebase` からの指数バックオフで、force push はしない。転送の実体は
`agentcore.transport.GitTransport` で、3 エンジンが同じ実装を共有している。

各ノードは自分専用のクローンを持つので、ローカルの変更はすべて自プロセス由来になる。
だからステージは `add -A` でよい。state_git の「自 subdir のみステージ」と同じ安全性が、
クローン分離によって成立している。

---

## 5. 協働のプロトコル

この節はコンポーネントの粒度。公示から納品までを時系列で追う。

### 5.1 公示・応募・決定的な担当決め

オーナーが `post --design <md> --roles <yaml>` を叩くと `missions/<mid>/` 一式が書かれ、
状態は open になる。参加ノードは自分の能力宣言（設定ファイルの `tags` / `agent_cli` /
`repos`）とロール要件（`requires.tags` / `requires.cli` / `requires.repos`）を突き合わせ、
合うロールへ応募する。応募は `assignments/<role-id>/<node>.json` に**自分名義のファイルを
書くだけ**なので、add/add コンフリクトが起きない。

勝者は、lease 内の全 claim のうち **`(ts, node)` 昇順の先頭 1 件**に決定的に定まる。全ノードが
同じ集合から同じ勝者を導くので、ローカルでも git でも二重アサインが起きない。claim と lease の
実体は `agentcore.protocol` にあり、agent-flow のタスク claim・委譲板の入札と同じ仕様を共有する。

`assignment_policy` で確定のしかたを選ぶ。`first-come`（既定）は claim 勝者がそのまま確定で、
オーナーは結果を `roster.json` へ鏡写しするだけ（表示と監査のため）。`owner-picks` では claim が
応募止まりになり、オーナーが `assign` で書いた者だけが確定する。

### 5.2 自己補充による 1 ノード完結

`staffing_timeout`（既定 600 秒）を過ぎても必須ロールが埋まらない場合、`staffing_policy` に従う。
既定の `self-staff` では、**オーナーノードが未充足ロールの amigo をローカルに立てて claim する**。
参加ノードが 0 でもミッションは必ず進む。これが「1 ノードでも完結する」の実体だ。
`wait` は充足まで open のまま待つ。`fail` は failed として終端し、オーナーへ理由を 1 通届ける。
ただし `fail` が効くのは**まだ誰も手番を取っていないミッションだけ**で、走り出した後に
ノードが落ちて空いた席は再募集（§5.3）の領分だ。区別しないと、夜中の 1 台のクラッシュが
進行中のミッションを巻き添えにする。終端は予算枯渇の `fail` と同じくファイルからの導出で、
新しい終端ファイルも書き手も増やさない。

1 台に複数 amigo が同居するときの同時実行数は、PC 単位のマーカー
（`~/.agents/amigos/turns/*.json`、pid 入り）で律速する。常駐体が起こした手番と、人が手元で
叩いた `run --once` の併走を同じ枠で数えるためだ。バス上の `status/<who>.json` は在籍状態で
あってターンの走行を表さないので、この観測には使えない。

### 5.3 離脱・計画停止・再募集

claim には lease が付き、ランナーは心拍で延長する。lease が切れ、かつ away 宣言も無ければ
クラッシュとみなしてロールを再募集する。成果物・inbox・events はバスに残っているので、
後任は「ロール定義 ＋ 前任の status（引き継ぎメモ）・events・artifacts」を読んで続きから始める。

lease は liveness の信号であって progress ではない。ハングはエージェント CLI 側の
タイムアウト（プラグイン定義の `timeout`）で塞ぐ。

計画停止の扱いは §3.5 のとおり。away 中のロール宛メッセージは inbox に溜まるだけで失われない。

### 5.4 メッセージとアクション封筒

経路は 3 つ。全体連絡は `channels/all/`、特定ロール宛は `inbox/<role-id>/`、
オーナー宛のエスカレーションは `inbox/owner/`。メッセージは型付きで、`question` /
`answer` / `request` / `review` / `status` / `decision-request` / `info` に加え、
システムが使う `wrap-up` / `approve` / `feedback` がある。

ランナーはエージェント CLI の出力を封筒として受け取り、検証してから代書する（§3.3）。

```json
{"actions": [
  {"kind": "send", "to": "architect", "type": "question", "subject": "...", "body": "..."},
  {"kind": "write_artifact", "path": "openapi.yaml", "content": "<ファイル全文>"},
  {"kind": "update_status", "note": "エンドポイント 3/5 完了"},
  {"kind": "declare_done"}
]}
```

検証するのは、宛先が実在するか、パスが自ロールの artifacts 内に収まるか（`..` は拒否）、
`approve` を名乗れる `approver` ロールか、の 3 点。不正なアクションは棄却して events に残し、
次ターンのプロンプトで LLM へ差し戻す。

会話の規約は 2 つだけ。**question には answer か owner へのエスカレーションで必ず応じる**
（`question_timeout`、既定 2 ターンを過ぎた未回答はランナーが自動で `decision-request` へ
昇格する。ただし**宛先が away の間は時計を止め**、代わりに送信側へ不在を 1 度だけ知らせる。
止めないと、相手の PC が夜に落ちているだけで全員の質問が期限切れになり、翌朝のオーナーの
inbox が裁定要求で埋まる）。**設計を左右する合意はオーナーが `decisions.jsonl` に書いて確定する**
（amigo は次ターンから全員これを読む）。design doc の改訂もオーナーのみで、amigo は
`request` で提案するに留める。何が正かを常に 1 箇所に保つ。

### 5.5 ターンループとエージェント CLI

```
loop:
  1. bus.sync_pull()
  2. 新着収集: inbox/<自ロール>/ + channels/all/ + decisions.jsonl（カーソル以降）
  3. 終端 or 自ロール完了済み or 剪定済み → exit
  4. 抑制チェック: 管理面の lifecycle（pause/stop）→ paused。ミッション予算 hard →
     作業ターンを開始しない。soft → wrap-up モードのプロンプト前置きに切替。
     ノード予算超過 → paused（degrade 指定なら縮退して継続）
  5. プロンプト合成: ロール定義 + design doc + 決定記録 + 新着 + 自分の直近 status + artifacts 一覧
  6. エージェント CLI 実行 → アクション封筒
  7. 封筒を検証して適用 + events へ cli_seconds 追記 + status 更新（心拍・引き継ぎメモ）
     — ここまでを単一コミットに（ターン原子性、§3.5）
  8. bus.sync_push() → sleep（無風なら間隔を最大 8 倍まで伸ばす）
```

新着もやることも無ければ**エージェント CLI を呼ばない**（idle ターン）。しかも idle が続いたら
status の書き込み自体も止める（心拍の鮮度維持だけ 60 秒おき）。git バスに無意味なコミットを
積まないためだ。

LLM 実行は [`schemas/agent-cli.schema.json`](../../schemas/agent-cli.schema.json) のプラグイン契約
（`agents/<name>.json`）をそのまま使う。kiro / claude / copilot / codex / cursor / ollama の
6 定義が同梱で、解釈は `agentcore.agentcli` の 1 実装（amigos 側の `agentcli.py` は薄い再輸出）。
amigos 側に CLI 分岐コードは書かない。`stub` は LLM を使わず決定的に封筒を組み立てる検証用の
実装で、プロトコル層のテストはすべてこれで回る。

ロールごとに CLI を選べるので、レビュアーは claude、実装は codex、QA は kiro という混成が組める。
さらに管理面（`schemas/agent-control.schema.json`、`~/.agents/control/control.json`）から
ロール別に CLI とモデルを横断上書きできる。優先順位は 管理面 > ノード既定 > ロール指定。
**どこからも決まらない場合は `stub` へ落とさず環境エラーにする**。既定を stub にすると、
設定を読み落とした経路でダミー応答の成果物がそのまま統合・納品まで進む。沈黙して壊れるより、
`[agent-error:env]` として paused になりオーナーへ理由が届くほうがいい。

失敗は [`agent-cli-plugin-design.md`](./agent-cli-plugin-design.md) の決定的トリアージ
（`[agent-error:quota|auth|env|transient]`）で読み分ける。`transient` はそのターンをリトライ。
`quota` / `auth` / `env` はその amigo を **paused** にして status へタグ付き理由を書き、
オーナーへ 1 度だけ通知する。ロールは lease を保持したまま待機し、環境を直せば続きから走る。
ミッション全体は殺さない。

### 5.6 収束条件

「どこまでやったら終わりか」はオーナーが公示時に宣言する。成立するのは次のいずれか早いほう。

- **`done_when` の成立**。`all-required-done`（既定）は全必須ロールの完了宣言。
  `reviewer-approved` は加えて `approver` ロールの承認。`consensus` は席グループの合意
  （§7.3）で、全席の完了を待たずに早期収束する。
- **静穏化**。全ロールの idle が `quiescence_turns`（既定 3）続き、未回答の質問が 0 のとき。
  会話が止まったということはこれ以上進まないので、現状で統合し、良し悪しは受入で判定する。
- **予算枯渇の wrap-up**（§6.1）。

後ろの 2 つで収束した場合、deliverable の `MANIFEST.json` に `partial: true` が付く。

### 5.7 統合と席グループの集約

`builtin: integrator` のロールが 1 つ要る。省略時はオーナーノードが自動で補充する。
integrator は **LLM を使わない**。収束を検知したら `artifacts/*` を走査して `deliverable/` へ
コピーし、由来ロールと SHA-256 の先頭 16 桁を `MANIFEST.json` に残すだけの決定的な処理だ。

静穏化や予算枯渇で partial 統合した後にミッションが本来の完了へ到達した場合は、完全版で
統合し直す（partial から done への昇格）。

席グループ（§7.3）に `aggregate` が指定されていれば、ここで決定的に集約して
`deliverable/<group>/AGGREGATE.{md,json}` を書く。

### 5.8 受入と納品

integrator の完了で reviewing に入る。`acceptance` が `manual` なら人が `accept` / `reject` を
叩く。`agent` ならオーナーノードのエージェント CLI が design doc と deliverable を突き合わせて
自動判定する。自動判定が `review_rounds` 回差し戻しても受からなければ、人へ
`decision-request` を上げて止まる。無限の差し戻しループは作らない。
`final.json` を書けるのはオーナーノードだけ、という不変条件は自動判定でも変わらない。

**accept は納品棚への搬出を伴う**。バスの `deliverable/` は受け渡しの場で gc の対象なので、
そこにしか成果物が無い状態を残さない。accept が成立した時点でオーナーホームの
`<home>/deliveries/<mission-id>/` へ搬出し、納品書 `delivery.json`
（正典: [`schemas/delivery.schema.json`](../../schemas/delivery.schema.json)）と受領一覧
`<home>/DELIVERY.md` を書く。push 型（accept の副作用として搬出）にしたのは、依頼者が取りに行く
pull 型だと取り忘れがそのまま成果物の喪失になるからだ。`collect` コマンドは残っているが、
納品棚以外へ改めて取り出す補助に降格した。納品書には受入結果（partial とその理由を含む）と
消費予算（events 集計の実行秒）も載り、依頼者は後から「いくらかかったか」を追える。
納品棚は gc の既定では消さない。消すのは人の判断（`gc --deliveries-keep-days`）に限る。

正本の置き場は種別で分ける。文書・調査結果・小さい画像は本体を納品棚へ。**コードは
`workspace.repo` の統合ブランチが正本**で、納品棚には参照だけを残す。10MB を超えるファイルも
搬出せず参照だけ（納品書の `exported: false`）。バスに巨大ファイルを積まない、という原則の帰結だ。

コード成果物の場合、amigo は `amigos/<mission-id>/<role-id>` ブランチで作業して push し、
integrator が `amigos/<mission-id>/integration` へマージする。

---

## 6. 予算

### 6.1 ミッション予算（依頼側）

```yaml
budget:
  execution_minutes: 120   # 全 amigo のエージェント CLI 実行秒の総和。0 = 無制限
  per_role_turns: 30       # ロールあたりターン上限（空転の保険）
  soft_ratio: 0.9          # これを超えたら wrap-up モードへ
  on_exhausted: wrap-up    # wrap-up | fail
```

消費は events の `cli_seconds` の総和（§3.4）。soft しきい値を超えると、ランナーは次の作業
ターンから wrap-up モード（新規の論点を開かず現状を納品可能な形に整えよ、というプロンプト
前置き）に切り替え、最初に検知したノードが全体チャンネルへ wrap-up を宣言する。
hard（100%）以降は integrator と受入以外の CLI 呼び出しを開始しない。

進行中のターンはプラグインの timeout まで走り得るので、超過は最大〈ターン timeout × 同時実行
amigo 数〉に収まる。上振れの上限が見積もれるのが、ロックを持たない設計の代償と担保だ。

追加はオーナーのみ（`budget add <mid> --minutes 60`）。mission.json を改訂して
`decisions.jsonl` に記録し、amigo は次ターンから読む。

### 6.2 ノード予算（請負側）

ノードで LLM を食うのは amigos だけではないので、上限は**ノード横断の共有台帳**で持つ。
正典は [`schemas/node-budget.schema.json`](../../schemas/node-budget.schema.json)。

```
$AGENT_BUDGET_DIR（既定 ~/.agents/budget/）
  config.json               # 上限設定（人 / dashboard / CLI が書く）
  ledger/<YYYYMMDD>.jsonl   # 記帳（UTC 日付・追記専用、各ツールが 1 実行 1 行）
```

合計上限 `execution_minutes`（0 = 無制限、既定）、適用期間 `period`（day / month / total）、
ワークロード別の内訳上限を持つ。amigos は `workload: amigos`、`ref: <mission-id>/<role>` で
記帳する。定常業務（kiro-loop）・agent-project・agent-flow も同じ台帳に記帳・抑制する。

超過するとそのノードの amigo は CLI ターンを開始せず paused になる（`[node-budget]` タグ付きで
オーナーへ 1 度だけ通知）。**ミッションは殺さない**。他ノードの amigo は進行を続け、依頼側は
通知を見て再アサインか待ちを判断できる。上限を上げるか期間が更新されると自動で復帰する。

管理面は agent-dashboard の Amigos タブ（`tools/agent-dashboard/src/features/amigos/`）が兼ねる。
ミッションの読み取りビューと、ノード予算の消費表示・上限編集を持つ。dashboard がバスへ
直接書くことは無い（書くのはホームの commands ドロップだけ）。

---

## 7. チーム設計の自動化

### 7.1 build-team — ミッションだけ投げる入口

役割ミッション表を人が書く経路（`post`）はそのままに、ゴールと design doc だけから役割表を
設計する入口を足した（`build-team`）。設計手順そのものは
[`.github/skills/team-builder/`](../../.github/skills/team-builder/) にスキルとして切り出してあり、
agent-amigos はそれを呼び出す。手順が 1 箇所にあるので、人（Claude Code / Copilot）が同じ
スキルで設計しても結果の形が揃う。

出力契約は `{"target": "amigos", "pattern": "<id|none>", "mission": {…}, "roles": [ … ]}`。
返ってきた設計は `normalize_mission` で検証してから、そのまま post 経路へ合流する。
以降のアサイン・協働・統合・受入は従来と一切変わらない。設計には実際のエージェント CLI が
要る（`stub` と未指定は不可）。

探索木や動的分解が本質のミッションだと判断された場合は、roles ではなく **agent-flow への
委譲封筒**（`target: agent-flow`、`schemas/delegation.schema.json` の op=post / workload=flow）を
出力する。team-builder が 2 つのエンジンのルータとして働く形だ。

### 7.2 オーケストレーションパターンのカタログ

論文由来のマルチエージェント・オーケストレーションパターン 40 種を、agent-amigos の
ロール構成へ写した設計テンプレとして
[`.github/skills/team-builder/patterns/<id>.json`](../../.github/skills/team-builder/patterns/) に
持つ（契約は同ディレクトリの `references/pattern.schema.json`）。

40 種の内訳は、カタログ搭載 37 件と、カタログを持たず自律コンダクタで表現する 3 件
（DyLAN / AgentVerse / meta-prompting）。搭載分は tier で扱いを分ける。

| tier | 件数 | 扱い |
|---|---:|---|
| high | 8 | `build-team` 実行時にカタログをプロンプトへ注入し、**自動選択**の対象にする |
| medium | 29 | 自動選択には入れない。`--pattern <id>` / commands の `"pattern"` で明示指定したときだけ使う |

high の 8 件は `self-refine` / `metagpt-sop` / `agentcoder` / `multiagent-debate` /
`mixture-of-agents` / `chateval` / `self-consistency` / `least-to-most`。磨く、作る、
コードとテスト、議論で詰める、多様性で底上げ、多面評価、頑健化、分解積み上げという
直交する 8 つのミッション形を最小構成で覆うことを狙った。medium はその派生・特化で、
重複するので自動選択には出さない。

medium のうち `tree-of-thoughts` / `graph-of-thoughts` / `lats` の 3 件は `target: agent-flow` で
登録してある。探索木は役割協働の領分ではなく、agent-flow（タスクグラフ）の領分だからだ
（§7.1 の委譲）。agent-amigos 本体に探索構造を持たせる案は採らない。

### 7.3 パターンを支えるプリミティブ

素の逐次パイプライン（要件 → 設計 → 実装 → 検証）とリファインループ（reviewer と差し戻し
ラウンド）は、`collaborates_with` と `review_rounds` だけで書ける。sampling / voting / debate 系を
忠実に写すために、次の 4 つを足した。いずれもコアの協働プロトコル（状態のファイル導出、
決定的 claim、収束会計）には手を入れていない。

**並列同一シート（`seats: N`）**。公示の正規化時に `<role>#0..#N-1` の具体席ロールへ展開する。
各席は通常の 1 席ロールなので、claim・roster・収束・統合・納品の既存機構をそのまま再利用できる。
1 ノード運用でも self-staff が全席を埋める。展開は静的で、実行中の増減は restaff の担当。

**決定的な集約（`aggregate`）**。席は回答を `ANSWER.md`（`aggregate_answer` で変更可）へ書き、
integrator が LLM を使わず集約する。`majority`（最頻値。得票降順 → 回答昇順で決定的に
タイブレーク）、`consensus`（全席一致の判定つき最頻値）、`weighted-vote`（席の `SCORE` を回答
ごとに合計）、`approval-count`（`SCORE` 最大の候補席を選抜）、`gather`（選抜せず全席を集める）。
`convergence.done_when: consensus` と組み合わせると、最頻回答が `consensus_ratio`（既定 0.6）を
占めた時点で全席の完了を待たず収束する。

**同期討論ラウンド（`rounds: N` と `topology`）**。各席が `round-<k>.md` を 1 ラウンドずつ書き、
**全席の round-(k-1) が揃うまで round-k へ進めない**バリアを課す。バリアはファイルの存在で
判定するので、非同期のターンループ上でも決定的に同期する。`topology`（`complete` 既定 /
`ring` / `star` / `tree`）で各席が毎ラウンド読む相手を制限できる（バリアは全席同期のまま）。
最終ラウンドの主張が `ANSWER.md` になる。

**実行中の編成変更（`restaff`）**。オーナーが `--add <roles>` でロールを足し、`--prune <id,…>` で
止める。追加ロールは通常どおり募集され、剪定ロールは `pruned/<id>.json` の存在によって
収束計算・募集・ターン実行から外れる。これを LLM に回させるのが**自律コンダクタ**
（`mission.conductor.enabled`、既定 off）で、現在のロール・進捗・直近の差し戻しを見て
`{add, prune}` を決める。暴走止めはラウンド律速・`max_ops` / `max_total_ops`・
ガードレール（integrator、唯一の承認者、最後の必須ワーカーは剪定しない）。

写せないまま残っているのは `pairwise-rank`（llm-blender / prd-peer-rank）だけ。双方向の
ペア対戦は比較そのものが意味判断なので決定的集約にできない。ranker ロール（approver）に
委ねる設計とし、プリミティブは足さない。

---

## 8. 運用

### 8.1 常駐は PC に 1 本

**agent-amigos 自身は常駐しない**。PC 単位の常駐体は `agent-project serve` の 1 本で、
そこから amigos の参加 tick（`participate`）と手番（`run --once`）が起動される。
agent-amigos が提供するのは単発実行（`drive` / `participate` / `run`）と依頼・確認の操作だけだ。
サブコマンド無しの裸起動は案内を出して終わる。黙って常駐すると常駐体と二重に回って
claim を奪い合うからだ。

ホームは設定ファイル（`agent-amigos.yaml`）の位置で決まる。探索順は
`<cwd>/agent-amigos.*` → `<cwd>/.agents/agent-amigos.*` → `~/.agents/agent-amigos.*`。
既定ではホーム自身がローカルバスになり、`missions/` がホームに生える。この設定ファイルは
agent-dashboard の自動発見マーカーも兼ねる。

外部からの指示は `<home>/.agents/agent-amigos/commands/*.json` に JSON を 1 ファイル置くだけ
（正典: [`schemas/amigos-command.schema.json`](../../schemas/amigos-command.schema.json)）。
プロセス間 API を持たず、結合は常にデータの一方向。処理済みは削除し、失敗は `.rejected` へ
改名する。壊れた指示を無限に噛み続けないためだ。

委譲公示板（agent-board）に参加する設定（`board:`）を与えると、巡回して `workload: amigos` の
公示に入札し、勝てば**自分がオーナーとして**ミッションを公示する。板は専用 git リポジトリ 1 本と
JSON 契約（正典: `schemas/board.schema.json`）だけで成立し、処理を持たない。板専用のデーモンは
作らず、入札は各エンジンの既存の巡回へ 1 ステップ足す形に畳んである。落札後の引き渡し先は
結局そのエンジンなので、分離する意味が無いからだ。入札はタスク claim と同一仕様
（lease 付き・`(ts, who)` の決定的タイブレーク）で、板上の書き込みもパス単位の名義分割
（公示と成果の確定は依頼者、bid / status / results は各ノードが自分名義のみ）だから
コンフリクトしない。公示の id はミッション id を貫く冪等キーで、再投函は同一公示に落ちる。
落札した公示は commands 契約の post へ変換して通常どおり公示し直すだけで、以降のロール募集から
受入までにエンジン側の特例は無い。forge の issue を板にする案や外部ブローカー
（NATS / RabbitMQ 等）は、「バス上のファイルが真実・中央は転送のみ」の原則と衝突するので
採らなかった。

入札の可否（担当リポジトリ・タグ・CLI・契約バージョン・引き受けるエンジン・枠の照合）は
`agentcore.board.eligible` の 1 実装で、agent-flow の板参加と共有する——以前は「同じ仕様・
別実装」で 2 つあり、片方だけ育つと同じ公示が経路によって拾えたり拾えなかったりした
（`agentcore.protocol` の claim と同じ理由で集約した）。判定材料の正典は各 PC の
`agent-project.host.yaml` で、板参加の宣言はノードの持ち物として一元化している
（[agent-project 設計書](./agent-project-design.md) の「板の請負」）。

**判定材料を渡し忘れると、症状は「入札しない」として出る。** ここは実際に踏んだ:
`requires.agent_cli` の照合は fail-close（使える CLI を宣言していないノードは入札しない）
なのに、板の巡回が `agent_cli` を渡していなかったため、CLI 指定つきの公示に amigos ノードは
**永久に入札していなかった**。例外も警告も出ず、板の側からは「誰も手を挙げない公示」に
見えるだけになる。このノードの CLI 宣言はスカラ 1 件（`--agent-cli` / 設定）で、板の語彙は
「使える CLI の一覧」なので、ロール応募（`assign.py`）と同じ流儀で畳んで渡す。
`workloads` / `budget.max_concurrent` はロールではなく**ノードの性質**なので、amigos 自身の
設定ではなく host.yaml から読む（agent-flow と同じ正典を見る）。

### 8.2 障害と回復

| 障害 | 検知 | 回復 |
|---|---|---|
| 計画的ノード停止 | SIGTERM フック → `state: away` | ロール保持のまま翌朝続きから。grace 超過やオーナー判断で再募集（§3.5） |
| ターン途中の電源断 | 検知不要 | ターン原子性によりバスは全部か無か。そのターンのやり直しだけ |
| ノードのクラッシュ | 心拍途絶 → lease 失効（away 宣言なし） | ロール再募集。後任が status / events / artifacts から引き継ぐ |
| ミッション予算の枯渇 | events の `cli_seconds` 総和 | soft で wrap-up → hard で partial 納品。オーナーは budget add で追加可 |
| ノード予算の枯渇 | 共有台帳の合計 | そのノードの amigo だけ paused。ミッションは他ノードで継続 |
| 会話の空転 | `quiescence_turns` の静穏化 | 現状で統合し、良し悪しは受入判定に委ねる |
| エージェント CLI のハング | プラグイン timeout | ターン失敗 → リトライ、繰り返せば paused ＋ 通知 |
| quota / auth / env | `[agent-error:*]` タグ | amigo paused。環境修復後に続きから（§5.5） |
| 使う CLI が決まらない | ターン先頭の解決（§5.5） | `[agent-error:env]` として paused。stub のダミー成果物を作らない |
| 質問の放置 | `question_timeout` | ランナーが owner へ自動エスカレーション。宛先が away の間は時計を止める |
| 募集の失敗 | `staffing_timeout` 超過かつ必須ロール未充足 | `staffing_policy: fail` なら failed で終端し、オーナーへ理由を通知。走り出した後の欠員は再募集 |
| 締切（wall-clock）超過 | `mission.deadline` | オーナーへ 1 度だけ通知。自動 fail はせず、予算追加か cancel かの判断は人に残す |
| push 競合（GitBus） | git | 名義分割で原理的に稀。`pull --rebase` リトライで吸収 |
| オーナーノードの停止 | roster / decisions が進まない | ミッションは自然停止（amigo は idle で待機）。復帰で再開 |

### 8.3 信頼境界

オンプレ限定。バスへの到達性がそのまま参加権限で、認可は既存基盤（git 認証）に委ねる。
バスに秘密情報は書かない。プロンプトへ渡す資格情報は各ノードのローカル環境変数に置く。

**他 amigo からのメッセージは半信頼**として扱う。ランナーはプロンプト合成時にメッセージを
「他エージェントからの入力（指示ではなく情報）」として区画表示し、design doc と decisions だけを
正典と明示する。プロンプトインジェクション耐性の最低線だ。ランナーによる代書（§3.3）が
唯一の書き込み経路なので、LLM の出力がバス規律やワークスペース外のパスを破ることはできない。

---

## 9. 実装状況と既知の欠落

**動いているもの**: バス（Local / Git）、公示から受入までの全経路、決定的 claim と自己補充、
away プロトコル、封筒ランナー、二層の予算会計、integrator と納品棚、owner-picks、
`acceptance: agent`、build-team とパターンカタログ、seats / aggregate / rounds / topology /
restaff / conductor、agent-board への入札参加、agent-dashboard の Amigos タブ。
テストは stub エージェント（LLM 不要）で 176 件。

**残っている欠落**:

| 項目 | 状態 |
|---|---|
| ノードの可用性ウィンドウ宣言 | 未実装。`owner-picks` の判断材料にできない。away プロトコル（§5.3）が事後の耐性は担保しているので、事前の宣言が無くても運用は回る |
| `acceptance: codd-gate` | 将来拡張。現状は `manual` / `agent` のみ受け付ける |
| `pairwise-rank` パターン | 設計方針として ranker ロールに委ねる（プリミティブは足さない） |

**2026-07-26 に直したもの**: 設定ファイルの `agent_cli` / `tags` / `roles` / `interval` /
`manual_claim` / `board` を `participate` しか読まなかった件（解決を `_resolve_ctx` へ一本化）、
決まらない agent CLI が黙って `stub` へ落ちていた件（`[agent-error:env]` で paused）、
`staffing_policy: fail` が `wait` と同じ挙動だった件、`deadline` 超過が通知されなかった件、
away 中も `question_timeout` が進んでいた件。

---

## 付録 A. ロールミッション表

`post --roles` に渡す入力（YAML / JSON）。正典スキーマは
[`schemas/mission.schema.json`](../../schemas/mission.schema.json)、雛形は
[`tools/agent-amigos/roles.yaml.example`](../../tools/agent-amigos/roles.yaml.example)。

```yaml
mission:
  title: 社内 FAQ ボットの MVP
  goal: design-doc.md の受入基準をすべて満たす FAQ ボットを納品する
  deadline: 2026-07-24T09:00:00Z     # 任意。超過は owner へ通知のみ（自動 fail しない）
  assignment_policy: first-come      # first-come | owner-picks
  staffing_policy: self-staff        # self-staff | wait | fail
  staffing_timeout: 600
  acceptance: manual                 # manual | agent
  convergence:
    done_when: reviewer-approved     # all-required-done | reviewer-approved | consensus
    quiescence_turns: 3
    review_rounds: 2                 # acceptance: agent の差し戻し上限
    question_timeout: 2
    consensus_ratio: 0.6             # done_when: consensus のしきい値
    consensus_min: 2
  budget:
    execution_minutes: 120           # 0 = 無制限
    per_role_turns: 30
    soft_ratio: 0.9
    on_exhausted: wrap-up            # wrap-up | fail
  conductor:                         # 自律コンダクタ（既定 off）
    enabled: false
    cli: claude
    max_ops: 3
    max_total_ops: 12
    interval_rounds: 1
  workspace:
    repo: ssh://git@gitlab.local/team/faq-bot.git   # コード成果物用（任意）

roles:
  - id: architect                    # all / owner は予約語。/ と # は使えない
    title: アーキテクト
    mission: |
      design-doc.md を正として構成を確定し、他ロールからの設計質問に回答する。
      判断に迷うものは owner へ decision-request でエスカレーションする。
    deliverables: [architecture.md]
    required: true
    agent_cli: claude
    model: null                      # 任意
  - id: impl-api
    title: API 実装
    mission: architecture.md に従い API を実装し、単体テストを通す。
    deliverables: [src/, tests/]
    requires: { tags: [python], cli: codex, repos: [app] }
    collaborates_with: [architect]   # 会話のヒント。実行順序の強制ではない
  - id: reviewer
    title: レビュアー
    mission: 全ロールの成果物を design-doc.md と突き合わせてレビューする。
    approver: true                   # done_when: reviewer-approved の承認者
  - id: solver                       # 席グループの例（§7.3）
    mission: 問題を独立に解き、最終回答を ANSWER.md に書く。
    deliverables: [ANSWER.md]
    seats: 5                         # solver#0..#4 へ展開
    aggregate: majority              # majority|consensus|weighted-vote|approval-count|gather
    rounds: 3                        # 同期討論（seats>=2 のみ）
    topology: ring                   # complete|ring|star|tree（rounds>=1 のみ）
    aggregate_answer: ANSWER.md
    aggregate_score: SCORE
  # integrator は省略可（省略時はオーナーノードが自動補充する組み込みロール）
```

`collaborates_with` は依存グラフではなく会話のヒントに留める。実行順序が本質の仕事は
タスクグラフ、つまり agent-flow の領分（§2）。

---

## 付録 B. CLI

```
agent-amigos init-bus     --bus <dir|git+url>
agent-amigos post         --design <md> --roles <yaml> [--drive]        # オーナー: 公示
agent-amigos build-team   --goal "..." --agent-cli <cli> [--pattern <id>] [--out <f>|--post]
agent-amigos participate  [--json] [--tags ...] [--roles ...] [--board ...]  # 参加のみ 1 巡
agent-amigos drive        [--mission-id <id>] [--cycles N]              # 単発駆動（終端まで）
agent-amigos join         [--roles ...] [--tags ...] [--agent-cli ...]  # 自前で回る従来の入口
agent-amigos run          --mission <mid> --role <role> [--once]        # 単発 amigo
agent-amigos status       [<mid>]
agent-amigos assign       <mid> <role> [<node>]                         # owner-picks の確定
agent-amigos restaff      <mid> [--add <roles>] [--prune <id,…>]        # 実行中の編成変更
agent-amigos accept       <mid>  /  reject <mid> --feedback "..."
agent-amigos deliveries   [-v]                                          # 納品棚の一覧
agent-amigos collect      <mid> --out <dir>                             # 納品棚とは別に取り出す
agent-amigos budget       add <mid> --minutes N  /  node [--limit-minutes N] [--period day]
agent-amigos say          <mid> --to <role|all|owner> --body "..."      # 人の介入発言
agent-amigos cancel       <mid>  /  gc [--keep-days N] [--deliveries-keep-days N]
```

`say` は人もチームの一員として口を挟むための穴。オーナー名義、または `human:` プレフィクス
付きでメッセージを書く。

主な環境変数: `AGENT_AMIGOS_BUS` / `AGENT_AMIGOS_NODE` / `AGENT_AMIGOS_LEASE`（既定 600 秒）/
`AGENT_AMIGOS_AWAY_GRACE`（既定 7200 秒）/ `AGENT_AMIGOS_PULL_INTERVAL`（既定 15 秒）/
`AGENT_AMIGOS_TURNS_DIR` / `AGENT_BUDGET_DIR`。詳細は
[`tools/agent-amigos/README.md`](../../tools/agent-amigos/README.md)。

---

## 付録 C. 関連文書と旧 § 番号の対応

**関連文書**: [`tools/agent-amigos/README.md`](../../tools/agent-amigos/README.md)（使い方）／
[`.github/skills/team-builder/SKILL.md`](../../.github/skills/team-builder/SKILL.md)（役割設計手順の正典）／
[`agent-project-design.md`](./agent-project-design.md)（常駐一本化と板の請負）。
納品棚の設計判断は §5.8 に、委譲公示板の要点は §8.1 に転記済み。

本書は 2026-07-26 に再構成した。旧 `agent-amigos-teambuilder-patterns.md` は §7 へ統合して削除。
旧番号を参照している外部文書のための対応表:

| 旧 | 新 | 旧 | 新 |
|---|---|---|---|
| §3.1 ライフサイクル | §4.1 | §7.4 ターンループ | §5.5 |
| §3.2 収束と予算 | §5.6 / §6.1 | §8.1 integrator | §5.7 |
| §3.3 ノード予算 | §6.2 | §8.2 受入 | §5.8 |
| §4.1 レイアウト | §4.2 | §8.3 コード成果物 | §5.8 |
| §4.2 書き込み規律 | §4.3 | §8.4 納品棚 | §5.8 |
| §5 / §5.1 転送層 | §4.4 | §9 エージェント CLI | §5.5 |
| §5.2 hub サーバ | 廃止（§3.1） | §10 データスキーマ | 付録 A |
| §6.1 公示と応募 | §5.1 | §10.1 team-builder | §7.1 |
| §6.3 確定名簿 | §5.1 / §5.2 | §11 CLI | 付録 B |
| §6.4 自己補充 | §5.2 | §11.1 常駐運用 | §8.1 |
| §6.5 離脱 | §5.3 | §12 障害回復 | §8.2 |
| §6.6 away | §3.5 / §5.3 | §13 セキュリティ | §8.3 |
| §7 通信 | §5.4 | §14 非目標 | §2.1 |
| §7.2 封筒 | §5.4 | §15 flow との住み分け | §2 |
| §7.3 会話の規約 | §5.4 | §16 フェーズ / §17 ADR | §9 / §3 |
