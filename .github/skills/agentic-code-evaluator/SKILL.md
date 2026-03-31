---
name: agentic-code-evaluator
description: "エージェント出力の自己評価・改善ループを実装するスキル。自己批評・リフレクションループ、エバリュエーター・オプティマイザーパイプライン、ルーブリック評価・LLM-as-judge 評価システムの設計などに使用する。「エージェント評価して」「出力を自己評価して」「品質ループ実装して」「反復改善して」などで発動する。"
metadata:
  version: 1.0.0
  tier: stable
  category: evaluation
  tags:
    - agentic
    - evaluation
    - reflection
    - code-quality
    - llm-as-judge
---

# agentic-code-evaluator

エージェントが自身の出力を評価・改善する反復ループのパターンと実装ガイド。

## 概要

単発生成から品質保証付きの反復改善サイクルへ移行するための評価パターン集。

```
生成 → 評価 → 批評 → 改善 → 出力
 ↑                              │
 └──────────────────────────────┘
```

## 使用場面

- **品質重視の生成**: 高精度が求められるコード・レポート・分析
- **明確な評価基準があるタスク**: 定義済みの成功指標が存在する場合
- **特定の基準を満たすコンテンツ**: スタイルガイド・コンプライアンス・フォーマット要件がある場合

---

## パターン 1: 基本リフレクション

エージェントが自己批評を通じて出力を評価・改善する。

```python
def reflect_and_refine(task: str, criteria: list[str], max_iterations: int = 3) -> str:
    """リフレクションループ付き生成。"""
    output = llm(f"このタスクを完了してください:\n{task}")

    pending_criteria = criteria  # 未クリアの基準のみ再評価してコストを削減
    for _ in range(max_iterations):
        # 自己批評（未クリア基準のみ評価）
        critique = llm(f"""
        以下の基準に対してこの出力を評価してください: {pending_criteria}
        出力: {output}
        各基準を PASS/FAIL とフィードバックで JSON 形式で評価してください。
        """)

        critique_data = json.loads(critique)
        all_pass = all(c["status"] == "PASS" for c in critique_data.values())
        if all_pass:
            return output

        # 次のイテレーションでは失敗した基準のみ再評価する
        pending_criteria = [k for k, v in critique_data.items() if v["status"] == "FAIL"]
        failed = {k: critique_data[k]["feedback"] for k in pending_criteria}
        output = llm(f"以下の問題を修正してください: {failed}\n元の出力: {output}")

    return output
```

**重要なポイント**: 批評結果の確実なパースのために構造化 JSON 出力を使用する。

---

## パターン 2: エバリュエーター・オプティマイザー

生成と評価の責務を分離し、明確な役割分担を実現する。

```python
class EvaluatorOptimizer:
    def __init__(self, score_threshold: float = 0.8):
        self.score_threshold = score_threshold

    def generate(self, task: str) -> str:
        return llm(f"完了してください: {task}")

    def evaluate(self, output: str, task: str) -> dict:
        return json.loads(llm(f"""
        タスクに対する出力を評価してください: {task}
        出力: {output}
        JSON で返してください: {{"overall_score": 0-1, "dimensions": {{"accuracy": ..., "clarity": ...}}}}
        """))

    def optimize(self, output: str, feedback: dict) -> str:
        return llm(f"フィードバックに基づいて改善してください: {feedback}\n出力: {output}")

    def run(self, task: str, max_iterations: int = 3) -> str:
        output = self.generate(task)
        history = []
        prev_score = 0.0
        for _ in range(max_iterations):
            evaluation = self.evaluate(output, task)
            score = evaluation["overall_score"]
            history.append({"score": score, "output": output})
            if score >= self.score_threshold:
                break
            if score <= prev_score:  # スコアが改善しない場合は収束と判定して早期終了
                break
            prev_score = score
            output = self.optimize(output, evaluation)
        return output
```

---

## パターン 3: コード特化リフレクション

コード生成のためのテスト駆動反復改善ループ。

```python
class CodeReflector:
    def reflect_and_fix(self, spec: str, max_iterations: int = 3) -> str:
        code = llm(f"以下の仕様の Python コードを書いてください: {spec}")
        tests = llm(f"以下の仕様の pytest テストを生成してください: {spec}\nコード: {code}")

        for _ in range(max_iterations):
            result = run_tests(code, tests)
            if result["success"]:
                return code
            code = llm(f"エラーを修正してください: {result['error']}\nコード: {code}")
        return code
```

---

## 評価ストラテジー

### 結果ベース評価

出力が期待する結果を達成しているか評価する。

```python
def evaluate_outcome(task: str, output: str, expected: str) -> str:
    return llm(
        f"出力は期待する結果を達成していますか? "
        f"タスク: {task}, 期待値: {expected}, 出力: {output}"
    )
```

### LLM-as-Judge

LLM を使って出力を比較・ランク付けする。

```python
def llm_judge(output_a: str, output_b: str, criteria: str) -> str:
    return llm(
        f"{criteria} の観点で出力 A と B を比較してください。"
        f"どちらが優れており、その理由は何ですか?"
    )
```

### ルーブリックベース評価

重み付きディメンションに基づいてスコアリングする。

```python
RUBRIC = {
    "accuracy":     {"weight": 0.4},  # 正確性
    "clarity":      {"weight": 0.3},  # 明確性
    "completeness": {"weight": 0.3},  # 完全性
}

def evaluate_with_rubric(output: str, rubric: dict) -> float:
    scores = json.loads(
        llm(f"各ディメンションを 1-5 で評価してください: {list(rubric.keys())}\n出力: {output}")
    )
    # 各スコアを最大値 5 で割って 0〜1 に正規化し、重み付き合計を返す
    return sum(scores[d] * rubric[d]["weight"] for d in rubric) / 5
```

---

## ベストプラクティス

| プラクティス | 理由 |
|-------------|------|
| **明確な基準を定義する** | 具体的・測定可能な評価基準を事前に定義することで評価の一貫性が上がる |
| **反復回数を制限する** | 無限ループを防ぐため最大回数（3〜5回）を設定する |
| **収束チェックを実施する** | イテレーション間でスコアが改善しない場合は早期終了する |
| **履歴をログに残す** | デバッグと分析のために全反復のトレースを保持する |
| **構造化出力を使用する** | 評価結果の確実なパースのために JSON 形式を採用する |

---

## 実装チェックリスト

```markdown
## 評価実装チェックリスト

### セットアップ
- [ ] 評価基準・ルーブリックを定義する
- [ ] 「十分良い」スコア閾値を設定する
- [ ] 最大反復回数を設定する（デフォルト: 3）

### 実装
- [ ] generate() 関数を実装する
- [ ] 構造化出力付き evaluate() 関数を実装する
- [ ] optimize() 関数を実装する
- [ ] 改善ループを接続する

### 安全性
- [ ] 収束検出を追加する
- [ ] デバッグ用に全反復をログに記録する
- [ ] 評価パース失敗を適切にハンドリングする
```

---

## 実行フロー

```
Step 1: タスクと評価基準を確認する
  └─ ユーザーのタスクを分析し、適切な評価パターンを選択する

Step 2: 初期出力を生成する
  └─ タスクに基づいて最初の出力を生成する

Step 3: 評価・批評を実施する
  └─ 定義した基準・ルーブリックに照らして出力を評価する
  └─ 失敗した基準・改善点を特定する

Step 4: 改善を実行する（基準未達の場合）
  └─ 批評フィードバックに基づいて出力を改善する
  └─ 最大反復回数に達するまで Step 3 に戻る

Step 5: 最終出力を報告する
  └─ 最終スコア・評価サマリーとともに出力を提示する
  └─ 改善の軌跡（反復ログ）を必要に応じて提示する
```
