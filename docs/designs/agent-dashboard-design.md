# agent-dashboard — 複数エージェントを束ねる操作面 設計書

> 作成 2026-07-14 ／ 最終照合 2026-07-26（実装 `tools/agent-dashboard/` と突き合わせ済み）
> 実装: `tools/agent-dashboard/`（Electron・ランタイム依存なし。テスト 61 ファイル・`npm test`）
> 読む契約: [`schemas/node-budget.schema.json`](../../schemas/node-budget.schema.json) /
> [`schemas/agent-control.schema.json`](../../schemas/agent-control.schema.json) /
> [`schemas/agent-cli.schema.json`](../../schemas/agent-cli.schema.json) /
> [`schemas/delegation.schema.json`](../../schemas/delegation.schema.json) /
> [`schemas/amigos-command.schema.json`](../../schemas/amigos-command.schema.json) /
> [`schemas/agent-session-commands.schema.json`](../../schemas/agent-session-commands.schema.json)
> 前提とする設計: [`agent-project-design.md`](./agent-project-design.md) /
> [`agent-flow-design.md`](./agent-flow-design.md) / [`agent-amigos-design.md`](./agent-amigos-design.md) /
> [`2026-07-24-single-resident-controller-design.md`](../plans/2026-07-24-single-resident-controller-design.md)

---

## 1. TL;DR

**何を作るか**: 別ホスト（WSL・他 PC）で動く agent-project / agent-flow / agent-amigos /
定常業務を 1 つの Windows GUI から見渡し、人の判断だけをその場で返せるようにする操作面。

**主要な決定**:

1. dashboard は状態の**書き手にならない**。読むのはファイル、書くのは公式契約（`commands/` 等）の投函だけ。
2. 制御面はソースツリーで分け、`features/index.js` の配列 1 本で合成する。動的プラグインにはしない。
3. プロジェクトの発見は実行側が書く状況ファイル 1 枚（`engine/status.json`）が唯一の根拠。

**却下した主要案**: dashboard 自身が状態共有リポジトリを pull / push する案。一度実装したが、
viewer の push が本体の状態ファイルへコンフリクトマーカーを書き込んで状態を失わせたので撤去した（§3.1）。

**読むべき人**: dashboard に制御面を足す人、契約ファイルを介して dashboard と繋ぐツールの実装者。
**読まなくてよい人**: 使い方だけ知りたい人（[`tools/agent-dashboard/README.md`](../../tools/agent-dashboard/README.md) を読めばよい）。

---

## 2. 背景と課題

エージェントを回す本体は WSL や別の PC にいる。状態はファイルで、正しく読めば全部見える。
問題は**人の側**だ。needs（人の判断待ち）が出たことに気づくには誰かが端末を覗く必要があり、
承認を返すにはコマンドを打つ必要がある。ツールが増えるほど覗く場所も増える。

しかも「人が触れる GUI」を素朴に作ると壊れる。実際に壊した: viewer から状態共有リポジトリへ
push した結果、本体の状態ファイルにコンフリクトマーカーが書き込まれ、状態共有が復旧不能になった。
GUI は状態の書き手として最悪の性質を持つ。人の操作タイミングは予測できず、本体の同期規律を知らない。

そこで dashboard は**見る面**と**判断を返す面**に徹する。状態を動かすのは常駐体
（`agent-project serve`）だけ、という一本化を GUI 側からも守る。

### 2.1 目標と非目標

**やらないこと**を先に決める。

- **状態の書き込み**。`backlog/*.md` の status、`project.json`、agent-flow の run 状態を
  画面から書き換えない。done の根拠は verify だけ、という本体の不変条件を GUI から壊さない。
- **GitLab への書き込み**。レビュー操作は gitlab-review-viewer の役割で、こちらは読み取りのみ。
- **完全自動運転**。判断を減らす仕組みは作るが、人がゼロになる状態は目標にしない。
- **動的プラグイン**（実行時ロード・サンドボックス・版管理）と npm ワークスペース分割。
- **実行エンジンを起動・再起動する経路**。常駐体（`agent-project serve`）の起動は OS の起動系が担う。
  唯一の例外は「この端末で募集中の仕事を引き受ける」参加操作で、人が明示的に押したときだけ
  agent-flow のワーカーを 1 つ立てる（§5 の `participation`）。

**やること**は次の 4 つ。

1. 複数プロジェクト・複数ワークロードの状態を 1 画面で見渡す。
2. 人の判断（承認・差し戻し・投入・一時停止）を公式契約で返す。
3. 判断が必要になったことに、画面を見ていなくても気づける。
4. 判断そのものを AI が下ごしらえして、人の 1 回の介入の質を上げる。

---

## 3. 主要な設計判断

### 3.1 読むのはファイル、書くのは契約の投函だけ（viewer からの git 書き込みを撤去）

**判断**: dashboard が触れるのは、状態ファイルの**読み取り**と、公式契約ファイルの**投函**の 2 つだけ。
状態共有リポジトリへの pull / push / commit も、本体の状態ファイルの書き換えも持たない。

**文脈**: 本体（agent-project / agent-flow / agent-amigos）は状態をファイルで持ち、git で共有する。
同期には規律（間隔律速・pathspec 限定コミット・rebase リトライ・force push 禁止）がある。
GUI はその規律の外側から、人の気まぐれなタイミングで書き込もうとする。

**選択肢と却下理由**:

- *viewer 自身が pull / push する*: リモートで動く本体へ操作を即座に届けられる。**一度実装した**
  （⇣ ボタン・自動 pull 間隔・操作を都度コミットしてプッシュ）。却下。viewer の push が本体の
  状態ファイルへコンフリクトマーカーを書き込み、状態共有が復旧不能になった。多重コミッタ対策
  （pathspec 限定・autostash・バックオフ）を積んでも、書き手が 2 人いる構図そのものが残る。
  常駐一本化で**書き手を常駐体 1 つに固定**し、この経路ごと消した。
- *操作を CLI で直接実行する（本体を起こす）*: 反映が速い。却下。CLI パスの誤りが
  「押しても何も起きない」原因不明の不具合になり、同一ホストでしか効かない。
  人の操作は `commands/` の投函で届き、実行は常駐体が担う。

**トレードオフ**: 操作の反映が常駐体の同期間隔ぶん遅れる。即時性は諦めた。
この判断は `test/no-git-writes.test.js` が構造として固定している（`pull` / `push` / `commit` /
`checkout` などのサブコマンド文字列を、状態を扱う層のソースから機械的に落とす）。
コードを足せば簡単に戻せてしまう類の護りなので、レビューではなくテストで縛る。
検査は `src/` 全体に掛かり、除外は `cowork` の 1 つだけ（定常業務は**人の成果物リポジトリ**で
ブランチを切って push する機能で、状態リポジトリには触らない）。同じテストが**範囲そのもの**も
検査する — 新しい feature は自動でこの護りの下に入り、外すには除外リストを触るしかない。
護りの中身より先に掛かっている範囲が縮むほうが起きやすく、しかもテストが緑のままなので
気づきにくい。

**確信度**: 高い。実障害が根拠。

### 3.2 制御面はソースツリー分離と列挙合成（フルプラグインを却下）

**判断**: Electron シェル等の共通部を `src/base/` に、各制御面を `src/features/<id>/` に置き、
`src/features/index.js` の配列に並べるだけで IPC・preload・設定既定へ反映する。

**文脈**: 上流のダッシュボード更新を取り込みつつ、別グループが自分の制御面を足せる形が要る。
一方で、この規模のアプリに動的ロードの仕組みを持ち込むのは明らかに過剰だ。

**選択肢と却下理由**: *フルプラグイン（動的ロード・隔離・版管理）*と *npm ワークスペース化*は却下。
分離したいのは**ソースの所有**であって実行時の境界ではない。列挙合成なら、上流とのマージで
衝突するのは `features/index.js` の 1 行だけになる。

**トレードオフ**: feature は同一プロセス・同一権限で動く。信頼できないコードは載せられない。

**確信度**: 高い。当初 2 つだった feature が 7 つに増えても、合成点は 1 ファイルのままだ。

### 3.3 プロジェクトの発見は実行側の状況ファイル 1 枚

**判断**: どのプロジェクトが存在するかは `<agents home>/engine/status.json` の `children[].root`
だけから導く。画面からの登録・登録解除は持たない。

**文脈**: 何を回すかを決めるのは実行側の `agent-project.host.yaml` で、dashboard はそれを映す面だ。
両側に登録簿があると、どちらが正かが曖昧になる。

**選択肢と却下理由**: *画面の設定でフォルダを列挙する*、*親フォルダをスキャンして自動追加する*、
*ロックファイルを直接覗いて稼働を判定する*はいずれも却下（実装していたものを撤去した）。
`engine/status.json` は常駐体だけが書く 1 枚で、発見・稼働・共有の健全性・子の隔離の
すべての根拠になる。設定に残るのは「どこの状況ファイルを読むか」（WSL ディストロ・ベースパス）と
表示の好みだけ。

**トレードオフ**: 常駐体が動いていないホストでは一覧が空になる。契約版が食い違うノードは
「更新漏れ」として表示し、黙って情報を欠いたまま正常に見せない。

**確信度**: 高い。`test/discover-engine.test.js` が入口の一本化を固定している。

### 3.4 AI は下書きまで。確定は人のボタン

**判断**: 画面の AI（Viewer アシスタント）は**テキストを返すだけ**で、ファイルを書かない。
charter の下書きも、承認の推薦も、差し戻し文面も、人が確定ボタンを押して初めてファイルになる。

**文脈**: 人の判断を減らすには AI に下ごしらえさせるのが効く。ただし AI に確定権を渡すと、
§3.1 で消したはずの「規律を知らない書き手」が別の顔で戻ってくる。

**選択肢と却下理由**: *AI にファイルを直接書かせる*は却下。書き込みは `authoring.js` /
`actions.js` のホワイトリスト経路に閉じ、AI の出力はそこへ**流し込む候補**に留める。
*条件付き自動承認*（人が事前に決めたポリシーで `commands/` approve を自動投函する案）は
却下ではなく**未実装**。やるとしても公式の承認契約を自動で押すだけで、verify ゲートも
done の不変条件も迂回しない形に限る（§8）。

**トレードオフ**: 人の操作回数は減らない。減るのは 1 回あたりの判断コストと手戻りの確率だ。

**確信度**: 高い。

### 3.5 renderer はバンドラなしのクラシックスクリプト分割

**判断**: 画面側はビルド工程を持たない。機能ごとにファイルを分け、`index.html` の
`<script>` 読み込み順でグローバルスコープを共有する。

**文脈**: 当初は 1 本の巨大 `renderer.js` だった。保守のために分けたいが、テストがソースを
**文字列走査**しているため、モジュール境界を入れると検査が全部壊れる。

**選択肢と却下理由**: *バンドラ（webpack / vite）や UI フレームワークの導入*は却下。
Electron にランタイム依存を増やさない方針（gitlab-review-viewer と同じ構成）を崩さないため。
代わりに**読み込み順を契約にする**: (1) `renderer.js`（core。`state` と共有ユーティリティ、
タブ登録簿 `registerFeatureTab`）→ (2) `sections/*.js`（各タブの描画。関数宣言のみで
load 時実行を持たないので相互の順序は不問）→ (3) `features/*.js`（自分のタブを差し込む）→
(4) `bootstrap.js`（`init()` の定義と呼び出し。最後）。
テストは `test/helpers/renderer-src.js` がこの順で結合して「元の全文」を復元する。

**トレードオフ**: グローバル汚染と、順序契約を破ったときの壊れ方が分かりにくいこと。
名前衝突は人が気をつけるしかない。

**確信度**: 中。renderer が今より倍になったら、この判断は見直す引き金になる。

---

## 4. 全体像

この節は概要の粒度。

```
tools/agent-dashboard/src/
├── base/main/          Electron シェル・設定合成・git 読み取り・GitLab 読み取り・通知・共通 IPC
├── features/
│   ├── index.js        載せる制御面の列挙（唯一の合成点）
│   ├── agent-project/  agent-project ＋ agent-flow（charter/backlog/needs/run/操作/オーサリング）
│   ├── kiro-loop/      定期実行ループの端末ビューと復旧送信（WSL の tmux 越し）
│   ├── cowork/         定期実行と定型業務の一覧・実行入口
│   ├── amigos/         agent-amigos ミッションの読み取りビュー
│   ├── orchestration/  ノード予算・エージェント制御・CLI ドロップインの横断管理
│   ├── delegation/     エンジン間の委譲封筒（内部機能。独立画面は持たない）
│   └── participation/  募集中の仕事へこの端末から参加する操作面
├── main/               旧パス互換シム（既存テストの require を壊さないため）
├── preload.js          base API ＋ 各 feature の preloadApi を合成
└── renderer/           画面（core → sections → features → bootstrap の順に読む）
```

| 層 | 責務 | 所有 |
|---|---|---|
| `base` | 窓・プロトコル・設定マージ・git 読み取り・GitLab 読み取り・OS 通知 | 上流 |
| `features/agent-project` | プロジェクトの可視化と人のアクション | 上流 |
| その他の `features/*` | 各ワークロードの制御面 | 追加する側 |

agent-flow は run-id の相互リンクや cancel / resubmit で agent-project と結合が強いため、
別 feature にせず `agent-project` に含める。ディレクトリ名は制御面の主対象に合わせた。

### 4.1 feature 記述子（合成契約）

各 feature は `src/features/<id>/index.js` から次を export する。

```js
{
  id: 'agent-project',
  configDefaults: { ... },          // base の既定設定へ deepMerge される
  registerIpc(ctx) { ... },         // ctx = { handle, loadConfig, saveConfig, git, GitLabClient, dialog, shell }
  preloadApi() {                    // window.api に生えるメソッドの工場
    return { foo: (invoke) => (a) => invoke('dashboard:foo', { a }) };
  },
}
```

IPC は全チャネルが `{ok, data|error}` に揃う（`base/main/handle.js` が包む）。
新しい制御面を足す手順は、`src/features/<id>/` を既存 feature を雛形に作り、
`features/index.js` の配列へ 1 行足し、必要なら renderer にタブを差し込む、の 3 手だけ。

### 4.2 データソースと更新

すべて読み取り専用で、本体の稼働を前提にしない（稼働中なら自動更新で追従する）。

| 見るもの | 読むファイル |
|---|---|
| プロジェクトの存在・稼働・共有の健全性 | `<agents home>/engine/status.json`（常駐体が書く唯一の根拠。§3.3） |
| charter / バックログ / 要対応 | `charter.md`・`backlog/<id>.md`・`archive/<id>.md`・`needs/<id>.md`・`policy.md` |
| 実行（agent-flow） | `<bus>/runs/<run-id>/` の `graph.json` ＋ `results/` ＋ `claims/` ＋ `waits/` からノード状態を導出。ポーリングごとに `flow-archive/<run-id>.json` へ写し取り、bus から消えた run も追える |
| 履歴 | `run-log.jsonl`・`decisions/<id>.md`・`DELIVERY.md`・`journal.md` |
| ミッション | agent-amigos のバス（読み取り専用）とオーナーホームの納品棚 |
| 定期実行 | WSL 上の `~/.kiro/loop-state/*.json`（`~/.agent/` も同形式なので両方読む）と tmux |
| ノード予算・エージェント制御 | `~/.agents/budget/`・`~/.agents/control/`（ツール横断のデータ契約） |
| レビュー待ち | `repos.json` の GitLab リポジトリのオープンイシュー（API 設定時） |

更新は既定 5 秒のポーリング。純プル型なので、気づきの仕組みは別に要る（§7）。

---

## 5. 制御面ごとの責務

この節はコンポーネントの粒度。画面の詳細は README と `docs/plans/` の各設計へ委ねる。

| feature | 見せるもの | 書くもの |
|---|---|---|
| `agent-project` | charter / 達成状況 / バックログ / 要対応 / 実行グラフ / レビュー待ち / 履歴 | `needs/` 記入・`inbox/` 投入・`commands/` ドロップ・上位入力ファイル（charter / policy / repos）の編集 |
| `cowork` | 定期実行ジョブと定型業務の一覧・設定同期・実行 | 人の成果物リポジトリ（プロジェクト状態には触れない） |
| `kiro-loop` | 稼働中ループの構造化状態・会話画面・復旧送信 | 何も書かない（`kiro-loop send` への依頼だけ） |
| `amigos` | ミッションの進行・担当・やりとり・納品棚 | ホームの `commands/` ドロップのみ（バスへは書かない） |
| `orchestration` | ノード予算・エージェント制御・CLI ドロップインの棚卸し | `~/.agents/` 配下の契約ファイル |
| `delegation` | 独立画面なし（ミッション・要対応・実行へ溶かす） | 委譲封筒をネイティブ形式へ変換して投函 |
| `participation` | 募集中の仕事とこの端末の参加操作 | 人が押したときだけ agent-flow ワーカーを 1 つ起動（唯一のプロセス起動経路） |

### 5.1 kiro-loop 連携 — 監視と復旧を tmux から引き上げる

定期実行ループ（kiro-loop / agent-loop）は tmux の上で動き、監視は `tmux attach`、復旧は
`kiro-loop send` という CLI 前提の運用だった。外部接点（状態ファイル・`ls` / `send`・ログ）は
あるが、すべて pull 型で人の負荷が高い。ここを dashboard へ移し、**人が tmux を知らなくても
運用できる状態**を作る。

前提として、dashboard は Windows で動き、kiro-loop / tmux / エージェント CLI は WSL にいる。
触る経路は常に `wsl.exe -e …` を通る。

構成は 2 レイヤ。**構造化状態**（最終実行時刻・alive / busy・会話履歴・復旧送信）は
`loop-state/<pid>.json` の読み取りと `kiro-loop send` への依頼で作り、**生画面**
（動いている tmux ペインそのもの）は `capture-pane` のポーリングで見せる。普段は構造化状態で
足り、深掘りしたいときだけ生画面へ降りる。概要から詳細へ、という画面全体の思想と揃えた。

不変条件は §3.1 と同じで、**dashboard は kiro-loop の状態の書き手にならない**。読むのは
ファイルと `capture-pane`、操作は `send` への依頼に限る。`send` はプロンプト名の解決・busy 判定・
スロット取得を内蔵しているので、生の `send-keys` を避けて CLI に依頼すれば GUI 操作が
同時実行制御を壊さない。busy 時に CLI が即時拒否するのは人が待って再実行する設計なので、
UI 側で「処理中につき送信待機」に変換する（kiro-loop 本体は変えない）。

文言の方針として、画面に tmux / セッション / capture-pane といった内部語を出さない。
定期プロンプト名は設定ファイル由来だと分かるよう「予定の名前」と呼ぶ。

IPC は 4 本（`kiroLoop:listSessions` / `capture` / `state` / `send`）。
インタラクティブな attach（`node-pty` ＋ `xterm.js`）は未実装で、当面やらない（§8）。

---

## 6. 人のアクションと護るべき不変条件

dashboard から返せる判断は、plan-review / delivery-review の承認・差し戻し・却下、feedback 再開、
revise（doing 中も）、replan、inbox 追加、pause / stop、reset、run の cancel と削除。
どれも次の護りを破らない。

- **done は verify のみが根拠**。状態遷移を画面から直接書き換えない。revise も状態を書かず、
  本体側の同一ロジックが遷移を決める。
- **公式の入力契約だけを使う**。`needs/` 記入・`inbox/` 投入・`commands/` ドロップの 3 つ。
- **AI はファイルを書かない**（§3.4）。
- **GitLab は読み取り専用**。
- **タスク状態ファイルは書き換えない**（`backlog/*.md` の status、`archive/`、`project.json`）。

例外は 2 つある。ひとつは 🗑 削除（タスク / run）で、削除の公式契約が無いためゴミ箱への移動として
行う。もうひとつは viewer 管理のサイドカー（監視担当の割り当て `assignments.json` と
レビューコメント `reviews/<task-id>/*.json`）で、どちらもタスク状態には触れない。

---

## 7. 気づく・下ごしらえする

ポーリングは純プル型なので、画面を見ていない間に出た要対応には気づけない。ここは 2 段で埋める。

**気づく**: 新しい要対応が現れたら OS 通知・タスクバーバッジ・ウィンドウのフラッシュで知らせる。
増分検知は `discover()` の `needsCount` の前後比較で、**観測済みプロジェクトで数が増えたときだけ**
通知する（起動直後の既存分・減少・新規発見では通知しない）。フォーカス中はポップアップと
フラッシュを抑え、バッジだけ更新する。クリックは既存のディープリンク（`agent-dashboard://`）へ流す。

**停滞を見せる**: 各要対応カードに待ち時間（`needs` の mtime からの経過）を出し、未対応は
停滞の長い順に並べる。既定選択も最も停滞したカードにして最優先へ誘導する。しきい値
（既定 24 時間）超で赤、1/3 超で黄。手戻りではなく**停滞**の可視化で、長時間放置＝下流が
止まっている、を一目で分かるようにする。

**下ごしらえする**: Viewer アシスタントは読み取り専用の 4 モードを持つ。全体を説明する
`consultation`（Doctor）、失敗ノードの `failure-diagnosis`、計画レビューの `plan-critique`、
検収の `delivery-rationale`。いずれも推薦と差し戻し文面案までを返し、確定は人が押す（§3.4）。
加えて構造化アシスト（フォローアップ案・投入補助・タスク記述の誘導）が JSON を返し、
フォームへ流し込める。

**上流で潰す**: タスク投入フォームは、完了条件が無い・自然文の accept が曖昧（「ちゃんと」
「正しく」等）を投入前に警告する。曖昧な accept は弱い verify に合成され、「PASS したはずが
人の期待と違う」手戻りの根本原因になる。非ブロックの警告で、続行するかは人が決める。

---

## 8. 実装状況と既知の欠落

**動いているもの**: §4 の 7 制御面すべて、§6 の人のアクション一式、§7 の通知・SLA・AI 補助・
投入時リンティング、kiro-loop の構造化状態と復旧送信、この PC の役割切り替え
（`engineer` / `viewer`）。テストは `npm test` で 61 ファイル・全緑。

**未実装の改善余地**（元の改善提案から、実装が無いものだけ残した）:

| 項目 | 内容 | 効き先 |
|---|---|---|
| 外部通知ルーティング | OS 通知と同じイベントを webhook にも流す（オプトイン・要約のみ・書き込み権限なし） | 在席していない・複数人運用 |
| 横断「要対応キュー」 | プロジェクト横断で要対応を 1 キューに集約し、緊急度 × 滞留時間でソート | 朝一の人待ち一掃 |
| 条件付き自動承認 | 人が事前に決めた安全条件（verify PASS ∧ 差分小 ∧ AI リスク低 等）で公式の `commands/` approve を自動投函。既定オフ | 触る回数そのものを減らす |
| 決定メモリ | 過去の approve / reject と理由（`decisions/` の DR）を索引し、類似の判断を提示。繰り返す差し戻し理由は `policy.md` へ昇格提案 | 同じ手戻りを二度させない |
| 再発失敗のクラスタリング | 失敗シグネチャ（同一 verify・同種エラー・同一ファイル）でまとめ、per-task の retry でなく charter / policy レベルで一度に直す動線へ | systemic な問題の発見 |
| メトリクス | 手戻り率・retry 分布・blocked 滞留・lead time・自動承認率の集計ビュー | 改善の効き先を決める計器 |
| 要対応キューのキーボード操作 | `a`=承認 / `r`=差し戻し / `h`=保留 / `j,k`=前後 | 一掃の高速化 |
| バッチ操作 | 同種の複数要対応を選択して一括承認・保留 | 同上 |
| 変更ダイジェスト | 前回閲覧以降の新規要対応・完了・失敗を 1 枚に要約 | 復帰コストの低減 |
| インタラクティブ attach | `node-pty` ＋ `xterm.js` での tmux attach（現状は読み取り専用の画面表示のみ） | 深掘り時の操作性 |

自動承認・決定メモリ・クラスタリングは、AI のリスク評価とメトリクスが土台になるので後段に置く。

**壊れ方が配布後にしか出ない箇所への護り**: `index.html` はバンドラを使わないので CSS / JS を
相対パスで直接読み、中にはアプリのソースツリーの外（`node_modules/diff2html/…`）を指すものがある。
開発起動では node_modules がそこに在るため気づけず、`build.files` から漏れていると**パッケージ版
だけ差分ビューが白紙**になる。electron-builder が本番依存を暗黙に含めるかどうかに配布物を賭けず、
`build.files` へ明示したうえで、`test/packaging-assets.test.js` が `index.html` の参照と同梱指定の
対応を機械的に突き合わせる。

---

## 付録 A. 関連文書

**使い方の正典**: [`tools/agent-dashboard/README.md`](../../tools/agent-dashboard/README.md)。
画面ごとのデータソース・操作・セットアップはこちら。

**画面ごとの詳細設計**は `docs/plans/` に日付つきで置かれている。主なもの:
[概要優先 UI](../plans/2026-07-14-agent-dashboard-overview-first-ui-design.md) ／
[詳細タブ UI](../plans/2026-07-14-agent-dashboard-detail-tabs-ui-design.md) ／
[Doctor](../plans/2026-07-14-agent-dashboard-doctor-design.md) ／
[失敗診断](../plans/2026-07-16-agent-dashboard-failure-diagnosis-design.md) ／
[利用者中心 UI](../plans/2026-07-16-agent-dashboard-user-centered-ui-design.md) ／
[ミッション詳細 UI](../plans/2026-07-18-agent-dashboard-mission-detail-ui-design.md) ／
[全体設定ページ](../plans/2026-07-19-agent-dashboard-global-settings-page-design.md) ／
[オーケストレーションとトークン予算](../plans/2026-07-19-agent-dashboard-orchestration-token-budget-design.md) ／
[セッション開始コマンド](../plans/2026-07-20-agent-dashboard-session-commands-design.md) ／
[参加 UI](../plans/2026-07-20-agent-dashboard-participation-ui-design.md) ／
[本番化計画](../plans/2026-07-21-agent-dashboard-production-hardening-plan.md)。

**構造を固定しているテスト**（設計判断の実体）:
`test/no-git-writes.test.js`（§3.1）／ `test/feature-split.test.js`（§3.2）／
`test/discover-engine.test.js`（§3.3）／ `test/needs-notify.test.js`・`test/needs-sla.test.js`（§7）／
`test/packaging-assets.test.js`（配布物の取りこぼし、§8）。

本書は 2026-07-26 に、次の 3 本を統合して作った。旧ファイルは削除済み。

| 旧ファイル | 統合先 |
|---|---|
| `agent-dashboard-feature-split-design.md` | §3.2 / §4 / §4.1 |
| `agent-dashboard-kiro-loop-terminal-design.md` | §5.1 |
| `agent-dashboard-project-ux-improvements.md` | §2 / §3.4 / §6 / §7 / §8 |
