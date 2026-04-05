# Kiro 履歴ガイド

Kiro IDE（Windows）および Kiro CLI（WSL/Linux）のチャット履歴の構造と取得方法の詳細。

---

## Kiro CLI のストレージ

### 保存場所

| 環境 | SQLite DB パス |
|---|---|
| Linux / WSL | `~/.kiro/store.db`（優先）、`~/.kiro/sessions.db`、`~/.kiro/db/sessions.db` |

Kiro CLI はセッションを **ディレクトリごと** に SQLite DB へ自動保存する（`/chat save` で任意の JSON にエクスポート可能）。

### SQLite スキーマ

公式の完全スキーマは未公開のため、スクリプトは以下の順でテーブルを探索する:

| 優先順 | テーブル名 | 備考 |
|---|---|---|
| 1 | `sessions` | 最も一般的 |
| 2 | `chat_sessions` | 別名パターン |
| 3 | `conversations` | 旧バージョン |

各行に期待されるカラム（大文字小文字不問）:

```
id               – セッション UUID
directory        – セッション開始ディレクトリ（project_path / workspace で代替）
messages         – JSON 配列（conversation / content / history でも代替）
created_at       – 作成タイムスタンプ（秒 or ミリ秒）
updated_at       – 更新タイムスタンプ
```

### messages カラムの形式

```json
[
  {"role": "user",      "content": "テストを追加してください", "timestamp": 1700000000},
  {"role": "assistant", "content": "...", "timestamp": 1700000001}
]
```

`role` が `user` または `human` のエントリのみ抽出する。`content` がブロック配列形式の場合も対応済み。

### 手動エクスポート（JSON）

チャットセッション内で以下のコマンドを実行してファイルに保存できる:

```bash
/chat save ./my-session.json
```

保存されたファイルは `--source` オプションなしに直接解析できる（標準 JSON 形式）。

---

## Kiro IDE のストレージ（Windows）

Kiro IDE は VSCode フォークのため、VSCode Copilot と同様のディレクトリ構造を持つ。

### 保存場所

| OS | chatSessions パス |
|---|---|
| Windows | `%APPDATA%\Kiro\User\workspaceStorage\*\chatSessions\` |
| macOS | `~/Library/Application Support/Kiro/User/workspaceStorage/*/chatSessions/` |
| Linux | `~/.config/Kiro/User/workspaceStorage/*/chatSessions/` |

**WSL からのアクセス**: WSL 環境では、スクリプトが `wslpath` を使って Windows の `%APPDATA%\Kiro\` を自動検出する:

```
/mnt/c/Users/<username>/AppData/Roaming/Kiro/User/workspaceStorage/*/chatSessions/
```

### データ形式

VSCode Copilot と同一形式（`chatSessions/` → 新形式、`state.vscdb` → 旧形式フォールバック）:

```json
{
  "requests": [
    {
      "message": {"text": "テストを追加してください"},
      "timestamp": 1700000000000
    }
  ]
}
```

---

## スクリプトのオプション早見表

```bash
# Kiro CLI（WSL / Linux）- 全セッション
python extract-copilot-history.py --source kiro-cli --noise-filter

# Kiro CLI - 過去30日、特定プロジェクトのみ
python extract-copilot-history.py --source kiro-cli --days 30 --workspace "my-project"

# Kiro CLI - DB パスを明示指定
python extract-copilot-history.py --source kiro-cli --kiro-db ~/.kiro/store.db

# Kiro IDE（Windows / WSL）
python extract-copilot-history.py --source kiro-ide --noise-filter

# Kiro CLI + IDE 両方
python extract-copilot-history.py --source kiro --noise-filter

# 全ソース（Copilot + Claude Code + Kiro 全て）
python extract-copilot-history.py --source auto --noise-filter
```

---

## WSL 固有の注意事項

- Kiro CLI は WSL の Linux 側（`~/.kiro/`）に DB を保存するため、WSL から直接 SQLite で読める
- Kiro IDE は Windows 側（`%APPDATA%\Kiro\`）に保存する。WSL からアクセスする場合は `/mnt/c/` 経由
- `wslpath` コマンドが利用できない場合は `--storage /mnt/c/Users/<username>/AppData/Roaming/Kiro/User/workspaceStorage` で手動指定する

```bash
# WSL で Kiro IDE を手動パス指定する場合
python extract-copilot-history.py \
  --source kiro-ide \
  --storage "/mnt/c/Users/yourname/AppData/Roaming/Kiro/User/workspaceStorage" \
  --noise-filter
```
