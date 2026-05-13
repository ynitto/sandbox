# table-spec-extractor 検証ロードマップ

**作成日**: 2026-05-13  
**統合元**: 改良方針合議・フィードバック設計合議（council-system 二回分）  
**目的**: 「仕様書A → 仕様書B への設定値落とし込み」の検証・自動化を LLM 自律作業と Human in the Loop で段階的に実現する

> 旧ドキュメント（council-decision / feedback-design）はこのファイルに統合済み。参照はこのファイルを使う。

---

## 設計原則

| 役割 | LLM が自律的に担う | Human が判断する |
|---|---|---|
| データ収集・分析 | クエリ実行・サンプリング・パターン検出・レポート生成 | 結果の解釈と原因の確定 |
| 実装 | コード生成・テスト・比較モード実行 | 仕様の承認・ドメイン語彙の定義 |
| フィードバック | LLM 判定レベルの自動ラベリング（セマンティック等価） | 数値・単位チェック、専門家裁定、ambiguous 判断 |
| 改良サイクル | confidence スコア更新・品質ゲート監視・サマリー生成 | 指標の閾値設定・フェーズ移行の最終承認 |

**HITL の原則**: LLM は「提案と根拠」を出す。Human は「承認・修正・却下」を決める。LLM が Human の承認なしに次フェーズへ進むことはない。

---

## 全体フロー

```
Phase 0: 前提確認        ─┐ HITL-0: 作業開始の合意
Phase 1: 診断            ─┤ HITL-1: 診断まとめを確認・主因を確定
Phase 2: フィードバック定義─┤ HITL-2: 判定基準・評価指標・閾値を承認
Phase 3: 比較モード実装   ─┤ HITL-3: 出力フォーマット・製品名辞書を承認
Phase 4: 改良サイクル実証 ─┤ HITL-4a: フィードバック品質の定期確認（毎月）
                          │ HITL-4b: 3ヶ月チェックポイント（中間判定）
Phase 5: 技術実装         ─┤ HITL-5: ベクトル検索の有効性を確認（診断結果次第）
Phase 6: Neo4j 統合       ─┘ HITL-6: 昇格ゲート審査（最終）
```

---

## Phase 0: 前提確認

### LLM が自律的に行う

```bash
# Neo4j バージョン確認
python scripts/run.py init --profile hw-spec
python scripts/run.py init --profile sw-spec

# 登録済みドキュメント一覧
python scripts/run.py search "*" --profile hw-spec --limit 5
python scripts/run.py search "*" --profile sw-spec --limit 5

# data-path の確認
python scripts/run.py config show hw-spec
python scripts/run.py config show sw-spec
```

LLM が出力するもの: 環境サマリー（Neo4j バージョン、DB 名、登録ドキュメント数、data-path）

### 🧑 HITL-0: 作業開始の合意

Human が確認・回答する:

- [ ] 手作業での落とし込みリストは何件あるか（診断サンプル数の基準になる）
- [ ] 検証作業に関わる annotator は何人か（1人 or 複数）
- [ ] 仕様書担当者（ドメイン専門家）にアクセスできるか
- [ ] Neo4j は 5.x 以上か（ベクトル検索の利用可否に影響）

**移行条件**: Human が上記を回答した時点で Phase 1 へ。

---

## Phase 1: 診断

> 「なぜヒット件数が少ないか」の根本原因を実測で特定する。原因を診断しないまま改良策を実装すると、どのアプローチも効果が出ない可能性がある。

### 原因仮説

| 仮説 | 内容 | 確認方法 |
|---|---|---|
| A. 語彙ギャップ | "Maximum Transmission Unit" vs "MTU最大値" のような表記揺れ | 失敗クエリの収集 |
| B. 構造解析エラー | 結合セルの誤分割・PDF OCR 不良でテキストが欠損 | path=null の割合確認 |
| C. スコープ差 | 仕様書Aと仕様書Bで比較できる項目がそもそも少ない | 落とし込みリストとの突合 |

### LLM が自律的に行う

**Step 1-1: 失敗クエリの収集（語彙ギャップの実測）**

```bash
# HITL-0 で受け取った落とし込みリストからキーワードを抽出し、両 DB に投げる
python scripts/run.py search "<キーワード>" --profile hw-spec --json
python scripts/run.py search "<キーワード>" --profile sw-spec --json
```

収集: ヒットするキーワードとしないキーワードを分類し、ヒットしない場合は DB 内にそのテキストが存在するか `--dry-run` で確認。

**Step 1-2: Cell.path 構造のサンプリング**

```cypher
-- 各 DB から 50 件サンプル抽出
MATCH (c:Cell) WHERE c.is_header = false
RETURN c.path, c.text LIMIT 50
```

確認: 階層数の揃い方、命名規則の対応可能性、path=null の割合。

**Step 1-3: 製品名エンティティの揺れ確認**

検索に使っているキーワードと DB に格納されているテキストを照合。正式型番・略称・日英混在を列挙。

**Step 1-4: スコープ差の確認**

落とし込みリストの総項目数と、仕様書Aに対応記述が存在する項目の割合を計算。

**LLM が出力する診断まとめシート**

| 観点 | 結果 | 主因への寄与 |
|---|---|---|
| ヒットしないキーワード例 | （LLM が記入） | 語彙ギャップ: 高/中/低 |
| Cell.path の階層差 | A:○階層 / B:○階層 | 構造差: 高/中/低 |
| path=null の割合 | ○% | 解析エラー: 高/中/低 |
| 製品名の表記揺れ | ○件 | 正規化不足: 高/中/低 |
| スコープ差（比較不可項目） | ○% | スコープ差: 高/中/低 |

### 🧑 HITL-1: 診断まとめを確認・主因を確定

Human が判断する:

- [ ] 診断まとめシートの各行を確認し、LLM の分類（高/中/低）が妥当か
- [ ] **主因を1〜2つに絞り込む**（以降のフェーズの優先順位が変わる）
- [ ] path=null が 20% 以上あれば「B. 構造解析エラー」を優先対処として指示する
- [ ] スコープ差が 50% 以上あれば「C. スコープ差」について仕様書担当者と対話が必要と判断する

**移行条件**:
- [ ] 50 件以上のサンプルで原因を分類できた
- [ ] Human が主因を確定した

---

## Phase 2: フィードバック定義

> フィードバック機能の設計より先に「何が正解か」「いつ改善と言えるか」を決める。格納先の議論はこの後。

### LLM が自律的に行う

HITL-1 の結果（主因）を受けて、以下の**ドラフト**を生成する:

**判定基準ドラフト**

| 段階 | 判定方法 | 適用条件 |
|---|---|---|
| ① 数値・単位の一致 | 機械的チェック（LLM 自動） | 数値を含む全セル |
| ② セマンティック等価 | LLM 判定 | 同一概念の別表現かどうか |
| ③ ドメイン専門家の裁定 | Human 判断 | ①②が曖昧な場合 / 主因がスコープ差の場合 |

**評価指標ドラフト（数値は Human が埋める）**

| 指標 | Human が設定する目標値 | 測定方法 |
|---|---|---|
| MAPS_TO confidence の適合率 | X% 以上改善（Human 記入） | フィードバック前後で比較 |
| 「要確認」判定の割合 | Y% 以下（Human 記入） | 比較モードの出力から集計 |
| 手作業検証での誤り件数 | Z% 削減（Human 記入） | 月次確認 |

**品質ゲートドラフト**

- annotator 信頼度スコア: 過去フィードバックとの一致率で計算
- 一辺倒検知: `verdict=correct` が 90% 以上の annotator に警告
- 一致率チェック: 複数 annotator の乖離が大きい場合は `ambiguous` 扱い

### 🧑 HITL-2: 判定基準・評価指標・閾値を承認

Human が決定する:

- [ ] 判定基準ドラフトの①〜③が妥当か。追加すべき条件があれば指示する
- [ ] **評価指標の目標値を数値で設定する**（X%, Y%, Z%）。これが Neo4j 昇格ゲートになる
- [ ] 品質ゲートの閾値（90% 一辺倒検知）が妥当か
- [ ] `ambiguous` verdict をどう扱うか（確認が必要な場合は Human が回答）

**移行条件**:
- [ ] 判定基準が確定した
- [ ] 評価指標の目標値が数値で設定された
- [ ] Neo4j 昇格の3条件（50件・指標達成・スキーマ安定）が文書に記録された

---

## Phase 3: 比較モード実装

> 検証作業を LLM が支援できる最小の形（比較 + フィードバック収集 + 逆流）を実装する。

### LLM が自律的に行う

**Step 3-1: `--compare-with` オプションの実装**

```bash
python scripts/run.py search "MTU" --profile hw-spec --compare-with sw-spec
```

出力形式（LLM が実装）:

```
[hw-spec] Network > MTU設定 > 最大値: 1500
  → [sw-spec] ネットワーク > MTU上限: 1500      (確信度: 高  ✅)
  → [sw-spec] フレーム設定 > 最大フレーム長: 9000 (確信度: 低  ⚠️ 要確認)
```

**Step 3-2: 製品名正規化辞書のドラフト生成**

HITL-1 の揺れ確認結果を元に、正規化辞書のドラフトを生成する:

```json
{
  "entities": [
    { "canonical": "（LLM が候補を生成）", "aliases": ["（揺れ表現一覧）"] }
  ]
}
```

**Step 3-3: フィードバック機能の実装**

- `feedback` モードの追加（`feedback record / list / summary`）
- フィードバックスキーマ（JSON Lines）:

```json
{
  "feedback_id": "uuid-v4",
  "timestamp": "ISO8601",
  "verdict": "correct | incorrect | ambiguous",
  "confidence_declared": 0.85,
  "annotator_id": "user@example.com",
  "annotator_type": "human | llm",
  "cell_a": { "profile": "hw-spec", "node_id": "sha256", "path": ["Network","MTU"], "text": "1500" },
  "cell_b": { "profile": "sw-spec", "node_id": "sha256", "path": ["ネットワーク設定","MTU上限"], "text": "9000" },
  "expected_value": "1500",
  "actual_value": "9000",
  "comment": "単位系が違う（レイヤ2 vs レイヤ3）",
  "maps_to_confidence_before": 0.72
}
```

格納先: `<data-path>/feedback/YYYY-MM-DD.jsonl`

**Step 3-4: フィードバック逆流（可視性）の実装（必須）**

```
✅ フィードバックを記録しました
  対応ペア: [Network > MTU] → [ネットワーク設定 > MTU上限]
  あなたの判定: incorrect（確信度: 85%）
  この対応ペアへの累計: 3件（correct: 1 / incorrect: 2）
  → MAPS_TO confidence: 0.72 → 0.58（更新）
```

⚠️ 逆流は MVP の必須要件。後回し厳禁。逆流がないと検証者の入力が 3 ヶ月で枯渇する。

### 🧑 HITL-3: 出力フォーマット・製品名辞書を承認

Human が判断する:

- [ ] 比較モードの出力形式が作業フローに合っているか
- [ ] **製品名正規化辞書のドラフトを確認し、追加・修正を指示する**（ドメイン専門家のアクセスが必要な場合はここで）
- [ ] フィードバック逆流の表示が分かりやすいか
- [ ] `feedback summary` の集計表示が判断に役立つか

**移行条件**:
- [ ] 比較モードが動作している
- [ ] **製品名正規化辞書が Human にレビューされた**（フェーズ 5 の前提条件）
- [ ] フィードバック逆流が動作している

---

## Phase 4: 改良サイクル実証

> フィードバックを実際に蓄積し、改良サイクルが機能することを実測で確認する。

### LLM が自律的に行う

**継続的な自律作業（毎回の検証セッションで実行）**:

1. 比較モードで対応候補を生成
2. 判定基準①（数値・単位）と②（セマンティック等価）で LLM が自動ラベリング
3. 品質ゲートをチェック（一辺倒検知・一致率チェック）
4. フィードバックサマリーを生成し Human に提示:

```
📊 フィードバックサマリー（2026-06-01 時点）
  累計件数: 23件
  verdict 分布: correct 12 / incorrect 8 / ambiguous 3
  annotator 信頼度: user@example.com → 0.84
  ⚠️ 要確認: 「フレーム設定 > 最大フレーム長」への incorrect が 3件集中
  MAPS_TO confidence 平均: 0.71 → 0.64（変化）
```

5. confidence スコアを JSON フィードバックから更新（バッチ処理）

### 🧑 HITL-4a: フィードバック品質の定期確認（毎月）

Human が判断する:

- [ ] LLM の自動ラベリング（① 数値・単位、② セマンティック等価）が妥当か抜き取り確認
- [ ] サマリーで「⚠️ 要確認」として挙げられた対応ペアに Human 判断を追加する
- [ ] 一辺倒検知に引っかかる annotator がいれば確認
- [ ] **③ ドメイン専門家の裁定が必要なケースに回答する**

### 🧑 HITL-4b: 3ヶ月チェックポイント（中間判定）

Human が決定する:

- [ ] 累計 20 件以上蓄積されているか
- [ ] 評価指標が改善傾向にあるか（LLM がレポートを生成）
- [ ] 品質ゲートが機能しているか
- [ ] 昇格条件の「50 件」が 6 ヶ月以内に達成できそうか。困難な場合は条件を見直す

**移行条件**:
- [ ] 累計 50 件以上のフィードバックが蓄積された
- [ ] HITL-2 で設定した評価指標のいずれかが達成された
- [ ] JSON スキーマに破壊的変更が必要ないと確認できた

---

## Phase 5: 技術実装（診断結果次第）

> Phase 1 の診断で「A. 語彙ギャップ」が主因と確定した場合のみ実施。

### LLM が自律的に行う

**Step 5-1: ベクトル検索の追加**

```bash
# requirements-vector.txt を追加
# sentence-transformers（日本語対応モデル: paraphrase-multilingual-MiniLM-L12-v2）

# Neo4j vector index 作成（5.x 以上が必要）
CREATE VECTOR INDEX cell_vector FOR (n:Cell) ON (n.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 384, `vector.similarity_function`: 'cosine'}}
```

- 既存 Cell ノード全件への埋め込みバックフィルを実行
- ハイブリッド検索（全文 + ベクトル）に切り替え

**Step 5-2: 確信度閾値の調整提案**

| 確信度 | 初期閾値（案） | 扱い |
|---|---|---|
| 高 | cosine ≥ 0.90 | 自動受理候補 |
| 中 | 0.75 ≤ cosine < 0.90 | 要確認 |
| 低 | cosine < 0.75 | 要人間判断 ⚠️ |

### 🧑 HITL-5: ベクトル検索の有効性を確認

Human が判断する:

- [ ] Phase 1 で失敗していたクエリが改善されているか確認
- [ ] **確信度閾値を調整する**（LLM の提案値を採用するか変更するかを決める）
- [ ] 誤マッピング（意味が違うのに高確信度でマッチしている）が増えていないか

**移行条件**:
- [ ] ヒット件数が改善されている（定量確認）
- [ ] 確信度閾値が Human に承認された

---

## Phase 6: Neo4j 統合

> Phase 4 の昇格条件（50 件蓄積・評価指標達成・スキーマ安定）を満たしてから着手。

### 🧑 HITL-6: 昇格ゲート審査

先に Human が以下を確認する:

- [ ] フィードバック累計 50 件以上
- [ ] HITL-2 で設定した評価指標のいずれかが達成された
- [ ] JSON スキーマへの破壊的変更が不要と確認できた
- [ ] Neo4j バージョンが 5.x 以上

全条件を満たした場合のみ LLM が実装に進む。

### LLM が自律的に行う

**グラフスキーマの拡張**:

```
(:ValidationEvent {
  id: UUID,
  timestamp: datetime,
  verdict: "correct" | "incorrect" | "ambiguous",
  annotator_type: "human" | "llm" | "rule",
  annotator_trust: float,
  confidence_before: float,
  confidence_after: float,
  comment: string
})
(:ValidationEvent)-[:VALIDATES]->(:Cell)    # 仕様書B側
(:ValidationEvent)-[:FOR_MAPPING]->(:Cell)  # 仕様書A側
```

**confidence スコア更新バッチ（Cypher）**:

```cypher
MATCH (a:Cell)-[m:MAPS_TO]->(b:Cell)
MATCH (v:ValidationEvent)-[:FOR_MAPPING]->(a)
MATCH (v)-[:VALIDATES]->(b)
WITH m,
     avg(CASE v.verdict
           WHEN 'correct'   THEN 1.0
           WHEN 'incorrect' THEN 0.0
           ELSE 0.5
         END * v.annotator_trust) AS new_conf
SET m.confidence = new_conf
```

**JSON フィードバックの Neo4j へのマイグレーション**:

既存の JSON Lines を ValidationEvent ノードとして一括インポートするスクリプトを生成・実行。

---

## リスクと対策

| リスク | 対策 |
|---|---|
| 診断が形式的に終わる | HITL-1 で「50 件以上・Human が主因を確定」を移行条件にする |
| 担当者を巻き込めず製品名辞書が作れない | 辞書レビュー完了を Phase 5 の開始条件にする（HITL-3 で確認） |
| フィードバック汚染（低品質ラベル大量投入） | 品質ゲート（一辺倒検知・一致率チェック）を Phase 3 から組み込む |
| 逆流（可視性）が後回しになる | HITL-3 の移行条件に「逆流が動作している」を含める |
| JSON MVP が「仮置き場」として永続化される | 昇格条件を数値で文書化し、HITL-4b で中間評価する |
| 評価指標を設定しないままループを回す | HITL-2 で「数値を設定する」を Human の必須アクションにする |
| Neo4j 昇格ゲートが永遠に達成されない | HITL-4b（3ヶ月）で達成困難なら条件を見直す |

---

## HITL 一覧

| ゲート | タイミング | Human が決める主なこと |
|---|---|---|
| HITL-0 | 開始前 | 落とし込みリスト件数・annotator 体制・担当者アクセス可否 |
| HITL-1 | 診断完了後 | 主因の確定（語彙ギャップ/構造エラー/スコープ差） |
| HITL-2 | フィードバック定義後 | 判定基準の承認・評価指標の**数値設定** |
| HITL-3 | 比較モード実装後 | 出力形式の承認・**製品名辞書のレビュー** |
| HITL-4a | 毎月 | フィードバック品質の抜き取り確認・③ 専門家裁定への回答 |
| HITL-4b | 3ヶ月後 | 中間判定・昇格条件の見直し判断 |
| HITL-5 | ベクトル検索追加後 | **確信度閾値の承認**・改善の定量確認 |
| HITL-6 | Neo4j 統合前 | **昇格ゲート審査**（全条件達成の確認） |

---

## 未解決の問いと確認事項

HITL-0 で Human に回答をもらう事項:

1. 手作業での落とし込みリストは何件あるか
2. 検証作業の頻度は週・月あたり何件程度か（50 件達成の見通しに影響）
3. annotator は何人か。複数人の場合は共有 data-path が必要
4. 仕様書担当者（ドメイン専門家）に継続的にアクセスできるか
5. Neo4j は 5.x 以上か

---

## 関連ドキュメント

- [改善ストーリー（既存実装）](./2026-05-11-table-spec-extractor-improvement-story.md)
- スキル定義: `.github/skills/table-spec-extractor/SKILL.md`
