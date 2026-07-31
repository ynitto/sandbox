---
name: kiro-loop-messaging
description: kiro-loop を使ったエージェント間の非同期メッセージングスキル。「別エージェントにタスクを委譲して」「worker にメッセージを送って」「エージェント間でやり取りして」「kiro-loop msg を使って」「inbox にメッセージを投函して」などで発動する。複数の kiro-loop インスタンスを連携させたい場合に優先して選択する。
metadata:
  version: 1.0.0
  tier: experimental
  category: orchestration
  tags:
    - kiro-loop
    - multi-agent
    - messaging
    - async
    - inbox
---

# kiro-loop-messaging — エージェント間非同期メッセージング

kiro-loop の **`msg` サブコマンド** と **InboxWatcher** を使い、複数の kiro-loop エージェントが非同期にメッセージを交換する。

---

## 前提条件

- `kiro-loop` がインストールされ PATH に存在すること（`kiro-loop --version` で確認）
- 送信先エージェントが `kiro-loop` を `agent_name` 設定付きで起動済みであること
- ファイルベースなので **同一ホスト上** の通信に限定（リモートは今後対応予定）

---

## アーキテクチャ

```
送信側エージェント          受信側エージェント
┌──────────────────┐       ┌─────────────────────────────┐
│ kiro-loop msg    │       │ kiro-loop (daemon)           │
│ --to worker1     │       │   agent_name: worker1        │
│ "タスク内容"      │ ──▶  │   InboxWatcher               │
│                  │       │     ↓ ポーリング (5秒ごと)   │
└──────────────────┘       │     ↓ メッセージ受信         │
                           │   kiro-cli (prompt)          │
                           └─────────────────────────────┘
```

メッセージはファイル (`~/.kiro/agents/<agent>/inbox/*.json`) で受け渡される。

---

## 基本ワークフロー

### Step 1: 受信側の設定（kiro-loop.yaml）

受信するエージェントの `kiro-loop.yaml` に `agent_name` を設定する。

```yaml
# kiro-loop.yaml
agent_name: worker1           # このエージェントの識別名
inbox_poll_seconds: 5         # 受信ボックス確認間隔（秒）

kiro_options:
  trust_all_tools: true

prompts:
  # 定期プロンプトも通常通り設定可能
  - name: "定期ヘルスチェック"
    prompt: "現在のタスク状況を要約してください"
    interval_minutes: 60
    enabled: true
```

kiro-loop を起動すると InboxWatcher が自動起動する：

```bash
kiro-loop   # agent_name が設定されていれば InboxWatcher も起動
```

### Step 2: メッセージを送信する

```bash
# 基本的な送信
kiro-loop msg --to worker1 "feature_x.py を実装してください"

# 送信元名と件名を付ける
kiro-loop msg --to worker1 --from orchestrator --subject "実装依頼" "feature_x.py を実装してください"

# ファイルで詳細なタスクを渡す（ファイルパスを指定すると内容を読み込む）
kiro-loop msg --to worker1 --from orchestrator task.md

# 会話 ID で複数メッセージをまとめる
kiro-loop msg --to worker1 --correlation-id "conv-001" "Step 1: 設計してください"
kiro-loop msg --to worker1 --correlation-id "conv-001" "Step 2: 実装してください"
```

### Step 3: 受信側での処理

InboxWatcher が受信したメッセージを以下のプロンプト形式で kiro-cli に送信する：

```
[エージェント orchestrator からのメッセージ]
件名: 実装依頼

feature_x.py を実装してください。
仕様: ...

---
返信する場合: kiro-loop msg --to orchestrator --reply-to "<msg_id>" "返答内容"
```

### Step 4: 返信する

受信エージェントが kiro-cli から返信を送る場合：

```bash
# 受信プロンプトに含まれる返信コマンドをそのまま実行
kiro-loop msg --to orchestrator --reply-to "<msg_id>" "実装が完了しました"
```

---

## サブコマンドリファレンス

### `kiro-loop msg` — メッセージ送信

```
kiro-loop msg --to AGENT [--from AGENT] [--subject TEXT] [--reply-to MSG_ID] [--correlation-id ID] BODY
```

| オプション | 短縮 | 説明 |
|-----------|------|------|
| `--to AGENT` | — | 宛先エージェント名（必須） |
| `--from AGENT` | — | 送信元エージェント名（省略可） |
| `--subject TEXT` | `-S` | 件名（短いタスク説明） |
| `--reply-to MSG_ID` | — | 返信元メッセージ ID |
| `--correlation-id ID` | — | 会話スレッド追跡 ID |
| `BODY` | — | 本文テキストまたはファイルパス |

**BODY にファイルパスを指定すると** そのファイルの内容が本文として送信される。

### `kiro-loop agents` — エージェント一覧

```bash
kiro-loop agents
```

```
  orchestrator  (inbox: 0 pending, 12 processed)
  worker1       (inbox: 2 pending, 8 processed)
```

---

## エージェント設計パターン

### パターン 1: オーケストレーター + ワーカー

```
orchestrator (kiro-loop, agent_name: orchestrator)
  └─ msg → worker1: "feature A を実装して"
  └─ msg → worker2: "feature B をテストして"
  ← msg from worker1: "feature A 完了"
  ← msg from worker2: "テスト結果: 全件 PASS"
```

**orchestrator の kiro-loop.yaml:**
```yaml
agent_name: orchestrator
prompts:
  - name: "タスク分配"
    prompt: |
      GitLab の未アサインイシューを確認し、
      worker1 または worker2 に kiro-loop msg で委譲してください。
    interval_minutes: 30
    enabled: true
```

**worker1 の kiro-loop.yaml:**
```yaml
agent_name: worker1
inbox_poll_seconds: 5
```

### パターン 2: パイプライン

```
agent-design → msg → agent-implement → msg → agent-review
```

各エージェントは受信したメッセージを処理して次のエージェントに転送する。

### パターン 3: イベントドリブン（event_hook との組み合わせ）

event_hook で外部イベントを検知し、別エージェントにメッセージを投函する：

```python
# ~/.kiro/hooks/gitlab-hook.py
def check() -> str | None:
    issues = get_new_issues()
    if not issues:
        return None
    issue = issues[0]
    # worker に委譲
    import subprocess
    subprocess.run([
        "kiro-loop", "msg",
        "--to", "worker1",
        "--from", "orchestrator",
        "--subject", f"Issue #{issue['iid']}: {issue['title']}",
        issue["description"] or issue["title"],
    ])
    return None  # orchestrator 自身はスキップ
```

---

## トラブルシューティング

| 問題 | 原因 | 対処 |
|------|------|------|
| メッセージが処理されない | 受信側で `agent_name` 未設定 | `kiro-loop.yaml` に `agent_name` を追加して再起動 |
| inbox に溜まったまま | kiro-cli が処理中（busy）| 完了を待つか、`kiro-loop agents` で状態確認 |
| `kiro-loop msg` コマンドが見つからない | kiro-loop が古いバージョン | 最新版に更新（`msg` サブコマンド追加済み版） |
| 宛先エージェントのフォルダがない | 受信側が未起動 | 受信側で kiro-loop を起動する（inbox ディレクトリは自動作成） |

### デバッグ

```bash
# 受信ボックスを直接確認
ls -la ~/.kiro/agents/worker1/inbox/
cat ~/.kiro/agents/worker1/inbox/<message>.json

# 処理済みメッセージの確認
ls ~/.kiro/agents/worker1/inbox/.processed/

# 全エージェントの状態確認
kiro-loop agents
```

---

## メッセージ JSON 構造（参考）

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

---

## 実装の注意点

- **同一ホスト限定**: メッセージファイルは `~/.kiro/agents/` 以下に置かれるため、同一マシン上のみ動作
- **べき等性**: 同じメッセージが二重処理されないよう、処理後は `.processed/` に移動される
- **セマフォ対応**: `max_concurrent` が設定されている場合、inbox メッセージもセマフォを取得してから送信する
- **保留と再試行**: セッション未準備またはセマフォ上限の場合、ファイルを保持して次のポーリングで再試行する
