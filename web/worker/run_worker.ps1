# 영상 PC 에서 워커 실행:  powershell -ExecutionPolicy Bypass -File web\worker\run_worker.ps1
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
while ($true) { python worker.py; Start-Sleep 5 }
