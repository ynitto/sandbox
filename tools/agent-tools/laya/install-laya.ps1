# install-laya.ps1 — Windows 用の入口。Python 3.10 以上を探して install_laya.py へ渡す。
#   powershell -ExecutionPolicy Bypass -File tools\agent-tools\laya\install-laya.ps1 [--port 8000] [--dry-run] ...
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here 'install_laya.py'
$check = 'import sys, venv; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
$candidates = @()
if ($env:PYTHON) { $candidates += ,@($env:PYTHON) }
$candidates += ,@('py', '-3')
$candidates += ,@('python')
$candidates += ,@('python3')
foreach ($cand in $candidates) {
  $exe = $cand[0]
  $pre = @($cand | Select-Object -Skip 1)
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
  & $exe @pre -c $check 2>$null
  if ($LASTEXITCODE -eq 0) {
    & $exe @pre $script @args
    exit $LASTEXITCODE
  }
}
Write-Error 'Python 3.10 以上が見つかりません。https://www.python.org/ から入れてください（Microsoft Store 版でも可）。'
exit 1
