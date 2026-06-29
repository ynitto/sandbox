---
name: agentic-search
description: 検索を「単発の retrieve」から「エージェントが反復する探索ループ」へ引き上げる共有スキル。検索系スキル（ltm-use / wiki-use / moltbook-use など）が自前の検索結果を正規化して渡すと、次の一手（next_action / suggested_queries / related_ids / gap_keywords / sufficient）を返す。反復ループ（計画→検索→評価→再構成→展開→統合）の正典。検索結果が弱い・横断的・うろ覚えのときに使う。
metadata:
  version: 1.0.0
  tier: core
  category: meta
  tags:
    - search
    - agentic
    - retrieval
    - iterative
    - shared-library
---

# agentic-search（反復探索の共有スキル）

検索を **単発の retrieve** から **エージェント（Claude）が反復する探索ループ** へ引き上げる、
検索系スキル横断の共有スキル。コーパスごとの検索（retrieve）は各スキルが担い、
本スキルは反復ループの「頭脳」＝**次の一手の手がかり（hints）計算**を一手に引き受ける。

- アルゴリズム詳細: [`references/protocol.md`](references/protocol.md)
- 更新履歴: [`CHANGELOG.md`](CHANGELOG.md)

---

## 設計思想

> 検索エンジンが一発で正解を返すモデルではなく、**「検索 → 評価 → 再構成 → 再検索 → 統合」を
> エージェントが反復する** agentic search を採る。本スキルは反復を内蔵せず、1 ステップ分の
> 結果に対して「次に何をすべきか」と再検索・展開の材料だけを返すプリミティブに徹する。

| 主体 | 責務 |
|------|------|
| **各検索スキル** | 自前コーパスの検索（retrieve）、結果の正規化、`hints.py` の呼び出し、出力 |
| **agentic-search**（本スキル） | `next_action` 判定・フォローアップ候補・関連参照抽出・gap 検出・充足判定 |
| **エージェント**（Claude） | クエリ分解・再構成、`next_action` に基づく分岐、マルチホップ展開、収束判定、統合 |

---

## 反復ループ（エージェント駆動）

1. **計画**: 情報ニーズを 1〜2 のキーワード集合に分解する
2. **検索**: 各検索スキルで検索し、結果＋ヒントを得る（探索中は追跡を切る運用を推奨）
3. **評価**: `hints.next_action` を読む
   - `synthesize` → 手がかり十分。ループを終了し結果を統合して回答する
   - `refine` → `hints.suggested_queries` でクエリを再構成して 2 へ戻る
   - `expand` → `hints.related_ids` を辿りマルチホップ展開（2 へ戻る）
   - `broaden` → 0件。`hints.gap_keywords` を手がかりに語を減らす／同義語へ置換して 2 へ戻る
4. **収束**: 新情報が増えない／2〜3 周で打ち切り、得た結果群を統合する

収束条件・next_action 決定ロジックの正典は [`references/protocol.md`](references/protocol.md)。

---

## 正規化済み結果の契約

各検索スキルは自前の検索結果を以下の形へ変換して渡す（`tags` / `related` / `text` は任意）。

```json
{
  "id": "string",            // バックエンド固有の ID / 参照（必須）
  "title": "string",         // タイトル（必須）
  "summary": "string",       // 要約（任意）
  "tags": ["string"],        // タグ / ラベル（任意。フォローアップ候補に使う）
  "score": 0.0,              // 0..1 に正規化した関連度（必須）
  "related": ["string"],     // 他アイテムへの参照（任意。マルチホップの種）
  "text": "string"           // 任意。gap 判定用の全文。無ければ title+summary+tags で代用
}
```

---

## 利用方法

### ライブラリとして（同一プロセス・推奨）

検索スキルは兄弟ディレクトリの `agentic-search/scripts` を `sys.path` に追加して import する。

```python
import os, sys
_AS = os.path.join(os.path.dirname(__file__), "..", "..", "agentic-search", "scripts")
if os.path.isdir(_AS):
    sys.path.insert(0, _AS)
    from hints import compute_hints, format_hints
    hints = compute_hints(normalized_results, keywords)   # dict
```

未インストール時は各スキルがローカルのフォールバック実装に切り替える（オプショナル依存）。

### CLI として（プロセス分離・他言語スキル向け）

```bash
echo '{"keywords": ["JWT","認証"], "results": [ ... ]}' \
  | python {skill_home}/agentic-search/scripts/hints.py
# 人間可読:
python {skill_home}/agentic-search/scripts/hints.py --input results.json --text
```

---

## hints の出力スキーマ

| キー | 意味 |
|------|------|
| `sufficient` | `max_score >= sufficient_score`（既定 0.5）かつ 1 件以上なら `true` |
| `max_score` | 最上位結果のスコア（0.0〜） |
| `result_count` | 結果件数 |
| `next_action` | `synthesize` / `refine` / `expand` / `broaden` の推奨次アクション |
| `suggested_queries` | 上位結果のタグから生成した再検索クエリ候補（最大 5 件） |
| `related_ids` | 結果から辿れる未取得の関連参照（マルチホップ先。fetch 方法は各スキル固有） |
| `gap_keywords` | どの結果にもヒットしなかったクエリ語（再構成シグナル） |

---

## 乗り入れ済みスキル

| スキル | 検索コマンド | agentic オプション |
|--------|-------------|-------------------|
| **ltm-use** | `recall_memory.py` | `--json` / `--suggest` / `--ids`（マルチホップ取得） |
| **wiki-use** | `wiki_query.py search` | `--json` / `--suggest` |
| **moltbook-use** | `moltbook.py search` | `--json` / `--suggest` |

新しい検索スキルを乗り入れるには、検索結果を上記契約に変換して `compute_hints` を呼ぶだけでよい。
