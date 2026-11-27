param([switch]$VerifyOnly)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
docker info --format '{{.OSType}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop with Linux containers first.' }
if (Test-Path -LiteralPath 'images.tar') {
    if (-not (Test-Path -LiteralPath 'deployment.json')) {
        throw 'Legacy offline package: rebuild it with scripts/package_enterprise.py.'
    }
    $deployment = Get-Content -LiteralPath 'deployment.json' -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($deployment.schema_version -ne 1 -or $deployment.target -ne 'linux-amd64' -or $deployment.database_schema -ne 6) {
        throw 'Unsupported deployment manifest.'
    }
    foreach ($required in @('images.tar', 'compose.local.yaml', 'start-local.ps1', 'server/releases.py', 'server/database.py')) {
        if (-not $deployment.files.PSObject.Properties[$required]) { throw "Missing deployment checksum: $required" }
    }
    $packageRoot = [IO.Path]::GetFullPath($PSScriptRoot) + [IO.Path]::DirectorySeparatorChar
    foreach ($file in $deployment.files.PSObject.Properties) {
        $fullPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $file.Name))
        if (-not $fullPath.StartsWith($packageRoot, [StringComparison]::OrdinalIgnoreCase) -or
            $file.Value -notmatch '^[a-f0-9]{64}$' -or -not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
            throw "Invalid deployment file: $($file.Name)"
        }
        $algorithm = [Security.Cryptography.SHA256]::Create()
        $stream = [IO.File]::OpenRead($fullPath)
        try { $actualHash = [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
        finally { $stream.Dispose(); $algorithm.Dispose() }
        if ($actualHash -ne $file.Value) {
            throw "Deployment checksum mismatch: $($file.Name). Restore the complete package before starting."
        }
    }
    docker load --input images.tar
    if ($LASTEXITCODE -ne 0) { throw 'Docker image import failed.' }
    foreach ($image in @(@($deployment.platform_image, $deployment.platform_image_id), @($deployment.database_image, $deployment.database_image_id))) {
        $identity = docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' $image[0]
        if ($LASTEXITCODE -ne 0 -or $identity -ne "$($image[1]) linux/amd64") { throw "Imported image identity mismatch: $($image[0])" }
    }
    $env:COWORK_PLATFORM_IMAGE = $deployment.platform_image
    $env:COWORK_DATABASE_IMAGE = $deployment.database_image
    Write-Host "Enterprise deployment $($deployment.version), source $($deployment.source_sha256)"
}
$platformImage = if ($env:COWORK_PLATFORM_IMAGE) { $env:COWORK_PLATFORM_IMAGE } else { 'cowork-enterprise:local' }
docker run --rm --network none $platformImage python -c "from server.database import SCHEMA_VERSION; from server.releases import DESKTOP_REQUIRED_FILES; assert SCHEMA_VERSION == 5 and 'desktop-release.json' in DESKTOP_REQUIRED_FILES"
if ($LASTEXITCODE -ne 0) { throw 'Build the current platform image first; this image does not support the current enterprise distribution.' }
if ($VerifyOnly) { Write-Host 'Deployment files and images verified; no services or business data changed.'; return }
if (-not (Test-Path -LiteralPath '.env.local')) {
    $volumes = docker volume ls --format '{{.Name}}'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect existing data volumes.' }
    if ('cowork-enterprise-local_database' -in $volumes -or 'cowork-enterprise-local_releases' -in $volumes) {
        throw 'Existing database found. Restore its original .env.local before starting; do not generate new credentials for existing data.'
    }
    $localRandom = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $localBytes = New-Object byte[] 32
    $localRandom.GetBytes($localBytes)
    $localRandom.Dispose()
    $localPassword = [BitConverter]::ToString($localBytes).Replace('-', '').ToLowerInvariant()
    $localKey = docker run --rm --network none $platformImage python -m server generate-key
    if ($LASTEXITCODE -ne 0) { throw 'Encryption key generation failed.' }
    $localConfig = @(
        "POSTGRES_PASSWORD=$localPassword"
        "COWORK_DATABASE_URL=postgresql+psycopg://cowork:${localPassword}@database:5432/cowork"
        'COWORK_PLATFORM_ID=cowork-local'
        'COWORK_PLATFORM_NAME=Cowork Local'
        "COWORK_ENCRYPTION_KEY=$($localKey.Trim())"
        'COWORK_SESSION_SECONDS=28800'
    ) -join "`n"
    [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot '.env.local'), $localConfig + "`n", (New-Object System.Text.UTF8Encoding $false))
}
docker compose --env-file .env.local -f compose.local.yaml up -d --no-build --pull never --wait
if ($LASTEXITCODE -ne 0) { throw 'Platform startup failed; inspect docker compose logs.' }
Write-Host 'Cowork enterprise platform: http://localhost:8788/app'
