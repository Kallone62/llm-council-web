# Exercise the same PowerShell assertion used by the bundled EXE smoke step.
$ErrorActionPreference = 'Stop'
$assertSettings = Join-Path $PSScriptRoot '../../scripts/windows/assert-smoke-settings.ps1'

$cases = @(
    @{ Name = 'valid Free settings'; Status = 200; Body = '{"profile":"free","api_key_configured":false}'; Pass = $true }
    @{ Name = 'paid profile with HTTP 200'; Status = 200; Body = '{"profile":"high","api_key_configured":false}'; Pass = $false }
    @{ Name = 'configured API key with HTTP 200'; Status = 200; Body = '{"profile":"free","api_key_configured":true}'; Pass = $false }
    @{ Name = 'missing key status'; Status = 200; Body = '{"profile":"free"}'; Pass = $false }
    @{ Name = 'missing profile'; Status = 200; Body = '{"api_key_configured":false}'; Pass = $false }
    @{ Name = 'non-boolean key status'; Status = 200; Body = '{"profile":"free","api_key_configured":"false"}'; Pass = $false }
    @{ Name = 'malformed JSON'; Status = 200; Body = '{invalid'; Pass = $false }
    @{ Name = 'non-200 status'; Status = 503; Body = '{"profile":"free","api_key_configured":false}'; Pass = $false }
)

foreach ($case in $cases) {
    $apiReady = $false
    $rejected = $false
    try {
        # Match the assignment and catch boundary in the smoke workflow.
        $apiReady = & $assertSettings -Response ([pscustomobject]@{
            StatusCode = $case.Status
            Content = $case.Body
        })
    } catch {
        $rejected = $true
    }
    if ($case.Pass) {
        if ($rejected -or $apiReady -ne $true) {
            throw "Expected readiness: $($case.Name)"
        }
    } elseif (-not $rejected -or $apiReady) {
        throw "False-positive readiness: $($case.Name)"
    }
    Write-Host "PASS: $($case.Name)"
}
