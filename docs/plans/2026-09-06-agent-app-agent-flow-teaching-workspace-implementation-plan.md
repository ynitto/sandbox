# agent-app agent-flow ティーチング・ワークスペース実装計画

## 元のタスク

> agent-appステートマシン作成の簡便化と同じようにagent-flowワークフローを実施したい。ステートマシンと違う点を洗い出し作成や編集の考え方を同じにする。UXはできるだけ合わせたい。

追加決定:

- 代表入力による実際の試運転と利用者承認を `利用可能` の必須条件にする。
- agent-flow は定型タスクではないため、目的、制約、品質基準を固定しつつ、分解と再計画の汎用性を保つ。
- DAG エディタの「差し戻し」は、後工程から前工程へ戻る線を指す。通常の依存 edge とは分け、実行時は DAG を保ったまま再計画する。

設計: `docs/plans/2026-09-06-agent-app-agent-flow-teaching-workspace-design.md`

## 実装原則

- `tools/statemachine-maker` を agent-flow 作成機能の正典とし、agent-app は既存の npm dependency、IPC prefix、vendor 変換で取り込む。
- `.agents/workflows/<id>.json` は agent-flow と agent-dashboard が読む標準定義のまま維持する。
- 教示会話、適応型仕様、候補世代、試運転参照は `.agents/workflows/.teaching/<id>.json` に分離する。
- AI は候補を返すだけとし、正規化、検査、保存、昇格は決定的な main-process module が行う。
- 通常依存 `deps` は循環させない。差し戻しは明示的な再作業ポリシーとして検証し、実行時に replacement node を生成する。
- 既存の task teaching、flow 実行、agent-dashboard 読み取り、会話領域を回帰させない。

## 分解した ToDo リスト

### 1. 既存テストのベースラインを固定する

What: 変更前の statemachine-maker、agent-app、agent-flow の関連テスト結果を確認し、既存 flow fixture と teaching fixture を再利用できる形で整理する。  
Where: `tools/statemachine-maker/test/`、`tools/agent-app/test/`、`tools/agent-flow/tests/`。  
How: maker と app の `npm test`、agent-flow の user plan、plan gate、workflow schema テストを実行する。現在の標準定義、run snapshot、task teaching の期待値を変更前 fixture として扱う。  
Why: 三つの実行境界をまたぐ変更で、既存の手動 DAG、実行、タスク作成のどこを壊したか判別できるようにするため。  
完了条件: `tools/statemachine-maker` と `tools/agent-app` の `npm test`、`tools/agent-flow` の `python3 -m unittest tests.test_user_plan tests.test_plan_gate tests.test_workflow_schema` が成功するか、環境依存 skip の理由が記録されている。

### 2. 再作業ポリシーの共有契約を追加する

What: 差し戻し元、論理的な再開地点、発火条件、理由、最大回数、上限後の動作を表す後方互換な契約を定義する。  
Where: `schemas/agent-workflow.schema.json`、`tools/agent-flow/tests/test_workflow_schema.py`。  
How: library workflow と投入 plan に任意の `rework` 配列を追加する。初版の trigger は `human-rejected` と `verification-failed` に限定し、`on_exhausted` は `human`、`fail`、`continue` の列挙にする。既存 JSON は追加項目なしで従来どおり通す。  
Why: UIだけの戻り線にせず、保存、実行、他の読み手が同じ意味を共有するため。  
完了条件: 正常な二種類のポリシーと、不明 trigger、回数ゼロ、不明 node、前方を指す再開地点を含む schema fixture が期待どおり合否判定される。

### 3. agent-flow の user plan 検証へ再作業ポリシーを追加する

What: 投入された `rework` を厳格に検証し、実行 strategy へ保持する。  
Where: `tools/agent-flow/agent_flow/patterns.py` の `plan_strategy_user` 周辺、`tools/agent-flow/tests/test_user_plan.py`。  
How: node のトポロジカル順を使い、from/to の実在、from の kind、to が前の論理工程であること、組み合わせの一意性、有限回数を検査する。不正な値を丸めず `UserPlanError` にする。通常の `deps` 循環検査は変更しない。  
Why: maker を経由しない plan-file や inbox でも同じ安全境界を守るため。  
完了条件: 不正な差し戻しが planner fallback せず `[user-plan]` で失敗し、正常なポリシーが strategy に正規化保存されるテストが通る。

### 4. agent-flow の差し戻し発火と上限処理を実装する

What: human rejection または verification failure を検出し、理由と既存成果を含む replacement plan を生成する。  
Where: `tools/agent-flow/agent_flow/orchestrate.py`、必要に応じて `patterns.py` / `continuation.py`、`tools/agent-flow/tests/test_plan_gate.py` と新規 `tests/test_rework_policy.py`。  
How: 既存の plan-gate 差し戻し、`replaces`、`_plan_changes`、iteration 記録を再利用する。戻り先以前の確定成果は保持し、戻り先以降を新 node へ置換する。event に policy id、from、to、理由、attempt、changes を残す。  
Why: 循環 edge を実行せず、追跡可能な DAG の世代として再作業するため。  
完了条件: rejection と verification failure の双方で新 node が生成され、旧 node に循環 edge が追加されず、run が継続するテストが通る。

### 5. agent-flow の差し戻し上限後の三動作を実装する

What: 最大回数到達後の `human`、`fail`、`continue` を決定的に処理する。  
Where: `tools/agent-flow/agent_flow/orchestrate.py`、`tools/agent-flow/tests/test_rework_policy.py`。  
How: `human` は最終判断用 interaction を生成し、`fail` は理由付きで終端し、`continue` は失敗を黙殺せず waiver event と結果注記を残して依存を解決する。resume 時に attempt がリセットされないよう graph strategy を正典にする。  
Why: 無限再計画を防ぎ、利用者が選んだ終了方針を監査可能にするため。  
完了条件: 三動作、resume、上限境界、二つのポリシーが同時に存在するケースのテストが通り、`max_iterations` と別カウンターとして動く。

### 6. maker の flow model に標準定義と差し戻し検査を実装する

What: `rework` の正規化、表示用 issue、digest、plan 変換を追加する。  
Where: `tools/statemachine-maker/src/main/flow-model.js`、`test/flow-model.test.js`。  
How: agent-flow の検証条件を保存前に先取りし、通常 node と再作業ポリシーを別配列で扱う。definition/digest には実行意味を持つ `rework` を含め、x/y など表示座標は引き続き除外する。  
Why: AI候補や手動編集を保存する時点で、実行できない差し戻しを利用者へ返すため。  
完了条件: 正常化、往復、digest 変化、通常 `deps` 非循環、差し戻し issue の単体テストが通る。

### 7. agent-dashboard の読み取り互換を更新する `[並列可: Task 6 と契約確定後]`

What: 新しい任意 `rework` を落とさず読み、実行 plan へ渡せるようにする。  
Where: `tools/agent-dashboard/src/features/adhoc-flow/main/adhoc.js` と関連テスト。  
How: `normalizeWorkflow`、`workflowDefinition`、plan 変換へ共有契約と同じ正規化を追加する。旧定義と rework なしの digest は変えない。リポジトリ共有フローを読み取り専用にする既存方針は維持する。  
Why: agent-app で作成した定義を dashboard から実行した場合にも差し戻し意味を失わないため。  
完了条件: 旧 fixture の digest が不変で、新定義を一覧、preview、投入 plan まで往復できるテストが通る。

### 8. agent-flow 教示セッションの純粋モデルを作成する `[並列可: Task 7]`

What: status、messages、evidence、understanding、generations、trials を正規化するモデルを追加する。  
Where: 新規 `tools/statemachine-maker/src/main/flow-teaching-model.js`、新規 `test/flow-teaching-model.test.js`。  
How: 既存 `teaching-model.js` の status 遷移、redact、generation、trial、confirm、restore のパターンを再利用し、understanding を purpose、scope、inputs、outputContract、constraints、nonGoals、decompositionPolicy、replanningPolicy、humanCheckpoints、qualityCriteria、unknowns に置き換える。  
Why: ステートマシンと同じライフサイクルを持ちながら、固定操作ではなく適応型ワークフローを教えるため。  
完了条件: 正規化、秘密除去、候補追加、成功試運転、承認、意味変更時の再試運転化、成功版復元の単体テストが通る。

### 9. 教示 sidecar store を追加する

What: `.agents/workflows/.teaching/<id>.json` の原子的な作成、一覧、読込、保存を実装する。  
Where: 新規 `tools/statemachine-maker/src/main/flow-teaching-store.js`、新規 `test/flow-teaching-store.test.js`。  
How: `flow-store.js` の root/id 境界と `teaching-store.js` の atomic write を組み合わせる。sidecar だけの下書きも一覧へ返し、標準定義の JSON 列挙には混ざらないことを固定する。symlink と不正 id は既存 root guard の内側でも拒否する。  
Why: 作成途中の会話を保持しつつ、agent-dashboard の `*.json` 読み取り契約を汚さないため。  
完了条件: 下書きのみ、既存定義あり、壊れたsidecar、不正パス、原子的更新のテストが通る。

### 10. 適応型仕様から制御骨格をコンパイルする

What: AIの意味仕様と候補を、検査可能な標準workflowへ変換する決定的な境界を追加する。  
Where: 新規 `tools/statemachine-maker/src/main/flow-teaching-compiler.js`、新規 `test/flow-teaching-compiler.test.js`、`flow-model.js`。  
How: AI候補のnodeを無条件で信用せず、単純な `work`、動的 `split`、統合、検証、human、rework の組み合わせを正規化する。分割の必要性が説明されない場合は過剰な骨格を issue にする。最終判定は `flowModel.preview` に委譲する。  
Why: 汎用性を保ちながら、AI出力を直接ファイルへ保存しないため。  
完了条件: 単純、fan-out、レビュー、差し戻しのgolden fixtureと、過剰分割、統合なし、無制限差し戻しの失敗テストが通る。

### 11. agent-flow 教示用 AI prompt と応答契約を追加する

What: agent-flow専用の質問、理解更新、候補生成、修復を実装する。  
Where: `tools/statemachine-maker/src/main/ai.js`、`src/main/ipc.js`、`test/ai.test.js`。  
How: `mode: flow-teach` を追加し、ステートマシンの操作記録や重要操作ではなく、依頼例、成果例、適用範囲、制約、分解、再計画、human checkpoint、品質基準を質問する。AI出力はcompilerとflow modelを通し、不正時の修復は一回に限定する。  
Why: UIを揃えても意味モデルを混ぜず、agent-flow向けの柔軟な候補を得るため。  
完了条件: 質問のみ、候補生成、既存定義からの変更相談、秘密情報、不正JSON、一回修復のテストが通る。

### 12. 候補の試運転・評価・昇格を実装する

What: 未承認 generation を一時 plan として agent-flow で実行し、run結果をtrialへ記録して、利用者承認後だけ標準定義へ昇格する。  
Where: 新規 `tools/statemachine-maker/src/main/flow-teaching-trial.js`、`src/main/agent-flow.js`、`src/main/ipc.js`、対応テスト。  
How: 既存 `flow:run:start/read/result` を再利用し、trial印とgeneration digestをsubmitter contextへ付ける。品質評価は必須基準ごとの観測を保存し、run全文は複製しない。confirm時にdigestを再照合してから `flowStore.save` する。  
Why: 試運転した候補と通常実行する定義の取り違えを防ぐため。  
完了条件: draft実行、失敗、確認待ち、品質未達、成功、digest競合、承認昇格、通常実行が最後の承認版を使うテストが通る。

### 13. IPC と preload の教示契約を公開する

What: flow teaching の list/create/read/save/evidence/trial/confirm/restore とAI進捗を maker と agent-app に公開する。  
Where: `tools/statemachine-maker/src/main/ipc.js`、`src/preload.js`、`test/preload-contract.test.js`、`tools/agent-app/src/preload.js`、agent-appの関連テスト。  
How: 既存 `teaching:*` と名前を衝突させず `flow:teaching:*` に揃える。すべて selectedRoot/root registration guard を通し、agent-app では既存 `automation:` prefix を使う。  
Why: rendererからファイルパスや実行コマンドを直接扱わせないため。  
完了条件: maker直結とagent-app埋め込みの全チャネル対応、未登録root拒否、payload上限の契約テストが通る。

### 14. agent-flow ティーチング画面を作成する

What: 会話、AIの理解、証拠追加、試運転、結果承認、変更相談を一つのfeatureとして描画する。  
Where: 新規 `tools/statemachine-maker/src/renderer/flow-teaching.js`、`src/renderer/flow.js`、`renderer.js`、`index.html`、対応 renderer テスト。  
How: `teaching.js` のレイアウトと四状態を踏襲し、右欄はagent-flow専用項目にする。操作記録の代わりに依頼例、成果例、参考ファイル、過去runを追加する。最終成果を先に表示し、戦略とnodeは詳細へ畳む。  
Why: ステートマシンと学習コストを揃えながら、固定手順を教える誤解を避けるため。  
完了条件: 新規作成、質問回答、証拠追加、試運転、修正相談、承認、既存flow変更のDOMテストが通る。

### 15. DAG エディタに通常線と差し戻し専用レーンを描画する

What: 高度な編集をレイヤー化DAGへ更新し、後工程から前工程への差し戻し線をグラフ外側に描く。  
Where: `tools/statemachine-maker/src/renderer/flow.js`、`styles.css`、必要なら新規純粋layout moduleと単体テスト。  
How: 通常edgeは上から下、差し戻しedgeは外側の直交レーンへ割り当てる。線種、矢印、`差し戻し` / `再開`、理由、最大回数、凡例を表示する。複数線は安定したlane indexで配置し、SVG要素をキーボード選択可能にする。  
Why: 戻り線がnodeや通常線を横切って読めなくなる従来の問題を解消するため。  
完了条件: 一本、入れ子、同一始点、同一終点、複数差し戻し、node追加削除後のgolden layoutとキーボード操作テストが通る。

### 16. DAG 差し戻し設定パネルを実装する

What: 差し戻し元、再開地点、発火条件、理由、最大回数、上限後の動作を文章として編集できるようにする。  
Where: `tools/statemachine-maker/src/renderer/flow.js`、`styles.css`、`test/app.test.js`。  
How: node idやreworkという内部語を標準表示に出さず、「もし検証に通らなければ、調査から最大2回やり直す」の形で編集する。削除nodeを参照する設定は保存前issueから対象へフォーカスする。  
Why: 線の見た目だけでなく、終了条件を利用者が理解して設定できるようにするため。  
完了条件: human rejection、verification failure、三種類の上限後動作、無効な戻り先の修正導線をUIテストで確認できる。

### 17. agent-app の一覧と埋め込み表示へ統合する

What: 教示下書きと四状態を左のワークフロー一覧へ反映し、作成操作を新しいティーチング画面へ送る。  
Where: `tools/agent-app/src/renderer/navigation.js`、`renderer.js`、`automation-frame.html`、`scripts/vendor.js`、`test/app.test.js`、`test/electron-smoke.test.js`。  
How: taskの `taskItems` と同様に、標準flow一覧とsidecar下書きを重複なく統合する純粋関数を追加する。`flow-teaching.js` をvendor対象に加え、親子postMessageはroot、area、intent idを検証する。  
Why: maker単体だけでなく、主要利用先のagent-appで同じ作成体験を成立させるため。  
完了条件: 下書き、試運転必要、確認待ち、利用可能の一覧表示、作成、選択復元、repo切替、埋め込み画面のsmokeが通る。

### 18. レスポンシブ表示とアクセシビリティを固定する `[並列可: Task 17 後のUI仕上げ]`

What: 教示画面とDAGエディタを720px、980px、1440pxで検証し、操作名、focus、live regionを整える。  
Where: `tools/statemachine-maker/src/renderer/styles.css`、`tools/agent-app/src/renderer/automation-frame.css`、UIテスト。  
How: 狭幅では会話、理解、試運転結果をタブ化し、DAGと設定を一画面ずつ表示する。線選択にはテキスト代替と可視focusを付け、色だけに依存しない。  
Why: ステートマシンと同じUX原則を画面幅や入力手段にかかわらず維持するため。  
完了条件: 三幅で中央領域の横スクロール、文字切れ、固定要素の重なりがなく、キーボードだけで主要操作と差し戻し編集を完了できる。

### 19. README と設計参照を更新する `[並列可: Task 18]`

What: 新しい作成・変更・試運転・差し戻しの利用方法と保存場所を利用者向けに記載する。  
Where: `tools/statemachine-maker/README.md`、`tools/agent-app/README.md`、必要に応じて `tools/agent-flow/README.md`。  
How: node kindやsidecarの説明を通常手順へ出しすぎず、通常利用と高度な編集を分ける。差し戻しは「前の工程へ戻って再計画」と説明し、循環depsではないことを高度な説明へ置く。  
Why: UIとドキュメントの用語を一致させ、既存の手動作成手順との混同を防ぐため。  
完了条件: READMEの操作手順だけで新規作成、試運転、承認、変更相談、差し戻し設定まで辿れる。

### 20. 全体回帰と実機確認を行う

What: 三プロジェクトの単体・統合・Electron実機テストを通し、受け入れ条件を確認する。  
Where: `tools/agent-flow`、`tools/statemachine-maker`、`tools/agent-dashboard`、`tools/agent-app`。  
How: agent-flow全テスト、maker/appのnpm test、dashboard関連テスト、利用可能ならElectron smokeを実行する。実際の代表flowを一件、作成→試運転→差し戻し→再計画→承認→通常実行まで通す。  
Why: 個別契約が通っても、vendorされた画面、IPC prefix、run busを通した統合でだけ起きる不具合を検出するため。  
完了条件: 自動テストが成功し、受け入れ条件の実機チェック結果と環境依存skipが記録され、`git diff --check` が成功する。

## 依存関係

```text
1 ベースライン
  ↓
2 共有契約
  ↓
3 user plan検証
  ├─→ 4 差し戻し再計画 → 5 上限後動作
  └─→ 6 maker flow model ─→ 7 dashboard互換
                         └─→ 8 教示モデル → 9 sidecar store
                                           ↓
                                      10 compiler
                                           ↓
                                      11 AI契約
                                           ↓
                                      12 trial・昇格
                                           ↓
                                      13 IPC/preload
                                           ↓
                                      14 教示画面
                                           ├─→ 15 差し戻し描画 → 16 設定パネル
                                           └─→ 17 agent-app統合
                                                     ↓
                                           18 UI仕上げ ─┬─ 19 docs
                                                        ↓
                                                    20 全体回帰
```

Task 7 と Task 8、Task 18 と Task 19 は、それぞれ前提タスク完了後に並列実行できる。Task 4〜5 は agent-flow の実行意味を固定するため、UI実装より先に完了させる。

## リスク順

1. **差し戻しの実行意味**: `continue` 時の依存解決、resume後の回数保持、複数policy競合をテスト先行で固定する。
2. **試運転候補と正式定義の取り違え**: generation digestをsubmitとconfirmの双方で照合する。
3. **既存定義互換**: optional fieldなしのnormalize結果とdigestをgolden fixtureで固定する。
4. **グラフ配置**: 描画と編集状態を分け、純粋layout関数のgolden testを先に作る。
5. **vendor差異**: maker単体だけで完了とせず、agent-appのpostinstall生成物とElectron smokeを必ず確認する。

## オープンクエスチョン

なし。次の判断は設計で確定している。

- 試運転と利用者承認は必須。
- 固定するのは目的、境界、確認条件、品質基準であり、実行nodeは適応可能。
- 差し戻しは循環depsではなく、外側レーンに表示する有限回の再作業ポリシー。
- agent-flowの標準定義とagent-dashboard互換を維持する。
