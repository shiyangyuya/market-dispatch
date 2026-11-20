# Windows 任务计划程序注册脚本：每小时运行 + 登录时触发 + 错过后尽快补跑
$ErrorActionPreference = "Stop"

$ProjectDir = (Resolve-Path "$PSScriptRoot\..\..").Path
$RunCmd = Join-Path $ProjectDir "run.cmd"
$TaskName = "MarketDispatchHourly"

$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$RunCmd`"" -WorkingDirectory $ProjectDir
$TriggerHourly = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 1)
$TriggerLogon = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $Action `
  -Trigger @($TriggerHourly, $TriggerLogon) `
  -Settings $Settings `
  -Description "Market Dispatch hourly financial news metadata fetch and HTML rendering" `
  -Force

Write-Host "[OK] Scheduled task '$TaskName' registered for $ProjectDir"
