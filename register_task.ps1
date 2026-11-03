# Registers a daily Windows Scheduled Task that runs the researcher and appends
# output to logs\run.log. Current-user scope - no admin required.
#
#   Register:    powershell -ExecutionPolicy Bypass -File .\register_task.ps1
#   Change time: pass -At, e.g.  ... -File .\register_task.ps1 -At 07:30
#   Remove:      Unregister-ScheduledTask -TaskName FreelanceJobResearcher -Confirm:$false

param(
    [string]$At = "08:00",
    [string]$TaskName = "FreelanceJobResearcher"
)

$ErrorActionPreference = "Stop"

# Resolve paths relative to THIS script so it works wherever the folder lives.
$proj = $PSScriptRoot
$py   = Join-Path $proj ".venv\Scripts\python.exe"
$main = Join-Path $proj "main.py"
$log  = Join-Path $proj "logs\run.log"

if (-not (Test-Path $py)) { throw "venv python not found at $py. Create it first: py -m venv .venv" }
New-Item -ItemType Directory -Force -Path (Join-Path $proj "logs") | Out-Null

# The task launches PowerShell, which runs the venv python and appends every
# stream (*>>) to the log. Paths are single-quoted inside the command string so
# the spaces in "freelance job researcher" survive.
# Trailing `exit $LASTEXITCODE` makes the task report python's real exit code.
$inner  = "& '$py' '$main' *>> '$log'; exit `$LASTEXITCODE"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$inner`"" `
    -WorkingDirectory $proj

$trigger = New-ScheduledTaskTrigger -Daily -At $At

# Laptop-safe settings. Task Scheduler's defaults refuse to start a task on
# battery power (error 0x800710E0, "the operator or administrator has refused
# the request") and skip the run entirely if the machine was asleep at $At —
# which silently produces no spreadsheet for days.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings `
    -Description "Daily Flutter/mobile freelance job scan to Excel" -Force | Out-Null

Write-Host "Registered '$TaskName' - runs daily at $At."
Write-Host "Test it now:   Start-ScheduledTask -TaskName $TaskName"
Write-Host "See status:    Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host "Remove it:     Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
