# codd-gate × agent-flow 統合刷新 設計

> 作成: 2026-08-13 ／ 関連: [`docs/designs/codd-gate-design.md`](../designs/codd-gate-design.md)（codd-gate 正典）,
> [`docs/designs/agent-flow-design.md`](../designs/agent-flow-design.md),
> [`docs/specs/agent-flow-spec.md`](../specs/agent-flow-spec.md) §3.1〜3.3,
> `tools/agent-dashboard/src/features/adhoc-flow/`
>
> 柱2 / C3・C5 — ドリフト修復を「事後に人へ送る」から「run 内の機械的な自己修復」へ移し、
> 決定的ゲートはどの投入経路でも迂回させない。

## 結論

新しい機構は作らない。codd-gate は独立 CLI のまま一切変えず、**2026-08 に agent-flow へ増えた
3 つの既存の口**へ載せる。

| 口 | 載せるもの | 効き方 |
|---|---|---|
| ① 検証計画（`verification_plan.commands`） | `codd-gate verify --base "$AGENT_BASE_REV"` | 差分ゲートが run 内で fail → `verify-fix` ノードが自己修復 → 再検証。receipt が証跡 |
| ② 手法パック（methods） | ドキュメント追随・接続注釈の worker 指示 2 件 | ドリフトの**予防**。Amber/Gray を発生源で減らす |
| ③ カスタムフロー（リポジトリ配布 workflow） | 負債掃除フローの参考実装 1 本 | agent-project 常駐の無い端末での単発返済（限定採用） |

2026-08-02 の集約判断（共通チェック 1 本・専用配線の撤去・「agent-flow に差し込まない」）は
**趣旨を維持したまま結論だけ更新する**。当時「将来必要になれば `gate_cmd` を E2 の相似形として
足す道はある」と留保した受け皿が、その後の統一 verify（P1-A）で**既に実装済み**になった——
検証計画は依頼側（制御層）が digest 付きで確定する契約であり、エンジンに独自ゲート機構を
新設せずに決定的コマンドを run 内で走らせられる。新設不要になったから使う、が本設計の骨子。

## 背景と課題

現行の連携（codd-gate-design.md §4.3）は「共通チェック 1 本」に集約済みで、それ自体は保つ。
問題は 3 つ、いずれも**集約後に agent-flow 側が拡張された**ことで生じた。

1. **ゲートが run の外・事後にしかない。** `regression_cmd` は settle 後に workdir で走る
   （`mr.py` の回帰ゲート）。ドリフトを見つけても needs（人の判断待ち）行きで、run 内の
   verify-fix 自己修復ループには乗らない。機械で直せる欠陥が人へ届くのは C3 に反する。
2. **新しい投入経路がゲートを通らない。** adhoc-flow（dashboard のクイック実行）・カスタム
   フロー・板委譲の standalone run は、昇格して agent-project のタスクになるまで codd-gate を
   一度も通らない。2026-08-02 の「単体利用はシェル合成（`agent-flow run … && codd-gate verify`）で
   足りる」という前提は、dashboard が nohup で切り離し起動する現在の形では成立しない。
3. **差分ゲートの実行場所が実測で壊れていた。** `regression_cmd` に書いた
   `codd-gate verify --base $AGENT_BASE_REV --repos repos.json` は、cwd が workdir・repos が
   url-only・base rev が状態 worktree の rev という三重のずれで恒常 NG になり、運用では
   `--debt --sync`（負債ラチェット）形へ退避した経緯がある（2026-07-17）。差分ゲートの正しい
   実行場所は**成果が作られた clone の中**であり、それはまさに検証計画の実行場所と一致する。

## 現状の棚卸し

| 差し込み点 | 現状 | 本設計での扱い |
|---|---|---|
| E2 `regression_cmd` → 共通チェック | 生きている。ただし差分ゲート形は上記 3 の理由で壊れやすく、負債ラチェット形で運用 | **負債ラチェット専用**と位置づけ直す（差分ゲートは①へ） |
| E1 acceptance の負債ラチェット | 生きている（共通チェック内で実行） | 変更なし |
| E3/E4 `tasks --debt` の intake | 生きている（opt-in） | 変更なし。常設返済の正はこちらのまま |
| 修復タスクの `check` verify | 生きている。統一 verify の commands に乗り clone 内で自己完結（`--repo-dir <name>=.`） | 変更なし——①はこの既存動作の一般化 |
| 専用配線（`codd_gate_*.py`・dashboard 専用表示） | 2026-08-02 撤去済み | 復活させない |
| 統一 verify（検証計画＋receipt＋verify-fix） | **集約判断の後に実装**。固定コマンド・`$AGENT_BASE_REV`・exit 127=inconclusive・fail→修復ノード注入 | ①の載せ先 |
| 手法パック（`methods/` カタログ＋repo 配布 `.agents/methods/`＋run 専用 AGENT_TUNING_DIR） | 実装済み。`consistency-sweep`（origin skill://codd-gate）が 1 件だけ存在 | ②の載せ先 |
| ユーザー定義フロー／adhoc-flow／repo 配布 `.agents/workflows/` | 実装済み。`verification_plan` は inbox 契約にあるが **adhoc submit は組んでいない** | ③と、①の adhoc 側拡張点 |

## ① 差分ゲートを検証計画へ — 本命

**契約は既存のまま。** inbox の `verification_plan` は依頼側確定・digest 付きで、固定コマンドは
workspace 宣言のある run なら成果 clone の中で `$AGENT_BASE_REV` 付きで走る（spec §3.3）。
そこへ 1 行載せる:

```
codd-gate verify --base "$AGENT_BASE_REV"
```

レジストリ無しの単一リポジトリなら cwd が repo `default` になるので引数はこれだけで足りる。
`.kiro/codd-gate.yaml` で複数 repo を宣言しているリポジトリは `--repo-dir <name>=.` を併記して
clone 内で自己完結させる（修復タスクの `check` が既にやっている形と同じ）。

この置き場所で得られるもの:

- **fail → 自己修復。** 終了コード非 0 は内容の失敗として `verify-fix` ノードが LLM なしで
  決定的に注入され、worker がドリフト（doc 未更新・壊れた参照）を同じ run 内で直してから
  再検証する。`max_iterations` で有界。人へ届くのは修復しきれなかった分だけになる（C3）。
- **フェイルクローズの一致。** codd-gate 未インストールの端末では exit 127 → inconclusive
  （環境の欠落）で、黙って PASS に倒れない。codd-gate 不変条件 2 と統一 verify の倒し方が
  最初から同じ向き。
- **証跡。** receipt にコマンド・終了コード・所要が残り、採用側（agent-project／昇格）が検算できる。

**書き手は投入側 = 制御層。** 「決定的な合否は制御層の専管」（2026-08-02 の却下理由）は崩れない。
エンジンは渡された plan を実行するだけで、codd-gate を知らない。

適用は 2 段階:

- **段A（adhoc-flow）**: dashboard の submit が `verification_plan` を組めるようにし、workspace
  指定のある投入に「一貫性ゲート」トグル（既定 off）を置く。on なら上記コマンドを commands へ
  積む。ノードの語彙もエンジンの実行系も無改造で、書くのは inbox 契約の既存フィールドだけ。
  digest（canonical JSON の SHA-256）は agentcore.verifycontract が 1 実装の正典なので、
  dashboard に再実装させず、読み取り専用サブコマンド `agent-flow verify-plan` を足して
  組み立てを委譲する（dashboard は返った JSON をそのまま運ぶ。`patterns --json` と同じ呼び方）。
- **段B（agent-project、canon 改訂が必要）**: `build_task_verification_plan` が全 workspace
  タスクの plan へ共通コマンドを合成する汎用キー（例 `plan_commands:`、codd-gate 非名指し）を
  1 つ足し、共通チェック側から差分ゲートを外す。§4.3 の「入口は regression_cmd 1 本」の改訂に
  当たるため、**codd-gate-design.md の改訂を同じ PR で提案し人の承認を得る**（強制ルール 3）。
  段A の実測（自己修復が実際に回るか・偽 NG が出ないか）を見てから着手する。

**却下案**: agent-flow への `gate_cmd` 新設（受け皿が既にある）。エンジンに決定的コマンド専用の
ノード kind を足す案（13 kind は LLM 実行の語彙。決定的検証は plan の領分で、混ぜると done の
根拠が 2 系統になる）。regression の差分ゲートを workdir で直す案（成果と違う場所で差分を測る
構造自体が原因。実行場所を成果へ寄せるのが根治）。

## ② 予防の追加指示 — 手法パック 2 件

ゲートは事後検出。発生源を減らす指示を worker へ注入する。既存の `consistency-sweep` は維持し、
codd-gate の Amber/Gray 分類に対応する 2 件を足す。

| 手法 | 置き場所 | fragment（worker・purposes: work） | 減らす分類 |
|---|---|---|---|
| `doc-follow-through` | `methods/` カタログ（enabled:false 既定） | コード変更では接続するドキュメント（README・仕様・設計書の該当箇所）を同じ変更内で更新し、更新不要ならその理由を成果に明記してください。 | Amber (doc-stale) |
| `coherence-annotate` | **リポジトリ配布** `.agents/methods/`（codd-gate 運用リポジトリだけに置く） | 新規ファイルや推定の効かない接続には `coherence: doc=…` / `test=…` 注釈を宣言してください。 | Gray (unmapped)・接続の誤検出 |

`coherence-annotate` をカタログに入れないのは、注釈規約が codd-gate 運用リポジトリ固有の知識で、
未運用リポジトリの worker に無意味な指示を焼くから。リポジトリ配布の口（ワークフロー設定再編で
整理した共通管理規則）がちょうどこの粒度の置き場所として最近できた。

**却下案**: planner/evaluator への注入（ゲートの知識は作業する側にだけ要る。計画の形を歪める）。
既定 enabled（カタログは実測で外せることが価値。常時全員に効かせる規律は flow-worker スキルの領分）。

## ③ 負債掃除のカスタムフロー — 限定採用

repo 配布 `.agents/workflows/coherence-sweep.json` を参考実装として 1 本置く:

```
inventory(work: codd-gate tasks --debt --json を実行し上位 N 件を要約)
  → fix(work: 列挙された負債を修復)
  → 検証は ① の plan（verify --debt --max-* ラチェット）
```

位置づけは「agent-project 常駐の無い端末・単発の掃除」。worker が codd-gate CLI を叩くのは
作業の手段であって done の根拠にしない——合否は①の receipt と、昇格後の受入だけが決める。
常設の負債返済は既存の E3 intake（`tasks --debt` → enqueue）が正のままで、フローはそれを
置き換えない。

**却下案**: split fan-out で負債 1 件 = 1 ノードへ自動展開する案。split へのデータ供給が worker の
CLI 実行結果に依存し、決定的であるべき機械展開が LLM の出力整形を経由してしまう。同種負債の
山は既設の `tasks --debt --cohort` → agent-project の pilot-then-batch が担う。

## 変えないこと

- codd-gate 本体: 独立・stdlib のみ・LLM 無し・全サブコマンド単発有界。**一切改修しない**。
- パッケージ境界: `agent_project/` からの名指し・import・自動配線の禁止と受入 grep
  （codd-gate-design.md §4.2）はそのまま。
- 負債ラチェット（共通チェック／acceptance）と intake の経路。
- LLM 判断をゲートに混ぜない。verify / gate ノード（エージェントの内側品質ループ）と
  決定的ゲートの区別。

## 段階実装と受入

| 段 | 内容 | 受入 |
|---|---|---|
| 1 | 手法 2 件（`methods/doc-follow-through.json`・参考実装として `.agents/methods/coherence-annotate.json` の例をドキュメントへ） | カタログ検証テストが通り、when 条件で worker/work にだけ効く |
| 2 | adhoc-flow の `verification_plan` 対応＋一貫性ゲートトグル | トグル on の run で drift を仕込むと verify-fix が注入され、修復後 receipt が pass になる。codd-gate 不在端末では inconclusive |
| 3 | （承認後）agent-project の `plan_commands` 汎用キー＋共通チェックから差分ゲートを外す＋codd-gate-design.md §4.3 改訂 | 同一変更に対する合否が移行前後で一致。workdir 起因の恒常 NG が再現しない |
| 4 | 掃除フロー参考実装 | 配布 workflow が読み取り専用として一覧に載り、実行で①のラチェットが判定する |

段 1〜2 は canon 改訂不要（既存契約の利用のみ）。段 3 だけが §4.3 の決定記録の更新を伴う。

## 実装記録

- 2026-08-13: 段 1・2・4 を実装。
  - 段 1: `methods/doc-follow-through.json`（カタログ 21 件目・golden 更新）。
    `coherence-annotate` は codd-gate README の付録にリポジトリ配布例として記載。
  - 段 2: `agent-flow verify-plan` サブコマンド（`verifyplan.py` / `cli.py`・spec §1.4）＋
    dashboard adhoc-flow の `coherenceGate`（`buildVerificationPlan` → inbox の
    `verification_plan`・フェイルクローズ・実行フォームのトグル）。
  - 段 4: `.agents/workflows/coherence-sweep.json`（リポジトリ配布・normalizeWorkflow 検証済み）。
  - 段 3 は未着手（codd-gate-design.md §4.3 の改訂承認待ち）。
- 2026-08-13: リポジトリ配布のカスタムフロー／手法の探索先を `.agent-flow/{workflows,methods}`
  から `.agents/{workflows,methods}` へ統一。agent-project が既に使っている project-local
  設定の語彙（`<repo>/.agents/agent-project.yaml`）と repo-scope を合わせ、home-scope
  （`~/.agents/workflows`・`~/.agents/methods`）とも対称にした（C7 — agent-flow だけ別名を
  持つ不整合を解消）。コードは adhoc-flow の `repositoryWorkflowDir` / `repositoryMethodsDir`
  / `availableMethods` の 3 箇所、ドキュメントは adhoc-flow README・agent-dashboard-design.md・
  workflow-settings-reorganization-design.md・codd-gate README・本書。
