# ltm-use 昇格ロジック改善案 v6

> **参照元**: [個人の暗黙知を組織知に自動昇格させる OSS マルチエージェント基盤を作った話 (Apache 2.0) — gen99, Qiita](https://qiita.com/gen99/items/43b3842920d94a6ad1de)

---

## 1. 現状の昇格ロジックと課題

### 現行の仕組み

```
share_score >= 70  → 昇格候補（半自動: ユーザー確認）
share_score >= 85  → 自動昇格対象（--auto フラグ）

share_score の主な加算要因:
  - recall ごとに access_count 加算
  - rate --good で +10
  - rate --correction で -15
  - importance: critical → ×1.5 乗算、high → ×1.2
  - importance: critical → 即時自動昇格（save と同時）
```

### 課題

| # | 課題 | 説明 |
|---|------|------|
| 1 | **個人頻度バイアス** | share_score はあくまで「自分が何回 recall したか」に依存する。自分だけが繰り返し参照してもスコアが上がり、チームにとっての価値とズレる |
| 2 | **アウトカムとの疎結合** | recall → 記憶を使って成果が出た（テスト合格・PR マージ等）という因果が記録されない |
| 3 | **LLM による価値判断なし** | 「この記憶が組織知として昇格すべきか」をモデルが評価する仕組みがない |
| 4 | **バッチ処理不在** | 昇格はトリガー駆動（save 時・recall 時）のみ。定期的な棚卸し型の昇格検討がない |
| 5 | **1 シグナル依存** | share_score という単一スコアに全要素を詰め込んでいるため、特定のユーザー行動で簡単に昇格/非昇格が決まる |

---

## 2. Praxia の昇格ロジック（参照元の要点）

Praxia は「個人記憶 → 組織知」の自動昇格を **3 つの独立シグナルの並列評価 + 加重ブレンド**で実現している。

```
L1 PersonalMemory
      │
      │  Sleep-time Consolidator（夜間バッチ）
      │       ↓ 3 シグナルを並列実行
      │  ① 頻度 (Frequency)    : N 人 / N セッション以上で再出現するか
      │  ② アウトカム相関      : 正成果（受注・テスト合格・PR 承認等）と共起するか
      │  ③ LLM 自己評価        : 0..1 で「組織知候補度」をモデルが採点
      │       ↓ 加重ブレンド
      │  いずれか 1 つでも決定的なら昇格
      ▼
L3 SharedMemory
```

**ポイント**:
- 3 シグナルは独立して計算され、どれか 1 つが「決定的」なら単独で昇格できる（OR 条件）
- 通常は 3 シグナルの加重ブレンドで最終スコアを算出（AND 的な総合評価）
- 人間の夜間バッチ相当（Sleep-time Consolidator）が定期実行し、リアルタイムに昇格を判断しない

---

## 3. ltm-use への適用案

### 3.1 アーキテクチャマッピング

| Praxia | ltm-use |
|--------|---------|
| L1 PersonalMemory | `home` スコープ |
| L3 SharedMemory | `shared` スコープ |
| PromotionEngine | `promote_memory.py`（拡張） |
| Sleep-time Consolidator | `periodic_promote.py`（新規）|

### 3.2 3 シグナルの定義

#### シグナル① 頻度シグナル (Frequency Signal)

```
frequency_signal = min(1.0, cross_session_count / F_threshold)

cross_session_count: 記憶が recall された「セッション数」（同セッション複数 recall = 1 カウント）
F_threshold: デフォルト 5 セッション（config で変更可能）
```

**現行との差分**:
- 現在の `access_count` は「同セッション内での複数 recall も全カウント」なので水増しされやすい
- セッション単位での cross_session_count を新たに記録することで「継続的に参照される記憶」を正しく評価

**実装追加フィールド**:

```yaml
# memory frontmatter に追加
cross_session_count: 3      # distinct session での recall 数
last_session_id: "sess-20260509-001"  # 重複カウント防止用
```

#### シグナル② アウトカム相関シグナル (Outcome Signal)

```
outcome_signal = min(1.0, positive_outcomes / O_threshold)

positive_outcomes: 記憶を recall したセッションで positive outcome が記録された回数
O_threshold: デフォルト 3 回（config で変更可能）
```

**positive outcome の定義**（エージェントが自動検出 or ユーザーが `rate --good` 時に記録）:

| outcome_type | 検出トリガー |
|---|---|
| `test_passed` | `rate --good --outcome test_passed` |
| `pr_merged` | `rate --good --outcome pr_merged` |
| `bug_fixed` | `rate --good --outcome bug_fixed` |
| `decision_made` | `rate --good --outcome decision_made` |
| `user_rating` | 従来の `rate --good`（汎用）|

**実装追加フィールド**:

```yaml
positive_outcomes: 2        # positive outcome 総数
outcome_log:                # 詳細ログ
  - session: "sess-20260501-003"
    type: "pr_merged"
    at: "2026-05-01T14:23:00"
```

#### シグナル③ LLM 自己評価シグナル (LLM Self-eval Signal)

```
llm_signal = llm_evaluate(memory) → float [0.0, 1.0]
```

**評価プロンプト骨格**:

```
あなたは組織の知識管理AIです。
以下の記憶エントリを読み、「この情報がチーム全体で共有すべき組織知か」を評価してください。

[記憶エントリ]
タイトル: {title}
カテゴリ: {category}
要約: {summary}
本文: {content}
タグ: {tags}
アクセス数: {access_count} / セッション数: {cross_session_count}

評価基準:
- 高スコア (0.8〜1.0): 再利用性が高い、チームの誰もが知るべき、プロジェクト横断で有効
- 中スコア (0.4〜0.7): 一部のメンバーには有用だが個人依存が大きい
- 低スコア (0.0〜0.3): 個人的な一時メモ、特定コンテキストにのみ有効

JSON で返答: {"score": 0.85, "reason": "..."}
```

**実装追加フィールド**:

```yaml
llm_promote_score: 0.82     # LLM 評価スコア (0..1)
llm_promote_evaluated_at: "2026-05-09"  # 評価日（再評価タイミング管理用）
```

---

### 3.3 最終プロモーションスコアの計算

```
promotion_score = w1 * frequency_signal
                + w2 * outcome_signal
                + w3 * llm_signal

デフォルト重みづけ:
  w1 = 0.35  (頻度)
  w2 = 0.35  (アウトカム相関)
  w3 = 0.30  (LLM 自己評価)
```

**昇格判定**:

```
# 総合スコア閾値
promotion_score >= 0.70 → 昇格候補（半自動: ユーザー確認）
promotion_score >= 0.85 → 自動昇格

# 単独決定条件（いずれか 1 つで即時昇格）
frequency_signal >= 0.95  → 自動昇格（多数セッションで継続参照）
outcome_signal   >= 0.90  → 自動昇格（成果との相関が極めて高い）
llm_signal       >= 0.95  → 自動昇格（LLM が確信を持って組織知と判定）

# importance による即時昇格は維持（既存ルールとの互換性）
importance: critical → 従来通り即時昇格
```

### 3.4 後方互換: 現行 share_score との統合

既存の `share_score` は `frequency_signal` の初期値として再利用する。

```
# 移行期の frequency_signal 計算（cross_session_count 未記録の既存記憶向け）
if cross_session_count is None:
    frequency_signal = min(1.0, share_score / 100.0)
else:
    frequency_signal = min(1.0, cross_session_count / F_threshold)
```

---

### 3.5 Sleep-time Consolidator 相当: periodic_promote.py（新規）

```
実行タイミング:
  - auto_update.py（既存の periodic_scripts）から週次で呼び出し
  - ユーザーが「定期昇格を実行して」と指示したとき

処理フロー:
  1. home スコープの全アクティブ記憶を走査
  2. 各記憶の 3 シグナルを計算
     - frequency_signal: cross_session_count から算出
     - outcome_signal:   positive_outcomes から算出
     - llm_signal:       llm_promote_score が 14 日以上古ければ再評価
  3. promotion_score を算出
  4. 自動昇格候補を home → shared に昇格 + git commit
  5. 半自動候補をレポートとして出力（ユーザーへの確認待ち）
```

**LLM 呼び出しコスト対策**:
- `llm_promote_evaluated_at` が 14 日以内の記憶は再評価しない
- `frequency_signal < 0.2 AND outcome_signal < 0.2` の低関心記憶は LLM 評価をスキップ

---

## 4. config.json への追加パラメータ

```json
{
  "promotion": {
    "frequency_threshold_sessions": 5,
    "outcome_threshold_count": 3,
    "weights": {
      "frequency": 0.35,
      "outcome": 0.35,
      "llm": 0.30
    },
    "auto_promote_score": 0.85,
    "semi_auto_promote_score": 0.70,
    "decisive_frequency": 0.95,
    "decisive_outcome": 0.90,
    "decisive_llm": 0.95,
    "llm_eval_interval_days": 14,
    "llm_eval_skip_below": 0.2
  }
}
```

---

## 5. rate コマンドの拡張

```bash
# 従来（維持）
python scripts/rate_memory.py --id mem-xxx --good

# 拡張: outcome_type を記録
python scripts/rate_memory.py --id mem-xxx --good \
  --outcome pr_merged

python scripts/rate_memory.py --id mem-xxx --good \
  --outcome test_passed \
  --note "このナレッジでフレーキーテストの原因が特定できた"
```

---

## 6. 現行との変更サマリー

| 項目 | 現行 (v5) | 改善案 (v6) |
|------|-----------|-------------|
| 昇格判定基準 | `share_score` 単一スコア | 3 シグナルの加重ブレンド |
| 頻度計測単位 | access_count（クリック数） | cross_session_count（セッション数） |
| アウトカム記録 | rate --good で加算のみ | outcome_type 付きログ + シグナル化 |
| LLM 評価 | なし | llm_signal（定期再評価） |
| 昇格バッチ | なし（トリガー駆動のみ） | periodic_promote.py（週次バッチ） |
| 後方互換 | — | share_score → frequency_signal に自動移行 |

---

## 7. 実装優先度

| フェーズ | 内容 | 難易度 |
|--------|------|--------|
| **P1** | `cross_session_count` の記録（recall 時） | 低 |
| **P1** | `outcome_type` 付き rate コマンド | 低 |
| **P2** | 3 シグナル計算ロジック（promote_memory.py 拡張） | 中 |
| **P2** | config.json への新パラメータ追加 | 低 |
| **P3** | LLM 自己評価（llm_signal）の実装 | 中〜高 |
| **P3** | `periodic_promote.py` 実装（バッチ） | 中 |

---

## 8. 懸念点・トレードオフ

| 懸念 | 対応策 |
|------|--------|
| LLM 呼び出しのコスト | `llm_eval_skip_below` 閾値でスキップ＋ 14 日キャッシュ |
| outcome の自動検出が難しい | まず `rate --outcome` の手動指定から始め、後続で自動検出を検討 |
| 既存記憶の cross_session_count がゼロ | 移行期に share_score をフォールバックとして使用 |
| 重みづけの妥当性 | config で変更可能にしてチームごとに調整できる設計 |
