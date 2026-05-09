# statemachine-use ハーネス堅牢化設計

TAKT設計思想（scrum-master v1.5.0 で導入、PR #122）をstatemachine-useのインライン実行ハーネスに適用するための改善設計書。

---

## 1. 背景と目的

### 1.1 ハーネスとは

statemachine-useの「ハーネス」は、エージェントLLMがSKILL.mdの実行モードに従って自律的にステートマシンをステップ実行する仕組み全体を指す。具体的には以下の要素から構成される：

- **実行プロトコル**（SKILL.md 実行モード）: LLMが守るべき実行手順の指示
- **Pythonスクリプト**（`next_state.py`, `run_machine.py`）: 遷移決定の決定論的処理
- **エンジン**（`engine.py`）: 完全自動実行モードのコアループ
- **アクションプロンプト設計**（`actions/*.md`）: 各ステートのLLM指示

### 1.2 TAKTとは

TAKT（PR #122で導入）はscrum-masterオーケストレーターに適用された品質強化設計思想で、以下の4原則からなる：

| 原則 | 内容 |
|---|---|
| **ファセットプロンプティング** | プロンプトをPersona/Policy/Instructions/Knowledge/Output Contractの5ファセットに分解 |
| **多角レビュー** | 単一視点ではなく複数の独立した観点から並列評価 |
| **AIアンチパターン検出** | AI生成コードに特有の問題（幻覚API、スコープクリープ等）を明示的に検出 |
| **ルールベーストランジション制御** | フェーズ遷移条件を「機械的に検証可能なルール」として定義し、AI判断への依存を排除 |

### 1.3 目的

現状のハーネスには「LLMの逸脱行動」「条件評価の不安定性」「プロンプト品質のばらつき」という構造的な脆弱性がある。TAKTの原則を適用してこれらを体系的に解消し、エージェントが長いワークフローを確実に最後まで実行できるハーネスを実現する。

---

## 2. 現状課題の分析

### 課題A: LLM読み飛ばし・実行順序逸脱

**問題**: SKILL.mdは「アクション実行前に条件を確認してはならない」と記述しているが、これは単なる自然言語の指示であり、LLMが遵守する保証がない。特に長いワークフローで文脈が長くなると、LLMがステップを省略したり、次のステートを先読みして現在のアクションを変質させるリスクがある。

**症状例**:
- 条件リストを取得する前にアクション出力の中で分岐判断をしてしまう
- `## [現在のステート: {state_id}]` 宣言を省略してステートの境界が不明確になる
- 複数ステートを1ターンで「まとめて」実行しようとする

### 課題B: 条件評価のLLM依存による不安定性

**問題**: `engine.py`の`_evaluate_condition`は、自然言語で書かれた条件をLLMにYES/NOで評価させる。これにより以下の問題が発生する：

```python
# 現状の実装（engine.py:436-454）
prompt = f"""... 条件: {condition} ... YES または NO の一語のみで回答してください。"""
response = (await self.llm_fn(prompt)).strip().upper()
return response.startswith("YES")
```

- あいまいな条件（「analysis_resultが概ね良好」など）でLLMが誤評価する
- コンテキストが長くなると条件と無関係な判断をする可能性がある
- `startswith("YES")`は脆弱で、"YES, however..."のような回答を真と判定する

**同様に**: `next_state.py`の`--list-conditions`でLLMが評価する条件テキストも、設計者の書き方次第で評価精度が大きく変わる。

### 課題C: アクションプロンプトの非体系化

**問題**: 現状のactions/*.mdは設計者が自由に書くため、品質にばらつきがある。特に「出力形式の指定」が末尾に書かれているだけで、LLMが重要度を低く評価しやすい。

```markdown
<!-- 現状パターン: 形式がフリーフォームで出力仕様が弱い -->
## [analyze: コードを分析する]
以下のコードを分析してください。
**対象:** {{input}}
確認項目: バグ、パフォーマンス...

**出力形式:** PASS / FAIL の一語
```

Output Contractが不明確だと、LLMが「PASS: 問題なし」や「評価: PASS」のような形式で出力し、`startswith("PASS")`の評価が失敗する。

### 課題D: max_retriesが未実装

**問題**: `schema.md`にはステートの`max_retries`フィールドが定義されているが、`engine.py`の`StateConfig`にフィールドはあるものの`_execute_state`内でリトライロジックが実装されていない。

```python
# engine.py:379-404: max_retriesを参照していない
async def _execute_state(self, state: StateConfig, ctx: dict, verbose: bool) -> str:
    # stateのmax_retriesを無視して1回実行するだけ
    output = await self.llm_fn(prompt)
    return output.strip()
```

出力形式違反があっても無条件で次の処理に進んでしまう。

### 課題E: ゲート条件の曖昧さ

**問題**: SKILL.mdの実行モードには「`NONE` → `on_no_transition` 設定に従う」としか書かれておらず、遷移が失敗した場合の対応手順が不明確。また、ワークフロー設計者がゲート条件（terminal遷移前の必須チェック）を設計する指針がない。

---

## 3. TAKTから取り入れる原則と適用方針

| TAKT原則 | 課題 | statemachine-useへの適用 |
|---|---|---|
| ルールベーストランジション制御 | B, E | `condition_rule`フィールドで決定論的評価を追加 |
| ファセットプロンプティング | C | 5ファセット標準テンプレートをpatterns.mdに追加 |
| AIアンチパターン検出 | A | ハーネス実行プロトコルにアンチパターン抑止を明示 |
| 多角レビュー | D | 出力バリデーション + max_retriesの実装 |

---

## 4. 具体的な改善設計

### 改善1: `condition_rule`フィールドの導入（ルールベーストランジション制御）

**対象ファイル**: `references/schema.md`, `scripts/engine.py`, `scripts/next_state.py`

#### 4.1.1 スキーマ拡張

トランジション定義に`condition_rule`フィールドを追加する。このフィールドが存在する場合、LLM評価の前に決定論的評価を優先する。

```yaml
transitions:
  - from: analyze
    to: approve
    condition: "analysis_result が PASS で始まる"       # 後方互換: LLMフォールバック
    condition_rule: "startswith:analysis_result:PASS"  # 決定論的評価（優先）
    priority: 1
  - from: analyze
    to: request_revision
    condition_rule: "not-startswith:analysis_result:PASS"  # 否定ルール
    priority: 2
```

#### 4.1.2 `condition_rule`書式仕様

```
書式: {演算子}:{キー}:{値}

演算子:
  startswith:KEY:VALUE   → context[KEY].startswith(VALUE)
  contains:KEY:VALUE     → VALUE in context[KEY]
  equals:KEY:VALUE       → context[KEY] == VALUE
  regex:KEY:PATTERN      → bool(re.search(PATTERN, context[KEY]))
  lt:KEY:NUMBER          → float(context[KEY]) < float(NUMBER)
  gte:KEY:NUMBER         → float(context[KEY]) >= float(NUMBER)
  not-startswith:KEY:V   → not context[KEY].startswith(VALUE)
  not-contains:KEY:V     → VALUE not in context[KEY]

複合条件（AND）:
  condition_rules:
    - "startswith:last_output:PASS"
    - "lt:retry_count:3"
```

#### 4.1.3 engine.py への実装追加

```python
def evaluate_condition_rule(rule: str | None, ctx: dict) -> bool | None:
    """
    condition_rule を決定論的に評価する。
    rule が None または解析不能な場合は None を返し、LLM評価にフォールバックする。
    """
    if not rule:
        return None
    
    parts = rule.split(":", 2)
    if len(parts) < 3:
        return None
    
    op, key, value = parts[0], parts[1], parts[2]
    ctx_value = str(ctx.get(key, ""))
    
    try:
        match op:
            case "startswith":       return ctx_value.startswith(value)
            case "contains":         return value in ctx_value
            case "equals":           return ctx_value == value
            case "regex":            return bool(re.search(value, ctx_value))
            case "lt":               return float(ctx_value) < float(value)
            case "gte":              return float(ctx_value) >= float(value)
            case "not-startswith":   return not ctx_value.startswith(value)
            case "not-contains":     return value not in ctx_value
            case _:                  return None
    except (ValueError, re.error):
        return None
```

`_evaluate_transitions`での利用：

```python
async def _evaluate_transitions(self, ...):
    for transition in candidates:
        # 1. condition_rule で決定論的評価を試みる
        rule_result = evaluate_condition_rule(
            getattr(transition, 'condition_rule', None), ctx
        )
        if rule_result is not None:
            matches = rule_result
        else:
            # 2. LLMフォールバック
            condition = render_template(transition.condition, ctx)
            matches = await self._evaluate_condition(condition, ctx, verbose)
        
        if matches:
            return transition.to_state
    return None
```

**効果**: 決定論的に評価可能なトランジションではLLM呼び出しを排除し、評価精度と実行速度を向上させる。

---

### 改善2: ファセットプロンプティング標準テンプレート（patterns.md追記）

**対象ファイル**: `references/patterns.md`

#### 4.2.1 5ファセット構造の定義

```markdown
## ファセットプロンプティング（アクション品質標準化）

TAKTファセット設計思想に基づき、actionプロンプトを5つの独立したファセットに分解する。
各ファセットは独立して読まれても意味が通じるように書く（LLMは全体を読まないことがある）。

**ファセット1: Persona（役割）**
このステートで LLM が担う役割を宣言する。役割を明確にすることで判断基準が安定する。

**ファセット2: Policy（品質規約）**
守るべき制約・禁止事項・品質基準を箇条書きで列挙する。特に Output Contract 違反の禁止を明記する。

**ファセット3: Instructions（手順）**
番号付きで実行手順を記述する。「現在のステートの作業のみ」に限定し、次のステートへの言及を排除する。

**ファセット4: Knowledge（参照コンテキスト）**
このステートの判断に必要なコンテキスト変数を明示する。不要な変数は含めない。

**ファセット5: Output Contract（出力契約）**
出力形式を「契約」として定義する。第1行の形式は特に厳格に指定する。
```

#### 4.2.2 標準テンプレート

```markdown
## [state_id: ステートの目的を一文で]

**Persona（役割）:** あなたは[役割]として振る舞ってください。

**Policy（品質規約）:**
- 出力の第1行は必ず[KEYWORD_A] または [KEYWORD_B] の一語のみにすること
- 現在のステートの作業のみを行い、次のステップを先読みしないこと
- スコープ外の変更・追加機能は行わないこと

**Instructions（手順）:**
1. [具体的な作業ステップ1]
2. [具体的な作業ステップ2]
3. 完了後、Output Contractの形式で出力する

**Knowledge（参照コンテキスト）:**
- 入力: {{input}}
- 直前のステート出力: {{last_output}}
- [必要な変数]: {{variable_name}}

**Output Contract（出力契約）:**

```
第1行: [KEYWORD_A] または [KEYWORD_B] の一語のみ（説明・修飾語を付けてはならない）
第2行以降: [根拠・詳細の形式説明]
```

**⚠️ 出力前の自己チェック:**
- [ ] 第1行が[KEYWORD_A]/[KEYWORD_B]の一語になっているか
- [ ] 現在のステート以外の判断・作業を含んでいないか
- [ ] コンテキスト変数を正しく参照したか

この指示に従ってタスクを実行してください。
完了後、Output Contractの形式のみで出力してください。次のステップは別途指示されます。
```

**効果**: Output Contractの明確化により、条件評価の`startswith()`判定が安定する。

---

### 改善3: ハーネス実行プロトコルの強化（AIアンチパターン抑止）

**対象ファイル**: `SKILL.md`（実行モード）

#### 4.3.1 実行モードへのアンチパターン抑止の追加

SKILL.mdの実行モード冒頭に以下のブロックを追加する：

```markdown
### ⛔ ハーネス実行プロトコル — 禁止行動（違反時は即座に停止して再確認）

| 禁止行動 | なぜ問題か | 代替行動 |
|---|---|---|
| アクション実行前に条件リストを取得する | 条件を知ることでアクション出力が汚染される | ①アクション実行 → ②出力確定 → ③条件取得の順を守る |
| 現在のステート以外の作業を実行する | ステートの境界が破壊されてワークフローが不定状態になる | 現在のステートの作業のみ実行する |
| `## [現在のステート: {state_id}]` 宣言を省略する | どのステートを実行中か追跡できなくなる | 毎ステートの冒頭で必ず宣言する |
| 条件を評価せずに遷移先を独断で決める | next_state.pyによる決定論的制御を迂回する | 必ず④のPythonスクリプトで遷移先を確定する |
| 複数ステートをまとめて実行する | 中間出力が失われ、条件評価の入力が不正になる | 1ステート = 1ターンを厳守する |
```

#### 4.3.2 各ステップへの明示的な完了確認の追加

現在のStep 1〜Nのループに「完了確認」ステップを追加：

```markdown
**⑤ 状態記録（必須）**

次のステートへ移動する前に、現在のステートの結果を記録する：

```
## [ステート {state_id} 完了]
- last_output: {出力の第1行のみ}
- 遷移先: {next_state_id または TERMINAL}
- ステップ数: {N}
```

この記録がない場合、ハーネスが中断したとみなす。
```

---

### 改善4: `max_retries`のエンジン実装（多角検証）

**対象ファイル**: `scripts/engine.py`

#### 4.4.1 出力バリデーション機能の追加

StateConfigに`output_validator`を追加し、出力形式を機械的に検証する：

```yaml
states:
  analyze:
    action_file: actions/analyze.md
    output_key: analysis_result
    max_retries: 2
    output_validator: "startswith:PASS,FAIL,MINOR,MAJOR,CRITICAL"  # 第1行がこれらで始まること
```

#### 4.4.2 engine.pyのリトライループ実装

```python
async def _execute_state(self, state: StateConfig, ctx: dict, verbose: bool) -> str:
    parts = []
    if state.on_enter:
        parts.append(render_template(state.on_enter, ctx))
    if state.action:
        parts.append(render_template(state.action, ctx))
    
    if not parts:
        return ""
    
    base_prompt = "\n\n".join(parts)
    max_attempts = state.max_retries + 1
    
    for attempt in range(max_attempts):
        prompt = base_prompt
        if attempt > 0:
            # リトライ時は出力形式違反を明示したプロンプトを追加
            prompt += f"\n\n⚠️ リトライ {attempt}/{state.max_retries}: 前回の出力が Output Contract に違反しました。形式を必ず守って再実行してください。"
        
        output = (await self.llm_fn(prompt)).strip()
        self._log(verbose, f"  アクション出力 (attempt {attempt+1}): {output[:200]}")
        
        # output_validatorがある場合は検証
        if hasattr(state, 'output_validator') and state.output_validator:
            if self._validate_output(output, state.output_validator):
                break
            elif attempt < state.max_retries:
                self._log(verbose, f"  出力バリデーション失敗、リトライします ({attempt+1}/{max_attempts})")
                continue
            else:
                self._log(verbose, f"  出力バリデーション失敗、最大リトライ到達")
                # 最終的に失敗した場合も出力を返す（エラー処理は遷移評価に委ねる）
        break
    
    # on_exit処理...
    return output


def _validate_output(self, output: str, validator: str) -> bool:
    """
    output_validator のルールに従って出力を検証する。
    書式: "startswith:VAL1,VAL2,VAL3" — 出力の第1行がいずれかで始まること
    """
    if validator.startswith("startswith:"):
        first_line = output.split("\n")[0].strip()
        allowed = validator[len("startswith:"):].split(",")
        return any(first_line.startswith(v.strip()) for v in allowed)
    return True  # 未知のvalidatorは常にTrue
```

---

### 改善5: ゲート条件チェックリスト設計パターン（patterns.md追記）

**対象ファイル**: `references/patterns.md`

#### 4.5.1 ゲート条件テーブルパターン

TAKT原則「ルールベーストランジション制御」をワークフロー設計者向けに提供する：

```markdown
## ゲート条件テーブルパターン

> **TAKT設計思想 — ルールベーストランジション制御**: 遷移の判断はAIの自由意思ではなく、
> 明示的なルールに基づいて行う。各条件は機械的に検証可能（出力のstartswith・contextの値）
> であり、曖昧な判断を排除する。

長いワークフローの終盤や副作用の大きい操作の前後に挿入するパターン。
通常のゲートステートの強化版で、遷移条件を「テーブル」で明示する。

### workflow.yaml

```yaml
states:
  gate_final:
    description: "最終ゲート: 全条件をAND評価して遷移を決定する"
    action_file: actions/gate_final.md
    output_key: gate_result

transitions:
  - from: gate_final
    to: complete
    condition_rule: "startswith:gate_result:GATE_PASS"   # 決定論的評価
    priority: 1
  - from: gate_final
    to: error_handler
    condition_rule: "not-startswith:gate_result:GATE_PASS"
    priority: 2
```

### `actions/gate_final.md` テンプレート

```markdown
## [gate_final: 全条件をテーブルで検証する]

**Persona:** あなたは厳格なゲートキーパーとして、全条件を客観的に検証します。

**Policy:**
- 各条件を独立して評価すること（前の条件の結果で他の評価を変えない）
- 1つでも条件が不適合なら GATE_FAIL を出力すること
- 条件番号を省略しないこと

**Instructions:**
1. 以下のゲート条件テーブルを上から順に評価する
2. 各条件に「適合 ✅ / 不適合 ❌」と理由を記入する
3. 全条件の評価が完了したら集約判定を出力する

**Knowledge:**
{{前フェーズの出力キー変数}}

**ゲート条件テーブル（全条件をANDで評価）:**

| # | 条件 | 検証方法 |
|---|------|---------|
| 1 | [条件1の説明] | [何を見て確認するか] |
| 2 | [条件2の説明] | [何を見て確認するか] |
| 3 | [条件3の説明] | [何を見て確認するか] |

**Output Contract:**

```
第1行: GATE_PASS または GATE_FAIL の一語のみ
第2行以降:
| # | 条件 | 結果 | 理由 |
|---|------|------|------|
| 1 | ... | ✅/❌ | ... |
不適合条件がある場合: 不適合 N 件: [条件番号リスト]
```

**⚠️ 出力前の自己チェック:**
- [ ] 全条件（N件）を評価したか
- [ ] 1件でも❌があればGATE_FAILを出力したか
- [ ] 第1行がGATE_PASS/GATE_FAILの一語になっているか

この指示に従ってタスクを実行してください。
完了後、Output Contractの形式のみで出力してください。次のステップは別途指示されます。
```
```

---

## 5. 実装ロードマップ

### Phase 1: ドキュメント改善（即時実施可能）

| # | 作業 | 対象ファイル | 効果 |
|---|---|---|---|
| 1-A | ファセットプロンプティングテンプレートを追記 | `references/patterns.md` | アクション品質の標準化 |
| 1-B | ゲート条件テーブルパターンを追記 | `references/patterns.md` | ゲート設計の明示化 |
| 1-C | 実行モードにアンチパターン抑止ブロックを追加 | `SKILL.md` | LLM逸脱の抑制 |
| 1-D | 実行モードに⑤状態記録ステップを追加 | `SKILL.md` | 実行追跡の強化 |

### Phase 2: スキーマ・スクリプト拡張（低リスク）

| # | 作業 | 対象ファイル | 効果 |
|---|---|---|---|
| 2-A | `condition_rule`フィールドをスキーマに追加 | `references/schema.md` | ルールベース評価の文書化 |
| 2-B | `output_validator`フィールドをスキーマに追加 | `references/schema.md` | 出力バリデーションの文書化 |
| 2-C | `evaluate_condition_rule()`を実装 | `scripts/engine.py` | 条件評価の決定論的化 |
| 2-D | `next_state.py`の`--evals`にルール評価を追加 | `scripts/next_state.py` | インライン実行のルールベース化 |

### Phase 3: エンジン機能実装（中リスク、既存テストが必要）

| # | 作業 | 対象ファイル | 効果 |
|---|---|---|---|
| 3-A | `max_retries`リトライループを実装 | `scripts/engine.py` | 出力形式違反の自動回復 |
| 3-B | `output_validator`検証ロジックを実装 | `scripts/engine.py` | 形式違反の検出 |
| 3-C | 実行ログへの詳細なリトライ履歴を追加 | `scripts/engine.py` | デバッグ性の向上 |

### Phase 4: サンプルワークフローの更新

| # | 作業 | 対象ファイル | 効果 |
|---|---|---|---|
| 4-A | `examples/issue_triage.yaml`に`condition_rule`を追加 | `examples/` | 実装例の提供 |
| 4-B | TAKTテンプレートを使ったexampleを追加 | `examples/` | ベストプラクティスの例示 |

---

## 6. 影響範囲と後方互換性

### 後方互換性の保証

- `condition_rule`は**任意フィールド**として追加する。既存のYAMLはそのまま動作する
- `output_validator`は**任意フィールド**として追加する。既存のYAMLはそのまま動作する
- `condition_rule`がない場合は既存のLLM評価にフォールバックする
- `max_retries: 0`（デフォルト）の場合は現状と同じ1回実行のみ

### 破壊的変更なし

Phase 1〜2はすべてオプション追加であり、既存のワークフローYAMLや実行動作に影響を与えない。

### 既存ワークフローへの段階的移行

既存ワークフローを改善する場合の優先手順：
1. 最も不安定なトランジションに`condition_rule`を追加する（最も高コスパ）
2. アクション出力が不安定なステートに`output_validator`と`max_retries: 1`を追加する
3. 複雑なゲートステートをゲート条件テーブルパターンにリファクタリングする

---

## 7. 参考

- PR #122: [Introduce TAKT design principles](https://github.com/ynitto/sandbox/pull/122) — scrum-masterへのTAKT適用
- `references/patterns.md` — 制御フローパターン集（本ドキュメントで拡張）
- `references/schema.md` — YAMLスキーマ仕様（Phase 2で拡張）
- `scripts/engine.py` — コアエンジン（Phase 2-3で拡張）
