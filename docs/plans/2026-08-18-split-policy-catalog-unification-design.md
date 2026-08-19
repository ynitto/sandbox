# エンジンが選ぶ指示文（selection: "engine"）— split_policy カタログ統一の設計

発端: `docs/plans/2026-08-15-workflow-feature-improvement-implementation.md` 第 1〜5 段の質疑で
「split_policy が methods と同じ統一を受けていないのは一貫性を欠くのでは」という指摘を受けた。
2026-08-18 に統合可否を検討（付録参照）、2026-08-19 に実装前の見直しで同型の系を全数確認し、
検討時の推奨案（案 C）を汎用化して実装した。本書はその設計の正典で、実装済み。

## 目的

agent-flow には「run パラメータの値 → 固定プロンプト文面」という選択が 4 系統ある:

| 系 | 実体 | 文面以外の効果（構造的効果） |
| --- | --- | --- |
| `SPLIT_POLICY_DIRECTIVES` | behavior / file → planner 文面 | なし |
| `GRANULARITY_SCOPE_DIRECTIVES` | coarse / fine / finest → planner 文面 | 並列数倍率（`GRANULARITY_FACTORS`） |
| `TIER_PLANNER/EVALUATOR/SPLIT_DIRECTIVES` | tier（basic）→ planner / evaluator / split 文面 | auto→finest の制御分岐等 |
| `REVIEW_LENSES` | 評価役のレビュー観点文面 | 観点キー（run 履歴の記録名） |

これらの文面は Python の辞書に固定されており、`integration-verify` や
`design-document-format` と違って**リポジトリ側で文面を上書きできない**例外だった。
本設計の目的は、この 4 系統の**文面の正典を手法カタログへ寄せ、選択の形は変えずに**、
プロジェクトごとの差し替え口をひとつの汎用インターフェースとして整えることにある。
構造的効果（倍率・分岐・観点キー・enum そのもの）はエンジンパラメータのままにする——
カタログで差し替わるのは文面だけ、が不変条件。

## 変更対象

### 手法カタログ: 選ばれ方の第 3 形態 `selection: "engine"`

既存の選ばれ方は auto（実行条件で自動適用）と per-task（工程ごとに人・planner が選ぶ）の
2 つ。これに **engine =「エンジンが CLI/config/agent-control の値から決定的に選ぶ」** を
加える。engine の定義では `enabled` / `when` は選択に関与しない（選択はエンジンの仕事で、
カタログは文面だけを差し替える）。

- 強制レイヤー: 選択の決定性はエンジン実装（`engine_directive` の呼び出し点が run
  パラメータから id を組み立てる）で強制。dashboard はトグルに出さない（表示のみ）——
  auto ルールの器（`agentcore.methods.select`）へは同梱定義の `enabled: false` が漏れを防ぐ。

### agent-flow: 単一の解決口 `engine_directive(id, role, fallback)`（`patterns.py`）

解決順（見つかった最初の role 一致テキストを使う）:

1. **run 専用 tuning.json の `methods[]`** — dashboard が run 作成時に複製したスナップショット
   （per-task と同じ器・run 単位の決定性。dashboard 経由の run は agent-flow の cwd が
   リポジトリ外なので、リポジトリ差し替えはこの複製が届ける）
2. **対象リポジトリの `.agents/methods/<id>.json`**（cwd → git root）— CLI 単体利用の
   差し替え口。ファイル名は `<id>.json` 固定（エンジンはディレクトリを走査しない）
3. **`$AGENT_METHODS_DIR/<id>.json`**（既定 `~/.agents/methods/`）— 同梱カタログの導入先
4. **組み込み文言**（Python 側の辞書）— カタログ不在・破損・role 不一致・空文字は
   すべてここへ倒す。split_policy はエンジンの分解方針そのものであり、無指定の run が
   黙って無方針になってよい機能ではない（`integration-verify` がカタログ欠如時に
   「標準装備を諦める」フェイルクローズと逆向きなのは、この性質の違いによる）。
   空文字の上書きによる「指示の抑止」はできない仕様。

- 強制レイヤー: フォールバックは `engine_directive` の実装で強制（テキストが得られない
  すべての経路が組み込み文言へ到達する）。id の書式（`^[a-z0-9][a-z0-9-]*$`）も同関数で強制。

各 directive 関数の配線:

| 関数 | カタログ id | role |
| --- | --- | --- |
| `split_policy_directive(policy)` | `split-policy-<policy>` | planner |
| `granularity_directive(level)` | `granularity-<level>`（auto・未知値は空のまま＝対象外） | planner |
| `tier_planner_directive(tier)` | `tier-<tier>` | planner |
| `tier_evaluator_directive(tier)` | `tier-<tier>` | evaluator |
| `tier_split_directive(tier)` | `tier-<tier>-split`（split ノード専用の注入点） | worker |
| `review_lens_directive()` | `review-lenses` | evaluator |

tier の語彙は agent-control の宣言で開いている（標準は basic/small/medium/large）ので、
組み込みが知らない tier（例 `tier-small`）でもカタログ定義を置けば指示文を足せる——
エンジン改修なしの拡張口。一方 split_policy の 2 値と granularity の enum は閉じたまま
（値の追加はエンジン変更）。

### 同梱カタログ（`methods/`）: 8 件の新設

`split-policy-behavior` / `split-policy-file` / `granularity-coarse|fine|finest` /
`tier-basic`（planner + evaluator）/ `tier-basic-split` / `review-lenses`。
いずれも `selection: "engine"` / `enabled: false` / `when` なし。文面は組み込み文言と同一。

- 強制レイヤー: 同梱文面と組み込み文言の一致はテスト
  （`BundledEngineDirectiveCatalogTests`）で強制——導入済み環境はカタログ側、未導入環境は
  組み込みが読まれるため、片方だけ直すと環境で指示文が食い違う。乖離はここで落ちる。

### dashboard（agent-dashboard）

- `RULE_SELECTIONS` に `engine` を追加。engine ルールは自動適用のトグルにも per-task の
  工程選択にも出さず、設定画面では一覧表示のみ（「エンジンが選ぶ指示文」節。差し替えは
  `.agents/methods/` に同 id を置く案内を添える）。
- run 作成時、per-task と同じ器で engine ルールを run tuning.json へ複製する
  （`engineMethodsSnapshot`・enabled: false のまま）。`availableMethods` を通すので、
  登録フォルダの `.agents/methods/` 差し替えは複製時に解決済み。

- 強制レイヤー: トグル対象外は renderer のフィルタ（表示のみ）と、同梱定義の
  `enabled: false` の両方で守る。run への到達は main の複製実装で強制。

## 受入基準

- カタログが無い・壊れている・role 不一致の環境で、全 directive 関数が従来の組み込み文言を
  そのまま返す（既定挙動不変。既存テストが無改変で緑であること）。
- `$AGENT_METHODS_DIR` / リポジトリ `.agents/methods/` / run tuning.json の各層で
  同 id を置くと、この優先順で文面が差し替わる。
- 同梱カタログ 8 件の文面が組み込み文言と一致している（乖離はテストで検出）。
- engine ルールが dashboard のトグル一覧・per-task 一覧に現れない。run tuning へは
  enabled: false で複製される。
- 組み込みが知らない tier のカタログ定義（`tier-<名前>`）が、エンジン改修なしで
  planner / evaluator / split の指示文として効く。

## 検証方法

- `tools/agent-flow/tests/test_engine_directives.py` — 解決順・フォールバック・repo 上書き・
  壊れた JSON・role 不一致・tier 開語彙・同梱と組み込みの文面一致・engine 宣言の検査
- `tools/agent-flow/tests/test_planner.py` ほか既存スイート — 無改変で緑（既定挙動不変の証明）
- `tools/agent-loop/test/test_methods_catalog.py` — カタログ 33 件・selection モデル・golden hash
- `tools/agent-dashboard` `npm test` — トグル/per-task 除外・run tuning 複製・golden hash（JS 側）

## 付録: 検討の経緯（2026-08-18 時点・当時の判断の記録）

### 現状の事実（検討時）

`split_policy`（`behavior` / `file`）は `SPLIT_POLICY_DIRECTIVES[split_policy(policy)]` で
プロンプト文字列を選ぶことにしか使われていない（`patterns.py`・`orchestrate.py` を全数確認。
他の分岐・制限には一切関与しない）。この点で `granularity` や `tier` とは性質が違う——
`GRANULARITY_FACTORS` は並列数の倍率という構造的な効果を持ち、`tier_planning_granularity()`
は basic tier で auto を finest へ倒す制御分岐も持つ。**split_policy だけが「値 → 固定テキスト
の選択」という用途に完全に閉じている**。これが methods と同じ形へ寄せられると考えた根拠。

### 案 A: `trials` / `variants`（A/B 実験プリミティブ）への統合 — 不適合

`agentcore/methods.py` の `select()` を読むと、`trials`/`variants` は variant 数が厳格に 2、
選択は `assignment_key` からのハッシュ（A/B 比較の対照群をタスクごとに安定させる決定的
疑似ランダム）、`enabled` での丸ごとオプトイン、と「効果を測る」ための道具である。
split_policy に要る性質は正反対——利用者が明示的に選び、その run では常に一方だけが確実に
効く。流用すると `--split-policy file` と指定したのにハッシュの都合で `behavior` 側が選ばれる
余地を生む。**カテゴリ違いの流用であり不採用。**

### 案 B: 現状維持

追加実装は不要だが、`integration-verify` / `design-document-format` と違って文面を
リポジトリ側で上書きできない例外が残り続ける。

### 案 C（当時の推奨）: 文面だけをカタログへ、選択の形は変えない

CLI/config の面は今のまま（enum を広げない）。`methods/split-policy-*.json` を新設し、
`split_policy_directive` はカタログを引き、無ければ組み込み文言へフォールバックする。
リポジトリの `.agents/methods/` に同 id を置けばそのリポジトリだけ文面を差し替えられる。
新しい選択プリミティブを発明せず、既存の「カタログの id 引き＋フォールバック」パターンを
再利用するだけなので実装コストが低い。

### 実装時（2026-08-19）に案 C から広げた点とその理由

当初案は granularity / tier 系を「構造的効果を持つ genuine なエンジンパラメータだから対象外」
としたが、その論拠が縛るのは**値の選ばれ方**であって**文面の置き場所**ではない。構造的効果を
Python 側に残したまま文面だけを同じ器に寄せれば性質の違いは保たれる。個別の
`split_policy_directive` 特例として実装するより、「エンジンが選ぶ」という選ばれ方に
`selection: "engine"` という名前を与えて 4 系統へ一様に適用するほうが、将来の run パラメータ
追加時にも同じ口で受けられる（本文の設計はこの判断の結果）。

対象外と確認したもの: `PATTERNS`（ユーザー定義フロー / flow-planner スキルの
patterns-catalog.yaml が既にカスタマイズの口）、agent-instructions / agent-tuning injections
（別契約として既にカタログ化済み）、planner / evaluator / worker の地の文（run パラメータで
選ばれる文面ではなくエンジン内部。足したい指示は既存の作業ルールで足せる）。
