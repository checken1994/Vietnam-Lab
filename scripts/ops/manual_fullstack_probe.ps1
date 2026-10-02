$ErrorActionPreference='Stop'
$Root=Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$Evidence=Join-Path $Root ('.private-secrets\manual-fullstack-'+$stamp)
New-Item -ItemType Directory -Force $Evidence | Out-Null
$Base=Get-ChildItem (Join-Path $Root '.private-secrets') -Directory -Filter 'fullstack-http-ready-*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if(-not $Base){throw 'No prior safe fullstack env available'}
$EnvFile=Join-Path $Evidence 'fullstack.env'
$lines=[IO.File]::ReadAllLines((Join-Path $Base.FullName 'fullstack.env'))
$replace=@{SCP_ENV_FILE=$EnvFile;SCP_SIDECAR_ENV_FILE=$EnvFile;SCP_EGRESS_MODE='deny';SCP_SKIP_STARTUP_GATE='1';SCP_BASE_URL='http://127.0.0.1:8000';LLM_BRIDGE_URL='http://127.0.0.1:8081'}
$SchedulerToken="test-only-scheduler-token"
$replace["SCP_SCHEDULER_ADMIN_TOKEN"]=$SchedulerToken
$out=foreach($line in $lines){$done=$false;foreach($k in $replace.Keys){if($line -match ('^'+[regex]::Escape($k)+'=')){$done=$true;$k+'='+$replace[$k];break}};if(-not $done){$line}}
foreach($k in $replace.Keys){if(-not ($out -match ('^'+[regex]::Escape($k)+'='))){$out+=$k+'='+$replace[$k]}}
[IO.File]::WriteAllLines($EnvFile,$out)
$py=Join-Path $Root 'scp\venv\Scripts\python.exe'
$procs=@{}
function Start-Child($name,$file,$argList,$dir){$o=Join-Path $Evidence ($name+'.out.log');$e=Join-Path $Evidence ($name+'.err.log');$p=Start-Process -FilePath $file -ArgumentList $argList -WorkingDirectory $dir -RedirectStandardOutput $o -RedirectStandardError $e -PassThru;$procs[$name]=$p;return $p}
function Wait-OK($url,$seconds=120){$end=(Get-Date).AddSeconds($seconds);do{try{$r=Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 4;if([int]$r.StatusCode -eq 200){return}}catch{};Start-Sleep 1}while((Get-Date)-lt $end);throw "readiness timeout $url"}
function Probe($name,$method,$url,$headers=@{}){try{$r=Invoke-WebRequest -UseBasicParsing -Method $method -Uri $url -Headers $headers -TimeoutSec 15;return [ordered]@{name=$name;status=[int]$r.StatusCode}}catch{$c=if($_.Exception.Response){[int]$_.Exception.Response.StatusCode}else{-1};return [ordered]@{name=$name;status=$c}}}
$results=@();$err=$null
try{
  foreach($line in $out){if($line -match '^([^#=][^=]*)=(.*)$'){[Environment]::SetEnvironmentVariable($Matches[1].Trim(),$Matches[2],'Process')}}
  $env:SCP_ENV_FILE=$EnvFile;$env:SCP_SIDECAR_ENV_FILE=$EnvFile;$env:SCP_EGRESS_MODE='deny';$env:PYTHONUTF8='1';$env:PYTHONIOENCODING='utf-8'
  Start-Child backend $py @('-m','scp') $Root|Out-Null;Wait-OK 'http://127.0.0.1:8000/health';$results+=Probe backend_health GET 'http://127.0.0.1:8000/health';$results+=Probe backend_detailed GET 'http://127.0.0.1:8000/health/detailed';$results+=Probe backend_auth_negative POST 'http://127.0.0.1:8000/v105/autofix/run-audit' @{'Authorization'='Bearer invalid-test-token'}
  Start-Child bridge 'bun.exe' @('index.ts') (Join-Path $Root 'mini-services\llm-bridge');Wait-OK 'http://127.0.0.1:8081/api/tags';$results+=Probe bridge_root GET 'http://127.0.0.1:8081/';$results+=Probe bridge_tags GET 'http://127.0.0.1:8081/api/tags'
  Start-Child scheduler 'bun.exe' @('index.ts') (Join-Path $Root 'mini-services\loop-scheduler');Wait-OK 'http://127.0.0.1:3031/healthz';$results+=Probe scheduler_healthz GET 'http://127.0.0.1:3031/healthz';$results+=Probe scheduler_pause_unauth POST 'http://127.0.0.1:3031/pause';$results+=Probe scheduler_pause POST 'http://127.0.0.1:3031/pause' @{Authorization=("Bearer "+$SchedulerToken)};$results+=Probe scheduler_resume POST 'http://127.0.0.1:3031/resume' @{Authorization=("Bearer "+$SchedulerToken)}
  Start-Child dashboard 'npm.cmd' @('run','dev','--','--hostname','127.0.0.1','--port','3000') (Join-Path $Root 'dashboard');Wait-OK 'http://127.0.0.1:3000/';$results+=Probe dashboard GET 'http://127.0.0.1:3000/'
  Start-Sleep 2
  foreach($cp in $procs.Values){if($cp -and -not $cp.HasExited){taskkill.exe /PID $cp.Id /T /F *> $null}}; Start-Sleep 2
  $logs=(@('bridge.out.log','scheduler.out.log','dashboard.out.log')|ForEach-Object{if(Test-Path(Join-Path $Evidence $_)){[IO.File]::ReadAllText((Join-Path $Evidence $_))}})-join "`n"
  $summary=[ordered]@{evidence=$Evidence;results=$results;bridge_env=($logs.Contains("env source=$EnvFile"));scheduler_env=($logs.Contains("env source=$EnvFile"));scheduler_8000=$logs.Contains('scp=http://127.0.0.1:8000');no_lan_url=(-not $logs.Contains('192.168.'));egress_mode='deny';error=$null}
  [IO.File]::WriteAllText((Join-Path $Evidence 'summary.json'),($summary|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
  Write-Output ('EVIDENCE='+$Evidence);Write-Output (($summary|ConvertTo-Json -Compress))
}catch{$err=$_;[IO.File]::WriteAllText((Join-Path $Evidence 'error.txt'),($_|Out-String),[Text.UTF8Encoding]::new($false));Write-Output ('ERROR='+$_.Exception.Message);throw}
finally{foreach($p in $procs.Values){if($p -and -not $p.HasExited){Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue}};foreach($port in @(3000,3031,8000,8081)){Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue|Select-Object -ExpandProperty OwningProcess -Unique|ForEach-Object{Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue}};Start-Sleep 2;foreach($port in @(3000,3031,8000,8081)){Write-Output ('PORT_'+$port+'_FREE='+(-not [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)))}}
