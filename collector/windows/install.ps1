# Register the collector as a Task Scheduler task that starts at logon.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File collector\windows\install.ps1
#
# Configuration is read from the environment. Machine-scoped values are set as
# user environment variables here rather than written into the task, so the
# write token is not visible in the scheduler UI or an exported task XML.

param(
    [string]$MachineId = 'personal-windows',
    [string]$IngestUrl = '',
    [string]$WriteToken = '',
    [int]$IntervalSeconds = 30
)

$ErrorActionPreference = 'Stop'
$collector = Split-Path -Parent $PSScriptRoot
$taskName = 'Activity Collector'

$python = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = (Get-Command python).Source }
if ($python -like '*\WindowsApps\*') {
    # The app execution alias resolves in a shell but not for the scheduler.
    $real = (Get-Item $python).Directory.GetDirectories('PythonSoftwareFoundation.Python.*')
    if ($real.Count -eq 0) { throw "install python from python.org, the Store alias cannot be scheduled" }
    $python = Join-Path $real[0].FullName 'pythonw.exe'
}

$version = & $python -c 'import sys; print(sys.version_info >= (3, 11))'
if ($version.Trim() -ne 'True') { throw "need python 3.11 or newer, found $(& $python -V)" }

# pythonw, or a console window opens at every logon.
[Environment]::SetEnvironmentVariable('ACTIVITY_MACHINE_ID', $MachineId, 'User')
[Environment]::SetEnvironmentVariable('ACTIVITY_INTERVAL_SECONDS', "$IntervalSeconds", 'User')
[Environment]::SetEnvironmentVariable('PYTHONPATH', (Join-Path $collector 'src'), 'User')
if ($IngestUrl)  { [Environment]::SetEnvironmentVariable('ACTIVITY_INGEST_URL', $IngestUrl, 'User') }
if ($WriteToken) { [Environment]::SetEnvironmentVariable('ACTIVITY_WRITE_TOKEN', $WriteToken, 'User') }

$action = New-ScheduledTaskAction -Execute $python -Argument '-m activity_collector' -WorkingDirectory $collector
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# No time limit: this is meant to run for as long as the session does.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description 'Privacy-preserving local activity collector.' | Out-Null

Write-Host "registered '$taskName'"
Write-Host "start now:  Start-ScheduledTask -TaskName '$taskName'"
Write-Host "state:      Get-ScheduledTaskInfo -TaskName '$taskName'"
Write-Host "uninstall:  Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
