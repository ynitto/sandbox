# local-gitlab-stack（案A）

個人 Windows PC の **WSL2 + Docker** に **ローカル GitLab CE** を作業インスタンスとして立て、
issues/MR/notes とコードをローカルで扱い、**コードは上流 GitLab（マスター）と双方向同期**する構成のデプロイ資産。

> 設計正典: [`docs/designs/plan-a-local-gitlab-design.md`](../../docs/designs/plan-a-local-gitlab-design.md)
> 比較の経緯: [`docs/designs/selfhost-forge-comparison.md`](../../docs/designs/selfhost-forge-comparison.md)
> コード同期: [`tools/gitea-sync-bot/`](../gitea-sync-bot/)（`forge: gitlab` 対応済み）

## なぜ案A か
既存の自動化資産（`kiro-flow/executors/gitlab.py` 等）が **GitLab API v4 前提**のため、ローカルも GitLab にすれば
**向け先（ホスト/トークン）を変えるだけで無改修**。issues/MR はローカル管理でき、上流に触るのは同期ボットだけ。

## 収録ファイル
| ファイル | 役割 |
|---|---|
| `docker-compose.yml` | ローカル GitLab CE（省メモリ設定・CI 無効・SSH 2222） |
| `.wslconfig.example` | WSL2 のメモリ/ネットワーク（`%UserProfile%\.wslconfig` へ） |
| `wsl.conf.example` | ディストロ内 `/etc/wsl.conf`（systemd 有効化） |
| `setup-network.ps1` | NAT モード時の LAN 公開（netsh portproxy + Firewall） |
| `backup-gitlab.sh` | gitlab-backup + secrets/config 退避 |
| `sync.config.yaml.example` | 同期ボット設定（working=ローカル / upstream=上流, `forge: gitlab`） |

## セットアップ手順（要約）

1. **WSL2 + Docker**
   - WSL2(Ubuntu 等)に Docker Engine を導入。`wsl.conf.example` を `/etc/wsl.conf` に置き `systemd=true`。
   - `.wslconfig.example` を `%UserProfile%\.wslconfig` に置いてメモリ/ネットワークを設定 → `wsl --shutdown` で反映。
2. **GitLab 起動**
   ```sh
   cd tools/local-gitlab-stack
   docker compose up -d
   docker exec -it gitlab cat /etc/gitlab/initial_root_password   # 初回 root パスワード
   ```
   初期化に数分。`external_url` は `http://gitlab.local`（LAN 名）。
3. **LAN 公開**
   - Windows 11 22H2+ → `.wslconfig` で `networkingMode=mirrored`（`setup-network.ps1` 不要）。
   - それ以外 → 管理者 PowerShell で `./setup-network.ps1` を実行（起動時タスクに登録して毎回貼り直す）。
   - 各 PC の hosts か社内 DNS で `gitlab.local` → ホスト IP。
4. **プロジェクト作成＆シード**
   - ローカル GitLab に上流と同じパスのプロジェクトを空で作成。
   - 同期ボットを `--once` 実行 → allowlist の ref が上流からシードされる。
5. **同期ボット常駐**
   ```sh
   cp sync.config.yaml.example /opt/gitea-sync-bot/sync.config.yaml   # 編集
   export LOCAL_GITLAB_TOKEN=... UPSTREAM_GITLAB_TOKEN=... SYNC_WEBHOOK_SECRET=...
   python3 ../gitea-sync-bot/gitea_sync_bot.py --config /opt/gitea-sync-bot/sync.config.yaml --once   # 動作確認
   python3 ../gitea-sync-bot/gitea_sync_bot.py --config /opt/gitea-sync-bot/sync.config.yaml --serve  # 常駐
   ```
   - ローカル GitLab に **Push webhook** `http://127.0.0.1:9000/`（Secret=`SYNC_WEBHOOK_SECRET`）を登録。
   - 上流→ローカルは webhook が届かないため、ボットが `ls-remote` で低頻度ポーリングして取り込む（自動）。
6. **既存ツールの向け先変更**
   - GitLab 前提資産のホスト/トークンを**ローカル GitLab**（`gitlab.local` + ローカル PAT）へ。issues/MR はローカルで管理。
7. **バックアップ**
   - `backup-gitlab.sh` を日次スケジュール（gitlab-backup + `gitlab-secrets.json` + `gitlab.rb`）。

## 起動時の自動化（タスクスケジューラ例）
「システム起動時」に管理者で:
```
wsl -d Ubuntu -u root -e sh -lc "cd /path/to/tools/local-gitlab-stack && docker compose up -d"
powershell -ExecutionPolicy Bypass -File C:\path\to\setup-network.ps1
```
コンテナは `restart: always` なので Docker 起動後に自動復帰する。

## 可用性の注意（個人PC）
個人PCがスリープ/シャットダウンすると 10名共有の GitLab（issues/MR 含む）が停止する。電源プランでスリープ無効化を推奨。
**コードは上流に ff 同期済み**なので停止中は上流へフォールバックできるが、**issues/MR はローカルのみ**のため参照不可。
恒久運用では**専用の常時稼働機**への移設を推奨（設計書 §6）。
