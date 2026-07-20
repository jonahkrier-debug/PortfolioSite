[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Scheduled,
    [switch]$Publish,
    [switch]$VerboseLogging
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$syncScript = Join-Path $PSScriptRoot "portfolio_sync.py"
$configPath = Join-Path $PSScriptRoot "portfolio-sync.json"
$stateDir = Join-Path $repoRoot ".portfolio-sync"
$lockPath = Join-Path $stateDir "portfolio-sync.lock"
$manifestPath = Join-Path $repoRoot "data\portfolio-manifest.json"
$python = Get-Command python -ErrorAction Stop

function Read-PortfolioManifest {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Resolve-RepoPath {
    param([string]$RelativePath)
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $RelativePath))
    $prefix = $repoRoot.TrimEnd('\') + '\'
    if (-not $candidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Manifest path escapes the repository: $RelativePath"
    }
    return $candidate
}

function Get-ManifestManagedPaths {
    param($Manifest)
    if ($null -eq $Manifest) {
        return @()
    }
    $paths = [System.Collections.Generic.List[string]]::new()
    foreach ($entry in @($Manifest.owned_files)) {
        if ($entry) { $paths.Add([string]$entry) }
    }
    foreach ($entry in @($Manifest.retained_files)) {
        if ($entry.path) { $paths.Add([string]$entry.path) }
    }
    foreach ($property in @($Manifest.text_sha256.PSObject.Properties)) {
        $paths.Add([string]$property.Name)
    }
    return $paths
}

function Assert-ManifestContent {
    param($Manifest, [string]$ExpectedManifestHash)
    if ($null -eq $Manifest) {
        throw "Generated portfolio manifest is missing."
    }
    if ((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash -ne $ExpectedManifestHash) {
        throw "The generated manifest changed before Git publication."
    }

    $checks = [System.Collections.Generic.Dictionary[string,string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in @($Manifest.assets)) {
        $checks[[string]$entry.path] = [string]$entry.sha256
    }
    foreach ($entry in @($Manifest.retained_files)) {
        $checks[[string]$entry.path] = [string]$entry.sha256
    }
    foreach ($property in @($Manifest.text_sha256.PSObject.Properties)) {
        $checks[[string]$property.Name] = [string]$property.Value
    }

    foreach ($pair in $checks.GetEnumerator()) {
        $fullPath = Resolve-RepoPath $pair.Key
        if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
            throw "Managed file disappeared before Git publication: $($pair.Key)"
        }
        $actual = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash
        if ($actual -ne $pair.Value) {
            throw "Managed file changed before Git publication: $($pair.Key)"
        }
    }
}

function Invoke-PortfolioSync {
    $priorManifest = $null
    $git = $null
    $gitHeadBefore = $null
    $gitRemoteBefore = $null
    if ($Publish) {
        $git = Get-Command git -ErrorAction Stop
        $branch = (& $git.Source -C $repoRoot branch --show-current).Trim()
        if ($LASTEXITCODE -ne 0 -or $branch -ne "main") {
            throw "Automatic publishing requires the local main branch. Current branch: '$branch'."
        }
        $before = @(& $git.Source -C $repoRoot status --porcelain)
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect the Git worktree."
        }
        if ($before.Count -gt 0) {
            throw "Automatic publishing requires a clean Git worktree. Commit or stash local changes first."
        }
        $upstream = (& $git.Source -C $repoRoot rev-parse --abbrev-ref --symbolic-full-name '@{u}').Trim()
        if ($LASTEXITCODE -ne 0 -or $upstream -ne "origin/main") {
            throw "Automatic publishing requires main to track origin/main."
        }
        $alignment = ((& $git.Source -C $repoRoot rev-list --left-right --count 'HEAD...origin/main').Trim() -split '\s+')
        if ($LASTEXITCODE -ne 0 -or $alignment.Count -ne 2) {
            throw "Unable to compare main with origin/main."
        }
        if ([int]$alignment[0] -ne 0 -or [int]$alignment[1] -ne 0) {
            throw "Automatic publishing requires main and origin/main to be aligned (no ahead/behind commits)."
        }
        $gitHeadBefore = (& $git.Source -C $repoRoot rev-parse HEAD).Trim()
        $gitRemoteBefore = (& $git.Source -C $repoRoot rev-parse origin/main).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to capture the Git publication baseline."
        }
        $priorManifest = Read-PortfolioManifest $manifestPath
    }

    $arguments = @($syncScript, "--config", $configPath, "--no-lock")
    if ($DryRun) {
        $arguments += "--dry-run"
    }
    if ($VerboseLogging) {
        $arguments += "--verbose"
    }

    & $python.Source @arguments | Out-Host
    $syncExitCode = $LASTEXITCODE
    if ($syncExitCode -ne 0) {
        if ($Scheduled -and $syncExitCode -in @(4, 5, 6)) {
            Write-Host "Portfolio sync was safely deferred; the scheduled task will retry."
            return 0
        }
        return $syncExitCode
    }

    if (-not $Publish -or $DryRun) {
        return 0
    }

    $manifest = Read-PortfolioManifest $manifestPath
    if ($null -eq $manifest) {
        throw "A successful sync did not produce data\portfolio-manifest.json."
    }
    $manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
    Assert-ManifestContent $manifest $manifestHash

    $branchAfter = (& $git.Source -C $repoRoot branch --show-current).Trim()
    $headAfter = (& $git.Source -C $repoRoot rev-parse HEAD).Trim()
    $remoteAfter = (& $git.Source -C $repoRoot rev-parse origin/main).Trim()
    if (
        $LASTEXITCODE -ne 0 -or
        $branchAfter -ne "main" -or
        $headAfter -ne $gitHeadBefore -or
        $remoteAfter -ne $gitRemoteBefore
    ) {
        throw "Git branch/HEAD changed during portfolio generation; publication was aborted."
    }

    $managedPaths = @(
        "data/portfolio-manifest.json"
        Get-ManifestManagedPaths $priorManifest
        Get-ManifestManagedPaths $manifest
    ) | Where-Object { $_ } | Sort-Object -Unique

    $changes = @(& $git.Source -C $repoRoot status --porcelain -- @managedPaths)
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect generated Git changes."
    }

    if ($changes.Count -gt 0) {
        & $git.Source -C $repoRoot add --all -- @managedPaths
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to stage generated portfolio changes."
        }
        try {
            Assert-ManifestContent $manifest $manifestHash
            & $git.Source -C $repoRoot diff --quiet -- @managedPaths
            if ($LASTEXITCODE -eq 1) {
                throw "A managed file changed while Git publication was being prepared."
            }
            if ($LASTEXITCODE -gt 1) {
                throw "Unable to verify the managed Git worktree."
            }
        } catch {
            & $git.Source -C $repoRoot restore --staged -- @managedPaths 2>$null
            throw
        }

        & $git.Source -C $repoRoot diff --cached --quiet -- @managedPaths
        if ($LASTEXITCODE -eq 1) {
            & $git.Source -C $repoRoot commit -m "Sync portfolio from darktable tags" | Out-Host
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to commit generated portfolio changes."
            }
        } elseif ($LASTEXITCODE -gt 1) {
            throw "Unable to inspect staged portfolio changes."
        }
    }

    & $git.Source -C $repoRoot push origin main | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Portfolio changes are committed locally, but the push to origin/main failed."
    }
    return 0
}

New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open(
        $lockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::ReadWrite
    )
    if ($lockStream.Length -eq 0) {
        $lockStream.SetLength(1)
    }
    $lockStream.Lock(0, 1)
} catch [System.IO.IOException] {
    if ($lockStream) { $lockStream.Dispose() }
    if ($Scheduled) {
        Write-Output "Another portfolio sync is already running; this scheduled run was skipped."
        exit 0
    }
    throw "Another portfolio sync is already running."
}

try {
    $result = Invoke-PortfolioSync
} finally {
    if ($lockStream) {
        try { $lockStream.Unlock(0, 1) } catch { }
        $lockStream.Dispose()
    }
}

exit $result
