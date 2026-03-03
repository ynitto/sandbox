---
name: ltm-use
description: >
  エージェントに長期記憶（永続メモリ）を与えるコアスキル。MCP・Claude Code非依存で、
  Agent Skillsだけで動作する。記憶をMarkdownファイルとして保存・検索・管理する。
  「覚えておいて」「記憶して」「保存して」「メモして」でsave操作、
  「思い出して」「記憶を探して」「以前の」「覚えてる？」「確認して」でrecall操作、
  「記憶一覧」「何を覚えてる」「メモ一覧」でlist操作、
  「忘れて」「記憶を削除」「アーカイブして」でdelete/archive操作、
  「昇格して」「共有して」「ナレッジを広める」でpromote操作、
  「記憶を整理して」「古い記憶を削除して」でcleanup操作、
  「共有知識を取り込んで」「チームの記憶を更新して」でsync操作、
  「役立った」「間違ってた」「修正が必要」でrate操作、
  「インデックスを再構築して」「統計を見せて」でbuild_index操作。
  セッションをまたいで知識・調査結果・決定事項を継続させたいときに使用する。
metadata:
  version: "3.0"
---

# ltm-use（Long-Term Memory Use）

エージェントにセッションをまたいだ長期記憶を与えるコアスキル。
MCPサーバーやClaude Code専用機能を使わず、**Markdownファイルへの読み書きだけ**で動作する。

---

## スコープ設計

```
workspace  →  (昇格)  →  home  →  (昇格・git)  →  shared
  ↑                        ↑                            ↑
プロジェクト固有          ユーザー横断               チーム共有
git除外                   ローカル永続              git管理
```

| スコープ | 保存先 | 用途 | git管理 |
|---------|--------|------|---------|
| `workspace` | `${SKILL_DIR}/memories/` | VSCodeワークスペース固有の知見 | **除外(.gitignore)** |
| `home` | `~/.copilot/memory/home/` | 複数プロジェクト横断の知見 | 個人管理（ローカル） |
| `shared` | `~/.copilot/memory/shared/<repo名>/memories/` | チーム共有すべき知見 | **git管理（skill-registry.json のリポジトリを使用）** |

---

## パス解決

このSKILL.mdが置かれているディレクトリを `SKILL_DIR`、記憶の保存先を `MEMORY_DIR` とする。

| このSKILL.mdのパス | SKILL_DIR | MEMORY_DIR(workspace) |
|---|---|---|
| `.github/skills/ltm-use/SKILL.md` | `.github/skills/ltm-use` | `.github/skills/ltm-use/memories` |
| `.claude/skills/ltm-use/SKILL.md` | `.claude/skills/ltm-use` | `.claude/skills/ltm-use/memories` |

スクリプトは `${SKILL_DIR}/scripts/` から実行する。
記憶フォーマット仕様: `${SKILL_DIR}/references/memory-format.md` を参照。

---

## 操作一覧

| 操作 | トリガー例 | スクリプト |
|------|-----------|-----------|
| **save** | 「覚えておいて」「記憶して」「保存して」 | `save_memory.py` |
| **recall** | 「思い出して」「以前の〇〇は？」「記憶を探して」 | `recall_memory.py` |
| **list** | 「記憶一覧」「何を覚えてる？」 | `list_memories.py` |
| **update** | 「記憶を更新して」「情報が変わった」 | `save_memory.py --update` |
| **archive** | 「忘れて」「古い情報」「アーカイブして」 | `save_memory.py --update --status archived` |
| **rate** | 「役立った」「間違ってた」「修正が必要」 | `rate_memory.py` |
| **promote** | 「昇格して」「共有知識にして」「チームに広める」 | `promote_memory.py` |
| **cleanup** | 「記憶を整理して」「古い記憶を削除して」 | `cleanup_memory.py` |
| **sync** | 「チームの記憶を取り込んで」「共有知識を更新して」 | `sync_memory.py` |
| **build_index** | 「インデックスを再構築して」「統計を見せて」 | `build_index.py` |

---

## save（記憶を保存する）

```bash
# ワークスペース記憶（デフォルト）
python ${SKILL_DIR}/scripts/save_memory.py \
  --category [カテゴリ] \
  --title "[タイトル]" \
  --summary "[要約（1〜2文）]" \
  --content "[詳細内容]" \
  --tags [タグ1],[タグ2]

# ホーム記憶として保存（プロジェクト横断）
python ${SKILL_DIR}/scripts/save_memory.py --scope home \
  --category architecture --title "[タイトル]" --summary "[要約]" --content "[内容]"
```

**手順（スクリプトなし・手動）**:
1. カテゴリを決定する（例: `auth`, `bug-investigation`, `general`）
2. `${MEMORY_DIR}/[カテゴリ]/[kebab-case-title].md` を作成する
3. フォーマット仕様（`references/memory-format.md`）に従ってフロントマターと本文を書く
4. **必須**: `summary` フィールドに1〜2文の要約を書く（検索の鍵）
5. `scope`, `access_count: 0`, `share_score: 0` を設定する

**saveの判断基準**:
- ✅ セッションをまたいで価値がある情報
- ✅ 調査・決定・失敗から得た知見
- ❌ 一時的な中間出力・作業ログ
- ❌ コードベースにすでに書かれている情報

---

## recall（記憶を想起する）

recallすると `access_count` が自動加算され `share_score` が再計算される。
インデックス（`.memory-index.json`）を使った高速検索で、記憶数が増えても性能劣化しない。
ワークスペースで見つからない場合は、home/shared を自動フォールバック検索する。

```bash
# ワークスペース検索（見つからなければ home/shared にフォールバック）
python ${SKILL_DIR}/scripts/recall_memory.py "[キーワード1] [キーワード2]"

# スコープ指定
python ${SKILL_DIR}/scripts/recall_memory.py "[キーワード]" --scope all

# 全文表示
python ${SKILL_DIR}/scripts/recall_memory.py "[キーワード]" --full

# access_count を更新しない（参照ログを残さない）
python ${SKILL_DIR}/scripts/recall_memory.py "[キーワード]" --no-track

# 結果に対してインタラクティブ評価ループを実行
python ${SKILL_DIR}/scripts/recall_memory.py "[キーワード]" --rate-after
```

**検索の仕組み（2段階）**:
1. インデックスで title/summary/tags を高速スコアリング（ファイル読み込みなし）
2. 上位候補のみ実ファイルを読み込んで body も含めた精密スコアリング

**手順（スクリプトなし・手動）**:
1. `${MEMORY_DIR}/` 以下のサブディレクトリを列挙してカテゴリを把握する
2. 各 `.md` ファイルの `summary` フィールドをスキャンしてキーワードとの関連を判断する
3. 関連するファイルを全文読み込みして内容を把握する
4. 見つからない場合は `~/.copilot/memory/home/` や `~/.copilot/memory/shared/` を同様にスキャンする
5. `access_count` をインクリメントし `last_accessed` を今日の日付に更新する

**recallのタイミング**:
- 関連するタスクを始める前
- 同じ問題を調査し始めたとき（重複調査を避ける）

---

## list（記憶の一覧を表示する）

```bash
python ${SKILL_DIR}/scripts/list_memories.py                    # workspace
python ${SKILL_DIR}/scripts/list_memories.py --scope all        # 全スコープ
python ${SKILL_DIR}/scripts/list_memories.py --promote-candidates  # 昇格候補
python ${SKILL_DIR}/scripts/list_memories.py --stats            # 統計のみ
```

---

## promote（記憶を昇格・共有する）

`share_score >= 70` で昇格候補、`>= 85` で自動昇格対象。
`recall` を繰り返すほど `share_score` が上がり、昇格対象になる。

```bash
# 昇格候補を確認（ドライラン）
python ${SKILL_DIR}/scripts/promote_memory.py --list

# 半自動昇格（各記憶を確認しながら workspace → home）
python ${SKILL_DIR}/scripts/promote_memory.py

# 自動昇格（score >= 85 を全て昇格）
python ${SKILL_DIR}/scripts/promote_memory.py --auto

# home → shared（git commit も実施）
python ${SKILL_DIR}/scripts/promote_memory.py --scope home --target shared --auto

# git push（共有）
python ${SKILL_DIR}/scripts/sync_memory.py --push
```

**昇格フロー**:
```
workspace → home:  プロジェクト固有 → 個人ナレッジとして永続化
home → shared:     個人ナレッジ → チーム共有（git commit → push で共有）
```

---

## rate（ユーザー評価・修正フィードバックを記録する）

recall した記憶が役立ったか、誤りがあったかを記録する。
`user_rating` と `correction_count` が更新され `share_score` に自動反映される。

```bash
# 役立った記憶を評価（share_score +10）
python ${SKILL_DIR}/scripts/rate_memory.py --id mem-20260303-001 --good
python ${SKILL_DIR}/scripts/rate_memory.py --file memories/auth/jwt.md --good

# 誤りがあった・修正が必要な記憶（share_score -15以上）
python ${SKILL_DIR}/scripts/rate_memory.py --file memories/auth/jwt.md \
  --correction --note "JWTの有効期限を30分に変更した"

# 役に立たなかった記憶（share_score -10）
python ${SKILL_DIR}/scripts/rate_memory.py --file memories/auth/jwt.md --bad
```

**評価の impact**:
- `--good`: `user_rating +1` → `share_score` +10点（最大+20まで累積）
- `--bad`: `user_rating -1` → `share_score` -10点
- `--correction`: `user_rating -1, correction_count +1` → `share_score` -15〜-20点、修正ログが本文に追記

**recallの `--rate-after` と組み合わせる**:
```bash
python ${SKILL_DIR}/scripts/recall_memory.py "JWT認証" --rate-after
# → 結果表示後にインタラクティブな評価入力ループが開始される
```

---

## build_index（インデックスを管理する）

インデックスは recall/save/rate 時に自動更新される。
統計確認や強制再構築に使用する。

```bash
# 統計を表示（インデックス状況・記憶品質サマリー）
python ${SKILL_DIR}/scripts/build_index.py --stats

# 全スコープの統計
python ${SKILL_DIR}/scripts/build_index.py --scope all --stats

# 増分更新（通常は不要）
python ${SKILL_DIR}/scripts/build_index.py

# 強制完全再構築（インデックス破損時）
python ${SKILL_DIR}/scripts/build_index.py --force
```

---

## cleanup（不要な記憶を削除する）

参照頻度・経過日数に基づいて不要な記憶を自動判定し削除する。

```bash
# ドライラン（削除対象を確認）
python ${SKILL_DIR}/scripts/cleanup_memory.py --dry-run

# ワークスペース記憶をクリーンアップ
python ${SKILL_DIR}/scripts/cleanup_memory.py

# 全スコープ
python ${SKILL_DIR}/scripts/cleanup_memory.py --scope all
```

**削除基準**（`~/.copilot/memory/config.json` で変更可能）:
- `access_count == 0` かつ作成から 30日以上経過
- `status == archived` かつ更新から 60日以上経過
- `status == deprecated`

---

## sync（git共有領域から自動更新する）

skill-registry.json に登録されたリポジトリ（git-skill-manager と共通）を使用する。
複数リポジトリ・readonly 対応。

```bash
# 全リポジトリを pull して差分確認
python ${SKILL_DIR}/scripts/sync_memory.py

# 特定リポジトリのみ
python ${SKILL_DIR}/scripts/sync_memory.py --repo origin

# 新しい shared 記憶を home に取り込む
python ${SKILL_DIR}/scripts/sync_memory.py --import-to-home

# 全 shared からキーワード検索
python ${SKILL_DIR}/scripts/sync_memory.py --search "API設計"

# push（readonly でないリポジトリへ）
python ${SKILL_DIR}/scripts/sync_memory.py --push [--repo origin]

# skill-registry.json 未設定時のフォールバック用 remote 設定
python ${SKILL_DIR}/scripts/sync_memory.py --set-remote git@github.com:org/memories.git
```

---

## 設定

### git リポジトリ（`~/.copilot/skill-registry.json`）

git-skill-manager と共通のリポジトリ設定を使用する。
各リポジトリに `memory_root`（省略時: `"memories"`）を指定すると、
shared 記憶の保存先（`local_dir/memory_root/`）を変更できる。

```json
{
  "repositories": [
    {
      "name": "origin",
      "url": "git@github.com:org/agent-skills.git",
      "branch": "main",
      "readonly": false,
      "priority": 1,
      "memory_root": "memories"
    },
    {
      "name": "team-b",
      "url": "git@github.com:team-b/skills.git",
      "branch": "main",
      "readonly": true,
      "priority": 2
    }
  ]
}
```

- `readonly: true` のリポジトリは pull のみ（commit/push 不可）
- 複数リポジトリが設定された場合、`priority` 順に処理し、書き込みは最優先リポジトリへ
- `skill-registry.json` が未設定の場合は `config.json` の `shared_remote` にフォールバック

### メモリー設定（`~/.copilot/memory/config.json`）

```json
{
  "shared_remote": "git@github.com:org/shared-memories.git",
  "shared_branch": "main",
  "auto_promote_threshold": 85,
  "semi_auto_promote_threshold": 70,
  "cleanup_inactive_days": 30,
  "cleanup_archived_days": 60
}
```

`shared_remote` は `skill-registry.json` が未設定の場合のフォールバックとして使用される。

---

## 記憶のライフサイクル

```
[作成] save (workspace)
  ↓ recall で access_count 加算、share_score 上昇
[昇格候補] share_score >= 70
  ↓ promote_memory.py で確認
[home 昇格] workspace → ~/.copilot/memory/home/
  ↓ さらに評価が高まった場合
[shared 昇格] home → ~/.copilot/memory/shared/<repo名>/ + git commit + push
  ↓ チームが sync_memory.py でインポート
[全員のhome] チーム全員が参照・活用

並行して:
[クリーンアップ] cleanup_memory.py で低価値・古い記憶を削除
```

---

## フォーマット詳細

`${SKILL_DIR}/references/memory-format.md` を参照すること。

---

## 使用例

```
ユーザー: 「JWTの有効期限を15分に設定したことを覚えておいて」
→ save_memory.py --category auth --title "JWT有効期限設定" \
   --summary "JWTアクセストークンを15分に設定。セキュリティとUXのバランスから決定。" \
   --content "..."

ユーザー: 「以前JWT認証について何か決めたっけ？」
→ recall_memory.py "JWT 認証"
  → access_count が加算され share_score が更新される
  → 見つからなければ home/shared を自動検索

ユーザー: 「よく参照するナレッジをチームと共有して」
→ list_memories.py --promote-candidates  # 昇格候補を確認
→ promote_memory.py --auto               # 自動昇格（workspace → home）
→ promote_memory.py --scope home --target shared --auto  # git管理へ
→ sync_memory.py --push                  # チームに公開
```
