param(
    [string]$ConfigPath = "./config.json",
    [ValidateSet("", "direct", "wt")]
    [string]$Mode = ""
)

if (!(Test-Path $ConfigPath)) {
    Write-Error "Config file not found: $ConfigPath"
    exit 1
}

$config = Get-Content $ConfigPath | ConvertFrom-Json

# --- config.json からエントリとモードを取得 ---
$entries = $config.entries
$configMode = if ($config.mode) { $config.mode } else { "" }

# -Mode パラメータが明示指定されていればそちらを優先、なければ config の mode、なければ "direct"
if ($Mode -eq "") {
    $Mode = if ($configMode -ne "") { $configMode } else { "direct" }
}

# --- WSLウォームアップ ---
if ($entries | Where-Object { $_.type -eq "wsl" }) {
    wsl -e true
    Start-Sleep -Seconds 2
}

# --- スクリプト出力先 ---
$tmpDir = Join-Path $env:TEMP "wsl-launcher"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

$index = 0

if ($Mode -eq "wt") {

    # --- wt モード: Windows Terminal タブで起動 ---
    $parts = @()

    foreach ($entry in $entries) {

        $safeTitle = ($entry.title -replace "[^a-zA-Z0-9_-]", "_")

        switch ($entry.type) {

            "wsl" {
                $scriptPath = Join-Path $tmpDir "$index-$safeTitle.sh"
                $wslUser = if ($entry.user) { $entry.user } else { "" }

@"
source ~/.bashrc && cd $($entry.dir) && $($entry.cmd)
exec bash
"@ | Out-File -Encoding utf8 $scriptPath

                $wslTabArgs = "wsl -d $($entry.distro)"
                if ($wslUser) { $wslTabArgs += " -u $wslUser" }
                $wslTabArgs += " -- bash `"$scriptPath`""

                $parts += "new-tab --title `"$($entry.title)`" $wslTabArgs"
            }

            "cmd" {
                $scriptPath = Join-Path $tmpDir "$index-$safeTitle.cmd"

@"
cd /d "$($entry.dir)"
$($entry.cmd)
"@ | Out-File -Encoding ascii $scriptPath

                $parts += "new-tab --title `"$($entry.title)`" cmd /k `"$scriptPath`""
            }

            "powershell" {
                $scriptPath = Join-Path $tmpDir "$index-$safeTitle.ps1"

@"
Set-Location "$($entry.dir)"
$($entry.cmd)
"@ | Out-File -Encoding utf8 $scriptPath

                $parts += "new-tab --title `"$($entry.title)`" powershell -NoExit -File `"$scriptPath`""
            }

            default {
                Write-Warning "Unknown type: $($entry.type)"
            }
        }

        $index++
    }

    $wtArgs = $parts -join " ; "

    Write-Host "Generated scripts in: $tmpDir"
    Write-Host "Arguments:"
    Write-Host $wtArgs

    Start-Process "wt" -ArgumentList $wtArgs

} else {

    # --- direct モード: プロセス直接起動 ---
    foreach ($entry in $entries) {

        $safeTitle = ($entry.title -replace "[^a-zA-Z0-9_-]", "_")

        switch ($entry.type) {

            "wsl" {
                $wslUser = if ($entry.user) { $entry.user } else { "" }
                $bashCmd = "source ~/.bashrc && cd $($entry.dir) && $($entry.cmd)"

                $wslArgs = @("-d", $entry.distro)
                if ($wslUser) { $wslArgs += @("-u", $wslUser) }
                $wslArgs += @("--", "bash", "-lc", $bashCmd)

                Write-Host "[$($entry.title)] wsl $($wslArgs -join ' ')"
                Start-Process "wsl" -ArgumentList $wslArgs
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

}
