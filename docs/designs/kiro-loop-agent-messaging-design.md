# kiro-loop エージェント間メッセージング 設計書

> 作成日: 2026-05-23
> 対象ブランチ: `claude/kiro-loop-agent-messaging-oNnAO`
> 関連ファイル: `tools/kiro-loop/kiro-loop.py`, `tools/kiro-loop/kiro-loop.yaml.example`

---

## 1. 概要

kiro-loop を使ってエージェント間の非同期メッセージングを実現する。  
各エージェントは **名前付きの受信ボックス（inbox）** を持ち、他エージェントから投函されたメッセージを kiro-cli へのプロンプトとして処理する。

```
orchestrator                          worker1
┌──────────────┐                    ┌──────────────────┐
│ kiro-loop    │  kiro-loop msg     │ kiro-loop        │
│ (agent_name: │ ──────────────────▶│ InboxWatcher     │
│  orchestrator│  ~/.kiro/agents/   │ (agent_name:     │
│ )            │  worker1/inbox/    │  worker1)        │
└──────────────┘                    │ ↓ kiro-cli       │
        ▲                           │   prompt         │
        │ kiro-loop msg --to orchestrator              │
        └──────────────────────────────────────────────┘
```

---

## 2. 現行アーキテクチャとの差分

| 機能 | 現行 | 本拡張 |
|------|------|--------|
| プロンプト送信 | `kiro-loop send` — tmux セッションへ同期送信 | `kiro-loop msg` — inbox へ非同期投函 |
| 受信 | なし | InboxWatcher スレッドがポーリング |
| ルーティング | tmux セッション名で指定 | エージェント名で指定 |
| 応答待ち | あり（response_timeout） | なし（非同期） |
| 追跡 | なし | `correlation_id` でスレッド追跡可能 |

---

## 3. 新規設定オプション

### 3.1 グローバル設定（`kiro-loop.yaml`）

```yaml
# エージェント識別名。設定するとこのデーモンが受信ボックスを監視する。
agent_name: orchestrator   # ← 新規

# 受信ボックスのポーリング間隔（秒）。デフォルト 5。
inbox_poll_seconds: 5      # ← 新規
```

### 3.2 `agent_name` の命名規則

- 英数字・ハイフン・アンダースコアのみ使用推奨
- `kiro-loop agents` コマンドで登録済み一覧を確認可能
- 未設定の場合は InboxWatcher を起動しない（受信機能なし）

---

## 4. ファイルシステム構造

```
~/.kiro/agents/
├── orchestrator/
│   └── inbox/
│       ├── .processed/            # 処理済みアーカイブ
│       │   └── 1716460000_<uuid>.json
│       └── 1716461000_<uuid>.json  # 未処理メッセージ
└── worker1/
    └── inbox/
        ├── .processed/
        └── 1716460500_<uuid>.json
```

### メッセージ JSON スキーマ

```json
{
  "id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
  "from": "orchestrator",
  "to": "worker1",
  "created_at": 1716460000.0,
  "subject": "feature X の実装依頼",
  "body": "src/feature_x.py を実装してください。\n仕様: ...",
  "reply_to": "orchestrator",
  "correlation_id": "conv-2026-05-23-001",
  "cwd": "/home/user/projects/myapp"
}
```

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `id` | str | ✅ | メッセージ固有 ID（UUIDv4 hex） |
| `from` | str | ✅ | 送信元エージェント名 |
| `to` | str | ✅ | 宛先エージェント名 |
| `created_at` | float | ✅ | 作成日時（Unix timestamp） |
| `subject` | str | — | 件名（短いタスク説明） |
| `body` | str | ✅ | メッセージ本文（kiro-cli へ渡すプロンプトのベース） |
| `reply_to` | str | — | 返信先エージェント名（省略時は `from` と同じ） |
| `correlation_id` | str | — | 会話スレッド追跡 ID |
| `cwd` | str | — | 送信元の作業ディレクトリ |

---

## 5. 新規コンポーネント

### 5.1 `InboxWatcher` クラス

スレッドとして動作し、受信ボックスを定期ポーリングする。

```
InboxWatcher._run_loop (poll_interval 秒ごと)
│
└─ _check_inbox()
   │
   ├─ glob("*.json") → 未処理メッセージ一覧（タイムスタンプ順）
   │
   └─ for each msg_file:
       ├─ parse JSON
       ├─ _try_dispatch(data)
       │   ├─ session_mgr.ensure_session(prompt_id, name)  失敗 → False（保留）
       │   ├─ semaphore.acquire(pane_id)                   失敗 → False（保留）
       │   └─ send prompt to kiro-cli                     成功 → True
       │
       └─ 成功時: inbox/ → .processed/ へ移動
          失敗時: ファイル保持（次のポーリングで再試行）
```

**保留・再試行の仕組み**
- セッション未準備またはセマフォ取得失敗 → ファイルをそのまま保持
- 次のポーリングサイクルで自動再試行
- メッセージは**ファイルが `.processed/` に移動するまで処理済みとみなさない**

### 5.2 `cmd_msg` 関数 / `msg` サブコマンド

```bash
kiro-loop msg --to <agent> [options] <body>
```

| オプション | 説明 |
|-----------|------|
| `--to AGENT` | 宛先エージェント名（必須） |
| `--from AGENT` | 送信元名（省略時: "unknown"） |
| `--subject TEXT` / `-S` | 件名 |
| `--reply-to MSG_ID` | 返信元メッセージ ID |
| `--correlation-id ID` | 会話追跡 ID |
| `body` | 本文テキストまたはファイルパス |

**ファイルパス自動検出**: `body` がファイルとして存在する場合、そのファイル内容を本文として使用する。

### 5.3 `cmd_agents` 関数 / `agents` サブコマンド

```bash
kiro-loop agents
```

`~/.kiro/agents/` 配下のディレクトリを列挙し、各エージェントの inbox 状態を表示する。

```
  orchestrator  (inbox: 0 pending, 12 processed)
  worker1       (inbox: 2 pending, 8 processed)
```

---

## 6. kiro-cli へ渡すプロンプトのテンプレート

受信メッセージは以下の形式で kiro-cli に送信される：

```
[エージェント {from} からのメッセージ]
件名: {subject}

{body}

---
返信する場合: kiro-loop msg --to {from} --reply-to "{id}" "返答内容"
```

---

## 7. 使用例

### 7.1 基本的な送信

```bash
# テキストを直接送信
kiro-loop msg --to worker1 --from orchestrator --subject "実装依頼" "feature_x.py を実装してください"

# ファイルで送信（大きなタスクの場合）
kiro-loop msg --to worker1 --from orchestrator task.md

# 返信
kiro-loop msg --to orchestrator --from worker1 --reply-to "a1b2c3d4..." "実装が完了しました"
```

### 7.2 kiro-loop.yaml 設定例（受信側）

```yaml
agent_name: worker1
inbox_poll_seconds: 5

kiro_options:
  trust_all_tools: true

prompts:
  - name: "定期ヘルスチェック"
    prompt: "現在のタスク状況を要約してください"
    interval_minutes: 60
    enabled: true
```

### 7.3 スキルからの呼び出し

kiro-loop-messaging スキルを使うと、エージェント内から以下のように記述できる：

```bash
# 別エージェントにタスクを委譲
kiro-loop msg --to worker1 --from orchestrator \
  --subject "コードレビュー依頼" \
  "src/main.py のコードレビューをしてください"

# 送信確認
kiro-loop agents
```

---

## 8. kiro-loop 側に足りない機能（実装優先度順）

### P0: 今回の実装対象

| 機能 | 説明 | 実装 |
|------|------|------|
| `InboxWatcher` クラス | 受信ボックスポーリング + dispatch | `kiro-loop.py` に追加 |
| `msg` サブコマンド | 非同期メッセージ投函 CLI | `kiro-loop.py` に追加 |
| `agents` サブコマンド | エージェント一覧表示 | `kiro-loop.py` に追加 |
| `agent_name` 設定 | エージェント識別名 | `kiro-loop.yaml` に追加 |
| `inbox_poll_seconds` 設定 | ポーリング間隔 | `kiro-loop.yaml` に追加 |

### P1: 今後の拡張

| 機能 | 説明 | 理由 |
|------|------|------|
| **メッセージ優先度** | `priority: high/normal/low` フィールド追加 | 緊急メッセージを先に処理 |
| **TTL（有効期限）** | `expires_at` フィールド追加 | 期限切れメッセージを自動破棄 |
| **配送確認** | メッセージ処理後に送信元へ ACK を返す | 送信元が到達確認できる |
| **inbox watch CLI** | `kiro-loop inbox --watch` でリアルタイム監視 | デバッグ支援 |
| **broadcast** | `--to "*"` で全エージェントに同報 | 一斉通知ユースケース |
| **ペイロード添付** | ファイルパス参照の構造化データ | 大きなコンテキストの共有 |

### P2: アーキテクチャ改善

| 機能 | 説明 |
|------|------|
| **名前解決レジストリ** | エージェント名 → 接続先マッピング（リモートエージェント対応） |
| **メッセージストア** | SQLite 等で inbox を永続化・検索可能に |
| **WebSocket/gRPC** | ファイルポーリングからリアルタイム通信へ移行 |

---

## 9. 既存機能との関係

```
kiro-loop send   ─── tmux セッションへ同期送信（既存・変更なし）
                      ↕ 用途の違い
kiro-loop msg    ─── エージェント inbox へ非同期投函（新規）
                      ↓ InboxWatcher が受信して kiro-cli へ dispatch
```

- `send` と `msg` は **相互排他的でなく補完的**
- `send` は即時・同期の操作に、`msg` は非同期・エージェント間通信に使い分ける
- `send` は引き続きデバッグ・手動操作に有用

---

## 10. 実装変更量サマリ

| ファイル | 変更種別 | 追加行数 |
|---------|---------|---------|
| `tools/kiro-loop/kiro-loop.py` | 定数追加 | +1 |
| `tools/kiro-loop/kiro-loop.py` | `InboxWatcher` クラス追加 | +~120 |
| `tools/kiro-loop/kiro-loop.py` | `cmd_msg` 関数追加 | +~60 |
| `tools/kiro-loop/kiro-loop.py` | `cmd_agents` 関数追加 | +~20 |
| `tools/kiro-loop/kiro-loop.py` | `main()` サブコマンド追加 | +~40 |
| `tools/kiro-loop/kiro-loop.py` | `main()` デーモン起動に InboxWatcher 追加 | +~10 |
| `tools/kiro-loop/kiro-loop.yaml.example` | `agent_name`, `inbox_poll_seconds` 追加 | +~10 |
| `.github/skills/kiro-loop-messaging/SKILL.md` | 新規スキル | +~200 |

**既存コードへの変更: 最小限（main() への挿入のみ）**
