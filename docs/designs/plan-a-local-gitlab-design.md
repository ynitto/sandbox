# 案A — ローカル GitLab CE（WSL2+Docker）設計・運用書

> 最終更新: 2026-07-08 ／ 関連: [`selfhost-forge-comparison.md`](selfhost-forge-comparison.md)（案の比較・案A採用）,
> [`tools/local-gitlab-stack/`](../../tools/local-gitlab-stack/)（デプロイ資産）,
> [`tools/gitea-sync-bot/`](../../tools/gitea-sync-bot/)（コード同期ボット・GitLab↔GitLab 対応済み）
>
> 本書は**案A の設計正典**。個人 Windows PC の WSL2+Docker にローカル GitLab CE を作業インスタンスとして立て、
> issues/MR/notes とコードをローカルで扱い、**コードは上流 GitLab（マスター）と双方向同期**する。
> **既存の GitLab 前提資産は「向け先を上流→ローカル GitLab に変える」だけで無改修**で動く。

## 0. 採用の前提（比較資料で確定）

- 上流 GitLab がマスター。目的は上流アクセス負荷の削減。**CI 不要**。
- 既存資産（`agent-flow/executors/gitlab.py` 等）が **GitLab API v4 前提** → ローカルも GitLab にすれば無改修。
- ホストは個人 Windows PC（可用性は弱点。運用で緩和する。§6）。

## 1. 全体アーキテクチャ

```
[開発者 ×10] ──LAN──▶ [ローカル GitLab CE  (WSL2+Docker / 個人PC)] ◀─ gitea-sync-bot ─▶ [上流 GitLab（マスター）]
   clone/push               ├ issues / MR / notes … ここで管理（ローカル）        ff調停(allowlist)    コード正本
   issues/MR/notes          └ コード … 上流と双方向 ff 同期
   既存ツール ─────────────▶ API v4（向け先をローカル GitLab に変更するだけ・無改修）
```

- **issues/MR/notes はローカル GitLab に置く**（＝当初要望どおりローカル管理）。上流へは同期しない。
- **コードは上流をマスター**として、`gitea-sync-bot` が allowlist の ref を **fast-forward 限定**で双方向同期（設計は
  [`gitea-gitlab-sync-design.md`](gitea-gitlab-sync-design.md) §3 と同一。remote が両方 GitLab になるだけ）。
- 上流に触るのは同期ボットだけ → **上流アクセス負荷は最小**。

## 2. コンポーネントと配置

| コンポーネント | 実体 | 置き場 |
|---|---|---|
| ローカル GitLab CE | `gitlab/gitlab-ce` コンテナ | WSL2 内 Docker（`tools/local-gitlab-stack/docker-compose.yml`） |
| コード同期ボット | `gitea-sync-bot`（`forge: gitlab`） | WSL2 内（常駐 `--serve`） |
| ネットワーク公開 | mirrored モード or netsh portproxy | Windows ホスト |
| バックアップ | `gitlab-backup` + `/etc/gitlab` 退避 | ホスト/共有へ |

## 3. コード同期の向きと webhook

同期方式は fast-forward 調停（分岐は人手 MR）で [`gitea-gitlab-sync-design.md`](gitea-gitlab-sync-design.md) §3 に従う。
案A 固有の点:

- **作業側 = ローカル GitLab**（`working`/`gitea` スロット）、**マスター側 = 上流 GitLab**（`upstream`/`gitlab` スロット）。
- **ローカル→上流**: ローカル GitLab から同一ホストのボットへ **webhook で即時**同期（ff push で上流へ）。
- **上流→ローカル**: 上流（社内/遠隔）から個人PCへ inbound webhook は通常届かないため、ボットが上流へ
  **`git ls-remote`（軽量）で低頻度ポーリング**して取り込む（§3.7）。ここでも上流負荷は極小。
- **分岐時**: 上流コミットをローカル GitLab に統合ブランチ（`sync/*`）として取り込み、**ローカル GitLab に MR を自動起票**
  （`forge: gitlab`）。マスター（上流）は絶対に force しない。

## 4. 既存 GitLab 前提資産の適合（無改修）

- `agent-flow/executors/gitlab.py` ほか GitLab v4 を叩く資産は、**接続先ホスト/トークンをローカル GitLab に変更するだけ**。
  - 例: 環境/設定の `host` を `gitlab.local`、`token` をローカル GitLab で発行した PAT に差し替える。
- issues/MR/notes は**ローカル GitLab 上**で作成・更新される（＝ローカル管理）。API スキーマは上流と同一なので挙動は不変。
- `git-file-sync` / `gitea-sync-bot` は git レベルなので元から無関係。

## 5. 初期セットアップ手順（要点）

詳細な Windows 手順は [`tools/local-gitlab-stack/README.md`](../../tools/local-gitlab-stack/README.md)。

1. **WSL2 + Docker**: WSL2（Ubuntu 等）に Docker Engine を入れ、`/etc/wsl.conf` で `systemd=true`。
   `%UserProfile%\.wslconfig` でメモリ上限とネットワークモードを設定（`.wslconfig.example`）。
2. **GitLab 起動**: `docker compose up -d`（`docker-compose.yml`）。`external_url` を LAN 名（`http://gitlab.local`）に。
   初回は初期化に数分。`root` 初期パスワードは `/etc/gitlab/initial_root_password`。
3. **ネットワーク公開**: Windows 11 22H2+ なら `.wslconfig` の `networkingMode=mirrored`。
   それ以外は `setup-network.ps1`（netsh portproxy + Firewall）を**起動時に実行**。
4. **DNS/名前解決**: 各 PC の hosts か社内 DNS で `gitlab.local` → ホスト IP。
5. **プロジェクト作成（シード）**: ローカル GitLab に上流と同じパスのプロジェクトを作成（空）。
   同期ボットを `--once` 実行すると allowlist の ref が上流から**シード**される。
6. **同期ボット常駐**: `sync.config.yaml`（`forge: gitlab`, working=ローカル / upstream=上流）で `--serve`。
   ローカル GitLab に **Push webhook**（`http://127.0.0.1:9000/`）を登録。
7. **既存ツールの向け先変更**: 各 GitLab 前提資産のホスト/トークンをローカル GitLab に変更。
8. **バックアップ**: `backup-gitlab.sh` を日次スケジュール（gitlab-backup + secrets/config）。

## 6. 可用性（個人PC の弱点と緩和）

- **弱点**: 個人PCがスリープ/シャットダウンすると、10名共有のローカル GitLab（issues/MR 含む）が停止する。
- **緩和**:
  - 電源プランでスリープ/休止を無効化。WSL 自動起動タスク＋コンテナ `restart: always`。
  - **コードは上流に ff 同期済み**なので、ローカル停止時は**上流から clone/push にフォールバック**して作業継続可能
    （その間だけ上流負荷が戻る）。
  - issues/MR はローカルのみに存在するため、**停止中は参照不可**。恒久運用なら**専用の常時稼働機**への移設を推奨。
- **リソース**: GitLab CE は実用 8GB/4vCPU。`.wslconfig` で WSL に十分割り当て、ホストは 16GB+ を推奨。
  省メモリ設定（Prometheus 無効化・puma ワーカー削減・CI 機能無効化）を compose に同梱（CI 不要のため）。

## 7. リスク / 制約

- **issues/MR は上流と同期しない**（ローカルにのみ存在）。上流の issues/MR 機能は使わない前提。
- GitLab CE は Pull Mirror 非対応（EE 専用）のため、上流→ローカルのコード取り込みは同期ボットが担う（実装済み）。
- 個人PC 運用の可用性は本質的に弱い（§6）。将来の常時稼働機移設を見据える。
- GitLab のメジャーアップグレードは段階的に行う（バージョン固定＋計画的更新）。
- LFS / サブモジュールは追加検証。

## 8. 段階的立ち上げ

1. **PoC**: 1 プロジェクトでローカル GitLab を起動、上流からシード、`--once --dry-run` で同期挙動を確認。
2. **片方向**: `main` のみ ローカル→上流 ff を有効化し、上流負荷が同期時のみになることを実測。
3. **双方向**: 上流→ローカルのポーリング取り込み＋分岐時ローカル MR 自動起票を有効化。
4. **本運用**: 既存ツールの向け先をローカル GitLab へ切替、10名・複数プロジェクトへ拡大。
