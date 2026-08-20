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

**(3) ただし `--split-policy` には実際の欠陥がある（下記 §5）。**
「おかしい」という違和感自体は当たっている。理由が「廃止したのに残っている」ではなく
「**既定の planner では黙って無視される**」という別のもの。

---

## 1. ワークフローの振る舞いを決める 4 層

上から順に「形」→「分け方」→「言い方」→「誰が実行するか」。層が違えば触る場所も違う。

| 層 | 何を決めるか | 実体 | 主な入口 |
| --- | --- | --- | --- |
| **L1 形** | 工程のグラフそのもの（何工程・依存・種別） | ワークフロー定義 / 標準パターン / planner の出力 | dashboard のフロー編集、`--pattern`、`--plan-file`、planner に任せる |
| **L2 分け方** | 分解の粒度・分割の単位・レビューの有無・実行ティア | run パラメータ | `--granularity` / `--split-policy` / `--review` / agent-control の tier |
| **L3 言い方** | プロンプトへ足す文・差し替える文 | 手法カタログ（methods） | `.agents/methods/`、dashboard の作業ルール、工程ごとの選択 |
| **L4 実行資源** | どの CLI / モデル / 予算で回すか | agent-control / agent-profiles / node-budget | dashboard の実行方針、`--agent-cli` など |

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

### ⚠ ここだけ JSON Schema が無い

`schemas/` にワークフロー定義のスキーマは**存在しない**。正典は 2 つの実装:

- `normalizeWorkflow()`（agent-dashboard・保存時の検証）
- `plan_strategy_user()`（agent-flow・実行時の検証。**厳格に失敗させる**＝丸めない）

他の契約（agent-tuning / agent-control / agent-instructions …）は `schemas/` に正典があるので、
ワークフロー定義だけが例外。2 実装の乖離はテストでしか止まらない。

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

## 5. 現状の穴（このまとめで見つかったもの・未修正）

### `--split-policy` は既定の planner では無視される

`split_policy` を planner へ渡しているのは `--planner agent` の分岐**だけ**:

| planner | split_policy | 備考 |
| --- | --- | --- |
| `flow-planner` | **無視** | **これが既定**（`CONFIG_DEFAULTS["planner"]`） |
| flow-planner → agent フォールバック | **無視** | `_planner_fallback` が引数を渡していない |
| `agent` | 効く | 明示指定したときだけ |
| `stub` | 無視 | LLM を使わないので当然 |

つまり**既定設定のまま `--split-policy file` と打っても何も起きない**。
CLI ヘルプにも README にも「planner を選ばないと効かない」とは書かれていない。
`--granularity` は 3 経路すべてに渡っているので、この 2 つは対称でない。

考えられる直し方（どれも未実施・要判断）:
1. flow-planner スキルへ `--split-policy` を渡す（スキル側の改修が要る）
2. せめてフォールバック経路には渡す（1 行）＋ヘルプと README に制約を明記
3. 効く条件を満たさない指定を警告する（黙って無視しない）

### dashboard から L2 を触れない

dashboard が run へ渡せる実行時指定（`executionOverrides`）は tier / agent_cli / model だけ。
`granularity` も `split_policy` も**画面からは設定できない**（CLI と設定ファイル専用）。
「分解の粒度を画面で変えたい」という要望が出たらここが対象になる。

### ワークフロー定義に JSON Schema が無い（§2 参照）

---

## 6. 迷ったときの早見表

| やりたいこと | 触る場所 |
| --- | --- |
| 工程の並びを変えたい | ワークフロー定義（dashboard のフロー編集 / `--plan-file`） |
| いつも同じ追加指示を効かせたい | 手法カタログ（`selection: auto`）＋ dashboard でトグル ON |
| 特定の工程にだけ指示を足したい | 手法カタログ（`selection: per-task`）＋ 工程で選ぶ |
| 分割方針の**文面**をプロジェクト用に書き換えたい | `<repo>/.agents/methods/split-policy-file.json` |
| 分割方針そのもの（behavior/file）を切り替えたい | `--split-policy` / 設定 `split_policy`（※ §5 の制約に注意） |
| 分解の粒度を変えたい | `--granularity` / 設定 `granularity` |
| 実行する CLI・モデル・予算を変えたい | dashboard の実行方針（agent-control / agent-profiles / node-budget） |
| 全ノード共通の指示を配りたい | agent-instructions（dashboard の共通指示） |

---

## 参照

- `docs/plans/2026-08-18-split-policy-catalog-unification-design.md` — `selection: "engine"` の設計（正典）
- `docs/plans/2026-08-15-workflow-feature-improvement-implementation.md` — auto / per-task の導入
- `schemas/README.md` — 契約の全カタログと所有者
- `tools/agent-flow/README.md` — run パラメータの詳細
- `tools/agent-dashboard/src/features/adhoc-flow/README.md` — ワークフロー編集と手法の UI 側
