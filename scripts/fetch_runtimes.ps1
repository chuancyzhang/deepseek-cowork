param(
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {
}

$NODE_VERSION = "v24.14.1"
$NODE_ARCHIVE = "node-v24.14.1-win-x64.zip"
$NODE_URL = "https://nodejs.org/dist/$NODE_VERSION/$NODE_ARCHIVE"
$NODE_SHA256 = "6E50CE5498C0CEBC20FD39AB3FF5DF836ED2F8A31AA093CECAD8497CFF126D70"

$GIT_VERSION = "2.53.0.2"
$GIT_TAG = "v2.53.0.windows.2"
$GIT_ARCHIVE = "PortableGit-2.53.0.2-64-bit.7z.exe"
$GIT_URL = "https://github.com/git-for-windows/git/releases/download/$GIT_TAG/$GIT_ARCHIVE"
$GIT_SHA256 = "5F4F76C7D5036EA3B29FBADEDCC510733B3A0EE8DA57A36796E2E57A466BE964"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$downloadDir = Join-Path $repoRoot ".runtime_downloads"
$nodeTargetDir = Join-Path $repoRoot "node_env"
$gitTargetDir = Join-Path $repoRoot "git_bash_env"

$nodeArchivePath = Join-Path $downloadDir $NODE_ARCHIVE
$gitArchivePath = Join-Path $downloadDir $GIT_ARCHIVE
$nodeExtractDir = Join-Path $downloadDir "node_extract"

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Assert-Hash([string]$Path, [string]$ExpectedSha256) {
    $actual = Get-FileSha256 -Path $Path
    if ($actual -ne $ExpectedSha256.ToUpperInvariant()) {
        throw "SHA256 mismatch for '$Path'. expected=$ExpectedSha256 actual=$actual"
    }
}

function Download-Artifact([string]$Url, [string]$Path, [string]$Sha256, [switch]$ForceDownload) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    if ((Test-Path $Path) -and -not $ForceDownload) {
        try {
            Assert-Hash -Path $Path -ExpectedSha256 $Sha256
            Write-Host "[OK] Reuse cached artifact: $Path"
            return
        } catch {
            Write-Host "[WARN] Cached artifact hash mismatch, re-downloading: $Path"
            Remove-Item -Force $Path
        }
    }
    Write-Host "[DL] $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Path
    Assert-Hash -Path $Path -ExpectedSha256 $Sha256
    Write-Host "[OK] Download + SHA256 verified: $Path"
}

New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null

Download-Artifact -Url $NODE_URL -Path $nodeArchivePath -Sha256 $NODE_SHA256 -ForceDownload:$Force
Download-Artifact -Url $GIT_URL -Path $gitArchivePath -Sha256 $GIT_SHA256 -ForceDownload:$Force

Write-Host "[STEP] Extract Node.js runtime..."
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $nodeExtractDir
New-Item -ItemType Directory -Force -Path $nodeExtractDir | Out-Null
Expand-Archive -Path $nodeArchivePath -DestinationPath $nodeExtractDir -Force

$nodeExtractedRoot = Join-Path $nodeExtractDir ("node-" + $NODE_VERSION.TrimStart("v") + "-win-x64")
if (-not (Test-Path $nodeExtractedRoot)) {
    $fallback = Get-ChildItem -Path $nodeExtractDir -Directory | Select-Object -First 1
    if (-not $fallback) {
        throw "Unable to locate extracted Node.js folder in $nodeExtractDir"
    }
    $nodeExtractedRoot = $fallback.FullName
}

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $nodeTargetDir
Move-Item -Path $nodeExtractedRoot -Destination $nodeTargetDir

Write-Host "[STEP] Extract Git Bash runtime..."
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $gitTargetDir
New-Item -ItemType Directory -Force -Path $gitTargetDir | Out-Null
& $gitArchivePath "-o$gitTargetDir" "-y" | Out-Null

$nodeExe = Join-Path $nodeTargetDir "node.exe"
$bashExe = Join-Path $gitTargetDir "bin\bash.exe"

if (-not (Test-Path $nodeExe)) {
    throw "Missing node executable: $nodeExe"
}
if (-not (Test-Path $bashExe)) {
    throw "Missing bash executable: $bashExe"
}

function Try-Run([scriptblock]$Block, [string]$Fallback) {
    try {
        $value = & $Block
        if ($null -eq $value) {
            return $Fallback
        }
        if ($value -is [System.Array]) {
            if ($value.Count -eq 0) { return $Fallback }
            return ($value[0].ToString()).Trim()
        }
        return ($value.ToString()).Trim()
    } catch {
        return $Fallback
    }
}

$nodeVersionOut = Try-Run -Block { & $nodeExe --version } -Fallback "unavailable"
$npmVersionOut = Try-Run -Block { & $nodeExe (Join-Path $nodeTargetDir "node_modules\npm\bin\npm-cli.js") --version } -Fallback "unavailable"
$bashVersionOut = Try-Run -Block { & $bashExe --version | Select-Object -First 1 } -Fallback "unavailable in current shell"

Write-Host ""
Write-Host "Runtime fetch completed."
Write-Host "  node: $nodeExe ($nodeVersionOut)"
Write-Host "  npm : $npmVersionOut"
Write-Host "  bash: $bashExe ($bashVersionOut)"
Write-Host ""
Write-Host "Next: run 'pyinstaller deepseek-cowork.spec'"
