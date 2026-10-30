$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $root 'scp\venv\Scripts\python.exe'
$apiOut = Join-Path $root 'data\scp-api.out.log'
$apiErr = Join-Path $root 'data\scp-api.err.log'

function Test-Port([int]$port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

if (-not (Test-Port 8000)) {
    Remove-Item $apiOut, $apiErr -Force -ErrorAction SilentlyContinue
    $env:SCP_SKIP_STARTUP_GATE = '1'
    Start-Process -FilePath $py -ArgumentList '-m', 'scp', '8000' -WorkingDirectory $root -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr -WindowStyle Hidden | Out-Null
    Remove-Item Env:SCP_SKIP_STARTUP_GATE -ErrorAction SilentlyContinue
}

$ready = $false
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Port 8000) {
        try {
            # [BUYER-05] /openapi.json là opt-in (SCP_ENABLE_DOCS=1) — readiness dùng /health.
            Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8000/health' -TimeoutSec 5 | Out-Null
            $ready = $true
            break
        } catch {
        }
    }
}
Write-Output ('apiReady=' + $ready)
Write-Output ('browserDebug=' + (Test-Port 9222))
if (-not $ready) {
    Write-Output 'apiError=API did not become ready'
    if (Test-Path $apiErr) { Get-Content $apiErr -Tail 40 -Encoding UTF8 }
    exit 2
}

$body = @{
    question = 'SCP_FALLBACK_TEST: reply with one short sentence'
    providers = @('chatgpt', 'claude', 'gemini')
    approved = $true
    useBrowser = $true
    allowLocal = $false
} | ConvertTo-Json -Depth 4

try {
    $result = Invoke-RestMethod 'http://127.0.0.1:8000/v3/ai/ask-resilient' -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 150
    Write-Output ('resilientSuccess=' + $result.success)
    Write-Output ('successfulAI=' + (($result.successfulAIProviders) -join ','))
    Write-Output ('skipped=' + (($result.skippedProviders | ForEach-Object { $_.provider + ':' + $_.reason }) -join '|'))
    Write-Output ('webSuccess=' + $result.webSearch.success)
    Write-Output ('webResultCount=' + @($result.webSearch.results).Count)
    foreach ($item in @($result.aiResults)) {
        $provider = $item.provider
        $providerResult = $item.result
        Write-Output ('provider=' + $provider + '|success=' + $providerResult.success + '|error=' + $providerResult.error)
    }
} catch {
    Write-Output ('resilientError=' + $_.Exception.Message)
    exit 3
}
