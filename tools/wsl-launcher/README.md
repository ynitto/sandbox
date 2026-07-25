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
      "wslPath": "/home/user",                 // ← cwd は問わない（宣言は host.yaml が持つ）
      "command": "agent-project serve",        // ← ここで常駐体を起動
      "distro": "Ubuntu",
      "keepOpen": true,
      "enabled": true
    }
  ]
}
```

## なぜ `agent-project serve` なのか

- **PC に 1 本**: `serve` は `agent-project.host.yaml` に宣言したプロジェクトを**まとめて**
  子として起動・監視する常駐体。プロジェクトごとにランチャの行を増やす必要はなく、
  持つプロジェクトを変えるときは host.yaml を書き換えるだけ。
- **cwd に依存しない**: 宣言が絶対パスなので `wslPath` はどこでもよい（プロジェクトルートに
  合わせる必要はない）。
- **落ちても上がる**: 子がクラッシュすれば常駐体が再起動し、繰り返し落ちるものだけを切り離す。
  常駐体自身の再起動は起動系の担当——**systemd がある環境なら
  `bash tools/install.sh --service` の方が確実**（`Restart=always` + `WatchdogSec` +
  `loginctl enable-linger`）。このランチャは Windows タスクスケジューラ方式の代わりに使う。
  二重構成しないこと（[セットアップガイド](../../docs/guides/single-resident-setup.md) §4）。
- **孤児 run の回収**: シャットダウンで消えた run は次の起動で同一 run-id のまま reclaim され、
  確定済みの成果を活かして続きから走る。

## 関連

- `tools/terminal-launcher/` — 同様のログオン時自動起動（PowerShell 版）。
- [`docs/guides/single-resident-setup.md`](../../docs/guides/single-resident-setup.md) — 常駐体の
  構成手順（host.yaml の書き方・常駐化 2 方式の選択）と dashboard 監視の組み方。
