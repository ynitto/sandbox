# obsidian-kiro-sender

Obsidian から WSL 上の Kiro (tmux セッション) にノートを送信するプラグイン。

## 前提条件

- Windows + WSL2 (Ubuntu 等)
- WSL 内に tmux がインストールされていること (`sudo apt install tmux`)
- WSL 内で Kiro が tmux セッションとして起動済みであること

## ビルド

```bash
cd tools/obsidian-kiro-sender
npm install
npm run build
# → main.js が生成される
```

## Obsidian へのインストール（手動）

1. Vault の `.obsidian/plugins/` 配下に `obsidian-kiro-sender/` ディレクトリを作成
2. 以下の 2 ファイルをコピー:
   - `main.js`
   - `manifest.json`
3. Obsidian > 設定 > コミュニティプラグイン > インストール済みのプラグイン → `Kiro Sender` を有効化

## 設定

Obsidian > 設定 > Kiro Sender から以下を設定する:

| 設定項目 | 説明 | デフォルト |
|----------|------|----------|
| tmux ターゲット | Kiro が動いている tmux のターゲット | `kiro:0` |
| WSL ディストリビューション | wsl コマンドに渡すディストリビューション名（空欄 = デフォルト） | （空欄） |
| ファイル参照プレフィックス | Kiro に送信するテキストの先頭に付ける文字列 | `@` |

### tmux ターゲットの調べ方

WSL 内で以下を実行してセッション一覧を確認:

```bash
tmux list-sessions
tmux list-panes -a
```

例えば出力が `kiro:0:0` なら、ターゲットは `kiro:0` と指定する。

## 使い方

### コマンドパレット

`Ctrl+P` → `Send current note to Kiro` を実行

### 右クリックメニュー

ファイルエクスプローラーでファイルを右クリック → `Kiro に送信`

## 動作フロー

```
Obsidian (Windows)
  ↓ ノートの Windows 絶対パスを取得
  ↓ /mnt/c/... の WSL パスへ変換
  ↓ execSync("wsl tmux send-keys -t kiro:0 '@/mnt/c/...' Enter")
WSL tmux
  ↓ Kiro セッションに "@/mnt/c/path/to/note.md" をキー送信
Kiro
  ↓ @ファイル参照を解釈してノートの内容を読み込む
  → 指示を実行
```

## ファイル参照プレフィックスのカスタマイズ例

| シナリオ | プレフィックス設定 |
|----------|----------------|
| ファイル参照だけ送る | `@` |
| 指示付きで送る | `以下の指示を実行してください: @` |
| shogun inbox 形式 | （別途 inbox_watcher.sh との連携が必要） |
