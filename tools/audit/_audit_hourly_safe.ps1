$ErrorActionPreference = 'Stop'
# [M-02 fix 2026-10-01] Resolve repo root relative to this script instead of a
# hardcoded user path (C:\Users\check\Downloads\scp). This file lives at
# <repo>\tools\audit\, so two levels up is the repository root.
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$base = Join-Path $root '.private-secrets\release-audit\scp-247'
$latestPath = Join-Path $base 'hourly-latest.json'
$journalPath = Join-Path $base 'hourly-monitor.jsonl'

$timestampKeys = '(?i)^(timestamp|timestamp_utc|checked_at|completed_at|created_at|started_at|time|ts)$'
$statusKeys = '(?i)^(status|state|result|verdict|run_status|health|readiness|decision)$'
$countKeys = '(?i)(count|checks|passed|failed|degraded|success|failure|total|restart|uptime)'
$provenanceKeys = '(?i)(commit|head|run_id|attempt_id|step_id|check_id|incident_id|source|profile|provenance|evidence_ref|generated_at)'
$sensitiveKeys = '(?i)(secret|token|password|cookie|prompt|question|answer|email|credential|private|api.?key|authorization)'

function Normalize-Status([object] $value) {
    if ($null -eq $value) { return 'null' }
    $s = [string]$value
    if ($s -match '(?i)fail|degrad|error|down|unhealthy|unknown|blocked|timeout|crash') { return 'BAD:' + $s.Substring(0, [Math]::Min($s.Length, 40)) }
    if ($s -match '(?i)pass|healthy|ok|success|running|ready|complete|allow') { return 'GOOD:' + $s.Substring(0, [Math]::Min($s.Length, 40)) }
    return 'OTHER:' + $s.Substring(0, [Math]::Min($s.Length, 30))
}

function Walk-Safe([object] $node, [string] $path, [int] $depth = 0) {
    if ($null -eq $node -or $depth -gt 8) { return }
    if ($node -is [System.Collections.IDictionary]) {
        foreach ($key in $node.Keys) {
            $name = [string]$key
            $value = $node[$key]
            if ($name -match $sensitiveKeys) { continue }
            Add-SafeField -name $name -value $value -path ($path + '.' + $name)
            if ($value -is [System.Collections.IDictionary] -or ($value -is [System.Collections.IEnumerable] -and $value -isnot [string])) { Walk-Safe $value ($path + '.' + $name) ($depth + 1) }
        }
        return
    }
    if ($node -is [PSCustomObject]) {
        foreach ($prop in $node.PSObject.Properties) {
            $name = [string]$prop.Name
            $value = $prop.Value
            if ($name -match $sensitiveKeys) { continue }
            Add-SafeField -name $name -value $value -path ($path + '.' + $name)
            if ($value -is [PSCustomObject] -or $value -is [System.Collections.IDictionary] -or ($value -is [System.Collections.IEnumerable] -and $value -isnot [string])) { Walk-Safe $value ($path + '.' + $name) ($depth + 1) }
        }
    }
    elseif ($node -is [System.Collections.IEnumerable] -and $node -isnot [string]) {
        $i = 0
        foreach ($item in $node) { Walk-Safe $item ($path + '[' + $i + ']') ($depth + 1); $i++ }
    }
}

$script:fields = [System.Collections.Generic.List[string]]::new()
$script:bad = $false
$script:good = $false
$script:hasTimestamp = $false
$script:hasProvenance = $false
function Add-SafeField([string] $name, [object] $value, [string] $path) {
    if ($name -match $timestampKeys -and $value -isnot [System.Collections.IEnumerable] -and $null -ne $value) {
        $s = [string]$value
        if ($s -match '^\d{4}-\d{2}-\d{2}') { $script:fields.Add('timestamp=' + $s.Substring(0, [Math]::Min($s.Length, 40))); $script:hasTimestamp = $true }
    }
    elseif ($name -match $statusKeys -and $value -isnot [System.Collections.IEnumerable] -and $null -ne $value) {
        $norm = Normalize-Status $value
        $script:fields.Add('status=' + $norm)
        if ($norm.StartsWith('BAD:')) { $script:bad = $true }
        if ($norm.StartsWith('GOOD:')) { $script:good = $true }
    }
    elseif ($name -match $countKeys -and $value -isnot [System.Collections.IEnumerable] -and [string]$value -match '^\s*-?\d+(\.\d+)?\s*$') {
        $script:fields.Add(('count_' + $name + '=' + [string]$value))
    }
    elseif ($name -match $provenanceKeys) {
        $script:hasProvenance = $true
    }
}

function Parse-Safe([string] $text) {
    $script:fields = [System.Collections.Generic.List[string]]::new()
    $script:bad = $false; $script:good = $false; $script:hasTimestamp = $false; $script:hasProvenance = $false
    try { $obj = $text | ConvertFrom-Json -Depth 30 } catch { return [pscustomobject]@{ parse='FAIL'; fields=@(); bad=$false; good=$false; timestamp=$false; provenance=$false } }
    Walk-Safe $obj '$'
    $unique = @($script:fields | Select-Object -Unique | Select-Object -First 12)
    return [pscustomobject]@{ parse='PASS'; fields=$unique; bad=$script:bad; good=$script:good; timestamp=$script:hasTimestamp; provenance=$script:hasProvenance }
}

Write-Output ('hourly_latest_exists=' + (Test-Path -LiteralPath $latestPath))
Write-Output ('hourly_monitor_exists=' + (Test-Path -LiteralPath $journalPath))
if (Test-Path -LiteralPath $latestPath) {
    $r = Parse-Safe ([IO.File]::ReadAllText($latestPath))
    Write-Output ('hourly_latest_parse=' + $r.parse)
    Write-Output ('hourly_latest_safe_fields=' + (($r.fields -join ';') -replace '[\r\n]', ''))
    Write-Output ('hourly_latest_bad_status=' + $r.bad)
    Write-Output ('hourly_latest_provenance_present=' + $r.provenance)
}
if (Test-Path -LiteralPath $journalPath) {
    $lines = @(Get-Content -LiteralPath $journalPath -Tail 12)
    Write-Output ('hourly_monitor_tail_records=' + $lines.Count)
    $idx = 0
    foreach ($line in $lines) {
        $r = Parse-Safe $line
        $safe = (($r.fields -join ';') -replace '[\r\n]', '')
        Write-Output ('record=' + $idx + ';parse=' + $r.parse + ';bad=' + $r.bad + ';provenance=' + $r.provenance + ';fields=' + $safe)
        $idx++
    }
}
