# WIN-01: run the actual installer against the candidate ZIP on a disposable Windows runner.
param([Parameter(Mandatory = $true)][string]$PackageZip)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$installer = Join-Path $root 'scripts/install.ps1'
$zip = (Resolve-Path -LiteralPath $PackageZip).Path
$temp = Join-Path ([IO.Path]::GetTempPath()) ('council-win01-' + [guid]::NewGuid().ToString('N'))
$originalLocalAppData = $env:LOCALAPPDATA
$desktop = [Environment]::GetFolderPath('Desktop')
if (-not $desktop -or -not (Test-Path -LiteralPath $desktop)) { throw 'Disposable runner needs a real Desktop directory' }
$shortcutPath = Join-Path $desktop 'LLM Council Web.lnk'
if (Test-Path -LiteralPath $shortcutPath) { throw 'WIN-01 requires an unused desktop shortcut path' }
if (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue) { throw 'WIN-01 requires unused port 8001' }
if ($env:OPENROUTER_API_KEY -or $env:LLM_COUNCIL_CONFIG -or $env:LLM_COUNCIL_UI_DATA_DIR) {
    throw 'WIN-01 requires an unconfigured test environment'
}
$env:LLM_COUNCIL_NO_BROWSER = '1'
$env:LLM_COUNCIL_NO_TRAY = '1'
$env:LOCALAPPDATA = Join-Path $temp 'localappdata'
$target = Join-Path $env:LOCALAPPDATA 'Programs\LLMCouncilWeb'
$exe = Join-Path $target 'LLMCouncilWeb.exe'
$sourceHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
$original = [IO.File]::ReadAllText($installer)
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Clear-TestInstall {
    Get-Process -Name LLMCouncilWeb -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $exe } | ForEach-Object {
            Stop-Process -Id $_.Id -Force
            $_.WaitForExit()
        }
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    if (Test-Path -LiteralPath $shortcutPath) { Remove-Item -LiteralPath $shortcutPath -Force }
}

function Assert-NoInstall {
    if ((Test-Path -LiteralPath $target) -or (Test-Path -LiteralPath $shortcutPath)) {
        throw 'Rejected clean package left installation or shortcut behind'
    }
    if (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Rejected clean package left a listening process'
    }
}

function Assert-Rejected($InstallerPath, $Candidate, $ExpectedError) {
    $failure = $null
    try { & $InstallerPath -PackageZip $Candidate } catch { $failure = $_.Exception.Message }
    if (-not $failure) { throw "False-positive installation: $ExpectedError" }
    if ($failure -ne $ExpectedError) { throw "Wrong rejection: $failure; expected: $ExpectedError" }
    Assert-NoInstall
    Write-Host "WIN-01 rejected: $ExpectedError"
}

function Write-Hash($Candidate) {
    $hash = (Get-FileHash -LiteralPath $Candidate -Algorithm SHA256).Hash
    Set-Content -LiteralPath "$Candidate.sha256" -Value "$hash  candidate.zip" -Encoding ascii
}

function New-BadPackage($Name, $EntryName, $Replacement) {
    $candidate = Join-Path $temp "$Name.zip"
    Copy-Item -LiteralPath $zip -Destination $candidate
    $archive = [IO.Compression.ZipFile]::Open($candidate, [IO.Compression.ZipArchiveMode]::Update)
    try {
        $entry = $archive.GetEntry($EntryName)
        if ($entry) { $entry.Delete() }
        $entry = $archive.CreateEntry($EntryName)
        $writer = [IO.StreamWriter]::new($entry.Open(), [Text.UTF8Encoding]::new($false))
        try { $writer.Write($Replacement) } finally { $writer.Dispose() }
    } finally { $archive.Dispose() }
    Write-Hash $candidate
    return $candidate
}

function Assert-Installed {
    $required = @(
        'LLMCouncilWeb.exe', 'llm_council.yaml', '.env.example', 'README.md', 'NOTICE.md',
        '_internal/frontend/dist/index.html', '_internal/frontend/dist/llm-council.svg',
        '_internal/brand/icon-64.png'
    )
    foreach ($name in $required) {
        $actual = Join-Path $target $name
        $expected = Join-Path $temp "extracted/LLMCouncilWeb/$name"
        if (-not (Test-Path -LiteralPath $actual -PathType Leaf)) { throw "Missing installed file: $name" }
        if ((Get-FileHash -LiteralPath $actual).Hash -ne (Get-FileHash -LiteralPath $expected).Hash) {
            throw "Installed file differs from ZIP: $name"
        }
    }
    if (Test-Path -LiteralPath (Join-Path $target '.env')) { throw 'Clean install contains a .env secret file' }
    $listener = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 8001 -State Listen)
    if ($listener.Count -ne 1) { throw 'Expected exactly one installed listener' }
    $process = Get-Process -Id $listener[0].OwningProcess
    if ($process.Path -ne $exe) { throw 'Listener is not the installed EXE' }
    $ui = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8001/app/' -TimeoutSec 5
    if ($ui.StatusCode -ne 200 -or $ui.Content -notmatch '<div id="root"') { throw 'Installed React UI unavailable' }
    $asset = [regex]::Match($ui.Content, 'src="(/app/assets/[^"]+\.js)"').Groups[1].Value
    if (-not $asset) { throw 'No compiled JS asset referenced by installed UI' }
    $js = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8001$asset" -TimeoutSec 5
    if ($js.StatusCode -ne 200 -or $js.Content.Length -lt 100) { throw 'Installed JS asset unavailable' }
    $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8001/api/settings' -TimeoutSec 5
    $settings = $response.Content | ConvertFrom-Json
    if ($response.StatusCode -ne 200 -or $settings.profile -cne 'free' -or $settings.use_profile_models -ne $true) {
        throw 'Clean install did not start with effective Free preset'
    }
    $models = @('google/gemma-4-31b-it:free', 'qwen/qwen3.8-27b:free', 'nvidia/nemotron-3-super-120b-a12b:free')
    if (($settings.models -join '|') -cne ($models -join '|') -or $settings.chairman -cne $models[0] -or $settings.resolved_chairman -cne $models[0]) {
        throw 'Installed Free model/Chairman differs from acceptance baseline'
    }
    if ($settings.api_key_configured -isnot [bool] -or $settings.api_key_configured) { throw 'Clean install has configured key' }
    if ($settings.style_normalization -ne 'off' -or $settings.rubric_enabled -ne $false -or $settings.safety_enabled -ne $false -or $settings.bias_audit_enabled -ne $false) {
        throw 'Installed Free feature defaults differ'
    }
    if ($settings.config_path -ne (Join-Path $target 'llm_council.yaml')) { throw 'Installed EXE uses wrong configuration path' }
    $history = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8001/api/conversations' -TimeoutSec 5
    if ($history.StatusCode -ne 200 -or $history.Content.Trim() -ne '[]') { throw 'Clean install has pre-existing conversation history' }
    if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) { throw 'Installer did not create desktop shortcut' }
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
    if ($shortcut.TargetPath -ne $exe -or $shortcut.WorkingDirectory -ne $target) { throw 'Shortcut points outside installed application' }
    Write-Host "WIN-01 installed listener PID $($process.Id); ZIP files, React JS, Free, no key, empty history and shortcut PASS"
}

try {
    New-Item -ItemType Directory -Path $env:LOCALAPPDATA -Force | Out-Null
    $hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
    $declared = [regex]::Match([IO.File]::ReadAllText("$zip.sha256"), '^[a-fA-F0-9]{64}').Value
    if ($hash -ne $declared) { throw 'CI candidate SHA256 mismatch' }
    Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $temp 'extracted')
    Write-Host "WIN-01 candidate SHA256: $hash"

    # Hash rejection before any files are installed.
    $badHash = Join-Path $temp 'wrong-hash.zip'
    Copy-Item -LiteralPath $zip -Destination $badHash
    Set-Content -LiteralPath "$badHash.sha256" -Value ('0' * 64) -Encoding ascii
    Assert-Rejected $installer $badHash 'Downloaded package failed SHA256 verification. Installation stopped.'

    # Same genuine executable; valid ZIP and recomputed SHA, but unsafe defaults.
    $keyZip = New-BadPackage 'configured-key' 'LLMCouncilWeb/.env' "OPENROUTER_API_KEY=win01-synthetic-never-used"
    $yaml = [IO.File]::ReadAllText((Join-Path $temp 'extracted/LLMCouncilWeb/llm_council.yaml'))
    if (-not $yaml.Contains('google/gemma-4-31b-it:free')) { throw 'Free profile fixture anchor missing' }
    $paidZip = New-BadPackage 'wrong-profile' 'LLMCouncilWeb/llm_council.yaml' ($yaml.Replace('google/gemma-4-31b-it:free', 'google/gemini-2.5-flash'))
    $cases = @(
        @{ Zip = $paidZip; Error = 'New-install preset must be Free' },
        @{ Zip = $keyZip; Error = 'New installation must explicitly report no configured API key' }
    )
    foreach ($case in $cases) {
        Assert-Rejected $installer $case.Zip $case.Error
        # A temporary production mutation removes exactly the rejection guard.
        # The very same negative test must detect a successful unsafe installation.
        $guard = "throw '$($case.Error)'"
        if (-not $original.Contains($guard)) { throw "Mutation anchor missing: $guard" }
        $mutant = Join-Path $temp 'mutant-installer.ps1'
        [IO.File]::WriteAllText($mutant, $original.Replace($guard, 'return'))
        $failure = $null
        try { Assert-Rejected $mutant $case.Zip $case.Error } catch { $failure = $_.Exception.Message }
        finally { Clear-TestInstall }
        if ($failure -ne "False-positive installation: $($case.Error)") { throw "Mutation not detected at exact oracle: $failure" }
        Write-Host "WIN-01 DETECTED: $failure"
    }
    if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne $sourceHash) { throw 'Production installer changed during mutations' }
    Assert-NoInstall
    & $installer -PackageZip $zip
    Assert-Installed
    Write-Host 'WIN-01 genuine clean install after negative controls PASS'
} finally {
    Clear-TestInstall
    $env:LOCALAPPDATA = $originalLocalAppData
    Remove-Item -LiteralPath $temp -Recurse -Force
}
