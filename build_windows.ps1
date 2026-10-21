param(
    [string]$PythonPath = 'python',
    [string]$OutputDirectory = '',
    [switch]$InstallDependencies,
    [string]$SigningCertificateThumbprint = '',
    [string]$SignToolPath = '',
    [uri]$TimestampUrl = 'http://timestamp.digicert.com'
)
$ErrorActionPreference = 'Stop'
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $PSScriptRoot 'dist' }
if ($InstallDependencies) {
    & $PythonPath -m pip install -r (Join-Path $PSScriptRoot 'requirements-build.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
}
& $PythonPath (Join-Path $PSScriptRoot 'make_icon.py')
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed' }
& $PythonPath -m PyInstaller --noconfirm --clean --distpath $OutputDirectory --workpath (Join-Path $PSScriptRoot 'build') (Join-Path $PSScriptRoot 'lingxi.spec')
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
$binary = Join-Path $OutputDirectory 'LingXi\LingXi.exe'
if (-not (Test-Path -LiteralPath $binary)) { throw 'LingXi.exe missing' }
if ($SigningCertificateThumbprint) {
    & (Join-Path $PSScriptRoot 'sign_windows.ps1') -ApplicationPath $binary `
        -CertificateThumbprint $SigningCertificateThumbprint -SignToolPath $SignToolPath -TimestampUrl $TimestampUrl
}
Get-FileHash -LiteralPath $binary -Algorithm SHA256
