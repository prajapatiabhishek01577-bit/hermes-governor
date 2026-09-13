[CmdletBinding()]
param(
    [string]$HermesHome = (Join-Path $env:LOCALAPPDATA 'hermes'),
    [string]$RepoRoot,
    [switch]$RunTests,
    [string]$TestPython,
    [string]$RollbackBackup
)

$ErrorActionPreference = 'Stop'
$ExpectedCommit = 'f377140e3ddb4c98a9e0b42c4497b8bb46ca697c'
$ExpectedVersion = '0.20.5'
$PackageRoot = Split-Path -Parent $PSScriptRoot
$PatchPath = Join-Path $PackageRoot 'patches\hermes-0.20.5-governor-host.patch'
$PluginSource = Join-Path $PackageRoot 'governor'
$SoulAppend = Join-Path $PackageRoot 'soul\GOVERNOR-PROTOCOL.append.md'
$ConfigScript = Join-Path $PSScriptRoot 'configure_governor.py'
$AffectedPaths = @(
    'agent/chat_completion_helpers.py', 'agent/conversation_loop.py',
    'agent/turn_context.py', 'agent/turn_finalizer.py', 'hermes_cli/plugins.py',
    'run_agent.py', 'agent/plugin_completion_policy.py',
    'tests/agent/test_plugin_completion_policy.py', 'tests/agent/test_governor_stream_buffer.py'
)

function Get-Sha256([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Require-File([string]$Path, [string]$Message) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw $Message }
}

function Require-Directory([string]$Path, [string]$Message) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { throw $Message }
}

function Invoke-Rollback([string]$BackupRoot) {
    $metaPath = Join-Path $BackupRoot 'install-manifest.json'
    Require-File $metaPath 'Rollback manifest not found.'
    $meta = Get-Content -LiteralPath $metaPath -Raw | ConvertFrom-Json
    $repo = [string]$meta.repo_root
    $hermesHomePath = [string]$meta.hermes_home
    Require-Directory $repo 'Recorded Hermes repository is unavailable.'
    Require-Directory $hermesHomePath 'Recorded Hermes home is unavailable.'

    foreach ($entry in $meta.post_install_hashes.PSObject.Properties) {
        $current = Join-Path $repo $entry.Name
        if (-not (Test-Path -LiteralPath $current) -or (Get-Sha256 $current) -ne $entry.Value) {
            throw "Refusing rollback: $($entry.Name) changed after installation. Restore manually from $BackupRoot."
        }
    }
    & git -C $repo apply -R --check $PatchPath
    if ($LASTEXITCODE -ne 0) { throw 'Refusing rollback: host patch cannot be reversed cleanly.' }
    & git -C $repo apply -R $PatchPath
    if ($LASTEXITCODE -ne 0) { throw 'Host patch rollback failed.' }

    foreach ($name in @('config.yaml', 'SOUL.md')) {
        $saved = Join-Path $BackupRoot $name
        $property = ($name -replace '\.', '_') + '_existed'
        $destination = Join-Path $hermesHomePath $name
        if ($meta.$property) {
            Require-File $saved "Required rollback copy $name is missing."
            Copy-Item -LiteralPath $saved -Destination $destination -Force
        } elseif (Test-Path -LiteralPath $destination) {
            Move-Item -LiteralPath $destination -Destination (Join-Path $BackupRoot "created-$name-after-rollback")
        }
    }
    $pluginTarget = Join-Path $hermesHomePath 'plugins\hermes-governor'
    if (Test-Path -LiteralPath $pluginTarget) {
        $retired = Join-Path $BackupRoot 'installed-plugin-after-rollback-request'
        Move-Item -LiteralPath $pluginTarget -Destination $retired
    }
    $savedPlugin = Join-Path $BackupRoot 'plugin-before-install'
    if ($meta.plugin_existed) {
        Require-Directory $savedPlugin 'Required prior plugin backup is missing.'
        Move-Item -LiteralPath $savedPlugin -Destination $pluginTarget
    }
    Write-Host "Rolled back Governor from $BackupRoot. Restart Hermes before using it."
    return
}

if ($RollbackBackup) { Invoke-Rollback (Resolve-Path -LiteralPath $RollbackBackup).Path; exit 0 }

$HermesHome = [IO.Path]::GetFullPath($HermesHome)
if (-not $RepoRoot) { $RepoRoot = Join-Path $HermesHome 'hermes-agent' }
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$Launcher = Join-Path $HermesHome 'bin\hermes.exe'
$Python = Join-Path $RepoRoot 'venv\Scripts\python.exe'
Require-Directory $HermesHome 'Hermes home was not found. Pass -HermesHome explicitly.'
Require-Directory $RepoRoot 'Hermes repository was not found. Pass -RepoRoot explicitly.'
Require-File $Launcher 'Normal Hermes launcher was not found under Hermes home.'
Require-File $Python 'Hermes virtual-environment Python was not found.'
Require-File $PatchPath 'Governor host patch is missing from this package.'
Require-Directory $PluginSource 'Governor plugin source is missing from this package.'
Require-File $SoulAppend 'Governor SOUL append is missing from this package.'

$insideWorkTree = (& git -C $RepoRoot rev-parse --is-inside-work-tree 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $insideWorkTree -ne 'true') { throw 'Target repository is not a Git checkout.' }
$commit = (& git -C $RepoRoot rev-parse HEAD).Trim()
if ($commit -ne $ExpectedCommit) { throw "Unsupported Hermes commit $commit. Expected $ExpectedCommit; no changes were made." }
$versionLine = Select-String -LiteralPath (Join-Path $RepoRoot 'hermes_cli\__init__.py') -Pattern '__version__ = "0.20.5"' -Quiet
if (-not $versionLine) { throw "Unsupported Hermes version. Expected $ExpectedVersion; no changes were made." }
$affectedStatus = @(& git -C $RepoRoot status --porcelain -- $AffectedPaths)
if ($LASTEXITCODE -ne 0 -or $affectedStatus.Count -ne 0) { throw 'Affected Hermes files already have local or untracked changes. Refusing to overwrite them.' }
& git -C $RepoRoot apply --check --whitespace=nowarn $PatchPath
if ($LASTEXITCODE -ne 0) { throw 'Governor patch is not compatible with this checkout. No changes were made.' }

$pluginTarget = Join-Path $HermesHome 'plugins\hermes-governor'
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
$backupRoot = Join-Path $HermesHome "governor-backups\$stamp"
New-Item -ItemType Directory -Path $backupRoot | Out-Null
foreach ($name in @('config.yaml', 'SOUL.md')) {
    $source = Join-Path $HermesHome $name
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $backupRoot $name) }
}
$configExisted = Test-Path -LiteralPath (Join-Path $HermesHome 'config.yaml')
$soulExisted = Test-Path -LiteralPath (Join-Path $HermesHome 'SOUL.md')
$pluginExisted = Test-Path -LiteralPath $pluginTarget
$hostBackup = Join-Path $backupRoot 'host-before'
foreach ($relative in $AffectedPaths) {
    $source = Join-Path $RepoRoot $relative
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        $destination = Join-Path $hostBackup $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination
    }
}

try {
    if ($pluginExisted) { Move-Item -LiteralPath $pluginTarget -Destination (Join-Path $backupRoot 'plugin-before-install') }
    & git -C $RepoRoot apply --whitespace=nowarn $PatchPath
    if ($LASTEXITCODE -ne 0) { throw 'Host patch application failed.' }
    Copy-Item -LiteralPath $PluginSource -Destination $pluginTarget -Recurse

    $soulTarget = Join-Path $HermesHome 'SOUL.md'
    $append = Get-Content -LiteralPath $SoulAppend -Raw
    $soul = if (Test-Path -LiteralPath $soulTarget) { Get-Content -LiteralPath $soulTarget -Raw } else { '' }
    if ($soul -notmatch '<!-- HERMES-GOVERNOR-PROTOCOL v1 START -->') {
        $soulSeparator = if ($soul) { "`r`n" } else { '' }
        [IO.File]::AppendAllText($soulTarget, ($soulSeparator + $append), [Text.UTF8Encoding]::new($false))
    }

    & $Python $ConfigScript --config (Join-Path $HermesHome 'config.yaml')
    if ($LASTEXITCODE -ne 0) { throw 'Config update failed.' }

    $post = [ordered]@{}
    foreach ($relative in $AffectedPaths) { $post[$relative] = Get-Sha256 (Join-Path $RepoRoot $relative) }
    [ordered]@{
        package_version = '1.0.0'; installed_at = (Get-Date).ToUniversalTime().ToString('o')
        hermes_home = $HermesHome; repo_root = $RepoRoot; commit = $ExpectedCommit
        config_yaml_existed = $configExisted; SOUL_md_existed = $soulExisted
        plugin_existed = $pluginExisted; post_install_hashes = $post
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $backupRoot 'install-manifest.json') -Encoding utf8

    $diagnostic = & $Launcher governor --diagnose 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Governor diagnostic failed: $diagnostic" }
    $diagnosticText = $diagnostic -join [Environment]::NewLine
    $jsonStart = $diagnosticText.IndexOf('{')
    if ($jsonStart -lt 0) { throw "Governor diagnostic returned no JSON: $diagnosticText" }
    $report = $diagnosticText.Substring($jsonStart) | ConvertFrom-Json
    $hookValues = @($report.hooks.PSObject.Properties | ForEach-Object { $_.Value })
    $diagnosedHome = [IO.Path]::GetFullPath([string]$report.home)
    if ($diagnosedHome -ne $HermesHome) { throw "Launcher diagnosed $diagnosedHome instead of target home $HermesHome." }
    if (-not $report.tool_registered -or -not $report.soul_loaded_in_full -or -not $report.settings.enabled -or -not $report.settings.require_verification -or $hookValues -contains $false) {
        throw 'Governor diagnostics did not confirm a complete activation.'
    }
    if ($RunTests) {
        Require-File $TestPython 'Use -TestPython with a Python environment containing Hermes dependencies and pytest.'
        $gitBashPath = Join-Path $env:ProgramFiles 'Git\bin\bash.exe'
        $bash = if (Test-Path -LiteralPath $gitBashPath -PathType Leaf) {
            Get-Item -LiteralPath $gitBashPath
        } else {
            Get-Command bash -ErrorAction Stop
        }
        $priorHermesPython = $env:HERMES_PYTHON
        try {
            $env:HERMES_PYTHON = [IO.Path]::GetFullPath($TestPython)
            Push-Location $RepoRoot
            try {
                & $bash.FullName 'scripts/run_tests.sh' 'tests/agent/test_plugin_completion_policy.py' 'tests/agent/test_governor_stream_buffer.py'
            } finally {
                Pop-Location
            }
            if ($LASTEXITCODE -ne 0) { throw 'Requested host regression tests failed.' }
        } finally {
            $env:HERMES_PYTHON = $priorHermesPython
        }
    }
    Write-Host "Governor installed and verified. Backup: $backupRoot"
    Write-Host "Rollback: .\Install-HermesGovernor.ps1 -RollbackBackup '$backupRoot'"
} catch {
    # Best-effort automatic restoration. Preserve every displaced file in the
    # timestamped backup; never touch auth.json, .env files, or credentials.
    & git -C $RepoRoot apply -R --check $PatchPath 2>$null
    if ($LASTEXITCODE -eq 0) { & git -C $RepoRoot apply -R $PatchPath | Out-Null }
    foreach ($name in @('config.yaml', 'SOUL.md')) {
        $saved = Join-Path $backupRoot $name
        $destination = Join-Path $HermesHome $name
        if (Test-Path -LiteralPath $saved) {
            Copy-Item -LiteralPath $saved -Destination $destination -Force
        } elseif (Test-Path -LiteralPath $destination) {
            Move-Item -LiteralPath $destination -Destination (Join-Path $backupRoot "created-$name-after-failed-install")
        }
    }
    if (Test-Path -LiteralPath $pluginTarget) {
        Move-Item -LiteralPath $pluginTarget -Destination (Join-Path $backupRoot 'plugin-after-failed-install')
    }
    $savedPlugin = Join-Path $backupRoot 'plugin-before-install'
    if (Test-Path -LiteralPath $savedPlugin) { Move-Item -LiteralPath $savedPlugin -Destination $pluginTarget }
    Write-Error "Governor installation did not complete. Backups remain at $backupRoot. No credentials were read, copied, or changed. $_"
    throw
}
