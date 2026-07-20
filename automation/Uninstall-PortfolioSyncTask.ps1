[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = "Stop"
$taskName = "Darktable Portfolio Sync"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Output "'$taskName' is not installed."
    exit 0
}

if ($PSCmdlet.ShouldProcess($taskName, "Unregister portfolio sync task")) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Output "Removed '$taskName'. Generated portfolio files were not deleted."
}
