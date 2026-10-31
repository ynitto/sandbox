<#
.SYNOPSIS
  Obsidian へメモを集めるフックを、Copilot アプリ（Windows）と WSL の kiro-cli に入れる・外す・点検する。

.DESCRIPTION
  Windows の Python で Copilot 側を、WSL の python3 で kiro-cli 側を設定する（中身は scripts/hook_install.py）。
  ボルトは Windows 側のフォルダを指定する。WSL 側には /mnt/<ドライブ>/... に読み替えて渡す。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File hook_install.ps1 -Vault "$HOME\Documents\Obsidian\Main"
  powershell -ExecutionPolicy Bypass -File hook_install.ps1 -Doctor
  powershell -ExecutionPolicy Bypass -File hook_install.ps1 -Uninstall
#>
param(
  [switch]$Uninstall,
  [switch]$Doctor,
  [string]$Vault = "",
  [string]$WikiDir = "llm-wiki",
  [switch]$SkipWsl,
  [switch]$V3
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "hook_install.py"
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

if ($action -eq "install" -and -not $Vault) {
  $Vault = Read-Host "Obsidian のボルトのフォルダ（例: $HOME\Documents\Obsidian\Main）"
}
$vaultArgs = @()
if ($action -eq "install" -and $Vault) { $vaultArgs = @("--vault", $Vault, "--wiki-dir", $WikiDir) }

$code = 0
& $py @pyArgs $script $action --client copilot @vaultArgs
if ($LASTEXITCODE -ne 0) { $code = $LASTEXITCODE }

if (-not $SkipWsl -and (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
  & wsl.exe -e sh -lc "command -v kiro-cli >/dev/null && command -v python3 >/dev/null"
  if ($LASTEXITCODE -eq 0) {
    $wslScript = (& wsl.exe -e wslpath -a "$script") -join ""
    $extra = ""
    if ($V3) { $extra = "--v3" }
    if ($action -eq "install" -and $Vault) {
      $wslVault = (& wsl.exe -e wslpath -a "$Vault") -join ""
      $extra = "$extra --vault '$wslVault' --wiki-dir '$WikiDir'"
    }
    Write-Host ""
    & wsl.exe -e sh -lc "python3 '$wslScript' $action --client kiro $extra"
    if ($LASTEXITCODE -ne 0) { $code = $LASTEXITCODE }
  } else {
    Write-Host "WSL に kiro-cli か python3 が見つからないので、kiro-cli には入れませんでした。"
  }
}
exit $code
