# 常駐一本化セットアップガイド（agent-project serve）

> 参照計画: [`docs/plans/2026-07-24-single-resident-controller-implementation-plan.md`](../plans/2026-07-24-single-resident-controller-implementation-plan.md) W1-11・W1-13。
> 参照設計: [`docs/plans/2026-07-24-single-resident-controller-design.md`](../plans/2026-07-24-single-resident-controller-design.md) §4.2・§4.3・§7。

1 PC = 1 常駐体（`agent-project serve`）。PC ごとに宣言する `agent-project.host.yaml` を
唯一のソースとして、登録したプロジェクトを監督し、amigos の参加・gc を周期実行する。

## 1. 前提

- `agent-project`（本ガイド）・`agent-flow`・`agent-amigos` を同じ PATH prefix へ
  インストール済み（各ツールの `install.sh` を実行済み）。
- Windows/WSL 配置の場合はクローンを WSL 側の ext4 に置く（`/mnt/c` は使わない — 設計 §7）。

## 2. host.yaml を書く

`~/.agents/agent-project.host.yaml`（または起動時の cwd）に置く。テンプレートは
[`tools/agent-project/agent-project.host.yaml.example`](../../tools/agent-project/agent-project.host.yaml.example)。

```yaml
schema_version: 1
node_id: pc-a                # 省略時はホスト名を正規化して使う
projects:
  - name: example-project
    root: /home/me/projects/example-project-state
tags: []
agent_cli: []
board: ""                    # 委譲公示板（未使用ならそのまま）
amigos_bus: ""                # amigos 参加 tick の対象バス（未使用ならそのまま — tick 自体を skip）
budget:
  max_concurrent: 0           # 0 = 既定（4）
```

`projects` が空なら**ワーカーノード（lite）プロファイル**（§4.3）になる。導入は
`agent-project worker init` が対話でこの yaml を生成する（最小手順）。

## 3. 起動して確かめる

```bash
agent-project serve
```

フォアグラウンドで起動し、登録プロジェクトを子プロセスとして監督する。別ターミナルで:

```bash
agent-project status        # 心拍・子の生死・隔離状態
agent-project status --json # 機械可読
```

Ctrl-C で graceful 停止（子の graceful shutdown → プロセス終了）。

## 4. 常駐化（PC 起動時に上がる・死んだら上げ直される）

要件はこの 2 つだけ（設計 §7）。実現方式はどちらか 1 つを選ぶ——**両方構成しない**。

選んだ方式は host.yaml に宣言する。doctor が検査できるのは systemd 側だけなので、
4b を選んだ PC では宣言しないと「常駐化が未構成」の誤警告が出続ける:

```yaml
residency: systemd        # 4a を選んだ（既定 auto も systemd がある環境では同じ扱い）
# residency: windows-task # 4b を選んだ（doctor は検査せず、人が schtasks で確認する）
# residency: none         # 常駐化しない（手動起動のみ）
```

### 4a. systemd user unit（WSL / Linux）

```bash
bash install.sh --service                       # 既定の host.yaml 探索
bash install.sh --service --host-config /path/to/agent-project.host.yaml
```

`~/.config/systemd/user/agent-project.service` を生成し、`systemctl --user enable --now`・
`loginctl enable-linger` まで一括で行う。`Restart=always` が死んだら上げ直す側を担い、
ハングは常駐体内蔵の self-watchdog が自ら abort して同じ経路に乗る（`Type=notify` +
`WatchdogSec` による外部監視の二重化は今回のスコープでは見送った — 4.1 節参照）。

手動で構成する場合の unit は上記コマンドが書き出す内容を参照。要点は
`ExecStart=agent-project serve` と `Restart=always` の 2 行。

### 4b. Windows タスクスケジューラ（WSL VM の keep-alive を兼ねる）

WSL の外側（Windows 側）の操作なので `install.sh` からは実行できない。ログオン時トリガーで
次を実行するタスクを手動登録する（PowerShell 管理者権限）:

```powershell
$action = New-ScheduledTaskAction -Execute "wsl.exe" `
  -Argument "-d <distro> -- agent-project serve"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "agent-project" `
  -Action $action -Trigger $trigger -RunLevel Highest
```

`agent-project serve` プロセス自体が WSL VM を生かし続けるため、これ 1 つで常駐起動と
WSL keep-alive を兼ねる（設計 §7）。タスクが落ちたときの再起動ループは
タスクスケジューラの「失敗時の再起動」設定（間隔・回数）を有効にする。

`.wslconfig` の `vmIdleTimeout` 延長は保険として併用してよいが、上記タスクが動いている限り
VM は起動し続けるため必須ではない（要検証と設計に明記— dashboard の UNC アクセス自体が
起動を維持するかは未検証）。

### 4c. どちらを選んだか確認する

```bash
agent-project doctor
```

systemd 環境（かつ `residency` が `windows-task`/`none` でない）なら unit の有無・有効化状態を
検査する（`常駐化が未構成` / `常駐 unit が未有効化`）。

**Windows タスクスケジューラ側は WSL の外なので doctor からは検査できない。** したがって
「両方構成してしまった」という二重構成も doctor では検出できない——1 つだけ選ぶのは人の責任。
4b を選んだ場合は `schtasks /query /tn agent-project` で人が確認する。

## 5. 周期表（コード定数・yaml では変えない）

| tick | 周期 | 内容 |
|---|---|---|
| supervise | 5s | 子の生死監視・ワーカー枠の消化・status 書き出し |
| amigos | 5s | `agent-amigos participate`（claim・心拍・板巡回）→ 担当ロールをワーカーへ投入 |
| gc | 10min | 登録プロジェクトごとに `agent-project gc`（agent-flow バスの掃除） |

`pace`（プロジェクトループの act 律速）以外は yaml で変えられない（設計 §2 原則5
「設定より規約」）。

## 6. 現時点で未実装のもの（意図的な見送り）

以下は設計上は P1 の範囲だが、実装を急ぐと二重実行・不整合のリスクがあるため見送った。
利用には影響しない（未構成のまま no-op）:

- **板の請負 tick**（node 名義での `nodes/<pc>.json` 能力宣言・workload=flow/amigos への
  入札・ワーカー経由の実行）。既存の flow/amigos の板参加はいずれも「委譲側の bus」を
  前提にしており、ノード直轄の契約側実行はまだ設計が固まっていない。
- **systemd `Type=notify` + `WatchdogSec`** の sd_notify 連携（内蔵 self-watchdog による
  自己 abort が主経路のため、無くても設計の 2 要件は満たされる）。
- **旧経路の削除**（`agent-flow daemon`/`submit`、`agent-amigos serve`/`hub`、
  `agent-project` の `instances`/`start`/`stop`/`restart`）。設計は P1 で削除する計画だが、
  常駐体側の置き換え（今回の `serve`/`status`/`worker`/amigos 参加 tick/gc tick）が
  実地で安定してから、テスト資産ごと計画的に削るべき規模の変更のため別作業とする。

## 7. トラブルシュート

- `agent-project status` が `見つかりません` → `agent-project serve` が未起動、または
  1 tick も回っていない（起動直後は supervise tick の初回書き出しを待つ）。
- 子プロジェクトが隔離（`!` マーク）→ 連続クラッシュ。原因を直してから
  `agent-project serve` を再起動する（隔離解除の自動タイムアウトは無い）。
- amigos tick が毎回エラーを積む → `agent-amigos` が PATH / 隣接配置のどちらからも
  解決できていない。`agent-project.host.yaml` の `amigos_bus`/`board` の綴りも確認する。
