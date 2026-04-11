param(
    [string]$ConfigPath = "./config.json"
)

if (!(Test-Path $ConfigPath)) {
    Write-Error "Config file not found: $ConfigPath"
    exit 1
}

$commands = Get-Content $ConfigPath | ConvertFrom-Json

# --- WSLウォームアップ ---
if ($commands | Where-Object { $_.type -eq "wsl" }) {
    wsl -e true
    Start-Sleep -Seconds 2
}

# --- スクリプト出力先 ---
$tmpDir = Join-Path $env:TEMP "wsl-send"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

$index = 0

foreach ($entry in $commands) {

    $safeTitle = ($entry.title -replace "[^a-zA-Z0-9_-]", "_")

    switch ($entry.type) {

        "wsl" {
            $scriptPath = Join-Path $tmpDir "$index-$safeTitle.sh"

@"
cd $($entry.dir)
$($entry.cmd)
"@ | Out-File -Encoding utf8 $scriptPath

            Write-Host "[$($entry.title)] wsl -d $($entry.distro) -- bash `"$scriptPath`""
            Start-Process "wsl" -ArgumentList "-d", $entry.distro, "--", "bash", $scriptPath
        }

        "cmd" {
            $scriptPath = Join-Path $tmpDir "$index-$safeTitle.cmd"

@"
cd /d "$($entry.dir)"
$($entry.cmd)
"@ | Out-File -Encoding ascii $scriptPath

            Write-Host "[$($entry.title)] cmd /c `"$scriptPath`""
            Start-Process "cmd" -ArgumentList "/c", $scriptPath
        }

        "powershell" {
            $scriptPath = Join-Path $tmpDir "$index-$safeTitle.ps1"

@"
Set-Location "$($entry.dir)"
$($entry.cmd)
"@ | Out-File -Encoding utf8 $scriptPath

            Write-Host "[$($entry.title)] powershell -File `"$scriptPath`""
            Start-Process "powershell" -ArgumentList "-NonInteractive", "-File", $scriptPath
        }

        default {
            Write-Warning "Unknown type: $($entry.type)"
        }
    }

    $index++
}

Write-Host "Generated scripts in: $tmpDir"
