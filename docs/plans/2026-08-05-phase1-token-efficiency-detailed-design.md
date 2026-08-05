# Phase 1 詳細設計 — 実行プロファイル自動選択 / 差分修復リトライ / 安定プレフィックス化

> 作成 2026-08-05
> 上位文書: [エンジン側のトークン効率化 — 比較検討](./2026-08-04-engine-token-efficiency-proposals.md)（10 案の比較）
> 対象: `agent-dashboard` / `agent-flow` / `agent-project` / `agentcore` / `schemas`
> 効く柱・原則: **柱1・柱2 / C1・C2・C3・C5・C7**（各部の冒頭に個別記載）
>
> 比較検討の推奨に従い、Phase 1 の 3 案を実装可能な粒度まで落とす。
> **案 G（判断のダイエット）は計測結果が対象を決めるため本書に含めない**——先に §5 の計測を
> 回し、引き下げ候補が確定してから別設計を起こす。

---

## 0. 3 案に共通する設計原則

この 3 つは狙う原資も実装場所も違うが、次の 4 点は共通の制約として全部に掛かる。

1. **完全オプトイン。** どの案も宣言が無ければ既定挙動は 1 バイトも変わらない。既存テストが
   そのまま通ることを受け入れ条件にする。
2. **決定的であること。** 「同じ入力なら同じ結果」を純関数として書き、テストで固定する。
   時刻が入るのは案 D の最小保持時間だけで、それも `now` を引数に取る純関数に閉じる。
3. **判断根拠は 1 か所（C7）。** 案 D の選択ロジックは dashboard だけが持ち、エンジンへは
   既存の agent-control 契約で結果だけを渡す。案 B-1 の修復材料はバス上の実状態から実行時に
   導出し、グラフやプランナー契約へ持ち回らない。案 H の順序規約は agentcore に 1 実装置く。
4. **効かなかったこと・悪化したことに気づける（C8）。** 各案は適用の有無と根拠を証跡へ残し、
   §5 の指標で before/after を比較できる状態で出す。

---

## 1. 案 D — 実行プロファイルの決定的自動選択（agent-dashboard・エンジン無改修）

> 柱1 / C1・C2・C7。**エンジンのコードは 1 行も変えない。**

### 1.1 何を解くか

「大・中・小（エージェント × モデルのセット）を複数定義し、トークン使用量に応じて自動で
選ばせたい」。ここで重要なのは、**利用枠は agent CLI（＝アカウント）ごとに別々**だという
現実（コンセプト正典 §2-1）である。ワークロードの予算が余っていても、その候補が使う CLI の
枠が枯れていれば意味がない。よって選択は 2 軸で行う:

- **段（tier）の決定** … ワークロードの予算残率から「大／中／小」を決める
- **候補の決定** … その段の候補列から、**枠が枯れていない CLI** の最初の候補を採る

dashboard は既に `budget.usage()` でワークロード別・**agent_cli 別**の消費を集計しており
（`usage().workloads[w].totalTokens` / `usage().agents[cli].totalTokens`）、
`control.saveControl()` で agent-control 契約へ投函する口も持っている。**足りないのは
「宣言（プロファイル）」と「純関数の選択」だけ**で、そこだけを足す。

### 1.2 契約 1: `agent-profiles`（新規・dashboard 専有）

置き場: `$AGENT_CONTROL_DIR/profiles.json`（既定 `~/.agents/control/profiles.json`）。
正典: `schemas/agent-profiles.schema.json`（新規）。

**不変条件: エンジンはこのファイルを読まない。** 読み書きするのは dashboard だけで、
エンジンへ伝わるのは選択結果（control.json）だけである。これにより「選択ロジックが 2 実装に
分かれる」ことも「エンジンが新しい契約を覚える」ことも起きない（C2・C7）。

```jsonc
{
  "version": 1,
  "enabled": true,
  "tiers": {                        // 名前は自由。順序は order（大きいほど上位）で決める
    "large":  { "order": 3, "label": "大",
                "candidates": [ {"agent_cli": "claude",  "model": "opus"},
                                {"agent_cli": "copilot", "model": "gpt-5"} ] },
    "medium": { "order": 2, "label": "中",
                "candidates": [ {"agent_cli": "claude",  "model": "sonnet"} ] },
    "small":  { "order": 1, "label": "小",
                "candidates": [ {"agent_cli": "ollama",  "model": "qwen3"} ] }
  },
  "policy": {
    "apply_to": ["project", "flow"],          // 対象ワークロード（未指定 = 何もしない）
    "steps": [                                 // 予算残率 → 段（降順に評価）
      {"min_remaining_ratio": 0.5, "tier": "large"},
      {"min_remaining_ratio": 0.2, "tier": "medium"},
      {"min_remaining_ratio": 0.0, "tier": "small"}
    ],
    "no_cap_tier": "large",                    // 上限未設定（無制限）のときの段
    "hysteresis": 0.05,                        // 上位へ戻るときだけ要求する上乗せ
    "min_hold_sec": 900,                       // 段を変えてからこの秒数は動かさない
    "interval_sec": 300                        // 自動評価の間隔（0 = 手動のみ）
  },
  "state": {                                   // dashboard が書く決定の記録（監査・ヒステリシス用）
    "flow": { "tier": "medium", "candidate": {"agent_cli": "claude", "model": "sonnet"},
              "since": "2026-08-05T02:00:00Z", "reason": "remaining=0.34 < 0.5" }
  }
}
```

`state` は宣言ではなく**導出結果の記録**だが、同じファイルに置く——書き手が dashboard 1 つで
あることが明白で、決定と根拠が 1 か所に揃う（人が読んで「なぜ今この段か」が分かる）。

### 1.3 契約 2: agent CLI ごとの枠（node-budget への additive 追加）

`schemas/node-budget.schema.json` の `allocation` へ、既存 `workloads` と対称な `agents` を足す:

```jsonc
"allocation": {
  "workloads": { "flow": { "weight": 1, "max_tokens": 0, "on_exhausted": "pause" } },
  "agents": {                                  // ★追加（additive・省略可）
    "claude":  { "max_tokens": 3000000 },      // この CLI（＝アカウント）の枠
    "copilot": { "max_tokens": 1000000 }
  }
}
```

エンジンはこのキーを読まない（読む必要がない——枠の按分と縮退は従来どおりワークロード側の
契約で効く）。dashboard の候補選択だけが使う。`usage()` は既に `agents[cli].totalTokens` を
返しているので、集計側の変更は不要。

### 1.4 決定的アルゴリズム

新規モジュール `src/features/orchestration/main/profiles.js` に**純関数**として置く。

```js
// decide(profiles, usage, now) -> { [workload]: { tier, candidate, reason, changed } }
```

入力は `profiles.json` の内容・`budget.usage()` のスナップショット・現在時刻の 3 つだけ。
ファイル I/O も乱数も持たない（テスト可能性と決定性のため）。

**段の決定（workload ごと）**

1. `cap = usage.workloads[w].tokenCap`。`cap <= 0`（無制限）なら段は `policy.no_cap_tier`。
2. そうでなければ `remaining = max(0, 1 - totalTokens / cap)`。
3. `steps` を `min_remaining_ratio` の降順に見て、最初に `remaining >= min_remaining_ratio` を
   満たす段を素の候補 `tier0` とする。どれも満たさなければ最下位の段。
4. **ヒステリシス**: 前回の段 `prev` があり `tier0.order > prev.order`（＝上位へ戻ろうとして
   いる）ときは、`remaining >= min_remaining_ratio + hysteresis` を満たすときだけ昇格する。
   満たさなければ `prev` を維持する。下降側には上乗せを課さない（枯渇方向は素直に効かせる）。
5. **最小保持**: `now - prev.since < min_hold_sec` なら `prev` を維持する（上昇・下降とも）。

> 期間内（`period: day` 等）は消費が単調増加＝残率は単調減少なので、段は本来単調に下がる。
> ヒステリシスと最小保持が効くのは、**期間の切り替わり**と **`rebalance` による上限の変更**で
> 残率が跳ね上がる瞬間だけである。フラッピングの原因はこの 2 つに限られる。

**候補の決定（段が決まったあと）**

1. 段の `candidates` を宣言順に見て、その候補の `agent_cli` の枠が残っているものを採る。
   枠の残り = `allocation.agents[cli].max_tokens` が未設定 or 0 なら**常に残っている**扱い、
   設定されていれば `usage.agents[cli].totalTokens < max_tokens` で判定する。
2. その段の候補が全滅なら**一段下へ降りて**同じ探索を続ける（理由に `quota-fallback` を記録）。
3. 最下位まで降りても全滅なら、**そのワークロードには何も書かない**。枠が全部枯れている状態は
   モデルの選び直しで解ける問題ではないので、既存の `on_exhausted`（pause / stop / degrade）に
   委ねる——ここで勝手に lifecycle を触らない（書き手を増やさない・C7）。

**書き込み（副作用はここだけ）**

`apply(cfg, decisions)` が `control.saveControl(cfg, {workloads: {…}})` を呼ぶ。ただし
**現在の control.json の値と一致する決定は書かない**。理由: `saveControl` は必ず
`revision + 1` するため、毎分同じ値を書くと revision がインフレし、エンジン側の
`revision_applied` 突き合わせ（未反映の可視化）が意味を失う。差分があるときだけ書く。

`state[w].since` は**段が変わったときだけ**更新する（候補だけが変わった場合は据え置き
——`min_hold_sec` は段のフラッピングを止めるためのもので、枠枯れによる候補退避を
遅らせるべきではない）。

### 1.5 実装点

| ファイル | 変更 |
|---|---|
| `schemas/agent-profiles.schema.json` | 新規（上記 1.2） |
| `schemas/node-budget.schema.json` | `allocation.agents` を additive 追加（1.3） |
| `src/features/orchestration/main/profiles.js` | 新規。`load` / `save` / `decide`（純関数）/ `apply` |
| `src/features/orchestration/main/ipc.js` | `orchestration:profilesLoad` / `profilesSave` / `profilesDecide`（dry-run）/ `profilesApply` |
| `src/features/orchestration/preload.js` | 上記の橋渡し |
| `src/renderer/sections/orchestration.js` | 「実行プロファイル」カード（1.6） |
| `src/features/orchestration/main/index.js` 相当 | `interval_sec` のタイマー（`enabled && interval_sec > 0` のときだけ。アプリ終了で止まる） |
| `src/features/orchestration/README.md` | 契約表に agent-profiles を追加（**エンジンは読まない**を明記） |

### 1.6 画面（C4: 材料を揃えて 1 回で決めさせる）

orchestration セクションに 1 カード追加する。表示する情報は「いま何が選ばれ、なぜそうなり、
次に何が起きるか」の 3 点に絞る:

- 段の定義（大・中・小それぞれの候補列。ドラッグ順が優先順）と、しきい値・ヒステリシス・
  最小保持・評価間隔の編集
- ワークロードごとの**現在の選択**と**理由**（例: `flow: 中（残 34% < 50%・claude:sonnet）`）、
  および**次の境界までの距離**（例: `残 14pt で「小」へ`）
- agent CLI ごとの枠と消費（`claude 2.1M / 3.0M`）。枯れている CLI は候補行にその旨を表示
- 「いま評価する（dry-run）」ボタン — **書かずに**決定結果と差分を見せる。適用は別ボタン

### 1.7 テスト

`test/` に `profiles.test.js`（既存の dashboard テストと同じ流儀）:

- 純関数の性質: 同じ入力 → 同じ出力（時刻を固定して 2 回呼ぶ）
- 段の決定: 境界値（`remaining == min_remaining_ratio` は満たす側）・無制限（`cap = 0`）→
  `no_cap_tier`・`steps` 未宣言 → 何も決めない
- ヒステリシス: 下降は素直に効く／上昇は上乗せを満たすまで起きない
- 最小保持: `min_hold_sec` 内は段が動かない／経過後は動く
- 候補選択: 枠が枯れた CLI を飛ばす／段が全滅なら一段下へ／全滅なら**そのワークロードを
  書かない**
- 書き込み: 決定が現状と同じなら `saveControl` を呼ばない（revision が増えない）
- フェイルセーフ: `profiles.json` 不在・破損・`enabled: false` → 何もしない（例外を投げない）

### 1.8 非目標

- 料金表（どのモデルがいくらか）を持たない。宣言するのは段と候補と枠だけ。
- エンジン側の改修をしない。エンジンは今日どおり control.json を最優先で適用する。
- lifecycle（pause / stop）を自動で触らない。枯渇時の振る舞いは既存の `on_exhausted` の責務。

---

## 2. 案 B-1 — 差分修復リトライ（agent-flow）

> 柱2 / C1・C5・C7。**やり直しを「全作り直し」から「指摘箇所の修復」に変える。**

### 2.1 何を解くか

現状、`verify=fail` や失敗ノードの作り直しは `{"id": "<dep>-r1", "goal": "[retry] <元の goal>",
"deps": [], "replaces": "<dep>"}` という**まっさらなノード**として積まれる
（`continuation.py:_continue_stub` の 2)・3)、および evaluator が返す `new_tasks`）。
前回の成果物も失敗理由も渡らないので、エージェントは**ゼロから探索して作り直す**。
1 文字の修正で済む失敗でもタスク 1 本分のトークンを丸ごと再消費する。

### 2.2 設計の要——修復材料はグラフに持ち回さず、実行時にバスから導出する

素直な実装は「continuation が retry ノードへ `repair: {...}` を埋める」だが、これは採らない。
理由:

- retry ノードは **stub 経路**（`continue_stub`）と **evaluator 経路**（`continue_agent` が
  返す `new_tasks`）の 2 つから生まれる。前者だけに埋めると片方しか効かず、後者も埋めるには
  **LLM の出力契約を増やす**ことになる（＝プランナーが忘れたら効かない非決定性が入る）。
- 材料（前回の出力・成果物・verify の issues）はすべて**バス上の実状態**であり、グラフへ
  写すと同じ事実が 2 か所に載る（C7 違反）。

したがって **`work.py` の実行直前に、ノードの `replaces` からバスを引いて修復ブリーフを
決定的に組み立てる**。両経路が同じ 1 実装を通り、プランナー契約も出力契約も増えない。

### 2.3 決定的アルゴリズム

`work.py` のノード実行直前（`dep_results` を集めた後、`call_executor` の前）:

```
repair_brief(bus, node, cfg) -> dict | None
  1. cfg.repair_retry が false → None（オプトイン）
  2. prev_id = node["replaces"]（無ければ None → None を返す）
  3. _retry_depth(node) >= 2 → None
     （修復は同一系統で 1 回だけ。2 回目以降は従来どおり全作り直しへ戻す＝
       壊れた前回に引きずられて収束しないケースを有界化する）
  4. prev = bus.result(prev_id)（無ければ None）
  5. issues = 直近の verify ノード（deps に prev_id を含む kind=verify）の data.issues
             ∪ prev.output の [agent-error:*] 分類
  6. return {
       "of": prev_id,
       "output": prev.output を有界に切り詰めたもの（既定 4000 バイト・decode 安全な境界）,
       "artifact_dir": bus.node_artifact_dir(prev_id)（存在してファイルがあるときだけ）,
       "issues": issues,
       "delivered": prev.delivery（前回の変更が作業ブランチへ commit 済みか）
     }
```

worker プロンプトへの描画は**決定的なテキストブロック**（LLM 要約はしない）:

```
【前回の試行と差し戻し】このタスクは前回 <of> として実行され、次の理由で差し戻されました。
  指摘:
    - <issue 1>
    - <issue 2>
  前回の成果（抜粋）: …（有界）
  前回の成果物: <artifact_dir> （このディレクトリのファイルを読むこと）
  前回の変更はすでに作業ブランチへ反映されています。作業ツリーの現状が前回の結果です。
全体を作り直さず、指摘された箇所だけを直してください。前回正しかった部分は保持すること。
```

`delivered` が真のとき「作業ツリーの現状が前回の結果」と言えるのは、`finalize_workspace` が
成功時に作業ブランチへ commit 済みだからである（`work.py:189`）。この 1 行が「途中から」を
実質的に成立させる——エージェントは差分だけを見ればよい。

### 2.4 実装点

| ファイル | 変更 |
|---|---|
| `agent_flow/config.py` | `CONFIG_DEFAULTS` に `repair_retry: False` / `repair_excerpt_bytes: 4000` |
| `agent_flow/work.py` | `repair_brief()` の呼び出しと `call_executor(..., repair=…)` |
| `agent_flow/plugins.py` | `call_executor` に `repair` を追加（既存の `_executor_accepts` 能力検出と同型。受け取れない executor には渡さない） |
| `agent_flow/agent.py` | `execute_agent(..., repair=None)`。組み込みプロンプトへ描画ブロックを挿入し、`_flow_worker_prompt` の payload へ `repair` を追加 |
| `agent_flow/bus.py` | `node_result(nid)` 相当の読み出しヘルパ（無ければ追加） |
| `agent-flow.yaml.example` | `repair_retry` の説明 |
| skills `flow-worker` | payload の `repair` を描画（**未対応スキルでも壊れない**——組み込み fallback と同様、未知キーは無視されるだけ） |

### 2.5 テスト

`tests/test_run.py` / `tests/test_executor.py` へ:

- `repair_retry: false`（既定）では `repair` が渡らない＝**プロンプトが従来と 1 バイトも同じ**
- `replaces` を持つノードで、前回の output・artifact パス・verify の issues がブリーフに載る
- `_retry_depth >= 2` のノードにはブリーフを付けない（全作り直しへ戻る）
- 前回結果が無い／壊れている場合に例外を出さず `None` へ倒す（フェイルセーフ）
- 切り詰めが決定的（同じ入力 → 同じバイト列。マルチバイト境界で壊れない）
- stub 経路・evaluator 経路の**両方**で同じブリーフが出る（1 実装であることの証明）

### 2.6 リスクと緩和

| リスク | 緩和 |
|---|---|
| 壊れた前回に引きずられて修復が収束しない | 修復は同一系統 1 回だけ。2 回目は全作り直し。上限は既存の `max_retries`（サーキットブレーカー）の内側で、新しい無限ループを作らない |
| 前回の誤りを「正しかった部分」として温存する | 指摘（verify の issues）を先に置き、「指摘箇所を直す」だけを指示する。最終判定は従来どおり verify（C5）——修復したと自己申告しても done にはならない |
| ブリーフ自体が長くなり削減が相殺される | 抜粋は有界（既定 4000 バイト）。成果物は本文に貼らずパス参照（既存の中間成果物プロトコルと同じ流儀） |

### 2.7 非目標（Phase 2 以降）

- 段階チェックポイント（調査→実装→自己検査の途中再開・比較検討の案 B-2）。worker skill と
  実行機の改修を伴うため、B-1 の実測を見てから起こす。
- agent-project のタスク単位リトライ（新世代リトライ）への適用。同型だが別レイヤなので分ける。

---

## 3. 案 H — 安定プレフィックス化（agent-project / agent-flow / agentcore）

> 柱1 / C1・C7。**注入する内容は変えず、順序と経路だけを変えて実効単価を下げる。**

### 3.1 何を解くか

プロンプトキャッシュは**先頭一致**で効く。現状の `build_request`（`request.py:194`）は
**タスク固有の内容（タイトル・完了条件・タスク ID・フィードバック）を先に**置き、
**プロジェクト共通で不変の内容（charter・rules.md・リポジトリ理解）を後ろに**置いている。
これはキャッシュにとって最悪の並びで、タスクが変わるたびに全体が別プレフィックスになる。

### 3.2 やってはいけない直し方（そして、なぜか）

「`build_request` の中を並べ替えて安定部を先頭にする」は**採らない**。`request` 文字列の
先頭は、エンジンの複数箇所で**意味を持つ**からである:

- `patterns.py:202` `_first_line(request)` … 生成ノードの見出し
- `continuation.py:181` `request[:30]` … classify 後の専門タスク名
- `patterns.py:72` `plan_stub` … 段落分割による分解の入力

並べ替えると、見出しやノード名が「プロジェクト定義（charter…」で埋まる。**削減のために
可読性と分解の入力を壊す**のは割に合わない。

### 3.3 採る設計 — 安定文脈を request から外し、run スナップショットとして前置する

既に同型の仕組みがある: **グローバル指示（agent-instructions）**は request に畳まず、
run の `meta.instructions = {revision, text}` に固定され、ワーカーが
`prepend_instructions()` でプロンプト先頭へ前置し、マーカーで二重注入を防いでいる
（`instructions.py` / `bus.py:80` / `agent.py:857`）。これをそのまま踏襲する。

```
[ instructions ]      ← 既存。ノード横断で不変
[ project context ]   ← ★新規。charter / rules.md / リポジトリ理解。プロジェクト内で不変
[ role / kind 前置 ]  ← kind ごとに不変
[ repo_instruction ]  ← run 内で不変
────────────────────── ここまでが安定プレフィックス
[ artifact プロトコル ]
[ goal・deps・request ] ← ここから可変
```

- **agent-project**: `build_request` から charter / rules.md / repo_map の 3 ブロックを外し、
  `project_context_block(cfg, task)` として描画する。`agent-flow run --context-file <path>` で
  渡す（ファイル渡しにするのは ARG_MAX を避けるため。既存の spill と同じ理由）。
- **agent-flow**: `--context-file` を読み、`meta.context = {digest, text}` へ固定する
  （`snapshot_instructions` と同じ形・同じタイミング）。ワーカーは `instructions` と同じ経路で
  前置し、`<!-- project-context digest:… -->` マーカーで二重注入を防ぐ。
- **planner にも context を注入する。** 既存規約では instructions を planner / evaluator へ
  注入しないが、**charter と rules は分解の質に効く**（今日は request 経由で届いている）。
  経路を変えても情報を失わないよう、planner のプロンプトは
  `[planner 前置][project context][request]` の順で組む。evaluator も同様。

**結果として `request` はタスク固有の内容だけになる。** 見出し・パターン検出・並列度ヒントの
入力から charter / rules のノイズが消えるので、`_detect_pattern` の誤検出（実行規律の文言が
戦略選定を汚す、というコメントに残っている既知の問題と同型）はむしろ減る。ただしこれは
**挙動の変化**なので、オプトインの内側に閉じる（既定 off では従来どおり request に畳む）。

### 3.4 順序規約の 1 実装（C7）

`agentcore/promptcompose.py`（新規）に、区切りと順序だけを決める小さな純関数を置く:

```python
def compose(stable: "list[str]", variable: "list[str]") -> str:
    """安定部 → 可変部の順に、決定的な区切りで連結する。空ブロックは落とす。"""
```

agent-project と agent-flow の両方がここを通す。「同じ規約の別実装」を作らないための置き場で、
ロジック自体は小さい——重要なのは**両エンジンのゴールデンテストが同じ関数を縛る**こと。

### 3.5 実装点

| ファイル | 変更 |
|---|---|
| `agentcore/promptcompose.py` + `agentcore/tests/test_promptcompose.py` | 新規 |
| `agent_project/configfile.py` | `CONFIG_DEFAULTS` に `stable_prefix: False`。`Config` へ配線（`test_config_keys.py` の構造テストが到達性を強制する） |
| `agent_project/request.py` | `project_context_block()` を切り出し。`stable_prefix` が真なら `build_request` から 3 ブロックを外す |
| `agent_project/flow.py` | context ブロックを一時ファイルへ書き `--context-file` を付ける |
| `agent_flow/cli.py` | `run` に `--context-file` |
| `agent_flow/bus.py` | `snapshot_context()`（`snapshot_instructions` と同型。`meta.context = {digest, text}`） |
| `agent_flow/work.py` | meta から context を読み `call_executor(..., context=…)` |
| `agent_flow/agent.py` | `execute_agent` / `_flow_worker_prompt` payload / 組み込みプロンプトの順序を `compose` に載せる。planner・evaluator にも context を前置 |
| 各 `*.yaml.example` | `stable_prefix` の説明 |

### 3.6 テスト

- **プレフィックス安定性（この案の本体）**: 同一プロジェクトの**異なる 2 タスク**から組んだ
  プロンプトが、N バイト以上の共通プレフィックスを持つ（N は context の長さ由来で、
  ゴールデンで固定）。rules.md を変えると共通プレフィックスが変わる（＝安定部が正しく
  安定部である）
- **内容不変**: `stable_prefix` の on / off で、プロンプトに含まれる**ブロック集合**が同一
  （順序と経路だけが違う）。ここが崩れると「削減のために情報を落とした」ことになる
- **既定 off で完全不変**: 既存の request 組み立てテストがそのまま通る
- **二重注入なし**: context マーカーがあるプロンプトへ再前置しても増えない
- **planner への注入**: `stable_prefix` on のとき、planner プロンプトに charter / rules が
  含まれる（request から外れた分が失われていない）

### 3.7 効果の測り方と、正直な限界

**H はトークン数を減らさない。減らすのは入力トークンの単価である。** したがって:

- トークン台帳（node-budget）の `tokens_in` は**変わらない**。ここで効果を見ようとしても
  何も見えない——これを先に明記しておかないと「効かなかった」と誤判定する。
- 効果が観測できるのは、**CLI / プロバイダがキャッシュ利用または課金額を報告する経路がある
  場合に限る**。報告が無い CLI では、削減を実測で示せない（プレフィックスが安定していることは
  §3.6 のテストで決定的に示せるが、それが値引きになったかは分からない）。
- よって受け入れ条件は「プレフィックス安定性のテストが通ること」+「報告のある CLI で
  before/after のコストを比較すること」の 2 段構えにする。**報告の無い環境では
  『効いているはず』と主張しない。**

### 3.8 リスクと緩和

| リスク | 緩和 |
|---|---|
| 注入順が変わってモデルの注意配分が変わる（品質変動） | オプトイン。§5 の品質指標（verify 一発通過率・差し戻し率）を before/after で見る。悪化したら off に戻せる |
| context が長くなり全ノードのコストが増える | 3 ブロックとも既に有界（charter 1400 字・repo_map / rules も上限あり）。総量は今日と同じで、置き場所が変わるだけ |
| skill（flow-worker）が context を描画しない | 未知キーは無視されるだけで壊れない。engine 側の組み込み経路と `prepend` で最低限は必ず前置される |

---

## 4. 実装順序と受け入れ条件

3 案は互いに依存しない。**D → B-1 → H** の順を推す（外部影響の小ささ順）。

| # | 単位 | 受け入れ条件 |
|---|---|---|
| 1 | 案 D | エンジンのコード差分ゼロ。`decide` の純関数テストが全ケース通る。dry-run で決定と差分を確認してから適用できる。既存 dashboard テストが通る |
| 2 | 案 B-1 | 既定 off でプロンプトが従来と一致（バイト比較）。stub / evaluator 両経路で同じブリーフ。修復は 1 回で全作り直しへ戻る |
| 3 | 案 H | 既定 off で既存テスト全通過。on で「ブロック集合が同一・共通プレフィックスが N バイト以上」を満たす |

各単位は独立した PR にする（revert 可能性を保つ——効かなかった案を単体で戻せること自体が
C8 の一部）。

## 5. 計測（3 案共通・実装と並行して回す）

比較検討 §5 の指標を、この 3 案に合わせて具体化する。すべて既存の agent-audit と
node-budget 台帳から取れる（新しい計測基盤を作らない）。

| 指標 | 取り方 | 何を判定するか |
|---|---|---|
| ワークロード別・モデル別トークン | `agent-audit --by workload` / `--by model` | 案 D の効き（安い段の比率が上がったか） |
| 段の選択履歴 | `profiles.json` の `state`（変更時に追記される `since` / `reason`） | 案 D の妥当性（フラッピングしていないか） |
| リトライ率・修復成功率 | run の結果から「作り直しノード数 / 全ノード数」「修復ブリーフ付きノードの verify 一発通過率」 | 案 B-1 の効き。**修復成功率が低いなら逆効果**（2 回課金）なので off に戻す判断材料 |
| verify 一発通過率・差し戻し率 | 既存の run brief / decisions | 案 H の**品質**副作用（順序変更で悪化していないか） |
| キャッシュ / コスト報告 | CLI が報告する経路がある場合のみ | 案 H の効き（§3.7 の限界つき） |

## 6. 作業ゲート（コンセプト正典 §8-4 チェックリスト）

- [x] **人の承認が無くても品質が壊れないか（C5）** — 3 案とも done の判定に触らない。案 B-1 の
      修復も従来どおり verify を通らなければ done にならない。
- [x] **人に聞く前に機械的解決・材料の下ごしらえを試みているか（C3・C4）** — 案 D は残量巡回と
      モデル選択という定型判断を人から機械へ移す。適用前に dry-run で材料を見せる。
- [x] **決着・操作が特定の個人・PC・在席に依存しないか（C6）** — 案 D は当該ノードの
      dashboard がそのノードの control を書くだけで、他ノードの決着に影響しない。
- [x] **消費は持ち主の宣言の内側で、処理は必ず止まるか（C1・C7）** — 案 D は宣言された枠と段の
      内側でしか選ばない。案 B-1 の修復は 1 回限りで既存サーキットブレーカーの内側。
- [x] **状態の書き手・判断根拠を増やしていないか（C7）** — 案 D の書き手は dashboard 1 つ
      （エンジンは profiles.json を読まない）。案 B-1 の材料はバスの実状態から実行時に導出し
      グラフへ写さない。案 H の順序規約は agentcore に 1 実装。
- [x] **他人のノードへ配ってよい情報だけか（C1）** — profiles.json はノード局所で共有しない。
      案 H の context はプロジェクト共有物（charter / rules）だけで、ローカルパスを含まない。
- [x] **適用・検証・停止まで追跡できるか（C8）** — §5 の指標と、各案を単体で revert できる
      PR 分割。案 H は「測れない環境では効果を主張しない」ことを明記済み。
