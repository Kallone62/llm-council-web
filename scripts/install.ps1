# LLM Council Web: one-command, per-user Windows desktop installer.
# Usage (after a signed/public GitHub Release is published):
# irm https://raw.githubusercontent.com/Kallone62/llm-council-web/v1.1.0-beta.5/scripts/install.ps1 | iex
$ErrorActionPreference = 'Stop'
$version = 'v1.1.0-beta.5'
$asset = 'LLMCouncilWeb-Windows-x64.zip'
$releaseBase = "https://github.com/Kallone62/llm-council-web/releases/download/$version"
$target = Join-Path $env:LOCALAPPDATA 'Programs\LLMCouncilWeb'
$temp = Join-Path $env:TEMP ('LLMCouncilWeb-install-' + [guid]::NewGuid().ToString('N'))
$backup = $null
$proc = $null
$payloadMoved = $false

try {
    if (-not $env:LOCALAPPDATA) {
        throw 'This installer requires a normal Windows user profile.'
    }
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    $zip = Join-Path $temp $asset
    $hashFile = "$zip.sha256"

    Write-Host "Downloading LLM Council Web $version from GitHub Release..." -ForegroundColor Cyan
    Invoke-WebRequest -UseBasicParsing -Uri "$releaseBase/$asset" -OutFile $zip
    Invoke-WebRequest -UseBasicParsing -Uri "$releaseBase/$asset.sha256" -OutFile $hashFile

    $expected = [regex]::Match(
        [IO.File]::ReadAllText($hashFile), '^[a-fA-F0-9]{64}'
    ).Value
    if (-not $expected) {
        throw 'Release package SHA256 file is invalid.'
    }
    $actual = (Get-FileHash -Path $zip -Algorithm SHA256).Hash
    if ($actual -ne $expected) {
        throw 'Downloaded package failed SHA256 verification. Installation stopped.'
    }

    $extracted = Join-Path $temp 'extracted'
    Expand-Archive -Path $zip -DestinationPath $extracted -Force
    $payload = Join-Path $extracted 'LLMCouncilWeb'
    if (-not (Test-Path (Join-Path $payload 'LLMCouncilWeb.exe'))) {
        throw 'Release ZIP does not contain LLMCouncilWeb.exe.'
    }

    # Do not accept health responses from a previous running installation.
    if (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Close the running LLM Council instance (or other service on port 8001) before installing.'
    }

    if (Test-Path $target) {
        # Preserve secrets, settings and history. The new EXE validates the
        # staged YAML and migrates stale Free presets; the old install is backed up.
        foreach ($file in @('.env', 'llm_council.yaml')) {
            $oldFile = Join-Path $target $file
            if (Test-Path $oldFile) {
                Copy-Item -Path $oldFile -Destination (Join-Path $payload $file) -Force
            }
        }
        $oldData = Join-Path $target 'data'
        if (Test-Path $oldData) {
            Copy-Item -Path $oldData -Destination (Join-Path $payload 'data') -Recurse -Force
        }
    }

    $parent = Split-Path $target -Parent
    New-Item -ItemType Directory -Force -Path $parent | Out-Null

    if (Test-Path $target) {
        # Preserve a full previous-install backup until the new one has launched.
        $backup = $target + '.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
        Move-Item -Path $target -Destination $backup
    }

    try {
        Move-Item -Path $payload -Destination $target
        $payloadMoved = $true
        $exe = Join-Path $target 'LLMCouncilWeb.exe'

        $oldValidation = $env:LLM_COUNCIL_VALIDATE_INSTALL
        try {
            $env:LLM_COUNCIL_VALIDATE_INSTALL = '1'
            $proc = Start-Process -FilePath $exe -WorkingDirectory $target -PassThru
        }
        finally {
            $env:LLM_COUNCIL_VALIDATE_INSTALL = $oldValidation
        }
        $ready = $false
        $lastError = 'Application did not become ready'
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            $proc.Refresh()
            if ($proc.HasExited) { throw "Application exited during startup ($($proc.ExitCode))" }
            try {
                $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/' -TimeoutSec 2
                $settings = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/settings' -TimeoutSec 2
                $ui = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8001/app/' -TimeoutSec 2
                if ($health.service -ne 'LLM Council API' -or "v$($health.version)" -ne $version) {
                    throw 'Unexpected application or version on port 8001'
                }
                if ($settings.config_path -ne (Join-Path $target 'llm_council.yaml')) {
                    throw 'Application loaded an unexpected configuration path'
                }
                if ($settings.configuration_valid -ne $true) { throw 'Effective council configuration is invalid' }
                if ($ui.StatusCode -ne 200) { throw 'React UI is not ready' }
                $ready = $true
                break
            }
            catch { $lastError = $_.Exception.Message }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) { throw "Application readiness check failed: $lastError" }
        $desktop = [Environment]::GetFolderPath('Desktop')
        if ($desktop) {
            $shortcutPath = Join-Path $desktop 'LLM Council Web.lnk'
            $shell = New-Object -ComObject WScript.Shell
            $shortcut = $shell.CreateShortcut($shortcutPath)
            $shortcut.TargetPath = $exe
            $shortcut.WorkingDirectory = $target
            $shortcut.IconLocation = $exe
            $shortcut.Save()
        }
        Write-Host "Installed to $target" -ForegroundColor Green
        if ($backup) {
            Write-Host "Previous installation backed up at $backup"
        }
    }
    catch {
        if ($proc) {
            # PyInstaller onedir uses a single application process.
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            $null = $proc.WaitForExit(5000)
        }
        if ($payloadMoved -and (Test-Path $target)) {
            Remove-Item -Path $target -Recurse -Force
        }
        if ($backup -and (Test-Path $backup)) {
            Move-Item -Path $backup -Destination $target
        }
        throw
    }
}
finally {
    if ($temp -and (Test-Path $temp)) {
        Remove-Item -Path $temp -Recurse -Force
    }
}
