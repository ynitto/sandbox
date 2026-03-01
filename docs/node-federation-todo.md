## ノードフェデレーション 未実装・不完全箇所レポート

### フェーズ別実装状況

| フェーズ | 状態 |
|---------|------|
| Phase 1: 基盤（Node Identity, Registry v5, Promotion Policy, Snapshot） | ✅ ほぼ完了 |
| Phase 2: コアトラッキング（Sync Policy統合, Delta Tracker） | ⚠️ 部分的 |
| Phase 3: エンドツーエンド統合（Metrics, Queue UI, Diff/Sync） | ❌ 未実装 |

---

### 🔴 クリティカルな未実装箇所

#### 1. `pull.py` が `check_sync_protection()` を呼ばない
- **場所**: `scripts/pull.py`（ローカル変更保護の統合部分）
- `delta_tracker.py` に `check_sync_protection()` 関数は存在するが、`pull.py` はインポートも呼び出しもしていない
- **影響**: ローカル変更済みスキルが pull 時に上書きされてしまう。フェデレーション保護の核心機能が無効

#### 2. `record_feedback.py` のメトリクス未計算
- **場所**: `scripts/record_feedback.py`
- レジストリスキーマに `metrics.total_executions`、`metrics.ok_rate`、`metrics.last_executed_at`、`metrics.central_ok_rate` フィールドは定義済み
- **しかしフィードバック記録時にこれらの値が一切更新されない**
- ok_rate の計算、実行回数カウント、タイムスタンプ記録のコードが未実装

#### 3. `manage.py` の Contribution Queue UI が欠落
- **場所**: `scripts/manage.py`
- キューへの追加（`promotion_policy.py` 経由）はできるが、**キューの閲覧・管理・処理のUI関数がない**
- `promote_skills()` 内にプレースホルダーのみ（line 269, 318）:
  ```python
  selected_indices = []  # プレースホルダー
  repo_choice = 0        # プレースホルダー
  ```

---

### 🟡 部分的な実装

#### 4. `delta_tracker.py` のキャッシュフォールバック戦略の欠如
- **場所**: `scripts/delta_tracker.py` (lines 81-92)
- 中央キャッシュが存在しない場合、サイレントに `local_modified=False` を返す
- リモートからのフェッチや警告が一切ない → 実際のローカル変更を見逃す可能性

#### 5. `version` フィールドの比較未実装
- レジストリスキーマに `version`、`central_version`、`version_ahead` フィールドが定義済み
- `pull.py` でバージョン読み取りはするが、**バージョン比較・自動更新判断ロジックが未実装**

---

### 🔴 未実装のコマンド・機能

| 機能 | 場所 | 状況 |
|------|------|------|
| `diff` コマンド（ローカル vs セントラル比較） | `manage.py` | 未実装 |
| `sync` コマンド（クロスリポジトリ配布） | `manage.py` | 未実装 |
| リネージ可視化（スキル系譜の表示） | `manage.py` | 未実装 |
| 自動プロモーション通知 | `promotion_policy.py` | 未実装 |

---

### エラーハンドリングの問題

- **`pull.py`**: `pinned_commit` の checkout 失敗がサイレントフォールバック
- **`push.py`**: 一時ディレクトリのクリーンアップが try/finally で保護されていない
- **`node_identity.py`**: `except Exception` の過剰な例外捕捉でエラー内容が隠れる

---

### まとめ

最重要の修正対象は以下の2点です：

1. **`pull.py` に `check_sync_protection()` の呼び出しを追加する**（ローカル変更保護の有効化）
2. **`record_feedback.py` にメトリクス計算ロジックを実装する**（ok_rate・実行回数・タイムスタンプの更新）

これらがないと、フェデレーション設計の「保護 & プロモーション」ワークフロー自体が機能しません。

具体的にどの部分から修正を始めますか？

エージェントの完全な分析が終わりました。先ほどの報告に加えて、いくつか追加の不完全箇所が見つかりました：

---

## 追加の不完全箇所

### `push.py` — Node IDがコミットメッセージに未付与
- **場所**: `scripts/push.py` line 74-75
- 設計書では「push時のコミットメッセージにnode-idを付与」と明記されているが未実装
- 結果: セントラルリポジトリ側でどのノードからの貢献か追跡不能

### `auto_update.py` — 引数パースのバグ
- **場所**: `scripts/auto_update.py` line 196
- `action="store_true"` と `default=None` の矛盾した指定
- `store_true` は常に bool を返すため `None` にならない
- `--enable` / `--disable` フラグが正しく動作しない可能性

### `snapshot.py` — アトミックなリストアでない
- リストアが途中で失敗すると、スキルが部分的に復元された不整合状態になる
- ドライランモードもなく、事前確認ができない

### `delta_tracker.py` — ハッシュ切り詰めのリスク
- **場所**: line 36: `hashlib.sha256(...).hexdigest()[:16]`
- SHA256を16文字に切り詰めており、衝突リスクがある

### テストコードが皆無
- `delta_tracker.py`、`pull.py`、`promotion_policy.py`、`snapshot.py` いずれもユニットテスト・統合テストが存在しない

---

## 優先度まとめ

| 優先度 | 項目 |
|--------|------|
| 🔴 HIGH | `pull.py` に `check_sync_protection()` を統合 |
| 🔴 HIGH | `pull.py` のバージョン比較ロジック実装（常に `version_ahead=False`） |
| 🔴 HIGH | `manage.py` のキューUI実装（プレースホルダー解消） |
| 🟡 MEDIUM | `record_feedback.py` のメトリクス更新実装 |
| 🟡 MEDIUM | `push.py` にNode IDをコミットメッセージへ付与 |
| 🟡 MEDIUM | `snapshot.py` のアトミックリストア実装 |
| 🟢 LOW | `auto_update.py` の引数パースバグ修正 |
| 🟢 LOW | `delta_tracker.py` のハッシュ長拡張 |
