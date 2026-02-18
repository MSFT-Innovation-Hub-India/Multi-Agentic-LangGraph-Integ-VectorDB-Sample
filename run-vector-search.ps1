param(
    [Parameter(Mandatory = $true)]
    [string]$QueryText,

    [int]$Top = 10,

    [Nullable[int]]$MaxRating = $null,

    [Nullable[double]]$DistanceThreshold = $null,

    [string]$Contains = $null
)

$pythonExe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$scriptPath = Join-Path $PSScriptRoot "scripts\vector_feedback_search.py"

if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found at $pythonExe. Activate/create your .venv first."
}

if (-not (Test-Path $scriptPath)) {
    throw "Script not found at $scriptPath"
}

$argsList = @(
    $scriptPath,
    "--query-text", $QueryText,
    "--top", $Top
)

if ($null -ne $MaxRating) {
    $argsList += @("--max-rating", $MaxRating)
}

if ($null -ne $DistanceThreshold) {
    $argsList += @("--distance-threshold", $DistanceThreshold)
}

if ($Contains) {
    $argsList += @("--contains", $Contains)
}

& $pythonExe @argsList
