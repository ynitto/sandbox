# webui-test のインストーラ（Windows）。PowerShell で実行する:
#
#   powershell -ExecutionPolicy Bypass -File tools\webui-test\install.ps1
#   powershell -ExecutionPolicy Bypass -File tools\webui-test\install.ps1 -Check        # 入れたあとサンプルで確かめる
#   powershell -ExecutionPolicy Bypass -File tools\webui-test\install.ps1 -SkipBrowser  # ブラウザを入れない
#
# 前提: Node.js 18 以上（無い・古いときはエラーで止まる。Node.js は入れない）
# 入れるもの:
#   - webui-test をグローバルに npm install -g（実体と依存の playwright・@playwright/test・@playwright/cli・yaml を
#     グローバル側へコピーする。入れたあとはこのフォルダを消しても動く）
#   - Playwright の Chromium
# WSL の中で使うときは、WSL で install.sh を実行する。
param(
  [switch]$Check,
  [switch]$SkipBrowser
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$ToolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = if ($env:WEBUI_TEST_HOME) { $env:WEBUI_TEST_HOME } else { Join-Path $env:LOCALAPPDATA 'webui-test' }

function Say($m) { Write-Host "==> $m" }
function Warn($m) { Write-Host "注意: $m" -ForegroundColor Yellow }

function Test-Node($exe) {
  if (-not $exe) { return $false }
  try {
    $major = & $exe -p "process.versions.node.split('.')[0]" 2>$null
    return ([int]$major -ge 18)
  } catch { return $false }
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# 1. Node.js（確認だけ）
$sys = Get-Command node -ErrorAction SilentlyContinue
if (-not $sys) { throw 'Node.js が見つかりません。https://nodejs.org/ から 18 以上を入れてください' }
$Node = $sys.Source
if (-not (Test-Node $Node)) { throw "Node.js が古いです（$(& $Node --version)）。18 以上にしてください" }
$Npm = (Get-Command npm -ErrorAction SilentlyContinue).Source
$Npx = (Get-Command npx -ErrorAction SilentlyContinue).Source
if (-not $Npm -or -not $Npx) { throw 'npm / npx が見つかりません（Node.js と一緒に入れてください）' }
Say "Node.js $(& $Node --version)（$Node）"

# 2. グローバルに入れる。フォルダを直接 -g すると、このフォルダへのリンクが置かれるだけで、
#    フォルダを消す・移すと動かなくなる。いったん tgz に固めてから入れ、実体と依存をグローバル側にコピーする。
Say 'webui-test をグローバルに入れます（npm install -g）'
$PackDir = Join-Path ([System.IO.Path]::GetTempPath()) ("webui-test-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $PackDir | Out-Null
try {
  Push-Location $ToolDir
  try { $tgz = (& $Npm pack --silent --pack-destination $PackDir | Select-Object -Last 1) } finally { Pop-Location }
  if ($LASTEXITCODE -ne 0 -or -not $tgz) { throw 'npm pack に失敗しました' }
  & $Npm install -g (Join-Path $PackDir $tgz.Trim()) --omit=dev --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw 'npm install -g に失敗しました。権限の不足なら npm の prefix をユーザーのフォルダにしてください（npm config set prefix $env:APPDATA\npm）' }
} finally { Remove-Item -Recurse -Force $PackDir -ErrorAction SilentlyContinue }
$BinDir = (& $Npm prefix -g).Trim()
$PkgDir = Join-Path (& $Npm root -g).Trim() 'webui-test'
$cmd = Join-Path $BinDir 'webui-test.cmd'
if (-not (Test-Path $PkgDir) -and (Get-Command volta -ErrorAction SilentlyContinue)) {
  # Volta で Node を管理していると、npm install -g は Volta に横取りされ、パッケージは Volta の置き場
  # （%LOCALAPPDATA%\Volta\tools\image\packages\webui-test）に入り、コマンドは Volta\bin の shim から呼ばれる。
  # npm prefix -g / root -g は Node 本体の置き場を指すので、Volta に実体の場所を聞く。
  $real = $null
  try { $real = (& volta which webui-test 2>$null | Select-Object -First 1) } catch { }
  if (-not $real) { throw 'Volta に入れた webui-test が見つかりません（volta list で確かめてください）' }
  $img = Split-Path -Parent "$real".Trim()
  # Windows の npm は <prefix>\node_modules に入れる（Linux / macOS は <prefix>\lib\node_modules）
  $PkgDir = @((Join-Path $img 'node_modules\webui-test'), (Join-Path (Split-Path -Parent $img) 'lib\node_modules\webui-test')) |
    Where-Object { Test-Path $_ } | Select-Object -First 1
  $BinDir = if ($env:VOLTA_HOME) { Join-Path $env:VOLTA_HOME 'bin' } else { Join-Path $env:LOCALAPPDATA 'Volta\bin' }
  $shim = Get-Command webui-test -ErrorAction SilentlyContinue
  $cmd = if ($shim) { $shim.Source } else { Join-Path $img 'webui-test.cmd' }
  Say "Volta の置き場に入りました（$PkgDir）"
}
if (-not $PkgDir -or -not (Test-Path $PkgDir)) { throw "入れた webui-test が見つかりません（$PkgDir）" }

# 3. ブラウザ
if ($SkipBrowser) {
  Warn "ブラウザは入れません（-SkipBrowser）。使う前に: cd $PkgDir; npx playwright install chromium"
} else {
  Say 'Playwright の Chromium を入れます'
  Push-Location $PkgDir
  try {
    & $Npx playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Chromium を入れられませんでした' }
  } finally { Pop-Location }
}

if (-not (($env:Path -split ';') -contains $BinDir)) { Warn "$BinDir が PATH にありません。ユーザーの PATH に足してください" }

# 4. エージェント CLI（テストケースを作るときに使う）
foreach ($c in @('kiro-cli', 'copilot')) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { Say "${c}: あり" } else { Warn "$c が見つかりません（webui-test generate で使うときに入れてください）" }
}

# 5. 確かめる
& $cmd --help | Out-Null
if ($Check) {
  Say '同梱のサンプルでテストを動かします'
  $port = 38917
  $server = Start-Process -FilePath $Node -ArgumentList "`"$PkgDir\examples\sample-app\server.js`"", $port -PassThru -WindowStyle Hidden
  try {
    Start-Sleep -Seconds 1
    & $cmd run "$PkgDir\examples\login.yaml" --base-url "http://localhost:$port" --out (Join-Path $DataDir 'check-results')
    if ($LASTEXITCODE -ne 0) { throw 'サンプルのテストが通りませんでした' }
  } finally { Stop-Process -Id $server.Id -ErrorAction SilentlyContinue }
}
Say 'できました。使い方: webui-test --help'
