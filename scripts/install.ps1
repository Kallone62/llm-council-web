# LLM Council Web: one-command, per-user Windows desktop installer.
# Usage (after a signed/public GitHub Release is published):
# irm https://raw.githubusercontent.com/Kallone62/llm-council-web/v1.1.0-beta.5/scripts/install.ps1 | iex
param([string]$PackageZip)

$ErrorActionPreference = 'Stop'
$version = 'v1.1.0-beta.5'
$asset = 'LLMCouncilWeb-Windows-x64.zip'
$releaseBase = "https://github.com/Kallone62/llm-council-web/releases/download/$version"
if (-not $env:LOCALAPPDATA) {
    throw 'This installer requires a normal Windows user profile.'
}
$target = Join-Path $env:LOCALAPPDATA 'Programs\LLMCouncilWeb'
$temp = Join-Path $env:TEMP ('LLMCouncilWeb-install-' + [guid]::NewGuid().ToString('N'))
$backup = $null
$freshInstall = -not (Test-Path -LiteralPath $target)
$installed = $false
$proc = $null

function Assert-CleanInstallReady($Process) {
    $deadline = [Diagnostics.Stopwatch]::StartNew()
    while ($deadline.Elapsed.TotalSeconds -lt 60) {
        $Process.Refresh()
        if ($Process.HasExited) { throw 'Installed EXE exited before readiness.' }
        $listener = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
        if ($listener -and @($listener | Where-Object { $_.OwningProcess -eq $Process.Id }).Count -eq 1) {
            try {
                $ui = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8001/app/' -TimeoutSec 3
                $api = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8001/api/settings' -TimeoutSec 3
            } catch {
                Start-Sleep -Milliseconds 500
                continue
            }
            if ($ui.StatusCode -ne 200 -or $ui.Content -notmatch '<div id="root"') {
                throw 'Installed EXE did not serve the React UI.'
            }
            if ($api.StatusCode -ne 200) { throw 'Installed Settings API is not ready.' }
            $settings = $api.Content | ConvertFrom-Json -ErrorAction Stop
            if ($settings.profile -cne 'free' -or $settings.use_profile_models -isnot [bool] -or -not $settings.use_profile_models) {
                throw 'New-install preset must be Free'
            }
            if ($settings.api_key_configured -isnot [bool] -or $settings.api_key_configured) {
                throw 'New installation must explicitly report no configured API key'
            }
            $Process.Refresh()
            if ($Process.HasExited) { throw 'Installed EXE exited during readiness.' }
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw 'Installed EXE did not become ready on its own loopback listener.'
}

try {
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    $zip = Join-Path $temp $asset
    $hashFile = "$zip.sha256"

    if ($PackageZip) {
        # Test an unpublished release candidate using the same installer and hash checks.
        $source = (Resolve-Path -LiteralPath $PackageZip).Path
        Copy-Item -LiteralPath $source -Destination $zip
        Copy-Item -LiteralPath "$source.sha256" -Destination $hashFile
    } else {
        Write-Host "Downloading LLM Council Web $version from GitHub Release..." -ForegroundColor Cyan
        Invoke-WebRequest -UseBasicParsing -Uri "$releaseBase/$asset" -OutFile $zip
        Invoke-WebRequest -UseBasicParsing -Uri "$releaseBase/$asset.sha256" -OutFile $hashFile
    }

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

    if (Test-Path $target) {
        # Keep user secrets, custom council profile and complete message history.
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
        $installed = $true
        $exe = Join-Path $target 'LLMCouncilWeb.exe'
        if ($freshInstall) {
            $proc = Start-Process -FilePath $exe -WorkingDirectory $target -PassThru
            Assert-CleanInstallReady $proc
        }
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
        if (-not $freshInstall) {
            Start-Process -FilePath $exe -WorkingDirectory $target
        }
        Write-Host "Installed to $target" -ForegroundColor Green
        if ($backup) {
            Write-Host "Previous installation backed up at $backup"
        }
    }
    catch {
        if ($freshInstall -and $proc) {
            $proc.Refresh()
            if (-not $proc.HasExited) {
                Stop-Process -Id $proc.Id -Force
                $proc.WaitForExit()
            }
        }
        if ($freshInstall -and $installed -and (Test-Path -LiteralPath $target)) {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
        if ($backup -and (Test-Path $backup)) {
            if (Test-Path $target) {
                Remove-Item -Path $target -Recurse -Force
            }
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
