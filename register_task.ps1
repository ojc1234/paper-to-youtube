# Windows 작업 스케줄러에 매일 실행 등록 (기본 오전 6:50)
# 실행:  powershell -ExecutionPolicy Bypass -File .\register_task.ps1 [-Time "06:50"]
param([string]$Time = "06:50")
$Kit = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $Kit "run_daily.ps1"
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName "PaperToYouTube" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "등록 완료: 매일 $Time 에 실행 (작업 스케줄러 > PaperToYouTube)"
Write-Host "해제: Unregister-ScheduledTask -TaskName PaperToYouTube"
