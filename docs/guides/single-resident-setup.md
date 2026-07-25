# 常駐一本化セットアップガイド（agent-project serve）

> 参照計画: [`docs/plans/2026-07-24-single-resident-controller-implementation-plan.md`](../plans/2026-07-24-single-resident-controller-implementation-plan.md) W1-11・W1-13。
> 参照設計: [`docs/plans/2026-07-24-single-resident-controller-design.md`](../plans/2026-07-24-single-resident-controller-design.md) §4.2・§4.3・§7。

1 PC = 1 常駐体（`agent-project serve`）。PC ごとに宣言する `agent-project.host.yaml` を
唯一のソースとして、登録したプロジェクトを監督し、amigos の参加・gc を周期実行する。

## 1. インストール

クローンして `tools/agent-tools/install.sh` を 1 回叩く。`agent-project` / `agent-flow` / `agent-amigos` の
3 コマンドが同じ場所（既定 `~/.local/bin`）へ入る。

```bash
git clone <このリポジトリ> && cd <クローン先>
bash tools/agent-tools/install.sh
```

**3 つを別々に入れない。** 同じ共通ライブラリと契約バージョンを共有しているので、片方だけ
古いと状態の読み書きや仕事の受け渡しが噛み合わなくなる。更新も同じコマンドでまとめて行う
（`git pull && bash tools/agent-tools/install.sh`）。

- 入れる先を変える: `bash tools/agent-tools/install.sh --prefix /usr/local/bin`
- 1 本だけ入れ直す: `bash tools/agent-tools/install.sh --only agent-project`
- Windows/WSL 配置の場合はクローンを WSL 側の ext4 に置く（`/mnt/c` は使わない — 設計 §7）。
- python 3.9 以上が要る。git・エージェント CLI（claude / codex 等）・PyYAML の有無は
  インストーラが確認して、足りないものだけ教える。

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
availability:                 # 稼働時間帯（省略 = 常時稼働）
  timezone: Asia/Tokyo
  daily_stop: "23:30"         # この時刻で停止時間帯に入る
  drain_before_sec: 1800      # 停止の 30 分前から新規 claim を止める（走っているものは続行）
  shutdown_grace_sec: 300     # daily_stop からこの猶予を使い切ったら子を止める
```

`projects` が空なら**ワーカーノード（lite）プロファイル**（§4.3）になる。導入は
`agent-project worker init` が対話でこの yaml を生成する（最小手順）。

`availability` の時間の進み方は 3 段:

1. `daily_stop - drain_before_sec` — **drain 開始**。新規 claim を止め、controller を他ノードへ
   譲る。走っているタスクはそのまま完走させる。
2. `daily_stop` — 停止時間帯。
3. `daily_stop + shutdown_grace_sec` — **子を止める**（常駐体が `pause`。SIGTERM → 猶予 →
   SIGKILL）。時間帯が戻れば常駐体がそのまま `resume` する。

停止を決めるのは**常に親（常駐体）**。計画停止は死亡回数に数えないので、毎晩止めても
隔離（quarantine）には達しない。`agent-project status` では休止中と隔離が別に出る。
`availability` の書式が不正なときは**止めたまま**にする（止めたい時間帯に動く方が害が
大きい）ので、`status` のエラー欄を確認する。

`max_concurrent` は **PC 単位**の上限。常駐体が起こした仕事だけでなく、人が直接叩いた
単発実行（`agent-amigos run --once` など）も同じ枠で数える——実行中の手番は
`~/.agents/amigos/turns/*.json` に印が出るので、常駐体がそれを読んで律速する
（置き場は `AGENT_AMIGOS_TURNS_DIR` で変更可）。

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
bash tools/agent-tools/install.sh --service                       # 既定の host.yaml 探索
bash tools/agent-tools/install.sh --service --host-config /path/to/agent-project.host.yaml
```

`~/.config/systemd/user/agent-project.service` を生成し、`systemctl --user enable --now`・
`loginctl enable-linger` まで一括で行う。止まったときの復帰は 3 段構え:

1. `Restart=always` — 落ちたら上げ直す。
2. 内蔵 self-watchdog — 周期処理が固まったら自分で abort し、1 の経路に乗る。
3. `WatchdogSec=90` — 2 すら打てないほど固まった場合に systemd が殺して上げ直す
   （常駐体は `WatchdogSec` の半分の間隔で生存を通知する）。

`Type=notify` なので、全ての周期処理が上がってから起動完了として扱われる
（起動途中の異常を「上がった」と誤認しない）。

手動で構成する場合の unit は上記コマンドが書き出す内容を参照。

### 4b. Windows タスクスケジューラ（WSL VM の keep-alive を兼ねる）

WSL の外側（Windows 側）の操作なので `tools/agent-tools/install.sh` からは実行できない。ログオン時トリガーで
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

- **ノード能力宣言**（node 名義で板へ `nodes/<pc>.json` として「何ができる PC か」を出す）。
  板の請負自体は各ツールの `participate` が委譲側 bus 経由で行っており、ノード直轄の
  能力宣言はそれとは別の設計判断が要る。

## 7. トラブルシュート

- `agent-project status` が `見つかりません` → `agent-project serve` が未起動、または
  1 tick も回っていない（起動直後は supervise tick の初回書き出しを待つ）。
- 子プロジェクトが隔離（`!` マーク）→ 連続クラッシュ。原因を直してから
  `agent-project serve` を再起動する（隔離解除の自動タイムアウトは無い）。
- amigos tick が毎回エラーを積む → `agent-amigos` が PATH / 隣接配置のどちらからも
  解決できていない。`agent-project.host.yaml` の `amigos_bus`/`board` の綴りも確認する。
