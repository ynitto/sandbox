# teams-use セットアップガイド

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
   - 名前: `teams-use-cli`（任意）
   - サポートされているアカウントの種類: **任意の組織ディレクトリ内のアカウント**（または組織テナントのみ）
   - リダイレクト URI: 種類を **パブリック クライアント/ネイティブ（モバイルとデスクトップ）** に変更し、`https://login.microsoftonline.com/common/oauth2/nativeclient` を入力

4. 登録後に表示される **アプリケーション（クライアント）ID** をコピーする

### 2-2. API アクセス許可を追加する

1. 登録したアプリ → **API のアクセス許可** → **アクセス許可の追加**
2. **Microsoft Graph** → **委任されたアクセス許可** を選択
3. 以下のスコープにチェックを入れて **アクセス許可の追加**:

| スコープ | 用途 |
|---------|------|
| `ChannelMessage.Send` | チャンネルへのメッセージ投稿 |
| `ChannelMessage.Read.All` | メッセージ読み取り・タイトル検索 |
| `Team.ReadBasic.All` | チーム一覧・名前解決 |
| `Channel.ReadBasic.All` | チャンネル一覧・名前解決 |
| `offline_access` | トークン更新（必須） |

4. 組織テナントの場合 → **管理者の同意を付与する** ボタンをクリック（IT 管理者が操作）

### 2-3. パブリッククライアントフローを有効にする

1. アプリ → **認証** → **詳細設定**
2. **パブリック クライアント フローを許可する** を **はい** に設定して **保存**

> これを設定しないとデバイスコードフローが「AADSTS7000218」エラーで失敗する。

---

## 3. Client ID の設定

スクリプトを初回実行すると、Client ID の入力を求められる:

```
Azure AD アプリの Client ID を入力してください: <ここに貼り付け>
```

入力した Client ID は `~/.teams_graph_client_id` に保存され、以降は自動で読み込まれる。

手動で設定する場合:

```bash
echo "YOUR_CLIENT_ID_HERE" > ~/.teams_graph_client_id
```

---

## 4. 初回認証

スクリプトを実行するとデバイスコードが表示される:

```
To sign in, use a web browser to open the page https://microsoft.com/devicelogin
and enter the code XXXXXXXX to authenticate.
```

1. ブラウザで `https://microsoft.com/devicelogin` を開く
2. 表示されたコードを入力
3. 組織の Microsoft アカウントでサインイン
4. 要求されているアクセス許可を確認して **承認**

認証成功後、トークンは `~/.teams_graph_cache.json` にキャッシュされる。以降は有効期限内であれば自動更新されるため、再認証不要。

---

## 5. 動作確認

```bash
# チャンネルのメッセージ確認（直近 5 件）
python scripts/get_teams_messages.py \
    --team-name "開発チーム" --channel-name "一般" --top 5

# テスト投稿
python scripts/send_teams_message.py \
    --team-name "開発チーム" --channel-name "一般" \
    --message "teams-use スキルの動作確認"
```

---

## 6. チーム・チャンネル ID の調べ方

名前がわかればスクリプトが自動解決するが、ID を事前に調べる場合は Teams クライアントを使う:

- **チーム ID**: Teams → 対象チームを右クリック → 「チームへのリンク取得」→ URL 内の GUID
- **チャンネル ID**: 同様にチャンネルのリンクから取得

または、スクリプトの出力に ID が含まれるため `get_teams_messages.py` 実行後の出力から確認することもできる。

---

## 7. よくある質問

### Q: 「Insufficient privileges」エラーが出る

組織テナントでは管理者の同意が必要。Azure Portal の **API のアクセス許可** → **管理者の同意を付与する** を実行するか、IT 管理者に依頼する。

### Q: トークンをリセットしたい

```bash
rm ~/.teams_graph_cache.json
```

次回実行時にデバイスコードフローで再認証される。

### Q: Client ID を変更したい

```bash
rm ~/.teams_graph_client_id
```

次回実行時に再入力を求められる。

### Q: 社内プロキシ環境で動かない

`requests` はシステムのプロキシ設定を自動的に使用する。環境変数でプロキシを設定する:

```bash
export HTTPS_PROXY=http://proxy.example.com:8080
python scripts/send_teams_message.py ...
```

---

## 8. セキュリティ注意事項

- `~/.teams_graph_cache.json` にはアクセストークン・リフレッシュトークンが保存される。ファイルのパーミッションを適切に設定すること:
  ```bash
  chmod 600 ~/.teams_graph_cache.json
  chmod 600 ~/.teams_graph_client_id
  ```
- このスクリプトは **委任アクセス許可** を使用するため、実行ユーザーの権限範囲のみ操作できる（他ユーザーのメッセージへのアクセス不可）。
- Teams チャンネルへの投稿は組織内で可視のため、テストは専用の開発チャンネルで行うことを推奨する。
