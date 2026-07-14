# wsl-launcher

Windows のログオン時に、**WSL の所定 cwd（プロジェクトルート）で kiro-project を常駐起動する**ためのランチャ。
メンテナンスで PC を毎日シャットダウン→起動する運用で、起動のたびに自動で agent-project／agent-flow を
立ち上げ直す配線を担う。

## 何をするか

1. `setup.py` のウィザードが、ログオン時トリガの自動起動を登録する（次のいずれか）:
   - Task Scheduler の `LogonTrigger` タスク（`WorkingDirectory` 固定・`MultipleInstancesPolicy=IgnoreNew`）
   - HKCU `...\CurrentVersion\Run` キー
   - Windows Terminal の `startOnUserLogin` + `startupActions`
2. ログオン時に `launch.pyw`（`pythonw` でコンソール非表示）が動き、**WSL の起動完了を待って**から、
   `config.json` の各ターミナルを **`wslPath`（＝所定 cwd）で `command` を実行**して開く
   （`wsl.exe --cd <wslPath> -- bash -c '<command>'`）。

つまり「起動 → WSL 起動待ち → 所定 cwd で kiro-project を起動」までを毎回自動で行う。

## セットアップ

```bat
python setup.py            :: ウィザード（自動起動の登録 + config.json 編集）
python setup.py --status   :: 登録状況の確認
python setup.py --unregister :: 自動起動の解除
```

`config.json` は `config.example.json` をコピーして作れる。`wslPath` を自分のプロジェクトルート
（WSL パス）に、`distro` をディストロ名に書き換えるだけでよい。

```jsonc
{
  "terminals": [
    {
      "name": "kiro-project (demo)",
      "wslPath": "/home/user/projects/demo",  // ← プロジェクトルート（charter.md / bus のある場所）
      "command": "agent-project start",        // ← ここで常駐を起動
      "distro": "Ubuntu",
      "keepOpen": true,
      "enabled": true
    }
  ]
}
```

## なぜ `agent-project start` を推奨するか（毎起動で叩いても安全）

- **二重起動しない**: `agent-project start` は同一 `root+host` の重複監視を既定で拒否する
  （`--force`/`restart` で明示上書き）。前回インスタンスの pid が起動で消えていれば新規 start が通る＝
  「毎起動でランチャが叩く」運用に整合する。`run --watch` を直接叩くより安全。
- **flow daemon も冪等**: `manage_flow_daemon` 構成なら agent-project が agent-flow daemon を確保する。
  daemon は realpath 正規化した flock のシングルトンで、既に稼働していれば二重起動せず終了する。
  daemon を別ターミナルで明示常駐したい場合は `config.example.json` の `_terminals_alt` を参照
  （`agent-flow daemon` と `agent-project start` の2件構成）。
- **所定 cwd で状態を再発見**: kiro-project は `root=cwd` をアンカーに charter/backlog・`bus`・state_git を
  発見して継続する。シャットダウンで消えた孤児 run は次の daemon 起動時に同一 run-id で reclaim され、
  確定済みの成果を活かして続きから走る。**`wslPath` を必ずプロジェクトルートに固定すること**
  （cwd を取り違えると別バス扱いになる）。

## 関連

- `tools/terminal-launcher/` — 同様のログオン時自動起動（PowerShell 版）。
- `tools/agent-project/agent-project.state-git.yaml.example` — 常駐手順（WSL・プロジェクトルートで
  `agent-flow daemon &` / `agent-project start`）と dashboard 監視の組み方。
