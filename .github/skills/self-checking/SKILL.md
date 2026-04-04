---
name: self-checking
description: "エージェントが自身の成果物（コード・調査・ドキュメント）を自己評価・改善する反復ループを実行するスキル。定義済みルーブリックと自動チェックスクリプトを組み合わせて品質を定量評価し、基準未達の場合は改善を繰り返す。「自己評価して」「出力を確認して」「品質チェックして」「成果物をレビューして」「改善ループ回して」などで発動する。オーケストレーター（scrum-master・skill-mentor）から自動起動される場合もある。"
metadata:
  version: 1.0.0
  tier: stable
  category: evaluation
  tags:
    - self-checking
    - evaluation
    - reflection
    - quality
    - code
    - research
    - document
---

# self-checking

エージェントが自身の成果物を評価・改善する反復ループスキル。コード・調査レポート・ドキュメントに対応する。

```
生成・実行 → 自己評価 → 改善 → 再評価 → 合格
     ↑                                      │
     └──────── スコア未達の場合のみ ─────────┘
```

## パス解決

このSKILL.mdが置かれているディレクトリを `SKILL_DIR` とする。

- スクリプト: `${SKILL_DIR}/scripts/check.py`
- ルーブリック: `${SKILL_DIR}/references/rubrics/`

---

## 対応する成果物の種別

| artifact_type | 対象 | ルーブリック |
|---|---|---|
| `code` | ソースコード・テストコード・設定ファイル | `${SKILL_DIR}/references/rubrics/code.md` |
| `research` | 調査レポート・技術調査・競合分析 | `${SKILL_DIR}/references/rubrics/research.md` |
| `document` | 設計書・要件定義書・README・仕様書 | `${SKILL_DIR}/references/rubrics/document.md` |

artifact_type が不明な場合は `check.py --detect [file]` で自動検出する。

---

## 実行プロトコル

### Step 1: 成果物と種別の確認

1. 評価対象の成果物と artifact_type を確認する
2. artifact_type が未指定の場合:
   ```bash
   python ${SKILL_DIR}/scripts/check.py --detect [対象ファイルパス]
   ```
   出力例: `{"artifact_type": "code", "confidence": 0.95}`
3. 対応するルーブリックファイルを読み込む:
   ```bash
   # 例: code の場合
   # ${SKILL_DIR}/references/rubrics/code.md を読む
   ```
4. 完了基準（done_criteria）が渡されている場合は評価基準に追加する

---

### Step 2: 自動チェックの実行

スクリプトで定量チェックを実施する:

```bash
python ${SKILL_DIR}/scripts/check.py \
  --type [artifact_type] \
  --files [対象ファイルパス（スペース区切り、複数可）] \
  --criteria "[完了基準（任意）]"
```

出力例（JSON）:
```json
{
  "artifact_type": "code",
  "checks": {
    "syntax": {"passed": true, "details": ""},
    "completeness": {"passed": true, "details": ""},
    "test_presence": {"passed": false, "details": "テストファイルが見当たらない"}
  },
  "auto_score": 0.67,
  "failed_checks": ["test_presence"]
}
```

スクリプト失敗時（ファイルなし・パースエラー等）は自動チェックをスキップし、ルーブリック評価のみを実施する。

---

### Step 3: ルーブリック評価

読み込んだルーブリックに従って各ディメンションを評価する。

**評価スケール**: 各ディメンションを 1〜5 で採点し、重み付き合計スコアを算出する。

```
スコア = Σ (採点 / 5 × 重み)
```

**評価結果を以下の形式で記録する**:

```json
{
  "dimensions": {
    "[ディメンション名]": {
      "score": 4,
      "weight": 0.3,
      "verdict": "PASS",
      "feedback": "[評価コメント]"
    }
  },
  "rubric_score": 0.82,
  "failed_dimensions": ["[基準未達のディメンション名]"]
}
```

**判定閾値**:
- `rubric_score >= 0.8` かつ `auto_score >= 0.7`（自動チェックあり時）→ **PASS**
- それ以外 → **NEEDS_IMPROVEMENT**（改善ループへ）

---

### Step 4: 改善ループ（NEEDS_IMPROVEMENT の場合のみ）

基準未達のディメンション・チェック項目のみを対象に改善する（最大 3 回）。

**収束検出**: 前回スコアと比較し、改善幅が 0.05 未満の場合は早期終了する。

```
イテレーション 1:
  1. 失敗した項目を特定する: failed_dimensions + failed_checks
  2. 各失敗項目について改善を実施する
  3. 改善内容を記録する: {"dimension": "...", "action": "...", "before": "...", "after": "..."}

イテレーション 2（前回スコア改善あり）:
  1. 再度 Step 2〜3 を実施する
  2. スコアが前回を超えていればループを継続する

最大反復回数（3回）に達した場合:
  → 現時点のベストスコアの成果物で Step 5 へ進む
```

**改善時の注意**:
- 失敗していない項目には手を加えない
- 改善前後のスコアと主な変更点を記録する

---

### Step 5: 結果報告

評価結果を以下の形式でまとめる:

```
## 自己評価結果

| 項目 | 値 |
|------|-----|
| 種別 | [artifact_type] |
| 最終判定 | PASS ✅ / NEEDS_IMPROVEMENT ⚠️ |
| ルーブリックスコア | [0.0〜1.0] |
| 自動チェックスコア | [0.0〜1.0 / N/A] |
| 反復回数 | [0〜3] |

### ディメンション別結果

| ディメンション | スコア | 判定 | コメント |
|---|---|---|---|
| [name] | [N]/5 | PASS ✅ / FAIL ❌ | [feedback] |

### 改善の軌跡

[反復ごとの変更概要]

### 残存する懸念事項（あれば）

[最終反復後も未解決の項目]
```

**verdict-json**（オーケストレーターが読み取る構造化出力）:

```json
<!-- verdict-json -->
{
  "skill": "self-checking",
  "verdict": "PASS | NEEDS_IMPROVEMENT",
  "artifact_type": "[code|research|document]",
  "rubric_score": 0.0,
  "auto_score": 0.0,
  "iterations": 0,
  "improved_files": [],
  "blocking_issues": []
}
<!-- /verdict-json -->
```

---

## ベストプラクティス

| プラクティス | 理由 |
|-------------|------|
| **失敗した基準のみ改善する** | 合格済み項目への不要な変更を防ぎ、回帰リスクを下げる |
| **収束チェックを実施する** | スコアが改善しない場合は早期終了して無駄なループを避ける |
| **反復上限は 3 回に固定する** | 無限ループを防ぎ、外部レビュー（agent-reviewer）への引き渡しを保証する |
| **自動チェックとルーブリックを組み合わせる** | 定量的な客観指標と定性的な評価の両方を担保する |
| **改善履歴をログに残す** | デバッグと振り返り分析のために全反復のトレースを保持する |

---

## エラーハンドリング

| 状況 | 対応 |
|------|------|
| `check.py` が失敗する | ルーブリック評価のみで続行。自動チェックスコアは `N/A` とする |
| ルーブリックファイルが見当たらない | artifact_type に最も近いルーブリックを使用するか、汎用的な評価（正確性・完全性・明確性）で代替する |
| 3 回反復してもスコアが閾値未達 | NEEDS_IMPROVEMENT のまま結果を返す。外部レビュー（agent-reviewer）で追加確認する |
| 成果物ファイルが存在しない | エラーを報告してスキルを終了する |
