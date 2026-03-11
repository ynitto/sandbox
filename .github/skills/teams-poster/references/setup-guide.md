# Teams Poster セットアップガイド

## モジュールインストール

```powershell
# 推奨: Microsoft.Graph（サブモジュール指定で軽量インストール）
Install-Module Microsoft.Graph.Authentication -Scope CurrentUser
Install-Module Microsoft.Graph.Teams -Scope CurrentUser

# または全モジュール一括（大きいが将来の拡張に対応）
Install-Module Microsoft.Graph -Scope CurrentUser
```

## 初回認証フロー

```powershell
# インタラクティブ（デスクトップ環境）
Connect-MgGraph -Scopes "ChannelMessage.Send","Team.ReadBasic.All","Channel.ReadBasic.All"

# デバイスコードフロー（SSHセッション / CI 環境）
Connect-MgGraph -Scopes "ChannelMessage.Send" -UseDeviceAuthentication
```

認証後、`Get-MgContext` でサインイン状態を確認できる。

## Azure AD アプリ登録（オプション）

組織のポリシーで委任権限が制限されている場合、Azure AD にアプリ登録が必要:

1. [Azure Portal](https://portal.azure.com) → **Azure Active Directory** → **アプリの登録** → **新規登録**
2. **API のアクセス許可** → `Microsoft Graph` → **委任されたアクセス許可** → `ChannelMessage.Send` を追加
3. **管理者の同意を与える**
4. クライアント ID を使って接続:

```powershell
Connect-MgGraph -ClientId "<your-client-id>" -TenantId "<your-tenant-id>" `
    -Scopes "ChannelMessage.Send"
```

## チャット（1:1 / グループ）への投稿

チャンネルではなくチャット（個人 / グループ）に投稿する場合:

```powershell
Connect-MgGraph -Scopes "Chat.ReadWrite"

# 自分のチャット一覧
$chats = Get-MgChat -All
$chats | Select-Object Id, ChatType, @{N='Members';E={(Get-MgChatMember -ChatId $_.Id).DisplayName -join ', '}}

# チャットにメッセージ送信
$body = @{ body = @{ contentType = "text"; content = "こんにちは！" } }
New-MgChatMessage -ChatId "<chat-id>" -BodyParameter $body
```

## メッセージ形式サンプル

```powershell
# Markdown 風 HTML
$html = @"
<b>ビルド結果</b><br>
環境: Production<br>
ステータス: <span style='color:green'>成功</span><br>
コミット: <a href='https://github.com/org/repo/commit/abc'>abc1234</a>
"@
.\Send-TeamsMessage.ps1 -TeamName "DevOps" -ChannelName "通知" -Message $html -ContentType "html"

# 複数行テキスト
$text = "デプロイ完了`nバージョン: 1.2.3`n環境: Staging"
.\Send-TeamsMessage.ps1 -TeamName "開発チーム" -ChannelName "一般" -Message $text
```

## CI/CD 環境での利用（非対話的認証）

GitHub Actions / Azure DevOps 等のパイプラインで使う場合はクライアントシークレット認証を推奨:

```powershell
# サービスプリンシパル認証（アプリケーション権限）
$clientSecret = ConvertTo-SecureString $env:CLIENT_SECRET -AsPlainText -Force
$credential = New-Object System.Management.Automation.PSCredential($env:CLIENT_ID, $clientSecret)

Connect-MgGraph -ClientSecretCredential $credential -TenantId $env:TENANT_ID

# アプリケーション権限では ChannelMessage.Send ではなく
# ChannelMessage.Send.All が必要（管理者同意必須）
```

## トークンキャッシュの管理

```powershell
# サインアウト
Disconnect-MgGraph

# キャッシュクリア（問題発生時）
Get-ChildItem "$env:USERPROFILE\.graph" -ErrorAction SilentlyContinue | Remove-Item -Recurse
```
