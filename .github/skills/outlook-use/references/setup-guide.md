# outlook-use セットアップガイド

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

> **注意**: 個人の Microsoft アカウント（Outlook.com / Hotmail）で `az login` した場合、Graph API の個人用メール・カレンダーへのアクセスは Azure CLI の権限設定によっては制限されることがある。その場合は方法 2 のフォールバックを使用する。

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
3. Microsoft アカウント（組織または個人）でサインイン
4. 「Microsoft Graph Command Line Tools」からのアクセス許可要求を承認

> **使用する Client ID**: `14d82eec-204b-4c2f-b7e8-296a70dab67e`（Microsoft Graph PowerShell SDK の公開 Client ID）。ユーザーが Azure AD にアプリを登録する必要はない。個人アカウント（Outlook.com / Hotmail）でも利用可能。

認証後、トークンは `~/.outlook_graph_cache.json` にキャッシュされ、以降は再認証不要（有効期限内）。

---

## 動作確認

```bash
# メール確認（受信トレイの直近 5 件）
python scripts/get_mail.py --top 5

# カレンダー確認（今後の予定 5 件）
python scripts/calendar_events.py list --top 5
```

---

## よくある質問

### Q: 「Insufficient privileges」エラーが出る

組織テナントでは管理者の同意が必要な場合がある。IT 管理者に以下のスコープへの同意を依頼する:

- `Mail.Read`
- `Mail.Send`
- `Calendars.ReadWrite`

### Q: 個人の Outlook.com / Hotmail アカウントで使えるか

**方法 2 (MSAL フォールバック) では使える。** `14d82eec-204b-4c2f-b7e8-296a70dab67e` は個人アカウントにも対応している。方法 1 (Azure CLI) は組織アカウント向けのため、個人アカウントでは制限される場合がある。

### Q: MSAL トークンをリセットしたい

```bash
rm ~/.outlook_graph_cache.json
```

次回実行時にデバイスコードフローで再認証される。

### Q: 社内プロキシ環境で動かない

```bash
export HTTPS_PROXY=http://proxy.example.com:8080
python scripts/get_mail.py
```

---

## セキュリティ注意事項

- `~/.outlook_graph_cache.json` にトークンが保存されるため、パーミッションを適切に設定する:
  ```bash
  chmod 600 ~/.outlook_graph_cache.json
  ```
- 委任アクセス許可を使用するため、実行ユーザーの権限範囲のみ操作できる（他ユーザーのメール・カレンダーへのアクセス不可）。
