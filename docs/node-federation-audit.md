# Node Federation 実装監査レポート

**監査日**: 2026-03-01
**対象ブランチ**: `claude/node-federation-audit-dSyRS`
**監査対象**: `.github/skills/git-skill-manager/scripts/` 以下の全スクリプト

---

## 1. 概要

Node Federation 実装は**設計面では概ね完成**しているが、実行・統合に複数のギャップがある。
Phase 1–2 のフレームワーク部分は大部分が実装済み。Phase 3 機能は部分的に未完成。

---

## 2. 優先度別 課題一覧

### 🔴 HIGH（動作に直接影響）

#### 2.1 バージョン比較が機能していない
**ファイル**: `pull.py` lines 275–276
**症状**: `central_version` が常に `None`、`version_ahead` が常に `False` に設定される。

```python
# 現状（バグ）
s["central_version"] = None
s["version_ahead"] = False
```

**影響**:
- `promotion_policy.py` が `version_ahead` を昇格条件として参照しているが、常に False なので、この条件が機能しない（line 81–84）
- ローカルでバージョンを上げたスキルが「ローカル改善なし」と判定される

**修正方針**:
- pull 時にセントラルキャッシュから `central_version` を読み取る
- ローカルバージョンとセントラルバージョンを比較して `version_ahead` を計算する
- `_read_frontmatter_version()` がルートレベルの `version:` も読めるよう修正（現状は `metadata.version` のみ対応）

---

#### 2.2 sync_policy による上書き保護が機能していない
**ファイル**: `pull.py` lines 247–250
**症状**: `pull.py` が `delta_tracker.check_sync_protection()` を呼ばないため、`protect_local_modified: true` が無視される。

```python
# 現状（保護チェックなし）
dest = os.path.join(skill_home, sname)
if os.path.exists(dest):
    shutil.rmtree(dest)
shutil.copytree(winner["full_path"], dest)
```

`delta_tracker.py` には `check_sync_protection()` 関数が実装済み（lines 193–204）だが呼ばれていない。

**影響**:
- `protect_local_modified: true` 設定でも、ローカルで改善したスキルが pull 時に上書きされる
- 設計書が約束する保護動作が実現しない

**修正方針**:
- コピー前に `detect_local_modification()` でローカル変更を確認
- `check_sync_protection()` で保護対象かチェック
- 保護対象の場合はスキップし、「ローカル版を保護しています」と通知

---

#### 2.3 promote の対話選択がプレースホルダーのまま
**ファイル**: `manage.py` lines 265–269
**症状**: `selected_indices = []` がプレースホルダーのまま残っており、`promote_skills()` が実際には何もコピーしない。

```python
# 現状（バグ）
print(f"\nユーザー領域にコピーするスキルを選んでください（カンマ区切り、例: 1,3）")
selected_indices = []  # プレースホルダー
```

同様に、push 先リポジトリの選択も `repo_choice = 0` のプレースホルダー（line 318）。

**影響**:
- `python manage.py promote` コマンドを実行しても一切スキルがコピーされない
- ユーザーへの「コピーするスキルを選んでください」メッセージが表示されるが選択を受け取れない

**修正方針**:
- `interactive=True` 時は `input()` で実際のユーザー入力を受け取る
- `interactive=False` 時は全スキルを自動選択
- 貢献キュー（contribution_queue）の表示・管理コマンドを追加

---

### 🟡 MEDIUM（品質・堅牢性に影響）

#### 2.4 メトリクスが収集されない
**ファイル**: `record_feedback.py`
フィードバックを記録するが `metrics` フィールドを更新しない。
`total_executions`, `ok_rate`, `last_executed_at` が常に初期値のまま。

#### 2.5 Node IDがコミットメッセージに付与されない
**ファイル**: `push.py` lines 74–75
設計書には「push 時のコミットメッセージに node-id を付与」と明記されているが未実装。
セントラルリポジトリでどのノードからの貢献か追跡不能。

#### 2.6 スナップショットのリストアがアトミックでない
**ファイル**: `snapshot.py`
リストアが途中で失敗すると、スキルが部分的に復元された不整合状態になる。
ドライランモードもなく、事前確認ができない。

#### 2.7 レジストリのスキーマバリデーションがない
**ファイル**: `registry.py`
v5 へのマイグレーション後に必須フィールドの存在チェックが行われない。
破損したレジストリは下流で暗号的なエラーを引き起こす。

---

### 🟢 LOW（軽微・将来的なリスク）

#### 2.8 auto_update.py の引数パースバグ
**ファイル**: `auto_update.py` line 196
`action="store_true"` と `default=None` の組み合わせが矛盾。
`store_true` は常に bool を返すため、`--enable`/`--disable` フラグが意図通り動作しない可能性。

#### 2.9 ハッシュ切り詰めによる衝突リスク
**ファイル**: `delta_tracker.py` line 36
```python
return hashlib.sha256(content.encode()).hexdigest()[:16]
```
SHA256 を 16 文字に切り詰めており、2^64 空間での衝突リスクがある。

#### 2.10 プロファイル切替が未実装
**ファイル**: `manage.py`
プロファイルの作成・削除は実装済みだが、実際にスキルをフィルタリングする「適用」ロジックが不完全。

---

## 3. 未完成機能マトリクス

| 機能 | ファイル | 状態 |
|------|--------|------|
| ノードアイデンティティ | `node_identity.py` | ✅ 完成 |
| スキル系譜追跡（lineage） | `delta_tracker.py`, `pull.py` | ⚠️ スキーマあり、追跡不完全 |
| セマンティックバージョニング | `pull.py` | ⚠️ スキーマあり、比較ロジック欠如 |
| 昇格ポリシーエンジン | `promotion_policy.py` | ✅ 概ね完成（キューUI未実装） |
| 貢献キュー | `manage.py`, `promotion_policy.py` | ⚠️ データ構造あり、UI/管理未実装 |
| 選択的同期ポリシー | `pull.py`, `delta_tracker.py` | ❌ 設計あり、pull.py で未統合 |
| 実行メトリクス | `record_feedback.py` | ❌ スキーマあり、更新ロジック未実装 |
| スナップショット/ロールバック | `snapshot.py` | ✅ 完成（アトミック性のみ欠如） |
| プロファイル管理 | `manage.py` | ⚠️ 部分的実装 |

---

## 4. 対応済み修正

このレポートに対応して、以下の HIGH 優先度の修正が実装された（`claude/node-federation-audit-dSyRS` ブランチ）:

1. **`pull.py`**: バージョン比較の修正
   - `_read_frontmatter_version()` がルートレベル `version:` も読み取るよう修正
   - pull 時に `central_version` を実際のバージョンで設定
   - `version_ahead` をローカル版とセントラル版の比較で計算

2. **`pull.py`**: sync_policy 保護の統合
   - コピー前に `delta_tracker.check_sync_protection()` を呼び出す
   - 保護対象スキルはスキップし、通知メッセージを表示

3. **`manage.py`**: プロモート選択の実装
   - `selected_indices = []` プレースホルダーを実際の `input()` 処理に置換
   - `repo_choice = 0` プレースホルダーも同様に修正
   - `show_queue()` 関数で貢献キューの表示機能を追加

---

## 5. 参考: 関連ファイル

- 設計書: `docs/node-federation-design.md`
- インストール: `install.py`
- スクリプト群: `.github/skills/git-skill-manager/scripts/`
  - `registry.py` — レジストリの読み書きとスキーマ管理
  - `pull.py` — スキルのダウンロードとインストール
  - `push.py` — スキルのアップロード
  - `manage.py` — スキル管理操作全般
  - `delta_tracker.py` — ローカル変更の検出
  - `promotion_policy.py` — 昇格ポリシーの評価
  - `record_feedback.py` — フィードバックの記録
  - `snapshot.py` — スナップショット/ロールバック
  - `node_identity.py` — ノード識別情報の管理
  - `auto_update.py` — 自動更新
