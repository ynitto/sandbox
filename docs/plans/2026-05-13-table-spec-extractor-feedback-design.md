# table-spec-extractor フィードバック機能設計 — 合議結論

> ⚠️ このドキュメントは [verification-roadmap](./2026-05-13-table-spec-extractor-verification-roadmap.md) に統合済み。新規参照はそちらを使うこと。

**合議日**: 2026-05-13  
**合議方式**: council-system（MELCHIOR / BALTHASAR / CASPER の三賢者合議）  
**前提文書**: [改良方針・実測検証計画](./2026-05-13-table-spec-extractor-council-decision.md)

---

## 議題

検証作業（仕様書Aの設定値 → 仕様書Bへの落とし込みが正しいか比較する）の結果を**フィードバックとして蓄積し、改良サイクルを自律的に回す**機能を追加したい。

フレームA: フィードバックをどこに格納するか（Neo4j / ローカル JSON）  
フレームB: 自律的な改良サイクルを実現するために、フィードバックの構造をどう設計するか

---

## 合議の核心的発見

> **「格納先の議論より先に解くべき上流の問いが2つあった。フィードバックの定義と品質管理が未解決のままでは、どの格納先を選んでも改良サイクルは機能しない」**

ROUND 1〜2 を通じて3者全員が見落としていた死角:

| 見落とし | 内容 |
|---|---|
| フィードバックの品質が悪いとき誰が検知するか | 3者全員から言及がなかった最重要問題 |
| 改良サイクルの「改良」をどう定義・計測するか | 評価指標がないままループを回すと「ノイズを学習する機械」になる |
| フィードバックの逆流（可視性）がないと人間が参加し続けない | 検証者が「自分の判断が使われているか分からない」状態では入力が枯渇する |

---

## 統合判断

**フィードバック定義・品質管理・評価指標の設計を先行させ、MVP はシンプルなローカル JSON から始め、改良サイクルの有効性が実測で確認されたあと Neo4j に統合する**

### フェーズ概要

```
Phase 1: 定義先行     → 判定基準・品質ゲート・改良サイクルの評価指標を設計する
Phase 2: JSON MVP    → フィードバック収集・逆流（可視性）を実装する
Phase 3: Neo4j 統合  → 昇格条件（50件蓄積・評価指標達成・スキーマ安定）を満たしてから
```

---

## Phase 1: 定義先行（ストレージより先に解く問い）

### 1-1: フィードバックの判定基準を定義する

「正しい対応関係」の判定を3段階で定義する:

| 段階 | 判定方法 | 例 |
|---|---|---|
| ① 数値・単位の一致 | 機械的チェック | 値が 1500 vs 1500 / 単位が A vs mA（不一致） |
| ② セマンティック等価 | LLM 判定 | "MTU 最大値" と "Maximum Transmission Unit" が同一概念か |
| ③ ドメイン専門家の裁定 | 人間判断 | 文脈上の意味的対応が正しいか（仕様書担当者に確認） |

`ambiguous` verdict は**有効な入力として受け入れる**（人間の認知上の曖昧さを排除しない）。ただし confidence スコアへの反映時には加重を下げる。

### 1-2: 品質ゲートを設計する

フィードバックがノイズかどうかを検知する仕組み:

- annotator ごとの **信頼度スコア**: 過去フィードバックとの一致率で動的に計算する
- **一辺倒検知**: `verdict=correct` が 90% 以上の annotator に対して警告を出す
- **一致率チェック**: 同一対応ペアに複数の annotator がフィードバックを付けた場合、乖離が大きければ `ambiguous` として処理する

### 1-3: 改良サイクルの「成功」指標を先に定義する

| 指標 | 例 |
|---|---|
| MAPS_TO confidence の適合率改善 | フィードバック前後で precision が X% 以上改善 |
| 人間レビューが必要な件数の変化 | 「要確認」判定が Y% 減少 |
| 誤マッピング件数 | 手作業検証での誤り件数が Z% 減少 |

**Neo4j 昇格の条件（事前合意）**:
- [ ] フィードバック 50 件以上が蓄積された
- [ ] 評価指標のいずれかが達成された
- [ ] JSON スキーマに破壊的変更が必要ないと確認できた

---

## Phase 2: JSON MVP

### フィードバックスキーマ（JSON Lines 形式）

```json
{
  "feedback_id": "uuid-v4",
  "timestamp": "2026-05-13T10:00:00+09:00",
  "verdict": "correct | incorrect | ambiguous",
  "confidence_declared": 0.85,
  "annotator_id": "user@example.com",
  "annotator_type": "human | llm",
  "cell_a": {
    "profile": "hw-spec",
    "node_id": "sha256-hash",
    "path": ["Network", "MTU"],
    "text": "1500"
  },
  "cell_b": {
    "profile": "sw-spec",
    "node_id": "sha256-hash",
    "path": ["ネットワーク設定", "MTU上限"],
    "text": "9000"
  },
  "expected_value": "1500",
  "actual_value": "9000",
  "comment": "単位系が違う（レイヤ2 vs レイヤ3）",
  "maps_to_confidence_before": 0.72
}
```

格納先: `data-path/feedback/YYYY-MM-DD.jsonl`（プロファイルの data-path 以下）

### フィードバックの逆流（可視性）— MVP に必須

フィードバックを入力した直後に、その結果を検証者に返す:

```
✅ フィードバックを記録しました
  対応ペア: [Network > MTU] → [ネットワーク設定 > MTU上限]
  あなたの判定: incorrect（確信度: 85%）
  この対応ペアへの累計フィードバック: 3件（correct: 1 / incorrect: 2）
  → MAPS_TO confidence: 0.72 → 0.58（更新）
```

⚠️ **逆流の実装は MVP の必須要件。後回し厳禁。** 検証者が「自分のフィードバックが使われているか分からない」状態が続くと、3ヶ月で入力が枯渇する。

### `feedback` モードの追加

```bash
# 比較モードの出力を受けてフィードバックを記録する
python scripts/run.py feedback \
  --cell-a hw-spec:sha256-abc \
  --cell-b sw-spec:sha256-xyz \
  --verdict incorrect \
  --confidence 0.85 \
  --comment "単位系が違う"

# フィードバック一覧・集計
python scripts/run.py feedback list --profile hw-spec
python scripts/run.py feedback summary
```

---

## Phase 3: Neo4j 統合（昇格条件達成後）

### グラフスキーマの拡張

```
(:ValidationEvent {
  id: UUID,
  timestamp: datetime,
  verdict: "correct" | "incorrect" | "ambiguous",
  annotator_type: "human" | "llm" | "rule",
  annotator_trust: float,           # annotator 信頼度スコア
  confidence_before: float,
  confidence_after: float,
  comment: string
})

(:ValidationEvent)-[:VALIDATES]->(:Cell)     # 検証対象 Cell（仕様書B側）
(:ValidationEvent)-[:FOR_MAPPING]->(:Cell)   # 対応元 Cell（仕様書A側）
```

### confidence スコアの更新ロジック（バッチ）

```cypher
// ValidationEvent を集計して MAPS_TO confidence を更新
MATCH (a:Cell)-[m:MAPS_TO]->(b:Cell)
MATCH (v:ValidationEvent)-[:FOR_MAPPING]->(a)
MATCH (v)-[:VALIDATES]->(b)
WITH m, 
     avg(CASE v.verdict WHEN 'correct' THEN 1.0
                        WHEN 'incorrect' THEN 0.0
                        ELSE 0.5 END * v.annotator_trust) AS new_conf
SET m.confidence = new_conf
```

---

## リスクと対策

| リスク | 対策 |
|---|---|
| **フィードバック汚染**: 低品質ラベル大量投入で confidence が意味を失う | 品質ゲート（annotator 信頼度スコア・一辺倒検知）を Phase 2 から組み込む |
| **先送りの罠**: JSON MVP が「仮置き場」として永続化される | Neo4j 昇格条件（50件・指標達成・スキーマ安定）を数値で定義して文書化する |
| **人間の参加離脱**: 逆流なしでは入力が枯渇する | フィードバック逆流を MVP の必須要件として扱う（後回し厳禁） |
| **誤りを加速する機械化**: 評価指標なしにループを回す | Phase 1 で評価指標を定義し、指標未達のままの自動化は行わない |
| **昇格ゲートが永遠に達成されない** | 3ヶ月時点の中間チェックポイント（20件目安）を設定し、達成困難なら昇格条件を見直す |

---

## 中間チェックポイント

| 時点 | 確認事項 |
|---|---|
| 1ヶ月後 | フィードバックが週 X 件ペースで蓄積されているか。逆流が機能しているか |
| 3ヶ月後 | 累計 20 件以上か。品質ゲートが機能しているか（一辺倒検知に引っかかる annotator がいないか） |
| 6ヶ月後 | 50 件達成か。評価指標が改善しているか。Neo4j 昇格判断を実施する |

---

## 未解決の問いとユーザーへの確認事項

1. **検証作業の頻度**: 実際の検証作業は週・月あたり何件程度か（母数が少なければ改良サイクル自体が成立しない）
2. **annotator の人数・体制**: 複数人が検証する場合、JSON は共有ストレージが必要（ローカルではなく共有の data-path を使う）
3. **「正しい対応関係」の客観性**: ドメインによっては単一の正解が存在しない場合があるか。あるとすれば `ambiguous` 中心の運用になる
4. **Neo4j のバージョン**: Phase 3 で vector index を使う場合は 5.x 以上が必要

---

## 関連ドキュメント

- [改良方針・実測検証計画](./2026-05-13-table-spec-extractor-council-decision.md)
- [改善ストーリー（既存実装）](./2026-05-11-table-spec-extractor-improvement-story.md)
- スキル定義: `.github/skills/table-spec-extractor/SKILL.md`
