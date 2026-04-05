# ポーリングデーモン — 詳細リファレンス

`scripts/gl_poll_daemon.py` / `scripts/gl_poll_setup.py`

---

## 目次

- [アーキテクチャ](#アーキテクチャ)
- [対応エージェント CLI](#対応エージェント-cli)
- [インストール手順](#インストール手順)
- [モック CLI モード](#モック-cli-モード)
- [設定の変更と再起動](#設定の変更と再起動)
- [リポジトリの追加](#リポジトリの追加)
- [セッション開始時の自動設定](#セッション開始時の自動設定)
- [デーモン管理コマンド一覧](#デーモン管理コマンド一覧)
- [プロンプトテンプレート](#プロンプトテンプレート)
- [設定ファイル仕様](#設定ファイル仕様)
- [ポーリング動作仕様](#ポーリング動作仕様)
- [トラブルシューティング](#トラブルシューティング)

---

## アーキテクチャ

```
gl_poll_setup.py  ─── インストール・設定管理（スキル発動時 / セッション開始時）
       │
       ▼
~/.config/gitlab-idd/          ← 設定ディレクトリ（OS 別パスは後述）
  config.json                  ← リポジトリ一覧・CLI 設定・既読 issue 管理
  gl_poll_daemon.py            ← インストール先にコピーされたデーモン本体
  gl_poll_setup.py             ← インストール先にコピーされたセットアップスクリプト
  templates/
    worker-prompt.md           ← ワーカー向けプロンプトテンプレート
    worker-prompt-wsl-kiro.md  ← kiro (WSL2) 向けテンプレート
  mock-prompts/                ← モック CLI モード時のプロンプト保存先
    {timestamp}-issue-{id}.md
  daemon.log                   ← デーモンログ
  worker-issue-{id}.log        ← ワーカーごとの実行ログ
       │
       ▼（OS バックグラウンドサービスとして常駐）
  macOS  : ~/Library/LaunchAgents/com.gitlab-idd-poll.plist
  Linux  : ~/.config/systemd/user/gitlab-idd-poll.service
  Windows: タスクスケジューラ "gitlab-idd-poll"
       │
       ▼（新規 status:open + assignee:any イシュー検出時）
GitLab API でイシュー取得 → テンプレートにデータを埋め込み → CLI を非同期起動
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
| 3 | Kiro (Windows) | `wsl kiro-cli agent "..."` | WSL2 経由、`--cwd` なし |
| 4 | Amazon Q | `q chat "..."` | チャットモード |

- `config.json` の `preferred_cli` フィールドで優先 CLI を固定できる
- kiro on Windows: WSL2 内でリポジトリをクローンするため `--cwd` は渡さない（テンプレート内の clone 手順で対処）

---

## インストール手順

### 前提条件チェック

```bash
# CLI 確認（bash/zsh）
command -v claude >/dev/null 2>&1; if [ $? -eq 0 ]; then claude --version; fi
command -v codex >/dev/null 2>&1; if [ $? -eq 0 ]; then codex --version --version; fi
command -v kiro-cli >/dev/null 2>&1; if [ $? -eq 0 ]; then kiro-cli --version; fi
command -v q >/dev/null 2>&1; if [ $? -eq 0 ]; then q --version; fi

# kiro on Windows (PowerShell)
wsl kiro-cli --version
```

### インストール実行

LLM が **ユーザーの明示的な同意を得てから** 以下を実行する。

```bash
# 通常インストール（CLI が必要）
python scripts/gl_poll_setup.py --install

# CLI なし → モック CLI モードでインストール（ユーザー確認後）
python scripts/gl_poll_setup.py --install --allow-mock-cli

# 実行前に副作用なしで確認
python scripts/gl_poll_setup.py --install --dry-run
```

`--install` の処理:
1. 利用可能な CLI を検出（なければ `--allow-mock-cli` で継続可）
2. カレントリポジトリを設定に追加
3. スクリプト・テンプレートを設定ディレクトリにコピー
4. OS バックグラウンドサービスとして登録
5. `~/.claude/settings.json` に `SessionStart` フックを追加

---

## モック CLI モード

**概要**: GitLab API は実際に呼び出し、CLI は起動せずにプロンプトをファイルに保存するモード。
CLI が未インストールの状態でもデーモンの動作テストができる。

### 有効化 / 無効化

```bash
# モック CLI モードを ON にして再起動
python gl_poll_setup.py --set mock_cli=true

# 通常モードに戻して再起動
python gl_poll_setup.py --set mock_cli=false

# インストール時からモック CLI モードを使う（CLI が見つからない場合）
python gl_poll_setup.py --install --allow-mock-cli
```

### --dry-run との違い

| 動作 | `--dry-run` | `mock_cli=true` |
|------|-------------|-----------------|
| GitLab API 呼び出し | ✅ 実接続 | ✅ 実接続 |
| CLI 起動 | ✗ 起動しない | ✗ 起動しない |
| プロンプト保存先 | `mock-prompts/` | `mock-prompts/` |
| `seen_issues` 更新 | ✗ 更新しない | ✅ 更新する |
| 対象 | 1 回限りのテスト | 常駐運用（CLI インストール待ち等）|

### 保存されたプロンプトの確認

```bash
ls ~/.config/gitlab-idd/mock-prompts/
cat ~/.config/gitlab-idd/mock-prompts/20240101T120000-issue-42.md
```

---

## 設定の変更と再起動

デーモンの設定は `--set` コマンドで変更できる。変更後に自動的にデーモンが再起動される。

```bash
# モック CLI モードを切り替える
python gl_poll_setup.py --set mock_cli=true
python gl_poll_setup.py --set mock_cli=false

# 使用 CLI を固定する
python gl_poll_setup.py --set preferred_cli=claude
python gl_poll_setup.py --set preferred_cli=codex

# ポーリング間隔を変更する
python gl_poll_setup.py --set poll_interval=60

# 設定変更なしで再起動だけ行う
python gl_poll_setup.py --restart
```

### 設定変更のみ（再起動なし）

`config.json` を直接編集した後、`--restart` で反映できる:

```bash
# config.json を直接編集
$EDITOR ~/.config/gitlab-idd/config.json

# デーモンを再起動して設定を反映
python ~/.config/gitlab-idd/gl_poll_setup.py --restart
```

config.json はポーリングサイクルごとに再読み込みされるため、
`preferred_cli` や `poll_interval_seconds` はサービス再起動なしで次のサイクルから有効になる。

---

## リポジトリの追加

```bash
# カレントリポジトリを追加
python scripts/gl_poll_setup.py --add-repo

# ドライラン
python scripts/gl_poll_setup.py --add-repo --dry-run
```

---

## セッション開始時の自動設定

インストール後、`~/.claude/settings.json` の `SessionStart` フックが各セッション開始時に:

1. カレントディレクトリが GitLab リポジトリかどうか確認
2. 未登録なら `config.json` に追加（`seen_issues` は引き継ぐ）
3. デーモンが停止中であれば再起動を試みる

---

## デーモン管理コマンド一覧

```bash
# 状態確認（CLI・リポジトリ・OS サービス・モード）
python gl_poll_setup.py --status

# リポジトリ追加
python gl_poll_setup.py --add-repo

# 設定変更 + 再起動
python gl_poll_setup.py --set KEY=VALUE

# 再起動のみ
python gl_poll_setup.py --restart

# アンインストール（設定ファイルは保持）
python gl_poll_setup.py --uninstall

# 1 回ポーリング（実 GitLab + モック CLI）
python ~/.config/gitlab-idd/gl_poll_daemon.py --dry-run

# 1 回ポーリング（実 GitLab + 実 CLI）
python ~/.config/gitlab-idd/gl_poll_daemon.py --once
```

スタンドアロン実行（インストール済みの場所から）:

```bash
python ~/.config/gitlab-idd/gl_poll_setup.py --status
python ~/.config/gitlab-idd/gl_poll_setup.py --set mock_cli=true
```

---

## プロンプトテンプレート

ワーカーへの指示は `templates/` ディレクトリのMarkdownテンプレートで管理する。
LLM による動的生成は行わず、Python の `string.Template` で変数を置換する。

| テンプレートファイル | 使用場面 |
|---------------------|---------|
| `worker-prompt.md` | claude / codex / amazonq / kiro (非Windows) |
| `worker-prompt-wsl-kiro.md` | kiro on Windows (WSL2経由) |

### 使用可能な変数

| 変数 | 内容 |
|------|------|
| `${issue_id}` | イシュー IID |
| `${issue_title}` | イシュータイトル |
| `${issue_url}` | イシュー URL |
| `${issue_body}` | イシュー本文（description）|
| `${issue_labels}` | ラベル（カンマ区切り）|
| `${host}` | GitLab ホスト名 |
| `${project}` | プロジェクトパス（namespace/repo）|
| `${project_name}` | リポジトリ名のみ |
| `${local_path}` | ローカルリポジトリの絶対パス |
| `${branch_name}` | 推奨ブランチ名（`feature/issue-{id}`）|
| `${remote_url}` | HTTPS クローン URL |
| `${clone_dir}` | WSL 内クローン先（`/tmp/gitlab-idd-work/{project_name}`）|

### テンプレートの優先順位

1. `~/.config/gitlab-idd/templates/` （インストール済み）
2. `.github/skills/gitlab-idd/templates/` （スキルリポジトリ内）
3. スクリプト内蔵のフォールバック文字列

テンプレートファイルを直接編集して指示内容をカスタマイズできる。
デーモンは毎回ファイルを読み込むため、再起動なしで反映される。

---

## 設定ファイル仕様

```json
{
  "poll_interval_seconds": 300,
  "preferred_cli": "claude",
  "mock_cli": false,
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
| `preferred_cli` | string? | 優先 CLI 名。省略時は自動検出順 |
| `mock_cli` | bool? | true でモック CLI モードを有効化 |
| `repos[].host` | string | GitLab ホスト名（例: `gitlab.com`）|
| `repos[].project` | string | プロジェクトパス（例: `namespace/repo`）|
| `repos[].local_path` | string | ローカルリポジトリの絶対パス |
| `repos[].token` | string? | GitLab トークン。省略時は環境変数を使用 |
| `seen_issues` | object | 既読イシュー ID の記録（重複防止）|

ファイル権限: Unix 系では `0600`（所有者のみ読み書き）。

---

## ポーリング動作仕様

| 項目 | 仕様 |
|------|------|
| ポーリング間隔 | デフォルト 300 秒、`config.json` / `--set poll_interval` で変更可 |
| 対象イシュー | `status:open` + `assignee:any` ラベルを持つ `opened` 状態 |
| 重複防止 | `seen_issues` に記録（モック CLI モード時は更新しない）|
| 新規イシュー検出時 | デスクトップ通知 + CLI 非同期起動（またはプロンプトを mock-prompts/ に保存）|
| 設定の動的反映 | 毎サイクル `config.json` を再読み込み。再起動不要 |
| ログ | `{config_dir}/daemon.log` |
| ワーカーログ | `{config_dir}/worker-issue-{id}.log` |

---

## トラブルシューティング

| 症状 | 確認事項 | 対処 |
|------|---------|------|
| デーモンが起動しない | `--status` でサービス状態確認 | `--restart` で再起動 |
| CLI が見つからない | `--status` で利用可能 CLI 確認 | CLI インストール後 `--set mock_cli=false` |
| イシューが検出されない | `--dry-run` で動作確認 | トークン・ラベル設定を確認 |
| 同じイシューが再処理 | `seen_issues` の確認 | `config.json` の `seen_issues` を確認 |
| Windows + kiro が動かない | `wsl kiro-cli --version` 確認 | WSL2 のインストール・kiro-cli の設定 |
| トークンエラー | `GITLAB_TOKEN` 環境変数確認 | `config.json` の `token` に直接設定 |
| テンプレートが見つからない | `ls ~/.config/gitlab-idd/templates/` | `--install` を再実行してコピー |
