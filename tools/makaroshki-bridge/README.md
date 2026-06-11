# makaroshki-bridge

[Macaroni Messenger (makaroshki)](https://github.com/vanyapr/makaroshki) を
**メッセージハブ**にして、人間とエージェント（Hermes など）を **PC 間で非同期に橋渡し**するツール。

makaroshki は「git リポジトリそのものをバックエンドにしたメッセンジャー」で、メッセージは
`.macaroni/` 以下の JSON ファイルとして commit / push される。専用サーバは不要で、git が
そのまま同期・保存・転送を担う。本ツールはこの仕組みを使い、

- **PC-A（人間）**: ブラウザで `messenger.html` を開いてメッセージを送る
- **PC-B（エージェント）**: 本ツールが新着を検知し、エージェントに渡して **実行・返信**する

を成立させる。

```
   PC-A（人間）                git リモート (ハブ)            PC-B（エージェント）
 ┌──────────────────┐        ┌───────────────────┐        ┌────────────────────────┐
 │ messenger.html   │ push   │  <repo>/.macaroni  │  pull  │ makaroshki-bridge      │
 │ （ブラウザで送信） │ ─────▶ │  chats/.../*.json  │ ◀───── │   ポーリング検知        │
 │                  │ pull   │  inbox/<id>/*.json │  push  │   → agent 実行          │
 │ ◀────── 返信表示  │ ◀───── │                    │ ─────▶ │   → 返信を投函           │
 └──────────────────┘        └───────────────────┘        └────────────────────────┘
```

git が唯一の真実（source of truth）なので、両 PC は同じリモート（GitHub / GitLab /
GitVerse など）を push/pull できればよい。常時接続も固定 IP も不要。

## 必要環境

- `git`（必須・PATH 上にあること）
- Python 3.9+
- PyYAML（YAML 設定を使う場合のみ。JSON 設定なら不要）— `pip install pyyaml`
- `tmux`（**任意**。`runner: tmux` で対話エージェントを駆動する場合のみ）

## セットアップガイド

セットアップは 3 段階。**共通（ハブの準備）→ PC-A（人間側）→ PC-B（エージェント側）** の順に行う。

### 共通: ハブ（git リポジトリ）を用意する

両 PC が push/pull できる git リポジトリを 1 つ用意する。

1. GitHub に新しいリポジトリを作る（例: `<you>/macaroni-hub`）。空でよい。
   - **公開リポジトリのメッセージは誰でも読める。** 会話を見られたくなければ private にする
     （private でもリポジトリにアクセスできる人は全員読める。makaroshki は「秘密にしない」設計）。
   - makaroshki のブラウザクライアントは現状 **GitHub のみ書き込み対応**なので、ハブは GitHub に置くのが簡単。
2. デフォルトブランチ名（`main` など）を控えておく。PC-B の設定で使う。

> ハブは普段使いの開発リポジトリと分けて専用に作るのを推奨。メッセージはすべてコミット履歴に残る。

### PC-A（人間側）: messenger.html をセットアップする

必要なもの: Chrome / Chromium / Edge（makaroshki の対応ブラウザ）。インストール作業は不要。

1. **トークンを作る** — GitHub の Settings → Developer settings → Fine-grained personal access tokens で、
   ハブリポジトリだけを対象に **Contents: Read and write** 権限のトークンを発行する。
   詳細手順: [makaroshki の access-token ガイド](https://github.com/vanyapr/makaroshki/blob/main/docs/access-token.en.md)
2. **クライアントを入手する** — [`messenger.html` をダウンロード](https://raw.githubusercontent.com/vanyapr/makaroshki/main/messenger.html)
   して、ブラウザで開く（ダブルクリックでよい。サーバ不要）。
3. **リポジトリを接続する** — Settings を開き、名前・ハブリポジトリの URL・トークンを入力する。
   自分の `client_id`（4 文字）が払い出され、`.macaroni/users/<client_id>.json` が作られる。
4. **チャットを作る** — エージェント用のチャットを 1 つ作成する（例: タイトル「Hermes」）。
   チャット ID（`chat_...`）は PC-B の `watch_chats` で使えるので控えておく。
5. 試しに 1 通送って、ハブリポジトリに `.macaroni/chats/.../messages/...` がコミットされることを確認する。

> 注意（makaroshki の仕様）: トークンはブラウザの localStorage に保存される。共有 PC では使わない。
> 新着はポーリングで届くため、エージェントの返信表示には数十秒の遅延がある。

### PC-B（エージェント側）: makaroshki-bridge をセットアップする

必要なもの: git / Python 3.9+（YAML 設定なら PyYAML も）。

1. **インストール**

   ```bash
   git clone <このリポジトリ> && cd <このリポジトリ>/tools/makaroshki-bridge
   bash install.sh
   # または手動で
   cp makaroshki-bridge.py ~/.local/bin/makaroshki-bridge && chmod +x ~/.local/bin/makaroshki-bridge
   ```

2. **git 認証を用意する** — bridge はハブを clone/push するので、この PC からハブへ push できる
   認証を先に通しておく（SSH 鍵 or credential helper を推奨。PC-A と同様の fine-grained PAT でもよい）。

   ```bash
   # 動作確認: これが通れば OK
   git ls-remote git@github.com:<you>/macaroni-hub.git
   ```

3. **エージェントを用意する** — 受信メッセージを処理するコマンドを決める。
   stdin にプロンプト、stdout に返信を出すものなら何でもよい（詳細は後述の「エージェントの繋ぎ方」）。
   Hermes なら薄いワンショットラッパ `hermes-oneshot` を 1 本書く。kiro-cli や `claude -p` でも可。

4. **設定ファイルを書く**

   ```bash
   cp config.yaml.example ~/makaroshki-bridge.yaml
   $EDITOR ~/makaroshki-bridge.yaml
   ```

   最低限の設定:

   ```yaml
   hub:
     remote: "git@github.com:<you>/macaroni-hub.git"   # 共通手順で作ったハブ
     branch: main

   agent:
     identity:
       client_id: "HERM"          # 4 文字（Macaroni の client_id 慣習）。PC-A の ID と重複しないこと
       display_name: "Hermes"
     runner: command
     command: "hermes-oneshot"    # stdin=プロンプト, stdout=返信 となるコマンド
   ```

5. **動作確認**

   ```bash
   # ハブに接続してチャット一覧を表示（クローン → pull される）
   # PC-A で作ったチャットが見えれば接続 OK
   makaroshki-bridge --config ~/makaroshki-bridge.yaml chats

   # 検知だけ確認（エージェント実行・返信はしない）
   makaroshki-bridge --config ~/makaroshki-bridge.yaml once --dry-run
   ```

6. **常駐起動**

   ```bash
   makaroshki-bridge --config ~/makaroshki-bridge.yaml run
   ```

   常駐させるなら tmux（`tmux new -s bridge 'makaroshki-bridge run'`）か systemd ユーザーユニットで:

   ```ini
   # ~/.config/systemd/user/makaroshki-bridge.service
   [Unit]
   Description=makaroshki-bridge
   After=network-online.target

   [Service]
   ExecStart=%h/.local/bin/makaroshki-bridge --config %h/makaroshki-bridge.yaml run
   Restart=on-failure

   [Install]
   WantedBy=default.target
   ```

   ```bash
   systemctl --user daemon-reload && systemctl --user enable --now makaroshki-bridge
   ```

### 疎通テスト（全体）

1. PC-A の `messenger.html` からチャットにメッセージを送る。
2. PC-B のログに「新着 → エージェント実行 → 返信を投函」が出るのを待つ（最大 `poll_interval_seconds` 秒）。
3. PC-A のブラウザに返信が表示される（こちらもポーリングのため少し待つ）。

PC-A を使わずに往復だけ試したいときは、PC-B（または第 3 の端末）から人間役で投函できる:

```bash
makaroshki-bridge --config ~/makaroshki-bridge.yaml send --chat <chat_id> --as SA6E --name 自分 "ping"
```

## 使い方

```
makaroshki-bridge [--config CONFIG] <command>

  run                       ポーリングループを開始（既定）
  once [--dry-run]          1 回だけ pull→処理→push して終了
  send --chat <id> [--as <client_id>] [--name <名前>] "本文"
                            メッセージを投函（人間役・テスト用）
  chats                     ハブのチャット一覧
  status                    処理済みメッセージ状態を表示
```

## エージェントの繋ぎ方（runner）

受信メッセージを「どうやってエージェントに渡し、返信を得るか」を 2 方式から選ぶ。

### `runner: command`（推奨・堅牢）

任意のコマンドを起動し、**プロンプトを stdin で渡し、標準出力をそのまま返信本文**にする。
以下の環境変数も渡される: `MACARONI_CHAT_ID` / `MACARONI_FROM` / `MACARONI_FROM_NAME` /
`MACARONI_MESSAGE_ID`。

```yaml
agent:
  runner: command
  command: "hermes-oneshot"     # 例。stdin→stdout で完結するものなら何でも可
  timeout_seconds: 600
  prompt_template: |
    {from_name} さんからのメッセージです。必要なら実行して結果を返信してください。
    {text}
```

Hermes は現状ワンショット実行を公式には公開していないため、**薄いラッパを 1 本用意するのが
最も確実**。例（擬似コード）:

```bash
#!/usr/bin/env bash
# hermes-oneshot — stdin のプロンプトを Hermes に渡し、最終応答を stdout に出す薄いラッパ
prompt="$(cat)"
# ここで hermes（または同梱の RPC / gateway / 任意の LLM CLI）を非対話で叩いて
# 最終テキスト応答だけを stdout に書く。
```

`kiro-cli` を使うなら `command: "kiro-cli chat --no-interactive"` のように差し替えるだけ。

### `runner: tmux`（対話エージェント向け・ベストエフォート）

`hermes chat` のような対話 TUI を tmux セッションで起動し、`send-keys` で送信、
`capture-pane` で応答を回収する。TUI からの抽出はヒューリスティックなので、安定運用には
`command` モードを推奨する。

```yaml
agent:
  runner: tmux
  session: makaroshki-agent
  command: "hermes chat --provider kiro-acp --model kiro-acp"
  startup_timeout_seconds: 60
  response_timeout_seconds: 300
```

> Hermes に Kiro バックエンドを足す `kiro-acp` プロバイダは `tools/hermes-kiro-acp` を参照。

## 動作の詳細

- **検知**: `hub.branch` を `git fetch` → `rebase`（失敗時は `reset --hard` で追従）し、
  監視対象チャットの `.macaroni/chats/<chat_id>/messages/**/*.json` を走査する。
  自分（`agent.identity.client_id`）以外が `from` の未処理メッセージを新着とみなす。
  `respond_to: addressed` なら自分が `to` に含まれるものだけに絞る。
- **冪等性**: 処理済み `message_id` を `state_dir` に記録し、二重処理・自分への返信ループを防ぐ。
- **返信の書き戻し**: Macaroni プロトコル v1 準拠で
  `.macaroni/chats/<chat_id>/messages/YYYY/MM/DD/<message_id>.json` を作成し、
  受信者ごとに `.macaroni/inbox/<recipient>/<message_id>.json` も作成、`reply_to` に元 ID を入れる。
  その後 commit → push（push 拒否時は pull/rebase して指数バックオフで再試行）。
- **起動時のバックログ**: 既定（`ignore_backlog_on_start: true`）では起動前の過去メッセージには
  応答せず処理済みとして記録する。過去分にも応答させたい場合は `false` にする。

## メッセージ JSON（Macaroni プロトコル v1・参考）

```json
{
  "version": 1,
  "id": "2026-06-11T08-31-25.054Z_HERM_a1b2c3",
  "chat_id": "chat_20260609_sa6e_k2xm_work",
  "type": "text",
  "from": "HERM",
  "from_name": "Hermes",
  "to": ["SA6E"],
  "created_at": "2026-06-11T08:31:25.054Z",
  "text": "返信本文",
  "reply_to": "2026-06-11T08-30-00.000Z_SA6E_zzz999",
  "attachments": [],
  "meta": { "client": "makaroshki-bridge 1.0.0" },
  "signature": null
}
```

`id` は `<created_at の ':' を '-' に置換>_<client_id>_<ランダム6文字>` で生成する。

## 制限・注意

- ポーリング間隔ぶんの遅延がある（既定 30 秒）。リアルタイム性が必要な用途には向かない。
- 認証情報（PAT 等）は `hub.remote` に直書きせず、可能なら credential helper / SSH を使う。
  直書きする場合は設定ファイルの権限管理に注意する。
- `runner: tmux` の応答抽出は TUI 依存のヒューリスティック。確実性が要るなら `command` を使う。
- 1 つのハブ・ブランチを複数の bridge インスタンスで監視しない（同一メッセージへの二重返信を避ける）。
