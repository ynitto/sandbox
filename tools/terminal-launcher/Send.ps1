param(
    [string]$ConfigPath = "./config.json",
    [string]$Name = "",
    [string]$CmdArgs = ""
)

if (!(Test-Path $ConfigPath)) {
    Write-Error "Config file not found: $ConfigPath"
    exit 1
}

$config = Get-Content $ConfigPath | ConvertFrom-Json

# --- config.json からエントリを取得 ---
$allEntries = $config.entries

# --- Name 指定時は該当エントリのみ、未指定時は manual エントリを除外 ---
if ($Name) {
    $entries = $allEntries | Where-Object { $_.title -eq $Name }
    if ($entries.Count -eq 0) {
        Write-Warning "No entry found with title: $Name"
        exit 0
    }
} else {
    $entries = $allEntries | Where-Object {
        $launchMode = if ($_.launch) { $_.launch } else { "auto" }
        $launchMode -ne "manual"
    }
}

# --- WSLウォームアップ ---
if ($entries | Where-Object { $_.type -eq "wsl" }) {
    wsl -e true
    Start-Sleep -Seconds 2
}

# --- スクリプト出力先 ---
$tmpDir = Join-Path $env:TEMP "terminal-launcher"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

# --- スタガー設定 ---
$slidingWindow = if ($null -ne $config.slidingWindow) { [int]$config.slidingWindow } else { 0 }
$stagger = if ($null -ne $config.stagger) { [bool]$config.stagger } else { $true }
$entryCount = @($entries).Count
# 起動するエントリ数が 2 以上かつ stagger 有効時のみ間隔を計算
$staggerMs = if ($stagger -and $entryCount -gt 1 -and $slidingWindow -gt 0) {
    [int]($slidingWindow / $entryCount)
} else { 0 }

$index = 0

# --- エントリごとの有効モードを決定し、wt / direct に振り分け ---
$wtParts = @()
$directEntries = @()

foreach ($entry in $entries) {
    $entryMode = if ($entry.mode) { $entry.mode } else { "direct" }
    if ($entryMode -eq "wt") {
        $wtParts += [PSCustomObject]@{ entry = $entry; index = $index }
    } else {
        $directEntries += [PSCustomObject]@{ entry = $entry; index = $index }
    }
    $index++
}

# --- wt モード: Windows Terminal タブで起動 ---
if ($wtParts.Count -gt 0) {

    $parts = @()

    foreach ($item in $wtParts) {
        $entry = $item.entry
        $i = $item.index
        $safeTitle = ($entry.title -replace "[^a-zA-Z0-9_-]", "_")
        $sleepMs = $i * $staggerMs

        switch ($entry.type) {

            "wsl" {
                $scriptPath = Join-Path $tmpDir "$i-$safeTitle.sh"
                $wslUser = if ($entry.user) { $entry.user } else { "" }
                $fullCmd = if ($CmdArgs) { "$($entry.cmd) $CmdArgs" } else { $entry.cmd }
                $sleepSec = ($sleepMs / 1000.0).ToString("0.###", [System.Globalization.CultureInfo]::InvariantCulture)
                $sleepLine = if ($sleepMs -gt 0) { "sleep $sleepSec" } else { "" }

                if ($sleepLine) {
@"
$sleepLine
source ~/.bashrc && cd $($entry.dir) && $fullCmd
exec bash
"@ | Out-File -Encoding utf8 $scriptPath
                } else {
@"
source ~/.bashrc && cd $($entry.dir) && $fullCmd
exec bash
"@ | Out-File -Encoding utf8 $scriptPath
                }

                $wslTabArgs = "wsl -d $($entry.distro)"
                if ($wslUser) { $wslTabArgs += " -u $wslUser" }
                $wslTabArgs += " -- bash `"$scriptPath`""

                $parts += "new-tab --title `"$($entry.title)`" $wslTabArgs"
            }

            "cmd" {
                $scriptPath = Join-Path $tmpDir "$i-$safeTitle.cmd"
                $fullCmd = if ($CmdArgs) { "$($entry.cmd) $CmdArgs" } else { $entry.cmd }
                $sleepSec = [int]([Math]::Ceiling($sleepMs / 1000.0))
                $sleepLine = if ($sleepSec -gt 0) { "timeout /t $sleepSec /nobreak > nul" } else { "" }

                if ($sleepLine) {
@"
$sleepLine
cd /d "$($entry.dir)"
$fullCmd
"@ | Out-File -Encoding ascii $scriptPath
                } else {
@"
cd /d "$($entry.dir)"
$fullCmd
"@ | Out-File -Encoding ascii $scriptPath
                }

                $parts += "new-tab --title `"$($entry.title)`" cmd /k `"$scriptPath`""
            }

            "powershell" {
                $scriptPath = Join-Path $tmpDir "$i-$safeTitle.ps1"
                $fullCmd = if ($CmdArgs) { "$($entry.cmd) $CmdArgs" } else { $entry.cmd }
                $sleepLine = if ($sleepMs -gt 0) { "Start-Sleep -Milliseconds $sleepMs" } else { "" }

                if ($sleepLine) {
@"
$sleepLine
Set-Location "$($entry.dir)"
$fullCmd
"@ | Out-File -Encoding utf8 $scriptPath
                } else {
@"
Set-Location "$($entry.dir)"
$fullCmd
"@ | Out-File -Encoding utf8 $scriptPath
                }

                $parts += "new-tab --title `"$($entry.title)`" powershell -NoExit -File `"$scriptPath`""
            }

            default {
                Write-Warning "Unknown type: $($entry.type)"
            }
        }
    }

    $wtArgs = $parts -join " ; "

    Write-Host "Generated scripts in: $tmpDir"
    Write-Host "Arguments:"
    Write-Host $wtArgs

    Start-Process "wt" -ArgumentList $wtArgs
}

# --- direct モード: プロセス直接起動 ---
if ($directEntries.Count -gt 0) {

    foreach ($item in $directEntries) {
        $entry = $item.entry
        $i = $item.index
        $safeTitle = ($entry.title -replace "[^a-zA-Z0-9_-]", "_")
        $sleepMs = $i * $staggerMs

        switch ($entry.type) {

            "wsl" {
                $wslUser = if ($entry.user) { $entry.user } else { "" }
                $fullCmd = if ($CmdArgs) { "$($entry.cmd) $CmdArgs" } else { $entry.cmd }
                $sleepSec = ($sleepMs / 1000.0).ToString("0.###", [System.Globalization.CultureInfo]::InvariantCulture)
                $bashCmd = if ($sleepMs -gt 0) {
                    "sleep $sleepSec && source ~/.bashrc && cd $($entry.dir) && $fullCmd"
                } else {
                    "source ~/.bashrc && cd $($entry.dir) && $fullCmd"
                }

                $wslArgs = @("-d", $entry.distro)
                if ($wslUser) { $wslArgs += @("-u", $wslUser) }
                $wslArgs += @("--", "bash", "-lc", $bashCmd)

                Write-Host "[$($entry.title)] wsl $($wslArgs -join ' ')"
                Start-Process "wsl" -ArgumentList $wslArgs
            }

            "cmd" {
                $scriptPath = Join-Path $tmpDir "$i-$safeTitle.cmd"
                $fullCmd = if ($CmdArgs) { "$($entry.cmd) $CmdArgs" } else { $entry.cmd }
                $sleepSec = [int]([Math]::Ceiling($sleepMs / 1000.0))
                $sleepLine = if ($sleepSec -gt 0) { "timeout /t $sleepSec /nobreak > nul" } else { "" }

                if ($sleepLine) {
@"
$sleepLine
cd /d "$($entry.dir)"
$fullCmd
"@ | Out-File -Encoding ascii $scriptPath
                } else {
@"
cd /d "$($entry.dir)"
$fullCmd
"@ | Out-File -Encoding ascii $scriptPath
                }

                Write-Host "[$($entry.title)] cmd /c `"$scriptPath`""
                Start-Process "cmd" -ArgumentList "/c", $scriptPath
            }

            "powershell" {
                $scriptPath = Join-Path $tmpDir "$i-$safeTitle.ps1"
                $fullCmd = if ($CmdArgs) { "$($entry.cmd) $CmdArgs" } else { $entry.cmd }
                $sleepLine = if ($sleepMs -gt 0) { "Start-Sleep -Milliseconds $sleepMs" } else { "" }

                if ($sleepLine) {
@"
$sleepLine
Set-Location "$($entry.dir)"
$fullCmd
"@ | Out-File -Encoding utf8 $scriptPath
                } else {
@"
Set-Location "$($entry.dir)"
$fullCmd
"@ | Out-File -Encoding utf8 $scriptPath
                }

                Write-Host "[$($entry.title)] powershell -File `"$scriptPath`""
                Start-Process "powershell" -ArgumentList "-NonInteractive", "-File", $scriptPath
            }

            default {
                Write-Warning "Unknown type: $($entry.type)"
            }
        }
    }

    Write-Host "Generated scripts in: $tmpDir"
}
