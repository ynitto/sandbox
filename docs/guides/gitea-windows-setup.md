# Gitea（Windows）セットアップ & GitLab 連携ガイド

> 最終更新: 2026-07-08 ／ 関連: [`docs/designs/gitea-gitlab-sync-design.md`](../designs/gitea-gitlab-sync-design.md)（設計正典）,
> [`tools/gitea-sync-bot/`](../../tools/gitea-sync-bot/)（同期ボット実装）
>
> 対象: **Windows 上に LAN 内 Gitea を立て**、Issue/MR を Gitea で管理し、コードを GitLab と
> 双方向同期する構成（最大 10 名規模）。本書は導入手順の正典。

## 0. 前提と全体像

```
[開発者 PC ×10] ──(LAN)──▶ [Gitea @ Windows] ◀──[gitea-sync-bot]──▶ [GitLab @ WAN]
   clone/push/issue/MR            issue/MR/コード        fast-forward 調停      正本/CI/バックアップ
```

- 開発者の日常操作は Gitea で完結（GitLab への WAN トラフィックを差分同期だけに圧縮）。
- コードは allowlist（`main` / `release/*` / tags 等）だけを GitLab と同期。`feature/*` は同期しない。
- 用意するもの: Windows マシン 1 台（Gitea 用。同居 or 別プロセスで sync-bot も動かす）、GitLab の対象プロジェクトへの権限。

---

## 1. Gitea のインストール（Windows）

Gitea は Go 製の単一 exe。10 名なら DB は SQLite でも動くが、**運用性の観点から PostgreSQL/MySQL 推奨**。
ここでは手堅い「バイナリ + Windows サービス化 + PostgreSQL」を示す（最短で試すなら §1.6 の SQLite 版）。

### 1.1 事前準備

- **git for Windows** をインストール（`git --version` が通ること）。Gitea も sync-bot も git に依存。
- 配置用フォルダを作成（例）:
  ```
  C:\gitea\            … 実行ファイルと設定
  C:\gitea\data\       … リポジトリ・添付・DB(SQLite時)
  C:\gitea\log\
  ```
- サービス実行用のローカルユーザー（例 `gitea`）を用意すると権限管理が楽（任意）。

### 1.2 バイナリ配置

1. 公式配布から Windows 版 `gitea-<version>-windows-4.0-amd64.exe` を取得し、
   `C:\gitea\gitea.exe` として保存する（社内でバイナリ検証ポリシーがあれば SHA256/署名を確認）。
2. PowerShell（管理者）でフォルダ権限を実行ユーザーに付与しておく。

### 1.3 データベース（PostgreSQL 推奨）

1. PostgreSQL をインストールし、Gitea 用の DB とユーザーを作成:
   ```sql
   CREATE ROLE gitea WITH LOGIN PASSWORD 'ここに強いパスワード';
   CREATE DATABASE giteadb WITH OWNER gitea ENCODING 'UTF8';
   ```
2. `giteadb` / `gitea` / パスワードを控える（§1.4 の初期設定で入力）。

### 1.4 初回起動と初期設定

1. まず対話なしで一度起動して待受を確認:
   ```powershell
   cd C:\gitea
   .\gitea.exe web
   ```
2. ブラウザで `http://<このPCのLAN IP>:3000/` を開き、初期インストール画面で設定:
   - **Database**: PostgreSQL、Host `127.0.0.1:5432`、User `gitea`、DB `giteadb`。
   - **Server Domain / SSH**: `SSH Server Domain` と `Gitea Base URL` を **LAN のホスト名/IP**にする
     （例 `http://gitea.local:3000/`）。ここを正しく入れないと後で clone URL がずれる。
   - **App Name / Repository Root Path**: `C:\gitea\data\gitea-repositories` など。
   - 最後に**管理者アカウント**を作成。
3. 初期設定は `C:\gitea\custom\conf\app.ini` に保存される。以降の変更はこのファイルを直接編集。

### 1.5 Windows サービス化（常駐）

`sc.exe` でサービス登録すると再起動後も自動で立ち上がる（管理者 PowerShell）:

```powershell
sc.exe create Gitea binPath= "C:\gitea\gitea.exe web --config C:\gitea\custom\conf\app.ini" start= auto DisplayName= "Gitea"
sc.exe description Gitea "Gitea Git Service (LAN)"
sc.exe start Gitea
```

- 実行ユーザーを専用ユーザーにするなら `obj=` と `password=` を付与。
- `app.ini` の `[server] HTTP_ADDR` を `0.0.0.0`（LAN 公開）に、必要なら Windows Firewall で
  TCP 3000 を LAN からのみ許可する。

### 1.6 最短で試す（SQLite・サービスなし）

PoC だけなら DB を SQLite にして exe を直接起動でよい:

```powershell
cd C:\gitea
.\gitea.exe web    # 初期設定で Database=SQLite3 を選ぶだけ
```

### 1.7 推奨初期設定（`app.ini` 抜粋）

```ini
[server]
HTTP_ADDR = 0.0.0.0
HTTP_PORT = 3000
ROOT_URL  = http://gitea.local:3000/     ; LAN のベース URL（clone URL に使われる）

[service]
DISABLE_REGISTRATION = true               ; 10 名運用は管理者が招待/作成する
REQUIRE_SIGNIN_VIEW  = true               ; 社内限定

[repository]
DEFAULT_BRANCH = main
```

- **TLS**: LAN 内でも HTTPS を推奨（社内 CA でも可）。Gitea 単体 TLS か、前段にリバースプロキシ（例 Caddy/nginx）。
- **バックアップ**: `gitea dump`（リポジトリ + DB + `custom/`）を日次でスケジュール実行（タスク スケジューラ）。
  GitLab が正本なので二重の保全になる。

### 1.8 ユーザー登録（最大 10 名）

- 管理者画面（Site Administration）→ Users から作成、または招待。
- 各開発者は Gitea 上で SSH 公開鍵か HTTP トークンを登録して clone/push する（**普段の操作先は Gitea だけ**）。

---

## 2. GitLab 連携の準備

同期ボットが GitLab へ push/fetch できるよう、**最小権限のトークン**を用意する。

### 2.1 GitLab 側: アクセストークン発行

- 対象プロジェクトで **Project Access Token**（または Deploy Token）を発行し、スコープは
  **`write_repository`（と read）のみ**に絞る。
- 発行した値を控える（例として環境変数 `GITLAB_TOKEN`）。ボットのホスト以外へは配布しない。
- 認証付き URL 例（設定の `repos[].gitlab.url`）:
  ```
  https://oauth2:${GITLAB_TOKEN}@gitlab.example.com/team/myproject.git
  ```

### 2.2 Gitea 側: ボット用トークン（統合 MR 起票に使う場合）

- 分岐時に Gitea へ統合 MR を自動起票する（`create_gitea_pr: true`）なら、Gitea で
  ボット用ユーザーの **Application Token**（`write:repository` 相当）を発行し、`GITEA_TOKEN` に設定。
- push だけで MR 起票が不要なら省略可。

### 2.3 資格情報の保管

- トークンは `config.yaml` に**直書きしない**。Windows の環境変数に設定し、設定では `${GITLAB_TOKEN}` で参照。
- サービスとして常駐させる場合は、サービス実行ユーザーの環境変数（またはシステム環境変数）に登録する。

---

## 3. 同期ボット（gitea-sync-bot）の設定と起動

実装と詳細: [`tools/gitea-sync-bot/`](../../tools/gitea-sync-bot/)。

### 3.1 セットアップ

```powershell
# Python 3.9+ と PyYAML（YAML 設定を使う場合）
python -m pip install pyyaml

cd <repo>\tools\gitea-sync-bot
copy config.yaml.example config.yaml    # 編集: repos / include / exclude / webhook.secret
```

`config.yaml` の要点:
- `sync.include` / `sync.exclude`: **同期する共有ブランチだけを include**、`feature/*` 等は exclude。
- `repos[]`: `gitea.url` は LAN の Gitea、`gitlab.url` は GitLab（必要なら `oauth2:${GITLAB_TOKEN}@` を埋める）。
- `webhook.secret`: Gitea/GitLab の webhook secret と一致させる。

### 3.2 動作確認（まず片方向・dry-run）

```powershell
# 予定だけ表示（push しない）
python gitea_sync_bot.py --config config.yaml --repo myproject --ref refs/heads/main --once --dry-run
# 問題なければ 1 回同期
python gitea_sync_bot.py --config config.yaml --once
```

### 3.3 常駐（webhook 待受 + cron バックストップ）

```powershell
python gitea_sync_bot.py --config config.yaml --serve
```

- 待受ポート（既定 9000）を Windows Firewall で LAN からのみ許可。
- サービス化するなら [NSSM](https://nssm.cc/) 等で `python gitea_sync_bot.py --config ... --serve` をサービス登録すると、
  Gitea サービスと並べて常駐運用できる。

### 3.4 webhook 登録

- **Gitea**: 対象リポジトリ → Settings → Webhooks → Gitea 種別。
  Target URL `http://<bot-host>:9000/`、Secret を `webhook.secret` と一致、Trigger は **Push events**。
- **GitLab**: Project → Settings → Webhooks。
  URL 同上、Secret Token を `webhook.secret` と一致、**Push events** を有効化。
- これで**変化があったときだけ**ボットが該当 ref を同期し、無変化時は GitLab に接続しない（§3.7）。

---

## 4. 段階的な立ち上げ（推奨）

1. **PoC**: 1 リポジトリで Gitea を構築し、`--once --dry-run` で挙動確認。
2. **片方向**: `allowlist=main` のみを Gitea→GitLab の ff で同期して安定確認。
   webhook 主導で GitLab 接続が変化時のみになることを実測（設計書 §3.7）。
3. **双方向**: GitLab 側 write も許容し、ff 双方向 + 分岐時の統合 MR を有効化。
   運用ルール（`main` は Gitea 側マージのみ 等・設計書 §3.5）を確定。
4. **横展開**: 対象リポジトリ・ユーザーを 10 名規模へ拡大。

## 5. トラブルシューティング / 注意

- **clone URL がおかしい**: `app.ini` の `ROOT_URL` を LAN のホスト名/IP に修正（§1.7）。
- **同期が動かない**: `--once --dry-run` で判定を確認。webhook が届いているかは `--serve` のログで確認。
- **分岐（diverged）が頻発**: 同一ブランチへ両側から同時 write している。write 方向を分ける運用に寄せる（§3.5）。
- **GitLab へ余計な push が出る**: `sync.include` を絞りすぎ/緩すぎないか確認。`feature/*` が include に入っていないか。
- **トークン漏洩防止**: `config.yaml` に生トークンを書かない。環境変数展開（`${...}`）を使う。
- Issue/MR は Gitea 内のみで管理（GitLab へは同期しない）。将来同期の設計概要は設計書 §5。
