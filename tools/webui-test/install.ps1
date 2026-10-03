# webui-test のインストーラ（Windows）。PowerShell で実行する:
#
#   powershell -ExecutionPolicy Bypass -File tools\webui-test\install.ps1
#   powershell -ExecutionPolicy Bypass -File tools\webui-test\install.ps1 -Check        # 入れたあとサンプルで確かめる
#   powershell -ExecutionPolicy Bypass -File tools\webui-test\install.ps1 -SkipBrowser  # ブラウザを入れない
#
# 前提: Node.js 18 以上（無い・古いときはエラーで止まる。Node.js は入れない）
# 入れるもの:
#   - webui-test をグローバルに npm install -g（依存の playwright・@playwright/test・@playwright/cli・yaml もこのフォルダに入る）
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

# 2. 依存を入れてから、グローバルに入れる（フォルダの -g は依存を入れず、このフォルダへのリンクを置くだけ）
Say "npm パッケージを入れます（$ToolDir\node_modules）"
Push-Location $ToolDir
try {
  & $Npm install --omit=dev --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw 'npm install に失敗しました' }
  Say 'webui-test をグローバルに入れます（npm install -g）'
  & $Npm install -g $ToolDir --omit=dev --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw 'npm install -g に失敗しました。権限の不足なら npm の prefix をユーザーのフォルダにしてください（npm config set prefix $env:APPDATA\npm）' }
  $BinDir = (& $Npm prefix -g).Trim()

  # 3. ブラウザ
  if ($SkipBrowser) {
    Warn "ブラウザは入れません（-SkipBrowser）。使う前に: cd $ToolDir; npx playwright install chromium"
  } else {
    Say 'Playwright の Chromium を入れます'
    & $Npx playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Chromium を入れられませんでした' }
  }
} finally { Pop-Location }

if (-not (($env:Path -split ';') -contains $BinDir)) { Warn "$BinDir が PATH にありません。ユーザーの PATH に足してください" }
$cmd = Join-Path $BinDir 'webui-test.cmd'

# 4. エージェント CLI（テストケースを作るときに使う）
foreach ($c in @('kiro-cli', 'copilot')) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { Say "${c}: あり" } else { Warn "$c が見つかりません（webui-test generate で使うときに入れてください）" }
}

# 5. 確かめる
& $cmd --help | Out-Null
if ($Check) {
  Say '同梱のサンプルでテストを動かします'
  $port = 38917
  $server = Start-Process -FilePath $Node -ArgumentList "`"$ToolDir\examples\sample-app\server.js`"", $port -PassThru -WindowStyle Hidden
  try {
    Start-Sleep -Seconds 1
    & $cmd run "$ToolDir\examples\login.yaml" --base-url "http://localhost:$port" --out (Join-Path $DataDir 'check-results')
    if ($LASTEXITCODE -ne 0) { throw 'サンプルのテストが通りませんでした' }
  } finally { Stop-Process -Id $server.Id -ErrorAction SilentlyContinue }
}
Say 'できました。使い方: webui-test --help'
