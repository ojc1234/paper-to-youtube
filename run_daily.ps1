# 매일 실행: Claude Code 를 헤드리스로 띄워 paper-to-youtube 스킬을 수행
# 수동 실행:  powershell -ExecutionPolicy Bypass -File .\run_daily.ps1
# 예약 등록:  powershell -ExecutionPolicy Bypass -File .\register_task.ps1
$ErrorActionPreference = "Continue"
$Kit = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Kit "config.ps1")

$logDir = Join-Path $env:PP_ROOT "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("run_" + (Get-Date -Format "yyyy-MM-dd_HHmm") + ".log")

$upload = if ($env:DO_UPLOAD -eq "1") { "" } else { " --no-upload" }
$prompt = @"
paper-to-youtube 스킬을 실행해줘. 인자: --category $($env:PAPER_CATEGORY) --max $($env:PAPERS_PER_DAY)$upload
환경변수는 이미 설정돼 있어 (PP_ROOT=$($env:PP_ROOT)). 사람에게 질문하지 말고 SKILL.md 의 판단 기준대로 끝까지 진행한 뒤 최종 보고를 출력해.
"@

Set-Location $env:PP_ROOT
# 허용 도구를 명시해 무인 실행 (필요 시 조정)
claude -p $prompt `
  --allowedTools "Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch,Skill" `
  --max-turns 300 *>&1 | Tee-Object -FilePath $log

Write-Host "로그: $log"
