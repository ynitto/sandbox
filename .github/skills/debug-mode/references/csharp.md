# C#

デバッグモードの計装パターン（C# 対応、Windows/Copilot 最適化）。

## ワンライナー

```csharp
#region debug:H1
System.IO.File.AppendAllText("debug.log", System.Text.Json.JsonSerializer.Serialize(new{h="H1",l="label",v=new{key=value},ts=DateTimeOffset.Now.ToUnixTimeMilliseconds()})+"\n");
#endregion
```

## 展開版

```csharp
#region debug:H1
using System.Text.Json;

var entry = new
{
    h = "H1",
    l = "user_state",
    v = new { userId = userId, cart = cart },
    ts = DateTimeOffset.Now.ToUnixTimeMilliseconds()
};

File.AppendAllText("debug.log", JsonSerializer.Serialize(entry) + "\n");
#endregion
```

## ログ分析（PowerShell）

```powershell
# 全件表示
Get-Content debug.log | ForEach-Object { $_ | ConvertFrom-Json }

# H1でフィルタ
Get-Content debug.log | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.h -eq "H1" }

# ラベルでフィルタ
Get-Content debug.log | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.l -eq "user_state" }

# リアルタイム監視
Get-Content debug.log -Wait | ForEach-Object { $_ | ConvertFrom-Json }
```

## ログクリア（PowerShell）

```powershell
Remove-Item -Force debug.log -ErrorAction SilentlyContinue
```

## 計装残骸の検索（PowerShell）

```powershell
Select-String -Path "src\*" -Pattern "#region debug:" -Recurse
```
