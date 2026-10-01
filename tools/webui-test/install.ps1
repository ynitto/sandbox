# webui-test のインストーラ（Windows）。PowerShell で実行する:
#
#   powershell -ExecutionPolicy Bypass -File tools\webui-test\install.ps1
#   powershell -ExecutionPolicy Bypass -File tools\webui-test\install.ps1 -Check        # 入れたあとサンプルで確かめる
#   powershell -ExecutionPolicy Bypass -File tools\webui-test\install.ps1 -SkipBrowser  # ブラウザを入れない
#
# 入れるもの（すでにあれば使う）:
#   - Node.js 18 以上。無い・古いときは公式の LTS の zip を %LOCALAPPDATA%\webui-test\node に展開する（管理者権限は不要）
#   - npm パッケージ（playwright・@playwright/test・@playwright/cli・yaml）… このフォルダの node_modules
#   - Playwright の Chromium
#   - webui-test コマンド … %LOCALAPPDATA%\webui-test\bin\webui-test.cmd（ユーザーの PATH に足す）
# WSL の中で使うときは、WSL で install.sh を実行する。
param(
  [switch]$Check,
  [switch]$SkipBrowser,
  [int]$NodeMajor = 22
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$ToolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = if ($env:WEBUI_TEST_HOME) { $env:WEBUI_TEST_HOME } else { Join-Path $env:LOCALAPPDATA 'webui-test' }
$BinDir = Join-Path $DataDir 'bin'

function Say($m) { Write-Host "==> $m" }
function Warn($m) { Write-Host "注意: $m" -ForegroundColor Yellow }

function Test-Node($exe) {
  if (-not $exe) { return $false }
  try {
    $major = & $exe -p "process.versions.node.split('.')[0]" 2>$null
    return ([int]$major -ge 18)
  } catch { return $false }
}

function Install-Node {
  $arch = if ([Environment]::Is64BitOperatingSystem) { if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' } } else { 'x86' }
  $dist = "https://nodejs.org/dist/latest-v$NodeMajor.x"
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ("webui-test-" + [Guid]::NewGuid())
  New-Item -ItemType Directory -Path $tmp | Out-Null
  try {
    $sums = (Invoke-WebRequest -UseBasicParsing "$dist/SHASUMS256.txt").Content -split "`n"
    $line = $sums | Where-Object { $_ -match "node-v[\d.]+-win-$arch\.zip$" } | Select-Object -First 1
    if (-not $line) { throw "Node.js の配布物が見つかりません（$dist）" }
    $sum, $file = ($line -split '\s+', 2)
    $file = $file.Trim()
    Say "Node.js を入れます: $file → $DataDir\node"
    $zip = Join-Path $tmp $file
    Invoke-WebRequest -UseBasicParsing "$dist/$file" -OutFile $zip
    if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $sum.ToLower()) { throw 'Node.js のチェックサムが合いません' }
    Expand-Archive $zip -DestinationPath $tmp -Force
    $nodeDir = Join-Path $DataDir 'node'
    if (Test-Path $nodeDir) { Remove-Item -Recurse -Force $nodeDir }
    Move-Item (Join-Path $tmp ($file -replace '\.zip$', '')) $nodeDir
  } finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  }
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# 1. Node.js
$sys = (Get-Command node -ErrorAction SilentlyContinue)
$local = Join-Path $DataDir 'node\node.exe'
if ($sys -and (Test-Node $sys.Source)) { $Node = $sys.Source }
elseif (Test-Node $local) { $Node = $local }
else { Install-Node; $Node = $local }
$NodeDir = Split-Path -Parent $Node
$env:Path = "$NodeDir;$env:Path"
$Npm = Join-Path $NodeDir 'npm.cmd'
$Npx = Join-Path $NodeDir 'npx.cmd'
if (-not (Test-Path $Npm)) { $Npm = (Get-Command npm -ErrorAction Stop).Source }
if (-not (Test-Path $Npx)) { $Npx = (Get-Command npx -ErrorAction Stop).Source }
Say "Node.js $(& $Node --version)（$Node）"

# 2. npm パッケージ
Say "npm パッケージを入れます（$ToolDir\node_modules）"
Push-Location $ToolDir
try {
  & $Npm install --omit=dev --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw 'npm install に失敗しました' }

  # 3. ブラウザ
  if ($SkipBrowser) {
    Warn "ブラウザは入れません（-SkipBrowser）。使う前に: cd $ToolDir; npx playwright install chromium"
  } else {
    Say 'Playwright の Chromium を入れます'
    & $Npx playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Chromium を入れられませんでした' }
  }
} finally { Pop-Location }

# 4. webui-test コマンド
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$cmd = Join-Path $BinDir 'webui-test.cmd'
# .cmd はコンソールのコードページで読まれる。ユーザー名などに日本語が入っても壊れないよう、
# 既知のフォルダは環境変数に置き換え、残りは OEM コードページで書く。
function To-CmdPath($p) {
  foreach ($v in @('LOCALAPPDATA', 'APPDATA', 'USERPROFILE')) {
    $base = [Environment]::GetEnvironmentVariable($v)
    if ($base -and $p.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) { return "%$v%" + $p.Substring($base.Length) }
  }
  return $p
}
$oem = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)
$body = "@echo off`r`nrem written by install.ps1`r`n`"$(To-CmdPath $Node)`" `"$(To-CmdPath (Join-Path $ToolDir 'bin\webui-test.js'))`" %*`r`n"
[IO.File]::WriteAllText($cmd, $body, $oem)
Say "webui-test コマンドを置きました: $cmd"
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $BinDir)) {
  [Environment]::SetEnvironmentVariable('Path', (@($userPath, $BinDir) | Where-Object { $_ }) -join ';', 'User')
  Warn "PATH に $BinDir を足しました。新しいターミナルから使えます"
}
$env:Path = "$BinDir;$env:Path"

# 5. エージェント CLI（テストケースを作るときに使う）
foreach ($c in @('kiro-cli', 'copilot')) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { Say "${c}: あり" } else { Warn "$c が見つかりません（webui-test generate で使うときに入れてください）" }
}

# 6. 確かめる
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
