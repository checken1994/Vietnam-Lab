$ErrorActionPreference = 'Stop'
# [M-02 fix 2026-10-01] Resolve repo root relative to this script instead of a
# hardcoded user path (C:\Users\check\Downloads\scp). This file lives at
# <repo>\tools\audit\, so two levels up is the repository root.
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$base = Join-Path $root '.private-secrets\release-audit\scp-247'
$latestPath = Join-Path $base 'hourly-latest.json'
$journalPath = Join-Path $base 'hourly-monitor.jsonl'

function Parse-Document([string] $text) {
    try {
        $doc = [System.Text.Json.JsonDocument]::Parse($text)
        return [pscustomobject]@{ ok=$true; kind=$doc.RootElement.ValueKind.ToString(); document=$doc }
    } catch {
        $e=$_.Exception
        $line=''; $byte=''
        if($e.PSObject.Properties.Name -contains 'LineNumber'){$line=[string]$e.LineNumber}
        if($e.PSObject.Properties.Name -contains 'BytePositionInLine'){$byte=[string]$e.BytePositionInLine}
        return [pscustomobject]@{ ok=$false; kind=''; document=$null; error_type=$e.GetType().Name; error_line=$line; error_byte=$byte }
    }
}
function Safe-Scalar([System.Text.Json.JsonElement] $v) {
    if ($v.ValueKind -eq [System.Text.Json.JsonValueKind]::String) {
        $s=$v.GetString(); if($s -match '^\d{4}-\d{2}-\d{2}') { return $s.Substring(0,[Math]::Min(40,$s.Length)) }; return $null
    }
    if ($v.ValueKind -eq [System.Text.Json.JsonValueKind]::Number) { return $v.ToString() }
    if ($v.ValueKind -eq [System.Text.Json.JsonValueKind]::True -or $v.ValueKind -eq [System.Text.Json.JsonValueKind]::False) { return $v.ToString() }
    return $null
}
function Summarize-Object([System.Text.Json.JsonElement] $root) {
    $timestamps=[System.Collections.Generic.List[string]]::new(); $statuses=[System.Collections.Generic.List[string]]::new(); $counts=[System.Collections.Generic.List[string]]::new(); $provenance=$false; $bad=$false; $visited=0
    $timestampKeys='(?i)^(timestamp|timestamp_utc|checked_at|completed_at|created_at|started_at|time|ts)$'
    $statusKeys='(?i)^(status|state|result|verdict|run_status|health|readiness|decision)$'
    $countKeys='(?i)(count|checks|passed|failed|degraded|success|failure|total|restart|uptime)'
    $provKeys='(?i)(commit|head|run_id|attempt_id|step_id|check_id|incident_id|source|profile|provenance|evidence_ref|generated_at)'
    $sensitive='(?i)(secret|token|password|cookie|prompt|question|answer|email|credential|private|api.?key|authorization)'
    $stack=[System.Collections.Generic.Stack[object]]::new(); $stack.Push(@('', $root))
    while($stack.Count -gt 0 -and $visited -lt 20000){ $pair=$stack.Pop(); $path=[string]$pair[0]; $node=[System.Text.Json.JsonElement]$pair[1]; $visited++
        if($node.ValueKind -eq [System.Text.Json.JsonValueKind]::Object){ foreach($prop in $node.EnumerateObject()){ $name=$prop.Name; $v=$prop.Value; if($name -match $sensitive){continue}; $scalar=Safe-Scalar $v
                if($name -match $timestampKeys -and $null -ne $scalar){$timestamps.Add($scalar)}
                elseif($name -match $statusKeys -and $null -ne $scalar){$statuses.Add($scalar); if([string]$scalar -match '(?i)fail|degrad|error|down|unhealthy|unknown|blocked|timeout|crash'){$bad=$true}}
                elseif($name -match $countKeys -and $null -ne $scalar -and [string]$scalar -match '^\s*-?\d+(\.\d+)?\s*$'){$counts.Add(($name+'='+$scalar))}
                elseif($name -match $provKeys){$provenance=$true}
                if($v.ValueKind -eq [System.Text.Json.JsonValueKind]::Object -or $v.ValueKind -eq [System.Text.Json.JsonValueKind]::Array){$stack.Push(@(($path+'.'+$name),$v))}
            }} elseif($node.ValueKind -eq [System.Text.Json.JsonValueKind]::Array){$i=0; foreach($v in $node.EnumerateArray()){if($v.ValueKind -eq [System.Text.Json.JsonValueKind]::Object -or $v.ValueKind -eq [System.Text.Json.JsonValueKind]::Array){$stack.Push(@(($path+'['+$i+']'),$v))};$i++}}
    }
    return [pscustomobject]@{timestamps=@($timestamps|Select-Object -Unique|Select-Object -First 4); statuses=@($statuses|Select-Object -Unique|Select-Object -First 8); counts=@($counts|Select-Object -Unique|Select-Object -First 8); provenance=$provenance; bad=$bad; visited=$visited}
}
function Process-Text([string]$text){$p=Parse-Document $text; if(-not $p.ok){return [pscustomobject]@{parse='FAIL';summary=$null;error_type=$p.error_type;error_line=$p.error_line;error_byte=$p.error_byte}}; return [pscustomobject]@{parse='PASS';summary=(Summarize-Object $p.document.RootElement)}}

Write-Output ('hourly_latest_exists='+(Test-Path -LiteralPath $latestPath))
if(Test-Path -LiteralPath $latestPath){$r=Process-Text ([IO.File]::ReadAllText($latestPath)); Write-Output ('hourly_latest_parse='+$r.parse); if($r.parse -eq 'FAIL'){Write-Output ('hourly_latest_error_type='+$r.error_type+';line='+$r.error_line+';byte='+$r.error_byte)}; if($r.summary){Write-Output ('hourly_latest_timestamps='+($r.summary.timestamps -join '|'));Write-Output ('hourly_latest_statuses='+($r.summary.statuses -join '|'));Write-Output ('hourly_latest_counts='+($r.summary.counts -join '|'));Write-Output ('hourly_latest_provenance='+$r.summary.provenance);Write-Output ('hourly_latest_bad='+$r.summary.bad)}}
Write-Output ('hourly_monitor_exists='+(Test-Path -LiteralPath $journalPath))
if(Test-Path -LiteralPath $journalPath){$lines=@(Get-Content -LiteralPath $journalPath -Tail 12);Write-Output ('hourly_monitor_tail_records='+$lines.Count);$idx=0;foreach($line in $lines){$r=Process-Text $line;if($r.summary){Write-Output ('record='+$idx+';parse='+$r.parse+';timestamps='+($r.summary.timestamps -join '|')+';statuses='+($r.summary.statuses -join '|')+';counts='+($r.summary.counts -join '|')+';provenance='+$r.summary.provenance+';bad='+$r.summary.bad)}else{Write-Output ('record='+$idx+';parse='+$r.parse+';error_type='+$r.error_type+';line='+$r.error_line+';byte='+$r.error_byte)};$idx++}}
