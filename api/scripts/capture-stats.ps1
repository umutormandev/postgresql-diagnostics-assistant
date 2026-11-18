param(
    [Parameter(Mandatory = $true)][string]$Python,
    [int]$Count = 16,
    [int]$IntervalSeconds = 60
)

if (-not $env:DIAGNOSTICS_SAMPLER_DATABASE_URL) {
    throw 'Set DIAGNOSTICS_SAMPLER_DATABASE_URL for this database.'
}
if ($Count -lt 1 -or $IntervalSeconds -lt 1) {
    throw 'Count and IntervalSeconds must be positive.'
}

$PythonPath = (Resolve-Path -LiteralPath $Python).Path
Push-Location (Join-Path $PSScriptRoot '..')
try {
    for ($index = 1; $index -le $Count; $index++) {
        & $PythonPath -m app.capture_stats
        if ($LASTEXITCODE -ne 0) { throw "Capture $index failed." }
        if ($index -lt $Count) { Start-Sleep -Seconds $IntervalSeconds }
    }
} finally {
    Pop-Location
}
