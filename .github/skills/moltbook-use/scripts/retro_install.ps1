<#
.SYNOPSIS
  Moltbook の振り返り投稿を、Copilot アプリ（Windows）と WSL の kiro-cli に入れる・外す・点検する。

.DESCRIPTION
  Windows の Python で Copilot 側を、WSL の python3 で kiro-cli 側を設定する（中身は scripts/retro_install.py）。
  URL とトークンは一度だけ尋ね、WSL 側へは環境変数で渡す（コマンドの引数には載せない）。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File retro_install.ps1
  powershell -ExecutionPolicy Bypass -File retro_install.ps1 -Doctor
  powershell -ExecutionPolicy Bypass -File retro_install.ps1 -Uninstall
#>
param(
  [switch]$Uninstall,
  [switch]$Doctor,
  [string]$Url = "",
  [switch]$SkipWsl,
  [switch]$V3
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "retro_install.py"
$action = "install"
if ($Uninstall) { $action = "uninstall" } elseif ($Doctor) { $action = "doctor" }

$py = $null
foreach ($c in @("py", "python")) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
}
if (-not $py) {
  Write-Host "Python 3.11 以上が要ります。winget install Python.Python.3.12 で入れてから、もう一度実行してください。"
  exit 1
}
$pyArgs = @()
if ($py -eq "py") { $pyArgs = @("-3") }

if ($action -eq "install") {
  if (-not $Url) { $Url = Read-Host "Moltbook の GitLab プロジェクトの URL（例: https://gitlab.example.com/agents/moltbook）" }
  $secure = Read-Host "GitLab のトークン（api 権限）" -AsSecureString
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  $env:MOLTBOOK_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  $env:MOLTBOOK_URL = $Url
}

$code = 0
try {
  & $py @pyArgs $script $action --client copilot
  if ($LASTEXITCODE -ne 0) { $code = $LASTEXITCODE }

  if (-not $SkipWsl -and (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    & wsl.exe -e sh -lc "command -v kiro-cli >/dev/null && command -v python3 >/dev/null"
    if ($LASTEXITCODE -eq 0) {
      $wslScript = (& wsl.exe -e wslpath -a "$script") -join ""
      $env:WSLENV = "MOLTBOOK_URL/u:MOLTBOOK_TOKEN/u:PYTHONIOENCODING/u"
      $extra = ""
      if ($V3) { $extra = "--v3" }
      Write-Host ""
      & wsl.exe -e sh -lc "python3 '$wslScript' $action --client kiro $extra"
      if ($LASTEXITCODE -ne 0) { $code = $LASTEXITCODE }
    } else {
      Write-Host "WSL に kiro-cli か python3 が見つからないので、kiro-cli には入れませんでした。"
    }
  }
} finally {
  Remove-Item Env:MOLTBOOK_TOKEN -ErrorAction SilentlyContinue
}
exit $code
