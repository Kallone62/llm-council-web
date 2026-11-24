# Verify the existing regression detects the original false-positive behavior.
# Mutations are confined to temporary copies, never to the actual production file.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("council-smoke-" + [guid]::NewGuid())
$helper = Join-Path $temp 'scripts/windows/assert-smoke-settings.ps1'
$test = Join-Path $temp 'tests/windows/test-smoke-settings.ps1'
$original = Get-Content (Join-Path $root 'scripts/windows/assert-smoke-settings.ps1') -Raw
$mutations = @(
    @{ Guard = "throw 'New-install preset must be Free'"; Expected = 'False-positive readiness: paid profile with HTTP 200' }
    @{ Guard = "throw 'Bundled beta must explicitly report no configured API key'"; Expected = 'False-positive readiness: configured API key with HTTP 200' }
)
try {
    New-Item -ItemType Directory -Path (Split-Path $helper), (Split-Path $test) -Force | Out-Null
    Copy-Item (Join-Path $root 'tests/windows/test-smoke-settings.ps1') $test
    Set-Content -LiteralPath $helper -Value $original
    & $test
    foreach ($mutation in $mutations) {
        if (-not $original.Contains($mutation.Guard)) { throw "Mutation target missing: $($mutation.Guard)" }
        Set-Content -LiteralPath $helper -Value ($original.Replace($mutation.Guard, 'return $true'))
        $failure = $null
        try { & $test } catch { $failure = $_.Exception.Message }
        if ($failure -ne $mutation.Expected) {
            throw "Mutation did not fail at the expected assertion: $($mutation.Expected); observed: $failure"
        }
        Write-Host "DETECTED: $failure"
    }
    Set-Content -LiteralPath $helper -Value $original
    & $test
} finally {
    Remove-Item -LiteralPath $temp -Recurse -Force
}
