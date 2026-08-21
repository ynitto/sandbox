# CHANGELOG

All notable changes to this project are documented in this file.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) — versions use [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### agent-dashboard の設計書を改訂し、仕様書を分離した

agent-flow・agent-project・agent-loop と同じ扱いを agent-dashboard にも通す。792 行の設計書に
判断 6 個と、合成契約・設定キー・IPC 一覧・画面ごとの表示規則（ダイアログの伸縮まで）が同居し、
「なぜそう決めたか」と「今どういう契約か」が同じ場所で混ざっていた。実装（制御面 9 つ・
`src/` 135 ファイル・約 43,600 行）と `docs/plans/` の 56 本に照合し、`slop-police` の設計書規約で
組み直したうえで、契約を新設の仕様書へ移す。

- **`docs/specs/agent-dashboard-spec.md` を新設**。feature 記述子と載せる順番、IPC 161 本の内訳、
  renderer の読み込み順契約と登録簿 3 つ、設定キー表（base ＋ 制御面 9 つ）、読むファイルと
  書くファイルの一覧、設計 run / 実装 run の契約、正典の写し 4 つとそれを縛るゴールデンテスト、
  構造を固定しているテスト、配布（`build.files` / `extraResources`）を収めた
- **設計書を 792 行から 387 行へ、判断を 6 個から 5 個へ**。3.1（git 書き込み撤去）と
  3.4（AI は下書きまで）と §5.2 の板への書き込み規則は**同じ判断の 3 つの適用**なので、
  「dashboard は状態の書き手にならない」1 個へ畳んだ。§5 の制御面ごとの長い散文と §6 の UI 表示
  規則は仕様書と README へ振り分け、設計 run と実装 run の分離は判断ではなく 1 節へ落とした
- **付録を整理**。「画面ごとの設計判断の要点」は却下案を伴うものだけに絞り、更新対象の文書
  （仕様書を先頭に）を付録 A として独立させた

### agent-dashboard: テスト 3 ファイルが `npm test` から漏れ、一度も実行されていなかった

`tools/agent-dashboard/test/` に置かれているのに `package.json` の `scripts.test` へ登録されておらず、
CI を含めてどこでも走っていないテストが 3 つあった。`npm test` は個々のファイルを直列に並べた
1 本のコマンドで、ディレクトリを走査しない——置いただけでは実行されない。

- `budget-summary-parity.test.js`（3 件・2026-08-14 追加）… status 射影の reason_codes と
  contract_version を schema 正本と突き合わせる。ファイル自身のコメントに「`node test/...` で走る」と
  書きながら、走らせる側へ足されていなかった
- `flow-interaction.test.js`（6 件・2026-08-12 追加）
- `note-tasking.test.js`（4 件・2026-08-12 追加）

3 つとも現状のコードで通ることを確認したうえで `scripts.test` へ登録した。あわせて仕様書の付録に
「テストファイルを足したら `scripts.test` へも足す」ことを明記した。

### agent-dashboard: 実装と食い違っていた記述

- **`src/features/agent-loop/` は存在しない**。README がループの端末ビューをこのパスで説明していたが、
  実体は `src/features/routines/` である
- **routines の IPC は 4 本ではなく 6 本**。設計書は `listSessions` / `capture` / `state` / `send` の
  4 本と書いていたが、`queue` / `queueMessage` が増えている
- **制御面は 8 つではなく 9 つ**。設計書の全体像は `adhoc-flow` を落としており、`preparation` は
  `src/features/` 配下にありながら feature 記述子を持たない共有モジュールで制御面ではない、という
  区別も書かれていなかった。ディレクトリ数 10・制御面 9 として仕様書に明記した
- **テストは 84 ファイルではなく 98 ファイル**
- `test/no-git-writes.test.js` の由来コメント「制御面が 2 つから 7 つへ増えた」を 9 へ更新した
- 制御面の README 4 本が参照していた設計書 §5 / §5.1 は改訂で移動したため、§4 と仕様書へ張り直した

### agent-loop の設計書を改訂し、仕様書を実装に追いつかせた

agent-flow・agent-project と同じ扱いを agent-loop にも通す。517 行の設計書に判断 5 個と機能 7 件の
節が並び、各機能の節が設定キー表・スキーマ・CLI 引数まで抱えて仕様書と二重化していた。実装
（`agent_loop` 27 モジュール・約 12,700 行）と `docs/plans/` に照合し、`slop-police` の設計書規約で
組み直したうえで、契約は仕様書へ寄せる。

- **設計書を 517 行から 291 行へ**。判断 5 個は維持し、機能 1〜7 の節は「機能ごとの『なぜ』」
  1 節へ畳んだ——各機能に残すのは採用理由だけで、キー表と契約は仕様書に一本化した。
  「未実装として残っているもの」を新設し、置いたが繋いでいない箇所を明示した
- **仕様書に、実装にあって文書に無かったものを追記**。動的インターバルの遷移表（activity /
  idle / error の 3 状態と、error 状態が現状どこからも遷移してこないこと）、レイヤ 2
  （`tool-loop`）とレイヤ 3（`single-shot`）を `headless_autonomy` で分ける表、CLI とモデルの
  差し替えが効く境界、グローバル指示（agent-instructions）の pull 注入契約、statemachine の
  50 ステップ上限と `health.check_interval_seconds`、テスト付録（44 ファイル・444 件）
- **`node.id` の既定を実装どおりに直した**。仕様書は連番と読める書き方だったが、実装は
  `uuid5(index, name)` で、ホストをまたいでも衝突せず同じ設定なら同じ値になる
- **対話コンソールとサブコマンドの区別を書いた**。`prompt-add` / `prompt-remove` は stdin
  コンソールのコマンドであってサブコマンドではなく、`slot-release` / `hook-event` は内部用

### agent-loop: 退役した kiro-loop 由来の名前が 5 か所残っていた

`kiro-loop` から `agent-loop` への改称は完了扱いだったが、利用者から見える文字列と内部識別子に
取りこぼしがあった。意図的に残す 2 種類（`session.json` の engine 値 `kiro-loop`＝読取互換のみ、
`~/.kiro/` のスロットとエージェント inbox の置き場＝稼働中の移設に実利が無い）以外はすべて処置した。

- `agent_loop/cli.py` のヘルプ 5 か所が「kiro-cli を定期プロンプトで自動操作する」と、特定 CLI
  専用のように読める文言だった。既定が kiro-cli なだけで `agent_cli` で差し替えられるため、
  「エージェント CLI」へ一般化した
- `agent_loop/scheduler.py` の動的ロード合成モジュール名 `kiro_loop_hook_*` /
  `kiro_loop_preflight` を `agent_loop_*` へ改名した
- `agent_loop/instructions.py` の由来コメントが一括置換で「旧 tools/agent-loop の同名実装を
  クローン」という自己言及になっていた（正しくは旧 `tools/kiro-loop`）
- `tools/agent-loop/README.md` の冒頭が kiro-cli 専用ツールのように読める書き出しだったのを
  一般化し、設計書・仕様書への導線を足した。移行の節は退役済みであることを見出しに書いた
- `docs/designs/gitlab-agent-sns-design.md` のロードマップが常時自律の担い手を `kiro-loop` と
  書いていたのを `agent-loop` へ直した

### agent-project の設計書を改訂し、仕様書を分離した

agent-flow と同じ扱いを agent-project にも通す。446 行の設計書に判断 9 個と仕様（CLI 表・設定・
ファイル構成・タスク書式）が同居し、日付つきの棚卸し記録まで挟まっていた。実装
（33 断片 + `resident/` 5 モジュール・約 24,400 行）と `docs/plans/` に照合し、`slop-police` の
設計書規約で組み直したうえで、仕様にあたる内容を新設の仕様書へ移す。

- **`docs/specs/agent-project-spec.md` を新設**。正準ループと停止理由の語彙、status の 10 値、
  コマンド表、設定の 2 ファイルと 4 群の層契約、タスク／要対応カード／検証計画と receipt／
  Execution Envelope／決定記録／委譲公示板／状態リポジトリのレイアウト／`engine/status.json`／
  知識観測の契約、予算と上限の表、効かない組合せを収めた
- **設計書の判断を 9 個から 5 個へ**。5・5-b・5-c・5-d（三段の自動解決・要対応カードの投影・
  削除と却下・強制完了）は判断ではなく「人との往復」の一節へ落とし、6（agent-flow への委譲）を
  判断 5 に繰り上げた。日付つきの 2 節（常駐一本化の棚卸し・板の請負）は、教訓を
  「決着済みの判断」へ、契約を該当節と仕様書へ振り分けて畳んだ
- **文書に無かった実装を追記**。Execution Envelope（実行前レビューの凍結点）、知識観測
  envelope、`verify_side_effects`、`force-complete` のコマンド表への掲載、断片表に抜けていた
  `knowledge` / `envelope`
- **数値を実装に合わせた**。断片 31 → 33、行数 約 21,800 → 約 24,400、テスト 1,219 → 1,300 件、
  実行コマンドを pytest → CI・README と同じ `unittest discover`

### agent-project: フォージは GitLab 専用ではなくなっていた

`mr.py` は GitLab と GitHub の両方で MR/PR の作成・決着まで扱い、gitea / codeberg は検出して
1 回警告する形になっているのに、設計書は非目標に「GitLab 以外のフォージ実装」と書き、本文でも
「フォージ実装は GitLab のみ」と説明していた。実装に合わせて GitLab / GitHub の 2 つとし、
フォージ無し運用（dashboard のボタン決着が正式な契約）を一級市民として書き直した。
`tools/agent-project/README.md` の「`verify` をローカルで実行して PASS したものだけ done に確定」も
実装（receipt の検算）へ直した——内蔵の verify 直実行は P1-A8 で撤去済み。

### agent-project: `verify_side_effects` が設定しても効いていなかった

charter 達成条件の verifier プロンプトが `charter.side_effects` という**存在しない属性**を
`getattr` で読んでいた。`Charter` にそのフィールドは無いので常に None → 既定（workspace）へ落ち、
プロジェクト yaml に `network` と書いても制約文は 1 文字も変わらなかった。`remote_review` と同じ
「読み手が `getattr` の既定で庇うので静かに効かない」形で、`test_config_keys.py` の到達検査
（`CONFIG_DEFAULTS` → `Config`）は Config までしか見ないため捕まえられていない。

- 設定 `verify_side_effects` から解決するよう直し、回帰テストを 2 件追加した
- 撤去済み機能の残骸キー `verifier` / `verifier_skill` を `_INERT_PROJECT_KEYS`（警告して無視）へ
  移した。内蔵 LLM verifier は撤去済み（P1-A8）で読み手がどこにも無く、黙って受理すると
  「false にしたのに検証が走る」ように見える
- `delivery_review` の注釈が「review 到達時に MR を自動作成する」と書いたままだったのを直した
  （実装も設計も、MR/PR を作るのは人が `mr-create` を押したときだけ）

### agent-flow の設計書・仕様書を実装と突き合わせて全面改訂した

機能拡張のたびに節を足していたため、両書ともパッチワークになっていた。実装（`tools/agent-flow/`
31 断片・約 12,400 行）と `docs/plans/` の設計と全件照合し、`slop-police` の設計書規約
（結論先出し・却下案つきの判断・強弱・省略）で組み直す。

- **設計書の判断を 7 個から 5 個へ**。「通信はファイルだけ」と「状態はファイルの存在から導く」は
  同じ公理の表裏なので 1 判断に統合し、park & poll は判断ではなく実行の流れの一節へ落とした。
  判断の番号が変わったので、参照側（`cli.py` / `adhoc.js` / カスタマイズ地図）も付け替えた
  ——**旧「判断 7」は新「判断 5」**（振る舞いを変える口の 4 層）
- **文書に無かった実装を追記**。run の完了条件（終端 `verify` が緑・赤なら `[verification]` で
  failed 終端）、公開レコードと復旧 ref・`force-complete`、公開後の CI 取り込み
  （`ci_status_command` ほか・既定 off・読めない出力は `unknown`）、`repair_retry` /
  `prompt_table` などのオプトイン、inbox の `execution_overrides`
- **GitLab を中心から外した**。park & poll も `--close-issues` も executor 非依存の機構なのに、
  両書とも GitLab 委譲を前提に説明していた。承認待ちは `human` ノードと `plan_gate` で足りる
  ことを主経路として書き、同梱の `gitlab` プラグインは「推奨しないオプション」と明記した。
  `watch_interval` も `gitlab.` 固定ではなく `<executor>.` として書き直した
- **数値と一覧を実装に合わせた**。テストは 26 ファイル・約 900 件 → 30 ファイル・1,027 件、
  実行コマンドは pytest → CI・README と同じ `unittest discover`。`force-complete` を
  コマンド表へ、`pattern` / `split_policy` / `stub_sleep_max` を設定キー表へ、
  `AGENT_CI_*` を環境変数表へ追加した

### agent-flow: inbox 要求の `pattern` が設定ファイルに黙って負けていた

計画パラメータの優先順位は **CLI 引数 > inbox 要求 > 設定ファイル > 既定** と決めてあるのに、
`pattern` だけが専用分岐で「`args.pattern` が未設定なら要求の値を載せる」形だった。
`resolve_config` が先に設定ファイルの値を `args` へ埋めるため、`agent-flow.yaml` に `pattern` を
書いたノードでは dashboard が指定した標準フローが黙って無視されていた（`granularity` /
`split_policy` は `_cli_explicit` で正しく見分けていたので、`pattern` だけが取り残されていた）。

- `pattern` を `_INBOX_PLANNING_KEYS` へ移し、他の計画パラメータと同じ経路で解決する。
  ついでに語彙検査も効くようになり、未知のパターン名は受理の時点で `InboxRequestError`
  （従来は子プロセスの argparse が usage エラーで落ち、理由が子の stderr にしか残らなかった）
- 回帰テストを 3 件追加（設定ファイルに勝つ / CLI に負ける / 未知の名前を断る）

### 設定の要点を設計書へ転記し、カスタマイズ地図を作業記録へ移した

`docs/designs/workflow-customization-map.md` は 2026-08-19 の調査で作った作業時点の地図で、
恒久的な設計判断の置き場としては設計書索引に並ぶべきものではなかった。ここまでの整理
（穴 3 件の解消・CLI の層別整理）を反映したうえで、要点を設計書へ引き上げる。

- **`docs/designs/agent-flow-design.md` に判断 7「振る舞いを変える口を層で分け、層ごとに
  1 つの名前で通す」を追加**。本書の書式（判断 / 文脈 / 選択肢と却下理由 / トレードオフ /
  確信度）に合わせ、4 層（形・分け方・言い方・実行資源）、CLI オプション名と設定キーと
  inbox キーを揃える取り決め、優先順位（CLI 引数 > inbox 要求 > 設定ファイル > 既定）、
  効かない指定を受け取らない規律、`selection: "engine"` の役割分担を書いた
- **地図を `docs/plans/2026-08-20-workflow-customization-map.md` へ移動**し、設計書索引から
  外した。地図側には「設計の正典は設計書の判断 7」であることを明記し、見つかった穴 3 件の
  決着を表にまとめた
- コード内の参照（`cli.py` / `adhoc.js`）も設計書の判断 7 を指すよう付け替えた

### dashboard から分解の粒度・分割の単位を指定できるようにした

dashboard が run へ渡せる実行時指定（`execution_overrides`）は tier / agent_cli / model
＝**L4 実行資源だけ**で、`granularity` も `split_policy` も画面からは設定できなかった
（CLI と設定ファイル専用）。`docs/plans/2026-08-20-workflow-customization-map.md` が挙げた最後の穴。

- **層ごとに別のキーで運ぶ**: `execution_overrides` へ相乗りさせず、inbox のトップレベルに
  `granularity` / `split_policy` を置いた。あちらは「役割・工程ごとに誰が実行するか」、
  こちらは「run 全体をどう分けるか」で、適用単位が違う。キー名は agent-flow の設定キーと、
  値の語彙は CLI の `--granularity` / `--split-policy` とそのまま同じ
- **優先順位は CLI 引数 > inbox 要求 > 設定ファイル > 既定**。要求は run 単位の意思なので
  そのノードの `agent-flow.yaml` より強く、人がその場で打った CLI 引数には負ける。
  `resolve_config` が「CLI で明示されたキー」を控えるようにして両者を見分ける——既定が
  偽値でない（`granularity: auto` など）キーは、これが無いと区別できなかった
- **未指定はキーを書かない**。画面が対象フォルダの設定を黙って上書きしないため、投入側が
  「指定しない」を表現できる。`auto` は「complexity から導出する」という明示の選択として通す
- **語彙外の値は起動前に断る**（`InboxRequestError`）。`split_policy()` などの解決関数は未知値を
  既定へ丸めるので、素通しすると誤記が「指定したのに効かない run」として静かに走る。
  daemon のオンデマンド起動もフェイルクローズで、要求を残したまま理由をログへ出す
- 画面の入口は実行前の確認ダイアログの「分け方を指定する」。選べる値は main（agent-flow と
  同じ語彙）が実行前プレビューで配り、画面側は持たない

契約検証: `tools/agent-flow/tests/test_run.py`（6 件追加: 要求が設定ファイルに勝つこと・CLI が
要求に勝つこと・キーが無ければ既定挙動が変わらないこと・語彙外の拒否・daemon 起動が要求の値を
使うこと・壊れた要求の拒否）/ `tools/agent-dashboard/test/adhoc-flow.test.js`（4 件追加:
inbox への書き込みと再実行への引き継ぎ・未指定でキーを書かないこと・語彙外の拒否・画面の
ラベルが語彙を網羅すること）

### agent-loop: サブコマンドに効かないオプションを断るようにした

`--split-direction` / `--no-auto-attach` / `--controller-mode` / `--instance-id` は tmux ペインの
張り方とインスタンス識別で、**サブコマンド無しのデーモン起動**でしか読まれない。グローバル引数
として黙って受理していたため、`agent-loop --split-direction vertical methods list` が
「効いたつもり」で通っていた（agent-flow の計画パラメータと同じ形の穴）。

- サブコマンド指定時にこれらが明示されていたら usage エラー（rc=2）で断る
- argparse のサブパーサへは移せない（効き先が「サブコマンド名を持たない起動」なので）ため、
  parse 後の検査で行う。全サブコマンドで意味がある `--log-level` は対象外

契約検証: `tools/agent-loop/test/test_cli_options.py`（新設 5 件: デーモンモードでは素通り・
各オプションが rc=2 で断られる・未指定を誤検知しない・メッセージがフラグ名と
サブコマンド名を出す・`--log-level` が対象外であること）

### CLI の計画パラメータを、計画するサブコマンドへ集約した

`--granularity` / `--split-policy` / `--exemplar-first` / `--plan-gate` 系は**グローバル引数**
だったため、計画しないサブコマンドでも受理されて黙って捨てられていた
（`agent-flow --granularity finest doctor` が通り、何も起きない）。同時に `run` と
`orchestrate` が同じ意味の引数を二重定義しており、片方にだけ help や設定キーの案内が付く
食い違いも生んでいた。

- 計画パラメータを `run` / `orchestrate` の引数へ移し、**両者が同じ定義を共有**するようにした
  （`_add_planning_args`）。計画しないサブコマンド（`work` / `doctor` / `status` …）では
  usage エラー（rc=2）で断る
- `--help` を 2 群に分けた。**計画（形と分け方）** = `--planner` / `--pattern` / `--plan-file` /
  `--granularity` / `--review` / `--plan-gate` 系（計画時に決まる）、**動的 fan-out
  （split → map → reduce）** = `--split-policy` / `--max-fanout` / `--exemplar-first`
  （計画時には数が決まらず、実行中の split の出力で展開数が決まる）
- `--pattern` にだけ無かった設定キー `pattern` を足した。これで計画パラメータは
  すべて「CLI オプション名 = 設定キー（snake_case）」で 1 対 1 に対応する。
  不正な名前は `plan_strategy_pattern` が断る（フェイルクローズ）
- 子プロセスの argv 組み立てを `_planning_args` へ 1 本化した。以前は orchestrator と
  worker の共通部分（`base`）へ積んでいたため、**計画しない worker にも渡っていた**
- agent-project 側の呼び出し（`build_agent_flow_cmd` / `--from-inbox` の起動）も
  `--granularity` を `run` の後ろへ移した

契約検証: `tools/agent-flow/tests/test_run.py`（`SpawnArgvTests` に 4 件追加: 計画引数が
サブコマンド名の後ろに来ること・inbox の pattern が設定より優先されること・計画しない
サブコマンドが usage エラーで断ること・`cmd_run` が orchestrator にだけ計画引数を渡し
worker には渡さないこと）/ agent-flow 1018 件・agent-project 1298 件緑

### `--split-policy` を既定の planner でも効かせた

分割の単位（`--split-policy` / 設定 `split_policy`）を planner へ渡していたのは
`--planner agent` の分岐だけで、**既定の `flow-planner` 経路では指定が黙って捨てられていた**。
`--granularity` は 3 経路すべてへ渡っていたので、この 2 つは対称でなかった
（既定設定のまま `--split-policy file` と打っても何も起きない状態）。

- flow-planner スキルへ `--split-directive` を新設し、Phase 3（グラフ生成）のプロンプトへ
  分割の単位の指示文を差し込むようにした。スキルが受け取るのは値名ではなく
  **解決済みのテキスト**——文面の正典は手法カタログ（`split-policy-<policy>`）にあり、
  対象リポジトリの `.agents/methods/` による差し替えをこの経路にも届けるため。
  `--tier` のようにスキル側へ文面を複製すると、差し替えがこの経路にだけ効かなくなる
- 版ずれ（フラグを知らない古いスキル）へは従来どおり渡さない（`_skill_flag_supported`）
- `_planner_fallback` が引数を落としていたのを直した。flow-planner → agent の縮退でも
  指定が生き残る（縮退したら behavior に戻る、が起きない）
- `_plan_strategy` が planner 分岐ごとに同じ値を渡すようにした（stub は LLM を通らず対象外）
- `docs/specs/agent-flow-spec.md` のグローバル引数一覧に `--split-policy` が抜けていたのを補った

契約検証: `tools/agent-flow/tests/test_planner.py`（`SplitPolicyTests` に 5 件追加: 解決済み
文面での受け渡し・版ずれ時の非送出・リポジトリ差し替えがスキル引数まで届くこと・縮退での
指定の生存・入口が全 planner 経路へ渡すこと）/ `test_flow_planner_granularity.py`
（スキル側 3 件: プロンプトへの到達・空文字なら従来と 1 バイトも変わらないこと・CLI の配線）

### ワークフロー定義のスキーマを登録した

契約のうちワークフロー定義だけ `schemas/` に正典が無く、正典が 2 実装
（agent-dashboard の `normalizeWorkflow` と agent-flow の `plan_strategy_user`）へ
分かれていた。`schemas/agent-workflow.schema.json` を新設して登録する。

- **2 段の形を 1 ファイルで宣言した**: ライブラリ定義（dashboard が編集・保存する
  `workflows/<id>.json` / `.agents/workflows/` / ユーザー領域）と、投入 plan
  （inbox 要求の `plan` / `--plan-file`）。変換は `planFromWorkflow` が行い、工程の
  作業ルールは本文へ畳まれて goal へ入る（plan に methods フィールドは無い）
- **表現できない不変条件を明記した**: id 一意・deps の実在・循環なし・entry=ルート /
  exit=末端・split の後段を静的に張らない、は JSON Schema で書けないので実装が正典。
  スキーマ検証だけでは足りないことを説明に残す
- 表現できる制約は宣言した: kind / purpose の enum、human は tier・methods を持たず
  interaction が要る、design は終端 1 つで human / split を使えない、plan のノード上限 64
- 検証ライブラリはこのリポジトリでは使わない方針なので、**スキーマと実装の語彙一致を
  両側のテストで担保**する（`mission.schema.json` と同じ流儀）

契約検証: `tools/agent-flow/tests/test_workflow_schema.py`（新設 9 件: kind enum・
ノード上限・既定値の一致、スキーマどおりの plan が実装に通ること、スキーマが禁じる形を
実装も拒むこと、同梱フローが必須項目を満たすこと）/ `tools/agent-dashboard npm test`

### ワークフロー機能: 選ばれ方の違いを自動注入の層で強制した（監査の結果）

カスタムワークフローと手法（methods）の実装を「意図どおりか」で見直し、**選ばれ方
（auto / per-task / engine）の切り分けが画面の出し分けにしか支えられていない**穴を塞いだ。
`enabled` は auto ルールのための宣言なのに、それを読む層が `selection` を見ていなかった。

- `agentcore.methods.select` が `selection: auto` 以外を自動注入の候補から外すようにした
  （`auto_selectable()`）。ここは agent-flow / agent-loop 双方の自動注入が通る唯一の
  チョークポイント。塞ぐ前は `agent-loop methods enable split-policy-behavior` や
  手書きの tuning.json で engine / per-task の指示文を `enabled: true` にでき、
  **`--split-policy file` の run に behavior の指示も入る**（選択と矛盾する二重注入）
  状態を作れた。trial の variant が非 auto を名指しした場合も同様に効かせず、
  効かなかった variant はその実行を代表しないので trial としても記録しない
- 書き込み口も同じ規則で断るようにした（効かない宣言を残さない）:
  `agent-loop methods enable` は理由付きで拒否し、`methods list` は非 auto に選ばれ方を
  表示する。dashboard の `tuning.setMethod` も拒否する
- run 複製で、プリセットが名指しした id（`picked`）から非 auto を落とすようにした。
  engine / per-task はこれまでどおりカタログ複製（`enabled: false`）で運ぶ。存在しない
  id を名指ししたプリセットは、従来どおり投入時に明示的に失敗する
- 工程セットの雛形（`methodWorkflowPattern`）も、工程の候補と同じ規則で作業ルールだけに
  絞った。engine の指示文を工程へ複製できるとエンジンの注入と二重になる
- `schemas/agent-tuning.schema.json` に `kind` / `selection` / `format` を宣言した。
  3 ツールが読む契約なのに未宣言で、`selection: "engine"` の追加も文書化されていなかった

契約検証: `tools/agent-tools/agentcore/tests/test_methods.py` /
`tools/agent-loop/test/test_tuning.py` / `tools/agent-dashboard npm test`

### ワークフロー機能: エンジンが選ぶ指示文を手法カタログへ寄せた（selection: "engine"）

split_policy の文面カタログ化（設計 2026-08-18・案 C）を実装する前に、「methods の JSON
形式を通らずにワークフローの振る舞い（プロンプト文面）を決めている系」を全数確認した。
該当したのは split_policy のほか、granularity のスコープ指示・実行 tier（basic）の
planner/evaluator/split 指示・レビュー観点（レンズ）の 4 系統。いずれも「run パラメータの
値 → 固定文面」の選択で、文面だけならひとつの器に寄せられる（並列数倍率・auto の導出・
観点キーといった構造的効果は Python 側に残す）。案 C を 1 機構へ汎用化して実装した。

- 手法カタログの選ばれ方に第 3 形態 `selection: "engine"` を追加した。auto（実行条件で
  自動）/ per-task（工程ごとに人・planner が選ぶ）に対し、engine は**エンジンが
  CLI/config/agent-control の値から決定的に選ぶ**——enabled / when は選択に関与しない
  （dashboard はトグルに出さず一覧表示のみ）
- agent-flow に単一の口 `engine_directive(id, role, fallback)` を新設した。解決順は
  run 専用 tuning.json（dashboard が run 作成時に複製・run 単位の決定性）→ 対象リポジトリの
  `.agents/methods/<id>.json`（cwd → git root）→ `$AGENT_METHODS_DIR` → 組み込み文言。
  カタログ不在・破損・role 不一致・空文字はすべて組み込みへ倒すフェイルセーフ
  （無指定の run が黙って無方針にならない）
- 同梱カタログへ 8 件を新設した: `split-policy-behavior` / `split-policy-file` /
  `granularity-coarse|fine|finest` / `tier-basic` / `tier-basic-split` / `review-lenses`。
  文面は組み込み文言と同一で、乖離はテストが検出する。リポジトリに同 id を置けば
  そのプロジェクトだけ文面を差し替えられ、tier のように語彙が開いているものは
  組み込みが知らない値（例 `tier-small`）にも指示文を足せる（エンジン改修なし）
- dashboard は engine ルールを run 専用 tuning.json へ複製する（enabled: false のまま。
  dashboard 経由の run は agent-flow の cwd がリポジトリ外なので、`.agents/methods/` の
  差し替えをこの複製が届ける）
- 工程の作業ルール候補（`nodeMethodChoices`）から engine 選択の指示文を除外した。
  あわせて overview の手法一覧が `kind` / `selection` を落としていたのを直した——
  ここが落ちていると「成果物の契約・engine 指示文を候補に混ぜない」フィルタが機能せず、
  role が合う engine 指示文（`tier-basic-split`）が工程へ付けられて二重注入になる。
  この取りこぼしは既存の不具合でもあった: `kind` が届いていなかったため、成果物の契約
  （`design-document-format`）が work 工程の追加ルール候補として出ていた

契約検証: `tools/agent-flow/tests/test_engine_directives.py` /
`tools/agent-loop/test/test_methods_catalog.py` / `tools/agent-dashboard npm test`

設計: `docs/plans/2026-08-18-split-policy-catalog-unification-design.md`（実装記録を追記）

### 記憶層を測る・整える・共有する・使わせる（エージェント横断ナレッジ運転 K0〜K4）

記憶の 3 層（persona-use / ltm-use / wiki-use）と共有路（moltbook-use）は揃っているのに、
**誰も測っていない**ため「保存したのに共有されていない」「引かれないまま眠っている」が
見えなかった。agent-audit に読み取り専用の源泉を 1 つ足して、この空白を埋めた。
新ツール・新スキル・新ストアは作っていない。

- **`memory-store` 源泉**（agent-audit collect）: ltm / wiki / persona / moltbook のローカル
  状態から、**メタデータだけ**（frontmatter・件数・mtime・索引・ログ）を増分・冪等に収集
  する。内容が変わったときだけ snapshot が 1 行増える（cli-quota と同じ署名カーソル）。
  記憶ファイルへは書かない——整理の実行はスキル側のスクリプトのままで、audit は読み手に徹する
- **保存先は skill-registry.json から自動発見する**: 各スキルは既に自分の保存先を
  `skill_configs`（`wiki-use.wiki_root` / `persona-use.persona_home` /
  `moltbook-use.home`。`ltm-use` は常に `{agent_home}/memory/home`）に持っているので、
  agent-audit はそこから発見する。`agent-audit.yaml` の `memory_stores:` へ同じパスを
  書き写す二重メンテナンスは要らない——上書きしたいキーだけ書けば足りる
- **`report --kind knowledge [--json]`**: publish 待ち（share_score >= 閾値かつ未公開）・
  忘却リスク帯・退役候補（未参照のまま N 日超）・類似クラスタ・索引の乖離・wiki の
  index 乖離と lint 相当違反・queries ヒット率・persona 観察ログの滞留・moltbook の
  outbox 滞留を 1 コマンドで出す（LLM 不使用）
- **測れないものを 0 と偽らない**: 未設定のストアは「未収集」と明示し、moltbook の
  未回答メンション・goods は GitLab を引かないと測れないため `uncollected` に名指しで残す。
  設定したのに読めないパスは collect / report とも exit 2（fail-close）
- **persona は件数と滞留日数だけ**: 本文・タイトル・ファイル名は audit のレコードにも
  集計にも入らない（C1）。この規律はテストで固定した

- **記憶メンテナンスの定期駆動**（agent-loop, K1）: `memory-maintenance-hook.py` が
  索引再構築・忘却曲線の一括更新・wiki lint・`collect --source memory-store` を LLM ゼロで
  回す（`check()` は常に None）。判断の要る整理（consolidate・persona 反映・wiki 統合・
  **削除を含む**）は定期プロンプト「記憶メンテナンス当番」が dry-run を先に見てから
  自ら適用する——このノードの記憶メンテナンスに人は関与しない前提のため、承認は経由しない

- **空き時間の Moltbook 運転**（moltbook-use / agent-loop, K2）: `moltbook-duty-hook.py`
  が LLM ゼロで outbox の publish backlog を sweep する（既存の privacy gate をそのまま
  通す）。「Moltbook 当番」の定期プロンプトは timeline を確認し、自層（ltm/wiki）から
  根拠が引けた質問だけ `reply --autonomous` する。Moltbook は各ノードの AI だけが操作する
  前提で、`reply_mode`/予算/深さ/クールダウンのゲートに阻まれた自律返信は下書きを
  残さず無音スキップする（人の承認・差し戻しの経路は持たない）

- **agent-audit の利用状況タブに「記憶と共有」の要約を追加**（agent-dashboard）: 既存の
  利用状況領域（プロジェクトごとの話ではないので独立領域を持つ）へ、publish 待ち・
  忘却リスク・outbox 滞留の**点数だけ**を小さな節として足した。新しい領域・タブ・設定・
  操作は増やさない——記憶の内容確認は Obsidian など既存の閲覧手段に任せる。取得に
  失敗しても利用状況本体の表示は壊さない

- **知識を使わせる（K4）**: 蓄積した知識が実際に使われたかを実測し、整理（consolidate/
  cleanup）で検索できなくなっていないかを検知するところまでを閉じた
  - `agent-project stats --json` に `rule_worked` / `rule_misfire`（rules 昇格後の
    learn-worked / learn-misfire の合算）を追加。既存の `list_rule_adjudication` を
    再利用し、第二の集計系は作らない
  - `agent-audit report --kind knowledge` の ltm 行に `access_growth_7d`（access_count
    総和の週次差分・recall された量の近似）を追加。既存の週次成長（`growth_7d`）と同じ
    snapshot 履歴から取るので二重の走査をしない
  - `wiki_query.py search` は 0 件ヒット時、弱一致があればスコア順に・無ければタイトルの
    アルファベット順に近傍候補をその場で提示する（採用戦略 Phase 1 の残項目。トークン化・
    aliases・重み付け・日本語正規化は既に実装済みだった）
  - **`regression_check.py`（ltm-use・新規）**: 整理の前後で「整理前に引けていた記憶
    （access_count>=1）が今も（consolidate で統合されていれば統合先が）同じ問いで
    引けるか」を snapshot/compare する。統合が原因の回帰は archived→active への
    差し戻し（非破壊）だけで直せる。削除（cleanup）が原因の回帰は復元できないため
    報告のみ。ノード固有の実記憶に依存する `retrieval_eval.py`（妨害文書入り・
    hit@5/MRR）はノード横断の自動ゲートには使えないため、自動ゲートは決定的な
    自己想起の一貫性チェックに置き換えた——`retrieval_eval.py` 自体は既存どおり手動の
    定点観測（埋め込み recall の閾値再測など）として残す
  - 「記憶メンテナンス当番」（agent-loop）の定期プロンプトに snapshot → 整理 →
    compare → （統合起因の回帰だけ）revert の手順を追記
  - **見送り**: ltm の段構え埋め込み recall（設計済み・paraphrase hit@5 35%→60% 実測済み）
    はローカル ollama サーバへの新しい実行時依存を要るため、この計画の他項目
    （既存依存のみで閉じる）と性質が違う。ollama 常設を前提にしてよいかの意思決定を
    挟んでから後続で入れる

契約検証: `python3 -m unittest discover -s tools/agent-audit/tests` /
`python3 -m unittest discover -s tools/agent-loop/test` /
`python3 -m unittest discover -s tools/agent-project/tests` /
`python3 -m unittest discover -s .github/skills/moltbook-use/tests` /
`python3 -m unittest discover -s .github/skills/wiki-use/tests` /
`python3 -m unittest discover -s .github/skills/ltm-use/tests` /
`cd tools/agent-dashboard && npm test`

計画: `docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md`（K0・K1・K2・K3・K4。
記憶メンテナンス・Moltbook 運転とも人の承認を介さない前提で設計している）、
設計: `docs/designs/agent-audit-design.md` §4.1・§5.5、`docs/designs/gitlab-agent-sns-design.md` §8.1

### ワークフロー機能: per-task カタログに tier の適格性フィルタを追加した

`_per_task_rule_catalog()`（planner へ提示する per-task ルールの一覧）が `selection`
だけで絞り込み、各ルールの `when.tiers` 等を見ていなかった不整合を直した。auto ルールは
Python 側の注入時に必ず `when` を評価するのに、per-task はその評価を素通りしていた。
新設した `_per_task_rule_eligible()` は run 全体の条件（engine/workload/現在の実行
tier）だけを見る（role/purpose/agent_cli はどのノードが選ぶか次第なので、計画時点では
判定せず選ばれた後の role 判定に委ねる）。

契約検証: `tools/agent-flow/tests/test_planner.py::PerTaskRuleTests`

### ワークフロー機能: 工程ごとに選ぶルールを planner・評価役へ渡す配線を追加した

「工程ごとに選ぶルール」（per-task）は、これまでダッシュボードの編集画面で人がノードを選んで
初めて効いていた。`type: auto`（planner がグラフを組み立てる、最も一般的な使い方）や、評価役が
実行時に足すタスクには選ぶ手段が無かったので、既存の複製の枠組みのまま手段を追加した。

- dashboard は per-task ルールの完全な定義を `enabled: false` のまま run 専用 tuning.json へ
  複製する（自動適用ルールと同じ器。`enabled` と `selection` の意味を分けて共存させる）
- agent-flow（Python）が同じ tuning.json を直接読み、`selection: "per-task"` の一覧を
  planner・評価役のプロンプトへ後置する（`per_task_rule_directive`）
- planner・評価役が返すタスクへ `"methods": ["<id>"]` を含められる。`_coerce_tasks`
  （3 経路共通の単一チョークポイント）が、そのノードの role に合う本文だけを goal へ複製する。
  未知の id・role 不一致は黙って外す（フェイルオープン）
- flow-planner スキル（外部プロセス）には配線しない。既存の `split_policy` と同じ既知の制約

契約検証: `tools/agent-flow/tests/test_planner.py::PerTaskRuleTests` / `tools/agent-dashboard npm test`

設計: `docs/plans/2026-08-15-workflow-feature-improvement-implementation.md`（第 5 段）

### ワークフロー機能: 手法カタログのモデルを作業ルールと成果物の契約に分けた

カスタマイズ口を作業ルール 1 本へ寄せた結果、カタログの中に性質の違うものが同居していることが
表に出たので、宣言でモデルを分けた。区別の軸は「成果物によるかどうか」ではなく、
**選択条件を実行時に機械で判定できるか**と、**指示か契約か**。

- **作業ルール（`kind: rule`・既定）**: 依頼文へ足す指示。`selection: auto`（既定）は実行条件
  （役割・工程種別・実行レベル・料金区分）だけで決まり、設定画面のトグルで自動適用する。
  `selection: per-task` は機械判定できない「その工程への指示」で、工程ごとに人が選ぶ
  （同梱は `integration-verify`）。トグル一覧には出さない
- **成果物の契約（`kind: contract`）**: 成果物の形式そのもの。指示と、機械で数える構造を同じ
  1 ファイルに持つ。ON/OFF せず、設定画面には「いま有効な書式」として表示する
- **既定 ON**: `ui-consistency` と `test-green-evidence` を同梱で有効にした。どちらも文面が
  自己条件づけで、触らない工程では何も足さない。端末設定の宣言が常に優先する
- **工程の追加ルールを複数選択可に**: ノード定義は `method`（単数）から `methods`（配列）へ。
  旧定義は読み込み時に配列化して互換を保つ
- run へ複製する手法は「既定 ON ＋ 利用者が有効化したもの ＋ プリセットが名指ししたもの」。
  A/B 試行（trials）は端末設定の宣言をそのまま運ぶ

契約検証: `cd tools/agent-dashboard && npm test` / `python3 -m unittest discover -s tools/agent-loop/test`

設計: `docs/plans/2026-08-15-workflow-feature-improvement-implementation.md`（第 4 段）

### ワークフロー機能: カスタマイズ口を作業ルール（手法カタログ）へ寄せた

改善提案（P1〜P6）の実装で足した 4 つのカスタマイズ口を、既に在る仕組み——手法カタログと
実行時の `when` 注入——へ寄せ、足した口を外した。

- **ノードの面（`surface`）を廃止**: ワークフローは作業フローの型で、成果物に特化しない。
  何を作るかは実行時にしか決まらず、実行時に増えるノード（分類・分割の後段、評価役が足す
  作り直し）には定義側の宣言が届かない。画面の一貫性とテストの緑の証跡は、作業ルール
  `ui-consistency` / `test-green-evidence` を実行時に worker ロールへ足すことで担保する
- **統合検証の自動付与を廃止**: 雛形は標準パターンの工程だけを複製する。検証工程を置くのは
  フローを作る人か planner で、検証のやり方は作業ルール `integration-verify`
  （`when.roles: [verify]`）が実行時に足す。run の完了条件は従来どおり agent-flow の終端検証が決める
- **設計書の書式を手法として定義**: フロー定義の `contract` 宣言をやめ、汎用の
  `methods/design-document-format.json` を正典にした。設計 run への指示（`fragments`）と、
  実装へ渡す前に数える構造（`format`）が同じ 1 ファイルに並ぶ。リポジトリの `.agents/methods/` に
  同 id を置けば、そのリポジトリの書式へ丸ごと差し替えられる
- **agent-flow の設定 `split_policies` / `review_lenses` を廃止**: プロンプトへ足す文言は作業ルールの
  仕事で、`when.roles: [planner]` / `[evaluator]` を宣言したルールが実行時に足される。設定キーは増やさない

契約検証: `cd tools/agent-dashboard && npm test` / `cd tools/agent-flow && python3 -m unittest discover -s tests` / `python3 -m unittest discover -s tools/agent-loop/test`

設計: `docs/plans/2026-08-15-workflow-feature-improvement-implementation.md`（第 3 段）

### ワークフロー機能: 完了条件と CI 結果をエンジン側でも扱えるようにした

改善提案（P1・P6）のうち、前段では表示側だけを変えていた 2 点を実装した。

- **run の完了条件を agent-flow 本体へ**: 他ノードから依存されていない `verify` ノード（並列の
  変更をまとめた後の統合検証）の判定を run の完了条件にした。全ノードが done でも終端の検証が
  赤なら run は failed で終端し、`meta.failure_reason` に `[verification]` タグ、`final.json` の
  `verification` に判定を残す。判定は verify の構造化成果（曖昧な出力は fail へ倒す既存の 1 実装）で
  読み、本文の文字列を二重に解釈しない。終端に検証を持たない run の振る舞いは変わらない
- **公開後の CI 結果の取り込み**（`ci_status_command`・既定 off）: 公開に成功した commit の CI 状態を
  run の終端で問い合わせ、結果ノードの公開レコードへ書き戻す。CI ごとのクライアントは持たず、
  宣言されたコマンドの標準出力 JSON を正典にし、実行時に対象の URL・ブランチ・コミット・ローカル
  リポジトリを環境変数で渡す。状態は passed / failed / running / unknown の 4 値で、読めなければ
  unknown（緑には倒さない）。`ci_wait_seconds` で終端まで有界に待てる
- **dashboard は記録を読むだけに**: 統合検証と CI の判定を実行結果の記録から読み、記録の無い
  旧 run だけ工程の構造から読む。赤い検証で終端した実行は、環境要因の失敗と分けて要対応として出す

契約検証: `cd tools/agent-flow && python3 -m unittest discover -s tests` /
`cd tools/agent-dashboard && npm test`

設計: `docs/plans/2026-08-15-workflow-feature-improvement-proposals.md`、
実装記録: `docs/plans/2026-08-15-workflow-feature-improvement-implementation.md`

### ワークフロー機能: 水平分割・無検証終端・文言だけの契約を仕組みで塞いだ

12 並列タスクとレビューラウンドで作った成果が、CI 赤・画面間のダイアログ差異・冗長な説明文・
レイヤーを跨ぐ契約の取り逃しで後追い修正を要した。個別バグではなくワークフロー機能側の構造要因
（分割単位・完了条件・表現の契約・強制レイヤー）へ対処した。

- **統合検証を実装フローの雛形へ標準装備**（agent-dashboard）: 実装フローの雛形は終端へ
  「対象パッケージのテストスイート全体を CI と同じ系統で実行し、赤なら直して再検証する」検証工程を
  既定で持つ。分割（split）が終端の雛形と設計フローには足さない。run の完了表示は「全工程 done」ではなく
  「終端の統合検証が緑」を条件にし、赤のまま終端した run は公開失敗と同じ要対応として出す
- **分割の単位を宣言できるようにした**（agent-flow）: `split_policy` / `--split-policy`。既定 `behavior` は
  利用者から見える 1 つの振る舞いを 1 ノードが縦に持ち（UI はマークアップ・スタイル・呼び出し側を同じ
  ノードへ、複数画面で同じ用途なら共有部品ノードを先に置く）、ファイル境界の水平分割 `file` は
  衝突回避が要る大規模変更の明示オプションにした
- **ノードが作るものに応じた作業ルールの自動付与**（agent-dashboard / methods）: ノードへ「画面」または
  「テスト」を宣言すると、`ui-consistency`（既存 UI へ揃える・内部語彙を出さない・禁止は選択肢の制限で守る）と
  `test-green-evidence`（追加したテストの単独実行の緑を成果へ添える）を plan 生成が目的へ複製する。
  ルールが引けないときは黙って外さず起動を失敗させる
- **レビューラウンドへ観点を割り当てた**（agent-flow）: 評価役に二重実装・画面間/用途間の表現差異・文言量の
  3 観点を当てさせ、所見を `reason` に残させる。評価ラウンドは成果ゼロでも `evaluate` イベントとして
  run 履歴に残る（無言の欠番を無くす）
- **強制レイヤーを設計成果の必須項目にした**（agent-dashboard）: 設計書の「変更対象」へ契約ごとの
  強制レイヤー（実行時にどの層で強制されるか）を書かせ、無い成果は実装準備完了にしない。設計セッションと
  作業準備で分かれていた「実行できる設計書か」の判定を 1 実装へ統合した
- **公開後の CI 結果を実行結果へ載せた**（agent-dashboard）: 公開レコードに CI の結果が記録されていれば
  公開状態と同じ場所に表示し、赤なら要対応にする（dashboard から CI へ問い合わせはしない）

契約検証: `cd tools/agent-dashboard && npm test` / `cd tools/agent-flow && python3 -m unittest discover -s tests`

設計: `docs/plans/2026-08-15-workflow-feature-improvement-proposals.md`、
実装記録: `docs/plans/2026-08-15-workflow-feature-improvement-implementation.md`


### agent-dashboard: カスタム設計フローを実装フローと分離し、作業準備から引き継げるようにした

設計フローを通常の実装フローと同じ保存済み一覧・実行経路で扱うと、設計 run を実装 run と誤認し、
同梱の設計用フローまで利用者の編集対象に見えていた。設計と実装を同じ仕事の別フェーズとして追えるよう、
用途・公開範囲・作業準備の契約を揃えた。

- **用途と公開範囲**: フロー定義に `purpose: implementation|design` と
  `libraryVisibility: library|internal` を持たせる。既存定義は `implementation/library` として互換読込し、
  `design/internal` の同梱 `design-interactive` / `design-auto` は通常の実装フロー編集ライブラリへ混ぜない
- **scope 付きカタログと snapshot**: 設計フローは対象 cwd の登録済みリポジトリ共有・ユーザー共通・同梱を
  `scope: repository|user|builtin` 付きで列挙し、選択キーを `id + scope + repository` に固定する。選択時に
  正規化定義、出所、digest を snapshot へ固定するため、後から元定義を編集しても準備中の仕事や handoff は変わらない
- **読み取り専用の設計 run**: 設計 run は実装 run と別 ID の短命 run、workspace なし、human / split なし、
  `af/` ブランチなしとする。ノードへファイル変更・commit・push 禁止を付け、agent-flow 本体の run / plan /
  workspace 契約は変更しない
- **成果 Markdown**: 実装へ渡す `設計結果.md` は `## 目的`、`## 変更対象`、`## 受入基準`、
  `## 検証方法` の必須4節を持つ。未決事項は `## 質問` と推奨回答・理由へ残し、検証コマンドは実装 run の
  verify 契約で実行する。未完成成果は実装準備完了にせず、直前の回答・材料を保持して再試行する
- **作業準備と遅延補完**: `agent-design` / `external-design` / `direct` の3経路を作業準備項目へ合流させ、
  設計結果を `design-result` 材料として実装へ渡す。旧 `designMode: auto` 項目は一括移行せず、設計開始時に
  `design-auto` の builtin snapshot を遅延補完する
- **保存・削除と Git**: Dashboard が保存・編集・削除できるのは `~/.agents/workflows/` の自分用だけ。共有版・
  同梱版は読み取り専用で、自分用の削除は `.trash/` へ移動する。成果物 repository の git 書き込み・同期は
  通常の Git 運用、PR/MR、clone 更新または CI に任せる

契約検証: `cd tools/agent-dashboard && node --test test/adhoc-flow.test.js test/preparation.test.js`

設計: `docs/plans/2026-08-15-agent-dashboard-design-implementation-lifecycle-design.md`

### agent-dashboard: 相談を 4 領域それぞれの対象フォルダへ結び直した

「この作業を相談」は全領域共通のヘッダーに 1 組だけあり、対象はプロジェクト選択に固定されていた。
ワークフローとミッションには対象フォルダの一覧が無いため領域切替で選択が引き継がれず、
**画面で見ている対象とは別のフォルダで CLI が開いていた**。無効化されるのではなく黙って別の対象を掴む。

- **配置規則**: 相談ボタンは、その領域で対象フォルダを決めているコントロールの隣へ、領域につき
  1 組だけ置く。プロジェクトはヘッダー据え置き、ワークフローとミッションは各フォームの
  フォルダ入力の直下、定常業務は作業タブの対象フォルダ表示の隣
- **1 実装のまま**: ボタン・フォルダ選択・起動処理は共通で、領域は対象フォルダの出しかたを
  `registerConsultSource` で登録するだけ。相談用の選択状態は新設しない
- **定常業務の表示崩れを修正**: レジストリを持たない作業フォルダが起動先候補で
  「プロジェクト（状態リポジトリ）」と名乗っていたのをやめ、候補計算自体を通さないようにした
- **実行方針へ接続**: 相談が `workload: dashboard` / `purpose: chat` を渡さず agent-control を
  素通りしていた。UI は「エージェントとモデルは実行方針から自動設定されます」と表示していたが、
  相談だけがプロジェクト設定か既定の kiro へ落ちていた
- **実行制御を尊重**: `lifecycle` が `pause` / `stop`、または利用上限に達しているときは新しい対話を
  開かず理由を出す。秒数の台帳へは記帳しない（対話の長さを dashboard から観測できず、0 秒の行は
  「相談は無料」に見えるため。実測は CLI 側の既存経路へ委ねる）
- **全体設定 ＞ アプリに「相談で使うエージェント」を追加**: 既定は「実行方針に従う」。保存先は
  `control.json` の `workloads.dashboard.agents.chat` 1 か所で、設定用のキーを新設しない
- **起動前に実効エージェントを表示**: エージェント名・モデル・由来（実行方針／全体設定／
  プロジェクト設定／既定）と、押せない場合の理由を文字で出す

設計: `docs/plans/2026-08-14-agent-dashboard-consult-entry-design.md`

### agent-aider / agent-ollama / agent-opencode: 非ログイン起動でもプロキシを迂回して ollama へ届くようにした

エンジンからの非ログイン subprocess では ~/.profile の export が届かず、agent-aider が
既定の localhost へ向かうか、接続が社内プロキシへ流れて 504 Gateway Timeout で落ちていた。
agent-ollama だけが持っていた ~/.profile 補完を 3 つの CLI へ広げ、プロキシ迂回まで面倒を見る。

- **~/.profile 補完の対象拡大**: `OLLAMA_*` / `AGENT_OLLAMA_*` に加えて `NO_PROXY` / `no_proxy` を
  取り込む。`OLLAMA_HOST` / `OLLAMA_API_BASE` / `NO_PROXY` が揃っていれば profile は読まない
- **相互補完**: `OLLAMA_HOST` ⇄ `OLLAMA_API_BASE` を相互に補う（aider/litellm は API base しか
  読まないため、片方しか export していない環境でも両方の読み手が同じサーバへ向く）
- **プロキシ迂回の保証**: ollama のホストを `NO_PROXY` / `no_proxy` の両表記へ常に追記する。
  親環境が不完全な `NO_PROXY` を持っていても迂回が効く
- **適用範囲**: agent-aider と agent-opencode は単体ファイル配布で agentcore を import
  できないため、正典（agentcore/ollama_adapter.py）の複製を持つ。テストで 3 箇所の
  振る舞い一致を担保
- aider.json に 504 / ProxyError を env 分類する診断ヒントを追加

### agent-dashboard: 依頼を設計書まで詰めてからワークフローを実行できるようにした

依頼欄に何をどこまで書けばよいか分からず手が止まる問題への対策。外で書いた設計書を持ち込む経路と、
短いやりたいことから dashboard 上で詰める経路の両方を、依頼欄という1つの合流点へ集めた。

- **実行前チェック**: 依頼欄の必須4節（目的・変更対象・受入基準・検証方法）を決定的に数え、
  足りない節をバッジで出す。言い換え（狙い・スコープ・完了条件・テスト方法など）と
  「目的:」形式も拾う。LLM は使わず、実行はブロックしない。
- **設計書の取り込み**: ファイル選択・ドラッグ&ドロップ・貼り付けの3口で全文を依頼欄へ流し込む。
  100KB 超は警告だけ出す。空欄から書くための4節の雛形ボタンと展開エディタも足した。
- **設計セッション**: 1行のやりたいことから設計書まで詰める。1ラウンド = 1本の短命な設計 run で、
  human ノードを使わないため回答待ちの run が残らない。ラウンド数に上限は無く、
  毎ラウンド完全な設計書が返るのでどこで止めても「この設計で実行」できる。
  進め方は対話（`design-interactive`）と全自動（`design-auto`）の2本を同梱。
- **同梱フロー**: リポジトリ直下の `workflows/*.json` を読み取り専用のカスタムフローとして読む
  （手法カタログ `methods/` と同じ配布規則）。優先順位はリポジトリ共有 → ユーザー共通 → 同梱。
- agent-flow 本体と flow-planner は無変更。設計書は全文を依頼テキストとして渡す。
- 設計: `docs/plans/2026-08-13-agent-dashboard-design-session-design.md`。

### agent-audit / agent-dashboard: CLI 別の上限・期限を利用状況と tier 切替へ揃えた

- `agent-audit usage --by agent_cli` が node-budget の CLI 別設定上限と quota 観測の復帰時刻を返す。
  収集時に落としていた `reset_at` も保持し、時刻不明の rate limit は既存規約どおり 1 時間で失効する
- 利用状況のエージェント別表へ設定上限、期限・復帰予定、状態、tier ごとのモデル候補を表示する。
  公称枠は推測せず、未設定は「上限未設定」とする
- Resource Controller の tier / 候補選択は同じ node-budget の上限・quota 状態を使うため、
  制限中は次候補または下位 tier へ退避し、復帰時刻後はヒステリシスを通って戻る

### feat(agent-dashboard): カスタムフローをリポジトリで共有できるようにした

- 従来のユーザー共通 `~/.agents/workflows/*.json` に加え、実行対象リポジトリの
  `.agent-flow/workflows/*.json` を自動探索する。同じ id はリポジトリ版を優先し、
  statemachine 定義と同じように定義ファイルを commit して任意の clone で共有できる。
- リポジトリ共有フローは読み取り専用とし、取得・公開は通常の clone 更新と PR/MR に任せる。
  dashboard は成果物リポジトリへ書かず、git 操作もしない。実行画面ではフォルダを確定した
  時点でカタログを読み直し、選択した共有フローを run の plan へ固定する。
- ノードへ追加するプロンプトも `.agent-flow/methods/*.json` からリポジトリ単位で探索し、同じ
  id はリポジトリ版を優先する。選択時に本文と source hash をノードへ複製する。

### feat(agent-flow): カスタムフローの動的 fan-out へ tier 補償を届けるようにした

`tier: basic` のお膳立て（planner/evaluator の分解指示・review 強制）はカスタムフロー
（`plan_strategy_user`）の動的生成部分に届いていなかった。人が描いた静的な形は変えずに、
エンジンが実行時に生成するノードだけへ補償を掛ける。実装計画:
`docs/plans/2026-08-12-agent-flow-custom-flow-tier-compensation-implementation-plan.md`

- **split の分解粒度を tier 対応に**: `execute_agent` が kind=split のとき
  `tier_split_directive` をプロンプト末尾へ後置する（`continue_agent` の評価指示と同じ
  流儀でスキル/組み込み両経路に 1 回だけ効く）。basic では「各要素 = 1 つの短い手順で
  完了できる大きさ」まで割らせ、出力契約（JSON 配列のみ）は指示文の中で再確認する。
  tier はノード固定 ＞ agent-control の workload 宣言の順で解決
- **user plan の review を三値として tier 判定へ**: `plan_strategy_user(plan, request, tier)`
  が `"review": False` 直書きをやめ、`plan.review`（true/false/"auto"・不正値は厳格に
  失敗）を `tier_review_decision` へ通す。"user-defined" は集約パターンに含まれないため
  **basic 以外では従来どおり False（後方互換）**。basic では map→reduce 間へ verify gate
  が入る（`_emit_reduce_tree` の既存機構）。採った tier は `strategy.tier` に残る
- **fan-out クランプの可視化**: `_expand_splits` が `max_fanout`（既定 50）超過の要素を
  黙って捨てていた。切り捨て時はログ・replan 理由（`data-driven fan-out: +N（fan-out
  クランプ: …）`）・reduce ノードの goal（「元 N 件のうち先頭 M 件のみを処理」）に明示し、
  集約結果が全件のように読まれないようにした。`max_fanout` の自動引き上げはしない
- **修正**: user plan の既定（`evaluate` 無効）では `_continue` が評価役より前に
  done/failed を返し、データ駆動 fan-out（機械展開・LLM 無し）まで塞いでいた——split を
  含むカスタムフローは map/reduce が一度も生成されず空振りしていた。「評価役の再計画で
  ノードを足さない」原則は LLM 判断の話であって機械展開は対象外なので、user_plan 分岐の
  先頭で `_expand_splits` を通すようにした
- **段の分離をテストで固定**: 動的生成ノード（map/reduce/gate）は `tier`・`agent` を
  持たず workload の段に従い、人が固定した静的ノード・その retry は段を保つ
  （「補償が届くノードだけ緩める」が実装上も自動的に成り立つ）

### agent-flow の機能・役割ごとに実行可能な実行レベルを宣言し、自動 tier を実行方針で決めるようにした

ワークフロービルダーはどのノード機能にもどの実行レベル（単純作業/軽量/標準/高性能）でも
固定でき、「work を単純作業へ」「judge を軽量で」のような不整合を止められなかった。
機能・役割 × 実行可能レベルのカタログを管理面の 1 実装
（`agent-dashboard/src/features/orchestration/main/flow-tiers.js`）として宣言し、
plan 生成時に適用するようにした。設計:
`docs/plans/2026-08-12-agent-flow-tier-eligibility-strategy-design.md`

- **実行可能レベルの見直し**: basic（単純作業）へ任せるのは一手順で完結する
  classify / filter / extract / map のみ。成果物を作る work / generate、検証 verify、
  読解 retrieve、集約 reduce は軽量以上。横断判断の synthesize / judge とフローの形を
  決める split、役割 planner / evaluator は標準以上
- **オプションとして拡張する振る舞いは下限を引き上げる**: classify の route
  （分類結果がフローの形を決める）は軽量以上、verify の retry（判定がリトライ予算と
  再作業を駆動する）は標準以上。エディタは選択肢を絞り、不適格になった固定レベルは
  「自動」へ戻して通知する
- **複数レベルで実行可能な振る舞い（自動 tier）は実行方針の戦略で決める**: 方針が選びうる
  段がすべて適格なら従来どおり実行時の方針を継承し、不適格な段を選びうる機能
  （例: 節約 × judge）だけ今の段を適格範囲へ丸めて plan へ固定する（丸めの方向は
  節約=下へ・それ以外=上へ。固定後は固定 tier ノードと同じく降格しない）
- **plan の tier 保持ギャップを解消**（統一実行方針設計・移行項目3）: agent-flow の
  `plan_strategy_user` / `_node_entry` が plan の `tier` を保持し、固定 tier が
  `pinned-tier` として status・台帳・手法判定（`when.tiers` のノード tier 優先）へ届く。
  継続動作（retry / replan）の作り直しノードも置き換え元の固定 tier を引き継ぐ

### feat(agent-flow): 計画承認ゲートと tier:basic のお膳立てを追加した

予算逼迫の緊急時、agent-profiles の縮退が `tier: basic` を宣言すると、普段は任せない
役割・作業へ最小能力ワーカーを投入せざるを得なくなる。その下地として、どちらも
オプトインの 2 機能を planner まわりに足した。

- **計画承認ゲート（`plan_gate` / `--plan-gate`・既定 off）**: planner の計画の実行前に
  `human` 承認ノード（`plan-gate`）を挿し、root を全てゲート依存へ付け替える。承認
  （approved）まで base-sync 含め何も実行しない。差し戻し（rejected＋コメント）は
  orchestrator が決定的に検知し、指摘を要求へ付けて planner を呼び直し、未確定ノードを
  新計画で置き換えて次のゲートを挿し直す（`max_retries` で有界・旧ゲートはグラフから
  外し結果は監査として残す）。期限切れは `[plan-gate]` タグ付き failed 終端
  （フェイルクローズ）。決着は既存の human interaction 機構（park → service_waits →
  `interactions/`）をそのまま使い、ユーザー定義フローには挿さない。
  期限は `plan_gate_timeout`（秒。0 = interaction 既定の 7 日）
- **tier:basic のお膳立て**: agent-control の `workloads.flow.tier` が `basic` のとき、
  (1) `granularity: auto` を finest へ解決（明示指定は覆さない）、(2) planner プロンプトへ
  「1 ノード = 1 短手順・goal に対象/成果/確認方法を明記」の分解指示を注入
  （flow-planner スキルは新設の `--tier` で受ける。フラグを知らない旧版スキルには渡さず
  縮退させない）、(3) `review: auto` を常時有効へ（basic の成果を無検証で集約しない）、
  (4) 評価役の再タスク生成にも同じ basic 指示を後置。採った tier は `strategy.tier` に残る
- **修正**: orchestrate の `_node_entry` が `interaction` を落としていたため、ユーザー定義
  フローの `human` ノードが orchestrate 経由の graph では interaction 不正で失敗終端して
  いた（worker は claim 時に graph の node を読む）。graph にも保持するようにした
- human interaction の差し戻しコメント（`outcome=rejected` の `answer.comment`）を
  `human_feedback_from_results` が「人の指摘」として評価役へ運ぶようにした
  （承認コメントは拾わない——承認済み run へ不要な replan を誘発しない）

### docs: 複数フックとターン完了 hook を設計書・仕様書へ取り込んだ

`hooks` 改称とターン完了 hook の実装が入ったあと、設計書には実装メモがそのまま残り、
仕様書は `event_hook` 時代のキーを載せたままだった。両方を現行の実装に揃えた。

- **設計書**: ターン完了 hook を「完了は画面推定より CLI 自身の通知を先に見る」節として
  機能 5 へ入れ、正典（[ターン完了 hook 設計](docs/plans/2026-08-12-agent-loop-turn-completion-hooks-design.md)）
  へリンクした。機能 1 には複数フックの節を足し、**プロンプトを返したフックの数だけ
  dispatch を作る**（まとめて 1 本にすると `ack()` の相手が混ざる）ことを明記。
  再び入っていた設定 YAML と重複見出しは仕様書・README 側へ寄せ直した
- **仕様書**: `event_hook` → `hooks`（文字列/配列・名前解決・複数指定の意味論）、
  `event_hook_config` → `hook_config`、新しいグローバルキー `mapping`（`{{lookup}}`）、
  受入条件のパス判定（区切りか拡張子を持つ表記だけ。コマンド名は対象外）、
  `json_variant` による制御応答の振り替えを反映
- **仕様書に §1.4「完了の見分け方」と §3.6「ターン完了 hook（内部契約）」を追加**。
  CLI 別の注入方法と native event、mailbox のファイル配置と権限、`hook-event` が
  状態を書き換える 5 条件、画面監視へ戻る条件、対象外（headless / external pane /
  手動起動 / Cursor / Kiro v3）を表と箇条書きで固定した
- 付録に `~/.agents/loop-hooks/` と install prefix の `hooks/` `agent-hooks/` を追加

### docs(specs): agent-loop の仕様書を新設した

設計書から設定マニュアルを外した結果、「どのキーが書けて、既定値は何で、何が拒否されるか」
を一望できる場所が無くなった。README は使い方の手引き、DESIGN.md は実装の内部構造なので、
どちらとも役割が違う。`docs/specs/agent-loop-spec.md` を新設し、仕様の一覧をここに置く。

- **できること**（送信のきっかけ 5 経路、実行のかたち 8 種、操作コマンド）、**設定**
  （ファイルの優先順位、グローバル 30 キー、エントリ 30 キーの型・既定・意味）、
  **契約**（event hook / webhook / inbox メッセージ / 限定ツール / RESULT 行）、
  **規約**（`slash` 名、webhook ルート名、tmux セッション名、環境変数名、パス）、
  **制約**（上限とタイムアウトの一覧、配送保証、失敗時の挙動、未実装）の 5 部構成
- 値は実装から取った。エントリの採用条件と起動を止める組合せは `validate_entries`、
  既定値は `cli.py` / `dispatch.py`、上限は `toolloop.py` / `_head.py` / `semaphore.py`、
  受入条件の照合規則は `toolloop.acceptance_paths` が根拠
- `docs/specs/` は本書が最初の文書。設計書のヘッダから相互リンクした

### docs(designs): `agent-loop-design.md` を設計書の形へ戻した

709 行のうち約 6 割が README / DESIGN.md と重複する設定マニュアルになっていて、
「何をどう決めたか」を読み取るのに全文を通読する必要があった。slop-police スキルの
設計書ルール（結論先出し・却下案つきの判断・強弱・省略）に沿って再編した（柱2 / C4）。

- **主要な設計判断を 5 つに再編**。旧 5 件のうち pull/push のフック契約と provider 非依存を
  1 件へ統合し、代わりに機能 5・7 の本文へ埋もれていた 2 件（実行経路をツールループの所在で
  分ける／完了を自然文の受入条件から機械照合する）を判断として立てた。確信度は判断ごとに
  書き分け、証跡ゲートの機械層だけが動いている点は「中くらい」と明記した
- **機能 1〜7 の節から設定マニュアルを外した**。YAML の書き方は
  `tools/agent-loop/README.md`、クラス構成と処理フローは `tools/agent-loop/DESIGN.md` へ
  委ね、本書には設計上の境界（既読化は `ack()` 後、webhook のフック例外は 200、
  外部キューは scheduler が保有、待機判定は CLI ごとに違う 等）だけを残した。
  README に記述の無い webhook / adaptive / acceptance の設定だけは最小形で残す
- **実装状況とテスト一覧を付録へ移した**。見出しの「— 実装済み」表記をやめ、未接続・未実装
  （adaptive の error 遷移、自然文基準の証跡判定層、headless ログの tmux 自動起動）は
  付録 A の 1 段落に集約した
- 709 行 → 496 行。機能番号は据え置き、`slash` 節の見出し変更に伴う anchor リンクは
  `tools/agent-loop/README.md` と opencode/ollama 提案書の 2 か所を追随させた

### 定型業務の Aider 実行を tmux で見える agent-loop ハーネスへ移した

Aider（ollama バックエンド）でのステートマシン実行は dashboard の main プロセス内で
非表示に走っていて、実行の様子を人が見られず、他の CLI の定常業務（tmux ウィンドウで
CLI が動く様子ごと見せる）と体験が割れていた。実行器そのものを agent-loop 側へ移した。

- **agent-loop に `statemachine` サブコマンドを追加**。statemachine-use のワークフローを
  aider 等の headless CLI で完走させるハーネス（限定ツールループ: `read_files` /
  `write_files` / `run` / `final`、パス・実行ファイル検証、argv 実行、JSONL ログ）を
  dashboard の in-process 実行器から移植した。CLI とモデルは `--agent-cli` / `--model`
  で実行ごとに指定でき（`agents/<name>.json` 契約で解決）、`--param KEY=VALUE` /
  `--input` でワークフローの実行パラメータを渡す。状態遷移は従来どおり
  statemachine-use の `next_state.py` が正典
- **dashboard の定型業務（Aider）は tmux ウィンドウでこのハーネスを起動**する。
  実行ごとに一意な tmux セッション（`agent-sm-…`）を作ってアタッチし、状態遷移と
  aider の呼び出しが画面に流れる。ウィンドウを閉じても実行は tmux 側で続く。
  ウィンドウを開けない環境では非同期実行へ落ち、ハーネスの `RESULT` 行を従来の
  実行履歴契約として記録する
- dashboard の in-process 実行器（`stateMachineRunner.js`）は削除した。実行境界が
  agent-loop（WSL 側）になったことで、win32 でも aider / ollama が実際に居る側で
  ステートマシンが走る。**Windows → WSL の受け渡し**は既存の `sh()` と同じ規則へ
  揃えた——起動は必ず `wsl.exe` 経由（同期・非同期の起動仕様を `cliSpawnSpec` に
  一本化）、tmux の `-c` と `cd` には翻訳済みの Linux パスだけを渡し、`--workflow` は
  cwd 相対の POSIX パスで渡す（作業フォルダの外を指す組み合わせは起動前に断る）
- 一回限りの実行なので、tmux の扱いはチャット経路と意図的に違える。**起動に失敗しても
  同じコマンドを窓で再実行しない**（対話 CLI なら安全な再実行も、ステートマシンでは
  ファイル編集ごと二重に走る）。代わりに、起動できない唯一の実質的な原因（PATH に無い）
  をセッション作成前に `command -v` で断る。また、実行が終わったあとにアタッチすると
  tmux はリサイズでペインの内容を捨てる（`remain-on-exit` で残しても同じ）ため、
  出力は `pipe-pane` でファイルへ写し、アタッチから戻ったあとに窓へ出す。実行中に
  離脱した場合は再接続方法（`tmux attach -t …`）を表示する

### 手法パックの「段」と dashboard の「段」を 1 つに揃えた

同じ段を指しているつもりの語彙が 2 系統に割れていて、**段を宣言しても手法が一度も効かない**
状態だった（柱3 / C7・C9）。原因は 3 つ重なっていた。

- **キーがラベルから作られていた**。画面は「キーは利用者が決めることではない」という方針で
  呼び名からキーを導いていたが、日本語ラベル（既定のプレースホルダも「たっぷり使う」）は
  英数字が残らず `tier-1` になる。手法カタログ（`methods/*.json`）は `small` / `medium` と
  書く前提なので、この組み合わせでは `when.tiers` が永久に一致しない。キーは**並び順**から
  振るようにした（下から `small` → `medium` → `large`、4 段目以降は `tier-4`…）。段の意味は
  並び順そのものなので、ラベルは表示専用のまま自由に付けられる。下から数えるのは、行を
  足しても「いちばん下＝いちばん弱い段」を動かさないため
- **段がエンジンへ届く経路が契約の外だった**。`current_tier()` が `profiles.json` の `state` を
  直接読んでいたが、agent-profiles は「エンジンから読まれない」ことを不変条件にしている契約
  である。dashboard が段を決めたら `control.json` の `workloads[].tier` へも投函し、エンジンは
  agent-control だけを読む形にした（読み口が 1 つになり、語彙が別物になる余地も消える）。
  候補が全滅して決められなかったワークロードには従来どおり触れない
- **文書と入力例が語彙から外れていた**。`{"tiers":["full"]}` という例（agent-loop README・
  dashboard の独自手法フォーム）を `small` に直し、段が 1 つも宣言されていないノードでは
  `tiers` 条件が当たらないこと、そこでも効かせたいなら `max_relative_cost` / `agent_cli` で
  絞ることを README とスキーマ（agent-tuning の `when.tiers`）に書いた

### agent-flow: ローカル CLI を planner に選ぶと、分解がキーワード判定まで黙って落ちていたのを直した

`agent_cli` に `ollama` を選んだ run で、要求の中身と無関係に同じパターン（`generate-and-filter`）
ばかりが選ばれていた。3 段の連鎖だった。

- **flow-planner が起動していなかった**。スキルの `--agent-cli` が組み込み 4 種の白リストで、
  `ollama` を弾いていた。argv の組み立てを agentcore（`agents/<name>.json`）へ委譲し、白リストを
  廃止。定義を置いただけの CLI もそのまま計画役に使える。agent-project の `--agent-cli` にも
  同じ白リストがあったので併せて撤廃した（README は以前から ollama を挙げていた）
- **単発 planner がツールループに食われていた**。planner を書き込みモードで呼ぶため、
  agent-ollama の `--tools bash` が生え、モデルが契約どおり返した JSON を「規約から外れています」
  と蹴っていた。**planner / evaluator は宣言が無ければ readonly を既定**にする（`READONLY_ROLES`）。
  `agents: {planner: {readonly: false}}` で従来どおりにも戻せる。設計判断の改訂は
  [`2026-08-08-agent-ollama-expansion-design.md`](docs/plans/2026-08-08-agent-ollama-expansion-design.md) §5.2 に記録した
- **stub のキーワード判定が定型文に当たっていた**。agent-project 由来の要求には charter の
  対象リポジトリ一覧が付き、その「書込先候補」の一語で `generate-and-filter` が選ばれていた
  （実測 15 件中 9 件）。判定は要求本体（先頭の段落）だけを見る。パターン名の名指しは
  どこに書かれていても優先する
- **縮退が静かだった**。flow-planner → agent planner → stub と落ちた事実と理由を、ログと
  `strategy.reason` の両方に残す。「計画できたように見えて実は stub」を後から見分けられる

### agent-dashboard: 全体設定・参加・プロジェクト設定・ミッションが開いても空だったのを直した

領域ナビの導入後、左メニューからこれらを開いても**何も表示されない**状態になっていた。
原因は 1 つの例外と、それが広がる構造だった。

- **原因**: 設定を「全体設定」と「定常業務の設定」の 2 画面へ分けたとき、値を入れる
  `populateSettingsFields` が全部の入力欄の存在を前提にしたままだった。定常業務の設定を
  描く時点では全体設定の欄はまだ DOM に無いので `null` への代入となり、例外になっていた。
  「あるものにだけ入れる」へ改めた
- **被害が広がった理由**: 画面の描画が 1 本の連鎖で、途中の例外で**それ以降の画面が全部
  描かれない**構造だった。以後は 1 画面ずつ隔離し、失敗はその画面に閉じ込めて開発者
  コンソールへ残す。領域ごとに独立した画面を並べるポータルで、1 か所の不具合が
  ほかの領域を巻き添えにしない
- **ミッションが常に空だった**のも別要因で直した。ミッションは端末（ノード）の話なのに、
  選択中プロジェクトのフォルダが依頼先ホームと一致するときだけ表示していた（タブが
  プロジェクトのタブ列に並んでいた頃の名残）。ノード単位の一覧に改め、左メニューへ出す
  条件とタブを出す条件を同じ式にした
- 起動時に `refreshAmigos()` を呼んでいなかったため、ミッションと参加が最初の巡回まで
  （自動更新を切っていれば永久に）左メニューに出なかったのも直した
- 出せるタブが 1 つも無い領域は、押しても何も起きない行き止まりだった。必ずホームへ
  着地させ、理由をコンソールへ残す

### agent-dashboard: 文言と見た目を人が使う前提で整えた（UX パス）

- **1 画面に見出しは 1 つ**。領域の見出しとペイン内の見出しが縦に 2 回並んでいたのを解消
  （自分の見出しを持つ画面では領域ヘッダーを畳み、持たない画面ではペイン側を消した）。
  ホームの最初の 1 行は「あなたの対応待ち」になった
- **タブは領域名を繰り返さない**（定常業務の設定 → 設定、プロジェクト設定 → 設定）。
  どの設定かは左メニューが示す
- **全体設定への入口を 1 つにした**。サイドバー共通ヘッダーの歯車ボタンを外し、
  領域ナビ末尾の「全体設定」へ一本化
- 説明文から内部語（ワークロード等）を消し、ホームのカードを
  キッカー＋名前＋数字＋「開く」だけに簡素化。利用状況カードの移設前の古い案内文も修正
- 見た目: 地の色を無彩色 3 段へ整理し、ボタンを「既定は輪郭のみ・主要操作だけ塗り」の
  二階層に統一。角丸・余白・ホバーを揃えた

### agent-dashboard: 左メニューを領域ナビにし、右ペインをその領域の内部ナビにした

ホームをタブとして足した時点では、左（プロジェクト一覧しかない）と右（定常業務・
ミッション・参加のタブが混ざる）の対応が取れていなかった。人が最初に選ぶのは「どの道具の
話か」なので、そこを左メニューへ引き上げた。

- **サイドバーが領域（ワークロード）ナビ ＋ その領域の対象一覧の二段**になった。
  ホーム / プロジェクト / 定常業務 / ミッション / 参加 / 利用状況 / 全体設定。使っていない
  道具（募集もミッションも無い等）は並べない。右ペインのタブは選んだ領域の中の画面だけを
  出し、タブが 1 本の領域ではタブ列ごと畳む
- **各領域に「動かす」「動いた結果を追う」「動かし方を決める」が揃った**。定常業務は
  作業 / 実行の記録 / 定常業務の設定、参加は参加できる仕事 / 参加の状況。実行の記録は
  ダイアログ（`dlg-cowork-history`）から領域のタブへ移し、ダイアログは廃止した
- **設定を効く範囲で分けた**。全体設定に残るのは端末のすべてのワークロードに効くもの
  （エージェント / 共通指示 / 実行制御 / 同期と実行）とこのアプリ自身の設定（アプリ /
  外部連携）だけ。利用状況（agent-audit）は利用状況領域へ、定常業務（cowork）は定常業務
  領域の設定タブへ移した。差し込みの仕組みも HTML の組み立ても変えず、受け側だけを移設
- **起動導線は右ペインに置いた**（左メニューは選ぶだけ）。押し間違いが起きやすく、
  どの対象に対する起動かも確定していない一覧に起動を置かないため。**実行エンジン
  （`agent-project serve`）を起こす経路は足していない** — 全領域に「単独起動」を揃えるなら
  常駐体の起動ボタンが要るが、それは設計 §2.1 の非目標そのものなので、起こせない領域では
  起動ボタンを出さず実行側と状況を示すに留めた（押せるのに何も起きないボタンを作らない）

設計: `docs/plans/2026-08-08-agent-dashboard-portal-design.md` §5。

### agent-dashboard: 最初の画面を agent-\* ファミリー横断の「ホーム（ポータル）」にした

これまでの起動画面は agent-project の「概要」（プロジェクト未選択なら「プロジェクトを
選択してください」）で、タブ列も agent-project の 5 タブが一等席だった。定常業務や
ミッションだけを使う端末では何もできない画面に見え、複数プロジェクトの要対応
（人の判断待ち）は各プロジェクトのタブを回らないと見えなかった。

[KiroCrew](https://github.com/kirodotdev/KiroCrew) のワークロード横断ダッシュボードを参考に、
最初の画面を**ホーム（ポータル）**に変えた（見出しも「プロジェクト管理」から
「エージェントポータル」へ）。ホームは全プロジェクト横断の「あなたの対応待ち」
（件数降順・クリックでそのプロジェクトの要対応タブへ）と、各ワークロード
（プロジェクト / 定常業務 / ミッション / 参加 / 利用状況）の入口カードを 1 枚で見せる。
カードは 3 つ目の登録簿 `registerPortalCard` で各制御面が差し込み、コアはどのカードが
載るかを知らない——agent-project も他の制御面と同じ 1 枚のカードになった。

アーキテクチャは借りていない: ホームは discover() が既に持つデータだけで組み、
新しい取得経路・状態の書き手・判断根拠を増やさない（KiroCrew の Gateway 型の中央常駐は
このファミリーの原則に反するため採らない）。起動時はホームに着地し、プロジェクトの
文脈は裏で復元する。サイドバーや通知クリックでプロジェクトを**選ぶ操作**だけが
ホームからそのプロジェクトの画面へ移る。設計:
`docs/plans/2026-08-08-agent-dashboard-portal-design.md`（柱2 / C4 — 横断ホームで
人の 1 回の介入の質と速さを上げる）。

### agent-dashboard: 定常業務が全体設定のエージェント指定を無視して常に kiro-cli で起動していたのを直した

全体設定 →「実行制御」→「機能ごとのエージェントとモデル」の**定常業務**（agent-control 契約の
`workloads.routine.agent_cli` / `model`）に `ollama` を指定しても、定常業務の実行はいつも
`kiro-cli chat --trust-all-tools` を tmux で起こしていた。**設定はしてあるのに効かない**という、
画面からは原因の見えない失敗になっていた。原因は 2 つ重なっていた。

- `cowork.chatCommand` の**既定値**が `kiro-cli chat --trust-all-tools` だった。設定の保存は
  既定値も `config.json` へ書き戻すので、誰も触っていなくても「明示上書きが常に載っている」
  状態になり、CLI 定義からの解決へ一度も到達しなかった。既定を空へ改め、既存の
  `config.json` に残る旧既定値は「人が選んだ上書き」ではなく残骸として無視する
- 定常業務の実行経路が `{ cowork: … }` だけを組み直して CLI 解決を呼んでいた。その時点で
  全体設定（`orchestration`）も ⚙ アシスタント設定（`agent`）も落ちており、何を設定しても
  既定へ倒れていた。実行側へアプリ設定の全体を渡すようにした

あわせて、定常業務の起動が `workloads.routine`（空欄なら `defaults`）を **⚙ アシスタント設定
より優先**して読むようにした。管理面が「この機能はこの CLI とこのモデルで」と宣言するのが
agent-control の役目で、それが起動に効かないなら宣言する意味が無い。宣言がモデルだけなら
CLI は下位の解決のままモデルだけ差し替える。起動 argv は従来どおり CLI 定義
（`agents/<name>.json` の `interactive`）が正典なので、`ollama` のように**モデル名を argv に
載せる CLI**（`agent-ollama --tui --think off <model>`）も指定どおりに起動する。入力受付の
待ち方（`ready_pattern`）とセッション開始コマンドの `when.agent_cli` 判定も、解決した CLI の
ものを使う——kiro 固定のまま ollama のセッションへ送ると、待ち受けも送る内容もずれる。

指定した名前の CLI 定義が見つからないときは、従来どおり下位の解決へ倒して定常業務は止めないが、
黙っては落とさず警告に残す（設定ミスに気付けないまま別の CLI で走り続けるのを避ける）。

### agent-ollama: TUI の入力行で矢印キー・履歴・Tab 補完が効くようにした

`--tui` は 1 行を素の `readline()` で読んでいたため、**矢印キーがエスケープ列のまま本文へ
混ざっていた**（`^[[A` が送信される）。打ち間違いを直す手段が Backspace しか無く、
長いプロンプトを書くデバッグ用途では実用にならなかった。

標準ライブラリの `readline` を噛ませて、左右キーのカーソル移動・上下キーの履歴・
`Ctrl-A/E/W/U/K` 等の編集・`Ctrl-R` の履歴検索・Tab 補完を効かせた。

- **Tab 補完**はローカルコマンド（`/help` `/tools` …）・スキル名（`/pdf` …）・`on|off` を出す。
  候補を出すのは**先頭の語がスラッシュで始まるときだけ**——本文中の `/`（パスや日付）で
  候補を出すと Tab がただの邪魔になる
- **履歴はセッションをまたいで残る**（`~/.agents/ollama/tui-history`・`AGENT_OLLAMA_HISTORY`
  で移せる）。ログ（gc の対象）とは別の場所へ置く: 履歴は人の入力で、実行の証跡ではない
- **`Ctrl-C` は入力中の行を捨てるだけで終了しない**（shell と同じ）。実行中の `Ctrl-C` は
  従来どおり推論の中断で、止めたいのが入力か推論かで意味を分けてある
- キー一覧は TUI 内の `/keys` で出る。割り当ては `~/.inputrc` がそのまま効く

**行指向・非全画面という設計上の制約は保っている**。readline は編集中の 1 行を書き換える
だけで、確定した行は通常どおりスクロールへ流れるので、tmux `capture-pane` から見た画面は
従来と同じ（`ready_pattern` の `> ` も含めて変わらない）。有効にするのは本物の端末で
対話しているときだけで、パイプ入力・非 tty では素の 1 行読みへ落ちる——編集用のエスケープを
非 tty へ吐くと、出力を読む側が壊れるため。`AGENT_OLLAMA_NO_READLINE=1` で明示的に切れる。
bracketed paste も切ってある（`capture-pane` の突き合わせに余計な文字を混ぜない）。

### agent-ollama: `~/.profile` の `OLLAMA_HOST` が届かず起動できなかったのを直した

エンジン（agent-project / agent-flow / agent-amigos）は agent-ollama を**非ログインシェルの
subprocess** として起動するため、`~/.profile` に書いた `export OLLAMA_HOST=...` が届かず、
既定の `127.0.0.1:11434` へ向かって env 落ちしていた。**設定はしてあるのに動かない**という、
一番説明しづらい失敗になっていた。

- `OLLAMA_HOST` が未設定のときだけ `~/.profile` を評価し、`OLLAMA_*` / `AGENT_OLLAMA_*` の
  未設定分を補完する。環境に既にある変数が常に勝つ（呼び出し側の明示指定を潰さない）
- profile の評価は sh の子プロセスに閉じ込め、失敗は黙って無視する（profile が壊れていても
  推論を止める理由にはしない）。子の stdin は閉じる——本体の stdin はプロンプト本文で、
  profile に読ませてよいものではない
- あわせて接続の上限（`DEFAULT_CONNECT_TIMEOUT_SEC`）を 30s → **120s** へ延ばした。混雑時や
  モデルロード直後は応答ヘッダの返りが遅く、30s では正常系を殺していた
  （`AGENT_OLLAMA_CONNECT_TIMEOUT` で従来どおり上書きできる）

### agent-ollama: 文脈使用量を見えるようにし、黙った切り捨てを起こさせないようにした

ローカル推論の「たまに指示を無視する」の正体の 1 つが**文脈長の黙った切り捨て**である。
会話が `num_ctx` を超えると ollama はエラーを返さず古い側を落とすので、システムプロンプトが
消えた状態で尤もらしい答えが返る。無進捗（stall）と同じで、**見えないから直せない**という
形の失敗だった。

- **いまの使用量を常に持つ**ようにした。直前の応答の `prompt_eval_count` + 出力トークンが
  会話全体のトークン数なので、追加の問い合わせは要らない。プレフィックスキャッシュが効いて
  「新規評価分だけ」返す版でも、**会話は伸びる一方**という性質で補正する（減って見えたら
  積み上げへ切り替え、`context_source` が `estimated` になる）
- **上限を「効く順」に解決する**: `--context-limit` → 送っている `num_ctx` → `/api/ps`
  （サーバが実際に確保した値）→ `/api/show`（モデルの宣言）。どれも取れなければ上限不明として
  使用量だけを出す——**知らない上限を根拠に警告も自衛もしない**。`--context <model>` で
  上限だけを調べることもできる（LLM を呼ばない）
- **上限へ近づいたら 1 回だけ警告する**（`--context-warn-pct`・既定 90）
- **ツール出力を残り容量に合わせて詰める**。それでも入らなければ `context_exhausted` で
  明示的に止め、`@agent-note` で「途中で打ち切った」ことを呼び出し側にも見せる（成果自体は
  返す——R1「止めない」）。サーバに黙って捨てさせるより、止まった理由が残る方がよい
- 使用量は `llm_end` イベント・`--status` の JSON・stderr の `@agent-context` 行・TUI の
  ステータス行（`ctx 4.2k/8.2k (51%)`）に出る。TUI では `/ctx` でいつでも確認できる
- `@agent-usage`（その実行で使った累計トークン = 台帳向け）と `@agent-context`（いま文脈が
  どれだけ埋まっているか）は意味が違うので**行を分けた**
- 上限の問い合わせは短いタイムアウト（既定 3 秒・`AGENT_OLLAMA_META_TIMEOUT`）で行い、
  失敗しても実行は続ける。失敗はキャッシュしない（TUI のような長命プロセスで、一度取れな
  かっただけで以後ずっと文脈表示が死ぬのを避ける）
### agent-loop: エージェント CLI を `agents/<name>.json` 契約で差し替えられるようにした（`agent_cli`）

agent-loop は kiro-cli 固定で、ファミリー共通のエージェント CLI プラグイン契約
（`agents/<name>.json`、設計 `docs/designs/agent-cli-plugin-design.md`）の対象外だった。
設定に `agent_cli: claude` 等と書けば、定義の `interactive` から起動 argv と**待機状態の
監視・判定**を解決してペインを駆動するようにした（`agent_loop/cliprofile.py`）。未指定の
挙動は不変で、未知・壊れた定義は起動時に明示エラー（黙って kiro へ倒さない）。

- **待機判定は CLI ごとに方法が違う**ことを契約側の宣言で吸収した。`interactive` に
  `busy_pattern`（処理中の正の検出。入力欄を出したまま処理する claude 等の TUI では
  ready の消失が起きないため、これが判定の正になる）と `idle_quiet_sec`（パターンを持たない
  CLI 向けの「画面が N 秒不変なら待機」）を追加。判定の優先順位は
  busy ＞ ready ＞ 静穏 ＞ 既定 busy で、`SlotMonitor` と送信前チェックが共通に使う
- **送信テキストの作法も定義に従う**: fresh_context のクリアコマンドは
  `interactive.clear_command`（kiro/claude=`/clear`、codex=`/new`、空文字=クリア手段なし）、
  `slash` 行とセッション開始コマンド（chat）の行頭 `/` は `skill_command_prefix` で差し替え
  （codex は `$name`）
- ローダは新設せず **agentcore.agentcli を同梱**（「ローダは言語ごとに 1 実装」を維持）。
  `install.sh` が zipapp へ同梱し、リポジトリ直接実行は相対探索。kiro 以外では
  slot-release stop hook（kiro-cli の agents 機構）は注入せず、スロット解放はペイン監視のみ
- 同梱定義に判定を追記: claude（`(esc to interrupt)` を busy、枠付き入力欄を ready）、
  codex（busy + `/new`）、ollama（`経過 [0-9]` のステータス行を busy、`> ` を ready、
  クリア手段なし）。スキーマ・`agents/README.md`・ゴールデンテスト互換（argv 不変）

### docs(designs): ループ拡張の設計書 8 件を `agent-loop-design.md` へ統合した

kiro-loop 系と agent-loop 系で同名の設計書が 4 対 8 件並存していた（クローン改称移行の途中状態）。
他の設計正典（agent-flow / agent-project）と同じ構成 — TL;DR → 背景と課題 → 主要な設計判断 →
機能別設計 → 付録 — の 1 冊 `docs/designs/agent-loop-design.md` に統合し、旧 8 件を削除した。

- 名称は移行先の `agent-loop` に統一。kiro-loop 系統との差分（設定パス・環境変数・inbox 共有・
  検証状況）は付録 B に集約。`tools/kiro-loop/` の実装自体は改称方針どおり残置のまま
- 対のあいだで食い違っていた記述（フック例外は 200 で握る・`reply_to` はメッセージ ID 専用 等）は、
  実装検証済みの agent-loop クローン版を正として採用。モジュール分割で陳腐化した行番号参照と
  実装当時の変更量見積り表は落とした
- `agent-loop-slash-property-design.md` は fork 先へ単体展開するための自己完結文書なので統合せず、
  統合版からリンクする形で残置
- `docs/designs/README.md` の索引と、`tools/{agent-loop,kiro-loop}/DESIGN.md` ほかの参照リンクを
  統合版へ差し替え

### agent-ollama: クラウド CLI が使えないときのバックアップ実行系にした

クラウドのエージェント CLI がガバナンスや予算の事情で使えなくなると、agent-tools の作業が
まるごと止まる。手元の ollama は使えるが、CPU 推論では 1 呼び出しに数十分かかるため、
**そのままでは「動いているのか固まっているのか分からないまま呼び出し側のタイムアウトで
殺される」**——遅さを許容する仕組みが無いせいで、遅い実行系が使えなかった。

`agent-ollama` を単発 text→text の変換器から、契約（ヘッドレス / 読み取り専用 / 書き込み /
対話 / 実測 usage / エラー分類）に完全適合する実行系へ広げた。速度と品質は犠牲にする代わりに、
**止まらないことと、止まっていないと示せることを要件にしている**（設計:
`docs/plans/2026-08-06-opencode-ollama-cpu-inference-proposals.md` §0.1・案 F-2）。

- **`--tools` で bash 1 つの最小ループ**を持つようにした。ツールをテキスト規約（コードブロック =
  実行するコマンド）で 1 つに絞ってあるので、毎リクエストの固定費はシステムプロンプトの
  数百トークンだけ——汎用ハーネスの 1〜2 万トークンは CPU では prefill だけで数分焼ける
- **ループとツールは書き込みモードでだけ生える**（定義の `write_args`）。読み取り専用モードには
  道具が 1 つも無いので `readonly: enforced` の宣言が嘘にならない
- **「遅い」と「死んだ」を区別する**ようにした。ストリーミングで受け、進捗を追記のみの
  JSONL（`~/.agents/logs/ollama/`）へ常時書く。沈黙中も heartbeat を打つので、
  「生きているが prefill 中」を事後にも証明できる
- **打ち切りは無進捗だけ**。`--stall-timeout`（既定 180 秒）は生成開始後の無進捗にのみ効き、
  **最初のトークンまでの待ちは既定で無制限**（CPU では prefill 10 分が正常なので、ここに
  上限を置くと正常な実行を殺す）。打ち切りは `transient` 分類で返しリトライ層へ渡す
- **`--status`（1 行 JSON）と `--follow`** を足した。前者は外部監視（`state` / `alive` /
  `since_last_progress_sec`）、後者は人が**ヘッドレスで走っている実行へ後からアタッチ**して
  ラウンド毎の動きを見るためのもの。`--tui` は同じ描画の対話版
- **`--think on|off` を CLI オプションで持つ**ようにした（API の `think` フィールドへ直結。
  プロンプトへ `/no_think` を混ぜる方式はモデル依存で本文へ漏れるので採らない）。
  `agents/ollama.json` に `--think off` を焼き込んだのでエンジン側は何も知らなくてよい
- **スキルは明示したものだけを遅延で読む**（`--skill`、またはプロンプト先頭ブロックの
  スラッシュ行）。カタログを LLM へ見せないので**使わないときの追加コストは 0**。読む先は
  `install.py` の既存配布先そのまま
- 対話起動を `ollama run` から `agent-ollama --tui` へ変えた（進捗が見えるため）。TUI は
  **全画面にしない**——tmux `capture-pane` から読めなくなるため
- `install.sh --with-rich` で rich を zipapp へ同梱できるようにした（任意。既定は
  ネットワーク不要のまま素の ANSI 表示）
- ollama の `options` をリクエスト単位で足せるようにした（`AGENT_OLLAMA_OPTIONS` の JSON。
  `num_ctx` などをサーバ全体の環境変数を触らずに変えられる）

### agent-loop: 定期プロンプトの前にスラッシュコマンドを送れるようにした（`slash`）

スキル呼び出しやモード切替を定期プロンプトで使うには、本文へ `/name` を書き込むしかなかった。
本文と制御指定が混ざり、コマンドだけ差し替える・外すのが面倒だった。

- `prompts` の各エントリに `slash: string | string[]` を足した。各要素は `/<name> [引数]` という
  **独立した 1 送信**になる（本文へ連結しない——対話 CLI はスラッシュコマンドを
  「1 入力 = 1 コマンド」で解釈するので、連結すると本文の一部として扱われる）
- 送信順は `fresh_context` の `/clear` → `slash`（宣言順）→ `prompt` 本文
- 規約外の要素は**その要素だけ**捨てて警告する（タイポ 1 個で定期駆動が黙って止まらないように）
- `prompt` を省いた `slash` 単独のエントリも有効にした（コマンドだけ定期送信）
- 未指定のエントリの挙動は変わらない。旧実装は未知キーを無視するので設定の相互運用も壊れない
- fork 先へ単体で展開できるよう仕様と移植手順を独立文書にした
  （`docs/designs/agent-loop-slash-property-design.md`）

### agent-dashboard: 全体設定「利用状況」の数字を agent-audit の集計へ一本化した

この節には集計が 2 つ並んでいた——画面がノード予算の台帳から自分で足した「利用量」と、
agent-audit が集計した「実測のトークン利用量」である。台帳（`budget-ledger`）は agent-audit の
源泉の 1 つで、そこへ CLI のセッションログを突き合わせた分だけ後者の方が確かなので、同じ話題の
数字を 2 つ置く理由が無かった（コンセプト正典 C7: 同じ判断の根拠を 2 つ置かない）。

- 合計・機能別・エージェント別を agent-audit の集計から描くようにした（新 IPC
  `agentAudit:summary` が `usage --json` を 2 軸ぶん取り、**合計は main 側で畳む**——表ごとに
  画面が足すと、片方の取得だけ失敗したときに食い違った数字が並ぶ）
- 上限はノード予算が正のまま。ゲージの分母として読むだけで、**期間が予算の期間と一致する
  ときだけ**残量を出す（別期間の集計に上限を重ねると嘘の残量になる）。期間の初期値も予算に合わせた
- agent-audit を入れていない・まだ収集していない端末では台帳だけの集計へフォールバックし、
  **どちらを見ているかを画面に明示する**（黙って別の数字に差し替えない）
- 実測と推定は従来どおり合算せず、内訳を必ず添える（agent-audit の設計不変条件）

### agent-dashboard / agent-flow: 自動実行の同時実行数を全体設定から宣言できるようにした

「この PC で同時にどれだけ走らせてよいか」は端末の資源の話なのに、設定の置き場（各プロジェクトの
`agent-flow.yaml` の `max_runs` / `workers`）はプロジェクトごとに散っていて、1 台の負荷を下げたい
人が全プロジェクトの yaml を直して回ることになっていた。

- [agent-control 契約](schemas/agent-control.schema.json) に `workloads.flow.concurrency`
  （`max_runs` / `workers`）を additive に追加。優先順位は契約どおり
  **control > CLI 引数 > 設定ファイル > 組み込み既定**で、キーを消せば元の解決へ戻る
- agent-flow が `participate`（run の受理枠）と `run`（worker 数）でこれを読む。壊れた値
  （負数・数値でない・`workers: 0`）は宣言なしとして無視する——GUI の入力ミスで run が
  誰にも進められなくなる方が、上書きが効かないより高くつく
- dashboard の「全体設定 → 実行制御 → 同時に動かす数（自動実行）」から宣言する。`max_runs: 0` は
  **上限なし**（既定へ戻すのは空欄）で、agent-flow 設定と同じ語彙に揃えた


### 修正 — agent-dashboard: シェル初期化メッセージでプロジェクト一覧が消える

WSL 側ホーム（`$HOME/.agents`）の解決が、`wsl.exe … sh -lc 'wslpath -w …'` の標準出力を
**先頭 1 行で決め打ち**し、パス形式でなければ捨てていた。ログインシェルはプロファイルを
読むので、標準出力にはコマンドの結果より前に初期化メッセージが混ざる（motd の転載・
nvm / conda のバナー・`.profile` の echo・更新の案内）。そういう端末ではホームの解決が
丸ごと失敗してローカルの `~/.agents` へ落ち、`engine/status.json` が読めなくなる。
これはプロジェクト発見の唯一の根拠なので、**画面からプロジェクト一覧が消える**——
しかも原因はバナーを出す設定を入れた日で、dashboard 側の変更とは無関係だった。

- 出力は全行を走査し、Windows のドライブパス（`C:\…`）か UNC（`\\wsl$\…`）の行だけを
  採るようにした。複数該当したら最後を採る（プロファイルの出力は先、`wslpath` の結果は後）
- 読み取りは `base/main/wsl.js` の `extractWindowsPath` の 1 実装に集約し、同じ書き方だった
  `~/.agent-project` の解決（instances 共有）も同じ経路へ寄せた
- 先頭行の決め打ちに戻さないことをテストで固定した


### agent-dashboard: 監査を独立タブから全体設定の「利用状況」へ移した

監査が扱う数字（実行証跡から集計した実測トークン・実行品質）は**この端末のもの**で、
選択中プロジェクトとは無関係だった。独立タブだとプロジェクトのタブ列に無関係なものが
並び、全体設定には既にノード予算から集計した「利用状況」があるので、同じ話題の数字が
2 か所へ分かれてもいた。

- 監査タブを廃止し、**全体設定 →「利用状況」**へ統合。既存の利用量パネルと同じ節に
  「実測のトークン利用量」「実行品質」「収集の設定」が並ぶ（見た目も同じ枠に揃えた）
- renderer コアに `registerGlobalSettingsPanel(section, { id, html, wire, reveal, refresh })`
  を追加。`registerFeatureTab` と同じ形で、タブにするほどではない面を全体設定の節へ
  差し込める。面は自分の容れ物だけを描き直す（他の節で入力中の欄を飛ばさない）
- 集計の取得は節が開いたときに初回だけ走る（利用状況を見ていない間は CLI を起こさない）
- 予算の取得に失敗しても監査の面は出す（データ源が別なので、片方の不調で両方を隠さない）


### agent-dashboard / agent-project: 進まないタスクの強制完了（force-complete）

どうにも進まないタスクを、人の判断で途中から完了にできるようにした（強制完了）。承認
（`approve --complete`）は検収待ち（`review` と成果のある `blocked`）にしか効かず、
`doing`（実行中）・`offloaded`（委譲中）・`ready` で堂々巡りしているタスクは画面から
完了にできなかった——承認しても `ready` へ積み直され、同じ工程がまた同じところで止まる。

- **agent-project**: `force-complete <id> --reason …`（CLI・`commands/` ドロップの両方）。
  verify は実行せず、成果ブランチの自動統合もせず、委譲中の run を切り離してから done を
  確定する。理由は必須
- 通常の完了と混ざらないよう **`FORCED`（未検証）として記録**する: 納品書
  （`archive/<id>.md` の `- 検収 : FORCED`・`verify … → 未実施`）／受領書（`DELIVERY.md`
  の検収欄）／決定記録（`action: force-complete`）。track の実績には手戻りとして記録
- 実行中に強制完了したとき、遅れて戻ってきた試行の結果で**タスクが backlog へ復活しない**
  ようにした（settle の入口でタスクファイルの消失と `force_completed` マーカーを見る）
- **agent-dashboard**: タスク詳細の「操作」タブと要対応カード（作業再開）に
  「強制的に完了にする」。理由の記入必須＋確認ダイアログで「検証しない・統合しない・
  未検証として残る」ことを押す前に提示する
- 受領書の検収欄が明記されていない過去の `archive/` は従来どおり `PASS` として再生成する


### agent-tools / agentcore: 監査で見つかった実装バグの修正

agentcore を横断監査し、再現できた高確度の実装バグを直した（柱 1 の分担契約と柱 2 の
fail-close 検証が壊れる経路）。仕様レベルの積み残し（分散 claim の一過性二重勝者、
再クローン時の未 push 更新ロス、workspace.path/base を見ない入札選別など）は本項では
直さず、PR 説明に棚卸しする。

- **protocol**: `extra` が `who` / `ts` / `lease_until` を上書きできた。予約キーは正規
  フィールドが勝つ。壊れた `lease_until` で `renew_lease` が ValueError していたのを
  `_as_float` へ。原子書き込みの一時名を PID 固定から `mkstemp` へ（同一プロセス並行衝突）
- **commands**: 受理レシートで payload が `ok` / `source` を偽装できた。正規メタを後勝ちに
- **board**: 壊れた `requires` を制限なし扱いにしていたのを fail-close へ。文字列の
  `contract_version` をパースし、読めない要求も不参加へ倒す
- **verifycontract**: `policy.confirm < 1` / `timeout_sec <= 0` を生成・検査の双方で拒否。
  `exit_code != 0` と `inconclusive` が両立する矛盾レコードは fail
- **repolocal**: ユーザ無し SCP（`host:path`）をローカルパスへ絶対化していた。非 object の
  host 設定で `.get()` が落ちないよう空 dict へ倒す。dashboard の正規化も同規則へ
- **transport**: `user.name` / `user.email` を独立に補完。`git add` / `commit` の
  「対象なし」以外の失敗を握り潰さない
- **agentcli / install.sh**: `~/.agents` 親の存在ではなく `agents/` サブディレクトリ単位で
  新旧ホームを判定（スキーマどおり）。`prompt_via` / `output` / `env` / `errors` の形を検査
- **ollama_adapter**: `urlopen` に timeout（既定 600s / `OLLAMA_TIMEOUT`）、非 dict 応答を明示エラーへ

副作用の緩和（同 PR 内）:

- `write_json_atomic` は `mkstemp`（0600・掃除非互換名）ではなく
  `<path>.tmp.<pid>.<unique>` を使い、agent-flow の残骸掃除を新接尾辞に対応
- `_commit_pending` は「ステージ差分の有無」で no-op 判定し、subdir 外 untracked による
  誤例外を防ぐ
- `agentcli.normalize` は `env: []` / `errors: {}` を `or {}` で握り潰さない

### agent-tools / agentcore: 上記修正の取りこぼしと副作用の追修正

前項の追試で見つかった、fail-close の入れ方が「拾わない」ではなく「落ちる / 止まる」に
なっていた 2 件と、片側だけ直っていた 2 件を揃えた。

- **transport**: subdir をまだ 1 度も書いていない起動直後の `sync_push` が
  `pathspec ... did not match any files` で **RuntimeError になっていた**（前項で
  `git add` の失敗を握り潰さなくしたときの取りこぼし——`state_git_subdir` 運用はバスが
  毎パス `sync_push` を呼ぶため、初回パスで必ず止まる）。ステージ対象が作業ツリーにも
  index にも無いときだけ no-op に倒す（`_scope_absent`）。文言ではなく実体で判定し、
  「subdir 配下を丸ごと消した削除だけのパス」は従来どおり commit・push する
- **board**: `requires.contract_version` が `NaN` / `Infinity` だと `int()` の
  ValueError / OverflowError が `eligible()` を貫通し、**そのノードの入札巡回ごと止まって
  いた**（`json` は既定でこれらのリテラルを受理する）。読めない値として不参加へ倒す
- **protocol**: `ts` 欠落・`null` の claim を `_as_float` が 0.0 と読み、最小 ts として
  **恒久的に勝ち続けていた**（前項で `renew_lease` 側だけ `_as_float` 化し、`winner` 側の
  同じ性質が残っていた）。欠落は「読めない」として無視する。`NaN` も無視する——比較が常に
  False になり `min()` の結果が入力順で変わるため、勝者判定の決定性が壊れる
- **agent-flow stategit**: 同期除外が `.tmp` 末尾だけを見ており、実際に生成される
  `<name>.tmp.<pid>[.<unique>]` を拾えず、**torn JSON の残骸が共有状態リポジトリへ
  commit・push され全 PC へ配られていた**（前項では掃除側 `cleanup` だけが新接尾辞に
  対応した）。除外側も同じ形を見る

### agent-flow / flow-planner: 列挙駆動の分解（対象単位のノードが生まれるようにする）・集約の木構造化

「API のドキュメント化」のような粗いバックログに対し、対象単位のノードが生まれず
「まとめて調査する 1 ノード」に畳まれていた。粒度ノブ（`granularity`）はこの問題の解ではない
——ノブは 1 ノードのスコープ上限を決めるもので、対象数に応じたノード数は**列挙**からしか
導出できない。粒度は計画時に確定させず、列挙を実行時のステップとして扱う設計に変えた。

**flow-planner**

- Phase 1 に **enumerability 軸**を追加。「同一手順を多数の独立した対象へ繰り返す」かを
  3 条件（手順が同一 / 対象間に依存が無い / 成果が対象単位で完結）で**個別に**判定する。
  単一フラグにしないのは、ファイル・関数が常に列挙可能なため、単一成果物の実装まで
  map-reduce へ倒れる（他パターンを侵食する）のを防ぐため
- Phase 2 で**ハイブリッド発動**: 3 条件全充足かつ件数 > 3 が確定なら Decision Matrix の
  スコアに関わらず map-reduce を含める（force。複合は潰さない追加）。件数不明なら +5 加点に
  とどめ LLM が最終判断（boost）。条件未充足・件数 ≤ 3 は**従来経路と完全に同一**（off）
- **列挙 probe**（`--probe-root`・LLM を呼ばない決定的走査）: 列挙手順のグロブ／ディレクトリを
  実際に走査して件数だけ実測する。依存物・隠しディレクトリは除外。0 件は「不明」として扱う
  （計画時点で作業対象を手元に取得していないことがあるため、対象なしとは読まない）
- Phase 3 は split の goal に実行時の列挙手順を埋め込ませ、force のときは**split の存在**も
  決定的ゲートで検査して 1 回だけ作り直す（強制したのに split が出ないと元の症状へ戻る）
- 発動根拠は `strategy.reason` と `strategy.enumeration` に必ず残す（誤爆の観測点）

**agent-flow**

- 実行時 fan-out の集約を**木構造**にした（新設定 `reduce_width`・既定 8）。map が幅を超えると
  チャンクごとの中間集約を挟み、最終集約は中間集約だけを受ける。単段集約は対象数に比例して
  1 ノードへ成果が集中し、正しく展開できたときほど失敗しやすかった
- 検証ゲート（review 時）も同じ幅で分割し、中間集約は自分の群のゲート通過後に走る。
  中間集約がなお幅を超える場合はもう一段畳む。**幅以下なら従来と同一構造**（id を含めて不変）

**agent-project（外側が内側の戦略を固定させていた層間の汚染・2 件）**

- 内側へ渡す分解粒度を **`flow_granularity`（既定 auto）** として分離した。従来は外側の
  `granularity`（バックログの INVEST 粒度・既定 coarse）をそのまま `--granularity` で渡して
  おり、agent-flow 側では明示指定が complexity 導出より優先されるため、**内側の work ノード
  レンジが常に 1〜3 に固定**されていた（複雑なタスクでも「まとめて 1〜3 ノード」に畳まれる）。
  外側の値は内側へ流れなくなった。内側だけ明示したいときは `flow_granularity: coarse|fine|finest`
- `build_request` の定型文からパターン語彙を除去した。従来は「完了条件を満たすまで反復し…
  （loop-until-done）」が**全タスク**の要求文に入り、flow-planner の戦略選定にはパターン正規名の
  アンカー、フォールバックのキーワード検出には「反復」ヒットとなって、実行規律のつもりの文が
  戦略選定を loop-until-done へ吸っていた。定型文は「完了条件: verify が exit 0（満たすまで
  作業を続ける）」だけを言う。定型文の語彙衛生はテストで固定

### agent-project / agent-dashboard: charter からの自動分解を廃止（分解は人の明示操作だけ）・削除は物理削除に変更

バックログを削除しても、次のパスで似たタスクが自動で作り直されていた。原因は charter 駆動の
分解が「消化可能タスクが無い」「charter が変わった」「却下した」を契機に**自動で**走り、
削除で空いた穴を planner が埋め直す設計だったこと。charter からの分解は人の試行錯誤で練る
ものなので、自動分解そのものをやめた。

**エンジン（agent-project）**

- plan（charter 分解）は**人の明示要求（`replan` 指示・viewer の分解ボタン）があったときだけ**
  走る。初回の分解も charter 編集の反映もこの口で人が起こす（自動契機は全廃）
- evaluate も未達 acceptance から改善タスクを自動起票しない。未達は新ステータス
  **awaiting-plan（分解待ち）**として milestone で人へ返す（opt-in の敵対的レビュー所見だけは
  従来どおり起票）
- `reject`（却下）は再計画を自動要求しない。却下済みは archive（rejected）・墓標に残り、
  次の分解時に backlog-planner への入力として渡る
- backlog-planner への入力を拡張: 同一バージョンのバックログ（保留・実行中・レビュー中）に
  加えて **archive の却下済み（却下理由付き・直近 30 件）**を渡し、「タイトルが違っても意図が
  同じ・似ているタスクは出力しない」をスキルの責務として明示（タイトル照合では言い換え
  再提案を捕まえられないため）。投入側の Jaccard 照合・墓標の完全一致抑止は最終防衛線として維持

**画面（agent-dashboard）**

- 🗑 削除は**物理削除**に変更（backlog と needs をゴミ箱へ。reject 投函をやめた）。分解が
  自動で走らなくなったので、消したタスクが勝手に復活することはなく、削除→明示的な分解で
  同種タスクが再提案されるのは期待どおりの試行錯誤の口。「作り直させない」意思表示は
  ✕ 却下（墓標・決定記録つき）が担う。実行中（doing）に加えて委譲実行中（offloaded）も拒否
- 「計画を作り直す」ボタンを「バックログを分解」に改め、初回分解の正規の口として案内。
  awaiting-plan の milestone カードは分解ボタンへの誘導を表示

**削除・却下と関連状態の整合（切り離しと孤児掃除）**

- エンジンの毎パスの整合点に 2 つの GC を追加。`prune_dangling_afters` は後続タスクの
  `after`（先行指定）から backlog にも archive にも無い id（＝物理削除済み）を切り離す。
  `reap_orphan_task_state` はタスク本体を失った付随状態——検証記録 `verifications/<id>/`・
  run ブリーフ `brief/<id>.md`・実行権ロック `claims/<id>.lock`——を物理削除する
  （archive に居る id の検証記録・ブリーフは記録として温存。ロックは backlog 基準）
- 却下（reject）は run ブリーフをその場で退役させ、蓄積を archive の却下記録へ転記する
  （done の archive と同じ扱い。brief/ に残すと同 id 再利用時に古い内容が注入される）
- dashboard の削除は viewer が持ち主のレビューコメント（`reviews/<id>/`）も一緒に掃除し、
  確認ダイアログに影響する後続タスク（先行指定が自動で外れるもの）を表示

### agent-project / agent-dashboard: バックログを削除しても要対応（needs）が残り、消しても復活する問題を修正

画面（agent-dashboard・Windows）からバックログを削除しても要対応カードが残り、手で消しても
復活する——試行錯誤（積んで、走らせて、要らなければ消す）が回らなくなっていた。原因は 3 つあり、
どれも「削除が公式契約の外にあった」ことから出ていた。

**1. 要対応カードに掃除する側が無かった**

`needs/<id>.md` は「タスクの status の投影」という契約なのに、投影を**作る**側（`ensure_needs`）
しか無かった。タスクを消した後に票だけが残ると、対応タスクの無い票は `ingest_feedback` が
読み飛ばす（`[x]` を付けても消えない）うえ、`has_work` は「人の入力あり」と数えて watch を
毎パス起こす。人からは「消しても消えない・復活する要対応」に見えていた。手で票を消しても、
タスクが blocked / review / proposed のまま残っていれば `ensure_needs` が作り直す（設計どおり）。

- `reap_orphan_needs` を追加。backlog に対応タスクが無いタスク級の票（`kind:` が
  plan-review / review / blocked）を掃除する。milestone 票の持ち主は従来どおり
  `reconcile_milestones`（`project.json` の status が正）なので触らない
- `reconcile_needs`（= ensure + reap）を毎パスの整合点にして、作ると消すを必ず対で回す。
  既に取り残されている票も、次のパスで自動的に片付く

**2. viewer の削除がファイルの生 unlink だった**

`backlog/<id>.md` を消すだけでは、票が残る（上記）／墓標（`tombstones.md`）が残らないので
charter 運用では次の再分解が同じタスクを作り直す／状態 git の同時変更裁定では `backlog/` は
実行側が正なので、本体側に書き込みがあると viewer 側の削除自体が取り消される。

- 削除を本体の**却下（reject）**へ委ねる（`commands/` へ投函）。archive への退避・needs の掃除・
  claim 解放・run の切り離し・墓標・決定記録が 1 つの操作として本体のプロセス内で起きる
- 実行中（doing）は押した瞬間に理由を返す（本体側の拒否と二重）

**3. 消したものを画面から戻せなかった**

却下は「作り直さない」記録（墓標）を残すので、同じ題は再投入も再分解もされない。解除は
CLI（`agent-project revive`）しか無く、Windows の画面だけで運用しているとやり直せなかった。

- `revive` を `commands/` ドロップで受けられるようにした（プロジェクト単位・タイトル指定）
- タスク画面に「却下済み（墓標）」の一覧と解除ボタンを追加。理由・日付・バージョンも見える
- タスクを失った要対応カードは画面側でも即座に落とす（本体の掃除を待たずに視界から消える）

### agent-project: 複数 PC 共有で「ステージに乗ったまま同期停止」と「バックログ分解の多重発火」を修正

複数 PC で 1 つの状態リポジトリを共有すると起きていた 2 つの実害を直した。

**一方の状態リポジトリがステージに乗ったまま同期が止まる**

state 同期の export は「detached worktree でコミット → CAS でブランチ前進 → 実 index を
追随（`_refresh_index`）」の順で進む。最後の追随の**前**にプロセスが死ぬ（夜間計画停止の
SIGTERM・watchdog abort・電源断）と、HEAD には入ったのに index だけ古いパスが残る。
作業ツリー＝HEAD なので次の export は「差分なし」で何も積まず、`git status` には
ステージ済みの変更が**恒久に**表示され続け（idle 中は journal も動かず自然回復しない）、
「状態リポジトリがステージに乗ったまま同期が止まった」ように見えていた。

- `_self_heal` に幻のステージの自己修復（`_realign_index`）を追加。判定は保守的に
  「index が HEAD と違うのに作業ツリーの内容は HEAD と一致する」同期対象パスだけ＝
  人のステージや編集中の実差分には触れない。次の sync で自動的に clean へ戻る

**バックログ分解が各 PC で勝手に走る**

「計画（charter 分解）は常に 1 台だけ」（複数 PC ガイド §3.2）の関門が 2 か所で破れていた。

- **ローカルが未 push の間、CAS が全滅していた**: `state_transaction` は「ローカルが
  リモートの祖先」を前提条件にしていたが、`state_git_interval`（既定 300 秒）の push 間隔の
  途中はローカルに未 push の state sync コミットがあるのが普通の運用状態。その間じゅう
  lease 更新・claim・自動割当が全て失敗し、lease が失効して計画役が PC 間を漂流→各 PC が
  好き勝手に分解する素地になっていた。トランザクションは remote HEAD を親に組み立てて
  成立させ、ローカルへは fast-forward できなければ決定的 3-way（`_integrate`）で合流する。
  push が通った後にローカル反映で失敗しても False を返さない（リモートで確定した claim を
  「失敗」と読み、孤児の doing を残さないため）
- **起動時にピアが見えない PC は関門ごと素通りしていた**: `cmd_run` の振り分けは起動時の
  `_coordination_active`（origin + 生存ピアの観測）で決まるため、他 PC の status がまだ
  同期されていない／stale な起動直後は charter ありでも素の `project_watch` に入り、以後
  lease を一切見ずに毎パス分解していた。`project_watch` のパス頭に controller 関門を追加:
  coordination が有効なら lease を取れたパスだけ `cmd_project`（分解・評価・milestone 整合）
  を起こし、取れなければ割当タスクの消化（runner）だけを行う。再分解要求
  （`.replan.request`＝ノード局所の明示アクション）だけは lease 無しでも通す
- `run_watch` の計画役分岐を「coordination 有効かつ controller」から「controller」へ:
  ピアが消えて単独に戻った PC（coordination 非活性）が計画を止めないようにした。多 charter
  構成では 1 watch パスで全 charter を 1 巡させる（従来は毎パス先頭の 1 本だけ）。charter
  更新での起床も coordination の有無に依らず効くようにした

### agent-dashboard: WSL 側の宣言が Windows の画面から読まれていなかった（P0-2 の残り）

正典構成（Windows の画面 + WSL の実行エンジン）で、**実行エンジンと共有する置き場を
`os.homedir()` で解決していた経路がもう 1 本残っていた**。P0-2 で指示の投函先を
`engine.agentsHome()` へ寄せたときの取りこぼしで、症状も同じ「押しても・書いても何も起きない」。

**host.yaml の `repos[]` が一度も読まれていなかった**

`nodeRepos.js` が `~/.agents/agent-project.host.yaml` を Windows 側のホーム
（`C:\Users\<user>\.agents`）に探していた。宣言は WSL 側にあるので常に「宣言なし」に倒れ、
CLIチャットの起動先と検収差分が使えない。**画面は逆に「`repos[]` に url と local を書くと
選べます」と案内する**ため、書いてある人には直しようがない（グレーアウトの理由表示が、
実際の原因と正反対のことを言う）。

- 置き場の解決を `engine.agentsHome(cfg)` の 1 実装へ寄せた（⚙ 設定のディストロ／
  ベースパスもここで効くようになる）。`AGENT_PROJECT_AGENTS_HOME` の優先は従来どおり
- 宣言された `local`（実行側が書く POSIX パス）を、この画面から届く形へ寄せてから実在を
  確かめる。変換前のパスで `statSync` していたため、置き場を直しても「実体が無い」判定の
  ままだった。寄せ先は実体の在り処で 2 通り:
  - `/mnt/<drive>/…` → `<drive>:\…`（**成果物リポジトリのクローンは Windows 側にあり得る**。
    状態リポジトリと違って flock と rename の原子性を要求しないので ext4 に置く必要が無い。
    UNC へ寄せると `\\wsl.localhost\<distro>\mnt\c\…` の二重経由になり、実体がすぐ隣の
    `C:\` にあるのに Windows のファイル共有が通せない）
  - それ以外の POSIX → `\\wsl.localhost\<distro>\…`（`engine/status.json` の
    `children[].root` と**同じ規則・同じディストロ**。既定ディストロへ丸めない）
  - この変換は成果物クローンの解決に閉じ込め、状態ルートを寄せる `toViewerPath` は
    触らない（設計 §4.6 が `/mnt` 経路を意図的に廃止している。状態は ext4 だけが正）
- 検収差分（`git:diff`）も同じ解決を通す。IPC から設定を渡すようにした
- `no-git-writes.test.js` の「この PC のホームで解決しない」検査の対象に `nodeRepos.js` を
  追加した。P0-2 で入れた不変条件が**1 ファイルしか見ていなかった**ため素通りしていた

**届かないプロジェクトを黙って消していた**

サイドバーは実体に届かないプロジェクト（`exists:false`）を無言で捨てており、ディストロ設定の
ずれ 1 つで一覧が空になった。そのとき画面は「このエンジンにはプロジェクトが登録されていません。
… host.yaml にプロジェクトを追加してください」と案内する——登録はされているので、人は
host.yaml を見に行っても間違いを見つけられない。

- 届かないプロジェクトは**消さずに非活性で並べる**（実行側が宣言したパスと、次に見る場所を
  添える）。「登録されていません」の案内は、本当に 1 件も宣言が無いときだけ出す

### agent-project / agentcore / agent-flow / agent-amigos: 契約の一本化（P2）

修正計画は [`docs/plans/2026-07-26-open-items-and-concerns.md`](docs/plans/2026-07-26-open-items-and-concerns.md) §7.3、
詳細設計は [`docs/plans/2026-07-26-p2-contract-consolidation-detailed-design.md`](docs/plans/2026-07-26-p2-contract-consolidation-detailed-design.md)。
**同じ規則が複数実装に割れている**ものを畳む段。片方だけ育つと、板の入札選別は fail-close
なので**誤動作ではなく無言の不参加**（「なぜかこの PC だけ仕事が来ない」）として出る。

**ノード契約バージョンの定義を 1 か所にした（P2-1）**

`CONTRACT_VERSION` が 3 箇所（`agentcore/board.py` の判定・`resident/status.py` の宣言・
dashboard `engine.js` の期待値）にあり、片方だけ上げると「版 2 と宣言しつつ版 1 で判定」に
なっていた。正典を `agentcore.board` にし、`resident/status.py` は import（`contract_compatible`
の重複本体ごと削除）。dashboard の定数は残るが、**Python の正典を実際に読んで**突き合わせる
ゴールデンテストで縛った（写しそのものが問題なので、写しを機械に突き合わせさせる）。

**板へ他 PC の絶対パスを配るのをやめた（P2-2）**

`nodes/<id>.json` に host.yaml の `repos[].local`（手元クローンの絶対パス）を載せていたが、
`repos.schema.json` は同じ値を「ホスト固有なので共有レジストリには置けない」と宣言しており、
S3 の動機と正面から矛盾していた。**読み手を全部数えたところ 1 人も居なかった**——入札判定は
name と正規化 url、画面は url からラベルを作るだけ、doctor は心拍だけ。速度最適化としての
`local` は請負ノードが自分の host.yaml から解決する（板は経路に無い）。publish をやめ、
`board.schema.json` の `$defs.node.repos` を実装の形（2 形を受ける・`local` 禁止）へ直した。

**宣言していたのに読まれていなかった 2 つを入札判定へ繋いだ（P2-3）**

- `workloads`（引き受けるエンジン）。スキーマは「公示の workload がこれに含まれないと入札
  しない」と宣言していたが、`eligible()` に引数自体が無かった。**明示宣言だけを正**とし、
  設定から導出しない——`amigos_bus` の有無などから推測すると、宣言していない PC が黙って
  入札をやめる。宣言が無ければ板にも出さない（宣言していないことを宣言しない）
- `budget.max_concurrent`。「超過時は新規入札を控える」という契約に実装が無く、忙しいノードが
  仕事を掴んだまま枠待ちで塞いでいた。板上の自分名義の非終端 `status/` 件数で自己抑制する
  （枠の真実は板にあるので、常駐体のワーカープールと二重管理しない）
- **`0` の意味を契約側（無制限）へ揃えた**。実装は「0 = 未設定 → 既定 4」と真逆に読んでいた。
  「未宣言なら 4」は設定を読む側の既定へ移し、`NodeWorkerPool` は上限なしを表現できるように
  した。**`max_concurrent: 0` を「既定 4 のつもり」で書いている PC は更新前に書き直すこと**
  （キーごと省略すれば従来どおり 4）
- あわせて **agent-amigos が `agent_cli` を渡し忘れていた**のを直した。判定は fail-close なので、
  `requires.agent_cli` を持つ公示に amigos ノードは**永久に入札していなかった**

**板への書き込みが排他を通るようにした（P2-4）**

S8 で足した `write_bid` / `write_cancelled` / `write_award` だけが flock と `_ensure()` を
通らず直接書いていた。転送層の破損時再クローン（`rmtree`）や `pull --rebase` と並走すると
入札・中止マーカーが消えうる。あわせて `write_bid` の戻り値（冪等で書かなかった場合）を
受理レシートの文言に出すようにした——常に「入札しました」と返すと、押したのに板へ
届いていない場合と区別が付かない。

**同じ文字列・同じ規則の写しを畳んだ（P2-5）**

- `DIFF_CRITERION`（差分の常設基準）を本体の 1 か所へ。スキルへは解決済みの文を入力で渡す
  （P1-1 の `side_effects_text` と同じ手）。2 か所で育てると、検証レポートに出る基準文と
  エージェントが見た基準文が黙ってずれる（判定は番号で突き合わせるので機械は気付かない）
- 退避の指示文の**枠**を `agentcore.agentcli.spill_instruction` へ。呼び出し側が決めるのは
  「何の全文か」だけ（定義側の `spill.instruction` へは寄せない——あちらは権限フラグ置換と
  セットの別機構）
- host.yaml の `repos:` 正規化を `agentcore.repolocal.normalize_repos` の 1 実装へ
  （写しの側では `local: null` が `"None"` という文字列のパスになっていた）
- JS の URL 正規化に **symlink 解決**を足して Python と揃え、両言語をゴールデン表で縛った
- 板レイアウトの名義綴りを `protocol.safe_name` へ（flow だけ置換文字が `_` で、正規化されて
  いない node_id では**書いたファイルと読むファイルが別名**になり、手動入札の受け皿が
  効かなくなる）

**CI が 31 件のテストを黙って素通りしていた**

`unittest discover` は `unittest.TestCase` のサブクラスしか集めない。`resident` の単体テスト
4 ファイル（scheduler / supervisor / worker / status）はモジュール直下の `def test_*` で
書かれており、**CI で緑とも赤とも報告されていなかった**（`if __name__` の手動実行でしか
走らない）。P3-1 で CI を入れたとき「4 パッケージの単体テスト」と書いたが、実際にはここが
抜けていた。標準の `load_tests` プロトコルで拾うようにした（`tools/agent-project/tests/_functest.py`。
pytest は足さない——stdlib だけで走るのがこのリポジトリの規約で、CI もそれ前提）。


### 破壊的変更: agent シリーズの python 下限を 3.11 へ

インストーラの版検査（`tools/agent-tools/install.sh`）と文書の要求が **3.9** だったが、
新設した CI が回すのは **3.11** だけで、宣言と検査が食い違っていた（積み残し §7.7 C-1）。
**検査に宣言を合わせる**方向で解消する——誰も動かしていない版を CI で支え続けても、
その版で動く保証は「たぶん」以上にはならない。

- `install.sh` は 3.11 未満の python を除外する。案内に **Ubuntu 22.04 の既定は 3.10**
  であることと、その系での入れ方（deadsnakes / 24.04 以降は既定で足りる）を書いた
- 反映先: セットアップガイド §1 / agent-flow の README・SKILL
- **既存ノードへの影響**: 3.10 以下で動かしている PC は、更新前に python を上げる必要がある
  （自己更新は `install.sh` を叩くので、上げていないノードはそこで止まって理由を表示する）

### リポジトリ / agent-project: 文書と CI（P3）

修正計画は [`docs/plans/2026-07-26-open-items-and-concerns.md`](docs/plans/2026-07-26-open-items-and-concerns.md) §7.4。
実機 canary（R1）と独立に進められる 4 件。

**CI を新設した（P3-1）**

このリポジトリには CI 設定が 1 つも無く（`.github/workflows` も `.gitlab-ci.yml` も
Makefile も無い）、全緑の担保は人が手元で回すことに依存していた。
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) で 3 系統を回す:

- 4 パッケージの単体テスト（agent-project / agent-flow / agent-amigos / agentcore）。
  **agentcore はテストルートが 2 つある**（`agentcore/tests` 58 件 と
  `agentcore/agentcore/tests` 74 件）ので両方を明示する——片方だけ `discover` すると
  残りが黙ってスキップされる。設定キーの構造テスト（P0-4）は agent-project 側に含まれる
- agent-dashboard の `npm test`（実行時依存だけを `npm install --omit=dev` で導入する。
  electron は開発依存でテストからは起動しないため入れない）
- 利用者向け文書の内部名検査（R10）

**CI を入れる前に間欠失敗を 1 件潰した（P3-1）**

`test_daemon.OrphanRecoveryTests.test_reclaim_after_owner_lease_expiry` が 40 回中 3 回失敗して
いた。`reclaim_request(..., lease_sec=0.01)` は「自分の claim を書く → 勝者判定」の往復より
lease が短く、**claim した瞬間に自分の lease が切れて自分で勝者判定に負ける**。実装ではなく
テストの作りの問題（0.01 秒 lease が非現実的）で、失効を時間ではなく値（`lease_until` を
過去へ）で作る形に直した。CI は手元より遅いランナーで回るので、時間依存はここで潰しておく
（間欠的に赤い CI は無い CI より悪い）。

**R10 検査を機械化した（P3-1）**

素朴な `grep` では成立しない: 利用者向け文書にはガイドのファイル名や契約のスキーマ名が
正当に含まれ、それらは内部名を含む（隠すのは製品名としての内部名であって契約の語彙ではない）。
[`tools/ci/check_user_docs.py`](tools/ci/check_user_docs.py) は**本文だけ**を見る——
コードブロック・インラインコード・リンク先・パス・ファイル名を落としてから検査する。
どうしても本文で言及する行は `<!-- r10-allow: 理由 -->` で免除し、理由が行に残る。
検査自身の単体テスト（`tools/ci/tests/`）で「何を落として何を見るか」を固定した。

**`docs/guides/multi-pc-operations.md` を全面改訂した（P3-2）**

常駐一本化の前に書かれた記述（廃止済みの `start` / `stop`、通らない `status --root`、
旧モデルの「各 PC 1 daemon」）が 10 箇所以上残り、一部だけ新しいという最も読み違えやすい
状態だった。「PC に 1 本の常駐体 + プロジェクトごとの子」「git を触るのは常駐体だけ」の
現行モデルで書き直し、dashboard の自動 pull を前提にした障害説明（凍結した clone が
ゴースト表示の主犯という記述）も、pull 経路が消えた現行に合わせて畳んだ。

**doctor に host.yaml の起動前チェックを足した（P3-3）**

`projects[].root` の綴り間違い・origin の取り違え・設定 2 層の帰属違反は、これまで
「子の起動失敗 → 隔離表示」という最も遠い症状でしか観測できなかった。
`doctor_host_projects_findings` が root の存在・git トップレベル・origin 一致・
チェックアウトのブランチ・E1〜E7 相当の設定検査を宣言ごとに見る。

- 層契約の判定は起動経路と**同じ関数**（`configfile.layer_findings`）を呼ぶ。doctor 用に
  別判定を書くと「doctor は緑なのに起動が止まる」が生まれる。fail-fast の出口だけを
  `_validate_layers` に残し、判定は共有した
- `state_repo` を宣言していて root が無いだけの状態は**所見にしない**（初回起動で clone
  される正常な形。ここを警告にすると新規プロジェクトの度に赤が出る）

**S1 詳細設計に記述訂正を追記した（P3-4）**

本文が現存しないシンボル（`_validate_layers` の引数形・`_STATE_SIGNIFICANT`）を参照して
いた。結論は正しいので本文は書き換えず、§6.1 に訂正表だけを足した。

### agent-project / agent-dashboard / agentcore: 実機 canary の前に直す 4 件（P0）

詳細設計は [`docs/plans/2026-07-26-p0-pre-canary-fixes-detailed-design.md`](docs/plans/2026-07-26-p0-pre-canary-fixes-detailed-design.md)。
いずれも canary（複数 PC で 1 週間）で観測したい異常と同じ症状を出す不具合で、直さずに入ると
「設計の問題か既知バグか」を切り分けられない。

**起動直後の SIGTERM で子だけが生き残っていた（P0-1）**

`serve` はシグナルハンドラを子の起動・`write_status()`（git 観測を含む）・tick 開始の**後**に
設置していた。この窓で SIGTERM が届くと既定ハンドラで即死し、`graceful_shutdown` が走らずに
`run --watch` の子が監督者不在で残る——次の起動で同一プロジェクトにループが 2 本並ぶ。
`systemctl restart` がそのまま踏む経路。

- ハンドラ設置を**起動バナーより前**へ移した。バナーは「以降は取りこぼさない」境界で、
  テストの待ち合わせ点にもなる。停止要求が入っていれば tick を始めず子を畳んで 0 で返る
- 2 度目のシグナルは握らず既定ハンドラへ戻す（停止処理が詰まったときの逃げ道を残す）
- **子（`run --watch`）にも同型の窓があった**: `_install_sigterm` が `state_sync`（git）と
  controller lease の取得より後で、その窓で死ぬと lease を握ったまま `finally` を通らない
  （次の子が最大 120 秒 controller へ昇格できない）。設置を先頭へ移した
- 間欠失敗していた `test_serve_exits_cleanly_on_sigterm`（5 回中 1 回）が決定的に緑になった

**dashboard のボタンが常駐体に届いていなかった（P0-2）**

正典構成（Windows の画面 + WSL の実行エンジン）で、指示の投函先と取り込み先が**別ファイル
システム**だった。同じ dashboard の `engine.js` は `wslpath` で WSL 側 home を解決して
`engine/status.json` を読んでいるのに、この経路だけ `os.homedir()` に落ちていた。

- 投函先の解決を `engine.agentsHome()` の 1 実装へ寄せた。旧ホーム（`~/.agent`）への
  フォールバックは**持たない**——実行エンジン側に無いので、書けるのに誰も読まない場所が増える
- **置き場を直しても届かなかった**: 指示の `board:` は板の**所在**（`host.board`）なのに、
  dashboard は板の**作業フォルダ**（`delegation.boardRepos`）を載せており、常駐体の
  完全一致検査に必ず引っかかっていた。常駐体が `engine/status.json` の `board` へ
  `workdir` を載せ（additive・契約版据え置き）、画面はそれと突き合わせて所在へ翻訳する
- 参加していない板への指示は投函せず**その場で理由を返す**（`.err` で後から知るより短い）
- `delegation.nodeCommandsDir` を既定と設定画面（「この端末への指示の受け渡し先」）に出した
- 実行エンジンと共有する置き場を `os.homedir()` で決めないことを構造テストで固定

**大文字ホスト名の PC が 2 名義になっていた（P0-3）**

`Config.node` だけが独自のサニタイズ（**小文字化しない**）で導出されており、`DESKTOP-X` の
ような PC はプロジェクト状態側 `status/DESKTOP-X.json` と板側 `nodes/desktop-x.json` に
割れていた。人が板の端末一覧（小文字）を見て書いた `- node:` を**どのノードも拾わないまま
ready で固まる**。

- `agentcore.nodeid.default_node_id()` を新設し、ホスト名の取り方まで含めて 1 実装に。
  agent-project / agent-flow / agent-amigos が同じ関数を使う
- `AGENT_PROJECT_NODE` と `--node` も正規化する（host.yaml 宣言だけが正規化されていた非対称）。
  綴りを変えたときは 1 行知らせる——黙って変えると「指定した名前で動いていない」ことに気付けない
- `- node:` の照合も正規形で行う。正規化前に書かれたタスクも同じ PC のものなら拾える
- `doctor --node-id-cutover` がプロジェクト状態側の残骸（`status/` の旧名義・旧名義の
  `claim_owner`・手で割り当てた `- node:`）も見る。切替手順書に読み方を追記

**`remote_review: observe` が到達不能の死んだコードだった（P0-4）**

`CONFIG_DEFAULTS` にキーはあり層検査も通るのに `Config` へ渡しておらず、読み出しは
`getattr(cfg, …, "settle")` だったので**プロジェクト yaml に `observe` と書いても常に
`settle`**。S5 の verifier キーと同型の 2 度目。

- `Config` へ配線し、値域外は警告して既定へ倒す。`mr.py` の `getattr` フォールバックは削除
  （読み手が庇うと、配線が落ちても静かに動き続ける）
- **設定キーの構造テストを新設**（`tests/test_config_keys.py`）: `CONFIG_DEFAULTS` の全キーが
  ①`Config` のフィールドを持ち ②設定ファイルから宣言すると実際に値が変わる、を検査する。
  除外は理由の記入を必須にし、番兵も除外も無いキーはテストが落ちる

### agent-dashboard / agent-project / agent-flow / agentcore: 板の観測・操作 UI と診断の対話化（S8・S9-4）

詳細設計は [`docs/plans/2026-07-26-s8-s9-4-board-ui-and-doctor-chat-detailed-design.md`](docs/plans/2026-07-26-s8-s9-4-board-ui-and-doctor-chat-detailed-design.md)。

**まず直したもの: `git+` 板では dashboard のボタンが誰にも届いていなかった**

板への `cancel` / `award` は板の作業ディレクトリへファイルを置くだけで、push する主体が居ない。
ローカル dir の板でしか成立しておらず、**`git+` 板では押しても何も起きないボタン**だった。
新しい操作（手動入札）を足す前にここを直した。

- 板への書き込み（`board-cancel` / `board-award` / `board-bid`）を**ノード宛て指示ドロップ**へ
  一本化した。新契約 [`agent-node-command.schema.json`](schemas/agent-node-command.schema.json)、
  置き場は `$AGENT_COMMANDS_DIR`（既定 `~/.agents/commands/`）。**板へ書くのは常駐体だけ**
- プロジェクト配下の `commands/` ではなく**ノードスコープ**に置いた——板はプロジェクトに属さず、
  プロジェクトを 1 つも持たない PC からも板を操作できる必要がある
- 形（`<name>.json` / `processed/` / `.err`）と述語は `agentcore.commands` で共有する。
  利用者から見える「送信済み → 受理済み → 失敗バナー」を 2 種類作らない

**同一ノードで 2 プロジェクトが同じ板を巡回すると、同じ公示を二重に取り込んでいた**

`poll_board` の「取り込み済みか」の判定が**自分のバスだけ**を見ていたため、A のバスへ取り込んだ
直後の公示を B のバスがもう一度取り込む（同一ノードでの二重実行）。判定を板の
`status/<who>.json`（自分が落札・引き渡し済みの印）へ移した——板が真実という原則にも合う。

**R2a: 常駐体の board tick（30 秒）**

- `nodes/<node-id>.json`（能力宣言）の**書き手ができた**。host.yaml の `tags` / `agent_cli` /
  `repos[]`（`local` 込み）/ `budget.max_concurrent` がそのまま載る（積み残し P1-a の決着）
- **心拍だけの更新は 5 分に 1 回**に律速する。30 秒ごとに書き換えると板に無意味なコミットが積む
  （宣言の内容が変わったときは間隔に関わらず即書く）
- ノード宛て指示を取り込み、板へ書いて push する。終端済み公示・別の板宛て・未知の指示は
  理由つきで `.err` へ退避する（黙って無視しない）
- `engine/status.json` に `board` ブロックを足した。**dashboard が「参加しているか・手動入札
  できるか」を判断する唯一の根拠**——host.yaml と agent-flow 設定を dashboard が自前で読み解くと
  宣言の解釈が 2 実装になる
- **入札の自動判断は tick に置かない**。自動入札は従来どおり各プロジェクトの `participate` が担う
  （同じノードに 2 つ目の入札主体を置くと二重落札になる）

**入札選別規則を `agentcore.board` へ 1 本化**

agent-flow と agent-amigos が「同じ仕様・別実装」で持っていた `board_eligible` を集約した
（`agentcore.repolocal` が解決したのと同型の問題）。契約にあって両方が見ていなかった
`requires.agent_cli` と `requires.contract_version` の判定も入れた（どちらも fail-close）。
判定材料の正典は host.yaml で、agent-flow 設定の `board_repos` / `board_tags` /
`board_agent_cli` は**明示上書きへ降格**した（S1 の取りこぼしだった二重宣言の解消）。

**S8: 板の観測と操作（委譲の独立タブは作らない）**

置き場は人の問いごとに 3 か所へ割った——orchestration タブは全体設定になったので、
そこに動く一覧は置けない:

- **タスク画面**: 委任（offloaded）タスクに「委任先: pc-b — 実行中」の 1 行と、
  詳細の「委任」行（中止ボタン付き）。データ源は板のファイルだけ
- **参加タブ**: 募集中の公示を「引き受ける」候補として出す（手動入札）。**引き受けても実行
  できない端末ではボタンを理由付きで非活性**にする——プロジェクトを 1 つも持たない端末の
  落札実行は未対応（Phase 5 / R2b）。「操作だけ増えて実行できない」状態を構造的に防ぐ
- **全体設定 → 同期**: この端末の参加状況と参加ノード一覧（心拍・引き受けられるもの・
  手元にあるリポジトリ・契約の版）。他 PC の絶対パス（`local`）は出さない
- **手動入札は「自己抑制の上書き」**。自分名義の有効な入札がある公示は、`poll_board` が
  repos/tags 照合を問わずに取り込む——人が押した意思がここで通る

**S9-4: 失敗診断の対話化**

- 失敗診断のボタンを「AIと対話で診断」（既定）と「文面を生成」（従来のヘッドレス 1 発）に
  分けた。原因究明は 1 往復では終わらない
- **120,000 字のスナップショットは対話セッションへ持ち込めない**（tmux への注入は改行を
  含められない 1 行、全文をファイルで渡す前提は読み取り専用でファイル読み取りごと落とす
  CLI で成立しない）。送るのは**ブリーフ 1 行（2,000 字上限）＋ 全文ファイルのパス**で、
  全文は「読めるなら読め」の追加資料に留める——S9 のスキーマを触らずに済む
- 診断は**使い捨て**（`readonly_args` + `no_session_args`）。セッション名も `agent-doctor-` と
  別系統にする（読み取り専用のつもりの窓が作業セッションに合流すると書き込みができてしまう）
- 同一の要対応の再診断は既存セッションへ attach し、**ブリーフは送り直さない**
- 読み取り専用を保証できない CLI（`readonly: best-effort`）では、その旨を画面に出す
- cwd はタスクの書込先リポジトリ（このノードの宣言から解決）→ プロジェクトの順

### agent-project / agent-dashboard / agent-flow: 「エージェントが書き、人が直す」バックログと spec の 3 段（S6・S7）

詳細設計は [`docs/plans/2026-07-26-s6-s7-backlog-planning-detailed-design.md`](docs/plans/2026-07-26-s6-s7-backlog-planning-detailed-design.md)。

**まず直したもの: `acceptance` の受け渡しが 4 か所で切れていた**

S5 は受入基準を「done の根拠」に据えたのに、**それを生む側・人が直す側のどこにも通っていなかった**。
新機能より先にここを直した（通っていない表現の上に生成側を乗せても、人には見えないまま回る）。

- `task_from_spec` が `acceptance` を既知キーとして扱うようにした。従来は「未知キー保持」の枝に落ち、
  配列が **`- acceptance: ['A', 'B']` という Python の repr 1 行**になっていた
- 投入時の「検証の材料があるか」の判定を `has_verify_plan` と同義に揃えた。従来は acceptance を
  数えておらず、**受入基準しか持たないタスクが inbox（人の triage）へ落ちていた**
- 計画レビュー票に受入基準を箇条書きで載せた（人が読んで直す一次表現が票に無ければ、直す機会は無い）
- `revise` で受入基準を編集できるようにした（`--acceptance` 複数指定＝全行置換）。dashboard の
  タスク編集にも「受入基準」欄を追加

**S6: バックログの生成・レビュー・整合**

- **`backlog-planner` スキル**（`.github/skills/backlog-planner/`）が charter を分解する。設定
  `planner_skill` で差し替え可。**見つからなければ組み込みプロンプトへ落ちる**——計画が止まると
  プロジェクトが 1 歩も進まないので、スキルは必須にしない
- **必須項目の決定的ゲート**（`plan_sections: required`・既定）: `why` / `desc`（作業概要）/
  `acceptance` / `size` の欠落は機械で見て**1 回だけ再要求**し、それでも欠けるタスクは**捨てずに
  人の目へ回す**（`plan_review` on なら proposed で票に欠落を書き、off なら draft）。捨てると
  「プランナーが何も出さなかった」としか見えず、charter が悪いのかスキルが壊れたのか切り分けられない
- **重複はプランナーに出させない**: 既存タスク一覧と墓標を入力に載せる。投入側の Jaccard 照合は
  最終防衛線として残す（スキルは差し替え可能なので、投入側の護りは外さない）
- **墓標（`tombstones.md`）**: `reject` が 1 行残し、同じタイトルは再提案されない。人が手で書ける。
  `agent-project revive <タイトル>` で解除、`replan --revive` は今回だけ無視（行は消さない）
- **抑止は正規化タイトルの完全一致のみ**。類似（Jaccard）は投入を止めず票に注記するだけにした——
  抑止は取り返しがつかない（黙って消えるので人は気づけない）が、提示は取り返しがつく
- **人が直した印**（`- edited: human`）を `revise` とレビュー票の確定で付け、プランナーへ
  「作り直すな」として届ける。題を直しても原題（`- planned_title:`）が指紋として残るので、
  次の replan で元の題のタスクが復活しない
- **随時入力の整合パス**: `enqueue` / `inbox/` / `intake_cmd` は重複照合・charter タグ付与・墓標照合を
  通ってから投入される。重複は新規作成せず理由を返す
- **観点メモ（`notes/`）**: 書き溜めても plan は**自動では消費しない**（メモは「まだ決めていないこと」の
  置き場で、勝手にタスク化されると人はメモを書けなくなる）。`agent-project distill-notes` か
  dashboard の「メモ」ボタンを押したときだけバックログ候補になり、取り込めたメモは `notes/archive/` へ

**S7: spec を 3 段にする（ブラウンフィールド適合）**

- 投入時採点 `max(c,r,a)` で スキップ / **ライト spec**（`design.md` 1 枚・展開なし）/ フル spec を選ぶ。
  `spec_threshold_full`（既定 3）/ `spec_threshold_light`（既定 2）。旧 `spec_threshold` は full の
  別名として読むので既存設定はそのまま効く
- ライト spec は「既存コードのどこをどう変えるか（変更方針・影響範囲・受入条件の差分）」だけを書かせる。
  要求は charter とタスクの `why`/`desc` に既にあり、分解は元タスクの粒度で足りる——3 点セットの
  オーバーヘッドの正体はこの 2 枚だった
- plan と spec ルーティングの直前では `repo_map` 設定に関わらず `context/<repo>.md` を用意する。
  作業概要の「変更対象」も影響範囲も既存コードの文脈が無ければ書けず、opt-in のままだと決定的ゲートが
  恒常的に発火して**設定 1 つで機能全体が空回りする**

**あわせて直した不具合**

- **S5 の設定キーが Config に届いていなかった** — `verifier` / `verifier_skill` /
  `verify_side_effects` は `CONFIG_DEFAULTS` にあるだけで `Config` へ渡されておらず、読み出しは
  `getattr` の既定に落ちていた。つまり `verifier: false` も `verifier_skill:` も**設定しても効かなかった**
- **`has_consumable` がタグ無しタスクを数えていなかった** — スコープ判定が完全一致を要求し、
  `_existing_titles` の述語（タグ無しはどの charter にも属しうる）と食い違っていた。結果、消化可能な
  タスクがあっても**再分解が誤発火**していた。`_has_project_human_wait` も同じ穴で人待ちを見落として
  いた（`task_charter_name` の戻り「default」を "" と比べていた）。述語を
  `task_belongs_to_charter` 1 つに寄せた
- **`--granularity` が agent-flow へ渡っていなかった** — 外側の backlog だけが設定に従い、内側の
  タスクグラフは常に auto で分解されていた（agent-flow 側の受け口は存在した）
- **flow-planner のスキル名がハードコードだった** — agent-flow に `planner_skill`（既定
  `flow-planner`）を足し、`worker_skill` と対称にした
- **組み込みプロンプトが既存タスク・墓標・メモ・再要求を落としていた** — スキル未導入の環境では
  再要求が同じプロンプトの繰り返しになり（欠落が直らない）、`distill-notes` がメモを読まないまま
  分解していた


### agentcore / agent-project / agent-flow: リトライのバックオフ待ちを 1 つの seam に集約

リトライ回数を検証するテストが **CPU 高負荷のときだけ落ちる**問題を直した。

原因はテスト側にあった: リトライの検証は `time.sleep` を差し替えて呼び出しを記録するが、
`time` は stdlib の共有モジュールなので、その差し替えは **CPython の `subprocess` 内部**にも効く。
`subprocess.run(timeout=…)` はプロセス終了を 0.001 秒から倍々（上限 0.05）でポーリングしており、
負荷で `git clone` が長引くとその sleep が記録へ混入して「バックオフ 1 回」の検証が壊れていた。

- `agentcore.transport.backoff_sleep` を追加し、**リトライの待ちはすべてここを通す**
  （transport の clone / git ロック / push 競合、agent-flow の gitcache・workspace・stategit・
  transient リトライ、agent-project の gitcache・stategit・coordination）
- 差し替えの対象が自分たちの関数 1 つになったので、stdlib の内部ポーリングは巻き込まれない
- `time.sleep` の直接呼び出しが seam 1 か所に限られることをテストで固定した
  （増やすと同じ壊れ方が戻るため）


### agent-project / agent-dashboard: 検収の MR/PR 一本化と、証跡ベースの検証（S4・S5）

**S4: 検収を MR/PR へ寄せ、決着を決定的シグナルで定義する**

- **フォージ側の人の操作が決着になる**。`poll_task_mrs` が検収待ちタスクの MR を照会し、
  マージ=承認 / 未マージクローズ=却下 / `status:changes-requested` ラベル・レビュー=差し戻し
  として決着させる（常駐体の sync 周期）。**コメント本文のキーワード推定は使わない**——
  書き手の言い回し 1 つで判定が変わり、変わったことに気づけないため。差し戻しに注入するのは
  **未解決**の discussion だけ（解決済みまで流すと、一度直した指摘が毎回積み直されて収束しない）。
- 到達不能（ネットワーク断・トークン失効）は決着しない。「未マージ＝却下」と読むと、回線が
  切れただけで成果が却下される。
- `remote_review: settle | observe`（既定 settle）。observe は表示のみ（移行用）。
- **フォージ境界**を切り、実装は GitLab のみ。GitHub / Gitea は未対応として「フォージ無し運用」
  へ倒す（1 回だけ警告）。動作確認できる環境が無いまま書いた API クライアントは、動くかどうか
  分からないコードが増えるだけ。認証情報は環境変数 / rc ファイルのままで、**設定 2 層のどちらにも
  置かない**（host.yaml は平文で PC に残り、プロジェクト yaml は全 PC へ配られる）。
- **検収カード**: MR があるあいだはカード内で差分を開かせない（レビューの正は MR 一本）。
  MR を持たないタスクだけ、S3 のノード宣言（host.yaml `repos[]`）から解決したクローンで差分を出す。
  worker の作業ツリーは `/tmp` で消えるため、`delivery.path` 前提の経路は別マシンの dashboard では
  そもそも動いていなかった（これが C5 の実体）。解決できないときは理由と宣言のしかたを表示する。

**S5: 「コマンドの exit 0」から「基準 × 証跡」へ**

- バックログに **`- acceptance:`（複数行可）** を追加した。`Task.extra` が (key, value) のリストなので
  同名キーの複数行はそのまま往復する——スキーマもパーサも変えずに済んだ。`accept`（自然文 1 行）は
  1 項目の acceptance として扱う（後方互換）。
- settle で決定的 `verify:` が無いタスクは、**検証エージェント**が基準ごとに実際にコマンドを
  試行錯誤して充足を確かめ、**判定 + 証跡**を返す。プロンプトと出力契約は
  `.github/skills/backlog-verifier/`（上位に置けば差し替え可・`verifier_skill` で名前も変更可）。
- 機械的な護りは 4 つ: **フェイルクローズ**（明示 pass が無ければ fail）/ **証跡必須**
  （pass なのに実行コマンドも参照ファイルも無ければ fail へ落とす）/ **差分の常設基準**
  （何も変えずに全 pass を返す道を塞ぐ・red-green の代替）/ **検収カードでの抜き取り監査**
  （証跡の薄い判定を警告表示。監査を別機能にせず人が毎回見る 1 枚に載せる）。
- **「検証不能」はリトライを焼かない**。環境にツールが無い等は失敗ではなく、直す先がタスクの中に
  無い。環境要因失敗と同じ扱いで理由付きで人へ回す（直して approve すれば同じ run の続きから）。
- 検証レポートを `verifications/<task-id>/<rev>.md` に保存し、needs 票に要約（基準 × 証跡の表）を
  載せる。**人検収で人が読むのはこの表**——コマンドの良し悪しは人には判断できないが、基準と証跡なら
  判断できる、というのが S5 のコンセプト変更そのもの。
- `verify_side_effects: workspace | network`（既定 workspace）。DB・外部サービスへの**書き込み**は
  どちらでも不可（検証は失敗するとリトライで何度も走るので副作用が累積する）。
- **廃止**: `accept` からの LLM 一発合成（`ensure_verify` の synth 経路）と、検証済み verify
  ライブラリ（`verify_lib_path` / `save_validated_verify` / `find_learned_verify`）。後者の
  置き換えは `verify-recipes/` で、**次回 verifier への参考情報**であって決定的ゲートには昇格させない。

詳細設計: [`docs/plans/2026-07-26-s4-s5-review-and-verification-detailed-design.md`](docs/plans/2026-07-26-s4-s5-review-and-verification-detailed-design.md)


### 全ツール: エージェント CLI 差分吸収レイヤ（S9-1〜3）

「この CLI をどう起動するか」の知識が **8 か所・4 実装**（agent-project / agent-flow /
agent-amigos / agent-dashboard）に散っていた。CLI の作法が変わるたび複数箇所の修正が要り、
実際に**同じ CLI でもツールによってフラグが違う**状態になっていた（claude が agent-project
では `--dangerously-skip-permissions` 付き・dashboard では無し、cursor は同梱定義と dashboard
の組み込み分岐で argv 自体が別物）。定義ファイル 1 枚に集約する。

- **組み込み CLI（kiro / claude / copilot / codex）も `agents/<name>.json` へ移した**。
  コード側に CLI 分岐は無い。組み込み名の予約も解除したので、上位ディレクトリ（`$KIRO_AGENTS_DIR`
  → プロジェクトの `agents/` → `~/.agents/agents/`）に置けば同梱定義を上書きできる——
  これが無いと「CLI の作法変更が JSON 1 ファイルで完結する」が成り立たない。
- **フォールバックの組み込みテーブルは持たない**。定義を解決できない `agent_cli` は明示エラー
  にする（インストール破損として読めるメッセージ）。テーブルを残すと「JSON を直したのに
  古い挙動のまま」という、いま消した二重管理が別の形で戻る。
- **契約の拡張**（`schemas/agent-cli.schema.json`）:
  `interactive`（対話 argv・`ready_pattern`・`ready_timeout_sec`・`prompt_inject`）/
  `readonly_args` + `readonly`（強制力の宣言）/ `write_args` / `no_session_args` /
  `command_suffix`（位置引数の末尾固定）/ `spill`（長大プロンプトの一時ファイル退避）。
  既存の定義（`cursor.json` / `ollama.json`）はそのまま有効。
- **Python ローダを `agentcore.agentcli` へ 1 本化**（agent-project / agent-flow / agent-amigos
  が共有）。`agentcore.repolocal` で URL 正規化を寄せたのと同じ判断で、解釈のズレが
  「同じ定義ファイルがツールによって別の argv になる」形で出るため。
  agent-dashboard だけは UI の応答性のため JS の自前ローダを持ち、**同じ定義から同じ argv が
  出ることをゴールデンテストで固定**した（`test/agent-cli-golden.test.js`）。
- **tmux 経由の起動がすべてこのレイヤを通る**（S9-3）。入力受付の検出パターン・タイムアウトは
  定義から来るようになり、定常業務（cowork）の tmux 実行も `agent_cli` 設定に従う——
  従来は `cowork.chatCommand` の**文字列固定**で、定常業務だけが常に kiro を起動していた
  （`cowork.chatCommand` は明示上書きとして残る）。
- **挙動が変わる点**: dashboard のヘッドレス LLM 呼び出し（charter 補完・Doctor・構造化
  Assist）は**すべて読み取り専用モード**で起動するようになった。「ファイルへの書き込みは
  ビュアー側が行う」という元々の護りの意図に argv を合わせたもので、移行前は charter 補完
  だけが権限フラグ無しだった。読み取り専用を保証しない CLI（`readonly: best-effort`）では
  警告を返す——このレイヤは argv を組み立てるだけで、フラグを無視する CLI への防御は持たない。
- **副産物の修正**: 失敗トリアージのヒントを「クラス一致」で引いていたため、読み込み済みの
  別 CLI 定義に同クラスの規則があるとその文言が出ていた（codex の usage limit に kiro の
  月間上限の案内が付く）。実際に一致した規則からヒントを採るようにした。

詳細設計: [`docs/plans/2026-07-26-s9-agent-cli-layer-detailed-design.md`](docs/plans/2026-07-26-s9-agent-cli-layer-detailed-design.md)


### agent-project / agent-flow / agent-dashboard: ノード固有ローカルクローン層と定常業務フォルダの登録（S3・S2）

Phase 1 の残り。どちらも「宣言は実行側が持つ」という同じ原則の適用。

**S3: ノード固有ローカルリポジトリ層**

- **`agentcore.repolocal`（新規）**: git URL の正規化一致を 1 実装に集約した。従来は
  agent-project の `_same_git_remote`・agent-flow の `_same_repo`・board の `_norm_repo_url` が
  別実装で、末尾 `.git` とスラッシュは 3 者とも吸収する一方 **小文字化は agent-flow だけ・
  ローカルパスの絶対化は agent-project だけ**という食い違いがあり、同じ 2 つの URL が経路に
  よって一致したりしなかったりしていた（`agentcore.nodeid` が解決したのと同型の問題）。
  host.yaml `repos[]` の読み取りと workspace spec への `local` マージもここに置く。
- **`local` の宣言場所を host.yaml `repos[]` に一本化**。共有 repos.json は charter から自動
  生成され状態リポジトリ経由で全 PC へ配られるため、1 台で書いた絶対パスが全ノードへ伝播して
  いた（C3/C4 の元凶）。repos.json の `local` は警告して無視する（移行先を示す・1 回だけ）。
  `schemas/repos.schema.json` の `local` / `dir` を deprecated と明記。
- **板の請負側の欠落を修正**: `poll_board` が落札した公示 workspace に自ノードの `local` を
  載せてから submit するようになった。公示に載るのは依頼側が見た URL だけで、請負側が自分の
  クローンを載せる実装が無かったため、板経由の仕事は手元に同じリポジトリがあっても毎回
  ネットワーク越しにミラーを取り直していた。
- agent-flow の provision は spec に `local` が無くてもノード宣言から解決する。
- **dashboard の CLIチャットに起動先（cwd）の選択**を追加。S1 以降プロジェクトのフォルダは
  状態リポジトリの clone なので、コードを触りたくて CLI を開いてもそこには 1 行もコードが
  無い。この PC にクローンがある成果物リポジトリを選べる。宣言が無いものは消さずに非活性で
  理由付きで並べる（一覧から消えると宣言し忘れに気付けない）。

**S2: 定常業務フォルダの dashboard 管理**

- dashboard 設定に **`cowork.roots[]`** を追加。agent-project 管理外のフォルダ（kiro-loop 設定や
  `.statemachine/` を持つだけ）を定常業務画面で扱えるようにする。W2-4 で一覧の唯一の源を
  `engine/status.json` にした結果、この経路が消えていた。
- 宣言を dashboard に置くのは、定常業務のエンジン（kiro-loop / statemachine-use）が
  agent-project の常駐体・状態リポジトリと無関係に動き、起動・tmux 管理・履歴記録をすべて
  dashboard が担っているため。host.yaml に載せると、常駐体が管理しないものを常駐体の宣言
  ファイルに書くねじれになる。
- 定常業務タブに登録・解除の UI（フォルダ選択 → マーカー検出 → プレビュー → 登録）。
- セレクタには **kind=routine** として合流し、既存の `isProject=false` 分岐（定常業務タブのみ）
  へ流す。project root と同じパスは **project 側を正**として畳む（routine で上書きすると
  backlog / charter / needs / 検収が画面から消えるため）。
- **agent-project のプロジェクト一覧は従来どおり `engine/status.json` のみ**（W2-4 維持）。

agent-flow のテスト環境にも host.yaml の隔離漏れがあったので塞いだ（agent-project では S1 で
修正済みの同種の穴）。


### agent-project: 状態専用リポジトリの唯一化と設定 2 層の責務分離（S1・**破壊的変更**）

設定の置き場が「成果物側 yaml・状態側 yaml・profile・host.yaml」の 4 か所に散り、状態ルートも
worktree 方式と専用リポジトリ方式の 2 系統が併存していた。宣言の場所と実行の場所が食い違う
ことが、移行が効かない・設定が効かない・ノード固有パスが全 PC へ配られる、の共通原因だった
（設計: `docs/plans/2026-07-26-s1-config-two-layer-detailed-design.md`）。

- **状態ルートは常に状態専用リポジトリの clone**。worktree 方式（`state_worktree_dir` /
  `state_branch` / `state_commit` / `state_push` / `state_backup_branch`）を廃止し、これらの
  キーを検出したら移行手順を示して**起動を止める**。黙って無視すると「バックアップされている
  つもりの未バックアップ状態」が続くため。
- **暗黙フォールバックの廃止**。宣言した `state_repo` と root の `origin` が食い違う・clone に
  失敗する構成は起動を止める。旧実装はここで黙って worktree 方式へ倒れ、移行が効いていない
  ことに誰も気付けないまま状態が旧構成へ書かれ続けていた。
- **成果物リポジトリを状態ルートにする事故を起動時に検出**。他リポジトリの内側・状態マーカーの
  無い git リポジトリを root にすると停止し、移行前の `state_repo:` 入り yaml が残っていれば
  その URL を案内に含める。
- **設定は 2 ファイル**: `~/.agents/agent-project.host.yaml`（ノード固有）と状態リポジトリ直下の
  `agent-project.yaml`（プロジェクト共有）。キーの帰属は起動時に検査し、違反は移行先を示して
  止める。両方に書けるのは `agent_cli` / `model` / `act_timeout` / `verify_timeout` /
  `location` / `concurrency` / `agent_timeout` / `actor` / `notify_cmd` / `ltm_home` /
  `flow_config` / `verify_cwd` の 12 キーだけで、優先順位は
  CLI > `projects[].overrides` > `defaults` > プロジェクト yaml > 既定。
- **profile（`~/.agents/agent-project/profiles/`）を廃止**。`root` / `node` / `availability` は
  host.yaml へ吸収した（`--profile` は移行先を示して停止する）。
- **設定ファイルの探索チェーンを廃止**（`cwd → ./.agents → ./.agent → ~/.agents`）。状態ルート
  直下のみを読む。旧探索先にファイルが残っていれば名指しで警告する（移行時に成果物側の古い
  yaml が黙って優先される事故を防ぐ）。
- **`update_*` と `board_workdir` をノード側へ移設**。ツールの自動更新はノードのインストール
  管理で、共有設定に置くと更新の停止・更新元の差し替えが全 PC へ一斉に飛ぶ。
- **状態のコミッタを `DirectStateGit` ただ 1 つに統合**（`commit_state` / `backup_state` /
  本体側ミラーの取り込みを削除）。ローカルのコミットは毎同期で行い、`state_git_interval` が
  律速するのは fetch/push だけ。
- **`state_top` / `source_root` を削除**。成果物リポジトリの解決は
  host.yaml `repos[].local` → ローカルパス → 共有 bare ミラーの順に置き換えた（S3 リゾルバの入口）。
- 常駐体は子へ `--project <名前>` だけを渡すようになった（宣言の解釈を親子で二重化しない）。
- 移行手順: `docs/guides/state-repo-migration.md`（廃止キーと移行先の対応表を追加）。

### docs: agent-dashboard の設計書を 1 本へ統合し、実装と再照合

`docs/designs/agent-dashboard-*.md` の 3 本（制御面分離 77 行 ＋ kiro-loop 端末ビュー 125 行 ＋
agent-project 連携の改善案 188 行）を **`docs/designs/agent-dashboard-design.md` の 1 本へ統合**し、
実装（`tools/agent-dashboard/`）と突き合わせて書き直した。agent-amigos の設計書統合と同じ流儀。

- **構成を抽象から具体への段階的開示に組み替えた**: TL;DR → 背景と目標/非目標 →
  主要な設計判断（ADR 5 件・却下案つき）→ 全体像と合成契約 → 制御面ごとの責務 →
  人のアクションと不変条件 → 気づく/下ごしらえする → 実装状況、＋付録（関連文書）。
- **実装と食い違っていた記述を訂正**: 旧「制御面分離」は feature を 2 つ（agent-project /
  kiro-loop）としていたが実際は 7 つ。renderer も「単一スクリプトのまま」ではなく
  core → sections → features → bootstrap の読み込み順契約へ分割済み。旧「改善案」の
  現状把握にあった「state_git 経由の pull/push」は撤去済みで、いまは常駐体が唯一の書き手。
- **改善案の実装状況を実測で洗い直した**: 通知・SLA バッジ・plan 批評・検収の変更理由説明・
  フォローアップ案・**投入時の acceptance リンティング**は実装済み（旧文書は最後の 1 つを
  未実装として「次アクション候補」に挙げたままだった）。未実装だけを §8 の表に残した。
- **README の陳腐化を修正**（`tools/agent-dashboard/README.md`）:
  - 「リモートで稼働する agent-project を見る（git 経由・一次経路）」節が、撤去済みの
    viewer 側 pull / push（⇣ ボタン・自動 pull 間隔・操作を都度コミットしてプッシュ・
    多重コミッタ対策）を現行手順として説明していた。実際の経路（常駐体が唯一の書き手）へ書き換え。
  - 「ワークスペースとプロジェクトルート」節が「登録するのはワークスペース」と書きつつ、
    同じ節の末尾で「画面からの登録・登録解除は無い」と自己矛盾していた。
  - 「セットアップ」がプロジェクトルートの登録手順を案内していた（登録機能は無い）。
    実際に設定する 4 項目へ差し替え、**未文書だった「この PC の役割」（engineer / viewer）**を追記。
  - 「制限事項」と操作表の「稼働していなければ CLI にフォールバック」（削除済み経路）を訂正。
  - 「実装メモ」が互換シム（`src/main/*.js`）のパスで実装を説明していたので実体パスへ。
- **feature README を 3 つ新設**（`orchestration` / `delegation` / `participation`）。
  「制御面をそのディレクトリに閉じられることが README で追える」という受け入れ条件を
  4/7 の feature しか満たしていなかった。amigos feature README の壊れた相対リンクも修正。
- `docs/designs/README.md` の索引を 24 件へ更新。

### agent-dashboard: 護りの検査範囲を全制御面へ広げ、配布物の取りこぼしを塞ぐ

設計書の照合で見つかった構造の穴 2 件を直した。どちらも**壊れても気づけない**性質のもの。

- **`no-git-writes.test.js` の検査範囲が 3 層だけだった**（`base/main`・`features/agent-project`・
  `renderer`）。制御面が 2 つから 7 つへ増えた結果、後から足した `amigos` / `delegation` /
  `orchestration` / `participation` には護りが掛かっていなかった。範囲を `src/` 全体へ広げ、
  除外は `cowork`（人の成果物リポジトリでブランチを切って push する機能）1 つだけにした。
  あわせて**範囲そのものを検査するテスト**を追加 — 新しい feature は自動でこの護りの下に入り、
  外すには除外リストを触るしかない。護りの中身より先に「掛かっている範囲」が縮むほうが
  起きやすく、しかもテストは緑のままなので気づけない。
- **配布物に `diff2html` が入る保証が無かった**。`index.html` は `../../node_modules/diff2html/…` を
  相対参照する一方、`package.json` の `build.files` は `src/**/*` と `package.json` しか
  列挙していなかった。electron-builder が本番依存を暗黙に含めるかどうかに配布物を賭けず、
  `node_modules/diff2html/bundles/**` を明示。開発起動では node_modules がそこに在るため
  気づけず、漏れていれば**パッケージ版だけ差分ビューが白紙**になる壊れ方だった。
  `test/packaging-assets.test.js` を新設し、`index.html` の相対参照と `build.files` の対応・
  参照先の実在・glob 判定そのものの健全性を検査する（依存未インストール環境でも走る）。
- テストは 60 → 61 ファイル、`npm test` 全緑。

### agent-amigos: 設計書との照合で見つかった 4 件を修正（設定の読み落とし・沈黙する stub・staffing fail・deadline）

設計書の統合（下記）で洗い出した実装漏れを直した。いずれも**沈黙して壊れる**性質のもの。

- **設定ファイルの読み落とし（最も実害が大きい）**: `agent_cli` / `tags` / `roles` /
  `interval` / `resume_hours` / `manual_claim` / `board` を読むのは `participate` だけで、
  `join` / `drive` / `run` は CLI 引数しか見ていなかった。設定に `agent_cli: claude` と
  書いたノードが黙って `stub` で走り、`tags` が空になるので `requires.tags` 付きロールへ
  応募もできなかった。**解決を `cli._resolve_ctx` へ一本化**し、全サブコマンドが同じ
  CLI > 環境変数 > 設定 > 既定 の順を通るようにした。`join` / `drive` に
  `--manual-claim` / `--no-manual-claim`、`join` に `--board`、`drive` に `--tags` /
  `--roles` を追加。`join` も commands/ を取り込むようになった。
  - 応募ロールの絞り込み `--roles` は dest を `role_filter` に分けた。`post --roles` は
    役割ミッション表の**ファイルパス**で、dest を共有すると公示のたびに
    「roles.yaml という名前のロールだけに応募する」絞り込みが生えていた。
  - `drive` は設定に `board` があっても板には触らない（R9: ローカルミッション）。
- **決まらない agent CLI が黙って stub へ落ちていた**: `stub` は LLM なしのダミー応答なので、
  ダミー成果物がそのまま統合・納品まで進み得た。解決できない場合は
  `[agent-error:env]` を投げ、既存の環境要因トリアージに乗せて **paused ＋ owner へ通知**
  にした（ミッションは殺さない）。stub は明示指定のときだけ使う。
  あわせて 3 か所に写経されていた paused 遷移を `AmigoRunner._pause` へ集約し、
  **遷移時だけ通知**（環境が直るまで owner の inbox を埋めない）に揃えた。
- **`staffing_policy: fail` が `wait` と同じ挙動だった**: 値は受け付けるのに誰も見ておらず、
  open のまま滞留していた。`derive_phase` が「`staffing_timeout` 超過かつ必須ロール未充足」を
  **ファイルから導出**して failed を返すようにした（新しい終端ファイルも書き手も増やさない）。
  効くのは**まだ誰も手番を取っていないミッションだけ**——走り出した後にノードが落ちて空いた席は
  再募集の領分で、区別しないと夜中の 1 台のクラッシュが進行中のミッションを巻き添えにする。
  `normalize_mission` で値の検証も追加した（`self_staff` のようなタイポが黙って通っていた）。
- **`mission.deadline` の超過が通知されなかった**: 正規化して保存するだけで誰も見ていなかった。
  オーナー巡回が `inbox/owner` へ **1 度だけ**通知する（`ownerops.owner_notices`）。自動 fail は
  しない——予算追加・収束条件の見直し・cancel のどれを選ぶかは人の判断に残す。
  `staffing_policy: fail` での終端理由も同じ経路で届く。
- **away 中も `question_timeout` が進んでいた**: 宛先ノードが夜に落ちているだけで質問が
  期限切れになり、翌朝の owner の inbox が裁定要求で埋まっていた。宛先が away（grace 内）の
  間は**時計を止め**、代わりに送信側へ不在を 1 度だけ知らせる。`open_questions` に宛先を
  記録するようにした（旧形式の int も読める）。
- 時刻パースの写経（`calendar.timegm(time.strptime(...))`）を `util.iso_to_epoch` へ寄せた。
- テストを 17 件追加（158 → 176）: 設定解決の全項目、CLI 未解決の paused、ロール側 CLI だけで
  足りること、通知が 1 度だけであること、staffing fail の終端と進行中ミッションの非巻き添え、
  deadline 通知、away 中のエスカレーション抑止と復帰後の再開。

### docs: agent-amigos の設計書を 1 本へ統合し、実装と再照合

`agent-amigos-design.md` と `agent-amigos-teambuilder-patterns.md` の 2 本を
**`docs/designs/agent-amigos-design.md` の 1 本へ統合**し、実装（`tools/agent-amigos/`）と
突き合わせて書き直した。

- **構成を抽象から具体への段階的開示に組み替えた**: TL;DR → 背景と目標/非目標 →
  主要な設計判断（ADR 5 件・却下案つき）→ 全体像 → 協働プロトコル → 予算 →
  チーム設計の自動化 → 運用 → 実装状況、＋付録（ロールミッション表 / CLI / 旧 § 番号の対応）。
  文字数は 2 本合計 75.8k → 54.3k。
- **実装と食い違っていた記述を訂正**: hub 中継サーバ（旧 §5.2・P2）と常駐 `serve`（旧 §11.1）は
  撤去済みなのに実装済みと書かれていた。`GlobalSemaphore（~/.kiro/slots/）`は turnmark
  （`~/.agents/amigos/turns/`）へ、`content_file` は `content` へ、`mission.yaml` /
  `roles/*.yaml` は正規化 JSON へ。未記載だった `drive` / `participate` / `deliveries` /
  `restaff` / `budget node`、agent-control 連携、agent-board への入札参加、`repos` 能力宣言、
  `done_when: consensus`、席・討論・コンダクタのプリミティブを反映。
- **既知の欠落を §9 に明記**: `staffing_policy: fail` 未実装、`mission.deadline` の超過通知
  未実装、away 中の `question_timeout` 抑止 未実装、可用性ウィンドウ宣言 未実装、
  設定ファイルの `agent_cli` / `tags` / `roles` / `manual_claim` / `board` を読むのが
  `participate` だけという読み落とし。
- **旧 § 番号を参照していた箇所を新番号へ追随**（`agent_amigos/` 各モジュール・テスト・
  `schemas/mission.schema.json` / `schemas/README.md`・dashboard の amigos feature・
  team-builder スキル）。対応表は設計書 付録 C。
- **設定ファイル例・README を見直し**: `agent-amigos.yaml.example` の「サブコマンド省略 =
  serve」を削除、`roles.yaml.example` に `done_when: consensus` / `review_rounds` /
  `consensus_*` を追加し未実装項目を注記。`tools/agent-amigos/README.md` の
  「現実装では seats>1・投票・同期ラウンド・動的編成が無い」という自己矛盾した記述を訂正
  （いずれも実装済み。medium は 25 → 29 種）。`schemas/mission.schema.json` に
  `requires.repos`（実装済みだが未文書化）を追加。
- `docs/designs/README.md` の索引を 26 件へ更新（未掲載だった agent-dashboard の設計 2 件を追加）。
- テストは 158 件緑のまま（コメントのみの変更）。

### agentcore: P0 完了確認・R9 の常設テスト化・残存重複の棚卸し

P0 が完了したかの確認と、設計 §5 の事前検証（V1〜V4）を実施した。

- **R9 を常設の非退行テストとして固定**（実装計画 §0-4）: `agent-flow run` が常駐体なし・
  `--git` 未指定・ネットワークなしで完結することを明示的に名前つきテストで固定した
  （開発木・zipapp 双方で確認）。amigos 側（`agent-amigos drive`）は P1（W1-3）の
  新設コマンドなので、この時点では対象外。
- **P0 完了条件「`_recover`/claim 系の実装が agentcore 以外に grep で見つからない」を
  再監査**: 未達であることを確認・列挙した。既知の残存（`agent_flow/stategit.py`・
  `DirectStateGit`）に加え、**今回新たに発見**: `agent_amigos/gitbus.py`
  （amigos のミッションバス自身の git+ 実装。板の `BoardMirror` とは別物・
  ミッション単位ブランチ分離・自身のヘッダに「P1」と明記済み）。
  `agent_flow/gitcache.py`・`agent_project/gitcache.py`・`workspace.py`
  （workspace/成果物クローンのキャッシュ）は設計が言う「5 実装」とは別カテゴリと判断し
  対象外のまま。
- **V2（agentcore の import 経路）を最終マージ後の状態で再検証**: 3 ツールの zipapp を
  実際に `install.sh` でビルドし、`agentcore/` の同梱・`agent-flow run` のローカル/
  `--git` 両モードでの実行を確認。
- **V1・V3・V4（WSL/Windows 起動系の実挙動）は本セッションの Linux サンドボックスでは
  検証不能**——実機（Windows + WSL）が必要。P1 着手前に別途実施が必要。
- 全テスト緑を再確認: agentcore 40 / agent-flow 530 / agent-amigos 145 /
  agent-project 918 / agent-dashboard 634 件。

### agentcore: P0 のレビュー指摘を修正 — 語彙統一の取りこぼし（dashboard）・claim 心拍の退行・転送の空 push

P0（W0-6〜W0-10）のレビューで見つかった 6 件を修正した。いずれも P0 の変更が入り口で、
放置すると実運用で表面化する。

- **語彙統一（W0-9）が agent-dashboard に届いていなかった（機能不全）**: 本体側は
  `cancelled` を書くようになったのに、dashboard は `canceled` 決め打ちのままだった。結果、
  (a) 中止した run が終端と認識されず「実行中／応答なし」に誤分類され、削除・再投入の可否も
  ずれる、(b) dashboard の「中止」は `meta.status = 'canceled'` を書くため、本体の終端判定に
  引っかからず **人の中止操作が run を止められない**。`flow.js` / `flow-adapter.js` /
  `participation/model.js` / renderer の 4 系統を `cancelled` へ統一し、`flow-adapter.js` が
  持っていた終端集合の複製は `flow.js` の 1 定義（`TERMINAL` / `isCancelled`）参照に置き換えた。
- **読み取り側だけ旧綴りを受け入れる互換を追加**: 語彙統一は静止点で一斉に切り替えるが、
  **バス上に既にある過去の run の meta.json は書き換わらない**。旧綴りを非終端と読むと、
  改称前に人が cancel した run が `active_runs` に戻り、孤児回収で failed 化されて蘇る。
  `agentcore.vocab.TERMINAL_READ` / `is_terminal_read()` を追加し、flow と dashboard の
  **読み取り**だけがこれを使う（**書き込みは正典 `cancelled` のみ**・翻訳マップは持たない）。
- **amigos のロール心拍が消えた claim を書き戻していた（退行）**: `assign.renew_lease` の
  移植先 `protocol.renew_lease` は「無ければ新規作成」する仕様だったため、剪定・取り下げ・
  オーナーの再編で claim が消えたあとも心拍が書き戻し、誰も動いていないロールを占有し続ける
  zombie 勝者を作りうる（移植前は自分の claim が無ければ no-op だった）。
  `protocol.renew_lease(..., create_if_missing=False)` を追加し、心拍からはこれで呼ぶ。
  板の入札延長（flow / amigos）は従来どおり新規作成する側の既定を使う。
- **`protocol.winner()` が壊れた claim 1 件で止まっていた（退行）**: 移植前の amigos
  `live_claims` は `lease_until` / `ts` を数値化して読めないものを飛ばしていたが、共通実装は
  素の比較だったため、壊れた 1 ファイルで `TypeError` になり **そのロール/委譲が誰にも
  取れなくなる**。数値として読めない claim を無視するようにした。
- **転送が「押し出すものが無くても push する」ようになっていた**: `BoardRepo` / `BoardMirror`
  は移植前に `status --porcelain` が空なら push を省いていたが、`GitTransport.sync_push` には
  その抑止が無く、板を巡回するたびに空 push がリモートへ飛ぶ。commit 済み未 push まで含めて
  判定する `_ahead()` で抑止する（前回 push が落ちて commit だけ残った場合は従来どおり押す）。
- **claim ファイル名の正規化がずれていた**: `protocol` は `safe_name(node_id)` でファイルを
  書くのに、amigos の `MissionPaths.assignment()` は生の `node_id` でパスを組んでいた。
  node_id に記号が混じる環境でだけ「書いたのに読めない」が起きるため、読み手側も同じ正規化を
  通す。あわせて claim 実装の残骸（flow の `_unique_ts` / `_claim_lock_path`。同じ claim_dir に
  対して 2 つのロック名前空間が並立する温床）を削除した。
- 回帰テストを追加（agentcore 5 / agent-flow 1 / agent-amigos 2 / dashboard 2）。全テスト緑:
  agentcore 40 / agent-flow 529 / agent-amigos 145 / agent-project 918 / dashboard 全件。

### agentcore: P0 完了 — GitBus/StateGit の transport 委譲・flow/amigos の claim 統一・語彙統一・契約掃除

[常駐一本化 実装計画](docs/plans/2026-07-24-single-resident-controller-implementation-plan.md) の
P0（W0-6〜W0-10）を完了し、直前のコミットで着手した P0 の残りを仕上げた。

- **W0-6 — `agent_flow/gitbus.py` の `GitBus` を transport 委譲へ**: 転送の実装は
  `agentcore.transport.GitTransport` の 1 実装のみに。白箱テスト（`_is_corrupt_error` の
  クラス参照・`_clone_with_retry` の monkey-patch・`_git`/`_probe_integrity` の直接呼び出し）
  と互換な薄いラッパーとして GitBus を残した。移植中に `GitTransport._rebuild_clone` の
  実バグ（存在しないメソッド名を呼んでいた——`sync_pull`/`sync_push` 経路の破損リカバリが
  必ず `AttributeError` で落ちる潜在バグ）を発見・修正し、再現テストを追加。
- **W0-8（残り）— flow のタスク claim・amigos のロール claim を `agentcore.protocol` へ**:
  `agent_flow/bus.py` の `_winner_in`/`_write_claim_in`/`_try_claim_in`/`extend_claim`・
  `agent_amigos/assign.py` の `claim_role`/`apply_role`/`live_claims`/`winner`/`renew_lease`、
  および flow 自身の板参加（`agent_flow/board.py` の `_write_or_renew_bid`）を移植。
  claim 3 実装 → 1 実装（設計 R1 の達成条件）。
- **W0-9 — 完了語彙の統一（`canceled` 米式 → `cancelled` 英式・静止点で全ツール一斉）**:
  agent-flow の内部 `TERMINAL` 定数を `agentcore.vocab.TERMINAL` の参照に置換し、run
  status・cancel マーカー・ログメッセージの綴りを統一。`agent_flow/board.py` の
  `_FLOW_TO_BOARD_STATUS` 翻訳マップを削除（板の語彙と一致したため翻訳不要に）。
  `agent_project/loop.py` の `endswith(("canceled","cancelled"))` 二重判定を単一判定へ
  縮約。Python の識別子（`mark_canceled`/`is_canceled_requested`/`_orch_check_canceled`）は
  内部実装詳細として据え置き、対外契約となる文字列値・スキーマ・ドキュメントのみ改称。
- **W0-10 — 契約の掃除**: `schemas/board.schema.json` から未実装の speculation
  （`result_report`/`results/<who>.json`/`resolve`）を削除し、`agent-board/README.md` の
  レイアウト説明も追従（実装時に additive で復活）。stale lock 閾値の 30s/300s 統一は
  StateGit の直接（direct）モード統一が前提の P1 マターと判断し見送り（理由をコード
  コメントに明記）。
- **W0-7 — `agent_project/stategit.py` の `StateGit`（管理クローンモード）を transport 委譲へ**:
  低レベルの git 実行・ロック回復・クローン/push リトライ層を `agentcore.transport` へ委譲し、
  CAS export・manifest 3-way・パス所有権裁定（`_resolve_rebase`/`_three_way`/
  `_take_local_on_conflict` 等）はこのクラスのポリシーとして残した（挙動不変。直接
  （direct）モードとの統一は P1）。副次効果として fsck 破損検知・durable-write・clone
  指数バックオフを新たに獲得。`DirectStateGit`（direct モード・実運用の既定経路）は
  アーキテクチャが大きく異なり（クローンを持たず detached worktree + CAS で完結）
  transport との重複が薄いため今回は対象外——フォローアップとして明記。
- 全テスト緑を確認: agentcore 35 / agent-flow 528 / agent-amigos 143 / agent-project 918 件。
- **未着手（フォローアップ）**: `DirectStateGit` の transport 委譲・`agent_flow/stategit.py`
  （flow 独自の状態鏡写し。今回の移植中に発見した 6 個目の転送重複実装）・
  `agent_flow/gitcache.py` / `workspace.py`（共有 git キャッシュ + worktree の別実装）の
  統合。P1〜P3（常駐体本体・dashboard 縮退・パッケージ統合・実機 canary）は本計画どおり。

### agentcore: 共通 git 転送層・claim/lease プロトコルを新設し BoardRepo / BoardMirror を移植（常駐一本化 P0 着手）

[常駐一本化 実装計画](docs/plans/2026-07-24-single-resident-controller-implementation-plan.md) の
P0（W0-1〜W0-5）に着手。転送・claim の重複実装を解消する共通ライブラリ `agentcore/`（3 ツールが
`import agentcore` する通常パッケージ・独立配布はしない）を新設し、`agent-project` の
`BoardRepo` と `agent-amigos` の `BoardMirror` をそちらへ移植した。

- **`agentcore.transport.GitTransport`**: `agent_flow/gitbus.py` の `GitBus` に実証されていた
  護り（stale lock 掃除・中断 rebase の abort・fsck プローブ・破損時の退避→再クローン→復元・
  durable-write 設定・clone/push の指数バックオフリトライ・force push 禁止・間隔律速で
  失敗時はクロックを進めない）を、sparse / フルチェックアウトの両方に使える汎用実装として
  切り出した。bare repo + 故意のロック残骸/中断 rebase/オブジェクト破損を使う新規単体テスト
  12 件。
- **`agentcore.protocol`**: 名前空間付き claim・`(ts, who)` 決定的タイブレーク・lease の
  書込/延長（残り半分で更新）/失効判定を共通化。`agentcore.vocab`（完了語彙
  `done`/`failed`/`cancelled`）・`agentcore.heartbeat`（心拍/鮮度）を追加。単体テスト 20 件。
- **`agent_project/board.py` の `BoardRepo`・`agent_amigos/board.py` の `BoardMirror`** を
  `GitTransport` 経由へ置換（board の入札・bid 延長ロジックも `agentcore.protocol` へ移植）。
  外部 API・既存テスト（`TestBoardAutoWiring` 12 件・`BoardParticipationTests` 等）は無改変で
  緑のまま。副次効果として、板の 2 クローンが GitBus 相当の破損自己回復・durable-write を
  新たに獲得した。既存クローン（マーカー導入前）を「管理外の非空ディレクトリ」として
  拒否しないための後方互換パスと新規テストを追加。amigos に `BoardMirrorGitTests`
  （git+ モードの 2 ノード post/bid 往復・ロック残骸回復）を新設。
- 3 ツールの `install.sh` を拡張し、zipapp へ `agentcore/` を同梱（独立パッケージ化はしない —
  設計 R10）。エントリスクリプト・パッケージ `__init__.py` に import 経路の path shim を追加。
- 全テスト緑を確認: agentcore 33 / agent-flow 528 / agent-amigos 143 / agent-project 918 件。
- **未着手（フォローアップ）**: W0-6（`GitBus` の転送委譲）・W0-7（`StateGit` 下回りの置換）・
  W0-8 の残り（flow タスク claim・amigos ロール claim の `agentcore.protocol` 移植）・
  W0-9（語彙統一 `canceled`→`cancelled` の全ツール一斉改称）・W0-10（契約の掃除）。
  P1〜P3（常駐体本体・dashboard 縮退・パッケージ統合・実機 canary）は本計画どおり後続フェーズ。

### agent-project: 委譲公示板（agent-board）への依頼側自動配線 ＋ 請負側の成果報告を実装

新 location `board`。`agent-project.yaml` に `board:`（板の場所。ローカル dir / `git+<url>`）を
設定すると、`location: auto` は `policy.offload` 一致タスクを（remote より優先して）委譲公示板へ
自動 post するようになる（`decide_location`・`agent_project/flow.py:_act_board`）。既存の
非ブロッキング委譲（`_Pending`/`offloaded` ステータス）と同じ枠組みに乗せてあるため、結果は
`_reap_offloaded` が板の `result.json` を1回ずつポーリングして回収し、done/failed/canceled の
既存 settle 経路（canceled → retries を進めて再投函 等）へそのまま合流する。

- **請負側の成果報告を新規実装**（自動配線の前提）: 従来 board 参加デーモン（agent-flow /
  agent-amigos）は「入札→自分のエンジンへ引き渡し」までで、完了を板へ書き戻す処理が無かった。
  依頼側の自動回収を機能させるため、`agent_flow/board.py` / `agent_amigos/board.py` に
  `report_board_results` を追加: 落札ノードが自分の実行（flow run / amigos mission）の終端
  （done/failed/cancelled）を検知し、板の `result.json` を直接書く（speculation 無し・単一落札の
  簡略形。冪等・二重報告しない）。`board.schema.json` の `result` に `status` を明示追加。
- **`agent_project/board.py` を git 対応の `BoardRepo` へ刷新**: 従来の手動 `board-offload` は
  ローカル dir の板にしか投函できなかった（`git+<url>` 未対応）。プロセス間 flock で直列化した
  git pull/push（間隔律速・rebase リトライ・force push 禁止・`main` ブランチ既定へのフォールバック）
  を実装し、`git+` 板にも対応。`task_to_delegation` は `build_request` の全文を `goal` に使うよう
  修正（従来は `task.title`/`desc` の簡易版で、charter/rules/decisions/run ブリーフ等が
  board 経由だと欠落していた — local run / daemon submit と同じ文脈を維持する）。
- `_submit_bound`（並列 submit 判定）・`batch.py` へ `board` を追加し、複数タスクの board 公示も
  並行化できるようにした。
- テスト: agent-project `TestBoardAutoWiring` 12 件（decide_location の優先順位・post→pending→
  result 到着での確定・reap の done/failed/canceled 分岐・`git+` 板の実 push/pull 往復）、
  agent-flow / agent-amigos に `report_board_results` のテストを追加。

### agent-board: 委譲公示板（依頼の公示・入札・成果一本化の分散バックエンド）を新設

契約 `schemas/board.schema.json` と、専用リポジトリ（＝板）の規約 `tools/agent-board/README.md`。
**agent-board は実行プロセスを持たず、「リポジトリ＋契約」だけ**。エージェント処理の依頼を公示し、
登録ノードの入札（先勝ち claim）で引き受け先を決める、エンジン非依存の一段下の層。**入札・引き渡しの
処理は既存デーモン（agent-flow / agent-amigos）が担う**（新しいデーモン・サーバは増やさない）。
真実は板のファイル・中央（forge）は転送のみ。結合はデータ契約のみ。正典設計:
`docs/plans/2026-07-23-delegation-board-distributed-bidding-design.md`。

- **板のレイアウト契約**: `nodes/<id>`（能力宣言）・`delegations/<id>/{post,bids,award,status,
  results,result,cancelled}`。書き込み所有権をパス単位で分割し git でもコンフリクトしない。
- **先勝ち入札 ＋ 決定的一本化**: agent-flow / agent-amigos と同一仕様の名前空間付き claim ＋
  `(ts, who)` タイブレーク（同じ仕様・別実装）。2 ノードが同時入札しても落札は決定的に 1 ノード。
  成果は `result.json` 1 つに一本化。

### schemas: `board.schema.json` を新設し、`delegation.schema.json` に additive 拡張

`delegation.schema.json` の post 封筒へ `requires`（入札資格 tags/agent_cli/repos）と
`speculation`（投機同時実行）を additive で追加（委譲公示板の参加者だけが解釈・直接経路は無視）。
`board.schema.json` は板のファイルレイアウト（node/bid/award/status/result_report/result/
cancelled）を文書化。`schemas/README.md` に board 行を追加。

### agent-flow: 委譲公示板への参加（請負・入札）と来歴の引き回し

設定 `board:`（CLI `--board`）を与えると、daemon が板を巡回して `workload=flow` の公示に
`board_repos` / `board_tags` で照合して入札（flow の claim をそのまま流用）、勝てば自分の
`inbox/<id>.json` へ取り込む（＝既存の inbox→orchestrator フローがそのまま拾う）。`agent_flow/board.py`。
取り込んだ run の `meta.json` には来歴 `delegation:{id, board}` が残る（`submit_request` /
`submit --delegation` / `note_delegation`・additive）。

### agent-amigos: 委譲公示板への参加（請負・入札）と `repos` 能力宣言

設定 `board:`（CLI `--board`）を与えると、daemon が板を巡回して `workload=amigos` の公示に
repos/tags 照合で入札し、勝てば**オーナーとしてミッションを公示**する（`agent_amigos/board.py`）。
あわせて `agent-amigos.yaml` の `repos:`（`repos.schema.json` 形）を能力宣言に追加し、`matches_role`
がロールの `requires.repos` とノードの担当リポジトリを名前 / URL（`.git`・末尾スラッシュの揺れを
吸収）で突き合わせる。成果物リポジトリに応じて応募ノードを絞れる。

### agent-project: `board-offload` — バックログのタスクを委譲公示板へ委譲

`agent-project board-offload <task-id> --board <repo>`。ルーティング（`resolve_workspace`）で
workspace を確定したうえで、タスクを `delegation.schema.json` の post 封筒へ変換して板へ投函する
（workspace の repo 名を `requires.repos` に載せ、担当ノードだけが入札する）。

### agent-dashboard: 委譲タブに公示板（board）ターゲットを追加

`src/features/delegation/main/board-adapter.js` を新設し、`target: 'board'` の post/award/cancel を
板リポジトリへファイル投函、板のファイルだけから正規化ビュー（入札の勝者判定・フェーズ・成果）を
導出。`delegation.boardRepos` を横断一覧に含める。契約コアは `requires` / `speculation` を保持。
テスト `test/delegation-board.test.js`。

### agent-dashboard: 検収の成果物 diff を最新化（fetch + origin/<branch>）し、done run が無くても成果を確認できるように

`src/base/main/git.js`（`diffRange`）・`src/base/main/ipc.js`・`src/renderer/sections/needs.js`。

- **diff が古いまま**の不具合を修正。コメント付きで再実行して push し直した run の検収で、差分が
  古い（前回の）内容のままだった。`diffRange` に `fetch` / `branch` を追加し、**diff を取る前に
  `git fetch origin <branch>`** で remote-tracking を更新し、比較先（tip）は **`origin/<branch>` を
  最優先**で使うようにした（比較元 base も fetch 後は `origin/<base>` を優先）。fetch はベスト
  エフォート（オフラインでも既存 ref で続行）。検収を開いた最初の解決で 1 回 fetch する。
- **done run が無くても、delivery に中身があれば「成果を確認」ボタンを表示**するようにした
  （`hasDeliveryContent`）。コメント付き再実行などで delivery だけが記録されている票でも成果物を
  確認できる。あわせて、作業ブランチだけで ref 未解決の検収物も `origin/<branch>` で差分を出せる
  ようにした（従来は「ref 未解決」で差分を出さなかった）。

### agent-dashboard: 状態フォルダ（<project>-agent-state）が無ければ開いたときに自動作成する

`src/features/agent-project/main/project.js`。プロジェクトを開いたとき、状態 worktree
（`<repo>-agent-state`）が無く、かつ `agent-state` ブランチが存在すれば、`git worktree add` で
自動作成するようにした（agent-project の `_ensure_state_worktree` と同型：`--no-checkout` →
状態ディレクトリだけ sparse checkout → checkout）。

- ブランチはローカル `refs/heads/agent-state` か remote-tracking `refs/remotes/origin/agent-state` を
  使う。どちらも無ければ作らない（クローン元が無い＝本体未セットアップ）。**fetch はしない**
  （readProject から同期的に呼ぶため UI をネットワーク待ちで固まらせない。通常の clone は
  `origin/agent-state` の remote-tracking ref を持つのでこれで足りる）。
- 既存・非 git・ブランチ未存在はすべて no-op。作成失敗・再試行はセッション内で 1 回に抑制。

### agent-dashboard / kiro-loop / agent-loop: 実行状況ダイアログの送信が `[[: not found` / `python: No such file or directory` で失敗する不具合を修正

- **agent-dashboard**: `src/features/kiro-loop/main/exec.js` の `shInWsl` を `sh -lc`（dash）から
  `bash -lc` へ変更。dash だと利用者の profile / `~/.bashrc` の bash 構文 `[[ … ]]` が
  `sh: N: [[: not found` になり、そこで止まって venv 有効化も走らず、kiro-loop の
  `#!/usr/bin/env python` が解決できず `python: No such file or directory` になっていた。
- **kiro-loop / agent-loop**: スクリプトの shebang を `#!/usr/bin/env python` → `#!/usr/bin/env python3`
  へ修正（`kiro-loop.py` / `kiro-send.py` / `agent-send.py`）。インストーラの python 検出順も
  `python python3` → `python3 python` にし、`python` 未存在（python3 のみ）の環境でも動くようにした。

### agent-dashboard: ステートマシン実行時、必要な入力パラメータを人へ質問させる

`src/features/cowork/main/cowork.js`。statemachine-use で作ったステートマシンに入力パラメータが要る
場合、勝手に仮の値で進めず、人へ質問してから実行させる。従来は汎用的な補助文だけだったが、
定義（`.statemachine/<name>/workflow.yaml`）を読んで**具体的に必要な入力を洗い出す**ようにした:

- action/condition が `{{input}}` を参照していて、実行時に入力が渡されていない → 実行対象の入力を要求
- `context` の初期値が空（`""` / 空欄）のキー → その `context.<key>` を要求

必要な入力があるときは、起動プロンプトに項目名を列挙し「値が不明なものを箇条書きで質問し、回答を
得てから実行して」と明示する。定義を読めない/追加入力が不要なときは従来の汎用補助へフォールバック。

### agent-dashboard: kiro-cli の入力プレースホルダを起動検出パターンに追加

`src/features/cowork/main/loopProvider.js`。kiro-cli は入力欄に `>` ではなくゴーストテキスト
「Ask a question or describe a task」を表示するため、「行全体が素のプロンプトだけ」という判定に
一致せず、起動検出が発火せずコマンドが送られなかった。検出正規表現にこのプレースホルダ
（`ask a question` / `describe a task`・大小無視）を追加する。入力受付中にだけ出るため、準備完了の
合図として適切。

### agent-dashboard: CLIチャットの「エージェントに送る」がまったく入力されない不具合を修正（send-keys の形）

`src/features/cowork/main/loopProvider.js`。送信を `tmux send-keys -t <pane> -l -- <text>` ＋別 Enter で
組み立てていたが、この形（特に `-l --` の組み合わせ／テキストと Enter の分割）では文字が届かない
環境があった。kiro-cli 用に実績のある kiro-loop の `send_prompt_to_session` と**完全に同じ形**
（`tmux send-keys -t <pane> -- <1行テキスト> Enter` の 1 コール・`-l` なし）へ揃える。

### agent-dashboard: CLIチャットの「エージェントに送る」が送られなくなる不具合を修正（プロンプト検出）

`src/features/cowork/main/loopProvider.js`。プロンプト検出を「画面末尾（非空）3 行」に絞ったところ、
kiro-cli のように**入力欄の下にステータス行/ヒントを出す** CLI では素のプロンプト（`>`）が末尾 3 行から
外れて一致せず、開始コマンドが送られなくなっていた。検出を**画面全体**の走査へ戻す（判定は従来どおり
「行全体が素のプロンプトだけ」で、起動バナー本文には出にくい形）。送信の打鍵化（send-keys）・前面即
アタッチ＋バックグラウンド送信はそのまま。

### agent-dashboard: kiro-cli の CLIチャットで「エージェントに送る」が文字化け・起動待ちで固まる不具合を修正

`src/features/cowork/main/loopProvider.js`。kiro-cli 等スラッシュ補完メニューを持つ CLI 向けに、
kiro-loop（kiro-cli 用の実績ある送信方式）へ揃えた。

- **送信方式を `paste-buffer`（一括ペースト）から `send-keys -l`（打鍵）へ変更**。一括ペーストは
  kiro-cli のスラッシュコマンド補完メニューの非同期描画と競合し、文字が化けていた
  （例: `/caveman` → `/cavem,an`）。kiro-loop と同じく 1 文字ずつ「打鍵」し、確定の Enter を
  分けて送る。複数行は空白で 1 行に畳む（kiro-loop と同じ）。
- **前面はすぐアタッチし、プロンプト検出＋送信はバックグラウンドで行う**ように変更。従来は入力
  プロンプト検出が終わるまで最大 60 秒 “起動を待っています” で固まって見えた（CLI 起動が遅いと
  その間ずっと待ち画面）。今は接続後に CLI 起動の様子が見え、準備でき次第に自動送信する。
- **プロンプト検出を「画面末尾（非空）3 行」に限定**（0.5 秒間隔）。従来は画面全体を見ていたため、
  起動バナー中の `>` を早合点して未起動のまま送信し、文字化けの一因になっていた（kiro-loop の
  `_pane_has_prompt` と同じ判定）。

### agent-dashboard: 検収画面の差分が target ブランチではなくローカル HEAD と比較していた不具合を修正

`src/base/main/git.js`（`diffRange`）と `src/renderer/sections/needs.js`。

- 作業 ref が未解決の検収物で、差分の比較元が **ローカルの現在ブランチ（HEAD）** になっており、
  設定した **target ブランチと比較されていなかった**。working-tree 差分のとき、target ブランチが
  分かるなら、その分岐点（`git merge-base <target> HEAD`）から作業ツリーまでを比較するようにした
  （未コミット分も含め「target に対して何を変えたか」を表示）。target が渡されない/解決できない
  ときだけ従来どおり HEAD との差分へフォールバックする。
- 差分ラベルを「現在の作業ツリー（HEADとの差分）」から「`<target>` との差分（作業ツリー）」へ変更。

### agent-dashboard: CLIチャットの起動が極端に遅い・送信コマンドが文字化けする不具合を修正

`src/features/cowork/main/loopProvider.js`。

- **起動が極端に遅い**（毎回 60 秒待たされる）不具合を修正。エージェント起動後の入力プロンプト
  検出が、素のプロンプト（`>` `❯` `›` `?` だけの行）しか一致せず、**枠で囲う入力欄**を持つ CLI
  （Claude Code の `│ > │` 等）では一致しないため、検出ループが上限の 60 秒まるごと待ってから
  アタッチしていた。検出正規表現に枠付きプロンプト（`│` に続く `>` `❯` `›`）を追加し、素早く
  検出してアタッチするようにした。
- **「エージェントに送る」コマンドが文字化けする**不具合を修正（例: `/caveman` → `/cavem,an`）。
  `tmux paste-buffer` の素のペーストが、スラッシュコマンドの補完メニューを持つ CLI（Claude Code
  等）の非同期メニュー描画と競合して文字を崩していた。ブラケットペースト（`paste-buffer -p`）で
  一括挿入し、ペースト確定を待ってから Enter で送るようにした（`-p` 非対応 CLI では素のペーストに
  フォールバックするため kiro-cli 等はそのまま動く）。

`src/features/cowork/main/loopProvider.js`。

- **「エージェントに送る」（chat モードのセッション開始コマンド）が CLIチャットで効かない不具合を修正**。
  従来は chat モードの開始コマンドを **tmux セッションを新規作成したときだけ**（`__new`）送っていた。
  CLIチャットのウィンドウは離脱（Ctrl+b d）してもセッションが常駐するため、初回オープン以降・および
  「セッションを作った後に開始コマンドを設定した」ケースでは一度も送られなかった。手動オープン
  （業務プロンプト無し）の経路では、既存セッションへ再接続したときも毎回送るように変更した。
  業務プロンプトを送る定常ループの経路は従来どおり新規セッション時だけに限定し、周期送信での
  前準備の二重実行を避ける。
- **process モードのセッション開始コマンドを bash で実行**するよう変更。従来は `sh -c`（dash）で
  走らせていたため、`source .venv/bin/activate` や `[[ … ]]` などの bash 構文が
  `sh: source: not found` 等で失敗していた。

### agent-dashboard: 定常業務タブの常時表示と Windows CLI チャットのログインシェル修正

- **定常業務タブを常に表示**（`src/renderer/renderer.js` の `updateCoworkTabVisibility`）。
  従来は「発見済み or 手動登録の作業が無いプロジェクトではタブごと隠す」挙動だったが、
  作業が未登録でも空状態（`renderCowork` の「このプロジェクトに登録された定常業務はありません」）
  から追加へ導けるよう、定常業務タブ・ペインは常時表示にした。現在のタブが隠れたときの退避先も
  既定タブが無ければ常時表示の定常業務へ倒す。agent-project 系タブの出し分けは従来どおり。
- **Windows から CLI チャットを開くと `sh: N: [[: not found` /
  `No virtual environment found in .venv or ~/.venv` で起動できない不具合を修正**
  （`src/features/cowork/main/loopProvider.js` の `windowStartCommand`）。
  別ウィンドウの WSL 実行を `wsl.exe -e sh -lc …`（sh=dash）で起動していたため、
  利用者の `~/.bashrc` / profile にある bash 構文 `[[ … ]]` が dash で解釈できずエラーになり、
  そこで止まって venv 有効化も走らず、エージェント CLI が起動できないまま長時間待たされていた。
  ログインシェルを **bash**（`wsl.exe -e bash -lc …`）に変更し、bash 構文と venv 自動有効化が
  正しく走るようにした。

### schemas / agent-dashboard: ノード予算 v2（トークン一次）とオーケストレーション契約の正典化

予算の一次単位を実行時間（分）からトークンへ刷新し、稼働中エンジンへの横断操作
（エージェント / モデル変更・縮退・一時停止 / 停止・委譲誘導）を dashboard が書き
エンジンが読む宣言的データ契約に統一する設計を確定した
（`docs/plans/2026-07-19-agent-dashboard-orchestration-token-budget-design.md`）。

- **`schemas/node-budget.schema.json` v2（additive）**: 台帳に実測 `tokens_in` / `tokens_out` /
  `agent_cli` / `model` / `usd` を追記可能に。config に `tokens`（トークン合計上限）・
  `allocation`（weight / min・max クランプ / `on_exhausted: pause|stop|degrade` / soft_ratio）・
  `computed`（管理面が再計算する実効上限）・`rates`（トークン未報告 CLI の推定レート表）を追加。
  台帳には事実のみ書き、推定は読み出し時（配分・較正の知能は管理面、エンジンは従来の単純比較）。
  v1 リーダは分上限だけを執行し続ける互換設計。
- **`schemas/agent-control.schema.json` 新設**: `$AGENT_CONTROL_DIR`（既定 `~/.agent/control/`）の
  `control.json`（望ましい状態）＋ `status/<tool>-<pid>.json`（適用ハートビート）。優先順位は
  control > CLI 引数 > 設定ファイル > 組み込み既定。kiro-loop は予算枯渇時に従来の一時停止でなく
  `on_exhausted: stop` で graceful 停止できるようになる（routine の推奨既定）。
- **4 エンジンへ v2 予算＋agent-control を実装**: agent-flow（`run_agent`）・agent-project
  （`_run_agent_cli`）・agent-amigos（`runner` / `nodebudget` / 新 `control`）・kiro-loop
  （`_run_loop`）の各チョークポイントに、① トークン集計（実測 or `rates` 推定）による超過判定、
  ② control のエージェント / モデル横断上書き（既存の解決関数の先頭に 1 段）、③ soft_ratio 到達時の
  `degraded` 適用、④ lifecycle（run|pause|stop）適用、⑤ `status/` ハートビート書出し、を追加。
  台帳へ `agent_cli` / `model` / 実測トークン（agent-project は `@cost` から）を帰属付きで記帳。
  **kiro-loop は `on_exhausted: stop` / lifecycle=stop で `_request_shutdown()`（自 SIGTERM →
  既存 `_cleanup`）により graceful 停止**し、停止理由を state ディレクトリへ残す。
- **agent-amigos CLI**: `budget node --limit-tokens` を追加し、消費表示にトークンを併記。
  `save_config` は dashboard が書いた v2 キー（allocation / rates 等）を保持する（未知キーを消さない）。
- **agent-dashboard オーケストレーションタブ**: 新制御面 `src/features/orchestration/`（予算ゲージ・
  配分エディタ＋再配分/レート較正・エージェント割当マトリクス・エンジン状態＋lifecycle 操作・
  agent-cli ドロップイン棚卸し）。割当マトリクスはワークロード既定に加え、**用途 / ロール / ノード
  種別（`agents.<key>`）別の上書きを追加・編集・削除**できる（project は用途・flow は役割＋kind を
  候補補完、amigos はロール id 自由入力、routine は非対応を明示）。既存 amigos 予算パネルは互換で存置。
- テスト: 各エンジンに v2 予算（トークン実測 / 推定・computed 上限・soft/degrade）と agent-control
  （上書き解決順・lifecycle・status・kiro-loop の stop 発火）の単体テストを追加。dashboard に
  orchestration（予算集計 / 配分 / 較正 / control の agents.<key> 追加・削除 / ドロップイン）テストを追加。

### agent-project: 計画バージョンの制約継承テストを実装意思に合わせて修正

`charters/<name>.md` の制約 / 前提の継承は「バージョンが `## constraints` を明示すれば置換、
見出しが無ければマスターから継承、空見出しなら継承値を消す」意思で実装されている（`_merge_master_charter`）。
`test_version_inherits_master_charter` の表明だけが旧来の「和集合」前提のまま残って失敗していたため、
実装意思（＝同テストの docstring の記述）に合わせて修正し、対称の 2 ケース
（見出し省略→継承 / 空見出し→クリア）を追加した。

### agent-project / agent-dashboard: 「承認して完了にする」を出し分けで消さない

要対応の画面で承認操作が見当たらず完了できない、という報告が繰り返し出ていた。原因は
**「承認 = 完了」か「承認 = 積み直し」かを両側が別々に推定していた**こと。条件分岐を削り、
意図を明示する形へ変えた。

- **本体（agent-project）**: `approve --complete`（commands ドロップは `{"complete": true}`）を
  追加。完了確定はこのフラグで決まり、**承認理由の文面には依存しない**。以前は理由に
  「検証」「受容」等のキーワードが揃ったときだけ完了し、外れると黙って ready へ積み直して
  同じ工程を再実行、また要対応へ戻る往復になっていた。旧経路（キーワード推定・verify 未定義）は
  後方互換のため残す。
- **画面（agent-dashboard）**: 承認ボタンの出し分けを「**検収物が少しでもあるか**」の 1 条件に
  統一（`needHasDeliverable`）。従来は「blocked かつ task が blocked かつ検証失敗の解析に成功し
  かつ完了 run が見つかった」の AND 連鎖で、どれか 1 つ欠けるとボタンごと消えていた。あわせて
  run 一覧の非同期読み込み前でもタスクの `last_run` で判定し、**読み込みタイミングでボタンが
  出たり消えたりしない**ようにした。差分検収ダイアログ経由に限定していた制約も外し、
  要対応の画面から直接承認できる（成果を見る導線は従来どおり併置）。

### agent-amigos / agent-dashboard: 成果物の納品を push 型（納品棚）にする

- **accept が納品棚へ搬出する**: バスの `deliverable/` は gc 対象なので、collect し忘れが
  成果物の喪失に直結していた。accept 成立時に owner デーモンが
  `<home>/deliveries/<mid>/` へ搬出し、納品書 `delivery.json`（受入日時・受入者・partial・
  消費実行時間・ファイル一覧、正典 `schemas/delivery.schema.json`）と受領一覧
  `<home>/DELIVERY.md` を書く。agent-project の archive + DELIVERY.md と同じ二段構え。
  `collect` は「納品棚とは別の場所へ改めてコピーする」補助へ降格。
- **正本の置き場を種別で分ける**: 文書・調査結果・画像は本体を納品棚へ、コードは
  `workspace.repo` の統合ブランチが正本で参照だけ、10MB 超は搬出せず参照だけ
  （`exported: false`）。納品棚は gc の既定では消さない（`--deliveries-keep-days` 明示時のみ）。
- **CLI**: `deliveries [-v]` を追加。`accept` / `deliveries` / `gc` は `--home` でホームを
  上書きできる（既定は設定ファイルの位置）。
- **dashboard**: 受入待ちミッションの成果物プレビュー（markdown は本文・画像はインライン・
  他はメタ情報）と「この成果を受け取る / 修正を依頼する」、受け取り済み成果物の閲覧
  （「保存先を開く」は既存の `shell:openPath`）を追加。受入操作は accept / reject の
  commands 投函で、**dashboard がバスへ書かない規律は不変**。
- **成果物はミッション単位で見せる**: 納品を独立した一覧として並べると、利用者が考える単位
  （ミッション）と画面の単位がずれる。overview で納品をミッションへ結び付け、詳細ダイアログの
  「受け取った成果物」節で中身（markdown は本文・画像はインライン）まで開けるようにした。
  中身は詳細を開いたときに `amigos:deliveryContents` で 1 件だけ読む（ポーリングで全文を運ばない）。
  読み方は受入プレビューと共通化（`preview.js`）。gc でバスから消えたミッションの納品だけは
  行き場が無くなるので「過去の成果物」節へ回す。
- **dashboard の無言の失敗を 3 つ塞いだ**: (1) 修正依頼が `window.prompt`（Electron 未対応）
  で例外になっていたのを専用ダイアログへ。(2) 納品 0 件のとき節ごと描かず「どこで受け取るのか」が
  画面から消えていたのを、保存先を案内する空状態に。(3) 投函した指示が常駐停止で取り込まれない
  ことに気づけなかったので、未取り込み件数（`pendingCommands`）を画面に出す。
  あわせてプロジェクト未選択時に納品だけがスコープを素通りしていた不具合も修正。

### agent-dashboard: エージェント CLI 界面を共通契約（agent-cli.schema.json）に整合

- **agents/`<name>`.json プラグイン CLI のローダを実装**: agent-cli.schema.json は共有者に
  agent-dashboard を挙げていたが、実装は 6 種ハードコードで未知の CLI 名は黙って kiro に
  フォールバックしていた。本体（agent-project / agent-flow / agent-amigos）と同じ契約・
  探索順（`$KIRO_AGENTS_DIR` → `<プロジェクト>/agents/` → `~/.agent/agents/` →
  `~/.kiro/agents/`）で解決し、`{model}` / `{output_file}` テンプレ・`prompt_via` /
  `prompt_flag` / `model_flag` / `default_model` / `output: file` / `env` / `timeout` /
  `empty_output_is_error` を解釈する（AI 補助・Doctor・構造化 Assist の全経路）。
- **CLI 解決順を ipc の契約どおりに修正**: `resolveAgent` がプロジェクト設定を無視して
  常に Viewer 設定（既定 kiro）を使っていた（`readProjectAgent` は dead code だった）。
  「⚙ Viewer 明示設定 > プロジェクト設定（agent-project.yaml / agent-flow.yaml の
  agent_cli / model）> 既定 kiro」へ。設定画面の CLI 欄は datalist 付き入力にして
  プラグイン名も指定でき、空欄＝未設定（プロジェクト設定へフォールバック）を保つ。
- **agent-loop クローンの端末視聴**: 端末（tmux）発見が `~/.kiro/loop-state/` のみを
  読んでいたため、agent-loop（状態は `~/.agent/loop-state/`・同形式）のデーモンが
  見つからなかった。両ディレクトリを読む。
- kiro-loop との界面（設定ファイル・loop-state 形式・tmux 命名・send のプロンプト名解決・
  入力プロンプト検出正規表現・node-budget 分担）は突き合わせの結果、齟齬なしを確認。

### agent-amigos / agent-dashboard: 入出力データの契約をスキーマへ整合

- **`schemas/amigos-command.schema.json` を新設**: `<home>/.agent/agent-amigos/commands/*.json`
  の指示ドロップ契約（post / claim / assign / accept / reject / cancel / say の各ペイロード・
  owner-only 権限・成功で削除/失敗で .rejected の規約）を明文化。投函側（人・agent-dashboard）と
  取り込み側（`agent_amigos/commands.py`）が同じ契約を参照し、コマンド一覧・必須フィールドの
  一致を両側のテストで担保（Python `CommandSchemaTests` / dashboard `amigos.test.js`）。
- **mission.schema.json にバスの読取契約（`$defs`）を追記**: 外部ビュアーが読む正規化出力
  `mission.json`（`id`/`owner_node`/`posted_at`）・`deliverable/MANIFEST.json`・`final.json`・
  `cancelled.json` の形を文書化（従来は additionalProperties で偶然通っていた）。
  `workspace` には「将来 checkout を実装する場合は repos.schema.json のエントリ形に揃える」旨を明記。
- agent-cli.schema.json の共有ツール一覧に agent-amigos を追記（`agentcli.py` は同スキーマの
  プラグインローダを実装済みだった）。
- **agent-dashboard: GitBus / HubBus ホームのミッション対応付けを修正**: `bus: git+…` / `hub+…`
  のホームは busDir が解決されず、ミッション → ホームの対応（引き受け・依頼の投函先解決）が
  切れていた。設定 `bus_workdir`、無ければ agent-amigos と同じ既定
  `~/.agent/amigos/{bus|hub}/<sha1(url)[:8]>` へ解決する。ミッション自動発見も GitBus の
  `bus/*` に加えて HubBus ミラーの `hub/*` を対象にした。

### agent-flow / agent-dashboard: リトライで旧 run の成果記録が消えないようにする（墓標）

- **agent-flow**: リトライ（新 run-id での世代交代）時、`inherit_from` が先行 run を bus から
  削除する前に墓標 `runs/<新>/inherited/<旧run-id>.json`（meta・graph・final・results の要約。
  工程出力は冒頭 1200＋末尾 2400 字の抜粋）を残すようにした。特に「全ノード done だが
  verify NG → feedback 付きリトライ」は結果を引き継がない設計のため、これまで完走した run の
  成果記録がリトライ開始の瞬間に bus から完全消滅していた（viewer がその瞬間にポーリング
  していなければ二度と見られない＝「リトライした run の成果物が dashboard 上で消失」の正体）。
  前世代の墓標も持ち越すため、最新 run に全世代の要約が残る。
- **agent-dashboard**: 墓標を readRun 互換のサマリへ変換して読み（`readInheritedTombstones`）、
  ポーリング時に flow-archive へ補完保存する（live 中に撮れた終端スナップショットがあれば
  そちらを正とし、無い/実行途中の写ししか無いときだけ墓標で置き換える）。フロータブでは
  「リトライで置き換えられた実行の記録」と明示する。
- **リトライ中も成果への導線を残す**: 要対応の「成果を確認」は最新試行（last_run）が done の
  ときしか出ず、リトライ実行中は旧世代の完了成果へ到達できなかった。閲覧は系統内の最新 done
  世代へフォールバックする（完了承認の可否判定は従来どおり最新試行の done を根拠にする）。
- **「そのまま再実行」の run-id が系統から切れる問題を修正**: 旧形式 `<run-id>-retry-<ts>` は
  決定的 run-id の解析（`REQ_ID_RE`）に合わず taskId/lineage が失われていた。req- 形式は
  `-v<retry-ts>` として付け、系統解析を保つ（長い id も切り詰めず保持）。

### agent-dashboard: 計画バージョンの継承を agent-project の規則に揃える（マスター憲章）

- **新規プロジェクト作成で入力した制約・前提が最初のバージョンに効かないバグを修正**:
  生成されるバージョン charter が空の `## constraints` / `## assumptions` 見出しを持っており、
  本体の継承規則「見出しがあって空＝継承値を空に上書き」によりマスターの制約・前提が
  適用されなかった。バージョン生成（`buildCharter` の `version` 指定・バージョン雛形）では
  空の制約・前提を見出しごと省略する。
- **フォーム保存が継承を黙って切断しないようにした**: 見出しの無い（マスターへ追従中の）
  バージョンをフォームで開いて保存すると、マスター値のスナップショットが明示値として
  書き込まれ、以後マスターの変更が伝搬しなかった。継承値から**変更したときだけ**見出しを
  書いて明示化し、変更しなければ見出しを書かず追従を維持する（全削除は「空で上書き」として
  空見出しを書く＝本体の意味論と同じ）。フォームの注記も追従中/固有値で出し分ける。
- **継承プレビュー・seed のマスター判定**: フォームが charter.md のマスター宣言
  （`## master`）を確認せずに制約・前提を「継承値」として表示していたのを、マスターの
  ときだけにした（非マスター charter.md から本体は継承しない）。
- **表示用 charter パーサを本体の規則に統一**: `## goal（目標）` のような注釈付き見出しや
  `# 憲章`（コロン任意）タイトルを取りこぼしていた（フォーム用パーサとも食い違っていた）。
- **AI 補助・概要表示の継承フォールバックを本体と同じ規則に**: goal/acceptance は
  「空ならマスター」、constraints/assumptions は「見出しが無ければマスター」（明示の空は
  空のまま）。概要のバージョンカードは goal 未設定時に共通設定の目標を継承表示する。

### agent-dashboard: repos / タスクの UI を agent-project のモデルに揃える（モノレポ対応）

- **repos フォームに `path`（モノレポ内フォルダ）と `target`（MR 先）を追加**（新規プロジェクト・
  編集の両方）。schemas/repos.schema.json の identity (url, path, base) どおり、同じ URL を
  path 別の行に分けてモノレポを表現できるようにした。保存時は名前重複と (url, path, base/target)
  重複を本体（parse_charter）と同じ規則で検証する。
- **フォーム往復でデータを失わないようにした**: これまで repos.json をフォームで開いて保存すると
  `path`/`target`/`readonly`/`local`/`docs` などフォームに列が無いキーが黙って消えていた。
  読み込み時に `_extra` として保持し、保存時にそのまま書き戻す（スキーマの additionalProperties /
  本体の未知キー保持と同じ契約）。
- **repos.yaml / repos.yml のプロジェクトで編集が無効になる問題を修正**: 本体は
  `repos.{yaml,yml,json}` の優先順で読むが、dashboard は repos.json 固定だった（yaml 運用では
  一覧が空に見え、フォーム保存した repos.json は本体に無視される）。ファイル解決を本体と同じ
  優先順に揃え、yaml/yml が正のときはフォームではなく生テキスト編集へ誘導する。
- **自動生成 repos.json（`_meta.generated_from`）の警告をフォームにも表示**: 生テキスト編集と
  同様に「保存すると手管理へ切り替わり charter の ## repos は反映されなくなる」ことを保存前に示す。
- **タスク追加に「書込先リポジトリ（workspace）」選択を追加**し、inbox 契約が
  `workspace` / `refs` / `paths` / `review` / `expect` / `followup`（task.schema.json の
  ルーティング・検収フィールド）を通すようにした。done タスクの「編集してやり直す」も
  これらを含めて引き継ぐ（従来は再投入で消えていた）。
- schemas/task.schema.json の `status` enum に `proposed` / `offloaded` / `rejected` を追加
  （agent-project の VALID_STATUS と同期。dashboard は以前から表示対応済み）。

### agent-amigos: 設定パスを `.agent/agent-amigos.yaml` へ移行

- 設定・状態領域を agent-project と同じ `.agent/` 配下へ揃えた。
  - 設定: `.kiro/kiro-amigos.*` → **`.agent/agent-amigos.{yaml,yml,json}`**
  - 指示取り込み / designs: `.kiro/kiro-amigos/{commands,designs}` →
    **`.agent/agent-amigos/{commands,designs}`**
  - 雛形: `kiro-amigos.yaml.example` → **`agent-amigos.yaml.example`**
- agent-dashboard のホーム自動発見マーカーも同パスへ追従。旧 `.kiro/` パスは読まない。
- 設定探索を agent-project と同じ `./` → `./.agent/` → `~/.agent/` に拡張。
  `--config` 任意パス時の `_home` は設定ファイルの親（`~/.agent/` は cwd）に修正。
- バス解決を全サブコマンドで `resolve_bus_spec`（CLI > 環境変数 > 設定 > ホーム）に統一。
- dashboard の `manual_claim` が JSON boolean / YAML `yes`/`on` でも効くようにした。
- GitBus の `git init` に `--template=` を付け、hooks コピー不可環境でも初期化できるようにした。

### agent-amigos: 常駐運用を agent-project に合わせる（無引数 serve・`.agent/agent-amigos.yaml`・cwd-as-hub・dashboard 依頼/引き受け）

- **サブコマンド省略 = 常駐起動（serve）**: agent-project の `run --watch` 既定と同じ流儀で、
  `agent-amigos` だけで cwd をホームとして面倒見るデーモンが立つ（ノードデーモン +
  指示取り込み + hub 公開）。
- **設定 `.agent/agent-amigos.yaml`**（`.yml`/`.json` 可・雛形 `agent-amigos.yaml.example`）:
  bus / node_id / agent_cli / tags / roles / interval / resume_hours / manual_claim /
  hub（serve/host/port/token）。優先順位 CLI > 設定 > 既定。バス解決は
  CLI > `AGENT_AMIGOS_BUS` > 設定 bus（既定 `.` = ホーム自身）。設定ファイルは
  dashboard の自動発見マーカーを兼ねる。
- **cwd を hub として利用可能**: `hub.serve: true` でホームのローカルバスをそのまま hub 公開。
  ローカル直接書き込みとの共存のため hub に**再走査**を追加（PUT を経ないファイル変更・
  削除を /list・long-poll 時に間隔律速で索引へ反映）。
- **指示のファイル取り込み** `<home>/.agent/agent-amigos/commands/*.json`（agent-project の
  commands/ と同じ結合方式）: `post`（タスク依頼 — design 本文はホームの designs/ へ永続化）/
  `claim`（手動引き受け — ポリシー準拠。owner-picks でオーナー自身なら応募＋即時確定）/
  `assign` / `accept` / `reject` / `cancel` / `say`。処理済みは削除・失敗は `.rejected`。
  `manual_claim: true` で自動応募を止めて手動引き受けだけで回せる。
- **agent-dashboard**: Amigos タブがホームを自動発見（`projects.roots` 走査 +
  `amigos.homeDirs`）し、**タスク依頼フォーム**と募集中ロールの**「引き受け」ボタン**を追加
  （どちらも commands 投函 — バスへは直接書かない。IPC は amigos:request / amigos:claim）。
- テスト: agent-amigos 53 件（+9: 設定ローダ・serve 読み替え・commands 取り込み
  post/claim/不正拒否・manual_claim・hub 再走査）、dashboard 371 件（+4: ホーム発見・
  投函検証・home 対応付け・**dashboard 投函 → Python 常駐デーモン取り込みのクロス検証**）。

### agent-amigos: ミッションスキーマを汎化名へ改称 + 単体実行インストーラを追加

- **スキーマ改称**: `schemas/amigos-mission.schema.json` → **`schemas/mission.schema.json`**。
  repos / task / node-budget と揃えた汎化名（協働ミッション公示の契約）にした。`$id`・
  タイトルと、設計書・README・roles.yaml.example・schemas/README・テストの参照を更新
  （実行時は stdlib パーサ検証でスキーマファイルを読まないため挙動不変。テストの
  enum/既定値突き合わせパスのみ追従）。
- **インストーラ** `tools/agent-amigos/install.sh`: agent-project / agent-flow と同じ
  **zipapp 単一実行ファイル**方式（標準ライブラリのみ・pip 依存なし）で
  `~/.local/bin/agent-amigos` へ配置（`--prefix` で変更可）。環境チェック（python 3.9+・
  git（分散時）・agent CLI（無ければ stub）・PyYAML（YAML 使用時））と、ローカル /
  git / hub / owner-picks / acceptance: agent / ノード予算の使用例を表示する。実体は
  `agent_amigos/` パッケージのまま（LLM が編集できる断片）、配布のみ 1 ファイルに束ねる。
  README にインストール手順を追記し、実行例をインストール後の `agent-amigos …` 形へ統一。

### agent-amigos: P2（hub サーバ・owner-picks・acceptance: agent・スキーマ正典化）を実装

- **hub サーバ + HubBus**（`--bus hub+<url>`）: git が使えない環境・低レイテンシ向けの
  任意コンポーネント。stdlib http.server の薄い API（所有者上書き PUT / リビジョン付き
  差分 list（long-poll 可）/ tree 削除）で、**調整はしない**（中央が落ちても壊れない）。
  データディレクトリはミッションレイアウトそのまま — hub ホストの dashboard は
  busDirs に指すだけで読める。クライアントはローカルミラー + 差分同期（claim の
  勝者確認は force pull）、Bearer 認証（`AGENT_AMIGOS_HUB_TOKEN`）、プロキシ迂回で
  LAN 直結。起動: `agent-amigos hub --data <dir>`。
- **owner-picks**: claim は「応募」になり、確定はオーナーの `agent-amigos assign
  <mid> <role> <node>` だけが行う（応募者一覧は `assign <mid> <role>` / status に表示）。
  自己補充（self-staff）は応募 + 即時確定で従来どおり 1 ノード完結。mirror_roster は
  自動確定せず離脱の掃除のみ（away 保持・クラッシュ再募集は両ポリシー共通）。
- **acceptance: agent**: reviewing になるとオーナーノードの agent CLI が design doc と
  deliverable（有界抜粋）を突き合わせて accept / reject を自動判定。差し戻しは通常の
  ラウンドとして働き、`convergence.review_rounds` 回で停止して owner へ
  decision-request をエスカレーション（**無限ループを作らない**。final を書けるのは
  オーナーノードだけ、の不変条件は維持）。stub 判定は決定的（partial → 差し戻し）。
  codd-gate 受入は将来拡張のまま。
- **スキーマ正典化**: [`schemas/mission.schema.json`](schemas/mission.schema.json)
  を新設（post --roles 入力の契約）。enum・既定値が実装（normalize_mission）とズレて
  いないことをテストで担保する。
- テスト 44 件（+12: hub 2 ノード E2E・hub 越し claim 競合・Bearer 認証・hub gc /
  owner-picks 応募と確定・E2E / 自動受入 done 到達・partial 差し戻し → 上限で人へ /
  スキーマ突き合わせ ×3）。

### kiro-loop / agent-project / agent-flow: ノード予算（node-budget 契約）の記帳・抑制を組み込み

- ノード予算の共有台帳（[`schemas/node-budget.schema.json`](schemas/node-budget.schema.json)）
  の消費側を**全ワークロードに展開**。これで「定常業務・プロジェクト・フロー・amigos の
  合計で上限を超えない」が実効になる（0 = 無制限が既定。設定が無ければ挙動は従来どおり）。
- **agent-flow（`flow`）/ agent-project（`project`）**: LLM 単一チョークポイント
  （`run_agent` / `_run_agent_cli`）で実行前にチェックし、超過は
  **`[agent-error:quota] [node-budget]`** として既存の決定的トリアージに乗せる —
  agent-flow は run を環境要因で即終端（全ノードでリトライを焼かない）、agent-project は
  リトライ・裁定を消費せず needs へ、viewer/dashboard は理由を言い切れる。成功実行の
  実測秒（monotonic）を台帳へ記帳。
- **kiro-loop（`routine`）**: `PeriodicScheduler._run_loop` がサイクル先頭でチェックし、
  超過中は定期送信・webhook キューの dispatch を停止（10 分間隔の警告ログ・キューは保持、
  上限引き上げ/期間更新で自動再開）。実行秒は**セマフォスロットの保持時間**（送信 →
  完了検知）で近似し `GlobalSemaphore.release` で記帳（タイムアウト強制解放は数えない。
  セマフォ未使用時は計測点が無く記帳されない既知の制約）。`agent-loop`（未統合クローン）
  へは次回のクローン同期で反映。
- 記帳は O_APPEND の best-effort（失敗しても実行は止めない — 上限は次の実行前チェックで
  効く）。読み書きは各ツールが自前の小さな実装を持つ（agent-cli プラグインと同じ
  「データ契約のみ・コード共有なし」の流儀）。
- テスト: agent-flow +7（超過で quota タグ即失敗・CLI 不呼び出し / 内訳上限 / period /
  記帳 / 成功実行の実測記帳）、agent-project +3（同系）。既存スイートは agent-flow 474 件・
  agent-project 794 件・agent-amigos 32 件・agent-dashboard 367 件すべて通過。

### agent-dashboard: Amigos タブ（agent-amigos ミッション + ノード予算の管理面）を追加

- **新 feature** `src/features/amigos/`（制御面分離の流儀どおり base / 他 feature 無改造で
  差し込み。IPC は `amigos:overview` / `amigos:budgetSave` の 2 チャネルのみ）:
  - **ミッション一覧（読み取り専用）**: バス上のファイル（真実）だけを読み、dashboard から
    バスへは一切書かない。ローカルバス（`missions/<mid>/`）と GitBus クローン作業領域
    （`mission__<mid>/`）の両形式に対応し、`amigos.busDirs` 未設定時は
    `~/.agent/amigos/bus/*` を自動発見。phase（近似導出）・ラウンド・名簿（担当 ×
    完了/一時停止）・ミッション予算消費・未回答質問数・partial 納品を表示する。
  - **ノード予算の管理面**: node-budget 契約（`schemas/node-budget.schema.json`）の
    config を書き ledger を読む。期間内の消費をワークロード別（定常業務 / プロジェクト /
    フロー / Amigos）に表示し、合計上限・期間（day/month/total）・内訳上限を編集
    （**0 = 無制限**）。超過中は「amigo は一時停止」を明示。依頼側・請負側どちらの
    ノードでも同じ契約 = 同じ画面。
  - タブはミッションか予算データが存在するときだけ表示（cowork と同じ流儀）。
- テスト: 配線テスト（feature-split）に amigos を追加、`test/amigos.test.js` を新設
  （予算集計・超過判定・保存 / 両バス形式の読み取り・phase 導出 / **Python 実装
  （agent-amigos stub）が実際に生成したバスを読めるクロス検証**）。全 367 件通過。

### agent-amigos: ノード予算（請負側の上限）とツール横断の共有台帳を追加

- **予算を二層に**: ミッション予算（依頼側がバスに宣言、§3.2）に加えて、
  **請負ノード側でも上限を設定可能**に。ノード予算はツール横断の**共有台帳契約**
  （新規 [`schemas/node-budget.schema.json`](schemas/node-budget.schema.json)、
  置き場所 `$AGENT_BUDGET_DIR`＝既定 `~/.agent/budget/`）で管理され、
  定常業務（routine）・agent-project（project）・agent-flow（flow）・amigos の
  **全ワークロード合計**に上限を掛ける。**0 = 無制限**（既定）。期間は day / month /
  total（日次リセットが既定）。ワークロード別の内訳上限も設定可。
- **amigos 側の実装**: ターン開始前に台帳の合計をチェックし、超過中はそのノードの
  amigo だけ **paused**（`[node-budget]` タグ・遷移時に一度だけ owner へ通知）。
  **ミッションは殺さない** — 他ノードは進行継続、上限引き上げ・期間更新で自動復帰。
  各ターンの CLI 実行秒は `workload: amigos`・`ref: <mission>/<role>` で台帳にも記帳
  （バス events = ミッション予算、台帳 = ノード予算の二重帳簿）。
  CLI: `agent-amigos budget node [--limit-minutes N] [--period day|month|total]
  [--amigos-minutes N]`（表示・設定）。status にもノード予算行を表示。
- **管理は依頼側・請負側どちらも**: agent-dashboard はこの契約（config.json を書き
  ledger を読む）でどちらのノードの管理面にもなれる。dashboard の管理タブと
  kiro-loop / agent-project / agent-flow の記帳・抑制の組み込みは、この契約に従う
  フォローアップ（契約が先、実装は各ツール — repos / task / agent-cli と同じ流儀）。
- テスト 32 件に拡充（+5: 0 = 無制限で完走・超過で paused ＋ owner 通知・上限引き上げで
  復帰完走・内訳上限（他ワークロード消費は不干渉）・他ツール消費だけで合計上限に到達）。

### agent-amigos: P1（GitBus 分散・away プロトコル）を実装

- **GitBus**（`--bus git+<url>`）: オンプレ git remote の**専用バスリポジトリ**で
  複数 PC 分散が動く。`main` は公示インデックスのみ、ミッション本体は
  `mission/<mid>` ブランチに分離（参加したブランチだけ clone、gc はブランチ削除）。
  同期は state_git の規律を流用 — pull 間隔律速（claim の勝者確認だけは force で
  常に最新化）・push 競合は `pull --rebase` → 再 push の指数バックオフ・
  force push なし・**1 ターン = origin 上の 1 コミット**（原子性、テストで検証）。
  各ノードが自分専用クローンを持つため `add -A` ステージでも他者の書き込みを
  巻き込まない（state_git「自 subdir のみステージ」の等価実装）。
- **away プロトコル**: デーモンは SIGTERM / Ctrl-C で graceful offboard
  （全 amigo を `state: away` + `resume_at` にして最後の push）。away 中は lease が
  切れても resume_at + grace（既定 2h、`AGENT_AMIGOS_AWAY_GRACE`）まで**ロールを
  保持**し、復帰した本人が続きから再開する。grace 超過・away 宣言なしのクラッシュは
  従来どおり再募集。予算は実質実行時間ベースなので不在時間は予算を消費しない。
- **git バスのコミットノイズ対策**: idle ターンの status 書き込みを quiescence 判定に
  影響しない範囲でキャップ（ハートビートは 60s 間隔で維持）、lease 更新は残り半分を
  切ってから（state_git「アイドル中の追加コミットはゼロ」の流儀）。
- **partial → done 昇格**: 静穏化・予算枯渇で partial 統合した後に全ロール完了へ
  到達したら、integrator が完全版で統合し直す。
- **adaptive interval**: 無風時はデーモンの巡回間隔を伸ばす（上限 8 倍）。
- テスト 27 件に拡充（+8: GitBus 2 ノード E2E・git 越し claim 競合の勝者一致・
  1 ターン 1 コミット・gc ブランチ削除・away 保持/grace 超過/クラッシュ区別・
  offboard → 復帰再開）。

### agent-amigos: P0（MVP）を実装

- **新ツール** [`tools/agent-amigos/`](tools/agent-amigos/): 設計書 P0 スコープの実装。
  ローカルバス上で post（公示）→ claim 型アサイン（決定的タイブレーク）→ 自己補充
  （self-staff で 1 ノード完結）→ 型付きメッセージ（質問/回答/レビュー/承認）→
  integrator 統合 → collect / accept / reject（差し戻しラウンド）の全周が動く。
  - **アクション封筒ランナー**: LLM はバスに直接書かず、ランナーが
    `send / write_artifact / update_status / declare_done` を検証して代書
    （パス逸脱・不正宛先・越権 approve は棄却して events に記録）。
  - **収束条件と予算会計**: `events/<who>.jsonl` の `cli_seconds` 総和による決定的会計。
    soft で wrap-up モード宣言、hard で partial 統合（`on_exhausted: fail` は終端）、
    静穏化（quiescence）収束、`budget add` による予算追加。
  - **agent CLI**: kiro / claude / copilot / codex 組み込み ＋ `agents/<name>.json`
    プラグイン契約（探索順・エラートリアージ `[agent-error:*]` は agent-flow と同一）。
    quota/auth/env は amigo を paused にして owner へ通知（他ロールは進行継続）。
  - **テスト 19 件**（stdlib unittest・stub のみ・LLM 不要）: claim の二重アサインなし・
    lease 失効 → 再募集・2 ノード分担・E2E・差し戻し・予算 wrap-up / fail・封筒検証・
    owner エスカレーション。
  - GitBus（専用バスリポジトリ＋ミッション別ブランチ）・away プロトコルは P1、
    hub・dashboard 連携は P2（設計書 §16）。

### agent-amigos: 役割駆動マルチエージェント協働ツールの設計書を追加

- **新規設計書** [`docs/designs/agent-amigos-design.md`](docs/designs/agent-amigos-design.md)（Draft、実装未着手）:
  オーナーノードが design doc ＋ 役割ミッション表でミッションを公示し、分散ノードが
  ロールを claim して amigo（ロールを演じるエージェント）として参加、型付きメッセージ
  （質問・回答・レビュー・決定）で相互協働しながら 1 つの成果物を組み上げてオーナーへ
  納品する協働基盤の設計。kiro / claude / copilot / codex / cursor は既存の
  agent-cli プラグイン契約（`agents/<name>.json`）をそのまま利用。バスは agent-flow と
  同じファイルベース（LocalBus / GitBus / 任意の HubBus）で、中央サーバは「転送のみ」。
  1 ノードでも未充足ロールの自己補充（self-staff）で完結する。
  `docs/designs/README.md` の索引にも追加（24 → 25 件）。
  - **収束条件と予算**: オーナーは post 時に収束条件（全必須ロール完了・レビュー承認・
    静穏化）と予算（**実質実行時間** = 全 amigo の agent CLI 実行秒の総和）を宣言でき、
    amigo はその範囲内で自律的にやり取りして収束する。会計はバス上の追記ログの総和で
    決定的、枯渇時は wrap-up（現状統合の partial 納品）。予算追加はオーナーのみ。
  - **中央は専用バスリポジトリ**: オンプレ git remote に `amigos-bus.git` を新規に切り、
    `main`（公示インデックス）＋ `mission/<mid>` ブランチで**ミッション（タスク）単位に分離**。
    同期の運用規律（間隔律速・rebase リトライ・force push 禁止・自パスのみステージ・
    `fresh_after_sec` 生存表示）は agent-dashboard / agent-project の state_git を流用。
  - **定期シャットダウン耐性**: 計画停止をクラッシュと区別する away プロトコル
    （graceful offboard・毎ターン更新の引き継ぎメモ・away_grace までロール保持）と、
    ターン成果を単一コミットにまとめる all-or-nothing 原子性で、夜間停止・電源断でも
    バスに壊れた中間状態を残さない。実行時間ベースの予算なので不在時間は予算を消費しない。

### agent-project: バックログに誘導・レビュー記述フィールドを追加（why / desc / scope / out_of_scope / constraints / hints / demo）

一般的なバックログ項目の慣行（背景・説明・スコープ境界・制約・確認手順）に合わせた任意
フィールドをタスク書式に追加。**人のレビュー**と**エージェント誘導**の両方に効く:

- **act 要求文へ整形注入**（`build_request`）: desc（作業内容の詳細）→ why（判断基準）→
  scope / out_of_scope（境界。範囲外は `@followup` 提案へ誘導）→ constraints（タスク固有の
  制約）→ hints（実装の手がかり）→ demo（人の検収観点）の順でワーカーに提示され、書けば
  挙動が変わる。値は 1 行（改行・リストは ⏎ 規約で 1 行化）。
- **レビュー票に判断材料として掲載**: 実行前レビュー（plan-review）・検収・blocked の
  `needs/<id>.md` のタスク定義ブロックに載り、「なぜこのタスクか」「どこまでやるか」から
  人が判断できる。plan（charter 分解）と敵対的レビューのプランナーは **why を必ず**、
  out_of_scope / hints を有益なら付けて提案する。
- **CLI・cohort・assess に対応**: `enqueue --why … --scope …` / `revise <id> --out-of-scope …`
  （commands/ の JSON ドロップも同キーを受理）。cohort は pilot・生成メンバへ引き継ぎ
  （`{item}` 差し込み可）。投入時アセスメント（c/r/a 採点）も記述があれば材料にする。
- done の根拠は従来どおり **verify のみ**（これらは誘導であって完了条件ではない）。
  書式の正典 `backlog.md.example`・JSON スキーマ `schemas/task.schema.json` を更新。
- **AI による補完**: dashboard のタスク詳細（修正フォーム）に「意図と境界」セクションと
  **「✦ AI で補完」**を追加 — 新モード `task-guide` が charter・既存 backlog・タスク定義から
  根拠のある項目だけを下書きし（憶測で境界を発明しない契約）、人が確認してから revise で送信。
  フォローアップ提案（検収 AI 補助）も why を必ず・out_of_scope / hints を有益なら付けて提案し、
  タスク追加フォームへ引き継がれる。本体側は plan-review 差し戻しの AI 修正（plan_rework）が
  誘導記述の補完・更新に対応（応答にキーが無い項目は既存値を温存）。
- **表示・引き継ぎの整合**: dashboard のタスク詳細で誘導記述を散文表示（⏎ は改行へ復元。
  feedback/note も同様に）、バックログ一覧に「目的（why）」を表示。inbox 投入・完了タスクの
  再投入・cohort・spec 展開の全経路で欠落しないよう許可リストを統一。
- **agent-flow**: stub プランナーが構造化要求（空行を含む＝build_request の要求文）を本文中の
  `;` / `->` で誤分割していたのを修正（区切りのミニ言語はフラットな 1 行/リスト要求専用に。
  verify コマンドや誘導記述に混ざる記号で issue が細切れにならない）。

### agent-dashboard / agent-project: 検収・定常業務の 4 つの不具合を修正

- **verify 未定義タスクを人の承認で完了できるように**: verify の無いタスクは工程完了後に
  blocked（確認待ち）になるが、要対応に「承認して完了にする」ボタンが無く、approve も
  ready 積み直し（再実行 → また blocked）の無限往復で完了できなかった。agent-project の
  `approve` が verify 未定義の確認待ち blocked を **done 確定**（納品書・決定記録つき）に
  し、dashboard の要対応／検収画面にも「承認して完了にする」を出す（環境要因
  `env_resume` の blocked は従来どおり「続きから再開」）。
- **検収画面で `/mnt/c/...` の diff が読めない問題**: WSL 側が記録した `/mnt/<drive>/...`
  の検収リポジトリを UNC（`\\wsl.localhost\...\mnt\c\...`）や `C:\mnt\c\...` に化けたまま
  解決していた。`toViewerPath` が `/mnt/<drive>/...` を **Windows ドライブ実体
  （`C:\...`）へ直接変換**し、diff・ファイルを開く操作が通るようになった。
- **定常業務の実行を新しいウィンドウ（WSL tmux + kiro-cli）で開始**: 従来の非表示
  `spawnSync`（60 秒 kill）はセッション未起動時の立ち上げ待ちで失敗し、理由も見えなかった。
  Windows では既定で**新しいコンソールウィンドウ**を開き、**kiro-loop を介さず** tmux
  セッションに kiro-cli をインタラクティブ起動（`cowork.chatCommand`）して、dashboard が
  解決したプロンプトを直接送信・そのまま `tmux attach` して**動いている様子を見られる**
  （`cowork.runWindow: false` で従来動作）。送るプロンプトは、kiro-loop に割り当てられた
  項目は `.kiro/kiro-loop.*` の**プロンプト本文**、それ以外は「statemachine-use スキルで
  〈名前〉ステートマシンを実行して」。`{{…}}` プレースホルダーや入力パラメータが未入力の
  場合に備え、**先に必要な入力を質問してから実行する補助文**を自動付加する。
  ウィンドウは `cmd /s /c start … wsl.exe` で開く（GUI プロセスからの直接 spawn では
  対話可能なコンソールが割り当てられずウィンドウが出ない）。スクリプト本文は
  `%TEMP%\agent-dashboard\` の一時ファイル経由で cmd の引用規則を回避。あわせて
  `loopCommand` / `chatCommand` の**複数語コマンド**（`python3 ~/…/kiro-loop.py` 等）と
  先頭 `~` の展開に対応（全体を 1 トークンとして引用して `not found` になっていた）。
- **定型業務の「端末」が tmux 画面を表示できない問題**: 原因は 2 つ。
  (1) tmux `-F` フォーマットの区切りがソース上リテラル `\t`（バックスラッシュ + t）で、
  tmux はこれをタブに変換せずそのまま出力するため、ペイン解析が全滅していた —
  本物のタブ文字を埋め込む形に修正し、ログインシェルのプロファイル出力ノイズ
  （nvm 等）も除外。
  (2) kiro-loop を tmux セッション内で起動するとワーカーペインは人のセッション（任意名）
  内に作られ、`tmux ls` のセッション名（`kiro-loop-…`）では見つけられない —
  **`~/.kiro/loop-state/*.json`（デーモンの状態ファイル）から pane_id を直接発見**して
  視聴する。既定接頭辞も `kiro` に広げ（`send` の既定セッション `kiro` を拾う）、
  Windows ドライブ上の repo と `/mnt/c/...` の cwd の突き合わせにも対応。
  実 tmux + kiro-loop デーモン（セッション内起動）での動作を確認済み。

### agent-dashboard: 計画レビュー・検収・バックログの AI 補助

計画レビューと検収で「何を見るか」は揃っていたが、**charter との整合・変更の意図・次の
バックログ案・依存/優先度の調整**は人が自力で補う必要があった。既存の読み取り専用 Doctor
契約を拡張し、判断の下ごしらえだけを AI が行い、承認・差し戻し・inbox 投入は従来どおり
人のボタンで確定する。

- **計画批評（plan-critique）**: 計画レビューカードの「AIで計画を批評」。提案タスクを
  charter / 兄弟 proposed と突き合わせ、取りこぼし・依存・acceptance・推薦・差し戻し文面案を返す。
- **変更理由（delivery-rationale）**: 検収カード／検収ダイアログの「変更理由を説明」。
  差分と verify/accept から「なぜ変えたか」と承認推薦を返す。
- **フォローアップ案（followup-suggest）**: 検収ダイアログの「フォローアップ案」。
  JSON でタスク案を返し、「タスク追加フォームへ」で人が確認してから inbox 投入できる。
- **依存・優先度提案（enqueue-assist）**: タスク追加の「AIで依存・優先度を提案」。
  after / priority / note を下書きし、既存タスクへの調整案も提示する。調整案は
  チェックボックスで選び「選択した調整を反映」で公式の `revise` として送信する
  （人確認必須・状態ファイル直書きなし）。手動側も先行タスクの datalist と
  既存バックログ一覧を追加。
- **差し戻し文面の流し込み**: Doctor 応答の「差し戻し文面案」を回答欄へコピーできる
  （送信は人が確定）。CLI は従来どおり読み取り専用。

> 設計: [`docs/plans/2026-07-15-agent-dashboard-plan-acceptance-assist-design.md`](docs/plans/2026-07-15-agent-dashboard-plan-acceptance-assist-design.md)。
> 改善案の B1/B2/B3 に対応: [`docs/designs/agent-dashboard-project-ux-improvements.md`](docs/designs/agent-dashboard-project-ux-improvements.md)。

### agent-dashboard: 要対応の待ち時間・SLA バッジ（停滞の可視化）

要対応（人の判断待ち）が**どれだけ待たされているか**が一覧から分からず、長時間放置＝下流が
止まっている状態に気づきにくかった。各カードに**待ち時間バッジ**を付け、未対応は**滞留の
長い順**に並べる（省力トリアージ＋停滞の可視化）。

- **待ち時間**: needs の最終更新（`mtime`。無ければ `date`）からの経過を「N 分/時間/日待ち」で表示。
- **SLA 色分け**: しきい値 `projects.needsSlaHours`（既定 24h・⚙ 設定）を超えると赤、1/3 を
  超えると黄。手戻りではなく**停滞**（人待ちで止まっている時間）を色で警告する。
- **並べ替え**: 未対応バケットは `mtime` 昇順（＝待ち時間の長い順）。既定選択も最も停滞した
  カードにして、最優先の判断へ自然に誘導する。
- 中核は純関数 `humanizeAge` / `needAgeInfo`（テスト: `test/needs-sla.test.js`）。
  再描画署名にラベルを含め、時間経過でラベルが変わったときだけ再描画する。

> 改善案の全体像は [`docs/designs/agent-dashboard-project-ux-improvements.md`](docs/designs/agent-dashboard-project-ux-improvements.md)（A4 として整理）。

### agent-dashboard: 要対応の OS 通知（気づく前に届く）

ダッシュボードは既定 5 秒ポーリングの純プル型で、**人が画面を見ていないと新しい要対応
（人の判断待ち）に気づけなかった**。張り付き監視を不要にする省力化として、新しい要対応が
現れたら **OS 通知・タスクバーバッジ・ウィンドウのフラッシュ**で知らせる。

- **増分検知**: `discover()` が各プロジェクトに載せる `needsCount`（サイドバーの要対応バッジと
  同じ数）を前回と突き合わせ、**観測済みプロジェクトで数が増えたときだけ**通知する。起動直後の
  既存分では通知しない（初回はベースライン取得のみ＝殺到しない）。減少・新規発見でも通知しない。
- **騒音を出さない**: ウィンドウを見ている（フォーカス中）間はポップアップとフラッシュを抑制し、
  バッジ（未対応の総数）だけを更新する。判断は main 側（`base/main/notify.js`）に集約。
- **クリックで対象へ**: 通知をクリックすると窓を前面化し、**既存のディープリンク経路**
  （`agent-dashboard://open?root=…` → `app:openTarget`）でそのプロジェクトを開く（新配線なし）。
- **設定**: ⚙ 設定に「要対応が増えたら OS 通知で知らせる」トグル（既定 on・
  `notifications.enabled`）。Windows は `setBadgeCount` 非対応のため通知とフラッシュで補う。
- **層の分離**: base は汎用の OS 通知プリミティブ（`app:notify`）だけを提供し、「何を・なぜ
  通知するか」は agent-project の意味を知る renderer が決める（feature 分離の方針どおり）。
  中核の増分ロジックは純関数 `computeNeedsDelta` に切り出してテスト（`test/needs-notify.test.js`）。

> 改善案の全体像は [`docs/designs/agent-dashboard-project-ux-improvements.md`](docs/designs/agent-dashboard-project-ux-improvements.md)（A1 として整理）。

### agent-flow / agent-project: kiro 依存の内部命名を汎化

旧 `kiro-flow` / `kiro-project` 由来で残っていた kiro 接頭辞の内部命名を、agent CLI 横断の
汎用名へ改称した。共有インフラ（`kiro-cli` 製品名・`agent_cli: kiro` 選択肢・複数ツール共有の
`$KIRO_*` 環境変数・`~/.kiro` 探索先・`kiro-loop`）は方針どおり維持している。

- **agent-flow の内部関数・変数**: `run_kiro`→`run_agent` / `execute_kiro`→`execute_agent` /
  `continue_kiro`→`continue_agent` / `plan_strategy_kiro`→`plan_strategy_agent` /
  `_kiro_timeout`→`_agent_timeout` / `_kiro_argv_limit`→`_agent_argv_limit` /
  `_KIRO_TIMEOUT`→`_AGENT_TIMEOUT` / `kiro_run` 注入引数→`agent_run`。
- **agent-flow の設定・環境変数**: 設定キー `kiro_timeout`→`agent_timeout`、環境変数
  `AGENT_FLOW_KIRO_TIMEOUT`→`AGENT_FLOW_TIMEOUT`。**いずれも旧名を後方互換で受理**する
  （既存設定・既存環境を壊さない）。
- **agent-project の内部**: `_run_kiro_cli`→`_run_agent_cli` / `kiro_run` 注入引数→`agent_run` /
  `_kiro_managed_rels`→`_agent_managed_rels`、一時ファイル接頭辞の kiro-* を汎用名へ。
- **ドキュメント修正**: 改称後に食い違っていた記述を訂正（executor/planner の既定値は `kiro`
  ではなく `agent`、`route_planner` 値は `agent/none`、`--executor kiro` 例→`--executor agent`、
  設計書のリンク切れ `kiro-spec-flow-integration`→`agent-spec-flow-integration` 等）。
  companion スキル（flow-worker / flow-planner）の関数参照・呼称も追随。

### agent-dashboard / agent-project: Bugbot PR コメント対応

- **startProject が --config / cwd を付けていなかった** — findProjectConfig と揃える。
- **findProjectConfig が状態 worktree 側 yaml を見落としていた** — dir と fromStateWorktree の両方を探索。
- **cmd_revise が offloaded 以外の flow_run（sync doing）を detach しなかった** —
  approve と同じく flow_run があれば切り離す（dashboard cancel→revise 向け）。

### agent-flow / agent-project / agent-dashboard: Set4 integration — CONVERGED

individual → integration で Set4 まで実施。手つなぎの新規バグは見当たらず停止。残差のみ:
- cancel 時 `close_issues=false` による GitLab イシュー再アタッチ
- remote `--git` bus への detach 伝播
- park lease UI（失効=pending）と flow 枠計算（wait 残=parked）の見え方差

### agent-flow / agent-project / agent-dashboard: Set4 individual バグ修正

- **run 化前 cancel マーカーを空 meta（{}）判定で消し、要求が起動していた** —
  daemon/cmd_cancel は `run_exists`、detach は meta 適用時だけ clear。
- **同期結果待ちが revise 以外の人操作（approve/hold）を無視** —
  flow_run ピン＋ status/detach 検知で中断。
- **sync `run` / alreadyTerminal が sticky cancel を残す** — 適用後に clear。

### agent-flow / agent-project / agent-dashboard: Set3 integration 手つなぎ修正

- **canceled を `--inherit-from` すると停止した行が蘇る** — flow/project とも canceled は引き継がない。
- **同期 act タイムアウトが run を非終端のまま放置** — detach（cancel）してから残骸刈り。
- **daemon 不在時に cancel マーカーが sticky** — dashboard/project/CLI 適用後に clear。
- **CLI cancel が終端 run の残 waits を残す** — dashboard と同じ掃除。
- **dashboard cancel が bus だけ push し revise が遅延** — project state も push。

### agent-flow / agent-project / agent-dashboard: Set3 individual バグ修正

- **agent-dashboard: 既に終端した run の cancel が revise でタスクを再キューしていた** —
  alreadyTerminal なら waits 掃除のみ（settled タスクを ready に戻さない）。
- **agent-project: `_kf_base` が flow_config（--config）を落とす** —
  sync run / submit / doctor も daemon と同じ yaml を渡す。
- **agent-project: act タイムアウトの `reap_orphan_flow` が外部 daemon ごと殺していた** —
  manage_flow_daemon=false では daemon 除外。submit タイムアウトは対象 run だけ cancel。
- **agent-project: 同期 `_act_run` が mid-revise を無視していた** — Popen ポーリング＋ detach。
- **agent-flow: 適用済み cancel マーカーが残り同一 ID 再開と毎 poll を汚染** —
  daemon 適用後に clear。orch は meta=canceled でも停止。

### agent-flow / agent-project / agent-dashboard: Set2 integration 手つなぎ修正

- **needs メモ付き環境復帰が env_resume を落として新 run になっていた** — メモは計画変更でない。
- **resume-run が offloaded / flow_run を放置し二重駆動し得た** — detach してから再開。
- **dashboard CLI が状態 worktree を --root に渡し二重リダイレクトしていた** — fromStateWorktree。
- **cancel が bus だけ止め project が offloaded のまま** — revise コマンドで本体契約どおり切り離し。
- **終端 run で park 抑制しつつ Issue 座標まで消していた** — waits から issue だけ読む。

### agent-flow / agent-project / agent-dashboard: Set2 individual バグ修正

- **agent-flow: 一晩再起動で park の wait_lease 失効だけを「進捗なし」扱いし max_resumes で failed** —
  wait ファイル残存を進捗と数え、枠消費も park 継続扱い。orphan fail 時は waits 掃除。
- **agent-flow: in-flight 差し戻しの冪等キーが文字数だけで同じ長さの別指摘を落とした** — 内容ハッシュへ。
- **agent-project: offloaded の approve / feedback が flow を止めなかった** — detach＋retries。
- **agent-project: reap が revise 後の ready を奪って settle し得た** — claim 後に offloaded 再確認。
- **agent-dashboard: 終端 run の残 waits を park 表示し、cancel も掃除しなかった** — 表示抑制＋掃除。
- **agent-dashboard: 長い run-id の resubmit が末尾スライスで接頭辞を落とした** — 中央切り詰め。

### agent-flow / agent-project / agent-dashboard: Set1 integration 手つなぎ修正

- **feedback 差し戻しが同じ run-id を再生成し agent-flow が旧 request で再開した** —
  ingest が retries を進め新 id にする（dashboard Decision Outcome → project → flow）。
- **dashboard の bus 解決がローカル残渣 runs を優先し、設定バスと割れた** —
  flowBus* / yaml `bus:` を先に採用。
- **sync `run` に `--inherit-from` が無く、submit だけ done を引き継いだ** —
  last_run 基準で sync/submit/offload を揃える（rev バンプ後の retries-1 空振りも解消）。
- **`taskIdOfRun` が ap/kp 以外の prefix を無視し resubmit が inbox へ落ちた** —
  単一段 `prefix/task` を受理。
- **dashboard CLI 委譲が cwd 依存で設定を拾えなかった** — `--config` + cwd 固定。
- **旧 `## フィードバック` 票が UI 上ずっと undecided** — project の FEEDBACK_MARKERS と揃える。

### agent-flow / agent-project / agent-dashboard: Set1 individual バグ修正

- **agent-flow: 途中「差し戻し」がイシューをクローズしていた** — docstring は閉じないとあるのに
  `_rejected_payload` 経由で閉じていた。`_rework_payload` で open のまま guidance を返し、
  note 消費マーカーで再アタッチ即却下ループを防ぐ。
- **agent-flow: 同期 run が非終端 orch 死で exit 0 になり得た** — failed 確定＋非 0。
  orch cancel は `close_issues` 時 waits を残し、daemon 終端時に on_cancel してから掃除。
- **agent-project: act 失敗/canceled が revise 予約を踏み潰した** — 先に `revised` を見て積み直し。
- **agent-project: submit 結果待ち中の revise が daemon run を放置した** — `detach_flow_run` で止める。
- **agent-project: doctor の orphan reap が watch 限定だった** — 単発 run でも刈る。
- **agent-project: hold/block 切り離し後に同一 run-id を再生成し得た** — detach 時に retries を進める。
- **agent-project: 環境ブロック復帰が feedback で新 run になっていた** — `env_resume` で同 run 再開。
- **agent-project / dashboard: 本文チェックリストの [x] で確定扱い** — Decision Outcome 配下のみ。
- **agent-dashboard: live 判定が listRuns(30) だけだった** — 31 件目以降が archived 誤表示。
- **agent-dashboard: ディープリンクが状態ルート（`root`）を見逃した** — `x.root` も照合。

### agent-dashboard: canceled やり直しの文言を本体契約に合わせる

- 助言・確認ダイアログ・トーストが「部分やり直し／同一 run 再開」と書いていた。
  canceled は新 run 固定なので文言を修正（ボタンラベルと一致）。

### agent-project / agent-dashboard: canceled 後の同一 run-id 再突入を防ぐ

- **cancel → ready のとき retries を進めなかった** — 次の `_new_run_id` が同じ id を生成し、
  agent-flow は終端 canceled を再開できず固まる。retries を進め新 run にする。
- **`resume-run` が canceled/done にも last_run を固定していた** — 同上の衝突。新実行へ振り分け。
- **dashboard が canceled を「失敗工程だけやり直し」と表示** — 文言を新実行向けに修正。

### agent-flow / agent-project: 同期 run の cancel を失敗として伝える

- **`agent-flow run` が canceled でも exit 0 だった** — `_act_run` が成功扱いし、verify=true で偽 done。
  canceled は exit 2。agent-project は meta.status=canceled を見て `… canceled` メッセージを返し、
  既存のリトライ非消費 ready 経路に乗せる。

### agent-project: offloaded タスク切り離し時に flow run を cancel

- **revise / hold / reject が委譲中 run を放置していた** — `flow_run` だけ落として agent-flow は走り続け、
  `ap/<task-id>` へ二重書き込みし得た。`detach_flow_run` で cancel マーカー＋meta canceled＋waits 掃除
  （dashboard / agent-flow cmd_cancel と同契約）。

### agent-project: 隣接 agent-flow の解決パスを修正

- **パッケージ分割後の `resolve_agent_flow` が誤った相対パスを見ていた** —
  `agent_project/request.py` から parent×2 だと `tools/agent-project/agent-flow`（存在しない）。
  正しくは tools 配下の隣接 `tools/agent-flow/agent-flow.py`。act 起動失敗が verify 成功で
  偽 done になっていた穴とセットで顕在化した。

### agent-project: daemon 再開・act 失敗・result run-id 連携を修正

- **resume-run / 失敗 run の続きが daemon 経路で効かなかった** — submit は `run_exists` で無視され
  `retry_failed` は `run` だけ。再開可能な `last_run` があるときは `_act_run` へ寄せる。
- **act 失敗 bool が捨てられ verify=true で偽 done になり得た** — `_act_batch` が ok を伝搬し、
  失敗時は `_settle_failure`（reap も同様）。
- **却下 guidance / approve notes が `--run-id` 無し** — 共有バスで別タスクの result を拾い得た。
  `last_run` を渡す。

### agent-flow / agent-project / agent-dashboard: 個別のキャンセル・再開まわりを修正

- **agent-flow: cancel 後も worker が pending を claim し続けた** — 終端判定を「仕事が無いとき」だけにしていた。TERMINAL なら claim 前に退出。
- **agent-flow: orchestrator の cancel が waits を残した** — daemon は既終端だと cancel 本体をスキップするため park が残った。orch 側で clear_waits、daemon も終端時に waits 掃除。
- **agent-flow: `set_status` が終端→running へ復活できた** — canceled 後の plan/resume 上書きを拒否。
- **agent-project: submit/offload が `last_run` を書かなかった** — settle/resume/delivery が run を見失う。全 act 経路と reap で pin。
- **agent-project: `revise` が offloaded を無視した** — 委譲中の修正が古い結果で settle され得た。flow_run を切り離して ready へ。
- **agent-dashboard: `readRun` がブランチ逆引きの taskId を載せていなかった** — 旧形式 run の助言・導線が外れる。
- **agent-dashboard: canceled 削除確認が「応答なし」と誤表示** — 終端集合で判定。parked を残り件数に含める。

### agent-dashboard / agent-project / agent-flow: 連携まわりの回帰バグを修正

- **agent-dashboard: `taskIdOfRun` が改名後の `ap/<task-id>` ブランチを見ていなかった** —
  コメントとテストは `ap/` なのに正規表現が旧 `kp/` のまま。旧形式 `run-<ts>-<rand>` の
  「やり直し」がタスク逆引きに失敗し、`bus/inbox` 投入（daemon 無しでは誰も拾わない）へ
  落ちていた。`ap/` を受け、旧データ互換で `kp/` も残す。
- **agent-dashboard: やり直しがワークスペース（`selectedDir`）へ `resume-run` を書いていた** —
  状態は `project.dir`（状態 worktree / `root:`）にある。`selectedDir` には backlog が無く
  タスク経路に乗れない／乗っても本体が監視しないツリーへ命令が落ちる。`project.dir` に統一。
- **agent-dashboard: `nodeTaskToken` が世代接尾辞を落としていなかった** —
  agent-flow gitlab executor は `-rN`/`-vM` を落として安定化する。viewer が全文ハッシュすると
  リトライ後のイシュー突合が外れ、クローズ済みが「実行中」のまま見える。executor と同契約に揃える。
- **agent-dashboard: git pull / 同期修復が状態 worktree ではなくワークスペースを見ていた** —
  リモートの backlog・commands・bus が画面に入らない。`project.dir` を pull/heal の対象にする。
- **agent-dashboard: cancel の git 反映から `waits/` 削除が抜けていた** — リモートで park 表示が
  一瞬復活しうる。`runs/<id>/waits` も pathspec に含める。
- **agent-project: 同期 run と daemon submit の run-id ハッシュが割れていた** —
  `_new_run_id`（hash(task.id)）と `_req_id_for`（hash(backlog)）が別系統。同一タスクが
  UI 上で別 lineage に見える。`_new_run_id` を `_req_id_for` に統一。
- **agent-project: canceled run を success 扱いにしていた** — `_flow_result_once` /
  `_act_submit` が `canceled` を ok とし、offload 回収が `verify=true` で done 確定し得た。
  canceled は失敗扱いし、reap / 同期 settle ではリトライを焼かず ready へ戻す。

### kiro-flow: モジュール分割（kiro-project と同じ断片合成）＋ zipapp 単一 CLI 配布

- **背景**: 単一 `kiro-flow.py`（約 6,800 行）は LLM ワーカーが丸ごと読むと context を圧迫する。
  kiro-project は既に「編集用の断片パッケージ + install 時 zipapp」へ移行済み。
- **構成**: `tools/kiro-flow/kiro_flow/` に 23 断片（`_head` … `cli`）を置き、`__init__.py` が
  依存順に共有名前空間へ `exec` 合成する（モンキーパッチ・private 参照は単一ファイル時代と同一）。
  リポジトリ内の `kiro-flow.py` は薄い shim。`install.sh` は zipapp で
  `~/.local/bin/kiro-flow`（CLI 呼び出し可能・単一ファイル）を生成し、`executors/` は従来どおり
  prefix 隣へ配置。
- **自己パス**: `self_path()` は shim → zipapp（`sys.argv[0]`）の順で解決（子プロセス起動・再起動・
  executor 検索がパッケージ化後も壊れない）。
- **テスト**: パッケージローダへ追随。419 件パス。zipapp インストールの `--help` も確認。

### kiro-project / kiro-flow: 検証ブランチ取り違え・空パス無限起床・park 再開打ち切り等を修正

- **kiro-project: verify が `kp/<task-id>` ではなく `target`/`base`（main）を clone していた** —
  `task_branch`（既定 on）では worker が成果を `kp/<task-id>` に積む。`_task_verify_cwd` は
  これを無視して MR の target/base を clone しており、journal に `@main のクローン内で検証` と
  出たあと永久 NG（retries 尽きたら blocked）になっていた。`branch` → `target` → `base` の順で
  clone するようにした。
- **kiro-project: `has_work` が依存未達の ready だけで起床し、空パスを無限に回していた** —
  blocked/doing の後ろに `after:` 待ちの ready が並ぶだけで `project_watch` が毎 poll 起きる
  （cycles が数千まで増え journal が秒単位で埋まる）。`ready_after_deps` が空なら起こさず、
  生存 claim の無い stale doing と offloaded/inbox だけ起こす。
- **kiro-project: daemon submit のタイムアウト後に孤児 run を刈らなかった** — `_act_run` と同様に
  `reap_orphan_flow` して二重実行を防ぐ。
- **kiro-project: revise が死んだ owner の claim を TTL だけで「実行中」と誤認していた** —
  `_claim_fresh` を `_claim_alive`（同一ホストは pid 生死）に寄せ、クラッシュ直後でも ready へ
  即積み直す。
- **kiro-flow: 生存 park だけの run が `max_resumes` で orphaned になっていた** — `record_resume` の
  「進捗」が results 数だけだったため、承認待ち（結果が増えない）の健康な run が毎晩の PC 再起動で
  failed に確定していた。生存 `wait_lease` を進捗として数え直す。
- **kiro-flow: `service_waits` がバックオフ中に wait_lease を更新しなかった** — poll を飛ばす枝で
  lease が切れ、監視主体が生きているのに node が pending へ縮退していた。skip 枝でも lease を更新。
- **kiro-flow: claim 敗者がファイルを残し、勝者 release 後に zombie claimed になっていた** —
  git 分散で両者が書けた場合、負けた自分の claim だけ消す（withdraw）。
- **kiro-flow: flock 非対応環境で daemon 二重起動を許していた** — PID 生存チェックで singleton を守る。
- **kiro-flow: 計画（LLM）中に orch heartbeat が止まっていた** — lease 切れ誤 adopt を防ぐため
  計画中も短間隔で heartbeat。
- **kiro-flow gitlab: inherit/revise で run_id が変わるとイシュー二重起票していた** —
  `_task_token` が世代接尾辞（`-rN`/`-vM`）を落として安定化し、open イシューへ再アタッチする。

### kiro-flow gitlab executor: self-host（http/別ポート）で「GitLab API へ接続できません」になるバグを修正

- **症状**: タスクノードが「GitLab API … へ接続できません」で failed になる。エラーに出るパスの
  `/projects/group%2Frepo/...` から「スラッシュのエスケープが原因」に見えるが、`%2F` は GitLab API の
  **正規エンコード**（namespace/repo は URL エンコードして渡す仕様）で無関係。
- **原因**: API の URL を常に `https://<hostname>/api/v4` で組み立てており、起票先 URL（workspace /
  `gitlab.repo_url`）の **scheme（http）とポートを捨てていた**。`http://gitlab.local`（local-gitlab-stack）
  等の self-host では存在しない `https://gitlab.local` へ接続しに行き、接続エラーになっていた。
- **修正**: 起票先 URL の scheme+host(:port) をそのまま API ベースに使う（`http://gitlab.local:8929/...`
  も可）。SSH 形（`git@host:...`）は従来どおり `https://<host>` に既定し、SSH 形しか無く API が
  http/別ポートの構成向けに `gitlab.api_base` の明示キーを追加（最優先）。接続エラーのメッセージは
  パスでなく**完全な URL** を出すようにし、scheme/ポートの取り違えを一目で診断できるようにした。
- **テスト**: ポート保持のパース・http scheme/ポートの保持・`api_base` 最優先・scheme 付きベースの
  URL 組み立て・接続エラーの完全 URL 表示を追加（353 件全パス）。

### リセット後の再起動で残骸 run が復活・類似バックログが重複・kiro-flow プロセス増殖を修正

リセット（charter 以外を全消去）→ kiro-projects 再起動で「似たようなバックログが複数できる」
「kiro-flow のプロセスがバックログ数ぶん立ち上がる」報告への 3 点セットの修正。

- **viewer: リセットで `bus/.state-git`（kiro-flow の同期クローン）を温存** — 従来はバスを
  ディレクトリ丸ごと削除しており、kiro-flow の state_git manifest（`bus/.state-git` 内）が飛ぶと
  次の同期が「リモートだけにある」と判定して**旧 run が全部復活**、daemon の孤児回収が残骸 run を
  一斉再開していた（残骸の正体）。バスは直下の非ドットだけを削除し、クローンを残して run の削除を
  「ローカルの削除」としてリモートへ伝播させる。
- **kiro-projects: 類似バックログの二重投入を修正** — plan/review（バックログ分解）の冪等照合が
  「エージェント委譲**前**のスナップショット」だけと比較していた。分解は数分かかるため、その間に
  投入されたタスク（別インスタンス・前パスの残り・state_git 同期で届いた分・リセット後に書き戻された
  残骸）が照合に無く、類似タスクを重複投入していた。`_enqueue_specs` が投入直前に backlog/archive を
  読み直して照合する。
- **kiro-flow: 同時実行 run（orchestrator プロセス）の上限 `max_runs` を追加（既定 8）** —
  orchestrator は run ごとに 1 プロセスで従来**無制限**。バックログ一括投入（act_async）や再起動直後の
  孤児一斉再開で「run 数ぶんの orchestrator ＋計画エージェント」が同時に立ち上がっていた。
  inbox 受理と孤児再開を「実行中 run 数」で律速する（超過は inbox / 次 poll に残る＝取りこぼさない。
  枠超過の孤児は failed にせず持ち越す）。**全ノードが park（承認待ち等）の run は枠に数えない**
  （worker も計画エージェントも使わないため。gitlab 長期委譲が上限を占有して新規 run を詰まらせない。
  park 孤児は枠と無関係に引き継ぐ＝service_waits の監視オーナーを絶やさない）。0 以下で無制限（従来動作）。
- **テスト**: 全 park 判定（claim 可能/claim 中/lease 失効の縮退）・busy カウント・枠超過孤児の
  持ち越し（failed にしない）・park 孤児の枠免除・無制限モード（kiro-flow）、投入直前読み直しの
  重複検知（backlog/archive・kiro-projects）、バス直下削除と `bus/.state-git` 温存（viewer）。

### kiro-projects-viewer: プロジェクトのリセットボタン（charter 以外を全消去 + kiro-flow 停止）

- **背景**: プロジェクトを「charter からゼロにやり直したい」とき（分解の迷走・実験のやり直し等）、
  backlog / archive / needs / decisions / journal / bus … を手で消して kiro-flow daemon も手で
  止める必要があり、消し漏れ（残った run が結果を書き戻す・古い needs が残る）が起きやすかった。
- **機能**: 概要タブに「危険な操作」カードと「⚠ リセット（charter 以外を全消去 + kiro-flow 停止）」
  ボタンを追加。確認のうえ ①バスの kiro-flow daemon を停止（同一ホストのロック pid へ SIGTERM →
  終了待ち。kiro-flow に stop コマンドは無く SIGTERM が graceful 停止の公式経路。別ホスト稼働は
  停止できない旨を報告）→ ②`charter.md` 以外の全データをゴミ箱へ移動（ゴミ箱の無い環境は完全削除）。
  順序は「停止 → 削除」（先に止めないと worker が消したバスへ結果を書き戻す）。charter が残るため、
  本体（kiro-projects）が稼働中なら次パスで charter から再分解して最初からやり直す。
- **同期との整合**: ドット始まりの同期内部（`.state-git` 等）は温存する。管理クローンの manifest が
  残ることで、削除が state_git の 3-way 同期で「ローカルの削除」としてリモートへ伝播する
  （クローンごと消すと manifest が飛び、次の同期でリモートから全データが復活してしまう）。
  gitAutoPush 有効時は削除を commit/push して即時反映する。
- **ガード**: charter.md が無いプロジェクトでは拒否（残すものが無く、プロジェクト削除になるため
  ボタン自体も出さない）。共有バス構成（バスがプロジェクト外）では daemon 停止が他プロジェクトの
  実行にも影響する旨を確認ダイアログで警告する。削除は 1 件の失敗で止めず、失敗一覧を通知する。
- **テスト**: 削除計画（charter 温存・`.state-git` 温存・`.replan.request` は対象・charter 無しは拒否）、
  実行（全削除・失敗収集）、daemon 停止（冪等・ロック pid への SIGTERM → 終了待ち）を検証
  （`test/reset.test.js`）。

### kiro-projects: `state_git_projects` 宣言プロジェクトが起動時に発見されないバグ等を修正

- **報告バグ（起動漏れ）**: `state_git_projects:` に書いたプロジェクトが、オプションなし起動
  （`kiro-projects` ＝ `run --watch --project all`）や `start` で起動しなかった。プロジェクトの発見が
  `<root>/projects/` の**ディレクトリ走査だけ**で、ローカルにフォルダが無い（＝状態が固有リポジトリ側に
  しかない）宣言済みプロジェクトを拾えず、フォルダは初回同期後にしかできない——という鶏卵で、取り込みも
  駆動も永遠に始まらなかった。`project_dir_names` を「ディレクトリ走査 ∪ `state_git_projects` の宣言」に
  変更し、起動時に宣言プロジェクトを実体化 → 固有リポジトリから取り込み → 駆動まで到達するようにした
  （doctor の kiro-flow daemon 不在チェック・`manage_flow_daemon` の自動起動も宣言プロジェクトに届く）。
- **単発 `run --project all` の取り込み順**: 非 watch でも駆動前にコンテナ同期を 1 回行うようにした
  （watch と同じ配線）。従来は run_loop 内の同期が plan の後になり、リモートにしか無い charter や
  タスクの取り込みが 1 周遅れていた。
- **start/stop/restart が設定ファイルの `root:` を見ない**: `--root` 未指定時の照合 root が常に
  `<cwd>/.kiro-projects` 固定で、設定ファイルで `root:` を変えている構成（state-git サンプル構成）では
  重複起動の検出が効かず（daemon の二重起動を許す）、`kiro-projects stop` も対象を見つけられなかった。
  照合 root を設定ファイルの `root`/`workdir` から解決するようにし、`stop --config` も追加した。
- **設定キーの FS セーフ化不一致**: `state_git_projects` のキーが生のプロジェクト名（例 `web/frontend`）
  のとき、実行時のディレクトリ名（`web_frontend`）と一致せず**黙って**既定 `state_git`（個人リポジトリ）
  へ落ちていた。FS セーフ化したキーでも解決するようにした。
- **テスト**: 宣言のみプロジェクトの発見・`all` センチネル除外・「リモートに状態だけがある状態から
  `run --project all` 一発で実体化 → 消化」・生キー解決・設定ファイル由来の root 照合を追加
  （`TestStateGitPerProject` / `TestLifecycle`）。

### kiro-projects / viewer: charter からバックログを再分解するボタン（エラー回復・done は重複排除）

- **背景**: plan フェーズの失敗・タスクの取りこぼし・誤削除などでバックログが崩れたとき、kiro-projects-viewer
  からは復旧手段が乏しかった。通常の再分解は「消化可能タスクが無い」か「charter が変わった」ときに自動で
  走るが、**charter 無変更のまま**バックログを作り直したいエラー回復ではどちらの条件も満たさず、charter を
  無理に編集する以外に再分解を起こす手立てが無かった。
- **本体（kiro-projects）**: プロジェクト単位（`id` 不要）の指示 `replan` を追加。`commands/<name>.json`
  （`{"command":"replan"}`）ドロップ、または CLI `kiro-projects replan --reason ...` で、次パスに一発だけ
  再分解を要求する（`.replan.request` マーカーを立て、DR を残す）。`cmd_project` の plan ゲートはこの要求が
  あれば **消化可能タスクが残り charter が無変更でも再分解**し、要求は one-shot で消化する。`has_work` が
  マーカーを検知して idle watch を起こす。再分解は既存＋`archive/`（done）タイトルで冪等に重複排除される
  ため、**done と類似のタスクは投入されず「取りこぼした差分」だけ**が入る。charter が無い（backlog ループ）
  プロジェクトでは対象が無いため拒否（`.err` 退避）。
- **ビュアー（kiro-projects-viewer）**: バックログタブに「↻ charter から再分解」ボタンを追加。確認のうえ
  `commands/replan`（稼働中）／CLI `replan`（停止中・失敗時はドロップ退避）で要求を届ける（`actions.requestReplan`）。
  要求中は `readProject` の `replanPending`（`commands/*replan*.json` か `.replan.request` の残存）を見て
  「再分解 取り込み待ち」バッジを出し、ボタンを二重送信防止で無効化する（本体が再分解まで進めると解除）。
  状態（done 等）は書き換えない — done は verify のみが根拠、の不変条件を保つ。
- **テスト**: 本体は再分解要求の強制 plan・done 重複排除・one-shot 消化・charter 無しの拒否を検証
  （`TestProjectLayer`）。ビュアーは `requestReplan` の file/cli/退避経路と `replanRequestPending` の検知を検証
  （`test/replan.test.js`）。

### kiro-flow: failed run を `run --run-id` で再実行できるようにする（失敗ノードを pending へ戻す）

- **背景**: `failed` になった run を `kiro-flow run --run-id <failed>` で再開しても、実際には何も
  再実行されなかった。resume は「既存グラフがあれば計画をやり直さず再開」する設計だが、失敗ノードの
  `results/<id>.json`（status=failed）が残っているため `node_state` が terminal のままで、`all_terminal()` が
  真＝全ノード終端で静止し、`set_status("running")` すら通らずそのまま再度 failed に落ちていた。daemon も
  終端 run は孤児 reclaim しない（無限リトライ防止）ため、failed run は事実上どの経路でも再実行できなかった。
- **修正**: `Bus.retry_failed()` を追加し、`cmd_run` が既存 run-id の status が `failed` のときに呼ぶ。
  失敗ノードの result と claim を消して **pending へ戻し**（＝再 claim・再実行の対象化）、確定済み `done`
  ノードは温存する（続きからやり直す）。併せて meta の終端/孤児簿記（`failure_reason` / `superseded` /
  `resume_count` 等）を掃除して status を `running` に戻す。以降の resume ループが失敗ノードだけを再実行する。
- **尊重**: `done`（正常完了）と `canceled`（人の明示停止）は終端として扱い再実行しない。retry は
  `failed` に対する人/消費者の明示操作でのみ行う（daemon の自動リトライは従来どおり無し＝暴走防止）。
  結果未書き込みのまま failed になった（orchestrator クラッシュ等の）pending ノードも再開対象に含まれる。
- **テスト**: 失敗ノードの pending 復帰・done 温存・簿記掃除・結果未書き込みノードの再開を検証
  （`RetryFailedRunTests`）。

### kiro-projects: charter.md の変更を backlog に反映（消化可能タスクがあっても再計画）

- **背景**: kiro-projects-viewer 等で charter.md を編集して保存しても、backlog（タスク）が変わらない
  ことがあった。`cmd_project` の plan は「消化可能タスクが無いときだけ」目標から backlog を起こす設計
  （毎サイクルの再分解を避けるため）だったので、**既にタスクがあるプロジェクトでは charter を編集しても
  再計画されず**、charter の変更が後段（backlog）に反映されなかった。watch ループは charter の mtime 変化で
  プロジェクトを駆動するものの、`cmd_project` 側の plan ゲートで止まっていた。
- **修正**: charter の「分解に効く内容（目標/repos/リンク/制約/前提/成果物）」の**安定した内容署名**
  （`_charter_plan_signature`）を project state に記録し、次回 run で署名が変わっていれば**消化可能タスクが
  残っていても再計画**して差分を投入する。再計画は既存/archive タイトルで冪等に重複排除されるため、
  既存タスクを二重投入せず「charter 差分が生む新規タスク」だけが入る。
- **予防（誤検知しない）**: mtime ではなく内容ベースの署名なので、state_git 同期やファイルコピーで mtime
  だけ変わっても再計画は誘発しない。acceptance だけの変更も分解入力ではないので再計画しない（done 判定は
  評価側で反映される）。署名未記録（既存プロジェクト/初回）はベースラインを張るだけで、次回以降の編集から検知する。
- **テスト**: 内容署名の安定性・charter 変更での再計画（消化可能タスクがあっても）・acceptance のみ編集では
  再計画しないことを検証（`TestProjectLayer`）。

### kiro-flow: `gc` が孤児 inbox 要求を掃除（不要 run の再起動を止める）

- **背景**: `gc` は古い run を消すとき対応する inbox 要求・claim も併せて消す（`remove_run`）が、
  **run を伴わない inbox 要求は掃除対象になっていなかった**。デーモンの受理ゲートは `run_exists` のみで
  判定するため、run が消えて inbox だけ残った要求は「新規要求」に見え、**毎 poll で再 claim → orchestrator
  起動 → 不要な run が走る**。旧バージョンや外部ツールが run だけ削除した／crash で `remove_run` が
  途中終了した等で、こうした孤児 inbox が取り残されると再実行が止まらなかった。
- **修正**: `gc` に「孤児 inbox 要求の掃除」を追加。`run_exists` が偽で、`--older-than` より古く、かつ
  現在 claim されていない（lease 内で担当 daemon が居ない）inbox 要求を `remove_run` で掃除する
  （claim/cancel マーカーも一緒に消える）。
- **保護（誤削除しない）**: フレッシュな未受理要求（`--older-than` 未満）は正規の受理待ちとして残す。
  lease 内で claim 中の要求（run 生成前でも処理中）は触らない。`--status` 指定時は「run の status で
  絞る」意図なので孤児 inbox には手を出さない。`--dry-run` で対象を確認できる。
- **テスト**: 孤児掃除・フレッシュ保護・claim 中保護・`--status` 非対象・`--dry-run` プレビュー・
  run を持つ inbox の従来通りの掃除を検証（`GcOrphanInboxTests`）。

### kiro-flow: 電源断で空になった git オブジェクトへの耐性（durable write ＋ 自己修復）

- **背景**: PC の定期シャットダウン/電源断が git の書き込み途中に起きると、loose object が
  **サイズ 0** で残る（git は既定で「一時ファイル→ rename」する際にオブジェクト *中身の fsync を
  しない*ため、rename のメタデータだけがジャーナルで残り中身が未フラッシュになる）。症状は
  `error: object file .git/objects/xx/yy… is empty` で、以後 add/commit/push/checkout/pull が全滅し、
  git バス（`--git`）／状態鏡（`state_git`）が**同期できない**状態に陥っていた。既存の自己回復は
  ロック残骸・中断 rebase だけを対象にしており、空オブジェクトは検知も修復もできなかった。
- **予防（durable write）**: kiro-flow が管理するクローンと、リモートがローカルパスの共有リポジトリ
  本体（push を受ける `receive-pack` 側）に `core.fsync=all` / `core.fsyncMethod=batch` を冪等に設定し、
  rename 前にオブジェクト内容を fsync させる（`batch` により tiny JSON の書き込みでも安価）。古い git が
  値を知らなくても無害（未知トークンは無視される）。URL 越しのサーバ本体は手動設定を README に明記。
- **自己修復**: 壊れたクローンを `git fsck --connectivity-only` の軽量プローブで検知し、捨ててリモート
  （真実）から作り直す。**クローン再利用時（起動時）** に加え、**`sync_push`/`sync_pull` 実行中**に破損が
  露見した場合も同様に作り直して続行する。未 push の作業は孤児 reclaim が続きから再実行するため
  情報は失われない。同じ耐性を `state_git`（`StateGit`）にも適用（manifest を失っても 3-way が再収束）。
- **リモート本体破損の明示**: 共有リポジトリ本体自体が壊れて clone/fetch が失敗する場合（作り直しでは
  直らない）は「リモート破損の可能性」を明示した `RuntimeError` で中断し、無限の再クローンループを避ける。
  復旧手順（健全クローンからの補填・`push --mirror`・リモート側の `core.fsync` 設定）を README に追記。
- **テスト**: サイズ 0 オブジェクトを注入する障害注入で、GitBus/StateGit の「予防設定の適用」「再利用時の
  作り直し」「`sync_push`/`state_sync` 実行中の自己修復」「リモート破損時の明示中断」「破損メッセージの
  分類（一過性エラーとの切り分け）」を検証（`GitDistributedTests` / `StateGitSyncTests`）。

### kiro-flow: gitlab 委譲の承認待ちを worker スロットから切り離す（park & poll）＋ `cancel` ＋ 同時イシュー上限

- **背景**: `--executor gitlab` は各タスクを GitLab イシューにして委譲し、決着（MR 全マージ＝承認／
  未マージクローズ＝却下）まで **worker を同期ブロックしてポーリング**していた。イシューが人の承認待ちで
  滞留すると、その worker が `max_workers` の 1 枠を数日占有し続け、**claim 可能タスクがあっても発行が
  止まる**。かといって `max_workers` を上げると常駐プロセスと GitLab ポーリングの多重で PC/サーバ負荷が
  増える。kiro-projects からの daemon 起動を主眼に、負荷を抑えつつ設計思想（ファイルのみのバス・
  オンデマンド worker・分散・クラッシュ耐性）を保ったまま改善した。
- **park & poll**: executor は決着していないとき `DeferDecision` を投げ、worker は終端 result を書かずに
  ノードを **park**（`runs/<run>/waits/<node>.json` に退避）して claim を解放する（`node_state` は新状態
  `waiting`）。承認待ちは監視主体（daemon / 単発 run）の `service_waits` が `watch_interval`（既定 90 秒）毎に
  **まとめて再確認** し、決着したら終端 result を直接書く。gitlab は承認時にローカル workspace を finalize
  する必要がない（成果はマージ済み MR にある）ため、監視主体が worker/clone 無しで結果を材料化できる。
  → **ブロック worker N 台 ×(1/30s)** を **監視 1 本 ×(1/watch_interval) のバッチ**へ畳み、スロット占有と
  多重ポーリングの二重負荷を同時に解消。`max_workers` は小さいまま据え置ける。
- **分散時の公平な分担**: git バス分散では、起票は既存の per-node claim で全 PC に公平分散し、監視は
  各 run の **駆動オーナー daemon 1 台に分担**する（`service_waits` は「自分が orchestrator を回している run」
  だけを見る）。これで N 台が全 park を **重複ポーリングしない**（run が各 PC に分散する分だけ監視も分散）。
  オーナー消失時は孤児 reclaim が run（＝監視）を別 PC へ移すので取りこぼさない。
- **耐性（維持・強化）**: park 記録はバス上で **git 同期し daemon 消失を跨いで生存**——次に起きた daemon が
  引き継いで再確認する（孤児 run reclaim と同じモデル）。`waits/` は claim と同じ **lease セマンティクス**に
  相乗りし、`wait_lease` 失効時は `node_state` が **`pending` へ縮退**して full worker が **冪等な再アタッチ**
  （同一トークンの既存 open イシューに再接続）で拾い直す——park を行き止まりにしない。イシュー削除（404）・
  外部クローズ・却下 data はブロック版と同じ関数を共有し、確認する場所が worker か監視主体かの違いだけ。
  監視主体の無い単発 `work` 実行は環境変数で deferral 無効となり **従来どおりブロック待機へフォールバック**
  （後方互換）。deferral は起動モードに依らず「監視主体が居れば効く」汎用機構で、`poll()`/`on_cancel()` は
  executor プラグイン契約の**任意拡張**（gitlab が最初の利用者。kiro/stub 等は従来どおりブロック）。
- **`cancel`（run スコープの恒久停止）**: 終端 status に **`canceled`** を追加し、`kiro-flow cancel <run-id>` を
  新設。cancel マーカーを inbox に置いて git 同期で全 PC / daemon へ伝え、run が存在すれば即 `canceled` に
  終端化する。監視主体は **新規起票・park の再ポーリング・孤児 resume を同時停止**（`canceled` は終端なので
  `active_runs` から外れ reclaim 対象にもならない）。orchestrator も要所で cancel を確認し、`running` への
  上書きで復活しない。`--close-issues` で起票済みイシューに取消コメントを付けてクローズ（既定は残す）。
  承認待ちで park 中の run も暴走中の run も止められる、人の明示指示による唯一の hard-stop。
- **同時イシュー上限（バックプレッシャ）**: `gitlab.max_open_issues`（0=無制限）で「同時に開ける未決着
  イシュー数」を絞れる。上限で **起票を一時停止**（**エラーにしない**。枠が空けば `service_waits` が自動で
  起票再開）。既存の再タスク打ち切り（`--max-retries` は `return "done"`）と同じ「これ以上作らない」思想の
  延長で、run を落とさず人のレビュー速度に発行をペーシングする。
- **従来モードへ戻す設定**: `gitlab.defer_waits`（既定 true）を追加。`false` で park & poll を無効化し、
  従来モード（worker がイシューを監視してブロック待機。1 worker=1 イシュー）に戻す。daemon/run が
  この設定で worker への環境変数 `KIRO_FLOW_DEFER_WAITS` を出し分け、`service_waits` も出番が無くなる。
- **設定整合**: gitlab executor プラグインの `_DEFAULTS` と本体 `CONFIG_DEFAULTS` の `timeout` 不一致
  （後者だけ 86400）を是正し、`timeout: 604800` / `approved_timeout: 1209600` を揃えた。`watch_interval` /
  `max_open_issues` / `defer_waits` を `gitlab:` ブロックに追加。README / `kiro-flow.yaml.example` を更新、
  テストを追加（waiting 状態・service_waits の決着/据え置き/締切/throttle/defer 無効・cancel・
  gitlab の DeferDecision/poll/on_cancel）。

### kiro-projects-viewer: park & poll の可視化 ＋ run キャンセル操作 ＋ canceled 終端対応

- 上記 kiro-flow の park & poll / cancel をビュアーから扱えるようにした。ビュアーは引き続き基本
  読み取り専用だが、run ライフサイクル操作（既存の再投入・削除）と同じ流儀で cancel を追加した。
- **park（承認待ち）の可視化**: `flow.js` が `runs/<run>/waits/<node>.json` を読み、生存 lease を持つ
  ノードを「**承認待ち（parked）**」状態として導出（オレンジのノード色＋レビュー中アイコン）。同時
  イシュー上限での「起票見送り（throttle）」も区別。lease 失効は pending へ縮退＝本体と同じ。ノード
  詳細に park の説明行（レビュー/MR 作成待ち・人の作業検知・throttle）とチップを表示。
- **run キャンセル**: run 詳細に「■ キャンセル」ボタン（非終端 run のみ）。`inbox/cancels/<run-id>.json`
  にマーカーを置き（git 同期で他 PC / daemon へ伝わる）、`meta.json` を canceled に確定、`waits/` を
  掃除して監視の再ポーリングを止める（kiro-flow の cmd_cancel と同形）。承認待ちで park 中でも暴走中でも
  止められる。起票済みイシューは残す（この viewer の GitLab クライアントは読み取り専用のため、クローズは
  daemon の `cancel --close-issues` か gitlab-review-viewer に委ねる）。
- **canceled 終端対応**: `flow.js` の `TERMINAL` に `canceled` を追加（canceled run を「応答なし/実行中」に
  誤分類しない）。run 削除の対象にも canceled を含め、status チップ／グラフ色を追加。テスト 7 件を追加。

### kiro-projects: kiro-flow の設定は `flow_config`（--config）に集約（個別注入をやめる・`flow_state_subdir` 廃止）

- 方針: kiro-projects 側に kiro-flow の設定値を1つずつ増やさない。kiro-flow の設定（`executor` /
  `state_git_subdir` / `gitlab.*` / `defer_waits` 等）は **`flow_config` で渡す kiro-flow.yaml に集約**し、
  daemon 起動時に `--config` で渡して kiro-flow に読ませる。kiro-projects が CLI 注入するのは、
  「どのバスをどのリポジトリへ鏡写しするか」の **per-project routing**（`--state-git` の remote /
  branch / interval）だけ——これは `state_git_projects` から導出する kiro-projects の役割。
- そのため、先に追加した `flow_state_subdir`（`--state-git-subdir` を個別 CLI 注入する設定）を **廃止**した。
  state_git サブディレクトリを変えたいときは kiro-flow.yaml の `state_git_subdir` を設定する（既定
  `kiro-flow`。CLI 注入しなくなったので上書きされず、そのまま効く）。viewer の `flowBusByProject`
  （`<clone>/<subdir>`）も合わせる。README / `kiro-projects.yaml.example` を更新、テストを更新。

### kiro-projects-viewer: タスクグラフノードのイシュー状態を自動表示（クリック不要）

- **背景**: 関連イシューの「今」の状態は GitLab API 由来のため、従来は「⟳ GitLab と突き合わせ」
  ボタンを押さないとノードに出なかった（グラフ状態は bus のファイルだけから作るため）。
- **自動突き合わせ**: run を開いたとき／ポーリング更新時に、GitLab 設定済みなら**一度だけ自動で
  突き合わせ**る（同一 run は **60 秒の律速**でキャッシュを使い、ポーリング毎回は叩かない）。
  結果は **run 単位でキャッシュ**し、run を切り替えても保持する（再取得を避ける）。
- **オープン中イシューも表示**: 突き合わせ結果にクローズ済みだけでなく**オープン中（レビュー待ち）**の
  イシューも含め、ノードに「レビュー中」チップとイシューアイコン（青系）を出す。クローズ済みは
  従来どおり完了/失敗を先読み反映（承認/却下）。ノード詳細のチップも 却下／承認／レビュー中／
  クローズ を明示的に色分けする。
- 手動ボタンは「⟳ GitLab 最新化」に改称（自動取得の即時再取得用）。追加の API 呼び出しは
  非終端ノードのみ・最大 40 件・直列・60 秒律速で有界。

### kiro-projects-viewer: 状態共有 git への push が黙ってスキップされる問題を可視化

- **バグ修正**: ユーザー操作の状態共有 git 反映（`gitAutoPush`）が、操作したディレクトリが
  **git 作業ツリーでない**と `commitPush` の `notRepo` で**黙ってスキップ**され、変更が共有
  リポジトリへ反映されないのに何も知らされなかった。最初に run（バス）削除で表面化したが、
  **バックログ修正・タスク操作・needs 記入など `p.dir` への操作も同じ**で、本体の state_git が
  「作業ディレクトリ→別クローン」方式で同期する構成では作業ディレクトリ自体が git リポジトリでない
  ため、viewer からは直接 push できず daemon 側の state_git 同期に委ねられる（バスは
  `_STATE_EXCLUDE_DIRS = {"bus","claims"}` で本体 state_git から除外され、kiro-flow 側が別クローンへ
  同期）。
- **対応**: `notRepo` スキップの検知を `gitPushAfterWrite` に集約し、**全操作**で「共有リポジトリへ
  直接反映できなかった／daemon の state_git 同期に委ねられる／viewer から直接反映するには git
  クローン上でプロジェクト（バスは `flowBusByProject`）を開く」ことをトーストで明示する（**沈黙の
  no-op をなくす**）。通知は**ディレクトリごとに一度だけ**（操作のたびには出さない）。git 追跡下の
  作業ツリー（pure-remote 構成・`flowBusByProject` の `<clone>/kiro-flow`）では従来どおり
  コミット・push される。
- `gitPushAfterWrite` は commitPush の結果 Promise を返すようにした（従来の fire-and-forget
  呼び出しは戻り値を無視するだけで挙動不変）。バス操作は `gitPushBusOp`（`kind:'bus'` でヒント切替）。

### kiro-projects-viewer: gitlab executor のクローズ済みイシューをタスクグラフへ反映

- **バグ修正**: gitlab executor の場合、関連イシューが GitLab で既にクローズ（承認/却下で決着）
  されていても、worker が決着ループでそれを検知して `result` を bus に書くまでは、タスクグラフの
  ノードが「実行中」のまま完了表示にできなかった。非ブロッキング委譲（`act_async`）＋PC の日次停止
  などで worker が止まっている間に人がイシューを承認クローズするケースで顕著。
- **対応**: RUN 概要に **「⟳ GitLab と突き合わせ」** を追加。その run の非終端ノードの関連イシュー
  （本文の決定的タスクトークンで検索）を GitLab の「今」の状態と突き合わせ、クローズ済みなら
  **executor と同一規則**（関連 MR の状態 → `status:approved`/`status:done` ラベル → 人コメントの
  承認/却下語。手掛かり無しのクローズは取り下げ＝却下）で承認/却下を判定し、ノードを
  **完了/失敗として先読み反映**する。判定ロジックは `flow.js` の純関数
  `reconcileNodeState`（executors/gitlab.py の `_mr_decision` / `_closed_issue_decision` /
  `_decision_from_comments` と一致）に切り出し、単体テスト（`test/flow-reconcile.test.js`）で固定。
- **表示**: 反映されたノードはタスクグラフで**破線枠**、ノード詳細で「GitLab 反映」チップと注記で
  区別する（bus が常に正で、反映は暫定表示。bus に `result` が届けば通常表示へ確定）。反映で URL が
  判明したノードには、グラフのイシューアイコン（1クリックでレビュー起動）も出るようにした。

### kiro-projects-viewer: 非ブロッキング委譲（`offloaded`）の表示対応

- **バグ修正**: パーサの既知ステータス一覧に `offloaded` が無く、offloaded タスクが既定 `inbox` に
  化けていた（`TASK_STATUSES` に追加）。
- **表示整合**: 概要タブのステータスタイル（`STATUS_ORDER`）・バックログのフィルタ（`BACKLOG_FILTERS`）に
  `offloaded` を追加。status-chip / tile に `.st-offloaded` 色（doing と同系＝機械稼働中）を追加。
- **run 連携**: offloaded タスクは `flow_run`（委譲先 run-id）を持つので、バックログ行に「▶ run」バッジ、
  タスク詳細の `flow_run` をクリックでフロータブの該当 run へ移動できるようにした。extras に
  「委譲実行中: <loc>」を表示。revise ダイアログに offloaded 用の注記（反映は run 完了時）を追加。

### kiro-projects: 非ブロッキング委譲（`act_async`）— gitlab 長期委譲でループを塞がない

- **背景**: `executor: gitlab` は MR 承認まで数日かかる。従来は act が結果を待つ（ブロック）ため、
  `act_timeout`（既定 30 分）が承認より先に切れて「タイムアウト→retry」を繰り返し、他タスクも
  待たされていた。専用 daemon が run を保持するようになったので、**待たずに次へ進める**ようにした。
- **`act_async`（opt-in）**: daemon/remote への submit で**結果を待たず**タスクを新状態 `offloaded` に退避し、
  次パスで `kiro-flow result` を1回だけポーリングして**終端した run だけ settle**する（未終端は次パスへ）。
  ループを塞がないので、同じプロジェクトの他タスクや他プロジェクトを並行に進められる。run の本当の
  失敗（却下・orchestrator 異常）は終端ステータスで検知されるため、待ち上限（タイムアウト）を安全網に
  する必要がない＝`act_timeout: 0` ＋ kiro-flow `gitlab.timeout/approved_timeout: 0` と併用で
  **誤タイムアウト由来の retry ループが完全に消える**。
- submit は決定的 run_id なので、`offloaded` のまま kiro-projects が再起動しても同じ run に再合流する
  （二重実行・イシュー二重起票なし）。`offloaded` は watch を起こし続け（ポーリング継続）、CONSUMABLE
  ではない（再 submit しない）。既定 off＝**完全後方互換**（従来どおり同期で待つ）。
- CLI `--act-async`、設定 `act_async`。テストと `*.yaml.example`（gitlab 委譲サンプル）を更新。

### kiro-projects / viewer: プロジェクト単位で保存先リポジトリを分ける（`state_git_projects`）

- **背景・目的**: これまで状態の git 同期（`state_git`）は**コンテナ丸ごと**（全プロジェクト）を 1
  リポジトリへ同期していた。プロジェクトごとに**別々のリポジトリ**へ分け、プロジェクト固有リポジトリで
  kiro-projects / kiro-flow の情報をメンバーと共有し、誰でも kiro-projects-viewer でドライブできるように
  する。`default` はユーザー個人リポジトリで管理し、他プロジェクトはプロジェクト固有リポジトリで共有・
  可視化する構成。**使う人ごとにアサインされるプロジェクトが違う点は、各自の設定で写像を変えるだけ**で
  吸収できる（リポジトリの設定で解決）。
- **kiro-projects の状態**: 設定 `state_git_projects`（`{プロジェクト名: URL/パス}` または
  `{名前: {remote/branch/subdir/interval}}`）を追加。写像に載ったプロジェクトは**そのプロジェクトの
  subtree だけ**をスコープして固有リポジトリ（`<subdir>/projects/<name>/…`。従来レイアウトを維持）へ
  同期し、未記載（`default` 含む）は既定の `state_git`（個人リポジトリ・未設定なら無効）へ落ちる。
  各プロジェクトは自分専用の管理クローン（`<container>/projects/<name>/.state-git`）を使い、多重
  コミッタの護りはそのまま。写像未設定なら従来どおりコンテナ丸ごと（**完全後方互換**）。
- **実行層 kiro-flow の run（kiro-flow は無改修）**: kiro-flow に「プロジェクト」の概念は持ち込まない。
  代わりに **kiro-projects が per-project の kiro-flow daemon を起動・監視**し、「このバスを、このプロジェクト
  のリポジトリの `kiro-flow` 名前空間へ鏡写しせよ」を**daemon 起動時の CLI（`--bus`/`--state-git*`）で
  注入**する（kiro-flow 側の設定ファイルや宣言ファイルは不要）。設定 `manage_flow_daemon: true`（opt-in）で
  watch ループが各プロジェクトの daemon を不在なら起動（バスロックで冪等）、`flow_max_workers` をマシン
  全体の予算として対象プロジェクト数で割り各 daemon の上限にする。`flow_config` で共有 kiro-flow.yaml を
  `--config` として渡せる。**kiro-projects を止めても daemon は detached で残る**ので、in-flight run
  （gitlab の長期委譲・夜間停止からの孤児再開）は daemon 側でそのまま継続し、再起動時はロックで再検知して
  二重起動しない。`doctor` は各プロジェクトバスに daemon がいるかを warn で点検する。プロジェクト固有
  リポジトリは `kiro-projects/projects/<name>/`（状態）と `kiro-flow/`（run）の 2 名前空間を持つ。
- **kiro-projects-viewer**: コンテナ（`roots`）は従来から複数登録できるため、プロジェクト固有リポジトリの
  clone `<clone>/kiro-projects` を 1 行ずつ足すだけで全プロジェクトを 1 画面に束ねられる。フローバスは
  設定 `flowBusByProject`（⚙「プロジェクト単位バス」・`プロジェクト名 = <clone>/kiro-flow`）を追加し、
  pure-remote 監視でプロジェクトごとの kiro-flow clone を割り当てられるようにした（ローカル `<project>/bus`
  に `runs/` があればそちらを優先）。
- **テスト・ドキュメント**: kiro-projects の per-project 同期・裁定、kiro-flow daemon の起動コマンド注入・
  冪等・予算分配・doctor 点検、viewer のバス解決テストを追加。README と `*.yaml.example` に構成方法を追記。
  既存の 1 リポジトリ複数プロジェクト構成からの**移行手順書**
  [`docs/guides/migrate-per-project-repos.md`](docs/guides/migrate-per-project-repos.md) を追加。

### kiro-projects-viewer: バックログ操作の明確化（ボタン名・UI）と revise の柔軟化

- **背景**: 「＋ タスクを追加」が**バックログを 1 件追加する**機能だと UI から分かりにくかった
  （実体は inbox に 1 件投入 → 本体が次サイクルで `backlog/<id>.md` にする）。現状の設計思想
  （**公式契約だけを使い、タスク状態＝done は直接書かない**）は崩さず、名前と UI を分かりやすくした
- **ボタン名・UI の明確化**: 「＋ タスクを追加」→「**＋ バックログに追加**」に改称し、ダイアログ見出しも
  「バックログにタスクを 1 件追加（inbox 経由）」に。バックログタブに折りたたみヘルプ
  「バックログの変え方」を追加し、**追加＝inbox／変更＝revise／タスクグラフ再構築＝revise**、いずれも
  状態（done 等）は直接書き換えない、という関係を一貫して示す
- **revise の柔軟化（既存バックログの更新）**: 修正フォームに **note / level / track** を追加
  （kiro-projects の `REVISE_FIELDS` 全項目に対応）。依存 **after** の編集は従来どおり本体側が DAG 循環を拒否
- **タスクグラフ再構築の明示**: revise は本体が取り込むと `rev` を上げて kiro-flow に**新しいタスク
  グラフ（run の DAG）**を作らせる（実行中タスクは現在の試行を破棄して積み直し）ことを、修正フォームに明記
- **実装**: renderer の UI 文言・revise フォームのみの変更。**main 側の契約・kiro-projects 本体は変更なし**
  （追加は既存の `inbox` 投入、更新は既存の `commands/` revise のまま）

### kiro-projects-viewer / gitlab-review-viewer: 起動済み portable exe への即時ハンドオフ（連携起動の高速化）

- **症状**: kiro-projects-viewer（portable exe）の「レビューで開く」で `exe` モードを使うと、
  gitlab-review-viewer（portable exe）が**既に起動していても**引き継ぎ表示までに数秒かかる
- **原因**: portable exe を argv 付きで再起動すると、起動済みでも OS が毎回「自己展開（一時
  ディレクトリ）→ Electron 起動 → single-instance で argv 転送 → 即終了」の 2 個目プロセス
  立ち上げコストを必ず払う。argv 転送自体は機能するが、その前段の自己展開が遅い
- **変更**: gitlab-review-viewer が起動時に**ローカル IPC エンドポイント**（Windows: 名前付き
  パイプ／その他: Unix ドメインソケット。username から決定的に導出＝ユーザーごとに分離）を開き、
  `gitlab-review-viewer://…` を 1 行受け取ると `second-instance` と同じく対象を開く
  （`src/main/handoff.js`）。kiro-projects-viewer の `exe` モードは exe を spawn する前にこの
  エンドポイントへ接続を試み、**届けば URL を送るだけで即ハンドオフ**（exe を再起動しない・
  トーストは「起動中の gitlab-review-viewer に引き継ぎました」）。未起動＝接続失敗のときだけ
  従来どおり exe を起動する（cold start のときにだけ自己展開コストを払う）
- **後方互換 / 安全性**: 設定不要・自動。エンドポイント非対応の古い gitlab-review-viewer が
  相手でも接続に失敗して従来の argv 起動へ素通りする。ローカルユーザー限定ソケットで、扱う URL は
  `gitlab-review-viewer://` のみ（既存の argv / protocol 経路と同じ信頼境界）。アプリ終了時に閉じる
- **実装**: gitlab-review-viewer に `src/main/handoff.js`（サーバ）を追加し main で起動/停止。
  kiro-projects-viewer に electron 非依存の `src/main/reviewHandoff.js`（クライアント）を追加し
  `review.js` の `exe` モードから利用。両側のエンドポイント導出一致と往復を検証する
  `test/review-handoff.test.js`（クライアントとサーバを実ソケットでつなぐ）を追加

### kiro-projects-viewer: プロジェクトの新規作成・上位入力ファイルの編集・archive タスクの再投入

- **背景**: これまでビュアーは既存プロジェクトの**閲覧**と、公式契約経由の人アクション
  （needs 記入・inbox 投入・commands 指示）に限られ、プロジェクトの**立ち上げ**や
  charter の**編集**、誤 done の**復帰**はアプリ外（エディタ・CLI）で行う必要があった
- **追加**: 3 つのオーサリング機能を、いずれも「人が書く入力だけを書き、タスク状態
  （done の不変条件）は触らない」原則を守って実装した
  - **＋ 新規プロジェクト**（サイドバー ＋・空状態にも導線）: フォーム（goal /
    constraints / deliverables / acceptance / repos）から `<root>/projects/<name>/charter.md` を
    生成し、repos があれば `repos.json`（kiro-projects の `export_repo_registry` と同一の
    `_meta.generated_from` 付き・キーソート）も作る。作成後はコンテナを設定 roots へ登録して
    発見対象にし、そのプロジェクトを選択する。backlog 生成は従来どおり本体の run が行う
  - **✎ プロジェクトファイル編集**（概要タブ）: `charter.md` / `policy.md` / `repos.json` を
    アプリ内で直接編集。保存すると次の run で後段（backlog 生成・ルーティング）に反映される。
    自動生成 repos.json（`_meta`）は「run 時に charter で上書きされる」旨を警告し、JSON は
    保存前に構文検証する。編集対象はホワイトリスト（人が書く上位入力）に限定
  - **↻ revise して再投入**（タスク詳細・archive のみ）: archive（done）タスクの内容を
    prefill した投入フォームを開き、編集して inbox へ**新しいタスク**として投入する
    （triage→verify を通す＝done を取り直す。archive の記録は残す）。誤 done などの
    エラー復帰用途。inbox 投入フォームには id / after 欄を追加した
- **実装**: `src/main/authoring.js`（charter 雛形生成・repos.json 生成・作成・
  ホワイトリスト読み書き）を追加し、IPC（`kiro:createProject` / `kiro:readFile` /
  `kiro:writeFile`）と `window.api` に公開。archive 再投入は既存の inbox 契約
  （`actions.enqueueToInbox`）を流用。`test/authoring.test.js` を追加
- **リモート連携（state_git 経由のファイルドロップ）**: 3 操作はすべて既存の状態共有 git
  （⚙ 設定「操作を都度コミットしてプッシュ」）に乗る — 編集/投入したディレクトリを
  pathspec 限定でコミット＆プッシュし、リモートの kiro-projects が state_git 同期で取り込む。
  charter.md / policy.md / inbox は既に「人の入力＝リモート優先」で裁定され、新規プロジェクトは
  ディレクトリ丸ごとの追加として同期され、`--project all` 常駐が watch ループで新規発見して回す。
  これに合わせ kiro-projects 側の同時変更裁定に **`repos.{json,yaml,yml}` をリモート優先**へ追加
  （手書きレジストリの viewer 編集を取りこぼさない。自動生成 repos.json は次 run が charter から
  再生成するので charter が正のまま）。`TestStateGitSync.test_conflict_repos_registry_prefers_remote` を追加

### kiro-flow: 孤児 run の resume で orchestrator が usage エラーで即死する不具合を修正

- **症状**: daemon が孤児 run を「同じ run-id で再開」した直後に
  `usage: kiro-flow [-h] …` とオプション不正のようなログを出して orchestrator が
  即終了し、引き継ぎ（resume）が静かに失敗していた
- **原因**: `_spawn_orchestrator` が組み立てる子プロセス argv で、`--inherit-from`
  （`orchestrate` サブコマンドの引数）を `orchestrate` トークン**より前**に置いていた。
  グローバル引数として親パーサに拾われ、`argument cmd: invalid choice` で exit 2 になっていた
  （`--inherit-from` を持つ＝リトライ引き継ぎ由来の run を resume したときに発現）
- **修正**: `cmd_run` の起動と同じく `--inherit-from` を `orchestrate` の**後ろ**へ移動。
  子プロセス argv が実 CLI パーサでそのまま parse できることを検証する回帰テスト
  （`SpawnArgvTests`）を追加し、パーサ構築を `build_parser()` として切り出して共有

### gitlab-review-viewer / kiro-projects-viewer: exe アイコンを追加

- これまで未設定（Electron既定のアイコン）だった Windows exe / ウィンドウの
  アイコンを設定。[Fluent UI System Icons](https://github.com/microsoft/fluentui-system-icons)
  （Microsoft・MIT license）のグリフに角丸カラー背景を合成して `assets/icon.ico`
  として生成し、`electron-builder` の `build.win.icon` と `BrowserWindow` の
  `icon` オプション（開発起動時用）の両方から参照する
  - gitlab-review-viewer: `clipboard_checkmark`（レビュー承認）＋ 赤系背景
  - kiro-projects-viewer: `board`（ダッシュボード）＋ 青系背景

### kiro-projects-viewer: タスクグラフの gitlab ノードにイシューアイコン（1 クリックでレビュー起動）

- **背景**: これまでタスクグラフのノードをクリックすると詳細パネルが開くだけで、関連 GitLab
  イシューを開くには詳細内の「レビューで開く」を**もう一度**押す 2 ステップだった
- **変更**: gitlab executor 由来で**関連イシュー URL が確定済み**のノードには、右上に小さな
  イシューアイコン（↗）を重ね、**1 クリックで gitlab-review-viewer を起動**する（`api.openReview`）。
  ノード選択（詳細表示）とは伝播を分離し、アイコンはイシュー起動を優先。却下ノードは赤で示す。
  実行中で URL 未確定のノードは対象外（従来どおり詳細パネルの「関連イシューを探す」が担当）

### kiro-projects: 優先順位付けでタスク 0/1 件のとき LLM 呼び出しをスキップ

- **背景**: `prioritize`（planner=kiro）は ready なタスクを kiro-cli（LLM）に並べ替えさせるが、
  対象が 0 件または 1 件のときは**並べ替えの余地が無く順序が自明**なのに、毎サイクル kiro-cli を
  起動していた（コスト・レイテンシの無駄）
- **変更**: `prioritize` は `len(ready) <= 1` のとき planner を問わず LLM を呼ばず決定的順序
  （priority＋古さ）にする。LLM 境界の `rank_agent` も 0/1 件は入力をそのまま返して短絡する。
  policy（pin/defer）は 1 件でも後段で必ず効く
- テスト: `test_rank_agent_skips_llm_for_zero_or_one` / `test_prioritize_skips_llm_for_single_task`

### kiro-projects-viewer: charter → backlog → run → issue の関係性を可視化・相互遷移

- **背景**: 従来はタブ（概要/バックログ/要対応/フロー/レビュー/履歴）が独立し、**バックログのタスクと
  kiro-flow の run（＝GitLab イシュー）を結ぶリンクが UI に無かった**。run-id はただの文字列として
  表示され、リトライ（`…-r0`/`…-r1`）も個別の run として並ぶだけだった
- **run-id の解析**（`flow.js` `parseRunId`）: 決定的 run-id `req-<hash>-<taskid>-r<retries>[-v<rev>]` を
  `taskId`/`retries`/`rev`/`lineageId`（同一タスクの系統キー）に分解し、`readRun` が surface する。
  `meta.inherited_from`（`--inherit-from` の引き継ぎ元）も返す
- **リトライを束ねる**: フロー一覧を系統（同一タスク）でまとめ、最新試行を見出しに過去試行を色付き
  ピル（`r0`/`r1`…）で畳む。「意味的に同一のオブジェクトはまとめる」を実装
- **パンくずと相互遷移**: タスクダイアログ・run 詳細に `🎯 charter ▸ 🗒 task ▸ ⚙ run ▸ 🔗 issue` の
  クリック可能なパンくずを追加。バックログ行の `⚙N` バッジ→フロー、フロー一覧の `🗒 taskid`→
  バックログ、issue→GitLab へワンクリック遷移（`switchTab`/`gotoRun`/`gotoTask`）
- テスト: `test/flow-relationship.test.js`（`npm test`）

### kiro-projects: `act_timeout=0` でタイムアウト無効（長時間委譲の空リトライを根治）＋ kiro-flow: リトライ時の run データ引き継ぎ・掃除

- **背景**: gitlab executor のような委譲は、人のレビュー往復で数日かかりうる（gitlab
  executor 側の待ちは `timeout=7日`/`approved_timeout=14日`）。一方 kiro-projects は run の結果を
  `act_timeout`（既定 1800 秒）しか待たず、**待ち切れずに retry を空増やし＆イシューを二重起票**し、
  `max_retries` 超過で誤エスカレーションしていた（`req-…-r2` のように「verify 未到達なのに
  リトライ番号だけ増える」症状の正体）
- **`act_timeout=0`＝無制限待ち**: `_act_submit`（daemon 待ち）・`_act_run`（都度起動）を「0 以下なら
  タイムアウトせず完了まで待つ」に変更。`_claim_ttl` も `act_timeout=0` のとき無限にし、長時間委譲中に
  他インスタンスへ claim を奪われて二重実行するのを防ぐ。設定例
  （`kiro-projects.yaml.example` / `kiro-projects.state-git.yaml.example`）の gitlab 委譲欄に
  `act_timeout: 0` 推奨を明記
- **kiro-flow `--inherit-from <先行run-id>`**: リトライ run 作成時に、タイムアウト/失敗した先行 run から
  **確定済み（done）ノードの結果・計画（graph）・中間成果物（artifacts）を引き継ぎ**、workspace 付き run
  では新 run の作業ブランチを旧 `kf/<old>` から派生させて**確定済み commit を失わない**。引き継ぎ後は
  **先行 run を掃除**（`runs/`＋inbox 要求＋claim を削除）。安全条件として、走っている run には触れず、
  「完全 done」（verify=NG 相当）の先行 run は状態を引き継がず掃除だけ行う（同一出力で即 done→再 NG の
  無限ループを防ぐ）。判断はすべて kiro-flow の `Bus.inherit_from` に閉じ込め、kiro-projects は直前試行の
  run-id を渡すだけ（`_prev_req_id`）
- 設計: `docs/designs/kiro-flow-retry-inheritance-design.md`。テスト: `InheritTests`（kiro-flow）/
  `TestActTimeoutZeroAndInherit`（kiro-projects）

### kiro-flow / kiro-projects-viewer: フロータブでもリモート daemon の生存信号（status.json）を追加

- **背景**: kiro-projects 側に実装した daemon 生存信号（state_git 経由でリモート viewer が稼働判定
  できるようにする機能）と同じギャップが、kiro-flow の daemon にもあった——フロータブの daemon
  稼働判定はロックファイル（`$TMPDIR/kiro-flow-locks/`）の pid 判定のみで、**同一ホスト限定**。
  state_git（鏡）越しにバスを見ているリモート viewer からは daemon の一時領域に届かず、常に
  「判定不能」になっていた
- **`<bus>/status.json`**: kiro-flow の daemon が `host`/`pid`/`node_id`/`orchestrators`/`workers`/
  `updated_iso`/`fresh_after_sec` を書く。`StateGit._scan()` はバスのツリー全体を走査するため、
  `bus.root` 直下に置くだけで既存の state_git がそのまま同期対象に含める（GitBus 側のような
  sparse-checkout の追加設定は不要）
- **idle 中の追加コミットは既定でゼロ**: 起動時に一度だけローカルへ書き、以降は実イベント
  （run 終端・「駆動中の run の生存リース」push）時に既存の sync/push へ相乗りする。
  `--status-interval`（`daemon` サブコマンドの引数。既定 `0`＝無効）を指定したときだけ、
  アイドル中もその間隔で status.json を更新する（kiro-projects 側と同じトレードオフ）
- **GitBus（`--git`）モードでは書かない**: sparse-checkout が `runs/`/`inbox/`（or
  `--git-subdir`）しか作業ツリーに展開せず、対象外パスへの書き込みが `sync_push()` の
  `git add -A` を壊しかねないため（state_git と `--git` は元々ここでも相互排他）
- **kiro-projects-viewer（フロータブ）**: `daemonStatus()` がロックファイル（同一ホスト・確定）→
  status.json（同期経由・推定）の順でフォールバックするようになった。daemon バッジは
  判定根拠を区別して表示（「稼働中（推定）」／「不明（同期経由）」＋最終確認からの経過時間・
  run/worker 数）

### kiro-projects / kiro-projects-viewer: リモート daemon の生存信号（status.json）— 別ホストでも稼働判定できるように

- **背景**: kiro-projects-viewer を daemon の稼働ホストとは別の PC で使う場合（`state_git` 経由でリモート本体の
  結果を見る構成）、操作（approve/hold/revise 等）は既存の `commands/`/`needs`/`inbox` ファイル契約でリモートでも
  同等に効いていたが、**daemon が今も生きているか」は分からなかった** — `~/.kiro-projects/instances/` はローカルの
  生存レジストリで state_git の同期対象外のため、リモートの viewer では「● 稼働中」バッジも概要の実行状況も
  常に空白になっていた
- **`status.json`（生存信号）**: 本体が `<project>/status.json`（`watch`/`level`/`updated_iso`/`fresh_after_sec`）を
  書き、これも state_git で同期する。実データ（backlog/needs/decisions/run-log 等）は既に同期されているため
  重複させず、生存信号だけの最小ファイルにした
- **idle 中の git 負荷は既定でゼロ**: `write_status` は実パス完了時にのみ呼ばれ、その他ファイルの変更と
  **同じコミットに相乗り**する（単体では追加の commit/push を生まない）。watch の idle 中は
  `--status-interval`（既定 `0`＝無効）を明示指定しない限り status.json に一切触れない。指定すればその間隔で
  idle 中も生存信号を更新でき、鮮度と git 負荷のトレードオフを利用者が選べる
- **`fresh_after_sec` は書き手が計算**: 本体が自分の同期間隔（`state_git_interval`/`status_interval` の大きい方の
  2 倍・下限 120 秒）から計算して埋め込むため、viewer 側は単純な経過時間比較だけで済む
- **kiro-projects-viewer**: instances（同一ホスト・確定）に無ければ status.json（同期経由・推定）へ
  フォールバックして稼働判定する。サイドバーの ● は判定根拠を区別して表示（同期経由の推定は輪郭のみの
  ◯＋プロジェクト名に `~`）。概要タブに「daemon の生存」カードを追加し、判定根拠・最終確認からの経過時間・
  `watch`/`level`・最終サイクル（`run-log.jsonl`）を表示する

### kiro-projects / kiro-projects-viewer: 人の即時フィードバック（revise）— 実行中でも気づいた時点で軌道修正

- **背景**: 自律バックログ消化中に人が「方向が違う」と気づいても（例: LLM がローカルサーバを
  立てて e2e を始めたが、実サーバに配備して実施してほしい）、従来はループがブロック（needs）
  するまで指示を届ける口が無かった。needs は**ループ起点（受動）**の往復であり、
  **人起点（能動）**でタスク内容やバックログ間の依存を直す経路が欠けていた
- **`revise` サブコマンド（CLI）**: `revise <id> [--title|--priority|--verify|--accept|--after|--note|--level|--track] [--feedback 指示] [--reason 理由]`。
  フィールドは置換（`''`/`none` で削除。`after` の自己依存・循環は拒否）、`--feedback` は次の act の
  要求文に必ず添付される。決定記録（DR `action: revise`）と `- learn:`（学習材料）を残す
- **効き方はタスク状態で決まる**: ready 等は即時反映 ／ blocked・review は ready へ積み直し
  （needs 消費・review からは手戻り記録）／ **doing（実行中）は `revised` マーカーで予約**し、
  実行側が settle 時に検知して**現在の試行の結果を確定しない**（verify も done もせず修正内容で
  積み直す）。daemon/remote の結果待ちもマーカー検知で早期に打ち切る。`rev` 世代番号が act の
  req_id に載るため、積み直し後の試行が修正前の古い run に合流しない
- **実行ループの即応性を強化**: ①パス途中（サイクル間）でも commands/・needs 記入を取り込む
  （長いパスでも人の修正が次のサイクルから効く）②claim 直後にディスク内容を採用してから
  doing 化（パス途中の CLI revise・直接編集を in-memory の古い内容で上書きしない）
  ③宙に浮いた `revised`（クラッシュ等）はパス開始時に回収して ready へ戻す（自己回復）
- **commands/ ドロップ契約に `revise` を追加**: `{"command": "revise", "id": ..., "feedback": ...,
  "after": ..., ...}`。CLI と同一ロジック・同一 DR（ビュアーや WSL 境界越しの操作向け）
- **kiro-projects-viewer**: タスク詳細に「✎ 修正して指示（revise）」フォームを追加
  （タイトル・優先度・依存 after・verify・accept の置換＋フィードバック。変更した項目だけ送信）。
  **実行中（doing）のタスクにも送れる**。送信後はタスク行に ✎ バッジ・詳細に「修正指示送信済み
  （取り込み待ち）」を表示し、本体が取り込むまで再送を防ぐ（needs と同じ file+mtime 照合）。
  経路は既存の指示と同じ auto/file/cli（既定はファイルドロップ・CLI 不要）
- **スキル更新**: `kiro-projects` スキルに「軌道修正（revise）」モードを追加
  （「タスクを直して」「やり方を変えさせて」「依存を付けて」等で発動）

### kiro-flow: git バスクローンの index.lock 残骸を自己回復（daemon の再 claim 無限ループを解消）

- **背景**: kiro-projects（autonomous）と kiro-flow を同じリポジトリのバスで併用中、前プロセスの
  異常終了（SIGKILL・電源断・daemon の terminate）がノードクローンに `.git/index.lock` を残すと、
  orchestrator の run 作成（`sync_push` の `git add`）が「File exists」で恒久的に失敗。run の meta が
  一度も push されず `run_exists` が偽のままなので、daemon が毎 poll 同じ要求を
  再 claim → commit → push → orchestrator 起動 → 即死 と繰り返す無限ループに陥っていた
- **ロック残骸の自己回復**: 管理クローンの再利用時に、十分古い（`GIT_LOCK_STALE_SEC`=30s 以上
  更新の無い）`index.lock` 等のロック残骸と中断 rebase（`rebase-merge/`）を除去してから使う。
  実行中に遭遇したロックも、新しいうちは短いバックオフで解放を待ち（稼働中の他 git を壊さない）、
  残骸と判明したら除去して再試行する（`git` 呼び出し共通のリトライ）。ロック検知を決定的にするため
  バスの git は `LC_ALL=C` で実行
- **使えないクローンは作り直す**: ロック除去でも回復できない管理クローン（index 破損等）は
  削除して再クローンする（バスの真実はリモート側にあるため使い捨てで安全）
- **daemon の終端化フォールバック（`fail_request`）**: orchestrator が run の meta を一度も
  書けずに死に続けた要求は、failed run を新規作成して終端化する。`run_exists` が真になり
  再 claim ループが有限回で必ず止まる。要求内容（request/workspace/references）は meta に
  引き写すので、消費者（kiro-projects の submit 待ち）も失敗を即検知できる
- **並行 submit の隔離**: submit のノード ID に pid を付与し、並行 submit が同じクローン
  作業ツリーを共有して index.lock を取り合う事故を予防

### gitlab-review-viewer: 起動時の「初期化に失敗しました」を修正

- `config.json` が想定外の形（全体が `null`・セクションが `null` や非オブジェクト等）に
  なっていると、設定マージ（`deepMerge`）が既定値を守らずそのまま通し、起動直後の
  `state.config.searchCache` / `state.config.gitlab.token` 参照で
  「初期化に失敗しました: Cannot read properties of null …」になっていた
- `deepMerge` を**既定値の型を保つマージ**に変更 — 既定値がオブジェクト / 配列のキーに
  型の合わない保存値（`null` 含む）が来た場合は既定値を採用し、壊れた設定ファイルでも
  起動できるようにした
- renderer 側の初期化も防御的に変更 — 設定の取得失敗時は最小構成で起動して
  「⚙ 設定から保存し直してください」と案内し、受け取った設定は形を検証してから使う。
  前回の検索条件の復元失敗も起動を妨げない

### kiro-projects-viewer: GitLab タブを「レビュー待ち」に特化

- GitLab タブを「レビュー待ち」に改名し、**repos のオープンイシュー＋関連 MR の
  横断一覧**（レビュー待ち・作業中）に特化。bus 由来の委譲イシュー一覧セクションは
  廃止 — run/ノード単位の決着（承認/却下）はフロータブのノード詳細が担当し、
  役割の重複を解消（bus は run 後に掃除されるため一覧としても不完全だった）
- 関連 MR の補完（glEnrich）を repos のオープンイシューに対して行うように変更
  （レビュー対象の MR チップが「レビュー待ち」一覧に出る）

### gitlab-review-viewer: 却下を「MR クローズ＋ブランチ削除・イシューは閉じる」に一本化

- 却下の「削除 / 閉じる」の 3 択を廃止し、**イシューは常に閉じる（削除しない）**に統一。
  コメント・経緯が記録として残り、委譲元ツール（kiro-flow はイシューのクローズで却下を
  検知し人コメントをやり直し指示として取り込む）にも決着が正しく伝わる。イシュー削除
  API（`glDeleteIssue`）は廃止
- 関連するマージリクエストは**クローズしてソースブランチを削除**する。対象はイシューの
  `related_merge_requests`（open）のうち**イシュー名と似たタイトルの MR のみ**
  （タブ選択と同じ `titleSimilarity` ≥ 0.5。本文で言及しただけの無関係な MR は対象外）。
  クローズ対象はダイアログに事前表示され、確認してから実行できる
- イシューのクローズは表示キャッシュの state に頼らず常に明示的に行う（委譲元の
  自動クローズは daemon 停止中は走らないため。クローズ済みなら no-op）
- **kiro-flow gitlab executor（防御）**: 決着待ち中にイシューが削除（404）されても
  一般エラーでなく**取り下げ＝却下**として決着させる（`decision: rejected`・
  guidance 空＝自動判断でやり直し）。404 以外のエラー（ネットワーク断・権限）は
  従来どおり失敗として送出

### kiro-flow: gitlab executor の却下を機械可読な決着に（data 付き failed）

- 却下時の failed result に、承認と対称の構造化データ（`issue_iid` / `web_url` /
  `decision: rejected` / `reason` / `guidance`（人コメント）/ `merged_mrs` / `closed`）を
  `data` として残す（却下例外に `data` 属性を載せ、worker が failed result に書く）。
  **status は failed のまま**——done は「後続が成果に依存してよい」契約であり、成果の無い
  却下では満たせない（却下=done にすると verify が緩いタスクで「人が却下したのに done 確定」の
  取り違えが起き得る）。やり直しの判断とループは従来どおり上位（kiro-projects）が担う
- kiro-projects の `read_reject_guidance` は構造化 data（`decision=rejected` の `guidance`）を
  優先し、無ければ従来の `[gitlab-reject]` 文字列マーカーにフォールバック（旧 run 互換）
- viewer は却下判定を `data.decision` からも導出し、ノード詳細に**却下理由と
  「やり直し指示（人コメント）」**を明示表示

### kiro-projects-viewer: ノード進捗の可視化・失敗時の人の指示・GitLab イシュー連動

- **ノード毎の進捗**: フロータブのノード詳細に、開始時刻・経過（実行中）・worker の
  heartbeat 鮮度と lease 生存・完了時刻と所要・作り直し回数（`retries`）・
  claimed/result のタイムライン（`events/*.jsonl` から）を表示
- **関連 GitLab イシュー（gitlab executor 連動）**: ノード詳細に関連イシューを表示し
  「レビューで開く」で gitlab-review-viewer へ引き継ぎ。承認済みは result の `data`、
  却下は output のイシュー URL（`decision=rejected` として GitLab タブにも並ぶ）、
  **実行中ノードは gitlab executor と同一導出の決定的タスクトークン**
  （`kf-<sha1(run_id/node_id)[:12]>`・イシュー本文の隠しマーカー）を GitLab API で
  検索して発見する（起票直後から追える）
- **失敗 run への指示**: run 詳細に「↻ 同じ要求で再投入」を追加。meta の要求・
  ワークスペース・参照リポジトリをそのまま新しい run として `inbox/` へ投入する
  （kiro-flow の公式入力契約のみ。daemon が新規要求として拾う）
- **README**: 「エラー時の流れとビュアーの役割」を追加 — kiro-flow 内の自動回復
  （retry → サーキットブレーカー）、gitlab executor の承認/却下と `[gitlab-reject]` の
  feedback 連携、人の出番（needs）とビュアーの対応窓口を 1 枚に整理
- 修正: アクティビティのイベント並び順が ISO タイムスタンプで正しくソートされて
  いなかった（数値減算前提だった）のを修正

### kiro-flow: PC の毎日シャットダウンに耐える（孤児 run を failed でなく自動再開）

- **孤児 run の引き継ぎ（resume）**: owning daemon が消失した（生存リース切れの）非終端 run を、
  次に起動した daemon が reclaim して**同じ run-id で orchestrator を再起動**する。確定済みの
  `results/` はバスに残っているため、未完了ノードだけが続きから実行される（従来は
  `orphaned: owning daemon が消失` として即 failed に確定していた）
- **暴走ガード `max_resumes`**（設定/`--max-resumes`・既定 3）: 「進捗なしの連続再開回数」で
  数え、前回の再開以降に results が増えていれば 1 から数え直す＝進捗のある長期 run は毎日の
  シャットダウンを跨いで何日でも継続できる。上限超過・要求ファイル欠損・無効化（0 以下）の
  ときだけ従来どおり failed に確定し、result を待つ消費者の永久待機を防ぐ
- daemon 稼働中の orchestrator 異常終了（クラッシュ）も同じ資格（max_resumes）で即時再開する
- 新 Bus API: `reclaim_request`（run が存在していても引き継ぎ claim できる）・
  `record_resume`（進捗リセット付きの再開カウンタ。meta の `resume_count` / `resume_progress`）。
  再開時は `run-resumed` イベントを events に記録

### kiro-projects: daemon 委譲の submit をリブート跨ぎで再接続可能に

- `_act_submit` の req_id を決定的に（`req-<backlogハッシュ>-<task.id>-r<retries>`）。
  PC のシャットダウンで submit の待機ごと消えても、再起動後の同じ試行は同じ req_id を
  再 submit して kiro-flow 側の既存 run（daemon が自動再開）に合流する＝**二重実行しない**。
  リトライ（retries+1）は新しい run になる

### kiro-projects-viewer: 自動再開の可視化

- run 詳細の heartbeat 行に自動再開回数（`resume_count`）を表示。「応答なし」の説明を
  「daemon が再起動すれば続きから自動再開されます」に更新

### kiro-projects: 指示のファイルドロップ口（commands/）を追加

- **新しい入力契約** `<project>/commands/<name>.json`
  （`{"command": "approve|hold|pin|defer", "id": "<task-id>", "reason": "..."}`）:
  CLI を実行できない環境（操作側が Windows・本体が WSL 内で稼働、など）から
  approve / hold / reprioritize と同じ人の指示をファイルだけで渡せる
- run/watch が取り込み、**CLI と同一のロジック（`cmd_approve` / `cmd_hold` /
  `cmd_reprioritize`）・同一の決定記録（DR）**で実行する（二重実装しない）。
  処理したファイルは削除、壊れた JSON・未知の指示・対象不在は `.err` へ退避して
  journal に記録（無限再試行を防ぐ）。watch 中は `--debounce` の静穏化が効く
- `has_work` が commands/ のドロップでも watch を起こす。`ensure_dirs` が口を作成し、
  instances レコードに `commands` パスを追加（外部操作者が発見できる）

### kiro-projects-viewer: 指示（承認/保留/優先度変更）をファイルベース化

- approve / hold / pin / defer を CLI 起動から `commands/<name>.json` ドロップに変更
  （上記の新契約）。**本体が WSL 内で稼働していてもファイル共有経由で届く**
- 届け方は ⚙ 設定「指示の届け方」で制御: auto（既定。instances の heartbeat で稼働中なら
  ファイル、停止中は CLI、CLI 不可ならファイルに退避）／file（常にファイル）／cli（従来）
- 書きかけ保護のため `.tmp` に書いてから rename（watch の debounce と二重の保護）
- 稼働判定は WSL 内の本体が登録する `root_windows`（`\\wsl.localhost\...`）にも一致

### kiro-projects-viewer: kiro-flow の状態を CLI に聞かずファイルだけで判定

- **run の生存判定**: `meta.json` の生存リース（`orch_lease_until` / `heartbeat_at`）から
  orchestrator の駆動中 / 応答なし（孤児の可能性）を導出（kiro-flow の `run_is_orphaned` と
  同じ規則。リース未記録の古い run は `updated_at` の age で判定）。running のまま owner が
  消えた run にフロータブで「応答なし」チップと heartbeat 経過を表示
- **daemon 稼働検知**: kiro-flow / kiro-projects と同一導出のロックパス
  （`sha1("local::" + realpath(bus))` → `<lock_dir>/daemon-<hash>.lock`）を読み、記録 pid の
  生存でバスごとの daemon 稼働をバッジ表示（kiro-projects の fcntl 不在時フォールバックと
  同じ根拠。CLI は起動しない）
- **共有バスの自動発見**: フロータブのバスを `<project>/bus` → `<container>/bus` →
  ⚙ 設定 `kiro.flowBus` → kiro-projects 設定ファイル（`<workdir>/.kiro` → `~/.kiro` の
  `bus:`）の順にファイルの存在だけで解決（`--bus` の共有バス構成でも run が見える）。
  run が無いときは探索した候補パスを表示
- **新設定**: `kiro.flowBus`（共有バスの明示指定）・`kiro.flowLockDir`（daemon ロック置き場。
  空なら `.kiro/` 設定の `lock_dir` → 既定 `$TMPDIR/kiro-flow-locks` を導出）
- 新モジュール `src/main/toolconfig.js`: `.kiro/` の kiro-projects / kiro-flow 設定から
  トップレベルのスカラ（`bus` / `lock_dir`）だけを読む簡易リーダー

### kiro-projects-viewer: プロジェクトダッシュボードを新規追加

- **新規ツール** `tools/kiro-projects-viewer/`: kiro-projects のプロジェクト状態を可視化する
  Electron アプリ（gitlab-review-viewer と同じプレーン Electron・実行時依存なしの構成）
- **概要タブ**: charter（goal / deliverables / constraints）・acceptance 達成状況
  （`project.json` の PASS 履歴スパークライン付き）・バックログの status 別集計・
  実行中クレーム・policy・直近 run（`run-log.jsonl`）・納品（`DELIVERY.md`）
- **バックログタブ**: `backlog/` / `archive/` のタスク一覧（status / priority / verify /
  after / level 等。フィルタチップ・詳細ダイアログ・ファイルを開く）
- **要対応タブ**: `needs/`（MADR 形式）の判断待ち / 検収待ちをカード表示。
  「ファイルを開いて回答」でエディタへ
- **フロータブ**: kiro-flow バス（`bus/runs/<run-id>/`）のタスクグラフを SVG の DAG で描画。
  ノード状態（done / failed / claimed / pending / 依存待ち）はファイル存在から kiro-flow と
  同じ規則で導出（lease 内 claim の決定的タイブレーク含む）。ノード詳細・進捗バー・
  アクティビティ（`events/*.jsonl`）付き
- **GitLab タブ**: gitlab executor が委譲したイシュー（results の issue_iid / web_url /
  decision / merged_mrs）と `repos.json` の GitLab リポジトリのオープンイシューを一覧。
  GitLab API（read）設定時はラベル・関連 MR の最新状態を補完
- **履歴タブ**: run-log・決定記録（`decisions/` の DR / learn）・納品・journal
- **プロジェクト発見**: 設定の roots に加え `~/.kiro-projects/instances/*.json`
  （稼働発見レコード）から稼働中コンテナを自動発見（heartbeat 鮮度で ● 稼働中表示）。
  `<root>/projects/<name>/` 標準レイアウトと旧フラット構成の両対応
- **ディープリンク**: `kiro-projects-viewer://open?root=<container>&project=<name>` で
  特定プロジェクトを直接開ける（シングルインスタンス）
- **人のアクション層**: 可視化だけでなく、人間ループの判断をアプリ内で完結できる。
  kiro-projects の公式な入力契約のみを使用（done 確定の不変条件を迂回しない）:
  - 要対応カードから **フィードバックして再開 / そのまま再実行**（needs の
    「## Decision Outcome」記入 + `- [x]` 確定 = `ingest_feedback` の正規ルート。
    本体の `read_feedback` / `feedback_submitted` で取り込み可能なことを相互検証済み）
  - **承認して done 確定**（review / milestone）・**保留（hold）**・
    **最優先へ / 後回し（pin / defer）** は kiro-projects CLI へ委譲（決定記録 DR が残る。
    CLI コマンドは設定可能）
  - **＋ タスクを追加**: `inbox/<name>.json` ドロップ（E4 push 型取り込み口）で投入。
    inbox 取り込み待ち件数もバックログタブに表示
  - 差し戻し（review）は修正方針の記入必須ガード付き。入力中は自動更新を一時停止し
    書きかけの回答を保護

### gitlab-review-viewer: ディープリンク対応（kiro-projects-viewer 連携）

- **カスタム URL スキーム** `gitlab-review-viewer://open?url=<web_url>` で外部ツールから
  特定イシュー / MR をレビュー画面として開けるように（対象は API で解決 → 候補一覧の
  先頭へ挿入 → 自動選択で関連イシュー / MR ごと左右ペインへ展開）
- **シングルインスタンス化**: 二重起動時は既存ウィンドウへディープリンクを転送
  （`second-instance` / macOS `open-url`）。electron-builder に `protocols` を宣言
- kiro-projects-viewer の GitLab タブ「レビューで開く」がこの入り口を使い、
  タスク→イシュー→レビューをシームレスに接続する

### gitlab-review-viewer: kiro-projects 連携を削除し、レビュー特化に再設計（破壊的変更）

- **削除**: kiro-projects needs（判断待ち/検収待ち）連携を全面削除（Needs タブ・
  フィードバック確定・approve・needs 要約と関連設定 `kiroAutonomous` / `needsPromptTemplate`）。
  GitLab のイシュー / MR レビューに特化する
- **プロキシ引き継ぎ**: `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` / `NO_PROXY` 環境変数を
  Chromium に引き継ぎ、webview 表示と GitLab API 呼び出し（`net.fetch` 化）の両方に適用
- **検索条件のキャッシュ**: グループ / プロジェクトの取得結果と選択値を含む検索条件すべてを
  `config.searchCache` に自動保存し、次回起動時に復元
- **作成者フィルタ**: イシュー / MR をユーザー名（`author_username`）で絞り込み可能に
- **ペイン振り分けの変更**: 種別（イシュー / MR）条件は候補一覧の絞り込みのみに適用。
  候補を選択すると紐づくイシューを左ペイン・MR を右ペインにタブ表示。イシューに紐づく
  MR が複数ある場合は、イシューとタイトルが同じ MR（`Draft:` / `Resolve "…"` 形式は
  同一視）のタブを自動でアクティブにする
- **スプリッター**: 左右ペイン間をドラッグしてサイズ変更可能に
- **URL バーメニュー（☰）**: 各ペインに「リーダーモード（本文テキストのみをタブ表示）」
  「要約を作成してタブ表示」「Obsidian へ送る」を追加。生成されたローカルタブは × で閉じられる。
  Obsidian 送信はアクションバーから ☰ メニューへ移動し、アクティブなタブの内容
  （GitLab ページのタブはリーダーモードと同等の本文抽出テキスト）を書き出す
- **アクションバー再編（承認 / 差し戻し / 却下 / 変更）**: 操作対象は表示中のイシュー
  （無ければ MR）に自動決定。旧マージ / クローズ / リオープンボタンと操作対象セレクト・
  常設ラベルプリセット行を撤去
  - **承認**: `status:elaborated` → `status:open`。`status:approved` は同タイトル MR を
    マージしてイシューをクローズ（コンフリクト / 未解決レビューコメント / 他ステータスは
    グレーアウト。可否は MR の `has_conflicts` / `blocking_discussions_resolved` で判定）
  - **差し戻し**: `status:elaborated` → `status:draft`、`status:approved` → `status:needs-rework`
    （他ステータスはグレーアウト）
  - **却下**: 削除 / 閉じる / キャンセルの 3 択ダイアログ。両者とも同タイトル MR を
    クローズし、削除はソースブランチ削除 + イシュー削除、閉じるはイシューをクローズ
  - **変更**: ラベルプリセット（従来の下ペイン UI）をダイアログ表示し「実行」で適用
  - いずれも入力テキストを本文として `# ボタン名` 見出し付きコメントを対象へ投稿
  - 実行後（コメント投稿・ショートカットのラベル変更を含む）は左右ペインの
    イシュー / MR 表示を自動で再読み込みして結果を反映
- **要約の高速化と出力フィルター**: 既定プロンプトを簡潔化（出力のみ・ツール実行禁止・
  400 字目安の分量上限）し、入力も自動で切り詰め（本文 4,000 字・直近コメント 20 件×400 字・
  変更ファイル 50 件）。要約は `===SUMMARY_START===` / `===SUMMARY_END===` マーカーで
  挟ませ、エージェント出力からマーカー間の要約本文のみを抽出（マーカーが無い場合は
  スピナー・枠線・進捗表示などのノイズ行のみ除去するフォールバック）

### codd-gate v1.0.0 — doc/code/test 一貫性ゲート（単体 CLI・kiro-projects 連携はオプション）

[CoDD (Coherence-Driven Development)](https://github.com/yohey-w/codd-dev) の設計
（Trace＝接続マップ / Impact＝Green・Amber・Gray 分類 / Verify＝no fake green）を翻案した
決定的ツールを追加。**kiro-projects に依存しない独立ツール**（python3＋git のみ・独立インストーラ
`install.sh`）として単体で CI / git hook から使え、kiro-projects とは**本体無改造**の一方向
オプション連携（既存フック regression_cmd・charter acceptance・タスク verify・enqueue --json / inbox
のみで結合するプラグイン方式）。ブラウンフィールド前提で、既存負債は止めずに
「棚卸し→ラチェット→backlog 返済」、新規変更だけを差分ゲートで護る。

- **新規ツール `tools/codd-gate/`**（stdlib のみ・LLM 不要）: `scan`（doc↔code↔test の接続マップと
  壊れた参照/未文書化/未テストの負債棚卸し）/ `impact`（差分の Green/Amber/Gray/**Followup** 分類）/
  `verify`（差分ゲート＋ `--debt` 負債ラチェット。exit 0/1）/ `tasks`（ドリフト・負債を共通 task
  スキーマの修復タスクへ変換。同一 repo は決定的 verify、別 repo は accept＋workspace で
  ルーティングに乗せる）/ `check`（修復タスク verify 用の状態アサーション: 接続・参照解決・鮮度）。
- **複数リポジトリ（外部フォーマット非依存）**: レジストリは共通スキーマ（`--repos` ファイル /
  設定 `repos:`。dir / docs / tests / code を per-repo 指定）。identity は (url, path, base)＝
  パス＋ブランチで一意。リポジトリ横断参照は `repo名:相対パス`。charter.md は読まない。
- **接続の推定は決定的**: 明示注釈 `coherence: doc|code|test=…`（最優先）＞ md のインラインコード/
  リンク ＞ Python import ＞ 命名規約（一意時のみ）。曖昧は接続も負債もしない。
- **git アクセスの原則**: 通常動作はローカル読み取りのみ（clone/fetch ゼロ・フル clone はどの経路にも
  無い）。url-only repo は `--sync`（opt-in）で git-worktree-cache-pattern 準拠に実体化——共有 bare
  ミラー（初回のみ blob:none・以後増分 fetch。`KIRO_GIT_CACHE_DIR` で kiro ツール群と共有）から
  **fetch 後の SHA** で detached worktree（INV-1 鮮度）を生やし、run 後に worktree だけ回収。
  実体化不能は黙って PASS 側に倒さない。`dir:` 指定 repo には触れない（判定対象は作業ツリーそのもの）。
- **kiro-projects に汎用取り込みフック `intake_cmd` を追加**（設定/CLI `--intake-cmd[-interval]`）:
  外部の決定的ゲート/検出器を watch の周期で pull し、stdout の enqueue --json を**冪等取り込み**
  （id が現役 backlog に居れば飛ばす）。パス開始時と idle 中に間隔律速で実行、失敗は journal に残して
  無視。**常駐は kiro-projects 側だけが持ち、intake_cmd（codd-gate 含む）は単発・有界**という役割
  分担を固定。有効化は設定だけ: `regression_cmd`（差分ゲート）＋`intake_cmd: codd-gate tasks --debt`
  （負債の自動返済）＋charter acceptance（ラチェット）。kiro-projects の install.sh は隣に
  codd-gate があれば同梱インストールする。
- **`tasks --debt --cohort`**: 未文書化/未テストのような同種負債の山を repo 単位の cohort
  （`cohort_items`＋`{item}`）に集約し、後段の分解を kiro-projects の pilot-then-batch に委ねる。
  タスク id は発見内容から決定的（48 字・末尾ハッシュ）＝intake の冪等キー。
- **共通スキーマ `schemas/` を新設（repos / task をツール横断の独立スキーマとして管理）**:
  `repos.schema.json`（リポジトリレジストリ。identity = (url, path, base)）と `task.schema.json`
  （制御層タスクの JSON 表現。Markdown 形の正典は backlog.md.example・未知キー保持）。
  kiro-projects は手書きの `<project>/repos.{yaml,yml,json}` があれば**レジストリの正**として読み
  （charter の `## repos` は互換入力＝内部で同形に正規化して引き回す）、**無ければ charter から
  repos.json を自動生成**して外部ツールへ「ファイルとして渡す」（_meta マーカー付き・正は charter に
  追従・## repos が消えれば生成物も消す。分類グロブ docs/tests/code も損失なく引き継ぐ）。
  repos ファイル単独では charter モードは発動しないがルーティング/参照解決には効く。kiro-flow の
  `--workspace`/`--reference` はこのスキーマの 1 エントリの射影。codd-gate のタスク出力がスキーマに
  適合することはテストで突き合わせる。
- **codd-gate は kiro-projects から完全独立に**: charter アダプタ（--charter）を廃止し、レジストリは
  共通スキーマ（--repos ファイル / 設定 repos:）のみに。`tasks` は共通 task スキーマへの**直接出力**
  であり特定ツール向けアダプタではない。結合は入力（repos スキーマ）・出力（task スキーマ）とも
  `schemas/` のデータ契約だけ。
- **タスク追加の責務境界を明文化**: kiro-projects は元よりタスクを入力とする設計（enqueue＝汎用の
  取り込み口・外部ソースは薄いアダプタで流し込む思想）で、タスク契約（正典 `backlog.md.example`・
  未知キー保持の前方互換）の所有者は kiro-projects。codd-gate コアの正は**所見**（`impact --json` /
  `verify --debt --json`）で、`tasks` はそれを共通 task スキーマへ直接出力する。
- **外部 CLI の差し込み点をカタログ化**: kiro-projects 設計書 §4.1 に公式の 6 点（E1 verify/
  acceptance・E2 regression_cmd・E3 intake_cmd・E4 inbox/enqueue・E5 notify_cmd・E6 executor）の契約
  （入出力・環境・制約）と選び方・妥当性を明文化。暗黙の拡張点は作らない（S1 優先順位・S5 エスカレー
  ション・S7 予算にはフックを設けない理由も記載）。codd-gate は E1+E2+E3 を使う適用例。
- **新規スキル `codd-gate`**: 単体運用（git hook / CI）を主、kiro-projects 結線
  （regression_cmd → acceptance ラチェット → intake_cmd 返済）を追加情報として整理。
- 設計書 `docs/designs/codd-gate-design.md`（codd-dev からの翻案対応表・差し込み点選択の妥当性検証
  つき）とテスト（codd-gate 28 件＋kiro-projects intake 5 件）を同梱。

### agentic-search v1.0.0 — 反復探索を共有スキル化し検索系スキルへ一括導入

検索を **単発の retrieve** から **エージェント（Claude）が「検索 → 評価 → 再構成 → 再検索 → 統合」を
反復する** agentic search へ引き上げた。反復ループの「頭脳」を検索系スキル横断の共有スキルとして切り出し、
コーパスごとの検索（retrieve）は各スキルに残す構成とした。各スキルの哲学（Markdown の読み書きだけ・
ループの駆動役はエージェント）に従い、スクリプトは反復を内蔵せず
**「1 ステップの検索 ＋ 次の一手の手がかり」を返すプリミティブ** に徹する。

- **新規スキル `agentic-search`（tier: core）**: `scripts/hints.py` がバックエンド非依存のヒント
  エンジン。正規化済み結果リスト＋クエリから `next_action`（synthesize/refine/expand/broaden）、
  `suggested_queries`、`related_ids`、`gap_keywords`、`sufficient` を計算する（ライブラリ／CLI 両対応）。
  反復ループ・収束条件の正典は `references/protocol.md`。
- **ltm-use v5.4.0**: `recall_memory.py` に `--json` / `--suggest` / `--ids`（マルチホップ取得）を追加し、
  ヒント計算を agentic-search に委譲（未導入時はローカル実装にフォールバック）。探索中は `--no-track` で
  `access_count`／忘却曲線を汚さない運用とする。
- **wiki-use**: `wiki_query.py search` に `--json` / `--suggest` を追加。被覆率を score、本文の
  `[[wikilink]]` を related（マルチホップの種）として正規化する。
- **moltbook-use**: `moltbook.py search` に `--json` / `--suggest` を追加。連邦検索（issues/blobs/notes）の
  ヒットを正規化し、トピックラベルを tags として扱う。
- **オプショナル依存**: wiki-use / moltbook-use は agentic-search 未導入時はヒントを省略して通常検索のみ
  返す（graceful degradation）。

### kiro-flow: git バスのクローンをリトライ化（不安定なネットワークでの起動失敗を修正）

イシュー委譲のような分散構成では、daemon／orchestrator／worker が**起動毎に git バスを clone** する。
従来この初回 clone（`GitBus._ensure_clone`）には**リトライが無く**、一過性のネットワーク障害で
即 `RuntimeError` となり「移譲側が起動できない」原因になっていた（push/pull は指数バックオフで
リトライ済みだったのに、clone だけ未対応だった）。

- `GitBus._clone_with_retry` を新設し、初回 clone を **push/pull と同じ指数バックオフ（2,4,8,16s・
  `CLONE_RETRIES` 回）**でリトライする。再試行の前に部分 clone の残骸を消す（`_reset_clone_dir`）ので
  「宛先が空でない」で二次失敗しない。blob フィルタ非対応サーバ向けフォールバックは従来どおり。
- **委譲される側（実作業ノード）**も同様に脆かったため、ワークスペースの per-task clone
  （`_clone_repo`）にも同じバックオフリトライを追加。clone 失敗で即タスク失敗にならないようにした。

### kiro-flow: gitlab executor の起票を冪等化（再 claim 時の二重起票を修正）

`--executor gitlab` で各タスクを GitLab イシューに委譲する際、ワーカーが MR の決着待ち（最長 7 日）の
最中に**夜間停止などで殺される**と、result が書かれないまま claim の `lease`（既定 30 分）が失効し、
タスクが `pending` に戻って**別の（リモートの）ワーカーに再 claim** される。従来はそのとき
`execute()` が無条件に新規イシューを起票していたため、**同一タスクのイシューが二重に立つ**現象が起きていた。

- **冪等な起票に修正。** イシュー本文にタスクごとの決定的トークン（`art_dir` ＝ `runs/<run>/artifacts/<node>`
  由来の `kf-<hex12>`）を隠しマーカーとして埋め込む。起票前に同じトークンの **open イシュー**を検索し、
  見つかれば**新規起票せず再アタッチ**してポーリングを再開する（`_task_token` / `_task_marker` /
  `_find_open_issue_by_token`、ポーリングループを `_wait_for_decision` に分離）。
- 検索の取りこぼし・別タスクの誤ヒットに備え、検索後にマーカーが description に**実在することを検証**して
  から再アタッチする。`art_dir` が想定形でない場合は従来どおり毎回新規起票（後方互換）。

### kiro-flow: gitlab executor が外部クローズの承認/却下を判定してタスクグラフへ反映

イシューが（人手・自動化など）**外部でクローズ**されることがある。従来は MR で決着がつかないまま
クローズされると一律「取り下げ＝却下」にしていたため、人が手動マージ後にクローズしたケースなどを
取りこぼしていた。MR の状態 → `status:approved`/`status:done` ラベル → イシューコメントの内容
（承認語/却下語）の順で承認・却下を推定し、結果をタスクグラフへ反映するよう改めた。

- `_mr_decision` は MR の状態のみで判定する責務に縮小（外部クローズの扱いを分離）。新設の
  `_closed_issue_decision` がラベル→コメント（`_decision_from_comments`、却下語を承認語より優先）の順で
  推定する。判断材料が無いクローズは従来どおり取り下げ＝却下。
- 承認なら `done` 成果として下流へ、却下なら `[gitlab-reject]` 例外で上位（kiro-projects）が
  コメントを活かしてやり直す。承認/却下の根拠（reason）をログ・成果テキスト・例外メッセージに出す。

### gitlab-gatekeeper（旧 review-concierge をリネーム＋門番化・破壊的変更）

AI が量産する MR/イシューのレビュー負荷を下げるため、`review-concierge` スキルを **`gitlab-gatekeeper`** に
リネームし、マージ承認の「門番」として 3 モード構成に拡張した。判断は人間が下し、スキルは執行に徹する。

- **packet モード（既定）に Gate A を追加。** レビューパケット生成を指示されたら、判断材料を集める**前に**
  紐づく MR の未対応レビューコメント（`get-mr-discussions --unresolved`）を確認し、**1 件でもあれば
  `status:needs-review` へ差し戻し、未対応スレッドの要点をコメントして終了**（パケットは作らない）。
- **decision モードを新設。** ユーザーの承認/否認を受け取り GitLab へ執行する。
  - **承認** → マージ可否（未対応コメント無し・非ドラフト・コンフリクト無し・CI 成功）を事前確認して
    `merge-mr` → イシューを `--state-event close`。**マージできない場合（事前不可、または merge-mr が非 2xx）は
    `status:needs-review` へ差し戻し、不可理由を具体的にコメント**（「マージした」と誤報告しない）。
  - **否認** → `status:needs-review` へ差し戻し、**ユーザーの自然文コメントを解釈して実行可能な差し戻しコメントを
    生成・投稿**（ユーザーが述べていない要求は足さない。曖昧なら 1 問確認）。
- ラベル/マージのポリシー（`needs_review_label` 既定 `status:needs-review`、`ready_labels` 既定
  `status:review-ready`、`merge.squash`/`merge.remove_source_branch`、`require_ci_success`）は呼び出し側が上書き可能。
- 後方互換は取らない（`review-concierge` のディレクトリ/スキル名は廃止）。GitLab 操作は従来どおり
  `gitlab-idd` の `gl.py` を再利用し、レビュー観点は `agent-reviewer` の references を再利用する。

### マルチリポジトリ・ルーティング（kiro-projects × kiro-flow・破壊的変更）

大規模・複数リポジトリのプロジェクトを自律運用するため、「タスク → コミット先リポジトリ」のルーティングを導入した。
**判断は制御層（kiro-projects）に集約し、執行は実行層（kiro-flow）が担保する。** 設計の詳細は
`tools/kiro-projects/ROUTING.md`。後方互換は取らない（旧 `--repo`／タスク `- repos:` は廃止）。

#### kiro-flow

- **1 run（=バックログ単位）= 1 ワークスペース（唯一の書込先）に固定。** `--repo`（複数・成果物リポジトリ）を
  廃止し、`--workspace`（ちょうど1つ・素の URL か JSON `{url,path,base,target,desc}`）へ刷新。**リポジトリの同一性は
  (url, path, base)**（同 URL でも path・ブランチが違えば別ワークスペース。`_workspace_clone` のキャッシュキーも修正）。
- **kiro-flow が作業ブランチを作ってワーカーへ渡す。** worker はワークスペースを clone し、`kf/<run-id>` を base から作成。
  エージェントは作業ツリーを編集するだけで、**変更があれば kiro-flow が commit して push**（分散 worker は同じ
  `kf/<run-id>` へ push し rebase リトライで統合）。**変更が無ければブランチを push しない**＝調査だけの読み取り専用
  グラフでは何も書き込まない。デリバリ（branch/commit/target）を result に記録。
- ノード単位の repo 割り当て（`resolve_node_repos`／プランナーの repos 注釈）を撤廃し、run 内の全ノードが同一
  ワークスペースを共有する形に単純化。参照だけのリポジトリは kiro-flow では扱わず、要求本文（goal）として伝搬する。
- executor 契約に構造化 `workspace`（spec dict）引数を追加。**gitlab executor は起票先 GitLab プロジェクトを
  ワークスペース URL から解決**（SSH/https 両形）し、無ければ `gitlab.repo_url` をフォールバックに使う。
- 孤立 clone の janitor 接頭辞を `kiro-flow-repos-` → `kiro-flow-ws-` に変更。
- **参照リポジトリ（読むだけ）を `--reference` で構造化伝搬**（run メタ `references`）。worker がエージェントの
  プロンプト（参照節）と **gitlab イシュー本文の『## 参照リポジトリ』節**に描画する。従来は要求本文へ畳んで
  いたため、分解後の各ノード/イシューに参照情報が届かなかったのを解消。gitlab イシューの対象/参照リポジトリ節は
  構造化 spec から Markdown 整形し、ローカルの clone パス（作業ディレクトリ）は載せない。
- **gitlab executor の完了判定を「関連 MR の状態」ベースに（人が MR を管理）**: kiro-flow は MR を
  **自動マージしない**。リモート worker が MR を用意し、人が関連 MR を管理する。**全 MR マージ＝承認**
  （イシューをクローズして成功）／**一つでも未マージでクローズ＝却下**（人コメントを取り込み元イシューを
  クローズし `[gitlab-reject]` 付きで失敗。コメントが無ければ自動判断）。MR が open のうちは待機。人の確認は
  時間がかかるため待機は長め・設定可能（`gitlab.timeout` 既定 7 日 / `gitlab.approved_timeout` 既定 14 日・0=無限）。
- run が `failed` で終端したら `kiro-flow run` は**非 0 終了**（委譲先の却下を上位が act 失敗として検知できる）。

#### kiro-projects

- **ルーティング解決を新設**（`resolve_workspace`）: タスクを**ちょうど1つの書込先ワークスペース**へ。解決順は
  明示 `- workspace:` > policy `route:` > charter `owns:` 推定 > auto-route（LLM）> `default_workspace`／候補1つ。
  決定はタスク md（`- workspace:` / `- routed_by:`）へ書き戻して安定・監査可能にする。owns 推定は
  タスクの `- paths:` ヒントに加え **verify コマンドが操作するパス**からも行う。
- **plan/review フェーズで書込先を必ず明示**（`assign_plan_workspace`）: charter からバックログを生成する時点で、
  各タスクの workspace を **verify が操作するパスの owns を持つ repo** として決定論的に確定し、それ以外（charter の
  他 repo・プランナーが挙げた repo）は参照（`refs`）へ振り分ける。生成直後から書込先が明示され、route 層は
  それを尊重する。`task_reference_specs` は `- refs:` に加え `- repos:` のトークンも参照として扱い、書込先 url は除外する。
- charter `## repos` に **`owns:`（担当パスのグロブ）** を追加。**owns 有り=書込先候補、owns 無し=参照リポジトリ**
  （読むだけ・`--reference` で伝搬・clone しない）。policy に **`route: <パターン> -> <repo名>`** ルールを追加。
- 設定 `route_planner`（kiro/none）と `default_workspace` を追加。タスクに `- workspace:` / `- paths:` / `- refs:` /
  `- routed_by:` フィールドを追加。kiro-flow へは `--workspace`（単一）と `--reference`（参照・複数）を渡す
  （旧 `--repo` 列を廃止）。参照は要求本文へ畳まず構造化伝搬する（`_reference_cmd_args`）。
- **【修正】`- workspace:` 指定タスクの verify を該当ワークスペースのクローン内で実行するようにした**（バグ修正）。
  ワークスペースへルーティングされたタスクは成果が workdir（git-bus ルート）でなく該当 repo の作業ブランチへ push される
  ため、verify／回帰を従来どおり workdir で回すと「成果の無い場所」で偽 NG になっていた。`_task_verify_cwd` を新設し、
  verify の実行先を **明示 `verify_cwd` > タスクの `- workspace:` 該当 repo の一時 clone（`target`→`base` ブランチ・`path`
  をルート）> workdir** の順で解決（`_acceptance_cwd` と同流儀）。差分基準 `$KIRO_BASE_REV` はクローンの HEAD に取り直し、
  clone は worker の push 先を反映するため都度取り直す。clone 失敗・`path` 不在は黙って workdir に倒さず NG 扱い
  （成果の無い場所での偽判定を防ぐ）。単体テスト 5 件（clone 実行先・`path` をルート・未指定は workdir・明示 `verify_cwd`
  優先・clone 失敗で RuntimeError）を追加。README / GUIDE に追記。
- **委譲 executor（gitlab）の却下→やり直し連携**: gitlab の却下（未マージ MR クローズ）を kiro-flow 内部で
  再委譲せず即失敗化するため、委譲 executor へ `--max-retries 0` を渡す（複数イシューの濫造を防止）。act 失敗時は
  `read_reject_guidance` が直近 run の `[gitlab-reject]` 指示（人コメント）を読み、`_settle_failure` が `feedback` に
  注入して通常リトライの次 act で活かす（コメントが無ければ自動判断）。
- 単体テストを新 API へ更新（kiro-flow・kiro-projects 両スイート、計 494 件 green）。

### kiro-projects

#### Added
- **acceptance/verify を「対象 repo のクローン先」で実行できるようにした**（offload で worker が対象 repo を temp に
  clone・push して消すと workdir に成果が出ず、verify を git-bus 等の workdir で実行してエラーになる問題への対処）。
  実行先を **明示 `--verify-cwd`（設定 `verify_cwd`）> 単一対象 repo の一時 clone > workdir** の順で解決
  （`_acceptance_cwd`）。charter の非 readonly repo がちょうど 1 つなら、その `target` ブランチ（worker の push 先）を
  毎評価で `git clone --depth 1` し `$KIRO_BASE_REV`＝clone HEAD で検証して後始末する。clone 失敗は workdir へ黙って
  フォールバックせず**全 NG 扱い**（成果の無い場所での偽判定を防ぐ）。複数 repo は曖昧なので自動 clone せず `--verify-cwd`
  で明示。タスク verify／回帰検査も `--verify-cwd` 指定時はその先で実行。CLI `--verify-cwd` / 設定 `verify_cwd` を追加。
  単体テスト 5 件（cwd 解決・明示上書き・単一 repo clone・clone 失敗で全 NG・複数 repo は workdir）を追加。
- **charter `## acceptance` に自然文を書けるようにした**（検証コマンドを書けない人向け。タスクの `accept:` と同じ流儀）。
  `- accept: <自然言語の完了条件>` か、全角句読点を含む散文の箇条書きを自然言語とみなし、run 時に `resolve_charter_acceptance`
  がエージェント（`synth_verify` 共用）で**決定的なシェル verify へ合成**する。合成結果は `project.json` の `acceptance_synth`
  （原文→コマンド）に**キャッシュ**してサイクル/再実行をまたいで done 基準を安定させる（再合成のブレ防止）。合成できない
  自然言語が残れば `no-acceptance`（done 判定不能）で人へ回す＝**「done は acceptance 全 PASS のみが根拠」の鉄則を保全**。
  散文を shell へ誤って流す事故は `_looks_like_shell_command` の二段チェックで防止。charter.md.example / README / GUIDE /
  design に追記。単体テスト 5 件（`_acceptance_kind` 分類・合成・キャッシュ安定・収束・合成不能で人へ）を追加。
- **自動アップデート（既定 on・6 時間毎・起動直後にも実施）**。スキルリポジトリ（配布元）の `main` に更新が
  入ったら、`run --watch` の **アイドル時** に取り込む。停止中に入った更新も起動直後の初回アイドルで拾う。doctor と同じ流儀で決定的: `git ls-remote` で main の先頭を
  確認 → 適用済み SHA（`~/.kiro/kiro-projects.update.json`）と違えば、temp 領域へ `tools/kiro-projects/`
  だけを **sparse-checkout** → `install.sh` 実行 → **動いていた cwd のまま `os.execv` で graceful 再起動**
  （レジストリ登録は再起動前に後始末）。手動は `update [--check|--now]`。**更新元 URL は `install.py` が
  生成する `skill-registry.json`（`repositories.origin.url` → `install_dir`）から自動解決**（`update_repo`
  未指定でよい）。設定キー `update_enabled`（off スイッチ・既定 on）/ `update_check_interval`（既定 21600=6h） / `update_repo` /
  `update_branch` / `update_subdir` / `update_installer`。初回はベースライン記録のみ（無更新）。タスク実行中は
  何もしない。単体テスト `SelfUpdateTests`（11 件）を追加。
- charter `## repos` に `path`（作業フォルダ）属性を追加。**モノレポを「同じ url で name と path を変えた複数
  エントリ」に分けてフォルダ別の役割を表現できる**（`desc` に役割、`path` に作業フォルダ）。プランナー提示
  （`build_charter_request`）に path＋役割(desc) を載せ、worker 文脈（`_charter_definition`）にも path を伝搬。
  同一 url を複数エントリで使う場合は distinct な `path` を必須化する検証を追加（曖昧さ防止）。`desc` は `役割`/`role`
  の別名も受ける。charter.md.example / README にモノレポの書き方を追記。
- charter `## repos` に `readonly`（参照のみ）フラグを追加（`- readonly: true` /『- 参照のみ:』。値なしでも True）。
- **repos のメタ（path/base/target/readonly/役割）をタスク単位で構造化 `--repo`（JSON）として kiro-flow へ伝搬**。
  従来は URL のみ・全 repo 一覧のテキストだけだったのを、`task_repo_specs`/`_repo_token`/`charter_repo_spec_map` で
  そのタスクの repo だけを構造化して渡す。kiro-flow 側は `parse_repo_token`（URL or JSON）/`_clone_repo`（base ブランチを
  checkout）/`ensure_work_repos`（spec＋clone パスを返す）/`repo_instruction`（フォルダ・作業ブランチ・push 先・参照のみを
  出し分け）を実装。**この指示は gitlab executor 経由でイシュー本文（## 目的）にも載る**ため、フォルダ/ブランチ/参照のみが
  イシューに構造的に表現される。参照のみは push を指示しない。後方互換: 素の URL トークンは従来どおり。
- **必要な repo だけを必要なノードで clone**（kiro-flow）。repo を run 全体ではなくノード（タスク）単位で割り当て、
  worker は `resolve_node_repos` でそのノードに割り当てられた repo だけを clone する（空配列＝何も clone しない・未注釈は
  全 repo にフォールバック）。計画時に `_assign_node_repos` で割当: **stub プランナーは全 repo（安全側）、kiro プランナーは
  利用可能 repo 一覧（フォルダ/役割/参照のみ込み）を見て各タスクに必要な repo だけを判断**（`_repos_planner_note`）。
  ノードに `repos` フィールドを追加（`_node_entry`/`_coerce_tasks` が保持）。fan-out で多数ノードに分解されても各ノードは
  自分に必要な repo だけ clone する（URL 単位の重複排除と併せ無駄 clone を最小化）。
  kiro-projects 5 件・kiro-flow 7 件のテストを追加（全 240 / 152 OK）。
- **成果物リポジトリ clone の削除機構を強化**（kiro-flow）。temp clone 名に所有 pid を埋め込み
  （`kiro-flow-repos-<pid>-…`）、daemon の定期掃除に `sweep_work_repo_dirs` を追加: **SIGKILL/OOM/電源断で
  finally が走らず残った孤立 clone を「所有 pid 死亡」を根拠に回収**（稼働中・`--keep-alive` 長命 worker の clone は
  経過時間に関わらず残す）。`cleanup_per_node`（CLI `--cleanup-per-node`）で各ノード完了/失敗ごとの即時削除を
  opt-in（長命 worker のディスク抑制）。`atexit` でプロセス終了時削除を二重化。テスト 2 件追加（kiro-flow 154 OK）。
  既存の削除経路（正常終了/エラー/agent タイムアウト/SIGTERM の finally＋signal）は従来どおり。
- 黒箱 CLI 統合テスト（`TestCliEndToEnd`）。`kiro-projects.py` を実プロセスとして argv 起動し、
  ループ機構を end-to-end で検証: drain→exit 0・成果物退避（archive）、verify 失敗→blocked→exit 1＋
  needs ファイル生成、予算超過→budget→exit 2、`--no-archive` で退避せず削除。`run_loop()` の in-process
  テスト（`TestRunLoop`）に対し、CLI 配線（argparse・パス解決・停止理由→exit code）を実バイナリで担保する。
- クロスツール統合テスト（`TestCliKiroFlowDelegation`）。autonomous CLI の act が実際に `kiro-flow.py` へ
  サブプロセス委譲して完走することを検証する。`--kiro-flow` にラッパを噛ませ、委譲 argv
  （`run --planner stub --executor stub …`）と委譲先 kiro-flow の正常終了（exit 0）を捕捉して assert する。
- GUIDE に「おすすめ構成（本番）」セクションを追加。**PC 起動時に両 daemon 常駐 ／ executor=gitlab ／
  bus=git** の完成形レシピ（kiro-flow.yaml / kiro-projects.yaml の雛形、systemd ユーザーサービス 2 本、
  `lock_dir` 一致・git 認証・`~/.kiro/` 自動探索の勘所、稼働確認コマンド）。L0–L4 を通した後の到達点を明示する。
- `--executor`（設定 `executor`）に kiro-flow の executor プラグインを指定できるようにした。組み込みの
  `kiro` / `stub` に加え、プラグイン名（例 `gitlab`）や `.py` パスをそのまま `kiro-flow run --executor <値>`
  へ委譲する（`choices` 制限を撤廃）。`kiro-projects.yaml.example` / README にも記載。`doctor` の
  kiro-flow 解決チェックも `executor != stub` の全 executor（プラグイン含む）を対象に拡張した。
- `doctor` サブコマンド。ログ/状態/環境から稼働を診断し、原因を **env（ユーザー環境固有）/
  config（設定）/ program（プログラム上の不具合）** に分類する。収集・修正・起票の駆動は決定的に、
  診断と分類は kiro-cli へ委譲（kiro-cli 不在時は決定的チェックのみで続行）。`--fix` で env/config を
  修正（`create-dirs` / policy への保護デニーリスト追記）し、program の不具合は `gitlab-idd` スキルで
  GitLab イシューを起票する。**スキルが見つからなければ出力のみ**。終了コード `0`=健康/`1`=所見あり/
  `2`=未解決の critical。既定（`--fix` 無し）は無害な診断のみ。
- `doctor` の **実行層 kiro-flow との連携**（`--with-flow`・既定 on／`--no-flow` で本体のみ）。
  同じバスに対して `kiro-flow doctor --json` を呼び、実行層の所見を `[flow]` 印で統合する。`--fix` 時は
  kiro-flow 側にも委譲し、kiro-flow が自分の env/config 修正と program 起票を担う（二重作業を避ける）。

#### Changed
- **charter `## repos` で同一 URL のエントリを base/target（ブランチ）でも区別できるようにした**。従来は同じ URL を
  複数エントリで使うと distinct な `path`（作業フォルダ）が必須だったが、`validate_charter` の一意キーを `path` 単独から
  `(path, base, target)` に拡張。ブランチ違い（例：`main` への修正と `release/1.x` へのバックポート）なら path 無しでも
  別エントリとして成立し、path も branch も全て一致するものだけを曖昧な重複として弾く。charter.md.example / README に
  ブランチ別の書き方を追記、単体テスト 1 件（ブランチ/target での区別）を追加し既存テストを新仕様へ更新。
- 内部リファクタリング（振る舞い不変・全機能維持・226 テスト green）。パッチ的に肥大化した実装を整理:
  重複していた `_pid_alive` 定義を削除、タイムスタンプ整形を `_now_ts()` に集約、kiro-flow コマンド構築の
  重複を `_kf_base` に統一。長い関数を凝集したヘルパに分割（`_settle_task`→review/done/failure、
  `run_loop`→`_run_setup`/`_budget_reason`、`cmd_project`→`_project_evaluate`）。外部挙動・CLI・出力は不変。
- `run` 起動時に、前回の異常終了（`kill -9` / クラッシュ / マシン再起動で `finally` が走らず残った）
  自ホストの死インスタンスレコードを register 前に prune するようにした。`instances` の発見ノイズと
  `start` の偽の重複検出を防ぐ。
- all-daemon の「all」センチネル（実体の無い擬似 root `<container>/projects/all`）を `instances` で
  `all-daemon` 印（`sentinel` フラグ）として表示し、実プロジェクトの監視レコードと明確に区別するように
  した。`projectA/default` 等は実プロジェクトの監視として従来どおり全件表示する。
- バスを明示設定（CLI `--bus` / 設定 `bus:`）したときは **`--project all` でも per-project バスへ上書きせず、
  全プロジェクトでその共有バスを使う**ようにした。従来は `--project all` が常に `<root>/projects/<name>/bus` へ
  上書きしていたため、別途常駐させた**単一の kiro-flow daemon を全プロジェクトから検知できなかった**
  （`location=auto/daemon` が常に local へフォールバック）。共有バスにすると同じ daemon ロックを全プロジェクトで
  参照でき、kiro-flow daemon を同じ bus で起動すれば warm worker を共有・再利用できる（submit の run_id は
  一意採番のため衝突しない）。example / README にも設定方法を追記。

#### Added
- charter の `## repos` / `## links` を**構造化サブ箇条書き**に対応。`- name = url` の下にインデントして
  `- desc:`（説明）/ `- base:`（ベースブランチ）/ `- target:`（ターゲット・既定 base）を付けられる（日本語キー
  desc=説明 / base=ベース / target=ターゲット も可）。複数リポジトリそれぞれに「内容物の説明」と「base/target
  ブランチ」を明示でき、タスクは説明を見て関係する repo を選び、その情報を個別タスク（gitlab イシュー等）へ
  伝搬できる。`## links` は wiki/ドキュメント URL 等も `- desc:` 付きで置ける。
- repos の必須項目検証。charter 駆動の実行開始時（`cmd_project`）に、各 repo の **`desc`（説明）と `base`
  ブランチが必須**であることを検証し、欠けていればエラーで停止して人へ知らせる（`target` は省略可・既定 base）。

#### Changed
- needs（判断待ち）と DELIVERY/archive（受領）の記述を充実化。人が成果物を見に行かずに判断できるよう、
  **「成果物の所在（リポジトリ/ブランチ/コミット・PR/MR）・差分（変更ファイル）・検証結果（PASS/FAIL）」**を
  まとめた「判断材料」を、blocked/review の needs ファイルと archive の納品書に載せるようにした（`delivery_evidence`）。
  DELIVERY.md の成果参照にも所在ブランチを併記。これまで「どこに成果物があるか・何が差分か・なぜ止めた/
  スキップしたか」が分からず判断できなかった問題を解消する。

#### Fixed
- **charter 駆動 watch が kiro-flow run の失敗終了を検知できず execute フェーズで永久待機する不具合を修正**。
  daemon/remote へ submit した run を待つ `_act_submit` は、`result --json` の `done`（＝終端 done/failed の両方）
  だけを見て **failed を success と取り違えて**いた。`status == "failed"` を act 失敗として返すようにし、
  orchestrator が異常終了して daemon が run を `failed` に確定したケースも**1 ポーリングで即検知**して
  `act_timeout` までの空待ちを避ける（verify=NG 相当で後段が retry/エスカレーション）。単体テスト 3 件
  （failed→失敗・done→成功・非終端は act_timeout で必ず返り永久待機しない）を追加。
- charter の `## repos`（対象リポジトリ）/`## links`（参考リンク）が act ワーカーへ渡る文脈
  （`charter_context`/`build_request`）に含まれていなかった不具合を修正。これらは parse 済みだったが
  `_charter_definition` が goal/constraints/assumptions/deliverables しか出力していなかったため、
  gitlab executor のイシュー等で**対象リポジトリ/ブランチ/説明が欠落**していた。goal 直後（truncation で
  落ちにくい位置）に、各 repo の説明・base/target ブランチと関連リンク（desc 付き）を含めるようにした。
- all-daemon の watch ループで heartbeat をラウンド毎に1回だけ更新するよう修正（従来は内側ループに
  あり、登録数 N に対し毎ラウンド N×(N+1) 回の無駄なファイル書き込みが発生していた）。
- `approve` / `hold`（`_block`）で古い claim ロック（`claims/<id>.lock`）を解放するよう修正。worker の
  クラッシュや review/blocked 滞留で残ったロックが人手解決後も残留し、TTL 切れまで次の実行を阻害しうる
  不備を解消（`release_claim` は冪等のため通常ケースは無害）。

### kiro-flow

#### Added
- **gitlab executor を GitLab REST 直叩き（native）化し、起票先 URL を kiro-flow.yaml から
  確実に渡すようにした**。従来は gitlab-idd スキルの外部 `gl.py` を subprocess 起動して
  イシュー化しており、起票先プロジェクトの解決が gl.py 側の `GL_PROJECT_URL`／
  connections.yaml／**git remote origin** フォールバックに依存していた（誤プロジェクトへの
  起票を招きうる）。`gl.py` 相当の必要処理（create-issue / get-issue / get-comments と REST
  呼び出し・ページング）を **stdlib のみ**でプラグインへ移植（`gl_api`/`gl_api_list`/
  `_parse_project_url` 等）。**gl.py への起動・フォールバックは廃止**（native 一本）。
  - **起票先 URL**: kiro-flow.yaml の `gitlab.repo_url` を権威とし、その URL をそのまま使う
    （git remote origin へは流れない）。未設定/解釈不能は明示エラー。
  - **トークン**: kiro-flow.yaml には置かず、**gl.py と同じ場所・同じ優先順**で解決する
    — connections.yaml（接続ラベル `conn_label`・config_loader 経由）→ 環境変数
    `GITLAB_TOKEN`/`GL_TOKEN` → シェル rc ファイル（`~/.bashrc` 等）。秘密情報を設定
    ファイルに残さない運用に合わせた。
  - イシュー操作は `_create_issue`/`_get_issue`/`_get_comments` に集約。kiro-flow.yaml.example /
    CONFIG_DEFAULTS のコメントを更新（`gitlab.token` は設けない）。
  単体テスト 20 件（起票/ポーリング/承認・クローズ完了・タイムアウト・repo_url 必須/SSH 拒否・
  URL 解析・REST リクエスト組立・HTTP エラー処理・トークン解決の優先順＝connections.yaml＞
  環境変数＞シェル rc、および kiro-flow.yaml の token を読まないこと）を追加。
- **自動アップデート（既定 on・6 時間毎・起動直後にも実施）**。スキルリポジトリ（配布元）の `main` に更新が
  入ったら、**daemon のアイドル時**（要求も子プロセスも無いとき）に取り込む。停止中に入った更新も起動直後に拾う。doctor と同じ流儀で決定的:
  `git ls-remote` で main の先頭を確認 → 適用済み SHA（`~/.kiro/kiro-flow.update.json`）と違えば、temp
  領域へ `tools/kiro-flow/` だけを **sparse-checkout** → `install.sh` 実行 → **動いていた cwd のまま
  `os.execv` で graceful 再起動**（子の terminate と daemon ロック解放を経て再起動）。手動は
  `update [--check|--now]`。**更新元 URL は `install.py` が生成する `skill-registry.json`
  （`repositories.origin.url` → `install_dir`）から自動解決**（`update_repo` 未指定でよい）。設定キー
  `update_enabled`（off スイッチ・既定 on）/ `update_check_interval`（既定 21600=6h） / `update_repo` / `update_branch` /
  `update_subdir` / `update_installer`。初回はベースライン記録のみ（無更新）。仕事中は何もしない。
  単体テスト `SelfUpdateTests`（11 件）を追加。
- `doctor` サブコマンド。run 状態/イベント/環境から稼働を診断し、原因を **env / config / program** に
  分類する。収集・修正・起票の駆動は決定的に、診断と分類は kiro-cli へ委譲（不在時は決定的チェックのみ）。
  `--fix` で env/config を修正（`ensure-bus`＝バス作成）し、program の不具合は `gitlab-idd` スキルで
  GitLab イシューを起票する（スキルが無ければ出力のみ）。`--json` の findings は kiro-projects の doctor と
  同一スキーマで、単独でも kiro-projects からの連携呼び出しでも使える。終了コード `0`/`1`/`2`。
- executor（ワーカーバス）のプラグイン化。kiro-loop の hooks（event_hook）と同じ流儀で、
  `--executor` に組み込み名（`kiro`/`stub`）に加えてプラグイン名（例 `gitlab`）や `.py` パスを
  指定できる。プラグインは標準ライブラリのみの単一ファイルで `execute(kind, goal, dep_results,
  model, art_dir, dep_arts)` を公開し、本体が `importlib` で動的ロードする（mtime キャッシュ付き）。
  検索順は スクリプト同階層 `executors/` → リポジトリ `tools/kiro-flow/executors/` →
  `~/.kiro/kiro-flow/executors/`（インストーラ配置）→ 設定 `executor_dir`。プラグイン固有設定は
  同名のトップレベル設定ブロックを JSON 化し環境変数 `KIRO_FLOW_EXECUTOR_CONFIG` で渡す。
  `install.sh` は同梱プラグインを `~/.kiro/kiro-flow/executors/` へコピーする。
- gitlab ワーカーバス（opt-in・`executors/gitlab.py` プラグイン）。`--executor gitlab` /
  設定 `executor: gitlab` を選ぶと、各ワーカータスクを gitlab-idd スキルの `gl.py` で GitLab
  イシュー化して委譲し、リモートのワーカーが実装・レビュアーが承認した結果を `get-issue` で
  ポーリングする。`status:approved`（または `status:done` / クローズ）に達したらそのタスクを
  完了とみなす。ポーリング間隔・タイムアウト・付与ラベルは設定 `gitlab:` ブロックで調整可。
  既定の executor は `kiro` のままで、明示選択時のみ有効になる。
- 作業後に sparse-checkout クローンを自動削除（既定 ON）。各コマンド終了時に
  ノード専用クローンを丸ごと掃除しクローンの溜まり込みを防ぐ。`--keep-clone` /
  設定 `cleanup_clone: false` で従来どおり残して再利用も可能。
- 中間成果物のファイル参照プロトコル。`output`/`data` に乗らない大きな成果物は
  決定的なディレクトリ `runs/<run-id>/artifacts/<node-id>/` に書き出し、後続タスクは
  依存ノードの同じパスを読んで発見できる。ワーカーは生成した成果物を result に記録し、
  `result` コマンドでも一覧できる。

#### Fixed
- **stub プランナーが構造化された複数行の要求を 1 行ずつ別タスクへ細切れにし、charter の
  対象リポジトリ一覧などが gitlab executor の各イシューのタイトル/目的を埋める不具合を修正**。
  kiro-cli が無い委譲シナリオでは LLM プランナーが `plan_stub` にフォールバックするが、`plan_stub`
  は改行をすべてタスク境界として扱っていたため、`build_request` が組み立てる charter 文脈（目標・
  完了条件・**対象リポジトリ行**・制約…）の 1 行 1 行が別タスク＝別イシューになり、repos の内容が
  タイトルや各節に繰り返し現れていた。改行は**空行を含まないフラットな簡易リストのときだけ**区切りと
  みなすようにし（`"\n\n"` を含む構造化要求は 1 件の要求として扱う）、見出し（タイトル相当）は
  `_first_line` で**先頭の非空行**に統一して本来の目的が 1 行で読めるようにした。明示区切り
  `;`/`->` は従来どおり。回帰テスト 6 件（簡易リストは従来どおり分割・構造化要求は細切れにしない・
  既定 fan-out パターンでも goal に repos が出ない・`_first_line`）を追加。
- **成果物リポジトリの clone 指示が goal 先頭に結合され、gitlab executor のイシュー タイトル/目的が
  指示テキストで埋まって本来の goal が見えなくなる不具合を修正**。`cmd_work` が clone 指示
  （`repo_instruction`）を `goal` の先頭へ文字列結合してから executor に渡していたため、gitlab は
  タイトル（`goal[:80]`）も本文の『## 目的』も clone 指示で占有されていた。executor 契約に任意の
  `repo_instruction` 引数を追加し、新設の `call_executor` が **clone 指示を goal とは別引数で渡す**
  ように変更（受け取れない旧プラグインには従来どおり goal 先頭へ結合してフォールバック＝後方互換）。
  `execute_kiro`/`execute_stub` は `repo_instruction` を受理（kiro はプロンプトへ別途付与）。gitlab は
  タイトルと『## 目的』に**本来の goal のみ**を出し、clone 指示は本文の独立節『## 成果物リポジトリ』に
  載せる。単体テスト 8 件（引数受理判定・新/旧 executor の分岐・kiro プロンプト・イシュー本文の節分離・
  タイトル/目的が本来の goal）を追加。
- **daemon が再起動すると孤児 run（owning daemon が消失した非終端 run）を復旧できず永久待機する不具合を修正**。
  上記の異常終了検知は「死んだ子（orchestrator）を自分で刈り取れる」前提で、**daemon プロセス自体が落ちて
  再起動した**ケース（remote/分散実行）を救えていなかった。再起動した新プロセスは `orchestrators` を引き継がず、
  前プロセスが残した `status:running` を見て `run_exists` で受理をスキップするだけ＝何もせず、remote へ
  `submit` した消費者は `act_timeout`（既定 1800s）まで待たされていた。**run 生存リース（heartbeat）**を導入し、
  daemon は駆動中の run の `meta` に `orch_lease_until`/`heartbeat_at` を毎 poll 更新（git バスへは間引いて push）。
  各 poll で **inbox 由来・自分が回しておらず・リース切れ**の run を `mark_run_failed` で `failed` 確定する
  （`Bus.touch_run`/`run_is_orphaned`/`_recover_orphan_runs`、リース窓 `_run_lease_window` ＝ `max(poll×10, 120s)`）。
  リース未記録の旧 run／heartbeat 前に死んだ run は作成 age で判定し、作成直後の run は孤児扱いしない（spawn 直後の
  race と他デーモンの生存 run の誤回収を防止）。これで再起動／別デーモンが ~リース窓内に run を `failed` 化し、
  消費者（PR の `_act_submit` 失敗検知と連携）が `act_timeout` を待たず復旧できる。単体テスト 11 件を追加。
- **daemon が orchestrator の異常終了を run の失敗として確定せず、run が非終端のまま放置される不具合を修正**。
  orchestrator（`orchestrate`）が `done` を書く前にクラッシュ／kill／起動失敗で終了すると、daemon は死んだ子を
  `del orchestrators[rid]` するだけで run の `status` を更新せず、`result`/`status` を待つ消費者
  （kiro-projects の charter 駆動 watch 等）が**永久待機**に陥っていた。死んだ orchestrator を刈り取る際に
  exit code を確認し、run がまだ終端でなければ `Bus.mark_run_failed` で `failed`（`failure_reason` 付き）に
  確定して `run-failed` イベントを記録・push するようにした（正常完了済みの run は上書きしない冪等動作）。
  これで `result --json` の `done=True`/`status=failed` として失敗終了が即座に消費者へ伝わる。単体テスト 4 件を追加。
- gitlab executor プラグインで、イシューの起票先が設定 `gitlab.repo_url` にならず git remote origin に
  フォールバックする不具合を修正。`run`/`daemon` が子プロセス（orchestrator/worker）へ **`--config` を
  引き継いでいなかった**ため、実際に `execute()` を呼ぶ worker が `gitlab:` ブロック（`repo_url` 含む）を
  再解決できず既定（空）になっていた。親が解決した設定パスを絶対パスで全子プロセスへ伝搬するようにした
  （プラグイン固有設定全般に効く）。
- 上記をさらに堅牢化: daemon が worker を起動する `_spawn_worker` で、親（daemon）が解決した executor
  プラグイン設定ブロック（例 `gitlab:` の `repo_url`/`conn_label`）を **`KIRO_FLOW_EXECUTOR_CONFIG` として
  worker の起動 env に明示注入**するようにした。worker が `--config` を再解決できない／別の設定ファイルを
  拾う状況でも親の設定が確実に届く。解決ロジックを `resolve_executor_config_json(args)` に集約し
  `make_executor` と共有。worker 側 `make_executor` は自分で設定を解決できたときだけ env を更新し、
  解決できない（空/None）ときは親が注入した値を上書きしない。テスト 4 件を追加。
- judge/評価役のサーキットブレーカー。同一系統の作り直し（verify=fail の再生成・
  失敗タスクの retry）が `--max-retries`（設定 `max_retries`, 既定 3）に達したら
  打ち切る。達成不可能な完了条件に対し無限に再タスクを積み続ける暴走を防ぐ
  （`--max-iterations` と二重ガード）。
- 依存タスクの成果物が大きいとき、kiro-cli へ渡すプロンプトが OS のコマンドライン長
  制限（ARG_MAX）に達して起動失敗する不具合を修正。一定サイズを超えるプロンプトは
  一時ファイルへ退避し参照渡しに切り替える（設定 `argv_limit` / `--argv-limit` で調整、既定 100000）。
- `GitBus._ensure_clone()` の sparse-checkout が親リポジトリに作用しうる不具合を修正。クローン先
  （`<bus>/<node>`）が親リポジトリの作業ツリー配下にある場合、workdir 直下に自前の `.git` が無いと
  git が親へ遡って最寄りの `.git` を掴み、`sparse-checkout` が**親リポジトリの作業ツリーを cone 化して
  隠してしまう**ことがあった。再利用は「`self.remote` を origin とする自前クローンのルート」に限定し、
  それ以外（親/別リポジトリ・非空の他ディレクトリ）には sparse-checkout を適用せず明示的に中断する。
- `GitBus` が **同一 remote の既存フルチェックアウト**（ユーザーの作業リポジトリ等）を `--bus` のクローン先に
  指定された場合に、`sparse-checkout`（cone）で **subdir 以外の追跡ファイルを作業ツリーから隠してしまう**
  不具合を修正（kiro-projects の `--git-bus`/`--git-subdir` 経由で発生しうる）。自前管理のバスクローンに
  目印（git config `kiro-flow.busclone=1`）を付け、再利用は「目印付き／既に sparse 済みの自前クローン」に
  限定。kiro-flow 管理外の既存チェックアウトには sparse-checkout せず明示的に中断する。あわせて全ての
  `git -C <workdir>` 実行に `GIT_CEILING_DIRECTORIES` を設定し、workdir 直下に `.git` が無くても親リポジトリへ
  遡れないよう多重防御した。

#### Added
- daemon/submit の黒箱統合テスト（`DaemonE2ETests`）。`daemon` を実プロセスとして常駐させ、`submit` 投入から
  orchestrator/worker のオンデマンド起動を経て `final.json` 生成（全ノード done）まで通す。複数 submit を
  並行に独立 run として完走させる経路も検証。bus プリミティブの in-process テスト（`DaemonPrimitiveTests`）に
  対し、常駐プロセス＋オンデマンド起動の配線を実プロセスで担保する。

#### Changed
- 内部リファクタリング（振る舞い不変・全機能維持・144 テスト green）。kiro-projects と同様に、
  パッチ的に重複した実装を整理: 子プロセス argv 構築を `_child_base()` に統一（`cmd_run`/`cmd_daemon` の重複解消）、
  モード表記を `_mode_string()` に集約、daemon の singleton ロック取得を `_acquire_daemon_lock()`・
  orchestrator/worker 起動を `_spawn_orchestrator()`/`_spawn_worker()` に分割、`cmd_orchestrate` の統合処理を
  `_finalize_run()` に分割。CLI・出力・挙動は不変（argparse は `--model`/`--model_opt` 等の差があるため共通化せず温存）。
- `install.sh` の executor プラグイン配置先を **本体（kiro-flow バイナリ）と同じフォルダ**
  （`<install-prefix>/executors/`、既定 `~/.local/bin/executors/`）に変更（旧: `~/.kiro/kiro-flow/executors/`）。
  kiro-loop と同じ「本体隣」の補助アセット配置に揃え、検索順 #1「スクリプト同階層の `executors/`」で
  名前解決できるようにした。`~/.kiro/kiro-flow/executors/` は後方互換の検索先として残す。

---

## [v1.0.0] — 2026-06-20

Initial release. 188 tests passing (kiro-flow + kiro-projects).

### kiro-projects

#### Added
- 並列消費 — kiro-flow の worker 並列へ寄せる（§11）
- 共有レジストリ越しの別ホスト発見（§11-7）
- 汎用の取り込み口 enqueue / inbox（§11-5）
- 常駐ライフサイクル start / stop / restart（§11-4）
- 自律裁定の判断材料を拡充（§11-3）
- 真偽フラグを設定ファイル対応（§11-1）
- コスト予算（トークン/金額の上限と per-task 計上）（§11-2）
- Loop Engineering 中核4機能（計測・自己生成・依存・回帰ゲート）
- 検収ゲート — verify=PASS でも人の承認を要する review 状態
- 自律裁定フック（needs 直前で kiro-cli が積み直し可否を判断）
- 設定ファイル対応（YAML 任意 / JSON フォールバック）＋サンプル
- 稼働インスタンスのレジストリ追加＋スキルを WSL/Windows 対応に
- サブコマンド省略時を `run --watch`（常駐監視）の既定に
- ltm-use への学習昇格（プロジェクト横断・エージェント不要）
- 編集完了の明示検知と成果物の納品書
- ファイルを `.kiro-projects/` に集約・一時バスを自動クリーンアップ
- DR 学習と rot 検知

#### Changed
- `auto_adjudicate` の既定を on に変更

### kiro-flow

#### Added
- flow-planner をデフォルト planner に変更し `~/.kiro/skills` のフォールバック追加
- flow-planner スキル — kiro-flow orchestrator 向け 3 フェーズパイプライン
- タスクタイムアウト機構（kiro-cli 呼び出しの無限ハング防止）
- 最終結果プレゼンテーションとコマンドアップデート
- 一時ファイルの自動クリーンアップ

---

[v1.0.0]: https://github.com/ynitto/sandbox/releases/tag/v1.0.0
