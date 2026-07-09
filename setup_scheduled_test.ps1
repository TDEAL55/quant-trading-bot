$ErrorActionPreference = 'Stop'

$taskName = 'QuantTradingBot-ScheduledPaperTest-OneTime'
$pythonPath = 'C:\Users\dealt\OneDrive\.venv\Scripts\python.exe'
$projectPath = 'C:\Users\dealt\OneDrive\quant-trading-bot'
$scriptPath = Join-Path $projectPath 'scheduled_paper_test.py'

# Compute next weekday date at 9:55 AM Eastern, then convert to local system time for Task Scheduler.
$easternTz = [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')
$localTz = [TimeZoneInfo]::Local
$nowUtc = [DateTime]::UtcNow
$nowEastern = [TimeZoneInfo]::ConvertTimeFromUtc($nowUtc, $easternTz)

$nextRunDate = $nowEastern.Date
if ($nowEastern.TimeOfDay -ge ([TimeSpan]::FromHours(9) + [TimeSpan]::FromMinutes(55))) {
    $nextRunDate = $nextRunDate.AddDays(1)
}
while ($nextRunDate.DayOfWeek -eq [DayOfWeek]::Saturday -or $nextRunDate.DayOfWeek -eq [DayOfWeek]::Sunday) {
    $nextRunDate = $nextRunDate.AddDays(1)
}
$nextRunEastern = $nextRunDate.AddHours(9).AddMinutes(55)
$nextRun = [TimeZoneInfo]::ConvertTime($nextRunEastern, $easternTz, $localTz)

if (-not (Test-Path $pythonPath)) {
    throw "Python executable not found at: $pythonPath"
}
if (-not (Test-Path $scriptPath)) {
    throw "scheduled_paper_test.py not found at: $scriptPath"
}

$action = New-ScheduledTaskAction -Execute $pythonPath -Argument 'scheduled_paper_test.py' -WorkingDirectory $projectPath
$trigger = New-ScheduledTaskTrigger -Once -At $nextRun
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description 'One-time dry-run paper test for quant-trading-bot.' | Out-Null

Write-Output "Created scheduled task '$taskName' for $($nextRun.ToString('yyyy-MM-dd HH:mm:ss')) local time ($($nextRunEastern.ToString('yyyy-MM-dd HH:mm:ss')) Eastern)."
Write-Output 'This task runs once and uses scheduled_paper_test.py (DRY_RUN path only).'
