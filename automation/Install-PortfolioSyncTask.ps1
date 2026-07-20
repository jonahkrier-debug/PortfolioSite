[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateRange(1, 60)]
    [int]$IntervalMinutes = 2,
    [switch]$EnableGitPublish
)

$ErrorActionPreference = "Stop"
$taskName = "Darktable Portfolio Sync"
$syncScript = Join-Path $PSScriptRoot "Sync-Portfolio.ps1"
$hiddenLauncher = Join-Path $PSScriptRoot "Start-PortfolioSyncHidden.vbs"
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$wscript = (Get-Command wscript.exe -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $syncScript -PathType Leaf)) {
    throw "Sync wrapper not found: $syncScript"
}
if (-not (Test-Path -LiteralPath $hiddenLauncher -PathType Leaf)) {
    throw "Hidden sync launcher not found: $hiddenLauncher"
}

# Installation is intentionally blocked until the required tag set is valid.
& $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $syncScript -DryRun
if ($LASTEXITCODE -ne 0) {
    throw "Dry-run validation failed. Create and populate the portfolio tags in darktable, then retry."
}

$taskArguments = "`"$hiddenLauncher`""
if ($EnableGitPublish) {
    $taskArguments += " -Publish"
}

$action = New-ScheduledTaskAction -Execute $wscript -Argument $taskArguments -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$trigger.Repetition.StopAtDurationEnd = $false
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Export darktable portfolio tags and update the local portfolio site."

if ($PSCmdlet.ShouldProcess($taskName, "Register recurring portfolio sync task")) {
    Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
    Write-Output "Installed '$taskName' (every $IntervalMinutes minute(s))."
    if ($EnableGitPublish) {
        Write-Output "Automatic commit and push to origin/main is enabled."
    } else {
        Write-Output "Local sync is enabled; Git publishing is disabled."
    }
}
