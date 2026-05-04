# Phase 3: 並列調査（章ドラフト生成）

**目的**: WBSの各章を調査してドラフトを生成する。推測を隠さず、疑問をQuestion Bankに積み上げる。

## 目次

- [実行モード](#実行モード)
- [3-1. 担当インベントリの読み込み](#3-1-担当インベントリの読み込み)
- [3-2. 章の記述](#3-2-章の記述)
- [3-3. 不確実性マーカーの使い分け](#3-3-不確実性マーカーの使い分け)
- [3-4. Question Bankへの追記](#3-4-question-bankへの追記)
- [3-5. サブエージェントへの指示（Claude Code の場合）](#3-5-サブエージェントへの指示claude-code-の場合)
- [3-6. ドラフトの保存](#3-6-ドラフトの保存)
- [完了条件](#完了条件)

---

## 実行モード

| 環境 | 実行方法 |
|---|---|
| Claude Code | 各章をサブエージェント（Task ツール）に割り当てて並列起動 |
| GitHub Copilot / kiro-cli | 章ごとに逐次実行（wbs.json の順に処理） |

---

## 章ドラフト生成の手順（1章ごと）

### 3-1. 担当インベントリの読み込み

`wbs.json` の `inventory_ids` に対応するファイルを読む。ファイル全体ではなく、関連する範囲を重点的に読む。

### 3-2. 章の記述

以下の構造で章を記述する:

```markdown
---
chapter_id: CH-01
file: 01-overview.md
title: サービス概要
status: draft
confidence_overall: MED
generated_at: 2025-01-01T00:00:00Z
---

# サービス概要

[CONFIDENCE: HIGH] このサービスは〜を提供するREST APIである。[REF: README.md:1-10]

[CONFIDENCE: MED] 主なユースケースは〜と〜である。[REF: src/routers/users.py:1-20]
[ASSUMED: エンドポイント命名からユーザー管理機能が主目的と推測; 根拠: /users, /users/{id} が最も多い]

[CONFIDENCE: LOW] SLAについては〜と思われるが、[ASK SME] 実際の目標値は運用担当者に確認が必要。

## 未解決疑問

- [BLOCKED: Q-003] 主要クライアントシステムの特定ができないため、「利用者」節は空欄
```

### 3-3. 不確実性マーカーの使い分け

**[CONFIDENCE: HIGH]**: コードに明示されている事実
- 関数定義・シグネチャ、設定ファイルの値、コメントで明記された仕様

**[CONFIDENCE: MED]**: 合理的な推測ができる
- 命名規約から読み取れる意図、コメントの文脈、テストケースからの仕様推定

**[CONFIDENCE: LOW]**: 推測に依存する
- コードの意図が不明瞭、コメントがない、過去の意思決定の痕跡がない

**[ASSUMED: 内容; 根拠: 推論]**: 推測の内容と根拠を明示
```
[ASSUMED: リトライ回数3回はAWSのSDKデフォルト値を流用; 根拠: boto3のデフォルト設定と一致]
```

**[ASK SME]**: コードからは答えられない
- 業務ルールの意図、システムの歴史的経緯、外部システムの仕様

**[BLOCKED: Q-XXX]**: critical疑問があり節が書けない
- 疑問IDを必ず記載する。節を空欄のまま残す。

### 3-4. Question Bankへの追記

調査中に生まれた疑問を `.specs-work/questions.json` に追記する:

```json
[
  {
    "id": "Q-002",
    "phase": "investigation",
    "chapter_id": "CH-03",
    "category": "security_compliance",
    "severity": "important",
    "status": "open",
    "question": "JWTトークンの署名アルゴリズムはHS256固定か？RS256への移行計画はあるか？",
    "source_ref": "src/auth/jwt.py:15",
    "related_inventory_ids": ["INV-012"]
  }
]
```

**severity の判断基準**:
- `critical`: 未解決なら `[BLOCKED]` で節が空欄になる
- `important`: 推測で進めるが `[CONFIDENCE: LOW]` + `[ASSUMED]` を付ける
- `nice-to-have`: 仕様書の完成度が上がるが必須ではない

### 3-5. サブエージェントへの指示（Claude Code の場合）

各章に対して以下の指示でサブエージェントを起動する:

```
あなたは仕様書調査エージェントです。以下の章を担当してください。

## ゴールコンテキスト
読者: {goal.json の reader}
目的: {goal.json の purpose}
粒度: {goal.json の granularity}

## 担当章
章ID: CH-XX
ファイル名: NN-slug.md（このファイル名を厳守すること）
タイトル: {タイトル}

## 調査対象インベントリ
{inventory_ids に対応するファイル・行番号の一覧}

## 作業指示
1. 上記ファイルを読み、章の内容を記述する
2. 不確実性マーカーを積極的に使う（推測を隠さない）
3. すべての記述に [REF: ファイル:行番号] を付与する
4. 答えられない疑問は questions.json に追記し、節に [BLOCKED: Q-XXX] を記載する
5. 完璧さより完了を優先する（MED/LOWのマーカーで進める）

## 禁止事項
- 推測を事実として記述する（必ず [ASSUMED] を付ける）
- ファイル名を変更する
- 他の章の範囲に踏み込む
- 根拠のない断定をする
```

### 3-6. ドラフトの保存

生成した章を `.specs-work/drafts/{NN-slug.md}` に保存する。完了したら `wbs.json` の `status` を `done` に更新する。

---

## 完了条件

- [ ] `wbs.json` の全章（`reserved` 以外）が `done` になっている
- [ ] `drafts/` 配下に全章ファイルが存在する
- [ ] `questions.json` に調査中の疑問が記録されている
- [ ] ユーザーに「Phase 3 完了。生成章数: X、登録疑問: Y件。Phase 4（検証）に進みます」と伝える
