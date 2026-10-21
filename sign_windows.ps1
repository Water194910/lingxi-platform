param(
    [Parameter(Mandatory=$true)][string]$ApplicationPath,
    [Parameter(Mandatory=$true)][string]$CertificateThumbprint,
    [string]$SignToolPath = '',
    [uri]$TimestampUrl = 'http://timestamp.digicert.com'
)

# 使用已安装且带私钥的可信代码签名证书；不生成证书、不改变系统信任。
$ErrorActionPreference = 'Stop'
$application = (Resolve-Path -LiteralPath $ApplicationPath).Path
if ([IO.Path]::GetFileName($application) -ne 'LingXi.exe') { throw 'Only LingXi.exe can be signed.' }
$thumbprint = ($CertificateThumbprint -replace '\s', '').ToUpperInvariant()
if ($thumbprint -notmatch '^[0-9A-F]{40}$') { throw 'Expected a 40-character certificate thumbprint.' }
if ($TimestampUrl.Scheme -notin @('http','https') -or -not $TimestampUrl.Host -or $TimestampUrl.UserInfo) {
    throw 'Invalid RFC3161 timestamp URL.'
}
$certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$thumbprint" -ErrorAction Stop
if (-not $certificate.HasPrivateKey) { throw 'Certificate has no accessible private key.' }
$now = Get-Date
if ($now -lt $certificate.NotBefore -or $now -gt $certificate.NotAfter) { throw 'Certificate is not currently valid.' }
$codeSigningOid = '1.3.6.1.5.5.7.3.3'
$ekuExtension = $certificate.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.37' }
if (-not @($ekuExtension.EnhancedKeyUsages | Where-Object { $_.Value -eq $codeSigningOid }).Count) {
    throw 'Certificate does not permit code signing.'
}
$chain = [Security.Cryptography.X509Certificates.X509Chain]::new()
try {
    $chain.ChainPolicy.ApplicationPolicy.Add([Security.Cryptography.Oid]::new($codeSigningOid))
    $chain.ChainPolicy.UrlRetrievalTimeout = [TimeSpan]::FromSeconds(15)
    if (-not $chain.Build($certificate)) {
        throw ('Certificate chain validation failed: '+(($chain.ChainStatus | ForEach-Object { $_.StatusInformation.Trim() }) -join '; '))
    }
} finally { $chain.Dispose() }

if (-not $SignToolPath) {
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($command) { $SignToolPath = $command.Source }
    else {
        $sdk = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
        if (Test-Path -LiteralPath $sdk) {
            $SignToolPath = Get-ChildItem -Path (Join-Path $sdk '*\x64\signtool.exe') -File |
                Sort-Object { [version]$_.Directory.Parent.Name } -Descending |
                Select-Object -First 1 -ExpandProperty FullName
        }
    }
}
if (-not $SignToolPath) { throw 'SignTool not found. Install Windows SDK Signing Tools or supply -SignToolPath.' }
$signTool = (Resolve-Path -LiteralPath $SignToolPath).Path
if ([IO.Path]::GetFileName($signTool) -ne 'signtool.exe') { throw 'Expected Windows SDK signtool.exe.' }

$before = (Get-FileHash -LiteralPath $application -Algorithm SHA256).Hash
# 不在原EXE上试签。网络/时间戳/校验失败时保留原文件。
$temporaryDirectory = Join-Path ([IO.Path]::GetDirectoryName($application)) ('.lingxi-sign-'+[guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($temporaryDirectory) | Out-Null
$temporaryExe = Join-Path $temporaryDirectory 'LingXi.exe'
try {
    Copy-Item -LiteralPath $application -Destination $temporaryExe
    & $signTool sign /sha1 $thumbprint /s My /fd SHA256 /tr $TimestampUrl.AbsoluteUri /td SHA256 $temporaryExe
    if ($LASTEXITCODE -ne 0) { throw 'SignTool signing or timestamping failed; original EXE was kept.' }
    & $signTool verify /pa /all /v $temporaryExe
    if ($LASTEXITCODE -ne 0) { throw 'Signature verification failed; original EXE was kept.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $temporaryExe
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $thumbprint -or -not $signature.TimeStamperCertificate) {
        throw 'Expected a valid signature with the selected signer and timestamp; original EXE was kept.'
    }
    Copy-Item -LiteralPath $temporaryExe -Destination $application -Force
    [pscustomobject]@{
        Application=$application; Publisher=$certificate.Subject; Status=$signature.Status
        TimestampSigner=$signature.TimeStamperCertificate.Subject
        BeforeSHA256=$before; SignedSHA256=(Get-FileHash -LiteralPath $application -Algorithm SHA256).Hash
    }
} finally {
    if (Test-Path -LiteralPath $temporaryExe) { Remove-Item -LiteralPath $temporaryExe }
    # 只清理本次唯一命名的空目录，不递归删除。
    if (Test-Path -LiteralPath $temporaryDirectory) { [IO.Directory]::Delete($temporaryDirectory) }
}
