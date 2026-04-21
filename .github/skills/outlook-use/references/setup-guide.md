# outlook-use セットアップガイド

## 1. Python パッケージのインストール

```bash
pip install msal requests
```

---

## 2. Azure AD アプリ登録

### 2-1. Azure ポータルでアプリを登録する

1. [Azure Portal](https://portal.azure.com) にサインイン
2. **Microsoft Entra ID**（旧 Azure Active Directory）→ **アプリの登録** → **新規登録** をクリック
3. 以下を入力して **登録**:
   - 名前: `outlook-use-cli`（任意）
   - サポートされているアカウントの種類: **任意の組織ディレクトリ内のアカウントと個人用 Microsoft アカウント（例: Skype、Xbox）**
   - リダイレクト URI: 種類を **パブリック クライアント/ネイティブ（モバイルとデスクトップ）** に変更し、`https://login.microsoftonline.com/common/oauth2/nativeclient` を入力

4. 登録後に表示される **アプリケーション（クライアント）ID** をコピーする

### 2-2. API アクセス許可を追加する

1. 登録したアプリ → **API のアクセス許可** → **アクセス許可の追加**
2. **Microsoft Graph** → **委任されたアクセス許可** を選択
3. 以下のスコープにチェックを入れて **アクセス許可の追加**:

| スコープ | 用途 |
|---------|------|
| `Mail.Read` | メール読み取り |
| `Mail.Send` | メール送信 |
| `Calendars.ReadWrite` | カレンダー読み書き |
| `offline_access` | トークン更新（必須） |

> **注意**: 組織テナントの場合、管理者の同意が必要になることがある。その際は IT 管理者に「管理者の同意を付与」を依頼する。

### 2-3. モバイルとデスクトップのフロー設定

1. アプリ → **認証** → **詳細設定**
2. **パブリック クライアント フローを許可する** を **はい** に設定して **保存**

---

## 3. Client ID の設定

スクリプトを初回実行すると、Client ID の入力を求められる:

```
Azure AD アプリの Client ID を入力してください: <ここに貼り付け>
```

入力した Client ID は `~/.outlook_graph_client_id` に保存され、以降は自動で読み込まれる。

手動で設定する場合:

```bash
echo "YOUR_CLIENT_ID_HERE" > ~/.outlook_graph_client_id
```

---

## 4. 初回認証

スクリプトを実行すると、デバイスコードフローで認証を求められる:

```
To sign in, use a web browser to open the page https://microsoft.com/devicelogin
and enter the code XXXXXXXX to authenticate.
```

1. ブラウザで `https://microsoft.com/devicelogin` を開く
2. 表示されたコードを入力
3. Microsoft アカウントでサインイン
4. 要求されているアクセス許可を確認して **承認**

認証成功後、トークンは `~/.outlook_graph_cache.json` にキャッシュされる。以降は有効期限内であれば自動更新されるため、再認証不要。

---

## 5. 動作確認

```bash
# メール確認（受信トレイの直近 5 件）
python scripts/get_mail.py --top 5

# カレンダー確認（今後の予定 5 件）
python scripts/calendar_events.py list --top 5
```

---

## 6. よくある質問

### Q: 「Insufficient privileges」エラーが出る

組織テナントでは管理者の同意が必要な場合がある。Azure Portal で **API のアクセス許可** → **〇〇 に管理者の同意を付与する** をクリックするか、IT 管理者に依頼する。

### Q: 個人用 Microsoft アカウント（Outlook.com / Hotmail）で使えるか

使える。アプリ登録時に「任意の組織ディレクトリ内のアカウントと個人用 Microsoft アカウント」を選択していれば、`@outlook.com` / `@hotmail.com` でも認証できる。

### Q: トークンをリセットしたい

```bash
rm ~/.outlook_graph_cache.json
```

次回実行時にデバイスコードフローで再認証される。

### Q: Client ID を変更したい

```bash
rm ~/.outlook_graph_client_id
```

次回実行時に再入力を求められる。

### Q: 社内プロキシ環境で動かない

`requests` はシステムのプロキシ設定を自動的に使用する。環境変数でプロキシを設定する:

```bash
export HTTPS_PROXY=http://proxy.example.com:8080
python scripts/get_mail.py
```

---

## 7. セキュリティ注意事項

- `~/.outlook_graph_cache.json` にはアクセストークン・リフレッシュトークンが保存される。ファイルのパーミッションを適切に設定すること:
  ```bash
  chmod 600 ~/.outlook_graph_cache.json
  ```
- Client ID はシークレットではないが、`~/.outlook_graph_client_id` も同様に保護することを推奨:
  ```bash
  chmod 600 ~/.outlook_graph_client_id
  ```
- このスクリプトは **委任アクセス許可** を使用するため、実行ユーザーの権限範囲のみ操作できる（他ユーザーのメールへのアクセスは不可）。
