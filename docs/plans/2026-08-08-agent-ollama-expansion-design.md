# agent-ollama 適用拡大の設計 — クラウド予算をローカル実行系で節約する

> **統合済み**: 現行の責務・実装状態・未実装範囲は
> [`agent-ollama-design.md`](../designs/agent-ollama-design.md) へ統合した。本書は段階導入の詳細検討記録として残す。

> 作成 2026-08-08（同日改訂: 品質優先 R3・文脈健全性 R4 を要求に追加）
> 対象: `tools/agent-project` / `agent-flow` / `agent-audit` / `agent-loop` / `agent-amigos` の
> エージェント CLI 呼び出し面、`agentcore/ollama_*.py`、`agents/ollama.json`、
> agent-dashboard の実行プロファイル
> 関連: [2026-08-07-agent-ollama-tool-disclosure-design.md](./2026-08-07-agent-ollama-tool-disclosure-design.md) /
> [agent-cli-plugin-design.md](../designs/agent-cli-plugin-design.md) /
> [agent-tools-concept.md](../designs/agent-tools-concept.md) /
> [`tools/agent-tools/README.md`](../../tools/agent-tools/README.md)（agent-ollama の現行仕様）
>
> **状態: 段 0〜3 実装済み（2026-08-08）。段 4・5 は未着手**——§7 のとおり、
> 先に段 0〜3 の節約実績と品質実測を台帳で見てから判断する。§2 は現状の記録。
> ゲート: **柱1 / C1・C7** — クラウドクレジットの消費を持ち主の予算の内側に収め、
> 枠が枯れても作業を止めない。ローカル実行系は「予算内で止まる」を満たす受け皿である。

---

## 0. 結論の先出し

位置づけを半歩動かす。agent-ollama は「クラウドが使えないときのバックアップ」から
**「恒常的に安い役割を引き受ける節約先」**へ広げる。それに伴い要求を 2 つ足す。

- **R3 — 品質は時間で買う。** 壁時計は犠牲にしてよいが、成果の品質は上げる。
  具体的には think を有効へ反転し、モデルは RAM に収まる最大級を割り、
  ラウンド・言い直しの予算を引き上げ、done の確定は従来どおり機械検証だけに置く。
- **R4 — 文脈の健全性。** サーバに黙って切り捨て・圧縮をさせず、クライアント側でも
  **圧縮の繰り返しで延命しない**。文脈が尽きたら止めて、タスクを割る。
  圧縮 1 回ごとの全再 prefill（CPU で数分〜数十分）と情報欠落の累積は、
  停滞と品質低下を同時に招く——本書が避ける最悪形である。

節約の前半は既にある口の宣言変更で取れる。残りを塞ぐ改良は小さい順に 4 段で、
各段は独立して価値があり、前段で止めてよい。

| 段 | 内容 | 改修 | 何がローカルへ動くか |
|---|---|---|---|
| 0 | 設定・宣言のみ（§3） | ゼロ | agent-audit の extract、agent-flow のテキスト系 kind、クラウド枠枯渇時の自動退避 |
| 1 | `--format`（JSON の文法強制）+ think 反転（§4） | agent-ollama に数十行 + 定義 2 枚 | planner / evaluator / 判定系 kind。全役割の推論品質が上がる |
| 2 | 役割の readonly 宣言（§5） | エンジン設定に 1 キー + 引数 1 個の配線 | agent-project の判断系 purpose。クラウド CLI の権限も最小化 |
| 3 | read ツールセット（§6・既存提案の実装） | tool-disclosure 設計の段 2 | 探索を要する readonly 役割・agent-flow の verify・artifacts のパス参照 |
| 4/5 | edit セット / パッチモード（§7） | 同設計の段 3 + 新モード | work（ファイルを書くタスク） |

横断の方針が 2 本ある。**品質を時間で買う具体策は §8**、**文脈の健全性は §9**。
どの段を採る場合も両方を併用する。

---

## 1. 何を解くか — 適用を阻む 4 つの障害

クラウド CLI の予算が厳しい。一方でローカルの agent-ollama は契約適合の実行系として
実装済みであり（R1「止めない」/ R2「逐次監視できる」——現行仕様は
[`tools/agent-tools/README.md`](../../tools/agent-tools/README.md)）、時間を許容できる
役割なら今日から受けられる。それでも適用が広がらない理由は 4 つに集約される。

- **(a) JSON 出力の揺れ。** 小型モデルは末尾 JSON の出力契約を守り損ねることがある。
  エンジンの修復リトライが 1 回は拾うが、リトライは CPU 推論では数分単位の追い銭になる。
  JSON 契約の役割（agent-flow の planner / evaluator / filter / judge / reduce / split、
  agent-project の plan など）をローカルへ振る際の主障害。
- **(b) ツールが無いのでファイルが読めない。** 既定の agent-ollama は text→text のみ。
  agent-flow の artifacts はパス参照で後続へ渡る設計（`workspace.py:326-347`
  「本文には貼りません。次のパス内のファイルを読んで利用すること」）なので、
  ローカルへ振れるのは依存成果が本文に inline される経路だけ。探索を要する役割
  （repo_map 等）も同じ理由で不可。
- **(c) エンジンが全呼び出しを write モードで投げる。** Python 3 エンジン
  （agent-project / agent-flow / agent-amigos）は readonly 引数を一度も渡していない
  （agent-project は `prioritize.py:160-171` → `agentcli.headless_cmd` 既定 `readonly=False`）。
  ollama へ振ると、純テキストの判断役割にまで `--tools`（無制限 bash のループ）が生える。
  不要なラウンド往復が文脈を焼き、不要なシェルが付く。クラウド CLI 側でも同じ構図で、
  adjudicate 1 回のために `--dangerously-skip-permissions`（claude）や
  `--trust-all-tools`（kiro）が常時付いている。
- **(d) work（ファイル編集）の文脈予算と品質。** 速度は R3 で許容へ変わったので、
  work の実障害は速度ではない。bash ループはラウンドごとに会話が伸び、長いタスクほど
  文脈上限に迫る（R4 の敵）。また探索しながらの編集は小型モデルには品質の難所が残る。

---

## 2. 現状の記録 — どの呼び出しがどこまでローカルへ振れるか

### 2.1 役割の 3 類型

呼び出しを「CLI 側に要る能力」で分けると 3 類型になり、以降の段組みはこの類型に対応する。

| 類型 | 定義 | 例 |
|---|---|---|
| **読まない**（text→text） | エンジンが材料を全部プロンプトに組み、CLI は文章か JSON を返すだけ | agent-project の plan（`build_planner_input` が charter・notes・context/*.md を束ねる）・adjudicate・prioritize・route・assess・distill、agent-audit の extract / distill、agent-flow のテキスト系 kind |
| **読む**（探索） | CLI が自分でファイルを開いて初めて答えられる | agent-project の repo_map（「ローカルのリポジトリ {dest} を調査し」`plan.py:37`）・doctor、agent-flow の verify（ワークスペース検証）、artifacts のパス参照を跨ぐ kind |
| **書く** | ワークスペースの編集そのもの | agent-flow の work |

### 2.2 エンジン別の切り替え口（実装済みの事実）

| エンジン | 役割別の上書き口 | 既定 | 備考 |
|---|---|---|---|
| agent-project | `agents:` に purpose 10 種（plan / review / prioritize / route / adjudicate / verify / distill / assess / repo_map / doctor）で `{agent_cli, model}`（`prioritize.py:82-137`） | kiro | 全呼び出し write |
| agent-flow | `agents:` に planner / evaluator / worker + 個別 kind（`agent.py:46-109`） | kiro | planner / evaluator は readonly 既定（§5.2 の改訂）、他は write。verify は依存成果を本文 inline で受ける（`agent.py:851-895`） |
| agent-audit | `agents:` に extract / distill / review（`configfile.py:136-149`） | claude | yaml example に extract を ollama へ振る例が既にある |
| agent-amigos | `roles.yaml` の各ロールに `agent_cli` / `model`（`runner.py:76-99`） | ノード既定 | 全呼び出し write |
| agent-loop | ワークスペース共通の `agent_cli` + `agent_cli_options`（model / readonly / extra_args）。常に対話（tmux） | kiro | `agents/ollama.json` の `interactive`（`--tui`）と `slash` プロパティで接続済み |

### 2.3 agent-ollama 側の品質・文脈まわり（実装済みの事実）

- **think は分離済み。** API の `thinking` フィールドを本文と別に受けており
  （`ollama_loop.py:337,344`）、think を有効にしても思考が成果物本文へ混ざらない。
  現行の `agents/ollama.json` は `--think off` を焼き込んでいるが、その根拠は
  「JSON 出力契約の安定化」と decode 節約であり、前者は §4 の `--format` が引き受け、
  後者は R3 で許容へ変わった。
- **文脈は実測・警告・明示停止まで実装済み。** ContextTracker が使用量を常時実測し、
  90% で警告、ツール出力を残量に合わせて詰め、入らなくなったら `context_exhausted` で
  **こちらから止める**（サーバに黙って捨てさせない。`ollama_loop.py:434-492`）。
- **打ち切りは機械可読に申告する。** `done` 以外（`no_command` / `max_rounds` /
  `context_exhausted` / `tool_denied`）で終わったら、成果本文の末尾へ
  `{"ok": false, "issues": ["…（status=…）"]}` を足し、`@agent-note` を stderr へ出す
  （`ollama_adapter.py`）。成果は返す（R1 止めない）が、**呼び出し側が本文を読まずに
  未完了と判れる**ことが要件——封筒が無かった間、規約から外れたまま打ち切った
  `no_command` の途中経過（未実行のコマンドを含む報告）が rc=0 の完了として扱われ、
  成果ブランチへ push されていた。封筒の形はエンジン側の worker 契約
  （agent-flow の `{"ok": …}` 判定）と同一にして 2 実装にしない。
  `--format json` のときは足さない（本文全体が JSON 契約なので壊せない。JSON 契約の
  役割の未完了はエンジン側の形式修復リトライが受ける）。
- **`context_exhausted` はエラー分類済み。** `agents/ollama.json` の `errors` に
  `context_exhausted|文脈が不足` → `env` がある（§9 の 4 番）。`transient` にしないのが
  要点で、同じ壁に同じ時間を掛けてぶつかる無限停滞を作らない。

### 2.4 予算駆動の候補選択（実装済みの事実）

実行プロファイルの自動選択は agent-dashboard に実装済みで、エンジンは読まない
（[`schemas/agent-profiles.schema.json`](../../schemas/agent-profiles.schema.json) の不変条件）。
段の候補列から「agent_cli の枠が残っている最初の候補」を採り、枠の宣言が無い CLI は
**常に残っているとみなす**（`profiles.js:187-193` `未設定 = 常に残っている`）。

つまり**候補列の末尾に ollama の候補を書くだけで「クラウドの枠が枯れたらローカルへ退避」が
宣言だけで成立する**。エンジン改修も新機構も要らない。ollama には枠を宣言しない
（ローカル推論の上限は電気代と時間であり、トークン枠で縛る意味がない）。

---

## 3. 段 0 — 設定・宣言だけで今日できる適用

改修ゼロ。この段だけでもクラウド呼び出しの相当数が動く。

```yaml
# agent-audit.yaml — 局所要約はローカルで足りる
agents:
  extract: {agent_cli: ollama, model: qwen3}

# agent-flow.yaml — テキスト系 kind をローカルへ。work / verify / planner / evaluator は残す
agents:
  classify:   {agent_cli: ollama, model: qwen3}
  filter:     {agent_cli: ollama, model: qwen3}
  judge:      {agent_cli: ollama, model: qwen3}
  reduce:     {agent_cli: ollama, model: qwen3}
  split:      {agent_cli: ollama, model: qwen3}
  map:        {agent_cli: ollama, model: qwen3}
  synthesize: {agent_cli: ollama, model: qwen3}
agent_timeout: 3600   # 壁時計は最後の砦。実質の検知は agent-ollama の --stall-timeout
```

```jsonc
// profiles.json — 各段の候補列の末尾にローカル退避を置く（§2.4）
"candidates": [
  {"agent_cli": "claude", "model": "..."},
  {"agent_cli": "kiro",   "model": "..."},
  {"agent_cli": "ollama", "model": "qwen3"}   // 枠宣言なし = 枯れない最後の受け皿
]
```

サーバ側の設定は §9.1（文脈の健全性の一部）にまとめた。agent-loop の定常業務も、
軽いもの（棚卸し・要約系）は `agent_cli: ollama` へ振れる。

この段の制約が 2 つ残る。**(1)** agent-flow でローカルへ振れるのは依存成果 inline の
経路だけで、artifacts のパス参照を跨がせない（グラフ設計側の注意。§6 で解消）。
**(2)** JSON 契約の役割は揺れを覚悟でリトライに頼る（§4 で解消）。planner / evaluator /
work / verify をこの段でローカルへ振らないのはこの 2 つが理由である。

---

## 4. 段 1 — `--format` による JSON の文法強制と、think の反転

### 4.1 `--format json`

ollama の API は `format` フィールドを持ち、出力を JSON（または JSON Schema）に
**デコード時の文法制約**で強制できる。プロンプトに 1 トークンも足さないので
prefill の固定費が増えない——「読み込み時間は増やさない」という
[tool-disclosure 設計](./2026-08-07-agent-ollama-tool-disclosure-design.md)の方針とも整合する。

agent-ollama に `--format json` を足し、API リクエストへ透過する。数十行。
Schema 渡し（`--format-schema`）は**要るまで作らない**——`json` 強制だけで
「妥当な JSON でない出力」という故障モードが消え、エンジンの抽出器
（agent-project の `_extract_json_array`、agent-flow の末尾 JSON 契約 + 修復リトライ）は
そのまま満たされる。

### 4.2 think の反転 — off の根拠が消えたので on へ

`--think off` を焼き込んだ根拠は 2 つとも失効する。JSON の安定は `--format` が
文法レベルで保証し、decode の節約は R3（品質は時間で買う）で不要になった。
そして think の中身は `thinking` フィールドで本文から分離済み（§2.3）なので、
有効にしても成果物は汚れない。よって:

- 変種 `ollama-json` は **`--think on` + `--format json`** で定義する。
- 既定の `agents/ollama.json` も `--think off` → `--think on` へ反転する。
  データ変更のみで、think 非対応モデルで問題が出たら定義で戻せる。

**2026-08-10 改訂 — write だけ off へ戻した（実測による）。** 反転は「1 回の呼び出しに
思考時間を足す」前提だったが、ツールループは 1 ノードで最大 30 ラウンド回る。実測では
worker の 1 ラウンドが思考だけで 7700 トークン・12 分を消費し、そのあと書き出したのは
`if` の本体が空の構文エラーのコードだった——思考が品質に変換されていない。そこで think を
**役割で分ける**: `readonly_args`（planner / evaluator = 1 回で終わる判断役）は `on`、
`write_args`（道具を持って手を動かす側）は `off`。R3「品質は時間で買う」は撤回しないが、
買えているかを実測で確かめる対象に含める（§10.2 の観測へ think 軸を足す）。
**2026-08-10 再改訂 — readonly も off へ戻した。反転そのものが失効した。** 上の確認 1 点
（「think 有効時に `format` が本文側にだけ効くこと」）を実機で踏み、**効かないことが判った**。
文法制約は thinking チャネルの 1 トークン目から掛かる。qwen3.5:9b は答えの JSON を丸ごと
thinking に吐き、本文は空のまま `done` で終わる——`empty_output_is_error` が transient を上げ、
agent-flow の評価役が heal ループへ落ちる。ログ 236 本の内訳:

| 役割 / 設定 | n | 結果 |
|---|---|---|
| plain・think on・format json | 39 | **本文が空 39/39**（思考も JSON に縛られ推論は増えない） |
| plain・think on・format なし | 5 | 完走するが**中央値 1000 秒**（`agent_timeout` 既定 600 秒を超過） |
| plain・think off | 6 | 中央値 9.3 秒 |
| tools・think on | 149 | 中央値 497 秒・p90 942 秒・600 秒超 21 件 |
| tools・think off | 37 | 中央値 245 秒・600 秒超 0 件 |

think を活かす経路が読む側の役割に無い（format と併用すれば推論ゼロ、外せばタイムアウト）。
よって `readonly_args` も `off`、変種 `ollama-json` も `--think off + --format json` とする。
併用は `_payload` で `format` 優先に潰し、定義ファイルで復活できないようにした
（`test_format_wins_over_think`）。TUI だけは人が待てるので `on` のまま。

R3「品質は時間で買う」は**まだ検証されていない**。ここまで測れたのは所要時間と完走率だけで、
think が品質を上げるかは 1 件も測れていない（完走しない実行の品質は測れない）。検証は
§10.2 の口ではなく、記録済みプロンプトの**オフライン再生**で行う（§10.3）。

### 4.3 エンジンへの載せ方 — 定義ファイルの別名 1 枚

```jsonc
// agents/ollama-json.json — JSON 契約の役割専用の変種
{
  "name": "ollama-json",
  "command": ["agent-ollama", "--think", "off", "--format", "json", "{model}"],
  "write_args": [],   // 道具は持たせない（下記）
  // ほかは ollama.json と同じ（readonly: enforced / errors）
}
```

**実装時の修正 1 点**: この変種の `write_args` は空にした。当初案は ollama.json と同じ
`["--tools"]` としていたが、`--format json` 下では全出力が JSON になるため、
ツールループの規約（bash のコードブロックを出す）が成立しない——書き込みモードで
呼ばれた瞬間に言い直しだけで終わる。JSON 契約の役割は定義上「読まない系」なので、
道具を落としても失うものが無く、`readonly: enforced` の真実性はどちらのモードでも保たれる。

割り当ては定義側の申告で自動化する。`ollama.json` に `"json_variant": "ollama-json"` を
1 行足すと、エンジンは JSON 契約の役割に限って解決済みの CLI をそちらへ振り替える
（`agentcore.agentcli.json_variant` の 1 実装。agent-flow は `JSON_CONTRACT_ROLES`、
agent-project は `JSON_CONTRACT_PURPOSES` で「どの役割が JSON 契約か」を宣言する）。
`write_args` / `readonly_args` が argv 連結である契約の設計
（[agent-cli-plugin-design.md](../designs/agent-cli-plugin-design.md)）にそのまま乗る。

**改訂 2026-08-09（初版からの変更）**: 初版は「エンジン側は
`agents: {planner: {agent_cli: ollama-json}}` と書くだけ・エンジン改修ゼロ」としていた。
実運用では成立しなかった——agent-dashboard の全体設定は `workloads.flow.agent_cli` を
ワークロード一括で置く導線が主で、そこで `ollama` を選ぶと役割別宣言（control の
`agents.<role>`）を書かない限り split / planner まで素の定義で呼ばれる。control は
`agents[purpose]` より優先なので、設定ファイル側に何を書いても勝てない。結果、
JSON 契約の役割が制御語だけを返して空応答で落ち、再計画の予算だけが焼ける事故が出た。
節約のための設定を人の設定作業で払わせるのはコンセプト 柱3「チューニングの手間も人介在」
への違反でもあるので、**振り替えを既定の挙動へ格上げする**。人が明示した CLI を無視する
わけではない: 振り替え先は同じエンジン・同じモデルの起動形違いで、外したければ定義から
`json_variant` を落とす。

- **却下: エンジン設定に汎用 `agent_args` を足す。** 3 エンジンへの改修になる割に、
  得るものは定義 1 枚と同じ。役割ごとの起動形の違いは「CLI 定義の変種」として
  データで表すのが契約の思想である。
- **却下: エンジンが CLI 名を見て `ollama` のときだけ変種へ倒す。** §5.2 と同じ理由——
  CLI 特別扱いはプラグイン契約の思想に反する。「JSON 用の変種を持つか」は定義が申告し、
  「この役割は JSON 契約か」はエンジンが宣言する。両者とも自分が知っていることだけを言う。
- **注意: `--format json` は本文の説明文を殺す**（全出力が JSON になる）。
  人が読む本文が成果の役割（synthesize 等）には使わない。JSON 契約の役割
  （planner / evaluator / filter / judge / reduce / split、agent-project の plan・
  adjudicate 等）に限って変種を割り当てる。`verify` は寛容パーサと証跡の本文を伴い、
  `distill` は行形式、`repo_map` / `doctor` は散文なので対象外。

この段で、§3 の制約 (2) が消え、planner / evaluator もローカル候補に入る
（品質は §10 の観測で判断する）。

---

## 5. 段 2 — 役割の readonly 宣言（エンジン改修・最小）

### 5.1 何が起きているか

エンジンは readonly の口（`agentcli.headless_cmd(readonly=)`）を持っているのに、
Python 3 エンジンはどの役割でも使っていない（§1 (c)）。結果が二重に悪い。

- **ollama に振ると**: 読まない系の役割に `--tools`（無制限 bash のループ）が生える。
  純 text→text で済む仕事が、不要なラウンド往復で文脈を焼き（R4 に逆行）、
  不要なシェル権限を抱える。
- **クラウド CLI でも**: 判断だけの呼び出しに `--dangerously-skip-permissions`（claude）や
  `--trust-all-tools`（kiro）が常時付く。権限は常に最大で走っている。

### 5.2 設計

エンジン設定の `agents[purpose]` に `readonly: true` を 1 キー足し、
`headless_cmd(readonly=...)` へ配線するだけ。判断根拠はエンジン設定 1 か所（C7）。
既定は、**その役割が読まない系だと定義から決まっているものだけ readonly**、
残りは write（改訂 2026-08-09。初版は一律 write だった——次項）。

```yaml
# agent-project.yaml — 読まない系の purpose を readonly で宣言する例
agents:
  adjudicate: {agent_cli: ollama, model: qwen3, readonly: true}
  assess:     {agent_cli: ollama, model: qwen3, readonly: true}
  distill:    {agent_cli: ollama, model: qwen3, readonly: true}
  plan:       {agent_cli: ollama-json, model: qwen3, readonly: true}
```

readonly で振ってよいのは**読まない系（§2.1）に限る**。読む系（repo_map / doctor /
review）は、クラウド CLI の readonly 実装がツールを大きく削る形
（claude: `--permission-mode plan --tools ""`、kiro: `--trust-tools=`）なので、
readonly にすると探索まで失う恐れがある。読む系は当面 write のまま残し、
ollama で受けるのは §6（read ツールセット）以降とする。

- **却下: エンジンが CLI 名で分岐して ollama のときだけ挙動を変える。**
  CLI 特別扱いはプラグイン契約の思想（CLI を選ばないデータ契約）に反する。
  権限は役割の性質で決まるのだから、役割側で宣言する。
- **副産物**: この宣言はクラウド CLI にもそのまま効き、判断系呼び出しの権限が
  最小化される。節約の設計だが、安全側への修正を同梱している。

**改訂（2026-08-09）— 既定を一律 write から役割ベースへ。** 初版は「挙動を黙って変えない」
ことを優先して既定を write に置いたが、その前提は「CLI を選ぶのは設定ファイルを書く人」
だった。agent-control（`workloads.<engine>.agent_cli`）が横断で CLI を差し替えられるように
なった今、dashboard から ollama を選ぶだけで、設定ファイルを一行も書いていない環境の
planner に `--tools bash` が生える。実測（agent-flow・2026-08-08）では、モデルが契約どおりの
JSON を返しているのにツールループ側が「規約から外れています」と蹴り、30 ラウンド空回りして
タイムアウト、stub のキーワード判定まで縮退した run が 4 本続いた。既定 write は挙動を
守るどころか、役割の定義と矛盾した組み合わせを黙って作る。

そこで agent-flow の planner / evaluator——**材料を全部プロンプトで受け取ると定義側で
決まっている役割**——は宣言が無ければ readonly を既定にする（`agent.py` の
`READONLY_ROLES`）。`readonly: false` と明示すれば従来どおり write で呼べる。この既定は
「権限は役割の性質で決まる」という §5.2 の原則そのままであり、CLI 名で分岐しない点も
変わらない。読む系（repo_map / doctor / review）と work / verify は本節のとおり write のまま。

---

## 6. 段 3 — read ツールセット（既存提案の実装を前倒しする理由）

実体は [tool-disclosure 設計](./2026-08-07-agent-ollama-tool-disclosure-design.md)の段 2
（`--tools <セット>` + 実行ゲート）であり、設計判断はあちらが正典。本書からの
追加はこの 3 点。

1. **read セットを最優先で実装する価値が上がった。** あちらでは中間権限段の一部
   だったが、適用拡大の観点では read セットだけで障害 (b) が丸ごと消える——
   agent-flow の artifacts パス参照、verify のワークスペース検証、agent-project の
   読む系 purpose（repo_map / doctor / review）が一気にローカル候補へ入る。
2. **必要時読みは文脈の健全性にも効く（R4）。** 材料を全部 inline で渡す方式は
   プロンプトが太り、文脈上限との衝突を早める。read セットがあれば、エンジンは
   参照（パス）だけ渡し、モデルが要る分だけ読む——品質を落とさず文脈を細く保つ。
   §9 の方針と同じ向きである。
3. **readonly 経路への載せ方は保守的に段階を分ける。** `readonly_args: ["--tools", "read"]`
   と書けば読む系も readonly で宣言できるが、`readonly: enforced` の真実性が
   実行ゲートの強度（パイプ・リダイレクト・メタ文字の判定）に懸かる。あちらの §8 が
   自認するとおりここが最難所なので、**初版は write 経路の read セットのみ**とし、
   ゲートの拒否テストが揃ってから readonly_args への昇格を別途判断する。
   それまで読む系は「write モード + read セット」で振る（権限はゲートが絞る）。
   実装は定義 `agents/ollama-read.json`（`write_args: ["--tools", "read", …]`・
   `readonly_args: []`）で、readonly で呼べば従来どおり道具ゼロに戻る。

**実装時に決めたゲートの判定方式**（あちらの §8 が実装時判断へ委ねた箇所）:
語彙は許可制（読み取り系コマンド + git の読み取り部分コマンド）、**引用の外**の
シェル記号は一律拒否、実行は `bash -lc` を介さず argv 直渡し——メタ文字がそもそも
解釈されない形にして、判定と実行の二段でゲートを本物にする。引用の中を許すのは
`find . -name '*.py'` を通すため（read セットで最も使う探索を一律で殺さない）。
拒否は 3 回目で `tool_denied` として走行を止める（`_MAX_NUDGES` と同じ形の予算）。

---

## 7. 段 4・5 — work のローカル化（edit セット / パッチモード）

最後に残る障害 (d)。二つの経路があり、どちらも**段 0〜3 の節約実績と品質実測を
台帳で見てから**着手する（work はクラウド消費に占める割合が大きい一方、
ローカル化の品質リスクも最大。先に安い段で削れる量を確定させる）。

- **edit セット + ToolPolicy**: tool-disclosure 設計の段 3。探索しながら編集する
  タスクを扱えるが、ラウンドごとに会話が伸びる分、文脈予算（§9）と正面から
  衝突する。`--max-rounds` を引き上げるなら `num_ctx` とセットで見積もる。
- **パッチモード（`--patch`・新設）**: エージェントループをやめ、1 リクエストで
  対象ファイルを渡して SEARCH/REPLACE ブロックを返させ、**適用はアダプターが決定的に
  行う**。R3・R4 の両方に効くのが要点——適用が決定的なので「モデルがファイルを
  壊す」故障モードが構造的に消え（品質）、1 リクエスト完結なのでラウンド累積による
  文脈圧迫も起きない（健全性）。適用失敗（SEARCH 不一致）は 1 回だけ言い直しを促す
  （既存の nudge と同じ形）。対象ファイルが計画時に確定しているタスク専用で、
  「原因を探して直せ」型はクラウドか edit セットに残す。agent-flow のスコープ契約
  （変更 ≤30 行に割る）がこの前提を planner 側で既に作っている。
  - **置き場は agent-ollama へ統合する**（`--patch` モード + 定義変種
    `agents/ollama-patch.json`）。別コマンドを立てると ollama の接続・監視・think の
    知識が 2 か所に増える——tool-disclosure 設計が別コマンド案を退けたのと同じ理由。

---

## 8. 品質を時間で買う（R3・横断方針）

「速度と品質は犠牲にしてよい」というバックアップ運転の前提を半分だけ改める。
**犠牲にするのは時間だけ**。品質は次の 4 本で引き上げ、確定は機械検証に置く。

1. **think を常時有効にする**（§4.2）。推論品質への寄与が最も大きく、
   コストは decode 時間だけ。思考は `thinking` フィールドで分離済みで本文を汚さない。
2. **モデルは RAM に収まる最大級を割る。** 速度をモデル選定の基準から外す。
   目安は MoE（qwen3 系 30B-A3B、アクティブ約 3B）か dense 14B 級。上限は
   「KV キャッシュ込みで物理 RAM に収まること」——スワップに落ちた瞬間、
   遅いではなく停滞になる（R4 の最悪形。§9.1）。役割別に軽重を分けるのは
   その次の調整で、設定の口は既にある。
3. **やり直しの予算を引き上げる。** `write_args` に `--max-rounds 30`
   `--command-timeout 900` を足す（定義ファイルだけで効く。tool-disclosure 設計の
   段 1 と同じレバー）。言い直し（nudge）や修復リトライも「時間で品質を買う」の
   一部として惜しまない。ただしラウンドは文脈を消費するので、引き上げは
   `num_ctx` の確保（§9.1）とセットで行う。
4. **done の確定は機械検証だけ（C5・従来どおり）。** ローカル産の成果も
   agent-flow の verify / evaluator、agent-project の受入基準×証跡を必ず通す。
   ローカルへ振ったグラフでは verify ノードを省かない——検証こそ時間で買う価値が
   最も高い品質投資である（verify 自体も §6 以降はローカルで受けられる）。

- **却下: best-of-N・自己合議で品質を作る。** 時間はあるので N 回生成して
  ローカル LLM に選ばせる案は魅力的に見えるが、選定者が生成者と同じモデルでは
  根拠が増えない（自己相関）。品質の正は機械検証（C5）であり、verify が既に
  その役割を担う。N 倍の電気と時間で得るものが薄い。
- **却下: クラウドでの二重実行（ローカル産を常にクラウドで検算）。** 節約の目的に
  正面から反する。品質不足の役割は §10 の観測で特定し、設定でクラウドへ戻す。

---

## 9. 文脈の健全性（R4・横断方針）— 黙った切り捨ても、繰り返し圧縮もさせない

敵は 2 つある。**サーバの黙った切り捨て**（`num_ctx` 超過時に ollama はエラーを返さず
古い側を落とす。システムプロンプトが消えた answers が返り、品質劣化が「たまに指示を
無視する」として現れる）と、**圧縮・切り捨て後の読み直しの繰り返し**（切り詰め・要約の
たびに先頭が変わり、会話全体の再 prefill が CPU で数分〜数十分。これが反復すると
作業は前に進まず、要約のたびに情報欠落だけが累積する）。前者は品質を、後者は
品質と進捗を同時に壊す。方針は 5 本。

1. **文脈は先に余裕を確保する。** タスク種別ごとに `num_ctx` を見積もり、
   `AGENT_OLLAMA_OPTIONS` の `num_ctx` で明示する（サーバ既定に任せない）。
   RAM は KV 量子化（`OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0`）で
   賄い、**スワップに落ちない範囲が絶対上限**。あわせて `OLLAMA_KEEP_ALIVE=1h`
   （冷起動の除去）と `OLLAMA_NUM_PARALLEL=1`（接頭辞キャッシュの前提。同時
   リクエストはサーバ側で直列に待ち合わせるため、エンジン側の並列制御は不要）。
2. **使用量は常時実測し、尽きる前に見える化する**（実装済み・§2.3）。
   ContextTracker の 90% 警告・ツール出力の詰め・`context_exhausted` の明示停止を
   そのまま使う。`--status` / `@agent-context` 行が「いまどれだけ埋まっているか」を
   人にもプログラムにも示す。
3. **圧縮で延命しない。** クライアント側に「要約して続行」の自動ループを
   **実装しない**（非目標として固定する）。圧縮 1 回は全再 prefill 1 回であり、
   繰り返した時点で停滞が確定する。文脈が尽きたら途中成果 + `@agent-note` を返して
   止まり（実装済み）、**続きはタスクを割って新しい会話でやる**——エンジンの
   再計画・スコープ契約（agent-flow の granularity・リトライ時の世代交代）が
   その受け皿で、分割された各タスクは小さく新鮮な文脈で品質を保てる。
4. **`context_exhausted` を再試行させない**（実装済み）。未分類のままだとエンジンが
   同一入力で再試行しうる——同じ壁に同じ時間を掛けてぶつかる無限停滞の芽。
   `agents/ollama.json` の `errors` はこれを持つ:

   ```jsonc
   { "match": "context_exhausted|文脈が不足",
     "class": "env",
     "hint": "文脈上限に達しました。タスクを小さく割るか、AGENT_OLLAMA_OPTIONS の num_ctx を引き上げてください" }
   ```

   transient にしないのが要点（transient は「リトライで解ける」の宣言であり、
   同一入力の再試行では解けない）。env 分類なら人と再計画に判断が渡る。
5. **既出の文脈を書き換えない**（追記のみ）。tool-disclosure 設計 §4.4 の不変条件と
   同一。先頭を 1 バイトでも書き換えると全再 prefill が発生する——ラウンド途中の
   開示・注入はすべて末尾追記で行う。

方針 1〜2 が「黙った切り捨て」を、3〜5 が「繰り返し圧縮の停滞」を塞ぐ。

---

## 10. ルーティングの分担と品質の観測

### 10.1 2 つの機構を混ぜない

ローカルへ振る判断は 2 種類あり、担当が違う。ここを混ぜると判断根拠が 2 か所になる。

| 判断 | 機構 | 書き手 |
|---|---|---|
| **恒常的な振り分け**(この役割はローカルで品質が足りる) | エンジン設定の `agents:`（§3・§5） | 人（設定ファイル） |
| **枯渇時の退避**（クラウドの枠が尽きたらローカルで凌ぐ） | 実行プロファイルの候補列末尾（§2.4） | agent-dashboard の決定的選択 |

- **却下: エンジン内の自動フォールバック**（呼び出し失敗時に別 CLI で再試行する層を
  エンジンへ足す）。候補選択の判断根拠は dashboard のプロファイルに 1 実装あり（C7）、
  quota 失敗はエラー分類として既に台帳へ残る。エンジンに第二の選択実装を持たせない。

### 10.2 品質は観測して戻す — 自動昇格は作らない

**2026-08-10 実測 — qwen3.5:9b は worker として不合格（受入 2/21）。** §11 が待っていた
「品質実測」がこれ。agent-project / agent-flow / bus を通さず、`write_args` と同じ argv・
flow-worker スキルの同じプロンプト・使い捨て worktree で 21 run を回し、合否は決定的
チェッカー（LLM 判定なし）で出した。ハーネスは判定役を一切使わない——偽 done が真因なので、
自己申告を指標に混ぜると測定が消える。

| 引いたレバー | 受入 |
|---|---|
| 基準（上限 600 秒 = `agent_timeout` 既定） | 1/9 |
| 予算 3 倍（1800 秒） | 0/3 |
| 粒度を極小（1 ファイル 1 関数・テスト契約なし） | 1/3 |
| 暴走止め（`options.num_predict=4000`） | 0/6 |

独立な 3 本のレバーが全部空振りしたので、原因は予算でも粒度でも暴走でもなく能力。
失敗様式は timeout 12 / returned 4 / cli_error 3 / 自己申告未完了 2。

副産物として**エンジンの穴が 1 つ出た**: 1 ラウンドあたりのトークン上限が無い。ある
ラウンドで停止トークンが出なくなり、10 tok/s で 19771 トークンまで書き続けた例がある
（停滞検知は「無進捗」を見るので、書き続ける暴走には反応しない）。`load_options` で
`num_predict` の既定（4096）を入れて塞いだ。明示指定はそのまま尊重するので、天井を
動かしたい呼び出しは `AGENT_OLLAMA_OPTIONS` で上書きできる。切られたことは `llm_end` の
`done_reason="length"` で見える——これが無いと、途中で切れた成果物が「そこで書き終えた
モデル」と区別できない。

測定手順とハーネスは [`tools/agent-tools/eval/`](../../tools/agent-tools/eval/README.md) に置いた。
`--model` を変えれば別モデルを同じ 21 run で 1 時間で判定できる。

段 4・5 はこの実測を根拠に**着手しない**。ローカル worker の受入率がこの水準では、
edit セット / パッチモードで削れるのは通らない実行のコストだけになる。

ローカルで失敗が続いたらクラウドへ自動昇格する機構は作らない。昇格判断には品質の
実測が要り、その実測がまだ無い。代わりに観測の口だけ決めておく:
`agent-audit --by model` / `--by workload` で役割別の実測トークン・失敗分類・
リトライ率を見る。**品質の一次指標は verify の PASS 率**（C5 の正をそのまま使う。
自己申告や印象では判断しない）。ローカルへ振った役割の verify 不合格率・リトライ率が
クラウド時代より明確に悪い（目安: 2 倍超）なら、その役割を設定でクラウドへ戻す——
判断は人、実行は §10.1 の恒常的な振り分けの変更で足りる。

---

## 11. 受け入れ基準

- **節約が測れる**: `agent-audit --by workload` のクラウド CLI トークンが、段 0 適用前の
  基準値から下がる（下がり幅は台帳の実測で報告し、目標値は運用で決める）。
- **品質が落ちていない**: ローカルへ振った役割の verify PASS 率・リトライ率が
  クラウド時代の実測と比べて許容内（§10.2。悪化した役割は設定で戻した記録が残る）。
- **段 1**: `--format json` を付けた役割で、JSON 抽出失敗による修復リトライが 0 に近づく。
  think 有効時の成果物本文に思考の混入が 0（`thinking` 分離の確認）。
- **段 2**: readonly 宣言した purpose の呼び出し argv に `write_args` が含まれない
  （定義検証テストで固定。ollama なら `--tools` が付かないこと）。
- **段 3**: read セットの実行ゲートが許可外コマンド・メタ文字入りコマンドを拒否する
  テストを持つ（tool-disclosure 設計の最難所の顕在化）。
- **文脈の健全性（R4）**: ollama サーバログに `truncating input prompt` が出ない。
  同一タスクが `context_exhausted` で 2 回以上再試行された記録が 0
  （分割か num_ctx 引き上げに落ちている）。壁時計 timeout で kill された実行が
  JSONL 進捗ログ上「進捗あり」だった件数 0（stall 検知が主役のまま）。

---

## 12. 非目標・決めていないこと

- **速いローカル推論を作ることは目標ではない。** 本書は一貫して時間を犠牲にする側に
  倒す（R3）。遅さが問題になる役割はローカルへ振らない、が答えである。
- **クラウドの完全代替は目標ではない。** 探索型 work・新規性の高い設計判断は
  当面クラウドに残る。
- **ローカル→クラウドの自動品質昇格は作らない**（§10.2）。
- **クライアント側の自動コンパクション（要約して続行）は作らない**（§9 方針 3）。
  文脈が尽きたら止めて割る、を唯一の道にする。
- **read / edit セットの語彙とゲート判定方式は決めない**——tool-disclosure 設計の
  実装時判断に委ねる（本書はその優先順位だけを動かした）。
- **`num_ctx` の具体値・モデルの具体選定**は運用判断。見積もりの原則（RAM に収まる
  最大級・スワップ厳禁）だけを本書が固定する。
