# YAML ステートマシン スキーマ リファレンス

ワークフロー YAML ファイルの完全な仕様です。

## 目次

- [トップレベルフィールド](#トップレベルフィールド)
- [ステート定義](#ステート定義)
- [トランジション定義](#トランジション定義)
- [ワイルドカードトランジション](#ワイルドカードトランジション)
- [Config ブロック](#config-ブロック)
- [コンテキスト変数リファレンス](#コンテキスト変数リファレンス)
- [完全サンプル](#完全サンプル)

## トップレベルフィールド

| フィールド | 型 | 必須 | 説明 |
|-------|------|----------|-------------|
| `name` | 文字列 | はい | ワークフローの表示名 |
| `description` | 文字列 | いいえ | ワークフローの説明 |
| `initial_state` | 文字列 | はい | 開始ステートのID |
| `context` | オブジェクト | いいえ | 初期コンテキストのキーと値のペア |
| `config` | オブジェクト | いいえ | エンジン設定の上書き |
| `states` | オブジェクト | はい | state_id → ステート定義のマップ |
| `transitions` | リスト | はい | トランジション定義のリスト |

## ステート定義

```yaml
states:
  my_state:
    description: "短い人が読めるラベル"
    action: |
      このステートに入ったときに LLM へ送るプロンプト。
      テンプレート変数をサポート: {{variable_name}}
      利用可能な組み込み変数:
        {{input}}           - マシンに渡された元の入力
        {{last_output}}     - 最後に実行されたステートの出力
        {{current_state}}   - このステートのID
        {{step_count}}      - 発生したトランジションの数
        {{history.STATE_ID}} - 特定の名前付きステートの出力
    action_file: actions/my_state.md   # 外部ファイル参照（action より優先）
    terminal: false
    on_enter: "オプション: action に前置される追加指示"
    on_exit: "オプション: action の後、トランジション前に実行される指示"
    output_key: "my_key"  # このステートの出力を context.my_key にも格納
```

### ステートフィールド

| フィールド | 型 | 必須 | デフォルト | 説明 |
|-------|------|----------|---------|-------------|
| `description` | 文字列 | いいえ | ステートID | 人が読めるラベル |
| `action` | 文字列 | はい* | — | このステートの LLM プロンプト。`file: path` 形式でファイル参照も可。*終端ステートでは不要 |
| `action_file` | 文字列 | いいえ | — | アクションを外部マークダウンファイルで指定（`action` より優先）。workflow.yaml からの相対パス |
| `terminal` | 真偽値 | いいえ | false | true の場合、ここで実行終了 |
| `on_enter` | 文字列 | いいえ | — | action プロンプトに前置されるプレフィックス |
| `on_exit` | 文字列 | いいえ | — | action 後のプロンプト（出力は格納されるがルーティングには使用しない） |
| `output_key` | 文字列 | いいえ | — | `context[output_key]` にも出力を格納 |
| `max_retries` | 整数 | いいえ | 0 | `output_validator` 検証に失敗した場合の action のリトライ回数 |
| `output_validator` | 文字列 | いいえ | — | 出力の第1行を検証するルール。書式: `startswith:VAL1,VAL2` — いずれかの値で始まること。検証失敗時は `max_retries` 回までリトライする |
| `check` | 文字列 / 配列 / オブジェクト | いいえ | — | **決定的検査**。action の後にハーネスが実行するコマンド。終了コードが遷移の材料になる（[検査](#決定的検査-check)） |
| `check_retries` | 整数 | いいえ | `max_retries` | 検査が落ちたときに同じステートをやり直す回数 |
| `check_on_exhausted` | 文字列 | いいえ | `escalate` | 再投入を使い切っても落ちるときの動作。`escalate` \| `continue` \| `error` |
| `check_feedback` | 真偽値 | いいえ | true | 再投入時に検査の出力を課題文へ足すか |
| `max_tool_rounds` | 整数 | いいえ | 0（＝宣言なし） | 外部ハーネスがこのステートのツールループに許すモデル呼び出し回数（[呼び出し回数の上限](#呼び出し回数の上限-max_tool_rounds)） |

### 呼び出し回数の上限 (max_tool_rounds)

ステート 1 つを進めるあいだに、外部ハーネス（`agentcore.harness.statemachine`）がモデルへ何回
問い合わせてよいかの上限。**このエンジン自身は使わない**——宣言の置き場をステート単位に揃える
ための素通しである（`check_retries` と同じ階層に書ける、が対象は別物: `check_retries` は
「検査が落ちたらやり直す回数」、`max_tool_rounds` は「1 回の試行のなかで道具を呼べる回数」）。

決まり方は 宣言 ＞ 環境変数 ＞ ハーネスの既定（8）。

| 出どころ | 効く範囲 | 用途 |
|---|---|---|
| ステートの `max_tool_rounds` | そのステートだけ | 運用の意思。書いたら必ず勝つ |
| `AGENT_MAX_TOOL_ROUNDS_WRITE` | `write:` を宣言したステートだけ | 編集の周だけ締める腕 |
| `AGENT_MAX_TOOL_ROUNDS` | 全ステート | 実行全体を締める腕 |

環境変数を用意したのは腕を引くためで、腕ごとにワークフローを全部書き換えると「どの上限で
切られたのか」が後から読めなくなる。小型モデルで編集を回すときは 2〜3 が実測の推奨帯だが、
read 系・判定系まで一律に締めると調査の周が足りなくなる——だから編集用の環境変数を分けてある。

**宣言があるステートには環境変数は効かない。** 同じ規則は他の層にも及ぶ——たとえば
`agents/ollama.json` の `write_args` は `--max-rounds 12` を宣言しているので、
`agent-ollama` の本番実行は環境変数では締まらない。測定条件が運用の宣言を黙って
上書きしない側に倒してある（腕を引くなら宣言側で引く）。

```yaml
states:
  implement:
    write: src/humansize.py       # 編集対象を宣言したステート
    max_tool_rounds: 3            # ここでは 3 周まで（環境変数より優先）
```

### 決定的検査 (check)

`output_validator` が見るのは**モデルが書いた第1行の書式**であり、「PASS」と書くのはモデル自身である。
`check` は対照的に、**ハーネスが実行するコマンドの終了コード**を遷移の材料にする。モデルは検査の
中身にも結果にも触れない。

```yaml
states:
  implement:
    action_file: actions/implement.md
    output_validator: "startswith:OK"
    check: "python3 -m pytest tests/test_humansize.py -q"   # ← 事実はこれが決める
    check_retries: 2
```

宣言の形は 3 つ。**シェルは介さない**（argv を直接実行する）ので、パイプ・リダイレクト・変数展開は
使えない。必要ならスクリプトにまとめる — 文字列にシェル記号があれば投入前にエラーになる
（黙って別物を実行するより落とす）。

```yaml
check: "python3 -m pytest tests/ -q"                        # 文字列（shlex で分割）
check: ["python3", "-m", "pytest", "tests/"]                # 配列（分割済み）
check: {command: "python3", args: ["-m", "pytest"], timeout_sec: 300}   # 既定 120 秒
```

動きは 3 段。

1. action の後に検査を実行する。**通れば次へ**。
2. 落ちたら同じステートを `check_retries` 回までやり直す。`check_feedback: true`（既定）なら
   測った不一致を課題文へ足す（受入は真偽だけでも変わらないが、実測では再試行が 28% 速い）。
3. 使い切っても落ちるときは `check_on_exhausted` が決める。

| `check_on_exhausted` | 動作 | 使いどころ |
|---|---|---|
| `escalate`（既定） | 実行を止め、結果に `escalate: true` を立てる（agent-loop は終了コード 3） | **この段では解けない**の宣告。上位の段（クラウド）へ回すシグナル |
| `continue` | 検査結果をコンテキストへ入れたまま遷移評価へ進む | 失敗経路を自分でグラフに書く場合（修復ステートへ分岐する等） |
| `error` | 通常の実行エラーとして落とす | 落ちたら止まってほしいだけの場合 |

`escalate` を既定にしているのは実測による — 再投入で直るのは「仕様の読み違い」族だけで、
「作業の丸ごと欠落」族は同じ診断を突きつけても 9/9 が同形で失敗する。決定的に同じ壊れ方をする
以上、引き直しでは埋まらないので、上限は 1〜2 で足り、到達したら段を上げるのが正しい。

#### 検査結果のコンテキスト変数

検査を宣言したステートでは、次の 3 つが `condition_rule` から使える。**ハーネスが測った事実**
であって、モデルが書いたテキストではない。

| 変数 | 値 | 説明 |
|---|---|---|
| `check_status` | `"0"` / `"1"` / … / `"error"` | 終了コードの文字列。実行できなかった場合（タイムアウト・コマンド不在）は `"error"` |
| `check_ok` | `"true"` / `"false"` | 終了コード 0 かつ実行できた場合のみ `"true"` |
| `check_output` | 文字列 | 診断の先頭行（stderr 優先） |

```yaml
transitions:
  - from: implement
    to: review
    condition_rule: "equals:check_ok:true"      # 成果物が実際に通ったときだけ進む
    priority: 1
  - from: implement
    to: repair
    condition_rule: "equals:check_ok:false"     # check_on_exhausted: continue のとき
    priority: 2
```

検査を宣言していないステートからこれらのキーで分岐すると、キーが存在せず
`condition_rule` は LLM 評価へフォールバックする——「決定的に見ているつもりが自己申告で
決まっていた」に静かに戻る。**この組み合わせは検証エラーにする**（投入前に落ちる）。

#### 編集対象の割付 — `write`（agent-loop の headless 実行）

定型の事前分解では、そのステートが編集するファイルまで決まっている。`write` に宣言すると、
agent-loop の headless ハーネスは制御周（「次の一手」をモデルに訊く周）を挟まず、
最初から編集 CLI をそのファイルへ向けて呼ぶ。

```yaml
states:
  implement:
    action_file: actions/implement.md
    write: src/humansize.py            # 文字列またはリスト
    check: "python3 -m pytest tests/ -q"
```

割付は制御席のモデルに訊く仕事ではない——訊くと小型モデルは pytest 実行や pip install の
調査ループで周を使い切る（実機再測 2026-08-15 の失敗機序）。検査（check）だけを材料に
遷移するステートでは、編集 CLI が契約文を返さず黙って直しても、書込完了を機械契約で
受理して check に判定を委ねる。検査の再投入も同様に、前の試行が書いたファイルへの
編集から直接入る。

### アクションの自動探索

`action` も `action_file` も指定されていない場合、`actions/{state_id}.md` が存在すれば自動で読み込む。

## トランジション定義

```yaml
transitions:
  - from: source_state_id    # 任意のステートからは "*" を使用
    to: target_state_id
    condition: |
      最後のステートの出力に対して評価する自然言語条件。
      LLM がこの条件に対して YES か NO で回答します。
      
      良い条件の例:
        "最後の出力に ERROR という単語が含まれている"
        "分類結果が BUG または FEATURE である"
        "{{retry_count}} が 2 より大きい"
        "前のステートが JSON オブジェクトを生成した"
    condition_file: conditions/source_to_target.md  # 外部ファイル参照（condition より優先）
    priority: 0
    description: "このトランジションの任意ラベル"
```

### トランジションフィールド

| フィールド | 型 | 必須 | デフォルト | 説明 |
|-------|------|----------|---------|-------------|
| `from` | 文字列 | はい | — | 元ステートのID。ワイルドカードは `"*"` |
| `to` | 文字列 | はい | — | 遷移先ステートのID |
| `condition` | 文字列 | はい* | — | 自然言語条件（LLM が YES/NO で評価）。`file: path` 形式も可。*`condition_file` または `condition_rule` がある場合は不要 |
| `condition_file` | 文字列 | いいえ | — | 条件を外部マークダウンファイルで指定（`condition` より優先）。workflow.yaml からの相対パス |
| `condition_rule` | 文字列 | いいえ | — | 決定論的評価ルール。**LLM評価より優先**して実行される。書式は下記「condition_rule 書式」参照 |
| `priority` | 整数 | いいえ | 0 | 評価順序（小さいほど先） |
| `description` | 文字列 | いいえ | — | 人が読めるラベル |

### 条件の自動探索

`condition` も `condition_file` も指定されていない場合、`conditions/{from}_to_{to}.md` が存在すれば自動で読み込む（`from` が `*` の場合は `wildcard_to_{to}.md`）。

### 無条件トランジション

自動探索でも条件が見つからず `condition_rule` も無いトランジションは**無条件**として扱い、評価せずそのまま成立させる（空の条件文を LLM に渡さない）。

`next_state.py --auto-eval` は、最優先の候補が無条件のとき `conditions` を組まずに次の応答を返す:

```json
{"state": "fetch", "auto_advance": true, "next_state": "parse"}
```

`priority` が後ろの無条件トランジションは、前段の条件が全て偽のときのフォールバックとして解決する。

`auto_advance` が省くのは条件評価だけで、アクションの実行と `output_validator` による成功確認は省略しない。

### condition_rule 書式

LLM を介さずにコンテキスト変数を決定論的に評価する。**`condition_rule` が評価可能な場合は `condition` の LLM 評価をスキップする**（LLM API 呼び出しが削減される）。

```yaml
# 単一ルール
condition_rule: "startswith:last_output:PASS"

# 複合条件（セミコロン区切り、AND評価）
condition_rule: "startswith:last_output:RETRY;lt:retry_count:3"
```

| 演算子 | 意味 | 例 |
|---|---|---|
| `startswith:KEY:VALUE` | `ctx[KEY]` が `VALUE` で始まる | `startswith:last_output:PASS` |
| `contains:KEY:VALUE` | `ctx[KEY]` に `VALUE` が含まれる | `contains:analysis_result:ERROR` |
| `equals:KEY:VALUE` | `ctx[KEY]` が `VALUE` と等しい | `equals:last_output:BUG` |
| `regex:KEY:PATTERN` | `ctx[KEY]` が正規表現 `PATTERN` にマッチ | `regex:last_output:^(PASS\|FAIL)` |
| `lt:KEY:NUMBER` | `ctx[KEY]` < NUMBER | `lt:retry_count:3` |
| `gte:KEY:NUMBER` | `ctx[KEY]` >= NUMBER | `gte:processed_count:10` |
| `not-startswith:KEY:V` | `ctx[KEY]` が `V` で始まらない | `not-startswith:last_output:PASS` |
| `not-contains:KEY:V` | `ctx[KEY]` に `V` が含まれない | `not-contains:last_output:ERROR` |
| `not-equals:KEY:V` | `ctx[KEY]` が `V` と等しくない | `not-equals:last_output:SKIP` |

- キーが `ctx` に存在しない場合は `None`（LLM評価にフォールバック）
- 解析不能なルールは `None` として扱い LLM評価にフォールバック
- インライン実行では `next_state.py --context '{"last_output":"VALUE","KEY":"VALUE"}'` でコンテキストを渡す
  （旧引数 `--last-output` / `--output KEY=VALUE` も互換として受け付けるが、`--context` に無いキーの補完としてのみ効く）

### ワイルドカードトランジション

任意のステートから適用されるトランジションを作成するには `from: "*"` を使用します:

```yaml
transitions:
  - from: "*"
    to: error
    condition: "最後の出力に 'FATAL ERROR' というフレーズが含まれている"
    priority: 100  # 通常のトランジションの後に評価
```

## Config ブロック

```yaml
config:
  max_steps: 50          # 強制停止までの最大ステート遷移数
  on_max_steps: "error"  # "error" | "stop" | ジャンプ先 state_id
  on_no_transition: "error"  # 条件が一致しない場合の動作
  verbose: false         # 各トランジションの推論をログ出力
  condition_model: "your-model-id"  # 条件評価に使用するモデルID（省略時はエージェントのデフォルトモデルを使用）
  action_model: "your-model-id"     # アクション実行に使用するモデルID（省略時はエージェントのデフォルトモデルを使用）
```

## Context Variable Reference

In `action` and `condition` strings, use `{{variable}}` syntax:

| Variable | Description |
|----------|-------------|
| `{{input}}` | Original input to the machine |
| `{{last_output}}` | Most recent state output |
| `{{today}}` | 実行日（`YYYY-MM-DD`）。組み込み。`context:` で上書き可 |
| `{{now}}` | 実行時刻（ISO 8601・秒精度・タイムゾーン付き）。組み込み。`context:` で上書き可 |
| `{{current_state}}` | Current state ID |
| `{{step_count}}` | Number of completed transitions |
| `{{history.STATE_ID}}` | Stored output from state STATE_ID |
| `{{context.KEY}}` | Any custom context variable |
| `{{output_key}}` | Any state output stored with `output_key:` |
| `{{check_status}}` / `{{check_ok}}` / `{{check_output}}` | 直前の[決定的検査](#決定的検査-check)の結果（宣言したステートのみ） |

## Complete Example

```yaml
name: "Code Review Pipeline"
description: "Automated code review with iterative improvement"
initial_state: analyze
context:
  max_revisions: 3
  revision_count: 0

config:
  max_steps: 30
  verbose: true

states:
  analyze:
    description: "Analyze the submitted code"
    action: |
      Analyze this code for quality issues:
      {{input}}
      
      Identify: bugs, code smells, missing error handling, performance issues.
      Output a severity rating: PASS, MINOR, MAJOR, or CRITICAL.
      Then list specific issues found.
    output_key: analysis_result

  request_revision:
    description: "Request code revision"
    action: |
      Based on this analysis:
      {{analysis_result}}
      
      Write a clear, actionable revision request for the developer.
      Be specific about what needs to change and why.
    terminal: true

  approve:
    description: "Approve the code"
    action: |
      The code review is complete. Analysis:
      {{analysis_result}}
      
      Write an approval message confirming the code meets quality standards.
    terminal: true

  escalate:
    description: "Escalate critical issues"
    action: |
      CRITICAL issues were found that require immediate attention:
      {{analysis_result}}
      
      Write an escalation notice for the team lead.
    terminal: true

transitions:
  - from: analyze
    to: approve
    condition_rule: "startswith:analysis_result:PASS"  # 決定論的評価（LLM不要）
    priority: 1

  - from: analyze
    to: escalate
    condition_rule: "startswith:analysis_result:CRITICAL"
    priority: 2

  - from: analyze
    to: request_revision
    condition: "The analysis_result starts with MINOR or MAJOR"  # LLM評価
    priority: 3
```
