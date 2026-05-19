# gitlab-idd 案C-Lite（タスク分解中心の軽量版）実装計画 — 2026-05-19

## 1. 位置づけ・スコープ

- **元ドキュメント**: [`2026-05-19-gitlab-idd-autonomy-consolidated-plan.md`](2026-05-19-gitlab-idd-autonomy-consolidated-plan.md)
- **兄弟**: [`2026-05-19-gitlab-idd-plan-c-implementation.md`](2026-05-19-gitlab-idd-plan-c-implementation.md)（フル案C）
- **本書**: 案C の **軽量代替** を、案A（即効・観測）と IV-1（攻めの分解）を中核に再構成。
- **戦略**: **多くのタスクを小さくして思慮の発動機会を減らし、分解しきれないタスクにだけ
  フル案C を選択適用する。**

## 2. 含む施策

| 適用範囲 | 施策 | 出典 |
|---------|------|------|
| 全タスク共通 | I-1（着手前 recall + wiki query） | 案A |
| 全タスク共通 | I-3（LTM save 高信号化） | 案A |
| 全タスク共通 | III-2（取り消し線証跡） | 案A |
| 全タスク共通 | V-1 部分（観測・eval・KPI・確信度スコア） | 案A |
| 全タスク共通 | III-1 軽量版（AC 提案 → 承認、未検証の仮定隔離、provisional フラグ） | 案C 軽量化 |
| 分解時 | **分解レビュー関門 ★新規** | 本書の核心 |
| 分解時 | IV-1（不確実性軸の分解、垂直スライス、spike、ワーカー split 権限） | 案D |
| `complexity:high`/`spike` のみ | フル案C の Phase 3.5 設計レビュー関門 / II-2 / II-3 | 案C 選択適用 |

## 3. 決定事項からの制約（案C と共通）

| 決定 | 制約 |
|------|------|
| ①中間 | 通常タスクは中断少なめ。`complexity:high` だけフェーズ境界中断あり |
| ②B（環境ファセット推奨） | 案C-Lite は環境スコープに直接依存しない |
| ③中間 | AC は提案 → 承認、取り消し線証跡、`<details>` 折りたたみで肥大化回避 |
| ④B（品質優先・分散多視点） | 分散多視点は「分解レビュー関門」と「complexity:high 時のフル案C」で担保 |

## 4. 戦略図

```
Requester: 依頼受領
   │
   ▼
[分解] ─→ [分解レビュー関門 ★] ─→ イシュー群作成（complexity ラベル付与）
            │
            別ノードが具体的・反証可能な懸念を最低 1 つ:
              「#3 は API 共有で #2 と非独立」
              「#5 は AC が単一でない」
              「#7 は spike にすべき」
              「#11 は分解しきれない → complexity:high」

ワーカー: イシュー取得 → complexity ラベルで分岐
   ├─ complexity:normal（大多数）  → 通常フロー（Phase 1→2→3→4→5）
   │                                  + 全タスク共通施策
   └─ complexity:high / spike      → フル案C フロー（Phase 3.5 設計レビュー関門あり）
                                      [兄弟ドキュメント参照]
```

## 5. 施策別の実装詳細

### 5-1. 分解レビュー関門（核心の新規）

`requester-post.md` に追加するステップ:

1. 分解とイシュー本文の準備が終わったら、まだイシューを作成せず**分解結果のサマリー
   コメントを下書きする**（タスク一覧・依存関係・complexity 仮判定・spike 候補）。
2. 別ノードを navigator として要請（ノード不在時は agent-reviewer の
   `decomposition-critique` perspective + 人間チェックポイント）。
3. navigator は具体的・反証可能な懸念を最低 1 つ返す。`LGTM` 単独は禁止。
4. requester が各懸念を解消（再分解 / 依存追加 / spike 化 / `complexity:high` 付与）して
   から実際にイシュー作成。

マーカー規約:
- `<!-- gitlab-idd:decomposition-review-requested:{requester-node} -->`
- `<!-- gitlab-idd:decomposition-challenge:{reviewer-node} -->`
- `<!-- gitlab-idd:decomposition-cleared -->`

これは案C の II-1（設計レビュー関門）を**分解時点に前倒し**したもの。本来 1 イシュー毎に
払うレビュー費用を、N イシューを生む分解 1 回に集約することで効率化する。

### 5-2. complexity ラベルと選択適用

新ラベル:

| ラベル | 意味 | 後続フロー |
|--------|------|-----------|
| `complexity:normal` | デフォルト | 通常フロー（軽量） |
| `complexity:high` | 分解しきれない / 高不確実性 / 設計分岐あり / 既知の落とし穴あり | フル案C |
| `complexity:spike` | 成果物が知識（推奨・wiki・LTM） | フル案C |

ワーカーは取得時にラベルで分岐する。

### 5-3. spike イシューの正式化

- **成果物**: 知識記録（LTM `save_memory.py` + 必要なら `wiki_ingest.py` + design 推奨）。
  コード変更なし。
- **ターゲットブランチ不要**: コード MR を作らない。
- **AC の書き方**: 「{調査内容}の結論として、{形式}で記録する。後続タスク #N が利用できる
  状態にする」。
- **Phase 5 の代替**: コード MR でなく「成果物コメント」を投稿（LTM `mem-id` と wiki ページ
  パスを記載）。
- **依存先になる**: 後続の `complexity:normal` タスクは `## 依存イシュー` で spike 完了を待つ。

spike が「分解しきれない不確実性」を**先に安く解消**し、後続を `complexity:normal` の
高確信タスクに変える。これが案C-Lite が「分解で思慮を要らなくする」根拠の中心。

### 5-4. ワーカーの split 権限

行動原則 11 に追加: **ワーカーも split を要求できる。**

- ワーカーが Phase 3 着手時に「まだ大きい / 隠れたサブタスクあり / 単一 AC でない」と判断
  したら:
  - 派生イシューとして分割提案コメントを投稿
  - `status:needs-decomposition` に更新（新ラベル）
  - requester に分解再実行を依頼（5-1 のループに戻る）
- 着手前にコードを最も読むワーカーが気づける分解の不備を引き戻す経路を作る。

### 5-5. III-1 軽量版（全タスク共通）

フル案C の III-1 から、Phase 3.5 設計レビュー関門を**除いた**版:
- AC 矛盾の解消（`worker-role.md:470/:528`）— ワーカーは AC 改訂を提案、requester が承認。
- 推測の隔離（design doc に「## 未検証の仮定」セクション）。
- provisional フラグ（MR に「⚠️ 未検証の仮定あり」）。
- 取り消し線証跡（III-2）。

## 6. 実装ステップと順序

1. 確信度スコア primitive（`gl.py`、案C と共通）
2. III-2 取り消し線追記ヘルパー（`gl.py`、案A と共通）
3. complexity ラベル規約 + 分解レビュー関門マーカー定義
4. `requester-post.md` 改訂 — 分解レビュー関門、complexity 判定基準、spike 規約
5. `worker-role.md` 改訂 — complexity 分岐、split 権限、I-1/III-1 軽量版/III-2 適用
6. `SKILL.md` 改訂 — ラベル規約、行動原則 11 拡張（ワーカー split 権限）
7. `requester-review.md` — AC 改訂承認、provisional フラグ確認
8. `eval.json` / `tests/`

`complexity:high`/`spike` のイシュー固有部分は案C 計画書の手順 3〜6 を流用する。

## 7. 影響ファイル一覧

| ファイル | 変更 |
|---------|------|
| `.github/skills/gitlab-idd/scripts/gl.py` | 確信度スコア、取り消し線ヘルパー、complexity フィルタ |
| `.github/skills/gitlab-idd/references/requester-post.md` | 分解レビュー関門、complexity 判定、spike 規約 |
| `.github/skills/gitlab-idd/references/requester-review.md` | AC 改訂承認、provisional フラグ確認 |
| `.github/skills/gitlab-idd/references/worker-role.md` | complexity 分岐、split 権限、共通施策適用 |
| `.github/skills/gitlab-idd/SKILL.md` | ラベル規約、行動原則 11 拡張 |
| `.github/skills/gitlab-idd/eval.json` / `tests/` | 検証項目追加 |

`complexity:high`/`spike` の処理は案C 計画書の影響ファイル一覧と合流する。

## 8. 残課題・検証

- **分解判定の誤り**: `complexity:high` 相当に気づかず `normal` で流すと事故。
  V-1 で「差し戻されたイシューの complexity 分布」を観測して判定基準を調整する。
- **分解レビューの儀式化**: navigator が「LGTM」化するリスク（FM7 と同型）。
  II-1 の実質性ゲートを流用し、空の同意を構造的に許さない。
- **過剰分解の協調税**: イシュー数増 = 統合ブランチ管理コスト増。親 epic イシューで
  共有文脈を保持する。
- **適用比率**: `complexity:high` の比率が継続的に高い場合、軽量効果が薄れる。
  測定 → §9 の昇格判断へ。

## 9. フル案C への昇格パス

案C-Lite を運用しながら V-1 で測り、必要に応じて段階的に昇格する:

| 観測指標 | 閾値 | アクション |
|---------|------|-----------|
| 差し戻し率（normal タスク） | 想定より高い | `complexity:high` の判定基準を下げる（より多くを高度パスへ） |
| `complexity:high` 比率 | 50% 超が継続 | 案C-Full に切り替え（Phase 3.5 を全タスクに適用） |
| 差し戻し率（normal タスク） | 継続的にゼロ近傍 | 案C は不要だった可能性 — 案C-Lite を維持 |
| 分解レビュー関門での懸念検出数 | 継続的にゼロ | 関門の実効性を再検討（儀式化の兆候） |

## 10. 案C 全面適用との比較（要約）

| 観点 | 案C-Lite | 案C-Full |
|------|---------|---------|
| 1 イシューあたりのレビュー往復 | 通常タスク 0 / `high` のみ Phase 3.5 | 全イシュー Phase 3.5 |
| レビュー集約点 | 分解 1 回 → N イシュー | イシュー毎 |
| 思慮の所在 | 主に分解時に集中 | 各イシュー実装時に分散 |
| 賭けの中心 | 「多くのタスクは機械的単位に落ちる」 | 「全タスクに depth が要る」 |
| コスト | 低 | 高 |
| 適合する条件 | コードベースが分解しやすい / requester が分解上手 | 分解しにくい問題が多い |
| リスク | 分解判定の誤り | レイテンシ・トークン増 |
