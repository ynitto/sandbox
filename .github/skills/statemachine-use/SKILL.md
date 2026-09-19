---
name: statemachine-use
description: 「ステートマシンを実行して」「ステートマシンを作成/作って」「YAMLワークフローを動かして」「ワークフローを回して」「エージェントループを起動して」「このYAMLを実行して」などで発動。作成モード（手順を.statemachine/{名前}/に生成）と実行モード（YAMLをLLM駆動で実行）を持つ。
metadata:
  version: 2.2.0
  tier: experimental
  category: workflow
  tags:
    - statemachine
    - yaml-workflow
    - agent-loop
    - hybrid-execution
---

# YAML ステートマシン スキル

YAMLと外部マークダウンファイルで定義されたLLM駆動ステートマシンを**作成・実行**します。

---

## モードの選択

| ユーザーの意図 | モード |
|---|---|
| 「〜という手順でステートマシンを作って」 | **作成モード** |
| 「〜を実行して」「〜を動かして」「YAMLを回して」 | **実行モード** |

---

## 作成モード

ユーザーが自然言語で説明した手順を `.statemachine/{名前}/` フォルダ以下のYAML+マークダウンに落とし込む。

### ステップ1: 利用可能なスキルを調査する

```bash
ls .github/skills/
```

出力されたスキル名を記録する。アクション定義でスキル呼び出しを活用できる場合に参照する。

### ステップ2: 手順を状態遷移として分解する

**LLM読み飛ばし防止の設計原則（重要）**

1. **ルーティングロジックをアクションに書かない** — 分岐判断はトランジション条件に書く
2. **出力形式を強制する** — 条件が評価しやすいキーワード出力を要求する（例: `PASS / FAIL`）
3. **将来のステートをヒントとして含めない** — アクションは現在のステートの作業のみを指示する
4. **アクションの末尾に単一指示を付与する** — 全アクションmdの末尾に必ず下記を追記する:
   ```
   この指示に従ってタスクを実行してください。
   完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
   ```
5. **スクリプトは原則作成しない** — スキル（ステップ1で確認）や他のAI機能でアクションを実行する
6. **スキルへ移譲するときはスキル名を明記する** — アクション本文に `` `skill-name` スキル `` と書く。この記法が無いと実行ハーネスはスキルを読み込まず、スクリプトの場所も分からない
7. **成功条件を `output_validator` で定義する** — 「第1行が `OK` か `FAILED`」のような機械が判定できる出力契約を states に書く。書かないとアクションの成否を確認できず、失敗したまま次のステートへ進む
8. **成果物の正しさは `check` で測る** — `output_validator` が見るのは書式だけで、「OK」と書くのはモデル自身である。**成果物が実際に仕様どおり動くかを見るには、ハーネスが実行する検査コマンドを宣言する**（下記）
9. **1 ステート 1 成果物** — 1 つのステートで作るファイルは 1 つだけにする（`write` に 2 つ以上を宣言した定義は投入前に落ちる）。小さいモデルは成果物を 2 つ同時に渡されると片方を丸ごと落とし、再投入を積んでも同じ落ち方をする（実測: 一括 0/3・1 成果物ずつ 3/3）。実装とテストなら 2 つのステートに割り、それぞれに `check` を付ける
10. **分類・振り分け・段階の評価だけのステートは `judge:` で書く** — 「N 語のどれかを 1 語で答える」ステート（issue_triage の classify、レビューの結論など）は、アクションを書かず `judge:` に問いと選択肢を書く（`references/schema.md`「判定だけのステート」）。判定 AI があれば生成 0 で終わり、無ければ宣言から作った短いプロンプトで 1 回だけ生成する。理由や本文が要るステートには使わない（それは通常のアクション）
11. **出力の内容で分岐する遷移は `outcome` で書く** — 同じステートから出る候補ごとに「この遷移が成立する結果」の短い名前を `outcome:` に書く（下記）。判定 AI（agent-herd の judge）はそれを選択肢にして「結果はどれか」を **1 問**で選ぶ——候補ごとに YES/NO を訊くより速く安く、2 つの条件が同時に真になる矛盾が構造として消える。judge が無い環境では同じ `outcome` が条件文として LLM に渡るので、定義を書き分けなくてよい

**`outcome` — 分岐を「条件の列」ではなく「結果の選択肢」として書く**

```yaml
transitions:
  - from: review
    to: approve
    outcome: "指摘なしで承認できる"        # 判定 AI の選択肢 A
    priority: 1
  - from: review
    to: revise
    outcome: "直すべき指摘がある"          # 選択肢 B
    priority: 2
  - from: review
    to: ask
    outcome: "判断できない"                # 選択肢 C（どれでもない、は自動で足される）
    priority: 3
```

- 同じ元ステートの **LLM 評価が要る候補すべて**に `outcome` があるときだけ 1 問の選択になる。1 つでも欠けると従来どおり条件ごとの YES/NO
- `condition_rule` で決まる候補（`check_ok` など）は選択肢に入らない。測れるものは測り、測れない残りだけを判定 AI に選ばせる
- `condition` と併記してもよい（`condition` は judge が無い経路の条件文、`outcome` は選択肢の名前）。`condition` を省くと `outcome` が条件文の代わりになる

**`check` — 遷移の材料を自己申告から実測へ移す**

```yaml
states:
  implement:
    action_file: actions/implement.md
    output_validator: "startswith:OK"      # 書式（モデルが書く）
    check: "python3 -m pytest tests/test_x.py -q"   # 事実（ハーネスが測る）
    check_retries: 2
transitions:
  - from: implement
    to: review
    condition_rule: "equals:check_ok:true"  # 実際に通ったときだけ進む
```

検査が落ちたら、測った不一致を課題文へ足して**同じステートをやり直す**。再投入を使い切っても
落ちるなら、実行を止めて `escalate`（この段では解けない = 上位の段へ回すシグナル）を返す。

ローカルモデルで定型作業を回すなら、これが**受入率を決める唯一のレバー**である。実測では
検知を伴わない分解は受入を下げ（0/3）、決定的な検知 + 再投入で 3/3 になった。逆に**通る課題に
ゲートは課金しない**（呼び出し回数は増えない）。書式・使える宣言の形・失敗時の動作は
`references/schema.md` の「決定的検査 (check)」を参照。作例は `examples/gated_implement.yaml`。

**検査を置ける単位で割る** — ステートを細かく割ること自体に効果は無い（実測では逆に下がる）。
分解の目的は**検査を差し込む場所を作ること**である。1 つのステートを設計するとき、
「このステートの成果は、どのコマンドの終了コードで測れるか」を先に決める。決められないなら、
そのステートはまだ割り方が正しくない。

**実行できるスクリプトの範囲**（ハーネスが強制する。定義側もこれに合わせて書く）:

| 決まり | 意味 |
|---|---|
| アクション本文が名指しした `.py` / `.js` / `.sh`、または移譲先スキルの SKILL.md に載っている `.py` / `.js` / `.sh` のみ | `scripts/` に置いてあるだけの下請けは呼べない |
| 固定インタプリタで実行する（`.py`→python / `.js`→node / `.sh`→bash または sh） | shebang や実行ビットで走らせるものを決めさせない |
| スキル名は実行コマンドではない | `` `demo` スキル `` の `demo` を command に置いても動かない。スクリプトのパスを書く |
| `bash -c` などの任意シェルは使えない | シェル経由の合成コマンドは拒否される |
| コマンドの stdout が空でも exit 0 なら成功 | 出力の有無で成否を判定しない。空の結果は正常な空結果 |

**パターンの自動検出** — 詳細テンプレートは `references/patterns.md` を参照:

| 手順の特徴 | 適用するパターン |
|---|---|
| 「同時に」「並列で」「〜と〜を一緒に」 | Fan-out/Fan-in |
| 処理後に次があるか確認してループ | ContinueAsNew Loop |
| 副作用の大きい操作（変更・デプロイ等）の後 | ゲートステート |
| 複雑な判断・推論を含むステート | ReActアンカリング |
| 「失敗したら元に戻す」「ロールバック」 | Saga |
| 5ステート以上の長いワークフロー | マイルストーンアンカー |

### ステップ3: scaffold で骨組みを生成する

フォルダとファイルを手で書かない。ステップ2で決めた状態列を scaffold へ渡す:

```bash
python .github/skills/statemachine-use/scripts/scaffold.py {名前} \
  --state "first_state:説明" --state second_state
```

- `--state ID[:説明]` を実行順に並べる。終端は `--terminal ID[:説明]`（省略時は complete を自動で足す）。
- `.statemachine/{名前}/` に workflow.yaml と actions/*.md スタブを生成し、**生成直後に検証する**
  （通らない骨組みは残さない）。
- 骨組みは直列遷移。分岐・ループ・複雑な条件（`conditions/{from}_to_{to}.md`）はステップ4で足す。
- 新スキーマの口（`output_validator` / `check` / `check_retries` / `check_on_exhausted` / `write`）は
  コメント付きで含まれる——ステップ2で決めた検査コマンドのコメントを外して実値にする。

### ステップ4: スタブを埋める

生成された workflow.yaml と actions/*.md を以下の形へ埋める。全フィールドの仕様は `references/schema.md` を参照:

```yaml
name: "ワークフロー名"
initial_state: first_state
context:
  # 初期変数（ループカウンター等）
config:
  max_steps: 30

states:
  state_id:
    description: "ラベル"
    action_file: actions/state_id.md   # 外部ファイル参照（推奨）
    output_key: result_key             # 任意: context に名前付き保存
    terminal: false

transitions:
  - from: state_id
    to: other_id
    condition: "自然言語条件"           # or condition_file: conditions/...md
    priority: 1
```

**actions/{state_id}.md:**

```markdown
## [state_id: 何をするか]

（スキル呼び出しや具体的な指示）

**入力:** {{input}}
**前のステートの出力:** {{last_output}}

**出力形式:** XXX または YYY の一語のみで回答してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

**conditions/{from}_to_{to}.md（複雑な条件のみ）:**

```markdown
以下の条件をYES/NOで評価してください:
- {{retry_count}} が {{max_retries}} 未満である、かつ
- 最後の出力が RETRY で始まる

両方を満たす場合のみ YES と回答してください。
```

### 作成例

```yaml
# .statemachine/review_code/workflow.yaml
name: "コードレビュー"
initial_state: analyze
states:
  triage:                                    # 判定だけのステートは judge で書く（生成 0）
    judge:
      question: "このコードの変更はどの種類か"
      choices: {BUGFIX: "不具合の修正", FEATURE: "機能の追加", REFACTOR: "振る舞いを変えない整理"}
    output_key: change_kind
  analyze:
    action_file: actions/analyze.md
    output_key: analysis_result
  approve:
    action_file: actions/approve.md
    terminal: true
  request_revision:
    action_file: actions/request_revision.md
    terminal: true
transitions:
  - from: analyze
    to: approve
    condition_rule: "startswith:analysis_result:PASS"   # 第 1 行の書式で決まるなら測る
    priority: 1
  - from: analyze
    to: request_revision
    outcome: "直すべき問題が見つかった"                    # 残りは判定 AI に選ばせる
    priority: 2
  - from: analyze
    to: approve
    outcome: "問題は見つからなかった"
    priority: 3
```

```markdown
<!-- .statemachine/review_code/actions/analyze.md -->
## [analyze: コード品質を分析する]

以下のコードを品質の観点で分析してください。
**対象コード:** {{input}}

確認項目: バグ、コードの臭い、エラーハンドリング漏れ、パフォーマンス問題

**出力形式:** 最初の行に PASS / MINOR / MAJOR / CRITICAL のいずれか一語、その後に問題点を列挙してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
```

### 定義のメンテナンス — migrate

スキーマは加算的に拡張される（check → check_on_exhausted → write）。手持ちの定義は
migrate で検査し、追随させる:

```bash
python .github/skills/statemachine-use/scripts/migrate.py path/to/workflow.yaml   # dry-run（検出と差分）
python .github/skills/statemachine-use/scripts/migrate.py .statemachine --apply   # フォルダごと適用
```

検出項目: check 宣言の無いステートからの `check_*` 分岐（検証エラー・修正案の提示）、
シェル記号入り `check`（投入前に落ちる）、`write` 未割付（編集対象が一意に決まる場合だけ提案）、
`check_on_exhausted` の暗黙既定の明示化。後ろ 2 つはコメントを保ったまま `--apply` で書き換える。
正規化・検証は engine.py の 1 実装を使うので、判定が実行系とずれることはない。

---

## 実行モード

### ⛔ ハーネス実行プロトコル — 禁止行動（違反時は即座に停止して再確認）

| 禁止行動 | 代替行動 |
|---|---|
| アクション実行前に条件リストを取得する | ① 実行 → 出力確定 → ② 条件取得 の順を守る |
| 現在のステート以外の作業を実行する | 現在のステートの作業のみ実行する |
| `## [現在のステート: {state_id}]` 宣言を省略する | 毎ステートの冒頭で必ず宣言する |
| 条件を評価せずに遷移先を独断で決める | 必ず ④ の Python スクリプトで遷移先を確定する |
| 複数ステートをまとめて実行する | 1ステート = 1ターンを厳守する |

---

### Step 0: 検証と開始ステートの取得

```bash
# ワークフローの検証
python .github/skills/statemachine-use/scripts/run_machine.py .statemachine/{名前}/workflow.yaml --dry-run

# 開始ステートの取得
python .github/skills/statemachine-use/scripts/next_state.py {名前} --initial-state
```

出力された `state_id` を現在のステートとして実行を開始する。

**agent-herd の有無を 1 回だけ確かめる。** あれば遷移条件の判定を判定 AI（judge）に任せ、
自分で YES/NO を考えない（③ で使う）。無ければ従来どおり自分で評価する——定義は同じでよい。

```bash
command -v agent-herd >/dev/null 2>&1 && agent-herd config --check judge >/dev/null 2>&1 \
  && echo "JUDGE=yes" || echo "JUDGE=no"
```

> `agent-herd harness statemachine --workflow …` が使える環境では、この手順をすべて
> ハーネスに任せる方が確実（ハーネスは同じ判定 AI を自分で呼ぶ）。会話の中で 1 ステートずつ
> 回すときだけ、以下の手順に従う。

### Step 1〜N: ステートループ（terminal まで繰り返す）

**現在のステートに入ったことを宣言する（毎ステート必須）:**

```
## [現在のステート: {state_id}]
```

**① アクションを実行する（LLM）**

現在のステートのアクションプロンプトを実行し、出力を `last_output` として記録する。

> **重要**: アクション実行前に条件を確認してはならない。出力が確定してから条件リストを取得する。

`judge:` を宣言したステート（判定だけのステート）は、アクションの代わりに次を行う:

```bash
python .github/skills/statemachine-use/scripts/next_state.py {名前} --state {現在のstate_id} --state-judge
```

- `judge` が `null` なら通常のステート。上のとおりアクションを実行する。
- JUDGE=yes なら、`question` を `agent-herd judge` に渡す（stdin は `input` の展開文）。答えの `choice` が
  `last_output`（`other` なら `unsure` の語）。**自分では選ばない。**
- JUDGE=no なら、`fallback_action` をそのまま実行し、選択肢のキーを 1 語だけ答える。

**② 条件を自動評価する（Python）**

状態値は `--context` の JSON オブジェクトで渡す（`last_output` と各 `output_key`）。

```bash
python .github/skills/statemachine-use/scripts/next_state.py {名前} \
  --state {現在のstate_id} --auto-eval \
  --context '{"last_output":"{last_outputの第1行}"}'
```

遷移先がここで確定する応答は 2 形。どちらも ③④ を飛ばして ⑤ へ進む:

- `auto_advance: true` — 無条件トランジション。`conditions` は返らず `next_state` が遷移先。
- `resolved` が `null` 以外 — `condition_rule` だけで確定。

> `auto_advance` が省くのは**条件評価だけ**。① のアクション実行と `output_validator` による成功確認は省略しない。
> アクションが失敗したステートから遷移してはならない。

`resolved` が `null` の場合のみ `needs_llm_eval: true` の条件を LLM で評価する。

> 旧ハーネス互換として `--list-conditions` / `--last-output` / `--output KEY=VALUE` も受け付ける。
> 旧引数は `--context` に無いキーの補完としてのみ効く。新規の呼び出しでは使わない。

**③ 残った条件を評価する（JUDGE=yes なら判定 AI、no なら LLM）**

*JUDGE=yes* — ② の応答にある `judge_questions` をそのまま `agent-herd judge` に渡す。
状態（アクションの出力全文）は stdin。自分では評価しない:

```bash
printf '%s' "{last_output 全文}" | agent-herd judge --questions '{②の judge_questions}' > .statemachine/{名前}/judge.json
```

*JUDGE=no* — `needs_llm_eval: true` の条件のみ `last_output` に対して YES / NO で評価し、JSON を構築する:
```json
{"1": false}
```
（`needs_llm_eval: false` の条件インデックスは省略可。`--eval` 渡し時に自動上書きされる）

**④ 遷移先を確定する（Python）**

```bash
# JUDGE=yes: judge の答えをそのまま渡す（choice も boolean もスクリプトが読む）
python .github/skills/statemachine-use/scripts/next_state.py {名前} \
  --state {現在のstate_id} --judge-answers "$(cat .statemachine/{名前}/judge.json)" \
  --context '{"last_output":"{last_outputの第1行}"}'

# JUDGE=no: 自分で評価した JSON を渡す
python .github/skills/statemachine-use/scripts/next_state.py {名前} \
  --state {現在のstate_id} --eval '{"1": false}' \
  --context '{"last_output":"{last_outputの第1行}"}'
```

出力: 次の `state_id`、`NONE`（一致なし）、`TERMINAL`（終端）

> `condition_rule` がある条件は `--context` から自動評価され、`--eval` / `--judge-answers` の値を上書きする。
> `--judge-answers` が終了コード 3 で止まったら、judge が確度不足で決めていない。その条件だけ
> 自分で評価して `--eval` で渡し直す。

**⑤ 完了を記録する**

```
## [ステート {state_id} 完了]
- 出力: {last_outputの第1行}
- 遷移先: {次のstate_id または TERMINAL}
```

**⑥ 遷移 or 終了**

- 次の `state_id` → そのステートへ移動して Step 1 に戻る
- `TERMINAL` → 実行完了、最終出力を表示
- `NONE` → `on_no_transition` 設定に従う（デフォルト: エラー）
