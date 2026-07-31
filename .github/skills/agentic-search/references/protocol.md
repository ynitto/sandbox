# agentic-search プロトコル詳細

検索系スキル横断の反復探索ループ（agentic search）の正典。
各検索スキルはここを参照し、ループ手順・ヒント計算・収束条件を重複定義しない。

## 目次

- [役割分担](#役割分担)
- [ヒント計算](#ヒント計算)
- [next_action 決定ロジック](#next_action-決定ロジック)
- [反復ループ（疑似コード）](#反復ループ疑似コード)
- [収束条件](#収束条件)
- [追跡の扱い（探索中は副作用を抑える）](#追跡の扱い探索中は副作用を抑える)

---

## 役割分担

| 主体 | 責務 |
|------|------|
| **各検索スキル** | 自前コーパスの検索（retrieve）、結果の正規化、`hints.py` の呼び出し、機械可読出力 |
| **agentic-search** | バックエンド非依存のヒント計算（`next_action` / 候補 / 関連 / gap / 充足） |
| **エージェント**（Claude） | クエリ分解・再構成、`next_action` に基づく分岐、マルチホップ展開、収束判定、統合 |

---

## ヒント計算

正規化済み結果リストとクエリから `hints` を計算する（`compute_hints`）。

```
max_score   = max(result.score for result in results)            # 0件なら 0.0
sufficient  = (count >= 1) and (max_score >= sufficient_score)   # sufficient_score 既定 0.5

related_ids = 各結果の related のうち「結果集合の id に未出」のもの（= まだ見ていない参照）
              ※ 参照が fetch 可能な ID かはバックエンド固有。呼び出し側が辿り方を決める

suggested_queries = 上位結果（score >= 0.15）のタグを頻度順に並べ、
                    元クエリに無いタグで「元クエリ + タグ」の絞り込み候補を生成（最大 5 件）

gap_keywords = 各結果の text（無ければ title+summary+tags）のどれにもヒットしないクエリ語
```

---

## next_action 決定ロジック

```
if count == 0:                       next_action = "broaden"     # 語を減らす / 同義語で広げる
elif max_score < sufficient_score:   next_action = "refine"      # suggested_queries で再構成
elif related_ids:                    next_action = "expand"      # related_ids を辿る
else:                                next_action = "synthesize"  # 十分。反復終了して統合
```

---

## 反復ループ（疑似コード）

```
visited = set()           # 取得済みアイテムID（重複辿りを防ぐ）
collected = []
queries = decompose(information_need)   # ニーズを 1〜2 のキーワード集合に分解
for round in range(MAX_ROUNDS):         # 既定 2〜3 周で打ち切り
    out = search(queries.pop())         # 各検索スキルの検索（結果 + hints を取得）
    collected += [r for r in out.results if r.id not in visited]
    visited |= {r.id for r in out.results}
    h = out.hints
    if h.next_action == "synthesize":
        break                                  # 収束
    elif h.next_action == "refine":
        queries += h.suggested_queries         # クエリ再構成
    elif h.next_action == "expand":
        out2 = fetch(h.related_ids)            # マルチホップ（各スキル固有の取得手段）
        collected += [r for r in out2.results if r.id not in visited]
        visited |= {r.id for r in out2.results}
    elif h.next_action == "broaden":
        queries.append(broaden(h.gap_keywords))  # 語を減らす / 同義語
synthesize(collected)     # 得た結果群を統合して回答
```

---

## 収束条件

以下のいずれかでループを終了する:

- `next_action == "synthesize"`（十分な手がかりを得た）
- 新規 ID が 1 件も増えなかった周がある（情報が飽和）
- `MAX_ROUNDS`（既定 2〜3）に到達

「とにかく検索を繰り返す」のではなく、**飽和か十分性で必ず止める**こと。

---

## 追跡の扱い（探索中は副作用を抑える）

検索が `access_count` / `retention_score`（忘却曲線）などの副作用を持つバックエンド
（例: ltm-use）では、**反復中の探索検索は追跡を切る**（ltm-use なら `--no-track`）。
最終的に回答へ採用したアイテムのみ追跡対象とする。これにより、探索的な空振り検索が
間隔反復効果を誤って強化することを防ぐ。副作用のないバックエンド（wiki-use の検索、
moltbook-use の連邦検索など）はこの限りではない。
