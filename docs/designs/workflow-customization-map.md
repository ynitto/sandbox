# ワークフローのカスタマイズ地図 — 契約（スキーマ）とエンジン・UI の関係

「ワークフローの振る舞いを変えたい」ときに、**どこを触ればよいか / なぜその置き場所なのか**を
1 枚にまとめた案内。個別の設計判断は各設計書（末尾の参照）に譲り、ここは**関係の地図**に徹する。

対象読者: agent-flow / agent-loop / agent-dashboard のどこに手を入れるか迷ったとき。

---

## 0. 先に、混同しやすい 3 点

**(1) agent-loop は「ワークフロー設定」を持っていない。**
`agent-loop methods ...` があるので誤解しやすいが、`methods` は agent-loop の機能ではなく
**agent-tuning という 2 エンジン共有の契約**（`$AGENT_TUNING_DIR/tuning.json`）で、
agent-flow と agent-loop が**同じ 1 ファイルを読む**。agent-loop の立ち位置は 2 つだけ:

- **消費者**: 自分の定期プロンプトへ `role: session` の手法を注入する（ワークフローとは無関係）
- **管理 CLI のホスト**: 共有 tuning.json を編集する CLI がたまたま agent-loop に同居している

つまり `agent-loop methods enable X` は**agent-flow の run にも効く**（`when` が合えば）。
「agent-loop がワークフローを設定している」のではなく、「共有契約の編集口が agent-loop にある」。

**(2) `split_policy` は廃止していない。カタログへ寄せたのは文面だけ。**
2026-08-18 の設計で統一したのは**プロンプト文面の正典の置き場所**であって、
**選択の口（CLI / 設定ファイル）ではない**。設計書にも「CLI/config の面は今のまま（enum を
広げない）」と明記してある。`--split-policy` が残っているのは設計どおり。

**(3) ただし「おかしい」という違和感自体は当たっていた（2026-08-20 に解消）。**
理由は「廃止したのに残っている」ではなく「**既定の planner では黙って無視されていた**」。
`split_policy` を planner へ渡していたのは `--planner agent` の分岐だけで、既定の
`flow-planner`（と、そこからの縮退経路）は指定を捨てていた——`--granularity` は 3 経路すべてへ
渡っていたので、この 2 つは対称でなかった。現在は flow-planner スキルの `--split-directive`
（**解決済みの文面**を渡す口。値名ではないので `.agents/methods/` の差し替えもこの経路へ届く）と
`_planner_fallback` の引数渡しで、stub 以外の全経路に効く。

---

## 1. ワークフローの振る舞いを決める 4 層

上から順に「形」→「分け方」→「言い方」→「誰が実行するか」。層が違えば触る場所も違う。

| 層 | 何を決めるか | 実体 | 主な入口 |
| --- | --- | --- | --- |
| **L1 形** | 工程のグラフそのもの（何工程・依存・種別） | ワークフロー定義 / 標準パターン / planner の出力 | dashboard のフロー編集、`--pattern`、`--plan-file`、planner に任せる |
| **L2 分け方** | 分解の粒度・分割の単位・レビューの有無・実行ティア | run パラメータ | `--granularity` / `--split-policy` / `--review` / agent-control の tier |
| **L3 言い方** | プロンプトへ足す文・差し替える文 | 手法カタログ（methods） | `.agents/methods/`、dashboard の作業ルール、工程ごとの選択 |
| **L4 実行資源** | どの CLI / モデル / 予算で回すか | agent-control / agent-profiles / node-budget | dashboard の実行方針、`--agent-cli` など |

計画に関わる run パラメータ（L1 の `--planner` / `--pattern` / `--plan-file` と L2 の `--granularity` /
`--review` / `--plan-gate` 系、および動的 fan-out の `--split-policy` / `--max-fanout` /
`--exemplar-first`）は、**実際に計画するサブコマンド（`run` / `orchestrate`）の引数**で、
グローバル引数ではない。計画しないサブコマンドに書くと usage エラーで断る（2026-08-20 まではグローバルで、
`agent-flow --granularity finest doctor` のような指定を受理して黙って捨てていた）。設定ファイルの
同名キーとは 1 対 1 で、CLI 指定が優先する。

**L3 が本設計の中心**で、L1・L2・L4 は別契約。「文面を変えたい」なら L3、
「形を変えたい」なら L1、「切り方を変えたい」なら L2。

---

## 2. L1: ワークフロー定義（形）

### 置き場所と優先順位

同じ id は**先に並ぶスコープが勝つ**（手法カタログと同じ上書き規則）:

1. `<登録フォルダ>/.agents/workflows/<id>.json` — リポジトリ共有（読み取り専用）
2. ユーザー領域の `workflows/<id>.json` — dashboard で保存したもの（編集可）
3. 同梱 `workflows/<id>.json` — 標準装備（読み取り専用）

### 形を決める 3 経路

| 経路 | 誰が形を決めるか | 入口 |
| --- | --- | --- |
| **ユーザー定義フロー** | 人（描いたグラフがそのまま実行される） | dashboard のフロー編集 / `--plan-file` / inbox の `plan` |
| **標準パターン** | 人がパターンを選び、エンジンが正準グラフへ展開 | `--pattern`、dashboard の「標準フロー」 |
| **planner** | LLM（flow-planner スキル / agent / stub） | 既定。`--planner` で切替 |

### スキーマと 2 つの実装の関係

契約は `schemas/agent-workflow.schema.json`（2026-08-20 登録。それまでワークフロー定義だけが
`schemas/` に正典を持たない例外だった）。ルートが**ライブラリ定義**（dashboard が保存する形）、
`$defs.plan` が**投入 plan**（agent-flow へ渡す形）で、`planFromWorkflow()` が前者から後者へ変換する。

検証そのものは今も 2 つの実装が行う（リポジトリに JSON Schema バリデータは無い）:

- `normalizeWorkflow()`（agent-dashboard・保存時の検証）
- `plan_strategy_user()`（agent-flow・実行時の検証。**厳格に失敗させる**＝丸めない）

スキーマは語彙と制約の正典（人と外部ツール向けの文書）で、グラフ不変条件（id の一意性・
deps の実在・循環の禁止・entry=根 / exit=葉・split の静的な後続禁止）は JSON Schema で
表現できないため実装が正典のまま。乖離はクロスチェックのテストで止める
（`tools/agent-flow/tests/test_workflow_schema.py` / dashboard の `adhoc-flow.test.js`）。

---

## 3. L3: 手法カタログ（methods）— 選ばれ方が 3 つある

`methods/<id>.json` の 1 件は「**モデル**（何を定義するか）」と「**選ばれ方**（誰が選ぶか）」の
2 軸で分類される。この 2 軸が本機能のいちばん重要な構造。

### モデル（`kind`）

| kind | 何を定義するか | 例 |
| --- | --- | --- |
| `rule`（既定） | プロンプトへ足す指示 | `test-first`, `integration-verify` |
| `contract` | 成果物の形式そのもの（指示＋機械で数える構造 `format`） | `design-document-format` |

### 選ばれ方（`selection`・rule のみ）

| selection | 誰が選ぶか | `enabled` / `when` の意味 | UI での見え方 |
| --- | --- | --- | --- |
| `auto`（既定） | **実行条件が自動で選ぶ**（role・工程種別・tier・料金区分） | `enabled: true` で自動適用、`when` で絞る | トグルで ON/OFF |
| `per-task` | **人 or planner が工程ごとに選ぶ** | 選択に関与しない | 一覧表示のみ（工程の編集画面で選ぶ） |
| `engine` | **エンジンが run パラメータから決定的に選ぶ** | 選択に関与しない | 一覧表示のみ（差し替えは `.agents/methods/`） |

**不変条件**: `auto` 以外は `enabled: true` を書かれても自動注入されない。
これは UI の出し分けではなく `agentcore.methods.select`（flow / loop 双方の自動注入が通る
唯一のチョークポイント）で強制する。書き込み口（CLI の enable・dashboard の保存・run 複製）も
同じ規則で断る。

### `selection: engine` の解決順

エンジンが run パラメータから id を組み立て（例: `--split-policy file` → `split-policy-file`）、
次の順で**文面だけ**を引く:

```
run 専用 tuning.json      ← dashboard 経由の run（run 単位で固定）
  ↓ 無ければ
<repo>/.agents/methods/<id>.json  ← プロジェクト固有の差し替え（CLI 単体利用）
  ↓ 無ければ
$AGENT_METHODS_DIR/<id>.json      ← 同梱カタログの導入先
  ↓ 無ければ
組み込み文言（Python の辞書）      ← 消えないための最後の砦
```

**カタログで差し替わるのは文面だけ**。値の意味（split_policy の 2 値、granularity の並列数倍率、
tier の auto→finest 分岐、レビュー観点のキー）は**エンジンパラメータのまま**で、カタログでは
変えられない。これが「個別インターフェースを廃止していない」ことの中身:
**選択はエンジン、文面はカタログ**、と役割を分けただけ。

---

## 4. 契約（スキーマ）と読み手・書き手

ワークフローに関わるものだけ抜粋（全体は `schemas/README.md`）。

| 契約 | 置き場所 | 読み手 | 書き手 |
| --- | --- | --- | --- |
| **ワークフロー定義**（スキーマ無し） | `workflows/` / `.agents/workflows/` / ユーザー領域 | agent-flow（実行）・dashboard（編集） | 人・dashboard |
| `agent-tuning`（methods / trials / injections） | `$AGENT_TUNING_DIR/tuning.json` | **agent-flow・agent-loop** | 人・dashboard・`agent-loop methods`・`agent-audit tune --apply` |
| 手法カタログ（tuning の材料） | `$AGENT_METHODS_DIR/` ＋ `.agents/methods/` | 同上＋dashboard | 人（同梱はリポジトリ） |
| `agent-instructions` | `$AGENT_INSTRUCTIONS_DIR` | 各エンジン | dashboard |
| `agent-control`（tier・縮退・停止） | `$AGENT_CONTROL_DIR/control.json` | 各エンジン | dashboard |
| `agent-profiles` | `$AGENT_CONTROL_DIR/profiles.json` | **エンジンは読まない**（管理面専用） | dashboard・agent-audit |

**run 専用 tuning.json** は特殊: dashboard が run 作成時に「その run で効く手法」を複製し、
`AGENT_TUNING_DIR` を差し替えて agent-flow を起動する。端末全体の tuning.json は
その run では**読まれない**（合成ではなく置換）。走り出した run の振る舞いを後から変えないための設計。

---

## 5. 現状の穴（このまとめで見つかったもの）

このまとめで挙げた 3 件（`--split-policy` が既定 planner で無視される / ワークフロー定義に
JSON Schema が無い / dashboard から L2 を触れない）は 2026-08-20 にすべて解消した。
最後の 1 件の顛末だけ、層の分け方の実例として残す。

### dashboard から L2 を触れない（解消済み）

dashboard が run へ渡せる実行時指定（`executionOverrides`）は tier / agent_cli / model
＝**L4 実行資源だけ**で、`granularity` も `split_policy` も画面からは設定できなかった。

直し方として `executionOverrides` に相乗りさせる手もあったが、**別の層は別のキーで運ぶ**
方針を採った。あちらは「役割・工程ごとに誰が実行するか」、こちらは「run 全体をどう分けるか」で、
粒度も適用単位も違う。inbox のトップレベルへ `granularity` / `split_policy` を置き、キー名は
設定ファイルのキーと、値の語彙は CLI とそのまま揃えてある（§1 の「同じオプション名で扱う」）。
画面の入口は実行前の確認ダイアログの「分け方を指定する」で、未指定なら値を書かない
——画面が対象フォルダの `agent-flow.yaml` を黙って上書きしないため。

---

## 6. 迷ったときの早見表

| やりたいこと | 触る場所 |
| --- | --- |
| 工程の並びを変えたい | ワークフロー定義（dashboard のフロー編集 / `--plan-file`） |
| いつも同じ追加指示を効かせたい | 手法カタログ（`selection: auto`）＋ dashboard でトグル ON |
| 特定の工程にだけ指示を足したい | 手法カタログ（`selection: per-task`）＋ 工程で選ぶ |
| 分割方針の**文面**をプロジェクト用に書き換えたい | `<repo>/.agents/methods/split-policy-file.json` |
| 分割方針そのもの（behavior/file）を切り替えたい | `--split-policy` / 設定 `split_policy` / dashboard の実行前の確認 |
| 分解の粒度を変えたい | `--granularity` / 設定 `granularity` / dashboard の実行前の確認 |
| 実行する CLI・モデル・予算を変えたい | dashboard の実行方針（agent-control / agent-profiles / node-budget） |
| 全ノード共通の指示を配りたい | agent-instructions（dashboard の共通指示） |

---

## 参照

- `docs/plans/2026-08-18-split-policy-catalog-unification-design.md` — `selection: "engine"` の設計（正典）
- `docs/plans/2026-08-15-workflow-feature-improvement-implementation.md` — auto / per-task の導入
- `schemas/README.md` — 契約の全カタログと所有者
- `tools/agent-flow/README.md` — run パラメータの詳細
- `tools/agent-dashboard/src/features/adhoc-flow/README.md` — ワークフロー編集と手法の UI 側
