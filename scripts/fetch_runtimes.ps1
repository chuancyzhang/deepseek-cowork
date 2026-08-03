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

$GIT_VERSION = "2.53.0.2"
$GIT_TAG = "v2.53.0.windows.2"
$GIT_ARCHIVE = "PortableGit-2.53.0.2-64-bit.7z.exe"
$GIT_URL = "https://github.com/git-for-windows/git/releases/download/$GIT_TAG/$GIT_ARCHIVE"
$GIT_SHA256 = "5F4F76C7D5036EA3B29FBADEDCC510733B3A0EE8DA57A36796E2E57A466BE964"

$BROWSER_SKILL_CLI_VERSION = "0.1.8"
$BROWSER_SKILL_CLI_ARCHIVE = "bsk-v0.1.8-x86_64-pc-windows-msvc.zip"
$BROWSER_SKILL_CLI_URL = "https://github.com/Tencent/BrowserSkill/releases/download/cli-v0.1.8/$BROWSER_SKILL_CLI_ARCHIVE"
$BROWSER_SKILL_CLI_SHA256 = "A5FEF16F7247F5BA6AE2ED032DF8C3704F124291884FEA40C19E6492AD442E13"
$BROWSER_SKILL_EXTENSION_VERSION = "0.1.4"
$BROWSER_SKILL_EXTENSION_ARCHIVE = "browser-skill-extension-v0.1.4-chrome.zip"
$BROWSER_SKILL_EXTENSION_URL = "https://github.com/Tencent/BrowserSkill/releases/download/ext-v0.1.4/$BROWSER_SKILL_EXTENSION_ARCHIVE"
$BROWSER_SKILL_EXTENSION_SHA256 = "0C7A0B371CC15AC42AF155A55ED0C1BDAF257916F1ACC71C0C2BC56AAE366C3E"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$downloadDir = Join-Path $repoRoot ".runtime_downloads"
$gitTargetDir = Join-Path $repoRoot "git_bash_env"
$browserSkillArtifactsDir = Join-Path $repoRoot "resources\browser_skill\artifacts"

$gitArchivePath = Join-Path $downloadDir $GIT_ARCHIVE
$browserSkillCliArchivePath = Join-Path $browserSkillArtifactsDir $BROWSER_SKILL_CLI_ARCHIVE
$browserSkillExtensionArchivePath = Join-Path $browserSkillArtifactsDir $BROWSER_SKILL_EXTENSION_ARCHIVE

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

Download-Artifact -Url $GIT_URL -Path $gitArchivePath -Sha256 $GIT_SHA256 -ForceDownload:$Force
Download-Artifact -Url $BROWSER_SKILL_CLI_URL -Path $browserSkillCliArchivePath -Sha256 $BROWSER_SKILL_CLI_SHA256 -ForceDownload:$Force
Download-Artifact -Url $BROWSER_SKILL_EXTENSION_URL -Path $browserSkillExtensionArchivePath -Sha256 $BROWSER_SKILL_EXTENSION_SHA256 -ForceDownload:$Force

Write-Host "[STEP] Extract Git Bash runtime..."
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $gitTargetDir
New-Item -ItemType Directory -Force -Path $gitTargetDir | Out-Null
& $gitArchivePath "-o$gitTargetDir" "-y" | Out-Null

$bashExe = Join-Path $gitTargetDir "bin\bash.exe"

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

$bashVersionOut = Try-Run -Block { & $bashExe --version | Select-Object -First 1 } -Fallback "unavailable in current shell"

Write-Host ""
Write-Host "Runtime fetch completed."
Write-Host "  bash: $bashExe ($bashVersionOut)"
Write-Host "  BrowserSkill CLI: $browserSkillCliArchivePath (v$BROWSER_SKILL_CLI_VERSION)"
Write-Host "  BrowserSkill extension: $browserSkillExtensionArchivePath (v$BROWSER_SKILL_EXTENSION_VERSION)"
Write-Host ""
Write-Host "Next: run 'pyinstaller deepseek-cowork.spec'"
