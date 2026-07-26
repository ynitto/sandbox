# agent-project 設計書

> 最終更新: 2026-07-26（実装 `tools/agent-project/agent_project/` と突き合わせ済み）
> 実装: `tools/agent-project/`（本体 33 断片 + `resident/` 5 モジュール・約 15,000 行）、テスト 988 件
> 関連: [agent-flow 設計書](./agent-flow-design.md) ／ [codd-gate 設計書](./codd-gate-design.md) ／ [単一常駐コントローラ設計](../plans/2026-07-24-single-resident-controller-design.md)
>
> 旧 `docs/plans/2026-07-22-agent-project-multi-node-daemon-design.md`（複数ノード分担実行）は
> 本書の「複数 PC で 1 つのバックログを分担する」節へ統合した。

## TL;DR

agent-project は、バックログを優先順位付けして実行へ委譲し、検証に通ったものだけを done に確定し、通らなかったものを積み直す制御層です。人がプロンプトを毎回投げなくても回り続け、人の判断が要るところだけを差し戻します。

主要な決定は 3 つです。第一に、done を確定できる根拠は verify コマンドの終了コード 0 だけで、エージェントの自己申告も設定もこれを迂回できません。第二に、状態はすべてプロジェクトルート直下のファイルに置き、複数 PC への共有は git そのものを同期路として使います。第三に、実行は agent-flow へ丸ごと委譲し、この層はルーティングとゲートと記録に徹します。

却下した主要案は、タスク状態を持つ中央サーバ（1 プロジェクト = 1 ディレクトリの可搬性が失われる）と、LLM に done を判定させる設計（検証の意味が消える）です。

読むべき人は、agent-project を運用する人、charter を書く人、agent-dashboard や agent-flow 側から連携する人です。日々の操作手順だけなら `tools/agent-project/README.md` と `GUIDE.md` で足ります。

## 背景と課題

エージェントに仕事を任せるとき、一番きついのは「投げ続けること」です。1 タスクごとに人がプロンプトを組み立て、結果を読み、次を決める。これでは人が律速になり、夜間や外出中は止まります。

かといって全自動にすると、別の壊れ方をします。エージェントは「できました」と言い切る。検証の当てがないまま done が積み上がり、あとでまとめて壊れていたことに気づく。止まらないループが予算を焼き切る。人が気づいたときには、何がどういう根拠で done になったのか追えない。

解くべき問いは「人の関与を減らしながら、done の意味を保ち、必ず止まり、判断の履歴が残るループをどう作るか」です。答えが、検証を唯一の門にして、人の判断だけをファイル越しに往復させる、この設計です。

### 目標

- バックログが空になるか予算が尽きるまで、人の介入なしに回り続ける
- done の根拠が後から機械的に再現できる（誰が何を verify して通したか）
- 複数の PC が同じバックログを分担しても、同じタスクを二度実行しない
- 毎晩シャットダウンする PC でも、翌朝続きから再開できる

### 非目標

- 汎用のタスク管理。バックログは「エージェントが消化する待ち行列」で、人の ToDo 管理ではありません
- タスクの実行そのもの。分解と並列実行と成果物の生成は agent-flow の仕事です
- リアルタイム性。ループは秒単位ではなくタスク単位で動きます
- 複数プロジェクトの統合ビュー。束ねた可視化は agent-dashboard が git 越しに担います

### 破ってはいけない 5 点

外周に機能を足すときも、これらは緩めず安全側へ倒します。

1. done は verify（プロジェクト層では acceptance）の終了コード 0 でしか確定しない。投入経路もスキルも設定も敵対的レビューも、自己申告の done を作れない。安全ゲートはタスクを足すか止めるかの方向にしか働かない
2. 必ず有限回で止まる。内側は drained と予算、上位ループは改善サイクル上限と停滞検知。`--watch` でも idle 中はエージェントを起動しない
3. 人の policy がエージェントの提案に勝つ。設定ファイルは既定のレイヤで、`policy.md` と決定記録の優先には介入しない
4. 標準ライブラリだけで動く（PyYAML は任意。無ければ JSON）
5. 知能は委譲し、こちらは決定的なファイル操作で完結する

## 主要な設計判断

### 1. done を確定できるのは verify の終了コードだけにする

**判断**: タスクは `- verify:` にシェルコマンドを持ち、その終了コード 0 だけが done の根拠になる。verify を書けない人のために `- accept:`（自然言語）からエージェントに合成させる経路と、`- verify_template:`（決定的テンプレ）を用意するが、合成されたものも最終的には同じ終了コードで判定される。

**文脈**: エージェントは成果物を出しつつ「完了しました」と言う。その言葉を信じると、動かないコードが done になって archive に積まれ、後続タスクがそれを前提にして連鎖的に壊れる。

**選択肢と却下理由**: LLM に成果物を読ませて合否を判定させる案は、判定者が生成者と同じ弱点を持つので、間違いが相関する。人が毎回目視する案は、そもそも自動化の目的を潰す。終了コードなら、後から誰でも同じコマンドを叩いて再現できる。

**トレードオフ**: verify が書けないタスクは done にできず、人の検収（review）へ回ります。合成 verify が `grep` 一発のような退化した検査になる危険もあるので、恒真式の決定的スクリーン、act 前のツリーでも PASS するかの red-green 検査、確認回数を増やす flake 判定を重ねています。

**確信度**: 高い。この判断がこの設計の存在理由そのものです。

### 2. 状態はプロジェクトルート直下のファイルに置き、git を同期路にする

**判断**: 1 プロジェクト = 1 ディレクトリ = 1 プロセス。`backlog/` `needs/` `decisions/` `archive/` などをルート直下に平たく置き、ルート自身を git リポジトリにして origin へ push する。複数 PC で共有するときは同じリポジトリを clone するだけ。

**文脈**: 分散の相手は常時稼働のサーバではなく、それぞれ違う時刻に落ちる複数の PC です。人も同じリポジトリを開いて `needs/<id>.md` に返事を書き込みます。

**選択肢と却下理由**: 中央にタスク DB を置く案は、サーバの運用コストに加えて「プロジェクトを丸ごと持ち歩く・複製する・アーカイブする」が難しくなる。専用の管理クローン（`<root>/.state-git`）を経由して subdir だけを鏡写しする案は実装して運用しましたが、書き手が増えたぶんだけ除外規則が食い違い、tracked なのに commit されないファイルが生まれて同期が復旧不能に詰まりました。いまは direct 一本で、ルート自身のリポジトリへ直接コミットして push します。

**トレードオフ**: ルートが無関係な既存リポジトリの内側にある構成では同期できません（そこで `git init` すると nested repo になり、外側の `git add -A` が壊れる）。その場合は同期を諦めるか、agent-project 専用の状態 worktree へ逃がします。

**確信度**: 高い。管理クローン方式は実際に壊れて撤去した経緯があります。

### 3. 常駐は PC に 1 本だけ持ち、プロジェクトループはその子にする

**判断**: `agent-project serve` が host.yaml に宣言されたプロジェクトを読み、それぞれに `run --watch` を子プロセスとして起こして監督する。子の起動・再起動・隔離・計画停止はすべて親が決める。周期仕事（amigos 参加、flow 参加、板、gc）も親の tick 表が持つ。

**文脈**: 以前は agent-project・agent-flow・agent-amigos がそれぞれ常駐しており、同じ PC に 3 つのループが回っていました。設定も生存監視も自動更新も三重で、どれが動いているのか運用者が把握できなくなっていました。

**選択肢と却下理由**: 各ツールの常駐を残したまま起動だけ揃える案は、三重ループの問題をそのまま残す。逆に 1 プロセスに全部を詰める案は、1 つの不具合が全プロジェクトを巻き込む。親子に分けると、子が死んでも親が指数バックオフで起こし直し、繰り返し死ぬ子だけを隔離できます。

**トレードオフ**: 親の tick が詰まると全体が止まるので、tick 内で周期を超えうる仕事（run の実行、amigos の手番）を絶対に実行しない規約を置きました。それらは `NodeWorkerPool` へ投げます。親自身のハングは self-watchdog（各 tick の心拍が `period + timeout + 猶予` を超えたら自プロセスを abort）が起動系の再起動に載せます。

**確信度**: 高い。ただし移行のとき、旧常駐のループにぶら下がっていた処理がいくつか呼び出し元を失いました。後述の棚卸しを参照してください。

### 4. 複数 PC の調停は git の fast-forward push を CAS として使う

**判断**: 状態リポジトリを共有する複数ノードは、`remote HEAD` を親にして変更を作り、fast-forward の push が通ったかどうかを compare-and-swap の成否として扱う（`state_transaction`）。誰が全体を進めるか（controller）はリース付きのファイルで持ち回り、タスクの実行権は owner/token/generation の 3 つ組を fencing token として持つ。

**文脈**: 常時稼働の PC がなく、各 PC は違う時刻に夜間停止します。復帰後は最初に起動した適格ノードが自動的に制御を再開できる必要があります。

**選択肢と却下理由**: 中央のロックサービスを置く案は、そのサービスが動いていない時間帯に全体が止まる。ファイルの mtime やホスト名で調停する案は、時計のずれとクロックスキューで二重実行を許す。git の push は既にアトミックで、リポジトリ自体が共有路なので、追加の部品を持たずに済みます。

**トレードオフ**: 1 回の調停にネットワーク往復が要ります。settle の直前には fencing を検証しますが、結果は `ok` / `lost` / `unknown` の 3 値で返します。リモートに届かなかっただけの `unknown` を「奪われた」と同一視して成果を捨てると、ネットワークが不安定な PC で作業が消えるからです。`unknown` は破棄も自動採用もせず、人の判断へ隔離します。

**確信度**: 中程度。単一 PC 運用では経路ごと無効化されます（`_coordination_active` が origin と peer の実在を見て判定する）。

### 5. 人へ送る前に、三段で自動解決を試みる

**判断**: verify が通らず上限まで積み直しても収束しないタスクは、いきなり人へ出さない。まず過去の決定記録から類似の learn を探して適用し（決定的）、次にエージェント CLI の裁定ゲートに「これは人が要るか」を判定させ、どちらも不可なら `needs/<id>.md` を書いて人へ送る。

**文脈**: 人へ送るコストは「読む・考える・書く」の合計で、1 件あたり数分から数十分です。同じ種類の詰まりを何度も送ると、人は読まなくなります。

**選択肢と却下理由**: 全部を人へ送る案は、人が律速になって自動化の意味が薄れる。全部を自動解決する案は、判断の質が担保できないうえ履歴が残らない。三段にすると、繰り返す詰まりは 1 段目で消え、新種だけが人に届きます。

**トレードオフ**: 決定記録が育つまでは 1 段目が効きません。効いた learn は `rules.md`（全タスクへ常時注入）へ、さらに効いたものは ltm-use の長期記憶へ、と昇格させて再利用の幅を広げます。

**確信度**: 高い。判断の履歴が `decisions/` に append-only で残るので、後から効き目を数えられます。

### 6. 実行は agent-flow へ丸ごと委譲し、この層はゲートと記録に徹する

**判断**: タスクの分解、並列ワーカー、内側の検証ループ、成果物リポジトリへの push は agent-flow の責務。agent-project が持つのは「どのタスクを・どの書込先で・どこで実行するか」の決定と、返ってきた成果を検証して確定する部分だけ。

**文脈**: 両方を 1 つのツールに入れると、優先順位付けの変更が実行の並列度に影響するような、無関係なはずの結合が生まれます。

**選択肢と却下理由**: 実行も自前で持つ案は、agent-flow が既に持っているタスクグラフと claim プロトコルを二重に実装することになる。逆に agent-project を薄いラッパにする案は、検証ゲートと決定記録の置き場がなくなる。境界を「1 run = 1 タスク = 1 書込先」に引くと、両者の語彙が噛み合います。

**トレードオフ**: プロセス境界を越えるので、失敗の情報は agent-flow の `result --json` から読み直します。`agent_flow` を import はしません（別 venv・別バージョンで動く前提）。

**確信度**: 高い。

## 正準ループ（`run` の 1 サイクル）

この節の抽象度は概要です。個々の関数には触れません。

```
 ── サイクル予算が残る間くり返す ──────────────────────────────────
 S7 収束判定   予算（サイクル数・実時間・トークン・コスト・ソフト上限）超過なら停止
 S0 取り込み   needs の返事・commands の指示・inbox のドロップ・外部 intake を取り込み、
               triage で inbox を ready へ昇格し、verify を用意し、spec 前段を前置する
 S1 選択       優先順位付け（planner）→ policy で上書き → 依存未達と report を除外
               → 先頭から concurrency 件を原子的に claim
 S2 実行       要求文を組み立てて agent-flow へ委譲（local か board）
 S3 検証       verify → 回帰 → パス保護 → 進捗 → flake の各ゲートを通す
 S4 判定       done（archive + 納品書）／ review（人の検収待ち）／ retry（積み直し）
 S5 送出       人へ送る前に learn 適用 → 裁定ゲート → needs/<id>.md 生成
 S6 自走       完了タスクから派生タスクを backlog へ
 ── 脱出後 ── 通知、learn の昇格、バス掃除、run-log 追記
```

止まる理由は 5 つに限られます。消化しきった（drained）、サイクル数・実時間・トークン・コストのいずれかの上限、ソフト上限（throttle）です。throttle に当たった `--watch` は以降 report へ降格し、実行を止めて監視だけ続けます。

`--watch` はパス終了後もプロセスを残しますが、idle 中にエージェントは起動しません。消化できるタスク、新しい inbox、人の指示、確定したフィードバックのいずれかを FS ポーリングで検知したときだけ次のパスを起こします。ここで「起こす条件」と「取り込む条件」を同じ述語にしてあるのが要点で、ずれていると何も処理しない空パスを無限に回します。

## プロジェクト層（charter からバックログを作る）

この節の抽象度は概要です。

`<root>/charter.md` があると、`run` は目標駆動のモードに入ります。charter が持つのは目標、制約、前提、成果物、そして `## acceptance`（受入 verify）です。1 パスは分解（plan）、消化（execute = 正準ループ）、評価（evaluate）の 3 段で、acceptance が全通するまで改善タスクを生成して反復します。

acceptance がプロジェクト done の唯一の根拠であるのは、タスクの verify と同じ理屈です。未達の acceptance は、それ自体を verify とする改善タスクへ機械的に変換します。的が外れないのは、生成された verify が未達の acceptance そのものだからです。

収束したら `needs/<pid>.md` に milestone を書いて人の検収に出します。プロジェクトの done は人が確定します。反復は改善サイクル上限、累計コスト上限、そして「acceptance の PASS 数が増えない連続回数」（停滞）で必ず止まります。

`charters/` に複数の charter を置くと、同じルートで複数バージョンを並行に進められます。ルートの `charter.md` に `## master` を付けるとマスター憲章になり、分解されずに全バージョンへ継承されます。

## 複数 PC で 1 つのバックログを分担する

この節の抽象度はコンポーネントです。

同じ状態リポジトリを複数 PC が clone し、それぞれで常駐体を動かすと、ノード間で仕事を分担できます。前提は「常時稼働の PC はない」「各 PC は違う時刻に夜間停止する」「全 PC が同時に止まる時間帯があってよい」です。

**controller** は全体を進める役です。リース付きのファイルを CAS で獲り、心拍で延長します。リースが切れれば次に起きたノードが引き継ぎます。長い act の最中もリースが切れないよう、別スレッドが延長し続けます。

**割当** は controller が決めます。生存が観測されているノードの `ready + doing` 件数が最小になるよう、未割当の ready を決定的に配ります。割り当てられていないタスクは他ノードが消化しません（`task_runnable_here`）。

**fencing** は実行権の同一性を確かめます。claim のとき owner/token/generation を書き、settle の直前に remote の正本が同じ 3 つ組の doing であることを確認します。`ok` なら確定、`lost` なら奪われたので成果を捨てて正本へ戻す、`unknown`（リモートに届かない）なら成果を捨てずに人の判断へ隔離します。

**夜間停止** は 2 種類を区別します。予定停止は、`availability` の宣言に従って drain（新規 claim を止め、走っているものを終わらせる）へ入り、猶予を使い切ったら親が子を止めます。この停止は死亡回数に数えないので、毎晩の停止を繰り返しても隔離されません。突然停止は controller リースと claim リースの失効で他ノードが引き継ぎます。

node_id は PC の身元で、板（agent-board）とプロトコル上の名義です。切り替えるときは静止点でしか行えません（旧名義の claim・bid・status が孤立して二重入札の温床になる）。`agent-project doctor --node-id-cutover <旧 node_id>` が事前チェックを持ちます。手順は [node-id-cutover ガイド](../guides/node-id-cutover.md)。

## コンポーネントと責務

この節の抽象度は実装です。断片（fragment）と責務の対応だけを載せます。

| 断片 | 責務 |
|---|---|
| `model` / `policy` / `decisions` | Task の読み書き、cohort、policy と自律度、決定記録と learn |
| `state` / `rules` / `brief` | 状態 worktree、`rules.md` への昇格、run ブリーフの蓄積と退役 |
| `needs` / `prioritize` / `verify` | 人への差し戻しと取り込み、優先順位付けと裁定、検証ゲート |
| `request` / `flow` / `board` | 要求文の組み立てとルーティング、agent-flow 連携、委譲公示板への post |
| `config` / `batch` / `mr` | 納品書と証跡、並列消費と claim、タスク MR と settle の分岐 |
| `stategit` / `coordination` | direct 同期（3-way 裁定）、CAS transaction と controller リース |
| `loop` / `commands` / `charter` / `plan` / `project` | 正準ループ、人の操作、charter の解析、分解、プロジェクト層 |
| `doctor` / `update` / `configfile` / `cli` | 診断、自己更新、設定解決、コマンド振り分け |
| `resident/` | 周期表（scheduler）、子の監督（supervisor）、ノード直轄ワーカー（worker）、gc、状態契約（status） |

`resident/` だけは通常の Python パッケージで、単体 import と単体テストができます。それ以外の 33 断片は共有名前空間へ順に exec して合成する方式なので、`from agent_project.<断片> import …` は成立しません（合成前は他断片のシンボルが未定義）。外から呼びたい機能には CLI の入口を用意します。

## 常駐一本化で拾い直したもの（2026-07-26）

旧常駐のループを畳んだとき、そこからしか呼ばれていなかった処理がいくつか呼び出し元を失いました。関数単体のテストは通り続けるので、経路が死んでいることに気づけません。今回の棚卸しと処置です。

**node_id 切替の事前チェックに入口が無かった。** 実装計画は「doctor に切替前チェックを実装」と書き、検査本体（`doctor_node_id_cutover_findings`）もテストもありましたが、doctor から呼ばれていませんでした。手順書は代わりに `from agent_project.doctor import …` を案内しており、これは断片の単体 import なので即 `NameError` になります。手順どおり叩いても動かない検査でした。`doctor --node-id-cutover <旧>` を入口として足し、板と amigos バスの場所・新名義は host.yaml から引くようにしました。

**合成 verify の継続行が結合されていなかった。** 行末バックスラッシュを 1 つの論理コマンドへ畳む関数はありましたが、候補を選ぶ側が呼んでいませんでした。結果、`pytest -q \` のような途中で切れたコマンドが採用されます。コードフェンス内は構文チェックを課さないので素通りし、壊れた verify がそのまま done の唯一の根拠になります。実行すれば必ず落ちるので、そのタスクは永久にリトライと人送りを繰り返します。候補選定の前に結合するよう直しました。

**agent-flow へ state_git を注入する経路が消えていた。** バスを root の外へ置いた構成向けに、agent-flow の state-git 設定を CLI で注入する関数がありましたが、注入していたのは旧 flow daemon の起動でした。いまは `flow_config` 経由で agent-flow 自身の yaml が持つのが正なので、関数ごと削除しました。

**`state_git_branch` / `state_git_subdir` が効かなくなっていた。** direct 同期はルートが開いているブランチへ push し、リポジトリ内のサブディレクトリ分離を使いません。設定キーと CLI フラグは残っていて、書いても無視されるだけでした。削除しました。

**その他の取り残し。** `location` の説明に残っていた `daemon` / `remote`（いまは `local` / `board` の 2 つ）、それに対応する納品書の分岐、旧停止経路のプロセスグループ送信、`capture_insight` に置き換わった一括追記、重複したフィールド定義を削除しました。

~~残した既知の窓が 1 つあります。板の `nodes/<pc>.json`（ノード能力宣言）を書く実装がありません。~~
→ **塞ぎました（2026-07-26・下記「板の請負」）。**

## 板の請負 — ノードの持ち物になった宣言（2026-07-26）

詳細は [S8/S9-4 詳細設計](../plans/2026-07-26-s8-s9-4-board-ui-and-doctor-chat-detailed-design.md)。設計上の位置づけだけをここに残します。

**板 tick（30 秒）を親の周期表に足しました。** やることは 3 つ——板の同期、ノード能力宣言 `nodes/<pc>.json` の書き出し、ノード宛て指示（`~/.agents/commands/`）の取り込みです。**入札の自動判断はここに置きません。** 自動入札は従来どおり各プロジェクトの `participate`（agent-flow / agent-amigos）が担います。同じノードに 2 つ目の入札主体を置くと、二重落札を防ぐ規則が 2 実装になるからです。ここが書く入札は「人が押した」分だけ。

**入札選別の宣言はノードの持ち物になりました。** host.yaml の `repos` / `tags` / `agent_cli` が正典で、agent-flow 設定の `board_repos` / `board_tags` / `board_agent_cli` は明示上書きへ降格しました（S1 が host.yaml 専有と決めた群が、agent-flow 側に残っていた取りこぼしの解消）。判定規則そのものは `agentcore.board.eligible` の 1 実装です——agent-flow と agent-amigos が「同じ仕様・別実装」で持っていて、片方だけ育つと同じ公示が経路によって拾えたり拾えなかったりします。

**dashboard は板へ書きません。** 中止・落札・手動入札はノード宛て指示ドロップ（`schemas/agent-node-command.schema.json`）として投函され、板へ書いて push するのは常駐体だけです。プロジェクト配下の `commands/` ではなくノードスコープに置くのは、**板がプロジェクトに属さない**ため——プロジェクトを 1 つも持たない PC からも板を操作できる必要があります。

**残る窓は「ノード直轄実行」です。** 落札した仕事を実行するのは、いまも各プロジェクトのバス経由（`poll_board` → inbox → `NodeWorkerPool`）です。プロジェクトを 1 つも持たないワーカーノードは落札しても行き先がありません。この事実は `engine/status.json` の `board.intake_projects` に出しており、dashboard はそれを見て手動入札のボタンを理由付きで非活性にします（操作だけ増えて実行できない状態を作らない）。実装は[実装計画 §7 R2b](../plans/2026-07-24-single-resident-controller-implementation-plan.md)。

## 付録

### A. ファイル構成

すべてプロジェクトルート直下に平たく置きます。

```
<root>/
  charter.md            人   目標・制約・受入 verify・links
  repos.yaml|json       人＋系 リポジトリレジストリ（手書きが正・無ければ charter から生成）
  policy.md             人   順位・実行先・安全ゲートの上書き
  backlog/<id>.md       人＋系 タスク本体（1 ファイル = 1 タスク）
  inbox/                外部  取り込み待ちドロップ口（.json / .md）
  commands/<name>.json  外部  指示のドロップ口（CLI と同一ロジックで実行して消す）
  claims/<id>.lock      系   実行権の原子的クレーム
  needs/<id>.md         系→人→系 判断待ち・検収待ちの通知とフィードバック欄
  decisions/<id>.md     系   決定記録（append-only。learn / avoid の材料）
  brief/<id>.md         系   run ブリーフ（タスク内で蓄積し、完了時に納品書へ退役）
  rules.md              人＋系 プロジェクトルール（全タスクへ常時注入）
  archive/<id>.md       系   done の保全と納品書
  DELIVERY.md           系   納品一覧（受領書）
  specs/<id>/           系   spec 前段の成果（spec.md / design.md / tasks.md）
  context/<repo>.md     系＋人 リポジトリ理解（repo-map）
  autonomy/<track>.json 系   track の自動昇格状態
  project.json          系   プロジェクト層の収束状態
  journal.md            系   人間可読のサイクルログ（閾値でローテーション）
  run-log.jsonl         系   構造化 run-log（run ごと 1 行）
  status.json / status/ 系   生存信号（単一ファイルとノード別）
  paused.json           系   一時停止マーカー
  bus/                  系   agent-flow の run 状態
~/.agents/
  engine/status.json    系   常駐体の心拍・子状態・同期健康（dashboard が読む唯一の入口）
  agent-project.host.yaml 人 この PC が持つプロジェクトの宣言（単一ソース）
```

### B. タスクと決定記録の書式

タスクは Markdown 1 ファイル。id はファイル名が正です。

```markdown
## <id>: <タイトル>
- status: inbox | proposed | ready | doing | offloaded | review | blocked | done
- verify: <終了コード 0 で PASS のシェルコマンド>
- accept: <自然言語の完了条件（verify が書けないとき）>
- priority: <整数・大きいほど高>
- after: <依存タスク id（カンマ区切り）>
- review: human            <検収を要する>
- level: report | assisted | unattended
- why / desc / scope / out_of_scope / constraints / hints / demo   <誘導記述>
```

決定記録は append-only で、`- learn:` 行が横断学習の材料になります。同じ種類の詰まりに二度目からは自動で効き、効いた回数が閾値を超えると `rules.md` へ、さらに ltm-use へ昇格します。

### C. CLI と設定

| コマンド | 用途 |
|---|---|
| `serve` / `status` / `worker` | 常駐体の起動・状態表示・ワーカーノード（サブコマンド省略時は `serve`） |
| `run` | 正準ループ。charter があれば目標駆動へ入る。`--watch` で常駐 |
| `enqueue` / `triage` / `needs` / `impact` | 投入、優先順位付けのみ、判断待ちの表示、依存の影響範囲 |
| `approve` / `hold` / `reprioritize` / `revise` / `reject` / `resume-run` | 人の操作（すべて決定記録に残る） |
| `replan` / `board-offload` | charter からの再分解、委譲公示板への手動委譲 |
| `stats` / `audit` / `runlog` / `doctor` / `gc` / `update` | 計測、Loop Readiness 採点、ログ、診断、掃除、自己更新 |
| `promote` / `rot` | learn の長期記憶への昇格、腐ったタスクの検出 |
| `flow-participate` / `flow-run` | 常駐体の内部配線（help には出さない） |

設定は 3 層です。PC 固有の値は `agent-project.profile.yaml`、プロジェクト共有の値は `agent-project.yaml`、この PC が持つプロジェクトの宣言は `agent-project.host.yaml`。優先順位は CLI 引数、profile（PC 固有キーのみ）、共有設定、組み込み既定の順です。キーの一覧と既定値は `CONFIG_DEFAULTS`（`agent_project/configfile.py`）が正典で、注釈つきの実例は `tools/agent-project/agent-project.yaml.example` にあります。

実行の委譲先は `--location`（既定 `auto`）で決まります。`local` は agent-flow の単発 run、`board` は委譲公示板への post（非ブロッキング）。`auto` は offload ポリシーに一致しかつ板が設定されていれば `board`、それ以外は `local` です。

### D. テスト

`tools/agent-project/tests/` に 988 件。共有の前置きは `_shared.py` にあり、エージェント CLI なしで全件が通ります。

```bash
python3 -m pytest tools/agent-project/tests -q
```

`resident/` は通常パッケージなので単体 import でテストできます（`test_resident_scheduler.py` / `test_resident.py` / `test_resident_status.py`）。分散の検証（CAS transaction、controller リース、fencing の 3 値、割当）は `test_state_git.py` と `test_coordination.py` が、実ローカルリポジトリを使って行います。
