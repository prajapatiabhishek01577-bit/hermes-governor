[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$HermesRepo,
    [Parameter(Mandatory = $true)]
    [string]$TestPython
)

$ErrorActionPreference = 'Stop'
$PackageRoot = Split-Path -Parent $PSScriptRoot
$HermesRepo = [IO.Path]::GetFullPath($HermesRepo)
$TestPython = [IO.Path]::GetFullPath($TestPython)
$Runner = Join-Path $HermesRepo 'scripts\run_tests.sh'
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) { throw 'Hermes test runner not found.' }
if (-not (Test-Path -LiteralPath $TestPython -PathType Leaf)) { throw 'Test Python not found.' }
$gitBashPath = Join-Path $env:ProgramFiles 'Git\bin\bash.exe'
$bash = if (Test-Path -LiteralPath $gitBashPath -PathType Leaf) {
    Get-Item -LiteralPath $gitBashPath
} else {
    Get-Command bash -ErrorAction Stop
}
function ConvertTo-GitBashPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -notmatch '^[A-Za-z]:\\') { throw "Expected an absolute Windows path: $full" }
    '/' + $full.Substring(0, 1).ToLowerInvariant() + '/' + $full.Substring(3).Replace('\', '/')
}
$runnerBash = ConvertTo-GitBashPath $Runner
$governorTests = ConvertTo-GitBashPath (Join-Path $PackageRoot 'governor\tests')
$hostTests = ConvertTo-GitBashPath (Join-Path $PackageRoot 'tests\host')
$installerTests = ConvertTo-GitBashPath (Join-Path $PackageRoot 'tests\installer')
$pytestConfig = ConvertTo-GitBashPath (Join-Path $PackageRoot 'pytest.ini')
$priorHermesPython = $env:HERMES_PYTHON
try {
    $env:HERMES_PYTHON = $TestPython
    & $bash.FullName $runnerBash `
        $governorTests `
        $hostTests `
        $installerTests `
        -j 2 --file-timeout 180 --file-retries 0 -- `
        -c $pytestConfig --tb=short
    if ($LASTEXITCODE -ne 0) { throw 'Portable package tests failed.' }
} finally {
    $env:HERMES_PYTHON = $priorHermesPython
}
