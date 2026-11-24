# Return true only after every Settings API readiness check has passed.
param(
    [Parameter(Mandatory = $true)]
    $Response
)

if ($Response.StatusCode -ne 200) {
    throw "Settings API must return HTTP 200, got $($Response.StatusCode)"
}
$effective = $Response.Content | ConvertFrom-Json -ErrorAction Stop
if ($effective.profile -cne 'free') {
    throw 'New-install preset must be Free'
}
if ($effective.api_key_configured -isnot [bool] -or $effective.api_key_configured) {
    throw 'Bundled beta must explicitly report no configured API key'
}
return $true
