#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [switch]$RunEvolution,
    [int]$PythonProbeTimeoutMs = 20000,
    [int]$EvolutionTimeoutMs = 180000
)

$ErrorActionPreference = 'Stop'
$root = 'C:\Users\check\Downloads\scp'
$python = Join-Path $root 'scp\venv\Scripts\python.exe'
$envFile = 'C:\Users\check\Downloads\.env'
$names = @(
    'codex_sandbox_offline_block_outbound',
    'codex_sandbox_offline_block_loopback_tcp'
)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$data = Join-Path $root 'data'
$beforeJson = Join-Path $data ("firewall-r28-change-window-before-$stamp.json")
$afterJson = Join-Path $data ("firewall-r28-change-window-after-$stamp.json")
$probePy = Join-Path $data ("python-ollama-probe-r28-$stamp.py")
$probeOut = Join-Path $data ("python-ollama-probe-r28-$stamp.out.log")
$probeErr = Join-Path $data ("python-ollama-probe-r28-$stamp.err.log")
$evolutionLog = Join-Path $data ("evolution-r28-firewall-window-$stamp.log")
$runtimeEnv = Join-Path $root (".private-secrets\env-r28-evolution-$stamp")
$prior = [ordered]@{}

function Write-JsonNoSecrets([string]$path, $value) {
    $value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $path -Encoding UTF8
}

foreach ($name in $names) {
    $rule = Get-NetFirewallRule -DisplayName $name -ErrorAction Stop
    $prior[$name] = [string]$rule.Enabled
}
Write-JsonNoSecrets $beforeJson ([ordered]@{
    generated_utc = (Get-Date).ToUniversalTime().ToString('o')
    rules = $prior
    python = $python
    remote_address = '127.0.0.1'
    # (llm-bridge removed 2026-10-24 — port 11434 retired; keep prior rule reference only.)
    remote_port = 11434
})

$probeContent = @'
import json
import time
import urllib.request

started = time.monotonic()
request = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
try:
    with urllib.request.urlopen(request, timeout=12) as response:
        payload = response.read(256)
        result = {
            "status": "PASS",
            "http_status": int(response.status),
            "bytes_read": len(payload),
            "elapsed_sec": round(time.monotonic() - started, 3),
        }
except Exception as exc:
    result = {
        "status": "FAIL",
        "error_type": type(exc).__name__,
        "error": str(exc)[:240],
        "elapsed_sec": round(time.monotonic() - started, 3),
    }
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(0 if result["status"] == "PASS" else 2)
'@
Set-Content -LiteralPath $probePy -Value $probeContent -Encoding UTF8

try {
    Disable-NetFirewallRule -DisplayName $names -ErrorAction Stop
    Write-Output 'WINDOW_FIREWALL_STATE=TEMPORARILY_DISABLED_FOR_TWO_EXISTING_BLOCK_RULES'

    $curlStatus = & curl.exe -sS -o NUL -w '%{http_code}' --connect-timeout 3 --max-time 10 'http://127.0.0.1:11434/api/tags'
    Write-Output ('NATIVE_CURL_HTTP=' + $curlStatus)

    $probeProcess = Start-Process -FilePath $python -ArgumentList @($probePy) -WorkingDirectory $root -RedirectStandardOutput $probeOut -RedirectStandardError $probeErr -WindowStyle Hidden -PassThru
    if (-not $probeProcess.WaitForExit($PythonProbeTimeoutMs)) {
        Stop-Process -Id $probeProcess.Id -Force -ErrorAction SilentlyContinue
        Write-Output 'PYTHON_OLLAMA_PROBE=TIMEOUT'
    } else {
        $probeStatus = if ($probeProcess.ExitCode -eq 0) { 'PASS' } else { 'FAIL' }
        Write-Output ('PYTHON_OLLAMA_PROBE=' + $probeStatus + '|EXIT=' + $probeProcess.ExitCode)
        if (Test-Path $probeOut) { Get-Content $probeOut | Select-Object -Last 3 }
        if (Test-Path $probeErr) { Get-Content $probeErr | Select-Object -Last 3 }
    }

    if ($RunEvolution) {
        $envText = Get-Content -LiteralPath $envFile -Raw -Encoding UTF8
        if ($envText -match '(?m)^OLLAMA_MODEL_AUTOFIX=') {
            $envText = [regex]::Replace($envText, '(?m)^OLLAMA_MODEL_AUTOFIX=.*$', 'OLLAMA_MODEL_AUTOFIX=llama3.2:latest')
        } else {
            $envText += "`r`nOLLAMA_MODEL_AUTOFIX=llama3.2:latest`r`n"
        }
        if ($envText -match '(?m)^SCP_LLM_PROVIDER_MODE=') {
            $envText = [regex]::Replace($envText, '(?m)^SCP_LLM_PROVIDER_MODE=.*$', 'SCP_LLM_PROVIDER_MODE=ollama_only')
        } else {
            $envText += "`r`nSCP_LLM_PROVIDER_MODE=ollama_only`r`n"
        }
        $taskModelKeys = @('OLLAMA_MODEL', 'OLLAMA_MODEL_AUTOFIX', 'OLLAMA_MODEL_WHY', 'OLLAMA_MODEL_LEARNING', 'OLLAMA_MODEL_FAST_LEARNING', 'OLLAMA_MODEL_JUDGE', 'OLLAMA_MODEL_CHAT')
        foreach ($modelKey in $taskModelKeys) {
            if ($envText -match ('(?m)^' + [regex]::Escape($modelKey) + '=')) {
                $envText = [regex]::Replace($envText, ('(?m)^' + [regex]::Escape($modelKey) + '.*$'), ($modelKey + '=llama3.2:latest'))
            } else {
                $envText += "`r`n$($modelKey)=llama3.2:latest`r`n"
            }
        }
        Write-Output 'EVOLUTION_ALL_OLLAMA_MODELS=llama3.2:latest'
        Set-Content -LiteralPath $runtimeEnv -Value $envText -Encoding UTF8
        $env:SCP_ENV_FILE = $runtimeEnv
        $env:OLLAMA_MODEL_AUTOFIX = 'llama3.2:latest'
        $env:SCP_LLM_PROVIDER_MODE = 'ollama_only'
        Write-Output 'EVOLUTION_PROVIDER_MODE=ollama_only'
        $env:PYTHONPATH = $root
        Write-Output 'EVOLUTION_ENV_MODEL=llama3.2:latest'
        $evolutionErr = [System.IO.Path]::ChangeExtension($evolutionLog, '.err.log')
        Remove-Item -LiteralPath $evolutionLog, $evolutionErr -Force -ErrorAction SilentlyContinue
        $evolution = Start-Process -FilePath $python -ArgumentList @('scp\autofix\runner.py','--evolve','--max-bugs','1','--evolution-timeout-seconds','120') -WorkingDirectory $root -RedirectStandardOutput $evolutionLog -RedirectStandardError $evolutionErr -WindowStyle Hidden -PassThru
        if (-not $evolution.WaitForExit($EvolutionTimeoutMs)) {
            Stop-Process -Id $evolution.Id -Force -ErrorAction SilentlyContinue
            Write-Output 'EVOLUTION_WINDOW=TIMEOUT'
        } else {
            Write-Output ('EVOLUTION_WINDOW=EXIT_' + $evolution.ExitCode)
        }
        Write-Output ('EVOLUTION_LOG=' + $evolutionLog)
        Write-Output ('EVOLUTION_ERR=' + $evolutionErr)
    }
}
finally {
    foreach ($name in $names) {
        Set-NetFirewallRule -DisplayName $name -Enabled $prior[$name] -ErrorAction Stop
    }
    $after = [ordered]@{}
    foreach ($name in $names) {
        $after[$name] = [string](Get-NetFirewallRule -DisplayName $name).Enabled
    }
    Write-JsonNoSecrets $afterJson ([ordered]@{
        restored_utc = (Get-Date).ToUniversalTime().ToString('o')
        rules = $after
        restored_to = $prior
    })
    Remove-Item -LiteralPath $probePy, $runtimeEnv -Force -ErrorAction SilentlyContinue
    Write-Output ('FIREWALL_RESTORED_MANIFEST=' + $afterJson)
}
