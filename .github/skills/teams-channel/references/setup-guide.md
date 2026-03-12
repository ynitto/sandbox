# Teams Channel セットアップガイド

## モジュールインストール

```powershell
# 推奨: Microsoft.Graph（サブモジュール指定で軽量インストール）
Install-Module Microsoft.Graph.Authentication -Scope CurrentUser
Install-Module Microsoft.Graph.Teams -Scope CurrentUser

# または全モジュール一括（大きいが将来の拡張に対応）
Install-Module Microsoft.Graph -Scope CurrentUser
```

インストールに失敗する場合（社内プロキシ環境等）は後述の「[プロキシ環境でのセットアップ](#プロキシ環境でのセットアップ)」を参照。

## 初回認証フロー

スクリプトの用途に応じて要求するスコープが異なる。

```powershell
# 投稿のみ（Send-TeamsMessage.ps1）
Connect-MgGraph -Scopes "ChannelMessage.Send","Team.ReadBasic.All","Channel.ReadBasic.All"

# 読み取りのみ（Get-TeamsMessages.ps1）
Connect-MgGraph -Scopes "ChannelMessage.Read.All","Team.ReadBasic.All","Channel.ReadBasic.All"

# 投稿 + タイトルで返信先検索（Send-TeamsMessage.ps1 で -ReplyToSubject を使う場合）
Connect-MgGraph -Scopes "ChannelMessage.Send","ChannelMessage.Read.All","Team.ReadBasic.All","Channel.ReadBasic.All"

# デバイスコードフロー（SSHセッション / CI 環境・投稿のみ）
Connect-MgGraph -Scopes "ChannelMessage.Send" -UseDeviceAuthentication
```

> **原則**: 各スクリプトは必要最小限のスコープのみ要求する。読み取り（`Get-TeamsMessages.ps1`）は `ChannelMessage.Send` を要求しない。

認証後、`Get-MgContext` でサインイン状態を確認できる。

## Azure AD アプリ登録（オプション）

組織のポリシーで委任権限が制限されている場合、Azure AD にアプリ登録が必要:

1. [Azure Portal](https://portal.azure.com) → **Azure Active Directory** → **アプリの登録** → **新規登録**
2. **API のアクセス許可** → `Microsoft Graph` → **委任されたアクセス許可** → 用途に応じて追加:
   - 投稿: `ChannelMessage.Send`
   - 読み取り: `ChannelMessage.Read.All`
3. **管理者の同意を与える**
4. クライアント ID を使って接続:

```powershell
# 投稿用
Connect-MgGraph -ClientId "<your-client-id>" -TenantId "<your-tenant-id>" `
    -Scopes "ChannelMessage.Send"

# 読み取り用
Connect-MgGraph -ClientId "<your-client-id>" -TenantId "<your-tenant-id>" `
    -Scopes "ChannelMessage.Read.All"
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

# アプリケーション権限（委任ではなく Application 権限）では:
# 投稿: ChannelMessage.Send ではなく ChannelMessage.Send.All が必要（管理者同意必須）
# 読み取り: ChannelMessage.Read.All（管理者同意必須）
```

## トークンキャッシュの管理

```powershell
# サインアウト
Disconnect-MgGraph

# キャッシュクリア（問題発生時）
Get-ChildItem "$env:USERPROFILE\.graph" -ErrorAction SilentlyContinue | Remove-Item -Recurse
```

## プロキシ環境でのセットアップ

社内プロキシ経由でないと外部通信できない環境では、`Install-Module` が失敗することがある。

### 対話スクリプトを使う（推奨）

[scripts/Setup-Proxy.ps1](../scripts/Setup-Proxy.ps1) を実行すると、プロキシ URL・認証情報を対話入力してセットアップを自動完了できる。

```powershell
.\Setup-Proxy.ps1
```

実行すると以下を順に行う:

1. プロキシ URL を入力（例: `http://proxy.example.com:8080`）
2. 認証要否を確認
   - 認証あり: ユーザー名とパスワード（マスク入力）を要求
   - 認証なし: 現在の Windows ログインユーザーの統合認証を使用
3. PowerShell Gallery への疎通確認
4. `Microsoft.Graph.Authentication` / `Microsoft.Graph.Teams` をインストール

> セッション内のみ有効。OS のプロキシ設定は変更しない。

---

### 手動でプロキシを設定する

`Install-Module` の前に以下を実行することで同様の効果が得られる。

```powershell
# ① プロキシ URL を設定
$proxyUrl = 'http://proxy.example.com:8080'
$proxy = New-Object System.Net.WebProxy($proxyUrl, $true)

# ② 認証情報を設定（不要な場合は ② を省略し ③ で UseDefaultCredentials = $true を使う）
$proxyUser = Read-Host 'プロキシ ユーザー名'
$proxyPass = Read-Host 'プロキシ パスワード' -AsSecureString
$proxy.Credentials = (New-Object System.Management.Automation.PSCredential($proxyUser, $proxyPass)).GetNetworkCredential()

# ③ 認証不要・統合 Windows 認証を使う場合はこちら（② の代わりに）
# $proxy.UseDefaultCredentials = $true

# ④ セッションへ適用
[System.Net.WebRequest]::DefaultWebProxy = $proxy
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

# ⑤ モジュールインストール
Install-Module Microsoft.Graph.Authentication -Scope CurrentUser -Force
Install-Module Microsoft.Graph.Teams -Scope CurrentUser -Force
```

---

### 同一セッションでスクリプトも実行する

プロキシ設定はセッションに閉じているため、別ウィンドウで `Send-TeamsMessage.ps1` や `Get-TeamsMessages.ps1` を開くとプロキシが外れる。同一セッションで続けて実行するか、`$env:HTTPS_PROXY` を設定してセッションをまたいで使う。

```powershell
# 環境変数でプロキシを設定（認証なしの場合）
$env:HTTPS_PROXY = 'http://proxy.example.com:8080'
$env:HTTP_PROXY  = 'http://proxy.example.com:8080'

# 認証ありの場合は URL に認証情報を埋め込む（パスワードは URLエンコードすること）
$env:HTTPS_PROXY = 'http://user:p%40ssword@proxy.example.com:8080'
```

> **注意**: 環境変数にパスワードを埋め込む場合、ログや履歴に残るリスクがある。可能な限りセッション内の `PSCredential` 方式（対話スクリプト）を使用すること。
