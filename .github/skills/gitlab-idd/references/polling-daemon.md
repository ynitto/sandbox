# ポーリングデーモン — 詳細リファレンス

`scripts/gl_poll_daemon.py` / `scripts/gl_poll_setup.py`

---

## 目次

- [アーキテクチャ](#アーキテクチャ)
- [対応エージェント CLI](#対応エージェント-cli)
- [インストール手順](#インストール手順)
- [リポジトリの追加](#リポジトリの追加)
- [セッション開始時の自動設定](#セッション開始時の自動設定)
- [デーモン管理コマンド](#デーモン管理コマンド)
- [ドライランモード](#ドライランモード)
- [設定ファイル仕様](#設定ファイル仕様)
- [ポーリング動作仕様](#ポーリング動作仕様)
- [トラブルシューティング](#トラブルシューティング)

---

## アーキテクチャ

```
gl_poll_setup.py  ─── インストール・設定管理（スキル発動時 / セッション開始時）
       │
       ▼
~/.config/gitlab-idd/        ← 設定ディレクトリ（OS 別パスは後述）
  config.json                ← リポジトリ一覧・既読 issue 管理・preferred_cli
  gl_poll_daemon.py          ← インストール先にコピーされたデーモン本体
  gl_poll_setup.py           ← インストール先にコピーされたセットアップスクリプト
  daemon.log                 ← デーモンログ
  worker-issue-{id}.log      ← ワーカーごとの実行ログ
       │
       ▼（OS バックグラウンドサービスとして常駐）
  macOS  : ~/Library/LaunchAgents/com.gitlab-idd-poll.plist
  Linux  : ~/.config/systemd/user/gitlab-idd-poll.service
  Windows: タスクスケジューラ "gitlab-idd-poll"
       │
       ▼（新規 status:open + assignee:any イシュー検出時）
エージェント CLI -p "ワーカーとして … イシュー #N を実行"  ←  非同期起動
```

### 設定ディレクトリの場所

| OS      | パス |
|---------|------|
| Linux   | `~/.config/gitlab-idd/` (または `$XDG_CONFIG_HOME/gitlab-idd/`) |
| macOS   | `~/Library/Application Support/gitlab-idd/` |
| Windows | `%APPDATA%\gitlab-idd\` |

---

## 対応エージェント CLI

デーモンはイシュー検出時に以下のいずれかを非同期起動する。
利用可能な CLI を自動検出し、優先順位の高いものを使用する。

| 優先 | CLI | 起動コマンド | 備考 |
|------|-----|------------|------|
| 1 | Claude Code | `claude -p "..."` | 非対話プリントモード |
| 2 | OpenAI Codex CLI | `codex -q "..."` | クワイエットモード |
| 3 | Kiro | `kiro-cli agent "..."` | エージェントタスクモード |
| 3 | Kiro (Windows) | `wsl kiro-cli agent --cwd /mnt/c/... "..."` | WSL2 経由 |
| 4 | Amazon Q | `q chat "..."` | チャットモード |

`config.json` の `preferred_cli` フィールドで優先 CLI を固定できる。

---

## インストール手順

### 前提条件チェック

```bash
# CLI 確認（bash/zsh）
command -v claude  && claude    --version
command -v codex   && codex     --version
command -v kiro-cli && kiro-cli --version
command -v q       && q         --version

# kiro on Windows (PowerShell)
wsl kiro-cli --version
```

### インストール実行

インストールは LLM が **ユーザーの明示的な同意を得てから**以下を実行する。

```bash
# スキルリポジトリのルートから実行
python scripts/gl_poll_setup.py --install

# または gl_poll_setup.py を直接指定（スタンドアロン実行）
python .github/skills/gitlab-idd/scripts/gl_poll_setup.py --install
```

インストール処理の内容:

1. 利用可能なエージェント CLI を検出（見つからない場合は中止）
2. カレントリポジトリを設定に追加（`config.json`）
3. `gl_poll_daemon.py` と `gl_poll_setup.py` を設定ディレクトリにコピー
4. OS バックグラウンドサービスとして登録（ログオン時自動起動）
5. `~/.claude/settings.json` に `SessionStart` フックを追加

### ドライラン確認（推奨）

実際のインストール前に何が行われるか確認する:

```bash
python scripts/gl_poll_setup.py --install --dry-run
```

---

## リポジトリの追加

デーモンインストール後、別のリポジトリからスキルを実行した際に追加する。
スキル実行時にカレントリポジトリが自動的に追加されるが、手動で追加もできる。

```bash
# カレントリポジトリを追加（サービス再起動なし）
python scripts/gl_poll_setup.py --add-repo

# ドライラン
python scripts/gl_poll_setup.py --add-repo --dry-run
```

ポーリング対象リポジトリは `config.json` の `repos` 配列に蓄積される。
すべてのリポジトリに対して同一のポーリング間隔が適用される。

---

## セッション開始時の自動設定

インストール完了後、`~/.claude/settings.json` に `SessionStart` フックが追加される。
これにより、新しいセッションが開始されるたびに:

1. カレントディレクトリが GitLab リポジトリかどうか確認
2. 未登録のリポジトリであれば `config.json` に追加
3. デーモンが停止中であれば再起動を試みる

フック設定の確認:

```bash
# ~/.claude/settings.json の SessionStart フックを確認
python -c "
import json, pathlib
s = json.loads(pathlib.Path.home().joinpath('.claude/settings.json').read_text())
print(json.dumps(s.get('hooks', {}).get('SessionStart', []), indent=2, ensure_ascii=False))
"
```

---

## デーモン管理コマンド

```bash
# 状態確認（利用可能 CLI・登録リポジトリ・OS サービス状態）
python scripts/gl_poll_setup.py --status

# カレントリポジトリを追加
python scripts/gl_poll_setup.py --add-repo

# アンインストール（設定ファイルは保持）
python scripts/gl_poll_setup.py --uninstall

# インストール済みデーモンを手動で 1 回実行（デバッグ用）
python ~/.config/gitlab-idd/gl_poll_daemon.py --once

# ドライランで 1 回ポーリング（GitLab への実接続なし）
python ~/.config/gitlab-idd/gl_poll_daemon.py --dry-run
```

### スタンドアロン実行

`gl_poll_setup.py` はインストール後もスタンドアロンで実行できる:

```bash
# インストール済みの場所から直接実行
python ~/.config/gitlab-idd/gl_poll_setup.py --status
python ~/.config/gitlab-idd/gl_poll_setup.py --add-repo
```

---

## ドライランモード

`--dry-run` フラグで副作用なしに動作確認ができる。

```bash
# セットアップのドライラン
python scripts/gl_poll_setup.py --install --dry-run

# デーモンのドライラン（モックイシューを使ってポーリング処理をシミュレート）
python scripts/gl_poll_daemon.py --dry-run

# 組み合わせ（1 回だけモックポーリング）
python scripts/gl_poll_daemon.py --dry-run --once
```

ドライランでは:

- GitLab API を呼ばずモックイシュー（`iid: 9999`）を使用
- エージェント CLI を起動せず、起動予定コマンドをログ出力
- `config.json` を書き換えない
- OS サービス登録・SessionStart フック設定を行わない

---

## 設定ファイル仕様

`config.json` のスキーマ:

```json
{
  "poll_interval_seconds": 300,
  "preferred_cli": "claude",
  "repos": [
    {
      "host": "gitlab.com",
      "project": "namespace/repo",
      "local_path": "/home/user/myproject",
      "token": "glpat-xxxxxxxxxxxx"
    }
  ],
  "seen_issues": {
    "gitlab.com|namespace/repo": [42, 43, 100]
  }
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `poll_interval_seconds` | int | ポーリング間隔（秒）。デフォルト: 300 |
| `preferred_cli` | string? | 優先エージェント CLI 名。省略時は自動検出 |
| `repos[].host` | string | GitLab ホスト名（例: `gitlab.com`）|
| `repos[].project` | string | プロジェクトパス（例: `namespace/repo`）|
| `repos[].local_path` | string | ローカルリポジトリの絶対パス |
| `repos[].token` | string? | GitLab トークン。省略時は環境変数を使用 |
| `seen_issues` | object | 既読イシュー ID の記録（重複防止） |

**セキュリティ**: `config.json` のファイル権限は Unix 系で `0600`（所有者のみ読み書き）。

---

## ポーリング動作仕様

| 項目 | 仕様 |
|------|------|
| ポーリング間隔 | デフォルト 300 秒（5 分）、`config.json` で変更可 |
| 対象イシュー | `status:open` + `assignee:any` ラベルを持つ `opened` 状態イシュー |
| 重複防止 | `seen_issues` に記録し同一イシューを二重処理しない |
| 新規イシュー検出時 | デスクトップ通知 + エージェント CLI を非同期起動 |
| 複数リポジトリ | 一定間隔で全リポジトリを順番にポーリング |
| 設定の動的反映 | 毎サイクル `config.json` を再読み込み。再起動不要 |
| ログ | `{config_dir}/daemon.log` |
| ワーカーログ | `{config_dir}/worker-issue-{id}.log` |

### デスクトップ通知

| OS | 方法 |
|----|------|
| macOS | `osascript` による通知センター通知 |
| Linux | `notify-send`（インストール済みの場合）|
| Windows | PowerShell の `NotifyIcon` バルーンチップ |

通知は best-effort。失敗しても処理は継続する。

---

## トラブルシューティング

| 症状 | 確認事項 | 対処 |
|------|---------|------|
| デーモンが起動しない | `--status` でサービス状態確認 | OS サービスを再起動 |
| CLI が見つからないエラー | `--status` で利用可能 CLI を確認 | CLI をインストール後 `--install` |
| イシューが検出されない | `--dry-run` で動作確認 | トークン・ラベル設定を確認 |
| 同じイシューが再処理される | `seen_issues` の確認 | `config.json` を確認 |
| Windows + kiro が動かない | `wsl kiro-cli --version` の確認 | WSL2 のインストール・kiro-cli の設定 |
| トークンエラー | `GITLAB_TOKEN` 環境変数の確認 | `config.json` の `token` フィールドに直接設定 |
