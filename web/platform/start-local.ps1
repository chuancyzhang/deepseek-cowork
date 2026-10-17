$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path -LiteralPath 'images.tar') {
    docker load --input images.tar
    if ($LASTEXITCODE -ne 0) { throw 'Docker image import failed.' }
}
if (-not (Test-Path -LiteralPath '.env.local')) {
    docker volume inspect cowork-enterprise-local_database *> $null
    if ($LASTEXITCODE -eq 0) {
        throw 'Existing database found. Restore its original .env.local before starting; do not generate new credentials for existing data.'
    }
    $localRandom = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $localBytes = New-Object byte[] 32
    $localRandom.GetBytes($localBytes)
    $localRandom.Dispose()
    $localPassword = [BitConverter]::ToString($localBytes).Replace('-', '').ToLowerInvariant()
    $localKey = docker run --rm cowork-enterprise:local python -m server generate-key
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
docker compose --env-file .env.local -f compose.local.yaml up -d --no-build --wait
if ($LASTEXITCODE -ne 0) { throw 'Platform startup failed; inspect docker compose logs.' }
Write-Host 'Cowork enterprise platform: http://localhost:8788/app'
