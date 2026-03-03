# ltm-use スキル評価レポート

評価日: 2026-03-03
評価対象: `.github/skills/ltm-use/` (version 3.0)
評価者: Claude (skill-evaluator 基準 + 実運用パフォーマンス分析)

---

## サマリー

| 評価軸 | 結果 |
|--------|------|
| 静的品質（ERROR） | ✅ なし |
| 静的品質（WARN） | ⚠️ 2件 |
| セキュリティ | ⚠️ MEDIUM 1件、HIGH 1件（いずれも意図的） |
| バグ | ❌ 4件（ドキュメント-実装パス不一致） |
| パフォーマンス（〜1000件） | ✅ 実用範囲 |
| パフォーマンス（10000件超） | ⚠️ 要注意 |
| **総合判定** | **⚠️ 要改良後昇格** |

バグ4件を修正すれば昇格可能。コア機能・設計は良好。

---

## 1. 静的品質チェック（skill-evaluator 基準）

### ERROR（仕様違反）

なし。

### WARN（品質改善推奨）

| コード | 対象 | 内容 |
|--------|------|------|
| `REF_NO_TOC` | `references/memory-format.md` | 162行あるが目次がない。100行超の参照ファイルには目次が推奨される |
| `SCRIPT_NETWORK` | `scripts/sync_memory.py` | git clone/pull による外部通信がある。`shared` スコープの設計上意図的だが警告対象 |

### セキュリティリスク（修正するかどうかはレビュアーが判断）

| コード | レベル | 内容 |
|--------|--------|------|
| `SEC_SCRIPT_NETWORK` | HIGH | `sync_memory.py` が git pull/clone、`memory_utils.py` が `git -C push` を実行。共有スコープの設計上意図的 |
| `SEC_SCRIPT_EXISTS` | MEDIUM | 8本のスクリプトが存在する。外部からインストールされた場合は確認が必要 |

---

## 2. バグ：ドキュメントと実装のパス不一致（重大）

Sprint 4 でパスが `~/.agent-memory/` → `~/.copilot/memory/` に変更されたが、以下の4箇所に古いパスが残っている。ユーザーが手動操作や設定確認をする際に混乱を招く。

| 箇所 | 現在の記述 | 正しい記述 |
|------|-----------|-----------|
| `SKILL.md`「設定ファイル」セクション | `~/.agent-memory/config.json` | `~/.copilot/memory/config.json` |
| `SKILL.md` recall 手動手順 ステップ4 | `~/.agent-memory/` | `~/.copilot/memory/home/` または `~/.copilot/memory/shared/` |
| `scripts/promote_memory.py` L166 | `git -C ~/.agent-memory/shared push origin main` | `git -C ~/.copilot/memory/shared push origin main` |
| `scripts/sync_memory.py` L150 | `設定ファイル: ~/.agent-memory/config.json` | `設定ファイル: ~/.copilot/memory/config.json` |

実装（`memory_utils.py`）では `HOME_MEMORY_ROOT = os.path.expanduser("~/.copilot/memory")` が正しく設定されており、動作自体は問題ない。表示テキストとドキュメントのみ修正が必要。

---

## 3. パフォーマンス評価（実運用耐性）

### 3-1. アーキテクチャ概要

```
記憶ファイル (.md)
    ↕ mtime比較
インデックス (.memory-index.json)  ← JSON、全件をメモリにロード
    ↕ recall時に毎回refresh
スクリプト（各実行は独立プロセス）
```

### 3-2. メモリ使用

| 記憶件数 | インデックスサイズ（推定） | 評価 |
|---------|--------------------------|------|
| 100件   | ~50 KB  | ✅ 問題なし |
| 1,000件 | ~500 KB | ✅ 問題なし |
| 5,000件 | ~2.5 MB | ✅ 許容範囲 |
| 10,000件 | ~5 MB  | ⚠️ JSON パース時間に注意 |
| 50,000件 | ~25 MB | ❌ インデックス設計の見直しが必要 |

インデックスエントリ1件あたりのフィールド: `filepath`, `mtime`, `id`, `title`, `summary`, `tags`, `status`, `scope`, `share_score`, `access_count`, `correction_count`, `user_rating`, `created`, `updated`（約500バイト相当）。

**実運用耐性**: 個人利用（数百件）・チーム共有（数千件）では問題なし。10,000件超は設計見直しが必要。

### 3-3. 検索速度

#### 設計評価（2段階検索）

`recall_memory.py` の検索アーキテクチャは適切に設計されている。

```
Step 1（高速）: インデックスで title/summary/tags をスコアリング
              → ファイルI/Oなし、O(n)スキャン

Step 2（精密）: 上位 max(limit*3, 30) 件のみ実ファイル読み込み
              → body の出現回数で追加スコア
```

#### ボトルネック：`refresh_index` の毎回実行

`recall_memory.py` は検索のたびに `refresh_index()` を呼ぶ。この関数は：
1. 既存インデックスをファイルから読み込む（JSONデシリアライズ）
2. `os.walk()` で全ファイルを走査
3. 各ファイルの `os.path.getmtime()` を比較
4. 変更分のみ再読み込み・インデックス更新

```python
# recall_memory.py 内のボトルネック
index = memory_utils.refresh_index(memory_dir)  # 毎回 os.walk が走る
```

#### 記憶件数別の推定レイテンシ

| 件数 | refresh_index | インデックス検索 | 上位30件ファイル読み込み | 合計 |
|------|---------------|-----------------|------------------------|------|
| 100件 | ~5 ms | ~1 ms | ~15 ms | **~20 ms** ✅ |
| 500件 | ~15 ms | ~3 ms | ~15 ms | **~33 ms** ✅ |
| 1,000件 | ~30 ms | ~8 ms | ~15 ms | **~53 ms** ✅ |
| 5,000件 | ~150 ms | ~40 ms | ~15 ms | **~205 ms** ⚠️ |
| 10,000件 | ~300 ms | ~80 ms | ~15 ms | **~395 ms** ⚠️ |

（環境依存。SSD上のローカルファイルシステムを前提とした推定値）

**実運用耐性の判定**:

| シナリオ | 規模 | 判定 |
|---------|------|------|
| 個人・プロジェクト固有（workspace） | ~200件 | ✅ 常時快適（<30ms） |
| 個人・全プロジェクト横断（home） | ~1000件 | ✅ 実用範囲（<100ms） |
| チーム共有（shared） | ~3000件 | ✅ 許容範囲（<200ms） |
| 大規模チーム共有（shared） | 10000件超 | ⚠️ 応答が遅く感じられる可能性 |

#### cleanup / promote のスケーラビリティ問題

`cleanup_memory.py` と `promote_memory.py` はインデックスを活用せず、全ファイルをフルスキャン・全文読み込みする設計になっている。

```python
# cleanup_memory.py L41 - インデックスを使わず全ファイル読み込み
for fpath, rel_cat in memory_utils.iter_memory_files(memory_dir):
    with open(fpath, encoding="utf-8") as f:
        text = f.read()
    meta, body = memory_utils.parse_frontmatter(text)
```

`recall` は2段階検索で最適化されているが、cleanup と promote はO(n)の全ファイル読み込みになる。数百件なら問題ないが、数千件規模では実行に数秒かかる可能性がある。

#### sync_memory.py の shared 検索

`sync_memory.py` の `search_shared()` もインデックスを使わず全ファイルを毎回フルテキストスキャンする。shared スコープを検索インデックス対応にすることで改善できる。

---

## 4. その他の技術的観点

### フロントマターパーサー（memory_utils.py）

PyYAML 非依存のカスタム実装は外部依存をなくす点で良い設計だが、制限がある。

- **問題**: 複数行の YAML 値（`summary` フィールドに改行が含まれる場合）は正しく解析されない
- **影響**: summary に改行文字を入れると破損するリスク（現状は`--summary`引数で指定するため改行が入ることは稀）

### フォールバック検索の自動 git pull

```python
# recall_memory.py L126-133
if remote:
    print("  → shared を git pull して再検索します...")
    ok, msg = memory_utils.git_pull_shared(...)
```

workspace に記憶がない場合に自動で git pull が走るのは便利だが、ネットワーク遅延（数秒）が発生することをユーザーが認識していない場合に体験が悪化する可能性がある。現状は `print` でメッセージが出るので問題は軽微。

### share_score スコアリング設計

```
min(access_count * 8, 32)   # 上限32点
+ min(tags数 * 5, 20)        # 上限20点
+ min(本文文字数 / 100, 18)   # 上限18点
+ (10 if status==active)     # 10点
+ max(min(user_rating*10,20),-20)  # ±20点
- min(correction_count*5,20) # -20点ペナルティ
```

参照頻度（アクセス数）を最大重視しており、実際の使用実績を昇格根拠にする設計思想と一致している。`user_rating` による ±20 点のブースト/ペナルティも適切。

---

## 5. 推奨アクション

### 必須（バグ修正）

| 優先 | 対象 | 修正内容 |
|------|------|---------|
| 高 | `SKILL.md` L283 | `~/.agent-memory/config.json` → `~/.copilot/memory/config.json` |
| 高 | `SKILL.md` L138 | recall手動手順の `~/.agent-memory/` → `~/.copilot/memory/home/` |
| 高 | `promote_memory.py` L166 | `~/.agent-memory/shared` → `~/.copilot/memory/shared` |
| 高 | `sync_memory.py` L150 | `~/.agent-memory/config.json` → `~/.copilot/memory/config.json` |

### 推奨（品質改善）

| 優先 | 対象 | 修正内容 |
|------|------|---------|
| 中 | `references/memory-format.md` | 目次を追加（REF_NO_TOC 解消） |
| 低 | `cleanup_memory.py`, `promote_memory.py` | インデックスを活用した候補フィルタリング（大規模対応） |
| 低 | `sync_memory.py` `search_shared()` | インデックスベース検索に変更 |

---

## 6. 評価結論

**判定: ⚠️ 要改良後昇格**

- コア機能（save / recall / rate / promote の連携、3スコープ設計、2段階検索）は適切に実装されている
- スクリプト間の一貫性も保たれており、設計品質は高い
- **唯一の阻害要因は Sprint 4 のパスリネーム時に取り残された4箇所のパス不一致**
- パスを修正すれば、個人〜チーム規模（〜3000件）の実運用に耐えられるスキルとして昇格可能
- 大規模利用（10000件超）に向けては cleanup/promote のインデックス活用が追加課題となる

改良完了後のフィードバック収集: ok ≥ 2 件で再評価を推奨する。
