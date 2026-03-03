---
name: agent-long-term-memory
description: >
  エージェントに長期記憶（永続メモリ）を与えるスキル。MCP・Claude Code非依存で、
  Agent Skillsだけで動作する。記憶をMarkdownファイルとして保存・検索・管理する。
  「覚えておいて」「記憶して」「保存して」「メモして」でsave操作、
  「思い出して」「記憶を探して」「以前の」「覚えてる？」「確認して」でrecall操作、
  「記憶一覧」「何を覚えてる」「メモ一覧」でlist操作、
  「忘れて」「記憶を削除」「アーカイブして」でdelete/archive操作。
  セッションをまたいで知識・調査結果・決定事項を継続させたいときに使用する。
metadata:
  version: "1.0"
---

# agent-long-term-memory

エージェントにセッションをまたいだ長期記憶を与えるスキル。
MCPサーバーやClaude Code専用機能を使わず、**Markdownファイルへの読み書きだけ**で動作する。

---

## パス解決

このSKILL.mdが置かれているディレクトリを `SKILL_DIR`、記憶の保存先を `MEMORY_DIR` とする。

| このSKILL.mdのパス | SKILL_DIR | MEMORY_DIR |
|---|---|---|
| `.github/skills/agent-long-term-memory/SKILL.md` | `.github/skills/agent-long-term-memory` | `.github/skills/agent-long-term-memory/memories` |
| `.claude/skills/agent-long-term-memory/SKILL.md` | `.claude/skills/agent-long-term-memory` | `.claude/skills/agent-long-term-memory/memories` |

スクリプトは `${SKILL_DIR}/scripts/` から実行する。
記憶フォーマット仕様: `${SKILL_DIR}/references/memory-format.md` を参照。

---

## 操作一覧

| 操作 | トリガー例 | 動作 |
|------|-----------|------|
| **save** | 「覚えておいて」「記憶して」「保存して」 | 記憶ファイルを作成する |
| **recall** | 「思い出して」「以前の〇〇は？」「記憶を探して」 | キーワードで記憶を検索する |
| **list** | 「記憶一覧」「何を覚えてる？」 | 全記憶を一覧表示する |
| **update** | 「記憶を更新して」「情報が変わった」 | 既存記憶を更新する |
| **archive** | 「忘れて」「古い情報」「アーカイブして」 | 記憶をarchived状態にする |

---

## save（記憶を保存する）

**手順（スクリプトあり）**:
```bash
python ${SKILL_DIR}/scripts/save_memory.py \
  --category [カテゴリ] \
  --title "[タイトル]" \
  --summary "[要約（1〜2文）]" \
  --content "[詳細内容]" \
  --tags [タグ1],[タグ2] \
  --context "[背景]" \
  --conclusion "[学び・結論]"
```

**手順（スクリプトなし・手動）**:
1. カテゴリを決定する（例: `auth`, `bug-investigation`, `general`）
2. `${MEMORY_DIR}/[カテゴリ]/[kebab-case-title].md` を作成する
3. フォーマット仕様（`references/memory-format.md`）に従ってフロントマターと本文を書く
4. **必須**: `summary` フィールドに1〜2文の要約を書く（これが検索の鍵）

**saveの判断基準**:
- ✅ セッションをまたいで価値がある情報
- ✅ 将来の自分（エージェント）が知っていると助かる情報
- ✅ 調査・決定・失敗から得た知見
- ❌ 一時的な中間出力・作業ログ
- ❌ コードベースにすでに書かれている情報

---

## recall（記憶を想起する）

**手順（スクリプトあり）**:
```bash
# キーワード検索（スペース区切りでAND検索）
python ${SKILL_DIR}/scripts/recall_memory.py "[キーワード1] [キーワード2]"

# カテゴリ絞り込み
python ${SKILL_DIR}/scripts/recall_memory.py "[キーワード]" --category [カテゴリ]

# 全文表示
python ${SKILL_DIR}/scripts/recall_memory.py "[キーワード]" --full
```

**手順（スクリプトなし・手動）**:
1. カテゴリ一覧を確認する: `${MEMORY_DIR}/` のサブディレクトリを列挙
2. サマリーをスキャンする: 各 `.md` ファイルのフロントマター `summary` を読む
3. 関連するファイルを特定する: summaryでスコアリングし、上位を全文読み込み
4. 内容を統合して回答に反映する

**recallのタイミング**:
- 関連するタスクを始める前
- 「以前〇〇したっけ？」と思ったとき
- 同じ問題を調査し始めたとき（重複調査を避ける）

---

## list（記憶の一覧を表示する）

```bash
# 全記憶一覧（activeのみ）
python ${SKILL_DIR}/scripts/list_memories.py

# カテゴリ絞り込み
python ${SKILL_DIR}/scripts/list_memories.py --category [カテゴリ]

# アーカイブ含む全件
python ${SKILL_DIR}/scripts/list_memories.py --status all

# タグで絞り込み
python ${SKILL_DIR}/scripts/list_memories.py --tag [タグ]

# 統計のみ
python ${SKILL_DIR}/scripts/list_memories.py --stats
```

**手動での一覧確認**: `${MEMORY_DIR}/` 以下の `.md` ファイルを列挙し、
各ファイルの `title` と `summary` を読む。

---

## update（記憶を更新する）

情報が古くなった・追加情報が得られたときに更新する。

```bash
python ${SKILL_DIR}/scripts/save_memory.py \
  --update ${MEMORY_DIR}/[カテゴリ]/[ファイル名].md \
  --summary "[新しい要約]" \
  --content "[新しい詳細]" \
  --conclusion "[新しい学び]"
```

手動更新の場合は `updated` フィールドを今日の日付に変更し、内容を修正する。

---

## archive（記憶をアーカイブ・削除する）

```bash
# アーカイブ（記録は残す）
python ${SKILL_DIR}/scripts/save_memory.py \
  --update ${MEMORY_DIR}/[カテゴリ]/[ファイル名].md \
  --status archived

# 完全削除（不要な場合）
rm ${MEMORY_DIR}/[カテゴリ]/[ファイル名].md
```

---

## 記憶の維持（メンテナンス）

定期的に以下を実施する:
- **更新**: 情報が変わったら `updated` と内容を更新する
- **アーカイブ**: 古くなった記憶を `status: archived` にする
- **統合**: 関連する複数の記憶を1つのファイルにまとめる
- **削除**: 明らかに不要な記憶を削除する

---

## フォーマット詳細

`${SKILL_DIR}/references/memory-format.md` を参照すること。

---

## 使用例

```
ユーザー: 「JWTの有効期限を15分に設定したことを覚えておいて」

→ save操作:
  カテゴリ: auth
  タイトル: jwt-expiry-setting
  summary: "JWTアクセストークンの有効期限を15分に設定。セキュリティとUXのバランスから決定。"
  content: "リフレッシュトークン（7日）と組み合わせてサイレントリフレッシュを実装..."

ユーザー: 「以前JWT認証について何か決めたっけ？」

→ recall操作:
  クエリ: "JWT 認証"
  → memories/auth/jwt-expiry-setting.md にヒット
  → summaryを確認し、全文を読んで回答
```
