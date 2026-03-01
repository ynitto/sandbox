# auto-update 詳細

セッション開始時やユーザーの指示で、リポジトリの更新を自動チェックする機能。デフォルトは無効。

→ 実装: `scripts/auto_update.py` — `run_auto_update()`, `check_updates()`, `configure_auto_update()`

## 動作モード

| モード | notify_only | 動作 |
|--------|------------|------|
| 通知のみ（デフォルト） | true | 更新があれば一覧表示。pull はユーザーに委ねる |
| 自動pull | false | 更新検出後に `pull_skills(interactive=False)` を自動実行 |

## トリガー

| トリガー | 説明 |
|---------|------|
| セッション開始時 | `~/.copilot/copilot-instructions.md` の指示で `run_auto_update()` を呼ぶ。前回チェックから `interval_hours` 以上経過していればチェックを実行 |
| ユーザー直接 | 「更新チェックして」で `--force` 付きの即座チェック |
| 設定変更 | 「自動更新を有効化して」「間隔を12時間にして」等 |

## 設定操作

```
「自動更新を有効化して」
→ python auto_update.py configure --enable

「自動更新を無効化して」
→ python auto_update.py configure --disable

「チェック間隔を12時間にして」
→ python auto_update.py configure --interval 12

「自動pullも有効にして」
→ python auto_update.py configure --auto-pull

「通知だけにして」
→ python auto_update.py configure --notify-only

「自動更新の設定を見せて」
→ python auto_update.py status
```

## チェック操作

```
「更新チェックして」「スキルの更新を確認して」
→ python auto_update.py check --force
```
