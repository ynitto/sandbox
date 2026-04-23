---
name: persona-use
description: ユーザーのプロンプトからペルソナ（コミュニケーションスタイル・技術嗜好・専門領域）を分析してマークダウンに蓄積し、応答のパーソナライズに活用するスキル。「ペルソナを見せて」でshow、「ペルソナを初期化して」でinit、「ペルソナをリセットして」でreset。セッション開始時にペルソナを自律的に読み込み、セッション中の観察を随時更新する。
metadata:
  version: 1.0.0
  tier: core
  category: meta
  config_script: scripts/init_persona.py
  tags:
    - persona
    - personalization
    - user-model
    - emotion-valuation
---

# persona-use（User Persona Use）

ユーザーのプロンプトを分析してペルソナを蓄積・活用するスキル。
認知科学における Emotion / Valuation（感情・価値判断）層を担う。

---

## ペルソナの構造

```
<persona_home>/
├── profile.md       ← コミュニケーションスタイル・全体プロファイル
├── preferences.md   ← 技術嗜好・ツール・フォーマット好み
├── expertise.md     ← 専門領域・技術スタック・経験レベル
└── update_log.md    ← 更新ログ（追記専用）
```

`persona_home` は `skill-registry.json` の `skill_configs.persona-use.persona_home` で指定する。

---

## 設定の確認

```bash
python {skill_home}/persona-use/scripts/persona_utils.py config
```

---

## 操作一覧

| 操作 | トリガー例 | スクリプト |
|------|-----------|-----------|
| **init** | 「ペルソナを初期化して」「persona-useをセットアップして」 | `init_persona.py` |
| **show** | 「ペルソナを見せて」「ユーザーの好みは？」「プロファイルを確認して」 | `show_persona.py` |
| **update** | セッション中に新たな観察を検出したとき（自律） | `update_persona.py --log "..."` |
| **reset** | 「ペルソナをリセットして」「プロファイルをクリアして」 | `init_persona.py --reset` |

---

## セッション開始時の自律動作

persona_home が設定されていれば、セッション開始直後に以下を実行してペルソナをコンテキストに読み込む:

```bash
python {skill_home}/persona-use/scripts/show_persona.py
```

読み込んだペルソナに基づいて以下を調整する:
- 応答の詳細さ（専門家→簡潔、初学者→丁寧に）
- 使用言語（日本語/英語の好み）
- コード量・コメントの量
- 好みのフレームワーク・ツールを優先提案

---

## セッション中の自律更新

以下を検出したら `update_persona.py --log` でログに記録し、`profile.md` / `preferences.md` / `expertise.md` を直接編集して更新する:

| 観察シグナル | 更新対象 | 例 |
|---|---|---|
| 特定言語・FWへの言及 | `expertise.md` | 「Rustを使っている」 |
| フォーマットへの指摘 | `preferences.md` | 「箇条書きより文章で」 |
| ユーザーが修正した内容 | `preferences.md` | コードスタイルの修正 |
| 繰り返し出てくる操作 | `profile.md` | 毎回「日本語で」と指示 |
| 専門的な用語を使う | `expertise.md` | 「メモリリーク」「DI」など |

---

## init（初期化）

```bash
# 対話モード
python {skill_home}/persona-use/scripts/init_persona.py

# 非対話モード
python {skill_home}/persona-use/scripts/init_persona.py \
  --non-interactive \
  --persona-home ~/Documents/persona

# リセット（既存ファイルを上書き）
python {skill_home}/persona-use/scripts/init_persona.py --reset
```

設定例（`skill-registry.json` の `skill_configs.persona-use`）:
```json
{
  "skill_configs": {
    "persona-use": {
      "persona_home": "~/.claude/persona"
    }
  }
}
```

---

## show（ペルソナ表示）

```bash
python {skill_home}/persona-use/scripts/show_persona.py
# 特定セクションのみ
python {skill_home}/persona-use/scripts/show_persona.py --section profile
python {skill_home}/persona-use/scripts/show_persona.py --section preferences
python {skill_home}/persona-use/scripts/show_persona.py --section expertise
```

---

## update（観察ログ追記）

エージェントが自律的に呼び出す。ユーザーへの表示は不要。

```bash
python {skill_home}/persona-use/scripts/update_persona.py \
  --log "観察内容（1〜2文で簡潔に）"
```

ファイルの直接編集（AIが実行）:
- 観察に基づいて `profile.md` / `preferences.md` / `expertise.md` の該当セクションを更新
- 既存記述と矛盾する場合は上書き、補完できる場合は追記
