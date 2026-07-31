# setup-network.ps1 — NAT モード時に LAN から WSL2 内 GitLab へ到達させる（管理者 PowerShell）。
# Windows 11 の networkingMode=mirrored を使う場合はこのスクリプトは不要。
#
# WSL2 の IP は再起動で変わるため、これを「ログオン/起動時タスク」に登録して毎回貼り直す。
# 使い方: 右クリック → PowerShell で実行、またはタスクスケジューラで起動時に実行。

param(
  [string]$Distro = "Ubuntu",
  [int[]]$Ports = @(80, 443, 2222)
)

$ErrorActionPreference = "Stop"

# WSL2 ディストロの IPv4 を取得
$wslIp = (wsl -d $Distro hostname -I).Trim().Split(" ")[0]
if (-not $wslIp) { throw "WSL の IP を取得できませんでした（ディストロ名: $Distro）" }
Write-Host "WSL2 IP = $wslIp"

foreach ($p in $Ports) {
  # 既存の転送を消してから貼り直す
  netsh interface portproxy delete v4tov4 listenport=$p listenaddress=0.0.0.0 2>$null | Out-Null
  netsh interface portproxy add    v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=$wslIp
  # LAN からの inbound を許可（未作成なら作成）
  if (-not (Get-NetFirewallRule -DisplayName "GitLab $p" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "GitLab $p" -Direction Inbound -LocalPort $p -Protocol TCP -Action Allow | Out-Null
  }
  Write-Host "forwarded 0.0.0.0:$p -> ${wslIp}:$p"
}

Write-Host "`n--- 現在の portproxy ---"
netsh interface portproxy show v4tov4
