param(
    [string]$ConfigPath = "./tabs.json"
)

if (!(Test-Path $ConfigPath)) {
    Write-Error "Config file not found: $ConfigPath"
    exit 1
}

$tabs = Get-Content $ConfigPath | ConvertFrom-Json

# --- WSLウォームアップ ---
if ($tabs | Where-Object { $_.type -eq "wsl" }) {
    wsl -e true
    Start-Sleep -Seconds 2
}

# --- スクリプト出力先 ---
$tmpDir = Join-Path $env:TEMP "wt-tabs"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

$parts = @()
$index = 0

foreach ($tab in $tabs) {

    $safeTitle = ($tab.title -replace "[^a-zA-Z0-9_-]", "_")
    $scriptPath = ""

    switch ($tab.type) {

        "cmd" {
            $scriptPath = Join-Path $tmpDir "$index-$safeTitle.cmd"

@"
cd /d "$($tab.dir)"
$($tab.cmd)
"@ | Out-File -Encoding ascii $scriptPath

            $parts += "new-tab --title `"$($tab.title)`" cmd /k `"$scriptPath`""
        }

        "powershell" {
            $scriptPath = Join-Path $tmpDir "$index-$safeTitle.ps1"

@"
Set-Location "$($tab.dir)"
$($tab.cmd)
"@ | Out-File -Encoding utf8 $scriptPath

            $parts += "new-tab --title `"$($tab.title)`" powershell -NoExit -File `"$scriptPath`""
        }

        "wsl" {
            $scriptPath = Join-Path $tmpDir "$index-$safeTitle.sh"

@"
cd $($tab.dir)
$($tab.cmd)
exec bash
"@ | Out-File -Encoding utf8 $scriptPath

            $parts += "new-tab --title `"$($tab.title)`" wsl -d $($tab.distro) -- bash `"$scriptPath`""
        }

        default {
            Write-Warning "Unknown type: $($tab.type)"
        }
    }

    $index++
}

# --- wt引数作成 ---
$wtArgs = $parts -join " ; "

Write-Host "Generated scripts in: $tmpDir"
Write-Host "Arguments:"
Write-Host $wtArgs

# ❌ Invoke-Expressionは使わない
# Invoke-Expression "wt $wtArgs"

# ✅ これが正解（最重要）
Start-Process "wt" -ArgumentList $wtArgs