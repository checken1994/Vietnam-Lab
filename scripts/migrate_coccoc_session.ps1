<#
.SYNOPSIS
    Migrates CocCoc browser session material into the SCP dev-tools profile.

.DESCRIPTION
    [A11 H-02 fix 2026-10-01] This script performs two destructive / sensitive
    steps and MUST NOT run unattended by default:
      1. FORCE-KILLS every running CocCoc browser process.
      2. COPIES browser credential/session material (Cookies, Local State,
         Local/Session Storage, IndexedDB, Preferences) to the target profile.

    Guard rails:
      - CmdletBinding(SupportsShouldProcess) + ConfirmImpact=High: every kill
        and every copy goes through $PSCmdlet.ShouldProcess() and prompts for
        confirmation on interactive hosts.
      - -Force: skip the interactive prompts (explicit opt-in for automation).
      - -WhatIf: dry-run — print what would happen, change nothing.
      - The copied material stays on the local machine; contents are never
        read or printed by this script.

.EXAMPLE
    ./migrate_coccoc_session.ps1              # prompts before kill + copy
    ./migrate_coccoc_session.ps1 -WhatIf     # dry run
    ./migrate_coccoc_session.ps1 -Force      # unattended (explicit consent)
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# [Zero-Hardcoded-Paths] C:\Users\check\AppData\Local == $env:LOCALAPPDATA.
$sourceData = Join-Path $env:LOCALAPPDATA 'CocCoc\Browser\User Data'
$sourceProfile = Join-Path $sourceData 'Default'
$targetData = Join-Path $env:LOCALAPPDATA 'SCP\CocCocDevToolsProfile'
$targetProfile = Join-Path $targetData 'Default'

if (-not (Test-Path $sourceProfile)) {
    throw "Khong tim thay profile Coc Coc nguon: $sourceProfile"
}

# [A11 H-02] -Force = explicit consent to run without prompts. Any other mode
# keeps the High-impact ShouldProcess confirmation below.
if ($Force) {
    $ConfirmPreference = 'None'
}

Write-Warning (@"
CHUONG TRINH NAY SE:
  1. FORCE-KILL moi tien trinh CocCoc dang chay.
  2. COPY material session/credential cua trinh duyet (Cookies, Local State,
     Local/Session Storage, IndexedDB, Preferences) sang: $targetData
Material duoc copy NGUYEN VEN va KHONG BAO GIO duoc doc/in boi script nay.
"@)

Write-Output 'Dang dong cac tien trinh Coc Coc...'
$processes = @(Get-CimInstance Win32_Process | Where-Object {
    ([string]$_.ExecutablePath -like '*\CocCoc\*') -or
    ([string]$_.CommandLine -like '*\CocCoc\*')
})
foreach ($proc in $processes) {
    # High-impact ShouldProcess: prompts unless -Force/-Confirm:$false/-WhatIf.
    if ($PSCmdlet.ShouldProcess("PID $($proc.ProcessId) ($($proc.Name))", 'Force-kill CocCoc process')) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Seconds 2

if ($PSCmdlet.ShouldProcess($targetProfile, 'Create target profile directory')) {
    New-Item -ItemType Directory -Force -Path $targetProfile | Out-Null
}

function Copy-SessionItem([string]$relativePath) {
    $source = Join-Path $sourceData $relativePath
    $target = Join-Path $targetData $relativePath
    if (-not (Test-Path $source)) {
        Write-Output ("SKIP|" + $relativePath)
        return
    }
    if (-not $script:PSCmdlet.ShouldProcess($relativePath, 'Copy browser session/credential material')) {
        return
    }
    $targetParent = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $targetParent | Out-Null
    $item = Get-Item $source
    if ($item.PSIsContainer) {
        Copy-Item -Path $source -Destination $target -Recurse -Force
        $bytes = (Get-ChildItem $source -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    } else {
        Copy-Item -Path $source -Destination $target -Force
        $bytes = $item.Length
    }
    Write-Output ("COPIED|" + $relativePath + "|bytes=" + $bytes)
}

# Local State contains the Windows-user-bound encryption key metadata.
# The following browser storage files hold session state; their contents are not read.
$items = @(
    'Local State',
    'Default\Cookies',
    'Default\Network\Cookies',
    'Default\Local Storage',
    'Default\Session Storage',
    'Default\IndexedDB',
    'Default\Preferences'
)
foreach ($item in $items) {
    Copy-SessionItem $item
}

Write-Output ("DONE|target=" + $targetData)
