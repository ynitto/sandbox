# teams-use セットアップガイド

## 概要

アプリ登録は不要。スクリプトは以下の順で認証を試みる:

1. **Azure CLI セッション** — `az login` でサインイン済みなら即座に使用
2. **MSAL デバイスコードフロー** — Microsoft Graph PowerShell SDK の公開 Client ID を使用（初回のみブラウザ認証）

---

## 方法 1: Azure CLI（推奨）

### インストール

| OS | 手順 |
|----|------|
| Windows | [Microsoft 公式インストーラー](https://aka.ms/installazurecliwindows) |
| macOS | `brew install azure-cli` |
| Linux | `curl -sL https://aka.ms/InstallAzureCLIDeb \| sudo bash` |

### サインイン

```bash
az login
```

ブラウザが開き、Microsoft アカウントでサインインする。サインイン後は以降の実行で自動的にセッションが使用される。

### 動作確認

```bash
az account get-access-token --resource https://graph.microsoft.com --query accessToken --output tsv
```

トークン文字列が表示されれば準備完了。

> **注意**: Azure CLI が組織テナントのアカウントでログイン済みであれば、Teams チャンネルへのアクセスに必要な権限も自動的に付与される（管理者の同意が組織レベルで行われている場合）。

---

## 方法 2: MSAL デバイスコードフロー（フォールバック）

Azure CLI が使えない環境では、自動的にこちらが使用される。

### インストール

```bash
pip install msal requests
```

### 初回認証

スクリプト実行時に以下が表示される:

```
Azure CLI が利用できません。MSAL デバイスコードフローを使用します。
To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code XXXXXXXX to authenticate.
```

1. ブラウザで `https://microsoft.com/devicelogin` を開く
2. 表示されたコードを入力
3. 組織の Microsoft アカウントでサインイン
4. 「Microsoft Graph Command Line Tools」からのアクセス許可要求を承認

> **使用する Client ID**: `14d82eec-204b-4c2f-b7e8-296a70dab67e`（Microsoft Graph PowerShell SDK の公開 Client ID）。ユーザーが Azure AD にアプリを登録する必要はない。

認証後、トークンは `~/.teams_graph_cache.json` にキャッシュされ、以降は再認証不要（有効期限内）。

---

## 動作確認

```bash
# メッセージ確認（直近 5 件）
python scripts/get_teams_messages.py \
    --team-name "開発チーム" --channel-name "一般" --top 5

# テスト投稿
python scripts/send_teams_message.py \
    --team-name "開発チーム" --channel-name "一般" \
    --message "teams-use スキルの動作確認"
```

---

## よくある質問

### Q: 「Insufficient privileges」エラーが出る

組織テナントでは管理者の同意が必要な場合がある。IT 管理者に以下のスコープへの同意を依頼する:

- `ChannelMessage.Send`
- `ChannelMessage.Read.All`
- `Team.ReadBasic.All`
- `Channel.ReadBasic.All`

### Q: Azure CLI でサインインしているのにエラーが出る

`az account show` でサインイン状態を確認し、必要なら `az login --scope https://graph.microsoft.com/.default` で Graph API スコープを明示的に要求する。

### Q: MSAL トークンをリセットしたい

```bash
rm ~/.teams_graph_cache.json
```

次回実行時にデバイスコードフローで再認証される。

### Q: 社内プロキシ環境で動かない

```bash
export HTTPS_PROXY=http://proxy.example.com:8080
python scripts/send_teams_message.py ...
```

---

## セキュリティ注意事項

- `~/.teams_graph_cache.json` にトークンが保存されるため、パーミッションを適切に設定する:
  ```bash
  chmod 600 ~/.teams_graph_cache.json
  ```
- 委任アクセス許可を使用するため、実行ユーザーの権限範囲のみ操作できる。
- Teams チャンネルへの投稿は組織内で可視のため、テストは専用の開発チャンネルで行うことを推奨する。
